#!/usr/bin/env python3
"""Layer-by-layer timelapse of a running Bambu print, from the printer's own camera.

capture  Polls status and saves one camera frame per new layer (or per interval)
         until the print finishes, fails or stops. Read-only: it uses the same
         telemetry and camera reads as bambu_read.py and never starts, pauses or
         moves anything.
render   Turns the captured frames into an MP4 (ffmpeg) or GIF (Pillow), with a
         layer/progress caption and optional intro and outro images, e.g. the
         reference image the model was made from and a photo of the finished part.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

DONE_STATES = {"FINISH", "FAILED", "IDLE"}
ACTIVE_STATES = {"PREPARE", "RUNNING", "PAUSE", "SLICING"}


def load_reader_module():
    spec = importlib.util.spec_from_file_location("bambu_read_for_timelapse", Path(__file__).with_name("bambu_read.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LanCamera:
    """Status and camera reads over the printer's legacy LAN endpoints (see bambu-lan.md)."""

    def __init__(self, profile_path, studio_config=None):
        self.read = load_reader_module()
        profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        if studio_config:
            config_path = Path(studio_config)
        elif os.environ.get("APPDATA"):
            config_path = Path(os.environ["APPDATA"]) / "BambuStudio/BambuStudio.conf"
        else:
            raise ValueError("Pass --studio-config on platforms without Windows APPDATA discovery")
        config = json.JSONDecoder().raw_decode(config_path.read_text(encoding="utf-8-sig"))[0]
        self.serial, self.host = profile["serial"], profile["host"]
        self._code = config["access_code"][self.serial]

    def _connect(self, port, action):
        # Same experimental private-LAN trust as bambu_read.py: encrypted, host not verified.
        context = ssl._create_unverified_context()
        with socket.create_connection((self.host, port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=self.host) as conn:
                return action(conn)

    def status(self):
        return self._connect(8883, lambda conn: self.read.status(conn, self.serial, self._code))["state"]

    def frame(self, path):
        self._connect(6000, lambda conn: self.read.camera(conn, self._code, path))


def utc():
    return datetime.now(timezone.utc).isoformat()


def capture(reader, out, *, every_layer=True, interval=60.0, poll=10.0, max_hours=24.0,
            sleep=time.sleep, clock=time.monotonic):
    """Save frames into out/frames and one JSON line per frame into out/frames.jsonl."""
    out = Path(out)
    frames = out / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    log = out / "frames.jsonl"
    start, last_layer, last_shot, seen_active = clock(), None, None, False
    count = len(list(frames.glob("*.jpg")))
    errors = 0
    while clock() - start < max_hours * 3600:
        try:
            state = reader.status()
            errors = 0
        except Exception as exc:  # A dropped connection should not end a long capture.
            errors += 1
            if errors >= 5:
                return {"frames": count, "stopped": "status_unavailable", "error_type": type(exc).__name__}
            sleep(poll)
            continue
        phase = state.get("gcode_state")
        layer = state.get("layer_num")
        seen_active = seen_active or phase in ACTIVE_STATES
        due = (layer is not None and layer != last_layer) if every_layer else (
            last_shot is None or clock() - last_shot >= interval)
        if phase in ACTIVE_STATES and due:
            path = frames / f"{count:05d}.jpg"
            try:
                reader.frame(path)
            except Exception:
                path.unlink(missing_ok=True)
            else:
                record = {"index": count, "file": path.name, "captured_at": utc(), "gcode_state": phase,
                          "layer": layer, "total_layers": state.get("total_layer_num"),
                          "percent": state.get("mc_percent"), "task_id": state.get("task_id"),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
                last_layer, last_shot = layer, clock()
        if seen_active and phase in DONE_STATES:
            # One last look at the finished part.
            path = frames / f"{count:05d}.jpg"
            try:
                reader.frame(path)
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"index": count, "file": path.name, "captured_at": utc(),
                                             "gcode_state": phase, "layer": layer, "final": True,
                                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}) + "\n")
                count += 1
            except Exception:
                path.unlink(missing_ok=True)
            return {"frames": count, "stopped": phase.lower()}
        sleep(poll)
    return {"frames": count, "stopped": "max_hours"}


def read_log(out):
    rows = []
    log = Path(out) / "frames.jsonl"
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return [row for row in rows if (Path(out) / "frames" / row["file"]).is_file()]


def _font(size):
    from PIL import ImageFont
    for name in ("DejaVuSans.ttf", "Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
                 "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def compose(path, size, caption=None):
    """Fit an image into `size` on black, with an optional caption bar."""
    from PIL import Image, ImageDraw
    canvas = Image.new("RGB", size, "black")
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail(size)
        canvas.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2))
    if caption:
        draw = ImageDraw.Draw(canvas)
        font = _font(max(14, size[1] // 24))
        box = draw.textbbox((0, 0), caption, font=font)
        pad = size[1] // 60 + 4
        draw.rectangle((0, size[1] - (box[3] - box[1]) - 3 * pad, size[0], size[1]), fill=(0, 0, 0))
        draw.text((pad, size[1] - (box[3] - box[1]) - 2 * pad), caption, fill=(255, 255, 255), font=font)
    return canvas


def caption_for(row):
    if row.get("final"):
        return "Finished"
    layer, total, percent = row.get("layer"), row.get("total_layers"), row.get("percent")
    parts = []
    if layer is not None:
        parts.append(f"Layer {layer}" + (f" / {total}" if total else ""))
    if percent is not None:
        parts.append(f"{percent}%")
    return "  ·  ".join(parts) or None


def render(out, target, *, fps=24, size=(1280, 720), intro=None, outro=None, hold_seconds=2.0, captions=True):
    rows = read_log(out)
    if not rows:
        raise ValueError("No captured frames to render")
    target = Path(target)
    if target.exists():
        raise ValueError("Refusing to overwrite an existing video")
    sequence = []
    hold = max(1, round(hold_seconds * fps))
    if intro:
        sequence += [(Path(intro), "The idea")] * hold
    for row in rows:
        sequence.append((Path(out) / "frames" / row["file"], caption_for(row) if captions else None))
    sequence += [sequence[-1]] * (hold - 1)
    if outro:
        sequence += [(Path(outro), "The print")] * hold
    with tempfile.TemporaryDirectory() as temp:
        stills = []
        cache = {}
        for index, (path, caption) in enumerate(sequence):
            key = (str(path), caption)
            if key not in cache:
                cache[key] = compose(path, size, caption)
            still = Path(temp) / f"{index:06d}.png"
            cache[key].save(still)
            stills.append(still)
        if target.suffix.lower() == ".gif":
            from PIL import Image
            images = [Image.open(still) for still in stills]
            images[0].save(target, save_all=True, append_images=images[1:], duration=round(1000 / fps), loop=0)
            for image in images:
                image.close()
        elif target.suffix.lower() == ".mp4":
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise ValueError("ffmpeg is not installed; render a .gif instead")
            command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-framerate", str(fps),
                       "-i", str(Path(temp) / "%06d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                       "-movflags", "+faststart", str(target)]
            subprocess.run(command, check=True)
        else:
            raise ValueError("Output must end in .mp4 or .gif")
    return {"video": str(target.resolve()), "frames": len(rows), "video_frames": len(sequence),
            "seconds": round(len(sequence) / fps, 2)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("capture", help="Save camera frames while a print runs (contacts the configured printer)")
    p.add_argument("--profile", required=True, help="printer.local.json with host and serial")
    p.add_argument("--studio-config", help="Bambu Studio configuration holding the LAN access code")
    p.add_argument("--out", required=True, help="Capture directory (frames/ and frames.jsonl)")
    p.add_argument("--interval", type=float, help="Seconds between frames instead of one per layer")
    p.add_argument("--poll", type=float, default=10.0, help="Seconds between status checks (default %(default)s)")
    p.add_argument("--max-hours", type=float, default=24.0)
    p = sub.add_parser("render", help="Make an MP4 or GIF from captured frames (offline)")
    p.add_argument("--capture", required=True, help="Capture directory")
    p.add_argument("--output", required=True, help="New .mp4 or .gif file")
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--size", nargs=2, type=int, default=[1280, 720], metavar=("W", "H"))
    p.add_argument("--intro", help="Image shown first, e.g. the reference image")
    p.add_argument("--outro", help="Image shown last, e.g. a photo of the finished part")
    p.add_argument("--no-captions", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            if not 1 <= args.poll <= 600 or not 0 < args.max_hours <= 72 or (args.interval is not None and args.interval < 1):
                raise ValueError("poll must be 1..600 s, max-hours 0..72 and interval at least 1 s")
            reader = LanCamera(args.profile, args.studio_config)
            result = capture(reader, args.out, every_layer=args.interval is None,
                             interval=args.interval or 60.0, poll=args.poll, max_hours=args.max_hours)
        else:
            if not 1 <= args.fps <= 60 or min(args.size) < 64 or max(args.size) > 3840:
                raise ValueError("fps must be 1..60 and size 64..3840 pixels")
            result = render(args.capture, args.output, fps=args.fps, size=tuple(args.size),
                            intro=args.intro, outro=args.outro, captions=not args.no_captions)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        # Keep credentials and network details out of the output.
        print(json.dumps({"error_type": type(exc).__name__, "error": str(exc) if isinstance(exc, ValueError) else None,
                          "operation": args.command}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
