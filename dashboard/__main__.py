"""Run the dashboard on the Windows loopback interface."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .app import create_app
from .report_loader import resolve_report_path
from .storage_report_loader import resolve_storage_report_path

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 5000


def loopback_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("port must be a whole number") from error

    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Display local Windows diagnostic and storage reports."
    )
    parser.add_argument(
        "--report",
        help=(
            "Path to a diagnostic JSON report. Overrides "
            "DIAGNOSTIC_REPORT_PATH and the default sample."
        ),
    )
    parser.add_argument(
        "--port",
        type=loopback_port,
        default=DEFAULT_PORT,
        help=f"Loopback port to use (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--storage-report",
        help=(
            "Path to a storage-analysis JSON report. Overrides "
            "STORAGE_REPORT_PATH. When omitted for a custom diagnostic report, "
            "the drive page shows local scan instructions."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    report_path = resolve_report_path(args.report)
    storage_report_path = resolve_storage_report_path(
        args.storage_report,
        diagnostic_report_path=report_path,
    )
    app = create_app(
        report_path=report_path,
        storage_report_path=storage_report_path,
    )
    app.run(
        host=LOOPBACK_HOST,
        port=args.port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
