# Storage Analysis Report Contract

Version: `1.0.0`

The storage extension uses a separate JSON contract for local, metadata-only
storage analysis. It does not extend or replace diagnostic report schema
`1.0.0`. A consumer must check both `report_type` and `schema_version` before
reading a report.

The formal JSON Schema is
[`schema/storage-report.schema.json`](../schema/storage-report.schema.json).

Milestone 1 defines the data boundary only. It does not scan a filesystem,
change a file, launch cleanup, or add a dashboard route.

## Local-data boundary

Real storage reports can contain complete local paths, filenames, sizes, and
timestamps. They remain on the user's computer under ignored
`storage-reports/`. They must never be committed or used in public screenshots.

Only deliberately fictional reports under `sample_data/` and
`tests/storage/fixtures/` are public and trackable.

The storage report does not contain:

- file contents;
- document text;
- passwords or authentication tokens;
- browser history;
- content hashes;
- public-IP information;
- telemetry or upload destinations.

## Top-level structure

```json
{
  "schema_version": "1.0.0",
  "report_type": "storage_analysis",
  "generated_at_utc": "2026-07-25T11:30:08Z",
  "scanner": {},
  "scan": {},
  "drive": {},
  "scan_scope": {},
  "accounting": {},
  "candidate_summary": {},
  "candidates": [],
  "folder_candidate_summary": {},
  "folder_candidates": [],
  "inaccessible_paths": [],
  "scan_errors": [],
  "development_insights": {},
  "limitations": []
}
```

| Property | Purpose |
| --- | --- |
| `schema_version` | Version of this storage-specific contract. |
| `report_type` | Distinguishes storage analysis from a diagnostic report. |
| `generated_at_utc` | UTC time at which report generation completed. |
| `scanner` | Scanner identity, version, platform, and metadata-only mode. |
| `scan` | Timing, status, coverage, observed totals, and retained-result limits. |
| `drive` | Drive capacity values observed at scan time. |
| `scan_scope` | User-approved roots and classification settings used for the scan. |
| `accounting` | Six non-overlapping drive chart categories. |
| `candidate_summary` | Unique file-candidate totals and overlapping attribute summaries. |
| `candidates` | Bounded file-level metadata, evidence, confidence, and protection. |
| `folder_candidate_summary` | Optional overlapping hierarchy totals for folder-tree candidates. |
| `folder_candidates` | Optional bounded folder-tree metadata, evidence, confidence, and protection. |
| `inaccessible_paths` | Paths that could not be inspected safely. |
| `scan_errors` | Structured, recoverable or terminal scan errors. |
| `development_insights` | Optional supported Python, pip-cache, and Java locations with explicit measurement boundaries. |
| `limitations` | Plain-language qualifications a consumer must display. |

## Scanner and scan state

The initial contract reserves this scanner identity:

```json
{
  "name": "Storage Insights Scanner",
  "version": "0.1.0",
  "platform": "Windows",
  "mode": "metadata_only"
}
```

`scan.status` is one of:

- `complete` — every approved root completed without an inaccessible-path or
  scan-error record;
- `partial` — useful results exist, but at least one area could not be
  inspected;
- `cancelled` — the user stopped the scan;
- `failed` — no useful scan could be completed.

Coverage is stated separately:

- `detail_coverage` explains whether candidate rows are complete, bounded,
  partial, or unavailable;
- `aggregate_coverage` explains whether totals are exact, estimated, partial,
  or unavailable.

A complete scan can still have `bounded` candidate details or `estimated`
drive categories. Completion must never be used to imply exhaustive
classification of the entire drive.

## Drive capacity

```json
{
  "drive_letter": "C:",
  "volume_label": "Fictional Sample",
  "filesystem": "NTFS",
  "total_bytes": 500000000000,
  "used_bytes": 460000000000,
  "free_bytes": 40000000000,
  "percent_free": 8,
  "observed_at_utc": "2026-07-25T11:30:00Z"
}
```

Contract-level accounting tests enforce:

```text
total_bytes = used_bytes + free_bytes
```

`percent_free` is derived from the byte values and retained for clear display.
No cleanup status is assigned by this contract.

## Scan scope and options

Every root records:

- the requested and canonical local path;
- whether subdirectories were included;
- root status;
- files, directories, logical bytes, and allocated bytes examined;
- error count.

The options snapshot makes later classifications reproducible:

- stale age in days;
- large-file byte threshold;
- minimum age for incomplete-download evidence;
- minimum age for temporary-file evidence;
- maximum detailed file candidates retained;
- maximum detailed folder candidates retained;
- an explicit `false` value for using last-access time as classification
  evidence;
- whether supported development-storage discovery was enabled.

Last-access time may be displayed when available, but Windows can defer or
disable its updates. It must not be used as the sole cleanup signal.

## Non-overlapping storage categories

The `accounting.categories` object always contains exactly:

1. `free_space`
2. `protected_system`
3. `installed_applications`
4. `user_content`
5. `development_tools_and_caches`
6. `other_or_unreadable`

Each category has:

- `bytes`;
- `measurement`: `exact`, `estimated`, or `unavailable`;
- a plain-language `explanation`.

The category values form the drive chart and cannot overlap. Validation tests
enforce:

```text
free_space bytes = drive free_bytes
all other category bytes = drive used_bytes
all six category bytes = drive total_bytes
```

Files with cleanup attributes are not separate chart slices.

Physical category accounting uses Windows allocated-size metadata rather than
ordinary logical file length. A stable volume/file identity prevents hard links
from being counted repeatedly. Accessible protected Windows and application
files contribute to protected chart categories but never to cleanup candidates.
The remainder between Windows-reported used bytes and observed allocated bytes
is assigned to `other_or_unreadable`; it can include inaccessible storage and
filesystem overhead.

## Development-storage insights

New Milestone 6 reports include the optional `development_insights` object.
Keeping it optional preserves compatibility with earlier schema `1.0.0`
fixtures and reports.

Each detected location records:

- ecosystem and kind;
- exact local path and documented discovery source;
- optional version and active-state evidence;
- whether it is inside a user-approved scan root;
- observed files and bytes only when it is inside that scope;
- measurement coverage;
- cleanup policy and an optional supported manual command;
- a consequence statement;
- the constant `automatic_cleanup_candidate: false`.

Supported sources are `pyvenv.cfg` metadata, `python -m pip cache dir`, Java
system-properties output, and a valid `JAVA_HOME` fallback. A discovery result
never starts a new recursive scan. Out-of-scope locations must retain null
counts with `not_measured` and `not_applicable` labels.

Contract validation rejects a report that also presents a file inside an
informational development location as a cleanup candidate. See
[`development-storage-insights.md`](development-storage-insights.md) for the
product behavior and limitations.

## Candidate records

A candidate is a regular file or an unavailable/protected file observation
that deserves review. It is not a deletion recommendation.

Each retained record contains:

- a report-local `candidate_id`;
- full local path, scan root, name, and extension (up to the Windows filename
  component limit, including long hash-like suffixes);
- logical size, optional allocated size, and selected timestamps, using `null`
  when unavailable;
- last-access reliability;
- one non-overlapping storage category;
- one or more candidate attributes;
- evidence for every attribute;
- classification confidence;
- removal risk: `low`, `medium`, `high`, or `protected`;
- selection eligibility and protection reason;
- regular-file and reparse-point state.

Initial attributes are:

- `stale`
- `likely_incomplete`
- `large`
- `empty`
- `temporary`
- `development_cache`
- `protected`
- `unavailable`

`stale` means the last-modified time crossed the configured threshold. It does
not mean unused. `likely_incomplete` describes conservative metadata evidence,
not proven corruption. `large` describes potential impact, not disposability.

Confidence expresses the strength of classification evidence. It never
expresses the probability that deletion is safe.

Eligibility and removal risk are separate. `review_only` candidates are regular
files whose metadata may still be useful, but the risk policy prevents Recycle
Bin selection. Installer/application extensions, application-managed AppData,
databases, configuration files, and likely save data are high-risk review-only
records. Cleanup immediately reapplies the current risk policy so an older
report cannot bypass new protections.

## Folder candidate records

Milestone 8 reports may include `folder_candidate_summary` and
`folder_candidates`. They are optional so earlier valid `1.0.0` reports remain
readable; consumers present folder analysis as unavailable until the report is
regenerated.

Folder candidates are aggregated during the same metadata traversal as file
candidates. No second content scan occurs. A retained folder record includes:

- `item_type: "folder"`, full path, scan root, name, and a report-local ID;
- total descendant logical and allocated bytes;
- descendant file and subfolder counts;
- oldest and newest descendant modification times;
- whether any descendant metadata was unavailable;
- a SHA-256 fingerprint of names and metadata only, never file contents;
- one or more evidence attributes, confidence, removal risk, and eligibility;
- directory and reparse-point state.

A folder is `stale` only when every observed descendant file has crossed the
configured threshold. A folder tree containing any newer file is not stale.
Empty or equivalent stale chains are collapsed to the highest useful candidate
so a hierarchy such as `parent/child/empty` does not create redundant cleanup
rows. The approved scan root itself is never a candidate.

Folder sizes deliberately use path-based hierarchy aggregation. A parent's
size includes its descendants, so parent and child rows overlap and must never
be added together. Hard-linked files can also make a folder's estimated
recoverable size higher than the physical allocation ultimately reclaimed.
These values do not alter the non-overlapping drive chart.

Folder cleanup eligibility is intentionally narrower than file eligibility.
Application-managed AppData, likely game or application saves,
application/configuration/runtime trees, protected or unavailable trees, and
folders identified only because they are large are high-risk `review_only`
records. If a tree is both high risk and partially unreadable, `unavailable`
takes precedence and the tree cannot be selected. Folder actions require a
typed confirmation and whole-tree
revalidation. The same operation cannot contain both a parent and its
descendant.

## Overlapping attributes and unique-byte accounting

Candidate attributes intentionally overlap. A 6 GB file can be stale, large,
and likely incomplete, but it is still one 6 GB file.

`candidate_summary.attributes` records count and byte totals within each
attribute. Those attribute totals must never be added together to estimate
recovery.

The authoritative values are:

- `total_unique_candidates`;
- `total_unique_candidate_bytes`;
- `retained_candidates`;
- `retained_unique_candidate_bytes`;
- `total_unique_candidate_allocated_bytes` when allocation metadata is present;
- `retained_unique_candidate_allocated_bytes` when allocation metadata is present;
- `omitted_candidates`.

The accounting method is fixed to `unique_candidate_id`. Tests reject duplicate
retained IDs, inflated retained-byte totals, and mismatches between retained
candidate rows and the summary.

When rows are bounded, aggregate totals include omitted candidates while the
report clearly states how many details were omitted. Retained rows are selected
by physical allocated size first. Eligibility, lower removal risk, stronger
evidence, and confidence provide deterministic tie-breakers; filesystem
traversal order does not decide which rows survive the limit. Dashboard filters
then apply to this retained set before the selected display sort is applied.

`folder_candidate_summary` uses the accounting method
`overlapping_hierarchy`. Its per-attribute counts and bytes describe review
evidence only. They are not unique recovery totals and must never be summed.
The summary separately reports retained and omitted folder details and the
largest retained tree.

## Partial scans and structured errors

An inaccessible path records:

- path and approved scan root;
- error type;
- safe message;
- UTC occurrence time.

A scan error records:

- stable error code;
- scope;
- optional path;
- safe message;
- whether collection could continue;
- UTC occurrence time.

A `partial` report must contain at least one inaccessible-path or scan-error
record. An unavailable candidate has disabled cleanup eligibility and does not
contribute unknown bytes to recoverable-space totals.

## Synthetic fixtures

Public fixtures cover:

- a healthy drive with ample free space;
- a low-space drive with bounded candidate details;
- stale, likely incomplete, large, empty, temporary, and development-cache
  attributes;
- candidates with multiple overlapping attributes;
- inaccessible metadata and a partial scan;
- folder candidates with aggregated descendant metadata and collapsed empty
  hierarchy;
- malformed JSON.

The default public example is
[`sample_data/sample-storage-report.json`](../sample_data/sample-storage-report.json).
All paths, names, timestamps, capacity values, and candidate evidence are
fictional.

## Compatibility rule

Consumers support only storage schema `1.0.0` until a later version is
explicitly implemented. Unsupported versions must fail with a clear error.
Additional fields are rejected so accidental contract changes are detected by
tests.
