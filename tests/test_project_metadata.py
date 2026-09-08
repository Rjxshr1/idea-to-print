from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_skill_frontmatter_and_ui_metadata():
    for directory in (ROOT / "skills").iterdir():
        text = (directory / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        metadata = yaml.safe_load(text.split("---", 2)[1])
        assert metadata["name"] == directory.name
        assert metadata["description"].strip()
        interface = yaml.safe_load((directory / "agents/openai.yaml").read_text(encoding="utf-8"))
        assert "$" + directory.name in interface["interface"]["default_prompt"]


def test_relative_documentation_links_resolve():
    documents = list(ROOT.glob("*.md")) + list((ROOT / "skills").rglob("*.md")) + list((ROOT / "docs").rglob("*.md"))
    for document in documents:
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            path = target.split("#", 1)[0]
            assert (document.parent / path).exists(), f"{document}: {target}"
