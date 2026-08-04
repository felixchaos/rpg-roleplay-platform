"""test_phase1_image_gen — Phase 1 生图基座 DB 运行时验证

6 项针对性验证：
1. 工具已注册 — generate_image 在 registry，scope/origins 正确
2. enqueue 链路 — ai_images pending 行 + chat_postproc_tasks image_gen 行
3. worker handler(mock provider) — 处理后 done + URL + 落盘文件
4. 缺 key → failed — status=failed，error 含 credentials_required
5. 确定性门控 — llm_chat 第1张入队，第2张入 pending_writes；ui_button 不受计数
6. 审批接入 — approve_pending_write(generate_image) → enqueue
"""
from __future__ import annotations

import asyncio
import json
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


def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _make_user(db) -> int:
    uname = f"p1test_{_rand()}"
    row = db.execute(
        """
        insert into users(username, display_name, password_hash, email)
        values (%s, %s, 'x', %s) returning id
        """,
        (uname, uname, f"{uname}@example.test"),
    ).fetchone()
    return int(row["id"])


def _make_save(db, user_id: int) -> int:
    """插一个最简 game_saves 行（executor 反查 user_id 需要它）。
    save_kind='tavern' 不受 chk_game_save_needs_script 约束。
    """
    row = db.execute(
        """
        insert into game_saves(user_id, title, state_path, save_kind)
        values (%s, %s, %s, 'tavern') returning id
        """,
        (user_id, f"save_{_rand()}", f"/tmp/save_{_rand()}.json"),
    ).fetchone()
    return int(row["id"])


# ══════════════════════════════════════════════════════════════════════
# 1. 工具已注册
# ══════════════════════════════════════════════════════════════════════

class TestToolRegistered(unittest.TestCase):
    """generate_image 工具注册正确性。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import init_db
        init_db()
        import tools_dsl.command_tools_register as r
        r.ensure_registered()

    def test_generate_image_in_registry(self):
        from tools_dsl.command_dispatcher import get_registry
        reg = get_registry()
        self.assertTrue(
            reg.has("generate_image"),
            "generate_image 不在 registry — ensure_registered() 未注册?",
        )

    def test_spec_scope_and_origins(self):
        from tools_dsl.command_dispatcher import get_registry
        spec = get_registry().get("generate_image")
        self.assertIsNotNone(spec)
        self.assertEqual(spec.scope, "save", f"scope 应为 'save'，实际={spec.scope!r}")
        self.assertIn("llm_chat", spec.origins,
                      f"origins 应含 'llm_chat'，实际={spec.origins}")
        self.assertIn("ui_button", spec.origins,
                      f"origins 应含 'ui_button'，实际={spec.origins}")
        self.assertIn("api_direct", spec.origins,
                      f"origins 应含 'api_direct'，实际={spec.origins}")


# ══════════════════════════════════════════════════════════════════════
# 2. enqueue 链路
# ══════════════════════════════════════════════════════════════════════

class TestEnqueueChain(unittest.TestCase):
    """enqueue_image_generation 建 ai_images + chat_postproc_tasks。"""

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

    def test_enqueue_returns_pending(self):
        from platform_app.image_jobs import enqueue_image_generation
        result = enqueue_image_generation(
            self.uid, "a cat on a rooftop", "chat",
            api_id="doubao", model="doubao-seedream-4-x",
        )
        self.assertIn("image_id", result, f"返回值缺 image_id: {result}")
        self.assertEqual(result["status"], "pending", f"status 应为 pending: {result}")
        self._image_id = result["image_id"]

    def test_ai_images_row_pending(self):
        from platform_app.db import connect
        from platform_app.image_jobs import enqueue_image_generation
        result = enqueue_image_generation(
            self.uid, "forest at dusk", "cover",
            api_id="doubao", model="doubao-seedream-4-x",
        )
        image_id = result["image_id"]
        with connect() as db:
            row = db.execute(
                "select status, prompt, user_id from ai_images where id = %s",
                (image_id,),
            ).fetchone()
        self.assertIsNotNone(row, f"ai_images 应有 id={image_id} 的行")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["prompt"], "forest at dusk")
        self.assertEqual(int(row["user_id"]), self.uid)

    def test_postproc_task_created(self):
        from platform_app.db import connect
        from platform_app.image_jobs import enqueue_image_generation
        result = enqueue_image_generation(
            self.uid, "snowy mountain peak", "game",
            api_id="doubao", model="doubao-seedream-4-x",
        )
        image_id = result["image_id"]
        with connect() as db:
            row = db.execute(
                """
                select task_kind, payload
                  from chat_postproc_tasks
                 where task_kind = 'image_gen'
                   and payload->>'image_id' = %s
                """,
                (str(image_id),),
            ).fetchone()
        self.assertIsNotNone(row, f"chat_postproc_tasks 应有 image_id={image_id} 的 image_gen 行")
        self.assertEqual(row["task_kind"], "image_gen")
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        self.assertEqual(int(payload["image_id"]), image_id)


# ══════════════════════════════════════════════════════════════════════
# 3. worker handler (mock provider)
# ══════════════════════════════════════════════════════════════════════

FAKE_PNG = b"\x89PNG\r\n\x1a\nFAKE"


class TestWorkerHandlerMock(unittest.TestCase):
    """handle_image_gen 走完整链路 (mock provider + mock credentials)。"""

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

    def test_handler_updates_done_and_writes_file(self):
        from platform_app.db import connect
        from platform_app.image_jobs import enqueue_image_generation, handle_image_gen

        # 先入队
        result = enqueue_image_generation(
            self.uid, "a bright galaxy", "chat",
            api_id="doubao", model="doubao-seedream-4-x",
        )
        image_id = result["image_id"]

        payload = {
            "image_id": image_id,
            "user_id": self.uid,
            "prompt": "a bright galaxy",
            "kind": "chat",
            "api_id": "doubao",
            "model": "doubao-seedream-4-x",
            "origin": "api_direct",
            "extra": {},
        }

        with patch(
            "agents.image_gen.dispatch.generate_image_bytes",
            return_value=[FAKE_PNG],
        ), patch(
            "platform_app.user_credentials.resolve_api_key",
            return_value={"key": "sk-test", "base_url_override": ""},
        ):
            asyncio.run(handle_image_gen(payload))

        with connect() as db:
            row = db.execute(
                "select status, url from ai_images where id = %s",
                (image_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "done",
                         f"status 应为 done，实际={row['status']!r}")
        url: str = row["url"]
        # W1 后 URL 格式统一为 /api/storage/ai_images/...（旧别名 /api/images/file/ 仍保留兼容）
        self.assertTrue(
            url.startswith("/api/storage/ai_images/") or url.startswith("/api/images/file/"),
            f"URL 应形如 /api/storage/ai_images/... 或 /api/images/file/...，实际={url!r}",
        )

        # 验证文件真正落盘
        from platform_app import storage as _storage
        if url.startswith("/api/storage/ai_images/"):
            filename = url[len("/api/storage/ai_images/"):]
        else:
            filename = url.split("/api/images/file/")[-1]
        fpath = _storage.resolve_path("ai_images/" + filename)
        self.assertTrue(fpath.exists(), f"磁盘文件不存在: {fpath}")
        self.assertEqual(fpath.read_bytes(), FAKE_PNG, "磁盘文件内容不符")

        # 清理测试文件
        fpath.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════
# 4. 缺 key → failed
# ══════════════════════════════════════════════════════════════════════

class TestWorkerNoKey(unittest.TestCase):
    """缺 key 时 handler 标 failed + error 含 credentials_required。"""

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

    def test_no_key_marks_failed(self):
        from platform_app.db import connect
        from platform_app.image_jobs import enqueue_image_generation, handle_image_gen

        result = enqueue_image_generation(
            self.uid, "a lonely lighthouse", "game",
            api_id="doubao", model="doubao-seedream-4-x",
        )
        image_id = result["image_id"]

        payload = {
            "image_id": image_id,
            "user_id": self.uid,
            "prompt": "a lonely lighthouse",
            "kind": "game",
            "api_id": "doubao",
            "model": "doubao-seedream-4-x",
            "origin": "api_direct",
            "extra": {},
        }

        # resolve_api_key 返回空 key（模拟未配置凭据）
        with patch(
            "platform_app.user_credentials.resolve_api_key",
            return_value={"key": "", "base_url_override": ""},
        ):
            asyncio.run(handle_image_gen(payload))

        with connect() as db:
            row = db.execute(
                "select status, error from ai_images where id = %s",
                (image_id,),
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "failed",
                         f"status 应为 failed，实际={row['status']!r}")
        self.assertIn("credentials_required", row["error"],
                      f"error 应含 credentials_required，实际={row['error']!r}")


# ══════════════════════════════════════════════════════════════════════
# 5. 确定性门控
# ══════════════════════════════════════════════════════════════════════

class _MinimalState:
    """最小 state 对象，只模拟 executor 需要的 state.data。"""
    def __init__(self, data: dict):
        self.data = data


class TestDeterministicGate(unittest.TestCase):
    """确定性门控：llm_chat 第1张入队，第2张入 pending_writes；ui_button 不受计数。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.uid = _make_user(db)
            cls.save_id = _make_save(db, cls.uid)

    @classmethod
    def tearDownClass(cls):
        with cls.connect() as db:
            db.execute("delete from users where id = %s", (cls.uid,))

    def _make_state(self):
        return _MinimalState({
            "_turn_images_generated": 0,
            "permissions": {},
        })

    def _count_ai_images(self) -> int:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select count(*) as c from ai_images where user_id = %s",
                (self.uid,),
            ).fetchone()
        return int(row["c"])

    def test_llm_chat_first_enqueues(self):
        """llm_chat 第1张：count 从 0 → 1，ai_images 新增一行。"""
        from tools_dsl.command_tools_image import _execute_generate_image

        state = self._make_state()
        before = self._count_ai_images()
        args = {
            "prompt": "test gate first image",
            "kind": "chat",
            "api_id": "doubao",
            "model": "doubao-seedream-4-x",
            "__call_origin__": "llm_chat",
            "save_id": self.save_id,
        }
        result = _execute_generate_image(state, args)
        after = self._count_ai_images()

        self.assertEqual(state.data["_turn_images_generated"], 1,
                         f"第1张后计数应为1，实际={state.data['_turn_images_generated']}")
        self.assertEqual(after, before + 1,
                         f"ai_images 应新增1行，before={before} after={after}")
        # 应返回 image_id 描述，不含 pending 字样
        self.assertNotIn("待审", result,
                         f"第1张不应进 pending，返回={result!r}")

    def test_llm_chat_second_enters_pending(self):
        """llm_chat 第2张：进 pending_writes，不新增 ai_images 行。"""
        from tools_dsl.command_tools_image import _execute_generate_image

        # 设置 count 已为 1（模拟第1张已生成）
        state = self._make_state()
        state.data["_turn_images_generated"] = 1

        before = self._count_ai_images()
        args = {
            "prompt": "test gate second image",
            "kind": "chat",
            "api_id": "doubao",
            "model": "doubao-seedream-4-x",
            "__call_origin__": "llm_chat",
            "save_id": self.save_id,
        }
        result = _execute_generate_image(state, args)
        after = self._count_ai_images()

        # ai_images 不应新增
        self.assertEqual(after, before,
                         f"第2张应被拦截，ai_images 不应新增行，before={before} after={after}")

        # pending_writes 应多一条 generate_image
        pending = state.data.get("permissions", {}).get("pending_writes", [])
        gen_pending = [p for p in pending if p.get("path") == "generate_image"]
        self.assertEqual(len(gen_pending), 1,
                         f"pending_writes 应有1条 generate_image，实际={pending}")
        self.assertEqual(gen_pending[0]["value"]["prompt"], "test gate second image")

        # 返回文案含门控字样
        self.assertIn("门控", result, f"返回文案应含门控字样，实际={result!r}")

    def test_ui_button_bypasses_gate(self):
        """ui_button origin 不受计数门控，即使 count 已 >=1 也直接入队。"""
        from tools_dsl.command_tools_image import _execute_generate_image

        state = self._make_state()
        state.data["_turn_images_generated"] = 5  # 假设已生成了 5 张

        before = self._count_ai_images()
        args = {
            "prompt": "ui button override test",
            "kind": "avatar",
            "api_id": "doubao",
            "model": "doubao-seedream-4-x",
            "__call_origin__": "ui_button",
            "save_id": self.save_id,
        }
        result = _execute_generate_image(state, args)
        after = self._count_ai_images()

        self.assertEqual(after, before + 1,
                         f"ui_button 应直接入队，ai_images 应新增1行，before={before} after={after}")

        # pending_writes 不应有新增
        pending = state.data.get("permissions", {}).get("pending_writes", [])
        self.assertEqual(len(pending), 0,
                         f"ui_button 不应入 pending_writes，实际={pending}")

        # 计数器不应改变
        self.assertEqual(state.data["_turn_images_generated"], 5,
                         f"ui_button 不应改变计数器，应仍为5，实际={state.data['_turn_images_generated']}")


# ══════════════════════════════════════════════════════════════════════
# 6. 审批接入
# ══════════════════════════════════════════════════════════════════════

class TestApproveImagePending(unittest.TestCase):
    """approve_pending_write 识别 generate_image path → 入队生图。"""

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

    def test_approve_enqueues_image(self):
        """构造 generate_image pending_write，approve 后 ai_images / chat_postproc_tasks 各增一行。"""
        from platform_app.db import connect
        from state._mixins.pending import _approve_image_pending

        # 构造 pending_write item（和 executor 存进去的格式一致）
        item = {
            "id": "test_pw_001",
            "path": "generate_image",
            "value": {
                "prompt": "approve test: ancient ruins",
                "kind": "cover",
                "api_id": "doubao",
                "model": "doubao-seedream-4-x",
                "extra": {},
                "user_id": self.uid,
                "__approved_origin__": "api_direct",
            },
            "source": "gm:image",
            "reason": "测试审批",
        }

        before_images = 0
        before_tasks = 0
        with connect() as db:
            row = db.execute(
                "select count(*) as c from ai_images where user_id = %s", (self.uid,)
            ).fetchone()
            before_images = int(row["c"])
            row = db.execute(
                "select count(*) as c from chat_postproc_tasks where user_id = %s and task_kind='image_gen'",
                (self.uid,),
            ).fetchone()
            before_tasks = int(row["c"])

        result = _approve_image_pending(item)

        with connect() as db:
            row = db.execute(
                "select count(*) as c from ai_images where user_id = %s", (self.uid,)
            ).fetchone()
            after_images = int(row["c"])
            row = db.execute(
                "select count(*) as c from chat_postproc_tasks where user_id = %s and task_kind='image_gen'",
                (self.uid,),
            ).fetchone()
            after_tasks = int(row["c"])

        self.assertEqual(after_images, before_images + 1,
                         f"approve 后 ai_images 应新增1行，before={before_images} after={after_images}")
        self.assertEqual(after_tasks, before_tasks + 1,
                         f"approve 后 chat_postproc_tasks 应新增1行，before={before_tasks} after={after_tasks}")
        self.assertIn("审批通过", result,
                      f"返回值应含审批通过，实际={result!r}")
        self.assertIn("image_id", result,
                      f"返回值应含 image_id，实际={result!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
