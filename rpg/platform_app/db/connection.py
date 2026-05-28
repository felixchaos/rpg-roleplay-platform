from __future__ import annotations

import atexit
import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DEFAULT_DATABASE_URL = "postgresql:///rpg_platform"
_pool: ConnectionPool | None = None


def database_url() -> str:
    from core.config import database_url_override as _database_url_override
    return (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or _database_url_override()
        or DEFAULT_DATABASE_URL
    )


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as db:
        yield db


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        from core.config import db_pool_min as _db_pool_min, db_pool_max as _db_pool_max, db_pool_timeout as _db_pool_timeout
        _pool = ConnectionPool(
            conninfo=database_url(),
            min_size=_db_pool_min(),
            max_size=_db_pool_max(),
            timeout=_db_pool_timeout(),
            kwargs={"row_factory": dict_row},
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


atexit.register(close_pool)
