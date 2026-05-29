<div align="center">

# RPG 平台

[English](./README.md) · **简体中文**

开源的小说改编 LLM 角色扮演游戏引擎.

[![tests](https://img.shields.io/badge/tests-passing-brightgreen)](#)
[![license](https://img.shields.io/badge/license-MIT-blue)](./LICENSE)
[![rust](https://img.shields.io/badge/rust-stable-orange)](#)

</div>

---

## 这是什么

RPG 平台是一个可自部署的运行时, 把一本小说, 一份设定集, 或任意结构化的世界数据, 变成可以真正玩起来, 由大语言模型驱动的角色扮演游戏. 作者把自己的故事丢进剧本目录, 玩家拿到一个类型安全的 Web 客户端, 背后由 Rust 服务负责骰子, 遭遇, 物品栏, 分支存档和对话记忆. 引擎不挡在故事前面: 规则即数据, provider 可插拔, 存档像代码一样有版本.

## 适合谁

- **小说作者**, 想给自己的世界做一个能玩的改编, 又不想从头写一个游戏引擎.
- **GM 和折腾党**, 用前沿模型跑长线单机或共享战役.
- **开发者**, 想在丰富的虚构状态上实验 agent 架构, 检索, 和工具调用.

## 亮点

- **自带剧本.** 剧本是纯数据: 角色, 地点, 阶段推进, 建议规则, 世界书条目. 换故事不需要改代码.
- **十家 LLM provider, 一份目录.** OpenAI, Anthropic, Google AI Studio, Anthropic Agent Platform, OpenRouter, DeepSeek, xAI, 阿里 Qwen, 腾讯混元, 小米 MiMo, 全部通过一个类型化的模型选择器暴露.
- **可分支的存档树.** commit, ref, checkout 像 Git 一样工作: 任意时刻打快照, 任意位置开分支, 任意决策可回放.
- **生产级后端.** Rust + axum + sqlx, pgvector 检索, KMS 加密密钥, Prometheus 指标, WebSocket 与 SSE 双流, 端到端测试框架.
- **端到端类型安全.** Rust 领域类型通过 ts-rs 导出到 TypeScript, React 客户端直接消费, API 契约在编译期两端都被检查.
- **可插拔规则.** 内置 D&D 5E 兼容核心, 每个剧本可以用 JSON 覆盖骰子, 遭遇, 物品规则, 不需要 fork 引擎.

## 快速开始

```bash
docker compose -f deploy/docker-compose.yml up -d postgres
cargo run -p rpg-server
cd frontend && npm install && npm run dev
# 打开 http://localhost:5173
```

## 文档

- [架构总览](./docs/architecture.md) (TODO)
- [剧本编写指南](./docs/scenarios.md) (TODO)
- [Provider 与模型目录](./docs/providers.md) (TODO)
- [部署模式](./docs/deployment.md) (TODO)

## 演示

[此处放截图]

## 贡献

欢迎 issue, 剧本包, provider 适配, 规则模块. 详见 [CONTRIBUTING.md](./CONTRIBUTING.md), 内含开发循环, 分支约定, 以及提 PR 前如何在本地跑通工作区测试.

## 许可

MIT. 详见 [LICENSE](./LICENSE).

---

*最初是为了改编一本特定的小说而做, 现在被泛化成了一个谁都可以把自己的故事丢进去的平台.*
