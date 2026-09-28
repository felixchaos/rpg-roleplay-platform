"""剧本编辑器 canon 实体增删改 —— 真库路由测试(无 DB 则跳过)。

群反馈截图「操作失败 / 缺少必填字段 logical_key / name / type」:md-editor 资源管理器
新建设定实体只发 {name, type},POST /canon-entities 却要求调用方必须给 logical_key,
从 f3e3fc58a(2026-06)起新建必失败。这里锁死整条编辑面:

  · 新建不带 logical_key → 后端按「提取重建同口径」确定性生成,稳定且唯一(同名同类再建加序号)
  · 显式给 logical_key 撞键 → 仍 409(旧语义留给显式传 key 的调用方)
  · name / type 校验 + 可读报错(不再只列字段名)
  · PUT 真能改名 / 改类型(之前静默丢弃 name/type,md-editor 改名、表格改名都是假成功)
  · PUT 传 null 不再写进字面量 "None";空串数字字段不再 500
  · 删除是真删除(之前 importance=-1 软删,但列表 / GM 读路径都不认,删了照样在)
  · 编辑器写入打 attrs.source='editor'(与编辑器 agent 工具同口径,重建知识库不抹掉)
  · 编辑器 agent 工具 upsert_canon_entity 同一套生成 / 校验规则
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

    uname = f"integtest_canon_{uuid.uuid4().hex[:8]}"
    with connect() as db:
        uid = db.execute(
            "insert into users(username, display_name) values (%s, 'canon') returning id", (uname,),
        ).fetchone()["id"]
        sid = db.execute(
            "insert into scripts(owner_id,title,source_path,chapter_count,word_count) "
            "values (%s,%s,%s,%s,%s) returning id",
            (uid, "canon-crud-pytest", "/tmp/x", 1, 10),
        ).fetchone()["id"]
        db.commit()

    fa = FastAPI()
    fa.include_router(edit_router)
    fa.include_router(scripts_router)
    fa.dependency_overrides[require_user] = lambda: {"id": uid, "username": uname, "role": "user"}
    client = TestClient(fa)
    try:
        yield {"client": client, "sid": sid, "uid": uid, "connect": connect}
    finally:
        with connect() as db:
            db.execute("delete from scripts where id=%s", (sid,))
            db.execute("delete from users where id=%s", (uid,))
            db.commit()


def _row(env, lk):
    with env["connect"]() as db:
        return db.execute(
            "select * from kb_canon_entities where script_id=%s and logical_key=%s",
            (env["sid"], lk),
        ).fetchone()


# ── 新建 ─────────────────────────────────────────────────────────────────────

def test_create_without_logical_key_generates_stable_unique_key(env):
    c, sid = env["client"], env["sid"]
    r1 = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": "新实体", "type": "concept"})
    assert r1.status_code == 200, r1.text
    j1 = r1.json()
    assert j1["ok"] is True
    lk1 = j1["entity"]["logical_key"]
    assert j1["logical_key"] == lk1
    # 与提取重建同口径:非 character 类型带 _<type> 后缀
    assert lk1 == "新实体_concept"

    # 同名同类型再建:不冲突,确定性加序号
    r2 = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": "新实体", "type": "concept"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["entity"]["logical_key"] == "新实体_concept_2"
    r3 = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": "新实体", "type": "concept"})
    assert r3.json()["entity"]["logical_key"] == "新实体_concept_3"

    # character 类型:key 就是规范化名字(空白转下划线),与 resolve._slug 一致
    r4 = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": " 穆蕾 莉娅 ", "type": "character"})
    assert r4.status_code == 200, r4.text
    assert r4.json()["entity"]["logical_key"] == "穆蕾_莉娅"
    assert r4.json()["entity"]["name"] == "穆蕾 莉娅"

    # 编辑器写入打 source='editor'(重建知识库不抹掉)
    assert (_row(env, lk1)["attrs"] or {}).get("source") == "editor"


def test_create_accepts_chinese_type_synonym(env):
    c, sid = env["client"], env["sid"]
    r = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": "北港", "type": "地点"})
    assert r.status_code == 200, r.text
    assert r.json()["entity"]["type"] == "location"
    assert r.json()["entity"]["logical_key"] == "北港_location"


def test_create_explicit_key_conflict_still_409(env):
    c, sid = env["client"], env["sid"]
    body = {"logical_key": "hero_01", "name": "主角", "type": "character"}
    assert c.post(f"/api/scripts/{sid}/canon-entities", json=body).status_code == 200
    r = c.post(f"/api/scripts/{sid}/canon-entities", json={**body, "name": "另一个"})
    assert r.status_code == 409
    err = r.json()["error"]
    assert "hero_01" in err and "留空" in err  # 可读:告诉用户怎么办
    assert _row(env, "hero_01")["name"] == "主角"  # 没被覆盖


def test_create_validation_errors_are_readable(env):
    c, sid = env["client"], env["sid"]
    r = c.post(f"/api/scripts/{sid}/canon-entities", json={"type": "concept"})
    assert r.status_code == 400
    assert "名称" in r.json()["error"]
    assert "logical_key" not in r.json()["error"]  # 不再只甩字段名

    r = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": "某物"})
    assert r.status_code == 400
    assert "类型" in r.json()["error"]

    r = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": "某物", "type": "weird"})
    assert r.status_code == 400
    err = r.json()["error"]
    assert "weird" in err and "character" in err and "人物" in err

    r = c.post(f"/api/scripts/{sid}/canon-entities",
               json={"name": "某物", "type": "item", "importance": "很高"})
    assert r.status_code == 400
    assert "整数" in r.json()["error"]


# ── 编辑 ─────────────────────────────────────────────────────────────────────

def test_put_renames_and_retypes(env):
    c, sid = env["client"], env["sid"]
    lk = c.post(f"/api/scripts/{sid}/canon-entities",
                json={"name": "旧名", "type": "concept"}).json()["entity"]["logical_key"]
    r = c.put(f"/api/scripts/{sid}/canon-entities/{lk}", json={"name": "新名", "type": "faction"})
    assert r.status_code == 200, r.text
    ent = r.json()["entity"]
    assert ent["name"] == "新名" and ent["type"] == "faction"
    assert ent["logical_key"] == lk  # key 是固定标识,改名不换 key
    row = _row(env, lk)
    assert row["name"] == "新名" and row["type"] == "faction"

    r = c.put(f"/api/scripts/{sid}/canon-entities/{lk}", json={"name": "  "})
    assert r.status_code == 400 and "名称" in r.json()["error"]
    r = c.put(f"/api/scripts/{sid}/canon-entities/{lk}", json={"type": "bogus"})
    assert r.status_code == 400 and "bogus" in r.json()["error"]
    assert _row(env, lk)["type"] == "faction"


def test_put_null_and_blank_values_do_not_corrupt(env):
    c, sid = env["client"], env["sid"]
    lk = c.post(f"/api/scripts/{sid}/canon-entities",
                json={"name": "甲", "type": "concept", "summary": "s",
                      "parent_logical_key": "x", "importance": 3}).json()["entity"]["logical_key"]
    # 表格里「(无上级)」发 parent_logical_key:null;md-editor 清空数字字段发空串
    r = c.put(f"/api/scripts/{sid}/canon-entities/{lk}",
              json={"parent_logical_key": None, "summary": None, "importance": "",
                    "first_revealed_chapter": None})
    assert r.status_code == 200, r.text
    row = _row(env, lk)
    assert row["parent_logical_key"] == ""  # 不是字面量 "None"
    assert row["summary"] == ""
    assert row["importance"] == 0 and row["first_revealed_chapter"] == 0

    r = c.put(f"/api/scripts/{sid}/canon-entities/{lk}", json={"parent_logical_key": lk})
    assert r.status_code == 400 and "上级" in r.json()["error"]


def test_put_marks_editor_source_and_keeps_it_when_attrs_replaced(env):
    c, sid = env["client"], env["sid"]
    with env["connect"]() as db:
        db.execute(
            "insert into kb_canon_entities(script_id, logical_key, name, type, attrs) "
            "values (%s, 'extracted_one', '提取来的', 'concept', '{\"k\": 1}'::jsonb)",
            (sid,),
        )
        db.commit()
    r = c.put(f"/api/scripts/{sid}/canon-entities/extracted_one", json={"summary": "人工改过"})
    assert r.status_code == 200, r.text
    attrs = _row(env, "extracted_one")["attrs"]
    assert attrs.get("source") == "editor" and attrs.get("k") == 1

    # md-editor 整体回写 attrs(不含 source)也不能把标记冲掉
    r = c.put(f"/api/scripts/{sid}/canon-entities/extracted_one", json={"attrs": {"gender": "女"}})
    assert r.status_code == 200, r.text
    attrs = _row(env, "extracted_one")["attrs"]
    assert attrs == {"gender": "女", "source": "editor"}


def test_put_missing_entity_404_readable(env):
    c, sid = env["client"], env["sid"]
    r = c.put(f"/api/scripts/{sid}/canon-entities/nope", json={"name": "x"})
    assert r.status_code == 404
    assert "不存在" in r.json()["error"]


# ── 删除 ─────────────────────────────────────────────────────────────────────

def test_delete_is_real_and_key_reusable(env):
    c, sid = env["client"], env["sid"]
    body = {"logical_key": "tmp_key", "name": "临时", "type": "item"}
    assert c.post(f"/api/scripts/{sid}/canon-entities", json=body).status_code == 200
    r = c.delete(f"/api/scripts/{sid}/canon-entities/tmp_key")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert _row(env, "tmp_key") is None
    assert c.get(f"/api/scripts/{sid}/canon-entities/tmp_key").status_code == 404
    keys = {e["logical_key"] for e in c.get(
        f"/api/scripts/{sid}/canon-entities", params={"fetch_all": "true"}).json()["items"]}
    assert "tmp_key" not in keys
    # 删了能用同一个 key 重建(软删时代这里恒 409)
    assert c.post(f"/api/scripts/{sid}/canon-entities", json=body).status_code == 200
    # commit 里留了删除前的整行供审计
    with env["connect"]() as db:
        payload = db.execute(
            "select payload from script_commits where script_id=%s and kind='canon_delete' "
            "order by id desc limit 1", (sid,),
        ).fetchone()["payload"]
    assert payload["before"]["name"] == "临时" and payload["before"]["type"] == "item"


# ── 列表 ─────────────────────────────────────────────────────────────────────

def test_list_fetch_all_and_type_filter(env):
    c, sid = env["client"], env["sid"]
    with env["connect"]() as db:
        # importance 各不相同,新建的(importance=0)排在最后 —— 默认一页 50 条时看不到
        for i in range(60):
            db.execute(
                "insert into kb_canon_entities(script_id, logical_key, name, type, importance) "
                "values (%s, %s, %s, %s, %s)",
                (sid, f"k{i}", f"n{i}", "character" if i % 2 else "location", 100 - i),
            )
        db.commit()
    lk = c.post(f"/api/scripts/{sid}/canon-entities",
                json={"name": "新建的", "type": "concept"}).json()["entity"]["logical_key"]
    j = c.get(f"/api/scripts/{sid}/canon-entities", params={"fetch_all": "true"}).json()
    keys = [e["logical_key"] for e in j["items"]]
    assert len(keys) == 61 and lk in keys
    assert j["page"]["has_more"] is False

    j = c.get(f"/api/scripts/{sid}/canon-entities",
              params={"fetch_all": "true", "type": "location"}).json()
    assert {e["type"] for e in j["items"]} == {"location"} and len(j["items"]) == 30


# ── 编辑器 agent 工具(tools_dsl)同口径 ─────────────────────────────────────

def test_editor_agent_tool_creates_without_key_and_validates_type(env):
    from tools_dsl.command_tools_script_write.canon import _t_upsert_canon_entity

    sid, uid = env["sid"], env["uid"]
    out = _t_upsert_canon_entity(uid, sid, {"name": "灵脉", "type": "concept"}, None)
    assert not out.startswith("失败"), out
    assert "灵脉_concept" in out
    assert _row(env, "灵脉_concept")["attrs"].get("source") == "editor"

    out = _t_upsert_canon_entity(uid, sid, {"name": "灵脉", "type": "concept"}, None)
    assert "灵脉_concept_2" in out

    out = _t_upsert_canon_entity(uid, sid, {"name": "某物", "type": "weapon"}, None)
    assert out.startswith("失败") and "weapon" in out

    out = _t_upsert_canon_entity(uid, sid, {"logical_key": "灵脉_concept", "type": "bogus"}, None)
    assert out.startswith("失败") and "bogus" in out
    assert _row(env, "灵脉_concept")["type"] == "concept"

    out = _t_upsert_canon_entity(uid, sid, {"logical_key": "灵脉_concept", "type": "势力"}, None)
    assert not out.startswith("失败"), out
    assert _row(env, "灵脉_concept")["type"] == "faction"


# ── 复核页 PATCH /canon(script-review 行内编辑摘要)同口径 ─────────────────────

def test_review_patch_update_marks_editor_source(env):
    c, sid = env["client"], env["sid"]
    with env["connect"]() as db:
        db.execute(
            "insert into kb_canon_entities(script_id, logical_key, name, type) "
            "values (%s, 'rv_one', '复核实体', 'concept')", (sid,),
        )
        db.commit()
    r = c.patch(f"/api/scripts/{sid}/canon",
                json={"op": "update_entity", "logical_key": "rv_one", "summary": "复核改过"})
    assert r.status_code == 200 and r.json()["updated"] == 1, r.text
    row = _row(env, "rv_one")
    assert row["summary"] == "复核改过"
    assert (row["attrs"] or {}).get("source") == "editor"  # 重建知识库不抹掉人工复核的改动

    r = c.patch(f"/api/scripts/{sid}/canon",
                json={"op": "update_entity", "logical_key": "rv_one", "importance": "高"})
    assert r.status_code == 400 and "整数" in r.json()["error"]
