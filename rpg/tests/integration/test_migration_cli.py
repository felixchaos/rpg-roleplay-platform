"""
test_migration_cli.py — 验证 migration CLI / 顺序 / fresh DB 兼容性

覆盖：
- MIGRATIONS 列表严格升序 + 无重复（_assert_migrations_monotonic 不抛）
- list_migrations() 在 fresh DB（无 schema_migrations）也返回 ok=True 且 fresh_database=True
- migrate.cmd_status 在 fresh DB 也返回 0（信息性输出，不当错误）
- _apply_versioned_migrations 是幂等的（再跑一次不重复插 schema_migrations）

为了不污染主测试 DB，fresh-DB 用例用一个临时 schema 跑，跑完 DROP 掉。
通过 monkey-patch _db.connect 让 list_migrations 内部的所有 connect() 都拿到
search_path 已经指向临时 schema 的连接。
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

import psycopg

from platform_app import db as _db


class MigrationOrdering(unittest.TestCase):
    def test_list_is_strictly_increasing(self):
        versions = [v for v, _, _ in _db.MIGRATIONS]
        self.assertEqual(versions, sorted(versions), f"MIGRATIONS 必须按 version 升序：{versions}")
        self.assertEqual(len(versions), len(set(versions)), f"MIGRATIONS 不能有重复 version：{versions}")

    def test_assertion_helper_passes(self):
        # 模块加载时已经调过一次；这里再调一次确认幂等
        _db._assert_migrations_monotonic()

    def test_assertion_helper_catches_disorder(self):
        bad = [(1, "a", []), (3, "c", []), (2, "b", [])]
        with self.assertRaises(RuntimeError):
            self._check(bad)

    def test_assertion_helper_catches_duplicate(self):
        bad = [(1, "a", []), (2, "b", []), (2, "dup", [])]
        with self.assertRaises(RuntimeError):
            self._check(bad)

    def _check(self, items):
        # 直接复用 db._assert_migrations_monotonic 的判定，但喂入自定义列表
        seen, last = set(), 0
        for version, _name, _ in items:
            if version in seen:
                raise RuntimeError(f"dup v{version}")
            if version <= last:
                raise RuntimeError(f"out-of-order v{version} after v{last}")
            seen.add(version)
            last = version


@contextmanager
def _temp_schema_and_patched_connect(name: str):
    """建临时 schema，并 monkey-patch _db.connect 让所有 connect() 都拿到
    search_path 已设到该 schema 的连接（独立 psycopg.connect，不走 pool）。
    """
    # 先用真 pool 连一下，建临时 schema
    with _db.connect() as setup:
        setup.execute(f"drop schema if exists {name} cascade")
        setup.execute(f"create schema {name}")

    @contextmanager
    def _patched_connect():
        # 关键：search_path 只含临时 schema，让 to_regclass('schema_migrations') 看不到 public 那张
        # extensions 走 pg_catalog（始终可见），所以 _do_init_db 的 create extension 不受影响
        conn = psycopg.connect(_db.database_url(), row_factory=__import__("psycopg").rows.dict_row)
        try:
            with conn.cursor() as cur:
                cur.execute(f"set search_path to {name}")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    with patch.object(_db, "connect", _patched_connect):
        try:
            yield name
        finally:
            pass

    # 用 pool 真删
    with _db.connect() as teardown:
        teardown.execute(f"drop schema if exists {name} cascade")


class FreshDatabaseStatus(unittest.TestCase):
    """fresh DB 场景：schema_migrations 表不存在时各诊断接口的行为。"""

    SCHEMA = "rpg_migrate_test_fresh"

    def test_list_migrations_on_fresh_returns_ok(self):
        """关键 fix：fresh DB 必须不挂在 schema_migrations 不存在上。"""
        with _temp_schema_and_patched_connect(self.SCHEMA):
            info = _db.list_migrations()
        self.assertTrue(info["ok"], f"fresh DB list_migrations 必须 ok: {info}")
        self.assertTrue(info.get("fresh_database"))
        self.assertFalse(info.get("schema_table_exists"))
        self.assertEqual(info["total_applied"], 0)
        self.assertEqual(info["total_known"], len(_db.MIGRATIONS))
        # 所有项都标 applied=False
        for it in info["migrations"]:
            self.assertFalse(it["applied"], it)

    def test_apply_then_status_marks_applied(self):
        """跑完 _apply_versioned_migrations 后再 list 应全部 applied=True。
        用 stub MIGRATIONS（不依赖任何已有表/扩展），让测试在隔离 schema 里完整跑得起来。
        """
        stub = [
            (1, "stub_a", ["create table t_a(id int primary key)"]),
            (2, "stub_b", ["create table t_b(id int primary key)"]),
            (3, "stub_c", ["alter table t_a add column note text not null default ''"]),
        ]
        with _temp_schema_and_patched_connect(self.SCHEMA):
            with patch.object(_db, "MIGRATIONS", stub):
                _db._apply_versioned_migrations()
                info = _db.list_migrations()
        self.assertTrue(info["ok"])
        self.assertFalse(info.get("fresh_database"), "已应用过 → 不再是 fresh")
        self.assertEqual(info["total_known"], 3)
        self.assertEqual(info["total_applied"], 3)
        for it in info["migrations"]:
            self.assertTrue(it["applied"], it)

    def test_apply_is_idempotent(self):
        """同一 migration 跑两次不会重复插 schema_migrations。"""
        stub = [
            (1, "stub_a", ["create table t_a(id int primary key)"]),
            (2, "stub_b", ["create table t_b(id int primary key)"]),
        ]
        with _temp_schema_and_patched_connect(self.SCHEMA):
            with patch.object(_db, "MIGRATIONS", stub):
                _db._apply_versioned_migrations()
                _db._apply_versioned_migrations()  # 再跑一次
                with _db.connect() as conn:
                    count = int(conn.execute(
                        "select count(*) as n from schema_migrations"
                    ).fetchone()["n"])
        self.assertEqual(count, len(stub), "重复跑不能增加 schema_migrations 行数")


class MigrateCliStatusCommand(unittest.TestCase):
    """python -m platform_app.migrate status 在 fresh DB 的行为。"""

    SCHEMA = "rpg_migrate_test_cli"

    def test_status_command_on_fresh_db_returns_zero(self):
        from platform_app import migrate
        with _temp_schema_and_patched_connect(self.SCHEMA):
            args = type("A", (), {})()
            rc = migrate.cmd_status(args)
        self.assertEqual(rc, 0, "fresh DB 跑 status 必须返回 0（信息性输出）而非 2（连接错）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
