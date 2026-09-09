"""Config layer for the AEM-side of the pipeline.

`SiteConfig` (small operator-facing settings: base URL, active page type,
auth token env-var name) and `PageTypeConfig` (fat config fetched from AEM:
template path, content root, DAM root, component mappings).

Both cache to disk so the pipeline can run offline once configs have been
fetched or loaded from samples.
"""


import pytest

from aem_client.config import (
    PageTypeConfig,
    SiteConfig,
    load_page_type_config,
    load_page_type_from_sample,
    load_site_config,
    save_page_type_config,
    save_site_config,
)


def test_site_config_defaults_to_local_aem_when_no_file(tmp_path):
    cfg = load_site_config(tmp_path / "missing.json")
    assert cfg.aem_base_url == "http://localhost:4502"
    assert cfg.active_page_type == "article"
    assert cfg.auth_token_env == "AEM_AUTH_TOKEN"
    assert cfg.cf_paths == {"article": "/content/dam/iffcotokio/site-config/article"}


def test_site_config_round_trip(tmp_path):
    path = tmp_path / "site.json"
    original = SiteConfig(
        aem_base_url="https://author-dev.acme.aemaacs.com",
        active_page_type="blog",
        auth_token_env="AEM_TOKEN_DEV",
        cf_paths={"blog": "/content/dam/acme/site-config/blog"},
    )
    save_site_config(original, path)

    loaded = load_site_config(path)
    assert loaded == original
    # file is human-editable JSON with a trailing newline
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "aem_base_url" in text


def test_page_type_config_from_dict_normalizes_keys():
    """Real AEM CF returns camelCase or JCR-style keys; PageTypeConfig
    normalizes to snake_case internally."""
    raw = {
        "type": "article",
        "templatePath": "/conf/site/templates/article",
        "contentRoot": "/content/site/en/articles",
        "damRoot": "/content/dam/site/articles",
        "pageNameStrategy": "slug_from_meta_title",
        "componentMappings": {
            "page-description": "iffcotokio/components/text",
            "faq": "iffcotokio/components/faq",
        },
    }
    cfg = PageTypeConfig.from_dict(raw)
    assert cfg.type == "article"
    assert cfg.template_path == "/conf/site/templates/article"
    assert cfg.content_root == "/content/site/en/articles"
    assert cfg.dam_root == "/content/dam/site/articles"
    assert cfg.page_name_strategy == "slug_from_meta_title"
    assert cfg.component_mappings["faq"] == "iffcotokio/components/faq"


def test_page_type_config_round_trip_cache(tmp_path):
    cfg = PageTypeConfig(
        type="article",
        template_path="/conf/x",
        content_root="/content/x",
        dam_root="/content/dam/x",
        page_name_strategy="slug_from_meta_title",
        component_mappings={"faq": "site/components/faq"},
    )
    path = tmp_path / "article.json"
    save_page_type_config(cfg, path)

    loaded = load_page_type_config(path)
    assert loaded == cfg


def test_load_page_type_from_sample_returns_article_default():
    """Sample config in config/samples/ unblocks development before the
    client's AEM hands over real page-type configs."""
    cfg = load_page_type_from_sample("article")
    assert cfg.type == "article"
    assert cfg.template_path.startswith("/conf/")
    assert cfg.content_root.startswith("/content/")
    assert cfg.dam_root.startswith("/content/dam/")
    assert "page-description" in cfg.component_mappings


def test_load_page_type_from_sample_unknown_raises():
    with pytest.raises(FileNotFoundError):
        load_page_type_from_sample("nonexistent-type")


def test_load_page_type_config_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_page_type_config(tmp_path / "missing.json")


def test_from_dict_ignores_unknown_fields():
    raw = {
        "type": "blog",
        "templatePath": "/x",
        "contentRoot": "/y",
        "damRoot": "/z",
        "pageNameStrategy": "slug_from_meta_title",
        "componentMappings": {},
        "someUnknownField": "ignored",
        "jcr:primaryType": "nt:unstructured",
    }
    cfg = PageTypeConfig.from_dict(raw)
    assert cfg.type == "blog"


def test_from_dict_missing_required_raises():
    with pytest.raises(KeyError):
        PageTypeConfig.from_dict({"type": "article"})  # missing templatePath, etc.


# ------ nested component-mapping value shape ------
# The mapping value can be a bare resourceType string (old form) OR a dict
# with resourceType + per-field renames (needed when an existing AEM
# component uses different property names than our NCM).

def test_component_mapping_string_form_is_kept_verbatim():
    raw = {
        "type": "article", "templatePath": "/x", "contentRoot": "/y", "damRoot": "/z",
        "pageNameStrategy": "slug_from_meta_title",
        "componentMappings": {"faq": "iffcotokio/components/faq"},
    }
    cfg = PageTypeConfig.from_dict(raw)
    assert cfg.component_mappings["faq"] == "iffcotokio/components/faq"


def test_component_mapping_dict_form_normalizes_and_survives_round_trip(tmp_path):
    raw = {
        "type": "article", "templatePath": "/x", "contentRoot": "/y", "damRoot": "/z",
        "pageNameStrategy": "slug_from_meta_title",
        "componentMappings": {
            "page-description": {
                "resourceType": "iffcotokio/components/text",
                "fieldRenames": {"htmltext": "richTextContent"},
            },
            "faq": {
                "resourceType": "iffcotokio/components/faq",
                "fieldRenames": {"title": "faqTitle"},
                "itemFieldRenames": {"question": "faqQuestion", "htmltext": "faqAnswer"},
            },
        },
    }
    cfg = PageTypeConfig.from_dict(raw)

    pd = cfg.component_mappings["page-description"]
    assert pd["resourceType"] == "iffcotokio/components/text"
    assert pd["fieldRenames"] == {"htmltext": "richTextContent"}

    faq = cfg.component_mappings["faq"]
    assert faq["itemFieldRenames"]["question"] == "faqQuestion"
    assert faq["itemFieldRenames"]["htmltext"] == "faqAnswer"

    # cache round-trip preserves the nested shape
    path = tmp_path / "cfg.json"
    save_page_type_config(cfg, path)
    reloaded = load_page_type_config(path)
    assert reloaded == cfg


def test_page_and_container_resource_types_default_when_not_in_cf():
    """Older CF payloads that don't declare the page/container resource
    types keep working -- the config falls back to a sensible default
    derived from the page type."""
    raw = {
        "type": "article", "templatePath": "/x", "contentRoot": "/y", "damRoot": "/z",
        "pageNameStrategy": "slug_from_meta_title",
        "componentMappings": {},
    }
    cfg = PageTypeConfig.from_dict(raw)
    assert cfg.page_resource_type == "article/components/page"
    assert cfg.container_resource_type == "article/components/container"


def test_component_slots_and_template_initial_survive_round_trip(tmp_path):
    raw = {
        "type": "article", "templatePath": "/x", "contentRoot": "/y", "damRoot": "/z",
        "pageNameStrategy": "slug_from_meta_title",
        "componentMappings": {},
        "componentSlots": {
            "page-description": "root/responsivegrid/right_par/page_description",
        },
        "templateInitialContent": {
            "name": "jcr:content", "primary_type": "cq:PageContent",
            "properties": {"sling:resourceType": "site/components/page"},
            "children": [],
        },
    }
    cfg = PageTypeConfig.from_dict(raw)
    assert cfg.component_slots == {
        "page-description": "root/responsivegrid/right_par/page_description"
    }
    assert cfg.template_initial_content["name"] == "jcr:content"

    path = tmp_path / "cfg.json"
    save_page_type_config(cfg, path)
    reloaded = load_page_type_config(path)
    assert reloaded == cfg


def test_page_and_container_resource_types_from_cf():
    raw = {
        "type": "article", "templatePath": "/x", "contentRoot": "/y", "damRoot": "/z",
        "pageNameStrategy": "slug_from_meta_title",
        "componentMappings": {},
        "pageResourceType": "iffcotokio/components/page",
        "containerResourceType": "iffcotokio/components/parsys",
    }
    cfg = PageTypeConfig.from_dict(raw)
    assert cfg.page_resource_type == "iffcotokio/components/page"
    assert cfg.container_resource_type == "iffcotokio/components/parsys"


def test_component_mapping_dict_missing_resource_type_raises():
    raw = {
        "type": "article", "templatePath": "/x", "contentRoot": "/y", "damRoot": "/z",
        "pageNameStrategy": "slug_from_meta_title",
        "componentMappings": {"faq": {"fieldRenames": {"htmltext": "text"}}},
    }
    with pytest.raises(KeyError):
        PageTypeConfig.from_dict(raw)


# ---- asset DAM path resolution (Phase 2B) ----

def _basic_cfg(**overrides) -> PageTypeConfig:
    base = {
        "type": "article",
        "template_path": "/conf/x",
        "content_root": "/content/x",
        "dam_root": "/content/dam/iffcotokio/articles",
        "page_name_strategy": "slug_from_url",
        "component_mappings": {},
    }
    base.update(overrides)
    return PageTypeConfig(**base)


def test_asset_dam_path_defaults_to_flat_layout():
    """Without the sub-base sorting flag, every asset for the page lives
    directly under `dam_root`, keeping the original filename. Matches the
    existing hand-authored asset convention for this client."""
    cfg = _basic_cfg()
    assert cfg.asset_dam_path(
        page_slug="cashless-treatment-vs-reimbursement-claims",
        filename="car-insurance.jpg",
    ) == "/content/dam/iffcotokio/articles/car-insurance.jpg"


def test_asset_dam_path_with_sub_base_sorting_buckets_per_page():
    """`enable_sub_base_image_sorting=True` puts every page's assets in a
    sub-folder named after the page slug, so cross-page filename collisions
    can't overwrite each other."""
    cfg = _basic_cfg(enable_sub_base_image_sorting=True)
    assert cfg.asset_dam_path(
        page_slug="cashless-treatment-vs-reimbursement-claims",
        filename="car-insurance.jpg",
    ) == "/content/dam/iffcotokio/articles/cashless-treatment-vs-reimbursement-claims/car-insurance.jpg"


def test_asset_dam_path_preserves_original_filename_casing():
    """The DAM path keeps the filename exactly as it appears in the doc
    marker -- no forced lowercase, no extension normalization -- so the
    installed asset name matches what the operator sees on disk."""
    cfg = _basic_cfg()
    assert cfg.asset_dam_path("some-slug", "Hero-Banner.PNG").endswith(
        "/Hero-Banner.PNG"
    )


def test_enable_sub_base_image_sorting_from_cf_camel_case():
    """CF payload uses camelCase; from_dict normalizes it just like every
    other camelCase field."""
    raw = {
        "type": "article", "templatePath": "/x", "contentRoot": "/y",
        "damRoot": "/content/dam/x",
        "pageNameStrategy": "slug_from_url", "componentMappings": {},
        "enableSubBaseImageSorting": True,
    }
    cfg = PageTypeConfig.from_dict(raw)
    assert cfg.enable_sub_base_image_sorting is True


def test_enable_sub_base_image_sorting_defaults_false_when_absent():
    raw = {
        "type": "article", "templatePath": "/x", "contentRoot": "/y",
        "damRoot": "/z", "pageNameStrategy": "slug_from_url",
        "componentMappings": {},
    }
    cfg = PageTypeConfig.from_dict(raw)
    assert cfg.enable_sub_base_image_sorting is False
