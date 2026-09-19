# City template onboarding: spreadsheet-driven pages

Date: 2026-09-20
Status: draft — four decisions open (see Open questions)

## Problem

The pipeline ships article pages today. The client's next template is the health
**city page** (`/health-insurance/city/<city>`). Four sample inputs were analysed:
`Chennai City Page.docx`, `New Delhi City Page.docx`, and the `City Pages-Health`
sheet of `Content tecknopoint.xlsx`.

Five gaps block reusing the article path as-is.

1. **City documents carry no URL.** Every article doc states
   `New Required URL: https://…`, which `_resolve_page_location` splits into the
   page's hierarchy and slug. Neither city document has that line. The URL exists
   **only in the spreadsheet**. Page identity therefore cannot come from the
   document at all.

2. **There is a second input the pipeline has never read.** `connectors/google_sheets/`
   is an empty stub. The spreadsheet holds URL, Summary, Meta Title, Meta Description
   and the image reference for every city.

3. **City documents are a third authoring format.** They use no `[component:]`
   markers, so `format_detect` cannot route them to `dsl_parser`; they are not the
   markerless article shape either, so `v3_parser` does not fit. Sections are
   delimited by Word heading styles alone.

4. **The two sample documents disagree with each other.** Nine structural
   divergences in a sample of two — body style, bullet mechanism, meta paragraph
   style, Quick Summary placement, heading levels, FAQ question style.

5. **Assets live in Google Drive.** The sheet's `Image Link` column is one folder
   URL repeated on every row, not a per-city file. `connectors/google_drive/` is
   also an empty stub.

Separately, the analysis surfaced a defect in existing batch code — see
[Defect: collision key](#defect-collision-key).

## Decisions

Settled during design. Not revisited during implementation.

| Decision | Choice |
|---|---|
| Page register | The **spreadsheet row is the page record**. A city page is one row plus one document. No row, no page. |
| Page identity | Inject the row's URL into `ncm.page.properties.url` **before** mapping, so the existing `slug_from_url` path applies unchanged. |
| Metadata precedence | Sheet wins, document is fallback. Configurable as `metaPrecedence`. |
| Row ↔ document join | Explicit `Doc Filename` column when present; otherwise normalised containment match. Ambiguous and unmatched both reported, never guessed silently. |
| Parser | A new `city` profile. Not an extension of `dsl_parser` or `v3_parser`. |
| Style divergence | Normalised by a **config-driven** alias table, not hardcoded style names. |
| Trailing static sections | Dropped by config (`dropSections`), not by code. |
| Quick Summary | A mapped field — its text is per-city. |
| Popular Cities, network block | Template-static. Zero pipeline work; the overlay carries them through. |
| Collision key | Fixed to the **full page path**. See the defect section. |

## Approach

### The central move: the sheet supplies the URL

`_resolve_page_location` (`mapping/mapper.py:190`) already does everything city pages
need:

```python
if cfg.page_name_strategy == "slug_from_url":
    hierarchy, slug = split_site_url(props.get("url") or "", cfg.site_origin)
    if slug:
        return slug, _build_page_path(cfg.content_root, hierarchy, slug)
```

`split_site_url` turns `https://www.iffcotokio.co.in/health-insurance/city/chennai`
into `(["health-insurance", "city"], "chennai")`. The same hierarchy already drives
the FAQ Content Fragment folder and the `faqCategory` tags.

So the design does **not** add a `slug_from_sheet` strategy or touch path logic.
It writes the sheet's URL into `ncm["page"]["properties"]["url"]` during the merge
step, and every downstream consumer — page path, section hierarchy, CF folder,
tags — works untouched.

Rejected: a new page-name strategy (duplicates working code), and deriving the path
from the `Page` column (17 rows across the workbook show `Page` and `URL` disagreeing;
`Make/Model` against a URL ending `seltos` is the worst case).

### Data flow

```
                    ┌─ sheet row ──→ PageRecord {url, summary, meta_*, image_ref}
                    │                      │
  XLSX ─→ sheets.read│                     │ merge (sheet wins)
                    │                      ▼
  docx ─→ format_detect ─→ city_parser ─→ NCM ─→ mapper ─→ node tree ─→ .zip
                                                   ▲
  Drive folder ─→ drive.resolve ──→ {name: bytes} ─┘
```

The merge writes into `ncm["page"]["properties"]`; nothing downstream learns that a
spreadsheet exists.

### Plumbing the new inputs

`run_pipeline` grows one parameter, following the exact shape `page_assets` took in
Phase 2B:

```python
def run_pipeline(
    docx_path: str,
    force: bool = False,
    assets_dir: str | None = None,
    format: str | None = None,
    page_type_config: PageTypeConfig | None = None,
    page_assets: dict[str, bytes] | None = None,
    page_record: dict | None = None,      # NEW - one spreadsheet row
) -> RunResult:
```

`page_record` reaches `map_ncm_to_node_tree` and is merged there, beside the existing
`_inject_page_meta_into_blocks` step. No new abstraction: `ui/service.py` keeps zero
web-framework imports and the mapper keeps consuming a plain dict.

### The city parser

`parser/word/city_parser.py`, emitting the same NCM dict shape as the other two.

Section model, after normalisation:

| Document region | NCM block | Notes |
|---|---|---|
| H1 page title | *dropped* | Meta Title is authoritative |
| `Image Link:` line | *dropped* | The sheet's `Image Link` wins |
| Quick Summary | `quick-summary` | Both authoring forms; text is per-city |
| `Meta Title:` / `Meta Description:` | `page.properties` | Fallback only; sheet wins |
| Body sections until the FAQ heading | `page-description` | One block, matching article behaviour |
| FAQ heading onward | `faq` | Reuses the existing FAQ → Content Fragment machinery |
| `dropSections` headings onward | *dropped* | Template supplies these nodes |

`format_detect` gains a third branch. City documents are identified by the absence of
`[component:]` and `[metadata]` markers **and** the presence of a configured
`citySignals` phrase (default: a `Quick Summary` heading or label). Documents matching
neither continue to route as they do today, so article behaviour is untouched.

### Style normalisation

Runs before classification, driven by config rather than literals:

```json
"styleAliases": {
  "Normal (Web)":   "Normal",
  "isselectedend":  "Normal",
  "Body Text":      "Normal"
},
"bulletStyles":  ["List Paragraph"],
"faqQuestionStyles": ["Heading 3"]
```

A bullet is recognised by native numbering (`numPr`, as today) **or** membership in
`bulletStyles`. An FAQ question is recognised by a fully-bold paragraph (the existing
`_paragraph_all_bold` helper) **or** membership in `faqQuestionStyles`. Both sample
documents are then handled by one code path.

Every alias added here is a divergence the content team did not have to fix. Every
divergence they *do* fix removes an entry.

### Asset resolution

One function, two backends, selected by config — not a class hierarchy:

```python
def resolve_page_images(spec: str, cfg) -> dict[str, bytes]:
    """spec is a Drive folder/file URL or a local directory path."""
```

Drive mode needs a service account. Local mode is the article template's existing
sibling-file convention, unchanged. A miss leaves `imagePath` unset so the template's
default image survives — the same rule the article hero already follows — and writes a
warning to `pages.csv`.

## Configuration

`config/samples/city.pagetypeconfig.json`. Slot paths are placeholders until Phase A
fetches the real template initial content from AEM.

```json
{
  "type": "city",
  "templatePath": "/conf/iffcotokio/settings/wcm/templates/<city-template>",
  "contentRoot": "/content/iffcotokio/en",
  "siteOrigin": "https://www.iffcotokio.co.in",
  "damRoot": "/content/dam/iffcotokio/city",
  "pageNameStrategy": "slug_from_url",
  "metaPrecedence": "sheet",
  "dropSections": ["Health Insurance in India", "Popular Cities"],
  "styleAliases": { "Normal (Web)": "Normal", "isselectedend": "Normal" },
  "bulletStyles": ["List Paragraph"],
  "faqQuestionStyles": ["Heading 3"],
  "componentMappings": {
    "quick-summary": {
      "resourceType": "iffcotokio/components/content/quick-summary",
      "fieldRenames": { "htmltext": "summaryText" }
    },
    "page-description": {
      "resourceType": "iffcotokio/components/content/page-description",
      "fieldRenames": { "htmltext": "description" },
      "staticProperties": { "textIsRich": "true", "enableContainer": "true" }
    }
  },
  "componentSlots": {
    "quick-summary":    "root/responsivegrid/quick_summary",
    "page-description": "root/responsivegrid/right_par/page_description",
    "faq":              "root/responsivegrid/right_par/faq_cf"
  }
}
```

`contentRoot` is `/content/iffcotokio/en` — **not** `/…/en/health-insurance/city`.
The `health-insurance/city` segments come from the URL hierarchy, exactly as the
article template's sections do. Setting the deeper root would double them.

## Spreadsheet contract

Required columns on `City Pages-Health`. The reader maps aliases per sheet, because
column names drift across the workbook (`URL`/`Url`, `Image Link`/`Image`,
`Sr No`/`S.No`).

| Column | Required | Use |
|---|---|---|
| `URL` | yes | Page path, hierarchy, slug, CF folder, tags |
| `Page` | yes | Display label and join fallback. **Never** used for the slug. |
| `Summary` | yes | Quick Summary text |
| `Meta Title` | yes | `jcr:title` |
| `Meta Description` | yes | `jcr:description` |
| `Image Link` | no | Banner image; template default when absent |
| `Type` | yes | `New` or `Existing` only |
| `Doc Filename` | no | Explicit join key; containment match when absent |

Rows skipped, not failed: fully blank spacers, language separators (`English`,
`Hindi` in `Page` with no URL), and template rows (any cell containing a `[token]`).
Each skip is reported with its reason.

The 90 issues the audit found are listed per cell by
`scripts/audit_content_sheet.py` (to be promoted from the scratch script used during
analysis). Phase B ships that audit as a pre-flight the operator can run before a batch.

## Defect: collision key

`ui/batch_service.py:190` keys collision detection on the **last path segment**:

```python
slug = page_path.rsplit("/", 1)[-1] if page_path else ""
```

Two pages at genuinely different paths that happen to end in the same segment collide
falsely, and the second is dropped from the package with a `collision` status.

This already affects the workbook's Hindi rows:

```
/content/iffcotokio/en/health-insurance/cashless-treatment-vs-reimbursement-claims
/content/iffcotokio/en/hindi/health-insurance/cashless-treatment-vs-reimbursement-claims
```

Distinct paths, identical last segment. Six such pairs exist in `Article-Health`.
Every Hindi page would be silently dropped.

Fix: key on the full `page_path`. One line, plus a regression test asserting that two
pages sharing a last segment under different hierarchies both survive a batch. Ship
this in Phase A — it is independent of city work and affects article batches today.

## Phases

| Phase | Deliverable | Days |
|---|---|---:|
| A | Data contract signed off; city template initial content fetched; collision-key fix | 1.5 |
| B | `connectors/google_sheets/` — reader, schema map, row validation, row↔doc join, pre-flight audit | 2 |
| C | `parser/word/city_parser.py` + style normalisation + `format_detect` third branch | 3 |
| D | `city.pagetypeconfig.json`, Quick Summary mapping, meta precedence | 1.5 |
| E | `connectors/google_drive/` — folder resolution, local fallback | 1.5 |
| F | Auto-generated city pages from the template row | 1.5 |
| G | Integration across all five cities, UAT, boundary fixes | 2.5 |
| | **Total** | **13.5** |

At 8 h/day: **108 hours**. Dropping F costs 12 h; dropping F and E costs 20 h,
leaving an 88 h minimum.

## Testing

Each phase lands with tests, following the existing suite's shape (185 tests today).

- **B** — one test per skip reason (blank, separator, token row), alias mapping across
  all five sheets, duplicate-URL detection, join hit / miss / ambiguous.
- **C** — one test per divergence in the Chennai/New Delhi table, built with
  `python-docx` the way `tests/test_word_dsl_parser.py` builds its fixtures, so no
  client document is needed to run the suite.
- **D** — meta precedence both directions; Quick Summary present and absent;
  template-static nodes survive the overlay untouched.
- **G** — one integration pass over the real city documents, mirroring
  `tests/test_integration_real_docs.py`.

## Open questions

Four decisions block Phase A. Each has a recommended default so work can start on
assumption; changing any after Phase D costs rework.

1. **Metadata precedence** — sheet or document? *Recommended: sheet wins, document
   fallback.* The sheet is the easier surface to maintain at scale, but it is blank
   for all five cities today, so the fallback will carry the first batch.
2. **City listing page** (`/health-insurance/city`, row 7) — in scope? *Recommended:
   out of scope, estimated separately.* It is a different template and component set.
3. **Google Drive service account** — granted? *Recommended: request it.* Without it,
   Phase E drops to local folders and someone downloads images before every batch.
4. **Auto-generated cities** — where does the city list come from? Rows 2–5 have their
   own documents, so the template row targets cities beyond them. No list exists in
   the workbook yet. *Recommended: a new sheet with one city per row.*
