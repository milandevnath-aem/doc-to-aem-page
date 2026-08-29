"""V3 markerless article parser.

The v3 format has no bracket markers. A doc looks like:

    <bold H1>
    Old URL: ...            <- optional (missing = new page)
    New Required URL: ...
    Meta Title: ...
    Meta Description: ...
    Meta keywords: ...

    Body paragraphs, Word Heading 2/3 styles, bullets, tables.
    ...
    FAQS                    <- bold-alone label starts the FAQ section
    <bold question>
    Plain answer paragraph...
    <bold question>
    ...
    Final Thought           <- bold-alone label starts the closing block
    Closing paragraph.

The parser reuses helpers (paragraph splitting, RTE HTML serialization,
image extraction) from dsl_parser -- only the state machine differs.
"""

import re
from pathlib import Path

from docx.table import Table
from docx.text.paragraph import Paragraph

from parser.word.dsl_parser import (
    _BLIP_QN,
    _EMBED_QN,
    _WRITABLE_IMAGE_EXTS,
    _blocks_to_html,
    _has_numbering,
    _paragraph_lines,
    _table_to_rows,
)

_META_KEY_MAP = {
    "old url": "old_url",
    "new required url": "url",
    "meta title": "title",
    "meta description": "description",
    "meta keywords": "keywords",
}
_META_KEYS_ORDERED = ["old_url", "url", "title", "description", "keywords"]
_META_KEY_RE = re.compile(
    r"^(Old URL|New Required URL|Meta Title|Meta Description|Meta keywords)\s*:\s*(.*)$",
    re.I,
)

_FAQ_LABEL_RE = re.compile(r"^faqs?$", re.I)
_FINAL_LABEL_RE = re.compile(r"^final thought[s]?$", re.I)


def _all_bold(spans):
    real = [(t, m) for t, m in spans if t.strip()]
    return bool(real) and all(m.get("bold") for _, m in real)


class _V3Parser:
    def __init__(self, assets_dir: str | None = None):
        self.page_properties: dict[str, str] = {"h1": ""}
        self.blocks: list[dict] = []
        self._section: str = "metadata"  # metadata | body | faq | closing
        self._current: dict | None = None
        self._faq_item: dict | None = None
        self._current_idx = -1  # walk_document sets this per body-element
        self._assets_dir = Path(assets_dir) if assets_dir else None
        self._image_count = 0

    def _start_page_description(self):
        self._current = {
            "type": "component",
            "name": "page-description",
            "variant": None,
            "fields": {"body": []},  # list of RTE blocks; finalized to HTML in to_dict
            "items": [],
            "table": None,
        }
        self.blocks.append(self._current)

    def _start_faq(self):
        self._current = {
            "type": "component",
            "name": "faq",
            "variant": None,
            "fields": {"title": "FAQS"},
            "items": [],
            "table": None,
        }
        self.blocks.append(self._current)
        self._faq_item = None

    def _open_faq_item(self, question: str):
        self._faq_item = {
            "id": None,
            "implicit": True,
            # `answer` is the working-list of RTE blocks; renamed to
            # `htmltext` in to_dict once serialized. Keeps the state
            # machine readable while producing the canonical output key.
            "fields": {"question": question, "answer": []},
        }
        self._current["items"].append(self._faq_item)

    def handle_image(self, part):
        if self._section != "body" or self._current is None:
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
        self._current["fields"].setdefault("images", []).append(
            {"source": "embedded", "name": name, "path": path}
        )

    def handle_paragraph(self, paragraph: Paragraph):
        for blip in paragraph._p.findall(f".//{_BLIP_QN}"):
            rid = blip.get(_EMBED_QN)
            if rid:
                self.handle_image(paragraph.part.related_parts[rid])
        style_name = paragraph.style.name if paragraph.style else "Normal"
        is_bullet = _has_numbering(paragraph)
        for line, spans in _paragraph_lines(paragraph):
            if not line:
                continue
            self._dispatch_line(line, spans, style_name, is_bullet)

    def _dispatch_line(self, line, spans, style_name, is_bullet):
        bold_alone = _all_bold(spans) and not is_bullet
        # bold-alone section labels override every other rule
        if bold_alone and _FAQ_LABEL_RE.match(line):
            self._section = "faq"
            self._start_faq()
            return
        if bold_alone and _FINAL_LABEL_RE.match(line):
            self._section = "closing"
            self._start_page_description()
            self._append_body({"kind": "heading", "level": 2, "spans": [(line, {})]})
            return
        if self._section == "metadata":
            self._handle_metadata_line(line, spans, bold_alone)
            return
        if self._section in ("body", "closing"):
            self._append_body_line(line, spans, style_name, is_bullet)
            return
        if self._section == "faq":
            self._handle_faq_line(line, spans, is_bullet, bold_alone)

    def _handle_metadata_line(self, line, spans, bold_alone):
        if not self.page_properties["h1"] and bold_alone:
            self.page_properties["h1"] = line
            return
        m = _META_KEY_RE.match(line)
        if m:
            key_display = m.group(1).lower()
            self.page_properties[_META_KEY_MAP[key_display]] = m.group(2).strip()
            return
        # first non-meta plain line: metadata block ends here, start body
        self._section = "body"
        self._start_page_description()
        self._append_body_line(line, spans, "Normal", False)

    def _append_body(self, block):
        if self._current is None:
            self._start_page_description()
        self._current["fields"]["body"].append(block)

    def _append_body_line(self, line, spans, style_name, is_bullet):
        if is_bullet:
            self._append_body({"kind": "bullet", "spans": spans})
            return
        if style_name.startswith("Heading"):
            level = int(style_name.replace("Heading", "").strip() or 2)
            level = max(2, min(level, 6))
            self._append_body({"kind": "heading", "level": level, "spans": [(line, {})]})
            return
        self._append_body({"kind": "para", "spans": spans})

    def _handle_faq_line(self, line, spans, is_bullet, bold_alone):
        if bold_alone:
            self._open_faq_item(line)
            return
        if self._faq_item is None:
            return  # stray content between FAQS label and first question -- ignore
        if is_bullet:
            self._faq_item["fields"]["answer"].append({"kind": "bullet", "spans": spans})
        else:
            self._faq_item["fields"]["answer"].append({"kind": "para", "spans": spans})

    def handle_table(self, table: Table):
        rows = _table_to_rows(table)
        if not rows:
            return
        headers, *data_rows = rows
        block = {"kind": "table", "headers": headers, "rows": data_rows}
        if self._section == "faq" and self._faq_item is not None:
            self._faq_item["fields"]["answer"].append(block)
            return
        if self._section == "metadata":
            # a table before any body text: shift into body and attach
            self._section = "body"
            self._start_page_description()
        self._append_body(block)

    def to_dict(self, source_path: str, format_name: str = "v3") -> dict:
        # ensure all 5 meta keys present -- missing key serializes as ""
        for k in _META_KEYS_ORDERED:
            self.page_properties.setdefault(k, "")
        for block in self.blocks:
            if block["name"] == "page-description":
                # rename body -> htmltext (single canonical RTE key)
                block["fields"]["htmltext"] = _blocks_to_html(block["fields"].pop("body"))
            elif block["name"] == "faq":
                for item in block["items"]:
                    item["fields"]["htmltext"] = _blocks_to_html(item["fields"].pop("answer"))
        return {
            "version": "0.1",
            "format": format_name,
            "source": {"type": "word", "path": source_path},
            "page": {"properties": self.page_properties},
            "blocks": self.blocks,
        }
