from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIRECTORY = Path(__file__).resolve().parent / "fixtures"
SCHEMA_PATH = REPOSITORY_ROOT / "schema" / "storage-report.schema.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def storage_schema() -> dict:
    return read_json(SCHEMA_PATH)


@pytest.fixture(scope="session")
def storage_validator(storage_schema: dict) -> Draft202012Validator:
    Draft202012Validator.check_schema(storage_schema)
    return Draft202012Validator(storage_schema, format_checker=FormatChecker())


@pytest.fixture
def storage_fixture():
    def _load(name: str) -> dict:
        return copy.deepcopy(read_json(FIXTURES_DIRECTORY / name))

    return _load
