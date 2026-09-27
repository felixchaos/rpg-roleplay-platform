"""平台 EMBED_* 配置不得漏进用户凭据路径(巡检 2026-09-28 F9,#104 同族的另一半)。

v1.88.1 修了「凭据没地址 → 解析成空串 → 发去 api.openai.com」,但用户分支的地址优先级仍是
`凭据 override → 平台 EMBED_BASE_URL → catalog`。部署一旦设了 EMBED_BASE_URL(部署文档推荐
的值就是 Gemini 的 OpenAI 兼容端点),普通用户配的 siliconflow / dashscope / openai 嵌入 key
全被发去了 Google;召回侧的 force 路径又不读 env,同一个 (用户, 供应商) 两条路算出两个主机。
其原有测试专门 delenv("EMBED_BASE_URL"),正好绕开了按文档部署的配置 —— 这里全部在设了它的前提下断言。

同批:免 Key 的本地嵌入模型(Ollama / LM Studio)被 `if cred.get("key")` 当成没配;
解析中途抛异常时普通用户会落到平台兜底、拿到平台 key;剧本锁定的供应商玩家没有凭据时,
召回会降级到 vertex 产出另一个向量空间的查询向量。
"""
from __future__ import annotations

import pytest

import core.llm_backend as llm_backend
import platform_app.user_credentials as uc
from platform_app.knowledge import embedding

_GEMINI_COMPAT = "https://generativelanguage.googleapis.com/v1beta/openai/"


@pytest.fixture
def prod_like_env(monkeypatch):
    """按部署文档(deploy/README.md、deploy/k8s/configmap.yaml)的样子设平台 env。"""
    monkeypatch.setenv("EMBED_API_ID", "openai")
    monkeypatch.setenv("EMBED_MODEL", "text-embedding-004")
    monkeypatch.setenv("EMBED_BASE_URL", _GEMINI_COMPAT)
    monkeypatch.setenv("EMBED_API_KEY", "PLATFORM-GEMINI-KEY")
    monkeypatch.setattr(embedding, "DEFAULT_EMBED_API_ID", "openai")
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: False)


def _user_picks(monkeypatch, api_id, model, cred):
    monkeypatch.setattr(llm_backend, "resolve_preferred_api", lambda *a, **k: api_id)
    monkeypatch.setattr(llm_backend, "resolve_preferred_model", lambda *a, **k: model)
    monkeypatch.setattr(uc, "resolve_api_key", lambda *a, **k: dict(cred))


def _force_path_endpoint(monkeypatch, api_id, model):
    seen = {}

    def _fake_dispatch(a, m, key, texts, base_url="", **_k):
        seen.update(api_id=a, key=key, base_url=base_url)
        return [[0.0] * 3]

    monkeypatch.setattr(embedding, "_embed_provider_dispatch", _fake_dispatch)
    embedding.embed_query("召回", user_id=7, force_api_id=api_id, force_model=model)
    return seen


@pytest.mark.parametrize("api_id,model", [
    ("siliconflow", "BAAI/bge-m3"),
    ("dashscope", "text-embedding-v3"),
    ("openai", "text-embedding-3-small"),
])
def test_user_key_never_goes_to_platform_base_url(prod_like_env, monkeypatch, api_id, model):
    _user_picks(monkeypatch, api_id, model,
                {"key": f"sk-user-{api_id}", "source": "user_db", "base_url_override": ""})
    got_api, got_model, key, base = embedding._resolve_embed_config(7)
    assert key == f"sk-user-{api_id}"
    assert "generativelanguage.googleapis.com" not in base, "用户的 key 不许发给平台 EMBED_BASE_URL"
    assert base == embedding._catalog_embed_base_url(api_id) != ""
    # 建库与召回同一个解析器:逐字节同一个 (key, 地址)
    seen = _force_path_endpoint(monkeypatch, api_id, model)
    assert (seen["key"], seen["base_url"]) == (key, base)


def test_credential_override_still_wins(prod_like_env, monkeypatch):
    _user_picks(monkeypatch, "openai", "text-embedding-3-small",
                {"key": "sk-u", "source": "user_db", "base_url_override": "https://mine.example/v1"})
    assert embedding._resolve_embed_config(7)[3] == "https://mine.example/v1"
    assert _force_path_endpoint(monkeypatch, "openai", "text-embedding-3-small")["base_url"] \
        == "https://mine.example/v1"


def test_admin_without_own_cred_still_uses_platform_env(prod_like_env, monkeypatch):
    """EMBED_BASE_URL 只属于平台 key:admin/vip 兜底仍按它走(Gemini 兼容端点 → 原生 embedContent)。"""
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: True)
    _user_picks(monkeypatch, None, None, {"key": "", "source": "none", "base_url_override": ""})
    api_id, model, key, base = embedding._resolve_embed_config(7)
    assert (api_id, key) == ("gemini", "PLATFORM-GEMINI-KEY")


def test_admin_platform_relay_base_url_kept(monkeypatch):
    monkeypatch.setenv("EMBED_BASE_URL", "https://relay.example/v1")
    monkeypatch.setenv("EMBED_API_KEY", "PLATFORM-KEY")
    monkeypatch.setattr(embedding, "DEFAULT_EMBED_API_ID", "openai")
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: True)
    _user_picks(monkeypatch, None, None, {"key": "", "source": "none", "base_url_override": ""})
    assert embedding._resolve_embed_config(7)[2:] == ("PLATFORM-KEY", "https://relay.example/v1")


# ── 免 Key 本地嵌入模型 ──────────────────────────────────────────────────────
_NO_AUTH = {"key": "", "source": "user_db_no_auth", "base_url_override": "http://127.0.0.1:11434/v1"}


def test_no_auth_local_embedder_is_usable(prod_like_env, monkeypatch):
    _user_picks(monkeypatch, "ollama_local", "nomic-embed-text", _NO_AUTH)
    api_id, model, key, base = embedding._resolve_embed_config(7)
    assert (api_id, model) == ("ollama_local", "nomic-embed-text")
    assert key == uc.NO_AUTH_PLACEHOLDER, "免 Key 用占位 token,不是空串(空串会被当成没配)"
    assert base == "http://127.0.0.1:11434/v1"


def test_no_auth_local_embedder_actually_sends_to_local(prod_like_env, monkeypatch):
    _user_picks(monkeypatch, "ollama_local", "nomic-embed-text", _NO_AUTH)
    sent = []
    monkeypatch.setattr(embedding, "_embed_via_openai",
                        lambda m, key, texts, base_url="": sent.append((m, key, base_url)) or [[0.0] * 3])
    monkeypatch.setattr(embedding, "_embed_via_vertex", lambda *a, **k: pytest.fail("不许静默改走 vertex"))
    assert embedding._embed_batch(["一段原文"], user_id=7) is not None
    assert sent == [("nomic-embed-text", uc.NO_AUTH_PLACEHOLDER, "http://127.0.0.1:11434/v1")]
    assert embedding.embedding_preflight(7)["ok"] is True


def test_no_auth_force_path_uses_same_resolver(prod_like_env, monkeypatch):
    _user_picks(monkeypatch, "ollama_local", "nomic-embed-text", _NO_AUTH)
    seen = _force_path_endpoint(monkeypatch, "ollama_local", "nomic-embed-text")
    assert (seen["key"], seen["base_url"]) == (uc.NO_AUTH_PLACEHOLDER, "http://127.0.0.1:11434/v1")


# ── BYOK 闸 ─────────────────────────────────────────────────────────────────
def test_resolver_exception_does_not_hand_platform_key_to_regular_user(prod_like_env, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(llm_backend, "resolve_preferred_api", _boom)
    _api, _model, key, base = embedding._resolve_embed_config(7)
    assert key == "" and base == "", "解析出错时普通用户也拿不到平台 key"


def test_resolver_exception_admin_keeps_platform_fallback(prod_like_env, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(llm_backend, "resolve_preferred_api", _boom)
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: True)
    assert embedding._resolve_embed_config(7)[2] == "PLATFORM-GEMINI-KEY"


def test_force_path_without_cred_does_not_fall_back_to_vertex(prod_like_env, monkeypatch):
    """剧本锁定 siliconflow,玩家没这家的 key:不发请求,别降级 vertex 产出异空间向量。"""
    monkeypatch.setattr(uc, "resolve_api_key",
                        lambda *a, **k: {"key": "", "source": "none", "base_url_override": ""})
    monkeypatch.setattr(embedding, "_embed_via_vertex", lambda *a, **k: pytest.fail("不许降级 vertex"))
    monkeypatch.setattr(embedding, "_embed_via_openai", lambda *a, **k: pytest.fail("没有 key 不许发"))
    assert embedding.embed_query("召回", user_id=7, force_api_id="siliconflow",
                                 force_model="BAAI/bge-m3") is None


def test_force_path_vertex_locked_still_dispatches(monkeypatch):
    """锁定的本来就是 vertex(用 SA 不用 key):照常分发。"""
    monkeypatch.setattr(uc, "resolve_api_key",
                        lambda *a, **k: {"key": "", "source": "none", "base_url_override": ""})
    called = []
    monkeypatch.setattr(embedding, "_embed_via_vertex",
                        lambda *a, **k: called.append(1) or [[0.0] * 3])
    assert embedding.embed_query("召回", user_id=7, force_api_id="vertex_ai",
                                 force_model="text-embedding-004") is not None
    assert called == [1]


# ── 写库不混向量空间 ─────────────────────────────────────────────────────────
def _admin_with_own_failing_key(monkeypatch):
    monkeypatch.setenv("EMBED_API_KEY", "PLATFORM-KEY")
    monkeypatch.setenv("EMBED_BASE_URL", "https://relay.example/v1")
    monkeypatch.setattr(embedding, "DEFAULT_EMBED_API_ID", "openai")
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: True)
    monkeypatch.setattr(embedding, "_resolve_embed_config",
                        lambda _uid: ("siliconflow", "BAAI/bge-m3", "sk-own", "https://api.siliconflow.cn/v1"))
    sent: list[str] = []
    monkeypatch.setattr(embedding, "_embed_via_openai",
                        lambda model, key, texts, base_url="": sent.append(key))  # 恒失败
    return sent


def test_write_path_never_mixes_in_platform_vectors(monkeypatch):
    """admin/vip 自己的 key 失败时,写进已绑定剧本的向量不改用平台模型:
    剧本按用户自己的 (api_id, model) 绑定,混进平台模型的向量 = 同一剧本两个向量空间。"""
    sent = _admin_with_own_failing_key(monkeypatch)
    assert embedding._embed_batch(["x"], user_id=7, allow_platform_fallback=False) is None
    assert sent == ["sk-own"]


def test_default_path_keeps_admin_platform_fallback(monkeypatch):
    sent = _admin_with_own_failing_key(monkeypatch)
    embedding._embed_batch(["x"], user_id=7)
    assert sent == ["sk-own", "PLATFORM-KEY"]


def test_embed_query_can_opt_out_of_platform_fallback(monkeypatch):
    """kb_events(永恒记忆)没有绑定 embedder 的元数据:写入和召回都关掉平台兜底,
    admin/vip 自己的 key 失败时不拿平台模型的向量去写 / 去比。默认路径的兜底不变。"""
    from core import request_cache

    sent = _admin_with_own_failing_key(monkeypatch)
    request_cache.reset_request_caches()
    try:
        assert embedding.embed_query("往事", user_id=7, allow_platform_fallback=False) is None
        assert sent == ["sk-own"]
        # 同一请求、同一文本:开关不同是两条缓存,默认路径照常切平台兜底
        embedding.embed_query("往事", user_id=7)
        assert sent == ["sk-own", "sk-own", "PLATFORM-KEY"]
    finally:
        request_cache._embed_vec_cache.set(None)


def test_episodic_embeds_without_platform_fallback(monkeypatch):
    """kb.episodic 的补嵌入(写)与向量召回(读)都传 allow_platform_fallback=False。"""
    from contextlib import contextmanager

    import platform_app.db as dbmod
    from kb import episodic

    seen: list[bool] = []

    def _fake_embed_query(text, user_id=None, *a, **kw):
        seen.append(kw.get("allow_platform_fallback", True))
        return None

    class _DB:
        def execute(self, sql, params=None):
            if "embedding_vec is null" in sql:
                return _R([{"id": 1, "summary": "在站台遇见楚轩"}])
            if "embedding_vec is not null limit 1" in sql:
                return _R1({"x": 1})
            return _R([])

    @contextmanager
    def _connect():
        yield _DB()

    monkeypatch.setattr(dbmod, "connect", _connect)
    monkeypatch.setattr(dbmod, "init_db", lambda: None)
    monkeypatch.setattr(embedding, "embed_query", _fake_embed_query)
    assert episodic.embed_pending_events(11, 7) == 0
    assert episodic._retrieve_vector(11, 3, 7, "楚轩") == []
    assert seen == [False, False]


class _R1:
    def __init__(self, one):
        self._one = one

    def fetchone(self):
        return self._one


class _R:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return None


def test_writers_opt_out_of_platform_fallback(monkeypatch):
    """原文片段 / 人物卡 / 世界书 / canon 四处写库都不切平台兜底(行为级:数实际传参)。"""
    from contextlib import contextmanager

    import platform_app.db as dbmod
    from extract import embed as canon_embed
    from platform_app.knowledge.embedding import _writer

    served = {"chunks": False}

    class _DB:
        def execute(self, sql, params=None):
            rows = []
            if "from document_chunks" in sql and "embedding_vec is null" in sql and not served["chunks"]:
                served["chunks"] = True
                rows = [{"id": 1, "content": "原文"}]
            elif "from character_cards" in sql and "embedding_vec is null" in sql:
                rows = [{"id": 2, "name": "甲", "identity": "", "personality": "", "appearance": ""}]
            elif "from worldbook_entries" in sql and "embedding_vec is null" in sql:
                rows = [{"id": 3, "title": "乙", "content": ""}]
            elif "from kb_canon_entities" in sql:
                rows = [{"id": 4, "name": "丙", "summary": "", "aliases": []}]
            return _R(rows)

    @contextmanager
    def _connect():
        yield _DB()

    seen: list[bool] = []

    def _fake_batch(texts, user_id=None, **kw):
        seen.append(kw.get("allow_platform_fallback", True))
        return [[0.0] * 3 for _ in texts]

    monkeypatch.setattr(dbmod, "connect", _connect)
    monkeypatch.setattr(_writer, "_resolve_embed_config",
                        lambda _uid: ("siliconflow", "BAAI/bge-m3", "sk", "https://api.siliconflow.cn/v1"))
    monkeypatch.setattr(_writer, "_embed_batch", _fake_batch)
    _writer._embed_chunks_loop_inner(script_id=5, user_id=7)
    monkeypatch.setattr(embedding, "_embed_batch", _fake_batch)
    canon_embed.embed_canon_entities(_DB(), 5, user_id=7)
    assert seen == [False, False, False, False]


# ── 默认模型也不套平台 EMBED_MODEL ─────────────────────────────────────────────
def test_no_pref_openai_chat_key_uses_catalog_embedding_model(prod_like_env, monkeypatch):
    """没设 RAG 偏好、只配了 OpenAI 聊天 key:模型取 OpenAI 目录里的嵌入模型,
    不是平台 EMBED_MODEL(text-embedding-004 发给 OpenAI 必然 404)。"""
    _user_picks(monkeypatch, None, None, {"key": "sk-openai-chat", "source": "user_db", "base_url_override": ""})
    assert embedding._resolve_embed_config(7) == (
        "openai", "text-embedding-3-small", "sk-openai-chat", embedding._catalog_embed_base_url("openai"))


def test_explicit_model_pref_still_wins(prod_like_env, monkeypatch):
    _user_picks(monkeypatch, "openai", "text-embedding-3-large",
                {"key": "sk-u", "source": "user_db", "base_url_override": ""})
    assert embedding._resolve_embed_config(7)[1] == "text-embedding-3-large"


# ── 召回 force 路径与建库解析器对 admin/vip 对称 ────────────────────────────────
def _no_own_cred(monkeypatch):
    monkeypatch.setattr(uc, "resolve_api_key",
                        lambda *a, **k: {"key": "", "source": "none", "base_url_override": ""})


def test_force_path_admin_platform_built_script_uses_platform_config(prod_like_env, monkeypatch):
    """admin/vip 没自配时建库用的是平台配置(剧本按它绑定);召回也得用它,否则这批剧本向量召回恒失败。"""
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: True)
    _no_own_cred(monkeypatch)
    plat_api, plat_model, plat_key, plat_base = embedding._platform_fallback_config()
    seen = _force_path_endpoint(monkeypatch, plat_api, plat_model)
    assert (seen["api_id"], seen["key"], seen["base_url"]) == (plat_api, plat_key, plat_base)


def test_force_path_regular_user_never_gets_platform_config(prod_like_env, monkeypatch):
    _no_own_cred(monkeypatch)
    plat_api, plat_model, _k, _b = embedding._platform_fallback_config()
    monkeypatch.setattr(embedding, "_embed_provider_dispatch", lambda *a, **k: pytest.fail("普通用户不许用平台 key"))
    assert embedding.embed_query("召回", user_id=7, force_api_id=plat_api, force_model=plat_model) is None


def test_force_path_admin_other_space_not_substituted(prod_like_env, monkeypatch):
    """锁定的 (api_id, model) 与平台配置不同:不拿平台 key 顶替(那是另一个向量空间)。"""
    monkeypatch.setattr(embedding, "_is_admin", lambda _uid: True)
    _no_own_cred(monkeypatch)
    monkeypatch.setattr(embedding, "_embed_provider_dispatch", lambda *a, **k: pytest.fail("不许跨空间顶替"))
    assert embedding.embed_query("召回", user_id=7, force_api_id="siliconflow",
                                 force_model="BAAI/bge-m3") is None
