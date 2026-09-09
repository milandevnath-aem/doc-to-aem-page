"""FileVault package builder: node tree -> installable .zip in bytes.

Tests unzip the produced bytes in-memory and assert the file list + a
few key pieces of content. Same shape AEM's Package Manager consumes.
"""

import zipfile
from io import BytesIO

from aem.package_generator.package import PackageMeta, build_package


def _tree():
    return {
        "name": "the-page",
        "path": "/content/site/en/articles/the-page",
        "primary_type": "cq:Page",
        "properties": {},
        "children": [{
            "name": "jcr:content",
            "primary_type": "cq:PageContent",
            "properties": {
                "sling:resourceType": "site/components/page",
                "jcr:title": "The Page",
            },
            "children": [],
        }],
    }


def _read_zip(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(data), "r")


def _meta(**overrides):
    defaults = dict(
        group="iffcotokio",
        name="cashless-vs-reimbursement",
        version="1.0.0",
        description="Generated from cashless.docx",
    )
    defaults.update(overrides)
    return PackageMeta(**defaults)


def test_package_has_expected_file_layout():
    data = build_package(_tree(), _meta())
    with _read_zip(data) as zf:
        names = set(zf.namelist())
    assert "META-INF/vault/filter.xml" in names
    assert "META-INF/vault/properties.xml" in names
    assert "jcr_root/content/site/en/articles/the-page/.content.xml" in names


def test_filter_xml_targets_the_page_path():
    data = build_package(_tree(), _meta())
    with _read_zip(data) as zf:
        filter_xml = zf.read("META-INF/vault/filter.xml").decode("utf-8")
    # filter must scope the package to the page path -- otherwise AEM would
    # potentially clobber sibling pages under the same content root
    assert '<filter root="/content/site/en/articles/the-page"' in filter_xml


def test_properties_xml_carries_group_name_version_description():
    data = build_package(_tree(), _meta(description="Generated from foo.docx"))
    with _read_zip(data) as zf:
        props = zf.read("META-INF/vault/properties.xml").decode("utf-8")
    assert "<entry key=\"group\">iffcotokio</entry>" in props
    assert "<entry key=\"name\">cashless-vs-reimbursement</entry>" in props
    assert "<entry key=\"version\">1.0.0</entry>" in props
    assert "<entry key=\"description\">Generated from foo.docx</entry>" in props


def test_content_xml_is_the_filevault_serialization_of_the_tree():
    data = build_package(_tree(), _meta())
    with _read_zip(data) as zf:
        content = zf.read(
            "jcr_root/content/site/en/articles/the-page/.content.xml"
        ).decode("utf-8")
    assert content.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert 'jcr:primaryType="cq:Page"' in content
    assert '<jcr:content' in content
    assert 'jcr:title="The Page"' in content


def test_package_is_deterministic_across_builds():
    """Same input -> byte-identical output. Enables diff-based review of
    generated packages and reproducible builds in CI."""
    meta = _meta()
    a = build_package(_tree(), meta)
    b = build_package(_tree(), meta)
    assert a == b


def test_build_batch_package_covers_multiple_pages():
    """One .zip, N pages, multi-root filter.xml so a single install writes
    every page."""
    from aem.package_generator.package import build_batch_package

    tree_a = {
        "name": "one", "path": "/content/site/articles/one",
        "primary_type": "cq:Page", "properties": {},
        "children": [{"name": "jcr:content", "primary_type": "cq:PageContent",
                      "properties": {"jcr:title": "One"}, "children": []}],
    }
    tree_b = {
        "name": "two", "path": "/content/site/articles/two",
        "primary_type": "cq:Page", "properties": {},
        "children": [{"name": "jcr:content", "primary_type": "cq:PageContent",
                      "properties": {"jcr:title": "Two"}, "children": []}],
    }
    data = build_batch_package([tree_a, tree_b], _meta())

    with _read_zip(data) as zf:
        names = set(zf.namelist())
        assert "META-INF/vault/filter.xml" in names
        assert "jcr_root/content/site/articles/one/.content.xml" in names
        assert "jcr_root/content/site/articles/two/.content.xml" in names
        filter_xml = zf.read("META-INF/vault/filter.xml").decode()
    assert '<filter root="/content/site/articles/one"/>' in filter_xml
    assert '<filter root="/content/site/articles/two"/>' in filter_xml


def test_batch_package_is_deterministic():
    from aem.package_generator.package import build_batch_package
    trees = [
        {"name": "x", "path": "/content/x", "primary_type": "cq:Page",
         "properties": {}, "children": []},
    ]
    m = _meta()
    assert build_batch_package(trees, m) == build_batch_package(trees, m)


def test_package_meta_defaults_to_sensible_values():
    """Bare PackageMeta with only name works -- version and description
    fall back to defaults."""
    meta = PackageMeta(name="minimal")
    data = build_package(_tree(), meta)
    with _read_zip(data) as zf:
        props = zf.read("META-INF/vault/properties.xml").decode("utf-8")
    assert "<entry key=\"group\">iffcotokio-content-automation</entry>" in props
    assert "<entry key=\"version\">1.0.0</entry>" in props


# ---- Phase 2B: DAM asset packaging ----

def _tree_with_asset(dam_path: str, filename: str, blob: bytes) -> dict:
    return {
        "name": "the-page", "path": "/content/site/articles/the-page",
        "primary_type": "cq:Page", "properties": {},
        "children": [{"name": "jcr:content", "primary_type": "cq:PageContent",
                      "properties": {"jcr:title": "T"}, "children": []}],
        "_assets": [{"dam_path": dam_path, "filename": filename, "bytes": blob}],
    }


def test_batch_package_includes_dam_asset_content_xml_and_binary():
    """A tree carrying `_assets` -> the package layers in a dam:Asset
    `.content.xml` AND the raw binary at the FileVault-expected path
    (`_jcr_content/renditions/original`)."""
    from aem.package_generator.package import build_batch_package

    tree = _tree_with_asset(
        dam_path="/content/dam/site/articles/car-insurance.jpg",
        filename="car-insurance.jpg",
        blob=b"\x89PNG-fake-bytes",
    )
    data = build_batch_package([tree], _meta())

    with _read_zip(data) as zf:
        names = set(zf.namelist())
        # dam:Asset descriptor
        asset_xml_path = (
            "jcr_root/content/dam/site/articles/car-insurance.jpg/.content.xml"
        )
        assert asset_xml_path in names
        asset_xml = zf.read(asset_xml_path).decode("utf-8")
        assert 'jcr:primaryType="dam:Asset"' in asset_xml
        assert 'jcr:primaryType="dam:AssetContent"' in asset_xml
        assert 'jcr:mimeType="image/jpeg"' in asset_xml
        # raw binary picked up by FileVault at install time
        binary_path = (
            "jcr_root/content/dam/site/articles/car-insurance.jpg"
            "/_jcr_content/renditions/original"
        )
        assert binary_path in names
        assert zf.read(binary_path) == b"\x89PNG-fake-bytes"


def test_batch_package_filter_xml_adds_root_per_asset():
    """Every packaged asset gets its own <filter root=...> so the install
    covers it without accidentally sweeping in unrelated DAM siblings."""
    from aem.package_generator.package import build_batch_package

    tree = _tree_with_asset(
        dam_path="/content/dam/site/articles/img.jpg",
        filename="img.jpg", blob=b"x",
    )
    data = build_batch_package([tree], _meta())

    with _read_zip(data) as zf:
        filter_xml = zf.read("META-INF/vault/filter.xml").decode()
    assert '<filter root="/content/site/articles/the-page"/>' in filter_xml
    assert '<filter root="/content/dam/site/articles/img.jpg"/>' in filter_xml


def test_batch_package_asset_mimetype_by_extension():
    """MIME type is derived from the filename extension, case-insensitive."""
    from aem.package_generator.package import build_batch_package

    cases = {
        "hero.PNG": "image/png",
        "banner.jpg": "image/jpeg",
        "banner.jpeg": "image/jpeg",
        "logo.svg": "image/svg+xml",
        "anim.gif": "image/gif",
        "next.webp": "image/webp",
    }
    for filename, mime in cases.items():
        tree = _tree_with_asset(
            dam_path=f"/content/dam/site/{filename}",
            filename=filename, blob=b"x",
        )
        data = build_batch_package([tree], _meta())
        with _read_zip(data) as zf:
            xml = zf.read(
                f"jcr_root/content/dam/site/{filename}/.content.xml"
            ).decode()
        assert f'jcr:mimeType="{mime}"' in xml, filename


def test_batch_package_strips_internal_assets_key_from_page_xml():
    """`_assets` is an internal packager signal -- it MUST NOT appear
    in the page's serialized .content.xml (would leak an unknown JCR
    property and fail install)."""
    from aem.package_generator.package import build_batch_package

    tree = _tree_with_asset(
        dam_path="/content/dam/site/img.jpg",
        filename="img.jpg", blob=b"x",
    )
    data = build_batch_package([tree], _meta())
    with _read_zip(data) as zf:
        page_xml = zf.read(
            "jcr_root/content/site/articles/the-page/.content.xml"
        ).decode()
    assert "_assets" not in page_xml


def test_batch_package_no_assets_key_is_unchanged():
    """Trees without `_assets` produce the same package they always did
    -- no DAM entries, filter xml unchanged."""
    from aem.package_generator.package import build_batch_package

    tree = {
        "name": "p", "path": "/content/site/p",
        "primary_type": "cq:Page", "properties": {},
        "children": [{"name": "jcr:content", "primary_type": "cq:PageContent",
                      "properties": {}, "children": []}],
    }
    data = build_batch_package([tree], _meta())
    with _read_zip(data) as zf:
        names = set(zf.namelist())
    assert not any("dam" in n for n in names)


def test_batch_package_with_assets_is_deterministic():
    """Same tree+asset -> byte-identical package. Enables diff-based
    review and reproducible CI builds when assets are involved."""
    from aem.package_generator.package import build_batch_package

    tree = _tree_with_asset(
        dam_path="/content/dam/site/img.jpg",
        filename="img.jpg", blob=b"deterministic-payload",
    )
    m = _meta()
    assert build_batch_package([tree], m) == build_batch_package([tree], m)
