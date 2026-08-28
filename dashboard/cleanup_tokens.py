"""Short-lived, single-use cleanup preview tokens held only in memory."""

from __future__ import annotations

import copy
import secrets
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_PREVIEW_LIFETIME = timedelta(minutes=10)
MAX_STORED_PREVIEWS = 64
HIGH_RISK_FILE_COUNT = 20
HIGH_RISK_TOTAL_BYTES = 10 * 1024**3


class CleanupPreviewError(RuntimeError):
    """Base error for missing, expired, or consumed preview state."""


class CleanupPreviewExpiredError(CleanupPreviewError):
    """Raised when a cleanup preview has exceeded its lifetime."""


@dataclass(frozen=True)
class CleanupPreview:
    token: str
    created_at_utc: datetime
    expires_at_utc: datetime
    drive_letter: str
    storage_report_path: str
    source_generated_at_utc: str
    candidate_kind: str
    candidates: tuple[dict[str, Any], ...]
    total_bytes: int
    requires_additional_confirmation: bool
    confirmation_phrase: str | None
    source_kind: str


class CleanupPreviewStore:
    """Thread-safe in-memory store that consumes every confirmation once."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        lifetime: timedelta = DEFAULT_PREVIEW_LIFETIME,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lifetime = lifetime
        self._previews: dict[str, CleanupPreview] = {}
        self._lock = threading.Lock()

    def _now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    def _purge_expired(self, now: datetime) -> None:
        expired = [
            token
            for token, preview in self._previews.items()
            if preview.expires_at_utc <= now
        ]
        for token in expired:
            self._previews.pop(token, None)

    def create(
        self,
        *,
        drive_letter: str,
        storage_report_path: str | Path,
        source_generated_at_utc: str,
        candidates: Iterable[dict[str, Any]],
        source_kind: str = "storage_report",
    ) -> CleanupPreview:
        if source_kind not in {"storage_report", "file_type_index"}:
            raise CleanupPreviewError("The cleanup preview source is not supported.")
        selected = tuple(copy.deepcopy(tuple(candidates)))
        kinds = {candidate.get("item_type", "file") for candidate in selected}
        if len(kinds) != 1 or not kinds.issubset({"file", "folder"}):
            raise CleanupPreviewError(
                "A cleanup preview must contain either files or folders, not both."
            )
        candidate_kind = next(iter(kinds))
        now = self._now()
        total_bytes = sum(
            candidate.get("allocated_size_bytes")
            if candidate.get("allocated_size_bytes") is not None
            else candidate["size_bytes"] or 0
            for candidate in selected
        )
        high_risk = (
            candidate_kind == "folder"
            or len(selected) >= HIGH_RISK_FILE_COUNT
            or total_bytes >= HIGH_RISK_TOTAL_BYTES
        )
        noun = (
            "FOLDER"
            if candidate_kind == "folder" and len(selected) == 1
            else "FOLDERS"
            if candidate_kind == "folder"
            else "FILE"
            if len(selected) == 1
            else "FILES"
        )
        phrase = f"RECYCLE {len(selected)} {noun}" if high_risk else None
        preview = CleanupPreview(
            token=secrets.token_urlsafe(32),
            created_at_utc=now,
            expires_at_utc=now + self._lifetime,
            drive_letter=drive_letter,
            storage_report_path=str(Path(storage_report_path).resolve()),
            source_generated_at_utc=source_generated_at_utc,
            candidate_kind=candidate_kind,
            candidates=selected,
            total_bytes=total_bytes,
            requires_additional_confirmation=high_risk,
            confirmation_phrase=phrase,
            source_kind=source_kind,
        )
        with self._lock:
            self._purge_expired(now)
            if len(self._previews) >= MAX_STORED_PREVIEWS:
                oldest_token = min(
                    self._previews,
                    key=lambda token: self._previews[token].created_at_utc,
                )
                self._previews.pop(oldest_token, None)
            self._previews[preview.token] = preview
        return preview

    def consume(self, token: str) -> CleanupPreview:
        now = self._now()
        with self._lock:
            preview = self._previews.pop(token, None)
        if preview is None:
            raise CleanupPreviewError(
                "This cleanup preview is missing, already used, or no longer valid."
            )
        if preview.expires_at_utc <= now:
            raise CleanupPreviewExpiredError(
                "This cleanup preview expired. Return to the candidate explorer and review the selection again."
            )
        return preview
