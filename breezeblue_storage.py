"""Storage layer for BreezeBlue Manager.

SQLite stays the default. The platform injects a ``DATABASE_URL`` pointing at the
managed Postgres add-on, but that add-on is not always actually running, so the
backend is chosen by probing rather than by the mere presence of the variable: an
unreachable Postgres must never stop the app from starting.
"""
from __future__ import annotations

import os
import sqlite3

_DATABASE_URL = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()
PREFERRED = os.environ.get("BREEZEBLUE_DB_BACKEND", "").strip().lower()
backend = "sqlite"
note = "not initialised"
_SQLITE_PATH = ""


def init(sqlite_path: str) -> None:
    """Choose the backend: explicit override, reachable Postgres, else SQLite."""
    global _SQLITE_PATH, backend, note
    _SQLITE_PATH = sqlite_path
    if PREFERRED not in ("", "sqlite", "postgres"):
        print(f"breezeblue-db-backend-ignored: {PREFERRED}", flush=True)
    if PREFERRED == "sqlite":
        backend, note = "sqlite", "forced by BREEZEBLUE_DB_BACKEND"
    elif not _DATABASE_URL:
        backend, note = "sqlite", "no DATABASE_URL injected"
    else:
        try:
            import psycopg

            with psycopg.connect(_DATABASE_URL, connect_timeout=5):
                backend, note = "postgres", "managed Postgres add-on is reachable"
        except Exception as exc:
            backend, note = "sqlite", f"Postgres unreachable, fell back to SQLite: {str(exc)[:180]}"
    print(f"breezeblue-db-backend={backend} ({note})", flush=True)


def is_postgres() -> bool:
    return backend == "postgres"


class Row(dict):
    """Row that mirrors sqlite3.Row: lookup by column name or by position."""

    def __getitem__(self, key):
        if isinstance(key, int) and not isinstance(key, bool):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


_TYPE_MAP = (
    ("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"),
    (" REAL", " DOUBLE PRECISION"),
)


def _to_pg(sql: str) -> str:
    for old, new in _TYPE_MAP:
        sql = sql.replace(old, new)
    return sql.replace("?", "%s")


class _Result:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        row = self._cursor.fetchone()
        return None if row is None else Row(row)

    def fetchall(self):
        return [row if isinstance(row, Row) else Row(row) for row in self._cursor.fetchall()]


class _Conn:
    """Drop-in stand-in for sqlite3.Connection's transaction protocol."""

    def __init__(self, raw, *, postgres: bool):
        self._raw = raw
        self._postgres = postgres

    def execute(self, sql: str, params=()):
        args = tuple(params) if params else ()
        if self._postgres:
            return _Result(self._raw.execute(_to_pg(sql), args or None))
        return _Result(self._raw.execute(sql, args))

    def executescript(self, sql: str) -> None:
        if not self._postgres:
            self._raw.executescript(sql)
            return
        for statement in (part.strip() for part in sql.split(";")):
            if statement:
                self._raw.execute(statement)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # Mirror sqlite3.Connection's own transaction protocol, then release the
        # handle so a redeploy or a restart cannot leak connections.
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self._raw.close()
        return False


def db():
    if backend == "postgres":
        import psycopg
        from psycopg.rows import dict_row

        return _Conn(psycopg.connect(_DATABASE_URL, row_factory=dict_row), postgres=True)
    conn = sqlite3.connect(_SQLITE_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return _Conn(conn, postgres=False)
