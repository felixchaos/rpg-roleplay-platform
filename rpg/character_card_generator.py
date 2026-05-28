"""
character_card_generator.py — task 87 / 49: 控制台助手的创意工具实现。

两个 public 函数:
  · generate_character_card_draft(brief, user_id, ...) → dict
  · refine_character_card_draft(previous_draft, feedback, user_id, ...) → dict

这两个工具被 ToolDispatcher 暴露给 console_assistant origin (侧栏助手),
不允许 GM (llm_chat) 自创角色——LLM 在叙事中提到一个新 NPC 时该走 GM 的
gm_provisional active_entity 路径,而不是创建持久卡片。

实现按 phase 87 设计的 5 层 validator 管线:

  Layer 1 — 现实切片快照
      从 DB / state 拉:
        · 当前剧本 phase (state.world.timeline.current_phase / 调用方传入)
        · 当前剧本已有 NPC 名单 (character_cards.name) — 用于查重
        · 当前 phase 已有 NPC 卡 (随机 3-5 张) — 作 LLM 风格 reference
        · worldbook 核心条目 (worldbook_entries) — 可选
        · ruleset.id — 是 5e_compatible 时 prompt LLM 多填数值字段
      任一数据拉不到 → 软降级,validations 里标 "skipped: 缺数据"。

  Layer 2 — 强 schema (Anthropic native tool_use)
      工具名 emit_card,tool_choice 强制 emit_card。
      input_schema 见 _CARD_SCHEMA — 与 prompt 中的 schema 描述同步。
      非 Anthropic backend (Vertex/OpenAI) 走 JSON mode,prompt 显式贴 schema。

  Layer 3 — 5 个 validator
      3a  姓名查重: name 不在 character_cards(本剧本) + user_character_cards(本用户)
      3b  phase 一致: phase_availability 含目标 phase (若有 phase 数据)
      3c  跨 phase token 黑名单: 全文不出现其他 phase 的专有 token
              (token 来源: worldbook key + 一个 baseline 关键词表)
      3d  Critic LLM 评分: 用同 backend 拿 reference + 候选卡评 0-1,< 0.6 拒
      3e  schema 字段完整 (兜底,SDK 已校验过)

  Layer 4 — Reject + Retry
      任一 validator 不过 → 把 violations 列表喂回 generator prompt
      最多 retry 2 次 (共 3 次生成)
      3 次全失败 → ok=False,draft 仍返回 (最后一次的)

  Layer 5 — 返回不写 DB
      助手层后续若用户确认,会经 dispatcher 调 create_character_card
      落地;这里只产出 candidate。

返回格式:
  {
    "ok": bool,
    "draft": dict | None,          # 最终 / 最后一次 draft
    "validations": list[dict],     # 每次 retry 的全部 validator 结果
    "retries": int,                # 实际重试次数 (0..2)
    "diagnostics": dict,           # phase / backend / 取舍记录
  }

依赖:
  · 不引入新外部依赖 (anthropic SDK 与 gm.py 已用)
  · backend 通过 gm._select_backend(user_id) 复用 (无 user → env fallback)
  · DB 通过 platform_app.db.connect (现有)
"""
from __future__ import annotations

import json
import re
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)

# ════════════════════════════════════════════════════════════════════
# Schema
# ════════════════════════════════════════════════════════════════════


# 兼容跨 backend 的 OpenAPI-ish schema。Anthropic 直接用,
# Vertex/OpenAI JSON mode 则把它转成 prompt 提示。
_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "角色名,≤30 字符,不与现有 NPC 重名"},
        "gender": {"type": "string", "description": "性别 / 自我认同"},
        "age": {"type": "string", "description": "年龄,允许 '约 200 岁' 等非数字"},
        "appearance": {"type": "string", "description": "一段外貌描写"},
        "personality": {"type": "string", "description": "性格关键词 + 简述"},
        "background": {"type": "string", "description": "起源故事 / 来历"},
        "motivation": {"type": "string", "description": "当前目标 / 驱动力"},
        "speaking_style": {
            "type": "string",
            "description": "说话风格说明 + 2-3 段实际对话样本 (用 / 分隔)",
        },
        "abilities": {
            "type": "array", "items": {"type": "string"},
            "description": "技能 / 能力清单",
        },
        "relationship_hints": {
            "type": "object",
            "description": "{NPC 名: 关系描述} key 应来自现有名单,可空",
            "additionalProperties": {"type": "string"},
        },
        "phase_availability": {
            "type": "array", "items": {"type": "string"},
            "description": "该卡适用的 phase 列表 (剧本宏观阶段标签)",
        },
        "consistency_check_self": {
            "type": "string",
            "description": "LLM 自我说明:为何符合目标 phase 与世界观",
        },
        # 5E 模组才填的可选字段
        "race": {"type": "string", "description": "(5E) 种族,非模组留空"},
        "class": {"type": "string", "description": "(5E) 职业"},
        "level": {"type": "integer", "minimum": 0, "maximum": 20,
                  "description": "(5E) 等级,非模组填 0"},
        "ability_scores": {
            "type": "object",
            "description": "(5E) STR/DEX/CON/INT/WIS/CHA 6 个属性整数",
            "additionalProperties": {"type": "integer"},
        },
    },
    "required": [
        "name", "gender", "age", "appearance", "personality",
        "background", "motivation", "speaking_style",
        "abilities", "phase_availability", "consistency_check_self",
    ],
}


_REQUIRED_FIELDS: tuple[str, ...] = tuple(_CARD_SCHEMA["required"])


# ════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════


def generate_character_card_draft(
    *,
    brief: str,
    user_id: int,
    script_id: int | None = None,
    kind: str = "user",
    phase: str | None = None,
    timeout_sec: int = 30,
) -> dict[str, Any]:
    """从简短描述生成符合规范的角色卡 candidate (不写 DB)。

    Args:
        brief: 用户输入的简短描述,如 "20 岁女法师,流亡贵族,傲娇但善良"
        user_id: 调用方 user_id (DB 鉴权 + critic backend 选择)
        script_id: 目标剧本 id (用于查重 / phase / 风格 reference)
        kind: "user" 或 "script" (写哪张表的语义,本函数不写 DB)
        phase: 目标 phase 标签,None → 用 state / DB 推断
        timeout_sec: 单次 LLM 调用超时 (目前 SDK 不直接支持,保留接口)

    Returns:
        {ok, draft, validations, retries, diagnostics}
    """
    brief = (brief or "").strip()
    if not brief:
        return {
            "ok": False, "draft": None,
            "validations": [{"layer": "input", "ok": False, "reason": "brief 为空"}],
            "retries": 0, "diagnostics": {},
        }
    slice_ = _layer1_reality_slice(user_id=user_id, script_id=script_id, target_phase=phase)
    return _generate_with_retry(
        brief=brief, user_id=user_id, slice_=slice_,
        previous_draft=None, feedback=None, kind=kind, timeout_sec=timeout_sec,
    )


def refine_character_card_draft(
    *,
    previous_draft: dict[str, Any],
    feedback: str,
    user_id: int,
    script_id: int | None = None,
    timeout_sec: int = 30,
) -> dict[str, Any]:
    """用 previous_draft + 用户反馈重新生成,走同一管线。"""
    feedback = (feedback or "").strip()
    if not isinstance(previous_draft, dict) or not previous_draft:
        return {
            "ok": False, "draft": None,
            "validations": [{"layer": "input", "ok": False,
                             "reason": "previous_draft 缺失"}],
            "retries": 0, "diagnostics": {},
        }
    if not feedback:
        return {
            "ok": False, "draft": previous_draft,
            "validations": [{"layer": "input", "ok": False,
                             "reason": "feedback 为空"}],
            "retries": 0, "diagnostics": {},
        }
    # 推断目标 phase 优先用 previous_draft.phase_availability[0]
    phase = None
    pa = previous_draft.get("phase_availability") or []
    if isinstance(pa, list) and pa:
        phase = str(pa[0])
    slice_ = _layer1_reality_slice(user_id=user_id, script_id=script_id, target_phase=phase)
    # brief 用 previous_draft 的核心字段重建,避免 LLM 漂移太远
    brief = _rebuild_brief_from_draft(previous_draft)
    return _generate_with_retry(
        brief=brief, user_id=user_id, slice_=slice_,
        previous_draft=previous_draft, feedback=feedback, kind="user",
        timeout_sec=timeout_sec,
    )


# ════════════════════════════════════════════════════════════════════
# Layer 1 — 现实切片
# ════════════════════════════════════════════════════════════════════


def _layer1_reality_slice(
    *, user_id: int, script_id: int | None, target_phase: str | None,
) -> dict[str, Any]:
    """收集所有 validator / prompt 需要的现实数据,失败软降级。"""
    slice_: dict[str, Any] = {
        "target_phase": target_phase or "",
        "script_id": script_id,
        "existing_npc_names": [],     # 本剧本所有 NPC
        "user_card_names": [],         # 本用户所有 user_character_cards
        "phase_reference_cards": [],   # 本 phase 的 3-5 张样卡
        "worldbook_keys": [],          # 本剧本 worldbook 关键词
        "other_phase_tokens": [],      # 其他 phase 的专有 token (黑名单)
        "ruleset_id": "",
        "warnings": [],                # validations 里会塞进 skipped 项
    }
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            # 剧本 NPC 名单
            if script_id is not None:
                rows = db.execute(
                    "select name, metadata, personality, current_status "
                    "from character_cards where script_id = %s limit 200",
                    (script_id,),
                ).fetchall() or []
                slice_["existing_npc_names"] = [r["name"] for r in rows if r.get("name")]
                # phase reference: metadata.phase_availability 或 current_status 含 phase 字样
                if target_phase:
                    refs: list[dict[str, Any]] = []
                    for r in rows:
                        meta = r.get("metadata") or {}
                        if isinstance(meta, str):
                            try:
                                meta = json.loads(meta)
                            except Exception:
                                meta = {}
                        if not isinstance(meta, dict):
                            meta = {}
                        phases = meta.get("phase_availability") or []
                        if (isinstance(phases, list) and target_phase in phases) or \
                                (target_phase in (r.get("current_status") or "")):
                            refs.append({
                                "name": r.get("name"),
                                "personality": (r.get("personality") or "")[:200],
                                "current_status": (r.get("current_status") or "")[:200],
                            })
                        if len(refs) >= 5:
                            break
                    slice_["phase_reference_cards"] = refs[:5]
                # worldbook
                wb_rows = db.execute(
                    "select title, keys from worldbook_entries "
                    "where script_id = %s limit 100",
                    (script_id,),
                ).fetchall() or []
                keys: list[str] = []
                for r in wb_rows:
                    ks = r.get("keys") or []
                    if isinstance(ks, str):
                        try:
                            ks = json.loads(ks)
                        except Exception:
                            ks = []
                    if isinstance(ks, list):
                        for k in ks:
                            if isinstance(k, str) and len(k) <= 40:
                                keys.append(k)
                    title = r.get("title")
                    if isinstance(title, str) and len(title) <= 40:
                        keys.append(title)
                slice_["worldbook_keys"] = list({k for k in keys if k.strip()})[:80]
            else:
                slice_["warnings"].append("script_id 缺失,跳过剧本 NPC / worldbook 查询")

            # user 自创卡名单 (查重补充)
            user_rows = db.execute(
                "select name from user_character_cards where user_id = %s limit 200",
                (user_id,),
            ).fetchall() or []
            slice_["user_card_names"] = [r["name"] for r in user_rows if r.get("name")]
    except Exception as exc:
        slice_["warnings"].append(f"DB 切片失败: {type(exc).__name__}: {exc}")

    # baseline 跨 phase token (手工短表,后续可拓展为按 phase 配置)
    # 思路: 月球篇里出现"柏林"算泄漏,反之亦然;管控关键词来自 worldbook + 内置
    slice_["other_phase_tokens"] = _derive_other_phase_tokens(slice_)
    # ruleset 通过 user_id 路径暂取不到,留空;调用方可在 args 里覆盖
    return slice_


_BASELINE_PHASE_TOKENS: dict[str, list[str]] = {
    # task 80: 通用底座 — 不再硬编码特定剧本的 phase 互斥词。
    # 后续可改成: 从 worldbook_entries (priority>=80) 抽取每条 entry 的 title 作为
    # phase 候选, entry 之间作为互斥关系 — 这样任何剧本导入后都能动态拿到 phase 词表。
}


def _derive_other_phase_tokens(slice_: dict[str, Any]) -> list[str]:
    target = (slice_.get("target_phase") or "").strip()
    if not target:
        return []
    out: list[str] = []
    for phase_key, toks in _BASELINE_PHASE_TOKENS.items():
        if phase_key not in target:
            out.extend(toks)
        else:
            # 同 phase 的 token 反而是 "允许出现的",从黑名单移除
            for t in toks:
                if t in out:
                    out.remove(t)
    # worldbook 里如果某 key 明确属于其他 phase,也可补充;此版只用 baseline 表
    return sorted({t for t in out if t})


# ════════════════════════════════════════════════════════════════════
# Layer 2 — 生成 (Anthropic native tool_use 优先)
# ════════════════════════════════════════════════════════════════════


def _build_system_prompt(slice_: dict[str, Any]) -> str:
    target_phase = slice_.get("target_phase") or "(未指定)"
    existing = ", ".join(slice_.get("existing_npc_names") or [])[:600]
    refs = slice_.get("phase_reference_cards") or []
    ref_lines = []
    for r in refs:
        ref_lines.append(
            f"  · {r.get('name')}: 性格={r.get('personality')} / 状态={r.get('current_status')}"
        )
    refs_block = "\n".join(ref_lines) if ref_lines else "  (本 phase 暂无现成 NPC 可参考)"
    worldbook_keys = ", ".join(slice_.get("worldbook_keys") or [])[:400]
    blacklist = ", ".join(slice_.get("other_phase_tokens") or [])
    ruleset = slice_.get("ruleset_id") or ""
    is_5e = ruleset == "dnd5e"

    parts = [
        "你是 RPG 平台的角色卡生成助手。把用户简短描述扩展为一张符合当前剧本规范的完整 NPC 卡。",
        "",
        f"【目标剧本 phase】{target_phase}",
        f"【已存在 NPC 姓名(不可重名)】{existing or '(无)'}",
        f"【本 phase 风格参考】\n{refs_block}",
        f"【世界书关键词(可在背景里引用)】{worldbook_keys or '(无)'}",
        f"【禁止出现的跨 phase token】{blacklist or '(无)'}",
    ]
    if is_5e:
        parts.append("【ruleset】5E compatible — 请填 race / class / level / ability_scores")
    else:
        parts.append("【ruleset】freeform — race/class/level/ability_scores 可留空")
    parts += [
        "",
        "硬约束:",
        "  · name 不得与 existing NPC 重名 (大小写不敏感)",
        "  · phase_availability 必须包含目标 phase",
        "  · 全文 (appearance/background/motivation/...) 不得出现『禁止出现的跨 phase token』",
        "  · 所有 required 字段必填,内容具体,不允许 'TBD' / '待定'",
        "  · speaking_style 至少给 2-3 段对话样本",
        "  · consistency_check_self 解释 1-2 句为何契合 phase 与世界观",
        "",
        "通过 emit_card 工具一次性输出完整卡片 JSON,不要写解释文字。",
    ]
    return "\n".join(parts)


def _build_user_message(
    *, brief: str, previous_draft: dict | None, feedback: str | None,
    last_violations: list[dict] | None,
) -> str:
    lines = [f"用户原始描述: {brief}"]
    if previous_draft:
        lines.append("")
        lines.append("上一版草稿:")
        lines.append(json.dumps(previous_draft, ensure_ascii=False, indent=2)[:1500])
    if feedback:
        lines.append("")
        lines.append(f"用户反馈: {feedback}")
    if last_violations:
        lines.append("")
        lines.append("上一次生成被 validator 拒绝,违规项:")
        for v in last_violations:
            lines.append(f"  · [{v.get('layer')}] {v.get('reason')}")
        lines.append("请在本次生成中修正以上违规。")
    return "\n".join(lines)


def _call_llm_for_card(
    *, user_id: int, system: str, user_msg: str, timeout_sec: int,
) -> tuple[dict | None, str]:
    """调 LLM 生成卡片。返回 (draft|None, backend_kind)。"""
    backend = _select_backend(user_id)
    backend_kind = type(backend).__name__
    # Anthropic 走 native tool_use
    if backend_kind == "_AnthropicBackend":
        return _anthropic_emit_card(backend, system, user_msg), backend_kind
    # 其他 backend 走 call_structured (JSON mode)
    try:
        schema_hint = json.dumps(_CARD_SCHEMA, ensure_ascii=False, indent=2)[:2000]
        full_sys = (
            system
            + "\n\n你必须只返回符合以下 JSON Schema 的 JSON 对象,不要包含 Markdown 围栏或解释:\n"
            + schema_hint
        )
        text = backend.call_structured(
            full_sys, [{"role": "user", "content": user_msg}],
            max_tokens=2000,
        )
        return _parse_json_safely(text), backend_kind
    except Exception as exc:
        return None, f"{backend_kind}:error:{type(exc).__name__}:{exc}"


def _anthropic_emit_card(backend, system: str, user_msg: str) -> dict | None:
    """Anthropic native tool_use 路径。"""
    tool = {
        "name": "emit_card",
        "description": "输出生成的角色卡。",
        "input_schema": _CARD_SCHEMA,
    }
    try:
        resp = backend.client.messages.create(
            model=backend.model_name,
            max_tokens=2000,
            temperature=0.7,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_card"},
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "emit_card":
                inp = block.input or {}
                if isinstance(inp, dict):
                    return inp
        return None
    except Exception:
        return None


def _select_backend(user_id: int | None, role: str = "generator"):
    """复用 gm.py 已有 backend 选择逻辑。

    task 56:
      尊重 user_preferences:
        · role="generator" 读 character_card_generator.api_id /
          character_card_generator.model_real_name
        · role="critic"    读 critic.api_id / critic.model_real_name
      未配置时回退到 Anthropic → Vertex 老路径。
    """
    api_id = _resolve_preferred_api(user_id, role)
    model = _resolve_preferred_model(user_id, role)
    if api_id and model:
        try:
            from agents.gm import GameMaster
            return GameMaster(api_id=api_id, model=model, user_id=user_id)._backend
        except Exception as exc:
            log.warning(f"[card-gen] 偏好 backend 构建失败 ({api_id}/{model}: {exc})，回退默认")
    # 默认: 直接构造 Anthropic backend, 失败则降级到 Vertex
    try:
        from agents.gm import _AnthropicBackend
        return _AnthropicBackend(user_id=user_id)
    except Exception:
        pass
    try:
        from agents.gm import _VertexBackend
        return _VertexBackend()
    except Exception as exc:
        raise RuntimeError(f"无可用 LLM backend: {exc}") from exc


def _resolve_preferred_model(user_id: int | None, role: str) -> str | None:
    """role = 'generator' → character_card_generator.model_real_name;
    role = 'critic'   → critic.model_real_name。"""
    if not user_id:
        return None
    key = "critic.model_real_name" if role == "critic" else "character_card_generator.model_real_name"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (int(user_id),),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            return row["preferences"].get(key) or None
    except Exception:
        return None
    return None


def _resolve_preferred_api(user_id: int | None, role: str) -> str | None:
    if not user_id:
        return None
    key = "critic.api_id" if role == "critic" else "character_card_generator.api_id"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (int(user_id),),
            ).fetchone()
        if row and isinstance(row.get("preferences"), dict):
            return row["preferences"].get(key) or None
    except Exception:
        return None
    return None


def _parse_json_safely(text: str) -> dict | None:
    if not text:
        return None
    text = text.strip()
    # 去掉可能的 markdown 围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except Exception:
        # 尝试抠出第一个 {...}
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
        return None


# ════════════════════════════════════════════════════════════════════
# Layer 3 — Validators
# ════════════════════════════════════════════════════════════════════


def _run_validators(
    *, draft: dict | None, slice_: dict[str, Any], user_id: int,
) -> list[dict[str, Any]]:
    """跑 5 个 validator,返回每个的结果 [{layer, ok, reason}]"""
    results: list[dict[str, Any]] = []
    if not isinstance(draft, dict):
        return [{"layer": "schema", "ok": False, "reason": "draft 不是 dict"}]

    # 3a 姓名查重
    results.append(_v_name_uniqueness(draft, slice_))
    # 3b phase 一致
    results.append(_v_phase_consistency(draft, slice_))
    # 3c 跨 phase token 黑名单
    results.append(_v_cross_phase_tokens(draft, slice_))
    # 3d Critic LLM 评分
    results.append(_v_critic_score(draft, slice_, user_id))
    # 3e schema 字段完整
    results.append(_v_schema_completeness(draft))
    return results


def _v_name_uniqueness(draft: dict, slice_: dict) -> dict:
    name = (draft.get("name") or "").strip()
    if not name:
        return {"layer": "name_uniqueness", "ok": False, "reason": "name 为空"}
    lower = name.lower()
    pool: set[str] = set()
    for n in (slice_.get("existing_npc_names") or []):
        if isinstance(n, str):
            pool.add(n.lower())
    for n in (slice_.get("user_card_names") or []):
        if isinstance(n, str):
            pool.add(n.lower())
    if lower in pool:
        return {
            "layer": "name_uniqueness", "ok": False,
            "reason": f"名字与已有角色重名: {name!r}",
        }
    return {"layer": "name_uniqueness", "ok": True, "reason": "唯一"}


def _v_phase_consistency(draft: dict, slice_: dict) -> dict:
    target_phase = (slice_.get("target_phase") or "").strip()
    if not target_phase:
        return {"layer": "phase_consistency", "ok": True,
                "reason": "skipped: 缺数据 (无目标 phase)"}
    avail = draft.get("phase_availability") or []
    if not isinstance(avail, list):
        return {"layer": "phase_consistency", "ok": False,
                "reason": "phase_availability 不是列表"}
    if target_phase in avail:
        return {"layer": "phase_consistency", "ok": True,
                "reason": f"包含目标 phase: {target_phase}"}
    # 宽松匹配: 包含子串也算
    for p in avail:
        if isinstance(p, str) and target_phase in p:
            return {"layer": "phase_consistency", "ok": True,
                    "reason": f"宽松匹配: {p}"}
    return {"layer": "phase_consistency", "ok": False,
            "reason": f"phase_availability {avail} 不含目标 phase {target_phase!r}"}


def _v_cross_phase_tokens(draft: dict, slice_: dict) -> dict:
    blacklist = slice_.get("other_phase_tokens") or []
    if not blacklist:
        return {"layer": "cross_phase_tokens", "ok": True,
                "reason": "skipped: 缺数据 (无黑名单)"}
    # 把 draft 的所有字符串字段拼起来扫
    text_fields = [
        str(draft.get("appearance") or ""),
        str(draft.get("personality") or ""),
        str(draft.get("background") or ""),
        str(draft.get("motivation") or ""),
        str(draft.get("speaking_style") or ""),
        " ".join(str(a) for a in (draft.get("abilities") or []) if isinstance(a, str)),
    ]
    blob = " ".join(text_fields)
    hits = [tok for tok in blacklist if tok and tok in blob]
    if hits:
        return {"layer": "cross_phase_tokens", "ok": False,
                "reason": f"包含其他 phase 专有 token: {hits}"}
    return {"layer": "cross_phase_tokens", "ok": True, "reason": "无泄漏"}


def _v_critic_score(draft: dict, slice_: dict, user_id: int) -> dict:
    """用便宜 LLM 调用做一致性评分。失败 → 软通过。"""
    refs = slice_.get("phase_reference_cards") or []
    target_phase = slice_.get("target_phase") or ""
    if not target_phase and not refs:
        return {"layer": "critic_score", "ok": True,
                "reason": "skipped: 缺数据 (无 phase 或 reference)"}
    try:
        backend = _select_backend(user_id, role="critic")
        sys = (
            "你是 RPG 角色卡一致性评审。给候选卡和 phase reference 打分 0.0-1.0,"
            "<0.6 视为不通过。只返回 JSON {\"score\": float, \"reason\": str},不写解释。"
        )
        payload = {
            "target_phase": target_phase,
            "reference_cards": refs,
            "candidate": draft,
        }
        text = backend.call_structured(
            sys, [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)[:6000]}],
            max_tokens=300,
        )
        parsed = _parse_json_safely(text) or {}
        score = float(parsed.get("score") or 0.0)
        reason = parsed.get("reason") or ""
        if score < 0.6:
            return {"layer": "critic_score", "ok": False,
                    "reason": f"critic 评 {score:.2f} < 0.6: {reason}", "score": score}
        return {"layer": "critic_score", "ok": True,
                "reason": f"critic 评 {score:.2f} ≥ 0.6", "score": score}
    except Exception as exc:
        return {"layer": "critic_score", "ok": True,
                "reason": f"skipped: critic 调用失败 ({type(exc).__name__}: {exc})"}


def _v_schema_completeness(draft: dict) -> dict:
    missing = []
    for field in _REQUIRED_FIELDS:
        v = draft.get(field)
        if v is None:
            missing.append(field)
            continue
        if isinstance(v, str) and not v.strip():
            missing.append(field)
            continue
        if isinstance(v, list) and not v:
            missing.append(field)
            continue
    if missing:
        return {"layer": "schema_completeness", "ok": False,
                "reason": f"缺必填字段: {missing}"}
    return {"layer": "schema_completeness", "ok": True, "reason": "字段齐全"}


# ════════════════════════════════════════════════════════════════════
# Layer 4 — Retry orchestration
# ════════════════════════════════════════════════════════════════════


MAX_RETRIES = 2  # 总共最多 3 次生成


def _generate_with_retry(
    *, brief: str, user_id: int, slice_: dict[str, Any],
    previous_draft: dict | None, feedback: str | None,
    kind: str, timeout_sec: int,
) -> dict[str, Any]:
    system = _build_system_prompt(slice_)
    all_validations: list[list[dict]] = []
    last_draft: dict | None = previous_draft
    last_backend = ""
    last_violations: list[dict] = []
    for attempt in range(MAX_RETRIES + 1):
        user_msg = _build_user_message(
            brief=brief,
            previous_draft=previous_draft if attempt == 0 else last_draft,
            feedback=feedback if attempt == 0 else None,
            last_violations=last_violations if attempt > 0 else None,
        )
        draft, backend_kind = _call_llm_for_card(
            user_id=user_id, system=system, user_msg=user_msg, timeout_sec=timeout_sec,
        )
        last_backend = backend_kind
        if draft is None:
            attempt_result = [{"layer": "generate", "ok": False,
                               "reason": f"LLM 未返回 draft (backend={backend_kind})"}]
            all_validations.append(attempt_result)
            last_violations = attempt_result
            continue
        last_draft = draft
        results = _run_validators(draft=draft, slice_=slice_, user_id=user_id)
        all_validations.append(results)
        violations = [r for r in results if not r.get("ok")]
        if not violations:
            return {
                "ok": True,
                "draft": draft,
                "validations": _flatten_validations(all_validations),
                "retries": attempt,
                "diagnostics": {
                    "backend": backend_kind,
                    "target_phase": slice_.get("target_phase"),
                    "warnings": slice_.get("warnings", []),
                    "kind": kind,
                },
            }
        last_violations = violations
    return {
        "ok": False,
        "draft": last_draft,
        "validations": _flatten_validations(all_validations),
        "retries": MAX_RETRIES,
        "diagnostics": {
            "backend": last_backend,
            "target_phase": slice_.get("target_phase"),
            "warnings": slice_.get("warnings", []),
            "kind": kind,
            "final_violations": last_violations,
        },
    }


def _flatten_validations(per_attempt: list[list[dict]]) -> list[dict]:
    flat: list[dict] = []
    for i, results in enumerate(per_attempt):
        for r in results:
            flat.append({"attempt": i, **r})
    return flat


def _rebuild_brief_from_draft(draft: dict) -> str:
    """从已有 draft 抠核心字段重组 brief,供 refine 路径用。"""
    bits = []
    for f in ("age", "gender", "background", "personality", "motivation"):
        v = draft.get(f)
        if isinstance(v, str) and v.strip():
            bits.append(v.strip())
        if len(", ".join(bits)) > 120:
            break
    return ", ".join(bits) or (draft.get("name") or "")


__all__ = [
    "generate_character_card_draft",
    "refine_character_card_draft",
    "MAX_RETRIES",
]
