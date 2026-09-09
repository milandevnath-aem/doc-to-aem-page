"""Structural sanity checks for a DSL-authored .docx, run before parsing.

Reuses the real parser's own marker/line classification (via
parser.word.dsl_parser.walk_document) so validation can never drift out of
sync with what the parser actually accepts. Catches problems that would
otherwise fail silently -- e.g. a typo'd bracket marker that the parser's
generic-marker fallback treats as a section boundary, quietly dropping
every line until the next real marker.
"""

import argparse
import sys
from dataclasses import dataclass

from docx import Document

from parser.word.dsl_parser import CARD_RE, COMPONENT_RE, MARKER_RE, _DslParser, walk_document
from parser.word.format_detect import detect_format
from parser.word.v3_parser import _V3Parser

# ponytail: hardcoded allow-list for now. Move to config/mappings/*.yaml once
# the mapping layer exists (Sprint 3) so it's one source of truth instead of two.
KNOWN_COMPONENTS = {
    "hero-banner", "new-hero-banner", "cards-with-full-width", "featurecards",
    "cardandrte", "teasercomponent", "page-description", "faq",
}
REQUIRED_METADATA = {"title", "description"}
# v3-format meta keys the parser fills into page.properties. old_url is
# intentionally optional (missing = new page, no redirect needed).
V3_META_KEYS = {"url", "title", "description", "keywords"}


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    paragraph_index: int
    snippet: str = ""
    context: str = ""

    def __str__(self):
        loc = f"body element #{self.paragraph_index}" if self.paragraph_index >= 0 else "document"
        ctx = f" (in component '{self.context}')" if self.context else ""
        head = f"[{self.severity.upper()}] {self.code} -- {loc}{ctx}"
        if self.snippet:
            return f"{head}\n    {self.message}\n    near: {self.snippet!r}"
        return f"{head}\n    {self.message}"


class _ValidatingDslParser(_DslParser):
    def __init__(self):
        super().__init__()
        self.issues: list[Issue] = []
        self._metadata_marker_seen = False
        self._metadata_table_seen = False

    def _record(self, severity, code, message, snippet=""):
        context = self._component["name"] if self._component else ""
        self.issues.append(Issue(severity, code, message, self._current_idx, snippet, context))

    def _component_has_content(self) -> bool:
        c = self._component
        return bool(c and (c["fields"] or c["items"] or c["table"]))

    def _start_component(self, name, variant):
        if self._component is not None and not self._component_has_content():
            self._record("warning", "empty-component",
                f"Component '{self._component['name']}' has no fields, items, or table before the next marker.")
        if name.lower() not in KNOWN_COMPONENTS:
            self._record("error", "unknown-component",
                f"'{name}' is not in the known component list: {sorted(KNOWN_COMPONENTS)}.",
                f"[component: {name}" + (f"||{variant}" if variant else "") + "]")
        super()._start_component(name, variant)

    def _start_item(self, item_id):
        if self._component is None:
            self._record("error", "card-outside-component",
                f"[card: {item_id}] appears with no active [component: ...] before it.", f"[card: {item_id}]")
            return
        super()._start_item(item_id)

    def handle_marker_line(self, line: str) -> bool:
        if COMPONENT_RE.match(line) or CARD_RE.match(line):
            return super().handle_marker_line(line)
        m = MARKER_RE.match(line)
        if m:
            label = m.group(1).strip().lower()
            if label == "metadata":
                self._metadata_marker_seen = True
            else:
                self._record("error", "unrecognized-marker",
                    f"'{line}' does not match [component: name], [component: name||variant], [card: NN], or [metadata]. "
                    "The parser's fallback treats it as a section boundary anyway, so content after it "
                    "may silently be dropped instead of attached to the intended component.",
                    line)
        return super().handle_marker_line(line)

    def handle_table(self, table):
        if self._component is None and self._metadata_marker_seen and not self._metadata_table_seen:
            self._metadata_table_seen = True
        super().handle_table(table)

    def finalize(self) -> list[Issue]:
        if self._component is not None and not self._component_has_content():
            self._record("warning", "empty-component",
                f"Component '{self._component['name']}' has no fields, items, or table before end of document.")

        if not self._metadata_marker_seen:
            self.issues.insert(0, Issue("error", "missing-metadata-marker",
                "No [metadata] marker found anywhere in the document.", 0))
        elif not self._metadata_table_seen:
            self.issues.insert(0, Issue("error", "missing-metadata-table",
                "[metadata] marker found but no Property|Value table follows it.", 0, "[metadata]"))
        else:
            missing = REQUIRED_METADATA - {k.lower() for k in self.page_properties}
            if missing:
                self.issues.insert(0, Issue("error", "missing-required-metadata",
                    f"Metadata table is missing required field(s): {sorted(missing)}.", 0, "[metadata]"))

        for component in self.blocks:
            self._finalize_rte(component["fields"])
            for item in component["items"]:
                self._finalize_rte(item["fields"])
            seen: dict[str, str] = {}
            for item in component["items"]:
                body = item["fields"].get("body")
                if not body:
                    continue
                if body in seen:
                    self.issues.append(Issue("warning", "duplicate-card-content",
                        f"Card '{item['id']}' has identical body content to card '{seen[body]}' in the same "
                        f"component ('{component['name']}') -- likely a copy-paste that wasn't edited.",
                        -1, body[:80] + ("..." if len(body) > 80 else "")))
                else:
                    seen[body] = item["id"]

        return self.issues


class _V3Validator(_V3Parser):
    """Runs the v3 parse and reports issues -- missing meta keys, unknown
    component names inside blocks (none produced today, but leaves room)."""

    def __init__(self):
        super().__init__()
        self.issues: list[Issue] = []

    def finalize(self) -> list[Issue]:
        # trigger the same finalize path so page.properties get "" defaults
        self.to_dict("<validation>", "v3")
        props = self.page_properties
        for k in sorted(V3_META_KEYS):
            if not props.get(k):
                self.issues.append(Issue(
                    "warning", "missing-v3-meta",
                    f"v3 metadata field '{k}' is empty. Fill it in the top block of the document.",
                    0, "", "",
                ))
        return self.issues


def validate_docx(path: str) -> list[Issue]:
    document = Document(path)
    fmt = detect_format(document)
    if fmt == "v3":
        return walk_document(document, _V3Validator()).finalize()
    return walk_document(document, _ValidatingDslParser()).finalize()


def print_issues(issues: list[Issue]):
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    for issue in errors + warnings:
        print(issue, file=sys.stderr)
    print(f"\n{len(errors)} error(s), {len(warnings)} warning(s).", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="Structural sanity check for a DSL-authored .docx")
    ap.add_argument("docx_path")
    args = ap.parse_args()

    issues = validate_docx(args.docx_path)
    if not issues:
        print("No structural issues found.")
        return
    print_issues(issues)
    sys.exit(1 if any(i.severity == "error" for i in issues) else 0)


if __name__ == "__main__":
    main()
