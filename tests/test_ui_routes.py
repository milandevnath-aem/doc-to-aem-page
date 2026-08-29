"""Tests for ui/app.py routes."""

import io

from fastapi.testclient import TestClient

from ui import app as app_module
from ui import runs as runs_module
from ui.app import app
from ui.service import RunResult


def test_home_page_renders_upload_form():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert 'name="docx_file"' in response.text
    assert 'action="/run"' in response.text
    # authoring-format selector: auto is the default, all three formats offered
    assert 'name="format"' in response.text
    assert 'value=""' in response.text          # auto
    assert 'value="v1v2"' in response.text
    assert 'value="v3"' in response.text


def test_run_route_forwards_format_override(tmp_path, monkeypatch):
    """Explicit format on the form must reach the parser and end up on the
    result page's detected-format badge."""
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)

    import docx as docx_lib
    document = docx_lib.Document()
    document.add_paragraph("[metadata]")
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "title", "T"
    table.cell(2, 0).text, table.cell(2, 1).text = "description", "D"
    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    client = TestClient(app_module.app)
    response = client.post(
        "/run",
        files={"docx_file": ("sample.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"format": "v1v2"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    result_response = client.get(response.headers["location"])
    assert "Detected format" in result_response.text
    assert "v1v2" in result_response.text


def test_run_route_uploads_and_redirects_to_result(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)

    import docx as docx_lib
    document = docx_lib.Document()
    document.add_paragraph("[metadata]")
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "title", "Test Page"
    table.cell(2, 0).text, table.cell(2, 1).text = "description", "A test page"
    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    client = TestClient(app_module.app)
    response = client.post(
        "/run",
        files={"docx_file": ("sample.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/")


def test_full_upload_result_history_flow(tmp_path, monkeypatch):
    """End-to-end: upload a docx, follow the redirect to the result page,
    confirm parsed content renders, then confirm the run shows up on /history.
    """
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)

    import docx as docx_lib
    document = docx_lib.Document()
    document.add_paragraph("[metadata]")
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "title", "Test Page"
    table.cell(2, 0).text, table.cell(2, 1).text = "description", "A test page"
    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    client = TestClient(app_module.app)
    response = client.post(
        "/run",
        files={"docx_file": ("e2e-flow.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/runs/")

    result_response = client.get(location)

    assert result_response.status_code == 200
    assert "e2e-flow.docx" in result_response.text
    assert "Test Page" in result_response.text

    history_response = client.get("/history")

    assert history_response.status_code == 200
    assert "e2e-flow.docx" in history_response.text


def test_run_route_rejects_non_docx_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)
    client = TestClient(app_module.app)

    response = client.post(
        "/run",
        files={"docx_file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )

    assert response.status_code == 400


def test_run_route_prevents_path_traversal(tmp_path, monkeypatch):
    """Verify that malicious filenames with path traversal segments are sanitized."""
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)

    import docx as docx_lib
    document = docx_lib.Document()
    document.add_paragraph("[metadata]")
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "title", "Test Page"
    table.cell(2, 0).text, table.cell(2, 1).text = "description", "A test page"
    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    client = TestClient(app_module.app)
    # Upload with a crafted filename containing path traversal segments
    response = client.post(
        "/run",
        files={"docx_file": ("../../evil.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    # Verify that the file was written only inside tmp_path, not in parent directories
    uploaded_docx_files = list(tmp_path.glob("*.docx"))
    assert len(uploaded_docx_files) == 1, f"Expected 1 .docx file in UPLOADS_DIR, found {len(uploaded_docx_files)}"
    # Verify the file has the sanitized name (evil.docx, not ../../evil.docx)
    assert "evil.docx" in uploaded_docx_files[0].name
    # Verify no files leaked to parent directories
    parent = tmp_path.parent
    parent_files = list(parent.glob("evil.docx"))
    assert len(parent_files) == 0, f"Path traversal vulnerability: evil.docx found in {parent}"


def test_result_page_shows_status_and_issues():
    result = RunResult(
        status="failed",
        filename="broken.docx",
        issues=[{
            "severity": "error", "code": "missing-required-metadata",
            "message": "Metadata table is missing required field(s): ['description'].",
            "paragraph_index": 0, "snippet": "[metadata]", "context": "",
        }],
        ncm=None,
    )
    run_id = runs_module.save_run(result)

    client = TestClient(app_module.app)
    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "missing-required-metadata" in response.text
    assert "FAILED" in response.text


def test_result_page_renders_rte_html_preview():
    result = RunResult(
        status="passed",
        filename="clean.docx",
        issues=[],
        ncm={
            "page": {"properties": {"title": "T"}},
            "blocks": [{
                "name": "hero-banner", "variant": None,
                "fields": {"title": "Hero", "description": "<p>Hello <strong>world</strong></p>"},
                "items": [], "table": None,
            }],
        },
    )
    run_id = runs_module.save_run(result)

    client = TestClient(app_module.app)
    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "<strong>world</strong>" in response.text  # rendered raw HTML, not escaped


def test_result_page_shows_full_ncm_json():
    result = RunResult(
        status="passed",
        filename="clean.docx",
        issues=[],
        ncm={
            "page": {"properties": {"title": "T"}},
            "blocks": [{
                "name": "hero-banner", "variant": None,
                "fields": {"title": "Hero", "description": "<p>Hello</p>"},
                "items": [], "table": None,
            }],
        },
    )
    run_id = runs_module.save_run(result)

    client = TestClient(app_module.app)
    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    # the raw master JSON (used for content-to-AEM mapping) must be present
    # in full, not just the field-by-field breakdown above it
    assert '"name": "hero-banner"' in response.text
    assert '"description": "\\u003cp\\u003eHello\\u003c/p\\u003e"' in response.text


def test_result_page_renders_items_with_image_thumbnail():
    result = RunResult(
        status="passed",
        filename="v2.docx",
        issues=[],
        ncm={
            "page": {"properties": {"title": "T"}},
            "blocks": [{
                "name": "featurecards", "variant": None,
                "fields": {"title": "Benefits"},
                "items": [{
                    "id": None, "implicit": True,
                    "fields": {
                        "title": "Financial Protection",
                        "description": "<p>Card body</p>",
                        "image": {"source": "embedded", "name": "image-1.png",
                                  "path": "ui/static/assets/run-x/image-1.png"},
                    },
                    "headings": [],
                }],
                "table": None,
            }],
        },
    )
    run_id = runs_module.save_run(result)

    client = TestClient(app_module.app)
    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert "Financial Protection" in response.text
    assert "<p>Card body</p>" in response.text
    assert '<img class="asset-thumb" src="/static/assets/run-x/image-1.png"' in response.text


def test_run_route_extracts_embedded_images_to_assets(tmp_path, monkeypatch):
    import base64

    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(app_module, "ASSETS_DIR", tmp_path / "assets")
    (tmp_path / "uploads").mkdir()

    png_1px = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    img_file = tmp_path / "pic.png"
    img_file.write_bytes(png_1px)

    import docx as docx_lib
    document = docx_lib.Document()
    document.add_paragraph("[metadata]")
    table = document.add_table(rows=3, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Property", "Value"
    table.cell(1, 0).text, table.cell(1, 1).text = "title", "Test Page"
    table.cell(2, 0).text, table.cell(2, 1).text = "description", "A test page"
    document.add_paragraph("[component: featurecards]")
    document.add_paragraph("Cards Title")
    document.add_picture(str(img_file))
    document.add_paragraph("Pasted Card")
    buf = io.BytesIO()
    document.save(buf)
    buf.seek(0)

    client = TestClient(app_module.app)
    response = client.post(
        "/run",
        files={"docx_file": ("with-image.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    extracted = list((tmp_path / "assets").rglob("image-1.png"))
    assert len(extracted) == 1

    result_response = client.get(response.headers["location"])
    assert "Pasted Card" in result_response.text
    assert "image-1.png" in result_response.text


def test_result_page_has_copy_buttons_per_section():
    result = RunResult(
        status="passed",
        filename="v3.docx",
        issues=[],
        ncm={
            "format": "v3",
            "page": {"properties": {"title": "T"}},
            "blocks": [{
                "name": "faq", "variant": None,
                "fields": {"title": "FAQS"},
                "items": [], "table": None,
            }],
        },
    )
    run_id = runs_module.save_run(result)

    client = TestClient(app_module.app)
    response = client.get(f"/runs/{run_id}")

    text = response.text
    # 3 copy buttons: Full NCM, Page Properties, one per block
    assert text.count('class="copy-btn"') == 3
    # each carries the section's JSON as data-copy. tojson is safe for
    # <script> not attributes -- single-quoted attribute + tojson's '
    # escape for apostrophes keeps the payload intact
    assert "data-copy='" in text
    # the block payload must survive intact (double-quoted JSON keys visible).
    # Jinja's tojson sorts keys alphabetically so 'fields' comes first.
    assert 'data-copy=\'{"fields":' in text
    assert '"name": "faq"' in text
    assert 'navigator.clipboard' in text            # handler script present


def test_result_page_404_for_unknown_run():
    client = TestClient(app_module.app)

    response = client.get("/runs/does-not-exist")

    assert response.status_code == 404


def test_history_page_lists_past_runs():
    runs_module.save_run(RunResult(status="passed", filename="a.docx", issues=[], ncm=None))
    runs_module.save_run(RunResult(status="failed", filename="b.docx", issues=[{
        "severity": "error", "code": "x", "message": "m", "paragraph_index": 0, "snippet": "", "context": "",
    }], ncm=None))

    client = TestClient(app_module.app)
    response = client.get("/history")

    assert response.status_code == 200
    assert "a.docx" in response.text
    assert "b.docx" in response.text


def test_history_page_empty_state():
    client = TestClient(app_module.app)

    response = client.get("/history")

    assert response.status_code == 200
    assert "No runs yet" in response.text
