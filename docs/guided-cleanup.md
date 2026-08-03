# Guided Recycle Bin Cleanup

Guided cleanup is Milestone 5 of the Storage Insights extension. It turns an
explicitly reviewed candidate selection into a guarded, file-only Windows
Recycle Bin action. It is not an automatic cleaner and it never permanently
deletes a file as a fallback.

## User workflow

1. Generate a storage report for one or more folders you deliberately select.
2. Open the matching drive's storage page in the local dashboard.
3. Filter and sort the retained candidates.
4. Select individual eligible files or use `Select all visible` for the
   current page.
5. Choose **Review Recycle Bin action**.
6. Read every exact path, logical and allocated size, classification reason,
   confidence label, and removal-risk label.
7. Tick the final confirmation checkbox. Selections of 20 or more files or at
   least 10 GiB also require the displayed confirmation phrase.
8. Submit the one-time confirmation.
9. Review the outcome recorded for every requested file.

Opening a review page does not move files. Cleanup can start only from a POST
confirmation bound to a short-lived, single-use, in-memory preview token.

## Immediate safety checks

Immediately before each Recycle Bin call, the toolkit checks that the exact
reported path:

- is still present;
- is still a regular file rather than a directory;
- remains on the analysed drive and inside the exact approved scan root;
- is not a symbolic link, junction, or other detected reparse point;
- is not in a protected Windows or application location;
- still passes the current removal-risk policy rather than relying only on the
  policy stored in an older report;
- still has the reviewed byte size and modification time.

A file that fails a check is skipped. Other selected files continue and receive
their own results.

## Result states

| Result | Meaning |
| --- | --- |
| `recycled` | Windows accepted the file for the Recycle Bin. |
| `skipped_changed` | Its size or modification time changed after the report. |
| `skipped_protected_or_invalid` | It is no longer an eligible regular file in an approved location. |
| `missing` | It no longer exists at the reviewed path. |
| `failed` | Windows could not recycle it; no permanent-delete fallback ran. |

Each operation produces a schema-validated local result under
`cleanup-records/`. That directory is ignored by Git because records contain
real machine paths. The result contract is
[`schema/cleanup-record.schema.json`](../schema/cleanup-record.schema.json).

## Important limitations

- Recycle Bin behavior depends on the drive and Windows configuration.
- Windows controls retention and restoration; recovery is not guaranteed
  indefinitely.
- The toolkit does not delete directories, request elevation, empty the
  Recycle Bin, or offer permanent deletion.
- Classification evidence explains why a file deserves review. It does not
  prove that a file is disposable, unused, or corrupted.
- High-risk application data, installer/application files, databases,
  configuration files, and likely save data are review-only and cannot enter a
  cleanup preview.
- If Recycle Bin support is unavailable, the operation fails safely and leaves
  the file in place.
- The preview store is process-local. Restarting the dashboard invalidates
  outstanding confirmations.

## Automated verification

The cleanup tests use controlled temporary files and a test Recycle Bin
adapter, so the automated suite does not move personal files:

```powershell
python -m pytest tests\storage\test_cleanup.py `
  tests\dashboard\test_cleanup_tokens.py `
  tests\dashboard\test_storage_cleanup_routes.py -q
```

Run the complete regression suite with:

```powershell
python -m pytest -q
```
