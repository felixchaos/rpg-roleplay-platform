"""gm_serving/anchor_reconcile.py — 每回合确定性「世界线锚点」兜底判定器。

动机
====
世界线收束(task 136)此前只有两条路径标记原著锚点已发生:
  1. GM 自觉调 mark_anchor_satisfied 工具(靠提示词自律,会漏)
  2. 玩家手动点 UI 按钮

两条都不保证「每回合」都核对一遍 pending 锚点。结果:剧情已经明确演到某个
原著锚点(例如某人物登场 / 某事件发生),但 GM 这一轮恰好没调工具 → 锚点永远
卡在 pending,下一轮上下文又把它当「还没发生」注入 → GM 反复重演同一桥段 /
进度窗口冻结。

本模块加第三条【确定性·每回合都跑】的兜底:回合 GM 正文流完后,系统主动拿本回合
正文 + 当前进度窗口内的 pending 锚点,做一次**严格保守**的廉价判定,只把本回合
剧情【明确到达】的锚点确定性落库(复用 command_tools_anchors 的 UPDATE 写逻辑 +
gm_serving.settings.advance_progress)。原两条路径全部保留。

铁律
====
- 保守:误推 = 跳过原著内容,比不推更糟。判定器宁漏勿误,低置信不标。
- 成本门控(BYOK 付费):
    · 仅当「进度窗口内有 pending 锚点」才跑(否则零 LLM 调用直接 return)
    · 用最廉价模型(复用 phase_digest / agent 通配偏好);解析不到模型 / 无 key
      → 静默 return,绝不报错不破回合
    · env RPG_ANCHOR_AUTO_RECONCILE 默认 '1' 可关
- 防剧透:只判定/推进【当前进度窗口内】的锚点,绝不跳到远未来锚点。
- 不破回合:整函数 try/except 包裹,任何失败只 log.warning 后吞掉。
- 确定性落库:命中后复用既有写逻辑,在 (user,save) scope lock + connect() 内,
  不另造写路径。

公开 API
========
    reconcile_anchors_for_turn(save_id, user_id, turn_text, *, db=None,
                               _judge=None) -> int
        返回本回合确定性标记的锚点数(供调用方 log / 派发刷新事件)。
        _judge / db 仅供离线单测注入(默认 None = 走真实路径)。
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from agents.anchor_seed_agent import get_progress_window, list_pending_for_phase
from core.json_parse import parse_llm_json
from core.logging import get_logger

log = get_logger(__name__)

# 进度窗口内单回合最多核对的 pending 锚点数(控 prompt 体积 + 成本)。
_MAX_PENDING_PER_TURN = 12
# 单回合最多确定性标记的锚点数(保守,防判定器一次性吞掉一大段原著)。
_MAX_MARK_PER_TURN = 4
# GM 正文截断长度(判定器只需要本回合发生了什么)。
_TURN_TEXT_CAP = 6000


def _enabled() -> bool:
    """env RPG_ANCHOR_AUTO_RECONCILE 默认 '1';设 '0'/'false' 关。"""
    return os.environ.get("RPG_ANCHOR_AUTO_RECONCILE", "1").strip().lower() not in (
        "0", "false", "no", "off", "",
    )


_SYSTEM_PROMPT = """\
你是一个【世界线锚点到达判定器】。你的唯一职责:读本回合 GM 写的剧情正文,判断
其中是否**明确叙述到了**某些「待发生的原著锚点事件」。

【最高铁律 — 极度保守,宁漏勿误】
1. 只有当本回合正文【明确、确凿地叙述了】某锚点事件**实际发生 / 实际到达**时,
   才把它列出来。仅仅提到、暗示、铺垫、即将发生、有人计划、做梦、回忆、假设、
   讨论某事件 —— 都【不算】到达,绝不列出。
2. 拿不准就【不列】。漏标的代价(下回合再核对一次)远小于误标(直接跳过原著内容)。
3. 你只能从给定的 pending 锚点列表里选,绝不发明新锚点、绝不改 anchor_key。
4. 只看本回合正文这一段材料,不要脑补正文之外的剧情。

【drift_score(偏离度,0.0-1.0)】
  · 0.0  = 完全按原著方式发生
  · 0.3  = 核心保留,具体过程/场景与原著不同(变体)
  · 0.7+ = 核心结果保留但发生方式大改
保守起见,拿不准 drift 时给 0.2。

【输出格式(严格)】
仅输出一个 JSON 数组(list),直接以 `[` 开头、以 `]` 结尾。不要 markdown 围栏,
不要任何解释文字。每个元素:
  {"anchor_key": "<必须来自给定列表>", "drift_score": <0.0-1.0 数字>}
本回合没有任何锚点明确到达 → 输出空数组 []。
"""


def _build_user_prompt(turn_text: str, pending: list[dict[str, Any]]) -> str:
    lines = ["【待发生的原著锚点(只能从这里选)】"]
    for a in pending:
        key = a.get("anchor_key") or ""
        summ = (a.get("summary") or "").strip().replace("\n", " ")
        if len(summ) > 240:
            summ = summ[:240]
        fatal = "[死神来了·必发生]" if a.get("is_fatal") else ""
        lines.append(f"- anchor_key={key} {fatal} 概要:{summ}")
    lines.append("")
    lines.append("【本回合 GM 剧情正文】")
    lines.append(turn_text.strip())
    lines.append("")
    lines.append(
        "请判断上面正文里【明确到达 / 实际发生】了哪些锚点。极度保守,宁漏勿误,"
        "只输出 JSON 数组。"
    )
    return "\n".join(lines)


def _default_judge(
    user_id: int | None, turn_text: str, pending: list[dict[str, Any]],
    *, save_id: int | None = None,
) -> list[dict[str, Any]]:
    """默认判定器:廉价模型一次聚焦判定。

    解析不到模型 / 无 key / 任何 LLM 错误 → 返回 [](静默跳过,绝不抛)。
    """
    try:
        from agents._harness import call_agent_json, resolve_api_and_model
    except Exception as exc:  # pragma: no cover - import 兜底
        log.warning("[anchor_reconcile] harness import 失败,跳过判定: %s", exc)
        return []

    # 成本门控②:复用 agent 通配廉价模型偏好;解析不到 / 无可用 BYOK → 静默跳过。
    try:
        api_id, model = resolve_api_and_model(
            user_id,
            api_pref_key="anchor_reconcile.api_id",
            model_pref_key="anchor_reconcile.model_real_name",
        )
    except Exception as exc:
        log.info("[anchor_reconcile] 无可用廉价模型(静默跳过): %s", exc)
        return []
    if not api_id or not model:
        return []

    try:
        text, _usage = call_agent_json(
            api_id=api_id,
            model=model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(turn_text, pending),
            user_id=user_id,
            tool_schema=None,  # 文本 JSON 数组即可,保持最廉价路径
            max_tokens=400,
            timeout_sec=20,
            agent_kind="anchor_reconcile",
            save_id=save_id,
        )
    except Exception as exc:
        # 无 key / 网络 / 凭证错误等一律静默跳过,绝不破回合。
        log.info("[anchor_reconcile] 判定调用失败(静默跳过): %s", exc)
        return []

    parsed = parse_llm_json(text or "", want=list)
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        key = (item.get("anchor_key") or "").strip()
        if not key:
            continue
        try:
            drift = float(item.get("drift_score"))
        except (TypeError, ValueError):
            drift = 0.2
        drift = max(0.0, min(1.0, drift))
        out.append({"anchor_key": key, "drift_score": drift})
    return out


def reconcile_anchors_for_turn(
    save_id: int | None,
    user_id: int | None,
    turn_text: str | None,
    *,
    db: Any = None,
    _judge: Callable[..., list[dict[str, Any]]] | None = None,
) -> int:
    """每回合确定性兜底:把本回合明确到达的 pending 锚点确定性标记 occurred/variant。

    返回标记的锚点数。任何异常被吞掉返回 0(绝不破回合)。

    参数:
      save_id / user_id : 当前存档与用户
      turn_text         : 本回合 GM 正文
      db                : 可选,复用调用方已有连接(否则内部 connect())
      _judge            : 可选,注入判定器(离线测试用)。签名
                          (user_id, turn_text, pending, *, save_id) -> list[{anchor_key, drift_score}]
    """
    try:
        return _reconcile_impl(save_id, user_id, turn_text, db=db, _judge=_judge)
    except Exception as exc:  # 不破回合:任何失败 log.warning 后吞掉
        log.warning("[anchor_reconcile] reconcile 整体失败(已吞,不影响回合): %s", exc)
        return 0


def _reconcile_impl(
    save_id: int | None,
    user_id: int | None,
    turn_text: str | None,
    *,
    db: Any,
    _judge: Callable[..., list[dict[str, Any]]] | None,
) -> int:
    # 1. env 门控
    if not _enabled():
        return 0
    if not save_id or not user_id:
        return 0
    save_id = int(save_id)
    user_id = int(user_id)
    text = (turn_text or "").strip()
    if not text:
        return 0
    if len(text) > _TURN_TEXT_CAP:
        text = text[:_TURN_TEXT_CAP]

    # 2. 进度窗口 + 窗口内 pending 锚点(零调用门控:为空直接 return)
    win = get_progress_window(save_id)
    ch_min = win.get("chapter_min")
    ch_max = win.get("chapter_max")
    pending = list_pending_for_phase(
        save_id, None,
        limit=_MAX_PENDING_PER_TURN,
        chapter_min=ch_min, chapter_max=ch_max,
        order_by_chapter=True,
    )
    if not pending:
        return 0  # 成本门控①:窗口内无 pending → 零 LLM 调用

    # 窗口内 pending 的 anchor_key → source_chapter,后续校验命中合法性 + 推进进度。
    win_by_key: dict[str, dict[str, Any]] = {}
    for a in pending:
        k = a.get("anchor_key")
        if k:
            win_by_key[k] = a

    # 3. 廉价判定(解析不到模型 / 无 key → judge 内部静默返 [])
    judge = _judge or _default_judge
    hits = judge(user_id, text, pending, save_id=save_id) or []
    if not hits:
        return 0

    # 只保留窗口内、合法 anchor_key 的命中(防判定器越界到远未来/编造 key)。去重。
    seen: set[str] = set()
    valid_hits: list[dict[str, Any]] = []
    for h in hits:
        key = (h.get("anchor_key") or "").strip()
        if not key or key in seen or key not in win_by_key:
            continue
        seen.add(key)
        try:
            drift = float(h.get("drift_score"))
        except (TypeError, ValueError):
            drift = 0.2
        valid_hits.append({"anchor_key": key, "drift_score": max(0.0, min(1.0, drift))})
        if len(valid_hits) >= _MAX_MARK_PER_TURN:
            break  # 保守:单回合最多标 N 个
    if not valid_hits:
        return 0

    # 4. 确定性落库:复用既有写逻辑,在 (user,save) scope lock + 单连接内。
    if db is not None:
        return _apply_hits(db, save_id, user_id, valid_hits)

    from platform_app.db import connect, init_db
    from tools_dsl.command_dispatcher import _get_sync_scope_lock
    init_db()
    with _get_sync_scope_lock((user_id, save_id)), connect() as conn:
        return _apply_hits(conn, save_id, user_id, valid_hits)


def _apply_hits(
    db: Any, save_id: int, user_id: int, hits: list[dict[str, Any]],
) -> int:
    """对每个命中锚点:仅处理仍 pending 的,复用 command_tools_anchors 的 UPDATE
    (status occurred/variant 按 drift)+ advance_progress(max-only)。

    已被 GM 本轮自调工具标过 occurred/variant 的天然不在 pending,不会重复处理。
    """
    marked = 0
    for h in hits:
        key = h["anchor_key"]
        drift = h["drift_score"]
        # status 阈值与 mark_anchor_satisfied 完全一致(drift>=0.15 → variant)。
        new_status = "variant" if drift >= 0.15 else "occurred"
        # 默认 occurred_turn 从 branch_commits 最大值取(与 mark_anchor_satisfied 一致)。
        r = db.execute(
            "select coalesce(max(turn_index), 0) as t from branch_commits where save_id = %s",
            (save_id,),
        ).fetchone()
        occurred_turn = int((r or {}).get("t") or 0)
        # 只 UPDATE 仍 pending 的(WHERE status='pending' 幂等 + 防覆盖 GM 已标的)。
        row = db.execute(
            """
            update save_anchor_states set
              status = %s,
              variant_description = %s,
              occurred_at_turn = %s,
              drift_score = %s,
              updated_at = now()
            where save_id = %s and anchor_key = %s and status = 'pending'
            returning id, source_chapter
            """,
            (
                new_status,
                "系统每回合确定性兜底判定:本回合剧情明确到达此锚点",
                occurred_turn, drift, save_id, key,
            ),
        ).fetchone()
        if not row:
            continue  # 已非 pending(GM 本轮自调过 / 并发已标)→ 跳过
        marked += 1
        # 推进玩家进度(max-only,只增不减,幂等)。复用既有 advance_progress。
        src_ch = row.get("source_chapter")
        if isinstance(src_ch, int) and src_ch >= 1:
            try:
                from gm_serving.settings import advance_progress
                advance_progress(db, save_id, src_ch)
            except Exception as adv_exc:  # 进度同步失败不阻断锚点标记
                log.warning("[anchor_reconcile] advance_progress 失败(忽略): %s", adv_exc)
    return marked


__all__ = ["reconcile_anchors_for_turn"]
