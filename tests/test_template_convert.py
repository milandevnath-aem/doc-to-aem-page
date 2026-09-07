"""AEM Sling `.infinity.json` response -> internal node tree conversion,
with AEM-metadata scrubbing.

Sling returns JCR subtrees as nested JSON where each key at a level is
either a JCR property (string/number/boolean) OR a child node (dict).
Our internal tree format is:

    {name, primary_type, properties, children}

The converter turns one into the other, dropping properties AEM sets
on its own at install time (jcr:createdBy, jcr:created, cq:lastModified,
etc.) -- if we ship those, AEM either ignores them or fights us on
install.
"""

from mapping.template_convert import aem_json_to_node


def test_flat_node_conversion():
    """A node with only properties, no children."""
    payload = {
        "jcr:primaryType": "nt:unstructured",
        "sling:resourceType": "iffcotokio/components/text",
        "text": "<p>Hi</p>",
    }
    node = aem_json_to_node("text", payload)
    assert node["name"] == "text"
    assert node["primary_type"] == "nt:unstructured"
    assert node["properties"]["sling:resourceType"] == "iffcotokio/components/text"
    assert node["properties"]["text"] == "<p>Hi</p>"
    assert node["children"] == []


def test_nested_children_become_children_list_in_order():
    payload = {
        "jcr:primaryType": "nt:unstructured",
        "sling:resourceType": "wcm/foundation/components/responsivegrid",
        "new_hero_banner": {
            "jcr:primaryType": "nt:unstructured",
            "sling:resourceType": "site/components/hero",
        },
        "right_par": {
            "jcr:primaryType": "nt:unstructured",
            "sling:resourceType": "wcm/foundation/components/responsivegrid",
        },
    }
    node = aem_json_to_node("responsivegrid", payload)
    child_names = [c["name"] for c in node["children"]]
    assert "new_hero_banner" in child_names
    assert "right_par" in child_names
    # each child preserved with own properties
    hero = next(c for c in node["children"] if c["name"] == "new_hero_banner")
    assert hero["properties"]["sling:resourceType"] == "site/components/hero"


def test_aem_managed_metadata_stripped():
    """Every AEM-managed property strips: jcr timestamps + created/modified
    identifiers + comment. Not in output, not in the .content.xml we ship."""
    payload = {
        "jcr:primaryType": "cq:PageContent",
        "jcr:createdBy": "admin",
        "jcr:created": "Mon Sep 07 2026 20:54:13 GMT+0530",
        "jcr:lastModified": "Mon Sep 07 2026 20:37:16 GMT+0000",
        "jcr:lastModifiedBy": "admin",
        "cq:lastModified": "Mon Sep 07 2026 20:37:16 GMT+0000",
        "cq:lastModifiedBy": "admin",
        "cq:createdComment": "auto",
        "jcr:mixinTypes": ["mix:versionable"],   # keep -- structural not metadata
        "sling:resourceType": "site/components/page",   # keep
        "cq:template": "/conf/site/templates/x",         # keep
    }
    node = aem_json_to_node("jcr:content", payload)
    p = node["properties"]
    for stripped in ["jcr:createdBy", "jcr:created", "jcr:lastModified",
                     "jcr:lastModifiedBy", "cq:lastModified",
                     "cq:lastModifiedBy", "cq:createdComment"]:
        assert stripped not in p, f"{stripped} should be stripped"
    for kept in ["sling:resourceType", "cq:template", "jcr:mixinTypes"]:
        assert kept in p, f"{kept} should be kept"


def test_metadata_stripping_is_recursive():
    payload = {
        "jcr:primaryType": "nt:unstructured",
        "jcr:createdBy": "admin",
        "child": {
            "jcr:primaryType": "nt:unstructured",
            "jcr:createdBy": "admin",       # also stripped on child
            "sling:resourceType": "x",
        },
    }
    node = aem_json_to_node("root", payload)
    child = node["children"][0]
    assert "jcr:createdBy" not in child["properties"]


def test_primary_type_defaults_to_nt_unstructured():
    """Some legacy AEM responses omit jcr:primaryType. Default so downstream
    package generation does not emit an empty attribute."""
    node = aem_json_to_node("x", {"sling:resourceType": "y"})
    assert node["primary_type"] == "nt:unstructured"
