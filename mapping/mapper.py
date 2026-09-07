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
        "sling:resourceType": cfg.page_resource_type.strip(),
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
        "properties": {"sling:resourceType": cfg.container_resource_type.strip()},
        "children": [_build_component(block, i, cfg)
                     for i, block in enumerate(ncm.get("blocks", []))],
    }


def _build_component(block: dict, index: int, cfg: PageTypeConfig) -> dict:
    source_name = block["name"]
    entry = cfg.component_mappings.get(source_name)
    shape = _entry_shape(entry, source_name)
    props = _map_fields(block.get("fields", {}), cfg, shape["fieldRenames"])
    # merge static properties AFTER field renames so NCM-supplied values win
    for k, v in shape["staticProperties"].items():
        props.setdefault(k, v)
    if entry is not None:
        props["sling:resourceType"] = shape["resourceType"]
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
        "children": [_build_item(item, i, cfg, shape["itemFieldRenames"],
                                 shape["itemStaticProperties"])
                     for i, item in enumerate(block.get("items", []))],
    }


def _build_item(item: dict, index: int, cfg: PageTypeConfig,
                renames: dict, statics: dict) -> dict:
    props = _map_fields(item.get("fields", {}), cfg, renames)
    for k, v in statics.items():
        props.setdefault(k, v)
    return {
        "name": f"item_{index}",
        "primary_type": "nt:unstructured",
        "properties": props,
        "children": [],
    }


def _entry_shape(entry, source_name: str) -> dict:
    """Unpack a component_mappings value into a normalized dict with
    resourceType, fieldRenames, itemFieldRenames, staticProperties,
    and itemStaticProperties. Accepts both the bare-string form and the
    nested-dict form. Resource types are stripped of surrounding
    whitespace so a stray copy-paste in the CF or operator form cannot
    silently ship an unresolvable resource type to AEM."""
    empty: dict = {}
    if isinstance(entry, str):
        return {"resourceType": entry.strip(), "fieldRenames": empty,
                "itemFieldRenames": empty, "staticProperties": empty,
                "itemStaticProperties": empty}
    if isinstance(entry, dict):
        return {
            "resourceType": (entry.get("resourceType") or source_name).strip(),
            "fieldRenames": entry.get("fieldRenames") or empty,
            "itemFieldRenames": entry.get("itemFieldRenames") or empty,
            "staticProperties": entry.get("staticProperties") or empty,
            "itemStaticProperties": entry.get("itemStaticProperties") or empty,
        }
    return {"resourceType": source_name, "fieldRenames": empty,
            "itemFieldRenames": empty, "staticProperties": empty,
            "itemStaticProperties": empty}


def _map_fields(fields: dict, cfg: PageTypeConfig, renames: dict | None = None) -> dict:
    """Translate NCM `fields` dict -> JCR property dict.

    Rules:
    - Any field named in `renames` is emitted under its renamed property
      name (and the automatic RTE default -- textIsRich=true for htmltext --
      is suppressed, since the renamed target may have a different rich-text
      contract or none at all).
    - Otherwise htmltext -> text + textIsRich=true (default AEM RTE contract)
    - image dict -> fileReference = dam_root/<name>
    - Everything else -> pass through under its rename target or original name
    - Falsy values dropped so empty strings don't overwrite template defaults
    """
    renames = renames or {}
    out: dict = {}
    for key, value in fields.items():
        if key == "image":
            fileref = _image_to_file_reference(value, cfg)
            if fileref:
                out["fileReference"] = fileref
            continue
        if not value and value != 0:
            continue
        if key in renames:
            out[renames[key]] = value
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
