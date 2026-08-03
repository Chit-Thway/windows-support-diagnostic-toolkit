from __future__ import annotations

import json
from pathlib import Path

from dashboard.app import create_app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC_FIXTURES = REPOSITORY_ROOT / "tests" / "fixtures"
STORAGE_FIXTURES = REPOSITORY_ROOT / "tests" / "storage" / "fixtures"


def build_app(storage_report_path: Path | str | None):
    return create_app(
        report_path=DIAGNOSTIC_FIXTURES / "warning-report.json",
        storage_report_path=storage_report_path,
        test_config={"TESTING": True},
    )


def test_disk_card_is_an_accessible_storage_link() -> None:
    response = build_app(
        STORAGE_FIXTURES / "healthy-storage-report.json"
    ).test_client().get("/")

    assert response.status_code == 200
    assert b'class="panel metric-card disk-card' in response.data
    assert b'href="/storage/C:"' in response.data
    assert b'aria-label="Open storage insights for C: drive"' in response.data


def test_valid_storage_report_renders_drive_dashboard() -> None:
    response = build_app(
        STORAGE_FIXTURES / "healthy-storage-report.json"
    ).test_client().get("/storage/C:")

    assert response.status_code == 200
    assert b"Where the drive space is used" in response.data
    assert b"Cleanup-candidate summary" in response.data
    assert b"Every byte appears in one category only" in response.data
    assert b"Candidate explorer" in response.data
    assert b"Match all" in response.data
    assert b"Match any" in response.data
    assert b"Select all visible" in response.data
    assert b"Export cleanup plan" in response.data
    assert b"Review Recycle Bin action" in response.data
    assert b"Development storage insights" in response.data
    assert b"Path contains" in response.data
    assert b'role="img"' in response.data
    assert b"read-only" in response.data.lower()


def test_empty_candidate_report_has_useful_empty_state() -> None:
    response = build_app(
        STORAGE_FIXTURES / "healthy-storage-report.json"
    ).test_client().get("/storage/C:")

    assert response.status_code == 200
    assert b"No cleanup candidates reported" in response.data


def test_partial_report_shows_errors_completeness_and_limitations() -> None:
    response = build_app(
        STORAGE_FIXTURES / "partial-storage-report.json"
    ).test_client().get("/storage/C:")

    assert response.status_code == 200
    assert b"This analysis is incomplete" in response.data
    assert b"Scan errors and inaccessible paths" in response.data
    assert b"The fictional path exists" in response.data
    assert b"Limitations" in response.data


def test_no_selected_storage_report_shows_scan_instructions() -> None:
    response = build_app(None).test_client().get("/storage/C:")

    assert response.status_code == 200
    assert b"Create a read-only storage analysis first" in response.data
    assert b"python -m storage" in response.data
    assert b"never start a filesystem scan" in response.data


def test_missing_storage_report_shows_scan_instructions(tmp_path: Path) -> None:
    response = build_app(tmp_path / "missing.json").test_client().get(
        "/storage/C:"
    )

    assert response.status_code == 200
    assert b"configured file was not found" in response.data


def test_malformed_storage_report_has_friendly_error() -> None:
    response = build_app(
        STORAGE_FIXTURES / "malformed-storage-report.json"
    ).test_client().get("/storage/C:")

    assert response.status_code == 422
    assert b"Storage analysis is not valid JSON" in response.data
    assert b"Traceback" not in response.data


def test_unsupported_storage_report_has_friendly_error(tmp_path: Path) -> None:
    report = json.loads(
        (STORAGE_FIXTURES / "healthy-storage-report.json").read_text(
            encoding="utf-8"
        )
    )
    report["schema_version"] = "2.0.0"
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    response = build_app(path).test_client().get("/storage/C:")

    assert response.status_code == 422
    assert b"Unsupported storage report version" in response.data
    assert b"Traceback" not in response.data


def test_storage_report_for_another_drive_is_rejected() -> None:
    response = build_app(
        STORAGE_FIXTURES / "candidate-attributes-storage-report.json"
    ).test_client().get("/storage/C:")

    assert response.status_code == 409
    assert b"belongs to another drive" in response.data
    assert b"The selected analysis is for D:, not C:" in response.data


def test_unknown_diagnostic_drive_is_rejected() -> None:
    response = build_app(
        STORAGE_FIXTURES / "healthy-storage-report.json"
    ).test_client().get("/storage/Z:")

    assert response.status_code == 404
    assert b"Drive not found in diagnostic report" in response.data


def test_storage_report_content_is_html_escaped(tmp_path: Path) -> None:
    report = json.loads(
        (STORAGE_FIXTURES / "healthy-storage-report.json").read_text(
            encoding="utf-8"
        )
    )
    report["limitations"][0] = "<script>alert('unsafe')</script>"
    path = tmp_path / "escaped.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    response = build_app(path).test_client().get("/storage/C:")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<script>alert('unsafe')</script>" not in body
    assert "&lt;script&gt;alert" in body


def test_storage_routes_are_read_only() -> None:
    client = build_app(STORAGE_FIXTURES / "healthy-storage-report.json").test_client()

    assert client.post("/storage/C:").status_code == 405
    assert client.put("/storage/C:").status_code == 405
    assert client.delete("/storage/C:").status_code == 405


def test_storage_page_keeps_local_security_headers() -> None:
    response = build_app(
        STORAGE_FIXTURES / "healthy-storage-report.json"
    ).test_client().get("/storage/C:")

    assert response.headers["Cache-Control"] == "no-store"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_candidate_row_contains_evidence_and_safe_actions() -> None:
    response = create_app(
        report_path=REPOSITORY_ROOT / "sample_data" / "sample-report.json",
        storage_report_path=(
            REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
        ),
        test_config={"TESTING": True, "STORAGE_ACTION_TOKEN": "fixed-token"},
    ).test_client().get("/storage/C:")

    assert response.status_code == 200
    assert b"fictional-archive.iso.part" in response.data
    assert b"Path and evidence" in response.data
    assert b"Copy path" in response.data
    assert b"Open folder" in response.data
    assert b'data-attributes="stale,likely_incomplete,large"' in response.data
    assert b'data-action-token="fixed-token"' in response.data
    assert b"python -m pip cache purge" in response.data
    assert b"Java 21.0.4" in response.data
    assert b"Automatic cleanup" in response.data


def test_development_insight_content_is_html_escaped(tmp_path: Path) -> None:
    report = json.loads(
        (REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json").read_text(
            encoding="utf-8"
        )
    )
    report["development_insights"]["locations"][0]["display_name"] = (
        "<script>alert('fictional')</script>"
    )
    storage_report = tmp_path / "escaped-storage-report.json"
    storage_report.write_text(json.dumps(report), encoding="utf-8")
    response = create_app(
        report_path=REPOSITORY_ROOT / "sample_data" / "sample-report.json",
        storage_report_path=storage_report,
        test_config={"TESTING": True},
    ).test_client().get("/storage/C:")

    assert response.status_code == 200
    assert b"<script>alert" not in response.data
    assert b"&lt;script&gt;alert" in response.data


def test_unavailable_candidate_has_disabled_selection_and_folder_action() -> None:
    response = build_app(
        STORAGE_FIXTURES / "partial-storage-report.json"
    ).test_client().get("/storage/C:")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-eligibility="unavailable"' in body
    assert "Select locked-fragment.tmp" in body
    assert 'disabled title="Unavailable metadata prevents selection' in body
    assert "Only eligible regular files can open their containing folder" in body


def test_open_folder_action_uses_report_candidate_not_client_path() -> None:
    opened: list[Path] = []
    app = create_app(
        report_path=REPOSITORY_ROOT / "sample_data" / "sample-report.json",
        storage_report_path=(
            REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
        ),
        test_config={
            "TESTING": True,
            "STORAGE_ACTION_TOKEN": "fixed-token",
            "OPEN_STORAGE_FOLDER_HANDLER": lambda folder: opened.append(folder),
        },
    )

    response = app.test_client().post(
        "/storage/C:/open-folder",
        data={
            "action_token": "fixed-token",
            "candidate_id": "sample-001",
            "path": r"C:\Windows\System32",
        },
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert [str(folder) for folder in opened] == [
        r"C:\Users\fictional.jamie\Downloads"
    ]


def test_open_folder_action_rejects_invalid_token() -> None:
    opened: list[Path] = []
    app = create_app(
        report_path=REPOSITORY_ROOT / "sample_data" / "sample-report.json",
        storage_report_path=(
            REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
        ),
        test_config={
            "TESTING": True,
            "STORAGE_ACTION_TOKEN": "fixed-token",
            "OPEN_STORAGE_FOLDER_HANDLER": lambda folder: opened.append(folder),
        },
    )

    response = app.test_client().post(
        "/storage/C:/open-folder",
        data={"action_token": "wrong", "candidate_id": "sample-001"},
    )

    assert response.status_code == 403
    assert opened == []


def test_open_folder_action_rejects_unavailable_candidate() -> None:
    app = create_app(
        report_path=DIAGNOSTIC_FIXTURES / "warning-report.json",
        storage_report_path=STORAGE_FIXTURES / "partial-storage-report.json",
        test_config={
            "TESTING": True,
            "STORAGE_ACTION_TOKEN": "fixed-token",
            "OPEN_STORAGE_FOLDER_HANDLER": lambda _folder: None,
        },
    )

    response = app.test_client().post(
        "/storage/C:/open-folder",
        data={"action_token": "fixed-token", "candidate_id": "partial-001"},
    )

    assert response.status_code == 409
    assert "Protected or unavailable" in response.get_json()["message"]


def test_open_folder_action_is_not_available_through_get() -> None:
    app = create_app(
        report_path=REPOSITORY_ROOT / "sample_data" / "sample-report.json",
        storage_report_path=(
            REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
        ),
        test_config={"TESTING": True},
    )

    assert app.test_client().get("/storage/C:/open-folder").status_code == 405
