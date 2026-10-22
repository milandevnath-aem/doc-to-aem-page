# AEM Content Automation Framework — Beginner's Guide to the Python Flow

Written for someone new to Python. Walks through what the system does, how a request flows through the code end to end, and explains the Python language features used along the way.

## 1. The big picture

The project has two halves:

1. **The engine** — `parser/word/dsl_parser.py` + `validators/word_structure.py`. Pure Python, no web framework involved. Reads a `.docx` file authored with a bracket/key-value DSL and produces a normalized JSON dict (the "NCM" — Normalized Content Model). This JSON is later used to build AEM pages.
2. **The steering wheel** — `ui/app.py` (FastAPI web routes), `ui/service.py` (glue layer), `ui/runs.py` (SQLite storage). Lets a human upload a `.docx` in a browser, see validation results, see the parsed JSON, and browse past runs.

```
Browser  --upload-->  ui/app.py (FastAPI route)
                          |
                          v
                     ui/service.py  --calls-->  validators/word_structure.py (sanity check)
                          |                          |
                          |                          v
                          |                    parser/word/dsl_parser.py (docx -> JSON)
                          v
                     ui/runs.py (save to SQLite)
                          |
                          v
                     redirect to /runs/{id}
                          |
                          v
                     ui/templates/result.html (renders JSON as HTML)
```

## 2. Following one request start to finish

### Step 1 — Browser uploads a file

You pick a `.docx` on the home page form and submit. The browser sends an HTTP POST to `/run`. This hits `ui/app.py`:

```python
@app.post("/run")
def run(docx_file: UploadFile = File(...), force: str = Form(default="")):
```

`@app.post("/run")` is a **decorator** — Python syntax for wrapping a function with extra behavior. FastAPI uses it to mean "when a POST request hits `/run`, call this function." `docx_file: UploadFile` is a **type hint**; FastAPI reads it and automatically extracts the uploaded file from the HTTP request. You never parse raw HTTP — the framework does it because you declared the type.

The function saves the upload to disk:

```python
dest = UPLOADS_DIR / f"{uuid.uuid4()}-{safe_filename}"
```

`f"{uuid.uuid4()}-{safe_filename}"` is an **f-string** — Python's built-in string interpolation. `uuid.uuid4()` generates a random unique ID so two uploads with the same filename never collide.

Then it calls the engine:

```python
result = run_pipeline(str(dest), force=(force == "true"))
```

### Step 2 — The engine runs (`ui/service.py`)

```python
@dataclass
class RunResult:
    status: str
    filename: str
    issues: list[dict]
    ncm: dict | None


def run_pipeline(docx_path: str, force: bool = False) -> RunResult:
    issues = validate_docx(docx_path)
    ...
    ncm = parse_docx(docx_path)
    return RunResult(status=status, filename=filename, issues=[...], ncm=ncm)
```

`@dataclass` is a shortcut: instead of hand-writing `__init__` to store `status`, `filename`, `issues`, `ncm` as attributes, the decorator generates that boilerplate from the field list above it. `RunResult(status=..., filename=..., ...)` then builds one.

Two things happen here, always in this order:

**a) Validate first.** `validate_docx` walks the document using the *same* DSL grammar as the real parser (it subclasses the parser class, so the two can never drift out of sync) and reports problems: unknown markers, missing required metadata, orphan cards, duplicate content, etc. This runs *before* parsing so a malformed doc fails fast with a located error message instead of silently producing garbage JSON.

**b) Parse, if safe.** If there are no blocking errors (or `force=True` was passed), `parse_docx(docx_path)` runs.

### Step 3 — Parsing the .docx (`parser/word/dsl_parser.py`)

```python
def parse_docx(path: str) -> dict:
    document = Document(path)                    # python-docx opens the .docx (it's really a zip of XML files)
    parser = walk_document(document, _DslParser())
    return parser.to_dict(str(Path(path)))
```

`_DslParser` is a **class** — an object that carries state (`self.blocks`, `self._component`, `self._active_field`, etc.) as it walks through the document paragraph by paragraph. `self` is Python's convention for "the specific object this method is being called on" — every method takes it as its first parameter, and Python passes it automatically when you write `parser.handle_paragraph(...)`.

`walk_document` is a plain function (not tied to a class) that loops over every paragraph and table in the document, in the order they appear, and dispatches:

```python
for idx, child in enumerate(document.element.body.iterchildren()):
    tag = child.tag.split("}")[-1]
    if tag == "p":
        parser.handle_paragraph(Paragraph(child, document))
    elif tag == "tbl":
        parser.handle_table(Table(child, document))
```

As it walks, the parser recognizes lines using **regular expressions (regex)** — patterns that match text shapes. For example:

```python
COMPONENT_RE = re.compile(r"^\[component:\s*([\w-]+)(?:\|\|([\w-]+))?\]$", re.I)
```

This matches a line like `[component: hero-banner]`. `\s*` means "zero or more spaces," `([\w-]+)` is a *captured group* of word-characters and hyphens (this is what becomes the component name), `(?:\|\|([\w-]+))?` optionally captures a `||variant` suffix. The pattern is compiled once, at import time (module load), so it doesn't get rebuilt on every single line — that would be slow.

Once a marker like `[component: hero-banner]` is seen, a new dict is started to hold that component's data. Then `title:` on its own line sets `self._active_field = "title"`; the next line's text is stored under that field. This continues field by field, card by card, until the next marker.

**A real bug this design caught:** originally, if a table appeared right after a `description:` key (instead of paragraph text), the parser ignored the active field entirely and dumped the table into an unrelated `component["table"]` attribute — leaving `description` empty. This was fixed by checking `self._active_field` inside `handle_table` and, when it's an RTE field, converting the table into real `<table>` HTML and appending it to that field's content instead.

### Step 4 — Turning styled text into HTML

Fields like `description` and `body` map to AEM's Rich Text (RTE) component, which stores its content as an HTML string (`<p>`, `<strong>`, `<a href>`, `<ul><li>`, etc.) — not plain text. So while walking, the parser keeps a *list of blocks* (paragraphs, bullets, tables) with their bold/italic/link marks attached, and only converts that list into one HTML string at the very end:

```python
def _finalize_rte(self, fields: dict):
    for key in RTE_FIELDS:          # {"description", "body"}
        value = fields.get(key)
        if isinstance(value, list):
            fields[key] = _blocks_to_html(value)
```

Plain fields (title, icon, dates) stay flat strings the whole time — no styling to preserve.

### Step 5 — The final JSON ("NCM")

```python
def to_dict(self, source_path: str) -> dict:
    for component in self.blocks:
        self._finalize_rte(component["fields"])
        ...
    return {
        "version": "0.1",
        "source": {"type": "word", "path": source_path},
        "page": {"properties": self.page_properties},
        "blocks": self.blocks,
    }
```

This dict *is* the "master JSON" that will later drive AEM page/component creation — it's what you see in the "Full NCM JSON" section of the result page.

### Step 6 — Saving the run (`ui/runs.py`)

Back in the route handler, `save_run(result)` writes a row into `ui/runs.db` — a SQLite database. SQLite is a whole database engine built into Python's standard library (`import sqlite3`) that lives in a single file — no server process needed, which is why it's a good fit for a local single-operator tool.

```python
conn.execute(
    "INSERT INTO runs (id, filename, created_at, status, issues_json, ncm_json) VALUES (?, ?, ?, ?, ?, ?)",
    (run_id, result.filename, datetime.now(timezone.utc).isoformat(), result.status,
     json.dumps(result.issues), json.dumps(result.ncm) if result.ncm is not None else None),
)
```

The `?` placeholders are parameterized query values — SQLite substitutes them safely, which is what prevents SQL injection. `issues` and `ncm` are Python dicts/lists, so they're serialized to JSON text before storing (SQLite columns are just text/number/blob — no native "JSON" or "dict" type).

### Step 7 — Redirect and render

```python
return RedirectResponse(url=f"/runs/{run_id}", status_code=303)
```

The browser follows this redirect to `GET /runs/{run_id}`, which calls `get_run(run_id)` (reads the row back, JSON-decodes it) and hands the result to a template:

```python
@app.get("/runs/{run_id}")
def show_run(request: Request, run_id: str):
    record = get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return templates.TemplateResponse(request, "result.html", {"run": record})
```

`ui/templates/result.html` is written in **Jinja2**, a templating language embedded in HTML:

- `{{ run.filename }}` — interpolates a value
- `{% for block in run.ncm.blocks %} ... {% endfor %}` — loops
- `{% if key in ["description", "body"] %} ... {% else %} ... {% endif %}` — branches (RTE fields render as raw HTML via the `| safe` filter so `<strong>` shows as **bold**, not literal text; everything else is auto-escaped so it can't accidentally inject HTML)

## 3. File-by-file summary

| File | Responsibility |
|---|---|
| `parser/word/dsl_parser.py` | Converts a `.docx` into the JSON content model — the core extraction logic |
| `validators/word_structure.py` | Pre-flight sanity check; reuses the parser's own grammar so it never drifts out of sync with what the parser actually accepts |
| `ui/service.py` | Wires validate + parse together into one call; deliberately has zero web-framework imports so it would survive a future move to a JSON API + separate frontend unchanged |
| `ui/runs.py` | SQLite persistence of past runs (history) |
| `ui/app.py` | HTTP routes — the only file that knows about FastAPI |
| `ui/templates/*.html` | What the browser actually renders |
| `tests/test_*.py` | Exercise all of the above without needing a browser — good place to see the same code paths run in isolation |

## 4. Python concepts glossary (as used in this codebase)

- **Module / import** — every `.py` file is a module; `from parser.word.dsl_parser import parse_docx` pulls one function out of another file.
- **Class / `self`** — a class bundles data (attributes) and behavior (methods) together. `self` inside a method always refers to the specific object the method was called on.
- **`@dataclass`** — auto-generates the boilerplate `__init__`/`__repr__` for a class that's mainly a bag of named fields (`RunResult`, `RunRecord`, `Issue`).
- **Type hints** (`def f(x: str) -> int:`) — optional annotations that document (and let tools/FastAPI validate) what types a function expects and returns. Python doesn't enforce them at runtime by itself.
- **f-strings** (`f"{a}-{b}"`) — inline string interpolation.
- **Regex** (`re.compile(...)`, `.match()`) — pattern matching against text; used here to recognize DSL markers like `[component: name]`.
- **Decorators** (`@app.post("/run")`) — a function that wraps another function to add behavior; FastAPI uses decorators to register URL routes.
- **Dict / list comprehensions** and **list unpacking** (`headers, *data_rows = rows`) — compact ways to build/transform collections in one line.
- **Context managers** (`with dest.open("wb") as f:`, `with _connect(db_path) as conn:`) — the `with` block guarantees the file/database connection is closed automatically, even if an error occurs inside the block.
- **`None` / Optional types** (`dict | None`) — Python's way of saying "this value might be absent."

## 5. Running it yourself

```bash
# run the automated tests
.venv\Scripts\python.exe -m pytest -v

# start the web app
.venv\Scripts\python.exe -m uvicorn ui.app:app --port 8000
# then open http://localhost:8000 in a browser
```
