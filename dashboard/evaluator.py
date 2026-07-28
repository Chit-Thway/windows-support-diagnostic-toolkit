"""Deterministic support-status evaluation for validated reports."""

from __future__ import annotations

from collections import Counter
from typing import Any

HEALTHY = "Healthy"
WARNING = "Warning"
PROBLEM = "Problem"
UNAVAILABLE = "Unavailable"

STATUS_PRIORITY = {
    HEALTHY: 0,
    UNAVAILABLE: 1,
    WARNING: 2,
    PROBLEM: 3,
}


def _check(
    category: str,
    status: str,
    title: str,
    evidence: str,
    explanation: str,
    next_action: str = "",
) -> dict[str, str]:
    return {
        "category": category,
        "status": status,
        "status_class": status.lower(),
        "title": title,
        "evidence": evidence,
        "explanation": explanation,
        "next_action": next_action,
    }


def _section_status(report: dict[str, Any], section: str) -> str:
    return (
        report.get("collection_summary", {})
        .get("sections", {})
        .get(section, "failed")
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _evaluate_system(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    system = dict(report.get("system") or {})
    section_status = _section_status(report, "system")
    required_values = (
        system.get("hostname"),
        system.get("windows_edition"),
        system.get("windows_version"),
        system.get("windows_build"),
    )

    if section_status != "success" or any(value is None for value in required_values):
        status = UNAVAILABLE
        check = _check(
            "Device",
            status,
            "Device overview is incomplete",
            f"System collection status: {section_status}.",
            "One or more device or Windows fields could not be collected.",
            "Review the collection errors and rerun the collector in a normal "
            "Windows PowerShell session.",
        )
    else:
        status = HEALTHY
        check = _check(
            "Device",
            status,
            "Device overview collected",
            f"{system.get('windows_edition')} build {system.get('windows_build')}.",
            "The expected device and operating-system summary is available.",
        )

    system["status"] = status
    system["status_class"] = status.lower()
    return system, check


def _evaluate_memory(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    resources = report.get("resources") or {}
    memory = dict(resources.get("memory") or {})
    percent_used = memory.get("percent_used")
    section_status = _section_status(report, "resources")

    if section_status == "failed" or not _is_number(percent_used):
        status = UNAVAILABLE
        check = _check(
            "Memory",
            status,
            "Memory usage is unavailable",
            f"Resources collection status: {section_status}.",
            "The dashboard cannot assess memory without a numeric usage snapshot.",
            "Review collection errors and generate a fresh report.",
        )
    elif percent_used >= 90:
        status = PROBLEM
        check = _check(
            "Memory",
            status,
            "Memory usage is very high",
            f"{percent_used:.2f}% of physical memory was in use.",
            "Very high memory pressure can make applications slow or unresponsive. "
            "This is a point-in-time snapshot, not a long-term diagnosis.",
            "Open Task Manager, identify unusually memory-heavy applications, save "
            "work, and close only applications you recognize and no longer need.",
        )
    elif percent_used >= 80:
        status = WARNING
        check = _check(
            "Memory",
            status,
            "Memory usage is elevated",
            f"{percent_used:.2f}% of physical memory was in use.",
            "Available memory is limited and performance may be affected if usage rises. "
            "This is a point-in-time snapshot.",
            "Use Task Manager to review memory use and repeat the check after closing "
            "unneeded applications.",
        )
    else:
        status = HEALTHY
        check = _check(
            "Memory",
            status,
            "Memory usage is within the healthy range",
            f"{percent_used:.2f}% of physical memory was in use.",
            "The snapshot is below the configured 80% warning threshold.",
        )

    memory["status"] = status
    memory["status_class"] = status.lower()
    return memory, check


def _evaluate_disk(disk: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    disk_view = dict(disk)
    drive = disk.get("drive", "Unknown drive")
    free_gb = disk.get("free_gb")
    percent_free = disk.get("percent_free")

    if not _is_number(free_gb) or not _is_number(percent_free):
        status = UNAVAILABLE
        check = _check(
            "Disk",
            status,
            f"{drive} free space is unavailable",
            "Free GB or percentage free is missing.",
            "The disk cannot be assessed without both measurements.",
            "Generate a fresh report and review any resource collection errors.",
        )
    elif percent_free < 5:
        status = PROBLEM
        check = _check(
            "Disk",
            status,
            f"{drive} is critically low on free space",
            f"{free_gb:.2f} GB free ({percent_free:.2f}%).",
            "Low free space can prevent updates, logs, and applications from working "
            "reliably.",
            "Open Windows Storage settings, review the largest categories, and remove "
            "or move only files you recognize. Do not delete Windows system files.",
        )
    elif percent_free < 20:
        status = WARNING
        check = _check(
            "Disk",
            status,
            f"{drive} is running low on free space",
            f"{free_gb:.2f} GB free ({percent_free:.2f}%).",
            "The drive is below the configured 20% free-space warning threshold "
            "and may need attention.",
            "Review Windows Storage settings and safely free space before it becomes "
            "critical.",
        )
    else:
        status = HEALTHY
        check = _check(
            "Disk",
            status,
            f"{drive} has sufficient free space",
            f"{free_gb:.2f} GB free ({percent_free:.2f}%).",
            "Neither the warning nor problem threshold applies.",
        )

    disk_view["status"] = status
    disk_view["status_class"] = status.lower()
    disk_view["percent_used"] = (
        round(100 - percent_free, 2) if _is_number(percent_free) else None
    )
    return disk_view, check


def _evaluate_disks(report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    disks = list((report.get("resources") or {}).get("disks") or [])
    section_status = _section_status(report, "resources")
    if not disks:
        return [], [
            _check(
                "Disk",
                UNAVAILABLE,
                "Disk information is unavailable",
                f"Resources collection status: {section_status}.",
                "No fixed-disk records were available to assess.",
                "Review collection errors and generate a fresh report.",
            )
        ]

    evaluated = [_evaluate_disk(disk) for disk in disks]
    return [item[0] for item in evaluated], [item[1] for item in evaluated]


def _evaluate_network(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    network = report.get("network") or {}
    adapters = [dict(adapter) for adapter in (network.get("adapters") or [])]
    section_status = _section_status(report, "network")

    if section_status == "failed":
        status = UNAVAILABLE
        check = _check(
            "Network",
            status,
            "Network configuration is unavailable",
            "The network collection section failed.",
            "No reliable adapter configuration was available.",
            "Review collection errors and rerun the collector.",
        )
    else:
        connected = [
            adapter
            for adapter in adapters
            if str(adapter.get("status", "")).lower() in {"connected", "up"}
        ]
        usable = [
            adapter
            for adapter in connected
            if adapter.get("ipv4_addresses")
            and adapter.get("default_gateways")
            and adapter.get("dns_servers")
        ]

        if usable:
            status = HEALTHY
            check = _check(
                "Network",
                status,
                "A usable network configuration is present",
                f"{len(usable)} connected adapter(s) include IPv4, gateway, and DNS.",
                "The expected local addressing details are present.",
            )
        elif connected:
            status = WARNING
            check = _check(
                "Network",
                status,
                "Connected adapter configuration is incomplete",
                "A connected adapter is missing an IPv4 address, gateway, or DNS server.",
                "Incomplete TCP/IP configuration can prevent local or internet access.",
                "Check the cable or Wi-Fi connection, then review adapter IP and DNS "
                "settings. Avoid changing managed settings without approval.",
            )
        elif adapters:
            status = PROBLEM
            check = _check(
                "Network",
                status,
                "No usable connected network adapter was found",
                f"{len(adapters)} adapter record(s) were collected, but none is connected.",
                "The computer may be offline. This can be intentional, so confirm the "
                "user's expected connection before treating it as a fault.",
                "Confirm airplane mode, cable, Wi-Fi, and adapter status. Do not reset "
                "network settings automatically.",
            )
        else:
            status = UNAVAILABLE if section_status == "partial" else PROBLEM
            check = _check(
                "Network",
                status,
                "No network adapter data is available",
                f"Network collection status: {section_status}.",
                "The report contains no IP-enabled adapter records.",
                "Confirm the computer is expected to be online and generate a fresh report.",
            )

    for adapter in adapters:
        adapter["status_class"] = (
            "healthy"
            if str(adapter.get("status", "")).lower() in {"connected", "up"}
            else "unavailable"
        )

    return {
        "status": status,
        "status_class": status.lower(),
        "adapters": adapters,
    }, check


def _evaluate_service(service: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    service_view = dict(service)
    name = service.get("display_name") or service.get("service_name") or "Unknown service"
    availability = str(service.get("availability", "")).lower()
    state = str(service.get("current_state") or "")
    startup = str(service.get("startup_mode") or "")

    if availability != "available":
        status = UNAVAILABLE
        check = _check(
            "Service",
            status,
            f"{name} is unavailable",
            "The collector could not read this service.",
            "An unavailable record is not proof that the service is broken.",
            "Review collection errors and confirm the service exists on this Windows version.",
        )
    elif state.lower() == "running":
        status = HEALTHY
        check = _check(
            "Service",
            status,
            f"{name} is running",
            f"Startup mode: {startup}.",
            "The service was available and running when collected.",
        )
    elif startup.lower() == "automatic":
        status = PROBLEM
        check = _check(
            "Service",
            status,
            f"{name} is configured Automatic but is stopped",
            f"Current state: {state or 'Unknown'}; startup mode: {startup}.",
            "A core service configured to run automatically may affect Windows or "
            "network support functions when stopped.",
            "Review the service and its dependencies in the Services console or local "
            "policy. Do not restart or reconfigure it without understanding the cause.",
        )
    elif startup.lower() in {"manual", "disabled"}:
        status = HEALTHY
        check = _check(
            "Service",
            status,
            f"{name} state is consistent with its startup mode",
            f"Current state: {state or 'Unknown'}; startup mode: {startup}.",
            "Manual or Disabled services are not automatically unhealthy when stopped.",
        )
    else:
        status = WARNING
        check = _check(
            "Service",
            status,
            f"{name} has an unexpected state",
            f"Current state: {state or 'Unknown'}; startup mode: {startup or 'Unknown'}.",
            "The service state cannot be confidently interpreted from the available data.",
            "Review the service configuration and related collection errors.",
        )

    service_view["status"] = status
    service_view["status_class"] = status.lower()
    return service_view, check


def _evaluate_services(
    report: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    services = list(report.get("services") or [])
    if not services:
        return [], [
            _check(
                "Service",
                UNAVAILABLE,
                "Service data is unavailable",
                "The report contains no service records.",
                "The approved service allowlist could not be assessed.",
                "Generate a new report and review service collection errors.",
            )
        ]

    evaluated = [_evaluate_service(service) for service in services]
    return [item[0] for item in evaluated], [item[1] for item in evaluated]


def _evaluate_events(report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    event_section = report.get("events") or {}
    items = [dict(item) for item in (event_section.get("items") or [])]
    section_status = _section_status(report, "events")
    critical_count = sum(item.get("level") == "Critical" for item in items)
    error_count = sum(item.get("level") == "Error" for item in items)

    for item in items:
        item["status"] = PROBLEM if item.get("level") == "Critical" else WARNING
        item["status_class"] = item["status"].lower()

    if section_status == "failed" and not items:
        status = UNAVAILABLE
        check = _check(
            "Events",
            status,
            "Recent event information is unavailable",
            "The bounded event query failed.",
            "No reliable Critical or Error event results are available.",
            "Review collection errors and query Event Viewer manually if appropriate.",
        )
    elif critical_count:
        status = PROBLEM
        check = _check(
            "Events",
            status,
            "Critical events were recorded recently",
            f"{critical_count} Critical and {error_count} Error event(s) were collected.",
            "Critical events deserve prompt review, but an event alone does not prove "
            "the root cause.",
            "Review the provider, event ID, timestamp, and surrounding context in Event "
            "Viewer. Search official vendor documentation before changing the system.",
        )
    elif error_count:
        status = WARNING
        check = _check(
            "Events",
            status,
            "Recent error events need review",
            f"{error_count} Error event(s) were collected.",
            "Windows can log errors on otherwise usable systems, so correlate them with "
            "the user's symptoms and timing.",
            "Compare provider, event ID, and time with the reported issue, then consult "
            "official documentation for that component.",
        )
    elif section_status != "success":
        status = UNAVAILABLE
        check = _check(
            "Events",
            status,
            "Event collection is incomplete",
            f"Events collection status: {section_status}.",
            "No matching events were returned, but collection was incomplete.",
            "Review collection errors before treating the absence of events as Healthy.",
        )
    else:
        status = HEALTHY
        check = _check(
            "Events",
            status,
            "No recent Critical or Error events were collected",
            "The bounded 24-hour query returned no matching events.",
            "No matching events were found within this report's limited query window.",
        )

    return {
        "status": status,
        "status_class": status.lower(),
        "lookback_hours": event_section.get("lookback_hours"),
        "maximum_events_per_log": event_section.get("maximum_events_per_log"),
        "critical_count": critical_count,
        "error_count": error_count,
        "items": items,
    }, check


def _collection_error_checks(report: dict[str, Any]) -> list[dict[str, str]]:
    checks = []
    for error in report.get("collection_errors") or []:
        checks.append(
            _check(
                "Collection",
                UNAVAILABLE,
                f"{error.get('check', 'Diagnostic check')} was unavailable",
                f"{error.get('error_type', 'Collection error')}: "
                f"{error.get('message', 'No details supplied')}",
                "This observation could not be collected. It is unavailable, not "
                "automatically broken.",
                "Review the error, permissions, and Windows support context before "
                "rerunning the collector.",
            )
        )
    return checks


def _overall_status(checks: list[dict[str, str]]) -> str:
    if not checks:
        return UNAVAILABLE
    return max(
        (check["status"] for check in checks),
        key=lambda status: STATUS_PRIORITY[status],
    )


def evaluate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Convert a validated collector report into a display-ready view model."""

    checks: list[dict[str, str]] = []
    collection_status = (
        report.get("collection_summary", {}).get("status") or "partial"
    )

    if collection_status != "complete":
        checks.append(
            _check(
                "Collection",
                UNAVAILABLE,
                "The diagnostic report is incomplete",
                f"Collection status: {collection_status}.",
                "Some observations may be missing even when other checks look healthy.",
                "Review the collection errors and rerun only after addressing access or "
                "availability issues.",
            )
        )

    system, system_check = _evaluate_system(report)
    memory, memory_check = _evaluate_memory(report)
    disks, disk_checks = _evaluate_disks(report)
    network, network_check = _evaluate_network(report)
    services, service_checks = _evaluate_services(report)
    events, event_check = _evaluate_events(report)

    checks.extend(
        [
            system_check,
            memory_check,
            *disk_checks,
            network_check,
            *service_checks,
            event_check,
            *_collection_error_checks(report),
        ]
    )

    status_counts = Counter(check["status"] for check in checks)
    for status in (HEALTHY, WARNING, PROBLEM, UNAVAILABLE):
        status_counts.setdefault(status, 0)

    findings = sorted(
        (check for check in checks if check["status"] != HEALTHY),
        key=lambda check: STATUS_PRIORITY[check["status"]],
        reverse=True,
    )

    return {
        "schema_version": report.get("schema_version"),
        "generated_at_utc": report.get("generated_at_utc"),
        "collection_status": collection_status,
        "collection_status_class": (
            "healthy" if collection_status == "complete" else "unavailable"
        ),
        "overall_status": _overall_status(checks),
        "overall_status_class": _overall_status(checks).lower(),
        "status_counts": dict(status_counts),
        "checks": checks,
        "findings": findings,
        "healthy_checks": [check for check in checks if check["status"] == HEALTHY],
        "system": system,
        "memory": memory,
        "disks": disks,
        "network": network,
        "services": services,
        "events": events,
        "collection_errors": list(report.get("collection_errors") or []),
    }
