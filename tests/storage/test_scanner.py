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
