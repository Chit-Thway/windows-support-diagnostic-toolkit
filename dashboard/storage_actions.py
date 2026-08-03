"""Guard read-only operating-system actions from the storage dashboard."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from storage.path_policy import (
    ProtectedPathPolicy,
    is_path_within,
    is_reparse_point,
)


class StorageActionError(RuntimeError):
    """Raised when a requested read-only action cannot be completed safely."""


def validate_candidate_for_folder_action(
    report: dict[str, Any], candidate: dict[str, Any]
) -> Path:
    """Return the containing folder only for a currently safe report entry."""

    protection = candidate["protection"]
    if protection["eligibility"] not in {"eligible", "review_only"}:
        raise StorageActionError(
            "Protected or unavailable entries cannot open local folders."
        )
    if not candidate["is_regular_file"] or candidate["is_reparse_point"]:
        raise StorageActionError(
            "Only regular, non-reparse-point file records can open folders."
        )

    candidate_path = Path(candidate["path"])
    drive_letter = report["drive"]["drive_letter"].upper()
    if candidate_path.drive.upper() != drive_letter:
        raise StorageActionError(
            "The candidate path does not belong to the analysed drive."
        )

    approved_roots = tuple(
        Path(root["canonical_path"] or root["requested_path"])
        for root in report["scan_scope"]["roots"]
    )
    if not any(is_path_within(candidate_path, root) for root in approved_roots):
        raise StorageActionError(
            "The candidate path is outside the approved scan roots."
        )

    policy = ProtectedPathPolicy()
    if policy.is_protected(candidate_path, drive_letter):
        raise StorageActionError(
            "Protected Windows or application locations cannot be opened here."
        )

    return candidate_path.parent


def open_containing_folder(folder: Path) -> None:
    """Open a verified directory with the Windows shell without changing files."""

    if not folder.is_dir():
        raise StorageActionError(
            "The containing folder no longer exists or is unavailable."
        )
    if is_reparse_point(folder):
        raise StorageActionError(
            "The containing folder is now a reparse point and was not opened."
        )
    if not hasattr(os, "startfile"):
        raise StorageActionError(
            "Opening a containing folder is available only on Windows."
        )

    try:
        os.startfile(str(folder))  # type: ignore[attr-defined]
    except OSError as error:
        raise StorageActionError(
            "Windows could not open the containing folder."
        ) from error
