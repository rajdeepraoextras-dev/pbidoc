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
import re
from pathlib import Path
from typing import Any, Optional

from ..schemas.model import Page, Visual, Bookmark

SLICER_TYPES = {"slicer", "advancedSlicerVisual"}
GROUP_TYPES = {"visualGroup", "visualContainerGroup"}

class PagesList(list):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bookmarks: list[Bookmark] = []

def _extract_action(visual_obj: dict) -> Optional[dict]:
    try:
        action_arr = visual_obj.get("objects", {}).get("action", [])
        if action_arr:
            props = action_arr[0].get("properties", {})
            atype = props.get("type", {}).get("expr", {}).get("Literal", {}).get("Value")
            if atype:
                atype = str(atype).strip("'")
                target = None
                if atype == "pageNavigation":
                    target = props.get("pageNavigation", {}).get("target", {}).get("expr", {}).get("Literal", {}).get("Value")
                elif atype == "bookmark":
                    target = props.get("bookmark", {}).get("target", {}).get("expr", {}).get("Literal", {}).get("Value")
                if target:
                    target = str(target).strip("'")
                return {"type": atype, "target": target}
    except Exception:
        pass
    return None

def _extract_legacy_action(container: dict) -> Optional[dict]:
    try:
        config = json.loads(container.get("config", "{}"))
        action_arr = config.get("singleVisual", {}).get("vcObjects", {}).get("action", [])
        if action_arr:
            props = action_arr[0].get("properties", {})
            atype = props.get("type", {}).get("expr", {}).get("Literal", {}).get("Value")
            if atype:
                atype = str(atype).strip("'")
                target = None
                if atype == "pageNavigation":
                    target = props.get("pageNavigation", {}).get("target", {}).get("expr", {}).get("Literal", {}).get("Value")
                elif atype == "bookmark":
                    target = props.get("bookmark", {}).get("target", {}).get("expr", {}).get("Literal", {}).get("Value")
                if target:
                    target = str(target).strip("'")
                return {"type": atype, "target": target}
    except Exception:
        pass
    return None


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


# A bare column dragged onto a visual with an implicit aggregation ("Sum",
# "Count", "Average", ...) gets a queryRef like "Sum('HR Data'.BadHires)" --
# the wrapping function name, not the field name. Only used as a last-resort
# fallback (see below): the structured ``field`` object gives the clean
# entity/property pair directly and is always tried first.
_AGG_QUERYREF_WRAPPER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\((.+)\)$")


_BRACKET_FIELD_RE = re.compile(r"^'?([^'\[\]]+)'?\[([^\]]+)\]$")


def _clean_query_ref(ref: str, aliases: dict[str, str] | None = None) -> str:
    m = _AGG_QUERYREF_WRAPPER_RE.match(ref.strip())
    if m and m.group(1).count("(") == m.group(1).count(")"):
        ref = m.group(1)
    ref = ref.strip()
    bracket = _BRACKET_FIELD_RE.match(ref)
    if bracket:
        ref = f"{bracket.group(1)}.{bracket.group(2)}"
    elif ref.startswith("[") and ref.endswith("]"):
        ref = ref[1:-1]
    if aliases and "." in ref:
        prefix, leaf = ref.split(".", 1)
        ref = f"{aliases.get(prefix, prefix)}.{leaf}"
    return ref


def _source_aliases(node) -> dict[str, str]:
    """Return SemanticQuery ``From`` aliases (``s`` -> ``Sales``)."""
    aliases: dict[str, str] = {}
    if isinstance(node, dict):
        name, entity = node.get("Name"), node.get("Entity")
        if isinstance(name, str) and isinstance(entity, str) and name and entity:
            aliases[name] = entity
        for value in node.values():
            aliases.update(_source_aliases(value))
    elif isinstance(node, list):
        for value in node:
            aliases.update(_source_aliases(value))
    return aliases


def _field_from_node(field: dict, aliases: dict[str, str] | None = None) -> str | None:
    aliases = aliases or {}
    for kind in ("Column", "Measure", "Aggregation", "HierarchyLevel"):
        node = field.get(kind)
        if not node:
            continue
        if kind == "Aggregation":
            nested = _field_from_node(node.get("Expression", {}), aliases)
            if nested:
                return nested
            node = node.get("Expression", {}).get("Column", {})
        expression = node.get("Expression", {})
        if kind == "HierarchyLevel":
            expression = expression.get("Hierarchy", {}).get("Expression", expression)
        source_ref = expression.get("SourceRef", {})
        entity = source_ref.get("Entity") or aliases.get(source_ref.get("Source", ""))
        prop = node.get("Property") or node.get("Level")
        if entity and prop:
            return f"{entity}.{prop}"
        if prop:
            return prop
    return None


def _walk_field_refs(node, fields: list[str], aliases: dict[str, str] | None = None) -> None:
    """Find Power BI field references across modern and legacy visual JSON.

    PBIR exports have moved visual bindings between ``query.queryState``,
    ``prototypeQuery.Select`` and nested expression nodes over time. A narrow
    path makes real data visuals look unbound in the wireframe, so this walker
    collects the same structured field object and queryRef shapes wherever
    they appear in the visual payload.
    """
    aliases = aliases or {}
    if isinstance(node, dict):
        resolved = None
        nested_field = node.get("field")
        if isinstance(nested_field, dict):
            resolved = _field_from_node(nested_field, aliases)
            if resolved:
                fields.append(resolved)
        if any(k in node for k in ("Column", "Measure", "Aggregation", "HierarchyLevel")):
            resolved = _field_from_node(node, aliases)
            if resolved:
                fields.append(resolved)
        ref = node.get("queryRef")
        if not resolved and isinstance(ref, str) and ref.strip():
            fields.append(_clean_query_ref(ref, aliases))
        if not resolved and any(k in node for k in ("Column", "Measure", "Aggregation", "HierarchyLevel")):
            name = node.get("Name")
            if isinstance(name, str) and name.strip():
                fields.append(_clean_query_ref(name, aliases))
        for key, value in node.items():
            if resolved and key == "field":
                continue
            _walk_field_refs(value, fields, aliases)
    elif isinstance(node, list):
        for item in node:
            _walk_field_refs(item, fields, aliases)


def _extract_fields(visual_obj: dict) -> list[str]:
    fields: list[str] = []
    try:
        _walk_field_refs(visual_obj, fields, _source_aliases(visual_obj))
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
        action=_extract_action(visual_obj),
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
    
    dt_fields = []
    try:
        binding = meta.get("pageBinding", {})
        if binding.get("type") == "Drillthrough":
            targets = binding.get("drillthrough", {}).get("drillthroughTarget", {}).get("fields", [])
            for t_obj in targets:
                for kind in ("Column", "Measure"):
                    node = t_obj.get(kind)
                    if node:
                        ent = node.get("Expression", {}).get("SourceRef", {}).get("Entity")
                        prop = node.get("Property")
                        if ent and prop:
                            dt_fields.append(f"{ent}.{prop}")
    except Exception:
        pass
    page.drillthrough_fields = dt_fields

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
    payloads: list[dict | list] = [sv]
    for key in ("query", "dataTransforms", "filters"):
        payload = container.get(key)
        if isinstance(payload, str) and payload.strip():
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        if isinstance(payload, (dict, list)):
            payloads.append(payload)
    aliases: dict[str, str] = {}
    for payload in payloads:
        aliases.update(_source_aliases(payload))
    fields: list[str] = []
    try:
        for payload in payloads:
            _walk_field_refs(payload, fields, aliases)
    except Exception:
        pass
    seen: set[str] = set()
    fields = [f for f in fields if not (f in seen or seen.add(f))]
    return Visual(
        id=config.get("name", "unknown"),
        type=vtype,
        title=title,
        x=container.get("x"), y=container.get("y"), z=container.get("z"),
        width=container.get("width"), height=container.get("height"),
        fields=fields,
        is_slicer=vtype in SLICER_TYPES,
        action=_extract_legacy_action(container),
    )


def _parse_legacy(layout: dict, warnings: list[str]) -> PagesList:
    pages = PagesList()
    
    # Parse bookmarks
    bookmarks = []
    for rb in layout.get("bookmarks", []):
        name = rb.get("displayName") or rb.get("name")
        if name:
            target = rb.get("targetSectionName")
            bookmarks.append(Bookmark(name=name, target_page=target))
    pages.bookmarks = bookmarks

    for sec in sorted(layout.get("sections", []), key=lambda s: s.get("ordinal", 0)):
        # Parse drillthrough fields if any
        dt_fields = []
        try:
            config = json.loads(sec.get("config", "{}"))
            # Some reports store drillthrough in section config under drillthroughFilter or similar
            dt_filter = config.get("drillthroughFilter", {})
            for proj in dt_filter.get("projections", []):
                for kind in ("Column", "Measure"):
                    node = proj.get("field", {}).get(kind)
                    if node:
                        ent = node.get("Expression", {}).get("SourceRef", {}).get("Entity")
                        prop = node.get("Property")
                        if ent and prop:
                            dt_fields.append(f"{ent}.{prop}")
        except Exception:
            pass

        page = Page(
            id=sec.get("name", ""),
            display_name=sec.get("displayName", sec.get("name", "")),
            ordinal=sec.get("ordinal"),
            is_hidden=sec.get("visibility", 0) == 1,
            width=sec.get("width"),
            height=sec.get("height"),
            drillthrough_fields=dt_fields,
            is_drillthrough=len(dt_fields) > 0 or sec.get("pageBinding", {}).get("type") == "Drillthrough"
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


def parse_report(report_dir: Path, warnings: list[str]) -> PagesList:
    """Parse a ``*.Report`` folder into the canonical list of pages and bookmarks."""
    definition_dir = report_dir / "definition"
    if (definition_dir / "pages").is_dir():
        # Enhanced PBIR
        pages = PagesList(_parse_enhanced(definition_dir, warnings))
        
        # Read bookmarks if present
        bookmarks_file = definition_dir / "bookmarks.json"
        if bookmarks_file.exists():
            try:
                data = json.loads(bookmarks_file.read_text(encoding="utf-8-sig"))
                b_list = []
                for b_obj in data.get("bookmarks", []):
                    name = b_obj.get("displayName") or b_obj.get("name")
                    if name:
                        b_list.append(Bookmark(name=name, target_page=b_obj.get("targetPage")))
                pages.bookmarks = b_list
            except Exception as exc:
                warnings.append(f"bookmarks.json parse error: {exc}")
        return pages
        
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
    return PagesList()
