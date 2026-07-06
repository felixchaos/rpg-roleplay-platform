# RATH 仿真核心 v2(状态优先重建)

## §0 v1 的架构性缺陷(用户逐条实锤后的定论)

v1 = 逐拍即兴场景生成器:每拍选两人 → LLM 即兴一场 → 出口滤网(名词闸/神圣条款)拦跑偏。
**病根:让写散文的 LLM 同时决定"发生什么"。** 没有持续世界状态(只有文字日志)、没有
agent(角色是被抽签召唤的提线木偶)、没有因果链(每场近乎无状态)。滤网是补丁,不是地基。

## §1 核心倒置:状态是本体,文字是投影

每实验持有 **sim_state**(rath_experiments.sim_state jsonb,migration 95),仿真的唯一读写源:

```
sim_state = {
  clock_min,                                  # 与实验世界钟同步
  cast: { name: {                             # 角色 = 有状态的 agent
      kind: "player"|"npc",
      sheet,                                  # 设定摘要(玩家=identity;NPC=卡片时间中性字段)
      location, activity,                     # 此刻在哪、在做什么
      goal, mood,                             # 持续意图(可演化)与情绪
      status,                                 # 如「昏迷」——状态守恒的依据
      memory: [..≤10]                         # 私记
  }},
  places: [..],                               # 已知地点白名单
  facts: [..≤40],                             # 已确立事实(append-only,散文的接地材料)
  threads: [{id, desc, tension0-10, participants}],  # 剧情线:开放张力,推进的载体
}
```

## §2 仿真环(每拍)

```
认领(CAS,同v1) →
① 时间推进(确定性):clock+=Δ;夜间(23:00-06:00)角色 activity 强制→睡眠(除非相关线 tension≥8)
② 调度(LLM-A,一次调用):输入=sim_state 紧凑视图+最新引导 → 输出严格 JSON:
   { cast_updates:{name:{location?,activity?,goal?,mood?}},       # 每人此窗口的意图与去向
     interaction:{participants[2],place,reason,expected_outcome}|null,  # 本拍是否有值得成戏的相遇
     world_events:[≤1条,必须影响 cast/舞台],
     thread_updates:[{id,tension_delta,note}], new_threads:[≤1], new_facts:[≤2] }
③ 裁决(确定性代码,单写者):逐字段验收后落 sim_state——
   cast_updates 键∈cast;location∈places(新地点走名词闸);玩家角色状态守恒(昏迷→活动
   只能是沉睡/生理反应,不得参加 interaction 主动方);全部文本过名词闸(白名单=worldbook
   +facts+cast+places);tension 夹 0-10;facts 封顶滚动。拒收字段丢弃并记运行日志。
④ 呈现(LLM-B,仅当 interaction 存在):输入=参与者状态+reason+expected_outcome+相关 facts
   → 只负责把【已裁决的事】写成对白与纪要。验收:speaker∈participants+名词闸+状态守恒。
   **散文层无权决定情节** —— 这是治歪的地基,滤网降级为保险丝。
⑤ 落库:sim_state 持久化;scene/heartbeat→rath_events+kb_events(召回闭环不变);
   全程相位 trace(运行日志,同v1)。
```

## §3 引导 = 结构注入

引导(directive 事件)不再只是 prompt 后缀:调度器被要求把最新引导**翻译成结构**
(thread 的开设/张力调整/goal 修正),裁决层落库后即成为世界的一部分——引导改变的是
状态,状态再长出文字。

## §4 初始化(实验创建/重置时)

- cast:玩家(来自快照 player,识别昏迷类 status)+ canon 卡司(importance≥100,
  first_revealed_chapter≤进度+3,personality/appearance 时间中性字段);
- places:玩家当前位置+世界书地点类条目;
- facts:从快照与最近正史事件确定性种 3-6 条(玩家在哪/什么状态/开场概要);
- threads:确定性种子规则(如玩家昏迷 → 「昏迷少女的照料与来历」tension 5)。

## §5 不变量(全部沿用 v1 已验证的地基)

离线绝不写游戏 state;产物落 kb_events(召回闭环)+rath 表;CAS 认领;预算闸;
玩家设定神圣(裁决层代码强制,不再只靠 prompt);运行日志逐相位;UI 不变
(日志/角色动态/运行日志/引导),角色动态直接读 sim_state.cast=永远有内容且真实。

## §6 成本

每拍 LLM 2 次(调度+呈现,无相遇时 1 次),与 v1 持平。

## §7 基础设施复用清单(用户实锤「基础设施一个没用」后的对账,2026-07-06)

已复用:`agents._harness.call_agent_json`(LLM调用+token_usage记账)/`_resolve_recorder_api_and_model`
(BYOK)/`kb.live_repo.record_event`(kb_events→情景召回闭环)/`core.json_parse.parse_llm_json`
(v1.60.2 收口,此前 sim.py 手写过两份解析)/`get_progress_window`(进度权威,v1.60.2 收口,
此前土算 progress_chapter)/世界书查询过 reveal 门控(v1.60.2 收口,此前裸查有剧透泄漏面)。

已知平行结构(记为债务,不急改):sim_state.cast 的 goal/mood/memory 与柱子3 npc_agendas /
私记格式平行——设计取舍(离线绝不写游戏 state),但字段命名与验收器应逐步对齐;
find_fabricated_nouns 与 black_swan validator/npc_extract L2 同族,未来可抽公共验收层。

教训:新子系统动工前先列「平台已有能力清单」,能 import 的绝不重写。
