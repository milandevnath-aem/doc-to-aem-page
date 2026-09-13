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

import copy
from pathlib import Path

from aem_client.config import PageTypeConfig
from mapping.faq_cf import build_faq_cf_trees
from mapping.slug import slugify
from mapping.url_paths import page_path as _build_page_path, split_site_url

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


def map_ncm_to_node_tree(
    ncm: dict,
    cfg: PageTypeConfig,
    page_assets: dict[str, bytes] | None = None,
) -> dict:
    page_name, page_jcr_path = _resolve_page_location(ncm, cfg)
    # Sync page-level meta into block fields BEFORE resolving assets or
    # walking blocks. Currently only new-hero-banner needs this (its
    # `title` field feeds bannerTitle, and the doc has no per-block
    # title of its own -- it comes from the Meta Title line).
    _inject_page_meta_into_blocks(ncm)
    # Resolve folder assets BEFORE building the tree so per-block
    # `imageFile` markers get rewritten into `imagePath` on the NCM
    # itself; the rest of the pipeline stays oblivious to the lookup.
    resolved_assets = _resolve_page_assets(ncm, cfg, page_name, page_assets or {})
    hierarchy, _ = split_site_url(
        ncm["page"]["properties"].get("url") or "", cfg.site_origin)
    faq_cfs = build_faq_cf_trees(ncm, cfg, hierarchy)
    if cfg.template_initial_content:
        jcr_content = _build_jcr_content_from_template(ncm, cfg, faq_cfs)
    else:
        jcr_content = _build_jcr_content(ncm, cfg, faq_cfs)
    tree = {
        "name": page_name,
        "path": page_jcr_path,
        "primary_type": "cq:Page",
        "properties": {},
        "children": [jcr_content],
    }
    if resolved_assets:
        # Special key stripped by the packager before XML serialization;
        # kept on the page node so batch-mode iterates assets per-page
        # in the same shape it iterates the multi-root filter.
        tree["_assets"] = resolved_assets
    # `_source_url` feeds the batch report's `url` column, so it is set
    # whenever the doc HAS a url -- a doc with a perfectly good URL and no
    # FAQ section must not report a blank url, which is the one thing that
    # column exists to tell apart from "this doc had no URL".
    source_url = ncm["page"]["properties"].get("url") or ""
    if source_url:
        tree["_source_url"] = source_url
    if faq_cfs:
        # Stripped by the packager before XML serialization, exactly like
        # `_assets`. `_faq_cf_root` saves the packager from reverse-engineering
        # where the hierarchy starts inside a fragment path.
        tree["_faq_cfs"] = faq_cfs
        tree["_faq_cf_root"] = cfg.faq.cf_root
        if not hierarchy:
            # Fragments were generated but no section hierarchy could be
            # derived: they land flat under cfRoot with no faqCategory tags.
            # Silent mis-filing otherwise -- surface it so the batch report
            # can show it in pages.csv.
            _warn(tree,
                  "FAQ fragments have no section hierarchy: they land flat "
                  f"under {cfg.faq.cf_root} with no faqCategory tags. Most "
                  "likely siteOrigin is unset in the config or does not match "
                  f"the doc's URL ({source_url or 'none found'}).")
    return tree


def _warn(tree: dict, message: str) -> None:
    """Operator-facing warning carried on the page tree. Read by the batch
    report's `warnings` column; ignored by the packager like every other
    underscore key."""
    tree.setdefault("_warnings", []).append(message)


# Components whose `title` field is authoritatively sourced from the
# doc's Meta Title (page.properties.title), NOT from an in-block title
# line. Extend when a second component adopts the same contract.
_META_TITLE_COMPONENTS = {"new-hero-banner"}


def _inject_page_meta_into_blocks(ncm: dict) -> None:
    """Populate per-block `title` from `page.properties.title` for the
    components that source their title from the doc's Meta Title. Runs
    BEFORE mapping so the standard `fieldRenames` (title -> bannerTitle)
    picks it up like any other field. Skips blocks whose `title` was
    already authored inline (author intent wins)."""
    meta_title = (ncm.get("page", {}).get("properties", {}) or {}).get("title") or ""
    if not meta_title:
        return
    for block in ncm.get("blocks", []):
        if block.get("name") not in _META_TITLE_COMPONENTS:
            continue
        fields = block.setdefault("fields", {})
        fields.setdefault("title", meta_title)


def _resolve_page_assets(
    ncm: dict, cfg: PageTypeConfig, page_slug: str,
    page_assets: dict[str, bytes],
) -> list[dict]:
    """Walk every NCM block; for any `imageFile` marker, look up the
    referenced filename in `page_assets` (case-insensitive) and rewrite
    the block's fields:

        imageFile: "car-insurance.jpg"
            -> imagePath: "<dam_root>/[slug/]car-insurance.jpg" and drop imageFile.

    A miss (referenced file not in the folder) drops the marker WITHOUT
    setting imagePath, so the template's default asset stays in place.

    Returns descriptors of the assets that were resolved, in NCM block
    order, for the packager to bundle.
    """
    if not page_assets:
        # Even when no assets are supplied, strip the marker so
        # single-doc runs don't ship the internal signal as a JCR
        # property.
        for block in ncm.get("blocks", []):
            block.get("fields", {}).pop("imageFile", None)
        return []
    by_lower = {name.lower(): (name, blob) for name, blob in page_assets.items()}
    descriptors: list[dict] = []
    for block in ncm.get("blocks", []):
        fields = block.get("fields", {})
        marker = fields.pop("imageFile", None)
        if not marker:
            continue
        match = by_lower.get(marker.lower())
        if not match:
            continue  # missing asset -> template default preserved
        actual_name, blob = match
        dam_path = cfg.asset_dam_path(page_slug, actual_name)
        fields["imagePath"] = dam_path
        descriptors.append({
            "dam_path": dam_path,
            "filename": actual_name,
            "bytes": blob,
        })
    return descriptors


def _resolve_page_location(ncm: dict, cfg: PageTypeConfig) -> tuple[str, str]:
    """Return (node_name, absolute_jcr_path).

    Under `slug_from_url` the URL's own path segments ARE the site hierarchy,
    so the page lands at content_root + hierarchy + slug. Every other strategy
    keeps the flat layout: one name directly under content_root.
    """
    props = ncm["page"]["properties"]
    if cfg.page_name_strategy == "slug_from_url":
        hierarchy, slug = split_site_url(props.get("url") or "", cfg.site_origin)
        if slug:
            return slug, _build_page_path(cfg.content_root, hierarchy, slug)
        # No usable URL -- fall through to the sentinel so batch mode skips
        # the page rather than inventing a location for it.
        name = slugify("")
        return name, _build_page_path(cfg.content_root, [], name)
    if cfg.page_name_strategy == "docx_filename":
        name = slugify(Path(ncm["source"]["path"]).stem)
    else:
        name = slugify(props.get("title", ""))
    return name, _build_page_path(cfg.content_root, [], name)


def _build_jcr_content(ncm: dict, cfg: PageTypeConfig, faq_cfs: list[dict]) -> dict:
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
        "children": [_build_root_container(ncm, cfg, faq_cfs)],
    }


def _faq_block_contributes_fragment(block: dict) -> bool:
    """True when at least one item on this `faq` block would actually
    produce a fragment (i.e. carries a non-blank `question`) -- mirrors
    `build_faq_cf_trees`'s own skip-blank-question rule without reaching
    into it, so the inline-removal decision matches what was actually
    generated block-by-block rather than config-wide. A block with no
    qualifying item must NOT be pulled off the page -- there is no
    fragment to reference, so removing it would silently drop the prose."""
    return any((item.get("fields", {}).get("question") or "").strip()
               for item in block.get("items", []))


def _build_root_container(ncm: dict, cfg: PageTypeConfig, faq_cfs: list[dict]) -> dict:
    # `faq` blocks that actually produced a fragment are represented by the
    # reference node below, not inline -- their prose already left the page
    # as generated Content Fragments. Only applies when FAQ CF generation is
    # configured AND the block contributed a fragment; otherwise a `faq`
    # block is legacy inline content like any other component.
    blocks = ncm.get("blocks", [])
    if cfg.faq is not None:
        blocks = [b for b in blocks
                  if not (b["name"] == "faq" and _faq_block_contributes_fragment(b))]
    children = [_build_component(block, i, cfg) for i, block in enumerate(blocks)]
    ref = _build_faq_reference(faq_cfs, cfg, len(children))
    if ref is not None:
        children.append(ref)
    return {
        "name": "root",
        "primary_type": "nt:unstructured",
        "properties": {"sling:resourceType": cfg.container_resource_type.strip()},
        "children": children,
    }


def _build_faq_reference(faq_cfs: list[dict], cfg: PageTypeConfig, index: int) -> dict | None:
    """A node pointing the page at its generated fragments. Returns None when
    there are no fragments, or when the AEM team has not yet supplied the
    component -- the fragments are still generated either way."""
    if not faq_cfs or cfg.faq is None:
        return None
    component = cfg.faq.page_component or {}
    resource_type = (component.get("resourceType") or "").strip()
    paths_property = (component.get("pathsProperty") or "").strip()
    if not resource_type or not paths_property:
        return None
    return {
        "name": f"faq_{index}",
        "primary_type": "nt:unstructured",
        "properties": {
            "sling:resourceType": resource_type,
            paths_property: [c["path"] for c in faq_cfs],
        },
        "children": [],
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


# ---------- template overlay mode ----------
#
# When PageTypeConfig.template_initial_content is set, the mapper uses that
# tree as the layout base -- component_slots names where each NCM block
# goes. Static template children (hero banner defaults, split containers,
# etc.) are preserved verbatim. Extra NCM blocks that overflow a slot land
# as siblings of the slot node; blocks with no slot at all fall back to
# the slot parent so nothing silently vanishes.


def _build_jcr_content_from_template(
    ncm: dict, cfg: PageTypeConfig, faq_cfs: list[dict],
) -> dict:
    # deep-copy the template so per-run overlays don't mutate the config
    jcr_content = copy.deepcopy(cfg.template_initial_content)
    # apply page-level properties (meta title, description, etc.) onto
    # jcr:content, mirroring the flat-mode build
    props = jcr_content["properties"]
    props["cq:template"] = cfg.template_path
    for source_key, jcr_key in _PAGE_PROPERTY_MAP.items():
        value = ncm["page"]["properties"].get(source_key, "")
        if value:
            props[jcr_key] = value

    slots = cfg.component_slots or {}
    # slot_parents keyed by parent-path so overflow siblings land right
    overflow_counters: dict[str, int] = {}

    for block in ncm.get("blocks", []):
        source_name = block["name"]
        if (source_name == "faq" and cfg.faq is not None
                and _faq_block_contributes_fragment(block)):
            # represented by the reference node appended below, not placed
            # into a slot -- its prose already left the page as CFs. A faq
            # block that contributed no fragment falls through to normal
            # slot placement so its content isn't silently dropped.
            continue
        slot_path = slots.get(source_name)
        if slot_path:
            slot_parts = slot_path.split("/")
            parent = _find_or_create_parent(jcr_content, slot_parts[:-1])
            leaf_name = slot_parts[-1]
            overflow_counters.setdefault(slot_path, 0)
            counter = overflow_counters[slot_path]
            existing = _find_child(parent, leaf_name)
            if counter == 0 and existing is not None:
                _merge_block_into_node(existing, block, cfg)
            else:
                node = _build_component(block, counter, cfg)
                node["name"] = leaf_name if counter == 0 else f"{leaf_name}_{counter}"
                parent["children"].append(node)
            overflow_counters[slot_path] += 1
        else:
            # No slot at all -- fall back to the "first slot parent" as
            # a visible catch-all so the operator sees the miss.
            fallback_parent = _pick_fallback_parent(jcr_content, slots)
            node = _build_component(block, len(fallback_parent["children"]), cfg)
            node["properties"]["__mapping_gap"] = source_name
            fallback_parent["children"].append(node)

    if faq_cfs and cfg.faq is not None:
        fallback_parent = _pick_fallback_parent(jcr_content, slots)
        ref = _build_faq_reference(faq_cfs, cfg, len(fallback_parent["children"]))
        if ref is not None:
            fallback_parent["children"].append(ref)
    return jcr_content


def _find_child(node: dict, name: str) -> dict | None:
    for c in node["children"]:
        if c["name"] == name:
            return c
    return None


def _find_or_create_parent(root: dict, parts: list[str]) -> dict:
    """Walk `root` following `parts` (relative to jcr:content), creating
    plain nt:unstructured containers for any missing segment."""
    node = root
    for name in parts:
        child = _find_child(node, name)
        if child is None:
            child = {"name": name, "primary_type": "nt:unstructured",
                     "properties": {}, "children": []}
            node["children"].append(child)
        node = child
    return node


def _merge_block_into_node(node: dict, block: dict, cfg: PageTypeConfig) -> None:
    """Overlay NCM block properties onto an existing template node -- NCM
    values win, template defaults preserved for keys the NCM does not
    supply. Used when a slot points at a node already present in the
    fetched template initial (e.g. new_hero_banner with default title)."""
    source_name = block["name"]
    entry = cfg.component_mappings.get(source_name)
    shape = _entry_shape(entry, source_name)
    field_props = _map_fields(block.get("fields", {}), cfg, shape["fieldRenames"])
    for k, v in shape["staticProperties"].items():
        field_props.setdefault(k, v)
    node["properties"].update(field_props)
    # only overwrite sling:resourceType when the mapping explicitly declares one
    if entry is not None:
        node["properties"]["sling:resourceType"] = shape["resourceType"]


def _pick_fallback_parent(jcr_content: dict, slots: dict) -> dict:
    """When an NCM block has no slot, put it under the parent of the first
    configured slot so unmapped content stays visible near where it likely
    belongs. Falls back to jcr:content root if no slots configured."""
    for slot_path in slots.values():
        parts = slot_path.split("/")
        return _find_or_create_parent(jcr_content, parts[:-1])
    return jcr_content
