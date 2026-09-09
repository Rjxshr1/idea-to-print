# Official Hunyuan3D 3.1 API adapter

The optional `scripts/hunyuan31_api.py` uses Tencent's official Python SDK.
It is separate from the 2.1 public-demo adapter and the logged-in Studio website.
Install the repository's `requirements-hunyuan31.txt` in the chosen Python
environment. `config-check` reports missing configuration without revealing
credentials or making a generation request.

Use `TENCENTCLOUD_SECRET_ID` and `TENCENTCLOUD_SECRET_KEY`, or the explicitly
selected private JSON file passed as `--credentials-file /PRIVATE/credentials.json`.
Its keys are `secret_id` and `secret_key`, with optional `token`. Environment-based
temporary credentials use `TENCENTCLOUD_TOKEN` in addition to the two secret fields. Do not put credentials in job files, source control,
prompts, screenshots or public reports. Missing configuration is CONFIG_REQUIRED;
finish offline work first. Do not buy credits or change accounts by inference.

## Commands

Use `--help` for the executable argument schema. Main operations are:

```text
hunyuan31_api.py config-check
hunyuan31_api.py submit --job JOB --attempt shape-01 --main source/main.png
hunyuan31_api.py query --job JOB --attempt shape-01
hunyuan31_api.py download --job JOB --attempt shape-01
hunyuan31_api.py recover --job JOB --attempt shape-01
```

Initialize V2 and record a fresh reference review before submission. Supplemental
`--view left=source/left.png` images require a consistency report binding each
actual image path and SHA256; default maximum is two extra images. Pass it with
`--review PATH`. The JSON requires status `PASS`, nonempty `reviewer` and `note`,
and `inputs` containing objects with `path` and `sha256` for every submitted image.
Changed image bytes invalidate the report. `--max-supplemental` explicitly changes
the local limit from 0 to 7; it never removes the review requirement. Official
3.1 slots are left/right/back/top/bottom/left_front/right_front, each at most once.
Do not map rear-left to left-front or pass collages as independent views.

Requests explicitly use API 2025-05-13, `Model="3.1"`,
`GenerateType="Geometry"`, `FaceCount=1500000`, region `ap-guangzhou`.
The official model default is 3.0, so omitting Model would change the route.
Preserve the original downloaded high-poly file before any Blender processing.

## Recovery contract

- Persist SUBMITTING before the network call; persist JobId immediately after
  receipt. Submission has no automatic retry, including SDK-level retries.
- A possible submission without a recorded JobId is UNKNOWN. The current public
  API has no caller-supplied idempotency key or task-list lookup. RequestId is a
  diagnostic identifier, not an idempotency key. Never automatically resubmit.
- With JobId, only query that task. WAIT/RUN remain pending; local timeout does
  not turn them into provider FAIL. Query and downloads have bounded retry
  (`--retries 1..5`, default 3). Local configuration failures return CONFIG_REQUIRED
  without changing the known JobId, provider state or attempt budget.
- `query` reports DONE without downloading; `download` collects a known DONE result.
  `recover` performs one bounded query and, if DONE, collects it in the same call.
  It is not a background poller. Download interruption retries collection,
  never generation. Validate the actual model file and hash before completion.
- Official docs state JobId and result URLs have a 24-hour validity period;
  collect promptly. There is no assumed renewal guarantee.
- Actual credits come from provider result fields; missing values remain unknown.
  Do not promise failed jobs are free or refunded.

Private receipts may contain expiring download URLs. POSIX files use mode 0600.
On Windows, chmod does not establish owner-only ACLs: privacy depends on existing
Windows permissions on the chosen directory. The adapter does not change Windows
ACLs or promise directory-fsync durability. Its kernel job lock works on POSIX
and Windows and is released after process exit. CLI summaries and exports
exclude signed URL parameters and credentials. A website fallback is an explicit
alternative attempt after the current submission has been reconciled; it is not
a retry mechanism for an ambiguous API request.

API reference: [submit](https://cloud.tencent.com/document/product/1804/123447),
[query](https://cloud.tencent.com/document/product/1804/123448),
[public parameters](https://cloud.tencent.com/document/product/1804/120832),
[official SDK](https://github.com/TencentCloud/tencentcloud-sdk-python/tree/master/tencentcloud/ai3d/v20250513).
