"""platform_app.api._deps — 跨 router 共享的 dependency / helper。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse as BaseJSONResponse

from tools_dsl.tool_registry import tool_payload

from .. import auth, library, workspace
from ..db import connect, init_db

SESSION_COOKIE = "rpg_session"
API_VERSION = "1"

COMMANDS = [
    ("GET", "/", "Backend root (service info JSON)"),
    ("GET", "/api/state", "读取当前可玩存档状态"),
    ("POST", "/api/new", "创建新游戏并保留旧档备份"),
    ("POST", "/api/opening", "生成开场"),
    ("POST", "/api/chat", "发送玩家行动/对话，支持流式 GM 输出与结构化状态写回"),
    ("POST", "/api/stop", "打断当前生成"),
    ("POST", "/api/save", "手动保存当前游戏"),
    ("POST", "/api/memory/mode", "设置记忆模式"),
    ("POST", "/api/memory/add", "添加长期记忆"),
    ("POST", "/api/memory/remove", "删除长期记忆"),
    ("POST", "/api/permissions", "设置 LLM 状态写入权限"),
    ("GET", "/api/models", "读取 API/模型树与前端显示模型"),
    ("POST", "/api/models/select", "选择当前前端模型"),
    ("POST", "/api/models/api", "新增或更新 API 供应商"),
    ("POST", "/api/models/model", "新增或更新 API 下属模型"),
    ("GET", "/api/tools", "插件/MCP/Skill 能力状态"),
    ("POST", "/api/mcp/server", "新增或更新 MCP 服务器配置"),
    ("POST", "/api/mcp/server/enabled", "启用或禁用 MCP 服务器"),
    ("POST", "/api/mcp/server/delete", "删除 MCP 服务器配置"),
    ("POST", "/api/mcp/server/validate", "校验 MCP stdio 命令可用性"),
    ("POST", "/api/skills/import", "本地部署导入 Skill 包"),
    ("POST", "/api/worldline/variable", "新增或锁定用户世界线变量"),
    ("POST", "/api/worldline/variable/remove", "移除用户世界线变量"),
    ("POST", "/api/auth/register", "注册账号"),
    ("POST", "/api/auth/login", "登录并写入会话 cookie"),
    ("POST", "/api/auth/logout", "退出登录"),
    ("GET", "/api/platform", "平台总览：主页、剧本、存档、库、工具"),
    ("GET", "/api/scripts", "剧本列表"),
    ("POST", "/api/scripts/import", "导入 TXT/MD 剧本并自动识别章节"),
    ("GET", "/api/scripts/{script_id}/chapters", "读取剧本章节目录与预览"),
    (
        "POST",
        "/api/scripts/{script_id}/knowledge/sync",
        "重建剧本 ChapterFact、世界书、人设卡和检索块",
    ),
    ("GET", "/api/scripts/{script_id}/chapter-facts", "读取剧本 ChapterFact 时间线"),
    (
        "GET",
        "/api/scripts/{script_id}/birthpoints",
        "入场选出生点：按 phase 聚合 + 每 phase 均匀采样 anchor",
    ),
    ("GET", "/api/scripts/{script_id}/character-cards", "读取剧本人设卡"),
    ("GET", "/api/scripts/{script_id}/worldbook", "读取剧本世界书条目"),
    ("GET", "/api/saves", "游戏存档目录"),
    ("POST", "/api/saves", "基于剧本创建新存档"),
    ("GET", "/api/branches/{save_id}", "读取某个存档的分支树"),
    ("POST", "/api/branches/continue", "从任意对话节点派生/激活当前游戏 runtime"),
    ("POST", "/api/branches/activate", "直接激活某个分支节点为当前游戏 runtime"),
    ("POST", "/api/branches/delete", "删除某条连线下的整条分支"),
    ("GET", "/api/saves/{save_id}/context-runs", "读取某个存档的上下文子代理运行记录"),
    ("GET", "/api/saves/{save_id}/anchors", "task 136: 读取存档世界线收束锚点状态"),
    ("POST", "/api/saves/{save_id}/anchors/reseed", "task 136: 重 seed 锚点 (调试用)"),
    ("GET", "/api/settings", "读取设置"),
    ("POST", "/api/settings", "写入设置"),
    ("GET", "/api/library", "文件库列表"),
    ("POST", "/api/library/upload", "文件库上传"),
    ("POST", "/api/library/mkdir", "文件库创建文件夹"),
    ("POST", "/api/library/delete", "文件库删除"),
    ("GET", "/api/library/download", "文件库下载"),
    ("GET", "/api/platform/commands", "读取全部功能指令清单"),
]


def json_response(content, status_code: int = 200, **kwargs):
    if isinstance(content, dict) and "meta" not in content:
        content = {
            **content,
            "meta": {
                "api_version": API_VERSION,
                "stable": True,
            },
        }
    return BaseJSONResponse(jsonable_encoder(content), status_code=status_code, **kwargs)


def _request_is_https(request: Request) -> bool:
    """浏览器面向的连接是否 HTTPS。
    容器内 uvicorn 不带 --proxy-headers 时 request.url.scheme 恒为 http(哪怕前面挂了
    TLS 反代),所以必须读 X-Forwarded-Proto 才能识别 nginx/CF 终止的 HTTPS。
    XFP 只用于决定 cookie 的 Secure 标志,伪造它顶多废掉伪造者自己的 cookie。
    """
    if request.url.scheme == "https":
        return True
    xfp = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return xfp == "https"


def _cookie_security_kwargs(request: Request) -> dict:
    """统一的 cookie 安全参数,set 和 delete 必须用同一组,否则浏览器会把
    delete 当成"另一个 cookie",导致原 cookie 残留。

    samesite 固定 lax(同源部署)。secure 跟随【有效协议】:HTTPS(含反代 X-Forwarded-Proto)
    才带 Secure;明文 HTTP 自托管不带(浏览器会【静默丢弃】明文 HTTP 上的 Secure cookie →
    会话存不下 → 登录后弹回登录页)。反代 HTTPS 终止时容器内 scheme 恒 http,必须靠 XFP
    才能正确带上 Secure。
    """
    return {
        "httponly": True,
        "secure": _request_is_https(request),
        "samesite": "lax",
        "path": "/",
    }


def _set_session_cookie(response: BaseJSONResponse, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 24 * 60 * 60,
        **_cookie_security_kwargs(request),
    )


def _delete_session_cookie(response: BaseJSONResponse, request: Request) -> None:
    """删 cookie 必须传跟 set 一致的 samesite/secure,否则浏览器收到的 Set-Cookie
    属性不匹配,会被当成"另一个 cookie"残留,反而留下原 cookie。"""
    response.delete_cookie(SESSION_COOKIE, **_cookie_security_kwargs(request))


def _auth_required() -> bool:
    """与 ui.py:_api_auth_required 同义，避免循环导入；服务器模式禁止匿名访问。"""
    from core.config import effective_auth_required as _eff
    return _eff()


# ensure_default 是「遗留存档 backfill」(补分支树 + runtime 指针),不是每请求都要做的事:
# 新存档在 workspace.create_save() 创建时已自行 seed_tree,所以 ensure_default 只为存量旧
# 存档兜底。原实现每个认证请求都跑一个 game_saves⋈scripts JOIN(+有存档时还 seed_tree +
# 读 runtime 文件),是热路径浪费。用进程内幂等守卫:每用户每进程只 backfill 一次。
# best-effort(GIL 下 set in/add 原子;多 worker 各跑一次、backfill 幂等,无害,故不上锁)。
_ENSURED_DEFAULT_USERS: set[int] = set()


def _local_default_user() -> dict | None:
    """本地/自部署免登录模式:取库中第一个用户(按 id)作为隐式登录用户。无则 None。"""
    try:
        with connect() as db:
            row = db.execute("select * from users order by id asc limit 1").fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def _is_loopback(request: Request) -> bool:
    """请求是否来自本机回环(127.0.0.1 / ::1 / localhost)。LAN 设备走真实 IP → False。"""
    host = (request.client.host if request.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost"} or host.startswith("127.")


def current_user(request: Request) -> dict | None:
    try:
        init_db()
        user = auth.user_from_token(request.cookies.get(SESSION_COOKIE))
        # 本地/自部署免登录模式(_auth_required()=False)且无 cookie 时,回退到库里第一个用户,
        # 让单用户本地部署开箱即用。**安全收紧**:仅「本机回环 + 默认账户未设密码」时才免登录回退;
        # LAN 设备(非回环)或默认账户已设密码 → 必须有真实会话(cookie / 魔法链接),
        # 否则同网任意设备都能越权拿到默认账户(做好本地部署鉴权)。服务器模式绝不走这条路。
        if user is None and not _auth_required():
            if _is_loopback(request) and not auth.local_account_has_password():
                user = _local_default_user()
        if user:
            uid = int(user["id"])
            if uid not in _ENSURED_DEFAULT_USERS:
                workspace.ensure_default(uid)
                _ENSURED_DEFAULT_USERS.add(uid)
        return user
    except Exception:
        return None


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="需要登录")
    return user


def is_admin(user: dict | None) -> bool:
    """纯 admin 管理权谓词(role == 'admin')。

    注意:这与「平台兜底资格」(role ∈ {admin, vip_user},见
    knowledge.embedding.has_platform_fallback_role)是不同职责,资格集合不同,绝不跨用。
    """
    return bool(user and user.get("role") == "admin")


def require_admin(user=Depends(require_user)) -> dict:
    """FastAPI Depends:要求当前用户是 admin,否则 403。"""
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def _resolve_save_id(user_id: int, body: dict) -> int:
    raw = body.get("save_id")
    if raw:
        sid = int(raw)
        # P1 fix: 显式校验 save 属于本人，不依赖下游兜底
        from ..perms import owns_save
        with connect() as db:
            if not owns_save(db, sid, user_id):
                raise HTTPException(status_code=403, detail="无权访问该存档")
        return sid
    with connect() as db:
        row = db.execute(
            "select id from game_saves where user_id = %s order by updated_at desc, id desc limit 1",
            (user_id,),
        ).fetchone()
    if not row:
        raise ValueError("还没有可写入的存档")
    return int(row["id"])


def platform_for(user: dict | None) -> dict:
    """构建 /api/platform 和注册/登录响应的 payload。

    安全：MCP server 的 command/args/env 含 secret，普通用户必须脱敏。
    与 ui.py:_redact_tools 共用同一份逻辑，避免再次出现"漏脱敏入口"。
    """
    payload = workspace.overview(user)
    is_admin = bool(user and user.get("role") == "admin")
    payload["tools"] = _redact_mcp_in_tools(tool_payload(), is_admin)
    payload["commands"] = command_payload()
    if user:
        payload["library"] = library.list_dir(user["id"], "")
    return payload


_MCP_SECRET_FIELDS = ("command", "args", "env", "url", "headers", "credential", "secret", "token")


def _redact_mcp_in_tools(tools: dict, is_admin: bool) -> dict:
    """递归脱敏 tools 里的 mcp.servers[].command/args/env。"""
    if is_admin:
        return tools
    import copy

    out = copy.deepcopy(tools)
    for srv in (out.get("mcp") or {}).get("servers") or []:
        for field in _MCP_SECRET_FIELDS:
            srv.pop(field, None)
    for srv in out.get("mcp_servers") or []:
        for field in _MCP_SECRET_FIELDS:
            srv.pop(field, None)
    return out


def command_payload() -> list[dict]:
    return [
        {"method": method, "path": path, "name": path.rsplit("/", 1)[-1] or path, "desc": desc}
        for method, path, desc in COMMANDS
    ]


def _client_ip(request: Request) -> str:
    """获取客户端 IP。

    安全：默认只用 TCP 层的 request.client.host，不信任 X-Forwarded-For
    （否则攻击者直接换头就能绕过按 IP 的速率限制）。
    仅当 TCP 对端 IP 在 RPG_TRUSTED_PROXIES 白名单里（如 nginx/cloudflare 后端），
    才信 XFF 的第一段。
    """
    tcp_ip = request.client.host if request.client else ""
    from core.config import trusted_proxies_raw as _trusted_proxies_raw

    trusted = {ip.strip() for ip in _trusted_proxies_raw().split(",") if ip.strip()}
    if tcp_ip and tcp_ip in trusted:
        xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if xff:
            return xff
    return tcp_ip
