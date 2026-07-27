"""Flask application factory for the local support dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask, Response, render_template

from .evaluator import evaluate_report
from .report_loader import ReportLoadError, load_report, resolve_report_path


def create_app(
    report_path: str | Path | None = None,
    test_config: dict[str, Any] | None = None,
) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_mapping(
        REPORT_PATH=str(resolve_report_path(report_path)),
    )
    if test_config:
        app.config.update(test_config)

    @app.after_request
    def add_local_privacy_headers(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    def dashboard() -> tuple[str, int] | str:
        selected_report_path = Path(app.config["REPORT_PATH"])
        try:
            report = load_report(selected_report_path)
        except ReportLoadError as error:
            return (
                render_template(
                    "report_error.html",
                    error_title=error.title,
                    error_detail=error.detail,
                    selected_report_path=selected_report_path,
                    status_code=error.status_code,
                ),
                error.status_code,
            )

        return render_template(
            "dashboard.html",
            dashboard=evaluate_report(report),
            selected_report_path=selected_report_path,
        )

    return app
