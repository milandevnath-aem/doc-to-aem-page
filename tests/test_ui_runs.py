"""Tests for ui/runs.py -- SQLite-backed run history."""

import time
from ui.runs import get_run, list_runs, save_run
from ui.service import RunResult


def test_save_and_get_run(tmp_path):
    db_path = tmp_path / "test_runs.db"
    result = RunResult(status="passed", filename="test.docx", issues=[], ncm={"page": {"properties": {}}})

    run_id = save_run(result, db_path=db_path)
    record = get_run(run_id, db_path=db_path)

    assert record is not None
    assert record.filename == "test.docx"
    assert record.status == "passed"
    assert record.issues == []
    assert record.ncm == {"page": {"properties": {}}}


def test_get_run_returns_none_for_unknown_id(tmp_path):
    db_path = tmp_path / "test_runs.db"
    list_runs(db_path=db_path)  # touch DB so the table exists before querying

    assert get_run("does-not-exist", db_path=db_path) is None


def test_save_run_with_no_ncm_stores_none(tmp_path):
    db_path = tmp_path / "test_runs.db"
    result = RunResult(
        status="failed", filename="broken.docx",
        issues=[{"severity": "error", "code": "x", "message": "m", "paragraph_index": 0, "snippet": "", "context": ""}],
        ncm=None,
    )

    run_id = save_run(result, db_path=db_path)
    record = get_run(run_id, db_path=db_path)

    assert record.ncm is None
    assert record.issues == [{"severity": "error", "code": "x", "message": "m", "paragraph_index": 0, "snippet": "", "context": ""}]


def test_list_runs_orders_newest_first(tmp_path):
    db_path = tmp_path / "test_runs.db"
    r1 = RunResult(status="passed", filename="first.docx", issues=[], ncm=None)
    r2 = RunResult(status="failed", filename="second.docx", issues=[], ncm=None)

    save_run(r1, db_path=db_path)
    time.sleep(0.01)  # Ensure different timestamps
    save_run(r2, db_path=db_path)

    records = list_runs(db_path=db_path)

    assert len(records) == 2
    assert records[0].filename == "second.docx"
    assert records[1].filename == "first.docx"
