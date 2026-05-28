#!/usr/bin/env node
/* task 33: 真实 React 渲染所有右侧 panel tab（status/memory/worldbook/cards/timeline/context/debug），
 * 用最小空 state（memory={}, world={}, worldline={} 等）覆盖真实 /api/state 在没跑过任何
 * chat 时的形态。任何 panel 抛错（特别是 "Cannot read properties of undefined" 或
 * "Objects are not valid as a React child"）都算 FAIL。 */
"use strict";

const fs = require("fs");
const path = require("path");
const babel = require("@babel/standalone");

global.window = global;
global.document = {
  createElement: () => ({}), getElementById: () => null,
  dispatchEvent: () => {}, addEventListener: () => {}, removeEventListener: () => {},
};
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
const React = require("react");
const ReactDOMServer = require("react-dom/server");
global.React = React;

global.useState = React.useState; global.useEffect = React.useEffect;
global.useRef = React.useRef; global.useMemo = React.useMemo;
global.useCallback = React.useCallback;
global.Icon = () => null;
global.MOCK_NOVEL = { script_title: "" };
global.api = { worldline: { set: async () => {}, remove: async () => {} } };

const src = fs.readFileSync(path.join(__dirname, "src/game-panels.jsx"), "utf8");
const { code } = babel.transform(src, { presets: ["react"] });
const codeWithExports = code + `
global.PanelStatus = PanelStatus;
global.PanelMemory = PanelMemory;
global.PanelWorldbook = PanelWorldbook;
global.PanelCharacters = PanelCharacters;
global.PanelTimeline = PanelTimeline;
global.PanelContext = PanelContext;
global.PanelDebug = PanelDebug;
`;
eval(codeWithExports);

function fail(msg) { console.error("FAIL:", msg); process.exit(1); }

// 真实 /api/state 在用户从未发过 chat、刚激活新 save 时常见形态
const emptyState = {
  player: { name: "", role: "", background: "", current_location: "" },
  world: { time: "", weather: "", known_events: [] },
  relationships: {},
  memory: {},  // 关键：没有 last_context，原 PanelContext.last_context.chapter_refs.map 会崩
  worldline: {},
  permissions: { mode: "full_access", pending_writes: [], pending_questions: [] },
  history: [],
  turn: 0,
  suggestions: [],
};

const panels = [
  ["PanelStatus", global.PanelStatus, { state: emptyState }],
  ["PanelMemory", global.PanelMemory, { state: emptyState, density: "normal" }],
  ["PanelWorldbook", global.PanelWorldbook, { state: emptyState }],
  ["PanelCharacters", global.PanelCharacters, { state: emptyState }],
  ["PanelTimeline", global.PanelTimeline, { state: emptyState }],
  ["PanelContext", global.PanelContext, { state: emptyState }],
  ["PanelDebug", global.PanelDebug, { state: emptyState }],
];

let pass = 0;
for (const [name, Comp, props] of panels) {
  if (typeof Comp !== "function") fail(`${name} 未导出到 global`);
  let html;
  try {
    html = ReactDOMServer.renderToString(React.createElement(Comp, props));
  } catch (e) {
    fail(`${name} 渲染抛异常（空 state）: ${e.message}`);
  }
  if (html.includes("[object Object]")) {
    fail(`${name} HTML 含 [object Object] —— 对象未被格式化`);
  }
  pass += 1;
  console.log(`PASS · ${name} 空 state 渲染不抛`);
}

// 二次：填一个含 last_context = { tokens_used, retrieval_chunks, chapter_refs: [...] } 的 state，
// 验证 PanelContext 正常显示数据
const filledState = {
  ...emptyState,
  memory: {
    last_context: {
      tokens_used: 1234,
      retrieval_chunks: 5,
      chapter_refs: ["第三章·雾港", "第七章·灯塔", { title: "对象引用-应被格式化" }],
    },
  },
};
let h2;
try {
  h2 = ReactDOMServer.renderToString(React.createElement(global.PanelContext, { state: filledState }));
} catch (e) {
  fail("PanelContext 填充 state 渲染抛: " + e.message);
}
// React 在相邻 text node 之间插 <!-- -->；用 strip 后再比较
const h2Plain = h2.replace(/<!--.*?-->/g, "");
if (!h2Plain.includes("1234 tokens")) fail("PanelContext 应渲染 tokens_used=1234 tokens");
if (!h2Plain.includes("5 chunks")) fail("PanelContext 应渲染 retrieval_chunks=5 chunks");
if (!h2.includes("第三章·雾港")) fail("PanelContext 应渲染 chapter_refs 字符串");
if (!h2.includes("对象引用-应被格式化")) fail("PanelContext 应处理对象类 chapter_ref 通过 .title");
if (h2.includes("[object Object]")) fail("PanelContext chapter_refs 含对象不应渲染成 [object Object]");
console.log("PASS · PanelContext 填充 state 渲染含 1234 tokens / 5 chunks / chapter_refs");

console.log(`\nALL TESTS PASSED (${pass + 1} panels)`);
