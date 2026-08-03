from __future__ import annotations

import os
from pathlib import Path

import pytest

from storage.windows_metadata import get_allocated_size, get_file_identity


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows metadata API")


def test_allocated_size_is_nonnegative_and_zero_file_uses_no_bytes(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"synthetic metadata test")
    empty = tmp_path / "empty.bin"
    empty.touch()

    assert get_allocated_size(payload, payload.stat().st_size) >= 0
    assert get_allocated_size(empty, 0) == 0


def test_windows_file_identity_deduplicates_hard_links(tmp_path: Path) -> None:
    original = tmp_path / "original.bin"
    linked = tmp_path / "linked.bin"
    original.write_bytes(b"one physical file")
    try:
        os.link(original, linked)
    except OSError:
        pytest.skip("Hard links are unavailable on this test volume.")

    assert get_file_identity(original) == get_file_identity(linked)
