# Contributing

## Setup

```bash
git clone <this-repo> pbicompass && cd pbicompass
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,service,agents,pbix]"
```

Copy `.env.example` to `.env` if you want to exercise the AI providers or the
web service's auth path locally — neither is required for the core
parse/generate/test workflow.

## Running tests

```bash
# PowerShell
$env:PYTHONPATH = "src"; python -m unittest discover -s tests -v

# bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

All 164 tests should pass with `pip install -e ".[dev]"` alone (the LLM path
is tested via an in-process fake client — no API key needed). Please add a
test alongside any behavioral change; there's a synthetic fixture at
`tests/fixtures/SampleSales` that exercises every parser code path.

## Ground rules for changes

- **The core parser (`src/pbicompass/parsers`, `src/pbicompass/adapters`) stays
  stdlib-only.** This is a deliberate zero-data-leakage design choice, not an
  oversight — don't add a third-party dependency there. New dependencies
  belong behind an optional extra in `pyproject.toml` (see `agents`,
  `service`, `pbix`).
- **Never read row-level data.** Any code touching `.pbix`/`pbixray` must not
  call `get_table()`/`get_dataframe()` or otherwise materialize business
  data — see `SECURITY.md`.
- **Zero-retention holds for the web service.** If you touch
  `src/pbicompass/service`, uploaded content and extracted metadata must still
  never survive outside the per-job sandbox (`sandbox.py`), and log lines
  must stay content-free.
- **Degrade gracefully.** Missing `pbixray` → layout-only; missing Pandoc →
  point at the HTML→print path; an LLM call failing → fall back to the
  deterministic engine per-agent, not fail the whole job. Follow this pattern
  for new integrations rather than raising.
- **The orphaned-measure audit (and similar factual checks) stay
  deterministic** — a set difference, never an LLM guess.

## Code style

- No comments explaining *what* code does (names should do that); comments
  are reserved for non-obvious *why*.
- Small, focused PRs. Match the existing module boundaries (`parsers` /
  `adapters` / `agents` / `render` / `service`) rather than introducing new
  top-level layers for a one-off feature.

## Pull requests

1. Fork, branch, make your change with a test.
2. Run the full test suite.
3. Open a PR describing the *why* — link an issue if there is one.
