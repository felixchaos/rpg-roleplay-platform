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
                   *, clock_min: int = 0, canon_beats: list[dict] | None = None,
                   canon_locations: list[str] | None = None) -> dict:
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
            "home": ploc,
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
            "home": ploc,
            "activity": "日常起居",
            "goal": "",
            "stance": "",
            "status": "",
            "memory": [],
        }

    places = [ploc]
    # D2:地点白名单独立源优先(kb_canon_entities type=location,engine.py 按 importance
    # 排名+reveal 闸取来的独立查询结果)——审计实锤:半数已玩剧本世界书标题不含「地点」
    # 类关键词,只靠下面的世界书标题扫描几乎收不到地点(无职转生5条地点条目0条命中)。
    for nm in canon_locations or []:
        nm = str(nm or "").strip()
        if nm and nm not in places:
            places.append(nm)
    # 世界书标题关键词扫描保留作补充(不互斥):部分剧本世界书本就用「地点·XX」命名,
    # 且这条路径不依赖 kb_canon_entities 是否已提取覆盖该剧本。
    for r in wb_rows or []:
        t = str(r.get("title") or "")
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
        # 500k 浸泡实锤:种子线带「她究竟是谁」钩子=奖励 LLM 在不许推进的谜团上
        # 「有进展」(灵魂锚点研究=解释装置新形态),矛盾内建。改纯照料措辞。
        threads.append({
            "id": "t1",
            "desc": f"照料昏迷的{pname}(日常看护;其来历是玩家自己的谜团,世界不得探究)",
            "tension": 3,
            "participants": [n for n in cast if n != pname][:2] + [pname],
        })
    canon = {"cursor": 0, "stall": 0,
             "beats": [{"chapter": int(b.get("chapter") or 0),
                        "text": str(b.get("summary") or "").strip()[:140]}
                       for b in (canon_beats or []) if str(b.get("summary") or "").strip()][:12]}
    return {"clock_min": int(clock_min), "cast": cast, "places": places,
            "facts": facts, "threads": threads, "canon": canon,
            # v3:NPC 关系网(键="甲|乙"排序对,kind=关系词,note=最近变化)——关系变化=戏剧引擎
            "relations": {}}


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
    active = _active_cast_names(sim)  # B11:决策槽位分配器——只给活跃 cast 全量卡片
    stable: list[str] = []
    for n, c in (sim.get("cast") or {}).items():
        if n not in active:
            stable.append(n)
            continue
        tag = "[玩家]" if c.get("kind") == "player" else ""
        st = f"状态:{c['status']};" if c.get("status") else ""
        lines.append(f"- {n}{tag}:在{c.get('location')};正在{c.get('activity')};{st}"
                     f"目标:{c.get('goal') or '(无)'};态度:{c.get('stance') or '(平静)'}")
    if stable:
        lines.append(f"(状态稳定:{'、'.join(stable)})")
    lines.append("已知地点:" + "、".join(sim.get("places") or []))
    rels = sim.get("relations") or {}
    if rels:
        # B7:预览按最近触及(since_seq 降序)排序,超10对时新/热关系不再被挤出窗口
        _top_rels = sorted(rels.items(), key=lambda kv: -int((kv[1] or {}).get("since_seq") or 0))[:10]
        lines.append("人物关系:" + ";".join(
            f"{k.replace('|', '与')}:{(v or {}).get('kind', '')}" for k, v in _top_rels))
    lines.append("剧情线:")
    for t in sim.get("threads") or []:
        _stg = t.get("stage") or "rising"
        _stg_hint = {"seed": "萌芽", "rising": "发展", "climax": "高潮·本拍应有决定性事件",
                     "aftermath": "余波·只收尾不升级"}.get(_stg, _stg)
        lines.append(f"  · [{t['id']}|张力{t['tension']}|{_stg_hint}] {t['desc']}")
    canon = sim.get("canon") or {}
    beats = canon.get("beats") or []
    cur = int(canon.get("cursor") or 0)
    nxt = beats[cur:cur + 2]
    if nxt:
        lines.append("原著河道(这个世界正在/即将发生的主流事件):")
        for b in nxt:
            lines.append(f"  → {b['text']}")
    hist = canon.get("history") or []
    if hist:
        # B8:canon 里程碑独立展示,不与 facts 的 FIFO 滚动共池竞争
        lines.append("原著进程(已发生,里程碑):")
        for h in hist[-3:]:
            lines.append(f"  ✓ {h}")
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
   绝不为其编造来历/身份;其谜团只属于玩家。**绝不让任何角色研究/调查/检测玩家或其
   随身物品,不设立以玩家为对象的研究项目(灵魂锚点、血统鉴定之类);照料是日常,不是调查。**
4. 剧情自然呼吸:可以推进也可以只是生活;推进必须从已确立事实自然长出;
   不必每拍都有相遇(interaction 可为 null)。剧情线有生命周期(萌芽→发展→高潮→余波):
   高潮线本拍应发生决定性事件;余波线只处理后果与情绪,不再引入新冲突。
   人物之间的关系会因相处而变化——值得记录的变化用 relation_updates 给出(≤2条)。
   当现有剧情线均已进入余波或平息、且「已确立事实」里出现新的矛盾苗头时,应给出1条
   new_threads;若所有剧情线张力长期偏低,也应主动构思一条贴近日常生活的新线(不必是
   冲突,邻里琐事/旧友来访皆可),避免世界只剩最初那一条线空转。
5. 有【观测者引导】时,将其翻译成剧情线/目标的调整,让世界朝该方向自然倾斜。
6. 生活优先:角色有自己的本职与日常,调查/异象不得吞噬全部生活;除非某剧情线张力≥7,
   角色应主要处于本职与日常活动。
7. 世界的超常现象只能来自【世界观要点】已有的体系;禁止发明新的超常机制/发光装置/
   现象链;禁止让环境发生灾变级变化;**禁止围绕玩家角色搭建解释其力量的装置或现象**。
8. **原著河道是这个世界的主线**:goal 带「(原著行程)」标记的角色,其行动必须服务于该
   行程,自由支线只能占用其零散时间;角色的本职、谈资、world_events 应与河道交织
   (回声/铺垫/亲历);当河道第一条动向已在世界中发生或正在发生,输出 canon_advance: true。
只输出严格 JSON(不要围栏):
{"cast_updates": {"名字": {"location": "可选", "activity": "可选", "goal": "可选", "stance": "可选"}},
 "interaction": {"participants": ["甲","乙"], "place": "已知地点", "reason": "为何相遇", "expected_outcome": "本场自然收在哪"} 或 null,
 "world_events": ["≤1条,必须直接影响列出的角色或舞台"],
 "thread_updates": [{"id": "t1", "tension_delta": -2到2, "note": "≤40字"}],
 "new_threads": [{"desc": "≤60字", "tension": 1到6, "participants": ["名字"]}],
 "relation_updates": [{"pair": ["甲","乙"], "kind": "≤12字关系词(如 挚友/心存芥蒂/暗生情愫)", "note": "≤40字这段时间关系为何变化"}],
 "new_facts": ["≤2条,只能是本窗口行动的自然结果"],
 "canon_advance": false}"""


def _climax_director_notes(sim: dict) -> list[str]:
    """高潮兑现导演指令(B2,Façade beat 导演范式)。上一拍若某高潮线未获任何覆盖其
    参与者的 interaction,裁决层会记一次「饥饿」计数(见 apply_scheduler_output);
    计数≥1 时下一拍调度 prompt 里显式插入一行确定性指令,逼该线的兑现不再靠随缘。"""
    starve = sim.get("_climax_starve") or {}
    by_id = {t.get("id"): t for t in (sim.get("threads") or [])}
    notes = []
    for tid, cnt in starve.items():
        if int(cnt or 0) < 1:
            continue
        t = by_id.get(tid)
        if t and t.get("stage") == "climax":
            notes.append(f"【导演指令:剧情线「{str(t.get('desc') or '')[:24]}」处于高潮,"
                         f"本拍应演出其参与者的决定性一场】")
    return notes


def build_scheduler_prompts(sim: dict, *, elapsed_hint: str, directive: str = "",
                            world_context: str = "") -> tuple[str, str]:
    director_notes = "\n".join(_climax_director_notes(sim))
    user = (f"【经过时间】{elapsed_hint}\n【世界现状】\n{compact_view(sim)}\n"
            + (f"【世界观要点】\n{world_context}\n" if world_context else "")
            + (f"{director_notes}\n" if director_notes else "")
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


def anchor_cast_to_beat(sim: dict, beat_text: str) -> list[str]:
    """河道前行时把 beat 中出现的原著卡司 goal 锚定为原著行程。确定性。

    500k 浸泡实锤:河道只当背景板→自由悬疑线(水泥厂暗门/撬棍)绑架原著卡司,
    goal 自我强化滚雪球。每次前行代码直接夺回 goal,自由线失去载体。"""
    anchored = []
    text = str(beat_text or "")
    if not text:
        return anchored
    for n, c in (sim.get("cast") or {}).items():
        if c.get("kind") == "player":
            continue
        short = n.split("·")[0]
        if (n in text) or (len(short) >= 2 and short in text):
            c["goal"] = "(原著行程)" + text[:70]
            anchored.append(n)
    return anchored


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


def _near_hit(text: str, pkeys: list[str], words: tuple[str, ...], *, window: int = 15) -> bool:
    """近邻窗口共现判定(B9,浸泡实锤误伤修复:探究闸/装置闸旧判定=玩家名与词表任意
    字符串共现即触发一整句,常见词高误伤——「薇欧拉调查账目」同句提到玩家即被整体拒收。
    改判:玩家名(或简称)须出现在文本中,且触发词与其中某次出现的词距≤window 字内,
    或该词后紧跟属格(「的来历/的身世/……」,覆盖代词间接指代、玩家名与触发词隔句的情形)。
    宁漏勿误:既有真阳性用例的短句距离天然 ≤window,不受影响。"""
    if not text or not pkeys:
        return False
    p_positions: list[int] = []
    for p in pkeys:
        start = 0
        while True:
            i = text.find(p, start)
            if i < 0:
                break
            p_positions.append(i)
            start = i + 1
    if not p_positions:
        return False
    for w in words:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            if re.match(r"的(来历|身世|秘密|血统|真相|过去)", text[i + len(w): i + len(w) + 6]):
                return True
            if any(abs(i - p) <= window for p in p_positions):
                return True
            start = i + 1
    return False


def _dup_fact(text: str, existing: list[str], *, prefix: int = 10) -> bool:
    """facts 入队前重复检查(B8,浸泡实锤:纯 FIFO 无查重,近义重复文本挤占窗口)。
    简单前缀/包含判定(不上语义相似度,保持零 IO 确定性):文本前 prefix 字互为前缀,
    或整段互相包含,视为重复。宁可漏放一条相近表述,不做重语义判断。"""
    t = (text or "").strip()
    if not t:
        return True
    for e in existing or []:
        ee = (e or "").strip()
        if not ee:
            continue
        if t in ee or ee in t:
            return True
        if t[:prefix] and ee[:prefix] and (t.startswith(ee[:prefix]) or ee.startswith(t[:prefix])):
            return True
    return False


def _push_canon_history(sim: dict, text: str) -> None:
    """canon 里程碑独立保存(B8):前行动向另存 canon.history(上限24),独立于 facts
    的 FIFO 滚动截断——避免场景纪要把「原著进程」里程碑挤出 compact_view 窗口。"""
    canon = sim.setdefault("canon", {})
    hist = canon.setdefault("history", [])
    hist.append(str(text or "")[:140])
    if len(hist) > 24:
        del hist[:-24]


def _active_cast_names(sim: dict) -> set[str]:
    """决策槽位分配器(B11:AI Metropolis 几何依赖调度 + Affordable GA 缓存范式的轻量
    移植)。只有「活跃」cast 才值得占用调度 prompt 的全量卡片槽位:近3拍内
    location/goal/activity 变过、或在场于任一现存(非平息)剧情线、或是上一拍相遇的
    参与者、或是玩家本人。其余压成一行名单——schema/裁决不变,LLM 仍可对名单里任何人
    下 cast_updates,只是 prompt 呈现更省 token、少漂移。"""
    seq = int(sim.get("tick_seq") or 0)
    active: set[str] = set()
    for n, c in (sim.get("cast") or {}).items():
        if c.get("kind") == "player":
            active.add(n)
            continue
        lc = c.get("last_changed_seq")
        # 缺失=未知(新实验首拍/旧实验升级后首拍)——未知视为活跃,冷启动给全量卡片,
        # 裁决层随 cast_updates 打上 seq 后自然收敛到「近3拍变过才活跃」。
        if lc is None or seq - int(lc) <= 3:
            active.add(n)
    for t in sim.get("threads") or []:
        active.update(t.get("participants") or [])
    active.update(sim.get("_last_interaction_participants") or [])
    return active


def apply_scheduler_output(sim: dict, data: dict, *, world_context: str = "") -> dict:
    """裁决层(确定性单写者):逐字段验收 → 落 sim_state。返回 {applied:…, rejected:[原因]}。
    玩家状态守恒与名词闸在这里代码强制。纯函数(原地改 sim)。"""
    rejected: list[str] = []
    applied = {"cast": 0, "events": [], "interaction": None, "threads": 0, "facts": 0}
    cast = sim.setdefault("cast", {})
    sim["tick_seq"] = int(sim.get("tick_seq") or 0) + 1  # 拍序(僵尸线判定的时钟)

    _pname = next((n for n, c in cast.items() if c.get("kind") == "player"), "")
    # 全名+首段简称都算提及玩家(「菲莉丝·卡俄斯」→「菲莉丝」,防简称绕过装置闸)
    _pkeys = [x for x in {_pname, _pname.split("·")[0] if _pname else ""} if len(x) >= 2]
    _APPARATUS = ("共振", "能量转移", "能量核心", "激活条件", "装置")
    # 探究闸(浸泡实锤:「灵魂锚点研究」=解释玩家来历的装置新形态,词表打地鼠打不完;
    # 根因=任何以玩家为对象的探究项目都在滑向解释。玩家名+探究词近邻共现一律拒收,宁严勿松)
    _PROBE = ("锚点", "锚定", "来历", "身世", "血统", "研究", "调查", "检测", "鉴定", "解析", "档案")
    # 夜间外出闸(浸泡实锤:夜律拍首拉回家,同拍调度又把人派去水泥厂搜查=夜归被覆盖。
    # 夜间低张力角色拒收「离家」位置变更;回家方向放行;高张力/高潮线参与者豁免)
    _m = int(sim.get("clock_min") or 0) % 1440
    _night = _m >= NIGHT_START or _m < NIGHT_END
    _hot = {p for t in (sim.get("threads") or [])
            if int(t.get("tension") or 0) >= 8 or t.get("stage") == "climax"
            for p in (t.get("participants") or [])}
    # B2 前置:上一拍持续到本拍的高潮线「饥饿」计数(见函数尾部 threads 段的收尾记账)
    _climax_starve_prev = dict(sim.get("_climax_starve") or {})

    def _gate(text: str, what: str) -> bool:
        t = str(text or "")
        # B5:惰性求值——每次调用当场重拼白名单,防同拍内本已被接受的新地点/事实
        # 因用函数开头那份旧快照判定而被"自我拒收"(单次裁决内的白名单过期)
        known = _whitelist(sim, world_context)
        fab = find_fabricated_nouns(t, known)
        if fab:
            rejected.append(f"{what}:新造名词{'/'.join(fab[:2])}")
            return False
        # 玩家设定神圣旁路闸(浸泡实锤:铭牌共振玩家头顶能量=给玩家力量建解释装置)
        # B9:近邻窗口判定替代整句字符串共现,降低"薇欧拉调查账目"式的高频词误伤
        if _near_hit(t, _pkeys, _APPARATUS):
            rejected.append(f"{what}:围绕玩家搭建解释装置")
            return False
        if _near_hit(t, _pkeys, _PROBE):
            rejected.append(f"{what}:以玩家为对象的探究(神圣条款)")
            return False
        return True

    def _feed_tension(participants: list[str]) -> None:
        """B1:张力喂养对冲源(世界侧事件对张力零反馈=玩家缺席线卡seed恒0)。canon
        前行/相遇成立均是"世界在动"的确定性信号,对涉及的现存线 +1(封顶8,低于衰减前
        的自由升压上限,避免自动喂养单独把线顶进/顶穿高潮)。余波/平息线不再喂养。"""
        pset = set(participants or [])
        if not pset:
            return
        for t in sim.get("threads") or []:
            if t.get("stage") in ("aftermath", "settled"):
                continue
            if pset & set(t.get("participants") or []):
                t["tension"] = min(8, int(t.get("tension") or 0) + 1)

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
                _home = str(c.get("home") or "").strip()
                if (_night and rk not in _hot and _home and _home != "未知地点"
                        and v != _home and str(c.get("location") or "") == _home):
                    rejected.append(f"{rk}:夜间不外出(低张力)")
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
            if k in ("location", "goal", "activity"):
                # B11:决策槽位分配器的活跃度时间戳——近3拍内变过即视为"活跃"
                c["last_changed_seq"] = sim["tick_seq"]

    # B2 前置:高潮线连续≥2拍无覆盖(饥饿计数≥2)且 top-2 参与者本拍同地点 →
    # 裁决层直接补设 interaction(不同地点则什么都不做,只留给下一拍的 prompt 指令,
    # 不硬造移动)。只在本拍模型自己没给出覆盖该线的 interaction 时插入,不抢别的相遇。
    for _t in (sim.get("threads") or []):
        if _t.get("stage") != "climax" or int(_climax_starve_prev.get(_t.get("id")) or 0) < 2:
            continue
        _tp = [p for p in (_t.get("participants") or []) if p in cast][:2]
        _cur_it = data.get("interaction") if isinstance(data.get("interaction"), dict) else None
        _cur_ps = set()
        if _cur_it:
            for x in (_cur_it.get("participants") or []):
                rp = resolve_cast_name(cast, str(x))
                if rp:
                    _cur_ps.add(rp)
        if _cur_ps & set(_tp):
            continue  # 已被本拍模型自己给出的 interaction 覆盖
        if len(_tp) == 2:
            _loc0 = str(cast[_tp[0]].get("location") or "").strip()
            _loc1 = str(cast[_tp[1]].get("location") or "").strip()
            if _loc0 and _loc0 == _loc1:
                data = dict(data or {})
                data["interaction"] = {
                    "participants": _tp, "place": _loc0,
                    "reason": f"剧情线「{str(_t.get('desc') or '')[:20]}」到了必须兑现的关口",
                    "expected_outcome": "给出决定性的进展或转折",
                }
                break  # 每拍只强插一场

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
        _homes = {str(cast[p].get("home") or "").strip() for p in ps if p in cast}
        # B4:夜间外出闸补齐——之前只挡 cast_updates.location,同拍 interaction.place
        # 却能绕过去演出深夜外出场景,自相矛盾。非 hot 双人+夜间+地点非双方家一律拒收,
        # 降级为一条中性事实(参照 canon_advance 失败路径"只记录不生成"的写法)。
        _night_violation = (len(ps) == 2 and _night and place
                            and not any(p in _hot for p in ps) and place not in _homes)
        if _night_violation:
            rejected.append(f"interaction:夜间外出(非双方home){place}")
            _fb = f"{ps[0]}与{ps[1]}本约在{place}相见,因夜深作罢,各自留宿"[:100]
            if _gate(_fb, "interaction_night_fallback"):
                sim.setdefault("facts", []).append(_fb)
        elif len(ps) == 2 and (place in (sim.get("places") or []) or _gate(place, "相遇地点")):
            # 昏迷玩家可以是场景的对象(被照料/被谈论),不作为主动方——呈现层约束
            applied["interaction"] = {
                "participants": ps, "place": place or cast[ps[0]].get("location") or "",
                "reason": str(it.get("reason") or "")[:80],
                "expected_outcome": str(it.get("expected_outcome") or "")[:80],
                "passive": unconscious_active,
            }
            _feed_tension(ps)  # B1:相遇成立=世界侧对冲信号,喂养涉及的现存线
        elif it:
            rejected.append("interaction:参与者/地点未过验收")
    # B11:上一拍相遇参与者(决策槽位分配器读取),每拍覆盖式记录(无相遇则清空,不留陈迹)
    sim["_last_interaction_participants"] = (
        list(applied["interaction"]["participants"]) if applied.get("interaction") else [])

    # B2 记账:本拍(拍首已判定的)高潮线是否被覆盖——覆盖则清零,否则饥饿计数+1。
    # 最终是否保留写回 sim,在下方 stage 生命周期循环算出本拍最终 stage 后再过滤
    # (线若本拍恰好转入 aftermath,不再需要"高潮兑现"提示,计数随之清除)。
    _climax_new_starve: dict = {}
    _applied_it_ps = set((applied.get("interaction") or {}).get("participants") or [])
    for _t in (sim.get("threads") or []):
        if _t.get("stage") != "climax":
            continue
        _tid = _t.get("id")
        _covered = bool(_applied_it_ps & set(_t.get("participants") or []))
        _climax_new_starve[_tid] = 0 if _covered else int(_climax_starve_prev.get(_tid) or 0) + 1

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
        t["last_touch"] = sim["tick_seq"]
        note = str(tu.get("note") or "").strip()
        if note and _gate(note, "thread.note"):
            t["desc"] = (t["desc"].split("——")[0] + "——" + note)[:120]
        applied["threads"] += 1
    # v3 长弧:stage 由裁决层按张力确定性推进(不信 LLM 直接设),线有形状而非数字浮动。
    # seed→rising(张力≥4)→climax(≥8)→aftermath(climax 停留≥2拍自动泄压)→平息(close_stale)。
    for t in threads:
        stg = t.get("stage") or ("seed" if int(t.get("tension") or 0) <= 3 else "rising")
        tn = int(t.get("tension") or 0)
        if stg == "seed" and tn >= 4:
            stg = "rising"
        if stg == "rising" and tn >= 8:
            stg = "climax"
            t["stage_seq"] = sim["tick_seq"]
        elif stg == "climax" and sim["tick_seq"] - int(t.get("stage_seq") or sim["tick_seq"]) >= 2:
            stg = "aftermath"
            t["tension"] = min(tn, 3)  # 高潮已过,确定性泄压
        t["stage"] = stg
        hist = t.setdefault("tension_hist", [])
        hist.append(int(t.get("tension") or 0))
        if len(hist) > 12:
            del hist[:-12]
    # B2 记账落定:只保留本拍(生命周期推进后)最终仍是 climax 的线的饥饿计数——
    # 若本拍恰好转入 aftermath,不再需要"高潮兑现"提示,计数随 stage 变化一并清除。
    sim["_climax_starve"] = {tid: cnt for tid, cnt in _climax_new_starve.items()
                             if by_id.get(tid, {}).get("stage") == "climax"}
    for nt in (data.get("new_threads") or [])[:1]:
        if len(threads) >= MAX_THREADS:
            break
        desc = str((nt or {}).get("desc") or "").strip()
        if desc and _gate(desc, "new_thread"):
            threads.append({
                "id": f"t{len(threads) + 1}",
                "desc": desc[:80],
                "last_touch": sim["tick_seq"],
                "stage": "seed",
                "tension": max(1, min(4, int((nt or {}).get("tension") or 3))),  # seed 期钳 ≤4
                "participants": list(dict.fromkeys(
                    rp for rp in (resolve_cast_name(cast, p)
                                  for p in ((nt or {}).get("participants") or [])) if rp))[:3],
            })
            applied["threads"] += 1

    # v3 关系网:relation_updates 验收——双方经别名归并后 ∈cast 且不同人;kind/note 过
    # 名词闸+神圣条款闸(涉玩家的关系合法如「守护」,但探究/装置措辞照拒)。无向对键排序。
    relations = sim.setdefault("relations", {})
    for ru in (data.get("relation_updates") or [])[:2]:
        if not isinstance(ru, dict):
            continue
        pair = [resolve_cast_name(cast, str(x)) for x in (ru.get("pair") or [])[:2]]
        if len(pair) != 2 or not all(pair) or pair[0] == pair[1]:
            rejected.append("relation:成员未识别")
            continue
        kind = str(ru.get("kind") or "").strip()[:12]
        note = str(ru.get("note") or "").strip()[:40]
        # 闸文本带上双方名字:探究闸靠「玩家名+探究词共现」判定,kind/note 本身无名字
        if not kind or not _gate(" ".join(pair) + " " + kind + " " + note, "relation"):
            continue
        key = "|".join(sorted(pair))
        relations[key] = {"kind": kind, "note": note, "since_seq": sim["tick_seq"]}
        applied["relations"] = applied.get("relations", 0) + 1
    if len(relations) > 24:  # 封顶:最老的先淘汰
        for k in sorted(relations, key=lambda x: int(relations[x].get("since_seq") or 0))[:len(relations) - 24]:
            relations.pop(k, None)

    # facts(≤2,滚动封顶)
    facts = sim.setdefault("facts", [])
    for f in (data.get("new_facts") or [])[:2]:
        t = str(f or "").strip()
        # B8:入队前查重(简单前缀/包含判定)——近义重复文本不再加速把真正的新信息挤出
        # compact_view 最近8条窗口
        if t and _gate(t, "new_fact") and not _dup_fact(t, facts):
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
        _push_canon_history(sim, beats[cur]["text"])  # B8:里程碑独立保存,不受 facts FIFO 挤出
        canon["cursor"] = cur + 1
        canon["stall"] = 0
        applied["canon_advance"] = True
        applied["canon_text"] = beats[cur]["text"]
        applied["canon_anchored"] = anchor_cast_to_beat(sim, beats[cur]["text"])
        _feed_tension(applied["canon_anchored"])  # B1:河道前行=世界侧对冲信号,喂养涉及的现存线
        # v4.1(storylets 补完,269 薄剧本浸泡实锤):没有昏迷种子线的剧本 threads 恒空,
        # 世界只有河道没有叙事弧。河道前行且无任何活跃线时,从本 beat 确定性孵化一条
        # (beat 即 storylet:precondition=游标到达,effect=生弧);participants 取锚定卡司。
        _threads = sim.setdefault("threads", [])
        _alive = [t for t in _threads if (t.get("stage") or "rising") != "aftermath"]
        if not _alive and len(_threads) < MAX_THREADS:
            _ids = {str(t.get("id")) for t in _threads}
            _n = 1
            while f"t{_n}" in _ids:
                _n += 1
            _parts = list(applied["canon_anchored"] or [])[:3]
            if not _parts:
                _parts = [nm for nm, cc in (sim.get("cast") or {}).items()
                          if cc.get("kind") != "player"][:2]
            _threads.append({"id": f"t{_n}",
                             "desc": ("(原著动向)" + str(applied["canon_text"]))[:80],
                             "tension": 3, "stage": "seed", "tension_hist": [3],
                             "participants": _parts})
            applied["thread_born_from_beat"] = f"t{_n}"
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
    _push_canon_history(sim, text)  # B8:强制前行同样是里程碑,独立保存不受 facts FIFO 挤出
    canon["cursor"] = cur + 1
    canon["stall"] = 0
    anchor_cast_to_beat(sim, text)
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


STALE_THREAD_TICKS = 8  # 张力≤1 的线连续 N 拍无触及即平息


def close_stale_threads(sim: dict) -> list[str]:
    """僵尸剧情线自动平息(浸泡实锤:伊萨尔河线张力0仍永久挂账,随时等 LLM 复活)。
    张力≤1 且连续 STALE_THREAD_TICKS 拍未被 thread_updates 触及 → 移入 facts(已平息)。
    确定性。返回被平息的线描述。"""
    seq = int(sim.get("tick_seq") or 0)
    keep, closed = [], []
    for t in sim.get("threads") or []:
        lt = t.get("last_touch")
        if lt is None:
            t["last_touch"] = seq  # 老档首见即记,从现在起计时
            keep.append(t)
            continue
        if int(t.get("tension") or 0) <= 1 and seq - int(lt) >= STALE_THREAD_TICKS:
            closed.append(str(t.get("desc") or "")[:40])
            sim.setdefault("facts", []).append("(已平息)" + str(t.get("desc") or "")[:60])
            continue
        keep.append(t)
    if closed:
        sim["threads"] = keep
        if len(sim.get("facts") or []) > MAX_FACTS:
            sim["facts"] = sim["facts"][-MAX_FACTS:]
    return closed


def enforce_night(sim: dict) -> int:
    """夜间(23:00-06:00)非高张力角色强制睡眠。返回被强制的人数。确定性。"""
    m = int(sim.get("clock_min") or 0) % 1440
    if not (m >= NIGHT_START or m < NIGHT_END):
        return 0
    hot = set()
    for t in sim.get("threads") or []:
        if int(t.get("tension") or 0) >= 8 or (t.get("stage") == "climax"):
            hot.update(t.get("participants") or [])
    # B3:夜律认原著行程——goal 带「(原著行程)」标记的角色(anchor_cast_to_beat 写入)
    # 不受夜律管辖,否则刚被锚定去异地过夜的原著卡司会被强制拉回家,与河道主线自相矛盾。
    hot.update(n for n, c in (sim.get("cast") or {}).items()
               if str(c.get("goal") or "").startswith("(原著行程)"))
    n = 0
    for name, c in (sim.get("cast") or {}).items():
        if name in hot or c.get("status") == "昏迷":
            continue
        if "睡" not in str(c.get("activity") or ""):
            c["activity"] = "睡眠"
            # 夜归(浸泡实锤:自由调查线滚雪球→留学生夜宿水泥厂石灰窑深坑=行为脱设定,
            # 名词闸挡不住这种歪法)。被强制入睡者拉回住所树立日常重力;
            # LLM 主动安排的外宿(activity 已是睡眠,如出差宿旅馆)尊重不动。
            home = str(c.get("home") or "").strip()
            if home and str(c.get("location") or "") != home:
                c["location"] = home
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
    # v3:两人现有关系喂给呈现层——关系是对白语气的地基(挚友与心存芥蒂说话不一样)
    ps2 = [str(x) for x in (interaction.get("participants") or [])[:2]]
    _rel_line = ""
    if len(ps2) == 2:
        _rv = (sim.get("relations") or {}).get("|".join(sorted(ps2)))
        if _rv:
            _rel_line = f"\n【两人关系】{_rv.get('kind', '')}" + (f"(近况:{_rv.get('note', '')})" if _rv.get("note") else "")
    user = ("【相遇】地点:" + str(interaction.get("place")) + ";缘由:" + str(interaction.get("reason"))
            + ";自然落点:" + str(interaction.get("expected_outcome")) + _rel_line + "\n【参与者】\n" + "\n".join(lines)
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
