# Storage Insights and Guided Cleanup — Development Plan

This extension will turn low-storage findings into safe, practical next steps.
It will analyse local storage, explain why files may deserve review, provide
powerful filtering and selection, and eventually move explicitly confirmed
files to the Windows Recycle Bin.

The extension is a separate portfolio case study built on the completed Windows
Support Diagnostic Toolkit. Development happens on the `storage-extension`
integration branch. Each milestone uses a focused child branch, is tested, and
is merged back into `storage-extension`. The extension will not merge into
`main` until the entire project is complete and approved.

## Planned milestones

### Milestone 1 — Storage report contract

Status: Complete and merged into `storage-extension`.

Define and test a versioned JSON format for drive summaries, scan results,
candidate evidence, classification confidence, and partial-scan errors. Add
synthetic fixtures containing no real machine data.

Planned branch: `storage-extension-m1-contract`

### Milestone 2 — Read-only storage scanner

Status: Complete and merged into `storage-extension`.

Build a local metadata-only scanner with progress, cancellation, protected-path
handling, and safe recovery from inaccessible or changing files. It will not
delete or modify anything.

Planned branch: `storage-extension-m2-read-only-scanner`

### Milestone 3 — Per-drive dashboard

Status: Complete and merged into `storage-extension` together with Milestone 4.

Make disk cards interactive and add an accessible per-drive page with capacity
information, a non-overlapping storage chart, scan completeness, and cleanup
candidate summaries.

Planned branch: `storage-extension-m3-drive-dashboard`

### Milestone 4 — Candidate explorer

Status: Complete and merged into `storage-extension` together with Milestone 3.

Add sortable file results, evidence and confidence labels, `Match all` and
`Match any` filters, individual checkboxes, and a safe `Select all visible`
workflow. Read-only actions will include opening a containing folder and copying
a path.

Original planned branch: `storage-extension-m4-candidate-explorer`. This work
was intentionally combined with the Milestone 3 branch before either milestone
was committed or pushed.

### Milestone 5 — Guided Recycle Bin cleanup

Status: Complete and merged into `storage-extension` together with Milestone 6.

After the read-only results are trusted, add an exact-path review screen,
explicit confirmation, immediate file revalidation, protected-path enforcement,
and per-file results. Files will move only to the Windows Recycle Bin; failure
will never fall back to permanent deletion.

Planned branch: `storage-extension-m5-guided-cleanup`

### Milestone 6 — Development-storage insights

Status: Complete and merged into `storage-extension` together with Milestone 5.

Explain storage used by supported Python environments, pip caches, and selected
Java locations. Runtimes remain informational, while supported cache cleanup is
presented with clear consequences.

Original planned branch: `storage-extension-m6-development-insights`. This
milestone was intentionally combined with the uncommitted Milestone 5 branch
before the user performs both manual tests and pushes the work.

### Milestone 7 — Hardening and portfolio release

Status: Functional hardening implemented, validated, and published as checkpoint
`storage-extension-m7-hardening`. Portfolio screenshots and the standalone case
study remain deferred until the folder-analysis amendment is manually approved.

Test large and partial scans, changing files, links, access denial, locked
files, cancellation, and cleanup failure paths. Complete public documentation,
synthetic screenshots, limitations, and a standalone storage-extension case
study.

Current branch: `storage-extension-m7-hardening`. The portfolio work will use a
separate follow-up branch after manual testing.

Final usability amendments on this branch:

- permit an explicitly supplied drive root while keeping protected locations
  and Recycle Bin contents outside cleanup eligibility;
- load multiple per-drive storage reports in one dashboard session and select
  the matching report automatically from each disk card.
- use Windows allocated-size and stable file-identity metadata for physical
  chart accounting;
- retain bounded candidates by physical size first, with deterministic safety
  and evidence tie-breakers rather than traversal order;
- keep application-managed data, installer/application files, databases,
  configuration files, and likely saves review-only.

### Milestone 8 — Folder analysis amendment

Status: Unmerged technical checkpoint on the child branch
`storage-extension-m8-folder-analysis`, created from the published Milestone 7
checkpoint. Its folder aggregation, hierarchy, and safety work may be reused,
but the general stale-file cleanup direction is being replaced by the planned
File-Type Explorer milestones below.

Add aggregated folder-tree candidates to the existing per-drive report without
removing the individual-file workflow. Provide a Files/Folders explorer switch,
collapse redundant empty or equivalent stale hierarchies, and classify a folder
as stale only when all observed descendant files are old.

Folder cleanup remains opt-in and Recycle Bin only. Application, save-data,
configuration, runtime, AppData, protected, and unavailable trees are
review-only. Every eligible selected tree is fully revalidated before action,
folder operations require a typed confirmation, and overlapping parent/child
selections are rejected.

Current branch: `storage-extension-m8-folder-analysis`

### Milestone 9 / V2 Milestone 1 — File-Type Explorer contract and fixtures

Status: Complete on `storage-extension-v2`.

Define the local index/report contract for a complete per-drive folder tree,
total folder sizes, per-extension totals, preset extension groups, matching
file metadata, scan coverage, and structured errors. Preserve truthful totals
when detailed rows are paginated or segmented. Add synthetic fixtures for
multiple drives, protected paths, inaccessible folders, long extensions,
custom extensions, overlapping scopes, and empty-item summaries.

Individual zero-byte files will not become normal explorer records. The scan
will count them in a summary and skip unnecessary enrichment. Nested empty
folder chains will collapse to the highest useful empty tree and will never be
presented as meaningful recoverable space.

Current branch: `storage-extension-v2`

### Milestone 10 / V2 Milestone 2 — Whole-drive extension indexer

Status: Complete and merged into `storage-extension-v2`.

Build one explicitly started scan per selected drive. Enumerate the complete
approved drive scope once, aggregate every folder's total logical size, and
aggregate matching counts and sizes for all supported extension presets. Keep
the initial pass metadata-only, cancellable, locally cached, and honest about
elapsed time, current path, observed counts, inaccessible paths, and coverage.

Avoid expensive candidate classification, allocated-size lookups, fingerprints,
and risk enrichment for unrelated files. Detect zero-byte files from basic
metadata, count them, and do not retain individual rows. Changing between
indexed preset groups after the scan must not start another drive scan.

Completed branch: `storage-extension-v2-m2-indexer`

### Milestone 11 / V2 Milestone 3 — Cleanup method and ranked folder tree

Status: Complete and merged into `storage-extension-v2`.

Add a **Cleanup methods** section to each disk page and a dedicated
**Method 1 — File-Type Explorer** page. Provide grouped extension checkboxes
for Documents, Videos, Audio, Images, Archives and disk images, and Installers.
Each group starts with its extensions enabled and exposes per-extension settings
plus a clearly scoped custom-extension option.

Render an accessible File Explorer-style hierarchy. The right-hand value shows
each folder's total size when no type filter is active, then changes to the
matching aggregate size when a group or extension is selected. Support
expand/collapse, breadcrumbs, analyzed/partial/protected states, and selection
of multiple non-overlapping folder scopes on the active drive.

Planned branch: `storage-extension-v2-m3-ranked-folder-tree`

### Milestone 12 / V2 Milestone 4 — Matching-file review and selection

Status: Implemented and validated locally on
`storage-extension-v2-m4-file-review`; not yet committed, pushed, or merged.

Show the matching files for the selected folder scopes with direct-folder and
include-subfolders modes. Add largest/smallest, oldest/newest, natural filename,
path, minimum-size, minimum-age, and filename filters. Support individual
checkboxes, shift-range selection, visible-only selection, exact selected-byte
totals, and deduplication across scopes.

Multiple sibling folders are allowed. Parent and child scopes cannot be active
together because they would duplicate results. Application-managed, AppData,
Program Files, Windows, protected, unavailable, and ambiguous save data remain
clearly labelled and excluded from convenient bulk selection.

Planned branch: `storage-extension-v2-m4-file-review`

### Milestone 13 / V2 Milestone 5 — Recycle Bin integration and final hardening

Status: Planned; implementation has not started.

Connect eligible File-Type Explorer selections to the existing exact-path
preview, one-time confirmation, immediate revalidation, and Recycle Bin-only
workflow. Never infer usefulness from age or extension alone. Test full-drive
scale, cancellation, stale indexes, changed files, inaccessible paths, long
extensions, overlapping scopes, protected locations, malformed indexes,
cleanup failure, and multi-drive isolation.

Complete Windows manual testing and public setup, safety, limitations, and
performance documentation. Portfolio screenshots and the standalone LinkedIn
case study remain a separate presentation milestone after functional approval.

Planned branch: `storage-extension-v2-m5-hardening`

## Safety principles

- Everything stays local and the dashboard remains bound to `127.0.0.1`.
- File contents, browser history, passwords, tokens, and personal-document
  contents are not collected.
- `Stale` and `Likely incomplete` are review evidence, not proof that deletion
  is safe.
- Age and file extension organise human review; neither proves that a file is
  unused or disposable.
- Protected Windows and application paths cannot be selected for cleanup.
- `Select all visible` never includes hidden or protected results.
- Every selected file or folder tree is shown and revalidated before action.
- Folder evidence is aggregated separately and is never added into the
  non-overlapping drive chart.
- Confirmed eligible items move to the Recycle Bin only.
- Automatic and permanent deletion are out of scope.
- Real storage reports and cleanup records remain ignored by Git.

## Completion workflow

For every milestone:

1. Create its child branch from the latest `storage-extension`.
2. Implement only that milestone.
3. Run its tests and all relevant regression tests.
4. Use commits that describe the milestone and the working outcome.
5. Review the changed files and generated-data exclusions.
6. Merge the completed child branch into `storage-extension`.
7. Update this plan's progress.

Only after all approved milestones pass final validation will
`storage-extension` be proposed for merge into `main`.
