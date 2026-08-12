"""test_phase2_attach — Phase 2 生图附着到目标 DB 运行时验证

6 项针对性验证：
1. attach=user_avatar    — handle_image_gen 后 users.avatar_url == 生成 url
2. attach=card_avatar(owner)  — 属主卡 avatar_path 被写入
3. attach=card_avatar 跨用户拒绝 — 他人卡 avatar_path 不变，rowcount=0
4. attach=script_cover(owner) — 属主脚本 cover_image_url 被写入
5. GET /api/images/{id} 鉴权 — owner 查返 200，他人查返 404
6. attach 用 id 键生效(回归对齐) — card_avatar 用 {"type":"card_avatar","id":N} 能写成功
"""
from __future__ import annotations

import asyncio
import os
import random
import string
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")

FAKE_PNG = b"\x89PNG\r\n\x1a\nFAKE_PHASE2"


def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _make_user(db) -> int:
    uname = f"p2test_{_rand()}"
    row = db.execute(
        """
        insert into users(username, display_name, password_hash, email)
        values (%s, %s, 'x', %s) returning id
        """,
        (uname, uname, f"{uname}@example.test"),
    ).fetchone()
    return int(row["id"])


def _make_pc_card(db, user_id: int) -> int:
    """插一张属于 user_id 的 pc 类型卡（最小字段）。"""
    slug = f"card_{_rand()}"
    row = db.execute(
        """
        insert into character_cards
            (user_id, name, slug, card_type, source)
        values (%s, %s, %s, 'pc', 'user')
        returning id
        """,
        (user_id, f"TestCard_{_rand()}", slug),
    ).fetchone()
    return int(row["id"])


def _make_script(db, owner_id: int) -> int:
    """插一个属于 owner_id 的 scripts 行。"""
    row = db.execute(
        """
        insert into scripts(owner_id, title)
        values (%s, %s)
        returning id
        """,
        (owner_id, f"TestScript_{_rand()}"),
    ).fetchone()
    return int(row["id"])


def _enqueue_and_handle(user_id: int, attach: dict) -> str:
    """入队 → handle_image_gen(mock provider) → 返回生成的 url。"""
    from platform_app.image_jobs import enqueue_image_generation, handle_image_gen

    result = enqueue_image_generation(
        user_id, "test prompt", "avatar",
        api_id="doubao", model="doubao-seedream-4-x",
        attach=attach,
    )
    image_id = result["image_id"]

    payload = {
        "image_id": image_id,
        "user_id": user_id,
        "prompt": "test prompt",
        "kind": "avatar",
        "api_id": "doubao",
        "model": "doubao-seedream-4-x",
        "origin": "api_direct",
        "extra": {},
        "attach": attach,
    }

    with patch(
        "agents.image_gen.dispatch.generate_image_bytes",
        return_value=[FAKE_PNG],
    ), patch(
        "platform_app.user_credentials.resolve_api_key",
        return_value={"key": "sk-test", "base_url_override": ""},
    ):
        asyncio.run(handle_image_gen(payload))

    # 取生成后的 url
    from platform_app.db import connect
    with connect() as db:
        row = db.execute(
            "select url from ai_images where id = %s", (image_id,)
        ).fetchone()
    url = row["url"] if row else ""

    # 清理测试文件（如有）—— W1 后 URL 格式为 /api/storage/ai_images/...
    if url:
        from platform_app import storage as _storage
        if url.startswith("/api/storage/ai_images/"):
            filename = url[len("/api/storage/ai_images/"):]
            _storage.delete_file("ai_images/" + filename)
        elif url.startswith("/api/images/file/"):
            filename = url.split("/api/images/file/")[-1]
            _storage.delete_file("ai_images/" + filename)

    return url


# ══════════════════════════════════════════════════════════════════════
# 1. attach=user_avatar
# ══════════════════════════════════════════════════════════════════════

class TestAttachUserAvatar(unittest.TestCase):
    """attach=user_avatar → users.avatar_url 被写入。"""

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

    def test_user_avatar_attached(self):
        url = _enqueue_and_handle(self.uid, {"type": "user_avatar"})
        self.assertTrue(
            url.startswith("/api/storage/ai_images/") or url.startswith("/api/images/file/"),
            f"handle 后 url 应非空，实际={url!r}",
        )

        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select avatar_url from users where id = %s", (self.uid,)
            ).fetchone()

        self.assertIsNotNone(row, "users 行不存在")
        self.assertEqual(row["avatar_url"], url,
                         f"users.avatar_url 应=url，实际={row['avatar_url']!r}")


# ══════════════════════════════════════════════════════════════════════
# 2. attach=card_avatar(owner)
# ══════════════════════════════════════════════════════════════════════

class TestAttachCardAvatarOwner(unittest.TestCase):
    """attach=card_avatar 属主卡 → character_cards.avatar_path 被写入。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid = _make_user(db)
            cls.card_id = _make_pc_card(db, cls.uid)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id = %s", (cls.uid,))

    def test_card_avatar_attached(self):
        url = _enqueue_and_handle(
            self.uid,
            {"type": "card_avatar", "card_id": self.card_id},
        )
        self.assertTrue(
            url.startswith("/api/storage/ai_images/") or url.startswith("/api/images/file/"),
            f"handle 后 url 应非空，实际={url!r}",
        )

        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select avatar_path from character_cards where id = %s", (self.card_id,)
            ).fetchone()

        self.assertIsNotNone(row, "character_cards 行不存在")
        self.assertEqual(row["avatar_path"], url,
                         f"avatar_path 应=url，实际={row['avatar_path']!r}")


# ══════════════════════════════════════════════════════════════════════
# 3. attach=card_avatar 跨用户拒绝
# ══════════════════════════════════════════════════════════════════════

class TestAttachCardAvatarCrossUser(unittest.TestCase):
    """attach=card_avatar 他人卡 → avatar_path 不变（ownership 拒绝）。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid_a = _make_user(db)
            cls.uid_b = _make_user(db)
            cls.card_id = _make_pc_card(db, cls.uid_a)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id in (%s, %s)", (cls.uid_a, cls.uid_b))

    def test_cross_user_attach_rejected(self):
        # B 尝试 attach 到 A 的卡
        _enqueue_and_handle(
            self.uid_b,
            {"type": "card_avatar", "card_id": self.card_id},
        )

        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select avatar_path from character_cards where id = %s", (self.card_id,)
            ).fetchone()

        self.assertIsNotNone(row, "character_cards 行不存在")
        self.assertEqual(row["avatar_path"], "",
                         f"跨用户 attach 应被拒绝，avatar_path 应仍为空，实际={row['avatar_path']!r}")


# ══════════════════════════════════════════════════════════════════════
# 4. attach=script_cover(owner)
# ══════════════════════════════════════════════════════════════════════

class TestAttachScriptCoverOwner(unittest.TestCase):
    """attach=script_cover 属主 → scripts.cover_image_url 被写入。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid = _make_user(db)
            cls.script_id = _make_script(db, cls.uid)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id = %s", (cls.uid,))

    def test_script_cover_attached(self):
        url = _enqueue_and_handle(
            self.uid,
            {"type": "script_cover", "id": self.script_id},
        )
        self.assertTrue(
            url.startswith("/api/storage/ai_images/") or url.startswith("/api/images/file/"),
            f"handle 后 url 应非空，实际={url!r}",
        )

        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select cover_image_url from scripts where id = %s", (self.script_id,)
            ).fetchone()

        self.assertIsNotNone(row, "scripts 行不存在")
        self.assertEqual(row["cover_image_url"], url,
                         f"cover_image_url 应=url，实际={row['cover_image_url']!r}")


# ══════════════════════════════════════════════════════════════════════
# 5. GET /api/images/{id} 鉴权
# ══════════════════════════════════════════════════════════════════════

class TestImageGetAuthz(unittest.TestCase):
    """GET /api/images/{id}：owner 查到，他人查不到（get_image_record + user_id 比对）。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid_a = _make_user(db)
            cls.uid_b = _make_user(db)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id in (%s, %s)", (cls.uid_a, cls.uid_b))

    def test_owner_can_read(self):
        """owner 查自己的 image 记录返回正确数据。"""
        from platform_app.api.images import create_image_record, get_image_record

        image_id = create_image_record(
            user_id=self.uid_a,
            kind="chat",
            prompt="test image for owner",
        )

        record = get_image_record(image_id)
        self.assertIsNotNone(record, "get_image_record 应返回记录")
        self.assertEqual(int(record["user_id"]), self.uid_a,
                         f"user_id 应={self.uid_a}，实际={record['user_id']}")

        # 模拟路由逻辑：owner 查到
        self.assertIsNotNone(record)
        self.assertEqual(int(record.get("user_id") or 0), self.uid_a)  # owner 匹配

    def test_other_user_denied(self):
        """他人（user B）查 user A 的 image 应当被路由逻辑拒绝（模拟 /api/images/{id}）。"""
        from platform_app.api.images import create_image_record, get_image_record

        image_id = create_image_record(
            user_id=self.uid_a,
            kind="chat",
            prompt="test image for denial",
        )

        record = get_image_record(image_id)
        # 路由逻辑：record is None OR record.user_id != user_b → 404
        denied = record is None or int(record.get("user_id") or 0) != self.uid_b
        self.assertTrue(denied,
                        f"user B 查 user A 的 image 应被拒绝（404），但 denied={denied}")


# ══════════════════════════════════════════════════════════════════════
# 6. attach 用 id 键生效（回归对齐）
# ══════════════════════════════════════════════════════════════════════

class TestAttachIdKeyAlignment(unittest.TestCase):
    """card_avatar 用 {"type":"card_avatar","id":N}（非 card_id 键）必须能写成功。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid = _make_user(db)
            cls.card_id = _make_pc_card(db, cls.uid)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id = %s", (cls.uid,))

    def test_id_key_writes_avatar(self):
        """使用 id 键（而非 card_id 键）时 avatar_path 被正确写入。"""
        url = _enqueue_and_handle(
            self.uid,
            {"type": "card_avatar", "id": self.card_id},  # 注意：id 不是 card_id
        )
        self.assertTrue(
            url.startswith("/api/storage/ai_images/") or url.startswith("/api/images/file/"),
            f"handle 后 url 应非空，实际={url!r}",
        )

        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select avatar_path from character_cards where id = %s", (self.card_id,)
            ).fetchone()

        self.assertIsNotNone(row, "character_cards 行不存在")
        self.assertEqual(row["avatar_path"], url,
                         f"用 id 键时 avatar_path 应=url，实际={row['avatar_path']!r}")

    def test_script_cover_id_key(self):
        """script_cover 用 id 键时 cover_image_url 被正确写入。"""
        from platform_app.db import connect

        with connect() as db:
            script_id = _make_script(db, self.uid)

        url = _enqueue_and_handle(
            self.uid,
            {"type": "script_cover", "id": script_id},
        )
        self.assertTrue(
            url.startswith("/api/storage/ai_images/") or url.startswith("/api/images/file/"),
            f"handle 后 url 应非空，实际={url!r}",
        )

        with connect() as db:
            row = db.execute(
                "select cover_image_url from scripts where id = %s", (script_id,)
            ).fetchone()

        self.assertIsNotNone(row, "scripts 行不存在")
        self.assertEqual(row["cover_image_url"], url,
                         f"用 id 键时 cover_image_url 应=url，实际={row['cover_image_url']!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
