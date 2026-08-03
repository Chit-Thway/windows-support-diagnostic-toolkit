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

Status: Functional hardening implemented and validated locally on
`storage-extension-m7-hardening`; awaiting manual Windows testing. Portfolio
screenshots and the standalone case study are intentionally deferred until the
working release is manually approved.

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
- retain bounded candidates by deterministic review value rather than traversal
  order;
- keep application-managed data, installer/application files, databases,
  configuration files, and likely saves review-only.

## Safety principles

- Everything stays local and the dashboard remains bound to `127.0.0.1`.
- File contents, browser history, passwords, tokens, and personal-document
  contents are not collected.
- `Stale` and `Likely incomplete` are review evidence, not proof that deletion
  is safe.
- Protected Windows and application paths cannot be selected for cleanup.
- `Select all visible` never includes hidden or protected results.
- Every selected file is shown and revalidated before action.
- The first cleanup release handles files only, not directories.
- Confirmed files move to the Recycle Bin only.
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
