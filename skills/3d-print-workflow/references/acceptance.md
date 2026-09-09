# Evidence levels

| Stage | Evidence | Does not establish |
|---|---|---|
| Modeled | Editable source, units, bounds and rendered mesh views | Printable geometry |
| Mesh checked | Topology report plus inspection of disconnected parts, thickness and intersections | Printed strength, stability or supports |
| Sliced | Actual machine/nozzle/material/plate, layer/support preview, bounds, hashes, time/weight and retained warnings | Physical success |
| Sent | Unique recorded dispatch attempt and outcome | Accepted/started printer task |
| Started | New matching task identity and coherent layers in PREPARE/RUNNING | Adhesion or a completed object |
| Completed | Matching task FINISH/full layers or a labeled user completion report | Surface quality or easy support removal |
| Accepted | Physical inspection/feedback on shape, detail, breakage and support removal | A universal model/material preset |

Useful offline regression cases for workflow changes:

1. Fixed-orientation fitting preserves proportions and chooses whichever actual axis limits the object. Reject invalid dimensions, build volumes and clearance.
2. Corrupted embedded G-code fails MD5 verification while warnings survive report generation.
3. Missing, reversed or duplicate mesh faces do not silently pass basic topology.
4. An uncertain prior dispatch triggers task/state inspection, not another Send.
5. A partial plate view cannot establish clearance; a still-applicable explicit user confirmation resolves it without repeated questions.
6. Modeling/review and workflow tests never initiate physical printing by themselves.

Keep verification proportional. Small fit/thickness/bridge/support coupons are useful only when the actual modeling/printing task needs and authorizes them. Do not print specimens merely to test orchestration code.


## V1 report semantics

Fidelity (`appearance`), geometry and slice review answer different questions.
A closed mesh may still fail resemblance; an accepted likeness may still have
thin connections; an intact G-code package may still contain trapped supports.

- `PASS` applies only to stated checks, methods, artifact/profile and reviewer.
- `FAIL` records an observed criterion failure and the evidence to repair it.
- `UNKNOWN` means insufficient, unrun, unsupported or stale evidence. An
  agent cannot replace it with a pass just by saying repair is complete.

`printability_gate.py` aggregates the required geometry scope while preserving
all checks and limitations; it is not a visual or toolpath reviewer.
`refinement_job.py` coordinates the actual geometry report, attributed
appearance review, slice package audit and attributed slice review. See the
[refinement reference](../../printable-modeling/references/refinement-and-validation.md).
A slice package audit checks its supported metadata/integrity, not every layer.
The ledger's mesh-to-G-code linkage is an operator-recorded provenance claim.

Further useful offline cases: a thin sampling hit cannot become a global
thickness pass; unsupported checks remain unknown; a geometry pass cannot
override a visual failure; changing the mesh/profile/preview/ready package
invalidates affected evidence; neither a workflow pass nor an existing job's
plate confirmation starts or authorizes a different print.
