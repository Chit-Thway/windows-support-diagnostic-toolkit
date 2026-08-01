from __future__ import annotations

from pathlib import Path

import pytest

from storage.path_policy import ProtectedPathPolicy, UnsafeScanRootError


def test_safe_user_selected_root_is_resolved(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    policy = ProtectedPathPolicy(protected_roots=())

    result = policy.validate_roots([root])

    assert result == (root.resolve(),)


def test_file_cannot_be_used_as_scan_root(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("synthetic", encoding="utf-8")
    policy = ProtectedPathPolicy(protected_roots=())

    with pytest.raises(UnsafeScanRootError, match="not a directory"):
        policy.validate_roots([file_path])


def test_protected_root_is_rejected(tmp_path: Path) -> None:
    protected = tmp_path / "Protected"
    protected.mkdir()
    child = protected / "Child"
    child.mkdir()
    policy = ProtectedPathPolicy(protected_roots=(protected,))

    with pytest.raises(UnsafeScanRootError, match="Protected"):
        policy.validate_roots([child])


def test_overlapping_roots_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "Selected"
    nested = root / "Nested"
    nested.mkdir(parents=True)
    policy = ProtectedPathPolicy(protected_roots=())

    with pytest.raises(UnsafeScanRootError, match="must not overlap"):
        policy.validate_roots([root, nested])


def test_reparse_point_root_is_rejected_before_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "FictionalJunction"
    root.mkdir()
    policy = ProtectedPathPolicy(protected_roots=())
    monkeypatch.setattr(
        "storage.path_policy.is_reparse_point",
        lambda path, stat_result=None: path == root,
    )

    with pytest.raises(UnsafeScanRootError, match="Reparse points"):
        policy.validate_roots([root])


def test_development_cache_must_be_inside_scan_root(tmp_path: Path) -> None:
    root = tmp_path / "Selected"
    cache = tmp_path / "OutsideCache"
    root.mkdir()
    cache.mkdir()
    policy = ProtectedPathPolicy(protected_roots=())
    roots = policy.validate_roots([root])

    with pytest.raises(UnsafeScanRootError, match="approved scan root"):
        policy.validate_development_cache_roots([cache], roots)


def test_whole_drive_root_is_rejected() -> None:
    drive_root = Path(Path.cwd().drive + "\\")
    policy = ProtectedPathPolicy(protected_roots=())

    with pytest.raises(UnsafeScanRootError, match="entire drive"):
        policy.validate_roots([drive_root])
