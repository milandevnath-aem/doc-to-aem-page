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
