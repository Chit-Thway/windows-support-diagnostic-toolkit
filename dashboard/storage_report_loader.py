"""Resolve, load, and validate local storage-analysis reports."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from storage.contract import (
    SUPPORTED_SCHEMA_VERSION,
    StorageReportValidationError,
    validate_storage_report,
)

from .report_loader import DEFAULT_REPORT_PATH

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORAGE_REPORT_PATH = (
    PROJECT_ROOT / "sample_data" / "sample-storage-report.json"
)
STORAGE_REPORT_PATH_ENVIRONMENT_VARIABLE = "STORAGE_REPORT_PATH"
MAX_STORAGE_REPORT_BYTES = 50 * 1024 * 1024


class StorageReportLoadError(Exception):
    """Base error displayed by the local storage dashboard."""

    title = "Storage analysis unavailable"
    status_code = 422

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class StorageReportNotFoundError(StorageReportLoadError):
    title = "Storage analysis not found"
    status_code = 404


class MalformedStorageReportError(StorageReportLoadError):
    title = "Storage analysis is not valid JSON"


class StorageReportTooLargeError(StorageReportLoadError):
    title = "Storage analysis is too large"
    status_code = 413


class UnsupportedStorageSchemaVersionError(StorageReportLoadError):
    title = "Unsupported storage report version"


class StorageReportContractError(StorageReportLoadError):
    title = "Storage analysis does not match the expected contract"


class StorageReportDriveMismatchError(StorageReportLoadError):
    title = "Storage analysis belongs to another drive"
    status_code = 409


def resolve_storage_report_paths(
    command_line_paths: str | Path | Iterable[str | Path] | None = None,
    environment: Mapping[str, str] | None = None,
    current_directory: Path | None = None,
    diagnostic_report_path: str | Path | None = None,
) -> tuple[Path, ...]:
    """Resolve every explicitly selected per-drive storage report."""

    environment = os.environ if environment is None else environment
    current_directory = Path.cwd() if current_directory is None else current_directory

    selected_paths: list[str | Path]
    if isinstance(command_line_paths, (str, Path)):
        selected_paths = [command_line_paths]
    elif command_line_paths is not None:
        selected_paths = list(command_line_paths)
    elif environment.get(STORAGE_REPORT_PATH_ENVIRONMENT_VARIABLE):
        selected_paths = [
            value
            for value in environment[STORAGE_REPORT_PATH_ENVIRONMENT_VARIABLE].split(
                os.pathsep
            )
            if value
        ]
    elif diagnostic_report_path is None or (
        Path(diagnostic_report_path).resolve() == DEFAULT_REPORT_PATH.resolve()
    ):
        return (DEFAULT_STORAGE_REPORT_PATH.resolve(),)
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


def resolve_storage_report_path(
    command_line_path: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    current_directory: Path | None = None,
    diagnostic_report_path: str | Path | None = None,
) -> Path | None:
    """Resolve CLI, environment, then safe sample/no-report behaviour.

    The fictional storage sample is selected only when the fictional default
    diagnostic report is also selected. A custom diagnostic report never
    silently inherits sample storage data.
    """

    paths = resolve_storage_report_paths(
        command_line_path,
        environment=environment,
        current_directory=current_directory,
        diagnostic_report_path=diagnostic_report_path,
    )
    return paths[0] if paths else None


def load_storage_report(report_path: str | Path) -> dict[str, Any]:
    """Read a bounded UTF-8 report and validate storage contract 1.0.0."""

    path = Path(report_path)
    if not path.is_file():
        raise StorageReportNotFoundError(
            f"No storage analysis exists at '{path}'. Generate a local scan "
            "or select a different report when starting the dashboard."
        )

    try:
        with path.open("rb") as report_file:
            report_bytes = report_file.read(MAX_STORAGE_REPORT_BYTES + 1)
    except OSError as error:
        raise StorageReportLoadError(
            "The storage analysis exists but could not be read. Check its "
            "file permissions."
        ) from error

    if len(report_bytes) > MAX_STORAGE_REPORT_BYTES:
        raise StorageReportTooLargeError(
            "The selected storage analysis exceeds the supported 50 MiB "
            "display limit. Generate a report with a smaller retained-candidate limit."
        )

    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MalformedStorageReportError(
            "The selected file could not be parsed as UTF-8 JSON. Generate "
            "a new storage report or choose a valid synthetic fixture."
        ) from error

    if not isinstance(report, dict):
        raise StorageReportContractError(
            "The storage report root must be a JSON object."
        )

    schema_version = report.get("schema_version")
    if schema_version is not None and schema_version != SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedStorageSchemaVersionError(
            f"This dashboard supports storage schema {SUPPORTED_SCHEMA_VERSION}; "
            f"the selected report uses {schema_version!r}."
        )

    try:
        validate_storage_report(report)
    except StorageReportValidationError as error:
        raise StorageReportContractError(str(error)) from error

    return report


def load_storage_report_for_drive(
    report_paths: Iterable[str | Path],
    drive: str,
) -> tuple[dict[str, Any], Path]:
    """Select and load the configured report that belongs to ``drive``."""

    paths = tuple(Path(path) for path in report_paths)
    if not paths:
        raise StorageReportNotFoundError("No storage analysis is selected.")

    first_error: StorageReportLoadError | None = None
    available_drives: list[str] = []
    for path in paths:
        try:
            report = load_storage_report(path)
        except StorageReportLoadError as error:
            if first_error is None:
                first_error = error
            continue

        report_drive = report["drive"]["drive_letter"]
        if report_drive == drive:
            return report, path
        if report_drive not in available_drives:
            available_drives.append(report_drive)

    if available_drives:
        if len(available_drives) == 1:
            raise StorageReportDriveMismatchError(
                f"The selected analysis is for {available_drives[0]}, not "
                f"{drive}. Generate or select an analysis for this drive."
            )
        available = ", ".join(available_drives)
        raise StorageReportDriveMismatchError(
            f"No selected storage analysis is for {drive}. Available selected "
            f"drive report(s): {available}."
        )
    if first_error is not None:
        raise first_error
    raise StorageReportNotFoundError(
        f"No selected storage analysis is available for {drive}."
    )
