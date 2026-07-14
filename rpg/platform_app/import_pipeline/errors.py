"""import_pipeline.errors — 凭证缺失异常类

来源: 原 rpg/platform_app/import_pipeline.py MissingUserCredentialError / MissingEmbeddingCredentialError(原 L116-136) 区段,纯机械搬家(函数体逐字未动),零行为变化。
"""
from __future__ import annotations

from typing import Any


class MissingUserCredentialError(ValueError):
    """Raised when a paid/user-scoped LLM pipeline has no user credential."""

    def __init__(self, api_id: str, model: str, credential_api_id: str):
        self.api_id = api_id
        self.model = model
        self.credential_api_id = credential_api_id
        super().__init__("需要先配置自己的 API Key 后才能继续知识流水线")


class MissingEmbeddingCredentialError(ValueError):
    """Raised when an embedding rebuild cannot run with the user's credentials."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = dict(payload)
        self.api_id = str(payload.get("api_id") or "")
        self.model = str(payload.get("model") or "")
        self.credential_api_id = str(payload.get("credential_api_id") or self.api_id)
        super().__init__(
            str(payload.get("error") or payload.get("hint") or "需要先配置向量嵌入凭证")
        )
