"""import_pipeline.rebuild_scheduler — rebuild 估算 + 异步调度(kind='rebuild_*' 写 import_jobs)

来源: 原 rpg/platform_app/import_pipeline.py estimate_module_rebuild / schedule_module_rebuild(原 L2472-2844) 区段,纯机械搬家(函数体逐字未动),零行为变化。
"""
from __future__ import annotations

import secrets
import threading
from typing import Any

from psycopg.types.json import Jsonb

from ..db import connect, init_db
from ..perms import script_owned
from .rebuild_registry import (
    REBUILD_MODULES,
    _embedding_preflight_or_raise,
    _embedding_prereq,
    normalize_rebuild_module,
)
from .rebuild_worker import _run_module_rebuild
from .stages_llm import (
    _credential_api_id_for,
    _has_user_llm_credential,
    _resolve_extractor_llm,
    require_user_llm_credential,
)


def estimate_module_rebuild(
    user_id: int, script_id: int, module: str, *, body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate a module rebuild and expose blocking prereqs for the UI modal."""
    init_db()
    module = normalize_rebuild_module(module)
    body = dict(body or {})
    if module not in REBUILD_MODULES:
        raise ValueError(f"unknown module: {module}")

    def _scalar(db, sql: str) -> int:
        row = db.execute(sql, (script_id,)).fetchone()
        return int(row["c"]) if row else 0

    with connect() as db:
        script = script_owned(db, script_id, user_id)
        if not script:
            raise ValueError("无权访问该剧本")

        chapter_count = int(script.get("chapter_count") or 0)
        chunks_total = _scalar(db, "select count(*) as c from document_chunks where script_id = %s")
        chunks_done = _scalar(
            db,
            "select count(*) as c from document_chunks "
            "where script_id = %s and embedding_vec is not null",
        )
        canon_total = _scalar(db, "select count(*) as c from kb_canon_entities where script_id = %s")
        canon_done = _scalar(
            db,
            "select count(*) as c from kb_canon_entities "
            "where script_id = %s and embedding is not null",
        )
        cards_total = _scalar(
            db,
            "select count(*) as c from character_cards "
            "where script_id = %s and card_type='npc'",
        )
        cards_done = _scalar(
            db,
            "select count(*) as c from character_cards "
            "where script_id = %s and card_type='npc' and embedding_vec is not null",
        )
        wb_total = _scalar(db, "select count(*) as c from worldbook_entries where script_id = %s")
        wb_done = _scalar(
            db,
            "select count(*) as c from worldbook_entries "
            "where script_id = %s and embedding_vec is not null",
        )

    prereqs: list[dict[str, Any]] = []
    affects: list[str] = []
    note = ""
    model: str | None = None
    tokens_est = 0       # 仅真·chat-LLM 模块(canon 全量 / worldbook-llm)>0;其余为 0 = 真免费
    cost_est = 0.0

    kind, _label, needs_llm = REBUILD_MODULES[module]
    source_pref = str(body.get("source") or body.get("mode") or "").lower()
    # needs_llm 必须反映 runner 的**实际**调用:之前 tokens_est/cost_est 写死 0,所有模块(含真
    # 烧 LLM 的 canon 全量重抽 / worldbook-llm)都显示「免费」,会误导用户(群反馈)。这里按真实
    # 路径校正 needs_llm,下方再据此算 token+成本。
    # - worldbook 默认 canon(零 LLM),仅 source=='llm' 才烧 LLM;
    # - cards = rebuild_cards_from_canon,**零 LLM**(从 canon/facts 反推),恒免费;
    # - canon 默认全量 LLM 重抽,resolve_only 是零 LLM。
    if module == "worldbook":
        needs_llm = (source_pref == "llm")
    if module == "cards":
        # cards 默认零 LLM(从 canon/facts 反推),恒免费;仅 source/mode=='llm' 的丰富重建烧 LLM。
        needs_llm = (source_pref == "llm")
    if module == "canon" and source_pref == "resolve_only":
        needs_llm = False
    if module == "world_key":
        # 结构先验默认零 LLM;仅 body.use_llm 真值时才需要 BYOK(第二层窄确认)。
        needs_llm = bool(body.get("use_llm"))

    if module == "embeddings":
        includes = [str(x) for x in (body.get("include") or ["chunks", "cards", "worldbook", "canon"])]
        pre = _embedding_prereq(user_id)
        prereqs.append(pre)
        model = str(pre.get("model") or "")
        target_pairs = {
            "chunks": (chunks_done, chunks_total, "document_chunks.embedding_vec"),
            "cards": (cards_done, cards_total, "character_cards.embedding_vec"),
            "worldbook": (wb_done, wb_total, "worldbook_entries.embedding_vec"),
            "canon": (canon_done, canon_total, "kb_canon_entities.embedding"),
        }
        target_total = 0
        target_done = 0
        for name in includes:
            done, total, table = target_pairs.get(name, (0, 0, name))
            target_done += done
            target_total += total
            affects.append(table)
        if "chunks" in includes and chunks_total == 0:
            prereqs.append({
                "key": "chunks",
                "label": "章节切块",
                "ok": False,
                "hint": "当前剧本还没有章节切块,请先重做「章节切片」。",
                "count": 0,
                "total": max(chapter_count, 1),
            })
        note = f"将检查 {target_total} 条向量目标,当前已完成 {target_done} 条。"
    else:
        affects = {
            "chunks": ["document_chunks"],
            "chapter-facts": ["chapter_facts"],
            "canon": ["kb_canon_entities"],
            "cards": ["character_cards"],
            "worldbook": ["worldbook_entries"],
            "anchors": ["script_timeline_anchors"],
            "facts_refine": ["chapter_facts"],
            "worldbook_enrich": ["worldbook_entries"],
            "world_key": ["chapter_facts", "script_timeline_anchors"],
        }.get(module, [kind])
        if module in {"chapter-facts", "anchors"} and chunks_total == 0:
            prereqs.append({
                "key": "chunks",
                "label": "章节切块",
                "ok": False,
                "hint": "当前剧本还没有章节切块,请先重做「章节切片」。",
                "count": chunks_total,
                "total": max(chapter_count, 1),
            })
        # worldbook 默认 canon(零 LLM,从知识库人物建)且**无回退** → canon 为空时硬失败
        # 「kb_canon_entities 为空」(前端表现为「点了没反应」,群反馈行者无疆)→ 给阻断 prereq。
        # cards **不在此列**:rebuild_cards_from_canon 在 canon 为空时会退化为 facts 词频(零 LLM),
        # 不会硬失败,不该被拦。
        if module == "worldbook" and source_pref != "llm" and canon_total == 0:
            prereqs.append({
                "key": "canon",
                "label": "规范实体",
                "ok": False,
                "hint": "当前没有规范实体(知识库人物为空),请先重做「知识库人物」,"
                        "或将世界书来源改为 LLM 生成。",
            })
        # facts_refine 精炼的是已有 chapter_facts 行(UPDATE,不新增),facts 为空则无可精炼。
        if module == "facts_refine":
            with connect() as db:
                facts_total = _scalar(db, "select count(*) as c from chapter_facts where script_id = %s")
            if facts_total == 0:
                prereqs.append({
                    "key": "chapter_facts",
                    "label": "章节事实",
                    "ok": False,
                    "hint": "当前剧本还没有章节事实,请先重做「章节事实」。",
                    "count": 0,
                    "total": max(chapter_count, 1),
                })
        # worldbook_enrich 充实的是已存在、标题命中 pattern 的世界书条目;世界书为空则无可充实。
        if module == "worldbook_enrich" and wb_total == 0:
            prereqs.append({
                "key": "worldbook",
                "label": "世界书条目",
                "ok": False,
                "hint": "当前剧本还没有世界书条目,请先重做「世界书」。",
                "count": 0,
            })
        if needs_llm:
            api_id, llm_model = _resolve_extractor_llm(user_id)
            model = llm_model
            if not _has_user_llm_credential(user_id, api_id):
                prereqs.append({
                    "key": "llm_credentials",
                    "label": "LLM API Key",
                    "ok": False,
                    "hint": "请先在「设置 → API 设置」配置知识提取模型的 API Key。",
                    "api_id": api_id,
                    "model": llm_model,
                    "credential_api_id": _credential_api_id_for(api_id),
                    "needs_credentials": True,
                })
            # 真·chat-LLM 路径才算 token+成本(否则「免费」会撒谎)。粗估,UI 标注≈。
            #   canon 全量重抽 = 跑全书 chunks(input≈全文,output≈每块结构化产出);
            #   worldbook-llm = 按 canon 规模的若干次抽取调用。
            est_in = est_out = 0
            if module == "canon":
                # canon 全量重抽走 arc 算法。真实用量用 **arc 感知**的 extract.budget.estimate
                # (与 wizard /llm-extract/estimate 同一权威源,~1.16M 与实测 838k@63% 对得上),
                # 而不是按「整本全文都喂 LLM」估的 chars/2(高估~2x);且原 `length(text)` 列名是
                # 错的(实际列名 content),会直接抛错让估算 500。统一口径,消除三套估算打架。
                try:
                    from extract.budget import estimate as _budget_estimate
                    with connect() as db:
                        _b = _budget_estimate(
                            db, script_id, algorithm="arc",
                            model=(llm_model or "deepseek-v4-flash"),
                        )
                    est_in = int(_b.get("est_input_tokens") or 0)
                    est_out = int(_b.get("est_output_tokens") or 0)
                except Exception:
                    est_in = est_out = 0
            elif module == "cards":                 # 必是 source=='llm'(否则 needs_llm=False)
                # 丰富重建走 run_llm_extraction(arc),与 canon 同口径估;chapter_max 限区间。
                try:
                    from extract.budget import estimate as _budget_estimate
                    _cmax_raw = body.get("chapter_max")
                    try:
                        _cmax = int(_cmax_raw) if _cmax_raw not in (None, "") else None
                    except (TypeError, ValueError):
                        _cmax = None
                    with connect() as db:
                        _b = _budget_estimate(
                            db, script_id, algorithm="arc",
                            model=(llm_model or "deepseek-v4-flash"),
                        )
                    est_in = int(_b.get("est_input_tokens") or 0)
                    est_out = int(_b.get("est_output_tokens") or 0)
                    # chapter_max 区间钳:按 chapter_max/全书章数 线性折减(粗估,UI 标≈)。
                    if _cmax and chapter_count and _cmax < chapter_count:
                        ratio = max(0.0, min(1.0, _cmax / float(chapter_count)))
                        est_in = int(est_in * ratio)
                        est_out = int(est_out * ratio)
                except Exception:
                    est_in = est_out = 0
            elif module == "worldbook":             # 必是 source=='llm'(否则 needs_llm=False)
                _base = max(canon_total, 20)
                est_in = _base * 1500
                est_out = _base * 300
            elif module == "facts_refine":
                # 逐章调 LLM 精炼 summary/in_world_time(extract.facts_refine.refine_script)。
                # 涉及章节数 = [ch_from, ch_to] 区间(默认整本),按 ~1.5k tokens/章估算
                # (与 estimate_module_rebuild 既有粗估口径一致,不接 arc budget——精炼是逐章
                # 独立小调用,非 arc 弧段合并调用)。
                _ch_from_raw = body.get("ch_from")
                _ch_to_raw = body.get("ch_to")
                try:
                    _ch_from = int(_ch_from_raw) if _ch_from_raw not in (None, "") else 1
                except (TypeError, ValueError):
                    _ch_from = 1
                try:
                    _ch_to = int(_ch_to_raw) if _ch_to_raw not in (None, "") else chapter_count
                except (TypeError, ValueError):
                    _ch_to = chapter_count
                _ch_to = min(_ch_to, chapter_count) if chapter_count else _ch_to
                _n_chapters = max(0, _ch_to - _ch_from + 1) if (_ch_to and _ch_from) else 0
                est_in = _n_chapters * 1500
                est_out = _n_chapters * 100  # 摘要产出短(≤200字),output 远小于 input
            elif module == "worldbook_enrich":
                # 命中 pattern 的世界书条目数(与 enrich_script_worldbook 同一 `title ~ pattern`
                # 查询口径,不调 LLM,只查一次 DB)× ~2k tokens/条目。
                _pattern = str(body.get("pattern") or "力量|概念|势力|体系")
                try:
                    with connect() as db:
                        _wb_hit_row = db.execute(
                            "select count(*) as c from worldbook_entries "
                            "where script_id = %s and title ~ %s",
                            (script_id, _pattern),
                        ).fetchone()
                    _n_entries = int(_wb_hit_row["c"]) if _wb_hit_row else 0
                except Exception:
                    _n_entries = 0
                est_in = _n_entries * 2000
                est_out = _n_entries * 400
            elif module == "world_key":              # 必是 use_llm=True(否则 needs_llm=False)
                # 第二层 LLM 窄确认按段(segment)发起调用,一段边界一次调用。段数用零 IO 的
                # classify_segments 纯函数算(不落库、不调 LLM),口径与 backfill_worldlines
                # 实际跑时产生的段数一致。
                try:
                    from extract.world_key_backfill import classify_segments as _classify_segments
                    with connect() as db:
                        _ck_rows = db.execute(
                            "select chapter_index, title, volume_title from script_chapters "
                            "where script_id = %s", (script_id,),
                        ).fetchall()
                    _chapters_for_seg = [
                        {"chapter_index": r["chapter_index"], "title": r.get("title") or "",
                         "volume_title": r.get("volume_title") or ""}
                        for r in (_ck_rows or [])
                    ]
                    _segments = _classify_segments(_chapters_for_seg)
                    _n_segments = max(0, len(_segments) - 1)  # 首段无边界可确认,N 段有 N-1 条边界
                except Exception:
                    _n_segments = 0
                est_in = _n_segments * 1000
                est_out = _n_segments * 150
            if est_in or est_out:
                tokens_est = est_in + est_out
                try:
                    from model_probe import get_pricing
                    _pr = get_pricing(api_id, llm_model) or {}
                    _ip = float(_pr.get("input", 0) or 0)
                    _op = float(_pr.get("output", 0) or 0)
                    cost_est = round((est_in * _ip + est_out * _op) / 1_000_000, 4)
                except Exception:
                    cost_est = 0.0
        note = "该模块将作为后台任务运行,可关闭页面后回来查看进度。"

    return {
        "ok": True,
        "script_id": script_id,
        "module": module,
        "kind": kind,
        "tokens_est": tokens_est,
        "cost_est": cost_est,
        "est_input_tokens": tokens_est,  # 兼容前端 est_input_tokens 读取
        "approximate": tokens_est > 0,   # >0 即非免费,UI 可标「≈」
        "model": model,
        "affects": affects,
        "prereqs": prereqs,
        "note": note,
    }


def schedule_module_rebuild(
    user_id: int, script_id: int, module: str,
    *, body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """异步调度单模块重建。返 {ok, job_id}。"""
    init_db()
    module = normalize_rebuild_module(module)
    body = dict(body or {})
    if module not in REBUILD_MODULES:
        raise ValueError(f"unknown module: {module}")
    kind, action_label, needs_llm = REBUILD_MODULES[module]
    source_pref = str(body.get("source") or body.get("mode") or "").lower()
    if needs_llm:
        # canon 默认走 LLM;worldbook **默认 canon(零 LLM)**,仅 source=='llm' 才需 LLM ——
        # 必须与 _run_module_rebuild 的 `src = source or "canon"` 对齐。否则默认(无 source)的
        # worldbook 会被误要求 LLM 凭证(没配 key 直接 credentials_required),而 runner 实际走
        # canon → 表现为「点生成世界书没反应/报错」。
        if module == "worldbook":
            needs_llm = (source_pref == "llm")
        if module == "canon" and source_pref == "resolve_only":
            needs_llm = False
    # 进度感知角色卡:cards 默认零 LLM(False);仅 source/mode=='llm' 的丰富重建才需 BYOK 凭证。
    if module == "cards" and source_pref == "llm":
        needs_llm = True
    # world_key:结构先验默认零 LLM(REBUILD_MODULES 基线 False);仅 body.use_llm 真值时才需要
    # BYOK(第二层窄确认)—— 与 estimate_module_rebuild 同一对齐规则。
    if module == "world_key" and body.get("use_llm"):
        needs_llm = True
    if needs_llm:
        require_user_llm_credential(user_id)
    with connect() as db:
        if not script_owned(db, script_id, user_id):
            raise ValueError("无权访问该剧本")

    if module == "embeddings":
        _embedding_preflight_or_raise(user_id)

    with connect() as db:
        existing = db.execute(
            "select job_id from import_jobs "
            "where user_id = %s and script_id = %s and kind = %s "
            "and status in ('pending', 'running') order by id desc limit 1",
            (user_id, script_id, kind),
        ).fetchone()
        if existing:
            return {"ok": True, "job_id": existing["job_id"], "reused": True}
        job_id = f"rb_{module}_{script_id}_{secrets.token_hex(6)}"
        db.execute(
            """
            insert into import_jobs(
              job_id, user_id, script_id, kind, status, stage,
              module, sub_kind, overall_total, budget_estimate, stages
            ) values (%s, %s, %s, %s, 'pending', 'pending',
                      %s, %s, 1, %s, %s)
            """,
            (
                job_id, user_id, script_id, kind,
                module, kind,
                Jsonb({"options": body, "action": action_label}),
                Jsonb([{"id": module, "label": action_label, "status": "pending"}]),
            ),
        )
    th = threading.Thread(
        target=_run_module_rebuild,
        args=(job_id, user_id, script_id, module, body),
        daemon=True,
    )
    th.start()
    return {"ok": True, "job_id": job_id, "reused": False, "module": module, "kind": kind}
