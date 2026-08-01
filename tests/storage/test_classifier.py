from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage.classifier import ClassificationOptions, classify_file

OBSERVED_AT = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def classify(
    path: Path,
    *,
    size_bytes: int = 100,
    age: timedelta = timedelta(days=1),
    options: ClassificationOptions | None = None,
    cache_roots: tuple[Path, ...] = (),
):
    return classify_file(
        path=path,
        size_bytes=size_bytes,
        modified_at_utc=OBSERVED_AT - age,
        observed_at_utc=OBSERVED_AT,
        options=options or ClassificationOptions(),
        development_cache_roots=cache_roots,
    )


def test_stale_threshold_is_inclusive() -> None:
    result = classify(
        Path(r"C:\Users\fictional.test\Downloads\archive.zip"),
        age=timedelta(days=730),
    )

    assert result.attributes == ("stale",)
    assert result.evidence[0]["code"] == "modified_before_stale_cutoff"


def test_partial_extension_requires_minimum_age() -> None:
    path = Path(r"C:\Users\fictional.test\Downloads\archive.zip.part")

    recent = classify(path, age=timedelta(hours=23, minutes=59))
    old_enough = classify(path, age=timedelta(hours=24))

    assert "likely_incomplete" not in recent.attributes
    assert "likely_incomplete" in old_enough.attributes


def test_large_threshold_is_inclusive() -> None:
    options = ClassificationOptions(large_file_threshold_bytes=1024)

    result = classify(
        Path(r"C:\Users\fictional.test\Downloads\large.bin"),
        size_bytes=1024,
        options=options,
    )

    assert result.attributes == ("large",)
    assert result.confidence == "high"


def test_zero_byte_regular_file_is_empty() -> None:
    result = classify(
        Path(r"C:\Users\fictional.test\Downloads\placeholder.txt"),
        size_bytes=0,
    )

    assert result.attributes == ("empty",)


def test_temporary_extension_requires_separate_age_threshold() -> None:
    path = Path(r"C:\Users\fictional.test\Downloads\render.tmp")

    recent = classify(path, age=timedelta(hours=167))
    old_enough = classify(path, age=timedelta(hours=168))

    assert "temporary" not in recent.attributes
    assert "temporary" in old_enough.attributes


def test_combined_evidence_keeps_each_attribute_once() -> None:
    options = ClassificationOptions(large_file_threshold_bytes=100)
    result = classify(
        Path(r"C:\Users\fictional.test\Downloads\video.iso.part"),
        size_bytes=100,
        age=timedelta(days=730),
        options=options,
    )

    assert result.attributes == ("stale", "likely_incomplete", "large")
    assert {item["attribute"] for item in result.evidence} == set(
        result.attributes
    )


def test_development_cache_requires_explicit_cache_root(tmp_path: Path) -> None:
    cache_root = tmp_path / "fictional-pip-cache"
    cache_root.mkdir()
    cache_file = cache_root / "wheel.bin"

    without_opt_in = classify(cache_file)
    with_opt_in = classify(cache_file, cache_roots=(cache_root,))

    assert "development_cache" not in without_opt_in.attributes
    assert "development_cache" in with_opt_in.attributes
    assert with_opt_in.storage_category == "development_tools_and_caches"


def test_last_access_is_not_an_input_to_classification() -> None:
    result = classify(
        Path(r"C:\Users\fictional.test\Downloads\ordinary.txt"),
        age=timedelta(days=1),
    )

    assert result.attributes == ()
    assert all(
        evidence["code"] != "last_access_before_cutoff"
        for evidence in result.evidence
    )
