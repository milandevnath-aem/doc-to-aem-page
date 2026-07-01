"""Tests for ui/app.py routes."""

from fastapi.testclient import TestClient

from ui.app import app


def test_home_page_renders_upload_form():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert 'name="docx_file"' in response.text
    assert 'action="/run"' in response.text
