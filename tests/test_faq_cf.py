"""FAQ NCM blocks -> Content Fragment node trees."""

from aem_client.config import FaqConfig, PageTypeConfig
from mapping.faq_cf import build_faq_cf_trees


def _cfg(**over):
    base = dict(
        type="article", template_path="/conf/t", content_root="/content/iffcotokio/en",
        dam_root="/content/dam/x", page_name_strategy="slug_from_url",
        component_mappings={},
        faq=FaqConfig(
            cf_root="/content/dam/iffcotokio/content-fragments/faq",
            cf_model="/conf/iffcotokio/settings/dam/cfm/models/faq-template",
            tag_namespace="iffcotokio",
            field_names={"question": "question", "answer": "answer",
                         "answerHTML": "answerHTML", "category": "faqCategory"},
            page_component={"resourceType": "iffcotokio/components/faq",
                            "pathsProperty": "fragmentPaths"},
        ),
    )
    base.update(over)
    return PageTypeConfig(**base)


def _ncm(items):
    return {"page": {"properties": {}}, "source": {"path": "x.docx"},
            "blocks": [{"name": "faq", "variant": None, "fields": {"title": "FAQs"},
                        "items": [{"id": None, "implicit": True, "fields": f}
                                  for f in items], "table": None}]}


def test_builds_one_tree_per_question():
    ncm = _ncm([
        {"question": "Can parents be included?", "htmltext": "<p>Yes.</p>"},
        {"question": "Is it mandatory?", "htmltext": "<p>No.</p>"},
    ])
    trees = build_faq_cf_trees(ncm, _cfg(), ["health-insurance", "family"])
    assert len(trees) == 2
    assert trees[0]["path"] == (
        "/content/dam/iffcotokio/content-fragments/faq/health-insurance/family/"
        "can-parents-be-included")
    assert trees[0]["name"] == "can-parents-be-included"
    assert trees[0]["primary_type"] == "dam:Asset"


def test_master_carries_question_answer_and_tags():
    trees = build_faq_cf_trees(
        _ncm([{"question": "Can parents be included?",
               "htmltext": "<p>Yes, <b>always</b>.</p>"}]),
        _cfg(), ["health-insurance", "family"])
    jcr_content = trees[0]["children"][0]
    assert jcr_content["properties"]["contentFragment"] is True
    assert jcr_content["properties"]["jcr:title"] == "Can parents be included?"
    data = jcr_content["children"][0]
    assert data["properties"]["cq:model"] == (
        "/conf/iffcotokio/settings/dam/cfm/models/faq-template")
    master = data["children"][0]["properties"]
    # trailing "?" stripped on the question field, kept on jcr:title --
    # matches the client's existing fragments
    assert master["question"] == "Can parents be included"
    assert master["answerHTML"] == "<p>Yes, <b>always</b>.</p>"
    assert master["answer"] == "Yes, always."
    assert master["faqCategory"] == ["iffcotokio:health-insurance", "iffcotokio:family"]
    assert master["jcr:mixinTypes"] == ["cq:Taggable"]


def test_no_jcr_uuid_is_emitted():
    """AEM assigns a fresh UUID at install. Shipping one would pin it."""
    trees = build_faq_cf_trees(_ncm([{"question": "Q?", "htmltext": "<p>A.</p>"}]),
                               _cfg(), ["a"])
    assert "jcr:uuid" not in trees[0]["properties"]
    assert trees[0]["properties"]["jcr:mixinTypes"] == ["mix:referenceable"]


def test_duplicate_question_slugs_are_suffixed():
    trees = build_faq_cf_trees(
        _ncm([{"question": "Is it covered?", "htmltext": "<p>Yes.</p>"},
              {"question": "Is it covered?", "htmltext": "<p>Sometimes.</p>"}]),
        _cfg(), ["a"])
    assert trees[0]["name"] == "is-it-covered"
    assert trees[1]["name"] == "is-it-covered-2"


def test_no_faq_config_yields_no_trees():
    trees = build_faq_cf_trees(_ncm([{"question": "Q?", "htmltext": "<p>A.</p>"}]),
                               _cfg(faq=None), ["a"])
    assert trees == []


def test_item_without_question_is_skipped():
    trees = build_faq_cf_trees(_ncm([{"htmltext": "<p>orphan</p>"}]), _cfg(), ["a"])
    assert trees == []
