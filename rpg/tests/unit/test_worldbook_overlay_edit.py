"""test_worldbook_overlay_edit.py — 存档内世界书条目「改」的端点(07-30 反馈)。

面板此前只有「+」和「×」:写错一个字只能删掉重打整段,而 overlay 正文常是几百字长文。
本测试锁三件事:

1. **部分更新语义**:只写 body 里真正出现的键(`"x" in body`),不是 truthy 判断
   —— 否则「只改优先级」会连带把 title/content 清空,或反过来被迫回传整段正文。
2. **priority 0 不被吞**:`int(raw or 50)` 会把合法的 0 偷偷改成 50(此坑在 add 路径尚存,
   update 明确避开)。
3. **归属判断走 `perms.owns_save`**:CLAUDE.md 明令严禁手写归属 SQL。本文件早先三处各
   写了一遍 game_saves join,已收口到 `_own_addition`。

DB 全用替身,不打真库。
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

_RPG = pathlib.Path(__file__).resolve().parents[2]   # …/rpg —— import 根
REPO = _RPG.parent                                   # 仓库根(frontend/ 在这一层)
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from routes import worldbook_overlay as wo  # noqa: E402

_SRC = pathlib.Path(wo.__file__).read_text(encoding="utf-8")


class _Req:
    """够用的 Request 替身:路由只调 await request.json()。"""

    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _run(coro):
    return asyncio.run(coro)


class _FakeDb:
    """记录所有 execute(sql, params);overlay 行归属由 owner_of 决定。"""

    def __init__(self, row=None):
        self.row = row
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        cur = MagicMock()
        if "from save_worldbook_overlays where id=" in sql:
            cur.fetchone = (lambda: dict(self.row)) if self.row else (lambda: None)
        else:
            cur.fetchone = lambda: None
        cur.fetchall = lambda: []
        return cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def updates(self) -> list[tuple[str, tuple]]:
        return [c for c in self.calls if c[0].lstrip().startswith("update ")]


_OWN_ROW = {"save_id": 7, "kind": "addition"}


def _call_update(body, *, row=_OWN_ROW, owns=True):
    db = _FakeDb(row)
    with patch("platform_app.db.connect", return_value=db), \
         patch("platform_app.db.init_db"), \
         patch("platform_app.perms.owns_save", return_value=owns) as own:
        resp = _run(wo.api_worldbook_overlay_update(_Req(body), {"id": 42}))
    return resp, db, own


class TestPartialUpdate(unittest.TestCase):
    def test_only_priority_leaves_text_untouched(self):
        """只改优先级 → SQL 里不该出现 title/content(否则正文会被清空)。"""
        resp, db, _ = _call_update({"id": 1, "priority": 90})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(db.updates), 1)
        sql, params = db.updates[0]
        self.assertIn("priority=%s", sql)
        self.assertNotIn("title=", sql)
        self.assertNotIn("content=", sql)
        self.assertEqual(params, (90, 1))

    def test_priority_zero_survives(self):
        """0 是合法优先级;`raw or 50` 会把它改成 50。"""
        _resp, db, _ = _call_update({"id": 1, "priority": 0})
        self.assertEqual(db.updates[0][1], (0, 1))

    def test_priority_blank_falls_back_to_default(self):
        _resp, db, _ = _call_update({"id": 1, "priority": ""})
        self.assertEqual(db.updates[0][1], (50, 1))

    def test_full_update_writes_all_four_and_bumps_updated_at(self):
        resp, db, _ = _call_update(
            {"id": 3, "title": "断剑·残", "content": "锋断而意不断", "keys": "断剑, 残", "priority": 70})
        self.assertEqual(resp.status_code, 200)
        sql, params = db.updates[0]
        for frag in ("title=%s", "content=%s", "keys=%s", "priority=%s", "updated_at=now()"):
            self.assertIn(frag, sql)
        self.assertEqual(params[0], "断剑·残")
        self.assertEqual(params[-1], 3)  # id 永远在最后

    def test_keys_accepts_comma_string_and_list(self):
        """前端传逗号串,LLM/脚本可能传列表 —— 同一条归一缝。"""
        self.assertEqual(wo._norm_keys(" a , b ,, c "), ["a", "b", "c"])
        self.assertEqual(wo._norm_keys([" a ", "", "b"]), ["a", "b"])
        self.assertEqual(wo._norm_keys(None), [])
        self.assertEqual(wo._norm_keys(123), [])

    def test_no_fields_is_rejected_not_a_noop_update(self):
        """空 body 不该生成 `set  where id=` 这种坏 SQL。"""
        resp, db, _ = _call_update({"id": 1})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(db.updates, [])


class TestValidation(unittest.TestCase):
    def test_blank_title_rejected(self):
        """建表 check 要求 addition 的 title <> ''。"""
        resp, db, _ = _call_update({"id": 1, "title": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(db.updates, [])

    def test_blank_content_rejected(self):
        resp, db, _ = _call_update({"id": 1, "content": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(db.updates, [])

    def test_bad_id_rejected(self):
        resp, db, _ = _call_update({"id": "abc", "title": "x"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(db.updates, [])


class TestOwnership(unittest.TestCase):
    def test_foreign_entry_not_updated(self):
        resp, db, own = _call_update({"id": 5, "title": "x"}, owns=False)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(db.updates, [])
        own.assert_called_once()

    def test_missing_entry_not_updated(self):
        resp, db, _ = _call_update({"id": 5, "title": "x"}, row=None)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(db.updates, [])

    def test_retirement_is_not_editable(self):
        """retirement 没有可编辑字段,不该被 update 路径碰到。"""
        resp, db, _ = _call_update({"id": 5, "title": "x"}, row={"save_id": 7, "kind": "retirement"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(db.updates, [])

    def test_ownership_goes_through_perms_helper(self):
        """CLAUDE.md:严禁手写归属 SQL —— 本文件不许再出现 game_saves join。"""
        self.assertNotIn("join game_saves", _SRC)
        self.assertNotIn("from game_saves", _SRC)
        self.assertIn("owns_save", _SRC)

    def test_remove_shares_the_same_ownership_seam(self):
        """update / remove 必须共用 _own_addition,否则两条路的归属判断会漂移。"""
        import ast
        tree = ast.parse(_SRC)
        for fn_name in ("api_worldbook_overlay_update", "api_worldbook_overlay_remove"):
            fn = next(n for n in ast.walk(tree)
                      if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == fn_name)
            called = {getattr(c.func, "id", None) or getattr(c.func, "attr", "")
                      for c in ast.walk(fn) if isinstance(c, ast.Call)}
            self.assertIn("_own_addition", called, f"{fn_name} 没走共用归属缝")


class TestRouteWiring(unittest.TestCase):
    def test_update_route_is_registered(self):
        paths = {r.path for r in wo.router.routes}
        self.assertIn("/api/worldbook/overlay/update", paths)

    def test_frontend_has_the_client_method(self):
        api = (REPO / "frontend" / "src" / "api-client.js").read_text(encoding="utf-8")
        self.assertIn("overlayUpdate:", api)
        self.assertIn("/worldbook/overlay/update", api)


if __name__ == "__main__":
    unittest.main()
