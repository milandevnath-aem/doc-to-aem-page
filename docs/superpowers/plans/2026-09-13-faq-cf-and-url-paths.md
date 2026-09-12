# FAQ Content Fragments and URL-derived paths — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract FAQ question/answer pairs from client `.docx` files into AEM Content Fragments, derive page and fragment paths from each document's own URL, and emit a per-page CSV audit of every batch run.

**Architecture:** A Content Fragment is a JCR node tree, so the existing `aem/package_generator/filevault.py` serializer handles it unchanged. New work is a URL-parsing module, a FAQ state machine in the v1/v2 parser, a CF tree builder, packaging entries, and a CSV writer. The parser stays AEM-ignorant: it receives plain strings, and `ui/service.py` translates config into those strings.

**Tech Stack:** Python 3.11, python-docx, pydantic (declared, unused), FastAPI, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-13-faq-cf-and-url-paths-design.md`

## Global Constraints

- Run everything through `.venv/Scripts/python.exe` — never a bare `python`.
- All AEM-specific values come from `PageTypeConfig`. Nothing hardcoded in parser or mapper.
- `parser/` and `validators/` must not import from `aem_client/`. They take plain strings; `ui/service.py` does the translation.
- Falsy values are dropped when mapping to JCR properties so empty strings never overwrite template defaults. Preserve this.
- Package output stays byte-deterministic: fixed zip timestamps, sorted entry order.
- `git add` specific files only. Never `git add -A`.
- Every task ends green: `.venv/Scripts/python.exe -m pytest -q` and `.venv/Scripts/python.exe -m ruff check .`

---

### Task 1: Value-based URL and keyword capture

The seven client documents spell the URL key four ways. Two documents lose metadata today. Match on the value's shape instead of a fixed key list.

**Files:**
- Modify: `aem_client/config.py` — add `site_origin` field and its camelCase rename
- Modify: `parser/word/dsl_parser.py:52-65` — replace `_V3_STYLE_META_RE` / `_V3_STYLE_META_KEY_MAP` handling
- Modify: `ui/service.py:34-60` — thread `site_origin` from config into `parse_docx`
- Test: `tests/test_word_dsl_parser.py`, `tests/test_aem_config.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `parse_docx(path, assets_dir=None, format=None, site_origin: str | None = None) -> dict`; `PageTypeConfig.site_origin: str`; `dsl_parser._is_site_url(value: str, origin: str | None) -> bool`.

- [ ] **Step 1: Write the failing test**

In `tests/test_word_dsl_parser.py`:

```python
def test_url_captured_from_any_label_containing_url(tmp_path):
    """Real client docs spell the URL key four different ways. Capture is
    driven by the value's shape, not a fixed list of labels."""
    def build(doc):
        doc.add_paragraph("URL Need to be Careated:https://www.iffcotokio.co.in/health-insurance/why-family")
        doc.add_paragraph("Meta Title: Why Family Health Insurance")
        doc.add_paragraph("Meta Description: D")
        doc.add_paragraph("Keywords:   family health insurance, tax")
        doc.add_paragraph("[component: new-hero-banner]")
        doc.add_paragraph("Banner Image: hero.jpg")
        doc.add_paragraph("Some banner prose.")

    path = _build_docx(tmp_path / "a4.docx", build)
    props = parse_docx(path, site_origin="https://www.iffcotokio.co.in")["page"]["properties"]
    assert props["url"] == "https://www.iffcotokio.co.in/health-insurance/why-family"
    assert props["keywords"] == "family health insurance, tax"
    assert props["title"] == "Why Family Health Insurance"


def test_old_url_label_routes_to_old_url_not_url(tmp_path):
    def build(doc):
        doc.add_paragraph("Old URL: https://www.iffcotokio.co.in/old/page")
        doc.add_paragraph("New Required URL : https://www.iffcotokio.co.in/new/page")
        doc.add_paragraph("Meta Title: T")
        doc.add_paragraph("Meta Description: D")
        doc.add_paragraph("[component: new-hero-banner]")
        doc.add_paragraph("Prose.")

    path = _build_docx(tmp_path / "urls.docx", build)
    props = parse_docx(path, site_origin="https://www.iffcotokio.co.in")["page"]["properties"]
    assert props["old_url"] == "https://www.iffcotokio.co.in/old/page"
    assert props["url"] == "https://www.iffcotokio.co.in/new/page"


def test_url_label_with_off_origin_value_is_not_captured(tmp_path):
    """A URL on another domain is prose, not this page's URL."""
    def build(doc):
        doc.add_paragraph("Reference URL: https://example.com/somewhere")
        doc.add_paragraph("Meta Title: T")
        doc.add_paragraph("Meta Description: D")
        doc.add_paragraph("[component: new-hero-banner]")
        doc.add_paragraph("Prose.")

    path = _build_docx(tmp_path / "off.docx", build)
    props = parse_docx(path, site_origin="https://www.iffcotokio.co.in")["page"]["properties"]
    assert "url" not in props
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_word_dsl_parser.py -k "url_captured or old_url_label or off_origin" -v`

Expected: FAIL — `parse_docx()` got an unexpected keyword argument `site_origin`.

- [ ] **Step 3: Write the implementation**

In `parser/word/dsl_parser.py`, replace the `_V3_STYLE_META_RE` / `_V3_STYLE_META_KEY_MAP` pair with:

```python
# Real client docs spell the URL key at least four ways ("New Required URL:",
# "New Required URL :", "URL Need to be Careated:"). Matching a fixed list of
# labels loses a doc every time an author invents a new spelling, so capture
# is driven by the VALUE's shape: the label only has to contain "url", and the
# value has to be an absolute URL on the configured site origin.
_ANY_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]{0,60})\s*:\s*(.*)$")
_META_TITLE_RE = re.compile(r"^meta\s+title$", re.I)
_META_DESC_RE = re.compile(r"^meta\s+description$", re.I)


def _is_site_url(value: str, origin: str | None) -> bool:
    """True when `value` is an absolute http(s) URL and -- when an origin is
    configured -- sits under it. Without an origin any absolute URL passes, so
    the CLI keeps working with no config."""
    low = value.strip().lower()
    if not low.startswith(("http://", "https://")):
        return False
    if not origin:
        return True
    return low.startswith(origin.rstrip("/").lower() + "/")
```

Add `site_origin` to `_DslParser.__init__`:

```python
    def __init__(self, assets_dir: str | None = None, site_origin: str | None = None):
        ...
        self._site_origin = site_origin
```

Replace `_try_capture_v3_style_meta` with:

```python
    def _try_capture_v3_style_meta(self, line: str) -> bool:
        """Capture inline meta keys as page.properties whenever the walker is
        NOT inside a component. Returns True to short-circuit further
        per-line handling. First-wins, so a repeated key cannot stomp the
        value the author wrote first."""
        if self._component is not None:
            return False
        m = _ANY_KEY_RE.match(line)
        if not m:
            return False
        label, value = m.group(1).strip(), m.group(2).strip()
        low = label.lower()
        if "url" in low:
            if not _is_site_url(value, self._site_origin):
                return False
            key = "old_url" if "old" in low else "url"
        elif "keyword" in low:
            key = "keywords"
        elif _META_TITLE_RE.match(label):
            key = "title"
        elif _META_DESC_RE.match(label):
            key = "description"
        else:
            return False
        if value:
            self.page_properties.setdefault(key, value)
        return True
```

Update `parse_docx`:

```python
def parse_docx(path: str, assets_dir: str | None = None, format: str | None = None,
               site_origin: str | None = None) -> dict:
    from parser.word.format_detect import detect_format
    from parser.word.v3_parser import _V3Parser

    document = Document(path)
    fmt = format or detect_format(document)
    if fmt == "v3":
        parser = walk_document(document, _V3Parser(assets_dir))
    else:
        parser = walk_document(document, _DslParser(assets_dir, site_origin))
    return parser.to_dict(str(Path(path)), fmt)
```

In `aem_client/config.py`, add the field to `PageTypeConfig` (after `container_resource_type`) and its rename entry:

```python
    # Origin of the live site, used to recognise which key lines in a doc
    # carry THIS page's URL. Empty means "accept any absolute URL".
    site_origin: str = ""
```

```python
        "siteOrigin": "site_origin",
```

In `ui/service.py`, pass it through:

```python
        ncm = parse_docx(
            docx_path, assets_dir=assets_dir, format=format,
            site_origin=(page_type_config.site_origin if page_type_config else None),
        )
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check .`

Expected: all pass. The three new tests go green and the existing 185 stay green — `_ANY_KEY_RE` is strictly more permissive than the old fixed list for these labels, and every other label falls through to `return False` exactly as before.

- [ ] **Step 5: Verify against the real client docs**

Run:

```bash
.venv/Scripts/python.exe -X utf8 -c "
import sys,glob,os; sys.path.insert(0,'.')
sys.stdout.reconfigure(encoding='utf-8')
from parser.word.dsl_parser import parse_docx
for p in sorted(glob.glob('../content-structure-test/Articles/*/*.docx')):
    props = parse_docx(p, site_origin='https://www.iffcotokio.co.in')['page']['properties']
    print(os.path.basename(os.path.dirname(p)), '|', props.get('url','MISSING')[:70])
"
```

Expected: all seven print a URL. Article4 must no longer print `MISSING`.

- [ ] **Step 6: Commit**

```bash
git add parser/word/dsl_parser.py aem_client/config.py ui/service.py tests/test_word_dsl_parser.py
git commit -m "feat(parser): capture URL and keyword meta by value shape, not fixed key list"
```

---

### Task 2: FAQ question/answer parsing in the v1/v2 parser

All seven client documents parse as `v1v2`, and that parser has no FAQ state machine — the FAQ section becomes a second unstructured `page-description`. Port the `_V3Parser` behaviour so a real `faq` block is produced.

**Files:**
- Modify: `parser/word/dsl_parser.py:442-458` — replace the page-description-for-FAQ branch
- Test: `tests/test_word_dsl_parser.py`

**Interfaces:**
- Consumes: `_DslParser` from Task 1.
- Produces: NCM blocks of the form `{"name": "faq", "fields": {"title": <label>}, "items": [{"id": None, "implicit": True, "fields": {"question": str, "htmltext": str}}]}`. Tasks 5 and 6 consume this shape.

- [ ] **Step 1: Write the failing test**

```python
def test_faq_section_becomes_structured_faq_block(tmp_path):
    """A bold FAQ label opens a faq block; each bold line is a question and
    the plain paragraphs under it are that question's answer."""
    def build(doc):
        doc.add_paragraph("Meta Title: T")
        doc.add_paragraph("Meta Description: D")
        doc.add_paragraph("[component: new-hero-banner]")
        doc.add_paragraph("Banner prose.")
        doc.add_paragraph().add_run("Body Heading").bold = True
        doc.add_paragraph("Article body paragraph.")
        doc.add_paragraph().add_run("FAQs").bold = True
        doc.add_paragraph().add_run("Can parents be included?").bold = True
        doc.add_paragraph("Yes, parents can be included.")
        doc.add_paragraph("Subject to age limits.")
        doc.add_paragraph().add_run("Is it mandatory?").bold = True
        doc.add_paragraph("No, it is not mandatory.")

    path = _build_docx(tmp_path / "faq.docx", build)
    blocks = parse_docx(path)["blocks"]
    faqs = [b for b in blocks if b["name"] == "faq"]
    assert len(faqs) == 1
    items = faqs[0]["items"]
    assert [i["fields"]["question"] for i in items] == [
        "Can parents be included?", "Is it mandatory?"]
    assert items[0]["fields"]["htmltext"] == (
        "<p>Yes, parents can be included.</p><p>Subject to age limits.</p>")
    assert items[1]["fields"]["htmltext"] == "<p>No, it is not mandatory.</p>"


def test_faq_answers_keep_bullets_as_list_markup(tmp_path):
    def build(doc):
        doc.add_paragraph("Meta Title: T")
        doc.add_paragraph("Meta Description: D")
        doc.add_paragraph("[component: new-hero-banner]")
        doc.add_paragraph("Banner prose.")
        doc.add_paragraph().add_run("FAQs").bold = True
        doc.add_paragraph().add_run("What is covered?").bold = True
        _add_bullet(doc, "Hospitalisation")
        _add_bullet(doc, "Day care")

    path = _build_docx(tmp_path / "faqbul.docx", build)
    faq = [b for b in parse_docx(path)["blocks"] if b["name"] == "faq"][0]
    assert faq["items"][0]["fields"]["htmltext"] == (
        "<ul><li>Hospitalisation</li><li>Day care</li></ul>")


def test_faq_label_with_no_questions_yields_empty_faq_block(tmp_path):
    """A FAQ heading with nothing under it must not silently vanish, and must
    not swallow the block into the article body either."""
    def build(doc):
        doc.add_paragraph("Meta Title: T")
        doc.add_paragraph("Meta Description: D")
        doc.add_paragraph("[component: new-hero-banner]")
        doc.add_paragraph("Banner prose.")
        doc.add_paragraph().add_run("Frequently Asked Questions").bold = True

    path = _build_docx(tmp_path / "faqempty.docx", build)
    faqs = [b for b in parse_docx(path)["blocks"] if b["name"] == "faq"]
    assert len(faqs) == 1
    assert faqs[0]["items"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_word_dsl_parser.py -k faq -v`

Expected: FAIL — no block named `faq`; the FAQ content is in a `page-description`.

- [ ] **Step 3: Write the implementation**

In `parser/word/dsl_parser.py`, replace the FAQ branch inside `handle_paragraph` (the block beginning `# Bold-only "FAQ" heading inside an implicit page-description`) with:

```python
        # A bold-only FAQ label opens a structured `faq` block. Everything
        # after it is question/answer pairs, not article prose: each further
        # bold-only line is a question, and the plain paragraphs and bullets
        # under it are that question's answer.
        if (
            _paragraph_all_bold(paragraph)
            and self._component is not None
            and self._item is None
            and _FAQ_LABEL_RE.match(paragraph.text.strip())
        ):
            self._start_faq(paragraph.text.strip())
            return
        if self._component is not None and self._component["name"] == "faq":
            if _paragraph_all_bold(paragraph) and paragraph.text.strip():
                self._start_faq_item(paragraph.text.strip())
                return
```

Add the two helpers to `_DslParser`:

```python
    def _start_faq(self, label: str) -> None:
        """Open a faq block. `_active_field` stays None until the first
        question arrives, so stray prose between the label and question one
        cannot become an answer belonging to nothing."""
        self._start_component("faq", None)
        self._component["fields"]["title"] = label

    def _start_faq_item(self, question: str) -> None:
        self._item = {"id": None, "implicit": True,
                      "fields": {"question": question, "description": []}}
        self._component["items"].append(self._item)
        self._active_field = "description"
```

`description` is used as the working key because `_finalize_rte` already collapses it to the canonical `htmltext`, matching every other component.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_word_dsl_parser.py -k faq -v`

Expected: PASS.

- [ ] **Step 5: Run the full suite and fix fallout**

Run: `.venv/Scripts/python.exe -m pytest -q`

Expected: one existing test may assert the old two-`page-description` shape. Update it to expect `['new-hero-banner', 'page-description', 'faq']`, since that is the corrected behaviour this task delivers — do not weaken the new assertions to keep the old one green.

- [ ] **Step 6: Verify against the real client docs**

Run:

```bash
.venv/Scripts/python.exe -X utf8 -c "
import sys,glob,os; sys.path.insert(0,'.')
sys.stdout.reconfigure(encoding='utf-8')
from parser.word.dsl_parser import parse_docx
for p in sorted(glob.glob('../content-structure-test/Articles/*/*.docx')):
    b = parse_docx(p)['blocks']
    faq = [x for x in b if x['name']=='faq']
    n = len(faq[0]['items']) if faq else 0
    print(f'{os.path.basename(os.path.dirname(p)):9} faq_items={n:2} blocks={[x[\"name\"] for x in b]}')
"
```

Expected counts, matching the bold-question survey of the source documents: Article1 8, Article2 5, Article3 3, Article4 2, Article5 5, Article6 2, Article7 5.

- [ ] **Step 7: Commit**

```bash
git add parser/word/dsl_parser.py tests/test_word_dsl_parser.py
git commit -m "feat(parser): parse FAQ sections into structured faq blocks in v1v2 docs"
```

---

### Task 3: URL to page hierarchy

Pure functions, no I/O. Everything downstream derives from these.

**Files:**
- Create: `mapping/url_paths.py`
- Test: `tests/test_url_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `split_site_url(url: str, origin: str) -> tuple[list[str], str]` returning `(hierarchy_segments, page_slug)`; `([], "")` when the URL is empty or off-origin.
  - `page_path(content_root: str, hierarchy: list[str], slug: str) -> str`
  - `cf_folder_path(cf_root: str, hierarchy: list[str]) -> str`
  - `faq_categories(namespace: str, hierarchy: list[str]) -> list[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_url_paths.py`:

```python
"""Path derivation from a document's own URL."""

from mapping.url_paths import (
    cf_folder_path, faq_categories, page_path, split_site_url,
)

ORIGIN = "https://www.iffcotokio.co.in"


def test_split_two_segment_hierarchy():
    hierarchy, slug = split_site_url(
        f"{ORIGIN}/health-insurance/family-health-insurance/who-should-buy", ORIGIN)
    assert hierarchy == ["health-insurance", "family-health-insurance"]
    assert slug == "who-should-buy"


def test_split_single_segment_hierarchy():
    hierarchy, slug = split_site_url(
        f"{ORIGIN}/health-insurance/cashless-vs-reimbursement", ORIGIN)
    assert hierarchy == ["health-insurance"]
    assert slug == "cashless-vs-reimbursement"


def test_split_tolerates_trailing_slash_and_html_suffix():
    assert split_site_url(f"{ORIGIN}/health-insurance/page.html", ORIGIN) == (
        ["health-insurance"], "page")
    assert split_site_url(f"{ORIGIN}/health-insurance/page/", ORIGIN) == (
        ["health-insurance"], "page")


def test_split_rejects_empty_and_off_origin():
    assert split_site_url("", ORIGIN) == ([], "")
    assert split_site_url("https://example.com/a/b", ORIGIN) == ([], "")


def test_split_slugifies_segments():
    hierarchy, slug = split_site_url(f"{ORIGIN}/Health Insurance/Who Should Buy", ORIGIN)
    assert hierarchy == ["health-insurance"]
    assert slug == "who-should-buy"


def test_page_path_joins_root_hierarchy_and_slug():
    assert page_path("/content/iffcotokio/en/",
                     ["health-insurance", "family-health-insurance"],
                     "who-should-buy") == (
        "/content/iffcotokio/en/health-insurance/family-health-insurance/who-should-buy")


def test_page_path_with_no_hierarchy():
    assert page_path("/content/iffcotokio/en", [], "who-should-buy") == (
        "/content/iffcotokio/en/who-should-buy")


def test_cf_folder_path_joins_root_and_hierarchy():
    assert cf_folder_path("/content/dam/x/faq/", ["health-insurance", "family"]) == (
        "/content/dam/x/faq/health-insurance/family")


def test_faq_categories_prefixes_each_segment():
    assert faq_categories("iffcotokio", ["health-insurance", "family"]) == [
        "iffcotokio:health-insurance", "iffcotokio:family"]


def test_faq_categories_empty_hierarchy():
    assert faq_categories("iffcotokio", []) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_url_paths.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'mapping.url_paths'`.

- [ ] **Step 3: Write the implementation**

Create `mapping/url_paths.py`:

```python
"""Derive JCR paths from a document's own page URL.

The client's documents state the live URL of the page they describe. Its path
segments are the site's section hierarchy, so one URL determines three things:
where the page lands under the content root, which DAM folder its FAQ
fragments land in, and which tags those fragments carry.

    https://www.iffcotokio.co.in/health-insurance/family-health-insurance/who-should-buy
      hierarchy = [health-insurance, family-health-insurance]
      slug      = who-should-buy

Pure string handling -- no I/O, no config objects. Callers pass the roots.
"""

from mapping.slug import slugify


def split_site_url(url: str, origin: str) -> tuple[list[str], str]:
    """Split a site URL into (hierarchy_segments, page_slug).

    Returns ([], "") for an empty URL or one that is not on `origin` -- the
    caller treats that as "no path could be derived" rather than guessing a
    location for the page.
    """
    url = (url or "").strip()
    origin = (origin or "").rstrip("/")
    if not url or not origin:
        return [], ""
    if not url.lower().startswith(origin.lower() + "/"):
        return [], ""
    path = url[len(origin):].split("?", 1)[0].split("#", 1)[0].strip("/")
    if not path:
        return [], ""
    segments = [s for s in path.split("/") if s]
    # a trailing .html is a rendering suffix, not part of the node name
    if segments[-1].lower().endswith(".html"):
        segments[-1] = segments[-1][: -len(".html")]
    slugged = [slugify(s) for s in segments]
    return slugged[:-1], slugged[-1]


def page_path(content_root: str, hierarchy: list[str], slug: str) -> str:
    return "/".join([content_root.rstrip("/"), *hierarchy, slug])


def cf_folder_path(cf_root: str, hierarchy: list[str]) -> str:
    return "/".join([cf_root.rstrip("/"), *hierarchy])


def faq_categories(namespace: str, hierarchy: list[str]) -> list[str]:
    """One tag per hierarchy segment, matching the client's existing
    fragments (folder segments and tags are 1:1 there)."""
    return [f"{namespace}:{segment}" for segment in hierarchy]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_url_paths.py -v && .venv/Scripts/python.exe -m ruff check .`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add mapping/url_paths.py tests/test_url_paths.py
git commit -m "feat(mapping): derive page hierarchy, CF folder and tags from the doc URL"
```

---

### Task 4: Page paths follow the URL hierarchy

`_pick_page_name` under `slug_from_url` keeps only the last segment, discarding the hierarchy. Move page-path construction onto `url_paths`.

**Files:**
- Modify: `mapping/mapper.py:59-60` and `mapping/mapper.py:156-166`
- Modify: `config/samples/article.pagetypeconfig.json` — `contentRoot`, add `siteOrigin`
- Test: `tests/test_mapper.py`

**Interfaces:**
- Consumes: `split_site_url`, `page_path` from Task 3; `PageTypeConfig.site_origin` from Task 1.
- Produces: `map_ncm_to_node_tree` returns a tree whose `path` includes the hierarchy. Tasks 5–7 read `tree["path"]`.

- [ ] **Step 1: Write the failing test**

In `tests/test_mapper.py`:

`tests/test_mapper.py` already has `_article_config()` (no keyword arguments) and
`_ncm(page_props=None, blocks=None, format_name="v3")`. Add one helper beside them and
leave both originals untouched, so existing tests keep passing:

```python
ORIGIN = "https://www.iffcotokio.co.in"


def _url_config(**over) -> PageTypeConfig:
    """An article config that derives page paths from the doc's own URL."""
    cfg = _article_config()
    cfg.page_name_strategy = "slug_from_url"
    cfg.content_root = "/content/iffcotokio/en"
    cfg.site_origin = ORIGIN
    for key, value in over.items():
        setattr(cfg, key, value)
    return cfg


def test_page_path_includes_url_hierarchy():
    ncm = _ncm(page_props={"title": "T", "url": f"{ORIGIN}/health-insurance/"
                                                f"family-health-insurance/who-should-buy"})
    tree = map_ncm_to_node_tree(ncm, _url_config())
    assert tree["path"] == ("/content/iffcotokio/en/health-insurance/"
                            "family-health-insurance/who-should-buy")
    assert tree["name"] == "who-should-buy"


def test_page_path_single_segment_hierarchy():
    ncm = _ncm(page_props={"title": "T",
                           "url": f"{ORIGIN}/health-insurance/cashless-vs-reimbursement"})
    tree = map_ncm_to_node_tree(ncm, _url_config())
    assert tree["path"] == ("/content/iffcotokio/en/health-insurance/"
                            "cashless-vs-reimbursement")


def test_page_name_unresolvable_without_url_under_slug_from_url():
    """No URL means no derivable location. The page must land on the
    UNRESOLVED_SLUG sentinel so batch mode skips it instead of inventing a
    path under the content root."""
    from mapping.slug import UNRESOLVED_SLUG
    ncm = _ncm(page_props={"title": "T", "url": ""})
    tree = map_ncm_to_node_tree(ncm, _url_config())
    assert tree["name"] == UNRESOLVED_SLUG
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mapper.py -k "hierarchy or unresolvable" -v`

Expected: FAIL — path is `/content/iffcotokio/en/who-should-buy`, hierarchy dropped.

- [ ] **Step 3: Write the implementation**

In `mapping/mapper.py`, add the import and replace the path construction in `map_ncm_to_node_tree`:

```python
from mapping.url_paths import page_path as _build_page_path, split_site_url
```

```python
    page_name, page_jcr_path = _resolve_page_location(ncm, cfg)
```

and use `page_jcr_path` where `page_path` was built. Replace `_pick_page_name` with:

```python
def _resolve_page_location(ncm: dict, cfg: PageTypeConfig) -> tuple[str, str]:
    """Return (node_name, absolute_jcr_path).

    Under `slug_from_url` the URL's own path segments ARE the site hierarchy,
    so the page lands at content_root + hierarchy + slug. Every other strategy
    keeps the flat layout: one name directly under content_root.
    """
    props = ncm["page"]["properties"]
    if cfg.page_name_strategy == "slug_from_url":
        hierarchy, slug = split_site_url(props.get("url") or "", cfg.site_origin)
        if slug:
            return slug, _build_page_path(cfg.content_root, hierarchy, slug)
        # No usable URL -- fall through to the sentinel so batch mode skips
        # the page rather than inventing a location for it.
        name = slugify("")
        return name, _build_page_path(cfg.content_root, [], name)
    if cfg.page_name_strategy == "docx_filename":
        name = slugify(Path(ncm["source"]["path"]).stem)
    else:
        name = slugify(props.get("title", ""))
    return name, _build_page_path(cfg.content_root, [], name)
```

In `config/samples/article.pagetypeconfig.json` set `"contentRoot": "/content/iffcotokio/en"` and add `"siteOrigin": "https://www.iffcotokio.co.in"`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q`

Expected: the three new tests pass. Existing mapper tests that asserted a flat `slug_from_url` path need their expected path updated to include the hierarchy — that is the behaviour change this task delivers.

- [ ] **Step 5: Commit**

```bash
git add mapping/mapper.py config/samples/article.pagetypeconfig.json tests/test_mapper.py
git commit -m "feat(mapping): place pages under their URL-derived section hierarchy"
```

---

### Task 5: FAQ config block and Content Fragment trees

**Files:**
- Modify: `aem_client/config.py` — `FaqConfig` dataclass, `faq` field, rename entry
- Create: `mapping/faq_cf.py`
- Test: `tests/test_faq_cf.py`, `tests/test_aem_config.py`

**Interfaces:**
- Consumes: `faq_categories`, `cf_folder_path` from Task 3; the `faq` NCM block shape from Task 2.
- Produces:
  - `FaqConfig(cf_root, cf_model, tag_namespace, field_names: dict, page_component: dict)` with `PageTypeConfig.faq: FaqConfig | None`
  - `mapping.faq_cf.build_faq_cf_trees(ncm: dict, cfg: PageTypeConfig, hierarchy: list[str]) -> list[dict]` — each tree is a node dict carrying a `"path"` key, consumed by Tasks 6–8.

- [ ] **Step 1: Write the failing test**

Create `tests/test_faq_cf.py`:

```python
"""FAQ NCM blocks -> Content Fragment node trees."""

from aem_client.config import FaqConfig, PageTypeConfig
from mapping.faq_cf import build_faq_cf_trees


def _cfg(**over):
    base = dict(
        type="article", template_path="/conf/t", content_root="/content/iffcotokio/en",
        dam_root="/content/dam/x", page_name_strategy="slug_from_url",
        component_mappings={},
        faq=FaqConfig(
            cf_root="/content/dam/iffcotokio/content-fragments/faq",
            cf_model="/conf/iffcotokio/settings/dam/cfm/models/faq-template",
            tag_namespace="iffcotokio",
            field_names={"question": "question", "answer": "answer",
                         "answerHTML": "answerHTML", "category": "faqCategory"},
            page_component={"resourceType": "iffcotokio/components/faq",
                            "pathsProperty": "fragmentPaths"},
        ),
    )
    base.update(over)
    return PageTypeConfig(**base)


def _ncm(items):
    return {"page": {"properties": {}}, "source": {"path": "x.docx"},
            "blocks": [{"name": "faq", "variant": None, "fields": {"title": "FAQs"},
                        "items": [{"id": None, "implicit": True, "fields": f}
                                  for f in items], "table": None}]}


def test_builds_one_tree_per_question():
    ncm = _ncm([
        {"question": "Can parents be included?", "htmltext": "<p>Yes.</p>"},
        {"question": "Is it mandatory?", "htmltext": "<p>No.</p>"},
    ])
    trees = build_faq_cf_trees(ncm, _cfg(), ["health-insurance", "family"])
    assert len(trees) == 2
    assert trees[0]["path"] == (
        "/content/dam/iffcotokio/content-fragments/faq/health-insurance/family/"
        "can-parents-be-included")
    assert trees[0]["name"] == "can-parents-be-included"
    assert trees[0]["primary_type"] == "dam:Asset"


def test_master_carries_question_answer_and_tags():
    trees = build_faq_cf_trees(
        _ncm([{"question": "Can parents be included?",
               "htmltext": "<p>Yes, <b>always</b>.</p>"}]),
        _cfg(), ["health-insurance", "family"])
    jcr_content = trees[0]["children"][0]
    assert jcr_content["properties"]["contentFragment"] is True
    assert jcr_content["properties"]["jcr:title"] == "Can parents be included?"
    data = jcr_content["children"][0]
    assert data["properties"]["cq:model"] == (
        "/conf/iffcotokio/settings/dam/cfm/models/faq-template")
    master = data["children"][0]["properties"]
    # trailing "?" stripped on the question field, kept on jcr:title --
    # matches the client's existing fragments
    assert master["question"] == "Can parents be included"
    assert master["answerHTML"] == "<p>Yes, <b>always</b>.</p>"
    assert master["answer"] == "Yes, always."
    assert master["faqCategory"] == ["iffcotokio:health-insurance", "iffcotokio:family"]
    assert master["jcr:mixinTypes"] == ["cq:Taggable"]


def test_no_jcr_uuid_is_emitted():
    """AEM assigns a fresh UUID at install. Shipping one would pin it."""
    trees = build_faq_cf_trees(_ncm([{"question": "Q?", "htmltext": "<p>A.</p>"}]),
                               _cfg(), ["a"])
    assert "jcr:uuid" not in trees[0]["properties"]
    assert trees[0]["properties"]["jcr:mixinTypes"] == ["mix:referenceable"]


def test_duplicate_question_slugs_are_suffixed():
    trees = build_faq_cf_trees(
        _ncm([{"question": "Is it covered?", "htmltext": "<p>Yes.</p>"},
              {"question": "Is it covered?", "htmltext": "<p>Sometimes.</p>"}]),
        _cfg(), ["a"])
    assert trees[0]["name"] == "is-it-covered"
    assert trees[1]["name"] == "is-it-covered-2"


def test_no_faq_config_yields_no_trees():
    trees = build_faq_cf_trees(_ncm([{"question": "Q?", "htmltext": "<p>A.</p>"}]),
                               _cfg(faq=None), ["a"])
    assert trees == []


def test_item_without_question_is_skipped():
    trees = build_faq_cf_trees(_ncm([{"htmltext": "<p>orphan</p>"}]), _cfg(), ["a"])
    assert trees == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_faq_cf.py -v`

Expected: FAIL — `cannot import name 'FaqConfig'`.

- [ ] **Step 3: Write the implementation**

In `aem_client/config.py`:

```python
@dataclass
class FaqConfig:
    """Everything the FAQ half of the pipeline needs from AEM. Absent from the
    CF means FAQ Content Fragments are not generated at all."""
    cf_root: str
    cf_model: str
    tag_namespace: str = "iffcotokio"
    field_names: dict = field(default_factory=lambda: {
        "question": "question", "answer": "answer",
        "answerHTML": "answerHTML", "category": "faqCategory",
    })
    page_component: dict = field(default_factory=dict)

    _RENAME = {
        "cfRoot": "cf_root", "cfModel": "cf_model",
        "tagNamespace": "tag_namespace", "fieldNames": "field_names",
        "pageComponent": "page_component",
    }

    @classmethod
    def from_dict(cls, raw: dict) -> "FaqConfig":
        normalized = {}
        for k, v in raw.items():
            snake = cls._RENAME.get(k, k)
            if snake in cls.__dataclass_fields__:
                normalized[snake] = v
        missing = {"cf_root", "cf_model"} - normalized.keys()
        if missing:
            raise KeyError(f"faq config missing required fields: {sorted(missing)}")
        return cls(**normalized)
```

Add to `PageTypeConfig`: `faq: "FaqConfig | None" = None`, the rename entry `"faq": "faq"`, and in `from_dict`, after normalization:

```python
        raw_faq = normalized.get("faq")
        if isinstance(raw_faq, dict):
            normalized["faq"] = FaqConfig.from_dict(raw_faq)
```

Create `mapping/faq_cf.py`:

```python
"""FAQ NCM blocks -> AEM Content Fragment node trees.

A Content Fragment is a dam:Asset whose jcr:content carries
contentFragment=true and a `data/master` node holding the model's fields. That
is an ordinary node tree, so the existing FileVault serializer emits it with no
changes -- this module only has to produce the right shape.

Shape mirrors the client's reference fragment: jcr:title keeps the question's
trailing "?", the `question` field drops it.
"""

import re

from aem_client.config import PageTypeConfig
from mapping.slug import slugify
from mapping.url_paths import cf_folder_path, faq_categories

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    """Plain-text twin of the RTE HTML, for the model's `answer` field."""
    text = _TAG_RE.sub(" ", html or "")
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


def build_faq_cf_trees(ncm: dict, cfg: PageTypeConfig, hierarchy: list[str]) -> list[dict]:
    """One CF node tree per FAQ question, in document order.

    Returns [] when the config declares no faq block -- pages still build, the
    fragments are simply not generated.
    """
    faq_cfg = cfg.faq
    if faq_cfg is None:
        return []
    folder = cf_folder_path(faq_cfg.cf_root, hierarchy)
    categories = faq_categories(faq_cfg.tag_namespace, hierarchy)
    names = faq_cfg.field_names
    trees: list[dict] = []
    seen: dict[str, int] = {}
    for block in ncm.get("blocks", []):
        if block.get("name") != "faq":
            continue
        for item in block.get("items", []):
            fields = item.get("fields", {})
            question = (fields.get("question") or "").strip()
            if not question:
                continue  # orphan answer with no question -- nothing to key on
            slug = slugify(question)
            seen[slug] = seen.get(slug, 0) + 1
            if seen[slug] > 1:
                slug = f"{slug}-{seen[slug]}"
            html = fields.get("htmltext") or ""
            master = {
                "jcr:mixinTypes": ["cq:Taggable"],
                names["question"]: question.rstrip("?").strip(),
                names["answer"]: _html_to_text(html),
                names["answerHTML"]: html,
            }
            if categories:
                master[names["category"]] = categories
            trees.append({
                "name": slug,
                "path": f"{folder}/{slug}",
                "primary_type": "dam:Asset",
                "properties": {"jcr:mixinTypes": ["mix:referenceable"]},
                "children": [{
                    "name": "jcr:content",
                    "primary_type": "dam:AssetContent",
                    "properties": {"jcr:title": question, "contentFragment": True},
                    "children": [
                        {"name": "data", "primary_type": "nt:unstructured",
                         "properties": {"cq:model": faq_cfg.cf_model},
                         "children": [{"name": "master",
                                       "primary_type": "nt:unstructured",
                                       "properties": master, "children": []}]},
                        {"name": "metadata", "primary_type": "nt:unstructured",
                         "properties": {"jcr:mixinTypes": ["cq:Taggable"]},
                         "children": []},
                        {"name": "related", "primary_type": "nt:unstructured",
                         "properties": {}, "children": []},
                    ],
                }],
            })
    return trees
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_faq_cf.py tests/test_aem_config.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add aem_client/config.py mapping/faq_cf.py tests/test_faq_cf.py tests/test_aem_config.py
git commit -m "feat(mapping): build FAQ Content Fragment node trees from faq blocks"
```

---

### Task 6: Page references its fragments

The FAQ prose leaves the page; the page gains a component holding the fragment paths.

**Files:**
- Modify: `mapping/mapper.py` — attach CF trees and the faq component
- Test: `tests/test_mapper.py`

**Interfaces:**
- Consumes: `build_faq_cf_trees` from Task 5.
- Produces: `map_ncm_to_node_tree` returns a tree carrying three extra top-level keys, all stripped by the packager exactly as `_assets` already is:
  - `tree["_faq_cfs"]: list[dict]` — the CF trees (Task 7 packages them, Task 9 reports them)
  - `tree["_faq_cf_root"]: str` — `cfg.faq.cf_root`, so the packager knows where the hierarchy starts without parsing it back out of a path (Task 7)
  - `tree["_source_url"]: str` — the URL the paths were derived from (Task 9's `url` column)

  All three are omitted when there are no fragments.

- [ ] **Step 1: Write the failing test**

Add these helpers beside `_url_config` from Task 4:

```python
from aem_client.config import FaqConfig


def _config_with_faq(**faq_over) -> PageTypeConfig:
    cfg = _url_config()
    faq_kwargs = dict(
        cf_root="/content/dam/iffcotokio/content-fragments/faq",
        cf_model="/conf/iffcotokio/settings/dam/cfm/models/faq-template",
        tag_namespace="iffcotokio",
        page_component={"resourceType": "iffcotokio/components/faq",
                        "pathsProperty": "fragmentPaths"},
    )
    faq_kwargs.update(faq_over)
    cfg.faq = FaqConfig(**faq_kwargs)
    return cfg


def _ncm_with_faq(url: str) -> dict:
    return _ncm(
        page_props={"title": "T", "url": url},
        blocks=[
            {"name": "page-description", "variant": None,
             "fields": {"htmltext": "<p>Article body.</p>"}, "items": [], "table": None},
            {"name": "faq", "variant": None, "fields": {"title": "FAQs"}, "table": None,
             "items": [
                 {"id": None, "implicit": True,
                  "fields": {"question": "Can parents be included?",
                             "htmltext": "<p>Yes, parents can be included.</p>"}},
                 {"id": None, "implicit": True,
                  "fields": {"question": "Is it mandatory?",
                             "htmltext": "<p>No.</p>"}},
             ]},
        ],
    )


def test_faq_block_becomes_cf_trees_and_a_page_reference():
    cfg = _config_with_faq()
    ncm = _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys")
    tree = map_ncm_to_node_tree(ncm, cfg)

    cfs = tree["_faq_cfs"]
    assert [c["name"] for c in cfs] == ["can-parents-be-included", "is-it-mandatory"]

    root = tree["children"][0]["children"][0]
    faq_nodes = [c for c in root["children"]
                 if c["properties"].get("sling:resourceType")
                 == "iffcotokio/components/faq"]
    assert len(faq_nodes) == 1
    assert faq_nodes[0]["properties"]["fragmentPaths"] == [c["path"] for c in cfs]


def test_faq_block_does_not_leave_rte_prose_on_the_page():
    tree = map_ncm_to_node_tree(
        _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys"), _config_with_faq())
    root = tree["children"][0]["children"][0]
    for node in root["children"]:
        assert "Yes, parents can be included" not in str(node["properties"])


def test_no_faq_page_component_configured_still_emits_cfs():
    """Fragments are the deliverable; the page reference is optional until the
    AEM team supplies the component."""
    tree = map_ncm_to_node_tree(
        _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys"),
        _config_with_faq(page_component={}))
    assert len(tree["_faq_cfs"]) == 2
    root = tree["children"][0]["children"][0]
    assert all("fragmentPaths" not in c["properties"] for c in root["children"])


def test_tree_carries_cf_root_and_source_url_for_downstream_consumers():
    tree = map_ncm_to_node_tree(
        _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys"), _config_with_faq())
    assert tree["_faq_cf_root"] == "/content/dam/iffcotokio/content-fragments/faq"
    assert tree["_source_url"] == f"{ORIGIN}/health-insurance/family/who-buys"


def test_no_faq_block_means_no_faq_keys_on_the_tree():
    tree = map_ncm_to_node_tree(_ncm(page_props={"title": "T", "url": ""}),
                                _config_with_faq())
    assert "_faq_cfs" not in tree
    assert "_faq_cf_root" not in tree
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_mapper.py -k faq -v`

Expected: FAIL — `KeyError: '_faq_cfs'`.

- [ ] **Step 3: Write the implementation**

In `mapping/mapper.py`, inside `map_ncm_to_node_tree` after the page location is resolved and before the jcr:content build, compute the hierarchy once and build the fragments:

```python
    hierarchy, _ = split_site_url(
        ncm["page"]["properties"].get("url") or "", cfg.site_origin)
    faq_cfs = build_faq_cf_trees(ncm, cfg, hierarchy)
```

Skip `faq` blocks when building components (they are represented by the reference node, not inline), and append the reference node to the root container. In `_build_root_container`:

```python
def _build_root_container(ncm: dict, cfg: PageTypeConfig, faq_cfs: list[dict]) -> dict:
    blocks = [b for b in ncm.get("blocks", []) if b["name"] != "faq"]
    children = [_build_component(block, i, cfg) for i, block in enumerate(blocks)]
    ref = _build_faq_reference(faq_cfs, cfg, len(children))
    if ref is not None:
        children.append(ref)
    return {
        "name": "root",
        "primary_type": "nt:unstructured",
        "properties": {"sling:resourceType": cfg.container_resource_type.strip()},
        "children": children,
    }


def _build_faq_reference(faq_cfs: list[dict], cfg: PageTypeConfig, index: int) -> dict | None:
    """A node pointing the page at its generated fragments. Returns None when
    there are no fragments, or when the AEM team has not yet supplied the
    component -- the fragments are still generated either way."""
    if not faq_cfs or cfg.faq is None:
        return None
    component = cfg.faq.page_component or {}
    resource_type = (component.get("resourceType") or "").strip()
    paths_property = (component.get("pathsProperty") or "").strip()
    if not resource_type or not paths_property:
        return None
    return {
        "name": f"faq_{index}",
        "primary_type": "nt:unstructured",
        "properties": {
            "sling:resourceType": resource_type,
            paths_property: [c["path"] for c in faq_cfs],
        },
        "children": [],
    }
```

Thread `faq_cfs` through `_build_jcr_content` to `_build_root_container`, and attach the
three downstream keys to the returned tree:

```python
    if faq_cfs:
        # Stripped by the packager before XML serialization, exactly like
        # `_assets`. `_faq_cf_root` saves the packager from reverse-engineering
        # where the hierarchy starts inside a fragment path; `_source_url`
        # feeds the batch report's `url` column.
        tree["_faq_cfs"] = faq_cfs
        tree["_faq_cf_root"] = cfg.faq.cf_root
        tree["_source_url"] = ncm["page"]["properties"].get("url") or ""
```

In template-overlay mode, append the reference node to the fallback parent using the same helper so both mapper modes behave alike.

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check .`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add mapping/mapper.py tests/test_mapper.py
git commit -m "feat(mapping): attach FAQ fragments and a page-side reference node"
```

---

### Task 7: Package the Content Fragments

**Files:**
- Modify: `aem/package_generator/package.py` — CF entries, folder nodes, filter roots
- Modify: `ui/batch_service.py` — carry `_faq_cfs` like `_assets`
- Modify: `ui/app.py` — re-attach on download
- Test: `tests/test_package.py`, `tests/test_batch_service.py`

**Interfaces:**
- Consumes: `tree["_faq_cfs"]` from Task 6.
- Produces: `build_batch_package(trees, meta)` emits `jcr_root/<cf_path>/.content.xml` per fragment, `sling:Folder` `.content.xml` per hierarchy level, and a filter root per fragment. `BatchEntry.faq_cfs: list[dict]`.

- [ ] **Step 1: Write the failing test**

In `tests/test_package.py`:

```python
def _cf_tree(path, name):
    return {"name": name, "path": path, "primary_type": "dam:Asset",
            "properties": {"jcr:mixinTypes": ["mix:referenceable"]},
            "children": [{"name": "jcr:content", "primary_type": "dam:AssetContent",
                          "properties": {"contentFragment": True, "jcr:title": "Q?"},
                          "children": []}]}


def test_batch_package_writes_cf_content_xml_and_filter_root():
    tree = {"name": "p", "path": "/content/iffcotokio/en/health-insurance/p",
            "primary_type": "cq:Page", "properties": {}, "children": [],
            "_faq_cfs": [_cf_tree(
                "/content/dam/x/faq/health-insurance/q-one", "q-one")]}
    data = build_batch_package([tree], PackageMeta(name="b"))
    with zipfile.ZipFile(BytesIO(data)) as zf:
        names = zf.namelist()
        assert "jcr_root/content/dam/x/faq/health-insurance/q-one/.content.xml" in names
        cf_xml = zf.read(
            "jcr_root/content/dam/x/faq/health-insurance/q-one/.content.xml").decode()
        assert 'jcr:primaryType="dam:Asset"' in cf_xml
        assert 'contentFragment="{Boolean}true"' in cf_xml
        filters = zf.read("META-INF/vault/filter.xml").decode()
        assert '<filter root="/content/dam/x/faq/health-insurance/q-one"/>' in filters


def test_batch_package_writes_sling_folder_for_each_cf_hierarchy_level():
    tree = {"name": "p", "path": "/content/iffcotokio/en/p", "primary_type": "cq:Page",
            "properties": {}, "children": [],
            "_faq_cfs": [_cf_tree(
                "/content/dam/x/faq/health-insurance/family/q-one", "q-one")]}
    data = build_batch_package([tree], PackageMeta(name="b"))
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for folder in ("health-insurance", "health-insurance/family"):
            xml = zf.read(f"jcr_root/content/dam/x/faq/{folder}/.content.xml").decode()
            assert 'jcr:primaryType="sling:Folder"' in xml


def test_cf_key_does_not_leak_into_page_xml():
    tree = {"name": "p", "path": "/content/iffcotokio/en/p", "primary_type": "cq:Page",
            "properties": {}, "children": [],
            "_faq_cfs": [_cf_tree("/content/dam/x/faq/q", "q")]}
    data = build_batch_package([tree], PackageMeta(name="b"))
    with zipfile.ZipFile(BytesIO(data)) as zf:
        page_xml = zf.read("jcr_root/content/iffcotokio/en/p/.content.xml").decode()
        assert "_faq_cfs" not in page_xml


def test_batch_package_is_byte_identical_across_builds_with_cfs():
    tree = {"name": "p", "path": "/content/iffcotokio/en/p", "primary_type": "cq:Page",
            "properties": {}, "children": [],
            "_faq_cfs": [_cf_tree("/content/dam/x/faq/a/q", "q")]}
    meta = PackageMeta(name="b")
    assert build_batch_package([tree], meta) == build_batch_package([tree], meta)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_package.py -k "cf or sling_folder" -v`

Expected: FAIL — CF paths absent from the zip.

- [ ] **Step 3: Write the implementation**

In `aem/package_generator/package.py`, add the folder serializer and extend `build_batch_package`:

```python
_SLING_FOLDER_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<jcr:root xmlns:sling="http://sling.apache.org/jcr/sling/1.0"'
    ' xmlns:jcr="http://www.jcp.org/jcr/1.0"\n'
    '    jcr:primaryType="sling:Folder"/>\n'
)


def _cf_folder_entries(cf_root: str, cf_path: str) -> dict[str, str]:
    """A sling:Folder descriptor for every hierarchy level between the CF root
    and the fragment, so an install into a fresh tree creates real folders
    rather than leaving FileVault to guess a node type."""
    relative = cf_path[len(cf_root):].strip("/")
    segments = relative.split("/")[:-1]   # drop the fragment itself
    entries: dict[str, str] = {}
    current = cf_root.rstrip("/")
    for segment in segments:
        current = f"{current}/{segment}"
        entries[f"jcr_root{current}/.content.xml"] = _SLING_FOLDER_XML
    return entries
```

Declare `all_cf_paths: list[str] = []` beside `all_asset_paths`, then inside the
`for tree in trees:` loop of `build_batch_package`, after the asset handling:

```python
        cf_root = tree.get("_faq_cf_root") or ""
        for cf in (tree.get("_faq_cfs") or []):
            cf_path = cf["path"]
            all_cf_paths.append(cf_path)
            entries[f"jcr_root{cf_path}/.content.xml"] = node_to_content_xml(cf)
            if cf_root:
                entries.update(_cf_folder_entries(cf_root, cf_path))
```

`_faq_cf_root` comes from Task 6, so the root is never reverse-engineered from the path
string.

Extend the filter roots to include them:

```python
    filter_roots = ([t["path"] for t in trees] + all_asset_paths + all_cf_paths)
```

Entry order in the zip is already `sorted(entries)`, and `filter_roots` is built in a
fixed traversal order, so output stays byte-identical across builds.

`node_to_content_xml` already ignores the non-standard `path` key, exactly as it ignores `_assets`, so no stripping is needed.

In `ui/batch_service.py`, add `faq_cfs: list[dict] = field(default_factory=list)` to `BatchEntry`, pop **only** `_faq_cfs` off the tree beside `_assets`, and round-trip it through `entries_to_json_list` / `entries_from_json_list` — it is already JSON-safe, so no base64 step. In `ui/app.py:download_batch_package`, re-attach it to the tree copy beside `_assets`.

Leave `_faq_cf_root` and `_source_url` **on** the tree: both are plain strings, so they
survive the JSON round-trip untouched, the packager needs `_faq_cf_root` on the
re-attached tree, and Task 9 reads `_source_url` from `entry.aem_tree`. Neither reaches
the XML — `node_to_content_xml` reads only `name`, `primary_type`, `properties` and
`children`, which is the same reason `path` and `_assets` have never leaked.

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check .`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add aem/package_generator/package.py ui/batch_service.py ui/app.py tests/test_package.py tests/test_batch_service.py
git commit -m "feat(package): bundle FAQ Content Fragments and their DAM folders"
```

---

### Task 8: Collision detection against live AEM

Overwrite is the chosen policy; the report has to say what got overwritten.

**Files:**
- Modify: `aem_client/fetcher.py` — existence probe
- Modify: `ui/batch_service.py` — record per-fragment collision state
- Test: `tests/test_aem_fetcher.py`, `tests/test_batch_service.py`

**Interfaces:**
- Consumes: `BatchEntry.faq_cfs` from Task 7.
- Produces: `aem_client.fetcher.cf_exists(client, base_url, cf_path, auth_token=None) -> str` returning `"yes" | "no" | "unknown"`. Each `faq_cfs` entry gains a `"collision"` key with that value.

- [ ] **Step 1: Write the failing test**

```python
def test_cf_exists_reports_yes_on_200():
    def handler(request):
        return httpx.Response(200, json={"jcr:primaryType": "dam:Asset"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert cf_exists(client, "http://localhost:4502", "/content/dam/x/q") == "yes"


def test_cf_exists_reports_no_on_404():
    def handler(request):
        return httpx.Response(404)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert cf_exists(client, "http://localhost:4502", "/content/dam/x/q") == "no"


def test_cf_exists_reports_unknown_when_aem_unreachable():
    """The package must still build offline; the report says so honestly
    rather than claiming the path is free."""
    def handler(request):
        raise httpx.ConnectError("refused")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert cf_exists(client, "http://localhost:4502", "/content/dam/x/q") == "unknown"


def test_cf_exists_reports_unknown_on_unexpected_status():
    def handler(request):
        return httpx.Response(500)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert cf_exists(client, "http://localhost:4502", "/content/dam/x/q") == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_aem_fetcher.py -k cf_exists -v`

Expected: FAIL — `cannot import name 'cf_exists'`.

- [ ] **Step 3: Write the implementation**

In `aem_client/fetcher.py`:

```python
def cf_exists(client: httpx.Client, base_url: str, cf_path: str,
              auth_token: str | None = None) -> str:
    """Is a Content Fragment already at `cf_path`?

    Returns "yes", "no", or "unknown". Never raises: the package must build
    with AEM down, and a report that says "unknown" is honest where one that
    says "no" would be a guess.
    """
    base = base_url.rstrip("/")
    path = cf_path if cf_path.startswith("/") else f"/{cf_path}"
    headers = {"authorization": auth_token} if auth_token else None
    try:
        response = client.get(f"{base}{path}.1.json", headers=headers, timeout=5.0)
    except httpx.HTTPError:
        return "unknown"
    if response.status_code == 200:
        return "yes"
    if response.status_code == 404:
        return "no"
    return "unknown"
```

In `ui/batch_service.py`, add the probe and call it at the end of `run_batch`, which
gains two parameters:

```python
def _annotate_collisions(entries: list[BatchEntry], aem_base_url: str | None,
                         auth_token: str | None) -> None:
    """Stamp every fragment with whether its path is already occupied in AEM.

    The chosen policy is overwrite, so this changes nothing about what the
    package does -- it only makes the report able to say what was overwritten.
    With no AEM configured, every fragment reports "unknown" rather than the
    guess that its path is free.
    """
    pending = [cf for entry in entries for cf in entry.faq_cfs]
    if not pending:
        return
    if not aem_base_url:
        for cf in pending:
            cf["collision"] = "unknown"
        return
    with httpx.Client() as client:
        for cf in pending:
            cf["collision"] = cf_exists(client, aem_base_url, cf["path"], auth_token)
```

```python
def run_batch(
    files: list[tuple[str, bytes]],
    page_type_config: PageTypeConfig | None = None,
    aem_base_url: str | None = None,
    auth_token: str | None = None,
) -> BatchResult:
```

with `_annotate_collisions(entries, aem_base_url, auth_token)` immediately before the
`return BatchResult(...)`. Add `import httpx` and
`from aem_client.fetcher import cf_exists` at the top.

In `ui/app.py:run_batch_route`:

```python
    result = run_batch(
        files,
        page_type_config=page_type_cfg,
        aem_base_url=site.aem_base_url,
        auth_token=os.environ.get(site.auth_token_env),
    )
```

Add a batch-service test that a run with no `aem_base_url` stamps every fragment
`"unknown"` and makes no network call.

- [ ] **Step 4: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check .`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add aem_client/fetcher.py ui/batch_service.py ui/app.py tests/test_aem_fetcher.py tests/test_batch_service.py
git commit -m "feat(aem): best-effort Content Fragment collision detection"
```

---

### Task 9: Batch CSV report

**Files:**
- Create: `ui/report.py`
- Modify: `ui/app.py` — download route
- Modify: `ui/templates/batch_result.html` — download link
- Test: `tests/test_report.py`, `tests/test_ui_routes.py`

**Interfaces:**
- Consumes: `BatchResult` / `BatchEntry` including `faq_cfs` with `collision`.
- Produces: `ui.report.build_report_zip(result) -> bytes` containing `pages.csv` and `faqs.csv`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_report.py`:

```python
"""Batch run -> two-CSV audit report."""

import csv
import io
import zipfile

from ui.batch_service import BatchEntry, BatchResult
from ui.report import build_report_zip


def _read(data: bytes, name: str) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return list(csv.DictReader(io.StringIO(zf.read(name).decode("utf-8-sig"))))


def _entry(**over):
    base = dict(folder="Article5", filename="a5.docx",
                page_path="/content/iffcotokio/en/health-insurance/family/who-buys",
                status="passed", issues=[], aem_tree={}, assets=[],
                faq_cfs=[{"path": "/content/dam/x/faq/health-insurance/family/q-one",
                          "name": "q-one", "collision": "no",
                          "children": [{"name": "jcr:content",
                                        "properties": {"jcr:title": "Q one?"},
                                        "children": [{"name": "data", "children": [
                                            {"name": "master", "properties": {
                                                "answer": "Yes it is.",
                                                "faqCategory": ["iffcotokio:health-insurance"],
                                            }}]}]}]}],
                error=None)
    base.update(over)
    return BatchEntry(**base)


def test_pages_csv_has_one_row_per_doc_including_failures():
    result = BatchResult(filename_top="Articles", entries=[
        _entry(), _entry(folder="Article4", filename="a4.docx", page_path="",
                         status="unresolved", faq_cfs=[], error="no URL found")])
    rows = _read(build_report_zip(result), "pages.csv")
    assert len(rows) == 2
    assert rows[0]["status"] == "passed"
    assert rows[0]["faq_count"] == "1"
    assert rows[1]["status"] == "unresolved"
    assert rows[1]["error"] == "no URL found"
    assert rows[1]["faq_count"] == "0"


def test_faqs_csv_has_one_row_per_fragment_joined_on_page_path():
    result = BatchResult(filename_top="Articles", entries=[_entry()])
    rows = _read(build_report_zip(result), "faqs.csv")
    assert len(rows) == 1
    assert rows[0]["page_path"] == (
        "/content/iffcotokio/en/health-insurance/family/who-buys")
    assert rows[0]["question"] == "Q one?"
    assert rows[0]["cf_path"] == "/content/dam/x/faq/health-insurance/family/q-one"
    assert rows[0]["faq_category"] == "iffcotokio:health-insurance"
    assert rows[0]["collision"] == "no"
    assert rows[0]["answer_chars"] == "10"


def test_collisions_column_counts_colliding_fragments():
    entry = _entry()
    entry.faq_cfs[0]["collision"] = "yes"
    rows = _read(build_report_zip(BatchResult(filename_top="X", entries=[entry])),
                 "pages.csv")
    assert rows[0]["collisions"] == "1"


def test_report_zip_contains_exactly_both_csvs():
    data = build_report_zip(BatchResult(filename_top="X", entries=[_entry()]))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["faqs.csv", "pages.csv"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report.py -v`

Expected: FAIL — `No module named 'ui.report'`.

- [ ] **Step 3: Write the implementation**

Create `ui/report.py`:

```python
"""Batch run -> a two-CSV audit an operator can file or diff.

pages.csv carries one row per source document -- including the ones that
failed, so nothing disappears from the audit. faqs.csv carries one row per
generated fragment. They join on page_path.

Written with utf-8-sig: the client opens these in Excel, which misreads plain
UTF-8 and mangles the non-ASCII characters these documents contain.
"""

import csv
import io
import zipfile

_PAGE_COLUMNS = ["doc", "folder", "status", "url", "page_path",
                 "faq_count", "collisions", "error"]
_FAQ_COLUMNS = ["page_path", "question", "cf_path", "faq_category",
                "collision", "answer_chars"]


def _master_properties(cf: dict) -> dict:
    """Reach the fragment's master node without assuming a fixed index."""
    def find(node, name):
        for child in node.get("children") or []:
            if child.get("name") == name:
                return child
        return None
    jcr_content = find(cf, "jcr:content")
    data = find(jcr_content, "data") if jcr_content else None
    master = find(data, "master") if data else None
    return (master or {}).get("properties", {}) or {}


def _title_of(cf: dict) -> str:
    for child in cf.get("children") or []:
        if child.get("name") == "jcr:content":
            return child.get("properties", {}).get("jcr:title", "")
    return ""


def _page_rows(result) -> list[dict]:
    rows = []
    for entry in result.entries:
        cfs = entry.faq_cfs or []
        rows.append({
            "doc": entry.filename,
            "folder": entry.folder,
            "status": entry.status,
            "url": (entry.aem_tree or {}).get("_source_url", ""),
            "page_path": entry.page_path,
            "faq_count": len(cfs),
            "collisions": sum(1 for c in cfs if c.get("collision") == "yes"),
            "error": entry.error or "",
        })
    return rows


def _faq_rows(result) -> list[dict]:
    rows = []
    for entry in result.entries:
        for cf in entry.faq_cfs or []:
            master = _master_properties(cf)
            categories = master.get("faqCategory") or []
            answer = master.get("answer") or ""
            rows.append({
                "page_path": entry.page_path,
                "question": _title_of(cf),
                "cf_path": cf.get("path", ""),
                "faq_category": ",".join(categories),
                "collision": cf.get("collision", "unknown"),
                "answer_chars": len(answer),
            })
    return rows


def _to_csv(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def build_report_zip(result) -> bytes:
    pages = _to_csv(_PAGE_COLUMNS, _page_rows(result))
    faqs = _to_csv(_FAQ_COLUMNS, _faq_rows(result))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in (("pages.csv", pages), ("faqs.csv", faqs)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text.encode("utf-8-sig"))
    return buf.getvalue()
```

The `url` column reads `tree["_source_url"]`, which Task 6 already sets, so the report
shows the exact URL the paths were derived from.

Add the route to `ui/app.py`:

```python
@app.get("/batch-runs/{run_id}/report.zip")
def download_batch_report(run_id: str):
    record = get_batch_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Batch run not found.")
    data = build_report_zip(record)
    name = (record.filename_top or "batch") + "-report"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
    )
```

`BatchRunRecord` exposes `.entries`, which is all `build_report_zip` reads, so the record can be passed directly.

Add the link to `ui/templates/batch_result.html` beside the existing package download.

- [ ] **Step 4: Write the route test**

In `tests/test_ui_routes.py`, beside the existing batch-package download test:

```python
def test_batch_report_zip_downloads_both_csvs(client, tmp_path):
    run_id = _post_batch_and_get_run_id(client, tmp_path)   # existing helper
    response = client.get(f"/batch-runs/{run_id}/report.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert sorted(zf.namelist()) == ["faqs.csv", "pages.csv"]
        header = zf.read("pages.csv").decode("utf-8-sig").splitlines()[0]
        assert header.startswith("doc,folder,status,url,page_path")


def test_batch_report_zip_unknown_run_is_404(client):
    assert client.get("/batch-runs/does-not-exist/report.zip").status_code == 404
```

Reuse whatever helper the existing batch-download test uses to post a folder and reach a
run id; if it inlines that setup rather than using a helper, inline the same setup here
instead of inventing `_post_batch_and_get_run_id`.

- [ ] **Step 5: Run the full suite**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check .`

Expected: all PASS.

- [ ] **Step 6: End-to-end check against the real documents**

Start the server, batch-upload `content-structure-test/Articles`, then download both the package and the report. Confirm:

- `pages.csv` has seven rows, one per article, each with a `page_path` under its URL hierarchy
- `faqs.csv` totals 30 rows (8 + 5 + 3 + 2 + 5 + 2 + 5)
- the package contains a `.content.xml` under `.../content-fragments/faq/...` for each of those 30

- [ ] **Step 7: Commit**

```bash
git add ui/report.py ui/app.py ui/templates/batch_result.html tests/test_report.py tests/test_ui_routes.py
git commit -m "feat(ui): two-CSV batch audit report with per-page FAQ detail"
```

---

## Definition of done

- All seven client documents produce a page under their URL-derived hierarchy.
- 30 Content Fragments generate across the seven documents, each at
  `{cfRoot}/{hierarchy}/{question-slug}` with `question`, `answer`, `answerHTML` and
  `faqCategory` populated.
- The batch package installs into AEM without touching parent pages.
- `pages.csv` and `faqs.csv` download from the batch result page.
- `.venv/Scripts/python.exe -m pytest -q` green; `ruff check .` clean.

## Follow-ups, explicitly out of scope

- `faq.pageComponent.resourceType` and `pathsProperty` must be supplied from AEM before
  Task 6 emits a page-side reference. Fragments generate without them.
- The empty `models/` NCM contract (see CLAUDE.md, Known gaps).
- Config-driven `ALLOWED_KEYS` / `KNOWN_COMPONENTS` — designed, not built.
