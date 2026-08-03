from __future__ import annotations

import ast
import copy
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage.cleanup import (
    CleanupRecordError,
    execute_guided_cleanup,
    revalidate_candidate,
    validate_cleanup_record,
    write_cleanup_record,
)


def utc_timestamp(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_candidate_report(
    storage_fixture, tmp_path: Path, *, name: str = "old-download.tmp"
) -> tuple[dict, dict, Path]:
    path = tmp_path / name
    path.write_bytes(b"review me")
    candidate = copy.deepcopy(
        storage_fixture("candidate-attributes-storage-report.json")[
            "candidates"
        ][0]
    )
    candidate.update(
        candidate_id="cleanup-001",
        path=str(path),
        scan_root=str(tmp_path),
        name=path.name,
        extension=path.suffix,
        size_bytes=path.stat().st_size,
        modified_at_utc=utc_timestamp(path),
        is_regular_file=True,
        is_reparse_point=False,
    )
    candidate["protection"] = {
        "eligibility": "eligible",
        "reason_code": None,
        "explanation": "Eligible test file inside the approved root.",
    }
    report = {
        "generated_at_utc": "2026-08-03T00:00:00Z",
        "drive": {"drive_letter": path.drive.upper()},
        "scan_scope": {
            "roots": [
                {
                    "requested_path": str(tmp_path),
                    "canonical_path": str(tmp_path),
                }
            ]
        },
    }
    return report, candidate, path


def test_validated_regular_file_is_sent_only_to_supplied_recycler(
    storage_fixture, tmp_path
) -> None:
    report, candidate, path = build_candidate_report(storage_fixture, tmp_path)
    recycled: list[Path] = []

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda reviewed_path: recycled.append(reviewed_path),
    )

    assert recycled == [path]
    assert path.exists()
    assert record["summary"]["recycled"] == 1
    assert record["safety"] == {
        "action": "windows_recycle_bin_only",
        "permanent_delete_fallback": False,
        "directories_allowed": False,
    }
    validate_cleanup_record(record)


@pytest.mark.parametrize("change", ["size", "modified"])
def test_changed_file_is_skipped_before_recycler(
    storage_fixture, tmp_path, change
) -> None:
    report, candidate, path = build_candidate_report(storage_fixture, tmp_path)
    if change == "size":
        path.write_bytes(b"changed after review")
    else:
        stat_result = path.stat()
        os.utime(path, (stat_result.st_atime, stat_result.st_mtime + 10))
    recycled: list[Path] = []

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda reviewed_path: recycled.append(reviewed_path),
    )

    assert recycled == []
    assert path.exists()
    assert record["summary"]["skipped_changed"] == 1


def test_missing_file_is_reported_without_calling_recycler(
    storage_fixture, tmp_path
) -> None:
    report, candidate, path = build_candidate_report(storage_fixture, tmp_path)
    path.unlink()
    recycled: list[Path] = []

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda reviewed_path: recycled.append(reviewed_path),
    )

    assert recycled == []
    assert record["summary"]["missing"] == 1


def test_directory_and_protected_candidate_fail_closed(
    storage_fixture, tmp_path
) -> None:
    report, candidate, _path = build_candidate_report(storage_fixture, tmp_path)
    directory = tmp_path / "not-a-file.tmp"
    directory.mkdir()
    candidate["path"] = str(directory)
    candidate["size_bytes"] = directory.stat().st_size
    candidate["modified_at_utc"] = utc_timestamp(directory)

    _path_result, invalid = revalidate_candidate(report, candidate)
    assert invalid["status"] == "skipped_protected_or_invalid"

    candidate["protection"]["eligibility"] = "protected"
    _path_result, protected = revalidate_candidate(report, candidate)
    assert protected["status"] == "skipped_protected_or_invalid"


def test_candidate_outside_reported_root_fails_closed(
    storage_fixture, tmp_path
) -> None:
    report, candidate, _path = build_candidate_report(storage_fixture, tmp_path)
    other = tmp_path.parent / "outside-cleanup.tmp"
    candidate["path"] = str(other)

    _path_result, result = revalidate_candidate(report, candidate)

    assert result["status"] == "skipped_protected_or_invalid"
    assert "outside" in result["message"]


def test_recycle_failure_never_removes_original_and_processing_continues(
    storage_fixture, tmp_path
) -> None:
    report, first, first_path = build_candidate_report(
        storage_fixture, tmp_path, name="first.tmp"
    )
    _report, second, second_path = build_candidate_report(
        storage_fixture, tmp_path, name="second.tmp"
    )
    second["candidate_id"] = "cleanup-002"
    calls: list[Path] = []

    def recycler(path: Path) -> None:
        calls.append(path)
        if path == first_path:
            raise OSError("simulated Recycle Bin failure")

    record = execute_guided_cleanup(
        report, [first, second], recycler=recycler
    )

    assert calls == [first_path, second_path]
    assert first_path.exists() and second_path.exists()
    assert record["summary"]["failed"] == 1
    assert record["summary"]["recycled"] == 1
    assert "not permanently deleted" in record["results"][0]["message"]


def test_cleanup_record_is_schema_validated_and_created_exclusively(
    storage_fixture, tmp_path
) -> None:
    report, candidate, _path = build_candidate_report(storage_fixture, tmp_path)
    record = execute_guided_cleanup(
        report, [candidate], recycler=lambda _path: None
    )

    output = write_cleanup_record(record, tmp_path / "records")

    assert output.is_file()
    assert output.parent == (tmp_path / "records").resolve()

    invalid = copy.deepcopy(record)
    invalid["summary"]["recycled"] = 0
    with pytest.raises(CleanupRecordError, match="does not match"):
        validate_cleanup_record(invalid)


def test_empty_or_duplicate_internal_selection_is_rejected(
    storage_fixture, tmp_path
) -> None:
    report, candidate, _path = build_candidate_report(storage_fixture, tmp_path)

    with pytest.raises(CleanupRecordError, match="between 1 and 500"):
        execute_guided_cleanup(report, [], recycler=lambda _path: None)
    with pytest.raises(CleanupRecordError, match="duplicate"):
        execute_guided_cleanup(
            report, [candidate, candidate], recycler=lambda _path: None
        )


def test_detected_reparse_point_is_never_recycled(
    storage_fixture, tmp_path, monkeypatch
) -> None:
    report, candidate, _path = build_candidate_report(storage_fixture, tmp_path)
    monkeypatch.setattr("storage.cleanup.is_reparse_point", lambda *_args: True)
    recycled: list[Path] = []

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda path: recycled.append(path),
    )

    assert recycled == []
    assert record["summary"]["skipped_protected_or_invalid"] == 1


def test_cleanup_module_has_no_permanent_delete_or_directory_remove_calls() -> None:
    source_path = Path(__file__).resolve().parents[2] / "storage" / "cleanup.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    prohibited_attributes = {"unlink", "remove", "rmdir", "rmtree"}

    discovered = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert discovered.isdisjoint(prohibited_attributes)
