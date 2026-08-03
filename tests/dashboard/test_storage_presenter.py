from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.storage_presenter import format_bytes, present_storage_report

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STORAGE_FIXTURES = REPOSITORY_ROOT / "tests" / "storage" / "fixtures"


def read_storage_fixture(name: str) -> dict:
    return json.loads((STORAGE_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "0 B"), (1024, "1.00 KiB"), (1073741824, "1.00 GiB")],
)
def test_format_bytes(value: int, expected: str) -> None:
    assert format_bytes(value) == expected


def test_chart_categories_are_non_overlapping_and_total_100_percent() -> None:
    report = read_storage_fixture("candidate-attributes-storage-report.json")

    view = present_storage_report(report, diagnostic_status="Warning")

    assert sum(category["bytes"] for category in view["categories"]) == report[
        "drive"
    ]["total_bytes"]
    assert sum(category["chart_percent"] for category in view["categories"]) == (
        pytest.approx(100, abs=0.00001)
    )
    assert view["drive"]["diagnostic_status"] == "Warning"
    assert view["categories"][0]["exact_bytes"].endswith(" bytes")


def test_candidate_unique_total_is_not_inflated_by_attribute_totals() -> None:
    report = read_storage_fixture("candidate-attributes-storage-report.json")

    view = present_storage_report(report, diagnostic_status="Healthy")

    attribute_bytes = sum(
        attribute["bytes"]
        for attribute in view["candidate_summary"]["attributes"]
    )
    assert attribute_bytes > view["candidate_summary"][
        "total_unique_candidate_bytes"
    ]
    assert view["candidate_summary"]["total_unique_candidates"] == 5
    assert len(view["candidates"]) == 5
    assert view["candidates"][0]["age_days"] is not None
    assert view["candidates"][0]["selectable"] is True
    assert {"stale", "likely_incomplete"}.issubset(
        set(view["candidates"][0]["attributes_filter"].split(","))
    )


def test_partial_report_preserves_completeness_and_exclusions() -> None:
    report = read_storage_fixture("partial-storage-report.json")
    report["scan"]["inaccessible_path_details_omitted"] = 4
    report["scan"]["scan_error_details_omitted"] = 4

    view = present_storage_report(report, diagnostic_status="Warning")

    assert view["scan"]["status_label"] == "Partial"
    assert view["scan"]["status_class"] == "unavailable"
    assert view["candidate_summary"]["excluded_count"] == 1
    assert view["inaccessible_paths"]
    assert view["scan"]["inaccessible_paths_total"] == 5
    assert view["scan"]["issue_details_omitted"] == 8
    assert view["candidates"][0]["selectable"] is False


def test_requested_root_is_displayed_when_canonical_path_is_unavailable() -> None:
    report = read_storage_fixture("partial-storage-report.json")
    report["scan_scope"]["roots"][0]["canonical_path"] = None

    view = present_storage_report(report, diagnostic_status="Warning")

    assert view["roots"][0]["display_path"] == report["scan_scope"]["roots"][0][
        "requested_path"
    ]


def test_development_insights_group_informational_locations() -> None:
    report = json.loads(
        (REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json").read_text(
            encoding="utf-8"
        )
    )

    view = present_storage_report(report, diagnostic_status="Healthy")

    assert view["development"]["status_label"] == "Complete"
    assert len(view["development"]["python_locations"]) == 2
    assert len(view["development"]["java_locations"]) == 1
    pip_cache = next(
        location
        for location in view["development"]["locations"]
        if location["kind"] == "package_cache"
    )
    assert pip_cache["size_display"] == "Unavailable"
    assert pip_cache["scope_label"].endswith("not measured")
