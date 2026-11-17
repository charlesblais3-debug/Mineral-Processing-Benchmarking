"""Create (or upgrade) data/benchmark.db from schema.sql. Safe to re-run."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "benchmark.db"
SCHEMA = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    con = connect(db_path)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    con.commit()
    return con
