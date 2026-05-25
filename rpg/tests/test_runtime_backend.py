"""
test_runtime_backend.py — B2 验证 runtime 元数据 DB 化

覆盖：
- server 模式（RPG_REQUIRE_AUTH=1）下 runtime 写入 user_runtime DB 表
- server 模式下不再写 platform_data/runtime/user_{id}.json 文件
- read_runtime 能从 DB 读回写入的指针
- 切换 backend 选择逻辑
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests.helpers import (
    cleanup_test_users,
    integtest_username,
    make_client,
    register_user,
)


class RuntimeBackendSelection(unittest.TestCase):
    def test_auto_under_require_auth_picks_db(self):
        from platform_app import runtime
        old = os.environ.get("RPG_REQUIRE_AUTH")
        old_backend = os.environ.get("RPG_RUNTIME_BACKEND")
        try:
            os.environ["RPG_REQUIRE_AUTH"] = "1"
            os.environ.pop("RPG_RUNTIME_BACKEND", None)
            self.assertEqual(runtime._runtime_backend(), "db")
        finally:
            if old is None:
                os.environ.pop("RPG_REQUIRE_AUTH", None)
            else:
                os.environ["RPG_REQUIRE_AUTH"] = old
            if old_backend is not None:
                os.environ["RPG_RUNTIME_BACKEND"] = old_backend

    def test_explicit_file_override(self):
        from platform_app import runtime
        old = os.environ.get("RPG_RUNTIME_BACKEND")
        try:
            os.environ["RPG_RUNTIME_BACKEND"] = "file"
            self.assertEqual(runtime._runtime_backend(), "file")
        finally:
            if old is None:
                os.environ.pop("RPG_RUNTIME_BACKEND", None)
            else:
                os.environ["RPG_RUNTIME_BACKEND"] = old

    def test_explicit_db_override(self):
        from platform_app import runtime
        old = os.environ.get("RPG_RUNTIME_BACKEND")
        try:
            os.environ["RPG_RUNTIME_BACKEND"] = "db"
            self.assertEqual(runtime._runtime_backend(), "db")
        finally:
            if old is None:
                os.environ.pop("RPG_RUNTIME_BACKEND", None)
            else:
                os.environ["RPG_RUNTIME_BACKEND"] = old


class RuntimeDBWriteRead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_write_then_read_roundtrip(self):
        """server 模式：write_runtime 写入 DB，read_runtime 从 DB 读回。"""
        from platform_app import runtime
        from platform_app.db import connect

        u = register_user(self.client)
        # 拿真实 user_id
        with connect() as db:
            row = db.execute(
                "select id from users where username = %s",
                (u["username"],),
            ).fetchone()
            user_id = int(row["id"])

        old = os.environ.get("RPG_RUNTIME_BACKEND")
        os.environ["RPG_RUNTIME_BACKEND"] = "db"
        try:
            # save_id 是 FK，传 None 避开违例（runtime 入口允许 save_id=0 → NULL）
            payload = runtime.write_runtime(
                user_id=user_id,
                save_id=0,
                node_id=12345,
                source_state_path="",
                ref_id=None,
            )
            self.assertEqual(payload["user_id"], user_id)

            got = runtime.read_runtime(user_id=user_id)
            self.assertEqual(int(got.get("user_id") or 0), user_id)
            self.assertEqual(int(got.get("active_commit_id") or 0), 12345)
        finally:
            if old is None:
                os.environ.pop("RPG_RUNTIME_BACKEND", None)
            else:
                os.environ["RPG_RUNTIME_BACKEND"] = old
            # 清掉 user_runtime 这行
            with connect() as db:
                db.execute("delete from user_runtime where user_id = %s", (user_id,))

    def test_db_mode_does_not_write_user_runtime_file(self):
        from platform_app import runtime
        from platform_app.db import connect

        u = register_user(self.client)
        with connect() as db:
            row = db.execute(
                "select id from users where username = %s",
                (u["username"],),
            ).fetchone()
            user_id = int(row["id"])

        per_user_file = runtime.RUNTIME_DIR / f"user_{user_id}.json"
        # 先确保不存在
        if per_user_file.exists():
            per_user_file.unlink()

        old = os.environ.get("RPG_RUNTIME_BACKEND")
        os.environ["RPG_RUNTIME_BACKEND"] = "db"
        try:
            runtime.write_runtime(
                user_id=user_id, save_id=0, node_id=22222,
                source_state_path="", ref_id=None,
            )
            self.assertFalse(
                per_user_file.exists(),
                f"server 模式不应创建 {per_user_file}",
            )
        finally:
            if old is None:
                os.environ.pop("RPG_RUNTIME_BACKEND", None)
            else:
                os.environ["RPG_RUNTIME_BACKEND"] = old
            with connect() as db:
                db.execute("delete from user_runtime where user_id = %s", (user_id,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
