# Optional Bambu LAN observation

`scripts/bambu_read.py` is an experimental adapter for existing Bambu Studio LAN credentials. It reads current status or one camera JPEG. It does not slice, upload, print, pause, calibrate, move axes, discover devices or log into a Bambu account. Status mode sends a `pushall` telemetry request and subscribes to reports; “read-only” describes printer behavior rather than a passive network connection.

It uses Python's standard library. The observed integration was a Bambu A1 mini with legacy LAN MQTT/camera endpoints. Other printer models, firmware and authorization modes are not established as compatible. Use official Studio status when the adapter is unavailable; do not bypass firmware access restrictions.

## Local configuration

Copy `printer.example.json` to a private `printer.local.json` and replace its placeholders with the user's actual LAN host and serial. `*.local.json` files and job outputs are ignored by the repository. Keep credentials out of tracked files, command-line arguments and logs. A profile identifies a printer; it does not establish print authorization.

The helper reads the access code for that serial from an existing Bambu Studio configuration. It expects the observed JSON `access_code` mapping; future Studio formats may require adaptation. Do not print the entire configuration when inspecting compatibility.

- On Windows, without an override, discovery uses `%APPDATA%/BambuStudio/BambuStudio.conf`.
- On Linux/macOS, pass `--studio-config` pointing to the actual existing Studio configuration. WSL can use an explicitly supplied mounted Windows configuration path. Automatic Linux/macOS discovery is not implemented.
- `--studio-config` specifies a file path, not the secret itself. Bambu cloud credentials are not substituted for a missing local device access code.

From the repository root, with the local profile created and output directory existing:

```sh
python skills/3d-print-workflow/scripts/bambu_read.py --profile printer.local.json --out jobs/example/work/status.json
python skills/3d-print-workflow/scripts/bambu_read.py --profile printer.local.json --studio-config /path/to/BambuStudio.conf --camera jobs/example/work/camera.jpg
```

These commands contact the configured printer. Do not run them as an installation or offline test against someone else's device.

## Trust and output limits

The experimental legacy adapter accepts printer-issued/self-signed TLS certificates without certificate verification, on LAN ports 8883 (telemetry) or 6000 (camera). This encrypts transport but does not authenticate the host's identity. Scope it to a trusted private LAN and a verified printer address; an untrusted host could receive the device access code. It is not appropriate for public hosts or a generic web API client. No certificate provisioning or verification implementation is included.

Output includes sampled state, task/layer information, temperatures, selected tray and reported errors. The helper suppresses exception details that might expose credentials. A failure therefore reports only operation/error type; inspect file existence and configuration structure locally without dumping secrets.

`missing_preflight_fields` and `filament_remaining_is_unverified` are explicit limitations. Missing values are unknown, not zero or error-free evidence. A tray field cannot prove material physically loaded or spool weight. One camera JPEG may not show the entire plate. Match task identity and expected layers so a prior completed print is not mistaken for a newly dispatched job. Keep local telemetry and camera outputs private when publishing examples.
