"""设定实体(canon)编辑面的数据完整性 —— 真库路由测试(无 DB 则跳过)。

巡检第二轮整合审查,编辑面修通以后暴露出的一串「改了但没生效 / 生效了但留了尾巴」:

  · 主提取链路(resolve → canon_repo.upsert_canon_entity)对编辑器写入的实体没有保护:
    重新提取会把用户改过的摘要冲掉、attrs 整列换成 {}(source 标记跟着丢);
    编辑器新建的 key 规则(名字_类型)又和主提取(非 concept 直接用名字)对不上,
    提取后同名同类型的实体变成两条。编辑器新建同名同类型实体也不拦,静默生成 _2。
  · 世界书列表 select * 把 768 维向量整列带回前端,md-editor 每打开一条就拉一遍全量。
  · canon 改了名字 / 摘要 / 别名,向量没置空,召回一直按旧文本命中。
  · 别名归并用进程级 lru_cache 永不失效:删了实体或去掉别名,GM 写关系仍按旧别名归并。
  · 物理删除实体不处理子实体:上级列挂着一个不存在的 key,之后同名重建还会被静默挂回去。
  · logical_key 可以是「.」「..」:浏览器把路径里的点段规范化,这条实体打不开也删不掉。
"""
from __future__ import annotations

import re
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

    uname = f"integtest_canonint_{uuid.uuid4().hex[:8]}"
    with connect() as db:
        uid = db.execute(
            "insert into users(username, display_name) values (%s, 'canonint') returning id", (uname,),
        ).fetchone()["id"]
        sid = db.execute(
            "insert into scripts(owner_id,title,source_path,chapter_count,word_count) "
            "values (%s,%s,%s,%s,%s) returning id",
            (uid, "canon-integrity-pytest", "/tmp/x", 1, 10),
        ).fetchone()["id"]
        other_sid = db.execute(
            "insert into scripts(owner_id,title,source_path,chapter_count,word_count) "
            "values (%s,%s,%s,%s,%s) returning id",
            (uid, "canon-integrity-other", "/tmp/y", 1, 10),
        ).fetchone()["id"]
        db.commit()

    fa = FastAPI()
    fa.include_router(edit_router)
    fa.include_router(scripts_router)
    fa.dependency_overrides[require_user] = lambda: {"id": uid, "username": uname, "role": "user"}
    client = TestClient(fa)
    try:
        yield {"client": client, "sid": sid, "other_sid": other_sid, "uid": uid, "connect": connect}
    finally:
        with connect() as db:
            db.execute("delete from books where script_id in (%s, %s)", (sid, other_sid))
            db.execute("delete from scripts where id in (%s, %s)", (sid, other_sid))
            db.execute("delete from users where id=%s", (uid,))
            db.commit()


def _row(env, lk, sid=None):
    with env["connect"]() as db:
        return db.execute(
            "select * from kb_canon_entities where script_id=%s and logical_key=%s",
            (sid or env["sid"], lk),
        ).fetchone()


def _insert(env, lk, name, typ, *, editor=False, summary="", aliases=None, parent=""):
    from psycopg.types.json import Jsonb
    with env["connect"]() as db:
        db.execute(
            "insert into kb_canon_entities(script_id, logical_key, name, type, summary, aliases, "
            "attrs, parent_logical_key) values (%s,%s,%s,%s,%s,%s,%s,%s)",
            (env["sid"], lk, name, typ, summary, Jsonb(aliases or []),
             Jsonb({"source": "editor"} if editor else {}), parent),
        )
        db.commit()


def _set_fake_embedding(env, lk):
    """给 canon 行塞一个假向量(pgvector 的 vector(N),或桌面无 pgvector 时的 jsonb 占位列)。"""
    with env["connect"]() as db:
        typ = db.execute(
            "select format_type(atttypid, atttypmod) as t from pg_attribute "
            "where attrelid = 'kb_canon_entities'::regclass and attname = 'embedding'",
        ).fetchone()["t"]
        m = re.match(r"vector\((\d+)\)", typ or "")
        if m:
            db.execute(
                f"update kb_canon_entities set embedding = array_fill(0.1::real, array[{int(m.group(1))}])::vector "
                "where script_id=%s and logical_key=%s", (env["sid"], lk))
        else:
            db.execute(
                "update kb_canon_entities set embedding = '[0.1]'::jsonb "
                "where script_id=%s and logical_key=%s", (env["sid"], lk))
        db.commit()


def _has_embedding(env, lk) -> bool:
    with env["connect"]() as db:
        return db.execute(
            "select embedding is not null as e from kb_canon_entities where script_id=%s and logical_key=%s",
            (env["sid"], lk),
        ).fetchone()["e"]


# ── 1.1 主提取链路不覆盖 / 不重复编辑器写入的实体 ────────────────────────────

def test_logical_key_base_matches_main_extraction():
    from kb.canon_repo import canon_logical_key_base
    # 主提取(extract.resolve):只有 concept 带 _concept 后缀,其它类型直接用规范化名字
    assert canon_logical_key_base("奉天城", "location") == "奉天城"
    assert canon_logical_key_base("德军", "faction") == "德军"
    assert canon_logical_key_base("萧炎", "character") == "萧炎"
    assert canon_logical_key_base("斗气", "concept") == "斗气_concept"


def test_extraction_upsert_keeps_editor_row(env):
    from kb import canon_repo
    _insert(env, "萧炎", "萧炎", "character", editor=True, summary="用户改过的摘要", aliases=["炎帝"])
    with env["connect"]() as db:
        canon_repo.upsert_canon_entity(
            db, env["sid"], "萧炎", name="萧炎", type="character",
            aliases=["小炎子"], summary="提取出来的摘要", importance=99)
        db.commit()
    row = _row(env, "萧炎")
    assert row["summary"] == "用户改过的摘要"
    assert row["aliases"] == ["炎帝"]
    assert (row["attrs"] or {}).get("source") == "editor"


def test_extraction_upsert_still_refreshes_extracted_rows(env):
    from kb import canon_repo
    _insert(env, "药老", "药老", "character", summary="旧摘要")
    with env["connect"]() as db:
        canon_repo.upsert_canon_entity(
            db, env["sid"], "药老", name="药老", type="character", summary="新摘要")
        db.commit()
    assert _row(env, "药老")["summary"] == "新摘要"


def test_extraction_does_not_duplicate_editor_entity_with_legacy_key(env):
    """旧规则建的编辑器实体(奉天城_location)在主提取写「奉天城」时不再多出一条。"""
    from kb import canon_repo
    _insert(env, "奉天城_location", "奉天城", "location", editor=True, summary="用户写的")
    with env["connect"]() as db:
        canon_repo.upsert_canon_entity(
            db, env["sid"], "奉天城", name="奉天城", type="location", summary="提取的")
        n = db.execute(
            "select count(*) as c from kb_canon_entities where script_id=%s and name='奉天城' and type='location'",
            (env["sid"],),
        ).fetchone()["c"]
        db.commit()
    assert n == 1
    assert _row(env, "奉天城_location")["summary"] == "用户写的"


def test_resolve_and_write_counts_only_rows_actually_written(env):
    """被编辑器保护而跳过的实体不计入 entities_written(之前每条都 +1,报告数偏高)。"""
    from extract.per_chapter import ChapterExtract
    from extract.resolve import resolve_and_write
    _insert(env, "奉天城_location", "奉天城", "location", editor=True, summary="用户写的")
    _insert(env, "萧炎", "萧炎", "character", editor=True, summary="用户写的")
    exs = [ChapterExtract(chapter=1, entities=[
        {"canonical_guess": "奉天城", "surface": "奉天城", "type": "location"},
        {"canonical_guess": "萧炎", "surface": "萧炎", "type": "character"},
        {"canonical_guess": "药尘", "surface": "药尘", "type": "character"},
    ], concepts=[])]
    with env["connect"]() as db:
        r = resolve_and_write(db, env["sid"], exs, embedder=None)
        db.commit()
    assert r["entities_written"] == 1, r
    assert _row(env, "药尘") is not None
    assert _row(env, "萧炎")["summary"] == "用户写的"


def test_rebuild_does_not_duplicate_editor_entity(env):
    from psycopg.types.json import Jsonb

    from extract.rebuild import rebuild_canon_resolve_from_facts
    sid = env["sid"]
    _insert(env, "北海", "北海", "location", editor=True, summary="用户写的")
    with env["connect"]() as db:
        bid = db.execute(
            "insert into books(owner_id, script_id, title, slug) values (%s,%s,%s,%s) returning id",
            (env["uid"], sid, "b", f"b-{uuid.uuid4().hex[:8]}"),
        ).fetchone()["id"]
        db.execute(
            "insert into chapter_facts(book_id, script_id, chapter, locations, characters) "
            "values (%s,%s,1,%s,%s)",
            (bid, sid, Jsonb(["北海"]), Jsonb(["路人甲"])),
        )
        r = rebuild_canon_resolve_from_facts(db, sid)
        db.commit()
    assert r["ok"], r
    with env["connect"]() as db:
        rows = db.execute(
            "select logical_key from kb_canon_entities where script_id=%s and name='北海' and type='location'",
            (sid,),
        ).fetchall()
    assert [x["logical_key"] for x in rows] == ["北海"]
    assert _row(env, "路人甲") is not None


def test_create_same_name_and_type_is_rejected_with_pointer(env):
    c, sid = env["client"], env["sid"]
    _insert(env, "云韵", "云韵", "character", summary="提取出来的")
    r = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": "云韵", "type": "character"})
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert "云韵" in err and "编辑" in err
    # 显式给了不同的 key 也一样:同名同类型两条,GM 读到两份
    r = c.post(f"/api/scripts/{sid}/canon-entities",
               json={"name": "云韵", "type": "character", "logical_key": "yunyun"})
    assert r.status_code == 409
    # 同名不同类型不算重复
    r = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": "云韵", "type": "concept"})
    assert r.status_code == 200, r.text


def test_agent_create_same_name_and_type_points_to_existing(env):
    from tools_dsl.command_tools_script_write.canon import _t_upsert_canon_entity
    _insert(env, "云岚宗", "云岚宗", "faction")
    out = _t_upsert_canon_entity(env["uid"], env["sid"], {"name": "云岚宗", "type": "势力"}, None)
    assert out.startswith("失败") and "云岚宗" in out and "logical_key" in out
    with env["connect"]() as db:
        n = db.execute(
            "select count(*) as c from kb_canon_entities where script_id=%s and name='云岚宗'",
            (env["sid"],),
        ).fetchone()["c"]
    assert n == 1


# ── 1.2 世界书列表不带向量列 + 单条读取 ──────────────────────────────────────

def test_rename_onto_same_name_and_type_is_rejected(env):
    """改名 / 改类型撞上已有的同名同类型实体,和新建一样拒绝并指向那条(否则 GM 读到两份)。"""
    c, sid = env["client"], env["sid"]
    _insert(env, "纳兰嫣然", "纳兰嫣然", "character", summary="正主")
    _insert(env, "嫣然", "嫣然", "character", summary="重复的")
    r = c.put(f"/api/scripts/{sid}/canon-entities/嫣然", json={"name": "纳兰嫣然"})
    assert r.status_code == 409, r.text
    assert r.json()["existing_logical_key"] == "纳兰嫣然"
    assert _row(env, "嫣然")["name"] == "嫣然"
    # 名字不变、只改摘要不受影响;改回自己的名字也不算撞
    r = c.put(f"/api/scripts/{sid}/canon-entities/嫣然", json={"name": "嫣然", "summary": "改了摘要"})
    assert r.status_code == 200, r.text
    # 改类型撞上:同名的势力已存在
    _insert(env, "萧家", "萧家", "faction")
    _insert(env, "萧家_loc", "萧家", "location")
    r = c.put(f"/api/scripts/{sid}/canon-entities/萧家_loc", json={"type": "faction"})
    assert r.status_code == 409, r.text
    assert r.json()["existing_logical_key"] == "萧家"


def test_agent_rename_onto_same_name_and_type_is_rejected(env):
    from tools_dsl.command_tools_script_write.canon import _t_upsert_canon_entity
    _insert(env, "美杜莎", "美杜莎", "character", summary="正主")
    _insert(env, "彩鳞", "彩鳞", "character")
    out = _t_upsert_canon_entity(env["uid"], env["sid"], {"logical_key": "彩鳞", "name": "美杜莎"}, None)
    assert out.startswith("失败") and "美杜莎" in out, out
    assert _row(env, "彩鳞")["name"] == "彩鳞"
    out = _t_upsert_canon_entity(env["uid"], env["sid"], {"logical_key": "彩鳞", "summary": "蛇人族女王"}, None)
    assert not out.startswith("失败"), out


def _wb_create(env, title, sid=None):
    r = env["client"].post(f"/api/scripts/{sid or env['sid']}/worldbook",
                           json={"title": title, "content": "正文"})
    assert r.status_code == 200, r.text
    j = r.json()
    return (j.get("entry") or j)["id"]


def test_worldbook_list_has_no_vector_columns(env):
    c, sid = env["client"], env["sid"]
    _wb_create(env, "条目一")
    for params in ({"fetch_all": "true"}, {}):
        j = c.get(f"/api/scripts/{sid}/worldbook", params=params).json()
        items = j.get("items") or []
        assert items, j
        for it in items:
            assert "embedding_vec" not in it
            assert "title" in it and "content" in it and "keys" in it and "metadata" in it


def test_worldbook_single_entry_endpoint(env):
    c, sid = env["client"], env["sid"]
    eid = _wb_create(env, "单条")
    r = c.get(f"/api/scripts/{sid}/worldbook/{eid}")
    assert r.status_code == 200, r.text
    entry = r.json()["entry"]
    assert entry["id"] == eid and entry["title"] == "单条"
    assert "embedding_vec" not in entry
    # 别的剧本的条目 id 不能从这个剧本的路径读出来
    other = _wb_create(env, "别处", sid=env["other_sid"])
    assert c.get(f"/api/scripts/{sid}/worldbook/{other}").status_code == 404


# ── 1.3 改了参与嵌入的文本 → 向量置空 ────────────────────────────────────────

def test_rest_put_text_change_clears_embedding(env):
    c, sid = env["client"], env["sid"]
    _insert(env, "云芝", "云芝", "character", summary="神秘女子")
    _set_fake_embedding(env, "云芝")
    # 不参与嵌入的字段(重要度)改了 → 向量保留
    assert c.put(f"/api/scripts/{sid}/canon-entities/云芝", json={"importance": 5}).status_code == 200
    assert _has_embedding(env, "云芝")
    # 值没变(md-editor 整体回写)→ 向量保留
    assert c.put(f"/api/scripts/{sid}/canon-entities/云芝",
                 json={"summary": "神秘女子", "name": "云芝"}).status_code == 200
    assert _has_embedding(env, "云芝")
    # 摘要真改了 → 置空,等重嵌
    assert c.put(f"/api/scripts/{sid}/canon-entities/云芝",
                 json={"summary": "云岚宗宗主"}).status_code == 200
    assert not _has_embedding(env, "云芝")
    _set_fake_embedding(env, "云芝")
    assert c.put(f"/api/scripts/{sid}/canon-entities/云芝", json={"aliases": ["云韵"]}).status_code == 200
    assert not _has_embedding(env, "云芝")


def test_agent_update_text_change_clears_embedding(env):
    from tools_dsl.command_tools_script_write.canon import _t_upsert_canon_entity
    _insert(env, "纳兰嫣然", "纳兰嫣然", "character", summary="旧")
    _set_fake_embedding(env, "纳兰嫣然")
    out = _t_upsert_canon_entity(env["uid"], env["sid"], {"logical_key": "纳兰嫣然", "importance": 3}, None)
    assert not out.startswith("失败"), out
    assert _has_embedding(env, "纳兰嫣然")
    out = _t_upsert_canon_entity(env["uid"], env["sid"], {"logical_key": "纳兰嫣然", "summary": "新"}, None)
    assert not out.startswith("失败"), out
    assert not _has_embedding(env, "纳兰嫣然")


def test_review_patch_text_change_clears_embedding(env):
    c, sid = env["client"], env["sid"]
    _insert(env, "rv_emb", "复核实体", "concept", summary="旧")
    _set_fake_embedding(env, "rv_emb")
    r = c.patch(f"/api/scripts/{sid}/canon",
                json={"op": "update_entity", "logical_key": "rv_emb", "summary": "新"})
    assert r.status_code == 200, r.text
    assert not _has_embedding(env, "rv_emb")


def test_extraction_upsert_text_change_clears_embedding(env):
    from kb import canon_repo
    _insert(env, "萧薰儿", "萧薰儿", "character", summary="旧摘要")
    _set_fake_embedding(env, "萧薰儿")
    with env["connect"]() as db:
        canon_repo.upsert_canon_entity(db, env["sid"], "萧薰儿", name="萧薰儿", type="character",
                                       summary="旧摘要")
        db.commit()
    assert _has_embedding(env, "萧薰儿")
    with env["connect"]() as db:
        canon_repo.upsert_canon_entity(db, env["sid"], "萧薰儿", name="萧薰儿", type="character",
                                       summary="新摘要")
        db.commit()
    assert not _has_embedding(env, "萧薰儿")


# ── 1.4 别名归并缓存会失效 ───────────────────────────────────────────────────

def test_alias_cache_invalidated_by_canon_edits(env):
    from kb import alias
    c, sid = env["client"], env["sid"]
    alias.invalidate_alias_cache()
    _insert(env, "云韵", "云韵", "character", aliases=["云芝"])
    assert alias._alias_to_canonical(sid, "云芝") == "云韵"
    # 编辑器把别名去掉 → 本进程立刻生效
    assert c.put(f"/api/scripts/{sid}/canon-entities/云韵", json={"aliases": []}).status_code == 200
    assert alias._alias_to_canonical(sid, "云芝") == "云芝"
    # 加回来再整条删掉
    assert c.put(f"/api/scripts/{sid}/canon-entities/云韵", json={"aliases": ["云芝"]}).status_code == 200
    assert alias._alias_to_canonical(sid, "云芝") == "云韵"
    assert c.delete(f"/api/scripts/{sid}/canon-entities/云韵").status_code == 200
    assert alias._alias_to_canonical(sid, "云芝") == "云芝"


def test_alias_cache_expires_for_other_workers(env, monkeypatch):
    """别的 worker 改的库本进程收不到失效通知:缓存有过期时间,过期后重新查库。"""
    from kb import alias
    sid = env["sid"]
    alias.invalidate_alias_cache()
    _insert(env, "美杜莎", "美杜莎", "character", aliases=["彩鳞"])
    assert alias._alias_to_canonical(sid, "彩鳞") == "美杜莎"
    with env["connect"]() as db:  # 模拟另一个进程直接改库(本进程没有 invalidate)
        db.execute("delete from kb_canon_entities where script_id=%s and logical_key='美杜莎'", (sid,))
        db.commit()
    assert alias._alias_to_canonical(sid, "彩鳞") == "美杜莎"  # TTL 内仍是缓存值
    real = alias._now
    monkeypatch.setattr(alias, "_now", lambda: real() + alias._ALIAS_TTL + 1)
    assert alias._alias_to_canonical(sid, "彩鳞") == "彩鳞"


# ── 1.6 删除实体时处理子实体引用 ─────────────────────────────────────────────

def test_delete_parent_clears_children_and_records_them(env):
    c, sid = env["client"], env["sid"]
    _insert(env, "德军", "德军", "faction")
    _insert(env, "铁人团", "铁人团", "faction", parent="德军")
    _insert(env, "无关", "无关", "faction", parent="别的")
    r = c.delete(f"/api/scripts/{sid}/canon-entities/德军")
    assert r.status_code == 200, r.text
    assert _row(env, "铁人团")["parent_logical_key"] == ""
    assert _row(env, "无关")["parent_logical_key"] == "别的"
    with env["connect"]() as db:
        payload = db.execute(
            "select payload from script_commits where script_id=%s and kind='canon_delete' "
            "order by id desc limit 1", (sid,),
        ).fetchone()["payload"]
    assert payload["detached_children"] == ["铁人团"]
    # 同名重建不会把旧子实体静默挂回来
    assert c.post(f"/api/scripts/{sid}/canon-entities", json={"name": "德军", "type": "faction"}).status_code == 200
    assert _row(env, "铁人团")["parent_logical_key"] == ""


def test_review_patch_delete_and_merge_handle_children(env):
    c, sid = env["client"], env["sid"]
    _insert(env, "a_parent", "甲", "faction")
    _insert(env, "a_child", "甲子", "faction", parent="a_parent")
    r = c.patch(f"/api/scripts/{sid}/canon", json={"op": "delete_entity", "logical_key": "a_parent"})
    assert r.status_code == 200, r.text
    assert _row(env, "a_child")["parent_logical_key"] == ""

    _insert(env, "m_from", "乙", "faction")
    _insert(env, "m_into", "乙军", "faction")
    _insert(env, "m_child", "乙子", "faction", parent="m_from")
    r = c.patch(f"/api/scripts/{sid}/canon",
                json={"op": "merge_entity", "from_key": "m_from", "into_key": "m_into"})
    assert r.status_code == 200, r.text
    assert _row(env, "m_from") is None
    assert _row(env, "m_child")["parent_logical_key"] == "m_into"  # 并入后跟着挂到合并目标下


# ── 1.7 logical_key 不能是会被 URL 规范化吃掉的点段 ──────────────────────────

def test_dot_segment_keys_rejected_and_never_generated(env):
    c, sid = env["client"], env["sid"]
    for bad in (".", ".."):
        r = c.post(f"/api/scripts/{sid}/canon-entities",
                   json={"name": "点", "type": "concept", "logical_key": bad})
        assert r.status_code == 400, (bad, r.text)
        assert "logical_key" in r.json()["error"]
    for nm in (".", ".."):
        r = c.post(f"/api/scripts/{sid}/canon-entities", json={"name": nm, "type": "character"})
        assert r.status_code == 200, r.text
        lk = r.json()["logical_key"]
        assert lk not in (".", "..")
        # 生成的 key 能按路径打开
        assert c.get(f"/api/scripts/{sid}/canon-entities/{lk}").status_code == 200


def test_agent_rejects_dot_segment_key(env):
    from tools_dsl.command_tools_script_write.canon import _t_upsert_canon_entity
    out = _t_upsert_canon_entity(env["uid"], env["sid"],
                                 {"logical_key": "..", "name": "点点", "type": "concept"}, None)
    assert out.startswith("失败") and "logical_key" in out
    assert _row(env, "..") is None
