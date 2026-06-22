"""agents.image_gen.openai_compat — 通用 OpenAI 兼容生图适配器。

覆盖所有「OpenAI 兼容」provider 的生图:OpenAI 官方(dall-e-3 / gpt-image-1)、
各类自建中转站、SiliconFlow(硅基)、以及 OpenRouter 的图像模型。在此适配器出现前,
dispatch 只支持 doubao/dashscope/vertex,其它一律 `unsupported image provider`
—— 而绝大多数 BYOK 用户(尤其 CN)配的就是 OpenAI 兼容中转站 → 选 dall-e / gpt-image /
gemini-*-image / flux 这类(被名字 heuristic 标成可生图)全部失败。

两条路(按 provider 实际支持自动选择):
  1. **标准 Images API**:POST {base}/images/generations(OpenAI / 多数中转站 / 硅基)。
  2. **Chat 图像模态**:POST {base}/chat/completions + modalities=["image","text"]
     (OpenRouter 的 google/gemini-*-image、openai/gpt-*-image 走这条;无 /images 端点)。
  先试 (1),遇 404/405(无此端点)再退 (2)。

复用浏览器 UA(core.outbound_ua):中转站多挂 Cloudflare,默认 SDK/urllib UA 会被 WAF 拦。
"""
from __future__ import annotations

from typing import Any

import httpx

from agents.image_gen.base import ImageGenError, decode_b64, download_url
from core.outbound import safe_httpx_client

_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 180.0  # 生图比聊天慢,给足时间

# 这些 provider 的图像 API 是 chat/completions 图像模态,**没有** /images/generations 端点
# (OpenRouter 的 google/gemini-*-image、openai/gpt-*-image 等)。直接走 chat 模态,免得先
# 打一发不存在的 /images/generations 拿到误导性的 401/404。
_CHAT_MODALITY_IMAGE_PROVIDERS = {"openrouter"}


def _raise_http(resp, api_id: str, label: str) -> None:
    """非 200 → 抛 ImageGenError。401/403 给「Key 无效」的可行动文案(而非裸 HTTP 401)。"""
    code = resp.status_code
    if code in (401, 403):
        hint = "(OpenRouter 的 key 形如 sk-or-v1-…)" if api_id == "openrouter" else ""
        raise ImageGenError(
            f"{api_id} 鉴权失败(HTTP {code}): provider 拒绝了你的 API Key。请到「设置 → API 凭证」"
            f"检查 {api_id} 的 Key 是否正确、有效{hint}"
        )
    try:
        detail = resp.json()
    except Exception:
        detail = resp.text[:300]
    raise ImageGenError(f"openai_compat: {label} HTTP {code}: {detail}")


def _resolve_base_url(api_id: str, base_url: str | None) -> str:
    """凭证里的 base_url_override 优先;内置 provider(openai/openrouter/guiji…)回退 catalog。"""
    if base_url:
        return base_url.rstrip("/")
    try:
        from model_registry import find_api, load_model_catalog, normalize_api_id
        api = find_api(load_model_catalog(), normalize_api_id(api_id)) or {}
        cb = (api.get("base_url") or "").rstrip("/")
        if cb:
            return cb
    except Exception:
        pass
    raise ImageGenError(
        f"openai_compat: 找不到 {api_id} 的 base_url(请在「设置 → API 凭证」填写中转站 Base URL)"
    )


def _headers(api_key: str) -> dict[str, str]:
    from core.outbound_ua import outbound_user_agent
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # 中转站常挂 CF,WAF 按 UA 拦默认 SDK/urllib 签名 → 用浏览器 UA 穿透(详见 core.outbound_ua)。
        "User-Agent": outbound_user_agent(),
    }


def _bytes_from_data_item(item: dict[str, Any]) -> bytes | None:
    """从 /images/generations 的 data[] 项取字节:b64_json 优先,其次 url。"""
    b64 = item.get("b64_json")
    if b64:
        return decode_b64(_strip_data_uri(b64))
    url = item.get("url")
    if url:
        if url.startswith("data:"):
            return decode_b64(_strip_data_uri(url))
        return download_url(url)
    return None


def _strip_data_uri(s: str) -> str:
    """`data:image/png;base64,XXXX` → `XXXX`;无前缀原样返回。"""
    if s.startswith("data:") and "," in s:
        return s.split(",", 1)[1]
    return s


def _try_images_api(
    base: str, headers: dict[str, str], prompt: str, model: str, params: dict, api_id: str
) -> list[bytes]:
    """标准 OpenAI Images API。返回 [] 表示「应回退到 chat 模态」(端点不存在)。"""
    endpoint = f"{base}/images/generations"
    body: dict[str, Any] = {"model": model, "prompt": prompt}
    # 透传受支持的可选字段;response_format 不主动传(gpt-image-1 不接受该参数),
    # 让 provider 用默认值,响应里 b64/url 都兼容解析。
    for field in ("size", "n", "seed", "quality", "style", "response_format"):
        if field in params and params[field] not in (None, ""):
            body[field] = params[field]

    try:
        with safe_httpx_client(timeout=_READ_TIMEOUT) as client:
            resp = client.post(
                endpoint, json=body, headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise ImageGenError(f"openai_compat: images/generations 超时 ({exc})") from exc
    except Exception as exc:
        raise ImageGenError(f"openai_compat: 网络错误 ({exc})") from exc

    # 端点不存在 → 交给调用方回退 chat 模态(OpenRouter 等)
    if resp.status_code in (404, 405):
        return []
    if resp.status_code != 200:
        _raise_http(resp, api_id, "images/generations")

    try:
        payload = resp.json()
    except Exception as exc:
        raise ImageGenError(f"openai_compat: 响应非 JSON: {exc}") from exc

    data = payload.get("data")
    if not data or not isinstance(data, list):
        raise ImageGenError(f"openai_compat: 响应结构异常: {str(payload)[:300]}")

    out: list[bytes] = []
    for item in data:
        b = _bytes_from_data_item(item) if isinstance(item, dict) else None
        if b:
            out.append(b)
    if not out:
        raise ImageGenError("openai_compat: images/generations 返回空(无 url/b64)")
    return out


def _collect_chat_images(message: dict[str, Any]) -> list[bytes]:
    """从 chat 响应 message 里抽图。兼容 OpenRouter `message.images[].image_url.url`
    与 content 数组里的 image_url 项(data: URI 或 http)。"""
    out: list[bytes] = []
    for img in (message.get("images") or []):
        url = ((img or {}).get("image_url") or {}).get("url") or (img or {}).get("url")
        if not url:
            continue
        out.append(decode_b64(_strip_data_uri(url)) if url.startswith("data:") else download_url(url))
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("image_url", "image"):
                url = ((part.get("image_url") or {}).get("url")) or part.get("url") or ""
                if url:
                    out.append(decode_b64(_strip_data_uri(url)) if url.startswith("data:") else download_url(url))
    return out


def _try_chat_modality(
    base: str, headers: dict[str, str], prompt: str, model: str, api_id: str
) -> list[bytes]:
    """OpenRouter 等:chat/completions + modalities=["image","text"],图在 message 里。"""
    endpoint = f"{base}/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
    }
    try:
        with safe_httpx_client(timeout=_READ_TIMEOUT) as client:
            resp = client.post(
                endpoint, json=body, headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise ImageGenError(f"openai_compat: chat 图像模态超时 ({exc})") from exc
    except Exception as exc:
        raise ImageGenError(f"openai_compat: 网络错误 ({exc})") from exc

    if resp.status_code != 200:
        _raise_http(resp, api_id, "chat 图像模态")

    try:
        payload = resp.json()
    except Exception as exc:
        raise ImageGenError(f"openai_compat: 响应非 JSON: {exc}") from exc

    choices = payload.get("choices") or []
    if not choices:
        raise ImageGenError(f"openai_compat: chat 响应无 choices: {str(payload)[:300]}")
    out = _collect_chat_images((choices[0] or {}).get("message") or {})
    if not out:
        raise ImageGenError(
            "openai_compat: 该模型未返回图像 —— 可能不是生图模型,或该 provider 用非标准接口。"
        )
    return out


def generate(
    prompt: str,
    params: dict,
    *,
    api_id: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
) -> list[bytes]:
    """通用 OpenAI 兼容生图:先标准 Images API,无端点则退 chat 图像模态。"""
    if not api_key:
        raise ImageGenError(f"openai_compat: {api_id} 缺少 API Key")
    base = _resolve_base_url(api_id, base_url)
    headers = _headers(api_key)

    # OpenRouter 等只有 chat 图像模态(无 /images/generations)→ 直接走,免打误导性 401。
    if api_id in _CHAT_MODALITY_IMAGE_PROVIDERS:
        return _try_chat_modality(base, headers, prompt, model, api_id)

    images = _try_images_api(base, headers, prompt, model, params or {}, api_id)
    if images:
        return images
    # /images/generations 不存在(404/405)→ 回退 chat 图像模态
    return _try_chat_modality(base, headers, prompt, model, api_id)
