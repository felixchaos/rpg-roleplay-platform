from __future__ import annotations

import atexit
import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


DEFAULT_DATABASE_URL = "postgresql:///rpg_platform"
_pool: ConnectionPool | None = None


def database_url() -> str:
    return (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("RPG_DATABASE_URL")
        or DEFAULT_DATABASE_URL
    )


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as db:
        yield db


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=database_url(),
            min_size=int(os.environ.get("RPG_DB_POOL_MIN", "1")),
            max_size=int(os.environ.get("RPG_DB_POOL_MAX", "10")),
            timeout=float(os.environ.get("RPG_DB_POOL_TIMEOUT", "8")),
            kwargs={"row_factory": dict_row},
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


atexit.register(close_pool)
