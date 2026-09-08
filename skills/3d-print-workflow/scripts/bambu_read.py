#!/usr/bin/env python3
"""Read Bambu LAN status/camera with existing Studio credentials; never starts a job.

Experimental legacy private-LAN adapter: accepts printer-issued TLS certificates
without verification. See references/bambu-lan.md before use.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import sys
import time
import uuid


def string(value):
    value = value.encode()
    return struct.pack("!H", len(value)) + value


def packet(kind, body):
    length, encoded = len(body), bytearray()
    while True:
        b, length = length % 128, length // 128
        encoded.append(b | (128 if length else 0))
        if not length:
            return bytes([kind]) + encoded + body


def exact(conn, size):
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size-len(data))
        if not chunk:
            raise EOFError("Connection closed")
        data.extend(chunk)
    return bytes(data)


def receive(conn):
    kind, length = exact(conn, 1)[0], 0
    for i in range(4):
        b = exact(conn, 1)[0]
        length += (b & 127) * 128**i
        if b < 128:
            if length > 2_000_000:
                raise ValueError("Oversized MQTT packet")
            return kind, exact(conn, length)
    raise ValueError("Invalid MQTT length")


def status(conn, serial, code):
    conn.sendall(packet(16, string("MQTT") + bytes([4, 194]) + struct.pack("!H", 20)
                        + string("codex-read-" + uuid.uuid4().hex[:8]) + string("bblp") + string(code)))
    kind, body = receive(conn)
    if kind != 32 or body != b"\x00\x00":
        raise RuntimeError("MQTT authentication failed")
    conn.sendall(packet(130, b"\x00\x01" + string(f"device/{serial}/report") + b"\x00"))
    # pushall requests telemetry only. No print, pause, calibration, or motion commands exist here.
    payload = json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}).encode()
    conn.sendall(packet(48, string(f"device/{serial}/request") + payload))
    deadline, data = time.monotonic() + 12, {}
    required = {"gcode_state", "print_error", "hms", "vt_tray"}
    while time.monotonic() < deadline:
        conn.settimeout(max(.1, deadline-time.monotonic()))
        try:
            kind, body = receive(conn)
        except socket.timeout:
            break
        if kind >> 4 == 3:
            size = int.from_bytes(body[:2], "big")
            offset = 2 + size + (2 if (kind >> 1) & 3 else 0)
            report = json.loads(body[offset:])
            if isinstance(report.get("print"), dict):
                data.update(report["print"])
            if required <= data.keys():
                break
    if not data.get("gcode_state"):
        raise RuntimeError("No usable printer state received")
    keys = ["gcode_state", "subtask_name", "gcode_file", "task_id", "subtask_id", "mc_percent",
            "mc_remaining_time", "layer_num", "total_layer_num", "print_error", "hms", "vt_tray",
            "bed_temper", "bed_target_temper", "nozzle_temper", "nozzle_target_temper", "stg_cur"]
    return {"sampled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "state": {key: data.get(key) for key in keys},
            "missing_preflight_fields": sorted(required - data.keys()),
            "filament_remaining_is_unverified": True}


def camera(conn, code, output):
    conn.sendall(struct.pack("<IIII32s32s", 0x40, 0x3000, 0, 0, b"bblp", code.encode()))
    data, deadline = bytearray(), time.monotonic() + 12
    while time.monotonic() < deadline and len(data) < 8_000_000:
        conn.settimeout(max(.1, deadline-time.monotonic()))
        chunk = conn.recv(65536)
        if not chunk:
            break
        data.extend(chunk)
        start = data.find(b"\xff\xd8")
        end = data.find(b"\xff\xd9", max(0, start))
        if start >= 0 and end > start:
            path = Path(output)
            path.write_bytes(data[start:end+2])
            return {"camera_file": str(path.resolve())}
    raise RuntimeError("No complete camera frame received")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--studio-config", help="Override credential file path; credential values are never arguments")
    parser.add_argument("--camera", help="Save one JPEG instead of reading status")
    parser.add_argument("--out", help="Save status JSON")
    args = parser.parse_args()
    if not args.studio_config and not os.environ.get("APPDATA"):
        parser.error("Pass --studio-config on platforms without Windows APPDATA discovery")
    try:
        profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
        cfg_path = Path(args.studio_config) if args.studio_config else Path(os.environ["APPDATA"]) / "BambuStudio/BambuStudio.conf"
        config = json.JSONDecoder().raw_decode(cfg_path.read_text(encoding="utf-8-sig"))[0]
        serial, host = profile["serial"], profile["host"]
        code = config["access_code"][serial]
        # Experimental legacy private-LAN trust: encryption without host identity
        # verification. Use only a verified printer on a trusted private LAN.
        context = ssl._create_unverified_context()
        port = 6000 if args.camera else 8883
        with socket.create_connection((host, port), timeout=5) as raw:
            with context.wrap_socket(raw, server_hostname=host) as conn:
                result = camera(conn, code, args.camera) if args.camera else status(conn, serial, code)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(text)
    except Exception as exc:
        # Avoid dumping config, credentials or network exception arguments.
        print(json.dumps({"error_type": type(exc).__name__, "operation": "camera" if args.camera else "status", "completed": False}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
