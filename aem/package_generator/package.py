"""Zip a FileVault package: node tree -> installable .zip bytes.

Output layout:

    META-INF/vault/filter.xml         # which JCR paths this package covers
    META-INF/vault/properties.xml     # group / name / version metadata
    jcr_root/<path>/.content.xml      # the page's node subtree
    jcr_root/<dam_path>/.content.xml  # dam:Asset descriptor (Phase 2B)
    jcr_root/<dam_path>/_jcr_content/renditions/original  # binary payload

Byte-deterministic -- fixed zip entry timestamps + sorted entry order,
so the same tree + meta always produces the same bytes. Useful for
diff-based review and CI reproducibility.
"""

import os
import zipfile
from dataclasses import dataclass
from io import BytesIO

from aem.package_generator.filevault import node_to_content_xml

# Filename-extension -> MIME type. FileVault stores this on the
# rendition's nt:resource; AEM's DAM lists + Sites author UI both read
# it from there when rendering assets.
_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

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


_SLING_FOLDER_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<jcr:root xmlns:sling="http://sling.apache.org/jcr/sling/1.0"'
    ' xmlns:jcr="http://www.jcp.org/jcr/1.0"\n'
    '    jcr:primaryType="sling:Folder"/>\n'
)


def _cf_folder_entries(cf_root: str, cf_path: str) -> dict[str, str]:
    """A sling:Folder descriptor for every hierarchy level between the CF root
    and the fragment, so an install into a fresh tree creates real folders
    rather than leaving FileVault to guess a node type."""
    relative = cf_path[len(cf_root):].strip("/")
    segments = relative.split("/")[:-1]   # drop the fragment itself
    entries: dict[str, str] = {}
    current = cf_root.rstrip("/")
    for segment in segments:
        current = f"{current}/{segment}"
        entries[f"jcr_root{current}/.content.xml"] = _SLING_FOLDER_XML
    return entries


def build_batch_package(trees: list[dict], meta: PackageMeta) -> bytes:
    """Multi-page variant of build_package: one .zip covers N pages, the
    filter.xml lists one root per page so a single install writes them
    all without clobbering unrelated siblings.

    When a tree carries `_assets` (Phase 2B), the packager emits a
    dam:Asset `.content.xml` + raw-binary pair for each asset and adds
    its DAM path to the filter list too. When a tree carries `_faq_cfs`
    (Task 6), the packager emits a `.content.xml` per fragment, a
    sling:Folder `.content.xml` for each DAM hierarchy level between
    `_faq_cf_root` and the fragment, and adds each fragment path to the
    filter list. None of `_assets` / `_faq_cfs` / `_faq_cf_root` /
    `path` are read by `node_to_content_xml`, so they never leak into
    the page XML as unknown JCR properties.
    """
    all_asset_paths: list[str] = []
    all_cf_paths: list[str] = []
    entries: dict[str, str | bytes] = {
        "META-INF/vault/properties.xml": _properties_xml(meta),
    }
    for tree in trees:
        # Read `_assets` without mutating the caller's tree -- repeat
        # builds must return byte-identical output, so a second call
        # after we popped the key would silently emit a different zip.
        # `node_to_content_xml` ignores non-standard top-level keys, so
        # `_assets` sitting on the dict does NOT leak into the XML.
        assets = tree.get("_assets") if isinstance(tree, dict) else None
        entries[f"jcr_root{tree['path']}/.content.xml"] = node_to_content_xml(tree)
        if assets:
            for asset in assets:
                dam_path = asset["dam_path"]
                all_asset_paths.append(dam_path)
                entries[f"jcr_root{dam_path}/.content.xml"] = _dam_asset_xml(asset["filename"])
                entries[f"jcr_root{dam_path}/_jcr_content/renditions/original"] = asset["bytes"]

        cf_root = tree.get("_faq_cf_root") or ""
        for cf in (tree.get("_faq_cfs") or []):
            cf_path = cf["path"]
            all_cf_paths.append(cf_path)
            entries[f"jcr_root{cf_path}/.content.xml"] = node_to_content_xml(cf)
            if cf_root:
                entries.update(_cf_folder_entries(cf_root, cf_path))

    # Filter roots emit in a stable order so the built package stays
    # byte-identical for identical inputs.
    filter_roots = [t["path"] for t in trees] + all_asset_paths + all_cf_paths
    entries["META-INF/vault/filter.xml"] = _multi_filter_xml(filter_roots)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(entries):
            info = zipfile.ZipInfo(path, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, entries[path])
    return buf.getvalue()


def _dam_asset_xml(filename: str) -> str:
    """FileVault `.content.xml` describing a dam:Asset node whose only
    rendition is `original`, an nt:file whose jcr:content (nt:resource)
    carries the mime type. FileVault matches the sibling
    `_jcr_content/renditions/original` binary entry to that nt:resource
    at install time and populates jcr:data with the bytes."""
    ext = os.path.splitext(filename)[1].lower()
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<jcr:root xmlns:jcr="http://www.jcp.org/jcr/1.0"'
        ' xmlns:dam="http://www.day.com/dam/1.0"'
        ' xmlns:nt="http://www.jcp.org/jcr/nt/1.0"\n'
        '    jcr:primaryType="dam:Asset">\n'
        '    <jcr:content jcr:primaryType="dam:AssetContent">\n'
        '        <renditions jcr:primaryType="nt:folder">\n'
        '            <original jcr:primaryType="nt:file">\n'
        f'                <jcr:content jcr:primaryType="nt:resource" jcr:mimeType="{mime}"/>\n'
        '            </original>\n'
        '        </renditions>\n'
        '    </jcr:content>\n'
        '</jcr:root>\n'
    )


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
