"""kb/live_repo.py — Phase C 行级 COW 世界树读写。

模型(append-only,分支安全):
  · 写 = INSERT 一行,打 born_commit = 写入时 active commit_id(永不 UPDATE 既往行)
  · 删 = INSERT 一行 tombstone(retired_at_commit = born_commit),作为该 key 的最新版本遮蔽旧 live 行
  · 读 = 沿 active commit 的 parent_id 谱系(recursive CTE 取祖先 commit 集),
         每 logical_key 取祖先集内 born_commit 最大的行;若该行是 tombstone(retired_at_commit 非空)则视为删除
  · fork = 零拷贝(只新建 commit 指针,kb_* 行不动,自动继承祖先可见行)
  · 分支隔离 = 天然(只读自己谱系上的 commit 的行)

设计 docs/design/BC_kb_schema_worldtree.md §3。delete=tombstone 比"在旧行上写 retired"更安全:
绝不改既往行 → 兄弟分支共享的旧行不会被一个分支的删除影响。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

# 每张表的 logical column 集合(用于 read 投影 + upsert)
_ENTITY_COLS = ("logical_key", "name", "type", "status", "summary", "attrs", "origin", "metadata")
_EVENT_COLS = ("logical_key", "story_time", "summary", "participants", "location", "metadata")
_REL_COLS = ("logical_key", "from_key", "to_key", "kind", "note", "metadata")
_VAR_COLS = ("logical_key", "value")

# ── 祖先 commit 集 CTE 前缀(参数 :commit) ──────────────────────────────────
_ANCESTRY = """
with recursive ancestry(cid) as (
    select %(commit)s::bigint
  union all
    select bc.parent_id
    from branch_commits bc
    join ancestry a on bc.id = a.cid
    where bc.parent_id is not null
)
"""


def _newest_visible(db, table: str, save_id: int, commit_id: int, extra_cols: tuple[str, ...]) -> list[dict]:
    """沿谱系取每 logical_key 最新可见行(过滤 tombstone)。通用于 4 张行级表。"""
    cols = ", ".join(extra_cols)
    sql = (
        _ANCESTRY
        + f"""
        , visible as (
            select {cols}, born_commit, retired_at_commit,
                   row_number() over (
                     partition by logical_key
                     order by born_commit desc, id desc
                   ) as rn
            from {table}
            where save_id = %(save)s
              and born_commit in (select cid from ancestry)
        )
        select {cols} from visible
        where rn = 1 and retired_at_commit is null
        order by logical_key
        """
    )
    return db.execute(sql, {"commit": commit_id, "save": save_id}).fetchall()


# ── 写:upsert(INSERT 新版本行) ─────────────────────────────────────────────
def upsert_entity(db, save_id: int, commit_id: int, logical_key: str, *, name: str,
                  type: str, status: str = "active", summary: str = "",
                  attrs: dict | None = None, origin: str = "player",
                  metadata: dict | None = None) -> dict:
    return db.execute(
        """
        insert into kb_entities(save_id, born_commit, logical_key, name, type, status, summary, attrs, origin, metadata)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning *
        """,
        (save_id, commit_id, logical_key, name, type, status, summary,
         Jsonb(attrs or {}), origin, Jsonb(metadata or {})),
    ).fetchone()


def record_event(db, save_id: int, commit_id: int, logical_key: str, *, summary: str,
                 story_time: str = "", participants: list | None = None,
                 location: str = "", metadata: dict | None = None) -> dict:
    return db.execute(
        """
        insert into kb_events(save_id, born_commit, logical_key, story_time, summary, participants, location, metadata)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning *
        """,
        (save_id, commit_id, logical_key, story_time, summary,
         Jsonb(participants or []), location, Jsonb(metadata or {})),
    ).fetchone()


def set_relationship(db, save_id: int, commit_id: int, logical_key: str, *, from_key: str,
                     to_key: str, kind: str, note: str = "", metadata: dict | None = None) -> dict:
    return db.execute(
        """
        insert into kb_relationships(save_id, born_commit, logical_key, from_key, to_key, kind, note, metadata)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        returning *
        """,
        (save_id, commit_id, logical_key, from_key, to_key, kind, note, Jsonb(metadata or {})),
    ).fetchone()


def set_worldline_var(db, save_id: int, commit_id: int, logical_key: str, *, value: Any) -> dict:
    return db.execute(
        """
        insert into kb_worldline_vars(save_id, born_commit, logical_key, value)
        values (%s, %s, %s, %s)
        returning *
        """,
        (save_id, commit_id, logical_key, Jsonb(value)),
    ).fetchone()


# ── 删:tombstone(INSERT retired 行) ────────────────────────────────────────
def _retire(db, table: str, save_id: int, commit_id: int, logical_key: str, extra: dict) -> None:
    """通用 tombstone:INSERT 一行 born_commit=commit_id 且 retired_at_commit=commit_id。"""
    base = {"save_id": save_id, "born_commit": commit_id, "logical_key": logical_key,
            "retired_at_commit": commit_id, **extra}
    cols = ", ".join(base.keys())
    ph = ", ".join(["%s"] * len(base))
    db.execute(f"insert into {table}({cols}) values ({ph})", tuple(base.values()))


def retire_entity(db, save_id: int, commit_id: int, logical_key: str) -> None:
    _retire(db, "kb_entities", save_id, commit_id, logical_key,
            {"name": "", "type": "", "status": "retired"})


def retire_event(db, save_id: int, commit_id: int, logical_key: str) -> None:
    _retire(db, "kb_events", save_id, commit_id, logical_key, {"summary": ""})


def retire_relationship(db, save_id: int, commit_id: int, logical_key: str) -> None:
    _retire(db, "kb_relationships", save_id, commit_id, logical_key, {})


def retire_worldline_var(db, save_id: int, commit_id: int, logical_key: str) -> None:
    _retire(db, "kb_worldline_vars", save_id, commit_id, logical_key, {})


# ── 读:当前分支可见集 ────────────────────────────────────────────────────────
def read_entities(db, save_id: int, commit_id: int) -> list[dict]:
    return _newest_visible(db, "kb_entities", save_id, commit_id, _ENTITY_COLS)


def read_events(db, save_id: int, commit_id: int) -> list[dict]:
    return _newest_visible(db, "kb_events", save_id, commit_id, _EVENT_COLS)


def read_relationships(db, save_id: int, commit_id: int) -> list[dict]:
    return _newest_visible(db, "kb_relationships", save_id, commit_id, _REL_COLS)


def read_worldline_vars(db, save_id: int, commit_id: int) -> list[dict]:
    return _newest_visible(db, "kb_worldline_vars", save_id, commit_id, _VAR_COLS)


def live_world_view(db, save_id: int, commit_id: int) -> dict[str, list[dict]]:
    """当前分支(active commit)的活态世界现状:newest-per-key,过滤 tombstone。"""
    return {
        "entities": read_entities(db, save_id, commit_id),
        "events": read_events(db, save_id, commit_id),
        "relationships": read_relationships(db, save_id, commit_id),
        "worldline_vars": read_worldline_vars(db, save_id, commit_id),
    }


# ── 检查点(读加速,可由行级表重建) ─────────────────────────────────────────
def write_checkpoint(db, save_id: int, commit_id: int) -> dict:
    snapshot = live_world_view(db, save_id, commit_id)
    return db.execute(
        """
        insert into kb_checkpoints(save_id, commit_id, snapshot)
        values (%s, %s, %s)
        on conflict(save_id, commit_id) do update set snapshot = excluded.snapshot, created_at = now()
        returning *
        """,
        (save_id, commit_id, Jsonb(snapshot)),
    ).fetchone()
