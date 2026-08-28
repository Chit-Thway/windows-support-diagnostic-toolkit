from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from storage.contract import StorageReportValidationError, validate_storage_report

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIRECTORY = Path(__file__).resolve().parent / "fixtures"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

VALID_FIXTURES = (
    "healthy-storage-report.json",
    "low-space-storage-report.json",
    "candidate-attributes-storage-report.json",
    "partial-storage-report.json",
)

EXPECTED_CATEGORIES = {
    "free_space",
    "protected_system",
    "installed_applications",
    "user_content",
    "development_tools_and_caches",
    "other_or_unreadable",
}

EXPECTED_ATTRIBUTES = {
    "stale",
    "likely_incomplete",
    "large",
    "empty",
    "temporary",
    "development_cache",
    "protected",
    "unavailable",
}


def assert_schema_valid(report: dict, validator: Draft202012Validator) -> None:
    errors = sorted(
        validator.iter_errors(report),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    assert not errors, "\n".join(
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: "
        f"{error.message}"
        for error in errors
    )


def assert_candidate_accounting(report: dict) -> None:
    candidates = report["candidates"]
    summary = report["candidate_summary"]
    scan = report["scan"]

    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    assert len(candidate_ids) == len(set(candidate_ids))

    retained_bytes = sum(
        candidate["size_bytes"] or 0 for candidate in candidates
    )
    assert summary["retained_candidates"] == len(candidates)
    assert summary["retained_unique_candidate_bytes"] == retained_bytes
    assert summary["total_unique_candidates"] == (
        summary["retained_candidates"] + summary["omitted_candidates"]
    )
    assert summary["total_unique_candidate_bytes"] >= retained_bytes
    assert scan["candidate_details_retained"] == summary["retained_candidates"]
    assert scan["candidate_details_omitted"] == summary["omitted_candidates"]

    for candidate in candidates:
        attributes = set(candidate["attributes"])
        evidence_attributes = {
            evidence["attribute"] for evidence in candidate["evidence"]
        }
        assert attributes == evidence_attributes

        eligibility = candidate["protection"]["eligibility"]
        if eligibility == "eligible":
            assert attributes.isdisjoint({"protected", "unavailable"})
            assert candidate["is_regular_file"] is True
            assert candidate["is_reparse_point"] is False
        elif eligibility == "protected":
            assert "protected" in attributes
        else:
            assert "unavailable" in attributes

    if summary["omitted_candidates"] == 0:
        for attribute in EXPECTED_ATTRIBUTES:
            matching_candidates = [
                candidate
                for candidate in candidates
                if attribute in candidate["attributes"]
            ]
            attribute_summary = summary["attributes"][attribute]
            assert attribute_summary["candidate_count"] == len(
                matching_candidates
            )
            assert attribute_summary["unique_bytes"] == sum(
                candidate["size_bytes"] or 0
                for candidate in matching_candidates
            )


def iter_path_values(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {
                "path",
                "scan_root",
                "requested_path",
                "canonical_path",
            } and isinstance(child, str):
                yield child
            yield from iter_path_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_path_values(child)


def test_schema_is_valid_draft_2020_12(storage_schema: dict) -> None:
    Draft202012Validator.check_schema(storage_schema)

    assert storage_schema["properties"]["schema_version"]["const"] == "1.0.0"
    assert (
        storage_schema["properties"]["report_type"]["const"]
        == "storage_analysis"
    )
    assert storage_schema["$id"].endswith("/storage-report.schema.json")


@pytest.mark.parametrize("fixture_name", VALID_FIXTURES)
def test_valid_fixture_matches_schema(
    fixture_name: str,
    storage_fixture,
    storage_validator: Draft202012Validator,
) -> None:
    assert_schema_valid(storage_fixture(fixture_name), storage_validator)


def test_public_sample_matches_schema(
    storage_validator: Draft202012Validator,
) -> None:
    sample = read_json(
        REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
    )

    assert_schema_valid(sample, storage_validator)


def test_public_sample_contains_valid_non_summed_folder_candidates() -> None:
    sample = read_json(
        REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
    )

    validate_storage_report(sample)
    summary = sample["folder_candidate_summary"]
    folders = sample["folder_candidates"]
    assert summary["accounting_method"] == "overlapping_hierarchy"
    assert summary["retained_candidates"] == len(folders)
    assert all(candidate["item_type"] == "folder" for candidate in folders)
    assert all(candidate["is_directory"] is True for candidate in folders)
    assert any("empty" in candidate["attributes"] for candidate in folders)


def test_malformed_fixture_is_rejected_by_json_parser() -> None:
    malformed_path = (
        FIXTURES_DIRECTORY / "malformed-storage-report.json"
    )

    with pytest.raises(json.JSONDecodeError):
        read_json(malformed_path)


def test_unsupported_version_is_rejected(
    storage_fixture,
    storage_validator: Draft202012Validator,
) -> None:
    report = storage_fixture("healthy-storage-report.json")
    report["schema_version"] = "2.0.0"

    errors = list(storage_validator.iter_errors(report))

    assert any(
        list(error.absolute_path) == ["schema_version"] for error in errors
    )


@pytest.mark.parametrize("fixture_name", VALID_FIXTURES)
def test_drive_and_non_overlapping_category_accounting(
    fixture_name: str,
    storage_fixture,
) -> None:
    report = storage_fixture(fixture_name)
    drive = report["drive"]
    categories = report["accounting"]["categories"]

    assert set(categories) == EXPECTED_CATEGORIES
    assert drive["total_bytes"] == drive["used_bytes"] + drive["free_bytes"]
    assert categories["free_space"]["bytes"] == drive["free_bytes"]

    used_category_bytes = sum(
        category["bytes"]
        for name, category in categories.items()
        if name != "free_space"
    )
    assert used_category_bytes == drive["used_bytes"]
    assert sum(category["bytes"] for category in categories.values()) == drive[
        "total_bytes"
    ]

    calculated_percent_free = (
        drive["free_bytes"] / drive["total_bytes"] * 100
    )
    assert drive["percent_free"] == pytest.approx(
        calculated_percent_free, abs=0.01
    )


@pytest.mark.parametrize("fixture_name", VALID_FIXTURES)
def test_candidate_accounting_uses_unique_candidate_identity(
    fixture_name: str,
    storage_fixture,
) -> None:
    assert_candidate_accounting(storage_fixture(fixture_name))


def test_overlapping_attributes_do_not_inflate_unique_candidate_bytes(
    storage_fixture,
) -> None:
    report = storage_fixture("candidate-attributes-storage-report.json")
    summary = report["candidate_summary"]
    attribute_byte_total = sum(
        value["unique_bytes"] for value in summary["attributes"].values()
    )

    assert summary["total_unique_candidate_bytes"] == 19000000000
    assert attribute_byte_total > summary["total_unique_candidate_bytes"]
    assert any(
        {"stale", "likely_incomplete"}.issubset(candidate["attributes"])
        for candidate in report["candidates"]
    )


def test_duplicate_candidate_ids_fail_accounting_invariant(
    storage_fixture,
) -> None:
    report = storage_fixture("candidate-attributes-storage-report.json")
    report["candidates"][1]["candidate_id"] = report["candidates"][0][
        "candidate_id"
    ]

    with pytest.raises(AssertionError):
        assert_candidate_accounting(report)


def test_duplicate_candidate_paths_are_rejected(storage_fixture) -> None:
    report = storage_fixture("candidate-attributes-storage-report.json")
    report["candidates"][1]["path"] = report["candidates"][0]["path"]

    with pytest.raises(
        StorageReportValidationError,
        match="candidate paths must be unique",
    ):
        validate_storage_report(report)


def test_inflated_retained_bytes_fail_accounting_invariant(
    storage_fixture,
) -> None:
    report = storage_fixture("candidate-attributes-storage-report.json")
    report["candidate_summary"]["retained_unique_candidate_bytes"] += 1

    with pytest.raises(AssertionError):
        assert_candidate_accounting(report)


def test_bounded_fixture_retains_truthful_aggregate_totals(
    storage_fixture,
) -> None:
    report = storage_fixture("low-space-storage-report.json")
    summary = report["candidate_summary"]

    assert report["scan"]["detail_coverage"] == "bounded"
    assert summary["omitted_candidates"] == 2
    assert summary["total_unique_candidates"] == 5
    assert (
        summary["total_unique_candidate_bytes"]
        > summary["retained_unique_candidate_bytes"]
    )


def test_partial_fixture_preserves_structured_errors(
    storage_fixture,
) -> None:
    report = storage_fixture("partial-storage-report.json")

    assert report["scan"]["status"] == "partial"
    assert report["scan"]["aggregate_coverage"] == "partial"
    assert report["scan_errors"]
    assert report["inaccessible_paths"]
    assert report["candidates"][0]["protection"]["eligibility"] == "unavailable"


def test_bounded_issue_detail_counts_match_retained_records(
    storage_fixture,
) -> None:
    report = storage_fixture("partial-storage-report.json")
    report["scan"].update(
        inaccessible_path_details_retained=len(report["inaccessible_paths"]),
        inaccessible_path_details_omitted=3,
        scan_error_details_retained=len(report["scan_errors"]),
        scan_error_details_omitted=3,
    )

    validate_storage_report(report)

    report["scan"]["scan_error_details_retained"] += 1
    with pytest.raises(
        StorageReportValidationError,
        match="issue-detail counts",
    ):
        validate_storage_report(report)


def test_complete_report_cannot_hide_omitted_issue_details(storage_fixture) -> None:
    report = storage_fixture("healthy-storage-report.json")
    report["scan"].update(
        inaccessible_path_details_retained=0,
        inaccessible_path_details_omitted=1,
        scan_error_details_retained=0,
        scan_error_details_omitted=1,
    )

    with pytest.raises(
        StorageReportValidationError,
        match="complete scan cannot omit",
    ):
        validate_storage_report(report)


@pytest.mark.parametrize(
    "fixture_path",
    [
        *(FIXTURES_DIRECTORY / name for name in VALID_FIXTURES),
        REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json",
    ],
)
def test_public_fixture_paths_and_values_are_fictional(
    fixture_path: Path,
) -> None:
    report = read_json(fixture_path)
    serialized = json.dumps(report).lower()

    assert "tarye" not in serialized
    assert "chit-thway" not in serialized
    assert "f:\\support-diagnostic-toolkit" not in serialized

    paths = list(iter_path_values(report))
    assert paths
    for path in paths:
        if path.lower().startswith("c:\\users\\"):
            assert "fictional." in path.lower()


@pytest.mark.parametrize("fixture_name", VALID_FIXTURES)
def test_last_access_is_never_classification_evidence(
    fixture_name: str,
    storage_fixture,
) -> None:
    report = storage_fixture(fixture_name)

    assert (
        report["scan_scope"]["options"][
            "use_last_access_as_classification_evidence"
        ]
        is False
    )
    for candidate in report["candidates"]:
        assert all(
            evidence["code"] != "last_access_before_cutoff"
            for evidence in candidate["evidence"]
        )


def test_public_sample_development_insights_are_informational_and_fictional() -> None:
    sample = read_json(
        REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
    )
    insights = sample["development_insights"]

    assert insights["locations"]
    assert all(
        location["automatic_cleanup_candidate"] is False
        for location in insights["locations"]
    )
    assert any(
        location["suggested_command"] == "python -m pip cache purge"
        for location in insights["locations"]
    )
    assert "fictional" in json.dumps(insights).lower()


def test_candidate_inside_informational_development_location_is_rejected() -> None:
    sample = read_json(
        REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
    )
    candidate = sample["candidates"][0]
    location = sample["development_insights"]["locations"][2]
    location["path"] = candidate["scan_root"]
    location["within_scan_scope"] = True
    location["files_observed"] = 1
    location["bytes_observed"] = candidate["size_bytes"]
    location["measurement"] = "observed_selected_roots"
    location["coverage"] = "complete"

    with pytest.raises(
        StorageReportValidationError,
        match="cannot contain cleanup candidates",
    ):
        validate_storage_report(sample)
