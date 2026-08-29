"""NCM + PageTypeConfig -> abstract AEM node tree.

The tree is plain-dict, JSON-serializable, no AEM SDK. Whichever backend
eventually installs pages (FileVault package, custom servlet, direct Sling
POST) consumes the same tree, so this is the seam between source-agnostic
NCM and destination-specific AEM output.

Tree shape:

    {
        "name": "<jcr node name>",
        "path": "<absolute JCR path>",      # only on the page node
        "primary_type": "cq:Page",
        "properties": {...},
        "children": [ {node}, {node} ],     # ordered
    }

Property naming today is intentionally straightforward -- title -> jcr:title,
description -> jcr:description, keywords -> metaKeywords, url ->
sling:vanityPath, old_url -> redirectFrom, h1 -> h1. Templates that need
different names (metaTitle, cq:seoTitle, etc.) will get an override map in
PageTypeConfig later. YAGNI for now.
"""

from pathlib import Path

from aem_client.config import PageTypeConfig
from mapping.slug import slugify

# NCM page.properties key -> jcr:content property name.
# Kept small on purpose. Extend via PageTypeConfig when per-template
# overrides become real.
_PAGE_PROPERTY_MAP = {
    "title": "jcr:title",
    "description": "jcr:description",
    "keywords": "metaKeywords",
    "h1": "h1",
    "url": "sling:vanityPath",
    "old_url": "redirectFrom",
}

# Any component whose fields carry `htmltext` writes it as the canonical
# AEM RTE contract: `text` + `textIsRich=true`.
_RTE_FIELD = "htmltext"

# Field-name -> JCR-property-name renames applied uniformly to every
# component and item. Keep tiny.
_FIELD_RENAMES = {
    "htmltext": "text",
}


def map_ncm_to_node_tree(ncm: dict, cfg: PageTypeConfig) -> dict:
    page_name = _pick_page_name(ncm, cfg)
    page_path = f"{cfg.content_root.rstrip('/')}/{page_name}"
    jcr_content = _build_jcr_content(ncm, cfg)
    return {
        "name": page_name,
        "path": page_path,
        "primary_type": "cq:Page",
        "properties": {},
        "children": [jcr_content],
    }


def _pick_page_name(ncm: dict, cfg: PageTypeConfig) -> str:
    props = ncm["page"]["properties"]
    strategy = cfg.page_name_strategy
    if strategy == "slug_from_url":
        segment = (props.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
        return slugify(segment) if segment else slugify(props.get("title", ""))
    if strategy == "docx_filename":
        return slugify(Path(ncm["source"]["path"]).stem)
    # slug_from_meta_title (default)
    return slugify(props.get("title", ""))


def _build_jcr_content(ncm: dict, cfg: PageTypeConfig) -> dict:
    props: dict = {
        "sling:resourceType": f"{cfg.type}/components/page",
        "cq:template": cfg.template_path,
    }
    # meta fields (skip empty -- template defaults win, no accidental overwrites)
    for source_key, jcr_key in _PAGE_PROPERTY_MAP.items():
        value = ncm["page"]["properties"].get(source_key, "")
        if value:
            props[jcr_key] = value
    return {
        "name": "jcr:content",
        "primary_type": "cq:PageContent",
        "properties": props,
        "children": [_build_root_container(ncm, cfg)],
    }


def _build_root_container(ncm: dict, cfg: PageTypeConfig) -> dict:
    return {
        "name": "root",
        "primary_type": "nt:unstructured",
        "properties": {"sling:resourceType": f"{cfg.type}/components/container"},
        "children": [_build_component(block, i, cfg)
                     for i, block in enumerate(ncm.get("blocks", []))],
    }


def _build_component(block: dict, index: int, cfg: PageTypeConfig) -> dict:
    source_name = block["name"]
    resource_type = cfg.component_mappings.get(source_name)
    props = _map_fields(block.get("fields", {}), cfg)
    if resource_type:
        props["sling:resourceType"] = resource_type
    else:
        # No mapping in the CF -- keep the block visible with a
        # __mapping_gap marker so the operator sees the miss instead of
        # the content silently vanishing at install time.
        props["sling:resourceType"] = source_name
        props["__mapping_gap"] = source_name
    return {
        "name": f"{source_name}_{index}",
        "primary_type": "nt:unstructured",
        "properties": props,
        "children": [_build_item(item, i, cfg)
                     for i, item in enumerate(block.get("items", []))],
    }


def _build_item(item: dict, index: int, cfg: PageTypeConfig) -> dict:
    return {
        "name": f"item_{index}",
        "primary_type": "nt:unstructured",
        "properties": _map_fields(item.get("fields", {}), cfg),
        "children": [],
    }


def _map_fields(fields: dict, cfg: PageTypeConfig) -> dict:
    """Translate NCM `fields` dict -> JCR property dict.

    Rules:
    - htmltext -> text + textIsRich=true (AEM RTE contract)
    - image dict -> fileReference = dam_root/<name>
    - anything else -> pass through under its rename target or original name
    - falsy values dropped so empty strings don't overwrite template defaults
    """
    out: dict = {}
    for key, value in fields.items():
        if key == "image":
            fileref = _image_to_file_reference(value, cfg)
            if fileref:
                out["fileReference"] = fileref
            continue
        if not value and value != 0:
            continue
        out_key = _FIELD_RENAMES.get(key, key)
        out[out_key] = value
        if key == _RTE_FIELD:
            out["textIsRich"] = "true"
    return out


def _image_to_file_reference(image: dict, cfg: PageTypeConfig) -> str:
    """AEM references DAM assets by their JCR path -- we don't care whether
    the image was pasted (embedded) or named (reference), only where it
    WILL live in the DAM."""
    if not isinstance(image, dict):
        return ""
    name = image.get("name") or ""
    if not name:
        return ""
    return f"{cfg.dam_root.rstrip('/')}/{name}"
