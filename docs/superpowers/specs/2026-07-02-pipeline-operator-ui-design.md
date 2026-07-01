# Pipeline Operator UI — Design

## Goal

A local web UI over the existing Word-DSL parsing pipeline (`parser/word/dsl_parser.py`, `validators/word_structure.py`), primarily for pipeline operators/developers, secondarily useful to content authors. Replaces reading terminal output with: run the pipeline on a `.docx`, browse validation errors, preview the parsed NCM JSON and rendered RTE HTML, and see run history.

## Non-goals (this iteration)

- Multi-file/bulk/folder runs (single file only — matches where the parser itself is today)
- Authentication/multi-user (local single-operator tool, Deployment Mode 1)
- AEM package generation/publish trigger (not built yet — UI wraps parse+validate only)
- Config/mapping editing through the UI

## Architecture

New top-level package `ui/`, sibling to `connectors/`, `parser/`, `validators/`, etc.:

```
ui/
  __init__.py
  app.py          # FastAPI app + HTTP routes (thin glue only)
  service.py      # run_pipeline(path, force=False) -> RunResult -- plain Python, no FastAPI/HTML coupling
  runs.py         # SQLite-backed run history: save_run, get_run, list_runs
  templates/
    base.html
    home.html      # upload form
    result.html    # validation issues + JSON viewer + RTE preview
    history.html   # past runs list
  static/
    style.css
```

`service.py` has zero FastAPI/HTML dependency so it's unit-testable standalone and so a future swap to a JSON API + separate SPA frontend (if this goes multi-user org-wide) only touches `app.py`'s route layer — `service.py`/`runs.py` carry over unchanged. This mirrors the existing publish-layer abstraction principle (package vs Author API) applied to the UI's presentation layer.

New dependencies: `fastapi`, `uvicorn`, `jinja2`, `python-multipart` (file upload), `httpx` (dev-only, for `TestClient`). `sqlite3` is stdlib.

## Data flow / pages

- `GET /` — upload form: single `.docx` file input + "force" checkbox (parse despite validation errors) + Run button.
- `POST /run` — accepts the multipart upload, saves to a local `uploads/` dir (gitignored) under a generated run id, calls `service.run_pipeline(path, force=...)`, persists the result via `runs.py`, redirects to `GET /runs/{run_id}`.
- `GET /runs/{run_id}` — results page:
  - status badge (passed/failed)
  - validation issues table: severity, code, location (`body element #N`), context, message
  - NCM JSON viewer: pretty-printed, collapsible per top-level block via `<details>`
  - RTE preview: for every `description`/`body` field, render the stored HTML string directly in a scoped `<div>` so operators see actual bold/links/lists, not raw tags
- `GET /history` — table of past runs: timestamp, filename, status, error/warning counts, link to each run's result page.

## Run storage

SQLite file `ui/runs.db` (gitignored, created on first run). Table `runs`: `id` (uuid text PK), `filename`, `source_path`, `created_at`, `status` (`passed`/`failed`), `issues_json` (text), `ncm_json` (text).

`runs.py` API: `save_run(result: RunResult) -> str`, `get_run(run_id: str) -> RunRecord | None`, `list_runs() -> list[RunRecord]`.

This doubles as an early version of the Sprint 5 RunManifest concept from the architecture sheet — pulled forward here rather than built twice later.

## Error handling

- Non-`.docx` upload → rejected at the route (extension check) before touching `service.py`; error message on the home page, no run recorded.
- Corrupt/unreadable `.docx` (python-docx raises) → caught inside `service.run_pipeline`, returned as a `RunResult(status="failed")` with one synthetic issue (`severity="error"`, `code="unreadable-file"`) — same shape as validator issues, so the results page needs no special-case rendering.
- Unhandled exceptions in routes → FastAPI's default 500 is acceptable for this internal dev-facing tool (a traceback is useful to this audience, not alarming); no custom global exception handler for this iteration.

## Testing

- `tests/test_ui_service.py` — unit tests directly against `service.run_pipeline()` (no HTTP), reusing the synthetic-docx-builder pattern already established in `tests/test_word_dsl_parser.py`.
- `tests/test_ui_routes.py` — FastAPI `TestClient` hitting `/`, `POST /run` (synthetic docx upload), `GET /runs/{id}`, `GET /history`; asserts status codes and that key content (issue codes, status badge) appears in the rendered HTML.

## Future: multi-user migration

If this needs to serve the org rather than a single local operator: swap `app.py`'s `TemplateResponse` calls for `JSONResponse` (less code than today, since Jinja rendering is the extra layer over FastAPI's native JSON mode) and add a separate React/Vite frontend consuming those endpoints. `service.py` and `runs.py` require no changes for that migration.
