"""One test that spans the whole pipeline on real input.

Every other suite stops at its own boundary on a handcrafted fixture, which
is how a sample config that placed pages flat, a report that read hardcoded
field names, and a blank `url` column all stayed invisible. This drives the
seven real client documents through parser -> mapper -> batch -> package ->
report with the BUNDLED sample config, and asserts on the package and the
report together.

Skips when the client documents are not checked out next to the repo.
"""

import csv
import io
import zipfile
from pathlib import Path

import pytest

from aem.package_generator.package import PackageMeta, build_batch_package
from aem_client.config import load_page_type_from_sample
from ui.batch_service import run_batch
from ui.report import build_report_zip

DOCS_DIR = Path(__file__).resolve().parents[1].parent / "content-structure-test" / "Articles"
CONTENT_ROOT = "/content/iffcotokio/en"
CF_ROOT = "/content/dam/iffcotokio/content-fragments/faq"


@pytest.fixture(scope="module")
def real_batch():
    if not DOCS_DIR.is_dir():
        pytest.skip(f"client documents not present at {DOCS_DIR}")
    files = [(str(f.relative_to(DOCS_DIR.parent)).replace("\\", "/"), f.read_bytes())
             for f in sorted(DOCS_DIR.rglob("*")) if f.is_file()]
    return run_batch(files, page_type_config=load_page_type_from_sample("article"))


def test_every_client_doc_passes_and_nests_under_its_url_hierarchy(real_batch):
    assert len(real_batch.entries) == 7
    assert [e.status for e in real_batch.entries] == ["passed"] * 7

    paths = [e.page_path for e in real_batch.entries]
    # every page lands under the section hierarchy stated by its own URL,
    # never flat as a sibling of the real section folders
    assert all(p.startswith(f"{CONTENT_ROOT}/health-insurance/") for p in paths)
    assert all(p.count("/") > CONTENT_ROOT.count("/") + 1 for p in paths)
    assert (f"{CONTENT_ROOT}/health-insurance/cashless-treatment-vs-reimbursement-claims"
            in paths)
    assert (f"{CONTENT_ROOT}/health-insurance/family-health-insurance/"
            "who-should-buy-family-health-insurance" in paths)


def test_thirty_fragments_land_under_the_matching_dam_hierarchy(real_batch):
    cfs = [cf for e in real_batch.entries for cf in e.faq_cfs]
    assert len(cfs) == 30
    assert len({cf["path"] for cf in cfs}) == 30

    for entry in real_batch.entries:
        # page and fragment placement agree: same hierarchy, different roots
        hierarchy = entry.page_path[len(CONTENT_ROOT):].rsplit("/", 1)[0]
        for cf in entry.faq_cfs:
            assert cf["path"].startswith(f"{CF_ROOT}{hierarchy}/")


def test_package_carries_every_page_and_every_fragment(real_batch):
    trees = [{**e.aem_tree, "_faq_cfs": e.faq_cfs} for e in real_batch.entries]
    data = build_batch_package(trees, PackageMeta(name="articles-batch"))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        filter_xml = zf.read("META-INF/vault/filter.xml").decode()

    for entry in real_batch.entries:
        assert f"jcr_root{entry.page_path}/.content.xml" in names
        for cf in entry.faq_cfs:
            assert f"jcr_root{cf['path']}/.content.xml" in names
    roots = [line for line in filter_xml.splitlines() if "<filter root=" in line]
    assert len(roots) == len(set(roots)) == 7 + 30


def test_report_matches_the_package(real_batch):
    with zipfile.ZipFile(io.BytesIO(build_report_zip(real_batch))) as zf:
        pages = list(csv.DictReader(io.StringIO(
            zf.read("pages.csv").decode("utf-8-sig"))))
        faqs = list(csv.DictReader(io.StringIO(
            zf.read("faqs.csv").decode("utf-8-sig"))))

    assert len(pages) == 7
    assert sum(int(r["faq_count"]) for r in pages) == 30
    # every doc reports the URL it was placed from, and nothing is mis-filed
    assert all(r["url"].startswith("https://www.iffcotokio.co.in/") for r in pages)
    assert all(r["warnings"] == "" for r in pages)

    assert len(faqs) == 30
    assert all(r["faq_category"].startswith("iffcotokio:health-insurance")
               for r in faqs)
    assert all(int(r["answer_chars"]) > 0 for r in faqs)
    assert all(r["batch_clash"] == "" for r in faqs)
    # the two CSVs join on page_path
    assert {r["page_path"] for r in faqs} <= {r["page_path"] for r in pages}
