"""agents.image_gen.doubao — Doubao / Ark image-generation adapter.

API: OpenAI-compatible images/generations
Endpoint: POST {base_url}/images/generations
Default base_url: https://ark.cn-beijing.volces.com/api/v3
Auth: Authorization: Bearer {api_key}

Request body fields (JSON):
    model          str   required  e.g. "doubao-seedream-4-x-t2i" (from catalog)
    prompt         str   required  text prompt
    size           str   optional  e.g. "1024x1024" / "1:1" / "16:9"
    n              int   optional  number of images (default 1)
    seed           int   optional  for reproducibility
    watermark      bool  optional  whether provider adds watermark (default false)
    response_format str  optional  "url" (default) or "b64_json"

Response shape:
    {
      "data": [
        {"url": "https://...", "b64_json": null},
        ...
      ]
    }

Provider returns URL by default; we download to bytes.
If response_format="b64_json" the URL is null and b64_json is populated.
"""
from __future__ import annotations

from typing import Any

import httpx

from agents.image_gen.base import ImageGenError, decode_b64, download_url

_DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 120.0

# seedream 4.x 强制最小 3,686,400 像素(1920×1920);传更小尺寸或不传(走 provider 默认)
# 都会被 Ark 退回 400 InvalidParameter(反馈 #65)。在适配器层(唯一出站口)钳到合法档,
# 所有调用方(直连 API / 角色卡自动生图 / LLM 工具)都受保护。
_SEEDREAM_MIN_PIXELS = 1920 * 1920  # 3_686_400
_SEEDREAM_SIZES: tuple[tuple[int, int], ...] = (
    (1920, 1920), (2048, 1152), (1152, 2048), (2048, 2048), (2560, 1440), (1440, 2560),
)


def _is_seedream(model: str) -> bool:
    return "seedream" in (model or "").lower()


def _coerce_seedream_size(size_str: str | None) -> str:
    """把请求尺寸钳到 seedream 合法档(>=3,686,400 像素)。

    - 缺省 / 比例串("1:1") / 无法解析 → 安全默认 "2048x2048"。
    - 已达像素下限 → 原样保留。
    - 不足 → 选纵横比最接近且满足下限的合法档(保留构图朝向)。
    """
    import re
    if not size_str:
        return "2048x2048"
    m = re.match(r"^\s*(\d+)\s*[xX*]\s*(\d+)\s*$", str(size_str))
    if not m:
        return "2048x2048"
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        return "2048x2048"
    if w * h >= _SEEDREAM_MIN_PIXELS:
        return f"{w}x{h}"
    ratio = w / h
    best = min(_SEEDREAM_SIZES, key=lambda s: (abs(s[0] / s[1] - ratio), s[0] * s[1]))
    return f"{best[0]}x{best[1]}"


def generate(
    prompt: str,
    params: dict,
    *,
    api_id: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
) -> list[bytes]:
    """Call Ark images/generations and return image bytes.

    Args:
        prompt:    Text prompt for image generation.
        params:    Optional provider parameters (size, n, seed, watermark,
                   response_format).  Keys match the Ark API field names.
        api_id:    Canonical provider id (e.g. "doubao") — informational only.
        model:     Model id string (e.g. "doubao-seedream-4-x-t2i").
        api_key:   Ark API key (Bearer token).
        base_url:  Override Ark endpoint base.  Defaults to
                   https://ark.cn-beijing.volces.com/api/v3
    Returns:
        list[bytes] — one element per generated image.
    Raises:
        ImageGenError on any provider or network failure.
    """
    base = (base_url or _DEFAULT_BASE).rstrip("/")
    endpoint = f"{base}/images/generations"

    # 反馈 #65:seedream 4.x 要求 >=3,686,400 像素。出站前统一钳尺寸(含未传 size 时给安全默认),
    # 否则过小尺寸被 Ark 退回 400。只对 seedream 生效,不影响其它 doubao 模型。
    if _is_seedream(model):
        params = dict(params or {})
        params["size"] = _coerce_seedream_size(params.get("size"))

    body: dict[str, Any] = {"model": model, "prompt": prompt}
    # Forward supported optional fields from params
    for field in ("size", "n", "seed", "watermark", "response_format"):
        if field in params:
            body[field] = params[field]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = httpx.post(
            endpoint,
            json=body,
            headers=headers,
            timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
            follow_redirects=False,
        )
    except httpx.TimeoutException as exc:
        raise ImageGenError(f"doubao: request timed out ({exc})") from exc
    except Exception as exc:
        raise ImageGenError(f"doubao: network error ({exc})") from exc

    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:500]
        raise ImageGenError(
            f"doubao: HTTP {resp.status_code} from {endpoint}: {detail}"
        )

    try:
        payload = resp.json()
    except Exception as exc:
        raise ImageGenError(f"doubao: invalid JSON response: {exc}") from exc

    data = payload.get("data")
    if not data or not isinstance(data, list):
        raise ImageGenError(f"doubao: unexpected response shape: {payload}")

    result: list[bytes] = []
    for item in data:
        b64 = item.get("b64_json")
        url = item.get("url")
        if b64:
            result.append(decode_b64(b64))
        elif url:
            result.append(download_url(url))
        else:
            raise ImageGenError(f"doubao: data item has neither url nor b64_json: {item}")

    if not result:
        raise ImageGenError("doubao: response data list is empty")

    return result
