"""platform_app.knowledge.card_audit — 按需 AI 复核 NPC 角色卡的人名/语义错误。

用户在剧本 → NPC 角色卡 点「AI 复核」并在弹出的公用模型选择器里选模型(默认其常用模型、可改)→
用所选模型对全部 NPC 卡做【一次】批量裁决:
  ① merges:把同一人的多张卡(本名/小名/敬称,如金玉/玉儿/小玉)合并成一张;
  ② protagonist:选出真主角并锁定(复用 _db_set_protagonist 的 protagonist_locked,重新提取不再覆盖);
  ③ non_persons:删除非人名卡(官职/泛称/地名,如将军/单于/众人/无忧宫)。

确定性应用裁决、保守(LLM 不确定就不动、ID 必须真实存在),返回变更摘要。**按需触发、不进导入流水线
→ 对导入零自动成本**;模型由用户当场选(写入 card_audit.* 偏好,后端兜底也读它)。
"""
from __future__ import annotations

import json
import re
from typing import Any

from psycopg.types.json import Jsonb

from platform_app.db import connect, init_db
from platform_app.knowledge._character_cards_repo import _db_set_protagonist
from platform_app.knowledge._utils import _require_script_owner

_AUDIT_SYSTEM = (
    "你是中文小说角色卡审计员。下面给你一部小说自动提取出的 NPC 角色卡(可能有错)。"
    "**只依据卡面信息**审核,拿不准就不动(宁可漏判不可错判)。**只输出一个 JSON 对象**,不要解释。"
)


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if x]
    if isinstance(v, str) and v.strip():
        return [s.strip() for s in v.split(",") if s.strip()]
    return []


def _parse_json_obj(text: str) -> dict:
    if not text:
        return {}
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {}
    except Exception:
        pass
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            v = json.loads(m.group(0))
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


def _roster(cards: list[dict]) -> str:
    lines = []
    for c in cards:
        al = _as_list(c.get("aliases"))
        al_s = "、".join(al[:6]) if al else "-"
        idt = (c.get("identity") or "").strip().replace("\n", " ")[:150] or "-"
        lines.append(
            f"[id={c['id']}] {c['name']} | 别名:{al_s} | 身份:{idt} | 出场频次:{int(c.get('importance') or 0)}"
        )
    return "\n".join(lines)


def _build_user_prompt(title: str, cards: list[dict]) -> str:
    return (
        f"小说标题:《{title or '未知'}》\n\n"
        f"请审核下列 {len(cards)} 张 NPC 角色卡,找出三类错误并给出修正:\n"
        "1) merges:哪些卡其实是【同一个人】的不同称呼(本名/小名/昵称/敬称,如『金玉/玉儿/小玉』、"
        "『红姑/红姑娘』),应合并成一张。每组:keep=保留哪张卡的 id,merge_ids=被并入的 id 列表。"
        "不同人即使共享一字也【绝不可】合并。\n"
        "2) protagonist_id:这部小说的【主角】是哪张卡的 id(叙事中心、贯穿全书、读者代入的那个人;"
        "常是身份/简介里写明『主角/主人公/穿越者/重生者』或第一视角的那张,**不一定是出场频次最高的**——"
        "反派/势力名往往频次更高但不是主角);全部看完再判,拿不准填 null。\n"
        "3) non_person_ids:哪些卡其实【不是具体人物】(官职/头衔/泛称/地名,如『将军/单于/众人/无忧宫』),应删除。\n\n"
        "【角色卡】\n" + _roster(cards) + "\n\n"
        "只输出这个 JSON(id 必须用上面给的数字 id,不要编造):\n"
        '{"merges":[{"keep":1,"merge_ids":[2,3]}],"protagonist_id":1,"non_person_ids":[4,5],"confidence":0.0}'
    )


def _resolve_audit_model(user_id: int, api_id: str, model: str) -> tuple[str, str]:
    """解析本次复核用的模型 —— 复用程序既有的模型解析设计,**不硬编码任何模型**:
      1. 前端当场传入的 (api_id, model)(用户在公用选择器里选的);
      2. card_audit.* 偏好(用户在选择器里改过会写这);
      3. 用户的【默认模型】—— first_user_model 已内含 gm.* 偏好 + BYOK(= 设置里设的默认模型)。
    三步都拿不到 → 返回空,由调用方的凭证预检转 credentials_required 引导用户去配,绝不回落到
    某个写死的便宜档。
    """
    from core.llm_backend import (
        first_user_model,
        resolve_preferred_api,
        resolve_preferred_model,
    )
    api_id = (api_id or "").strip()
    model = (model or "").strip()
    if not (api_id and model):
        api_id = api_id or (resolve_preferred_api(user_id, "card_audit.api_id") or "")
        model = model or (resolve_preferred_model(user_id, "card_audit.model_real_name") or "")
    if not (api_id and model):
        fu = first_user_model(user_id)  # gm.* 偏好优先 + 仅 BYOK 命中 = 用户的默认模型
        if fu:
            api_id = api_id or fu[0]
            model = model or fu[1]
    from model_aliases import normalize_api_id
    return normalize_api_id(api_id) if api_id else "", model


def audit_character_cards(user_id: int, script_id: int, api_id: str = "", model: str = "",
                          *, max_cards: int = 600) -> dict[str, Any]:
    """对某剧本全部 NPC 卡做一次 AI 复核裁决并应用。**仅 owner**。返回变更摘要。

    凭证缺失 → 抛 MissingUserCredentialError(端点转 credentials_required)。
    """
    init_db()
    from platform_app import import_pipeline
    api_id, model = _resolve_audit_model(user_id, api_id, model)

    with connect() as db:
        _require_script_owner(db, user_id, script_id)
        title_row = db.execute("select title from scripts where id=%s", (script_id,)).fetchone()
        title = ((title_row.get("title") if title_row else "") or "") if title_row else ""
        rows = db.execute(
            "select id, name, full_name, aliases, identity, importance, metadata "
            "from character_cards where script_id=%s and card_type='npc' "
            "order by importance desc, id asc limit %s",
            (script_id, int(max_cards)),
        ).fetchall()
    cards = [dict(r) for r in rows]
    if len(cards) < 2:
        return {"summary": {"merged": [], "protagonist": None, "dropped": []},
                "message": "NPC 卡不足 2 张,无需复核", "cards_reviewed": len(cards),
                "model": f"{api_id}/{model}"}

    # 凭证预检(用户当场选的 provider)。强鉴权下缺 key → 抛错让前端引导去配。
    from core.config import require_auth as _require_auth
    from platform_app.user_credentials import resolve_api_key
    if _require_auth():
        cred = resolve_api_key(user_id, api_id, env_fallback="")
        if not cred.get("key"):
            raise import_pipeline.MissingUserCredentialError(
                api_id=api_id, model=model,
                credential_api_id=import_pipeline._credential_api_id_for(api_id))

    from agents._harness import call_agent_json
    text, _usage = call_agent_json(
        api_id, model, _AUDIT_SYSTEM, _build_user_prompt(title, cards), user_id,
        max_tokens=1500, timeout_sec=60, agent_kind="card_audit",
        metadata_extra={"script_id": script_id, "cards": len(cards)},
    )
    verdict = _parse_json_obj(text)
    if not verdict:
        raise ValueError("AI 复核返回无法解析,请重试或换一个模型")

    cards_by_id = {int(c["id"]): c for c in cards}
    with connect() as db:
        _require_script_owner(db, user_id, script_id)
        summary = _apply_verdicts(db, script_id, cards_by_id, verdict)
    summary["cards_reviewed"] = len(cards)
    return {"summary": summary, "model": f"{api_id}/{model}"}


def schedule_card_audit(user_id: int, script_id: int, api_id: str = "", model: str = "") -> dict[str, Any]:
    """异步调度 NPC 卡 AI 复核(单次 LLM 裁决)→ 进 import_jobs,全局后台任务浮窗自动跟踪。返 {ok, job_id}。

    复核本身是【一次】LLM 调用(非逐卡循环),所以没有真实百分比 —— 浮窗按 spinner 显示(与生图一致),
    完成后从 import_jobs.budget_estimate.result 读回摘要。凭证预检在调度【前】同步做(缺 key 立即返
    credentials_required,不进队列空转);owner 校验同理。复用同剧本进行中的复核任务,避免重复触发。
    """
    init_db()
    import secrets
    import threading

    from platform_app import import_pipeline
    rapi, rmodel = _resolve_audit_model(user_id, api_id, model)

    with connect() as db:
        _require_script_owner(db, user_id, script_id)

    # 凭证预检(与同步版一致,放在 spawn 前):缺 key → 抛 MissingUserCredentialError,端点转 credentials_required。
    from core.config import require_auth as _require_auth
    from platform_app.user_credentials import resolve_api_key
    if _require_auth() and rapi:
        cred = resolve_api_key(user_id, rapi, env_fallback="")
        if not cred.get("key"):
            raise import_pipeline.MissingUserCredentialError(
                api_id=rapi, model=rmodel,
                credential_api_id=import_pipeline._credential_api_id_for(rapi))

    with connect() as db:
        existing = db.execute(
            "select job_id from import_jobs where user_id=%s and script_id=%s and kind=%s "
            "and status in ('pending','queued','running') order by id desc limit 1",
            (user_id, script_id, "cards_audit"),
        ).fetchone()
        if existing:
            return {"ok": True, "job_id": existing["job_id"], "reused": True}
        job_id = f"audit_{script_id}_{secrets.token_hex(6)}"
        db.execute(
            "insert into import_jobs(job_id, user_id, script_id, kind, status, stage, "
            "module, sub_kind, overall_total, budget_estimate, stages) "
            "values (%s,%s,%s,%s,'pending','pending',%s,%s,1,%s,%s)",
            (
                job_id, user_id, script_id, "cards_audit",
                "cards_audit", "cards_audit",
                Jsonb({"action": "AI 复核角色卡", "api_id": rapi, "model": rmodel}),
                Jsonb([{"id": "audit", "label": "AI 复核角色卡", "status": "pending"}]),
            ),
        )
    th = threading.Thread(
        target=_run_card_audit, args=(job_id, user_id, script_id, rapi, rmodel), daemon=True,
    )
    th.start()
    return {"ok": True, "job_id": job_id, "reused": False}


def _run_card_audit(job_id: str, user_id: int, script_id: int, api_id: str, model: str) -> None:
    """复核 worker:统一 import_jobs 状态机。done 时把摘要写进 budget_estimate.result(前端读回)。"""
    from platform_app.import_pipeline import JobController
    ctl = JobController(job_id)
    ctl.update(status="running", stage="audit", overall_progress=0, overall_total=1,
               stages=[{"id": "audit", "label": "AI 复核角色卡", "status": "running"}])
    with connect() as db:
        db.execute("update import_jobs set started_at=now() where job_id=%s", (job_id,))
    try:
        result = audit_character_cards(user_id, script_id, api_id, model)
        with connect() as db:
            # 合并进 budget_estimate(保留 insert 时写入的 api_id/model),供 get_job_status 暴露给前端。
            db.execute(
                "update import_jobs set budget_estimate = budget_estimate || %s where job_id=%s",
                (Jsonb({"result": result}), job_id),
            )
        ctl.update(status="done", stage="audit", overall_progress=1, overall_total=1,
                   stages=[{"id": "audit", "label": "AI 复核角色卡", "status": "done"}])
        with connect() as db:
            db.execute("update import_jobs set finished_at=now() where job_id=%s", (job_id,))
    except Exception as exc:  # noqa: BLE001
        ctl.update(status="failed", stage="audit", error=str(exc),
                   stages=[{"id": "audit", "label": "AI 复核角色卡", "status": "failed"}])
        with connect() as db:
            db.execute("update import_jobs set finished_at=now() where job_id=%s", (job_id,))


def _apply_verdicts(db, script_id: int, cards_by_id: dict, verdict: dict) -> dict:
    """确定性应用 LLM 裁决。保守:id 必须真实存在;被并/删的不再二次处理;主角被并走则用保留卡。"""
    out: dict[str, Any] = {"merged": [], "protagonist": None, "dropped": []}
    deleted: set[int] = set()
    merged_into: dict[int, int] = {}

    def _cid(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    # 1) 合并同一人
    for grp in (verdict.get("merges") or []):
        if not isinstance(grp, dict):
            continue
        keep = _cid(grp.get("keep"))
        if keep is None or keep not in cards_by_id or keep in deleted:
            continue
        merge_ids = [m for m in (_cid(x) for x in (grp.get("merge_ids") or []))
                     if m and m in cards_by_id and m != keep and m not in deleted]
        if not merge_ids:
            continue
        keep_card = cards_by_id[keep]
        new_aliases = set(_as_list(keep_card.get("aliases")))
        max_imp = int(keep_card.get("importance") or 0)
        for mid in merge_ids:
            mc = cards_by_id[mid]
            new_aliases.add(str(mc["name"]))
            new_aliases.update(_as_list(mc.get("aliases")))
            max_imp = max(max_imp, int(mc.get("importance") or 0))
            merged_into[mid] = keep
        new_aliases.discard(str(keep_card["name"]))
        db.execute(
            "update character_cards set aliases=%s, importance=%s, "
            "row_version=row_version+1, updated_at=now() "
            "where id=%s and script_id=%s and card_type='npc'",
            (Jsonb(sorted(new_aliases)), max_imp, keep, script_id),
        )
        for mid in merge_ids:
            db.execute("delete from character_cards where id=%s and script_id=%s and card_type='npc'",
                       (mid, script_id))
            deleted.add(mid)
        out["merged"].append({"keep": keep_card["name"],
                              "merged": [cards_by_id[m]["name"] for m in merge_ids]})

    # 2) 删除非人名
    for nid in (_cid(x) for x in (verdict.get("non_person_ids") or [])):
        if nid and nid in cards_by_id and nid not in deleted:
            db.execute("delete from character_cards where id=%s and script_id=%s and card_type='npc'",
                       (nid, script_id))
            deleted.add(nid)
            out["dropped"].append(cards_by_id[nid]["name"])

    # 3) 锁定主角(若主角卡被并走 → 用保留卡)
    pid = _cid(verdict.get("protagonist_id"))
    pid = merged_into.get(pid, pid)
    if pid and pid in cards_by_id and pid not in deleted:
        row = _db_set_protagonist(db, script_id, pid)
        if row:
            out["protagonist"] = cards_by_id[pid]["name"]
    return out
