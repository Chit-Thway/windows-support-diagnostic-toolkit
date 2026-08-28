from __future__ import annotations

import ast
import copy
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage.cleanup import (
    CleanupRecordError,
    _folder_tree_snapshot,
    execute_guided_cleanup,
    revalidate_candidate,
    validate_cleanup_record,
    write_cleanup_record,
)
from storage.scanner import ScannerOptions, StorageScanner


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


def test_current_risk_policy_blocks_installer_even_from_older_eligible_report(
    storage_fixture, tmp_path
) -> None:
    report, candidate, _path = build_candidate_report(
        storage_fixture,
        tmp_path,
        name="Zoom.msi",
    )
    recycled: list[Path] = []

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda path: recycled.append(path),
    )

    assert recycled == []
    assert record["summary"]["skipped_protected_or_invalid"] == 1
    assert "removal-risk policy" in record["results"][0]["message"]


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

    duplicate_path = copy.deepcopy(candidate)
    duplicate_path["candidate_id"] = "cleanup-002"
    with pytest.raises(CleanupRecordError, match="same reviewed path twice"):
        execute_guided_cleanup(
            report,
            [candidate, duplicate_path],
            recycler=lambda _path: None,
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


def test_reparse_point_in_parent_path_is_never_recycled(
    storage_fixture, tmp_path, monkeypatch
) -> None:
    report, candidate, _path = build_candidate_report(storage_fixture, tmp_path)
    monkeypatch.setattr(
        "storage.cleanup._has_reparse_component",
        lambda *_args: True,
    )
    recycled: list[Path] = []

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda path: recycled.append(path),
    )

    assert recycled == []
    assert record["summary"]["skipped_protected_or_invalid"] == 1
    assert "reparse point or link" in record["results"][0]["message"]


def test_unreadable_metadata_fails_safely_before_recycler(
    storage_fixture, tmp_path, monkeypatch
) -> None:
    report, candidate, path = build_candidate_report(storage_fixture, tmp_path)
    original_lstat = os.lstat

    def denied_lstat(value):
        if Path(value) == path:
            raise PermissionError("simulated locked metadata")
        return original_lstat(value)

    monkeypatch.setattr("storage.cleanup.os.lstat", denied_lstat)
    recycled: list[Path] = []

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda reviewed: recycled.append(reviewed),
    )

    assert recycled == []
    assert record["summary"]["failed"] == 1
    assert path.exists()


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


def build_empty_folder_candidate(tmp_path: Path, name: str = "Old Empty"):
    folder = tmp_path / name
    folder.mkdir()
    snapshot = _folder_tree_snapshot(folder)
    candidate = {
        "candidate_id": "folder-cleanup-001",
        "item_type": "folder",
        "path": str(folder),
        "scan_root": str(tmp_path),
        "name": folder.name,
        "extension": None,
        "size_bytes": 0,
        "allocated_size_bytes": 0,
        "created_at_utc": utc_timestamp(folder),
        "modified_at_utc": utc_timestamp(folder),
        "last_accessed_at_utc": None,
        "last_access_reliability": "unavailable",
        "storage_category": "user_content",
        "attributes": ["empty"],
        "evidence": [],
        "confidence": "high",
        "removal_risk": "low",
        "protection": {
            "eligibility": "eligible",
            "reason_code": None,
            "explanation": "Empty test directory inside the approved root.",
        },
        "is_regular_file": False,
        "is_directory": True,
        "is_reparse_point": False,
        "file_count": 0,
        "directory_count": 0,
        "newest_descendant_modified_at_utc": None,
        "oldest_descendant_modified_at_utc": None,
        "contains_unavailable_items": False,
        "contains_high_risk_items": False,
        "tree_metadata_fingerprint": snapshot[
            "tree_metadata_fingerprint"
        ],
    }
    report = {
        "generated_at_utc": "2026-08-03T00:00:00Z",
        "drive": {"drive_letter": folder.drive.upper()},
        "scan_scope": {
            "roots": [
                {
                    "requested_path": str(tmp_path),
                    "canonical_path": str(tmp_path),
                }
            ]
        },
    }
    return report, candidate, folder


def refresh_folder_candidate(candidate: dict, folder: Path) -> None:
    snapshot = _folder_tree_snapshot(folder)
    candidate.update(
        size_bytes=snapshot["size_bytes"],
        allocated_size_bytes=snapshot["allocated_size_bytes"],
        file_count=snapshot["file_count"],
        directory_count=snapshot["directory_count"],
        modified_at_utc=(
            snapshot["modified_at_utc"].isoformat().replace("+00:00", "Z")
        ),
        newest_descendant_modified_at_utc=(
            snapshot["newest_descendant_modified_at_utc"]
            .isoformat()
            .replace("+00:00", "Z")
            if snapshot["newest_descendant_modified_at_utc"]
            else None
        ),
        oldest_descendant_modified_at_utc=(
            snapshot["oldest_descendant_modified_at_utc"]
            .isoformat()
            .replace("+00:00", "Z")
            if snapshot["oldest_descendant_modified_at_utc"]
            else None
        ),
        tree_metadata_fingerprint=snapshot["tree_metadata_fingerprint"],
    )


def test_unchanged_empty_folder_can_be_sent_to_supplied_recycler(
    tmp_path: Path,
) -> None:
    report, candidate, folder = build_empty_folder_candidate(tmp_path)
    recycled: list[Path] = []

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda reviewed: recycled.append(reviewed),
    )

    assert recycled == [folder]
    assert folder.exists()
    assert record["results"][0]["item_type"] == "folder"
    assert record["summary"]["recycled"] == 1
    assert record["safety"]["directories_allowed"] is True
    validate_cleanup_record(record)


def test_scanner_folder_fingerprint_matches_cleanup_revalidation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    folder = root / "Old Empty"
    (folder / "nested").mkdir(parents=True)
    report = StorageScanner().scan(
        [root],
        options=ScannerOptions(discover_development_insights=False),
    )
    candidate = next(
        item
        for item in report["folder_candidates"]
        if item["name"] == "Old Empty"
    )

    reviewed, failure = revalidate_candidate(report, candidate)

    assert failure is None, failure["message"]
    assert reviewed == folder


def test_folder_changed_after_report_is_skipped(tmp_path: Path) -> None:
    report, candidate, folder = build_empty_folder_candidate(tmp_path)
    (folder / "new-file.txt").write_text("changed", encoding="utf-8")
    recycled: list[Path] = []

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda reviewed: recycled.append(reviewed),
    )

    assert recycled == []
    assert record["summary"]["skipped_changed"] == 1


def test_same_sized_folder_descendant_rename_is_detected(
    tmp_path: Path,
) -> None:
    report, candidate, folder = build_empty_folder_candidate(
        tmp_path, "Old Download"
    )
    original = folder / "old.part"
    original.write_bytes(b"same")
    refresh_folder_candidate(candidate, folder)
    original_stat = original.stat()
    replacement = folder / "renamed.part"
    original.rename(replacement)
    os.utime(
        replacement,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )

    record = execute_guided_cleanup(
        report,
        [candidate],
        recycler=lambda _reviewed: pytest.fail("changed tree was recycled"),
    )

    assert record["summary"]["skipped_changed"] == 1


def test_overlapping_parent_and_child_folders_are_rejected(
    tmp_path: Path,
) -> None:
    report, parent, folder = build_empty_folder_candidate(tmp_path, "Parent")
    child_path = folder / "Child"
    child_path.mkdir()
    child = copy.deepcopy(parent)
    child.update(
        candidate_id="folder-cleanup-002",
        path=str(child_path),
        scan_root=str(tmp_path),
        name="Child",
        modified_at_utc=utc_timestamp(child_path),
    )

    with pytest.raises(CleanupRecordError, match="overlapping parent and child"):
        execute_guided_cleanup(
            report,
            [parent, child],
            recycler=lambda _path: None,
        )


def test_mixed_file_and_folder_cleanup_is_rejected(
    storage_fixture, tmp_path: Path
) -> None:
    report, file_candidate, _file_path = build_candidate_report(
        storage_fixture, tmp_path
    )
    _folder_report, folder_candidate, _folder = build_empty_folder_candidate(
        tmp_path, "Empty Folder"
    )

    with pytest.raises(CleanupRecordError, match="cannot mix"):
        execute_guided_cleanup(
            report,
            [file_candidate, folder_candidate],
            recycler=lambda _path: None,
        )
