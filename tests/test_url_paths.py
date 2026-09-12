"""Path derivation from a document's own URL."""

from mapping.url_paths import (
    cf_folder_path, faq_categories, page_path, split_site_url,
)

ORIGIN = "https://www.iffcotokio.co.in"


def test_split_two_segment_hierarchy():
    hierarchy, slug = split_site_url(
        f"{ORIGIN}/health-insurance/family-health-insurance/who-should-buy", ORIGIN)
    assert hierarchy == ["health-insurance", "family-health-insurance"]
    assert slug == "who-should-buy"


def test_split_single_segment_hierarchy():
    hierarchy, slug = split_site_url(
        f"{ORIGIN}/health-insurance/cashless-vs-reimbursement", ORIGIN)
    assert hierarchy == ["health-insurance"]
    assert slug == "cashless-vs-reimbursement"


def test_split_tolerates_trailing_slash_and_html_suffix():
    assert split_site_url(f"{ORIGIN}/health-insurance/page.html", ORIGIN) == (
        ["health-insurance"], "page")
    assert split_site_url(f"{ORIGIN}/health-insurance/page/", ORIGIN) == (
        ["health-insurance"], "page")


def test_split_rejects_empty_and_off_origin():
    assert split_site_url("", ORIGIN) == ([], "")
    assert split_site_url("https://example.com/a/b", ORIGIN) == ([], "")


def test_split_slugifies_segments():
    hierarchy, slug = split_site_url(f"{ORIGIN}/Health Insurance/Who Should Buy", ORIGIN)
    assert hierarchy == ["health-insurance"]
    assert slug == "who-should-buy"


def test_page_path_joins_root_hierarchy_and_slug():
    assert page_path("/content/iffcotokio/en/",
                     ["health-insurance", "family-health-insurance"],
                     "who-should-buy") == (
        "/content/iffcotokio/en/health-insurance/family-health-insurance/who-should-buy")


def test_page_path_with_no_hierarchy():
    assert page_path("/content/iffcotokio/en", [], "who-should-buy") == (
        "/content/iffcotokio/en/who-should-buy")


def test_cf_folder_path_joins_root_and_hierarchy():
    assert cf_folder_path("/content/dam/x/faq/", ["health-insurance", "family"]) == (
        "/content/dam/x/faq/health-insurance/family")


def test_faq_categories_prefixes_each_segment():
    assert faq_categories("iffcotokio", ["health-insurance", "family"]) == [
        "iffcotokio:health-insurance", "iffcotokio:family"]


def test_faq_categories_empty_hierarchy():
    assert faq_categories("iffcotokio", []) == []
