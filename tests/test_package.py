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


def test_package_meta_defaults_to_sensible_values():
    """Bare PackageMeta with only name works -- version and description
    fall back to defaults."""
    meta = PackageMeta(name="minimal")
    data = build_package(_tree(), meta)
    with _read_zip(data) as zf:
        props = zf.read("META-INF/vault/properties.xml").decode("utf-8")
    assert "<entry key=\"group\">iffcotokio-content-automation</entry>" in props
    assert "<entry key=\"version\">1.0.0</entry>" in props
