"""test_models_sync_response_overlay.py — 「拉取模型」的响应要如实反映用户的模型清单。

设置 → 模型页一打开就对每个已配 key 的供应商自动跑一次 POST /api/models/remote/sync,
然后**用响应里的 models 整个替换**页面上的模型列表(web / 手机两端都这样)。旧响应回的是
这次从供应商拉到的原始清单:
  · enabled 一律写死 true —— 而落库时 replace_synced_models 会沿用用户之前的隐藏设置,
    于是用户隐藏过的模型每次打开设置页又显示成「已启用」,看起来隐藏没生效;
  · 不带 synced 标记 —— 前端据它把可见性 / 启停 / 删除路由到按用户的端点,缺了就落到
    管理员专用的全局端点(普通用户 403 / 被跳过,管理员则会把私人模型写进全局目录);
  · 不含用户手填的模型(source='manual',同步时保留)—— 手填的模型一打开设置页就从列表消失。

锁死:响应 models = 同步落库后该用户在这个供应商下的 overlay 实际清单。
全用替身,不连供应商、不打真库。
"""
from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import patch

_RPG = pathlib.Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

from routes import models as rm  # noqa: E402

_OVERLAY_AFTER = {
    "openai": [
        {"id": "gpt-a", "real_name": "gpt-a", "display_name": "gpt-a", "enabled": False,
         "capabilities": ["text"], "synced": True},
        {"id": "gpt-b", "real_name": "gpt-b", "display_name": "gpt-b", "enabled": True,
         "capabilities": ["text"], "synced": True},
        {"id": "my-manual", "real_name": "my-manual", "display_name": "我手填的", "enabled": True,
         "capabilities": ["text", "streaming"], "synced": True},
    ],
}


def _sync():
    remote = {"ok": True, "models": [{"id": "gpt-a"}, {"id": "gpt-b"}]}
    with patch("app._check_probe_permission", return_value=None), \
         patch("model_registry.load_model_catalog", return_value={"apis": []}), \
         patch("model_registry.find_api", return_value={"id": "openai", "kind": "openai", "base_url": "https://api.openai.com/v1"}), \
         patch("model_registry.default_api_for", return_value={}), \
         patch("model_registry.load_catalog_for_user", return_value={"apis": []}), \
         patch("platform_app.user_credentials.get_credential", return_value={}), \
         patch("platform_app.user_credentials._validate_base_url", return_value=None), \
         patch("model_probe.list_remote_models", return_value=remote), \
         patch("model_probe.get_capabilities", return_value=["text"]), \
         patch("platform_app.user_models.replace_synced_models", return_value=2), \
         patch("platform_app.user_models.load_overlay", return_value=_OVERLAY_AFTER):
        resp = rm._remote_sync_blocking({"id": 7}, 7, {"api_id": "openai"})
    return json.loads(resp.body.decode("utf-8"))


def test_response_models_keep_user_hidden_state():
    out = _sync()
    assert out["ok"] is True
    by_id = {m["id"]: m for m in out["models"]}
    assert by_id["gpt-a"]["enabled"] is False, "用户隐藏过的模型,响应里不能又变回启用"


def test_response_models_are_marked_per_user():
    out = _sync()
    assert out["models"] and all(m.get("synced") is True for m in out["models"])


def test_response_includes_manual_models():
    out = _sync()
    assert "my-manual" in {m["id"] for m in out["models"]}, "手填模型不能在同步后从列表里消失"


def test_counts_still_describe_the_remote_pull():
    out = _sync()
    assert out["synced"] == 2
    assert out["remote_total"] == 2
