from __future__ import annotations

import json
import os
import stat
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from storage.classifier import ClassificationOptions
from storage.contract import (
    StorageReportWriteError,
    validate_storage_report,
    write_storage_report,
)
from storage.development import CommandResult, DevelopmentInsightsInspector
from storage.path_policy import FILE_ATTRIBUTE_REPARSE_POINT, ProtectedPathPolicy
from storage.scanner import ScannerOptions, StorageScanner

FIXED_NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
DiskUsage = namedtuple("DiskUsage", "total used free")


def make_scanner() -> StorageScanner:
    return StorageScanner(
        path_policy=ProtectedPathPolicy(protected_roots=()),
        clock=lambda: FIXED_NOW,
        disk_usage=lambda _path: DiskUsage(
            total=100_000_000,
            used=60_000_000,
            free=40_000_000,
        ),
        volume_information=lambda _drive: ("Fictional Test", "NTFS"),
        allocated_size=lambda _path, logical_size: logical_size,
    )


def set_modified_time(path: Path, timestamp: datetime) -> None:
    epoch = timestamp.timestamp()
    os.utime(path, (epoch, epoch))


def test_scanner_collects_metadata_without_changing_files(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    candidate = root / "fictional-video.iso.part"
    candidate.write_bytes(b"synthetic payload")
    ordinary = root / "recent.txt"
    ordinary.write_text("unchanged", encoding="utf-8")
    set_modified_time(candidate, datetime(2020, 1, 1, tzinfo=timezone.utc))
    before = {
        path: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
        for path in (candidate, ordinary)
    }
    options = ScannerOptions(
        classification=ClassificationOptions(
            stale_after_days=365,
            large_file_threshold_bytes=10,
            incomplete_min_age_hours=1,
            temporary_min_age_hours=1,
        )
    )

    report = make_scanner().scan([root], options=options)

    validate_storage_report(report)
    assert report["scan"]["status"] == "complete"
    assert report["scan"]["files_examined"] == 2
    assert report["candidate_summary"]["total_unique_candidates"] == 1
    assert report["candidates"][0]["attributes"] == [
        "stale",
        "likely_incomplete",
        "large",
    ]
    assert report["candidates"][0]["last_access_reliability"] == "limited"
    assert report["scan_scope"]["options"][
        "use_last_access_as_classification_evidence"
    ] is False

    after = {
        path: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
        for path in (candidate, ordinary)
    }
    assert after == before


def test_candidate_details_are_bounded_but_aggregates_remain_complete(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    for name in ("a.tmp", "b.tmp", "c.tmp"):
        path = root / name
        path.write_bytes(b"x")
        set_modified_time(path, datetime(2020, 1, 1, tzinfo=timezone.utc))
    options = ScannerOptions(
        classification=ClassificationOptions(
            stale_after_days=1,
            large_file_threshold_bytes=1000,
            incomplete_min_age_hours=1,
            temporary_min_age_hours=1,
        ),
        maximum_candidates_retained=1,
    )

    report = make_scanner().scan([root], options=options)

    validate_storage_report(report)
    assert report["scan"]["detail_coverage"] == "bounded"
    assert report["candidate_summary"]["total_unique_candidates"] == 3
    assert report["candidate_summary"]["retained_candidates"] == 1
    assert report["candidate_summary"]["omitted_candidates"] == 2
    assert report["candidate_summary"]["total_unique_candidate_bytes"] == 3
    assert report["candidate_summary"]["attributes"]["temporary"] == {
        "candidate_count": 3,
        "unique_bytes": 3,
    }


def test_progress_reports_observations_without_a_false_percentage(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    (root / "two.txt").write_text("two", encoding="utf-8")
    updates = []

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(progress_every_files=1),
        progress_callback=updates.append,
    )

    assert report["scan"]["files_examined"] == 2
    assert updates
    assert updates[-1].files_examined == 2
    assert not hasattr(updates[-1], "percent_complete")


def test_cancellation_returns_a_valid_partial_report(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(name, encoding="utf-8")
    calls = 0

    def cancel_after_first_file() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 4

    report = make_scanner().scan([root], cancel_check=cancel_after_first_file)

    validate_storage_report(report)
    assert report["scan"]["status"] == "cancelled"
    assert report["scan"]["files_examined"] == 1
    assert report["scan_scope"]["roots"][0]["status"] == "cancelled"
    assert any(
        error["code"] == "scan_cancelled"
        for error in report["scan_errors"]
    )


class FakeScandir:
    def __init__(self, entries) -> None:
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, _error_type, _error, _traceback) -> None:
        return None


class MissingEntry:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.name = path.name

    def stat(self, *, follow_symlinks: bool):
        assert follow_symlinks is False
        raise FileNotFoundError(self.path)


class DeniedEntry:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.name = path.name

    def stat(self, *, follow_symlinks: bool):
        assert follow_symlinks is False
        raise PermissionError(self.path)


class RegularMetadataEntry:
    def __init__(self, path: Path, *, inode: int = 1) -> None:
        self.path = str(path)
        self.name = path.name
        self._inode = inode

    def stat(self, *, follow_symlinks: bool):
        assert follow_symlinks is False
        timestamp = FIXED_NOW.timestamp()
        return SimpleNamespace(
            st_mode=stat.S_IFREG,
            st_size=5,
            st_ctime=timestamp,
            st_mtime=timestamp,
            st_atime=timestamp,
            st_ino=self._inode,
            st_dev=1,
            st_file_attributes=0,
        )


class ReparseEntry:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.name = path.name

    def stat(self, *, follow_symlinks: bool):
        assert follow_symlinks is False
        return SimpleNamespace(
            st_mode=stat.S_IFLNK,
            st_file_attributes=FILE_ATTRIBUTE_REPARSE_POINT,
        )


def test_disappearing_file_is_recorded_and_scan_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    missing = root / "disappeared.part"
    monkeypatch.setattr(
        "storage.scanner.os.scandir",
        lambda _directory: FakeScandir([MissingEntry(missing)]),
    )

    report = make_scanner().scan([root])

    validate_storage_report(report)
    assert report["scan"]["status"] == "partial"
    assert report["inaccessible_paths"][0]["error_type"] == "disappeared"
    assert report["candidates"][0]["attributes"] == ["unavailable"]
    assert report["candidates"][0]["protection"]["eligibility"] == (
        "unavailable"
    )


def test_access_denied_is_bounded_and_remaining_scan_stays_useful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    denied = [DeniedEntry(root / f"locked-{index}.tmp") for index in range(5)]
    monkeypatch.setattr(
        "storage.scanner.os.scandir",
        lambda _directory: FakeScandir(denied),
    )

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(
            maximum_issue_records=2,
            discover_development_insights=False,
        ),
    )

    validate_storage_report(report)
    assert report["scan"]["status"] == "partial"
    assert len(report["inaccessible_paths"]) == 2
    assert len(report["scan_errors"]) == 2
    assert report["scan"]["inaccessible_path_details_omitted"] == 3
    assert report["scan"]["scan_error_details_omitted"] == 3
    assert report["candidate_summary"]["total_unique_candidates"] == 5
    assert all(
        item["error_type"] == "access_denied"
        for item in report["inaccessible_paths"]
    )
    assert any("additional inaccessible-path" in item for item in report["limitations"])


def test_content_lock_does_not_block_metadata_only_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    locked = root / "content-locked.bin"
    locked.write_bytes(b"metadata remains available")

    def reject_content_open(*_args, **_kwargs):
        raise PermissionError("simulated content lock")

    monkeypatch.setattr(Path, "open", reject_content_open)

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(discover_development_insights=False),
    )

    assert report["scan"]["status"] == "complete"
    assert report["scan"]["files_examined"] == 1
    assert report["scan"]["bytes_examined"] == len(b"metadata remains available")


def test_long_metadata_path_is_recorded_without_content_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    long_path = root / (("fictional-segment-" * 20) + ".bin")
    monkeypatch.setattr(
        "storage.scanner.os.scandir",
        lambda _directory: FakeScandir([RegularMetadataEntry(long_path)]),
    )

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(discover_development_insights=False),
    )

    validate_storage_report(report)
    assert report["scan"]["status"] == "complete"
    assert report["scan"]["files_examined"] == 1


def test_large_candidate_set_keeps_aggregate_totals_when_details_are_bounded(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    for index in range(250):
        (root / f"empty-{index:03d}.tmp").touch()

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(
            maximum_candidates_retained=25,
            discover_development_insights=False,
        ),
    )

    validate_storage_report(report)
    assert report["scan"]["detail_coverage"] == "bounded"
    assert report["candidate_summary"]["total_unique_candidates"] == 250
    assert report["candidate_summary"]["retained_candidates"] == 25
    assert report["candidate_summary"]["omitted_candidates"] == 225


def test_bounded_candidates_retain_largest_physical_records_first(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    stale = root / "a-stale.bin"
    stale.write_bytes(b"ordinary stale file that is physically larger")
    incomplete = root / "z-download.part"
    incomplete.write_bytes(b"partial")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    set_modified_time(stale, old)
    set_modified_time(incomplete, old)

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(
            maximum_candidates_retained=1,
            discover_development_insights=False,
        ),
    )

    validate_storage_report(report)
    assert report["candidate_summary"]["total_unique_candidates"] == 2
    assert report["candidates"][0]["name"] == "a-stale.bin"
    assert report["candidates"][0]["allocated_size_bytes"] == len(
        b"ordinary stale file that is physically larger"
    )


def test_bounded_candidate_size_ties_use_safety_and_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    stale = root / "a-stale.bin"
    stale.write_bytes(b"1234567")
    incomplete = root / "z-download.part"
    incomplete.write_bytes(b"1234567")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    set_modified_time(stale, old)
    set_modified_time(incomplete, old)

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(
            maximum_candidates_retained=1,
            discover_development_insights=False,
        ),
    )

    validate_storage_report(report)
    assert report["candidates"][0]["name"] == "z-download.part"
    assert report["candidates"][0]["removal_risk"] == "low"


def test_hash_length_extension_remains_valid_report_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Cache"
    root.mkdir()
    hash_suffix = "8aa3f8beea3f8dfb32ecd478c874ca438d2eb07f78decf5b0a7121c3557c45ed"
    candidate = root / f"entry.{hash_suffix}"
    candidate.write_bytes(b"fictional cache payload")
    set_modified_time(candidate, datetime(2020, 1, 1, tzinfo=timezone.utc))

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(discover_development_insights=False),
    )

    validate_storage_report(report)
    assert report["candidates"][0]["extension"] == f".{hash_suffix}"


def test_installer_candidate_is_high_risk_and_review_only(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    installer = root / "Zoom.msi"
    installer.write_bytes(b"fictional installer")
    set_modified_time(installer, datetime(2020, 1, 1, tzinfo=timezone.utc))

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(discover_development_insights=False),
    )

    validate_storage_report(report)
    candidate = report["candidates"][0]
    assert candidate["removal_risk"] == "high"
    assert candidate["protection"]["eligibility"] == "review_only"
    assert candidate["protection"]["reason_code"] == "application_or_installer_file"


def test_protected_files_contribute_to_accounting_but_not_candidates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "DriveRoot"
    protected = root / "Windows"
    protected.mkdir(parents=True)
    system_file = protected / "system.bin"
    system_file.write_bytes(b"protected bytes")
    set_modified_time(system_file, datetime(2020, 1, 1, tzinfo=timezone.utc))
    scanner = StorageScanner(
        path_policy=ProtectedPathPolicy(protected_roots=(protected,)),
        clock=lambda: FIXED_NOW,
        disk_usage=lambda _path: DiskUsage(
            total=100_000_000,
            used=60_000_000,
            free=40_000_000,
        ),
        volume_information=lambda _drive: ("Fictional Test", "NTFS"),
        allocated_size=lambda _path, logical_size: logical_size,
    )

    report = scanner.scan(
        [root],
        options=ScannerOptions(discover_development_insights=False),
    )

    validate_storage_report(report)
    assert report["candidates"] == []
    assert report["accounting"]["categories"]["protected_system"]["bytes"] == len(
        b"protected bytes"
    )


def test_allocated_sizes_drive_accounting_instead_of_logical_sizes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "DriveRoot"
    root.mkdir()
    sparse_like = root / "fictional-sparse.bin"
    sparse_like.write_bytes(b"logical payload")
    scanner = StorageScanner(
        path_policy=ProtectedPathPolicy(protected_roots=()),
        clock=lambda: FIXED_NOW,
        disk_usage=lambda _path: DiskUsage(total=1000, used=600, free=400),
        volume_information=lambda _drive: ("Fictional Test", "NTFS"),
        allocated_size=lambda _path, _logical_size: 100,
    )

    report = scanner.scan(
        [root],
        options=ScannerOptions(discover_development_insights=False),
    )

    validate_storage_report(report)
    assert report["scan"]["bytes_examined"] == len(b"logical payload")
    assert report["scan"]["allocated_bytes_examined"] == 100
    assert report["accounting"]["categories"]["user_content"]["bytes"] == 100
    assert report["accounting"]["categories"]["other_or_unreadable"]["bytes"] == 500


def test_hard_linked_file_is_counted_once_when_identity_is_available(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    original = root / "original.bin"
    linked = root / "linked.bin"
    original.write_bytes(b"one physical file")
    try:
        os.link(original, linked)
    except OSError:
        pytest.skip("Hard links are unavailable on this test volume.")
    report = make_scanner().scan(
        [root],
        options=ScannerOptions(discover_development_insights=False),
    )

    validate_storage_report(report)
    assert report["scan"]["files_examined"] == 1
    assert report["scan"]["bytes_examined"] == len(b"one physical file")


def test_cancellation_before_first_root_marks_every_root_skipped(
    tmp_path: Path,
) -> None:
    first = tmp_path / "First"
    second = tmp_path / "Second"
    first.mkdir()
    second.mkdir()

    report = make_scanner().scan(
        [first, second],
        options=ScannerOptions(discover_development_insights=False),
        cancel_check=lambda: True,
    )

    validate_storage_report(report)
    assert report["scan"]["status"] == "cancelled"
    assert [root["status"] for root in report["scan_scope"]["roots"]] == [
        "skipped",
        "skipped",
    ]


def test_reparse_point_is_not_followed_and_is_protected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    link = root / "fictional-junction"
    monkeypatch.setattr(
        "storage.scanner.os.scandir",
        lambda _directory: FakeScandir([ReparseEntry(link)]),
    )

    report = make_scanner().scan([root])

    validate_storage_report(report)
    assert report["scan"]["status"] == "partial"
    assert report["inaccessible_paths"][0]["error_type"] == "reparse_point"
    assert report["candidates"][0]["attributes"] == ["protected"]
    assert report["candidates"][0]["is_reparse_point"] is True
    assert report["candidates"][0]["protection"]["eligibility"] == "protected"


def test_explicit_development_cache_is_classified_separately(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Development"
    cache = root / "fictional-pip-cache"
    cache.mkdir(parents=True)
    cache_file = cache / "wheel.bin"
    cache_file.write_bytes(b"cache")
    options = ScannerOptions(development_cache_roots=(cache,))

    report = make_scanner().scan([root], options=options)

    validate_storage_report(report)
    assert report["candidates"][0]["attributes"] == ["development_cache"]
    assert report["accounting"]["categories"][
        "development_tools_and_caches"
    ]["bytes"] == len(b"cache")


def test_python_environment_is_informational_and_never_a_candidate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Development"
    environment_root = root / "support-app" / ".venv"
    package = environment_root / "Lib" / "site-packages" / "old.py"
    package.parent.mkdir(parents=True)
    marker = environment_root / "pyvenv.cfg"
    marker.write_text("fictional marker", encoding="utf-8")
    package.write_bytes(b"old package")
    set_modified_time(package, datetime(2020, 1, 1, tzinfo=timezone.utc))
    options = ScannerOptions(
        classification=ClassificationOptions(
            stale_after_days=1,
            large_file_threshold_bytes=1,
            incomplete_min_age_hours=1,
            temporary_min_age_hours=1,
        )
    )

    report = make_scanner().scan([root], options=options)

    validate_storage_report(report)
    environment = next(
        location
        for location in report["development_insights"]["locations"]
        if location["kind"] == "virtual_environment"
    )
    assert report["candidates"] == []
    assert environment["automatic_cleanup_candidate"] is False
    assert environment["bytes_observed"] == (
        marker.stat().st_size + package.stat().st_size
    )
    assert report["accounting"]["categories"][
        "development_tools_and_caches"
    ]["bytes"] == environment["bytes_observed"]


def test_supported_pip_cache_uses_guidance_instead_of_file_candidates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Development"
    cache = root / "pip-cache"
    cache.mkdir(parents=True)
    wheel = cache / "old.whl"
    wheel.write_bytes(b"cache payload")
    set_modified_time(wheel, datetime(2020, 1, 1, tzinfo=timezone.utc))

    def inspector_factory(**kwargs):
        return DevelopmentInsightsInspector(
            **kwargs,
            python_executable=r"C:\FictionalPython\python.exe",
            command_runner=lambda _command: CommandResult(0, str(cache), ""),
            java_finder=lambda _name: None,
        )

    scanner = StorageScanner(
        path_policy=ProtectedPathPolicy(protected_roots=()),
        clock=lambda: FIXED_NOW,
        disk_usage=lambda _path: DiskUsage(
            total=100_000_000,
            used=60_000_000,
            free=40_000_000,
        ),
        volume_information=lambda _drive: ("Fictional Test", "NTFS"),
        development_inspector_factory=inspector_factory,
    )

    report = scanner.scan([root])

    validate_storage_report(report)
    cache_location = next(
        location
        for location in report["development_insights"]["locations"]
        if location["kind"] == "package_cache"
    )
    assert report["candidates"] == []
    assert cache_location["suggested_command"] == "python -m pip cache purge"
    assert cache_location["bytes_observed"] == wheel.stat().st_size


def test_validated_report_is_written_as_utf8_and_not_overwritten(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    (root / "empty.txt").touch()
    report = make_scanner().scan([root])
    output = tmp_path / "storage-report.json"

    written = write_storage_report(report, output)

    assert written == output.resolve()
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["report_type"] == "storage_analysis"
    with pytest.raises(StorageReportWriteError, match="already exists"):
        write_storage_report(report, output)


def test_folder_candidates_aggregate_descendant_metadata_once(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    old_folder = root / "OldGame" / "Saves"
    old_folder.mkdir(parents=True)
    first = old_folder / "slot-one.dat"
    second = old_folder / "slot-two.dat"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
    set_modified_time(first, old_time)
    set_modified_time(second, old_time)

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(
            classification=ClassificationOptions(
                stale_after_days=365,
                large_file_threshold_bytes=1000,
                incomplete_min_age_hours=1,
                temporary_min_age_hours=1,
            )
        ),
    )

    validate_storage_report(report)
    old_game = next(
        candidate
        for candidate in report["folder_candidates"]
        if candidate["name"] == "OldGame"
    )
    assert old_game["item_type"] == "folder"
    assert old_game["file_count"] == 2
    assert old_game["directory_count"] == 1
    assert old_game["size_bytes"] == 11
    assert old_game["allocated_size_bytes"] == 11
    assert old_game["attributes"] == ["stale"]
    assert old_game["contains_high_risk_items"] is True
    assert old_game["protection"]["eligibility"] == "review_only"
    assert len(old_game["tree_metadata_fingerprint"]) == 64


def test_nested_empty_directories_collapse_to_highest_useful_ancestor(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    deepest = root / "Cyberpunk" / "gamedata" / "temp" / "dlc"
    deepest.mkdir(parents=True)

    report = make_scanner().scan([root])

    validate_storage_report(report)
    empty_paths = [
        Path(candidate["path"])
        for candidate in report["folder_candidates"]
        if "empty" in candidate["attributes"]
    ]
    assert empty_paths == [root / "Cyberpunk"]


def test_unavailable_folder_state_overrides_high_risk_descendant_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Downloads"
    archive = root / "OldArchive"
    archive.mkdir(parents=True)
    old_file = archive / "notes.txt"
    old_file.write_text("fictional notes", encoding="utf-8")
    set_modified_time(old_file, datetime(2020, 1, 1, tzinfo=timezone.utc))
    reparse_like = archive / "linked-location"
    reparse_like.write_text("synthetic reparse metadata", encoding="utf-8")
    monkeypatch.setattr(
        "storage.scanner.is_reparse_point",
        lambda path, _metadata: path == reparse_like,
    )

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(discover_development_insights=False),
    )

    validate_storage_report(report)
    folder = next(
        candidate
        for candidate in report["folder_candidates"]
        if candidate["name"] == "OldArchive"
    )
    assert folder["contains_unavailable_items"] is True
    assert folder["contains_high_risk_items"] is True
    assert folder["protection"]["eligibility"] == "unavailable"
    assert folder["removal_risk"] == "protected"


def test_folder_is_stale_only_when_every_descendant_is_old(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    mixed = root / "MixedAge"
    mixed.mkdir(parents=True)
    old_file = mixed / "old.txt"
    recent_file = mixed / "recent.txt"
    old_file.write_text("old", encoding="utf-8")
    recent_file.write_text("recent", encoding="utf-8")
    set_modified_time(old_file, datetime(2020, 1, 1, tzinfo=timezone.utc))
    set_modified_time(recent_file, datetime(2026, 7, 24, tzinfo=timezone.utc))

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(
            classification=ClassificationOptions(
                stale_after_days=365,
                large_file_threshold_bytes=1000,
                incomplete_min_age_hours=1,
                temporary_min_age_hours=1,
            )
        ),
    )

    assert all(
        candidate["name"] != "MixedAge"
        for candidate in report["folder_candidates"]
    )


def test_large_only_folder_is_visible_but_review_only(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    large_folder = root / "RecentProject"
    large_folder.mkdir(parents=True)
    (large_folder / "payload.bin").write_bytes(b"large")

    report = make_scanner().scan(
        [root],
        options=ScannerOptions(
            classification=ClassificationOptions(
                stale_after_days=365,
                large_file_threshold_bytes=1,
                incomplete_min_age_hours=1,
                temporary_min_age_hours=1,
            )
        ),
    )

    candidate = next(
        item
        for item in report["folder_candidates"]
        if item["name"] == "RecentProject"
    )
    assert candidate["attributes"] == ["large"]
    assert candidate["removal_risk"] == "high"
    assert candidate["protection"]["eligibility"] == "review_only"


def test_recent_incomplete_folder_name_waits_for_age_threshold(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    partial = root / "active.download"
    partial.mkdir(parents=True)

    report = make_scanner().scan([root])

    candidate = next(
        item
        for item in report["folder_candidates"]
        if item["name"] == "active.download"
    )
    assert "likely_incomplete" not in candidate["attributes"]
