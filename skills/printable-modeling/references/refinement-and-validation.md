# Refinement and validation

Use this reference for generated organic meshes, substantial shape changes or a
repeatable repair loop. A simple existing model can enter at its first relevant
stage; do not regenerate accepted geometry or impose an unrelated design review.

## Three independent questions

| Domain | Evidence | What a pass means |
|---|---|---|
| Fidelity, recorded as `appearance` | Selected reference, actual mesh renders from meaningful views, attributed observations | The stated visual criteria were reviewed on this revision |
| Geometry | Exported millimetre STL, profile, deterministic report and hashes | Supported required checks passed within their stated methods and coverage |
| Slice | Exact sliced package, machine/material settings, layer/support previews and attributed review | The package and stated manufacturing checks were reviewed |

Use `PASS / FAIL / UNKNOWN`. `UNKNOWN` includes unrun, unsupported, insufficient
coverage and stale evidence. Do not turn it into a pass by writing “fixed” in a
note. An agent's appearance judgment remains an agent judgment; use
`--reviewer owner` only for actual owner feedback. Neither kind grants printing
permission.

A geometry pass can coexist with an appearance failure. A texture can depict
fur, scales or cracks that do not exist in the printable surface. Render a
neutral-material view of the exported mesh as well as any colored preview.

## Design and high-poly input

Preserve the selected image and its hash. For multiple views, retain each file's
view label, provenance and selection; check pose, limb count, silhouette, markings,
horns/wings and permanent supports across views. Generated turnarounds are
inferred design references, not calibrated scans. A collage must be split into
actual view files for a service that expects independent views; extracting tiles
does not establish that their geometry is consistent. Duplicated files do not add
views. Resolve contradictory inputs before submission.

The official Tencent Hunyuan3D V3.1 website has been exercised for high-poly
drafts. Check its currently available mode and account quota when using it.
The repository's `hunyuan_shape.py` is the separate 2.1 public demo adapter,
which submits one image. The optional [official 3.1 adapter](hunyuan31-api.md)
is a separate configured route. See [image-to-3D.md](image-to-3d.md) for the demo
and [V2](../../idea-to-print/references/workflow-v2.md) for bounded execution.

## Inspect → refine → recheck

Keep raw meshes immutable. Register the baseline, then create a new editable and
exported revision for a concrete defect. Record the operation, affected region,
parameters, reason and before/after preview paths in a local operations JSON;
`refinement_job.py` preserves its bytes and hash without interpreting it as a
Blender program. Repeat until applicable criteria are satisfied or a concrete
remaining limitation needs a design decision.

| Observed defect | Possible local operation | Recheck |
|---|---|---|
| Wrong face, proportions or gesture | Local sculpt/deformation with a protected body/silhouette | Reference match from front and three-quarter views |
| Smooth areas where surface relief was requested | Sculpt or controlled displacement on actual surfaces | Neutral-material geometry, thickness and slice detail |
| Fragile whisker, horn or feather root | Curve with bevel, local thickening or a thicker continuous root | Part-specific connection dimensions and removal loads |
| Inconsistent scales, feathers or patterns | Surface-following geometry/curves/Geometry Nodes | Overlap, exposed tips and preserved anatomy; avoid a generic repeated pattern everywhere |
| Unwanted slab, hole or detached fragment | Targeted removal, joining or topology repair | Connected shells, boundaries and internal intersections |
| Intended negative space is fused | Controlled boolean or local remodeling | Wall/connection thickness, usable opening and support exit |
| Footprint too rounded or unstable | Measured local sole trim or designed base union | Contact at first layers, silhouette, load path and assembly |

These are defect-specific methods, not a universal automated sculptor. Uniform
smoothing/remeshing can erase the accepted details. Keep the source and compare
actual exports before accepting it. Do not add a base or change a pose silently
when it changes the selected design; faithful routine repairs remain in scope.

## Configure the geometric scope

Copy [printability-profile.example.json](../../3d-print-workflow/references/printability-profile.example.json)
into the private job and edit it for the actual process. Its 180 mm volume,
0.4 mm nozzle, PLA and illustrative thresholds are examples, not owner's device
settings or universal safety minima.

- `build_volume_mm` and `reserve_total_mm` use the exported fixed orientation;
  reserve is total per axis, not per side. The slicer must still check supports,
  brim and plate exclusions.
- `rules` contains `wall_min_mm`, `detail_min_mm` and `connection_min_mm`.
  Relate these to the actual line width, layer height, material, root length and
  handling. A surface relief and a cantilever need different targets.
- `regions` may override rules within millimetre bounding boxes in the STL's
  coordinate system. Update those regions after changing scale or orientation.
  For example, a local tail/root rule may be stronger than the body's relief.
  A bounding box is an explicitly chosen scope, not automatic anatomical labeling.
- `required_checks` declares the standalone gate's diagnostic scope. Keep a
  scope/rationale when selecting a narrower inspection. The full sculpture
  ledger still requires its fixed 16 geometric checks; shrinking a profile
  cannot remove those requirements or produce a full-workflow pass. Measured
  failures remain failures even when excluded from the diagnostic scope.
- `thickness_sampling` is optional. A short normal-ray distance is a warning or
  detected risk under that method. Even all samples above threshold leave the
  complete minimum wall measurement `UNKNOWN`; this is not a whole-surface
  thickness certificate.

Run from the repository root with installed modeling dependencies, replacing the
absolute example paths with real files:

```sh
python skills/3d-print-workflow/scripts/printability_gate.py inspect \
  --mesh /path/to/job/outputs/model-v1.stl \
  --profile /path/to/job/work/printability-profile.local.json \
  --out /path/to/job/work/geometry-v1.json
```

The checker welds exactly coincident STL coordinates for topology analysis and
reports input integrity, closed surfaces, edge and vertex manifoldness, winding,
degenerate/duplicate faces, shared-edge components and fixed-orientation bounds.
Read the returned normal/orientation method and limitations. It does not certify
every self-intersection, complete wall/detail/connection minimum, contact or
stability condition. Those remain `UNKNOWN` when not covered. Appearance,
slicer islands and support removal are separate categories and are not silently
approved by geometric topology.

Each check includes status, required flag, method, measurements, thresholds and
limitations. `overall_scope=geometry` aggregates geometry only: any geometric
failure takes precedence; otherwise an unknown required geometric check yields
`UNKNOWN`. A geometry `PASS` does not assert all optional checks passed or that
the workflow is ready. The full example deliberately includes checks that this
tool cannot yet complete automatically. The CLI exits 0 for PASS, 2 for FAIL
and 3 for UNKNOWN; an UNKNOWN report was still produced successfully. Optional
`expected_mesh_sha256` in the profile can bind the intended mesh identity.

## Record revisions and evidence

`job.json` must already exist, for example after image intake. The following
commands use absolute artifact paths and do not modify their geometry:

```sh
python skills/idea-to-print/scripts/refinement_job.py register \
  --job /path/to/job --id v1 \
  --mesh /path/to/job/outputs/model-v1.stl \
  --editable /path/to/job/work/model-v1.blend \
  --preview /path/to/job/outputs/model-v1-front.png \
  --preview /path/to/job/outputs/model-v1-back.png \
  --reference /path/to/job/source/selected.png \
  --operations /path/to/job/work/operations-v1.json \
  --note "Round cheeks locally; preserve pose and body dimensions"

python skills/idea-to-print/scripts/refinement_job.py check \
  --job /path/to/job --id v1 \
  --profile /path/to/job/work/printability-profile.local.json

python skills/idea-to-print/scripts/refinement_job.py review \
  --job /path/to/job --id v1 --kind appearance --status FAIL --reviewer agent \
  --evidence /path/to/job/outputs/model-v1-front.png \
  --note "Example observation: cheeks still narrower than the selected reference"

python skills/idea-to-print/scripts/refinement_job.py status --job /path/to/job --id v1
```

The appearance result above is an example of recording an observed failure, not
an instruction to judge every model that way. Use actual observations and
evidence. `--operations` and `--reference` are optional; use `--parent v1` when
registering the next revision to retain the repair chain. Revision IDs are
immutable. The check command runs the validator; it does not accept a manually
supplied geometry pass.

The ledger independently aggregates all 16 geometry checks: input integrity,
watertightness, edge and vertex manifoldness, winding, degenerate and duplicate
faces, connected components, outward normals, build volume, self-intersection,
minimum wall, minimum detail, minimum connection, base contact and stability.
Every item must be present, explicitly required and `PASS` for full geometry
acceptance. A missing, excluded, unsupported or unknown item yields `UNKNOWN`;
any automatic geometry `FAIL` still takes precedence. Appearance and slice
reviews cannot replace these measurements. `status` exposes `coverage` with
`gate_scoped_status`, `profile_required`, `excluded` and `unknown_full`, so a
diagnostic gate `PASS` remains distinguishable from full sculpture acceptance.
Existing scoped records are recomputed from their unchanged tool reports when
status is read; their old cached `PASS` is not reused as full acceptance.

The ledger lives in `job.json.refinement`, preserving unrelated selection,
authorization and dispatch fields. It rehashes artifacts and evidence when
checking status. Changing a registered mesh, preview, profile, validator, report
or sliced package makes affected evidence stale/unknown. Register changed
revision artifacts under a new ID and rerun affected checks. This bookkeeping
lock coordinates ledger writes only, not physical printer dispatch. Record-writing
commands return 0 for a successful write even if the result is unknown; the
explicit `status` query returns 0=PASS, 2=FAIL or 3=UNKNOWN.

## Diagnostic slice → manufacturing review

Diagnostic slicing may expose narrow tips, unsupported starts and trapped
supports before every geometric item is resolved. Record it as diagnostic and
continue repairs; a sliced package alone is not a ready verdict.

Use the real machine, nozzle, material and plate. Record the exact exported
package and how it was produced from this revision:

```sh
python skills/idea-to-print/scripts/refinement_job.py slice \
  --job /path/to/job --id v1 --plate 1 \
  --package /path/to/job/outputs/model-v1.gcode.3mf \
  --note "Diagnostic slice from model-v1.stl; record actual slicer/profile here"
```

The slice helper audits G-code integrity and the package's declared outside
flag. The mesh-to-toolpath relationship is operator-recorded; it is not
independently reconstructed from G-code. Warnings remain in the report.

After inspecting actual layer/support previews, record a `review --kind slice`
with six `--check NAME=PASS|FAIL|UNKNOWN` entries: `first_layer`,
`supports_access`, `thin_features`, `machine_material`, `envelope` and `warnings`.
Give `--evidence` files, the reviewer and concrete observations. Its overall
`--status` must agree with those six checks. A screen capture or review file is
evidence of the stated observation, not a geometric proof of all hidden layers.

Required unresolved defects and checks prevent calling this tracked revision
ready. Narrow diagnostic profiles cannot waive the ledger's 16 fixed geometry
requirements. An overall workflow pass still has the stated measurement and
review limitations and grants no printing authorization.
Preserve an already applicable authorization and plate/material confirmation;
request a new decision only if missing facts or a meaningful change requires it.
A model-only request ends with model files and qualified evidence. Dispatch,
scheduling and monitoring are separate requested actions handled by
`3d-print-workflow`.
