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
