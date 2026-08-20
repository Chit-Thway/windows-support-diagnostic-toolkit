from pathlib import Path

from storage.risk import assess_removal_risk


def test_installer_in_application_data_is_review_only() -> None:
    result = assess_removal_risk(
        Path(r"C:\Users\fictional\AppData\Roaming\Zoom\ZoomDownload\Zoom.msi"),
        ("stale",),
    )

    assert result.level == "high"
    assert result.eligibility == "review_only"
    assert result.reason_code == "application_or_installer_file"


def test_possible_game_save_is_review_only() -> None:
    result = assess_removal_risk(
        Path(r"C:\Users\fictional\Saved Games\Example\slot1.sav"),
        ("stale",),
    )

    assert result.level == "high"
    assert result.eligibility == "review_only"


def test_old_partial_download_has_low_risk_but_still_requires_review() -> None:
    result = assess_removal_risk(
        Path(r"C:\Users\fictional\Downloads\archive.zip.part"),
        ("stale", "likely_incomplete"),
    )

    assert result.level == "low"
    assert result.eligibility == "eligible"


def test_ordinary_stale_file_remains_medium_risk() -> None:
    result = assess_removal_risk(
        Path(r"D:\Archives\important-notes.txt"),
        ("stale",),
    )

    assert result.level == "medium"
    assert result.eligibility == "eligible"
