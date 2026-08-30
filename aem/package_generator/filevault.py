"""Serialize an abstract AEM node tree into FileVault `.content.xml`.

FileVault is the XML format used by AEM Content Packages -- one XML file
per JCR node subtree, embedded inside a zip. This module handles the
tricky parts:

- Namespace declarations on the root element only.
- Attribute-based property serialization (JCR node -> XML element with
  the JCR properties as attributes; children as nested elements).
- Typed prefixes: `{Boolean}true`, `{Long}42`, etc.
- Multi-value string syntax: `[val1,val2]`.
- FileVault-specific escaping for single-value strings that would
  otherwise be misparsed (`[`, `,`).
- Standard XML entity escaping for `<`, `>`, `&`, `"`.

The XML text this module produces is the exact bytes AEM's Package
Manager consumes; a schema mistake here (missing xmlns, unescaped
character, wrong type prefix) means AEM refuses to install the package.
"""

from xml.sax.saxutils import escape as _xml_escape

# Namespaces declared on the root <jcr:root> element. Cover every prefix
# that could plausibly appear in a page tree we generate. Extra unused
# namespaces are harmless; missing ones break installs.
_NAMESPACES = {
    "jcr": "http://www.jcp.org/jcr/1.0",
    "cq": "http://www.day.com/jcr/cq/1.0",
    "sling": "http://sling.apache.org/jcr/sling/1.0",
    "nt": "http://www.jcp.org/jcr/nt/1.0",
    "dam": "http://www.day.com/dam/1.0",
    "mix": "http://www.jcp.org/jcr/mix/1.0",
}


def node_to_content_xml(node: dict) -> str:
    """Return the full `.content.xml` text for a node tree rooted at
    `node`. Emits the XML prolog and declares all JCR namespaces on the
    root element."""
    body = _serialize_root(node)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def _serialize_root(node: dict) -> str:
    xmlns = " ".join(f'xmlns:{p}="{u}"' for p, u in _NAMESPACES.items())
    attrs = _format_attributes(node)
    children_xml = _serialize_children(node.get("children") or [], indent="    ")
    open_tag = f"<jcr:root {xmlns} {attrs}".rstrip()
    if not children_xml:
        return f"{open_tag}/>"
    return f"{open_tag}>\n{children_xml}\n</jcr:root>"


def _serialize_child(node: dict, indent: str) -> str:
    name = node["name"]
    attrs = _format_attributes(node)
    children_xml = _serialize_children(node.get("children") or [], indent + "    ")
    if not children_xml:
        return f'{indent}<{name} {attrs}/>'
    return f'{indent}<{name} {attrs}>\n{children_xml}\n{indent}</{name}>'


def _serialize_children(children: list, indent: str) -> str:
    return "\n".join(_serialize_child(c, indent) for c in children)


def _format_attributes(node: dict) -> str:
    """Return the XML attribute string for `node`, with jcr:primaryType
    first and everything else alphabetically."""
    parts = [f'jcr:primaryType="{node["primary_type"]}"']
    for key in sorted(node.get("properties") or {}):
        value = node["properties"][key]
        parts.append(f'{key}="{_format_value(value)}"')
    return " ".join(parts)


def _format_value(value) -> str:
    """Turn a Python value into a FileVault attribute value string with
    the right typed prefix, XML escaping, and value-level escaping."""
    if isinstance(value, bool):
        # bool check MUST precede int check -- bool is a subclass of int
        return "{Boolean}" + ("true" if value else "false")
    if isinstance(value, int):
        return f"{{Long}}{value}"
    if isinstance(value, float):
        return f"{{Double}}{value}"
    if isinstance(value, list):
        return "[" + ",".join(_escape_multivalue_item(str(v)) for v in value) + "]"
    return _escape_singlevalue_string(str(value))


def _escape_singlevalue_string(text: str) -> str:
    """XML-escape + FileVault-escape a single-value string. `[` and `,`
    get a leading backslash so the value is not misparsed as multi-value."""
    text = text.replace("\\", "\\\\")
    text = text.replace("[", r"\[")
    text = text.replace(",", r"\,")
    return _xml_escape(text, {'"': "&quot;"})


def _escape_multivalue_item(text: str) -> str:
    """Inside a multi-value list, individual items still need XML escape
    and comma escaping (but not `[` -- brackets are the list delimiters
    and only the outer pair is structural)."""
    text = text.replace("\\", "\\\\")
    text = text.replace(",", r"\,")
    return _xml_escape(text, {'"': "&quot;"})
