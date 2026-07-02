"""Turn an uploaded artifact into a parsed :class:`SemanticModel`.

Accepted uploads:
- ``.pbix``  — parsed directly.
- ``.zip``   — a zipped ``.pbip`` project; extracted (with a zip-slip guard)
               into the sandbox, then the project root is located and parsed.
- ``.pbip``  — only useful if its sibling folders are present (rare for an
               upload); otherwise the user should zip the project.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from ..parsers import detect_and_parse
from ..schemas.model import SemanticModel


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if dest != target and dest not in target.parents:
            raise ValueError("Refusing to extract zip entry outside the sandbox (zip-slip).")
    zf.extractall(dest)


def _find_project(root: Path) -> Path | None:
    """Locate a .pbip project root: a dir containing a ``*.SemanticModel`` folder."""
    candidates = [root, *(p for p in root.rglob("*") if p.is_dir())]
    for d in candidates:
        try:
            if any(c.is_dir() and c.name.endswith(".SemanticModel") for c in d.iterdir()):
                return d
        except OSError:
            continue
    pbips = list(root.rglob("*.pbip"))
    return pbips[0] if pbips else None


def ingest_to_model(upload_path: Path, sandbox_dir: Path) -> SemanticModel:
    suffix = upload_path.suffix.lower()
    if suffix == ".pbix":
        return detect_and_parse(upload_path)
    if suffix == ".pbip":
        return detect_and_parse(upload_path)
    if suffix == ".zip":
        extracted = sandbox_dir / "extracted"
        extracted.mkdir(exist_ok=True)
        with zipfile.ZipFile(upload_path) as zf:
            _safe_extract(zf, extracted)
        project = _find_project(extracted)
        if project is None:
            raise ValueError(
                "No Power BI project found in the zip "
                "(expected a '*.SemanticModel' folder or a '.pbip' file)."
            )
        return detect_and_parse(project)
    raise ValueError(f"Unsupported upload type '{suffix}'. Upload a .pbix or a .zip of a .pbip project.")
