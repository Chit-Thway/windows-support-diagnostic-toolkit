"""Read-only, metadata-only scanner for user-approved Windows folders."""

from __future__ import annotations

import ctypes
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


class ScanConfigurationError(ValueError):
    """Raised when scanner inputs cannot produce a safe per-drive scan."""


@dataclass(frozen=True)
class ScannerOptions:
    """Bounded scanner settings captured in the output report."""

    classification: ClassificationOptions = field(
        default_factory=ClassificationOptions
    )
    maximum_candidates_retained: int = 5000
    development_cache_roots: tuple[str | Path, ...] = ()
    progress_every_files: int = 100

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_candidates_retained <= 100000:
            raise ValueError(
                "maximum_candidates_retained must be between 1 and 100000"
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
    files_examined: int = 0
    directories_examined: int = 0
    bytes_examined: int = 0
    candidate_counter: int = 0
    total_candidate_bytes: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    inaccessible_paths: list[dict[str, Any]] = field(default_factory=list)
    scan_errors: list[dict[str, Any]] = field(default_factory=list)
    seen_file_identities: set[tuple[object, ...]] = field(default_factory=set)
    user_content_bytes: int = 0
    development_cache_bytes: int = 0
    cancelled: bool = False
    attribute_summaries: dict[str, dict[str, int]] = field(
        default_factory=lambda: {
            name: {"candidate_count": 0, "unique_bytes": 0}
            for name in ATTRIBUTE_NAMES
        }
    )

    @property
    def omitted_candidates(self) -> int:
        return self.candidate_counter - len(self.candidates)


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
    ) -> None:
        self._path_policy = path_policy or ProtectedPathPolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._disk_usage = disk_usage or shutil.disk_usage
        self._volume_information = volume_information or _volume_information

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
        state = _ScanState(
            started_at=started_at,
            maximum_candidates_retained=options.maximum_candidates_retained,
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
                "candidate_details_retained": len(state.candidates),
                "candidate_details_omitted": state.omitted_candidates,
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
                    "use_last_access_as_classification_evidence": False,
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
                "omitted_candidates": state.omitted_candidates,
                "attributes": state.attribute_summaries,
            },
            "candidates": state.candidates,
            "inaccessible_paths": state.inaccessible_paths,
            "scan_errors": state.scan_errors,
            "limitations": self._limitations(state, development_cache_roots),
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
        cancel_check: Callable[[], bool],
        progress_callback: Callable[[ProgressUpdate], None] | None,
    ) -> dict[str, Any]:
        root_files = 0
        root_directories = 0
        root_bytes = 0
        root_errors_before = len(state.scan_errors)
        stack = [root]

        while stack and not state.cancelled:
            if cancel_check():
                state.cancelled = True
                break
            directory = stack.pop()
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
                if self._path_policy.is_protected(path, drive_letter):
                    self._record_protected_path(state, path, root)
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    stack.append(path)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    continue

                identity = self._file_identity(path, metadata)
                if identity in state.seen_file_identities:
                    continue
                state.seen_file_identities.add(identity)

                size_bytes = max(0, int(metadata.st_size))
                state.files_examined += 1
                state.bytes_examined += size_bytes
                root_files += 1
                root_bytes += size_bytes

                in_cache = is_development_cache_path(
                    path, development_cache_roots
                )
                if in_cache:
                    state.development_cache_bytes += size_bytes
                else:
                    state.user_content_bytes += size_bytes

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
                    candidate = {
                        "candidate_id": self._next_candidate_id(state),
                        "path": _absolute_path_text(path),
                        "scan_root": str(root),
                        "name": path.name,
                        "extension": path.suffix.casefold() or None,
                        "size_bytes": size_bytes,
                        "created_at_utc": _utc_text(created_at),
                        "modified_at_utc": _utc_text(modified_at),
                        "last_accessed_at_utc": _utc_text(accessed_at),
                        "last_access_reliability": "limited",
                        "storage_category": result.storage_category,
                        "attributes": list(result.attributes),
                        "evidence": list(result.evidence),
                        "confidence": result.confidence,
                        "protection": {
                            "eligibility": "eligible",
                            "reason_code": None,
                            "explanation": (
                                "The regular file is inside an approved scan "
                                "root and is not a detected reparse point."
                            ),
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

        root_error_count = len(state.scan_errors) - root_errors_before
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
            "errors_count": 0,
        }

    @staticmethod
    def _file_identity(path: Path, metadata: os.stat_result) -> tuple[object, ...]:
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
        state.total_candidate_bytes += size_bytes
        for attribute in candidate["attributes"]:
            state.attribute_summaries[attribute]["candidate_count"] += 1
            state.attribute_summaries[attribute]["unique_bytes"] += size_bytes
        if len(state.candidates) < state.maximum_candidates_retained:
            state.candidates.append(candidate)

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
        state.inaccessible_paths.append(
            {
                "path": _absolute_path_text(path),
                "scan_root": str(scan_root),
                "error_type": error_type,
                "message": message,
                "occurred_at_utc": occurred_at,
            }
        )
        state.scan_errors.append(
            {
                "code": code,
                "scope": scope,
                "path": _absolute_path_text(path),
                "message": message,
                "recoverable": True,
                "occurred_at_utc": occurred_at,
            }
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
    ) -> None:
        state.scan_errors.append(
            {
                "code": code,
                "scope": scope,
                "path": path,
                "message": message,
                "recoverable": recoverable,
                "occurred_at_utc": _utc_text(
                    self._clock().astimezone(timezone.utc)
                ),
            }
        )

    @staticmethod
    def _scan_status(state: _ScanState) -> str:
        if state.cancelled:
            return "cancelled"
        if state.scan_errors:
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
        observed_used = state.user_content_bytes + state.development_cache_bytes
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
            user_content_bytes = 0
            development_cache_bytes = 0
        else:
            user_content_bytes = state.user_content_bytes
            development_cache_bytes = state.development_cache_bytes

        other_bytes = used_bytes - user_content_bytes - development_cache_bytes
        coverage = "partial" if status != "complete" or state.scan_errors else "estimated"
        return {
            "coverage": coverage,
            "categories": {
                "free_space": {
                    "bytes": free_bytes,
                    "measurement": "exact",
                    "explanation": "Free bytes reported by the selected drive.",
                },
                "protected_system": {
                    "bytes": 0,
                    "measurement": "unavailable",
                    "explanation": (
                        "Protected Windows storage is not recursively scanned in "
                        "this milestone."
                    ),
                },
                "installed_applications": {
                    "bytes": 0,
                    "measurement": "unavailable",
                    "explanation": (
                        "Installed application directories are protected and are "
                        "not recursively scanned."
                    ),
                },
                "user_content": {
                    "bytes": user_content_bytes,
                    "measurement": (
                        "estimated" if user_content_bytes else "unavailable"
                    ),
                    "explanation": (
                        "Logical file bytes observed in accessible, user-approved "
                        "roots outside explicit development-cache roots."
                    ),
                },
                "development_tools_and_caches": {
                    "bytes": development_cache_bytes,
                    "measurement": (
                        "estimated"
                        if development_cache_bytes
                        else "unavailable"
                    ),
                    "explanation": (
                        "Logical file bytes observed only in development-cache "
                        "roots explicitly supplied by the user."
                    ),
                },
                "other_or_unreadable": {
                    "bytes": other_bytes,
                    "measurement": "estimated",
                    "explanation": (
                        "Remaining used drive space was not assigned by the "
                        "selected-root metadata scan."
                    ),
                },
            },
        }

    @staticmethod
    def _limitations(
        state: _ScanState,
        development_cache_roots: tuple[Path, ...],
    ) -> list[str]:
        limitations = [
            "Only user-approved roots were scanned; this is not a silent whole-drive scan.",
            "Only metadata was inspected; file contents and content hashes were not read.",
            "Modification time does not prove whether a file is useful or safe to remove.",
            "Likely incomplete describes naming and age evidence, not proven corruption.",
            "Last-access time is informational and was not used as classification evidence.",
            "Protected paths and reparse points were not followed.",
            "File identities were counted once when the operating system exposed a stable identity.",
        ]
        if state.omitted_candidates:
            limitations.append(
                f"{state.omitted_candidates} candidate detail record(s) were "
                "omitted by the configured limit; aggregate totals include them."
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
