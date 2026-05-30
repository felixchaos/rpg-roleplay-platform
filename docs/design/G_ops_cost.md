# Phase G — 运营护栏 + 成本模型

> 实施级设计。把"BYOK 玩法零成本 + 一次性可共享提取"落成可执行的配额/共享/上限机制。
>
> 现有锚点:`import_jobs`(v9/v12,提取作业状态 + kind: full_pipeline|knowledge_sync)、
> `user_credentials`/`resolve_api_key`(BYOK)、`scripts.public_id`、`token_usage`(v5 迁移)、
> `command_dispatcher` 限流(MAX_CALLS_PER_USER_PER_SECOND=20)。

---

## 1. 成本模型(决定性事实)
- **玩法 = BYOK**:每用户自己的加密 key(`resolve_api_key`),GM loop 每 step 走用户 key → **平台零经常性推理成本**
- **唯一变量 = 一次性提取**:per-script 提取一次,可共享、可配额、可加上限
- 单本 ≈ **$1-2.5**(§A_extraction §7:Pass1 flash+批量五折 $0.35-1 + Pass0/2 frontier 小量 $0.5-1 + 自托管嵌入≈免费)
- 100 用户启动:人均 2-3 本,**因公开书共享去重,大概率 < $100 一次性**

## 2. 五个降本杠杆(免费档可行的关键)
1. **模型分层**(铁律):Pass1 用 flash,Pass0/2 才 frontier;**绝不全程 frontier**(会 $15-30/本)
2. **自托管嵌入**:BGE-M3 自己跑,嵌入成本 ≈ 0
3. **公开书 KB 共享去重**:同一本公开书(`scripts.public_id` / 内容指纹)只提取一次,所有存档共享规范层(§3)
4. **月配额**:免费档每月 N 本提取额度;超额走 BYOK-提取(用户自己 key 跑提取)
5. **import 硬 token 上限**:单次导入字符/章节上限,防超大 txt 拖垮

## 3. 公开书共享去重(省钱核心)
- 导入时算**内容指纹**(归一后正文 hash + 章数 + 字数);命中已提取的公开书 → **直接复用规范层**(`kb_canon_*`/`worldbook`/`script_worldline_*`),跳过提取
- 私有书(版权/未公开)不共享,各自提取(走配额/BYOK)
- 实现:`scripts` 加 `content_fingerprint`;导入前查重;公开书规范层标 `shareable=true`

## 4. 提取作业护栏(挂 import_jobs)
- 提取走 `import_jobs`(已有),每 Pass 一 stage,断点续跑;失败只重该 stage
- **成本上限**:作业累计 token 超 per-book ceiling(如 $3)→ 暂停 + 告警,不静默烧钱
- **批量对齐**:Pass1 走 Batch API(五折),作业轮询批状态写 import_jobs 进度
- **配额检查**:作业启动前查用户月额度;超额要求 BYOK-提取或拒绝

## 5. 玩法侧限流
- 复用 `command_dispatcher` 限流(20 calls/user/s);GM loop `max_turns=3` + `max_budget_usd=0.05`/回合(§D)双重护栏
- BYOK 透传:用户 key 的速率/额度是用户自己的事,平台只透传 + 记 `token_usage`(可观测)

## 6. 落地改动清单
- **改** `scripts` 表加 `content_fingerprint` / `shareable`;导入前查重复用(§3)
- **改** 提取作业(`rpg/extract/*` + import_jobs)加成本上限 + 配额检查 + 批量轮询(§4)
- **新建** 配额表/逻辑(免费档月额度计数)
- **复用**:`import_jobs` / `token_usage` / `resolve_api_key` / dispatcher 限流

## 7. 验收
- 同一公开书导入两次:第二次命中指纹,**零提取成本**,秒级完成
- 提取作业触达成本上限 → 暂停 + 告警,不超烧
- BYOK 玩法:GM loop 全程走用户 key,平台账单零推理费(只有一次性提取 + 自托管嵌入机器成本)
