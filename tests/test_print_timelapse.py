"""Timelapse capture and rendering with a fake printer; no network or hardware."""
import importlib.util
import json
from pathlib import Path
import shutil

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tested_timelapse", ROOT / "skills/3d-print-workflow/scripts/print_timelapse.py")
lapse = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lapse)


class FakePrinter:
    """Replays scripted status reads; each camera frame is a distinct solid-colour JPEG."""

    def __init__(self, states, failing_frames=()):
        self.states = list(states)
        self.frames = 0
        self.failing = set(failing_frames)
        self.calls = 0

    def status(self):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, Exception):
            raise state
        return state

    def frame(self, path):
        self.calls += 1
        if self.calls in self.failing:
            raise TimeoutError("no frame")
        Image.new("RGB", (320, 180), (self.frames * 40 % 255, 80, 160)).save(path, "JPEG")
        self.frames += 1


def state(phase, layer, total=3, percent=None):
    return {"gcode_state": phase, "layer_num": layer, "total_layer_num": total, "mc_percent": percent, "task_id": "t1"}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def run(printer, out, **options):
    clock = Clock()
    return lapse.capture(printer, out, sleep=clock.sleep, clock=clock, **options)


def test_one_frame_per_new_layer_plus_the_finished_part(tmp_path):
    printer = FakePrinter([state("PREPARE", 0), state("RUNNING", 1, percent=30), state("RUNNING", 1),
                           state("RUNNING", 2, percent=60), state("RUNNING", 3, percent=95), state("FINISH", 3)])
    result = run(printer, tmp_path / "cap")
    assert result == {"frames": 5, "stopped": "finish"}
    rows = lapse.read_log(tmp_path / "cap")
    assert [row.get("layer") for row in rows] == [0, 1, 2, 3, 3]
    assert rows[-1]["final"] is True
    assert all(len(row["sha256"]) == 64 for row in rows)


def test_interval_mode_and_dropped_reads_do_not_end_the_capture(tmp_path):
    printer = FakePrinter([state("RUNNING", 1), ConnectionError("drop"), state("RUNNING", 1),
                           state("RUNNING", 1), state("FAILED", 1)], failing_frames={2})
    result = run(printer, tmp_path / "cap", every_layer=False, interval=15, poll=10)
    assert result["stopped"] == "failed"
    rows = lapse.read_log(tmp_path / "cap")
    assert rows[-1]["final"] is True
    assert len({row["file"] for row in rows}) == len(rows)  # A failed frame leaves no gap or file.


def test_a_print_that_already_finished_is_not_mistaken_for_this_one(tmp_path):
    printer = FakePrinter([state("FINISH", 120, total=120)])
    result = run(printer, tmp_path / "cap", max_hours=0.01, poll=10)
    assert result == {"frames": 0, "stopped": "max_hours"}


def test_persistent_status_failure_stops_cleanly(tmp_path):
    printer = FakePrinter([ConnectionError("offline")])
    result = run(printer, tmp_path / "cap")
    assert result["stopped"] == "status_unavailable" and result["frames"] == 0


def captured(tmp_path):
    printer = FakePrinter([state("RUNNING", 1, percent=10), state("RUNNING", 2, percent=60), state("FINISH", 2)])
    run(printer, tmp_path / "cap")
    return tmp_path / "cap"


def test_render_gif_with_intro_outro_and_captions(tmp_path):
    folder = captured(tmp_path)
    intro, outro = tmp_path / "idea.png", tmp_path / "photo.jpg"
    Image.new("RGB", (400, 400), "white").save(intro)
    Image.new("RGB", (600, 300), "gray").save(outro)
    result = lapse.render(folder, tmp_path / "lapse.gif", fps=10, size=(320, 180), intro=intro, outro=outro,
                          hold_seconds=0.5)
    assert result["frames"] == 3
    # Intro and outro are held for 5 frames each, and the last print frame is held too.
    assert result["video_frames"] == 5 + 3 + 4 + 5
    with Image.open(tmp_path / "lapse.gif") as gif:
        # Pillow merges identical consecutive frames, so check the total running time.
        total = 0
        for index in range(gif.n_frames):
            gif.seek(index)
            total += gif.info["duration"]
        assert total == result["video_frames"] * 100 and gif.size == (320, 180)
    with pytest.raises(ValueError, match="overwrite"):
        lapse.render(folder, tmp_path / "lapse.gif")


def test_captions_describe_progress():
    assert lapse.caption_for({"layer": 12, "total_layers": 80, "percent": 15}) == "Layer 12 / 80  ·  15%"
    assert lapse.caption_for({"final": True}) == "Finished"
    assert lapse.caption_for({}) is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_mp4(tmp_path):
    folder = captured(tmp_path)
    result = lapse.render(folder, tmp_path / "lapse.mp4", fps=12, size=(320, 180))
    assert (tmp_path / "lapse.mp4").stat().st_size > 0 and result["seconds"] > 0


def test_render_needs_frames_and_a_known_format(tmp_path):
    with pytest.raises(ValueError, match="No captured frames"):
        lapse.render(tmp_path, tmp_path / "x.gif")
    folder = captured(tmp_path)
    with pytest.raises(ValueError, match=".mp4 or .gif"):
        lapse.render(folder, tmp_path / "x.avi")


def test_cli_rejects_bad_options_without_contacting_a_printer(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lapse, "LanCamera", lambda *a, **k: pytest.fail("must not connect"))
    assert lapse.main(["capture", "--profile", "p.json", "--out", str(tmp_path), "--poll", "0"]) == 2
    assert json.loads(capsys.readouterr().err)["operation"] == "capture"
