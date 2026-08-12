"""platform_app.api.admin.logs —— 2.6 日志端点(/api/admin/logs)。纯机械搬家,行为零变化。"""
from __future__ import annotations

import os

from fastapi import Depends

from .._deps import json_response
from ._shared import _require_admin, log, router


@router.get("/api/admin/logs")
async def admin_logs(
    lines: int = 100,
    level: str = "",
    admin=Depends(_require_admin),
):
    lines = max(1, min(500, lines))
    log_file = os.environ.get("LOG_FILE", "")

    if log_file and os.path.isfile(log_file):
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            # read last lines*3 to filter, then take last `lines`
            tail = all_lines[-(lines * 3):]
            if level:
                level_up = level.upper()
                tail = [l for l in tail if level_up in l]
            tail = tail[-lines:]
            return json_response({
                "lines": [l.rstrip("\n") for l in tail],
                "total_lines": len(tail),
                "source": "file",
            })
        except Exception as exc:
            log.warning("admin_logs read error: %s", exc)

    return json_response({
        "lines": ["（日志文件路径未配置）"],
        "total_lines": 1,
        "source": "none",
    })
