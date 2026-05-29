# rpg-rust — Python → Rust 迁移工作区

迁移分支:`rust-migration`(不影响 main)
启动:2026-05-29

## 状态

[ ] 工具链就绪(brew install rust 后台安装中)
[ ] workspace 骨架
[ ] rpg-core / rpg-schemas / rpg-db / rpg-rules / rpg-rules-bridge / rpg-retrieval / rpg-routes (sonnet)
[ ] rpg-llm / rpg-state / rpg-context / rpg-agents / rpg-platform / rpg-server (opus)
[ ] cargo check 全 workspace 通过

## crate 拓扑

```
rpg-core         配置/日志/error/secrets
  ↑
rpg-schemas      serde 数据模型
  ↑
rpg-db           sqlx pool + migrations + repos
  ↑
rpg-llm          Anthropic / Vertex / OpenAI 客户端
  ↑
rpg-state        GameState + apply_ops (动态 JSON 路径)
  ↑
rpg-rules / rpg-rules-bridge / rpg-retrieval / rpg-tools-dsl
  ↑
rpg-context  →  rpg-agents
  ↑
rpg-platform
  ↑
rpg-routes  →  rpg-server (axum main)
```

## 与 Python 源对应

| Rust crate | Python 源 | 行数 |
|---|---|---|
| rpg-core | `rpg/core/` + `rpg/model_registry.py` | ~700 |
| rpg-schemas | `rpg/schemas/` | 290 |
| rpg-db | `rpg/db/*.sql` + `rpg/platform_app/db/` | ~1500 |
| rpg-rules | `rpg/rules/` | 1182 |
| rpg-rules-bridge | `rpg/rules_bridge/` | 1147 |
| rpg-retrieval | `rpg/retrieval.py` + `rpg/chapter_fact_indexer.py` | ~500 |
| rpg-llm | `rpg/agents/gm/backends/` + `rpg/chat_pipeline.py` + `rpg/model_probe.py` | ~2000 |
| rpg-state | `rpg/state.py` + state mixins | ~1800 |
| rpg-context | `rpg/context_engine/` + `rpg/context_providers/` | 2791 |
| rpg-agents | `rpg/agents/` (除 gm/backends) | ~3400 |
| rpg-platform | `rpg/platform_app/` (除 db/) | ~12000 |
| rpg-routes | `rpg/routes/` | 2444 |
| rpg-tools-dsl | `rpg/tools_dsl/` + skill_executor | ~500 |
| rpg-server | `rpg/app.py` + startup | ~2000 |

合计 ~30k Python 源,目标 ~35-50k Rust(类型扩展系数)。

## 设计决策(锁定)

1. **HTTP 框架**:axum 0.7 + tower-http(CORS/Gzip/Trace),tokio runtime
2. **DB**:sqlx 0.8 + pgvector 0.4,advisory_lock 迁移
3. **LLM SDK**:reqwest + eventsource-stream 自封 Anthropic;yup-oauth2 + REST Vertex;async-openai 直用
4. **状态**:GameState.data 用 `serde_json::Value`,路径访问器(JSON Pointer 风格),保留运行时灵活性
5. **错误**:thiserror crate-level,anyhow 在 bin
6. **日志**:tracing + tracing-subscriber,JSON 输出可切
7. **AppState**:`Arc<AppState { db: PgPool, llm: LlmRouter, state_store: DashMap<UserId, Arc<RwLock<GameState>>>, ... }>`,显式注入,根除 `from app import` 反模式

## 翻译范围

全量翻译。前端不动(保留浏览器内 React + JSX,见审计报告)。
