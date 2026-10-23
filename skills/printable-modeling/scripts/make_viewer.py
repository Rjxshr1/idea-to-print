#!/usr/bin/env python3
"""Make a shareable 3D turntable / AR page for a model.

Converts a millimetre STL (or GLB) to a real-size GLB and writes one HTML page
using <model-viewer>: the model turns slowly, can be dragged and zoomed, shows
its printed size, and on a phone can be placed on a table at real size in AR
(Android Scene Viewer / WebXR, iOS Quick Look). AR needs the page served over
HTTPS; opening the file locally still gives the 3D turntable.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path
import sys

MODEL_VIEWER = "https://cdn.jsdelivr.net/npm/@google/model-viewer@4.3.1/dist/model-viewer.min.js"
MAX_EMBED_BYTES = 12 * 1024 * 1024  # Above this, write the GLB next to the page instead.


def real_size_glb(source, color):
    """Load a millimetre mesh and return (GLB bytes, stats). glTF is metres and Y-up."""
    import numpy as np
    import trimesh
    loaded = trimesh.load(str(source), force="mesh", process=False)
    if not isinstance(loaded, trimesh.Trimesh) or not len(loaded.faces):
        raise ValueError("Expected one nonempty mesh")
    mesh = loaded.copy()
    mesh.merge_vertices()  # STL stores each face's corners separately.
    low, high = mesh.bounds
    size_mm = (high - low).tolist()
    # Stand the model on the floor, centred, then convert Z-up millimetres to Y-up metres.
    mesh.apply_translation([-(low[0] + high[0]) / 2, -(low[1] + high[1]) / 2, -low[2]])
    mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    mesh.apply_scale(0.001)
    rgba = [int(color[i:i + 2], 16) for i in (1, 3, 5)] + [255]
    mesh.visual = trimesh.visual.ColorVisuals(mesh, face_colors=np.tile(rgba, (len(mesh.faces), 1)))
    data = trimesh.exchange.gltf.export_glb(trimesh.Scene(mesh))
    stats = {"size_mm": [round(x, 1) for x in size_mm], "faces": int(len(mesh.faces)),
             "volume_cm3": round(abs(float(mesh.volume)) * 1e6, 1) if mesh.is_watertight else None}
    return data, stats


def page(title, model_src, stats, poster_color):
    width, depth, height = stats["size_mm"]
    facts = [f"{width:g} × {depth:g} × {height:g} mm", f"{stats['faces']:,} faces"]
    if stats["volume_cm3"] is not None:
        facts.append(f"{stats['volume_cm3']:g} cm³")
    facts_html = "".join(f"<li>{html.escape(fact)}</li>" for fact in facts)
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{safe_title}</title>
<script type="module" src="{MODEL_VIEWER}"></script>
<style>
  /* Full-bleed stage; the model is the page. Facts float top-left, AR action bottom-centre. */
  :root {{ --bg: #eeece6; --stage: #e4e1d8; --fg: #1f1e1b; --muted: #66645d; --card: rgba(250,249,245,.82);
    --accent: {poster_color}; --body: system-ui, -apple-system, "Segoe UI", sans-serif; }}
  @media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg: #151513; --stage: #1d1c1a;
    --fg: #f0eee8; --muted: #a29f97; --card: rgba(29,28,26,.82); color-scheme: dark; }} }}
  :root[data-theme="dark"] {{ --bg: #151513; --stage: #1d1c1a; --fg: #f0eee8; --muted: #a29f97;
    --card: rgba(29,28,26,.82); color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; height: 100%; }}
  body {{ background: radial-gradient(circle at 50% 40%, var(--stage), var(--bg) 70%); background-color: var(--bg);
    color: var(--fg); font: 15px/1.4 var(--body); }}
  model-viewer {{ display: block; width: 100%; height: 100%; --poster-color: transparent; }}
  .card {{ position: fixed; left: max(16px, env(safe-area-inset-left, 0px)); top: calc(16px + env(safe-area-inset-top, 0px));
    max-width: calc(100% - 32px); padding: 12px 16px; background: var(--card); backdrop-filter: blur(10px);
    border-radius: 12px; }}
  h1 {{ margin: 0 0 4px; font-size: 18px; font-weight: 650; text-wrap: balance; }}
  ul {{ display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 0; padding: 0; list-style: none; color: var(--muted);
    font-variant-numeric: tabular-nums; }}
  .ar {{ position: fixed; left: 50%; bottom: calc(24px + env(safe-area-inset-bottom, 0px)); transform: translateX(-50%);
    border: 0; padding: 12px 20px; border-radius: 999px; background: var(--fg); color: var(--bg); font: inherit;
    font-weight: 600; cursor: pointer; }}
  .ar:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; }}
  .hint {{ position: fixed; right: max(16px, env(safe-area-inset-right, 0px)); bottom: calc(16px + env(safe-area-inset-bottom, 0px));
    color: var(--muted); font-size: 13px; }}
  @media (max-width: 520px) {{ .hint {{ display: none; }} }}
  @media (prefers-reduced-motion: reduce) {{ model-viewer {{ --auto-rotate: 0; }} }}
</style>
</head>
<body>
<model-viewer src="{model_src}" alt="{safe_title}" camera-controls auto-rotate rotation-per-second="18deg"
  auto-rotate-delay="0" interaction-prompt="none" shadow-intensity="1" exposure="1.05"
  environment-image="neutral" ar ar-modes="webxr scene-viewer quick-look" ar-scale="fixed"
  camera-orbit="35deg 70deg auto">
  <button slot="ar-button" class="ar">View in your space</button>
</model-viewer>
<div class="card"><h1>{safe_title}</h1><ul>{facts_html}</ul></div>
<div class="hint">Drag to turn · pinch or scroll to zoom</div>
</body>
</html>
"""


def make(source, output, *, title=None, color="#EDEBE6", external=False):
    source, output = Path(source), Path(output)
    if output.suffix.lower() != ".html":
        raise ValueError("Output must be an .html file")
    if output.exists():
        raise ValueError("Refusing to overwrite an existing page")
    if not (len(color) == 7 and color[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in color[1:])):
        raise ValueError("Colour must look like #RRGGBB")
    data, stats = real_size_glb(source, color)
    glb = None
    if external or len(data) > MAX_EMBED_BYTES:
        glb = output.with_suffix(".glb")
        if glb.exists():
            raise ValueError("Refusing to overwrite an existing GLB")
        glb.write_bytes(data)
        src = glb.name
    else:
        src = "data:model/gltf-binary;base64," + base64.b64encode(data).decode("ascii")
    output.write_text(page(title or source.stem, src, stats, color), encoding="utf-8")
    return {"page": str(output.resolve()), "glb": str(glb.resolve()) if glb else None,
            "embedded": glb is None, "glb_bytes": len(data), **stats}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="Millimetre STL, or GLB")
    parser.add_argument("--output", required=True, help="New .html page")
    parser.add_argument("--title", help="Page title (default: model file name)")
    parser.add_argument("--color", default="#EDEBE6", help="Model colour, #RRGGBB (default: %(default)s)")
    parser.add_argument("--external-glb", action="store_true",
                        help="Write the GLB next to the page instead of embedding it")
    args = parser.parse_args(argv)
    try:
        result = make(args.model, args.output, title=args.title, color=args.color, external=args.external_glb)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
