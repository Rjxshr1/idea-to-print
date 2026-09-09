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

Suggested states: `design/selected → generate/import → inspect → refine →
validate → slice → ready → dispatch_intent → prepare/running → completed →
accepted`. Skip work already satisfied by current evidence. “Validated” must
state its scope; it does not mean every manufacturing property was tested.

Geometry, scale, orientation, profile or material changes invalidate downstream
checks. Preserve superseded files/evidence without applying them to a new revision.
If an attempt may already have dispatched, reconcile the printer before sending
another revision. Early device telemetry can retain a prior task's percentage or
current-layer number; check new identity and expected total layers together.

On restart: read manifest → verify artifacts/hashes → reconcile outstanding remote
generation or print attempts → continue the first incomplete stage. Recover a
cached model before submitting another generation. Ask only for an actual missing
decision or physical fact after preparing the reviewable result.


## Revision-bound evidence

`scripts/refinement_job.py` maintains `job.json.refinement` alongside the
existing selection and authorization. It records immutable revision IDs,
parent relationships, actual STL/editable/preview/reference paths and SHA256,
optional operations recipes, deterministic geometry reports, attributed
appearance review, audited sliced packages and attributed layer/support review.
Use the exact CLI in the
[refinement reference](../../printable-modeling/references/refinement-and-validation.md#record-revisions-and-evidence).

The three domains are fidelity (`appearance`), geometry and slice. Their
`PASS / FAIL / UNKNOWN` statuses must remain separate before aggregation.
A user-supplied approval is recorded as such; a tool result or agent judgment
does not impersonate it. Geometry checks invoke the validator instead of
accepting a hand-written overall pass.

Status rehashes revision artifacts and evidence. Missing/changed evidence is
`UNKNOWN` with a `STALE` reason; retain history and register changed revision
artifacts under a new ID. A diagnostic slice is allowed before full readiness,
but its package audit cannot establish support accessibility by itself.
The status command never grants print authorization or dispatches a task.
Successful record writes return 0 even when a stored result is unknown; the
explicit `status` query uses exit codes 0=PASS, 2=FAIL and 3=UNKNOWN.

Keep local task photos, renders, account/device details and real print logs out
of the public repository. Public examples and regression fixtures should be
synthetic and should not start model generation or operate a printer.
