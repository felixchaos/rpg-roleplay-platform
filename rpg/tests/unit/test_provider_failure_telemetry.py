"""test_provider_failure_telemetry.py — 「错误有没有被拦截器捕获」必须可从库里答出来。

v1.84.0 之前:provider 失败只进日志和 SSE,**一条都不落库** —— 近 60 天 token_usage 里
0 条错误记录。于是「拦截率多少 / 多少走了未分类兜底 / 哪个渠道在坏」这些问题,只能靠
反向扫 messages 里有没有原始报错痕迹来间接推断。

`_client_safe_error` 是所有流式错误的**唯一漏斗**(chat 与 opening 都经它),它已经分好类、
生成了 error_id、写了日志。本文件锁住:**它的每一条分支都要落一行**,尤其是未分类那条 ——
那正是「错误码 Exxx」盲区(码是随机的、反查不到),不落库就永远数不出它有多少。
"""
from __future__ import annotations

import pytest

from routes.game._shared import _client_safe_error


@pytest.fixture
def captured(monkeypatch):
    rows: list[dict] = []
    import platform_app.usage as usage_mod
    monkeypatch.setattr(usage_mod, "record_provider_failure",
                        lambda **kw: rows.append(kw))
    return rows


class _Status:
    """带 HTTP status 的假 provider 异常(classify_provider_error 按 status 分类)。"""
    def __init__(self, status: int, msg: str):
        self.status_code = status
        self._msg = msg

    def __str__(self) -> str:
        return self._msg


class FakeProviderError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        if status is not None:
            self.status_code = status


def test_unclassified_error_is_recorded(captured):
    """未分类分支 = 「错误码 Exxx」盲区。它最需要被数出来。"""
    msg = _client_safe_error(RuntimeError("something nobody has seen before"),
                             user_id=7, save_id=42, api_id="deepseek", model="v4-pro")
    assert "错误码 E" in msg
    assert len(captured) == 1, "未分类错误没落库 —— 盲区依旧不可测"
    row = captured[0]
    assert row["category"] == "unclassified"
    assert row["user_id"] == 7 and row["save_id"] == 42
    assert row["api_id"] == "deepseek" and row["model"] == "v4-pro"
    assert row["exc_type"] == "RuntimeError"
    assert row["error_id"].startswith("E")


def test_classified_error_is_recorded_with_its_category(captured):
    _client_safe_error(FakeProviderError("insufficient balance", status=402), user_id=1)
    assert len(captured) == 1
    assert captured[0]["category"] == "balance", captured[0]


def test_missing_credential_is_recorded(captured):
    """构造期异常(没 status、没响应体)—— 曾经的 <100ms 未分类盲区。"""
    _client_safe_error(Exception("Missing credentials"), user_id=2)
    assert len(captured) == 1
    assert captured[0]["category"] == "auth", captured[0]


def test_runtime_prereq_branch_is_recorded(captured):
    _client_safe_error(RuntimeError("未找到 Vertex AI Service Account。"), user_id=3)
    assert len(captured) == 1
    assert captured[0]["category"] == "runtime_prereq"


def test_error_id_in_message_matches_the_recorded_row(captured):
    """客户端拿到的码必须能在库里查到 —— 否则玩家报「错误码 Exxx」我们依旧反查不到。"""
    msg = _client_safe_error(RuntimeError("boom"), user_id=9)
    eid = msg.split("错误码 ")[1].rstrip(")）")
    assert captured[0]["error_id"] == eid, (msg, captured[0])


def test_detail_is_redacted(captured):
    """detail 存的是 provider 原话,必须过脱敏 —— 日志能留,库里也不能留明文 key。

    假 key **运行时拼装**,不写成字面量:仓库的 pre-commit 密钥扫描会(正确地)拦下
    看起来像真 key 的字符串,第一版就是这么被拦的。
    """
    fake_key = "sk-" + ("x" * 32)
    _client_safe_error(RuntimeError(f"bad key {fake_key}"), user_id=4)
    assert fake_key not in captured[0]["detail"], captured[0]


def test_telemetry_failure_never_breaks_error_handling(monkeypatch):
    """遥测挂了不许把错误处理本身弄挂 —— 玩家该收到的文案一个字不能少。"""
    import platform_app.usage as usage_mod

    def _boom(**kw):
        raise RuntimeError("telemetry down")

    monkeypatch.setattr(usage_mod, "record_provider_failure", _boom)
    msg = _client_safe_error(RuntimeError("boom"), user_id=5)
    assert "错误码 E" in msg


def test_both_stream_entrypoints_pass_context():
    """chat 与 opening 两条流都必须把上下文传进漏斗,否则落库行是无主的。"""
    import inspect

    from routes.game import chat as chat_mod
    from routes.game import opening as opening_mod
    for mod, scen in ((chat_mod, '"chat"'), (opening_mod, '"opening"')):
        src = inspect.getsource(mod)
        assert "_client_safe_error(" in src
        assert "user_id=" in src and "api_id=" in src, f"{mod.__name__} 没传上下文"
        assert f"scenario={scen}" in src, f"{mod.__name__} 的 scenario 不对"
