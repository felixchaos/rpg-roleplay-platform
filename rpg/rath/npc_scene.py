"""rath/npc_scene.py — NPC-NPC 离线对手戏(搖光轴②:玩家不在场时角色互演)。

纯函数层(select_scene_pair / build_scene_prompts / validate_scene)零 IO 可单测;
LLM 调用与落库在 engine.tick_experiment 编排。铁律:绝不写游戏 state——
scene_summary 落 kb_events(情景召回天然可见),transcript 落 rath_events(观测台展示)。
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_LINES = 8
MAX_LINE_CHARS = 120
MAX_SUMMARY_CHARS = 160
MAX_PRIVATE_MEMORY_CHARS = 80

# 通用称谓黑名单(实测:开场史官把玩家的神秘身体记成「少女」进了 relationships,
# 被选进对手戏=昏迷中的玩家开口说话)。泛指条目不是角色,不配登台。
_GENERIC_NAMES = frozenset((
    "少女", "少年", "男人", "女人", "老人", "老者", "孩子", "小孩",
    "神秘人", "陌生人", "路人", "士兵", "侍者", "店主", "商人", "旅人",
))


def select_scene_pair(
    state_data: dict, extra_candidates: list[str] | None = None,
    exclude_names: set[str] | None = None,
) -> tuple[str, str] | None:
    """从快照选两个对手戏 NPC:议程 NPC 优先(有 goal/stance 才演得像),
    不足两个则从 relationships 补位。选不出两个 → None(本 tick 不演,正常)。

    extra_candidates(用户实锤「主角团根本没这人」后补):原著 canon 主要角色
    (character_cards 按 importance,调用方已做防剧透门控)。有 canon 候选时,
    第二席让给 canon 头名 —— 让离线世界围绕书的卡司运转,而不是围着 GM 即兴
    发明的路人打转。"""
    if not isinstance(state_data, dict):
        return None
    excl = {x for x in (exclude_names or set()) if x}

    def _ok(n) -> bool:
        return (isinstance(n, str) and n.strip()
                and n not in _GENERIC_NAMES and n not in excl)

    agendas = state_data.get("npc_agendas") or {}
    names = [n for n in agendas if _ok(n)]
    if len(names) < 2:
        rels = state_data.get("relationships") or {}
        for n in rels:
            if _ok(n) and n not in names:
                names.append(n)
            if len(names) >= 2:
                break
    extras = [n for n in (extra_candidates or [])
              if _ok(n) and n not in names]
    if not names and len(extras) >= 2:
        return extras[0], extras[1]  # 全新档:直接演卡司
    if len(names) < 1 or (len(names) < 2 and not extras):
        return None
    # 确定性选择:议程最近更新的在前(无议程的排后但保持字典序稳定)
    def _key(n: str):
        a = agendas.get(n) or {}
        return (-int(a.get("updated_turn") or 0), n)
    names.sort(key=_key)
    if extras:
        return names[0], extras[0]  # 本地熟人 × 原著卡司
    return names[0], names[1]


def _npc_dossier(state_data: dict, name: str, extra_dossiers: dict | None = None) -> str:
    agendas = state_data.get("npc_agendas") or {}
    rels = state_data.get("relationships") or {}
    a = agendas.get(name) or {}
    parts = [f"姓名:{name}"]
    extra = (extra_dossiers or {}).get(name)
    if extra:
        parts.append(str(extra)[:120])
    if a.get("goal"):
        parts.append(f"当前目标:{a['goal']}")
    if a.get("stance"):
        parts.append(f"当前态度:{a['stance']}")
    r = rels.get(name)
    if isinstance(r, str) and r.strip():
        parts.append(f"与玩家关系:{r.strip()[:40]}")
    return ";".join(parts)


def build_scene_prompts(
    state_data: dict, npc_a: str, npc_b: str, *,
    elapsed_hint: str = "", recent_events: list[str] | None = None,
    world_context: str = "", directive: str = "",
    extra_dossiers: dict | None = None,
    player_in_scene: str = "",
) -> tuple[str, str]:
    """构造对手戏 prompt。防剧透同心跳口径:只喂快照里已有的信息 + 世界书要点
    (world_context,拆书审计后补:离线戏没有世界观材料会滑向平庸写实,战姬味丢失)。"""
    player = (state_data.get("player") or {})
    world = (state_data.get("world") or {})
    location = str(player.get("current_location") or "").strip()
    wtime = str(world.get("time") or "").strip()
    ev_lines = "\n".join(f"- {e}" for e in (recent_events or [])[:4]) or "(暂无)"
    _rule3 = (
        (f"3. 本场包含玩家角色【{player_in_scene}】:由你按其【设定】驱动其自主行动。"
         "行动必须严格符合其当前状态(如昏迷则只能有微弱生理反应/本能的力量外溢/梦呓,绝不能清醒对话);"
         "小步演进;**不替玩家做重大不可逆决定**(不缔约/不杀伤/不离开当前地点/不暴露其最深的秘密)。\n")
        if player_in_scene else
        "3. 玩家不在场:对话中可以提到玩家(用其名字),但玩家绝不出现、绝不说话。\n"
    )
    system_prompt = (
        "你是离线世界模拟器:模拟玩家未亲自操作期间,世界中角色之间的一小段真实互动。\n"
        "铁律:\n"
        "1. 只能使用下面档案、世界观要点与近况中给出的信息,不得发明新的重要人物/地点/物品。\n"
        "2. 地理连贯:场景只能发生在两人当前合理所在之处;远方的人与事只能被谈及,不能到场;\n"
        "   若玩家所在地未知,场景应设在与两人身份相符的日常场所,禁止凭空编造军事/战场场景。\n"
        + _rule3 +
        f"4. 对话不超过 {MAX_TRANSCRIPT_LINES} 行,每行不超过 {MAX_LINE_CHARS} 字;小事即可,不要写重大转折。\n"
        + "5. 顺其自然:这是一段真实生活——情节可以推进,也可以只是日常,由情境与人物动机决定,"
        "不强求进展也不禁止进展;**推进时必须从已有事实自然长出**,禁止为了『有新内容』而凭空发明。"
        "不要复读近期动向。\n"
        + "6. **专有名词铁律**:绝不发明新的机构/地点/组织/装置/计划的名字;只能使用档案、世界观要点、近况中出现过的名词。\n"
        "   世界观要点里的元素可以自然进入闲谈,体现这个世界的质感。\n"
        "7. 只输出严格 JSON(不要代码围栏),schema:\n"
        '{"transcript":[{"speaker":"名字","line":"台词或动作"}],'
        '"scene_summary":"≤120字的第三人称场景纪要(写清两人谈了什么/做了什么)",'
        '"npc_updates":{"名字":{"goal":"可选,若目标有微调","stance":"可选","private_memory":"这个角色私下会记住的一句话"}}}'
    )
    wc_block = f"【世界观要点】\n{world_context}\n" if (world_context or "").strip() else ""
    dv_block = (f"【观测者引导(世界应朝此方向自然倾斜,允许铺垫,不得突兀跳变)】{directive.strip()}\n"
                if (directive or "").strip() else "")
    user_prompt = (
        f"【NPC甲】{_npc_dossier(state_data, npc_a, extra_dossiers)}\n"
        f"【NPC乙】{_npc_dossier(state_data, npc_b, extra_dossiers)}\n"
        f"【玩家最后所在】{location or '(未知)'}\n"
        f"【世界时间】{wtime or '(未知)'}\n"
        f"【离线时长】{elapsed_hint or '(不详)'}\n"
        f"{wc_block}{dv_block}"
        f"【近期世界侧动向(已发生,不要复读)】\n{ev_lines}\n\n"
        f"请生成 {npc_a} 与 {npc_b} 之间这段离线互动。"
    )
    return system_prompt, user_prompt


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


_NOUN_SUFFIX_RE = re.compile(
    r"[一-鿿]{1,6}(?:试验场|实验场|实验室|研究所|研究院|兵工厂|军工厂|司令部|指挥部|"
    r"办事处|委员会|结社|教团|骑士团|情报局|安全局|管理局|档案馆|收容所|基地|要塞|"
    r"计划|行动|工程|协议|条约|装置|机关|型号)"
)


def find_fabricated_nouns(text: str, known_text: str) -> list[str]:
    """专有名词闸(剧情膨胀实锤:3拍编出G7臂甲/第七试验场/毛瑟厂密室调令):
    机构/地点/计划/装置类后缀的词若不在已知材料里出现过 → 视为幻觉新造。纯函数。"""
    if not text:
        return []
    known = known_text or ""
    out = []
    for m in _NOUN_SUFFIX_RE.finditer(text):
        tok = m.group(0)
        # 贪婪捕获会把句子前缀吞进 token(「他要去第七试验场」);判定用右对齐渐进:
        # token 的任一右对齐子串(≥后缀+1字)在材料中出现 → 视为已知(宁漏勿误)。
        is_known = any(tok[i:] in known for i in range(len(tok) - 2))
        if not is_known and tok not in out:
            out.append(tok)
    return out


def validate_scene(raw_text: str, npc_a: str, npc_b: str,
                   known_text: str | None = None) -> dict | None:
    """解析+验收 LLM 场景输出。返回规整 dict 或 None(拒收,本 tick 无戏,正常)。

    防臆造闸(同柱子3口径):transcript speaker 与 npc_updates 键都必须 ∈ {npc_a, npc_b}。
    """
    if not raw_text:
        return None
    text = raw_text.strip()
    m = _FENCE_RE.search(text)
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
    allowed = {npc_a, npc_b}
    transcript = []
    for row in (data.get("transcript") or [])[:MAX_TRANSCRIPT_LINES]:
        if not isinstance(row, dict):
            continue
        speaker = str(row.get("speaker") or "").strip()
        line = str(row.get("line") or "").strip()[:MAX_LINE_CHARS]
        if speaker in allowed and line:
            transcript.append({"speaker": speaker, "line": line})
    summary = str(data.get("scene_summary") or "").strip()[:MAX_SUMMARY_CHARS]
    if not transcript or not summary:
        return None
    updates: dict[str, dict] = {}
    raw_updates = data.get("npc_updates") or {}
    if isinstance(raw_updates, dict):
        for name, u in raw_updates.items():
            if str(name).strip() not in allowed or not isinstance(u, dict):
                continue
            clean: dict[str, str] = {}
            for k in ("goal", "stance"):
                v = str(u.get(k) or "").strip()
                if v:
                    clean[k] = v[:80]
            pm = str(u.get("private_memory") or "").strip()
            if pm:
                clean["private_memory"] = pm[:MAX_PRIVATE_MEMORY_CHARS]
            if clean:
                updates[str(name).strip()] = clean
    if known_text is not None:
        joined = summary + " " + " ".join(r["line"] for r in transcript)
        fabricated = find_fabricated_nouns(joined, known_text)
        if fabricated:
            log.info("[rath] 场景拒收(幻觉新造名词): %s", fabricated)
            return None
    return {"transcript": transcript, "scene_summary": summary, "npc_updates": updates}
