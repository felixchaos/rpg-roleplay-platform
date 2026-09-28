"""md-editor 资源管理器「新建 / 改名」对其它节点类型(锚点 / NPC 角色卡)的后端契约 —— 真库路由测试。

横扫 canon 新建必失败时顺带核出来的同面问题:
  · 锚点:REST 新建没打 source='editor'(编辑器 agent 的 create_anchor 有)→ 时间线重建把用户
    手建的时间点静默删掉;同名撞唯一约束时报错只甩列名;改名撞唯一约束直接 500。
  · NPC 角色卡:POST 不带 id 本应是「新建」,却走 on conflict(script_id,name) do update ——
    新建时名字和已有 NPC 重了,会把那张卡的全部人设静默清空覆盖(md-editor / 剧本详情 /
    角色卡页 / 手机端「新增 NPC」四个入口都走这条)。
"""
from __future__ import annotations

import uuid

import pytest


def _db_or_skip():
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            db.execute("select 1").fetchone()
        return connect
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"无 DB: {exc}")


@pytest.fixture()
def env():
    connect = _db_or_skip()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from platform_app.api._deps import require_user
    from platform_app.api.script_edit import router as edit_router
    from platform_app.api.scripts import router as scripts_router

    uname = f"integtest_tree_{uuid.uuid4().hex[:8]}"
    with connect() as db:
        uid = db.execute(
            "insert into users(username, display_name) values (%s, 'tree') returning id", (uname,),
        ).fetchone()["id"]
        sid = db.execute(
            "insert into scripts(owner_id,title,source_path,chapter_count,word_count) "
            "values (%s,%s,%s,%s,%s) returning id",
            (uid, "tree-crud-pytest", "/tmp/x", 1, 10),
        ).fetchone()["id"]
        db.commit()

    fa = FastAPI()
    fa.include_router(edit_router)
    fa.include_router(scripts_router)
    fa.dependency_overrides[require_user] = lambda: {"id": uid, "username": uname, "role": "user"}
    try:
        yield {"client": TestClient(fa), "sid": sid, "uid": uid, "connect": connect}
    finally:
        with connect() as db:
            db.execute("delete from scripts where id=%s", (sid,))
            db.execute("delete from users where id=%s", (uid,))
            db.commit()


# ── 锚点 ─────────────────────────────────────────────────────────────────────

def test_anchor_create_marks_editor_source_and_duplicate_is_readable(env):
    c, sid = env["client"], env["sid"]
    r = c.post(f"/api/scripts/{sid}/anchors",
               json={"story_time_label": "三年后", "chapter_min": 1, "chapter_max": 1})
    assert r.status_code == 200, r.text
    aid = r.json()["anchor"]["id"]
    with env["connect"]() as db:
        src = db.execute("select source from script_timeline_anchors where id=%s", (aid,)).fetchone()["source"]
    assert src == "editor"  # 时间线重建只删 source<>'editor' 的原著骨架

    r = c.post(f"/api/scripts/{sid}/anchors",
               json={"story_time_label": "三年后", "chapter_min": 1, "chapter_max": 1})
    assert r.status_code == 409
    assert "三年后" in r.json()["error"] and "story_phase" not in r.json()["error"]


def test_anchor_rename_collision_is_409_not_500(env):
    c, sid = env["client"], env["sid"]
    c.post(f"/api/scripts/{sid}/anchors", json={"story_time_label": "甲", "chapter_min": 1, "chapter_max": 1})
    aid = c.post(f"/api/scripts/{sid}/anchors",
                 json={"story_time_label": "乙", "chapter_min": 2, "chapter_max": 2}).json()["anchor"]["id"]
    r = c.put(f"/api/scripts/{sid}/anchors/{aid}", json={"story_time_label": "甲"})
    assert r.status_code == 409, r.text
    assert "甲" in r.json()["error"]
    # 正常改名照旧
    r = c.put(f"/api/scripts/{sid}/anchors/{aid}", json={"story_time_label": "丙"})
    assert r.status_code == 200 and r.json()["anchor"]["story_time_label"] == "丙"


# ── NPC 角色卡 ───────────────────────────────────────────────────────────────

def test_card_create_with_existing_name_does_not_wipe_that_card(env):
    c, sid = env["client"], env["sid"]
    r = c.post(f"/api/scripts/{sid}/character-cards",
               json={"name": "张三", "identity": "镖师", "background": "走南闯北二十年"})
    assert r.status_code == 200, r.text
    cid = r.json()["card"]["id"]

    # md-editor 资源管理器「新建角色卡」只发 {name}
    r = c.post(f"/api/scripts/{sid}/character-cards", json={"name": "张三"})
    assert r.status_code == 400
    assert "张三" in r.json()["error"]
    with env["connect"]() as db:
        row = db.execute("select identity, background from character_cards where id=%s", (cid,)).fetchone()
    assert row["identity"] == "镖师" and row["background"] == "走南闯北二十年"

    # 带 id 的编辑照常
    r = c.post(f"/api/scripts/{sid}/character-cards", json={"id": cid, "name": "张三", "identity": "总镖头"})
    assert r.status_code == 200 and r.json()["card"]["identity"] == "总镖头"


def test_internal_upsert_by_name_still_merges(env):
    """酒馆卡导入 / 导入流水线直调 upsert_character_card(不经 REST 新建语义),同名仍按名合并。"""
    from platform_app.knowledge.character_cards import upsert_character_card

    sid, uid = env["sid"], env["uid"]
    a = upsert_character_card(uid, sid, {"name": "李四", "identity": "旧"})
    b = upsert_character_card(uid, sid, {"name": "李四", "identity": "新"})
    assert a["id"] == b["id"] and b["identity"] == "新"
