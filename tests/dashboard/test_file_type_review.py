from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.file_type_index_loader import load_file_type_index_snapshot
from dashboard.file_type_review import (
    FileTypeReviewQueryError,
    query_file_type_files,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY_ROOT / "tests" / "storage" / "fixtures"
COMPLETE_INDEX = FIXTURES / "complete-file-type-index.json"


@pytest.fixture
def snapshot():
    return load_file_type_index_snapshot(COMPLETE_INDEX)


def test_recursive_scope_filters_extensions_and_sorts_largest(snapshot) -> None:
    result = query_file_type_files(
        snapshot,
        folder_ids=["folder-users"],
        extensions=[".pdf", ".docx", ".xlsx"],
        sort_by="largest",
    )

    assert result["matching_count"] == 4
    assert [file["name"] for file in result["files"]] == [
        "support-guide.pdf",
        "handbook.pdf",
        "training-notes.docx",
        "fictional-budget.xlsx",
    ]
    assert result["matching_bytes"] == 16 * 1024 * 1024


def test_direct_scope_excludes_descendant_files(snapshot) -> None:
    direct = query_file_type_files(
        snapshot,
        folder_ids=["folder-profile"],
        extensions=[".pdf"],
        scope_mode="direct",
    )
    recursive = query_file_type_files(
        snapshot,
        folder_ids=["folder-profile"],
        extensions=[".pdf"],
        scope_mode="recursive",
    )

    assert direct["matching_count"] == 0
    assert recursive["matching_count"] == 2


def test_filename_size_and_age_filters_are_combined(snapshot) -> None:
    result = query_file_type_files(
        snapshot,
        folder_ids=["folder-users"],
        extensions=[".pdf"],
        filename="hand",
        minimum_size_bytes=3 * 1024 * 1024,
        minimum_age_days=1500,
    )

    assert [file["name"] for file in result["files"]] == ["handbook.pdf"]


@pytest.mark.parametrize(
    ("sort_by", "expected"),
    [
        ("smallest", ["handbook.pdf", "support-guide.pdf"]),
        ("oldest", ["handbook.pdf", "support-guide.pdf"]),
        ("newest", ["support-guide.pdf", "handbook.pdf"]),
        ("name", ["handbook.pdf", "support-guide.pdf"]),
        ("path", ["handbook.pdf", "support-guide.pdf"]),
    ],
)
def test_supported_sorts_are_deterministic(snapshot, sort_by, expected) -> None:
    result = query_file_type_files(
        snapshot,
        folder_ids=["folder-users"],
        extensions=[".pdf"],
        sort_by=sort_by,
    )

    assert [file["name"] for file in result["files"]] == expected


def test_pagination_reports_complete_matching_totals(snapshot) -> None:
    result = query_file_type_files(
        snapshot,
        folder_ids=["folder-root"],
        extensions=[".pdf"],
        page=2,
        page_size=2,
    )

    assert result["matching_count"] == 4
    assert result["total_pages"] == 2
    assert result["first_row"] == 3
    assert result["last_row"] == 4
    assert len(result["files"]) == 2


def test_protected_files_are_visible_but_not_selectable(snapshot) -> None:
    result = query_file_type_files(
        snapshot,
        folder_ids=["folder-root"],
        extensions=[".pdf"],
    )
    protected = [file for file in result["files"] if not file["selectable"]]

    assert {file["name"] for file in protected} == {
        "application-manual.pdf",
        "system-help.pdf",
    }
    assert all(file["selection_state"] == "protected" for file in protected)


def test_bounded_index_discloses_omitted_file_details() -> None:
    snapshot = load_file_type_index_snapshot(
        FIXTURES / "bounded-file-type-index.json"
    )
    result = query_file_type_files(
        snapshot,
        folder_ids=[snapshot.root["folder_id"]],
        extensions=[".pdf"],
    )

    assert result["detail_coverage"] == "bounded"
    assert result["omitted_files"] > 0
    assert result["omitted_logical_bytes"] > 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"folder_ids": []},
        {"folder_ids": ["folder-users", "folder-documents"]},
        {"extensions": []},
        {"extensions": [".not-indexed"]},
        {"scope_mode": "everything"},
        {"sort_by": "random"},
        {"page_size": 101},
    ],
)
def test_invalid_queries_fail_closed(snapshot, overrides) -> None:
    arguments = {
        "folder_ids": ["folder-users"],
        "extensions": [".pdf"],
    }
    arguments.update(overrides)

    with pytest.raises(FileTypeReviewQueryError):
        query_file_type_files(snapshot, **arguments)
