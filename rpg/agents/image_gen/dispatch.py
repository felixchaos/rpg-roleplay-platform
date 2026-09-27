"""agents.image_gen.dispatch — route image generation to the right provider adapter.

Public function:
    generate_image_bytes(
        *,
        api_id: str,
        model: str,
        prompt: str,
        params: dict,
        api_key: str,
        base_url: str | None = None,
    ) -> list[bytes]

Routing is based on the normalized api_id (via model_aliases.normalize_api_id).
Supported providers:
    doubao      →  agents.image_gen.doubao
    dashscope   →  agents.image_gen.dashscope
    vertex_ai   →  agents.image_gen.vertex
    anthropic   →  显式拒绝(无生图能力)
    其余一切     →  agents.image_gen.openai_compat(OpenAI 官方 / 中转站 / OpenRouter / 硅基 …)

只有 anthropic 会因「不支持」被拒;其余 OpenAI 兼容 provider 统一走 openai_compat 适配器。
"""
from __future__ import annotations

from agents.image_gen.base import ImageGenError


def generate_image_bytes(
    *,
    api_id: str,
    model: str,
    prompt: str,
    params: dict,
    api_key: str,
    base_url: str | None = None,
    user_id: int | None = None,
    proxy: str | None = None,
) -> list[bytes]:
    """Route image generation to the correct provider adapter.

    Args:
        api_id:    Provider id string (normalized or raw; this function normalizes it).
        model:     Model id string from catalog.
        prompt:    Text prompt for image generation.
        params:    Provider-specific optional parameters dict.
        api_key:   API key for the provider.
        base_url:  Optional base URL override (used by doubao for custom ARK endpoints).
        proxy:     凭据代理(core.outbound.credential_proxy 的结果,本地模式才有值)。
                   vertex 走 Service Account + genai SDK,不经这里的代理。

    Returns:
        list[bytes] — one element per generated image.

    Raises:
        ImageGenError on provider error, network failure, or unsupported provider.
    """
    from model_aliases import normalize_api_id  # lazy import — avoids circular deps

    normalized = normalize_api_id(api_id)

    if normalized == "doubao":
        from agents.image_gen import doubao
        return doubao.generate(
            prompt, params,
            api_id=normalized, model=model, api_key=api_key, base_url=base_url, proxy=proxy,
        )

    if normalized == "dashscope":
        from agents.image_gen import dashscope
        return dashscope.generate(
            prompt, params,
            api_id=normalized, model=model, api_key=api_key, base_url=base_url, proxy=proxy,
        )

    if normalized == "vertex_ai":
        from agents.image_gen import vertex
        return vertex.generate(
            prompt, params,
            api_id=normalized, model=model, api_key=api_key, base_url=base_url,
            user_id=user_id,
        )

    if normalized == "anthropic":
        # Anthropic 无生图能力,显式拒绝(比打到 OpenAI 兼容端点报 404 更清楚)。
        raise ImageGenError("anthropic 不支持图像生成,请改用 OpenAI 兼容 / 豆包 / 通义万相 / Vertex 的生图模型")

    # 其余一律按 **OpenAI 兼容** 处理:OpenAI 官方 / 各类中转站 / OpenRouter / 硅基(guiji)/
    # deepseek 等。这是平台的统一假设(非 vertex/anthropic 的 provider 都走 openai_compat),
    # 也是绝大多数 BYOK 用户(中转站)能生图的唯一通路。具体端点(images/generations vs
    # chat 图像模态)由适配器按 provider 实际支持自动选择。
    from agents.image_gen import openai_compat
    return openai_compat.generate(
        prompt, params,
        api_id=normalized, model=model, api_key=api_key, base_url=base_url, proxy=proxy,
    )
