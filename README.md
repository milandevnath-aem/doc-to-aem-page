# AEM Content Automation Framework

Turns Microsoft Word documents into installable AEM Content Packages.

An operator uploads `.docx` files, the pipeline parses them, maps them onto a
page type's AEM component structure, and emits a FileVault `.zip` to install
through Package Manager. There is no AEM SDK and no custom server code on the
AEM side.

```
.docx → validate → parse → NCM dict → map → node tree → FileVault .zip
```

Used in production for the IFFCO-TOKIO article, blog and city page migrations.

---

## Quick start

Windows paths are used throughout; `.venv/Scripts/python.exe` is called
directly so it works whether or not the venv is activated.

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

Start the operator UI:

```bash
.venv/Scripts/python.exe -m uvicorn ui.app:app --port 8000
```

Open <http://localhost:8000>. Add `--reload` while developing.

Run the tests:

```bash
.venv/Scripts/python.exe -m pytest
```

Requires Python 3.11 or newer.

---

## Operating it

### 1. Point it at an AEM instance

On the home page, set the **site config**: the AEM base URL, the active page
type, and the name of the environment variable holding the auth token
(`AEM_AUTH_TOKEN` by default). The token itself is never stored in the repo or
in the config file — only the variable's name is.

```bash
set AEM_AUTH_TOKEN=...          # cmd
$env:AEM_AUTH_TOKEN = "..."     # PowerShell
```

### 2. Give it a page-type config

A `PageTypeConfig` says which AEM template to build on, where pages land, and
which component each parsed block becomes. Two ways to get one:

- **Fetch from AEM** — reads the Content Fragment named in `cf_paths` for the
  active page type, plus the template's initial content. Cached under
  `config/cache/`.
- **Load a bundled sample** — `config/samples/*.pagetypeconfig.json`, one per
  page type (article, blog, city, make, model, two-wheeler make/model).

Without one, documents still parse but cannot be mapped, and the batch reports
every row as **unmapped** rather than shipping an empty package.

### 3. Run a document, or a folder

- **Single run** — upload one `.docx`, inspect the parsed result, download the
  package.
- **Batch run** — upload a folder. Each subfolder groups one `.docx` with its
  images; the subfolder name never appears in the JCR path. The result table
  reports every page as passed, failed, collision, unresolved or unmapped, and
  offers the combined package and a report `.zip`.

Run history is kept in SQLite and browsable at `/history`.

### Where pages land

Page paths do **not** mirror the source folder tree. The page name comes from
the active `pageNameStrategy`:

| strategy | page name from | lands |
|---|---|---|
| `slug_from_url` | the document's own URL | under `contentRoot` + the URL's section hierarchy |
| `slug_from_meta_title` | the Meta Title | flat, directly under `contentRoot` |
| `docx_filename` | the file name | flat, directly under `contentRoot` |

With `slug_from_url`, a document carrying
`https://www.iffcotokio.co.in/health-insurance/cashless-treatment-vs-reimbursement-claims`
and `contentRoot` `/content/iffcotokio/en` becomes
`/content/iffcotokio/en/health-insurance/cashless-treatment-vs-reimbursement-claims`.

The same hierarchy drives the FAQ Content Fragment folder and the fragments'
category tags, so pages and fragments stay in step. If `siteOrigin` is unset or
does not match the document's URL, no hierarchy can be derived and the batch
report says so in `pages.csv`.

---

## Command line

No UI needed for either of these.

```bash
# structure check; exit 0 = clean or warnings, 1 = errors
.venv/Scripts/python.exe -m validators.word_structure "path/to/doc.docx"

# parse to JSON
.venv/Scripts/python.exe -m parser.word.dsl_parser "path/to/doc.docx" -o out.json
```

---

## Layout

| directory | what it holds |
|---|---|
| `parser/word/` | the engine. `dsl_parser.py` (bracket-marker v1/v2), `v3_parser.py` (markerless articles), `format_detect.py` picks between them |
| `validators/` | pre-flight structure checks; subclasses the real parser so it cannot drift from what the parser accepts |
| `mapping/` | NCM + `PageTypeConfig` → abstract node tree. The seam between source-agnostic and AEM-specific |
| `aem_client/` | `SiteConfig` / `PageTypeConfig` and the fetcher that pulls them from AEM |
| `aem/package_generator/` | node tree → `.content.xml` → installable `.zip`, byte-deterministic |
| `ui/` | FastAPI operator UI. `app.py` is the only file that knows HTTP |
| `config/` | `samples/` bundled configs, `cache/` fetched ones. JSON only |
| `tools/` | operator scripts that are not part of the pipeline |

Four design constraints are deliberate and worth preserving:

- Component mapping is config-driven — rules live in the Content Fragment,
  never hardcoded per content type.
- Publishing is abstracted behind the node tree, so a future Sling POST
  publisher consumes the same dict a FileVault package does.
- `ui/service.py` imports no web framework, so the UI can become a JSON API
  without touching pipeline logic.
- A block with no mapping is marked `__mapping_gap` rather than vanishing
  silently at install time.

---

## Further reading

- **[docs/CHEATSHEET.md](docs/CHEATSHEET.md)** — every command for build, run,
  test, lint and debug, plus the document authoring format the client writes to
  and a troubleshooting section.
- **[docs/python-flow-guide.md](docs/python-flow-guide.md)** — one request
  followed end to end, file by file, written for someone new to the codebase.
- **[CLAUDE.md](CLAUDE.md)** — architecture notes and the current known gaps.

---

## Known gaps

Recorded so nobody rediscovers them the hard way:

- `models/` is empty. The NCM is an untyped dict built independently by both
  parsers, so renaming a key such as `htmltext` fails silently.
- `connectors/` is empty. `dsl_parser.py` opens the `.docx` itself, so the
  source-agnostic connector boundary does not exist yet — a second source type
  means extracting it first.
- No logging anywhere. Batch failures surface only as error strings in the UI.
- `ALLOWED_KEYS` and `KNOWN_COMPONENTS` are hardcoded, duplicating
  `component_mappings` from the Content Fragment.
- `aem/author_api/` (writing pages directly instead of producing a package) is
  scaffolded but not started.
