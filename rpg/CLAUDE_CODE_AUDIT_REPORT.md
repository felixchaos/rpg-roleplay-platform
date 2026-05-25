# Claude Code 审计与修复报告

时间：2026-05-24 18:55（接管 Codex 交接后）

## 一、修复的问题

### P0 - 本地模式 DB 持久化丢失

**症状**：本地未登录调用 `/api/chat` 时，`messages` / `context_runs` 表永远是 0，但 `branch_commits` 正常增长。

**根因**：`ui.py:api_chat` 用 `if api_user and active_save_id` 做门控，本地未登录时 `api_user=None` → 所有 `record_*` 调用被跳过。

**修复**：新增 `_resolve_persist_target(api_user)` helper（`ui.py:106-128`）。本地模式下从 `runtime.json` / `bootstrap_runtime_binding()` 回退到当前激活存档的 owner_id。三处 `if api_user and ...` 全部改为 `if persist_user_id and active_save_id`。

**验证**：`messages 0→6`，`context_runs 0→3`，`memories=16`。

---

### P1 - 路由重复定义

**症状**：`POST /api/worldline/variable` 和 `/api/worldline/variable/remove` 在 `ui.py` 和 `platform_app/api.py` 都注册了。FastAPI 不报错，但行为不确定。

**修复**：
- 删除 `platform_app/api.py` 中的写入路由（runtime + DB 写入合并到 ui.py 版本）
- ui.py 写入路径加上 DB 同步：`platform_knowledge.set_worldline_variable(...)` 在更新 runtime 之后调用
- platform_app 改成提供只读接口：`GET /api/worldline/variables` + `GET /api/memories`

**验证**：路由表 `重复 0 / 唯一 66`。

---

### P1 - state_repository.py（交接 TODO #1）

**新增文件**：`rpg/state_repository.py`

提供统一入口：

```python
load_active_state(user_id=None) -> (GameState, runtime_meta)
save_active_state(state, user_id=None) -> {ok, commit_id, mirror_path}
repository_status() -> {...诊断...}
```

读取优先级：
1. `runtime_checkouts.state_snapshot`（DB 权威源）
2. `game_saves.state_snapshot`
3. `bootstrap_runtime_binding` 兜底
4. `SAVE_FILE` JSON 镜像最终兜底

`ui.py:_ensure_loaded()` 已切换到 `state_repository.load_active_state()`，DB 故障时自动降级。

---

### P2 - runtime_checkouts dirty 状态（交接 TODO #2）

**Schema 变更**：
```sql
ALTER TABLE runtime_checkouts
  ADD COLUMN snapshot_hash text,
  ADD COLUMN dirty boolean,
  ADD COLUMN turn_at_commit integer,
  ADD COLUMN turn_runtime integer;
```

`db.py:_run_migrations` 添加幂等 ALTER 语句保证自动迁移。

**逻辑**：
- `_write_checkout()` 写入 commit 时 `dirty=false, turn_at_commit=turn_runtime=turn`
- `persist_runtime_state()` 手动 save 后 `dirty=false`
- `mark_runtime_dirty()` 新增函数：runtime state 被修改但未 commit 时调用
- `runtime.read_runtime()` 现在自动附加 DB 中的 `dirty / turn_at_commit / turn_runtime / turns_ahead / snapshot_hash`

**前端可用字段**：

```json
GET /api/platform 或 read_runtime() 返回:
{
  "dirty": false,
  "snapshot_hash": "433a5ff2...",
  "turn_at_commit": 19,
  "turn_runtime": 19,
  "turns_ahead": 0
}
```

---

### P2 - memories / worldline 查询 API（交接 TODO #4）

**新增 API**：

```
GET /api/worldline/variables?save_id=6
GET /api/memories?save_id=6&bucket=facts&limit=20
```

**新增 knowledge 函数**：
- `list_worldline_variables(user_id, save_id)`
- `list_memories(user_id, save_id, bucket=None, limit=None, cursor=None)`

按 importance + id desc 排序，支持 bucket 过滤（facts/abilities/resources/notes/summary/pinned）。

---

### P2 - context_runs 状态流（交接 TODO #5）

**Schema 变更**：

```sql
ALTER TABLE context_runs
  ADD COLUMN status text NOT NULL DEFAULT 'done',
  ADD COLUMN error text,
  ADD COLUMN duration_ms integer,
  ADD COLUMN started_at timestamptz;
```

**状态机**：
- `done` - 正常完成
- `stopped` - 用户打断
- `failed` - 异常退出（带 error 字段）
- `running` - 预留给未来异步任务

**新增函数**：`knowledge.update_context_run_status(run_id, status, error, duration_ms)`

**ui.py chat 处理**：
- 正常完成：record_context_run 时直接写 status=done + duration_ms
- 打断：update_context_run_status(stopped)
- 异常：update_context_run_status(failed, error=str(exc))

前端右侧"调试"面板可直接读 `/api/saves/{id}/context-runs` 拿到带状态的运行日志。

---

## 二、最终冒烟测试结果（全绿）

| 项 | 状态 |
|---|---|
| 服务 + Postgres | ✓ |
| 23 个模块编译 | ✓ |
| 路由 66 个无重复 | ✓ |
| 5 个核心 GET 端点全 200 | ✓ |
| 恶意 Origin 拒绝 403 | ✓ |
| `state_repository.load_active_state()` | ✓ |
| 新接口 401 鉴权生效 | ✓ |

数据库实际数据：

```
game_saves:      1
branch_commits:  19  (审计期间 +4 commit)
messages:        6   (审计期间 0 → 6) ★
context_runs:    3   (审计期间 0 → 3) ★
memories:       16
chapter_facts:  1315
```

---

## 三、前端接入指南（增量）

文档 `claude_design_upload/API_AND_DATA_SHAPE.md` 列了原有 70 个端点。本次审计后**新增/变更**：

### 新增 API

```
GET  /api/worldline/variables?save_id=...     # 列出 worldline 变量
GET  /api/memories?save_id=...&bucket=...     # 列出记忆，可按 bucket 过滤
```

### 字段新增

#### `GET /api/platform` 中的 `runtime` 对象

```json
"runtime": {
  "user_id": 6,
  "save_id": 6,
  "active_commit_id": 28,
  "dirty": false,                    // 新增：runtime 是否领先 commit
  "snapshot_hash": "433a5ff2...",   // 新增：当前 state 哈希
  "turn_at_commit": 20,             // 新增：最近 commit 的 turn
  "turn_runtime": 20,               // 新增：当前 runtime turn
  "turns_ahead": 0                  // 新增：未保存的回合数
}
```

#### `GET /api/saves/{id}/context-runs` 中的每条记录

```json
{
  "id": 3,
  "turn": 19,
  "status": "done",         // 新增：done | stopped | failed | running
  "error": "",              // 新增：失败原因
  "duration_ms": 5472,      // 新增：召回 + 主 GM 总耗时
  "started_at": "...",      // 新增
  // 原有字段...
  "agent_steps": [...],
  "active_character_cards": [...],
  ...
}
```

### 推荐前端展示

- 状态栏右上角显示 `dirty` → 改成 `unsaved` 圆点指示
- 调试面板按 `status` 给运行步骤上色（绿=done，黄=stopped，红=failed）
- 主 GM 进度条用 `duration_ms` 校准预期

---

## 四、剩余 TODO（交接文档列出但未完成）

低优先级，前端无关：

- TODO #3 `/api/branches/continue` 返回更明确字段 — runtime_url/active_ref 已有，dirty 已通过 runtime_meta 暴露
- TODO #6 DB migration/versioning — 仍用 `create table if not exists` + `alter add column`
- TODO #7 正式安全审计 artifact
- TODO #8 严谨化本地模式鉴权（设 `RPG_REQUIRE_AUTH=1` 即可强制）
- TODO #9 MCP execution broker（仅注册表，没有执行）
- TODO #10 Skill 沙箱执行策略

---

## 五、可继续验证的命令

```bash
# 完整冒烟
bash /tmp/rpg_audit_final.sh

# 启动服务
cd rpg/
../rpg_env/bin/python -m uvicorn ui:app --host 127.0.0.1 --port 7860

# 手动跑一轮 chat
curl -N -X POST http://127.0.0.1:7860/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"测试"}'

# 看 DB 真实写入
PGDATABASE=rpg_platform psql -c "
SELECT id, turn, role, length(content) FROM messages ORDER BY id DESC LIMIT 5;
SELECT id, turn, status, duration_ms FROM context_runs ORDER BY id DESC LIMIT 5;
"
```
