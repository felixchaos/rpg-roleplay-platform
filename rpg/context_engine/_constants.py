"""context_engine._constants — 共享常量."""
from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).parent.parent
CHAR_IDX = BASE / "indexes" / "characters.json"
WORLD_IDX = BASE / "indexes" / "world.json"

# GM 上下文预算 —— 这些是每层的 char 上限(≈ /2 = token)。
# 原值是给 8k 小窗模型设计的,导致整轮总上下文只有 ~4k token,而生产模型(deepseek-v4-pro
# 128k / gemini 1M)能吃几十万 token → 小说正文/角色卡/世界书被严重截断,GM 写不出原著
# 细节与文风、推进缓慢。这里整体放开到能装真正有用的素材;可用 RPG_CTX_SCALE 整体缩放。
import os as _os

try:
    _CTX_SCALE = max(0.25, float(_os.environ.get("RPG_CTX_SCALE", "1.0")))
except (TypeError, ValueError):
    _CTX_SCALE = 1.0

_BASE_LAYER_CHARS = {
    "rules": 2000,
    "rules_state": 2000,          # RulesProvider 动态层(HP/骰子日志),与静态 rules 分 id

    "agent_runtime": 1600,
    "timeline": 2400,
    "timeline_pending": 2400,     # provider 实际层 id,补全防默认 1800 截断
    "novel_timeline": 2400,
    "memory": 4000,
    "worldline": 3000,
    "worldline_directive": 3000,   # task 140: 玩家给 GM 的高优先级导演指令
    "anchor_pending": 8000,        # 世界线收束·接下来的锚点 — ch1 通常 8+ 实体
    "context_agent": 2400,
    "player_card": 2400,
    "npc_cards": 12000,            # 多 NPC 同台 → 别只塞 4 张卡
    "worldbook": 10000,
    "novel_worldbook": 10000,     # ★ 实际 provider 层 id 是这个,不是 "worldbook" → 之前走默认 1800
    "module_worldbook": 10000,
    "rag": 16000,                 # 旧 caller 兜底路径
    "novel_retrieval": 20000,     # ★ 关键:真正的小说正文 RAG(原来不在字典→默认 1800 被砍)
    "state": 3000,
    "state_schema": 1600,   # 纯 schema 模板,不需要长,保持精简
    "write_results": 1000,  # 上轮标签结果反馈,简洁即可
    "fact_groups": 4000,    # canon / runtime / user_constraint 分组渲染
    "hypotheses": 1200,
    "candidate_actions": 1600,
    "recent_chat": 16000,         # 多保留对话历史 → 连贯性
    "user_input": 2400,
    # task 107E: 双时间线 — 存档级历史摘要 + 剧本未来预期
    "runtime_phase_digests": 5000,        # GM 思考历史 (本存档)
    "script_phase_anticipation": 4000,    # GM 思考未来 (剧本预期)
    # 补全:酒馆/模组 provider 层 id 之前不在表 → 走默认 1800 → 角色卡/persona/场景被截断。
    "tavern_card_system": 6000,           # 导入 persona skill 原文常 2000-5000 字
    "tavern_character": 5000,             # 完整角色卡(identity/性格/外观/说话风格/样例对话)
    "tavern_persona": 3000,
    "module_scene": 3000,                 # 房间描述/出口/NPC/检查
    "module_encounter": 3000,
    # 补全第三批(v1.82.0,由 test_context_layer_budget_registry 的 AST 守卫扫出):
    # 这四层从上线起就没进过本表 → 一直走默认 1800 静默截断。episodic_recall 尤其致命 ——
    # 它就是长程记忆的召回层,被砍到 900 token 等于「召回了但塞不进去」。
    "episodic_recall": 12000,             # 相关往事·全程历史召回(长局的记忆主通道)
    "world_pulse": 4000,                  # RATH 离线世界脉动
    "npc_agenda": 4000,                   # NPC 日程/意图
    "consequence_echo": 3000,             # 上轮后果回响
}
MAX_LAYER_CHARS = {k: int(v * _CTX_SCALE) for k, v in _BASE_LAYER_CHARS.items()}

# 未登记层的兜底上限。**它是个陷阱**:漏登记一个层 id 不会报错,只会让那层被砍到 1800 字符,
# 而症状(「GM 不记得」「世界书像摆设」)与截断毫无字面关联,历史上已经这样丢过 8 个层。
# 所以现在有两道闸:① AST 守卫 test_context_layer_budget_registry 在 CI 里挡住新的漏登记;
# ② layer_char_budget() 把「命中/未命中」作为返回值交出去,build_context_bundle 记进 debug
#    并 warn 一次 —— 动态生成的层 id 绕得过 ①,绕不过 ②。
DEFAULT_LAYER_CHARS = 1800


def layer_char_budget(layer_id: str) -> tuple[int, bool]:
    """返回 (该层 char 上限, 是否命中登记表)。

    未命中时给 DEFAULT_LAYER_CHARS,但把 False 交出去 —— 调用方负责让这件事**可见**,
    不许静默。这是「资格在登记处声明,miss 必须可诊断」的最小落点。
    """
    v = MAX_LAYER_CHARS.get(layer_id)
    if v is None:
        return int(DEFAULT_LAYER_CHARS * _CTX_SCALE), False
    return v, True


# ── 全局预算求解(v1.82.0)────────────────────────────────────────────────
# 上面那张表是每层的 want(想要多少)。它们之和 ≈17.3 万字符 ≈ 8.7 万 token,而在此之前
# **没有任何一处**拿这个和跟模型真实 context window 比对过 —— context_window_for() 只在
# app.py 事后记账。小窗模型上等于把「超没超」交给 backend 去盲截。
#
# 现在 build_context_bundle 可以收一个 budget_chars 做求解:先保每层 min,再把剩余预算按
# priority 降序、按 (want-min) 比例分配。**求解结果永不超过 want** —— 所以预算宽裕时
# (gemini 1M / deepseek 1M)输出与改动前逐字节相同,只有真的装不下时才生效。
_LAYER_MIN_CHARS = {
    # 不可压缩:压了就不是「少一点素材」,而是契约/输入本身残缺。min == want。
    "rules": 2000,
    "agent_runtime": 1600,
    "state_schema": 1600,
    "user_input": 2400,
    # 有意义的下限:低于它这层就没有信息价值,宁可整层丢弃也别留半句。
    "state": 1200,
    "anchor_pending": 1500,
    "episodic_recall": 1500,
    "novel_retrieval": 2000,
    "npc_cards": 1500,
    "worldbook": 1200,
    "novel_worldbook": 1200,
    "module_worldbook": 1200,
    "runtime_phase_digests": 900,
    "script_phase_anticipation": 900,
    "tavern_card_system": 1200,
    "tavern_character": 1200,
    "tavern_persona": 600,
}
# ⚠️ priority 是**层在 prompt 里的位置**,不是重要性 —— user_input 的 priority=0 是因为
# 它必须垫底(近因),不是因为它可有可无。所以丢弃顺序不能直接用 priority:那样第一个被丢的
# 就是玩家这一轮说的话。这份名单把「不许丢」显式列出来,与位置解耦。
NEVER_DROP_LAYERS = frozenset((
    "rules",           # GM 行为契约
    "agent_runtime",   # 主 GM 代理运行契约
    "state_schema",    # 状态字段 schema:丢了 GM 写不出合法标签
    "state",           # 当前状态简报
    "user_input",      # 玩家本轮输入 —— 丢它等于没收到消息
))

# 未列出的层:按 want 的比例折算并夹进 [200, 1500]。
_MIN_RATIO = 0.30
_MIN_FLOOR = 200
_MIN_CEIL = 1500


def layer_min_chars(layer_id: str, want_chars: int) -> int:
    """求解用的每层下限。永不超过 want(否则 min>want 会让求解无解)。"""
    explicit = _LAYER_MIN_CHARS.get(layer_id)
    if explicit is not None:
        return min(int(explicit * _CTX_SCALE), want_chars)
    derived = int(want_chars * _MIN_RATIO)
    return min(max(derived, _MIN_FLOOR), _MIN_CEIL, want_chars)

# Q 三贤者分层缓存:层 id → cache_tier。
#   A 会话级稳定 = 逐回合字节恒等 → 厂商缓存真命中(放可缓存前缀)。
#   B 场景级稳定 = 一幕戏内稳定,换场/换章才变(打断点免费:命中就赚,不中退化全价)。
#   C 回合动态   = 每回合变,永不缓存(放末尾)。
# provider 层可用 make_layer(cache_tier=...) 覆盖;未列出的层兜底 "C"。
# 详见 docs/design/Q_three_sage_pipeline.md §5。
LAYER_CACHE_TIER = {
    # ── A 会话级稳定 ──
    "rules": "A",
    "agent_runtime": "A",
    "player_card": "A",
    "state_schema": "A",
    "worldline_directive": "A",      # 玩家给 GM 的高优先级导演指令,改动很少
    "tavern_card_system": "A",       # 酒馆卡内嵌 system_prompt
    "tavern_character": "A",         # 酒馆角色定义
    "tavern_persona": "A",           # 玩家 persona
    # ── B 场景级稳定 ──
    "npc_cards": "B",
    "worldbook": "B",
    "novel_worldbook": "B",
    "module_worldbook": "B",
    "anchor_pending": "B",
    "novel_timeline": "B",
    "timeline": "B",
    "script_phase_anticipation": "B",
    "module_scene": "B",
    "module_encounter": "B",
    # ── C 回合动态(显式列出便于审计;未列出也兜底 C)──
    "timeline_pending": "C",
    "state": "C",
    "fact_groups": "C",
    "memory": "C",
    "worldline": "C",
    "write_results": "C",
    "hypotheses": "C",
    "context_agent": "C",
    "candidate_actions": "C",
    "novel_retrieval": "C",
    "rag": "C",
    "recent_chat": "C",
    "runtime_phase_digests": "C",
    "user_input": "C",
    "episodic_recall": "C",
    "world_pulse": "C",
    "npc_agenda": "C",
    "consequence_echo": "C",
}


def layer_cache_tier(layer: dict) -> str:
    """解析单层的 cache_tier:层显式 > 中央映射 > 兜底 C。"""
    t = (layer.get("cache_tier") or "").strip().upper()
    if t in ("A", "B", "C"):
        return t
    return LAYER_CACHE_TIER.get(layer.get("id", ""), "C")
