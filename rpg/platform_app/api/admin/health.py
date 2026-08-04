"""platform_app.api.admin.health —— 2.5 系统健康端点(/api/admin/health)。纯机械搬家,行为零变化。"""
from __future__ import annotations

import os
import sys
import time

from fastapi import Depends

from ...db import connect
from .._deps import json_response
from ._shared import _require_admin, log, router


@router.get("/api/admin/health")
async def admin_health(admin=Depends(_require_admin)):
    # DB latency
    db_ok = False
    db_latency_ms = 0.0
    db_pool_size = 0
    db_pool_idle = 0
    try:
        t0 = time.perf_counter()
        with connect() as db:
            db.execute("select 1").fetchone()
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        db_ok = True

        # try to get pool stats
        try:
            from platform_app.db import get_pool
            pool = get_pool()
            if pool is not None:
                db_pool_size = pool.get_stats().get("pool_size", 0)
                db_pool_idle = pool.get_stats().get("pool_available", 0)
        except Exception:
            pass
    except Exception as exc:
        log.warning("health check db error: %s", exc)

    # process info
    pid = os.getpid()
    memory_rss_mb = None
    uptime_s = None

    try:
        import psutil
        proc = psutil.Process(pid)
        memory_rss_mb = round(proc.memory_info().rss / (1024 * 1024), 2)
        uptime_s = round(time.time() - proc.create_time(), 1)
    except ImportError:
        pass
    except Exception:
        pass

    # disk
    disk_free_gb = 0.0
    disk_total_gb = 0.0
    disk_percent_used = 0.0
    try:
        st = os.statvfs("/")
        disk_total_gb = round(st.f_frsize * st.f_blocks / (1024 ** 3), 2)
        disk_free_gb = round(st.f_frsize * st.f_bavail / (1024 ** 3), 2)
        disk_percent_used = round((1 - st.f_bavail / st.f_blocks) * 100, 1) if st.f_blocks else 0.0
    except Exception:
        pass

    overall_ok = db_ok

    return json_response({
        "db": {
            "ok": db_ok,
            "latency_ms": db_latency_ms,
            "pool_size": db_pool_size,
            "pool_idle": db_pool_idle,
        },
        "process": {
            "pid": pid,
            "uptime_s": uptime_s,
            "memory_rss_mb": memory_rss_mb,
        },
        "disk": {
            "free_gb": disk_free_gb,
            "total_gb": disk_total_gb,
            "percent_used": disk_percent_used,
        },
        "python_version": sys.version,
        "ok": overall_ok,
    })
