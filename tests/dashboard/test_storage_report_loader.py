from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.report_loader import DEFAULT_REPORT_PATH
from dashboard.storage_report_loader import (
    DEFAULT_STORAGE_REPORT_PATH,
    MalformedStorageReportError,
    StorageReportContractError,
    UnsupportedStorageSchemaVersionError,
    load_storage_report,
    resolve_storage_report_path,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STORAGE_FIXTURES = REPOSITORY_ROOT / "tests" / "storage" / "fixtures"


def test_default_sample_is_used_only_with_default_diagnostic_report() -> None:
    assert resolve_storage_report_path(
        environment={}, diagnostic_report_path=DEFAULT_REPORT_PATH
    ) == DEFAULT_STORAGE_REPORT_PATH.resolve()

    assert resolve_storage_report_path(
        environment={}, diagnostic_report_path="tests/fixtures/healthy-report.json"
    ) is None


def test_storage_report_path_precedence(tmp_path: Path) -> None:
    resolved = resolve_storage_report_path(
        "cli.json",
        environment={"STORAGE_REPORT_PATH": "environment.json"},
        current_directory=tmp_path,
        diagnostic_report_path="custom-diagnostic.json",
    )

    assert resolved == (tmp_path / "cli.json").resolve()


def test_storage_report_environment_path_is_supported(tmp_path: Path) -> None:
    resolved = resolve_storage_report_path(
        environment={"STORAGE_REPORT_PATH": "environment.json"},
        current_directory=tmp_path,
        diagnostic_report_path="custom-diagnostic.json",
    )

    assert resolved == (tmp_path / "environment.json").resolve()


def test_valid_storage_report_loads() -> None:
    report = load_storage_report(STORAGE_FIXTURES / "healthy-storage-report.json")

    assert report["report_type"] == "storage_analysis"
    assert report["schema_version"] == "1.0.0"


def test_malformed_storage_report_is_rejected() -> None:
    with pytest.raises(MalformedStorageReportError):
        load_storage_report(STORAGE_FIXTURES / "malformed-storage-report.json")


def test_unsupported_storage_schema_is_rejected(tmp_path: Path) -> None:
    report = json.loads(
        (STORAGE_FIXTURES / "healthy-storage-report.json").read_text(
            encoding="utf-8"
        )
    )
    report["schema_version"] = "2.0.0"
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(UnsupportedStorageSchemaVersionError):
        load_storage_report(path)


def test_semantically_invalid_storage_report_is_rejected(tmp_path: Path) -> None:
    report = json.loads(
        (STORAGE_FIXTURES / "healthy-storage-report.json").read_text(
            encoding="utf-8"
        )
    )
    report["drive"]["used_bytes"] += 1
    path = tmp_path / "invalid-accounting.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(StorageReportContractError):
        load_storage_report(path)
