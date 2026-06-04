"""test_embedding_byok.py — BYOK embedding 路由测试。

覆盖:
  · 无 user_id → 走系统默认 vertex 路径
  · user_id + embed.api_id='openai' → 走 OpenAI 路径,model 名正确
  · user_id + embed.api_id='vertex' → 仍走 vertex
  · user_id + embed.api_id='cohere' → 走 cohere 路径
  · 未知 api_id → 降级 vertex + warn 日志
  · resolve_api_key 无 key 时 openai 降级 vertex
  · embed_query BYOK: 返回正确向量字符串格式
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")
os.environ.setdefault("EMBED_MODEL", "text-embedding-004")
os.environ.setdefault("EMBED_API_ID", "vertex")


class TestEmbedProviderDispatch(unittest.TestCase):
    """_embed_provider_dispatch 直接单元测试(不依赖 DB / Vertex SDK)。"""

    def _import(self):
        # 每次重新 import 确保 module cache 不污染
        import importlib

        import platform_app.knowledge.embedding as mod
        importlib.reload(mod)
        return mod

    def test_vertex_path_calls_vertex(self):
        mod = self._import()
        fake_vecs = [[0.1] * 768]
        with patch.object(mod, "_embed_via_vertex", return_value=fake_vecs) as m:
            result = mod._embed_provider_dispatch("vertex", "text-embedding-004", "", ["hello"])
        m.assert_called_once_with("text-embedding-004", ["hello"], task_type="RETRIEVAL_DOCUMENT")
        self.assertEqual(result, fake_vecs)

    def test_google_api_id_also_routes_vertex(self):
        mod = self._import()
        fake_vecs = [[0.2] * 768]
        with patch.object(mod, "_embed_via_vertex", return_value=fake_vecs) as m:
            result = mod._embed_provider_dispatch("google", "text-embedding-004", "", ["hello"])
        m.assert_called_once()
        self.assertEqual(result, fake_vecs)

    def test_openai_path_calls_openai(self):
        mod = self._import()
        fake_vecs = [[0.3] * 1536]
        with patch.object(mod, "_embed_via_openai", return_value=fake_vecs) as m:
            result = mod._embed_provider_dispatch(
                "openai", "text-embedding-3-small", "sk-test", ["hello world"]
            )
        m.assert_called_once_with(
            "text-embedding-3-small", "sk-test", ["hello world"], base_url=""
        )
        self.assertEqual(result, fake_vecs)

    def test_openai_compat_path(self):
        mod = self._import()
        fake_vecs = [[0.4] * 1536]
        with patch.object(mod, "_embed_via_openai", return_value=fake_vecs) as m:
            result = mod._embed_provider_dispatch(
                "openai_compat", "my-model", "key123", ["text"], base_url="https://custom.api/v1"
            )
        m.assert_called_once_with("my-model", "key123", ["text"], base_url="https://custom.api/v1")
        self.assertEqual(result, fake_vecs)

    def test_openai_no_key_falls_back_vertex(self):
        mod = self._import()
        fake_vecs = [[0.5] * 768]
        with patch.object(mod, "_embed_via_vertex", return_value=fake_vecs) as mv, \
             patch.object(mod, "_embed_via_openai") as mo:
            result = mod._embed_provider_dispatch("openai", "text-embedding-3-small", "", ["hi"])
        mo.assert_not_called()
        mv.assert_called_once()
        self.assertEqual(result, fake_vecs)

    def test_cohere_path_calls_cohere(self):
        mod = self._import()
        fake_vecs = [[0.6] * 1024]
        with patch.object(mod, "_embed_via_cohere", return_value=fake_vecs) as m:
            result = mod._embed_provider_dispatch("cohere", "embed-multilingual-v3.0", "co-key", ["text"])
        m.assert_called_once_with("embed-multilingual-v3.0", "co-key", ["text"])
        self.assertEqual(result, fake_vecs)

    def test_unknown_api_id_falls_back_vertex_and_warns(self):
        mod = self._import()
        fake_vecs = [[0.7] * 768]
        with patch.object(mod, "_embed_via_vertex", return_value=fake_vecs) as mv, \
             self.assertLogs("platform_app.knowledge.embedding", level="WARNING") as cm:
            result = mod._embed_provider_dispatch("magic_provider", "some-model", "key", ["hi"])
        mv.assert_called_once()
        self.assertTrue(any("unknown api_id" in line for line in cm.output))
        self.assertEqual(result, fake_vecs)


class TestResolveEmbedConfig(unittest.TestCase):
    """_resolve_embed_config 从 user_preferences 读取并合并 BYOK key。"""

    def _import(self):
        import importlib

        import platform_app.knowledge.embedding as mod
        importlib.reload(mod)
        return mod

    def test_no_user_id_returns_defaults(self):
        mod = self._import()
        api_id, model, api_key, base_url = mod._resolve_embed_config(None)
        self.assertEqual(api_id, mod.DEFAULT_EMBED_API_ID)
        self.assertEqual(model, mod.DEFAULT_EMBED_MODEL)

    def test_user_id_reads_prefs_and_key(self):
        mod = self._import()
        with patch("core.llm_backend.resolve_preferred_api", return_value="openai") as rpa, \
             patch("core.llm_backend.resolve_preferred_model", return_value="text-embedding-3-small") as rpm, \
             patch(
                 "platform_app.user_credentials.resolve_api_key",
                 return_value={"key": "sk-byok-test", "source": "user_db", "base_url_override": ""},
             ) as rk:
            api_id, model, api_key, base_url = mod._resolve_embed_config(42)
        self.assertEqual(api_id, "openai")
        self.assertEqual(model, "text-embedding-3-small")
        self.assertEqual(api_key, "sk-byok-test")
        rpa.assert_called_once_with(42, "embed.api_id")
        rpm.assert_called_once_with(42, "embed.model_real_name")
        rk.assert_called_once()

    def test_user_id_pref_none_falls_back_env(self):
        mod = self._import()
        with patch("core.llm_backend.resolve_preferred_api", return_value=None), \
             patch("core.llm_backend.resolve_preferred_model", return_value=None), \
             patch(
                 "platform_app.user_credentials.resolve_api_key",
                 return_value={"key": "", "source": "none", "base_url_override": ""},
             ):
            api_id, model, api_key, base_url = mod._resolve_embed_config(99)
        self.assertEqual(api_id, mod.DEFAULT_EMBED_API_ID)
        self.assertEqual(model, mod.DEFAULT_EMBED_MODEL)


class TestEmbedQueryBYOK(unittest.TestCase):
    """embed_query 全链路测试(mock 到 provider dispatch 层)。"""

    def _import(self):
        import importlib

        import platform_app.knowledge.embedding as mod
        importlib.reload(mod)
        return mod

    def test_embed_query_byok_openai_returns_vec_string(self):
        """user_id=42, embed.api_id=openai → dispatch 走 openai,返回 pgvector 格式字符串。"""
        mod = self._import()
        fake_vec = [0.1, 0.2, 0.3]
        with patch("core.llm_backend.resolve_preferred_api", return_value="openai"), \
             patch("core.llm_backend.resolve_preferred_model", return_value="text-embedding-3-small"), \
             patch(
                 "platform_app.user_credentials.resolve_api_key",
                 return_value={"key": "sk-byok", "source": "user_db", "base_url_override": ""},
             ), \
             patch.object(mod, "_embed_via_openai", return_value=[fake_vec]) as mo:
            result = mod.embed_query("测试文本", user_id=42)
        mo.assert_called_once_with("text-embedding-3-small", "sk-byok", ["测试文本"], base_url="")
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("["))
        self.assertTrue(result.endswith("]"))
        # 向量值精确到 6 位小数
        self.assertIn("0.100000", result)

    def test_embed_query_no_user_id_uses_vertex(self):
        """无 user_id → 走 vertex 路径。"""
        mod = self._import()
        fake_vec = [0.5] * 768
        with patch.object(mod, "_embed_via_vertex", return_value=[fake_vec]) as mv:
            result = mod.embed_query("hello", user_id=None)
        mv.assert_called_once()
        self.assertIsNotNone(result)

    def test_embed_query_empty_text_returns_none(self):
        mod = self._import()
        result = mod.embed_query("   ", user_id=None)
        self.assertIsNone(result)

    def test_embed_query_provider_returns_none_returns_none(self):
        mod = self._import()
        with patch.object(mod, "_embed_provider_dispatch", return_value=None):
            result = mod.embed_query("hi", user_id=None)
        self.assertIsNone(result)


class TestEmbedBatch(unittest.TestCase):
    """_embed_batch 传 user_id 走 BYOK。"""

    def _import(self):
        import importlib

        import platform_app.knowledge.embedding as mod
        importlib.reload(mod)
        return mod

    def test_embed_batch_with_user_id_routes_byok(self):
        mod = self._import()
        fake_vecs = [[0.1, 0.2]]
        with patch("core.llm_backend.resolve_preferred_api", return_value="openai"), \
             patch("core.llm_backend.resolve_preferred_model", return_value="text-embedding-3-small"), \
             patch(
                 "platform_app.user_credentials.resolve_api_key",
                 return_value={"key": "sk-batch", "source": "user_db", "base_url_override": ""},
             ), \
             patch.object(mod, "_embed_via_openai", return_value=fake_vecs) as mo:
            result = mod._embed_batch(["text1"], user_id=7)
        mo.assert_called_once()
        self.assertEqual(result, fake_vecs)

    def test_embed_batch_empty_returns_empty(self):
        mod = self._import()
        result = mod._embed_batch([], user_id=None)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
