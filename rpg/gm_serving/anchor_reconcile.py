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

import json
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

# ── Bug B(进度冻结)修复:有界叙事章估计 — 详见 docs/design/M_progress_advancement.md ──
# 事件抽取稀疏的章(本书 ch2-8 有摘要、0 event → 0 锚点)进度系统全盲,玩家走完整段
# 进度表死在第 1 章。本回合判定器【顺带】(常态:同一次 LLM 调用,零新增成本)读正文估「当前最
# 接近原著第几章」,再 clamp 到 [当前进度, max(已确认锚点,当前进度) + CAP] 推进进度 —— 既补平滑又防
# ch77 乱跳(floor=0 时上限 = prev+CAP,有界)。GM 自由 world.time 标签 resolve 不到锚点,故弃用标签
# 路径、改读真实剧情正文。
_LOOKAHEAD_CAP = 12

# Q 锚点限速(flag RPG_ANCHOR_PACE,默认关):治用户屡报的「锚点更新速度跟实际对不上、跳章」。
# 根因:canon 实体横跨多章(D20 在 ch1 和 ch11 各有锚点),宽候选窗口(±12)让玩家在 ch1 就把
# ch11 锚点判为到达 → floor 棘轮锁高 → 进度跳。修:① 收窄标记候选窗口(_MARK_WINDOW),远未来锚点
# 不进候选;② 每回合进度最多 +_PACE_CAP 章,匹配对话实际节奏。
_MARK_WINDOW = 4    # 标记候选只看 [ch_min, ch_min+4],别让远章锚点误判
_PACE_CAP = 2       # 每回合进度最多推进 2 章


def _anchor_pace(user_id: int | None = None) -> bool:
    """每用户特性 anchor_pace(默认开)。user_id 给定 → 按用户;否则仅环境默认。"""
    from core.feature_flags import feature_enabled
    return feature_enabled("anchor_pace", user_id)
# 喂给判定器估章的章节摘要截断(每章),控 prompt 体积。
_EST_SUMMARY_CAP = 160

# ── 确定性 intro 标记(harness 确定性,不靠保守 LLM)──────────────────────────────
# 「首次登场/首次引入/首次出现」型锚点 = 某实体首次现身。这是【确定性】信号:主体名/别名
# 出现在本回合正文 = 到达。保守 LLM 判定器对此屡漏(用户报「更新速度跟实际对不上」)→ 补一条
# 确定性路径,与 maintain_structured_kb 的 canon 名扫描同源同口径(名入正文=现身)。事件型锚点
# (非 intro)无法靠名字确定性判定,仍交给 LLM 判定器。
_INTRO_MARKERS = ("首次登场", "首次引入", "首次出现")


def _intro_subject(summary: str) -> str | None:
    """从 intro 锚点摘要提取主体名。覆盖三种 seed 格式:
      · concept「苍白之血」首次引入 / item「D20」首次引入  → 「」/『』 内
      · 爱丽丝(character)首次登场                        → NAME( 之前
      · 场景秋叶原避难所首次出现                          → 场景…首次 之间
    取冒号前的标题段再解析,失败返 None。"""
    import re
    s = (summary or "").split(":", 1)[0].split("：", 1)[0].strip()
    m = re.search(r"[「『]([^」』]+)[」』]", s)
    if m:
        return m.group(1).strip()
    m = re.match(r"^场景(.+?)首次", s)
    if m:
        return m.group(1).strip()
    m = re.match(r"^(.+?)[（(]", s)
    if m:
        return m.group(1).strip()
    m = re.match(r"^(.+?)首次", s)
    return m.group(1).strip() if m else None


def _deterministic_intro_hits(
    save_id: int, pending: list[dict[str, Any]], turn_text: str,
) -> list[dict[str, Any]]:
    """intro 型 pending 锚点里,主体名/别名出现在本回合正文的 → 确定性到达(drift=0.0=occurred)。

    只看传入的 pending(已被进度窗口约束),故不会越界标远未来锚点。canon 别名用于短名增广
    (如 D20 别名「战术辅助设备D20」)。无 script/无 intro 锚点 → 返 []。
    """
    intro_pend = [a for a in pending
                  if any(mk in (a.get("summary") or "") for mk in _INTRO_MARKERS)]
    if not intro_pend:
        return []
    # 人/概念/物 vs 场景【两套口径】:
    #   · 人/概念/物「首次引入」= 被命名即引入 → 正文名匹配(命名就是登场)。
    #   · 场景「首次出现」≠ 被提及:地点常被第三方提及/玩家拒绝而非实际进入。实测玩家拒绝去秋叶原避难所,
    #     GM 正文仍含「秋叶原」→ 名匹配会误标 → 过早退役未访问的强制场景。**改用权威信号:玩家当前所在地**
    #     (player.current_location,结构化态、GM 维护),且只用【全名】匹配(秋叶原避难所 ∈ "秋叶原避难所·入口
    #     通道" 命中;但 ∉ "秋叶原废墟·废土小径入口" → 拒绝/路过不误标)。短别名(秋叶原)会把废墟误配,故弃用。
    ent_pend = [a for a in intro_pend if not (a.get("summary") or "").lstrip().startswith("场景")]
    scene_pend = [a for a in intro_pend if (a.get("summary") or "").lstrip().startswith("场景")]
    from platform_app.db import connect, init_db
    init_db()
    alias_map: dict[str, list[str]] = {}
    cur_loc = ""
    with connect() as db:
        s = db.execute("select script_id from game_saves where id=%s", (save_id,)).fetchone()
        script_id = int(s["script_id"]) if (s and s.get("script_id") is not None) else None
        if script_id:
            for c in db.execute(
                "select name, aliases from kb_canon_entities where script_id=%s", (script_id,)
            ).fetchall():
                nm = (c.get("name") or "").strip()
                if nm:
                    alias_map[nm] = [a for a in (c.get("aliases") or []) if isinstance(a, str)]
        if scene_pend:
            r = db.execute(
                "select state_snapshot from runtime_checkouts where save_id=%s "
                "order by updated_at desc nulls last limit 1", (save_id,)
            ).fetchone()
            st = (r or {}).get("state_snapshot") or {}
            if isinstance(st, str):
                try:
                    st = json.loads(st)
                except (ValueError, TypeError):
                    st = {}
            cur_loc = str((st.get("player") or {}).get("current_location") or "")
    hits: list[dict[str, Any]] = []
    for a in ent_pend:
        subj = _intro_subject(a.get("summary") or "")
        if not subj or len(subj) < 2:
            continue
        names = [subj] + alias_map.get(subj, [])
        names = [n for n in names if isinstance(n, str) and len(n) >= 2]
        if any(n in turn_text for n in names):
            key = (a.get("anchor_key") or "").strip()
            if key:
                hits.append({"anchor_key": key, "drift_score": 0.0})
    for a in scene_pend:
        subj = _intro_subject(a.get("summary") or "")
        if not subj or len(subj) < 2 or not cur_loc:
            continue
        if subj in cur_loc:  # 全名 ∈ 当前所在地 = 确实进入了该场景
            key = (a.get("anchor_key") or "").strip()
            if key:
                hits.append({"anchor_key": key, "drift_score": 0.0})
    return hits


# ── 死亡/失效检测(确定性,保守)────────────────────────────────────────────────
# 玩家操作可能杀死/移除某角色 → 该角色【未来】只涉及他一人的锚点不再可能发生,应 superseded。
# is_fatal=true(死神来了)锚点【绝不】退役:那是命中注定要发生的死亡,世界会绕路实现。
# 因果细分(死亡【使 X 的行动锚点失效】vs【触发他人反应锚点】)无法确定性判断 → 只退役
# 【单人参与】锚点(participants==[死者]):整条锚点只关于死者自己 → 死了必不可能,无歧义。
# 多人参与锚点(可能是"为 X 复仇"等死亡的后果)留 pending,交 GM 工具/后续判断。宁漏勿误。
_DEATH_MARKERS = (
    "死亡", "死了", "死去", "身亡", "阵亡", "战死", "丧命", "毙命", "殒命", "气绝",
    "暴毙", "横死", "惨死", "已死", "殒身", "命丧", "毙于", "死于", "断气", "咽气",
)
_KILL_VERBS = ("杀死", "杀了", "击杀", "处决", "处刑", "斩杀", "格杀", "弄死", "了结了", "结果了")
_DEATH_NEGATION = (
    "没死", "未死", "没有死", "不会死", "死不了", "不死", "假死", "诈死", "装死",
    "差点", "差一点", "险些", "未遂", "没能", "未能", "无法", "幸免", "逃过", "躲过",
    "复活", "起死回生", "并未", "并没", "似乎", "以为", "好像", "是不是", "会不会",
)


def _detect_dead_in_prose(prose: str, char_aliases: dict[str, list[str]]) -> set[str]:
    """确定性、保守地从本回合正文检出【死亡】角色。规则:角色名【紧邻】强死亡词(名后 ≤6 字
    出现死亡词,或名前 ≤6 字出现击杀动词),且邻域内无否定/未遂/假死/疑问。宁漏勿误 —— 误判会
    错误退役有效锚点。char_aliases: {canonical: [name, *aliases]}(只含 character)。"""
    import re
    dead: set[str] = set()
    for canonical, names in char_aliases.items():
        found = False
        for nm in names:
            if not nm or len(nm) < 2 or nm not in prose:
                continue
            for m in re.finditer(re.escape(nm), prose):
                before = prose[max(0, m.start() - 6):m.start()]
                after = prose[m.end():m.end() + 6]
                window = before + nm + after
                if any(ng in window for ng in _DEATH_NEGATION):
                    continue  # 否定/未遂/假死/疑问 → 不算死
                if any(dk in after for dk in _DEATH_MARKERS) or any(kv in before for kv in _KILL_VERBS):
                    found = True
                    break
            if found:
                break
        if found:
            dead.add(canonical)
    return dead


def _invalidate_dead_entity_anchors(db: Any, save_id: int, prose: str) -> int:
    """检出本回合死亡角色 → 退役其【单人参与】的未来 pending 锚点(非 is_fatal)。返回退役数。
    任何异常吞掉返回 0(绝不破回合;调用方已在 try 内,但这里再保一层)。"""
    try:
        s = db.execute("select script_id from game_saves where id=%s", (save_id,)).fetchone()
        script_id = int(s["script_id"]) if (s and s.get("script_id") is not None) else None
        if not script_id:
            return 0
        rows = db.execute(
            "select name, aliases from kb_canon_entities where script_id=%s and type='character'",
            (script_id,),
        ).fetchall()
        char_aliases: dict[str, list[str]] = {}
        for c in rows:
            nm = (c.get("name") or "").strip()
            if nm:
                char_aliases[nm] = [nm] + [a for a in (c.get("aliases") or []) if isinstance(a, str)]
        if not char_aliases:
            return 0
        dead = _detect_dead_in_prose(prose, char_aliases)
        if not dead:
            return 0
        total = 0
        for name in dead:
            names = char_aliases.get(name, [name])
            res = db.execute(
                """
                update save_anchor_states set
                    status = 'superseded',
                    variant_description = %s,
                    drift_score = 1.0,
                    updated_at = now()
                where save_id = %s
                  and status = 'pending'
                  and is_fatal = false
                  and jsonb_array_length(coalesce(metadata->'participants', '[]'::jsonb)) = 1
                  and (metadata->'participants'->>0) = any(%s)
                returning id
                """,
                (f"角色「{name}」已死亡 —— 仅其单人参与的未来锚点不再可能发生(非死神来了)",
                 save_id, names),
            ).fetchall()
            if res:
                total += len(res)
                log.info("[anchor_reconcile] 死亡失效 save=%s 角色=%s 退役 %d 个单人未来锚点",
                         save_id, name, len(res))
        return total
    except Exception as exc:
        log.warning("[anchor_reconcile] 死亡失效检测失败(已吞): %s", exc)
        return 0


def _enabled() -> bool:
    """env RPG_ANCHOR_AUTO_RECONCILE 默认 '1';设 '0'/'false' 关。"""
    return os.environ.get("RPG_ANCHOR_AUTO_RECONCILE", "1").strip().lower() not in (
        "0", "false", "no", "off", "",
    )


def _estimate_enabled() -> bool:
    """env RPG_PROGRESS_NARRATIVE_ESTIMATE 默认 '1';设 '0'/'false' 关有界叙事章估计。"""
    return os.environ.get("RPG_PROGRESS_NARRATIVE_ESTIMATE", "1").strip().lower() not in (
        "0", "false", "no", "off", "",
    )


_SYSTEM_PROMPT = """\
你是一个【世界线锚点判定器】。读本回合 GM 写的剧情正文,完成两件事:
(A) 判断其中是否**明确叙述到了**某些「待发生的原著锚点事件」;
(B) 判断本回合剧情**最接近原著的第几章**(进度估计)。

【任务 A — 锚点到达判定·极度保守,宁漏勿误】
1. 只有当本回合正文【明确、确凿地叙述了】某锚点事件**实际发生 / 实际到达**时,
   才把它列出来。仅仅提到、暗示、铺垫、即将发生、有人计划、做梦、回忆、假设、
   讨论某事件 —— 都【不算】到达,绝不列出。
2. 拿不准就【不列】。漏标的代价(下回合再核对一次)远小于误标(直接跳过原著内容)。
3. 你只能从给定的 pending 锚点列表里选,绝不发明新锚点、绝不改 anchor_key。
4. 只看本回合正文这一段材料,不要脑补正文之外的剧情。
drift_score(偏离度 0.0-1.0):0.0=完全按原著;0.3=核心保留但过程/场景不同(变体);
0.7+=核心结果保留但发生方式大改。拿不准给 0.2。

【任务 B — 当前章估计·保守】
给你一份「原著章节地图」(章号 + 该章梗概)。判断本回合正文最接近哪一章,返回该章号。
1. 只能返回章节地图里列出的章号;正文明显还没到地图最早那章 → 返回 null。
2. 看正文实际演到哪里,不要被人物的回忆/预告/计划带偏。
3. 拿不准、正文太抽象无法定位 → 返回 null(漏估的代价远小于误估推快进度)。

【两任务独立】先独立完成任务 A(锚点到达,极度保守),再做任务 B(当前章估计)。
任务 B 的章号推理【绝不可】反过来改变任务 A 对锚点的取舍。

【输出格式(严格)】
仅输出一个 JSON 对象,以 `{` 开头、以 `}` 结尾。不要 markdown 围栏、不要解释:
  {"reached": [{"anchor_key":"<来自列表>","drift_score":<0.0-1.0>}], "current_chapter": <章号整数 或 null>, "progress_motion": <0|1|2>}
没有锚点到达 → reached 为 []。无法定位章节 → current_chapter 为 null。
progress_motion(必答,本回合叙事推进度):0=原地/回忆无推进,1=正常推进一拍,2=重大跨越。发散局脱离原著也要答,与 current_chapter 无关(供进度节奏兜底 pace fallback 用)。
"""


def _build_user_prompt(
    turn_text: str,
    pending: list[dict[str, Any]],
    window_chapters: list[dict[str, Any]] | None = None,
) -> str:
    lines = ["【待发生的原著锚点(任务 A,只能从这里选)】"]
    if pending:
        for a in pending:
            key = a.get("anchor_key") or ""
            summ = (a.get("summary") or "").strip().replace("\n", " ")
            if len(summ) > 240:
                summ = summ[:240]
            fatal = "[死神来了·必发生]" if a.get("is_fatal") else ""
            lines.append(f"- anchor_key={key} {fatal} 概要:{summ}")
    else:
        lines.append("(本窗口暂无待发生锚点)")
    if window_chapters:
        lines.append("")
        lines.append("【原著章节地图(任务 B,current_chapter 只能从这些章号里选或 null)】")
        for c in window_chapters:
            ch = c.get("chapter")
            label = (c.get("label") or "").strip().replace("\n", " ")
            summ = (c.get("summary") or "").strip().replace("\n", " ")
            if len(summ) > _EST_SUMMARY_CAP:
                summ = summ[:_EST_SUMMARY_CAP]
            head = f"第{ch}章" + (f"「{label}」" if label else "")
            lines.append(f"- chapter={ch} {head}:{summ}")
    lines.append("")
    lines.append("【本回合 GM 剧情正文】")
    lines.append(turn_text.strip())
    lines.append("")
    lines.append(
        "请完成任务 A(到达了哪些锚点)+ 任务 B(最接近第几章)。极度保守,宁漏勿误/宁漏勿快,"
        "只输出 JSON 对象。"
    )
    return "\n".join(lines)


def _default_judge(
    user_id: int | None, turn_text: str, pending: list[dict[str, Any]],
    *, save_id: int | None = None, window_chapters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """默认判定器:廉价模型一次聚焦判定(同一次调用顺带估当前章,零新增成本)。

    返回 {"reached": [{anchor_key, drift_score}], "estimated_chapter": int|None}。
    解析不到模型 / 无 key / 任何 LLM 错误 → 返回 {"reached": [], "estimated_chapter": None}
    (静默跳过,绝不抛)。
    """
    empty: dict[str, Any] = {"reached": [], "estimated_chapter": None}
    try:
        from agents._harness import call_agent_json, resolve_api_and_model
    except Exception as exc:  # pragma: no cover - import 兜底
        log.warning("[anchor_reconcile] harness import 失败,跳过判定: %s", exc)
        return empty

    # 成本门控②:复用 agent 通配廉价模型偏好;解析不到 / 无可用 BYOK → 静默跳过。
    try:
        api_id, model = resolve_api_and_model(
            user_id,
            api_pref_key="anchor_reconcile.api_id",
            model_pref_key="anchor_reconcile.model_real_name",
        )
    except Exception as exc:
        log.info("[anchor_reconcile] 无可用廉价模型(静默跳过): %s", exc)
        return empty
    if not api_id or not model:
        return empty

    try:
        text, _usage = call_agent_json(
            api_id=api_id,
            model=model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(turn_text, pending, window_chapters),
            user_id=user_id,
            tool_schema=None,  # 文本 JSON 即可,保持最廉价路径
            max_tokens=500,
            timeout_sec=20,
            agent_kind="anchor_reconcile",
            save_id=save_id,
        )
    except Exception as exc:
        # 无 key / 网络 / 凭证错误等一律静默跳过,绝不破回合。
        log.info("[anchor_reconcile] 判定调用失败(静默跳过): %s", exc)
        return empty

    # want=None 先原样拿到:廉价模型(haiku/flash)常忽略「对象 vs 数组」要求、退回裸数组
    # [{...}]。若用 want=dict,顶层 list 会被过滤成 None → 锚点命中被静默吞掉(任务 A 退化)。
    # 故 want=None,裸数组按「只含 reached」兼容,仍非 dict → 视空。
    parsed = parse_llm_json(text or "", want=None)
    if isinstance(parsed, list):
        parsed = {"reached": parsed, "current_chapter": None}
    if not isinstance(parsed, dict):
        return empty

    reached_raw = parsed.get("reached")
    reached: list[dict[str, Any]] = []
    if isinstance(reached_raw, list):
        for item in reached_raw:
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
            reached.append({"anchor_key": key, "drift_score": drift})

    est_raw = parsed.get("current_chapter")
    estimated_chapter: int | None = None
    try:
        if est_raw is not None:
            estimated_chapter = int(est_raw)
            if estimated_chapter < 1:
                estimated_chapter = None
    except (TypeError, ValueError):
        estimated_chapter = None

    # 遗漏补(同 recorder tool-schema 的 progress_motion 修复):_default_judge(recorder_unified 关时走)
    # 之前不产 progress_motion → 该路径 pace fallback 也失效。补上,与史官三合一路径同信号。
    _pm_raw = parsed.get("progress_motion")
    progress_motion: int | None = None
    try:
        if _pm_raw is not None:
            _pm = int(_pm_raw)
            progress_motion = 0 if _pm <= 0 else (2 if _pm >= 2 else 1)
    except (TypeError, ValueError):
        progress_motion = None

    return {"reached": reached, "estimated_chapter": estimated_chapter, "progress_motion": progress_motion}


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


def _normalize_judge_result(raw: Any) -> tuple[list[dict[str, Any]], int | None]:
    """统一判定器返回:新式 dict {reached, estimated_chapter} / 旧式裸 list(只含 reached)。

    返回 (reached_list, estimated_chapter)。
    """
    if isinstance(raw, dict):
        reached = raw.get("reached")
        reached = reached if isinstance(reached, list) else []
        est = raw.get("estimated_chapter")
        try:
            est = int(est) if est is not None else None
        except (TypeError, ValueError):
            est = None
        return reached, (est if (est is None or est >= 1) else None)
    if isinstance(raw, list):
        return raw, None
    return [], None


def _motion_from_raw(raw: Any) -> int | None:
    """从判定器返回里取 progress_motion(0/1/2;缺省/非法 → None=本回合无信号)。
    与 _normalize_judge_result 分开取,避免改其 2 元组返回签名破坏既有调用点/测试。"""
    if not isinstance(raw, dict):
        return None
    v = raw.get("progress_motion")
    if v is None:
        return None
    try:
        iv = int(v)
    except (TypeError, ValueError):
        return None
    return 0 if iv <= 0 else (2 if iv >= 2 else 1)


_FALSY_ENV = ("0", "false", "no", "off", "")
# 叙事节奏兜底:累计多少「推进点」=+1 章(motion 1=+1 点,2=+2 点)。3 ≈ 正常推进每 3 回合 +1 章;
# 重大跨越(motion 2)更快。保守取值治「发散play卡死」又不致跳章(再叠加 _PACE_CAP 每回合上限)。
_PACE_FALLBACK_POINTS_PER_CHAPTER = 3


def _pace_fallback_enabled() -> bool:
    """env RPG_PACE_FALLBACK 默认 '1';设 '0'/'false' 关。"""
    return os.environ.get("RPG_PACE_FALLBACK", "1").strip().lower() not in _FALSY_ENV


def _apply_pace_fallback(db: Any, save_id: int, motion: int | None) -> int:
    """估章对不上原著(发散/无限流副本)、进度卡住时的【确定性兜底】推进。

    机制:recorder 的 progress_motion(纯 LLM 语义信号:本回合叙事推进度)累计成「推进点」,
    够一章(_PACE_FALLBACK_POINTS_PER_CHAPTER)就 +1 章,限速 _PACE_CAP/回合、单调只增。
    判定「有没有前进」是 LLM 的活;累计/落库是确定性代码 —— 既不靠 LLM 自己记着推进度,也不靠
    硬编码回合计数凭空推进(motion None/0 → 不累计不推进)。

    只在 _do 里「本回合估章未推进进度」时调用(估章正常推进=贴原著play,不重复兜底)。
    返回新进度(未推进返 0)。
    """
    if motion is None or motion <= 0:
        return 0
    row = db.execute(
        "select coalesce((worldline->>'progress_chapter')::int, 1) as pc, "
        "coalesce((worldline->>'progress_pace_accum')::int, 0) as ac "
        "from game_sessions where save_id = %s",
        (save_id,),
    ).fetchone()
    if not row:
        return 0
    prev = max(1, int(row.get("pc") or 1))
    accum = int(row.get("ac") or 0) + int(motion)
    chapters = accum // _PACE_FALLBACK_POINTS_PER_CHAPTER
    new_progress = 0
    if chapters >= 1:
        chapters = min(chapters, _PACE_CAP)
        accum -= chapters * _PACE_FALLBACK_POINTS_PER_CHAPTER
        new_progress = prev + chapters
        from gm_serving.settings import advance_progress
        advance_progress(db, save_id, new_progress)  # max-only,单调
    # 持久化累计器(无论是否推进,点数都要留住)。
    db.execute(
        "update game_sessions set worldline = jsonb_set(coalesce(worldline, '{}'::jsonb), "
        "'{progress_pace_accum}', to_jsonb(%s::int), true) where save_id = %s",
        (int(accum), save_id),
    )
    if new_progress:
        log.info(
            "[anchor_reconcile] 叙事节奏兜底推进进度 save=%s %s→%s (motion=%s, 余点=%s)",
            save_id, prev, new_progress, motion, accum,
        )
    return new_progress


def _load_estimate_context(save_id: int) -> dict[str, Any] | None:
    """一次连接备齐有界叙事章估计所需上下文(自连接,与 get_progress_window 同模式):
      · prev   = 当前 progress_chapter(权威进度)
      · floor  = 已确认锚点(occurred/variant)最大原著章 —— ceiling 的地面真值
      · script_id
      · window_chapters = 章节地图 [max(floor,prev), max(floor,prev)+CAP] —— 与 ceiling 口径对齐,
        让判定器既能定位「进度落后于 floor」的存档实际所处章,又不越过 clamp 上限。

    无 script_id → 返 None(本回合跳过估章,调用方据此关估章基线失真)。
    """
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        prev = 1
        r = db.execute(
            "select worldline->>'progress_chapter' as pc from game_sessions where save_id=%s",
            (save_id,),
        ).fetchone()
        if r and r.get("pc") is not None:
            try:
                prev = max(1, int(r["pc"]))
            except (TypeError, ValueError):
                prev = 1
        s = db.execute("select script_id from game_saves where id=%s", (save_id,)).fetchone()
        script_id = int(s["script_id"]) if (s and s.get("script_id") is not None) else None
        if not script_id:
            return None
        fr = db.execute(
            "select coalesce(max(source_chapter), 0) as c from save_anchor_states "
            "where save_id=%s and status in ('occurred','variant')",
            (save_id,),
        ).fetchone()
        floor = int((fr or {}).get("c") or 0)
        lo = max(1, floor, prev)
        hi = lo + _LOOKAHEAD_CAP
        rows = db.execute(
            "select chapter, story_time_label as label, summary from chapter_facts "
            "where script_id=%s and chapter between %s and %s order by chapter",
            (script_id, lo, hi),
        ).fetchall()
    window_chapters = [
        {"chapter": r["chapter"], "label": r.get("label") or "", "summary": r.get("summary") or ""}
        for r in rows
    ]
    return {"prev": prev, "floor": floor, "script_id": script_id, "window_chapters": window_chapters}


def _apply_estimate(db: Any, save_id: int, prev_progress: int, estimated_chapter: int) -> int:
    """有界叙事章估计落库:new = max(prev, floor, clamp(估计, ≤ max(floor,prev)+CAP))(设计 §4.1)。

    · floor = 已确认锚点(occurred/variant)最大原著章 —— 可靠地面真值(此处 fresh 重查,
      故能反映本回合 _apply_hits 刚标记的锚点)。
    · ceiling = max(floor, prev) + CAP —— 估计最多越过地面真值 CAP 章(floor=0 时上限 = prev+CAP,
      根治 ch77 远跳:blast radius 有界)。
    · 单调:绝不低于当前进度(回退是 rewind 端点的显式职责)。
    · prev_progress 是标记前快照;floor 重查 + advance_progress max-only 共同保证不回退,故快照偏旧无害。

    不变量(进度推进的唯一估章写者):progress_chapter 的叙事估章只由本函数写;retrieval.py 进度块
    只做 anchor-floor 同步、绝不引入估章;两路径都经 gm_serving.settings.advance_progress(max-only)
    收敛,故双写不抖动、不互相拉低。改动任一方前请维持此契约。

    返回新进度(未推进返 0)。
    """
    row = db.execute(
        "select coalesce(max(source_chapter), 0) as c from save_anchor_states "
        "where save_id = %s and status in ('occurred', 'variant')",
        (save_id,),
    ).fetchone()
    floor = int((row or {}).get("c") or 0)
    prev = max(1, int(prev_progress or 1))
    ceiling = max(floor, prev) + _LOOKAHEAD_CAP
    candidate = max(prev, floor, min(int(estimated_chapter), ceiling))
    # Q 锚点限速:每回合进度最多 +_PACE_CAP 章(匹配对话节奏,治跳章)。floor 是已确认锚点硬下界
    # (不能退),故限速作用在 floor 之上:candidate = max(floor, min(candidate, prev+PACE))。
    from core.feature_flags import feature_enabled_for_save
    if feature_enabled_for_save("anchor_pace", save_id, db):
        candidate = max(prev, floor, min(candidate, prev + _PACE_CAP))
    if candidate <= prev:
        return 0
    from gm_serving.settings import advance_progress
    advance_progress(db, save_id, candidate)
    log.info(
        "[anchor_reconcile] 叙事估章推进进度 save=%s %s→%s (floor=%s, est=%s, ceil=%s)",
        save_id, prev, candidate, floor, estimated_chapter, ceiling,
    )
    return candidate


def _reconcile_impl(
    save_id: int | None,
    user_id: int | None,
    turn_text: str | None,
    *,
    db: Any,
    _judge: Callable[..., Any] | None,
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

    # 2. 进度窗口 + 窗口内 pending 锚点。
    win = get_progress_window(save_id)
    ch_min = win.get("chapter_min")
    ch_max = win.get("chapter_max")
    # 候选窗口放宽到 lookahead:让【快进/自插入玩家】正在跨的远章锚点也能进候选、被史官看到
    # (原 _MARK_WINDOW=4 窄窗把它们挡在门外 → 快进玩家在 ch16、候选只到 ch11 → 他正跨的锚点
    #  永远标不上、进度只能靠 pace 慢爬 = 行者无疆卡章的结构性一环)。
    # 防跳章不再靠"不给史官看",而靠【标记接受上界 _mark_ceiling = 史官对本回合玩家位置的语义
    # 估计 current_chapter】(见下方):史官说玩家在 ch16、才允许标到 ch16;它没估出来(便宜模型常
    # 返 null)就退回窄窗 ch_min+_MARK_WINDOW —— 保守、与旧版逐字等价、不跳章。marks 恒在候选窗内
    # (win_by_key),故最坏跳章上限 = +_LOOKAHEAD_CAP/回合,与估章 clamp 同界。
    if _anchor_pace(user_id) and ch_min is not None:
        ch_max = int(ch_min) + _LOOKAHEAD_CAP
    pending = list_pending_for_phase(
        save_id, None,
        limit=_MAX_PENDING_PER_TURN,
        chapter_min=ch_min, chapter_max=ch_max,
        order_by_chapter=True,
    )
    # 窗口内 pending 的 anchor_key → 后续校验命中合法性(防越界/编造)。
    win_by_key: dict[str, dict[str, Any]] = {}
    for a in pending:
        k = a.get("anchor_key")
        if k:
            win_by_key[k] = a

    # 3. Bug B 有界叙事章估计上下文。只在【真实默认判定器】路径备料(只读自连接);注入式 _judge
    #    (离线测试)不备料、不连库,估章值由注入判定器自带。备料失败 → est_ctx=None → 本回合关估章
    #    (避免 prev 基线失真还参与落库)。
    # 修正(违背「确定性不得绕过三贤者」的回退):此前【前沿启用时关掉估章】,进度改由纯确定性
    # 「已到达锚点」派生 → 发散局(锚点命中不了)进度冻死(行者无疆 268 号档:turn 503 还卡在第 7 章)。
    # 恢复:前沿模式下史官的进度判断照常跑 ——「本回合有没有前进」是史官(LLM)的判断(progress_motion /
    # 估章),确定性只做累计 / clamp / 单调护栏(_apply_pace_fallback / _apply_estimate 均有界,pace_cap=2/回合、
    # ceiling=prev+CAP,不会 catastrophic 乱跳)。前沿退回只当【揭示护栏】(reached 地板防剧透),不再当
    # 唯一进度源、不再绕过史官。RPG_PROGRESS_NARRATIVE_ESTIMATE=0 仍可全局强关。
    est_on = _estimate_enabled()
    est_ctx: dict[str, Any] | None = None
    # 无论 _judge 是否由 recorder_bridge 注入,只要 est_on 就加载 est_ctx —— 它提供估章的 prev 基线。
    # 原 `and _judge is None` 让生产路径(recorder_bridge 总注入 _judge)恒不加载 → est_prev 恒为 1 →
    # ceiling=max(floor,1)+12 锁死在 13,第 13 章后进度永远推不动(用户长期反馈的「进度卡住」)。
    if est_on:
        try:
            est_ctx = _load_estimate_context(save_id)
        except Exception as exc:
            log.info("[anchor_reconcile] 估章上下文备料失败(本回合跳过估章): %s", exc)
            est_ctx = None

    # 成本门控:窗口内无 pending 且本回合不会估章 → 零 LLM 调用直接 return。
    #   · 有 pending → 判定器照常跑,估章搭这次调用便车(零新增成本,常态)。
    #   · 无 pending 但估章就绪(设计 §73:估章不依赖 pending)→ 仍跑判定器只估章不标锚点。
    #     此时该回合产生 1 次廉价调用,仅在「进度窗口(默认 50 章)内无任何 pending 锚点」的稀疏空白段
    #     触发(根治锚点间隔 > 窗口时的进度冻结);env RPG_PROGRESS_NARRATIVE_ESTIMATE=0 可关此行为。
    will_estimate = bool(est_on and (est_ctx is not None or _judge is not None))
    # Q 死亡失效:pace on 时每回合都查正文有无角色死亡(纯确定性、独立于锚点命中/估章,不需 LLM);
    # text 顶部已保证非空。pace off → do_death=False,控制流与旧版逐字等价。
    do_death = _anchor_pace(user_id)
    if not pending and not will_estimate and not do_death:
        return 0

    # 4. 廉价判定(同一次调用:任务 A 锚点命中 + 任务 B 当前章估计)。
    #    仅当有 pending 或要估章时才跑 LLM —— 死亡失效是纯确定性,不该为它平白产生 LLM 调用。
    #    注入式 _judge 保持旧签名契约(老测试不破);默认判定器才接 window_chapters。
    reached: list[dict[str, Any]] = []
    estimated_chapter: int | None = None
    progress_motion: int | None = None
    if pending or will_estimate:
        window_chapters = est_ctx.get("window_chapters") if est_ctx else None
        if _judge is not None:
            raw = _judge(user_id, text, pending, save_id=save_id)
        else:
            raw = _default_judge(user_id, text, pending, save_id=save_id, window_chapters=window_chapters)
        reached, estimated_chapter = _normalize_judge_result(raw)
        progress_motion = _motion_from_raw(raw)

    # 标记接受上界(防跳章):史官对本回合玩家位置的语义估计 current_chapter 优先 —— 它说玩家在
    # 第几章,就只允许标到第几章(+1 容差);史官没估出来(便宜模型常 null)则退回窄窗 ch_min+_MARK_WINDOW
    # (保守、与旧版逐字等价)。宽候选窗口负责【让快进玩家的真锚点能被看到】,这个上界负责【拒绝超出
    # 玩家实际位置的远章误标】——该标的标得上,不该标的挡得住。marks 恒在候选窗内,故上界天然 ≤ ch_min+_LOOKAHEAD_CAP。
    _cmin = int(ch_min) if ch_min is not None else 1
    _est_ok = estimated_chapter is not None and int(estimated_chapter) >= _cmin
    _mark_ceiling = (int(estimated_chapter) + 1) if _est_ok else (_cmin + _MARK_WINDOW)

    def _anchor_src_chapter(k: str) -> int | None:
        _p = (k or "").split(":")
        return int(_p[1]) if len(_p) > 1 and _p[1].isdigit() else None

    # 只保留窗口内、合法 anchor_key 的命中(防判定器越界到远未来/编造 key)。去重。
    seen: set[str] = set()
    valid_hits: list[dict[str, Any]] = []
    # 顺序:LLM 判定【优先】(它读上下文、算 drift、能识别玩家背离/拒绝/敌对 → 该标 variant 还是
    # 不标都由它定);确定性 intro 标记【兜底补漏】(保守 LLM 屡漏 intro)只补 LLM 没提到的 key,且
    # 仅 character/concept/item(场景已在 _deterministic_intro_hits 排除)。这样背离时 LLM 的语义判断
    # 不被确定性 d=0.0 覆盖,faithful 时 LLM 漏的仍被兜住。
    for h in reached:
        if not isinstance(h, dict):
            continue
        key = (h.get("anchor_key") or "").strip()
        if not key or key in seen or key not in win_by_key:
            continue
        _kch = _anchor_src_chapter(key)
        if _kch is not None and _kch > _mark_ceiling:
            continue  # 防跳章:锚点原著章 > 玩家实际位置上界(史官估章 / 无估章退窄窗)→ 拒绝远章误标
        seen.add(key)
        try:
            drift = float(h.get("drift_score"))
        except (TypeError, ValueError):
            drift = 0.2
        valid_hits.append({"anchor_key": key, "drift_score": max(0.0, min(1.0, drift))})
        if len(valid_hits) >= _MAX_MARK_PER_TURN:
            break  # 保守:单回合最多标 N 个
    # [round-4-P2] _deterministic_intro_hits 是纯字符串匹配(角色名现身即「首次登场」锚点到达),
    #   不依赖 LLM,应始终运行——原来锁在 _anchor_pace(user_id) 闸后,flag OFF 时 intro 锚点退回保守
    #   LLM 判定会漏标/拖慢、卡住进度。pace flag 只该管章窗收窄/标记速率,不该管这条确定性路径。
    if len(valid_hits) < _MAX_MARK_PER_TURN:
        for h in _deterministic_intro_hits(save_id, pending, text):
            k = (h.get("anchor_key") or "").strip()
            _kc = _anchor_src_chapter(k)
            if _kc is not None and _kc > _mark_ceiling:
                continue  # 防跳章:确定性 intro 兜底也受同一位置上界约束(宽候选窗后尤其必要)
            if k and k in win_by_key and k not in seen:
                seen.add(k)
                valid_hits.append({"anchor_key": k, "drift_score": 0.0})
                if len(valid_hits) >= _MAX_MARK_PER_TURN:
                    break

    # 估章是否落库:本回合会估章 + 估章值有效。无锚点命中、不估章、不查死亡 → 无事可做。
    do_estimate = bool(will_estimate and estimated_chapter)
    # 叙事节奏兜底:估章对不上原著(发散play)时,靠 recorder 的 progress_motion 确定性推进进度。
    do_pace = bool(est_on and _pace_fallback_enabled() and progress_motion and progress_motion >= 1)
    if not valid_hits and not do_estimate and not do_death and not do_pace:
        return 0

    # 5. 确定性落库:锚点标记 + 有界估章推进 + 死亡失效,同一 (user,save) scope lock + 单连接内。
    est_prev_ctx = int(est_ctx.get("prev")) if est_ctx else None
    def _do(conn: Any) -> int:
        marked = _apply_hits(conn, save_id, user_id, valid_hits) if valid_hits else 0
        estimate_advanced = 0
        if do_estimate:
            # [round-4-P2] est_ctx 不可用(recorder_bridge 注入 _judge 路径里 _load_estimate_context
            #   失败)时,est_prev 原硬编码 1 → ceiling=1+CAP 把已在更后章节玩家的进度天花板压回、卡住
            #   推进。改为锚到权威当前进度(与 _load_estimate_context 同源:game_sessions.worldline)。
            _ep = est_prev_ctx
            if _ep is None:
                try:
                    r = conn.execute(
                        "select worldline->>'progress_chapter' as pc from game_sessions where save_id=%s",
                        (save_id,),
                    ).fetchone()
                    _ep = max(1, int(r["pc"])) if (r and r.get("pc") is not None) else 1
                except Exception:
                    _ep = 1
            # 锚点标记后 floor 可能已升,_apply_estimate 内重查 floor 算 ceiling。
            estimate_advanced = _apply_estimate(conn, save_id, _ep, int(estimated_chapter))
        # 估章未推进(对不上原著章号/发散play)→ 用 progress_motion 确定性兜底推进。
        # 估章已推进=贴原著play,不再叠加兜底(避免双推进)。
        if do_pace and not estimate_advanced:
            _apply_pace_fallback(conn, save_id, progress_motion)
        if do_death:
            _invalidate_dead_entity_anchors(conn, save_id, text)
        return marked

    if db is not None:
        return _do(db)

    from platform_app.db import connect, init_db
    from tools_dsl.command_dispatcher import _get_sync_scope_lock
    init_db()
    with _get_sync_scope_lock((user_id, save_id)), connect() as conn:
        return _do(conn)


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
        # P4(S6):写前沿(GM/判定器声明到达 → 增量并入可见集)。复用同一 db 连接,原子。
        # flag off 时 _frontier_on 返 False → 不写,行为零变化。
        from kb.reveal import _frontier_on as _fr_on
        if _fr_on(save_id):
            try:
                from kb.reveal import mark_anchor_reached
                mark_anchor_reached(save_id, key, turn=occurred_turn,
                                    via="reconciler", drift=drift, db=db)
            except Exception as fr_exc:  # 前沿写失败不阻断锚点标记
                log.warning("[anchor_reconcile] mark_anchor_reached 失败(忽略): %s", fr_exc)
    return marked


__all__ = ["reconcile_anchors_for_turn"]
