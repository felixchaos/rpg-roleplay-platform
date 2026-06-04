# Contributing

欢迎贡献!以下是参与开发的完整指南。

---

## 🛠️ 开发流程

### 1. 启动开发环境

```bash
cd rpg-roleplay-platform/
./scripts/dev.sh start
```

### 2. 改后端 (`rpg/`)

- 修改代码后**必须**跑测试确认基线
- 遵守通用底座原则:不往代码里写剧本专有名词
- 剧本特有配置写入 `rpg/modules/_script_overrides/<key>.json`

### 3. 改前端 (`frontend/`)

- `frontend/` 是 git submodule,有自己的提交流程
- 详见 `frontend/README`

---

## 📝 Commit 风格

使用中文描述 + task 编号:

| 场景 | 格式 | 示例 |
|---|---|---|
| 新功能 | `feat(task N): 描述` | `feat(task 88): 添加 worldevent 主动触发接口` |
| Bug 修复 | `fix(task N): 描述` | `fix(task 85): 修复 phase 切换时状态不同步` |
| 重构 | `refactor(phase N): 描述` | `refactor(phase 3): 去除 GM 硬编码角色名` |
| 杂项 | `chore: 描述` | `chore: 更新 dependabot 配置` |

---

## 🧪 测试

### 跑完整测试套件

```bash
cd rpg
../rpg_env/bin/python -m unittest discover -s tests -t .
```

### 基线要求

| 指标 | 要求 |
|---|---|
| pass | ≥ 754 |
| fail | ≤ 30 (30 fail 在 frontend submodule,修完会归零) |
| error | **必须 0** |
| skip | ≤ 1 |

> ⚠️ 任何 PR 不得让 error 从 0 变成非零,也不得让 pass 数下降。

### 针对单个模块测试

```bash
cd rpg
../rpg_env/bin/python -m unittest tests/test_<module>.py
```

---

## 🔍 Lint

```bash
ruff check rpg/
```

**当前状态**: 约 249 个 F401 警告 (re-export 故意保留,不需要修)。
**目标**: 不引入**新的** ruff 警告。

---

## ✅ PR 检查清单

提交 PR 前请自检:

- [ ] 测试基线持平或改善 (pass ≥ 754 / error = 0)
- [ ] 无新 ruff 警告
- [ ] 不引入剧本专有名词硬编码 (通用底座原则)
- [ ] 新功能有对应测试
- [ ] commit message 格式符合规范

---

## 🏗️ 架构约定

### 通用底座原则

所有剧本相关的名词、规则、角色名、地点名**不得**出现在 `rpg/` 的 Python 代码中。
正确做法:写入 `rpg/modules/_script_overrides/<key>.json`,代码通过 config loader 读取。

### 分层架构

- `routes/` — HTTP 接口层,只做参数校验和响应序列化
- `agents/` — AI 逻辑层,不直接操作数据库
- `platform_app/` — service + repo 两层,service 调 repo
- `state/` — 纯数据结构,不依赖外部 IO

---

## 🐛 报 Bug

请在 issue 中提供:
1. 复现步骤
2. 期望行为 vs 实际行为
3. 相关日志 (去掉 API key 等敏感信息)
