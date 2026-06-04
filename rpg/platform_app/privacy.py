"""platform_app.privacy — GPC (Global Privacy Control) 处理工具。

我们不出售、不共享用户数据。当浏览器发送 Sec-GPC: 1 时,
我们在响应中回写 X-GPC-Acknowledged: 1 以表明已收到并遵守。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response


def parse_gpc(request: Request) -> bool:
    """返回 True 如果请求携带 Sec-GPC: 1 头。"""
    return request.headers.get("sec-gpc") == "1"


def annotate_gpc(request: Request, response: Response) -> None:
    """若请求携带 GPC 信号,在 response 中回写确认头。

    X-GPC-Acknowledged: 1 告知客户端:我们已收到 GPC 请求且默认不出售/共享数据。
    """
    if parse_gpc(request):
        response.headers["x-gpc-acknowledged"] = "1"
