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


# 「这条凭据算不算可用」的 SQL 谓词 —— **单一真相源**。
# 原本这个条件(length(encrypted_key) > 0)被抄在至少三处:llm_backend.first_user_model 的
# 两条查询、feedback 的环境快照。加免鉴权模式时任何一处漏改,都会让本地 provider 在那条
# 路径上"半可用"(能选不能跑 / 能跑不上报),属于本仓的「修 A 漏 B」惯犯地形。
# 免鉴权必须同时有 base_url_override:不指地址的无 key 凭据不是"本地模型",只是残缺行。
CREDENTIAL_USABLE_SQL = (
    "(length(encrypted_key) > 0 "
    " or (auth_mode = 'none' and coalesce(base_url_override, '') <> ''))"
)


# 免鉴权端点在 HTTP 层要带的占位 token。**不能用空串**:openai SDK(实测 2.41.1)对
# api_key="" 与 None 一视同仁,直接抛 OpenAIError("Missing credentials") —— 在构造 client
# 时就崩,连请求都发不出去。Ollama / llama.cpp / LM Studio / 未开 --api-key 的 vLLM
# 都不校验 Authorization 的值,收到占位串照常应答。
NO_AUTH_PLACEHOLDER = "no-key-required"


def resolved_is_usable(result: dict[str, Any] | None) -> bool:
    """resolve_api_key 的返回值是否代表「可以发请求」。

    有 key → 可用;没 key 但 source='user_db_no_auth'(用户显式声明免鉴权)→ 也可用。
    各 agent/probe 路径统一用它,别再各写 `if not cred.get("key")`——那样新加的免鉴权
    模式会在每条没改到的路径上表现为「配好了却说没配 key」。
    """
    if not result:
        return False
    return bool(result.get("key")) or result.get("source") == "user_db_no_auth"


def resolved_auth_token(result: dict[str, Any] | None) -> str:
    """要真正塞进 Authorization 头的串:有 key 用 key,免鉴权用占位 token。"""
    if not result:
        return ""
    key = result.get("key") or ""
    if key:
        return key
    return NO_AUTH_PLACEHOLDER if result.get("source") == "user_db_no_auth" else ""


def credential_is_usable(cred: dict[str, Any] | None) -> bool:
    """CREDENTIAL_USABLE_SQL 的 Python 侧孪生(给已取出的 cred dict 用)。

    与 SQL 版语义一致:有 key 即可用;无 key 但用户显式声明免鉴权 → 也可用。
    get_credential 返回的 base_url_override 已归一,空串表示没配。
    """
    if not cred:
        return False
    if cred.get("key"):
        return True
    return cred.get("auth_mode") == "none" and bool(cred.get("base_url_override"))


def _credential_aliases(api_id: str) -> list[str]:
    canonical = normalize_api_id(api_id)
    aliases = [canonical]
    for alias, target in _API_ID_ALIASES.items():
        if target == canonical and alias not in aliases:
            aliases.append(alias)
    return aliases


def _ip_is_internal(ip_str: str) -> bool:
    """判断单个 IP 是否私有/本地/保留(含 IPv4-mapped/6to4/NAT64 内嵌 IPv4)。

    双层判定:①显式钉死的封锁网段(版本无关)②解释器 is_private/is_reserved 标志。
    只用后者不够 —— CPython 3.10→3.14 间对 6to4/NAT64/Teredo/文档段的分类有过变化,
    OSS 自托管跑任意解释器版本时,攻击者域名解析到 2002:a00:1::(6to4 包 10.0.0.1)或
    64:ff9b::a00:1(NAT64 包 10.0.0.1)可能穿透某些版本的标志判定。显式钉死使判定
    在各版本上一致且不弱于任何版本的标志判定(只紧不松)。
    """
    import ipaddress
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法解析为 IP 视为不安全
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) → 取出内嵌 IPv4 再判
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # 6to4(2002::/16)/NAT64(64:ff9b::/96):外壳里藏内嵌 IPv4,拆出来按 IPv4 判(防私有 v4 藏进 v6)
    if isinstance(ip, ipaddress.IPv6Address):
        packed = ip.packed
        if packed[:2] == b"\x20\x02":              # 6to4:内嵌 v4 在 bytes 2..6
            if _ip_is_internal(str(ipaddress.IPv4Address(packed[2:6]))):
                return True
        if int(ip) >> 32 == 0x0064FF9B:            # NAT64 64:ff9b::/96:内嵌 v4 在末 4 字节
            if _ip_is_internal(str(ipaddress.IPv4Address(packed[12:16]))):
                return True
    _EXPLICIT_V4 = (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16",
        "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "240.0.0.0/4",
    )
    _EXPLICIT_V6 = ("::1/128", "fc00::/7", "fe80::/10", "2001::/32", "2001:db8::/32")
    nets = _EXPLICIT_V4 if isinstance(ip, ipaddress.IPv4Address) else _EXPLICIT_V6
    for cidr in nets:
        if ip in ipaddress.ip_network(cidr):
            return True
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
    # Google AI Studio 的 OpenAI 兼容端点在 `/v1beta/openai`。用户常只填到 `/v1beta`(原生 Gemini base)
    # → SDK 拼 `.../v1beta/chat/completions`(原生无此端点→404)与 `.../v1beta/models`(原生列模型端点
    # 拒 Bearer、要 ?key= → 401「provider 拒绝列模型」)。自愈:generativelanguage host 且以 /v1beta 结尾
    # (非 /v1beta/openai)→ 补 /openai。行者无疆(u115)误填 `.../v1beta`,谷歌并未改 base。
    _low = s.lower()
    if "generativelanguage.googleapis.com" in _low and _low.endswith("/v1beta"):
        s = s + "/openai"
    # ⚠️ 这里**刻意不对 volces.com(火山方舟)做任何路径纠正**。v1.74.0 曾加过一条
    # 「*.volces.com → /api/v3」的自愈,当时以为用户把 `/api/plan` 写错了 —— **那是错的**:
    # 火山方舟同一个域名下至少有两个不同产品的入口,
    #   · `/api/v3`   —— Ark OpenAI 兼容(doubao 系模型)
    #   · `/api/plan` —— Agent Plan(Anthropic Messages 原生格式,用 AUTH_TOKEN)
    # 反馈者原话「agent plan 给的是这个地址」,即 `/api/plan` 是火山控制台**真给的**合法端点。
    # 按 host 一刀切改写会把人家正确的 Agent Plan 配置悄悄改成打不开的 /api/v3。
    # 而 ark 网关先验鉴权再路由(任意路径都回 401),用户根本看不出地址被我们动过。
    # 教训:base_url 自愈只能针对**同一产品**公认的写法歧义(如 Google 少写 /openai),
    # 一个域名下有多产品时,猜哪个都是错的。
    return s


def set_credential(user_id: int, api_id: str, plaintext_key: str, base_url_override: str = "", enabled: bool = True, *, allow_base_url: bool = False, proxy: str = "", preserve_key_if_empty: bool = False, auth_mode: str = "api_key") -> dict[str, Any]:
    """加密保存。空 key 等价于删除该 credential（preserve_key_if_empty=True 时例外）。

    auth_mode（v1.81.0）:
      'api_key'(默认) —— 现有语义,一字未变:必须有非空 key,空 key = 删除凭据。
      'none'          —— 免鉴权端点(本地 Ollama / vLLM / llama.cpp / LM Studio)。
                         **key 可选**:不填就存一条无 key 凭据,填了照常加密保存并在请求里发送
                         (部分 vLLM/LM Studio 会校验一个任意 token)。因此空 key **不再**等价删除。
                         必须同时给 base_url_override —— 不指地址的「免 key」没有意义,
                         也防止把某个托管 provider 误设成免鉴权后静默走空 key 撞 401。

    preserve_key_if_empty=True 时，空 key 表示「只改 base_url_override / 启用态，保留
    已存密文 key 与 metadata(proxy)」—— 对应「编辑」弹窗只改接口地址、不重填 key 的
    场景（key 从不回显，逼用户为改 URL 重填 key 既反直觉又正是该 bug 的成因）。无已存
    凭证则报错。

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
    auth_mode = (auth_mode or "api_key").strip() or "api_key"
    if auth_mode not in ("api_key", "none"):
        raise ValueError("auth_mode 只能是 api_key 或 none")
    if auth_mode == "none" and not base_url_override:
        raise ValueError("免鉴权模式必须填接口地址(base_url)——不指地址的「免 Key」无意义")
    if not plaintext_key and not preserve_key_if_empty and auth_mode != "none":
        # 空 key 常态 = 删除凭证（base_url 无关，短路在校验之前，保持 delete 路径零变化）。
        # auth_mode='none' 例外:无 key 正是它的常态,要落库而不是删。
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
    if not plaintext_key and auth_mode != "none":
        # preserve_key_if_empty：只改 base_url_override / 启用态，保留密钥与 metadata(proxy)。
        return _update_credential_meta(user_id, api_id, base_url_override, enabled, auth_mode)
    # auth_mode='none' 且没填 key → 继续往下走正常 upsert,只是密文为空(见 encrypted 计算)。
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
    # 免鉴权且用户没填 key → 存空密文(列是 bytea not null default ''),length(encrypted_key)=0
    # 与「没有 key」在 SQL 侧口径一致。填了 key 就照常加密,免鉴权模式下也会带上。
    encrypted = encrypt_api_key(plaintext_key, user_id, api_id) if plaintext_key else b""
    with connect() as db:
        row = db.execute(
            """
            insert into user_api_credentials(user_id, api_id, encrypted_key, base_url_override, enabled, metadata, auth_mode)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict(user_id, api_id) do update set
              encrypted_key = excluded.encrypted_key,
              base_url_override = excluded.base_url_override,
              enabled = excluded.enabled,
              metadata = excluded.metadata,
              auth_mode = excluded.auth_mode,
              updated_at = now()
            returning id, user_id, api_id, base_url_override, enabled, auth_mode, updated_at
            """,
            (user_id, api_id, encrypted, base_url_override or "", enabled, Jsonb(meta), auth_mode),
        ).fetchone()
    result = {"ok": True, **(expose(row) or {}), "has_credential": bool(plaintext_key)}

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


def _update_credential_meta(user_id: int, api_id: str, base_url_override: str, enabled: bool,
                            auth_mode: str = "api_key") -> dict[str, Any]:
    """只更新已存凭证的 base_url_override / enabled / auth_mode，保留密文 key 与 metadata(proxy)。

    调用方（set_credential 的 preserve_key_if_empty 分支）已做完 SSRF 闸与
    base_url_override 归一，这里只落库。无匹配行 → 报错，让前端提示先填 Key。
    metadata 不动，因此 proxy 等既有字段原样保留。

    auth_mode 也要一起写:用户把一条免鉴权凭据改回「需要 API Key」时,若只改列名不改
    auth_mode,DB 里会留着 'none',可用性判定继续按免鉴权放行 —— 与用户所见不符。
    """
    canonical = normalize_api_id(api_id)
    with connect() as db:
        row = db.execute(
            """
            update user_api_credentials
               set base_url_override = %s, enabled = %s, auth_mode = %s, updated_at = now()
             where user_id = %s and api_id = any(%s)
            returning id, user_id, api_id, base_url_override, enabled, auth_mode, updated_at
            """,
            (base_url_override or "", enabled, auth_mode, user_id, _credential_aliases(canonical)),
        ).fetchone()
    if not row:
        raise ValueError("尚未配置该供应商的 API Key，请先填写 Key")
    return {"ok": True, **(expose(row) or {}), "has_credential": True}


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
                   metadata, auth_mode, length(encrypted_key) as cipher_len
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
        _auth_mode = str(r.get("auth_mode") or "api_key") or "api_key"
        items.append({
            "api_id": api_id,
            "has_credential": int(r["cipher_len"] or 0) > 0,
            "auth_mode": _auth_mode,
            # 免鉴权凭据没 key 也算配好了 —— 前端用它显示「已配置」,别再提示"请先填 Key"
            "configured": int(r["cipher_len"] or 0) > 0 or _auth_mode == "none",
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
        # auth_mode='none' = 用户显式声明「这个端点免鉴权」(本地 Ollama/vLLM 等)。
        # 这类凭据允许 key 为空;key 填了就照常带上(部分 vLLM/LM Studio 要一个任意 token)。
        auth_mode = str(row.get("auth_mode") or "api_key").strip() or "api_key"
        # 密钥派生(HKDF info=api:<id>)与 AAD(api=<id>)都绑定 api_id。历史上凭据可能以
        # 别名(如 'AgentPlatform')加密;migration v67 规范化重命名了 api_id 列却未重新
        # 加密 blob,导致用当前列值解密会失败(AAD/密钥不匹配)。依次尝试 [当前列值] +
        # [canonical 的全部别名],命中即恢复 —— 兼容任意历史 api_id 命名,无需重新加密迁移。
        plaintext = ""
        for _cand in [stored_api_id, *_credential_aliases(canonical)]:
            plaintext = decrypt_api_key(blob, user_id, _cand)
            if plaintext:
                break
        # 免鉴权凭据没有 key 是正常态,不能像 api_key 模式那样当成「解密失败」跳过。
        if not plaintext and auth_mode != "none":
            continue
        _meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        return {
            "api_id": canonical,
            "key": plaintext,
            "auth_mode": auth_mode,
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
            # 读时也过一遍规整(补 Google /openai、剥 /chat/completions)→ 存量误填的凭据自愈,用户无需重存。
            # 注:免鉴权(auth_mode='none')但用户仍填了 key 的,也走这条 —— key 照常发送。
            return {"key": cred["key"], "source": "user_db",
                    "base_url_override": _normalize_openai_base_url(cred.get("base_url_override", "")),
                    "proxy": cred.get("proxy", "")}
        if cred and cred.get("auth_mode") == "none":
            # 免鉴权端点且用户没填 key:这是**合法可用**状态,不能继续往下掉进 env 回退/none。
            # source 单独标记,好让 GM 后端区分「用户明确说不需要 key」与「压根没配」。
            return {"key": "", "source": "user_db_no_auth",
                    "base_url_override": _normalize_openai_base_url(cred.get("base_url_override", "")),
                    "proxy": cred.get("proxy", "")}

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
