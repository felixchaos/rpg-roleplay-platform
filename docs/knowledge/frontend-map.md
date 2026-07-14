# 前端地图(frontend/src/)

React(Vite + Cloudscape Design)前端逐目录/模块职责。给 AI 协作者:找「某 UI 住哪」先查这里。
权威真相源(api-client / storage / toast / AgentModelPicker)见根 `CLAUDE.md`,不在此重复。
**UI 铁律**:不要 emoji / 装饰图标(状态用文字/边框/高亮,图标用 Cloudscape `iconName`);加设置项前先 grep 后端有没有真读那个 pref。

## 构建与入口

- **多入口**:`entries/`——`platform.jsx`(平台主 SPA)、`game-console.jsx`(游戏台)、`tavern.jsx`(酒馆)、`login.jsx`。对应根 HTML(`Platform.html`/`Game Console.html`/`Tavern.html`/`Login.html`)。Vite 配置见 `frontend/vite.config.js`。
- `router.js` — Platform SPA 轻量 History 路由(`plNavigate(id)` + `pl-navigate` 事件,干净 URL)。
- **顶层 App 壳**:`platform-app.jsx`、`game-app.jsx`、`tavern-app.jsx`、`login-app.jsx`。

## 核心通信/工具(必用,别自造)

- `api-client.js` — **后端 API 单一入口**,装 `window.api.<group>.<method>()`,同源 Cookie 会话 + SSE helper(`/api/chat`、`/api/opening`),离线回退 MOCK。
- `lib/storage.js` — localStorage 封装(`lsGet`/`lsSet`/`lsGetJSON`/`lsSetJSON`/`lsRemove`)。`lib/prefs.js` — 偏好读写。`lib/creds.js`/`lib/crypto-safe.js` — 凭据/加密。
- `toast.jsx` — **双 toast 总线**:`createToastChannel` + `setWindowToast`(平台 `window.toast`)/ `setApiToast`(游戏 `window.__apiToast`)。两条独立总线是刻意的,别合并、别自己 mount stack。
- `data-loader.js`、`state-event-bridge.js`、`runtime-telemetry.js`/`web-vitals-rum.js`(埋点)、`i18n/`(`index.js` + `locales/en.json`、`zh-CN.json`)。

## 页面(pages/)

整页视图:`admin.jsx`(**已拆薄为转发壳**,实现住 `components/admin/`)、`cards.jsx`(**已拆薄为路由壳**,实现住 `components/cards/`)、`saves.jsx`(**已拆薄为路由壳**,实现住 `components/saves/`)、`feedback.jsx`、`device.jsx`、`rath.jsx`(RATH 观测台)、`tavern.jsx`、`md-editor.jsx`(**已拆薄为转发壳**,三栏 IDE 剧本编辑器实现住 `components/md-editor/`)、`script-review.jsx`、`script-edit-canon.jsx`/`script-edit-worldbook.jsx`、`script-modules-panel.jsx`。

- **[拆分中]** `settings.jsx` 与 `scripts.jsx` 正被拆到 `components/settings/`、`components/scripts/`。当前壳很薄,**以完成后结构为准,别按行号引用**。TODO:拆分收尾后补全本节子模块清单。
- **[已拆分]** `admin.jsx` 只保留 14 个管理页组件的具名 export 转发(供 `platform-app.jsx` 再转发给 `entries/platform.jsx`);实现全部住 `components/admin/`(见下)。
- **[已拆分]** `saves.jsx` 只保留 `SavesPage` 路由壳 + 具名 export 转发(`SavesPage`/`SavesListView`/`BranchesPage`/`ContinuePicker`/`NewGameModal`);实现住 `components/saves/`(见下)。
- **[已拆分]** `cards.jsx` 只保留 `CardsPage` 路由壳 + 具名 export 转发(共 12 个:`CardsPage`/`CardGrid`/`UserCardsView`/`NpcCardsView`/`CardEditModal`/`TavernImportModal`/`CardSheet`/`cardSnippet`/`CardEditFields`/`cardFormInit`/`cardFormPayload`/`npcToUserCardBody`);实现住 `components/cards/`(见下)。外部引用面广(`entries/platform`、`tavern-drawer`/`pages/tavern`/`tavern-app`、`components/scripts/ScriptDetail`、`components/saves/NewGame`、`mobile/pages/MobileCards`),转发壳保持这些 import 不变。
- **[已拆分]** `md-editor.jsx` 只保留 12 行转发壳:`import './md-editor.css'`(模块级副作用留壳)+ `export default MdEditorPage`(默认导出供 `entries/platform.jsx` lazy 边界不变)+ `export { nodeKey }` 具名转发;实现住 `components/md-editor/`(见下)。

## 组件(components/)

- `components/platform/` — 平台外壳页:`AuthPage.jsx`、`LibraryPage.jsx`、`ModulesPage.jsx`、`ProfilePage.jsx`、`UsagePage.jsx`、`CapPages.jsx`、`UnifiedSearch.jsx`、`DialogHost.jsx`、`WelcomeModal.jsx`、`achievements.jsx`、`me-pages.jsx`、`shared.jsx`。
- `components/settings/` — 设置分区(settings.jsx 拆分目标):`account-section`、`models-section`/`module-models-section`/`modelparams-section`、`memory-section`、`perm-section`、`deploy-section`、`danger-section`、`pref-sections`。
- `components/admin/` — 后台管理分页(admin.jsx 拆分目标):`shared.jsx`(`fmtTime` 工具)、`users-section`、`usage-section`、`audit-section`、`health-section`、`logs-section`、`registration-section`、`security-section`、`dmca-sections`(下架队列 + Strike 两页)、`csam-section`、`aup-section`、`maintenance-section`、`feedback-section`、`achievements-section`。
- `components/scripts/` — 剧本相关:`ScriptsList.jsx`、`ScriptDetail.jsx`、`ScriptsImport.jsx`。
- `components/saves/` — 存档相关(saves.jsx 拆分目标):`SavesList.jsx`(`SavesListView` + 就地设置表单/分支节点列表/导出弹窗)、`Branches.jsx`(`BranchesPage` git-graph 页 + `ContinuePicker` 继续游戏选择器,守卫测试 read_text 断言此文件)、`NewGame.jsx`(`NewGameModal` 新游戏向导 + 剧本就绪判定纯工具)。
- `components/cards/` — 角色卡相关(cards.jsx 拆分目标,纯机械搬家零行为变化):`helpers.js`(纯工具 + mock 数据:`cardFormInit`/`cardFormPayload`/`npcToUserCardBody`/`cardSnippet`/`_oneLine`/`clampLines`/`ELLIPSIS_1`/`USER_CARDS`/`NPC_CARDS`,被 mobile/tavern/saves/scripts 借用)、`CardFields.jsx`(`CardEditFields` 共享字段组 + `CardSheet` 只读档 + `SkillContentSection` 人格 skill 折叠拉取)、`CardGrid.jsx`(`CardGrid` 卡面网格 + `promoteNpcToUserCard` NPC→用户卡迁移,历史病灶)、`CardDetailPanel.jsx`(`CardDetailPanel` 详情面板 + `PersonaImageGallery`/`PersonaThumbStrip` 人设图,含 `CardAv`/头像刷新逻辑)、`TavernImportModal.jsx`(酒馆卡/聊天导入弹窗)、`CardEditModal.jsx`(EC2 式全屏编辑器 + 克隆/转卡流程)、`CardViews.jsx`(`UserCardsView`/`NpcCardsView`/`OnlineCardsView` 三列表视图)。
- `components/md-editor/` — 剧本三栏 IDE(md-editor.jsx 拆分目标,纯机械搬家零行为变化):`helpers.js`(常量 + 无 UI 工具:`IS_MAC`/`NODE_GROUPS`/`api`/`toast`/`stripChapterPrefix`/`canonTypeZh`/`KIND_ICON`/`CAN_*`/`nodeKey`/`kindLabelZh`)、`node-crud.js`(树内实体增删改 `createNode`/`renameNode`/`deleteNode` + 分组列表 `fetchGroupList`)、`node-io.js`(节点内容读写:`loadNodeContentMeta` 加载→toMd、`saveNodeContent` diff-only 保存 + front-matter 冻结闸)、`ContextMenu.jsx`(三处共用右键菜单)、`TbIcon.jsx`(顶栏图标)、`FileTree.jsx`(左栏资源管理器 + `NewMenu`)、`EditorPane.jsx`(中栏编辑器外壳)、`QuickOpen.jsx`(Mod+P)、`GlobalSearch.jsx`(Mod+Shift+F 全书检索)、`ChapterHistory.jsx`(版本历史)、`WritingRules.jsx`(写作规范)、`ProblemsPanel.jsx`(审稿问题面板)、`MdEditorPage.jsx`(页面壳 + 状态编排,默认导出;agent 写库 vs 未保存改动互覆盖 / autosave 竞态相关 handler `openConflictMerge`/`saveTab`/`refreshTab`/`proposeChapterDiff` 原样保留=历史病灶,勿改动)。
- `components/game/` — 游戏台侧栏面板族(game-panels.jsx 拆分目标,每个 tab / section 一文件):`RightPanel.jsx`(`RightPanel` 外壳 + `PANEL_TABS`)、`PanelStatus.jsx`(状态 tab:`_statusProfileFor`/`Module`+`NovelStatusProfile`/`PanelStatus`,守卫 `test_status_panel_content_pack_profile` read_text 断言此文件)、`PanelRules.jsx`(5E 规则 tab,守卫 `test_panel_rules_isolation` read_text 断言此文件)、`PanelMemory.jsx`(记忆 tab,pinned/notes/facts 桶各自独立渲染路径=历史病灶,勿统一)、`PanelWorldbook.jsx`、`PanelCharacters.jsx`(三层人物 + `CharacterCard`)、`PanelTimeline.jsx`(时间线 + `WorldlineAnchorsSection`)、`PanelContext.jsx`(上下文 + DemandLedger)、`PanelDebug.jsx`、`ForcedSetSection.jsx`、`WorldbookSections.jsx`(`WorldbookOverlaySection` + `RegexScriptsSection`,酒馆抽屉/设置/移动共用)、`InlineEditField.jsx`(共用 inline editor)。
- **可复用**:`AgentModelPicker.jsx`(**按需 LLM 增强统一用它**)、`Modal.jsx`/`ConfirmDialog.jsx`/`DetailDrawer.jsx`、`DesktopDialogHost.jsx`(桌面裸 confirm/prompt 的抽屉 Host)、`GlobalTaskFloater.jsx`(全局后台任务浮窗)、`HelpDrawer.jsx`(文档 iframe)、`ErrorBoundary.jsx`、`ToolCallBlock.jsx`、`PolicyNoticeBanner.jsx`、`AdultSplash.jsx`、`ModelConfigInterceptModal.jsx`。
- **编辑器/媒体**:`CodeMirrorEditor.jsx`、`MdEditorAgent.jsx`(写作搭档)、`EditorKbPanel.jsx`、`EditorPlaytest.jsx`、`GmStyleEditor.jsx`、`MediaStudio.jsx`/`MediaUploadZone.jsx`/`GenerateImageModal.jsx`/`ImageLightbox.jsx`/`ImageSizePicker.jsx`、`ModuleMatrixOverview.jsx`/`ModuleStatusCard.jsx`、`RebuildEstimateModal.jsx`/`RebuildJobBanner.jsx`、`AcceptanceAbPanel.jsx`(A/B 验收)、`FileLibrary.jsx`、`AvatarImg.jsx`/`CharacterCardHero.jsx`。

## 游戏台(顶层散件)

`game-app.jsx`(壳)、`game-composer.jsx`(输入区)、`game-panels.jsx`(**已拆薄为转发壳**,侧栏面板实现住 `components/game/`)、`game-icons.jsx`、`console-assistant-navigation.jsx`、`branch-graph.jsx`(分支图)、`narrative-strip.js`、`markdown-render.jsx`、`worldbook-status-toast.js`。样式:`game-console.css`、`markdown-render.css`、`tavern.css`、`platform.css`、`media.css`、`motion.css`、`tokens.css`。

## Hooks / UI kit

- `hooks/` — `useTavernChatRun.js`(酒馆聊天回合)、`useImageGeneration.js`、`useStickToBottom.js`。
- `ui/` — `kit.jsx` + `kit.css`(共享 UI 原子)。
- `cloudscape-theme.js`、`responsive.jsx`、`ui-atlas.js`、`agent-modules.js`、`mock-data.js`。

## 移动 Web 壳(frontend/src/mobile/)

**注意:这是 web 前端内的响应式移动壳(`?m2=1` 门控),不是顶层 `mobile/` 那个独立 Expo app。**
`MobileRoot.jsx`(壳)、`MobileHome.jsx`、`chrome.jsx`、`launch.js`、`Composer.jsx`/`Field.jsx`/`Sheet.jsx`/`dialog.jsx`/`icons.jsx`、`game/`(`MobileGame.jsx`、`panels.jsx`)、`pages/`(`MobileSaves`/`MobileScripts`/`MobileCards`/`MobileTavern`/`MobileSettings`/`MobileMe`/`MobileNewGame`/`MobileCaps`/`MobileAdmin`)。

## 测试

- `__tests__/`(vitest 单测,真 EditorView / 真组件)、`e2e/`(Playwright,`playwright.config.js`)。
- 跑法:`cd frontend && npm run build && npm test`。类型:`npm run typecheck`(见 `TYPESCRIPT.md`)。
