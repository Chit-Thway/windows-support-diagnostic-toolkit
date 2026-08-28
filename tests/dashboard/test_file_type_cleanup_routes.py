from __future__ import annotations

import copy
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import dashboard.file_type_cleanup as cleanup_routes
from dashboard.app import create_app
from dashboard.cleanup_tokens import CleanupPreviewStore
from dashboard.file_type_index_loader import FileTypeIndexSnapshot

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC_REPORT = REPOSITORY_ROOT / "tests" / "fixtures" / "warning-report.json"
INDEX_FIXTURE = (
    REPOSITORY_ROOT
    / "tests"
    / "storage"
    / "fixtures"
    / "complete-file-type-index.json"
)


def _utc_timestamp(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _snapshot(tmp_path: Path, *, state: str = "selectable"):
    indexed_file = tmp_path / "review-me.pdf"
    indexed_file.write_bytes(b"controlled test file")
    index_path = tmp_path / "index.json"
    index_path.write_text("{}", encoding="utf-8")
    report = json.loads(INDEX_FIXTURE.read_text(encoding="utf-8"))
    row = copy.deepcopy(
        next(item for item in report["files"] if item["extension"] == ".pdf")
    )
    row.update(
        file_id="file-controlled",
        folder_id="folder-root",
        path=str(indexed_file),
        name=indexed_file.name,
        size_bytes=indexed_file.stat().st_size,
        modified_at_utc=_utc_timestamp(indexed_file),
        selection_state=state,
        protection_reason="Controlled test selection state.",
    )
    report["drive"]["drive_letter"] = tmp_path.drive.upper()
    report["scope"]["root_path"] = str(tmp_path)
    report["files"] = [row]
    snapshot = FileTypeIndexSnapshot(
        path=index_path,
        report=report,
        folders_by_id={},
        children_by_parent={},
        root={},
        indexed_extensions=frozenset({".pdf"}),
    )
    return snapshot, indexed_file


def _app(tmp_path: Path, snapshot, recycled: list[Path]):
    return create_app(
        report_path=DIAGNOSTIC_REPORT,
        file_type_index_path=snapshot.path,
        test_config={
            "TESTING": True,
            "STORAGE_ACTION_TOKEN": "fixed-action-token",
            "CLEANUP_PREVIEW_STORE": CleanupPreviewStore(),
            "CLEANUP_RECORD_DIRECTORY": tmp_path / "cleanup-records",
            "RECYCLE_HANDLER": lambda path: recycled.append(path),
        },
    )


def _preview(client, drive: str):
    return client.post(
        f"/storage/{drive}/file-types/cleanup/preview",
        data={
            "action_token": "fixed-action-token",
            "file_id": "file-controlled",
        },
    )


def _preview_token(response) -> str:
    match = re.search(
        r'name="preview_token" value="([^"]+)"',
        response.get_data(as_text=True),
    )
    assert match is not None
    return html.unescape(match.group(1))


def test_file_type_cleanup_is_post_only_and_requires_action_token(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot, _file = _snapshot(tmp_path)
    monkeypatch.setattr(
        cleanup_routes, "load_file_type_index_for_drive", lambda _paths, _drive: snapshot
    )
    client = _app(tmp_path, snapshot, []).test_client()
    drive = snapshot.report["drive"]["drive_letter"]

    assert client.get(f"/storage/{drive}/file-types/cleanup/preview").status_code == 405
    assert client.get(f"/storage/{drive}/file-types/cleanup/confirm").status_code == 405
    response = client.post(
        f"/storage/{drive}/file-types/cleanup/preview",
        data={"action_token": "wrong", "file_id": "file-controlled"},
    )
    assert response.status_code == 403


def test_preview_lists_exact_path_and_requires_separate_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot, indexed_file = _snapshot(tmp_path)
    monkeypatch.setattr(
        cleanup_routes, "load_file_type_index_for_drive", lambda _paths, _drive: snapshot
    )
    client = _app(tmp_path, snapshot, []).test_client()
    drive = snapshot.report["drive"]["drive_letter"]

    response = _preview(client, drive)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert str(indexed_file) in body
    assert "No file has been moved or deleted" in body
    assert "Move reviewed files to Recycle Bin" in body


def test_review_only_index_file_cannot_enter_preview(tmp_path: Path, monkeypatch) -> None:
    snapshot, _file = _snapshot(tmp_path, state="review_only")
    monkeypatch.setattr(
        cleanup_routes, "load_file_type_index_for_drive", lambda _paths, _drive: snapshot
    )
    drive = snapshot.report["drive"]["drive_letter"]

    response = _preview(_app(tmp_path, snapshot, []).test_client(), drive)

    assert response.status_code == 409
    assert b"Review-only, protected, and unavailable" in response.data


def test_confirm_revalidates_and_invokes_only_configured_recycler(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot, indexed_file = _snapshot(tmp_path)
    monkeypatch.setattr(
        cleanup_routes, "load_file_type_index_for_drive", lambda _paths, _drive: snapshot
    )
    recycled: list[Path] = []
    client = _app(tmp_path, snapshot, recycled).test_client()
    drive = snapshot.report["drive"]["drive_letter"]
    token = _preview_token(_preview(client, drive))

    response = client.post(
        f"/storage/{drive}/file-types/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
        },
    )

    assert response.status_code == 200
    assert recycled == [indexed_file]
    assert b"Recycled</dt><dd>1" in response.data
    assert list((tmp_path / "cleanup-records").glob("cleanup-result-*.json"))


def test_changed_file_is_skipped_and_consumed_token_cannot_be_reused(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot, indexed_file = _snapshot(tmp_path)
    monkeypatch.setattr(
        cleanup_routes, "load_file_type_index_for_drive", lambda _paths, _drive: snapshot
    )
    recycled: list[Path] = []
    client = _app(tmp_path, snapshot, recycled).test_client()
    drive = snapshot.report["drive"]["drive_letter"]
    token = _preview_token(_preview(client, drive))
    indexed_file.write_bytes(b"changed after preview")
    confirmation = {
        "action_token": "fixed-action-token",
        "preview_token": token,
        "confirm_cleanup": "yes",
    }

    response = client.post(
        f"/storage/{drive}/file-types/cleanup/confirm", data=confirmation
    )
    reused = client.post(
        f"/storage/{drive}/file-types/cleanup/confirm", data=confirmation
    )

    assert response.status_code == 200
    assert b"Changed and skipped</dt><dd>1" in response.data
    assert recycled == []
    assert reused.status_code == 409


def test_replaced_index_after_preview_fails_closed(tmp_path: Path, monkeypatch) -> None:
    snapshot, _indexed_file = _snapshot(tmp_path)
    monkeypatch.setattr(
        cleanup_routes, "load_file_type_index_for_drive", lambda _paths, _drive: snapshot
    )
    recycled: list[Path] = []
    client = _app(tmp_path, snapshot, recycled).test_client()
    drive = snapshot.report["drive"]["drive_letter"]
    token = _preview_token(_preview(client, drive))
    snapshot.report["generated_at_utc"] = "2026-08-28T00:00:00Z"

    response = client.post(
        f"/storage/{drive}/file-types/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
        },
    )

    assert response.status_code == 409
    assert b"index was replaced after review" in response.data
    assert recycled == []


def test_recycle_failure_is_recorded_without_permanent_delete(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot, _file = _snapshot(tmp_path)
    monkeypatch.setattr(
        cleanup_routes, "load_file_type_index_for_drive", lambda _paths, _drive: snapshot
    )
    app = _app(tmp_path, snapshot, [])
    app.config["RECYCLE_HANDLER"] = lambda _path: (_ for _ in ()).throw(
        OSError("controlled failure")
    )
    client = app.test_client()
    drive = snapshot.report["drive"]["drive_letter"]
    token = _preview_token(_preview(client, drive))

    response = client.post(
        f"/storage/{drive}/file-types/cleanup/confirm",
        data={
            "action_token": "fixed-action-token",
            "preview_token": token,
            "confirm_cleanup": "yes",
        },
    )

    assert response.status_code == 200
    assert b"Failed safely</dt><dd>1" in response.data
    assert b"No permanent-delete fallback was used" in response.data
