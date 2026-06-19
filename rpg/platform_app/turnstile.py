"""platform_app.turnstile — Cloudflare Turnstile 人机验证（注册防机器人）。

配置门控（**fail-safe**：未配置 secret 即整体关闭，不改变现有行为）:
  RPG_TURNSTILE_SECRET   — 后端 secret key。设置后注册接口强制校验 token。
  RPG_TURNSTILE_SITEKEY  — 前端 site key。经 /api/auth/schema 透出，前端据此渲染挂件。

契约（两个 env 应**同时**设置，由同一次部署动作提供）:
  - secret 未设      → enabled()=False  → 后端跳过（=当前行为，零改动）。
  - sitekey 未设     → schema 不透出   → 前端不渲染挂件。
  - 两者都设         → 前端渲染挂件并随注册请求带上 token；后端强制校验。
  - secret 设了但请求缺 token / 校验失败 → 运行时拒绝（fail-closed）。

校验目标是固定可信端点 challenges.cloudflare.com，无 SSRF 面，直连即可。
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def secret() -> str:
    return (os.environ.get("RPG_TURNSTILE_SECRET") or "").strip()


def sitekey() -> str:
    return (os.environ.get("RPG_TURNSTILE_SITEKEY") or "").strip()


def enabled() -> bool:
    """后端是否强制校验。仅当配置了 secret 时为 True。"""
    return bool(secret())


def verify(token: str, *, ip: str | None = None, timeout: float = 8.0) -> bool:
    """向 Cloudflare 校验 Turnstile token。

    secret 未配置 → 直接放行（关闭态）。已配置时:
      - token 为空        → False
      - 网络/解析异常     → False（fail-closed：宁可拒绝也不放过机器人）
      - Cloudflare 返回 success=true → True
    """
    s = secret()
    if not s:
        return True
    token = (token or "").strip()
    if not token:
        return False
    data = {"secret": s, "response": token}
    if ip:
        data["remoteip"] = ip
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(_VERIFY_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (固定可信端点)
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception:
        return False
    return bool(payload.get("success"))
