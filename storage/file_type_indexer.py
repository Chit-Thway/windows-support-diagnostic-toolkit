"""Fast, metadata-only whole-drive indexer for File-Type Explorer."""

from __future__ import annotations

import argparse
import ctypes
import heapq
import os
import re
import shutil
import signal
import stat
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .file_type_contract import (
    PRESET_EXTENSION_GROUPS,
    FileTypeIndexValidationError,
    FileTypeIndexWriteError,
    validate_file_type_index,
    write_file_type_index,
)
from .path_policy import (
    DRIVE_PATTERN,
    FILE_ATTRIBUTE_REPARSE_POINT,
    ProtectedPathPolicy,
    is_path_within,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_REPORT_DIRECTORY = PROJECT_ROOT / "storage-reports"
INSTALLER_EXTENSIONS = frozenset(
    PRESET_EXTENSION_GROUPS["installers"][1]
)
EXTENSION_PATTERN = re.compile(r'^\.[^\\/:*?"<>|]+$')


class FileTypeIndexConfigurationError(ValueError):
    """Raised when a requested drive index cannot be created safely."""


@dataclass(frozen=True)
class FileTypeIndexerOptions:
    """Bound memory and issue details without weakening aggregate totals."""

    custom_extensions: tuple[str, ...] = ()
    maximum_file_details: int = 100_000
    maximum_issue_records: int = 1_000
    progress_every_entries: int = 1_000

    def __post_init__(self) -> None:
        if not 0 <= self.maximum_file_details <= 1_000_000:
            raise ValueError(
                "maximum_file_details must be between 0 and 1000000"
            )
        if not 1 <= self.maximum_issue_records <= 10_000:
            raise ValueError(
                "maximum_issue_records must be between 1 and 10000"
            )
        if self.progress_every_entries < 1:
            raise ValueError("progress_every_entries must be at least 1")


@dataclass(frozen=True)
class FileTypeProgress:
    """Truthful scan progress with no fabricated completion percentage."""

    current_path: str
    files_examined: int
    directories_examined: int
    logical_bytes_observed: int
    matching_files: int
    elapsed_seconds: float


@dataclass
class _FolderAggregate:
    folder_id: str
    parent_id: str | None
    path: Path
    name: str
    depth: int
    access: dict[str, str]
    child_ids: list[str] = field(default_factory=list)
    direct_file_count: int = 0
    recursive_file_count: int = 0
    direct_logical_bytes: int = 0
    recursive_logical_bytes: int = 0
    direct_zero_byte_files: int = 0
    recursive_zero_byte_files: int = 0
    recursive_directory_count: int = 0
    direct_extensions: dict[str, list[int]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0])
    )
    recursive_extensions: dict[str, list[int]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0])
    )


@dataclass
class _IndexState:
    started_at: datetime
    started_monotonic: float
    maximum_file_details: int
    maximum_issue_records: int
    folders: dict[str, _FolderAggregate] = field(default_factory=dict)
    folder_counter: int = 0
    file_counter: int = 0
    entries_examined: int = 0
    files_examined: int = 0
    directories_examined: int = 0
    logical_bytes_observed: int = 0
    zero_byte_files: int = 0
    matching_files: int = 0
    matching_logical_bytes: int = 0
    file_heap: list[tuple[tuple[int, int], dict[str, Any]]] = field(
        default_factory=list
    )
    inaccessible_paths: list[dict[str, Any]] = field(default_factory=list)
    scan_errors: list[dict[str, Any]] = field(default_factory=list)
    inaccessible_path_counter: int = 0
    scan_error_counter: int = 0
    cancelled: bool = False
    root_failed: bool = False

    @property
    def omitted_inaccessible_paths(self) -> int:
        return self.inaccessible_path_counter - len(self.inaccessible_paths)

    @property
    def omitted_scan_errors(self) -> int:
        return self.scan_error_counter - len(self.scan_errors)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_from_epoch(value: float) -> str | None:
    try:
        return _utc_text(datetime.fromtimestamp(value, timezone.utc))
    except (OSError, OverflowError, ValueError):
        return None


def _volume_information(drive_letter: str) -> tuple[str | None, str | None]:
    """Read optional Windows volume metadata without changing the volume."""

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


def _normalize_custom_extensions(values: tuple[str, ...]) -> tuple[str, ...]:
    presets = {
        extension
        for _label, extensions in PRESET_EXTENSION_GROUPS.values()
        for extension in extensions
    }
    normalized: list[str] = []
    seen: set[str] = set()
    for supplied in values:
        extension = supplied.strip().casefold()
        if extension and not extension.startswith("."):
            extension = f".{extension}"
        if (
            not 2 <= len(extension) <= 255
            or EXTENSION_PATTERN.fullmatch(extension) is None
        ):
            raise FileTypeIndexConfigurationError(
                f"Invalid custom extension: {supplied!r}."
            )
        if extension in presets:
            raise FileTypeIndexConfigurationError(
                f"Custom extension {extension!r} is already in a preset group."
            )
        if extension not in seen:
            normalized.append(extension)
            seen.add(extension)
    if len(normalized) > 100:
        raise FileTypeIndexConfigurationError(
            "No more than 100 custom extensions can be indexed."
        )
    return tuple(normalized)


def _extension_groups() -> list[dict[str, Any]]:
    return [
        {
            "group_id": group_id,
            "label": label,
            "extensions": list(extensions),
        }
        for group_id, (label, extensions) in PRESET_EXTENSION_GROUPS.items()
    ]


def _error_type(error: OSError) -> str:
    if isinstance(error, PermissionError):
        return "access_denied"
    if isinstance(error, FileNotFoundError):
        return "disappeared"
    if getattr(error, "winerror", None) in {32, 33}:
        return "locked"
    return "other"


def _bounded_message(error: BaseException, fallback: str) -> str:
    message = str(error).strip() or fallback
    return message[:500]


def _entry_is_reparse(entry: os.DirEntry[str], metadata: os.stat_result) -> bool:
    try:
        if entry.is_symlink():
            return True
    except OSError:
        pass
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & FILE_ATTRIBUTE_REPARSE_POINT
    )


class FileTypeIndexer:
    """Enumerate one explicitly selected Windows drive exactly once."""

    def __init__(
        self,
        *,
        path_policy: ProtectedPathPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        disk_usage: Callable[[str | os.PathLike[str]], Any] | None = None,
        volume_information: (
            Callable[[str], tuple[str | None, str | None]] | None
        ) = None,
        root_validator: (
            Callable[[str | Path], tuple[Path, str]] | None
        ) = None,
    ) -> None:
        self._path_policy = path_policy or ProtectedPathPolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._disk_usage = disk_usage or shutil.disk_usage
        self._volume_information = volume_information or _volume_information
        self._root_validator = root_validator or self._validate_drive_root

    @staticmethod
    def _validate_drive_root(requested_root: str | Path) -> tuple[Path, str]:
        if os.name != "nt":
            raise FileTypeIndexConfigurationError(
                "The File-Type Explorer indexer currently supports Windows only."
            )

        expanded = Path(requested_root).expanduser()
        lexical = Path(os.path.abspath(str(expanded)))
        drive_letter = lexical.drive.upper()
        if not DRIVE_PATTERN.fullmatch(drive_letter):
            raise FileTypeIndexConfigurationError(
                "Select a local Windows drive-letter root such as C:\\."
            )
        expected_root = os.path.normcase(
            os.path.abspath(f"{drive_letter}\\")
        )
        if os.path.normcase(str(lexical)) != expected_root:
            raise FileTypeIndexConfigurationError(
                "The V2 index requires an explicit whole-drive root such as C:\\."
            )
        try:
            root = lexical.resolve(strict=True)
        except OSError as error:
            raise FileTypeIndexConfigurationError(
                f"The requested drive root is unavailable: {lexical}"
            ) from error
        if not root.is_dir():
            raise FileTypeIndexConfigurationError(
                f"The requested drive root is not a directory: {root}"
            )
        return root, drive_letter

    def index_drive(
        self,
        drive_root: str | Path,
        *,
        options: FileTypeIndexerOptions | None = None,
        cancel_check: Callable[[], bool] | None = None,
        progress_callback: Callable[[FileTypeProgress], None] | None = None,
    ) -> dict[str, Any]:
        """Build a contract-valid index without reading or changing contents."""

        options = options or FileTypeIndexerOptions()
        cancel_check = cancel_check or (lambda: False)
        root, drive_letter = self._root_validator(drive_root)
        custom_extensions = _normalize_custom_extensions(
            tuple(options.custom_extensions)
        )
        indexed_extensions = {
            extension
            for _label, extensions in PRESET_EXTENSION_GROUPS.values()
            for extension in extensions
        }
        indexed_extensions.update(custom_extensions)
        protected_roots = tuple(
            path
            for path in self._path_policy.protected_roots_for_drive(
                drive_letter
            )
            if path.drive.upper() == drive_letter
        )

        try:
            capacity = self._disk_usage(root)
            total_bytes = int(capacity.total)
            free_bytes = int(capacity.free)
        except (OSError, TypeError, ValueError, AttributeError) as error:
            raise FileTypeIndexConfigurationError(
                f"Drive capacity could not be read for {drive_letter}."
            ) from error
        if total_bytes <= 0 or not 0 <= free_bytes <= total_bytes:
            raise FileTypeIndexConfigurationError(
                f"Drive capacity is unavailable for {drive_letter}."
            )

        started_at = self._clock().astimezone(timezone.utc)
        state = _IndexState(
            started_at=started_at,
            started_monotonic=self._monotonic(),
            maximum_file_details=options.maximum_file_details,
            maximum_issue_records=options.maximum_issue_records,
        )
        root_folder = self._add_folder(
            state,
            path=root,
            parent_id=None,
            depth=0,
            drive_letter=drive_letter,
            scope_root=root,
            protected_roots=protected_roots,
        )
        state.directories_examined = 1
        stack = [root_folder.folder_id]

        while stack and not state.cancelled:
            if cancel_check():
                state.cancelled = True
                break
            folder_id = stack.pop()
            folder = state.folders[folder_id]
            try:
                with os.scandir(folder.path) as entries:
                    for entry in entries:
                        if cancel_check():
                            state.cancelled = True
                            break
                        self._inspect_entry(
                            entry=entry,
                            folder=folder,
                            drive_letter=drive_letter,
                            scope_root=root,
                            protected_roots=protected_roots,
                            indexed_extensions=indexed_extensions,
                            custom_extensions=set(custom_extensions),
                            state=state,
                            stack=stack,
                        )
                        if (
                            progress_callback is not None
                            and state.entries_examined
                            % options.progress_every_entries
                            == 0
                        ):
                            self._emit_progress(
                                state, Path(entry.path), progress_callback
                            )
            except OSError as error:
                folder.access = {
                    "state": "unavailable",
                    "reason_code": "directory_unavailable",
                    "explanation": (
                        "The directory could not be fully enumerated during "
                        "this index."
                    ),
                }
                self._record_os_error(
                    state,
                    path=folder.path,
                    scope="directory",
                    code="directory_enumeration_failed",
                    error=error,
                )
                if folder.parent_id is None:
                    state.root_failed = True

        if state.cancelled:
            self._record_scan_error(
                state,
                code="scan_cancelled",
                scope="report",
                path=None,
                message=(
                    "The user cancelled the scan. Observed aggregate values "
                    "are partial."
                ),
                recoverable=True,
            )

        self._aggregate_folders(state)
        completed_at = self._clock().astimezone(timezone.utc)
        if progress_callback is not None:
            self._emit_progress(state, root, progress_callback)
        report = self._build_report(
            root=root,
            drive_letter=drive_letter,
            capacity=(total_bytes, total_bytes - free_bytes, free_bytes),
            custom_extensions=custom_extensions,
            state=state,
            completed_at=completed_at,
        )
        validate_file_type_index(report)
        return report

    def _add_folder(
        self,
        state: _IndexState,
        *,
        path: Path,
        parent_id: str | None,
        depth: int,
        drive_letter: str,
        scope_root: Path,
        protected_roots: tuple[Path, ...],
    ) -> _FolderAggregate:
        state.folder_counter += 1
        folder_id = f"folder-{state.folder_counter:08d}"
        folder = _FolderAggregate(
            folder_id=folder_id,
            parent_id=parent_id,
            path=path,
            name=drive_letter if parent_id is None else path.name,
            depth=depth,
            access=self._folder_access(path, scope_root, protected_roots),
        )
        state.folders[folder_id] = folder
        if parent_id is not None:
            state.folders[parent_id].child_ids.append(folder_id)
        return folder

    def _folder_access(
        self,
        path: Path,
        scope_root: Path,
        protected_roots: tuple[Path, ...],
    ) -> dict[str, str]:
        if any(is_path_within(path, root) for root in protected_roots):
            return {
                "state": "protected",
                "reason_code": "protected_location",
                "explanation": (
                    "The folder is visible for storage context but protected "
                    "from cleanup selection."
                ),
            }
        try:
            relative_parts = path.relative_to(scope_root).parts
        except ValueError:
            relative_parts = path.parts
        if any(part.casefold() == "appdata" for part in relative_parts):
            return {
                "state": "review_only",
                "reason_code": "application_managed_data",
                "explanation": (
                    "Application-managed data requires individual review and "
                    "is excluded from convenient bulk selection."
                ),
            }
        return {
            "state": "normal",
            "reason_code": "normal_folder",
            "explanation": (
                "The folder is indexed for user review; no cleanup decision "
                "is inferred."
            ),
        }

    def _inspect_entry(
        self,
        *,
        entry: os.DirEntry[str],
        folder: _FolderAggregate,
        drive_letter: str,
        scope_root: Path,
        protected_roots: tuple[Path, ...],
        indexed_extensions: set[str],
        custom_extensions: set[str],
        state: _IndexState,
        stack: list[str],
    ) -> None:
        state.entries_examined += 1
        path = Path(entry.path)
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as error:
            self._record_os_error(
                state,
                path=path,
                scope="file",
                code="metadata_read_failed",
                error=error,
            )
            return

        if _entry_is_reparse(entry, metadata):
            if stat.S_ISDIR(metadata.st_mode):
                reparse_folder = self._add_folder(
                    state,
                    path=path,
                    parent_id=folder.folder_id,
                    depth=folder.depth + 1,
                    drive_letter=drive_letter,
                    scope_root=scope_root,
                    protected_roots=protected_roots,
                )
                reparse_folder.access = {
                    "state": "unavailable",
                    "reason_code": "reparse_point",
                    "explanation": (
                        "Reparse-point targets are not followed by the indexer."
                    ),
                }
                state.directories_examined += 1
            self._record_inaccessible(
                state,
                path=path,
                error_type="reparse_point",
                message="Reparse-point targets are not followed.",
            )
            self._record_scan_error(
                state,
                code="reparse_point_skipped",
                scope="directory" if stat.S_ISDIR(metadata.st_mode) else "file",
                path=path,
                message="A reparse point was skipped without following its target.",
                recoverable=True,
            )
            return

        if stat.S_ISDIR(metadata.st_mode):
            child = self._add_folder(
                state,
                path=path,
                parent_id=folder.folder_id,
                depth=folder.depth + 1,
                drive_letter=drive_letter,
                scope_root=scope_root,
                protected_roots=protected_roots,
            )
            state.directories_examined += 1
            stack.append(child.folder_id)
            return
        if not stat.S_ISREG(metadata.st_mode):
            return

        size_bytes = max(0, int(metadata.st_size))
        folder.direct_file_count += 1
        folder.direct_logical_bytes += size_bytes
        state.files_examined += 1
        state.logical_bytes_observed += size_bytes
        if size_bytes == 0:
            folder.direct_zero_byte_files += 1
            state.zero_byte_files += 1

        extension = path.suffix.casefold()
        if extension not in indexed_extensions:
            return
        totals = folder.direct_extensions[extension]
        totals[0] += 1
        totals[1] += size_bytes
        state.matching_files += 1
        state.matching_logical_bytes += size_bytes
        if size_bytes == 0:
            return

        state.file_counter += 1
        priority = (size_bytes, -state.file_counter)
        if not self._file_detail_is_retainable(state, priority):
            return
        selection_state, reason = self._file_access(
            folder=folder,
            extension=extension,
            is_custom=extension in custom_extensions,
        )
        row = {
            "file_id": f"file-{state.file_counter:09d}",
            "folder_id": folder.folder_id,
            "path": str(path),
            "name": path.name,
            "extension": extension,
            "size_bytes": size_bytes,
            "modified_at_utc": _timestamp_from_epoch(metadata.st_mtime),
            "selection_state": selection_state,
            "protection_reason": reason,
        }
        self._retain_file(state, row, priority)

    @staticmethod
    def _file_access(
        *,
        folder: _FolderAggregate,
        extension: str,
        is_custom: bool,
    ) -> tuple[str, str]:
        if folder.access["state"] in {"protected", "unavailable"}:
            return (
                "protected",
                "The containing folder is protected or unavailable.",
            )
        if folder.access["state"] == "review_only":
            return (
                "review_only",
                "Application-managed data requires individual review.",
            )
        if extension in INSTALLER_EXTENSIONS:
            return (
                "review_only",
                "Installer files can support repair or reinstall workflows.",
            )
        if is_custom:
            return (
                "review_only",
                "A custom extension has no automatic cleanup safety meaning.",
            )
        return (
            "selectable",
            "The extension is indexed for explicit human review before cleanup.",
        )

    @staticmethod
    def _file_detail_is_retainable(
        state: _IndexState, priority: tuple[int, int]
    ) -> bool:
        return state.maximum_file_details > 0 and (
            len(state.file_heap) < state.maximum_file_details
            or priority > state.file_heap[0][0]
        )

    @staticmethod
    def _retain_file(
        state: _IndexState,
        row: dict[str, Any],
        priority: tuple[int, int],
    ) -> None:
        item = (priority, row)
        if len(state.file_heap) < state.maximum_file_details:
            heapq.heappush(state.file_heap, item)
        elif priority > state.file_heap[0][0]:
            heapq.heapreplace(state.file_heap, item)

    def _aggregate_folders(self, state: _IndexState) -> None:
        for folder in state.folders.values():
            folder.recursive_file_count = folder.direct_file_count
            folder.recursive_logical_bytes = folder.direct_logical_bytes
            folder.recursive_zero_byte_files = folder.direct_zero_byte_files
            folder.recursive_extensions = defaultdict(
                lambda: [0, 0],
                {
                    extension: values.copy()
                    for extension, values in folder.direct_extensions.items()
                },
            )

        for folder in sorted(
            state.folders.values(), key=lambda item: item.depth, reverse=True
        ):
            for child_id in folder.child_ids:
                child = state.folders[child_id]
                folder.recursive_file_count += child.recursive_file_count
                folder.recursive_logical_bytes += child.recursive_logical_bytes
                folder.recursive_zero_byte_files += (
                    child.recursive_zero_byte_files
                )
                folder.recursive_directory_count += (
                    1 + child.recursive_directory_count
                )
                for extension, values in child.recursive_extensions.items():
                    totals = folder.recursive_extensions[extension]
                    totals[0] += values[0]
                    totals[1] += values[1]

    def _folder_rows(self, state: _IndexState) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for folder in state.folders.values():
            extensions = sorted(folder.recursive_extensions)
            rows.append(
                {
                    "folder_id": folder.folder_id,
                    "parent_id": folder.parent_id,
                    "path": str(folder.path),
                    "name": folder.name,
                    "depth": folder.depth,
                    "direct_file_count": folder.direct_file_count,
                    "recursive_file_count": folder.recursive_file_count,
                    "direct_logical_bytes": folder.direct_logical_bytes,
                    "recursive_logical_bytes": folder.recursive_logical_bytes,
                    "direct_zero_byte_files": folder.direct_zero_byte_files,
                    "recursive_zero_byte_files": (
                        folder.recursive_zero_byte_files
                    ),
                    "extension_totals": [
                        {
                            "extension": extension,
                            "direct_file_count": folder.direct_extensions.get(
                                extension, [0, 0]
                            )[0],
                            "direct_logical_bytes": folder.direct_extensions.get(
                                extension, [0, 0]
                            )[1],
                            "recursive_file_count": folder.recursive_extensions[
                                extension
                            ][0],
                            "recursive_logical_bytes": folder.recursive_extensions[
                                extension
                            ][1],
                        }
                        for extension in extensions
                    ],
                    "access": folder.access,
                }
            )
        return rows

    @staticmethod
    def _empty_trees(state: _IndexState) -> list[dict[str, Any]]:
        trees: list[dict[str, Any]] = []
        for folder in state.folders.values():
            if folder.parent_id is None or folder.recursive_logical_bytes != 0:
                continue
            if (
                folder.recursive_zero_byte_files == 0
                and folder.recursive_directory_count == 0
            ):
                continue
            parent = state.folders[folder.parent_id]
            if parent.parent_id is not None and parent.recursive_logical_bytes == 0:
                continue
            trees.append(
                {
                    "highest_folder_id": folder.folder_id,
                    "path": str(folder.path),
                    "descendant_zero_byte_files": (
                        folder.recursive_zero_byte_files
                    ),
                    "descendant_directories": (
                        folder.recursive_directory_count
                    ),
                    "recoverable_bytes": 0,
                }
            )
        return trees

    @staticmethod
    def _retained_files(state: _IndexState) -> list[dict[str, Any]]:
        rows = [item[1] for item in state.file_heap]
        for row in rows:
            folder = state.folders[row["folder_id"]]
            if folder.access["state"] in {"protected", "unavailable"}:
                row["selection_state"] = "protected"
                row["protection_reason"] = (
                    "The containing folder is protected or unavailable."
                )
            elif (
                folder.access["state"] == "review_only"
                and row["selection_state"] == "selectable"
            ):
                row["selection_state"] = "review_only"
                row["protection_reason"] = (
                    "Application-managed data requires individual review."
                )
        return sorted(
            rows,
            key=lambda row: (-row["size_bytes"], row["path"].casefold()),
        )

    def _build_report(
        self,
        *,
        root: Path,
        drive_letter: str,
        capacity: tuple[int, int, int],
        custom_extensions: tuple[str, ...],
        state: _IndexState,
        completed_at: datetime,
    ) -> dict[str, Any]:
        retained_files = self._retained_files(state)
        retained_bytes = sum(row["size_bytes"] for row in retained_files)
        omitted_files = state.matching_files - len(retained_files)
        omitted_bytes = state.matching_logical_bytes - retained_bytes
        if state.root_failed:
            status = "failed"
            coverage = "unavailable"
        elif state.cancelled:
            status = "cancelled"
            coverage = "partial"
        elif state.inaccessible_path_counter or state.scan_error_counter:
            status = "partial"
            coverage = "partial"
        else:
            status = "complete"
            coverage = "exact"
        volume_label, filesystem = self._volume_information(drive_letter)
        duration_ms = max(
            0, int((self._monotonic() - state.started_monotonic) * 1000)
        )
        timestamp_id = completed_at.strftime("%Y%m%dT%H%M%SZ")
        return {
            "schema_version": "1.0.0",
            "index_type": "file_type_index",
            "index_id": (
                f"file-type-{drive_letter[0].casefold()}-{timestamp_id}"
            ),
            "generated_at_utc": _utc_text(completed_at),
            "indexer": {
                "name": "File-Type Explorer Indexer",
                "version": __version__,
                "platform": "Windows",
                "mode": "metadata_only",
            },
            "drive": {
                "drive_letter": drive_letter,
                "volume_label": volume_label,
                "filesystem": filesystem,
                "total_bytes": capacity[0],
                "used_bytes": capacity[1],
                "free_bytes": capacity[2],
                "observed_at_utc": _utc_text(state.started_at),
            },
            "scan": {
                "started_at_utc": _utc_text(state.started_at),
                "completed_at_utc": _utc_text(completed_at),
                "duration_ms": duration_ms,
                "status": status,
                "aggregate_coverage": coverage,
                "files_examined": state.files_examined,
                "directories_examined": state.directories_examined,
                "logical_bytes_observed": state.logical_bytes_observed,
                "zero_byte_files": state.zero_byte_files,
                "matching_files": state.matching_files,
                "matching_logical_bytes": state.matching_logical_bytes,
                "inaccessible_path_details_retained": len(
                    state.inaccessible_paths
                ),
                "inaccessible_path_details_omitted": (
                    state.omitted_inaccessible_paths
                ),
                "scan_error_details_retained": len(state.scan_errors),
                "scan_error_details_omitted": state.omitted_scan_errors,
            },
            "scope": {
                "root_path": str(root),
                "recursive": True,
                "explicitly_requested": True,
            },
            "extension_groups": _extension_groups(),
            "custom_extensions": list(custom_extensions),
            "folders": self._folder_rows(state),
            "empty_summary": {
                "zero_byte_files": state.zero_byte_files,
                "collapsed_tree_count": len(self._empty_trees(state)),
                "trees": self._empty_trees(state),
            },
            "file_detail_summary": {
                "coverage": "bounded" if omitted_files else "complete",
                "retained_files": len(retained_files),
                "omitted_files": omitted_files,
                "retained_logical_bytes": retained_bytes,
                "omitted_logical_bytes": omitted_bytes,
            },
            "files": retained_files,
            "inaccessible_paths": state.inaccessible_paths,
            "scan_errors": state.scan_errors,
            "limitations": [
                (
                    "The index records filesystem metadata only and never "
                    "reads file contents."
                ),
                (
                    "Folder sizes are logical byte totals, not allocated "
                    "on-disk size."
                ),
                (
                    "Age and extension organize human review; they do not "
                    "prove a file is disposable."
                ),
                (
                    "Reparse-point targets are not followed, so linked "
                    "storage is not double-counted."
                ),
                (
                    "Detailed matching-file rows may be bounded while folder "
                    "aggregates remain truthful."
                ),
                (
                    "Zero-byte files are counted only and are not retained "
                    "as normal file rows."
                ),
            ],
        }

    def _record_os_error(
        self,
        state: _IndexState,
        *,
        path: Path,
        scope: str,
        code: str,
        error: OSError,
    ) -> None:
        message = _bounded_message(error, "The path could not be inspected.")
        self._record_inaccessible(
            state,
            path=path,
            error_type=_error_type(error),
            message=message,
        )
        self._record_scan_error(
            state,
            code=code,
            scope=scope,
            path=path,
            message=message,
            recoverable=True,
        )

    @staticmethod
    def _record_inaccessible(
        state: _IndexState,
        *,
        path: Path,
        error_type: str,
        message: str,
    ) -> None:
        state.inaccessible_path_counter += 1
        if len(state.inaccessible_paths) < state.maximum_issue_records:
            state.inaccessible_paths.append(
                {
                    "path": str(path),
                    "error_type": error_type,
                    "message": message[:500],
                }
            )

    @staticmethod
    def _record_scan_error(
        state: _IndexState,
        *,
        code: str,
        scope: str,
        path: Path | None,
        message: str,
        recoverable: bool,
    ) -> None:
        state.scan_error_counter += 1
        if len(state.scan_errors) < state.maximum_issue_records:
            state.scan_errors.append(
                {
                    "code": code,
                    "scope": scope,
                    "path": None if path is None else str(path),
                    "message": message[:500],
                    "recoverable": recoverable,
                }
            )

    def _emit_progress(
        self,
        state: _IndexState,
        current_path: Path,
        callback: Callable[[FileTypeProgress], None],
    ) -> None:
        callback(
            FileTypeProgress(
                current_path=str(current_path),
                files_examined=state.files_examined,
                directories_examined=state.directories_examined,
                logical_bytes_observed=state.logical_bytes_observed,
                matching_files=state.matching_files,
                elapsed_seconds=max(
                    0.0, self._monotonic() - state.started_monotonic
                ),
            )
        )


def resolve_file_type_output_path(
    value: str | None,
    *,
    drive_letter: str,
) -> Path:
    """Keep real indexes inside the ignored local report directory."""

    report_directory = STORAGE_REPORT_DIRECTORY.resolve()
    if value is None:
        filename = f"file-type-index-{drive_letter[0].casefold()}.json"
        return report_directory / filename

    supplied = Path(value).expanduser()
    if supplied.is_absolute():
        output = supplied.resolve()
    else:
        from_current_directory = (Path.cwd() / supplied).resolve()
        if is_path_within(from_current_directory, report_directory):
            output = from_current_directory
        elif supplied.parent == Path("."):
            output = (report_directory / supplied.name).resolve()
        else:
            output = from_current_directory
    if not is_path_within(output, report_directory):
        raise FileTypeIndexConfigurationError(
            "File-type indexes must be written under the ignored "
            f"'{report_directory}' directory."
        )
    if output.suffix.casefold() != ".json":
        raise FileTypeIndexConfigurationError(
            "The output filename must end in .json."
        )
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m storage.file_type_indexer",
        description=(
            "Index one explicitly selected Windows drive for File-Type "
            "Explorer. The scan is local, metadata-only, and read-only."
        ),
    )
    parser.add_argument(
        "--drive",
        required=True,
        help="Explicit drive root to index, for example C:\\.",
    )
    parser.add_argument(
        "--output",
        help=(
            "JSON filename or path under the ignored storage-reports folder."
        ),
    )
    parser.add_argument(
        "--custom-extension",
        action="append",
        default=[],
        help="Optional extension to index in addition to all presets.",
    )
    parser.add_argument(
        "--max-file-details",
        type=int,
        default=100_000,
        help=(
            "Maximum matching file rows retained; exact folder totals are "
            "preserved when details are bounded."
        ),
    )
    parser.add_argument("--max-issue-records", type=int, default=1_000)
    parser.add_argument("--progress-every", type=int, default=1_000)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Atomically replace the selected ignored per-drive index after "
            "the new index validates."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress updates while retaining the final summary.",
    )
    return parser


def _print_progress(update: FileTypeProgress) -> None:
    message = (
        f"Elapsed {update.elapsed_seconds:.1f}s | "
        f"{update.directories_examined} folders | "
        f"{update.files_examined} files | "
        f"{update.matching_files} indexed matches | {update.current_path}"
    )
    print(f"\r{message[:180]:<180}", end="", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    cancellation = threading.Event()

    def request_cancellation(_signal_number, _frame) -> None:
        cancellation.set()

    previous_handler = signal.signal(signal.SIGINT, request_cancellation)
    report: dict[str, Any] | None = None
    try:
        options = FileTypeIndexerOptions(
            custom_extensions=tuple(arguments.custom_extension),
            maximum_file_details=arguments.max_file_details,
            maximum_issue_records=arguments.max_issue_records,
            progress_every_entries=arguments.progress_every,
        )
        indexer = FileTypeIndexer()
        report = indexer.index_drive(
            arguments.drive,
            options=options,
            cancel_check=cancellation.is_set,
            progress_callback=None if arguments.quiet else _print_progress,
        )
        output_path = resolve_file_type_output_path(
            arguments.output,
            drive_letter=report["drive"]["drive_letter"],
        )
        written_path = write_file_type_index(
            report,
            output_path,
            replace_existing=arguments.refresh,
        )
    except (
        FileTypeIndexConfigurationError,
        FileTypeIndexValidationError,
        FileTypeIndexWriteError,
        ValueError,
    ) as error:
        if not arguments.quiet:
            print(file=sys.stderr)
        print(f"File-type index failed: {error}", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    if not arguments.quiet:
        print(file=sys.stderr)
    assert report is not None
    print(f"Index status: {report['scan']['status']}")
    print(f"Folders examined: {report['scan']['directories_examined']}")
    print(f"Files examined: {report['scan']['files_examined']}")
    print(f"Indexed matches: {report['scan']['matching_files']}")
    print(f"Index saved to: {written_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
