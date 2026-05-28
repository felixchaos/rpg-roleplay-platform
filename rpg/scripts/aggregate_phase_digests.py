#!/usr/bin/env python3
"""task 85: 把 chapter_facts 按 story_phase 聚合成 phase_digests。

通用脚本: 对任意 script_id 都能跑。chapter_facts 一行一章, 同 phase 多章合并:
  - chapter_min/max = phase 内章节范围
  - summary = 拼接每章 summary, 截断 3000 字
  - key_events = 合并各章 events_json, 去重保留前 N
  - key_locations / characters = 同上, 按出现频次降序

用法:
  python aggregate_phase_digests.py [--script SCRIPT_ID]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/Volumes/我的电脑/我穆蕾莉娅不爱你/我蕾穆丽娜不爱你/rpg")

from psycopg.types.json import Jsonb  # noqa: E402

from platform_app.db import connect, init_db  # noqa: E402


def aggregate_for_script(script_id: int) -> int:
    init_db()
    with connect() as db:
        rows = db.execute(
            """select chapter, story_phase, story_time_label, summary,
                   events, locations, characters
               from chapter_facts where script_id=%s order by chapter""",
            (script_id,),
        ).fetchall()
        if not rows:
            return 0
        # group by phase
        by_phase: dict[str, list[dict]] = {}
        for r in rows:
            phase = (r["story_phase"] or "").strip() or "未分组"
            by_phase.setdefault(phase, []).append(dict(r))

        # clear old
        db.execute("delete from phase_digests where script_id=%s", (script_id,))

        n = 0
        for phase, chapters in by_phase.items():
            chs = [c["chapter"] for c in chapters]
            cmin, cmax = min(chs), max(chs)
            # summary: 拼一段
            summary_parts = []
            for c in chapters[:50]:  # 防爆
                s = (c.get("summary") or "").strip()
                if s:
                    summary_parts.append(f"第{c['chapter']}章 · {s[:120]}")
            summary = "\n".join(summary_parts)[:3000]
            # 时间标签
            tls = [c.get("story_time_label") or "" for c in chapters if c.get("story_time_label")]
            tl_start = tls[0] if tls else ""
            tl_end = tls[-1] if tls else ""
            # events
            ev_counter = Counter()
            ev_entries = []
            for c in chapters:
                for ev in (c.get("events") or [])[:5]:
                    if isinstance(ev, dict):
                        text = str(ev.get("event") or "").strip()
                        if text and text not in ev_counter:
                            ev_counter[text] += 1
                            ev_entries.append({"chapter": c["chapter"], "event": text})
            key_events = ev_entries[:30]
            # locations / characters
            loc_counter = Counter()
            for c in chapters:
                for loc in (c.get("locations") or []):
                    name = loc.get("name") if isinstance(loc, dict) else str(loc)
                    if name:
                        loc_counter[name] += loc.get("count", 1) if isinstance(loc, dict) else 1
            key_locations = [{"name": n, "freq": cnt} for n, cnt in loc_counter.most_common(15)]
            char_counter = Counter()
            for c in chapters:
                for ch in (c.get("characters") or []):
                    name = ch.get("name") if isinstance(ch, dict) else str(ch)
                    if name:
                        char_counter[name] += ch.get("count", 1) if isinstance(ch, dict) else 1
            key_characters = [{"name": n, "freq": cnt} for n, cnt in char_counter.most_common(15)]

            db.execute(
                """insert into phase_digests(
                  script_id, phase_label, chapter_min, chapter_max, summary,
                  key_events, key_locations, key_characters,
                  story_time_label_start, story_time_label_end, chapter_count
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (script_id, phase, cmin, cmax, summary,
                 Jsonb(key_events), Jsonb(key_locations), Jsonb(key_characters),
                 tl_start, tl_end, len(chapters)),
            )
            n += 1
            print(f"  phase={phase!r} ch={cmin}-{cmax} ({len(chapters)} 章) events={len(key_events)} locs={len(key_locations)} chars={len(key_characters)}")
        return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=int, default=None)
    args = ap.parse_args()
    if args.script:
        scripts = [args.script]
    else:
        # 跑所有已 import 的 script
        with connect() as db:
            scripts = [r["id"] for r in db.execute(
                "select id from scripts where chapter_count > 0"
            ).fetchall()]
    for sid in scripts:
        print(f"Aggregating script_id={sid} ...")
        n = aggregate_for_script(sid)
        print(f"  → {n} phases")


if __name__ == "__main__":
    main()
