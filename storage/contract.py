"""Storage report validation and UTF-8 JSON export."""

from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from .path_policy import is_path_within

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schema" / "storage-report.schema.json"
SUPPORTED_SCHEMA_VERSION = "1.0.0"


class StorageReportValidationError(ValueError):
    """Raised when a report does not satisfy schema or accounting rules."""


class StorageReportWriteError(OSError):
    """Raised when a validated report cannot be written safely."""


@lru_cache(maxsize=4)
def _load_schema(schema_path: str) -> dict[str, Any]:
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StorageReportValidationError(
            "The storage report schema could not be read."
        ) from error

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise StorageReportValidationError(
            "The storage report schema is invalid."
        ) from error
    return schema


def _validation_location(error: Any) -> str:
    if not error.absolute_path:
        return "the report root"
    return ".".join(str(part) for part in error.absolute_path)


def _validate_semantics(report: dict[str, Any]) -> None:
    drive = report["drive"]
    if drive["total_bytes"] != drive["used_bytes"] + drive["free_bytes"]:
        raise StorageReportValidationError(
            "Drive total_bytes must equal used_bytes plus free_bytes."
        )

    calculated_percent = drive["free_bytes"] / drive["total_bytes"] * 100
    if not math.isclose(
        drive["percent_free"], calculated_percent, abs_tol=0.01
    ):
        raise StorageReportValidationError(
            "Drive percent_free does not match the capacity byte values."
        )

    categories = report["accounting"]["categories"]
    if categories["free_space"]["bytes"] != drive["free_bytes"]:
        raise StorageReportValidationError(
            "The free_space category must equal drive free_bytes."
        )
    used_category_bytes = sum(
        category["bytes"]
        for name, category in categories.items()
        if name != "free_space"
    )
    if used_category_bytes != drive["used_bytes"]:
        raise StorageReportValidationError(
            "Non-free categories must equal drive used_bytes without overlap."
        )

    candidates = report["candidates"]
    summary = report["candidate_summary"]
    scan = report["scan"]
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise StorageReportValidationError(
            "Retained candidate_id values must be unique."
        )
    candidate_paths = [
        os.path.normcase(os.path.abspath(candidate["path"]))
        for candidate in candidates
    ]
    if len(candidate_paths) != len(set(candidate_paths)):
        raise StorageReportValidationError(
            "Retained candidate paths must be unique."
        )

    retained_bytes = sum(
        candidate["size_bytes"] or 0 for candidate in candidates
    )
    retained_allocated_bytes = sum(
        candidate.get("allocated_size_bytes") or 0 for candidate in candidates
    )
    if summary["retained_candidates"] != len(candidates):
        raise StorageReportValidationError(
            "retained_candidates does not match the candidate rows."
        )
    if summary["retained_unique_candidate_bytes"] != retained_bytes:
        raise StorageReportValidationError(
            "retained_unique_candidate_bytes does not match retained rows."
        )
    if "retained_unique_candidate_allocated_bytes" in summary and (
        summary["retained_unique_candidate_allocated_bytes"]
        != retained_allocated_bytes
    ):
        raise StorageReportValidationError(
            "retained_unique_candidate_allocated_bytes does not match retained rows."
        )
    if summary["total_unique_candidates"] != (
        summary["retained_candidates"] + summary["omitted_candidates"]
    ):
        raise StorageReportValidationError(
            "Total candidate count must equal retained plus omitted candidates."
        )
    if summary["total_unique_candidate_bytes"] < retained_bytes:
        raise StorageReportValidationError(
            "Total unique candidate bytes cannot be below retained bytes."
        )
    if (
        "total_unique_candidate_allocated_bytes" in summary
        and summary["total_unique_candidate_allocated_bytes"]
        < retained_allocated_bytes
    ):
        raise StorageReportValidationError(
            "Total allocated candidate bytes cannot be below retained allocated bytes."
        )
    if scan["candidate_details_retained"] != summary["retained_candidates"]:
        raise StorageReportValidationError(
            "Scan and summary retained-candidate counts do not match."
        )
    if scan["candidate_details_omitted"] != summary["omitted_candidates"]:
        raise StorageReportValidationError(
            "Scan and summary omitted-candidate counts do not match."
        )

    issue_detail_fields = (
        (
            "inaccessible_path_details_retained",
            "inaccessible_path_details_omitted",
            report["inaccessible_paths"],
        ),
        (
            "scan_error_details_retained",
            "scan_error_details_omitted",
            report["scan_errors"],
        ),
    )
    for retained_name, omitted_name, records in issue_detail_fields:
        if retained_name in scan or omitted_name in scan:
            retained = scan.get(retained_name)
            omitted = scan.get(omitted_name)
            if retained != len(records) or omitted is None:
                raise StorageReportValidationError(
                    "Scan issue-detail counts do not match retained records."
                )
            if omitted and scan["status"] == "complete":
                raise StorageReportValidationError(
                    "A complete scan cannot omit issue-detail records."
                )

    retained_attribute_counts = {
        attribute: 0 for attribute in summary["attributes"]
    }
    retained_attribute_bytes = {
        attribute: 0 for attribute in summary["attributes"]
    }
    for candidate in candidates:
        if candidate.get("item_type", "file") != "file":
            raise StorageReportValidationError(
                "The candidates collection can contain files only."
            )
        attributes = set(candidate["attributes"])
        evidence_attributes = {
            evidence["attribute"] for evidence in candidate["evidence"]
        }
        if attributes != evidence_attributes:
            raise StorageReportValidationError(
                f"Candidate {candidate['candidate_id']} must contain evidence "
                "for exactly its listed attributes."
            )

        eligibility = candidate["protection"]["eligibility"]
        removal_risk = candidate.get(
            "removal_risk",
            "protected"
            if eligibility in {"protected", "unavailable"}
            else "medium",
        )
        if eligibility == "eligible":
            if attributes.intersection({"protected", "unavailable"}):
                raise StorageReportValidationError(
                    "Eligible candidates cannot be protected or unavailable."
                )
            if not candidate["is_regular_file"]:
                raise StorageReportValidationError(
                    "Eligible candidates must be regular files."
                )
            if candidate["is_reparse_point"]:
                raise StorageReportValidationError(
                    "Reparse-point candidates cannot be eligible."
                )
            if removal_risk in {"high", "protected"}:
                raise StorageReportValidationError(
                    "High-risk or protected candidates cannot be cleanup eligible."
                )
        elif eligibility == "review_only":
            if not candidate["is_regular_file"] or candidate["is_reparse_point"]:
                raise StorageReportValidationError(
                    "Review-only candidates must be regular non-reparse files."
                )
            if removal_risk != "high":
                raise StorageReportValidationError(
                    "Review-only candidates must carry high removal risk."
                )
        elif eligibility == "protected" and "protected" not in attributes:
            raise StorageReportValidationError(
                "Protected candidates must include the protected attribute."
            )
        elif eligibility == "unavailable" and "unavailable" not in attributes:
            raise StorageReportValidationError(
                "Unavailable candidates must include the unavailable attribute."
            )
        if (
            eligibility in {"protected", "unavailable"}
            and removal_risk != "protected"
        ):
            raise StorageReportValidationError(
                "Protected and unavailable candidates require protected removal risk."
            )

        for attribute in attributes:
            retained_attribute_counts[attribute] += 1
            retained_attribute_bytes[attribute] += candidate["size_bytes"] or 0

    for attribute, attribute_summary in summary["attributes"].items():
        if attribute_summary["candidate_count"] < retained_attribute_counts[
            attribute
        ]:
            raise StorageReportValidationError(
                f"Attribute summary for {attribute} omits retained candidates."
            )
        if attribute_summary["unique_bytes"] < retained_attribute_bytes[
            attribute
        ]:
            raise StorageReportValidationError(
                f"Attribute summary for {attribute} understates retained bytes."
            )
        if summary["omitted_candidates"] == 0:
            if attribute_summary["candidate_count"] != retained_attribute_counts[
                attribute
            ]:
                raise StorageReportValidationError(
                    f"Attribute count for {attribute} does not match candidates."
                )
            if attribute_summary["unique_bytes"] != retained_attribute_bytes[
                attribute
            ]:
                raise StorageReportValidationError(
                    f"Attribute bytes for {attribute} do not match candidates."
                )

    folder_candidates = report.get("folder_candidates", [])
    folder_summary = report.get("folder_candidate_summary")
    if folder_candidates and folder_summary is None:
        raise StorageReportValidationError(
            "Folder candidates require a folder_candidate_summary."
        )
    if folder_summary is not None:
        folder_ids = [
            candidate["candidate_id"] for candidate in folder_candidates
        ]
        if len(folder_ids) != len(set(folder_ids)):
            raise StorageReportValidationError(
                "Retained folder candidate_id values must be unique."
            )
        if set(folder_ids).intersection(candidate_ids):
            raise StorageReportValidationError(
                "File and folder candidate identifiers must not overlap."
            )
        folder_paths = [
            os.path.normcase(os.path.abspath(candidate["path"]))
            for candidate in folder_candidates
        ]
        if len(folder_paths) != len(set(folder_paths)):
            raise StorageReportValidationError(
                "Retained folder candidate paths must be unique."
            )
        if folder_summary["retained_candidates"] != len(folder_candidates):
            raise StorageReportValidationError(
                "Folder retained_candidates does not match the folder rows."
            )
        if folder_summary["total_candidates"] != (
            folder_summary["retained_candidates"]
            + folder_summary["omitted_candidates"]
        ):
            raise StorageReportValidationError(
                "Total folder candidates must equal retained plus omitted."
            )
        if "folder_candidate_details_retained" in scan and (
            scan["folder_candidate_details_retained"]
            != folder_summary["retained_candidates"]
            or scan.get("folder_candidate_details_omitted")
            != folder_summary["omitted_candidates"]
        ):
            raise StorageReportValidationError(
                "Scan and folder-summary detail counts do not match."
            )

        retained_folder_counts = {
            attribute: 0 for attribute in folder_summary["attributes"]
        }
        retained_folder_bytes = {
            attribute: 0 for attribute in folder_summary["attributes"]
        }
        for candidate in folder_candidates:
            if candidate.get("item_type") != "folder":
                raise StorageReportValidationError(
                    "Folder candidates must declare item_type folder."
                )
            if candidate.get("is_directory") is not True:
                raise StorageReportValidationError(
                    "Folder candidates must describe directories."
                )
            if candidate["is_regular_file"] or candidate["is_reparse_point"]:
                raise StorageReportValidationError(
                    "Folder candidates cannot be regular files or reparse points."
                )
            attributes = set(candidate["attributes"])
            evidence_attributes = {
                evidence["attribute"] for evidence in candidate["evidence"]
            }
            if attributes != evidence_attributes:
                raise StorageReportValidationError(
                    f"Folder candidate {candidate['candidate_id']} must contain "
                    "evidence for exactly its listed attributes."
                )
            eligibility = candidate["protection"]["eligibility"]
            removal_risk = candidate.get("removal_risk", "protected")
            if eligibility == "eligible" and removal_risk in {
                "high",
                "protected",
            }:
                raise StorageReportValidationError(
                    "High-risk folders cannot be cleanup eligible."
                )
            if eligibility == "review_only" and removal_risk != "high":
                raise StorageReportValidationError(
                    "Review-only folders must carry high removal risk."
                )
            if eligibility == "unavailable" and (
                "unavailable" not in attributes
                or removal_risk != "protected"
            ):
                raise StorageReportValidationError(
                    "Unavailable folders require unavailable evidence and protected risk."
                )
            if candidate.get("contains_unavailable_items") and (
                eligibility != "unavailable"
            ):
                raise StorageReportValidationError(
                    "Folders with unavailable descendants cannot be selectable."
                )
            if (
                candidate.get("contains_high_risk_items")
                and not candidate.get("contains_unavailable_items")
                and (
                    eligibility != "review_only" or removal_risk != "high"
                )
            ):
                raise StorageReportValidationError(
                    "Folders with high-risk descendants must remain review-only."
                )
            if not candidate.get("tree_metadata_fingerprint"):
                raise StorageReportValidationError(
                    "Folder candidates require a metadata tree fingerprint."
                )
            for attribute in attributes:
                retained_folder_counts[attribute] += 1
                retained_folder_bytes[attribute] += (
                    candidate.get("allocated_size_bytes") or 0
                )

        retained_largest = max(
            (
                candidate.get("allocated_size_bytes") or 0
                for candidate in folder_candidates
            ),
            default=0,
        )
        if folder_summary["largest_candidate_allocated_bytes"] < retained_largest:
            raise StorageReportValidationError(
                "Largest folder candidate size understates retained rows."
            )
        for attribute, attribute_summary in folder_summary["attributes"].items():
            if attribute_summary["candidate_count"] < retained_folder_counts[
                attribute
            ] or attribute_summary["overlapping_bytes"] < retained_folder_bytes[
                attribute
            ]:
                raise StorageReportValidationError(
                    f"Folder attribute summary for {attribute} understates retained rows."
                )
            if folder_summary["omitted_candidates"] == 0 and (
                attribute_summary["candidate_count"]
                != retained_folder_counts[attribute]
                or attribute_summary["overlapping_bytes"]
                != retained_folder_bytes[attribute]
            ):
                raise StorageReportValidationError(
                    f"Folder attribute summary for {attribute} does not match rows."
                )

    development = report.get("development_insights")
    if development is not None:
        if (
            development["errors"] or development.get("errors_omitted", 0)
        ) and development["status"] == "complete":
            raise StorageReportValidationError(
                "Development discovery with errors cannot be complete."
            )
        locations = development["locations"]
        location_ids = [location["location_id"] for location in locations]
        if len(location_ids) != len(set(location_ids)):
            raise StorageReportValidationError(
                "Development insight location_id values must be unique."
            )
        scan_roots = tuple(
            Path(root["canonical_path"] or root["requested_path"])
            for root in report["scan_scope"]["roots"]
        )
        for location in locations:
            path = Path(location["path"])
            actually_within_scope = any(
                is_path_within(path, root) for root in scan_roots
            )
            if location["within_scan_scope"] != actually_within_scope:
                raise StorageReportValidationError(
                    "Development location scope does not match the scan roots."
                )
            if actually_within_scope:
                if (
                    location["measurement"] != "observed_selected_roots"
                    or location["files_observed"] is None
                    or location["bytes_observed"] is None
                    or location["coverage"] == "not_applicable"
                ):
                    raise StorageReportValidationError(
                        "Measured development locations require observed values and coverage."
                    )
            elif (
                location["measurement"] != "not_measured"
                or location["files_observed"] is not None
                or location["bytes_observed"] is not None
                or location["coverage"] != "not_applicable"
            ):
                raise StorageReportValidationError(
                    "Out-of-scope development locations must remain unmeasured."
                )

            if any(
                is_path_within(Path(candidate["path"]), path)
                for candidate in candidates
            ):
                raise StorageReportValidationError(
                    "Informational development locations cannot contain cleanup candidates."
                )


def validate_storage_report(
    report: dict[str, Any],
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
) -> None:
    """Validate JSON Schema and cross-field accounting invariants."""

    if report.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise StorageReportValidationError(
            f"Only storage schema {SUPPORTED_SCHEMA_VERSION} is supported."
        )

    schema = _load_schema(str(Path(schema_path).resolve()))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(report),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first_error = errors[0]
        raise StorageReportValidationError(
            f"Validation failed at {_validation_location(first_error)}: "
            f"{first_error.message}"
        )

    _validate_semantics(report)


def write_storage_report(report: dict[str, Any], output_path: str | Path) -> Path:
    """Validate and exclusively create a UTF-8 JSON report."""

    validate_storage_report(report)
    output = Path(output_path)
    payload = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as report_file:
            report_file.write(payload)
    except FileExistsError as error:
        raise StorageReportWriteError(
            f"A report already exists at '{output}'. Choose a new filename."
        ) from error
    except OSError as error:
        raise StorageReportWriteError(
            f"The report could not be written to '{output}'."
        ) from error
    return output.resolve()
