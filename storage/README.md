# Read-only Storage Scanner

The Storage Insights scanner creates a local, metadata-only JSON report for
folders selected by the user. It is Milestone 2 of the storage extension.

The scanner does not delete, move, rename, repair, upload, or open the contents
of scanned files. It records paths, sizes, selected timestamps, evidence-based
candidate attributes, scan completeness, and structured errors.

## Safety boundaries

- One or more specific roots must be supplied with `--root`.
- A drive root such as `C:\` is rejected to prevent a silent whole-drive scan.
- All roots in one report must be distinct, non-overlapping folders on the
  same local Windows drive.
- Windows, Program Files, ProgramData, Recovery, Recycle Bin internals, and
  System Volume Information are protected from recursive scanning.
- Symbolic links, junctions, and other reparse points are recorded and skipped.
- Access-denied, disappearing, or unreadable paths do not stop the remaining
  scan.
- Candidate details are bounded while aggregate candidate totals continue.
- Last-access time is informational and is never classification evidence.
- Reports are schema-validated before export and existing reports are not
  overwritten.
- All generated reports stay under ignored `storage-reports/`.

The scanner is read-only with respect to selected roots. Creating a requested
JSON report is its only file-writing operation.

## Requirements

- Windows
- Python 3.10 or later
- Dependencies from `requirements.txt`

From the repository root, activate the project environment:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

## Create a report

Scan the current user's Downloads folder:

```powershell
python -m storage --root "$env:USERPROFILE\Downloads"
```

The report is written to a timestamped file similar to:

```text
storage-reports\storage-report-20260801-103000Z.json
```

Choose a filename inside the same ignored directory:

```powershell
python -m storage `
  --root "$env:USERPROFILE\Downloads" `
  --output "first-storage-report.json"
```

Scan multiple non-overlapping folders on the same drive:

```powershell
python -m storage `
  --root "$env:USERPROFILE\Downloads" `
  --root "$env:USERPROFILE\Videos"
```

The scanner never starts a scan by loading a dashboard page. It runs only when
the user invokes this command.

## View a report in the local dashboard

Start the dashboard with both the diagnostic report and the generated storage
analysis:

```powershell
python -m dashboard `
  --report '.\reports\first-report.json' `
  --storage-report '.\storage-reports\YOUR-STORAGE-REPORT.json'
```

Open <http://127.0.0.1:5000>, then select the matching disk card. The per-drive
page validates storage contract `1.0.0` and displays capacity, non-overlapping
categories, scan completeness, inaccessible paths, candidate totals, and
limitations. Its candidate explorer supports:

- `Match all` and `Match any` attribute filters;
- minimum size and age, extension, root, confidence, and eligibility filters;
- deterministic sorting by size, modification time, path, or confidence;
- individual selection and `Select all visible` for the current page;
- unique selected-byte totals that do not double-count overlapping attributes;
- copying a path, opening an eligible containing folder, and exporting a local
  review-only cleanup plan;
- opening an exact-path cleanup preview for explicitly selected eligible files.

The page does not start scans or perform repairs. Opening a preview does not
change files. A separate, explicit POST confirmation can move revalidated
regular files to the Windows Recycle Bin only. There is no permanent-delete
fallback and no directory action. Exported review plans contain real local
paths and are ignored by Git through `storage-cleanup-review*.json`; cleanup
results stay under ignored `cleanup-records/`.

The guided-cleanup workflow, checks, results, and limitations are documented in
[`docs/guided-cleanup.md`](../docs/guided-cleanup.md).

You can set the storage path for the current PowerShell session instead:

```powershell
$env:STORAGE_REPORT_PATH = (Resolve-Path '.\storage-reports\YOUR-STORAGE-REPORT.json').Path
python -m dashboard --report '.\reports\first-report.json'
Remove-Item Env:STORAGE_REPORT_PATH
```

## Classification settings

The defaults are:

- stale: modification time at least 730 days old;
- large: at least 1 GiB;
- likely incomplete: allowlisted partial-download extension at least 24 hours
  old;
- temporary: `.tmp` or `.temp` file at least 168 hours old;
- candidate details retained: 5,000.

Override them explicitly:

```powershell
python -m storage `
  --root "$env:USERPROFILE\Downloads" `
  --stale-days 730 `
  --large-bytes 1073741824 `
  --incomplete-min-hours 24 `
  --temporary-min-hours 168 `
  --max-candidates 5000
```

`Stale`, `Likely incomplete`, `Large`, `Empty`, and `Temporary` are review
attributes. None means that a file is unused, corrupted, or safe to remove.

## Development-storage insights

The scanner now recognises Python virtual environments from `pyvenv.cfg`
metadata, queries pip's supported cache-location command, and reads supported
Java runtime properties when Java is available. Environment and runtime files
are informational and never become automatic cleanup candidates. Locations
outside selected scan roots are displayed without recursively measuring them.

See
[`docs/development-storage-insights.md`](../docs/development-storage-insights.md)
for the discovery rules, consequences, and manual testing workflow.

Disable this discovery explicitly when it is not wanted:

```powershell
python -m storage `
  --root "$env:USERPROFILE\Downloads" `
  --no-development-insights
```

### Explicit custom cache roots

For a cache not supported by automatic discovery, classification still applies
only when its folder is explicitly supplied and already inside an approved scan
root:

```powershell
python -m storage `
  --root "C:\FictionalDevelopment" `
  --development-cache-root "C:\FictionalDevelopment\pip-cache"
```

The scanner does not infer cleanup commands for custom cache roots.

## Progress and cancellation

Progress reports observed file, directory, byte, and candidate counts. It does
not display a guessed completion percentage.

Press `Ctrl+C` once to request cancellation. The scanner stops between metadata
checks and writes the useful partial result with status `cancelled`.

Use `--quiet` to suppress progress output.

## Validate the report

Every CLI report is validated automatically against:

- [`schema/storage-report.schema.json`](../schema/storage-report.schema.json)
- non-overlapping category accounting;
- unique candidate IDs and bytes;
- retained and omitted candidate totals;
- candidate evidence and protection invariants.

The full contract is documented in
[`docs/storage-report-contract.md`](../docs/storage-report-contract.md).

## Run tests

```powershell
python -m pytest tests\storage -q
python -m pytest -q
```

The tests use controlled synthetic temporary files. Public fixtures and
screenshots must never contain personal paths or real storage reports.
