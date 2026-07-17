"""core.startup — FastAPI app 启动配置 (middleware / exception_handlers / lifespan)。

调用方式:
    from core.startup import configure_app
    configure_app(app)

lifespan 需在 FastAPI() 构造时传入:
    from core.startup import lifespan
    app = FastAPI(lifespan=lifespan, ...)
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from ipaddress import ip_address, ip_network
from json import JSONDecodeError
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware

from core.config import (
    cors_max_age as _cors_max_age,
    gzip_min_bytes as _gzip_min_bytes,
    trusted_proxies as _trusted_proxies,
)
from core.logging import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

# ── 可观测性: request_id ContextVar ─────────────────────────────────────────
# contextvars 会跨 asyncio.to_thread 传播,SSE stream 子线程也能拿到 request_id。
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class RequestIdFilter(logging.Filter):
    """将当前 ContextVar 中的 request_id 注入 LogRecord。"""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        record.request_id = _request_id_var.get() or "-"  # type: ignore[attr-defined]
        return True


def get_request_id() -> str:
    """供业务代码读取当前 request_id。"""
    return _request_id_var.get() or "-"

# ── API 版本（与 app.py 保持一致）────────────────────────────────────────
API_VERSION = "1"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_LOOPBACK_ORIGIN_REGEX = r"^https?://(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d{1,5})?$"
# 本地/桌面自托管放行的来源:loopback + 私网 LAN(RFC1918)+ CGNAT/网状 VPN(RFC6598,
# 如 Tailscale 的 100.64/10 与 fd7a::/48 ULA)+ IPv6 ULA/链路本地。让用户从手机 / 同网
# 设备 / Tailscale 组网经内网 IP(如 http://192.168.1.4:7860、http://100.98.88.64:7860)
# 访问桌面端服务器——邀请链接 / 登录 / 打开存档。
# ⚠️ 仅在本地模式(is_local_mode)启用;server/production 永不放宽。
# 安全:Origin 由浏览器按页面真实来源填写,外部站点(evil.com)的 Origin 是其域名、
# 非私网/CGNAT IP,仍被拒;DNS rebinding 的 Origin 也是攻击者域名而非 IP,同样被拒。
# ⚠️ 同面横扫:下方 _PRIVATE_LAN_NETS(_origin_allowed/CSRF 路径,精确 ip 判定)与
#    _LOCAL_LAN_ORIGIN_REGEX(CORS allow_origin_regex 路径)是同一「本地来源」判定的孪生
#    实现,FastAPI CORSMiddleware 只吃 regex 故无法共用一份——改一处必须同步改另一处。
_PRIVATE_LAN_NETS = (
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("100.64.0.0/10"),   # RFC6598 CGNAT / Tailscale 等网状 VPN
    ip_network("fc00::/7"),        # IPv6 ULA(含 Tailscale fd7a::/48)
    ip_network("fe80::/10"),       # IPv6 链路本地
)
_LOCAL_LAN_ORIGIN_REGEX = (
    r"^https?://(?:"
    r"localhost|127(?:\.\d{1,3}){3}|\[::1\]"          # loopback
    r"|10(?:\.\d{1,3}){3}"                             # 10.0.0.0/8
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"      # 172.16–31.x.x
    r"|192\.168(?:\.\d{1,3}){2}"                       # 192.168.x.x
    r"|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])(?:\.\d{1,3}){2}"  # 100.64–127.x.x CGNAT/Tailscale
    r"|\[[fF][cCdD][0-9a-fA-F:]*\]"                    # IPv6 ULA fc00::/7
    r"|\[[fF][eE][89aAbB][0-9a-fA-F:]*\]"              # IPv6 链路本地 fe80::/10
    r")(?::\d{1,5})?$"
)

# ── CORS origins 计算 ────────────────────────────────────────────────────

def _cors_origins() -> tuple[list[str], bool]:
    default_origins = (
        "http://127.0.0.1:7860,http://localhost:7860,"
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:3000,http://localhost:3000"
    )
    from core.config import cors_origins_with_default as _cors_origins_with_default
    raw = _cors_origins_with_default(default_origins)
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if not origins:
        origins = ["http://127.0.0.1:7860", "http://localhost:7860"]
    allow_all = "*" in origins
    return (["*"] if allow_all else origins), not allow_all


_origins, _allow_credentials = _cors_origins()


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    if "*" in _origins or origin in _origins:
        return True
    # 本地/桌面自托管放宽到 loopback + 私网 LAN(见 _is_local_origin);
    # server/production 仍只认显式白名单 _origins。
    return _local_loopback_origins_allowed() and _is_local_origin(origin)


def _local_loopback_origins_allowed() -> bool:
    from core.config import is_local_mode as _is_local_mode
    return _is_local_mode()


def _is_local_origin(origin: str) -> bool:
    """本地开发/自托管允许的来源:loopback + 私网 LAN(RFC1918)。
    覆盖 Vite 等任意 loopback 端口,以及手机/同网设备经 LAN IP 访问桌面端服务器。"""
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.path or parsed.query or parsed.fragment:
        return False
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        addr = ip_address(host)
    except ValueError:
        return False
    return addr.is_loopback or any(addr in net for net in _PRIVATE_LAN_NETS)


# ── lifespan (startup / shutdown) ────────────────────────────────────────


def _wait_for_db_ready() -> bool:
    """冷启动 DB 探活:有界重试直到 DB(经 PgBouncer)接受连接(select 1)。

    根因(审计#8):systemd 下 uvicorn worker 可能早于 PgBouncer/PG 就绪即启动 → init_db /
    默认账户 / durable 恢复 / zombie 回收 全部失败,而这些恢复步骤是「仅启动跑一次、当轮不重试」
    (请求路径只自愈 init_db,绝不重跑恢复)→ 残留未回收的 pending/僵尸 job 直到下次重启。
    先在 DB 就绪后再跑这些步骤即可根治。

    DB 已就绪时只花一次快速探测(零额外延迟);未就绪才退避重试。超时上限 RPG_DB_WAIT_TIMEOUT
    (秒,默认 25;设 0 跳过等待,供无 DB 的单测)。超时也不阻断启动,返 False 让后续步骤各自兜底。
    """
    import time

    try:
        timeout_s = float(os.environ.get("RPG_DB_WAIT_TIMEOUT", "25"))
    except ValueError:
        timeout_s = 25.0
    if timeout_s <= 0:
        return True
    import psycopg

    from platform_app.db.connection import database_url
    deadline = time.monotonic() + timeout_s
    delay = 0.25
    attempt = 0
    last_exc: Exception | None = None
    while True:
        attempt += 1
        try:
            with psycopg.connect(database_url(), connect_timeout=5) as c:
                c.execute("select 1")
            if attempt > 1:
                log.info("[startup] DB 就绪(第 %d 次探活)", attempt)
            return True
        except Exception as exc:
            last_exc = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(delay, remaining))
            delay = min(delay * 1.6, 2.0)
    log.error(
        "[startup] DB 探活超时 %.0fs(%d 次尝试),最后错误:%s —— 仍继续启动,依赖请求路径自愈",
        timeout_s, attempt, last_exc,
    )
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: startup → yield → shutdown。"""
    # ── startup ──────────────────────────────────────────────────────────
    # 0a. 放大默认线程执行器。run_in_executor(None)/asyncio.to_thread 默认池 =
    #     min(32, cpu+4) = 10/worker,是聊天并发天花板(每轮 chat 占 1~4 线程)。LLM 流多为
    #     socket recv 阻塞,放大到 64 线程仅多 ~0.5MB 栈/worker、几乎不占 CPU。
    try:
        import asyncio as _asyncio
        from concurrent.futures import ThreadPoolExecutor as _TPE
        _default_exec = _TPE(max_workers=64, thread_name_prefix="rpg-blocking")
        _asyncio.get_running_loop().set_default_executor(_default_exec)
        app.state.default_executor = _default_exec  # shutdown 时显式清理(见下)
    except Exception:
        log.exception("[startup] set_default_executor failed")

    # 0-pre. 等 DB 就绪:治冷启动竞争(worker 早于 PgBouncer/PG → init_db/恢复/回收 全败且当轮不重试)。
    #         DB 已就绪时仅一次快速探测,无额外延迟。
    _wait_for_db_ready()

    # 0. init_db — schema 创建 + migration（lazy import 避免循环依赖）
    try:
        from app import _bootstrap_init_db  # type: ignore[import]
        _bootstrap_init_db()
    except Exception:
        log.exception("[startup] init_db failed")

    # 0b. 本地/桌面模式:确保存在一个默认账户。否则全新桌面库零用户 →
    #     current_user 的本地回退取不到用户 → 首个业务接口 401,app 开箱即不可用。
    try:
        # 修:原调未定义的 _deployment_mode() → NameError → bootstrap_local_account 从不跑 →
        # 全新桌面库无默认账户 → 首个业务接口 401。自包含读 RPG_DEPLOYMENT_MODE(不依赖 config.is_local_mode
        # —— 生产 deploy 仓 config.py 与 OSS 分叉、那边无此函数,import 会抛 ImportError 让 server 启动期误报
        # bootstrap failed ERROR;自包含两仓都干净)。
        import os as _os
        _dm = _os.getenv("RPG_DEPLOYMENT_MODE", "local").strip().lower()
        if _dm in ("local", "desktop", "self_hosted", "self-hosted"):
            from platform_app import auth as _auth
            acct = _auth.bootstrap_local_account()
            log.info("[startup] 本地默认账户 ready: id=%s username=%s", acct.get("id"), acct.get("username"))
    except Exception:
        log.exception("[startup] bootstrap_local_account failed")

    # 0c. Turnstile 配置一致性:secret 与 sitekey 必须同时设置或同时留空。
    #     只配一半 → enabled()=False(不强制,防锁死),但运维多半以为开了 → 启动期高声告警。
    try:
        from platform_app import turnstile as _ts
        if _ts.misconfigured():
            log.error(
                "[startup] turnstile_misconfigured: RPG_TURNSTILE_SECRET 与 RPG_TURNSTILE_SITEKEY "
                "必须同时配置(secret=%s sitekey=%s) —— 当前只配一半,人机验证【未生效】,请补全后重启。",
                bool(_ts.secret()), bool(_ts.sitekey()),
            )
        elif _ts.enabled():
            log.info("[startup] turnstile enabled (注册人机验证已启用)")
    except Exception:
        log.exception("[startup] turnstile config check failed")

    # 1. MCP health loop
    try:
        import mcp_broker
        mcp_broker.start_health_loop()
    except Exception:
        pass

    # 2. command_tools + dispatcher 注册
    try:
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
        from tools_dsl.command_dispatcher import get_registry
        log.info(f"[startup] command_dispatcher: 已注册 {len(get_registry().list_all())} 个工具")
    except Exception as exc:
        log.exception("command tools registration failed: %s", exc)

    # 3. durable job 恢复 (B5)
    try:
        from platform_app import script_import
        result = script_import.recover_pending_sync_jobs()
        if result.get("recovered_pending") or result.get("reclaimed_stale"):
            log.info(
                "durable sync recovery: pending=%s stale=%s resubmitted=%s",
                result.get("recovered_pending"),
                result.get("reclaimed_stale"),
                len(result.get("resubmitted", [])),
            )
    except Exception:
        log.exception("durable sync recovery failed")

    # 3b. 僵尸 import_jobs 回收:卡死的 running 行(worker 线程挂死,finally 没跑到)
    #     本进程刚起,这些行绝无 worker 真在跑 → 既无进度更新又无 token_usage 活动的标 failed。
    try:
        from platform_app.import_pipeline import reap_zombie_import_jobs
        zres = reap_zombie_import_jobs()
        if zres.get("reaped"):
            log.warning(
                "[startup] reaped %d zombie import_jobs: %s",
                zres["reaped"], [j["job_id"] for j in zres.get("jobs", [])],
            )
    except Exception:
        log.exception("[startup] zombie import_jobs reap failed")

    # 4. 清理残留上传分片（防磁盘泄漏）
    try:
        from platform_app.script_import import cleanup_stale_upload_chunks
        n = cleanup_stale_upload_chunks(ttl_hours=24)
        if n:
            log.info("[startup] 清理 %d 个 stale upload chunks (>24h)", n)
    except Exception as e:
        log.warning("[startup] cleanup_stale_upload_chunks failed: %s", e)

    # 5. Redis 跨进程事件总线 listener(多 worker 下 SSE 事件跨 worker 投递)。
    #    Redis 未配置则 redis_listener() 立即返回,纯进程内模式不受影响。
    _redis_listener_task = None
    try:
        import asyncio as _asyncio

        import state_event_bus
        _redis_listener_task = _asyncio.create_task(state_event_bus.redis_listener())
        app.state._redis_listener_task = _redis_listener_task  # 持引用防 GC 提前回收
    except Exception:
        log.exception("[startup] redis event listener 启动失败(降级进程内)")

    # 6. RATH·搖光观测台 ticker(离线世界钟,docs/design/rath_observation_deck_v0.md)。
    #    每 60s 一次廉价 DB 扫描(无实验时零 LLM 开销);实验产物只落 kb_events + rath 表,
    #    绝不写游戏 state。双 worker 双 ticker 无害(engine 的 CAS 认领天然去重)。
    _rath_ticker_task = None

    async def _rath_ticker():
        import asyncio as _aio
        while True:
            try:
                await _aio.sleep(60)
                from rath.engine import run_due_ticks
                n = await _aio.to_thread(run_due_ticks)
                if n:
                    log.info("[rath] ticker 本轮推进 %d 个实验", n)
            except _aio.CancelledError:
                raise
            except Exception as exc:
                log.warning("[rath] ticker 轮询失败(非致命): %s", exc)

    try:
        import asyncio as _asyncio2
        _rath_ticker_task = _asyncio2.create_task(_rath_ticker())
        app.state._rath_ticker_task = _rath_ticker_task
    except Exception:
        log.exception("[startup] rath ticker 启动失败(不影响主服务)")

    yield

    # ── shutdown ──────────────────────────────────────────────────────────
    _bg_tasks = [t for t in (_redis_listener_task, _rath_ticker_task) if t is not None]
    for _t in _bg_tasks:
        try:
            _t.cancel()
        except Exception:
            pass
    if _bg_tasks:
        try:
            import asyncio as _asyncio
            # 等取消真正完成(原 fire-and-forget:cancel 后立刻往下走,监听协程的 finally
            # 清理可能没跑完连接就被下面 close_pool 抽走)。
            await _asyncio.gather(*_bg_tasks, return_exceptions=True)
        except Exception:
            pass
    # 自建默认 executor(64线程,startup 段0a)显式停机:worker 线程非 daemon,且
    # concurrent.futures 模块注册了 atexit join——只要某线程还压着 60-100s 的阻塞
    # LLM 调用,解释器正常退出就卡在 join 上(uvicorn 加 --timeout-graceful-shutdown
    # 后正常退出路径才真的会走到这里;不处理=停机挂死换个地方复现)。
    # cancel_futures 丢弃排队任务;清 _threads_queues 让 atexit 跳过 join
    # (停机语义:在跑的线程随进程退出,不再等它)。
    try:
        _exec = getattr(app.state, "default_executor", None)
        if _exec is not None:
            _exec.shutdown(wait=False, cancel_futures=True)
        import concurrent.futures.thread as _cft
        _cft._threads_queues.clear()
    except Exception:
        pass
    try:
        import mcp_broker
        mcp_broker.stop_health_loop()
        mcp_broker.stop_all()
    except Exception:
        pass

    try:
        from platform_app.db.connection import close_pool
        close_pool()
    except Exception:
        pass


# ── Exception handlers ───────────────────────────────────────────────────

async def _value_error_handler(request: Request, exc: ValueError):
    from platform_app.api._deps import json_response
    return json_response({"ok": False, "error": str(exc) or "invalid value"}, status_code=400)


async def _key_error_handler(request: Request, exc: KeyError):
    from platform_app.api._deps import json_response
    return json_response({"ok": False, "error": f"missing field: {exc}"}, status_code=400)


async def _type_error_handler(request: Request, exc: TypeError):
    from platform_app.api._deps import json_response
    msg = str(exc)
    return json_response({"ok": False, "error": f"invalid input type: {msg[:200]}"}, status_code=400)


async def _json_decode_handler(request: Request, exc: JSONDecodeError):
    from platform_app.api._deps import json_response
    return json_response({"ok": False, "error": "invalid JSON body"}, status_code=400)


async def _permission_handler(request: Request, exc: PermissionError):
    from platform_app.api._deps import json_response
    return json_response({"ok": False, "error": str(exc) or "forbidden"}, status_code=403)


async def _file_not_found_handler(request: Request, exc: FileNotFoundError):
    from platform_app.api._deps import json_response
    return json_response({"ok": False, "error": str(exc) or "not found"}, status_code=404)


async def _internal_error_handler(request: Request, exc: Exception):
    """兜底 500 handler — 避免 FastAPI/Starlette 默认行为把堆栈+SQL 泄漏给前端。

    完整 traceback 写到服务端日志（含 request_id 便于追查），返回给前端只有通用错误码。
    """
    from platform_app.api._deps import json_response
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    log.exception("unhandled exception in request %s: %s", request_id, type(exc).__name__)
    return json_response(
        {"ok": False, "error": "internal server error", "request_id": request_id, "code": "E_INTERNAL"},
        status_code=500,
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
    )


# ── Middleware ────────────────────────────────────────────────────────────

# dev 模式:RPG_ENV=dev,或部署模式为 local 家族
def _is_dev_mode() -> bool:
    """True 表示本地开发环境,放宽部分安全策略(如 CSP connect-src、cookie Secure)。"""
    rpg_env = os.getenv("RPG_ENV", "").strip().lower()
    if rpg_env == "dev":
        return True
    if rpg_env == "prod":
        return False
    from core.config import is_local_mode as _is_local_mode
    return _is_local_mode()


def _build_csp(dev: bool) -> str:
    """构建 Content-Security-Policy 策略字符串。

    dev 模式:connect-src 放宽以支持 Vite HMR ws://localhost:*。
    prod 模式:仅允许已知第三方 AI API 端点。
    """
    if dev:
        connect_src = (
            "'self' ws: wss: http://localhost:* http://127.0.0.1:* "
            "ws://localhost:* ws://127.0.0.1:* "
            "api.anthropic.com api.openai.com api.deepseek.com "
            "dashscope.aliyuncs.com ark.cn-beijing.volces.com "
            "api.minimax.chat hunyuan.tencentcloudapi.com"
        )
    else:
        # SEC(M-15): 原 prod connect-src 含裸 `https:` 等价 https://*:* → 任意 XSS 后可向任意
        # HTTPS 主机外泄。去掉通配,仅留 'self' + wss(SSE/WS)+ 已知 AI/分析主机显式白名单。
        # (浏览器不直连 LLM provider,后端代理;LLM base_url 仅在设置页作占位文本展示。)
        connect_src = (
            "'self' wss: "
            "api.anthropic.com api.openai.com api.deepseek.com "
            "dashscope.aliyuncs.com ark.cn-beijing.volces.com "
            "api.minimax.chat hunyuan.tencentcloudapi.com"
        )
    # CF orange-cloud 自动注入 beacon.min.js (RUM 数据);允许它避免 9 个 CSP error
    # 噪音(不影响功能但污染 console)。同 connect-src 加 cloudflareinsights.com
    # 让 beacon POST 也通。
    # Cloudflare Turnstile（人机验证，仅注册页在配置 sitekey 时加载）:脚本 + iframe
    # 均来自 challenges.cloudflare.com。无条件 allowlist——未启用时该域名不会被访问,
    # 加上它零副作用,启用时缺它则 widget 被 CSP 拦死。
    directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' static.cloudflareinsights.com challenges.cloudflare.com",
        "style-src 'self' 'unsafe-inline' fonts.googleapis.com",
        # data: 放行 Cloudscape 设计系统内嵌的 Open Sans woff2(base64 data URI),
        # 否则每次加载刷 8 条 font CSP 违规红字(不影响显示——界面实际用 Noto Sans SC)。
        # data: 字体不能执行脚本,风险极低;真正的 XSS 边界在 script-src/style-src。
        "font-src 'self' fonts.gstatic.com data:",
        "img-src 'self' data: https:",
        f"connect-src {connect_src} cloudflareinsights.com static.cloudflareinsights.com challenges.cloudflare.com",
        # docs.stellatrix.icu:站内帮助抽屉以 iframe 打开开源文档站的 /embed/<slug>。
        "frame-src 'self' challenges.cloudflare.com https://docs.stellatrix.icu",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    return "; ".join(directives)


# 安全 headers 默认值（HTML/static 资源加；JSON API 不强加 CSP, 避免破坏 fetch 路径）
_DEFAULT_HTML_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

# Cookie 白名单前缀（只允许 rpg_session / rpg.lang 这两种 cookie）
_ALLOWED_COOKIE_PREFIXES = ("rpg_session", "rpg.lang")


def _is_https(request: Request) -> bool:
    """判断请求是否经由 HTTPS,支持 nginx/CF 反代场景。

    仅当 RPG_TRUSTED_PROXIES 已设置时才信任 X-Forwarded-Proto,防止客户端伪造。
    """
    if request.url.scheme == "https":
        return True
    if _trusted_proxies():
        xfp = request.headers.get("x-forwarded-proto", "").lower()
        return xfp == "https"
    return False


def _harden_set_cookie(header_value: str, is_https: bool) -> str:
    """强制 Set-Cookie 头带上 Secure/HttpOnly/SameSite=Lax 属性。

    仅处理 rpg_ / rpg. 前缀的 cookie(白名单范围)。
    """
    # 解析 cookie 名
    parts = [p.strip() for p in header_value.split(";")]
    if not parts:
        return header_value
    name_value = parts[0]
    cookie_name = name_value.split("=", 1)[0].strip()
    is_allowed = any(
        cookie_name == allowed or cookie_name.startswith(allowed)
        for allowed in _ALLOWED_COOKIE_PREFIXES
    )
    if not is_allowed:
        return header_value  # 非白名单 cookie,不干预

    attrs_lower = {p.strip().lower().split("=")[0] for p in parts[1:]}
    result = list(parts)

    # HttpOnly
    if "httponly" not in attrs_lower:
        result.append("HttpOnly")
    # SameSite=Lax
    if "samesite" not in attrs_lower:
        result.append("SameSite=Lax")
    # Secure(仅 HTTPS 模式)
    if is_https and "secure" not in attrs_lower:
        result.append("Secure")
    # Max-Age=14天(1209600秒),若未设置
    if "max-age" not in attrs_lower and "expires" not in attrs_lower:
        result.append("Max-Age=1209600")

    return "; ".join(result)


async def api_contract_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id  # 让 _internal_error_handler 能拿到
    _request_id_var.set(request_id)  # 可观测性: SSE stream + to_thread 子线程均可读取

    # Request-scoped DB cache: 每个请求独立 dict,避免跨请求污染。
    # ContextVar 的 copy-on-write 语义保证不同请求互不干扰。
    from core.request_cache import reset_request_caches as _reset_caches
    _reset_caches()
    original_path = request.scope.get("path", "")
    prefix = f"/api/v{API_VERSION}"
    if original_path == prefix:
        request.scope["path"] = "/api"
    elif original_path.startswith(prefix + "/"):
        request.scope["path"] = "/api" + original_path[len(prefix):]
    if original_path.startswith("/api") and request.method in MUTATING_METHODS:
        origin = request.headers.get("origin")
        if not _origin_allowed(origin):
            from platform_app.api._deps import json_response
            return json_response(
                {"ok": False, "error": "Origin 不在允许列表", "request_id": request_id},
                status_code=403,
                headers={"X-API-Version": API_VERSION, "X-Request-ID": request_id, "Cache-Control": "no-store"},
            )
    response = await call_next(request)

    # ── GPC 确认 ─────────────────────────────────────────────────────────
    from platform_app.privacy import annotate_gpc
    annotate_gpc(request, response)

    # ── Cookie 强化 ───────────────────────────────────────────────────────
    _https = _is_https(request)
    raw_cookies = response.headers.getlist("set-cookie")
    if raw_cookies:
        # MutableHeaders 不支持 multi-value 逐条替换,需先删后加
        del response.headers["set-cookie"]
        for cookie_val in raw_cookies:
            response.headers.append("set-cookie", _harden_set_cookie(cookie_val, _https))

    if original_path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-API-Version"] = API_VERSION
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("Vary", "Origin")
    else:
        # 非 /api 路径（HTML/JS/CSS/static）默认加安全 headers
        for k, v in _DEFAULT_HTML_SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        # CSP 仅加在 HTML 路径上(非 API)
        _dev = _is_dev_mode()
        response.headers.setdefault("Content-Security-Policy", _build_csp(_dev))
        # HSTS — 反代友好,读 X-Forwarded-Proto
        if _https:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
    return response


# ── configure_app 入口 ────────────────────────────────────────────────────

def configure_app(app: FastAPI) -> None:
    """应用所有 middleware / exception_handlers 到 app 实例。

    lifespan 须在 FastAPI(lifespan=lifespan, ...) 构造时传入，不在此处注册。
    """
    # CORS — 注意: allow_credentials=True 时 allow_headers 必须明确枚举（Fetch 规范不允许 *）
    # 旧实现 allow_headers=["*"] + credentials=True 严格浏览器下静默失败
    _allowed_request_headers = [
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "X-Request-ID",
        "X-API-Version",
        "Accept",
        "Accept-Language",
        "Origin",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_origin_regex=(_LOCAL_LAN_ORIGIN_REGEX if _local_loopback_origins_allowed() else None),
        allow_credentials=_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=(["*"] if not _allow_credentials else _allowed_request_headers),
        expose_headers=["X-API-Version", "X-Request-ID"],
        max_age=_cors_max_age(),
    )

    # GZip
    app.add_middleware(GZipMiddleware, minimum_size=_gzip_min_bytes())

    # Custom middleware (后注册的先执行)
    app.middleware("http")(api_contract_middleware)

    # Exception handlers
    app.add_exception_handler(ValueError, _value_error_handler)
    app.add_exception_handler(KeyError, _key_error_handler)
    app.add_exception_handler(TypeError, _type_error_handler)
    app.add_exception_handler(JSONDecodeError, _json_decode_handler)
    app.add_exception_handler(PermissionError, _permission_handler)
    app.add_exception_handler(FileNotFoundError, _file_not_found_handler)
    # 兜底 Exception handler — 必须最后注册（具体异常优先匹配）
    app.add_exception_handler(Exception, _internal_error_handler)

    # 可观测性: 把 RequestIdFilter 挂到 root logger,让所有子 logger 日志都带 request_id
    _rid_filter = RequestIdFilter()
    logging.getLogger().addFilter(_rid_filter)
