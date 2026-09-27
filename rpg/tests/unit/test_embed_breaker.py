"""嵌入供应商熔断 + 回合内查询向量复用(巡检 2026-09-28 F7)。

症状:嵌入配置有问题的用户(中转站 429 / 余额不足 402 / 模型 404 / key 失效 401 / 超时),
每回合对同一个坏供应商连打 4-5 次嵌入请求(只有 2 种不同文本),下一回合照打不误;
对「每分钟 2 次、失败也计数」的中转站,这些请求把用户自己的聊天配额一起吃掉。
另:最近一次失败的报错是进程级全局,A 用户的中转站主机名会出现在 B 用户的提示里。

这里用假的 safe_urlopen 数真实发出的 HTTP 次数,逐条断言:
- 429 / 401 / 402 / 404 / 超时 → 冷却期内查询路径 0 次请求,进入冷却只打一条 WARNING;
- 冷却到期恢复;Retry-After 生效;到期后再 429 冷却翻倍;
- 400(内容审核 / 超长,跟这条文本有关)不熔断;5xx 连续 3 次才熔断;
- 200 但响应体坏 → 按服务端故障计,不当「没凭据」;
- 换 key 立即恢复;保存凭据(reset_user)立即恢复;
- 用户之间互不影响,提示按用户隔离;
- 写库路径:限流冷却中照常真打(交给重试循环退避),配置类冷却才短路并立刻报错;
- 同一请求内同一文本只嵌入一次,失败也只打一次。
"""
from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest

import core.outbound as outbound
from core import request_cache
from platform_app.knowledge import embedding
from platform_app.knowledge.embedding import _breaker

_RELAY = "https://relay.example/v1"
_LOGGER = "platform_app.knowledge.embedding"


class _Resp:
    def __init__(self, payload: bytes):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_payload(n: int) -> bytes:
    dim = embedding.EMBED_DIM or 4
    return json.dumps({"data": [{"index": i, "embedding": [0.01] * dim} for i in range(n)]}).encode()


class _FakeNet:
    """按脚本逐次响应:元素是 int(HTTP 错误码)/ ("code", headers) / "ok" / "timeout" / "garbage"。
    脚本耗尽后重复最后一个。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data.decode())
        self.calls.append({"url": req.full_url, "auth": req.get_header("Authorization"), "input": body.get("input")})
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        headers = {}
        if isinstance(step, tuple):
            step, headers = step
        if step == "ok":
            return _Resp(_ok_payload(len(body.get("input") or [None])))
        if step == "garbage":
            return _Resp(b'{"error": {"message": "quota exceeded"}}')  # 200 但不是 embeddings 格式
        if step == "timeout":
            raise TimeoutError("timed out")
        raise urllib.error.HTTPError(req.full_url, int(step), "err", headers,
                                     io.BytesIO(b'{"error":{"message":"nope"}}'))


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(_breaker, "_clock", c)
    return c


def _use_config(monkeypatch, key="sk-a", base=_RELAY, api_id="openai", model="text-embedding-3-small"):
    monkeypatch.setattr(embedding, "_resolve_embed_config", lambda _uid: (api_id, model, key, base))
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)


def _net(monkeypatch, script):
    fake = _FakeNet(script)
    monkeypatch.setattr(outbound, "safe_urlopen", fake)
    return fake


def _cooldown_warnings(caplog):
    return [r for r in caplog.records if "冷却" in r.getMessage() and r.levelno == logging.WARNING]


# ── 一、各类失败的冷却 ────────────────────────────────────────────────────────
def test_429_opens_cooldown_query_path_sends_nothing(monkeypatch, clock, caplog):
    _use_config(monkeypatch)
    net = _net(monkeypatch, [429])
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    assert embedding.embed_query("第一句", user_id=1) is None
    assert len(net.calls) == 1
    for text in ("第二句", "第三句", "第四句"):
        assert embedding.embed_query(text, user_id=1) is None
    assert len(net.calls) == 1, "冷却期内查询路径不许再发请求"
    assert len(_cooldown_warnings(caplog)) == 1, "进入冷却只记一条"
    # 默认 90s 冷却;到期恢复
    clock.t += 91
    net.script = ["ok"]
    assert embedding.embed_query("第五句", user_id=1) is not None
    assert len(net.calls) == 2


def test_retry_after_is_honored_and_repeat_429_escalates(monkeypatch, clock):
    _use_config(monkeypatch)
    net = _net(monkeypatch, [(429, {"Retry-After": "200"})])
    embedding.embed_query("a", user_id=1)
    clock.t += 150
    embedding.embed_query("b", user_id=1)
    assert len(net.calls) == 1, "Retry-After=200 → 150s 时仍在冷却"
    clock.t += 51
    net.script = [429]  # 到期后再次 429,没带 Retry-After
    embedding.embed_query("c", user_id=1)
    assert len(net.calls) == 2
    st = _breaker.status(_breaker.key_for(1, "openai", "text-embedding-3-small", _RELAY, "sk-a"))
    assert st["kind"] == _breaker.KIND_RATE
    assert st["remaining"] >= 399, "到期后又被 429:冷却翻倍(200 → 400)"


@pytest.mark.parametrize("code,cooldown", [(401, 600), (403, 600), (404, 600), (402, 900)])
def test_config_errors_cool_down_long(monkeypatch, clock, code, cooldown):
    _use_config(monkeypatch)
    net = _net(monkeypatch, [code])
    embedding.embed_query("a", user_id=1)
    clock.t += cooldown - 5
    embedding.embed_query("b", user_id=1)
    assert len(net.calls) == 1
    clock.t += 10
    net.script = ["ok"]
    assert embedding.embed_query("c", user_id=1) is not None
    assert len(net.calls) == 2


def test_timeout_trips_immediately_for_60s(monkeypatch, clock):
    _use_config(monkeypatch)
    net = _net(monkeypatch, ["timeout"])
    embedding.embed_query("a", user_id=1)
    embedding.embed_query("b", user_id=1)
    assert len(net.calls) == 1, "超时首次即熔断,免得一回合串行等好几个 60s"
    clock.t += 61
    embedding.embed_query("c", user_id=1)
    assert len(net.calls) == 2


def test_400_is_request_level_not_breaker(monkeypatch, clock):
    """内容审核拒绝 / 输入超长是这条文本的事:一句敏感话不能让该用户整段时间没有向量召回。"""
    _use_config(monkeypatch)
    net = _net(monkeypatch, [400])
    assert embedding.embed_query("敏感的一句", user_id=1) is None
    n = len(net.calls)  # 带 dimensions 400 → 去掉 dimensions 重试一次
    net.script = ["ok"]
    assert embedding.embed_query("正常的一句", user_id=1) is not None
    assert len(net.calls) == n + 1


def test_5xx_trips_only_after_three_in_a_row(monkeypatch, clock):
    _use_config(monkeypatch)
    net = _net(monkeypatch, [502])
    for i in range(3):
        embedding.embed_query(f"q{i}", user_id=1)
    assert len(net.calls) == 3
    embedding.embed_query("q3", user_id=1)
    assert len(net.calls) == 3, "连续 3 次 5xx 后才熔断"


def test_200_with_error_body_counts_as_server_fault_not_missing_credential(monkeypatch, clock):
    _use_config(monkeypatch)
    net = _net(monkeypatch, ["garbage"])
    embedding.embed_query("a", user_id=1)
    embedding.embed_query("b", user_id=1)
    assert len(net.calls) == 2, "不是「没凭据」,不应一次就长冷却"
    msg = _breaker.last_error_for(1)
    assert "embeddings" in msg


# ── 二、恢复:换 key / 保存凭据 ────────────────────────────────────────────────
def test_new_key_recovers_immediately(monkeypatch, clock):
    _use_config(monkeypatch, key="sk-old")
    net = _net(monkeypatch, [401])
    embedding.embed_query("a", user_id=1)
    _use_config(monkeypatch, key="sk-new")
    net.script = ["ok"]
    assert embedding.embed_query("b", user_id=1) is not None
    assert len(net.calls) == 2
    assert net.calls[-1]["auth"] == "Bearer sk-new"


def test_reset_user_recovers_same_key(monkeypatch, clock):
    """充值后 key 没变:保存凭据时 reset_user 解除冷却,不用等 15 分钟。"""
    _use_config(monkeypatch)
    net = _net(monkeypatch, [402])
    embedding.embed_query("a", user_id=1)
    _breaker.reset_user(1)
    net.script = ["ok"]
    assert embedding.embed_query("b", user_id=1) is not None
    assert len(net.calls) == 2


class _JsonRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.parametrize("route", ["set", "delete"])
def test_credentials_api_resets_breaker(monkeypatch, clock, route):
    """保存 / 删除凭据后,该用户的冷却和最近错误立即清掉(充值后 key 没变也能马上恢复);
    别的用户不受影响。走真的路由函数,不做源码字面量核对。"""
    import asyncio

    from platform_app import user_credentials
    from platform_app.api.me import credentials as cred_api

    net = _net(monkeypatch, [402])
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)
    keys = {1: "sk-user-a", 2: "sk-user-b"}
    monkeypatch.setattr(embedding, "_resolve_embed_config",
                        lambda uid: ("openai", "text-embedding-3-small", keys[uid], _RELAY))
    embedding.embed_query("a", user_id=1)
    embedding.embed_query("a", user_id=2)
    k1 = _breaker.key_for(1, "openai", "text-embedding-3-small", _RELAY, keys[1])
    k2 = _breaker.key_for(2, "openai", "text-embedding-3-small", _RELAY, keys[2])
    assert _breaker.status(k1) and _breaker.status(k2)

    monkeypatch.setattr(user_credentials, "set_credential", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(user_credentials, "delete_credential", lambda *a, **k: {"ok": True})
    user = {"id": 1, "role": "admin"}
    if route == "set":
        resp = asyncio.run(cred_api.api_set_credential(
            _JsonRequest({"api_id": "openai", "api_key": "sk-user-a"}), user=user))
    else:
        resp = asyncio.run(cred_api.api_delete_credential(_JsonRequest({"api_id": "openai"}), user=user))
    assert resp.status_code == 200
    assert _breaker.status(k1) is None, "凭据变了,该用户的冷却要清掉"
    assert _breaker.last_error_for(1) == ""
    assert _breaker.status(k2) is not None, "别的用户的冷却不受影响"
    net.script = ["ok"]
    assert embedding.embed_query("b", user_id=1) is not None


def test_reset_user_none_keeps_platform_cooldown(monkeypatch, clock):
    """系统任务(user_id=None)开始建向量不代表平台 key 恢复了:平台单元的冷却不能被顺手清掉。"""
    net = _net(monkeypatch, [429])
    monkeypatch.setattr(embedding, "_resolve_embed_config",
                        lambda _uid: ("openai", "text-embedding-3-small", "PLATFORM-KEY", _RELAY))
    embedding.embed_query("a", user_id=None)
    bkey = _breaker.key_for(None, "openai", "text-embedding-3-small", _RELAY, "PLATFORM-KEY")
    assert _breaker.status(bkey) is not None
    _breaker.reset_user(None)
    assert _breaker.status(bkey) is not None
    embedding.embed_query("b", user_id=None)
    assert len(net.calls) == 1


# ── 三、用户隔离 ──────────────────────────────────────────────────────────────
def test_users_on_same_relay_do_not_block_each_other(monkeypatch, clock):
    net = _net(monkeypatch, [429])
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)
    keys = {1: "sk-user-a", 2: "sk-user-b"}
    monkeypatch.setattr(embedding, "_resolve_embed_config",
                        lambda uid: ("openai", "text-embedding-3-small", keys[uid], _RELAY))
    embedding.embed_query("a", user_id=1)
    net.script = ["ok"]
    assert embedding.embed_query("b", user_id=2) is not None
    assert len(net.calls) == 2


def test_preflight_hint_is_per_user(monkeypatch, clock):
    net = _net(monkeypatch, [401])
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)
    keys = {1: "sk-user-a", 2: "sk-user-b"}
    monkeypatch.setattr(embedding, "_resolve_embed_config",
                        lambda uid: ("openai", "text-embedding-3-small", keys[uid], _RELAY))
    embedding.embed_query("a", user_id=1)
    assert len(net.calls) == 1
    pa = embedding.embedding_preflight(1)
    pb = embedding.embedding_preflight(2)
    assert "relay.example" in pa.get("last_error_hint", "")
    assert "last_error_hint" not in pb, "B 用户不该看到 A 的中转站主机名和报错"
    assert not hasattr(embedding, "_last_openai_embed_error"), "进程级全局错误已删"


# ── 四、写库路径 ──────────────────────────────────────────────────────────────
def test_batch_path_still_sends_during_rate_cooldown(monkeypatch, clock):
    """一次 429 不能让角色卡 / 世界书 / canon 的后续批次在几毫秒内全被跳过。"""
    _use_config(monkeypatch)
    net = _net(monkeypatch, [429])
    embedding.embed_query("a", user_id=1)  # 查询路径打开限流冷却
    net.script = ["ok"]
    assert embedding._embed_batch(["x", "y"], user_id=1) is not None
    assert len(net.calls) == 2
    # 查询路径仍在冷却(写库成功会清掉;这里再打开一次看查询路径是否短路)
    net.script = [429]
    embedding.embed_query("b", user_id=1)
    n = len(net.calls)
    embedding.embed_query("c", user_id=1)
    assert len(net.calls) == n


def test_batch_path_short_circuits_on_config_cooldown(monkeypatch, clock):
    _use_config(monkeypatch)
    net = _net(monkeypatch, [401])
    assert embedding._embed_batch(["x"], user_id=1) is None
    assert embedding._embed_batch(["y"], user_id=1) is None
    assert len(net.calls) == 1


class _WriterDB:
    """document_chunks 永远有一批没嵌入的行;其它查询返回空。"""

    def execute(self, sql, params=None):
        rows = [{"id": 1, "content": "x" * 20}] if "embedding_vec is null" in sql and "document_chunks" in sql else []

        class _R:
            def fetchall(self_inner):
                return rows

            def fetchone(self_inner):
                return None
        return _R()


def _fake_connect():
    from contextlib import contextmanager

    @contextmanager
    def _c():
        yield _WriterDB()
    return _c


def test_writer_gives_up_at_once_on_config_error(monkeypatch, clock):
    """key 无效:第一批 401 就停并带上原因,不再 5×30s 空转。"""
    import platform_app.db as dbmod
    from platform_app.knowledge.embedding import _writer

    monkeypatch.setattr(dbmod, "connect", _fake_connect())
    monkeypatch.setattr(_writer, "_resolve_embed_config",
                        lambda _uid: ("openai", "text-embedding-3-small", "sk-a", _RELAY))
    _use_config(monkeypatch)
    net = _net(monkeypatch, [401])
    sleeps: list[float] = []
    monkeypatch.setattr(_writer.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(RuntimeError) as ei:
        _writer._embed_chunks_loop_inner(script_id=5, user_id=1)
    assert len(net.calls) == 1
    assert sleeps == []
    assert "401" in str(ei.value) and "relay.example" in str(ei.value)


def test_writer_backs_off_by_rate_cooldown_and_keeps_retry_budget(monkeypatch, clock):
    import platform_app.db as dbmod
    from platform_app.knowledge.embedding import _writer

    monkeypatch.setattr(dbmod, "connect", _fake_connect())
    monkeypatch.setattr(_writer, "_resolve_embed_config",
                        lambda _uid: ("openai", "text-embedding-3-small", "sk-a", _RELAY))
    _use_config(monkeypatch)
    net = _net(monkeypatch, [(429, {"Retry-After": "100"})])
    sleeps: list[float] = []
    monkeypatch.setattr(_writer.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(RuntimeError):
        _writer._embed_chunks_loop_inner(script_id=5, user_id=1)
    assert len(net.calls) == embedding._MAX_EMBED_BATCH_RETRIES, "限流时每次重试都是真请求,重试预算不被短路虚耗"
    assert sleeps and sleeps[0] == 100, "按冷却剩余时间退避(至少 30s,最多 120s)"


def test_writer_start_resets_breaker(monkeypatch, clock):
    """用户主动建向量:先清熔断,至少真打一次(充值后 key 没变的情况)。"""
    import platform_app.db as dbmod
    from platform_app.knowledge.embedding import _writer

    _use_config(monkeypatch)
    net = _net(monkeypatch, [402])
    embedding.embed_query("a", user_id=1)
    assert len(net.calls) == 1
    monkeypatch.setattr(dbmod, "connect", _fake_connect())
    monkeypatch.setattr(_writer, "_resolve_embed_config",
                        lambda _uid: ("openai", "text-embedding-3-small", "sk-a", _RELAY))
    monkeypatch.setattr(_writer.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        _writer._embed_chunks_loop_inner(script_id=5, user_id=1)
    assert len(net.calls) == 2, "建向量开头清了熔断,真打了一次"


# ── 五、回合内复用 ────────────────────────────────────────────────────────────
def test_same_text_embedded_once_per_request(monkeypatch, clock):
    _use_config(monkeypatch)
    net = _net(monkeypatch, ["ok"])
    request_cache.reset_request_caches()
    try:
        for _ in range(3):
            assert embedding.embed_query("我去找康拉德", user_id=1) is not None
        for _ in range(2):
            assert embedding.embed_query("康拉德 找 我去", user_id=1) is not None
        assert len(net.calls) == 2, "一回合只有两种文本 → 两次请求"
        # 下一个请求重新计
        request_cache.reset_request_caches()
        embedding.embed_query("我去找康拉德", user_id=1)
        assert len(net.calls) == 3
    finally:
        request_cache._embed_vec_cache.set(None)


def test_failed_turn_costs_one_request_then_zero(monkeypatch, clock):
    _use_config(monkeypatch)
    net = _net(monkeypatch, [429])
    request_cache.reset_request_caches()
    try:
        for text in ("原句", "原句", "二元组汤", "二元组汤", "二元组汤"):
            embedding.embed_query(text, user_id=1)
        assert len(net.calls) == 1, "失败回合:第一种文本失败即熔断,第二种不再发"
        request_cache.reset_request_caches()  # 下一回合
        for text in ("原句2", "二元组汤2"):
            embedding.embed_query(text, user_id=1)
        assert len(net.calls) == 1, "冷却中的下一回合:0 次"
    finally:
        request_cache._embed_vec_cache.set(None)


def test_no_request_cache_outside_request_context(monkeypatch, clock):
    """后台线程 / cron 不在请求里:不缓存,行为与改造前一致。"""
    _use_config(monkeypatch)
    net = _net(monkeypatch, ["ok"])
    request_cache._embed_vec_cache.set(None)
    embedding.embed_query("同一句", user_id=1)
    embedding.embed_query("同一句", user_id=1)
    assert len(net.calls) == 2


# ── 六、子通道归属 / 平台兜底不重复打 ──────────────────────────────────────────
def test_vertex_native_rate_limit_not_recorded_as_missing_credential(monkeypatch, clock):
    """_embed_via_vertex 先走平台 Gemini 原生(被 429)、再发现没有 SA:以真打的那次为准记限流,
    别记成「没凭据」—— 那会让写库路径也被短路 5 分钟。"""
    from platform_app.knowledge.embedding import _gemini, _vertex

    monkeypatch.setenv("EMBED_API_KEY", "PLATFORM-KEY")
    monkeypatch.setattr(_vertex, "_is_admin", lambda _uid: True)
    monkeypatch.setattr(_vertex, "_get_vertex_client", lambda user_id=None: None)
    monkeypatch.setattr(_gemini, "_geo_ban_active", lambda _ch: False)
    _net(monkeypatch, [429])
    assert embedding._embed_provider_dispatch("vertex_ai", "text-embedding-004", "", ["x"],
                                              task_type="RETRIEVAL_QUERY", user_id=None) is None
    st = _breaker.status(_breaker.key_for(None, "vertex_ai", "text-embedding-004", "", ""))
    assert st is not None and st["kind"] == _breaker.KIND_RATE


def test_vertex_without_sa_is_no_cred(monkeypatch, clock):
    from platform_app.knowledge.embedding import _vertex

    monkeypatch.delenv("EMBED_API_KEY", raising=False)
    monkeypatch.setattr(_vertex, "_get_vertex_client", lambda user_id=None: None)
    assert embedding._embed_provider_dispatch("vertex_ai", "text-embedding-004", "", ["x"], user_id=3) is None
    st = _breaker.status(_breaker.key_for(3, "vertex_ai", "text-embedding-004", "", ""))
    assert st is not None and st["kind"] == _breaker.KIND_NO_CRED


def test_admin_platform_config_failure_not_retried_verbatim(monkeypatch, clock):
    """admin/vip 没自配时第一步用的就是平台配置;失败后「平台兜底」别对同一端点原样再打一次。"""
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: True)
    monkeypatch.setattr(embedding, "_platform_fallback_config",
                        lambda: ("openai", "text-embedding-3-small", "PLATFORM-KEY", _RELAY))
    monkeypatch.setattr(embedding, "_resolve_embed_config",
                        lambda _uid: ("openai", "text-embedding-3-small", "PLATFORM-KEY", _RELAY))
    net = _net(monkeypatch, [502])
    assert embedding._embed_batch(["x"], user_id=1) is None
    assert len(net.calls) == 1


# ── 七、审查补充:翻倍基数 / 冷却原因变化 / 地区封禁 ────────────────────────────
def test_batch_429_during_rate_cooldown_does_not_reset_escalation(monkeypatch, clock):
    """冷却中写库路径又撞 429 走「顺延」:翻倍基数不能被顺延出来的较短剩余时长拉低,
    否则下一次到期再 429 时冷却反而回退。"""
    _use_config(monkeypatch)
    net = _net(monkeypatch, [429])
    bkey = _breaker.key_for(1, "openai", "text-embedding-3-small", _RELAY, "sk-a")
    embedding.embed_query("a", user_id=1)                 # 90s
    clock.t += 91
    embedding.embed_query("b", user_id=1)                 # 到期再 429 → 180s
    assert int(_breaker.status(bkey)["remaining"]) == 180
    clock.t += 100                                        # 剩 80s
    assert embedding._embed_batch(["x"], user_id=1) is None   # 写库路径冷却中真打 → 顺延
    assert len(net.calls) == 3
    clock.t += 181
    embedding.embed_query("c", user_id=1)                 # 到期再 429 → 应从 180 翻到 360
    assert int(_breaker.status(bkey)["remaining"]) == 360


def test_cooldown_reason_change_is_logged(monkeypatch, clock, caplog):
    """超时冷却中,写库路径又撞上 429:冷却原因变了,要打一条(否则日志里只看得到超时)。"""
    _use_config(monkeypatch)
    net = _net(monkeypatch, ["timeout"])
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    embedding.embed_query("a", user_id=1)
    assert len(_cooldown_warnings(caplog)) == 1
    net.script = [429]
    embedding._embed_batch(["x"], user_id=1)
    warns = _cooldown_warnings(caplog)
    assert len(warns) == 2 and "HTTP 429" in warns[-1].getMessage()
    embedding._embed_batch(["y"], user_id=1)              # 同类再失败不重复刷
    assert len(_cooldown_warnings(caplog)) == 2


def test_direct_gemini_geo_ban_skip_opens_config_cooldown(monkeypatch, clock, caplog):
    """用户选的就是 Gemini、服务器所在地区被 Google 拒绝:封禁期间的跳过记成配置类冷却,
    提示写明原因;不再每次检索都算一次「真失败」打 WARNING。"""
    from platform_app.knowledge.embedding import _gemini

    monkeypatch.setattr(_gemini, "_geo_ban_active", lambda _ch: True)
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)
    monkeypatch.setattr(embedding, "_resolve_embed_config",
                        lambda _uid: ("gemini", "gemini-embedding-001", "AIza-user", ""))
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    for text in ("一", "二", "三"):
        assert embedding.embed_query(text, user_id=5) is None
    st = _breaker.status(_breaker.key_for(5, "gemini", "gemini-embedding-001", "", "AIza-user"))
    assert st is not None and st["kind"] == _breaker.KIND_CONFIG
    assert "地区" in _breaker.last_error_for(5)
    no_vec_warns = [r for r in caplog.records
                    if "returned no vectors" in r.getMessage() and r.levelno == logging.WARNING]
    assert len(no_vec_warns) == 1 and len(_cooldown_warnings(caplog)) == 1


def test_vertex_geo_ban_skip_does_not_mask_missing_sa(monkeypatch, clock):
    """_vertex 里平台 Gemini 原生只是可选的优先通道:它在封禁期间被跳过时不记熔断,
    以后面 SDK 那一路的结果为准(这里没有 SA → 记「没凭据」)。"""
    from platform_app.knowledge.embedding import _gemini, _vertex

    monkeypatch.setenv("EMBED_API_KEY", "PLATFORM-KEY")
    monkeypatch.setattr(_vertex, "_is_admin", lambda _uid: True)
    monkeypatch.setattr(_vertex, "_get_vertex_client", lambda user_id=None: None)
    monkeypatch.setattr(_gemini, "_geo_ban_active", lambda _ch: True)
    assert embedding._embed_provider_dispatch("vertex_ai", "text-embedding-004", "", ["x"],
                                              task_type="RETRIEVAL_QUERY", user_id=None) is None
    st = _breaker.status(_breaker.key_for(None, "vertex_ai", "text-embedding-004", "", ""))
    assert st is not None and st["kind"] == _breaker.KIND_NO_CRED
