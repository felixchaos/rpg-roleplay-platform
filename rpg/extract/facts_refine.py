"""extract/facts_refine.py — chapter_facts 的 LLM 精炼层(拆书审计 R2/R3 根修)。

背景:生产 chapter_facts 全表 30909 行 100% source=deterministic_import,summary 是
原文残句拼接(「；」join + 240 字硬截断)、story_time_label 96% 复读章标题、
in_world_time 全库 0% 非空 —— Pass 2 LLM 提取早已设计(per_chapter.py schema 明文要求
「chapter_summary 30-150字浓缩,绝不照抄原文」)但产出物从未写回 chapter_facts(死代码)。

本模块把这条断链接活:逐章调 LLM 产出【真摘要】+【故事内时间】,UPSERT 回
chapter_facts.summary / in_world_time,metadata.source='llm_refined'(下游可按来源门控)。
按需触发(admin CLI / 未来 UI 按钮),BYOK 模型,绝不进导入关键路径 —— 未跑时确定性
占位仍在,只是标着 deterministic_import。

CLI: python -m extract.facts_refine <script_id> [--from N] [--to M] [--api-id X --model Y] [--apply]
     默认 dry-run 打印前 3 章产出;--apply 才写库。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 9000   # 每章喂给 LLM 的正文上限(超长章截断,足够写 150 字摘要)
MIN_SUMMARY_CHARS = 20
MAX_SUMMARY_CHARS = 200
MAX_TIME_CHARS = 40

_SYSTEM = """你是小说章节归纳器。读一章正文,输出严格 JSON(不要代码围栏):
{"chapter_summary": "本章主线的第三人称浓缩,30-150字。写清谁做了什么、局势有何变化。**绝不照抄原文句子**,必须是归纳复述。不要评价不要剧透后文。",
 "in_world_time": "本章故事内时间的简短归纳,如「穿越当日下午」「三个月后的冬天」「紧接上章当夜」;正文无法判断则给空字符串"}
只输出这一个 JSON 对象。"""


def build_refine_prompts(title: str, content: str) -> tuple[str, str]:
    body = (content or "")[:MAX_CONTENT_CHARS]
    user = f"【章节标题】{(title or '').strip() or '(无题)'}\n【正文】\n{body}"
    return _SYSTEM, user


def validate_refined(raw_text: str, content: str) -> dict | None:
    """解析+验收。拒收:非 JSON/摘要过短过长/摘要照抄原文(前40字连续片段命中正文)。"""
    if not raw_text:
        return None
    text = raw_text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    summary = re.sub(r"\s+", " ", str(data.get("chapter_summary") or "").strip())
    itime = str(data.get("in_world_time") or "").strip()[:MAX_TIME_CHARS]
    if not (MIN_SUMMARY_CHARS <= len(summary) <= MAX_SUMMARY_CHARS + 60):
        return None
    summary = summary[:MAX_SUMMARY_CHARS]
    # 照抄检测:摘要里任一 25 字连续片段逐字出现在正文 → 拒(要求归纳复述)
    body = re.sub(r"\s+", "", content or "")
    probe = re.sub(r"\s+", "", summary)
    for i in range(0, max(1, len(probe) - 25), 12):
        if probe[i:i + 25] and probe[i:i + 25] in body:
            return None
    return {"summary": summary, "in_world_time": itime}


def refine_chapter(db, script_id: int, chapter_index: int, user_id: int,
                   api_id: str, model: str) -> dict | None:
    """单章精炼。返回写库负载或 None(跳过)。只读 chunks,不在此写库。"""
    rows = db.execute(
        "select content from document_chunks where script_id=%s and chapter_index=%s "
        "order by chunk_index limit 12",
        (int(script_id), int(chapter_index)),
    ).fetchall()
    content = "\n".join((r.get("content") or "") for r in (rows or [])).strip()
    if len(content) < 80:
        return None  # 空/短章(幽灵章残留)不烧钱
    trow = db.execute(
        "select title from script_chapters where script_id=%s and chapter_index=%s",
        (int(script_id), int(chapter_index)),
    ).fetchone()
    sys_p, usr_p = build_refine_prompts(str((trow or {}).get("title") or ""), content)
    from agents._harness import call_agent_json_guarded
    # 结构化微任务禁深思(268 实锤族)+空正文护栏
    text, _usage = call_agent_json_guarded(
        api_id, model, sys_p, usr_p, user_id,
        tool_schema=None, max_tokens=300, timeout_sec=45,
        no_think=True, log_tag="facts_refine",
    )
    return validate_refined(text or "", content)


def apply_refined(db, script_id: int, chapter_index: int, refined: dict) -> None:
    """UPSERT 回 chapter_facts:真摘要+故事内时间,来源改标 llm_refined(R4 下游按此门控)。"""
    db.execute(
        """update chapter_facts
           set summary = %s,
               in_world_time = nullif(%s, ''),
               confidence = greatest(coalesce(confidence, 0), 0.85),
               metadata = coalesce(metadata, '{}'::jsonb) || %s::jsonb
           where script_id = %s and chapter = %s""",
        (refined["summary"], refined.get("in_world_time") or "",
         json.dumps({"source": "llm_refined"}, ensure_ascii=False),
         int(script_id), int(chapter_index)),
    )


def refine_script(script_id: int, user_id: int, *, ch_from: int = 1, ch_to: int | None = None,
                  api_id: str | None = None, model: str | None = None,
                  apply: bool = False,
                  should_cancel=None, progress_cb=None) -> dict[str, Any]:
    """批量精炼 [ch_from, ch_to]。逐章独立失败跳过;apply=False 只打样前 3 章。

    should_cancel: 可选无参回调,返回真值表示用户已请求取消 —— 每章开头检查一次,
      命中即停在章边界并返回 {"cancelled": True} + 已完成的统计(已 apply 的章保留)。
      整本几百章逐章调 LLM 要几十分钟,没有这个检查点「取消」就只是个不生效的按钮。
    progress_cb: 可选 (done, total) 回调,每章一次,给 job 进度条用。
    """
    from platform_app.db import connect, init_db
    init_db()
    from agents.recorder import _resolve_recorder_api_and_model
    rapi, rmodel = _resolve_recorder_api_and_model(user_id, api_id, model)
    if not rapi or not rmodel:
        return {"ok": False, "error": "无可用 api/model(BYOK 未配置)"}
    with connect() as db:
        row = db.execute(
            "select coalesce(max(chapter_index),0) m from script_chapters where script_id=%s",
            (int(script_id),),
        ).fetchone()
        max_ch = int(row["m"] or 0)
    ch_to = min(int(ch_to or max_ch), max_ch)
    done = skipped = failed = 0
    samples: list[dict] = []
    cancelled = False
    total_ch = max(ch_to - int(ch_from) + 1, 0)
    for ch in range(int(ch_from), ch_to + 1):
        # 取消检查点:放在每章 LLM 调用之前 → 最坏等一章(秒级),而不是等整本跑完。
        if should_cancel is not None:
            try:
                if should_cancel():
                    cancelled = True
                    log.info("[facts_refine] script=%s 用户取消,停在 ch%d(已精炼 %d 章)",
                             script_id, ch, done)
                    break
            except Exception:
                pass  # 取消信号查询失败不该中断正在跑的任务
        # 进度回传放在循环体最前面(done=已处理章数):下面 try 里有 continue,放末尾会被跳过。
        if progress_cb is not None:
            try:
                progress_cb(ch - int(ch_from), total_ch)
            except Exception:
                pass  # 进度回传失败不该中断任务
        try:
            with connect() as db:
                refined = refine_chapter(db, script_id, ch, user_id, rapi, rmodel)
                if not refined:
                    # 验收拒(便宜模型偶发照抄原文被拒)重试一次;仍拒才跳过,
                    # 该章保留确定性占位(句边界截断版),不算失败。
                    refined = refine_chapter(db, script_id, ch, user_id, rapi, rmodel)
                if not refined:
                    skipped += 1
                    continue
                if apply:
                    apply_refined(db, script_id, ch, refined)
                    if hasattr(db, "commit"):
                        db.commit()
                elif len(samples) < 3:
                    samples.append({"chapter": ch, **refined})
            done += 1
            if done % 20 == 0:
                log.info("[facts_refine] script=%s 进度 %d 章(至 ch%d)", script_id, done, ch)
            if not apply and done >= 3:
                break  # dry-run 只打样
        except Exception as exc:
            failed += 1
            log.warning("[facts_refine] ch%s 失败跳过: %s", ch, exc)
    return {"ok": True, "refined": done, "skipped": skipped, "failed": failed,
            "range": [ch_from, ch_to], "samples": samples, "applied": bool(apply),
            "cancelled": cancelled}


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("script_id", type=int)
    ap.add_argument("--from", dest="ch_from", type=int, default=1)
    ap.add_argument("--to", dest="ch_to", type=int, default=None)
    ap.add_argument("--user-id", type=int, default=1)
    ap.add_argument("--api-id", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    out = refine_script(a.script_id, a.user_id, ch_from=a.ch_from, ch_to=a.ch_to,
                        api_id=a.api_id, model=a.model, apply=a.apply)
    print(json.dumps(out, ensure_ascii=False, indent=2))
