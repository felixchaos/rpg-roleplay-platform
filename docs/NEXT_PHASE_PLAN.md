# 下一阶段精确计划(v1 · 2026-05-30)

> 接续:架构主线 Phase A.0→H 后端/数据/逻辑已完成 + live 验证(~33 commit / 38 测试)。
> 本计划覆盖:实体类型偏斜调优、uvicorn 扩容、隐私合规 Route-B 收尾、G 运营护栏、懒/增量提取、E/F 前端。
> 排序原则:**质量修复 → 可 live 验证的后端 → 合规 landmine → 成本架构 → 前端(需浏览器)**。

---

## W1 · 实体类型偏斜调优(Phase A 质量,最先)
**问题**(Phase H 实测):105 规范实体里 99 是 concept、character 仅 1。角色被严重少抽/过度合并。
**根因候选**:① Pass1 prompt 把人/地/组织也标成 concept;② `cluster_entities` 嵌入阈值 0.86 把不同角色名合并;③ Pass0 自举词表 concept 偏多。
**改动**
- `extract/per_chapter.py`:prompt 强化类型纪律——character=具名人物;faction=组织/势力;location=地点;concept=**仅**抽象设定/力量体系/专有名词,**禁止**把人/地/组织塞进 concept。加 few-shot 正反例。
- `extract/resolve.py`:character 类型聚类阈值收紧(或要求 surface 重叠才合并),避免把不同角色并成一个;concept 去重照旧。
- `extract/seed.py`:自举 NER 输出按类均衡(characters/factions/locations 各自取够)。
**验收(live,~25 章样本)**:character 进**两位数**且与原著主要人物对得上;type 分布合理(角色驱动小说 character ≥ concept 或至少 character 数十);抽 10 章人工核对 precision。

## W2 · uvicorn workers + 热路径缓存(扩容)
- **W2-a**:`deploy/Dockerfile`/`compose` CMD → `uvicorn app:app --workers 4`(吃满 6 核)。
  - ⚠️ **先查 SSE 停止/取消机制**(`chat_pipeline` 的 `stop_event`/`run_id`):若是进程内字典,多 worker 下跨 worker 失效 → 改 DB/共享信号。
  - 核对:`pool_max(10)×4=40 < postgres max_connections(100)` ✅。
- **W2-b**:`gm_serving/context_inject.build_constant_layer` 按 script_id 加 TTL 缓存(常驻骨架每回合相同)。
**验收**:4 worker 起;30 并发 SSE 不串台;停止键跨 worker 生效;常驻层二次组装 < 5ms。

## W3 · 隐私合规 Route-B 收尾(法律 landmine)
> 已完成:`legal/ROUTE_B_DATA_HANDLING.md`(工程真相+逐节 redline)+ EN 隐私 §3.B/§3.F/§5/§6.B/§3.H/§10。剩:
- **W3-a** EN 隐私残留:§3.C(ciphertext identifiers)、§4 表(ciphertext only)、§7(client-side encryption substantially reduces)。
- **W3-b** `terms-of-service.en.md` §6.E + §8;`adult-content`/`AUP §2.J(e)` 的 still-encrypted。
- **W3-c** **ZH 镜像**:privacy + ToS zh-CN 同步全部改动。
- **W3-d** `CODE_COMPLIANCE_CHECKLIST.{en,zh}`:ENC-01..06→ENC-R1/R2;BYOK-01 反转(不删服务端 backend);BYOK-02/03/05/06 改写;BYOK-04 保留;FB-02 去"浏览器解密"。
- **W3-e** 重跑 `scripts/build_legal_html.py` 重渲染 HTML;CHANGELOG+README → **v1.2(Route-B realignment)**。
**验收**:全仓 grep 无残留 `client-side encrypt / zero-knowledge / browser…directly / only ciphertext / still-encrypted`;HTML≡MD;律师复审标注保留。

## W4 · G 运营护栏(全 live 可验)
- **W4-a 预算估算器** `extract/budget.py`:`estimate(script_id, model)` = 可提取章数×每章估算token×模型单价表 → `{est_usd, est_tokens, chapters}`;import 前弹"约 $0.87(你的 flash key),确认?"。
- **W4-b 提取走 BYOK**:`run_llm_extraction` 用**用户自己的 key**;跑前预算闸 + 跑中累计 vs ceiling。
- **W4-c 月配额 + 成本上限**:新表 `extraction_quota`(免费档月 N 本);作业启动查额度;per-book token ceiling 超→暂停告警(挂 import_jobs)。
- **W4-d 共享 embedding key 轮换**(BYOK-04 + 合规):`vertex_sa.json` 轮换;定 embedding 谁付费(平台 ~$3/月 或也 BYOK)。
**验收**:估算器对 80 章实测校准误差 <15%;超 ceiling 真暂停;配额拦截生效。

## W5 · 懒/增量提取(成本架构,配进度门)
**动机**:急切全书提取浪费——防剧透只让玩家看到进度以内 KB,过进度的提了也被过滤。改成按进度切片提。
- 新增 per-script `extracted_through_chapter` 标记。
- 建档时:Pass0 种子(纪元/词表,一次性,采样)+ 提取首 N_initial 章(开局够用)。
- `settings.advance_progress` 推进时:触发增量作业,只提 `[extracted_through, progress+buffer]` 的新切片(幂等,不重提)。
- 与 W4 衔接:预算按"本次切片"报价,不是全书。
**验收(live)**:玩到 50 章→只提 ~50 章(成本∝进度);推进到 100→只增量提 50-100;不重复提。

## W6 · F 创建向导前端 + 设置端点(需浏览器)
- **W6-a 后端端点**(可 live 验):`GET /api/saves/{id}/settings`(read_settings+schema)+ `PATCH`(apply_settings,锁死 enforcement)。
- **W6-b 前端**:`frontend/src/pages/saves.jsx` NewGameModal→5 步向导;游戏内设置面板可改 vs 锁死灰显。
**验收(浏览器)**:默认一路开局;omniscient+suspicious 生效;改起始世界线被灰显拦。

## W7 · E 可视化画布前端(需浏览器,最后)
- `frontend/src/pages/script-review.jsx`(E.1 只读复核表,接 GET /graph)→ React Flow 画布(god 编辑接 PATCH /canon)。
**验收(浏览器)**:复核表定位 A.0 污染章/改实体摘要落库;画布拖合并别名→重算嵌入→lookup 取到合并后实体。

---

## 执行顺序
**W1(质量)→ W2(扩容)→ W3(合规)→ W4(护栏)→ W5(懒提取)→ W6(F前端)→ W7(E前端)。**
W1–W5 全后端/可 live 验证;W6–W7 需浏览器 e2e。
W4 与 W5 有衔接(预算按切片),建议连做。

## 验证铁律(贯穿)
每个 W 完成 = 代码改 + **live/postgres/真LLM 验证 或 浏览器 e2e** + commit。不接受"看起来对"。
