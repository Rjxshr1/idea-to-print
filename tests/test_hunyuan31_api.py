"""Offline paid-API boundary tests. Never load credentials or call Tencent."""
import importlib.util
import json
import os
from pathlib import Path
import struct

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/printable-modeling/scripts/hunyuan31_api.py"


@pytest.fixture
def api():
    spec = importlib.util.spec_from_file_location("hunyuan31_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def job(tmp_path):
    folder = tmp_path / "job"
    (folder / "source").mkdir(parents=True)
    (folder / "job.json").write_text("{}")
    for index, name in enumerate(("front", "left", "right", "back")):
        Image.new("RGB", (129, 129), (index * 50, 100, 200)).save(folder / "source" / (name + ".png"))
    return folder


class Budget:
    def __init__(self):
        self.attempts = {}
        self.starts = 0
        self.remote_crash = False

    def init(self, job):
        pass

    def start_attempt(self, job, kind, attempt, strategy, defect, **kwargs):
        assert kind == "generate"
        if attempt in self.attempts or self.starts >= 2:
            raise ValueError("budget/duplicate")
        self.starts += 1
        self.attempts[attempt] = {"status": "running", **kwargs}

    def record_remote(self, job, attempt, remote_id, evidence=None):
        if self.remote_crash:
            self.remote_crash = False
            raise SystemExit("synthetic process death after JobId persistence")
        row = self.attempts[attempt]
        assert row.get("remote_id", remote_id) == remote_id
        row["remote_id"] = remote_id

    def finish_attempt(self, job, attempt, outcome, **kwargs):
        row = self.attempts[attempt]
        if row["status"] == "uncertain" and outcome != "uncertain":
            raise ValueError("requires reconcile")
        row.update(status=outcome, **kwargs)

    def reconcile_attempt(self, job, attempt, outcome, **kwargs):
        self.attempts[attempt].update(status=outcome, **kwargs)


class Transport:
    def __init__(self):
        self.submits = []
        self.queries = []
        self.submit_error = None
        self.responses = []
        self.response = {"Status": "DONE", "ResultFile3Ds": [
            {"Type": "GLB", "Url": "https://example.test/model.glb?secret=PRIVATE-SIGNATURE"}
        ], "ResultCreditConsumed": 40, "RequestId": "query-request"}

    def submit(self, payload):
        self.submits.append(payload)
        if self.submit_error:
            raise self.submit_error
        return {"JobId": "remote-123", "RequestId": "submit-request"}

    def query(self, job_id):
        self.queries.append(job_id)
        response = self.responses.pop(0) if self.responses else self.response
        if isinstance(response, Exception):
            raise response
        return response


def glb():
    body = b'{"asset":{"version":"2.0"}}'
    body += b" " * (-len(body) % 4)
    return struct.pack("<4sII", b"glTF", 2, 20 + len(body)) + struct.pack("<I4s", len(body), b"JSON") + body


@pytest.fixture
def setup(api, job):
    transport, budget, downloads, sleeps = Transport(), Budget(), [], []

    def download(url, path):
        downloads.append(url)
        path.write_bytes(glb())

    adapter = api.Hunyuan31Adapter(
        job, "g1", transport=transport, workflow=budget,
        downloader=download, sleep=sleeps.append,
    )
    return adapter, transport, budget, downloads, sleeps


def review(api, job):
    evidence = job / "views.json"
    evidence.write_text(json.dumps({
        "status": "PASS", "reviewer": "agent", "note": "Synthetic pose/identity review",
        "inputs": [{"path": "source/" + n + ".png", "sha256": api.digest(job / "source" / (n + ".png"))}
                   for n in ("front", "left", "right")],
    }))
    return evidence


def test_success_preserves_original_and_counts_once(setup, api):
    adapter, transport, budget, downloads, _ = setup
    assert adapter.submit("source/front.png")["status"] == "SUBMITTED"
    assert transport.submits[0]["FaceCount"] == 1500000
    assert transport.submits[0]["Model"] == "3.1"
    assert transport.submits[0]["GenerateType"] == "Geometry"
    assert "Prompt" not in transport.submits[0]
    result = adapter.recover()
    assert result["status"] == "COMPLETE" and result["credit_consumed"] == 40
    assert budget.starts == len(transport.submits) == 1
    assert len(downloads) == 1
    path = adapter.root / result["artifacts"][0]["path"]
    assert path.read_bytes() == glb()
    assert result["artifacts"][0]["sha256"] == api.digest(path)
    if os.name != "nt":
        assert adapter.receipt_path.stat().st_mode & 0o777 == 0o600
    assert "PRIVATE-SIGNATURE" not in json.dumps(result)
    assert "PRIVATE-SIGNATURE" in adapter.receipt_path.read_text()
    for _ in range(2):
        assert adapter.recover()["status"] == "COMPLETE"
        assert adapter.submit("source/front.png")["status"] == "COMPLETE"
    assert budget.starts == len(transport.submits) == len(downloads) == 1


def test_lost_submit_response_is_unknown_and_never_retried(setup):
    adapter, transport, budget, _, sleeps = setup
    transport.submit_error = TimeoutError("https://private.test/?SecretKey=DO-NOT-LOG")
    assert adapter.submit("source/front.png")["status"] == "UNKNOWN"
    assert adapter.recover()["status"] == "UNKNOWN"
    assert adapter.submit("source/front.png")["status"] == "UNKNOWN"
    assert len(transport.submits) == budget.starts == 1
    assert transport.queries == sleeps == []
    assert "DO-NOT-LOG" not in adapter.receipt_path.read_text()
    assert budget.attempts["g1"]["status"] == "uncertain"


def test_process_crash_during_submit_without_response_is_unknown(setup):
    adapter, transport, budget, _, _ = setup
    transport.submit_error = SystemExit("process killed after provider may have accepted")
    with pytest.raises(SystemExit):
        adapter.submit("source/front.png")
    assert adapter.read()["status"] == "SUBMITTING"
    assert adapter.recover()["status"] == "UNKNOWN"
    assert len(transport.submits) == budget.starts == 1
    assert transport.queries == []


def test_process_crash_after_saved_jobid_recovers_without_submit(setup):
    adapter, transport, budget, _, _ = setup
    budget.remote_crash = True
    with pytest.raises(SystemExit):
        adapter.submit("source/front.png")
    assert adapter.read()["job_id"] == "remote-123"
    assert adapter.recover()["status"] == "COMPLETE"
    assert len(transport.submits) == budget.starts == 1
    assert transport.queries == ["remote-123"]


def test_crash_after_download_rename_does_not_download_again(setup, monkeypatch):
    adapter, transport, budget, downloads, _ = setup
    adapter.submit("source/front.png")
    adapter.query()
    original_save = adapter.save

    def crash(receipt):
        if receipt["artifacts"]:
            raise SystemExit("crash before persisting first artifact hash")
        original_save(receipt)

    monkeypatch.setattr(adapter, "save", crash)
    with pytest.raises(SystemExit):
        adapter.download()
    monkeypatch.setattr(adapter, "save", original_save)
    assert adapter.recover()["status"] == "COMPLETE"
    assert len(downloads) == len(transport.submits) == budget.starts == 1


def test_query_network_retries_are_bounded_and_do_not_resubmit(setup):
    adapter, transport, budget, _, sleeps = setup
    adapter.submit("source/front.png")
    transport.responses = [TimeoutError("private"), TimeoutError("private"), {"Status": "RUN"}]
    assert adapter.query()["status"] == "RUN"
    assert sleeps == [1, 2]
    assert len(transport.queries) == 3
    assert len(transport.submits) == budget.starts == 1


def test_query_timeout_then_recover_uses_original_jobid(setup):
    adapter, transport, budget, _, _ = setup
    adapter.submit("source/front.png")
    transport.responses = [TimeoutError()] * 3
    assert adapter.query()["status"] == "UNKNOWN"
    assert adapter.recover()["status"] == "COMPLETE"
    assert set(transport.queries) == {"remote-123"}
    assert len(transport.submits) == budget.starts == 1


def test_partial_download_failure_retries_only_download(setup):
    adapter, transport, budget, downloads, _ = setup
    adapter.submit("source/front.png")
    adapter.query()
    original_downloader = adapter.downloader
    failed = []

    def partial(url, path):
        failed.append(url)
        path.write_bytes(b"partial")
        raise TimeoutError("signed-url-do-not-log")

    adapter.downloader = partial
    assert adapter.download()["status"] == "UNKNOWN"
    assert len(failed) == 3
    adapter.downloader = original_downloader
    assert adapter.recover()["status"] == "COMPLETE"
    assert len(transport.queries) == 1
    assert len(transport.submits) == len(downloads) == budget.starts == 1


def test_expired_jobid_stops_queries_without_regeneration(setup):
    adapter, transport, budget, _, _ = setup
    adapter.clock = lambda: "2026-09-01T00:00:00+00:00"
    adapter.submit("source/front.png")
    adapter.clock = lambda: "2026-09-02T00:00:00+00:00"
    result = adapter.recover()
    assert result["reason"] == "JOB_ID_EXPIRED"
    assert result["status"] == "UNKNOWN"
    assert transport.queries == []
    assert len(transport.submits) == budget.starts == 1


def test_missing_credit_is_unknown_not_zero(setup):
    adapter, transport, _, _, _ = setup
    transport.response.pop("ResultCreditConsumed")
    adapter.submit("source/front.png")
    result = adapter.recover()
    assert result["credit_known"] is False
    assert result["credit_consumed"] is None


def test_provider_failure_is_terminal_without_refund_claim(setup):
    adapter, transport, budget, _, _ = setup
    transport.response = {"Status": "FAIL", "ErrorCode": "InvalidParameter", "ErrorMessage": "private"}
    adapter.submit("source/front.png")
    result = adapter.query()
    assert result["status"] == "FAILED" and result["credit_known"] is False
    assert adapter.recover()["status"] == "FAILED"
    assert len(transport.queries) == 1
    assert budget.attempts["g1"]["status"] == "failed"


@pytest.mark.parametrize("response", [{"Status": "MYSTERY"}, {"Status": "DONE", "ResultFile3Ds": []}])
def test_incomplete_provider_response_is_unknown(setup, response):
    adapter, transport, budget, _, _ = setup
    transport.response = response
    adapter.submit("source/front.png")
    assert adapter.query()["status"] == "UNKNOWN"
    assert budget.starts == 1


def test_multiview_requires_fresh_review_and_valid_slots(api, job):
    views = {"left": "source/left.png", "right": "source/right.png"}
    with pytest.raises(api.AdapterError, match="REVIEW_REQUIRED"):
        api.payload_for(job, "source/front.png", views)
    reviewed = review(api, job)
    payload, records, _ = api.payload_for(job, "source/front.png", views, reviewed)
    assert len(records) == 3
    assert [v["ViewType"] for v in payload["MultiViewImages"]] == ["left", "right"]
    Image.new("RGB", (129, 129), "white").save(job / "source/left.png")
    with pytest.raises(api.AdapterError, match="STALE"):
        api.payload_for(job, "source/front.png", views, reviewed)
    with pytest.raises(api.AdapterError, match="INVALID_VIEW_SLOT"):
        api.payload_for(job, "source/front.png", {"rear_left": "source/left.png"})
    with pytest.raises(api.AdapterError, match="SUPPLEMENTAL_VIEW_LIMIT"):
        api.payload_for(job, "source/front.png", {k: "source/" + k + ".png" for k in ("left", "right", "back")})


def test_explicitly_selected_job_images_only(setup, job, api):
    adapter, transport, budget, _, _ = setup
    outside = job.parent / "outside.png"
    Image.new("RGB", (129, 129), "white").save(outside)
    for path in (outside, "../outside.png"):
        with pytest.raises(api.AdapterError, match="INPUT_MUST_BE_FILE_IN_JOB"):
            adapter.submit(path)
    assert budget.starts == 0 and transport.submits == []


def test_missing_credentials_do_not_consume_budget(api, job, monkeypatch):
    for name in ("TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRET_KEY", "TENCENTCLOUD_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    budget = Budget()
    adapter = api.Hunyuan31Adapter(job, "g1", workflow=budget)
    with pytest.raises(api.AdapterError, match="CONFIG_REQUIRED"):
        adapter.submit("source/front.png")
    assert budget.starts == 0 and not adapter.receipt_path.exists()


def test_config_check_reports_names_only(api, monkeypatch, capsys):
    monkeypatch.setenv("TENCENTCLOUD_SECRET_ID", "SENSITIVE-ID")
    monkeypatch.delenv("TENCENTCLOUD_SECRET_KEY", raising=False)
    monkeypatch.setenv("TENCENTCLOUD_TOKEN", "SENSITIVE-TOKEN")
    assert api.main(["config-check"]) == 2
    text = capsys.readouterr().out
    assert "secret_key" in text
    assert "SENSITIVE" not in text


def test_changed_archived_original_blocks_recovery_without_regeneration(setup, api):
    adapter, transport, budget, _, _ = setup
    adapter.submit("source/front.png")
    result = adapter.recover()
    (adapter.root / result["artifacts"][0]["path"]).write_bytes(b"modified")
    with pytest.raises(api.AdapterError, match="HASH_MISMATCH"):
        adapter.recover()
    assert len(transport.submits) == budget.starts == 1


def test_cli_never_prints_query_urls_or_exception_messages(api, setup, monkeypatch, capsys):
    adapter, _, _, _, _ = setup
    adapter.submit("source/front.png")
    monkeypatch.setattr(api, "Hunyuan31Adapter", lambda *a, **k: adapter)
    assert api.main(["recover", "--job", str(adapter.root), "--attempt", "g1"]) == 0
    output = capsys.readouterr().out
    assert "PRIVATE-SIGNATURE" not in output and "example.test" not in output
    assert '"status": "COMPLETE"' in output
    monkeypatch.setattr(adapter, "query", lambda **k: (_ for _ in ()).throw(RuntimeError("SECRET-URL")))
    assert api.main(["query", "--job", str(adapter.root), "--attempt", "g1"]) == 2
    output = capsys.readouterr().out
    assert "SECRET-URL" not in output and "OPERATION_FAILED" in output


def real_budget(api, job):
    workflow = api.workflow_module()
    workflow.init(job)
    workflow.record_stage(
        job, "reference", "PASS", evidence=[job / "source/front.png"],
        checks={"identity": "PASS", "pose_consistency": "PASS", "input_quality": "PASS"},
        note="Reviewed the actual synthetic input", reviewer="agent")
    return workflow


def test_real_v2_reference_gate_and_counting(api, job):
    workflow = api.workflow_module()
    transport = Transport()
    adapter = api.Hunyuan31Adapter(job, "g1", transport=transport, workflow=workflow,
                                   downloader=lambda url, p: p.write_bytes(glb()), sleep=lambda _: None)
    with pytest.raises(ValueError, match="reference review"):
        adapter.submit("source/front.png")
    assert transport.submits == []
    assert workflow.status(job)["budgets"]["generate"]["used"] == 0
    real_budget(api, job)
    adapter.submit("source/front.png")
    assert workflow.status(job)["budgets"]["generate"]["used"] == 1
    adapter.recover()
    adapter.recover()
    assert workflow.status(job)["budgets"]["generate"]["used"] == 1
    assert len(transport.submits) == 1
    manifest = json.loads((job / "job.json").read_text())
    row = manifest["workflow_v2"]["attempts"]["g1"]
    assert row["status"] == "completed" and row["remote_id"] == "remote-123"
    assert workflow.ledger.stale([row["receipt"]]) == []
    assert "PRIVATE-SIGNATURE" not in Path(row["receipt"]["path"]).read_text()


def test_real_v2_uncertain_blocks_new_attempt(api, job):
    workflow = real_budget(api, job)
    transport = Transport()
    transport.submit_error = TimeoutError()
    adapter = api.Hunyuan31Adapter(job, "g1", transport=transport, workflow=workflow)
    assert adapter.submit("source/front.png")["status"] == "UNKNOWN"
    other = api.Hunyuan31Adapter(job, "g2", transport=transport, workflow=workflow)
    with pytest.raises(ValueError, match="unresolved"):
        other.submit("source/front.png")
    assert len(transport.submits) == 1
    assert workflow.status(job)["budgets"]["generate"]["used"] == 1


def test_actual_installed_sdk_contract_is_offline(api, job, monkeypatch):
    models = pytest.importorskip("tencentcloud.ai3d.v20250513.models")
    from tencentcloud.common.retry import NoopRetryer
    from types import SimpleNamespace
    monkeypatch.setenv("TENCENTCLOUD_SECRET_ID", "synthetic-id")
    monkeypatch.setenv("TENCENTCLOUD_SECRET_KEY", "synthetic-key")
    monkeypatch.delenv("TENCENTCLOUD_TOKEN", raising=False)
    transport = api.SDKTransport()
    assert transport.client.profile.signMethod == "TC3-HMAC-SHA256"
    assert isinstance(transport.client.profile.retryer, NoopRetryer)
    assert transport.client.profile.disable_region_breaker is True
    assert transport.client.profile.httpProfile.endpoint == api.ENDPOINT
    assert transport.client.region == "ap-guangzhou"
    checked = review(api, job)
    payload, _, _ = api.payload_for(job, "source/front.png",
                                    {"left": "source/left.png"}, checked)
    captured = []

    def receive(request):
        assert isinstance(request, models.SubmitHunyuanTo3DProJobRequest)
        captured.append(request._serialize())
        return SimpleNamespace(to_json_string=lambda: '{"JobId":"mock","RequestId":"mock-request"}')

    monkeypatch.setattr(transport.client, "SubmitHunyuanTo3DProJob", receive)
    assert transport.submit(payload)["JobId"] == "mock"
    assert captured == [payload]
    assert captured[0]["MultiViewImages"][0]["ViewType"] == "left"
    calls = []
    def fail_once():
        calls.append(1)
        raise TimeoutError()
    with pytest.raises(TimeoutError):
        transport.client.profile.retryer.send_request(fail_once)
    assert calls == [1]


@pytest.mark.parametrize("terminal", ["uncertain", "completed"])
def test_ledger_commit_before_receipt_sync_recovers(api, job, monkeypatch, terminal):
    workflow = real_budget(api, job)
    transport = Transport()
    adapter = api.Hunyuan31Adapter(job, "g1", transport=transport, workflow=workflow,
                                   downloader=lambda url, p: p.write_bytes(glb()), sleep=lambda _: None)
    adapter.submit("source/front.png")
    save = adapter.save
    def die_after_ledger_commit(receipt):
        if receipt.get("budget_status") == terminal:
            raise SystemExit("ledger committed before receipt sync")
        save(receipt)
    monkeypatch.setattr(adapter, "save", die_after_ledger_commit)
    if terminal == "uncertain":
        transport.responses = [TimeoutError()] * 3
    with pytest.raises(SystemExit):
        adapter.recover()
    assert workflow.get_attempt(job, "g1")["status"] == terminal
    assert adapter.read()["budget_status"] == "running"
    monkeypatch.setattr(adapter, "save", save)
    assert adapter.recover()["status"] == "COMPLETE"
    assert workflow.get_attempt(job, "g1")["status"] == "completed"
    assert len(transport.submits) == 1


def test_crash_after_budget_reservation_before_receipt_is_unknown(api, job, monkeypatch):
    workflow = real_budget(api, job)
    transport = Transport()
    adapter = api.Hunyuan31Adapter(job, "g1", transport=transport, workflow=workflow)
    save = adapter.save
    def die_before_receipt(receipt):
        raise SystemExit("reserved but no durable submission intent")
    monkeypatch.setattr(adapter, "save", die_before_receipt)
    with pytest.raises(SystemExit):
        adapter.submit("source/front.png")
    assert workflow.get_attempt(job, "g1")["status"] == "running"
    assert not adapter.receipt_path.exists() and transport.submits == []
    monkeypatch.setattr(adapter, "save", save)
    assert adapter.recover()["status"] == "UNKNOWN"
    assert workflow.get_attempt(job, "g1")["status"] == "uncertain"
    assert transport.submits == transport.queries == []
    assert workflow.status(job)["budgets"]["generate"]["used"] == 1


def test_query_missing_config_preserves_known_state_without_retry(api, setup, monkeypatch, capsys):
    adapter, transport, budget, _, sleeps = setup
    adapter.submit("source/front.png")
    before_receipt = adapter.receipt_path.read_bytes()
    before_budget = json.dumps(budget.attempts, sort_keys=True, default=str)
    adapter.transport = None
    for name in ("TENCENTCLOUD_SECRET_ID", "TENCENTCLOUD_SECRET_KEY", "TENCENTCLOUD_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api, "Hunyuan31Adapter", lambda *a, **k: adapter)
    assert api.main(["query", "--job", str(adapter.root), "--attempt", "g1"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "CONFIG_REQUIRED"
    assert adapter.receipt_path.read_bytes() == before_receipt
    assert json.dumps(budget.attempts, sort_keys=True, default=str) == before_budget
    assert sleeps == [] and transport.queries == []
    assert len(transport.submits) == budget.starts == 1


def test_recover_missing_sdk_preserves_known_job_without_retry(api, setup, monkeypatch):
    adapter, _, budget, _, sleeps = setup
    adapter.submit("source/front.png")
    before = adapter.receipt_path.read_bytes()
    adapter.transport = None
    def missing(*args, **kwargs):
        raise api.AdapterError("OPTIONAL_SDK_NOT_INSTALLED")
    monkeypatch.setattr(api, "SDKTransport", missing)
    with pytest.raises(api.AdapterError, match="OPTIONAL_SDK_NOT_INSTALLED"):
        adapter.recover()
    assert adapter.receipt_path.read_bytes() == before
    assert budget.attempts["g1"]["status"] == "running" and sleeps == []


def test_kernel_job_lock_blocks_another_process_and_releases_after_death(api, job):
    import subprocess
    import sys
    child_code = (
        "import runpy,sys,time; from pathlib import Path; "
        "lock=runpy.run_path(sys.argv[1])['job_lock']; "
        "guard=lock(Path(sys.argv[2])); guard.__enter__(); "
        "print('LOCKED',flush=True); time.sleep(30)"
    )
    # Windows venv python.exe is a redirector that can leave its worker alive
    # when only the redirector is terminated. This child needs stdlib only; use
    # the same-version base interpreter so terminate targets the lock owner.
    child_python = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
    process = subprocess.Popen(
        [child_python, "-c", child_code, str(SCRIPT), str(job)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        assert process.stdout.readline().strip() == "LOCKED"
        with pytest.raises(api.AdapterError, match="JOB_BUSY"):
            with api.job_lock(job):
                pytest.fail("another process owns this job")
    finally:
        process.terminate()
        process.wait(timeout=10)
    with api.job_lock(job):
        assert (job / ".hunyuan31.lock").is_file()
