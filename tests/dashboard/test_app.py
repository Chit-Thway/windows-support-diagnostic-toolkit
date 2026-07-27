from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.app import create_app


@pytest.mark.parametrize(
    ("fixture_name", "expected_status"),
    [
        ("healthy-report.json", "Healthy"),
        ("warning-report.json", "Warning"),
        ("problem-report.json", "Problem"),
    ],
)
def test_fixture_dashboard_pages(
    fixture_name: str, expected_status: str
) -> None:
    app = create_app(
        report_path=Path("tests/fixtures") / fixture_name,
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert f"Overall support status".encode() in response.data
    assert f">{expected_status}<".encode() in response.data
    assert response.headers["Cache-Control"] == "no-store"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_resource_progress_indicators_have_accessible_names() -> None:
    app = create_app(
        report_path="tests/fixtures/problem-report.json",
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b'aria-label="Memory used"' in response.data
    assert b'aria-label="C: used space"' in response.data


def test_partial_report_displays_unavailable_data_and_errors() -> None:
    app = create_app(
        report_path="tests/fixtures/partial-report.json",
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"Collection Partial" in response.data
    assert b"Collection errors" in response.data
    assert b"Unavailable" in response.data


def test_malformed_report_returns_friendly_error() -> None:
    app = create_app(
        report_path="tests/fixtures/malformed-report.json",
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")

    assert response.status_code == 422
    assert b"Report is not valid JSON" in response.data
    assert b"Traceback" not in response.data


def test_missing_report_returns_friendly_error(tmp_path: Path) -> None:
    app = create_app(
        report_path=tmp_path / "missing.json",
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")

    assert response.status_code == 404
    assert b"Report file not found" in response.data
    assert b"Traceback" not in response.data


def test_unsupported_schema_returns_friendly_error(
    fixture_data, write_report
) -> None:
    report = fixture_data("healthy-report.json")
    report["schema_version"] = "2.0.0"
    app = create_app(
        report_path=write_report(report),
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")

    assert response.status_code == 422
    assert b"Unsupported report version" in response.data


def test_schema_invalid_empty_services_returns_friendly_error(
    fixture_data, write_report
) -> None:
    report = fixture_data("healthy-report.json")
    report["services"] = []
    app = create_app(
        report_path=write_report(report),
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")

    assert response.status_code == 422
    assert b"expected contract" in response.data
    assert b"Traceback" not in response.data


def test_null_and_unavailable_data_render_without_crashing(
    fixture_data, write_report
) -> None:
    report = fixture_data("healthy-report.json")
    report["collection_summary"]["status"] = "partial"
    report["collection_summary"]["sections"]["resources"] = "partial"
    report["resources"]["memory"].update(
        {
            "total_gb": None,
            "available_gb": None,
            "used_gb": None,
            "percent_used": None,
        }
    )
    report["collection_errors"] = [
        {
            "section": "resources",
            "check": "Physical memory snapshot",
            "error_type": "CimException",
            "message": "Fictional memory query failure.",
            "occurred_at_utc": "2026-07-26T10:30:03.0000000Z",
        }
    ]
    app = create_app(
        report_path=write_report(report),
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"Usage unavailable" in response.data
    assert b"Fictional memory query failure." in response.data


def test_report_content_is_html_escaped(fixture_data, write_report) -> None:
    report = fixture_data("warning-report.json")
    report["events"]["items"][0]["message"] = "<script>alert('unsafe')</script>"
    report["system"]["hostname"] = "<img src=x onerror=alert(1)>"
    app = create_app(
        report_path=write_report(report),
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<script>alert('unsafe')</script>" not in body
    assert "&lt;script&gt;alert" in body
    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_empty_event_list_displays_empty_state(fixture_data, write_report) -> None:
    report = fixture_data("healthy-report.json")
    report["events"]["items"] = []
    app = create_app(
        report_path=write_report(report),
        test_config={"TESTING": True},
    )

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"No matching recent events" in response.data


def test_entry_point_binds_only_to_loopback(monkeypatch) -> None:
    import dashboard.__main__ as dashboard_main

    captured: dict = {}

    class FakeApp:
        def run(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(dashboard_main, "create_app", lambda report_path: FakeApp())
    dashboard_main.main(["--port", "5051"])

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 5051
    assert captured["debug"] is False
    assert captured["use_reloader"] is False
