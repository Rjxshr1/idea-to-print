# Preserve a checked slice when opening Studio

`scripts/make_slice_only.py` prepares a sliced-file container for opening existing Bambu toolpaths without re-slicing editable geometry. It supports Bambu Studio 02.07.01.62 single-plate CLI packages with the layout specified below.

Prefer the official **export plate sliced file** command when available. The helper removes editable geometry while preserving G-code, settings and warnings.

From the repository root:

```sh
python skills/3d-print-workflow/scripts/make_slice_only.py --source jobs/example/outputs/job-ready.gcode.3mf --output jobs/example/outputs/job-dispatch.gcode.3mf --report jobs/example/work/slice-only.json
```

Supported format: producer `BambuStudio-02.07.01.62`, exactly `plate_1`, core/production XML namespaces, external `object_N.model` geometry and no assembly items. Python 3.11+ standard library is sufficient. Output/report parent directories must exist; existing files are preserved. Use official export for other versions or layouts.

The converter validates XML and source embedded G-code MD5, rejects unsupported/multiple plates and ambiguous ZIP entries, removes geometry and compares every unaffected entry byte-for-byte. G-code, stored MD5, settings, warnings, plate metadata and previews are retained. The container SHA256 changes and must identify the actual dispatched artifact. Preserve the editable project and original ready package.

After conversion:

1. Run `print_audit.py slice` on the new file. Retained warnings remain unresolved warnings.
2. Open it in the actual Studio version and confirm direct G-code loading, thumbnail, layer count, time, material and toolpaths. Offline conversion cannot establish UI behavior on a new version.
3. Use this verified file/hash in dispatch intent. Reconcile any later re-slicing/settings change before Send.

No conversion, successful offline audit or GUI preview authorizes printer dispatch by itself.
