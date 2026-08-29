# V3 Markerless Parsing — Design

**Date:** 2026-07-15
**Status:** Approved

## Background

The client's v3 sample (`Articale page - referance page content v3.docx`) drops
every bracket marker from the doc — no `[metadata]`, no `[component:]`. The
document opens with a bold title + five `Key: value` metadata lines and then
runs as continuous article prose (paragraphs, Word `Heading` styles, bullets,
one differences table, an FAQ block, a closing "Final Thought" section).

Content shape observed in the sample:

- Line 0 — bold H1 (page title)
- Lines 1–4 — `Old URL:` / `New Required URL:` / `Meta Title:` / `Meta Description:` / `Meta keywords:` (the sample is missing `Old URL:` — that's a "new page" and the value should serialize as `""`)
- Body — mix of Word `Heading 2/3`, `<p>`, `<ul>`, one `<table>` (differences)
- Bold-alone paragraph `FAQS` — starts the FAQ block
- Alternating bold-question / plain-answer pairs
- Bold-alone paragraph `Final Thought` — starts a closing paragraph

Section boundaries are visually delimited by Word horizontal rules in the
authored doc, but they don't always survive to the docx XML (as in this
sample). We fall back to bold-label boundaries (`FAQS`, `Final Thought`).

## Decision

**Extend the existing pipeline** — `parse_docx` gains a `format` parameter
and auto-detects when unset. A new `_V3Parser` (sibling of `_DslParser`)
handles v3; strict/lenient path unchanged. Format detection lives in a
tiny new module so validators, UI, and CLI all share it.

## 1. Data model (NCM)

```json
{
  "version": "0.1",
  "format": "v3",
  "source": {"type": "word", "path": "..."},
  "page": {
    "properties": {
      "h1": "Cashless Treatment vs Reimbursement Claims",
      "old_url": "",
      "url": "https://www.iffcotokio.co.in/health-insurance/cashless-treatment-vs-reimbursement-claims",
      "title": "Cashless vs Reimbursement Health Insurance Claims",
      "description": "Learn about the cashless...",
      "keywords": "health insurance claim process, ..."
    }
  },
  "blocks": [
    {
      "name": "page-description",
      "variant": null,
      "fields": {"body": "<h2>...</h2><p>...</p><ul><li>...</li></ul><table>...</table>"},
      "items": [],
      "table": null
    },
    {
      "name": "faq",
      "variant": null,
      "fields": {"title": "FAQS"},
      "items": [
        {"id": null, "implicit": true,
         "fields": {"question": "What is pre-authorization in health insurance?",
                    "answer": "<p>Pre-authorization is...</p>"},
         "headings": []}
      ],
      "table": null
    },
    {"name": "page-description", "variant": null, "fields": {"body": "<h2>Final Thought</h2><p>...</p>"}, "items": [], "table": null}
  ]
}
```

New top-level `format` field. Missing meta keys serialize as `""`.

## 2. Format detection

```python
def detect_format(document) -> str:
    """Return 'v1v2' or 'v3'. v1/v2 share the marker-based path."""
```

Rules:
- If `[metadata]` marker seen anywhere → `v1v2`
- Else if any line matches `^(Meta Title|Meta Description|Meta keywords|Old URL|New Required URL):` → `v3`
- Else → `v1v2` (validator will surface the missing-metadata error)

## 3. V3 parse rules

1. **Metadata block** (start of document, until first non-meta line or FAQ label):
   - First fully-bold paragraph → `page.properties.h1`
   - Lines matching `Key: value` for the five known keys → normalized snake-case key in `page.properties`
   - After metadata block ends, all 5 keys are ensured present with `""` fallback

2. **Body**: everything after the metadata block until a bold-alone `FAQS` label or `Final Thought` label opens a fresh `page-description` component whose `fields.body` accumulates every paragraph, bullet, table as an RTE block. Word `Heading 2/3` styles produce `<h2>/<h3>` tags in the RTE HTML.

3. **FAQ**: bold "FAQS" label starts a `faq` component (`fields.title = "FAQS"`). Every subsequent fully-bold paragraph opens a new item with `fields.question`; following non-bold paragraphs and bullets accumulate as `fields.answer` (RTE). A bold "Final Thought" or `<hr>` closes the FAQ.

4. **Trailing content** (after FAQ) → new `page-description` component whose body includes the closing label as `<h2>`.

## 4. RTE HTML with Word Heading styles

`_blocks_to_html` extended with a `"heading"` block kind. Levels 2/3 emit `<h2>`/`<h3>`. Existing bullet/para/table branches unchanged.

## 5. Dispatch

```python
def parse_docx(path: str, assets_dir: str | None = None, format: str | None = None) -> dict:
    document = Document(path)
    fmt = format or detect_format(document)
    if fmt == "v3":
        parser = walk_document(document, _V3Parser(assets_dir))
    else:
        parser = walk_document(document, _DslParser(assets_dir))
    return parser.to_dict(str(Path(path)), fmt)
```

`_V3Parser` reuses helpers (`_paragraph_lines`, `_blocks_to_html`,
`_table_to_html`, image extraction) — no duplication.

## 6. Validator

- `KNOWN_COMPONENTS` gains `page-description`, `faq`
- v3 mode: skip `[metadata]`/`[component:]` requirement; require at least one of `title`/`description` in `page.properties`
- warning if any of `url`, `title`, `description`, `keywords` empty (`old_url` empty is fine — signals new page)

## 7. UI

- Home page: dropdown `Format: Auto (recommended) / v1 strict DSL / v2 lenient / v3 article`. Auto is default.
- POST /run passes `format` through the service to the parser.
- Result page: adds a `Detected format: <fmt>` badge.
- Full NCM JSON section (existing) shows the `format` field top-level.

Bulk-upload mode with static version selector: deferred, not in this feature.

## 8. Testing

- `tests/test_format_detect.py` (new): v1 marker doc → `v1v2`; v3 sample → `v3`; blank doc → `v1v2` (default)
- v3 unit tests in `tests/test_v3_parser.py`:
  - metadata block: 5 keys → page.properties, missing key → `""`
  - body: h2/h3 in output HTML
  - table in body → RTE `<table>`
  - FAQ: bold Q + plain A pairs → items
  - "Final Thought" splits FAQ from trailing `page-description`
- Regression: full v1/v2 suite still green
- E2E fixture: real v3 client doc — assert 3 blocks (`page-description`, `faq`, `page-description`), FAQ item count = 9, meta H1 present, `old_url == ""`
