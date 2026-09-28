"""test_worldbook_first_revealed_chapter.py — 世界书「首次揭示章节」编辑器能改、后端却不认。

剧本编辑器(/md-editor)的世界书 front-matter 把 first_revealed_chapter 列为可写字段
(lib/md-serialize.js SCHEMAS.worldbook.writeScalars),GET 列表也原样返回它;但
PUT /api/scripts/{sid}/worldbook/{eid} 与 POST 新建都不认这个键:
  · 只改了这一项 → sets 为空 → 400「无可更新字段」(必然失败);
  · 同时改了别的 → 这一项被静默丢掉。
而它恰恰是检索的防剧透门槛(_search: first_revealed_chapter <= 玩家进度)。

DB 全用替身,不打真库。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

_RPG = pathlib.Path(__file__).resolve().parents[2]
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))

from platform_app.api.script_edit import worldbook as wb  # noqa: E402

_ROW = {
    "id": 5, "title": "灯塔", "content": "旧文", "priority": 50, "enabled": True, "metadata": {},
    "keys": [], "regex_keys": [], "character_filter": [], "scene_filter": [],
    "token_budget": 600, "sticky_turns": 0, "cooldown_turns": 0, "probability": 100.0,
    "insertion_position": "worldbook", "first_revealed_chapter": 0,
}


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _FakeDb:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, tuple(params or ())))
        cur = MagicMock()
        s = sql.strip().lower()
        if s.startswith("select") and "from worldbook_entries" in s:
            cur.fetchone = lambda: dict(_ROW)
        elif s.startswith("insert into worldbook_entries"):
            cur.fetchone = lambda: dict(_ROW, id=9)
        else:
            cur.fetchone = lambda: None
        return cur

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _call(fn, *args):
    db = _FakeDb()
    with patch.object(wb, "connect", return_value=db), \
         patch.object(wb, "_require_owner", return_value=True), \
         patch.object(wb, "_write_commit", return_value=1):
        resp = asyncio.run(fn(*args))
    return resp, db


def _body(resp):
    return json.loads(resp.body.decode("utf-8"))


def test_put_only_first_revealed_chapter_is_accepted_and_written():
    resp, db = _call(wb.api_worldbook_update, _Req({"first_revealed_chapter": 12}), 1, 5, {"id": 1})
    assert resp.status_code == 200, _body(resp)
    updates = [c for c in db.calls if c[0].lstrip().upper().startswith("UPDATE WORLDBOOK_ENTRIES")]
    assert updates, "应当落 UPDATE"
    sql, params = updates[0]
    assert "first_revealed_chapter=%s" in sql
    assert 12 in params


def test_put_blank_first_revealed_chapter_means_visible_from_start():
    # md 编辑器里清空这一格 → 空串;按列约定 0 = 开局即可见,不能 int('') 炸 500。
    resp, db = _call(wb.api_worldbook_update, _Req({"first_revealed_chapter": ""}), 1, 5, {"id": 1})
    assert resp.status_code == 200, _body(resp)
    sql, params = [c for c in db.calls if c[0].lstrip().upper().startswith("UPDATE WORLDBOOK_ENTRIES")][0]
    assert "first_revealed_chapter=%s" in sql
    assert 0 in params


def test_post_new_entry_carries_first_revealed_chapter():
    resp, db = _call(wb.api_worldbook_add, _Req({"title": "新条目", "content": "x", "first_revealed_chapter": 7}), 1, {"id": 1})
    assert resp.status_code == 200, _body(resp)
    sql, params = [c for c in db.calls if c[0].lstrip().lower().startswith("insert into worldbook_entries")][0]
    assert "first_revealed_chapter" in sql
    assert 7 in params
