"""Command-line interface.

    pbicompass parse <file.pbip | project_dir | file.pbix> [-o model.json]

Prints a human-readable summary and optionally writes the canonical
``model.json``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

from .agents import generate_document, get_client
from .agents.context import build_job_context
from .agents.generators import DOCUMENT_TYPES
from .parsers import detect_and_parse
from .render import pandoc, registry
from .render.audit import _top_cluster as _audit_top_cluster
from .schemas.model import SemanticModel


def _print_summary(model: SemanticModel) -> None:
    c = model.meta.counts
    print(f"Report:        {model.report_name}")
    print(f"Model:         {model.model_name or '(unnamed)'}")
    print(f"Source format: {model.meta.source_format}")
    print(
        "Counts:        "
        f"{c.get('tables', 0)} tables, "
        f"{c.get('columns', 0)} columns, "
        f"{c.get('measures', 0)} measures, "
        f"{c.get('relationships', 0)} relationships, "
        f"{c.get('roles', 0)} roles, "
        f"{c.get('pages', 0)} pages, "
        f"{c.get('visuals', 0)} visuals"
    )
    if model.data_sources:
        print("Data sources:")
        for ds in model.data_sources:
            target = ds.server or ds.detail or ""
            db = f"/{ds.database}" if ds.database else ""
            print(f"  - {ds.type}: {target}{db}")
    if model.roles:
        print("RLS roles:")
        for r in model.roles:
            tp = ", ".join(p.table for p in r.table_permissions) or "(no filters)"
            print(f"  - {r.name} [{r.model_permission or 'read'}] -> {tp}")
    if model.pages:
        print("Pages:")
        for p in model.pages:
            flags = []
            if p.is_hidden:
                flags.append("hidden")
            if p.is_drillthrough:
                flags.append("drillthrough")
            suffix = f" ({', '.join(flags)})" if flags else ""
            print(f"  - {p.display_name}: {len(p.visuals)} visuals{suffix}")
    if model.meta.warnings:
        print(f"Warnings ({len(model.meta.warnings)}):")
        for w in model.meta.warnings:
            print(f"  ! {w}")


def _write_hub(model: SemanticModel, docs: dict, out_path: Path, ext: str, *, quiet: bool) -> None:
    """Write a documentation hub (``{stem}.index.html``) linking every
    document just generated, using the exact filenames the multi-document
    ``-o`` path above just wrote them under. The hosted service builds the
    equivalent hub in ``worker.py`` using fixed ``{type}.html`` names inside
    its zip bundle instead."""
    from .render._shared import format_timestamp
    from .render.hub import hub_stats, render_hub

    entries = [
        {"type": dtype, "href": out_path.with_name(f"{out_path.stem}.{dtype}{ext}").name,
         "stats": hub_stats(dtype, doc)}
        for dtype, doc in docs.items()
    ]
    health_score = None
    audit_doc = docs.get("audit")
    if audit_doc is not None:
        health_score = {"overall": audit_doc.health.overall, "band": audit_doc.health.band}

    hub_html = render_hub(
        entries, report_name=model.report_name,
        generated_at=format_timestamp(model.meta.generated_at), health_score=health_score,
    )
    hub_path = out_path.with_name(f"{out_path.stem}.index.html")
    hub_path.write_text(hub_html, encoding="utf-8")
    if not quiet:
        print(f"Wrote {hub_path} (hub)", file=sys.stderr)


def _write_bundle(model: SemanticModel, docs: dict, document_types: list[str],
                  out_path: Optional[Path], enrichment_data: dict, *,
                  enrichment_file_used: bool, quiet: bool) -> int:
    """5.7: render every format for every requested document type, plus
    ``model.json`` and (with ``--enrich``) the enrichment skeleton, into one
    zip — the CLI's own bundle, alongside the hosted service's equivalent
    (``service/worker.py``, gated on a multi-document job there)."""
    from . import enrichment as enrichment_mod
    from .render._shared import format_timestamp
    from .render.hub import doc_switcher_links, hub_stats, render_hub

    multi = len(document_types) > 1
    html_filenames = {d: f"{d}.html" for d in document_types}
    outputs: dict[str, bytes] = {}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for dtype, doc in docs.items():
            renderers = registry.RENDERERS[dtype]
            doc_links = doc_switcher_links(document_types, dtype, html_filenames, "index.html") if multi else None
            outputs[f"{dtype}.md"] = renderers["md"](doc).encode("utf-8")
            outputs[f"{dtype}.json"] = doc.to_json().encode("utf-8")
            outputs[f"{dtype}.html"] = renderers["html"](
                doc, doc_links=doc_links, sibling_hrefs=html_filenames if multi else None,
            ).encode("utf-8")

            docx_path = tmp / f"out.{dtype}.docx"
            renderers["docx"](doc, docx_path)
            outputs[f"{dtype}.docx"] = docx_path.read_bytes()

            if pandoc.weasyprint_available():
                try:
                    pdf_path = tmp / f"out.{dtype}.pdf"
                    pandoc.html_to_pdf(outputs[f"{dtype}.html"].decode("utf-8"), pdf_path)
                    outputs[f"{dtype}.pdf"] = pdf_path.read_bytes()
                except pandoc.PandocError:
                    pass

    if multi:
        entries = [
            {"type": dtype, "href": html_filenames[dtype], "stats": hub_stats(dtype, doc)}
            for dtype, doc in docs.items()
        ]
        health_score = None
        audit_doc = docs.get("audit")
        if audit_doc is not None:
            health_score = {"overall": audit_doc.health.overall, "band": audit_doc.health.band}
        hub_html = render_hub(
            entries, report_name=model.report_name,
            generated_at=format_timestamp(model.meta.generated_at), health_score=health_score,
        )
        outputs["index.html"] = hub_html.encode("utf-8")

    outputs["model.json"] = model.to_json().encode("utf-8")
    if enrichment_file_used:
        outputs["enrichment.yaml"] = enrichment_mod.generate_enrichment_template(
            model, previous=enrichment_data,
        ).encode("utf-8")

    zip_path = out_path or Path(f"{_safe_stem(model.report_name)}-documentation.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in outputs.items():
            zf.writestr(name, data)

    if not quiet:
        print(f"Wrote {zip_path} (bundle: {', '.join(sorted(outputs))})", file=sys.stderr)
    return 0


def _safe_stem(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name or "").strip("_")
    return cleaned or "documentation"


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 on the console so non-Latin-1 content (e.g. "↔" in model
    # risks, or Unicode table/column names) doesn't crash on Windows cp1252.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="pbicompass", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_parse = sub.add_parser("parse", help="Extract metadata to the canonical model.json")
    p_parse.add_argument("path", type=Path, help=".pbip file, project directory, or .pbix")
    p_parse.add_argument("-o", "--out", type=Path, help="Write model.json to this path")
    p_parse.add_argument("--compact", action="store_true", help="Minified JSON output")
    p_parse.add_argument("--quiet", action="store_true", help="Suppress the summary")
    p_parse.add_argument("--stats", action="store_true",
                       help=".pbix only: also read VertiPaq aggregate stats (column cardinality/size). Opt-in — never row-level data.")

    p_gen = sub.add_parser("generate", help="Parse a file and generate documentation")
    p_gen.add_argument("path", type=Path, help=".pbip file, project directory, or .pbix")
    p_gen.add_argument("-o", "--out", type=Path, help="Output path (.md or .json by extension)")
    p_gen.add_argument("--stats", action="store_true",
                       help=".pbix only: also read VertiPaq aggregate stats (column cardinality/size). Opt-in — never row-level data.")
    p_gen.add_argument("--rules", type=Path,
                       help="Path to a pbicompass.rules.toml config: disable rule IDs, override "
                            "severities, and set thresholds (audit document only).")
    p_gen.add_argument("--enrich", type=Path,
                       help="Path to an enrichment YAML file (5.1). If it doesn't exist yet, a "
                            "skeleton is written there for you to fill in. If it exists, its "
                            "measure/column descriptions, data-source/role details, and rule "
                            "overrides are applied, its report metadata becomes the default for "
                            "--owner/--author/etc. (explicit flags still win), and the file is "
                            "rewritten afterward so filled-in fields persist across runs.")
    p_gen.add_argument("--diff-against", type=Path,
                       help="A previous model.json to diff against (used with --enrich): computes "
                            "the change log and stores it in the enrichment file's history for "
                            "the 'Changes since last documentation' section.")
    p_gen.add_argument("--bundle", action="store_true",
                       help="Ignore --format/-o's single-file output; instead render every format "
                            "(md/json/html/docx/pdf-if-available) for the requested --document "
                            "type(s), plus model.json and (with --enrich) the enrichment skeleton, "
                            "into one zip (-o names the zip, default '{report}-documentation.zip').")
    p_gen.add_argument("--provider", default="none",
                       help="LLM provider: 'none' (deterministic, default), 'anthropic', 'gemini', 'cohere', "
                            "or 'meshapi' (https://developers.meshapi.ai — one API key routes to 1000+ models "
                            "across providers; use a 'provider/model-name' --model id, e.g. "
                            "'deepseek/deepseek-v4-flash', the default — also settable via the "
                            "MESHAPI_MODEL env var)")
    p_gen.add_argument("--model", default="claude-opus-4-8", help="Model id for the LLM provider")
    p_gen.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"],
                       help="Reasoning effort (quality vs. latency), applied to every "
                            "provider's own native reasoning knob where the configured "
                            "model supports one (Anthropic always; Gemini via thinking "
                            "budget; Cohere/MeshAPI only for reasoning-capable models, "
                            "e.g. --model command-a-reasoning / openai/gpt-5 / "
                            "deepseek/deepseek-v3.2-speciale). Ignored for --provider none.")
    p_gen.add_argument("--document", default="technical", choices=[*DOCUMENT_TYPES, "all"],
                       help="Document type to generate (default: technical — the original documentation). "
                            "'all' generates every document type from a single parse.")
    p_gen.add_argument("--format", choices=["md", "json", "html", "docx", "pdf"],
                       help="Force output format (else inferred from -o suffix)")
    p_gen.add_argument("--owner", help="Report owner (Document Metadata)")
    p_gen.add_argument("--audience", help="Target audience (Document Metadata)")
    p_gen.add_argument("--refresh", help="Refresh schedule (Document Metadata)")
    p_gen.add_argument("--version", dest="doc_version", help="Report version (Document Control)")
    p_gen.add_argument("--status", help="Report status (Document Control)")
    p_gen.add_argument("--author", help="Report author (Document Control)")
    p_gen.add_argument("--reviewer", help="Report reviewer/approver (Document Control)")
    p_gen.add_argument("--classification", help="Data classification (Document Control)")
    p_gen.add_argument("--business-decision", help="Primary business decision driven by the report (Executive Summary)")
    p_gen.add_argument("--requirements", help="Business requirements (Business Requirements)")
    p_gen.add_argument("--security-notes", help="RLS/OLS Validation Notes (Row-Level Security)")
    p_gen.add_argument("--refresh-notes", help="Gateway & Performance Details (Refresh & Performance)")
    p_gen.add_argument("--deployment-notes", help="Workspaces & App Deployment details (Deployment)")
    p_gen.add_argument("--access-notes", help="User Group Permissions & Workspace Access (Access & Permissions)")
    p_gen.add_argument("--glossary", help="Glossary of Business terms (Data Dictionary / Glossary)")
    p_gen.add_argument("--assumptions", help="Business assumptions and limitations (Known Issues & Assumptions)")
    p_gen.add_argument("--support-notes", help="Support Escalation, SLA, & Maintenance details (Support & Maintenance)")
    p_gen.add_argument("--plan", default="enterprise", choices=["free", "pro", "enterprise"],
                       help="Plan tier gate for paid AI features (currently: AI-suggested fix "
                            "snippets on the audit document, --document audit/all only). The CLI "
                            "has no account/billing concept, so this defaults to 'enterprise' "
                            "(every paid feature enabled) for self-hosted runs; pass --plan free "
                            "to preview what a free-tier hosted job would omit.")
    p_gen.add_argument("--quiet", action="store_true", help="Suppress warnings/status")

    p_diff = sub.add_parser("diff", help="Compare two model.json files and print a change log (5.2)")
    p_diff.add_argument("old", type=Path, help="Previous model.json")
    p_diff.add_argument("new", type=Path, help="Current model.json")
    p_diff.add_argument("-o", "--out", type=Path, help="Write the change log here instead of stdout")
    p_diff.add_argument("--format", choices=["md", "html"], default="md",
                        help="Output format: 'md' (default, reviewer-ready markdown) or 'html' "
                             "(a self-contained styled 'What Changed' page)")

    p_pub = sub.add_parser(
        "publish",
        help="Publish generated documentation to where your docs live "
             "(Confluence, SharePoint, a Git repo/share) or announce it in Teams")
    p_pub.add_argument("target", choices=["filesystem", "confluence", "sharepoint", "teams"],
                       help="Destination. Credentials come from PBICOMPASS_* env vars "
                            "unless passed explicitly.")
    p_pub.add_argument("path", type=Path,
                       help="A generated .html/.md document, or a bundle directory")
    p_pub.add_argument("--prefer", choices=["html", "md"], default="html",
                       help="Which document kind to publish for page/notification targets "
                            "(default: html)")
    p_pub.add_argument("--dry-run", action="store_true",
                       help="Show exactly what would be published, and send nothing")
    # filesystem / git
    p_pub.add_argument("--dest", help="filesystem: destination directory")
    p_pub.add_argument("--git", action="store_true",
                       help="filesystem: stage and commit the copy (destination must be a repo)")
    p_pub.add_argument("--git-push", action="store_true",
                       help="filesystem: also push the commit (implies --git)")
    p_pub.add_argument("-m", "--message", help="filesystem: git commit message")
    # confluence
    p_pub.add_argument("--url", help="confluence: base URL, e.g. https://site.atlassian.net/wiki")
    p_pub.add_argument("--email", help="confluence: account email")
    p_pub.add_argument("--space", help="confluence: space key")
    p_pub.add_argument("--parent-id", help="confluence: parent page id")
    # sharepoint
    p_pub.add_argument("--drive-id", help="sharepoint: Graph drive id")
    p_pub.add_argument("--folder", help="sharepoint: destination folder (default: PBICompass)")
    # teams
    p_pub.add_argument("--webhook", help="teams: Incoming Webhook URL")
    p_pub.add_argument("--link", help="teams: link to where the docs live")
    # shared secret (confluence API token / sharepoint Graph token). Prefer the
    # env var: a token passed as an argument lands in shell history.
    p_pub.add_argument("--token", help="API token (prefer the PBICOMPASS_* env var)")

    p_serve = sub.add_parser("serve", help="Run the web service (upload UI + API)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")

    p_acct = sub.add_parser("account", help="Manage API accounts (multi-tenant auth)")
    acct_sub = p_acct.add_subparsers(dest="account_cmd", required=True)
    p_ac = acct_sub.add_parser("create", help="Create an account and mint an API key")
    p_ac.add_argument("--tenant", required=True, help="Tenant identifier")
    p_ac.add_argument("--name", default="", help="Human-readable name")
    p_ac.add_argument("--plan", default="free", help="Plan: free, pro, or business")
    p_ac.add_argument("--db", help="SQLite path (default: $PBICOMPASS_DB or pbicompass.db)")
    p_al = acct_sub.add_parser("list", help="List accounts")
    p_al.add_argument("--db", help="SQLite path (default: $PBICOMPASS_DB or pbicompass.db)")
    p_ar = acct_sub.add_parser("revoke", help="Revoke an account (its API key stops working immediately)")
    p_ar.add_argument("--id", required=True, help="Account id (see 'account list')")
    p_ar.add_argument("--db", help="SQLite path (default: $PBICOMPASS_DB or pbicompass.db)")
    p_ab = acct_sub.add_parser("backup", help="Snapshot accounts/keys/quotas to a JSON file")
    p_ab.add_argument("--out", required=True, type=Path, help="Output file path")
    p_ab.add_argument("--db", help="SQLite path (default: $PBICOMPASS_DB or pbicompass.db)")
    p_arr = acct_sub.add_parser(
        "restore",
        help="Restore accounts/keys/quotas from a JSON file (see 'account backup'). "
             "Point --db at an empty/scratch database for a restore drill.",
    )
    p_arr.add_argument("--in", dest="in_path", required=True, type=Path, help="Snapshot file to restore")
    p_arr.add_argument("--db", help="SQLite path (default: $PBICOMPASS_DB or pbicompass.db)")

    args = parser.parse_args(argv)

    if args.command == "parse":
        try:
            model = detect_and_parse(args.path, include_stats=args.stats)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not args.quiet:
            _print_summary(model)
        if args.out:
            args.out.write_text(
                model.to_json(indent=None if args.compact else 2),
                encoding="utf-8",
            )
            if not args.quiet:
                print(f"\nWrote {args.out}")
        return 0

    if args.command == "diff":
        import json

        from .agents.model_diff import (
            compute_model_diff,
            generate_change_log_markdown,
            render_change_summary_html,
        )

        try:
            old = json.loads(args.old.read_text(encoding="utf-8"))
            new = json.loads(args.new.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        diff = compute_model_diff(old, new)
        if args.format == "html":
            title = f"What Changed — {_safe_stem(new.get('report_name') or args.new.stem)}"
            output = render_change_summary_html(diff, title=title)
        else:
            output = generate_change_log_markdown(diff)
        if args.out:
            args.out.write_text(output + "\n", encoding="utf-8")
            total = sum(diff.get("summary", {}).values())
            print(f"Wrote {args.out} ({total} change(s))", file=sys.stderr)
        else:
            print(output)
        return 0

    if args.command == "publish":
        from .publish import PublishError, collect_documents, get_publisher

        try:
            publisher = get_publisher(
                args.target,
                dest=args.dest, git=args.git, git_push=args.git_push,
                commit_message=args.message,
                url=args.url, email=args.email, token=args.token,
                space=args.space, parent_id=args.parent_id,
                drive_id=args.drive_id, folder=args.folder,
                webhook=args.webhook, link=args.link,
                prefer=args.prefer,
            )
        except PublishError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if args.dry_run:
            try:
                if args.target in ("confluence", "teams"):
                    items = [d.title for d in collect_documents(args.path, prefer=args.prefer)]
                else:
                    p = args.path
                    items = [p.name] if p.is_file() else sorted(
                        x.name for x in p.iterdir() if x.is_file())
            except PublishError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"Dry run — would publish {len(items)} item(s) to {args.target}:")
            for name in items:
                print(f"  - {name}")
            print("Nothing was sent.")
            return 0

        try:
            result = publisher.publish(args.path)
        except PublishError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(result.summary())
        return 0

    if args.command == "generate":
        # LLM response cache (5.4): on by default for the CLI, off by
        # default for the hosted service (service/worker.py never sets this
        # env var, so it keeps cache.py's off default). Respect an explicit
        # override (a custom path, or "off") from the user's environment.
        os.environ.setdefault("PBICOMPASS_LLM_CACHE", ".pbicompass_cache.db")
        # Score trend (4.5): same on-CLI/off-service default split.
        os.environ.setdefault("PBICOMPASS_SCORE_HISTORY", ".pbicompass_history.json")

        try:
            model = detect_and_parse(args.path, include_stats=args.stats)
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        def _warn(msg: str) -> None:
            # Also appended to model.meta.warnings (Day 2) — a durable,
            # structured record alongside the parse-time warnings already
            # there, so a bundle's model.json carries every generation-time
            # correction (e.g. the consistency pass fixing a contradicted
            # claim) even in --quiet runs where nothing prints to stderr.
            model.meta.warnings.append(msg)
            if not args.quiet:
                print(f"warning: {msg}", file=sys.stderr)

        # Rule suppression/severity/threshold config (4.3 / J.A.3). Invalid
        # TOML is a warning, not a fatal error — the job still runs, just
        # without any overrides applied.
        from .agents import audit_rules
        audit_rules.set_rules_config_path(None)
        audit_rules.set_rules_override_config({})
        if args.rules:
            error = audit_rules.validate_rules_file(args.rules)
            if error:
                _warn(f"{error} — continuing without rule overrides.")
            else:
                audit_rules.set_rules_config_path(args.rules)

        # Enrichment round-trip (5.1). First run with a given --enrich path
        # that doesn't exist yet: bootstrap a skeleton there and stop —
        # nothing to apply. Existing file: apply its measure/column
        # descriptions, data-source/role details, and rule overrides to the
        # model, and use its report metadata as the *default* for
        # --owner/--author/etc. (an explicit flag still wins). The file
        # itself is rewritten at the end of this command so filled-in
        # fields persist and 4.5/5.2's diff history stays current.
        from . import enrichment as enrichment_mod

        enrichment_data: dict = {}
        enrichment_meta: dict = {}
        enrichment_applied = False
        changelog_text: Optional[str] = None
        if args.enrich and not args.enrich.exists():
            try:
                args.enrich.write_text(enrichment_mod.generate_enrichment_template(model), encoding="utf-8")
                _warn(f"No enrichment file at {args.enrich} — wrote a fresh skeleton. "
                      "Fill it in and rerun to apply it.")
            except Exception as exc:
                _warn(f"Could not write enrichment skeleton to {args.enrich}: {exc}")
        elif args.enrich:
            try:
                enrichment_data = enrichment_mod.load_enrichment(args.enrich)
            except ValueError as exc:
                _warn(f"{exc} — continuing without enrichment.")
                enrichment_data = {}
            else:
                enrichment_applied = True
                overridden = enrichment_mod.apply_enrichment(model, enrichment_data)
                enrichment_meta = overridden["metadata"]

                # 5.2: change log / diff history, driven by this same file.
                history = enrichment_data.setdefault("history", {})
                current_fp = enrichment_mod.get_model_fingerprint(model)
                prev_fp = history.get("previous_fingerprint") or ""
                if args.diff_against:
                    try:
                        import json as _json
                        old_dict = _json.loads(args.diff_against.read_text(encoding="utf-8"))
                        diff = enrichment_mod.compute_model_diff(old_dict, model.to_dict())
                        changelog_text = enrichment_mod.generate_change_log_markdown(diff)
                        history["previous_summary"] = changelog_text
                    except Exception as exc:
                        _warn(f"Could not diff against {args.diff_against}: {exc}")
                elif prev_fp and prev_fp != current_fp and history.get("previous_summary"):
                    changelog_text = history["previous_summary"]
                history["previous_fingerprint"] = current_fp

                try:
                    args.enrich.write_text(
                        enrichment_mod.generate_enrichment_template(model, previous=enrichment_data),
                        encoding="utf-8",
                    )
                except Exception as exc:
                    _warn(f"Could not update enrichment file {args.enrich}: {exc}")

        def _meta(flag_value, key):
            return flag_value if flag_value is not None else (enrichment_meta.get(key) or None)

        owner = _meta(args.owner, "owner")
        audience = _meta(args.audience, "target_audience")
        refresh = _meta(args.refresh, "refresh_schedule")
        doc_version = _meta(args.doc_version, "version")
        status = _meta(args.status, "status")
        author = _meta(args.author, "author")
        reviewer = _meta(args.reviewer, "reviewer")
        classification = _meta(args.classification, "classification")
        business_decision = _meta(args.business_decision, "business_decision")
        requirements = _meta(args.requirements, "requirements")
        security_notes = _meta(args.security_notes, "security_notes")
        refresh_notes = _meta(args.refresh_notes, "refresh_notes")
        deployment_notes = _meta(args.deployment_notes, "deployment_notes")
        access_notes = _meta(args.access_notes, "access_notes")
        glossary = _meta(args.glossary, "glossary")
        assumptions = _meta(args.assumptions, "assumptions")
        support_notes = _meta(args.support_notes, "support_notes")

        client = None
        if args.provider not in ("none", "offline", "deterministic"):
            client_kwargs = {"model": args.model, "effort": args.effort}
            try:
                client = get_client(args.provider, **client_kwargs)
            except Exception as exc:
                print(f"error: {args.provider} provider unavailable ({exc})", file=sys.stderr)
                return 1

        from .service.worker import _complete_metadata
        completed_meta = _complete_metadata(model, client, {
            "owner": owner, "audience": audience, "refresh": refresh,
            "version": doc_version, "status": status, "author": author,
            "reviewer": reviewer, "classification": classification,
            "business_decision": business_decision, "requirements": requirements,
            "security_notes": security_notes, "refresh_notes": refresh_notes,
            "deployment_notes": deployment_notes, "access_notes": access_notes,
            "glossary": glossary, "assumptions": assumptions, "support_notes": support_notes,
        }, _warn)
        owner, audience, refresh = (completed_meta[k] for k in ("owner", "audience", "refresh"))
        doc_version, status, author = (completed_meta[k] for k in ("version", "status", "author"))
        reviewer, classification = (completed_meta[k] for k in ("reviewer", "classification"))
        business_decision, requirements = (completed_meta[k] for k in ("business_decision", "requirements"))
        security_notes, refresh_notes = (completed_meta[k] for k in ("security_notes", "refresh_notes"))
        deployment_notes, access_notes = (completed_meta[k] for k in ("deployment_notes", "access_notes"))
        glossary, assumptions, support_notes = (
            completed_meta[k] for k in ("glossary", "assumptions", "support_notes")
        )

        document_types = list(DOCUMENT_TYPES) if args.document == "all" else [args.document]

        # Phase 0: one DAX Translator pass shared by every requested document
        # type — CLI keeps its persistent cache default (no explicit
        # cache_path override; ``LLMResponseCache`` falls back to the
        # ``PBICOMPASS_LLM_CACHE`` env var, same as before this phase).
        ai_context = build_job_context(
            model, client, _warn,
            business_decision=business_decision, target_audience=audience,
            assumptions=assumptions, security_notes=security_notes,
            refresh_notes=refresh_notes, deployment_notes=deployment_notes,
            access_notes=access_notes, support_notes=support_notes,
        ) if client is not None else None

        # Day 8/Day 2: when "audit" is requested alongside any other document
        # type in the same run, generate it first so its Audit Synthesizer
        # clusters (Day 7, technical §16 only) and its deterministic verdicts
        # (Day 2's cross-artifact consistency check, every other doc type) are
        # both available — avoids a second, potentially-inconsistent
        # Synthesizer call and is reused below instead of regenerating
        # "audit" in the main loop.
        # Day 3: the full human intake field set — every generator now
        # accepts it (previously only the technical document did), so it's
        # threaded to all four here rather than the owner/audience/refresh/
        # version/status/classification subset audit/executive/user-guide
        # used to be limited to.
        _meta_kwargs = dict(
            owner=owner, audience=audience, refresh=refresh,
            version=doc_version, status=status, classification=classification,
            author=author, reviewer=reviewer,
            business_decision=business_decision, requirements=requirements,
            security_notes=security_notes, refresh_notes=refresh_notes,
            deployment_notes=deployment_notes, access_notes=access_notes,
            glossary=glossary, assumptions=assumptions, support_notes=support_notes,
        )

        # Day 4: the Requirements Traceability Matrix has no ordering
        # dependency on any other document (unlike top_cluster/audit_verdicts,
        # which need the Audit document to already exist) — model +
        # requirements text + client are all available here, so it's
        # computed once up front and shared with technical/audit/executive
        # rather than each independently re-matching/re-calling the LLM.
        from .agents.traceability import build_requirements_matrix
        requirements_matrix = build_requirements_matrix(
            model, requirements, client, _warn, ai_context=ai_context,
            business_decision=business_decision, target_audience=audience,
            assumptions=assumptions, security_notes=security_notes,
            refresh_notes=refresh_notes, deployment_notes=deployment_notes,
            access_notes=access_notes, support_notes=support_notes,
        )

        pre_audit_doc = None
        if "audit" in document_types and len(document_types) > 1:
            pre_audit_doc = DOCUMENT_TYPES["audit"].generate(
                model, client, **_meta_kwargs,
                on_warning=_warn, ai_context=ai_context, plan=args.plan,
                requirements_matrix=requirements_matrix,
            )
        top_cluster = _audit_top_cluster(pre_audit_doc) if pre_audit_doc is not None else None
        audit_verdicts = None
        if pre_audit_doc is not None:
            from .agents.consistency import build_audit_verdicts
            audit_verdicts = build_audit_verdicts(model, pre_audit_doc)

        def _generate_one(document_type: str):
            if document_type == "audit" and pre_audit_doc is not None:
                return pre_audit_doc
            if document_type == "technical":
                return generate_document(
                    model, client, **_meta_kwargs,
                    on_warning=_warn, ai_context=ai_context, top_cluster=top_cluster,
                    audit_verdicts=audit_verdicts, requirements_matrix=requirements_matrix,
                )
            if document_type == "audit":
                return DOCUMENT_TYPES["audit"].generate(
                    model, client, **_meta_kwargs,
                    on_warning=_warn, ai_context=ai_context, plan=args.plan,
                    requirements_matrix=requirements_matrix,
                )
            if document_type == "executive":
                return DOCUMENT_TYPES["executive"].generate(
                    model, client, **_meta_kwargs,
                    on_warning=_warn, ai_context=ai_context, audit_verdicts=audit_verdicts,
                    requirements_matrix=requirements_matrix,
                )
            return DOCUMENT_TYPES[document_type].generate(
                model, client, **_meta_kwargs,
                on_warning=_warn, ai_context=ai_context, audit_verdicts=audit_verdicts,
            )

        docs = {dtype: _generate_one(dtype) for dtype in document_types}
        from .service.worker import _synchronize_glossary
        _synchronize_glossary(docs)
        for dtype in ("technical", "audit"):
            if changelog_text and dtype in docs:
                docs[dtype].changelog = changelog_text

        # Benchmark-gated Senior Reviewer loop: score the whole bundle,
        # fix-and-rescore until every evaluated check passes (capped), then
        # render. Internal-only telemetry — a failure here never blocks the
        # job and nothing from it reaches the rendered documents.
        try:
            from .agents.reviewer import run_review_loop
            quality = run_review_loop(docs, model, client, _warn, ai_context)
            if not args.quiet:
                print(quality.summary_line(), file=sys.stderr)
                for result in quality.results:
                    if result.get("passed") is False:
                        locations = ", ".join(result.get("locations") or [])
                        suffix = f" Locations: {locations}." if locations else ""
                        print(f"quality issue {result['check_id']}: {result.get('detail', '')}{suffix}",
                              file=sys.stderr)
                for gap in quality.gaps:
                    print(f"quality gap {gap.get('check_id')}: {gap.get('description', '')}",
                          file=sys.stderr)
        except Exception as exc:
            _warn(f"Senior Reviewer: quality pass failed, continuing ({exc})")

        # Optional intake fields are not gate criteria; missing values render
        # as "Not provided". Generated content defects do block the export.
        try:
            from .agents.output_gate import validate_bundle
            gate_filenames = None
            requested_format = args.format or ({".html": "html", ".htm": "html"}.get(
                args.out.suffix.lower()) if args.out else None)
            if len(document_types) > 1 and args.out and requested_format == "html":
                gate_filenames = {
                    dtype: args.out.with_name(f"{args.out.stem}.{dtype}.html").name
                    for dtype in document_types
                }
            validate_bundle(
                docs, model, html_filenames=gate_filenames, ai_context=ai_context,
            )
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if args.bundle:
            return _write_bundle(model, docs, document_types, args.out, enrichment_data,
                                enrichment_file_used=enrichment_applied, quiet=args.quiet)

        suffix_map = {".json": "json", ".md": "md", ".markdown": "md",
                      ".html": "html", ".htm": "html", ".docx": "docx", ".pdf": "pdf"}
        ext_map = {"json": ".json", "md": ".md", "html": ".html", "docx": ".docx", "pdf": ".pdf"}
        fmt = args.format or (suffix_map.get(args.out.suffix.lower(), "md") if args.out else "md")

        if fmt in ("docx", "pdf") and not args.out:
            print(f"error: --format {fmt} is a binary format and requires -o <path>", file=sys.stderr)
            return 1

        multi = len(document_types) > 1
        # Cross-document doc-switcher links (2.7) only make sense when every
        # sibling's filename is known up front — true here (the CLI's
        # multi-doc naming is deterministic: "<stem>.<type>.html" per file).
        # Label + relative href per sibling, plus the hub.
        html_filenames: dict[str, str] = {}
        if multi and args.out and fmt == "html":
            html_filenames = {d: args.out.with_name(f"{args.out.stem}.{d}.html").name for d in document_types}

        for dtype, doc in docs.items():
            renderers = registry.RENDERERS[dtype]
            out_path = args.out
            if out_path and multi:
                out_path = out_path.with_name(f"{out_path.stem}.{dtype}{ext_map[fmt]}")
            doc_links = None
            if html_filenames:
                from .render.hub import doc_switcher_links
                doc_links = doc_switcher_links(
                    document_types, dtype, html_filenames, f"{args.out.stem}.index.html",
                )
            try:
                if fmt in ("json", "md", "html"):
                    content = {
                        "json": doc.to_json,
                        "md": lambda: renderers["md"](doc),
                        "html": lambda: renderers["html"](doc, doc_links=doc_links,
                                                          sibling_hrefs=html_filenames or None),
                    }[fmt]()
                    if out_path:
                        out_path.write_text(content, encoding="utf-8")
                    else:
                        if multi:
                            print(f"=== {dtype.upper()} ===")
                        print(content)
                elif fmt == "docx":
                    renderers["docx"](doc, out_path)
                elif fmt == "pdf":
                    pandoc.html_to_pdf(renderers["html"](doc), out_path)
            except pandoc.PandocError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            except Exception as exc:
                print(f"error: writing {fmt} for '{dtype}': {exc}", file=sys.stderr)
                return 1

            if out_path and not args.quiet:
                print(f"Wrote {out_path} ({fmt})", file=sys.stderr)

        if multi and fmt == "html" and args.out:
            _write_hub(model, docs, args.out, ext_map[fmt], quiet=args.quiet)
        return 0

    if args.command == "serve":
        try:
            import uvicorn
        except ImportError:
            print('error: the web service needs extra deps. Install with: '
                  'pip install -e ".[service]"', file=sys.stderr)
            return 1
        print(f"PBICompass service running on http://{args.host}:{args.port}", file=sys.stderr)
        uvicorn.run("pbicompass.service.app:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    if args.command == "account":
        from .service.accounts import AccountStore
        db = args.db or os.environ.get("PBICOMPASS_DB", "pbicompass.db")
        accounts = AccountStore(db)
        try:
            if args.account_cmd == "create":
                try:
                    acct, key = accounts.create_account(args.tenant, args.name, args.plan)
                except ValueError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return 1
                print(f"Account created for tenant '{acct.tenant}' (plan: {acct.plan}, db: {db}).")
                print("API key (shown once — store it securely):\n")
                print(f"    {key}\n")
                return 0
            if args.account_cmd == "list":
                accts = accounts.list_accounts()
                if not accts:
                    print("No accounts yet.")
                    return 0
                for a in accts:
                    print(f"{a.id}  {a.tenant:<22} {a.plan:<12} {a.name}")
                return 0
            if args.account_cmd == "revoke":
                if not accounts.revoke_account(args.id):
                    print(f"error: no account with id '{args.id}'", file=sys.stderr)
                    return 1
                print(f"Revoked account '{args.id}'.")
                return 0
            if args.account_cmd == "backup":
                from .service.db_backup import backup_to_file
                count = backup_to_file(accounts, args.out)
                print(f"Backed up {count} account(s) (db: {db}) to {args.out}")
                return 0
            if args.account_cmd == "restore":
                from .service.db_backup import restore_from_file
                count = restore_from_file(accounts, args.in_path)
                print(f"Restored {count} account(s) from {args.in_path} into {db}")
                return 0
        finally:
            accounts.close()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
