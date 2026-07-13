"""The job worker — queue-agnostic.

``process_job`` is a plain function: it takes the store, ids, the upload path,
the sandbox, and an options dict. FastAPI runs it as a ``BackgroundTask`` today;
a Celery task body would call it identically. It owns the zero-retention
contract: everything happens inside the sandbox, the sandbox is shredded in a
``finally`` block, and only the rendered documents (held briefly in the store)
survive. Errors are recorded as a content-free message — never raw metadata.

Generates one or more document types from a single parsed model (never
re-parses). When exactly one document type is requested — including the
default, omitted case, which resolves to ``"technical"`` — output keys stay
flat (``"md"``, ``"html"``, ...) for exact backward compatibility with
existing callers. Composite ``"{type}.{format}"`` keys (e.g. ``"audit.html"``)
are used only when more than one document type is requested.

Multi-document jobs also get a documentation hub (``index.html``) with a
doc-switcher in every doc's sidebar and cross-document content links (audit
measure names -> the technical doc, executive risks -> the audit doc). These
only resolve when every sibling file sits next to the others under these
exact *fixed* relative names — true inside the zip bundle this also produces,
and also true of the individual per-format downloads: ``app.py``'s download
endpoint serves any composite ``"{type}.html"``/``"index.html"`` key under
its own fixed name rather than the upload-derived one it uses for every
other format, specifically so a user who saves the HTML documents one at a
time into the same folder still ends up with working cross-document links.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Callable

from .. import enrichment as enrichment_mod
from ..agents import audit_rules, generate_document, get_client
from ..agents.context import JobAIContext, build_job_context
from ..agents.generators import DOCUMENT_TYPES
from ..render import pandoc, registry
from ..render._shared import format_timestamp
from ..render.audit import _top_cluster as _audit_top_cluster
from ..render.hub import doc_switcher_links, hub_stats, render_hub
from .ingest import ingest_to_model
from .jobs import JobStore
from .logging_config import job_id_var
from .sandbox import JobSandbox

log = logging.getLogger("pbicompass.service")

# Friendly, content-free messages keyed by failure mode (no metadata leaks into logs/UI).
_FRIENDLY = {
    "ingest": "Could not read the uploaded file. Ensure it is a valid .pbix or a "
              ".zip of a .pbip project.",
    "generate": "Documentation generation failed for the uploaded model.",
}


# Cost policy (owner decision, 2026-07-07, §4.0): best output, token cost is
# not a concern. Reasoning depth is never clamped by plan — every tier runs
# at whatever ``effort`` the caller asked for. The monthly job quota
# (``PLAN_LIMITS`` in accounts.py) is the only cost guardrail; a prior
# per-plan effort ceiling was removed here.


def _make_client(options: dict) -> tuple[object | None, str | None]:
    """Resolve the requested provider to a client. Returns ``(client, warning)``
    — ``warning`` is a content-free, user-facing message set whenever the
    requested LLM engine could not be used and the job fell back to the
    offline engine (missing/invalid key, missing SDK, network error, ...)."""
    provider = options.get("provider")
    if provider in (None, "", "none", "offline", "deterministic"):
        return None, None
    kwargs = {"model": options.get("model", "claude-opus-4-8"), "effort": options.get("effort") or "high"}
    api_key = options.get("provider_api_key")
    if api_key:
        kwargs["api_key"] = api_key
    try:
        return get_client(provider, **kwargs), None
    except Exception as exc:  # missing SDK/key -> deterministic, don't fail the job
        log.warning("LLM provider unavailable (%s); using offline engine", type(exc).__name__)
        return None, (
            f"Could not use the {provider} engine ({type(exc).__name__}); "
            "generated with the offline engine instead."
        )


def _resolve_document_types(raw: str | None) -> list[str]:
    """Parse the ``document_types`` option: ``None``/empty -> ``["technical"]``
    (API back-compat), ``"all"`` -> every registered type, otherwise a
    comma-separated list — unknown entries are dropped rather than failing
    the whole job."""
    raw = (raw or "technical").strip()
    if raw.lower() == "all":
        return list(DOCUMENT_TYPES)
    seen: list[str] = []
    for token in raw.split(","):
        dtype = token.strip()
        if dtype in DOCUMENT_TYPES and dtype not in seen:
            seen.append(dtype)
    return seen or ["technical"]


def _effective_metadata(options: dict, enrichment_meta: dict) -> dict:
    """Merge the request's explicit metadata fields (Form values) over the
    enrichment file's report metadata (5.1) — an explicit field wins, the
    enrichment file supplies the default for anything the caller omitted."""
    keys = ("owner", "audience", "refresh", "version", "status", "author", "reviewer",
            "classification", "business_decision", "requirements", "security_notes",
            "refresh_notes", "deployment_notes", "access_notes", "glossary",
            "assumptions", "support_notes")
    # The enrichment file's own field names differ slightly from the CLI/
    # request kwarg names for three of them.
    enrichment_key = {"audience": "target_audience", "refresh": "refresh_schedule"}
    return {
        k: options.get(k) if options.get(k) is not None
        else (enrichment_meta.get(enrichment_key.get(k, k)) or None)
        for k in keys
    }


def _generate_one(document_type: str, model, client, meta: dict, warn: Callable[[str], None],
                   ai_context: JobAIContext | None, top_cluster=None, plan: str | None = None,
                   audit_verdicts=None, requirements_matrix=None) -> object:
    # Day 3: ``meta``'s keys (see _effective_metadata) already match every
    # generator's kwarg names exactly, and every generator now accepts the
    # full human intake field set (previously only the technical document
    # did) — so all four document types get it here, not just technical.
    if document_type == "technical":
        return generate_document(
            model, client, **meta,
            on_warning=warn, ai_context=ai_context, top_cluster=top_cluster,
            audit_verdicts=audit_verdicts, requirements_matrix=requirements_matrix,
        )
    if document_type == "audit":
        return DOCUMENT_TYPES["audit"].generate(
            model, client, **meta,
            on_warning=warn, ai_context=ai_context, plan=plan,
            requirements_matrix=requirements_matrix,
        )
    if document_type == "executive":
        return DOCUMENT_TYPES["executive"].generate(
            model, client, **meta,
            on_warning=warn, ai_context=ai_context, audit_verdicts=audit_verdicts,
            requirements_matrix=requirements_matrix,
        )
    return DOCUMENT_TYPES[document_type].generate(
        model, client, **meta,
        on_warning=warn, ai_context=ai_context, audit_verdicts=audit_verdicts,
    )


def process_job(store: JobStore, job_id: str, upload_path: Path,
                sandbox: JobSandbox, options: dict) -> None:
    # Set explicitly (not inherited from request context) so every log line
    # for this job carries the same id regardless of executor — inline
    # BackgroundTasks, a Celery worker in a separate process, or the CLI.
    job_id_token = job_id_var.set(job_id)
    store.mark_processing(job_id)
    try:
        try:
            model = ingest_to_model(upload_path, sandbox.dir)
        except Exception as exc:
            log.info("job %s ingest failed: %s", job_id, type(exc).__name__)
            store.mark_failed(job_id, _FRIENDLY["ingest"])
            return

        warnings: list[str] = []

        def warn(m: str) -> None:
            log.info("job %s: %s", job_id, m)
            warnings.append(m)

        client, client_warning = _make_client(options)
        if client_warning:
            warn(client_warning)

        # Phase 0: one DAX Translator pass shared by every requested document
        # type this job generates (previously up to 3x redundant per job),
        # cached at a path inside this job's own sandbox — shredded with
        # everything else in the outer ``finally``, and never the env-var
        # default (which would race across jobs running concurrently in
        # this same worker process; the CLI keeps that persistent default).
        # Raw ``options`` (not yet merged with the enrichment file's
        # metadata -- ``meta`` below computes that merge, but only after
        # this call) covers the common case: a value the caller typed into
        # the request itself. An enrichment-file-only value with no
        # matching request field would be missed here, unlike everywhere
        # else these fields are used after ``meta`` exists.
        ai_context = (
            build_job_context(
                model, client, warn, cache_path=str(sandbox.path("llm_cache.db")),
                business_decision=options.get("business_decision"),
                target_audience=options.get("audience"),
                assumptions=options.get("assumptions"),
                security_notes=options.get("security_notes"),
                refresh_notes=options.get("refresh_notes"),
                deployment_notes=options.get("deployment_notes"),
                access_notes=options.get("access_notes"),
                support_notes=options.get("support_notes"),
            )
            if client is not None else None
        )

        # Per-job rule-suppression/severity/threshold config (4.3 / J.A.3).
        # Invalid TOML is a warning, not a failure — the job still runs,
        # just without any overrides applied. Always reset afterward (see
        # the outer finally) since this is process-wide module state.
        audit_rules.set_rules_override_config({})
        rules_file_path = options.get("rules_file_path")
        if rules_file_path:
            error = audit_rules.validate_rules_file(rules_file_path)
            if error:
                warn(f"{error} — continuing without rule overrides.")
            else:
                audit_rules.set_rules_config_path(rules_file_path)

        # Enrichment file (5.1): applies measure/column descriptions,
        # data-source/role details, and rule overrides to the model; its
        # report metadata becomes the default for owner/author/etc. (an
        # explicit Form field still wins). Unlike the CLI, the service never
        # bootstraps a skeleton for a missing path — a job either brings its
        # own file or doesn't. The regenerated skeleton is always added to
        # this job's outputs so the caller can download, fill in, and
        # re-upload it on the next run (the round trip; 5.2's changelog is
        # driven by the same file's fingerprint/summary history).
        enrichment_meta: dict = {}
        enrichment_data: dict = {}
        changelog_text: str | None = None
        enrichment_file_path = options.get("enrichment_file_path")
        if enrichment_file_path:
            try:
                enrichment_data = enrichment_mod.load_enrichment(Path(enrichment_file_path))
            except ValueError as exc:
                warn(f"{exc} — continuing without enrichment.")
                enrichment_data = {}
            else:
                overridden = enrichment_mod.apply_enrichment(model, enrichment_data)
                enrichment_meta = overridden["metadata"]

                history = enrichment_data.setdefault("history", {})
                current_fp = enrichment_mod.get_model_fingerprint(model)
                prev_fp = history.get("previous_fingerprint") or ""
                if prev_fp and prev_fp != current_fp and history.get("previous_summary"):
                    changelog_text = history["previous_summary"]
                history["previous_fingerprint"] = current_fp

        meta = _effective_metadata(options, enrichment_meta)

        document_types = _resolve_document_types(options.get("document_types"))
        multi = len(document_types) > 1
        # Fixed relative filenames, valid only inside the zip bundle built
        # below (never as the individual /download?format= names, which
        # depend on the upload's filename) — the doc-switcher and
        # cross-document links below assume these exact names.
        html_filenames = {d: f"{d}.html" for d in document_types} if multi else {}

        # Day 8/Day 2: when "audit" is requested alongside any other document
        # type, generate it first so its Audit Synthesizer clusters (Day 7,
        # technical §16 only) and its deterministic verdicts (Day 2's
        # cross-artifact consistency check, every other doc type) are both
        # available — avoids a second, potentially-inconsistent Synthesizer
        # call. The pre-generated audit doc is reused below when the main
        # loop reaches "audit" rather than regenerated, so this never doubles
        # LLM cost.
        # Day 9: the AI fix-snippets feature is plan-gated (pro/enterprise
        # only) — threaded here rather than read again per-doc, matching how
        # ``options.get("plan")`` is already validated once by the quota
        # check above in ``app.py``.
        plan = options.get("plan")

        # Day 4: computed once, before pre_audit_doc — no ordering
        # dependency on the Audit document, unlike top_cluster/audit_verdicts.
        from ..agents.traceability import build_requirements_matrix
        requirements_matrix = build_requirements_matrix(
            model, meta.get("requirements"), client, warn, ai_context=ai_context,
            business_decision=meta.get("business_decision"), target_audience=meta.get("audience"),
            assumptions=meta.get("assumptions"), security_notes=meta.get("security_notes"),
            refresh_notes=meta.get("refresh_notes"), deployment_notes=meta.get("deployment_notes"),
            access_notes=meta.get("access_notes"), support_notes=meta.get("support_notes"),
        )

        pre_audit_doc = None
        if "audit" in document_types and len(document_types) > 1:
            pre_audit_doc = _generate_one("audit", model, client, meta, warn, ai_context, plan=plan,
                                          requirements_matrix=requirements_matrix)
        top_cluster = _audit_top_cluster(pre_audit_doc) if pre_audit_doc is not None else None
        audit_verdicts = None
        if pre_audit_doc is not None:
            from ..agents.consistency import build_audit_verdicts
            audit_verdicts = build_audit_verdicts(model, pre_audit_doc)

        outputs: dict[str, bytes] = {}
        docs: dict[str, object] = {}
        # Phase A: generate every requested document first (no rendering yet)
        # so the whole-bundle Senior Reviewer pass below sees the complete
        # bundle — its highest-value checks are cross-document.
        for dtype in document_types:
            if dtype == "audit" and pre_audit_doc is not None:
                doc = pre_audit_doc
            else:
                doc = _generate_one(dtype, model, client, meta, warn, ai_context,
                                     top_cluster=top_cluster if dtype == "technical" else None,
                                     plan=plan if dtype == "audit" else None,
                                     audit_verdicts=audit_verdicts,
                                     requirements_matrix=requirements_matrix)
            if changelog_text and dtype in ("technical", "audit"):
                doc.changelog = changelog_text
            docs[dtype] = doc

        # Benchmark-gated Senior Reviewer loop between generation and
        # rendering. Internal-only: the quality report goes to the job log
        # and the warnings list (both already hidden from the end-user
        # completed-job screen), never into the rendered outputs.
        try:
            from ..agents.reviewer import run_review_loop
            quality = run_review_loop(docs, model, client, warn, ai_context)
            log.info("job %s quality: %s", job_id, json.dumps(quality.to_dict()))
            warn(quality.summary_line())
        except Exception as exc:
            warn(f"Senior Reviewer: quality pass failed, continuing ({exc})")

        # Phase B: render everything (body unchanged from the old combined
        # loop).
        for dtype in document_types:
            doc = docs[dtype]
            renderers = registry.RENDERERS[dtype]

            def key(fmt: str, _dtype: str = dtype) -> str:
                return f"{_dtype}.{fmt}" if multi else fmt

            doc_links = doc_switcher_links(document_types, dtype, html_filenames, "index.html") if multi else None

            outputs[key("md")] = renderers["md"](doc).encode("utf-8")
            outputs[key("json")] = doc.to_json().encode("utf-8")
            outputs[key("html")] = renderers["html"](
                doc, doc_links=doc_links, sibling_hrefs=html_filenames or None,
            ).encode("utf-8")

            docx_path = sandbox.path(f"out.{dtype}.docx")
            renderers["docx"](doc, docx_path)
            outputs[key("docx")] = docx_path.read_bytes()

            if pandoc.pandoc_available() and pandoc.find_pdf_engine():
                try:
                    pdf_path = sandbox.path(f"out.{dtype}.pdf")
                    pandoc.to_pdf(
                        renderers["md"](doc), pdf_path,
                        title=doc.metadata.report_name, author=doc.metadata.owner,
                        date=format_timestamp(doc.metadata.generated_at),
                    )
                    outputs[key("pdf")] = pdf_path.read_bytes()
                except pandoc.PandocError as exc:
                    log.info("job %s pdf skipped for %s: %s", job_id, dtype, type(exc).__name__)

        if multi:
            # 5.7: the parsed model and (when enrichment was used) its
            # round-tripped enrichment skeleton join the bundle — multi-doc
            # only, like the hub/zip themselves, since a single-doc job's
            # output keys must stay flat and dot-free for API back-compat.
            outputs["model.json"] = model.to_json().encode("utf-8")
            if enrichment_file_path:
                try:
                    outputs["enrichment.yaml"] = enrichment_mod.generate_enrichment_template(
                        model, previous=enrichment_data
                    ).encode("utf-8")
                except Exception as exc:
                    warn(f"Could not regenerate the enrichment file: {exc}")

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

            # The zip is the only place the fixed "{type}.html" names (and
            # the doc-switcher/cross-document links built on them) are
            # actually valid side by side — bundle everything generated so
            # far under those same names.
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, data in outputs.items():
                    zf.writestr(name, data)
            outputs["zip"] = zip_buf.getvalue()

        store.store_outputs(job_id, outputs)
        usage = ai_context.usage if ai_context is not None else {}
        store.mark_done(job_id, list(outputs.keys()), warnings, usage=usage)
        if usage:
            # Content-free: agent names and integer call/token counts only.
            log.info("job %s AI usage: %s", job_id, usage)
        log.info("job %s done (%s)", job_id, ",".join(outputs))
    except Exception as exc:  # pragma: no cover - defensive catch-all
        log.exception("job %s failed unexpectedly", job_id)
        store.mark_failed(job_id, _FRIENDLY["generate"])
    finally:
        # Never leak one job's rules/enrichment overrides into the next —
        # both are process-wide module state (audit_rules), not per-job.
        audit_rules.set_rules_config_path(None)
        audit_rules.set_rules_override_config({})
        sandbox.cleanup()  # zero-retention: shred everything, success or failure
        job_id_var.reset(job_id_token)
