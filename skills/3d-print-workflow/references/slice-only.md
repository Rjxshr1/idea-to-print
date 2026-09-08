# Preserve a checked slice when opening Studio

With Bambu Studio 02.07.01.62, an observed CLI-produced `.gcode.3mf` contained editable geometry. Opening it as a project caused re-slicing with substituted acceleration settings, changing reviewed toolpaths/time. The suffix alone did not mean “load only the original G-code.” This is a version/layout-specific observation, not a claim about every Studio release.

Prefer official **export plate sliced file** when it preserves the reviewed slice. For this exact editable-CLI-container issue, `scripts/make_slice_only.py` converts the known layout to the sliced-file structure observed in official Studio export. It removes editable geometry, not G-code or warnings.

From the repository root:

```sh
python skills/3d-print-workflow/scripts/make_slice_only.py --source jobs/example/outputs/job-ready.gcode.3mf --output jobs/example/outputs/job-dispatch.gcode.3mf --report jobs/example/work/slice-only.json
```

Supported scope is deliberately narrow: producer `BambuStudio-02.07.01.62`, exactly `plate_1`, observed core/production XML namespaces, external `object_N.model` geometry and no assembly items. Python 3.11+ standard library is sufficient. Output/report parent directories must exist and existing files are not overwritten. Different versions/layouts require official export and inspection before adapting the helper; do not disable its checks to force conversion.

The converter validates XML and source embedded G-code MD5, rejects unsupported/multiple plates and ambiguous ZIP entries, removes geometry and compares every unaffected entry byte-for-byte. G-code, stored MD5, settings, warnings, plate metadata and previews are retained. The container SHA256 changes and must identify the actual dispatched artifact. Preserve the editable project and original ready package.

After conversion:

1. Run `print_audit.py slice` on the new file. Retained warnings remain unresolved warnings.
2. Open it in the actual Studio version and confirm direct G-code loading, thumbnail, layer count, time, material and toolpaths. Offline conversion cannot establish UI behavior on a new version.
3. Use this verified file/hash in dispatch intent. Reconcile any later re-slicing/settings change before Send.

No conversion, successful offline audit or GUI preview authorizes printer dispatch by itself.
