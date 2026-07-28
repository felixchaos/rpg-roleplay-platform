"""test_manual_model_is_per_user.py — 用户手填的模型只属于他自己,且不被同步冲掉。

前科(v1.76.0,已整条回滚):「添加模型」按钮的确认回调接了 `POST /api/models/model`
(`model_registry.upsert_model`)—— 那是 **admin-only 的全局 catalog 写入**。普通用户点下去
直接撞「需要管理员权限」;更糟的是,一旦写成功,用户自己的私人模型会出现在**所有人**的
模型列表里。用户自己的模型必须写 per-user overlay(`user_model_entries`)。

第二个坑:`replace_synced_models` 是覆盖语义(先 delete 整份再重写)。手填的模型若不加区分,
用户每点一次「拉取模型」就把自己填的清空一次 —— 而正是那些**没有 /models 接口**的
provider(火山方舟 Agent Plan 订阅套餐恒 404)才需要手填。故加 `source` 列区分。
"""
from __future__ import annotations

import pathlib


import pytest

import platform_app.user_models as um
from platform_app.db import migrations as _mig

_SRC = pathlib.Path(um.__file__).read_text(encoding="utf-8")
_ROUTES = pathlib.Path(
    pathlib.Path(um.__file__).parent / "frontend_routes" / "models.py").read_text(encoding="utf-8")
_FE = pathlib.Path(
    pathlib.Path(um.__file__).parents[2] / "frontend" / "src" / "components" / "settings"
    / "models-section.jsx").read_text(encoding="utf-8")


# ── 写入面必须是 per-user,不是全局 ────────────────────────────────────────
def test_manual_upsert_writes_user_scoped_table():
    assert hasattr(um, "upsert_manual_model")
    body = _SRC[_SRC.index("def upsert_manual_model"):]
    body = body[: body.index("\ndef ", 1)]
    assert "user_model_entries" in body, "手填模型没写 per-user overlay"
    assert "user_id" in body
    assert "'manual'" in body, "没标 source='manual' → 下次同步会被清掉"


def _called_names(func_name: str) -> set[str]:
    """AST 取该函数体内**真实调用**的名字 —— 不能用字符串匹配,
    否则文档字符串里解释「不要调 upsert_model」也会被当成调用(本测试第一版就这么假红了)。"""
    import ast
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == func_name)
    out: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(getattr(f, "id", None) or getattr(f, "attr", "") or "")
    return out


def test_manual_upsert_never_touches_global_catalog():
    """全局 catalog 的写入口是 model_registry.upsert_model;per-user 路径绝不能碰它。"""
    called = _called_names("upsert_manual_model")
    assert "upsert_model" not in called
    assert "save_model_catalog" not in called


def test_route_is_user_scoped_and_not_admin_gated():
    """端点走 require_user(任何登录用户),不是 admin 闸。"""
    assert '@router.post("/api/me/models/model")' in _ROUTES
    seg = _ROUTES[_ROUTES.index('@router.post("/api/me/models/model")'):]
    seg = seg[: seg.index("@router.post", 10)]
    assert "require_user(request)" in seg
    assert "require_admin" not in seg and "is_admin" not in seg


def test_frontend_calls_the_per_user_endpoint():
    i = _FE.index("const addModel = async")
    body = _FE[i: i + 1400]
    assert "meUpsertModel" in body, "前端又接回全局端点了"
    assert "models.upsertModel(" not in body, "upsertModel 是 admin-only 的全局写入,不能用"


# ── 同步不许冲掉手填的 ────────────────────────────────────────────────────
def test_sync_deletes_only_synced_rows():
    body = _SRC[_SRC.index("def replace_synced_models"):]
    body = body[: body.index("\ndef ", 1)]
    dele = body[body.index("delete from user_model_entries"):][:300]
    assert "manual" in dele, "同步的覆盖删除没排除手填模型 → 拉一次模型就清空一次"


def test_migration_99_adds_source_column():
    entry = next((m for m in _mig.MIGRATIONS if m[0] == 99), None)
    assert entry is not None, "缺 migration 99"
    sql = " ".join(entry[2])
    assert "user_model_entries" in sql and "source" in sql
    assert "default 'synced'" in sql, "存量行必须默认 synced(它们本来就是同步写入的)"


def test_migrations_stay_append_only():
    """铁律:MIGRATIONS 只加不改,version 单调递增。"""
    versions = [m[0] for m in _mig.MIGRATIONS]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


@pytest.mark.parametrize("fn", ["upsert_manual_model", "delete_overlay_model"])
def test_helpers_reject_bad_input_without_raising(fn):
    f = getattr(um, fn)
    assert f(0, "doubao", {} if fn == "upsert_manual_model" else "x") in (None, 0)
    assert f(1, "", {} if fn == "upsert_manual_model" else "x") in (None, 0)
