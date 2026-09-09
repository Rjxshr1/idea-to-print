# Actual model pipeline

`scripts/model_pipeline.py` executes inside Blender. It preserves raw bytes and
an imported high-poly scene, makes a separate lightweight proxy, applies only
explicit regional operations, exports STL/GLB/BLEND, then reimports the final STL
for acceptance renders. A new preparation refuses a nonempty output revision;
the explicit render-only recovery entry below preserves completed evidence.

```text
blender --background --factory-startup --disable-autoexec --python-use-system-env \
  --python-exit-code 1 --threads 4 --python /ABS/skills/printable-modeling/scripts/model_pipeline.py \
  -- --source /ABS/job/source/mesh.stl --out-dir /ABS/job/model-v1 --config /ABS/job/model-config.json
```

The exercised WSL Blender needs `--python-use-system-env` for NumPy. Check the
chosen runtime once. Rendering uses CPU by default and does not interrupt other
model services. Example explicit configuration:

```json
{
  "schema_version": 1,
  "orientation": {"up_axis": "Z", "front_axis": "-Y"},
  "normalize": {"longest_mm": 160},
  "proxy": {"max_faces": 80000},
  "render": {"resolution": 800, "samples": 64, "threads": 4, "denoise": true,
    "views": ["front", "side", "left45", "right45", "back", "bottom"]},
  "operations": []
}
```

Orientation describes Blender world coordinates **after format import**; GLB/FBX
importers can already transform axes. Normalization uses X-right/Z-up/front -Y,
centered XY and ground Z=0. Use the actual requested size, not this example for
all designs. For a continuing millimetre STL/BLEND repair, explicit
`normalize: {"preserve_mm": true}` with Z/-Y orientation keeps the coordinates
and avoids rescaling a shortened tip. Do not combine it with longest_mm.

All six full-object views are required for V2 visual PASS. Optional `face` in
views requires `render.face.center_mm` and `span_mm`. Inspect framing and actual
output. Render reports bind final STL, each image, completion, script hashes,
settings and denoising availability. A proxy or concept is not final geometry.

## Regional operations

Declare stage, affected box, feather width, protected boxes and displacement
limit. Empty protection is explicit. Example:

```json
{
  "kind": "soften", "stage": "shape",
  "region": {"min_mm": [-10,-30,90], "max_mm": [10,-10,110]},
  "feather_mm": 2,
  "protect_regions": [{"min_mm": [-4,-31,103], "max_mm": [4,-20,111]}],
  "max_displacement_mm": 0.3,
  "parameters": {"alpha": 0.15, "iterations": 3}
}
```

`soften` changes selected coordinates; `blunt_tip` retracts a selected tip toward
an axis/plane; `thicken_root` increases local radial extent. They do not perform
automatic anatomy, segmentation, boolean repair or global thickness proof. Tip
retraction does not guarantee a radius and may create degenerate faces: recheck.

Protection conservatively freezes every vertex of every triangle whose bounding
box overlaps a protected box, including triangles crossing the box with all
vertices outside. It may freeze extra neighboring faces. The report names that
conservative method and counts fully frozen triangles. It does not check whether
another edited surface later intrudes into the protected volume.

Protection and displacement are measured per operation; cumulative change is
reported. Complete shape before fabrication operations. No default photo-brightness
displacement, periodic fur, global remesh or global smoothing is applied.
Every edited export still needs form/detail and geometry checks before slicing.

## Recover interrupted rendering

Use this entry only after the revision recorded its exported STL hash and
configuration. It loads that STL and renders only the missing configured views:

```
blender --background --factory-startup --disable-autoexec --python-use-system-env \
  --python-exit-code 1 --threads 4 --python /ABS/skills/printable-modeling/scripts/model_pipeline.py \
  -- resume-render --out-dir /ABS/job/model-v1 --config /ABS/job/model-config.json
```

The config argument is optional on resume; the saved, hashed input and resolved
configuration remain authoritative. A supplied config must match the original
bytes. Resume verifies the recorded STL and other exports, both configuration
files, every completed image and its camera. Changed geometry, configuration or
recorded images requires a new revision; recovery never overwrites such evidence.
A completed bundle is a no-op.

Resume does not import the original FBX/GLB/BLEND, normalize dimensions, create a
proxy or execute local operations again. Completed PNGs remain byte-for-byte
untouched. An existing PNG without a matching completed report entry is preserved
as *.incomplete-<sha256>.png before its view is regenerated. Prior report bytes
are preserved under resume-history/, and report history records which views were
reused/generated and the implementation hash used for recovery. Geometry-generation
implementation hashes are retained; a new render implementation does not relabel
the earlier mesh history. Missing images and a partially written report remain
incomplete until the exact configured views finish.

Recovery cannot adopt an unrecorded partial STL export or repair a failed import.
It also does not make a prior form/detail or geometry verdict pass. Re-register
changed reports/evidence through the revision ledger where required.

## Transform and runtime boundaries

When a source object has a negative world-transform determinant, the transformed
working copy's polygon winding is reversed as well. This preserves the incoming
orientation under reflection; it does not correct already inconsistent or inward
source faces. The original imported high-poly file is preserved before this step.

CPU denoising is enabled only when the runtime's Cycles RNA enum exposes
OpenImageDenoise and assignment succeeds. Builds with no denoiser remain usable:
the report explicitly records enabled:false, the available enum and the fallback
reason, and retains raw CPU sampling. Higher sample counts may reduce noise, but
neither denoising nor smooth display normals repairs geometric surface artifacts.
