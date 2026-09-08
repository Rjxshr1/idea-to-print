"""Submit one recorded Hunyuan3D draft, or recover its cached local GLB.

No slicing, printer operations, auth tokens, or automatic generation retries.
The public demo/API may change; --describe-api is a read-only capability probe.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct

SERVICE = "https://tencent-hunyuan3d-2-1.hf.space"
API = "/shape_generation"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def glb_info(path):
    with path.open("rb") as f:
        header = f.read(12)
    if len(header) != 12:
        raise ValueError("Truncated GLB header")
    magic, version, size = struct.unpack("<4sII", header)
    if magic != b"glTF" or version != 2 or size != path.stat().st_size or size <= 20:
        raise ValueError("Expected a complete GLB v2 file; inspect cached result")
    return {"bytes": size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def result_path(result):
    item = result[0] if isinstance(result, (list, tuple)) and result else result
    while isinstance(item, dict) and item.get("__type__") == "update":
        item = item.get("value")
    if isinstance(item, dict):
        item = item.get("path")
    if not isinstance(item, str) or "://" in item:
        raise ValueError("No downloaded local GLB in response; inspect cached result, do not regenerate")
    path = Path(item).expanduser()
    if not path.is_file():
        raise FileNotFoundError("Returned local GLB is unavailable; locate the original download before retrying")
    return path


def materialize(result, destination):
    source = result_path(result)
    info = glb_info(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if glb_info(destination) != info:
            raise FileExistsError("Destination already exists with different geometry")
        return info
    # x prevents accidentally replacing another model. Interrupted copies are
    # left visible for diagnosis, never treated as completed or auto-replaced.
    with destination.open("xb") as out, source.open("rb") as inp:
        import shutil
        shutil.copyfileobj(inp, out)
    if glb_info(destination) != info:
        raise ValueError("Copied GLB differs from cached result")
    return info


def get_client(downloads=None):
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
    from gradio_client import Client
    return Client(SERVICE, token=False, verbose=False, analytics_enabled=False,
                  download_files=str(downloads) if downloads else False,
                  httpx_kwargs={"timeout": 600})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path)
    parser.add_argument("--input", help="Selected/cleaned reference path relative to the job")
    parser.add_argument("--attempt", help="Unique lowercase attempt ID; never reuse to submit")
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--describe-api", action="store_true")
    parser.add_argument("--recover", action="store_true", help="Recover cached response; no network or new generation")
    args = parser.parse_args(argv)
    if args.describe_api:
        if args.recover:
            parser.error("--describe-api and --recover are mutually exclusive")
        print(json.dumps(get_client().view_api(print_info=False, return_format="dict"), ensure_ascii=False))
        return
    if not args.job or not args.attempt or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", args.attempt):
        parser.error("--job and a unique lowercase --attempt are required")
    root = args.job.resolve(strict=True)
    work = root / "work"
    work.mkdir(exist_ok=True)
    record = work / f"shape-request-{args.attempt}.json"
    cached = work / f"shape-result-{args.attempt}.json"
    destination = root / "source" / f"shape-{args.attempt}.glb"
    if args.recover:
        details = json.loads(record.read_text(encoding="utf-8"))
        if details.get("attempt") != args.attempt:
            raise ValueError("Request record does not match attempt")
        result = json.loads(cached.read_text(encoding="utf-8"))
        info = materialize(result, destination)
        details.update(state="completed", output=str(destination.relative_to(root)),
                       recovered_at=now(), **info)
        details.setdefault("events", []).append({"at": now(), "event": "recovered_cached_result_without_generation"})
        save(record, details)
        print(json.dumps(details, ensure_ascii=False))
        return
    if not args.input:
        parser.error("--input is required for a new generation")
    reference = (root / args.input).resolve(strict=True)
    if not reference.is_relative_to(root) or not reference.is_file():
        parser.error("Copy the selected reference into this job before submitting")
    if cached.exists() or destination.exists():
        raise FileExistsError("Existing attempt artifacts; inspect/recover instead of resubmitting")
    details = {"attempt": args.attempt, "service": SERVICE, "api": API,
               "auth": "anonymous", "state": "created", "created_at": now(),
               "input": str(reference.relative_to(root)),
               "input_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
               "params": {"steps": 30, "guidance_scale": 5, "seed": args.seed,
                          "octree_resolution": 512, "num_chunks": 8000,
                          "check_box_rembg": True, "randomize_seed": False},
               "events": []}
    with record.open("x", encoding="utf-8") as f:
        json.dump(details, f, ensure_ascii=False, indent=2)
    submitted = False
    result_received = False
    try:
        client = get_client(work / f"hf-downloads-{args.attempt}")
        from gradio_client import handle_file
        data = client.view_api(print_info=False, return_format="dict")
        endpoint = data.get("named_endpoints", {}).get(API)
        if not endpoint:
            raise RuntimeError("Observed /shape_generation API is unavailable; inspect service before proceeding")
        names = {p.get("parameter_name") for p in endpoint.get("parameters", [])}
        required = set(details["params"]) | {"image", "mv_image_front", "mv_image_back", "mv_image_left", "mv_image_right"}
        if not required.issubset(names):
            raise RuntimeError("Service schema changed; adapt only after inspecting the returned API")
        details.update(state="submission_intent", submitted_at=now())
        save(record, details)
        submitted = True
        result = client.predict(image=handle_file(str(reference)),
                                mv_image_front=None, mv_image_back=None,
                                mv_image_left=None, mv_image_right=None,
                                **details["params"], api_name=API)
        result_received = True
        save(cached, result)
        details.update(state="result_received", cached_result=str(cached.relative_to(root)))
        save(record, details)
        info = materialize(result, destination)
        details.update(state="completed", output=str(destination.relative_to(root)), **info)
    except Exception as exc:
        details["state"] = "result_needs_recovery" if result_received else ("submission_uncertain" if submitted else "not_submitted")
        details["events"].append({"at": now(), "error_type": type(exc).__name__, "error": str(exc)})
        raise
    finally:
        details["updated_at"] = now()
        save(record, details)
        print(json.dumps(details, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
