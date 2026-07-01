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

# ponytail: allow-list of observed DSL keys, not a general "any colon-terminated
# phrase is a key" rule -- plain prose (e.g. "Visit Us: Walk into...") collides
# with that heuristic. Extend as more sample docs/components are seen.
ALLOWED_KEYS = {"title", "description", "body", "icon", "data-of-release", "estimated-read-time"}

# Fields that map to an AEM RTE component and must preserve styling as HTML.
RTE_FIELDS = {"description", "body"}


def _has_numbering(paragraph: Paragraph) -> bool:
    pPr = paragraph._p.pPr
    return pPr is not None and pPr.numPr is not None


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


def _blocks_to_html(blocks: list[dict]) -> str:
    """Serialize accumulated RTE blocks (paragraphs / bullet items) to HTML,
    matching what an AEM Core Text (RTE) component stores in its `text`
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
        else:
            text_html = "".join(_span_html(t, m) for t, m in block["spans"])
            if text_html:
                html_parts.append(f"<p>{text_html}</p>")
            i += 1
    return "".join(html_parts)


class _DslParser:
    def __init__(self):
        self.page_properties: dict[str, str] = {}
        self.blocks: list[dict] = []
        self._in_metadata = False
        self._component: dict | None = None
        self._item: dict | None = None
        self._active_field: str | None = None
        self._current_idx = -1  # body-element index of the line being processed; used by validators/word_structure.py

    def _target_fields(self) -> dict:
        return self._item["fields"] if self._item is not None else self._component["fields"]

    def _start_component(self, name: str, variant: str | None):
        self._component = {
            "type": "component",
            "name": name,
            "variant": variant,
            "fields": {},
            "items": [],
            "headings": [],
            "table": None,
        }
        self.blocks.append(self._component)
        self._item = None
        self._active_field = None
        self._in_metadata = False

    def _start_item(self, item_id: str):
        if self._component is None:
            return
        self._item = {"id": item_id, "fields": {}, "headings": []}
        self._component["items"].append(self._item)
        self._active_field = None

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
        fields = self._target_fields()
        if key in RTE_FIELDS:
            # inline value on the key line itself (rare in practice) carries no marks
            fields[key] = [{"kind": "para", "spans": [(value, {})]}] if value else []
        else:
            fields[key] = value
        self._active_field = key
        return True

    def handle_bullet_line(self, text: str, spans: list[tuple[str, dict]]):
        if self._component is None or self._active_field is None:
            return
        fields = self._target_fields()
        if self._active_field in RTE_FIELDS:
            blocks = fields.setdefault(self._active_field, [])
            blocks.append({"kind": "bullet", "spans": spans})
        else:
            items = fields.setdefault(f"{self._active_field}__list", [])
            items.append(text)

    def handle_plain_line(self, text: str, spans: list[tuple[str, dict]]):
        if self._component is None or self._active_field is None:
            return
        fields = self._target_fields()
        if self._active_field in RTE_FIELDS:
            blocks = fields.setdefault(self._active_field, [])
            blocks.append({"kind": "para", "spans": spans})
        else:
            existing = fields.get(self._active_field, "")
            fields[self._active_field] = f"{existing} {text}".strip() if existing else text

    def handle_heading(self, level: int, text: str):
        target = self._item if self._item is not None else self._component
        if target is not None:
            target["headings"].append({"level": level, "text": text})

    def handle_table(self, table: Table):
        rows = _table_to_rows(table)
        if self._component is None:
            if self._in_metadata:
                for row in rows[1:]:  # first row is the header (Property | Value)
                    if len(row) >= 2 and row[0]:
                        self.page_properties[row[0]] = row[1]
            return
        headers, *data_rows = rows if rows else ([], [])
        self._component["table"] = {"headers": headers, "rows": data_rows}

    def handle_paragraph(self, paragraph: Paragraph):
        style_name = paragraph.style.name if paragraph.style else "Normal"
        is_bullet = _has_numbering(paragraph)
        for line, spans in _paragraph_lines(paragraph):
            if not line:
                continue
            if self.handle_marker_line(line):
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

    def _finalize_rte(self, fields: dict):
        for key in RTE_FIELDS:
            value = fields.get(key)
            if isinstance(value, list):
                fields[key] = _blocks_to_html(value)

    def to_dict(self, source_path: str) -> dict:
        for component in self.blocks:
            self._finalize_rte(component["fields"])
            for item in component["items"]:
                self._finalize_rte(item["fields"])
        return {
            "version": "0.1",
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


def parse_docx(path: str) -> dict:
    document = Document(path)
    parser = walk_document(document, _DslParser())
    return parser.to_dict(str(Path(path)))


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
