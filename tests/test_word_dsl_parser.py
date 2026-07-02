"""Regression checks for parser/word/dsl_parser.py and validators/word_structure.py.

Builds synthetic .docx files on the fly with python-docx (no Node/docx-js
dependency) so these run anywhere `pytest` runs.
"""

from pathlib import Path

import docx

from parser.word.dsl_parser import parse_docx
from validators.word_structure import validate_docx


def _build_docx(path: Path, builder) -> str:
    document = docx.Document()
    builder(document)
    document.save(str(path))
    return str(path)


def test_parse_docx_extracts_rte_html_and_plain_fields(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=3, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "Test Page"
        table.cell(2, 0).text, table.cell(2, 1).text = "description", "A test page"

        doc.add_paragraph("[component: hero-banner]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Hero Title")
        doc.add_paragraph("description:")
        p = doc.add_paragraph()
        p.add_run("Plain text with ")
        p.add_run("bold text").bold = True
        p.add_run(" after it.")

    path = _build_docx(tmp_path / "sample.docx", build)
    result = parse_docx(path)

    assert result["page"]["properties"]["title"] == "Test Page"
    hero = result["blocks"][0]
    assert hero["name"] == "hero-banner"
    assert hero["fields"]["title"] == "Hero Title"
    assert hero["fields"]["description"] == "<p>Plain text with <strong>bold text</strong> after it.</p>"


def test_parse_docx_table_under_rte_field_becomes_html_table(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        meta = doc.add_table(rows=3, cols=2)
        meta.cell(0, 0).text, meta.cell(0, 1).text = "Property", "Value"
        meta.cell(1, 0).text, meta.cell(1, 1).text = "title", "Test Page"
        meta.cell(2, 0).text, meta.cell(2, 1).text = "description", "A test page"

        doc.add_paragraph("[component: cardandRTE]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Motor Insurance by IFFCO Tokio - A Quick Look")
        doc.add_paragraph("description:")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Key Features", "Benefits"
        table.cell(1, 0).text, table.cell(1, 1).text = "Own Damage Cover", "Available"

    path = _build_docx(tmp_path / "sample.docx", build)
    result = parse_docx(path)

    card = result["blocks"][0]
    assert card["name"] == "cardandRTE"
    assert card["fields"]["description"] == (
        "<table><tr><th>Key Features</th><th>Benefits</th></tr>"
        "<tr><td>Own Damage Cover</td><td>Available</td></tr></table>"
    )
    assert card["table"] is None  # table belonged to the RTE field, not the component


def test_validate_docx_flags_unrecognized_marker_and_missing_metadata(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "Only Title"
        doc.add_paragraph("[component hero-banner]")  # missing colon -> unrecognized marker
        doc.add_paragraph("title:")
        doc.add_paragraph("dropped content")

    path = _build_docx(tmp_path / "broken.docx", build)
    issues = validate_docx(path)
    codes = {i.code for i in issues}

    assert "unrecognized-marker" in codes
    assert "missing-required-metadata" in codes  # description missing
    assert all(i.severity == "error" for i in issues)


def test_validate_docx_flags_card_outside_component(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=3, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "T"
        table.cell(2, 0).text, table.cell(2, 1).text = "description", "D"
        doc.add_paragraph("[card: 01]")  # no active component

    path = _build_docx(tmp_path / "orphan_card.docx", build)
    issues = validate_docx(path)

    assert any(i.code == "card-outside-component" for i in issues)


def test_validate_docx_clean_doc_has_no_errors(tmp_path):
    def build(doc):
        doc.add_paragraph("[metadata]")
        table = doc.add_table(rows=3, cols=2)
        table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
        table.cell(1, 0).text, table.cell(1, 1).text = "title", "T"
        table.cell(2, 0).text, table.cell(2, 1).text = "description", "D"
        doc.add_paragraph("[component: hero-banner]")
        doc.add_paragraph("title:")
        doc.add_paragraph("Hero Title")

    path = _build_docx(tmp_path / "clean.docx", build)
    issues = validate_docx(path)

    assert not any(i.severity == "error" for i in issues)
