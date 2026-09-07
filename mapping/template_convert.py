"""AEM Sling `.infinity.json` response -> internal node tree.

Sling returns a JCR subtree as nested JSON where at each level the keys
are either JCR properties (leaf values) or child nodes (dict values).
Our internal tree uses named nodes with an ordered children list, so
the converter picks them apart.

While walking, drop AEM-managed metadata -- properties AEM sets on its
own at install time. Shipping them in a package either wastes bytes or
picks fights with AEM's own values on install.
"""

# Properties AEM writes on its own. Dropping them keeps generated .content.xml
# focused on what actually configures the page and its components.
_STRIP_PROPERTIES = frozenset({
    "jcr:createdBy",
    "jcr:created",
    "jcr:lastModified",
    "jcr:lastModifiedBy",
    "cq:lastModified",
    "cq:lastModifiedBy",
    "cq:createdComment",
})


def aem_json_to_node(name: str, payload: dict) -> dict:
    """Convert one AEM Sling JSON subtree to an internal-format node.

    Recursive: every dict value becomes a child node, every scalar value
    (string / number / boolean / list) becomes a JCR property, and every
    key in `_STRIP_PROPERTIES` is dropped at every level.
    """
    properties: dict = {}
    children: list[dict] = []
    for key, value in payload.items():
        if key in _STRIP_PROPERTIES:
            continue
        if key == "jcr:primaryType":
            continue  # promoted to the node's primary_type field below
        if isinstance(value, dict):
            children.append(aem_json_to_node(key, value))
        else:
            properties[key] = value
    return {
        "name": name,
        "primary_type": payload.get("jcr:primaryType", "nt:unstructured"),
        "properties": properties,
        "children": children,
    }
