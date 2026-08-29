"""NCM + PageTypeConfig -> abstract AEM node tree.

The tree is a plain-dict structure (JSON-serializable, no AEM SDK):

    {
        "name": "<jcr node name>",
        "primary_type": "cq:Page",
        "properties": {...},
        "children": [ {...}, {...} ],   # ordered
    }

Whichever backend eventually installs the pages (FileVault package, custom
servlet, direct Sling POST) consumes the same tree.
"""

from aem_client.config import PageTypeConfig
from mapping.mapper import map_ncm_to_node_tree
from mapping.slug import slugify


# ---------- slug ----------

def test_slug_lowercases_and_hyphenates():
    assert slugify("Cashless Treatment vs Reimbursement Claims") == \
        "cashless-treatment-vs-reimbursement-claims"


def test_slug_collapses_repeated_separators_and_strips_edges():
    assert slugify("  Hello --  World  ") == "hello-world"


def test_slug_drops_jcr_illegal_chars():
    # colon is the JCR namespace separator; slash is the path separator
    assert slugify("Health & Family: 2026/2027") == "health-family-2026-2027"


def test_slug_empty_input_returns_placeholder():
    assert slugify("") == "page"
    assert slugify("   !!! ---   ") == "page"


# ---------- shared fixtures ----------

def _article_config() -> PageTypeConfig:
    return PageTypeConfig(
        type="article",
        template_path="/conf/iffcotokio/settings/wcm/templates/article",
        content_root="/content/iffcotokio/en/articles",
        dam_root="/content/dam/iffcotokio/articles",
        page_name_strategy="slug_from_meta_title",
        component_mappings={
            "page-description": "iffcotokio/components/text",
            "faq": "iffcotokio/components/faq",
            "hero-banner": "iffcotokio/components/herobanner",
            "featurecards": "iffcotokio/components/featurecards",
        },
    )


def _ncm(page_props: dict | None = None, blocks: list | None = None,
         format_name: str = "v3") -> dict:
    return {
        "version": "0.1",
        "format": format_name,
        "source": {"type": "word", "path": "sample.docx"},
        "page": {"properties": page_props or {"title": "The Title"}},
        "blocks": blocks or [],
    }


# ---------- page node + jcr:content ----------

def test_map_produces_page_and_jcr_content_nodes():
    tree = map_ncm_to_node_tree(_ncm(), _article_config())

    assert tree["primary_type"] == "cq:Page"
    assert tree["name"] == "the-title"
    assert tree["properties"] == {}

    jcr = tree["children"][0]
    assert jcr["name"] == "jcr:content"
    assert jcr["primary_type"] == "cq:PageContent"
    assert jcr["properties"]["cq:template"] == \
        "/conf/iffcotokio/settings/wcm/templates/article"
    assert jcr["properties"]["jcr:title"] == "The Title"


def test_map_places_page_under_configured_content_root():
    tree = map_ncm_to_node_tree(_ncm(), _article_config())
    assert tree["path"] == "/content/iffcotokio/en/articles/the-title"


def test_map_meta_fields_become_page_properties():
    ncm = _ncm(page_props={
        "title": "Sample Title",
        "h1": "Sample H1",
        "description": "SEO desc.",
        "keywords": "kw1, kw2",
        "url": "/health/sample",
        "old_url": "https://old.example.com/x",
    })
    tree = map_ncm_to_node_tree(ncm, _article_config())
    jcr = tree["children"][0]["properties"]
    assert jcr["jcr:title"] == "Sample Title"
    assert jcr["jcr:description"] == "SEO desc."
    assert jcr["metaKeywords"] == "kw1, kw2"
    assert jcr["h1"] == "Sample H1"
    assert jcr["sling:vanityPath"] == "/health/sample"
    assert jcr["redirectFrom"] == "https://old.example.com/x"


def test_map_empty_meta_fields_are_omitted():
    """Missing / empty page properties must not create empty JCR properties
    -- keeps the tree clean and avoids overwriting template defaults with
    empty strings on subsequent Sling POSTs."""
    ncm = _ncm(page_props={"title": "T", "description": "", "old_url": ""})
    jcr = map_ncm_to_node_tree(ncm, _article_config())["children"][0]["properties"]
    assert "jcr:description" not in jcr
    assert "redirectFrom" not in jcr


# ---------- root container + components ----------

def test_map_component_becomes_child_under_root_container():
    ncm = _ncm(blocks=[{
        "type": "component", "name": "page-description", "variant": None,
        "fields": {"htmltext": "<p>Hello</p>"},
        "items": [], "table": None,
    }])
    tree = map_ncm_to_node_tree(ncm, _article_config())
    jcr_content = tree["children"][0]
    root = jcr_content["children"][0]
    assert root["name"] == "root"
    assert root["primary_type"] == "nt:unstructured"
    assert root["properties"]["sling:resourceType"].endswith("/container")

    text = root["children"][0]
    assert text["name"] == "page-description_0"
    assert text["properties"]["sling:resourceType"] == "iffcotokio/components/text"
    # htmltext -> text + textIsRich, canonical AEM RTE contract
    assert text["properties"]["text"] == "<p>Hello</p>"
    assert text["properties"]["textIsRich"] == "true"


def test_map_multiple_components_preserve_order_with_indexed_names():
    ncm = _ncm(blocks=[
        {"type": "component", "name": "page-description", "variant": None,
         "fields": {"htmltext": "<p>a</p>"}, "items": [], "table": None},
        {"type": "component", "name": "faq", "variant": None,
         "fields": {"title": "FAQS"}, "items": [], "table": None},
        {"type": "component", "name": "page-description", "variant": None,
         "fields": {"htmltext": "<p>b</p>"}, "items": [], "table": None},
    ])
    root = map_ncm_to_node_tree(ncm, _article_config())["children"][0]["children"][0]
    names = [c["name"] for c in root["children"]]
    assert names == ["page-description_0", "faq_1", "page-description_2"]


def test_map_component_with_unmapped_name_falls_back_to_source_name():
    """Component in NCM but not in PageTypeConfig.component_mappings -- keep
    the block in the tree with the source name as resourceType so an operator
    can spot the miss instead of the block silently vanishing."""
    ncm = _ncm(blocks=[{
        "type": "component", "name": "unknown-thing", "variant": None,
        "fields": {"htmltext": "<p>x</p>"}, "items": [], "table": None,
    }])
    child = map_ncm_to_node_tree(ncm, _article_config())["children"][0]["children"][0]["children"][0]
    assert child["name"] == "unknown-thing_0"
    assert child["properties"]["sling:resourceType"] == "unknown-thing"
    assert child["properties"]["__mapping_gap"] == "unknown-thing"


# ---------- FAQ items ----------

def test_map_faq_items_become_child_nodes():
    ncm = _ncm(blocks=[{
        "type": "component", "name": "faq", "variant": None,
        "fields": {"title": "FAQS"},
        "items": [
            {"id": None, "implicit": True,
             "fields": {"question": "What?", "htmltext": "<p>A one.</p>"}},
            {"id": None, "implicit": True,
             "fields": {"question": "Why?", "htmltext": "<p>A two.</p>"}},
        ],
        "table": None,
    }])
    faq = map_ncm_to_node_tree(ncm, _article_config())["children"][0]["children"][0]["children"][0]
    assert faq["properties"]["title"] == "FAQS"
    items = faq["children"]
    assert [c["name"] for c in items] == ["item_0", "item_1"]
    assert items[0]["properties"]["question"] == "What?"
    assert items[0]["properties"]["text"] == "<p>A one.</p>"
    assert items[0]["properties"]["textIsRich"] == "true"


# ---------- featurecards items with images ----------

def test_map_featurecards_items_with_reference_image_get_dam_path():
    ncm = _ncm(blocks=[{
        "type": "component", "name": "featurecards", "variant": None,
        "fields": {"title": "Benefits"},
        "items": [
            {"id": None, "implicit": True,
             "fields": {
                 "title": "Financial Protection",
                 "htmltext": "<p>helps</p>",
                 "image": {"source": "reference", "name": "location1.png", "path": None},
             }},
        ],
        "table": None,
    }])
    cards = map_ncm_to_node_tree(ncm, _article_config())["children"][0]["children"][0]["children"][0]
    item = cards["children"][0]
    assert item["properties"]["title"] == "Financial Protection"
    assert item["properties"]["text"] == "<p>helps</p>"
    # fileReference = dam_root + / + image name (AEM DAM asset path)
    assert item["properties"]["fileReference"] == \
        "/content/dam/iffcotokio/articles/location1.png"


def test_map_featurecards_item_embedded_image_uses_same_dam_convention():
    ncm = _ncm(blocks=[{
        "type": "component", "name": "featurecards", "variant": None,
        "fields": {"title": "Benefits"},
        "items": [
            {"id": None, "implicit": True,
             "fields": {
                 "title": "Card",
                 "htmltext": "<p>x</p>",
                 "image": {"source": "embedded", "name": "image-1.png",
                           "path": "ui/static/assets/run-x/image-1.png"},
             }},
        ],
        "table": None,
    }])
    item = map_ncm_to_node_tree(ncm, _article_config())["children"][0]["children"][0]["children"][0]["children"][0]
    # regardless of source, the AEM property is the DAM path we WILL install to
    assert item["properties"]["fileReference"] == \
        "/content/dam/iffcotokio/articles/image-1.png"


# ---------- page-name strategy ----------

def test_page_name_strategy_slug_from_url_uses_last_segment():
    cfg = _article_config()
    cfg.page_name_strategy = "slug_from_url"
    ncm = _ncm(page_props={
        "title": "Some Title",
        "url": "/health-insurance/cashless-vs-reimbursement",
    })
    tree = map_ncm_to_node_tree(ncm, cfg)
    assert tree["name"] == "cashless-vs-reimbursement"


def test_page_name_strategy_docx_filename_uses_source_stem():
    cfg = _article_config()
    cfg.page_name_strategy = "docx_filename"
    ncm = _ncm(page_props={"title": "Some Title"})
    ncm["source"]["path"] = "some/dir/Cashless Vs Reimbursement.docx"
    tree = map_ncm_to_node_tree(ncm, cfg)
    assert tree["name"] == "cashless-vs-reimbursement"


# ---------- real-doc smoke ----------

def test_map_full_v3_shape_end_to_end():
    """One NCM shaped like the real v3 client doc all the way through --
    smoke check that the tree is deep, ordered, and doesn't lose blocks."""
    ncm = _ncm(page_props={
        "h1": "Cashless vs Reimbursement",
        "title": "Cashless Meta Title",
        "description": "SEO desc",
        "keywords": "kw",
        "old_url": "",
        "url": "/health/cashless",
    }, blocks=[
        {"type": "component", "name": "page-description", "variant": None,
         "fields": {"htmltext": "<p>intro</p><h3>Section</h3><ul><li>b</li></ul>"},
         "items": [], "table": None},
        {"type": "component", "name": "faq", "variant": None,
         "fields": {"title": "FAQS"},
         "items": [
             {"id": None, "implicit": True,
              "fields": {"question": "Q1?", "htmltext": "<p>A1</p>"}},
         ], "table": None},
        {"type": "component", "name": "page-description", "variant": None,
         "fields": {"htmltext": "<h2>Final Thought</h2><p>...</p>"},
         "items": [], "table": None},
    ])
    tree = map_ncm_to_node_tree(ncm, _article_config())
    jcr = tree["children"][0]["properties"]
    assert jcr["jcr:title"] == "Cashless Meta Title"
    assert "redirectFrom" not in jcr  # empty old_url dropped
    root_children = tree["children"][0]["children"][0]["children"]
    assert [c["name"] for c in root_children] == [
        "page-description_0", "faq_1", "page-description_2"]
    faq = root_children[1]
    assert faq["children"][0]["name"] == "item_0"
    assert faq["children"][0]["properties"]["question"] == "Q1?"
