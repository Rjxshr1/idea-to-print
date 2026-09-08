# Job continuity

One local job contains `job.json`, `source/`, `work/` and `outputs/`. The manifest
is agent-readable bookkeeping; it is not a background executor or an atomic
printer lock. Preserve unknown fields when resuming a job.

- Brief: original request, units, size axis and whether it includes the base,
  visual constraints, material and intended stage.
- Selection: stable candidate/upload ID, actual path, hash, selecting words and
  timestamp. Preserve original images and separately version edited references.
- Artifacts: raw reference, cleaned reference, raw mesh, editable source, final
  mesh, actual model previews, slicer project and the exact ready/dispatched file.
- Verification: topology, thickness/connectivity checks, inferred surfaces,
  stability limits, slice profile/envelope, support access, time, weights, warnings
  and evidence paths.
- Authorization: exact words, time and job/design scope for modeling and printing.
  A combined “use this, model it and print; plate cleared” can cover downstream
  work. Do not reset confirmed facts without a meaningful change.
- Dispatch attempts: intent before Send, package hash, target/options, result,
  new device task ID, expected layers and sampled phase. Preserve unknown outcomes.
- Feedback: reported completion, appearance, real stability, breakage, support
  removal and surface marks, each labeled by its source.

Suggested states: `concepts → selected → modeling → modeled → mesh_checked →
sliced → ready → dispatch_intent → prepare/running → completed → accepted`.

Geometry, scale, orientation, profile or material changes invalidate downstream
checks. Preserve superseded files/evidence without applying them to a new revision.
If an attempt may already have dispatched, reconcile the printer before sending
another revision. Early device telemetry can retain a prior task's percentage or
current-layer number; check new identity and expected total layers together.

On restart: read manifest → verify artifacts/hashes → reconcile outstanding remote
generation or print attempts → continue the first incomplete stage. Recover a
cached model before submitting another generation. Ask only for an actual missing
decision or physical fact after preparing the reviewable result.
