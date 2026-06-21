"""extract/rebuild.py — 零 LLM 重建路径,从 DB 已 persist 的中间产物再造下游。

为什么需要这个:
  - 用户改了 worldbook 渲染逻辑 / 加了 subtype 阈值,想看新版输出 — 不该重烧 LLM
  - 修了 anchors 聚合算法 — chapter_facts 已经有 story_time_label,直接重跑就行
  - 改了 canon → worldbook 的 importance 过滤阈值 — 应该秒回

跟 arc_pipeline / pipeline 的 build_constant_worldbook / build_timeline 区别:
  - 那俩是 in-memory 跑完 LLM 拿到 chapter_extracts → 落库 同时调
  - 这俩是从已落库的 kb_canon_entities / chapter_facts 重新生成,无 LLM
"""
from __future__ import annotations

from extract.seed import ScriptSeed


def synthesize_seed_from_db(db, script_id: int) -> ScriptSeed:
    """从 kb_canon_entities + script.metadata 合成一个 ScriptSeed 对象,
    给 build_constant_worldbook 当 seed 参数用。

    不会跑 LLM。era / power_system / key_factions / entity_vocab 从已有数据反推。
    """
    seed = ScriptSeed()

    # era — 优先 scripts.author_era 列(老库/当前 schema 无此列则跳过,避免 UndefinedColumn
    # 在事务里炸掉整个 worldbook 重建);退化到 canon "纪元" entity 的 summary。
    # 先用 information_schema 探列存在性:该查询永不报错,不会污染当前事务。
    era_val = ""
    has_author_era = db.execute(
        "select 1 from information_schema.columns "
        "where table_name='scripts' and column_name='author_era' limit 1",
    ).fetchone()
    if has_author_era:
        row = db.execute(
            "select author_era from scripts where id = %s",
            (script_id,),
        ).fetchone()
        if row and (row.get("author_era") or "").strip():
            era_val = row["author_era"].strip()
    if era_val:
        seed.era = era_val
    else:
        row = db.execute(
            "select summary from kb_canon_entities where script_id=%s and "
            "(name='纪元' or name like '%%纪元%%') order by importance desc limit 1",
            (script_id,),
        ).fetchone()
        if row and row.get("summary"):
            seed.era = (row["summary"] or "").strip()[:80]

    # key_factions — canon 里 type=faction 按 importance desc 取前 40
    rows = db.execute(
        "select name from kb_canon_entities "
        "where script_id=%s and type='faction' "
        "order by importance desc nulls last limit 40",
        (script_id,),
    ).fetchall()
    seed.key_factions = [r["name"] for r in rows if r.get("name")]

    # power_system — canon 里 type=concept 且 subtype 包含 '力量'/'体系'/'法术' 关键词
    rows = db.execute(
        "select name, entity_subtype from kb_canon_entities "
        "where script_id=%s and type='concept' "
        "order by importance desc nulls last limit 60",
        (script_id,),
    ).fetchall()
    POWER_HINTS = ("力量", "体系", "法术", "魔法", "异能", "修炼", "功法", "灵能")
    seed.power_system = [
        r["name"] for r in rows
        if r.get("name") and any(h in (r.get("entity_subtype") or "") for h in POWER_HINTS)
    ][:30]
    # 没匹配上但有 concept 数据 → 退化用 importance top 30 当 power_system
    if not seed.power_system and rows:
        seed.power_system = [r["name"] for r in rows[:30] if r.get("name")]

    # entity_vocab — 全实体名称(给 worldbook _enrich 找 canon)
    rows = db.execute(
        "select name from kb_canon_entities where script_id=%s",
        (script_id,),
    ).fetchall()
    seed.entity_vocab = [r["name"] for r in rows if r.get("name")]

    return seed


def rebuild_worldbook_from_db(db, script_id: int) -> dict:
    """零 LLM 重建 worldbook_entries。返 {worldbook: N, used_seed_era, used_factions}。

    依赖:kb_canon_entities 必须已存在(不然没东西可以聚合)。
    """
    from extract import resolve as R

    row = db.execute(
        "select id from books where script_id = %s",
        (script_id,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": "books 行不存在,请先完整跑一次提取"}
    book_id = int(row["id"])

    n_canon = db.execute(
        "select count(*) as c from kb_canon_entities where script_id = %s",
        (script_id,),
    ).fetchone()
    if not n_canon or int(n_canon["c"]) == 0:
        return {"ok": False, "error": "kb_canon_entities 为空,无法重建 worldbook"}

    # phase_backend: before/after_count + partial_failures
    before_row = db.execute(
        "select count(*) as c from worldbook_entries where script_id=%s",
        (script_id,),
    ).fetchone()
    before_count = int(before_row["c"]) if before_row else 0
    seed = synthesize_seed_from_db(db, script_id)
    try:
        written = R.build_constant_worldbook(db, script_id, book_id, seed)
    except Exception as exc:
        return {
            "ok": False, "source": "canon",
            "before_count": before_count, "after_count": before_count,
            "error": str(exc), "partial_failures": [],
        }
    after_row = db.execute(
        "select count(*) as c from worldbook_entries where script_id=%s",
        (script_id,),
    ).fetchone()
    after_count = int(after_row["c"]) if after_row else 0
    return {
        "ok": True, "source": "canon",
        "before_count": before_count, "after_count": after_count,
        "worldbook": written,
        "used_seed_era": seed.era,
        "used_factions": len(seed.key_factions),
        "used_powers": len(seed.power_system),
        "partial_failures": [],
    }


def rebuild_canon_resolve_from_facts(db, script_id: int) -> dict:
    """零 LLM:从 chapter_facts.characters/locations/factions/concepts 重 cluster
    canon_entities。等价于 resolve.gather_entity_mentions + cluster_entities,
    但只从已落库的 chapter_facts 拉,绕开 LLM Pass1。

    依赖:chapter_facts 必须存在且非空。返 {ok, before_count, after_count, partial_failures}。
    """
    partial_failures: list[dict] = []
    before_row = db.execute(
        "select count(*) as c from kb_canon_entities where script_id=%s",
        (script_id,),
    ).fetchone()
    before_count = int(before_row["c"]) if before_row else 0
    fact_rows = db.execute(
        "select chapter, characters, locations, factions, concepts "
        "from chapter_facts where script_id=%s order by chapter",
        (script_id,),
    ).fetchall()
    if not fact_rows:
        return {
            "ok": False, "source": "chapter_facts",
            "before_count": before_count, "after_count": before_count,
            "error": "chapter_facts 为空,无法重 cluster",
            "partial_failures": partial_failures,
        }
    # 把 facts 转成 resolve._cluster 输入格式(per-type counter)
    from collections import defaultdict
    by_type: dict[str, dict[str, dict]] = defaultdict(dict)

    def _add(typ: str, name: str, ch: int):
        nm = (name or "").strip()
        if not nm:
            return
        rec = by_type[typ].setdefault(nm, {"name": nm, "count": 0, "first": ch,
                                            "surfaces": set(), "full_name": "",
                                            "identity": "", "background": "",
                                            "subtype": "", "parent_names": []})
        rec["count"] += 1
        rec["first"] = min(rec["first"], ch)

    for fr in fact_rows:
        ch = int(fr.get("chapter") or 0)
        for kind, items in (
            ("character", fr.get("characters") or []),
            ("location", fr.get("locations") or []),
            ("faction", fr.get("factions") or []),
            ("concept", fr.get("concepts") or []),
        ):
            if not isinstance(items, list):
                continue
            for it in items:
                if isinstance(it, dict):
                    _add(kind, it.get("name", ""), ch)
                elif isinstance(it, str):
                    _add(kind, it, ch)
    # savepoint 兜底:cluster + 写库出错时回滚到 savepoint,不污染 transaction
    db.execute("SAVEPOINT canon_rebuild")
    try:
        from psycopg.types.json import Jsonb
        # 清旧(只清同 script,不动其他 script)。editor=编辑器 agent 写的(upsert_canon_entity,
        # attrs.source='editor')重建保留不删,与 worldbook/timeline 同口径(harness 审计 P1)。
        # 再插用 on conflict do nothing,故幸存 editor 实体不会被同 logical_key 的重抽撞键覆盖。
        db.execute(
            "delete from kb_canon_entities where script_id=%s "
            "and coalesce(attrs->>'source','') <> 'editor'",
            (script_id,),
        )
        written = 0
        for typ, mentions in by_type.items():
            for name, rec in mentions.items():
                try:
                    db.execute(
                        """
                        insert into kb_canon_entities(
                          script_id, logical_key, name, aliases, type, summary,
                          attrs, first_revealed_chapter, importance, metadata
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (script_id, logical_key) do nothing
                        """,
                        (
                            script_id,
                            _slugify(name) + ("_" + typ if typ != "character" else ""),
                            name,
                            Jsonb([]),
                            typ,
                            "",
                            Jsonb({}),
                            int(rec["first"]),
                            int(rec["count"]),
                            Jsonb({"source": "resolve_only_rebuild"}),
                        ),
                    )
                    written += 1
                except Exception as exc:
                    partial_failures.append({
                        "name": name, "type": typ, "error": str(exc),
                    })
        db.execute("RELEASE SAVEPOINT canon_rebuild")
    except Exception as exc:
        db.execute("ROLLBACK TO SAVEPOINT canon_rebuild")
        return {
            "ok": False, "source": "chapter_facts",
            "before_count": before_count, "after_count": before_count,
            "error": str(exc),
            "partial_failures": partial_failures,
        }
    after_row = db.execute(
        "select count(*) as c from kb_canon_entities where script_id=%s",
        (script_id,),
    ).fetchone()
    after_count = int(after_row["c"]) if after_row else 0
    return {
        "ok": True, "source": "chapter_facts",
        "before_count": before_count, "after_count": after_count,
        "partial_failures": partial_failures,
    }


def _slugify(name: str) -> str:
    import re as _re
    s = _re.sub(r"[\s\-/\.·_,]+", "_", (name or "").strip())
    s = _re.sub(r"[^\w一-鿿]", "", s)
    return s[:60] or "unknown"


def rebuild_timeline_from_db(db, script_id: int) -> dict:
    """零 LLM 重建 script_timeline_anchors。从 chapter_facts.story_time_label
    + chapter_facts.summary 按章节顺序聚合连续 label 段。

    依赖:chapter_facts 必须有 story_time_label(空 label 的章节跳过)。

    phase_backend: 写入用 savepoint 包,失败 ROLLBACK TO SAVEPOINT 不污染 tx;
    返 {ok, before_count, after_count, partial_failures, source}。
    """
    partial_failures: list[dict] = []
    before_row = db.execute(
        "select count(*) as c from script_timeline_anchors where script_id=%s",
        (script_id,),
    ).fetchone()
    before_count = int(before_row["c"]) if before_row else 0
    # 读全部章节(不再只取非空 label)—— 让承接/序章兜底能补满开篇与中段空洞。
    rows = db.execute(
        "select chapter, summary, story_time_label, story_phase "
        "from chapter_facts where script_id=%s "
        "order by chapter asc",
        (script_id,),
    ).fetchall()
    if not rows:
        return {
            "ok": False, "source": "chapter_facts",
            "before_count": before_count, "after_count": before_count,
            "error": "chapter_facts 没有章节数据,无法重建时间线",
            "partial_failures": partial_failures,
        }

    # 跟 resolve.build_timeline 同样的聚合(同一份确定性规则):剔占位 label、开篇归「序章」、
    # 无明确时间承接上一段 —— 但读 chapter_facts 而非 chapter_extracts。
    from extract.resolve import _clean_timeline_label
    segments: list[dict] = []
    last_label = ""
    last_phase = ""
    for r in rows:
        ch = int(r["chapter"])
        label = _clean_timeline_label(r.get("story_time_label") or "")
        phase = (r.get("story_phase") or "").strip()
        if not label:
            label = last_label or "序章"
            phase = phase or last_phase
        last_label, last_phase = label, phase
        summary = (r.get("summary") or "").strip()
        if segments and segments[-1]["label"] == label:
            segments[-1]["chapter_max"] = ch
            if summary:
                segments[-1]["summaries"].append((ch, summary))
        else:
            segments.append({
                "label": label, "phase": phase,
                "chapter_min": ch, "chapter_max": ch,
                "summaries": [(ch, summary)] if summary else [],
            })

    # phase_backend: SAVEPOINT 包 — 失败时 ROLLBACK TO 不污染上层 tx
    db.execute("SAVEPOINT timeline_rebuild")
    written = 0
    try:
        # 先清旧 anchors — upsert 也可以但有歪历史数据时 conflict key 不一致会留垃圾。
        # 只删原著骨架(source='novel');保留编辑器续写新增的(source='editor')。
        db.execute(
            "delete from script_timeline_anchors where script_id = %s and coalesce(source,'novel') <> 'editor'",
            (script_id,),
        )
        for seg in segments:
            sums = seg.get("summaries") or []
            if sums:
                picks = [sums[0]]
                if len(sums) >= 3:
                    picks.append(sums[len(sums) // 2])
                if len(sums) >= 2:
                    picks.append(sums[-1])
                sample_summary = " / ".join(f"第{ch}章:{s}" for ch, s in picks)[:1900]
            else:
                sample_summary = ""
            try:
                db.execute(
                    """
                    insert into script_timeline_anchors(script_id, story_phase, story_time_label,
                      chapter_min, chapter_max, chapter_count, sample_summary, confidence)
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict(script_id, story_phase, story_time_label) do update set
                      chapter_min=least(script_timeline_anchors.chapter_min, excluded.chapter_min),
                      chapter_max=greatest(script_timeline_anchors.chapter_max, excluded.chapter_max),
                      chapter_count=greatest(script_timeline_anchors.chapter_max, excluded.chapter_max)
                        - least(script_timeline_anchors.chapter_min, excluded.chapter_min) + 1,
                      sample_summary=case when length(excluded.sample_summary) > 0
                        then excluded.sample_summary else script_timeline_anchors.sample_summary end,
                      updated_at=now()
                    where script_timeline_anchors.source <> 'editor'
                    """,
                    (script_id, seg["phase"], seg["label"], seg["chapter_min"], seg["chapter_max"],
                     seg["chapter_max"] - seg["chapter_min"] + 1, sample_summary, 0.7),
                )
                written += 1
            except Exception as exc:
                partial_failures.append({
                    "label": seg.get("label"), "error": str(exc),
                })
        db.execute("RELEASE SAVEPOINT timeline_rebuild")
    except Exception as exc:
        db.execute("ROLLBACK TO SAVEPOINT timeline_rebuild")
        return {
            "ok": False, "source": "chapter_facts",
            "before_count": before_count, "after_count": before_count,
            "error": str(exc),
            "partial_failures": partial_failures,
        }
    return {
        "ok": True, "source": "chapter_facts",
        "before_count": before_count, "after_count": written,
        "anchors": written,
        "partial_failures": partial_failures,
    }
