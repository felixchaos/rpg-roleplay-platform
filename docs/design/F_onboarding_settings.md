# Phase F — 创建引导 + 设置模型(重设计 NewGameModal)

> 实施级设计。把建档从"一个表单"升级成**分步向导**,把影响体验的关键选择显式交给玩家(都有默认),
> 并明确**游戏中可改 vs 锁死**的边界。
>
> 现有锚点:`frontend/src/pages/saves.jsx`(NewGameModal/ContinuePicker)、
> `game_saves` / `game_sessions`(state/memory_mode/permission_mode/model_name)、
> `character_cards`/`user_character_cards`/personas、`_build_initial_snapshot`(建档初始化,W13 已移植)。

---

## 1. 分步向导(5 步,都有默认,高级可折叠)
| 步 | 选什么 | 落到哪 |
|---|---|---|
| 1 选剧本 + 起始世界线 | script_id + `script_worldlines.wl_key`(默认 main) | `game_saves` + 初始 steering 起点 |
| 2 角色 | 角色卡 / persona / 出生点 / 身份(复用 4 级优先链) | `_build_initial_snapshot` 输入 |
| 3 元知识模式 | `foreknowledge_mode`(none/partial/omniscient)+ `npc_awareness`(oblivious/suspicious) | `game_sessions.metadata` / state 设置(§D-8) |
| 4 引导强度 + 防剧透 | `steering_strength`(铁轨↔自由)+ `spoiler_guard`(严/松) | session 设置 |
| 5 记忆/模型/权限 | memory_mode / model_name / permission_mode(**已有**) | `game_sessions`(复用) |
- 每步有合理默认 → 玩家可一路 Next 用默认直接开;高级项默认折叠

## 2. 设置模型:可改 vs 锁死
| 类别 | 项 | 游戏中 | 理由 |
|---|---|---|---|
| **可改** | 引导强度 / 防剧透 / 记忆模式 / 模型 / 权限 / NPC察觉 / 先知模式 | ✅ 随时 | 体验旋钮,不破坏世界树一致性 |
| **锁死** | 剧本 / 起始世界线 / 角色身份 | ❌ 灰显+提示 | 改了会损坏已积累的世界树(commit 谱系 + kb_* 行假设了固定起点) |
- 锁死项在游戏内设置面板**灰显** + tooltip 说明"建档时确定,改动会破坏存档";要换 = 新建存档
- 实现:设置项加 `locked_after_create: bool` 元数据,前端据此渲染

## 3. 设置如何接入 GM
- 可改项写 `game_sessions`(或 state 设置块),每回合 GM 注入时读(§D §3①/§5/§7/§8)
- `steering_strength` 调 `resolve_steering_target` 的软目标强度(铁轨=强提示+主动重锚;自由=弱提示+少干预)
- `spoiler_guard` + `foreknowledge_mode` 调 `resolve_world_view` 的过滤宽度(§D §7/§8)

## 4. 落地改动清单
- **改** 前端 `saves.jsx` — NewGameModal → 分步向导组件(`NewGameWizard`),5 步;游戏内设置面板渲染 locked 灰显
- **改** 后端建档端点 — 接收 worldline/foreknowledge/npc_awareness/steering_strength/spoiler_guard,存 `game_sessions` + 传 `_build_initial_snapshot`
- **复用**:角色 4 级优先链 / memory/model/permission / `_build_initial_snapshot` 全不重写

## 5. 验收
- 一路默认能开局(零额外选择)
- 选 omniscient + suspicious → GM 叙事体现穿越者全知 + NPC 起疑
- 游戏内试图改"起始世界线"被灰显拦截 + 提示;改"引导强度"立即生效
