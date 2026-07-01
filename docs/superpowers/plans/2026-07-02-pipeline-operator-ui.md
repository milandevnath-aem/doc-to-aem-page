# Pipeline Operator UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local FastAPI web UI over the existing Word-DSL pipeline (`parser/word/dsl_parser.py`, `validators/word_structure.py`) so operators can upload a `.docx`, run parse+validate, and browse results (validation issues, parsed NCM JSON, rendered RTE HTML preview, run history) instead of reading terminal output.

**Architecture:** New `ui/` package: `service.py` (plain-Python pipeline wrapper, no FastAPI/HTML coupling), `runs.py` (SQLite-backed run history), `app.py` (thin FastAPI route layer over the two), `templates/` (Jinja2), `static/` (CSS). `service.py`/`runs.py` stay swap-safe for a future JSON API + SPA frontend without rework.

**Tech Stack:** FastAPI, Uvicorn, Jinja2, python-multipart (uploads), stdlib `sqlite3` for storage, `httpx`/`fastapi.testclient.TestClient` for route tests. Existing `python-docx`, `parser.word.dsl_parser`, `validators.word_structure` are consumed, not modified.

## Global Constraints

- Python >= 3.11 (existing `pyproject.toml` floor)
- Single `.docx` file per run only — no bulk/folder upload this iteration
- No authentication / no multi-user this iteration (local single-operator tool)
- No AEM package generation/publish trigger — UI wraps parse+validate only
- No config/mapping editing through the UI this iteration
- `service.py` must have zero FastAPI/HTML imports — it's the layer that stays unchanged if this later moves to a JSON API + SPA

---

## Task 1: Run-result model + pipeline service wrapper

**Files:**
- Create: `ui/__init__.py`
- Create: `ui/service.py`
- Test: `tests/test_ui_service.py`

**Interfaces:**
- Consumes: `validators.word_structure.validate_docx(path: str) -> list[Issue]`, `validators.word_structure.Issue` (fields: `severity: str, code: str, message: str, paragraph_index: int, snippet: str = "", context: str = ""`), `parser.word.dsl_parser.parse_docx(path: str) -> dict`
- Produces: `ui.service.RunResult` (fields: `status: str` [`"passed"`|`"failed"`], `filename: str`, `issues: list[dict]`, `ncm: dict | None`); `ui.service.run_pipeline(docx_path: str, force: bool = False) -> RunResult`

- [ ] **Step 1: Write the failing tests**

Create `ui/__init__.py` (empty file) first so the package exists.

Create `tests/test_ui_service.py`:

```python
"""Tests for ui/service.py -- the plain-Python pipeline wrapper behind the UI."""

from pathlib import Path

import docx

from ui.service import run_pipeline


def _build_docx(path: Path, builder) -> str:
    document = docx.Document()
    builder(document)
    document.save(str(path))
    return str(path)


def _valid_doc_builder(doc):
    doc.add_paragraph("[metadata]")
    table = doc.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "title", "Test Page"
    table.cell(2, 0).text, table.cell(2, 1).text = "description", "A test page"
    doc.add_paragraph("[component: hero-banner]")
    doc.add_paragraph("title:")
    doc.add_paragraph("Hero Title")


def test_run_pipeline_passes_clean_doc(tmp_path):
    path = _build_docx(tmp_path / "clean.docx", _valid_doc_builder)

    result = run_pipeline(path)

    assert result.status == "passed"
    assert result.filename == "clean.docx"
    assert result.issues == []
    assert result.ncm["blocks"][0]["name"] == "hero-banner"


def test_run_pipeline_blocks_parse_on_error_by_default(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "Only Title"
        # no "description" row -> missing-required-metadata error

    path = _build_docx(tmp_path / "broken.docx", build)

    result = run_pipeline(path)

    assert result.status == "failed"
    assert result.ncm is None
    assert any(i["code"] == "missing-required-metadata" for i in result.issues)


def test_run_pipeline_force_parses_despite_errors(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "Only Title"

    path = _build_docx(tmp_path / "broken.docx", build)

    result = run_pipeline(path, force=True)

    assert result.status == "failed"  # errors existed, status still reflects that
    assert result.ncm is not None  # but parsing was attempted anyway
    assert result.ncm["page"]["properties"]["title"] == "Only Title"


def test_run_pipeline_warnings_only_still_passes_and_parses(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=3, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "T"
        table.cell(2, 0).text, table.cell(2, 1).text = "description", "D"
        doc.add_paragraph("[component: hero-banner]")  # left empty -> "empty-component" warning
        doc.add_paragraph("[component: featurecards]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Real content")

    path = _build_docx(tmp_path / "warn_only.docx", build)

    result = run_pipeline(path)

    assert result.status == "passed"
    assert result.ncm is not None
    assert any(i["code"] == "empty-component" and i["severity"] == "warning" for i in result.issues)


def test_run_pipeline_unreadable_file_returns_failed_result(tmp_path):
    path = tmp_path / "not_really.docx"
    path.write_text("this is not a valid docx file")

    result = run_pipeline(str(path))

    assert result.status == "failed"
    assert result.ncm is None
    assert result.issues == [{
        "severity": "error",
        "code": "unreadable-file",
        "message": "'not_really.docx' could not be opened as a .docx file.",
        "paragraph_index": -1,
        "snippet": "",
        "context": "",
    }]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ui.service'` (or `No module named 'ui'`)

- [ ] **Step 3: Write the implementation**

Create `ui/service.py`:

```python
"""Business logic for the pipeline operator UI.

Deliberately has zero FastAPI/HTML imports: this is the layer that survives
unchanged if the UI later moves from server-rendered templates to a JSON
API + separate SPA frontend.
"""

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from docx.opc.exceptions import PackageNotFoundError

from parser.word.dsl_parser import parse_docx
from validators.word_structure import Issue, validate_docx


@dataclass
class RunResult:
    status: str  # "passed" or "failed"
    filename: str
    issues: list[dict]
    ncm: dict | None


def run_pipeline(docx_path: str, force: bool = False) -> RunResult:
    filename = Path(docx_path).name

    try:
        issues = validate_docx(docx_path)
    except PackageNotFoundError:
        issue = Issue("error", "unreadable-file", f"'{filename}' could not be opened as a .docx file.", -1)
        return RunResult(status="failed", filename=filename, issues=[dataclasses.asdict(issue)], ncm=None)

    has_errors = any(i.severity == "error" for i in issues)
    status = "failed" if has_errors else "passed"

    ncm = None
    if not has_errors or force:
        ncm = parse_docx(docx_path)

    return RunResult(status=status, filename=filename, issues=[dataclasses.asdict(i) for i in issues], ncm=ncm)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_service.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add ui/__init__.py ui/service.py tests/test_ui_service.py
git commit -m "feat: add pipeline service wrapper for operator UI"
```

---

## Task 2: SQLite run history storage

**Files:**
- Create: `ui/runs.py`
- Test: `tests/test_ui_runs.py`

**Interfaces:**
- Consumes: `ui.service.RunResult` (from Task 1)
- Produces: `ui.runs.RunRecord` (fields: `id: str, filename: str, created_at: str, status: str, issues: list, ncm: dict | None`); `ui.runs.save_run(result: RunResult, db_path: Path | None = None) -> str`; `ui.runs.get_run(run_id: str, db_path: Path | None = None) -> RunRecord | None`; `ui.runs.list_runs(db_path: Path | None = None) -> list[RunRecord]`; module-level `ui.runs.DB_PATH: Path` (default DB location, overridable per-call and monkeypatchable in tests since it's read fresh inside each function rather than bound as a frozen default)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ui_runs.py`:

```python
"""Tests for ui/runs.py -- SQLite-backed run history."""

from ui.runs import get_run, list_runs, save_run
from ui.service import RunResult


def test_save_and_get_run(tmp_path):
    db_path = tmp_path / "test_runs.db"
    result = RunResult(status="passed", filename="test.docx", issues=[], ncm={"page": {"properties": {}}})

    run_id = save_run(result, db_path=db_path)
    record = get_run(run_id, db_path=db_path)

    assert record is not None
    assert record.filename == "test.docx"
    assert record.status == "passed"
    assert record.issues == []
    assert record.ncm == {"page": {"properties": {}}}


def test_get_run_returns_none_for_unknown_id(tmp_path):
    db_path = tmp_path / "test_runs.db"
    list_runs(db_path=db_path)  # touch DB so the table exists before querying

    assert get_run("does-not-exist", db_path=db_path) is None


def test_save_run_with_no_ncm_stores_none(tmp_path):
    db_path = tmp_path / "test_runs.db"
    result = RunResult(
        status="failed", filename="broken.docx",
        issues=[{"severity": "error", "code": "x", "message": "m", "paragraph_index": 0, "snippet": "", "context": ""}],
        ncm=None,
    )

    run_id = save_run(result, db_path=db_path)
    record = get_run(run_id, db_path=db_path)

    assert record.ncm is None
    assert record.issues == [{"severity": "error", "code": "x", "message": "m", "paragraph_index": 0, "snippet": "", "context": ""}]


def test_list_runs_orders_newest_first(tmp_path):
    db_path = tmp_path / "test_runs.db"
    r1 = RunResult(status="passed", filename="first.docx", issues=[], ncm=None)
    r2 = RunResult(status="failed", filename="second.docx", issues=[], ncm=None)

    save_run(r1, db_path=db_path)
    save_run(r2, db_path=db_path)

    records = list_runs(db_path=db_path)

    assert len(records) == 2
    assert records[0].filename == "second.docx"
    assert records[1].filename == "first.docx"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_runs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ui.runs'`

- [ ] **Step 3: Write the implementation**

Create `ui/runs.py`:

```python
"""SQLite-backed run history for the pipeline operator UI.

DB_PATH is read fresh inside each function (not bound as a frozen default
parameter value) so tests can monkeypatch `ui.runs.DB_PATH` and have it
take effect -- a bound default (`db_path: Path = DB_PATH`) would capture
the value at import time and ignore later monkeypatching.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ui.service import RunResult

DB_PATH = Path(__file__).parent / "runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    ncm_json TEXT
)
"""


@dataclass
class RunRecord:
    id: str
    filename: str
    created_at: str
    status: str
    issues: list
    ncm: dict | None


def _resolve_db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DB_PATH


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    return conn


def save_run(result: RunResult, db_path: Path | None = None) -> str:
    db_path = _resolve_db_path(db_path)
    run_id = str(uuid.uuid4())
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO runs (id, filename, created_at, status, issues_json, ncm_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                result.filename,
                datetime.now(timezone.utc).isoformat(),
                result.status,
                json.dumps(result.issues),
                json.dumps(result.ncm) if result.ncm is not None else None,
            ),
        )
    return run_id


def _row_to_record(row: tuple) -> RunRecord:
    return RunRecord(
        id=row[0], filename=row[1], created_at=row[2], status=row[3],
        issues=json.loads(row[4]), ncm=json.loads(row[5]) if row[5] is not None else None,
    )


def get_run(run_id: str, db_path: Path | None = None) -> RunRecord | None:
    db_path = _resolve_db_path(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, filename, created_at, status, issues_json, ncm_json FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    return _row_to_record(row) if row is not None else None


def list_runs(db_path: Path | None = None) -> list[RunRecord]:
    db_path = _resolve_db_path(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, filename, created_at, status, issues_json, ncm_json FROM runs ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_record(row) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_runs.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add ui/runs.py tests/test_ui_runs.py
git commit -m "feat: add SQLite run history storage for operator UI"
```

---

## Task 3: FastAPI app skeleton + home page (upload form)

**Files:**
- Modify: `pyproject.toml`
- Create: `ui/app.py`
- Create: `ui/templates/base.html`
- Create: `ui/templates/home.html`
- Create: `ui/static/style.css`
- Test: `tests/test_ui_routes.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing from earlier tasks yet (this task is the HTTP skeleton)
- Produces: `ui.app.app` (the FastAPI instance), `ui.app.BASE_DIR: Path`, `ui.app.templates: Jinja2Templates`, route `GET /`

- [ ] **Step 1: Add dependencies to pyproject.toml**

In `pyproject.toml`, update the `[project]` dependencies list and `[project.optional-dependencies]` dev list:

```toml
[project]
name = "aem-content-automation"
version = "0.1.0"
description = "Content automation framework generating AEM pages from structured content sources"
requires-python = ">=3.11"
dependencies = [
    "python-docx>=1.1",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.5",
    "httpx>=0.27",
]
```

Run: `.venv\Scripts\python.exe -m pip install -e ".[dev]"`
Expected: installs without error (these packages are already present in this environment from earlier verification, so this should be a fast no-op re-resolve)

- [ ] **Step 2: Add ui/ runtime artifacts to .gitignore**

In `.gitignore`, add:

```
ui/runs.db
ui/uploads/
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_ui_routes.py`:

```python
"""Tests for ui/app.py routes."""

from fastapi.testclient import TestClient

from ui.app import app


def test_home_page_renders_upload_form():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert 'name="docx_file"' in response.text
    assert 'action="/run"' in response.text
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ui.app'`

- [ ] **Step 5: Write the implementation**

Create `ui/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{% block title %}Pipeline Operator UI{% endblock %}</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <nav>
        <a href="/">Run</a>
        <a href="/history">History</a>
    </nav>
    <main>
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

Create `ui/templates/home.html`:

```html
{% extends "base.html" %}
{% block title %}Run Pipeline{% endblock %}
{% block content %}
<h1>Run Pipeline</h1>
<form action="/run" method="post" enctype="multipart/form-data">
    <p>
        <label for="docx_file">.docx file</label><br>
        <input type="file" id="docx_file" name="docx_file" accept=".docx" required>
    </p>
    <p>
        <label>
            <input type="checkbox" name="force" value="true">
            Force parse despite validation errors
        </label>
    </p>
    <button type="submit">Run</button>
</form>
{% endblock %}
```

Create `ui/static/style.css`:

```css
body { font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }
nav a { margin-right: 1rem; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
th, td { border: 1px solid #ccc; padding: 0.5rem; text-align: left; }
.status-passed { color: #1a7f37; font-weight: bold; }
.status-failed { color: #cf222e; font-weight: bold; }
.severity-error { color: #cf222e; }
.severity-warning { color: #9a6700; }
.rte-preview { border: 1px solid #ccc; padding: 1rem; margin: 0.5rem 0; background: #fafafa; }
```

Create `ui/app.py`:

```python
"""FastAPI app for the pipeline operator UI -- thin HTTP glue over
ui/service.py and ui/runs.py. No pipeline logic lives here.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Pipeline Operator UI")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_routes.py -v`
Expected: `1 passed`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore ui/app.py ui/templates/base.html ui/templates/home.html ui/static/style.css tests/test_ui_routes.py
git commit -m "feat: add FastAPI app skeleton and home page for operator UI"
```

---

## Task 4: POST /run — upload, execute pipeline, redirect to result

**Files:**
- Modify: `ui/app.py`
- Test: `tests/test_ui_routes.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: `ui.service.run_pipeline` (Task 1), `ui.runs.save_run` (Task 2)
- Produces: route `POST /run` (multipart form: `docx_file`, `force`), redirects (303) to `/runs/{run_id}` on success, 400 on non-.docx upload

- [ ] **Step 1: Add shared test DB isolation fixture**

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures for the test suite."""

import pytest

from ui import runs as runs_module


@pytest.fixture(autouse=True)
def isolated_runs_db(tmp_path, monkeypatch):
    """Point ui.runs.DB_PATH at a throwaway file for every test, so route
    tests never read or write the real ui/runs.db."""
    monkeypatch.setattr(runs_module, "DB_PATH", tmp_path / "test_runs.db")
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_ui_routes.py`:

```python
import io

from ui import app as app_module


def test_run_route_uploads_and_redirects_to_result(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)

    import docx as docx_lib
    document = docx_lib.Document()
    document.add_paragraph("[metadata]")
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "title", "Test Page"
    table.cell(2, 0).text, table.cell(2, 1).text = "description", "A test page"
    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    client = TestClient(app_module.app)
    response = client.post(
        "/run",
        files={"docx_file": ("sample.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/")


def test_run_route_rejects_non_docx_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)
    client = TestClient(app_module.app)

    response = client.post(
        "/run",
        files={"docx_file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )

    assert response.status_code == 400
```

Note: `test_home_page_renders_upload_form` (Task 3) imports `TestClient` and `app` directly; these new tests import `ui.app` as `app_module` so `UPLOADS_DIR` can be monkeypatched on the module. Update the top of `tests/test_ui_routes.py` so both styles are available:

```python
"""Tests for ui/app.py routes."""

import io

from fastapi.testclient import TestClient

from ui import app as app_module
from ui.app import app


def test_home_page_renders_upload_form():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert 'name="docx_file"' in response.text
    assert 'action="/run"' in response.text


def test_run_route_uploads_and_redirects_to_result(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)

    import docx as docx_lib
    document = docx_lib.Document()
    document.add_paragraph("[metadata]")
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "title", "Test Page"
    table.cell(2, 0).text, table.cell(2, 1).text = "description", "A test page"
    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    client = TestClient(app_module.app)
    response = client.post(
        "/run",
        files={"docx_file": ("sample.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/")


def test_run_route_rejects_non_docx_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)
    client = TestClient(app_module.app)

    response = client.post(
        "/run",
        files={"docx_file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )

    assert response.status_code == 400
```

This replaces the entire file content from Task 3.

- [ ] **Step 3: Run tests to verify the new ones fail**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_routes.py -v`
Expected: `test_home_page_renders_upload_form` PASSES, the two new tests FAIL with 404 (route `/run` doesn't exist yet)

- [ ] **Step 4: Write the implementation**

In `ui/app.py`, add after the existing `home` route:

```python
import shutil
import uuid

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse

from ui.runs import save_run
from ui.service import run_pipeline

UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)


@app.post("/run")
def run(docx_file: UploadFile = File(...), force: str = Form(default="")):
    if not docx_file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are accepted.")

    dest = UPLOADS_DIR / f"{uuid.uuid4()}-{docx_file.filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(docx_file.file, f)

    result = run_pipeline(str(dest), force=(force == "true"))
    run_id = save_run(result)

    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)
```

Full resulting `ui/app.py`:

```python
"""FastAPI app for the pipeline operator UI -- thin HTTP glue over
ui/service.py and ui/runs.py. No pipeline logic lives here.
"""

import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ui.runs import save_run
from ui.service import run_pipeline

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Pipeline Operator UI")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})


@app.post("/run")
def run(docx_file: UploadFile = File(...), force: str = Form(default="")):
    if not docx_file.filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are accepted.")

    dest = UPLOADS_DIR / f"{uuid.uuid4()}-{docx_file.filename}"
    with dest.open("wb") as f:
        shutil.copyfileobj(docx_file.file, f)

    result = run_pipeline(str(dest), force=(force == "true"))
    run_id = save_run(result)

    return RedirectResponse(url=f"/runs/{run_id}", status_code=303)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_routes.py -v`
Expected: `3 passed`

Note: `test_run_route_uploads_and_redirects_to_result` passes even though `GET /runs/{run_id}` doesn't exist until Task 5 — it uses `follow_redirects=False` and only checks the 303 status and `Location` header, it never actually requests that URL.

- [ ] **Step 6: Commit**

```bash
git add ui/app.py tests/test_ui_routes.py tests/conftest.py
git commit -m "feat: add POST /run upload-and-execute route"
```

---

## Task 5: GET /runs/{run_id} — results page

**Files:**
- Modify: `ui/app.py`
- Create: `ui/templates/result.html`
- Test: `tests/test_ui_routes.py`

**Interfaces:**
- Consumes: `ui.runs.get_run` (Task 2)
- Produces: route `GET /runs/{run_id}` (200 with rendered page, 404 if unknown id)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ui_routes.py`:

```python
from ui import runs as runs_module
from ui.service import RunResult


def test_result_page_shows_status_and_issues():
    result = RunResult(
        status="failed",
        filename="broken.docx",
        issues=[{
            "severity": "error", "code": "missing-required-metadata",
            "message": "Metadata table is missing required field(s): ['description'].",
            "paragraph_index": 0, "snippet": "[metadata]", "context": "",
        }],
        ncm=None,
    )
    run_id = runs_module.save_run(result)

    client = TestClient(app_module.app)
    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "missing-required-metadata" in response.text
    assert "FAILED" in response.text


def test_result_page_renders_rte_html_preview():
    result = RunResult(
        status="passed",
        filename="clean.docx",
        issues=[],
        ncm={
            "page": {"properties": {"title": "T"}},
            "blocks": [{
                "name": "hero-banner", "variant": None,
                "fields": {"title": "Hero", "description": "<p>Hello <strong>world</strong></p>"},
                "items": [], "table": None,
            }],
        },
    )
    run_id = runs_module.save_run(result)

    client = TestClient(app_module.app)
    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "<strong>world</strong>" in response.text  # rendered raw HTML, not escaped


def test_result_page_404_for_unknown_run():
    client = TestClient(app_module.app)

    response = client.get("/runs/does-not-exist")

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_routes.py -v`
Expected: `test_result_page_shows_status_and_issues` and `test_result_page_renders_rte_html_preview` FAIL (expected 200, get 404, since `/runs/{run_id}` doesn't exist yet). `test_result_page_404_for_unknown_run` passes already — FastAPI's default 404 for an unregistered path happens to match what that test expects, which is fine; the other two are the ones proving this step is necessary.

- [ ] **Step 3: Write the implementation**

Create `ui/templates/result.html`:

```html
{% extends "base.html" %}
{% block title %}Run Result: {{ run.filename }}{% endblock %}
{% block content %}
<h1>{{ run.filename }}</h1>
<p class="status-{{ run.status }}">Status: {{ run.status | upper }}</p>
<p>Run at: {{ run.created_at }}</p>

<h2>Validation Issues</h2>
{% if run.issues %}
<table>
    <tr><th>Severity</th><th>Code</th><th>Location</th><th>Message</th></tr>
    {% for issue in run.issues %}
    <tr class="severity-{{ issue.severity }}">
        <td>{{ issue.severity }}</td>
        <td>{{ issue.code }}</td>
        <td>body element #{{ issue.paragraph_index }}{% if issue.context %} (in {{ issue.context }}){% endif %}</td>
        <td>{{ issue.message }}</td>
    </tr>
    {% endfor %}
</table>
{% else %}
<p>No issues found.</p>
{% endif %}

{% if run.ncm %}
<h2>Parsed Content (NCM)</h2>
<details open>
    <summary>Page Properties</summary>
    <pre>{{ run.ncm.page.properties | tojson(indent=2) }}</pre>
</details>
{% for block in run.ncm.blocks %}
<details>
    <summary>{{ block.name }}{% if block.variant %} ({{ block.variant }}){% endif %}</summary>
    <h3>Fields</h3>
    {% for key, value in block.fields.items() %}
    <p><strong>{{ key }}</strong>:
    {% if key in ["description", "body"] %}
    <div class="rte-preview">{{ value | safe }}</div>
    {% else %}
    {{ value }}
    {% endif %}
    </p>
    {% endfor %}
    {% for item in block.items %}
    <h4>Card: {{ item.id }}</h4>
    {% for key, value in item.fields.items() %}
    <p><strong>{{ key }}</strong>:
    {% if key in ["description", "body"] %}
    <div class="rte-preview">{{ value | safe }}</div>
    {% else %}
    {{ value }}
    {% endif %}
    </p>
    {% endfor %}
    {% endfor %}
</details>
{% endfor %}
{% else %}
<p>Parsing was not performed (validation errors blocked it).</p>
{% endif %}
{% endblock %}
```

In `ui/app.py`, add:

```python
from ui.runs import get_run, save_run
```

(replacing the earlier `from ui.runs import save_run` import line with this one that also brings in `get_run`), and add the new route after `run`:

```python
@app.get("/runs/{run_id}")
def show_run(request: Request, run_id: str):
    record = get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return templates.TemplateResponse(request, "result.html", {"run": record})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_routes.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add ui/app.py ui/templates/result.html tests/test_ui_routes.py
git commit -m "feat: add run result page with validation issues and RTE preview"
```

---

## Task 6: GET /history — run history page

**Files:**
- Modify: `ui/app.py`
- Create: `ui/templates/history.html`
- Test: `tests/test_ui_routes.py`

**Interfaces:**
- Consumes: `ui.runs.list_runs` (Task 2)
- Produces: route `GET /history`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ui_routes.py`:

```python
def test_history_page_lists_past_runs():
    runs_module.save_run(RunResult(status="passed", filename="a.docx", issues=[], ncm=None))
    runs_module.save_run(RunResult(status="failed", filename="b.docx", issues=[{
        "severity": "error", "code": "x", "message": "m", "paragraph_index": 0, "snippet": "", "context": "",
    }], ncm=None))

    client = TestClient(app_module.app)
    response = client.get("/history")

    assert response.status_code == 200
    assert "a.docx" in response.text
    assert "b.docx" in response.text


def test_history_page_empty_state():
    client = TestClient(app_module.app)

    response = client.get("/history")

    assert response.status_code == 200
    assert "No runs yet" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_routes.py -v`
Expected: the two new tests FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

Create `ui/templates/history.html`:

```html
{% extends "base.html" %}
{% block title %}Run History{% endblock %}
{% block content %}
<h1>Run History</h1>
{% if runs %}
<table>
    <tr><th>Filename</th><th>Status</th><th>Errors</th><th>Warnings</th><th>Run At</th></tr>
    {% for run in runs %}
    <tr>
        <td><a href="/runs/{{ run.id }}">{{ run.filename }}</a></td>
        <td class="status-{{ run.status }}">{{ run.status | upper }}</td>
        <td>{{ run.issues | selectattr("severity", "equalto", "error") | list | length }}</td>
        <td>{{ run.issues | selectattr("severity", "equalto", "warning") | list | length }}</td>
        <td>{{ run.created_at }}</td>
    </tr>
    {% endfor %}
</table>
{% else %}
<p>No runs yet.</p>
{% endif %}
{% endblock %}
```

In `ui/app.py`, add:

```python
from ui.runs import get_run, list_runs, save_run
```

(replacing the Task 5 import line), and add the new route:

```python
@app.get("/history")
def history(request: Request):
    records = list_runs()
    return templates.TemplateResponse(request, "history.html", {"runs": records})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests\test_ui_routes.py -v`
Expected: `8 passed`

- [ ] **Step 5: Run the full test suite**

Run: `.venv\Scripts\python.exe -m pytest tests\ -v`
Expected: `21 passed` — 4 in `test_word_dsl_parser.py` + 5 in `test_ui_service.py` + 4 in `test_ui_runs.py` + 8 in `test_ui_routes.py`, zero failures

- [ ] **Step 6: Commit**

```bash
git add ui/app.py ui/templates/history.html tests/test_ui_routes.py
git commit -m "feat: add run history page"
```

---

## Task 7: Manual verification — run the server and exercise it end-to-end

**Files:** none (verification only)

- [ ] **Step 1: Start the server**

Run: `.venv\Scripts\python.exe -m uvicorn ui.app:app --reload --port 8000`
Expected: `Uvicorn running on http://127.0.0.1:8000`

- [ ] **Step 2: Open the home page**

Open `http://127.0.0.1:8000/` in a browser. Expected: upload form with a `.docx` file input and a Run button.

- [ ] **Step 3: Run the real sample doc through it**

Upload `C:\Users\milan.devnath\Downloads\cheap-car-insurance-policy-online.docx`, click Run.

Expected: redirects to a results page showing:
- Status: PASSED (one `duplicate-card-content` warning, matching the CLI output from earlier in this project)
- The `hero-banner`, `cards-with-full-width` (both variants), `featurecards`, `cardandRTE` blocks, each expandable
- `description`/`body` fields rendered as actual bold text and bullet lists (not raw `<p>`/`<strong>` tags)

- [ ] **Step 4: Check history**

Navigate to `http://127.0.0.1:8000/history`. Expected: the run just completed appears in the table with a link back to its result page.

- [ ] **Step 5: Stop the server**

Ctrl+C in the terminal running uvicorn.
