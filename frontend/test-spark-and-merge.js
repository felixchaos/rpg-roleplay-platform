#!/usr/bin/env node
/* 任务 4 + 5 的最小回归 smoke：
 *  - Spark({values:[]}) / [single] / [全 NaN] 都不产生包含 "NaN" 的 d
 *  - data-loader 的深合并语义在 backend partial state 下保留 baseline 嵌套字段
 *  - ConfirmStrip 在 undefined pendingWrites/pendingQuestions 下不抛
 *
 * 不依赖浏览器，靠 Node + jsdom 跑组件渲染。失败 exit(1)。
 */
"use strict";

const fs = require("fs");
const path = require("path");
const babel = require("@babel/standalone");
const backendPyPath = fs.existsSync(path.join(__dirname, "../rpg/ui.py"))
  ? path.join(__dirname, "../rpg/ui.py")
  : path.join(__dirname, "../rpg/app.py");

// ---- 极简浏览器 shim ----
global.window = global;
global.document = { createElement: () => ({}), getElementById: () => null };

// React umd 来自 unpkg；直接用 npm 装的 react/react-dom
global.React = require("react");
const ReactDOMServer = require("react-dom/server");

// ---------------------------------------------------------------------------
// 1) data-loader 的 deepMergeInto 行为：从源码里抠出来当单元测试
//    用 brace-counting 提取整段 function（regex 抠不准 nested {}）
// ---------------------------------------------------------------------------
const dlSrc = fs.readFileSync(path.join(__dirname, "src/data-loader.js"), "utf8");
function extractFunction(src, name) {
  // 兼容参数是解构对象的情况（function Spark({a,b={}})）：先用 ( ) 配对跳过参数表，
  // 再用 { } 配对取函数体。否则 indexOf("{") 会落在参数表的解构对象上，提早收口。
  const re = new RegExp("function\\s+" + name + "\\s*\\(");
  const m = re.exec(src);
  if (!m) return null;
  // 找参数表的 ) — 从 m[0] 末尾向后括号配对
  let i = m.index + m[0].length;
  let pdepth = 1;
  while (i < src.length && pdepth > 0) {
    const ch = src[i];
    if (ch === "(") pdepth++;
    else if (ch === ")") pdepth--;
    i++;
  }
  // 此时 i 在 ) 之后；找函数体 {
  while (i < src.length && src[i] !== "{") i++;
  if (i >= src.length) return null;
  let depth = 1; i++;
  while (i < src.length && depth > 0) {
    const ch = src[i];
    if (ch === "{") depth++;
    else if (ch === "}") depth--;
    i++;
  }
  return src.slice(m.index, i);
}
const deepMergeIntoSrc = extractFunction(dlSrc, "deepMergeInto");
if (!deepMergeIntoSrc) {
  console.error("FAIL · 未在 data-loader.js 找到 deepMergeInto");
  process.exit(1);
}
const deepMergeInto = new Function(deepMergeIntoSrc + "; return deepMergeInto;")();

function assertEq(actual, expected, label) {
  const aj = JSON.stringify(actual);
  const ej = JSON.stringify(expected);
  if (aj !== ej) { console.error(`FAIL · ${label}\n  expected ${ej}\n  actual   ${aj}`); process.exit(1); }
  console.log(`PASS · ${label}`);
}

// 缺失嵌套字段：baseline 的子字段必须保留
{
  const baseline = {
    player: { name: "X", inventory: [{ name: "怀表" }] },
    world: { timeline: { current_label: "序", current_phase: "起" }, known_events: ["evt"] },
    permissions: { mode: "default", pending_writes: [], pending_questions: [] },
  };
  const partial = {
    player: { name: "Y" },          // 没给 inventory
    world: { time: "10:00" },       // 没给 timeline / known_events
    permissions: { mode: "full_access" }, // 没给 pending_*
  };
  deepMergeInto(baseline, partial);
  assertEq(baseline.player.name, "Y", "player.name 被 partial 覆盖");
  assertEq(baseline.player.inventory.length, 1, "player.inventory 保留 baseline");
  assertEq(baseline.world.time, "10:00", "world.time 注入");
  assertEq(baseline.world.timeline.current_label, "序", "world.timeline.current_label 保留");
  assertEq(baseline.world.known_events.length, 1, "world.known_events 保留");
  assertEq(baseline.permissions.mode, "full_access", "permissions.mode 覆盖");
  assertEq(baseline.permissions.pending_writes.length, 0, "permissions.pending_writes 保留 baseline []");
}

// 数组整体替换语义
{
  const t = { list: [1, 2, 3] };
  deepMergeInto(t, { list: [9] });
  assertEq(t.list, [9], "数组整体替换（不是 union）");
}

// undefined 字段被跳过
{
  const t = { a: 1 };
  deepMergeInto(t, { a: undefined });
  assertEq(t.a, 1, "undefined 字段不覆盖现有值");
}

// ---------------------------------------------------------------------------
// 2) Spark 在退化数据下不产生 NaN
// ---------------------------------------------------------------------------
// 用 brace-counting 抠 Spark 函数（nested 大括号 regex 不可靠）
const pljsx = fs.readFileSync(path.join(__dirname, "src/platform-app.jsx"), "utf8");
const sparkJsx = extractFunction(pljsx, "Spark");
if (!sparkJsx) { console.error("FAIL · 未找到 Spark"); process.exit(1); }
const compiled = babel.transform(sparkJsx, { presets: ["react"] }).code;
const SparkFn = new Function("React", compiled + "; return Spark;")(global.React);

function renderD(values) {
  const el = global.React.createElement(SparkFn, { values });
  const html = ReactDOMServer.renderToStaticMarkup(el);
  return html;
}

for (const [label, values] of [
  ["empty array", []],
  ["single value", [42]],
  ["all NaN", [NaN, NaN, NaN]],
  ["mixed NaN/finite single survivor", [NaN, 5, NaN]],     // safe=1 → degraded path
  ["null values", [null, null]],
  ["normal series", [1, 2, 3, 4, 5]],
]) {
  const html = renderD(values);
  if (html.indexOf("NaN") >= 0) {
    console.error(`FAIL · Spark "${label}" 输出仍含 NaN：\n  ${html}`);
    process.exit(1);
  }
  if (!/d="M[0-9]/.test(html)) {
    console.error(`FAIL · Spark "${label}" 没有合法的 path d=：\n  ${html}`);
    process.exit(1);
  }
  console.log(`PASS · Spark(${label}) → no NaN, has valid d=`);
}

// ---------------------------------------------------------------------------
// 3) ConfirmStrip 在 undefined props 下不抛
// ---------------------------------------------------------------------------
// 不渲染整个组件（依赖 useState + 一堆 Icon），只验证「items 构造行不抛」。
// 把组件头部 5 行手抄成等价闭包，并断言其与源码同步（防止被人 revert 兜底）。
const gcjsx = fs.readFileSync(path.join(__dirname, "src/game-composer.jsx"), "utf8");
if (!/Array\.isArray\(pendingWrites\)\s*\?\s*pendingWrites\s*:\s*\[\]/.test(gcjsx)) {
  console.error("FAIL · ConfirmStrip pendingWrites 防御兜底丢失（被 revert?）");
  process.exit(1);
}
if (!/Array\.isArray\(pendingQuestions\)\s*\?\s*pendingQuestions\s*:\s*\[\]/.test(gcjsx)) {
  console.error("FAIL · ConfirmStrip pendingQuestions 防御兜底丢失");
  process.exit(1);
}
// task 46：旧锚要求 (it.data.choices || []).map；现在改成
// ((it.data.options || it.data.choices) || []).map 兼容后端真正的字段名 options。
// 旧 anchor 仍含 'it.data.choices'，所以只要正则同时找 options 兜底就行。
if (!/it\.data\.options\s*\|\|\s*it\.data\.choices/.test(gcjsx) || !/\)\s*\|\|\s*\[\]\)\.map/.test(gcjsx)) {
  console.error("FAIL · ConfirmStrip 缺 (it.data.options || it.data.choices) || [] 兜底（task 46）");
  process.exit(1);
}
console.log("PASS · game-composer.jsx ConfirmStrip 兜底三连仍在（含 task 46 options/choices 兼容）");

// 行为锚：等价 stub 跑一遍
function confirmStripItems({ pendingWrites, pendingQuestions }) {
  const writes = Array.isArray(pendingWrites) ? pendingWrites : [];
  const questions = Array.isArray(pendingQuestions) ? pendingQuestions : [];
  return [
    ...questions.map(q => ({ kind: "question", id: q.id })),
    ...writes.map(w => ({ kind: "write", id: w.id })),
  ];
}
for (const [label, props] of [
  ["both undefined", {}],
  ["pendingWrites undefined", { pendingQuestions: [] }],
  ["pendingQuestions undefined", { pendingWrites: [] }],
  ["both null", { pendingWrites: null, pendingQuestions: null }],
  ["mock-shaped", { pendingWrites: [{ id: "pw1" }], pendingQuestions: [{ id: "pq1" }] }],
]) {
  try {
    const items = confirmStripItems(props);
    console.log(`PASS · ConfirmStrip(${label}) → items.length=${items.length}, no throw`);
  } catch (e) {
    console.error(`FAIL · ConfirmStrip(${label}) 抛错：${e.message}`);
    process.exit(1);
  }
}

// ---------------------------------------------------------------------------
// 4) PanelStatus 同样的兜底锚
// ---------------------------------------------------------------------------
const gpjsx = fs.readFileSync(path.join(__dirname, "src/game-panels.jsx"), "utf8");
for (const needle of [
  /\(state && state\.player\) \|\| \{\}/,
  /\(state && state\.world\) \|\| \{\}/,
  /Array\.isArray\(p\.inventory\) \? p\.inventory : \[\]/,
  /Array\.isArray\(w\.known_events\) \? w\.known_events : \[\]/,
]) {
  if (!needle.test(gpjsx)) {
    console.error(`FAIL · PanelStatus 兜底丢失：${needle}`);
    process.exit(1);
  }
}
console.log("PASS · game-panels.jsx PanelStatus 嵌套字段兜底齐全");

// ---------------------------------------------------------------------------
// 5) Design Canvas 首屏不发缺失 sidecar 请求（task 6）
//    防止 GET /.design-canvas.state.json -> 404 控制台噪音
// ---------------------------------------------------------------------------
const dcjsx = fs.readFileSync(path.join(__dirname, "src/design-canvas.jsx"), "utf8");
if (!/DC_LOCALSTORAGE_KEY/.test(dcjsx)) {
  console.error("FAIL · design-canvas 没有 localStorage 主存储分支");
  process.exit(1);
}
// 必须有 `if (window.omelette)` gate 把 fetch 包起来，否则普通环境会 404
if (!/if\s*\(\s*window\.omelette\s*\)[\s\S]{0,200}fetch\(['"]\.\/' \+ DC_STATE_FILE/.test(dcjsx)) {
  console.error("FAIL · design-canvas 仍在无 omelette 环境下硬打 sidecar fetch");
  process.exit(1);
}
console.log("PASS · design-canvas.jsx 首屏不发 sidecar fetch（task 6）");

// ---------------------------------------------------------------------------
// 6) AuthPage 「忘记密码」点击有内联可见反馈（task 7）
//    旧：window.__apiToast?.(...) → Login 页 toast 不存在，optional chaining 吞掉
// ---------------------------------------------------------------------------
const plSrc = fs.readFileSync(path.join(__dirname, "src/platform-app.jsx"), "utf8");
if (!/const \[notice, setNotice\] = useStatePL\(""\);/.test(plSrc)) {
  console.error("FAIL · AuthPage notice state 缺失");
  process.exit(1);
}
if (!/setNotice\("请联系管理员重置密码/.test(plSrc)) {
  console.error("FAIL · 忘记密码点击没有 setNotice 内联反馈");
  process.exit(1);
}
if (!/className="pl-auth-notice"/.test(plSrc)) {
  console.error("FAIL · notice 渲染 DOM 缺失");
  process.exit(1);
}
console.log("PASS · AuthPage 忘记密码 内联反馈三件套（state + 渲染 + 点击 set）");

// ---------------------------------------------------------------------------
// 7) ConfirmStrip key 唯一性（task 8）
//    模拟 ConfirmStrip 的 items 构造，验证 backend 各种退化形态下 key 都唯一。
// ---------------------------------------------------------------------------
function confirmStripKeys({ pendingWrites, pendingQuestions }) {
  const writes = Array.isArray(pendingWrites) ? pendingWrites : [];
  const questions = Array.isArray(pendingQuestions) ? pendingQuestions : [];
  const items = [
    ...questions.map((q, i) => ({ key: `q:${q && q.id != null ? q.id : `idx${i}`}` })),
    ...writes.map((w, i) => ({ key: `w:${w && w.id != null ? w.id : `idx${i}`}` })),
  ];
  return items.map(i => i.key);
}

function assertUnique(keys, label) {
  const seen = new Set();
  for (const k of keys) {
    if (seen.has(k)) { console.error(`FAIL · ${label}: 重复 key=${k}（all=${keys.join(",")}）`); process.exit(1); }
    seen.add(k);
  }
  console.log(`PASS · ${label} → ${keys.length} 项全部唯一 key`);
}

assertUnique(confirmStripKeys({
  pendingWrites: [{ id: "pw-1" }, { id: "pw-2" }],
  pendingQuestions: [{ id: "pq-1" }],
}), "mock 形态（write/question id 已区分）");

assertUnique(confirmStripKeys({
  pendingWrites: [{ id: 1 }, { id: 2 }],
  pendingQuestions: [{ id: 1 }, { id: 2 }],
}), "数字 id 跨类型重叠（write 1 vs question 1）—— 复合 key 必须区分");

assertUnique(confirmStripKeys({
  pendingWrites: [{}, {}, {}],   // backend 完全没给 id
  pendingQuestions: [{}, {}],
}), "全部缺 id —— 必须用 index 兜底唯一");

assertUnique(confirmStripKeys({
  pendingWrites: [null, undefined, { id: null }],
  pendingQuestions: [null],
}), "null/undefined/id=null 都不抛且各自唯一");

// 注意：若 backend 在同一 kind 列表里给出 [{id:"x"},{id:"x"}]，前端无法凭空生成
// 不同 key。该场景是 backend 应修的 bug，超出本任务（task 8）前端兜底范围。

// 静态锚：源码里的 key 表达式必须是复合形式（防 revert）
if (!/key=\{it\.key\}/.test(gcjsx)) {
  console.error("FAIL · ConfirmStrip 主 map 没有使用复合 it.key");
  process.exit(1);
}
if (!/key=\{`\$\{it\.key\}:\$\{ci\}:\$\{c\}`\}/.test(gcjsx)) {
  console.error("FAIL · ConfirmStrip choices 没有用 (key, ci, c) 复合 key");
  process.exit(1);
}
console.log("PASS · game-composer.jsx ConfirmStrip key 复合表达式仍在");

// ---------------------------------------------------------------------------
// 8) TopBar 历史回顾 / 搜索本档 按钮接入了真实抽屉（task 9）
// ---------------------------------------------------------------------------
const gajsx = fs.readFileSync(path.join(__dirname, "src/game-app.jsx"), "utf8");
const gchtml = fs.readFileSync(path.join(__dirname, "Game Console.html"), "utf8");

// TopBar 搜索按钮必须绑了 onOpenSearch（修复前是空 onClick）
if (!/data-tip="搜索本档"[\s\S]{0,80}onClick=\{onOpenSearch\}/.test(gajsx)) {
  console.error("FAIL · 搜索本档 按钮未绑定 onOpenSearch");
  process.exit(1);
}
console.log("PASS · TopBar 搜索本档按钮已绑 onOpenSearch");

// 两个 Drawer 组件存在且导出到 window
for (const name of ["HistoryDrawer", "SearchDrawer"]) {
  if (!new RegExp(`function ${name}\\(`).test(gajsx)) {
    console.error(`FAIL · ${name} 组件未定义`); process.exit(1);
  }
  if (!new RegExp(`\\b${name}\\b`).test(gajsx)) {
    console.error(`FAIL · ${name} 未挂到 window`); process.exit(1);
  }
}
if (!/Object\.assign\(window,[^)]*HistoryDrawer[^)]*SearchDrawer/.test(gajsx)) {
  console.error("FAIL · HistoryDrawer/SearchDrawer 未一起 Object.assign 到 window");
  process.exit(1);
}
console.log("PASS · HistoryDrawer / SearchDrawer 组件已定义并 export");

// Game Console.html 要有 state + 渲染 + onOpen* 回调指向 setShow*
if (!/showSearchDrawer, setShowSearchDrawer/.test(gchtml)) {
  console.error("FAIL · Game Console.html 缺 showSearchDrawer state"); process.exit(1);
}
if (!/onOpenSearch=\{\(\) => setShowSearchDrawer\(true\)\}/.test(gchtml)) {
  console.error("FAIL · onOpenSearch 没绑 setShowSearchDrawer(true)"); process.exit(1);
}
if (!/onOpenHistory=\{\(\) => setShowHistoryDrawer\(true\)\}/.test(gchtml)) {
  console.error("FAIL · onOpenHistory 没绑 setShowHistoryDrawer(true)"); process.exit(1);
}
if (!/<HistoryDrawer open=\{showHistoryDrawer\}/.test(gchtml)) {
  console.error("FAIL · <HistoryDrawer> 未挂载"); process.exit(1);
}
if (!/<SearchDrawer open=\{showSearchDrawer\}/.test(gchtml)) {
  console.error("FAIL · <SearchDrawer> 未挂载"); process.exit(1);
}
console.log("PASS · Game Console.html 接入两个 drawer state/回调/挂载齐全");

// 抽屉本体必须有 onClose（可关闭）与 Esc 处理
for (const fn of ["HistoryDrawer", "SearchDrawer"]) {
  const re = new RegExp(`function ${fn}[\\s\\S]*?return ReactDOM\\.createPortal`);
  const m = gajsx.match(re);
  if (!m) { console.error(`FAIL · ${fn} 提取失败`); process.exit(1); }
  if (!/onClose/.test(m[0])) { console.error(`FAIL · ${fn} 缺 onClose`); process.exit(1); }
  if (!/Escape/.test(m[0])) { console.error(`FAIL · ${fn} 缺 Esc 关闭`); process.exit(1); }
}
console.log("PASS · 两个 drawer 都有 onClose + Esc 关闭");

// ---------------------------------------------------------------------------
// 9) currentSaveId / hash alias / 真实 stats（task 10 + 11 + 12）
// ---------------------------------------------------------------------------
// 10) Game Console 不再用硬编码 11
if (/currentSaveId=\{game\._raw\?\.save_id \|\| 11\}/.test(gchtml)) {
  console.error("FAIL · Game Console 仍有 hardcoded 11 兜底（task 10）"); process.exit(1);
}
if (!/currentSaveId=\{activeSave\?\.id \?\? null\}/.test(gchtml)) {
  console.error("FAIL · Game Console currentSaveId 未改为 activeSave?.id"); process.exit(1);
}
if (!/reloadSaves/.test(gchtml)) {
  console.error("FAIL · Game Console 未拉真实 saves"); process.exit(1);
}
console.log("PASS · Game Console.html 用真实 activeSave，不再 hardcode 11 (task 10)");

// 11) Platform.html 含 hash 别名 + 旧 link 已改成 saves-branches
const platformHtml = fs.readFileSync(path.join(__dirname, "Platform.html"), "utf8");
if (!/HASH_ALIASES.*branches.*saves-branches/.test(platformHtml.replace(/\n/g, " "))) {
  console.error("FAIL · Platform.html 缺 #branches → #saves-branches 别名"); process.exit(1);
}
// 旧 link 不应在 game-app.jsx / Design Canvas / Overview / index 留有 #branches
for (const f of ["src/game-app.jsx", "Design Canvas.html", "Overview.html", "index.html"]) {
  const src = fs.readFileSync(path.join(__dirname, f), "utf8");
  if (/Platform\.html#branches\b/.test(src)) {
    console.error(`FAIL · ${f} 仍有旧的 Platform.html#branches 链接（task 11）`); process.exit(1);
  }
}
console.log("PASS · 路由别名在位 + 所有 #branches 链接已迁移到 #saves-branches (task 11)");

// 12) ProfilePage 真实 stats：MOCK_PLATFORM.stats 不再被原样渲染
const plSrc12 = fs.readFileSync(path.join(__dirname, "src/platform-app.jsx"), "utf8");
// 必须有 realSaves.length 等真实派生
if (!/realSaves\.length/.test(plSrc12)) {
  console.error("FAIL · ProfilePage 没用 realSaves.length 派生存档数 (task 12)"); process.exit(1);
}
// 不应再渲染 stats.api_calls / 1000 之类的旧 mock 公式
if (/stats\.api_calls\s*\/\s*1000/.test(plSrc12)) {
  console.error("FAIL · ProfilePage 仍在用 stats.api_calls / 1000 渲染 mock 数字"); process.exit(1);
}
// 不应再有 branchLabel 假分支表（只在 ProfilePage 函数体内查，brace-match 截取）
const profileBody = extractFunction(plSrc12, "ProfilePage");
if (!profileBody) { console.error("FAIL · 未抽出 ProfilePage 函数体"); process.exit(1); }
if (/branchLabel\s*=/.test(profileBody)) {
  console.error("FAIL · ProfilePage 仍有 hard-coded branchLabel 赋值 (task 12)"); process.exit(1);
}
if (/Math\.floor\(\(s\.id \* 137\)/.test(profileBody)) {
  console.error("FAIL · ProfilePage 仍有「第 X 章」mock 公式 (task 12)"); process.exit(1);
}
console.log("PASS · ProfilePage 改用真实数据派生 stats + 移除 mock branchLabel/章号 (task 12)");

// data-loader：stats 现在派生自真实数组
const dlSrc12 = fs.readFileSync(path.join(__dirname, "src/data-loader.js"), "utf8");
if (!/platform\.stats\s*=\s*\{[\s\S]*scripts:\s*\(platform\.scripts/.test(dlSrc12)) {
  console.error("FAIL · data-loader stats 未从真实 platform.scripts 派生"); process.exit(1);
}
console.log("PASS · data-loader 把 platform.stats 改为真实派生 (task 12)");

// ---------------------------------------------------------------------------
// 10) task 13 / 14 / 15 锚点
// ---------------------------------------------------------------------------
// task 13: useReactiveUser + publishUser + MePage saveProfile 后广播
const plSrc13 = fs.readFileSync(path.join(__dirname, "src/platform-app.jsx"), "utf8");
if (!/function useReactiveUser\(/.test(plSrc13)) {
  console.error("FAIL · useReactiveUser hook 缺失 (task 13)"); process.exit(1);
}
if (!/function publishUser\(/.test(plSrc13)) {
  console.error("FAIL · publishUser 函数缺失 (task 13)"); process.exit(1);
}
if (!/window\.__publishUser/.test(plSrc13)) {
  console.error("FAIL · __publishUser 未 expose 到 window (task 13)"); process.exit(1);
}
// PlatformShell 必须用 reactiveUser 不用 platform.user 渲染左侧名片
const shellBody = extractFunction(plSrc13, "PlatformShell");
if (!shellBody) { console.error("FAIL · 提取 PlatformShell 失败"); process.exit(1); }
if (!/reactiveUser\.display_name/.test(shellBody)) {
  console.error("FAIL · PlatformShell 仍在用 platform.user.display_name 不响应保存 (task 13)"); process.exit(1);
}
console.log("PASS · task 13 useReactiveUser + publishUser 三件套");

// task 14: GameToastStack 组件存在 + window.toast 安装在 game-app.jsx 加载时
const gajsx14 = fs.readFileSync(path.join(__dirname, "src/game-app.jsx"), "utf8");
if (!/function GameToastStack\(/.test(gajsx14)) {
  console.error("FAIL · GameToastStack 组件未定义 (task 14)"); process.exit(1);
}
if (!/window\.toast\s*=\s*fire/.test(gajsx14)) {
  console.error("FAIL · game-app.jsx 未在加载时安装 window.toast (task 14)"); process.exit(1);
}
if (!/__GAME_TOAST_INSTALLED/.test(gajsx14)) {
  console.error("FAIL · 缺少重复安装防御 __GAME_TOAST_INSTALLED (task 14)"); process.exit(1);
}
if (!/GameToastStack/.test(gchtml)) {
  console.error("FAIL · Game Console.html 未渲染 <GameToastStack/> (task 14)"); process.exit(1);
}
console.log("PASS · task 14 toast 容器装好 + Game Console 已挂载");

// task 15 + task 21 (修订): NEVER use `__late` 包装器 — 它会让 Chrome renderer 崩溃。
// 改用直接 window.X || (() => null) 兜底，babel-standalone 按 DOM 顺序加载脚本，
// game-app.jsx (script #4) 必定在 inline (script #6) 之前执行，所以正常无需 late-binding。
if (/const __late = \(name\) =>/.test(gchtml)) {
  console.error("FAIL · 检测到 __late 包装器，必须移除（Chrome renderer crash）"); process.exit(1);
}
for (const name of ["HistoryDrawer", "SearchDrawer", "LeftRail", "TopBar", "ChatArea", "RunSteps", "GameToastStack"]) {
  if (!new RegExp(`const ${name} = window\\.${name} \\|\\| \\(\\(\\) => null\\)`).test(gchtml)) {
    console.error(`FAIL · ${name} 没用 const X = window.X || (() => null) 兜底 (task 21)`); process.exit(1);
  }
}
console.log("PASS · task 21 直接 window.X 兜底，禁用 __late 包装器");

// task 21 加强：staged mount 让首屏 Composer 立刻可见，重组件分批延后到 rAF
if (!/const \[mountStage, setMountStage\] = useState\(0\)/.test(gchtml)) {
  console.error("FAIL · Game Console.html 缺 mountStage 分阶段挂载 (task 21)"); process.exit(1);
}
if (!/mountStage >= 1/.test(gchtml) || !/mountStage >= 2/.test(gchtml)) {
  console.error("FAIL · 缺 stage>=1 / stage>=2 gate (task 21)"); process.exit(1);
}
console.log("PASS · task 21 mountStage 分阶段挂载在位");

// ---------------------------------------------------------------------------
// 11) task 16: 剧本导入预览发真后端 + 失败不假数据
// ---------------------------------------------------------------------------
const plSrc16 = fs.readFileSync(path.join(__dirname, "src/platform-app.jsx"), "utf8");
// startEstimate 必须用 base64 + 真的 body 形状（file: {name, base64}）打后端
if (!/readFileAsBase64/.test(plSrc16)) {
  console.error("FAIL · 缺 readFileAsBase64（task 16）"); process.exit(1);
}
if (!/file:\s*\{\s*name:\s*selectedFile\.name,\s*base64\s*\}/.test(plSrc16)) {
  console.error("FAIL · POST /api/scripts/preview body 不是 {file:{name,base64}} 形状"); process.exit(1);
}
// 失败路径必须 toast + 不再回退 fakeFile（previewError 标志）
if (!/previewError:\s*detail/.test(plSrc16)) {
  console.error("FAIL · 失败路径没有 previewError 字段（前端无法区分真实预算 vs 失败）"); process.exit(1);
}
// 不能再把 isMockEstimate / fakeFile 混进真实文件的成功路径
const startEstBody = (plSrc16.match(/const startEstimate = async \(\) => \{[\s\S]*?\n  \};/) || [])[0] || "";
if (!startEstBody) { console.error("FAIL · 提取 startEstimate body 失败"); process.exit(1); }
// 真实文件成功路径不应再次 setEstimate({file: fakeFile, ...})（按缩进/上下文判断）
if (/setEstimate\(\{\s*file:\s*fakeFile/.test(startEstBody.replace(/\/\/[^\n]*/g, "").split("if (!selectedFile)").slice(1).join("if (!selectedFile)").split("setPreviewBusy(false);\n      return;").slice(1).join(""))) {
  // 上面的切片是：先去掉「无选文件」分支（合法 fakeFile），然后剩下的部分不应再有 setEstimate(fakeFile)
  console.error("FAIL · 选了真实文件后仍可能回退 fakeFile（task 16 静默假数据问题）"); process.exit(1);
}
console.log("PASS · task 16 预览发真 base64 后端 + 失败显式 toast，不静默回退假数据");

// ---------------------------------------------------------------------------
// 12) task 17 / 18 / 19 / 20 anchors
// ---------------------------------------------------------------------------
const plSrc17 = fs.readFileSync(path.join(__dirname, "src/platform-app.jsx"), "utf8");
const apiSrc = fs.readFileSync(path.join(__dirname, "src/api-client.js"), "utf8");
const platformHtml17 = fs.readFileSync(path.join(__dirname, "Platform.html"), "utf8");

// task 17: 真正发 total_bytes/total_chunks + 失败不造 fake job
if (!/total_bytes:\s*totalBytes,\s*\n\s*total_chunks:\s*totalChunks/.test(plSrc17)) {
  console.error("FAIL · startImport 未发 total_bytes/total_chunks 给 /api/uploads/init (task 17)"); process.exit(1);
}
if (!/chunk_index:\s*Number\(index\)/.test(apiSrc)) {
  console.error("FAIL · api-client.uploads.chunk 未改成 JSON {chunk_index, base64} (task 17)"); process.exit(1);
}
// 失败路径必须 setJob(null) 而不是建假 job
const startImportBody = (plSrc17.match(/const startImport = async \(\) => \{[\s\S]*?\n  \};/) || [])[0] || "";
if (!startImportBody) { console.error("FAIL · 提取 startImport body 失败"); process.exit(1); }
if (!/setJob\(null\);/.test(startImportBody)) {
  console.error("FAIL · startImport catch 路径未 setJob(null)；仍可能造 fake job (task 17)"); process.exit(1);
}
if (!/upload_id:\s*uploadId/.test(startImportBody)) {
  console.error("FAIL · scripts.importScript 未传 upload_id (task 17)"); process.exit(1);
}
console.log("PASS · task 17 上传链 init/chunk/import 用真实契约 + 失败不造 fake job");

// 后端 api.py: api_import_script 透传 upload_id
const apiPy = fs.readFileSync(path.join(__dirname, "..", "rpg", "platform_app", "api.py"), "utf8");
if (!/upload_id=str\(body\.get\("upload_id"\) or ""\)/.test(apiPy)) {
  console.error("FAIL · 后端 api_import_script 没透传 body.upload_id (task 17)"); process.exit(1);
}
console.log("PASS · task 17 后端 import 路由透传 upload_id");

// task 18: HASH_ALIASES 不再把合法 hash (scripts) 重写到不存在的 (saves-scripts)
if (/HASH_ALIASES = \{[^}]*scripts[^}]*\}/.test(platformHtml17)) {
  console.error("FAIL · Platform.html HASH_ALIASES 仍把合法 #scripts 重映射 (task 18)"); process.exit(1);
}
if (!/HASH_ALIASES = \{\s*"branches":\s*"saves-branches"\s*\}/.test(platformHtml17)) {
  console.error("FAIL · HASH_ALIASES 应只剩 branches→saves-branches (task 18)"); process.exit(1);
}
console.log("PASS · task 18 HASH_ALIASES 收窄，#scripts 不再被错误重写");

// 移除 setState-in-render：tick 内不再 mutate MOCK_PLATFORM.scripts + 不再在 setJob updater 里发 toast
if (/platform\.scripts\s*=\s*\[\s*\{[\s\S]{0,200}刚导入/.test(plSrc17)) {
  console.error("FAIL · 仍在 tick 里 mutate MOCK_PLATFORM.scripts 造假行 (task 19)"); process.exit(1);
}
// task 19: ScriptsListView 改用真实数据初始化 [] 而非 MOCK_PLATFORM.scripts
const scriptsListBody = extractFunction(plSrc17, "ScriptsListView");
if (!scriptsListBody) { console.error("FAIL · 抽 ScriptsListView 失败"); process.exit(1); }
if (/useStatePL\(window\.MOCK_PLATFORM\.scripts\)/.test(scriptsListBody)) {
  console.error("FAIL · ScriptsListView 仍用 MOCK_PLATFORM.scripts 初始化 (task 19)"); process.exit(1);
}
if (!/window\.addEventListener\("rpg-scripts-updated"/.test(scriptsListBody)) {
  console.error("FAIL · ScriptsListView 未监听 rpg-scripts-updated 事件 (task 19)"); process.exit(1);
}
console.log("PASS · task 19 ScriptsListView 改真实数据 + 监听刷新事件");

// task 20: NewGameModal 改用真实 /api/scripts + /api/me/personas
const newGameBody = extractFunction(plSrc17, "NewGameModal");
if (!newGameBody) { console.error("FAIL · 抽 NewGameModal 失败"); process.exit(1); }
if (/setPickedCard\("uc1"\)/.test(newGameBody)) {
  console.error("FAIL · NewGameModal 仍用 mock 'uc1' 当默认 character (task 20)"); process.exit(1);
}
if (!/window\.api\.scripts\.list\(\)/.test(newGameBody)) {
  console.error("FAIL · NewGameModal 没拉真实 /api/scripts (task 20)"); process.exit(1);
}
if (!/\/api\/me\/personas/.test(newGameBody)) {
  console.error("FAIL · NewGameModal 没拉真实 personas (task 20)"); process.exit(1);
}
if (!/submitErr/.test(newGameBody)) {
  console.error("FAIL · NewGameModal 没有 inline 错误显示 (task 20)"); process.exit(1);
}
console.log("PASS · task 20 NewGameModal 真实数据 + inline 错误");

// ---------------------------------------------------------------------------
// 13) task 21: ChatArea 窗口化 + reloadState rAF 推迟
// ---------------------------------------------------------------------------
const gajsx21 = fs.readFileSync(path.join(__dirname, "src/game-app.jsx"), "utf8");
const chatBody = extractFunction(gajsx21, "ChatArea");
if (!chatBody) { console.error("FAIL · 抽 ChatArea 失败"); process.exit(1); }
if (!/HISTORY_WINDOW\s*=\s*80/.test(chatBody)) {
  console.error("FAIL · ChatArea 没有 HISTORY_WINDOW 窗口化（task 21）"); process.exit(1);
}
if (!/history\.slice\(visibleStart\)/.test(chatBody)) {
  console.error("FAIL · ChatArea 仍渲染整段 history（task 21）"); process.exit(1);
}
if (!/requestAnimationFrame/.test(chatBody)) {
  console.error("FAIL · ChatArea scroll 没用 rAF 延后（task 21）"); process.exit(1);
}
console.log("PASS · task 21 ChatArea 窗口化 + scroll 推迟到 rAF");

const gchtml21 = fs.readFileSync(path.join(__dirname, "Game Console.html"), "utf8");
if (!/requestAnimationFrame\(\(\) => \{\s*if \(cancelled\) return;\s*reloadState\(\);\s*reloadSaves\(\);/.test(gchtml21.replace(/\n/g, " "))) {
  console.error("FAIL · Game Console.html 没把 reloadState/reloadSaves 延后到 rAF（task 21）"); process.exit(1);
}
console.log("PASS · task 21 Game Console reloadState/reloadSaves 延后 rAF");

// ---------------------------------------------------------------------------
// 14) task 21 加强：消除 Chrome renderer OOM（Aw, Snap! Error 5）的字段白名单
// ---------------------------------------------------------------------------
const gchtmlOOM = fs.readFileSync(path.join(__dirname, "Game Console.html"), "utf8");
const dlSrcOOM = fs.readFileSync(path.join(__dirname, "src/data-loader.js"), "utf8");

// reloadState 必须改成白名单 PICK_STATE_KEYS，不再 setGame(g => ({...g, ...data}))
if (!/PICK_STATE_KEYS\s*=\s*\["player","world"/.test(gchtmlOOM)) {
  console.error("FAIL · Game Console 缺 PICK_STATE_KEYS 白名单（task 21 OOM）"); process.exit(1);
}
// 不能再有 spread data 的 setGame
if (/setGame\(g => \(\{ \.\.\.g, \.\.\.data \}\)\)/.test(gchtmlOOM)) {
  console.error("FAIL · Game Console 仍有 setGame({...g, ...data})；会把 tools/models/_raw 200KB+ 灌进 React state（task 21 OOM）"); process.exit(1);
}
if (/setGame\(g => \(\{ \.\.\.g, \.\.\.data\.state \}\)\)/.test(gchtmlOOM)) {
  console.error("FAIL · Game Console 仍有 setGame({...g, ...data.state})（同样问题）"); process.exit(1);
}
console.log("PASS · task 21 game state 白名单收窄，避免 Chrome OOM");

// data-loader 不再把整个 backend data 存进 MOCK_STATE._raw
if (/merged\._raw = data;/.test(dlSrcOOM)) {
  console.error("FAIL · data-loader 仍把整个 backend response 存进 _raw（task 21 OOM）"); process.exit(1);
}
if (!/merged\._raw = \{\s*save_id:/.test(dlSrcOOM)) {
  console.error("FAIL · data-loader 应只把 {save_id, save_title, turn} 存进 _raw（task 21 OOM）"); process.exit(1);
}
console.log("PASS · task 21 data-loader _raw 只留 save 上下文，不再存全包");

// ---------------------------------------------------------------------------
// 15) task 24: scripts/saves list 必须兼容后端 page_payload 的 {items,...} 形态
// ---------------------------------------------------------------------------
const plSrc24 = fs.readFileSync(path.join(__dirname, "src/platform-app.jsx"), "utf8");
const dlSrc24 = fs.readFileSync(path.join(__dirname, "src/data-loader.js"), "utf8");
const gchtml24 = fs.readFileSync(path.join(__dirname, "Game Console.html"), "utf8");
// 严格匹配「只看 .scripts/.saves 没看 .items」的旧形态：括号内只含 r?.scripts/r?.saves 单一项
for (const [src, name] of [[plSrc24, "platform-app.jsx"], [dlSrc24, "data-loader.js"], [gchtml24, "Game Console.html"]]) {
  // 旧形态：(r?.scripts || []) 或 (saves && saves.saves) 等无 items 兜底
  // 匹配 `: (r?.scripts || [])`（不允许前面有 items）
  if (/:\s*\(\s*[rs]\?\.scripts\s*\|\|\s*\[\]\s*\)/.test(src)) {
    console.error(`FAIL · ${name} 仍有 ": (r?.scripts || [])" 旧形态（漏 .items）`); process.exit(1);
  }
  if (/:\s*\(\s*[rs]\?\.saves\s*\|\|\s*\[\]\s*\)/.test(src)) {
    console.error(`FAIL · ${name} 仍有 ": (r?.saves || [])" 旧形态（漏 .items）`); process.exit(1);
  }
}
// 所有 list-parse 应同时检查 .items
if (!/items \|\| r\?\.scripts/.test(plSrc24)) {
  console.error("FAIL · platform-app.jsx 缺 .items || .scripts 复合解析 (task 24)"); process.exit(1);
}
if (!/items \|\| r\?\.saves/.test(plSrc24)) {
  console.error("FAIL · platform-app.jsx 缺 .items || .saves 复合解析 (task 24)"); process.exit(1);
}
if (!/items \|\| scripts\?\.scripts/.test(dlSrc24)) {
  console.error("FAIL · data-loader.js 缺 scripts.items 解析 (task 24)"); process.exit(1);
}
if (!/items \|\| saves\?\.saves/.test(dlSrc24)) {
  console.error("FAIL · data-loader.js 缺 saves.items 解析 (task 24)"); process.exit(1);
}
console.log("PASS · task 24 scripts/saves list 全部兼容 {items} 形态");

// ---------------------------------------------------------------------------
// 16) task 26: PanelTimeline 渲染富对象不能崩
// ---------------------------------------------------------------------------
const gpSrc26 = fs.readFileSync(path.join(__dirname, "src/game-panels.jsx"), "utf8");
// 必须有 _renderVarValue 兜底函数
if (!/function _renderVarValue\(/.test(gpSrc26)) {
  console.error("FAIL · game-panels.jsx 缺 _renderVarValue 兜底函数 (task 26)"); process.exit(1);
}
// gp-var-val 必须走 _renderVarValue 而不是直接 {v}
if (!/<span className="gp-var-val">\{_renderVarValue\(v\)\}<\/span>/.test(gpSrc26)) {
  console.error("FAIL · gp-var-val 仍直接渲染对象 v (task 26)"); process.exit(1);
}
// last_projection 也必须走 _renderVarValue（task 33 兜底改成 state && state.worldline && ... 写法）
if (!/_renderVarValue\(state[^)]*worldline[^)]*last_projection\)/.test(gpSrc26)) {
  console.error("FAIL · last_projection 仍直接放进 JSX (task 26)"); process.exit(1);
}
console.log("PASS · task 26 PanelTimeline 富对象渲染全部走 _renderVarValue 兜底");

// 真实 React renderToString：渲染富对象 schema，应不抛
const { execFileSync } = require("child_process");
try {
  const out = execFileSync("node", [path.join(__dirname, "_test_paneltimeline.js")],
    { encoding: "utf8", timeout: 20000 });
  if (!/ALL TESTS PASSED/.test(out)) {
    console.error("FAIL · _test_paneltimeline.js 没有 ALL TESTS PASSED：\n" + out.split("\n").slice(-10).join("\n"));
    process.exit(1);
  }
  console.log("PASS · task 26 _test_paneltimeline.js 全 13 例通过");
} catch (e) {
  console.error("FAIL · _test_paneltimeline.js 跑挂了：" + e.message);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// 17) task 27: /set 指令必须在调 LLM 之前持久化，否则上游 504 会丢硬改动
// ---------------------------------------------------------------------------
// 这条 anchor 只是静态校验 ui.py 里的 /api/chat handler 形状：
//   - apply_player_directives 调用后立刻 _persist_runtime_checkpoint
//   - 再 yield 一条 stage=pre_llm 的 updates SSE 事件
// 真正的 round-trip 由 rpg/tests/test_set_persists_on_gm_failure.py 覆盖。
const uiSrc27 = fs.readFileSync(backendPyPath, "utf8");
if (!/task 27[：:]/.test(uiSrc27)) {
  console.error("FAIL · ui.py 缺 task 27 注释锚（应在 /api/chat 里）"); process.exit(1);
}
// 必须能找到 directive_updates → _persist_runtime_checkpoint → pre_llm updates 的连贯结构
const pre = uiSrc27.indexOf("apply_player_directives(message_for_model)");
const persistIdx = uiSrc27.indexOf("_persist_runtime_checkpoint(state, api_user)", pre);
const preLlmIdx = uiSrc27.indexOf('"stage": "pre_llm"', pre);
if (pre < 0 || persistIdx < 0 || preLlmIdx < 0 || persistIdx < pre || preLlmIdx < persistIdx) {
  console.error("FAIL · ui.py /api/chat 没有按『apply_player_directives → _persist_runtime_checkpoint → updates(stage=pre_llm)』顺序排布 (task 27)");
  process.exit(1);
}
// 这段必须出现在 except 之前（说明它在 try 里、能保证落盘后再进 LLM）
// 用 lastIndexOf(..., pre) 找紧邻 apply_player_directives 之前的那个 try:
const tryIdx = uiSrc27.lastIndexOf("try:", pre);
const exceptIdx = uiSrc27.indexOf("except Exception as exc:", preLlmIdx);
if (tryIdx < 0 || exceptIdx < 0 || tryIdx > pre || preLlmIdx > exceptIdx) {
  console.error("FAIL · pre_llm 持久化必须包在 try 里且早于 except (task 27)");
  process.exit(1);
}
console.log("PASS · task 27 ui.py /api/chat 已在 LLM 之前持久化 /set 指令");

// ---------------------------------------------------------------------------
// 18) task 28: /set 顺序——时间/位置先做，显式 path=value 兜底覆盖
// ---------------------------------------------------------------------------
const stSrc28 = fs.readFileSync(path.join(__dirname, "../rpg/state.py"), "utf8");
if (!/task 28[：:]/.test(stSrc28)) {
  console.error("FAIL · state.py 缺 task 28 注释锚"); process.exit(1);
}
// 关键顺序锚：apply_set_directive 里 _extract_set_time_targets 必须先于 _extract_set_assignments
const timeIdx28 = stSrc28.indexOf("_extract_set_time_targets(directive)");
const assignIdx28 = stSrc28.indexOf("_extract_set_assignments(directive)");
if (timeIdx28 < 0 || assignIdx28 < 0 || timeIdx28 > assignIdx28) {
  console.error("FAIL · apply_set_directive 顺序错：time targets 必须在 assignments 之前 (task 28)");
  process.exit(1);
}
console.log("PASS · task 28 /set 顺序：time/location 先，显式 path=value 最后覆盖");

// ---------------------------------------------------------------------------
// 19) task 29: 新建存档 UI 角色卡必须真的写入 initial player state
// ---------------------------------------------------------------------------
const plSrc29 = fs.readFileSync(path.join(__dirname, "src/platform-app.jsx"), "utf8");
// frontend：「设定」textarea 必须绑 newCardBg
if (!/setNewCardBg\b/.test(plSrc29) || !/value=\{newCardBg\}/.test(plSrc29)) {
  console.error("FAIL · NewGameModal「设定」textarea 仍没绑 state (task 29)"); process.exit(1);
}
// new_card payload 必须带 background
if (!/background:\s*newCardBg/.test(plSrc29)) {
  console.error("FAIL · new_card payload 没带 background (task 29)"); process.exit(1);
}
// onCreate 必须透传 character_kind
if (!/character_kind:\s*vals\.character_kind/.test(plSrc29)) {
  console.error("FAIL · onCreate 没把 character_kind 透传给后端 (task 29)"); process.exit(1);
}
// backend：workspace.create_save 接 new_card
const wsSrc29 = fs.readFileSync(path.join(__dirname, "../rpg/platform_app/workspace.py"), "utf8");
if (!/task 29[：:]/.test(wsSrc29)) {
  console.error("FAIL · workspace.py 缺 task 29 注释锚"); process.exit(1);
}
if (!/def create_save\([^)]*new_card[^)]*\)/.test(wsSrc29)) {
  console.error("FAIL · workspace.create_save 签名缺 new_card 参数 (task 29)"); process.exit(1);
}
if (!/_build_initial_snapshot\(/.test(wsSrc29) || !/setup_player\(/.test(wsSrc29)) {
  console.error("FAIL · workspace 缺 _build_initial_snapshot/setup_player 调用 (task 29)"); process.exit(1);
}
// api.py：POST /api/saves 把 new_card / character 传到 create_save
const apiSrc29 = fs.readFileSync(path.join(__dirname, "../rpg/platform_app/api.py"), "utf8");
if (!/task 29[：:]/.test(apiSrc29)) {
  console.error("FAIL · api.py 缺 task 29 注释锚"); process.exit(1);
}
// 简单子串检查：api_create_save 函数体内有 new_card=new_card 这串
if (!/new_card=new_card/.test(apiSrc29)) {
  console.error("FAIL · POST /api/saves 没把 new_card 传到 create_save (task 29)"); process.exit(1);
}
console.log("PASS · task 29 NewGameModal background + backend create_save 接 new_card");

// ---------------------------------------------------------------------------
// 20) task 30: /api/saves/{id}/activate 必须真切 runtime + 前端 confirm 等 activate
// ---------------------------------------------------------------------------
const frSrc30 = fs.readFileSync(path.join(__dirname, "../rpg/platform_app/frontend_routes.py"), "utf8");
if (!/task 30[：:]/.test(frSrc30)) {
  console.error("FAIL · frontend_routes.py 缺 task 30 注释锚"); process.exit(1);
}
if (!/_branches\.activate_save\(/.test(frSrc30)) {
  console.error("FAIL · api_save_activate 仍未调 branches.activate_save (task 30)"); process.exit(1);
}
if (!/_invalidate_user_cache\(user\)/.test(frSrc30)) {
  console.error("FAIL · api_save_activate 仍未清 ui._invalidate_user_cache (task 30)"); process.exit(1);
}
const brSrc30 = fs.readFileSync(path.join(__dirname, "../rpg/platform_app/branches.py"), "utf8");
if (!/def activate_save\(user_id: int, save_id: int\)/.test(brSrc30)) {
  console.error("FAIL · branches.py 缺 activate_save 函数 (task 30)"); process.exit(1);
}
if (!/runtime\.activate_state_snapshot\(/.test(brSrc30)) {
  console.error("FAIL · activate_save 没调 runtime.activate_state_snapshot (task 30)"); process.exit(1);
}
// 前端 ContinueGameModal confirm 必须先 await activate 再 navigate
if (!/task 30[：:]/.test(plSrc29)) {
  console.error("FAIL · platform-app.jsx 缺 task 30 注释锚"); process.exit(1);
}
if (!/\/api\/saves\/\$\{[^}]*\}\/activate/.test(plSrc29) && !/window\.api\.saves\.activate\(targetId\)/.test(plSrc29)) {
  console.error("FAIL · confirm() 没在跳 Game Console 前 POST /api/saves/{id}/activate (task 30)"); process.exit(1);
}
console.log("PASS · task 30 backend activate 切 runtime + 前端 confirm 等 activate");

// ---------------------------------------------------------------------------
// 21) task 31: /api/chat 字段契约（前端发 message+text, 后端两字段都吃, UI 错误显示真因）
// ---------------------------------------------------------------------------
const uiSrc31 = fs.readFileSync(backendPyPath, "utf8");
if (!/task 31[：:]/.test(uiSrc31)) {
  console.error("FAIL · ui.py 缺 task 31 注释锚"); process.exit(1);
}
if (!/body\.get\("message"\)\s*or\s*body\.get\("text"\)/.test(uiSrc31)) {
  console.error("FAIL · ui.py /api/chat 没同时接 message 和 text (task 31)"); process.exit(1);
}
const gcHtml31 = fs.readFileSync(path.join(__dirname, "Game Console.html"), "utf8");
if (!/task 31[：:]/.test(gcHtml31)) {
  console.error("FAIL · Game Console.html 缺 task 31 注释锚"); process.exit(1);
}
if (!/message:\s*playerText[\s\S]{0,30}text:\s*playerText/.test(gcHtml31)) {
  console.error("FAIL · Game Console.html chat 调用没同时发 message 和 text (task 31)"); process.exit(1);
}
// on_error 必须读 data.message（不止 data.detail）
if (!/data\s*&&\s*\(\s*data\.message\s*\|\|\s*data\.detail/.test(gcHtml31)) {
  console.error("FAIL · Game Console.html on_error 仍未读 data.message (task 31)"); process.exit(1);
}
// game-app.jsx ChatArea 不能再硬编码"上游 504"作显示文本
const gaSrc31 = fs.readFileSync(path.join(__dirname, "src/game-app.jsx"), "utf8");
if (/请求中断：上游 504。已保留你的上一条输入/.test(gaSrc31)) {
  console.error("FAIL · game-app.jsx 仍硬编码『请求中断：上游 504』作为错误显示文本 (task 31)"); process.exit(1);
}
if (!/task 31[：:]/.test(gaSrc31)) {
  console.error("FAIL · game-app.jsx 缺 task 31 注释锚"); process.exit(1);
}
console.log("PASS · task 31 /api/chat 字段契约 message/text 双向兼容 + 错误显示真因");

// ---------------------------------------------------------------------------
// 22) task 32: 时间跳转混合标签 pending 必须优先于 explicit confirm
// ---------------------------------------------------------------------------
const stSrc32 = fs.readFileSync(path.join(__dirname, "../rpg/state.py"), "utf8");
if (!/task 32[：:]/.test(stSrc32)) {
  console.error("FAIL · state.py 缺 task 32 注释锚"); process.exit(1);
}
// helper 必须不再"看见时间跳跃确认就立刻 return False"
const helperStart = stSrc32.indexOf("def _gm_is_asking_for_time_confirm");
const helperEnd = stSrc32.indexOf("\n\n\n", helperStart);
const helperBody = stSrc32.substring(helperStart, helperEnd > 0 ? helperEnd : helperStart + 3000);
if (/if "时间跳跃确认" in tag:\s*\n\s*return False/.test(helperBody)) {
  console.error("FAIL · _gm_is_asking_for_time_confirm 仍在第一个『时间跳跃确认』tag 就立刻 return False (task 32)");
  process.exit(1);
}
if (!/has_pending_signal/.test(helperBody) || !/has_explicit_confirm/.test(helperBody)) {
  console.error("FAIL · _gm_is_asking_for_time_confirm 缺 has_pending_signal/has_explicit_confirm 双信号 (task 32)");
  process.exit(1);
}
// apply_structured_updates 的 "时间跳跃确认" 分支也必须检查 value pending
if (!/_value_pending/.test(stSrc32)) {
  console.error("FAIL · apply_structured_updates 时间跳跃确认分支缺 _value_pending 防御 (task 32)"); process.exit(1);
}
console.log("PASS · task 32 时间跳跃混合标签 pending 信号优先");

// ---------------------------------------------------------------------------
// 23) task 33: PanelContext/PanelTimeline/PanelWorldbook 空 state 兜底 + 全 7 tab 真实渲染
// ---------------------------------------------------------------------------
const gpSrc33 = fs.readFileSync(path.join(__dirname, "src/game-panels.jsx"), "utf8");
if (!/task 33[：:]/.test(gpSrc33)) {
  console.error("FAIL · game-panels.jsx 缺 task 33 注释锚"); process.exit(1);
}
// PanelContext 必须把 chapter_refs 兜底成数组
if (!/Array\.isArray\(lastCtx\.chapter_refs\)\s*\?\s*lastCtx\.chapter_refs\s*:\s*\[\]/.test(gpSrc33)) {
  console.error("FAIL · PanelContext 没把 chapter_refs 兜底成数组 (task 33)"); process.exit(1);
}
// PanelTimeline 必须把 timeline / anchors 兜底
if (!/const timeline\s*=\s*\(state && state\.world && state\.world\.timeline\) \|\| \{\}/.test(gpSrc33)) {
  console.error("FAIL · PanelTimeline 没把 timeline 兜底 (task 33)"); process.exit(1);
}
// 实际跑全 7 tab + 填充状态的 React renderToString
try {
  const out = execFileSync("node", [path.join(__dirname, "_test_panel_all_tabs.js")],
    { encoding: "utf8", timeout: 25000 });
  if (!/ALL TESTS PASSED \(8 panels\)/.test(out)) {
    console.error("FAIL · _test_panel_all_tabs.js 没有 ALL TESTS PASSED：\n" + out.split("\n").slice(-15).join("\n"));
    process.exit(1);
  }
  console.log("PASS · task 33 _test_panel_all_tabs.js 全 7 tab + filled PanelContext 全部 PASS");
} catch (e) {
  console.error("FAIL · _test_panel_all_tabs.js 跑挂了：" + e.message);
  process.exit(1);
}

// ---------------------------------------------------------------------------
// 24) task 34: 从导入剧本创建新 save 时 state_snapshot 不能用 DEFAULT_STATE 柏林剧情
// ---------------------------------------------------------------------------
const wsSrc34 = fs.readFileSync(path.join(__dirname, "../rpg/platform_app/workspace.py"), "utf8");
if (!/task 34[：:]/.test(wsSrc34)) {
  console.error("FAIL · workspace.py 缺 task 34 注释锚"); process.exit(1);
}
if (!/def _apply_script_opening\(/.test(wsSrc34)) {
  console.error("FAIL · workspace 缺 _apply_script_opening 函数 (task 34)"); process.exit(1);
}
if (!/def _scrub_berlin_default\(/.test(wsSrc34)) {
  console.error("FAIL · workspace 缺 _scrub_berlin_default 函数 (task 34)"); process.exit(1);
}
// _build_initial_snapshot 必须调用 _apply_script_opening
const buildStart = wsSrc34.indexOf("def _build_initial_snapshot");
const buildEnd = wsSrc34.indexOf("\ndef ", buildStart + 5);
const buildBody = wsSrc34.substring(buildStart, buildEnd > 0 ? buildEnd : buildStart + 3000);
if (!/_apply_script_opening\(state,\s*user_id,\s*script_id\)/.test(buildBody)) {
  console.error("FAIL · _build_initial_snapshot 没调 _apply_script_opening (task 34)"); process.exit(1);
}
// 三个 inline 元数据关键词（当前地点/当前目标/时间锚点）必须出现在 workspace 里
for (const kw of ["当前地点", "当前目标", "时间锚点"]) {
  if (!wsSrc34.includes(kw)) {
    console.error("FAIL · workspace 缺 inline meta 关键词『" + kw + "』(task 34)"); process.exit(1);
  }
}
console.log("PASS · task 34 _apply_script_opening + _scrub_berlin_default 在新存档初始化链路");

// ---------------------------------------------------------------------------
// 24b) task 40: 真实 import shape（chapter 1=空文档标题）必须扫多章选真首章
// ---------------------------------------------------------------------------
const wsSrc40 = wsSrc34; // 同文件
if (!/task 40[：:]/.test(wsSrc40)) {
  console.error("FAIL · workspace.py 缺 task 40 注释锚"); process.exit(1);
}
// 必须不再 limit 1，而是 limit 10 多扫
if (!/limit 10/.test(wsSrc40)) {
  console.error("FAIL · _apply_script_opening 仍 limit 1，没扫多章 (task 40)"); process.exit(1);
}
// 必须有 _is_doc_title_only 和 _has_opening_meta 辅助
if (!/def _is_doc_title_only\(/.test(wsSrc40) || !/def _has_opening_meta\(/.test(wsSrc40)) {
  console.error("FAIL · workspace 缺 _is_doc_title_only / _has_opening_meta 辅助 (task 40)"); process.exit(1);
}
// 正则必须不再用 ^...$ MULTILINE（真实 import 把换行折叠成空格）
if (/_OPENING_LOCATION_RE\s*=\s*re\.compile\(r"\^/.test(wsSrc40)) {
  console.error("FAIL · _OPENING_*_RE 仍要求行起止 ^...，无法匹配真实 import 单行连缀 (task 40)"); process.exit(1);
}
console.log("PASS · task 40 _apply_script_opening 多章扫描 + 折叠空格 inline meta 正则");

// ---------------------------------------------------------------------------
// 24c) task 41: suggestions 拆 fallback 通用 / 柏林专属，导入剧本不再推柏林势力图
// ---------------------------------------------------------------------------
const stSrc41 = fs.readFileSync(path.join(__dirname, "../rpg/state.py"), "utf8");
if (!/task 41[：:]/.test(stSrc41)) {
  console.error("FAIL · state.py 缺 task 41 注释锚"); process.exit(1);
}
// suggestions() 必须有 is_default_novel 判断 + fallback 拆分
if (!/is_default_novel/.test(stSrc41)) {
  console.error("FAIL · suggestions() 缺 is_default_novel 判断 (task 41)"); process.exit(1);
}
if (!/fallback_generic/.test(stSrc41) || !/fallback_default_novel/.test(stSrc41)) {
  console.error("FAIL · suggestions() fallback 没拆通用/柏林专属 (task 41)"); process.exit(1);
}
// 柏林势力图必须只在 fallback_default_novel 里
const fbDefaultIdx = stSrc41.indexOf("fallback_default_novel");
const berlinFallbackIdx = stSrc41.indexOf("要求一份柏林当前势力图");
if (berlinFallbackIdx < 0) {
  console.error("FAIL · '要求一份柏林当前势力图' 字符串不见了 (task 41)"); process.exit(1);
}
// 后端测试文件存在
const testFile = path.join(__dirname, "../rpg/tests/test_suggestions_no_berlin_leak.py");
if (!fs.existsSync(testFile)) {
  console.error("FAIL · 缺 test_suggestions_no_berlin_leak.py (task 41)"); process.exit(1);
}
console.log("PASS · task 41 suggestions fallback 拆分 + 柏林专属仅在默认 state 推出");

// ---------------------------------------------------------------------------
// 24d) task 42: retrieval 按 script_id 隔离，导入剧本不读 .webnovel SQLite / indexes JSON
// ---------------------------------------------------------------------------
const retSrc42 = fs.readFileSync(path.join(__dirname, "../rpg/retrieval.py"), "utf8");
if (!/task 42[：:]/.test(retSrc42)) {
  console.error("FAIL · retrieval.py 缺 task 42 注释锚"); process.exit(1);
}
// 必须有 _is_default_mumu_script + _strip_default_novel_leakage
if (!/def _is_default_mumu_script\(/.test(retSrc42)) {
  console.error("FAIL · retrieval.py 缺 _is_default_mumu_script (task 42)"); process.exit(1);
}
if (!/def _strip_default_novel_leakage\(/.test(retSrc42)) {
  console.error("FAIL · retrieval.py 缺 _strip_default_novel_leakage (task 42)"); process.exit(1);
}
// retrieve_context 签名必须加 script_id
if (!/def retrieve_context\([\s\S]{0,400}script_id/.test(retSrc42)) {
  console.error("FAIL · retrieve_context 签名缺 script_id (task 42)"); process.exit(1);
}
// 非默认 script 必须跳过 SQLite 来源
if (!/if is_default:\s*\n\s*facts_text = load_chapter_facts/.test(retSrc42)) {
  console.error("FAIL · retrieve_context 没把 load_chapter_facts 包在 is_default 条件下 (task 42)"); process.exit(1);
}
if (!/if is_default:\s*\n\s*char_names = detect_mentioned_characters/.test(retSrc42)) {
  console.error("FAIL · retrieve_context 没把角色卡 / BM25 / summaries 包在 is_default 条件下 (task 42)"); process.exit(1);
}
// context_agent 已改为 provider 调度器；script_id 必须进入 ProviderServices，
// 再由 NovelRetrievalProvider 透传给 retrieve_fn/retrieve_context。
const caSrc42 = fs.readFileSync(path.join(__dirname, "../rpg/context_agent.py"), "utf8");
const novelProviderSrc42 = fs.readFileSync(path.join(__dirname, "../rpg/context_providers/novel.py"), "utf8");
if (!/script_id=script_id/.test(caSrc42) || !/ProviderServices\([\s\S]{0,500}script_id=script_id/.test(caSrc42)) {
  console.error("FAIL · context_agent 没把 script_id 注入 ProviderServices (task 42/provider 架构)"); process.exit(1);
}
if (!/NovelRetrievalProvider/.test(novelProviderSrc42) || !/services\.retrieve_fn\([\s\S]{0,300}script_id=services\.script_id/.test(novelProviderSrc42)) {
  console.error("FAIL · NovelRetrievalProvider 没把 script_id 透传给 retrieve_fn (task 42/provider 架构)"); process.exit(1);
}
// 测试文件存在
if (!fs.existsSync(path.join(__dirname, "../rpg/tests/test_retrieval_no_default_leak.py"))) {
  console.error("FAIL · 缺 test_retrieval_no_default_leak.py (task 42)"); process.exit(1);
}
console.log("PASS · task 42 retrieval 按 script_id 隔离 + 默认柏林 token 后处理过滤");

// ---------------------------------------------------------------------------
// 24e) task 43: /api/opening 透传 script_id 给 retrieve_context，导入剧本 query 动态构
// ---------------------------------------------------------------------------
const uiSrc43 = fs.readFileSync(backendPyPath, "utf8");
if (!/task 43[：:]/.test(uiSrc43)) {
  console.error("FAIL · ui.py 缺 task 43 注释锚"); process.exit(1);
}
// /api/opening 必须不再硬编码柏林 query 作为唯一路径，且必须传 script_id
const openingStart = uiSrc43.indexOf('async def api_opening');
if (openingStart < 0) {
  console.error("FAIL · ui.py 找不到 api_opening 函数 (task 43)"); process.exit(1);
}
const openingBody = uiSrc43.substring(openingStart, openingStart + 2200);
if (!/script_id\s*=\s*_active_script_id\(api_user\)/.test(openingBody)) {
  console.error("FAIL · api_opening 没拿 script_id = _active_script_id(api_user) (task 43)"); process.exit(1);
}
if (!/retrieve_context\([\s\S]{0,400}script_id=script_id/.test(openingBody)) {
  console.error("FAIL · api_opening retrieve_context 调用没传 script_id (task 43)"); process.exit(1);
}
// query 必须按 script_id 动态构（非默认走 player/world/memory token）
if (!/if script_id:\s*\n[\s\S]{0,800}query_parts/.test(openingBody)) {
  console.error("FAIL · api_opening 没按 script_id 动态构 query (task 43)"); process.exit(1);
}
// 测试文件存在
if (!fs.existsSync(path.join(__dirname, "../rpg/tests/test_opening_no_default_leak.py"))) {
  console.error("FAIL · 缺 test_opening_no_default_leak.py (task 43)"); process.exit(1);
}
console.log("PASS · task 43 /api/opening 透传 script_id + 动态 query");

// ---------------------------------------------------------------------------
// 30) task 44: pending_jump 下 GM prompt 禁叙事 + SSE on_status/on_done 读对 payload
// ---------------------------------------------------------------------------
const ceSrc44 = fs.readFileSync(path.join(__dirname, "../rpg/context_engine.py"), "utf8");
if (!/task 44[：:]/.test(ceSrc44)) {
  console.error("FAIL · context_engine.py 缺 task 44 注释锚"); process.exit(1);
}
// pending 分支必须含禁止性指令
for (const kw of ["禁止把玩家请求的未来时间", "禁止输出标签", "pending_confirmation"]) {
  if (!ceSrc44.includes(kw)) {
    console.error("FAIL · context_engine 缺 pending 禁止指令『" + kw + "』(task 44)"); process.exit(1);
  }
}
if (!fs.existsSync(path.join(__dirname, "../rpg/tests/test_pending_jump_forbids_narrative.py"))) {
  console.error("FAIL · 缺 test_pending_jump_forbids_narrative.py (task 44)"); process.exit(1);
}
// Game Console.html SSE 回调修复：on_status 读 data 直接，on_done 读 data.status
const gcHtml44 = fs.readFileSync(path.join(__dirname, "Game Console.html"), "utf8");
if (!/task 44[：:]/.test(gcHtml44)) {
  console.error("FAIL · Game Console.html 缺 task 44 注释锚"); process.exit(1);
}
// 老 bug：if (data && data.state) setGame... 必须消失
if (/if \(data && data\.state\) setGame/.test(gcHtml44)) {
  console.error("FAIL · Game Console.html SSE 回调仍误读 data.state（应读 data 或 data.status）(task 44)");
  process.exit(1);
}
// on_done 必须从 data.status 提 payload
if (!/const payload = \(data && data\.status\) \|\| null/.test(gcHtml44)) {
  console.error("FAIL · on_done 没从 data.status 提 payload (task 44)"); process.exit(1);
}
console.log("PASS · task 44 GM pending 禁叙事 + SSE 回调读对 payload");

// ---------------------------------------------------------------------------
// 31) task 45: 登录态零 mock + 匿名态示例数据横幅
// ---------------------------------------------------------------------------
const gcHtml45 = fs.readFileSync(path.join(__dirname, "Game Console.html"), "utf8");
if (!/task 45[：:]/.test(gcHtml45)) {
  console.error("FAIL · Game Console.html 缺 task 45 注释锚"); process.exit(1);
}
// 老 useState(structuredClone(window.MOCK_STATE)) 必须消失
if (/useState\(\(\)\s*=>\s*structuredClone\(window\.MOCK_STATE\)\)/.test(gcHtml45)) {
  console.error("FAIL · Game Console.html 仍用 useState(structuredClone(window.MOCK_STATE)) 当初始值 (task 45)");
  process.exit(1);
}
// 必须有 IS_ANON 判断 + EMPTY_STATE 兜底
if (!/IS_ANON/.test(gcHtml45) || !/EMPTY_STATE\s*=/.test(gcHtml45)) {
  console.error("FAIL · Game Console.html 缺 IS_ANON / EMPTY_STATE (task 45)"); process.exit(1);
}
// data-loader 必须有 injectDemoBanner
const dlSrc45 = fs.readFileSync(path.join(__dirname, "src/data-loader.js"), "utf8");
if (!/task 45[：:]/.test(dlSrc45) || !/injectDemoBanner/.test(dlSrc45)) {
  console.error("FAIL · data-loader.js 缺 injectDemoBanner (task 45)"); process.exit(1);
}
// platform-app.jsx 必须有 usePlatformData + ContinuePicker 不再硬读 MOCK_PLATFORM.saves
const plSrc45 = fs.readFileSync(path.join(__dirname, "src/platform-app.jsx"), "utf8");
if (!/task 45[：:]/.test(plSrc45) || !/function usePlatformData/.test(plSrc45)) {
  console.error("FAIL · platform-app.jsx 缺 usePlatformData hook (task 45)"); process.exit(1);
}
// ContinuePicker 不应再有 `const allSaves = window.MOCK_PLATFORM.saves;`（顶层硬读）
if (/const allSaves = window\.MOCK_PLATFORM\.saves;/.test(plSrc45)) {
  console.error("FAIL · ContinuePicker 仍硬读 MOCK_PLATFORM.saves (task 45)"); process.exit(1);
}
// BRANCH_DATA 不应再被 ContinuePicker 当 nodes 源
if (/const nodes = BRANCH_DATA\.nodes;/.test(plSrc45)) {
  console.error("FAIL · ContinuePicker 仍用 BRANCH_DATA 假节点 (task 45)"); process.exit(1);
}
// publishUser 不应再 mutate MOCK_PLATFORM.user
if (/window\.MOCK_PLATFORM\.user\s*=\s*\{/.test(plSrc45)) {
  console.error("FAIL · publishUser 仍 mutate MOCK_PLATFORM.user (task 45)"); process.exit(1);
}
// MOCK_NOVEL.script_title 不应在 game-app.jsx 当唯一标题来源（仅 fallback 可保留）
const gaSrc45 = fs.readFileSync(path.join(__dirname, "src/game-app.jsx"), "utf8");
if (!/task 45[：:]/.test(gaSrc45)) {
  console.error("FAIL · game-app.jsx 缺 task 45 注释锚"); process.exit(1);
}
// 必须不再是 `<strong>{window.MOCK_NOVEL.script_title}</strong>` 直接读
if (/<strong>\{window\.MOCK_NOVEL\.script_title\}<\/strong>/.test(gaSrc45)) {
  console.error("FAIL · game-app.jsx 仍把 MOCK_NOVEL.script_title 当唯一品牌名 (task 45)"); process.exit(1);
}
console.log("PASS · task 45 登录态零 mock + 匿名横幅 + usePlatformData + ContinuePicker 真分支");

// ---------------------------------------------------------------------------
// 25) task 35: 玩家本轮 NL 触发的 pending 不允许 GM 同轮锁
// ---------------------------------------------------------------------------
const stSrc35 = fs.readFileSync(path.join(__dirname, "../rpg/state.py"), "utf8");
if (!/task 35[：:]/.test(stSrc35)) {
  console.error("FAIL · state.py 缺 task 35 注释锚"); process.exit(1);
}
// apply_structured_updates 必须用 pending_jump.turn == state.turn 触发强制 asking
if (!/_player_pending_this_turn/.test(stSrc35)) {
  console.error("FAIL · apply_structured_updates 缺 _player_pending_this_turn 防御 (task 35)"); process.exit(1);
}
console.log("PASS · task 35 同 turn pending 强制 asking_for_confirm");

// ---------------------------------------------------------------------------
// 26) task 36: 用户显式 /set path=value 加入 user_locked_fields，
//     update_time 检查跳过 _phase_for_time 覆盖
// ---------------------------------------------------------------------------
const stSrc36 = stSrc35; // 同一文件
if (!/task 36[：:]/.test(stSrc36)) {
  console.error("FAIL · state.py 缺 task 36 注释锚"); process.exit(1);
}
if (!/def mark_user_locked\(/.test(stSrc36) || !/def _is_user_locked\(/.test(stSrc36)) {
  console.error("FAIL · state.py 缺 mark_user_locked / _is_user_locked (task 36)"); process.exit(1);
}
// update_time 必须检查 user_locked 才决定是否覆盖 current_phase
if (!/_is_user_locked\("world\.timeline\.current_phase"\)/.test(stSrc36)) {
  console.error("FAIL · update_time 没检查 world.timeline.current_phase user lock (task 36)"); process.exit(1);
}
// apply_state_write 在 force/user 源时必须 mark_user_locked
if (!/if force or str\(source or ""\)\.startswith\("user"\):\s*\n\s*self\.mark_user_locked\(path\)/.test(stSrc36)) {
  console.error("FAIL · apply_state_write 没在 force/user 源时 mark_user_locked (task 36)"); process.exit(1);
}
console.log("PASS · task 36 user_locked_fields 注册表保护 /set 显式 path=value");

// ---------------------------------------------------------------------------
// 27) task 37: gc-rail-foot 不再拦截「返回主页」link 点击
// ---------------------------------------------------------------------------
const cssSrc37 = fs.readFileSync(path.join(__dirname, "src/game-console.css"), "utf8");
if (!/task 37[：:]/.test(cssSrc37)) {
  console.error("FAIL · game-console.css 缺 task 37 注释锚"); process.exit(1);
}
// .gc-rail-foot a 必须有 position: relative + z-index 让 paint 在父之上
if (!/\.gc-rail-foot a\s*\{[\s\S]{0,400}position:\s*relative/.test(cssSrc37)) {
  console.error("FAIL · .gc-rail-foot a 缺 position:relative (task 37)"); process.exit(1);
}
if (!/\.gc-rail-foot a\s*\{[\s\S]{0,400}z-index:\s*1/.test(cssSrc37)) {
  console.error("FAIL · .gc-rail-foot a 缺 z-index:1 (task 37)"); process.exit(1);
}
if (!/\.gc-rail-foot a\s*\{[\s\S]{0,400}display:\s*inline-flex/.test(cssSrc37)) {
  console.error("FAIL · .gc-rail-foot a 缺 display:inline-flex (task 37)"); process.exit(1);
}
// JSX 里 Icon 不再带 verticalAlign 推 SVG 出 inline 行盒
const gaSrc37 = fs.readFileSync(path.join(__dirname, "src/game-app.jsx"), "utf8");
const railFootMatch = gaSrc37.match(/<div className="gc-rail-foot">[\s\S]{0,1000}<\/div>/);
if (!railFootMatch) {
  console.error("FAIL · 找不到 gc-rail-foot JSX 块 (task 37)"); process.exit(1);
}
// 必须找到 a 标签，且其内不能再出现 verticalAlign+marginRight 在 Icon 上
const anchorMatch = railFootMatch[0].match(/<a [\s\S]*?<\/a>/);
if (!anchorMatch) {
  console.error("FAIL · gc-rail-foot 内找不到 <a> (task 37)"); process.exit(1);
}
if (/verticalAlign/.test(anchorMatch[0]) || /marginRight/.test(anchorMatch[0])) {
  console.error("FAIL · gc-rail-foot 的 Icon 仍带 verticalAlign/marginRight，会让 SVG 视觉外溢 (task 37)"); process.exit(1);
}
if (!/task 37[：:]/.test(gaSrc37)) {
  console.error("FAIL · game-app.jsx 缺 task 37 注释锚"); process.exit(1);
}
console.log("PASS · task 37 gc-rail-foot 链接 inline-flex + z-index + 不再 verticalAlign 推 SVG");

// ---------------------------------------------------------------------------
// 28) task 38: /api/branches/continue 接 save_id+message_index + 前端 MsgActions 透传
// ---------------------------------------------------------------------------
const apiSrc38 = fs.readFileSync(path.join(__dirname, "../rpg/platform_app/api.py"), "utf8");
if (!/task 38[：:]/.test(apiSrc38)) {
  console.error("FAIL · api.py 缺 task 38 注释锚"); process.exit(1);
}
// api.py 必须读 save_id + message_index 双形态
for (const kw of ['body.get\\("node_id"\\)', 'body.get\\("save_id"\\)', 'body.get\\("message_index"\\)']) {
  if (!new RegExp(kw).test(apiSrc38)) {
    console.error("FAIL · api.py /api/branches/continue 没读 " + kw + " (task 38)"); process.exit(1);
  }
}
if (!/缺字段[:：]/.test(apiSrc38)) {
  console.error("FAIL · api.py 缺字段时应返清晰 400 error message (task 38)"); process.exit(1);
}
const brSrc38 = fs.readFileSync(path.join(__dirname, "../rpg/platform_app/branches.py"), "utf8");
if (!/def resolve_commit_id_by_message\(/.test(brSrc38)) {
  console.error("FAIL · branches.py 缺 resolve_commit_id_by_message (task 38)"); process.exit(1);
}
// frontend: NarrativeBlock/PlayerBlock/MsgActions 必须接 msgIndex + saveId
const gaSrc38 = fs.readFileSync(path.join(__dirname, "src/game-app.jsx"), "utf8");
if (!/task 38[：:]/.test(gaSrc38)) {
  console.error("FAIL · game-app.jsx 缺 task 38 注释锚"); process.exit(1);
}
if (!/function MsgActions\([^)]*saveId[^)]*\)/.test(gaSrc38)) {
  console.error("FAIL · MsgActions 签名缺 saveId (task 38)"); process.exit(1);
}
if (!/function NarrativeBlock\([^)]*msgIndex[\s\S]{0,80}saveId/.test(gaSrc38)) {
  console.error("FAIL · NarrativeBlock 签名缺 msgIndex/saveId (task 38)"); process.exit(1);
}
// doFork 必须发 save_id+message_index 而不是只发 label
if (!/body\.save_id\s*=\s*saveId/.test(gaSrc38) || !/body\.message_index\s*=\s*msgIndex/.test(gaSrc38)) {
  console.error("FAIL · MsgActions.doFork 没发 save_id+message_index (task 38)"); process.exit(1);
}
// Game Console.html 必须把 saveId 传给 ChatArea
const gcHtml38 = fs.readFileSync(path.join(__dirname, "Game Console.html"), "utf8");
if (!/task 38[：:]/.test(gcHtml38)) {
  console.error("FAIL · Game Console.html 缺 task 38 注释锚"); process.exit(1);
}
if (!/saveId=\{[^}]*activeSave[^}]*\}/.test(gcHtml38)) {
  console.error("FAIL · Game Console.html ChatArea 没传 saveId (task 38)"); process.exit(1);
}
console.log("PASS · task 38 /api/branches/continue 接 save_id+message_index + 前端 MsgActions 透传 saveId");

// ---------------------------------------------------------------------------
// 29) task 39: 命令菜单含 /set + 选择后写入 trigger 到 text
// ---------------------------------------------------------------------------
const gcompSrc39 = fs.readFileSync(path.join(__dirname, "src/game-composer.jsx"), "utf8");
if (!/task 39[：:]/.test(gcompSrc39)) {
  console.error("FAIL · game-composer.jsx 缺 task 39 注释锚"); process.exit(1);
}
// SLASH_COMMANDS 必须含 id: "set"
if (!/id:\s*"set"\s*,\s*trigger:\s*"\/set "/.test(gcompSrc39)) {
  console.error("FAIL · SLASH_COMMANDS 缺 /set 条目 (task 39)"); process.exit(1);
}
// Game Console.html onSlashPick 必须把 trigger（带空格）写到 text
const gcHtml39 = gcHtml38; // 同文件
if (!/task 39[：:]/.test(gcHtml39)) {
  console.error("FAIL · Game Console.html 缺 task 39 注释锚"); process.exit(1);
}
if (!/cmd\.trigger\.endsWith\(" "\)/.test(gcHtml39)) {
  console.error("FAIL · onSlashPick 没识别『trigger 带空格 = 立即写入 text』(task 39)"); process.exit(1);
}
if (!/setText\(cmd\.trigger\)/.test(gcHtml39)) {
  console.error("FAIL · onSlashPick 没调 setText(cmd.trigger) (task 39)"); process.exit(1);
}
console.log("PASS · task 39 命令菜单含 /set + 选择写 trigger 到 text");

console.log("\nALL TESTS PASSED");
