# 后端 API 契约

面向前端（Claude Design）的接口契约说明。截至 B6 批次完成时（2026-05-25）后端共暴露 ~110 个 endpoint，本文挑高频/核心的列出；其余可通过 `/api/platform/commands` 自我描述。

## 通用约定

- **认证**：Cookie `rpg_session`（HttpOnly + SameSite=Lax + Secure 在 https 下自动启）。`/api/auth/login` 写入，`/api/auth/logout` 删除。其他接口缺 cookie 多数返回 401/400。
- **Content-Type**：JSON 请求体；SSE 端点用 `text/event-stream`。
- **错误形态**：`{ok: false, error: "..."}`。极少使用 HTTP 500；输入校验失败返回 400/422。
- **多用户隔离**：所有 `/api/me/*`、`/api/saves`、`/api/scripts` 都按当前用户隔离。匿名/未登录访问受保护接口会被拒。
- **限流**：登录失败 → 5 次/分钟 + 阶梯锁定；其他端点未单独限流，依赖反代/网关。
- **缓存**：`/api` 全部 `Cache-Control: no-store`；响应头带 `X-API-Version` + `X-Request-ID`。
- **服务器模式**：`RPG_REQUIRE_AUTH=1` 强制鉴权，禁全局 SAVE_FILE 写，runtime 元数据走 DB（`user_runtime` 表）。

## 鉴权

| Method | Path | Body | 返回 |
|---|---|---|---|
| POST | `/api/auth/register` | `{username, password, display_name}` | `{ok, user, platform}` + Set-Cookie |
| POST | `/api/auth/login` | `{username, password}` | 同上；失败 400/429 |
| POST | `/api/auth/logout` | — | `{ok}` + 清 cookie |
| GET | `/api/auth/me` | — | `{ok, user, database}`（user 为 null 表示未登录） |

`user` 形态：`{id, username, display_name, role, bio, created_at}`。`platform` 形态：`{deploy_mode, require_auth, version, runtime}`。

## 存档 / 分支（Git-like）

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/saves` | 当前用户的存档列表（轻量字段，按 updated_at desc） |
| GET | `/api/saves/{id}` | 存档详情，含 active_commit_id / active_ref_id |
| GET | `/api/saves/{id}/export` | 完整存档 JSON 下载（含 branch_commits/refs/messages/memories） |
| POST | `/api/saves/import` | 从导出 JSON 还原存档，owner 重映射为当前用户 |
| GET | `/api/saves/{id}/context-runs` | 该存档的子代理决策历史（dashboard 用） |
| GET | `/api/branches/{save_id}` | 存档的分支树（commits 列表 + refs） |
| POST | `/api/branches/activate` | `{save_id, ref_id?, commit_id?}` 切换激活分支/提交 |
| POST | `/api/branches/continue` | 从当前激活节点直接续游戏（创建新 commit 准备 chat） |
| POST | `/api/branches/delete` | 删除某 ref（不影响别的 ref 已引用的 commit） |

## 主流程：聊天（SSE）

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/chat` | SSE 流；事件名见下 |
| POST | `/api/chat/estimate` | `{message}` → `{estimated_tokens, context_used, context_max, budget}` 预算预估 |
| POST | `/api/stop` | 打断当前用户正在跑的 chat（按 user 隔离，不影响其他用户） |
| POST | `/api/save` | 手动保存当前 runtime（写 DB + 视情况镜像 JSON） |
| POST | `/api/new` | 开新存档 |
| POST | `/api/opening` | SSE 生成开场白 |

**SSE 事件**（按出现顺序）：
- `retrieval` `{text}` — RAG 拼出的上下文文本
- `context` `{debug}` — 上下文层级 debug 包（前端展示用）
- `status` — 完整 state 快照
- `agent` `{phase, message, status, elapsed_ms, ...}` — 子代理决策步骤（context_agent）
- `token` `{text}` — 流式生成的 GM 正文 chunk
- `tool_call` `{server_id, tool, arguments}` — MCP 工具被 GM 调用
- `tool_result` `{ok, result, error}` — 工具结果
- `tool_error` `{error, raw}` — 工具调用 JSON 解析失败
- `usage` `{turns, by_model, cost_usd, ...}` — 本轮 usage 汇总
- `updates` `{items}` — GM 输出的结构化标签变更（位置/时间/资源等）
- `done` `{status, interrupted, usage}` — 流结束
- `error` `{message, partial}` — 异常

请求体：`{message: str, attachments?: [{name, content_b64, mime}]}`。

## 用量与计费

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/me/usage?days=30` | 累计：input/output/cost，按模型分组 + 最近 20 轮 |
| GET | `/api/me/usage/timeline?days=30&group_by=day` | 时序：按天或按模型 |
| GET | `/api/me/profile` | 用户资料 + 用量摘要（profile 页用） |

**新增**：`token_usage.metadata.kind = "sub_agent"` 标识子代理独立计费的记录。Dashboard 可按 kind 拆分主代理/子代理成本。

## 模型 / Provider

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/models` | 列出所有 provider + model 树（admin 看 base_url，普通用户脱敏） |
| GET | `/api/models/capabilities?api_id=&model=` | 单模型能力（context window、function calling、vision 等） |
| POST | `/api/models/select` | `{api_id, model}` 切换当前用户选择的模型 |
| POST | `/api/models/probe` | admin only：远端探测 provider 可用 model 列表 |
| GET | `/api/models/pricing?api_id=&model=` | 定价信息 |

## 用户级 API Key

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/me/credentials` | 已配置的 provider 列表（不含明文 key） |
| POST | `/api/me/credentials` | `{api_id, api_key, base_url_override?}` 写入加密 key（AES-GCM + HKDF） |
| POST | `/api/me/credentials/delete` | `{api_id}` 删除 |
| GET | `/api/me/credentials/test?api_id=` | 探测该 provider 用当前 key 能否连通 |

普通用户的 `base_url_override` 会被忽略（仅 admin 可设；SSRF 防护）。

## 角色卡 / 世界书

**用户级**（跨剧本可复用）：
| Method | Path | 说明 |
|---|---|---|
| GET/POST/PUT/DELETE | `/api/me/character-cards[/{card_id}]` | 自建 NPC 卡 CRUD |
| POST | `/api/me/character-cards/import-tavern` | 上传 SillyTavern V1/V2 JSON 或 PNG，自动解析 |
| GET | `/api/me/character-cards/{id}/export-tavern` | 导出 V2 JSON |
| GET | `/api/me/character-cards/{id}/export-png` | 导出 PNG 嵌入卡 |
| GET/POST/PUT/DELETE | `/api/me/personas[/{persona_id}]` | 玩家身份卡（玩家自己扮演的角色） |

**书内**（绑剧本/书）：
| Method | Path | 说明 |
|---|---|---|
| GET/POST/PUT | `/api/scripts/{id}/character-cards[/{card_id}]` | 该剧本/书内 NPC |
| POST | `/api/scripts/{id}/character-cards/{cid}/enabled` | 启用/禁用 |
| GET/POST/PUT | `/api/scripts/{id}/worldbook` | 该剧本/书的世界书条目 |

注：B3 完成后 `context_engine` 优先读 DB（`character_cards` / `worldbook_entries`），失败才回退 `indexes/*.json`。

## 剧本导入（拆书）

| Method | Path | 说明 |
|---|---|---|
| POST | `/api/uploads/init` | `{filename, total_size, chunks}` 启动分片上传，返回 `upload_id` |
| POST | `/api/uploads/{id}/chunk` | `{index, data_b64}` 上传单片（base64 严格校验） |
| POST | `/api/uploads/{id}/finish` | 合并 + 拆章 dry-run |
| POST | `/api/uploads/{id}/cancel` | 取消上传 |
| POST | `/api/scripts/import` | 直接整篇 JSON 提交（小文件用） |
| POST | `/api/scripts/preview` | dry-run 预切章节，不落盘 |
| POST | `/api/scripts/batch-import` | 上传 ZIP，最多 50 个 .txt/.md 文件批量导入 |
| GET | `/api/scripts/{id}/chapters?q=` | 章节列表（支持全文搜索） |
| POST | `/api/scripts/{id}/chapters/{idx}` | 修改章节 |
| POST | `/api/scripts/{id}/chapters/merge` | 合并相邻两章 |
| POST | `/api/scripts/{id}/chapters/{idx}/split` | 在字符位置切分 |
| POST | `/api/scripts/{id}/delete` | 删除剧本 |
| POST | `/api/scripts/{id}/resplit` | 重新拆章 |

**异步流水线（5 阶段：chunks/facts/entities/cards/worldbook）**：
| Method | Path | 说明 |
|---|---|---|
| GET | `/api/scripts/{id}/import-budget` | `{tokens, cost_usd, eta_sec}` 预算预估 |
| POST | `/api/scripts/{id}/import-pipeline` | 启动 → 返回 `job_id` |
| GET | `/api/me/import-jobs` | 当前用户活跃 + 历史 job 列表 |
| GET | `/api/scripts/import-jobs/{job_id}` | job 详情 |
| GET | `/api/scripts/import-jobs/{job_id}/stream` | SSE 推送进度（15s 心跳） |
| POST | `/api/scripts/import-jobs/{job_id}/cancel` | 请求取消（next checkpoint 触发） |
| POST | `/api/scripts/{id}/knowledge/sync` | 触发 knowledge_sync durable job（B5：DB 持久化，重启可恢复） |
| GET | `/api/scripts/{id}/import-status` | knowledge_sync 状态（读 import_jobs 表） |

## MCP / Skill

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/tools` | 已注册的 MCP server 列表（脱敏：不返 env） |
| POST | `/api/mcp/server` | 新增 / 更新 server 配置（admin） |
| POST | `/api/mcp/server/start` | `{id}` 启动 + 健康监控开始 |
| POST | `/api/mcp/server/stop` | `{id}` 停止 |
| POST | `/api/mcp/server/enabled` | `{id, enabled}` 切换开关 |
| GET | `/api/mcp/server/{id}/validate` | 探测配置可启动性 |
| GET | `/api/mcp/runtime` | 运行时状态（admin 看 stderr_tail / health / failures） |
| GET | `/api/mcp/tools` | 已发现的所有工具（按 server 分组） |
| POST | `/api/mcp/tool/call` | `{server_id, tool, arguments}` 手动调用（dashboard 测试用） |
| POST | `/api/skills/import` | 上传 Skill 包（zip） |
| POST | `/api/skills/{id}/run` | 沙箱执行（ulimit + 临时目录 + env 白名单） |

**主 GM 工具循环**：`/api/chat` 内部自动调 `mcp_broker.discover_all_tools()`，注入到 system prompt。GM 输出 `<<TOOL_CALL>>{...}<<END_TOOL_CALL>>` 即触发；SSE 发 `tool_call` / `tool_result` 事件。

## 其他

| Path | 说明 |
|---|---|
| GET `/api/platform` | 部署模式 + DB 版本 + features 标志 |
| GET `/api/platform/commands` | 所有端点自描述清单（前端可据此渲染调试面板） |
| GET `/api/state` | 当前用户运行时 state 快照（用于刷新时恢复 UI） |
| POST `/api/permissions` | `{mode}` 切换权限模式（strict / partial / full_access） |
| POST `/api/permissions/pending-write` | 应用 / 拒绝 GM 提出的写操作 |
| GET `/api/memories` | 当前 state 的 memory 列表（按 bucket 分） |
| POST `/api/memory/add` / `/api/memory/remove` / `/api/memory/mode` | memory 编辑 |
| GET `/api/worldline/variables` + add/remove | 玩家自定义世界线变量 |
| POST `/api/me/preference` | 用户偏好（含 `sub_agent_model_override`），body 形如 `{key, value}` 增量更新 |
| GET/POST | `/api/library*` | 文件库（图床 / 附件） |

## 部署相关

```bash
# CI / deploy
python -m platform_app.migrate full   # 首次：baseline + migrate + pgvector
python -m platform_app.migrate up     # 后续：只跑待应用 migration
python -m platform_app.migrate check  # 健康检查：schema 落后 exit(1)

# 生产 worker（让 app 永不碰 DDL，更快更安全）
RPG_SKIP_AUTO_MIGRATE=1 RPG_REQUIRE_AUTH=1 uvicorn app:app --workers 4
```

## 测试

```bash
python -m unittest discover tests  # 全套（auth / runtime / context / sub_agent / sync / permissions）
```

集成测试基于 `fastapi.testclient.TestClient`，所有 fixture 用 `integtest_` 前缀用户，运行后自动清理，不污染真实数据。
