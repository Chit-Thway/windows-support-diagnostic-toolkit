from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dashboard.cleanup_tokens import (
    CleanupPreviewError,
    CleanupPreviewExpiredError,
    CleanupPreviewStore,
    HIGH_RISK_FILE_COUNT,
    HIGH_RISK_TOTAL_BYTES,
)


def candidate(candidate_id: str, size_bytes: int = 1) -> dict:
    return {"candidate_id": candidate_id, "size_bytes": size_bytes}


def test_preview_token_is_single_use_and_selection_is_copied(tmp_path) -> None:
    selected = [candidate("one")]
    store = CleanupPreviewStore()

    preview = store.create(
        drive_letter="F:",
        storage_report_path=tmp_path / "report.json",
        source_generated_at_utc="2026-08-03T00:00:00Z",
        candidates=selected,
    )
    selected[0]["size_bytes"] = 999

    assert store.consume(preview.token).candidates[0]["size_bytes"] == 1
    with pytest.raises(CleanupPreviewError, match="already used"):
        store.consume(preview.token)


def test_expired_preview_is_rejected(tmp_path) -> None:
    now = [datetime(2026, 8, 3, tzinfo=timezone.utc)]
    store = CleanupPreviewStore(
        clock=lambda: now[0],
        lifetime=timedelta(seconds=5),
    )
    preview = store.create(
        drive_letter="F:",
        storage_report_path=tmp_path / "report.json",
        source_generated_at_utc="2026-08-03T00:00:00Z",
        candidates=[candidate("one")],
    )

    now[0] += timedelta(seconds=5)

    with pytest.raises(CleanupPreviewExpiredError, match="expired"):
        store.consume(preview.token)


@pytest.mark.parametrize(
    "candidates",
    [
        [candidate(str(index)) for index in range(HIGH_RISK_FILE_COUNT)],
        [candidate("large", HIGH_RISK_TOTAL_BYTES)],
    ],
)
def test_large_selection_requires_typed_confirmation(
    tmp_path, candidates
) -> None:
    preview = CleanupPreviewStore().create(
        drive_letter="F:",
        storage_report_path=tmp_path / "report.json",
        source_generated_at_utc="2026-08-03T00:00:00Z",
        candidates=candidates,
    )

    assert preview.requires_additional_confirmation is True
    assert preview.confirmation_phrase == f"RECYCLE {len(candidates)} " + (
        "FILE" if len(candidates) == 1 else "FILES"
    )

