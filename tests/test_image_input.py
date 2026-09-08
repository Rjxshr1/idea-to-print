import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import subprocess

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def intake():
    return module("skills/idea-to-print/scripts/prepare_image.py", "image_intake")


@pytest.mark.parametrize("fmt,extension", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")])
def test_input_preserves_image_and_does_not_authorize_print(tmp_path, intake, fmt, extension):
    original = tmp_path / "reference.wrong-extension"
    Image.new("RGB", (12, 18), "white").save(original, format=fmt)
    job = tmp_path / "my-job"
    result = intake.prepare(original, job, "A sculpture", 160)
    assert (job / ("source/selected" + extension)).read_bytes() == original.read_bytes()
    assert result["selected"]["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert result["selected"]["pixels"] == [12, 18]
    assert result["authorization"] == {"model": None, "print": None}
    assert result["dispatch_attempts"] == []
    assert json.loads((job / "job.json").read_text(encoding="utf-8")) == result


def test_existing_selection_is_not_overwritten(tmp_path, intake):
    source = tmp_path / "image.png"
    Image.new("RGB", (10, 10)).save(source)
    job = tmp_path / "job"
    intake.prepare(source, job)
    original = (job / "job.json").read_bytes()
    with pytest.raises(FileExistsError):
        intake.prepare(source, job)
    assert (job / "job.json").read_bytes() == original


@pytest.mark.parametrize("size", [-1, 0, float("inf"), float("nan")])
def test_invalid_target_does_not_create_job(tmp_path, intake, size):
    source = tmp_path / "image.png"
    Image.new("RGB", (10, 10)).save(source)
    with pytest.raises(ValueError):
        intake.prepare(source, tmp_path / "job", target_mm=size)
    assert not (tmp_path / "job").exists()


def test_corrupt_image_and_unsupported_format(tmp_path, intake):
    source = tmp_path / "image.png"
    source.write_bytes(b"invalid")
    with pytest.raises(OSError):
        intake.prepare(source, tmp_path / "job")
    assert not (tmp_path / "job").exists()
    Image.new("RGB", (10, 10)).save(source, format="BMP")
    with pytest.raises(ValueError, match="PNG, JPEG and WebP"):
        intake.prepare(source, tmp_path / "job")
    assert not (tmp_path / "job").exists()


def test_resource_limits_before_job_creation(tmp_path, intake, monkeypatch):
    source = tmp_path / "image.png"
    Image.new("RGB", (20, 20)).save(source)
    monkeypatch.setattr(intake, "MAX_PIXELS", 100)
    with pytest.raises(ValueError, match="megapixels"):
        intake.prepare(source, tmp_path / "job")
    assert not (tmp_path / "job").exists()


def test_install_preserves_existing_skills(tmp_path):
    installer = module("install.py", "installer")
    destination = tmp_path / "skills"
    destination.mkdir()
    existing = destination / "printable-modeling"
    existing.mkdir()
    marker = existing / "SKILL.md"
    marker.write_text("User's existing skill")
    with pytest.raises(FileExistsError):
        installer.install(destination)
    assert marker.read_text() == "User's existing skill"
    assert not (destination / "idea-to-print").exists()


def test_install_all_skills_in_separate_directory(tmp_path):
    installer = module("install.py", "installer")
    installed = installer.install(tmp_path / "new-skills")
    assert {p.name for p in installed} == set(installer.SKILLS)
    assert all((p / "SKILL.md").is_file() for p in installed)
    assert (tmp_path / "new-skills/idea-to-print/scripts/prepare_image.py").is_file()
    for directory in installed:
        runtime = json.loads((directory / "runtime.local.json").read_text(encoding="utf-8"))
        assert runtime["python"] == str(Path(sys.executable).absolute())
        assert runtime["skill_directory"] == str(directory)
    observed_prefix = subprocess.check_output(
        [runtime["python"], "-c", "import sys; print(sys.prefix)"],
        cwd=tmp_path, text=True).strip()
    assert Path(observed_prefix) == Path(sys.prefix)


def test_installed_image_helper_works_from_another_working_directory(tmp_path):
    installer = module("install.py", "installer")
    destination = tmp_path / "installed"
    installer.install(destination)
    skill = destination / "idea-to-print"
    runtime = json.loads((skill / "runtime.local.json").read_text(encoding="utf-8"))
    source = tmp_path / "uploaded.png"
    Image.new("RGB", (12, 12)).save(source)
    job = tmp_path / "unrelated-job"
    subprocess.run([runtime["python"], str(skill / "scripts/prepare_image.py"),
                    "--image", str(source), "--job", str(job)],
                   cwd=tmp_path, check=True, capture_output=True)
    assert (job / "source/selected.png").read_bytes() == source.read_bytes()


def test_install_cannot_recurse_into_source(tmp_path):
    installer = module("install.py", "installer")
    with pytest.raises(ValueError):
        installer.install(ROOT / "skills/recursive")
