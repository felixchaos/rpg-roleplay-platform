"""test_model_settings_ownership_writes.py — 设置 → 模型页剩下几处「普通用户打管理员端点」。

上一轮把单个模型的启停 / 删除按归属路由(用户自己的 → 按用户的 overlay,内置目录 → 管理员),
同页还剩三处照旧一律打管理员端点,普通用户 403 被 catch 吞掉,界面乐观改了、刷新又变回来:
  · 改显示名(POST /api/models/model)
  · 供应商总开关(POST /api/models/api)—— 开关显示的是用户自己凭据的启用态,写的却是全局目录
  · 校验弹窗「全部添加」—— diff 拿全局目录比,普通用户点一次 403 一次

这里锁后端这一侧:
  · 用户可以改自己 overlay 里模型的显示名,且下次同步不被远端名字冲掉
  · 供应商开关有按用户的凭据启停端点
  · 全局目录 upsert_model 是补丁语义:只改显示名不会顺手把停用的模型重新启用,
    只改启停不会把策展的显示名重置成 real_name
  · 非管理员的 diff 拿**他自己看到的清单**比(否则「新增 N 个」都是他列表里已有的)
"""
from __future__ import annotations

import copy
import pathlib

import model_probe
import model_registry

_ROOT = pathlib.Path(model_registry.__file__).parent
_ROUTES = (_ROOT / "platform_app" / "frontend_routes" / "models.py").read_text(encoding="utf-8")
_CRED_ROUTES = (_ROOT / "platform_app" / "api" / "me" / "credentials.py").read_text(encoding="utf-8")
_MODELS_ROUTES = (_ROOT / "routes" / "models.py").read_text(encoding="utf-8")


def _route_body(src: str, decorator: str) -> str:
    assert decorator in src, f"缺路由 {decorator}"
    seg = src[src.index(decorator):]
    nxt = seg.find("@router.", 10)
    return seg if nxt < 0 else seg[:nxt]


# ── 全局目录 upsert_model:补丁语义 ─────────────────────────────────────────
def _fake_catalog(monkeypatch, models):
    state = {"catalog": {"apis": [{"id": "deepseek", "kind": "openai_compat", "models": copy.deepcopy(models)}]}}
    monkeypatch.setattr(model_registry, "load_model_catalog", lambda: copy.deepcopy(state["catalog"]))

    def _save(cat):
        state["catalog"] = copy.deepcopy(cat)
    monkeypatch.setattr(model_registry, "save_model_catalog", _save)
    return state


def _model(state, mid):
    return next(m for m in state["catalog"]["apis"][0]["models"] if m["id"] == mid)


def test_upsert_model_rename_keeps_disabled_state(monkeypatch):
    st = _fake_catalog(monkeypatch, [{"id": "ds-old", "real_name": "ds-old", "display_name": "旧版",
                                      "enabled": False, "capabilities": ["text", "vision"]}])
    model_registry.upsert_model("deepseek", {"real_name": "ds-old", "display_name": "旧版(停用)"})
    m = _model(st, "ds-old")
    assert m["display_name"] == "旧版(停用)"
    assert m["enabled"] is False, "只改显示名把停用的模型重新启用了"
    assert m["capabilities"] == ["text", "vision"]


def test_upsert_model_toggle_keeps_curated_display_name(monkeypatch):
    st = _fake_catalog(monkeypatch, [{"id": "ds-chat", "real_name": "ds-chat", "display_name": "DeepSeek V4.1",
                                      "enabled": True, "capabilities": ["text"]}])
    model_registry.upsert_model("deepseek", {"real_name": "ds-chat", "enabled": False})
    m = _model(st, "ds-chat")
    assert m["enabled"] is False
    assert m["display_name"] == "DeepSeek V4.1", "只改启停把策展的显示名重置成了 real_name"


def test_upsert_model_new_entry_defaults_unchanged(monkeypatch):
    st = _fake_catalog(monkeypatch, [])
    model_registry.upsert_model("deepseek", {"real_name": "ds-new"})
    m = _model(st, "ds-new")
    assert m["display_name"] == "ds-new" and m["enabled"] is True


# ── 按用户的写端点 ─────────────────────────────────────────────────────────
def test_me_display_name_route_is_user_scoped():
    body = _route_body(_ROUTES, '@router.post("/api/me/models/display-name")')
    assert "require_user(request)" in body
    assert "is_admin" not in body and "require_admin" not in body
    assert "set_overlay_model_display_name" in body


def test_me_credential_enabled_route_exists():
    body = _route_body(_CRED_ROUTES, '@router.post("/api/me/credentials/enabled")')
    assert "require_user" in body
    assert "set_credential_enabled" in body


# ── diff 基准:普通用户拿自己的清单比 ───────────────────────────────────────
def test_diff_uses_user_view_for_non_admin(monkeypatch):
    monkeypatch.setattr(model_probe, "list_remote_models", lambda api_id, user_id=None: {
        "ok": True, "models": [{"real_name": "a"}, {"real_name": "b"}]})
    monkeypatch.setattr(model_registry, "load_model_catalog", lambda: {"apis": [
        {"id": "deepseek", "models": [{"real_name": "a"}, {"real_name": "old"}]}]})
    # 用户的 overlay 视图:同步后就是远端清单 a / b
    monkeypatch.setattr(model_registry, "apply_user_overlay", lambda cat, uid: {"apis": [
        {"id": "deepseek", "models": [{"real_name": "a"}, {"real_name": "b"}]}]})
    user = model_probe.diff_catalog("deepseek", user_id=7, user_view=True)
    assert user["remote_only"] == [] and user["local_only"] == []
    admin = model_probe.diff_catalog("deepseek", user_id=7)
    assert admin["remote_only"] == ["b"] and admin["local_only"] == ["old"]


def test_diff_route_passes_user_view_by_role():
    body = _route_body(_MODELS_ROUTES, '@router.get("/api/models/diff")')
    assert "user_view" in body
