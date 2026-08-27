from __future__ import annotations

from pathlib import Path

from dashboard.file_type_index_loader import load_file_type_index_snapshot
from dashboard.file_type_presenter import (
    present_file_type_children,
    present_file_type_index,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPLETE_INDEX = (
    REPOSITORY_ROOT
    / "tests"
    / "storage"
    / "fixtures"
    / "complete-file-type-index.json"
)


def test_presenter_exposes_group_controls_without_file_rows() -> None:
    view = present_file_type_index(
        load_file_type_index_snapshot(COMPLETE_INDEX)
    )

    assert view["drive"]["drive_letter"] == "C:"
    assert view["root"]["total_bytes"] == 70_782_976
    assert view["root"]["extension_bytes"][".pdf"] == 15_204_352
    assert [group["label"] for group in view["extension_groups"][:3]] == [
        "Documents",
        "Videos",
        "Audio",
    ]
    assert view["custom_extension_count"] == 1


def test_children_are_ranked_by_total_size_with_access_states() -> None:
    snapshot = load_file_type_index_snapshot(COMPLETE_INDEX)

    children = present_file_type_children(snapshot, "folder-root")

    assert [child["name"] for child in children] == [
        "Users",
        "Program Files",
        "Windows",
    ]
    assert children[0]["extension_bytes"][".pdf"] == 13_631_488
    assert children[1]["access_state"] == "protected"
    assert children[1]["access_label"] == "Protected"
    assert children[1]["scope_selectable"] is True


def test_unavailable_folder_cannot_be_selected_as_scope() -> None:
    partial = (
        REPOSITORY_ROOT
        / "tests"
        / "storage"
        / "fixtures"
        / "partial-file-type-index.json"
    )
    snapshot = load_file_type_index_snapshot(partial)
    unavailable = next(
        folder
        for folder in snapshot.report["folders"]
        if folder["access"]["state"] == "unavailable"
    )
    parent_id = unavailable["parent_id"]

    child = next(
        item
        for item in present_file_type_children(snapshot, parent_id)
        if item["folder_id"] == unavailable["folder_id"]
    )
    assert child["scope_selectable"] is False
