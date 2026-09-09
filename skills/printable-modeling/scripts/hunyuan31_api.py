"""Tencent Hunyuan 3.1: durable, budgeted submission and resumable downloads.

No paid request is made by importing this module or by config-check. A submission
with a lost response is UNKNOWN and is never automatically submitted again.
Private receipts may contain expiring signed download URLs; stdout never does.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import struct
import sys
import time
import uuid
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile

ENDPOINT = "ai3d.tencentcloudapi.com"
REGION = "ap-guangzhou"
VERSION = "2025-05-13"
SLOTS = frozenset(("left", "right", "back", "top", "bottom", "left_front", "right_front"))
PARAMS = {"Model": "3.1", "GenerateType": "Geometry", "FaceCount": 1500000}
MAX_DOWNLOAD = 1024 * 1024 * 1024


class AdapterError(RuntimeError):
    """Only constant, non-sensitive messages may be passed to this exception."""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    # Windows cannot fsync a directory using os.open. The file was flushed
    # before atomic replace; directory durability is additionally flushed on POSIX.
    if os.name != "nt":
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


@contextmanager
def job_lock(root):
    # Kernel ownership is released after a crash. Never unlink a persistent
    # lock file: another process may already hold its open file descriptor.
    path = root / ".hunyuan31.lock"
    with path.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(stream.fileno()).st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AdapterError("JOB_BUSY") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def in_job(root, value):
    candidate = Path(value)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise AdapterError("INPUT_MUST_BE_FILE_IN_JOB")
    return path


def load_credentials(credential_file=None):
    if credential_file:
        try:
            data = json.loads(Path(credential_file).expanduser().read_text(encoding="utf-8"))
        except Exception:
            raise AdapterError("CREDENTIAL_FILE_UNREADABLE") from None
        if not isinstance(data, dict):
            raise AdapterError("CREDENTIAL_FILE_INVALID")
        return {key: data.get(key) for key in ("secret_id", "secret_key", "token")}
    return {
        "secret_id": os.environ.get("TENCENTCLOUD_SECRET_ID"),
        "secret_key": os.environ.get("TENCENTCLOUD_SECRET_KEY"),
        "token": os.environ.get("TENCENTCLOUD_TOKEN"),
    }


def config_check(credential_file=None):
    missing = []
    try:
        creds = load_credentials(credential_file)
        missing.extend(key for key in ("secret_id", "secret_key")
                       if not isinstance(creds.get(key), str) or not creds[key].strip())
    except AdapterError:
        missing.append("credential_file")
    try:
        sdk = importlib.util.find_spec("tencentcloud.ai3d.v20250513") is not None
    except (ImportError, ValueError):
        sdk = False
    if not sdk:
        missing.append("tencentcloud-sdk-python-ai3d")
    return {"status": "READY" if not missing else "CONFIG_REQUIRED", "configured": not missing, "missing": missing, "region": REGION,
            "model": "3.1", "paid_request_made": False}


class SDKTransport:
    def __init__(self, credential_file=None):
        # Do not ask the SDK to discover credentials from arbitrary local files.
        creds = load_credentials(credential_file)
        if any(not isinstance(creds.get(k), str) or not creds[k].strip()
               for k in ("secret_id", "secret_key")):
            raise AdapterError("CONFIG_REQUIRED")
        try:
            from tencentcloud.common.credential import Credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.common.retry import NoopRetryer
            from tencentcloud.ai3d.v20250513 import ai3d_client, models
        except ImportError:
            raise AdapterError("OPTIONAL_SDK_NOT_INSTALLED") from None
        logging.getLogger("tencentcloud_sdk_common").setLevel(logging.CRITICAL)
        profile = ClientProfile(
            signMethod="TC3-HMAC-SHA256", retryer=NoopRetryer(),
            disable_region_breaker=True,
            httpProfile=HttpProfile(endpoint=ENDPOINT, reqTimeout=60),
        )
        self.client = ai3d_client.Ai3dClient(
            Credential(creds["secret_id"], creds["secret_key"], creds.get("token")),
            REGION, profile,
        )
        self.models = models

    def submit(self, payload):
        request = self.models.SubmitHunyuanTo3DProJobRequest()
        request.from_json_string(json.dumps(payload))
        return json.loads(self.client.SubmitHunyuanTo3DProJob(request).to_json_string())

    def query(self, job_id):
        request = self.models.QueryHunyuanTo3DProJobRequest()
        request.JobId = job_id
        return json.loads(self.client.QueryHunyuanTo3DProJob(request).to_json_string())


def workflow_module():
    scripts = Path(__file__).resolve().parents[2] / "idea-to-print" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    return importlib.import_module("workflow_v2")


def payload_for(root, main, views=None, review=None, max_supplemental=2):
    views = views or {}
    if not 0 <= max_supplemental <= 7 or len(views) > max_supplemental:
        raise AdapterError("SUPPLEMENTAL_VIEW_LIMIT")
    if set(views) - SLOTS:
        raise AdapterError("INVALID_VIEW_SLOT")
    images = {"front": in_job(root, main)}
    images.update({slot: in_job(root, name) for slot, name in views.items()})
    if len(set(images.values())) != len(images):
        raise AdapterError("DUPLICATE_VIEW_IMAGE")
    from PIL import Image
    records, encoded, total = [], {}, 0
    for slot, path in images.items():
        with Image.open(path) as img:
            width, height = img.size
            allowed = {"JPEG", "PNG", "WEBP"} if slot == "front" else {"JPEG", "PNG"}
            dimensions_ok = (128 <= min(width, height) and max(width, height) <= 5000)
            if slot != "front":
                dimensions_ok = 128 < min(width, height) and max(width, height) < 5000
            if img.format not in allowed or not dimensions_ok:
                raise AdapterError("INVALID_IMAGE_FORMAT_OR_SIZE")
            img.verify()
        raw = path.read_bytes()
        total += len(raw)
        encoded[slot] = base64.b64encode(raw).decode("ascii")
        records.append({"slot": slot, "path": str(path.relative_to(root)),
                        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    if total > 6 * 1024 * 1024 or sum(map(len, encoded.values())) > 8 * 1024 * 1024:
        raise AdapterError("IMAGE_PAYLOAD_TOO_LARGE")
    review_record = None
    if views:
        if not review:
            raise AdapterError("VIEW_CONSISTENCY_REVIEW_REQUIRED")
        review_path = in_job(root, review)
        checked = json.loads(review_path.read_text(encoding="utf-8"))
        if (checked.get("status") != "PASS" or not checked.get("reviewer")
                or not checked.get("note") or not isinstance(checked.get("inputs"), list)):
            raise AdapterError("VIEW_CONSISTENCY_REVIEW_NOT_PASS")
        reviewed = {}
        for item in checked["inputs"]:
            checked_path = in_job(root, item["path"])
            reviewed[str(checked_path.relative_to(root))] = item.get("sha256")
        if any(reviewed.get(item["path"]) != item["sha256"] for item in records):
            raise AdapterError("VIEW_REVIEW_STALE_OR_INCOMPLETE")
        review_record = {"path": str(review_path.relative_to(root)), "sha256": digest(review_path)}
    payload = dict(PARAMS, ImageBase64=encoded.pop("front"))
    if views:
        payload["MultiViewImages"] = [
            {"ViewType": slot, "ViewImageBase64": data} for slot, data in sorted(encoded.items())
        ]
    return payload, records, review_record


def safe_status(receipt):
    return {key: receipt.get(key) for key in (
        "attempt_id", "status", "job_id", "reason", "credit_consumed",
        "credit_known", "artifacts", "updated_at",
    )}


def network_download(url, destination):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AdapterError("INVALID_RESULT_URL")
    with urlopen(url, timeout=60) as response, Path(destination).open("wb") as out:
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD:
                raise AdapterError("RESULT_TOO_LARGE")
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())


def validate_download(path):
    size = path.stat().st_size
    if not size or size > MAX_DOWNLOAD:
        raise AdapterError("INVALID_RESULT_SIZE")
    if path.suffix == ".glb":
        with path.open("rb") as stream:
            header = stream.read(12)
        if len(header) != 12:
            raise AdapterError("INVALID_GLB")
        magic, version, declared = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or declared != size or size <= 20:
            raise AdapterError("INVALID_GLB")
    elif path.suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None or not archive.namelist():
                    raise AdapterError("INVALID_RESULT_ARCHIVE")
        except zipfile.BadZipFile:
            raise AdapterError("INVALID_RESULT_ARCHIVE") from None


class Hunyuan31Adapter:
    def __init__(self, job, attempt, *, transport=None, credential_file=None,
                 workflow=None, downloader=None, sleep=time.sleep, clock=utc_now):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", attempt):
            raise AdapterError("INVALID_ATTEMPT_ID")
        self.root = Path(job).resolve(strict=True)
        if not (self.root / "job.json").is_file():
            raise AdapterError("JOB_NOT_INITIALIZED")
        self.attempt = attempt
        self.folder = self.root / "work" / "generation" / attempt
        if not self.folder.resolve().is_relative_to(self.root):
            raise AdapterError("UNSAFE_RECEIPT_DIRECTORY")
        self.receipt_path = self.folder / "receipt.json"
        self.transport = transport
        self.credential_file = credential_file
        self.workflow = workflow
        self.downloader = downloader or network_download
        self.sleep, self.clock = sleep, clock

    def api(self):
        if self.transport is None:
            self.transport = SDKTransport(self.credential_file)
        return self.transport

    def budget(self):
        if self.workflow is None:
            self.workflow = workflow_module()
        return self.workflow

    def read(self):
        result = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        if result.get("attempt_id") != self.attempt or result.get("provider") != "hunyuan31-api":
            raise AdapterError("RECEIPT_MISMATCH")
        return result

    def save(self, receipt):
        receipt["updated_at"] = self.clock()
        atomic_json(self.receipt_path, receipt)

    def proof(self, receipt):
        # The budget ledger hashes evidence; do not give it a mutable receipt or
        # raw provider response containing signed URLs.
        evidence = dict(safe_status(receipt), provider="hunyuan31-api",
                        payload_sha256=receipt.get("payload_sha256"),
                        inputs=receipt.get("inputs"),
                        query_response_sha256=receipt.get("query_response_sha256"))
        path = self.folder / "evidence" / (uuid.uuid4().hex + ".json")
        atomic_json(path, evidence)
        return path

    def settle(self, receipt, outcome, artifacts=None):
        ledger = self.budget()
        args = dict(artifacts=artifacts or [], note="hunyuan31-api:" + receipt["status"])
        # job.json is authoritative across a crash between its commit and the
        # receipt update. Never infer the ledger state from the receipt alone.
        state = (ledger.get_attempt(self.root, self.attempt)["status"]
                 if hasattr(ledger, "get_attempt") else receipt.get("budget_status"))
        if state == "uncertain" and outcome != "uncertain":
            ledger.reconcile_attempt(self.root, self.attempt, outcome,
                                     evidence=[self.proof(receipt)], **args)
        elif state != outcome:
            ledger.finish_attempt(self.root, self.attempt, outcome, **args)
        receipt["budget_status"] = outcome
        self.save(receipt)

    def mark_unknown(self, receipt, reason):
        receipt.update(status="UNKNOWN", reason=reason)
        self.save(receipt)
        self.settle(receipt, "uncertain")
        return safe_status(receipt)

    def submit(self, main, *, views=None, review=None, max_supplemental=2):
        with job_lock(self.root):
            if self.receipt_path.exists():
                # Even an explicit repeated submit is a read, never another request.
                receipt = self.read()
                if not receipt.get("job_id") and receipt["status"] == "SUBMITTING":
                    return self.mark_unknown(receipt, "SUBMISSION_RESPONSE_LOST")
                return safe_status(receipt)
            payload, inputs, reviewed = payload_for(
                self.root, main, views, review, max_supplemental)
            api = self.api()  # Config/SDK checks happen before reserving a paid attempt.
            ledger = self.budget()
            ledger.init(self.root)
            ledger.start_attempt(
                self.root, "generate", self.attempt, "hunyuan31-api", "initial-shape",
                inputs=[self.root / item["path"] for item in inputs], adapter="hunyuan31-api")
            receipt = {
                "schema_version": 1, "provider": "hunyuan31-api", "attempt_id": self.attempt,
                "status": "SUBMITTING", "created_at": self.clock(), "budget_status": "running",
                "region": REGION, "api_version": VERSION, "parameters": dict(PARAMS),
                "inputs": inputs, "view_review": reviewed,
                "payload_sha256": hashlib.sha256(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "job_id": None, "credit_known": False, "credit_consumed": None,
                "artifacts": [],
            }
            self.save(receipt)  # Durable intent before the only submission call.
            try:
                response = api.submit(payload)
                job_id = response.get("JobId")
                if not isinstance(job_id, str) or not job_id:
                    return self.mark_unknown(receipt, "SUBMISSION_MISSING_JOB_ID")
            except Exception:
                return self.mark_unknown(receipt, "SUBMISSION_RESPONSE_UNKNOWN")
            receipt.update(status="SUBMITTED", job_id=job_id,
                           submit_request_id=response.get("RequestId"), accepted_at=self.clock())
            self.save(receipt)  # JobId must survive a crash before any other bookkeeping.
            ledger.record_remote(self.root, self.attempt, job_id, evidence=self.proof(receipt))
            return safe_status(receipt)

    def retry(self, call, attempts=3):
        if not 1 <= attempts <= 5:
            raise AdapterError("INVALID_RETRY_BOUND")
        for index in range(attempts):
            try:
                return call()
            except Exception:
                if index + 1 == attempts:
                    raise AdapterError("NETWORK_RETRIES_EXHAUSTED") from None
                self.sleep(min(2 ** index, 8))

    def query(self, *, retries=3):
        with job_lock(self.root):
            return self._query(retries)

    def _query(self, retries):
        receipt = self.read()
        if receipt["status"] == "COMPLETE":
            self._verify_artifacts(receipt)
            self.settle(receipt, "completed", [self.root / a["path"] for a in receipt["artifacts"]])
            return safe_status(receipt)
        if not receipt.get("job_id"):
            return self.mark_unknown(receipt, "SUBMISSION_RESPONSE_LOST")
        if receipt["status"] == "FAILED":
            self.settle(receipt, "failed")
            return safe_status(receipt)
        # Submit and query IDs have a documented 24h lifetime. Do not claim that
        # cached signed URLs can be refreshed after expiry.
        accepted = datetime.fromisoformat(receipt["accepted_at"])
        age = (datetime.fromisoformat(self.clock()) - accepted).total_seconds()
        if age >= 24 * 60 * 60:
            return self.mark_unknown(receipt, "JOB_ID_EXPIRED")
        api = self.api()  # Local configuration errors are not request failures.
        self.budget().record_remote(self.root, self.attempt, receipt["job_id"],
                                    evidence=self.proof(receipt))
        try:
            response = self.retry(lambda: api.query(receipt["job_id"]), retries)
        except Exception:
            return self.mark_unknown(receipt, "QUERY_UNAVAILABLE")
        state = response.get("Status")
        response_path = self.folder / "responses" / (uuid.uuid4().hex + ".json")
        atomic_json(response_path, response)  # private immutable raw API evidence
        receipt.setdefault("responses", []).append({
            "path": str(response_path.relative_to(self.root)), "sha256": digest(response_path)})
        receipt["query_response"] = response  # private: includes signed URLs
        receipt["query_response_sha256"] = hashlib.sha256(
            json.dumps(response, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        credit = response.get("ResultCreditConsumed")
        known = isinstance(credit, (int, float)) and not isinstance(credit, bool)
        if known:
            import math
            known = math.isfinite(credit) and credit >= 0
        receipt.update(credit_known=known, credit_consumed=credit if known else None)
        if state not in ("WAIT", "RUN", "FAIL", "DONE"):
            return self.mark_unknown(receipt, "INVALID_QUERY_STATUS")
        receipt.update(status={"WAIT": "WAIT", "RUN": "RUN", "FAIL": "FAILED", "DONE": "DONE"}[state],
                       reason=None, provider_status=state)
        self.save(receipt)
        if state == "FAIL":
            self.settle(receipt, "failed")
        elif state == "DONE" and not response.get("ResultFile3Ds"):
            return self.mark_unknown(receipt, "DONE_WITHOUT_FILES")
        return safe_status(receipt)

    def _verify_artifacts(self, receipt):
        if not receipt.get("artifacts"):
            raise AdapterError("ARCHIVE_MISSING")
        for item in receipt["artifacts"]:
            try:
                path = in_job(self.root, item["path"])
                if digest(path) != item["sha256"]:
                    raise AdapterError("ARCHIVE_HASH_MISMATCH")
            except FileNotFoundError:
                raise AdapterError("ARCHIVE_MISSING") from None

    def download(self, *, retries=3):
        with job_lock(self.root):
            return self._download(retries)

    def _download(self, retries):
        receipt = self.read()
        if receipt["status"] == "COMPLETE":
            self._verify_artifacts(receipt)
            self.settle(receipt, "completed", [self.root / a["path"] for a in receipt["artifacts"]])
            return safe_status(receipt)
        if receipt.get("provider_status") != "DONE":
            raise AdapterError("QUERY_DONE_BEFORE_DOWNLOAD")
        files = receipt.get("query_response", {}).get("ResultFile3Ds")
        if not isinstance(files, list) or not files:
            return self.mark_unknown(receipt, "DONE_WITHOUT_FILES")
        original = self.folder / "originals"
        original.mkdir(exist_ok=True, mode=0o700)
        try:
            for index, item in enumerate(files):
                url = item.get("Url")
                if not isinstance(url, str) or urlparse(url).scheme != "https":
                    raise AdapterError("INVALID_RESULT_URL")
                kind = item.get("Type", "").lower()
                if kind not in ("glb", "obj", "fbx", "stl", "usdz"):
                    raise AdapterError("UNSUPPORTED_RESULT_FORMAT")
                suffix = Path(urlparse(url).path).suffix.lower()
                if suffix not in (".glb", ".obj", ".fbx", ".stl", ".usdz", ".zip"):
                    suffix = "." + kind
                path = original / ("%02d-%s%s" % (index, kind, suffix))
                previous = next((a for a in receipt["artifacts"]
                                 if a.get("index") == index), None)
                if previous:
                    if digest(path) != previous["sha256"]:
                        raise AdapterError("ARCHIVE_HASH_MISMATCH")
                    continue
                if not path.exists():
                    temporary = path.with_name(path.stem + ".partial" + path.suffix)
                    self.retry(lambda: self.downloader(url, temporary), retries)
                    validate_download(temporary)
                    os.replace(temporary, path)
                # Recover crash after rename but before recording the hash.
                validate_download(path)
                receipt["artifacts"].append({
                    "index": index, "type": item["Type"], "path": str(path.relative_to(self.root)),
                    "sha256": digest(path), "bytes": path.stat().st_size,
                })
                self.save(receipt)
        except Exception:
            return self.mark_unknown(receipt, "DOWNLOAD_NEEDS_RECOVERY")
        receipt.update(status="COMPLETE", reason=None, completed_at=self.clock())
        self.save(receipt)
        self.settle(receipt, "completed", [self.root / a["path"] for a in receipt["artifacts"]])
        return safe_status(receipt)

    def recover(self, *, retries=3):
        with job_lock(self.root):
            if not self.receipt_path.exists():
                # The budget reservation precedes the submission intent file.
                # A crash in that gap cannot authorize a second submission.
                ledger = self.budget()
                if not hasattr(ledger, "get_attempt"):
                    raise AdapterError("MISSING_RECEIPT_NO_ATTEMPT")
                row = ledger.get_attempt(self.root, self.attempt)
                if row.get("adapter") != "hunyuan31-api" or row.get("kind") != "generate":
                    raise AdapterError("ATTEMPT_PROVIDER_MISMATCH")
                artifacts = []
                for item in row.get("artifacts", []):
                    path = in_job(self.root, item["path"])
                    artifacts.append(dict(item, path=str(path.relative_to(self.root))))
                receipt = {
                    "schema_version": 1, "provider": "hunyuan31-api",
                    "attempt_id": self.attempt, "created_at": row["started_at"],
                    "accepted_at": row["started_at"], "budget_status": row["status"],
                    "job_id": row.get("remote_id"), "inputs": row["inputs"],
                    "status": {"completed": "COMPLETE", "failed": "FAILED"}.get(row["status"], "UNKNOWN"),
                    "reason": "RECEIPT_REBUILT_FROM_LEDGER", "artifacts": artifacts,
                    "credit_known": False, "credit_consumed": None,
                }
                self.save(receipt)
            receipt = self.read()
            if receipt.get("provider_status") == "DONE" or receipt["status"] == "COMPLETE":
                return self._download(retries)
            result = self._query(retries)
            if result["status"] == "DONE":
                return self._download(retries)
            return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("config-check")
    check.add_argument("--credentials-file", type=Path)
    for name in ("submit", "query", "download", "recover"):
        command = sub.add_parser(name)
        command.add_argument("--job", required=True, type=Path)
        command.add_argument("--attempt", required=True)
        command.add_argument("--credentials-file", type=Path)
        if name == "submit":
            command.add_argument("--main", required=True)
            command.add_argument("--view", action="append", default=[], metavar="SLOT=PATH")
            command.add_argument("--review")
            command.add_argument("--max-supplemental", type=int, default=2, choices=range(0, 8))
        else:
            command.add_argument("--retries", type=int, default=3, choices=range(1, 6))
    args = parser.parse_args(argv)
    try:
        if args.command == "config-check":
            result = config_check(args.credentials_file)
            code = 0 if result["configured"] else 2
        else:
            adapter = Hunyuan31Adapter(args.job, args.attempt,
                                       credential_file=args.credentials_file)
            if args.command == "submit":
                views = {}
                for value in args.view:
                    if "=" not in value:
                        raise AdapterError("VIEW_MUST_BE_SLOT_EQUALS_PATH")
                    slot, filename = value.split("=", 1)
                    if slot in views:
                        raise AdapterError("DUPLICATE_VIEW_SLOT")
                    views[slot] = filename
                result = adapter.submit(args.main, views=views, review=args.review,
                                        max_supplemental=args.max_supplemental)
            else:
                result = getattr(adapter, args.command)(retries=args.retries)
            code = 2 if result["status"] in ("UNKNOWN", "FAILED") else 0
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return code
    except Exception as exc:
        # Exception text from SDK/HTTP may contain credentials or signed URLs.
        # Only our fixed error codes may be exposed to stdout.
        error = str(exc) if type(exc) is AdapterError else "OPERATION_FAILED"
        print(json.dumps({"status": "CONFIG_REQUIRED" if error == "CONFIG_REQUIRED" else "ERROR", "error": error}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
