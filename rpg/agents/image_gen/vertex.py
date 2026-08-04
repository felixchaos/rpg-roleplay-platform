"""agents.image_gen.vertex — Vertex AI (Gemini image) 图片适配器。

复用平台 Service Account（core.vertex_sa.load_sa_credentials）认证，无需用户 BYOK
api_key 字符串。模型如 gemini-3.1-flash-image / gemini-3-pro-image 通过
generate_content 的 IMAGE response modality 返回内联图片字节。
"""
from __future__ import annotations

from typing import Any

from agents.image_gen.base import ImageGenError


def generate(
    prompt: str,
    params: dict[str, Any],
    *,
    api_id: str,
    model: str,
    api_key: str = "",
    base_url: str | None = None,
    user_id: int | None = None,
) -> list[bytes]:
    """用 Vertex SA 调 Gemini image 模型生图，返回图片字节列表。

    api_key 被忽略（Vertex 走 SA）；user_id 用于 load_sa_credentials（生产鉴权模式取
    用户 BYOK SA，本地匿名模式取全局 SA）。
    """
    try:
        from google import genai
        from google.genai import types

        from core.vertex_sa import load_sa_credentials
    except Exception as exc:  # pragma: no cover - import env issue
        raise ImageGenError(f"vertex genai import failed: {exc}") from exc

    credentials, project_id = load_sa_credentials(user_id)
    if credentials is None or project_id is None:
        raise ImageGenError(
            "vertex SA unavailable — 该用户无可用 Service Account"
            "（生产模式需在 设置 → API & 模型 上传 SA）"
        )

    client = genai.Client(
        vertexai=True,
        project=project_id,
        location="global",
        credentials=credentials,
    )

    try:
        resp = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
    except Exception as exc:
        raise ImageGenError(f"vertex generate_content failed: {exc}") from exc

    images: list[bytes] = []
    for cand in (getattr(resp, "candidates", None) or []):
        content = getattr(cand, "content", None)
        for part in (getattr(content, "parts", None) or []):
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline else None
            if data:
                images.append(bytes(data))

    if not images:
        # 无图片部分：可能被安全过滤或模型只返回了文本，带上文本帮助诊断
        txt = ""
        try:
            txt = getattr(resp, "text", None) or ""
        except Exception:
            txt = ""
        raise ImageGenError(f"vertex returned no image part (text={txt[:200]!r})")

    return images
