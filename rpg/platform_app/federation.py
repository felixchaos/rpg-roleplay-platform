"""
federation.py — 本地部署 ↔ 在线剧本库打通(功能 B)

同一套代码,一个实例可同时扮演两种角色:

【在线提供方 provider】对外签发个人访问令牌(PAT)/ 设备码,暴露 /api/ext/library/*:
  - PAT:Bearer 令牌,scope 限 library:read / library:publish。库里只存 sha256 哈希。
  - 设备码流(GitHub CLI 式):客户端拿 device_code 轮询,用户浏览器输 user_code 批准。
    令牌明文在「轮询命中」那一刻才生成并一次性返回,从不落库。
  - ext API:列公开剧本 / 整包 zip 下载(read)/ 上传整包发布(publish)。
    只服务 is_public 剧本 + 该令牌用户自有剧本;私有他人剧本绝不出库(版权 + 隔离)。

【本地客户端 client】把在线服务当剧本源:
  - 连接器凭据复用 user_api_credentials(api_id=online_library,令牌加密存储)+ base_url(SSRF 校验)。
  - 浏览 / 导入(整包 clone 落本地)/ 发布(整包上传到在线)。
  - 默认指向官方域名,允许覆盖为自建在线节点(走 _validate_base_url)。
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from . import user_credentials
from .db import connect, expose, init_db
from .knowledge import script_pack

# ── 轻量进程内限流(滑动窗口)──────────────────────────────────────────────
# 200 用户级别足够;多机部署时改 Redis(workers=2 时每键上限 ×2,仍足够防滥用)。
_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}
MAX_PATS_PER_USER = 50          # 每用户活跃(未吊销)PAT 上限


def rate_ok(key: str, max_calls: int, window_s: float) -> bool:
    """滑动窗口限流。key 命中数 < max_calls 才放行。线程安全 + 机会式清理。"""
    now = time.monotonic()
    with _rate_lock:
        if len(_rate_hits) > 10000:          # 防 key 膨胀:整体过大时清掉过期窗口
            for k in list(_rate_hits.keys()):
                _rate_hits[k] = [t for t in _rate_hits[k] if now - t < window_s]
                if not _rate_hits[k]:
                    del _rate_hits[k]
        hits = [t for t in _rate_hits.get(key, []) if now - t < window_s]
        if len(hits) >= max_calls:
            _rate_hits[key] = hits
            return False
        hits.append(now)
        _rate_hits[key] = hits
        return True

# ── 常量 ────────────────────────────────────────────────────────────────
PAT_PREFIX = "rpgpat_"
DEFAULT_OFFICIAL_BASE = "https://rpg-roleplay.stellatrix.icu"
CONNECTOR_API_ID = "online_library"          # user_api_credentials.api_id
VALID_SCOPES = ("library:read", "library:publish")
DEVICE_TTL_SECONDS = 600                      # 设备码 10 分钟有效
DEVICE_POLL_INTERVAL = 5
PAT_DEFAULT_TTL_DAYS = 365
MAX_PACK_BYTES = script_pack.MAX_ZIP_BYTES    # 复用剧本包上限


def provider_enabled() -> bool:
    """本实例是否扮演「在线库提供方」(签发 PAT/设备码、暴露 /api/ext/*)。

    默认仅服务器模式(effective_auth_required)开启;本地单用户自部署(client 角色)关闭——
    本地实例只该作为「连接到在线库」的客户端,不该自己签发令牌/批准设备(否则一个暴露在网络上的
    本地实例 = 无鉴权的令牌签发面 = 权限泄漏)。可用 RPG_FEDERATION_PROVIDER=1/0 显式覆盖
    (供自建在线联邦节点)。"""
    import os

    from core.config import effective_auth_required
    raw = (os.environ.get("RPG_FEDERATION_PROVIDER", "") or "").strip()
    if raw == "1":
        return True
    if raw == "0":
        return False
    return effective_auth_required()


def official_base() -> str:
    """本服务的规范对外地址。用 PUBLIC_BASE_URL 配置,**不取请求 Host**
    (防 Host 头注入把 verification_uri 指向攻击者域名 = 反射式 open-redirect)。"""
    import os
    return os.environ.get("PUBLIC_BASE_URL", DEFAULT_OFFICIAL_BASE).rstrip("/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _clean_scopes(scopes: Any) -> list[str]:
    out = [s for s in (scopes or []) if s in VALID_SCOPES]
    return out or ["library:read"]


# ════════════════════════════════════════════════════════════════════════
#  PROVIDER:个人访问令牌(PAT)
# ════════════════════════════════════════════════════════════════════════

def create_pat(user_id: int, name: str, scopes: list[str], ttl_days: int = PAT_DEFAULT_TTL_DAYS,
               source: str = "manual") -> dict[str, Any]:
    """生成 PAT。明文令牌只在此处返回一次,库里只存哈希。source: manual|device。"""
    init_db()
    token = PAT_PREFIX + secrets.token_urlsafe(32)
    token_hash = _hash(token)
    scopes = _clean_scopes(scopes)
    expires_at = _now() + timedelta(days=max(1, min(int(ttl_days or PAT_DEFAULT_TTL_DAYS), 3650)))
    with connect() as db:
        active = db.execute(
            "select count(*) as n from personal_access_tokens "
            "where user_id = %s and revoked_at is null",
            (user_id,),
        ).fetchone()
        if int((dict(active) or {}).get("n", 0)) >= MAX_PATS_PER_USER:
            raise ValueError(f"活跃令牌已达上限({MAX_PATS_PER_USER}),请先吊销不用的令牌")
        row = db.execute(
            "insert into personal_access_tokens(user_id, token_hash, name, scopes, expires_at, source) "
            "values (%s, %s, %s, %s, %s, %s) returning id, name, scopes, created_at, expires_at, source",
            (user_id, token_hash, (name or "")[:120], Jsonb(scopes), expires_at,
             "device" if source == "device" else "manual"),
        ).fetchone()
    return {"ok": True, "token": token, "pat": expose(row)}


def list_pats(user_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        rows = db.execute(
            "select id, name, scopes, source, created_at, expires_at, last_used_at, revoked_at "
            "from personal_access_tokens where user_id = %s order by id desc",
            (user_id,),
        ).fetchall()
    return {"ok": True, "items": [expose(r) for r in rows]}


def revoke_pat(user_id: int, pat_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        db.execute(
            "update personal_access_tokens set revoked_at = now() "
            "where id = %s and user_id = %s and revoked_at is null",
            (int(pat_id), user_id),
        )
    return {"ok": True}


def verify_pat(token: str, required_scope: str | None = None) -> dict[str, Any]:
    """校验 Bearer PAT。返回 {user, scopes}。失败抛 PermissionError。"""
    if not token or not token.startswith(PAT_PREFIX):
        raise PermissionError("invalid token")
    init_db()
    th = _hash(token)
    with connect() as db:
        row = db.execute(
            "select p.id, p.user_id, p.scopes, p.expires_at, p.revoked_at, "
            "       u.username, u.role, u.public_id "
            "from personal_access_tokens p join users u on u.id = p.user_id "
            "where p.token_hash = %s",
            (th,),
        ).fetchone()
        if not row:
            raise PermissionError("invalid token")
        d = dict(row)
        if d.get("revoked_at"):
            raise PermissionError("token revoked")
        exp = d.get("expires_at")
        if exp and exp < _now():
            raise PermissionError("token expired")
        scopes = list(d.get("scopes") or [])
        if required_scope and required_scope not in scopes:
            raise PermissionError(f"missing scope {required_scope}")
        db.execute("update personal_access_tokens set last_used_at = now() where id = %s", (d["id"],))
    return {
        "user": {"id": int(d["user_id"]), "username": d.get("username"),
                 "role": d.get("role"), "public_id": d.get("public_id")},
        "scopes": scopes,
    }


# ════════════════════════════════════════════════════════════════════════
#  PROVIDER:设备码流
# ════════════════════════════════════════════════════════════════════════

def _gen_user_code() -> str:
    """人类可读、无易混字符(去 0/O/1/I)的 4-4 码,如 WXYZ-7K9M。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def device_start(client_name: str, scopes: list[str], verification_uri: str) -> dict[str, Any]:
    """客户端发起设备授权。返回 device_code(客户端轮询用)+ user_code(用户浏览器输入)。"""
    init_db()
    device_code = secrets.token_urlsafe(32)
    user_code = _gen_user_code()
    scopes = _clean_scopes(scopes)
    expires_at = _now() + timedelta(seconds=DEVICE_TTL_SECONDS)
    with connect() as db:
        # 机会式清理过期/终态设备授权行,防表无界增长(放大 DoS)。
        db.execute(
            "delete from device_authorizations where expires_at < now() - interval '1 hour'",
        )
        db.execute(
            "insert into device_authorizations(device_code_hash, user_code, client_name, scopes, "
            "  status, expires_at, interval_seconds) values (%s, %s, %s, %s, 'pending', %s, %s)",
            (_hash(device_code), user_code, (client_name or "")[:120], Jsonb(scopes),
             expires_at, DEVICE_POLL_INTERVAL),
        )
    sep = "&" if "?" in verification_uri else "?"
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "verification_uri_complete": f"{verification_uri}{sep}code={user_code}",
        "expires_in": DEVICE_TTL_SECONDS,
        "interval": DEVICE_POLL_INTERVAL,
        "scopes": scopes,
    }


def device_lookup(user_code: str) -> dict[str, Any] | None:
    """按 user_code 查待批准的设备授权(给批准页展示 client_name/scopes)。"""
    init_db()
    uc = (user_code or "").strip().upper()
    with connect() as db:
        row = db.execute(
            "select id, client_name, scopes, status, expires_at from device_authorizations "
            "where user_code = %s",
            (uc,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("expires_at") and d["expires_at"] < _now():
        return None
    return expose(d)


def device_approve(user_id: int, user_code: str, deny: bool = False) -> dict[str, Any]:
    """登录用户在浏览器批准/拒绝某 user_code。不在此生成 PAT(轮询命中时才生成)。"""
    init_db()
    uc = (user_code or "").strip().upper()
    with connect() as db:
        row = db.execute(
            "select id, status, expires_at from device_authorizations where user_code = %s",
            (uc,),
        ).fetchone()
        if not row:
            raise ValueError("授权码不存在或已失效")
        d = dict(row)
        if d.get("expires_at") and d["expires_at"] < _now():
            raise ValueError("授权码已过期")
        if d.get("status") != "pending":
            raise ValueError("该授权码已处理")
        db.execute(
            "update device_authorizations set status = %s, user_id = %s, approved_at = now() "
            "where id = %s",
            ("denied" if deny else "approved", user_id, d["id"]),
        )
    return {"ok": True, "status": "denied" if deny else "approved"}


def device_poll(device_code: str) -> dict[str, Any]:
    """客户端轮询。批准且未取过令牌时,在此一次性生成并返回 PAT 明文(不落库)。"""
    init_db()
    dh = _hash(device_code)
    with connect() as db:
        row = db.execute(
            "select id, user_id, scopes, status, expires_at, pat_id, client_name "
            "from device_authorizations where device_code_hash = %s",
            (dh,),
        ).fetchone()
        if not row:
            return {"error": "invalid_grant"}
        d = dict(row)
        if d.get("expires_at") and d["expires_at"] < _now():
            return {"error": "expired_token"}
        status = d.get("status")
        if status == "pending":
            return {"error": "authorization_pending"}
        if status == "denied":
            return {"error": "access_denied"}
        # approved
        if d.get("pat_id"):
            return {"error": "token_already_issued"}
        res = create_pat(int(d["user_id"]), d.get("client_name") or "device",
                         list(d.get("scopes") or []), source="device")
        db.execute(
            "update device_authorizations set pat_id = %s where id = %s",
            (res["pat"]["id"], d["id"]),
        )
    return {"ok": True, "access_token": res["token"], "scopes": res["pat"]["scopes"]}


# ════════════════════════════════════════════════════════════════════════
#  PROVIDER:外部库读写
# ════════════════════════════════════════════════════════════════════════

def ext_list_scripts(q: str | None, limit: int, offset: int) -> dict[str, Any]:
    """列公开剧本(供外部客户端浏览)。"""
    init_db()
    limit = max(1, min(int(limit or 30), 100))
    offset = max(0, int(offset or 0))
    where = "s.is_public"
    params: list[Any] = []
    if q:
        where += " and (lower(s.title) like %s or lower(coalesce(s.description,'')) like %s)"
        like = f"%{q.lower()}%"
        params += [like, like]
    with connect() as db:
        rows = db.execute(
            f"select s.id, s.title, s.description, s.clone_count, s.updated_at, u.username as owner_name "
            f"from scripts s left join users u on u.id = s.owner_id "
            f"where {where} order by s.clone_count desc nulls last, s.updated_at desc "
            f"limit %s offset %s",
            (*params, limit, offset),
        ).fetchall()
    return {"ok": True, "items": [expose(r) for r in rows]}


def ext_export_pack(script_id: int) -> tuple[bytes, str]:
    """导出公开剧本整包 zip。只允许 is_public 剧本;以原 owner 身份打包。"""
    init_db()
    with connect() as db:
        row = db.execute(
            "select owner_id, is_public, title from scripts where id = %s",
            (int(script_id),),
        ).fetchone()
    if not row:
        raise ValueError("剧本不存在")
    d = dict(row)
    if not d.get("is_public"):
        raise PermissionError("该剧本未公开")
    return script_pack.export_script_pack(int(script_id), int(d["owner_id"]), include_chunks=False)


def ext_publish_pack(user_id: int, zip_bytes: bytes) -> dict[str, Any]:
    """外部客户端上传整包发布到在线库:导入为该用户新剧本。

    安全:**不无条件公开**。导入后走与正常「设为公开」端点(api/scripts.py)一致的闸:
    非空章节 + review_status='reviewed' 才置 is_public=true;否则只导入为私有草稿
    + 返回 warning,引导用户在在线服务里复核后再公开。防未审/侵权/空剧本污染公开库。
    """
    if len(zip_bytes) > MAX_PACK_BYTES:
        raise ValueError(f"pack too large (max {MAX_PACK_BYTES // 1024 // 1024}MB)")
    res = script_pack.import_script_pack(zip_bytes, user_id)
    new_sid = int(res.get("script_id"))
    warnings = list(res.get("warnings") or [])
    init_db()
    with connect() as db:
        ch = db.execute(
            "select count(*) as n from script_chapters where script_id = %s", (new_sid,),
        ).fetchone()
        chapter_count = int((dict(ch) or {}).get("n", 0))
        sc = db.execute(
            "select review_status from scripts where id = %s and owner_id = %s", (new_sid, user_id),
        ).fetchone()
        review_status = (dict(sc) or {}).get("review_status", "unreviewed") if sc else "unreviewed"
        eligible = chapter_count > 0 and review_status == "reviewed"
        if eligible:
            db.execute(
                "update scripts set is_public = true, published_at = now() "
                "where id = %s and owner_id = %s",
                (new_sid, user_id),
            )
        else:
            if chapter_count == 0:
                warnings.append("空剧本(0 章)未公开;导入为私有草稿。")
            else:
                warnings.append("剧本未通过 KB 复核,未公开;已导入为私有草稿,请在在线服务复核后再公开。")
    return {"ok": True, "script_id": new_sid, "is_public": eligible, "warnings": warnings}


# ════════════════════════════════════════════════════════════════════════
#  CLIENT:本地连接器(指向在线服务)
# ════════════════════════════════════════════════════════════════════════

def _normalize_base(base_url: str) -> str:
    return (base_url or DEFAULT_OFFICIAL_BASE).strip().rstrip("/")


def connector_set(user_id: int, base_url: str, token: str) -> dict[str, Any]:
    """保存本地连接器:base_url(SSRF 校验)+ 令牌(加密)。空 token = 断开。"""
    base = _normalize_base(base_url)
    user_credentials._validate_base_url(base)  # 复用 SSRF 防护(私网/环回/明文 http 拦截)
    # 复用 user_api_credentials 加密存储:encrypted_key=PAT,base_url_override=在线地址。
    return user_credentials.set_credential(
        user_id, CONNECTOR_API_ID, token, base_url_override=base,
        enabled=bool(token), allow_base_url=True,
    )


def connector_get(user_id: int) -> dict[str, Any]:
    """读连接器状态(不含明文令牌)。"""
    cred = user_credentials.get_credential(user_id, CONNECTOR_API_ID)
    if not cred or not cred.get("key"):
        return {"ok": True, "connected": False, "base_url": DEFAULT_OFFICIAL_BASE}
    return {"ok": True, "connected": True,
            "base_url": cred.get("base_url_override") or DEFAULT_OFFICIAL_BASE}


def _connector_auth(user_id: int) -> tuple[str, str]:
    """返回 (base_url, token)。未连接抛 ValueError。

    请求时再校一次 base_url(SSRF / DNS rebinding 缓解:存时校过,但攻击者可能
    在存与用之间翻转 DNS,故每次出站前重新解析校验)。
    """
    cred = user_credentials.get_credential(user_id, CONNECTOR_API_ID)
    if not cred or not cred.get("key"):
        raise ValueError("尚未连接在线剧本库,请先在「设置 → 在线剧本库」连接")
    base = _normalize_base(cred.get("base_url_override"))
    user_credentials._validate_base_url(base)
    return base, cred["key"]


def _client(base_url: str, token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # 整包下载/上传可能数十秒;给 120s,与前端 180s 留余量。
    # 使用 _SsrfGuardTransport 替代裸 httpx.Client:传输层 SSRF 守卫(use-time 重解析 + 不跟随重定向)。
    from core.outbound import _SsrfGuardTransport
    inner = httpx.HTTPTransport()
    return httpx.Client(
        base_url=base_url,
        headers=headers,
        timeout=httpx.Timeout(120.0, connect=10.0),
        follow_redirects=False,
        transport=_SsrfGuardTransport(inner),
    )


def connector_test(user_id: int) -> dict[str, Any]:
    base, token = _connector_auth(user_id)
    with _client(base, token) as c:
        r = c.get("/api/ext/library/scripts", params={"limit": 1})
    if r.status_code == 200:
        return {"ok": True, "base_url": base}
    if r.status_code in (401, 403):
        return {"ok": False, "error": "令牌无效或已过期,请重新连接"}
    return {"ok": False, "error": f"连接失败 HTTP {r.status_code}"}


def connector_list(user_id: int, q: str | None, limit: int = 30, offset: int = 0) -> dict[str, Any]:
    base, token = _connector_auth(user_id)
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if q:
        params["q"] = q
    with _client(base, token) as c:
        r = c.get("/api/ext/library/scripts", params=params)
    if r.status_code != 200:
        raise ValueError(f"在线库返回 HTTP {r.status_code}")
    return r.json()


def connector_import(user_id: int, remote_script_id: int) -> dict[str, Any]:
    """从在线库下载整包 → 完整 clone 到本地账户(非指针)。"""
    base, token = _connector_auth(user_id)
    with _client(base, token) as c:
        r = c.get(f"/api/ext/library/scripts/{int(remote_script_id)}/pack")
    if r.status_code == 403:
        raise ValueError("该剧本未公开,无法导入")
    if r.status_code != 200:
        raise ValueError(f"下载失败 HTTP {r.status_code}")
    zip_bytes = r.content
    if zip_bytes[:4] != b"PK\x03\x04":
        raise ValueError("在线库返回的不是合法剧本包")
    res = script_pack.import_script_pack(zip_bytes, user_id)
    return {"ok": True, "script_id": res.get("script_id"), "warnings": res.get("warnings") or []}


def connector_publish(user_id: int, local_script_id: int) -> dict[str, Any]:
    """把本地自有剧本整包上传到在线库发布(需令牌含 library:publish)。"""
    base, token = _connector_auth(user_id)
    # 以本地 owner 身份导出整包(校验自有)
    zip_bytes, _fname = script_pack.export_script_pack(int(local_script_id), user_id, include_chunks=False)
    with _client(base, token) as c:
        r = c.post("/api/ext/library/scripts/publish",
                   files={"file": ("pack.zip", zip_bytes, "application/zip")})
    if r.status_code == 403:
        raise ValueError("令牌缺少发布权限(library:publish),请重新连接并勾选发布")
    if r.status_code != 200:
        raise ValueError(f"发布失败 HTTP {r.status_code}")
    return r.json()


# ── CLIENT:设备码流(本地引导用户在浏览器授权在线服务)──────────────────
def connector_device_start(user_id: int, base_url: str, scopes: list[str]) -> dict[str, Any]:
    base = _normalize_base(base_url)
    user_credentials._validate_base_url(base)
    with _client(base) as c:
        r = c.post("/api/ext/device/code",
                   json={"client_name": "本地部署", "scopes": _clean_scopes(scopes)})
    if r.status_code != 200:
        raise ValueError(f"在线服务不支持设备码流或返回 HTTP {r.status_code}")
    data = r.json()
    data["base_url"] = base
    return data


def connector_device_poll(user_id: int, base_url: str, device_code: str) -> dict[str, Any]:
    """轮询在线服务;拿到令牌即落本地加密存储。"""
    base = _normalize_base(base_url)
    user_credentials._validate_base_url(base)  # SEC(H-1): 与 device_start 对齐,防 SSRF 跳过 start 直调 poll
    with _client(base) as c:
        r = c.post("/api/ext/device/token", json={"device_code": device_code})
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if data.get("ok") and data.get("access_token"):
        connector_set(user_id, base, data["access_token"])
        return {"ok": True, "connected": True}
    # 透传 pending / denied / expired 等状态给前端继续轮询或停止
    return {"ok": False, "status": data.get("error") or "pending"}
