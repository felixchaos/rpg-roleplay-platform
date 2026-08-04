"""command_tools_script_write §提取/委派族(拆包 2026-07-14,纯机械搬家零行为变化)。

extract_from_selection:对选中正文跑结构化提取(只产提议不写库,调一次提取 LLM/BYOK)。
delegate_writing_task:派用户自己配置的(BYOK)子模型做写作任务(绝不平台兜底)。
"""
from __future__ import annotations

import json
from typing import Any

from ._helpers import _resolve_sid, _user_can_read_script


def _t_extract_from_selection(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """对用户选中的一段正文跑结构化提取(复用 extract/per_chapter.extract_chapter 的提取器,含其
    反史实/反编造/中文别名归并铁律),返回提议的人物/势力/地点/概念/事件/摘要 —— 供 agent 按用户意愿
    用 upsert_canon_entity / update_npc_card / upsert_worldbook_entry / create_anchor 落库(经写入权限闸)。
    本工具只产提议、不写库;会调一次提取 LLM(BYOK)。这是「把提取器拆成选区工具」的核心(作者优先)。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    text = str(args.get("text") or "").strip()
    if not text:
        return "失败: text 必填(要提取信息的选中正文)"
    text = text[:8000]
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _user_can_read_script(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户或未订阅"
            known = [r["name"] for r in (db.execute(
                "select name from kb_canon_entities where script_id=%s and coalesce(name,'')<>'' "
                "order by importance desc, id asc limit 200", (sid,)).fetchall() or [])]
        from agents._harness import resolve_api_and_model
        api_id, model_real = resolve_api_and_model(
            user_id, api_pref_key="extractor.api_id", model_pref_key="extractor.model_real_name")
        if not api_id or not model_real:
            return "失败: 未找到可用的提取模型,请到「设置 → 模块模型」配置 extractor(或编辑器/GM)模型后重试。"
        from extract.llm import ExtractLLM
        from extract.per_chapter import extract_chapter
        llm = ExtractLLM(model=str(model_real), api_id=str(api_id), user_id=user_id,
                         script_id=sid, algorithm="editor_selection")
        ex = extract_chapter(llm, 0, text, era="", known_entities=known)
        if not getattr(ex, "raw_ok", False):
            return "提取失败:模型未返回有效结构,可换更强的提取模型或缩短选区后重试。"
        proposal = {
            "summary": getattr(ex, "chapter_summary", ""),
            "entities": getattr(ex, "entities", []),       # type=character/faction/location/...,含 full_name/aliases/identity/background/subtype/parent
            "concepts": getattr(ex, "concepts", []),
            "events": getattr(ex, "events", []),
            "relationships": getattr(ex, "relationships", []),
        }
        body = json.dumps(proposal, ensure_ascii=False, indent=2)[:6000]
        return ("【从选中段提取到的提议(尚未写库)】先一句话向用户说清要建/改哪些,再落库(写入受三级权限闸):"
                "entities 里 type=character → upsert_canon_entity 或 generate_character_card_draft 后建 NPC 卡;"
                "faction/location/concept → upsert_canon_entity 或 upsert_worldbook_entry;events → create_anchor。\n"
                + body)
    except Exception as exc:
        try:
            from agents.provider_errors import classify_provider_error
            k = classify_provider_error(exc)
            if k:
                return f"提取失败:{k[1]}"
        except Exception:
            pass
        return f"提取失败:{type(exc).__name__}: {str(exc)[:120]}"


def _t_delegate_writing_task(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    task = str(args.get("task") or "").strip()
    if not task:
        return "失败: task 必填(要委派子模型做的写作任务,如『以冷峻文风写第5章开头300字』)"
    api_id_in = str(args.get("api_id") or "").strip()
    model_in = str(args.get("model") or args.get("model_real_name") or "").strip()
    # 1) 解析模型:显式优先 → 用户 writer/gm 偏好。
    api_id = model = ""
    try:
        from agents._harness import resolve_api_and_model
        api_id, model = resolve_api_and_model(
            user_id, api_pref_key="writer.api_id", model_pref_key="writer.model_real_name",
            api_id_override=(api_id_in or None), model_override=(model_in or None),
        )
    except Exception:
        api_id = model = ""
    if not (api_id and model):
        try:
            from core.llm_backend import first_user_model
            fu = first_user_model(user_id)
            if fu:
                api_id, model = fu
        except Exception:
            pass
    if not (api_id and model):
        return ("委派失败: 没找到可用模型。本工具只用【你自己配置的模型】(不走平台兜底);"
                "请到「设置 → API 与模型」配置并测试一个你自己的模型后重试。")
    # 2) 强制 BYOK:用户必须持有该 provider 的 key —— 不走 env/平台兜底(用户铁律)。
    try:
        from platform_app.user_credentials import resolve_api_key
        if not resolve_api_key(user_id, api_id, env_fallback="").get("key"):
            return (f"委派失败: 模型 {api_id}/{model} 没有你自己的 API Key。本工具只用你自己配置的模型,"
                    "请去「设置 → API 与模型」配置该 provider 的 key,或改用一个已配置的模型。")
    except Exception as exc:  # noqa: BLE001
        return f"委派失败(凭据校验出错): {type(exc).__name__}: {exc}"
    # 3) 构造后端 + 纯文本生成;任何失败都明确回报(让主 agent 转述/换模型重试)。
    try:
        from agents.gm import GameMaster
        backend = GameMaster(api_id=str(api_id), model=str(model), user_id=user_id)._backend
    except Exception as exc:  # noqa: BLE001
        from agents.provider_errors import classify_provider_error
        known = classify_provider_error(exc)
        return f"委派失败(后端初始化 {api_id}/{model}): {known[1] if known else f'{type(exc).__name__}: {exc}'}"
    try:
        max_tokens = min(6000, max(400, int(args.get("max_tokens") or 2500)))
    except (TypeError, ValueError):
        max_tokens = 2500
    ctx = str(args.get("context") or "")[:8000]
    sys_p = ("你是中文小说写作助手。严格按用户要求直接产出【成稿正文/内容】,"
             "不要解释、不要加前后缀说明、不要复述任务。")
    user_p = (f"【参考上下文】\n{ctx}\n\n" if ctx else "") + f"【写作任务】\n{task}"
    try:
        parts: list[str] = []
        for chunk in backend.stream(sys_p, [{"role": "user", "content": user_p}], max_tokens=max_tokens):
            if chunk:
                parts.append(chunk)
        out = "".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        from agents.provider_errors import classify_provider_error
        known = classify_provider_error(exc)
        return (f"委派失败(模型 {api_id}/{model} 调用出错): "
                f"{known[1] if known else f'{type(exc).__name__}: {exc}'}。可换一个你已配置的模型重试。")
    if not out:
        return (f"委派失败: 模型 {api_id}/{model} 返回空内容(可能是推理模型 max_tokens 不够、"
                "或中转站拒绝)。可调大 max_tokens、换模型或重试。")
    return (f"[子模型 {api_id}/{model} 产出 · 仅供参考,需你确认后再落库]\n{out}")


