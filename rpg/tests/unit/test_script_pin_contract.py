"""test_script_pin_contract.py — 剧本引用(/pin)端点的入参契约。

以前剧本详情的「共享模式」选择器把 target_script_id 填成剧本自己:保存成功(200),
sharing_mode 改成了引用,可 KB 读取重定向(knowledge/_pin.effective_kb_script_id)指回自己,
等于什么都没变 —— 用户以为设置生效了。自引用没有任何语义,端点必须明确拒绝,
且要在碰数据库之前拒绝。另外「公开」不是 sharing_mode(公开发布是 is_public),
合法模式集合里不该出现 public。
"""
from __future__ import annotations

import asyncio
import json

from platform_app.api.script_edit import sharing


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _call(script_id, body, monkeypatch):
    def _no_db(*a, **k):  # 自引用 / 坏参数必须在查库前就被拒
        raise AssertionError("不应走到数据库")
    monkeypatch.setattr(sharing, "connect", _no_db)
    resp = asyncio.run(sharing.api_pin_script(_Req(body), script_id, user={"id": 1}))
    return resp.status_code, json.loads(resp.body)


def test_pin_to_self_is_rejected_before_db(monkeypatch):
    code, body = _call(5, {"mode": "floating-latest", "target_script_id": 5}, monkeypatch)
    assert code == 400
    assert body["ok"] is False
    assert "自己" in body["error"]


def test_pin_self_as_string_is_rejected(monkeypatch):
    code, _ = _call(5, {"mode": "pinned-snapshot", "target_script_id": "5", "commit_id": 3}, monkeypatch)
    assert code == 400


def test_non_integer_target_is_400_not_500(monkeypatch):
    code, body = _call(5, {"mode": "floating-latest", "target_script_id": "abc"}, monkeypatch)
    assert code == 400
    assert body["ok"] is False


def test_public_is_not_a_sharing_mode(monkeypatch):
    assert "public" not in sharing._VALID_SHARING_MODES
    code, _ = _call(5, {"mode": "public", "target_script_id": 9}, monkeypatch)
    assert code == 400
