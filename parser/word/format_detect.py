"""Detect authoring format of a Word document.

Returns 'v1v2' for the marker-based DSL (with `[metadata]` and `[component:]`
markers -- both strict and lenient share this path in the parser) or 'v3' for
the markerless article format (bold H1 + `Meta Title:`-style key lines +
article body + FAQ block).

Detection is a fast scan of paragraph text -- no parsing yet.
"""

import re

from docx import Document
from docx.text.paragraph import Paragraph

_META_KEY_RE = re.compile(r"^(Meta Title|Meta Description|Meta keywords|Old URL|New Required URL)\s*:", re.I)


def detect_format(document: Document) -> str:
    saw_meta_key = False
    for child in document.element.body.iterchildren():
        if not child.tag.endswith("}p"):
            continue
        text = Paragraph(child, document).text.strip()
        if not text:
            continue
        if text.lower() == "[metadata]" or text.lower().startswith("[component:"):
            return "v1v2"
        if _META_KEY_RE.match(text):
            saw_meta_key = True
    return "v3" if saw_meta_key else "v1v2"
