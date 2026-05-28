"""agents.gm — GameMaster 子包 (按 LLM backend 拆分)."""
from agents.gm.backends.anthropic import _AnthropicBackend
from agents.gm.backends.openai_compat import _OpenAICompatBackend
from agents.gm.backends.vertex import _VertexBackend
from agents.gm.master import _WORLD, GameMaster  # noqa: F401 — 测试通过 agents.gm._WORLD 访问

__all__ = ["GameMaster", "_VertexBackend", "_AnthropicBackend", "_OpenAICompatBackend", "_WORLD"]
