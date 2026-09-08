"""Copy the three portable skills into an explicitly chosen local skill folder.

Existing skills are never overwritten. This installs instructions/helpers only,
not Blender, model weights, ImageGen, a desktop-control tool or printer access.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys

SKILLS = ("idea-to-print", "printable-modeling", "3d-print-workflow")


def install(destination, source_root=None):
    source_root = Path(source_root or Path(__file__).parent / "skills").resolve()
    destination = Path(destination).expanduser().resolve()
    if destination == source_root or destination.is_relative_to(source_root):
        raise ValueError("Destination must be outside the repository's source skills")
    for name in SKILLS:
        if not (source_root / name / "SKILL.md").is_file():
            raise FileNotFoundError(f"Missing source skill: {name}")
        if (destination / name).exists() or (destination / name).is_symlink():
            raise FileExistsError(f"Existing skill preserved: {destination / name}")
    destination.mkdir(parents=True, exist_ok=True)
    installed = []
    for name in SKILLS:
        shutil.copytree(source_root / name, destination / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.local.json"))
        # Do not resolve symlinks: a venv's Python may link to the system binary,
        # but invoking that system path would discard the installed environment.
        runtime = {"python": str(Path(sys.executable).absolute()),
                   "skill_directory": str(destination / name),
                   "note": "Use this interpreter only while its dependencies and path remain available."}
        (destination / name / "runtime.local.json").write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        installed.append(destination / name)
    return installed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True,
                        help="A skill directory recognized by your agent host")
    args = parser.parse_args()
    try:
        for path in install(args.destination):
            print(path)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Installation stopped: {exc}\n")


if __name__ == "__main__":
    main()
