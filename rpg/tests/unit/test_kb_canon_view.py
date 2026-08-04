"""Phase B/C — kb.canon_repo + kb.view: 进度过滤(防剧透)/元知识/canon∪live 合并。

live DB,无 DB 则跳过。
"""
from __future__ import annotations

import pytest


def _db_or_skip():
    try:
        from platform_app.db import connect, init_db
        init_db()
        return connect
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"无 DB: {exc}")


def test_anti_spoiler_foreknowledge_and_merge():
    connect = _db_or_skip()
    from kb import canon_repo as C
    from kb import live_repo as L
    from kb import view as V

    with connect() as db:
        uid = db.execute("select id from users order by id limit 1").fetchone()
        if not uid:
            pytest.skip("无 user 种子")
        uid = uid["id"]
        sid = db.execute(
            "insert into scripts(owner_id,title,source_path,chapter_count,word_count) "
            "values (%s,%s,%s,%s,%s) returning id",
            (uid, "BC-pytest剧本", "/tmp/x", 100, 1000),
        ).fetchone()["id"]
        try:
            save_id = db.execute(
                "insert into game_saves(user_id,script_id,title,state_path) values (%s,%s,%s,%s) returning id",
                (uid, sid, "s", "/tmp/s.json"),
            ).fetchone()["id"]
            c1 = db.execute(
                "insert into branch_commits(save_id,parent_id,object_hash,tree_hash,turn_index,kind,title) "
                "values (%s,null,%s,%s,0,%s,%s) returning id",
                (save_id, "h1", "t1", "round", "c1"),
            ).fetchone()["id"]

            C.upsert_canon_entity(db, sid, "hero", name="主角", type="character", first_revealed_chapter=1)
            C.upsert_canon_entity(db, sid, "villain", name="反派", type="character", first_revealed_chapter=80)
            C.upsert_canon_entity(db, sid, "empire", name="帝国", type="faction",
                                  first_revealed_chapter=200, public_knowledge=True)
            C.upsert_canon_entity(db, sid, "doomsday", name="终焉", type="concept",
                                  first_revealed_chapter=300, metadata={"famous": True})

            def keys(mode):
                return {e["logical_key"] for e in C.read_canon_entities(db, sid, progress_chapter=50, mode=mode)}

            assert keys("none") == {"hero", "empire"}
            assert keys("partial") == {"hero", "empire", "doomsday"}
            assert keys("omniscient") == {"hero", "villain", "empire", "doomsday"}

            C.upsert_worldline(db, sid, "main", label="主线", is_primary=True)
            C.upsert_worldline_node(db, sid, "main", "n1", seq=1, label="柏林开局", chapter_min=1, chapter_max=30)
            wls = V.steering_context(db, script_id=sid, progress_chapter=50)["worldlines"]
            assert wls[0]["nodes"][0]["label"] == "柏林开局"

            L.upsert_entity(db, save_id, c1, "hero", name="主角", type="character",
                            summary="玩家改", origin="canon_override")
            L.upsert_entity(db, save_id, c1, "sidekick", name="伙伴", type="character",
                            summary="新造", origin="player")
            view = V.resolve_world_view(db, script_id=sid, save_id=save_id, commit_id=c1,
                                        progress_chapter=50, mode="none")
            by = {e["logical_key"]: (e["summary"], e["_source"]) for e in view["entities"]}
            assert by["hero"] == ("玩家改", "live_override")
            assert by["sidekick"] == ("新造", "live_new")
            assert by["empire"][1] == "canon"
            assert "villain" not in by
        finally:
            db.execute("delete from scripts where id=%s", (sid,))
