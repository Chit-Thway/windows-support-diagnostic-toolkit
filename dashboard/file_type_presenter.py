"""Present validated File-Type Explorer index data to local routes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .file_type_index_loader import FileTypeIndexSnapshot
from .storage_presenter import format_bytes

ACCESS_PRESENTATION = {
    "normal": ("Analyzed", "healthy"),
    "review_only": ("Review only", "warning"),
    "protected": ("Protected", "unavailable"),
    "unavailable": ("Unavailable", "unavailable"),
}


def _natural_key(value: str) -> tuple[Any, ...]:
    return tuple(
        int(token) if token.isdigit() else token.casefold()
        for token in re.split(r"(\d+)", value)
    )


def _extension_bytes(folder: dict[str, Any]) -> dict[str, int]:
    return {
        total["extension"]: total["recursive_logical_bytes"]
        for total in folder["extension_totals"]
    }


def present_file_type_folder(
    snapshot: FileTypeIndexSnapshot,
    folder: dict[str, Any],
) -> dict[str, Any]:
    """Return one safely displayable folder-tree record."""

    access_label, access_class = ACCESS_PRESENTATION[folder["access"]["state"]]
    children = snapshot.children_by_parent.get(folder["folder_id"], ())
    return {
        "folder_id": folder["folder_id"],
        "parent_id": folder["parent_id"],
        "path": folder["path"],
        "name": folder["name"],
        "depth": folder["depth"],
        "total_bytes": folder["recursive_logical_bytes"],
        "total_size": format_bytes(folder["recursive_logical_bytes"]),
        "extension_bytes": _extension_bytes(folder),
        "file_count": folder["recursive_file_count"],
        "zero_byte_files": folder["recursive_zero_byte_files"],
        "has_children": bool(children),
        "child_count": len(children),
        "access_state": folder["access"]["state"],
        "access_label": access_label,
        "access_class": access_class,
        "access_explanation": folder["access"]["explanation"],
        "scope_selectable": folder["access"]["state"] != "unavailable",
    }


def present_file_type_children(
    snapshot: FileTypeIndexSnapshot,
    parent_id: str,
) -> list[dict[str, Any]]:
    """Return one child level ranked by total observed logical size."""

    children = snapshot.children_by_parent.get(parent_id, ())
    ordered = sorted(
        children,
        key=lambda folder: (
            -folder["recursive_logical_bytes"],
            _natural_key(folder["name"]),
            folder["path"].casefold(),
        ),
    )
    return [present_file_type_folder(snapshot, folder) for folder in ordered]


def present_file_type_index(
    snapshot: FileTypeIndexSnapshot,
) -> dict[str, Any]:
    """Prepare the dedicated Method 1 page without exposing file rows."""

    report = snapshot.report
    scan = report["scan"]
    status_label = scan["status"].replace("_", " ").title()
    status_class = {
        "complete": "healthy",
        "partial": "warning",
        "cancelled": "warning",
        "failed": "problem",
    }[scan["status"]]
    extension_groups = [
        {
            "group_id": group["group_id"],
            "label": group["label"],
            "extensions": group["extensions"],
        }
        for group in report["extension_groups"]
    ]
    if report["custom_extensions"]:
        extension_groups.append(
            {
                "group_id": "custom_indexed",
                "label": "Custom indexed extensions",
                "extensions": report["custom_extensions"],
            }
        )

    issue_count = (
        scan["inaccessible_path_details_retained"]
        + scan["inaccessible_path_details_omitted"]
        + scan["scan_error_details_retained"]
        + scan["scan_error_details_omitted"]
    )
    return {
        "schema_version": report["schema_version"],
        "index_id": report["index_id"],
        "generated_at_utc": report["generated_at_utc"],
        "index_filename": Path(snapshot.path).name,
        "drive": report["drive"],
        "scan": {
            **scan,
            "status_label": status_label,
            "status_class": status_class,
            "coverage_label": scan["aggregate_coverage"].title(),
            "duration_seconds": round(scan["duration_ms"] / 1000, 2),
            "files_display": f"{scan['files_examined']:,}",
            "directories_display": f"{scan['directories_examined']:,}",
            "matching_files_display": f"{scan['matching_files']:,}",
            "matching_size": format_bytes(scan["matching_logical_bytes"]),
            "issue_count": issue_count,
        },
        "extension_groups": extension_groups,
        "custom_extension_count": len(report["custom_extensions"]),
        "root": present_file_type_folder(snapshot, snapshot.root),
        "file_detail_summary": report["file_detail_summary"],
        "limitations": report["limitations"],
    }
