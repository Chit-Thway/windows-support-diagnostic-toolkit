from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dashboard.file_type_index_loader import (
    DEFAULT_FILE_TYPE_INDEX_PATH,
    FileTypeIndexContractError,
    FileTypeIndexDriveMismatchError,
    MalformedFileTypeIndexError,
    UnsupportedFileTypeIndexVersionError,
    clear_file_type_index_cache,
    load_file_type_index,
    load_file_type_index_for_drive,
    load_file_type_index_snapshot,
    resolve_file_type_index_paths,
)
from dashboard.report_loader import DEFAULT_REPORT_PATH

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INDEX_FIXTURES = REPOSITORY_ROOT / "tests" / "storage" / "fixtures"


@pytest.fixture(autouse=True)
def clear_snapshot_cache() -> None:
    clear_file_type_index_cache()


def test_default_sample_is_used_only_with_default_diagnostic_report() -> None:
    assert resolve_file_type_index_paths(
        environment={}, diagnostic_report_path=DEFAULT_REPORT_PATH
    ) == (DEFAULT_FILE_TYPE_INDEX_PATH.resolve(),)

    assert resolve_file_type_index_paths(
        environment={}, diagnostic_report_path="tests/fixtures/warning-report.json"
    ) == ()


def test_cli_paths_override_environment_and_are_deduplicated(
    tmp_path: Path,
) -> None:
    resolved = resolve_file_type_index_paths(
        ["c.json", "c.json", "f.json"],
        environment={"FILE_TYPE_INDEX_PATH": "environment.json"},
        current_directory=tmp_path,
        diagnostic_report_path="custom.json",
    )

    assert resolved == (
        (tmp_path / "c.json").resolve(),
        (tmp_path / "f.json").resolve(),
    )


def test_environment_supports_multiple_indexes(tmp_path: Path) -> None:
    resolved = resolve_file_type_index_paths(
        environment={
            "FILE_TYPE_INDEX_PATH": os.pathsep.join(("c.json", "f.json"))
        },
        current_directory=tmp_path,
        diagnostic_report_path="custom.json",
    )

    assert [path.name for path in resolved] == ["c.json", "f.json"]


def test_valid_index_loads_and_builds_navigation_snapshot() -> None:
    path = INDEX_FIXTURES / "complete-file-type-index.json"

    report = load_file_type_index(path)
    snapshot = load_file_type_index_snapshot(path)

    assert report["index_type"] == "file_type_index"
    assert snapshot.root["path"] == "C:\\"
    assert len(snapshot.children_by_parent["folder-root"]) == 3
    assert ".pdf" in snapshot.indexed_extensions


def test_malformed_index_is_rejected() -> None:
    with pytest.raises(MalformedFileTypeIndexError):
        load_file_type_index(
            INDEX_FIXTURES / "malformed-file-type-index.json"
        )


def test_unsupported_version_is_rejected(tmp_path: Path) -> None:
    index = json.loads(
        (INDEX_FIXTURES / "complete-file-type-index.json").read_text(
            encoding="utf-8"
        )
    )
    index["schema_version"] = "2.0.0"
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(UnsupportedFileTypeIndexVersionError):
        load_file_type_index(path)


def test_semantically_invalid_index_is_rejected(tmp_path: Path) -> None:
    index = json.loads(
        (INDEX_FIXTURES / "complete-file-type-index.json").read_text(
            encoding="utf-8"
        )
    )
    index["folders"][0]["recursive_logical_bytes"] += 1
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(FileTypeIndexContractError):
        load_file_type_index(path)


def test_drive_selection_isolated_across_multiple_indexes() -> None:
    snapshot = load_file_type_index_for_drive(
        (
            INDEX_FIXTURES / "complete-file-type-index.json",
            INDEX_FIXTURES / "partial-file-type-index.json",
        ),
        "F:",
    )

    assert snapshot.report["drive"]["drive_letter"] == "F:"


def test_drive_mismatch_is_friendly() -> None:
    with pytest.raises(
        FileTypeIndexDriveMismatchError, match="Available indexed drive"
    ):
        load_file_type_index_for_drive(
            (INDEX_FIXTURES / "complete-file-type-index.json",), "F:"
        )
