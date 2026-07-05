"""stream_with_pretoken_retry(流式首token前自动重试)单测。

核心不变量:
1. 已提交(正文token/工具事件)之后的失败【绝不】重试(防工具双重副作用/正文重复)。
2. 只对 upstream/ratelimit 分类重试;402/401/未知错误原样抛。
3. 重试次数有界+退避可注入;GeneratorExit(断连)永不重试。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agents.gm.stream_retry import (  # noqa: E402
    opening_chunk_commits,
    stream_with_pretoken_retry,
)


class _Exc502(Exception):
    status_code = 502


class _Exc429(Exception):
    status_code = 429


class _Exc402(Exception):
    status_code = 402


def _factory_seq(*gens):
    """依次返回给定生成器(每次调用取下一个)。"""
    it = iter(gens)

    def factory():
        return next(it)
    return factory


def _gen_fail_immediately(exc):
    def g():
        raise exc
        yield  # pragma: no cover
    return g()


def _gen_events(*events, fail_after=None):
    def g():
        for e in events:
            yield e
        if fail_after is not None:
            raise fail_after
    return g()


def test_retry_then_success_pre_token():
    delays = []
    out = list(stream_with_pretoken_retry(
        _factory_seq(
            _gen_fail_immediately(_Exc502("bad gateway")),
            _gen_events({"type": "text", "text": "你好"}),
        ),
        sleep=delays.append,
    ))
    assert {"type": "text", "text": "你好"} in out
    notices = [e for e in out if e.get("type") == "retry_notice"]
    assert len(notices) == 1 and notices[0]["attempt"] == 1 and notices[0]["category"] == "upstream"
    assert delays == [1.5]


def test_no_retry_after_text_committed():
    with pytest.raises(_Exc502):
        list(stream_with_pretoken_retry(
            _factory_seq(_gen_events({"type": "text", "text": "开"}, fail_after=_Exc502("mid"))),
            sleep=lambda _s: None,
        ))


def test_no_retry_after_tool_call():
    with pytest.raises(_Exc502):
        list(stream_with_pretoken_retry(
            _factory_seq(_gen_events({"type": "tool_call", "tool": "worldbook_add"},
                                     fail_after=_Exc502("mid"))),
            sleep=lambda _s: None,
        ))


def test_reasoning_only_does_not_commit():
    out = list(stream_with_pretoken_retry(
        _factory_seq(
            _gen_events({"type": "reasoning", "text": "思考…"}, fail_after=_Exc429("rl")),
            _gen_events({"type": "text", "text": "正文"}),
        ),
        sleep=lambda _s: None,
    ))
    assert {"type": "text", "text": "正文"} in out
    assert any(e.get("type") == "retry_notice" and e.get("category") == "ratelimit" for e in out)


def test_non_retryable_402_raises_immediately():
    with pytest.raises(_Exc402):
        list(stream_with_pretoken_retry(
            _factory_seq(_gen_fail_immediately(_Exc402("no balance"))),
            sleep=lambda _s: None,
        ))


def test_unknown_error_raises_immediately():
    with pytest.raises(ValueError):
        list(stream_with_pretoken_retry(
            _factory_seq(_gen_fail_immediately(ValueError("weird"))),
            sleep=lambda _s: None,
        ))


def test_retries_bounded_then_raises():
    delays = []
    with pytest.raises(_Exc502):
        list(stream_with_pretoken_retry(
            _factory_seq(
                _gen_fail_immediately(_Exc502("1")),
                _gen_fail_immediately(_Exc502("2")),
                _gen_fail_immediately(_Exc502("3")),
            ),
            sleep=delays.append,
        ))
    assert delays == [1.5, 3.0]  # 两次退避后第三次失败原样抛


def test_stop_event_set_no_retry():
    class _Stop:
        @staticmethod
        def is_set():
            return True

    with pytest.raises(_Exc502):
        list(stream_with_pretoken_retry(
            _factory_seq(_gen_fail_immediately(_Exc502("x"))),
            stop_event=_Stop(), sleep=lambda _s: None,
        ))


def test_opening_chunks_silent_retry():
    out = list(stream_with_pretoken_retry(
        _factory_seq(
            _gen_fail_immediately(_Exc502("gw")),
            _gen_events("第一段", "第二段"),
        ),
        is_commit=opening_chunk_commits, emit_retry_notice=False,
        sleep=lambda _s: None,
    ))
    assert out == ["第一段", "第二段"]  # 无 notice dict 混入裸字符串流


def test_opening_no_retry_after_first_chunk():
    with pytest.raises(_Exc502):
        list(stream_with_pretoken_retry(
            _factory_seq(_gen_events("已开写", fail_after=_Exc502("mid"))),
            is_commit=opening_chunk_commits, emit_retry_notice=False,
            sleep=lambda _s: None,
        ))
