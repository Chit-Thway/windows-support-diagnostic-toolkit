"""POST-only preview and confirmation routes for guided Recycle Bin cleanup."""

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

from .cleanup_tokens import CleanupPreviewError, CleanupPreviewExpiredError
from .storage_actions import (
    StorageActionError,
    validate_candidate_for_folder_action,
)
from .storage_presenter import format_bytes, present_storage_report
from .storage_report_loader import (
    StorageReportLoadError,
    load_storage_report_for_drive,
)

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
    return secrets.compare_digest(
        supplied,
        app.config["STORAGE_ACTION_TOKEN"],
    )


def _load_configured_report(app: Flask, drive: str) -> tuple[dict[str, Any], Path]:
    configured_paths = app.config["STORAGE_REPORT_PATHS"]
    if not configured_paths:
        raise StorageReportLoadError("No storage analysis is selected.")
    return load_storage_report_for_drive(configured_paths, drive)


def register_storage_cleanup_routes(app: Flask) -> None:
    @app.post("/storage/<drive>/cleanup/preview")
    def storage_cleanup_preview(drive: str):
        if not _valid_action_token(app):
            return _error_response(
                "Cleanup preview rejected",
                "The local action token is missing or no longer valid.",
                403,
            )

        candidate_ids = request.form.getlist("candidate_id")
        candidate_kind = request.form.get("candidate_kind", "file")
        if candidate_kind not in {"file", "folder"}:
            return _error_response(
                "Selection rejected",
                "The cleanup selection type is invalid.",
                400,
            )
        if not candidate_ids:
            return _error_response(
                "No items selected",
                "Return to the candidate explorer and select individual items to review.",
                400,
            )
        if len(candidate_ids) > MAX_CLEANUP_SELECTION:
            return _error_response(
                "Selection is too large",
                f"Review no more than {MAX_CLEANUP_SELECTION} items in one operation.",
                400,
            )
        if len(candidate_ids) != len(set(candidate_ids)):
            return _error_response(
                "Selection rejected",
                "Duplicate candidate identifiers were submitted.",
                400,
            )

        try:
            report, report_path = _load_configured_report(app, drive)
        except StorageReportLoadError as error:
            return _error_response(error.title, error.detail, error.status_code)

        source_candidates = (
            report.get("folder_candidates", [])
            if candidate_kind == "folder"
            else report["candidates"]
        )
        candidate_map = {
            candidate["candidate_id"]: candidate
            for candidate in source_candidates
        }
        if any(candidate_id not in candidate_map for candidate_id in candidate_ids):
            return _error_response(
                "Selection rejected",
                "At least one selected identifier is not present in the current report.",
                400,
            )

        selected = [candidate_map[candidate_id] for candidate_id in candidate_ids]
        if any(
            candidate["protection"]["eligibility"] != "eligible"
            for candidate in selected
        ):
            return _error_response(
                "Unsafe selection rejected",
                "Review-only, protected, and unavailable items cannot enter a cleanup preview.",
                409,
            )
        try:
            for candidate in selected:
                validate_candidate_for_folder_action(report, candidate)
        except StorageActionError as error:
            return _error_response(
                "Unsafe selection rejected",
                str(error),
                409,
            )

        preview = app.config["CLEANUP_PREVIEW_STORE"].create(
            drive_letter=drive,
            storage_report_path=report_path,
            source_generated_at_utc=report["generated_at_utc"],
            candidates=selected,
        )
        presented = present_storage_report(report, diagnostic_status="Unavailable")
        presented_candidates = (
            presented["folder_candidates"]
            if candidate_kind == "folder"
            else presented["candidates"]
        )
        presented_by_id = {
            candidate["candidate_id"]: candidate
            for candidate in presented_candidates
        }
        preview_candidates = [
            presented_by_id[candidate_id] for candidate_id in candidate_ids
        ]
        return render_template(
            "storage_cleanup_preview.html",
            preview=preview,
            candidates=preview_candidates,
            total_size=format_bytes(preview.total_bytes),
            storage_action_token=app.config["STORAGE_ACTION_TOKEN"],
        )

    @app.post("/storage/<drive>/cleanup/confirm")
    def storage_cleanup_confirm(drive: str):
        if not _valid_action_token(app):
            return _error_response(
                "Cleanup confirmation rejected",
                "The local action token is missing or no longer valid.",
                403,
            )

        preview_token = request.form.get("preview_token", "")
        try:
            preview = app.config["CLEANUP_PREVIEW_STORE"].consume(preview_token)
        except CleanupPreviewExpiredError as error:
            return _error_response("Cleanup preview expired", str(error), 410)
        except CleanupPreviewError as error:
            return _error_response("Cleanup preview unavailable", str(error), 409)

        if preview.drive_letter != drive:
            return _error_response(
                "Cleanup confirmation rejected",
                "The confirmation does not belong to this drive.",
                409,
            )
        if request.form.get("confirm_cleanup") != "yes":
            return _error_response(
                "Explicit confirmation required",
                "No items were changed. Create a new preview and confirm the review checkbox.",
                400,
            )
        if (
            preview.requires_additional_confirmation
            and request.form.get("confirmation_phrase", "")
            != preview.confirmation_phrase
        ):
            return _error_response(
                "Additional confirmation did not match",
                "No items were changed. Create a new preview and enter the displayed phrase exactly.",
                400,
            )

        try:
            report, report_path = _load_configured_report(app, drive)
        except StorageReportLoadError as error:
            return _error_response(error.title, error.detail, error.status_code)
        if str(report_path.resolve()) != preview.storage_report_path:
            return _error_response(
                "Storage report changed",
                "The selected storage report path changed after review. No items were changed.",
                409,
            )
        if report["generated_at_utc"] != preview.source_generated_at_utc:
            return _error_response(
                "Storage report changed",
                "The storage report was replaced after review. No items were changed.",
                409,
            )

        source_candidates = (
            report.get("folder_candidates", [])
            if preview.candidate_kind == "folder"
            else report["candidates"]
        )
        current_candidates = {
            candidate["candidate_id"]: candidate
            for candidate in source_candidates
        }
        selected: list[dict[str, Any]] = []
        for reviewed in preview.candidates:
            current = current_candidates.get(reviewed["candidate_id"])
            if current is None or current != reviewed:
                return _error_response(
                    "Reviewed selection changed",
                    "A reviewed candidate changed in the report. No items were changed.",
                    409,
                )
            selected.append(current)

        record = execute_guided_cleanup(
            report,
            selected,
            recycler=app.config["RECYCLE_HANDLER"],
        )
        record_path = None
        record_error = None
        try:
            record_path = write_cleanup_record(
                record,
                app.config["CLEANUP_RECORD_DIRECTORY"],
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
        )
