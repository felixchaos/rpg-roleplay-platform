"""
test_credentials_save_nonblocking.py
====================================

反馈 #107:保存 API key(POST /api/me/credentials)会在落库后内联拉一次远程模型列表,
「同步模型」(POST /api/models/remote/sync)和 GET /api/models/remote 也是网络请求 ——
以前都在 async 路由里同步调用,打不通的端点把事件循环整个冻住。桌面版单 worker,
于是整个后端卡死,顶栏掉成「未登录」,连本来 2 秒就能配好的 DeepSeek 也保存超时。

锁死:网络调用放进线程后,list_remote_models 被桩成睡 3 秒时,保存 / 同步请求进行中
并发的 GET /api/health 仍在 1 秒内返回。走真的 ASGI 栈(httpx.ASGITransport),
set_credential 用真实实现(只把 DB 连接换成内存桩),验的是真实调用链。
"""
from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI

_SLOW = 3.0


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDb:
    def execute(self, sql, params=None):
        return _FakeResult({
            "id": 1, "user_id": 7, "api_id": "openai", "base_url_override": "",
            "enabled": True, "auth_mode": "api_key", "updated_at": "2026-09-28",
        })


@contextmanager
def _fake_connect():
    yield _FakeDb()


def _slow_list_remote_models(*a, **k):
    time.sleep(_SLOW)  # 模拟打不通的端点:同步阻塞
    return {"ok": False, "error": "timeout", "models": []}


@pytest.fixture()
def app(monkeypatch):
    import model_probe
    from platform_app import user_credentials, user_models
    from platform_app.api._deps import require_user
    from platform_app.api.me import router as me_router
    from routes._deps_fastapi import get_current_user
    from routes.models import router as models_router

    monkeypatch.setattr(user_credentials, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(user_credentials, "connect", _fake_connect)
    monkeypatch.setattr(user_credentials, "encrypt_api_key", lambda *a, **k: b"cipher")
    monkeypatch.setattr(user_credentials, "get_credential", lambda *a, **k: None)
    monkeypatch.setattr(user_credentials, "_validate_base_url", lambda *a, **k: None)
    monkeypatch.setattr(model_probe, "list_remote_models", _slow_list_remote_models)
    monkeypatch.setattr(user_models, "replace_synced_models", lambda *a, **k: None)
    import model_registry
    monkeypatch.setattr(model_registry, "load_model_catalog", lambda *a, **k: {"apis": [
        {"id": "openai", "kind": "openai_compat", "base_url": "https://api.openai.com/v1",
         "enabled": True, "models": []},
    ]})
    monkeypatch.setattr(model_registry, "load_catalog_for_user", lambda *a, **k: {"apis": []})

    fa = FastAPI()
    fa.include_router(me_router)
    fa.include_router(models_router)

    @fa.get("/api/health")
    async def _health():
        return {"ok": True}

    admin = {"id": 7, "role": "admin", "username": "t"}
    fa.dependency_overrides[require_user] = lambda: admin
    fa.dependency_overrides[get_current_user] = lambda: admin
    return fa


async def _health_latency_during(app, method: str, path: str, **kw) -> tuple[float, bool, float, int]:
    """返回 (从发起慢请求到 health 返回的总耗时, health 返回时慢请求是否仍在进行, 慢请求耗时, 状态码)。

    必须从慢请求发起时刻计时:事件循环被冻住时,连本协程的 sleep 定时器都醒不来,
    只量 health 自身的往返会在冻结结束后才开始计时,量出来永远很快(假绿)。
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t", timeout=30) as c:
        t0 = time.monotonic()
        slow = asyncio.create_task(c.request(method, path, **kw))
        await asyncio.sleep(0.3)  # 让慢请求先进到网络调用里
        r = await c.get("/api/health")
        health_done = time.monotonic() - t0
        slow_in_flight = not slow.done()
        assert r.status_code == 200
        resp = await slow
        return health_done, slow_in_flight, time.monotonic() - t0, resp.status_code


@pytest.mark.parametrize("method,path,kw", [
    ("POST", "/api/me/credentials", {"json": {"api_id": "openai", "api_key": "sk-test"}}),
    ("POST", "/api/models/remote/sync", {"json": {"api_id": "openai"}}),
    ("GET", "/api/models/remote?api_id=openai&refresh=1", {}),
])
def test_health_stays_responsive_while_probe_blocks(app, method, path, kw):
    health_done, slow_in_flight, slow_s, status = asyncio.run(
        _health_latency_during(app, method, path, **kw))
    assert status == 200
    assert slow_s >= _SLOW - 0.5, "桩没生效:慢请求没有真的走到 list_remote_models"
    # 0.3s 的起步间隔 + 1s 预算
    assert health_done < 1.3 and slow_in_flight, (
        f"探测进行中 /api/health 到 {health_done:.2f}s 才返回 —— 事件循环被同步网络调用冻住了")
