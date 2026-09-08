"""Create a local job from an uploaded image, preserving original bytes.

No uploads, image generation, inference, slicing or printer operations.
"""
import argparse
import datetime
import hashlib
import json
import math
from pathlib import Path

FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 40_000_000


def prepare(image, job, brief="", target_mm=None):
    from PIL import Image
    source = Path(image).expanduser().resolve(strict=True)
    root = Path(job).expanduser().resolve()
    if root.exists():
        raise FileExistsError("Job already exists; resume it instead of replacing its selection")
    if target_mm is not None and (not math.isfinite(target_mm) or target_mm <= 0):
        raise ValueError("Target size must be finite and positive")
    if source.stat().st_size > MAX_BYTES:
        raise ValueError("Local intake limit is 64 MiB; provide a smaller reference")
    data = source.read_bytes()
    import io
    with Image.open(io.BytesIO(data)) as im:
        image_format = im.format
        if image_format not in FORMATS:
            raise ValueError("Supported inputs are PNG, JPEG and WebP")
        if getattr(im, "n_frames", 1) != 1:
            raise ValueError("Select one still frame before importing an animated image")
        if im.width * im.height > MAX_PIXELS:
            raise ValueError("Local intake limit is 40 megapixels; provide a smaller reference")
        dimensions = [im.width, im.height]
        orientation = im.getexif().get(274, 1)
        im.load()
    digest = hashlib.sha256(data).hexdigest()
    selected = "source/selected" + FORMATS[image_format]
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record = {
        "schema_version": 1, "name": root.name, "state": "selected",
        "brief": {"user_text": brief, "target_mm": {"longest_axis_including_base": target_mm}},
        "input_kind": "uploaded_image", "candidates": [],
        "selected": {"id": "uploaded-01", "path": selected, "sha256": digest,
                     "recorded_at": timestamp, "format": image_format,
                     "pixels": dimensions, "exif_orientation": orientation},
        "artifacts": {"original_image": selected}, "verification": {},
        "authorization": {"model": None, "print": None},
        "physical_confirmation": {"plate_cleared": None,
                                  "filament_remaining": "unknown",
                                  "insufficient_filament_risk_accepted": False},
        "dispatch_attempts": [], "feedback": {"physical_tested": False},
    }
    root.mkdir(parents=True, exist_ok=False)
    for folder in ("source", "work", "outputs"):
        (root / folder).mkdir()
    with (root / selected).open("xb") as out:
        out.write(data)
    with (root / "job.json").open("x", encoding="utf-8") as out:
        json.dump(record, out, ensure_ascii=False, indent=2)
        out.write("\n")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--brief", default="")
    parser.add_argument("--target-mm", type=float)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.image, args.job, args.brief, args.target_mm),
                         ensure_ascii=False, indent=2))
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(2, f"Image intake stopped: {exc}\n")


if __name__ == "__main__":
    main()
