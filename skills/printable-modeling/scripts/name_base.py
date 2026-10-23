#!/usr/bin/env python3
"""Put a figurine on a name plate: a rounded base with raised text in front of it.

The base is extended forward so the name (and an optional second line, such as
a date) sits on its top surface, where FDM prints lettering most cleanly. Text
comes from a real font's outlines (Chinese and other scripts work with a font
that has those glyphs), and figurine, base and text are merged into one
watertight solid. Run the geometry gate on the result as for any other model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Fonts with CJK coverage tried in order when --font is not given.
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc", "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def find_font(text, explicit=None):
    from fontTools.ttLib import TTFont
    candidates = [explicit] if explicit else FONT_CANDIDATES
    for path in candidates:
        if not path or not Path(path).is_file():
            continue
        font = TTFont(path, fontNumber=0, lazy=True)
        cmap = font.getBestCmap() or {}
        missing = sorted({char for char in text if not char.isspace() and ord(char) not in cmap})
        if not missing:
            return path, font
        if explicit:
            raise ValueError(f"Font has no glyphs for: {''.join(missing)}")
    raise ValueError("No installed font covers this text; pass --font with a font that does")


class FlattenPen:
    """Collects glyph contours as polygons, flattening curves into short segments."""

    def __init__(self, glyph_set, steps=8):
        from fontTools.pens.basePen import BasePen

        outer = self

        class Pen(BasePen):
            def _moveTo(self, point):
                outer.contours.append([point])

            def _lineTo(self, point):
                outer.contours[-1].append(point)

            def _curveToOne(self, p1, p2, p3):
                p0 = self._getCurrentPoint()
                for i in range(1, steps + 1):
                    t = i / steps
                    u = 1 - t
                    outer.contours[-1].append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                                               u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))

            def _qCurveToOne(self, p1, p2):
                p0 = self._getCurrentPoint()
                for i in range(1, steps + 1):
                    t = i / steps
                    u = 1 - t
                    outer.contours[-1].append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                                               u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))

            def _closePath(self):
                pass

        self.contours = []
        self.pen = Pen(glyph_set)


def text_outline(text, font, size_mm):
    """Polygons (in mm) for one line of text, left edge at x=0, baseline at y=0."""
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    units = font["head"].unitsPerEm
    scale = size_mm / units
    polygons, cursor = [], 0.0
    for char in text:
        name = cmap.get(ord(char))
        if name is None:
            cursor += units * 0.3
            continue
        pen = FlattenPen(glyph_set)
        glyph_set[name].draw(pen.pen)
        for contour in pen.contours:
            if len(contour) >= 3:
                polygons.append([((x + cursor) * scale, y * scale) for x, y in contour])
        cursor += font["hmtx"][name][0]
    return polygons


def text_solid(lines, font, height_mm, width_mm, depth_mm, stroke_boost=0.25, gap_ratio=0.35):
    """Raised text for up to two lines, fitted into width x height, centred at the origin."""
    import manifold3d
    sections = []
    sizes = [1.0] + [0.7] * (len(lines) - 1)
    for line, size in zip(lines, sizes):
        outline = text_outline(line, font, size)
        if not outline:
            raise ValueError(f"Nothing to draw for {line!r}")
        sections.append(manifold3d.CrossSection(outline, manifold3d.FillRule.NonZero))
    boxes = [section.bounds() for section in sections]  # (min_x, min_y, max_x, max_y)
    widths = [box[2] - box[0] for box in boxes]
    heights = [box[3] - box[1] for box in boxes]
    total_height = sum(heights) + gap_ratio * heights[0] * (len(lines) - 1)
    scale = min(width_mm / max(widths), height_mm / total_height)
    placed, top = [], total_height * scale / 2
    for section, box, w, h in zip(sections, boxes, widths, heights):
        moved = section.translate((-box[0] - w / 2, -box[3])).scale((scale, scale)).translate((0, top))
        placed.append(moved)
        top -= (h + gap_ratio * heights[0]) * scale
    combined = placed[0]
    for section in placed[1:]:
        combined = combined + section
    if stroke_boost > 0:
        # Thin strokes and dots do not print with a 0.4 mm nozzle; grow every outline a little.
        combined = combined.offset(stroke_boost, manifold3d.JoinType.Round, circular_segments=16)
    # Drop the very short segments that curve flattening and offsetting leave behind.
    combined = combined.simplify(0.01)
    return combined.extrude(depth_mm), scale * heights[0]


def rounded_plate(width, length, height, radius, segments=96):
    import manifold3d
    radius = min(radius, width / 2, length / 2)
    plate = manifold3d.CrossSection.square((width - 2 * radius, length - 2 * radius), center=True)
    outline = plate.offset(radius, manifold3d.JoinType.Round, circular_segments=segments) if radius > 0 else plate
    return outline.extrude(height)


def build(model, output, name, *, subtitle=None, font_path=None, base_height=4.0, margin=6.0,
          text_depth=0.8, text_height=None, sink=0.4, stroke_boost=0.25, label_min=0.8):
    import manifold3d
    import numpy as np
    import trimesh
    output = Path(output)
    if output.exists():
        raise ValueError("Refusing to overwrite an existing model")
    if not name.strip():
        raise ValueError("Name must not be empty")
    for label, value, low, high in (("base height", base_height, 1.2, 30), ("margin", margin, 1, 50),
                                    ("text depth", text_depth, 0.4, 5), ("sink", sink, 0, 5),
                                    ("stroke boost", stroke_boost, 0, 1)):
        if not low <= value <= high:
            raise ValueError(f"{label} must be between {low} and {high} mm")
    lines = [name.strip()] + ([subtitle.strip()] if subtitle and subtitle.strip() else [])
    path, font = find_font("".join(lines), font_path)
    figure = trimesh.load(str(model), force="mesh", process=False)
    if not isinstance(figure, trimesh.Trimesh) or not len(figure.faces):
        raise ValueError("Expected one nonempty mesh")
    figure = figure.copy()
    figure.merge_vertices()
    low, high = figure.bounds
    footprint_w, footprint_d = high[0] - low[0], high[1] - low[1]
    plate_w = max(footprint_w + 2 * margin, 40.0)
    label_h = text_height or max(8.0, min(20.0, plate_w * 0.22)) * (1.5 if len(lines) > 1 else 1.0)
    label_band = label_h + margin
    plate_d = footprint_d + 2 * margin + label_band
    # The figurine stands on the back of the plate; the label band is at the front (-Y), facing the viewer.
    shift = [-(low[0] + high[0]) / 2, plate_d / 2 - margin - high[1], base_height - sink - low[2]]
    figure.apply_translation(shift)
    plate = rounded_plate(plate_w, plate_d, base_height, radius=min(8.0, margin + 2))
    text, cap_height = text_solid(lines, font, label_h * 0.9, plate_w - 2 * margin, text_depth + 0.2, stroke_boost)
    text = text.translate((0, -plate_d / 2 + label_band / 2, base_height - 0.2))
    label_box = text.bounding_box()
    figure_solid = manifold3d.Manifold(manifold3d.Mesh(vert_properties=np.asarray(figure.vertices, dtype=np.float32),
                                                       tri_verts=np.asarray(figure.faces, dtype=np.uint32)))
    if figure_solid.status() != manifold3d.Error.NoError:
        raise ValueError(f"Figurine is not a closed solid ({figure_solid.status()}); repair it first")
    # Merge near-coincident vertices so the STL (single precision) stays manifold.
    combined = (plate + text + figure_solid).simplify(0.005)
    mesh = combined.to_mesh()
    result = trimesh.Trimesh(mesh.vert_properties[:, :3], mesh.tri_verts, process=False)
    lift = -result.bounds[0][2]
    result.apply_translation([0, 0, lift])
    result.export(output)
    # Lettering is a detail: judge it by the detail rule instead of the general wall rule.
    region = {"id": "name-label", "bounds_mm": [[round(label_box[0] - 0.5, 2), round(label_box[1] - 0.5, 2), round(base_height - 0.5 + lift, 2)],
                                               [round(label_box[3] + 0.5, 2), round(label_box[4] + 0.5, 2), round(label_box[5] + 0.5 + lift, 2)]],
              "rules": {"wall_min_mm": label_min, "connection_min_mm": label_min}}
    return {"model": str(output.resolve()), "font": path, "lines": lines,
            "plate_mm": [round(plate_w, 1), round(plate_d, 1), base_height],
            "text_cap_height_mm": round(cap_height, 1), "text_raised_mm": text_depth,
            "size_mm": [round(x, 1) for x in result.extents], "faces": int(len(result.faces)),
            "watertight": bool(result.is_watertight),
            "suggested_gate_region": region}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", required=True, help="Figurine STL in millimetres, standing on Z")
    parser.add_argument("--output", required=True, help="New STL with the name plate")
    parser.add_argument("--name", required=True, help="Text on the plate, e.g. the pet's name")
    parser.add_argument("--subtitle", help="Optional smaller second line, e.g. a date")
    parser.add_argument("--font", help="Font file with glyphs for the text (default: an installed CJK font)")
    parser.add_argument("--base-height", type=float, default=4.0)
    parser.add_argument("--margin", type=float, default=6.0, help="Plate border around the figurine and text")
    parser.add_argument("--text-depth", type=float, default=0.8, help="How far the letters stand up")
    parser.add_argument("--stroke-boost", type=float, default=0.25,
                        help="Grow letter outlines by this many mm so thin strokes print (default %(default)s)")
    parser.add_argument("--label-min-mm", type=float, default=0.8,
                        help="Wall/connection minimum suggested for the lettering region (default %(default)s)")
    args = parser.parse_args(argv)
    try:
        result = build(args.model, args.output, args.name, subtitle=args.subtitle, font_path=args.font,
                       base_height=args.base_height, margin=args.margin, text_depth=args.text_depth,
                       stroke_boost=args.stroke_boost, label_min=args.label_min_mm)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
