"""Supported, metadata-only discovery for development storage insights."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .path_policy import is_path_within, is_reparse_point

COMMAND_TIMEOUT_SECONDS = 8
MAX_COMMAND_OUTPUT_CHARACTERS = 32_768
MAX_DEVELOPMENT_ERRORS = 100


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str]], CommandResult]


def _default_command_runner(command: Sequence[str]) -> CommandResult:
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
        creationflags=creation_flags,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout[:MAX_COMMAND_OUTPUT_CHARACTERS],
        stderr=completed.stderr[:MAX_COMMAND_OUTPUT_CHARACTERS],
    )


def _normalized(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _absolute_path(value: str) -> Path | None:
    expanded = Path(value.strip().strip('"')).expanduser()
    if not expanded.is_absolute() or not expanded.drive:
        return None
    return Path(os.path.abspath(str(expanded)))


@dataclass
class _ObservedLocation:
    ecosystem: str
    kind: str
    path: Path
    source: str
    display_name: str
    version: str | None
    active: bool | None
    cleanup_policy: str
    suggested_command: str | None
    explanation: str
    consequence: str
    within_scan_scope: bool
    files_observed: int = 0
    bytes_observed: int = 0


class DevelopmentInsightsInspector:
    """Discover supported development locations without scanning new roots."""

    def __init__(
        self,
        *,
        scan_roots: Iterable[Path],
        environment: Mapping[str, str] | None = None,
        python_executable: str | Path | None = None,
        active_python_environments: Iterable[str | Path] | None = None,
        command_runner: CommandRunner | None = None,
        java_finder: Callable[[str], str | None] | None = None,
        enabled: bool = True,
    ) -> None:
        self._scan_roots = tuple(Path(root) for root in scan_roots)
        self._environment = os.environ if environment is None else environment
        self._python_executable = str(python_executable or sys.executable)
        self._command_runner = command_runner or _default_command_runner
        self._java_finder = java_finder or shutil.which
        self._enabled = enabled
        self._locations: dict[tuple[str, str], _ObservedLocation] = {}
        self._errors: list[dict[str, str]] = []
        self._error_count = 0
        if active_python_environments is None:
            active_values: list[str | Path] = []
            if sys.prefix != sys.base_prefix:
                active_values.append(sys.prefix)
            if self._environment.get("VIRTUAL_ENV"):
                active_values.append(self._environment["VIRTUAL_ENV"])
            self._active_python_environments = {
                _normalized(Path(value)) for value in active_values
            }
        else:
            self._active_python_environments = {
                _normalized(Path(value)) for value in active_python_environments
            }

        if self._enabled:
            self._discover_pip_cache()
            self._discover_java_runtime()

    def _record_error(self, ecosystem: str, code: str, message: str) -> None:
        self._error_count += 1
        if len(self._errors) < MAX_DEVELOPMENT_ERRORS:
            self._errors.append(
                {"ecosystem": ecosystem, "code": code, "message": message}
            )

    def _run_supported_command(
        self, command: Sequence[str], *, ecosystem: str, error_code: str
    ) -> CommandResult | None:
        try:
            return self._command_runner(command)
        except (OSError, subprocess.SubprocessError, TimeoutError):
            self._record_error(
                ecosystem,
                error_code,
                "The supported local discovery command was unavailable or timed out.",
            )
            return None

    def _add_location(
        self,
        *,
        ecosystem: str,
        kind: str,
        path: Path,
        source: str,
        display_name: str,
        version: str | None,
        active: bool | None,
        cleanup_policy: str,
        suggested_command: str | None,
        explanation: str,
        consequence: str,
    ) -> _ObservedLocation:
        normalized = _normalized(path)
        key = (kind, normalized)
        existing = self._locations.get(key)
        if existing is not None:
            return existing
        location = _ObservedLocation(
            ecosystem=ecosystem,
            kind=kind,
            path=Path(os.path.abspath(str(path))),
            source=source,
            display_name=display_name,
            version=version,
            active=active,
            cleanup_policy=cleanup_policy,
            suggested_command=suggested_command,
            explanation=explanation,
            consequence=consequence,
            within_scan_scope=any(
                is_path_within(path, root) for root in self._scan_roots
            ),
        )
        self._locations[key] = location
        return location

    def _discover_pip_cache(self) -> None:
        result = self._run_supported_command(
            (self._python_executable, "-m", "pip", "cache", "dir"),
            ecosystem="python",
            error_code="pip_cache_query_failed",
        )
        if result is None:
            return
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        cache_path = _absolute_path(lines[-1]) if result.returncode == 0 and lines else None
        if cache_path is None:
            self._record_error(
                "python",
                "pip_cache_query_unavailable",
                "pip did not return a usable local cache directory.",
            )
            return
        self._add_location(
            ecosystem="python",
            kind="package_cache",
            path=cache_path,
            source="pip_cache_dir",
            display_name="pip download cache",
            version=None,
            active=None,
            cleanup_policy="supported_command_only",
            suggested_command="python -m pip cache purge",
            explanation=(
                "pip reported this cache through the supported "
                "'python -m pip cache dir' command."
            ),
            consequence=(
                "Purging the cache removes reusable package downloads; future "
                "installs may need to download them again."
            ),
        )

    @staticmethod
    def _java_properties(output: str) -> dict[str, str]:
        properties: dict[str, str] = {}
        for line in output.splitlines():
            stripped = line.strip()
            if " = " not in stripped:
                continue
            key, value = stripped.split(" = ", 1)
            if key in {"java.home", "java.version"} and value:
                properties[key] = value.strip()
        return properties

    def _discover_java_runtime(self) -> None:
        java_executable = self._java_finder("java")
        if java_executable:
            result = self._run_supported_command(
                (java_executable, "-XshowSettings:properties", "-version"),
                ecosystem="java",
                error_code="java_properties_query_failed",
            )
            if result is not None and result.returncode == 0:
                properties = self._java_properties(
                    f"{result.stdout}\n{result.stderr}"
                )
                java_home = _absolute_path(properties.get("java.home", ""))
                if java_home is not None:
                    version = properties.get("java.version")
                    self._add_location(
                        ecosystem="java",
                        kind="runtime_or_sdk",
                        path=java_home,
                        source="java_system_properties",
                        display_name=(
                            f"Java {version}" if version else "Java runtime or SDK"
                        ),
                        version=version,
                        active=True,
                        cleanup_policy="informational_only",
                        suggested_command=None,
                        explanation=(
                            "The Java launcher on PATH reported this java.home "
                            "through its supported system-properties output."
                        ),
                        consequence=(
                            "Removing a runtime or SDK can break applications, "
                            "builds, and projects that depend on that version."
                        ),
                    )
                    return
                self._record_error(
                    "java",
                    "java_home_query_unavailable",
                    "The Java launcher did not return a usable java.home property.",
                )
            elif result is not None:
                self._record_error(
                    "java",
                    "java_properties_query_unavailable",
                    "The Java launcher could not return supported system properties.",
                )

        java_home_value = self._environment.get("JAVA_HOME")
        java_home = _absolute_path(java_home_value or "")
        if java_home is not None and java_home.is_dir():
            self._add_location(
                ecosystem="java",
                kind="runtime_or_sdk",
                path=java_home,
                source="java_home",
                display_name="Java runtime or SDK",
                version=None,
                active=True,
                cleanup_policy="informational_only",
                suggested_command=None,
                explanation="JAVA_HOME identifies this configured Java location.",
                consequence=(
                    "Removing a runtime or SDK can break applications, builds, "
                    "and projects that depend on it."
                ),
            )

    def observe_directory(self, directory: Path, entries: Iterable[Any]) -> None:
        """Detect a virtual environment marker without reading its contents."""

        if not self._enabled:
            return
        marker_entry = next(
            (
                entry
                for entry in entries
                if entry.name.casefold() == "pyvenv.cfg"
            ),
            None,
        )
        if marker_entry is None:
            return

        marker = Path(marker_entry.path)
        valid_marker = False
        try:
            metadata = marker_entry.stat(follow_symlinks=False)
            valid_marker = stat.S_ISREG(metadata.st_mode) and not is_reparse_point(
                marker, metadata
            )
        except OSError:
            self._record_error(
                "python",
                "pyvenv_marker_unreadable",
                "A pyvenv.cfg marker was present but its metadata was unavailable.",
            )
            return

        if not valid_marker:
            self._record_error(
                "python",
                "pyvenv_marker_not_regular",
                "A pyvenv.cfg marker was not a regular non-reparse-point file.",
            )
            return

        environment_path = Path(os.path.abspath(str(directory)))
        active = _normalized(environment_path) in self._active_python_environments
        self._add_location(
            ecosystem="python",
            kind="virtual_environment",
            path=environment_path,
            source="pyvenv_cfg",
            display_name=environment_path.name or "Python virtual environment",
            version=None,
            active=active,
            cleanup_policy="informational_only",
            suggested_command=None,
            explanation=(
                "A pyvenv.cfg marker identifies this Python virtual environment; "
                "the marker contents were not read."
            ),
            consequence=(
                "Removing an environment discards its installed packages and "
                "may require the project dependencies to be recreated."
            ),
        )

    def observe_file(self, path: Path, size_bytes: int) -> bool:
        """Account a file and report whether it belongs to an insight location."""

        matched = False
        for location in self._locations.values():
            if location.within_scan_scope and is_path_within(path, location.path):
                location.files_observed += 1
                location.bytes_observed += size_bytes
                matched = True
        return matched

    def build_report(self, *, scan_status: str) -> dict[str, Any]:
        locations = []
        for index, location in enumerate(
            sorted(
                self._locations.values(),
                key=lambda item: (
                    item.ecosystem,
                    item.kind,
                    str(item.path).casefold(),
                ),
            ),
            start=1,
        ):
            measured = location.within_scan_scope
            locations.append(
                {
                    "location_id": f"development-{index:03d}",
                    "ecosystem": location.ecosystem,
                    "kind": location.kind,
                    "path": str(location.path),
                    "source": location.source,
                    "display_name": location.display_name,
                    "version": location.version,
                    "active": location.active,
                    "within_scan_scope": location.within_scan_scope,
                    "files_observed": location.files_observed if measured else None,
                    "bytes_observed": location.bytes_observed if measured else None,
                    "measurement": (
                        "observed_selected_roots" if measured else "not_measured"
                    ),
                    "coverage": (
                        "complete"
                        if measured and scan_status == "complete"
                        else "partial"
                        if measured
                        else "not_applicable"
                    ),
                    "cleanup_policy": location.cleanup_policy,
                    "suggested_command": location.suggested_command,
                    "automatic_cleanup_candidate": False,
                    "explanation": location.explanation,
                    "consequence": location.consequence,
                }
            )

        errors_omitted = self._error_count - len(self._errors)
        limitations = [
            "Only pyvenv.cfg metadata identifies Python environments; age does not prove an environment is abandoned.",
            "Development locations outside selected scan roots are displayed but not measured recursively.",
            "No cross-vendor Java cache is assumed because Java distributions do not expose one universal supported cache location.",
            "Runtimes and environments are informational and never automatic cleanup candidates.",
        ]
        if errors_omitted:
            limitations.append(
                f"{errors_omitted} additional development-discovery error "
                "record(s) were omitted by the safety limit."
            )

        return {
            "status": (
                "unavailable"
                if not self._enabled
                else "partial"
                if self._error_count
                else "complete"
            ),
            "locations": locations,
            "errors": self._errors,
            "errors_omitted": errors_omitted,
            "limitations": limitations,
        }
