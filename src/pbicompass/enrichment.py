from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from .schemas.model import SemanticModel


def generate_enrichment_template(model: SemanticModel, previous: Optional[dict] = None) -> str:
    """Generate a template YAML string for the user to enrich report metadata.

    ``previous`` is the last-loaded enrichment dict (``load_enrichment``'s
    return value), if any. Passing it back in is what makes this a *round
    trip* rather than always emitting a blank skeleton: the report-level
    metadata (owner, classification, ...), per-source latency, rules
    suppression/severity overrides, and diff history have no home on
    :class:`SemanticModel` at all — they only ever exist inside the
    enrichment file — so without ``previous`` they'd reset to blank on every
    regeneration. Measure/column descriptions and data-source auth/role
    descriptions *do* have a model-side home (set by :func:`apply_enrichment`
    before this is called), so those are always read live from ``model``.

    Lazily imports PyYAML to preserve the core's zero-dependency property.
    """
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required for enrichment features. "
            "Install with 'pip install PyYAML' or 'pip install pbicompass[enrich]'."
        )

    prev = previous or {}
    prev_meta = prev.get("metadata") if isinstance(prev.get("metadata"), dict) else {}
    prev_ds_by_loc = {
        d.get("location"): d for d in (prev.get("data_sources") or []) if isinstance(d, dict)
    }
    prev_roles_by_name = {
        r.get("name"): r for r in (prev.get("roles") or []) if isinstance(r, dict)
    }
    prev_rules = prev.get("rules_config") if isinstance(prev.get("rules_config"), dict) else {}
    prev_history = prev.get("history") if isinstance(prev.get("history"), dict) else {}

    data = {
        "metadata": {
            "owner": prev_meta.get("owner", ""),
            "refresh_schedule": prev_meta.get("refresh_schedule", ""),
            "target_audience": prev_meta.get("target_audience", ""),
            "version": prev_meta.get("version", "1.0.0"),
            "status": prev_meta.get("status", "Draft"),
            "author": prev_meta.get("author", ""),
            "reviewer": prev_meta.get("reviewer", ""),
            "classification": prev_meta.get("classification", "Internal"),
            "business_decision": prev_meta.get("business_decision", ""),
            "requirements": prev_meta.get("requirements", ""),
            "security_notes": prev_meta.get("security_notes", ""),
            "refresh_notes": prev_meta.get("refresh_notes", ""),
            "deployment_notes": prev_meta.get("deployment_notes", ""),
            "access_notes": prev_meta.get("access_notes", ""),
            "glossary": prev_meta.get("glossary", ""),
            "assumptions": prev_meta.get("assumptions", ""),
            "support_notes": prev_meta.get("support_notes", ""),
        },
        "data_sources": [
            {
                "type": ds.type,
                "location": (ds.detail or ds.server or ""),
                "authentication_status": ds.authentication_status
                or prev_ds_by_loc.get(ds.detail or ds.server or "", {}).get(
                    "authentication_status", "Not specified"
                ),
                "latency_minutes": prev_ds_by_loc.get(ds.detail or ds.server or "", {}).get(
                    "latency_minutes", 0
                ),
            }
            for ds in model.data_sources
        ],
        "roles": [
            {
                "name": r.name,
                "members_description": r.members_description
                or prev_roles_by_name.get(r.name, {}).get("members_description", ""),
                "filter_logic_explanation": r.filter_logic_explanation
                or prev_roles_by_name.get(r.name, {}).get("filter_logic_explanation", ""),
            }
            for r in model.roles
        ],
        "measure_descriptions": {
            m.name: m.description or ""
            for m in model.all_measures()
        },
        "column_descriptions": {
            t.name: {
                c.name: c.description or ""
                for c in t.columns if not c.is_hidden
            }
            for t in model.tables if any(not c.is_hidden for c in t.columns)
        },
        "rules_config": {
            "suppressed_rules": prev_rules.get("suppressed_rules", []),
            "severity_overrides": prev_rules.get("severity_overrides", {}),
        },
        "history": {
            "previous_fingerprint": prev_history.get("previous_fingerprint", ""),
            "previous_summary": prev_history.get("previous_summary", ""),
            "score_history": prev_history.get("score_history", []),
        }
    }

    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


def load_enrichment(path: Path) -> dict:
    """Load the enrichment YAML file.
    
    Lazily imports PyYAML.
    """
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required for enrichment features. "
            "Install with 'pip install PyYAML' or 'pip install pbicompass[enrich]'."
        )

    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
            return content if isinstance(content, dict) else {}
    except Exception as exc:
        raise ValueError(f"Failed to parse enrichment file {path}: {exc}")


def apply_enrichment(model: SemanticModel, enrichment: dict) -> dict[str, Any]:
    """Overlay enrichment data onto the semantic model, updating properties.
    
    Returns a dict detailing what was overridden.
    """
    overridden = {
        "metadata": {},
        "measures": set(),
        "columns": set(),
        "data_sources": {},
        "roles": {}
    }
    
    # 1. Document/Report Metadata. These fields (owner, classification, ...)
    # have no home on SemanticModel/ModelMeta — they're generator inputs
    # (the same ones --owner/--author/etc. supply on the CLI), not parsed
    # facts. The caller (CLI/service) merges ``overridden["metadata"]`` into
    # its generator kwargs; here we only record *which* fields were
    # human-supplied, since that list drives the provenance badges
    # (``model.meta.overridden_fields`` -> ``DocMetadataCore.overridden_fields``
    # -> ``render._shared.section_provenance``).
    meta = enrichment.get("metadata", {})
    if isinstance(meta, dict):
        overridden["metadata"] = {k: v for k, v in meta.items() if v}
    model.meta.overridden_fields = list(overridden["metadata"].keys())

    # 2. Measure overrides
    meas_descs = enrichment.get("measure_descriptions", {})
    if isinstance(meas_descs, dict):
        for m in model.all_measures():
            if m.name in meas_descs and meas_descs[m.name]:
                m.description = meas_descs[m.name]
                m.provenance = "Human-provided"
                overridden["measures"].add(m.name)

    # 3. Column overrides
    col_descs = enrichment.get("column_descriptions", {})
    if isinstance(col_descs, dict):
        for t in model.tables:
            if t.name in col_descs and isinstance(col_descs[t.name], dict):
                t_cols = col_descs[t.name]
                for c in t.columns:
                    if c.name in t_cols and t_cols[c.name]:
                        c.description = t_cols[c.name]
                        c.provenance = "Human-provided"
                        overridden["columns"].add(f"{t.name}[{c.name}]")

    # 4. Data source credentials & authentication status
    ds_configs = enrichment.get("data_sources", [])
    if isinstance(ds_configs, list):
        for ds_conf in ds_configs:
            if isinstance(ds_conf, dict):
                loc = ds_conf.get("location")
                if loc:
                    overridden["data_sources"][loc] = ds_conf
                    # Map to model's data_sources database
                    for ds in model.data_sources:
                        if ds.detail == loc or ds.server == loc:
                            ds.authentication_status = ds_conf.get("authentication_status", ds.authentication_status)

    # 5. RLS Roles & Permission details
    role_configs = enrichment.get("roles", [])
    if isinstance(role_configs, list):
        for r_conf in role_configs:
            if isinstance(r_conf, dict):
                name = r_conf.get("name")
                if name:
                    overridden["roles"][name] = r_conf
                    # Map RLS roles
                    for r in model.roles:
                        if r.name == name:
                            r.members_description = r_conf.get("members_description", "")
                            r.filter_logic_explanation = r_conf.get("filter_logic_explanation", "")

    # 6. Rules config override
    rules_config = enrichment.get("rules_config", {})
    if isinstance(rules_config, dict):
        from .agents.audit_rules import set_rules_override_config
        set_rules_override_config(rules_config)

    return overridden


def get_model_fingerprint(model: SemanticModel) -> str:
    """Generate a deterministic hash of the model schema structure."""
    import hashlib
    import json
    data = {
        "tables": [
            {
                "name": t.name,
                "columns": sorted([c.name for c in t.columns]),
                "measures": sorted([m.name for m in t.measures])
            }
            for t in sorted(model.tables, key=lambda x: x.name)
        ],
        "relationships": sorted([
            f"{r.from_table}[{r.from_column}] -> {r.to_table}[{r.to_column}]"
            for r in model.relationships
        ])
    }
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def compute_model_diff(old: dict, new: dict) -> dict:
    """Compare two semantic model dict schemas and return structural delta."""
    diff = {
        "added_tables": [],
        "removed_tables": [],
        "changed_tables": {},
        "added_measures": [],
        "removed_measures": [],
        "changed_measures": {},
        "added_relationships": [],
        "removed_relationships": []
    }
    
    old_tables = {t["name"]: t for t in old.get("tables", [])}
    new_tables = {t["name"]: t for t in new.get("tables", [])}
    
    for name, new_t in new_tables.items():
        if name not in old_tables:
            diff["added_tables"].append(name)
        else:
            old_t = old_tables[name]
            old_cols = {c["name"]: c for c in old_t.get("columns", [])}
            new_cols = {c["name"]: c for c in new_t.get("columns", [])}
            
            added_c = [c for c in new_cols if c not in old_cols]
            removed_c = [c for c in old_cols if c not in new_cols]
            changed_c = []
            
            for col_name, n_col in new_cols.items():
                if col_name in old_cols:
                    o_col = old_cols[col_name]
                    if o_col.get("expression") != n_col.get("expression") or o_col.get("is_calculated") != n_col.get("is_calculated"):
                        changed_c.append(col_name)
            
            if added_c or removed_c or changed_c:
                diff["changed_tables"][name] = {
                    "added_columns": added_c,
                    "removed_columns": removed_c,
                    "changed_columns": changed_c
                }
                
    for name in old_tables:
        if name not in new_tables:
            diff["removed_tables"].append(name)
            
    old_meas = {}
    for t in old.get("tables", []):
        for m in t.get("measures", []):
            old_meas[m["name"]] = m
            
    new_meas = {}
    for t in new.get("tables", []):
        for m in t.get("measures", []):
            new_meas[m["name"]] = m
            
    for name, n_m in new_meas.items():
        if name not in old_meas:
            diff["added_measures"].append(name)
        else:
            o_m = old_meas[name]
            if o_m.get("expression") != n_m.get("expression"):
                diff["changed_measures"][name] = {
                    "old_expression": o_m.get("expression"),
                    "new_expression": n_m.get("expression")
                }
                
    for name in old_meas:
        if name not in new_meas:
            diff["removed_measures"].append(name)
            
    old_rels = {f"{r.get('from_table')}[{r.get('from_column')}] -> {r.get('to_table')}[{r.get('to_column')}]" for r in old.get("relationships", [])}
    new_rels = {f"{r.get('from_table')}[{r.get('from_column')}] -> {r.get('to_table')}[{r.get('to_column')}]" for r in new.get("relationships", [])}
    
    diff["added_relationships"] = sorted(list(new_rels - old_rels))
    diff["removed_relationships"] = sorted(list(old_rels - new_rels))
    
    return diff


def generate_change_log_markdown(diff: dict) -> str:
    """Produce a formatted markdown change log from the diff."""
    parts = []
    if diff.get("added_tables"):
        parts.append(f"- **Added Tables:** {', '.join(diff['added_tables'])}")
    if diff.get("removed_tables"):
        parts.append(f"- **Removed Tables:** {', '.join(diff['removed_tables'])}")
        
    for t_name, t_diff in sorted(diff.get("changed_tables", {}).items()):
        sub = []
        if t_diff.get("added_columns"):
            sub.append(f"added columns: {', '.join(t_diff['added_columns'])}")
        if t_diff.get("removed_columns"):
            sub.append(f"removed columns: {', '.join(t_diff['removed_columns'])}")
        if t_diff.get("changed_columns"):
            sub.append(f"modified columns: {', '.join(t_diff['changed_columns'])}")
        if sub:
            parts.append(f"- **Table '{t_name}':** {'; '.join(sub)}")
            
    if diff.get("added_measures"):
        parts.append(f"- **Added Measures:** {', '.join(diff['added_measures'])}")
    if diff.get("removed_measures"):
        parts.append(f"- **Removed Measures:** {', '.join(diff['removed_measures'])}")
    if diff.get("changed_measures"):
        parts.append(f"- **Modified Measures (Logic changed):** {', '.join(sorted(diff['changed_measures'].keys()))}")
        
    if diff.get("added_relationships"):
        parts.append(f"- **Added Relationships:** {', '.join(diff['added_relationships'])}")
    if diff.get("removed_relationships"):
        parts.append(f"- **Removed Relationships:** {', '.join(diff['removed_relationships'])}")
        
    if not parts:
        return "No structural or logic changes detected since the last documentation run."
    return "\n".join(parts)
