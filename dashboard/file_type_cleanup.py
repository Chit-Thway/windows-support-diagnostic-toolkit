"""Fail-closed Recycle Bin routes for File-Type Explorer selections."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from flask import Flask, render_template, request

from storage.cleanup import (
    CleanupRecordError,
    execute_guided_cleanup,
    write_cleanup_record,
)
from storage.path_policy import ProtectedPathPolicy, is_path_within
from storage.risk import assess_removal_risk

from .cleanup_tokens import CleanupPreviewError, CleanupPreviewExpiredError
from .file_type_index_loader import (
    FileTypeIndexLoadError,
    FileTypeIndexSnapshot,
    load_file_type_index_for_drive,
)
from .storage_presenter import format_bytes

MAX_CLEANUP_SELECTION = 500


def _error_response(title: str, detail: str, status_code: int):
    return (
        render_template(
            "storage_cleanup_error.html",
            error_title=title,
            error_detail=detail,
            status_code=status_code,
        ),
        status_code,
    )


def _valid_action_token(app: Flask) -> bool:
    supplied = request.form.get("action_token", "")
    return secrets.compare_digest(supplied, app.config["STORAGE_ACTION_TOKEN"])


def _load_index(app: Flask, drive: str) -> FileTypeIndexSnapshot:
    configured_paths = app.config["FILE_TYPE_INDEX_PATHS"]
    if not configured_paths:
        raise FileTypeIndexLoadError("No File-Type Explorer index is selected.")
    return load_file_type_index_for_drive(configured_paths, drive)


def _cleanup_report(snapshot: FileTypeIndexSnapshot) -> dict[str, Any]:
    root = snapshot.report["scope"]["root_path"]
    return {
        "generated_at_utc": snapshot.report["generated_at_utc"],
        "drive": snapshot.report["drive"],
        "scan_scope": {
            "roots": [{"requested_path": root, "canonical_path": root}]
        },
    }


def _cleanup_candidate(
    snapshot: FileTypeIndexSnapshot, file_row: dict[str, Any]
) -> dict[str, Any]:
    path = Path(file_row["path"])
    risk = assess_removal_risk(path, ())
    root = snapshot.report["scope"]["root_path"]
    return {
        "candidate_id": file_row["file_id"],
        "item_type": "file",
        "path": file_row["path"],
        "scan_root": root,
        "name": file_row["name"],
        "extension": file_row["extension"],
        "size_bytes": file_row["size_bytes"],
        "allocated_size_bytes": None,
        "modified_at_utc": file_row["modified_at_utc"],
        "attributes": [],
        "evidence": [
            {
                "attribute": "file_type_review",
                "code": "explicit_file_type_selection",
                "description": (
                    f"This exact {file_row['extension']} path was explicitly "
                    "selected in File-Type Explorer."
                ),
                "observed_value": file_row["path"],
            }
        ],
        "confidence": "medium",
        "removal_risk": risk.level,
        "is_regular_file": True,
        "is_reparse_point": False,
        "protection": {
            "eligibility": risk.eligibility,
            "reason_code": risk.reason_code,
            "explanation": risk.explanation,
        },
    }


def _validate_index_selection(
    snapshot: FileTypeIndexSnapshot, file_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    report = snapshot.report
    drive = report["drive"]["drive_letter"].upper()
    root = Path(report["scope"]["root_path"])
    policy = ProtectedPathPolicy()
    selected: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for file_row in file_rows:
        if file_row["selection_state"] != "selectable":
            raise ValueError(
                "Review-only, protected, and unavailable files cannot enter a cleanup preview."
            )
        if file_row["modified_at_utc"] is None:
            raise ValueError(
                "Files without a recorded modification time cannot be safely revalidated."
            )
        path = Path(file_row["path"])
        normalized = str(path).casefold()
        if normalized in seen_paths:
            raise ValueError("The same exact path cannot be selected twice.")
        seen_paths.add(normalized)
        if path.drive.upper() != drive or not is_path_within(path, root):
            raise ValueError("A selected file is outside the indexed drive scope.")
        if policy.is_protected(path, drive):
            raise ValueError("A selected file is now inside a protected location.")
        candidate = _cleanup_candidate(snapshot, file_row)
        if candidate["protection"]["eligibility"] != "eligible":
            raise ValueError(
                "The current removal-risk policy keeps a selected file review-only."
            )
        selected.append(candidate)
    return selected


def _present_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **candidate,
            "allocated_size": format_bytes(candidate["size_bytes"]),
            "attributes_view": [
                {"label": f"{candidate['extension']} file"}
            ],
            "confidence_label": "Medium",
            "removal_risk_label": candidate["removal_risk"].title(),
        }
        for candidate in candidates
    ]


def register_file_type_cleanup_routes(app: Flask) -> None:
    @app.post("/storage/<drive>/file-types/cleanup/preview")
    def file_type_cleanup_preview(drive: str):
        if not _valid_action_token(app):
            return _error_response(
                "Cleanup preview rejected",
                "The local action token is missing or no longer valid.",
                403,
            )
        file_ids = request.form.getlist("file_id")
        if not file_ids:
            return _error_response(
                "No files selected",
                "Return to File-Type Explorer and select exact files to review.",
                400,
            )
        if len(file_ids) > MAX_CLEANUP_SELECTION:
            return _error_response(
                "Selection is too large",
                f"Review no more than {MAX_CLEANUP_SELECTION} files in one operation.",
                400,
            )
        if len(file_ids) != len(set(file_ids)):
            return _error_response(
                "Selection rejected",
                "Duplicate file identifiers were submitted.",
                400,
            )
        try:
            snapshot = _load_index(app, drive)
        except FileTypeIndexLoadError as error:
            return _error_response(error.title, error.detail, error.status_code)

        rows_by_id = {row["file_id"]: row for row in snapshot.report["files"]}
        if any(file_id not in rows_by_id for file_id in file_ids):
            return _error_response(
                "Selection rejected",
                "At least one selected file is not in the current index.",
                400,
            )
        try:
            selected = _validate_index_selection(
                snapshot, [rows_by_id[file_id] for file_id in file_ids]
            )
        except ValueError as error:
            return _error_response("Unsafe selection rejected", str(error), 409)

        preview = app.config["CLEANUP_PREVIEW_STORE"].create(
            drive_letter=drive,
            storage_report_path=snapshot.path,
            source_generated_at_utc=snapshot.report["generated_at_utc"],
            candidates=selected,
            source_kind="file_type_index",
        )
        return render_template(
            "storage_cleanup_preview.html",
            preview=preview,
            candidates=_present_candidates(selected),
            total_size=format_bytes(preview.total_bytes),
            storage_action_token=app.config["STORAGE_ACTION_TOKEN"],
            cleanup_confirm_url=f"/storage/{drive}/file-types/cleanup/confirm",
            cleanup_return_url=f"/storage/{drive}/file-types",
        )

    @app.post("/storage/<drive>/file-types/cleanup/confirm")
    def file_type_cleanup_confirm(drive: str):
        if not _valid_action_token(app):
            return _error_response(
                "Cleanup confirmation rejected",
                "The local action token is missing or no longer valid.",
                403,
            )
        try:
            preview = app.config["CLEANUP_PREVIEW_STORE"].consume(
                request.form.get("preview_token", "")
            )
        except CleanupPreviewExpiredError as error:
            return _error_response("Cleanup preview expired", str(error), 410)
        except CleanupPreviewError as error:
            return _error_response("Cleanup preview unavailable", str(error), 409)
        if preview.drive_letter != drive or preview.source_kind != "file_type_index":
            return _error_response(
                "Cleanup confirmation rejected",
                "The confirmation does not belong to this File-Type Explorer drive.",
                409,
            )
        if request.form.get("confirm_cleanup") != "yes":
            return _error_response(
                "Explicit confirmation required",
                "No files were changed. Create a new preview and confirm the review checkbox.",
                400,
            )
        if (
            preview.requires_additional_confirmation
            and request.form.get("confirmation_phrase", "")
            != preview.confirmation_phrase
        ):
            return _error_response(
                "Additional confirmation did not match",
                "No files were changed. Create a new preview and enter the displayed phrase exactly.",
                400,
            )
        try:
            snapshot = _load_index(app, drive)
        except FileTypeIndexLoadError as error:
            return _error_response(error.title, error.detail, error.status_code)
        if str(snapshot.path.resolve()) != preview.storage_report_path:
            return _error_response(
                "File-Type Explorer index changed",
                "The selected index path changed after review. No files were changed.",
                409,
            )
        if snapshot.report["generated_at_utc"] != preview.source_generated_at_utc:
            return _error_response(
                "File-Type Explorer index changed",
                "The index was replaced after review. No files were changed.",
                409,
            )

        rows_by_id = {row["file_id"]: row for row in snapshot.report["files"]}
        current_rows: list[dict[str, Any]] = []
        for reviewed in preview.candidates:
            row = rows_by_id.get(reviewed["candidate_id"])
            if row is None:
                return _error_response(
                    "Reviewed selection changed",
                    "A reviewed file disappeared from the index. No files were changed.",
                    409,
                )
            current_rows.append(row)
        try:
            selected = _validate_index_selection(snapshot, current_rows)
        except ValueError as error:
            return _error_response("Unsafe selection rejected", str(error), 409)
        if tuple(selected) != preview.candidates:
            return _error_response(
                "Reviewed selection changed",
                "A reviewed file changed in the index. No files were changed.",
                409,
            )

        report = _cleanup_report(snapshot)
        try:
            record = execute_guided_cleanup(
                report,
                selected,
                recycler=app.config["RECYCLE_HANDLER"],
            )
        except CleanupRecordError as error:
            return _error_response("Cleanup failed safely", str(error), 409)
        record_path = None
        record_error = None
        try:
            record_path = write_cleanup_record(
                record, app.config["CLEANUP_RECORD_DIRECTORY"]
            )
        except CleanupRecordError as error:
            record_error = str(error)
        return render_template(
            "storage_cleanup_result.html",
            drive=drive,
            record=record,
            record_path=record_path,
            record_error=record_error,
            requested_size=format_bytes(record["requested_unique_bytes"]),
            cleanup_return_url=f"/storage/{drive}/file-types",
        )
