# Idea to Print

Turn a short idea, a reference image or an existing model into an editable 3D sculpture, with shape review, sizing and a complete pre-print package.

[中文](README.md) · [Requirements](docs/requirements.md) · [MIT License](LICENSE)

The project provides **three installable agent skills and Python tools** connecting image generation, Hunyuan3D, Blender and Bambu Studio. It supports pets, mythical creatures and other organic sculptures, including workflows starting from an existing model. An agent runs each stage and retains model versions and their reviews.

<div align="center">
  <img src="docs/assets/demo.gif" alt="Idea to Print terminal demo: intake an image job, init the workflow ledger, inspect next action and status" width="880">
</div>

## Features

| Feature | Capabilities |
|---|---|
| Text and image input | Concept selection or direct PNG/JPEG/WebP intake, preserving original bytes and SHA256 |
| Official image-to-3D API | Tencent Hunyuan3D 3.1 SDK adapter with a main image, named additional views and Geometry requests whose face count follows the print size (up to 1.5 million) |
| Staged shape review | Reference consistency, silhouette and volume, followed by fur, scales or feathers; separate visual and manufacturing results |
| Blender processing | Preserve high-poly source; export STL, GLB, BLEND and a lightweight proxy; normalize size or retain millimetre coordinates |
| Regional refinement | Scoped softening, tip blunting and root thickening with protected regions and displacement limits |
| Actual mesh previews | Render the final STL from six views, add face close-ups and resume missing renders after interruption |
| Shared job ledger | `next/status/record/reconcile/export` track stages, attempts, remote tasks and evidence; candidate, best and delivered revisions stay separate |
| Bounded recovery | Defaults of two generations, three repairs and one reference correction; stop strategies without improvement and resume known remote jobs |
| Bambu slicing | Preset inheritance flattening, fixed placement, machine/process/filament snapshots, a Windows/macOS offline CLI launcher and native-result verification |
| Preview and delivery | Open exact extracted G-code, retain layer screenshots, and export models, slices, review history and a file manifest |

Owner and agent judgments are stored separately. An agent review cannot erase an owner's rejection of the same mesh. Changed models, profiles or images invalidate affected checks. Results retain `PASS / FAIL / UNKNOWN`; exhausted attempts produce the best candidate with outstanding work. The bundled geometry gate tests every face pair for self-intersection and, once the profile sets part-specific thresholds, measures wall, detail and connection thickness on every face, base contact area and centre-of-mass stability (for both solid and hollow-shell mass models, reporting the worst). A check without its threshold stays `UNKNOWN` and the job ends at `preview_complete`; `print_ready` needs every geometry check to pass plus the appearance and slice reviews. A job directory can be moved or renamed as a whole; recorded evidence paths follow it.

## Usage examples

With an agent that provides image, modeling and file tools:

> Use $idea-to-print to design a flowing white fox sculpture, 160 mm overall, with a stable base. Show three concepts first.

With an attached image:

> Use $idea-to-print to make this image into a 160 mm sculpture. Preserve the pose, show six views of the actual mesh, and prepare the pre-print package.

With an existing model:

> Inspect this STL, preserve its size and shape, and prepare a white PLA slicing preview for an A1 mini. Include the model, sliced package and outstanding checks.

Jobs can end at concept, model or pre-print delivery. When physical printing is requested and the job's authorization and device conditions are established, the agent uses the official printer interface.

## Installation

Python 3.11+ is required. Install Blender and Bambu Studio separately as needed.

```bash
git clone https://github.com/Rjxshr1/idea-to-print.git
cd idea-to-print
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# Local model processing and the official Hunyuan API
python -m pip install -r requirements-modeling.txt -r requirements-hunyuan31.txt

python install.py --destination ~/.agents/skills
```

Use the directory recognized by your agent, such as `~/.codex/skills`. The installer stops if a target skill already exists and records the active Python interpreter's absolute path for new installations. Merge updates to existing installations while retaining local configuration.

| Skill | Responsibility |
|---|---|
| `idea-to-print` | Text/image intake, design selection, job state and stage coordination |
| `printable-modeling` | Image-to-3D, Blender processing, shape review and sizing |
| `3d-print-workflow` | Geometry checks, slicing, printer configuration and print-flow coordination |

Image generation and desktop control come from the agent host. The official Hunyuan API requires Tencent Cloud credentials and available quota. Blender and slicing run locally; cloud image-to-3D does not use a local inference GPU.

## Try it offline

With `requirements.txt` and `requirements-modeling.txt` installed, run the whole flow offline with no account, network or printer:

```bash
python skills/idea-to-print/scripts/demo.py --out demo
```

It creates a job from a synthetic reference image, builds a cat figurine on a round base, runs all 16 geometry checks and the mesh audit, and prints each real command it runs. With a local Bambu Studio (Windows or macOS), `--slice` also flattens the presets and slices offline; `--printer` picks `a1mini`, `a1`, `p1s` or `x1c`. Nothing is sent to a printer.

## Command-line entry points

Create an image job and inspect its next action:

```bash
python skills/idea-to-print/scripts/prepare_image.py \
  --image /path/to/reference.png --job jobs/my-sculpture \
  --target-mm 160 --brief "White sculpture with a stable base"

python skills/idea-to-print/scripts/workflow_v2.py init --job jobs/my-sculpture
python skills/idea-to-print/scripts/workflow_v2.py next --job jobs/my-sculpture
python skills/idea-to-print/scripts/workflow_v2.py status --job jobs/my-sculpture

# Check API configuration without requesting generation
python skills/printable-modeling/scripts/hunyuan31_api.py config-check
```

Check a model's geometry (copy `printability-profile.example.json` and set the thresholds for the part); exit code 0=PASS, 2=FAIL, 3=UNKNOWN:

```bash
python skills/3d-print-workflow/scripts/printability_gate.py inspect \
  --mesh jobs/my-sculpture/outputs/model-v1.stl \
  --profile jobs/my-sculpture/work/printability-profile.json \
  --out jobs/my-sculpture/work/geometry-v1.json
```

Slice offline (A1 mini on macOS shown; on Windows the presets are under the Studio install's `resources/profiles/BBL`):

```bash
python skills/3d-print-workflow/scripts/slice_pipeline.py resolve-profiles \
  --out jobs/my-sculpture/work/profiles \
  --search /Applications/BambuStudio.app/Contents/Resources/profiles/BBL \
  --machine "Bambu Lab A1 mini 0.4 nozzle" --process "0.20mm Standard @BBL A1M" \
  --filament "Bambu PLA Basic @BBL A1M"

python skills/3d-print-workflow/scripts/slice_pipeline.py prepare \
  --mesh jobs/my-sculpture/outputs/model-v1.stl --out jobs/my-sculpture/work/slice-v1 \
  --machine jobs/my-sculpture/work/profiles/machine.json \
  --process jobs/my-sculpture/work/profiles/process.json \
  --filament jobs/my-sculpture/work/profiles/filament.json \
  --slicer /Applications/BambuStudio.app/Contents/MacOS/BambuStudio \
  --slicer-version 02.08.02.61 --bed 180 180 180

python skills/3d-print-workflow/scripts/run_bambu_slice.py --launch jobs/my-sculpture/work/slice-v1/launch.json
python skills/3d-print-workflow/scripts/slice_pipeline.py collect --run jobs/my-sculpture/work/slice-v1
```

After reference review, the agent submits generation, records actual results and continues the next stage. A possible submission without JobId stops automatic resubmission. Known jobs resume querying; download failures resume downloading.

| Operation | Documentation |
|---|---|
| Job state, reviews, recovery and export | [Workflow](skills/idea-to-print/references/workflow-v2.md) |
| Credentials, view slots, submission and download | [Hunyuan 3.1 API](skills/printable-modeling/references/hunyuan31-api.md) |
| Imports, regional operations, rendering and resume | [Blender configuration](skills/printable-modeling/references/model-pipeline.md) |
| Geometry checks, revisions and slice evidence | [Model validation](skills/printable-modeling/references/refinement-and-validation.md) |
| Other image-to-3D routes | [Service adapters](skills/printable-modeling/references/image-to-3d.md) |
| Optional Bambu LAN status reads | [Printer configuration](skills/3d-print-workflow/references/bambu-lan.md) |

## Deliverables

Export a selected revision with STL, editable BLEND, actual mesh renders, an available sliced package, key-layer screenshots and `manifest.json`. The manifest binds file hashes, model revision, placement, profiles and slicer version. Review history is retained separately. Reviewed previews and diagnostic handoffs with outstanding checks are labeled distinctly.

An agent compares actual meshes with references for shape review; geometry reports state their coverage. Built-in operators handle explicit regional deformations. Complex anatomy and natural fur can continue in Blender using the handoff materials. Official printer interfaces handle dispatch; included scripts prepare, inspect and read device status.

## Development and tests

```bash
python -m pip install -r requirements-dev.txt -r requirements-hunyuan31.txt
python -m pytest -q
```

CI covers Linux/Windows and Python 3.11/3.12, plus the official SDK contract. Offline tests use synthetic models and simulated service responses to cover budgets, recovery, revision selection, locks and evidence integrity.

Original code, documentation and parametric examples use the MIT license. External models, services, software and user images retain their own terms; see [NOTICE](NOTICE.md). Issues and pull requests for features, adapters and documentation are welcome.
