"""import_pipeline.stages_llm — 提取模型解析/凭证预检 + LLM 微任务阶段

来源: 原 rpg/platform_app/import_pipeline.py _resolve_extractor_llm 族 / _stage_story_phase_llm / _stage_cards / _stage_worldbook / _stage_npc_voices / _parse_json(原 L917-1125, 1264-1597, 1735-1875, 2141-2151) 区段,纯机械搬家(函数体逐字未动),零行为变化。
"""
from __future__ import annotations

import json
import re
from typing import Any

from psycopg.types.json import Jsonb

from ..db import connect
from .errors import MissingUserCredentialError
from core.llm_backend import DEFAULT_FALLBACK_API, DEFAULT_FALLBACK_MODEL
from model_aliases import credential_storage_api_id, normalize_api_id


def _resolve_extractor_llm(user_id: int) -> tuple[str, str]:
    """解析拆书流水线 LLM 配置。

    优先级:
      1. user_preferences["extractor.api_id"] / ["extractor.model_real_name"]
      2. user_preferences["agent.api_id"] / ["agent.model_real_name"]
      3. 默认: vertex_ai / gemini-3.5-flash

    返回 (api_id, model)。
    """
    from agents._harness import resolve_api_and_model
    api_id, model = resolve_api_and_model(
        user_id,
        api_pref_key="extractor.api_id",
        model_pref_key="extractor.model_real_name",
        default_api=DEFAULT_FALLBACK_API,
        default_model=DEFAULT_FALLBACK_MODEL,
    )
    # 别名→canonical 统一走全量别名表(原 _normalize_llm_api_id 只覆盖 vertex 残缺子集)。
    return normalize_api_id(api_id), model


def _credential_api_id_for(api_id: str) -> str:
    # 薄委托:canonical→凭证寻址(Vertex 存 "AgentPlatform" 行)。与 normalize_api_id 方向相反。
    return credential_storage_api_id(api_id)


def require_user_llm_credential(user_id: int) -> dict[str, str]:
    """Preflight paid LLM work before any import writes user-visible data."""
    api_id, model = _resolve_extractor_llm(user_id)
    _require_user_llm_credential(user_id, api_id, model)
    return {
        "api_id": api_id,
        "model": model,
        "credential_api_id": _credential_api_id_for(api_id),
    }


def _has_user_llm_credential(user_id: int | None, api_id: str) -> bool:
    # 薄委托 → core.llm_backend.user_can_use_provider(单一真源:vertex→BYOK SA / 其它→用户 key)。
    from core.llm_backend import user_can_use_provider
    return user_can_use_provider(user_id, api_id)


def _require_user_llm_credential(user_id: int, api_id: str, model: str) -> None:
    """Production import pipeline must use user-scoped credentials only."""
    if not _has_user_llm_credential(user_id, api_id):
        raise MissingUserCredentialError(api_id, model, _credential_api_id_for(api_id))

def _stage_story_phase_llm(ctl: JobController, user_id: int, script_id: int) -> None:
    """facts 完成后，一次 LLM call 把章节范围分到 开端/发展/高潮/结局/番外。
    成功 → 按范围批量 update chapter_facts.story_phase；
    失败/解析不出 → 全部回退 "未明"。
    """
    api_id, model = _resolve_extractor_llm(user_id)

    with connect() as db:
        rows = db.execute(
            "select chapter, summary, title from chapter_facts "
            "where script_id = %s and (story_phase = '' or story_phase is null) "
            "order by chapter",
            (script_id,),
        ).fetchall()

    if not rows:
        return

    total = len(rows)
    # 均匀采样 ≤30 章喂给 LLM (成本控)；保留每章的 chapter 号让模型按号给区间
    if total <= 30:
        sample = rows
    else:
        step = max(1, total // 30)
        sample = rows[::step][:30]
    lines = "\n".join(
        f"第{r['chapter']}章《{r['title']}》: {(r['summary'] or '')[:120]}"
        for r in sample
    )
    prompt = (
        f"这本书共 {total} 章 (第 1 章 — 第 {total} 章)，以下是均匀采样的章节摘要。"
        "请把章节范围划分到这 5 个阶段:开端 / 发展前期 / 发展中期 / 发展后期 / 结局。"
        "不需要每个阶段都出现 — 只列实际存在的。这 5 个 phase 标签是底座固定枚举,"
        "saves 出生点 wizard 和 GM 翻阅都依赖这套命名,**不要使用其他标签(如 高潮/番外/序章 等)**。\n\n"
        "返回严格 JSON 数组，每段一项,无任何前后文字:\n"
        '[{"phase":"开端","start":1,"end":N},{"phase":"发展前期","start":N+1,"end":M},...]\n\n'
        f"章节摘要:\n{lines}"
    )
    try:
        # 结构化微任务禁深思(268 实锤族)+空正文护栏
        from agents._harness import call_agent_json_guarded
        raw, last = call_agent_json_guarded(
            api_id, model,
            "你是小说剧情分析器,只输出 JSON 数组。",
            prompt,
            user_id,
            log_tag="story_phase",
            max_tokens=400,
            no_think=True,
            agent_kind="import_pipeline",
        )
        from ..usage import compute_cost
        cost = float(compute_cost(api_id, model, last))
        ctl.add_usage(int(last.get("input_tokens", 0)), int(last.get("output_tokens", 0)), cost)

        # 5 段固定枚举,与 saves wizard birthpoints fallback 完全一致 —
        # phase_label 是跨层共享 key (chapter_facts.story_phase / phase_digests.phase_label
        # / state.world.timeline.current_phase / worldbook_agent._resolve_anchor)。
        valid = {"开端", "发展前期", "发展中期", "发展后期", "结局"}
        ranges = _parse_json(raw)
        # LLM 返非 array (dict / None / 解析失败) 时退化为**5 段均分**,
        # 而不是塞全书单一 "发展" — 单 phase 会让 worldbook_agent.consult 在任何
        # current_phase 输入下都 fallback 到那同一段,phase_digests 索引等于失效。
        # 5 段均分至少保证 birthpoints / phase_digests / GM 翻阅各层 phase 一致。
        if not isinstance(ranges, list) or not ranges:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[story_phase] LLM returned non-array %r, falling back to 5-bucket even split",
                type(ranges).__name__,
            )
            try:
                ctl.update(warnings={
                    "stage": "story_phase_llm",
                    "exception": "InvalidResponse",
                    "message": f"LLM 返回非数组(type={type(ranges).__name__}),已退化为 5 段均分",
                })
            except Exception:
                pass
            ranges = _even_split_phases(total)

        with connect() as db:
            for item in ranges:
                if not isinstance(item, dict):
                    continue
                phase = str(item.get("phase", "")).strip()
                if phase not in valid:
                    continue
                try:
                    start = int(item.get("start") or 1)
                    end = int(item.get("end") or total)
                except (TypeError, ValueError):
                    continue
                db.execute(
                    "update chapter_facts set story_phase = %s "
                    "where script_id = %s and chapter between %s and %s "
                    "and (story_phase = '' or story_phase is null)",
                    (phase, script_id, start, end),
                )
            # 剩余没匹配到的章 → 走 5 段均分兜底而不是塞 "未明"
            # ("未明" 标签不在 valid 集合里,会让 phase_digests 聚合出无意义的
            # "未明" phase entry,worldbook_agent 命中后给 GM 注入空摘要)
            _backfill_unphased_with_even_split(db, script_id, total)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[story_phase] LLM call failed, falling back to 5-bucket even split: %s",
            exc, exc_info=True,
        )
        try:
            with connect() as db:
                _backfill_unphased_with_even_split(db, script_id, total)
        except Exception as exc2:
            _logging.getLogger(__name__).warning(
                "[story_phase] fallback update failed: %s", exc2, exc_info=True,
            )
        try:
            ctl.update(warnings={
                "stage": "story_phase_llm",
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
            })
        except Exception:
            pass


def _backfill_unphased_with_even_split(db, script_id: int, total: int) -> None:
    """LLM 推断失败/部分缺失时,把 story_phase 仍为空的章节按 5 段均分填回。
    避免 "未明" 标签污染 phase_digests。"""
    if total <= 0:
        return
    for item in _even_split_phases(total):
        db.execute(
            "update chapter_facts set story_phase = %s "
            "where script_id = %s and chapter between %s and %s "
            "and (story_phase = '' or story_phase is null)",
            (item["phase"], script_id, item["start"], item["end"]),
        )


def _even_split_phases(total: int) -> list[dict[str, Any]]:
    """把 total 章按 5 段均分:开端 / 发展前期 / 发展中期 / 发展后期 / 结局。

    与 saves wizard birthpoints fallback 用同一组 phase 标签,避免存档侧
    current_phase 和剧本侧 phase_digests 对不上号(常见症状:存档 timeline
    显示『开端』但 worldbook_agent 永远 fallback 到第一个 phase)。
    """
    if total <= 0:
        return []
    labels = ["开端", "发展前期", "发展中期", "发展后期", "结局"]
    # 章节 ≤ 5 时直接一一对应 + 截短
    if total <= 5:
        return [{"phase": labels[i], "start": i + 1, "end": i + 1} for i in range(total)]
    bucket = total // 5
    out = []
    for i, lab in enumerate(labels):
        s = i * bucket + 1
        e = (i + 1) * bucket if i < 4 else total  # 末段吃余数
        out.append({"phase": lab, "start": s, "end": e})
    return out

def _stage_cards(ctl: JobController, user_id: int, script_id: int, entities: list[dict[str, Any]]) -> int:
    """LLM 给 top N 人物生成人设卡。

    简化：调 call_agent_json 让模型按 JSON schema 输出。
    超时/失败的角色跳过，不阻断整个流水线。
    """
    from .. import knowledge
    api_id, model = _resolve_extractor_llm(user_id)

    top_n = 30
    targets = [e for e in entities[:top_n] if e["count"] >= 5]
    ctl.update(stage_progress=0, stage_total=len(targets))

    # 取每个角色的最相关文本片段（出现该名字的前 3 章节）
    with connect() as db:
        chapters_idx = db.execute(
            "select chapter_index, content from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        book_row = db.execute(
            "select id from books where script_id = %s", (script_id,),
        ).fetchone()
        int(book_row["id"]) if book_row else None

    # 拉该 script 的 chapter_facts（用摘要做二次 pass 输入）
    with connect() as db:
        fact_rows = db.execute(
            "select chapter, summary, characters from chapter_facts "
            "where script_id = %s order by chapter",
            (script_id,),
        ).fetchall()

    # #5 角色卡去重: 预载该 script 现有 NPC 卡的 name/full_name/aliases(归一化)。
    # extract 时若候选角色(名或别名)已存在 → 跳过,避免"短名/全名"等变体产生重复卡。
    # 只跳过不覆盖 → 不会 clobber 用户手编卡;不改唯一索引/不迁移,零数据风险。
    def _norm_name(s: Any) -> str:
        return str(s or "").strip().casefold().replace(" ", "").replace("·", "").replace("・", "")
    existing_keys: set[str] = set()
    try:
        with connect() as db:
            for cr in db.execute(
                "select name, full_name, aliases from character_cards "
                "where script_id=%s and card_type='npc'",
                (script_id,),
            ).fetchall():
                existing_keys.add(_norm_name(cr.get("name")))
                if cr.get("full_name"):
                    existing_keys.add(_norm_name(cr.get("full_name")))
                for _a in (cr.get("aliases") or []):
                    existing_keys.add(_norm_name(_a))
        existing_keys.discard("")
    except Exception:
        existing_keys = set()

    generated = 0
    llm_failures = 0  # phase_backend: 累计 LLM 调用失败次数,>50% 标 partial
    for i, entity in enumerate(targets):
        if ctl.is_cancelled():
            raise RuntimeError("cancelled")
        name = entity["name"]

        # #5 去重(pre-LLM): 候选名已等于某现有卡的 name/别名 → 跳过,省 LLM 调用 + 不重复建卡。
        if _norm_name(name) in existing_keys:
            ctl.update(stage_progress=i + 1)
            continue

        # 优先用 chapter_facts 摘要（信噪比高），fallback 到原始章节文本片段
        relevant_summaries = []
        for fr in fact_rows:
            chars = fr.get("characters") or []
            if isinstance(chars, list) and any(
                isinstance(c, dict) and c.get("name") == name for c in chars
            ):
                relevant_summaries.append(f"第{fr['chapter']}章: {(fr['summary'] or '')[:200]}")
            if len(relevant_summaries) >= 8:
                break

        if relevant_summaries:
            context = "章节摘要（该角色相关）：\n" + "\n".join(relevant_summaries)
        else:
            snippets = []
            for ch in chapters_idx:
                if name in ch["content"]:
                    snippets.append(ch["content"][:1500])
                    if len(snippets) >= 3:
                        break
            if not snippets:
                ctl.update(stage_progress=i + 1)
                continue
            context = "文本片段：\n" + "\n---\n".join(snippets)

        # task 47: 显式让 LLM 判断"这是真人名吗",false 时直接跳过不写卡。
        # 2-3 字中文 ngram 候选有大量副词/连词/动词性短语(为什么/的声音/紧接着/有德的)
        # 维护硬编码 blacklist 永远跟不上内容,LLM 一个布尔判断成本极低且精度高。
        prompt = (
            f"分析「{name}」是否是真实的角色人名(不是副词/连词/动词/地名/物品/碎片),返回严格 JSON:\n"
            "如果不是真人名,返回 {\"is_character\": false}\n"
            "如果是真人名,返回 {\n"
            "  \"is_character\": true,\n"
            "  \"identity\": \"身份/职业/势力\",\n"
            "  \"appearance\": \"外貌描述\",\n"
            "  \"personality\": \"性格特点\",\n"
            "  \"speech_style\": \"说话风格\",\n"
            "  \"secrets\": \"秘密或重要伏笔(如无则空字符串)\",\n"
            "  \"aliases\": [\"别名1\"]\n"
            "}\n\n"
            + context
        )
        try:
            # 结构化微任务禁深思(268 实锤族)+空正文护栏
            from agents._harness import call_agent_json_guarded
            raw, last = call_agent_json_guarded(
                api_id, model,
                "你是角色卡提取器,严格判断 name 是否为真实角色人名。只输出 JSON。"
                "【虚构铁律】本作是虚构小说,即使角色与真实历史人物同名,所有字段也"
                "**只能依据给定的章节片段/摘要**,严禁引入你自己的真实史实/生平/百科知识"
                "(如『活捉单于』『封冠军侯』这类片段里没有的内容),给不出片段依据就留空。",
                prompt,
                user_id,
                log_tag="card_extract",
                max_tokens=700,
                no_think=True,
                agent_kind="import_pipeline",
            )
            data = _parse_json(raw)
            # 累 usage(无论是否写卡,LLM 都跑了)
            from ..usage import compute_cost
            cost = float(compute_cost(api_id, model, last))
            ctl.add_usage(int(last.get("input_tokens", 0)), int(last.get("output_tokens", 0)), cost)
            # task 47: LLM 明确说不是人名 → 跳过;identity 为空也判定为假名(双保险)
            if data and data.get("is_character") is not False and (data.get("identity") or "").strip():
                # #5 去重(post-LLM): 候选名或其别名已存在 → 跳过,不创建短名/全名变体重复卡。
                _cand_keys = {_norm_name(name)} | {_norm_name(a) for a in (data.get("aliases") or [])}
                _cand_keys.discard("")
                if _cand_keys & existing_keys:
                    ctl.update(stage_progress=i + 1)
                    continue
                # 写入 character_cards(含 secrets 字段)
                knowledge.upsert_character_card(user_id, script_id, {
                    "name": name,
                    "aliases": data.get("aliases") or [],
                    "identity": data.get("identity") or "",
                    "appearance": data.get("appearance") or "",
                    "personality": data.get("personality") or "",
                    "speech_style": data.get("speech_style") or "",
                    "secrets": data.get("secrets") or "",
                    "metadata": {"source": "llm_pipeline", "freq": entity["count"]},
                })
                generated += 1
                existing_keys |= _cand_keys  # 防同一次 run 内后续变体重复建卡
        except Exception as exc:
            # phase_backend: 不再 silent swallow,记 warning(exc_info=True)
            # 同时累计 LLM 失败,>50% targets 全失败时主 worker 标 partial
            llm_failures += 1
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[cards] LLM card for %r failed: %s", name, exc, exc_info=True,
            )
        ctl.update(stage_progress=i + 1)
    # 失败比例 >50% → 写 warnings 到 import_jobs,让 _run_pipeline 标 partial
    if targets and llm_failures > len(targets) // 2:
        try:
            ctl.update(
                warnings={
                    "stage": "cards",
                    "llm_failures": llm_failures,
                    "targets": len(targets),
                    "generated": generated,
                },
            )
        except Exception:
            pass
    # 返 (generated, llm_failures) 让 _run_pipeline 决定是否标 partial
    setattr(_stage_cards, "_last_llm_failures", llm_failures)
    setattr(_stage_cards, "_last_targets", len(targets))
    return generated


def _stage_worldbook(ctl: JobController, user_id: int, script_id: int) -> int:
    """LLM 从 chapter_facts 摘要 + facts 提取世界观条目入 worldbook_entries。"""
    api_id, model = _resolve_extractor_llm(user_id)

    with connect() as db:
        book_row = db.execute(
            "select id from books where script_id = %s", (script_id,),
        ).fetchone()
        if not book_row:
            return 0
        book_id = int(book_row["id"])

        # 用 chapter_facts 摘要 + locations/factions/concepts 作为输入（比原始文本信噪比高）
        fact_rows = db.execute(
            "select chapter, summary, locations, factions, concepts "
            "from chapter_facts where script_id = %s order by chapter limit 40",
            (script_id,),
        ).fetchall()

    ctl.update(stage_progress=0, stage_total=1)

    if fact_rows:
        summaries_block = "\n".join(
            f"第{r['chapter']}章: {(r['summary'] or '')[:100]}"
            for r in fact_rows[:30]
        )
        # 聚合高频地点/势力/概念作为提示
        from collections import Counter as _Counter
        loc_cnt: _Counter = _Counter()
        fac_cnt: _Counter = _Counter()
        con_cnt: _Counter = _Counter()
        for r in fact_rows:
            for item in (r.get("locations") or []):
                if isinstance(item, dict):
                    loc_cnt[item.get("name", "")] += item.get("count", 1)
            for item in (r.get("factions") or []):
                if isinstance(item, dict):
                    fac_cnt[item.get("name", "")] += item.get("count", 1)
            for item in (r.get("concepts") or []):
                if isinstance(item, dict):
                    con_cnt[item.get("name", "")] += item.get("count", 1)
        top_locs = [n for n, _ in loc_cnt.most_common(10) if n]
        top_facs = [n for n, _ in fac_cnt.most_common(10) if n]
        top_cons = [n for n, _ in con_cnt.most_common(10) if n]
        hints = (
            f"高频地点: {', '.join(top_locs)}\n"
            f"高频势力: {', '.join(top_facs)}\n"
            f"高频概念: {', '.join(top_cons)}\n"
        )
        seed = hints + "\n章节摘要：\n" + summaries_block
    else:
        with connect() as db:
            chapters = db.execute(
                "select content from script_chapters where script_id = %s order by chapter_index",
                (script_id,),
            ).fetchall()
        seed = "\n".join(c["content"] for c in chapters)[:8000]

    # 读取新提取管线已落库的纪元(若存在),作为铁律塞进 prompt,治 _stage_worldbook 独立 LLM
    # 凭空编"哥本哈根研究所 2927年创立"这种带具体年份的 hallucination
    era_lock = ""
    with connect() as db:
        era_row = db.execute(
            "select content from worldbook_entries where script_id=%s and title='纪元' limit 1",
            (script_id,),
        ).fetchone()
        if era_row and era_row.get("content"):
            era_lock = str(era_row["content"])[:200]
    era_iron_rule = (
        f"【纪元铁律】{era_lock}\n严禁在 content 中编造具体的创立年/事件年份;"
        "若必须提及年代,只能引用上述纪元,**绝不写真实历史年份**(1927/1935/1940 等)。\n"
        if era_lock else
        "【纪元约束】不要在 content 中编造具体年份(避免幻觉);只描述背景/角色/地理/势力关系。\n"
    )
    prompt = (
        era_iron_rule +
        "根据下面的章节摘要和高频实体，提取重要的世界观条目（地点/势力/概念），返回严格 JSON 数组：\n"
        "[{\"name\":\"...\",\"keys\":[\"关键词1\",\"关键词2\"],\"content\":\"≤200字解释\",\"priority\":80}]\n"
        "数量上限 20。\n\n" + seed
    )
    try:
        # 结构化微任务禁深思(268 实锤族)+空正文护栏
        from agents._harness import call_agent_json_guarded
        raw, last = call_agent_json_guarded(
            api_id, model,
            "你是世界书编辑，只输出 JSON 数组。",
            prompt,
            user_id,
            log_tag="worldbook_extract",
            max_tokens=2000,
            no_think=True,
            agent_kind="import_pipeline",
        )
        from ..usage import compute_cost
        cost = float(compute_cost(api_id, model, last))
        ctl.add_usage(int(last.get("input_tokens", 0)), int(last.get("output_tokens", 0)), cost)
        entries = _parse_json(raw) or []
        if not isinstance(entries, list):
            entries = []
        count = 0
        with connect() as db:
            for entry in entries[:20]:
                if not isinstance(entry, dict) or not entry.get("name"):
                    continue
                _cur = db.execute(
                    """
                    insert into worldbook_entries(
                      book_id, script_id, title, keys, content, priority, enabled, metadata
                    ) values (%s, %s, %s, %s, %s, %s, true, %s)
                    on conflict (script_id, title) do update set
                      content   = excluded.content,
                      keys      = excluded.keys,
                      priority  = excluded.priority,
                      metadata  = excluded.metadata,
                      updated_at = now()
                    where coalesce(worldbook_entries.metadata->>'source','') <> 'editor'
                    """,
                    (
                        book_id, script_id,
                        str(entry["name"])[:120],
                        Jsonb(entry.get("keys") or [entry["name"]]),
                        str(entry.get("content") or "")[:2000],
                        int(entry.get("priority") or 80),
                        Jsonb({"source": "llm_pipeline"}),
                    ),
                )
                # rowcount=1 表示插入或更新成功;冲突且 where 不满足(editor 条目)时 rowcount=0。
                # psycopg3:rowcount 在 execute() 返回的 cursor 上,不在 Connection 上
                # (旧代码 `db.rowcount` → AttributeError,整个 worldbook LLM 抽取阶段崩、条目没入库)。
                count += (getattr(_cur, "rowcount", 0) or 0)
        ctl.update(stage_progress=1)
        # phase_backend: 标记 worldbook 阶段写了多少条 — 0 当作 partial 让上层标 done_with_errors
        setattr(_stage_worldbook, "_last_count", count)
        return count
    except Exception as exc:
        # phase_backend: 不 silent swallow,把 LLM 失败写到 import_jobs.error + warnings
        import logging as _logging
        import traceback as _tb
        _logging.getLogger(__name__).warning(
            "[worldbook] LLM extract failed: %s", exc, exc_info=True,
        )
        try:
            ctl.update(
                stage_progress=1,
                error=f"_stage_worldbook: {type(exc).__name__}: {str(exc)[:300]}",
                warnings={
                    "stage": "worldbook",
                    "exception": type(exc).__name__,
                    "message": str(exc)[:500],
                    "traceback": _tb.format_exc()[:800],
                },
            )
        except Exception:
            pass
        setattr(_stage_worldbook, "_last_count", 0)
        return 0

def _stage_npc_voices(user_id: int, script_id: int, *, max_npc: int = 20, only_empty: bool = True) -> int:
    """LLM 抽 NPC 的 personality / speech_style / sample_dialogue 结构化字段,写回 character_cards。

    设计原则(harness):
      · 数据底座产出**结构化字段**,而不是注入原文片段让 GM 自学
      · GM 看 character_card 直接拿到 "性格: 平静、镇定、戏谑信息密度高"
        + "说话风格: 称玩家'被选中者',尾音常用'呢/呐'" + "台词示例: [...]"
      · 不依赖 GM 遵守 "学风格不复述" 这种 prompt 指令
      · LLM 抽的是高密度结构化数据,代价合理(每 NPC 一次 LLM,典型 30-100 NPC)

    数据源(给 LLM 的上下文):
      · character_card.identity + background + aliases (已抽过的结构化)
      · documents 反查 name + aliases 命中段 ×3,每段 ±400 字符(上下文足够丰富)

    LLM 输出 schema:
      {
        "personality": "≤80字性格特点,具体形容词+行为倾向",
        "speech_style": "≤80字说话风格,语气+常用词+句式特征",
        "sample_dialogue": ["原文里此人最有代表性的 2-3 句台词,逐字摘"]
      }

    args:
      script_id: 目标剧本
      max_npc: 单次最多处理几个 NPC (按 importance desc),避免一次跑爆
      only_empty: True 时只补 personality/speech_style 全空的卡,False 时全部覆盖重抽

    返回 backfilled count。
    """
    api_id, model = _resolve_extractor_llm(user_id)
    import logging as _log
    log = _log.getLogger(__name__)
    with connect() as db:
        where = "script_id=%s and card_type='npc'"
        params: list = [script_id]
        if only_empty:
            where += " and (coalesce(personality,'')='' or coalesce(speech_style,'')='')"
        rows = db.execute(
            f"select id, name, aliases, identity, background, "
            f"       first_revealed_chapter, importance "
            f"from character_cards where {where} "
            f"order by importance desc nulls last, priority desc nulls last "
            f"limit %s",
            (*params, int(max_npc)),
        ).fetchall() or []
    if not rows:
        return 0

    backfilled = 0
    for r in rows:
        name = r["name"]
        first_ch = int(r["first_revealed_chapter"] or 1)
        # 拉原文片段(用 lookup_entity 的 helper 逻辑,这里就地实现)
        try:
            with connect() as db:
                doc_rows = db.execute(
                    "select sc.chapter_index, d.content from documents d "
                    "join script_chapters sc on sc.id = d.chapter_id "
                    "where d.script_id=%s and sc.chapter_index between %s and %s "
                    "order by sc.chapter_index asc",
                    (script_id, first_ch, first_ch + 2),
                ).fetchall() or []
            aliases = r["aliases"] or []
            if isinstance(aliases, str):
                aliases = [a.strip() for a in aliases.split(",") if a.strip()]
            terms = [name] + [a for a in aliases if isinstance(a, str) and a]
            excerpts: list[str] = []
            for dr in doc_rows:
                content = dr["content"] or ""
                for term in terms:
                    idx = content.find(term)
                    if idx < 0:
                        continue
                    start = max(0, idx - 400)
                    end = min(len(content), idx + len(term) + 400)
                    excerpts.append(content[start:end].strip())
                    break
                if len(excerpts) >= 3:
                    break
            if not excerpts:
                log.info(f"[npc_voice] skip {name}: no source excerpts found")
                continue
        except Exception as exc:
            log.warning(f"[npc_voice] skip {name}: excerpt fetch failed: {exc}")
            continue

        prompt = (
            f"分析下述 NPC「{name}」在原文中的人物特征,产出**结构化**性格与说话风格,"
            f"用于 RPG 引擎让 GM 拿到结构化数据后准确扮演,**不要长篇大论解读**。\n\n"
            f"已知身份: {r['identity'] or '(空)'}\n"
            f"已知背景: {r['background'] or '(空)'}\n\n"
            f"原文片段(此 NPC 出场场景,可能不完整):\n"
            + "\n---\n".join(excerpts) + "\n\n"
            f"严格输出 JSON(无前后文字),字段必填:\n"
            f"{{\n"
            f'  "personality": "≤80字性格,用具体形容词+行为倾向(例:平静、戏谑、信息密度高、不轻易动情绪)",\n'
            f'  "speech_style": "≤80字说话风格,语气+常用词+句式特征(例:称对方为被选中者,尾音常带呢/呐,'
            f'冷峻陈述穿插戏谑反问)",\n'
            f'  "sample_dialogue": ["逐字摘原文 2-3 句最有代表性的台词,保留引号"]\n'
            f"}}"
        )
        try:
            # 结构化微任务禁深思(268 实锤族)+空正文护栏
            from agents._harness import call_agent_json_guarded
            raw, last = call_agent_json_guarded(
                api_id, model,
                "你是 RPG 角色档案抽取器,只输出结构化 JSON,不解释。"
                "【虚构铁律】本作是虚构小说,即使角色与真实历史人物同名,也**只能依据上面给的原文片段**"
                "总结其性格/说话风格,严禁掺入你自己知道的真实史实/生平/百科;片段不足就写概括性短语,不要脑补。",
                prompt, user_id, log_tag="npc_voice", max_tokens=500, no_think=True,
                agent_kind="import_pipeline",
            )
            data = _parse_json(raw)
            if not isinstance(data, dict):
                log.warning(f"[npc_voice] {name}: LLM returned non-dict {type(data).__name__}")
                continue
            personality = str(data.get("personality") or "").strip()[:200]
            speech_style = str(data.get("speech_style") or "").strip()[:200]
            sample = data.get("sample_dialogue") or []
            if not isinstance(sample, list):
                sample = []
            sample = [str(x)[:200] for x in sample[:5] if x]
            if not personality and not speech_style:
                log.info(f"[npc_voice] {name}: LLM 返回空 personality+speech_style, skip update")
                continue
            with connect() as db:
                db.execute(
                    "update character_cards set "
                    "  personality = case when length(%s) > 0 then %s else personality end, "
                    "  speech_style = case when length(%s) > 0 then %s else speech_style end, "
                    "  sample_dialogue = case when array_length(%s::text[], 1) > 0 then %s::jsonb else sample_dialogue end, "
                    "  updated_at = now() "
                    "where id = %s",
                    (personality, personality, speech_style, speech_style,
                     sample, json.dumps(sample, ensure_ascii=False), r["id"]),
                )
            backfilled += 1
            log.info(f"[npc_voice] {name}: 写回 personality={personality[:30]}... speech_style={speech_style[:30]}...")
        except Exception as exc:
            log.warning(f"[npc_voice] {name}: LLM call failed: {exc}")
            continue
    return backfilled

def _parse_json(text: str) -> Any:
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I | re.M).strip()
    m = re.search(r"[\[\{].*[\]\}]", cleaned, re.S)
    if m:
        cleaned = m.group(0)
    try:
        return json.loads(cleaned)
    except Exception:
        return None
