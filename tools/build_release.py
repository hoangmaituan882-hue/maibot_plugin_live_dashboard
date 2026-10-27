"""Build an installable ZIP; omit user config, development caches, and logs."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".git",
    "logs",
    "data",
    "temp",
    "dist",
}


def build(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob("*")):
            relative = path.relative_to(ROOT)
            if not path.is_file() or path.resolve() == destination.resolve():
                continue
            if any(part in EXCLUDED for part in relative.parts):
                continue
            if path.name == "config.toml" or path.suffix in (".pyc", ".pyo", ".log", ".zip"):
                continue
            archive.write(path, relative.as_posix())
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert {"plugin.py", "_manifest.json", "LICENSE", "README.md"} <= set(archive.namelist())
    print(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    build(parser.parse_args().destination)
