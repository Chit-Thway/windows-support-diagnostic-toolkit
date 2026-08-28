# Read-only Storage Scanner

The Storage Insights scanner creates a local, metadata-only JSON report for
folders or an explicit drive root selected by the user. It is Milestone 2 of
the storage extension.

The scanner does not delete, move, rename, repair, upload, or open the contents
of scanned files. It records paths, sizes, selected timestamps, evidence-based
candidate attributes, scan completeness, and structured errors.

## Safety boundaries

- One or more roots must be supplied explicitly with `--root`.
- A drive root such as `C:\` is supported only when the user supplies it in the
  scanner command; the dashboard never starts a whole-drive scan silently.
- All roots in one report must be distinct, non-overlapping folders on the
  same local Windows drive.
- During an explicit drive-root scan, accessible Windows, Program Files,
  ProgramData, Recovery, and Recycle Bin metadata contributes only to protected
  chart categories; these files never become cleanup candidates.
- Symbolic links, junctions, and other reparse points are recorded and skipped.
- Access-denied, disappearing, or unreadable paths do not stop the remaining
  scan.
- Candidate details are bounded while aggregate candidate totals continue; the
  retained set keeps the largest physical candidates first, then uses safety
  and evidence as deterministic tie-breakers rather than traversal order.
- Last-access time is informational and is never classification evidence.
- Reports are schema-validated before export and existing reports are not
  overwritten.
- All generated reports stay under ignored `storage-reports/`.

The scanner is read-only with respect to selected roots. Creating a requested
JSON report is its only file-writing operation.

## V2 File-Type Explorer index

The separate V2 indexer performs one explicitly requested, metadata-only pass
over an actual drive root. It records complete observed folder totals and all
preset extension totals so File-Type Explorer can change filters without
rescanning.

```powershell
python -m storage.file_type_indexer `
  --drive 'C:\' `
  --output 'c-file-type-index.json'
```

Repeat the command for another drive:

```powershell
python -m storage.file_type_indexer `
  --drive 'F:\' `
  --output 'f-file-type-index.json'
```

Generated indexes are validated and written only under ignored
`storage-reports/`. Without `--output`, each drive uses a stable name such as
`file-type-index-c.json`; an existing index is not replaced unless the user
explicitly adds `--refresh`. Refresh uses an atomic replacement only after the
new index validates. The scan shows elapsed time and observed counters but no
misleading percentage. Press `Ctrl+C` once to request a valid partial result.
Zero-byte files are counted without becoming normal file rows, and matching
detail defaults to the 100,000 largest logical files while exact observed
folder aggregates remain available.

Load one or more generated indexes when starting the dashboard:

```powershell
python -m dashboard `
  --report '.\reports\first-report.json' `
  --storage-report '.\storage-reports\c-drive-report.json' `
  --file-type-index '.\storage-reports\c-file-type-index.json'
```

Repeat `--file-type-index` for additional drives. Open the matching disk card,
then choose **Cleanup methods → File-Type Explorer**. Group or exact-extension
filters update stored folder aggregates without another scan. The tree is
read-only and selects non-overlapping folder review scopes. The matching-file
panel queries only retained index metadata and supports direct-folder or
recursive scope, filename/size/age filters, deterministic sorting, pagination,
individual and shift-range selection, and `Select all visible`. Bounded indexes
disclose omitted file rows rather than pretending those paths are selectable.
Selecting a file changes nothing. **Review Recycle Bin action** creates a
separate exact-path preview. Only after another explicit confirmation does the
toolkit recheck the file's drive, approved scope, type, size, modification time,
reparse-point state, protected-location policy, and current removal-risk policy.
Eligible unchanged files are sent only to the Windows Recycle Bin. The toolkit
never permanently deletes a file as a fallback.

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

Scan every accessible, non-protected folder on a drive:

```powershell
python -m storage --root 'C:\' --output 'c-drive-report.json'
python -m storage --root 'F:\' --output 'f-drive-report.json'
```

A drive-root scan can take considerably longer and will commonly be labelled
partial because Windows denies access to some locations. Windows, Program
Files, ProgramData, Recovery, Recycle Bin internals, System Volume Information,
reparse points, and unreadable paths remain outside cleanup eligibility.

## View a report in the local dashboard

Start the dashboard with both the diagnostic report and the generated storage
analysis:

```powershell
python -m dashboard `
  --report '.\reports\first-report.json' `
  --storage-report '.\storage-reports\YOUR-STORAGE-REPORT.json'
```

Load independent analyses for several drives by repeating `--storage-report`:

```powershell
python -m dashboard `
  --report '.\reports\first-report.json' `
  --storage-report '.\storage-reports\c-drive-report.json' `
  --storage-report '.\storage-reports\f-drive-report.json'
```

Each disk card automatically uses the selected report whose drive letter
matches that disk. There is no need to restart the dashboard to switch between
C: and F:. Each newly generated report contains both file and folder candidates;
a second folder-specific scan is not required.

Open <http://127.0.0.1:5000>, then select the matching disk card. The per-drive
page validates storage contract `1.0.0` and displays capacity, non-overlapping
categories, scan completeness, inaccessible paths, candidate totals, and
limitations. Use the **Files / Folders** switch to change between individual
files and aggregated folder trees from the same report. Its candidate explorer
supports:

- `Match all` and `Match any` attribute filters;
- minimum size and age, root, confidence, removal-risk, and eligibility filters,
  plus file extension in the Files view;
- deterministic sorting by size, modification time, path, or confidence;
- individual selection and `Select all visible` for the current page;
- unique selected-byte totals that do not double-count overlapping attributes;
- copying a path, opening an eligible location, and exporting a local
  review-only cleanup plan;
- opening an exact-path cleanup preview for explicitly selected eligible files
  or folder trees.

The page does not start scans or perform repairs. Opening a preview does not
change files. A separate, explicit POST confirmation can move revalidated files
or eligible folder trees to the Windows Recycle Bin only. There is no
permanent-delete fallback. A folder operation rechecks the entire descendant
tree, rejects reparse points or changed metadata, and cannot include overlapping
parent and child selections. A metadata-only tree fingerprint catches renamed
or same-sized replacement entries without opening or hashing file contents.
Exported review plans contain real local paths and are ignored by Git through
`storage-cleanup-review*.json`; cleanup results stay under ignored
`cleanup-records/`.

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
- file candidate details retained: 5,000;
- folder candidate details retained: 2,000.

Override them explicitly:

```powershell
python -m storage `
  --root "$env:USERPROFILE\Downloads" `
  --stale-days 730 `
  --large-bytes 1073741824 `
  --incomplete-min-hours 24 `
  --temporary-min-hours 168 `
  --max-candidates 5000 `
  --max-folder-candidates 2000
```

`Stale`, `Likely incomplete`, `Large`, `Empty`, and `Temporary` are review
attributes. None means that a file or folder is unused, corrupted, or safe to
remove. A folder is stale only when every observed descendant file is stale.
Nested empty or equivalent stale chains collapse to the highest useful folder
candidate instead of producing a row for every level.

The scanner separately labels removal risk. Application-managed AppData,
installer/application files, databases, configuration files, likely game or
application saves, runtime folders, and large-only folder trees are `High` risk
and `Review only`; the dashboard can show and open them, but it cannot select
them for recycling. Candidate confidence describes classification evidence,
not removal safety.

Drive-chart categories use Windows allocated-size metadata and a stable
volume/file identity. Logical file size remains available for evidence, but it
does not drive the physical-space donut chart. Inaccessible bytes and filesystem
overhead remain in `Other or unreadable`.

Folder candidate sizes are hierarchy aggregates and therefore overlap: a
parent includes its descendants. They are never summed into the physical-space
chart or presented as a unique recovery total. Hard-linked data can also make a
folder's path-based recovery estimate higher than the physical bytes ultimately
reclaimed.

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
