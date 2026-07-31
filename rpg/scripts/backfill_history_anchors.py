#!/usr/bin/env python3
"""backfill_history_anchors.py — 把「级联接线之前」已发生的锚点迁移补记成历史锚点。

为什么需要它:v1.78.2 之前,历史锚点级联挂在 GM 工具 `mark_anchor_*` 上,而真正在标锚点
的是 `anchor_reconcile` 的每回合确定性兜底 —— 于是那段时间里所有「剧情到达锚点」都没留档,
玩家的「存档独立时间线」冻在建档初期(群反馈:「存档永远只有 2 个锚点」)。v1.78.2 修好了
往后的路,这个脚本负责往回补。

安全设计(这是往真实玩家存档里写数据):
  · **只 INSERT**,不 UPDATE、不 DELETE、不碰 save_anchor_states 本身
  · 走**和线上完全同一条缝** `cascade_history_from_anchor` —— 不写第二份逻辑,
    补出来的行与线上自然产生的行逐字段同形
  · 去重同样靠 `linked_pending_anchors @> [key]`,**重复跑不会双写**
  · 写入行带 `metadata.backfill=true`,万一不对可一条 SQL 精确撤回:
      delete from save_history_anchors
       where save_id=<id> and (metadata->>'backfill')::boolean is true;
  · **默认 dry-run**,必须显式 --apply 才落库

哪些迁移会被补记(与线上级联的口径完全一致):
  ✓ occurred / variant —— 「剧情到达了这个锚点」,是真发生的事
  ✓ GM 工具或玩家手动标记的 superseded —— 玩家改写了原著走向
  ✗ 角色死亡导致的单人锚点失效、phase 关闭时的批量自动绕过
     —— 这两类是「不再可能发生」,不是「发生了什么」,补进去只会刷屏(线上同样豁免)

用法:
    python -m scripts.backfill_history_anchors --save-id 123            # 预演
    python -m scripts.backfill_history_anchors --save-id 123 --apply    # 落库
"""
from __future__ import annotations

import argparse
import sys

# 批量退役的描述指纹 —— 这两类刻意不补(理由见模块头)。
_BULK_RETIREMENT_MARKERS = ("角色「", "已结束 (turn", "自动绕过")
# 系统兜底判定的描述指纹(anchor_reconcile 写的固定串)。
_SYSTEM_MARKER = "系统每回合确定性兜底判定"
_PLAYER_MARKER = "玩家在世界线面板手动标记已到达"


def _source_of(variant_description: str) -> str:
    """按描述指纹还原「当初是谁标的」,与线上三个写入方的 source 一一对应。"""
    d = variant_description or ""
    if d.startswith(_SYSTEM_MARKER):
        return "system"
    if d.startswith(_PLAYER_MARKER):
        return "player_declared"
    return "gm_generated"


def _is_bulk_retirement(variant_description: str) -> bool:
    d = variant_description or ""
    return any(m in d for m in _BULK_RETIREMENT_MARKERS)


def collect(db, save_id: int) -> list[dict]:
    """列出该存档所有「该补而未补」的锚点迁移(按发生回合升序,时间线才顺)。"""
    rows = db.execute(
        """
        select s.anchor_key, s.status, s.summary, s.variant_description,
               s.occurred_at_turn, s.updated_at
          from save_anchor_states s
         where s.save_id = %s
           and s.status in ('occurred', 'variant', 'superseded')
           and coalesce(s.anchor_key, '') <> ''
           and not exists (
                 select 1 from save_history_anchors h
                  where h.save_id = s.save_id
                    and h.linked_pending_anchors @> to_jsonb(array[s.anchor_key]))
         order by coalesce(s.occurred_at_turn, 0), s.updated_at
        """,
        (int(save_id),),
    ).fetchall() or []
    out = []
    for r in rows:
        r = dict(r)
        if _is_bulk_retirement(r.get("variant_description") or ""):
            continue
        out.append(r)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="补记历史锚点(幂等,默认预演)")
    ap.add_argument("--save-id", type=int, required=True)
    ap.add_argument("--apply", action="store_true", help="真正落库(不给就只预演)")
    ap.add_argument("--limit", type=int, default=0, help="最多补几条(0=不限)")
    args = ap.parse_args(argv)

    from agents.save_history import cascade_history_from_anchor, history_summary
    from platform_app.db import connect, init_db

    init_db()
    with connect() as db:
        before = history_summary(args.save_id)
        todo = collect(db, args.save_id)
        if args.limit > 0:
            todo = todo[: args.limit]

        by_status: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for r in todo:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
            src = _source_of(r.get("variant_description") or "")
            by_source[src] = by_source.get(src, 0) + 1

        print(f"存档 {args.save_id} 现有历史锚点: {before['total']} 条 "
              f"(系统判定 {before.get('system_count', 0)} / GM 写 {before['gm_count']} / "
              f"玩家声明 {before['player_count']})")
        print(f"待补记: {len(todo)} 条  按状态 {by_status}  按来源 {by_source}")
        if todo:
            lo = todo[0].get("occurred_at_turn")
            hi = todo[-1].get("occurred_at_turn")
            print(f"回合范围: {lo} → {hi}")
        if not args.apply:
            print("\n[预演] 没有写入任何数据。确认无误后加 --apply 重跑。")
            return 0

        written = 0
        for r in todo:
            cascade_history_from_anchor(
                args.save_id,
                anchor_key=r["anchor_key"],
                anchor_summary=r.get("summary") or "",
                new_status=r["status"],
                detail=r.get("variant_description") or "",
                turn_occurred=r.get("occurred_at_turn"),
                source=_source_of(r.get("variant_description") or ""),
                via="backfill",
                db=db,          # 复用本连接,别在持连接的块里再开连接
                extra_metadata={"backfill": True},
            )
            written += 1

        after = history_summary(args.save_id)
        print(f"\n已补记 {written} 条。现有: {after['total']} 条 "
              f"(系统判定 {after.get('system_count', 0)} / GM 写 {after['gm_count']} / "
              f"玩家声明 {after['player_count']})")
        print("撤回(如需): delete from save_history_anchors "
              f"where save_id={args.save_id} and (metadata->>'backfill')::boolean is true;")
    return 0


if __name__ == "__main__":
    sys.exit(main())
