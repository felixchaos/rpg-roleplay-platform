"""反馈 #104:千问 key 在 RAG 里报「API Key 无效(401)」,可拉模型一切正常。

实测:用户贴回的原始响应是 `Incorrect API key provided: sk-b23a5***…1a14. You can find your
API key …` —— 这是 **OpenAI** 的报错格式(DashScope 的是 `Incorrect API key provided. For
details, see: https://help.aliyun.com/…`)。也就是说千问的 key 被发到了 api.openai.com。

根因:嵌入链路取 base_url 只看「凭据 override → EMBED_BASE_URL → 静态模板 default_api_for」,
自己加的供应商不在静态模板里 → 解析成空串 → `_embed_via_openai` 把空串当官方 OpenAI。
聊天/拉模型走 `base_url_for`(live catalog),所以一直正常 —— 典型的修 A 漏 B。

两道确定性修复,各自断言:
1. 地址解析补 live catalog 一档(静态模板仍优先,dashscope 原生模式地址没有 /embeddings);
2. 非 OpenAI 供应商解析不出地址时拒发,绝不把 key 送去 api.openai.com。
另:401 报错写出实际请求的主机,下次看一眼就知道是发错了地方还是 key 真坏了。
"""
import io
import urllib.error
from unittest import mock

import model_registry
from platform_app.knowledge import embedding

_DS_COMPAT = "https://dashscope.aliyuncs.com/compatible-mode/v1"


# ── 一、地址解析 ─────────────────────────────────────────────────────────────
def test_custom_provider_falls_back_to_live_catalog():
    with mock.patch.object(model_registry, "base_url_for", return_value="https://relay.example/v1"):
        assert embedding._catalog_embed_base_url("my_qwen") == "https://relay.example/v1"


def test_static_template_still_wins_for_dashscope():
    """live catalog 可能把 dashscope 切到原生 /api/v1(没有 /embeddings),嵌入必须仍走 compatible-mode。"""
    with mock.patch.object(model_registry, "base_url_for",
                           return_value="https://dashscope.aliyuncs.com/api/v1"):
        assert embedding._catalog_embed_base_url("dashscope") == _DS_COMPAT


def test_illegal_api_id_does_not_raise():
    """生产库里真有大写 id(如 'Jove'),normalize_api_id 会抛 —— 这里只能当查不到。"""
    with mock.patch.object(model_registry, "base_url_for", return_value=""):
        assert embedding._catalog_embed_base_url("Jove") == ""


def test_resolve_config_uses_live_catalog_when_cred_has_no_base_url(monkeypatch):
    import core.llm_backend as llm_backend
    import platform_app.user_credentials as uc
    monkeypatch.delenv("EMBED_BASE_URL", raising=False)
    monkeypatch.setattr(llm_backend, "resolve_preferred_api", lambda *a, **k: "my_qwen")
    monkeypatch.setattr(llm_backend, "resolve_preferred_model", lambda *a, **k: "text-embedding-v3")
    monkeypatch.setattr(uc, "resolve_api_key",
                        lambda *a, **k: {"key": "sk-qwen", "base_url_override": ""})
    monkeypatch.setattr(model_registry, "base_url_for", lambda _a: "https://relay.example/v1")
    api_id, model, key, base = embedding._resolve_embed_config(7)
    assert (api_id, key, base) == ("my_qwen", "sk-qwen", "https://relay.example/v1")


def test_credential_override_still_first(monkeypatch):
    import core.llm_backend as llm_backend
    import platform_app.user_credentials as uc
    monkeypatch.setattr(llm_backend, "resolve_preferred_api", lambda *a, **k: "dashscope")
    monkeypatch.setattr(llm_backend, "resolve_preferred_model", lambda *a, **k: "text-embedding-v3")
    monkeypatch.setattr(uc, "resolve_api_key",
                        lambda *a, **k: {"key": "sk-q", "base_url_override": "https://mine.example/v1"})
    assert embedding._resolve_embed_config(7)[3] == "https://mine.example/v1"


def test_force_path_uses_same_resolver(monkeypatch):
    """召回侧 embed_query(force_api_id=…) 是第二条取地址的路,不能再各写各的。"""
    import platform_app.user_credentials as uc
    monkeypatch.setattr(uc, "resolve_api_key", lambda *a, **k: {"key": "sk-q", "base_url_override": ""})
    monkeypatch.setattr(model_registry, "base_url_for", lambda _a: "https://relay.example/v1")
    seen = {}

    def _fake_dispatch(api_id, model, key, texts, base_url="", **_k):
        seen["base_url"] = base_url
        return [[0.0] * 3]

    monkeypatch.setattr(embedding, "_embed_provider_dispatch", _fake_dispatch)
    embedding.embed_query("你好", user_id=7, force_api_id="my_qwen", force_model="text-embedding-v3")
    assert seen["base_url"] == "https://relay.example/v1"


# ── 二、拒发:非 OpenAI 的 key 绝不送去 api.openai.com ─────────────────────────
def test_non_openai_provider_with_empty_base_url_is_refused(monkeypatch):
    called = []
    monkeypatch.setattr(embedding, "_embed_via_openai", lambda *a, **k: called.append(1))
    out = embedding._embed_provider_dispatch("my_qwen", "text-embedding-v3", "sk-qwen", ["x"],
                                             base_url="", user_id=7)
    assert out is None
    assert not called, "空地址 + 非 OpenAI 供应商 → 不许发请求"
    # 提示按用户记(不再是进程级全局,别的用户看不到)
    msg = embedding._breaker.last_error_for(7)
    assert "my_qwen" in msg
    assert "接口地址" in msg
    assert embedding._breaker.last_error_for(8) == ""


def test_openai_itself_may_use_default_endpoint(monkeypatch):
    called = []
    monkeypatch.setattr(embedding, "_embed_via_openai",
                        lambda model, key, texts, base_url="": called.append(base_url) or [[0.0]])
    embedding._embed_provider_dispatch("openai", "text-embedding-3-small", "sk-o", ["x"], base_url="")
    assert called == [""]


# ── 三、报错写出请求主机 ───────────────────────────────────────────────────────
def test_401_message_names_the_host(monkeypatch):
    import core.outbound as outbound

    def _raise(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(b'{"error":{"message":"Incorrect API key provided."}}'))

    monkeypatch.setattr(outbound, "safe_urlopen", _raise)
    assert embedding._embed_provider_dispatch("dashscope", "text-embedding-v3", "sk-q", ["x"],
                                              base_url=_DS_COMPAT, user_id=7) is None
    msg = embedding._breaker.last_error_for(7)
    assert "401" in msg and "dashscope.aliyuncs.com" in msg
