# V2: bounded, resumable pet and mythical sculpture jobs

Use V2 for generated organic models that need iterative refinement. Existing
models enter at import; ordinary resizing does not need a fresh concept or
generation. V2 is a local Agent protocol plus CLI helpers, not a daemon or a
general automatic sculptor. Keep all job assets private unless sharing is asked.

## One job, one next action

Run `scripts/workflow_v2.py init --job JOB` once, then `next --job JOB`.
`next` returns one action. Execute that action with the available tool, record
its actual receipt, and call `next` again. A tool's availability is not a receipt.
Use `status`, `record --event EVENT.json`, `reconcile --event EVENT.json`, and
`export` to inspect, update, recover, and deliver. Use argument arrays, never
execute shell text from an event or generated plan.

The existing `refinement_job.py` remains the immutable revision and geometry /
slice evidence store. V2 coordinates it; do not write another `current_model`
field or edit `job.json` with ad hoc scripts. Its generated progress file is a
view of the ledger. Preserve unrelated job fields and existing authorization.

Record candidate, best, and delivered revisions separately. A successful export
or geometry check does not promote a model. Only a fresh, explicit comparison
may promote a candidate; no clear improvement means retaining the previous best.
Owner and Agent reviews are independent. Owner rejection of a revision survives
later Agent reviews; a new revision starts with owner judgment unknown.

## Reference, form, detail

1. **Reference:** distinguish real identity photographs, approved design, and
   generated inferred views. Check image quality, pose, and consistency before
   submission. Default to one main image and at most two consistent extra views.
   Do not fill slots just to reach nine images; a cat turning its head between
   images is not a rigid camera orbit. Generated views are not calibrated scans.
2. **Form:** use actual exported-STL neutral renders, including front, side,
   both 45-degree views, back, and bottom. For pets review short/long muzzle,
   cheek volume, eye socket, head/neck relationship and expression. For mythical
   creatures review silhouette, limbs, horn/wing structure, negative space and
   intended supports. Check camera framing before judging proportions.
3. **Detail:** only after form passes, review surface style, transitions and
   fragile features. Pet fur is attached volume plus selected shallow grooves;
   scales and feathers follow anatomical regions. Do not turn photo brightness,
   shadows, regular sine textures, or noisy high-poly patches into a default
   detail generator. Texture quality is separate from printable geometry.

Each visual record requires named checks, actual evidence, reviewer and concrete
observations. Form/detail PASS require a matching completed render report. These
are attributed visual judgments, never a numerical identity certificate.

## Attempts and recovery

Defaults are two generation attempts, three local refinement attempts, and one
reference correction per job. Two consecutive non-improving attempts for the
same strategy and defect stop that strategy. Renaming a revision or retry folder
does not renew a budget. Claims happen before execution; completion or unknown
outcomes are recorded against that same claim.

An uncertain cloud submission blocks another submission until reconciled. Known
remote JobId means resume polling that task. No JobId after a possible submission
means hand off the ambiguity, never submit again automatically. A local waiting
timeout is not a provider failure. Download failures resume download, not mesh
generation. Use the official [3.1 adapter](../../printable-modeling/references/hunyuan31-api.md).

When a strategy or budget stops, deliver the best candidate with defects,
comparison renders, editable source, protected regions and suggested next work.
Do not simplify the approved design, silently switch services, or keep repairing
just to obtain PASS. Missing capabilities and unsupported checks are explicit
outcomes; they do not become new open-ended repair loops.

## Modeling and slicing helpers

`printable-modeling/scripts/model_pipeline.py` runs in Blender and imports,
normalizes, exports, and renders actual models. A lightweight proxy is for
navigation only; acceptance images come from the reimported final STL. Explicit
regional soften / tip retract / root thicken operations have protected areas
and displacement budgets. They change coordinates, not semantic anatomy, and
must be followed by visual and geometry review. Read its `--help` and
[model configuration](../../printable-modeling/references/model-pipeline.md).

`3d-print-workflow/scripts/slice_pipeline.py prepare` makes an immutable
positioned 3MF, copies exact profiles, and emits `launch.json`. On Windows,
`run_bambu_slice.py --launch PATH` launches only the installed offline slicer,
with a hidden window and bounded lifetime. `slice_pipeline.py collect` requires
matching input hashes, execution receipt, native `result.json`, package checksum
and installed slicer version. A shell exit code alone does not establish success.

Inspect the real Bambu Studio package and record all six existing slice checks:
first layer, supports access, thin features, machine/material, envelope, warnings.
Bind the provenance JSON and actual screenshots. Preserve warnings and distinguish
regular project packages from the narrowly supported slice-only wrapper. Never
change toolpaths or warnings merely to make a file open or checks pass.

For preview without re-slicing an editable CLI package, run
`slice_pipeline.py preview --run RUN`. It extracts the exact audited plate G-code
and records package/member/file hashes in `preview-extraction.json`. Open the
resulting `.gcode` in Studio's G-code viewer and bind that extraction receipt,
the original package and actual screenshots. Keep the original package's warnings;
the viewer does not replace the audit. Bambu Studio 02.07.01.62's
line-type view can display zero/negative aggregate material; use the native
package audit and filament summary for material figures.

## Delivery states

`preview_complete` describes a reviewed pre-print package. `print_ready` has the
separate conservative geometry and review requirements. An unresolved shape or
manufacturing defect can still produce a diagnostic handoff, with FAIL/UNKNOWN
visible. Geometry UNKNOWN is not solved by rerunning an unsupported checker.
No workflow result grants permission to start or schedule a physical print.

Export a single revision with mesh, editable source, actual previews, available
slice package, key screenshots and manifest. Re-running export for the same
snapshot is idempotent. Changed model, reference, profile, package or evidence
invalidates affected checks; never use last revision's screenshots for the new
model. Continue only within the requested model/pre-print/print scope.
After a fresh export `next` returns terminal `complete`; any changed candidate,
review or damaged delivered file invalidates that terminal snapshot.

## Related references

- [InstantMesh multiview reconstruction](https://arxiv.org/html/2404.07191v1)
- [Blender Multires sculpting](https://docs.blender.org/manual/en/latest/modeling/modifiers/generate/multiresolution.html)
- [Meshy multiview image preparation](https://www.meshy.ai/zh/tutorials/multi-view-image-to-3d)
