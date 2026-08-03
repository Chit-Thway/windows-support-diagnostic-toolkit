from __future__ import annotations

import os
from pathlib import Path

from storage.development import CommandResult, DevelopmentInsightsInspector


def test_supported_discovery_reports_python_pip_and_java_without_new_scans(
    tmp_path: Path,
) -> None:
    environment_root = tmp_path / "support-app" / ".venv"
    environment_root.mkdir(parents=True)
    marker = environment_root / "pyvenv.cfg"
    marker.write_text("fictional marker", encoding="utf-8")
    package_file = environment_root / "Lib" / "site-packages" / "demo.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_bytes(b"python package")
    pip_cache = tmp_path / "pip-cache"
    pip_cache.mkdir()
    wheel = pip_cache / "demo.whl"
    wheel.write_bytes(b"cached wheel")
    java_home = tmp_path / "jdk-21"
    java_home.mkdir()
    java_file = java_home / "bin" / "java.exe"
    java_file.parent.mkdir()
    java_file.write_bytes(b"fictional java")

    def runner(command) -> CommandResult:
        if tuple(command[1:]) == ("-m", "pip", "cache", "dir"):
            return CommandResult(0, str(pip_cache), "")
        return CommandResult(
            0,
            "",
            f"    java.home = {java_home}\n    java.version = 21.0.4\n",
        )

    inspector = DevelopmentInsightsInspector(
        scan_roots=(tmp_path,),
        python_executable=r"C:\FictionalPython\python.exe",
        active_python_environments=(environment_root,),
        command_runner=runner,
        java_finder=lambda _name: r"C:\FictionalJava\bin\java.exe",
    )
    with os.scandir(environment_root) as entries:
        inspector.observe_directory(environment_root, list(entries))
    for path in (marker, package_file, wheel, java_file):
        assert inspector.observe_file(path, path.stat().st_size) is True

    report = inspector.build_report(scan_status="complete")
    by_kind = {location["kind"]: location for location in report["locations"]}

    assert report["status"] == "complete"
    assert by_kind["virtual_environment"]["active"] is True
    assert by_kind["virtual_environment"]["bytes_observed"] == (
        marker.stat().st_size + package_file.stat().st_size
    )
    assert by_kind["package_cache"]["suggested_command"] == (
        "python -m pip cache purge"
    )
    assert by_kind["package_cache"]["automatic_cleanup_candidate"] is False
    assert by_kind["runtime_or_sdk"]["version"] == "21.0.4"
    assert all(
        location["measurement"] == "observed_selected_roots"
        for location in report["locations"]
    )


def test_out_of_scope_locations_are_displayed_but_not_measured(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "fictional-pip-cache"

    def runner(command) -> CommandResult:
        assert tuple(command[1:]) == ("-m", "pip", "cache", "dir")
        return CommandResult(0, str(outside), "")

    inspector = DevelopmentInsightsInspector(
        scan_roots=(tmp_path,),
        command_runner=runner,
        java_finder=lambda _name: None,
    )

    location = inspector.build_report(scan_status="complete")["locations"][0]

    assert location["within_scan_scope"] is False
    assert location["bytes_observed"] is None
    assert location["files_observed"] is None
    assert location["measurement"] == "not_measured"
    assert location["coverage"] == "not_applicable"


def test_invalid_pyvenv_marker_is_reported_without_claiming_an_environment(
    tmp_path: Path,
) -> None:
    environment_root = tmp_path / "fictional-project" / ".venv"
    invalid_marker = environment_root / "pyvenv.cfg"
    invalid_marker.mkdir(parents=True)
    inspector = DevelopmentInsightsInspector(
        scan_roots=(tmp_path,),
        command_runner=lambda _command: CommandResult(2, "", ""),
        java_finder=lambda _name: None,
    )

    with os.scandir(environment_root) as entries:
        inspector.observe_directory(environment_root, list(entries))

    report = inspector.build_report(scan_status="complete")

    assert report["locations"] == []
    assert any(
        error["code"] == "pyvenv_marker_not_regular"
        for error in report["errors"]
    )


def test_failed_supported_query_is_structured_and_nonfatal(tmp_path: Path) -> None:
    inspector = DevelopmentInsightsInspector(
        scan_roots=(tmp_path,),
        command_runner=lambda _command: CommandResult(2, "", "unavailable"),
        java_finder=lambda _name: None,
    )

    report = inspector.build_report(scan_status="complete")

    assert report["status"] == "partial"
    assert report["locations"] == []
    assert report["errors"][0]["code"] == "pip_cache_query_unavailable"


def test_java_home_is_used_only_as_a_reliable_fallback(tmp_path: Path) -> None:
    java_home = tmp_path / "configured-java"
    java_home.mkdir()
    inspector = DevelopmentInsightsInspector(
        scan_roots=(tmp_path,),
        environment={"JAVA_HOME": str(java_home)},
        command_runner=lambda _command: CommandResult(2, "", ""),
        java_finder=lambda _name: None,
    )

    java = next(
        location
        for location in inspector.build_report(scan_status="complete")[
            "locations"
        ]
        if location["ecosystem"] == "java"
    )

    assert java["source"] == "java_home"
    assert java["cleanup_policy"] == "informational_only"


def test_discovery_can_be_explicitly_disabled_without_commands(tmp_path: Path) -> None:
    calls = []
    inspector = DevelopmentInsightsInspector(
        scan_roots=(tmp_path,),
        command_runner=lambda command: calls.append(command),
        java_finder=lambda _name: None,
        enabled=False,
    )

    report = inspector.build_report(scan_status="complete")

    assert calls == []
    assert report["status"] == "unavailable"
    assert report["locations"] == []
