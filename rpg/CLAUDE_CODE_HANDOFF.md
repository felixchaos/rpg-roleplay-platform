# Claude Code Handoff - RPG Backend

交接时间：2026-05-24 Asia/Shanghai

工作目录：

```text
/Volumes/我的电脑/我穆蕾莉娅不爱你/我蕾穆丽娜不爱你/rpg
```

本地服务：

```text
http://127.0.0.1:7860
当前监听进程：Python PID 34131
启动命令：../rpg_env/bin/python -m uvicorn ui:app --host 127.0.0.1 --port 7860
```

## 用户核心要求

1. 后端优先，不要在用户明确要求前继续重构前端 UI。
2. 存档必须数据库优先，JSON 只能作为本地兼容镜像/恢复垫片。
3. 一个存档是一棵巨型分支树；当前游戏是某个分支节点的 runtime checkout，参考 Git 的 commit/ref/worktree 思路。
4. 支持百万字小说：章节拆分、ChapterFact、世界书、人设卡、RAG 召回、上下文构建必须可扩展。
5. `/set` 是用户强制设定命令，LLM 必须能按用户授权修改时间线、世界观、设定、人设、页面变量等支持字段。
6. 子代理和主 GM 要分离：子代理负责上下文筛选/整理，主 GM 负责剧情生成；前端应能展示 agent 运行步骤，但目前继续后端构建模式。

## 当前存档安全状态

用户存档已经恢复并同步到数据库：

```text
saves/game_state.json
turn = 16
player.name = 杭雁菱
history length = 12
world.time = 图卢兹失守后翌日，柏林
```

曾经出现过一个 190 字节的小 runtime 文件覆盖了本地 JSON。已备份到：

```text
saves/backups/game_state_20260524_192658_pre_db_restore_tiny_runtime.json
```

完整可玩的备份来源：

```text
saves/backups/game_state_20260523_230012_before_scroll_timeline_fix.json
```

注意：现在 PostgreSQL 是权威源，`saves/game_state.json` 是兼容镜像。不要再把小 JSON 当成权威存档。

## 数据库状态

数据库：

```text
postgresql:///rpg_platform
```

关键表已存在：

```text
users
sessions
scripts
game_saves
branch_commits
branch_refs
runtime_checkouts
books
documents
document_chunks
chapter_facts
character_cards
worldbook_entries
game_sessions
messages
memories
worldline_variables
worldline_projections
context_runs
model_apis
model_entries
mcp_servers
imported_skills
```

最近验证计数：

```text
game_saves = 1
branch_commits = 15
branch_refs = 3
chapter_facts = 1315
document_chunks = 4655
model_apis = 2
model_entries = 5
```

当前用户/剧本/存档常用 ID：

```text
user_id = 6
username = admin
script_id = 6
save_id = 6
active_commit_id = 15
active_ref_id = 3
```

## 已完成的后端改动

### PostgreSQL 平台表

主要文件：

```text
platform_app/db.py
```

已补齐知识库、运行态、上下文运行、模型注册表、MCP/Skill 注册表等表结构。

### DB-first 分支/存档/runtime

主要文件：

```text
platform_app/branches.py
platform_app/runtime.py
platform_app/workspace.py
```

当前模型：

```text
game_saves            = 一个用户存档
branch_commits        = 不可变剧情提交/回合快照
branch_refs           = 分支指针，类似 refs/heads/*
runtime_checkouts     = 当前可玩 worktree/runtime
saves/game_state.json = 本地镜像
```

`continue_from()` 已改为创建/激活 ref，而不是先塞一个假的 branch commit。新剧情 commit 应在下一次 GM 生成成功后由 `record_runtime_turn()` 创建。

`persist_runtime_state()` 已增加快照质量保护：如果传入 runtime 文件明显比数据库快照差，不会让小 JSON 覆盖完整 DB 快照。

### 剧本导入与 ChapterFact

主要文件：

```text
chapter_splitter.py
platform_app/script_import.py
platform_app/knowledge.py
retrieval.py
```

导入剧本后会同步：

```text
documents
document_chunks
chapter_facts
character_cards
worldbook_entries
```

`retrieval.py` 已优先尝试 PostgreSQL ChapterFact / chunks，再保留旧 SQLite/vector fallback。

### 记忆/世界线/上下文运行记录

主要文件：

```text
platform_app/knowledge.py
ui.py
state.py
context_agent.py
context_engine.py
gm.py
```

`ensure_game_session()` 会把 state snapshot 同步进：

```text
game_sessions
memories
worldline_variables
worldline_projections
```

每轮上下文子代理运行会写：

```text
context_runs
```

每轮玩家/GM 消息会写：

```text
messages
```

### 模型/API 注册表

主要文件：

```text
model_registry.py
platform_app/db.py
```

模型树已数据库化：

```text
model_apis
model_entries
app_config.selected_model
```

JSON 文件 `config/model_catalog.json` 只作为镜像/兼容。

### MCP / Skill 注册表

主要文件：

```text
tool_registry.py
platform_app/db.py
```

MCP/Skill 已数据库化：

```text
mcp_servers
imported_skills
```

`config/mcp_servers.json` 和 `user_skills/` 仍作为本地镜像/文件承载。服务器非管理员模式应禁用 Skill 导入；本地部署允许导入。

### 安全与跨域

主要文件：

```text
ui.py
platform_app/api.py
platform_app/auth.py
platform_app/library.py
tool_registry.py
```

已检查：

```text
平台 API 基本经过 require_user
主游戏 API 在非本地部署要求登录
MCP/Skill/模型配置写入为 admin 接口
文件库 safe_path 限制在 user_<id> 根目录内
恶意 Origin POST /api/stop 返回 403
聊天附件已改为 uploads/user_<id>/ 或 uploads/local/
```

## 最新验证命令

语法检查：

```bash
../rpg_env/bin/python -m py_compile \
  platform_app/db.py platform_app/runtime.py platform_app/branches.py \
  platform_app/workspace.py platform_app/knowledge.py platform_app/script_import.py \
  platform_app/api.py platform_app/auth.py platform_app/library.py \
  retrieval.py state.py gm.py context_agent.py model_registry.py tool_registry.py ui.py
```

数据库/检索烟测：

```bash
../rpg_env/bin/python - <<'PY'
from platform_app.db import init_db, connect
from platform_app import branches, knowledge
from state import SAVE_FILE

init_db()
print(branches.persist_runtime_state(str(SAVE_FILE), user_id=6)["ok"])
ctx = knowledge.retrieve_script_context(
    6,
    "图卢兹失守 柏林 蕾穆丽娜",
    chapter_min=1309,
    chapter_max=1315,
    top_k=1,
)
print("Postgres ChapterFact" in ctx, len(ctx))
PY
```

API 烟测：

```bash
curl -sS http://127.0.0.1:7860/api/state
curl -sS http://127.0.0.1:7860/api/tools
curl -sS http://127.0.0.1:7860/api/models
curl -sS -i -X POST http://127.0.0.1:7860/api/stop -H 'Origin: https://evil.example'
```

已确认：

```text
/api/state => turn 16, 杭雁菱, history 12
/api/tools => ok
/api/models => ok
恶意 Origin => 403
```

## 重要注意事项

1. 不要把 `claude_design_upload/current_code/` 当作源代码改；那是给 Claude Design 的设计包。
2. 不要删除 `saves/backups/`。用户很在意存档安全。
3. 不要再让 `saves/game_state.json` 单方面覆盖 DB。若要同步，请走 `branches.persist_runtime_state(..., state_data=state.data)`。
4. 如果要继续后端迭代，先确认 `platform_data/runtime.json` 的 active save/ref/commit，再操作分支。
5. 前端后续会由 Claude Design 重新设计；现在除非用户明确要求，不要继续大改 `ui.py` 中的 UI 部分。
6. `ui.py` 目前同时包含 FastAPI 主游戏后端和旧内嵌前端 HTML；后续最好拆分，但用户当前要求是后端先稳定。

## 剩余后端 TODO

优先级从高到低：

1. 补一个正式的 `state_repository.py`：统一从 `runtime_checkouts.state_snapshot` / `game_saves.state_snapshot` 加载状态，减少直接读写 `SAVE_FILE`。
2. 给 `branch_commits` 与 `runtime_checkouts` 增加更清楚的 worktree dirty 状态：让前端知道当前 runtime 是否领先 active commit。
3. 给 `/api/branches/continue` 返回更明确的 `runtime_url`、`active_ref`、`checkout_dirty` 字段，并保证前端点击继续后能直接进入游戏。
4. 给 `worldline_variables` / `memories` 增加查询 API，未来右侧面板不要只读 JSON snapshot。
5. 给 `context_runs` 加状态流：running / stopped / done / failed，方便前端显示类似 Codex/Claude Code 的运行标记。
6. 补 DB migration/versioning，不要长期靠 `create table if not exists` + `alter add column`。
7. 补正式安全审计 artifact：威胁模型、发现、验证、攻击路径。目前只做了实用级人工扫描与烟测。
8. 更严谨处理本地模式鉴权：当前 local 默认不强制登录，服务器部署必须设置 `RPG_REQUIRE_AUTH=1` 或 `RPG_DEPLOYMENT_MODE=server`。
9. MCP 现在只是配置注册表，还没有真正启动/代理 MCP server。后续应设计 MCP execution broker。
10. Skill 导入有 zip-slip 防护和大小限制，但还没有内容沙箱/签名/隔离执行策略。

## 给 Claude Code 的下一步建议

先不要碰 UI。建议按这个顺序继续：

```text
1. 新建 state_repository.py，把状态加载/保存彻底 DB-first。
2. 调整 ui.py 里的 _ensure_loaded / api_chat / api_save，让它们调用 repository，而不是直接依赖 SAVE_FILE。
3. 给 runtime_checkouts 加 dirty/worktree fields，明确“运行态领先提交树”的状态。
4. 补 branches API 的 contract test，尤其是 continue_from -> chat -> record_runtime_turn。
5. 做一次 repository-wide security scan artifact，重点是 auth、CORS、upload、Skill/MCP、file download。
6. 最后再等待 Claude Design 产出的前端设计，统一重建前端。
```

## 快速恢复命令

如果本地服务挂了：

```bash
cd /Volumes/我的电脑/我穆蕾莉娅不爱你/我蕾穆丽娜不爱你/rpg
../rpg_env/bin/python -m uvicorn ui:app --host 127.0.0.1 --port 7860
```

如果存档又被小 JSON 覆盖：

```bash
cp saves/game_state.json saves/backups/game_state_$(date +%Y%m%d_%H%M%S)_pre_restore.json
cp saves/backups/game_state_20260523_230012_before_scroll_timeline_fix.json saves/game_state.json
../rpg_env/bin/python - <<'PY'
from platform_app import branches, knowledge
from state import SAVE_FILE
import json
s = json.loads(SAVE_FILE.read_text())
branches.persist_runtime_state(str(SAVE_FILE), user_id=6, state_data=s)
knowledge.ensure_game_session(6, 6, s)
print("restored", s.get("turn"), len(s.get("history") or []), s.get("player", {}).get("name"))
PY
```
