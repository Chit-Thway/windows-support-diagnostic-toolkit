"""Prepare validated storage data for the per-drive dashboard."""

from __future__ import annotations

from datetime import datetime
from pathlib import PureWindowsPath
from typing import Any

CATEGORY_PRESENTATION = (
    ("free_space", "Free space", "free"),
    ("protected_system", "Windows and system", "system"),
    ("installed_applications", "Installed applications", "applications"),
    ("user_content", "User content", "user"),
    (
        "development_tools_and_caches",
        "Development tools and caches",
        "development",
    ),
    ("other_or_unreadable", "Other or unreadable", "other"),
)

ATTRIBUTE_PRESENTATION = (
    ("stale", "Stale"),
    ("likely_incomplete", "Likely incomplete"),
    ("large", "Large"),
    ("empty", "Empty"),
    ("temporary", "Temporary"),
    ("development_cache", "Development cache"),
)

DEVELOPMENT_KIND_LABELS = {
    "virtual_environment": "Python virtual environment",
    "package_cache": "Package cache",
    "runtime_or_sdk": "Runtime or SDK",
}

DEVELOPMENT_SOURCE_LABELS = {
    "pyvenv_cfg": "pyvenv.cfg marker",
    "pip_cache_dir": "pip cache dir command",
    "java_system_properties": "Java system properties",
    "java_home": "JAVA_HOME",
}


def format_bytes(value: int | None) -> str:
    """Return a compact binary-size label without losing the byte total."""

    if value is None:
        return "Unavailable"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount):,} {unit}"
            return f"{amount:,.2f} {unit}"
        amount /= 1024
    return f"{value:,} B"


def _title_token(value: str) -> str:
    return value.replace("_", " ").title()


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _present_development_insights(report: dict[str, Any]) -> dict[str, Any]:
    insights = report.get("development_insights")
    if insights is None:
        return {
            "status": "unavailable",
            "status_label": "Unavailable",
            "status_class": "unavailable",
            "locations": [],
            "python_locations": [],
            "java_locations": [],
            "errors": [],
            "errors_omitted": 0,
            "limitations": [
                "This older storage report does not contain development-storage insights."
            ],
        }

    locations = []
    for location in insights["locations"]:
        active = location["active"]
        locations.append(
            {
                **location,
                "ecosystem_label": location["ecosystem"].title(),
                "kind_label": DEVELOPMENT_KIND_LABELS[location["kind"]],
                "source_label": DEVELOPMENT_SOURCE_LABELS[location["source"]],
                "size_display": format_bytes(location["bytes_observed"]),
                "files_display": (
                    f"{location['files_observed']:,}"
                    if location["files_observed"] is not None
                    else "Not measured"
                ),
                "measurement_label": _title_token(location["measurement"]),
                "coverage_label": _title_token(location["coverage"]),
                "active_label": (
                    "Active for this scan"
                    if active is True
                    else "Not active for this scan"
                    if active is False
                    else "Not applicable"
                ),
                "scope_label": (
                    "Measured in selected roots"
                    if location["within_scan_scope"]
                    else "Outside selected roots — not measured"
                ),
                "cleanup_policy_label": _title_token(
                    location["cleanup_policy"]
                ),
            }
        )

    return {
        **insights,
        "errors_omitted": insights.get("errors_omitted", 0),
        "status_label": _title_token(insights["status"]),
        "status_class": (
            "healthy" if insights["status"] == "complete" else "unavailable"
        ),
        "locations": locations,
        "python_locations": [
            location
            for location in locations
            if location["ecosystem"] == "python"
        ],
        "java_locations": [
            location
            for location in locations
            if location["ecosystem"] == "java"
        ],
    }
def present_storage_report(
    report: dict[str, Any], diagnostic_status: str
) -> dict[str, Any]:
    """Create non-overlapping chart and summary values for the template."""

    drive = report["drive"]
    scan = report["scan"]
    accounting = report["accounting"]
    total_bytes = drive["total_bytes"]

    categories: list[dict[str, Any]] = []
    offset = 0.0
    for key, label, style_class in CATEGORY_PRESENTATION:
        category = accounting["categories"][key]
        percent = category["bytes"] / total_bytes * 100
        categories.append(
            {
                "key": key,
                "label": label,
                "style_class": style_class,
                "bytes": category["bytes"],
                "size": format_bytes(category["bytes"]),
                "exact_bytes": f"{category['bytes']:,} bytes",
                "percent": round(percent, 2),
                "chart_percent": round(percent, 6),
                "chart_offset": round(-offset, 6),
                "measurement": _title_token(category["measurement"]),
                "explanation": category["explanation"],
            }
        )
        offset += percent

    summary = report["candidate_summary"]
    candidate_attributes = []
    for key, label in ATTRIBUTE_PRESENTATION:
        attribute = summary["attributes"][key]
        candidate_attributes.append(
            {
                "key": key,
                "label": label,
                "count": attribute["candidate_count"],
                "bytes": attribute["unique_bytes"],
                "size": format_bytes(attribute["unique_bytes"]),
            }
        )

    generated_at = _parse_utc(report["generated_at_utc"])
    candidates = []
    for candidate in report["candidates"]:
        modified_at = _parse_utc(candidate["modified_at_utc"])
        age_days = None
        if generated_at is not None and modified_at is not None:
            age_days = max(0, (generated_at - modified_at).days)

        attributes = [
            {
                "key": attribute,
                "label": _title_token(attribute),
            }
            for attribute in candidate["attributes"]
        ]
        path = PureWindowsPath(candidate["path"])
        eligibility = candidate["protection"]["eligibility"]
        removal_risk = candidate.get(
            "removal_risk",
            "protected"
            if eligibility in {"protected", "unavailable"}
            else "medium",
        )
        allocated_size = candidate.get("allocated_size_bytes")
        reclaimable_size = (
            allocated_size
            if allocated_size is not None
            else candidate["size_bytes"] or 0
        )
        candidates.append(
            {
                **candidate,
                "directory": str(path.parent),
                "size": format_bytes(candidate["size_bytes"]),
                "allocated_size": format_bytes(allocated_size),
                "size_sort": reclaimable_size,
                "age_days": age_days,
                "attributes_view": attributes,
                "attributes_filter": ",".join(candidate["attributes"]),
                "confidence_label": _title_token(candidate["confidence"]),
                "removal_risk": removal_risk,
                "removal_risk_label": _title_token(removal_risk),
                "eligibility": eligibility,
                "eligibility_label": _title_token(eligibility),
                "selectable": (
                    eligibility == "eligible"
                    and candidate["is_regular_file"]
                    and not candidate["is_reparse_point"]
                ),
                "folder_openable": (
                    eligibility in {"eligible", "review_only"}
                    and candidate["is_regular_file"]
                    and not candidate["is_reparse_point"]
                ),
            }
        )

    root_options = sorted(
        {candidate["scan_root"] for candidate in candidates},
        key=str.casefold,
    )

    roots = []
    for root in report["scan_scope"]["roots"]:
        roots.append(
            {
                **root,
                "display_path": root["canonical_path"] or root["requested_path"],
                "status_label": _title_token(root["status"]),
                "bytes_examined_display": format_bytes(root["bytes_examined"]),
                "allocated_bytes_examined_display": format_bytes(
                    root.get("allocated_bytes_examined")
                ),
            }
        )

    return {
        "schema_version": report["schema_version"],
        "generated_at_utc": report["generated_at_utc"],
        "drive": {
            **drive,
            "diagnostic_status": diagnostic_status,
            "diagnostic_status_class": diagnostic_status.lower(),
            "total_size": format_bytes(drive["total_bytes"]),
            "used_size": format_bytes(drive["used_bytes"]),
            "free_size": format_bytes(drive["free_bytes"]),
            "percent_used": round(100 - drive["percent_free"], 2),
        },
        "scan": {
            **scan,
            "status_label": _title_token(scan["status"]),
            "status_class": (
                "healthy" if scan["status"] == "complete" else "unavailable"
            ),
            "detail_coverage_label": _title_token(scan["detail_coverage"]),
            "aggregate_coverage_label": _title_token(
                scan["aggregate_coverage"]
            ),
            "bytes_examined_display": format_bytes(scan["bytes_examined"]),
            "allocated_bytes_examined_display": format_bytes(
                scan.get("allocated_bytes_examined")
            ),
            "files_examined_display": f"{scan['files_examined']:,}",
            "directories_examined_display": f"{scan['directories_examined']:,}",
            "duration_seconds": round(scan["duration_ms"] / 1000, 2),
            "inaccessible_paths_total": (
                len(report["inaccessible_paths"])
                + scan.get("inaccessible_path_details_omitted", 0)
            ),
            "issue_details_omitted": (
                scan.get("inaccessible_path_details_omitted", 0)
                + scan.get("scan_error_details_omitted", 0)
            ),
        },
        "accounting_coverage": _title_token(accounting["coverage"]),
        "categories": categories,
        "candidate_summary": {
            **summary,
            "total_unique_candidate_size": format_bytes(
                summary["total_unique_candidate_bytes"]
            ),
            "retained_unique_candidate_size": format_bytes(
                summary["retained_unique_candidate_bytes"]
            ),
            "total_unique_candidate_allocated_size": format_bytes(
                summary.get("total_unique_candidate_allocated_bytes")
            ),
            "retained_unique_candidate_allocated_size": format_bytes(
                summary.get("retained_unique_candidate_allocated_bytes")
            ),
            "attributes": candidate_attributes,
            "excluded_count": sum(
                1
                for candidate in candidates
                if candidate["eligibility"] != "eligible"
            ),
        },
        "candidates": candidates,
        "candidate_root_options": root_options,
        "roots": roots,
        "inaccessible_paths": report["inaccessible_paths"],
        "scan_errors": report["scan_errors"],
        "development": _present_development_insights(report),
        "limitations": report["limitations"],
    }
