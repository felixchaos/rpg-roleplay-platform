"""import_pipeline.rebuild_modules — 单模块 rebuild 函数(被 /rebuild/{module} 路由调用)

来源: 原 rpg/platform_app/import_pipeline.py rebuild_chunks_from_db / rebuild_facts_from_db / rebuild_cards_from_canon / rebuild_cards_with_llm / rebuild_worldbook_with_llm(原 L2154-2406) 区段,纯机械搬家(函数体逐字未动),零行为变化。
"""
from __future__ import annotations

from typing import Any

from ..db import connect
from ..perms import script_owned


# ══════════════════════════════════════════════════════════════════════
#  phase_backend: 单模块 rebuild 函数(被 /rebuild/{module} 路由调用)
#  各 rebuild 返 {ok, before_count, after_count, partial_failures, source}
# ══════════════════════════════════════════════════════════════════════
def rebuild_chunks_from_db(user_id: int, script_id: int) -> dict[str, Any]:
    """零 LLM:重新切 document_chunks。从 script_chapters 读,清旧 chunks 写新。"""
    from .. import knowledge
    partial_failures: list[dict[str, Any]] = []
    with connect() as db:
        before = db.execute(
            "select count(*) as c from document_chunks where script_id = %s",
            (script_id,),
        ).fetchone()
        before_count = int(before["c"]) if before else 0
        script = script_owned(db, script_id, user_id)
        if not script:
            return {"ok": False, "error": "无权访问该剧本"}
        book = knowledge._ensure_book(db, script)
        chapters = db.execute(
            "select * from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        # 清旧
        db.execute("delete from document_chunks where script_id = %s", (script_id,))
        total = 0
        for chapter in chapters:
            try:
                doc = knowledge._upsert_document(db, book, script, chapter)
                for ci, content in enumerate(knowledge._chunk_text(chapter["content"])):
                    knowledge._insert_chunk(db, book, script, chapter, doc, ci, content)
                    total += 1
            except Exception as exc:
                partial_failures.append({
                    "chapter": chapter.get("chapter_index"),
                    "error": str(exc),
                })
    return {
        "ok": True, "source": "script_chapters",
        "before_count": before_count, "after_count": total,
        "partial_failures": partial_failures,
    }


def rebuild_facts_from_db(user_id: int, script_id: int) -> dict[str, Any]:
    """零 LLM:从 script_chapters 重抽 chapter_facts(规则匹配,不调 LLM)。"""
    from .. import knowledge
    partial_failures: list[dict[str, Any]] = []
    with connect() as db:
        before = db.execute(
            "select count(*) as c from chapter_facts where script_id = %s",
            (script_id,),
        ).fetchone()
        before_count = int(before["c"]) if before else 0
        script = script_owned(db, script_id, user_id)
        if not script:
            return {"ok": False, "error": "无权访问该剧本"}
        book = knowledge._ensure_book(db, script)
        chapters = db.execute(
            "select * from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        chars = knowledge._load_characters(script_id=script_id) or {}
        world = knowledge._load_world(script_id=script_id) or {}
        summaries = knowledge._load_summaries()
        known_names = knowledge._known_names(chars)
        known_locations = knowledge._known_locations(world)
        known_concepts = knowledge._known_concepts(world)
        total = 0
        for chapter in chapters:
            try:
                doc = db.execute(
                    "select * from documents where script_id = %s and chapter_id = %s",
                    (script_id, chapter["id"]),
                ).fetchone()
                if not doc:
                    doc = knowledge._upsert_document(db, book, script, chapter)
                fact = knowledge._fact_from_chapter(
                    chapter, summaries, known_names, known_locations, known_concepts,
                )
                knowledge._upsert_chapter_fact(db, book, script, chapter, doc, fact)
                total += 1
            except Exception as exc:
                partial_failures.append({
                    "chapter": chapter.get("chapter_index"),
                    "error": str(exc),
                })
    return {
        "ok": True, "source": "script_chapters",
        "before_count": before_count, "after_count": total,
        "partial_failures": partial_failures,
    }


def rebuild_cards_from_canon(user_id: int, script_id: int, *,
                             chapter_max: int | None = None) -> dict[str, Any]:
    """零 LLM:从 kb_canon_entities 的 character 类回填 character_cards。
    无 canon 数据时退化为 _aggregate_characters_from_facts(零 LLM 词频)。

    chapter_max(进度感知角色卡 Phase 1A):仅回填 first_revealed_chapter<=chapter_max
    的角色(过滤掉中后期才出场的角色,序章重建时不引入未登场角色);并透传
    first_revealed_chapter 给 _sync(别再被刷成 0 丢防剧透章)。None=全书(默认)。
    """
    from .. import knowledge
    from ..knowledge.session import _aggregate_characters_from_facts
    partial_failures: list[dict[str, Any]] = []
    cmax = int(chapter_max) if chapter_max is not None else None
    with connect() as db:
        before = db.execute(
            "select count(*) as c from character_cards "
            "where script_id = %s and card_type = 'npc'",
            (script_id,),
        ).fetchone()
        before_count = int(before["c"]) if before else 0
        script = script_owned(db, script_id, user_id)
        if not script:
            return {"ok": False, "error": "无权访问该剧本"}
        book = knowledge._ensure_book(db, script)
        # 优先用 canon entity (LLM extract 已有);否则用 facts 聚合。
        # 补取 first_revealed_chapter:① 透传给 _sync 修「重建后丢防剧透章」② chapter_max 区间过滤。
        # 0 / NULL = 未知章节,保守放行(不误隐藏该出场的角色,与 canon_repo._reveal_clause 语义一致)。
        canon_sql = (
            "select name, aliases, summary, importance, "
            "coalesce(first_revealed_chapter, 0) as first_revealed_chapter "
            "from kb_canon_entities "
            "where script_id = %s and type = 'character' "
        )
        canon_args: list[Any] = [script_id]
        if cmax is not None:
            canon_sql += "and (coalesce(first_revealed_chapter, 0) <= %s or coalesce(first_revealed_chapter, 0) = 0) "
            canon_args.append(cmax)
        canon_sql += "order by importance desc nulls last"
        canon_rows = db.execute(canon_sql, tuple(canon_args)).fetchall()
        source = "canon"
        if canon_rows:
            chars: dict[str, Any] = {}
            for r in canon_rows:
                nm = (r.get("name") or "").strip()
                if not nm:
                    continue
                chars[nm] = {
                    "name": nm,
                    "identity": (r.get("summary") or "")[:200],
                    "appearance": "",
                    "personality": "",
                    "speech_style": "",
                    "current_status": "",
                    "secrets": "",
                    "sample_dialogue": [],
                    "priority": int(r.get("importance") or 0),
                    "aliases": list(r.get("aliases") or []),
                    # 透传防剧透章(canon SELECT 已取),否则 _sync 写 0 丢章。
                    "first_revealed_chapter": int(r.get("first_revealed_chapter") or 0),
                    "importance": int(r.get("importance") or 0),
                }
        else:
            source = "chapter_facts"
            try:
                chars = _aggregate_characters_from_facts(script_id, chapter_max=cmax)
            except Exception as exc:
                partial_failures.append({"stage": "aggregate", "error": str(exc)})
                chars = {}
        from ..knowledge._sync import _sync_character_cards
        try:
            after_count = _sync_character_cards(db, book, script, chars)
        except Exception as exc:
            partial_failures.append({"stage": "_sync_character_cards", "error": str(exc)})
            after_count = 0
    return {
        "ok": True, "source": source,
        "before_count": before_count, "after_count": after_count,
        "chapter_max": cmax,
        "partial_failures": partial_failures,
    }


def rebuild_cards_with_llm(user_id: int, script_id: int, *,
                           chapter_max: int | None = None,
                           model: str = "deepseek-v4-flash",
                           api_id: str = "deepseek",
                           progress_cb=None) -> dict[str, Any]:
    """可选 LLM 丰富重建(进度感知角色卡 Phase 1A):走 run_llm_extraction(带 chapter_max)
    对 1..chapter_max 区间重抽规范层,产「该时期态」的丰富 identity/background,然后零 LLM
    路径把 canon 回填到 character_cards。BYOK,用户付费。

    优雅降级:LLM 抽取失败(无 key / 配额 / 网络)→ 回退零 LLM 版(rebuild_cards_from_canon),
    保证「重建」永远有产物、永不报错卡死。
    """
    from ..knowledge.llm_extract import run_llm_extraction
    llm_ok = False
    llm_error = ""
    try:
        r = run_llm_extraction(
            user_id, script_id,
            algorithm="arc",
            model=model, api_id=api_id,
            chapter_min=1, chapter_max=chapter_max,
            confirmed=True,
            progress_cb=progress_cb,
        )
        llm_ok = bool(r.get("ok"))
        if not llm_ok:
            llm_error = str(r.get("error") or r.get("message") or "llm_extract failed")
    except Exception as exc:
        llm_error = str(exc)
    # 无论 LLM 是否成功,都跑零 LLM 回填把(新或旧)canon → character_cards。
    out = rebuild_cards_from_canon(user_id, script_id, chapter_max=chapter_max)
    out["source"] = "llm_extract" if llm_ok else (out.get("source") or "canon")
    out["llm_ok"] = llm_ok
    if llm_error and not llm_ok:
        out.setdefault("partial_failures", []).append({"stage": "llm_extract", "error": llm_error})
    return out


def rebuild_worldbook_with_llm(user_id: int, script_id: int, *,
                                source: str = "canon") -> dict[str, Any]:
    """worldbook 重建。source='canon' 零 LLM(rebuild_worldbook_from_db),
    'llm' 走 _stage_worldbook 一次 LLM 调用 + 写库。"""
    partial_failures: list[dict[str, Any]] = []
    with connect() as db:
        before = db.execute(
            "select count(*) as c from worldbook_entries where script_id = %s",
            (script_id,),
        ).fetchone()
        before_count = int(before["c"]) if before else 0
        if not script_owned(db, script_id, user_id):
            return {"ok": False, "error": "无权访问该剧本"}
    if source == "canon":
        with connect() as db:
            from extract.rebuild import rebuild_worldbook_from_db
            res = rebuild_worldbook_from_db(db, script_id)
        if not res.get("ok"):
            return {
                "ok": False, "source": "canon",
                "before_count": before_count, "after_count": before_count,
                "error": res.get("error"),
                "partial_failures": partial_failures,
            }
        with connect() as db:
            after_row = db.execute(
                "select count(*) as c from worldbook_entries where script_id = %s",
                (script_id,),
            ).fetchone()
        return {
            "ok": True, "source": "canon",
            "before_count": before_count,
            "after_count": int(after_row["c"]) if after_row else 0,
            "partial_failures": partial_failures,
        }
    # source == 'llm' — 走 _stage_worldbook (一次 LLM)。job_id 由调用方传入 ctl
    raise NotImplementedError(
        "rebuild_worldbook_with_llm(source='llm') 必须从 rebuild job runner 调用,"
        "需要 JobController 上下文以记 usage_actual"
    )
