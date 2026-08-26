// router.js —— Platform 单页应用的轻量 History 路由(取代 hash 路由)。
//
// 设计:
//   · 干净 URL:page id `settings` ↔ 路径 `/settings`;首页 `profile` ↔ `/`。
//   · plNavigate(id) = pushState + 派发 `pl-navigate` 事件;PlatformApp 同时监听
//     popstate(浏览器前进/后退)与 pl-navigate(任意组件编程跳转)→ 统一更新 page。
//   · 兼容旧链接:命中 `Platform.html#x` / 残留 hash 时,从 hash 抢救 page id,
//     首屏 replaceState 规范化成干净路径(老书签/外链不破)。
//   · query(?script=…)按需透传:plNavigate 默认丢弃旧 query,需要时显式传 search。
//
// 后端必须为这些路径做 history-fallback(返回 Platform.html),否则深链/刷新 404。

export const PL_HASH_ALIASES = { branches: 'saves-branches', 'settings-deploy': 'admin-deploy' };

// page id → 路径。
// 注意:主页用 /profile 而非裸 /。生产 Cloudflare 有「裸 / → /Login.html」上游规则,
// 若 SPA 落到 / 会被 CF 弹回登录页(登录后跳 / 会死循环)。用 /profile 绕开。
export function plPageToPath(id) {
  return '/' + (id || 'profile');
}

// 当前 URL → page id(无效返回 null;空/根/入口文件名 → 'profile')
export function plPathToPage(validIds) {
  let raw = '';
  try { raw = decodeURIComponent((location.pathname || '/').replace(/^\/+/, '').replace(/\/+$/, '')); }
  catch (_) { raw = (location.pathname || '/').replace(/^\/+/, '').replace(/\/+$/, ''); }
  // 旧 Platform.html#x 直达 / 残留 hash → 从 hash 抢救
  if ((!raw || raw === 'Platform.html' || raw === 'index.html') && location.hash) {
    raw = location.hash.replace(/^#/, '').split('?')[0];
  }
  raw = PL_HASH_ALIASES[raw] || raw;
  if (!raw || raw === 'Platform.html' || raw === 'index.html') return 'profile';
  if (validIds && !validIds.includes(raw)) return null;
  return raw;
}

// 编程跳转:写 URL + 通知 PlatformApp。search 形如 '?script=12'(可选)。
export function plNavigate(id, opts = {}) {
  const { replace = false, search = '' } = opts;
  const url = plPageToPath(id) + (search || '');
  try { history[replace ? 'replaceState' : 'pushState'](null, '', url); } catch (_) {}
  try { window.dispatchEvent(new CustomEvent('pl-navigate', { detail: id })); } catch (_) {}
}

// 本文档是不是 Platform SPA 本身。由 entries/platform.jsx 挂载时置位 —— 显式标记优于
// 按路径/DOM 猜:干净路由上线后 location.pathname 是 /settings 这类页面路径,
// 老的 /Platform\.html/ 判据已经恒假。
export function plIsPlatformDoc() {
  return typeof window !== 'undefined' && window.__PL_ROUTER__ === true;
}

// 「跳到平台某页」的唯一入口 —— 任意文档(Platform SPA / 游戏台 / 酒馆)都可调。
//   · Platform SPA 内 → plNavigate,无刷新换页
//   · 独立文档(Game Console.html)→ 新标签打开该路径,不打断正在进行的回合
//     (沿用 GCWelcomeModal / console-assistant-navigation 既有约定)
//
// ⚠ 历史坑(用户反馈「已有 apikey,这个界面点不了配置」):这些跳转原本写的是
//   `window.location.hash = 'settings-models'`,而 PlatformApp 只监听 popstate /
//   pl-navigate —— hash 路由在本文件顶部那次迁移里就被 History 路由取代了,
//   plPathToPage 也只在首屏落在 Platform.html 时才去 hash 里抢救 page id。
//   于是「去配 key」按钮点下去只是把 URL 尾巴改成 #settings-models,画面纹丝不动,
//   而没配 key 的用户恰恰只能从这个按钮走到配置页 → 整条上手路径死锁。
//   新增任何跳转一律走这里,别再写 location.hash。守卫见
//   frontend/tests/unit/router-goto-parity.test.js。
export function plGoto(id, opts = {}) {
  if (plIsPlatformDoc()) { plNavigate(id, opts); return; }
  const url = plPageToPath(id);
  try {
    if (window.open(url, '_blank')) return;
  } catch (_) { /* 弹窗被拦 → 落到整页导航 */ }
  try { window.location.assign(url); } catch (_) {}
}
