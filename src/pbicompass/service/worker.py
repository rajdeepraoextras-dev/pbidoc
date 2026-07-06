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
only work when every sibling file sits next to the others under a *fixed*
relative name — unlike the single-file download endpoint's name (which
depends on the upload's filename), so the fixed names only ever appear
together inside the zip bundle this also produces (pulling 5.7 forward),
never as the individual per-format downloads.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path
from typing import Callable

from .. import enrichment as enrichment_mod
from ..agents import audit_rules, generate_document, get_client
from ..agents.generators import DOCUMENT_TYPES
from ..render import pandoc, registry
from ..render._shared import format_timestamp
from ..render.hub import doc_switcher_links, hub_stats, render_hub
from .ingest import ingest_to_model
from .jobs import JobStore
from .sandbox import JobSandbox

log = logging.getLogger("pbicompass.service")

# Friendly, content-free messages keyed by failure mode (no metadata leaks into logs/UI).
_FRIENDLY = {
    "ingest": "Could not read the uploaded file. Ensure it is a valid .pbix or a "
              ".zip of a .pbip project.",
    "generate": "Documentation generation failed for the uploaded model.",
}


def _make_client(options: dict) -> tuple[object | None, str | None]:
    """Resolve the requested provider to a client. Returns ``(client, warning)``
    — ``warning`` is a content-free, user-facing message set whenever the
    requested LLM engine could not be used and the job fell back to the
    offline engine (missing/invalid key, missing SDK, network error, ...)."""
    provider = options.get("provider")
    if provider in (None, "", "none", "offline", "deterministic"):
        return None, None
    kwargs = {"model": options.get("model", "claude-opus-4-8")}
    if provider in ("anthropic", "claude") and options.get("effort"):
        kwargs["effort"] = options["effort"]
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


def _generate_one(document_type: str, model, client, meta: dict, warn: Callable[[str], None]):
    if document_type == "technical":
        return generate_document(
            model, client,
            owner=meta.get("owner"),
            audience=meta.get("audience"),
            refresh=meta.get("refresh"),
            version=meta.get("version"),
            status=meta.get("status"),
            author=meta.get("author"),
            reviewer=meta.get("reviewer"),
            classification=meta.get("classification"),
            business_decision=meta.get("business_decision"),
            requirements=meta.get("requirements"),
            security_notes=meta.get("security_notes"),
            refresh_notes=meta.get("refresh_notes"),
            deployment_notes=meta.get("deployment_notes"),
            access_notes=meta.get("access_notes"),
            glossary=meta.get("glossary"),
            assumptions=meta.get("assumptions"),
            support_notes=meta.get("support_notes"),
            on_warning=warn,
        )
    return DOCUMENT_TYPES[document_type].generate(
        model, client,
        owner=meta.get("owner"),
        audience=meta.get("audience"),
        refresh=meta.get("refresh"),
        version=meta.get("version"),
        status=meta.get("status"),
        classification=meta.get("classification"),
        on_warning=warn,
    )


def process_job(store: JobStore, job_id: str, upload_path: Path,
                sandbox: JobSandbox, options: dict) -> None:
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

        outputs: dict[str, bytes] = {}
        docs: dict[str, object] = {}
        for dtype in document_types:
            doc = _generate_one(dtype, model, client, meta, warn)
            if changelog_text and dtype in ("technical", "audit"):
                doc.changelog = changelog_text
            docs[dtype] = doc
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
        store.mark_done(job_id, list(outputs.keys()), warnings)
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
