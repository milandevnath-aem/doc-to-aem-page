"""FAQ NCM blocks -> AEM Content Fragment node trees.

A Content Fragment is a dam:Asset whose jcr:content carries
contentFragment=true and a `data/master` node holding the model's fields. That
is an ordinary node tree, so the existing FileVault serializer emits it with no
changes -- this module only has to produce the right shape.

Shape mirrors the client's reference fragment: jcr:title keeps the question's
trailing "?", the `question` field drops it.
"""

import re

from aem_client.config import PageTypeConfig
from mapping.slug import slugify
from mapping.url_paths import cf_folder_path, faq_categories

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    """Plain-text twin of the RTE HTML, for the model's `answer` field."""
    text = _TAG_RE.sub(" ", html or "")
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    text = re.sub(r"\s+", " ", text).strip()
    # tag stripping above leaves a space where a closing tag met trailing
    # punctuation (e.g. "always</b>." -> "always ."); drop it so plain-text
    # punctuation matches normal prose spacing.
    return re.sub(r"\s+([.,!?;:])", r"\1", text)


def build_faq_cf_trees(ncm: dict, cfg: PageTypeConfig, hierarchy: list[str]) -> list[dict]:
    """One CF node tree per FAQ question, in document order.

    Returns [] when the config declares no faq block -- pages still build, the
    fragments are simply not generated.
    """
    faq_cfg = cfg.faq
    if faq_cfg is None:
        return []
    folder = cf_folder_path(faq_cfg.cf_root, hierarchy)
    categories = faq_categories(faq_cfg.tag_namespace, hierarchy)
    names = faq_cfg.field_names
    trees: list[dict] = []
    seen: dict[str, int] = {}
    for block in ncm.get("blocks", []):
        if block.get("name") != "faq":
            continue
        for item in block.get("items", []):
            fields = item.get("fields", {})
            question = (fields.get("question") or "").strip()
            if not question:
                continue  # orphan answer with no question -- nothing to key on
            slug = slugify(question)
            seen[slug] = seen.get(slug, 0) + 1
            if seen[slug] > 1:
                slug = f"{slug}-{seen[slug]}"
            html = fields.get("htmltext") or ""
            master = {
                "jcr:mixinTypes": ["cq:Taggable"],
                names["question"]: question.rstrip("?").strip(),
                names["answer"]: _html_to_text(html),
                names["answerHTML"]: html,
            }
            if categories:
                master[names["category"]] = categories
            trees.append({
                "name": slug,
                "path": f"{folder}/{slug}",
                "primary_type": "dam:Asset",
                "properties": {"jcr:mixinTypes": ["mix:referenceable"]},
                "children": [{
                    "name": "jcr:content",
                    "primary_type": "dam:AssetContent",
                    "properties": {"jcr:title": question, "contentFragment": True},
                    "children": [
                        {"name": "data", "primary_type": "nt:unstructured",
                         "properties": {"cq:model": faq_cfg.cf_model},
                         "children": [{"name": "master",
                                       "primary_type": "nt:unstructured",
                                       "properties": master, "children": []}]},
                        {"name": "metadata", "primary_type": "nt:unstructured",
                         "properties": {"jcr:mixinTypes": ["cq:Taggable"]},
                         "children": []},
                        {"name": "related", "primary_type": "nt:unstructured",
                         "properties": {}, "children": []},
                    ],
                }],
            })
    return trees
