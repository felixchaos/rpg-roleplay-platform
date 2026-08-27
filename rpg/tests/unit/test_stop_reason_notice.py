"""test_stop_reason_notice.py — 上游把这一轮掐了,玩家得知道(v1.84.0)。

生产实证:save 268 在两周内撞了 42 次 `finish_reason='content_filter'`,玩家每次收到的
都是**模型自己的拒答原话**「你好,我无法给到相关内容。」(13 字)——读起来像 GM 突然出戏。
同期 `length` 42 次 / 10 个存档,玩家拿到半截场景也没有任何解释。
两者此前都只有服务端 log.warning。

判据必须是 provider 的 finish_reason(确定性信号),**不能猜正文** —— 短回复既可能是拒答、
也可能是正常的一句话叙事。
"""
from __future__ import annotations

import pytest

from chat_pipeline.gm import _stop_reason_notice


def _ctx(finish_reason=None, *, no_backend=False, raise_on_access=False):
    class _B:
        last_usage = {} if finish_reason is None else {"finish_reason": finish_reason}

    class _G:
        _backend = None if no_backend else _B()

    class _Ctx:
        gm = _G()

    if raise_on_access:
        class _Boom:
            @property
            def gm(self):
                raise RuntimeError("boom")
        return _Boom()
    return _Ctx()


@pytest.mark.parametrize("fr", ["stop", "tool_calls", None, ""])
def test_normal_finish_says_nothing(fr):
    assert _stop_reason_notice(_ctx(fr)) == []


def test_content_filter_explains_it_was_a_policy_block():
    out = _stop_reason_notice(_ctx("content_filter"))
    assert len(out) == 1
    phase, msg = out[0]
    assert phase == "stop_reason"
    assert "内容策略" in msg
    assert "不是剧情" in msg, "得说清那句回绝不是剧情,否则玩家仍以为 GM 出戏"
    assert "换" in msg, "得给可行动的下一步"


def test_length_explains_truncation():
    out = _stop_reason_notice(_ctx("length"))
    assert len(out) == 1
    assert "截断" in out[0][1]
    assert "继续" in out[0][1], "得告诉玩家怎么接着写"


def test_no_backend_is_silent_not_crashing():
    assert _stop_reason_notice(_ctx(no_backend=True)) == []


def test_broken_ctx_is_silent_not_crashing():
    """提示是锦上添花,绝不能把回合本身弄挂。"""
    assert _stop_reason_notice(_ctx(raise_on_access=True)) == []


def test_notice_does_not_rewrite_the_narrative():
    """只发提示,不动正文 —— 模型产出可能仍有可用部分,由玩家自己判断。"""
    import inspect

    from chat_pipeline import gm as gm_mod
    src = inspect.getsource(gm_mod._stop_reason_notice)
    assert "ctx.response" not in src, "提示函数不该碰正文"
