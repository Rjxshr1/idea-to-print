# Idea to Print

[中文](README.md) · [MIT License](LICENSE)

An agent workflow for turning a short idea **or an uploaded image** into a
reviewable, printable 3D sculpture and an authorized FDM print.

This repository contains three installable skills and reusable Python helpers.
It is not a hosted upload application, an automatic sculpting engine, or an
unattended printer service. An agent coordinates available image, modeling,
filesystem and desktop tools.

## Start with text or an image

> Use $idea-to-print to design a flowing fox sculpture, white, 160 mm overall,
> with a stable base and accessible removable supports. Show concepts first.

Or attach an image:

> Use $idea-to-print to make this image into a 160 mm printable model. Show
> actual front and back mesh renders before printing.

An explicitly selected upload skips concept generation. Input supports static
PNG, JPEG and WebP. A single view leaves hidden geometry to be inferred and
checked; it is not sufficient evidence of printability.

## Install

Python3.11+ is required for helpers:

```bash
git clone https://github.com/Rjxshr1/idea-to-print.git
cd idea-to-print
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python install.py --destination ~/.agents/skills
```

Use the skill directory actually recognized by your host, such as
`~/.codex/skills` on a host configured that way. The installer refuses to
overwrite existing skills. It installs `idea-to-print`, `printable-modeling`
and `3d-print-workflow`; it does not install Blender/Bambu Studio, grant model
access, or bundle the host's ImageGen or desktop-control plugins.

Run the installer from the activated environment. Each installed skill gets a
local `runtime.local.json` recording that Python's absolute path. Retain this
environment or update the local record when moving it. Agents locate scripts
relative to the actual installed `SKILL.md`, use the configured interpreter and
absolute job paths when invoked outside the clone. Manual skill copies require
configuring a Python environment with the corresponding dependencies.

## Direct image input

```bash
python skills/idea-to-print/scripts/prepare_image.py \
  --image /path/to/reference.png --job jobs/my-sculpture --target-mm 160

python skills/printable-modeling/scripts/hunyuan_shape.py --describe-api
python skills/printable-modeling/scripts/hunyuan_shape.py \
  --job jobs/my-sculpture --input source/selected.png --attempt shape-v1
```

The first command is local only and preserves original bytes and SHA256.
The last command uploads the selected image to the third-party public
Hunyuan3D-2.1 demo and requests a draft GLB. Anonymous access worked in the
observed route; the service can queue, sleep, change or become unavailable.
It must not be used for a local-only request. For JPEG/WebP use the actual
selected path recorded in `job.json`.

Generation attempts are recorded before submission. Recover a cached response
with the same `--attempt` plus `--recover`; do not resend an uncertain attempt.
Next inspect and adapt the real geometry in Blender, preserve editable source,
export STL, then slice with a profile matching the actual printer. Modeling
repairs are specific to the object, not a universal automatic repair algorithm.

Optional local mesh dependencies: `pip install -r requirements-modeling.txt`.
Blender is installed separately. Hosted generation needs no local inference GPU;
local mesh memory depends on complexity. No universal RAM/VRAM minimum has been
measured and64GB is not a requirement. See [requirements](docs/requirements.md).

## Printing and validation

Use official Bambu Studio for dispatch with current job authorization and plate
clearance. Desktop automation must be supplied by the host; otherwise deliver the
checked file for manual dispatch. Included printer code reads LAN telemetry or
one camera frame only and is experimental/unofficial. See
[printer setup](skills/3d-print-workflow/references/bambu-lan.md).

Inspect first layers, thin features, support access and the full envelope,
including brim/supports. Preserve slicer warnings and exact artifact hashes.
Never infer good support removal from a tree-support label. Send once and verify
new task identity, expected layers and actual device phase.

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use synthetic fixtures and fake service responses, with no live generation
or printer operations. Real-world workflow evidence reaches device acceptance
and preparation; physical finish and support removal are separate evidence.

MIT covers original repository content. Third-party models/services/software
and user images retain their own terms; see [NOTICE](NOTICE.md). Model weights,
private jobs and printer credentials are not included. Contributions should
include reproducible, redacted evidence and passing offline tests.
