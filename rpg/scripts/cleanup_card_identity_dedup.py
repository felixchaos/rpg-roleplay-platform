"""cleanup_card_identity_dedup — 零 LLM 存量清洗:角色卡 / canon 张冠李戴。

修复历史数据中由旧提取逻辑产生的四类问题(与已上线的 extract/resolve.py 预防规则一致):
  1. 简繁/全半角未归一 → 同一角色出现繁简两张卡(如「杨过」与「楊過」)。
  2. 光杆泛指 / 关系泛称被当别名(那人 / 老头 / 姐姐 / 女朋友 ...)。
  3. 串味别名:A 的别名里混进 B 的主名(如「薇欧拉」误含「妮娅 / 艾森豪威尔」)。
  4. 繁体主名。

处理对象:character_cards(card_type='npc')+ kb_canon_entities(type='character')。
两者都清,因为「重做角色卡」会从 canon 回灌卡(rebuild_cards_from_canon),只清卡会被还原。

合并安全性:character_cards 输家先把 card_persona_images / game_saves 引用 repoint 到赢家再删;
kb_canon_entities 无入向外键,直接删输家。全程单事务,--apply 才落库。

用法:
  cd rpg && ./.venv/bin/python scripts/cleanup_card_identity_dedup.py --dry-run
  cd rpg && ./.venv/bin/python scripts/cleanup_card_identity_dedup.py --apply
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from psycopg.types.json import Jsonb

from extract.resolve import _is_generic_referent, _norm_name, _to_simplified
from platform_app.db import connect


def clean_aliases(aliases, sid, own_norm, prim_norm_by_sid):
    """归一简体 + 去泛指 + 去串味(等于本剧本另一卡主名)+ 去重。保留与自身同名的别名,
    以与已上线 resolve.py anti-bleed 行为一致(产出『用新代码重抽等价』的数据)。"""
    out, seen = [], set()
    for a in aliases or []:
        a2 = _to_simplified((a or "").strip())
        if not a2:
            continue
        na = _norm_name(a2)
        if not na:
            continue
        if _is_generic_referent(a2):
            continue
        if na in prim_norm_by_sid.get(sid, set()) and na != own_norm:
            continue
        if na in seen:
            continue
        seen.add(na)
        out.append(a2)
    return out


def plan_merges(records):
    """records:[{id,sid,name,aliases,importance}] → (survivors, merges)。
    survivors 含合并后赢家(name 已归一简体、aliases 已并入输家);merges:[(winner_id,[loser_ids])]。"""
    groups = defaultdict(list)
    for r in records:
        groups[(r["sid"], _to_simplified((r["name"] or "").strip()))].append(r)
    merges, survivors, merged_winner_ids = [], [], set()
    for (sid, simp), grp in groups.items():
        if len(grp) == 1:
            survivors.append(grp[0])
            continue
        grp_sorted = sorted(grp, key=lambda r: (-int(r["importance"] or 0), r["id"]))
        winner, losers = dict(grp_sorted[0]), grp_sorted[1:]
        aliases = list(winner.get("aliases") or [])
        for lo in losers:
            aliases.extend(lo.get("aliases") or [])
            aliases.append(lo["name"])          # 输家原名 → 赢家别名
        winner["aliases"] = aliases
        winner["name"] = simp                   # 赢家主名归一简体
        survivors.append(winner)
        merged_winner_ids.add(winner["id"])
        merges.append((winner["id"], [lo["id"] for lo in losers]))
    return survivors, merges, merged_winner_ids


def process_table(db, table, type_col, type_val):
    rows = db.execute(
        f"select id, script_id as sid, name, coalesce(aliases,'[]'::jsonb) as aliases, "
        f"coalesce(importance,0) as importance from {table} where {type_col}=%s",
        (type_val,),
    ).fetchall()
    for r in rows:
        if isinstance(r["aliases"], str):
            r["aliases"] = json.loads(r["aliases"])
    survivors, merges, merged_winner_ids = plan_merges(rows)
    prim_norm_by_sid = defaultdict(set)
    for s in survivors:
        prim_norm_by_sid[s["sid"]].add(_norm_name(_to_simplified(s["name"])))
    updates = []
    for s in survivors:
        new_name = _to_simplified((s["name"] or "").strip())
        own_norm = _norm_name(new_name)
        new_aliases = clean_aliases(s["aliases"], s["sid"], own_norm, prim_norm_by_sid)
        # 合并赢家强制写(其 name 已被预置成简体,常规 diff 会漏判);其余按差异判定。
        forced = s["id"] in merged_winner_ids
        if forced or new_name != s["name"] or new_aliases != list(s["aliases"] or []):
            updates.append((s["id"], new_name, new_aliases))
    return merges, updates, len(rows)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="只报告不写(默认)")
    g.add_argument("--apply", action="store_true", help="落库(单事务)")
    args = ap.parse_args()
    apply = bool(args.apply)

    with connect() as db:
        c_merges, c_updates, c_total = process_table(db, "character_cards", "card_type", "npc")
        k_merges, k_updates, k_total = process_table(db, "kb_canon_entities", "type", "character")

        print(f"=== MODE: {'APPLY' if apply else 'DRY-RUN'} ===")
        print(f"[character_cards] total={c_total} merges={len(c_merges)} "
              f"losers={sum(len(l) for _, l in c_merges)} updates={len(c_updates)}")
        for w, losers in c_merges:
            print(f"   merge winner #{w} <= losers {losers}")
        print(f"[kb_canon_entities] total={k_total} merges={len(k_merges)} "
              f"losers={sum(len(l) for _, l in k_merges)} updates={len(k_updates)}")

        if not apply:
            print("DRY-RUN: 无写入。加 --apply 落库。")
            db.rollback()
            return

        for w, losers in c_merges:
            for lo in losers:
                db.execute("update card_persona_images set card_id=%s where card_id=%s", (w, lo))
                db.execute("update game_saves set tavern_character_card_id=%s where tavern_character_card_id=%s", (w, lo))
                db.execute("update game_saves set tavern_persona_card_id=%s where tavern_persona_card_id=%s", (w, lo))
                db.execute("delete from character_cards where id=%s", (lo,))
        for _w, losers in k_merges:
            for lo in losers:
                db.execute("delete from kb_canon_entities where id=%s", (lo,))
        for cid, nn, na in c_updates:
            db.execute("update character_cards set name=%s, aliases=%s, row_version=row_version+1, "
                       "updated_at=now() where id=%s", (nn, Jsonb(na), cid))
        for kid, nn, na in k_updates:
            db.execute("update kb_canon_entities set name=%s, aliases=%s where id=%s", (nn, Jsonb(na), kid))
        # connect() 上下文正常退出即提交
        print(f"APPLIED. cards: -{sum(len(l) for _, l in c_merges)} merged / {len(c_updates)} updated; "
              f"canon: -{sum(len(l) for _, l in k_merges)} merged / {len(k_updates)} updated.")


if __name__ == "__main__":
    main()
