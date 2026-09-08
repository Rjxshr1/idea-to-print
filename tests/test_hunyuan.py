"""Offline request/recovery regressions. A fake client never performs I/O."""
import json
import struct
import sys
from types import SimpleNamespace

import pytest


def test_cached_update_envelope_recovers_without_client(tmp_path, load_helper, write_glb, monkeypatch):
    helper = load_helper("hunyuan")
    source = write_glb(tmp_path / "cached.glb")
    work = tmp_path / "work"
    work.mkdir()
    record = work / "shape-request-choice-1.json"
    record.write_text(json.dumps({"attempt": "choice-1", "state": "result_needs_recovery", "events": []}))
    result = [{"__type__": "update", "value": {"__type__": "update", "value": {"path": str(source)}}}, 1234]
    (work / "shape-result-choice-1.json").write_text(json.dumps(result))
    monkeypatch.setattr(helper, "get_client", lambda *args: pytest.fail("Recovery must not create a network client"))

    helper.main(["--job", str(tmp_path), "--attempt", "choice-1", "--recover"])

    destination = tmp_path / "source/shape-choice-1.glb"
    assert destination.read_bytes() == source.read_bytes()
    details = json.loads(record.read_text())
    assert details["state"] == "completed"
    assert details["sha256"] == helper.glb_info(source)["sha256"]
    # Replaying recovery is idempotent and does not replace different content.
    helper.main(["--job", str(tmp_path), "--attempt", "choice-1", "--recover"])
    assert destination.read_bytes() == source.read_bytes()


def test_existing_request_prevents_resubmission(tmp_path, load_helper, monkeypatch):
    helper = load_helper("hunyuan")
    (tmp_path / "reference.png").write_bytes(b"synthetic input")
    work = tmp_path / "work"
    work.mkdir()
    record = work / "shape-request-first.json"
    original = b'{"attempt":"first","state":"submission_uncertain"}'
    record.write_bytes(original)
    monkeypatch.setattr(helper, "get_client", lambda *args: pytest.fail("Duplicate request created a client"))

    with pytest.raises(FileExistsError):
        helper.main(["--job", str(tmp_path), "--input", "reference.png", "--attempt", "first"])
    assert record.read_bytes() == original
    assert not (work / "shape-result-first.json").exists()


def test_uncertain_submission_is_recorded_and_not_retried(tmp_path, load_helper, monkeypatch):
    helper = load_helper("hunyuan")
    (tmp_path / "reference.png").write_bytes(b"synthetic input")
    calls = []
    fields = {"steps", "guidance_scale", "seed", "octree_resolution", "num_chunks",
              "check_box_rembg", "randomize_seed", "image", "mv_image_front", "mv_image_back",
              "mv_image_left", "mv_image_right"}
    record = tmp_path / "work/shape-request-once.json"

    class FakeClient:
        def view_api(self, **kwargs):
            return {"named_endpoints": {helper.API: {"parameters": [{"parameter_name": name} for name in fields]}}}

        def predict(self, **kwargs):
            # Intent must already be durable before a remote operation could happen.
            assert json.loads(record.read_text())["state"] == "submission_intent"
            calls.append(kwargs)
            raise TimeoutError("Synthetic lost response")

    monkeypatch.setattr(helper, "get_client", lambda *args: FakeClient())
    monkeypatch.setitem(sys.modules, "gradio_client", SimpleNamespace(handle_file=lambda path: path))
    arguments = ["--job", str(tmp_path), "--input", "reference.png", "--attempt", "once"]

    with pytest.raises(TimeoutError):
        helper.main(arguments)
    details = json.loads(record.read_text())
    assert details["state"] == "submission_uncertain"
    assert len(calls) == 1
    assert details["events"][0]["error_type"] == "TimeoutError"
    with pytest.raises(FileExistsError):
        helper.main(arguments)
    assert len(calls) == 1
    assert not (tmp_path / "source/shape-once.glb").exists()


def test_materialize_rejects_mismatched_destination(tmp_path, load_helper, write_glb):
    helper = load_helper("hunyuan")
    cached = write_glb(tmp_path / "cached.glb", "cached")
    destination = write_glb(tmp_path / "selected.glb", "other geometry")
    original = destination.read_bytes()
    with pytest.raises(FileExistsError):
        helper.materialize([{"path": str(cached)}], destination)
    assert destination.read_bytes() == original


def test_recovery_rejects_another_attempt_record(tmp_path, load_helper, monkeypatch):
    helper = load_helper("hunyuan")
    work = tmp_path / "work"
    work.mkdir()
    record = work / "shape-request-first.json"
    original = b'{"attempt":"different","state":"result_needs_recovery"}'
    record.write_bytes(original)
    monkeypatch.setattr(helper, "get_client", lambda *args: pytest.fail("Recovery tried network"))
    with pytest.raises(ValueError):
        helper.main(["--job", str(tmp_path), "--attempt", "first", "--recover"])
    assert record.read_bytes() == original
    assert not (tmp_path / "source").exists()


@pytest.mark.parametrize("kind", ["truncated", "wrong_magic", "wrong_version", "wrong_size"])
def test_incomplete_glb_is_not_materialized(tmp_path, load_helper, write_glb, kind):
    helper = load_helper("hunyuan")
    source = write_glb(tmp_path / "bad.glb")
    data = bytearray(source.read_bytes())
    if kind == "truncated":
        data = data[:8]
    elif kind == "wrong_magic":
        data[:4] = b"WRNG"
    elif kind == "wrong_version":
        struct.pack_into("<I", data, 4, 1)
    else:
        struct.pack_into("<I", data, 8, len(data) + 4)
    source.write_bytes(data)
    destination = tmp_path / "source/model.glb"
    with pytest.raises(ValueError):
        helper.materialize(str(source), destination)
    assert not destination.exists()


def test_remote_result_is_not_implicitly_downloaded(tmp_path, load_helper):
    helper = load_helper("hunyuan")
    with pytest.raises(ValueError):
        helper.materialize([{"path": "https://example.invalid/model.glb"}], tmp_path / "model.glb")
    assert not (tmp_path / "model.glb").exists()
