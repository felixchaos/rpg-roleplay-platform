# 模块模型分配

## 这页能干嘛

平台内部有多个 AI 子模块同时运作（GM 主叙事、规则裁定、上下文召回等），这里可以给每个模块单独指定使用哪个模型。没有单独指定的模块会自动跟随主 GM 的模型。

---

## 什么是「模块」

平台把 AI 工作拆成多个专职子模块，每个模块有不同的任务和对模型的要求：

| 模块名 | 职责 | 对模型的要求 |
|---|---|---|
| 主 GM | 根据玩家输入生成游戏世界的叙事响应 | 最高，需要创意和长文能力 |
| Sub-GM（Context Agent） | 整理玩家意图、检索计划；把模糊输入变成结构化指令 | 中等，需要理解力 |
| Command Agent | 解析 `/set` 命令的自然语言，转为结构化操作 | 中等偏低，规则性强 |
| Console Assistant | 驱动侧栏控制台助手（管理员工具） | 视使用场景而定 |
| Extractor | GM 叙事二次解析，把输出提取成结构化 ops（两步式 GM 第二步） | 低，便宜模型即可 |
| Character Card Generator | 生成 / 微调角色卡 | 中等，需要创意 |
| Critic（一致性评分） | 对角色卡生成结果打一致性分数（0–1，阈值 0.6） | 低，可用小模型 |
| Acceptance Verifier | 校验 GM 输出是否满足 curator 设置的验收条件 | 低，可用小模型 |
| Embedding（RAG 检索） | 向量嵌入，用于记忆的语义召回 | 需要支持 embedding 的专用模型 |

---

## 为什么要分模块？

不同模块的性价比要求差异很大：

- **主 GM** 是游戏体验的核心，值得用最强的旗舰模型（GPT-4o、Claude Opus、Qwen-Max 等）。
- **Extractor / Critic / Verifier** 只做结构化处理，不需要创意，用便宜的小模型（如 claude-haiku、gpt-4o-mini、gemini-flash）即可，成本可以降低 70–90%。
- **Command Agent** 解析用户命令，对准确性要求高但创意要求低，适合中端模型。

分模块配置让你在保持核心体验的同时大幅节省 API 费用。

---

## 推荐配置矩阵

以下是基于常见供应商的建议。实际情况依你的预算和偏好调整：

| 模块 | 推荐档位 | 示例模型 |
|---|---|---|
| 主 GM | 旗舰 | GPT-4o · Claude Opus · Qwen-Max · DeepSeek-R1 |
| Sub-GM | 中端 | GPT-4o mini · Claude Sonnet · Qwen-Plus |
| Command Agent | 中端或小型 | GPT-4o mini · Claude Haiku · Gemini Flash |
| Console Assistant | 与主 GM 相同或中端 | （默认跟主 GM）|
| Extractor | 便宜小型 | Claude Haiku · gemini-3.5-flash · gpt-4o-mini |
| Character Card Generator | 中端旗舰 | Claude Sonnet · GPT-4o |
| Critic | 便宜小型 | Claude Haiku · gemini-flash |
| Acceptance Verifier | 便宜小型 | Claude Haiku · gpt-4o-mini |
| Embedding（RAG） | 专用 embedding 模型 | text-embedding-3-small · 通义 embedding |

> 留空（跟主 GM）是最简单的配置，适合刚开始使用时。等 API 费用变得明显再逐步细化。

---

## 常见任务

### 任务 1：为某个模块指定专用模型

步骤 1. 进入「平台设置 → 模块模型」。

步骤 2. 在列表中找到你想配置的模块行，点击该行最右侧的下拉框。

<!-- TODO: 截图 settings-modules 模块列表 -->

步骤 3. 下拉列表会显示所有已配置 API Key 的供应商下的模型（标灰的为已禁用模型，不可选）。选择目标模型。

步骤 4. 选择后即时保存，「当前生效」列会刷新显示新的模型。

### 任务 2：重置某个模块，让它跟随主 GM

步骤 1. 在该模块的下拉框中选「（跟主 GM）」选项。

步骤 2. 即时生效，主 GM 切换模型时该模块也会跟着切换。

### 任务 3：一键重置所有模块覆盖

步骤 1. 点击页面右上角「重置全部为默认」按钮。

步骤 2. 确认后，所有模块（主 GM 除外）都会回到「跟主 GM」状态。

---

## 注意事项

- **主 GM 的模型在这里只读**，需要去「API 设置」页面中修改当前选中的模型。
- 下拉列表中标灰的模型是已禁用（enabled = false）的，不可选择。如果看不到想要的模型，先去「API 设置」检查该模型是否已启用。
- Embedding 模块需要支持 embedding 的专用模型（不是普通的 chat 模型），填错会导致记忆召回失败。
- 配置改动通过 POST `/api/me/preference` 即时保存，无需手动点保存按钮。

---

## 常见问题

**Q: Extractor 是什么，我需要开启它吗？**
A: Extractor 是「两步式 GM」的第二步：主 GM 先生成叙事，Extractor 再把叙事解析成结构化的状态变更操作。开启后错误率更低，但成本约增加 20%。对新手来说，用默认的单步 GM 已经够用，等出现状态一致性问题再考虑开启。

**Q: 我的模型列表里没有我想要的模型怎么办？**
A: 需要先在「API 设置」里添加对应供应商的 Key，并确认该模型已经被嗅探并加入到模型列表中。

**Q: 为 Sub-GM 单独指定模型有什么好处？**
A: Sub-GM 主要做意图解析和检索计划，不需要旗舰模型的创意能力，用 mini / flash 级别的模型速度更快、费用更低，且效果差异不大。

**Q: 「当前生效」列显示「未知」是什么意思？**
A: 说明该模块保存的 api_id / model_name 在当前 catalog 中找不到对应条目，通常是模型被删除或 Key 被撤销。重新选择一个有效模型即可。

---

## 相关

- [API 设置（模型配置）](./settings-models.md)
- [模型参数调优](./settings-modelparams.md)
