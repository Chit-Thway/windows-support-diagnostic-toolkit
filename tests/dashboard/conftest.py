from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIRECTORY = REPOSITORY_ROOT / "tests" / "fixtures"


def read_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIRECTORY / name).read_text(encoding="utf-8"))


@pytest.fixture
def fixture_data():
    def _load(name: str) -> dict:
        return copy.deepcopy(read_fixture(name))

    return _load


@pytest.fixture
def write_report(tmp_path: Path):
    def _write(report: dict, name: str = "report.json") -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    return _write
