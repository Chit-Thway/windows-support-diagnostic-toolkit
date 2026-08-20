from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from storage.file_type_contract import (
    PRESET_EXTENSION_GROUPS,
    FileTypeIndexValidationError,
    validate_file_type_index,
    validate_non_overlapping_scopes,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIRECTORY = Path(__file__).resolve().parent / "fixtures"
SCHEMA_PATH = REPOSITORY_ROOT / "schema" / "file-type-index.schema.json"
SAMPLE_PATH = REPOSITORY_ROOT / "sample_data" / "sample-file-type-index.json"
VALID_FIXTURES = (
    "complete-file-type-index.json",
    "partial-file-type-index.json",
    "bounded-file-type-index.json",
)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema() -> dict:
    return read_json(SCHEMA_PATH)


@pytest.fixture(scope="module")
def validator(schema: dict) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.fixture
def complete_index() -> dict:
    return copy.deepcopy(
        read_json(FIXTURES_DIRECTORY / "complete-file-type-index.json")
    )


def assert_schema_valid(
    index: dict, validator: Draft202012Validator
) -> None:
    errors = sorted(
        validator.iter_errors(index),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    assert not errors, "\n".join(
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: "
        f"{error.message}"
        for error in errors
    )


def test_schema_declares_independent_versioned_contract(schema: dict) -> None:
    assert schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert schema["properties"]["index_type"]["const"] == "file_type_index"
    assert schema["$id"].endswith("/file-type-index.schema.json")


@pytest.mark.parametrize("fixture_name", VALID_FIXTURES)
def test_synthetic_fixtures_match_schema_and_semantics(
    fixture_name: str, validator: Draft202012Validator
) -> None:
    index = read_json(FIXTURES_DIRECTORY / fixture_name)

    assert_schema_valid(index, validator)
    validate_file_type_index(index)


def test_public_sample_matches_contract(
    validator: Draft202012Validator,
) -> None:
    sample = read_json(SAMPLE_PATH)

    assert_schema_valid(sample, validator)
    validate_file_type_index(sample)


def test_malformed_fixture_is_rejected_by_parser() -> None:
    with pytest.raises(json.JSONDecodeError):
        read_json(FIXTURES_DIRECTORY / "malformed-file-type-index.json")


@pytest.mark.parametrize(
    "path",
    [
        *(FIXTURES_DIRECTORY / name for name in VALID_FIXTURES),
        SAMPLE_PATH,
    ],
)
def test_public_values_are_fictional(path: Path) -> None:
    serialized = path.read_text(encoding="utf-8").casefold()

    assert "tarye" not in serialized
    assert "chit-thway" not in serialized
    assert "f:\\support-diagnostic-toolkit" not in serialized
    assert "fictional" in serialized


def test_complete_versioned_preset_catalog_is_declared(
    complete_index: dict,
) -> None:
    actual = {
        group["group_id"]: (group["label"], tuple(group["extensions"]))
        for group in complete_index["extension_groups"]
    }

    assert actual == PRESET_EXTENSION_GROUPS


def test_tree_supports_total_and_filtered_size_views(
    complete_index: dict,
) -> None:
    folders = {
        folder["path"]: folder for folder in complete_index["folders"]
    }
    root = folders["C:\\"]
    users = folders["C:\\Users"]
    downloads = folders["C:\\Users\\fictional.user\\Downloads"]

    assert root["recursive_logical_bytes"] == 70782976
    assert users["recursive_logical_bytes"] == 69210112
    pdf_by_folder = {
        folder["path"]: next(
            (
                total["recursive_logical_bytes"]
                for total in folder["extension_totals"]
                if total["extension"] == ".pdf"
            ),
            0,
        )
        for folder in complete_index["folders"]
    }
    assert pdf_by_folder["C:\\"] == 15204352
    assert pdf_by_folder["C:\\Users"] == 13631488
    assert pdf_by_folder[downloads["path"]] == 10485760


def test_zero_byte_files_are_summarized_not_retained(
    complete_index: dict,
) -> None:
    summary = complete_index["empty_summary"]

    assert complete_index["scan"]["zero_byte_files"] == 1
    assert summary["zero_byte_files"] == 1
    assert summary["collapsed_tree_count"] == 1
    assert summary["trees"][0]["path"].endswith("EmptyArchive")
    assert summary["trees"][0]["recoverable_bytes"] == 0
    assert all(file["size_bytes"] > 0 for file in complete_index["files"])


def test_long_hash_like_custom_extension_remains_valid(
    complete_index: dict,
) -> None:
    extension = complete_index["custom_extensions"][0]

    assert len(extension) == 65
    assert any(file["extension"] == extension for file in complete_index["files"])


def test_partial_index_preserves_drive_isolation_and_errors() -> None:
    partial = read_json(FIXTURES_DIRECTORY / "partial-file-type-index.json")

    assert partial["drive"]["drive_letter"] == "F:"
    assert partial["scope"]["root_path"] == "F:\\"
    assert partial["scan"]["status"] == "partial"
    assert partial["scan"]["aggregate_coverage"] == "partial"
    assert partial["inaccessible_paths"]
    assert partial["scan_errors"]


def test_bounded_details_preserve_exact_aggregate_totals() -> None:
    bounded = read_json(FIXTURES_DIRECTORY / "bounded-file-type-index.json")

    assert bounded["scan"]["matching_files"] == 3
    assert bounded["file_detail_summary"] == {
        "coverage": "bounded",
        "retained_files": 1,
        "omitted_files": 2,
        "retained_logical_bytes": 300,
        "omitted_logical_bytes": 300,
    }


def test_duplicate_preset_extension_is_rejected(complete_index: dict) -> None:
    complete_index["extension_groups"][1]["extensions"].append(".pdf")

    with pytest.raises(
        FileTypeIndexValidationError,
        match="more than one preset group",
    ):
        validate_file_type_index(complete_index)


def test_recursive_folder_accounting_mismatch_is_rejected(
    complete_index: dict,
) -> None:
    complete_index["folders"][0]["recursive_logical_bytes"] += 1

    with pytest.raises(
        FileTypeIndexValidationError,
        match="Recursive folder bytes",
    ):
        validate_file_type_index(complete_index)


def test_recursive_extension_accounting_mismatch_is_rejected(
    complete_index: dict,
) -> None:
    complete_index["folders"][0]["extension_totals"][0][
        "recursive_logical_bytes"
    ] += 1

    with pytest.raises(
        FileTypeIndexValidationError,
        match="Recursive extension bytes",
    ):
        validate_file_type_index(complete_index)


def test_file_extension_must_match_its_path(complete_index: dict) -> None:
    complete_index["files"][0]["extension"] = ".docx"

    with pytest.raises(
        FileTypeIndexValidationError,
        match="extensions must match their paths",
    ):
        validate_file_type_index(complete_index)


def test_protected_folder_cannot_contain_selectable_file(
    complete_index: dict,
) -> None:
    protected = next(
        file
        for file in complete_index["files"]
        if file["folder_id"] == "folder-program-files"
    )
    protected["selection_state"] = "selectable"

    with pytest.raises(
        FileTypeIndexValidationError,
        match="must remain protected",
    ):
        validate_file_type_index(complete_index)


def test_complete_index_cannot_hide_collection_errors(
    complete_index: dict,
) -> None:
    complete_index["scan_errors"].append(
        {
            "code": "synthetic_error",
            "scope": "file",
            "path": "C:\\fictional.txt",
            "message": "A fictional error occurred.",
            "recoverable": True,
        }
    )
    complete_index["scan"]["scan_error_details_retained"] = 1

    with pytest.raises(
        FileTypeIndexValidationError,
        match="complete scan requires exact aggregates and no collection errors",
    ):
        validate_file_type_index(complete_index)


def test_empty_summary_must_point_to_highest_folder(
    complete_index: dict,
) -> None:
    complete_index["empty_summary"]["trees"][0][
        "highest_folder_id"
    ] = "folder-empty-leaf"

    with pytest.raises(
        FileTypeIndexValidationError,
        match="paths must match their highest folders",
    ):
        validate_file_type_index(complete_index)


def test_sibling_scopes_are_allowed() -> None:
    scopes = validate_non_overlapping_scopes(
        [
            "C:\\Users\\fictional.user\\Downloads",
            "C:\\Users\\fictional.user\\Documents",
        ],
        "C:\\",
    )

    assert len(scopes) == 2


def test_parent_and_child_scopes_are_rejected() -> None:
    with pytest.raises(
        FileTypeIndexValidationError,
        match="Parent and child",
    ):
        validate_non_overlapping_scopes(
            [
                "C:\\Users\\fictional.user",
                "C:\\Users\\fictional.user\\Downloads",
            ],
            "C:\\",
        )


def test_scope_from_another_drive_is_rejected() -> None:
    with pytest.raises(
        FileTypeIndexValidationError,
        match="active indexed drive",
    ):
        validate_non_overlapping_scopes(["F:\\Media"], "C:\\")
