"""Filter and present retained File-Type Explorer file metadata."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import PureWindowsPath
from typing import Any

from storage.file_type_contract import (
    FileTypeIndexValidationError,
    validate_non_overlapping_scopes,
)

from .file_type_index_loader import FileTypeIndexSnapshot
from .storage_presenter import format_bytes

FILE_SCOPE_MODES = frozenset({"direct", "recursive"})
FILE_SORT_OPTIONS = frozenset(
    {"largest", "smallest", "oldest", "newest", "name", "path"}
)
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class FileTypeReviewQueryError(ValueError):
    """Raised when a local file-review query is invalid."""


def _natural_key(value: str) -> tuple[Any, ...]:
    return tuple(
        int(token) if token.isdigit() else token.casefold()
        for token in re.split(r"(\d+)", value)
    )


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normal_path(value: str) -> str:
    return str(PureWindowsPath(value)).rstrip("\\").casefold()


def _is_descendant_or_same(path: str, scope: str) -> bool:
    path_parts = tuple(part.casefold() for part in PureWindowsPath(path).parts)
    scope_parts = tuple(part.casefold() for part in PureWindowsPath(scope).parts)
    return len(path_parts) >= len(scope_parts) and path_parts[: len(scope_parts)] == scope_parts


def _positive_integer(value: Any, name: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise FileTypeReviewQueryError(f"{name} must be a whole number.") from error
    if parsed < 1:
        raise FileTypeReviewQueryError(f"{name} must be at least 1.")
    return parsed


def _non_negative_integer(value: Any, name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise FileTypeReviewQueryError(f"{name} must be a whole number.") from error
    if parsed < 0:
        raise FileTypeReviewQueryError(f"{name} cannot be negative.")
    return parsed


def _validate_scope_ids(
    snapshot: FileTypeIndexSnapshot,
    folder_ids: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    if not folder_ids:
        raise FileTypeReviewQueryError("Choose at least one folder review scope.")
    if len(folder_ids) > 100:
        raise FileTypeReviewQueryError("At most 100 folder scopes can be reviewed together.")

    folders: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder_id in folder_ids:
        if folder_id in seen:
            continue
        seen.add(folder_id)
        folder = snapshot.folders_by_id.get(folder_id)
        if folder is None:
            raise FileTypeReviewQueryError(
                "A selected folder is not present in this drive index."
            )
        if folder["access"]["state"] == "unavailable":
            raise FileTypeReviewQueryError(
                "Unavailable folders cannot be used as review scopes."
            )
        folders.append(folder)

    try:
        validate_non_overlapping_scopes(
            [folder["path"] for folder in folders],
            snapshot.root["path"],
        )
    except FileTypeIndexValidationError as error:
        raise FileTypeReviewQueryError(str(error)) from error
    return folders


def _present_file(file: dict[str, Any], age_days: int | None) -> dict[str, Any]:
    return {
        "file_id": file["file_id"],
        "folder_id": file["folder_id"],
        "path": file["path"],
        "directory": str(PureWindowsPath(file["path"]).parent),
        "name": file["name"],
        "extension": file["extension"],
        "size_bytes": file["size_bytes"],
        "size": format_bytes(file["size_bytes"]),
        "modified_at_utc": file["modified_at_utc"],
        "age_days": age_days,
        "selection_state": file["selection_state"],
        "selection_label": file["selection_state"].replace("_", " ").title(),
        "selectable": file["selection_state"] == "selectable",
        "protection_reason": file["protection_reason"],
    }


def query_file_type_files(
    snapshot: FileTypeIndexSnapshot,
    *,
    folder_ids: list[str] | tuple[str, ...],
    extensions: list[str] | tuple[str, ...],
    scope_mode: str = "recursive",
    filename: str = "",
    minimum_size_bytes: int | str | None = None,
    minimum_age_days: int | str | None = None,
    sort_by: str = "largest",
    page: int | str = 1,
    page_size: int | str = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """Return one deterministic page of retained matching-file metadata."""

    scopes = _validate_scope_ids(snapshot, folder_ids)
    if scope_mode not in FILE_SCOPE_MODES:
        raise FileTypeReviewQueryError("Scope mode must be direct or recursive.")
    if sort_by not in FILE_SORT_OPTIONS:
        raise FileTypeReviewQueryError("The selected file sort is not supported.")

    normalized_extensions = tuple(
        dict.fromkeys(extension.casefold() for extension in extensions if extension)
    )
    if not normalized_extensions:
        raise FileTypeReviewQueryError("Choose at least one indexed file extension.")
    unsupported = set(normalized_extensions).difference(snapshot.indexed_extensions)
    if unsupported:
        raise FileTypeReviewQueryError(
            "Every selected extension must exist in the loaded index."
        )

    minimum_size = _non_negative_integer(minimum_size_bytes, "Minimum size")
    minimum_age = _non_negative_integer(minimum_age_days, "Minimum age")
    page_number = _positive_integer(page, "Page", 1)
    selected_page_size = _positive_integer(page_size, "Page size", DEFAULT_PAGE_SIZE)
    if selected_page_size > MAX_PAGE_SIZE:
        raise FileTypeReviewQueryError(
            f"Page size cannot exceed {MAX_PAGE_SIZE} rows."
        )

    generated_at = _parse_utc(snapshot.report["generated_at_utc"])
    filename_filter = filename.strip().casefold()
    scope_paths = [folder["path"] for folder in scopes]
    scope_ids = {folder["folder_id"] for folder in scopes}
    deduplicated: dict[str, tuple[dict[str, Any], int | None, datetime | None]] = {}

    for file in snapshot.report["files"]:
        if file["extension"] not in normalized_extensions:
            continue
        folder = snapshot.folders_by_id[file["folder_id"]]
        in_scope = (
            file["folder_id"] in scope_ids
            if scope_mode == "direct"
            else any(_is_descendant_or_same(folder["path"], path) for path in scope_paths)
        )
        if not in_scope:
            continue
        if filename_filter and filename_filter not in file["name"].casefold():
            continue
        if minimum_size is not None and file["size_bytes"] < minimum_size:
            continue

        modified = _parse_utc(file["modified_at_utc"])
        age_days = (
            max(0, (generated_at - modified).days)
            if generated_at is not None and modified is not None
            else None
        )
        if minimum_age is not None and (age_days is None or age_days < minimum_age):
            continue
        deduplicated.setdefault(_normal_path(file["path"]), (file, age_days, modified))

    rows = list(deduplicated.values())
    natural_name = lambda row: (_natural_key(row[0]["name"]), _natural_key(row[0]["path"]))
    if sort_by == "largest":
        rows.sort(key=lambda row: (-row[0]["size_bytes"], *natural_name(row)))
    elif sort_by == "smallest":
        rows.sort(key=lambda row: (row[0]["size_bytes"], *natural_name(row)))
    elif sort_by == "oldest":
        rows.sort(
            key=lambda row: (
                row[2] is None,
                row[2] or datetime.max.replace(tzinfo=timezone.utc),
                *natural_name(row),
            )
        )
    elif sort_by == "newest":
        rows.sort(
            key=lambda row: (
                row[2] is None,
                -(row[2].timestamp()) if row[2] is not None else 0,
                *natural_name(row),
            )
        )
    elif sort_by == "name":
        rows.sort(key=natural_name)
    else:
        rows.sort(key=lambda row: _natural_key(row[0]["path"]))

    total_rows = len(rows)
    total_bytes = sum(row[0]["size_bytes"] for row in rows)
    total_pages = max(1, math.ceil(total_rows / selected_page_size))
    page_number = min(page_number, total_pages)
    start = (page_number - 1) * selected_page_size
    page_rows = rows[start : start + selected_page_size]
    detail_summary = snapshot.report["file_detail_summary"]

    return {
        "files": [_present_file(file, age_days) for file, age_days, _ in page_rows],
        "matching_count": total_rows,
        "matching_bytes": total_bytes,
        "matching_size": format_bytes(total_bytes),
        "page": page_number,
        "page_size": selected_page_size,
        "total_pages": total_pages,
        "first_row": start + 1 if total_rows else 0,
        "last_row": min(start + selected_page_size, total_rows),
        "detail_coverage": detail_summary["coverage"],
        "omitted_files": detail_summary["omitted_files"],
        "omitted_logical_bytes": detail_summary["omitted_logical_bytes"],
        "omitted_size": format_bytes(detail_summary["omitted_logical_bytes"]),
        "scope_mode": scope_mode,
        "sort_by": sort_by,
    }
