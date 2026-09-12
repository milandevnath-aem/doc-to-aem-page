"""Batch run -> two-CSV audit report."""

import csv
import io
import zipfile

from ui.batch_service import BatchEntry, BatchResult
from ui.report import build_report_zip


def _read(data: bytes, name: str) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return list(csv.DictReader(io.StringIO(zf.read(name).decode("utf-8-sig"))))


def _entry(**over):
    base = dict(folder="Article5", filename="a5.docx",
                page_path="/content/iffcotokio/en/health-insurance/family/who-buys",
                status="passed", issues=[], aem_tree={}, assets=[],
                faq_cfs=[{"path": "/content/dam/x/faq/health-insurance/family/q-one",
                          "name": "q-one", "collision": "no",
                          "children": [{"name": "jcr:content",
                                        "properties": {"jcr:title": "Q one?"},
                                        "children": [{"name": "data", "children": [
                                            {"name": "master", "properties": {
                                                "answer": "Yes it is.",
                                                "faqCategory": ["iffcotokio:health-insurance"],
                                            }}]}]}]}],
                error=None)
    base.update(over)
    return BatchEntry(**base)


def test_pages_csv_has_one_row_per_doc_including_failures():
    result = BatchResult(filename_top="Articles", entries=[
        _entry(), _entry(folder="Article4", filename="a4.docx", page_path="",
                         status="unresolved", faq_cfs=[], error="no URL found")])
    rows = _read(build_report_zip(result), "pages.csv")
    assert len(rows) == 2
    assert rows[0]["status"] == "passed"
    assert rows[0]["faq_count"] == "1"
    assert rows[1]["status"] == "unresolved"
    assert rows[1]["error"] == "no URL found"
    assert rows[1]["faq_count"] == "0"


def test_faqs_csv_has_one_row_per_fragment_joined_on_page_path():
    result = BatchResult(filename_top="Articles", entries=[_entry()])
    rows = _read(build_report_zip(result), "faqs.csv")
    assert len(rows) == 1
    assert rows[0]["page_path"] == (
        "/content/iffcotokio/en/health-insurance/family/who-buys")
    assert rows[0]["question"] == "Q one?"
    assert rows[0]["cf_path"] == "/content/dam/x/faq/health-insurance/family/q-one"
    assert rows[0]["faq_category"] == "iffcotokio:health-insurance"
    assert rows[0]["collision"] == "no"
    assert rows[0]["answer_chars"] == "10"


def test_collisions_column_counts_colliding_fragments():
    entry = _entry()
    entry.faq_cfs[0]["collision"] = "yes"
    rows = _read(build_report_zip(BatchResult(filename_top="X", entries=[entry])),
                 "pages.csv")
    assert rows[0]["collisions"] == "1"


def test_report_zip_contains_exactly_both_csvs():
    data = build_report_zip(BatchResult(filename_top="X", entries=[_entry()]))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["faqs.csv", "pages.csv"]


def test_formula_prefixed_cells_are_neutralised_in_both_csvs():
    """=, +, -, @ leading a string cell must come back apostrophe-prefixed
    in every column -- not just question/answer -- so Excel renders it as
    text instead of evaluating it as a formula. Numeric columns must be
    unaffected."""
    entry = _entry(
        error="=cmd|'/c calc'!A1",
        faq_cfs=[{"path": "+SUM(1,1)", "name": "q", "collision": "-yes",
                  "children": [{"name": "jcr:content",
                                "properties": {"jcr:title": "@HYPERLINK(x)"},
                                "children": [{"name": "data", "children": [
                                    {"name": "master", "properties": {
                                        "answer": "=HYPERLINK(\"http://evil\",\"click\")",
                                        "faqCategory": ["=1+1"],
                                    }}]}]}]}],
    )
    result = BatchResult(filename_top="X", entries=[entry])
    data = build_report_zip(result)

    pages = _read(data, "pages.csv")
    assert pages[0]["error"] == "'=cmd|'/c calc'!A1"
    assert pages[0]["faq_count"] == "1"       # numeric column untouched

    faqs = _read(data, "faqs.csv")
    assert faqs[0]["cf_path"] == "'+SUM(1,1)"
    assert faqs[0]["collision"] == "'-yes"
    assert faqs[0]["question"] == "'@HYPERLINK(x)"
    assert faqs[0]["faq_category"] == "'=1+1"
    assert faqs[0]["answer_chars"] == str(len('=HYPERLINK("http://evil","click")'))


def test_ordinary_values_pass_through_byte_identical():
    """The guard must not touch content with no dangerous leading char."""
    rows = _read(build_report_zip(BatchResult(filename_top="X", entries=[_entry()])),
                 "faqs.csv")
    assert rows[0]["question"] == "Q one?"
    assert rows[0]["cf_path"] == "/content/dam/x/faq/health-insurance/family/q-one"
    assert rows[0]["collision"] == "no"
