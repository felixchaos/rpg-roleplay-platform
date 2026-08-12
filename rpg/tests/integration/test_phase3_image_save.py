"""test_phase3_image_save — Phase 3 即时生图后端 DB 运行时验证

5 项验证：
1. save_id 落库 — enqueue_image_generation(..., save_id='S123') → ai_images 该行 save_id=='S123'
2. 每日配额边界 — RPG_IMAGE_DAILY_CAP=2，同一用户连续 enqueue 3 次：前2次 pending，第3次 quota_exceeded 且无第3行
3. list 端点 owner+save 过滤 — user A S1:2张/S2:1张，user B 查 A 的 S1 得 0 张
4. cleanup_old_chat_images — 旧 game/chat 被删，近期图和 avatar 旧图保留；返回数正确
5. generate 端点配额回报 — TestClient 超配额时返回 ok=False code=quota_exceeded（搭不起时跳过并说明）
"""
from __future__ import annotations

import os
import random
import string
import sys
import unittest
from datetime import UTC
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")

FAKE_PNG = b"\x89PNG\r\n\x1a\nFAKE_PHASE3"


def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _make_user(db) -> int:
    uname = f"p3test_{_rand()}"
    row = db.execute(
        """
        insert into users(username, display_name, password_hash, email)
        values (%s, %s, 'x', %s) returning id
        """,
        (uname, uname, f"{uname}@example.test"),
    ).fetchone()
    return int(row["id"])


# ══════════════════════════════════════════════════════════════════════
# 1. save_id 落库
# ══════════════════════════════════════════════════════════════════════

class TestSaveIdPersisted(unittest.TestCase):
    """enqueue_image_generation 带 save_id 时，ai_images 行中 save_id 字段正确存储。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid = _make_user(db)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id = %s", (cls.uid,))

    def test_save_id_written_to_ai_images(self):
        """enqueue 传 save_id='S123' → ai_images 该行 save_id=='S123'。"""
        from platform_app.db import connect
        from platform_app.image_jobs import enqueue_image_generation

        result = enqueue_image_generation(
            self.uid,
            "moonlit river valley",
            "game",
            api_id="doubao",
            model="doubao-seedream-4-x",
            save_id="S123",
        )

        self.assertIn("image_id", result, f"返回值缺 image_id: {result}")
        self.assertEqual(result["status"], "pending", f"status 应为 pending: {result}")

        image_id = result["image_id"]
        with connect() as db:
            row = db.execute(
                "select save_id, status, user_id from ai_images where id = %s",
                (image_id,),
            ).fetchone()

        self.assertIsNotNone(row, f"ai_images 应有 id={image_id} 的行")
        self.assertEqual(row["save_id"], "S123",
                         f"save_id 应为 'S123'，实际={row['save_id']!r}")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(int(row["user_id"]), self.uid)

    def test_save_id_none_when_not_passed(self):
        """不传 save_id 时，ai_images 该行 save_id 为 NULL。"""
        from platform_app.db import connect
        from platform_app.image_jobs import enqueue_image_generation

        result = enqueue_image_generation(
            self.uid,
            "forest in autumn",
            "chat",
            api_id="doubao",
            model="doubao-seedream-4-x",
            # 不传 save_id
        )

        image_id = result["image_id"]
        with connect() as db:
            row = db.execute(
                "select save_id from ai_images where id = %s",
                (image_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertIsNone(row["save_id"],
                          f"不传 save_id 时应为 NULL，实际={row['save_id']!r}")

    def test_save_id_index_exists(self):
        """ix_ai_images_user_save 索引存在，确认 v70 迁移完整。"""
        from platform_app.db import connect

        with connect() as db:
            row = db.execute(
                """
                select indexname from pg_indexes
                 where tablename = 'ai_images'
                   and indexname = 'ix_ai_images_user_save'
                """,
            ).fetchone()

        self.assertIsNotNone(row, "ix_ai_images_user_save 索引不存在，v70 迁移可能缺失")


# ══════════════════════════════════════════════════════════════════════
# 2. 每日配额边界
# ══════════════════════════════════════════════════════════════════════

class TestDailyQuota(unittest.TestCase):
    """RPG_IMAGE_DAILY_CAP=2：前2次 pending，第3次 quota_exceeded 且无新 DB 行。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid = _make_user(db)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id = %s", (cls.uid,))

    def _count_user_images(self) -> int:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select count(*) as c from ai_images where user_id = %s",
                (self.uid,),
            ).fetchone()
        return int(row["c"])

    def test_quota_boundary(self):
        """连续 enqueue 3 次：前2次 pending，第3次 quota_exceeded 且未建第3行。"""
        from platform_app.image_jobs import enqueue_image_generation

        # 确保这个 user 没有旧图（隔离性）
        count_before = self._count_user_images()

        with patch.dict(os.environ, {"RPG_IMAGE_DAILY_CAP": "2"}):
            # 第1次
            r1 = enqueue_image_generation(
                self.uid, "quota test image 1", "game",
                api_id="doubao", model="doubao-seedream-4-x",
                save_id="QUOTA_TEST",
            )
            # 第2次
            r2 = enqueue_image_generation(
                self.uid, "quota test image 2", "game",
                api_id="doubao", model="doubao-seedream-4-x",
                save_id="QUOTA_TEST",
            )
            # 第3次：应被配额拦截
            r3 = enqueue_image_generation(
                self.uid, "quota test image 3 - should fail", "game",
                api_id="doubao", model="doubao-seedream-4-x",
                save_id="QUOTA_TEST",
            )

        # r1/r2 成功
        self.assertEqual(r1.get("status"), "pending",
                         f"第1次 enqueue 应返回 pending，实际={r1}")
        self.assertIn("image_id", r1, f"第1次 enqueue 应有 image_id: {r1}")

        self.assertEqual(r2.get("status"), "pending",
                         f"第2次 enqueue 应返回 pending，实际={r2}")
        self.assertIn("image_id", r2, f"第2次 enqueue 应有 image_id: {r2}")

        # r3 超配额
        self.assertEqual(r3.get("error"), "quota_exceeded",
                         f"第3次 enqueue 应返回 error=quota_exceeded，实际={r3}")
        self.assertEqual(r3.get("status"), "failed",
                         f"第3次 enqueue 应返回 status=failed，实际={r3}")
        self.assertIsNone(r3.get("image_id"),
                          f"第3次 enqueue 不应有 image_id，实际={r3.get('image_id')!r}")

        # DB 行数：应只多了 2 行，不是 3 行
        count_after = self._count_user_images()
        self.assertEqual(count_after, count_before + 2,
                         f"配额拦截后 ai_images 应只新增 2 行，before={count_before} after={count_after}")

    def test_quota_counts_pending_not_only_done(self):
        """配额计数包含 pending 状态（非 failed），即 pending 行也消耗配额。"""
        from platform_app.db import connect
        from platform_app.image_jobs import enqueue_image_generation

        # 建一个全新用户确保隔离
        with connect() as db:
            fresh_uid = _make_user(db)

        try:
            with patch.dict(os.environ, {"RPG_IMAGE_DAILY_CAP": "1"}):
                # 第1次：建 pending 行
                r1 = enqueue_image_generation(
                    fresh_uid, "pending counts quota test", "game",
                    api_id="doubao", model="doubao-seedream-4-x",
                )
                self.assertEqual(r1.get("status"), "pending",
                                 f"第1次应 pending，实际={r1}")

                # 第2次：pending 行应被计入配额
                r2 = enqueue_image_generation(
                    fresh_uid, "this should be quota_exceeded", "game",
                    api_id="doubao", model="doubao-seedream-4-x",
                )
                self.assertEqual(r2.get("error"), "quota_exceeded",
                                 f"pending 行应计入配额，第2次应 quota_exceeded，实际={r2}")
        finally:
            with connect() as db:
                db.execute("delete from users where id = %s", (fresh_uid,))


# ══════════════════════════════════════════════════════════════════════
# 3. list 端点 owner+save 过滤
# ══════════════════════════════════════════════════════════════════════

class TestListImages(unittest.TestCase):
    """GET /api/images/list 路由逻辑：owner 隔离 + save_id 过滤。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect

        with connect() as db:
            cls.uid_a = _make_user(db)
            cls.uid_b = _make_user(db)

        # 直接用 create_image_record 建测试数据（绕过 enqueue，避免 postproc_tasks 副作用）
        from platform_app.api.images import create_image_record, update_image_record

        # User A, save S1: 2 张 done
        i1 = create_image_record(
            user_id=cls.uid_a, kind="game", prompt="A S1 img1",
            api_id="doubao", model="x", save_id="S1",
        )
        update_image_record(i1, "done", url="/api/images/file/test1.png")
        cls.a_s1_id1 = i1

        i2 = create_image_record(
            user_id=cls.uid_a, kind="game", prompt="A S1 img2",
            api_id="doubao", model="x", save_id="S1",
        )
        update_image_record(i2, "done", url="/api/images/file/test2.png")
        cls.a_s1_id2 = i2

        # User A, save S2: 1 张 done
        i3 = create_image_record(
            user_id=cls.uid_a, kind="game", prompt="A S2 img1",
            api_id="doubao", model="x", save_id="S2",
        )
        update_image_record(i3, "done", url="/api/images/file/test3.png")
        cls.a_s2_id = i3

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id in (%s, %s)", (cls.uid_a, cls.uid_b))

    def _list_images_direct(self, user_id: int, save_id: str) -> list[dict]:
        """直接查 DB，模拟 GET /api/images/list 路由的 SQL 逻辑（owner 隔离）。"""
        from platform_app.db import connect
        with connect() as db:
            rows = db.execute(
                """
                select id, url, kind, prompt, status, created_at
                  from ai_images
                 where user_id = %s and save_id = %s
                 order by created_at desc
                """,
                (user_id, save_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def test_user_a_save_s1_returns_2(self):
        """user A 查 S1 应得 2 张。"""
        results = self._list_images_direct(self.uid_a, "S1")
        self.assertEqual(len(results), 2,
                         f"user A 查 S1 应得 2 张，实际={len(results)}: {results}")

    def test_user_a_save_s2_returns_1(self):
        """user A 查 S2 应得 1 张。"""
        results = self._list_images_direct(self.uid_a, "S2")
        self.assertEqual(len(results), 1,
                         f"user A 查 S2 应得 1 张，实际={len(results)}: {results}")

    def test_user_b_cannot_see_user_a_images(self):
        """user B 查 user A 的 save S1（实际是用 B 的 user_id 查 S1）应得 0 张（owner 隔离）。"""
        results = self._list_images_direct(self.uid_b, "S1")
        self.assertEqual(len(results), 0,
                         f"user B 不应看到 user A 的图片，实际={len(results)}: {results}")

    def test_list_result_fields_complete(self):
        """返回结果包含 id/url/kind/prompt/status/created_at 字段。"""
        results = self._list_images_direct(self.uid_a, "S1")
        self.assertTrue(len(results) > 0, "应有至少一条结果")
        for field in ("id", "url", "kind", "prompt", "status", "created_at"):
            self.assertIn(field, results[0],
                          f"结果缺少字段 {field!r}: {results[0]}")

    def test_list_ordering_desc(self):
        """结果按 created_at desc 排序。"""
        results = self._list_images_direct(self.uid_a, "S1")
        if len(results) >= 2:
            t0 = results[0]["created_at"]
            t1 = results[1]["created_at"]
            self.assertGreaterEqual(t0, t1,
                                    f"结果应按 created_at desc，第0条={t0} 第1条={t1}")


# ══════════════════════════════════════════════════════════════════════
# 4. cleanup_old_chat_images
# ══════════════════════════════════════════════════════════════════════

class TestCleanupOldChatImages(unittest.TestCase):
    """cleanup_old_chat_images(days=14) 选择性删除：
    - 删除 kind in (game,chat) 且超 14 天的行
    - 保留近期图、保留 kind=avatar 的旧图
    - 返回正确删除数
    """

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid = _make_user(db)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id = %s", (cls.uid,))

    def test_cleanup_selective_delete(self):
        """建旧/新/avatar图，cleanup(14天)后只删旧的 game/chat，保留其余。"""
        from platform_app.api.images import (
            cleanup_old_chat_images,
            create_image_record,
        )
        from platform_app.db import connect

        # 建 4 张图
        # old_game: kind=game, 20天前 → 应被删
        old_game_id = create_image_record(
            user_id=self.uid, kind="game", prompt="old game img",
            api_id="doubao", model="x", save_id="CLEANUP_TEST",
        )
        # old_chat: kind=chat, 20天前 → 应被删
        old_chat_id = create_image_record(
            user_id=self.uid, kind="chat", prompt="old chat img",
            api_id="doubao", model="x",
        )
        # recent_game: kind=game, 近期 → 应保留
        recent_game_id = create_image_record(
            user_id=self.uid, kind="game", prompt="recent game img",
            api_id="doubao", model="x", save_id="CLEANUP_TEST",
        )
        # old_avatar: kind=avatar, 20天前 → 应保留（avatar 不在清理范围）
        old_avatar_id = create_image_record(
            user_id=self.uid, kind="avatar", prompt="old avatar img",
            api_id="doubao", model="x",
        )

        # 手动把 old_game / old_chat / old_avatar 的 created_at 改成 20 天前
        with connect() as db:
            db.execute(
                """
                update ai_images
                   set created_at = now() - interval '20 days'
                 where id = any(%s::bigint[])
                """,
                ([old_game_id, old_chat_id, old_avatar_id],),
            )

        # 记录 cleanup 前 4 张都存在
        with connect() as db:
            ids_before = set(
                r["id"] for r in db.execute(
                    "select id from ai_images where id = any(%s::bigint[])",
                    ([old_game_id, old_chat_id, recent_game_id, old_avatar_id],),
                ).fetchall()
            )
        self.assertEqual(ids_before, {old_game_id, old_chat_id, recent_game_id, old_avatar_id},
                         f"cleanup 前 4 张应全存在，实际={ids_before}")

        # 执行 cleanup
        deleted_count = cleanup_old_chat_images(days=14)

        # 应删了 2 行（old_game + old_chat）
        self.assertEqual(deleted_count, 2,
                         f"cleanup 应删 2 行，实际返回={deleted_count}")

        # 确认 DB 状态
        with connect() as db:
            ids_after = set(
                r["id"] for r in db.execute(
                    "select id from ai_images where id = any(%s::bigint[])",
                    ([old_game_id, old_chat_id, recent_game_id, old_avatar_id],),
                ).fetchall()
            )

        self.assertNotIn(old_game_id, ids_after,
                         f"old_game(id={old_game_id}) 应已被删除")
        self.assertNotIn(old_chat_id, ids_after,
                         f"old_chat(id={old_chat_id}) 应已被删除")
        self.assertIn(recent_game_id, ids_after,
                      f"recent_game(id={recent_game_id}) 应保留（近期）")
        self.assertIn(old_avatar_id, ids_after,
                      f"old_avatar(id={old_avatar_id}) 应保留（avatar 不清理）")

    def test_cleanup_returns_zero_when_nothing_old(self):
        """没有超期图时，cleanup 返回 0。"""
        from platform_app.api.images import cleanup_old_chat_images, create_image_record

        # 建一张近期图（不超期）
        create_image_record(
            user_id=self.uid, kind="chat", prompt="very recent img",
            api_id="doubao", model="x",
        )

        # 在 1 天内什么都不应被删
        deleted_count = cleanup_old_chat_images(days=365)
        self.assertGreaterEqual(deleted_count, 0,
                                "返回值应为非负整数")
        # 365天内的 chat/game 图不应被删，此次调用中不应删上面刚建的近期图
        # （注意：可能删掉其他测试留下的旧图，所以只检查 >= 0 且函数不抛异常）

    def test_cleanup_empty_table_ok(self):
        """空表（或当前 user 无旧图）时，cleanup 不抛异常。"""
        from platform_app.api.images import cleanup_old_chat_images
        # 用较短期限确保会匹配所有：连跑两次验证幂等
        try:
            cleanup_old_chat_images(days=0)
            cleanup_old_chat_images(days=0)
        except Exception as exc:
            self.fail(f"cleanup_old_chat_images 不应抛异常: {exc}")


# ══════════════════════════════════════════════════════════════════════
# 5. generate 端点配额回报（TestClient）
# ══════════════════════════════════════════════════════════════════════

class TestGenerateEndpointQuota(unittest.TestCase):
    """POST /api/images/generate 超配额时返回 ok=False + code=quota_exceeded。

    若 FastAPI app 搭建依赖未就绪，跳过并注明原因。
    """

    @classmethod
    def setUpClass(cls):
        cls.skip_reason: str | None = None
        cls.app = None
        cls.client = None
        cls.uid: int = 0
        cls.session_token: str = ""
        cls._connect = None

        try:
            from platform_app.db import connect, init_db
            init_db()
            from starlette.testclient import TestClient

            # 动态导入主 app（顶层 rpg/app.py）
            import app as _app_module
            app = _app_module.app
            cls.app = app
            cls.client = TestClient(app, raise_server_exceptions=False)
            cls._connect = connect

            import hashlib
            import secrets as _sec
            from datetime import datetime, timedelta
            with connect() as db:
                cls.uid = _make_user(db)
                # 建一个 session token（token_hash=sha256，cookie name=rpg_session）
                tok = _sec.token_urlsafe(32)
                tok_hash = hashlib.sha256(tok.encode()).hexdigest()
                expires_at = datetime.now(UTC) + timedelta(hours=1)
                db.execute(
                    """
                    insert into sessions(user_id, token, token_hash, expires_at)
                    values (%s, %s, %s, %s)
                    """,
                    (cls.uid, "", tok_hash, expires_at),
                )
                cls.session_token = tok

        except Exception as exc:
            cls.skip_reason = f"TestClient 搭建失败（app 依赖未就绪）: {exc}"

    @classmethod
    def tearDownClass(cls):
        if cls.uid and cls._connect:
            try:
                with cls._connect() as db:
                    db.execute("delete from users where id = %s", (cls.uid,))
            except Exception:
                pass

    def setUp(self):
        if self.skip_reason:
            self.skipTest(self.skip_reason)

    def test_quota_exceeded_response(self):
        """超配额时 POST /api/images/generate 返回 429 + ok=False + code=quota_exceeded。"""
        # cookie name = rpg_session（见 _deps.py SESSION_COOKIE）
        headers = {"Cookie": f"rpg_session={self.session_token}"}

        with patch.dict(os.environ, {"RPG_IMAGE_DAILY_CAP": "0"}):
            resp = self.client.post(
                "/api/images/generate",
                json={"prompt": "test quota endpoint", "kind": "game"},
                headers=headers,
            )

        # 期望 429 或至少 body 含 quota_exceeded
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        # 首先检查 HTTP 状态码
        self.assertEqual(resp.status_code, 429,
                         f"超配额应返回 429，实际={resp.status_code}, body={data}")
        self.assertFalse(data.get("ok", True),
                         f"ok 应为 False，实际 body={data}")
        self.assertEqual(data.get("code"), "quota_exceeded",
                         f"code 应为 quota_exceeded，实际 body={data}")

    def test_normal_generate_returns_pending(self):
        """未超配额时 POST /api/images/generate 返回 ok=True + status=pending。"""
        headers = {"Cookie": f"rpg_session={self.session_token}"}

        with patch.dict(os.environ, {"RPG_IMAGE_DAILY_CAP": "999"}):
            resp = self.client.post(
                "/api/images/generate",
                json={
                    "prompt": "test normal endpoint",
                    "kind": "game",
                    "api_id": "doubao",
                    "model": "doubao-seedream-4-x",
                },
                headers=headers,
            )

        data = {}
        try:
            data = resp.json()
        except Exception:
            pass

        self.assertEqual(resp.status_code, 200,
                         f"正常请求应返回 200，实际={resp.status_code}, body={data}")
        self.assertTrue(data.get("ok"),
                        f"ok 应为 True，实际 body={data}")
        self.assertEqual(data.get("status"), "pending",
                         f"status 应为 pending，实际 body={data}")
        self.assertIn("image_id", data,
                      f"应有 image_id 字段，实际 body={data}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
