# pbicompass — production image.
# Python 3.12: full wheel availability for FastAPI/pydantic, and (optionally)
# pbixray for .pbix parsing. The .pbip path is pure stdlib and works anywhere.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# pandoc enables PDF output (HTML/DOCX/MD/JSON work without it).
RUN apt-get update \
    && apt-get install -y --no-install-recommends pandoc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# Web service + all AI engines by default (the web UI lets a user pick
# Claude, Gemini, or Cohere per job — ship all so that choice isn't silently
# a no-op). No engine does anything without its API key set at runtime
# (ANTHROPIC_API_KEY / GEMINI_API_KEY / COHERE_API_KEY) or passed BYOK from
# the UI; the offline engine still needs neither. Add ".[pbix]" for legacy
# .pbix parsing too.
# postgres: managed Postgres for AccountStore (PBICOMPASS_DB=postgres://...,
#   e.g. Supabase's own Postgres) -- inert if PBICOMPASS_DB stays a plain
#   sqlite path.
# auth: Supabase Auth JWT verification (SUPABASE_URL) -- inert if
#   SUPABASE_URL is unset (API-key-only self-host mode).
RUN pip install ".[service,agents,postgres,auth]"

# Run as a non-root user; /data holds the SQLite accounts DB (mount a volume here).
RUN useradd --create-home app && mkdir -p /data && chown app /data
USER app

ENV PBICOMPASS_DB=/data/pbicompass.db \
    PBICOMPASS_JOBS_DB=/data/pbicompass_jobs.db \
    PBICOMPASS_SANDBOX_ROOT=/tmp/pbicompass \
    PBICOMPASS_MAX_UPLOAD_MB=100
# Auth is OFF by default (public tenant). For a hosted SaaS set:
#   PBICOMPASS_REQUIRE_AUTH=1   (and create accounts with `pbicompass account create`)

EXPOSE 8000

# Single worker: the job store is in-process. Scale out later via Celery/Redis.
# Bind to $PORT when the platform provides one (Render/Railway), else 8000 (local/VM).
CMD ["sh", "-c", "uvicorn pbicompass.service.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
