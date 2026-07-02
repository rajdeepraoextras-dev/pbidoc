"""``.pbip`` / ``.pbix`` ingestion orchestrator.

Locates the semantic model and report artifacts, dispatches to the TMDL/TMSL
and report parsers, then enriches the result (data-source inference, table-kind
heuristics, counts). Produces the canonical :class:`SemanticModel`.

Zero-data-leakage: only definition artifacts are read. For ``.pbix`` the
VertiPaq ``DataModel`` part is never deserialised — the semantic model is
extracted via the pbixray adapter (metadata frames only, never ``get_table``)
and the report layout is read from the ZIP's ``Report/Layout`` part.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Optional

from ..schemas.model import DataSource, ModelMeta, SemanticModel, Table
from . import tmdl, tmsl
from .pbir import parse_report

# Known Power Query connectors we surface as source systems.
_CONNECTORS = (
    "Sql.Database", "Sql.Databases", "PostgreSQL.Database", "MySQL.Database",
    "Oracle.Database", "Snowflake.Databases", "AmazonRedshift.Database",
    "GoogleBigQuery.Database", "Databricks.Catalogs", "Web.Contents",
    "Excel.Workbook", "Csv.Document", "Json.Document", "Odbc.DataSource",
    "OData.Feed", "SharePoint.Tables", "SharePoint.Files", "AzureStorage.Blobs",
    "Folder.Files", "File.Contents",
)
_CONN_RE = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in _CONNECTORS) +
    r")\s*\(\s*\"([^\"]*)\"(?:\s*,\s*\"([^\"]*)\")?"
)


def _find_dir(root: Path, suffix: str, stem: Optional[str]) -> Optional[Path]:
    """Find a ``*<suffix>`` directory under ``root`` (prefer one matching stem)."""
    candidates = [p for p in root.iterdir() if p.is_dir() and p.name.endswith(suffix)]
    if not candidates:
        candidates = [p for p in root.rglob(f"*{suffix}") if p.is_dir()]
    if stem:
        for c in candidates:
            if c.name == f"{stem}{suffix}":
                return c
    return candidates[0] if candidates else None


def _infer_data_sources(model: SemanticModel) -> list[DataSource]:
    texts: list[str] = []
    for t in model.tables:
        for p in t.partitions:
            if p.expression:
                texts.append(p.expression)
    for e in model.expressions:
        if e.expression:
            texts.append(e.expression)

    seen: set[tuple] = set()
    sources: list[DataSource] = []
    for text in texts:
        for func, a1, a2 in _CONN_RE.findall(text):
            url_like = func in ("Web.Contents", "OData.Feed", "Csv.Document",
                                "Excel.Workbook", "Json.Document", "File.Contents",
                                "Folder.Files")
            ds = DataSource(
                type=func,
                server=None if url_like else (a1 or None),
                database=(a2 or None) if not url_like else None,
                detail=a1 if url_like else None,
            )
            key = (ds.type, ds.server, ds.database, ds.detail)
            if key not in seen:
                seen.add(key)
                sources.append(ds)
    return sources


def _classify_tables(model: SemanticModel) -> None:
    """Light fact/dimension heuristic (refined later by the Data Modeler agent)."""
    many_end: dict[str, int] = {}
    one_end: dict[str, int] = {}
    for r in model.relationships:
        many_end[r.from_table] = many_end.get(r.from_table, 0) + 1
        one_end[r.to_table] = one_end.get(r.to_table, 0) + 1
    for t in model.tables:
        if t.kind not in ("unknown",):  # respect calculation / calculation-group
            continue
        m, o = many_end.get(t.name, 0), one_end.get(t.name, 0)
        has_measures = bool(t.measures)
        if m and (has_measures or m >= o):
            t.kind = "fact"
        elif o and not m:
            t.kind = "dimension"
        elif m:
            t.kind = "fact"
        else:
            t.kind = "unknown"


def _assemble(agg: dict, pages: list, report_name: str,
              source_format: str, source_path: str, warnings: list[str]) -> SemanticModel:
    model = SemanticModel(
        report_name=report_name,
        model_name=agg.get("model_name"),
        tables=agg.get("tables", []),
        relationships=agg.get("relationships", []),
        roles=agg.get("roles", []),
        expressions=agg.get("expressions", []),
        pages=pages,
        meta=ModelMeta(source_format=source_format, source_path=source_path,
                       warnings=warnings),
    )
    model.data_sources = _infer_data_sources(model)
    _classify_tables(model)
    model.compute_counts()
    return model


def parse_pbip(path: Path) -> SemanticModel:
    """Parse a ``.pbip`` project (pass the ``.pbip`` file or the project dir)."""
    path = Path(path)
    warnings: list[str] = []
    if path.is_file() and path.suffix.lower() == ".pbip":
        root, stem = path.parent, path.stem
    elif path.is_dir():
        root, stem = path, None
    else:
        raise FileNotFoundError(f"Not a .pbip file or project directory: {path}")

    sem_dir = _find_dir(root, ".SemanticModel", stem)
    report_dir = _find_dir(root, ".Report", stem)

    agg: dict = {"tables": [], "relationships": [], "roles": [],
                 "expressions": [], "model_name": None}
    source_format = "unknown"
    if sem_dir:
        definition = sem_dir / "definition"
        if definition.is_dir() and any(definition.rglob("*.tmdl")):
            agg = tmdl.parse_semantic_model_tmdl(definition, warnings)
            source_format = "pbip-tmdl"
        elif (sem_dir / "model.bim").exists():
            bim = json.loads((sem_dir / "model.bim").read_text(encoding="utf-8-sig"))
            agg = tmsl.parse_semantic_model_tmsl(bim, warnings)
            source_format = "pbip-tmsl"
        else:
            warnings.append(f"no TMDL/model.bim found under {sem_dir.name}")
    else:
        warnings.append("no *.SemanticModel folder found")

    pages: list = []
    if report_dir:
        pages = parse_report(report_dir, warnings)
    else:
        warnings.append("no *.Report folder found")

    report_name = (stem or (report_dir.name[:-7] if report_dir else None)
                   or agg.get("model_name") or root.name)
    return _assemble(agg, pages, report_name, source_format, str(path), warnings)


def _extract_pbix_layout(path: Path, warnings: list[str]) -> list:
    """Read the report layout from a ``.pbix`` ZIP (never touches DataModel)."""
    pages: list = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "Report/Layout" not in names:
                warnings.append("no Report/Layout part found in .pbix")
                return pages
            raw = zf.read("Report/Layout")
            layout = None
            for enc in ("utf-16-le", "utf-8-sig", "utf-8"):
                try:
                    layout = json.loads(raw.decode(enc))
                    break
                except Exception:
                    layout = None
            if layout and "sections" in layout:
                from .pbir import _parse_legacy
                pages = _parse_legacy(layout, warnings)
            else:
                warnings.append("could not decode Report/Layout")
    except Exception as exc:
        warnings.append(f"failed to open .pbix archive: {exc}")
    return pages


def parse_pbix(path: Path) -> SemanticModel:
    """Parse a legacy ``.pbix``: report layout from the ZIP + semantic model
    via the pbixray adapter.

    The VertiPaq ``DataModel`` row data is never materialised. If ``pbixray`` is
    not installed the report layout is still returned and a clear warning is
    recorded (graceful degradation rather than a hard failure).
    """
    path = Path(path)
    warnings: list[str] = []
    pages = _extract_pbix_layout(path, warnings)
    agg: dict = {"tables": [], "relationships": [], "roles": [],
                 "expressions": [], "model_name": None}
    try:
        from ..adapters import build_model_from_frames, load_frames_from_pbix
        frames = load_frames_from_pbix(path)
        agg = build_model_from_frames(frames, warnings)
    except ImportError:
        warnings.append(
            "pbixray not installed — .pbix semantic-model extraction skipped "
            "(report layout still parsed). Install with `pip install pbixray` on "
            "Python <=3.13, or export the file as a .pbip project for full extraction."
        )
    except Exception as exc:
        warnings.append(f"pbixray extraction failed: {exc}")
    return _assemble(agg, pages, path.stem, "pbix", str(path), warnings)


def detect_and_parse(path: Path) -> SemanticModel:
    """Entry point: dispatch by file type / directory."""
    path = Path(path)
    if path.is_dir():
        return parse_pbip(path)
    suffix = path.suffix.lower()
    if suffix == ".pbip":
        return parse_pbip(path)
    if suffix == ".pbix":
        return parse_pbix(path)
    raise ValueError(f"Unsupported input: {path} (expected .pbip, .pbix, or a project dir)")
