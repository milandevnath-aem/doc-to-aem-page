# Cheat Sheet — AEM Content Automation Framework

Everything you need to build, run, test, and debug the project.
All commands assume you are in the project root:

```
D:\iffcotokio\revamp\workspace\code
```

On Windows, `.venv/Scripts/python.exe` is used everywhere instead of a bare
`python`, so it works whether or not the venv is "activated".

---

## 1. First-time setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

`-e ".[dev]"` = editable install + dev tools (pytest, ruff, httpx).
Only needed once, or after `pyproject.toml` dependencies change.

---

## 2. Start the operator UI

```bash
.venv/Scripts/python.exe -m uvicorn ui.app:app --port 8000
```

Then open <http://localhost:8000>.

With auto-reload while developing (restarts on file save):

```bash
.venv/Scripts/python.exe -m uvicorn ui.app:app --port 8000 --reload
```

Pages:

| URL | What it does |
|---|---|
| `/` | Upload a `.docx` |
| `/runs/{id}` | Validation issues + parsed content + full NCM JSON |
| `/history` | Every past run |

Stop the server with `Ctrl+C`.

---

## 3. Run the tests

```bash
# everything
.venv/Scripts/python.exe -m pytest

# verbose
.venv/Scripts/python.exe -m pytest -v

# one file
.venv/Scripts/python.exe -m pytest tests/test_word_dsl_parser.py

# one test
.venv/Scripts/python.exe -m pytest tests/test_word_dsl_parser.py::test_parse_real_v2_client_doc_end_to_end

# with coverage
.venv/Scripts/python.exe -m pytest --cov
```

---

## 4. Lint

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff check . --fix
```

---

## 5. Command-line tools (no UI needed)

Validate a document's structure:

```bash
.venv/Scripts/python.exe -m validators.word_structure "path/to/doc.docx"
```

Exit code `0` = clean or warnings only, `1` = errors.

Parse a document to JSON:

```bash
# print to screen
.venv/Scripts/python.exe -m parser.word.dsl_parser "path/to/doc.docx"

# write to a file
.venv/Scripts/python.exe -m parser.word.dsl_parser "path/to/doc.docx" -o output.json

# parse even though validation found errors
.venv/Scripts/python.exe -m parser.word.dsl_parser "path/to/doc.docx" --force
```

Quick inspection from Python:

```bash
.venv/Scripts/python.exe -c "
from parser.word.dsl_parser import parse_docx
ncm = parse_docx('tests/fixtures/article-v2-sample.docx')
for b in ncm['blocks']:
    print(b['name'], b['variant'], len(b['items']))
"
```

---

## 6. Where things live

| Path | Responsibility |
|---|---|
| `parser/word/dsl_parser.py` | `.docx` → NCM JSON. The core engine. |
| `validators/word_structure.py` | Pre-flight structure checks; reuses the parser's own grammar |
| `ui/app.py` | FastAPI routes (the only file that knows about HTTP) |
| `ui/service.py` | validate + parse glue; no web imports |
| `ui/runs.py` | SQLite run history (`ui/runs.db`) |
| `ui/templates/` | Jinja2 HTML |
| `tests/fixtures/article-v2-sample.docx` | The real client v2 doc, used as a regression test |
| `docs/superpowers/specs/` | Approved design docs |
| `docs/templates/` | Authoring template handed to content authors |
| `mapping/`, `transformers/`, `aem/`, `assets/` | **Not built yet** — the AEM output half |

Generated, git-ignored (safe to delete anytime):
`ui/runs.db`, `ui/uploads/`, `ui/static/assets/`, `output_sample.json`, `.venv/`

---

## 7. Document authoring format (what the client writes)

```
[metadata]
<a 2-column table: Property | Value — must include title and description>

[component: hero-banner]
Tips for Buying Insurance                 <- first line = title
An intro paragraph.                       <- following text = description
Data-of-release: June 23, 2026            <- optional key: value

[component: featurecards]
Benefits of Buying Insurance              <- component title (bold)
icon: location1.png                       <- starts card 1
Financial Protection                      <- card title
Helps manage unexpected expenses.         <- card description

icon: location2.png                       <- starts card 2
Peace of Mind
Focus on recovery instead of bills.
```

Rules that matter:

- A **new card** starts on an `icon: name.png` line, a **pasted image**, or a **fully bold line** once the component already has a title.
- **Pasted images work** — paste directly into Word instead of writing a filename. Extracted automatically to `ui/static/assets/<run-id>/`. If a card has both, the pasted image wins.
- The old strict format (`title:` / `description:` keys on their own lines) **still works** — both styles can coexist in one document.
- Word bullet lists become `<ul><li>` in rich-text fields.
- Known components: `hero-banner`, `cards-with-full-width`, `featurecards`, `cardandrte`, `teasercomponent`. Anything else is a validation error — add it to `KNOWN_COMPONENTS` in `validators/word_structure.py`.

---

## 8. Git

```bash
git status
git log --oneline -10
git diff

# feature work
git checkout -b feature/<name>
# ... edit, test ...
git add <specific files>            # never `git add -A`
git commit -m "feat: ..."
git checkout master
git merge feature/<name> --no-edit
git branch -d feature/<name>
```

Remote (`https://github.com/Milandevnath/docx-to-aem`) — push not working yet;
needs a GitHub Personal Access Token with `repo` scope, and the stale Windows
credential for github.com cleared from Credential Manager first.

---

## 9. Troubleshooting

**`Port 8000 is in use`** — a server is already running. Find and stop it:

```bash
netstat -ano | findstr :8000
```

Then in PowerShell: `Stop-Process -Id <PID> -Force`. Or just use another port
with `--port 8001`.

**`ModuleNotFoundError: No module named 'parser'`** — you are not in the project
root, or the editable install never ran. Re-run step 1.

**`Device or resource busy` deleting `ui/runs.db`** — the server still has it
open. Stop the server first.

**A table or section vanished from the parsed output** — check the result page's
"Validation Issues" table first; an unknown `[component: ...]` name silently
swallows everything after it until the next marker.

**Changes not showing in the browser** — the server was started without
`--reload`. Restart it.
