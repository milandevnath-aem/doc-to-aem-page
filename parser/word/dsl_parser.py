"""Parse the bracket/key-value component DSL used in current .docx content
into a normalized JSON dict.

# ponytail: this is a spike matching one real sample doc, not the Sprint-1
# BaseParser/NCM Pydantic contract yet. Formalize once the shape is confirmed
# against more samples.

DSL observed in practice:
    [metadata]                        -> followed by a Property|Value table
    [component: name]                 -> starts a component block
    [component: name||variant]        -> component with a variant modifier
    [card: 01]                        -> starts a repeatable item within
                                          the current component
    key:                               -> sets the active field; its value is
    <value on following line(s)>          the plain text (or, for RTE fields,
                                          the styled paragraphs) that follow
    <bulleted paragraph>              -> native Word numbering (numPr);
                                          becomes a <ul><li> in RTE fields, or
                                          collected into "<field>__list" for
                                          plain fields
    <table>                           -> attached to current component as
                                          "table" (or, before any component,
                                          treated as page metadata)

RTE fields (description, body, ...) preserve Word styling -- bold, italic,
links, bullet lists -- and are emitted as an HTML string, since that is the
literal storage format of an AEM Core Text (RTE) component's `text`
property. Plain fields (title, icon, dates, ...) stay flat strings; AEM
scalar/date fields have no styling to preserve.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from docx.text.paragraph import Paragraph

COMPONENT_RE = re.compile(r"^\[component:\s*([\w-]+)(?:\|\|([\w-]+))?\]$", re.I)
CARD_RE = re.compile(r"^\[card:\s*([\w-]+)\]$", re.I)
MARKER_RE = re.compile(r"^\[([\w -]+)\]$")
KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]{0,40}):-?\s*(.*)$")
IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|svg|gif|webp)$", re.I)

# Real client docs mix v1/v2 `[component:]` markers with v3-style inline
# metadata (Meta Title:, Meta Description:, ...) at the top of the file,
# no `[metadata]` marker/table in sight. Recognize the same keys the v3
# parser handles so bannerTitle / jcr:title / seoDescription have values
# to source from in this hybrid.
_V3_STYLE_META_RE = re.compile(
    r"^(Old URL|New Required URL|Meta Title|Meta Description|Meta keywords)\s*:\s*(.*)$",
    re.I,
)
_V3_STYLE_META_KEY_MAP = {
    "old url": "old_url",
    "new required url": "url",
    "meta title": "title",
    "meta description": "description",
    "meta keywords": "keywords",
}

# ponytail: component-specific asset marker for new-hero-banner. The line
# `Banner Image: <file>` inside a `[component:new-hero-banner]` block names
# the DAM asset in the same folder to wire into the component's imagePath.
# Not added to ALLOWED_KEYS globally: other components would silently accept
# `Image: foo.jpg` as a real field. Extend to more components when a second
# one asks for the same pattern.
BANNER_IMAGE_RE = re.compile(r"^banner\s+image:\s*(\S.+?\.(?:png|jpe?g|svg|gif|webp))\s*$", re.I)
_BANNER_IMAGE_COMPONENTS = {"new-hero-banner"}

# Bold-only paragraph text that reliably marks the FAQ section in real
# client docs. `FAQ`, `FAQs`, `FAQS`, or the long form with an optional
# `(FAQ)` suffix -- any casing. When the parser hits one of these
# inside an implicit page-description block it opens a NEW
# page-description block so the FAQ copy doesn't fold into the main
# body page-description on the rendered page.
_FAQ_LABEL_RE = re.compile(
    r"^(?:faqs?|frequently\s+asked\s+questions(?:\s*\(faq\))?)\s*$", re.I,
)

_BLIP_QN = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_EMBED_QN = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_WRITABLE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}

# ponytail: allow-list of observed DSL keys, not a general "any colon-terminated
# phrase is a key" rule -- plain prose (e.g. "Visit Us: Walk into...") collides
# with that heuristic. Extend as more sample docs/components are seen.
ALLOWED_KEYS = {"title", "description", "body", "icon", "data-of-release", "estimated-read-time"}

# Fields that map to an AEM RTE component and must preserve styling as HTML.
RTE_FIELDS = {"description", "body"}


def _has_numbering(paragraph: Paragraph) -> bool:
    pPr = paragraph._p.pPr
    return pPr is not None and pPr.numPr is not None


def _paragraph_all_bold(paragraph: Paragraph) -> bool:
    """True when every run with visible text on the paragraph is bold.
    Real client docs use fully-bold `[normal]` paragraphs as visual
    section titles instead of a Word Heading style, so this doubles as
    a heading detector for section-boundary decisions."""
    visible = [r for r in paragraph.runs if r.text.strip()]
    return bool(visible) and all(r.bold for r in visible)


def _table_to_rows(table: Table) -> list[list[str]]:
    return [[cell.text.strip() for cell in row.cells] for row in table.rows]


def _append_run_text(lines: list[list[tuple[str, dict]]], text: str, marks: dict):
    """Split run text on soft line-breaks, appending (text, marks) spans to
    the current line and starting a new line on each break."""
    for i, part in enumerate(text.split("\n")):
        if i > 0:
            lines.append([])
        if part:
            lines[-1].append((part, marks))


def _paragraph_lines(paragraph: Paragraph) -> list[tuple[str, list[tuple[str, dict]]]]:
    """Split a paragraph into soft-broken lines, each as (plain_text, spans),
    where spans carries per-run bold/italic/href marks for RTE serialization."""
    lines: list[list[tuple[str, dict]]] = [[]]
    for item in paragraph.iter_inner_content():
        if isinstance(item, Hyperlink):
            for run in item.runs:
                _append_run_text(lines, run.text, {"bold": bool(run.bold), "italic": bool(run.italic), "href": item.address})
        else:
            _append_run_text(lines, item.text, {"bold": bool(item.bold), "italic": bool(item.italic)})
    return [("".join(text for text, _ in line).strip(), line) for line in lines]


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _span_html(text: str, marks: dict) -> str:
    html = _escape_html(text)
    if marks.get("bold"):
        html = f"<strong>{html}</strong>"
    if marks.get("italic"):
        html = f"<em>{html}</em>"
    href = marks.get("href")
    if href:
        html = f'<a href="{_escape_html(href)}">{html}</a>'
    return html


def _table_to_html(headers: list[str], rows: list[list[str]]) -> str:
    parts = ["<table>"]
    if headers:
        parts.append("<tr>" + "".join(f"<th>{_escape_html(h)}</th>" for h in headers) + "</tr>")
    for row in rows:
        parts.append("<tr>" + "".join(f"<td>{_escape_html(c)}</td>" for c in row) + "</tr>")
    parts.append("</table>")
    return "".join(parts)


def _blocks_to_html(blocks: list[dict]) -> str:
    """Serialize accumulated RTE blocks (paragraphs / bullet items / tables) to
    HTML, matching what an AEM Core Text (RTE) component stores in its `text`
    property. Consecutive bullet blocks are grouped into one <ul>."""
    html_parts = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if block["kind"] == "bullet":
            items = []
            while i < len(blocks) and blocks[i]["kind"] == "bullet":
                items.append("<li>" + "".join(_span_html(t, m) for t, m in blocks[i]["spans"]) + "</li>")
                i += 1
            html_parts.append("<ul>" + "".join(items) + "</ul>")
        elif block["kind"] == "table":
            html_parts.append(_table_to_html(block["headers"], block["rows"]))
            i += 1
        elif block["kind"] == "heading":
            text_html = "".join(_span_html(t, m) for t, m in block["spans"])
            html_parts.append(f"<h{block['level']}>{text_html}</h{block['level']}>")
            i += 1
        else:
            text_html = "".join(_span_html(t, m) for t, m in block["spans"])
            if text_html:
                html_parts.append(f"<p>{text_html}</p>")
            i += 1
    return "".join(html_parts)


class _DslParser:
    def __init__(self, assets_dir: str | None = None):
        self.page_properties: dict[str, str] = {}
        self.blocks: list[dict] = []
        self._in_metadata = False
        self._component: dict | None = None
        self._item: dict | None = None
        self._active_field: str | None = None
        self._current_idx = -1  # body-element index of the line being processed; used by validators/word_structure.py
        self._assets_dir = Path(assets_dir) if assets_dir else None
        self._image_count = 0

    def _target_fields(self) -> dict:
        return self._item["fields"] if self._item is not None else self._component["fields"]

    def _start_component(self, name: str, variant: str | None):
        self._component = {
            "type": "component",
            "name": name,
            "variant": variant,
            "fields": {},
            "items": [],
            "table": None,
        }
        self.blocks.append(self._component)
        self._item = None
        self._active_field = None
        self._in_metadata = False

    def _start_item(self, item_id: str):
        if self._component is None:
            return
        self._item = {"id": item_id, "implicit": False, "fields": {}}
        self._component["items"].append(self._item)
        self._active_field = None

    def _start_implicit_item(self) -> dict:
        """v2 lenient authoring: items are delimited by icon lines, pasted
        images, or bold labels instead of explicit [card:] markers."""
        self._item = {"id": None, "implicit": True, "fields": {}}
        self._component["items"].append(self._item)
        self._active_field = None
        return self._item

    def handle_marker_line(self, line: str) -> bool:
        m = COMPONENT_RE.match(line)
        if m:
            self._start_component(m.group(1), m.group(2))
            return True
        m = CARD_RE.match(line)
        if m:
            self._start_item(m.group(1))
            return True
        m = MARKER_RE.match(line)
        if m:
            self._in_metadata = m.group(1).strip().lower() == "metadata"
            self._component = None
            self._item = None
            self._active_field = None
            return True
        return False

    def handle_key_line(self, line: str, spans: list[tuple[str, dict]]) -> bool:
        m = KEY_RE.match(line)
        if not m:
            return False
        key, value = m.group(1).strip().lower(), m.group(2).strip()
        if key not in ALLOWED_KEYS or self._component is None:
            return False
        if key == "icon" and IMAGE_EXT_RE.search(value) and (self._item is None or self._item["implicit"]):
            # v2 lenient: an icon line at component level (or inside an
            # implicit item) starts a new card item. Explicit [card:] items
            # keep the strict behavior below.
            item = self._start_implicit_item()
            item["fields"]["image"] = {"source": "reference", "name": value, "path": None}
            return True
        fields = self._target_fields()
        if key in RTE_FIELDS:
            # inline value on the key line itself (rare in practice) carries no marks
            fields[key] = [{"kind": "para", "spans": [(value, {})]}] if value else []
            self._active_field = key
        else:
            fields[key] = value
            # inline value completes a scalar field; a bare "key:" keeps the
            # field active so the next line can fill it
            self._active_field = None if value else key
        return True

    def handle_bullet_line(self, text: str, spans: list[tuple[str, dict]]):
        if self._component is None:
            return
        if self._active_field is None:
            # v2 lenient: bullets under unkeyed content belong to the implicit description
            self._target_fields().setdefault("description", []).append({"kind": "bullet", "spans": spans})
            return
        fields = self._target_fields()
        if self._active_field in RTE_FIELDS:
            blocks = fields.setdefault(self._active_field, [])
            blocks.append({"kind": "bullet", "spans": spans})
        else:
            items = fields.setdefault(f"{self._active_field}__list", [])
            items.append(text)

    def handle_plain_line(self, text: str, spans: list[tuple[str, dict]]):
        if self._component is None:
            return
        # Component-specific asset marker MUST run before implicit/RTE dumping,
        # otherwise the "Banner Image: foo.jpg" line ends up as the block's
        # implicit title (first unkeyed line) OR gets folded into the RTE
        # description.
        if (
            self._component["name"] in _BANNER_IMAGE_COMPONENTS
            and self._item is None
        ):
            m = BANNER_IMAGE_RE.match(text)
            if m:
                self._component["fields"]["imageFile"] = m.group(1).strip()
                # Every subsequent unkeyed line inside a new-hero-banner
                # block IS the banner description RTE. Activating the field
                # up front prevents `_handle_implicit_line` from stealing
                # the first prose paragraph as an implicit `title` (a title
                # the mapper wouldn't use anyway -- it takes bannerTitle
                # from the doc's Meta Title).
                self._active_field = "description"
                return
        if self._active_field is None:
            self._handle_implicit_line(text, spans)
            return
        fields = self._target_fields()
        if self._active_field in RTE_FIELDS:
            blocks = fields.setdefault(self._active_field, [])
            blocks.append({"kind": "para", "spans": spans})
        else:
            existing = fields.get(self._active_field, "")
            fields[self._active_field] = f"{existing} {text}".strip() if existing else text

    def _handle_implicit_line(self, text: str, spans: list[tuple[str, dict]]):
        """v2 lenient mapping for unkeyed content (previously dropped): the
        first line becomes the title, a fully-bold line after a title starts a
        new item, everything else accumulates as description."""
        fields = self._target_fields()
        if "title" not in fields:
            fields["title"] = text
            return
        is_bold = all(m.get("bold") for t, m in spans if t.strip()) and any(t.strip() for t, _ in spans)
        if is_bold:
            item = self._start_implicit_item()
            item["fields"]["title"] = text
            return
        fields.setdefault("description", []).append({"kind": "para", "spans": spans})

    def handle_heading(self, level: int, text: str):
        # Word Heading-styled paragraphs at the component level are treated as
        # RTE content of the active field when one is open. Absent an active
        # field they fold into the component's htmltext under an <hN> tag.
        # A heading BEFORE any component (page title in v3-style hybrid docs)
        # is dropped -- the mapper takes bannerTitle / jcr:title from the
        # captured Meta Title, not from a stray H1.
        if self._component is None:
            return
        target_fields = self._target_fields()
        if self._active_field in RTE_FIELDS:
            blocks = target_fields.setdefault(self._active_field, [])
            blocks.append({"kind": "heading", "level": level, "spans": [(text, {})]})

    def handle_table(self, table: Table):
        rows = _table_to_rows(table)
        if self._component is None:
            if self._in_metadata:
                for row in rows[1:]:  # first row is the header (Property | Value)
                    if len(row) >= 2 and row[0]:
                        self.page_properties[row[0]] = row[1]
            return
        headers, *data_rows = rows if rows else ([], [])
        if self._active_field in RTE_FIELDS:
            # a table appearing under an RTE field key is authored content for
            # that field (not AEM's Table component) -- fold it into the RTE
            # HTML instead of the component-level "table" attribute.
            blocks = self._target_fields().setdefault(self._active_field, [])
            blocks.append({"kind": "table", "headers": headers, "rows": data_rows})
            return
        self._component["table"] = {"headers": headers, "rows": data_rows}

    def handle_image(self, part):
        """A pasted (embedded) image: extract it and start a new implicit item,
        mirroring what an `icon: name.ext` line does in v2 authoring."""
        if self._component is None:
            return
        self._image_count += 1
        ext = Path(str(part.partname)).suffix.lower()
        name = f"image-{self._image_count}{ext}"
        path = None
        if self._assets_dir is not None and ext in _WRITABLE_IMAGE_EXTS:
            self._assets_dir.mkdir(parents=True, exist_ok=True)
            target = self._assets_dir / name
            target.write_bytes(part.blob)
            path = target.as_posix()
        image = {"source": "embedded", "name": name, "path": path}
        if self._item is not None and self._item["implicit"] and "title" not in self._item["fields"]:
            existing = self._item["fields"].get("image")
            if existing is not None and existing["source"] == "reference":
                # same card carries both `icon: name.ext` and a pasted image -- pasted wins
                self._item["fields"]["image"] = image
                return
        item = self._start_implicit_item()
        item["fields"]["image"] = image

    def handle_paragraph(self, paragraph: Paragraph):
        for blip in paragraph._p.findall(f".//{_BLIP_QN}"):
            rid = blip.get(_EMBED_QN)
            if rid:
                self.handle_image(paragraph.part.related_parts[rid])
        style_name = paragraph.style.name if paragraph.style else "Normal"
        # Real client v1/v2 docs mark ONLY the hero banner with
        # `[component:new-hero-banner]` and leave the article body
        # unmarked. The natural end of the hero is a Word Heading
        # paragraph OR a fully-bold `[normal]` paragraph acting as a
        # visual section title -- close the hero then and start an
        # implicit page-description so the article body doesn't fold
        # into bannerDescription. Only fires after at least one prose
        # paragraph has accumulated as banner description, so a bold
        # marker line in the same paragraph as `[component:...]`
        # can't close the hero before it has any content.
        is_closer = style_name.startswith("Heading") or _paragraph_all_bold(paragraph)
        if (
            is_closer
            and self._component is not None
            and self._component["name"] in _BANNER_IMAGE_COMPONENTS
            and self._item is None
            and self._component["fields"].get("description")
        ):
            self._close_hero_start_implicit_page_desc()
        # Bold-only "FAQ" heading inside an implicit page-description
        # opens a NEW page-description block for the FAQ section so
        # Q/A copy doesn't fold into the main article body block.
        if (
            _paragraph_all_bold(paragraph)
            and self._component is not None
            and self._component["name"] == "page-description"
            and self._item is None
            and self._component["fields"].get("description")
            and _FAQ_LABEL_RE.match(paragraph.text.strip())
        ):
            self._start_component("page-description", None)
            self._active_field = "description"
        is_bullet = _has_numbering(paragraph)
        for line, spans in _paragraph_lines(paragraph):
            if not line:
                continue
            if self.handle_marker_line(line):
                continue
            if self._try_capture_v3_style_meta(line):
                continue
            if self.handle_key_line(line, spans):
                continue
            if style_name.startswith("Heading"):
                level = int(style_name.replace("Heading", "").strip() or 0)
                self.handle_heading(level, line)
                continue
            if is_bullet:
                self.handle_bullet_line(line, spans)
            else:
                self.handle_plain_line(line, spans)

    def _close_hero_start_implicit_page_desc(self) -> None:
        """End the current new-hero-banner block and open an implicit
        `[component:page-description]` so subsequent Heading/prose/bullet
        content lands under it. The implicit page-description then owns
        an active `description` field so plain lines flow into RTE
        without another explicit `description:` key line."""
        self._start_component("page-description", None)
        self._active_field = "description"

    def _try_capture_v3_style_meta(self, line: str) -> bool:
        """Capture v3-style inline meta keys as page.properties whenever
        the walker is NOT inside a component (real client hybrid docs
        put these at the top of the file with no `[metadata]` table).
        Returns True to short-circuit further per-line handling."""
        if self._component is not None:
            return False
        m = _V3_STYLE_META_RE.match(line)
        if not m:
            return False
        normalized = _V3_STYLE_META_KEY_MAP.get(m.group(1).lower())
        if not normalized:
            return False
        # first-wins so a second occurrence of the same key can't stomp
        # what the operator wrote first
        self.page_properties.setdefault(normalized, m.group(2).strip())
        return True

    def _finalize_rte(self, fields: dict):
        # Serialize any accumulated RTE blocks (description, body, ...) and
        # collapse them into a single canonical `htmltext` field so the AEM
        # mapping layer only ever looks at one key regardless of the source
        # authoring key. body wins over description when both are present.
        html = None
        for key in ("body", "description"):
            value = fields.pop(key, None)
            if isinstance(value, list):
                serialized = _blocks_to_html(value)
                if html is None and serialized:
                    html = serialized
            elif isinstance(value, str) and html is None and value:
                html = value
        if html:
            fields["htmltext"] = html

    def to_dict(self, source_path: str, format_name: str = "v1v2") -> dict:
        for component in self.blocks:
            self._finalize_rte(component["fields"])
            for item in component["items"]:
                self._finalize_rte(item["fields"])
        return {
            "version": "0.1",
            "format": format_name,
            "source": {"type": "word", "path": source_path},
            "page": {"properties": self.page_properties},
            "blocks": self.blocks,
        }


def walk_document(document: Document, parser: "_DslParser") -> "_DslParser":
    """Drive a _DslParser (or subclass, e.g. the structure validator) over a
    document's body in order. Shared so the validator sees exactly the same
    marker/line classification the real parser uses -- no separate walk to
    drift out of sync."""
    for idx, child in enumerate(document.element.body.iterchildren()):
        parser._current_idx = idx
        tag = child.tag.split("}")[-1]
        if tag == "p":
            parser.handle_paragraph(Paragraph(child, document))
        elif tag == "tbl":
            parser.handle_table(Table(child, document))
    return parser


def parse_docx(path: str, assets_dir: str | None = None, format: str | None = None) -> dict:
    """Parse a .docx into NCM JSON.

    `format` controls which parser handles the doc. Omit for auto-detection:
    documents that use `[metadata]` / `[component:]` markers take the strict /
    lenient path (`v1v2`), and markerless article documents (bold H1 +
    `Meta Title:` key lines) take the `v3` path.
    """
    from parser.word.format_detect import detect_format
    from parser.word.v3_parser import _V3Parser

    document = Document(path)
    fmt = format or detect_format(document)
    if fmt == "v3":
        parser = walk_document(document, _V3Parser(assets_dir))
    else:
        parser = walk_document(document, _DslParser(assets_dir))
    return parser.to_dict(str(Path(path)), fmt)


def main():
    ap = argparse.ArgumentParser(description="Parse a DSL-authored .docx into NCM-style JSON")
    ap.add_argument("docx_path")
    ap.add_argument("-o", "--output", help="write JSON to this path instead of stdout")
    ap.add_argument("--force", action="store_true", help="parse and emit JSON even if structural validation found errors")
    args = ap.parse_args()

    from validators.word_structure import validate_docx, print_issues

    issues = validate_docx(args.docx_path)
    if issues:
        print_issues(issues)
    has_errors = any(i.severity == "error" for i in issues)
    if has_errors and not args.force:
        print("\nAborting: fix the errors above (or re-run with --force) before parsing.", file=sys.stderr)
        sys.exit(1)

    result = parse_docx(args.docx_path)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
