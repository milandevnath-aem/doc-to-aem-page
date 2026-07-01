"""Shared pytest fixtures for the test suite."""

import pytest

from ui import runs as runs_module


@pytest.fixture(autouse=True)
def isolated_runs_db(tmp_path, monkeypatch):
    """Point ui.runs.DB_PATH at a throwaway file for every test, so route
    tests never read or write the real ui/runs.db."""
    monkeypatch.setattr(runs_module, "DB_PATH", tmp_path / "test_runs.db")
