# Development Storage Insights

Development Storage Insights is Milestone 6 of the storage extension. It adds
an informational overview of supported Python and Java locations without
turning runtimes or environments into cleanup candidates.

Discovery runs locally as part of an explicitly started storage scan. It does
not upload data, read project or configuration-file contents, install tools, or
scan additional directory trees outside the roots selected by the user.

## Supported discovery

### Python virtual environments

The scanner recognises a directory containing a regular, non-reparse-point
`pyvenv.cfg` marker. It checks only the marker's filesystem metadata and does
not read the file contents.

Files observed inside that environment are included in the Development tools
and caches chart category, but they are not cleanup candidates. An environment
is never described as abandoned merely because its files are old. The current
scanner environment is labelled active when it can be matched reliably.

### pip cache

The cache path is obtained through pip's supported command:

```powershell
python -m pip cache dir
```

If that path is inside a selected scan root, its observed files and bytes are
reported as development storage and excluded from individual cleanup
candidates. If it is outside the selected roots, the path is displayed but its
size remains `Not measured`.

The dashboard may show pip's supported manual cleanup command:

```powershell
python -m pip cache purge
```

The toolkit never runs that command automatically. Purging the cache removes
reusable downloads, so later package installations may need to download them
again.

### Java runtime or SDK

When a Java launcher is available on `PATH`, the scanner queries its supported
system-properties output for `java.home` and `java.version`. A valid
`JAVA_HOME` directory is used as a fallback.

Java installations are informational only. Typical installations are outside
the selected roots and therefore are not measured recursively. Removing a
runtime or SDK can break applications, builds, and projects. The toolkit does
not assume a universal Java cache because modern Java distributions do not
expose one reliable cross-vendor cache location.

## Measurement boundaries

- Only bytes already encountered inside user-approved scan roots are counted.
- A discovered location never causes a new recursive scan.
- Outside-scope locations have null byte and file counts rather than guessed
  values.
- Partial or cancelled scans label observed development totals as partial.
- Development locations are never automatic cleanup candidates.
- Custom cache roots supplied with `--development-cache-root` retain their
  explicit, user-selected classification behavior.

## Run a development-aware scan

Discovery is enabled by default:

```powershell
python -m storage --root "$env:USERPROFILE\Projects"
```

To measure the supported pip cache as part of the same report, explicitly add
the path returned by pip when it is on the same drive and does not overlap the
first root:

```powershell
$pipCache = python -m pip cache dir
python -m storage `
  --root "$env:USERPROFILE\Projects" `
  --root "$pipCache"
```

Disable development discovery when it is not wanted:

```powershell
python -m storage `
  --root "$env:USERPROFILE\Downloads" `
  --no-development-insights
```

The resulting local report remains under ignored `storage-reports/` and can be
opened with the existing dashboard command.

