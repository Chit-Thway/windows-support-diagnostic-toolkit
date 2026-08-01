from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCANNER_FILES = (
    REPOSITORY_ROOT / "storage" / "scanner.py",
    REPOSITORY_ROOT / "storage" / "classifier.py",
    REPOSITORY_ROOT / "storage" / "path_policy.py",
)

PROHIBITED_CALLS = {
    "os.remove",
    "os.rename",
    "os.replace",
    "os.rmdir",
    "os.unlink",
    "send2trash.send2trash",
    "shutil.move",
    "shutil.rmtree",
}

PROHIBITED_IMPORT_ROOTS = {
    "ftplib",
    "http",
    "requests",
    "socket",
    "urllib",
}


def dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def test_scanner_contains_no_file_deletion_or_move_calls() -> None:
    discovered_calls: set[str] = set()
    for source_path in SCANNER_FILES:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = dotted_name(node.func)
                if call_name:
                    discovered_calls.add(call_name)

    assert discovered_calls.isdisjoint(PROHIBITED_CALLS)
    assert not any(name.endswith(".unlink") for name in discovered_calls)


def test_scanner_contains_no_network_client_imports() -> None:
    imported_roots: set[str] = set()
    for source_path in SCANNER_FILES:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots.isdisjoint(PROHIBITED_IMPORT_ROOTS)


def test_scanner_source_does_not_open_or_read_file_contents() -> None:
    content_read_calls = {
        "open",
        "Path.open",
        "Path.read_bytes",
        "Path.read_text",
    }
    discovered_calls: set[str] = set()
    for source_path in SCANNER_FILES:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = dotted_name(node.func)
                if call_name:
                    discovered_calls.add(call_name)

    assert discovered_calls.isdisjoint(content_read_calls)
    assert "open(" not in (REPOSITORY_ROOT / "storage" / "scanner.py").read_text(
        encoding="utf-8"
    )


def test_scanner_does_not_resolve_detected_reparse_targets() -> None:
    scanner_source = (REPOSITORY_ROOT / "storage" / "scanner.py").read_text(
        encoding="utf-8"
    )

    assert "resolve(strict=False)" not in scanner_source


def test_generated_storage_reports_remain_ignored() -> None:
    ignore_lines = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".gitignore").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "storage-reports/" in ignore_lines
    assert "cleanup-records/" in ignore_lines
    assert ".private/" in ignore_lines
