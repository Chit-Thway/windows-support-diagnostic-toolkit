from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from storage import __main__ as storage_cli
from storage.scanner import ScanConfigurationError


def test_default_output_is_timestamped_under_ignored_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_cli, "STORAGE_REPORT_DIRECTORY", tmp_path)

    output = storage_cli.resolve_output_path(
        None,
        now=datetime(2026, 7, 25, 12, 30, tzinfo=timezone.utc),
    )

    assert output == tmp_path / "storage-report-20260725-123000Z.json"


def test_bare_output_filename_is_kept_in_report_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_cli, "STORAGE_REPORT_DIRECTORY", tmp_path)

    output = storage_cli.resolve_output_path("first-storage-report.json")

    assert output == (tmp_path / "first-storage-report.json").resolve()


def test_output_outside_ignored_directory_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_directory = tmp_path / "storage-reports"
    outside = tmp_path / "public-report.json"
    monkeypatch.setattr(
        storage_cli,
        "STORAGE_REPORT_DIRECTORY",
        report_directory,
    )

    with pytest.raises(ScanConfigurationError, match="must be written under"):
        storage_cli.resolve_output_path(str(outside))


def test_non_json_output_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage_cli, "STORAGE_REPORT_DIRECTORY", tmp_path)

    with pytest.raises(ScanConfigurationError, match="must end in .json"):
        storage_cli.resolve_output_path("report.txt")
