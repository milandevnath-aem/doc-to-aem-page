"""FileVault serialization: node tree -> .content.xml.

The XML format is stable enough to test literally on small inputs -- if
attribute order / escaping / namespace declarations drift, AEM refuses
to install the package, so keeping the tests string-literal is the point.
"""

from aem.package_generator.filevault import node_to_content_xml


def _text_node(text: str = "<p>Hello</p>") -> dict:
    return {
        "name": "page-description_0",
        "primary_type": "nt:unstructured",
        "properties": {
            "sling:resourceType": "iffcotokio/components/text",
            "text": text,
            "textIsRich": "true",
        },
        "children": [],
    }


def test_simple_node_serializes_with_jcr_namespace_and_props():
    xml = node_to_content_xml(_text_node())
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
    assert 'xmlns:jcr="http://www.jcp.org/jcr/1.0"' in xml
    assert 'xmlns:sling="http://sling.apache.org/jcr/sling/1.0"' in xml
    assert 'jcr:primaryType="nt:unstructured"' in xml
    assert 'sling:resourceType="iffcotokio/components/text"' in xml
    assert 'text="&lt;p&gt;Hello&lt;/p&gt;"' in xml
    assert 'textIsRich="true"' in xml


def test_child_node_becomes_nested_xml_element():
    node = {
        "name": "root",
        "primary_type": "nt:unstructured",
        "properties": {"sling:resourceType": "iffcotokio/components/container"},
        "children": [_text_node()],
    }
    xml = node_to_content_xml(node)
    # child element uses its node name as tag; JCR namespace only on root
    assert "<page-description_0" in xml
    assert 'jcr:primaryType="nt:unstructured"' in xml
    # child element closes properly
    assert "</page-description_0>" in xml or "/>" in xml


def test_string_special_chars_escaped_in_attributes():
    node = {
        "name": "n",
        "primary_type": "nt:unstructured",
        "properties": {
            "quoted": 'A "quoted" value',
            "ampersand": "A & B",
            "angle": "<not a tag>",
        },
        "children": [],
    }
    xml = node_to_content_xml(node)
    assert 'quoted="A &quot;quoted&quot; value"' in xml
    assert 'ampersand="A &amp; B"' in xml
    assert 'angle="&lt;not a tag&gt;"' in xml


def test_string_value_with_comma_or_bracket_escaped_for_filevault():
    """FileVault interprets `[val1,val2]` as a multi-value string. A literal
    `[` or `,` inside a single-value string must be escaped with `\\` so
    AEM does not mis-parse it as a multi-value list."""
    node = {
        "name": "n",
        "primary_type": "nt:unstructured",
        "properties": {"csv": "one, two", "opener": "[start"},
        "children": [],
    }
    xml = node_to_content_xml(node)
    assert r'csv="one\, two"' in xml
    assert r'opener="\[start"' in xml


def test_multivalue_string_property_serialized_with_bracket_syntax():
    node = {
        "name": "n",
        "primary_type": "nt:unstructured",
        "properties": {"cq:tags": ["ins/health", "ins/family"]},
        "children": [],
    }
    xml = node_to_content_xml(node)
    assert 'cq:tags="[ins/health,ins/family]"' in xml


def test_boolean_property_gets_typed_prefix():
    node = {
        "name": "n",
        "primary_type": "nt:unstructured",
        "properties": {"hideInNav": True, "activated": False},
        "children": [],
    }
    xml = node_to_content_xml(node)
    assert 'hideInNav="{Boolean}true"' in xml
    assert 'activated="{Boolean}false"' in xml


def test_integer_property_gets_long_typed_prefix():
    node = {
        "name": "n",
        "primary_type": "nt:unstructured",
        "properties": {"order": 3},
        "children": [],
    }
    xml = node_to_content_xml(node)
    assert 'order="{Long}3"' in xml


def test_mapping_gap_marker_becomes_regular_string_property():
    """`__mapping_gap` from the mapper is a diagnostic field -- serialize
    it as-is so operators can spot missing mappings in the installed page,
    but no special prefixing."""
    node = {
        "name": "n_0",
        "primary_type": "nt:unstructured",
        "properties": {
            "sling:resourceType": "unknown-thing",
            "__mapping_gap": "unknown-thing",
        },
        "children": [],
    }
    xml = node_to_content_xml(node)
    assert '__mapping_gap="unknown-thing"' in xml


def test_property_order_alphabetical_with_jcr_first():
    """JCR convention (and easier diffs) -- jcr:primaryType comes first,
    then everything else alphabetically."""
    node = {
        "name": "n",
        "primary_type": "cq:PageContent",
        "properties": {"zulu": "z", "alpha": "a", "sling:resourceType": "x"},
        "children": [],
    }
    xml = node_to_content_xml(node)
    idx_primary = xml.index("jcr:primaryType")
    idx_alpha = xml.index("alpha=")
    idx_sling = xml.index("sling:resourceType=")
    idx_zulu = xml.index("zulu=")
    assert idx_primary < idx_alpha < idx_sling < idx_zulu


def test_full_page_tree_produces_installable_shape():
    """Smoke: real-ish tree with meta props, root container, one component.
    Not asserting byte-exact -- asserting the pieces AEM needs are present."""
    tree = {
        "name": "the-page",
        "path": "/content/site/en/articles/the-page",
        "primary_type": "cq:Page",
        "properties": {},
        "children": [{
            "name": "jcr:content",
            "primary_type": "cq:PageContent",
            "properties": {
                "sling:resourceType": "site/components/page",
                "cq:template": "/conf/site/settings/wcm/templates/article",
                "jcr:title": "The Page",
            },
            "children": [{
                "name": "root",
                "primary_type": "nt:unstructured",
                "properties": {"sling:resourceType": "site/components/container"},
                "children": [_text_node()],
            }],
        }],
    }
    xml = node_to_content_xml(tree)
    for expected in [
        '<?xml version="1.0" encoding="UTF-8"?>',
        'xmlns:cq="http://www.day.com/jcr/cq/1.0"',
        'jcr:primaryType="cq:Page"',
        "<jcr:content",
        'jcr:primaryType="cq:PageContent"',
        'cq:template="/conf/site/settings/wcm/templates/article"',
        'jcr:title="The Page"',
        "<root",
        "<page-description_0",
        'text="&lt;p&gt;Hello&lt;/p&gt;"',
        "</jcr:content>",
        "</jcr:root>",
    ]:
        assert expected in xml, f"expected {expected!r} in generated XML"
