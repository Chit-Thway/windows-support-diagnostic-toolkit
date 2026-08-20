"""Conservative removal-risk policy for metadata-only candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

APPLICATION_OR_INSTALLER_EXTENSIONS = frozenset(
    {".appx", ".config", ".db", ".dll", ".exe", ".msi", ".msix", ".msp", ".sqlite", ".sqlite3", ".sys"}
)
SAVE_FILE_EXTENSIONS = frozenset({".ess", ".sav", ".save"})
SAVE_DIRECTORY_NAMES = frozenset(
    {"my games", "save", "saved games", "savegame", "savegames", "saves"}
)


@dataclass(frozen=True)
class RemovalRisk:
    level: str
    eligibility: str
    reason_code: str | None
    explanation: str


def _parts(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in PureWindowsPath(str(path)).parts)


def assess_removal_risk(path: Path, attributes: tuple[str, ...]) -> RemovalRisk:
    """Separate cleanup safety from candidate evidence and confidence."""

    parts = _parts(path)
    extension = path.suffix.casefold()
    attribute_set = set(attributes)
    explicit_cache = "development_cache" in attribute_set
    in_app_data = "appdata" in parts
    in_local_temp = (
        "appdata" in parts and "local" in parts and "temp" in parts
    )
    resembles_save_data = (
        extension in SAVE_FILE_EXTENSIONS
        or bool(set(parts).intersection(SAVE_DIRECTORY_NAMES))
    )

    if extension in APPLICATION_OR_INSTALLER_EXTENSIONS:
        return RemovalRisk(
            level="high",
            eligibility="review_only",
            reason_code="application_or_installer_file",
            explanation=(
                "Application, installer, database, configuration, or system-style "
                "files require manual review and cannot be recycled by the toolkit."
            ),
        )
    if resembles_save_data:
        return RemovalRisk(
            level="high",
            eligibility="review_only",
            reason_code="possible_save_data",
            explanation=(
                "The path or extension resembles saved application or game data, "
                "so the toolkit keeps it review-only."
            ),
        )
    if in_app_data and not in_local_temp and not explicit_cache:
        return RemovalRisk(
            level="high",
            eligibility="review_only",
            reason_code="application_managed_data",
            explanation=(
                "Application-managed AppData may contain settings, profiles, or "
                "working data and cannot be recycled by the toolkit."
            ),
        )
    if (
        "likely_incomplete" in attribute_set
        or ("temporary" in attribute_set and in_local_temp)
    ):
        return RemovalRisk(
            level="low",
            eligibility="eligible",
            reason_code=None,
            explanation=(
                "The regular file is outside protected locations and has stronger "
                "temporary or incomplete-download evidence. Review its exact path."
            ),
        )
    return RemovalRisk(
        level="medium",
        eligibility="eligible",
        reason_code=None,
        explanation=(
            "The regular file is technically selectable, but its metadata does not "
            "prove that recycling it is safe. Review its exact path and purpose."
        ),
    )
