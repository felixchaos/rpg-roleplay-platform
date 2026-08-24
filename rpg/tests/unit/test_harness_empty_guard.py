"""call_agent_json_guarded 空正文护栏回归测试(268 实锤范式抽成 harness 级共享)。

思考模型对结构化 JSON 微任务会无界思考吃光 max_tokens 正文恒空;空正文若被静默
解析成「没有结果」,整条功能链无声失效(史官 recorder 曾因此 1300+ 回合零验收无人
发觉)。本护栏:第一跳空 → 告警(带 reasoning_tokens 证据)+ 扩预算重试一次;再空 →
原样返回空文本(调用方保留各自空处理语义)但已留痕;重试自身抛异常 → 不上抛,返回
第一跳空结果。

mock 风格对齐 test_recorder_no_think.py:monkeypatch.setattr(_harness, "call_agent_json", fake)
拦截底层三通道 dispatch,只验护栏编排逻辑。
"""
from agents import _harness


def _make_fake(returns):
    """构造拦截 call_agent_json 的 fake:按 returns 序列依次返回 (text, usage);
    records 记录每次调用的位置参数与 kwargs,供断言透传/扩预算。"""
    seq = iter(returns)
    records = []

    def fake(api_id, model, system_prompt, user_prompt, user_id, **kw):
        records.append({
            "args": (api_id, model, system_prompt, user_prompt, user_id),
            "kw": dict(kw),
        })
        ret = next(seq)
        if isinstance(ret, Exception):
            raise ret
        return ret

    return fake, records


def test_first_hop_nonempty_passthrough(monkeypatch):
    """第一跳非空 → 只调 1 次,原样透传 (text, usage),kwargs 原样传给底层。"""
    fake, records = _make_fake([("PAYLOAD", {"output_tokens": 42})])
    monkeypatch.setattr(_harness, "call_agent_json", fake)

    text, usage = _harness.call_agent_json_guarded(
        "relay", "m", "sys", "user", 1,
        no_think=True, tool_schema={"name": "x"}, agent_kind="curator", max_tokens=400,
    )
    assert (text, usage) == ("PAYLOAD", {"output_tokens": 42})
    assert len(records) == 1
    # 护栏专属 kwargs 不得泄漏进底层;业务 kwargs 原样透传
    kw = records[0]["kw"]
    assert "log_tag" not in kw and "retry_max_tokens" not in kw
    assert kw["no_think"] is True
    assert kw["tool_schema"] == {"name": "x"}
    assert kw["agent_kind"] == "curator"
    assert kw["max_tokens"] == 400


def test_empty_then_retry_doubles_budget_min_1200(monkeypatch):
    """第一跳空 → 重试发生,max_tokens == max(原值*2, 1200);其余 kwargs 不变;
    第二跳有正文 → 返回第二跳结果。原值 400 → 1200(下限),1600 → 3200(翻倍)。"""
    # 原值 400 → 下限 1200
    fake, records = _make_fake([("", {"reasoning_tokens": 400}), ("OK2", {"output_tokens": 9})])
    monkeypatch.setattr(_harness, "call_agent_json", fake)
    text, usage = _harness.call_agent_json_guarded(
        "relay", "m", "sys", "user", 1,
        no_think=True, tool_schema={"name": "x"}, max_tokens=400,
    )
    assert (text, usage) == ("OK2", {"output_tokens": 9})
    assert len(records) == 2
    assert records[1]["kw"]["max_tokens"] == 1200
    # 其余 kwargs 不变
    assert records[1]["kw"]["no_think"] is True
    assert records[1]["kw"]["tool_schema"] == {"name": "x"}

    # 原值 1600 → 翻倍 3200
    fake2, records2 = _make_fake([("", {"reasoning_tokens": 1600}), ("OK2", {})])
    monkeypatch.setattr(_harness, "call_agent_json", fake2)
    _harness.call_agent_json_guarded("relay", "m", "sys", "user", 1, max_tokens=1600)
    assert records2[1]["kw"]["max_tokens"] == 3200


def test_both_empty_returns_empty_no_raise(monkeypatch):
    """两跳全空 → 共 2 次调用,返回空文本(不抛异常)。"""
    fake, records = _make_fake([("", {"reasoning_tokens": 1200}), ("  ", {})])
    monkeypatch.setattr(_harness, "call_agent_json", fake)
    text, usage = _harness.call_agent_json_guarded("relay", "m", "sys", "user", 1, max_tokens=400)
    assert len(records) == 2
    assert (text or "").strip() == ""
    assert usage == {}


def test_retry_raises_returns_first_empty(monkeypatch):
    """重试那跳抛异常 → 不向上抛,返回第一跳的空结果。"""
    first_usage = {"reasoning_tokens": 800}
    fake, records = _make_fake([("", first_usage), RuntimeError("relay 503")])
    monkeypatch.setattr(_harness, "call_agent_json", fake)
    text, usage = _harness.call_agent_json_guarded("relay", "m", "sys", "user", 1, max_tokens=400)
    assert len(records) == 2
    assert text == ""
    assert usage is first_usage


def test_first_hop_raises_propagates(monkeypatch):
    """第一跳抛异常 → 照常向上抛(与裸 call_agent_json 行为一致)。"""
    import pytest
    fake, records = _make_fake([RuntimeError("boom")])
    monkeypatch.setattr(_harness, "call_agent_json", fake)
    with pytest.raises(RuntimeError, match="boom"):
        _harness.call_agent_json_guarded("relay", "m", "sys", "user", 1, max_tokens=400)
    assert len(records) == 1


def test_explicit_retry_max_tokens_wins(monkeypatch):
    """retry_max_tokens 显式传入 → 优先于默认公式(max(原值*2, 1200))。"""
    fake, records = _make_fake([("", {"reasoning_tokens": 1200}), ("OK2", {})])
    monkeypatch.setattr(_harness, "call_agent_json", fake)
    _harness.call_agent_json_guarded(
        "relay", "m", "sys", "user", 1,
        retry_max_tokens=5000, max_tokens=400,
    )
    # 若走默认公式会是 1200;显式值优先
    assert records[1]["kw"]["max_tokens"] == 5000


def test_empty_warns_with_reasoning_tokens(monkeypatch, caplog):
    """第一跳空的 warning 带 reasoning_tokens 数字证据(非强制,加做锁静默病根)。"""
    import logging
    fake, _ = _make_fake([("", {"reasoning_tokens": 777}), ("OK2", {})])
    monkeypatch.setattr(_harness, "call_agent_json", fake)
    with caplog.at_level(logging.WARNING):
        _harness.call_agent_json_guarded("relay", "m", "sys", "user", 1, log_tag="curator", max_tokens=400)
    assert any("777" in rec.getMessage() and "curator" in rec.getMessage() for rec in caplog.records)



# ── require_json:护栏从「正文全空」扩到「有正文但不是 JSON」(反馈 #99)──────────
#
# 思考型模型(deepseek-v4-pro 一类)的失败形态不止空正文 —— 更常见的是正文被推理挤成
# 半截 JSON,或者干脆回一段解释。调用方眼里两者都是「没答上来」,待遇必须一致。

def test_require_json_unparseable_retries_with_bigger_budget(monkeypatch):
    """有正文但解析不出 → 扩预算重试一次,重试成功则返回重试结果。"""
    fake, records = _make_fake([
        ("好的,我来分析一下这个角色……", {"output_tokens": 30}),
        ('{"is_character": true, "identity": "镖师"}', {"output_tokens": 60}),
    ])
    monkeypatch.setattr(_harness, "call_agent_json", fake)

    text, usage = _harness.call_agent_json_guarded(
        "deepseek", "deepseek-v4-pro", "sys", "user", 1,
        max_tokens=700, no_think=True, require_json=True,
    )
    assert len(records) == 2, "解析不出必须重试一次"
    assert records[1]["kw"]["max_tokens"] == 1400, "重试要扩预算(max(700*2,1200))"
    assert records[1]["kw"]["no_think"] is True, "业务 kwargs 不变"
    assert "require_json" not in records[0]["kw"], "护栏专属 kwargs 不得泄漏进底层"
    assert text == '{"is_character": true, "identity": "镖师"}'
    assert usage == {"output_tokens": 60}


def test_require_json_truncated_json_counts_as_parseable(monkeypatch):
    """被 max_tokens 打断但已完整字段可打捞 → 不算失败,不该白花一次重试。"""
    fake, records = _make_fake([
        ('{"is_character": true, "identity": "镖师", "appearance": "刀疤', {}),
    ])
    monkeypatch.setattr(_harness, "call_agent_json", fake)

    _harness.call_agent_json_guarded(
        "deepseek", "m", "sys", "user", 1, max_tokens=700, require_json=True,
    )
    assert len(records) == 1


def test_require_json_retry_still_bad_returns_first_text(monkeypatch):
    """重试仍解析不出 → 最多两跳,返回第一跳原文(留证据给日志/用户,别丢)。"""
    fake, records = _make_fake([("解释一通", {"output_tokens": 5}), ("还是解释一通", {})])
    monkeypatch.setattr(_harness, "call_agent_json", fake)

    text, usage = _harness.call_agent_json_guarded(
        "deepseek", "m", "sys", "user", 1, max_tokens=700, require_json=True,
    )
    assert len(records) == 2, "最多重试一次,不能无限试"
    assert (text, usage) == ("解释一通", {"output_tokens": 5})


def test_require_json_off_keeps_old_passthrough(monkeypatch):
    """不开 require_json 的调用点行为一字不变(非空即透传)。"""
    fake, records = _make_fake([("随便什么文本", {})])
    monkeypatch.setattr(_harness, "call_agent_json", fake)

    text, _ = _harness.call_agent_json_guarded("deepseek", "m", "sys", "user", 1, max_tokens=700)
    assert len(records) == 1
    assert text == "随便什么文本"


def test_require_json_retry_exception_falls_back_to_first(monkeypatch):
    """重试自身抛异常 → 不上抛,返回第一跳(与空正文护栏同语义)。"""
    fake, records = _make_fake([("解释一通", {}), RuntimeError("boom")])
    monkeypatch.setattr(_harness, "call_agent_json", fake)

    text, _ = _harness.call_agent_json_guarded(
        "deepseek", "m", "sys", "user", 1, max_tokens=700, require_json=True,
    )
    assert text == "解释一通"
    assert len(records) == 2
