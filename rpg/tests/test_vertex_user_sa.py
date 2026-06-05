"""tests.test_vertex_user_sa — 用户级 Vertex SA BYOK 端到端单元测试。

覆盖:
  1. 用户 BYOK SA → vertex backend 使用用户 credentials
  2. 无用户 SA → fallback 全局 vertex_sa.json
  3. 二者均无 → RuntimeError with 友好提示
  4. model_probe 用户模式 + 用户 SA → 允许探测
  5. model_probe 服务器模式 + 无用户 SA → reject
  6. embedding vertex client 用户 BYOK 优先
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ──────────────────────────────────────────────────────────────────────
#  Fixtures & helpers
# ──────────────────────────────────────────────────────────────────────

_FAKE_SA = {
    "type": "service_account",
    "project_id": "test-project-byok",
    "private_key_id": "key-id-123",
    "private_key": "<FAKE_PRIVATE_KEY_PEM>",
    "client_email": "byok-sa@test-project-byok.iam.gserviceaccount.com",
    "client_id": "123456789",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}

_GLOBAL_SA = {**_FAKE_SA, "project_id": "global-project", "client_email": "global-sa@global-project.iam.gserviceaccount.com"}


def _make_fake_cred(project_id="test-project-byok"):
    cred = MagicMock()
    cred.project_id = project_id
    return cred


# ──────────────────────────────────────────────────────────────────────
#  core.vertex_sa.load_sa_credentials
# ──────────────────────────────────────────────────────────────────────

class TestLoadSaCredentials:
    def test_user_byok_used_when_available(self):
        """用户 BYOK SA 命中 → 应使用用户级 credentials。"""
        fake_cred = _make_fake_cred()
        with (
            patch("platform_app.user_credentials.get_credential", return_value={"key": json.dumps(_FAKE_SA)}),
            patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=fake_cred),
        ):
            from core.vertex_sa import load_sa_credentials
            creds, project_id = load_sa_credentials(user_id=42)
        assert creds is fake_cred
        assert project_id == "test-project-byok"

    def test_fallback_to_global_when_no_user_sa(self, tmp_path):
        """无用户 SA → 应 fallback 到全局 vertex_sa.json。"""
        sa_file = tmp_path / "vertex_sa.json"
        sa_file.write_text(json.dumps(_GLOBAL_SA))
        fake_cred = _make_fake_cred("global-project")

        import core.vertex_sa as vtx_sa_mod
        with (
            patch("platform_app.user_credentials.get_credential", return_value=None),
            patch("google.oauth2.service_account.Credentials.from_service_account_info", return_value=fake_cred),
            patch.object(vtx_sa_mod, "_RPG_BASE", tmp_path),
            patch.dict("os.environ", {"GOOGLE_APPLICATION_CREDENTIALS": ""}, clear=False),
        ):
            creds, project_id = vtx_sa_mod.load_sa_credentials(user_id=42)

        assert project_id == "global-project"

    def test_returns_none_when_nothing_configured(self, tmp_path):
        """用户 SA 和全局 SA 都没有 → 应返回 (None, None)。"""
        import core.vertex_sa as vtx_sa_mod
        with (
            patch("platform_app.user_credentials.get_credential", return_value=None),
            patch.object(vtx_sa_mod, "_RPG_BASE", tmp_path),  # tmp_path 里没有 vertex_sa.json
            patch.dict("os.environ", {"GOOGLE_APPLICATION_CREDENTIALS": ""}, clear=False),
        ):
            creds, project_id = vtx_sa_mod.load_sa_credentials(user_id=None)

        assert creds is None
        assert project_id is None


# ──────────────────────────────────────────────────────────────────────
#  _VertexBackend
# ──────────────────────────────────────────────────────────────────────

class TestVertexBackend:
    def _make_backend(self, user_id, load_sa_return):
        fake_genai = MagicMock()
        with (
            patch("agents.gm.backends.vertex.load_sa_credentials", return_value=load_sa_return),
            patch.dict("sys.modules", {"google.genai": fake_genai, "google": MagicMock()}),
        ):
            # 动态重新导入以保证 patch 生效
            import importlib

            import agents.gm.backends.vertex as vtx_mod
            importlib.reload(vtx_mod)
            backend = vtx_mod._VertexBackend(model="gemini-3.5-flash", user_id=user_id)
        return backend

    def test_init_with_user_sa(self):
        """user_id 非 None + BYOK SA → 正常初始化。"""
        fake_cred = _make_fake_cred()
        fake_genai = MagicMock()
        with (
            patch("core.vertex_sa.load_sa_credentials", return_value=(fake_cred, "proj-byok")),
            patch.dict("sys.modules", {
                "google": MagicMock(), "google.genai": fake_genai,
                "google.oauth2": MagicMock(), "google.oauth2.service_account": MagicMock(),
            }),
        ):
            import importlib

            import agents.gm.backends.vertex as vtx_mod
            importlib.reload(vtx_mod)
            backend = vtx_mod._VertexBackend(model="gemini-3.5-flash", user_id=42)
        assert backend.user_id == 42
        assert backend.model_name == "gemini-3.5-flash"

    def test_init_raises_when_no_sa(self):
        """无任何 SA → 应抛 RuntimeError，包含友好提示。"""
        fake_genai = MagicMock()
        with (
            patch("core.vertex_sa.load_sa_credentials", return_value=(None, None)),
            patch.dict("sys.modules", {
                "google": MagicMock(), "google.genai": fake_genai,
                "google.oauth2": MagicMock(), "google.oauth2.service_account": MagicMock(),
            }),
        ):
            import importlib

            import agents.gm.backends.vertex as vtx_mod
            importlib.reload(vtx_mod)
            with pytest.raises(RuntimeError, match="Service Account"):
                vtx_mod._VertexBackend(model="gemini-3.5-flash", user_id=None)


# ──────────────────────────────────────────────────────────────────────
#  model_probe
# ──────────────────────────────────────────────────────────────────────

class TestModelProbe:
    def test_server_mode_vertex_no_user_sa_rejected(self):
        """服务器模式 + 无用户 SA → 应 reject probe_availability。"""
        with (
            patch("model_probe._require_user_credential", return_value=True),
            patch("core.vertex_sa.has_user_sa", return_value=False),
            patch("model_registry.load_model_catalog", return_value={"apis": [{"id": "vertex_ai", "kind": "vertex_ai", "enabled": True, "models": [{"real_name": "gemini-3.5-flash"}]}]}),
            patch("model_registry.find_api", return_value={"id": "vertex_ai", "kind": "vertex_ai", "enabled": True, "models": [{"real_name": "gemini-3.5-flash"}]}),
        ):
            import model_probe
            result = model_probe.probe_availability("vertex_ai", user_id=None)
        assert result["ok"] is False
        assert "Service Account" in result["error"] or "Agent Platform" in result["error"]

    def test_server_mode_vertex_with_user_sa_allowed(self):
        """服务器模式 + 用户有 BYOK SA → 应允许探测（会真实调 GameMaster）。"""
        fake_gm = MagicMock()
        fake_gm._backend.call.return_value = "1"
        with (
            patch("model_probe._require_user_credential", return_value=True),
            patch("core.vertex_sa.has_user_sa", return_value=True),
            patch("model_registry.load_model_catalog", return_value={"apis": []}),
            patch("model_registry.find_api", return_value={"id": "vertex_ai", "kind": "vertex_ai", "enabled": True, "models": [{"real_name": "gemini-3.5-flash"}]}),
            patch("agents.gm.GameMaster", return_value=fake_gm),
        ):
            import model_probe
            result = model_probe.probe_availability("vertex_ai", model_real_name="gemini-3.5-flash", user_id=99)
        assert result["ok"] is True

    def test_list_vertex_models_server_mode_no_sa_rejected(self):
        """服务器模式 + 无 SA → list_remote_models 应 reject。"""
        with (
            patch("model_probe._require_user_credential", return_value=True),
            patch("core.vertex_sa.has_user_sa", return_value=False),
            patch("model_probe._has_user_credential", return_value=False),
            patch("model_registry.load_model_catalog", return_value={"apis": [{"id": "vertex_ai", "kind": "vertex_ai", "enabled": True, "models": []}]}),
            patch("model_registry.find_api", return_value={"id": "vertex_ai", "kind": "vertex_ai", "enabled": True, "models": []}),
        ):
            import model_probe
            result = model_probe.list_remote_models("vertex_ai", user_id=None)
        assert result["ok"] is False


# ──────────────────────────────────────────────────────────────────────
#  embedding
# ──────────────────────────────────────────────────────────────────────

class TestEmbeddingVertexByok:
    def test_get_vertex_client_uses_user_sa(self):
        """_get_vertex_client(user_id=42) 应用用户 SA 初始化 client。"""
        fake_cred = _make_fake_cred()
        fake_client = MagicMock()
        fake_genai = MagicMock()
        fake_genai.Client.return_value = fake_client

        with (
            patch("core.vertex_sa.load_sa_credentials", return_value=(fake_cred, "proj-byok")),
            patch.dict("sys.modules", {"google": MagicMock(), "google.genai": fake_genai}),
        ):
            import importlib

            import platform_app.knowledge.embedding as emb_mod
            # 清空 cache 让函数重新初始化
            emb_mod._VERTEX_CLIENT_CACHE.clear()
            importlib.reload(emb_mod)
            emb_mod._VERTEX_CLIENT_CACHE.clear()
            client = emb_mod._get_vertex_client(user_id=42)

        # client 不为 None（表示 BYOK 路径成功）
        assert client is not None

    def test_get_vertex_client_returns_none_when_no_sa(self):
        """无 SA 时 _get_vertex_client 应返回 None，不抛异常。"""
        with patch("core.vertex_sa.load_sa_credentials", return_value=(None, None)):
            import importlib

            import platform_app.knowledge.embedding as emb_mod
            emb_mod._VERTEX_CLIENT_CACHE.clear()
            importlib.reload(emb_mod)
            emb_mod._VERTEX_CLIENT_CACHE.clear()
            client = emb_mod._get_vertex_client(user_id=None)

        assert client is None
