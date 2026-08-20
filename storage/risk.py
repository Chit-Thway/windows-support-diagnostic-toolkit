"""Conservative removal-risk policy for metadata-only candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

APPLICATION_OR_INSTALLER_EXTENSIONS = frozenset(
    {
        ".appx",
        ".config",
        ".db",
        ".dll",
        ".exe",
        ".msi",
        ".msix",
        ".msp",
        ".sqlite",
        ".sqlite3",
        ".sys",
    }
)
SAVE_FILE_EXTENSIONS = frozenset({".ess", ".sav", ".save"})
SAVE_DIRECTORY_NAMES = frozenset(
    {"my games", "save", "saved games", "savegame", "savegames", "saves"}
)
APPLICATION_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "bin",
        "config",
        "configuration",
        "data",
        "database",
        "databases",
        "node_modules",
        "plugins",
        "runtime",
        "runtimes",
        "venv",
    }
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


def assess_folder_removal_risk(
    path: Path, attributes: tuple[str, ...]
) -> RemovalRisk:
    """Apply stricter policy to recursive directory cleanup candidates."""

    parts = _parts(path)
    attribute_set = set(attributes)
    explicit_cache = "development_cache" in attribute_set
    in_app_data = "appdata" in parts
    in_local_temp = (
        "appdata" in parts and "local" in parts and "temp" in parts
    )
    resembles_save_data = bool(set(parts).intersection(SAVE_DIRECTORY_NAMES))
    resembles_application_tree = bool(
        set(parts).intersection(APPLICATION_DIRECTORY_NAMES)
    )

    if resembles_save_data:
        return RemovalRisk(
            level="high",
            eligibility="review_only",
            reason_code="possible_save_data_tree",
            explanation=(
                "This directory resembles saved application or game data. It "
                "can be inspected but not recycled by the toolkit."
            ),
        )
    if in_app_data and not in_local_temp and not explicit_cache:
        return RemovalRisk(
            level="high",
            eligibility="review_only",
            reason_code="application_managed_directory",
            explanation=(
                "Application-managed AppData may contain working data, settings, "
                "or profiles, so the directory remains review-only."
            ),
        )
    if resembles_application_tree and "development_cache" not in attribute_set:
        return RemovalRisk(
            level="high",
            eligibility="review_only",
            reason_code="possible_application_directory",
            explanation=(
                "The directory name resembles application, configuration, "
                "runtime, database, or project infrastructure."
            ),
        )
    if "large" in attribute_set and not attribute_set.intersection(
        {"empty", "stale", "likely_incomplete", "temporary", "development_cache"}
    ):
        return RemovalRisk(
            level="high",
            eligibility="review_only",
            reason_code="size_only_folder_evidence",
            explanation=(
                "Size alone is useful evidence for investigation but is not "
                "enough to make an entire directory selectable."
            ),
        )
    if attribute_set.intersection({"empty", "likely_incomplete", "temporary"}):
        return RemovalRisk(
            level="low",
            eligibility="eligible",
            reason_code=None,
            explanation=(
                "The directory has stronger empty, temporary, or incomplete-tree "
                "evidence and is outside protected locations. Review the whole "
                "tree before recycling it."
            ),
        )
    return RemovalRisk(
        level="medium",
        eligibility="eligible",
        reason_code=None,
        explanation=(
            "The directory is technically selectable, but age or cache metadata "
            "does not prove it is disposable. Review every consequence before "
            "recycling the complete tree."
        ),
    )
