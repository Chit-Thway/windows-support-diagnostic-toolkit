from __future__ import annotations

import copy
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard.app import create_app
from dashboard.cleanup_tokens import CleanupPreviewStore, HIGH_RISK_FILE_COUNT

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC_REPORT = REPOSITORY_ROOT / "tests" / "fixtures" / "warning-report.json"
STORAGE_FIXTURES = REPOSITORY_ROOT / "tests" / "storage" / "fixtures"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_timestamp(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_storage_report(tmp_path: Path, *, count: int = 1) -> tuple[dict, list[Path]]:
    report = read_json(STORAGE_FIXTURES / "healthy-storage-report.json")
    candidate_template = read_json(
        STORAGE_FIXTURES / "candidate-attributes-storage-report.json"
    )["candidates"][0]
    paths: list[Path] = []
    candidates = []
    for index in range(count):
        path = tmp_path / f"review-{index + 1}.tmp"
        path.write_bytes(f"candidate-{index + 1}".encode("ascii"))
        paths.append(path)
        candidate = copy.deepcopy(candidate_template)
        candidate.update(
            candidate_id=f"cleanup-{index + 1:03d}",
            path=str(path),
            scan_root=str(tmp_path),
            name=path.name,
            extension=".tmp",
            size_bytes=path.stat().st_size,
            created_at_utc=utc_timestamp(path),
            modified_at_utc=utc_timestamp(path),
            last_accessed_at_utc=utc_timestamp(path),
            last_access_reliability="limited",
            attributes=["stale"],
            evidence=[
                {
                    "attribute": "stale",
                    "code": "modified_before_stale_cutoff",
                    "description": "Synthetic old-file evidence for a controlled test.",
                    "observed_value": utc_timestamp(path),
                }
            ],
            confidence="medium",
            is_regular_file=True,
            is_reparse_point=False,
        )
        candidate["protection"] = {
            "eligibility": "eligible",
            "reason_code": None,
            "explanation": "Synthetic eligible file inside an approved root.",
        }
        candidates.append(candidate)

    total_bytes = sum(path.stat().st_size for path in paths)
    report["drive"]["drive_letter"] = tmp_path.drive.upper()
    report["scan"]["files_examined"] = count
    report["scan"]["bytes_examined"] = total_bytes
    report["scan"]["candidate_details_retained"] = count
    report["scan"]["candidate_details_omitted"] = 0
    root = report["scan_scope"]["roots"][0]
    root.update(
        requested_path=str(tmp_path),
        canonical_path=str(tmp_path),
        files_examined=count,
        bytes_examined=total_bytes,
    )
    report["candidates"] = candidates
    summary = report["candidate_summary"]
    summary.update(
        total_unique_candidates=count,
        total_unique_candidate_bytes=total_bytes,
        retained_candidates=count,
        retained_unique_candidate_bytes=total_bytes,
        omitted_candidates=0,
    )
    for attribute, values in summary["attributes"].items():
        values["candidate_count"] = count if attribute == "stale" else 0
        values["unique_bytes"] = total_bytes if attribute == "stale" else 0
    return report, paths


def write_storage_report(tmp_path: Path, report: dict) -> Path:
    path = tmp_path / "storage-report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def build_app(
    report_path: Path,
    tmp_path: Path,
    recycled: list[Path],
    config_overrides: dict | None = None,
):
    test_config = {
        "TESTING": True,
        "STORAGE_ACTION_TOKEN": "fixed-action-token",
        "CLEANUP_PREVIEW_STORE": CleanupPreviewStore(),
        "CLEANUP_RECORD_DIRECTORY": tmp_path / "cleanup-records",
        "RECYCLE_HANDLER": lambda path: recycled.append(path),
    }
    if config_overrides:
        test_config.update(config_overrides)
    return create_app(
        report_path=DIAGNOSTIC_REPORT,
        storage_report_path=report_path,
        test_config=test_config,
    )


def preview_selection(
    client,
    drive: str,
    candidate_ids: list[str],
    *,
    candidate_kind: str = "file",
):
    return client.post(
        f"/storage/{drive}/cleanup/preview",
        data={
            "action_token": "fixed-action-token",
            "candidate_id": candidate_ids,
            "candidate_kind": candidate_kind,
        },
    )


def preview_token(response) -> str:
    match = re.search(
        r'name="preview_token" value="([^"]+)"',
        response.get_data(as_text=True),
    )
    assert match is not None
    return html.unescape(match.group(1))


def test_cleanup_routes_are_post_only_and_require_local_action_token(tmp_path) -> None:
    report, _paths = build_storage_report(tmp_path)
    report_path = write_storage_report(tmp_path, report)
    app = build_app(report_path, tmp_path, [])
    client = app.test_client()
    drive = report["drive"]["drive_letter"]

    assert client.get(f"/storage/{drive}/cleanup/preview").status_code == 405
    assert client.get(f"/storage/{drive}/cleanup/confirm").status_code == 405
    response = client.post(
        f"/storage/{drive}/cleanup/preview",
        data={"action_token": "wrong", "candidate_id": "cleanup-001"},
    )
    assert response.status_code == 403


def test_preview_shows_every_exact_path_and_escapes_report_content(tmp_path) -> None:
    report, paths = build_storage_report(tmp_path, count=2)
    report["candidates"][0]["evidence"][0]["description"] = (
        "<script>alert('unsafe')</script>"
    )
    report_path = write_storage_report(tmp_path, report)
    client = build_app(report_path, tmp_path, []).test_client()
    drive = report["drive"]["drive_letter"]

    response = preview_selection(
        client, drive, ["cleanup-001", "cleanup-002"]
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert all(str(path) in body for path in paths)
    assert "<script>alert('unsafe')</script>" not in body
    assert "&lt;script&gt;alert" in body
    assert "No file has been moved or deleted" in body


def test_folder_preview_uses_folder_collection_and_requires_phrase(tmp_path) -> None:
    report = read_json(
        REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
    )
    report_path = write_storage_report(tmp_path, report)
    client = build_app(report_path, tmp_path, []).test_client()

    response = preview_selection(
        client,
        "C:",
        ["folder-sample-002"],
        candidate_kind="folder",
    )
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Review every folder before recycling" in body
    assert "No folder has been moved or deleted" in body
    assert "RECYCLE 1 FOLDER" in body
    assert "Old Empty" in body


def test_review_only_folder_is_rejected_before_cleanup_preview(tmp_path) -> None:
    report = read_json(
        REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
    )
    candidate = report["folder_candidates"][0]
    candidate["removal_risk"] = "high"
    candidate["contains_high_risk_items"] = True
    candidate["protection"] = {
        "eligibility": "review_only",
        "reason_code": "high_risk_descendant",
        "explanation": "Fictional application data remains review-only.",
    }
    report_path = write_storage_report(tmp_path, report)
    client = build_app(report_path, tmp_path, []).test_client()

    response = preview_selection(
        client,
        "C:",
        [candidate["candidate_id"]],
        candidate_kind="folder",
    )

    assert response.status_code == 409
    assert b"cannot enter a cleanup preview" in response.data


def test_unknown_duplicate_and_empty_selections_are_rejected(tmp_path) -> None:
    report, _paths = build_storage_report(tmp_path)
    report_path = write_storage_report(tmp_path, report)
    client = build_app(report_path, tmp_path, []).test_client()
    drive = report["drive"]["drive_letter"]

    assert preview_selection(client, drive, []).status_code == 400
    assert preview_selection(client, drive, ["unknown"]).status_code == 400
    assert (
        preview_selection(
            client, drive, ["cleanup-001", "cleanup-001"]
        ).status_code
        == 400
    )


def test_confirm_revalidates_recycles_once_and_writes_ignored_record(tmp_path) -> None:
    report, paths = build_storage_report(tmp_path)
    report_path = write_storage_report(tmp_path, report)
    recycled: list[Path] = []
    client = build_app(report_path, tmp_path, recycled).test_client()
    drive = report["drive"]["drive_letter"]
    preview = preview_selection(client, drive, ["cleanup-001"])
    token = preview_token(preview)

    response = client.post(
        f"/storage/{drive}/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
        },
    )
    repeated = client.post(
        f"/storage/{drive}/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
        },
    )

    assert response.status_code == 200
    assert b"Recycle Bin action finished" in response.data
    assert recycled == paths
    assert repeated.status_code == 409
    assert recycled == paths
    assert len(list((tmp_path / "cleanup-records").glob("*.json"))) == 1


def test_missing_checkbox_consumes_preview_without_recycling(tmp_path) -> None:
    report, _paths = build_storage_report(tmp_path)
    report_path = write_storage_report(tmp_path, report)
    recycled: list[Path] = []
    client = build_app(report_path, tmp_path, recycled).test_client()
    drive = report["drive"]["drive_letter"]
    token = preview_token(preview_selection(client, drive, ["cleanup-001"]))

    response = client.post(
        f"/storage/{drive}/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
        },
    )

    assert response.status_code == 400
    assert recycled == []


def test_file_changed_after_preview_is_skipped_safely(tmp_path) -> None:
    report, paths = build_storage_report(tmp_path)
    report_path = write_storage_report(tmp_path, report)
    recycled: list[Path] = []
    client = build_app(report_path, tmp_path, recycled).test_client()
    drive = report["drive"]["drive_letter"]
    token = preview_token(preview_selection(client, drive, ["cleanup-001"]))
    paths[0].write_bytes(b"changed after preview")

    response = client.post(
        f"/storage/{drive}/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
        },
    )

    assert response.status_code == 200
    assert recycled == []
    assert b"Changed and skipped" in response.data
    assert b"file size changed" in response.data


def test_report_candidate_change_after_preview_fails_closed(tmp_path) -> None:
    report, _paths = build_storage_report(tmp_path)
    report_path = write_storage_report(tmp_path, report)
    recycled: list[Path] = []
    client = build_app(report_path, tmp_path, recycled).test_client()
    drive = report["drive"]["drive_letter"]
    token = preview_token(preview_selection(client, drive, ["cleanup-001"]))
    report["candidates"][0]["name"] = "changed-in-report.tmp"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    response = client.post(
        f"/storage/{drive}/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
        },
    )

    assert response.status_code == 409
    assert recycled == []
    assert b"Reviewed selection changed" in response.data


def test_high_risk_selection_requires_exact_typed_phrase(tmp_path) -> None:
    report, _paths = build_storage_report(
        tmp_path, count=HIGH_RISK_FILE_COUNT
    )
    report_path = write_storage_report(tmp_path, report)
    recycled: list[Path] = []
    client = build_app(report_path, tmp_path, recycled).test_client()
    drive = report["drive"]["drive_letter"]
    ids = [candidate["candidate_id"] for candidate in report["candidates"]]
    preview = preview_selection(client, drive, ids)
    token = preview_token(preview)

    assert b"RECYCLE 20 FILES" in preview.data
    response = client.post(
        f"/storage/{drive}/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
            "confirmation_phrase": "wrong",
        },
    )

    assert response.status_code == 400
    assert recycled == []


def test_expired_preview_never_recycles_a_file(tmp_path) -> None:
    report, _paths = build_storage_report(tmp_path)
    report_path = write_storage_report(tmp_path, report)
    recycled: list[Path] = []
    now = [datetime(2026, 8, 3, tzinfo=timezone.utc)]
    store = CleanupPreviewStore(
        clock=lambda: now[0],
        lifetime=timedelta(seconds=1),
    )
    client = build_app(
        report_path,
        tmp_path,
        recycled,
        {"CLEANUP_PREVIEW_STORE": store},
    ).test_client()
    drive = report["drive"]["drive_letter"]
    token = preview_token(preview_selection(client, drive, ["cleanup-001"]))
    now[0] += timedelta(seconds=2)

    response = client.post(
        f"/storage/{drive}/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
        },
    )

    assert response.status_code == 410
    assert recycled == []
    assert b"preview expired" in response.data.lower()


def test_cleanup_result_remains_visible_when_local_record_write_fails(
    tmp_path,
) -> None:
    report, paths = build_storage_report(tmp_path)
    report_path = write_storage_report(tmp_path, report)
    recycled: list[Path] = []
    blocked_record_directory = tmp_path / "record-path-is-a-file"
    blocked_record_directory.write_text("synthetic blocker", encoding="utf-8")
    client = build_app(
        report_path,
        tmp_path,
        recycled,
        {"CLEANUP_RECORD_DIRECTORY": blocked_record_directory},
    ).test_client()
    drive = report["drive"]["drive_letter"]
    token = preview_token(preview_selection(client, drive, ["cleanup-001"]))

    response = client.post(
        f"/storage/{drive}/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
        },
    )

    assert response.status_code == 200
    assert recycled == paths
    assert b"Record warning" in response.data
    assert b"could not be written" in response.data
