"""core.outbound — SSRF 安全的统一出站 HTTP 出口。

所有「base_url 用户/admin 可控 + 携 Authorization」的裸 urllib 出站请求(extractor /
command_agent / _harness / embedding)统一收口到 `safe_urlopen`,消除散落各处、强度不一的
no-redirect opener,并补齐运行时(use-time)的 SSRF 防线。

两道防线(在写时闸 `platform_app.user_credentials._validate_base_url` 之外补强):

(a) 不跟随重定向(SEC H-4 / H-5)
    默认 urllib opener 跟随 ≤10 次 301/302。base_url 即便存入时过了 `_validate_base_url`,
    攻击者控制的端点也能用一条 30x 把携带 Authorization 的请求重定向到 169.254.169.254
    (云元数据)或内网。这里用不跟随重定向的 opener;遇到 30x 直接抛 HTTPError(fail-closed)。

(b) use-time 重解析 + IP pin(抗 DNS rebinding)
    写时闸只在「存 base_url」那一刻解析校验。攻击者可让域名写入时解析到公网、请求时再
    rebind 到内网/元数据(TOCTOU)。`safe_urlopen` 在每次发请求前**重新解析**目标 host,
    任一解析出的 IP 命中 `_ip_is_internal` 即拒;并把底层 socket **pin 到刚校验过的那个 IP**
    (Host 头 / TLS SNI / 证书校验仍用原 hostname),使「校验后再 rebind」无从下手 ——
    校验的 IP 和真正拨号的 IP 是同一个。

内网/保留地址判定复用 `platform_app.user_credentials._ip_is_internal`(单一真源,与写时闸
零漂移;十进制/八进制/十六进制/IPv4-mapped IPv6 各种伪装在 getaddrinfo 归一化后统一被拦)。
core 懒导入 platform_app 是本仓既有模式(见 core.vertex_sa / core.request_cache)。

出站代理(本地/自部署单用户模式专属,服务器模式一概不走代理):
- 凭据里配的代理 → 一律经 `credential_proxy(resolved)` 取(单一真源,服务器模式恒 None)。
  以前只有 GM 的 openai_compat 后端读它,拉模型/校验连接/子代理/生图/向量全都绕开,
  表现为「聊天能通,保存 key、同步模型却超时」(反馈 #107)。
- 凭据没配代理时,本地模式跟随环境变量与系统代理(HTTPS_PROXY / macOS 系统代理 /
  Windows 注册表),这是 2026-06 SSRF 加固前的行为:当时给 httpx 塞了自定义 transport,
  httpx 就不再读环境代理(`allow_env_proxies = trust_env and transport is None`),
  桌面版从此「浏览器能通、后端不通」。环境/系统代理里 httpx 用不了的条目(Ubuntu 导出的
  all_proxy=socks://、Windows 只配 SOCKS 时映射出的 socks4://)只跳过那一条,不让 Client
  构造失败(`_env_proxy_map`)。
- 无论显式代理还是系统代理,本机/局域网目标(127.0.0.1、localhost、私网段、*.local /
  *.internal / *.lan / *.home.arpa、单标签主机名)一律直连,本机 Ollama / LM Studio 绝不会
  被送进代理(`_is_local_target`)。
- 凭据代理是 SOCKS 时:httpx 这一侧(GM / 拉模型 / 生图提交)借 socksio 真走 SOCKS;urllib 这一侧
  (子代理 harness / extractor / 验收器 / command_agent 的 OpenAI 兼容路径 / 向量 / 生图下载)不会
  SOCKS,**不抛错**,退回环境 / 系统代理(与没配凭据代理时同一条路,也是凭据代理接进 urllib 之前的
  行为),该代理只 warning 一次(`_urllib_proxy_handler`)。以前在这里抛 UnsupportedProxy,上层大多
  吞成空结果只打日志:聊天正常,状态抽取和 /set 却静默失效。
"""
from __future__ import annotations

import http.client
import ipaddress
import logging
import re
import socket
import urllib.request
from typing import Any
from urllib.parse import urlparse

import httpx

_log = logging.getLogger(__name__)


class OutboundBlocked(ValueError):
    """目标解析到私有/本地/保留地址,出于 SSRF 防护拒绝连接。"""


class UnsupportedProxy(ValueError):
    """凭据里配的出站代理用不了,且不该悄悄退回别的路(用户明确要走这个代理)。

    抛出点:httpx 线 —— 缺 socksio 组件、代理地址写坏 / 协议不认;urllib 线 —— 协议既不是
    http/https 也不是 socks(写时闸只放行 http/https/socks5,只可能是存量脏数据)。
    urllib 线遇到 SOCKS **不**抛,见 `_urllib_proxy_handler`。
    """


def _ssrf_enforced() -> bool:
    """解析级 SSRF 拦截**仅在服务器(多租户)模式**启用。

    本地/自部署单用户模式下「用户即操作者」:指向本机大模型(Ollama/LM Studio 127.0.0.1)、
    或开着梯子(Clash fake-ip 把公网 API 域名解析成 198.18.x.x 这类保留段)都是合法用法,
    解析级 IP 拦截会误杀(用户反馈:开代理→「api 使用了保留地址」连接失败)。
    本地模式仍保留「不跟随重定向」这道结构性防线,只放开 IP 黑名单。
    取不到配置时保守=启用(fail-safe)。
    """
    try:
        from core.config import require_auth
        return bool(require_auth())
    except Exception:
        return True


def _ip_is_internal(ip_str: str) -> bool:
    """复用写时闸的内网判定(单一真源,避免逻辑漂移)。"""
    from platform_app.user_credentials import _ip_is_internal as _impl
    return _impl(ip_str)


def credential_proxy(resolved: dict[str, Any] | None) -> str | None:
    """凭据解析结果(resolve_api_key 的返回)→ 这次出站实际该用的代理 URL。单一真源。

    只有本地/自部署单用户模式(`not _ssrf_enforced()`)才返回凭据里配的 proxy;服务器
    (多租户)模式恒返回 None —— 代理 URL 合法地可以指向 127.0.0.1,无法用「禁私网」校验
    拦住,所以托管后端永远不用用户代理(与 set_credential 的写时闸构成双闸)。
    取不到配置时 `_ssrf_enforced()` 按服务器模式处理(fail-safe),同样返回 None。
    """
    if not resolved or _ssrf_enforced():
        return None
    proxy = str(resolved.get("proxy") or "").strip()
    return proxy or None


def proxy_kwargs(proxy: str | None) -> dict[str, str]:
    """有代理 → {"proxy": proxy};没有 → {}。

    给 urllib 出站点用:没配代理时调用形态与改动前逐字节相同(`safe_urlopen(req, timeout=...)`),
    不给既有调用点和测试桩平添一个参数。
    """
    return {"proxy": proxy} if proxy else {}


def redact_proxy_url(url: Any) -> str:
    """代理 URL 打日志 / 拼进报错之前去掉账号密码:http://user:pass@host:port → http://***@host:port。"""
    return re.sub(r"(://)[^/\s]*@", r"\1***@", str(url or ""))


# 环境 / 系统代理里用不了的条目只 warning 一次(每次建 client 都会重读环境,别刷屏)。
_WARNED_ENV_PROXIES: set[str] = set()


# 局域网网段:RFC1918 + CGNAT(Tailscale 等组网常用 100.64/10)+ IPv6 ULA。
# 回环 / 链路本地 / 未指定地址由 ipaddress 的属性判断,不在这里重复列。
_LAN_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7",
))


# 只在局域网 / 本机内有意义的私用域名后缀。容器里经 host.docker.internal 访问宿主机的 Ollama,
# 容器环境又设了 HTTP_PROXY 时,不列进来就会被送进代理(宿主机上的代理多半解析不了这个名字)。
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")


def _is_local_target(host: str | None) -> bool:
    """出站目标是不是本机/局域网 —— 是的话无论配没配代理都直连。

    只看 URL 里写的主机名/IP 字面量,不做 DNS 解析(梯子的 fake-ip 会把公网域名解析成
    198.18.x.x,那种必须照常走代理)。判为本地的:localhost 与 _LOCAL_SUFFIXES 里的私用
    后缀(*.local、Docker 访问宿主机用的 host.docker.internal、路由器常用的 *.lan、
    RFC 8375 的 *.home.arpa)、回环 / 链路本地 / 私网网段 IP、以及不带点的单标签主机名
    (容器服务名、局域网机器名,与 Windows 代理例外里 <local> 的语义一致)。
    """
    h = (host or "").strip().strip("[]").lower().rstrip(".")
    if not h:
        return False
    if h == "localhost" or h.endswith(_LOCAL_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        return "." not in h
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        return True
    return any(ip.version == net.version and ip in net for net in _LAN_NETWORKS)


def _urllib_proxy_handler(proxy: str) -> urllib.request.ProxyHandler:
    """凭据代理 → urllib ProxyHandler。

    - http / https 代理:照用。
    - SOCKS 代理:urllib 只会 HTTP CONNECT,不能把 socks5://… 当 HTTP 代理去连(只会得到一个
      看不懂的 Connection refused);也**不抛错** —— 写时闸放行 socks5,聊天(httpx)真能走它,
      这里抛错等于让子代理 / 状态抽取 / /set 解析在同一份配置下静默失效。退回环境 / 系统代理
      (`_urllib_env_proxy_handler`,即没配凭据代理时的那条路),每个代理只 warning 一次。
    - 其它协议:写时闸不放行,只可能是存量脏数据,抛 UnsupportedProxy 让调用方记下原因。
    """
    scheme = (urlparse(proxy).scheme or "").lower()
    if scheme.startswith("socks"):
        shown = redact_proxy_url(proxy)
        key = f"credential-socks:{shown}"
        if key not in _WARNED_ENV_PROXIES:
            _WARNED_ENV_PROXIES.add(key)
            _log.warning(
                "[outbound] 凭据里的 SOCKS 代理 %s 在 urllib 出站上用不了(子代理 / 状态抽取 / "
                "向量 / 生图下载),这些请求改走环境/系统代理,没有就直连", shown)
        return _urllib_env_proxy_handler()
    if scheme not in {"http", "https"}:
        raise UnsupportedProxy(f"代理地址协议不支持:{scheme or '(空)'}")
    return urllib.request.ProxyHandler({"http": proxy, "https": proxy})


def _urllib_env_proxy_handler() -> urllib.request.ProxyHandler:
    """没配凭据代理时 urllib 出站用的环境 / 系统代理(与 httpx 那边 `_env_proxy_map` 同口径)。

    urllib.request.getproxies() 读 HTTP(S)_PROXY、macOS 系统代理、Windows 注册表;Windows 只配了
    SOCKS 时它会给出 https=socks4://…,urllib 拿它当 HTTP 代理去 CONNECT,只会得到一个看不懂的
    连接错误。这里只留 http/https 代理(不带协议的 host:port 按 http 算),其余跳过 = 这类请求直连。
    """
    usable: dict[str, str] = {}
    for scheme, url in urllib.request.getproxies().items():
        if scheme == "no" or not url:
            continue  # 例外名单由 ProxyHandler.proxy_open 里的 proxy_bypass 自己读
        full = url if "://" in url else f"http://{url}"
        if (urlparse(full).scheme or "").lower() in {"http", "https"}:
            usable[scheme] = url
            continue
        key = f"urllib:{scheme}={url}"
        if key not in _WARNED_ENV_PROXIES:
            _WARNED_ENV_PROXIES.add(key)
            _log.warning("[outbound] 环境/系统代理 %s → %s 这条出站用不了,这一条不走代理",
                         scheme, redact_proxy_url(url))
    return urllib.request.ProxyHandler(usable)


def _resolve_external_ip(host: str, port: int) -> str:
    """解析 host 的所有 A/AAAA,任一为内网/保留即拒;返回首个已校验的公网 IP 供 pin。

    与 `_validate_base_url` 同样的「全部解析结果都必须是公网」语义 —— 不是只看第一条。
    """
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise OutboundBlocked(f"出站目标无法解析:{host}") from exc
    if not infos:
        raise OutboundBlocked(f"出站目标无 A/AAAA 记录:{host}")
    pinned: str | None = None
    for info in infos:
        ip_str = info[4][0]
        if _ip_is_internal(ip_str):
            raise OutboundBlocked(
                f"出站目标解析到私有/本地/保留地址,已拒绝(防 SSRF/DNS rebinding):"
                f"{host} → {ip_str}"
            )
        if pinned is None:
            pinned = ip_str
    if pinned is None:
        raise OutboundBlocked(f"出站目标解析后无有效公网 IP:{host}")
    return pinned


def _pinned_http_connection(pinned_ip: str):
    """返回一个 HTTPConnection 子类,connect() 拨号固定到已校验的 pinned_ip。"""

    class _PinnedHTTPConnection(http.client.HTTPConnection):
        def connect(self):  # noqa: D401
            self.sock = socket.create_connection(
                (pinned_ip, self.port), self.timeout, self.source_address
            )
            if self._tunnel_host:
                self._tunnel()

    return _PinnedHTTPConnection


def _pinned_https_connection(pinned_ip: str):
    """同上,HTTPS 版:拨号到 pinned_ip,但 SNI/证书校验仍用原 hostname(self.host)。"""

    class _PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):  # noqa: D401
            sock = socket.create_connection(
                (pinned_ip, self.port), self.timeout, self.source_address
            )
            if self._tunnel_host:
                self.sock = sock
                self._tunnel()
                server_hostname = self._tunnel_host
            else:
                server_hostname = self.host
            # self._context.check_hostname 默认 True → 证书按原 hostname 校验(非 pinned IP)
            self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)

    return _PinnedHTTPSConnection


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """拒绝跟随任何 30x:redirect_request 返 None → urllib 不发起重定向请求。"""

    def redirect_request(self, *args, **kwargs):
        return None


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, pinned_ip: str):
        super().__init__()
        self._conn_class = _pinned_http_connection(pinned_ip)

    def http_open(self, req):
        return self.do_open(self._conn_class, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned_ip: str):
        super().__init__()
        self._conn_class = _pinned_https_connection(pinned_ip)

    def https_open(self, req):
        # 只透传 context(默认 SSLContext,check_hostname=True);不传 check_hostname kwarg
        # —— Python 3.12+ 的 HTTPSConnection 已移除该形参。
        return self.do_open(self._conn_class, req, context=self._context)


def safe_urlopen(req, *, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, proxy: str | None = None):
    """SSRF 安全地打开一个 urllib Request(或 URL 字符串)。

    - 不跟随重定向(30x → 抛 urllib.error.HTTPError,fail-closed)。
    - 发请求前重解析目标 host,任一 IP 内网/保留即抛 OutboundBlocked。
    - socket 拨号 pin 到已校验 IP(抗 DNS rebinding),Host/SNI/证书仍用原 hostname。

    仅支持 http/https。timeout 语义与 urllib.request.urlopen 一致。

    proxy:凭据里配的出站代理,调用方一律传 `credential_proxy(...)` 的结果(经 proxy_kwargs 展开)。
    契约(本地模式;服务器模式走 IP pin,不经任何代理,传了也忽略):
      - 本机/局域网目标:一律直连,不看 proxy 也不看环境代理;
      - http/https 代理:经它出去;
      - SOCKS 代理:urllib 不会 SOCKS,**不抛错**,退回环境/系统代理(没有就直连),每个代理只
        warning 一次 —— 与没传 proxy 时同一条路;
      - 其它协议(写时闸不放行的存量脏数据):抛 UnsupportedProxy;
      - 没传:环境/系统代理(只留 urllib 用得了的 http/https 条目)。
    """
    full_url = req.full_url if isinstance(req, urllib.request.Request) else req
    parsed = urlparse(full_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise OutboundBlocked(f"出站仅允许 http/https:{scheme or '(空)'}")
    host = parsed.hostname
    if not host:
        raise OutboundBlocked("出站目标缺少 host")
    port = parsed.port or (443 if scheme == "https" else 80)

    # urllib 默认 UA(Python-urllib/x.y)会被部分 provider 网关 WAF 直接 403(实测 opencode.ai/zen
    # 对 Python-urllib 403、对常规 UA 200;某些挂 Cloudflare 的自建中转站亦然 —— 见 core.outbound_ua)。
    # 本仓 httpx 出站(GM/SDK)早已统一覆盖 UA 故能通,但 urllib 出站(_harness 子代理 / extractor /
    # embedding / 生图下载)此前漏了 → 出现「同一 key 同一模型,GM 200 / 子代理 harness 403」。
    # 这里收口到同一 UA 真源 outbound_user_agent();调用方已显式设 UA 的尊重不动。
    if isinstance(req, urllib.request.Request) and not req.has_header("User-agent"):
        from core.outbound_ua import outbound_user_agent
        req.add_header("User-Agent", outbound_user_agent())

    # 服务器模式:重解析 + IP pin(抗 rebinding);本地/自部署模式:不做 IP 拦截/pin
    # (允许本机大模型 / 梯子 fake-ip),但仍保留不跟随重定向。
    if _ssrf_enforced():
        pinned_ip = _resolve_external_ip(host, port)
        opener = urllib.request.build_opener(
            _PinnedHTTPHandler(pinned_ip),
            _PinnedHTTPSHandler(pinned_ip),
            _NoRedirect(),
        )
    elif _is_local_target(host):
        # 本机/局域网模型:空 ProxyHandler = 不走任何代理(显式的、环境的、系统的都不走)。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    elif proxy:
        opener = urllib.request.build_opener(_urllib_proxy_handler(proxy), _NoRedirect())
    else:
        # 没配代理:读环境变量 / 系统代理(与 build_opener 默认的 ProxyHandler 同源),只留 urllib
        # 用得了的 http/https 代理;NO_PROXY / 系统例外仍由 urllib 的 proxy_bypass 处理。
        opener = urllib.request.build_opener(_urllib_env_proxy_handler(), _NoRedirect())
    return opener.open(req, timeout=timeout)


# 单次下载体积上限(防内网响应当「图片」被无限抓回 + 放大攻击)。生图返回的图通常 < 几 MB。
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


def safe_get_bytes(
    url: str,
    *,
    timeout: float = 60.0,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
    max_redirects: int = 3,
    proxy: str | None = None,
) -> bytes:
    """SSRF 安全地 GET 一个 URL 的字节(给生图 download_url 等用)。

    与 `safe_urlopen` 同源防线(不跟随重定向 + use-time 重解析 + pin 已校验 IP),但:
    - **手动**跟随 ≤max_redirects 次重定向,**每一跳都重新走 safe_urlopen**(即每跳都重解析 +
      私网校验 + pin),既兼容 CDN 合法 302,又杜绝「公网 200 → 302 内网」与 DNS rebinding。
    - 限制响应体大小,防把内网/元数据响应当图片无限抓回。

    URL 来自 provider 响应(攻击者可控),从不经写时 `_validate_base_url`,故这里是唯一硬防线。

    proxy:与 safe_urlopen 同一契约,每一跳都带上。生图下载必须传该任务凭据的
    `credential_proxy(...)`(提交走了代理、下载不走,需要代理的图片域名就会超时);
    不需要代理的调用点显式写 proxy=None(守卫 test_outbound_proxy_parity)。
    """
    import urllib.error
    import urllib.request
    from urllib.parse import urljoin

    current = url
    for _hop in range(max_redirects + 1):
        req = urllib.request.Request(current, method="GET")
        try:
            with safe_urlopen(req, timeout=timeout, **proxy_kwargs(proxy)) as resp:
                data = resp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise OutboundBlocked(f"下载体积超限(> {max_bytes} bytes):{current}")
                return data
        except urllib.error.HTTPError as exc:
            # _NoRedirect 把 30x 变成 HTTPError;手动跟随并对下一跳重新校验(safe_urlopen 内做)。
            if exc.code in (301, 302, 303, 307, 308):
                loc = exc.headers.get("Location") if exc.headers else None
                if not loc:
                    raise
                current = urljoin(current, loc)
                continue
            raise
    raise OutboundBlocked(f"重定向次数超限(> {max_redirects}):{url}")


class OutboundBlockedTransportError(httpx.ConnectError, OutboundBlocked):
    """httpx 传输层里的 SSRF 拒绝:同时是 httpx.ConnectError 和 OutboundBlocked。

    httpx 的约定是 transport 只抛 TransportError。以前 _SsrfGuardTransport 直接抛
    OutboundBlocked(ValueError),openai<3.14 靠 `except Exception` 兜着把它包成
    APIConnectionError 并重试;openai 3.14.1 起改成只包 httpx.RequestError,裸 ValueError
    原样穿出去,分类器认不出、SDK 也不重试(服务器模式 DNS 瞬时失败直接报错)。
    改成 ConnectError 子类后:SDK 照常包成 APIConnectionError(__cause__ 是本异常)并重试,
    分类回到 network;`except OutboundBlocked` / `except ValueError` 的既有写法也照样接得住。
    """


class _SsrfGuardTransport:
    """httpx 传输层 SSRF 闸:发请求前对目标 host 重解析,任一 IP 内网/保留即拒。

    供 OpenAI / Anthropic SDK 等**必须用 httpx**(safe_urlopen 是 urllib,覆盖不到)的出站点用。
    配合 `follow_redirects=False`(在 safe_httpx_client 里设)即可挡住「302 → 内网」与裸打内网;
    use-time 重解析缓解 DNS rebinding(httpx 不便像 urllib 那样 pin socket,故此处为校验而非 pin,
    残余 TOCTOU 窗口极小,且写时闸 + 不跟随重定向已覆盖主要攻击面)。
    拒绝一律抛 OutboundBlockedTransportError(httpx transport 契约,见该类注释)。
    """

    def __init__(self, inner):
        self._inner = inner

    def handle_request(self, request):
        try:
            host = request.url.host
            scheme = (request.url.scheme or "").lower()
            if scheme not in {"http", "https"}:
                raise OutboundBlocked(f"出站仅允许 http/https:{scheme or '(空)'}")
            if not host:
                raise OutboundBlocked("出站目标缺少 host")
            port = request.url.port or (443 if scheme == "https" else 80)
            # 服务器模式才做内网拦截;本地/自部署模式放行(本机大模型 / 梯子 fake-ip)。
            if _ssrf_enforced():
                _resolve_external_ip(host, port)  # 内网即抛 OutboundBlocked(fail-closed)
        except OutboundBlocked as exc:
            raise OutboundBlockedTransportError(str(exc), request=request) from exc
        return self._inner.handle_request(request)

    def close(self):
        self._inner.close()

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *a):
        self._inner.__exit__(*a)


def _socksio_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("socksio") is not None


def _env_proxy_map() -> dict[str, Any]:
    """环境变量 / 系统代理 → httpx 的 {URL 模式: Proxy | None},只留 httpx 真用得了的条目。

    与 httpx 自己读环境代理的口径相同(同一个 get_environment_proxies:HTTP(S)_PROXY / ALL_PROXY /
    NO_PROXY,macOS 系统代理,Windows 注册表),区别只在于:某一条 httpx 用不了时,跳过这一条并
    记一次 warning,而不是让整个 Client 构造失败。常见的用不了:
    - Ubuntu / GNOME 设了系统代理(Clash 常见)会导出 all_proxy=socks://…,Windows 注册表只配了
      SOCKS 时 Python 会映射成 socks4://… —— httpx 只认 http / https / socks5 / socks5h,构造时抛
      ValueError("Unknown scheme for proxy URL");
    - 地址写坏(端口不是数字等)抛 InvalidURL;
    - socks5 代理但没装 socksio,建传输层时抛 ImportError。
    以前任何一条这样的环境变量都会让本地模式所有 httpx 出站(GM / 拉模型 / 生图 / 本机 Ollama)在
    构造时就崩掉;现在只是这一条不走代理,同一环境里能用的 https_proxy 照常生效。
    """
    import httpx
    from httpx._utils import get_environment_proxies

    out: dict[str, Any] = {}
    for pattern, url in get_environment_proxies().items():
        if url is None:
            out[pattern] = None  # NO_PROXY 例外 = 直连
            continue
        reason = ""
        proxy: Any = None
        try:
            proxy = httpx.Proxy(url=url)
        except Exception as exc:  # noqa: BLE001 —— 协议不认(ValueError)/ 地址写坏(InvalidURL)
            reason = str(exc)
        else:
            if proxy.url.scheme in ("socks5", "socks5h") and not _socksio_available():
                proxy, reason = None, "缺少 SOCKS 代理组件 socksio"
        if proxy is None:
            key = f"{pattern}={url}"
            if key not in _WARNED_ENV_PROXIES:
                _WARNED_ENV_PROXIES.add(key)
                _log.warning(
                    "[outbound] 环境/系统代理 %s → %s 用不了(%s),这一条不走代理",
                    pattern, redact_proxy_url(url), reason,
                )
            continue
        out[pattern] = proxy
    return out


_LOCAL_CLIENT_CLS: Any = None


def _local_client_cls():
    """本地模式用的 httpx.Client 子类,改了 httpx 的两个内部钩子:

    - `_transport_for_url`:本机/局域网目标永远走直连连接池。httpx 的环境代理例外只认 NO_PROXY,
      且 URL 匹配不支持网段(10.0.0.0/8 这种写不进去),也不认 macOS 系统例外 / Windows
      ProxyOverride 里的 <local>。所以在「按 URL 挑传输层」这一步先拦一道:本地目标返回默认
      传输层(= 直连),其余交回 httpx 原逻辑(显式代理 / 环境代理 / NO_PROXY)。
    - `_get_proxy_map`:读环境代理时改用 `_env_proxy_map`,跳过 httpx 用不了的条目,不让一条
      socks:// 环境变量把整个 Client 构造弄崩。显式代理仍走 httpx 原逻辑。
    test_outbound_local_proxy 锁着这两个钩子名,httpx 升级改了名字会立刻红。
    """
    global _LOCAL_CLIENT_CLS
    if _LOCAL_CLIENT_CLS is None:
        import httpx

        class _LocalModeClient(httpx.Client):
            def _transport_for_url(self, url):  # noqa: D401
                if _is_local_target(url.host):
                    return self._transport
                return super()._transport_for_url(url)

            def _get_proxy_map(self, proxy, allow_env_proxies):  # noqa: D401
                if proxy is None and allow_env_proxies:
                    return _env_proxy_map()
                return super()._get_proxy_map(proxy, allow_env_proxies)

        _LOCAL_CLIENT_CLS = _LocalModeClient
    return _LOCAL_CLIENT_CLS


def _local_httpx_client(*, timeout: float, proxy: str | None, http2: bool):
    """本地/自部署单用户模式的出站 client。

    不挂 SSRF 守卫 transport(本地模式那道守卫本来就是空操作),这样 httpx 才会:
    显式 proxy 走显式 proxy;没有时按 trust_env 读环境变量和系统代理。本机/局域网目标直连。
    """
    import httpx

    kwargs: dict[str, Any] = {
        "follow_redirects": False,
        "timeout": httpx.Timeout(timeout, connect=10.0),
        "http2": http2,
    }
    cls = _local_client_cls()
    try:
        return cls(proxy=proxy or None, trust_env=True, **kwargs)
    except Exception as exc:  # noqa: BLE001 —— 分两种来源各自处理,见下
        if proxy:
            # 显式凭据代理:给用户一句能照着改的话,不退回直连(用户明确要走代理)。
            if isinstance(exc, ImportError):
                # SOCKS 代理需要 socksio(requirements 已带);老安装包可能还缺它。
                raise UnsupportedProxy(
                    "当前安装缺少 SOCKS 代理支持组件,用不了 socks5:// 代理。请把连接方式里的代理"
                    "改成 HTTP 代理(形如 http://127.0.0.1:7890),或更新到最新版本后再试。"
                ) from exc
            if isinstance(exc, (ValueError, httpx.InvalidURL)):
                raise UnsupportedProxy(
                    f"连接方式里填的代理地址用不了:{redact_proxy_url(proxy)}。请改成形如 "
                    "http://127.0.0.1:7890 的 HTTP 代理地址(或 socks5://127.0.0.1:1080)。"
                ) from exc
            raise
        # 代理来自环境变量 / 系统设置:用不了的条目 _env_proxy_map 已经跳过了,走到这里说明还有
        # 别的问题(比如 httpx 升级改了内部接口)。退回直连,别让整条出站崩掉。
        _log.warning("[outbound] 按环境/系统代理建出站 client 失败(%s),本次出站改为直连", exc)
        return cls(proxy=None, trust_env=False, **kwargs)


def safe_httpx_client(*, timeout: float = 30.0, proxy: str | None = None, http2: bool = True):
    """返回一个 SSRF 安全的 httpx.Client:不跟随重定向 + 传输层 use-time 私网校验(缓解 DNS rebinding)。

    用于把 user/admin 可控 base_url 喂给 OpenAI 兼容 SDK 的出站点(model_probe 拉模型、
    gm/backends/openai_compat.py 的 GM LLM 调用)。传输层守卫只在服务器模式挂上(私网拦截);
    本地/自部署模式不挂守卫,改走 `_local_httpx_client`(显式代理 / 系统代理 / 本地目标直连)。

    proxy:调用方一律传 `credential_proxy(...)` 的结果,不需要代理的写 proxy=None(守卫测试
    要求每个调用点显式写出 proxy=)。服务器模式下 credential_proxy 恒为 None,托管多用户后端
    永不走用户代理(防 SSRF —— 代理可合法指向内网,无法用「禁私网」拦)。

    http2(默认 True):开 HTTP/2。一个 GM run 内会发多个 LLM 调用(推理 + 工具轮),都走 stream=True;
    OpenAI/Anthropic SDK 的流式响应到 [DONE] 即停、不 drain body → HTTP/1.1 下 httpx 无法把 socket
    归还连接池 → 每次调用都重新 TCP+TLS 握手(×N 握手开销,见社区反馈)。HTTP/2 把每个调用变成同一
    连接上的一条 stream(关 stream ≠ 关 connection),故即便 SDK 不 drain 也复用同一连接 → run 内
    持久连接、省掉 ×N 握手。provider 不支持 h2 时 ALPN 自动回退 HTTP/1.1(行为同现状,无害)。
    安全不变:_SsrfGuardTransport 仍每请求重解析校验;复用的 h2 连接已 pin 到首次校验过的公网 IP,
    后续 stream 不再重解析 → DNS rebinding 对已建连接无效(与「不复用就每次新建」相比反而更少新解析)。
    需 `h2` 包(httpx[http2]);缺包时 httpx 在建 h2 连接时报错,故仅在装了 h2 时开启,否则退回 1.1。
    """
    import httpx

    _h2 = bool(http2)
    if _h2:
        try:
            import h2  # noqa: F401  # 仅探测是否可用
        except Exception:
            _h2 = False  # 没装 h2 包 → 退回 HTTP/1.1(不报错)
    if not _ssrf_enforced():
        return _local_httpx_client(timeout=timeout, proxy=proxy, http2=_h2)
    inner = (
        httpx.HTTPTransport(proxy=proxy, http2=_h2) if proxy
        else httpx.HTTPTransport(http2=_h2)
    )
    return httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout, connect=10.0),
        transport=_SsrfGuardTransport(inner),
    )
