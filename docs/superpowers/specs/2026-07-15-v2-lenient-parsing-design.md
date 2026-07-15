# V2 Lenient Parsing + Image Extraction — Design

**Date:** 2026-07-15
**Status:** Approved (Approach A)

## Background

The client declined to author documents in the full bracket/key-value DSL. Their
v2 sample (`Articale page - referance page content v2.docx`) keeps the DSL
skeleton — `[metadata]` + property table, `[component: name||variant]` markers —
but drops per-field keys. Content inside a component is implicit:

- `icon: <name>.<ext>` lines delimit repeated card items
- the first plain line after a marker (or icon line) is a title
- following paragraphs/bullets are the description
- the `||numbered` variant uses bold `Step N:` labels as item delimiters
- several logical lines may share one paragraph via soft line breaks (Shift+Enter)

The current parser loses ~70% of v2 content (fields without keys are dropped,
each `icon:` overwrites the previous, bold labels are ignored).

Additionally, clients may paste images directly into the document instead of
writing `icon: name.png`. Pasted images live inside the docx zip at
`word/media/` and are referenced at an exact paragraph position.

## Decision: Approach A — extend the existing parser

One `_DslParser` handles both strict (v1 DSL) and lenient (v2) authoring.
Strict keys keep working; lenient rules apply to unkeyed content. No format
detection, no second engine.

Rejected: (B) separate lenient parser module — ~60% duplicated logic, two
engines to maintain, detection heuristic can misfire; (C) preprocessor that
rewrites v2 into strict DSL — images and validation line numbers don't thread
through cleanly.

## 1. Data model (NCM change)

Component block gains `items` alongside existing `fields`:

```json
{
  "name": "featurecards",
  "variant": null,
  "fields": {"title": "Benefits of Buying the Right Insurance Policy"},
  "items": [
    {
      "title": "Financial Protection",
      "description": "<p>Insurance helps manage unexpected expenses...</p>",
      "image": {"source": "reference", "name": "location1.png", "path": null}
    }
  ],
  "table": null
}
```

- `items` is always present — empty list for strict-DSL docs (non-breaking)
- Item `description` is RTE HTML, produced by the existing `_blocks_to_html`
  (paragraphs + bullets + tables all supported)
- `image.source` is `"reference"` (`icon: name.ext`) or `"embedded"` (pasted;
  `path` points to the extracted file). If an item has both, embedded wins.

## 2. Parsing rules (lenient additions)

1. **Soft line breaks:** every paragraph's text is split on `\n` first; each
   line goes through key detection independently.
2. **Item delimiters** — a new item starts on any of:
   - an `icon: <name>.<ext>` line (extension one of png/jpg/jpeg/svg/gif/webp)
   - a paragraph containing a pasted (embedded) image
   - a whole-paragraph-bold label, once the component already has a title
3. **Implicit mapping:**
   - first plain/bold line after `[component:]` marker → component `fields.title`
   - plain content before the first item → component `fields.description`
   - inside an item: first plain line → item `title`; the rest → description blocks
4. **Strict keys unchanged:** `title:`, `description:` etc. still hit the
   existing field logic. Both formats coexist in one document.
5. `teasercomponent` added to the known-components list.

## 3. Image extraction

- `parse_docx(path, assets_dir=None)` — new optional param.
- A paragraph containing `a:blip` → rId → image part blob. With `assets_dir`
  set, blob is written to `assets_dir/image-<n>.<ext>` and the NCM records the
  relative path. With `assets_dir=None`, the NCM records
  `{"source": "embedded", "name": "image-<n>.<ext>", "path": null}` and no
  file is written.
- Extension derived from the docx part name.
- UI service passes `ui/static/assets/<run-id>/` so the result page can show
  thumbnails.

## 4. Validator

- Knows `teasercomponent`.
- Empty-component check counts `items` as content.
- New warning for an embedded image with an unrecognized format (skipped, named).
- Acceptance: the real v2 client doc validates with zero errors.

## 5. UI result page

Items rendered under each component card: title, description HTML, image
thumbnail when a path exists. The Full-NCM-JSON section already shows the raw
model — unchanged.

## 6. Error handling

- Corrupt/unreadable image part → warning issue, parse continues.
- Empty items (delimiter followed by no content) pruned at finalize.
- Strict-format docs behave byte-identically (guarded by existing tests).

## 7. Testing

- Unit: soft-break splitting; icon-delimited items; bold-step items; implicit
  title/description; embedded-image extraction (synthetic docx built with
  `add_picture`); strict-format regression (existing suite untouched).
- End-to-end fixture: the real v2 client doc in `tests/fixtures/`, asserting
  7 blocks, hero title present, item counts (5/7/6 feature cards, 7 steps,
  6 teaser lines).
