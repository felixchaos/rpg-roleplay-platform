"""
tests/helpers.py — 集成测试公共工具

约定：
- 使用同一个真实数据库（DATABASE_URL），但用户名前缀 `integtest_` 隔离
- 每个 TestCase setUp/tearDown 清理本测试创建的用户和级联数据
- 不依赖外部 LLM，不触发 /api/chat
"""
from __future__ import annotations

import os
import random
import string
import sys
from pathlib import Path
from typing import Any

# 让测试能 import 顶层模块
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) != sys.path[0]:
    sys.path.insert(0, str(REPO_ROOT))

# 强制服务器鉴权模式，避免 local 模式的隐式登录干扰
os.environ.setdefault("RPG_REQUIRE_AUTH", "1")


def random_suffix(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def integtest_username() -> str:
    return f"integtest_{random_suffix()}"


def make_client():
    """构造一个 FastAPI TestClient（lazy import 防止 conftest 阶段就加载 ui）"""
    from fastapi.testclient import TestClient

    import app  # noqa: F401 触发路由注册
    return TestClient(app.app)


def cleanup_test_users() -> int:
    """删除所有 integtest_ 前缀用户（级联清掉 sessions/saves 等）。
    返回删除条数。生产环境如果用了正经测试 DB 也安全。
    """
    from platform_app.db import connect
    with connect() as db:
        row = db.execute(
            "delete from users where username like 'integtest_%' returning id"
        ).fetchall()
    return len(row)


def register_user(client, username: str | None = None, password: str = "Test12345!") -> dict[str, Any]:
    """注册并返回 (username, password, body)。"""
    uname = username or integtest_username()
    resp = client.post(
        "/api/auth/register",
        json={"username": uname, "password": password, "display_name": "integ"},
    )
    return {
        "username": uname,
        "password": password,
        "status": resp.status_code,
        "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {},
        "cookies": dict(resp.cookies),
    }


def login_user(client, username: str, password: str = "Test12345!") -> dict[str, Any]:
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    return {
        "status": resp.status_code,
        "body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {},
        "cookies": dict(resp.cookies),
    }
