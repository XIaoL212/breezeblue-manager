"""Storage layer for BreezeBlue Manager.

SQLite stays the default so the app runs anywhere. When the platform injects
``DATABASE_URL`` (a managed Postgres add-on) the exact same SQL runs against
Postgres instead, so account data survives redeploys and container restarts.
"""
from __future__ import annotations

import os
import sqlite3

_SQLITE_PATH = ""
_DATABASE_URL = (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or "").strip()
_IS_POSTGRES = bool(_DATABASE_URL)


def init(sqlite_path: str) -> None:
    """Point the SQLite side at ``sqlite_path``; Postgres wins when configured."""
    global _SQLITE_PATH, _IS_POSTGRES
    _SQLITE_PATH = sqlite_path
    _IS_POSTGRES = bool(_DATABASE_URL)


def is_postgres() -> bool:
    return _IS_POSTGRES


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
        # sqlite3.Connection.commit/rollback already ran here before close().
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self._raw.close()
        return False


def db():
    if _IS_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row

        return _Conn(psycopg.connect(_DATABASE_URL, row_factory=dict_row), postgres=True)
    conn = sqlite3.connect(_SQLITE_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return _Conn(conn, postgres=False)
