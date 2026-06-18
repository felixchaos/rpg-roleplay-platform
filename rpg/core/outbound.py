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
"""
from __future__ import annotations

import http.client
import socket
import urllib.request
from urllib.parse import urlparse


class OutboundBlocked(ValueError):
    """目标解析到私有/本地/保留地址,出于 SSRF 防护拒绝连接。"""


# 已知安全域名白名单：这些域名即使解析到保留 IP 段（如 198.18.0.0/15）也允许连接。
# 用于兼容 CDN / Anycast 等场景（如 api.x.ai 解析到 198.18.0.x）。
_SSRF_HOST_ALLOWLIST: frozenset[str] = frozenset({
    "api.x.ai",
    "api.openai.com",
    "api.anthropic.com",
    "api.deepseek.com",
    "generativelanguage.googleapis.com",
    "api.hunyuan.cloud.tencent.com",
    "chat.d.xiaomi.net",
})


def _ip_is_internal(ip_str: str) -> bool:
    """复用写时闸的内网判定(单一真源,避免逻辑漂移)。"""
    from platform_app.user_credentials import _ip_is_internal as _impl
    return _impl(ip_str)


def _resolve_external_ip(host: str, port: int) -> str:
    """解析 host 的所有 A/AAAA,任一为内网/保留即拒;返回首个已校验的公网 IP 供 pin。

    与 `_validate_base_url` 同样的「全部解析结果都必须是公网」语义 —— 不是只看第一条。
    已知安全域名（_SSRF_HOST_ALLOWLIST）跳过内网检查，兼容 CDN/Anycast 场景。
    """
    # 白名单域名跳过 SSRF 检查（如 api.x.ai 解析到 198.18.0.x 保留段）
    if host in _SSRF_HOST_ALLOWLIST:
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise OutboundBlocked(f"出站目标无法解析:{host}") from exc
        if not infos:
            raise OutboundBlocked(f"出站目标无 A/AAAA 记录:{host}")
        return infos[0][4][0]

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
    assert pinned is not None  # 上面非空且全过校验 → 必有值
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


def safe_urlopen(req, *, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
    """SSRF 安全地打开一个 urllib Request(或 URL 字符串)。

    - 不跟随重定向(30x → 抛 urllib.error.HTTPError,fail-closed)。
    - 发请求前重解析目标 host,任一 IP 内网/保留即抛 OutboundBlocked。
    - socket 拨号 pin 到已校验 IP(抗 DNS rebinding),Host/SNI/证书仍用原 hostname。

    仅支持 http/https。timeout 语义与 urllib.request.urlopen 一致。
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

    pinned_ip = _resolve_external_ip(host, port)

    opener = urllib.request.build_opener(
        _PinnedHTTPHandler(pinned_ip),
        _PinnedHTTPSHandler(pinned_ip),
        _NoRedirect(),
    )
    return opener.open(req, timeout=timeout)


# 单次下载体积上限(防内网响应当「图片」被无限抓回 + 放大攻击)。生图返回的图通常 < 几 MB。
_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024


def safe_get_bytes(
    url: str,
    *,
    timeout: float = 60.0,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
    max_redirects: int = 3,
) -> bytes:
    """SSRF 安全地 GET 一个 URL 的字节(给生图 download_url 等用)。

    与 `safe_urlopen` 同源防线(不跟随重定向 + use-time 重解析 + pin 已校验 IP),但:
    - **手动**跟随 ≤max_redirects 次重定向,**每一跳都重新走 safe_urlopen**(即每跳都重解析 +
      私网校验 + pin),既兼容 CDN 合法 302,又杜绝「公网 200 → 302 内网」与 DNS rebinding。
    - 限制响应体大小,防把内网/元数据响应当图片无限抓回。

    URL 来自 provider 响应(攻击者可控),从不经写时 `_validate_base_url`,故这里是唯一硬防线。
    """
    import urllib.error
    import urllib.request
    from urllib.parse import urljoin

    current = url
    for _hop in range(max_redirects + 1):
        req = urllib.request.Request(current, method="GET")
        try:
            with safe_urlopen(req, timeout=timeout) as resp:
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


class _SsrfGuardTransport:
    """httpx 传输层 SSRF 闸:发请求前对目标 host 重解析,任一 IP 内网/保留即拒。

    供 OpenAI / Anthropic SDK 等**必须用 httpx**(safe_urlopen 是 urllib,覆盖不到)的出站点用。
    配合 `follow_redirects=False`(在 safe_httpx_client 里设)即可挡住「302 → 内网」与裸打内网;
    use-time 重解析缓解 DNS rebinding(httpx 不便像 urllib 那样 pin socket,故此处为校验而非 pin,
    残余 TOCTOU 窗口极小,且写时闸 + 不跟随重定向已覆盖主要攻击面)。
    """

    def __init__(self, inner):
        self._inner = inner

    def handle_request(self, request):
        host = request.url.host
        scheme = (request.url.scheme or "").lower()
        if scheme not in {"http", "https"}:
            raise OutboundBlocked(f"出站仅允许 http/https:{scheme or '(空)'}")
        if not host:
            raise OutboundBlocked("出站目标缺少 host")
        port = request.url.port or (443 if scheme == "https" else 80)
        _resolve_external_ip(host, port)  # 内网即抛 OutboundBlocked(fail-closed)
        return self._inner.handle_request(request)

    def close(self):
        self._inner.close()

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *a):
        self._inner.__exit__(*a)


def safe_httpx_client(*, timeout: float = 30.0):
    """返回一个 SSRF 安全的 httpx.Client:不跟随重定向 + 传输层私网校验。

    用于把 user/admin 可控 base_url 喂给 OpenAI 兼容 SDK 的出站点(如 model_probe 拉模型),
    与 gm/backends/openai_compat.py 的 `http_client=httpx.Client(follow_redirects=False)` 一致,
    并额外加传输层私网校验。
    """
    import httpx

    return httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout, connect=10.0),
        transport=_SsrfGuardTransport(httpx.HTTPTransport()),
    )
