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

from aem_client.config import FaqConfig, PageTypeConfig
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


ORIGIN = "https://www.iffcotokio.co.in"


def _url_config(**over) -> PageTypeConfig:
    """An article config that derives page paths from the doc's own URL."""
    cfg = _article_config()
    cfg.page_name_strategy = "slug_from_url"
    cfg.content_root = "/content/iffcotokio/en"
    cfg.site_origin = ORIGIN
    for key, value in over.items():
        setattr(cfg, key, value)
    return cfg


def _config_with_faq(**faq_over) -> PageTypeConfig:
    cfg = _url_config()
    faq_kwargs = dict(
        cf_root="/content/dam/iffcotokio/content-fragments/faq",
        cf_model="/conf/iffcotokio/settings/dam/cfm/models/faq-template",
        tag_namespace="iffcotokio",
        page_component={"resourceType": "iffcotokio/components/faq",
                        "pathsProperty": "fragmentPaths"},
    )
    faq_kwargs.update(faq_over)
    cfg.faq = FaqConfig(**faq_kwargs)
    return cfg


def _ncm_with_faq(url: str) -> dict:
    return _ncm(
        page_props={"title": "T", "url": url},
        blocks=[
            {"name": "page-description", "variant": None,
             "fields": {"htmltext": "<p>Article body.</p>"}, "items": [], "table": None},
            {"name": "faq", "variant": None, "fields": {"title": "FAQs"}, "table": None,
             "items": [
                 {"id": None, "implicit": True,
                  "fields": {"question": "Can parents be included?",
                             "htmltext": "<p>Yes, parents can be included.</p>"}},
                 {"id": None, "implicit": True,
                  "fields": {"question": "Is it mandatory?",
                             "htmltext": "<p>No.</p>"}},
             ]},
        ],
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
    cfg.site_origin = ORIGIN
    ncm = _ncm(page_props={
        "title": "Some Title",
        "url": f"{ORIGIN}/health-insurance/cashless-vs-reimbursement",
    })
    tree = map_ncm_to_node_tree(ncm, cfg)
    assert tree["name"] == "cashless-vs-reimbursement"


def test_page_path_includes_url_hierarchy():
    ncm = _ncm(page_props={"title": "T", "url": f"{ORIGIN}/health-insurance/"
                                                f"family-health-insurance/who-should-buy"})
    tree = map_ncm_to_node_tree(ncm, _url_config())
    assert tree["path"] == ("/content/iffcotokio/en/health-insurance/"
                            "family-health-insurance/who-should-buy")
    assert tree["name"] == "who-should-buy"


def test_page_path_single_segment_hierarchy():
    ncm = _ncm(page_props={"title": "T",
                           "url": f"{ORIGIN}/health-insurance/cashless-vs-reimbursement"})
    tree = map_ncm_to_node_tree(ncm, _url_config())
    assert tree["path"] == ("/content/iffcotokio/en/health-insurance/"
                            "cashless-vs-reimbursement")


def test_page_name_unresolvable_without_url_under_slug_from_url():
    """No URL means no derivable location. The page must land on the
    UNRESOLVED_SLUG sentinel so batch mode skips it instead of inventing a
    path under the content root."""
    from mapping.slug import UNRESOLVED_SLUG
    ncm = _ncm(page_props={"title": "T", "url": ""})
    tree = map_ncm_to_node_tree(ncm, _url_config())
    assert tree["name"] == UNRESOLVED_SLUG


def test_page_name_strategy_docx_filename_uses_source_stem():
    cfg = _article_config()
    cfg.page_name_strategy = "docx_filename"
    ncm = _ncm(page_props={"title": "Some Title"})
    ncm["source"]["path"] = "some/dir/Cashless Vs Reimbursement.docx"
    tree = map_ncm_to_node_tree(ncm, cfg)
    assert tree["name"] == "cashless-vs-reimbursement"


# ---------- real-doc smoke ----------

def test_map_field_rename_replaces_property_name_on_component():
    """Existing AEM component uses different property names than our NCM.
    fieldRenames on that component's mapping entry translates our field
    names to what the component's dialog stores."""
    cfg = _article_config()
    cfg.component_mappings["page-description"] = {
        "resourceType": "iffcotokio/components/text",
        "fieldRenames": {"htmltext": "richTextContent"},
    }
    ncm = _ncm(blocks=[{
        "type": "component", "name": "page-description", "variant": None,
        "fields": {"htmltext": "<p>x</p>"}, "items": [], "table": None,
    }])
    text = map_ncm_to_node_tree(ncm, cfg)["children"][0]["children"][0]["children"][0]
    props = text["properties"]
    assert props["richTextContent"] == "<p>x</p>"
    assert "text" not in props
    # Overriding htmltext disables the default textIsRich emission -- the
    # renamed target may have its own richtext contract or not be RTE at all
    assert "textIsRich" not in props


def test_map_item_field_renames_apply_to_faq_and_featurecard_items():
    cfg = _article_config()
    cfg.component_mappings["faq"] = {
        "resourceType": "iffcotokio/components/faq",
        "fieldRenames": {"title": "faqTitle"},
        "itemFieldRenames": {"question": "faqQuestion", "htmltext": "faqAnswer"},
    }
    ncm = _ncm(blocks=[{
        "type": "component", "name": "faq", "variant": None,
        "fields": {"title": "FAQS"},
        "items": [
            {"id": None, "implicit": True,
             "fields": {"question": "Q?", "htmltext": "<p>A.</p>"}},
        ], "table": None,
    }])
    faq = map_ncm_to_node_tree(ncm, cfg)["children"][0]["children"][0]["children"][0]
    # component-level rename
    assert faq["properties"]["faqTitle"] == "FAQS"
    assert "title" not in faq["properties"]
    # item-level rename
    item = faq["children"][0]
    assert item["properties"]["faqQuestion"] == "Q?"
    assert item["properties"]["faqAnswer"] == "<p>A.</p>"
    assert "question" not in item["properties"]
    assert "text" not in item["properties"]


def test_map_emits_static_properties_on_component():
    """Some AEM components need fixed properties every install regardless
    of source content (enableContainer=true on a text component, cardAlign
    default on a hero-banner, etc.). staticProperties on the mapping
    entry declares them."""
    cfg = _article_config()
    cfg.component_mappings["page-description"] = {
        "resourceType": "iffcotokio/components/content/page-description",
        "fieldRenames": {"htmltext": "description"},
        "staticProperties": {
            "textIsRich": "true",
            "enableContainer": "true",
        },
    }
    ncm = _ncm(blocks=[{
        "type": "component", "name": "page-description", "variant": None,
        "fields": {"htmltext": "<p>x</p>"}, "items": [], "table": None,
    }])
    node = map_ncm_to_node_tree(ncm, cfg)["children"][0]["children"][0]["children"][0]
    props = node["properties"]
    assert props["description"] == "<p>x</p>"
    assert props["textIsRich"] == "true"
    assert props["enableContainer"] == "true"


def test_map_static_properties_do_not_overwrite_ncm_supplied_values():
    """A source doc that carries a real value for a property whose name
    matches a static one wins -- static is only a DEFAULT."""
    cfg = _article_config()
    cfg.component_mappings["hero-banner"] = {
        "resourceType": "iffcotokio/components/content/hero-banner-compnent/new-hero-banner",
        "fieldRenames": {
            "title": "bannerTitle",
            "htmltext": "bannerDescription",
        },
        "staticProperties": {
            "cardAlign": "left",
            "imagePath": "/content/dam/iffcotokio/banner/default.png",
        },
    }
    ncm = _ncm(blocks=[{
        "type": "component", "name": "hero-banner", "variant": None,
        "fields": {"title": "Hi", "htmltext": "<p>x</p>",
                   "cardAlign": "right"},   # source overrides static
        "items": [], "table": None,
    }])
    node = map_ncm_to_node_tree(ncm, cfg)["children"][0]["children"][0]["children"][0]
    props = node["properties"]
    assert props["bannerTitle"] == "Hi"
    assert props["bannerDescription"] == "<p>x</p>"
    # NCM value overrides the static default
    assert props["cardAlign"] == "right"
    # static default kept when no NCM value collides
    assert props["imagePath"] == "/content/dam/iffcotokio/banner/default.png"


def test_map_static_properties_apply_per_item_via_itemStaticProperties():
    cfg = _article_config()
    cfg.component_mappings["faq"] = {
        "resourceType": "iffcotokio/components/faq",
        "itemFieldRenames": {"question": "faqQuestion", "htmltext": "faqAnswer"},
        "itemStaticProperties": {"answerIsRich": "true"},
    }
    ncm = _ncm(blocks=[{
        "type": "component", "name": "faq", "variant": None,
        "fields": {"title": "FAQS"},
        "items": [
            {"id": None, "implicit": True,
             "fields": {"question": "Q?", "htmltext": "<p>A</p>"}},
        ], "table": None,
    }])
    item = map_ncm_to_node_tree(ncm, cfg)["children"][0]["children"][0]["children"][0]["children"][0]
    assert item["properties"]["faqQuestion"] == "Q?"
    assert item["properties"]["faqAnswer"] == "<p>A</p>"
    assert item["properties"]["answerIsRich"] == "true"


def test_map_uses_configured_page_and_container_resource_types():
    cfg = _article_config()
    cfg.page_resource_type = "iffcotokio/components/page"
    cfg.container_resource_type = "iffcotokio/components/parsys"
    tree = map_ncm_to_node_tree(_ncm(), cfg)
    jcr = tree["children"][0]
    assert jcr["properties"]["sling:resourceType"] == "iffcotokio/components/page"
    root = jcr["children"][0]
    assert root["properties"]["sling:resourceType"] == "iffcotokio/components/parsys"


def test_map_strips_whitespace_from_component_resource_type():
    """Operator-entered configs can accidentally carry leading/trailing
    whitespace. Strip it -- AEM will not resolve a resource type that
    starts with 14 spaces."""
    cfg = _article_config()
    cfg.component_mappings["page-description"] = "   iffcotokio/components/text   "
    ncm = _ncm(blocks=[{
        "type": "component", "name": "page-description", "variant": None,
        "fields": {"htmltext": "<p>x</p>"}, "items": [], "table": None,
    }])
    text = map_ncm_to_node_tree(ncm, cfg)["children"][0]["children"][0]["children"][0]
    assert text["properties"]["sling:resourceType"] == "iffcotokio/components/text"


def test_map_strips_whitespace_from_nested_component_resource_type():
    cfg = _article_config()
    cfg.component_mappings["page-description"] = {
        "resourceType": "  iffcotokio/components/text  ",
        "fieldRenames": {},
    }
    ncm = _ncm(blocks=[{
        "type": "component", "name": "page-description", "variant": None,
        "fields": {"htmltext": "<p>x</p>"}, "items": [], "table": None,
    }])
    text = map_ncm_to_node_tree(ncm, cfg)["children"][0]["children"][0]["children"][0]
    assert text["properties"]["sling:resourceType"] == "iffcotokio/components/text"


def test_map_string_form_component_mapping_still_defaults_to_text_plus_isrich():
    """Simple string form -- when htmltext is NOT overridden, it maps to
    text + textIsRich=true (unchanged behavior)."""
    ncm = _ncm(blocks=[{
        "type": "component", "name": "page-description", "variant": None,
        "fields": {"htmltext": "<p>x</p>"}, "items": [], "table": None,
    }])
    text = map_ncm_to_node_tree(ncm, _article_config())["children"][0]["children"][0]["children"][0]
    assert text["properties"]["text"] == "<p>x</p>"
    assert text["properties"]["textIsRich"] == "true"


def _tpl_initial():
    """Minimal template initial tree matching the client's article template."""
    return {
        "name": "jcr:content",
        "primary_type": "cq:PageContent",
        "properties": {"sling:resourceType": "iffcotokio/components/structure/page"},
        "children": [{
            "name": "root",
            "primary_type": "nt:unstructured",
            "properties": {"sling:resourceType": "wcm/foundation/components/responsivegrid"},
            "children": [
                {"name": "responsivegrid_1616964146", "primary_type": "nt:unstructured",
                 "properties": {"sling:resourceType": "wcm/foundation/components/responsivegrid"},
                 "children": []},
                {"name": "responsivegrid", "primary_type": "nt:unstructured",
                 "properties": {"sling:resourceType": "wcm/foundation/components/responsivegrid"},
                 "children": [
                     {"name": "new_hero_banner", "primary_type": "nt:unstructured",
                      "properties": {
                          "sling:resourceType": "site/components/hero",
                          "bannerTitle": "Template Default Title",
                      }, "children": []},
                     {"name": "right_par", "primary_type": "nt:unstructured",
                      "properties": {"sling:resourceType": "wcm/foundation/components/responsivegrid"},
                      "children": []},
                 ]},
            ],
        }],
    }


def test_template_overlay_uses_template_tree_as_base(tmp_path):
    """When template_initial_content is set, the tree matches the template
    layout instead of the flat root/<component>_<index> layout."""
    cfg = _article_config()
    cfg.template_initial_content = _tpl_initial()
    cfg.component_slots = {
        "page-description": "root/responsivegrid/right_par/page_description",
    }
    ncm = _ncm(blocks=[
        {"type": "component", "name": "page-description", "variant": None,
         "fields": {"htmltext": "<p>x</p>"}, "items": [], "table": None},
    ])
    tree = map_ncm_to_node_tree(ncm, cfg)
    jcr = tree["children"][0]
    root = next(c for c in jcr["children"] if c["name"] == "root")
    # template's static nodes preserved
    assert any(c["name"] == "responsivegrid_1616964146" for c in root["children"])
    grid = next(c for c in root["children"] if c["name"] == "responsivegrid")
    # hero banner still at its template-supplied default (no NCM hero-banner block)
    hero = next(c for c in grid["children"] if c["name"] == "new_hero_banner")
    assert hero["properties"]["bannerTitle"] == "Template Default Title"
    # our page_description was placed at the slot path
    right_par = next(c for c in grid["children"] if c["name"] == "right_par")
    page_desc = next(c for c in right_par["children"] if c["name"] == "page_description")
    assert page_desc["properties"]["sling:resourceType"] == "iffcotokio/components/text"
    assert page_desc["properties"]["text"] == "<p>x</p>"


def test_template_overlay_extra_blocks_go_as_siblings_of_the_slot():
    """Multiple NCM blocks of the same source name -- first fills the slot
    node, extras become siblings under the same parent with _1, _2, ..."""
    cfg = _article_config()
    cfg.template_initial_content = _tpl_initial()
    cfg.component_slots = {
        "page-description": "root/responsivegrid/right_par/page_description",
    }
    ncm = _ncm(blocks=[
        {"type": "component", "name": "page-description", "variant": None,
         "fields": {"htmltext": "<p>one</p>"}, "items": [], "table": None},
        {"type": "component", "name": "page-description", "variant": None,
         "fields": {"htmltext": "<p>two</p>"}, "items": [], "table": None},
        {"type": "component", "name": "page-description", "variant": None,
         "fields": {"htmltext": "<p>three</p>"}, "items": [], "table": None},
    ])
    tree = map_ncm_to_node_tree(ncm, cfg)
    right_par = tree["children"][0]["children"][0]["children"][1]["children"][1]
    assert right_par["name"] == "right_par"
    names = [c["name"] for c in right_par["children"]]
    assert names == ["page_description", "page_description_1", "page_description_2"]
    texts = [c["properties"]["text"] for c in right_par["children"]]
    assert texts == ["<p>one</p>", "<p>two</p>", "<p>three</p>"]


def test_template_overlay_unmatched_blocks_stay_visible_under_slot_parent():
    """A block without a componentSlot mapping is placed at the same
    fallback location (the slot's parent) with a __mapping_gap marker so
    content never silently vanishes."""
    cfg = _article_config()
    cfg.template_initial_content = _tpl_initial()
    cfg.component_slots = {
        "page-description": "root/responsivegrid/right_par/page_description",
    }
    ncm = _ncm(blocks=[
        {"type": "component", "name": "faq", "variant": None,
         "fields": {"title": "FAQS"}, "items": [], "table": None},
    ])
    tree = map_ncm_to_node_tree(ncm, cfg)
    right_par = tree["children"][0]["children"][0]["children"][1]["children"][1]
    faq_nodes = [c for c in right_par["children"] if c["name"].startswith("faq")]
    assert len(faq_nodes) == 1
    # __mapping_gap flags the miss so operators see it in the emitted node
    assert faq_nodes[0]["properties"].get("__mapping_gap") == "faq"


def test_template_overlay_missing_falls_back_to_flat_layout():
    """No template_initial_content -> today's flat behavior. Backward-compat."""
    cfg = _article_config()
    cfg.template_initial_content = {}   # no template
    ncm = _ncm(blocks=[
        {"type": "component", "name": "page-description", "variant": None,
         "fields": {"htmltext": "<p>x</p>"}, "items": [], "table": None},
    ])
    tree = map_ncm_to_node_tree(ncm, cfg)
    root = tree["children"][0]["children"][0]
    assert root["name"] == "root"
    # flat mode: direct child of root, named <source>_<index>
    assert root["children"][0]["name"] == "page-description_0"


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


# ---- Phase 2B: banner-image asset resolution ----

def _article_cfg_with_hero_banner_slot() -> PageTypeConfig:
    """Article config aligned with the real client shape: hero-banner
    slot points at the template's `new_hero_banner` node, mapping declares
    the field renames, static image path is the placeholder default the
    template already carries."""
    cfg = _article_config()
    cfg.template_initial_content = _tpl_initial()
    cfg.component_slots = {
        "new-hero-banner": "root/responsivegrid/new_hero_banner",
    }
    cfg.component_mappings["new-hero-banner"] = {
        "resourceType": "iffcotokio/components/content/hero-banner-compnent/new-hero-banner",
        "fieldRenames": {"htmltext": "bannerDescription"},
        "staticProperties": {
            "cardAlign": "left",
            "imagePath": "/content/dam/iffcotokio/banner/default.png",
        },
    }
    return cfg


def _hero_ncm(image_file: str | None = "car-insurance.jpg") -> dict:
    fields: dict = {"htmltext": "<p>Banner copy.</p>"}
    if image_file is not None:
        fields["imageFile"] = image_file
    return _ncm(
        page_props={"title": "Cashless Meta Title"},
        blocks=[
            {"type": "component", "name": "new-hero-banner", "variant": None,
             "fields": fields, "items": [], "table": None},
        ],
    )


def _hero_node(tree: dict) -> dict:
    root = tree["children"][0]["children"][0]
    grid = next(c for c in root["children"] if c["name"] == "responsivegrid")
    return next(c for c in grid["children"] if c["name"] == "new_hero_banner")


def test_map_wires_banner_image_asset_to_imagepath_and_records_descriptor():
    """`imageFile` marker + matching folder asset -> `imagePath` set to
    the DAM path on the mapped hero-banner node, and an asset descriptor
    is recorded at tree[_assets] so the packager knows what to bundle.
    The internal `imageFile` marker must NEVER leak into JCR."""
    cfg = _article_cfg_with_hero_banner_slot()
    tree = map_ncm_to_node_tree(
        _hero_ncm(),
        cfg,
        page_assets={"car-insurance.jpg": b"\x89PNG-fake-bytes"},
    )
    hero = _hero_node(tree)
    assert hero["properties"]["imagePath"] == \
        "/content/dam/iffcotokio/articles/car-insurance.jpg"
    assert hero["properties"]["bannerDescription"] == "<p>Banner copy.</p>"
    assert "imageFile" not in hero["properties"]

    assets = tree.get("_assets", [])
    assert len(assets) == 1
    assert assets[0]["dam_path"] == \
        "/content/dam/iffcotokio/articles/car-insurance.jpg"
    assert assets[0]["filename"] == "car-insurance.jpg"
    assert assets[0]["bytes"] == b"\x89PNG-fake-bytes"


def test_map_missing_banner_asset_keeps_template_default_imagepath():
    """When the doc references `Banner Image: missing.jpg` but the folder
    does not carry it, the mapper must NOT set imagePath -- the template
    initial + staticProperty default supplies the fallback. No asset
    descriptor recorded either (nothing to bundle)."""
    cfg = _article_cfg_with_hero_banner_slot()
    tree = map_ncm_to_node_tree(
        _hero_ncm(image_file="missing.jpg"),
        cfg,
        page_assets={"other-image.jpg": b"unrelated"},
    )
    hero = _hero_node(tree)
    # staticProperty default preserved -- template default kicks in
    assert hero["properties"]["imagePath"] == \
        "/content/dam/iffcotokio/banner/default.png"
    assert "imageFile" not in hero["properties"]
    assert tree.get("_assets", []) == []


def test_map_banner_asset_lookup_is_case_insensitive():
    """Author writes `Banner Image: Car-Insurance.JPG`, folder holds
    `car-insurance.jpg` -- still resolves. Filename in the DAM path
    reflects what's ON DISK (the packager will bundle THAT byte payload,
    so its name is what matters)."""
    cfg = _article_cfg_with_hero_banner_slot()
    tree = map_ncm_to_node_tree(
        _hero_ncm(image_file="Car-Insurance.JPG"),
        cfg,
        page_assets={"car-insurance.jpg": b"bytes"},
    )
    hero = _hero_node(tree)
    assert hero["properties"]["imagePath"].endswith("/car-insurance.jpg")


def test_map_banner_asset_uses_sub_base_bucket_when_flag_enabled():
    """`enable_sub_base_image_sorting=True` -> `<dam_root>/<slug>/<file>`."""
    cfg = _article_cfg_with_hero_banner_slot()
    cfg.enable_sub_base_image_sorting = True
    tree = map_ncm_to_node_tree(
        _hero_ncm(),
        cfg,
        page_assets={"car-insurance.jpg": b"bytes"},
    )
    # meta title is "Cashless Meta Title" -> slug "cashless-meta-title"
    hero = _hero_node(tree)
    assert hero["properties"]["imagePath"] == \
        "/content/dam/iffcotokio/articles/cashless-meta-title/car-insurance.jpg"


def test_map_no_page_assets_still_strips_imagefile_marker():
    """Backwards compat: single-doc UI calls map_ncm_to_node_tree without
    page_assets. The `imageFile` marker still must be stripped -- it's
    an internal parser signal, never a JCR property."""
    cfg = _article_cfg_with_hero_banner_slot()
    tree = map_ncm_to_node_tree(_hero_ncm(), cfg)  # no page_assets
    hero = _hero_node(tree)
    assert "imageFile" not in hero["properties"]
    # template default preserved (imagePath never overridden)
    assert hero["properties"]["imagePath"] == \
        "/content/dam/iffcotokio/banner/default.png"
    # no asset bundled
    assert tree.get("_assets", []) == []


def test_map_hero_banner_title_from_meta_title():
    """Per Q5 decision: bannerTitle sources from Meta Title, not any
    block-level title field. The mapper already reads page.properties.title
    into jcr:title; this test locks in that the hero banner picks up the
    same value via the templated `bannerTitle` when static or field-rename
    doesn't already supply one."""
    cfg = _article_cfg_with_hero_banner_slot()
    # mapping tells the hero to sink meta title into bannerTitle
    cfg.component_mappings["new-hero-banner"]["fieldRenames"]["metaTitle"] = "bannerTitle"
    ncm = _hero_ncm()
    # simulate mapper propagating meta title into the block's fields
    ncm["blocks"][0]["fields"]["metaTitle"] = ncm["page"]["properties"]["title"]
    tree = map_ncm_to_node_tree(
        ncm, cfg, page_assets={"car-insurance.jpg": b"b"},
    )
    hero = _hero_node(tree)
    assert hero["properties"]["bannerTitle"] == "Cashless Meta Title"


# ---------- FAQ -> Content Fragments + page reference (Task 6) ----------

def test_faq_block_becomes_cf_trees_and_a_page_reference():
    cfg = _config_with_faq()
    ncm = _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys")
    tree = map_ncm_to_node_tree(ncm, cfg)

    cfs = tree["_faq_cfs"]
    assert [c["name"] for c in cfs] == ["can-parents-be-included", "is-it-mandatory"]

    root = tree["children"][0]["children"][0]
    faq_nodes = [c for c in root["children"]
                 if c["properties"].get("sling:resourceType")
                 == "iffcotokio/components/faq"]
    assert len(faq_nodes) == 1
    assert faq_nodes[0]["properties"]["fragmentPaths"] == [c["path"] for c in cfs]


def test_faq_block_does_not_leave_rte_prose_on_the_page():
    tree = map_ncm_to_node_tree(
        _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys"), _config_with_faq())
    root = tree["children"][0]["children"][0]
    for node in root["children"]:
        assert "Yes, parents can be included" not in str(node["properties"])


def test_no_faq_page_component_configured_still_emits_cfs():
    """Fragments are the deliverable; the page reference is optional until the
    AEM team supplies the component."""
    tree = map_ncm_to_node_tree(
        _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys"),
        _config_with_faq(page_component={}))
    assert len(tree["_faq_cfs"]) == 2
    root = tree["children"][0]["children"][0]
    assert all("fragmentPaths" not in c["properties"] for c in root["children"])


def test_tree_carries_cf_root_and_source_url_for_downstream_consumers():
    tree = map_ncm_to_node_tree(
        _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys"), _config_with_faq())
    assert tree["_faq_cf_root"] == "/content/dam/iffcotokio/content-fragments/faq"
    assert tree["_source_url"] == f"{ORIGIN}/health-insurance/family/who-buys"


def test_no_faq_block_means_no_faq_keys_on_the_tree():
    tree = map_ncm_to_node_tree(_ncm(page_props={"title": "T", "url": ""}),
                                _config_with_faq())
    assert "_faq_cfs" not in tree
    assert "_faq_cf_root" not in tree


def test_template_overlay_faq_block_becomes_cf_trees_and_a_page_reference():
    """Both mapper modes must behave alike: template-overlay mode also pulls
    FAQ prose out of the tree and appends a reference node (to the fallback
    parent, since `faq` is not in componentSlots here), instead of emitting
    the fragment content twice."""
    cfg = _config_with_faq()
    cfg.template_initial_content = _tpl_initial()
    cfg.component_slots = {
        "page-description": "root/responsivegrid/right_par/page_description",
    }
    ncm = _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys")
    tree = map_ncm_to_node_tree(ncm, cfg)

    cfs = tree["_faq_cfs"]
    assert [c["name"] for c in cfs] == ["can-parents-be-included", "is-it-mandatory"]

    right_par = tree["children"][0]["children"][0]["children"][1]["children"][1]
    assert right_par["name"] == "right_par"
    # the faq reference lands in the fallback parent (right_par, the parent
    # of the only configured slot) -- not inlined as raw FAQ prose.
    faq_nodes = [c for c in right_par["children"]
                 if c["properties"].get("sling:resourceType")
                 == "iffcotokio/components/faq"]
    assert len(faq_nodes) == 1
    assert faq_nodes[0]["properties"]["fragmentPaths"] == [c["path"] for c in cfs]
    for node in right_par["children"]:
        assert "Yes, parents can be included" not in str(node["properties"])


def test_faq_block_with_no_question_falls_back_to_inline_rendering():
    """`build_faq_cf_trees` skips items with a blank/missing `question`, so a
    faq block whose items are ALL like that contributes no fragment. The
    inline-removal filter must key on that per-block, not just on `cfg.faq`
    being set -- otherwise the prose is pulled off the page for a fragment
    that was never generated, and vanishes with no trace."""
    cfg = _config_with_faq()
    ncm = _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys")
    ncm["blocks"][1]["items"] = [
        {"id": None, "implicit": True,
         "fields": {"question": "", "htmltext": "<p>Orphan answer.</p>"}},
    ]
    tree = map_ncm_to_node_tree(ncm, cfg)
    assert "_faq_cfs" not in tree  # nothing qualified for a fragment

    root = tree["children"][0]["children"][0]
    faq_nodes = [c for c in root["children"] if c["name"].startswith("faq")]
    assert len(faq_nodes) == 1
    # legacy inline rendering: resourceType from component_mappings, not the
    # page_component's reference shape, and no fragmentPaths were emitted
    assert faq_nodes[0]["properties"]["sling:resourceType"] == "iffcotokio/components/faq"
    assert "fragmentPaths" not in faq_nodes[0]["properties"]
    # the answer prose is still reachable on the page tree
    assert faq_nodes[0]["children"][0]["properties"]["text"] == "<p>Orphan answer.</p>"


def test_template_overlay_faq_block_with_no_question_falls_back_to_inline_rendering():
    """Same rule in template-overlay mode: a faq block that contributes no
    fragment must not be silently dropped -- it falls through to normal
    slot placement (here, no slot for `faq` -> fallback parent) instead of
    being skipped as if a reference node would replace it."""
    cfg = _config_with_faq()
    cfg.template_initial_content = _tpl_initial()
    cfg.component_slots = {
        "page-description": "root/responsivegrid/right_par/page_description",
    }
    ncm = _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys")
    ncm["blocks"][1]["items"] = [
        {"id": None, "implicit": True,
         "fields": {"question": "", "htmltext": "<p>Orphan answer.</p>"}},
    ]
    tree = map_ncm_to_node_tree(ncm, cfg)
    assert "_faq_cfs" not in tree

    right_par = tree["children"][0]["children"][0]["children"][1]["children"][1]
    faq_nodes = [c for c in right_par["children"] if c["name"].startswith("faq")]
    assert len(faq_nodes) == 1
    assert "fragmentPaths" not in faq_nodes[0]["properties"]
    assert faq_nodes[0]["children"][0]["properties"]["text"] == "<p>Orphan answer.</p>"


def test_source_url_is_set_even_when_the_doc_has_no_faq_fragments():
    """The report's `url` column exists to tell "no URL in the doc" apart from
    everything else. Gating `_source_url` on fragments existing made a doc
    with a perfectly good URL and no FAQ section report a blank url."""
    tree = map_ncm_to_node_tree(
        _ncm(page_props={"title": "T", "url": f"{ORIGIN}/health-insurance/no-faqs"}),
        _config_with_faq())

    assert "_faq_cfs" not in tree
    assert tree["_source_url"] == f"{ORIGIN}/health-insurance/no-faqs"


def test_fragments_with_no_derivable_hierarchy_carry_a_warning():
    """URL not on siteOrigin (or siteOrigin unset) -> split_site_url yields
    nothing, so the fragments land flat under cfRoot with no faqCategory
    tags. Silent mis-filing otherwise: the tree must say so."""
    cfg = _config_with_faq()
    cfg.page_name_strategy = "slug_from_meta_title"   # page still builds
    cfg.site_origin = ""

    tree = map_ncm_to_node_tree(
        _ncm_with_faq("https://www.somewhere-else.example/health-insurance/x"), cfg)

    assert len(tree["_faq_cfs"]) == 2
    # flat, untagged -- the condition the warning is about
    assert tree["_faq_cfs"][0]["path"].rsplit("/", 1)[0] == \
        "/content/dam/iffcotokio/content-fragments/faq"
    warnings = tree["_warnings"]
    assert len(warnings) == 1
    assert "siteOrigin" in warnings[0]
    assert "faqCategory" in warnings[0]


def test_fragments_with_a_derivable_hierarchy_carry_no_warning():
    tree = map_ncm_to_node_tree(
        _ncm_with_faq(f"{ORIGIN}/health-insurance/family/who-buys"), _config_with_faq())
    assert "_warnings" not in tree
