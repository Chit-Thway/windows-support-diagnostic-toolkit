from __future__ import annotations

import json
from pathlib import Path

from dashboard.extension_help import EXTENSION_HELP, extension_help

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPLETE_INDEX = (
    REPOSITORY_ROOT
    / "tests"
    / "storage"
    / "fixtures"
    / "complete-file-type-index.json"
)


def test_every_preset_extension_has_plain_english_help() -> None:
    report = json.loads(COMPLETE_INDEX.read_text(encoding="utf-8"))
    preset_extensions = {
        extension
        for group in report["extension_groups"]
        for extension in group["extensions"]
    }

    assert preset_extensions == set(EXTENSION_HELP)
    for extension in sorted(preset_extensions):
        item = extension_help(extension)
        assert item["extension"] == extension
        assert 30 <= len(item["description"].split()) <= 60
        assert 7 <= len(item["example"].split()) <= 16
        assert 1 <= len(item["icon"]) <= 5


def test_custom_extension_help_is_neutral_and_review_only() -> None:
    item = extension_help(".fictional")

    assert item["extension"] == ".fictional"
    assert item["name"] == "Custom indexed extension"
    assert "does not assume" in item["description"]
    assert "review-only" in item["description"]
