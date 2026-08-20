# Guided Recycle Bin Cleanup

Guided cleanup began in Milestone 5 of the Storage Insights extension. The
Milestone 8 folder-analysis amendment extends the same guarded workflow to
conservatively eligible folder trees. It is not an automatic cleaner and it
never permanently deletes an item as a fallback.

## User workflow

1. Generate a storage report for one or more folders you deliberately select.
2. Open the matching drive's storage page in the local dashboard.
3. Filter and sort the retained candidates.
4. Choose the Files or Folders view, then select eligible items individually or
   use `Select all visible` for the current page.
5. Choose **Review Recycle Bin action**.
6. Read every exact path, logical and allocated size, classification reason,
   confidence label, and removal-risk label.
7. Tick the final confirmation checkbox. Every folder operation, every
   selection of 20 or more files, and every selection of at least 10 GiB also
   requires the displayed confirmation phrase.
8. Submit the one-time confirmation.
9. Review the outcome recorded for every requested item.

Opening a review page does not move files. Cleanup can start only from a POST
confirmation bound to a short-lived, single-use, in-memory preview token.

## Immediate safety checks

Immediately before each Recycle Bin call, the toolkit checks that the exact
reported path:

- is still present;
- is still the expected item type;
- remains on the analysed drive and inside the exact approved scan root;
- is not a symbolic link, junction, or other detected reparse point;
- is not in a protected Windows or application location;
- still passes the current removal-risk policy rather than relying only on the
  policy stored in an older report;
- still has the reviewed byte size and modification time.

For a selected folder, the toolkit also walks the entire tree again and checks
the descendant file count, subfolder count, logical bytes, allocated bytes, and
newest modification time. It also compares a SHA-256 fingerprint made only from
paths and filesystem metadata; file contents are never opened or hashed. Any
inaccessible entry, reparse point, changed item, or unexpected tree value causes
that folder to be skipped. A cleanup request cannot mix file and folder
candidates, and cannot contain both a parent and its descendant.

An item that fails a check is skipped. Other selected items continue and
receive their own results.

## Result states

| Result | Meaning |
| --- | --- |
| `recycled` | Windows accepted the file or folder for the Recycle Bin. |
| `skipped_changed` | Its size or modification time changed after the report. |
| `skipped_protected_or_invalid` | It is no longer an eligible item in an approved location. |
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
- The toolkit does not request elevation, empty the Recycle Bin, or offer
  permanent deletion.
- Classification evidence explains why an item deserves review. It does not
  prove that a file or folder is disposable, unused, or corrupted.
- High-risk application data, installer/application files, databases,
  configuration files, and likely save data are review-only and cannot enter a
  cleanup preview.
- High-risk folder trees, including likely save data, application-managed
  AppData, application/configuration directories, runtime folders, and folders
  flagged only because of aggregate size are review-only.
- If Recycle Bin support is unavailable, the operation fails safely and leaves
  the item in place.
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
