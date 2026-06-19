"""
user_credentials.py — 用户级 API key CRUD + 解密读取

调用入口：
- set_credential(user_id, api_id, plaintext_key, base_url_override="")
- get_credential(user_id, api_id) → 明文 key 或空串
- list_credentials(user_id) → 不返回 key 本身，只返回存在与否、最近更新时间
- delete_credential(user_id, api_id)
- resolve_api_key(user_id, api_id, env_fallback) → 解密 → 环境变量回退（仅 admin/本地）

设计原则：
- DB 里永远是密文
- 解密只在调用 LLM 时即时做，结果不缓存
- list 接口永远不返回 raw key，只给 has_credential 布尔标记
"""
from __future__ import annotations

import os
import re
from typing import Any

from psycopg.types.json import Jsonb

from utils.crypto import decrypt_api_key, encrypt_api_key

from .db import connect, expose, init_db
from model_aliases import normalize_api_id, _API_ID_ALIASES  # noqa: F401 — re-export for compat

_PRIVATE_HOST_PREFIXES = (
    "127.", "10.", "192.168.", "169.254.",
    "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "0.", "localhost", "::1", "fc", "fd", "fe80",
)


def _credential_aliases(api_id: str) -> list[str]:
    canonical = normalize_api_id(api_id)
    aliases = [canonical]
    for alias, target in _API_ID_ALIASES.items():
        if target == canonical and alias not in aliases:
            aliases.append(alias)
    return aliases


def _ip_is_internal(ip_str: str) -> bool:
    """判断单个 IP 是否私有/本地/保留(含 IPv4-mapped IPv6)。"""
    import ipaddress
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法解析为 IP 视为不安全
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) → 取出内嵌 IPv4 再判
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def _validate_base_url(url: str) -> None:
    """禁止把 base_url 指向私网/本机/保留地址，避免 SSRF。

    安全关键:**解析 hostname → 校验真实 IP**,而非字符串前缀黑名单。
    这样十进制(2130706433)/八进制(0177.0.0.1)/十六进制(0x7f000001)/
    IPv4-mapped IPv6([::ffff:169.254.169.254]) 这些绕过形式都会在 getaddrinfo
    归一化后被 _ip_is_internal 统一拦截。DNS rebinding 在请求时(_connector_auth)
    会再校一次缓解。
    """
    import socket
    from urllib.parse import urlparse
    try:
        p = urlparse(url)
    except Exception as exc:
        raise ValueError("base_url 必须是合法 URL") from exc
    if p.scheme not in {"https", "http"}:
        raise ValueError("base_url 必须是 http/https")
    from core.config import require_auth as _require_auth
    # 本地/自部署单用户模式:用户自己的机器 + 自己的 key,SSRF「自我保护」无意义。而这里的
    # 解析级 IP 拦截会**误杀两类合法本地用法**:① 指向本机大模型(Ollama/LM Studio 127.0.0.1)
    # ② 开着梯子(Clash fake-ip 把公网 API 域名解析成 198.18.x.x 这类保留段)。真请求其实经代理/
    # 本机能通,却被预校验当内网拒了(用户反馈:开代理→「api 使用了保留地址」连接失败)。
    # SSRF 真防线在请求时的 safe_* 出站层 + 托管模式 byok_only 守卫;解析级拦截只是服务器自保,
    # 故仅在服务器模式(require_auth)生效;本地模式只校验 scheme。
    if not _require_auth():
        return
    if p.scheme == "http":
        raise ValueError("服务器模式下 base_url 必须是 https")
    host = (p.hostname or "").lower()
    if not host:
        raise ValueError("base_url 缺少 host")
    # 字面量本地名快速拦截
    if host in {"localhost", "ip6-localhost", "ip6-loopback"} or host.endswith(".localhost"):
        raise ValueError(f"base_url 不允许指向本地地址：{host}")
    # 真正的防线:解析出所有 A/AAAA,任一为内网/保留即拒(覆盖各种进制 IP 伪装)。
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ValueError(f"base_url 主机无法解析：{host}") from exc
    for info in infos:
        ip_str = info[4][0]
        if _ip_is_internal(ip_str):
            raise ValueError(f"base_url 解析到私有/本地/保留地址，已拒绝：{host} → {ip_str}")


def _normalize_openai_base_url(url: str) -> str:
    """规整 OpenAI 兼容 base_url:剥掉用户常误填的完整端点尾巴 `/chat/completions`。

    中转站文档普遍把「接口地址」写成完整 `https://host/v1/chat/completions`,用户整段填进
    base_url → SDK 再拼 `/chat/completions`、`/models` → `.../chat/completions/chat/completions`
    与 `.../chat/completions/models` 双双 404 →「不可访问 / 0 模型」。这里只剥这一个公认尾巴
    (大小写无关),不动 `/v1`、`/v1beta/openai` 等合法 base 路径。写时+读时都过一遍,自愈历史误填。
    """
    s = (url or "").strip().rstrip("/")
    if s.lower().endswith("/chat/completions"):
        s = s[: -len("/chat/completions")].rstrip("/")
    return s


def set_credential(user_id: int, api_id: str, plaintext_key: str, base_url_override: str = "", enabled: bool = True, *, allow_base_url: bool = False, proxy: str = "") -> dict[str, Any]:
    """加密保存。空 key 等价于删除该 credential。

    安全：base_url_override 是 SSRF 风险源。allow_base_url 默认 False，
    意味着普通用户无法用自己的 key 让服务器访问任意 URL（如 127.0.0.1）。
    本地匿名模式 / admin 设置时调用方传 allow_base_url=True 才能写入。

    proxy: 该 provider 出站走的 HTTP/SOCKS 代理 URL(存进 metadata)。**注意**:代理合法地
    常是 127.0.0.1(本地梯子),不能用 _validate_base_url 拦私网。SSRF 由「只在本地模式
    (非 require_auth)才真正使用」兜底(见 openai_compat.py)——托管多用户后端永不使用用户
    proxy,故存了也无害。这里只做轻量格式校验。
    """
    init_db()
    api_id = normalize_api_id(api_id)
    if not api_id:
        raise ValueError("api_id 不能为空")
    if not plaintext_key:
        return delete_credential(user_id, api_id)
    # P1 #7：之前非 admin 传 base_url_override 直接静默 = ""，UI 以为已设置。
    # 改成显式 raise ValueError，让 /api/me/credentials 回 400，前端能感知。
    if base_url_override and not allow_base_url:
        raise ValueError("base_url_override 仅管理员可设置 · 普通用户必须使用 catalog 中的 base_url")
    if not allow_base_url:
        base_url_override = ""
    elif base_url_override:
        base_url_override = _normalize_openai_base_url(base_url_override)
        _validate_base_url(base_url_override)
    proxy = (proxy or "").strip()
    if proxy:
        if not re.match(r"^(https?|socks5h?)://[^\s/]+", proxy, re.IGNORECASE):
            raise ValueError("代理地址格式不对 · 形如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080")
        # SEC: 托管多用户模式下,proxy 指向内网/本机 = SSRF 隐患(代理合法地可填 127.0.0.1,无法
        # 靠 _validate_base_url 拦)。这里在**写时**就拒掉内网代理,与消费侧 byok_only 守卫
        # (openai_compat.py:仅 require_auth=False 才用 proxy)构成双闸,杜绝「存量内网 proxy 随
        # 某次重构变实弹」。本地单用户模式(require_auth=False)才允许 127.0.0.1 这类本地梯子。
        try:
            from core.config import require_auth as _require_auth
            _hosted = bool(_require_auth())
        except Exception:
            _hosted = True
        if _hosted:
            import socket as _socket
            from urllib.parse import urlparse as _urlparse
            _phost = (_urlparse(proxy).hostname or "").lower()
            if (not _phost or _phost in {"localhost", "ip6-localhost", "ip6-loopback"}
                    or _phost.endswith(".localhost")):
                raise ValueError("服务器模式下代理不允许指向本地地址")
            try:
                _infos = _socket.getaddrinfo(_phost, None, proto=_socket.IPPROTO_TCP)
            except OSError as _exc:
                raise ValueError(f"代理主机无法解析:{_phost}") from _exc
            if any(_ip_is_internal(_i[4][0]) for _i in _infos):
                raise ValueError(f"服务器模式下代理不允许指向私有/本地/保留地址:{_phost}")
    meta = {"proxy": proxy} if proxy else {}
    encrypted = encrypt_api_key(plaintext_key, user_id, api_id)
    with connect() as db:
        row = db.execute(
            """
            insert into user_api_credentials(user_id, api_id, encrypted_key, base_url_override, enabled, metadata)
            values (%s, %s, %s, %s, %s, %s)
            on conflict(user_id, api_id) do update set
              encrypted_key = excluded.encrypted_key,
              base_url_override = excluded.base_url_override,
              enabled = excluded.enabled,
              metadata = excluded.metadata,
              updated_at = now()
            returning id, user_id, api_id, base_url_override, enabled, updated_at
            """,
            (user_id, api_id, encrypted, base_url_override or "", enabled, Jsonb(meta)),
        ).fetchone()
    result = {"ok": True, **(expose(row) or {}), "has_credential": True}

    # best-effort: 配 key 后自动拉该 provider 的真实模型列表并写入用户 overlay。
    # lazy import 防循环依赖（model_probe → model_registry → ? ← credentials）。
    # 失败只 log，绝不影响存 key 主流程。
    try:
        import logging as _logging
        from model_probe import invalidate_user_api, list_remote_models
        from platform_app.user_models import replace_synced_models
        # 先清旧 key 的远程模型缓存,再强制重拉:绝不能命中改 key 前「校验连接/拉取模型」
        # 写满的旧 key 60s 缓存,否则会把旧 key 的模型写进 overlay(issue #22 根因之一)。
        invalidate_user_api(user_id, api_id)
        sync_result = list_remote_models(api_id, user_id=user_id, force_refresh=True)
        if sync_result.get("ok") and sync_result.get("models"):
            replace_synced_models(user_id, api_id, sync_result["models"])
        else:
            # 换 key 后新 key 列不出模型(provider 不支持 /models 或调用失败)：必须清掉
            # 旧 key 同步来的 overlay，否则游戏控制台模型列表会一直残留旧 key 的模型，
            # 表现为「换 key 后模型列表不刷新」(OSS issue #22)。清空后该 provider 回退
            # 全局策展菜单(key 无关，始终可用)；用户可再手动「拉取远程模型」补齐。
            replace_synced_models(user_id, api_id, [])
    except Exception as _sync_exc:
        try:
            _logging.getLogger(__name__).warning(
                "set_credential auto-sync failed (non-fatal): %s", _sync_exc
            )
        except Exception:
            pass

    return result


def delete_credential(user_id: int, api_id: str) -> dict[str, Any]:
    init_db()
    canonical = normalize_api_id(api_id)
    with connect() as db:
        db.execute(
            "delete from user_api_credentials where user_id = %s and api_id = any(%s)",
            (user_id, _credential_aliases(canonical)),
        )
    # 删 key 后清掉该 provider 的 per-user 模型 overlay：否则旧 key「拉取远程模型」同步来的
    # 模型清单仍残留在游戏控制台模型列表里，删了 key 也不消失(OSS issue #22)。best-effort，
    # 清 overlay 失败不影响删 key 主流程。覆盖所有别名，防 normalize 后落到不同 api_id。
    try:
        from model_probe import invalidate_user_api
        from platform_app.user_models import replace_synced_models
        for _alias in {canonical, *_credential_aliases(canonical)}:
            if _alias:
                replace_synced_models(user_id, _alias, [])
                # 同步清远程模型缓存:否则删 key 后 60s 内「拉取远程模型」仍返已删 key 的清单。
                invalidate_user_api(user_id, _alias)
    except Exception:
        pass
    return {"ok": True, "deleted": True, "api_id": canonical}


def list_credentials(user_id: int) -> dict[str, Any]:
    """返回用户已配置的 API 凭证列表（不含 raw key）"""
    init_db()
    with connect() as db:
        rows = db.execute(
            """
            select user_id, api_id, base_url_override, enabled, created_at, updated_at,
                   metadata, length(encrypted_key) as cipher_len
            from user_api_credentials
            where user_id = %s
            order by api_id
            """,
            (user_id,),
        ).fetchall()
    items = []
    seen: set[str] = set()
    for r in rows:
        api_id = normalize_api_id(r["api_id"])
        if api_id in seen:
            continue
        seen.add(api_id)
        _meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        items.append({
            "api_id": api_id,
            "has_credential": int(r["cipher_len"] or 0) > 0,
            "base_url_override": r["base_url_override"] or "",
            "proxy_url": (_meta or {}).get("proxy") or "",
            "enabled": bool(r["enabled"]),
            "updated_at": str(r["updated_at"]),
        })
    return {"ok": True, "items": items, "total": len(items)}


def get_credential(user_id: int, api_id: str) -> dict[str, Any] | None:
    """返回包含明文 key 的 dict（调用方负责不写日志/不返回前端）。失败返回 None。"""
    init_db()
    canonical = normalize_api_id(api_id)
    with connect() as db:
        rows = db.execute(
            """
            select * from user_api_credentials
            where user_id = %s and api_id = any(%s)
            order by (api_id = %s) desc, updated_at desc
            """,
            (user_id, _credential_aliases(canonical), canonical),
        ).fetchall()
    for row in rows:
        if not row or not row.get("enabled"):
            continue
        stored_api_id = row.get("api_id") or canonical
        blob = row.get("encrypted_key")
        # 密钥派生(HKDF info=api:<id>)与 AAD(api=<id>)都绑定 api_id。历史上凭据可能以
        # 别名(如 'AgentPlatform')加密;migration v67 规范化重命名了 api_id 列却未重新
        # 加密 blob,导致用当前列值解密会失败(AAD/密钥不匹配)。依次尝试 [当前列值] +
        # [canonical 的全部别名],命中即恢复 —— 兼容任意历史 api_id 命名,无需重新加密迁移。
        plaintext = ""
        for _cand in [stored_api_id, *_credential_aliases(canonical)]:
            plaintext = decrypt_api_key(blob, user_id, _cand)
            if plaintext:
                break
        if not plaintext:
            continue
        _meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        return {
            "api_id": canonical,
            "key": plaintext,
            "base_url_override": _normalize_openai_base_url(row.get("base_url_override") or ""),
            "proxy": (_meta or {}).get("proxy") or "",
        }
    return None


def resolve_api_key(user_id: int | None, api_id: str, env_fallback: str = "") -> dict[str, Any]:
    """
    GM 调用入口：按用户隔离取 key。

    解析顺序：
    1. 当前 user 在 user_api_credentials 表里的 key（绝对隔离）
    2. 本地未登录 + 环境变量（仅 RPG_REQUIRE_AUTH != 1 时允许）

    返回 {"key": "...", "source": "user_db" | "env" | "none", "base_url_override": "..."}

    内部使用 request-scoped cache（core.request_cache.get_api_cred_cached），
    同一请求内相同 (user_id, api_id) 只查一次 DB；非请求上下文行为不变。
    """
    if user_id:
        try:
            from core.request_cache import get_api_cred_cached
            cred = get_api_cred_cached(int(user_id), api_id)
        except Exception:
            cred = get_credential(user_id, api_id)
        if cred and cred.get("key"):
            return {"key": cred["key"], "source": "user_db", "base_url_override": cred.get("base_url_override", ""), "proxy": cred.get("proxy", "")}

    # 仅未强制鉴权时允许环境变量回退
    from core.config import require_auth as _require_auth
    if _require_auth():
        return {"key": "", "source": "none", "base_url_override": ""}
    if env_fallback:
        env_key = os.environ.get(env_fallback)
        if env_key:
            return {"key": env_key, "source": "env", "base_url_override": ""}
    # 自部署「全局 key」约定:环境变量 RPG_KEY_<API_ID>(大写,非字母数字→_)。
    # 仅本地/自部署模式(上方 require_auth gate 已挡掉服务器模式)。让用户在控制台「配置」里
    # 填一次全局密钥即对所有调用生效(无需逐用户 BYOK)。用户库内凭据优先级仍高于此回退。
    conv = "RPG_KEY_" + "".join(ch if ch.isalnum() else "_" for ch in normalize_api_id(api_id)).upper()
    conv_key = os.environ.get(conv)
    if conv_key:
        return {"key": conv_key, "source": "env", "base_url_override": ""}
    return {"key": "", "source": "none", "base_url_override": ""}
