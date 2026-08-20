"""Single-pass folder aggregation and conservative folder candidates."""

from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath
from typing import Any

from .classifier import ClassificationOptions, PARTIAL_DOWNLOAD_EXTENSIONS
from .path_policy import is_path_within
from .risk import assess_folder_removal_risk

COLLAPSIBLE_ATTRIBUTES = frozenset(
    {"empty", "stale", "likely_incomplete", "temporary", "development_cache"}
)
TEMPORARY_FOLDER_NAMES = frozenset(
    {"cache", "caches", "temp", "temporary", "tmp"}
)


def metadata_tree_fingerprint(parts: list[str]) -> str:
    digest = hashlib.sha256()
    for part in sorted(parts):
        digest.update(part.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\n")
    return digest.hexdigest()


def file_fingerprint_part(
    name: str,
    logical_bytes: int,
    allocated_bytes: int | None,
    modified_ns: int,
) -> str:
    """Return a metadata-only fingerprint component for one regular file."""

    allocated = -1 if allocated_bytes is None else allocated_bytes
    return f"F\0{name}\0{logical_bytes}\0{allocated}\0{modified_ns}"


def directory_fingerprint_part(
    name: str, tree_fingerprint: str
) -> str:
    """Return a metadata-only fingerprint component for one child directory."""

    return f"D\0{name}\0{tree_fingerprint}"


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _depth(path: Path) -> int:
    return len(PureWindowsPath(str(path)).parts)


@dataclass
class FolderAggregate:
    """Metadata accumulated for one directory and all of its descendants."""

    path: Path
    scan_root: Path
    created_at_utc: datetime
    directory_modified_at_utc: datetime
    protected_category: str | None
    file_count: int = 0
    unique_file_count: int = 0
    directory_count: int = 0
    logical_bytes: int = 0
    allocated_bytes: int = 0
    newest_modified_at_utc: datetime | None = None
    oldest_modified_at_utc: datetime | None = None
    unavailable_descendants: int = 0
    contains_high_risk_items: bool = False
    attribute_file_counts: dict[str, int] = field(default_factory=dict)
    fingerprint_parts: list[str] = field(default_factory=list)
    tree_metadata_fingerprint: str | None = None

    def observe_file_entry(
        self,
        *,
        name: str,
        modified_at_utc: datetime | None,
        modified_ns: int | None,
        attributes: tuple[str, ...],
        logical_bytes: int = 0,
        allocated_bytes: int | None = None,
        high_risk: bool = False,
    ) -> None:
        self.file_count += 1
        self.logical_bytes += logical_bytes
        self.allocated_bytes += allocated_bytes or 0
        self.contains_high_risk_items = (
            self.contains_high_risk_items or high_risk
        )
        if modified_at_utc is None:
            self.unavailable_descendants += 1
        else:
            if (
                self.newest_modified_at_utc is None
                or modified_at_utc > self.newest_modified_at_utc
            ):
                self.newest_modified_at_utc = modified_at_utc
            if (
                self.oldest_modified_at_utc is None
                or modified_at_utc < self.oldest_modified_at_utc
            ):
                self.oldest_modified_at_utc = modified_at_utc
        if modified_ns is not None:
            self.fingerprint_parts.append(
                file_fingerprint_part(
                    name,
                    logical_bytes,
                    allocated_bytes,
                    modified_ns,
                )
            )
        for attribute in attributes:
            self.attribute_file_counts[attribute] = (
                self.attribute_file_counts.get(attribute, 0) + 1
            )

    def observe_unique_file(self) -> None:
        self.unique_file_count += 1

    def mark_unavailable(self) -> None:
        self.unavailable_descendants += 1

    def mark_high_risk(self) -> None:
        self.contains_high_risk_items = True

    def finalize_fingerprint(self) -> None:
        if self.tree_metadata_fingerprint is None:
            self.tree_metadata_fingerprint = metadata_tree_fingerprint(
                self.fingerprint_parts
            )

    def merge_child(self, child: FolderAggregate) -> None:
        child.finalize_fingerprint()
        self.file_count += child.file_count
        self.unique_file_count += child.unique_file_count
        self.directory_count += child.directory_count + 1
        self.logical_bytes += child.logical_bytes
        self.allocated_bytes += child.allocated_bytes
        self.unavailable_descendants += child.unavailable_descendants
        self.contains_high_risk_items = (
            self.contains_high_risk_items
            or child.contains_high_risk_items
            or child.protected_category is not None
        )
        self.fingerprint_parts.append(
            directory_fingerprint_part(
                child.path.name,
                child.tree_metadata_fingerprint or "",
            )
        )
        if child.newest_modified_at_utc is not None and (
            self.newest_modified_at_utc is None
            or child.newest_modified_at_utc > self.newest_modified_at_utc
        ):
            self.newest_modified_at_utc = child.newest_modified_at_utc
        if child.oldest_modified_at_utc is not None and (
            self.oldest_modified_at_utc is None
            or child.oldest_modified_at_utc < self.oldest_modified_at_utc
        ):
            self.oldest_modified_at_utc = child.oldest_modified_at_utc
        for attribute, count in child.attribute_file_counts.items():
            self.attribute_file_counts[attribute] = (
                self.attribute_file_counts.get(attribute, 0) + count
            )


def fold_folder_aggregates(
    aggregates: dict[Path, FolderAggregate],
) -> None:
    """Fold each child into its parent once, producing subtree totals."""

    for path in sorted(aggregates, key=_depth, reverse=True):
        aggregates[path].finalize_fingerprint()
        parent = path.parent
        if parent in aggregates:
            aggregates[parent].merge_child(aggregates[path])


def _folder_name_is_incomplete(path: Path) -> bool:
    name = path.name.casefold()
    return any(name.endswith(suffix) for suffix in PARTIAL_DOWNLOAD_EXTENSIONS)


def _folder_attributes(
    aggregate: FolderAggregate,
    *,
    observed_at_utc: datetime,
    options: ClassificationOptions,
    development_cache_roots: tuple[Path, ...],
) -> tuple[list[str], list[dict[str, Any]]]:
    attributes: list[str] = []
    evidence: list[dict[str, Any]] = []
    newest = aggregate.newest_modified_at_utc
    effective_modified = newest or aggregate.directory_modified_at_utc
    age = max(timedelta(), observed_at_utc - effective_modified)
    complete_metadata = aggregate.unavailable_descendants == 0

    zero_byte_count = aggregate.attribute_file_counts.get("empty", 0)
    empty_tree = aggregate.file_count == 0 or (
        aggregate.file_count > 0 and zero_byte_count == aggregate.file_count
    )
    if empty_tree and complete_metadata:
        attributes.append("empty")
        evidence.append(
            {
                "attribute": "empty",
                "code": "empty_directory_tree",
                "description": (
                    "No non-empty regular files were observed anywhere in this directory tree."
                ),
                "observed_value": aggregate.file_count,
            }
        )

    if (
        aggregate.file_count > 0
        and complete_metadata
        and age >= timedelta(days=options.stale_after_days)
    ):
        attributes.append("stale")
        evidence.append(
            {
                "attribute": "stale",
                "code": "newest_descendant_before_stale_cutoff",
                "description": (
                    "Every observed file in this directory tree is older than "
                    "the configured stale threshold."
                ),
                "observed_value": _utc_text(effective_modified),
            }
        )

    incomplete_count = aggregate.attribute_file_counts.get(
        "likely_incomplete", 0
    )
    old_enough_for_incomplete = age >= timedelta(
        hours=options.incomplete_min_age_hours
    )
    if complete_metadata and old_enough_for_incomplete and (
        _folder_name_is_incomplete(aggregate.path)
        or (
            aggregate.file_count > 0
            and incomplete_count == aggregate.file_count
        )
    ):
        attributes.append("likely_incomplete")
        evidence.append(
            {
                "attribute": "likely_incomplete",
                "code": "incomplete_directory_tree",
                "description": (
                    "The folder name resembles an incomplete download or every "
                    "observed file carries incomplete-download evidence."
                ),
                "observed_value": incomplete_count,
            }
        )

    candidate_size = aggregate.allocated_bytes or aggregate.logical_bytes
    if candidate_size >= options.large_file_threshold_bytes:
        attributes.append("large")
        evidence.append(
            {
                "attribute": "large",
                "code": "folder_size_at_or_above_threshold",
                "description": (
                    "The aggregated directory tree is at or above the configured "
                    "large-item threshold."
                ),
                "observed_value": candidate_size,
            }
        )

    name_is_temporary = aggregate.path.name.casefold() in TEMPORARY_FOLDER_NAMES
    temporary_count = aggregate.attribute_file_counts.get("temporary", 0)
    old_enough_for_temporary = age >= timedelta(
        hours=options.temporary_min_age_hours
    )
    if complete_metadata and old_enough_for_temporary and (
        name_is_temporary
        or (
            aggregate.file_count > 0
            and temporary_count == aggregate.file_count
        )
    ):
        attributes.append("temporary")
        evidence.append(
            {
                "attribute": "temporary",
                "code": "temporary_directory_tree",
                "description": (
                    "The directory name or all observed files match the "
                    "temporary-data rules and are old enough for review."
                ),
                "observed_value": temporary_count,
            }
        )

    if any(is_path_within(aggregate.path, root) for root in development_cache_roots):
        attributes.append("development_cache")
        evidence.append(
            {
                "attribute": "development_cache",
                "code": "user_approved_cache_root",
                "description": (
                    "The directory is inside a development-cache root explicitly "
                    "approved for this scan."
                ),
                "observed_value": str(
                    next(
                        root
                        for root in development_cache_roots
                        if is_path_within(aggregate.path, root)
                    )
                ),
            }
        )

    if aggregate.unavailable_descendants:
        attributes.append("unavailable")
        evidence.append(
            {
                "attribute": "unavailable",
                "code": "incomplete_folder_metadata",
                "description": (
                    "At least one descendant could not be inspected, so this "
                    "folder cannot be selected for cleanup."
                ),
                "observed_value": aggregate.unavailable_descendants,
            }
        )
    return attributes, evidence


def _confidence(attributes: set[str]) -> str:
    if "unavailable" in attributes:
        return "low"
    if attributes.intersection({"empty", "large"}):
        return "high"
    return "medium"


def _storage_category(
    path: Path, development_cache_roots: tuple[Path, ...]
) -> str:
    if any(is_path_within(path, root) for root in development_cache_roots):
        return "development_tools_and_caches"
    return "user_content"


def build_folder_candidates(
    aggregates: dict[Path, FolderAggregate],
    *,
    observed_at_utc: datetime,
    options: ClassificationOptions,
    development_cache_roots: tuple[Path, ...],
) -> list[dict[str, Any]]:
    """Build collapsed folder candidates from already-observed metadata."""

    fold_folder_aggregates(aggregates)
    raw: list[dict[str, Any]] = []
    roots = {_normalized(aggregate.scan_root) for aggregate in aggregates.values()}

    for aggregate in sorted(aggregates.values(), key=lambda item: _normalized(item.path)):
        if aggregate.protected_category is not None:
            continue
        if _normalized(aggregate.path) in roots:
            continue
        attributes, evidence = _folder_attributes(
            aggregate,
            observed_at_utc=observed_at_utc,
            options=options,
            development_cache_roots=development_cache_roots,
        )
        if not attributes:
            continue
        attribute_set = set(attributes)
        if "unavailable" in attribute_set:
            risk_level = "protected"
            eligibility = "unavailable"
            reason_code = "incomplete_folder_metadata"
            explanation = (
                "Incomplete descendant metadata prevents folder cleanup."
            )
        elif aggregate.contains_high_risk_items:
            risk_level = "high"
            eligibility = "review_only"
            reason_code = "high_risk_descendant"
            explanation = (
                "At least one descendant resembles application, configuration, "
                "installer, runtime, or save data, so the complete tree remains "
                "review-only."
            )
        else:
            risk = assess_folder_removal_risk(aggregate.path, tuple(attributes))
            risk_level = risk.level
            eligibility = risk.eligibility
            reason_code = risk.reason_code
            explanation = risk.explanation

        modified = (
            aggregate.newest_modified_at_utc
            or aggregate.directory_modified_at_utc
        )
        raw.append(
            {
                "item_type": "folder",
                "path": os.path.abspath(str(aggregate.path)),
                "scan_root": str(aggregate.scan_root),
                "name": aggregate.path.name or str(aggregate.path),
                "extension": None,
                "size_bytes": aggregate.logical_bytes,
                "allocated_size_bytes": aggregate.allocated_bytes,
                "created_at_utc": _utc_text(aggregate.created_at_utc),
                "modified_at_utc": _utc_text(modified),
                "last_accessed_at_utc": None,
                "last_access_reliability": "unavailable",
                "storage_category": _storage_category(
                    aggregate.path, development_cache_roots
                ),
                "attributes": attributes,
                "evidence": evidence,
                "confidence": _confidence(attribute_set),
                "removal_risk": risk_level,
                "protection": {
                    "eligibility": eligibility,
                    "reason_code": reason_code,
                    "explanation": explanation,
                },
                "is_regular_file": False,
                "is_directory": True,
                "is_reparse_point": False,
                "file_count": aggregate.file_count,
                "directory_count": aggregate.directory_count,
                "newest_descendant_modified_at_utc": (
                    _utc_text(aggregate.newest_modified_at_utc)
                    if aggregate.newest_modified_at_utc is not None
                    else None
                ),
                "oldest_descendant_modified_at_utc": (
                    _utc_text(aggregate.oldest_modified_at_utc)
                    if aggregate.oldest_modified_at_utc is not None
                    else None
                ),
                "contains_unavailable_items": bool(
                    aggregate.unavailable_descendants
                ),
                "contains_high_risk_items": (
                    aggregate.contains_high_risk_items
                ),
                "tree_metadata_fingerprint": (
                    aggregate.tree_metadata_fingerprint
                ),
            }
        )

    kept: list[dict[str, Any]] = []
    for candidate in sorted(raw, key=lambda item: (_depth(Path(item["path"])), item["path"].casefold())):
        candidate_actions = set(candidate["attributes"]).intersection(
            COLLAPSIBLE_ATTRIBUTES
        )
        redundant = False
        if candidate_actions:
            for ancestor in kept:
                ancestor_actions = set(ancestor["attributes"]).intersection(
                    COLLAPSIBLE_ATTRIBUTES
                )
                if (
                    candidate_actions.issubset(ancestor_actions)
                    and is_path_within(Path(candidate["path"]), Path(ancestor["path"]))
                    and _normalized(Path(candidate["path"]))
                    != _normalized(Path(ancestor["path"]))
                ):
                    redundant = True
                    break
        if not redundant:
            kept.append(candidate)
    return sorted(kept, key=lambda item: item["path"].casefold())
