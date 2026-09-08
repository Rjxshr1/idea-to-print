# Direct uploaded-image entry

Accept a host attachment or local PNG, JPEG or WebP path. An attachment must be
saved to a real readable file by the host before a Python script can consume it;
do not fabricate attachment filesystem paths. If the host only exposes a visual
reference, use its supported file-export mechanism or ask for the missing file.

When the user says “make this image into a model”, that image is already selected.
Skip concept generation. Multiple images may be choices or coherent views of the
same object; establish that distinction if unclear. Do not collage unrelated
images into one reconstruction input.

```bash
python skills/idea-to-print/scripts/prepare_image.py \
  --image /path/to/reference.png --job jobs/my-sculpture \
  --target-mm 160 --brief "Use this image; stable base; accessible supports"
```

`prepare_image.py` validates a static image, copies its bytes unchanged to
`source/selected.<extension>`, and records actual format, dimensions, orientation
and SHA256 in `job.json`. No network call occurs. It refuses existing jobs;
resume their manifests instead of re-importing over an established selection.
Its local resource limits are64MiB and40megapixels, not provider limits.

The helper does not infer model/print permission from free text: the agent records
the user's actual authorization in the manifest. Intake alone never authorizes
printing. An EXIF orientation other than1 must be respected by the visual review;
when cleanup/reorientation is needed, preserve the original and save a separate
reference through the host's image-editing capability.

Useful references show a single complete subject in a clear three-quarter or
side view, with good silhouette separation and limited occlusion. A clean photo,
illustration or concept rendering can be used; unrelated background geometry and
shadows can become unwanted model surfaces. Assess the actual image before using
optional cleanup. A single view leaves the back and underside unspecified.

After intake, follow `printable-modeling`. Hosted generation uploads the selected
file to the named service. A request to import or inspect an image does not itself
call that service. If the user requires local-only handling, do not use it.
