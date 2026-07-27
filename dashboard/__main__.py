"""Run the dashboard on the Windows loopback interface."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from .app import create_app
from .report_loader import resolve_report_path

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 5000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Display a local Windows diagnostic report."
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
        type=int,
        default=DEFAULT_PORT,
        help=f"Loopback port to use (default: {DEFAULT_PORT}).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    report_path = resolve_report_path(args.report)
    app = create_app(report_path=report_path)
    app.run(
        host=LOOPBACK_HOST,
        port=args.port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
