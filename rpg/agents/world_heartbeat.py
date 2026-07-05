"""agents/world_heartbeat.py — 世界心跳 v0(活世界·柱子1)。

设计文档: docs/design/world_heartbeat_v0.md

目标: 世界在玩家不注视的地方也在动 —— 每隔几回合确定性地产生 1-2 条「世界侧
小事」(村庄级/配角级/环境级),留存在 state.data["background_events"],由
context provider(rpg/context_providers/world_pulse.py)择机以传闻/路人交谈的
方式浮出。

与后果账本(柱子2, state/consequence_ledger.py)的分工: 后果=玩家种的因(有
明确到期), 心跳=世界自己的事(无因果绑定)。两者互不读写。

原则(与柱子2同一套纪律):
- 调度(should_tick)/去重/防剧透/上限/剪除 = 确定性代码; 只有事件文案由 LLM 写。
- flag `world_heartbeat` 默认关。
- 零新持久化机制: 直接改 state.data,由本回合 Phase 5 统一持久化。
- 全程 try/except: 任何失败静默跳过,绝不破回合(见 run_heartbeat_tick)。

状态结构 (state.data["background_events"]: list[dict]):
    {
      "id": "bg_a1b2c3",
      "text": "村东磨坊主的驴昨夜挣脱缰绳跑进了麦田,踩坏了半垄麦子",
      "created_turn": 12,
      "surfaced_turn": None,
    }

调度元信息 (state.data["heartbeat_meta"]: dict):
    {"last_tick_turn": int}
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Any

from core.logging import get_logger
from state.parsers import _clean_item

log = get_logger(__name__)

# ── 常量(设计文档 §1/§2)───────────────────────────────────────────

MIN_TURN_TO_START = 4      # 前 3 回合让故事立足,不产心跳
TICK_INTERVAL_TURNS = 3    # K=3 节流
MAX_UNSURFACED_BACKLOG = 8   # 未浮出条目 >= 8 就别再产(积压保护,should_tick 用)
MAX_UNSURFACED_STORED = 12   # 未浮出条目上限,超出拒收(register 用)
SURFACED_RETENTION_TURNS = 5  # 已浮出条目保留 5 回合后确定性剪除

MAX_ITEM_CHARS = 120        # 单条事件文案最大字数(验收器拒绝口径)
MAX_ITEMS_PER_TICK = 2      # 一次 tick 最多产出条数


# ── 指纹归一化(与 state/consequence_ledger._normalize_for_fp 同思路)──────

_FP_STRIP_RE = None  # 惰性编译


def _normalize_for_fp(text: str) -> str:
    """指纹归一化: 去掉全部标点/空白,只留文字数字。

    与 state.consequence_ledger._normalize_for_fp 同思路(同一份归一化规则会
    比精确匹配更抗措辞漂移),此处独立复制一份而非 import,避免心跳模块反向
    依赖后果账本模块(两个柱子互不读写,详见设计文档 §分工)。
    """
    global _FP_STRIP_RE
    if _FP_STRIP_RE is None:
        _FP_STRIP_RE = re.compile(r"[\W_]+", re.UNICODE)
    return _FP_STRIP_RE.sub("", text or "")


# ── 状态访问 helper ────────────────────────────────────────────────

def _events(state_data: dict) -> list[dict]:
    """取 / 建 state_data["background_events"] 列表引用(原地可变)。"""
    events = state_data.setdefault("background_events", [])
    if not isinstance(events, list):
        events = []
        state_data["background_events"] = events
    return events


def _meta(state_data: dict) -> dict:
    meta = state_data.setdefault("heartbeat_meta", {})
    if not isinstance(meta, dict):
        meta = {}
        state_data["heartbeat_meta"] = meta
    return meta


# ── 调度(纯函数)────────────────────────────────────────────────────

def should_tick(state_data: dict, user_id: int | None) -> bool:
    """本回合是否该产心跳。纯函数,不做任何写入。

    条件(全部满足才 True):
      1. flag `world_heartbeat` 开
      2. state.turn >= MIN_TURN_TO_START(前 3 回合让故事立足)
      3. turn - heartbeat_meta.last_tick_turn >= TICK_INTERVAL_TURNS(K=3 节流)
      4. 未浮出条目数 < MAX_UNSURFACED_BACKLOG(积压多就别再产)
    """
    try:
        from core.feature_flags import feature_enabled
        if not feature_enabled("world_heartbeat", user_id):
            return False
    except Exception:
        return False

    turn = int(state_data.get("turn", 0) or 0)
    if turn < MIN_TURN_TO_START:
        return False

    meta = state_data.get("heartbeat_meta") or {}
    last_tick_turn = int(meta.get("last_tick_turn", -(10 ** 9)) or -(10 ** 9))
    if turn - last_tick_turn < TICK_INTERVAL_TURNS:
        return False

    events = state_data.get("background_events") or []
    if not isinstance(events, list):
        events = []
    unsurfaced = sum(1 for e in events if isinstance(e, dict) and not e.get("surfaced_turn"))
    if unsurfaced >= MAX_UNSURFACED_BACKLOG:
        return False

    return True


# ── 验收器(确定性,代码不信 LLM)───────────────────────────────────────

def _validate_items(
    raw_items: Any,
    *,
    state_data: dict,
    player_name: str = "",
) -> list[str]:
    """逐条校验 LLM 产出的候选事件文案,返回通过的文本列表(去重、防剧透)。

    拒绝规则(设计文档 §2):
      - 非字符串 / 空串
      - 超过 MAX_ITEM_CHARS 字
      - 含「你」或玩家名(防止直接提到玩家本人)
      - 与现存 background_events 指纹重复(归一化后)
    全拒则返回空列表(正常,不重试)。
    """
    if not isinstance(raw_items, list):
        return []

    existing = _events(state_data) if isinstance(state_data, dict) else []
    seen_fp = {
        _normalize_for_fp(e.get("text", ""))
        for e in existing
        if isinstance(e, dict)
    }
    player_name = _clean_item(player_name or "")

    out: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, str):
            continue
        text = _clean_item(raw)
        if not text:
            continue
        if len(text) > MAX_ITEM_CHARS:
            continue
        if "你" in text:
            continue
        if player_name and player_name in text:
            continue
        fp = _normalize_for_fp(text)
        if not fp or fp in seen_fp:
            continue
        seen_fp.add(fp)
        out.append(text)
        if len(out) >= MAX_ITEMS_PER_TICK:
            break
    return out


# ── 输入材料构造(防剧透: 只喂已揭示信息)──────────────────────────────

def _build_materials(state_data: dict, pending_anchors: list[dict] | None) -> dict:
    """收集喂给 LLM 的材料,全部是已揭示/揭示窗口内的(设计文档 §2)。

    不喂原著 RAG、不喂未到锚点的正文;可选喂 pending_anchors 的 summary(≤2 条)。
    """
    world = state_data.get("world") or {}
    player = state_data.get("player") or {}
    memory = state_data.get("memory") or {}
    relationships = state_data.get("relationships") or {}
    active_entities = state_data.get("active_entities") or []
    existing_events = _events(state_data)

    facts = memory.get("facts") or []
    if not isinstance(facts, list):
        facts = []

    entities_brief = []
    if isinstance(active_entities, list):
        for e in active_entities[:8]:
            if not isinstance(e, dict):
                continue
            entities_brief.append({
                "name": str(e.get("name", "")),
                "disposition": str(e.get("disposition", "unknown")),
            })

    recent_bg = [
        str(e.get("text", ""))
        for e in existing_events[-3:]
        if isinstance(e, dict)
    ]

    anchors_brief = []
    if pending_anchors:
        for a in pending_anchors[:2]:
            if isinstance(a, dict) and a.get("summary"):
                anchors_brief.append(str(a.get("summary", ""))[:200])

    return {
        "time": str(world.get("time", "")),
        "current_location": str(player.get("current_location", "")),
        "current_phase": str((world.get("timeline") or {}).get("current_phase", "")),
        "facts_recent": [str(f) for f in facts[-10:]],
        "relationship_names": list(relationships.keys()) if isinstance(relationships, dict) else [],
        "active_entities": entities_brief,
        "recent_background_events": recent_bg,
        "pending_anchor_hints": anchors_brief,
    }


def _build_prompts(materials: dict) -> tuple[str, str]:
    """构造心跳 tick 的 system + user prompt(设计文档 §2 prompt 要点)。"""
    system_prompt = (
        "你是一个桌面角色扮演游戏的『世界脉动』写手。你的任务是想象玩家**不在场处**"
        "正在发生的极小事件(村庄级/配角级/环境级的日常琐事),让世界显得在自行运转。\n"
        "硬性规则:\n"
        "1. 每条不超过80字;\n"
        "2. 禁止提到玩家本人(不能出现『你』或玩家名);\n"
        "3. 禁止重大剧情转折、禁止死亡/战争/灾变级事件(那是主线的事);\n"
        "4. 内容必须与下面给出的已知事实、地点、人物基调保持一致,不得杜撰未出现的重要人物；\n"
        "5. 只输出 1-2 条,严格输出一个 JSON 字符串数组,不要任何其它文字/解释/markdown围栏。\n"
        '示例输出: ["村东磨坊主的驴昨夜挣脱缰绳跑进了麦田,踩坏了半垄麦子", "镇上的铁匠铺新到了一批矿石"]'
    )
    user_prompt = (
        "当前世界快照:\n"
        f"- 时间: {materials.get('time') or '（未知）'}\n"
        f"- 地点: {materials.get('current_location') or '（未知）'}\n"
        f"- 阶段: {materials.get('current_phase') or '（未知）'}\n"
        f"- 最近事实: {json.dumps(materials.get('facts_recent') or [], ensure_ascii=False)}\n"
        f"- 已知关系人名: {json.dumps(materials.get('relationship_names') or [], ensure_ascii=False)}\n"
        f"- 在场角色基调: {json.dumps(materials.get('active_entities') or [], ensure_ascii=False)}\n"
        f"- 最近已产出的世界事件(避免重复方向): "
        f"{json.dumps(materials.get('recent_background_events') or [], ensure_ascii=False)}\n"
        f"- 世界正在酝酿什么(方向暗示,非确定事实): "
        f"{json.dumps(materials.get('pending_anchor_hints') or [], ensure_ascii=False)}\n\n"
        "请写玩家不在场处正在发生的 1-2 件小事,与已知事实一致、与在场剧情无直接因果。"
        "严格输出 JSON 数组。"
    )
    return system_prompt, user_prompt


# ── LLM 调用 + 落地(唯一带副作用的入口)───────────────────────────────

def run_heartbeat_tick(
    state: Any,
    user_id: int | None = None,
    *,
    api_id_override: str | None = None,
    model_override: str | None = None,
    pending_anchors: list[dict] | None = None,
    timeout_sec: int = 15,
) -> list[str]:
    """跑一次心跳 tick: 一次 LLM 调用产出 1-2 条世界侧事件,通过确定性验收后
    append 进 state.data["background_events"] + 更新 heartbeat_meta.last_tick_turn。

    直接改 state.data(本回合 Phase 5 统一持久化,与 extractor 同命运)。
    返回本次实际写入的事件文本列表(空列表 = 本 tick 空手而归,正常情况)。

    pending_anchors: 可选,调用方按需透传(设计文档 §2「世界正在酝酿什么」方向
    暗示,≤2 条 summary)。v0 接线点(chat_pipeline._worker_heartbeat)未额外做
    DB 查询取它,默认 None 不影响主流程 —— 只是 prompt 材料的锦上添花项。

    全程 try/except: 任何失败静默跳过(log.debug),绝不破回合。
    """
    try:
        state_data = getattr(state, "data", None)
        if not isinstance(state_data, dict):
            return []

        # 模型解析: 复用史官的解析函数,严格 BYOK,同用户级偏好。
        from agents.recorder import _resolve_recorder_api_and_model
        try:
            api_id, model = _resolve_recorder_api_and_model(user_id, api_id_override, model_override)
        except Exception as exc:
            log.info("[world_heartbeat] 模型解析失败,跳过: %s", exc)
            return []
        if not api_id or not model:
            log.info("[world_heartbeat] 无可用 api_id/model,跳过")
            return []

        materials = _build_materials(state_data, pending_anchors)
        system_prompt, user_prompt = _build_prompts(materials)

        from agents._harness import call_agent_json
        try:
            text, usage = call_agent_json(
                api_id=api_id,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                user_id=user_id,
                tool_schema=None,  # 心跳只要简单 JSON 数组,不用 tool schema
                max_tokens=400,
                timeout_sec=timeout_sec,
                agent_kind="world_heartbeat",
            )
        except Exception as exc:
            log.info("[world_heartbeat] LLM 调用失败,跳过: %s", exc)
            return []

        from core.json_parse import parse_llm_json
        raw_items = parse_llm_json(text, want=list)
        if raw_items is None:
            log.info("[world_heartbeat] 输出不是合法 JSON 数组,跳过(raw前120字: %r)", (text or "")[:120])
            return []

        turn = int(state_data.get("turn", 0) or 0)
        # 更新节流锚点:不论本次验收是否有条目通过,只要 tick 真的跑过(拿到了 LLM
        # 输出并尝试解析)就更新 —— 避免全拒时下一回合立刻又被 should_tick 放行、
        # 短时间内重复调用 LLM(设计文档 §2:"全拒则本 tick 空手而归,正常,不重试")。
        _meta(state_data)["last_tick_turn"] = turn

        player_name = str((state_data.get("player") or {}).get("name", "") or "")
        accepted = _validate_items(raw_items, state_data=state_data, player_name=player_name)
        log.info("[world_heartbeat] tick@turn=%s raw=%d accepted=%d", turn, len(raw_items), len(accepted))
        if not accepted:
            return []

        events = _events(state_data)
        written: list[str] = []
        for text_item in accepted:
            # 未浮出上限:超出拒收(设计文档 §1),不影响已浮出的条目计数。
            unsurfaced_count = sum(
                1 for e in events if isinstance(e, dict) and not e.get("surfaced_turn")
            )
            if unsurfaced_count >= MAX_UNSURFACED_STORED:
                break
            entry = {
                "id": f"bg_{secrets.token_urlsafe(6)}",
                "text": text_item,
                "created_turn": turn,
                "surfaced_turn": None,
            }
            events.append(entry)
            written.append(text_item)

        if written:
            log.info("[world_heartbeat] tick@turn=%s 写入 %d 条世界事件", turn, len(written))
        return written
    except Exception as exc:
        log.warning("[world_heartbeat] tick 异常,跳过(不破回合): %s", exc, exc_info=True)
        return []


__all__ = [
    "MIN_TURN_TO_START",
    "TICK_INTERVAL_TURNS",
    "MAX_UNSURFACED_BACKLOG",
    "MAX_UNSURFACED_STORED",
    "SURFACED_RETENTION_TURNS",
    "should_tick",
    "run_heartbeat_tick",
    "_validate_items",
    "_normalize_for_fp",
]
