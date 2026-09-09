# Shape and fabrication decisions

## Existing models

Preserve source, author/license and selected print profile. A successful print at one size does not establish success after scaling: small features, thickness, support relationships and bending loads change. Lower infill can reduce weight but does not change a bulky silhouette. Hollowing requires considering weak shells and trapped support.

Apply scale once from known imported dimensions. Keep proportions unless explicitly redesigning the object. Reorient before maximizing size if that improves support removal; distinguish display orientation from print orientation. For major booleans, export/reimport and inspect all shells because automatic repair can remove intended detail.

## Parametric forms and functional parts

Use named millimetre parameters and meaningful constraints. Build ribbons from closed profiles with real thickness and continuous attachment, using smooth section changes at thin roots. OpenSCAD is suited to constructive solids, extrusions and mathematical forms; detailed anatomy may need sculpting.

For functional parts, preserve hole spacing, mating surfaces and tolerances rather than globally scaling every feature. A critical fit may need a small clearance coupon. STL does not preserve analytic CAD surfaces; do not label a triangulated wrapper as an editable STEP solid.

The bundled ribbon example narrows with height, including its thickness: nominal 2.4 mm becomes approximately 1.49 mm at scale 0.62. Further tapering can make it fragile. More twist increases per-layer lateral displacement and overhang demand. Recheck the sliced tip, base attachment and stability after changes.

## Organic sculpture

Work from silhouette and gesture to anatomical proportions, continuous surfaces, detail and print adaptation. Preserve the modeling engine's editable source alongside STL.

Inspect modifiers at export resolution, thicken open sheets and join or intentionally split parts. A pleasing render can hide zero-thickness fabric, floating eyes, intersections or fragile ears. Remeshing can erase detail and dimensional fit; keep the source and compare before/after mesh views.

An image leaves unseen surfaces undetermined. Identify the features that must match and disclose significant invented geometry. Generated meshes commonly need unit correction, coherent topology, thicker extremities and a stable base. Fidelity and easy support removal cannot be established from the image alone.

## Thickness, stability and support removal

- Visual lightness comes from curves, readable silhouette and negative space. Physical strength comes from continuous attachment and adequately thick roots; infill is a separate choice.
- Around three actual extrusion lines can be an initial decorative-wall study for a 0.4 mm nozzle, not a universal minimum. Long cantilevers and parts handled during support removal often need more. Use the real line width and layer preview.
- Trace a path for fingers/pliers and for the removed support piece to leave the model. Avoid support trapped inside enclosed skirts, intertwined limbs and narrow deep channels. Place contacts away from primary display surfaces where possible.
- Rotation, self-supporting section changes or discreet splits can reduce difficult support. Splits add assembly and seams; make significant appearance/use tradeoffs reviewable.
- Support interface gaps, density and material pairing trade removal force against underside finish. Retain a material/printer baseline and adjust a few parameters from actual feedback. Tree support does not prove easy removal.
- A 45-degree rule is only an approximation: cooling, span, speed, curvature and layer height matter. Inspect isolated starts and lateral displacement in the slice.
- Tall moving-bed prints need adhesion and stiffness while being built. An object that stands upright after printing may still wobble before completion.

## Feedback

Record appearance, functional dimensions when relevant, defects, broken features, support-removal effort and difficult areas. Change the affected modeling/orientation/support choice. Device completion alone does not validate a reusable support-removal preset.


## Surface detail and local constraints

Match the kind of detail, not just its amount. Animal fur may need connected,
rounded clumps and variable flow; scales may need distinct overlapping ridges;
feathers need a supported root and defined edges. A single repeated thin-plate
pattern can have high face count while failing the selected appearance. Check
the neutral-material exported mesh at the intended size and from the actual
display views; texture and polygon count do not establish printable relief.

Measure or explicitly leave unknown the vulnerable regions after scaling.
Keep wall thickness, attached relief and load-bearing connections as distinct
rules, scoped by part and process. A normal-ray sample can detect a thin risk
but cannot certify a global minimum. Geometric contact, a calculated center
of mass and a finished object standing still also do not certify adhesion or
strength throughout a moving-bed print. See
[refinement-and-validation.md](refinement-and-validation.md) for configured
reports and revision-bound evidence.
