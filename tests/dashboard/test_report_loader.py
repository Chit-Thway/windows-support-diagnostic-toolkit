from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.report_loader import (
    DEFAULT_REPORT_PATH,
    MalformedReportError,
    ReportNotFoundError,
    ReportValidationError,
    UnsupportedSchemaVersionError,
    load_report,
    resolve_report_path,
)


def test_valid_healthy_report_loads() -> None:
    report = load_report("tests/fixtures/healthy-report.json")

    assert report["schema_version"] == "1.0.0"
    assert report["collection_summary"]["status"] == "complete"


def test_malformed_report_is_rejected() -> None:
    with pytest.raises(MalformedReportError):
        load_report("tests/fixtures/malformed-report.json")


def test_missing_report_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReportNotFoundError):
        load_report(tmp_path / "does-not-exist.json")


def test_unsupported_schema_version_has_specific_error(
    fixture_data, write_report
) -> None:
    report = fixture_data("healthy-report.json")
    report["schema_version"] = "2.0.0"

    with pytest.raises(UnsupportedSchemaVersionError):
        load_report(write_report(report))


def test_empty_service_list_fails_schema_validation(
    fixture_data, write_report
) -> None:
    report = fixture_data("healthy-report.json")
    report["services"] = []

    with pytest.raises(ReportValidationError):
        load_report(write_report(report))


def test_missing_required_section_fails_gracefully(
    fixture_data, write_report
) -> None:
    report = fixture_data("healthy-report.json")
    del report["network"]

    with pytest.raises(ReportValidationError):
        load_report(write_report(report))


def test_non_object_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "array.json"
    path.write_text(json.dumps([]), encoding="utf-8")

    with pytest.raises(ReportValidationError):
        load_report(path)


def test_command_line_path_has_highest_precedence(tmp_path: Path) -> None:
    cli_path = tmp_path / "cli.json"
    environment_path = tmp_path / "environment.json"

    result = resolve_report_path(
        cli_path,
        environment={"DIAGNOSTIC_REPORT_PATH": str(environment_path)},
        current_directory=tmp_path,
    )

    assert result == cli_path.resolve()


def test_environment_path_precedes_default(tmp_path: Path) -> None:
    environment_path = tmp_path / "environment.json"

    result = resolve_report_path(
        environment={"DIAGNOSTIC_REPORT_PATH": str(environment_path)},
        current_directory=tmp_path,
    )

    assert result == environment_path.resolve()


def test_default_path_points_to_synthetic_sample() -> None:
    result = resolve_report_path(environment={})

    assert result == DEFAULT_REPORT_PATH.resolve()
    assert result.name == "sample-report.json"
