"""Fail-closed guided cleanup through the Windows Recycle Bin."""

from __future__ import annotations

import json
import os
import stat
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

from .folders import (
    directory_fingerprint_part,
    file_fingerprint_part,
    metadata_tree_fingerprint,
)
from .path_policy import ProtectedPathPolicy, is_path_within, is_reparse_point
from .risk import assess_folder_removal_risk, assess_removal_risk
from .windows_metadata import get_allocated_size

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLEANUP_SCHEMA_PATH = PROJECT_ROOT / "schema" / "cleanup-record.schema.json"
DEFAULT_CLEANUP_RECORD_DIRECTORY = PROJECT_ROOT / "cleanup-records"
CLEANUP_RECORD_SCHEMA_VERSION = "1.0.0"

RESULT_STATUSES = (
    "recycled",
    "skipped_changed",
    "skipped_protected_or_invalid",
    "missing",
    "failed",
)


class CleanupRecordError(RuntimeError):
    """Raised when a local cleanup result cannot be validated or recorded."""


class RecycleUnavailableError(RuntimeError):
    """Raised when the operating-system Recycle Bin action is unavailable."""


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result(
    candidate: dict[str, Any], status: str, message: str
) -> dict[str, Any]:
    return {
        "candidate_id": candidate["candidate_id"],
        "item_type": candidate.get("item_type", "file"),
        "path": candidate["path"],
        "expected_size_bytes": candidate["size_bytes"],
        "expected_modified_at_utc": candidate["modified_at_utc"],
        "status": status,
        "message": message,
    }


def _approved_roots(report: dict[str, Any]) -> tuple[Path, ...]:
    return tuple(
        Path(root["canonical_path"] or root["requested_path"])
        for root in report["scan_scope"]["roots"]
    )


def _has_reparse_component(path: Path, matched_root: Path) -> bool:
    """Detect a link or junction introduced anywhere under the approved root."""

    current = path
    while True:
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            return False
        except OSError:
            return True
        if is_reparse_point(current, metadata):
            return True
        if os.path.normcase(os.path.abspath(str(current))) == os.path.normcase(
            os.path.abspath(str(matched_root))
        ):
            return False
        parent = current.parent
        if parent == current:
            return True
        current = parent


def revalidate_candidate(
    report: dict[str, Any], candidate: dict[str, Any]
) -> tuple[Path | None, dict[str, Any] | None]:
    """Recheck an exact file immediately before a Recycle Bin operation."""

    if candidate["protection"]["eligibility"] != "eligible":
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The report marks this entry as review-only, protected, or unavailable.",
        )
    if candidate.get("item_type", "file") == "folder":
        return _revalidate_folder_candidate(report, candidate)
    if not candidate["is_regular_file"] or candidate["is_reparse_point"]:
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The report does not describe an eligible regular file.",
        )

    path = Path(os.path.abspath(candidate["path"]))
    current_risk = assess_removal_risk(path, tuple(candidate["attributes"]))
    if current_risk.eligibility != "eligible":
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The current removal-risk policy keeps this path review-only.",
        )
    drive_letter = report["drive"]["drive_letter"].upper()
    if path.drive.upper() != drive_letter:
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The path no longer belongs to the analysed drive.",
        )

    roots = _approved_roots(report)
    reported_root = Path(os.path.abspath(candidate["scan_root"]))
    matching_roots = tuple(
        root
        for root in roots
        if os.path.normcase(os.path.abspath(str(root)))
        == os.path.normcase(os.path.abspath(str(reported_root)))
        and is_path_within(path, root)
    )
    if not matching_roots:
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The path is outside the approved scan roots.",
        )

    policy = ProtectedPathPolicy()
    if policy.is_protected(path, drive_letter):
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The path is now inside a protected Windows or application location.",
        )

    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None, _result(
            candidate,
            "missing",
            "The file no longer exists at the reviewed path.",
        )
    except OSError:
        return None, _result(
            candidate,
            "failed",
            "The file metadata could not be read immediately before recycling.",
        )

    if _has_reparse_component(path, matching_roots[0]):
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "A reparse point or link is present in the reviewed path.",
        )
    if not stat.S_ISREG(metadata.st_mode):
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The reviewed path is no longer a regular file.",
        )
    if candidate["size_bytes"] is None or metadata.st_size != candidate["size_bytes"]:
        return None, _result(
            candidate,
            "skipped_changed",
            "The file size changed after the storage report was created.",
        )

    expected_modified = _parse_utc(candidate["modified_at_utc"])
    actual_modified = datetime.fromtimestamp(metadata.st_mtime, timezone.utc)
    if expected_modified is None or actual_modified != expected_modified:
        return None, _result(
            candidate,
            "skipped_changed",
            "The modification time changed after the storage report was created.",
        )

    return path, None


def _folder_tree_snapshot(
    path: Path, *, development_cache: bool = False
) -> dict[str, Any]:
    """Re-read every descendant metadata record without opening file contents."""

    root_metadata = os.lstat(path)
    if not stat.S_ISDIR(root_metadata.st_mode) or is_reparse_point(
        path, root_metadata
    ):
        raise ValueError("The reviewed path is not a regular directory tree.")

    risk_attributes = ("development_cache",) if development_cache else ()

    def visit(directory: Path, metadata: os.stat_result) -> dict[str, Any]:
        file_count = 0
        directory_count = 0
        logical_bytes = 0
        allocated_bytes = 0
        newest_modified: datetime | None = None
        oldest_modified: datetime | None = None
        contains_high_risk_items = False
        fingerprint_parts: list[str] = []

        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        for entry in entries:
            child = Path(entry.path)
            child_metadata = entry.stat(follow_symlinks=False)
            if is_reparse_point(child, child_metadata):
                raise ValueError(
                    "A reparse point appeared inside the reviewed directory tree."
                )
            if stat.S_ISDIR(child_metadata.st_mode):
                directory_count += 1
                child_snapshot = visit(child, child_metadata)
                file_count += child_snapshot["file_count"]
                directory_count += child_snapshot["directory_count"]
                logical_bytes += child_snapshot["size_bytes"]
                allocated_bytes += child_snapshot["allocated_size_bytes"]
                contains_high_risk_items = (
                    contains_high_risk_items
                    or child_snapshot["contains_high_risk_items"]
                    or assess_folder_removal_risk(
                        child, risk_attributes
                    ).level
                    == "high"
                )
                child_newest = child_snapshot[
                    "newest_descendant_modified_at_utc"
                ]
                child_oldest = child_snapshot[
                    "oldest_descendant_modified_at_utc"
                ]
                if child_newest is not None and (
                    newest_modified is None or child_newest > newest_modified
                ):
                    newest_modified = child_newest
                if child_oldest is not None and (
                    oldest_modified is None or child_oldest < oldest_modified
                ):
                    oldest_modified = child_oldest
                fingerprint_parts.append(
                    directory_fingerprint_part(
                        entry.name,
                        child_snapshot["tree_metadata_fingerprint"],
                    )
                )
                continue
            if not stat.S_ISREG(child_metadata.st_mode):
                continue
            file_count += 1
            size_bytes = max(0, int(child_metadata.st_size))
            allocated_size = get_allocated_size(child, size_bytes)
            logical_bytes += size_bytes
            allocated_bytes += allocated_size
            contains_high_risk_items = (
                contains_high_risk_items
                or assess_removal_risk(child, risk_attributes).level == "high"
            )
            modified = datetime.fromtimestamp(
                child_metadata.st_mtime, timezone.utc
            )
            if newest_modified is None or modified > newest_modified:
                newest_modified = modified
            if oldest_modified is None or modified < oldest_modified:
                oldest_modified = modified
            fingerprint_parts.append(
                file_fingerprint_part(
                    entry.name,
                    size_bytes,
                    allocated_size,
                    child_metadata.st_mtime_ns,
                )
            )

        directory_modified = datetime.fromtimestamp(
            metadata.st_mtime, timezone.utc
        )
        return {
            "file_count": file_count,
            "directory_count": directory_count,
            "size_bytes": logical_bytes,
            "allocated_size_bytes": allocated_bytes,
            "modified_at_utc": newest_modified or directory_modified,
            "newest_descendant_modified_at_utc": newest_modified,
            "oldest_descendant_modified_at_utc": oldest_modified,
            "contains_high_risk_items": contains_high_risk_items,
            "tree_metadata_fingerprint": metadata_tree_fingerprint(
                fingerprint_parts
            ),
        }

    return visit(path, root_metadata)


def _revalidate_folder_candidate(
    report: dict[str, Any], candidate: dict[str, Any]
) -> tuple[Path | None, dict[str, Any] | None]:
    if candidate.get("is_directory") is not True or candidate["is_reparse_point"]:
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The report does not describe an eligible directory.",
        )

    path = Path(os.path.abspath(candidate["path"]))
    drive_letter = report["drive"]["drive_letter"].upper()
    if path.drive.upper() != drive_letter:
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The directory no longer belongs to the analysed drive.",
        )

    roots = _approved_roots(report)
    reported_root = Path(os.path.abspath(candidate["scan_root"]))
    matching_roots = tuple(
        root
        for root in roots
        if os.path.normcase(os.path.abspath(str(root)))
        == os.path.normcase(os.path.abspath(str(reported_root)))
        and is_path_within(path, root)
    )
    if not matching_roots:
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The directory is outside the approved scan roots.",
        )
    if os.path.normcase(os.path.abspath(str(path))) == os.path.normcase(
        os.path.abspath(str(matching_roots[0]))
    ):
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "A scan root can never become a folder cleanup candidate.",
        )

    policy = ProtectedPathPolicy()
    if policy.is_protected(path, drive_letter):
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The directory is now inside a protected Windows or application location.",
        )
    current_risk = assess_folder_removal_risk(
        path, tuple(candidate["attributes"])
    )
    if current_risk.eligibility != "eligible":
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The current folder-risk policy keeps this directory review-only.",
        )

    try:
        if _has_reparse_component(path, matching_roots[0]):
            return None, _result(
                candidate,
                "skipped_protected_or_invalid",
                "A reparse point or link is present in the reviewed path.",
            )
        snapshot = _folder_tree_snapshot(
            path,
            development_cache=(
                "development_cache" in candidate["attributes"]
            ),
        )
    except FileNotFoundError:
        return None, _result(
            candidate,
            "missing",
            "The directory no longer exists at the reviewed path.",
        )
    except ValueError as error:
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            str(error),
        )
    except OSError:
        return None, _result(
            candidate,
            "failed",
            "The complete directory tree could not be revalidated before recycling.",
        )

    expected_values = {
        "file_count": candidate.get("file_count"),
        "directory_count": candidate.get("directory_count"),
        "size_bytes": candidate.get("size_bytes"),
        "allocated_size_bytes": candidate.get("allocated_size_bytes"),
    }
    for key, expected in expected_values.items():
        if expected is None or snapshot[key] != expected:
            return None, _result(
                candidate,
                "skipped_changed",
                "The directory contents changed after the storage report was created.",
            )

    if snapshot["contains_high_risk_items"]:
        return None, _result(
            candidate,
            "skipped_protected_or_invalid",
            "The directory now contains high-risk application, configuration, runtime, or save data.",
        )

    expected_fingerprint = candidate.get("tree_metadata_fingerprint")
    if (
        not expected_fingerprint
        or snapshot["tree_metadata_fingerprint"] != expected_fingerprint
    ):
        return None, _result(
            candidate,
            "skipped_changed",
            "The directory tree paths or metadata changed after the storage report was created.",
        )

    expected_modified = _parse_utc(candidate.get("modified_at_utc"))
    if candidate.get("file_count", 0) > 0 and (
        expected_modified is None
        or snapshot["modified_at_utc"] != expected_modified
    ):
        return None, _result(
            candidate,
            "skipped_changed",
            "The newest directory-tree modification time changed after the report was created.",
        )
    return path, None


def send_to_recycle_bin(path: Path) -> None:
    """Move one validated file or folder to the Recycle Bin."""

    try:
        from send2trash import send2trash
    except ImportError as error:
        raise RecycleUnavailableError(
            "Recycle Bin support is unavailable. Install the project dependencies."
        ) from error

    try:
        send2trash(str(path))
    except Exception as error:
        raise RecycleUnavailableError(
            "Windows could not move this item to the Recycle Bin."
        ) from error


def execute_guided_cleanup(
    report: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
    *,
    recycler: Callable[[Path], None] = send_to_recycle_bin,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Revalidate and recycle selected items, preserving per-item outcomes."""

    clock = clock or (lambda: datetime.now(timezone.utc))
    selected = tuple(candidates)
    if not 1 <= len(selected) <= 500:
        raise CleanupRecordError(
            "A guided cleanup must contain between 1 and 500 items."
        )
    candidate_ids = [candidate["candidate_id"] for candidate in selected]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise CleanupRecordError(
            "A guided cleanup cannot contain duplicate candidate identifiers."
        )
    selected_kinds = {
        candidate.get("item_type", "file") for candidate in selected
    }
    if not selected_kinds.issubset({"file", "folder"}) or len(selected_kinds) != 1:
        raise CleanupRecordError(
            "A guided cleanup cannot mix file and folder candidates."
        )
    selected_paths = [
        os.path.normcase(os.path.abspath(candidate["path"]))
        for candidate in selected
    ]
    if len(selected_paths) != len(set(selected_paths)):
        raise CleanupRecordError(
            "A guided cleanup cannot contain the same reviewed path twice."
        )
    for index, left in enumerate(selected):
        left_path = Path(os.path.abspath(left["path"]))
        for right in selected[index + 1 :]:
            right_path = Path(os.path.abspath(right["path"]))
            if (
                is_path_within(left_path, right_path)
                or is_path_within(right_path, left_path)
            ):
                raise CleanupRecordError(
                    "A guided cleanup cannot combine overlapping parent and child paths."
                )
    started_at = clock().astimezone(timezone.utc)
    results: list[dict[str, Any]] = []

    for candidate in selected:
        path, failed_result = revalidate_candidate(report, candidate)
        if failed_result is not None:
            results.append(failed_result)
            continue

        try:
            recycler(path)
        except Exception:
            results.append(
                _result(
                    candidate,
                    "failed",
                    "Windows could not move this item to the Recycle Bin; it was not permanently deleted.",
                )
            )
            continue
        results.append(
            _result(
                candidate,
                "recycled",
                "Windows accepted the item for the Recycle Bin.",
            )
        )

    completed_at = clock().astimezone(timezone.utc)
    counts = {
        status: sum(1 for result in results if result["status"] == status)
        for status in RESULT_STATUSES
    }
    return {
        "schema_version": CLEANUP_RECORD_SCHEMA_VERSION,
        "record_type": "guided_cleanup_result",
        "operation_id": str(uuid.uuid4()),
        "started_at_utc": _utc_text(started_at),
        "completed_at_utc": _utc_text(completed_at),
        "source_report": {
            "generated_at_utc": report["generated_at_utc"],
            "drive_letter": report["drive"]["drive_letter"],
        },
        "requested_count": len(selected),
        "requested_unique_bytes": sum(
            candidate["size_bytes"] or 0 for candidate in selected
        ),
        "summary": counts,
        "results": results,
        "safety": {
            "action": "windows_recycle_bin_only",
            "permanent_delete_fallback": False,
            "directories_allowed": any(
                candidate.get("item_type", "file") == "folder"
                for candidate in selected
            ),
        },
    }


@lru_cache(maxsize=2)
def _load_cleanup_schema(schema_path: str) -> dict[str, Any]:
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaError) as error:
        raise CleanupRecordError("The cleanup record schema could not be read.") from error
    return schema


def validate_cleanup_record(
    record: dict[str, Any],
    schema_path: str | Path = DEFAULT_CLEANUP_SCHEMA_PATH,
) -> None:
    schema = _load_cleanup_schema(str(Path(schema_path).resolve()))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(record),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise CleanupRecordError(
            f"Cleanup record validation failed at {location}: {error.message}"
        )

    results = record["results"]
    if record["requested_count"] != len(results):
        raise CleanupRecordError(
            "Cleanup record requested_count must equal the result count."
        )
    for status in RESULT_STATUSES:
        actual_count = sum(
            1 for result in results if result["status"] == status
        )
        if record["summary"][status] != actual_count:
            raise CleanupRecordError(
                f"Cleanup record summary for {status} does not match results."
            )
    expected_bytes = sum(
        result["expected_size_bytes"] or 0 for result in results
    )
    if record["requested_unique_bytes"] != expected_bytes:
        raise CleanupRecordError(
            "Cleanup record requested_unique_bytes does not match results."
        )


def write_cleanup_record(
    record: dict[str, Any],
    directory: str | Path = DEFAULT_CLEANUP_RECORD_DIRECTORY,
) -> Path:
    """Exclusively create a validated machine-local cleanup record."""

    validate_cleanup_record(record)
    record_directory = Path(directory)
    filename = (
        "cleanup-result-"
        f"{record['started_at_utc'].replace(':', '').replace('-', '')}-"
        f"{record['operation_id']}.json"
    )
    output = record_directory / filename
    payload = (json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    try:
        record_directory.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as record_file:
            record_file.write(payload)
    except OSError as error:
        raise CleanupRecordError(
            "The cleanup finished, but its local result record could not be written."
        ) from error
    return output.resolve()
