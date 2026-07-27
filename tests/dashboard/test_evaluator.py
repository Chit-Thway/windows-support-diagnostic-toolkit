from __future__ import annotations

import pytest

from dashboard.evaluator import (
    HEALTHY,
    PROBLEM,
    UNAVAILABLE,
    WARNING,
    _evaluate_disk,
    _evaluate_service,
    evaluate_report,
)


def statuses_for(view_model: dict, category: str) -> list[str]:
    return [
        check["status"]
        for check in view_model["checks"]
        if check["category"] == category
    ]


def test_healthy_fixture_evaluates_healthy(fixture_data) -> None:
    result = evaluate_report(fixture_data("healthy-report.json"))

    assert result["overall_status"] == HEALTHY
    assert result["status_counts"][WARNING] == 0
    assert result["status_counts"][PROBLEM] == 0
    assert result["collection_status"] == "complete"


def test_warning_fixture_has_explanations_and_next_actions(fixture_data) -> None:
    result = evaluate_report(fixture_data("warning-report.json"))

    assert result["overall_status"] == WARNING
    assert WARNING in statuses_for(result, "Memory")
    assert WARNING in statuses_for(result, "Disk")
    assert WARNING in statuses_for(result, "Events")
    assert all(
        check["explanation"] and check["next_action"]
        for check in result["findings"]
        if check["status"] in {WARNING, PROBLEM}
    )


def test_problem_fixture_evaluates_problem(fixture_data) -> None:
    result = evaluate_report(fixture_data("problem-report.json"))

    assert result["overall_status"] == PROBLEM
    assert PROBLEM in statuses_for(result, "Memory")
    assert PROBLEM in statuses_for(result, "Disk")
    assert PROBLEM in statuses_for(result, "Network")
    assert PROBLEM in statuses_for(result, "Service")
    assert PROBLEM in statuses_for(result, "Events")


def test_partial_fixture_keeps_collection_status_separate(fixture_data) -> None:
    result = evaluate_report(fixture_data("partial-report.json"))

    assert result["collection_status"] == "partial"
    assert result["overall_status"] == WARNING
    assert result["status_counts"][UNAVAILABLE] >= 1
    assert result["collection_errors"]


@pytest.mark.parametrize(
    ("percent_used", "expected"),
    [
        (79.99, HEALTHY),
        (80.0, WARNING),
        (89.99, WARNING),
        (90.0, PROBLEM),
    ],
)
def test_memory_threshold_boundaries(
    fixture_data, percent_used: float, expected: str
) -> None:
    report = fixture_data("healthy-report.json")
    report["resources"]["memory"]["percent_used"] = percent_used

    result = evaluate_report(report)

    assert result["memory"]["status"] == expected


@pytest.mark.parametrize(
    ("percent_free", "free_gb", "expected"),
    [
        (15.0, 20.0, HEALTHY),
        (14.99, 19.99, WARNING),
        (9.0, 15.0, WARNING),
        (12.0, 9.0, WARNING),
        (9.99, 9.99, PROBLEM),
        (5.0, 25.0, HEALTHY),
    ],
)
def test_combined_disk_thresholds(
    percent_free: float, free_gb: float, expected: str
) -> None:
    disk, _ = _evaluate_disk(
        {
            "drive": "C:",
            "total_gb": 100.0,
            "free_gb": free_gb,
            "percent_free": percent_free,
        }
    )

    assert disk["status"] == expected


@pytest.mark.parametrize(
    ("startup_mode", "current_state", "availability", "expected"),
    [
        ("Automatic", "Stopped", "available", PROBLEM),
        ("Manual", "Stopped", "available", HEALTHY),
        ("Disabled", "Stopped", "available", HEALTHY),
        ("Automatic", "Running", "available", HEALTHY),
        (None, None, "unavailable", UNAVAILABLE),
    ],
)
def test_service_startup_behavior(
    startup_mode: str | None,
    current_state: str | None,
    availability: str,
    expected: str,
) -> None:
    service, _ = _evaluate_service(
        {
            "service_name": "EventLog",
            "display_name": "Windows Event Log",
            "availability": availability,
            "current_state": current_state,
            "startup_mode": startup_mode,
        }
    )

    assert service["status"] == expected


def test_empty_events_are_healthy_when_collection_succeeded(fixture_data) -> None:
    report = fixture_data("healthy-report.json")
    report["events"]["items"] = []

    result = evaluate_report(report)

    assert result["events"]["status"] == HEALTHY


def test_empty_services_are_unavailable_without_crashing(fixture_data) -> None:
    report = fixture_data("healthy-report.json")
    report["services"] = []

    result = evaluate_report(report)

    assert result["services"] == []
    assert UNAVAILABLE in statuses_for(result, "Service")


def test_null_memory_is_unavailable(fixture_data) -> None:
    report = fixture_data("healthy-report.json")
    report["resources"]["memory"]["total_gb"] = None
    report["resources"]["memory"]["available_gb"] = None
    report["resources"]["memory"]["used_gb"] = None
    report["resources"]["memory"]["percent_used"] = None
    report["collection_summary"]["status"] = "partial"
    report["collection_summary"]["sections"]["resources"] = "partial"

    result = evaluate_report(report)

    assert result["memory"]["status"] == UNAVAILABLE
    assert result["overall_status"] == UNAVAILABLE
