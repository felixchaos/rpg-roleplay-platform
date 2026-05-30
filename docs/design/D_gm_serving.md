# Phase D — GM serving(有界 agentic loop + 三层注入 + 规范世界线引导 + 抗污染/防剧透/元知识)

> 实施级设计。把 `chat_pipeline` 的固定 5 阶段升级成**回合内有界 tool-calling loop**:
> GM 一个回合内可多次查询 KB / 写世界树 delta,直到 `end_turn` 自终止。
>
> 现有锚点:`chat_pipeline.py`(5 阶段)、`command_dispatcher`(ToolSpec/scope/origin/审计/锁/MAX_TRACE_DEPTH=3)、
> `state_op_tool_map`(GM `{op,path,value}` → 工具)、`save_anchor_states` + `mark_anchor_satisfied/superseded`、
> `worldbook_entries`(insertion_position)、`retrieval.bm25_search`、`state/core.py` short_summary(已剥 secrets)。

---

## 1. 有界 agentic loop(替换固定 5 阶段)
保留 5 阶段的**外壳**(directives → context → GM → apply → persist),但把"GM"那一阶段从"单次流式"升级成 loop:
```
turn(user_input):
  Phase1 apply_player_directives           # 复用现有(/set → dispatcher)
  Phase2 assemble_context                  # §3 三层注入 第①层(常驻)
  Phase3 GM LOOP (max_turns=3, budget=$0.05):
     step:
       model 生成(流式叙事 + 可选 tool_use)
       if tool_use:
         dispatch(env)  # §4 查询/写工具,走 command_dispatcher
         feed tool_result back → 继续 loop
       if end_turn / 无 tool_use:
         break
  Phase4 apply_pending_ops                  # 收尾:残留 JSON ops(复用 state_op_tool_map)
  Phase5 persist                            # 复用 record_runtime_turn → 新 commit + kb_* 行已就位
```
- **终止**:模型 `end_turn` 自停;硬护栏 `max_turns≈3`(防绕圈)、`max_budget_usd≈0.05`/回合(BYOK,用户掏钱,Agent SDK 实证)
- **中断**:用户中途打断 → 取消未完 step,保已落 delta
- **BYOK 计费**:每 loop step 走用户自己的 key(`resolve_api_key`),平台零成本
- 实现:`chat_pipeline.run_gm_phase` 改成 loop;工具调用复用 `ToolDispatcher.dispatch_sync`(已支持流式 handler)

---

## 2. 与现有工具框架对接
GM loop 的工具 = 注册到 `command_dispatcher` 的 ToolSpec,`origin='llm_chat'`。
- 查询工具:`scope='script'`(读规范层,executor 签名 `(user_id, script_id, args, state)`)
- 写工具:`scope='save'`(写活态层,executor 签名 `(state, args)`,内部 INSERT `kb_*` 打当前 commit 戳)
- 审计/限流/per-save 锁/MAX_TRACE_DEPTH 全部由 dispatcher 现成提供

---

## 3. 三层注入

### ① 常驻层(每轮无条件,prompt 缓存)— 决策2
组成(预算 **per-script 计算 + 封顶 ~3K token**,不是固定值):
- **世界观骨架**:`worldbook_entries WHERE insertion_position='constant'`(纪元/力量体系/主要派系/当前弧)——治 1935
- **当前场景**:`state.scene` + `state.player` 摘要(轻量运行态)
- **下一规范世界线锚点(软目标)**:`resolve_steering_target(save)`(§5)给出"玩家最近哪条线、下一节点是什么"的**软提示**,不是铁轨指令
- prompt 缓存:常驻层稳定 → Anthropic prompt caching 命中,省钱省延迟

### ② 按需查询工具(GM 主动拉,不预注入长尾)
| 工具 | 作用 | 实现 |
|---|---|---|
| `search_canon(query, k)` | 语义检索规范 lore | pgvector cos on `kb_canon_entities.embedding` + worldbook(vector 模式)+ `retrieval` 混合 |
| `lookup_entity(name)` | 取实体详情(含活态覆盖) | `kb/view.resolve_world_view` 单 key |
| `lookup_timeline(label?)` | 查时间线锚点 | `script_timeline_anchors` |
| `lookup_lore(topic)` | 查世界书条目 | `worldbook_entries`(keyed/vector) |
| `graph_neighbors(entity)` | 关系图邻居 | `kb_relationships` + Pass4 图 |
- **全部按玩家进度过滤**(§6 防剧透):查询结果先过 `first_revealed_chapter <= progress_chapter`

### ③ 结构化写工具(= 世界树 delta,走 dispatcher save-scope)
| 工具 | 作用 |
|---|---|
| `upsert_entity(logical_key, ...)` | 新造/改实体 → INSERT `kb_entities` 行(born_commit=当前) |
| `record_event(summary, participants, ...)` | 记事件 → `kb_events` |
| `set_relationship(from, to, kind)` | 关系 → `kb_relationships`(复用现有 `set_relationship` 语义) |
| `set_worldline_var(key, value)` | 世界线变量 → `kb_worldline_vars`(复用现有 `set_user_variable`) |
| `mark_anchor_satisfied / superseded` | **复用已有**收束工具(drift_score/fatal 逻辑现成) |
- 现有 `state_op_tool_map` 继续兜底:GM 若输出 `{op,path,value}` 仍能映射到这些工具

---

## 4. 注入预算算法(治"800 token 拍脑袋")
```
budget(script) = clamp(base + per_constant_entry * n_constant, 800, 3000)   # per-script 计算
```
- 常驻层填到预算上限,按 `importance` 降序截断;prompt 缓存命中后边际成本低
- 查询工具返回也设单次上限(防一次拉爆),GM 多轮拉而非一次灌

---

## 5. 规范世界线引导 `resolve_steering_target(save)`(决策1:粗引导)
每轮四步,产出 ① 层的"软目标":
1. **定位**:玩家当前在哪条 `script_worldlines` 线、最近通过哪个 `script_worldline_nodes` 节点(看已 `occurred` 的 `save_anchor_states` 簇匹配哪个节点的 `anchor_keys`)
2. **引导**:把"下一节点 summary + must_preserve"作软目标注入常驻层(口吻:朝这个方向,不是必须照搬)
3. **放权**:**怎么达成交玩家**——GM 即兴,局部自由
4. **重锚**:玩家明显偏到另一条规范枝叉 → 切到那条线的锚点;完全脱稿 → 即兴 + 尽量收束回最近节点
- 火候分类(GM 自判):**局部偏移**(同节点内细节差异)→ 放行;**跨线偏移**(走向另一条规范线)→ 重锚;**脱稿**(规范图里没有)→ 即兴 + `mark_anchor_superseded`(非 fatal 才行,复用现有 fatal 守卫)
- 复用 `_list_pending_anchors`(已有)拉候选锚点 + `drift_score` 量化偏移

---

## 6. 抗提示词污染(关键)
1. **影响因子量化**:每个玩家动作/事件按"爆炸半径"分级 `impact ∈ {local, scene, faction, world}`。绝大多数是 local(对话/移动)→ **零世界推演,零污染**
2. **高影响触发带外世界推演子代理**:仅当 `impact >= faction`,异步起一个**隔离上下文**子代理算涟漪(谁会知道、势力关系怎么变),它**只输出结构化 delta**(调写工具),叙事 GM **看不到它的推理过程** → 推理不污染叙事 prompt
3. **结构化状态非散文**:世界态不是越滚越长的散文,而是 `kb_*` 行级现状;每轮 `resolve_world_view` 解析成**紧凑现状**注入(O(当前可见实体),不 O(历史))
4. **检索而非注入**:长尾 lore 靠 ② 查询工具拉,不预灌
→ 叙事 GM 的 prompt = 紧凑现状 + 查到的具体规则 + 当前锚点软目标,**始终精简稳定**(利于 prompt 缓存)

---

## 7. 防剧透(决策3:已揭示集合)
- 每存档 track `progress_chapter`(玩家故事已达的最大规范章;随剧情推进/通过锚点更新)
- 常驻 lore + 所有查询结果先过 `first_revealed_chapter <= progress_chapter` 过滤
- 玩家自造活态行(`kb_entities.origin='player'`)不过滤(玩家自己的)
- **GM 行为约束**(prompt 硬规则):NPC 绝不表现得"知道玩家知道未来剧情",除非元知识设置允许

---

## 8. 元知识(穿越者先知 + NPC 察觉)— 可切换设置
- `foreknowledge_mode ∈ {none, partial, omniscient}` 调**已揭示集合宽度**:
  - `none`:严格 `progress_chapter`(玩家和角色同步,无先知)
  - `partial`:`progress_chapter` ∪ 标记为"著名未来事件"的 KB 行(`metadata.famous=true`)——穿越者模糊知道大事
  - `omniscient`:不过滤(穿越者全知原著)
- `npc_awareness ∈ {oblivious, suspicious}`:NPC 是否察觉玩家异常先知 → 影响 GM 叙事(suspicious 时 NPC 会起疑)
- 玩家私有先知存 `state.player_private`(已有,short_summary 已剥,不进 GM 叙事 prompt;仅供规则判定)

---

## 9. 落地改动清单
- **改** `chat_pipeline.py` — `run_gm_phase` → 有界 loop(max_turns/budget/中断);Phase2 调 `assemble_context`(三层①)
- **新建** `rpg/gm/context_inject.py` — 三层注入 + 预算算法(§3/§4)
- **新建** `rpg/gm/steering.py` — `resolve_steering_target`(§5)
- **新建** `rpg/gm/impact.py` — 影响因子分级 + 带外推演子代理触发(§6)
- **新建/注册** 查询/写工具 ToolSpec(`rpg/tools_dsl/command_tools_kb.py`),走 `command_tools_register.ensure_registered`
- **改** `kb/view.resolve_world_view` 加 `progress_chapter` + `foreknowledge_mode` 过滤(§7/§8)
- **复用**:`command_dispatcher` / `state_op_tool_map` / `mark_anchor_*` / `worldbook_entries` 全不重写

## 10. 验收 / 测试
- 纪元:GM 全程纪元 = 2930s,绝不出 1935(Phase H 回归)
- loop:多轮工具调用收敛(end_turn),max_turns/budget 护栏触发正确
- 防剧透:`progress_chapter` 之后的事件不出现在 GM 回复;切 `omniscient` 才出现
- 抗污染:high-impact 动作触发带外推演、写 delta;low-impact 零额外调用;叙事 prompt 长度稳定不随回合数膨胀
- 分支隔离:两分支 GM 看到各自世界(`resolve_world_view` 正确)
