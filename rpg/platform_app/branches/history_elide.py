"""branches/history_elide.py — 世界树历史祖先裁剪(存储 O(n²)→O(n))。

库解剖实锤(save 268):每个 commit 的 state_snapshot 都全量复制整份对话 history
(turn650=1301 条/2.9MB),754 个 commit 累计≈65 万条消息拷贝(不重复的仅 1301 条),
单档独占 branch_commits 表 76%(734MB)。

原理:history 是 append-only 的 ⇒ 任何【有后代】的 commit,其 history 恒为后代
history 的前缀 ⇒ 物理裁剪为 `_history_elided:{count:N}` 标记,恢复时从任意足量
后代取前 N 条无损重建。逻辑语义不变:每个 commit 仍"拥有"完整历史,只是惰性存储。

不裁剪(重建供体/热路径):
  - 各 save 的 game_saves.active_commit_id(工作树/materialize/导出/换稿的权威源);
  - 所有 branch_refs.target_commit_id(含 head/pin/trash——恢复入口);
  - 叶子 commit(无后代=无供体,天然全量);
  - history < MIN_HISTORY_TO_ELIDE 的(裁了不省多少)。

已知语义点(刻意接受,352 e2e 实证):history 条目的附属字段(reasoning 思考流/
tool_ops 工具痕迹)会被平台既有清理机制在后续回合剥离——hydrate 重建以后代版本
为准,这些字段可能缺失;**content/role(叙事正文)经逐条比对严格无损**,这是裁剪的
无损铁标。acceptance 换稿同理:祖先"看到"换稿后版本=玩家钦定稿全局生效。

恢复接线:refs._write_checkout(所有 commit→工作树拷贝的单一入口)与 deletion 三
回退路径改走 hydrate_commit_state;被恢复为活跃头的裁剪 commit 由 _write_checkout
un-elide 回写全量(它将成为新的重建供体)。
"""
from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

MIN_HISTORY_TO_ELIDE = 20


def hydrate_commit_state(db, save_id: int, commit_row: dict[str, Any]) -> dict[str, Any]:
    """commit_state + 裁剪快照的历史无损重建。恢复路径一律经此,勿直调 commit_state。

    未裁剪快照原样返回(零开销);裁剪快照沿子树找最近的足量供体,取前 N 条。
    无足量供体=不变量被破坏,抛错(宁失败勿静默丢历史)。"""
    from platform_app.branches._helpers import commit_state
    snap = commit_state(commit_row or {})
    marker = snap.get("_history_elided") if isinstance(snap, dict) else None
    if not isinstance(marker, dict):
        return snap
    n = int(marker.get("count") or 0)
    cid = int((commit_row or {}).get("id") or 0)
    if n <= 0:
        snap["history"] = []
        snap.pop("_history_elided", None)
        return snap
    donor = db.execute(
        """
        with recursive d as (
            select id, parent_id, state_snapshot, 1 as depth
              from branch_commits where save_id = %s and parent_id = %s
            union all
            select c.id, c.parent_id, c.state_snapshot, d.depth + 1
              from branch_commits c join d on c.parent_id = d.id
             where c.save_id = %s
        )
        select state_snapshot from d
        where not (state_snapshot ? '_history_elided')
          and jsonb_array_length(coalesce(state_snapshot->'history', '[]'::jsonb)) >= %s
        order by depth asc limit 1
        """,
        (int(save_id), cid, int(save_id), n),
    ).fetchone()
    hist = (((donor or {}).get("state_snapshot") or {}).get("history")) or []
    if len(hist) < n:
        raise RuntimeError(f"history hydrate 失败: commit {cid} 需要 {n} 条,无足量后代供体")
    snap["history"] = hist[:n]
    snap.pop("_history_elided", None)
    return snap


def unelide_commit(db, save_id: int, commit_id: int, full_snapshot: dict[str, Any]) -> None:
    """把重建后的全量快照写回 commit 行(退出裁剪态)。

    恢复到裁剪 commit 时必须调用:它即将成为活跃头=materialize/导出/换稿的权威源
    与后续裁剪的重建供体,必须物理全量。"""
    db.execute(
        "update branch_commits set state_snapshot = %s where id = %s and save_id = %s",
        (Jsonb(full_snapshot), int(commit_id), int(save_id)),
    )


def protected_commit_ids(db, save_id: int) -> set[int]:
    out: set[int] = set()
    r = db.execute(
        "select active_commit_id from game_saves where id = %s", (int(save_id),)).fetchone()
    if r and r.get("active_commit_id"):
        out.add(int(r["active_commit_id"]))
    for row in db.execute(
            "select target_commit_id from branch_refs where save_id = %s "
            "and target_commit_id is not null", (int(save_id),)).fetchall():
        out.add(int(row["target_commit_id"]))
    return out


def elide_save(db, save_id: int, *, min_history: int = MIN_HISTORY_TO_ELIDE,
               dry_run: bool = False) -> dict[str, Any]:
    """单存档 compaction(调用方须持该 save 的 advisory lock,与回合/分支操作互斥)。

    可裁剪 = 有后代 ∧ 非保护集 ∧ 未裁剪 ∧ history≥min_history。
    单条 UPDATE 纯 SQL 原子改写(不经 python 读回,防大 blob 往返)。"""
    prot = protected_commit_ids(db, int(save_id))
    rows = db.execute(
        """
        select c.id,
               jsonb_array_length(coalesce(c.state_snapshot->'history','[]'::jsonb)) as n,
               pg_column_size(c.state_snapshot) as bytes
        from branch_commits c
        where c.save_id = %s
          and c.state_snapshot is not null
          and not (c.state_snapshot ? '_history_elided')
          and jsonb_array_length(coalesce(c.state_snapshot->'history','[]'::jsonb)) >= %s
          and exists (select 1 from branch_commits k
                      where k.save_id = %s and k.parent_id = c.id)
        """,
        (int(save_id), int(min_history), int(save_id)),
    ).fetchall()
    todo = [r for r in (rows or []) if int(r["id"]) not in prot]
    bytes_before = sum(int(r["bytes"] or 0) for r in todo)
    if dry_run:
        return {"save_id": int(save_id), "elided": 0, "candidates": len(todo),
                "bytes_before": bytes_before, "dry_run": True}
    n_done = n_skipped = 0
    for r in todo:
        # 前缀实测(268 e2e 实锤:重试/换稿会让树上同回合存在多个正文版本,4.2% 的
        # commit 与后代前缀不符——对这些不猜语义,实测 content/role 前缀不成立就跳过
        # 保持全量,保证已裁剪集合的绝对无损)。比对在 DB 内做(只传 bool 不回传大 blob)。
        ok = db.execute(
            """
            with cand as (
                select state_snapshot->'history' as h,
                       jsonb_array_length(state_snapshot->'history') as n
                from branch_commits where id = %s and save_id = %s
            ),
            donor as (
                with recursive d as (
                    select id, parent_id, state_snapshot, 1 as depth
                      from branch_commits where save_id = %s and parent_id = %s
                    union all
                    select c.id, c.parent_id, c.state_snapshot, d.depth + 1
                      from branch_commits c join d on c.parent_id = d.id
                     where c.save_id = %s
                )
                select state_snapshot->'history' as h from d
                where not (state_snapshot ? '_history_elided')
                  and jsonb_array_length(coalesce(state_snapshot->'history','[]'::jsonb))
                      >= (select n from cand)
                order by depth asc limit 1
            )
            select coalesce(bool_and(
                     a.e->>'content' is not distinct from b.e->>'content'
                     and a.e->>'role' is not distinct from b.e->>'role'), false) as ok
            from cand, donor,
                 lateral jsonb_array_elements(cand.h) with ordinality a(e, i),
                 lateral jsonb_array_elements(donor.h) with ordinality b(e, i2)
            where a.i = b.i2 and a.i <= (select n from cand)
            """,
            (int(r["id"]), int(save_id), int(save_id), int(r["id"]), int(save_id)),
        ).fetchone()
        if not (ok and ok.get("ok")):
            n_skipped += 1
            continue
        db.execute(
            """
            update branch_commits
               set state_snapshot = (state_snapshot - 'history')
                   || jsonb_build_object(
                        'history', '[]'::jsonb,
                        '_history_elided', jsonb_build_object(
                            'count', jsonb_array_length(coalesce(state_snapshot->'history','[]'::jsonb))))
             where id = %s and save_id = %s
               and not (state_snapshot ? '_history_elided')
            """,
            (int(r["id"]), int(save_id)),
        )
        n_done += 1
    return {"save_id": int(save_id), "elided": n_done, "candidates": len(todo),
            "skipped_prefix_mismatch": n_skipped,
            "bytes_before": bytes_before, "protected": len(prot)}
