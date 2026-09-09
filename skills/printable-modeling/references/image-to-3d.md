# Image-to-3D routes

Use an uploaded image or a selected concept image to create initial geometry,
then prepare that geometry for the requested size and printing process.

| Route | Integration | Configuration |
|---|---|---|
| Official Tencent Hunyuan3D 3.1 API | `hunyuan31_api.py`: submit, query, download and recover recorded attempts | Official Python SDK, Tencent Cloud credentials and available quota |
| Public Tencent Hunyuan3D 2.1 demo | `hunyuan_shape.py`: single-image geometry generation and cached-result recovery | `gradio_client` and access to the public service |
| Official Hunyuan3D Studio website | Host browser interaction or manual operation | User login and available quota |

All three routes run model inference in the cloud. The selected images are
uploaded to the chosen service. Local processing uses Blender and optional mesh
libraries; see [installation requirements](../../../docs/requirements.md).

## Official 3.1 API

Install `requirements-hunyuan31.txt` and configure
`TENCENTCLOUD_SECRET_ID` and `TENCENTCLOUD_SECRET_KEY`. Temporary credentials
also use `TENCENTCLOUD_TOKEN`. A private JSON file passed with
`--credentials-file` is supported as an alternative. Keep credentials outside
job artifacts and source control.

Initialize the job and record its reference review through the
[job workflow](../../idea-to-print/references/workflow-v2.md). The following
commands run from the repository root:

```sh
python skills/printable-modeling/scripts/hunyuan31_api.py config-check
python skills/printable-modeling/scripts/hunyuan31_api.py submit --job jobs/example --attempt shape-01 --main source/main.png
python skills/printable-modeling/scripts/hunyuan31_api.py query --job jobs/example --attempt shape-01
python skills/printable-modeling/scripts/hunyuan31_api.py download --job jobs/example --attempt shape-01
```

`config-check` checks the SDK and credentials without submitting a job. Generation
uses `Model=3.1`, `GenerateType=Geometry`, `FaceCount=1500000` and the
`ap-guangzhou` region. The helper accepts one main image and, by default, up to
two supplemental views. Supplemental images need a consistency review binding
their paths and SHA256 hashes. Supported view slots and the review format are in
the [API configuration reference](hunyuan31-api.md).

The adapter records submission intent before making the request, saves the
returned JobId, and downloads the original model with its hash. To continue a
recorded attempt after interruption:

```sh
python skills/printable-modeling/scripts/hunyuan31_api.py recover --job jobs/example --attempt shape-01
```

Recovery queries the existing JobId and downloads a completed result. An
uncertain submission without a JobId stays `UNKNOWN` for reconciliation rather
than triggering another generation. Query and download retries are bounded.
Collect completed results promptly: the provider documents a 24-hour validity
period for JobId and result URLs.

## Public 2.1 helper

Use Python 3.11+ and install `requirements.txt`, which provides `gradio_client`.
The helper connects anonymously to
[`tencent-hunyuan3d-2-1.hf.space`](https://tencent-hunyuan3d-2-1.hf.space)
and disables implicit Hugging Face token use. Service access, queueing and API
schema are controlled by that public endpoint.

Save the reference inside the job, then inspect the API and submit one attempt:

```sh
python skills/printable-modeling/scripts/hunyuan_shape.py --describe-api
python skills/printable-modeling/scripts/hunyuan_shape.py --job jobs/example --input source/selected.png --attempt shape-01
```

The first command fetches API metadata. The second submits one image to
`/shape_generation` and generates geometry without textures. Defaults are 30
steps, guidance 5, octree resolution 512, 8,000 chunks, seed 1234 and background
removal; `--seed` selects a different seed. Expected argument names are checked
before submission.

The helper writes `work/shape-request-<attempt>.json`,
`work/shape-result-<attempt>.json`, downloaded files and
`source/shape-<attempt>.glb` with byte count and SHA256. Each attempt ID is
exclusive; run generation attempts for a job sequentially.

If the service returned a result but local result handling was interrupted,
recover the cached response without another network request:

```sh
python skills/printable-modeling/scripts/hunyuan_shape.py --job jobs/example --attempt shape-01 --recover
```

`result_needs_recovery` identifies a cached result awaiting collection.
`submission_uncertain` requires inspecting the recorded request and service state
before starting another attempt. Retain prior models and rejection reasons in
the job record.

## Studio website

Open [Hunyuan3D Studio](https://3d.hunyuan.tencent.com/studio/creation/geo) with
the host's browser tools or use it manually. Select the available model, geometry
mode and reference views, then record the reference hashes, settings, attempt ID
and returned model hash in the job. Available inputs and quotas are shown by the
website. Inspect an existing service task before retrying an uncertain submission.

## Prepare geometry for printing

The [model pipeline](model-pipeline.md) preserves the original high-poly model,
creates a lightweight proxy, applies configured regional edits and exports
STL/GLB/BLEND. It renders the exported STL from six views and can resume an
interrupted render from the saved revision.

1. Import the returned format with its scene transforms, set the orientation and
   normalize to the requested millimetre dimensions.
2. Compare front, side, back and underside views with the reference. Check the
   silhouette, anatomy, openings, permanent supports and base.
3. Apply the needed local edits, such as softening a region, retracting a tip or
   thickening a root. Protect nearby detail and set displacement limits.
4. Check the exported mesh's topology, shells, thickness and intersections using
   the [refinement and validation workflow](refinement-and-validation.md).
5. Slice that same exported revision and inspect its layers, supports and support
   removal access. Printable detail must be present in geometry rather than only
   in textures or shading.

Record visual fidelity, geometry checks and slice results against the exact
revision. For a fully local image-to-3D route, deploy a compatible inference
service separately with its required model weights and CUDA/PyTorch environment.
