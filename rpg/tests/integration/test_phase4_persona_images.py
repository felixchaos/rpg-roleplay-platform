"""test_phase4_persona_images — Phase 4 人设图自动维护+历史 DB 运行时验证

6 项验证：
1. 钩子-变更触发     — auto_image_sync=true 改 appearance 再 upsert → persona_hash 更新 + 入 image job
2. 钩子-auto_sync 关闭不触发 — auto_image_sync=false 时改 persona 字段 → hash 更新 但不入 job
3. 钩子-无变化不触发 — 相同内容再 upsert → 不入新 job
4. worker is_current 翻转 — handle_image_gen(persona_image) 两次：旧行 false 新行 true + avatar_path 跟随更新
5. list_persona_images — 返回 2 行按 created_at desc；owner 隔离（别的 user 查得空/拒）
6. set_current_persona_image(回滚) — 把第 1 张设为 current；跨 user 拒绝
"""
from __future__ import annotations

import asyncio
import os
import random
import string
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")

FAKE_PNG_A = b"\x89PNG\r\n\x1a\nFAKE_P4_A"
FAKE_PNG_B = b"\x89PNG\r\n\x1a\nFAKE_P4_B"


def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _make_user(db) -> int:
    uname = f"p4test_{_rand()}"
    row = db.execute(
        """
        insert into users(username, display_name, password_hash, email)
        values (%s, %s, 'x', %s) returning id
        """,
        (uname, uname, f"{uname}@example.test"),
    ).fetchone()
    return int(row["id"])


# ══════════════════════════════════════════════════════════════════════
# 1. 钩子-变更触发
# ══════════════════════════════════════════════════════════════════════

class TestHookTriggersOnChange(unittest.TestCase):
    """auto_image_sync=true 且 persona 字段变化时，钩子应入队生图 job 并更新 persona_hash。"""

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
            cls.connect().close() if False else None
            db.execute("delete from users where id = %s", (cls.uid,))

    def _count_image_jobs(self, db, card_id: int) -> int:
        """计数 chat_postproc_tasks 中 persona_image attach 的 image_gen job 数量。"""
        rows = db.execute(
            """
            select count(*) as c
              from chat_postproc_tasks
             where task_kind = 'image_gen'
               and payload->>'attach' like %s
            """,
            (f'%"id": {card_id}%',),
        ).fetchone()
        # 兼容两种 json 格式："id": 123 或 "id":123
        rows2 = db.execute(
            """
            select count(*) as c
              from chat_postproc_tasks
             where task_kind = 'image_gen'
               and (payload->>'attach')::jsonb->>'id' = %s
            """,
            (str(card_id),),
        ).fetchone()
        return int(rows2["c"]) if rows2 else int(rows["c"])

    def test_hook_triggers_when_hash_changes_and_auto_sync_on(self):
        """upsert persona(auto_image_sync=true) → 改 appearance → 再 upsert → job 入队 + hash 更新。"""
        from platform_app.db import connect
        from platform_app.user_cards import (
            compute_persona_hash,
            set_auto_image_sync,
            upsert_persona,
        )

        # 第1次 upsert：建卡（初始 hash 从默认 '' 变为真实 hash，会触发钩子）
        # 为精确控制：先建卡（关闭 auto_sync），再开启 auto_sync，再改内容 upsert
        card = upsert_persona(self.uid, {
            "name": f"HookTest_{_rand()}",
            "appearance": "short black hair",
            "personality": "calm",
        })
        card_id = int(card["id"])

        # 第一次 upsert 后 auto_sync=false（默认），先记录 job 数
        with connect() as db:
            jobs_before_enable = self._count_image_jobs(db, card_id)

        # 开启 auto_image_sync
        set_auto_image_sync(self.uid, card_id, True)

        # 改 appearance 字段后再 upsert（hash 必变）
        upsert_persona(self.uid, {
            "id": card_id,
            "name": card["name"],
            "appearance": "long silver hair with a braid",  # 变化
            "personality": "calm",
        })

        # 验证 persona_hash 已更新
        with connect() as db:
            row = db.execute(
                "select persona_hash, auto_image_sync from character_cards where id = %s",
                (card_id,),
            ).fetchone()
            jobs_after = self._count_image_jobs(db, card_id)

        expected_hash = compute_persona_hash({
            "name": card["name"],
            "identity": "",
            "appearance": "long silver hair with a braid",
            "personality": "calm",
            "background": "",
        })
        self.assertEqual(row["persona_hash"], expected_hash,
                         f"persona_hash 应已更新为新 appearance 的 hash，实际={row['persona_hash']!r}")
        self.assertTrue(bool(row["auto_image_sync"]), "auto_image_sync 应为 true")
        self.assertGreater(
            jobs_after, jobs_before_enable,
            f"auto_image_sync=true 且内容变化时应入队 image job，"
            f"jobs_before_enable={jobs_before_enable} jobs_after={jobs_after}",
        )


# ══════════════════════════════════════════════════════════════════════
# 2. 钩子-auto_sync 关闭不触发
# ══════════════════════════════════════════════════════════════════════

class TestHookNoTriggerWhenSyncOff(unittest.TestCase):
    """auto_image_sync=false 时改 persona 字段 → persona_hash 更新，但不入 job。"""

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

    def _count_persona_jobs(self, db, card_id: int) -> int:
        row = db.execute(
            """
            select count(*) as c
              from chat_postproc_tasks
             where task_kind = 'image_gen'
               and (payload->>'attach')::jsonb->>'id' = %s
            """,
            (str(card_id),),
        ).fetchone()
        return int(row["c"]) if row else 0

    def test_no_job_when_auto_sync_false(self):
        """auto_image_sync=false 时改字段 upsert → hash 变、job 数不增。"""
        from platform_app.db import connect
        from platform_app.user_cards import compute_persona_hash, upsert_persona

        # 建卡，auto_sync 默认 false
        card = upsert_persona(self.uid, {
            "name": f"NoSync_{_rand()}",
            "appearance": "blue eyes",
            "personality": "quiet",
        })
        card_id = int(card["id"])

        with connect() as db:
            jobs_before = self._count_persona_jobs(db, card_id)
            # 确认 auto_image_sync=false
            row_before = db.execute(
                "select persona_hash, auto_image_sync from character_cards where id = %s",
                (card_id,),
            ).fetchone()
        self.assertFalse(bool(row_before["auto_image_sync"]),
                         "测试前提：auto_image_sync 应为 false")

        # 改 appearance 再 upsert
        upsert_persona(self.uid, {
            "id": card_id,
            "name": card["name"],
            "appearance": "green eyes",   # 变化
            "personality": "quiet",
        })

        with connect() as db:
            jobs_after = self._count_persona_jobs(db, card_id)
            row_after = db.execute(
                "select persona_hash from character_cards where id = %s",
                (card_id,),
            ).fetchone()

        expected_hash = compute_persona_hash({
            "name": card["name"],
            "identity": "",
            "appearance": "green eyes",
            "personality": "quiet",
            "background": "",
        })
        # hash 应更新
        self.assertEqual(row_after["persona_hash"], expected_hash,
                         f"auto_sync=false 时 persona_hash 仍应更新，实际={row_after['persona_hash']!r}")
        # job 数不增
        self.assertEqual(jobs_after, jobs_before,
                         f"auto_sync=false 时不应入队 image job，"
                         f"before={jobs_before} after={jobs_after}")


# ══════════════════════════════════════════════════════════════════════
# 3. 钩子-无变化不触发
# ══════════════════════════════════════════════════════════════════════

class TestHookNoTriggerOnSameContent(unittest.TestCase):
    """相同内容再 upsert → persona_hash 不变 → 不入新 job（即使 auto_image_sync=true）。"""

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

    def _count_persona_jobs(self, db, card_id: int) -> int:
        row = db.execute(
            """
            select count(*) as c
              from chat_postproc_tasks
             where task_kind = 'image_gen'
               and (payload->>'attach')::jsonb->>'id' = %s
            """,
            (str(card_id),),
        ).fetchone()
        return int(row["c"]) if row else 0

    def test_no_job_when_content_unchanged(self):
        """相同内容第2次 upsert → job 数不增（hash 未变不触发钩子）。"""
        from platform_app.db import connect
        from platform_app.user_cards import set_auto_image_sync, upsert_persona

        # 建卡（auto_sync=false 时建，再开启，避免首次 upsert 建 job）
        persona_data = {
            "name": f"SameContent_{_rand()}",
            "appearance": "red hair",
            "personality": "energetic",
        }
        card = upsert_persona(self.uid, persona_data)
        card_id = int(card["id"])
        # 首次 upsert 时 auto_sync=false，不应建 job，但 hash 已写入
        set_auto_image_sync(self.uid, card_id, True)

        # 第一次「有变化」upsert（auto_sync=true，hash 不变但要先稳定 hash）
        # 此时 hash 已经等于第一次内容，再 upsert 相同内容 → 无变化 → 无 job
        with connect() as db:
            jobs_after_enable = self._count_persona_jobs(db, card_id)

        # 相同内容再 upsert（auto_sync=true，但 hash 相同）
        upsert_persona(self.uid, {
            "id": card_id,
            "name": persona_data["name"],
            "appearance": persona_data["appearance"],  # 相同
            "personality": persona_data["personality"],  # 相同
        })

        with connect() as db:
            jobs_after_same = self._count_persona_jobs(db, card_id)

        self.assertEqual(
            jobs_after_same, jobs_after_enable,
            f"相同内容 upsert 不应入新 job，"
            f"enable 后={jobs_after_enable} same 后={jobs_after_same}",
        )


# ══════════════════════════════════════════════════════════════════════
# 4. worker is_current 翻转（handle_image_gen + persona_image）
# ══════════════════════════════════════════════════════════════════════

class TestWorkerPersonaImageIsCurrentFlip(unittest.TestCase):
    """handle_image_gen 两次（H1/H2）：
    - 第一次：card_persona_images 新增1行 is_current=true + avatar_path 更新
    - 第二次：旧行 is_current=false，新行 is_current=true（partial unique 不报错），avatar_path 更新
    """

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid = _make_user(db)
            # 建一张 persona 卡
            uname = f"worker_p4_{_rand()}"
            row = db.execute(
                """
                insert into character_cards(user_id, slug, card_type, source, scope,
                    first_revealed_chapter, importance, name)
                values (%s, %s, 'persona', 'persona', 'private', 1, 100, %s)
                returning id
                """,
                (cls.uid, f"wp4_{_rand()}", f"WorkerCard_{_rand()}"),
            ).fetchone()
            cls.card_id = int(row["id"])

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id = %s", (cls.uid,))

    def _run_worker(self, fake_png: bytes, persona_hash: str) -> str:
        """enqueue_image_generation + handle_image_gen (mock provider+key)，返回图片 url。"""
        from platform_app.image_jobs import enqueue_image_generation, handle_image_gen

        result = enqueue_image_generation(
            self.uid,
            prompt=f"portrait of a persona hash={persona_hash[:8]}",
            kind="persona",
            api_id="doubao",
            model="doubao-seedream-4-x",
            attach={
                "type": "persona_image",
                "id": self.card_id,
                "persona_hash": persona_hash,
                "source": "manual",
            },
        )
        image_id = result["image_id"]

        payload = {
            "image_id": image_id,
            "user_id": self.uid,
            "prompt": f"portrait of a persona hash={persona_hash[:8]}",
            "kind": "persona",
            "api_id": "doubao",
            "model": "doubao-seedream-4-x",
            "origin": "api_direct",
            "extra": {},
            "attach": {
                "type": "persona_image",
                "id": self.card_id,
                "persona_hash": persona_hash,
                "source": "manual",
            },
        }

        with patch(
            "agents.image_gen.dispatch.generate_image_bytes",
            return_value=[fake_png],
        ), patch(
            "platform_app.user_credentials.resolve_api_key",
            return_value={"key": "sk-test-p4", "base_url_override": ""},
        ):
            asyncio.run(handle_image_gen(payload))

        # 拿 url
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select url, status from ai_images where id = %s",
                (image_id,),
            ).fetchone()
        self.assertEqual(row["status"], "done",
                         f"ai_images id={image_id} status 应为 done，实际={row['status']!r}")
        return str(row["url"])

    def test_first_worker_run_creates_is_current_row(self):
        """第一次 worker 跑完：card_persona_images 有1行 is_current=true，avatar_path == url。"""
        from platform_app.db import connect

        url1 = self._run_worker(FAKE_PNG_A, "H1_" + _rand(16))

        with connect() as db:
            rows = db.execute(
                "select id, image_url, is_current from card_persona_images where card_id = %s",
                (self.card_id,),
            ).fetchall()
            card_row = db.execute(
                "select avatar_path from character_cards where id = %s",
                (self.card_id,),
            ).fetchone()

        current_rows = [r for r in rows if r["is_current"]]
        self.assertEqual(len(current_rows), 1,
                         f"第一次 worker 后应有 1 行 is_current=true，实际={rows}")
        self.assertEqual(current_rows[0]["image_url"], url1,
                         f"is_current 行的 image_url 应为 {url1!r}")
        self.assertEqual(card_row["avatar_path"], url1,
                         f"avatar_path 应更新为 {url1!r}，实际={card_row['avatar_path']!r}")

        # 保存第一行 id 给后续测试
        self.__class__._img_id_1 = int(current_rows[0]["id"])
        self.__class__._url_1 = url1

    def test_second_worker_run_flips_is_current_no_unique_violation(self):
        """第二次 worker 跑完：旧行 is_current=false，新行 is_current=true，partial unique 不报错。"""
        # 依赖第一次已跑
        if not hasattr(self.__class__, "_img_id_1"):
            self.test_first_worker_run_creates_is_current_row()

        from platform_app.db import connect

        url2 = self._run_worker(FAKE_PNG_B, "H2_" + _rand(16))

        with connect() as db:
            rows = db.execute(
                "select id, image_url, is_current from card_persona_images where card_id = %s",
                (self.card_id,),
            ).fetchall()
            card_row = db.execute(
                "select avatar_path from character_cards where id = %s",
                (self.card_id,),
            ).fetchone()

        current_rows = [r for r in rows if r["is_current"]]
        non_current_rows = [r for r in rows if not r["is_current"]]

        self.assertEqual(len(current_rows), 1,
                         f"第二次 worker 后仍应只有1行 is_current=true，实际={rows}")
        self.assertEqual(current_rows[0]["image_url"], url2,
                         f"新 is_current 行 url 应为 {url2!r}")
        # 旧行应变为 false
        old_row = next((r for r in non_current_rows if int(r["id"]) == self.__class__._img_id_1), None)
        self.assertIsNotNone(old_row,
                             f"旧行 id={self.__class__._img_id_1} 应存在且 is_current=false")
        self.assertFalse(bool(old_row["is_current"]),
                         "旧行 is_current 应为 false")
        # avatar_path 更新为 url2
        self.assertEqual(card_row["avatar_path"], url2,
                         f"avatar_path 应更新为 {url2!r}，实际={card_row['avatar_path']!r}")

        # 保存 img_id_2 给后续 list/set_current 测试
        self.__class__._img_id_2 = int(current_rows[0]["id"])
        self.__class__._url_2 = url2
        self.__class__._all_rows = rows


# ══════════════════════════════════════════════════════════════════════
# 5. list_persona_images
# ══════════════════════════════════════════════════════════════════════

class TestListPersonaImages(unittest.TestCase):
    """list_persona_images 返回 2 行按 created_at desc；owner 隔离。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid_a = _make_user(db)
            cls.uid_b = _make_user(db)
            # 建卡 for uid_a
            row = db.execute(
                """
                insert into character_cards(user_id, slug, card_type, source, scope,
                    first_revealed_chapter, importance, name)
                values (%s, %s, 'persona', 'persona', 'private', 1, 100, %s)
                returning id
                """,
                (cls.uid_a, f"list_p4_{_rand()}", f"ListCard_{_rand()}"),
            ).fetchone()
            cls.card_id = int(row["id"])
        # 预先建两张图，保证所有测试方法独立可运行
        cls._run_worker(cls.uid_a, cls.card_id, FAKE_PNG_A, f"LH_setup1_{_rand()}")
        time.sleep(0.05)
        cls._run_worker(cls.uid_a, cls.card_id, FAKE_PNG_B, f"LH_setup2_{_rand()}")

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id in (%s,%s)", (cls.uid_a, cls.uid_b))

    @staticmethod
    def _run_worker(uid: int, card_id: int, fake_png: bytes, h: str) -> str:
        """快速 enqueue + worker(mock)，返回 url。"""
        from platform_app.image_jobs import enqueue_image_generation, handle_image_gen
        result = enqueue_image_generation(
            uid,
            prompt=f"list test {h}",
            kind="persona",
            api_id="doubao",
            model="m",
            attach={
                "type": "persona_image",
                "id": card_id,
                "persona_hash": h,
                "source": "manual",
            },
        )
        payload = {
            "image_id": result["image_id"],
            "user_id": uid,
            "prompt": f"list test {h}",
            "kind": "persona",
            "api_id": "doubao",
            "model": "m",
            "origin": "api_direct",
            "extra": {},
            "attach": {
                "type": "persona_image",
                "id": card_id,
                "persona_hash": h,
                "source": "manual",
            },
        }
        with patch("agents.image_gen.dispatch.generate_image_bytes", return_value=[fake_png]), \
             patch("platform_app.user_credentials.resolve_api_key",
                   return_value={"key": "sk-x", "base_url_override": ""}):
            asyncio.run(handle_image_gen(payload))
        from platform_app.db import connect
        with connect() as db:
            r = db.execute("select url from ai_images where id=%s", (result["image_id"],)).fetchone()
        return str(r["url"])

    def test_list_returns_two_rows_desc(self):
        """list 返回 ≥2 行按 created_at desc（图已在 setUpClass 预建）。"""
        from platform_app.image_jobs import list_persona_images
        imgs = list_persona_images(self.uid_a, self.card_id)
        self.assertGreaterEqual(len(imgs), 2,
                                f"应至少 2 行，实际={len(imgs)}")
        # 按 created_at desc：第0条不早于第1条
        if len(imgs) >= 2:
            t0 = imgs[0]["created_at"]
            t1 = imgs[1]["created_at"]
            self.assertGreaterEqual(
                t0, t1,
                f"结果应按 created_at desc，imgs[0]={t0} imgs[1]={t1}",
            )

    def test_list_owner_isolation(self):
        """uid_b 查 uid_a 的卡应抛 ValueError（card 不属于 uid_b）。"""
        from platform_app.image_jobs import list_persona_images
        with self.assertRaises(ValueError, msg="跨 user 查应抛 ValueError"):
            list_persona_images(self.uid_b, self.card_id)

    def test_list_result_fields(self):
        """结果包含 id/image_url/persona_hash/card_row_version/source/is_current/created_at。"""
        from platform_app.image_jobs import list_persona_images
        imgs = list_persona_images(self.uid_a, self.card_id)
        if not imgs:
            self.skipTest("list_persona_images 返回空，跳过字段检查")
        for field in ("id", "image_url", "persona_hash", "card_row_version",
                      "source", "is_current", "created_at"):
            self.assertIn(field, imgs[0], f"结果缺字段 {field!r}: {imgs[0]}")


# ══════════════════════════════════════════════════════════════════════
# 6. set_current_persona_image（回滚）
# ══════════════════════════════════════════════════════════════════════

class TestSetCurrentPersonaImage(unittest.TestCase):
    """set_current_persona_image 回滚：把第1张设为 current；跨 user 调用被拒。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid_owner = _make_user(db)
            cls.uid_other = _make_user(db)
            row = db.execute(
                """
                insert into character_cards(user_id, slug, card_type, source, scope,
                    first_revealed_chapter, importance, name)
                values (%s, %s, 'persona', 'persona', 'private', 1, 100, %s)
                returning id
                """,
                (cls.uid_owner, f"setcur_{_rand()}", f"SetCurCard_{_rand()}"),
            ).fetchone()
            cls.card_id = int(row["id"])
        # 建两张图
        cls.url_1, cls.img_id_1 = cls._run_worker_static(cls.uid_owner, cls.card_id, FAKE_PNG_A, f"SC_H1_{_rand()}")
        time.sleep(0.05)
        cls.url_2, cls.img_id_2 = cls._run_worker_static(cls.uid_owner, cls.card_id, FAKE_PNG_B, f"SC_H2_{_rand()}")

    @staticmethod
    def _run_worker_static(uid, card_id, fake_png, h):
        from platform_app.db import connect
        from platform_app.image_jobs import enqueue_image_generation, handle_image_gen
        result = enqueue_image_generation(
            uid, prompt=f"setcur {h}", kind="persona",
            api_id="doubao", model="m",
            attach={
                "type": "persona_image",
                "id": card_id,
                "persona_hash": h,
                "source": "manual",
            },
        )
        image_id = result["image_id"]
        payload = {
            "image_id": image_id,
            "user_id": uid,
            "prompt": f"setcur {h}",
            "kind": "persona",
            "api_id": "doubao",
            "model": "m",
            "origin": "api_direct",
            "extra": {},
            "attach": {
                "type": "persona_image",
                "id": card_id,
                "persona_hash": h,
                "source": "manual",
            },
        }
        with patch("agents.image_gen.dispatch.generate_image_bytes", return_value=[fake_png]), \
             patch("platform_app.user_credentials.resolve_api_key",
                   return_value={"key": "sk-x", "base_url_override": ""}):
            asyncio.run(handle_image_gen(payload))
        with connect() as db:
            r = db.execute("select url from ai_images where id=%s", (image_id,)).fetchone()
            # 读 card_persona_images 找新建的那行 id
            cpi = db.execute(
                "select id from card_persona_images where card_id=%s and is_current=true",
                (card_id,),
            ).fetchone()
        return str(r["url"]), int(cpi["id"])

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id in (%s,%s)",
                       (cls.uid_owner, cls.uid_other))

    def test_set_current_rollback_to_first_image(self):
        """把第1张（旧）设为 current → 该图 is_current=true、第2张 false、avatar_path 更新。"""
        from platform_app.db import connect
        from platform_app.image_jobs import set_current_persona_image

        # 执行回滚：把第1张设为 current
        result = set_current_persona_image(self.uid_owner, self.card_id, self.img_id_1)
        self.assertTrue(result.get("ok"), f"set_current 应返回 ok=True，实际={result}")
        self.assertEqual(result.get("image_url"), self.url_1,
                         f"返回 image_url 应为 url_1={self.url_1!r}，实际={result.get('image_url')!r}")

        with connect() as db:
            rows = db.execute(
                "select id, image_url, is_current from card_persona_images where card_id=%s",
                (self.card_id,),
            ).fetchall()
            card = db.execute(
                "select avatar_path from character_cards where id=%s",
                (self.card_id,),
            ).fetchone()

        rows_by_id = {int(r["id"]): r for r in rows}

        # img_id_1 应为 true
        self.assertTrue(
            bool(rows_by_id[self.img_id_1]["is_current"]),
            f"img_id_1={self.img_id_1} 应为 is_current=true，rows={rows}",
        )
        # img_id_2 应为 false
        self.assertFalse(
            bool(rows_by_id[self.img_id_2]["is_current"]),
            f"img_id_2={self.img_id_2} 应为 is_current=false，rows={rows}",
        )
        # avatar_path 更新为 url_1
        self.assertEqual(card["avatar_path"], self.url_1,
                         f"avatar_path 应为 url_1={self.url_1!r}，实际={card['avatar_path']!r}")

    def test_set_current_cross_user_rejected(self):
        """uid_other 调用 set_current_persona_image → 应抛 ValueError（card 不属于 uid_other）。"""
        from platform_app.image_jobs import set_current_persona_image
        with self.assertRaises(ValueError, msg="跨 user 设 current 应抛 ValueError"):
            set_current_persona_image(self.uid_other, self.card_id, self.img_id_1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
