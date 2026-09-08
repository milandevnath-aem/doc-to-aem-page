"""Batch mode -- one folder, many docs, one pipeline run per docx.

Each subfolder = one page. The subfolder NAME does not leak into the JCR
path -- it is only a container for a page's docx + its assets. Page path
comes from pageNameStrategy applied to doc contents, same as single-doc.
"""

from pathlib import Path

import docx

from aem_client.config import PageTypeConfig
from ui.batch_service import run_batch


def _sample_config() -> PageTypeConfig:
    return PageTypeConfig(
        type="article",
        template_path="/conf/x",
        content_root="/content/site/articles",
        dam_root="/content/dam/site/articles",
        page_name_strategy="slug_from_meta_title",
        component_mappings={"page-description": "site/components/text"},
    )


def _write_v3_doc(path: Path, title: str, body: str = "Body content.") -> None:
    document = docx.Document()
    p = document.add_paragraph()
    p.add_run(title).bold = True
    document.add_paragraph(f"Meta Title: {title}")
    document.add_paragraph("Meta Description: Some description.")
    document.add_paragraph("Meta keywords: kw")
    document.add_paragraph(body)
    document.save(str(path))


def test_batch_runs_pipeline_per_file_and_returns_ordered_entries(tmp_path):
    for i, (folder, title) in enumerate([
        ("Article1", "First Article"),
        ("Article2", "Second Article"),
        ("Article3", "Third Article"),
    ]):
        d = tmp_path / folder
        d.mkdir()
        _write_v3_doc(d / f"doc{i}.docx", title)

    files = [(str(f.relative_to(tmp_path)).replace("\\", "/"), f.read_bytes())
             for f in sorted(tmp_path.rglob("*.docx"))]

    result = run_batch(files, page_type_config=_sample_config())

    assert len(result.entries) == 3
    folders = [e.folder for e in result.entries]
    assert folders == ["Article1", "Article2", "Article3"]
    assert all(e.status == "passed" for e in result.entries)
    # page path derived from meta title, subfolder name absent
    assert result.entries[0].page_path == "/content/site/articles/first-article"
    assert result.entries[1].page_path == "/content/site/articles/second-article"
    assert "article1" not in result.entries[0].page_path.lower()


def test_batch_entry_carries_full_aem_tree(tmp_path):
    (tmp_path / "Article1").mkdir()
    _write_v3_doc(tmp_path / "Article1" / "a.docx", "The Only Article")

    files = [(str(f.relative_to(tmp_path)).replace("\\", "/"), f.read_bytes())
             for f in tmp_path.rglob("*.docx")]

    result = run_batch(files, page_type_config=_sample_config())

    assert result.entries[0].aem_tree is not None
    assert result.entries[0].aem_tree["primary_type"] == "cq:Page"


def test_batch_flags_page_path_collisions(tmp_path):
    """Two docs with identical meta title -> same JCR path. First wins,
    second recorded as a collision -- no silent overwrite."""
    for folder in ["A1", "A2"]:
        d = tmp_path / folder
        d.mkdir()
        _write_v3_doc(d / "d.docx", "Same Title")

    files = [(str(f.relative_to(tmp_path)).replace("\\", "/"), f.read_bytes())
             for f in sorted(tmp_path.rglob("*.docx"))]

    result = run_batch(files, page_type_config=_sample_config())

    assert result.entries[0].status == "passed"
    assert result.entries[1].status == "collision"
    assert "same-title" in result.entries[1].error.lower()
    assert result.entries[1].aem_tree is None


def test_batch_survives_a_failing_doc_and_continues(tmp_path):
    (tmp_path / "A1").mkdir()
    _write_v3_doc(tmp_path / "A1" / "ok.docx", "Good One")
    # a broken file (not a real docx)
    (tmp_path / "A2").mkdir()
    (tmp_path / "A2" / "broken.docx").write_bytes(b"not a real docx")
    (tmp_path / "A3").mkdir()
    _write_v3_doc(tmp_path / "A3" / "also-ok.docx", "Good Two")

    files = [(str(f.relative_to(tmp_path)).replace("\\", "/"), f.read_bytes())
             for f in sorted(tmp_path.rglob("*.docx"))]

    result = run_batch(files, page_type_config=_sample_config())

    assert [e.status for e in result.entries] == ["passed", "failed", "passed"]
    # failed entry keeps its identity so operator sees which one broke
    assert result.entries[1].folder == "A2"
    assert result.entries[1].filename == "broken.docx"


def _write_v3_doc_no_seed(path: Path) -> None:
    """v3 doc that validates fine (has Meta Title + Meta Description) but
    whose Meta Title slugifies to only non-alphanumeric characters, so
    the mapper's pageNameStrategy = slug_from_meta_title falls back to
    the UNRESOLVED_SLUG placeholder."""
    document = docx.Document()
    document.add_paragraph().add_run("H1").bold = True
    # Meta Title contains only punctuation -> slugify collapses to empty
    document.add_paragraph("Meta Title: ---")
    document.add_paragraph("Meta Description: desc")
    document.add_paragraph("Meta keywords: kw")
    document.add_paragraph("Body.")
    document.save(str(path))


def test_batch_flags_unresolvable_page_names_and_drops_them(tmp_path):
    """A doc that validates OK but whose title slugifies to the
    UNRESOLVED_SLUG placeholder ('page'). Batch mode marks it unresolved,
    drops the aem_tree so it never lands in the package, and keeps the
    row in the log so the operator sees which doc to fix."""
    (tmp_path / "A1").mkdir()
    _write_v3_doc_no_seed(tmp_path / "A1" / "nameless.docx")

    files = [(str(f.relative_to(tmp_path)).replace("\\", "/"), f.read_bytes())
             for f in tmp_path.rglob("*.docx")]

    result = run_batch(files, page_type_config=_sample_config())

    assert result.entries[0].status == "unresolved"
    # dropped from the package -- no aem_tree available for the batch builder
    assert result.entries[0].aem_tree is None
    assert "could not derive" in result.entries[0].error.lower()


def test_batch_unresolved_pages_are_excluded_from_slug_collision_set(tmp_path):
    """Three docs all slugging to the placeholder must not eat each other
    as 'collisions' -- each is independently unresolved."""
    for folder in ["A1", "A2", "A3"]:
        d = tmp_path / folder
        d.mkdir()
        _write_v3_doc_no_seed(d / "d.docx")

    files = [(str(f.relative_to(tmp_path)).replace("\\", "/"), f.read_bytes())
             for f in sorted(tmp_path.rglob("*.docx"))]

    result = run_batch(files, page_type_config=_sample_config())

    assert [e.status for e in result.entries] == ["unresolved", "unresolved", "unresolved"]


def test_batch_result_carries_top_folder_label(tmp_path):
    (tmp_path / "Articles" / "A1").mkdir(parents=True)
    _write_v3_doc(tmp_path / "Articles" / "A1" / "doc.docx", "One")

    files = [(str(f.relative_to(tmp_path)).replace("\\", "/"), f.read_bytes())
             for f in tmp_path.rglob("*.docx")]

    result = run_batch(files, page_type_config=_sample_config())
    # top folder = first segment before subfolder + filename
    assert result.filename_top == "Articles"
