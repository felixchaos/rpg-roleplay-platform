"""
chat_tool_router.py — task 87 Phase 5: 统一工具路由 (GM tool_use)

GM 流式响应中调用工具时,需要识别:
  · dispatcher 工具 (server_id="" 或 magic "__dispatcher__"): 走 ToolDispatcher
  · MCP 工具 (server_id 是真实 server): 走 mcp_broker.call_tool

unified router 在 chat handler 内构造,带上当前 user_id / save_id / trace_id 上下文。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tools_dsl.command_dispatcher import (
    ToolCallEnvelope,
    ToolDispatcher,
    get_registry,
)

# task 87 Phase 5: sentinel 必须不含 "__" (backend 用作 server_id__tool_name 分隔符),
# 否则 backend 把 full_name 拆错,server_id 解析失败 → router 回退到 mcp_broker 调用失败。
DISPATCHER_SENTINEL = "dispatcher"


# 酒馆 = 基于 harness 的完整 agent(用户决策):**允许「改写只读剧本 canon」以外的所有操作**。
# 不再像旧设计那样砍掉战斗/物品/模组/锚点/时间线 —— 那些写的是本存档自身状态,是合法的
# 「世界随对话推进写入 DB」的一部分。只有两类按需丢弃:
#   · canon 写(kb_*)= 改世界树 KB:绑定只读剧本时禁(不许改原著);无剧本时也无对象 → 丢。
#   · canon 读(search_canon 等):无绑定剧本时没有原著可读 → 丢;绑定后放开(贴合原著)。
_TAVERN_CANON_WRITE_SUBSTR = ("kb_",)
_TAVERN_CANON_READ_SUBSTR = (
    "search_canon", "lookup_entity", "lookup_timeline", "graph_neighbors",
    "get_chapter_facts", "get_worldbook",
)

# 酒馆自举工具(建/换角色、persona、列/绑剧本)的前缀 —— **单一真相源**。
# v1.82.0:这个元组此前在本文件里有三份拷贝(_TAVERN_KEEP_PREFIX / _rank 的字面量 /
# build_unified_tool_list 里 GM 模式的丢弃分支),补 switch_tavern_ 时漏改其中两处,
# 结果 switch_tavern_persona_card 在 GM 模式拿到 tier -1、占掉窗口第一个名额。
# 三处合一,以后加前缀只改这里。
TAVERN_SELF_PREFIXES = ("set_tavern_", "edit_tavern_", "tavern_", "switch_tavern_")

# 别名:保留旧名给既有 import(语义相同 —— 这批工具永不被子串规则误伤)。
# tavern_list_scripts / tavern_bind_script 含 "script" 子串,否则可能被规则吞掉。
_TAVERN_KEEP_PREFIX = TAVERN_SELF_PREFIXES


# ── 锚点/存档历史族(时间线的读写全集)——**提权名单的单一真源** ──────────────
# 工具名前缀天然分不到一起:record_*/check_* 落 _rank 的兜底档 3、list_* 落档 1,
# 而窗口只有 16 → 落档 3 的成员拿不到 schema,GM 只能在 tiered 目录里看见一行简介,
# 实际从不 load。前科两次,同一个族被分两批发现:
#   ① mark_anchor_satisfied / mark_anchor_superseded 落档 3 → 剧情锚点「推不动」;
#   ② 本条(v1.72.4,群反馈「50 多章从来没创造过玩家锚点」):record_history_anchor
#      在 100 个工具里排 89、list_recent_history 排 39,双双在窗口外 → 存档独立时间线
#      这一侧**写不进也查不到**。生产实证:全站 gm_generated 锚点仅 10 条 / 3 档,
#      877 回合的 save 268 一条没有,玩家看到的两条全是 phase digest 自动写的 system 档。
# 修 ① 时只把「未来侧」四个名字塞进 _rank 的字面量元组,漏了「过去侧」——典型修 A 漏 B。
# 收成一份名单:以后这个族新增工具,只改这里一处;成员资格由 tools_dsl 的注册名兜底
# (奇偶守卫见 rpg/tests/unit/test_anchor_tool_window.py)。
# 两侧必须分开(v1.82.0):相关性门只能门控**剧本未来侧** —— 「这个剧本有没有待收束的
# 锚点」是剧本侧事实。**存档过去侧**(玩家自己的历史锚点)与剧本无关,任何一局都可能要写,
# 门控它就会重演 v1.72.4 那个 bug:877 回合的存档一条玩家锚点都没有,因为工具够不到。
_ANCHOR_SCRIPT_SIDE = frozenset((
    "list_pending_anchors", "mark_anchor_satisfied", "mark_anchor_superseded",
    "summarize_anchors", "check_pending_anchor_drift",
))
_ANCHOR_HISTORY_SIDE = frozenset((
    "record_history_anchor", "list_recent_history",
))
_ANCHOR_FAMILY = _ANCHOR_SCRIPT_SIDE | _ANCHOR_HISTORY_SIDE
# 族内**必须常驻直发窗口**的子集 = 主回合闭环真正会用到的读写。
# check_pending_anchor_drift 是排查用的反查器(GM 主循环走 list_pending_anchors +
# mark_*),不占窗口名额;它仍在族里,受同一份奇偶守卫覆盖。
_ANCHOR_WINDOW_PROMOTED = _ANCHOR_FAMILY - {"check_pending_anchor_drift"}


# ── 直发窗口的档位(v1.82.0)──────────────────────────────────────────────
# 在此之前档位是「按名字前缀猜」,匹配不到就落兜底档 3 —— 而窗口是硬名额,落兜底
# 等于这个工具不存在。前科两次(锚点族分两批发现),两次都是几个月后靠群反馈找出来的。
#
# 这一版把三件事改掉:
#   ① 档位规则变成**显式声明表**(_TIER_RULES),不再是散在 if 里的前缀链;
#   ② 匹配不到任何规则的工具进 UNCLASSIFIED,由守卫测试
#      rpg/tests/unit/test_tool_window_tiering.py 断言其为空 —— 新工具不选档就红,
#      不再有「静默落兜底」这条路;
#   ③ 平台/账户管理类工具单列一档(PLATFORM),它们在 GM 主回合毫无用处,却因为
#      同档内 tie-break 是**字母序**而实际占着窗口名额。实测(104 个 llm_chat 工具,
#      窗口 18):get_import_status / get_my_stats / get_my_usage 三个平台查询挤在
#      窗口里,而 query_memory / get_worldbook / get_pending_questions 全在窗外。
TIER_TAVERN_SELF = -1     # 酒馆自举:建/换角色、persona、列/绑剧本
TIER_TURN_CRITICAL = 0    # 主回合闭环必需:锚点族、canon 读写、面向玩家交互
TIER_PRIMARY_READ = 1     # 主回合首要读:这个存档**此刻**的状态(记忆/世界书/关系/原著窗口)
TIER_TURN_READ = 2        # 主回合次要读:其余 get_/list_/query_
TIER_SECONDARY_READ = 3   # 编辑域枚举:拿 id 用的清单,不是主回合读
TIER_TURN_WRITE = 4       # 主回合写入:改 state
TIER_SITUATIONAL = 5      # 情境专用:战斗 / 物品 / 生图 / 建卡 / 导入 / UI
TIER_PLATFORM = 6         # 平台账户管理:用量 / 凭据 / 存档管理 / 审计 —— 永不占窗口
TIER_UNCLASSIFIED = 9     # 没匹配到任何规则 —— 守卫测试要求这一档恒为空


def _curated_turn_critical() -> frozenset[str]:
    """从 gm_serving 那份**人工审定过的**清单派生主回合必需集,不手抄名字。

    `GM_ALL_KB_TOOLS` 的定义就是「文宗精简档把工具收成 12 个之后**仍然必须保留**的
    存档级 KB 维护工具」—— 那是一份现成的、与窗口无关的「主回合真正需要什么」权威声明。

    v1.82.0 只把 ask_player_choice 从这个不对称里救了出来(精简档保它、非精简档却够不到
    窗口),**没有回头查同一份清单里的其他成员** —— 典型的修 A 漏 B,而且是我自己犯的。
    实测:清单 11 个里 7 个已在 tier 0(canon 读 4 + 锚点 3),剩下 4 个 kb_* 写工具落
    tier 1,按字母序排在 38 个 get_* 之后,在非精简档从来进不了 18 的窗口 —— 也就是
    「GM 不维护存档级 KB」的一条确定性成因。

    改成结构派生:以后往 GM_ALL_KB_TOOLS 加工具,窗口自动跟着变(effective_window 会
    按不变量定容),不需要有人记得来这里补名字。
    """
    try:
        from gm_serving.serve import GM_ALL_KB_TOOLS
        return frozenset(GM_ALL_KB_TOOLS)
    except Exception:  # gm_serving 不可用(裁剪部署 / 单测替身)时退回空集,不影响其余规则
        return frozenset()

# 主回合闭环必需的具名工具(前缀分不到一起,只能列名)。
_TURN_CRITICAL_NAMES = frozenset((
    # 面向玩家的选择题。gm.py 的文宗精简档已经显式保它(注释原话:「否则 slim 档 GM
    # 无法弹玩家选择(用户报"选项有时不弹"的根因之一)」)—— 但**非 slim 档**它一直落在
    # 兜底档、进不了 18 的窗口,同一个根因在另一半路径上没被修。这里补齐。
    "ask_player_choice",
    # 当前场景与状态:GM 每轮都要知道「此刻在哪、什么状态」
    "get_current_scene", "get_game_state", "get_pending_questions",
)) | _curated_turn_critical()

# 平台/账户管理:与本回合叙事无关,不该竞争窗口名额。窗口外仍可经 load_tools 目录取用。
_PLATFORM_NAMES = frozenset((
    "get_my_stats", "get_my_usage", "list_my_usage",
    "get_import_status", "list_my_import_jobs",
    "list_my_credentials_meta", "list_available_models", "list_available_tools",
    "recent_audit_log",
    "list_my_saves", "get_save_detail", "list_branches",
))

# 情境专用:只在特定玩法/操作里用得上,平时不占窗口。
_SITUATIONAL_PREFIXES = (
    "combat_", "skill_check", "saving_throw", "short_rest",
    "consume_item", "grant_item", "pickup_loot",
    "generate_image", "create_character_card", "create_persona",
    "clone_npc_", "export_character_card", "import_",
    "module_", "extract_from_selection", "read_attached_text",
    "claim_protagonist_pov", "revoke_protagonist_pov", "recommend_player_identity",
    "schedule_consequence", "ui_", "phase_list",
    "check_pending_anchor_drift",
    "list_my_character_cards", "list_my_personas", "list_modules",
)

# 编辑域枚举:这些工具**按它们自己的描述**就是「更新前先用它拿 id」的清单
# (list_anchors → 拿 anchor_id 给 update_anchor;list_canon_entities → 拿 logical_key
# 给 upsert_canon_entity),属剧本编辑工作流而不是主回合叙事。放次要档,给主回合读腾名额。
# ⚠️ 刻意不含 list_script_npcs / get_script_character_card —— 那两个是「这一幕谁在场」,
# 主回合真的会用。
_SECONDARY_READ_NAMES = frozenset((
    "list_anchors", "list_canon_entities", "list_scripts", "get_script_chapters",
))

# 主回合**首要**读 —— 判据是「本回合是关于这个存档此刻的状态」,不是调用频次。
# (频次数据不可用作判据:窗口外的工具模型根本看不见、自然也不会调,统计天然有幸存者偏差;
#  kb_* 那 68 次调用实际来自文宗精简档的白名单,与窗口构成无关。)
# 这一档与 tier 0 一起进 effective_window 的定容不变量,所以它们不再靠字母序抢名额 ——
# v1.81 里 get_import_status / get_my_usage 压过 query_memory / get_worldbook,就是字母序。
_PRIMARY_TURN_READS = frozenset((
    "query_memory",          # 记忆检索
    "get_worldbook",         # 世界书(设定权威)
    "list_relationships",    # 关系网
    "get_known_events",      # 已发生事件
    "get_pending_writes",    # 上轮待确认的写
    "get_chapter_context",   # 贴原著档每回合都要
    "get_chapter_facts",
))

# 主回合读 / 写的前缀规则(顺序敏感:读在写前,因为 kb_ 既是读也是写的前缀)。
_TURN_READ_PREFIXES = ("kb_", "get_", "list_", "query_")
_TURN_WRITE_PREFIXES = ("set_", "add_", "pin_", "clarify", "confirm_",
                        "reject_", "dismiss_", "save_", "worldbook_")


def classify_tool(name: str, *, signals: frozenset[str] | None = None) -> int:
    """把工具名归到一个显式档位。返回 TIER_* 之一。

    signals 是**本回合**的相关性信号集合(见 build_unified_tool_list 的 signals 参数)。
    传 None = 不做相关性门控,与 v1.81 行为一致。

    相关性门只做**降权**,不做提权,而且只降两族:
      · 锚点族的**剧本未来侧**:没有 pending anchor 时从 TURN_CRITICAL 降到 TURN_READ。
        今天这 4 个工具无条件占着窗口名额,哪怕这局根本没有待收束的锚点。
        **存档过去侧(record_history_anchor / list_recent_history)不受门控** —— 见
        _ANCHOR_HISTORY_SIDE 的注释。
      · canon 读:没有绑定剧本时降级 —— 没有原著可读。
    有信号时行为与改动前完全一致(仍在 TURN_CRITICAL),所以「有用的时候一个不少」。
    """
    n = (name or "").lower()
    if n.startswith(TAVERN_SELF_PREFIXES):
        return TIER_TAVERN_SELF
    if n in _PLATFORM_NAMES:
        return TIER_PLATFORM
    # ⚠️ 受相关性门控的两族(锚点剧本侧 / canon 读)必须排在 _TURN_CRITICAL_NAMES **之前**。
    # _TURN_CRITICAL_NAMES 含 gm_serving 那份审定清单,而清单里的 canon 读(search_canon /
    # lookup_* / graph_neighbors)也是 GM_KB_QUERY_TOOLS 成员 —— 放在前面会让它们绕过门,
    # 于是「没绑剧本、根本没有原著可读」时 canon 读仍占着窗口名额。
    # 两个权威在这里打架:清单说的是「有剧本时主回合要用」,门说的是「压根没东西可读」,门赢。
    # 清单真正额外贡献的是那 4 个 kb_* 写工具,它们不受任何门控,在下面被接住。
    if n in _ANCHOR_WINDOW_PROMOTED:
        # 存档过去侧无条件常驻:写自己的历史锚点跟剧本有没有 pending anchor 无关。
        if n in _ANCHOR_HISTORY_SIDE:
            return TIER_TURN_CRITICAL
        if signals is not None and "anchors" not in signals:
            return TIER_TURN_READ
        return TIER_TURN_CRITICAL
    if n.startswith(("search_canon", "lookup_", "graph_neighbors")):
        if signals is not None and "canon" not in signals:
            return TIER_TURN_READ
        return TIER_TURN_CRITICAL
    if n in _TURN_CRITICAL_NAMES:
        return TIER_TURN_CRITICAL
    if n in _SECONDARY_READ_NAMES:
        return TIER_SECONDARY_READ
    if n in _PRIMARY_TURN_READS:
        return TIER_PRIMARY_READ
    if n.startswith(_SITUATIONAL_PREFIXES):
        return TIER_SITUATIONAL
    if n.startswith(_TURN_READ_PREFIXES):
        return TIER_TURN_READ
    if n.startswith(_TURN_WRITE_PREFIXES):
        return TIER_TURN_WRITE
    return TIER_UNCLASSIFIED


def unclassified_tools(names) -> list[str]:
    """匹配不到任何显式规则的工具名。守卫测试断言它恒为空。

    这是「miss 必须可诊断」的落点:新工具不选档就在 CI 里红,而不是上线几个月后
    由玩家报「这个功能 GM 从来不用」。
    """
    return sorted(n for n in names if classify_tool(n) == TIER_UNCLASSIFIED)


def turn_signals(*, bound_script_id: int | None = None,
                 has_pending_anchors: bool = False) -> frozenset[str]:
    """本回合的相关性信号。

    刻意只吃**已经算好的**事实(是否绑了剧本、有没有 pending anchor),不新增任何 IO —— 
    与上下文层共用同一份 world state 快照,不另开一条会漂移的真相源。
    """
    sig: set[str] = set()
    if bound_script_id:
        sig.add("canon")
    if has_pending_anchors:
        sig.add("anchors")
    return frozenset(sig)


def _tavern_drops_tool(name: str, *, bound_script_id: int | None = None) -> bool:
    n = (name or "").lower()
    # 酒馆自举工具永远保留
    if any(n.startswith(p) for p in _TAVERN_KEEP_PREFIX):
        return False
    if bound_script_id:
        # 绑定只读剧本:仅禁「改写 canon」,canon 读 + 其余所有写本档状态的工具全开
        return any(s in n for s in _TAVERN_CANON_WRITE_SUBSTR)
    # 无绑定剧本:没有 canon 对象 → canon 读/写工具都丢;其余(world/memory/关系/战斗/物品/模组…)全开
    return any(s in n for s in (_TAVERN_CANON_WRITE_SUBSTR + _TAVERN_CANON_READ_SUBSTR))


def build_unified_tool_list(
    mcp_tools: list[dict[str, Any]] | None,
    origin: str = "llm_chat",
    *,
    mode: str | None = None,
    bound_script_id: int | None = None,
    signals: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """合并 MCP 工具列表 + dispatcher 注册表中允许 origin 的工具。

    输出格式与 mcp_broker.discover_all_tools 一致:
        [{"server_id": str, "name": str, "description": str, "schema": dict}, ...]
    dispatcher 工具用 server_id="__dispatcher__" 标识。

    **排序即窗口资格。** backend 只把前 N 个(core.config.tool_window_size(),默认 18)
    的完整 schema 直发给模型,其余进 load_tools 目录 —— 而实测模型极少主动 load,所以
    窗口是硬名额,排在窗口外约等于这个工具不存在。档位定义见本模块 classify_tool()。

    档位(小的靠前):
      -1 酒馆自举   ·  0 主回合闭环  ·  1 主回合首要读 ·  2 主回合次要读
       3 编辑域枚举 ·  4 主回合写    ·  5 情境专用     ·  6 平台账户管理(永不占窗口)

    signals: 本回合相关性信号(见 turn_signals())。None = 不做门控,与 v1.81 一致。
             只降权不提权:没有 pending anchor 时锚点族让出窗口名额,没绑剧本时
             canon 读让出 —— 有信号时与改动前逐个工具相同。
    """
    def _rank(name: str) -> int:
        return classify_tool(name, signals=signals)

    out: list[dict[str, Any]] = list(mcp_tools or [])
    disp: list[dict[str, Any]] = []
    for spec in get_registry().list_for_origin(origin):
        if mode == "tavern_gm":
            if _tavern_drops_tool(spec.name, bound_script_id=bound_script_id):
                continue
        # 非酒馆(游戏控制台 freeform/novel)模式:酒馆自管理工具(建/换角色、persona、列/绑剧本)
        # 在游戏里无意义且会因 tier -1 抢占窗口最前。这里丢掉,别污染游戏控制台工具表。
        elif spec.name.lower().startswith(TAVERN_SELF_PREFIXES):
            continue
        disp.append({
            "server_id": DISPATCHER_SENTINEL,
            "name": spec.name,
            "description": spec.description,
            "schema": spec.input_schema,
            # 档位随工具一起交给下游(纯数据,backend 只读它认识的键)。_tiered.split_window
            # 据此把直发窗口按「主回合闭环工具必须全部直发」这条不变量自动定容 —— 在此
            # 之前窗口是个手算出来的常量,已经被手算过三次(16→18,每次都是有工具掉出去
            # 之后才发现)。MCP 工具没有这个键,不参与定容。
            "tier": _rank(spec.name),
        })
    disp.sort(key=lambda d: (_rank(d.get("name", "")), d.get("name", "")))
    out.extend(disp)
    return out


def build_tool_call_router(
    *,
    user_id: int,
    save_id: int | None,
    script_id: int | None,
    trace_id: str,
    state_provider: Callable[[ToolCallEnvelope], Any],
    fallback_mcp_call: Callable[[str, str, dict], dict] | None = None,
) -> Callable[[str, str, dict], dict[str, Any]]:
    """构造给 backend.stream_with_mcp_loop 用的 unified mcp_call。

    backend 调 router(server_id, tool_name, arguments) 时:
      · server_id == DISPATCHER_SENTINEL → 走 dispatcher (origin=llm_chat)
      · 否则 → fallback_mcp_call (默认 mcp_broker.call_tool)

    返回 dict {"ok":bool, "result":Any, "error":str|None} 与 mcp_broker 兼容。
    """
    if fallback_mcp_call is None:
        from mcp_broker import call_tool as _default_mcp
        fallback_mcp_call = _default_mcp

    dispatcher = ToolDispatcher(
        registry=get_registry(),
        state_provider=state_provider,
    )

    def _router(server_id: str, tool_name: str, arguments: dict) -> dict[str, Any]:
        if (server_id or "") == DISPATCHER_SENTINEL or (not (server_id or "") and get_registry().has(tool_name)):
            env = ToolCallEnvelope(
                user_id=user_id,
                save_id=save_id,
                script_id=script_id,
                tool=tool_name,
                args=arguments or {},
                origin="llm_chat",
                trace_id=trace_id,
                depth=1,  # GM 响应路径已经在一个 trace 内,标记 depth=1
            )
            result = dispatcher.dispatch_sync(env)
            return {
                "ok": result.ok,
                "result": result.result,
                "error": result.error,
            }
        # MCP 工具
        try:
            return fallback_mcp_call(server_id, tool_name, arguments)
        except Exception as exc:
            return {"ok": False, "error": f"MCP 工具调用异常: {type(exc).__name__}: {exc}"}

    return _router


__all__ = [
    "DISPATCHER_SENTINEL",
    "build_unified_tool_list",
    "build_tool_call_router",
]
