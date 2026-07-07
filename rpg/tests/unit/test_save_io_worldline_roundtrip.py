"""
test_save_io_worldline_roundtrip.py
====================================

矩阵审计 M6:platform_app/save_io.py 的 _STATE_TABLES 导出/导入白名单不含
game_sessions，导入分支只建空白 game_sessions 行 → 导入后 worldline 里玩家显式
设置全丢（steering_strength 退回默认 'guided'、user_progress_floor /
progress_chapter 丢失；progress 靠下回合自愈但 floor / steering 永久丢）。

修复（最小）：
  - export_save 单独把 game_sessions.worldline（按 save_id 单行）打包进导出
    payload 的 "game_session_worldline" 键（不动 _STATE_TABLES 通用机制）。
  - import_save 建 game_sessions 行时，若 payload 带该块，按 jsonb 白名单键
    （_WORLDLINE_SETTINGS_KEYS）回填，未知键一律丢弃（防注入）。
  - 向后兼容：老导出包（无 "game_session_worldline" 键）不受影响。

本测试不connect DB（沿用仓库里 test_continue_picker_uses_commit_activate.py /
test_branch_ops_advisory_lock.py 的源码结构断言风格），只读源码文本 + AST 断言
关键契约，因为本地测试环境不保证有可写 Postgres。
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

PROJECT_RPG = Path(__file__).resolve().parents[2]  # .../rpg
SAVE_IO_PATH = PROJECT_RPG / "platform_app" / "save_io.py"
SAVE_IO_SRC = SAVE_IO_PATH.read_text(encoding="utf-8")

SESSION_REPO_PATH = PROJECT_RPG / "platform_app" / "knowledge" / "_session_repo.py"
SESSION_REPO_SRC = SESSION_REPO_PATH.read_text(encoding="utf-8")


def _func_body(src: str, def_line: str) -> str:
    """提取从 `def_line` 开始到下一个顶层 `def ` 之前的源码片段。"""
    idx = src.find(def_line)
    assert idx >= 0, f"未找到函数定义: {def_line!r}"
    end = src.find("\ndef ", idx + 1)
    return src[idx: end if end > 0 else len(src)]


class ModuleParses(unittest.TestCase):
    """先确认改动后文件仍是合法 Python（compileall 的补充校验，AST 层面）。"""

    def test_ast_parses(self):
        ast.parse(SAVE_IO_SRC, filename=str(SAVE_IO_PATH))


class WhitelistKeysDefined(unittest.TestCase):
    """_WORLDLINE_SETTINGS_KEYS 必须存在，且与 knowledge/_session_repo.py 里
    _PRESERVE_SETTINGS_SQL 的「玩家可改设置」命名空间键集合一致（同一份契约的
    两处副本不能漂移，否则导出白名单和「跨回合 sticky」白名单各说各话）。"""

    _EXPECTED_KEYS = {
        "starting_worldline",
        "foreknowledge_mode",
        "npc_awareness",
        "steering_strength",
        "spoiler_guard",
        "progress_chapter",
        "user_progress_floor",
    }

    def test_constant_exists(self):
        self.assertIn("_WORLDLINE_SETTINGS_KEYS", SAVE_IO_SRC)

    def test_constant_matches_expected_keys(self):
        ns: dict = {}
        exec(
            compile(
                "_WORLDLINE_SETTINGS_KEYS = frozenset({\n"
                + SAVE_IO_SRC.split("_WORLDLINE_SETTINGS_KEYS: frozenset[str] = frozenset({", 1)[1]
                .split("})", 1)[0]
                + "})",
                "<extract>",
                "exec",
            ),
            ns,
        )
        self.assertEqual(ns["_WORLDLINE_SETTINGS_KEYS"], self._EXPECTED_KEYS)

    def test_matches_session_repo_preserve_settings_keys(self):
        """_PRESERVE_SETTINGS_SQL 的 in (...) 键列表应与本白名单同一集合。

        源码里该子句是拼接的多个字符串字面量(`"where key in "` 换行接
        `"(...)"`),不是单条连续文本 —— 直接从 `_PRESERVE_SETTINGS_SQL = (`
        到对应右括号截取整段拼接字符串,再从中提取所有引号包裹的 key,
        比对严格的单行正则更抗重排版。"""
        start = SESSION_REPO_SRC.find("_PRESERVE_SETTINGS_SQL = (")
        self.assertGreater(start, 0, "未在 _session_repo.py 找到 _PRESERVE_SETTINGS_SQL 定义")
        end = SESSION_REPO_SRC.find("\n)\n", start)
        self.assertGreater(end, start)
        block = SESSION_REPO_SRC[start:end]
        self.assertIn("where key in", block)
        raw_keys = re.findall(r"'([a-z_]+)'", block)
        self.assertEqual(set(raw_keys), self._EXPECTED_KEYS)


class ExportIncludesWorldlineBlock(unittest.TestCase):
    """export_save 必须查询 game_sessions.worldline 并打包进 game_session_worldline。"""

    def _export_save_body(self) -> str:
        return _func_body(SAVE_IO_SRC, "def export_save(")

    def test_selects_worldline_column(self):
        body = self._export_save_body()
        self.assertIn("worldline", body)
        # 必须真的 select 了 worldline 列，不是只提 id
        self.assertRegex(body, r"select\s+id,\s*worldline\s+from\s+game_sessions")

    def test_payload_has_game_session_worldline_key(self):
        body = self._export_save_body()
        self.assertIn('"game_session_worldline"', body)

    def test_does_not_touch_state_tables_mechanism(self):
        """最小修法要求：不把 game_sessions 塞进 _STATE_TABLES 通用机制。"""
        # _STATE_TABLES 定义本身不应包含 game_sessions
        state_tables_block = SAVE_IO_SRC.split("_STATE_TABLES: tuple[tuple[str, str], ...] = (", 1)[1]
        state_tables_block = state_tables_block.split(")\n", 1)[0]
        self.assertNotIn("game_sessions", state_tables_block)


class ImportAppliesWhitelistedWorldline(unittest.TestCase):
    """import_save 建 game_sessions 行后，必须按白名单过滤 payload 里的
    game_session_worldline 再写回，且未知键不能直接进 SQL。"""

    def _import_save_body(self) -> str:
        return _func_body(SAVE_IO_SRC, "def import_save(")

    def test_reads_game_session_worldline_from_payload(self):
        body = self._import_save_body()
        self.assertIn('payload.get("game_session_worldline")', body)

    def test_filters_by_whitelist_before_writing(self):
        body = self._import_save_body()
        # 必须看到用 _WORLDLINE_SETTINGS_KEYS 过滤 raw_wl 的字典推导式
        self.assertRegex(
            body,
            r"k\s*:\s*v\s+for\s+k,\s*v\s+in\s+raw_wl\.items\(\)\s+if\s+k\s+in\s+_WORLDLINE_SETTINGS_KEYS",
            "必须按白名单键过滤后才写入 worldline，防止未知键（含伪造/注入）落库",
        )

    def test_writes_via_jsonb_merge_not_full_overwrite(self):
        """用 `||` 合并而非整列覆盖，避免把新建空 worldline 里可能存在的其它字段冲掉
        （虽然此刻新建行 worldline 默认就是 {}，但合并语义更安全、也与
        _session_repo.py 的 sticky 合并写法一致）。"""
        body = self._import_save_body()
        self.assertIn("coalesce(worldline, '{}'::jsonb) ||", body)

    def test_session_row_created_even_without_messages(self):
        """M6 顺带修复:老代码建 game_sessions 行的逻辑嵌在 `if messages_raw:` 里，
        没消息的存档导入后连空白 session 行都没有 → worldline 无处可回填。
        修复后建 session 行必须在 messages_raw 判断之外。"""
        body = self._import_save_body()
        session_select_idx = body.find('"select id from game_sessions where save_id = %s order by id limit 1"')
        messages_if_idx = body.find("if messages_raw:")
        self.assertGreater(session_select_idx, 0)
        self.assertGreater(messages_if_idx, 0)
        self.assertLess(
            session_select_idx, messages_if_idx,
            "game_sessions 行的建立必须先于 `if messages_raw:` 判断，不能只在有消息时才建",
        )

    def test_backward_compat_missing_block_is_noop(self):
        """老导出包没有 game_session_worldline 键时，payload.get(...) 返回 None，
        isinstance(None, dict) 为 False → 整段回填逻辑短路，不抛异常、不产生 warning。"""
        body = self._import_save_body()
        self.assertIn("isinstance(raw_wl, dict) and raw_wl", body)


class RoundtripSemantics(unittest.TestCase):
    """不连 DB，用纯函数方式模拟 export → import 的白名单过滤这一段核心逻辑，
    验证注入的伪造键会被丢弃、合法键会被保留（对应 import_save 里的字典推导式）。"""

    def _whitelist(self) -> frozenset:
        ns: dict = {}
        exec(
            compile(
                "_WORLDLINE_SETTINGS_KEYS = frozenset({\n"
                + SAVE_IO_SRC.split("_WORLDLINE_SETTINGS_KEYS: frozenset[str] = frozenset({", 1)[1]
                .split("})", 1)[0]
                + "})",
                "<extract>",
                "exec",
            ),
            ns,
        )
        return ns["_WORLDLINE_SETTINGS_KEYS"]

    def test_legit_settings_survive_filter(self):
        keys = self._whitelist()
        raw_wl = {
            "steering_strength": "rail",
            "user_progress_floor": 12,
            "progress_chapter": 12,
            "foreknowledge_mode": "partial",
        }
        filtered = {k: v for k, v in raw_wl.items() if k in keys}
        self.assertEqual(filtered, raw_wl, "四个核心设置键必须全部通过白名单过滤")

    def test_unknown_or_forged_keys_are_dropped(self):
        keys = self._whitelist()
        raw_wl = {
            "steering_strength": "free",
            "user_variables": {"pwned": True},       # 世界树运行态键，不属于设置命名空间
            "last_projection": "; DROP TABLE x; --",  # 伪造/注入尝试
            "__proto__": "x",
        }
        filtered = {k: v for k, v in raw_wl.items() if k in keys}
        self.assertEqual(filtered, {"steering_strength": "free"})
        self.assertNotIn("user_variables", filtered)
        self.assertNotIn("last_projection", filtered)
        self.assertNotIn("__proto__", filtered)

    def test_empty_or_non_dict_worldline_block_is_falsy(self):
        """老导出包(v2 修复前生成的)没有该键 → get 返回 None;
        或该键存在但值不是 dict(畸形 payload)—— 两种情况回填逻辑都应视为空块跳过。"""
        for bogus in (None, [], "not-a-dict", 0, {}):
            with self.subTest(bogus=bogus):
                should_apply = isinstance(bogus, dict) and bool(bogus)
                self.assertFalse(should_apply)


if __name__ == "__main__":
    unittest.main(verbosity=2)
