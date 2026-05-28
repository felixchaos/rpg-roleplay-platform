"""agents.gm — GameMaster 子包 (按 LLM backend 拆分)."""
from agents.gm.master import GameMaster
from agents.gm.backends.vertex import _VertexBackend
from agents.gm.backends.anthropic import _AnthropicBackend
from agents.gm.backends.openai_compat import _OpenAICompatBackend

__all__ = ["GameMaster", "_VertexBackend", "_AnthropicBackend", "_OpenAICompatBackend"]
