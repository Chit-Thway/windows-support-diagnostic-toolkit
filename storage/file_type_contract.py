"""Validation for the versioned File-Type Explorer index contract."""

from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "schema" / "file-type-index.schema.json"
SUPPORTED_FILE_TYPE_INDEX_VERSION = "1.0.0"
PRESET_EXTENSION_GROUPS = {
    "documents": (
        "Documents",
        (".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".ppt", ".pptx", ".xls", ".xlsx"),
    ),
    "videos": ("Videos", (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm")),
    "audio": ("Audio", (".mp3", ".wav", ".flac", ".m4a", ".aac")),
    "images": ("Images", (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic")),
    "archives_disk_images": (
        "Archives and disk images",
        (".zip", ".rar", ".7z", ".tar", ".gz", ".iso"),
    ),
    "installers": ("Installers", (".exe", ".msi", ".msix", ".appx")),
}


class FileTypeIndexValidationError(ValueError):
    """Raised when a File-Type Explorer index violates its contract."""


@lru_cache(maxsize=4)
def _load_schema(schema_path: str) -> dict[str, Any]:
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FileTypeIndexValidationError(
            "The File-Type Explorer schema could not be read."
        ) from error

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise FileTypeIndexValidationError(
            "The File-Type Explorer schema is invalid."
        ) from error
    return schema


def _validation_location(error: Any) -> str:
    if not error.absolute_path:
        return "the index root"
    return ".".join(str(part) for part in error.absolute_path)


def _normal_path(value: str) -> str:
    return str(PureWindowsPath(value)).rstrip("\\").casefold()


def _normal_extension(value: str) -> str:
    return value.casefold()


def _is_within(path: str, root: str) -> bool:
    path_parts = PureWindowsPath(path).parts
    root_parts = PureWindowsPath(root).parts
    if len(path_parts) < len(root_parts):
        return False
    return tuple(part.casefold() for part in path_parts[: len(root_parts)]) == tuple(
        part.casefold() for part in root_parts
    )


def validate_non_overlapping_scopes(
    scopes: list[str] | tuple[str, ...], drive_root: str
) -> tuple[str, ...]:
    """Validate selected folder scopes without touching the filesystem."""

    root = PureWindowsPath(drive_root)
    normalized: list[PureWindowsPath] = []
    normalized_keys: set[str] = set()
    for value in scopes:
        path = PureWindowsPath(value)
        if not _is_within(str(path), str(root)):
            raise FileTypeIndexValidationError(
                "Selected folders must remain on the active indexed drive."
            )
        key = _normal_path(str(path))
        if key in normalized_keys:
            raise FileTypeIndexValidationError(
                "Selected folder scopes must be unique."
            )
        normalized_keys.add(key)
        normalized.append(path)

    for index, left in enumerate(normalized):
        left_parts = tuple(part.casefold() for part in left.parts)
        for right in normalized[index + 1 :]:
            right_parts = tuple(part.casefold() for part in right.parts)
            shorter = min(len(left_parts), len(right_parts))
            if left_parts[:shorter] == right_parts[:shorter]:
                raise FileTypeIndexValidationError(
                    "Parent and child folder scopes cannot be selected together."
                )
    return tuple(str(path) for path in normalized)


def _validate_extensions(index: dict[str, Any]) -> set[str]:
    group_ids: set[str] = set()
    indexed: set[str] = set()
    for group in index["extension_groups"]:
        group_id = group["group_id"]
        if group_id in group_ids:
            raise FileTypeIndexValidationError(
                "Extension group identifiers must be unique."
            )
        group_ids.add(group_id)
        for extension in group["extensions"]:
            normalized = _normal_extension(extension)
            if extension != normalized:
                raise FileTypeIndexValidationError(
                    "Indexed extensions must use lowercase spelling."
                )
            if normalized in indexed:
                raise FileTypeIndexValidationError(
                    "An extension cannot appear in more than one preset group."
                )
            indexed.add(normalized)

    actual_groups = {
        group["group_id"]: (group["label"], tuple(group["extensions"]))
        for group in index["extension_groups"]
    }
    if actual_groups != PRESET_EXTENSION_GROUPS:
        raise FileTypeIndexValidationError(
            "The index must declare the complete versioned preset catalog."
        )

    for extension in index["custom_extensions"]:
        normalized = _normal_extension(extension)
        if extension != normalized:
            raise FileTypeIndexValidationError(
                "Custom extensions must use lowercase spelling."
            )
        if normalized in indexed:
            raise FileTypeIndexValidationError(
                "Custom extensions cannot duplicate preset extensions."
            )
        indexed.add(normalized)
    return indexed


def _extension_map(folder: dict[str, Any]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for total in folder["extension_totals"]:
        extension = _normal_extension(total["extension"])
        if extension in totals:
            raise FileTypeIndexValidationError(
                "Folder extension totals must be unique per extension."
            )
        if total["direct_file_count"] > total["recursive_file_count"]:
            raise FileTypeIndexValidationError(
                "Direct extension counts cannot exceed recursive counts."
            )
        if total["direct_logical_bytes"] > total["recursive_logical_bytes"]:
            raise FileTypeIndexValidationError(
                "Direct extension bytes cannot exceed recursive bytes."
            )
        totals[extension] = total
    return totals


def _validate_folders(
    index: dict[str, Any], indexed_extensions: set[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    folders = index["folders"]
    by_id = {folder["folder_id"]: folder for folder in folders}
    if len(by_id) != len(folders):
        raise FileTypeIndexValidationError("Folder identifiers must be unique.")

    normalized_paths = [_normal_path(folder["path"]) for folder in folders]
    if len(normalized_paths) != len(set(normalized_paths)):
        raise FileTypeIndexValidationError("Folder paths must be unique.")

    roots = [folder for folder in folders if folder["parent_id"] is None]
    if len(roots) != 1:
        raise FileTypeIndexValidationError(
            "A per-drive index requires exactly one root folder."
        )
    root = roots[0]
    scope_root = index["scope"]["root_path"]
    if _normal_path(root["path"]) != _normal_path(scope_root):
        raise FileTypeIndexValidationError(
            "The root folder must match the explicitly requested scope."
        )
    if root["depth"] != 0:
        raise FileTypeIndexValidationError("The root folder depth must be zero.")

    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    extension_maps: dict[str, dict[str, Any]] = {}
    for folder in folders:
        if not _is_within(folder["path"], scope_root):
            raise FileTypeIndexValidationError(
                "Every folder must remain inside the indexed drive scope."
            )
        if folder["direct_file_count"] > folder["recursive_file_count"]:
            raise FileTypeIndexValidationError(
                "Direct folder counts cannot exceed recursive counts."
            )
        if folder["direct_logical_bytes"] > folder["recursive_logical_bytes"]:
            raise FileTypeIndexValidationError(
                "Direct folder bytes cannot exceed recursive bytes."
            )
        if folder["direct_zero_byte_files"] > folder["recursive_zero_byte_files"]:
            raise FileTypeIndexValidationError(
                "Direct empty-file counts cannot exceed recursive counts."
            )
        extension_map = _extension_map(folder)
        if not set(extension_map).issubset(indexed_extensions):
            raise FileTypeIndexValidationError(
                "Folder totals can contain indexed extensions only."
            )
        extension_maps[folder["folder_id"]] = extension_map

        parent_id = folder["parent_id"]
        if parent_id is None:
            continue
        parent = by_id.get(parent_id)
        if parent is None:
            raise FileTypeIndexValidationError(
                "Every non-root folder requires an existing parent."
            )
        if folder["depth"] != parent["depth"] + 1:
            raise FileTypeIndexValidationError(
                "Folder depth must be exactly one greater than its parent."
            )
        if PureWindowsPath(folder["path"]).parent != PureWindowsPath(
            parent["path"]
        ):
            raise FileTypeIndexValidationError(
                "Folder paths must identify direct parent-child relationships."
            )
        children[parent_id].append(folder)

    for folder in sorted(folders, key=lambda item: item["depth"], reverse=True):
        folder_children = children[folder["folder_id"]]
        expected_files = folder["direct_file_count"] + sum(
            child["recursive_file_count"] for child in folder_children
        )
        expected_bytes = folder["direct_logical_bytes"] + sum(
            child["recursive_logical_bytes"] for child in folder_children
        )
        expected_empty = folder["direct_zero_byte_files"] + sum(
            child["recursive_zero_byte_files"] for child in folder_children
        )
        if folder["recursive_file_count"] != expected_files:
            raise FileTypeIndexValidationError(
                "Recursive folder file counts must equal direct plus child totals."
            )
        if folder["recursive_logical_bytes"] != expected_bytes:
            raise FileTypeIndexValidationError(
                "Recursive folder bytes must equal direct plus child totals."
            )
        if folder["recursive_zero_byte_files"] != expected_empty:
            raise FileTypeIndexValidationError(
                "Recursive empty-file counts must equal direct plus child totals."
            )

        current_extensions = extension_maps[folder["folder_id"]]
        child_extensions = {
            extension
            for child in folder_children
            for extension in extension_maps[child["folder_id"]]
        }
        for extension in set(current_extensions).union(child_extensions):
            total = current_extensions.get(extension)
            expected_count = (total["direct_file_count"] if total else 0) + sum(
                extension_maps[child["folder_id"]]
                .get(extension, {})
                .get("recursive_file_count", 0)
                for child in folder_children
            )
            expected_size = (total["direct_logical_bytes"] if total else 0) + sum(
                extension_maps[child["folder_id"]]
                .get(extension, {})
                .get("recursive_logical_bytes", 0)
                for child in folder_children
            )
            if total is None or total["recursive_file_count"] != expected_count:
                raise FileTypeIndexValidationError(
                    "Recursive extension counts must equal direct plus child totals."
                )
            if total["recursive_logical_bytes"] != expected_size:
                raise FileTypeIndexValidationError(
                    "Recursive extension bytes must equal direct plus child totals."
                )
    return by_id, extension_maps


def _validate_files(
    index: dict[str, Any],
    folders: dict[str, dict[str, Any]],
    extension_maps: dict[str, dict[str, Any]],
    indexed_extensions: set[str],
) -> None:
    files = index["files"]
    file_ids = [file["file_id"] for file in files]
    if len(file_ids) != len(set(file_ids)):
        raise FileTypeIndexValidationError("File identifiers must be unique.")
    file_paths = [_normal_path(file["path"]) for file in files]
    if len(file_paths) != len(set(file_paths)):
        raise FileTypeIndexValidationError("File paths must be unique.")

    retained_by_folder: dict[tuple[str, str], tuple[int, int]] = defaultdict(
        lambda: (0, 0)
    )
    for file in files:
        folder = folders.get(file["folder_id"])
        if folder is None:
            raise FileTypeIndexValidationError(
                "Every retained file requires an existing folder."
            )
        if PureWindowsPath(file["path"]).parent != PureWindowsPath(folder["path"]):
            raise FileTypeIndexValidationError(
                "A retained file path must be directly inside its folder."
            )
        extension = _normal_extension(file["extension"])
        if file["extension"] != extension or extension not in indexed_extensions:
            raise FileTypeIndexValidationError(
                "Retained files require a lowercase indexed extension."
            )
        if PureWindowsPath(file["path"]).suffix.casefold() != extension:
            raise FileTypeIndexValidationError(
                "Retained file extensions must match their paths."
            )
        folder_state = folder["access"]["state"]
        if folder_state == "review_only" and file["selection_state"] == "selectable":
            raise FileTypeIndexValidationError(
                "Files in review-only folders cannot be directly selectable."
            )
        if folder_state in {"protected", "unavailable"} and file[
            "selection_state"
        ] != "protected":
            raise FileTypeIndexValidationError(
                "Files in protected or unavailable folders must remain protected."
            )
        current_count, current_bytes = retained_by_folder[(file["folder_id"], extension)]
        retained_by_folder[(file["folder_id"], extension)] = (
            current_count + 1,
            current_bytes + file["size_bytes"],
        )

    summary = index["file_detail_summary"]
    retained_bytes = sum(file["size_bytes"] for file in files)
    if summary["retained_files"] != len(files):
        raise FileTypeIndexValidationError(
            "Retained file-detail count must match the inline rows."
        )
    if summary["retained_logical_bytes"] != retained_bytes:
        raise FileTypeIndexValidationError(
            "Retained file-detail bytes must match the inline rows."
        )
    if summary["coverage"] == "complete" and (
        summary["omitted_files"] or summary["omitted_logical_bytes"]
    ):
        raise FileTypeIndexValidationError(
            "Complete file details cannot declare omitted rows or bytes."
        )
    if summary["coverage"] == "bounded" and not summary["omitted_files"]:
        raise FileTypeIndexValidationError(
            "Bounded file details must declare at least one omitted row."
        )

    for (folder_id, extension), (retained_count, retained_bytes) in retained_by_folder.items():
        total = extension_maps[folder_id].get(extension)
        if total is None:
            raise FileTypeIndexValidationError(
                "Retained files require matching folder extension totals."
            )
        if retained_count > total["direct_file_count"] or retained_bytes > total[
            "direct_logical_bytes"
        ]:
            raise FileTypeIndexValidationError(
                "Retained file details cannot exceed direct extension totals."
            )


def _validate_empty_summary(
    index: dict[str, Any], folders: dict[str, dict[str, Any]]
) -> None:
    summary = index["empty_summary"]
    if summary["zero_byte_files"] != index["scan"]["zero_byte_files"]:
        raise FileTypeIndexValidationError(
            "The empty summary must match the scan zero-byte count."
        )
    if summary["collapsed_tree_count"] != len(summary["trees"]):
        raise FileTypeIndexValidationError(
            "Collapsed empty-tree count must match the retained summaries."
        )
    tree_paths: set[str] = set()
    for tree in summary["trees"]:
        folder = folders.get(tree["highest_folder_id"])
        if folder is None:
            raise FileTypeIndexValidationError(
                "Every collapsed empty tree requires an existing highest folder."
            )
        normalized_path = _normal_path(tree["path"])
        if normalized_path != _normal_path(folder["path"]):
            raise FileTypeIndexValidationError(
                "Collapsed empty-tree paths must match their highest folders."
            )
        if normalized_path in tree_paths:
            raise FileTypeIndexValidationError(
                "Collapsed empty-tree paths must be unique."
            )
        tree_paths.add(normalized_path)
        if not tree["descendant_zero_byte_files"] and not tree[
            "descendant_directories"
        ]:
            raise FileTypeIndexValidationError(
                "An empty-tree summary must describe at least one empty item."
            )
        if tree["descendant_zero_byte_files"] > folder[
            "recursive_zero_byte_files"
        ]:
            raise FileTypeIndexValidationError(
                "Collapsed empty-tree file counts cannot exceed folder totals."
            )
        if folder["recursive_logical_bytes"] != 0:
            raise FileTypeIndexValidationError(
                "Collapsed empty trees cannot contain non-empty logical data."
            )


def _validate_semantics(index: dict[str, Any]) -> None:
    drive = index["drive"]
    if drive["total_bytes"] != drive["used_bytes"] + drive["free_bytes"]:
        raise FileTypeIndexValidationError(
            "Drive total_bytes must equal used_bytes plus free_bytes."
        )
    scope_root = PureWindowsPath(index["scope"]["root_path"])
    if scope_root.drive.upper() != drive["drive_letter"]:
        raise FileTypeIndexValidationError(
            "The indexed scope must belong to the declared drive."
        )

    indexed_extensions = _validate_extensions(index)
    folders, extension_maps = _validate_folders(index, indexed_extensions)
    _validate_files(index, folders, extension_maps, indexed_extensions)
    _validate_empty_summary(index, folders)

    root = next(folder for folder in folders.values() if folder["parent_id"] is None)
    scan = index["scan"]
    root_extensions = extension_maps[root["folder_id"]]
    matching_files = sum(
        total["recursive_file_count"] for total in root_extensions.values()
    )
    matching_bytes = sum(
        total["recursive_logical_bytes"] for total in root_extensions.values()
    )
    if scan["files_examined"] != root["recursive_file_count"]:
        raise FileTypeIndexValidationError(
            "Scan files_examined must match the root recursive file count."
        )
    if scan["logical_bytes_observed"] != root["recursive_logical_bytes"]:
        raise FileTypeIndexValidationError(
            "Scan observed bytes must match the root recursive bytes."
        )
    if scan["zero_byte_files"] != root["recursive_zero_byte_files"]:
        raise FileTypeIndexValidationError(
            "Scan zero-byte count must match the root recursive summary."
        )
    if scan["matching_files"] != matching_files:
        raise FileTypeIndexValidationError(
            "Scan matching count must equal root extension totals."
        )
    if scan["matching_logical_bytes"] != matching_bytes:
        raise FileTypeIndexValidationError(
            "Scan matching bytes must equal root extension totals."
        )

    detail_summary = index["file_detail_summary"]
    if detail_summary["retained_files"] + detail_summary["omitted_files"] != matching_files:
        raise FileTypeIndexValidationError(
            "Retained plus omitted file details must equal matching files."
        )
    if (
        detail_summary["retained_logical_bytes"]
        + detail_summary["omitted_logical_bytes"]
        != matching_bytes
    ):
        raise FileTypeIndexValidationError(
            "Retained plus omitted file bytes must equal matching bytes."
        )

    issue_counts = (
        (
            "inaccessible_path_details_retained",
            "inaccessible_path_details_omitted",
            index["inaccessible_paths"],
        ),
        (
            "scan_error_details_retained",
            "scan_error_details_omitted",
            index["scan_errors"],
        ),
    )
    for retained_name, omitted_name, rows in issue_counts:
        if scan[retained_name] != len(rows):
            raise FileTypeIndexValidationError(
                "Retained issue-detail counts must match their rows."
            )
        if scan[omitted_name] and scan["status"] == "complete":
            raise FileTypeIndexValidationError(
                "A complete scan cannot omit issue-detail records."
            )

    if scan["status"] == "complete" and (
        scan["aggregate_coverage"] != "exact"
        or index["inaccessible_paths"]
        or index["scan_errors"]
    ):
        raise FileTypeIndexValidationError(
            "A complete scan requires exact aggregates and no collection errors."
        )
    if scan["status"] != "complete" and scan["aggregate_coverage"] == "exact":
        raise FileTypeIndexValidationError(
            "An incomplete scan cannot claim exact aggregate coverage."
        )


def validate_file_type_index(
    index: dict[str, Any],
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
) -> None:
    """Validate schema shape and cross-field accounting invariants."""

    schema = _load_schema(str(Path(schema_path).resolve()))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(index),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        raise FileTypeIndexValidationError(
            f"Validation failed at {_validation_location(first)}: {first.message}"
        )
    _validate_semantics(index)
