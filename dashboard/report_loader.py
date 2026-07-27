"""Load and validate local diagnostic reports."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_PATH = PROJECT_ROOT / "sample_data" / "sample-report.json"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schema" / "report.schema.json"
REPORT_PATH_ENVIRONMENT_VARIABLE = "DIAGNOSTIC_REPORT_PATH"
SUPPORTED_SCHEMA_VERSION = "1.0.0"


class ReportLoadError(Exception):
    """Base error displayed by the local dashboard."""

    title = "Report unavailable"
    status_code = 422

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ReportNotFoundError(ReportLoadError):
    title = "Report file not found"
    status_code = 404


class MalformedReportError(ReportLoadError):
    title = "Report is not valid JSON"
    status_code = 422


class UnsupportedSchemaVersionError(ReportLoadError):
    title = "Unsupported report version"
    status_code = 422


class ReportValidationError(ReportLoadError):
    title = "Report does not match the expected contract"
    status_code = 422


def resolve_report_path(
    command_line_path: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    current_directory: Path | None = None,
) -> Path:
    """Resolve CLI, environment, then default report path precedence."""

    environment = os.environ if environment is None else environment
    current_directory = Path.cwd() if current_directory is None else current_directory

    selected_path: str | Path
    if command_line_path:
        selected_path = command_line_path
    elif environment.get(REPORT_PATH_ENVIRONMENT_VARIABLE):
        selected_path = environment[REPORT_PATH_ENVIRONMENT_VARIABLE]
    else:
        return DEFAULT_REPORT_PATH.resolve()

    path = Path(selected_path).expanduser()
    if not path.is_absolute():
        path = current_directory / path
    return path.resolve()


@lru_cache(maxsize=4)
def _load_schema(schema_path: str) -> dict[str, Any]:
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReportValidationError(
            "The local JSON Schema could not be read. Recheck the project files."
        ) from error

    Draft202012Validator.check_schema(schema)
    return schema


def _format_validation_location(error: Any) -> str:
    if not error.absolute_path:
        return "the report root"
    return ".".join(str(part) for part in error.absolute_path)


def load_report(
    report_path: str | Path,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
) -> dict[str, Any]:
    """Read a local report and enforce schema version 1.0.0."""

    path = Path(report_path)
    if not path.is_file():
        raise ReportNotFoundError(
            f"No report exists at '{path}'. Check the --report path or "
            f"{REPORT_PATH_ENVIRONMENT_VARIABLE}."
        )

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MalformedReportError(
            "The selected file could not be parsed as UTF-8 JSON. "
            "Generate a new report or choose a valid fixture."
        ) from error
    except OSError as error:
        raise ReportLoadError(
            "The report exists but could not be read. Check file permissions."
        ) from error

    if not isinstance(report, dict):
        raise ReportValidationError("The report root must be a JSON object.")

    schema_version = report.get("schema_version")
    if schema_version is not None and schema_version != SUPPORTED_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"This dashboard supports schema {SUPPORTED_SCHEMA_VERSION}; "
            f"the selected report uses {schema_version!r}."
        )

    schema = _load_schema(str(Path(schema_path).resolve()))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validation_errors = sorted(
        validator.iter_errors(report),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if validation_errors:
        first_error = validation_errors[0]
        location = _format_validation_location(first_error)
        raise ReportValidationError(
            f"Validation failed at {location}: {first_error.message}"
        )

    return report
