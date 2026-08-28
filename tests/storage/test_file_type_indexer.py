from __future__ import annotations

import json
import os
from collections import namedtuple
from pathlib import Path

import pytest

from storage.file_type_contract import (
    FileTypeIndexWriteError,
    validate_file_type_index,
    write_file_type_index,
)
from storage.file_type_indexer import (
    FileTypeIndexConfigurationError,
    FileTypeIndexer,
    FileTypeIndexerOptions,
    resolve_file_type_output_path,
)
from storage.path_policy import ProtectedPathPolicy

DiskUsage = namedtuple("DiskUsage", "total used free")


def _test_indexer(root: Path, *, protected: tuple[Path, ...] = ()) -> FileTypeIndexer:
    drive_letter = root.drive.upper()
    assert drive_letter
    return FileTypeIndexer(
        path_policy=ProtectedPathPolicy(protected_roots=protected),
        root_validator=lambda _requested: (root.resolve(), drive_letter),
        disk_usage=lambda _path: DiskUsage(total=10_000, used=6_000, free=4_000),
        volume_information=lambda _drive: ("Test Volume", "NTFS"),
    )


def _write_fixture_tree(root: Path) -> dict[str, Path]:
    downloads = root / "Users" / "fictional.user" / "Downloads"
    appdata = root / "Users" / "fictional.user" / "AppData" / "Local"
    protected = root / "Protected"
    empty = root / "Empty" / "Nested"
    for folder in (downloads, appdata, protected, empty):
        folder.mkdir(parents=True)

    (downloads / "large.pdf").write_bytes(b"p" * 300)
    (downloads / "small.pdf").write_bytes(b"p" * 50)
    (downloads / "clip.mp4").write_bytes(b"v" * 200)
    (downloads / "ignored.bin").write_bytes(b"b" * 400)
    (appdata / "cache.pdf").write_bytes(b"c" * 40)
    (protected / "setup.exe").write_bytes(b"e" * 50)
    (empty / "placeholder.pdf").write_bytes(b"")
    return {
        "downloads": downloads,
        "appdata": appdata,
        "protected": protected,
        "empty": root / "Empty",
    }


def _folder(report: dict, path: Path) -> dict:
    return next(row for row in report["folders"] if row["path"] == str(path))


def _extension_total(folder: dict, extension: str) -> dict:
    return next(
        total
        for total in folder["extension_totals"]
        if total["extension"] == extension
    )


def test_one_pass_index_builds_exact_folder_and_extension_totals(
    tmp_path: Path,
) -> None:
    paths = _write_fixture_tree(tmp_path)
    indexer = _test_indexer(tmp_path, protected=(paths["protected"],))

    report = indexer.index_drive(tmp_path)

    validate_file_type_index(report)
    assert report["scan"]["status"] == "complete"
    assert report["scan"]["aggregate_coverage"] == "exact"
    assert report["scan"]["files_examined"] == 7
    assert report["scan"]["logical_bytes_observed"] == 1_040
    assert report["scan"]["matching_files"] == 6
    assert report["scan"]["matching_logical_bytes"] == 640
    root = _folder(report, tmp_path.resolve())
    assert root["recursive_logical_bytes"] == 1_040
    assert _extension_total(root, ".pdf")["recursive_file_count"] == 4
    assert _extension_total(root, ".pdf")["recursive_logical_bytes"] == 390
    downloads = _folder(report, paths["downloads"])
    assert downloads["direct_logical_bytes"] == 950
    assert _extension_total(downloads, ".pdf")["direct_logical_bytes"] == 350


def test_zero_byte_files_are_counted_and_collapsed_not_retained(
    tmp_path: Path,
) -> None:
    paths = _write_fixture_tree(tmp_path)
    report = _test_indexer(tmp_path).index_drive(tmp_path)

    assert report["scan"]["zero_byte_files"] == 1
    assert report["empty_summary"]["zero_byte_files"] == 1
    assert report["empty_summary"]["collapsed_tree_count"] == 1
    tree = report["empty_summary"]["trees"][0]
    assert tree["path"] == str(paths["empty"])
    assert tree["descendant_zero_byte_files"] == 1
    assert tree["descendant_directories"] == 1
    assert tree["recoverable_bytes"] == 0
    assert all(row["size_bytes"] > 0 for row in report["files"])
    assert report["file_detail_summary"]["omitted_files"] == 1


def test_file_details_are_bounded_by_largest_logical_size(
    tmp_path: Path,
) -> None:
    _write_fixture_tree(tmp_path)
    report = _test_indexer(tmp_path).index_drive(
        tmp_path,
        options=FileTypeIndexerOptions(maximum_file_details=2),
    )

    assert [row["name"] for row in report["files"]] == [
        "large.pdf",
        "clip.mp4",
    ]
    assert report["file_detail_summary"] == {
        "coverage": "bounded",
        "retained_files": 2,
        "omitted_files": 4,
        "retained_logical_bytes": 500,
        "omitted_logical_bytes": 140,
    }
    validate_file_type_index(report)


def test_application_and_protected_paths_are_not_bulk_selectable(
    tmp_path: Path,
) -> None:
    paths = _write_fixture_tree(tmp_path)
    report = _test_indexer(
        tmp_path, protected=(paths["protected"],)
    ).index_drive(tmp_path)
    by_name = {row["name"]: row for row in report["files"]}

    assert by_name["cache.pdf"]["selection_state"] == "review_only"
    assert by_name["setup.exe"]["selection_state"] == "protected"
    assert by_name["large.pdf"]["selection_state"] == "selectable"


def test_custom_extension_is_normalized_and_kept_review_only(
    tmp_path: Path,
) -> None:
    custom = "." + "a" * 64
    (tmp_path / f"artifact{custom}").write_bytes(b"x" * 25)

    report = _test_indexer(tmp_path).index_drive(
        tmp_path,
        options=FileTypeIndexerOptions(custom_extensions=(custom.upper(),)),
    )

    assert report["custom_extensions"] == [custom]
    assert report["files"][0]["extension"] == custom
    assert report["files"][0]["selection_state"] == "review_only"
    validate_file_type_index(report)


def test_directory_error_preserves_partial_observations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accessible = tmp_path / "Accessible"
    unavailable = tmp_path / "Unavailable"
    accessible.mkdir()
    unavailable.mkdir()
    (accessible / "kept.pdf").write_bytes(b"x" * 20)
    real_scandir = os.scandir

    def denied_scandir(path):
        if Path(path) == unavailable:
            raise PermissionError("Fictional access denial")
        return real_scandir(path)

    monkeypatch.setattr("storage.file_type_indexer.os.scandir", denied_scandir)
    report = _test_indexer(tmp_path).index_drive(tmp_path)

    assert report["scan"]["status"] == "partial"
    assert report["scan"]["aggregate_coverage"] == "partial"
    assert report["scan"]["matching_files"] == 1
    assert report["inaccessible_paths"][0]["error_type"] == "access_denied"
    assert _folder(report, unavailable)["access"]["state"] == "unavailable"
    validate_file_type_index(report)


def test_reparse_directory_is_recorded_without_following_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = tmp_path / "Linked"
    linked.mkdir()
    (linked / "must-not-be-indexed.pdf").write_bytes(b"x" * 20)

    monkeypatch.setattr(
        "storage.file_type_indexer._entry_is_reparse",
        lambda entry, _metadata: entry.name == "Linked",
    )
    report = _test_indexer(tmp_path).index_drive(tmp_path)

    assert report["scan"]["status"] == "partial"
    assert report["scan"]["matching_files"] == 0
    assert _folder(report, linked)["access"]["state"] == "unavailable"
    assert report["inaccessible_paths"][0]["error_type"] == "reparse_point"
    assert any(
        error["code"] == "reparse_point_skipped"
        for error in report["scan_errors"]
    )
    validate_file_type_index(report)


def test_cancellation_returns_a_valid_partial_index(tmp_path: Path) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    for index in range(20):
        (downloads / f"document-{index}.pdf").write_bytes(b"x" * 10)
    checks = 0

    def cancel_check() -> bool:
        nonlocal checks
        checks += 1
        return checks > 8

    report = _test_indexer(tmp_path).index_drive(
        tmp_path, cancel_check=cancel_check
    )

    assert report["scan"]["status"] == "cancelled"
    assert report["scan"]["aggregate_coverage"] == "partial"
    assert any(error["code"] == "scan_cancelled" for error in report["scan_errors"])
    validate_file_type_index(report)


def test_progress_reports_observed_counts_without_percentage(
    tmp_path: Path,
) -> None:
    (tmp_path / "one.pdf").write_bytes(b"x")
    updates = []

    _test_indexer(tmp_path).index_drive(
        tmp_path,
        options=FileTypeIndexerOptions(progress_every_entries=1),
        progress_callback=updates.append,
    )

    assert updates
    assert updates[-1].files_examined == 1
    assert updates[-1].matching_files == 1
    assert not hasattr(updates[-1], "percent_complete")


def test_each_discovered_directory_is_enumerated_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "First"
    second = first / "Second"
    second.mkdir(parents=True)
    (first / "one.pdf").write_bytes(b"1")
    (second / "two.mp4").write_bytes(b"22")
    real_scandir = os.scandir
    calls: dict[str, int] = {}

    def counted_scandir(path):
        key = os.path.normcase(os.path.abspath(str(path)))
        calls[key] = calls.get(key, 0) + 1
        return real_scandir(path)

    monkeypatch.setattr("storage.file_type_indexer.os.scandir", counted_scandir)
    report = _test_indexer(tmp_path).index_drive(tmp_path)

    assert report["scan"]["directories_examined"] == 3
    assert len(calls) == 3
    assert set(calls.values()) == {1}


def test_public_validator_rejects_a_nested_scan_root(tmp_path: Path) -> None:
    with pytest.raises(
        FileTypeIndexConfigurationError, match="whole-drive root"
    ):
        FileTypeIndexer._validate_drive_root(tmp_path)


def test_writer_uses_utf8_and_never_overwrites(
    tmp_path: Path,
) -> None:
    (tmp_path / "résumé.pdf").write_bytes(b"x")
    report = _test_indexer(tmp_path).index_drive(tmp_path)
    output = tmp_path / "index.json"

    written = write_file_type_index(report, output)

    payload = written.read_text(encoding="utf-8")
    assert "résumé.pdf" in payload
    assert json.loads(payload)["index_type"] == "file_type_index"
    with pytest.raises(FileTypeIndexWriteError, match="already exists"):
        write_file_type_index(report, output)

    report["index_id"] = "replacement-index"
    refreshed = write_file_type_index(
        report, output, replace_existing=True
    )
    assert json.loads(refreshed.read_text(encoding="utf-8"))["index_id"] == (
        "replacement-index"
    )


def test_default_output_is_one_stable_ignored_cache_per_drive() -> None:
    output = resolve_file_type_output_path(None, drive_letter="F:")

    assert output.parent.name == "storage-reports"
    assert output.name == "file-type-index-f.json"


def test_output_resolution_stays_in_ignored_report_directory() -> None:
    output = resolve_file_type_output_path(
        "c-file-type-index.json", drive_letter="C:"
    )

    assert output.parent.name == "storage-reports"
    assert output.name == "c-file-type-index.json"
