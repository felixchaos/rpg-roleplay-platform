"""
test_phase_digest_agent.py — task 107D 单元/集成测试。

覆盖:
  1. _parse_json: 各种 LLM 输出格式 (裸 JSON / ```json fence / 包噪声) 都能抠出 dict
  2. _normalize_digest: 截断 / 限制条数 / 容错缺字段
  3. compact_phase: 用 mock backend 跑端到端 (建 save + 3 commits + open phase → 摘要 → 验 DB)
  4. compact_phase: short-circuit (已 closed + 有 summary, force=False 不重做)
  5. compact_phase: LLM 调用失败时返 {"error": ...},不抛
  6. compact_phase: 第一次 LLM 输出无效 JSON, 第二次成功 → 整体 OK
  7. compact_phase: 两次都失败 → 抛 ValueError

不调真实 LLM (那个走 scripts/llm_e2e_test_phase_digest.py 一次性手测)。
DB 走真实 Postgres,用户名前缀 integtest_,跑完清理。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

# 让测试能 import 顶层模块
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.helpers import (  # noqa: E402
    cleanup_test_users,
    integtest_username,
    make_client,
    register_user,
)

# ────────────────────────────────────────────────────────────
#  纯函数: 不需 DB
# ────────────────────────────────────────────────────────────


class ParseJsonTests(unittest.TestCase):
    def test_raw_json_object(self):
        from agents.phase_digest_agent import _parse_json
        out = _parse_json('{"summary": "hi", "key_events": []}')
        self.assertIsInstance(out, dict)
        self.assertEqual(out["summary"], "hi")

    def test_json_in_markdown_fence(self):
        from agents.phase_digest_agent import _parse_json
        text = '好的, 这是结果:\n\n```json\n{"summary":"测试","key_events":[]}\n```\n'
        out = _parse_json(text)
        self.assertIsInstance(out, dict)
        self.assertEqual(out["summary"], "测试")

    def test_json_with_leading_garbage(self):
        from agents.phase_digest_agent import _parse_json
        text = '某些前导文字 {"summary":"x","emotion_arc":"a→b"} 一些尾部'
        out = _parse_json(text)
        self.assertEqual(out["summary"], "x")
        self.assertEqual(out["emotion_arc"], "a→b")

    def test_invalid_returns_none(self):
        from agents.phase_digest_agent import _parse_json
        self.assertIsNone(_parse_json(""))
        self.assertIsNone(_parse_json("纯文本没 JSON"))
        self.assertIsNone(_parse_json("[1,2,3]"))  # 是 list 不是 dict


class NormalizeDigestTests(unittest.TestCase):
    def test_full_valid_input(self):
        from agents.phase_digest_agent import _normalize_digest
        d = _normalize_digest({
            "summary": "一段摘要",
            "key_events": [{"turn": 3, "summary": "事件"}],
            "key_npcs": [{"name": "阿衡", "first_turn": 1, "role": "医师",
                          "current_status": "信任"}],
            "key_locations": ["雾港", "灯塔"],
            "key_decisions": [{"turn": 5, "choice": "拒绝贿赂",
                                "consequence": "失警局长好感"}],
            "emotion_arc": "好奇 → 怀疑 → 坚定",
        })
        self.assertEqual(d["summary"], "一段摘要")
        self.assertEqual(len(d["key_events"]), 1)
        self.assertEqual(d["key_locations"], ["雾港", "灯塔"])

    def test_truncates_overflow(self):
        from agents.phase_digest_agent import _normalize_digest
        d = _normalize_digest({
            "summary": "x" * 5000,
            "key_events": [{"turn": i, "summary": "e"} for i in range(100)],
            "key_npcs": [{"name": f"n{i}"} for i in range(50)],
            "key_locations": [f"loc{i}" for i in range(50)],
            "key_decisions": [{"turn": i, "choice": "c"} for i in range(50)],
            "emotion_arc": "y" * 1000,
        })
        self.assertLessEqual(len(d["summary"]), 2000)
        self.assertEqual(len(d["key_events"]), 5)
        self.assertEqual(len(d["key_npcs"]), 8)
        self.assertEqual(len(d["key_locations"]), 6)
        self.assertEqual(len(d["key_decisions"]), 5)
        self.assertLessEqual(len(d["emotion_arc"]), 200)

    def test_coerces_turn_to_int(self):
        from agents.phase_digest_agent import _normalize_digest
        d = _normalize_digest({
            "summary": "x",
            "key_events": [{"turn": "5", "summary": "ok"},
                            {"turn": "junk", "summary": "fallback"}],
        })
        self.assertEqual(d["key_events"][0]["turn"], 5)
        self.assertEqual(d["key_events"][1]["turn"], 0)

    def test_handles_missing_fields(self):
        from agents.phase_digest_agent import _normalize_digest
        d = _normalize_digest({"summary": "只有 summary"})
        self.assertEqual(d["summary"], "只有 summary")
        self.assertEqual(d["key_events"], [])
        self.assertEqual(d["key_npcs"], [])
        self.assertEqual(d["key_locations"], [])
        self.assertEqual(d["key_decisions"], [])
        self.assertEqual(d["emotion_arc"], "")

    def test_filters_non_dict_items(self):
        from agents.phase_digest_agent import _normalize_digest
        d = _normalize_digest({
            "summary": "x",
            "key_events": ["not a dict", {"turn": 1, "summary": "ok"}, 123],
            "key_locations": ["loc", 42, "", None, {"bad": "dict"}],
        })
        self.assertEqual(len(d["key_events"]), 1)
        self.assertIn("loc", d["key_locations"])
        self.assertIn("42", d["key_locations"])
        self.assertNotIn("", d["key_locations"])
        # dict 不应被当成 location
        self.assertNotIn({"bad": "dict"}, d["key_locations"])


# ────────────────────────────────────────────────────────────
#  集成: 真实 DB + mock backend
# ────────────────────────────────────────────────────────────


class _FakeBackend:
    """模拟 _VertexBackend, 只实现 .call_structured()。

    behavior 控制返回内容:
      - "happy"   : 返合法 JSON object
      - "garbage" : 永远返垃圾, 触发重试 → 仍失败 → ValueError
      - "retry"   : 第一次返垃圾, 第二次返合法 JSON
      - "raise"   : 调用就 raise (模拟网络异常)
    """
    def __init__(self, behavior: str = "happy"):
        self.behavior = behavior
        self.call_count = 0
        self.last_system = ""
        self.last_user = ""
        self.model_name = "<fake-vertex-flash>"

    def call_structured(self, system: str, messages: list[dict], max_tokens: int) -> str:
        self.call_count += 1
        self.last_system = system
        self.last_user = messages[-1]["content"] if messages else ""
        if self.behavior == "raise":
            raise RuntimeError("simulated network failure")
        if self.behavior == "garbage":
            return "不是 JSON 这是中文"
        if self.behavior == "retry" and self.call_count == 1:
            return "again not json"
        return json.dumps({
            "summary": "玩家在雾港码头与守夜人相遇,发现一枚刻字怀表,顺线索找到了医师沈知微。"
                       "对话中沈知微透露码头近期有走私船活动,玩家决定夜里潜入仓库一探究竟。",
            "key_events": [
                {"turn": 1, "summary": "玩家在码头捡到怀表"},
                {"turn": 2, "summary": "拜访医师沈知微"},
                {"turn": 3, "summary": "决定夜潜仓库"},
            ],
            "key_npcs": [
                {"name": "沈知微", "first_turn": 2, "role": "雾港医师",
                 "current_status": "对玩家有保留信任"},
            ],
            "key_locations": ["雾港码头", "沈知微医馆"],
            "key_decisions": [
                {"turn": 3, "choice": "夜潜仓库", "consequence": "暴露给走私者警戒"},
            ],
            "emotion_arc": "好奇 → 警觉 → 决断",
        }, ensure_ascii=False)


class CompactPhaseIntegration(unittest.TestCase):
    """端到端: 真插 game_saves + branch_commits + save_phase_digests, 跑 compact_phase。"""

    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _seed(self, *, n_commits: int = 3, with_phase: bool = True) -> tuple[int, int, int]:
        """返回 (user_id, save_id, phase_index)。"""
        from platform_app.db import connect

        u = register_user(self.client)
        with connect() as db:
            uid_row = db.execute(
                "select id from users where username = %s", (u["username"],),
            ).fetchone()
            uid = int(uid_row["id"])
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, f"integtest_pd_{integtest_username()}"),
            ).fetchone()
            script_id = int(scr["id"])
            sv = db.execute(
                """insert into game_saves(user_id, script_id, title, state_path)
                   values (%s,%s,%s,%s) returning id""",
                (uid, script_id, "pd-test-save", "/tmp/_pd_test.json"),
            ).fetchone()
            save_id = int(sv["id"])

            for t in range(1, n_commits + 1):
                db.execute(
                    """
                    insert into branch_commits
                        (save_id, object_hash, turn_index, kind, title,
                         player_input, gm_output)
                    values (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (save_id, f"pd_test_oh_{save_id}_{t}", t, "user",
                     f"t{t}",
                     f"玩家发言 turn {t}",
                     f"GM 在 turn {t} 这样回应玩家,描述了一段剧情……"),
                )

            phase_index = 0
            if with_phase:
                db.execute(
                    """insert into save_phase_digests
                       (save_id, phase_index, turn_start, turn_end,
                        phase_label, story_time_label, status, generated_by)
                       values (%s,0,%s,%s,%s,%s,'open','llm')""",
                    (save_id, 1, n_commits, "雾港初探", "1903-08-15 黄昏"),
                )
        return uid, save_id, phase_index

    def test_happy_path_writes_all_fields(self):
        from agents.phase_digest_agent import compact_phase
        from platform_app.db import connect

        uid, save_id, phase_index = self._seed(n_commits=3)
        fake = _FakeBackend("happy")
        result = compact_phase(save_id, phase_index, user_id=uid,
                               force=False, _backend=fake)

        self.assertNotIn("error", result, msg=str(result))
        self.assertEqual(result["save_id"], save_id)
        self.assertEqual(result["phase_index"], 0)
        self.assertIn("怀表", result["summary"])
        self.assertEqual(len(result["key_events"]), 3)
        self.assertEqual(result["emotion_arc"], "好奇 → 警觉 → 决断")
        self.assertEqual(result["commit_count"], 3)
        self.assertEqual(fake.call_count, 1)

        # DB 实际写入
        with connect() as db:
            row = db.execute(
                "select status, summary, key_events, key_npcs, key_locations, "
                "       key_decisions, emotion_arc, metadata, generated_by "
                "  from save_phase_digests where save_id=%s and phase_index=%s",
                (save_id, phase_index),
            ).fetchone()
            self.assertEqual(row["status"], "closed")
            self.assertEqual(row["generated_by"], "llm")
            self.assertIn("怀表", row["summary"])
            self.assertEqual(row["emotion_arc"], "好奇 → 警觉 → 决断")
            self.assertEqual(len(row["key_events"]), 3)
            self.assertEqual(len(row["key_npcs"]), 1)
            self.assertEqual(len(row["key_locations"]), 2)
            self.assertEqual(len(row["key_decisions"]), 1)
            meta = row["metadata"] or {}
            self.assertEqual(meta.get("needs_rebuild"), False)
            self.assertTrue(meta.get("last_compact_model"))

            # branch_commits 被标记
            commits = db.execute(
                "select turn_index, digested_in_phase, digest_at "
                "  from branch_commits where save_id=%s order by turn_index",
                (save_id,),
            ).fetchall()
            self.assertEqual(len(commits), 3)
            for c in commits:
                self.assertEqual(c["digested_in_phase"], 0)
                self.assertIsNotNone(c["digest_at"])

    def test_prompt_includes_dialogue(self):
        """system prompt + user prompt 里要看到玩家+GM 的话, 否则 LLM 没法摘。"""
        from agents.phase_digest_agent import compact_phase
        uid, save_id, phase_index = self._seed(n_commits=2)
        fake = _FakeBackend("happy")
        compact_phase(save_id, phase_index, user_id=uid, force=False, _backend=fake)
        self.assertIn("玩家发言 turn 1", fake.last_user)
        self.assertIn("GM 在 turn 2", fake.last_user)
        self.assertIn("阶段元信息", fake.last_user)
        self.assertIn("严格", fake.last_system)
        self.assertIn("JSON", fake.last_system)

    def test_short_circuit_when_already_closed(self):
        """已经 closed 且有 summary 时, force=False 不重做。"""
        from agents.phase_digest_agent import compact_phase
        from platform_app.db import connect

        uid, save_id, phase_index = self._seed(n_commits=2)
        with connect() as db:
            db.execute(
                "update save_phase_digests set status='closed', summary=%s "
                "where save_id=%s and phase_index=%s",
                ("旧摘要已存在", save_id, phase_index),
            )
        fake = _FakeBackend("happy")
        result = compact_phase(save_id, phase_index, user_id=uid,
                               force=False, _backend=fake)
        self.assertEqual(fake.call_count, 0, "短路时不该调 LLM")
        self.assertEqual(result.get("skipped"), "already_closed")
        self.assertEqual(result["summary"], "旧摘要已存在")

    def test_force_rewrites_even_when_closed(self):
        from agents.phase_digest_agent import compact_phase
        from platform_app.db import connect

        uid, save_id, phase_index = self._seed(n_commits=2)
        with connect() as db:
            db.execute(
                "update save_phase_digests set status='closed', summary=%s "
                "where save_id=%s and phase_index=%s",
                ("旧的", save_id, phase_index),
            )
        fake = _FakeBackend("happy")
        result = compact_phase(save_id, phase_index, user_id=uid,
                               force=True, _backend=fake)
        self.assertNotIn("error", result)
        self.assertEqual(fake.call_count, 1)
        self.assertNotEqual(result.get("skipped"), "already_closed")
        self.assertIn("怀表", result["summary"])

    def test_llm_call_failure_returns_error_dict(self):
        from agents.phase_digest_agent import compact_phase
        from platform_app.db import connect

        uid, save_id, phase_index = self._seed(n_commits=2)
        fake = _FakeBackend("raise")
        result = compact_phase(save_id, phase_index, user_id=uid,
                               force=False, _backend=fake)
        self.assertIn("error", result)
        self.assertEqual(result["save_id"], save_id)
        # DB 不应被改 (status 仍是 open)
        with connect() as db:
            row = db.execute(
                "select status, summary from save_phase_digests "
                "where save_id=%s and phase_index=%s",
                (save_id, phase_index),
            ).fetchone()
            self.assertEqual(row["status"], "open")
            self.assertEqual(row["summary"], "")

    def test_retry_succeeds_on_second_call(self):
        from agents.phase_digest_agent import compact_phase
        uid, save_id, phase_index = self._seed(n_commits=2)
        fake = _FakeBackend("retry")
        result = compact_phase(save_id, phase_index, user_id=uid,
                               force=False, _backend=fake)
        self.assertNotIn("error", result, msg=str(result))
        self.assertEqual(fake.call_count, 2)
        self.assertIn("怀表", result["summary"])

    def test_garbage_twice_raises(self):
        from agents.phase_digest_agent import compact_phase
        uid, save_id, phase_index = self._seed(n_commits=2)
        fake = _FakeBackend("garbage")
        # 第二次仍失败 → 函数应当吞掉 ValueError 转成 error dict (compact_phase
        # 把 _call_llm_with_retry 的异常捕获了)
        result = compact_phase(save_id, phase_index, user_id=uid,
                               force=False, _backend=fake)
        self.assertIn("error", result)
        self.assertIn("ValueError", result["error"])
        self.assertEqual(fake.call_count, 2)

    def test_missing_phase_returns_error(self):
        from agents.phase_digest_agent import compact_phase
        uid, save_id, phase_index = self._seed(n_commits=1)
        # phase 99 不存在
        result = compact_phase(save_id, 99, user_id=uid, _backend=_FakeBackend("happy"))
        self.assertIn("error", result)
        self.assertIn("not found", result["error"])

    def test_no_commits_returns_error(self):
        from agents.phase_digest_agent import compact_phase
        from platform_app.db import connect

        uid, save_id, phase_index = self._seed(n_commits=1)
        # 把唯一 commit 删了
        with connect() as db:
            db.execute("delete from branch_commits where save_id=%s", (save_id,))
        result = compact_phase(save_id, phase_index, user_id=uid,
                               _backend=_FakeBackend("happy"))
        self.assertIn("error", result)
        self.assertIn("no branch_commits", result["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
