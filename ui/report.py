"""Batch run -> a two-CSV audit an operator can file or diff.

pages.csv carries one row per source document -- including the ones that
failed, so nothing disappears from the audit. faqs.csv carries one row per
generated fragment. They join on page_path.

Written with utf-8-sig: the client opens these in Excel, which misreads plain
UTF-8 and mangles the non-ASCII characters these documents contain.
"""

import csv
import io
import zipfile

_PAGE_COLUMNS = ["doc", "folder", "status", "url", "page_path",
                 "faq_count", "collisions", "error", "warnings"]
_FAQ_COLUMNS = ["page_path", "question", "cf_path", "faq_category",
                "collision", "batch_clash", "answer_chars"]


def _master_properties(cf: dict) -> dict:
    """Reach the fragment's master node without assuming a fixed index."""
    def find(node, name):
        for child in node.get("children") or []:
            if child.get("name") == name:
                return child
        return None
    jcr_content = find(cf, "jcr:content")
    data = find(jcr_content, "data") if jcr_content else None
    master = find(data, "master") if data else None
    return (master or {}).get("properties", {}) or {}


def _title_of(cf: dict) -> str:
    for child in cf.get("children") or []:
        if child.get("name") == "jcr:content":
            return child.get("properties", {}).get("jcr:title", "")
    return ""


def _page_rows(result) -> list[dict]:
    rows = []
    for entry in result.entries:
        cfs = entry.faq_cfs or []
        rows.append({
            "doc": entry.filename,
            "folder": entry.folder,
            "status": entry.status,
            "url": (entry.aem_tree or {}).get("_source_url", ""),
            "page_path": entry.page_path,
            "faq_count": len(cfs),
            "collisions": sum(1 for c in cfs if c.get("collision") == "yes"),
            "error": entry.error or "",
            # Non-fatal things the operator still has to know about -- a
            # mis-filed fragment set, a cross-document path clash. Put on
            # the tree by the mapper / batch service.
            "warnings": "; ".join((entry.aem_tree or {}).get("_warnings") or []),
        })
    return rows


def _faq_rows(result) -> list[dict]:
    rows = []
    for entry in result.entries:
        for cf in entry.faq_cfs or []:
            master = _master_properties(cf)
            # Property names are config-driven (faq.fieldNames), so read the
            # names the fragment was actually built with rather than the
            # defaults -- otherwise a renamed field reports an empty
            # category and a zero-length answer for a perfectly good fragment.
            names = cf.get("_field_names") or {}
            categories = master.get(names.get("category", "faqCategory")) or []
            answer = master.get(names.get("answer", "answer")) or ""
            rows.append({
                "page_path": entry.page_path,
                "question": _title_of(cf),
                "cf_path": cf.get("path", ""),
                "faq_category": ",".join(categories),
                "collision": cf.get("collision", "unknown"),
                # Set when another document in the same batch derived this
                # exact CF path; holds the path that was asked for before
                # this fragment was suffixed to keep both answers.
                "batch_clash": cf.get("batch_clash", ""),
                "answer_chars": len(answer),
            })
    return rows


_DANGEROUS_PREFIXES = ("=", "+", "-", "@")


def _neutralize_formula(value):
    """Prefix a leading apostrophe onto any string cell that Excel would
    otherwise evaluate as a formula (=, +, -, @). These CSVs are filed and
    handed to other people, opened in Excel -- a question/answer/error/etc.
    sourced from an author-controlled Word doc is a real trust boundary,
    not just a CSV-quoting concern. Non-strings (the int columns) pass
    through untouched so they keep rendering as plain numbers.
    """
    if isinstance(value, str) and value.startswith(_DANGEROUS_PREFIXES):
        return "'" + value
    return value


def _to_csv(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _neutralize_formula(v) for k, v in row.items()})
    return buf.getvalue()


def build_report_zip(result) -> bytes:
    pages = _to_csv(_PAGE_COLUMNS, _page_rows(result))
    faqs = _to_csv(_FAQ_COLUMNS, _faq_rows(result))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in (("pages.csv", pages), ("faqs.csv", faqs)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text.encode("utf-8-sig"))
    return buf.getvalue()
