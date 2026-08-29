"""SQLite-backed run history for the pipeline operator UI.

DB_PATH is read fresh inside each function (not bound as a frozen default
parameter value) so tests can monkeypatch `ui.runs.DB_PATH` and have it
take effect -- a bound default (`db_path: Path = DB_PATH`) would capture
the value at import time and ignore later monkeypatching.
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ui.service import RunResult

DB_PATH = Path(__file__).parent / "runs.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    ncm_json TEXT,
    aem_tree_json TEXT
)
"""

# Migration for pre-aem_tree DBs. SQLite can't add-column-if-not-exists,
# so we probe and ADD COLUMN only when missing. Cheap on every connect.
_MIGRATION = "ALTER TABLE runs ADD COLUMN aem_tree_json TEXT"


@dataclass
class RunRecord:
    id: str
    filename: str
    created_at: str
    status: str
    issues: list
    ncm: dict | None
    aem_tree: dict | None = None


def _resolve_db_path(db_path: Path | None) -> Path:
    return db_path if db_path is not None else DB_PATH


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "aem_tree_json" not in cols:
        conn.execute(_MIGRATION)
    return conn


def save_run(result: RunResult, db_path: Path | None = None) -> str:
    db_path = _resolve_db_path(db_path)
    run_id = str(uuid.uuid4())
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO runs (id, filename, created_at, status, issues_json, ncm_json, aem_tree_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                result.filename,
                datetime.now(timezone.utc).isoformat(),
                result.status,
                json.dumps(result.issues),
                json.dumps(result.ncm) if result.ncm is not None else None,
                json.dumps(result.aem_tree) if result.aem_tree is not None else None,
            ),
        )
    return run_id


def _row_to_record(row: tuple) -> RunRecord:
    return RunRecord(
        id=row[0], filename=row[1], created_at=row[2], status=row[3],
        issues=json.loads(row[4]),
        ncm=json.loads(row[5]) if row[5] is not None else None,
        aem_tree=json.loads(row[6]) if row[6] is not None else None,
    )


_SELECT_COLS = "id, filename, created_at, status, issues_json, ncm_json, aem_tree_json"


def get_run(run_id: str, db_path: Path | None = None) -> RunRecord | None:
    db_path = _resolve_db_path(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    return _row_to_record(row) if row is not None else None


def list_runs(db_path: Path | None = None) -> list[RunRecord]:
    db_path = _resolve_db_path(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM runs ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
    return [_row_to_record(row) for row in rows]
