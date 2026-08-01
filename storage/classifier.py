"""Deterministic metadata-only storage candidate classification."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PARTIAL_DOWNLOAD_EXTENSIONS = frozenset(
    {
        ".crdownload",
        ".download",
        ".opdownload",
        ".part",
        ".partial",
    }
)

TEMPORARY_EXTENSIONS = frozenset({".temp", ".tmp"})


@dataclass(frozen=True)
class ClassificationOptions:
    """Thresholds captured in every storage report."""

    stale_after_days: int = 730
    large_file_threshold_bytes: int = 1024 * 1024 * 1024
    incomplete_min_age_hours: int = 24
    temporary_min_age_hours: int = 168

    def __post_init__(self) -> None:
        values = {
            "stale_after_days": self.stale_after_days,
            "large_file_threshold_bytes": self.large_file_threshold_bytes,
            "incomplete_min_age_hours": self.incomplete_min_age_hours,
            "temporary_min_age_hours": self.temporary_min_age_hours,
        }
        for name, value in values.items():
            if value < 1:
                raise ValueError(f"{name} must be at least 1")


@dataclass(frozen=True)
class ClassificationResult:
    """Candidate attributes and their supporting evidence."""

    attributes: tuple[str, ...]
    evidence: tuple[dict[str, object], ...]
    confidence: str
    storage_category: str


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath(
            (os.path.normcase(str(path)), os.path.normcase(str(root)))
        ) == os.path.normcase(str(root))
    except ValueError:
        return False


def is_development_cache_path(
    path: Path,
    development_cache_roots: tuple[Path, ...],
) -> bool:
    """Return true only for a cache root explicitly supplied by the user."""

    return any(_is_path_within(path, root) for root in development_cache_roots)


def classify_file(
    *,
    path: Path,
    size_bytes: int,
    modified_at_utc: datetime,
    observed_at_utc: datetime,
    options: ClassificationOptions,
    development_cache_roots: tuple[Path, ...] = (),
) -> ClassificationResult:
    """Classify a regular file using metadata without opening its contents."""

    age_seconds = max(
        0.0,
        (observed_at_utc - modified_at_utc).total_seconds(),
    )
    extension = path.suffix.casefold()
    attributes: list[str] = []
    evidence: list[dict[str, object]] = []

    if age_seconds >= options.stale_after_days * 86400:
        attributes.append("stale")
        evidence.append(
            {
                "attribute": "stale",
                "code": "modified_before_stale_cutoff",
                "description": (
                    "The modification time is older than the configured "
                    "stale threshold."
                ),
                "observed_value": modified_at_utc.isoformat().replace(
                    "+00:00", "Z"
                ),
            }
        )

    if (
        extension in PARTIAL_DOWNLOAD_EXTENSIONS
        and age_seconds >= options.incomplete_min_age_hours * 3600
    ):
        attributes.append("likely_incomplete")
        evidence.append(
            {
                "attribute": "likely_incomplete",
                "code": "allowlisted_partial_extension",
                "description": (
                    "The allowlisted partial-download extension is old enough "
                    "for review; this does not prove corruption."
                ),
                "observed_value": extension,
            }
        )

    if size_bytes >= options.large_file_threshold_bytes:
        attributes.append("large")
        evidence.append(
            {
                "attribute": "large",
                "code": "size_at_or_above_threshold",
                "description": (
                    "The file is at or above the configured large-file "
                    "threshold."
                ),
                "observed_value": size_bytes,
            }
        )

    if size_bytes == 0:
        attributes.append("empty")
        evidence.append(
            {
                "attribute": "empty",
                "code": "zero_byte_regular_file",
                "description": "The regular file has a reported size of zero bytes.",
                "observed_value": 0,
            }
        )

    if (
        extension in TEMPORARY_EXTENSIONS
        and age_seconds >= options.temporary_min_age_hours * 3600
    ):
        attributes.append("temporary")
        evidence.append(
            {
                "attribute": "temporary",
                "code": "allowlisted_temporary_extension",
                "description": (
                    "The allowlisted temporary extension is older than the "
                    "configured threshold."
                ),
                "observed_value": extension,
            }
        )

    in_development_cache = is_development_cache_path(
        path, development_cache_roots
    )
    if in_development_cache:
        attributes.append("development_cache")
        evidence.append(
            {
                "attribute": "development_cache",
                "code": "user_approved_cache_root",
                "description": (
                    "The file is inside a development-cache root explicitly "
                    "included in this scan."
                ),
                "observed_value": str(
                    next(
                        root
                        for root in development_cache_roots
                        if _is_path_within(path, root)
                    )
                ),
            }
        )

    confidence = (
        "high"
        if {"large", "empty"}.intersection(attributes)
        else "medium"
    )
    storage_category = (
        "development_tools_and_caches"
        if in_development_cache
        else "user_content"
    )

    return ClassificationResult(
        attributes=tuple(attributes),
        evidence=tuple(evidence),
        confidence=confidence,
        storage_category=storage_category,
    )
