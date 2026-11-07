"""SQLAlchemy-backed compatibility layer for SQLite tests and PostgreSQL pilots.

Application SQL is deliberately kept behind this small adapter while the
domain modules are split into repositories. New code should prefer named
queries/repositories rather than adding SQL directly to route handlers.
"""
from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine, Result

from config import SETTINGS


BASE = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("UDYAM_DB", BASE / "orders.db"))


def _database_url() -> str:
    if SETTINGS.database_url:
        url = SETTINGS.database_url
        return "postgresql+psycopg://" + url.removeprefix("postgresql://") if url.startswith("postgresql://") else url
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite+pysqlite:///" + DB_PATH.resolve().as_posix()


DATABASE_URL = _database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")
ENGINE: Engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 10} if IS_SQLITE else {},
)


if IS_SQLITE:
    @event.listens_for(ENGINE, "connect")
    def _sqlite_settings(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()


class CompatRow(Mapping[str, Any]):
    def __init__(self, mapping: Mapping[str, Any]):
        self._mapping = dict(mapping)
        self._keys = list(self._mapping)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._mapping[self._keys[key]]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


class CompatResult:
    def __init__(self, result: Result | None = None, rowcount: int = 0):
        self._result = result
        self.rowcount = result.rowcount if result is not None else rowcount

    @staticmethod
    def _row(row):
        return CompatRow(row._mapping) if row is not None else None

    def fetchone(self):
        return self._row(self._result.fetchone()) if self._result is not None else None

    def fetchall(self):
        return [self._row(row) for row in self._result.fetchall()] if self._result is not None else []

    def __iter__(self):
        if self._result is None:
            return iter(())
        return (self._row(row) for row in self._result)


def _named_sql(sql: str, params) -> tuple[str, dict[str, Any]]:
    if isinstance(params, Mapping):
        return sql, dict(params)
    values = tuple(params or ())
    count = sql.count("?")
    if count != len(values):
        if count == 0 and not values:
            return sql, {}
        raise ValueError(f"SQL parameter mismatch: expected {count}, received {len(values)}")
    parts = sql.split("?")
    rendered = parts[0]
    binds: dict[str, Any] = {}
    for index, value in enumerate(values):
        name = f"p{index}"
        rendered += f":{name}" + parts[index + 1]
        binds[name] = value
    return rendered, binds


class DatabaseConnection:
    def __init__(self):
        self._connection: Connection | None = None
        self._transaction = None

    def __enter__(self):
        self._connection = ENGINE.connect()
        self._transaction = self._connection.begin()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._transaction.commit()
            else:
                self._transaction.rollback()
        finally:
            self._connection.close()
        return False

    def execute(self, sql: str, params=()) -> CompatResult:
        if self._connection is None:
            raise RuntimeError("Database connection is not open")
        if sql.strip().upper().startswith("BEGIN"):
            # Context entry already opened one transaction. PostgreSQL row-level
            # locking is applied in repositories where concurrent mutation matters.
            return CompatResult(rowcount=0)
        rendered, binds = _named_sql(sql, params)
        return CompatResult(self._connection.execute(text(rendered), binds))

    def select_for_update(self, sql: str, params=()) -> CompatResult:
        statement = sql if IS_SQLITE else sql.rstrip().rstrip(";") + " FOR UPDATE"
        return self.execute(statement, params)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)


def connect() -> DatabaseConnection:
    return DatabaseConnection()


def dispose() -> None:
    ENGINE.dispose()
