"""Storage report validation and UTF-8 JSON export."""

from __future__ import annotations

import json
import math
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

    retained_bytes = sum(
        candidate["size_bytes"] or 0 for candidate in candidates
    )
    if summary["retained_candidates"] != len(candidates):
        raise StorageReportValidationError(
            "retained_candidates does not match the candidate rows."
        )
    if summary["retained_unique_candidate_bytes"] != retained_bytes:
        raise StorageReportValidationError(
            "retained_unique_candidate_bytes does not match retained rows."
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
    if scan["candidate_details_retained"] != summary["retained_candidates"]:
        raise StorageReportValidationError(
            "Scan and summary retained-candidate counts do not match."
        )
    if scan["candidate_details_omitted"] != summary["omitted_candidates"]:
        raise StorageReportValidationError(
            "Scan and summary omitted-candidate counts do not match."
        )

    retained_attribute_counts = {
        attribute: 0 for attribute in summary["attributes"]
    }
    retained_attribute_bytes = {
        attribute: 0 for attribute in summary["attributes"]
    }
    for candidate in candidates:
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
        elif eligibility == "protected" and "protected" not in attributes:
            raise StorageReportValidationError(
                "Protected candidates must include the protected attribute."
            )
        elif eligibility == "unavailable" and "unavailable" not in attributes:
            raise StorageReportValidationError(
                "Unavailable candidates must include the unavailable attribute."
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

    development = report.get("development_insights")
    if development is not None:
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
