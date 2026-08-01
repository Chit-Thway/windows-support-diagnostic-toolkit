"""Path validation and protected-location rules for read-only scans."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
DRIVE_PATTERN = re.compile(r"^[A-Za-z]:$")


class UnsafeScanRootError(ValueError):
    """Raised when a requested root cannot be scanned safely."""


def is_path_within(path: Path, root: Path) -> bool:
    """Compare Windows paths without relying on case-sensitive Path methods."""

    normalized_path = os.path.normcase(os.path.abspath(str(path)))
    normalized_root = os.path.normcase(os.path.abspath(str(root)))
    try:
        return os.path.commonpath((normalized_path, normalized_root)) == (
            normalized_root
        )
    except ValueError:
        return False


def is_reparse_point(path: Path, stat_result=None) -> bool:
    """Detect symbolic links and Windows reparse points without following them."""

    try:
        if path.is_symlink():
            return True
        metadata = os.lstat(path) if stat_result is None else stat_result
    except OSError:
        return False

    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def default_protected_roots(
    drive_letter: str,
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Return conservative Windows locations excluded from recursive scans."""

    environment = os.environ if environment is None else environment
    candidates: list[Path] = []
    for variable in (
        "SystemRoot",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "ProgramW6432",
        "ProgramData",
    ):
        value = environment.get(variable)
        if value:
            candidates.append(Path(value))

    drive_root = Path(f"{drive_letter}\\")
    candidates.extend(
        (
            drive_root / "$Recycle.Bin",
            drive_root / "System Volume Information",
            drive_root / "Recovery",
        )
    )

    unique: dict[str, Path] = {}
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(str(candidate)))
        unique.setdefault(normalized, Path(os.path.abspath(str(candidate))))
    return tuple(unique.values())


class ProtectedPathPolicy:
    """Validate approved roots and block protected or ambiguous locations."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        protected_roots: Iterable[str | Path] | None = None,
    ) -> None:
        self._environment = os.environ if environment is None else environment
        self._explicit_protected_roots = (
            tuple(Path(path) for path in protected_roots)
            if protected_roots is not None
            else None
        )

    def protected_roots_for_drive(self, drive_letter: str) -> tuple[Path, ...]:
        if self._explicit_protected_roots is not None:
            return self._explicit_protected_roots
        return default_protected_roots(drive_letter, self._environment)

    def is_protected(self, path: Path, drive_letter: str) -> bool:
        return any(
            is_path_within(path, protected_root)
            for protected_root in self.protected_roots_for_drive(drive_letter)
        )

    def validate_roots(
        self,
        requested_roots: Iterable[str | Path],
    ) -> tuple[Path, ...]:
        roots = tuple(requested_roots)
        if not roots:
            raise UnsafeScanRootError(
                "Select at least one directory to scan; whole-drive scanning "
                "is never started silently."
            )

        resolved_roots: list[Path] = []
        drive_letter: str | None = None
        for requested_root in roots:
            expanded = Path(requested_root).expanduser()
            lexical_root = Path(os.path.abspath(str(expanded)))
            if is_reparse_point(lexical_root):
                raise UnsafeScanRootError(
                    f"Reparse points cannot be used as scan roots: "
                    f"{lexical_root}"
                )
            try:
                resolved = expanded.resolve(strict=True)
            except OSError as error:
                raise UnsafeScanRootError(
                    f"The scan root does not exist or is unavailable: {expanded}"
                ) from error

            if not resolved.is_dir():
                raise UnsafeScanRootError(
                    f"The scan root is not a directory: {resolved}"
                )
            if is_reparse_point(resolved):
                raise UnsafeScanRootError(
                    f"Reparse points cannot be used as scan roots: {resolved}"
                )

            current_drive = resolved.drive.upper()
            if not DRIVE_PATTERN.fullmatch(current_drive):
                raise UnsafeScanRootError(
                    "Only local Windows drive-letter roots are supported."
                )
            if resolved == Path(f"{current_drive}\\"):
                raise UnsafeScanRootError(
                    "Select specific folders instead of scanning an entire "
                    "drive silently."
                )
            if drive_letter is None:
                drive_letter = current_drive
            elif current_drive != drive_letter:
                raise UnsafeScanRootError(
                    "One storage report can contain roots from only one drive."
                )
            if self.is_protected(resolved, current_drive):
                raise UnsafeScanRootError(
                    f"Protected Windows or application paths cannot be scan "
                    f"roots: {resolved}"
                )

            resolved_roots.append(resolved)

        for index, root in enumerate(resolved_roots):
            for other in resolved_roots[index + 1 :]:
                if is_path_within(root, other) or is_path_within(other, root):
                    raise UnsafeScanRootError(
                        "Scan roots must be distinct and must not overlap: "
                        f"{root} and {other}"
                    )

        return tuple(resolved_roots)

    def validate_development_cache_roots(
        self,
        requested_cache_roots: Iterable[str | Path],
        scan_roots: tuple[Path, ...],
    ) -> tuple[Path, ...]:
        cache_roots: list[Path] = []
        drive_letter = scan_roots[0].drive.upper()
        for requested_root in requested_cache_roots:
            expanded = Path(requested_root).expanduser()
            lexical_root = Path(os.path.abspath(str(expanded)))
            if is_reparse_point(lexical_root):
                raise UnsafeScanRootError(
                    f"The development-cache root is not a safe directory: "
                    f"{lexical_root}"
                )
            try:
                resolved = expanded.resolve(strict=True)
            except OSError as error:
                raise UnsafeScanRootError(
                    f"The development-cache root is unavailable: {expanded}"
                ) from error

            if not resolved.is_dir() or is_reparse_point(resolved):
                raise UnsafeScanRootError(
                    f"The development-cache root is not a safe directory: "
                    f"{resolved}"
                )
            if self.is_protected(resolved, drive_letter):
                raise UnsafeScanRootError(
                    f"Protected paths cannot be development-cache roots: "
                    f"{resolved}"
                )
            if not any(is_path_within(resolved, root) for root in scan_roots):
                raise UnsafeScanRootError(
                    "Every development-cache root must be inside an approved "
                    f"scan root: {resolved}"
                )
            cache_roots.append(resolved)

        return tuple(cache_roots)
