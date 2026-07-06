"""rath/sim.py — RATH 仿真核心 v2(状态优先,docs/design/rath_simulation_v2.md)。

架构倒置:世界的本体是 sim_state(结构化、持续演化),文字只是投影。
- 调度(LLM-A)产出结构化意图 → 裁决(确定性单写者)逐字段验收落状态 →
  呈现(LLM-B)只把已裁决的事写成对白。**散文层无权决定情节**=治歪的地基。
- 玩家设定神圣/状态守恒由裁决层代码强制,不再只靠 prompt。
纯函数层(init/validate/apply/prompts)零 IO 可单测;LLM 与落库由 engine 编排。
"""
from __future__ import annotations

import json
import logging
import re

from core.text_gates import find_fabricated_nouns

log = logging.getLogger(__name__)

MAX_FACTS = 40
MAX_MEMORY = 10
MAX_THREADS = 6
NIGHT_START, NIGHT_END = 23 * 60, 6 * 60  # 夜间强制睡眠窗(分钟,模 1440)
UNCONSCIOUS_MARKERS = ("昏迷", "沉睡", "深度睡眠", "无意识")


# ── 初始化 ───────────────────────────────────────────────────────────

def init_sim_state(snapshot: dict, cast_cards: list[dict], wb_rows: list[dict],
                   *, clock_min: int = 0, canon_beats: list[dict] | None = None) -> dict:
    """从游戏快照+canon 卡司+世界书确定性构建初始 sim_state。纯函数。"""
    player = (snapshot or {}).get("player") or {}
    pname = str(player.get("name") or "").strip() or "玩家"
    ploc = str(player.get("current_location") or "").strip() or "未知地点"
    pbg = str(player.get("background") or player.get("identity_role_desc") or "").strip()
    pstatus = _derive_player_status(snapshot)

    cast: dict = {
        pname: {
            "kind": "player",
            "sheet": (str(player.get("role") or "").strip() + ";" + pbg[:220]).strip(";"),
            "location": ploc,
            "activity": "昏迷沉睡" if pstatus == "昏迷" else "静养",
            "goal": "",
            "stance": "",
            "status": pstatus,
            "memory": [],
        }
    }
    for c in cast_cards or []:
        n = str(c.get("name") or "").strip()
        if not n or n == pname:
            continue
        cast[n] = {
            "kind": "npc",
            # 优先用正典渲染的 sheet(_format_card,engine 传入);无则回退拼字段
            "sheet": str(c.get("sheet") or "").strip() or ";".join(x for x in [
                str(c.get("personality") or "").strip()[:80],
                ("外貌:" + str(c.get("appearance") or "").strip()[:40]) if c.get("appearance") else "",
            ] if x),
            "location": ploc,
            "activity": "日常起居",
            "goal": "",
            "stance": "",
            "status": "",
            "memory": [],
        }

    places = [ploc]
    for r in wb_rows or []:
        t = str(r.get("title") or "")
        # 世界书地点类条目入白名单(标题去分类前缀)
        if any(k in t for k in ("地点", "场所", "城", "镇", "村", "宅", "厂", "馆", "宫")):
            nm = t.split("·")[-1].strip()
            if nm and nm not in places:
                places.append(nm)
    facts = [f"{pname}目前在{ploc},状态:{pstatus or '正常'}。"]
    hist = (snapshot or {}).get("history") or []
    if hist:
        last = str((hist[-1] or {}).get("content") or "")
        if last:
            facts.append("最近发生:" + re.sub(r"\s+", " ", last)[:160])
    threads = []
    if pstatus == "昏迷":
        threads.append({
            "id": "t1",
            "desc": f"昏迷的{pname}:照料她,以及她究竟是谁(谜团保持开放,不得替她定来历)",
            "tension": 5,
            "participants": [n for n in cast if n != pname][:2] + [pname],
        })
    canon = {"cursor": 0, "stall": 0,
             "beats": [{"chapter": int(b.get("chapter") or 0),
                        "text": str(b.get("summary") or "").strip()[:140]}
                       for b in (canon_beats or []) if str(b.get("summary") or "").strip()][:12]}
    return {"clock_min": int(clock_min), "cast": cast, "places": places,
            "facts": facts, "threads": threads, "canon": canon}


def _derive_player_status(snapshot: dict) -> str:
    player = (snapshot or {}).get("player") or {}
    blob = " ".join(str(player.get(k) or "") for k in ("current_status", "background", "identity_role_desc"))
    hist = (snapshot or {}).get("history") or []
    if hist:
        blob += " " + str((hist[-1] or {}).get("content") or "")[-300:]
    return "昏迷" if any(m in blob for m in UNCONSCIOUS_MARKERS) else ""


# ── 调度(LLM-A)prompt 与验收 ────────────────────────────────────────

def compact_view(sim: dict) -> str:
    lines = [f"世界钟:{sim.get('clock_min', 0)}分钟"]
    for n, c in (sim.get("cast") or {}).items():
        tag = "[玩家]" if c.get("kind") == "player" else ""
        st = f"状态:{c['status']};" if c.get("status") else ""
        lines.append(f"- {n}{tag}:在{c.get('location')};正在{c.get('activity')};{st}"
                     f"目标:{c.get('goal') or '(无)'};态度:{c.get('stance') or '(平静)'}")
    lines.append("已知地点:" + "、".join(sim.get("places") or []))
    lines.append("剧情线:")
    for t in sim.get("threads") or []:
        lines.append(f"  · [{t['id']}|张力{t['tension']}] {t['desc']}")
    canon = sim.get("canon") or {}
    beats = canon.get("beats") or []
    cur = int(canon.get("cursor") or 0)
    nxt = beats[cur:cur + 2]
    if nxt:
        lines.append("原著河道(这个世界正在/即将发生的主流事件):")
        for b in nxt:
            lines.append(f"  → {b['text']}")
    lines.append("已确立事实(节选):")
    for f in (sim.get("facts") or [])[-8:]:
        lines.append(f"  · {f}")
    return "\n".join(lines)


_SCHED_SYSTEM = """你是世界仿真的调度器:决定接下来这段时间里,每个角色做什么、谁与谁相遇、剧情线如何呼吸。
铁律:
0. 若某角色的位置是「未知地点」,本拍必须先为其确立一个符合情境的具体位置(cast_updates.location)。
1. 只能调度下面列出的角色与地点;确需新的日常小地点(如某人的房间/街角)可给出,但新组织/新机构/新装置一律禁止发明。
2. 每个角色的行动必须符合其设定、状态、目标与此刻时间(深夜就该睡觉,工作日就该干活)。
3. 玩家角色[player]的状态神圣:昏迷则只能沉睡/微弱生理反应,绝不能行动或对话;
   绝不为其编造来历/身份;其谜团只属于玩家。
4. 剧情自然呼吸:可以推进也可以只是生活;推进必须从已确立事实自然长出;
   不必每拍都有相遇(interaction 可为 null)。
5. 有【观测者引导】时,将其翻译成剧情线/目标的调整,让世界朝该方向自然倾斜。
6. 生活优先:角色有自己的本职与日常,调查/异象不得吞噬全部生活;除非某剧情线张力≥7,
   角色应主要处于本职与日常活动。
7. 世界的超常现象只能来自【世界观要点】已有的体系;禁止发明新的超常机制/发光装置/
   现象链;禁止让环境发生灾变级变化;**禁止围绕玩家角色搭建解释其力量的装置或现象**。
8. 原著河道是这个世界的主流:角色的本职、谈资、world_events 应与河道交织(回声/铺垫/
   亲历);当河道第一条动向在世界中自然成熟发生,输出 canon_advance: true。
只输出严格 JSON(不要围栏):
{"cast_updates": {"名字": {"location": "可选", "activity": "可选", "goal": "可选", "stance": "可选"}},
 "interaction": {"participants": ["甲","乙"], "place": "已知地点", "reason": "为何相遇", "expected_outcome": "本场自然收在哪"} 或 null,
 "world_events": ["≤1条,必须直接影响列出的角色或舞台"],
 "thread_updates": [{"id": "t1", "tension_delta": -2到2, "note": "≤40字"}],
 "new_threads": [{"desc": "≤60字", "tension": 1到6, "participants": ["名字"]}],
 "new_facts": ["≤2条,只能是本窗口行动的自然结果"],
 "canon_advance": false}"""


def build_scheduler_prompts(sim: dict, *, elapsed_hint: str, directive: str = "",
                            world_context: str = "") -> tuple[str, str]:
    user = (f"【经过时间】{elapsed_hint}\n【世界现状】\n{compact_view(sim)}\n"
            + (f"【世界观要点】\n{world_context}\n" if world_context else "")
            + (f"【观测者引导】{directive}\n" if directive else "")
            + "请给出这段时间的调度。")
    return _SCHED_SYSTEM, user


def _whitelist(sim: dict, extra: str = "") -> str:
    parts = [extra or ""]
    parts += list((sim.get("cast") or {}).keys())
    parts += sim.get("places") or []
    parts += sim.get("facts") or []
    for b in ((sim.get("canon") or {}).get("beats") or []):
        parts.append(str(b.get("text") or ""))
    for t in sim.get("threads") or []:
        parts.append(str(t.get("desc") or ""))
    for c in (sim.get("cast") or {}).values():
        parts.append(str(c.get("sheet") or ""))
        parts.append(str(c.get("goal") or ""))
    return " ".join(parts)


def parse_scheduler_output(raw_text: str) -> dict | None:
    # 复用平台 parse_llm_json(专治便宜模型形态漂移;基础设施复用清单见设计文档§7)
    from core.json_parse import parse_llm_json
    data = parse_llm_json(raw_text or "", want=dict)
    return data if isinstance(data, dict) else None


def resolve_cast_name(names, raw: str) -> str | None:
    """实体别名归并(500k 浸泡实锤:调度输出简称「菲莉丝」,cast 键=全名「菲莉丝·卡俄斯」
    →被当未知角色拒收,玩家 agent 僵死;「侍者=汉斯」同族)。
    精确命中优先;否则互包含(≥2字)且唯一命中才归并,歧义不猜。确定性。"""
    n = str(raw or "").strip()
    if not n:
        return None
    pool = list(names)
    if n in pool:
        return n
    # 剥尾部注记(「菲莉丝[玩家]」「薇欧拉(昏迷)」——LLM 会照抄视图标签进键名)
    n2 = re.sub(r"[\s\[【(（].*$", "", n).strip()
    if n2 in pool:
        return n2
    n = n2 or n
    if len(n) < 2:
        return None
    hits = [k for k in pool if (n in k) or (k in n)]
    return hits[0] if len(hits) == 1 else None


def apply_scheduler_output(sim: dict, data: dict, *, world_context: str = "") -> dict:
    """裁决层(确定性单写者):逐字段验收 → 落 sim_state。返回 {applied:…, rejected:[原因]}。
    玩家状态守恒与名词闸在这里代码强制。纯函数(原地改 sim)。"""
    rejected: list[str] = []
    applied = {"cast": 0, "events": [], "interaction": None, "threads": 0, "facts": 0}
    cast = sim.setdefault("cast", {})
    known = _whitelist(sim, world_context)

    _pname = next((n for n, c in cast.items() if c.get("kind") == "player"), "")
    # 全名+首段简称都算提及玩家(「菲莉丝·卡俄斯」→「菲莉丝」,防简称绕过装置闸)
    _pkeys = [x for x in {_pname, _pname.split("·")[0] if _pname else ""} if len(x) >= 2]
    _APPARATUS = ("共振", "能量转移", "能量核心", "激活条件", "装置")

    def _gate(text: str, what: str) -> bool:
        t = str(text or "")
        fab = find_fabricated_nouns(t, known)
        if fab:
            rejected.append(f"{what}:新造名词{'/'.join(fab[:2])}")
            return False
        # 玩家设定神圣旁路闸(浸泡实锤:铭牌共振玩家头顶能量=给玩家力量建解释装置)
        if _pkeys and any(p in t for p in _pkeys) and any(k in t for k in _APPARATUS):
            rejected.append(f"{what}:围绕玩家搭建解释装置")
            return False
        return True

    # cast_updates(键先过别名归并:简称/全名互认)
    for name, u in (data.get("cast_updates") or {}).items():
        rk = resolve_cast_name(cast, str(name))
        c = cast.get(rk) if rk else None
        if not c or not isinstance(u, dict):
            rejected.append(f"cast_updates:未知角色{name}")
            continue
        unconscious = c.get("kind") == "player" and c.get("status") in ("昏迷",)
        for k in ("location", "activity", "goal", "stance"):
            v = str(u.get(k) or "").strip()
            if not v:
                continue
            if k == "location":
                if unconscious and str(c.get("location") or "") != "未知地点":
                    # 守恒=不可移动;但「落定未知位置」不是移动(实锤:昏迷玩家位置永远卡死未知)
                    rejected.append("玩家昏迷:位置不可变")
                    continue
                if v not in (sim.get("places") or []):
                    if not _gate(v, "新地点"):
                        continue
                    sim.setdefault("places", []).append(v)
            if unconscious and k == "activity" and not any(m in v for m in ("沉睡", "昏迷", "梦", "呼吸", "颤", "翕动")):
                rejected.append(f"玩家昏迷:活动『{v[:12]}』违反状态守恒")
                continue
            if k in ("goal", "activity") and not _gate(v, f"{name}.{k}"):
                continue
            c[k] = v[:80]
            applied["cast"] += 1

    # interaction
    it = data.get("interaction")
    if isinstance(it, dict):
        ps = []
        for x in (it.get("participants") or []):
            rp = resolve_cast_name(cast, str(x))
            if rp and rp not in ps:
                ps.append(rp)
        ps = ps[:2]
        place = str(it.get("place") or "").strip()
        unconscious_active = [p for p in ps if cast[p].get("kind") == "player"
                              and cast[p].get("status") == "昏迷"]
        if len(ps) == 2 and (place in (sim.get("places") or []) or _gate(place, "相遇地点")):
            # 昏迷玩家可以是场景的对象(被照料/被谈论),不作为主动方——呈现层约束
            applied["interaction"] = {
                "participants": ps, "place": place or cast[ps[0]].get("location") or "",
                "reason": str(it.get("reason") or "")[:80],
                "expected_outcome": str(it.get("expected_outcome") or "")[:80],
                "passive": unconscious_active,
            }
        elif it:
            rejected.append("interaction:参与者/地点未过验收")

    # world_events(≤1,名词闸)
    for ev in (data.get("world_events") or [])[:1]:
        t = str(ev or "").strip()
        if t and _gate(t, "world_event") and "你" not in t:
            applied["events"].append(t[:120])

    # threads
    threads = sim.setdefault("threads", [])
    by_id = {t["id"]: t for t in threads}
    for tu in (data.get("thread_updates") or []):
        t = by_id.get(str((tu or {}).get("id")))
        if not t:
            continue
        try:
            delta = max(-2, min(1, int(tu.get("tension_delta") or 0)))  # 升压封1,降压封2(防棘轮)
        except Exception:
            delta = 0
        t["tension"] = max(0, min(10, int(t.get("tension") or 0) + delta))
        note = str(tu.get("note") or "").strip()
        if note and _gate(note, "thread.note"):
            t["desc"] = (t["desc"].split("——")[0] + "——" + note)[:120]
        applied["threads"] += 1
    for nt in (data.get("new_threads") or [])[:1]:
        if len(threads) >= MAX_THREADS:
            break
        desc = str((nt or {}).get("desc") or "").strip()
        if desc and _gate(desc, "new_thread"):
            threads.append({
                "id": f"t{len(threads) + 1}",
                "desc": desc[:80],
                "tension": max(1, min(6, int((nt or {}).get("tension") or 3))),
                "participants": list(dict.fromkeys(
                    rp for rp in (resolve_cast_name(cast, p)
                                  for p in ((nt or {}).get("participants") or [])) if rp))[:3],
            })
            applied["threads"] += 1

    # facts(≤2,滚动封顶)
    facts = sim.setdefault("facts", [])
    for f in (data.get("new_facts") or [])[:2]:
        t = str(f or "").strip()
        if t and _gate(t, "new_fact"):
            facts.append(t[:120])
            applied["facts"] += 1
    if len(facts) > MAX_FACTS:
        sim["facts"] = facts[-MAX_FACTS:]

    # 原著河道推进(canon_advance):动向成熟 → 入事实池,游标前移
    canon = sim.get("canon") or {}
    beats = canon.get("beats") or []
    cur = int(canon.get("cursor") or 0)
    if data.get("canon_advance") and cur < len(beats):
        sim.setdefault("facts", []).append("【原著进程】" + beats[cur]["text"])
        canon["cursor"] = cur + 1
        canon["stall"] = 0
        applied["canon_advance"] = True
        applied["canon_text"] = beats[cur]["text"]
    else:
        canon["stall"] = int(canon.get("stall") or 0) + 1
        applied["canon_advance"] = False
    if len(sim.get("facts") or []) > MAX_FACTS:
        sim["facts"] = sim["facts"][-MAX_FACTS:]
    return {"applied": applied, "rejected": rejected}


CANON_STALL_LIMIT = 6  # 河道滞留上限:连续N拍未自然成熟则强制前行(原著世界不等小屋)
CANON_REFILL_THRESHOLD = 3  # 未消费段 ≤N 时低水位,该补给了
CANON_KEEP_CONSUMED = 1  # 补给裁剪时保留的已消费段数(cursor 平移)


def canon_refill_from(sim: dict) -> int | None:
    """河道低水位检查:未消费段不足时返回应从哪章续拉(无需补给→None)。纯函数。

    500k 浸泡前置修:beats 只在 init 装 12 段,长程仿真最迟 12×CANON_STALL_LIMIT
    拍烧穿,之后河道空转=退回 0% 原著重合。"""
    canon = sim.get("canon") or {}
    beats = canon.get("beats") or []
    if not beats:
        return None  # 从未有过河道(无剧本材料)→ 无处可补
    cur = int(canon.get("cursor") or 0)
    if len(beats) - cur > CANON_REFILL_THRESHOLD:
        return None
    last_ch = max((int(b.get("chapter") or 0) for b in beats), default=0)
    return (last_ch + 1) if last_ch > 0 else None


def extend_canon(sim: dict, new_beats: list[dict]) -> int:
    """追加河道段(只收章号大于现有最后一章的,防重)并裁剪已消费段。返回追加数。纯函数。"""
    canon = sim.setdefault("canon", {"cursor": 0, "stall": 0, "beats": []})
    beats = canon.setdefault("beats", [])
    last_ch = max((int(b.get("chapter") or 0) for b in beats), default=0)
    added = 0
    for b in new_beats or []:
        ch = int(b.get("chapter") or 0)
        text = str(b.get("summary") or b.get("text") or "").strip()[:140]
        if ch > last_ch and text:
            beats.append({"chapter": ch, "text": text})
            last_ch = ch
            added += 1
    cur = int(canon.get("cursor") or 0)
    drop = max(0, cur - CANON_KEEP_CONSUMED)
    if drop:
        canon["beats"] = beats[drop:]
        canon["cursor"] = cur - drop
    return added


def advance_stalled_canon(sim: dict) -> str | None:
    """河道滞留强制前行。返回被推进的动向文本(无则 None)。确定性。"""
    canon = sim.get("canon") or {}
    beats = canon.get("beats") or []
    cur = int(canon.get("cursor") or 0)
    if cur >= len(beats) or int(canon.get("stall") or 0) < CANON_STALL_LIMIT:
        return None
    text = beats[cur]["text"]
    sim.setdefault("facts", []).append("【原著进程】" + text)
    if len(sim["facts"]) > MAX_FACTS:
        sim["facts"] = sim["facts"][-MAX_FACTS:]
    canon["cursor"] = cur + 1
    canon["stall"] = 0
    return text


def decay_threads(sim: dict) -> int:
    """张力衰减(浸泡实锤:LLM 只升不降,张力钉死10→夜律豁免全员=24/7永动侦探)。
    每拍每线 -1(下限1),戏剧压力必须靠持续喂养才能维持。返回衰减条数。确定性。"""
    n = 0
    for t in sim.get("threads") or []:
        cur = int(t.get("tension") or 0)
        if cur > 1:
            t["tension"] = cur - 1
            n += 1
    return n


def enforce_night(sim: dict) -> int:
    """夜间(23:00-06:00)非高张力角色强制睡眠。返回被强制的人数。确定性。"""
    m = int(sim.get("clock_min") or 0) % 1440
    if not (m >= NIGHT_START or m < NIGHT_END):
        return 0
    hot = set()
    for t in sim.get("threads") or []:
        if int(t.get("tension") or 0) >= 8:
            hot.update(t.get("participants") or [])
    n = 0
    for name, c in (sim.get("cast") or {}).items():
        if name in hot or c.get("status") == "昏迷":
            continue
        if "睡" not in str(c.get("activity") or ""):
            c["activity"] = "睡眠"
            n += 1
    return n


# ── 呈现(LLM-B):把已裁决的相遇写成对白 ─────────────────────────────

_DIRECTOR_SYSTEM = """你是场景呈现器:把【已经决定发生的一次相遇】写成一小段对白与纪要。
铁律:
1. 情节已定(参与者/地点/缘由/自然落点都给你了),你只负责演绎,不得加戏、不得引入新情节要素。
2. 只能使用给出的信息;绝不发明新的机构/地点/装置/计划名。
3. 标注为[昏迷]的参与者绝不能说话或行动,只能有微弱生理反应(呼吸/皱眉/梦呓片段)。
4. 对话≤8行,每行≤120字;语气贴合各自设定。
只输出严格 JSON(不要围栏):
{"transcript": [{"speaker": "名字", "line": "台词或动作"}],
 "scene_summary": "≤120字第三人称纪要",
 "private_memories": {"名字": "这个角色私下会记住的一句话(≤60字,可省略)"}}"""


def build_director_prompts(sim: dict, interaction: dict, *, elapsed_hint: str = "") -> tuple[str, str]:
    cast = sim.get("cast") or {}
    lines = []
    for p in interaction.get("participants") or []:
        c = cast.get(p) or {}
        tag = "[昏迷]" if p in (interaction.get("passive") or []) else ""
        lines.append(f"- {p}{tag}:{c.get('sheet','')[:100]};目标:{c.get('goal') or '(无)'};态度:{c.get('stance') or '(平静)'}")
    rel_facts = (sim.get("facts") or [])[-6:]
    user = ("【相遇】地点:" + str(interaction.get("place")) + ";缘由:" + str(interaction.get("reason"))
            + ";自然落点:" + str(interaction.get("expected_outcome")) + "\n【参与者】\n" + "\n".join(lines)
            + "\n【相关事实】\n" + "\n".join("· " + f for f in rel_facts)
            + (f"\n【经过时间】{elapsed_hint}" if elapsed_hint else "")
            + "\n请写出这场相遇。")
    return _DIRECTOR_SYSTEM, user


def validate_director_output(raw_text: str, interaction: dict, sim: dict,
                             *, world_context: str = "") -> dict | None:
    from core.json_parse import parse_llm_json
    data = parse_llm_json(raw_text or "", want=dict)
    if not isinstance(data, dict):
        return None
    ps = set(interaction.get("participants") or [])
    passive = set(interaction.get("passive") or [])
    transcript = []
    for row in (data.get("transcript") or [])[:8]:
        if not isinstance(row, dict):
            continue
        sp = str(row.get("speaker") or "").strip()
        sp = resolve_cast_name(ps, sp) or sp  # 简称/全名互认(别名归并)
        ln = str(row.get("line") or "").strip()[:120]
        if sp not in ps or not ln:
            continue
        if sp in passive and not any(k in ln for k in ("呼吸", "皱眉", "梦呓", "颤", "翕动", "睫毛", "沉睡", "呢喃")):
            continue  # 昏迷者只允许生理反应行
        transcript.append({"speaker": sp, "line": ln})
    summary = str(data.get("scene_summary") or "").strip()[:160]
    if not transcript or not summary:
        return None
    known = _whitelist(sim, world_context) + " " + json.dumps(interaction, ensure_ascii=False)
    fab = find_fabricated_nouns(summary + " " + " ".join(r["line"] for r in transcript), known)
    if fab:
        log.info("[rath.sim] 呈现拒收(新造名词): %s", fab)
        return None
    mems = {}
    for n, v in (data.get("private_memories") or {}).items():
        rn = resolve_cast_name(ps, str(n))
        if rn and str(v or "").strip():
            mems[rn] = str(v).strip()[:60]
    return {"transcript": transcript, "scene_summary": summary, "private_memories": mems}


def absorb_scene(sim: dict, interaction: dict, scene: dict) -> None:
    """呈现结果回写状态:私记入 cast.memory,场景纪要入 facts。纯函数。"""
    cast = sim.get("cast") or {}
    for n, v in (scene.get("private_memories") or {}).items():
        c = cast.get(n)
        if c is not None:
            c.setdefault("memory", []).append(v)
            c["memory"] = c["memory"][-MAX_MEMORY:]
    facts = sim.setdefault("facts", [])
    facts.append(scene.get("scene_summary", "")[:120])
    if len(facts) > MAX_FACTS:
        sim["facts"] = facts[-MAX_FACTS:]
