"""agents.image_gen.base — shared adapter interface + exception.

Each provider adapter must expose:

    def generate(
        prompt: str,
        params: dict,
        *,
        api_id: str,
        model: str,
        api_key: str,
        base_url: str | None = None,
    ) -> list[bytes]

Returns a list of raw image bytes (one element per generated image).
If the provider returns URLs, the adapter downloads them and returns bytes.
If the provider returns base64, the adapter decodes and returns bytes.

Raises ImageGenError on any provider-level or network error.
"""
from __future__ import annotations

import base64


class ImageGenError(Exception):
    """Raised by any image-gen adapter on provider error, network failure,
    or unsupported configuration.  The message includes the provider's raw
    error details where available.
    """


def download_url(url: str, *, timeout: float = 60.0, proxy: str | None = None) -> bytes:
    """Fetch image bytes from a URL.  Raises ImageGenError on failure.

    SEC: 这个 URL 来自 provider 响应(data[].url / message.images),而 provider 的 base_url
    是用户/admin 可控的中转站 —— 攻击者可让假 provider 返回指向 169.254.169.254 / 127.0.0.1 的
    URL,把本函数变成二阶 SSRF(读云元数据/内网,且抓回的字节会落盘后经 /api/images/file 取出
    = 非盲外带)。故统一走 core.outbound.safe_get_bytes:不跟随重定向到内网 + 每跳重解析校验 +
    pin 已校验 IP(抗 DNS rebinding)+ 体积上限。data: URI 由各 adapter 自行 decode,不进这里。

    proxy:该生图任务凭据的代理(adapter 把 generate() 收到的 proxy 原样传下来)。提交走了代理、
    下载不走的话,需要代理的图片域名会下载超时,整单失败(而提交那一步已经计费)。
    """
    from core.outbound import OutboundBlocked, safe_get_bytes
    try:
        return safe_get_bytes(url, timeout=timeout, proxy=proxy)
    except OutboundBlocked as exc:
        raise ImageGenError(f"image url blocked (SSRF guard): {exc}") from exc
    except Exception as exc:
        raise ImageGenError(f"image download error: {exc}") from exc


def decode_b64(b64_str: str) -> bytes:
    """Decode a base64 image string.  Raises ImageGenError on bad input."""
    try:
        return base64.b64decode(b64_str)
    except Exception as exc:
        raise ImageGenError(f"base64 decode error: {exc}") from exc
