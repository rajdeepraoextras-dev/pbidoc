"""Command-line interface.

    pbicompass parse <file.pbip | project_dir | file.pbix> [-o model.json]

Prints a human-readable summary and optionally writes the canonical
``model.json``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .agents import generate_document, get_client
from .agents.generators import DOCUMENT_TYPES
from .parsers import detect_and_parse
from .render import pandoc, registry
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
    p_gen.add_argument("--provider", default="none",
                       help="LLM provider: 'none' (deterministic, default), 'anthropic', 'gemini', or 'cohere'")
    p_gen.add_argument("--model", default="claude-opus-4-8", help="Model id for the LLM provider")
    p_gen.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"],
                       help="Anthropic thinking effort (quality vs. latency). Ignored for --provider gemini/cohere/none.")
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
    p_gen.add_argument("--quiet", action="store_true", help="Suppress warnings/status")

    p_serve = sub.add_parser("serve", help="Run the web service (upload UI + API)")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")

    p_acct = sub.add_parser("account", help="Manage API accounts (multi-tenant auth)")
    acct_sub = p_acct.add_subparsers(dest="account_cmd", required=True)
    p_ac = acct_sub.add_parser("create", help="Create an account and mint an API key")
    p_ac.add_argument("--tenant", required=True, help="Tenant identifier")
    p_ac.add_argument("--name", default="", help="Human-readable name")
    p_ac.add_argument("--plan", default="free", help="Plan: free, pro, or enterprise")
    p_ac.add_argument("--db", help="SQLite path (default: $PBICOMPASS_DB or pbicompass.db)")
    p_al = acct_sub.add_parser("list", help="List accounts")
    p_al.add_argument("--db", help="SQLite path (default: $PBICOMPASS_DB or pbicompass.db)")
    p_ar = acct_sub.add_parser("revoke", help="Revoke an account (its API key stops working immediately)")
    p_ar.add_argument("--id", required=True, help="Account id (see 'account list')")
    p_ar.add_argument("--db", help="SQLite path (default: $PBICOMPASS_DB or pbicompass.db)")

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
            if not args.quiet:
                print(f"warning: {msg}", file=sys.stderr)

        client = None
        if args.provider not in ("none", "offline", "deterministic"):
            client_kwargs = {"model": args.model}
            if args.provider in ("anthropic", "claude"):
                client_kwargs["effort"] = args.effort
            try:
                client = get_client(args.provider, **client_kwargs)
            except Exception as exc:
                _warn(f"{args.provider} provider unavailable ({exc}); using offline engine")

        document_types = list(DOCUMENT_TYPES) if args.document == "all" else [args.document]

        def _generate_one(document_type: str):
            if document_type == "technical":
                return generate_document(
                    model, client,
                    owner=args.owner, audience=args.audience, refresh=args.refresh,
                    version=args.doc_version, status=args.status, author=args.author,
                    reviewer=args.reviewer, classification=args.classification,
                    business_decision=args.business_decision, requirements=args.requirements,
                    security_notes=args.security_notes, refresh_notes=args.refresh_notes,
                    deployment_notes=args.deployment_notes, access_notes=args.access_notes,
                    glossary=args.glossary, assumptions=args.assumptions,
                    support_notes=args.support_notes,
                    on_warning=_warn,
                )
            return DOCUMENT_TYPES[document_type].generate(
                model, client,
                owner=args.owner, audience=args.audience, refresh=args.refresh,
                version=args.doc_version, status=args.status, classification=args.classification,
                on_warning=_warn,
            )

        docs = {dtype: _generate_one(dtype) for dtype in document_types}

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
        # multi-doc naming is deterministic), unlike the hosted service
        # (job-filename-dependent download names; needs the zip-bundle work
        # first). Label + relative href per sibling, plus the hub.
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
                    from .render._shared import format_timestamp
                    pandoc.to_pdf(
                        renderers["md"](doc), out_path,
                        title=doc.metadata.report_name, author=doc.metadata.owner,
                        date=format_timestamp(doc.metadata.generated_at),
                    )
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
        finally:
            accounts.close()

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
