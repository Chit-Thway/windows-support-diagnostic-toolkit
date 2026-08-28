"""Flask application factory for the local support dashboard."""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request
from storage.cleanup import (
    DEFAULT_CLEANUP_RECORD_DIRECTORY,
    send_to_recycle_bin,
)

from .cleanup_tokens import CleanupPreviewStore
from .evaluator import evaluate_report
from .file_type_index_loader import (
    FileTypeIndexLoadError,
    FileTypeIndexNotFoundError,
    load_file_type_index_for_drive,
    resolve_file_type_index_paths,
)
from .file_type_presenter import (
    present_file_type_children,
    present_file_type_index,
)
from .file_type_review import FileTypeReviewQueryError, query_file_type_files
from .report_loader import ReportLoadError, load_report, resolve_report_path
from .storage_cleanup import register_storage_cleanup_routes
from .storage_actions import (
    StorageActionError,
    open_containing_folder,
    validate_candidate_for_folder_action,
)
from .storage_presenter import present_storage_report
from .storage_report_loader import (
    StorageReportLoadError,
    StorageReportNotFoundError,
    load_storage_report_for_drive,
    resolve_storage_report_paths,
)


def create_app(
    report_path: str | Path | None = None,
    storage_report_path: str | Path | Sequence[str | Path] | None = None,
    file_type_index_path: str | Path | Sequence[str | Path] | None = None,
    test_config: dict[str, Any] | None = None,
) -> Flask:
    resolved_report_path = resolve_report_path(report_path)
    resolved_storage_report_paths = resolve_storage_report_paths(
        storage_report_path,
        diagnostic_report_path=resolved_report_path,
    )
    resolved_file_type_index_paths = resolve_file_type_index_paths(
        file_type_index_path,
        diagnostic_report_path=resolved_report_path,
    )
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        REPORT_PATH=str(resolved_report_path),
        STORAGE_REPORT_PATHS=tuple(
            str(path) for path in resolved_storage_report_paths
        ),
        STORAGE_REPORT_PATH=(
            str(resolved_storage_report_paths[0])
            if resolved_storage_report_paths
            else None
        ),
        FILE_TYPE_INDEX_PATHS=tuple(
            str(path) for path in resolved_file_type_index_paths
        ),
        STORAGE_ACTION_TOKEN=secrets.token_urlsafe(32),
        OPEN_STORAGE_FOLDER_HANDLER=open_containing_folder,
        CLEANUP_PREVIEW_STORE=CleanupPreviewStore(),
        CLEANUP_RECORD_DIRECTORY=DEFAULT_CLEANUP_RECORD_DIRECTORY,
        RECYCLE_HANDLER=send_to_recycle_bin,
    )
    if test_config:
        app.config.update(test_config)

    @app.after_request
    def add_local_privacy_headers(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    def dashboard() -> tuple[str, int] | str:
        selected_report_path = Path(app.config["REPORT_PATH"])
        try:
            report = load_report(selected_report_path)
        except ReportLoadError as error:
            return (
                render_template(
                    "report_error.html",
                    error_title=error.title,
                    error_detail=error.detail,
                    selected_report_path=selected_report_path,
                    status_code=error.status_code,
                ),
                error.status_code,
            )

        return render_template(
            "dashboard.html",
            dashboard=evaluate_report(report),
            selected_report_path=selected_report_path,
        )

    @app.get("/storage/<drive>")
    def storage_drive(drive: str) -> tuple[str, int] | str:
        selected_report_path = Path(app.config["REPORT_PATH"])
        try:
            diagnostic_report = load_report(selected_report_path)
        except ReportLoadError as error:
            return (
                render_template(
                    "report_error.html",
                    error_title=error.title,
                    error_detail=error.detail,
                    selected_report_path=selected_report_path,
                    status_code=error.status_code,
                ),
                error.status_code,
            )

        dashboard_view = evaluate_report(diagnostic_report)
        diagnostic_disk = next(
            (item for item in dashboard_view["disks"] if item["drive"] == drive),
            None,
        )
        if diagnostic_disk is None:
            return (
                render_template(
                    "storage_report_error.html",
                    error_title="Drive not found in diagnostic report",
                    error_detail=(
                        f"The selected diagnostic report does not contain drive {drive}."
                    ),
                    selected_storage_report_path=None,
                    drive=drive,
                ),
                404,
            )

        configured_storage_paths = app.config["STORAGE_REPORT_PATHS"]
        if not configured_storage_paths:
            return render_template(
                "storage_report_missing.html",
                drive=drive,
                diagnostic_disk=diagnostic_disk,
                selected_report_path=selected_report_path,
            )

        try:
            storage_report, selected_storage_report_path = (
                load_storage_report_for_drive(configured_storage_paths, drive)
            )
        except StorageReportNotFoundError:
            return render_template(
                "storage_report_missing.html",
                drive=drive,
                diagnostic_disk=diagnostic_disk,
                selected_report_path=selected_report_path,
                selected_storage_report_path=Path(configured_storage_paths[0]),
            )
        except StorageReportLoadError as error:
            return (
                render_template(
                    "storage_report_error.html",
                    error_title=error.title,
                    error_detail=error.detail,
                    selected_storage_report_path=None,
                    drive=drive,
                ),
                error.status_code,
            )

        return render_template(
            "storage_drive.html",
            storage=present_storage_report(
                storage_report,
                diagnostic_status=diagnostic_disk["status"],
            ),
            selected_storage_report_path=selected_storage_report_path,
            storage_action_token=app.config["STORAGE_ACTION_TOKEN"],
        )

    @app.get("/storage/<drive>/file-types")
    def file_type_explorer(drive: str) -> tuple[str, int] | str:
        selected_report_path = Path(app.config["REPORT_PATH"])
        try:
            diagnostic_report = load_report(selected_report_path)
        except ReportLoadError as error:
            return (
                render_template(
                    "report_error.html",
                    error_title=error.title,
                    error_detail=error.detail,
                    selected_report_path=selected_report_path,
                    status_code=error.status_code,
                ),
                error.status_code,
            )

        dashboard_view = evaluate_report(diagnostic_report)
        diagnostic_disk = next(
            (item for item in dashboard_view["disks"] if item["drive"] == drive),
            None,
        )
        if diagnostic_disk is None:
            return (
                render_template(
                    "file_type_index_error.html",
                    error_title="Drive not found in diagnostic report",
                    error_detail=(
                        f"The selected diagnostic report does not contain drive {drive}."
                    ),
                    drive=drive,
                    selected_file_type_index_path=None,
                ),
                404,
            )

        configured_paths = app.config["FILE_TYPE_INDEX_PATHS"]
        if not configured_paths:
            return render_template(
                "file_type_index_missing.html",
                drive=drive,
                selected_report_path=selected_report_path,
            )
        try:
            snapshot = load_file_type_index_for_drive(configured_paths, drive)
        except FileTypeIndexNotFoundError:
            return render_template(
                "file_type_index_missing.html",
                drive=drive,
                selected_report_path=selected_report_path,
                selected_file_type_index_path=Path(configured_paths[0]),
            )
        except FileTypeIndexLoadError as error:
            return (
                render_template(
                    "file_type_index_error.html",
                    error_title=error.title,
                    error_detail=error.detail,
                    drive=drive,
                    selected_file_type_index_path=None,
                ),
                error.status_code,
            )

        return render_template(
            "file_type_explorer.html",
            explorer=present_file_type_index(snapshot),
        )

    @app.get("/storage/<drive>/file-types/folders")
    def file_type_folder_children(drive: str):
        configured_paths = app.config["FILE_TYPE_INDEX_PATHS"]
        if not configured_paths:
            return jsonify(
                ok=False,
                message="No File-Type Explorer index is selected.",
            ), 404
        try:
            snapshot = load_file_type_index_for_drive(configured_paths, drive)
        except FileTypeIndexLoadError as error:
            return jsonify(ok=False, message=error.detail), error.status_code

        parent_id = request.args.get(
            "parent_id", snapshot.root["folder_id"]
        ).strip()
        parent = snapshot.folders_by_id.get(parent_id)
        if parent is None:
            return jsonify(
                ok=False,
                message="The requested parent folder is not in this index.",
            ), 404
        return jsonify(
            ok=True,
            parent_id=parent_id,
            children=present_file_type_children(snapshot, parent_id),
        )

    @app.get("/storage/<drive>/file-types/files")
    def file_type_matching_files(drive: str):
        configured_paths = app.config["FILE_TYPE_INDEX_PATHS"]
        if not configured_paths:
            return jsonify(
                ok=False,
                message="No File-Type Explorer index is selected.",
            ), 404
        try:
            snapshot = load_file_type_index_for_drive(configured_paths, drive)
            result = query_file_type_files(
                snapshot,
                folder_ids=request.args.getlist("folder_id"),
                extensions=request.args.getlist("extension"),
                scope_mode=request.args.get("scope_mode", "recursive"),
                filename=request.args.get("filename", ""),
                minimum_size_bytes=request.args.get("minimum_size_bytes"),
                minimum_age_days=request.args.get("minimum_age_days"),
                sort_by=request.args.get("sort", "largest"),
                page=request.args.get("page", "1"),
                page_size=request.args.get("page_size", "25"),
            )
        except FileTypeIndexLoadError as error:
            return jsonify(ok=False, message=error.detail), error.status_code
        except FileTypeReviewQueryError as error:
            return jsonify(ok=False, message=str(error)), 400
        return jsonify(ok=True, **result)

    @app.post("/storage/<drive>/open-folder")
    def open_storage_folder(drive: str):
        supplied_token = request.form.get("action_token", "")
        expected_token = app.config["STORAGE_ACTION_TOKEN"]
        if not secrets.compare_digest(supplied_token, expected_token):
            return jsonify(
                ok=False,
                message="The local action token is missing or no longer valid.",
            ), 403

        configured_storage_paths = app.config["STORAGE_REPORT_PATHS"]
        if not configured_storage_paths:
            return jsonify(
                ok=False,
                message="No storage analysis is selected.",
            ), 404

        try:
            storage_report, _selected_storage_report_path = (
                load_storage_report_for_drive(configured_storage_paths, drive)
            )
        except StorageReportLoadError as error:
            return jsonify(ok=False, message=error.detail), error.status_code

        candidate_id = request.form.get("candidate_id", "")
        candidate_kind = request.form.get("candidate_kind", "file")
        if candidate_kind not in {"file", "folder"}:
            return jsonify(
                ok=False,
                message="The candidate type is invalid.",
            ), 400
        candidate_collection = (
            storage_report.get("folder_candidates", [])
            if candidate_kind == "folder"
            else storage_report["candidates"]
        )
        candidate = next(
            (
                item
                for item in candidate_collection
                if item["candidate_id"] == candidate_id
            ),
            None,
        )
        if candidate is None:
            return jsonify(
                ok=False,
                message="The selected candidate is not present in this report.",
            ), 404

        try:
            folder = validate_candidate_for_folder_action(
                storage_report,
                candidate,
            )
            app.config["OPEN_STORAGE_FOLDER_HANDLER"](folder)
        except StorageActionError as error:
            return jsonify(ok=False, message=str(error)), 409

        return jsonify(
            ok=True,
            message="The containing folder was opened in Windows.",
        )

    register_storage_cleanup_routes(app)

    return app
