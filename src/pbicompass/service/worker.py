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
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from ..agents import generate_document, get_client
from ..agents.generators import DOCUMENT_TYPES
from ..render import pandoc, registry
from ..render._shared import format_timestamp
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


def _generate_one(document_type: str, model, client, options: dict, warn: Callable[[str], None]):
    if document_type == "technical":
        return generate_document(
            model, client,
            owner=options.get("owner"),
            audience=options.get("audience"),
            refresh=options.get("refresh"),
            version=options.get("version"),
            status=options.get("status"),
            author=options.get("author"),
            reviewer=options.get("reviewer"),
            classification=options.get("classification"),
            business_decision=options.get("business_decision"),
            requirements=options.get("requirements"),
            security_notes=options.get("security_notes"),
            refresh_notes=options.get("refresh_notes"),
            deployment_notes=options.get("deployment_notes"),
            access_notes=options.get("access_notes"),
            glossary=options.get("glossary"),
            assumptions=options.get("assumptions"),
            support_notes=options.get("support_notes"),
            on_warning=warn,
        )
    return DOCUMENT_TYPES[document_type].generate(
        model, client,
        owner=options.get("owner"),
        audience=options.get("audience"),
        refresh=options.get("refresh"),
        version=options.get("version"),
        status=options.get("status"),
        classification=options.get("classification"),
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
        document_types = _resolve_document_types(options.get("document_types"))
        multi = len(document_types) > 1

        outputs: dict[str, bytes] = {}
        for dtype in document_types:
            doc = _generate_one(dtype, model, client, options, warn)
            renderers = registry.RENDERERS[dtype]

            def key(fmt: str, _dtype: str = dtype) -> str:
                return f"{_dtype}.{fmt}" if multi else fmt

            outputs[key("md")] = renderers["md"](doc).encode("utf-8")
            outputs[key("json")] = doc.to_json().encode("utf-8")
            outputs[key("html")] = renderers["html"](doc).encode("utf-8")

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

        store.store_outputs(job_id, outputs)
        store.mark_done(job_id, list(outputs.keys()), warnings)
        log.info("job %s done (%s)", job_id, ",".join(outputs))
    except Exception as exc:  # pragma: no cover - defensive catch-all
        log.exception("job %s failed unexpectedly", job_id)
        store.mark_failed(job_id, _FRIENDLY["generate"])
    finally:
        sandbox.cleanup()  # zero-retention: shred everything, success or failure
