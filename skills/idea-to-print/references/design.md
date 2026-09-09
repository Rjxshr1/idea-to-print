# Concepts designed for eventual printing

Translate the user's brief into subject, gesture, silhouette, material appearance,
target size and support strategy. Defaults are proposals, not universal tastes.
For flowing sculpture, a useful recipe is:

> One [subject] sculpture, [material appearance], [specific gesture], readable
> curves and negative space. A visually modest stable base and continuous
> [wind/cloud/ribbon/architectural] supports carry the form. Strong concealed
> roots and resilient ends. Full sculpture and base visible in a clear
> three-quarter view, plain background, lighting that reveals shape. No text,
> scenery, extra objects or temporary slicer supports.

Vary geometry across candidates: pose, movement direction, negative space and
support path. Lighting-only variations do not resolve a silhouette choice.
Respond to “too heavy” by changing form, not just reducing infill. Respond to
“looks unstable” by changing contact/load paths and balance, not camera angle.

Permanent sculpted supports stay with the object. Temporary support trees belong
to the later slicer output. Do not bake trees into the reconstruction reference.
A light-looking curve can have thicker hidden roots; thin appearance does not
require fragile structural sections.

Render actual returned images through working host media or absolute local paths.
Keep a candidate ID, image hash and caption per version. Editing an accepted
image creates a new version. Do not silently switch to a different candidate.


## Coherent multi-view references

For a complex object whose hidden structure matters, derive additional views
from the selected design rather than generating unrelated candidates. Front,
left, right, back and three-quarter views are useful when they resolve an actual
ambiguity; do not force a fixed view count on simple shapes or adequate uploads.

Before reconstruction, compare silhouette, pose, number/placement of limbs,
horns, wings, major markings and permanent supports across views. Keep each
view in a separate file with its label, source and hash. Correct inconsistent
views or omit the contradictory input with the assumption recorded. More
inconsistent images are not better constraints.

A multi-view collage is a presentation, not automatically a valid multi-view
API input. Extract actual individual views only when the service accepts those
files, then inspect each; extraction does not establish geometric consistency.
Never copy the same image to claim additional views. AI turnarounds are design
inferences, not a calibrated scan of a physical object. Verify the provider's
current view limit and available interface rather than assuming a product claim
is supported by the local adapter.
