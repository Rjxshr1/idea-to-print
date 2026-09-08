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
