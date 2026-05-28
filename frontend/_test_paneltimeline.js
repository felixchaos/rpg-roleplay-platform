#!/usr/bin/env node
/* task 107G: 更新 PanelTimeline 测试。
 * 旧版测试针对 worldline.user_variables 显示;
 * 新版 PanelTimeline 改为双时间线 panel (剧本期望线 + 实际足迹线),
 * 数据来自 /api/saves/:id/timeline fetch,state._raw.save_id 决定是否触发 fetch。
 *
 * SSR 环境无法测 fetch 分支 (useEffect 不执行),
 * 只测: (1) 无 saveId 时渲染空态 (2) _renderVarValue 单元测试 (仍被其他面板使用)。
 */
"use strict";

const fs = require("fs");
const path = require("path");
const babel = require("@babel/standalone");

global.window = global;
global.document = { createElement: () => ({}), getElementById: () => null,
  dispatchEvent: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const React = require("react");
const ReactDOMServer = require("react-dom/server");
global.React = React;

// Tiny stubs game-panels.jsx expects on global / window:
global.useState = React.useState; global.useEffect = React.useEffect;
global.useRef = React.useRef; global.useMemo = React.useMemo;
global.useCallback = React.useCallback;
global.Icon = () => null;
global.MOCK_NOVEL = { script_title: "" };
global.api = { worldline: { set: async () => {}, remove: async () => {} } };

// Babel-transform game-panels.jsx + eval (it's pure component functions)
const src = fs.readFileSync(path.join(__dirname, "src/game-panels.jsx"), "utf8");
const { code } = babel.transform(src, { presets: ["react"] });
// Append explicit globals so we can grab the funcs out
const codeWithExports = code + "\nglobal.PanelTimeline = PanelTimeline; global._renderVarValue = _renderVarValue;";
eval(codeWithExports);

function fail(msg) { console.error("FAIL:", msg); process.exit(1); }

// ── Test 1: 无 saveId 时渲染"暂无存档上下文"空态 ──────────────────────
const stateNoSave = { player: {}, world: {}, memory: {}, worldline: {}, relationships: {}, history: [] };
let html;
try {
  html = ReactDOMServer.renderToString(React.createElement(global.PanelTimeline, { state: stateNoSave }));
} catch (e) {
  fail("PanelTimeline render (no saveId) threw: " + e.message);
}
if (html.includes("[object Object]")) fail("HTML 含 [object Object] —— 对象未被格式化");
if (!html.includes("暂无存档上下文")) fail("无 saveId 时应显示'暂无存档上下文'提示");
console.log("PASS · PanelTimeline 无存档上下文空态渲染正确");

// ── Test 2: 有 saveId 但 data 尚未加载 (SSR,useEffect 不执行) → loading 态 ──
// 注意:useEffect 在 SSR (renderToString) 中不执行,fetch 不会发出。
// 初始 state: data=null, loading=false → 渲染"正在加载"不会出现,而是"暂无存档上下文"仍出现。
// 实际上,由于 useEffect 不执行,saveId 有值但 data=null 时组件会渲染"暂无存档上下文"空态。
// 这是 SSR 测试的局限性,浏览器中 fetch 会执行。
const stateWithSave = { _raw: { save_id: 42, save_title: "测试存档" }, player: {}, world: {}, memory: {} };
let html2;
try {
  html2 = ReactDOMServer.renderToString(React.createElement(global.PanelTimeline, { state: stateWithSave }));
} catch (e) {
  fail("PanelTimeline render (with saveId, SSR) threw: " + e.message);
}
if (html2.includes("[object Object]")) fail("SSR 有 saveId 时 HTML 含 [object Object]");
// SSR 下 useEffect 不执行 → data=null → 应该渲染"暂无存档上下文" 或 loading 态
// 不抛异常即通过
console.log("PASS · PanelTimeline 有存档 ID 的 SSR 渲染不抛异常");

// ── Test 3: _renderVarValue 单元测试 (仍被其他面板使用) ──────────────────
const rv = global._renderVarValue;
if (typeof rv !== "function") fail("_renderVarValue 未导出到 global");
const cases = [
  [null, "—"],
  [undefined, "—"],
  ["str", "str"],
  [123, "123"],
  [true, "true"],
  [{ value: "ok" }, "ok"],
  [{ text: "txt" }, "txt"],
  [{ label: "lab" }, "lab"],
  [{ name: "nm" }, "nm"],
  [{ value: null, text: "fallback" }, "fallback"],  // value null 时跳到 text
  [[1, "x", { value: "y" }], "1，x，y"],
];
for (const [input, expected] of cases) {
  const got = rv(input);
  if (got !== expected) fail(`_renderVarValue(${JSON.stringify(input)}) → ${JSON.stringify(got)} ≠ ${JSON.stringify(expected)}`);
}
console.log("PASS · _renderVarValue 单元 11 例全过");
console.log("\nALL TESTS PASSED");
