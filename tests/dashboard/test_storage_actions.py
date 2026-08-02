from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.storage_actions import (
    StorageActionError,
    open_containing_folder,
    validate_candidate_for_folder_action,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_STORAGE_REPORT = (
    REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
)


def sample_report() -> dict:
    return json.loads(SAMPLE_STORAGE_REPORT.read_text(encoding="utf-8"))


def test_eligible_candidate_resolves_to_reported_containing_folder() -> None:
    report = sample_report()

    folder = validate_candidate_for_folder_action(report, report["candidates"][0])

    assert str(folder) == r"C:\Users\fictional.jamie\Downloads"


def test_unavailable_candidate_cannot_open_folder() -> None:
    report = sample_report()
    candidate = report["candidates"][0]
    candidate["protection"]["eligibility"] = "unavailable"

    with pytest.raises(StorageActionError, match="Protected or unavailable"):
        validate_candidate_for_folder_action(report, candidate)


def test_candidate_outside_approved_roots_is_rejected() -> None:
    report = sample_report()
    candidate = report["candidates"][0]
    candidate["path"] = r"C:\Unapproved\outside.tmp"

    with pytest.raises(StorageActionError, match="outside the approved"):
        validate_candidate_for_folder_action(report, candidate)


def test_candidate_on_another_drive_is_rejected() -> None:
    report = sample_report()
    candidate = report["candidates"][0]
    candidate["path"] = r"D:\Other\outside.tmp"

    with pytest.raises(StorageActionError, match="analysed drive"):
        validate_candidate_for_folder_action(report, candidate)


def test_open_containing_folder_uses_windows_shell(
    tmp_path: Path, monkeypatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "dashboard.storage_actions.os.startfile",
        lambda path: opened.append(path),
    )

    open_containing_folder(tmp_path)

    assert opened == [str(tmp_path)]


def test_missing_containing_folder_is_not_opened(tmp_path: Path) -> None:
    with pytest.raises(StorageActionError, match="no longer exists"):
        open_containing_folder(tmp_path / "missing")
