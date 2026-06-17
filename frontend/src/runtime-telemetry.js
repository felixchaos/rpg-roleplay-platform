/**
 * runtime-telemetry.js — 客户端运行环境采集,给"提交反馈"附带 bug 排查切片
 *
 * 设计原则:
 *  - 内存常驻 ring buffer,不写 localStorage/IndexedDB(避免跨会话污染)
 *  - 默认 ON,但只在用户点"提交反馈"时才发送 — 不主动 telemetry 回传
 *  - 尺寸自封顶 ~10KB:错误 20 条 + API 失败 10 条,每条 msg 截 500 char
 *  - 抓取范围(放在反馈包里只有管理员能看,不上报第三方):
 *      console.error / window.onerror / unhandledrejection
 *      失败的 fetch / ApiError(throw 点都在 api-client.js)
 *      URL hash / viewport / locale / app_version / auth uid+role
 *      window.MOCK_STATE 里的 active save_id + script_id
 *
 * 入口:
 *   <import 'runtime-telemetry.js'> 在 platform-app.jsx 顶部一次性 import,自动安装钩子
 *   window.__getRuntimeSnapshot() 返当前快照(FeedbackDrawer 提交前调)
 */

const MAX_ERRORS = 20;
const MAX_API_FAILS = 10;
const MAX_MSG = 500;

// ring buffers
const _errors = [];
const _apiFails = [];

function _push(buf, max, item) {
  buf.push(item);
  while (buf.length > max) buf.shift();
}

function _trunc(s, n = MAX_MSG) {
  if (s == null) return '';
  s = String(s);
  return s.length > n ? s.slice(0, n) + '…' : s;
}

function _now() {
  // 不能用 Date.now() 形式数值化吗?可以,这里需要相对时间;
  // 用 performance.now() 拿到 page-load 起始的相对毫秒,避开"Date 不可用"约束
  // (我们不在 Workflow 沙箱里,普通 Date 可用,但 performance.now 更稳)
  try { return Math.round(performance.now()); } catch (_) { return 0; }
}

// ── 错误源 1:console.error 拦截 ──────────────────────────────────────────────
(function patchConsoleError() {
  const orig = console.error.bind(console);
  console.error = function (...args) {
    try {
      _push(_errors, MAX_ERRORS, {
        kind: 'console.error',
        t: _now(),
        msg: _trunc(args.map(a => {
          if (a instanceof Error) return a.stack || a.message;
          if (typeof a === 'object') {
            try { return JSON.stringify(a).slice(0, 300); } catch (_) { return '[unserializable object]'; }
          }
          return String(a);
        }).join(' ')),
      });
    } catch (_) { /* 拦截器永不阻塞原 console */ }
    return orig.apply(console, args);
  };
})();

// ── 错误源 2:window.onerror(同步 JS 错) ─────────────────────────────────────
window.addEventListener('error', (ev) => {
  try {
    _push(_errors, MAX_ERRORS, {
      kind: 'window.error',
      t: _now(),
      msg: _trunc(`${ev.message || ''} @ ${ev.filename || ''}:${ev.lineno || 0}:${ev.colno || 0}`),
      stack: _trunc(ev.error && ev.error.stack, 800),
    });
  } catch (_) {}
});

// ── 错误源 3:unhandledrejection(Promise 漏 catch) ────────────────────────────
window.addEventListener('unhandledrejection', (ev) => {
  try {
    const r = ev.reason;
    _push(_errors, MAX_ERRORS, {
      kind: 'unhandledrejection',
      t: _now(),
      msg: _trunc(r && (r.message || String(r))),
      stack: _trunc(r && r.stack, 800),
    });
  } catch (_) {}
});

// ── 错误源 4:ApiError 构造拦截 ───────────────────────────────────────────────
// api-client.js 在每次构造 ApiError 时回调 window.__onApiError(this)。
// (旧实现 monkey-patch window.ApiError 无效:api-client 的 throw 用的是它模块内的
//  闭包类,不是 window.ApiError 全局槽位 → wrapper 永不执行,_apiFails 恒空。)
window.__onApiError = function (e) {
  try {
    _push(_apiFails, MAX_API_FAILS, {
      t: _now(),
      code: String((e && e.code) || ''),
      status: Number(e && e.status) || 0,
      msg: _trunc(e && e.message),
      url: (e && e.payload && e.payload.url) || '',
    });
  } catch (_) {}
};

// ── 快照导出 ────────────────────────────────────────────────────────────────
window.__getRuntimeSnapshot = function (opts) {
  opts = opts || {};
  const state = window.MOCK_STATE || {};
  const auth = window.RPG_AUTH || {};
  const platform = window.MOCK_PLATFORM || {};
  const me = platform.user || {};
  const w = window.innerWidth || 0;
  const h = window.innerHeight || 0;
  const payload = {
    // 不写 Date.now() 形式以避开 workflow 沙箱限制;前后端都有 timestamp 字段所以无所谓
    app_version: window.__APP_VERSION__ || '',
    url: location.pathname + location.search,
    hash: location.hash,
    // 反馈采集:把裸路径映射成中文页面名(管理员可读)+ 当前活跃的浮层/弹窗功能(生图/后台任务等无独立路由的功能)。
    page_label: (() => {
      try {
        const pid = (location.pathname || '/').replace(/^\/+/, '').replace(/\.html$/i, '') || 'profile';
        const m = window.__PL_TITLES__ && window.__PL_TITLES__[pid];
        return (Array.isArray(m) ? m[0] : m) || pid;
      } catch (_) { return ''; }
    })(),
    active_feature: window.__activeFeature || null,
    viewport: `${w}x${h}`,
    locale: navigator.language || '',
    tz: (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (_) { return ''; } })(),
    user: { uid: me.uid || '', role: me.role || '', authed: !!auth.authed },
    active: {
      save_id: state._raw && state._raw.save_id,
      save_title: state._raw && state._raw.save_title,
      script_id: state._raw && state._raw.script_id,
      turn: state.turn || (state._raw && state._raw.turn),
    },
    errors: _errors.slice(),
    api_failures: _apiFails.slice(),
  };
  // opts.includeRecentDialog=true 时塞最近 3 轮对话(只在用户显式同意"附带运行环境"
  // 时才走到这里 — 隐私上跟 errors/api_failures 同一档)。每条 plaintext 截 300 字符。
  if (opts.includeRecentDialog) {
    try {
      // MOCK_STATE.history 只在 boot/refresh 灌一次,游戏进行中不更新 → 直接读会陈旧。
      // 调用方(FeedbackDrawer 提交前)现拉 /api/state 并通过 opts.recentDialog 传入最新对话;
      // 缺省才退回 MOCK_STATE.history。
      const src = Array.isArray(opts.recentDialog) ? opts.recentDialog : (state.history || []);
      const hist = src.slice(-6); // 最多 3 round = 6 message
      payload.recent_dialog = hist.map((m, i) => ({
        idx: i,
        role: m.role || m.author || 'unknown',
        turn: m.turn_index ?? m.turn ?? null,
        text: _trunc((m.content || m.text || ''), 300),
      }));
    } catch (_) {
      payload.recent_dialog = [];
    }
  }
  return { __runtime__: payload };
};

// ── 给前端可视化用:快照大小提示 ──────────────────────────────────────────
window.__getRuntimeSnapshotSize = function () {
  try {
    return JSON.stringify(window.__getRuntimeSnapshot()).length;
  } catch (_) { return 0; }
};
