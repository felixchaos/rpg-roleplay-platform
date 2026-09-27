"""嵌入熔断 / 凭据代理的整合审查跟进(巡检 2026-09-28 整合审查)。

几组修复合在一起之后留下的缝:
1. 代理用不了(UnsupportedProxy,urllib 不支持 SOCKS)只有 OpenAI 兼容通道认,Gemini 通道
   把它记成「返回的内容不是 embeddings 格式」(服务端类,连 3 次才熔断,提示也指错方向)。
   判断收进 _breaker.note_exception,各通道共用。
2. 地区封禁缓存是进程级的、按通道名记:用户照提示给 Gemini 凭据配了代理,缓存却不认代理,
   1 小时内每次都不发请求,提示还说「服务器」(本地模式下是用户自己的网络出口)。
3. 冷却生效中写库路径又失败(超时 / 5xx)会把更长的限流冷却缩短,查询路径提前恢复,
   继续吃「失败也计数」的中转站配额。
4. 代理没跟着「这次实际用的那份凭据」走:dispatch 按 (用户, api_id) 反查一次,admin/vip 用
   平台 EMBED_* 配置时被套上了用户自己聊天凭据里的代理。
5. 多 worker 下保存凭据的 reset_user 只清处理请求的那个进程:熔断单元 key 带上凭据版本
   (保存时间),重存之后所有 worker 都落到新单元。
6. _vertex 的「平台 Gemini 原生直连」不看平台配置是哪家,把 SiliconFlow 等别家的
   EMBED_API_KEY 放进 URL 发给了 Google。

网络一律用假的 safe_urlopen,不依赖 core.outbound 对 SOCKS 是抛错还是退回环境代理。
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

import core.llm_backend as llm_backend
import core.outbound as outbound
import platform_app.user_credentials as uc
from platform_app.knowledge import embedding
from platform_app.knowledge.embedding import _breaker, _gemini, _vertex

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


# ── 4. 代理跟着这次实际用的凭据走 ──────────────────────────────────────────────
def _creds_by_api(monkeypatch, table: dict):
    def _resolve(_uid, api_id, env_fallback=""):
        return dict(table.get(api_id) or {"key": "", "source": "none", "base_url_override": ""})
    monkeypatch.setattr(uc, "resolve_api_key", _resolve)


def _prefs(monkeypatch, api_id, model):
    monkeypatch.setattr(llm_backend, "resolve_preferred_api", lambda *a, **k: api_id)
    monkeypatch.setattr(llm_backend, "resolve_preferred_model", lambda *a, **k: model)


@pytest.fixture
def platform_relay(monkeypatch):
    monkeypatch.setenv("EMBED_API_KEY", "PLATFORM-KEY")
    monkeypatch.setenv("EMBED_BASE_URL", "https://platform-relay.example/v1")
    monkeypatch.setenv("EMBED_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(embedding, "DEFAULT_EMBED_API_ID", "openai")


def test_admin_on_platform_config_does_not_borrow_chat_credential_proxy(monkeypatch, clock, platform_relay):
    """admin/vip 没配嵌入凭据 → 用平台配置(openai + 平台地址)。他自己的 openai 聊天凭据里
    配的代理跟平台 key 毫无关系,不能套到平台请求上。"""
    _local_mode(monkeypatch)
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: True)
    _prefs(monkeypatch, "siliconflow", "BAAI/bge-m3")
    _creds_by_api(monkeypatch, {
        "openai": {"key": "sk-chat", "source": "user_db", "base_url_override": "",
                   "proxy": "http://127.0.0.1:7890"},
    })
    net = _net(monkeypatch, ["ok"])
    assert embedding.embed_query("你好", user_id=9) is not None
    assert net.calls[0]["url"].startswith("https://platform-relay.example/v1")
    assert net.calls[0]["proxy"] is None


def test_user_own_embed_credential_proxy_is_used(monkeypatch, clock, platform_relay):
    _local_mode(monkeypatch)
    _prefs(monkeypatch, "siliconflow", "BAAI/bge-m3")
    _creds_by_api(monkeypatch, {
        "siliconflow": {"key": "sk-sf", "source": "user_db", "base_url_override": _RELAY,
                        "proxy": "http://127.0.0.1:7890"},
    })
    net = _net(monkeypatch, ["ok"])
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)
    assert embedding.embed_query("你好", user_id=9) is not None
    assert net.calls[0]["proxy"] == "http://127.0.0.1:7890"
    # 召回 force 路径:同一份凭据、同一个代理
    embedding.embed_query("召回", user_id=9, force_api_id="siliconflow", force_model="BAAI/bge-m3")
    assert net.calls[-1]["proxy"] == "http://127.0.0.1:7890"


def test_force_path_platform_fallback_has_no_user_proxy(monkeypatch, clock, platform_relay):
    _local_mode(monkeypatch)
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: True)
    _creds_by_api(monkeypatch, {
        "siliconflow": {"key": "sk-sf", "source": "user_db", "base_url_override": _RELAY,
                        "proxy": "http://127.0.0.1:7890"},
    })
    net = _net(monkeypatch, ["ok"])
    assert embedding.embed_query("召回", user_id=9, force_api_id="openai",
                                 force_model="text-embedding-3-small") is not None
    assert net.calls[0]["url"].startswith("https://platform-relay.example/v1")
    assert net.calls[0]["proxy"] is None


def test_server_mode_never_uses_credential_proxy(monkeypatch, clock, platform_relay):
    _local_mode(monkeypatch, local=False)
    _prefs(monkeypatch, "siliconflow", "BAAI/bge-m3")
    _creds_by_api(monkeypatch, {
        "siliconflow": {"key": "sk-sf", "source": "user_db", "base_url_override": _RELAY,
                        "proxy": "http://127.0.0.1:7890"},
    })
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)
    net = _net(monkeypatch, ["ok"])
    assert embedding.embed_query("你好", user_id=9) is not None
    assert net.calls[0]["proxy"] is None


def test_gemini_socks_via_dispatch_opens_config_cooldown(monkeypatch, clock, geo_cache):
    """经 dispatch:一次就进配置类冷却,最近错误里写的是代理的事(设置页 / 拆书预检看得到)。"""
    _local_mode(monkeypatch)
    net = _net(monkeypatch, ["socks"])
    for _ in range(3):
        assert embedding._embed_provider_dispatch(
            "gemini", "gemini-embedding-001", "AIza-user", ["x"], task_type="RETRIEVAL_QUERY",
            user_id=5, proxy="socks5://127.0.0.1:1080") is None
    assert len(net.calls) == 1
    st = _breaker.status(_breaker.key_for(5, "gemini", "gemini-embedding-001", "", "AIza-user"))
    assert st is not None and st["kind"] == _breaker.KIND_CONFIG
    assert "SOCKS" in _breaker.last_error_for(5)


def test_geo_ban_behind_proxy_opens_cooldown_instead_of_hitting_every_turn(monkeypatch, clock, geo_cache):
    """代理出口也被封:按用户记配置类冷却,不再每回合都真打一次。"""
    _local_mode(monkeypatch)
    net = _net(monkeypatch, ["geo"])
    for _ in range(3):
        embedding._embed_provider_dispatch("gemini", "gemini-embedding-001", "AIza-user", ["x"],
                                           task_type="RETRIEVAL_QUERY", user_id=5,
                                           proxy="http://127.0.0.1:7890")
    assert len(net.calls) == 1


# ── 5. 重存凭据:所有 worker 都落到新熔断单元 ──────────────────────────────────
def test_resaved_credential_leaves_old_cooldown_without_reset_user(monkeypatch, clock):
    """另一个 worker 收到了保存请求(本进程的 reset_user 没被调用):凭据版本变了,
    本进程也立刻落到新单元,不用等 900s 的余额不足冷却过期;旧提示也不再显示。"""
    _local_mode(monkeypatch, local=False)
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)
    _prefs(monkeypatch, "openai", "text-embedding-3-small")
    cred = {"key": "sk-a", "source": "user_db", "base_url_override": _RELAY,
            "updated_at": "2026-09-28 10:00:00+08:00"}
    _creds_by_api(monkeypatch, {"openai": cred})
    net = _net(monkeypatch, [402])
    embedding.embed_query("a", user_id=1)
    embedding.embed_query("b", user_id=1)
    assert len(net.calls) == 1
    assert "last_error_hint" in embedding.embedding_preflight(1)

    cred["updated_at"] = "2026-09-28 10:05:00+08:00"   # 充值后同一把 key 重新保存
    net.script = ["ok"]
    assert "last_error_hint" not in embedding.embedding_preflight(1)
    assert embedding.embed_query("c", user_id=1) is not None
    assert len(net.calls) == 2


def test_same_credential_version_stays_in_cooldown(monkeypatch, clock):
    _local_mode(monkeypatch, local=False)
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)
    _prefs(monkeypatch, "openai", "text-embedding-3-small")
    _creds_by_api(monkeypatch, {"openai": {"key": "sk-a", "source": "user_db", "base_url_override": _RELAY,
                                           "updated_at": "2026-09-28 10:00:00+08:00"}})
    net = _net(monkeypatch, [402])
    for t in ("a", "b", "c"):
        embedding.embed_query(t, user_id=1)
    assert len(net.calls) == 1


def test_writer_breaker_key_matches_dispatch_with_credential_version(monkeypatch, clock):
    """写库循环按同一个熔断单元判断「配置类就立即放弃」:单元 key 带了凭据版本,两边要一致。"""
    import platform_app.db as dbmod
    from platform_app.knowledge.embedding import _writer

    class _DB:
        def execute(self, sql, params=None):
            rows = [{"id": 1, "content": "x" * 20}] if "embedding_vec is null" in sql else []

            class _R:
                def fetchall(self_inner):
                    return rows

                def fetchone(self_inner):
                    return None
            return _R()

    from contextlib import contextmanager

    @contextmanager
    def _connect():
        yield _DB()

    _local_mode(monkeypatch, local=False)
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)
    _prefs(monkeypatch, "openai", "text-embedding-3-small")
    _creds_by_api(monkeypatch, {"openai": {"key": "sk-a", "source": "user_db", "base_url_override": _RELAY,
                                           "updated_at": "2026-09-28 10:00:00+08:00"}})
    monkeypatch.setattr(dbmod, "connect", _connect)
    net = _net(monkeypatch, [401])
    sleeps: list[float] = []
    monkeypatch.setattr(_writer.time, "sleep", lambda s: sleeps.append(s))
    with pytest.raises(RuntimeError) as ei:
        _writer._embed_chunks_loop_inner(script_id=5, user_id=1)
    assert len(net.calls) == 1 and sleeps == []
    assert "401" in str(ei.value)


def test_resolve_api_key_carries_credential_version(monkeypatch):
    monkeypatch.setattr(uc, "get_credential", lambda *_a, **_k: {
        "api_id": "openai", "key": "sk-a", "auth_mode": "api_key", "base_url_override": "",
        "proxy": "", "updated_at": "2026-09-28 10:00:00+08:00"})
    got = uc.resolve_api_key(1, "openai")
    assert got["updated_at"] == "2026-09-28 10:00:00+08:00"
    monkeypatch.setattr(uc, "get_credential", lambda *_a, **_k: {
        "api_id": "ollama", "key": "", "auth_mode": "none", "base_url_override": "http://127.0.0.1:11434/v1",
        "proxy": "", "updated_at": "2026-09-28 11:00:00+08:00"})
    got = uc.resolve_api_key(1, "ollama")
    assert got["source"] == "user_db_no_auth" and got["updated_at"] == "2026-09-28 11:00:00+08:00"


def test_get_credential_returns_updated_at(monkeypatch):
    from contextlib import contextmanager

    row = {"api_id": "openai", "enabled": True, "encrypted_key": b"blob", "auth_mode": "api_key",
           "base_url_override": "", "metadata": {}, "updated_at": "2026-09-28 10:00:00+08:00"}

    class _DB:
        def execute(self, *_a, **_k):
            class _R:
                def fetchall(self_inner):
                    return [row]
            return _R()

    @contextmanager
    def _connect():
        yield _DB()

    monkeypatch.setattr(uc, "init_db", lambda: None)
    monkeypatch.setattr(uc, "connect", _connect)
    monkeypatch.setattr(uc, "decrypt_api_key", lambda *_a, **_k: "sk-a")
    got = uc.get_credential(1, "openai")
    assert got["key"] == "sk-a" and got["updated_at"] == "2026-09-28 10:00:00+08:00"


# ── 6. _vertex 的平台 Gemini 原生直连只给 Gemini 的 key ────────────────────────
class _FakeVertexClient:
    def __init__(self):
        self.calls = 0

        class _Models:
            def embed_content(inner, model, contents, config):
                self.calls += 1

                class _E:
                    values = [0.02] * _dim()

                class _Resp:
                    embeddings = [_E() for _ in contents]
                return _Resp()
        self.models = _Models()


@pytest.fixture
def vertex_sdk(monkeypatch):
    client = _FakeVertexClient()
    monkeypatch.setattr(_vertex, "_get_vertex_client", lambda user_id=None: client)
    monkeypatch.setattr(_vertex, "_is_admin", lambda _uid: True)
    return client


@pytest.mark.parametrize("api_id,base_url", [
    ("vertex_ai", "https://api.siliconflow.cn/v1"),     # 自部署:SiliconFlow key + 地址,EMBED_API_ID 留默认
    ("openai", ""),                                      # 显式 OpenAI 的 key
    ("siliconflow", "https://api.siliconflow.cn/v1"),
])
def test_vertex_does_not_send_non_gemini_platform_key_to_google(monkeypatch, vertex_sdk, geo_cache,
                                                                api_id, base_url):
    monkeypatch.setenv("EMBED_API_KEY", "sk-not-a-gemini-key")
    if base_url:
        monkeypatch.setenv("EMBED_BASE_URL", base_url)
    else:
        monkeypatch.delenv("EMBED_BASE_URL", raising=False)
    monkeypatch.setattr(embedding, "DEFAULT_EMBED_API_ID", api_id)
    net = _net(monkeypatch, ["ok"])
    out = embedding._embed_via_vertex("text-embedding-004", ["x"], user_id=None)
    assert out and vertex_sdk.calls == 1
    assert net.calls == [], "平台 key 不是 Gemini 的,不许拼进 URL 发给 Google"


@pytest.mark.parametrize("api_id,base_url", [
    ("vertex_ai", ""),                                                  # 历史配法:只填了一把 Gemini key
    ("openai", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("vertex_ai", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("gemini", ""),
])
def test_vertex_prefers_native_rest_for_platform_gemini_key(monkeypatch, vertex_sdk, geo_cache,
                                                            api_id, base_url):
    monkeypatch.setenv("EMBED_API_KEY", "AIza-platform")
    if base_url:
        monkeypatch.setenv("EMBED_BASE_URL", base_url)
    else:
        monkeypatch.delenv("EMBED_BASE_URL", raising=False)
    monkeypatch.setattr(embedding, "DEFAULT_EMBED_API_ID", api_id)
    net = _net(monkeypatch, ["ok"])
    out = embedding._embed_via_vertex("text-embedding-004", ["x"], user_id=None)
    assert out
    assert len(net.calls) == 1 and _GEMINI_HOST in net.calls[0]["url"]
    assert vertex_sdk.calls == 0
