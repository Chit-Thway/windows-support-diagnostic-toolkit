"""Resolve, cache, and validate local File-Type Explorer indexes."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from storage.file_type_contract import (
    SUPPORTED_FILE_TYPE_INDEX_VERSION,
    FileTypeIndexValidationError,
    validate_file_type_index,
)

from .report_loader import DEFAULT_REPORT_PATH

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE_TYPE_INDEX_PATH = (
    PROJECT_ROOT / "sample_data" / "sample-file-type-index.json"
)
FILE_TYPE_INDEX_PATH_ENVIRONMENT_VARIABLE = "FILE_TYPE_INDEX_PATH"
MAX_FILE_TYPE_INDEX_BYTES = 256 * 1024 * 1024


class FileTypeIndexLoadError(Exception):
    """Base error displayed by the local File-Type Explorer page."""

    title = "File-Type Explorer index unavailable"
    status_code = 422

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class FileTypeIndexNotFoundError(FileTypeIndexLoadError):
    title = "File-Type Explorer index not found"
    status_code = 404


class MalformedFileTypeIndexError(FileTypeIndexLoadError):
    title = "File-Type Explorer index is not valid JSON"


class FileTypeIndexTooLargeError(FileTypeIndexLoadError):
    title = "File-Type Explorer index is too large"
    status_code = 413


class UnsupportedFileTypeIndexVersionError(FileTypeIndexLoadError):
    title = "Unsupported File-Type Explorer index version"


class FileTypeIndexContractError(FileTypeIndexLoadError):
    title = "File-Type Explorer index does not match the contract"


class FileTypeIndexDriveMismatchError(FileTypeIndexLoadError):
    title = "File-Type Explorer index belongs to another drive"
    status_code = 409


@dataclass(frozen=True)
class FileTypeIndexSnapshot:
    """Validated index plus navigation maps reused by local GET routes."""

    path: Path
    report: dict[str, Any]
    folders_by_id: dict[str, dict[str, Any]]
    children_by_parent: dict[str, tuple[dict[str, Any], ...]]
    root: dict[str, Any]
    indexed_extensions: frozenset[str]


def resolve_file_type_index_paths(
    command_line_paths: str | Path | Iterable[str | Path] | None = None,
    environment: Mapping[str, str] | None = None,
    current_directory: Path | None = None,
    diagnostic_report_path: str | Path | None = None,
) -> tuple[Path, ...]:
    """Resolve CLI, environment, then fictional-sample index precedence."""

    environment = os.environ if environment is None else environment
    current_directory = Path.cwd() if current_directory is None else current_directory

    selected_paths: list[str | Path]
    if isinstance(command_line_paths, (str, Path)):
        selected_paths = [command_line_paths]
    elif command_line_paths is not None:
        selected_paths = list(command_line_paths)
    elif environment.get(FILE_TYPE_INDEX_PATH_ENVIRONMENT_VARIABLE):
        selected_paths = [
            value
            for value in environment[
                FILE_TYPE_INDEX_PATH_ENVIRONMENT_VARIABLE
            ].split(os.pathsep)
            if value
        ]
    elif diagnostic_report_path is None or (
        Path(diagnostic_report_path).resolve() == DEFAULT_REPORT_PATH.resolve()
    ):
        return (DEFAULT_FILE_TYPE_INDEX_PATH.resolve(),)
    else:
        return ()

    resolved: list[Path] = []
    seen: set[str] = set()
    for selected_path in selected_paths:
        path = Path(selected_path).expanduser()
        if not path.is_absolute():
            path = current_directory / path
        path = path.resolve()
        key = os.path.normcase(str(path))
        if key not in seen:
            resolved.append(path)
            seen.add(key)
    return tuple(resolved)


def _read_index(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise FileTypeIndexNotFoundError(
            f"No File-Type Explorer index exists at '{path}'. Generate one "
            "explicitly or select a different index when starting the dashboard."
        ) from error
    if not path.is_file():
        raise FileTypeIndexNotFoundError(
            f"No File-Type Explorer index exists at '{path}'."
        )
    if size > MAX_FILE_TYPE_INDEX_BYTES:
        raise FileTypeIndexTooLargeError(
            "The selected index exceeds the supported 256 MiB display limit. "
            "Generate a bounded index with a smaller --max-file-details value."
        )

    try:
        with path.open("r", encoding="utf-8") as index_file:
            index = json.load(index_file)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MalformedFileTypeIndexError(
            "The selected file could not be parsed as UTF-8 JSON. Generate a "
            "fresh index or choose a valid synthetic fixture."
        ) from error
    except OSError as error:
        raise FileTypeIndexLoadError(
            "The index exists but could not be read. Check its permissions."
        ) from error

    if not isinstance(index, dict):
        raise FileTypeIndexContractError(
            "The File-Type Explorer index root must be a JSON object."
        )
    version = index.get("schema_version")
    if version is not None and version != SUPPORTED_FILE_TYPE_INDEX_VERSION:
        raise UnsupportedFileTypeIndexVersionError(
            "This dashboard supports File-Type Explorer index schema "
            f"{SUPPORTED_FILE_TYPE_INDEX_VERSION}; the selected index uses "
            f"{version!r}."
        )
    try:
        validate_file_type_index(index)
    except FileTypeIndexValidationError as error:
        raise FileTypeIndexContractError(str(error)) from error
    return index


@lru_cache(maxsize=8)
def _load_snapshot_cached(
    path_text: str,
    modified_time_ns: int,
    size: int,
) -> FileTypeIndexSnapshot:
    del modified_time_ns, size
    path = Path(path_text)
    report = _read_index(path)
    folders_by_id = {
        folder["folder_id"]: folder for folder in report["folders"]
    }
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    root: dict[str, Any] | None = None
    for folder in report["folders"]:
        parent_id = folder["parent_id"]
        if parent_id is None:
            root = folder
        else:
            children[parent_id].append(folder)
    assert root is not None
    indexed_extensions = {
        extension
        for group in report["extension_groups"]
        for extension in group["extensions"]
    }
    indexed_extensions.update(report["custom_extensions"])
    return FileTypeIndexSnapshot(
        path=path,
        report=report,
        folders_by_id=folders_by_id,
        children_by_parent={
            parent_id: tuple(folder_rows)
            for parent_id, folder_rows in children.items()
        },
        root=root,
        indexed_extensions=frozenset(indexed_extensions),
    )


def load_file_type_index_snapshot(
    index_path: str | Path,
) -> FileTypeIndexSnapshot:
    """Load a validated snapshot, refreshing the cache after file changes."""

    path = Path(index_path)
    try:
        metadata = path.stat()
    except OSError as error:
        raise FileTypeIndexNotFoundError(
            f"No File-Type Explorer index exists at '{path}'. Generate one "
            "explicitly before opening this cleanup method."
        ) from error
    return _load_snapshot_cached(
        str(path.resolve()), metadata.st_mtime_ns, metadata.st_size
    )


def load_file_type_index(index_path: str | Path) -> dict[str, Any]:
    """Load only the validated public index document."""

    return load_file_type_index_snapshot(index_path).report


def load_file_type_index_for_drive(
    index_paths: Iterable[str | Path],
    drive: str,
) -> FileTypeIndexSnapshot:
    """Select the configured index that belongs to ``drive``."""

    paths = tuple(Path(path) for path in index_paths)
    if not paths:
        raise FileTypeIndexNotFoundError(
            "No File-Type Explorer index is selected."
        )

    first_error: FileTypeIndexLoadError | None = None
    available_drives: list[str] = []
    for path in paths:
        try:
            snapshot = load_file_type_index_snapshot(path)
        except FileTypeIndexLoadError as error:
            if first_error is None:
                first_error = error
            continue
        report_drive = snapshot.report["drive"]["drive_letter"]
        if report_drive == drive:
            return snapshot
        if report_drive not in available_drives:
            available_drives.append(report_drive)

    if available_drives:
        available = ", ".join(available_drives)
        raise FileTypeIndexDriveMismatchError(
            f"No selected File-Type Explorer index is for {drive}. Available "
            f"indexed drive(s): {available}."
        )
    if first_error is not None:
        raise first_error
    raise FileTypeIndexNotFoundError(
        f"No selected File-Type Explorer index is available for {drive}."
    )


def clear_file_type_index_cache() -> None:
    """Clear cached snapshots for deterministic tests and explicit reloads."""

    _load_snapshot_cached.cache_clear()
