"""Report layout parser.

Handles both report formats:

* **PBIR enhanced** (current ``.pbip`` default): ``definition/pages/<page>/``
  folders, each with ``page.json`` and ``visuals/<id>/visual.json``.
* **Legacy Layout** JSON: a single document with a ``sections`` array
  (older ``.pbip`` / extracted ``.pbix``). Parsed best-effort.

Only layout/structure metadata is read: page names, visual types, positions,
slicers, drill-through flags, and field references — no data values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..schemas.model import Page, Visual

SLICER_TYPES = {"slicer", "advancedSlicerVisual"}
GROUP_TYPES = {"visualGroup", "visualContainerGroup"}


# -- enhanced-format helpers --------------------------------------------------
def _extract_title(visual_obj: dict) -> Optional[str]:
    try:
        title = visual_obj["objects"]["title"]
        if isinstance(title, list) and title:
            text = title[0]["properties"]["text"]
            lit = text.get("expr", {}).get("Literal", {}).get("Value")
            if isinstance(lit, str):
                return lit.strip().strip("'")
    except Exception:
        pass
    return None


def _extract_fields(visual_obj: dict) -> list[str]:
    fields: list[str] = []
    try:
        query_state = visual_obj.get("query", {}).get("queryState", {})
        for role in query_state.values():
            for proj in role.get("projections", []):
                ref = proj.get("queryRef")
                if ref:
                    fields.append(ref)
                    continue
                field = proj.get("field", {})
                for kind in ("Column", "Measure", "Aggregation"):
                    node = field.get(kind)
                    if not node:
                        continue
                    if kind == "Aggregation":
                        node = node.get("Expression", {}).get("Column", {})
                    entity = node.get("Expression", {}).get("SourceRef", {}).get("Entity")
                    prop = node.get("Property")
                    if entity and prop:
                        fields.append(f"{entity}.{prop}")
    except Exception:
        pass
    # de-dupe, preserve order
    seen: set[str] = set()
    return [f for f in fields if not (f in seen or seen.add(f))]


def _parse_visual_json(obj: dict, warnings: list[str]) -> Optional[Visual]:
    pos = obj.get("position", {}) or {}
    visual_obj = obj.get("visual")
    if visual_obj is None:
        # a group container, not a leaf visual
        vtype = "visualGroup"
        return Visual(
            id=obj.get("name", "unknown"), type=vtype,
            x=pos.get("x"), y=pos.get("y"), z=pos.get("z"),
            width=pos.get("width"), height=pos.get("height"),
        )
    vtype = visual_obj.get("visualType", "unknown")
    return Visual(
        id=obj.get("name", "unknown"),
        type=vtype,
        title=_extract_title(visual_obj),
        x=pos.get("x"), y=pos.get("y"), z=pos.get("z"),
        width=pos.get("width"), height=pos.get("height"),
        fields=_extract_fields(visual_obj),
        is_slicer=vtype in SLICER_TYPES,
    )


def _parse_page_dir(page_dir: Path, ordinal: int, warnings: list[str]) -> Page:
    page_json_path = page_dir / "page.json"
    raw = page_json_path.read_text(encoding="utf-8-sig")
    meta = json.loads(raw)
    page = Page(
        id=meta.get("name", page_dir.name),
        display_name=meta.get("displayName", meta.get("name", page_dir.name)),
        ordinal=ordinal,
        is_hidden=meta.get("visibility") == "HiddenInViewMode",
        is_drillthrough=(
            meta.get("pageBinding", {}).get("type") == "Drillthrough"
            or '"Drillthrough"' in raw
        ),
        width=meta.get("width"),
        height=meta.get("height"),
    )
    visuals_dir = page_dir / "visuals"
    if visuals_dir.is_dir():
        for vdir in sorted(p for p in visuals_dir.iterdir() if p.is_dir()):
            vfile = vdir / "visual.json"
            if not vfile.exists():
                continue
            try:
                vobj = json.loads(vfile.read_text(encoding="utf-8-sig"))
                visual = _parse_visual_json(vobj, warnings)
                if visual:
                    page.visuals.append(visual)
            except Exception as exc:
                warnings.append(f"page '{page.display_name}': visual parse error: {exc}")
    page.visuals.sort(key=lambda v: (v.z or 0))
    return page


def _parse_enhanced(definition_dir: Path, warnings: list[str]) -> list[Page]:
    pages_dir = definition_dir / "pages"
    order: list[str] = []
    pages_index = pages_dir / "pages.json"
    if pages_index.exists():
        try:
            order = json.loads(pages_index.read_text(encoding="utf-8-sig")).get("pageOrder", [])
        except Exception as exc:
            warnings.append(f"pages.json parse error: {exc}")
    dirs = {p.name: p for p in pages_dir.iterdir() if p.is_dir()}
    ordered_names = order + [n for n in sorted(dirs) if n not in order]
    pages: list[Page] = []
    for i, name in enumerate(ordered_names):
        page_dir = dirs.get(name)
        if not page_dir or not (page_dir / "page.json").exists():
            continue
        try:
            pages.append(_parse_page_dir(page_dir, i, warnings))
        except Exception as exc:
            warnings.append(f"page '{name}': parse error: {exc}")
    return pages


# -- legacy-format helpers ----------------------------------------------------
def _parse_legacy_container(container: dict, warnings: list[str]) -> Optional[Visual]:
    try:
        config = json.loads(container.get("config", "{}"))
    except Exception:
        config = {}
    sv = config.get("singleVisual", {})
    vtype = sv.get("visualType", "unknown" if sv else "visualGroup")
    title = None
    try:
        title_obj = sv.get("vcObjects", {}).get("title", [{}])[0]
        lit = title_obj["properties"]["text"]["expr"]["Literal"]["Value"]
        title = str(lit).strip().strip("'")
    except Exception:
        pass
    fields: list[str] = []
    try:
        for sel in sv.get("prototypeQuery", {}).get("Select", []):
            for kind in ("Column", "Measure", "Aggregation"):
                node = sel.get(kind)
                if node:
                    entity = node.get("Expression", {}).get("SourceRef", {}).get("Entity", "")
                    prop = node.get("Property", "")
                    if prop:
                        fields.append(f"{entity}.{prop}" if entity else prop)
    except Exception:
        pass
    return Visual(
        id=config.get("name", "unknown"),
        type=vtype,
        title=title,
        x=container.get("x"), y=container.get("y"), z=container.get("z"),
        width=container.get("width"), height=container.get("height"),
        fields=fields,
        is_slicer=vtype in SLICER_TYPES,
    )


def _parse_legacy(layout: dict, warnings: list[str]) -> list[Page]:
    pages: list[Page] = []
    for sec in sorted(layout.get("sections", []), key=lambda s: s.get("ordinal", 0)):
        page = Page(
            id=sec.get("name", ""),
            display_name=sec.get("displayName", sec.get("name", "")),
            ordinal=sec.get("ordinal"),
            is_hidden=sec.get("visibility", 0) == 1,
            width=sec.get("width"),
            height=sec.get("height"),
        )
        for container in sec.get("visualContainers", []):
            try:
                visual = _parse_legacy_container(container, warnings)
                if visual:
                    page.visuals.append(visual)
            except Exception as exc:
                warnings.append(f"page '{page.display_name}': container error: {exc}")
        pages.append(page)
    return pages


def parse_report(report_dir: Path, warnings: list[str]) -> list[Page]:
    """Parse a ``*.Report`` folder into the canonical list of pages."""
    definition_dir = report_dir / "definition"
    if (definition_dir / "pages").is_dir():
        return _parse_enhanced(definition_dir, warnings)
    # legacy: a single layout document with a "sections" array
    for candidate in (report_dir / "report.json", report_dir / "Report" / "Layout"):
        if candidate.exists():
            try:
                layout = json.loads(candidate.read_text(encoding="utf-8-sig"))
                if "sections" in layout:
                    return _parse_legacy(layout, warnings)
            except Exception as exc:
                warnings.append(f"legacy layout parse error: {exc}")
    warnings.append("no recognisable report layout found")
    return []
