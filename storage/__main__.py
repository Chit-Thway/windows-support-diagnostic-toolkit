"""Command-line entry point for the read-only storage scanner."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from .classifier import ClassificationOptions
from .contract import (
    StorageReportValidationError,
    StorageReportWriteError,
    validate_storage_report,
    write_storage_report,
)
from .path_policy import is_path_within
from .scanner import (
    ProgressUpdate,
    ScanConfigurationError,
    ScannerOptions,
    StorageScanner,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STORAGE_REPORT_DIRECTORY = PROJECT_ROOT / "storage-reports"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m storage",
        description=(
            "Scan metadata in selected Windows folders and create a local "
            "storage-analysis JSON report. Files are never modified."
        ),
    )
    parser.add_argument(
        "--root",
        action="append",
        required=True,
        help=(
            "Directory or explicit drive root to scan. Repeat --root for "
            "additional non-overlapping folders on the same drive."
        ),
    )
    parser.add_argument(
        "--development-cache-root",
        action="append",
        default=[],
        help=(
            "Optional cache directory inside an approved scan root. Repeat "
            "for multiple explicitly selected cache locations."
        ),
    )
    parser.add_argument(
        "--output",
        help=(
            "JSON filename or path under the ignored storage-reports folder. "
            "A timestamped filename is used by default."
        ),
    )
    parser.add_argument("--stale-days", type=int, default=730)
    parser.add_argument(
        "--large-bytes",
        type=int,
        default=1024 * 1024 * 1024,
    )
    parser.add_argument("--incomplete-min-hours", type=int, default=24)
    parser.add_argument("--temporary-min-hours", type=int, default=168)
    parser.add_argument("--max-candidates", type=int, default=5000)
    parser.add_argument(
        "--max-folder-candidates",
        type=int,
        default=2000,
        help="Maximum retained folder-candidate details.",
    )
    parser.add_argument(
        "--max-issue-records",
        type=int,
        default=1000,
        help=(
            "Maximum retained inaccessible-path and scan-error details. "
            "Total omitted counts remain in the report."
        ),
    )
    parser.add_argument(
        "--no-development-insights",
        action="store_true",
        help=(
            "Disable local Python, pip-cache, and Java discovery. Selected "
            "folders are still scanned normally."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress updates; the final result is still printed.",
    )
    return parser


def resolve_output_path(value: str | None, now: datetime | None = None) -> Path:
    """Keep machine-specific reports inside the ignored output directory."""

    report_directory = STORAGE_REPORT_DIRECTORY.resolve()
    if value is None:
        now = now or datetime.now(timezone.utc)
        filename = f"storage-report-{now:%Y%m%d-%H%M%SZ}.json"
        return report_directory / filename

    supplied = Path(value).expanduser()
    if supplied.is_absolute():
        output = supplied.resolve()
    else:
        from_current_directory = (Path.cwd() / supplied).resolve()
        if is_path_within(from_current_directory, report_directory):
            output = from_current_directory
        elif supplied.parent == Path("."):
            output = (report_directory / supplied.name).resolve()
        else:
            output = from_current_directory

    if not is_path_within(output, report_directory):
        raise ScanConfigurationError(
            "Storage reports must be written under the ignored "
            f"'{report_directory}' directory."
        )
    if output.suffix.casefold() != ".json":
        raise ScanConfigurationError("The output filename must end in .json.")
    return output


def _print_progress(update: ProgressUpdate) -> None:
    message = (
        f"Scanned {update.files_examined} file(s), "
        f"{update.directories_examined} directorie(s), "
        f"found {update.candidates_found} candidate(s)"
    )
    print(f"\r{message}", end="", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    cancellation = threading.Event()

    def request_cancellation(_signal_number, _frame) -> None:
        cancellation.set()

    previous_handler = signal.signal(signal.SIGINT, request_cancellation)
    try:
        classification_options = ClassificationOptions(
            stale_after_days=arguments.stale_days,
            large_file_threshold_bytes=arguments.large_bytes,
            incomplete_min_age_hours=arguments.incomplete_min_hours,
            temporary_min_age_hours=arguments.temporary_min_hours,
        )
        scanner_options = ScannerOptions(
            classification=classification_options,
            maximum_candidates_retained=arguments.max_candidates,
            maximum_folder_candidates_retained=(
                arguments.max_folder_candidates
            ),
            maximum_issue_records=arguments.max_issue_records,
            development_cache_roots=tuple(arguments.development_cache_root),
            discover_development_insights=(
                not arguments.no_development_insights
            ),
        )
        output_path = resolve_output_path(arguments.output)
        scanner = StorageScanner()
        report = scanner.scan(
            arguments.root,
            options=scanner_options,
            cancel_check=cancellation.is_set,
            progress_callback=None if arguments.quiet else _print_progress,
        )
        validate_storage_report(report)
        written_path = write_storage_report(report, output_path)
    except (
        ScanConfigurationError,
        StorageReportValidationError,
        StorageReportWriteError,
        ValueError,
    ) as error:
        if not arguments.quiet:
            print(file=sys.stderr)
        print(f"Storage scan failed: {error}", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    if not arguments.quiet:
        print(file=sys.stderr)
    print(f"Report status: {report['scan']['status']}")
    print(f"Files examined: {report['scan']['files_examined']}")
    print(
        "Candidates: "
        f"{report['candidate_summary']['total_unique_candidates']}"
    )
    print(
        "Folder candidates: "
        f"{report['folder_candidate_summary']['total_candidates']}"
    )
    print(f"Report saved to: {written_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
