"""test_dispatcher_save_binding.py — save_id 绑定与带内失败可观测(v1.83.1)。

生产实证(近 30 天 origin=llm_chat):`list_pending_anchors` 239 次失败里 238 次带了
args.save_id,模型填的是 1(135 次)/ 0(19 次)/ 2(3 次),真实存档是 268 / 493 / 540。
成功率 0.4%;`mark_anchor_satisfied` / `lookup_entity` / `kb_*` / `search_canon` /
`graph_neighbors` 全 0%。同期 `get_game_state` 97.6% —— 差别只在「谁来填 save_id」:
scope="save" 由服务端注入,scope="user" 要模型自己填,而**模型无从知道存档 id**。

本文件锁住绑定行为、跨 origin 的语义差别,以及「失败必须留下原因」。
"""
from __future__ import annotations

import pytest

from tools_dsl.command_dispatcher import (
    ToolCallEnvelope,
    ToolDispatcher,
    ToolRegistry,
    ToolSpec,
    _bind_current_save,
)

REAL_SAVE = 268
MODEL_GUESS = 1  # 模型实际填的那个数


def _spec(name="probe", scope="user", with_save=True, executor=None):
    props = {"save_id": {"type": "integer"}} if with_save else {}
    return ToolSpec(
        name=name,
        description="probe",
        input_schema={"type": "object", "properties": props,
                      "required": ["save_id"] if with_save else []},
        executor=executor or (lambda *a: "ok"),
        scope=scope,
        origins=frozenset({"llm_chat", "console_assistant"}),
    )


def _env(origin="llm_chat", save_id=REAL_SAVE, args=None):
    return ToolCallEnvelope(user_id=7, save_id=save_id, script_id=None, tool="probe",
                            args=args if args is not None else {"save_id": MODEL_GUESS},
                            origin=origin, trace_id="t")


class TestBinding:
    def test_gm_turn_overrides_the_models_guess(self):
        env = _env()
        _bind_current_save(env, _spec())
        assert env.args["save_id"] == REAL_SAVE, "GM 回合没把模型瞎填的 save_id 覆盖掉"

    def test_gm_turn_fills_when_model_omitted_it(self):
        env = _env(args={})
        _bind_current_save(env, _spec())
        assert env.args["save_id"] == REAL_SAVE

    def test_console_assistant_keeps_an_explicit_cross_save_id(self):
        """平台助手确实会跨档(「列出我的存档」→「看第 3 个的锚点」),不能一刀切覆盖。"""
        env = _env(origin="console_assistant", args={"save_id": 999})
        _bind_current_save(env, _spec())
        assert env.args["save_id"] == 999

    def test_console_assistant_fills_when_missing(self):
        env = _env(origin="console_assistant", args={})
        _bind_current_save(env, _spec())
        assert env.args["save_id"] == REAL_SAVE

    def test_no_save_in_schema_is_untouched(self):
        env = _env(args={"foo": 1})
        _bind_current_save(env, _spec(with_save=False))
        assert "save_id" not in env.args

    def test_save_scope_left_to_the_existing_fence(self):
        """scope="save" 已由 _execute 的围栏覆盖,这里不许重复插手。"""
        env = _env(args={"save_id": MODEL_GUESS})
        _bind_current_save(env, _spec(scope="save"))
        assert env.args["save_id"] == MODEL_GUESS  # 由 _execute 稍后覆盖

    def test_no_bound_save_leaves_it_alone(self):
        env = _env(save_id=None, args={"save_id": MODEL_GUESS})
        _bind_current_save(env, _spec())
        assert env.args["save_id"] == MODEL_GUESS


class TestEndToEndThroughDispatcher:
    def test_executor_receives_the_authenticated_save_id(self):
        """行为守卫:走完整 dispatch,执行器拿到的必须是 env.save_id 而不是模型填的。"""
        seen = {}

        def executor(user_id, args):
            seen.update(args)
            return "ok"

        reg = ToolRegistry()
        reg.register(_spec(executor=executor))
        d = ToolDispatcher(registry=reg, state_provider=lambda env: None)
        res = d.dispatch_sync(_env())
        assert res.ok, res.error
        assert seen["save_id"] == REAL_SAVE, (
            f"执行器收到 {seen['save_id']},模型瞎填的值穿透了绑定")


class TestRealFamilyIsCovered:
    """真实注册表:GM 能看到的、schema 里声明了 save_id 的工具,一个都不许漏。"""

    @classmethod
    @pytest.fixture(scope="class", autouse=True)
    def _registered(cls):
        from tools_dsl import command_tools_register as _reg
        _reg.ensure_registered()

    def test_every_llm_chat_tool_with_save_id_gets_bound(self):
        from tools_dsl.command_dispatcher import get_registry
        covered, skipped = [], []
        for spec in get_registry().list_for_origin("llm_chat"):
            props = (spec.input_schema or {}).get("properties") or {}
            if "save_id" not in props:
                continue
            env = _env(args={"save_id": MODEL_GUESS})
            _bind_current_save(env, spec)
            (covered if env.args["save_id"] == REAL_SAVE else skipped).append(spec.name)
        # scope="save" 的由 _execute 覆盖,不该出现在 skipped 之外的意义上
        leaked = [n for n in skipped
                  if next(s.scope for s in get_registry().list_for_origin("llm_chat")
                          if s.name == n) != "save"]
        assert not leaked, f"这些工具的 save_id 仍要模型自己填(它无从知道): {leaked}"
        assert len(covered) >= 15, f"只覆盖到 {len(covered)} 个,判据可能失效"


class TestInBandFailureIsRecorded:
    def test_failure_string_becomes_the_error_reason(self, monkeypatch):
        """工具体内 except 后返回「失败: ...」字符串时,原因必须落进 error 列 ——
        否则留下一条「失败了,但不知道为什么」(生产曾有 981 条这样的记录)。"""
        captured = {}

        import tools_dsl.command_dispatcher as cd

        def fake_persist(env, *, ok, error, error_kind):
            captured.update(ok=ok, error=error, error_kind=error_kind)

        monkeypatch.setattr(cd, "_persist_invocation_async", fake_persist)
        reg = ToolRegistry()
        reg.register(_spec(executor=lambda uid, args: "失败 (权限): save 1 不属于当前用户或不存在"))
        d = ToolDispatcher(registry=reg, state_provider=lambda env: None)
        res = d.dispatch_sync(_env())
        assert res.ok is False
        assert captured["ok"] is False
        assert "不属于当前用户" in (captured["error"] or ""), captured
        assert captured["error_kind"] == "in_band"

    def test_success_records_no_error(self, monkeypatch):
        captured = {}
        import tools_dsl.command_dispatcher as cd
        monkeypatch.setattr(cd, "_persist_invocation_async",
                            lambda env, *, ok, error, error_kind: captured.update(
                                ok=ok, error=error, error_kind=error_kind))
        reg = ToolRegistry()
        reg.register(_spec(executor=lambda uid, args: "done"))
        d = ToolDispatcher(registry=reg, state_provider=lambda env: None)
        assert d.dispatch_sync(_env()).ok
        assert captured["error"] is None and captured["error_kind"] is None
