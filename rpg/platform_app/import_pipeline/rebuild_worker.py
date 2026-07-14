"""import_pipeline.rebuild_worker — rebuild job worker

来源: 原 rpg/platform_app/import_pipeline.py _run_module_rebuild / _count(原 L2847-3209) 区段,纯机械搬家(函数体逐字未动),零行为变化。
"""
from __future__ import annotations

from typing import Any

from ..db import connect
from .control import JobController
from .rebuild_modules import (
    rebuild_cards_from_canon,
    rebuild_cards_with_llm,
    rebuild_chunks_from_db,
    rebuild_facts_from_db,
    rebuild_worldbook_with_llm,
)
from .rebuild_registry import REBUILD_MODULES
from .runner import finalize_job_if_unterminated
from .stages_llm import _stage_worldbook


def _run_module_rebuild(
    job_id: str, user_id: int, script_id: int, module: str, body: dict[str, Any],
) -> None:
    """rebuild worker。统一 import_jobs + SSE,失败标 failed,
    partial 失败标 done_with_errors,写 before/after_count + warnings。"""
    ctl = JobController(job_id)
    ctl.update(status="running", stage=module, overall_progress=0)
    with connect() as db:
        db.execute("update import_jobs set started_at = now() where job_id = %s", (job_id,))
    try:
        source = str(body.get("source") or body.get("mode") or "")
        if module == "chunks":
            result = rebuild_chunks_from_db(user_id, script_id)
        elif module == "chapter-facts":
            result = rebuild_facts_from_db(user_id, script_id)
        elif module == "cards":
            # 进度感知角色卡:chapter_max 区间 + 可选 LLM 丰富(source/mode=='llm')。
            cmax_raw = body.get("chapter_max")
            try:
                cmax = int(cmax_raw) if cmax_raw not in (None, "") else None
            except (TypeError, ValueError):
                cmax = None
            if source == "llm":
                # 进度只有 0/100 的根因:此前只有 arc_extract 阶段写 overall_progress,seed/
                # per_chapter/resolve/embed 只写 stage_progress,而前端浮窗读的是 overall_*。给每个
                # 阶段分配一段 overall 进度带,段内按 done/total 线性插值 → 全程平滑推进。
                _BANDS = {"seed": (0, 5), "per_chapter": (5, 60), "arc_extract": (60, 85),
                          "resolve": (85, 95), "embed": (95, 100)}

                def _cards_progress(stage: str, info: dict) -> None:
                    try:
                        total = int(info.get("total") or 0)
                        done = int(info.get("done") or 0)
                        band = _BANDS.get(stage)
                        if band:
                            lo, hi = band
                            frac = (done / total) if total > 0 else 0.0
                            overall = int(lo + (hi - lo) * max(0.0, min(1.0, frac)))
                            ctl.update(stage=stage, stage_progress=done, stage_total=max(total, 1),
                                       overall_progress=overall, overall_total=100)
                    except Exception:
                        pass
                result = rebuild_cards_with_llm(
                    user_id, script_id, chapter_max=cmax,
                    model=str(body.get("model") or "deepseek-v4-flash"),
                    api_id=str(body.get("api_id") or "deepseek"),
                    progress_cb=_cards_progress,
                )
            else:
                result = rebuild_cards_from_canon(user_id, script_id, chapter_max=cmax)
        elif module == "canon":
            # full = 重抽 LLM;resolve_only = 从 chapter_extracts 重 cluster (零 LLM)
            from extract.rebuild import rebuild_canon_resolve_from_facts
            if source == "resolve_only":
                with connect() as db:
                    result = rebuild_canon_resolve_from_facts(db, script_id)
            else:
                # full LLM: 走 schedule_llm_extraction 同款 (但这里直接调底层)
                from platform_app.knowledge.llm_extract import run_llm_extraction
                # db 复用修复:上面写 started_at 的 with connect() 已退出、连接已还池,这里不能再用
                # 那个 db(workers≥2 时可能已被别的 worker 取走 = 未定义行为)。各取独立连接。
                with connect() as _dbc:
                    before = _count(_dbc, "kb_canon_entities", script_id)
                # 进度回传:arc 每弧完成 → 更新 overall 进度。否则整段 ~100 弧提取期 overall_progress
                # 恒 0、stage 卡在 canon,用户以为卡死(实则后台在烧)。arc_extract 是主阶段,映射到
                # overall;done/total 由 as_completed 串行回调,无并发竞争。终态会把 overall_total
                # 重置回 1(见下方写终态),避免 done 时显示 1/弧数。
                def _canon_progress(stage: str, info: dict) -> None:
                    try:
                        total = int(info.get("total") or 0)
                        done = int(info.get("done") or 0)
                        if stage == "arc_extract" and total:
                            ctl.update(stage="arc_extract", stage_progress=done,
                                       stage_total=total, overall_progress=done,
                                       overall_total=max(total, 1))
                        elif stage in ("seed", "per_chapter", "resolve", "embed"):
                            ctl.update(stage=stage, stage_progress=done,
                                       stage_total=max(total, 1))
                    except Exception:
                        pass
                r = run_llm_extraction(
                    user_id, script_id,
                    algorithm=str(body.get("algorithm") or "arc"),
                    model=str(body.get("model") or "deepseek-v4-flash"),
                    api_id=str(body.get("api_id") or "deepseek"),
                    confirmed=True,
                    progress_cb=_canon_progress,
                )
                with connect() as _dbc:
                    after = _count(_dbc, "kb_canon_entities", script_id) if r.get("ok") else before
                result = {
                    "ok": bool(r.get("ok")),
                    "source": "llm_extract",
                    "before_count": before,
                    "after_count": after,
                    "partial_failures": [],
                    "error": r.get("error") if not r.get("ok") else "",
                }
        elif module == "worldbook":
            src = source or "canon"
            if src == "llm":
                # 走 import pipeline 的 _stage_worldbook(单次 LLM)
                with connect() as db:
                    before = db.execute(
                        "select count(*) as c from worldbook_entries where script_id = %s",
                        (script_id,),
                    ).fetchone()
                    before_count = int(before["c"]) if before else 0
                count = _stage_worldbook(ctl, user_id, script_id)
                result = {
                    "ok": count > 0, "source": "llm",
                    "before_count": before_count, "after_count": count,
                    "partial_failures": [] if count > 0 else [
                        {"stage": "worldbook", "error": "LLM returned 0 entries"}
                    ],
                }
            else:
                result = rebuild_worldbook_with_llm(user_id, script_id, source="canon")
        elif module == "anchors":
            with connect() as db:
                before = db.execute(
                    "select count(*) as c from script_timeline_anchors where script_id = %s",
                    (script_id,),
                ).fetchone()
                before_count = int(before["c"]) if before else 0
                from extract.rebuild import rebuild_timeline_from_db
                r = rebuild_timeline_from_db(db, script_id)
                after = db.execute(
                    "select count(*) as c from script_timeline_anchors where script_id = %s",
                    (script_id,),
                ).fetchone()
                after_count = int(after["c"]) if after else 0
                result = {
                    "ok": bool(r.get("ok")),
                    "source": "chapter_facts",
                    "before_count": before_count,
                    "after_count": after_count,
                    "partial_failures": [],
                    "error": r.get("error") if not r.get("ok") else "",
                }
        elif module == "embeddings":
            includes = list(body.get("include") or ["chunks", "cards", "worldbook", "canon"])
            from ..knowledge import embedding as _embed
            from extract.embed import embed_canon_entities
            counts = {}
            partial_failures = []
            # KB 卫生(设计 O §5.2):「重做」= 强制重嵌。先把被选类型的向量清成 NULL,再跑增量循环
            # (_embed_chunks_loop_inner / embed_canon_entities 都按 WHERE embedding_vec IS NULL 重嵌)→
            # 真重嵌全部,而非「秒完成」空操作(群反馈:世界书编辑后重做秒完成、实际没重新生成)。
            # embed_script 会重嵌 chunks+cards+worldbook,canon 由 embed_canon_entities 重嵌,故清空安全。
            _FORCE_CLEAR = {
                "chunks":    "update document_chunks set embedding_vec=NULL where script_id=%s",
                "cards":     "update character_cards set embedding_vec=NULL, embedded_at=NULL where script_id=%s and card_type='npc'",
                "worldbook": "update worldbook_entries set embedding_vec=NULL, embedded_at=NULL where script_id=%s",
                "canon":     "update kb_canon_entities set embedding=NULL where script_id=%s",
            }
            with connect() as db:
                for _k in includes:
                    _sql = _FORCE_CLEAR.get(_k)
                    if not _sql:
                        continue
                    try:
                        db.execute(_sql, (script_id,))
                    except Exception as exc:
                        partial_failures.append({"stage": f"force_clear_{_k}", "error": str(exc)})
                db.commit()
            with connect() as db:
                if "chunks" in includes or "cards" in includes or "worldbook" in includes:
                    try:
                        # embed_script fire-and-forget;这里改成同步等(rebuild 等任务跑完)
                        # 但为简洁同复用现有线程模型,直接调 sub-rountines
                        # 不直接调:embed_script 已是异步 dispatch,只确认凭证有效
                        _ = _embed.embed_status(script_id)
                    except Exception as exc:
                        partial_failures.append({"stage": "embed_check", "error": str(exc)})
                if "canon" in includes:
                    try:
                        emb = embed_canon_entities(db, script_id, user_id=user_id)
                        counts["canon"] = emb
                    except Exception as exc:
                        partial_failures.append({"stage": "embed_canon", "error": str(exc)})
            # 触发后台 embed_script(chunks/cards/worldbook)
            try:
                from ..knowledge import embedding as _embed2
                _embed2.embed_script(user_id, script_id)
            except Exception as exc:
                partial_failures.append({"stage": "embed_script", "error": str(exc)})
            with connect() as db:
                done = db.execute(
                    "select count(*) as c from document_chunks "
                    "where script_id = %s and embedding_vec is not null",
                    (script_id,),
                ).fetchone()
                total = db.execute(
                    "select count(*) as c from document_chunks where script_id = %s",
                    (script_id,),
                ).fetchone()
            result = {
                "ok": True, "source": "pgvector",
                "before_count": int(done["c"]) if done else 0,
                "after_count": int(total["c"]) if total else 0,
                "partial_failures": partial_failures,
                "extra": counts,
            }
        elif module == "facts_refine":
            # extract.facts_refine.refine_script:逐章调 LLM 精炼 chapter_facts.summary/
            # in_world_time。options 透传 {ch_from, ch_to, api_id, model};不传 api_id/model
            # 时 refine_script 内部走 _resolve_recorder_api_and_model 解析用户默认模型。
            from extract.facts_refine import refine_script
            ch_from_raw = body.get("ch_from")
            ch_to_raw = body.get("ch_to")
            try:
                ch_from = int(ch_from_raw) if ch_from_raw not in (None, "") else 1
            except (TypeError, ValueError):
                ch_from = 1
            try:
                ch_to = int(ch_to_raw) if ch_to_raw not in (None, "") else None
            except (TypeError, ValueError):
                ch_to = None
            with connect() as _dbc:
                before = _count(_dbc, "chapter_facts", script_id)
            r = refine_script(
                script_id, user_id,
                ch_from=ch_from, ch_to=ch_to,
                api_id=(body.get("api_id") or None), model=(body.get("model") or None),
                apply=True,
            )
            with connect() as _dbc:
                after = _count(_dbc, "chapter_facts", script_id)
            partial_failures = []
            if int(r.get("skipped") or 0) or int(r.get("failed") or 0):
                partial_failures.append({
                    "stage": "facts_refine",
                    "error": f"skipped={r.get('skipped')} failed={r.get('failed')}",
                })
            result = {
                "ok": bool(r.get("ok")),
                "source": "llm_refined",
                "before_count": before,
                "after_count": after,
                "partial_failures": partial_failures,
                "error": r.get("error") if not r.get("ok") else "",
                "extra": {"refined": r.get("refined"), "range": r.get("range")},
            }
        elif module == "worldbook_enrich":
            # extract.worldbook_enrich.enrich_script_worldbook:命中 pattern 的世界书条目
            # LLM 重写 content。pattern 默认核心设定类词汇正则。
            from extract.worldbook_enrich import enrich_script_worldbook
            pattern = str(body.get("pattern") or "力量|概念|势力|体系")
            with connect() as _dbc:
                before = _count(_dbc, "worldbook_entries", script_id)
            r = enrich_script_worldbook(
                script_id, user_id, pattern=pattern,
                api_id=(body.get("api_id") or None), model=(body.get("model") or None),
                apply=True,
            )
            entries = list(r.get("entries") or [])
            ok_count = sum(1 for e in entries if e.get("status") == "ok")
            partial_failures = [
                {"stage": "worldbook_enrich", "entry_id": e.get("id"), "error": e.get("status")}
                for e in entries if e.get("status") not in ("ok",)
            ]
            result = {
                "ok": bool(r.get("ok")),
                "source": "llm_enriched",
                "before_count": before,
                "after_count": before,   # UPDATE,不新增行;充实数用 extra.enriched 体现
                "partial_failures": partial_failures,
                "error": r.get("error") if not r.get("ok") else "",
                "extra": {"enriched": ok_count, "total_matched": len(entries)},
            }
        elif module == "world_key":
            # extract.world_key_backfill.backfill_worldlines:结构先验回填 worldline_key/
            # in_world_time;body.use_llm 真值时追加第二层 LLM 窄确认(BYOK)。
            from extract.world_key_backfill import backfill_worldlines
            with connect() as _dbc:
                before = _count(_dbc, "script_timeline_anchors", script_id)
            r = backfill_worldlines(
                script_id,
                dry_run=False,
                use_llm=bool(body.get("use_llm")),
                user_id=user_id,
                api_id_override=(body.get("api_id") or None),
                model_override=(body.get("model") or None),
            )
            with connect() as _dbc:
                after = _count(_dbc, "script_timeline_anchors", script_id)
            partial_failures = []
            if r.get("overcut"):
                partial_failures.append({
                    "stage": "world_key",
                    "error": "overcut:分段过密已整体退回单世界(null)",
                })
            result = {
                "ok": True,
                "source": "structural_prior" if not body.get("use_llm") else "structural_prior+llm",
                "before_count": before,
                "after_count": after,
                "partial_failures": partial_failures,
                "extra": {"written": r.get("written"), "would_write": r.get("would_write"),
                          "overcut": r.get("overcut")},
            }
        else:
            result = {"ok": False, "error": f"unhandled module: {module}"}
        # 写终态 — 任何 partial_failures 标 done_with_errors
        partial_failures = list(result.get("partial_failures") or [])
        ok = bool(result.get("ok"))
        final_status = "done" if ok and not partial_failures else (
            "failed" if not ok else "done_with_errors"
        )
        ctl.update(
            status=final_status,
            stage="done",
            overall_progress=1,
            overall_total=1,   # 重置:canon 进度回传期间把 overall_total 改成弧数,终态要还原成 1/1=100%
            stage_progress=1,
            stage_total=1,
            source=str(result.get("source") or ""),
            before_count=int(result.get("before_count") or 0),
            after_count=int(result.get("after_count") or 0),
            warnings=partial_failures,
            error=str(result.get("error") or "")[:500],
            stages=[{
                "id": module,
                "label": REBUILD_MODULES[module][1],
                "status": "error" if final_status == "failed" else "done",
                "before_count": int(result.get("before_count") or 0),
                "after_count": int(result.get("after_count") or 0),
            }],
        )
        with connect() as db:
            db.execute(
                "update import_jobs set finished_at=now() where job_id=%s", (job_id,),
            )
    except Exception as exc:
        import traceback as _tb
        import logging as _logging
        _logging.getLogger(__name__).exception(
            "_run_module_rebuild %s failed: %s", job_id, exc,
        )
        ctl.update(
            status="failed",
            error=f"{type(exc).__name__}: {str(exc)[:400]}",
            warnings={
                "stage": module,
                "exception": type(exc).__name__,
                "traceback": _tb.format_exc()[:800],
            },
        )
        with connect() as db:
            db.execute(
                "update import_jobs set finished_at=now() where job_id=%s", (job_id,),
            )
    finally:
        # 兜底:无论正常/异常/被吞的错误,确保不留 status='running' 僵尸行(镜像 _run_pipeline)
        finalize_job_if_unterminated(job_id)


def _count(db, table: str, script_id: int) -> int:
    row = db.execute(
        f"select count(*) as c from {table} where script_id = %s", (script_id,),
    ).fetchone()
    return int(row["c"]) if row else 0
