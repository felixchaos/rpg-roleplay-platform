"""
test_e2e_memory_invariant.py — task 73：codex 记忆架构评审 §7.6 落地

核心 invariant（来自 codex 评审）：
> 玩家看到的故事、右侧状态栏、数据库状态、下一轮 GM 上下文，四者必须一致。
> 只要这四者不同步，LLM 记忆就会开始"漂"。

本测试覆盖 4 个关键场景，每个都验证：
- 通过 API 写入 → /api/state 反映 → 强制 reload 后仍一致
- 后端 _state_by_user 缓存清掉、走 state_repository 从 DB 重读
- 数据库存的内容 = API 返回的内容

不依赖 LLM；用直接调用 state machine + /api/save 持久化的方式模拟
chat 内部对 GameState 的修改（绕开 /api/chat 的 LLM 调用），然后通过 API
回读验证。这保证了 state machine ↔ DB ↔ API 三层一致。

新增 task 74-78 之前先建立这个保护网，让 MemoryItem 重构有 regression
检测能力。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# 让本测试也可独立运行
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")

from tests.helpers import (  # noqa: E402
    cleanup_test_users,
    make_client,
    register_user,
)


def _get_user_id(client, cookies) -> int:
    """从 /api/auth/me 拿当前 user_id"""
    r = client.get("/api/auth/me", cookies=cookies)
    assert r.status_code == 200, f"/api/auth/me failed: {r.text}"
    return int(r.json()["user"]["id"])


def _runtime_state(user_id: int):
    """直接拿 ui._state_by_user 里这个用户的 GameState（绕过 LLM 改 state）"""
    import app as _ui
    state = _ui._state_by_user.get(user_id)
    assert state is not None, f"user {user_id} 没有活跃 runtime（API 没初始化？）"
    return state


def _invalidate_runtime(user_id: int) -> None:
    """模拟"用户重新打开页面"——清掉 runtime 缓存，下次 /api/state 会从 DB 重读"""
    import app as _ui
    fake_user = {"id": user_id}
    _ui._invalidate_user_cache(fake_user)


class MemoryInvariantE2E(unittest.TestCase):
    """codex §7.6 的 4 类场景。"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _setup_user_with_save(self):
        """注册 + 创建一个新存档，返回 (cookies, user_id)"""
        u = register_user(self.client)
        self.assertEqual(u["status"], 200, f"register failed: {u['body']}")
        cookies = u["cookies"]
        # 初次拉一次 /api/state，触发 _ensure_loaded → 在 _state_by_user 注册
        r = self.client.get("/api/state", cookies=cookies)
        self.assertEqual(r.status_code, 200, f"initial /api/state failed: {r.text}")
        user_id = _get_user_id(self.client, cookies)
        return cookies, user_id

    # ────────────────────────────────────────────────────────────────────
    def test_set_persists_through_reload(self):
        """场景 A：/set 写入 → 持久化 → reload 后 API 仍返回新值"""
        cookies, user_id = self._setup_user_with_save()

        # 通过直接操作 in-process GameState 来模拟 GM/玩家的 /set 落地
        # （绕开 LLM 但走真正的 state machine 路径）
        state = _runtime_state(user_id)
        state.apply_player_directives("/set memory.main_quest=营救沈知微")
        # 把 runtime 状态持久化到 DB（同 /api/save 路径）
        r_save = self.client.post("/api/save", cookies=cookies)
        self.assertEqual(r_save.status_code, 200, f"save failed: {r_save.text}")

        # 第一次验证：还在内存里时 /api/state 反映
        r1 = self.client.get("/api/state", cookies=cookies)
        self.assertEqual(r1.status_code, 200)
        body1 = r1.json()
        main_quest_before = (body1.get("memory") or body1.get("state", {}).get("memory") or {}).get("main_quest")
        self.assertEqual(main_quest_before, "营救沈知微",
            f"内存中 /set 未反映: {body1}")

        # 关键：清缓存，强制下一次 /api/state 从 DB 重读
        _invalidate_runtime(user_id)
        r2 = self.client.get("/api/state", cookies=cookies)
        self.assertEqual(r2.status_code, 200)
        body2 = r2.json()
        main_quest_after = (body2.get("memory") or body2.get("state", {}).get("memory") or {}).get("main_quest")
        self.assertEqual(main_quest_after, "营救沈知微",
            f"reload 后 /set 丢失（DB 持久化或读路径出错）: {body2}")

    # ────────────────────────────────────────────────────────────────────
    def test_pending_jump_visible_and_world_time_unchanged(self):
        """场景 B：时间跳跃请求 → /api/state 显示 pending，world.time 未被改"""
        cookies, user_id = self._setup_user_with_save()
        state = _runtime_state(user_id)

        # 记录初始时间
        original_time = state.data["world"].get("time", "")

        # 玩家自然语言请求跳跃 → 应进入 pending_confirmation
        state.apply_player_directives("请把剧情时间推进到次日清晨")
        timeline = state.data["world"].get("timeline") or {}
        self.assertEqual(timeline.get("anchor_state"), "pending_confirmation",
            f"自然语言跳跃应入 pending: {timeline}")

        # 持久化
        r_save = self.client.post("/api/save", cookies=cookies)
        self.assertEqual(r_save.status_code, 200)

        # API 应反映 pending + world.time 未变
        r = self.client.get("/api/state", cookies=cookies)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        world = body.get("world") or body.get("state", {}).get("world") or {}
        self.assertEqual(world.get("time"), original_time,
            f"pending 期间 world.time 不应被改写: {world}")
        tl_api = world.get("timeline") or {}
        self.assertEqual(tl_api.get("anchor_state"), "pending_confirmation",
            f"API 应暴露 pending_confirmation 状态: {tl_api}")
        self.assertEqual((tl_api.get("pending_jump") or {}).get("to"), "次日清晨",
            f"pending_jump.to 应保留目标: {tl_api}")

        # reload 后仍一致
        _invalidate_runtime(user_id)
        r2 = self.client.get("/api/state", cookies=cookies)
        body2 = r2.json()
        world2 = body2.get("world") or body2.get("state", {}).get("world") or {}
        tl2 = world2.get("timeline") or {}
        self.assertEqual(world2.get("time"), original_time, "reload 后时间应仍未推进")
        self.assertEqual(tl2.get("anchor_state"), "pending_confirmation",
            "reload 后 pending 应仍存在")

    # ────────────────────────────────────────────────────────────────────
    def test_confirm_jump_locks_and_persists(self):
        """场景 C：confirm 时间跳跃 → 锁定 → reload 后仍锁定，pending 已清"""
        cookies, user_id = self._setup_user_with_save()
        state = _runtime_state(user_id)

        # 1. 请求跳跃进 pending
        state.apply_player_directives("请把剧情时间推进到次日清晨")
        self.assertEqual(state.data["world"]["timeline"].get("anchor_state"),
                         "pending_confirmation")

        # 2. 模拟 GM 下一轮显式确认（task 35：必须 turn 推进后才允许 confirm）
        state.data["turn"] = int(state.data.get("turn", 0)) + 1
        state.apply_structured_updates("好的，时间正式推进到次日清晨。【时间跳跃确认：次日清晨】")

        # 应已锁定
        self.assertEqual(state.data["world"]["time"], "次日清晨",
            f"confirm 后 world.time 应锁到目标: {state.data['world']}")
        self.assertEqual(state.data["world"]["timeline"].get("anchor_state"), "locked")
        self.assertIsNone(state.data["world"]["timeline"].get("pending_jump"),
            "confirm 后 pending_jump 应被清")

        # 持久化
        r_save = self.client.post("/api/save", cookies=cookies)
        self.assertEqual(r_save.status_code, 200)

        # reload 后验证：API 应继续显示锁定 + 无 pending
        _invalidate_runtime(user_id)
        r = self.client.get("/api/state", cookies=cookies)
        body = r.json()
        world = body.get("world") or body.get("state", {}).get("world") or {}
        self.assertEqual(world.get("time"), "次日清晨", f"reload 后时间锁定丢失: {world}")
        tl = world.get("timeline") or {}
        self.assertEqual(tl.get("anchor_state"), "locked")
        self.assertIsNone(tl.get("pending_jump"), "reload 后 pending_jump 应仍是 None")

    # ────────────────────────────────────────────────────────────────────
    def test_audit_log_persists_through_reload(self):
        """场景 D：audit_log 条目跨 reload 仍可见
        (task 60+65 给 AuditLogView 写入的 parse_error/extractor_error 也走这条路径)
        """
        cookies, user_id = self._setup_user_with_save()
        state = _runtime_state(user_id)

        # 直接走 apply_state_write 一次正常写入（会产生 kind=write 的 audit 条目）
        state.apply_state_write("memory.main_quest=拯救阿衡", source="gm")
        # 再触发一次 parse_error（无 path）
        state.apply_state_write("乱写一通没等号", source="gm")
        # 再触发一次 hard_forbidden（permissions.* 是黑名单）
        state.apply_state_write("permissions.mode=full_access", source="user", force=True)

        # 内存里应该有 3 条 audit 条目（write / parse_error / hard_forbidden）
        audit_mem = state.data.get("permissions", {}).get("audit_log") or []
        kinds_mem = [a.get("kind") or ("write" if a.get("path") and not a.get("blocked") else a.get("blocked"))
                     for a in audit_mem]
        self.assertIn("parse_error", [a.get("kind") for a in audit_mem],
            f"应记录 parse_error: {audit_mem}")
        self.assertIn("hard_forbidden", [a.get("blocked") for a in audit_mem],
            f"应记录 hard_forbidden: {audit_mem}")

        # 持久化 + reload
        r_save = self.client.post("/api/save", cookies=cookies)
        self.assertEqual(r_save.status_code, 200)
        _invalidate_runtime(user_id)
        r = self.client.get("/api/state", cookies=cookies)
        body = r.json()
        perms = body.get("permissions") or body.get("state", {}).get("permissions") or {}
        audit_api = perms.get("audit_log") or []
        kinds_api = [a.get("kind") for a in audit_api]
        blocked_api = [a.get("blocked") for a in audit_api]
        # 数量 + 种类要一致
        self.assertGreaterEqual(len(audit_api), 2,
            f"reload 后至少应保留 parse_error+hard_forbidden 两条: {audit_api}")
        self.assertIn("parse_error", kinds_api,
            f"reload 后 parse_error 丢失: {audit_api}")
        self.assertIn("hard_forbidden", blocked_api,
            f"reload 后 hard_forbidden 丢失: {audit_api}")


class MemoryItemBackfillMigration(unittest.TestCase):
    """task 83（codex §7.1 phase B）：旧存档 backfill 迁移单元测试。

    task 74 只做 dual-write：新写入同时落 legacy facts/notes/pinned/abilities/
    resources 和结构化 items。但**旧存档**里 memory.items 还是空（迁移没回填）。
    task 83 在 GameState._migrate 里加一段 backfill：items 为空 + legacy 任一
    字段有内容 → 把 legacy 数组转成 MemoryItem 注入 items（kind=runtime_fact,
    source=legacy_migration_v1, turn=0），同时保留 legacy 字段不动（dual-read
    兼容期）。

    这测纯粹走 _migrate 静态方法，不需要 API client / DB。
    """

    def test_legacy_facts_backfill_into_items(self):
        """旧 shape state（memory.items=[], memory.facts=[...]）过 _migrate 后：
        - items 长度 == 2
        - 每条 kind=='runtime_fact', source=='legacy_migration_v1',
          legacy_bucket=='facts', status=='active', turn==0
        - 原 facts 数组仍是 ['事实A','事实B']（未删，dual-read 兼容）
        """
        # 延迟 import 避免依赖 RPG_REQUIRE_AUTH 等环境（这测不走 API）
        from state import GameState  # noqa: E402

        old_shape = {
            "memory": {
                "items": [],
                "facts": ["事实A", "事实B"],
            },
        }
        state = GameState(old_shape)
        memory = state.data.get("memory", {})
        items = memory.get("items", [])

        # 长度断言
        self.assertEqual(len(items), 2,
            f"backfill 后 items 应有 2 条: {items}")

        # 每条字段断言
        for item in items:
            self.assertEqual(item.get("kind"), "runtime_fact",
                f"kind 应为 runtime_fact: {item}")
            self.assertEqual(item.get("source"), "legacy_migration_v1",
                f"source 应为 legacy_migration_v1: {item}")
            self.assertEqual(item.get("legacy_bucket"), "facts",
                f"legacy_bucket 应为 facts: {item}")
            self.assertEqual(item.get("status"), "active",
                f"status 应为 active: {item}")
            self.assertEqual(item.get("turn"), 0,
                f"turn 应为 0（旧数据无 turn 可考）: {item}")
            self.assertTrue(item.get("id", "").startswith("mem_"),
                f"id 应以 mem_ 前缀: {item}")
            self.assertTrue(item.get("ts"),
                f"ts 不应为空: {item}")

        # text 内容应匹配原 facts（按顺序）
        texts = [i.get("text") for i in items]
        self.assertEqual(texts, ["事实A", "事实B"],
            f"items text 应按 facts 顺序: {texts}")

        # 原 facts 数组未删（dual-read 兼容）
        self.assertEqual(memory.get("facts"), ["事实A", "事实B"],
            f"legacy facts 数组不应被删: {memory.get('facts')}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
