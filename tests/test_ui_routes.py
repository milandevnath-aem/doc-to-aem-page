"""Tests for ui/app.py routes."""

import io

from fastapi.testclient import TestClient

from ui import app as app_module
from ui.app import app


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


def test_run_route_rejects_non_docx_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "UPLOADS_DIR", tmp_path)
    client = TestClient(app_module.app)

    response = client.post(
        "/run",
        files={"docx_file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )

    assert response.status_code == 400
