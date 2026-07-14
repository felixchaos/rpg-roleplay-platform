"""
test_command_dispatcher.py — task 87: ToolDispatcher + ToolRegistry + 队列 隔离

测试覆盖:
  Layer A — ToolRegistry: register / get / origin 过滤
  Layer B — ToolDispatcher 验证管道: unknown_tool / origin_forbidden / scope_missing /
            depth_exceeded / rate_limited / trace_duplicate / destructive_blocked
  Layer C — 跨账号 / 跨存档 隔离 (per-(user,save) 锁)
  Layer D — Phase 2 首批 8 个工具的执行结果正确性
  Layer E — 审计写入 state.permissions.audit_log + 进程级 recent_audit
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import DEFAULT_STATE, GameState  # noqa: E402
from tools_dsl.command_dispatcher import (  # noqa: E402
    MAX_CALLS_PER_USER_PER_SECOND,
    MAX_TRACE_DEPTH,
    MAX_TRACE_SEEN,
    ToolCallEnvelope,
    ToolDispatcher,
    ToolRegistry,
    ToolSpec,
    get_registry,
)
from tools_dsl.command_tools_register import (  # noqa: E402
    force_reset_for_tests,
)


def _new_state(turn=3) -> GameState:
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = turn
    return s


# ────────────────────────────────────────────────────────────
# Layer A: Registry
# ────────────────────────────────────────────────────────────


class RegistryAPI(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()

    def test_register_and_get(self):
        spec = ToolSpec(
            name="echo",
            description="echo",
            input_schema={"type": "object", "properties": {}},
            executor=lambda state, args: "echoed",
            scope="save",
        )
        self.reg.register(spec)
        self.assertIs(self.reg.get("echo"), spec)
        self.assertTrue(self.reg.has("echo"))

    def test_register_duplicate_raises(self):
        spec = ToolSpec(name="dup", description="", input_schema={"type": "object"},
                        executor=lambda s, a: "")
        self.reg.register(spec)
        with self.assertRaises(ValueError):
            self.reg.register(spec)

    def test_list_for_origin(self):
        a = ToolSpec(name="a", description="", input_schema={"type": "object"},
                     executor=lambda s, x: "",
                     origins=frozenset({"llm_chat", "ui_button"}))
        b = ToolSpec(name="b", description="", input_schema={"type": "object"},
                     executor=lambda s, x: "",
                     origins=frozenset({"ui_button"}))
        self.reg.register(a)
        self.reg.register(b)
        ui = {s.name for s in self.reg.list_for_origin("ui_button")}
        chat = {s.name for s in self.reg.list_for_origin("llm_chat")}
        self.assertEqual(ui, {"a", "b"})
        self.assertEqual(chat, {"a"})


# ────────────────────────────────────────────────────────────
# Layer B: Dispatcher 验证
# ────────────────────────────────────────────────────────────


class DispatcherValidation(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.register(ToolSpec(
            name="set_thing", description="",
            input_schema={"type": "object", "properties": {"v": {"type": "string"}}},
            executor=lambda state, args: f"set to {args.get('v','')}",
            scope="save",
            origins=frozenset({"llm_set", "ui_button"}),
        ))
        self.reg.register(ToolSpec(
            name="delete_save", description="",
            input_schema={"type": "object", "properties": {}},
            executor=lambda state, args: "deleted",
            scope="save",
            origins=frozenset({"ui_button"}),  # 不允许 llm_chat
            destructive=True,
        ))
        self.reg.register(ToolSpec(
            name="list_models", description="",
            input_schema={"type": "object", "properties": {}},
            executor=lambda args: "ok",
            scope="global",
            origins=frozenset({"llm_chat", "ui_button"}),
        ))
        self.state = _new_state()
        self.dispatcher = ToolDispatcher(
            registry=self.reg,
            state_provider=lambda env: self.state,
        )

    def _env(self, **kw):
        defaults = {"user_id": 1, "tool": "set_thing", "args": {"v": "x"},
                    "origin": "llm_set", "save_id": 100, "trace_id": "t1"}
        defaults.update(kw)
        return ToolCallEnvelope(**defaults)

    def test_unknown_tool(self):
        r = self.dispatcher.dispatch_sync(self._env(tool="ghost"))
        self.assertFalse(r.ok)
        self.assertIn("unknown_tool", (r.error or ""))

    def test_origin_forbidden(self):
        r = self.dispatcher.dispatch_sync(self._env(origin="llm_chat"))
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", (r.error or ""))

    def test_scope_save_missing_save_id(self):
        r = self.dispatcher.dispatch_sync(self._env(save_id=None))
        self.assertFalse(r.ok)
        self.assertIn("scope_missing_save", (r.error or ""))

    def test_depth_exceeded(self):
        r = self.dispatcher.dispatch_sync(self._env(depth=MAX_TRACE_DEPTH + 1))
        self.assertFalse(r.ok)
        self.assertIn("depth_exceeded", (r.error or ""))

    def test_trace_duplicate(self):
        ok = self.dispatcher.dispatch_sync(self._env())
        dup = self.dispatcher.dispatch_sync(self._env())
        self.assertTrue(ok.ok)
        self.assertFalse(dup.ok)
        self.assertIn("trace_duplicate", (dup.error or ""))

    def test_destructive_blocked_from_llm_chat(self):
        # delete_save 本身 origins 不含 llm_chat → 应先在 origin_forbidden 拦下
        # 这里另注册一个 destructive=True 且 origins 含 llm_chat 的工具,测 destructive 检查
        self.reg.register(ToolSpec(
            name="dangerous_open",
            description="",
            input_schema={"type": "object"},
            executor=lambda state, args: "danger",
            scope="save",
            origins=frozenset({"llm_chat", "ui_button"}),
            destructive=True,
        ))
        r = self.dispatcher.dispatch_sync(
            self._env(tool="dangerous_open", origin="llm_chat", trace_id="t-d")
        )
        self.assertFalse(r.ok)
        self.assertIn("destructive_blocked", (r.error or ""))

    def test_rate_limit(self):
        self.dispatcher.reset_rate_limits()
        # MAX_CALLS_PER_USER_PER_SECOND 之前正常,再多就拒
        for i in range(MAX_CALLS_PER_USER_PER_SECOND):
            r = self.dispatcher.dispatch_sync(self._env(trace_id=f"t-r{i}"))
            self.assertTrue(r.ok, f"第 {i} 次应通过;{r}")
        over = self.dispatcher.dispatch_sync(self._env(trace_id="t-over"))
        self.assertFalse(over.ok)
        self.assertIn("rate_limited", (over.error or ""))

    def test_global_tool_no_state_no_save(self):
        r = self.dispatcher.dispatch_sync(self._env(
            tool="list_models", save_id=None, origin="llm_chat", trace_id="g1",
        ))
        self.assertTrue(r.ok, f"global 工具不需要 save_id; {r}")

    def test_auth_failure(self):
        d2 = ToolDispatcher(
            registry=self.reg,
            state_provider=lambda env: self.state,
            authorize=lambda uid: uid == 99,
        )
        r = d2.dispatch_sync(self._env(user_id=1, trace_id="ax"))
        self.assertFalse(r.ok)
        self.assertIn("auth_failed", (r.error or ""))


# ────────────────────────────────────────────────────────────
# Layer C: 跨账号 / 跨存档 隔离
# ────────────────────────────────────────────────────────────


class CrossAccountIsolation(unittest.TestCase):
    """同一进程内,两个 user/save 的工具调用必须落到不同 state,互不污染。"""

    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.register(ToolSpec(
            name="touch", description="",
            input_schema={"type": "object", "properties": {"v": {"type": "string"}}},
            executor=lambda state, args: (
                state.data.setdefault("memory", {}).setdefault("notes", []).append(args.get("v", "")) or
                "touched"
            ),
            scope="save",
            origins=frozenset({"llm_set", "ui_button"}),
        ))
        self.states = {(1, 100): _new_state(), (1, 200): _new_state(), (2, 100): _new_state()}
        self.dispatcher = ToolDispatcher(
            registry=self.reg,
            state_provider=lambda env: self.states[(env.user_id, env.save_id)],
        )

    def test_distinct_save_isolated(self):
        # user 1, save 100 添加 "A"; user 1, save 200 添加 "B"
        self.dispatcher.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=100, tool="touch", args={"v": "A"},
            origin="llm_set", trace_id="ti-1",
        ))
        self.dispatcher.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=200, tool="touch", args={"v": "B"},
            origin="llm_set", trace_id="ti-2",
        ))
        self.assertEqual(self.states[(1, 100)].data["memory"]["notes"], ["A"])
        self.assertEqual(self.states[(1, 200)].data["memory"]["notes"], ["B"])

    def test_distinct_user_isolated(self):
        self.dispatcher.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=100, tool="touch", args={"v": "U1"},
            origin="llm_set", trace_id="tu-1",
        ))
        self.dispatcher.dispatch_sync(ToolCallEnvelope(
            user_id=2, save_id=100, tool="touch", args={"v": "U2"},
            origin="llm_set", trace_id="tu-2",
        ))
        self.assertEqual(self.states[(1, 100)].data["memory"]["notes"], ["U1"])
        self.assertEqual(self.states[(2, 100)].data["memory"]["notes"], ["U2"])


# ────────────────────────────────────────────────────────────
# Layer D: Phase 2 新工具执行正确性
# ────────────────────────────────────────────────────────────


class Phase2ToolExecution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state(turn=5)
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def _call(self, tool, args, *, origin="ui_button", trace_id=None):
        env = ToolCallEnvelope(
            user_id=1, save_id=100, tool=tool, args=args,
            origin=origin, trace_id=trace_id or f"tt-{tool}",
        )
        return self.dispatcher.dispatch_sync(env)

    def test_remove_memory_item(self):
        self.state.data["memory"]["facts"] = ["A", "B", "C"]
        r = self._call("remove_memory_item", {"bucket": "facts", "index": 1})
        self.assertTrue(r.ok, r.error)
        self.assertEqual(self.state.data["memory"]["facts"], ["A", "C"])

    def test_remove_memory_item_index_out_of_range(self):
        self.state.data["memory"]["facts"] = ["A"]
        r = self._call("remove_memory_item", {"bucket": "facts", "index": 5})
        self.assertFalse(r.ok)
        self.assertEqual(self.state.data["memory"]["facts"], ["A"])

    def test_remove_memory_item_blocked_from_llm_chat(self):
        """remove_memory_item destructive=True,llm_chat origin 不可调。
        可能由 origins 黑名单直接拒 (origin_forbidden),或 destructive 检查拒。
        两者都算正确拒绝。"""
        self.state.data["memory"]["facts"] = ["A"]
        r = self._call("remove_memory_item",
                       {"bucket": "facts", "index": 0},
                       origin="llm_chat", trace_id="tdl-1")
        self.assertFalse(r.ok)
        self.assertTrue(
            "origin_forbidden" in (r.error or "") or "destructive_blocked" in (r.error or ""),
            f"应被拒绝;实际 {r.error}",
        )
        self.assertEqual(self.state.data["memory"]["facts"], ["A"])

    def test_dismiss_pending_question(self):
        self.state.data.setdefault("permissions", {})["pending_questions"] = [
            {"id": "q1", "question": "X"}, {"id": "q2", "question": "Y"},
        ]
        r = self._call("dismiss_pending_question", {"id": "q1"})
        self.assertTrue(r.ok, r.error)
        remaining = [q["id"] for q in self.state.data["permissions"]["pending_questions"]]
        self.assertEqual(remaining, ["q2"])

    def test_remove_user_variable(self):
        self.state.data.setdefault("worldline", {})["user_variables"] = {
            "trust_slaine": {"value": "信任"},
        }
        r = self._call("remove_user_variable", {"key": "trust_slaine"})
        self.assertTrue(r.ok, r.error)
        self.assertNotIn("trust_slaine", self.state.data["worldline"]["user_variables"])

    def test_save_runtime(self):
        # server 模式下 save 返回 "" — 仍认为成功
        os.environ["RPG_DEPLOYMENT_MODE"] = "server"
        os.environ["RPG_REQUIRE_AUTH"] = "1"
        try:
            r = self._call("save_runtime", {}, trace_id="tsr-srv")
            self.assertTrue(r.ok, r.error)
        finally:
            os.environ["RPG_DEPLOYMENT_MODE"] = "local"
            os.environ["RPG_REQUIRE_AUTH"] = "0"

    def test_add_world_event(self):
        # task #14: add_world_event 别名已删,改用 set_world_known_event(arg: event)
        r = self._call("set_world_known_event", {"event": "图卢兹陷落第七天"})
        self.assertTrue(r.ok, r.error)
        self.assertIn("图卢兹陷落第七天", self.state.data["world"]["known_events"])

    def test_add_world_event_dedup(self):
        self.state.data.setdefault("world", {})["known_events"] = ["X"]
        r = self._call("set_world_known_event", {"event": "X"}, trace_id="tdup-1")
        self.assertIn("已存在", r.result)

    def test_stop_current_chat(self):
        r = self._call("stop_current_chat", {})
        self.assertTrue(r.ok, r.error)
        self.assertTrue(
            self.state.data["permissions"]["stop_signal"]["requested"],
        )

    def test_stop_current_chat_blocked_from_llm_chat(self):
        """LLM 自停被禁止。"""
        r = self._call("stop_current_chat", {}, origin="llm_chat",
                       trace_id="tsc-lc")
        self.assertFalse(r.ok)
        self.assertIn("origin_forbidden", (r.error or ""))


# ────────────────────────────────────────────────────────────
# Layer E: 审计
# ────────────────────────────────────────────────────────────


class AuditTrail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state()
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def test_successful_call_audited_to_state(self):
        env = ToolCallEnvelope(
            user_id=1, save_id=100, tool="set_world_known_event",
            args={"event": "事件A"}, origin="ui_button", trace_id="ta-1",
        )
        r = self.dispatcher.dispatch_sync(env)
        self.assertTrue(r.ok, r.error)
        audit = self.state.data["permissions"]["audit_log"]
        tool_audits = [a for a in audit if a.get("kind") == "tool_call"]
        self.assertGreaterEqual(len(tool_audits), 1)
        last = tool_audits[-1]
        self.assertEqual(last["tool"], "set_world_known_event")
        self.assertEqual(last["origin"], "ui_button")
        self.assertTrue(last["ok"])

    def test_rejected_call_audited(self):
        # llm_chat 调 destructive=True 的 remove_memory_item
        env = ToolCallEnvelope(
            user_id=1, save_id=100, tool="remove_memory_item",
            args={"bucket": "facts", "index": 0},
            origin="llm_chat", trace_id="tr-r",
        )
        r = self.dispatcher.dispatch_sync(env)
        self.assertFalse(r.ok)
        recent = self.dispatcher.recent_audit(limit=20)
        rejected = [a for a in recent if a.get("kind") == "tool_call_rejected"]
        self.assertGreaterEqual(len(rejected), 1)
        last = rejected[-1]
        # origins 黑名单和 destructive 检查都是合法拒绝原因
        self.assertIn(
            last["reject_kind"],
            ("destructive_blocked", "origin_forbidden"),
        )


# ────────────────────────────────────────────────────────────
# Layer F: 旧 command_tools 仍可通过 dispatcher 调用
# ────────────────────────────────────────────────────────────


class LegacyCommandToolsViaDispatcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        force_reset_for_tests()

    def setUp(self):
        self.state = _new_state(turn=7)
        self.dispatcher = ToolDispatcher(
            registry=get_registry(),
            state_provider=lambda env: self.state,
        )

    def test_set_world_time_via_dispatcher(self):
        env = ToolCallEnvelope(
            user_id=1, save_id=100, tool="set_world_time",
            args={"target": "火星·扬陆城内"},
            origin="llm_set", trace_id="legacy-1",
        )
        r = self.dispatcher.dispatch_sync(env)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(self.state.data["world"]["time"], "火星·扬陆城内")
        self.assertEqual(
            self.state.data["world"]["timeline"].get("user_set_jump_turn"), 7,
            "通过 dispatcher 调 set_world_time 也必须设 user_set_jump_turn",
        )

    def test_set_relationship_via_dispatcher(self):
        env = ToolCallEnvelope(
            user_id=1, save_id=100, tool="set_relationship",
            args={"character": "斯雷因", "status": "警惕中立"},
            origin="llm_set", trace_id="legacy-2",
        )
        r = self.dispatcher.dispatch_sync(env)
        self.assertTrue(r.ok, r.error)
        self.assertEqual(self.state.data["relationships"]["斯雷因"], "警惕中立")


class TraceSeenLRUBound(unittest.TestCase):
    """_trace_seen 去重表必须有界:dispatcher 是进程级单例,trace_id 按回合唯一,
    plain dict 会随累计回合数无限增长(内存泄漏)。验证 LRU 上限 + 去重仍正确。"""

    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.register(ToolSpec(
            name="list_models", description="",
            input_schema={"type": "object", "properties": {}},
            executor=lambda args: "ok",
            scope="global",
            origins=frozenset({"llm_chat", "ui_button"}),
        ))
        self.dispatcher = ToolDispatcher(registry=self.reg, state_provider=lambda env: None)

    def test_trace_seen_bounded(self):
        # 派发远超上限个不同 trace_id,断言去重表不超过 MAX_TRACE_SEEN
        for i in range(MAX_TRACE_SEEN + 200):
            self.dispatcher.dispatch_sync(ToolCallEnvelope(
                user_id=1, tool="list_models", args={}, origin="llm_chat",
                trace_id=f"trace-{i}",
            ))
        self.assertLessEqual(len(self.dispatcher._trace_seen), MAX_TRACE_SEEN,
                             "_trace_seen 超过 LRU 上限,未防住内存泄漏")

    def test_dedup_still_works_for_active_trace(self):
        # 同 trace 同 (tool,args) 第二次必须被拒(去重逻辑未被 LRU 改坏)
        env = ToolCallEnvelope(user_id=1, tool="list_models", args={}, origin="llm_chat", trace_id="dup-trace")
        r1 = self.dispatcher.dispatch_sync(env)
        self.assertTrue(r1.ok)
        r2 = self.dispatcher.dispatch_sync(ToolCallEnvelope(
            user_id=1, tool="list_models", args={}, origin="llm_chat", trace_id="dup-trace"))
        self.assertFalse(r2.ok)
        self.assertIn("trace_duplicate", (r2.error or ""))

    def test_active_trace_not_evicted_under_churn(self):
        # 活跃 trace 反复使用时,即使有大量新 trace 涌入也不应被淘汰(LRU move_to_end)
        live = "live-trace"
        self.dispatcher.dispatch_sync(ToolCallEnvelope(
            user_id=1, tool="list_models", args={"k": "a"}, origin="llm_chat", trace_id=live))
        for i in range(MAX_TRACE_SEEN + 50):
            self.dispatcher.dispatch_sync(ToolCallEnvelope(
                user_id=1, tool="list_models", args={}, origin="llm_chat", trace_id=f"noise-{i}"))
            # 每轮也碰一下 live trace,使其保持在末尾不被淘汰
            self.dispatcher.dispatch_sync(ToolCallEnvelope(
                user_id=1, tool="list_models", args={"k": str(i)}, origin="llm_chat", trace_id=live))
        # live trace 仍在表中且其去重集保留了历史签名
        self.assertIn(live, self.dispatcher._trace_seen)


class IntegerArgCoercion(unittest.TestCase):
    """schema 驱动整数纠偏(群反馈 行者无疆:/set 推进到第30章 → to_chapter="第30章"):
    JSON-mode fallback 模型把 integer 参数传成玩家原话字符串,dispatcher 在
    _validate 8.5 步做确定性提取;歧义(0/≥2 个数字组)保持原值 —— 宁漏勿误。"""

    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.register(ToolSpec(
            name="fake_advance",
            description="",
            input_schema={
                "type": "object",
                "properties": {
                    "to_chapter": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": ["to_chapter"],
            },
            executor=lambda state, args: f"got {args['to_chapter']!r}/{args.get('label')!r}",
            scope="save",
            origins=frozenset({"llm_set"}),
        ))
        self.dispatcher = ToolDispatcher(
            registry=self.reg, state_provider=lambda env: _new_state())

    def _dispatch(self, args, trace):
        return self.dispatcher.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=100, tool="fake_advance", args=args,
            origin="llm_set", trace_id=trace))

    def test_chinese_wrapped_chapter_coerced(self):
        r = self._dispatch({"to_chapter": "第30章"}, "co-1")
        self.assertTrue(r.ok, r.error)
        self.assertIn("got 30/", r.result)

    def test_numeric_string_coerced(self):
        r = self._dispatch({"to_chapter": "36"}, "co-2")
        self.assertIn("got 36/", r.result)

    def test_fullwidth_digits_coerced(self):
        r = self._dispatch({"to_chapter": "第３０章"}, "co-3")
        self.assertIn("got 30/", r.result)

    def test_float_integral_coerced(self):
        r = self._dispatch({"to_chapter": 30.0}, "co-4")
        self.assertIn("got 30/", r.result)

    def test_ambiguous_two_groups_untouched(self):
        r = self._dispatch({"to_chapter": "从第3章到第5章"}, "co-5")
        self.assertIn("got '从第3章到第5章'/", r.result)

    def test_string_typed_param_never_coerced(self):
        r = self._dispatch({"to_chapter": 9, "label": "第30章"}, "co-6")
        self.assertIn("/'第30章'", r.result)

    def test_real_advance_story_progress_schema_coerced(self):
        # 真工具表:走 _validate 验证 advance_story_progress 的 schema 触发纠偏
        force_reset_for_tests()
        d = ToolDispatcher(registry=get_registry(), state_provider=lambda env: None)
        env = ToolCallEnvelope(
            user_id=1, save_id=100, tool="advance_story_progress",
            args={"to_chapter": "第30章"}, origin="llm_set", trace_id="co-real")
        d._validate(env)
        self.assertEqual(env.args["to_chapter"], 30)


class FailureResultDetection(unittest.TestCase):
    """结果串失败识别:老检测只认「失败/ERROR/拒绝」裸前缀,漏掉平台更普遍的
    「<tool_name> 失败: ...」惯例 → 失败被记 ok=True,前端计进「设定已更新」。"""

    def _make(self, ret):
        reg = ToolRegistry()
        reg.register(ToolSpec(
            name="probe", description="",
            input_schema={"type": "object", "properties": {}},
            executor=lambda state, args: ret,
            scope="save", origins=frozenset({"llm_set"})))
        d = ToolDispatcher(registry=reg, state_provider=lambda env: _new_state())
        return d.dispatch_sync(ToolCallEnvelope(
            user_id=1, save_id=100, tool="probe", args={}, origin="llm_set"))

    def test_toolname_prefixed_failure_detected(self):
        r = self._make("advance_story_progress 失败: to_chapter 必须是整数章号")
        self.assertFalse(r.ok)

    def test_bare_failure_prefix_still_detected(self):
        self.assertFalse(self._make("失败: index 必须是整数").ok)

    def test_rejected_prefix_still_detected(self):
        self.assertFalse(self._make("拒绝: 不允许").ok)

    def test_success_result_stays_ok(self):
        self.assertTrue(self._make("进度推进:第 17 章 → 第 30 章").ok)

    def test_midstring_failure_word_not_flagged(self):
        # 「失败」出现在正文中间(非失败惯例形态)不误判
        self.assertTrue(self._make("记录:上次尝试失败的教训已写入笔记").ok)


if __name__ == "__main__":
    unittest.main(verbosity=2)
