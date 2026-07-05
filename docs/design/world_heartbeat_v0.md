# 世界心跳 World Heartbeat v0(活世界·柱子1)

目标:世界在玩家不注视的地方也在动。生产基线局实证:过夜=纯氛围零事件,「爪印」是被
玩家问出来的即兴发挥、不问就不存在。心跳让世界侧事件确定性地产生、留存、并以
传闻/痕迹/路人交谈的形式自然浮出。

与后果账本(柱子2)的分工:**后果=玩家种的因(有明确到期),心跳=世界自己的事(无因果绑定)**。
两者互不读写。

原则(与柱子2同一套纪律):
- 调度/预算/去重/防剧透/注入 = 确定性代码;LLM 只写事件文案。
- flag `world_heartbeat` 默认关;前端开关同批交付。
- **零新持久化机制**:写进活 state.data,由本回合 Phase 5 统一持久化(见架构决策)。

## 0. 架构决策(v1.41.1 勘误后定案)

⚠️ 勘误(生产实测抓出):`_run_post_gm_parallel` 是 **sync 模式专用路径**(生产默认
RPG_POSTPROC_MODE=async 在其之前早退),v1.41.0 只接在那里导致生产永不触发——
灰度双路径老坑。定案:**执行点 = async 早退分支内、与史官三合一并行**
(asyncio.create_task(to_thread) 起在史官之前、两个 return 前 await,保证写完 state
再进 Phase 5 持久化);sync 路径的 `_run_post_gm_parallel` 接线保留作 parity,
两路都活。原选点理由(仍成立,只是路径找错了):
- postproc worker 进程侧「无法安全访问实时 state」(run_postproc_worker.py:101 的
  black_swan handler 因此 enable_llm=False)——心跳若走 worker 会重蹈跨 worker 写状态坑。
- post-GM 并行段在 Phase 5 持久化之前、与 extractor 并行,墙钟开销≈0(心跳调用
  token 少,必然短于 extractor)。
- 代价(诚实声明):世界推进量绑定玩家回合数,不是真实时间。真·离线自转留给 v1。

## 1. 状态结构

`state.data["background_events"]: list[dict]`:

```json
{
  "id": "bg_a1b2c3",
  "text": "村东磨坊主的驴昨夜挣脱缰绳跑进了麦田,踩坏了半垄麦子",
  "created_turn": 12,
  "surfaced_turn": null
}
```

- 上限 12 条未浮出(超出拒收);指纹去重复用 `state.consequence_ledger._normalize_for_fp`
  同思路(去标点归一化)。
- 已浮出(surfaced_turn 非空)条目保留 5 回合后确定性剪除(防膨胀;GM 叙事过的内容
  会被史官自然收进 memory.facts,无需长存)。

## 2. 产生(心跳 tick)

新文件 `rpg/agents/world_heartbeat.py`:

- `should_tick(state_data, user_id) -> bool`(纯函数):flag 开 && `state.turn >= 4`
  (前 3 回合让故事立足) && `turn - heartbeat_meta.last_tick_turn >= 3`(K=3 节流)
  && 未浮出条目 < 8(积压多就别再产)。
- `run_heartbeat_tick(state, user_id, api_id_override=None, model_override=None) -> list[str]`:
  一次便宜 LLM 调用(模型解析复用 `agents.recorder._resolve_recorder_api_and_model` 的
  同款用户级解析——史官用什么它用什么,严格 BYOK),产 1-2 条世界侧事件。
- **输入材料(全部是已揭示/揭示窗口内的,防剧透)**:state 快照(时间/地点/天气)、
  memory.facts 最近 10 条、relationships 键名、active_entities 的 name+disposition、
  最近 3 条 background_events(避免重复方向)。**不喂原著 RAG、不喂未到锚点的正文**;
  可选喂 pending_anchors 的 summary ≤2 条(锚点窗口本身已过揭示策略,作为「世界正在
  酝酿什么」的方向暗示)。
- prompt 要点:「写玩家**不在场处**正在发生的 1-2 件小事(村庄级/配角级/环境级),
  与已知事实一致、与在场剧情无直接因果;每条 ≤80 字;禁止提到玩家本人;禁止重大
  剧情转折(那是锚点的事);输出严格 JSON 数组 ["...", "..."]」。
- **确定性验收(代码,不信 LLM)**:逐条拒绝——空串/>120字/含「你」或玩家名/与现存
  条目指纹重复;全拒则本 tick 空手而归(正常,不重试)。
- 通过的条目 append 进 `background_events` + 更新 `heartbeat_meta.last_tick_turn`,
  直接改 state.data(本回合 Phase 5 统一落库,与 extractor 同命运)。
- 全程 try/except:任何失败静默跳过(log.debug),绝不破回合。

## 3. 注入(新 context provider)

`rpg/context_providers/world_pulse.py`,id="world_pulse",priority 55(低于 memory 60,
高于历史摘要 48),novel + freeform 双 manifest,flag gate 同 consequence_echo 口径。

collect():取最多 2 条最旧的未浮出条目,标 `surfaced_turn=当前turn` + 执行过期剪除,
渲染:

> 【世界脉动·你不在场时】以下是世界里同期发生的小事,可择其一以传闻/路人交谈/
> 环境痕迹的方式自然浮现(不强求本回合全用,禁止生硬播报):
> - 村东磨坊主的驴……

无条目 → skipped。

## 4. Flag 与前端

- `core/feature_flags.py`:`"world_heartbeat": ("RPG_WORLD_HEARTBEAT", "0")`。
- `frontend/src/agent-modules.js` FEATURES(group "world")+ zh/en i18n + settings 两端
  fallback 文案(照 consequence_ledger 条目全套)。

## 5. 接线

`_run_post_gm_parallel` 增加 `_worker_heartbeat`:内部先 `should_tick`,不该跳就
立即返回(零成本);该跳则 `asyncio.to_thread(run_heartbeat_tick, ...)`。
与 extractor/black_swan 同级 gather,互不等待。

## 6. 测试(不依赖 DB)

- should_tick:flag关/回合<4/间隔不足/积压≥8 各拒,全条件满足才 True。
- 验收器:超长/含「你」/含玩家名/重复指纹 逐条拒,合法条目过。
- 上限:未浮出 12 拒收;过期剪除:surfaced 超 5 回合被剪,未浮出不剪。
- provider:gate 关 skip、无条目 skip、注入标 surfaced、一次最多 2 条、模板文案含
  「不强求」;注入后再 collect 不重复给同条。
- prompt 构造:材料含 facts/active_entities、不含「你」外的玩家指称约束文本存在。

## 7. v0 明确不做

- 真·离线时钟自转(worker 侧安全写穿是前置,v1)。
- 持久 NPC 议程线(柱子3 motivation 接线后再演进为线程制)。
- 心跳事件与后果账本的联动。
