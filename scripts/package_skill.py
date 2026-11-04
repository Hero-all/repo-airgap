"""Build a self-contained skill/source archive; never include local evidence or environments."""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build():
    output = ROOT / "dist/repo-airgap-skill-0.1.0.zip"
    output.parent.mkdir(exist_ok=True)
    files = [
        ROOT / name
        for name in ("SKILL.md", "README.md", "README_CN.md", "LICENSE", "pyproject.toml", ".cursorrules")
        if (ROOT / name).is_file()
    ]
    for directory in ("agents", "references", "rules", "src", "scripts", "tests"):
        files.extend(
            p
            for p in (ROOT / directory).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
        )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, "repo-airgap/" + path.relative_to(ROOT).as_posix())
    print(output)


if __name__ == "__main__":
    build()
