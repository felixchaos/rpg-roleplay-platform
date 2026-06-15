# N — VSCode 风 Markdown 编辑器(剧本内联编辑 + agent 直写)

状态:施工中(2026-06-16)。用户:晓卡。**先本地完整测试,后发布**(不同步上线)。
决策(用户拍板):**完整版 / CodeMirror 6 / agent 端到端直写库**。

## 0. 目标
剧本所有者在一个三栏 IDE 里编辑某剧本的全部知识资产:
- **左**:文件树 —— 章节正文 / NPC 角色卡 / 世界书 / 时间线锚点 / canon 实体(按类型分组、可折叠)。
- **中**:多标签 CodeMirror 6 编辑器,每个实体 = 一个 md 文档(YAML front-matter 结构化字段 + 正文)。VSCode 式脏点 + 可关标签。
- **右**:agent 面板(复用 `console_assistant` SSE),可**端到端直写库**(过 owner 闸 + 二次确认 + 审计)。

## 1. 架构(复用为主)
- 后端读写端点 95% 已存在(`api.scripts.*` / `api.cards.*`),**只补缺口**(见 §3)。
- agent 后端 = `console_assistant`(完整 SSE/工具/确认框架),**只补 script 级写工具**(见 §5)。
- 前端新页面 `pages/md-editor.jsx` 挂进 Platform SPA(History 路由);CodeMirror 6 首次引入 React.lazy 懒加载。

## 2. md 序列化契约(`frontend/src/lib/md-serialize.js`,最高正确性风险)
每实体一份 schema:`{ bodyField, frontMatter:[{key,type}], arrayFields, ... }`。`toMd(row)→text` / `fromMd(text)→patch`。
- **YAML front-matter** 承载结构化标量 + 数组;**正文** = 该实体的主文本列。
- 字段类型:`scalar` / `text[]`(timeline.keywords,**写回走原生 ARRAY 非 jsonb**)/ `jsonb-strlist`(aliases/keys/tags)/ `jsonb-objlist`(sample_dialogue/relationships)/ `jsonb-open`(attrs,原样 YAML 块)。
- **无损铁律**:`fromMd(toMd(row))` 必须等价于 row 的可写子集;不可写字段(id/created_at/embedding)只读回显、保存时剔除。regex_keys 单引号包裹防 YAML 转义坑。

| 实体 | 正文列 | 风险 | 本期处置 |
|---|---|---|---|
| script_chapters | content | 低 | 全字段往返 |
| worldbook_entries | content | 中 | 全字段(需扩写端点收 9 检索字段) |
| script_timeline_anchors | sample_summary | 低-中 | 全字段(需扩写端点 + keywords text[]) |
| character_cards(npc) | (无固定正文,正文留人工备注) | 中-高 | 全字段(avatar 走专用端点) |
| kb_canon_entities | background | 高 | 全字段;attrs 用 raw YAML 块;无 updated_at |
| chapter_facts | summary | 高(7 jsonb) | **只读展示**(本期不往返编辑) |

## 3. 后端缺口(Phase 1 要补)
- `worldbook` 写端点(PUT/POST)补收:keys/regex_keys/token_budget/insertion_position/sticky_turns/cooldown_turns/probability/character_filter/scene_filter。
- `canon-entities` 写端点(PUT/POST)补收:aliases/attrs/first_revealed_chapter/public_knowledge;**新增 GET 列表端点** `GET /api/v1/scripts/{id}/canon-entities`。
- `anchors` 写端点(PUT)补收:story_time_label/chapter_min/chapter_max/keywords/confidence/sample_title。
- `api-client.js` 补 `api.scripts.canonList/canonGet/canonUpsert/canonDelete` + anchor 的 update/create/delete wrapper。
- 全部走 `script_owned` 严格 owner 闸 + `_write_commit` 审计(复用)。

## 4. 前端三栏(Phase 0+3+4)
- 页面壳:`md-editor.jsx`,全幅(disableContentPaddings + standalone)。手写 CSS 用 tokens.css 变量(--bg/--panel/--text/--accent/--line),编辑区字体 --font-mono。
- 文件树:按实体类型分组(章节/角色卡/世界书/时间线/canon),懒加载(列表只拉摘要,点开拉全文 → toMd)。
- 标签编辑器:CodeMirror 6(markdown 语法 + 行号 + 查找)。每标签独立脏态;关标签若脏 → 确认。保存 = fromMd → 对应 REST。
- 复用:`ResizableSplit`、`AgentModelPicker`、全局 `window.__apiToast/__confirm/__prompt`、`lib/storage.js`。

## 5. agent 直写(Phase 5)
- 新建 `rpg/tools_dsl/command_tools_script_write.py`:`update_script_chapter`(destructive=True 覆盖)/`upsert_worldbook_entry`/`update_npc_card`/`update_anchor`/`upsert_canon_entity`。
  - executor 签名 `(user_id, script_id, args, state)`;**写鉴权强制 `script_owned`**(非 `_user_can_read_script`);origins=`{ui_button, api_direct, console_assistant}`。
  - 复用 `platform_app.script_import.update_chapter` 等现成写函数 + `_write_commit` 审计。
- 注册:`command_tools_register.ensure_registered()` 末尾加 try/import 块;`console_assistant/tools.py` PRIMARY 集加读+写工具名。
- system prompt:`prompts.build_system_prompt` 按 `page_context`(script_id + 当前打开文件)注入剧本编辑上下文。
- 前端 agent 面板:消费 console_assistant SSE(token/tool_call/tool_result/confirmation_required/done),弹二次确认,选模型(AgentModelPicker prefPrefix="script_editor"),写完刷新对应标签。
- 鉴权链:`page_context.script_id → _validate_owned_script_id → dispatch scope=script → executor 内 script_owned 兜底`。

## 6. 页面接线(Phase 0,确切位点见 recon)
- `entries/platform.jsx`:PL_IDS 加 `md-editor`;if/else 树加 `React.lazy` + `<Suspense>` 分支。
- `platform-app.jsx`:getCSModules 加模块条目、getPLTitles 加标题、standalone 加 `md-editor`、disableContentPaddings 加 `md-editor`。
- `vite.config.js`:manualChunks 加 `codemirror` chunk + `md-editor` 页 chunk;optimizeDeps.include 加 @codemirror/*。
- `npm i @codemirror/state @codemirror/view @codemirror/lang-markdown @codemirror/commands @codemirror/language @codemirror/search`(+ 暖色主题用 EditorView.theme 映射 CSS 变量)。

## 7. 施工阶段(每阶段本地测)
- **P0** deps + 页面接线 + 空三栏壳 → 页面能开、build 绿。
- **P1** 后端缺口端点 + api-client wrapper → pytest + curl 全字段往返。
- **P2** md-serialize.js + vitest 往返单测。
- **P3** 文件树 + 多标签 CodeMirror + 脏态 → 浏览器 e2e 打开/编辑/保存。
- **P4** 各实体读写接线全打通 → 本地 DB 全 CRUD。
- **P5** agent 直写(后端工具 + 前端面板)→ agent 改卡/章 → 库更新 → 编辑器刷新。
- **P6** 全栈本地 e2e + 对抗评审 → 全绿才发布。

## 8. 红线
- **不同步上线**:本地完整测过、用户确认后才部署。
- 写鉴权一律 `script_owned`(严格 owner);agent 写过二次确认 + 审计。
- chapter_facts 本期只读(7 jsonb 往返风险高)。
- CodeMirror 懒加载(2-3MB,不进主 bundle)。

关联:[[project_progress_advancement]] [[project_oss_repo]] [[project_rpg_deploy]] [[feedback_no_emoji_ui]] [[feedback_ondemand_llm_ui]]
