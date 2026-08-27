from __future__ import annotations

import json
from pathlib import Path

from dashboard.app import create_app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIAGNOSTIC = REPOSITORY_ROOT / "sample_data" / "sample-report.json"
SAMPLE_STORAGE = REPOSITORY_ROOT / "sample_data" / "sample-storage-report.json"
SAMPLE_INDEX = REPOSITORY_ROOT / "sample_data" / "sample-file-type-index.json"
COMPLETE_INDEX = (
    REPOSITORY_ROOT
    / "tests"
    / "storage"
    / "fixtures"
    / "complete-file-type-index.json"
)
INDEX_FIXTURES = REPOSITORY_ROOT / "tests" / "storage" / "fixtures"


def build_app(index_path=COMPLETE_INDEX):
    return create_app(
        report_path=SAMPLE_DIAGNOSTIC,
        storage_report_path=SAMPLE_STORAGE,
        file_type_index_path=index_path,
        test_config={"TESTING": True},
    )


def test_storage_page_links_to_cleanup_method() -> None:
    response = build_app().test_client().get("/storage/C:")

    assert response.status_code == 200
    assert b"Cleanup methods" in response.data
    assert b"Method 1" in response.data
    assert b"Open File-Type Explorer" in response.data
    assert b'href="/storage/C:/file-types"' in response.data


def test_file_type_explorer_renders_groups_and_root_only() -> None:
    response = build_app().test_client().get("/storage/C:/file-types")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "File-Type Explorer" in body
    assert "Documents" in body
    assert "Archives and disk images" in body
    assert 'data-extension-toggle value=".pdf" checked' in body
    assert 'data-folder-id="folder-root"' in body
    assert "support-guide.pdf" not in body
    assert "Changing a filter never starts another drive scan" in body
    assert "Matching-file results" in body


def test_folder_children_endpoint_returns_one_ranked_level() -> None:
    response = build_app().test_client().get(
        "/storage/C:/file-types/folders?parent_id=folder-root"
    )
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["ok"] is True
    assert [child["name"] for child in payload["children"]] == [
        "Users",
        "Program Files",
        "Windows",
    ]
    assert payload["children"][0]["extension_bytes"][".pdf"] == 13_631_488
    assert "files" not in payload


def test_folder_children_endpoint_rejects_unknown_parent() -> None:
    response = build_app().test_client().get(
        "/storage/C:/file-types/folders?parent_id=missing-folder"
    )

    assert response.status_code == 404
    assert response.get_json()["ok"] is False


def test_missing_index_shows_explicit_local_commands() -> None:
    app = create_app(
        report_path=REPOSITORY_ROOT / "tests" / "fixtures" / "warning-report.json",
        storage_report_path=(
            INDEX_FIXTURES / "healthy-storage-report.json"
        ),
        file_type_index_path=(),
        test_config={"TESTING": True},
    )
    response = app.test_client().get("/storage/C:/file-types")

    assert response.status_code == 200
    assert b"Create a local file-type index first" in response.data
    assert b"python -m storage.file_type_indexer" in response.data
    assert b"never starts a drive scan automatically" in response.data


def test_malformed_index_has_friendly_error() -> None:
    response = build_app(
        INDEX_FIXTURES / "malformed-file-type-index.json"
    ).test_client().get("/storage/C:/file-types")

    assert response.status_code == 422
    assert b"index is not valid JSON" in response.data
    assert b"Traceback" not in response.data


def test_unsupported_index_has_friendly_error(tmp_path: Path) -> None:
    index = json.loads(COMPLETE_INDEX.read_text(encoding="utf-8"))
    index["schema_version"] = "2.0.0"
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(index), encoding="utf-8")

    response = build_app(path).test_client().get("/storage/C:/file-types")

    assert response.status_code == 422
    assert b"Unsupported File-Type Explorer index version" in response.data


def test_index_for_another_drive_is_rejected() -> None:
    response = build_app(
        INDEX_FIXTURES / "partial-file-type-index.json"
    ).test_client().get("/storage/C:/file-types")

    assert response.status_code == 409
    assert b"belongs to another drive" in response.data
    assert b"Available indexed drive(s): F:" in response.data


def test_method_routes_are_read_only_and_keep_security_headers() -> None:
    client = build_app().test_client()

    page = client.get("/storage/C:/file-types")
    assert page.headers["Cache-Control"] == "no-store"
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]
    assert client.post("/storage/C:/file-types").status_code == 405
    assert client.delete("/storage/C:/file-types/folders").status_code == 405


def test_dynamic_tree_script_uses_text_content_not_html_insertion() -> None:
    source = (
        REPOSITORY_ROOT / "dashboard" / "static" / "file_type_explorer.js"
    ).read_text(encoding="utf-8")

    assert ".textContent" in source
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "fetch(" in source


def test_report_controlled_folder_text_is_html_escaped(tmp_path: Path) -> None:
    index = json.loads(COMPLETE_INDEX.read_text(encoding="utf-8"))
    index["folders"][0]["access"]["explanation"] = (
        '<script data-test="unsafe">alert(1)</script>'
    )
    path = tmp_path / "escaped-index.json"
    path.write_text(json.dumps(index), encoding="utf-8")

    response = build_app(path).test_client().get("/storage/C:/file-types")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<script data-test=" not in body
    assert "&lt;script data-test=&#34;unsafe&#34;&gt;" in body
