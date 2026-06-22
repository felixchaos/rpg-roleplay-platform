"""kb.t0_seed — 剧本知识库(T0)→ 存档知识库 的 seed 工具(原型,验证架构方向)。

设计方向(用户拍板,见 memory/project_kb_dbization_architecture):
  · 剧本知识库 = T0 基线(kb_canon_entities 等,script_id 域,只读、归剧本 owner)。
  · 存档知识库 = 用户级 save 域、**字段继承自剧本**、从剧本 **T0 seed**、游戏中确定性向前演化。
  · 单一来源:存档 KB 就是 save 的 kb_entities(COW 行级表),不再依赖 JSONB blob / JSON 文件。
  · seed = **定向读取工具**:keys=None 全量 T0 镜像(有剧本的存档);keys=[...] 定向懒加载
    (酒馆无 T0 / 对话中按需拉某实体)。
  · 绝不改剧本 KB:只 SELECT kb_canon_entities;写只落 save 域 kb_entities。

本模块是「create_save 的 T0 seed 步骤」与「按需 materialize 工具」的共用底座。
"""
from __future__ import annotations

from typing import Any

from kb import live_repo


def root_commit_id(db, save_id: int) -> int | None:
    """存档的根(seed)commit = 该 save 最早的 branch_commits 行。T0 实体 born 在此。"""
    r = db.execute(
        "select min(id) as c from branch_commits where save_id = %s", (save_id,)
    ).fetchone()
    return int(r["c"]) if r and r.get("c") is not None else None


def seed_save_kb_from_script(
    db,
    save_id: int,
    script_id: int,
    *,
    commit_id: int | None = None,
    keys: list[str] | None = None,
) -> dict[str, Any]:
    """把剧本 canon 实体 seed 进存档 kb_entities(born_commit = 根 commit,= T0)。

    keys=None  → 全量 T0 镜像;keys=[...] → 只 materialize 指定 logical_key(定向/懒)。
    已存在于存档 KB 的 logical_key 跳过(幂等,不重复 seed)。返回 {seeded, skipped, commit}。
    """
    cid = commit_id if commit_id is not None else root_commit_id(db, save_id)
    if not cid:
        return {"seeded": 0, "skipped": 0, "commit": None, "error": "no root commit"}

    where = "script_id = %s"
    params: list[Any] = [int(script_id)]
    if keys:
        where += " and logical_key = any(%s)"
        params.append(list(keys))
    rows = db.execute(
        f"""select logical_key, name, type, summary, attrs, identity, background,
                   aliases, full_name, first_revealed_chapter, importance
            from kb_canon_entities where {where}""",
        params,
    ).fetchall()

    existing = {
        r["logical_key"]
        for r in db.execute(
            "select distinct logical_key from kb_entities where save_id = %s", (save_id,)
        ).fetchall()
    }

    seeded = skipped = 0
    for r in rows:
        lk = r["logical_key"]
        if lk in existing:
            skipped += 1
            continue
        # 继承剧本字段:identity / background / aliases / full_name / 首现章 → 并进 attrs。
        attrs = dict(r.get("attrs") or {})
        for k in ("identity", "background", "full_name"):
            if r.get(k):
                attrs.setdefault(k, r[k])
        if r.get("aliases"):
            attrs.setdefault("aliases", r["aliases"])
        attrs["_t0"] = True  # 标记 T0-seed 行(运行时镜像的初始态,非玩家产生)
        live_repo.upsert_entity(
            db, save_id, cid, lk,
            name=r["name"], type=(r.get("type") or "entity"),
            status="live", summary=(r.get("summary") or ""),
            attrs=attrs, origin="script_seed",
            metadata={"first_revealed_chapter": r.get("first_revealed_chapter"),
                      "importance": r.get("importance")},
        )
        seeded += 1
    return {"seeded": seeded, "skipped": skipped, "commit": cid}


def read_save_world_entities(db, save_id: int, commit_id: int) -> list[dict]:
    """读存档知识库当前可见实体(COW 沿谱系取每 key 最新可见行,= 运行时态)。"""
    return live_repo._newest_visible(
        db, "kb_entities", save_id, commit_id,
        ("logical_key", "name", "type", "status", "summary", "attrs", "origin", "metadata"),
    )
