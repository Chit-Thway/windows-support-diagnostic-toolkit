# File-Type Explorer Index Contract

## Purpose and version

The File-Type Explorer uses a separate local JSON index rather than changing
the existing diagnostic or `storage_analysis` contracts. Its initial contract
is:

- schema version: `1.0.0`;
- index type: `file_type_index`;
- schema: [`schema/file-type-index.schema.json`](../schema/file-type-index.schema.json);
- public sample: [`sample_data/sample-file-type-index.json`](../sample_data/sample-file-type-index.json).

The contract is produced by the explicitly invoked V2 whole-drive indexer. The
dashboard does not start it automatically. The ranked-tree interface consumes
the validated index read-only; matching-file selection and cleanup actions
remain later milestones.

The index is metadata-only, local, and scoped to one explicitly requested
Windows drive. A separate index is required for each drive.

## Create a local index

From an activated project virtual environment, explicitly select one drive and
run:

```powershell
python -m storage.file_type_indexer `
  --drive 'C:\' `
  --output 'c-file-type-index.json'
```

The validated UTF-8 file is created under the ignored `storage-reports/`
directory. Existing files are not replaced by default. Repeat the command with
a different drive and output name to create an independent index for that drive.
If `--output` is omitted, the stable per-drive cache name is
`file-type-index-c.json` (or the selected drive letter). A later run must use
`--refresh` to atomically replace that ignored cache after the new index passes
validation.

The indexer performs one metadata-only traversal and calculates unfiltered
logical folder totals plus totals for every preset extension. Changing a future
dashboard filter will use these stored aggregates and will not rescan the
drive. Progress reports elapsed time, current path, observed folders and files,
and indexed matches without inventing a completion percentage. `Ctrl+C`
requests cancellation and preserves a valid partial index.

Matching file detail is bounded to 100,000 largest logical files by default;
folder and extension aggregates remain truthful. Change the bound explicitly
with `--max-file-details`. Optional extensions can be added with repeated
`--custom-extension` values, but custom types remain review-only.

## Load the index in the dashboard

Select one or more per-drive indexes when starting the loopback-only dashboard:

```powershell
python -m dashboard `
  --report '.\reports\first-report.json' `
  --storage-report '.\storage-reports\c-drive-report.json' `
  --file-type-index '.\storage-reports\c-file-type-index.json'
```

Repeat `--file-type-index` for other drives. Command-line selection takes
priority over `FILE_TYPE_INDEX_PATH`; multiple environment paths use the
platform path separator. A custom diagnostic report never inherits the public
sample index silently.

The File-Type Explorer validates contract `1.0.0`, isolates each index by drive
letter, and caches it until the file metadata changes. The initial page renders
only the root; local GET requests fetch one child level when a folder is
expanded. Group and individual extension controls sum the stored recursive
aggregates in the browser, so filter changes do not read the filesystem or
start another scan. Scope selection accepts siblings and other non-overlapping
folders but rejects a parent and descendant together.

Matching-file review queries retained metadata only. Users can choose direct
folder or include-subfolders scope; filter by indexed extension, filename,
minimum logical size, and modification age; and sort by logical size,
modification time, natural filename, or path. Results are returned in bounded
pages so a large local index is not rendered as one browser document. Selection
is explicit and can cover individual rows, a shift-range on the current page,
or eligible rows visible on the current page only. Protected and review-only
rows cannot enter the selected-path summary.

Contract `1.0.0` guarantees unique file IDs and case-insensitive unique paths.
The dashboard also deduplicates normalized paths defensively, while the
non-overlapping scope rule prevents the same retained row from being returned
through both a parent and descendant scope. This contract does not contain a
stable physical file identity; physical hard-link deduplication therefore
cannot be claimed for V2 indexes.

## Top-level structure

```json
{
  "schema_version": "1.0.0",
  "index_type": "file_type_index",
  "index_id": "fictional-public-file-type-index",
  "generated_at_utc": "2026-08-20T07:01:00Z",
  "indexer": {},
  "drive": {},
  "scan": {},
  "scope": {},
  "extension_groups": [],
  "custom_extensions": [],
  "folders": [],
  "empty_summary": {},
  "file_detail_summary": {},
  "files": [],
  "inaccessible_paths": [],
  "scan_errors": [],
  "limitations": []
}
```

Unknown properties are rejected so accidental or sensitive additions cannot
silently become part of the public contract.

## Drive and explicit scope

`drive` records capacity observed at index time: drive letter, optional volume
label and filesystem, total/used/free bytes, and UTC observation time.

`scope.root_path` must belong to that drive. `recursive` and
`explicitly_requested` are always `true`. Opening the dashboard must never
create this index implicitly.

Capacity describes the volume. It is separate from observed folder totals
because permissions, cancellation, or changing files can make a recursive scan
partial.

## Scan status and coverage

`scan` records:

- start, completion, and duration;
- `complete`, `partial`, `cancelled`, or `failed` status;
- `exact`, `partial`, or `unavailable` aggregate coverage;
- files and directories examined;
- logical bytes observed;
- zero-byte files counted;
- files and logical bytes matching indexed extensions;
- retained and omitted structured issue counts.

A complete scan requires exact aggregate coverage and no collection errors. An
incomplete scan cannot claim exact coverage. Counters report observed work and
must not be converted into a fabricated completion percentage.

## Versioned extension presets

Version `1.0.0` declares the complete catalog below:

| Group | Extensions |
| --- | --- |
| Documents | `.pdf`, `.doc`, `.docx`, `.odt`, `.rtf`, `.txt`, `.ppt`, `.pptx`, `.xls`, `.xlsx` |
| Videos | `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.webm` |
| Audio | `.mp3`, `.wav`, `.flac`, `.m4a`, `.aac` |
| Images | `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.heic` |
| Archives and disk images | `.zip`, `.rar`, `.7z`, `.tar`, `.gz`, `.iso` |
| Installers | `.exe`, `.msi`, `.msix`, `.appx` |

Extensions are lowercase, begin with `.`, and cannot appear in more than one
group. `custom_extensions` cannot duplicate a preset. A long hash-like suffix
is valid when explicitly indexed, but remains a review filter—not a safety
classification.

## Folder hierarchy and dynamic totals

`folders` contains one hierarchy rooted at `scope.root_path`. Each folder has:

- a report-local ID and nullable parent ID;
- full path, name, and depth;
- direct and recursive file counts;
- direct and recursive logical bytes;
- direct and recursive zero-byte counts;
- per-extension direct and recursive counts and bytes;
- an access state and explanation.

The contract enforces direct parent-child paths and bottom-up accounting:

```text
recursive folder value = direct value + recursive values of immediate children
```

The same invariant applies independently to every extension. This lets the
future interface show a folder's total size initially, then replace the value
with Documents-only, `.pdf`-only, Videos-only, or another filtered total
without rescanning.

Folder access states are `normal`, `review_only`, `protected`, and
`unavailable`. Protected and application-managed totals can remain visible for
context while their files stay unselectable.

## Empty-item summary

Zero-byte files contribute no recoverable bytes. They are counted in
`scan.zero_byte_files` and `empty_summary.zero_byte_files`, but the schema
forbids them from normal `files` rows by requiring `size_bytes >= 1`.

Nested empty directory chains may be summarized in `empty_summary.trees`. Each
summary identifies only the highest useful folder, descendant empty-file and
directory counts, and a constant `0` recoverable bytes. Its path must match an
existing folder whose recursive logical size is zero.

This avoids thousands of distracting rows while keeping truthful metadata. An
Empty Items cleanup method remains outside V2 Method 1.

## Matching file details

`files` contains non-empty metadata for indexed extensions:

- report-local file and folder IDs;
- full path, name, and lowercase extension;
- logical byte size;
- optional UTC modification time;
- `selectable`, `review_only`, or `protected` state;
- a plain-English protection reason.

Every file must be directly inside its declared folder and its extension must
match its path. A file cannot be more selectable than its folder.

`file_detail_summary` separates aggregate truth from retained detail:

- `complete` means every matching observed file is present;
- `bounded` declares omitted rows and omitted logical bytes explicitly.

Retained plus omitted counts and bytes must equal the root extension totals.
This preserves correct ranked-folder values when a later implementation uses
pagination or bounded detail. Cleanup must never silently act on omitted paths.

## Folder-scope selection

The contract helper accepts multiple sibling or otherwise non-overlapping
folder scopes on the active drive. It rejects duplicate scopes, paths on
another drive, and a parent and descendant selected together. This prevents
duplicate results and double-counted selected bytes.

The dashboard preserves explicitly selected files when presentation filters or
pages change, and always displays the complete selected-path and byte summary.
Changing the folder scope or switching between direct and recursive depth
clears file selection because the review boundary changed.

## Partial scans and structured errors

An inaccessible path records its path, error type, and bounded message. A scan
error records code, scope, optional path, message, and whether collection could
continue. Retained issue counts must match actual rows, and omitted issue
details cannot be hidden by a `complete` status.

The partial fixture uses a fictional `F:` drive and demonstrates that
accessible results remain usable without pretending the entire drive was
classified.

## Privacy and safety

Real indexes can contain complete local paths and filenames. They must remain in
ignored local output locations and must never be committed, uploaded, or sent
as telemetry.

The index contains no file contents, passwords, tokens, browser history,
personal-document contents, public IP lookup, or automatic cleanup decision.
Modification age and extension organize human review; neither proves that a
file is unused or disposable.

Only fictional fixtures under `tests/storage/fixtures/` and `sample_data/` are
appropriate for the public repository.

## Contract test coverage

Automated tests cover schema validity; complete `C:` and partial `F:` indexes;
bounded detail; malformed JSON; preset and custom extensions; long hash-like
suffixes; hierarchy and extension accounting; total-to-filtered folder values;
summary-only zero-byte handling; protected paths; scan status; non-overlapping
scopes; and absence of known real user and repository values.
