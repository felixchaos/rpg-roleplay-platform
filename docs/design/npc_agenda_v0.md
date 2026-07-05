# NPC 议程 v0(活世界·柱子3,改道后正案)

目标:NPC 从「布景」变「角色」——每个活跃 NPC 带一份持续演化的议程(此刻想要什么/
对玩家什么态度),GM 生成时看得见,跨回合连续。改道背景:原方案(人物卡 motivation
接线)被证实是「写了没存」的死字段(生产 150 卡采样零持久化),不基于死字段建设;
本案做**存档级动态议程状态**,与静态人物卡正交(卡=出厂设定,议程=当下活状态)。

原则(柱子1/2 同一套纪律):
- 存储/上限/去重/剪枝/注入 = 确定性代码;LLM 只写议程内容。
- flag `npc_agenda` 默认关;前端开关同批交付。
- 写穿史官权威通道(recorder_unified 下 GM fence 被剥弃的教训:只教 GM=白教),
  prompt 与 tool-schema 同源门控(parity 铁律)。

## 1. 状态结构

`state.data["npc_agendas"]: dict[str, dict]`,键=NPC 名(与 relationships 键同源):

```json
{
  "雷纳德": {
    "goal": "查清东林兽伤的真相,保住村子的猎场",
    "stance": "对玩家信任但保留观察",
    "updated_turn": 15
  }
}
```

- 上限 12 个 NPC;超出时剪掉 updated_turn 最旧的(活跃度自然淘汰)。
- 单条 goal/stance 各 ≤60 字(验收截断);键必须已存在于 relationships 或
  active_entities(防 LLM 发明路人,确定性校验)。

## 2. 写入通道(史官 ops 扩展,与 consequence 同款形状)

- 新 op:`{"op":"agenda","name":"雷纳德","goal":"...","stance":"..."}`;
  goal/stance 至少给一(部分更新合并,不整条覆盖)。
- recorder:`_SYSTEM_OPS_AGENDA` 提示块 + tool-schema enum/name/goal/stance 字段,
  `agenda_enabled` 参数与 consequence_enabled 同源传法(record_turn 内读 flag 一次
  传 prompt 与 schema 两处);提示要点:「只为**本回合言行透露了新意图/态度变化**的
  NPC 更新,每轮 ≤2 条,无变化不输出;goal 写角色自己想要的,不是玩家任务」。
- GM 侧 `_CONSEQUENCE_GUIDE` 同款:`_AGENDA_GUIDE` 经 flag 门控追加 system prompt
  (让 GM 叙事时就体现意图连续性,史官读正文更可靠转录)。
- apply_structured_updates 新分支(question/consequence 旁),薄封装调纯函数。

## 3. 纯函数核心(rpg/state/npc_agenda.py)

- `upsert_agenda(state_data, name, goal, stance, turn) -> (ok, msg)`:名字校验
  (relationships ∪ active_entities 成员)/截断/合并/上限剪枝。
- `agendas_for_injection(state_data, relationships_keys, limit=6) -> list`:
  只取与当前在场相关的(键在 relationships 里的优先,按 updated_turn 降序)。

## 4. 注入(新 context provider)

`rpg/context_providers/npc_agenda.py`,id="npc_agenda",priority 76(紧贴 npc_cards
78 之下),novel+freeform 双 manifest,flag gate 同款:

> 【NPC 议程(当下活状态,优先于人物卡的静态设定)】
> - 雷纳德:想要「查清东林兽伤真相」;对玩家「信任但保留观察」(第15回合更新)

无条目 skip;最多 6 条。

## 5. Flag 与前端

`core/feature_flags.py`:`"npc_agenda": ("RPG_NPC_AGENDA", "0")`;
agent-modules FEATURES(group world)+ zh/en i18n + settings 两端 fallback,全套照抄。

## 6. 测试

- 纯函数:名字不在册拒/截断/合并部分更新/上限剪最旧/injection 排序与上限。
- recorder parity 守卫:agenda_enabled 开=prompt+schema 都有,关=都无(consequence
  同款测试复制)。
- apply 分支:合法/缺 name 拒/goal+stance 至少一。
- provider:gate 关 skip/无条目 skip/渲染含「当下活状态」。

## 7. 验收(探针局)

save 347:开 flag 玩 2-3 回合与雷纳德/艾琳互动 → 史官登记议程 → 下回合注入层出现
→ 后续回合 NPC 行为体现议程连续性(艾琳记得自己想去石桥渡的动机)。

## 8. v0 不做

- 议程驱动的主动行为(那是心跳 v1 的线程制);
- 人物卡 motivation 列(死字段不复活);
- 议程冲突消解/NPC 间关系图。
