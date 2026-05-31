# API 设置

## 这页能干嘛

在这里添加你自己的 AI 供应商 API Key，让平台调用你账号下的模型来驱动游戏。没有配置 Key 的供应商不会显示在列表中。

---

## 关键概念

- **BYOK（Bring Your Own Key）**：平台不内置共享 Key，你需要自己去各供应商申请 API Key 后填入。Key 写入后加密存储在你的用户凭证表中，不会明文保存，也不会在页面上回显（只显示末尾几位提示）。
- **供应商（Provider）**：提供 AI 模型的服务商，如 OpenAI、Anthropic、阿里 DashScope 等。
- **Base URL**：供应商 API 的地址。大多数供应商已内置默认值；如果你使用中转站或自建代理，可以在这里覆盖。
- **模型嗅探**：添加 Key 后，平台可以向供应商查询它支持哪些模型，并自动添加到你的列表中。
- **健康状态**：每个模型旁边有一个圆点——绿色可达、黄色降级、红色不可达、灰色尚未测试。

---

## 已支持的供应商

| 供应商 | 说明 |
|---|---|
| OpenAI | GPT-4o、o3 系列等，OpenAI 兼容协议 |
| Anthropic | Claude Opus / Sonnet / Haiku，Native 协议 |
| DeepSeek | DeepSeek R1 / Chat，OpenAI 兼容 |
| 阿里 DashScope / Qwen | 通义千问系列，支持 OpenAI-compat 或 Native 两种模式 |
| 腾讯 Hunyuan | Hunyuan 系列，OpenAI 兼容 |
| xAI / Grok | Grok 系列，OpenAI 兼容 |
| Xiaomi MiMo | MiMo 系列，OpenAI 兼容 |
| Google AI Studio | Gemini 系列，Native 协议 |
| OpenRouter | 聚合代理，一个 Key 可访问几十家模型 |
| Agent Platform | 上传 Service Account JSON 鉴权 |
| 自定义 | 任意 OpenAI 兼容端点，手动填写 ID 和 Base URL |

> **关于「OpenAI 兼容」**：只要服务商支持 OpenAI 接口格式（`/v1/chat/completions`），选「自定义」就能接入，包括国产中转站、本地 vLLM 等。

---

## 常见任务

### 任务 1：添加一个供应商的 API Key

步骤 1. 点击页面右上角「添加 API Key」按钮。

![添加 API Key 弹窗](./screenshots/settings-models-add-key.png)

步骤 2. 在弹窗的「供应商」下拉中选择你的供应商（如 OpenAI、Anthropic 等）。选择后 Base URL 会自动填入。

步骤 3. 在「API Key」框中粘贴你的 Key（格式通常以 `sk-` 开头）。Key 写入后不再回显，只显示末尾几位作为提示。

步骤 4. 如有需要，修改「Base URL」（使用中转站时填中转地址）和「连接方式」（直连 / HTTP 代理 / 局域网）。

步骤 5. 点击「添加」，完成后该供应商会出现在列表中。

---

### 任务 2：测试连通性并嗅探可用模型

步骤 1. 在 API 列表中点击一个已配置 Key 的供应商，底部展开详情面板。

步骤 2. 点击详情面板右上角的「校验连接」按钮。

![校验连接弹窗](./screenshots/settings-models-validate.png)

步骤 3. 弹窗向供应商发起嗅探，查询远端可用模型列表，并与本地已有列表对比，显示：

- **新增**：远端有但本地没有的模型
- **本地多余**：本地有但远端已下线的模型
- **不可达**：health 为红色（超时 / 4xx / 5xx）

步骤 4. 点击「全部添加」一次性导入新模型；勾选多余条目后点「删除 N 个」清理废弃模型。

---

### 任务 3：控制哪些模型显示在选择列表中

步骤 1. 点击供应商进入详情面板，点击「管理显示模型」。

步骤 2. 在弹窗中勾选/取消要显示的模型。隐藏不等于删除，随时可以重新勾选。

步骤 3. 点击「保存」。

---

### 任务 4：更新或删除已有 Key

- **更新 Key**：在供应商详情面板点击「编辑」，重新填入新 Key（留空则保留原值）。
- **删除 Key**：在详情面板点击「删除 Key」并确认，该供应商的模型将不再可用。

---

## 模型生效优先级

1. 「模块模型」页面为该模块手动指定的模型（优先级最高）
2. 主 GM 在「API 设置」中选中的模型
3. 平台 catalog 内置默认推荐（兜底）

具体模块分配见[模块模型分配](./settings-modules.md)。

---

## 安全提示

- Key 在服务端加密存储，不会明文出现在数据库或日志中，页面也不回显。
- 建议申请**低权限 Key**，只开放 Chat Completions，不开放 Fine-tune / Admin / Billing 等权限，限制泄露风险。
- 若 Key 疑似泄露，立即去供应商控制台撤销，再填入新 Key。
- 模型旁出现「密钥已失效」橙色标签，说明 Key 已过期或被撤销，请尽快更新。

---

## 常见问题

**Q: 我填了 Key 为什么模型还是不可用？**
A: 点「校验连接」查看嗅探结果。嗅探失败时检查：Key 是否正确、Base URL 是否可达、是否需要代理（国内直连部分海外服务需要 HTTP 代理）。

**Q: 可以同时配置多个供应商吗？**
A: 可以。不同供应商的模型可以分别分配给不同游戏模块，见[模块模型分配](./settings-modules.md)。

**Q: OpenRouter 怎么用？**
A: 去 openrouter.ai 注册账号并申请 Key，在供应商列表中选「OpenRouter」填入即可。一个 Key 能访问数十家供应商的模型。

**Q: 健康状态一直是灰色「未测试」怎么办？**
A: 点「校验连接」触发一次 probe，或等页面自动后台刷新（进入此页后约 8 秒更新一次）。

---

## 相关

- [模型参数调优](./settings-modelparams.md)
- [模块模型分配](./settings-modules.md)
