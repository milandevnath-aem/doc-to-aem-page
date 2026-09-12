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
                 "faq_count", "collisions", "error"]
_FAQ_COLUMNS = ["page_path", "question", "cf_path", "faq_category",
                "collision", "answer_chars"]


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
        })
    return rows


def _faq_rows(result) -> list[dict]:
    rows = []
    for entry in result.entries:
        for cf in entry.faq_cfs or []:
            master = _master_properties(cf)
            categories = master.get("faqCategory") or []
            answer = master.get("answer") or ""
            rows.append({
                "page_path": entry.page_path,
                "question": _title_of(cf),
                "cf_path": cf.get("path", ""),
                "faq_category": ",".join(categories),
                "collision": cf.get("collision", "unknown"),
                "answer_chars": len(answer),
            })
    return rows


def _to_csv(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
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
