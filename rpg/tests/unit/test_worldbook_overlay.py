"""
test_worldbook_overlay.py — task 107H: worldbook overlay 工具 + agent merge view 测试。

覆盖范围:
  · worldbook_add   — 成功路径、origin 拦截
  · worldbook_retire — 成功路径、validate base_entry_id、duplicate 幂等
  · worldbook_list_save_overlay — 列出 additions/retirements
  · load_effective_worldbook_for_save — merge view 逻辑（排除 retirement、加入 addition）
  · worldbook_agent.consult — 带 save_id 走 merge view（不返 retired entry）

所有 DB 操作用 unittest.mock 替身，不打真 DB。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import DEFAULT_STATE, GameState  # noqa: E402
from tools_dsl.command_dispatcher import (  # noqa: E402
    ToolCallEnvelope,
    ToolDispatcher,
    get_registry,
)
from tools_dsl.command_tools_register import force_reset_for_tests  # noqa: E402

# ────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────

def _new_state(turn=5, save_id=None) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    if save_id is not None:
        s.data["save_id"] = save_id
    return s


def _make_fetchone(data: dict | None):
    """返回一个 mock fetchone，使其返回真实 dict（psycopg dict_row 模式）。"""
    if data is None:
        return lambda: None
    # 使用真实 dict，支持 dict(row)、row["key"]、row.get("key")
    return lambda: dict(data)


def _make_fetchall(rows: list[dict]):
    # 返回真实 dict 列表，支持 dict(row) 和 row["key"]
    copied = [dict(d) for d in rows]
    return lambda: list(copied)


# ────────────────────────────────────────────────────────────
# worldbook_add 测试
# ────────────────────────────────────────────────────────────

class TestWorldbookAdd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state(turn=5)
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def _call(self, args, origin="ui_button", save_id=100):
        env = ToolCallEnvelope(
            user_id=1, save_id=save_id, tool="worldbook_add",
            args=args, origin=origin, trace_id="t-wb-add",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_add_success(self):
        """worldbook_add 成功插入并返回条目 id。"""
        inserted_row = {"id": 42}
        mock_cursor = MagicMock()
        mock_cursor.fetchone = _make_fetchone(inserted_row)
        mock_db = MagicMock()
        mock_db.execute.return_value = mock_cursor
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)

        with patch("platform_app.db.connect", return_value=mock_db), \
             patch("platform_app.db.init_db"):
            r = self._call({
                "save_id": 100,
                "title": "魔法石",
                "content": "一块蕴含古代魔力的石头",
                "keys": ["魔法石", "石头"],
                "priority": 60,
            })

        self.assertTrue(r.ok, r.error or r.result)
        self.assertIn("#42", r.result)
        self.assertIn("魔法石", r.result)

    def test_add_missing_title_rejected_by_dispatcher(self):
        """title 缺失时 dispatcher 在 required 检查阶段拒绝。"""
        r = self._call({"save_id": 100, "content": "内容"})
        self.assertFalse(r.ok)
        self.assertIn("title", r.error or "")

    def test_add_missing_content_rejected_by_dispatcher(self):
        r = self._call({"save_id": 100, "title": "X"})
        self.assertFalse(r.ok)
        self.assertIn("content", r.error or "")

    def test_add_missing_save_id_rejected_by_dispatcher(self):
        """save_id 不在 args 时，dispatcher required 检查拒绝。"""
        r = self._call({"title": "X", "content": "Y"})
        self.assertFalse(r.ok)
        self.assertIn("save_id", r.error or "")

    def test_add_blocked_from_wrong_origin(self):
        """worldbook_add 不在 _ADD_ORIGINS 的 origin 应被拦截（没有 mcp_call）。"""
        r = self._call(
            {"save_id": 100, "title": "X", "content": "Y"},
            origin="mcp_call",
        )
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", r.error or "")

    def test_add_allowed_from_llm_chat(self):
        """llm_chat 允许调 worldbook_add（non-destructive）。"""
        inserted_row = {"id": 7}
        mock_cursor = MagicMock()
        mock_cursor.fetchone = _make_fetchone(inserted_row)
        mock_db = MagicMock()
        mock_db.execute.return_value = mock_cursor
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)

        with patch("platform_app.db.connect", return_value=mock_db), \
             patch("platform_app.db.init_db"):
            r = self._call(
                {"save_id": 100, "title": "线索", "content": "NPC 的秘密"},
                origin="llm_chat",
                save_id=100,
            )
        self.assertTrue(r.ok, r.error or r.result)


# ────────────────────────────────────────────────────────────
# worldbook_retire 测试
# ────────────────────────────────────────────────────────────

class TestWorldbookRetire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state(turn=10)
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def _call(self, args, origin="ui_button", save_id=100):
        env = ToolCallEnvelope(
            user_id=1, save_id=save_id, tool="worldbook_retire",
            args=args, origin=origin, trace_id="t-wb-retire",
        )
        return self.dispatcher.dispatch_sync(env)

    def _make_db(self, *, save_script_id=9, entry_exists=True, retirement_exists=False):
        """构造满足常规场景的 mock DB。"""
        mock_db = MagicMock()
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)

        def _execute(sql, params=None):
            c = MagicMock()
            sql_lower = sql.lower()
            if "from game_saves" in sql_lower:
                c.fetchone = _make_fetchone({"script_id": save_script_id})
            elif "from worldbook_entries" in sql_lower:
                if entry_exists:
                    c.fetchone = _make_fetchone({"id": params[0] if params else 1})
                else:
                    c.fetchone = lambda: None
            elif "from save_worldbook_overlays" in sql_lower and "select id" in sql_lower:
                if retirement_exists:
                    c.fetchone = _make_fetchone({"id": 99})
                else:
                    c.fetchone = lambda: None
            elif "insert into save_worldbook_overlays" in sql_lower:
                c.fetchone = _make_fetchone({"id": 55})
            else:
                c.fetchone = lambda: None
                c.fetchall = lambda: []
            return c

        mock_db.execute = _execute
        return mock_db

    def test_retire_success(self):
        mock_db = self._make_db()
        with patch("platform_app.db.connect", return_value=mock_db), \
             patch("platform_app.db.init_db"):
            r = self._call({
                "save_id": 100,
                "base_entry_id": 7,
                "reason": "NPC 已死亡 turn 10",
            })
        self.assertTrue(r.ok, r.error or r.result)
        self.assertIn("#7", r.result)
        self.assertIn("已停用", r.result)

    def test_retire_entry_not_found(self):
        mock_db = self._make_db(entry_exists=False)
        with patch("platform_app.db.connect", return_value=mock_db), \
             patch("platform_app.db.init_db"):
            r = self._call({
                "save_id": 100,
                "base_entry_id": 999,
                "reason": "测试",
            })
        self.assertFalse(r.ok)
        self.assertIn("不存在", r.result or "")

    def test_retire_duplicate_idempotent(self):
        """重复 retire 同一 entry 返回已存在提示（不是 ok）。"""
        mock_db = self._make_db(retirement_exists=True)
        with patch("platform_app.db.connect", return_value=mock_db), \
             patch("platform_app.db.init_db"):
            r = self._call({
                "save_id": 100,
                "base_entry_id": 7,
                "reason": "重复操作",
            })
        # 返回 "已存在" 提示，ok=False（result 以 "已存在" 开头,不被 dispatcher 标为 ok）
        self.assertIn("已存在", r.result or "")

    def test_retire_blocked_from_llm_chat(self):
        """destructive 工具禁 llm_chat。"""
        r = self._call(
            {"save_id": 100, "base_entry_id": 1, "reason": "死了"},
            origin="llm_chat",
        )
        self.assertFalse(r.ok)
        # dispatcher 的 destructive_blocked 在 origin 检查之前（只要 llm_chat 不在 _RETIRE_ORIGINS 里）
        self.assertTrue(
            "origin_forbidden" in (r.error or "") or "destructive_blocked" in (r.error or ""),
            f"期望被拦截，实际 error={r.error!r}",
        )

    def test_retire_allowed_from_llm_chat_json_op(self):
        """llm_chat_json_op 允许 retire。"""
        mock_db = self._make_db()
        with patch("platform_app.db.connect", return_value=mock_db), \
             patch("platform_app.db.init_db"):
            r = self._call(
                {"save_id": 100, "base_entry_id": 7, "reason": "GM op 删除"},
                origin="llm_chat_json_op",
            )
        self.assertTrue(r.ok, r.error or r.result)


# ────────────────────────────────────────────────────────────
# worldbook_list_save_overlay 测试
# ────────────────────────────────────────────────────────────

class TestWorldbookListOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state()
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def _call(self, args, origin="ui_button"):
        env = ToolCallEnvelope(
            user_id=1, save_id=None, tool="worldbook_list_save_overlay",
            args=args, origin=origin, trace_id="t-wb-list",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_list_returns_additions_and_retirements(self):
        overlay_rows = [
            {
                "id": 1, "kind": "addition", "title": "神秘地图",
                "content": "一张被撕破的藏宝图",
                "keys": ["地图", "宝藏"], "priority": 60,
                "retired_entry_id": None, "retired_reason": "",
                "introduced_turn": 3,
            },
            {
                "id": 2, "kind": "retirement", "title": "",
                "content": "", "keys": [], "priority": 50,
                "retired_entry_id": 77, "retired_reason": "NPC 死亡",
                "introduced_turn": 8,
            },
        ]

        mock_cursor_save = MagicMock()
        mock_cursor_save.fetchone = _make_fetchone({"id": 100})
        mock_cursor_overlay = MagicMock()
        mock_cursor_overlay.fetchall = _make_fetchall(overlay_rows)

        call_count = [0]
        def _execute(sql, params=None):
            call_count[0] += 1
            if "from game_saves" in sql.lower():
                return mock_cursor_save
            return mock_cursor_overlay

        mock_db = MagicMock()
        mock_db.execute = _execute
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)

        with patch("platform_app.db.connect", return_value=mock_db), \
             patch("platform_app.db.init_db"):
            r = self._call({"save_id": 100})

        self.assertTrue(r.ok, r.error or r.result)
        data = json.loads(r.result)
        self.assertEqual(len(data["additions"]), 1)
        self.assertEqual(data["additions"][0]["title"], "神秘地图")
        self.assertEqual(data["additions"][0]["introduced_turn"], 3)
        self.assertEqual(len(data["retirements"]), 1)
        self.assertEqual(data["retirements"][0]["retired_entry_id"], 77)
        self.assertEqual(data["retirements"][0]["retired_reason"], "NPC 死亡")

    def test_list_allowed_from_llm_chat(self):
        """列表工具允许 llm_chat 读取（无 mock 路径会在 origin 检查之前/之后失败，但不被 origin 拦截）。"""
        ToolCallEnvelope(
            user_id=1, save_id=None, tool="worldbook_list_save_overlay",
            args={"save_id": 1}, origin="llm_chat", trace_id="t-wb-list-llm",
        )
        # origin 不应被 dispatcher 拦截（验证 origin 允许）
        spec = get_registry().get("worldbook_list_save_overlay")
        self.assertIsNotNone(spec)
        self.assertIn("llm_chat", spec.origins)

    def test_list_wrong_user_permission(self):
        """save 不属于当前用户应返回权限错误。"""
        mock_cursor_save = MagicMock()
        mock_cursor_save.fetchone = lambda: None  # 找不到该 user 的 save
        mock_db = MagicMock()
        mock_db.execute.return_value = mock_cursor_save
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)

        with patch("platform_app.db.connect", return_value=mock_db), \
             patch("platform_app.db.init_db"):
            r = self._call({"save_id": 999})

        self.assertFalse(r.ok)
        self.assertIn("权限", r.result or "")


# ────────────────────────────────────────────────────────────
# load_effective_worldbook_for_save 测试
# ────────────────────────────────────────────────────────────

class TestLoadEffectiveWorldbook(unittest.TestCase):
    """单元测试 load_effective_worldbook_for_save merge 逻辑。"""

    def _run(self, script_rows, overlay_rows):
        """构造 mock db 并调用 load_effective_worldbook_for_save。"""
        from agents.worldbook_agent import load_effective_worldbook_for_save

        mock_script_cursor = MagicMock()
        mock_script_cursor.fetchall = _make_fetchall(script_rows)

        mock_overlay_cursor = MagicMock()
        mock_overlay_cursor.fetchall = _make_fetchall(overlay_rows)

        call_count = [0]
        def _execute(sql, params=None):
            call_count[0] += 1
            if "from worldbook_entries" in sql.lower():
                return mock_script_cursor
            if "from save_worldbook_overlays" in sql.lower():
                return mock_overlay_cursor
            return MagicMock(fetchall=lambda: [], fetchone=lambda: None)

        mock_db = MagicMock()
        mock_db.execute = _execute

        return load_effective_worldbook_for_save(script_id=1, save_id=10, db=mock_db)

    def test_no_overlay(self):
        """没有任何 overlay 时，直接返回 script entries。"""
        script_rows = [
            {"id": 1, "title": "A", "content": "aa", "keys": [], "priority": 50},
            {"id": 2, "title": "B", "content": "bb", "keys": ["b"], "priority": 80},
        ]
        result = self._run(script_rows, [])
        self.assertEqual(len(result), 2)
        titles = [r["title"] for r in result]
        self.assertIn("A", titles)
        self.assertIn("B", titles)

    def test_retirement_excludes_entry(self):
        """retirement overlay 应从候选中排除对应的 script entry。"""
        script_rows = [
            {"id": 1, "title": "守门人", "content": "xxx", "keys": [], "priority": 50},
            {"id": 2, "title": "村长", "content": "yyy", "keys": [], "priority": 50},
        ]
        overlay_rows = [
            {
                "id": 10, "kind": "retirement",
                "title": "", "content": "", "keys": [], "priority": 50,
                "retired_entry_id": 1, "introduced_turn": 5,
            },
        ]
        result = self._run(script_rows, overlay_rows)
        titles = [r["title"] for r in result]
        self.assertNotIn("守门人", titles)  # retired
        self.assertIn("村长", titles)        # still active

    def test_addition_overlay_included(self):
        """addition overlay 应被加入候选。"""
        script_rows = [
            {"id": 1, "title": "A", "content": "aa", "keys": [], "priority": 50},
        ]
        overlay_rows = [
            {
                "id": 20, "kind": "addition",
                "title": "新法术", "content": "玩家在废墟中发现的古代咒语",
                "keys": ["法术", "咒语"], "priority": 70,
                "retired_entry_id": None, "introduced_turn": 3,
            },
        ]
        result = self._run(script_rows, overlay_rows)
        titles = [r["title"] for r in result]
        self.assertIn("A", titles)
        self.assertIn("新法术", titles)
        # addition 应标记来源
        addition = next(r for r in result if r["title"] == "新法术")
        self.assertEqual(addition.get("_source"), "addition")

    def test_priority_sort(self):
        """候选按 priority desc 排序。"""
        script_rows = [
            {"id": 1, "title": "low", "content": "x", "keys": [], "priority": 10},
            {"id": 2, "title": "high", "content": "x", "keys": [], "priority": 90},
        ]
        overlay_rows = [
            {
                "id": 5, "kind": "addition",
                "title": "mid", "content": "x", "keys": [], "priority": 50,
                "retired_entry_id": None, "introduced_turn": 1,
            },
        ]
        result = self._run(script_rows, overlay_rows)
        priorities = [r["priority"] for r in result]
        self.assertEqual(priorities, sorted(priorities, reverse=True))

    def test_retirement_and_addition_combined(self):
        """同时有 retirement 和 addition 时，逻辑正确合并。"""
        script_rows = [
            {"id": 1, "title": "死去的 NPC", "content": "x", "keys": [], "priority": 50},
            {"id": 2, "title": "活着的 NPC", "content": "x", "keys": [], "priority": 50},
        ]
        overlay_rows = [
            {
                "id": 10, "kind": "retirement",
                "title": "", "content": "", "keys": [], "priority": 50,
                "retired_entry_id": 1, "introduced_turn": 7,
            },
            {
                "id": 11, "kind": "addition",
                "title": "新地点", "content": "玩家发现的隐藏村庄",
                "keys": ["隐藏村庄"], "priority": 65,
                "retired_entry_id": None, "introduced_turn": 7,
            },
        ]
        result = self._run(script_rows, overlay_rows)
        titles = [r["title"] for r in result]
        self.assertNotIn("死去的 NPC", titles)
        self.assertIn("活着的 NPC", titles)
        self.assertIn("新地点", titles)


# ────────────────────────────────────────────────────────────
# worldbook_agent.consult 与 save_id 集成测试
# ────────────────────────────────────────────────────────────

class TestConsultWithSaveId(unittest.TestCase):
    """验证 consult 传 save_id 时走 merge view，retired entry 不出现。"""

    def _make_db(self, *, script_rows, overlay_rows, anchor_row=None):
        def _execute(sql, params=None):
            c = MagicMock()
            sql_l = sql.lower()
            if "from phase_digests" in sql_l:
                if anchor_row:
                    c.fetchone = _make_fetchone(anchor_row)
                else:
                    c.fetchone = lambda: None
            elif "from chapter_facts" in sql_l:
                c.fetchall = lambda: []
            elif "from worldbook_entries" in sql_l:
                c.fetchall = _make_fetchall(script_rows)
            elif "from save_worldbook_overlays" in sql_l:
                c.fetchall = _make_fetchall(overlay_rows)
            else:
                c.fetchone = lambda: None
                c.fetchall = lambda: []
            return c

        mock_db = MagicMock()
        mock_db.execute = _execute
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        return mock_db

    def test_consult_without_save_id_uses_script_entries(self):
        """不传 save_id 时，consult 直接查 worldbook_entries（旧路径）。"""
        from agents.worldbook_agent import consult

        script_rows = [
            {"id": 1, "title": "活 NPC", "content": "活", "keys": ["活"], "priority": 95},
        ]
        mock_db = self._make_db(script_rows=script_rows, overlay_rows=[])

        with patch("platform_app.db.connect", return_value=mock_db):
            result = consult(script_id=1, query="活")
        titles = [e["title"] for e in result.worldbook_entries]
        self.assertIn("活 NPC", titles)

    def test_consult_with_save_id_excludes_retired(self):
        """传 save_id 时，consult 通过 merge view 排除被 retired 的 entry。"""
        from agents.worldbook_agent import consult

        script_rows = [
            {"id": 1, "title": "死去的 NPC", "content": "死", "keys": ["死去"], "priority": 95},
            {"id": 2, "title": "活着的 NPC", "content": "活", "keys": ["活"], "priority": 90},
        ]
        overlay_rows = [
            {
                "id": 10, "kind": "retirement",
                "title": "", "content": "", "keys": [], "priority": 50,
                "retired_entry_id": 1, "introduced_turn": 5,
            },
        ]
        mock_db = self._make_db(script_rows=script_rows, overlay_rows=overlay_rows)

        with patch("platform_app.db.connect", return_value=mock_db):
            result = consult(script_id=1, query="死去 活", save_id=10)

        titles = [e["title"] for e in result.worldbook_entries]
        self.assertNotIn("死去的 NPC", titles,
                         "retired entry 不应出现在 consult 结果里")
        self.assertIn("活着的 NPC", titles)

    def test_consult_with_save_id_includes_addition(self):
        """传 save_id 时，consult 结果包含 addition overlay。"""
        from agents.worldbook_agent import consult

        script_rows = [
            {"id": 1, "title": "剧本 NPC", "content": "x", "keys": ["剧本"], "priority": 50},
        ]
        overlay_rows = [
            {
                "id": 5, "kind": "addition",
                "title": "玩家发现的法器", "content": "一把锈迹斑斑的剑",
                "keys": ["法器", "剑"], "priority": 95,
                "retired_entry_id": None, "introduced_turn": 3,
            },
        ]
        mock_db = self._make_db(script_rows=script_rows, overlay_rows=overlay_rows)

        with patch("platform_app.db.connect", return_value=mock_db):
            result = consult(script_id=1, query="法器 剑", save_id=10)

        titles = [e["title"] for e in result.worldbook_entries]
        self.assertIn("玩家发现的法器", titles,
                      "addition overlay 应被 consult 返回")


if __name__ == "__main__":
    unittest.main()
