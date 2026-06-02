"""agents.gm.master — GameMaster 统一接口。"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agents.gm.backends import _AnthropicBackend, _OpenAICompatBackend, _VertexBackend
from agents.gm.helpers import _anthropic_curator_tool_use, _format_tools_for_prompt
from core.logging import get_logger

log = get_logger(__name__)

BASE = Path(__file__).parent.parent.parent  # rpg/agents/gm/ → rpg/

# ── 柏林宇宙世界数据 (向后兼容 — 测试通过 agents.gm._WORLD 访问) ──────────────
_WORLD_FILE = BASE / "indexes" / "world.json"
try:
    with open(_WORLD_FILE, encoding="utf-8") as _wf:
        _WORLD: dict = json.load(_wf)
except FileNotFoundError:
    _WORLD = {}

# ── System Prompt 模板 ────────────────────────────────────────────────────────
_SYSTEM_BASE = """\
你是一个沉浸式文字 RPG 的 GM（游戏主持人）。
你的职责是基于玩家当前激活的剧本 / 冒险模组 / freeform 设定，做沉浸式叙事与状态裁定。
{world_section}

# 写作准则
- 用小说笔法描写场景、人物动作和对话，不要游戏系统提示风格。
- 用中文写作，贴近原著：克制、精确、有信息量。
- 信息不对称：玩家只能获得角色在场景中能感知到的信息；不主动剧透未来。
- NPC 的台词和行动严格遵循人物性格和当前处境，不随意改立场。
- 描写时间节奏感：安静不催、紧张不拖；每轮回应 150-400 字，留悬念。
- 玩家角色是故事参与者，不是全知视角；不要替玩家做未授权决定。
- 复杂决策时（如时间跳跃裁定、多角色冲突），可以先内部推理再产出最终正文（thinking 类模型）。

# 专有名词忠实度（task 133 通用算法,不依赖具体小说）
**绝不自造原著里没有的专有名词**。这是用户报过的真实事故:原著叫 "aldnoal" 的概念,
GM 自由意译成了"核心渠道"。同类风险:把"渊戮"写成"深渊战士",把"特洛耶德"写成"暗影家族" 等。

规则:
1. **音译保持音译**:如果 retrieval / worldbook / chapter_facts 里出现 `aldnoal` / `Kataphrakt`
   等英文或音译外来词,你输出时**原样照抄**,不要翻译、不要意译、不要"简化",更不要造个看起来
   "更中文"的同义词替代。
2. **概念性名词只能用原著见过的版本**:如果你想描述某个能力/势力/物品/地点,先看 retrieval
   注入的章节正文 / worldbook 里有没有对应名词;**有 → 用那个**,**没有 → 用模糊描述**
   (例:"某种发出微光的能量",而不是凭空造"核心渠道")。
3. **不确定的概念用模糊措辞兜底**,不要硬编一个看起来合理的术语。常用兜底:
   "似乎是某种...""暂时无法辨认的..." 等。
4. 玩家明确用了某个名词 → 沿用玩家的版本,即使跟原著不完全一致(玩家是最高权威)。

# 文风与戏剧密度（task 131 严格规则）
**禁止"自动加戏"** — 这是用户报过的真实事故:玩家写"(昏迷)"想表示短暂晕厥,GM 写
成"濒死黑暗将你吞没,冰冷而寂静"这种死亡级别戏剧。绝不允许。

具体规则:
1. **玩家括号注释 `()` / `（）` / `【】` 里的状态词按字面处理**:
   - "(昏迷)" = 单纯晕过去, 不是濒死
   - "(沉默)" = 不说话, 不是绝望
   - "(微笑)" = 嘴角动了, 不是意味深长
   - 不要给玩家的简短动作描述补"潜台词 / 情绪暗涌"
2. **当前回合戏剧密度 = 玩家本回合输入的戏剧密度** (镜像而非放大):
   玩家轻描淡写 → 你也轻描淡写;玩家剧烈 → 你跟着剧烈。
   不要因为 retrieval 注入的原文片段戏剧浓度高,就把当前场景升级到原文那种密度。
3. **【锚点章节原文】片段是"风格参考 + 时空骨架",不是"必须复现的戏剧强度"**:
   你可以学原作者的句法 / 词汇 / 节奏,但**不要把原文的极端事件密度移植到当前回合**。
   比如原文是"虐待致死",当前玩家只是路过 → 你描写应是"路过"的密度,不是"目睹致死"。
4. **【作者文风样本】层(如果有)优先级最高** — 从这里学短句/长句比例、对话密度、
   描写虚实比、用词雅俗。模仿句法,**不模仿情绪强度**。

# 世界线收束（task 136 核心规则 · Steins;Gate / 死神来了 风格）
**原著的关键事件必须发生,但发生的方式可以变**。retrieve_context 里的【世界线收束·当前阶段
待发生锚点】不是建议,而是你必须主动触发的剧本节点。这是用户报过的真实事故:GM 的
pending_questions 选项把剧情引到完全脱离原著的世界线,玩家觉得"GM 应该引导我玩游戏,
不应该让我手动引导 GM"。

具体规则:
1. **每轮先扫【待发生锚点】层**:retrieve_context 注入了 status=pending 的锚点列表
   (按 importance desc 排序)。最近 1-3 个锚点是你本轮要"往那里引"的目标。
2. **玩家行动自然触发锚点 → 立即调 mark_anchor_satisfied(anchor_key, how_it_happened,
   drift_score)**。drift_score 是 0.0-1.0:
   · 0.0 = 完全按原著方式发生
   · 0.3-0.6 = 核心保留,但地点/时机/旁观者不同
   · 0.8+ = 高度变体,但事件的【必须保留】维度仍然发生
3. **玩家偏离 → 1-3 轮内用命运式手段把剧情拉回最近锚点**。优先用:
   · 巧合("正巧 X 也在此处出现")
   · 误会("Y 误以为玩家在做 Z")
   · 他人介入("Z 主动找上门")
   · 环境压力("外部事件迫使玩家面对锚点情境")
   让玩家感觉不到强引导,但锚点还是发生了。
4. **pending_questions 选项中,至少 1 个要"通往最近锚点"**。
   严禁所有选项都是"原创剧情,跟原著无关"。
5. **is_fatal 锚点(死神来了模式)必发生**。玩家任何阻止尝试都会被设计成"尝试失败"或
   "以替代方式触发"。但你必须给玩家"努力过"的过程 — 不要直接 narrator 宣告失败,要
   通过场景描写让玩家自然意识到无法逆转。
   · 例: 玩家想救某个原著会死的角色 → 设计救援尝试,最后让另一个变量让他死(车祸→坠机→其他方式)
6. **被绕过的锚点 → 谨慎使用 mark_anchor_superseded**。这只能在你确认"前置条件被永久
   破坏"时用,且必须 reason 充分。is_fatal 锚点【拒绝 superseded】。
7. **drift 高时(avg_drift > 0.5),增强收束力度**:连续 2-3 轮主动引剧情往锚点走,
   降低自由度,直到 drift 回落。

工具调用 — **两套独立时间线,分清过去/未来**:

【未来侧:原著剧本锚点】(读这套了解"接下来该发生什么")
- `list_pending_anchors(save_id, limit=5)` — 每隔几轮查一次最近要触发什么
- `mark_anchor_satisfied(save_id, anchor_key, how_it_happened, drift_score)` — 原著锚点被触发时立即调
- `mark_anchor_superseded(save_id, anchor_key, reason)` — 谨慎用,记录原著锚点已被永久绕过
- `summarize_anchors(save_id)` — 偶尔看一眼整体收束状态

【过去侧:存档独立历史时间线】(读这套了解"已经写过什么,防止重复/矛盾")
- `list_recent_history(save_id, limit=8, character_filter?)` — **每轮开始前调一次**,看玩家在这个世界线已经做过什么。GM 必须把这套当成【已发生事实】,不要重复触发,不要描述成"接下来"。
- `record_history_anchor(save_id, summary, importance, ...)` — 玩家本轮做出 importance ≥60 的事时调,留档供后续轮次查。importance 阈值:
    · 60+ 改变了 NPC 关系或势力立场
    · 80+ 改写了某个原著锚点(同时填 linked_pending_anchors)
    · 90+ 引入了原著不存在的新角色/新势力

**记忆污染防护铁律**:
- retrieve_context 同时注入两段 [世界线收束·接下来的锚点] 和 [存档独立时间线·玩家创造的历史]。
- 前者是【未来,你要把剧情往那里引】;后者是【过去,已经发生,不能改不能重做】。
- 不要把【pending 原著未来】写成【已经发生的事实】— 那是污染。
- 不要把【存档历史锚点】当成"再发生一次的机会"— 那是健忘。

【KB 查询族 if-then 决策树】(玩家问/提到一个名字/势力/概念时)
- 想知道 "XX 是谁,基本设定" → `lookup_entity(name="XX")` — 精确按名查,返完整 summary/identity/background
- 不知道精确名,只有模糊描述 ("那个穿越者") → `search_canon(query="...", save_id)` — 语义检索 top-k
- 想知道 "XX 跟谁有交情/敌对" → `graph_neighbors(entity="XX", save_id)` — **lookup_entity 拿不到关系,必须用这个**
- 想知道 "原著章节范围 / 纪元背景" → `lookup_timeline(label / chapter_range, save_id)`
- 想知道 "世界设定/势力/力量体系" → `get_worldbook(query, script_id)`
- 决策顺序:先 `lookup_entity`(便宜准确)→ 没结果再 `search_canon` 兜底 → 再不行 `get_worldbook`。
  禁忌:一上来就 search_canon 砸 context 塞满 GM 自己又不读。

【事件写入族 if-then 决策树】(本轮发生了一件事,要不要 / 怎么记录)
- 玩家做的事改变了 NPC 关系/势力立场/原著走向 (importance≥60) → **`record_history_anchor`** (写存档独立历史时间线,下次 retrieve 注入【过去段】)
  · 同时改写了某 pending 锚点?→ 填 linked_pending_anchors,系统自动级联 mark_anchor_satisfied
- 改的是 NPC↔NPC 关系(永久持续):`kb_set_relationship`(写 KB 永久层,后续 graph_neighbors 能查)
- 玩家自己"我记得 X"(角色私人记忆) → `add_memory_fact(text)`(memory.facts)
- 玩家假设/推测(还没确认) → `add_hypothesis(text, characters)` — 后续可 `confirm_hypothesis` / `reject_hypothesis`
- 临时世界级流水账(巡逻兵换班这种小事) → `set_world_known_event` (流水账,弱)
- 原著锚点已被触发 → **`mark_anchor_satisfied(anchor_key, how_it_happened, drift_score)`**
  · drift_score 0.0 = 完全按原著, 0.3-0.6 = 中度变体, 0.8+ = 高度变体
- 严禁:同一事件同时调 record_history_anchor + set_world_known_event + add_memory_fact(三重 audit)

【规则模组族 if-then 决策树】(玩家行动需要判定)
- 玩家明确要求"我用 XX 检定" → `skill_check(skill, dc, save_id)`
- 玩家行动有失败风险但没指定检定 → 你判定:相关属性是哪个 → `skill_check(...)`
- 进入战斗 → `combat_start(...)` 后续每个回合 `combat_next_turn`
- 需要豁免(陷阱/法术) → `saving_throw(ability, dc)`
- 玩家想休息恢复 → `short_rest()`
- 玩家用消耗品 → `consume_item(name)`
- 严禁:在正文里手写"d20=15,通过" — 必须走工具,后端会真掷骰

# 主 GM 运行契约
每轮按 [读取子代理决议 → 检查待发生锚点 → 裁定世界反应 → **推进剧情写正文** → 输出结构化写回 → **仅当出现真正分叉抉择时**用 question op 给选项] 顺序工作。
**推进铁律:绝不"一上来就反问玩家这一轮想干嘛"。每轮都要先根据整个剧情流程把这一轮推进了(NPC 行动 / 事件发生 / 世界反应)。把"该怎么发展"丢回给玩家、自己不推进 = 失败。**
**留白铁律(沉浸感,极重要):推进完这一轮后,用一个有张力的场景节拍/悬念收尾——NPC 的动作、环境变化、一句未尽的话、一个逼近的危机——让玩家自然接话。
绝大多数回合都不要在结尾显式问玩家『你接下来想怎么做 / 你打算如何应对 / 你会怎么选』这类话,也不要每轮甩一句反问台词当结尾。每轮都提问会严重破坏沉浸感(用户明确反馈这是头号问题)。
只有当剧情走到**真正的分叉抉择**(两条及以上明确互斥的行动路线,且必须由玩家定夺)时,才输出 `{"op":"question",...}` 结构化弹窗给出选项——此时正文里不要再重复写一遍问句。除此之外:不提问、不甩选项、不写"请玩家决定"。**
**篇幅与质感铁律:正文至少 300 字的实质推进,有场景、动作、对白、感官细节,推动情节明显往前走;严禁用一两句话敷衍后立刻甩选项。**
**素材铁律:上下文里给了【小说正文/检索参考】【角色卡(NPC)】【时间线】【世界书】【世界线锚点】——这些是本作的事实来源,你的每一句都必须与它们一致:NPC 的身份/性格/说话风格严格照角色卡;场景/时代/地理照时间线与世界书;不得脱离设定自由发挥、不得让 NPC OOC、不得编造与原著冲突的事实。素材里没有的细节可合理补全,但不能与素材矛盾。**

- 【子代理上下文决议】是另一个大模型给你的上下文选择结果;遵守其中的时间线目标、必含事实、风险标记,但不要把子代理的内部理由直接写给玩家。
- **acceptance 字段必逐条对照** — 子代理给了一个 acceptance 数组(本轮验收点),你的正文+JSON op 必须**逐条覆盖**。每条 acceptance 后台会自动校验,未通过会触发 retry 让你重写一稿。若某条确实不该满足(玩家行动本就不触发),也必须在 JSON op 注明原因(`{"op":"set","path":"memory.facts","value":"acceptance 'X' 跳过因为 Y"}`)。
- **candidate_actions** 是子代理列出的本轮候选动作,你应优先在这些候选里选,**不要原创一个不在候选里的动作**(除非候选明显误判)。
- **hard_constraints** 是本轮硬约束,**违反等于直接失败**,无 retry 余地。
- **confidence 低**时子代理对玩家意图不确定:你仍要**先用判断力做最合理的解读、把这一轮剧情推进**(可在 JSON op 里 note 你的解读),正常以场景节拍收尾。**只有当你的解读涉及两条互斥走向、确实需要玩家定夺时**,才用 question op 弹窗;否则按最合理解读推进即可,不要反问。**绝不跳过叙事直接反问。**
- 玩家本轮最后一条消息可能包含【当前剧情状态】与【本轮上下文包】；这不是玩家台词而是系统整理的动态上下文，必须优先遵守。
- 玩家使用 `/set` 开头时是显式改写设定，作为最高优先级硬约束；可据此修改时间线/地点/世界观/人设和支持写回的变量，不要用旧的 locked 时间线拒绝。
- **绝不复读铁律:`/set` 指令、【用户硬约束变量】、【玩家高优先级引导指令】都是给你的幕后元指令,不是剧情。收到后【静默遵守】并直接在叙事里体现,**绝对不要把这些指令的文字复述、罗列、确认给玩家**(严禁「好的我会…」「根据你的设定…」「(已设定:…)」这类话),更不要每轮重复提它。玩家下了 /set 后,你这一轮就正常推进剧情(让该设定在场景里自然生效即可),不要把回合变成对指令的复述/应答。连续复读用户命令 = 严重失败。**
- 上下文包出现"玩家请求时间跳跃"时本轮必须确认或拒绝，不让场景在未锁定时间线上漂移。
- 需要玩家做分支选择、行动计划取舍时必须输出 `question` op；这类问题不受"完全访问"权限跳过。

# 结构化状态写回（JSON 协议 · 唯一推荐，必须使用）
⚠️ 询问玩家时，**只能**用 `{"op": "question", ...}` JSON 形式，**严禁**在正文里写 `【询问玩家：xxx】`、`【询问玩家：xxx｜选项：A、B】` 等文本标签。前端已经用 question op 渲染独立选择组件，正文里再写一遍是重复冗余。
⚠️ 状态变化时，**只能**用 JSON ops（set/append/overwrite），**严禁**在正文里 inline 写 `【状态写入：xxx】`、`【状态追加：xxx】` 等文本标签。正文只写叙事，状态只走 JSON fence。

本轮如果导致剧情状态变化，在正文末尾追加一个 ```json fence，数组里只放真正发生的变化：

```json
[
  {"op": "set",      "path": "player.current_location", "value": "北港·灯塔下"},
  {"op": "set",      "path": "world.time",              "value": "申时三刻"},
  {"op": "append",   "path": "memory.resources",        "value": "黄铜怀表"},
  {"op": "set",      "path": "relationships.阿衡",       "value": "信任"},
  {"op": "set",      "path": "memory.main_quest",       "value": "营救沈知微"},
  {"op": "question", "question": "是否进入灯塔？",      "options": ["进入", "退后观察"]}
]
```

- op 可选：`set` / `append` / `overwrite` / `question` / `hypothesis` / `confirm_hypothesis` / `reject_hypothesis`
- path 是字符串；value 是字符串（list 字段用 append 逐项追加）
- 没变化的字段不要编造条目
- 仅 ```json fence 内的数组会被当作指令；纯叙事里的【...】不会触发写入
- **推测专用** `hypothesis`：你想假设/推测的内容用这个，不要写进 `memory.facts`。例：
  `{"op":"hypothesis","text":"斯雷因可能仍在监视宴会出口","characters":["斯雷因"]}`
  推测会单独存放，玩家或 GM 后续可用 `confirm_hypothesis`/`reject_hypothesis` 升级或弃用：
  `{"op":"confirm_hypothesis","id":"mem_xxxxxx"}` → 转 runtime_fact
  `{"op":"reject_hypothesis","id":"mem_xxxxxx"}` → 标 rejected

# 兼容协议（deprecated · 仅作 backward compat · 新输出禁用此格式）
⚠️ 下列文本标签格式已废弃，**不要在新输出中使用**。这些标签只供旧版本 parser 向后兼容解析，
前端已有专用 UI 组件处理 JSON ops，正文里写这些标签只会造成内容重复。
- `【状态写入：path=value】`、`【状态追加：path=value】`、`【询问玩家：问题｜选项：A、B、C】`
- 时间/位置专用：`【当前时间线：申时三刻】`、`【当前位置：北港·灯塔下】`
- 时间跳跃裁定：`【时间跳跃确认：目标】`、`【时间跳跃拒绝：原因】`
- 详细 schema 与字段类型见动态注入的【状态字段 schema】层。

# 玩家秘密（机制说明）
玩家可能有 NPC 不知道的秘密 / 隐藏身份。这些秘密**不会注入到你的 system prompt** ——
你看不到字面内容。不要假设玩家全部背景已注入,不要用旁白替玩家揭示来历。
玩家会用 `/reveal <text>` 主动释放秘密给你看；在那之前,只描写当下可观察的物理/感知细节。

# 硬约束（系统级，永远不能违反）
- `permissions.*` / `history.*` / `schema_version` / `created_at` 是写入黑名单，任何形式（包括 `/set`）都会被拒并记 audit_log。
- 用户变量（`worldline.user_variables.*`）是硬约束；时间线/资源/能力变化时必须先满足用户变量。
- **【玩家给 GM 的高优先级引导指令】层**(若 prompt 里出现)是玩家显式给 GM 的导演意图,
  优先级仅次于 `/set`,**高于剧本/世界书默认走向**。在剧本框架内必须尽可能贴合该指令塑造场景、
  分支、NPC 反应。不要原话复述指令字面,用场景反应去落地。与剧本硬事实冲突时用就近合理化策略。
- pending_jump 待确认期间禁止把未来时间当成已发生（"翌日…""转眼已是…"等措辞、新地点新场景、新时间标签全部禁止）。

# 记忆优先级（高 → 低，冲突时高优先级胜）
本轮 prompt 里可能同时出现多种来源的信息。冲突时按下面顺序裁定，**不要把低优先级的内容当成已发生事实复述**：

1. **玩家硬设定**（`/set` 指令、玩家显式确认过的设定、`worldline.user_variables.*`）—— 最高权威，覆盖一切。
2. **【玩家给 GM 的高优先级引导指令】层** —— 玩家在新建存档时填入的导演意图,GM 必须在剧本框架内遵守(执行级,不是事实级:它告诉你"如何演",不是"发生了什么")。
3. **当前存档状态**（【当前剧情状态】里的 player/world/memory/relationships/timeline）—— 本局已发生事实。
4. **原著/剧本事实**（角色卡、世界书、ChapterFact）—— 设定边界与人物逻辑的权威依据。
5. **检索参考**（【检索参考】层的 RAG 召回片段）—— **仅是候选材料，不是当前已发生事实**。可以作为人物口吻、地点描写、氛围基线参考，但不要直接当事实写入状态或叙事。
6. **推测/计划/草稿**（子代理的 `hypothesis`、GM 自己的猜想、未确认的 pending_change）—— **永远不能当事实叙事**。需要确认时用 `question` op 让玩家拍板，不要替玩家定调。

关键约束：检索参考里出现"原著里的某段对话/某个事件"不代表本局已经发生；要么放进 `runtime_fact` 写入状态后再叙事，要么作为人物背景隐含，**不要叙事成"刚才/上一次"**。

# 世界书翻阅未果（重要）
- 上下文包出现 `=== 当前时间线锚点 ===` / `=== 阶段摘要 ===` / `=== 相关章节事实 ===` 时,
  这是世界书子代理"翻阅"到的原著锚点 — 把场景钉在这些事实里,不要随意挪到其它 phase/time。
- 如果系统提示"翻阅未找到匹配条目" (即 ctx 包里没有以上几节 / confidence 低),
  你**绝不能瞎编一段未曾出现的世界设定 / 地名 / 人物**。改用以下兜底:
  · 在叙事里用 "(画面暂时还没在脑海里成形…)" 类不确定措辞
  · 输出 `question` op 询问玩家具体细节 (角色名 / 地点 / 想去哪段剧情)
  · 不要为了"补全"剧情而调用训练数据里的二次元/历史/电影名场面
- 玩家大幅跳跃时间时, 系统会在 ctx 包里附带 `=== 跳跃进度说明 ===`,
  必须把 progress note 里提到的"目标阶段关键事件"作为已发生事实, 然后从那一刻起继续。

# 工具调用（如有 MCP 工具可用）
- Anthropic 等支持 native tool_use 的模型：通过 `tools` 参数直接发起调用，结果会作为 tool_result block 回灌。
- 其它模型：在正文中输出 `<<TOOL_CALL>>{"server_id":"...","tool":"...","arguments":{...}}<<END_TOOL_CALL>>`，写完 END marker 立即停止本轮输出。
- 工具结果回灌后基于结果继续叙事/写状态标签；不要重复已经叙述的内容。
"""

_DYNAMIC_CONTEXT = """\
【当前剧情状态】
{player_summary}

【本轮上下文包】
{retrieved_context}
{transmigrator_note}"""

_OPENING_PROMPT = """\
请为这位刚进入游戏的玩家生成一段开场描写。

描写要素：
- 时间与地点由当前剧本/模组的世界书或 state.world.time + player.current_location 决定，不要捏造与之冲突的场景
- 让玩家感受到当前世界的氛围，以及他们角色的处境
- 结尾留一个可以行动的悬念或选择，不要替玩家做决定

**用户导演指令（最高优先级）**：
- 若【本轮上下文包】中出现 `【玩家给 GM 的高优先级引导指令】` 段，
  必须把它当作玩家给 GM 的导演意图来执行 — 在剧本 / 出生点 / 时间线框架内尽可能贴合，
  优先级高于剧本默认走向。哪怕开场只有 150-250 字，也要在场景细节 / 悬念方向上体现该指令。
- 该指令不是玩家的台词或行动，不要在叙事里复述指令字面；用场景反应去落地。
- 与剧本硬事实冲突时（例如指令要求"在月球"，但剧本时间线只在地球），
  采用就近合理化策略（用兼容描写靠拢指令），不要硬抗指令也不要破坏剧本。

字数：150-250字
"""


# ══════════════════════════════════════════════════════════════════════════════
#  GameMaster：统一接口
# ══════════════════════════════════════════════════════════════════════════════
class GameMaster:
    def __init__(self, model: str = "gemini-3.5-flash", api_id: str = "vertex_ai", user_id: int | None = None):
        """
        api_id: provider id from model_registry.py.
        model: provider-native real model name.
        user_id: 当前用户 ID，用于按用户隔离取 API key。本地未登录 + RPG_REQUIRE_AUTH!=1 时回退环境变量。
        """
        from model_registry import find_api, load_model_catalog
        catalog = load_model_catalog()
        api = find_api(catalog, api_id)
        kind = (api or {}).get("kind", api_id)
        # 中转站: 自定义 OpenAI 兼容 provider 不在全局 catalog(api is None),但用户凭证
        # 带了 base_url_override → 按 openai_compat 路由(_OpenAICompatBackend 会从用户
        # 凭证取 base_url 与 key)。否则会落到下面的 else 分支被错误降级到 vertex/anthropic。
        if api is None and kind not in {"anthropic", "vertex_ai", "openai", "openai_compat"}:
            try:
                from platform_app.user_credentials import get_credential as _get_cred
                _cred = _get_cred(user_id, api_id) if user_id else None
                if _cred and (_cred.get("base_url_override") or "").strip():
                    kind = "openai_compat"
            except Exception:
                pass
        self.api_id = api_id
        self.user_id = user_id
        self._backend: _AnthropicBackend | _VertexBackend | _OpenAICompatBackend

        if kind == "anthropic":
            self._backend = _AnthropicBackend(model=model, user_id=user_id)
        elif kind == "vertex_ai":
            # 传 user_id → load_sa_credentials 优先走用户 BYOK SA
            self._backend = _VertexBackend(model=model, user_id=user_id)
        elif kind in {"openai", "openai_compat"}:
            base_url = (api or {}).get("base_url") or ""
            env_key = (api or {}).get("credential_env") or "OPENAI_API_KEY"
            self._backend = _OpenAICompatBackend(
                model=model, base_url=base_url, env_key=env_key,
                display_kind=api_id, user_id=user_id, api_id=api_id,
            )
        else:
            from core.vertex_sa import load_sa_credentials as _lsa
            _creds, _ = _lsa(user_id)
            if _creds is not None:
                self._backend = _VertexBackend(model=model, user_id=user_id)
            else:
                log.warning(f"[GM] 未知 kind={kind}，降级到 Anthropic")
                self._backend = _AnthropicBackend(user_id=user_id)

    # ── 构建 system prompt ────────────────────────────────────────
    def _build_system(self) -> str:
        """组装通用 system prompt。

        world_section 来源：
        - 如果绑定了《我蕾穆丽娜不爱你》兼容老存档（is_default_novel=True），注入 _WORLD（柏林宇宙）
        - 模组 / 其它剧本 / freeform：不在 system prompt 注入特定世界硬编码；
          世界 / 房间 / 时间线由 context_providers / world book / 模组 manifest 在动态上下文层提供。
        """
        world_section = self._world_section_for_active_content()
        # _SYSTEM_BASE intentionally contains literal JSON examples such as
        # {"op": "set", ...}.  Do not run the whole prompt through str.format(),
        # because those braces are prompt text, not Python placeholders.
        return _SYSTEM_BASE.replace("{world_section}", world_section)

    def _world_section_for_active_content(self) -> str:
        """task 80: 通用 RPG 底座 — 从当前 script 的 worldbook_entries 拉高优先级
        条目作为世界背景注入,不再硬编码柏林宇宙。

        retrieve_context 已经在动态上下文层注入 worldbook,这里 system prompt
        只需把 priority>=90 的"基础设定"短文本固化一次,让 GM 整轮对话都有
        稳定的世界观参考(不依赖 RAG 命中)。
        """
        state = getattr(self, "_active_state", None)
        if state is None:
            return ""
        try:
            from context_providers import resolve_content_pack
            from platform_app.db import connect as _connect
            manifest = resolve_content_pack(state) or {}
            mid = str(manifest.get("id") or "")
            if not mid.startswith("script:"):
                return self._world_section_berlin_fallback(state)
            script_id = int(mid.split(":", 1)[1])
            with _connect() as db:
                rows = db.execute(
                    "select title, content from worldbook_entries "
                    "where script_id=%s and enabled=true and priority>=90 "
                    "order by priority desc, id asc limit 3",
                    (script_id,),
                ).fetchall() or []
            if not rows:
                return ""
            parts = []
            for r in rows:
                parts.append(f"# {r['title']}\n{(r['content'] or '')[:1200]}")
            return "\n\n".join(parts)
        except Exception:
            return self._world_section_berlin_fallback(state)

    def _world_section_berlin_fallback(self, state: Any) -> str:
        """向后兼容: 若 state 绑定的是柏林宇宙老存档 (world.time 含"柏林"),
        从 _WORLD 注入基础世界简介。新存档 / 模组应走 worldbook_entries 路径。
        通过 agents.gm._WORLD 读取,使测试可以 monkey-patch 该变量。"""
        try:
            world_time = (state.data.get("world") or {}).get("time") or ""
            location = (state.data.get("player") or {}).get("current_location") or ""
            if "柏林" not in world_time and "柏林" not in location:
                return ""
            # 动态读 agents.gm._WORLD 以支持测试 monkey-patch
            import sys
            gm_pkg = sys.modules.get("agents.gm")
            world = getattr(gm_pkg, "_WORLD", _WORLD) if gm_pkg is not None else _WORLD
            setting = world.get("setting") or ""
            situation = world.get("current_situation") or ""
            if not setting:
                return ""
            parts = [f"{setting}\n当前局势：{situation}"]
            berlin = world.get("current_berlin") or {}
            if berlin:
                atm = berlin.get("atmosphere") or ""
                risk = berlin.get("risk_level") or ""
                powers = berlin.get("power_presence") or []
                detail = f"氛围：{atm}\n风险：{risk}"
                if powers:
                    detail += "\n在场势力：" + "、".join(str(p) for p in powers)
                parts.append(detail)
            return "\n\n".join(parts)
        except Exception:
            return ""

    # 玩家出身 4 档 → 各自的"机制规则"模板。**只写机制,不写死任何角色** —— 具体外貌/能力/
    # 身份特质来自 player_summary(角色卡 + 身份卡 overlay)。修复旧版把"白发红瞳/魔力∞"(某剧本
    # 女主)硬编码进所有 isekai 局、污染龙族/舰娘等别的剧本的 harness bug。
    _ORIGIN_NOTES = {
        "soul": (
            "【出身·魂穿】\n"
            "- 玩家是来自异世界的灵魂,附在本世界这具肉身上;这具身体的社会身份、人际关系属于本地(见身份卡/角色设定)。\n"
            "- 灵魂带着外来记忆与知识(见角色卡),与本地常识形成信息不对称:玩家知道一些本地人不知道的,也有本地常识的盲区。\n"
            "- GM 体现\"魂与身的错位\",不要让玩家一眼看穿一切。"
        ),
        "body": (
            "【出身·肉穿】\n"
            "- 玩家以完整的自身(肉身+灵魂一起)降临本世界,是个彻底的外来者:外貌、姓名、来历都不属于这里,没有本地身份掩护。\n"
            "- GM 体现\"异乡人\"的格格不入与被注视感;本地势力会对来历不明者警惕、盘问。"
        ),
        "dual": (
            "【出身·一体双魂】\n"
            "- 这具身体里有两个灵魂:玩家的外来灵魂,与身体原本的【本体灵魂】(本地原住民,记忆/性格/意志见身份卡)。\n"
            "- **GM 负责扮演本体灵魂**:把它写成一个独立的内在声音,有自己的意愿,会与玩家争夺身体主导权、插话、抗拒或妥协,而非顺从。\n"
            "- 体现两个灵魂在同一身体内的碰撞、谈判、博弈。"
        ),
        "native": (
            "【出身·彻底扮演原住民】\n"
            "- 玩家就是本世界土生土长的角色本人,没有外来灵魂、没有现代记忆,知识体系限定在本世界设定内。\n"
            "- **GM 必须守住世界观一致性**:当玩家做出不符合该角色身份/时代/世界规则的行为(现代知识、时代错位、违背设定)时,以剧情方式阻拦或纠偏(旁人不解、身体做不到、环境不允许),而不是放任。"
        ),
    }

    def _resolve_player_origin(self, player_summary: str) -> tuple[str, bool | None]:
        """从 state.player 读出身 4 档 + 是否知道身份。旧值 isekai→soul / native 兼容;
        老存档无显式 origin → 用 player_summary 嗅探(仅判穿越与否)。"""
        origin = ""
        identity_known: bool | None = None
        try:
            state = getattr(self, "_active_state", None)
            if state is not None:
                p = state.data.get("player") or {}
                origin = str(p.get("player_origin") or "").lower().strip()
                ik = p.get("identity_known")
                if isinstance(ik, bool):
                    identity_known = ik
        except Exception:
            pass
        if origin == "isekai":
            origin = "soul"
        if origin not in self._ORIGIN_NOTES:
            origin = "soul" if "穿越者" in player_summary else ""
        return origin, identity_known

    def _dynamic_context(self, player_summary: str, retrieved_context: str) -> str:
        origin, identity_known = self._resolve_player_origin(player_summary)
        note = self._ORIGIN_NOTES.get(origin, "")
        # "是否知道身份" = 与 4 档正交的修饰(肉穿无身份卡 → 不加此段)
        if note and origin != "body" and identity_known is not None:
            if identity_known:
                note += ("\n- 【开局·知道身份】玩家开局即清楚自己的身份及其社会位置、人际关系,"
                         "GM 可假定玩家了解本地身份背景。")
            else:
                note += ("\n- 【开局·不知道身份】玩家此刻并不清楚自己的身份(失忆/初醒/错乱)。"
                         "GM 不要直接告知身份,让玩家在互动中逐步拼凑;玩家对身份相关的人际关系、社会位置一无所知。")
        transmigrator_note = ("\n" + note) if note else ""
        return _DYNAMIC_CONTEXT.format(
            player_summary=player_summary,
            retrieved_context=retrieved_context or "（本轮无额外召回）",
            transmigrator_note=transmigrator_note,
        )

    def _turn_message(self, user_input: str, state, retrieved_context: str) -> str:
        return (
            f"{self._dynamic_context(state.short_summary(), retrieved_context)}\n\n"
            f"【玩家本轮输入】\n{user_input}"
        )

    def curate_context(self, agent_prompt: str, task_prompt: str) -> str:
        """Run the model-backed context sub-agent before the main GM call.

        task 68：Anthropic 用 native tool_use（input_schema 强校验），
        消除原 _parse_curator_json 的 re.search(r'\\{.*\\}') 兜底脆性。
        Vertex / OpenAI compat 继续走各自的 JSON mode（response_mime_type /
        response_format=json_object），那两条路径已经够稳。
        """
        messages = [{"role": "user", "content": task_prompt}]
        backend = self._backend
        # Anthropic backend 走 native tool_use
        if isinstance(backend, _AnthropicBackend):
            try:
                return _anthropic_curator_tool_use(
                    backend, agent_prompt, messages, max_tokens=900,
                )
            except Exception as exc:
                log.warning(f"[curator] native tool_use 失败，降级到文本 JSON：{exc}")
                # fallback to text JSON
        return backend.call_structured(agent_prompt, messages, max_tokens=900)

    # ── 生成开场白 ────────────────────────────────────────────────
    def generate_opening(self, state, retrieved_context: str = "") -> str:
        self._active_state = state
        system   = self._build_system()
        messages = [{"role": "user", "content": self._turn_message(_OPENING_PROMPT, state, retrieved_context)}]
        return self._backend.call(system, messages, max_tokens=600)

    def generate_opening_stream(self, state, retrieved_context: str = "", *, stop_event=None) -> Iterator[str]:
        self._active_state = state
        system   = self._build_system()
        messages = [{"role": "user", "content": self._turn_message(_OPENING_PROMPT, state, retrieved_context)}]
        for chunk in self._backend.stream(system, messages, max_tokens=600):
            if stop_event is not None and stop_event.is_set():
                return  # SSE 客户端已断开,提前退出
            yield chunk

    # ── 主响应 ────────────────────────────────────────────────────
    def respond(self, user_input: str, retrieved_context: str, state, max_tokens: int = 800) -> str:
        self._active_state = state
        system   = self._build_system()
        messages = state.history_messages()
        messages.append({"role": "user", "content": self._turn_message(user_input, state, retrieved_context)})
        return self._backend.call(system, messages, max_tokens=max_tokens)

    def respond_stream(self, user_input: str, retrieved_context: str, state, max_tokens: int = 800) -> Iterator[str]:
        self._active_state = state
        system   = self._build_system()
        messages = state.history_messages()
        messages.append({"role": "user", "content": self._turn_message(user_input, state, retrieved_context)})
        yield from self._backend.stream(system, messages, max_tokens=max_tokens)

    # ── 主响应（带 MCP 工具循环） ─────────────────────────────────
    def respond_stream_with_tools(
        self,
        user_input: str,
        retrieved_context: str,
        state,
        tools: list[dict[str, Any]] | None = None,
        max_iterations: int = 3,
        max_tokens: int = 800,
        tool_call_router: Any = None,
        stop_event=None,
    ) -> Iterator[dict[str, Any]]:
        """带 MCP 工具循环的流式响应。

        yields 事件字典：
          - {"type": "text", "text": "..."}
          - {"type": "tool_call", "server_id":..., "tool":..., "arguments":...}
          - {"type": "tool_result", "ok": bool, "result":..., "error":...}
          - {"type": "tool_error", "error": "..."}（解析失败）

        没有 tools 时退化成普通流式输出。

        task 66：backend 支持 native tool_use（Anthropic）时走 native 路径，
        否则用文本 marker 兜底。
        """
        if not tools:
            for chunk in self.respond_stream(user_input, retrieved_context, state, max_tokens=max_tokens):
                if stop_event is not None and stop_event.is_set():
                    return
                yield {"type": "text", "text": chunk}
            return

        # task 66：native tool_use 分支
        if getattr(self._backend, "supports_native_tools", False):
            yield from self._respond_stream_native_tools(
                user_input, retrieved_context, state, tools,
                max_iterations, max_tokens, tool_call_router=tool_call_router,
            )
            return

        self._active_state = state
        system = self._build_system() + _format_tools_for_prompt(tools)
        messages = state.history_messages()
        messages.append({
            "role": "user",
            "content": self._turn_message(user_input, state, retrieved_context),
        })

        START = "<<TOOL_CALL>>"
        END = "<<END_TOOL_CALL>>"
        tail_keep = max(len(START), len(END)) - 1
        accumulated_text = ""  # 用于把 assistant 回合拼回 messages

        for _iteration in range(max_iterations):
            if stop_event is not None and stop_event.is_set():
                return
            buffer = ""
            in_tool = False
            tool_invoked = False
            for chunk in self._backend.stream(system, messages, max_tokens=max_tokens):
                if stop_event is not None and stop_event.is_set():
                    return
                buffer += chunk
                while True:
                    if not in_tool:
                        start_idx = buffer.find(START)
                        if start_idx < 0:
                            # 没看到 start，但保留尾部 tail_keep 字符以防 marker 被切断
                            if len(buffer) > tail_keep:
                                emit = buffer[:-tail_keep]
                                buffer = buffer[-tail_keep:]
                                if emit:
                                    accumulated_text += emit
                                    yield {"type": "text", "text": emit}
                            break
                        # 看到 start：把前面的正文吐出
                        pre = buffer[:start_idx]
                        if pre:
                            accumulated_text += pre
                            yield {"type": "text", "text": pre}
                        buffer = buffer[start_idx + len(START):]
                        in_tool = True
                        continue
                    # in_tool: 找 END
                    end_idx = buffer.find(END)
                    if end_idx < 0:
                        # 等更多 chunk
                        break
                    tool_json_raw = buffer[:end_idx]
                    buffer = buffer[end_idx + len(END):]
                    in_tool = False
                    tool_invoked = True
                    try:
                        tool_data = json.loads(tool_json_raw.strip())
                        server_id = str(tool_data.get("server_id", ""))
                        tool_name = str(tool_data.get("tool", ""))
                        arguments = tool_data.get("arguments") or {}
                        if not isinstance(arguments, dict):
                            arguments = {}
                    except Exception as exc:
                        yield {
                            "type": "tool_error",
                            "error": f"工具调用 JSON 解析失败: {exc}",
                            "raw": tool_json_raw[:200],
                        }
                        # 失败也插回一条 user 消息，让 GM 自纠
                        messages.append({"role": "assistant", "content": accumulated_text + START + tool_json_raw + END})
                        messages.append({"role": "user", "content": "【系统】上一条工具调用 JSON 解析失败，请重新生成或放弃工具调用。"})
                        accumulated_text = ""
                        break
                    yield {
                        "type": "tool_call",
                        "server_id": server_id,
                        "tool": tool_name,
                        "arguments": arguments,
                    }
                    try:
                        from mcp_broker import call_tool as _mcp_call_tool
                        result = _mcp_call_tool(server_id, tool_name, arguments)
                    except Exception as exc:
                        result = {"ok": False, "error": f"call_tool 异常: {exc}"}
                    yield {
                        "type": "tool_result",
                        "ok": bool(result.get("ok")),
                        "result": result.get("result"),
                        "error": result.get("error"),
                    }
                    # 把"前缀正文 + 工具调用块"作为 assistant 写回，工具结果作为 user 写回，准备续生成
                    assistant_msg = accumulated_text + START + tool_json_raw + END
                    messages.append({"role": "assistant", "content": assistant_msg})
                    truncated_result = json.dumps(result, ensure_ascii=False)[:2000]
                    messages.append({
                        "role": "user",
                        "content": (
                            f"【工具结果：{server_id}/{tool_name}】\n{truncated_result}\n\n"
                            f"请基于工具结果继续本轮回应（不要重复正文，可继续描写或追加状态标签）。"
                        ),
                    })
                    accumulated_text = ""
                    break  # 跳出 while，开始下一轮 backend.stream
                if tool_invoked:
                    break
            if not tool_invoked:
                # task 61：在 fall-through 前先检查"开了 TOOL_CALL 但没收到 END"
                # 这是 LLM 输出 <<TOOL_CALL>>{...} 但忘 <<END_TOOL_CALL>> 的常见错误。
                # 之前的 buffer（含未闭合的 JSON 片段）会被当成 text 吐给用户，
                # LLM 完全不知道工具没真调用 → 以为"成功"继续叙事 → 状态彻底乱。
                if in_tool:
                    yield {
                        "type": "tool_error",
                        "error": "工具调用未闭合：找到 <<TOOL_CALL>> 但流结束前没有 <<END_TOOL_CALL>>。重新生成时请把 marker 写完整。",
                        "raw": buffer[:200],
                    }
                    # 把不完整片段塞回 messages，告诉模型重试
                    messages.append({"role": "assistant", "content": accumulated_text + START + buffer})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【系统】上一条工具调用未闭合（缺 <<END_TOOL_CALL>> 结束 marker）。"
                            "请重新输出完整的 <<TOOL_CALL>>{\"server_id\":\"...\",\"tool\":\"...\",\"arguments\":{...}}<<END_TOOL_CALL>>，"
                            "或放弃工具调用直接续写叙事。"
                        ),
                    })
                    accumulated_text = ""
                    continue  # 进下一轮 iteration 让 LLM 重试
                # 正常 fall-through：buffer 直接吐出（无任何 marker）
                if buffer:
                    yield {"type": "text", "text": buffer}
                return
        # 达到 max_iterations：给个收尾提示
        yield {"type": "text", "text": "\n\n【已达本轮工具调用上限 (限制为本次回复内的调用次数,下一条消息自动重置),本轮终止】"}

    # ── 主响应（带 MCP 工具循环 · native tool_use 路径） ───────────
    def _respond_stream_native_tools(
        self,
        user_input: str,
        retrieved_context: str,
        state,
        tools: list[dict[str, Any]],
        max_iterations: int,
        max_tokens: int,
        tool_call_router: Any = None,
    ) -> Iterator[dict[str, Any]]:
        """task 66/70/71：用 backend 的 native tool_use / function calling API
        跑 MCP 循环。

        优点（相对 text marker 协议）：
        - input_schema 由 SDK/provider 校验，错误率降 5-10×
        - 不需要在正文里塞 <<TOOL_CALL>> marker → 节省 tokens
        - 不会有"marker 被切断 / 未闭合"问题（task 61 修的就是这类）

        每个 backend 拥有自己的 stream_with_mcp_loop()，封装：
        - MCP tool 列表 → 该 provider 原生格式
        - 流式 event 解析（content blocks / parts / delta.tool_calls 各家不同）
        - assistant + tool_result 消息装回历史的 provider-specific 格式
        本方法只是 dispatcher。
        """
        self._active_state = state
        system = self._build_system()
        messages = state.history_messages()
        messages.append({
            "role": "user",
            "content": self._turn_message(user_input, state, retrieved_context),
        })
        # 没有 tools 时退化
        if not tools:
            for chunk in self._backend.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return
        # task 87 Phase 5: tool_call_router 默认是 mcp_broker.call_tool,
        # 但 chat handler 可以传入 unified router (识别 dispatcher 工具 + MCP 工具)
        if tool_call_router is None:
            from mcp_broker import call_tool as _mcp_call_tool
            tool_call_router = _mcp_call_tool
        yield from self._backend.stream_with_mcp_loop(
            system, messages, tools, max_iterations, max_tokens, mcp_call=tool_call_router,
        )
