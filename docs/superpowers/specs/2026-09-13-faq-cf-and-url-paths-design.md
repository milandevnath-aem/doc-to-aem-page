# FAQ Content Fragments, URL-derived paths, and batch reporting

Date: 2026-09-13
Status: approved

## Problem

Three gaps block the current pipeline from producing the output the client needs.

1. **FAQs are unstructured.** Every one of the seven real client articles parses as
   `v1v2`. That parser has no FAQ state machine, so the FAQ section lands in a second
   `page-description` RTE blob. The structured `faq` block with question/answer items
   exists only in `_V3Parser`, which never runs on these documents because they carry
   `[component:]` markers. There is nothing for a Content Fragment mapper to consume.

2. **Page paths ignore the site structure.** Every page is generated flat under
   `/content/iffcotokio/en/articles`, discarding the section hierarchy the document's
   own URL already states.

3. **Batch runs are not auditable.** A batch produces a package and an HTML table. There
   is no per-page record an operator can file, diff, or hand to someone else.

## Decisions

These were settled during design and are not revisited during implementation.

| Decision | Choice |
|---|---|
| URL detection | Match on the value: key label contains `url`, value is absolute on `siteOrigin`. Label containing `old` routes to `old_url`. |
| Page vs CF | FAQ prose leaves the page RTE. Page gets an faq component carrying the generated CF paths. |
| CF path collision in AEM | Overwrite. Every collision is reported. |
| Page hierarchy parents | Assumed to exist. Filter roots at the leaf page only. |
| Report shape | Two CSVs (`pages.csv`, `faqs.csv`) joined on `page_path`, delivered as one zip. |

### Accepted risk: overwrite

A FileVault install replaces the node at each filter root, including its `jcr:uuid`.
Overwriting a hand-authored fragment therefore churns its UUID and breaks any existing
page that references it. This was raised during design and the overwrite policy was
chosen deliberately, with collision reporting as the mitigation. Generated fragments
omit `jcr:uuid` so AEM assigns one at install.

Collision detection is best-effort: a GET per CF path against the configured AEM. When
AEM is unreachable the package still builds and every row reports `unknown`.

## Approach

A Content Fragment is a JCR node tree. The existing `filevault.node_to_content_xml`
already emits `{Boolean}true`, `[mix:referenceable]`, multi-value properties and
XML-escaped HTML — verified against the client's reference package before this spec was
written. CF generation therefore adds a mapper and packaging entries, not a serializer.

Rejected: a dedicated CF serializer (duplicates working code) and carrying CFs inside
the page tree to split at package time (couples two unrelated outputs).

## Configuration

All new values come from the PageTypeConfig Content Fragment. Nothing is hardcoded.

```
siteOrigin    "https://www.iffcotokio.co.in"
contentRoot   "/content/iffcotokio/en"          # was /content/iffcotokio/en/articles
faq:
  cfRoot         "/content/dam/iffcotokio/content-fragments/faq"
  cfModel        "/conf/iffcotokio/settings/dam/cfm/models/faq-template"
  tagNamespace   "iffcotokio"
  fieldNames     {question, answer, answerHTML, category}
  pageComponent  {resourceType: "<supplied by AEM team>", pathsProperty: "fragmentPaths"}
```

`faq.pageComponent.resourceType` and `pathsProperty` are required before phase D
produces a page-side reference. Phases A through C do not need them.

Moving `contentRoot` up one level relocates every page this pipeline generates. This is
intended and is a breaking change to existing output.

## Phases

### A — Meta extraction

The seven sample documents spell the URL key four different ways, and two documents lose
metadata today.

| Document | Key as authored | Captured before |
|---|---|---|
| 1, 2, 5, 6 | `New Required URL:` | yes |
| 7 | `New Required URL :` | yes |
| 3 | `Keywords:` | no |
| 4 | `URL Need to be Careated:` | no |

Replace the fixed key list with shape matching. A line is a URL key when its label
contains `url` and its value is an absolute URL whose origin equals `siteOrigin`. A label
containing `old` populates `old_url`; any other populates `url`. Keyword capture widens
to any label containing `keyword`. First-wins is preserved, so a repeated key cannot
overwrite the first value.

Prose cannot trigger this: the line must parse as a `label: value` key line *and* the
value must be an absolute URL on the configured origin.

### B — FAQ parsing in `_DslParser`

Port the `_V3Parser` FAQ state machine. On a bold-alone FAQ label, open a `faq` block
rather than today's second `page-description`. Each subsequent bold-alone line opens a
new item and becomes its `question`. Non-bold paragraphs and bullets accumulate into that
item's answer RTE blocks until the next bold line or the end of the document.

This reverses commit `1865a38` for the FAQ case specifically.

The "two horizontal lines" bounding the section in Word are visual only — every paragraph
carries `pBdr` with all-`nil` values, so there is no rule to detect. The bold label and
the bold/plain alternation are the reliable signals, and they hold in all seven documents
(2 to 8 questions each, answers spanning up to four paragraphs).

### C — URL-derived paths

New module `mapping/url_paths.py`.

```
https://www.iffcotokio.co.in/health-insurance/family-health-insurance/who-should-buy-...

  hierarchy = [health-insurance, family-health-insurance]
  slug      = who-should-buy-...

page_path   = {contentRoot}/health-insurance/family-health-insurance/who-should-buy-...
cf_folder   = {faq.cfRoot}/health-insurance/family-health-insurance
faqCategory = [iffcotokio:health-insurance, iffcotokio:family-health-insurance]
```

Hierarchy depth varies across the samples (one or two segments) and follows from the URL
with no special casing. `_pick_page_name` under `slug_from_url` currently keeps only the
last segment; page path construction moves to this module so the hierarchy survives.

### D — CF generation and packaging

`map_ncm_to_cf_trees(ncm, cfg, hierarchy)` returns one node tree per question, shaped from
the client's reference package:

- `dam:Asset`, `jcr:mixinTypes=[mix:referenceable]`, no `jcr:uuid`
- `jcr:content` — `dam:AssetContent`, `contentFragment={Boolean}true`, `jcr:title` = the
  question including its trailing question mark
- `data` — `cq:model` = `faq.cfModel`
- `master` — `jcr:mixinTypes=[cq:Taggable]`, plus `question` (trailing `?` stripped, per
  the reference), `answer` (plain text), `answerHTML` (RTE HTML), `faqCategory`
  (multi-value tags). Property names come from `faq.fieldNames`.
- empty `metadata` and `related` nodes

Node name is the question slug. The page tree gains an faq component node whose
`pathsProperty` holds the generated CF paths in document order.

The packager emits a `sling:Folder` `.content.xml` for each hierarchy level under
`cfRoot`, one `.content.xml` per fragment, and one filter root per CF leaf so sibling
fragments are never in scope.

### E — Batch report

`ui/report.py` builds both CSVs from a `BatchResult`; route
`/batch-runs/{run_id}/report.zip` serves them.

```
pages.csv  doc, folder, status, url, page_path, faq_count, collisions, error
faqs.csv   page_path, question, cf_path, faq_category, collision, answer_chars
```

Every document gets a row in `pages.csv`, including failures, so nothing disappears from
the audit.

## Error handling

| Case | Behaviour |
|---|---|
| No URL found | status `unresolved`; no page, no CFs; row in `pages.csv` with the reason |
| FAQ label present, no bold questions | warning; page builds; `faq_count=0` |
| Two identical question slugs in one document | second suffixed `-2`; in-package clash, distinct from the AEM overwrite policy |
| `faq` config block absent | CFs skipped with a warning; pages still build |
| AEM unreachable during collision check | package builds; every collision column reports `unknown` |

## Testing

Test-driven, phase by phase. The seven real client documents are the fixtures and cover
every variant that matters: a missing URL, one- and two-segment hierarchies, 2 to 8
questions per document, and multi-paragraph answers.

Each phase lands behind passing tests before the next begins.

## Out of scope

- The empty `models/` NCM contract. Tracked separately in CLAUDE.md's Known gaps.
- Making `ALLOWED_KEYS` and `KNOWN_COMPONENTS` config-driven. Designed, not yet built.
- Mode 2 direct Author API publishing.
