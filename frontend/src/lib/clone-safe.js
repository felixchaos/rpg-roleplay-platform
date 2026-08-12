// clone-safe.js — structuredClone 降级封装(与 crypto-safe.js 同族:裸用新 API → 整页崩)。
//
// `structuredClone` 是 Chrome 98+ / Safari 15.4+ / Firefox 94+ 才有的全局函数。
// 老浏览器和一批 App 内置 WebView(QQ/微信等)没有,裸调直接抛 TypeError ——
// 而 game-console 入口是在**模块顶层初始化 INITIAL_STATE** 时调它的,抛了就是整个游戏台白屏。
// 同批用户已实测缺 `AbortSignal.timeout`(Chrome 103+,见 api-client.js 的回退),
// 缺 structuredClone(更早的 98)完全在射程内。
//
// 这里的克隆对象是纯 JSON 游戏状态(EMPTY_STATE / MOCK_STATE),没有 Map/Set/Date/循环引用,
// 所以 JSON 往返是等价回退。真需要结构化克隆语义的场景别用这个函数。

export function safeStructuredClone(value) {
  try {
    if (typeof structuredClone === 'function') return structuredClone(value);
  } catch (_) { /* fallthrough:某些 WebView 有这个名字但对特定输入抛 DataCloneError */ }
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (_) {
    return value;   // 连 JSON 都过不去(含循环引用等):退回原引用,至少不炸页面
  }
}
