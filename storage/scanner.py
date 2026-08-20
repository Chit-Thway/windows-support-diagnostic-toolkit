"""Read-only, metadata-only scanner for user-approved Windows folders."""

from __future__ import annotations

import ctypes
import heapq
import os
import shutil
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .classifier import (
    ClassificationOptions,
    classify_file,
    is_development_cache_path,
)
from .development import DevelopmentInsightsInspector
from .path_policy import (
    ProtectedPathPolicy,
    UnsafeScanRootError,
    is_reparse_point,
)

ATTRIBUTE_NAMES = (
    "stale",
    "likely_incomplete",
    "large",
    "empty",
    "temporary",
    "development_cache",
    "protected",
    "unavailable",
)
from .risk import assess_removal_risk
from .windows_metadata import get_allocated_size, get_file_identity

class ScanConfigurationError(ValueError):
    """Raised when scanner inputs cannot produce a safe per-drive scan."""


@dataclass(frozen=True)
class ScannerOptions:
    """Bounded scanner settings captured in the output report."""

    classification: ClassificationOptions = field(
        default_factory=ClassificationOptions
    )
    maximum_candidates_retained: int = 5000
    maximum_issue_records: int = 1000
    development_cache_roots: tuple[str | Path, ...] = ()
    discover_development_insights: bool = True
    progress_every_files: int = 100

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_candidates_retained <= 100000:
            raise ValueError(
                "maximum_candidates_retained must be between 1 and 100000"
            )
        if not 1 <= self.maximum_issue_records <= 10000:
            raise ValueError(
                "maximum_issue_records must be between 1 and 10000"
            )
        if self.progress_every_files < 1:
            raise ValueError("progress_every_files must be at least 1")


@dataclass(frozen=True)
class ProgressUpdate:
    """A truthful progress observation without a false completion percentage."""

    current_path: str
    files_examined: int
    directories_examined: int
    bytes_examined: int
    candidates_found: int


@dataclass
class _ScanState:
    started_at: datetime
    maximum_candidates_retained: int
    maximum_issue_records: int
    files_examined: int = 0
    directories_examined: int = 0
    bytes_examined: int = 0
    allocated_bytes_examined: int = 0
    candidate_counter: int = 0
    total_candidate_bytes: int = 0
    total_candidate_allocated_bytes: int = 0
    candidate_heap: list[
        tuple[tuple[int, ...], int, dict[str, Any]]
    ] = field(default_factory=list)
    inaccessible_paths: list[dict[str, Any]] = field(default_factory=list)
    scan_errors: list[dict[str, Any]] = field(default_factory=list)
    inaccessible_path_counter: int = 0
    scan_error_counter: int = 0
    seen_file_identities: set[tuple[object, ...]] = field(default_factory=set)
    category_allocated_bytes: dict[str, int] = field(
        default_factory=lambda: {
            "protected_system": 0,
            "installed_applications": 0,
            "user_content": 0,
            "development_tools_and_caches": 0,
        }
    )
    cancelled: bool = False
    attribute_summaries: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            name: {"candidate_count": 0, "unique_bytes": 0}
            for name in ATTRIBUTE_NAMES
        }
    )

    @property
    def omitted_candidates(self) -> int:
        return self.candidate_counter - len(self.candidate_heap)

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return sorted(
            (entry[2] for entry in self.candidate_heap),
            key=lambda candidate: candidate["candidate_id"],
        )

    @property
    def omitted_inaccessible_paths(self) -> int:
        return self.inaccessible_path_counter - len(self.inaccessible_paths)

    @property
    def omitted_scan_errors(self) -> int:
        return self.scan_error_counter - len(self.scan_errors)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_from_epoch(value: float) -> datetime:
    return datetime.fromtimestamp(value, timezone.utc)


def _absolute_path_text(path: Path) -> str:
    """Return an absolute display path without resolving links or junctions."""

    return os.path.abspath(str(path))


def _volume_information(drive_letter: str) -> tuple[str | None, str | None]:
    """Read optional Windows volume metadata without changing the drive."""

    if os.name != "nt":
        return None, None
    volume_name = ctypes.create_unicode_buffer(261)
    filesystem_name = ctypes.create_unicode_buffer(261)
    root = f"{drive_letter}\\"
    try:
        success = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root),
            volume_name,
            len(volume_name),
            None,
            None,
            None,
            filesystem_name,
            len(filesystem_name),
        )
    except (AttributeError, OSError):
        return None, None
    if not success:
        return None, None
    return volume_name.value or None, filesystem_name.value or None


class StorageScanner:
    """Scan selected roots using file metadata and no content reads."""

    def __init__(
        self,
        *,
        path_policy: ProtectedPathPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        disk_usage: Callable[[str | os.PathLike[str]], Any] | None = None,
        volume_information: (
            Callable[[str], tuple[str | None, str | None]] | None
        ) = None,
        development_inspector_factory: (
            Callable[..., DevelopmentInsightsInspector] | None
        ) = None,
        allocated_size: Callable[[Path, int], int] | None = None,
        file_identity: Callable[[Path], tuple[object, ...]] | None = None,
    ) -> None:
        self._path_policy = path_policy or ProtectedPathPolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._disk_usage = disk_usage or shutil.disk_usage
        self._volume_information = volume_information or _volume_information
        self._development_inspector_factory = (
            development_inspector_factory or DevelopmentInsightsInspector
        )
        self._allocated_size = allocated_size or get_allocated_size
        self._file_identity_reader = file_identity or get_file_identity

    def scan(
        self,
        roots: Iterable[str | Path],
        *,
        options: ScannerOptions | None = None,
        cancel_check: Callable[[], bool] | None = None,
        progress_callback: Callable[[ProgressUpdate], None] | None = None,
    ) -> dict[str, Any]:
        """Return a contract-compatible report without modifying scanned files."""

        if os.name != "nt":
            raise ScanConfigurationError(
                "The storage scanner currently supports Windows only."
            )
        options = options or ScannerOptions()
        cancel_check = cancel_check or (lambda: False)

        try:
            scan_roots = self._path_policy.validate_roots(roots)
            development_cache_roots = (
                self._path_policy.validate_development_cache_roots(
                    options.development_cache_roots,
                    scan_roots,
                )
            )
        except UnsafeScanRootError as error:
            raise ScanConfigurationError(str(error)) from error

        drive_letter = scan_roots[0].drive.upper()
        try:
            capacity = self._disk_usage(scan_roots[0])
        except OSError as error:
            raise ScanConfigurationError(
                f"Drive capacity could not be read for {drive_letter}."
            ) from error

        if capacity.total <= 0:
            raise ScanConfigurationError(
                f"Drive capacity is unavailable for {drive_letter}."
            )

        started_at = self._clock().astimezone(timezone.utc)
        development_inspector = self._development_inspector_factory(
            scan_roots=scan_roots,
            enabled=options.discover_development_insights,
        )
        state = _ScanState(
            started_at=started_at,
            maximum_candidates_retained=options.maximum_candidates_retained,
            maximum_issue_records=options.maximum_issue_records,
        )
        root_reports: list[dict[str, Any]] = []

        for root_index, root in enumerate(scan_roots):
            if state.cancelled or cancel_check():
                state.cancelled = True
                root_reports.extend(
                    self._skipped_root_report(path)
                    for path in scan_roots[root_index:]
                )
                break

            root_report = self._scan_root(
                root=root,
                drive_letter=drive_letter,
                state=state,
                options=options,
                development_cache_roots=development_cache_roots,
                development_inspector=development_inspector,
                cancel_check=cancel_check,
                progress_callback=progress_callback,
            )
            root_reports.append(root_report)
            if state.cancelled:
                root_reports.extend(
                    self._skipped_root_report(path)
                    for path in scan_roots[root_index + 1 :]
                )
                break

        if state.cancelled:
            self._record_scan_error(
                state,
                code="scan_cancelled",
                scope="report",
                path=None,
                message=(
                    "The user cancelled the scan; collected metadata was "
                    "retained as a partial result."
                ),
                recoverable=True,
            )

        completed_at = self._clock().astimezone(timezone.utc)
        status = self._scan_status(state)
        accounting = self._build_accounting(
            state=state,
            used_bytes=int(capacity.used),
            free_bytes=int(capacity.free),
            total_bytes=int(capacity.total),
            status=status,
        )
        status = self._scan_status(state)
        volume_label, filesystem = self._volume_information(drive_letter)

        report = {
            "schema_version": "1.0.0",
            "report_type": "storage_analysis",
            "generated_at_utc": _utc_text(completed_at),
            "scanner": {
                "name": "Storage Insights Scanner",
                "version": __version__,
                "platform": "Windows",
                "mode": "metadata_only",
            },
            "scan": {
                "started_at_utc": _utc_text(started_at),
                "completed_at_utc": _utc_text(completed_at),
                "duration_ms": max(
                    0,
                    round((completed_at - started_at).total_seconds() * 1000),
                ),
                "status": status,
                "detail_coverage": self._detail_coverage(state, status),
                "aggregate_coverage": (
                    "partial" if status != "complete" else "estimated"
                ),
                "files_examined": state.files_examined,
                "directories_examined": state.directories_examined,
                "bytes_examined": state.bytes_examined,
                "allocated_bytes_examined": state.allocated_bytes_examined,
                "candidate_details_retained": len(state.candidates),
                "candidate_details_omitted": state.omitted_candidates,
                "inaccessible_path_details_retained": len(
                    state.inaccessible_paths
                ),
                "inaccessible_path_details_omitted": (
                    state.omitted_inaccessible_paths
                ),
                "scan_error_details_retained": len(state.scan_errors),
                "scan_error_details_omitted": state.omitted_scan_errors,
            },
            "drive": {
                "drive_letter": drive_letter,
                "volume_label": volume_label,
                "filesystem": filesystem,
                "total_bytes": int(capacity.total),
                "used_bytes": int(capacity.used),
                "free_bytes": int(capacity.free),
                "percent_free": round(
                    int(capacity.free) / int(capacity.total) * 100,
                    2,
                ),
                "observed_at_utc": _utc_text(started_at),
            },
            "scan_scope": {
                "roots": root_reports,
                "options": {
                    "stale_after_days": (
                        options.classification.stale_after_days
                    ),
                    "large_file_threshold_bytes": (
                        options.classification.large_file_threshold_bytes
                    ),
                    "incomplete_min_age_hours": (
                        options.classification.incomplete_min_age_hours
                    ),
                    "temporary_min_age_hours": (
                        options.classification.temporary_min_age_hours
                    ),
                    "maximum_candidates_retained": (
                        options.maximum_candidates_retained
                    ),
                    "maximum_issue_records": options.maximum_issue_records,
                    "use_last_access_as_classification_evidence": False,
                    "discover_development_insights": (
                        options.discover_development_insights
                    ),
                },
            },
            "accounting": accounting,
            "candidate_summary": {
                "accounting_method": "unique_candidate_id",
                "total_unique_candidates": state.candidate_counter,
                "total_unique_candidate_bytes": state.total_candidate_bytes,
                "retained_candidates": len(state.candidates),
                "retained_unique_candidate_bytes": sum(
                    candidate["size_bytes"] or 0
                    for candidate in state.candidates
                ),
                "total_unique_candidate_allocated_bytes": (
                    state.total_candidate_allocated_bytes
                ),
                "retained_unique_candidate_allocated_bytes": sum(
                    candidate.get("allocated_size_bytes") or 0
                    for candidate in state.candidates
                ),
                "omitted_candidates": state.omitted_candidates,
                "attributes": state.attribute_summaries,
            },
            "candidates": state.candidates,
            "inaccessible_paths": state.inaccessible_paths,
            "scan_errors": state.scan_errors,
            "development_insights": development_inspector.build_report(
                scan_status=status
            ),
            "limitations": self._limitations(
                state,
                development_cache_roots,
                whole_drive_scan=any(
                    root == Path(f"{root.drive}\\") for root in scan_roots
                ),
            ),
        }

        if progress_callback is not None:
            progress_callback(
                self._progress_update(state, str(scan_roots[-1]))
            )
        return report

    def _scan_root(
        self,
        *,
        root: Path,
        drive_letter: str,
        state: _ScanState,
        options: ScannerOptions,
        development_cache_roots: tuple[Path, ...],
        development_inspector: DevelopmentInsightsInspector,
        cancel_check: Callable[[], bool],
        progress_callback: Callable[[ProgressUpdate], None] | None,
    ) -> dict[str, Any]:
        root_files = 0
        root_directories = 0
        root_bytes = 0
        root_allocated_bytes = 0
        root_errors_before = state.scan_error_counter
        stack = [
            (
                root,
                self._path_policy.protected_category(root, drive_letter),
            )
        ]

        while stack and not state.cancelled:
            if cancel_check():
                state.cancelled = True
                break
            directory, directory_category = stack.pop()
            try:
                with os.scandir(directory) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name.casefold())
                state.directories_examined += 1
                root_directories += 1
            except OSError as error:
                self._record_os_error(
                    state=state,
                    path=directory,
                    scan_root=root,
                    error=error,
                    scope="directory",
                )
                continue

            if directory_category is None:
                development_inspector.observe_directory(directory, entries)
            for entry in entries:
                if cancel_check():
                    state.cancelled = True
                    break
                path = Path(entry.path)
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError as error:
                    self._record_os_error(
                        state=state,
                        path=path,
                        scan_root=root,
                        error=error,
                        scope="file",
                        retain_unavailable_candidate=True,
                    )
                    continue

                if is_reparse_point(path, metadata):
                    self._record_reparse_point(state, path, root)
                    continue
                protected_category = (
                    directory_category
                    or self._path_policy.protected_category(path, drive_letter)
                )
                if stat.S_ISDIR(metadata.st_mode):
                    stack.append((path, protected_category))
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    continue

                identity = self._file_identity(path, metadata)
                if identity in state.seen_file_identities:
                    continue
                state.seen_file_identities.add(identity)

                size_bytes = max(0, int(metadata.st_size))
                try:
                    allocated_size_bytes: int | None = self._allocated_size(
                        path,
                        size_bytes,
                    )
                except OSError:
                    allocated_size_bytes = None
                    self._record_path_issue(
                        state=state,
                        path=path,
                        scan_root=root,
                        error_type="unreadable_metadata",
                        code="allocated_size_unavailable",
                        scope="file",
                        message=(
                            "Windows could not report the file's allocated size; "
                            "the remaining scan continued."
                        ),
                    )
                state.files_examined += 1
                state.bytes_examined += size_bytes
                state.allocated_bytes_examined += allocated_size_bytes or 0
                root_files += 1
                root_bytes += size_bytes
                root_allocated_bytes += allocated_size_bytes or 0

                if protected_category is not None:
                    state.category_allocated_bytes[protected_category] += (
                        allocated_size_bytes or 0
                    )
                    if (
                        progress_callback is not None
                        and state.files_examined
                        % options.progress_every_files
                        == 0
                    ):
                        progress_callback(
                            self._progress_update(state, str(path))
                        )
                    continue

                in_cache = is_development_cache_path(
                    path, development_cache_roots
                )
                in_discovered_development_location = (
                    development_inspector.observe_file(path, size_bytes)
                )
                if in_cache or in_discovered_development_location:
                    storage_category = "development_tools_and_caches"
                else:
                    storage_category = "user_content"
                state.category_allocated_bytes[storage_category] += (
                    allocated_size_bytes or 0
                )

                if in_discovered_development_location:
                    if (
                        progress_callback is not None
                        and state.files_examined % options.progress_every_files == 0
                    ):
                        progress_callback(
                            self._progress_update(state, str(path))
                        )
                    continue

                try:
                    created_at = _timestamp_from_epoch(metadata.st_ctime)
                    modified_at = _timestamp_from_epoch(metadata.st_mtime)
                    accessed_at = _timestamp_from_epoch(metadata.st_atime)
                except (OSError, OverflowError, ValueError) as error:
                    self._record_os_error(
                        state=state,
                        path=path,
                        scan_root=root,
                        error=error,
                        scope="file",
                        retain_unavailable_candidate=True,
                    )
                    continue

                result = classify_file(
                    path=path,
                    size_bytes=size_bytes,
                    modified_at_utc=modified_at,
                    observed_at_utc=state.started_at,
                    options=options.classification,
                    development_cache_roots=development_cache_roots,
                )
                if result.attributes:
                    removal_risk = assess_removal_risk(
                        path,
                        result.attributes,
                    )
                    candidate = {
                        "candidate_id": self._next_candidate_id(state),
                        "path": _absolute_path_text(path),
                        "scan_root": str(root),
                        "name": path.name,
                        "extension": path.suffix.casefold() or None,
                        "size_bytes": size_bytes,
                        "allocated_size_bytes": allocated_size_bytes,
                        "created_at_utc": _utc_text(created_at),
                        "modified_at_utc": _utc_text(modified_at),
                        "last_accessed_at_utc": _utc_text(accessed_at),
                        "last_access_reliability": "limited",
                        "storage_category": result.storage_category,
                        "attributes": list(result.attributes),
                        "evidence": list(result.evidence),
                        "confidence": result.confidence,
                        "removal_risk": removal_risk.level,
                        "protection": {
                            "eligibility": removal_risk.eligibility,
                            "reason_code": removal_risk.reason_code,
                            "explanation": removal_risk.explanation,
                        },
                        "is_regular_file": True,
                        "is_reparse_point": False,
                    }
                    self._record_candidate(state, candidate)

                if (
                    progress_callback is not None
                    and state.files_examined % options.progress_every_files == 0
                ):
                    progress_callback(self._progress_update(state, str(path)))

        root_error_count = state.scan_error_counter - root_errors_before
        status = (
            "cancelled"
            if state.cancelled
            else "partial" if root_error_count else "complete"
        )
        return {
            "requested_path": str(root),
            "canonical_path": str(root),
            "include_subdirectories": True,
            "status": status,
            "files_examined": root_files,
            "directories_examined": root_directories,
            "bytes_examined": root_bytes,
            "allocated_bytes_examined": root_allocated_bytes,
            "errors_count": root_error_count,
        }

    @staticmethod
    def _skipped_root_report(root: Path) -> dict[str, Any]:
        return {
            "requested_path": str(root),
            "canonical_path": str(root),
            "include_subdirectories": True,
            "status": "skipped",
            "files_examined": 0,
            "directories_examined": 0,
            "bytes_examined": 0,
            "allocated_bytes_examined": 0,
            "errors_count": 0,
        }

    def _file_identity(
        self,
        path: Path,
        metadata: os.stat_result,
    ) -> tuple[object, ...]:
        try:
            return self._file_identity_reader(path)
        except OSError:
            pass
        inode = getattr(metadata, "st_ino", 0)
        device = getattr(metadata, "st_dev", 0)
        if inode:
            return ("inode", device, inode)
        return ("path", os.path.normcase(_absolute_path_text(path)))

    @staticmethod
    def _next_candidate_id(state: _ScanState) -> str:
        return f"candidate-{state.candidate_counter + 1:06d}"

    @staticmethod
    def _record_candidate(
        state: _ScanState,
        candidate: dict[str, Any],
    ) -> None:
        state.candidate_counter += 1
        size_bytes = candidate["size_bytes"] or 0
        allocated_size_bytes = candidate.get("allocated_size_bytes") or 0
        state.total_candidate_bytes += size_bytes
        state.total_candidate_allocated_bytes += allocated_size_bytes
        for attribute in candidate["attributes"]:
            state.attribute_summaries[attribute]["candidate_count"] += 1
            state.attribute_summaries[attribute]["unique_bytes"] += size_bytes
        priority = StorageScanner._candidate_priority(
            candidate,
            state.candidate_counter,
        )
        entry = (priority, state.candidate_counter, candidate)
        if len(state.candidate_heap) < state.maximum_candidates_retained:
            heapq.heappush(state.candidate_heap, entry)
        elif priority > state.candidate_heap[0][0]:
            heapq.heapreplace(state.candidate_heap, entry)

    @staticmethod
    def _candidate_priority(
        candidate: dict[str, Any],
        ordinal: int,
    ) -> tuple[int, ...]:
        attribute_weights = {
            "likely_incomplete": 6,
            "temporary": 5,
            "empty": 4,
            "development_cache": 3,
            "stale": 2,
            "large": 1,
            "protected": 0,
            "unavailable": 0,
        }
        risk_weights = {"low": 3, "medium": 2, "high": 1, "protected": 0}
        confidence_weights = {"high": 3, "medium": 2, "low": 1}
        eligibility = candidate["protection"]["eligibility"]
        return (
            1 if eligibility == "eligible" else 0,
            risk_weights.get(candidate.get("removal_risk", "protected"), 0),
            sum(
                attribute_weights.get(attribute, 0)
                for attribute in candidate["attributes"]
            ),
            confidence_weights.get(candidate["confidence"], 0),
            candidate.get("allocated_size_bytes")
            or candidate.get("size_bytes")
            or 0,
            -ordinal,
        )

    def _record_unavailable_candidate(
        self,
        state: _ScanState,
        path: Path,
        scan_root: Path,
        code: str,
        message: str,
    ) -> None:
        candidate = {
            "candidate_id": self._next_candidate_id(state),
            "path": _absolute_path_text(path),
            "scan_root": str(scan_root),
            "name": path.name or str(path),
            "extension": path.suffix.casefold() or None,
            "size_bytes": None,
            "allocated_size_bytes": None,
            "created_at_utc": None,
            "modified_at_utc": None,
            "last_accessed_at_utc": None,
            "last_access_reliability": "unavailable",
            "storage_category": "other_or_unreadable",
            "attributes": ["unavailable"],
            "evidence": [
                {
                    "attribute": "unavailable",
                    "code": code,
                    "description": message,
                    "observed_value": None,
                }
            ],
            "confidence": "low",
            "removal_risk": "protected",
            "protection": {
                "eligibility": "unavailable",
                "reason_code": code,
                "explanation": (
                    "Unreadable metadata prevents this path from being selected."
                ),
            },
            "is_regular_file": False,
            "is_reparse_point": False,
        }
        self._record_candidate(state, candidate)

    def _record_reparse_point(
        self,
        state: _ScanState,
        path: Path,
        scan_root: Path,
    ) -> None:
        message = (
            "The reparse point was not followed, preventing traversal outside "
            "the approved scan tree or a directory loop."
        )
        self._record_path_issue(
            state=state,
            path=path,
            scan_root=scan_root,
            error_type="reparse_point",
            code="reparse_point_skipped",
            scope="directory",
            message=message,
        )
        candidate = {
            "candidate_id": self._next_candidate_id(state),
            "path": _absolute_path_text(path),
            "scan_root": str(scan_root),
            "name": path.name or str(path),
            "extension": path.suffix.casefold() or None,
            "size_bytes": None,
            "allocated_size_bytes": None,
            "created_at_utc": None,
            "modified_at_utc": None,
            "last_accessed_at_utc": None,
            "last_access_reliability": "unavailable",
            "storage_category": "other_or_unreadable",
            "attributes": ["protected"],
            "evidence": [
                {
                    "attribute": "protected",
                    "code": "reparse_point_skipped",
                    "description": message,
                    "observed_value": True,
                }
            ],
            "confidence": "high",
            "removal_risk": "protected",
            "protection": {
                "eligibility": "protected",
                "reason_code": "reparse_point",
                "explanation": "Reparse points cannot be cleanup candidates.",
            },
            "is_regular_file": False,
            "is_reparse_point": True,
        }
        self._record_candidate(state, candidate)

    def _record_protected_path(
        self,
        state: _ScanState,
        path: Path,
        scan_root: Path,
    ) -> None:
        self._record_path_issue(
            state=state,
            path=path,
            scan_root=scan_root,
            error_type="path_unavailable",
            code="protected_path_skipped",
            scope="directory",
            message=(
                "A protected Windows or application path was skipped without "
                "inspection."
            ),
        )

    def _record_os_error(
        self,
        *,
        state: _ScanState,
        path: Path,
        scan_root: Path,
        error: BaseException,
        scope: str,
        retain_unavailable_candidate: bool = False,
    ) -> None:
        if isinstance(error, PermissionError):
            error_type = "access_denied"
            code = "access_denied"
            message = "Access was denied; the remaining scan continued."
        elif isinstance(error, FileNotFoundError):
            error_type = "disappeared"
            code = "path_disappeared"
            message = "The path changed or disappeared during the scan."
        else:
            error_type = "unreadable_metadata"
            code = "metadata_unreadable"
            message = "Metadata could not be read; the remaining scan continued."

        self._record_path_issue(
            state=state,
            path=path,
            scan_root=scan_root,
            error_type=error_type,
            code=code,
            scope=scope,
            message=message,
        )
        if retain_unavailable_candidate:
            self._record_unavailable_candidate(
                state,
                path,
                scan_root,
                code,
                message,
            )

    def _record_path_issue(
        self,
        *,
        state: _ScanState,
        path: Path,
        scan_root: Path,
        error_type: str,
        code: str,
        scope: str,
        message: str,
    ) -> None:
        occurred_at = _utc_text(self._clock().astimezone(timezone.utc))
        state.inaccessible_path_counter += 1
        if len(state.inaccessible_paths) < state.maximum_issue_records:
            state.inaccessible_paths.append(
                {
                    "path": _absolute_path_text(path),
                    "scan_root": str(scan_root),
                    "error_type": error_type,
                    "message": message,
                    "occurred_at_utc": occurred_at,
                }
            )
        self._record_scan_error(
            state,
            code=code,
            scope=scope,
            path=_absolute_path_text(path),
            message=message,
            recoverable=True,
            occurred_at=occurred_at,
        )

    def _record_scan_error(
        self,
        state: _ScanState,
        *,
        code: str,
        scope: str,
        path: str | None,
        message: str,
        recoverable: bool,
        occurred_at: str | None = None,
    ) -> None:
        state.scan_error_counter += 1
        if len(state.scan_errors) < state.maximum_issue_records:
            state.scan_errors.append(
                {
                    "code": code,
                    "scope": scope,
                    "path": path,
                    "message": message,
                    "recoverable": recoverable,
                    "occurred_at_utc": occurred_at
                    or _utc_text(self._clock().astimezone(timezone.utc)),
                }
            )

    @staticmethod
    def _scan_status(state: _ScanState) -> str:
        if state.cancelled:
            return "cancelled"
        if state.scan_error_counter:
            return "partial"
        return "complete"

    @staticmethod
    def _detail_coverage(state: _ScanState, status: str) -> str:
        if status != "complete":
            return "partial"
        if state.omitted_candidates:
            return "bounded"
        return "complete"

    def _build_accounting(
        self,
        *,
        state: _ScanState,
        used_bytes: int,
        free_bytes: int,
        total_bytes: int,
        status: str,
    ) -> dict[str, Any]:
        observed_categories = dict(state.category_allocated_bytes)
        observed_used = sum(observed_categories.values())
        if observed_used > used_bytes:
            self._record_scan_error(
                state,
                code="observed_bytes_exceed_drive_usage",
                scope="accounting",
                path=None,
                message=(
                    "Observed logical file sizes exceeded current drive usage, "
                    "so selected-root category estimates were not applied."
                ),
                recoverable=True,
            )
            observed_categories = {
                category: 0 for category in observed_categories
            }
            observed_used = 0

        other_bytes = used_bytes - observed_used
        coverage = (
            "partial"
            if status != "complete" or state.scan_error_counter
            else "estimated"
        )
        return {
            "coverage": coverage,
            "categories": {
                "free_space": {
                    "bytes": free_bytes,
                    "measurement": "exact",
                    "explanation": "Free bytes reported by the selected drive.",
                },
                "protected_system": {
                    "bytes": observed_categories["protected_system"],
                    "measurement": (
                        "estimated"
                        if observed_categories["protected_system"]
                        else "unavailable"
                    ),
                    "explanation": (
                        "Allocated bytes observed in accessible protected Windows, "
                        "recovery, and Recycle Bin locations."
                    ),
                },
                "installed_applications": {
                    "bytes": observed_categories["installed_applications"],
                    "measurement": (
                        "estimated"
                        if observed_categories["installed_applications"]
                        else "unavailable"
                    ),
                    "explanation": (
                        "Allocated bytes observed in accessible Program Files and "
                        "ProgramData locations; these files are never candidates."
                    ),
                },
                "user_content": {
                    "bytes": observed_categories["user_content"],
                    "measurement": (
                        "estimated"
                        if observed_categories["user_content"]
                        else "unavailable"
                    ),
                    "explanation": (
                        "Allocated bytes observed in accessible scan locations "
                        "outside protected and development-tool categories."
                    ),
                },
                "development_tools_and_caches": {
                    "bytes": observed_categories[
                        "development_tools_and_caches"
                    ],
                    "measurement": (
                        "estimated"
                        if observed_categories[
                            "development_tools_and_caches"
                        ]
                        else "unavailable"
                    ),
                    "explanation": (
                        "Allocated bytes observed in supported development "
                        "locations and explicit development-cache roots."
                    ),
                },
                "other_or_unreadable": {
                    "bytes": other_bytes,
                    "measurement": "estimated",
                    "explanation": (
                        "Remaining physical drive usage not assigned from "
                        "accessible allocated-size metadata, including filesystem "
                        "overhead and inaccessible locations."
                    ),
                },
            },
        }

    @staticmethod
    def _limitations(
        state: _ScanState,
        development_cache_roots: tuple[Path, ...],
        *,
        whole_drive_scan: bool,
    ) -> list[str]:
        scope_limitation = (
            "The explicitly selected drive root was scanned across all accessible, "
            "non-protected folders."
            if whole_drive_scan
            else "Only explicitly selected user-approved folders were scanned."
        )
        limitations = [
            scope_limitation,
            "Only metadata was inspected; file contents and content hashes were not read.",
            "Drive categories use Windows allocated-size metadata and stable file identities; inaccessible bytes remain unclassified.",
            "Modification time does not prove whether a file is useful or safe to remove.",
            "Likely incomplete describes naming and age evidence, not proven corruption.",
            "Last-access time is informational and was not used as classification evidence.",
            "Protected Windows and application files are measured when accessible but never become cleanup candidates; reparse points are not followed.",
            "File identities were counted once when the operating system exposed a stable identity.",
            "Application-managed data, installer-style files, databases, and likely save data are review-only.",
        ]
        if state.omitted_candidates:
            limitations.append(
                f"{state.omitted_candidates} candidate detail record(s) were "
                "omitted by the configured limit; aggregate totals include them."
            )
        if state.omitted_inaccessible_paths:
            limitations.append(
                f"{state.omitted_inaccessible_paths} additional inaccessible-path "
                "record(s) were omitted by the configured safety limit."
            )
        if state.omitted_scan_errors:
            limitations.append(
                f"{state.omitted_scan_errors} additional scan-error record(s) "
                "were omitted by the configured safety limit."
            )
        if development_cache_roots:
            limitations.append(
                "Development-cache classification applies only to roots explicitly supplied for this scan."
            )
        return limitations

    @staticmethod
    def _progress_update(state: _ScanState, path: str) -> ProgressUpdate:
        return ProgressUpdate(
            current_path=path,
            files_examined=state.files_examined,
            directories_examined=state.directories_examined,
            bytes_examined=state.bytes_examined,
            candidates_found=state.candidate_counter,
        )
