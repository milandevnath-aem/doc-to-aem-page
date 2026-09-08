"""Zip a FileVault package: node tree -> installable .zip bytes.

Output layout:

    META-INF/vault/filter.xml         # which JCR paths this package covers
    META-INF/vault/properties.xml     # group / name / version metadata
    jcr_root/<path>/.content.xml      # the page's node subtree

Byte-deterministic -- fixed zip entry timestamps + sorted entry order,
so the same tree + meta always produces the same bytes. Useful for
diff-based review and CI reproducibility.
"""

import zipfile
from dataclasses import dataclass
from io import BytesIO

from aem.package_generator.filevault import node_to_content_xml

# Fixed timestamp so builds are reproducible. Uses zipfile's canonical
# "no timestamp" marker (1980-01-01) which every unzipper accepts.
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass
class PackageMeta:
    name: str
    group: str = "iffcotokio-content-automation"
    version: str = "1.0.0"
    description: str = "Generated from a Word document."


def build_package(tree: dict, meta: PackageMeta) -> bytes:
    """Return the zip contents for a single-page FileVault package."""
    entries = _entries_for(tree, meta)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(entries):
            info = zipfile.ZipInfo(path, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, entries[path])
    return buf.getvalue()


def _entries_for(tree: dict, meta: PackageMeta) -> dict[str, str]:
    page_path = tree["path"]
    content_xml = node_to_content_xml(tree)
    return {
        "META-INF/vault/filter.xml": _filter_xml(page_path),
        "META-INF/vault/properties.xml": _properties_xml(meta),
        f"jcr_root{page_path}/.content.xml": content_xml,
    }


def _filter_xml(page_path: str) -> str:
    """Scope the package to this page path so an install doesn't clobber
    sibling pages under the same content root."""
    return _multi_filter_xml([page_path])


def _multi_filter_xml(page_paths: list[str]) -> str:
    filters = "\n".join(f'    <filter root="{p}"/>' for p in page_paths)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<workspaceFilter version="1.0">\n'
        f'{filters}\n'
        '</workspaceFilter>\n'
    )


def build_batch_package(trees: list[dict], meta: PackageMeta) -> bytes:
    """Multi-page variant of build_package: one .zip covers N pages, the
    filter.xml lists one root per page so a single install writes them
    all without clobbering unrelated siblings."""
    entries: dict[str, str] = {
        "META-INF/vault/filter.xml": _multi_filter_xml([t["path"] for t in trees]),
        "META-INF/vault/properties.xml": _properties_xml(meta),
    }
    for tree in trees:
        entries[f"jcr_root{tree['path']}/.content.xml"] = node_to_content_xml(tree)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(entries):
            info = zipfile.ZipInfo(path, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, entries[path])
    return buf.getvalue()


def _properties_xml(meta: PackageMeta) -> str:
    """FileVault package manifest -- Java Properties XML format, which
    AEM's Package Manager UI displays as-is."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE properties SYSTEM "http://java.sun.com/dtd/properties.dtd">\n'
        '<properties>\n'
        f'    <entry key="group">{_escape(meta.group)}</entry>\n'
        f'    <entry key="name">{_escape(meta.name)}</entry>\n'
        f'    <entry key="version">{_escape(meta.version)}</entry>\n'
        f'    <entry key="description">{_escape(meta.description)}</entry>\n'
        '</properties>\n'
    )


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
