# PowerShell Collector

`Collect-Diagnostics.ps1` is a read-only Windows PowerShell 5.1 diagnostic collector. It writes one local JSON report matching contract version `1.0.0`.

## Default run

From the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\collector\Collect-Diagnostics.ps1
```

The default output is:

```text
reports\windows-support-report-YYYYMMDD-HHmmss.json
```

The script prints the exact saved path when it finishes.

## Choose a filename

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\collector\Collect-Diagnostics.ps1 -OutputPath .\reports\first-report.json
```

Validate that report:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tests\Test-ReportContract.ps1 -ReportPath .\reports\first-report.json
```

## Collected checks

- Hostname, signed-in username, Windows edition/version/build, architecture, manufacturer/model, processor, and uptime.
- Point-in-time physical memory use.
- Fixed local disk capacity, free GB, and percentage free.
- IP-enabled network adapter name/status, full local IPv4, gateway, and configured DNS addresses.
- State and startup mode for `EventLog`, `Winmgmt`, `BFE`, `Dhcp`, `Dnscache`, and `NlaSvc`.
- At most 10 Critical/Error records from each of the Application and System logs during the previous 24 hours.
- Actual event messages truncated to 500 characters.
- Structured errors for checks that are denied or unavailable.

## Safety and privacy

The collector does not restart services, change the registry, delete files, repair the system, elevate itself, upload reports, use telemetry, or make public-IP requests.

Real reports contain unmasked local diagnostic information and are saved under the Git-ignored `reports/` directory. Review a report before choosing to share it, and never force-add it to Git.
