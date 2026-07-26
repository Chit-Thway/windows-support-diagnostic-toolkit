# Diagnostic Report Contract

Version: `1.0.0`

The PowerShell collector writes one local UTF-8 JSON document per run. The report contains observations only. A later milestone will evaluate those observations as Healthy, Warning, or Problem.

The formal JSON Schema is [`schema/report.schema.json`](../schema/report.schema.json).

## Local-data boundary

Reports are created and processed entirely on the user's computer. The collector does not upload reports, use telemetry, make public-IP requests, or transmit diagnostic data.

Real reports can contain unmasked hostnames, signed-in usernames, local IPv4 addresses, gateways, DNS addresses, local paths, and bounded event messages. For that reason, `reports/` is ignored by Git. Only fictional reports under `sample_data/` and `tests/fixtures/` are suitable for the public repository.

The collector never reads passwords, authentication tokens, Windows product keys, saved Wi-Fi passwords or profiles, browser history, personal documents, public IP services, or the Windows Security event log.

## Top-level structure

```json
{
  "schema_version": "1.0.0",
  "generated_at_utc": "2026-07-26T14:10:30.0000000Z",
  "collector": {},
  "collection_summary": {},
  "system": {},
  "resources": {},
  "network": {},
  "services": [],
  "events": {},
  "collection_errors": []
}
```

| Property | Purpose |
| --- | --- |
| `schema_version` | Contract version used to read the report safely. |
| `generated_at_utc` | UTC time at which report collection finished. |
| `collector` | Collector name, version, script name, and PowerShell version. |
| `collection_summary` | Start/end times, duration, overall completeness, and section results. |
| `system` | Windows, computer, processor, and boot observations. |
| `resources` | Point-in-time memory values and fixed-disk capacity values. |
| `network` | Adapter status and full local IPv4, gateway, and configured DNS values. |
| `services` | The six approved Windows services and their availability/state/startup mode. |
| `events` | The bounded event-query settings and collected event records. |
| `collection_errors` | Structured errors for checks that could not be completed. |

## Collector metadata

```json
{
  "name": "Windows Support Diagnostic Toolkit",
  "version": "0.1.0",
  "script_name": "Collect-Diagnostics.ps1",
  "powershell_version": "5.1.26100.4652"
}
```

## Collection summary

`status` is:

- `complete` when every section succeeds and `collection_errors` is empty.
- `partial` when one or more checks are partial or failed.

Each of `system`, `resources`, `network`, `services`, and `events` has a section status of `success`, `partial`, or `failed`.

## System

```json
{
  "hostname": "LAB-SAMPLE-01",
  "signed_in_username": "FICTIONAL\\sam.lee",
  "windows_edition": "Microsoft Windows 11 Pro",
  "windows_version": "10.0.26100",
  "windows_build": "26100",
  "os_architecture": "64-bit",
  "manufacturer": "Contoso Devices",
  "model": "SupportBook 14",
  "processor_name": "Intel(R) Core(TM) i5-1135G7 @ 2.40GHz",
  "logical_processor_count": 8,
  "last_boot_time_utc": "2026-07-26T01:15:00.0000000Z",
  "uptime_seconds": 46530
}
```

If the system query fails, unavailable values are `null` and the error is recorded.

## Resources

Memory values are a point-in-time snapshot:

```json
{
  "memory": {
    "observed_at_utc": "2026-07-26T14:10:29.0000000Z",
    "total_gb": 16.0,
    "available_gb": 7.25,
    "used_gb": 8.75,
    "percent_used": 54.69
  },
  "disks": [
    {
      "drive": "C:",
      "total_gb": 476.94,
      "free_gb": 186.20,
      "percent_free": 39.04
    }
  ]
}
```

Numeric measurements are numbers, not formatted display strings.

## Network

One item is created for each IP-enabled adapter:

```json
{
  "adapters": [
    {
      "name": "Ethernet",
      "status": "Connected",
      "ipv4_addresses": ["192.168.50.24"],
      "default_gateways": ["192.168.50.1"],
      "dns_servers": ["192.168.50.1", "1.1.1.1"]
    }
  ]
}
```

Values are stored in full. No public-IP request is made.

## Services

The report always attempts these service names:

- `EventLog`
- `Winmgmt`
- `BFE`
- `Dhcp`
- `Dnscache`
- `NlaSvc`

Each record contains:

```json
{
  "service_name": "EventLog",
  "display_name": "Windows Event Log",
  "availability": "available",
  "current_state": "Running",
  "startup_mode": "Automatic"
}
```

For a missing or unreadable service, `availability` is `unavailable`, state/startup mode are `null`, and a collection error is recorded. Availability is not itself a diagnosis.

## Events

The contract fixes the query to:

- `Application` and `System` logs only.
- Critical and Error levels only.
- Previous 24 hours.
- At most 10 newest records per log.
- At most 500 characters of actual, unredacted message content per record.

```json
{
  "lookback_hours": 24,
  "maximum_events_per_log": 10,
  "items": [
    {
      "log_name": "Application",
      "event_id": 1000,
      "provider_name": "Application Error",
      "level": "Error",
      "time_created_utc": "2026-07-26T12:40:00.0000000Z",
      "message": "Fictional application failure in C:\\Program Files\\Contoso\\SupportApp.exe."
    }
  ]
}
```

If either log query fails, the other log is still queried and a structured error is added.

## Collection errors

```json
{
  "section": "events",
  "check": "System event log",
  "error_type": "UnauthorizedAccessException",
  "message": "Access to the requested log was denied.",
  "occurred_at_utc": "2026-07-26T14:10:29.0000000Z"
}
```

Collection errors describe unavailable observations. They do not claim that Windows is broken.

## Compatibility rules

- Readers must reject unsupported major schema versions.
- New optional fields require a minor version.
- Breaking changes require a new major version.
- Arrays remain arrays when empty or when they contain a single item.
- UTC timestamps use ISO 8601.
- Real reports remain under `reports/` and must not be force-added to Git.
