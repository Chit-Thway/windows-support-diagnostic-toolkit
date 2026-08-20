"""Read Windows allocation and file-identity metadata without file contents."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

INVALID_FILE_SIZE = 0xFFFFFFFF
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FILE_READ_ATTRIBUTES = 0x0080
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", wintypes.FILETIME),
        ("ftLastAccessTime", wintypes.FILETIME),
        ("ftLastWriteTime", wintypes.FILETIME),
        ("dwVolumeSerialNumber", wintypes.DWORD),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("nNumberOfLinks", wintypes.DWORD),
        ("nFileIndexHigh", wintypes.DWORD),
        ("nFileIndexLow", wintypes.DWORD),
    ]


def _windows_api_path(path: Path) -> str:
    absolute = os.path.abspath(str(path))
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute


def _kernel32():
    if os.name != "nt":
        raise OSError("Windows file-allocation metadata is available only on Windows.")
    return ctypes.WinDLL("kernel32", use_last_error=True)


def get_allocated_size(path: Path, logical_size: int) -> int:
    """Return physical bytes allocated for a file on its Windows volume."""

    kernel32 = _kernel32()
    get_compressed_size = kernel32.GetCompressedFileSizeW
    get_compressed_size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    get_compressed_size.restype = wintypes.DWORD
    high = wintypes.DWORD(0)
    ctypes.set_last_error(0)
    low = int(get_compressed_size(_windows_api_path(path), ctypes.byref(high)))
    error_code = ctypes.get_last_error()
    if low == INVALID_FILE_SIZE and error_code:
        raise OSError(error_code, os.strerror(error_code), str(path))
    allocated = (int(high.value) << 32) | low
    if logical_size == 0:
        return 0
    return max(0, allocated)


def get_file_identity(path: Path) -> tuple[object, ...]:
    """Return a stable volume/file identifier suitable for hard-link deduping."""

    kernel32 = _kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    ctypes.set_last_error(0)
    handle = create_file(
        _windows_api_path(path),
        FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        error_code = ctypes.get_last_error()
        raise OSError(error_code, os.strerror(error_code), str(path))

    try:
        information = _ByHandleFileInformation()
        if not get_information(handle, ctypes.byref(information)):
            error_code = ctypes.get_last_error()
            raise OSError(error_code, os.strerror(error_code), str(path))
        file_index = (
            int(information.nFileIndexHigh) << 32
        ) | int(information.nFileIndexLow)
        return (
            "windows_file_id",
            int(information.dwVolumeSerialNumber),
            file_index,
        )
    finally:
        close_handle(handle)
