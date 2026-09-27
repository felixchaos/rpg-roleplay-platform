"""嵌入熔断 / 凭据代理的整合审查跟进(巡检 2026-09-28 整合审查)。

几组修复合在一起之后留下的缝:
1. 代理用不了(UnsupportedProxy,urllib 不支持 SOCKS)只有 OpenAI 兼容通道认,Gemini 通道
   把它记成「返回的内容不是 embeddings 格式」(服务端类,连 3 次才熔断,提示也指错方向)。
   判断收进 _breaker.note_exception,各通道共用。
2. 地区封禁缓存是进程级的、按通道名记:用户照提示给 Gemini 凭据配了代理,缓存却不认代理,
   1 小时内每次都不发请求,提示还说「服务器」(本地模式下是用户自己的网络出口)。
3. 冷却生效中写库路径又失败(超时 / 5xx)会把更长的限流冷却缩短,查询路径提前恢复,
   继续吃「失败也计数」的中转站配额。

网络一律用假的 safe_urlopen,不依赖 core.outbound 对 SOCKS 是抛错还是退回环境代理。
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

import core.outbound as outbound
from platform_app.knowledge import embedding
from platform_app.knowledge.embedding import _breaker, _gemini

_RELAY = "https://relay.example/v1"
_GEMINI_HOST = "generativelanguage.googleapis.com"
_SOCKS_MSG = ("这项功能暂不支持 SOCKS 代理。请在「设置 → API & 模型」这个供应商的连接方式里"
              "改填 HTTP 代理(形如 http://127.0.0.1:7890),聊天和其它功能都能用。")


class _Resp:
    def __init__(self, payload: bytes):
        self._p = payload

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _dim() -> int:
    return embedding.EMBED_DIM or 4


class _FakeNet:
    """记录每次出站(url / 代理),按脚本应答。元素:"ok" / int(HTTP 错误码)/ ("code", headers)
    / "timeout" / "geo"(Google 地区封禁 400)/ "socks"(模拟 urllib 对 SOCKS 代理抛 UnsupportedProxy)。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, req, timeout=None, **kw):
        self.calls.append({"url": req.full_url, "proxy": kw.get("proxy")})
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        headers = {}
        if isinstance(step, tuple):
            step, headers = step
        if step == "socks":
            raise outbound.UnsupportedProxy(_SOCKS_MSG)
        if step == "timeout":
            raise TimeoutError("timed out")
        if step == "geo":
            body = (b'{"error": {"code": 400, "message": "User location is not supported for the API use.",'
                    b' "status": "FAILED_PRECONDITION"}}')
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body))
        if step == "ok":
            if _GEMINI_HOST in req.full_url:
                return _Resp(json.dumps({"embedding": {"values": [0.01] * _dim()}}).encode())
            n = len(json.loads(req.data.decode()).get("input") or [None])
            return _Resp(json.dumps({"data": [{"index": i, "embedding": [0.01] * _dim()}
                                              for i in range(n)]}).encode())
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


@pytest.fixture
def geo_cache():
    _gemini._GEO_BAN_CACHE.clear()
    yield _gemini._GEO_BAN_CACHE
    _gemini._GEO_BAN_CACHE.clear()


def _net(monkeypatch, script) -> _FakeNet:
    fake = _FakeNet(script)
    monkeypatch.setattr(outbound, "safe_urlopen", fake)
    return fake


def _local_mode(monkeypatch, local: bool = True):
    monkeypatch.setattr(outbound, "_ssrf_enforced", lambda: not local)


# ── 1. 代理用不了:各通道同一个判断 ─────────────────────────────────────────────
def test_note_exception_classifies_unsupported_proxy_as_config():
    with _breaker.attempt() as att:
        _breaker.note_exception(outbound.UnsupportedProxy(_SOCKS_MSG), "relay.example")
    assert att.kind == _breaker.KIND_CONFIG
    assert att.friendly.startswith("向量嵌入请求没有发出去")
    assert "SOCKS" in att.friendly and "embeddings 格式" not in att.friendly


def test_note_exception_redacts_proxy_credentials():
    with _breaker.attempt() as att:
        _breaker.note_exception(outbound.UnsupportedProxy("代理 socks5://alice:s3cret@10.0.0.2:1080 用不了"))
    assert att.kind == _breaker.KIND_CONFIG
    assert "s3cret" not in att.friendly and "alice" not in att.friendly


def test_gemini_channel_socks_proxy_is_config_not_server(monkeypatch, geo_cache):
    _local_mode(monkeypatch)
    _net(monkeypatch, ["socks"])
    with _breaker.attempt() as att:
        out = embedding._embed_via_gemini("gemini-embedding-001", "AIza-user", ["hi"],
                                          proxy="socks5://127.0.0.1:1080")
    assert out is None
    assert att.kind == _breaker.KIND_CONFIG
    assert "SOCKS" in att.friendly
    assert "embeddings 格式" not in att.friendly


def test_openai_channel_socks_proxy_same_judgement(monkeypatch):
    _local_mode(monkeypatch)
    _net(monkeypatch, ["socks"])
    with _breaker.attempt() as att:
        out = embedding._embed_via_openai("text-embedding-3-small", "sk", ["hi"], base_url=_RELAY,
                                          proxy="socks5://127.0.0.1:1080")
    assert out is None
    assert att.kind == _breaker.KIND_CONFIG
    assert att.friendly.startswith("向量嵌入请求没有发出去") and "SOCKS" in att.friendly


def test_gemini_proxy_that_works_just_goes_through(monkeypatch, geo_cache):
    """core.outbound 若对代理不再抛错(例如退回环境代理),通道照常出站,不记任何失败。"""
    _local_mode(monkeypatch)
    net = _net(monkeypatch, ["ok"])
    with _breaker.attempt() as att:
        out = embedding._embed_via_gemini("gemini-embedding-001", "AIza-user", ["hi"],
                                          proxy="socks5://127.0.0.1:1080")
    assert out and len(out[0]) == _dim()
    assert att.kind is None
    assert len(net.calls) == 1


# ── 2. 地区封禁:按出口判,配了凭据代理就是另一个出口 ─────────────────────────────
def test_credential_proxy_bypasses_process_geo_ban(monkeypatch, geo_cache):
    _local_mode(monkeypatch)
    _gemini._geo_ban_mark(_gemini._GEO_BAN_CHANNEL_GEMINI_NATIVE)
    net = _net(monkeypatch, ["ok"])
    out = embedding._embed_via_gemini("gemini-embedding-001", "AIza-user", ["hi"],
                                      proxy="http://127.0.0.1:7890")
    assert out, "配了代理就是换了出口:进程级封禁标记不该挡住这次请求"
    assert net.calls and net.calls[0]["proxy"] == "http://127.0.0.1:7890"


def test_geo_ban_behind_proxy_does_not_mark_process_cache(monkeypatch, geo_cache):
    """代理出口也被封:只说明这个代理不行,不能把「不走代理」的直连一起标记 1 小时。
    记成配置类(换个代理 / 换供应商之前不会好),提示说的是代理。"""
    _local_mode(monkeypatch)
    _net(monkeypatch, ["geo"])
    with _breaker.attempt() as att:
        assert embedding._embed_via_gemini("gemini-embedding-001", "AIza-user", ["hi"],
                                           proxy="http://127.0.0.1:7890") is None
    assert _gemini._GEO_BAN_CHANNEL_GEMINI_NATIVE not in geo_cache
    assert att.kind == _breaker.KIND_CONFIG
    assert "代理" in att.friendly and "服务器" not in att.friendly


@pytest.mark.parametrize("local,must,must_not", [
    (True, "当前网络出口", "服务器"),
    (False, "服务器", "当前网络出口"),
])
def test_geo_ban_hint_names_the_right_exit(monkeypatch, geo_cache, local, must, must_not):
    _local_mode(monkeypatch, local)
    _gemini._geo_ban_mark(_gemini._GEO_BAN_CHANNEL_GEMINI_NATIVE)
    net = _net(monkeypatch, ["ok"])
    with _breaker.attempt() as att:
        assert embedding._embed_via_gemini("gemini-embedding-001", "AIza-user", ["hi"]) is None
    assert net.calls == [], "没配凭据代理:仍按进程级封禁跳过直连"
    assert att.kind == _breaker.KIND_CONFIG
    assert must in att.friendly and must_not not in att.friendly
    assert "地区" in att.friendly


def test_direct_geo_ban_first_hit_opens_config_cooldown(monkeypatch, clock, geo_cache):
    """第一次撞上封禁就进配置类冷却(以前第一次按 400 请求级处理、第二次才熔断)。"""
    _local_mode(monkeypatch)
    net = _net(monkeypatch, ["geo"])
    for _ in range(3):
        embedding._embed_provider_dispatch("gemini", "gemini-embedding-001", "AIza-user", ["x"],
                                           task_type="RETRIEVAL_QUERY", user_id=5)
    assert len(net.calls) == 1
    assert _gemini._GEO_BAN_CHANNEL_GEMINI_NATIVE in geo_cache
    st = _breaker.status(_breaker.key_for(5, "gemini", "gemini-embedding-001", "", "AIza-user"))
    assert st is not None and st["kind"] == _breaker.KIND_CONFIG


# ── 3. 冷却只延长不缩短 ────────────────────────────────────────────────────────
def _use_config(monkeypatch, key="sk-a", base=_RELAY, api_id="openai", model="text-embedding-3-small"):
    monkeypatch.setattr(embedding, "_resolve_embed_config", lambda _uid: (api_id, model, key, base))
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)


def _bkey():
    return _breaker.key_for(1, "openai", "text-embedding-3-small", _RELAY, "sk-a")


def test_batch_timeout_does_not_shorten_rate_cooldown(monkeypatch, clock):
    _use_config(monkeypatch)
    net = _net(monkeypatch, [(429, {"Retry-After": "600"})])
    embedding.embed_query("a", user_id=1)
    clock.t += 30
    net.script = ["timeout"]
    assert embedding._embed_batch(["x"], user_id=1) is None      # 写库路径冷却中照常真打,撞上超时
    st = _breaker.status(_bkey())
    assert st["kind"] == _breaker.KIND_RATE, "限流冷却不能被超时改写"
    assert st["remaining"] >= 569
    clock.t += 61
    n = len(net.calls)
    assert embedding.embed_query("b", user_id=1) is None
    assert len(net.calls) == n, "限流冷却还剩 500 多秒,查询路径不许提前恢复"


def test_batch_5xx_strikes_do_not_shorten_rate_cooldown(monkeypatch, clock):
    _use_config(monkeypatch)
    net = _net(monkeypatch, [(429, {"Retry-After": "300"})])
    embedding.embed_query("a", user_id=1)
    net.script = [502]
    for _ in range(3):
        embedding._embed_batch(["x"], user_id=1)
    st = _breaker.status(_bkey())
    assert st["kind"] == _breaker.KIND_RATE and st["remaining"] >= 299


def test_longer_timeout_extends_short_rate_remainder(monkeypatch, clock):
    """限流冷却只剩 10s 时写库撞上超时:取更晚的到期时间(60s),原因仍记限流。"""
    _use_config(monkeypatch)
    net = _net(monkeypatch, [429])                # 默认 90s
    embedding.embed_query("a", user_id=1)
    clock.t += 80
    net.script = ["timeout"]
    embedding._embed_batch(["x"], user_id=1)
    st = _breaker.status(_bkey())
    assert st["kind"] == _breaker.KIND_RATE
    assert int(st["remaining"]) == 60


def test_config_error_during_rate_cooldown_takes_over(monkeypatch, clock):
    """限流冷却中写库撞上 401:改成配置类(写库循环据此立即放弃),时长取更长的那个。"""
    _use_config(monkeypatch)
    net = _net(monkeypatch, [(429, {"Retry-After": "600"})])
    embedding.embed_query("a", user_id=1)
    clock.t += 10
    net.script = [401]
    embedding._embed_batch(["x"], user_id=1)
    st = _breaker.status(_bkey())
    assert st["kind"] == _breaker.KIND_CONFIG and int(st["remaining"]) == 600
