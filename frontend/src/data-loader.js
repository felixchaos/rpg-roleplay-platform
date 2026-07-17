/* ============================================================
 *  RPG Roleplay · Data Loader
 *  -----------------------------------------------------------
 *  Bridges the real backend (window.api) with the Claude Design
 *  mock globals (window.MOCK_*). On first load it tries to fill
 *  the globals from /api/*. Static designer fallback values are
 *  only allowed in explicit offline preview; authenticated pages
 *  must render loading/empty states instead of mock records.
 *
 *  Pages can wait for `window.RPG_DATA_READY` (a Promise) or the
 *  `rpg-data-ready` DOM event before rendering — the included
 *  HTML uses it to delay initial paint when the API is reachable.
 * ============================================================ */
import i18n from './i18n/index.js';
// Capture the designer baseline so we can fall back / extend it.
// ⚠️ P0 根修(2026-07-18,平台页全员挂死):mock 全局(window.MOCK_*)由 mock-data.js 的
// 模块副作用设置。此前这里在模块体顶层**立即**快照 = 隐式契约「mock-data 必须先于本模块求值」。
// v1.70.x 新增共享 lib 改变了 chunk 依赖图,rolldown 对 Platform 入口的模块求值顺序随之
// 反转:本模块先跑 → MOCK_PLATFORM 未赋值 → deepCopy(undefined) 走 catch 返回 undefined →
// baseline().platform=undefined → hydratePlatform 的 try 内 set 炸被吞、catch 内 read 再炸
// 逃逸 → 引导 Promise 拒绝、页面永久「加载中」。
// 根修 = 惰性快照(首次访问必然晚于整张模块图求值完成)+ platform 空对象兜底;
// 配套:文件尾 init() 的调用也推迟一个 microtask,双保险与求值顺序彻底解耦。
let _BASELINE = null;
function baseline() {
  if (!_BASELINE) {
    _BASELINE = {
      novel: deepCopy(window.MOCK_NOVEL),
      state: deepCopy(window.MOCK_STATE),
      runSteps: deepCopy(window.MOCK_RUN_STEPS),
      platform: deepCopy(window.MOCK_PLATFORM) || {},
    };
    window.__MOCK_BASELINE = _BASELINE;
  }
  return _BASELINE;
}

function deepCopy(o) { try { return JSON.parse(JSON.stringify(o)); } catch (_) { return o; } }

function useDesignerFallback() {
  try { return new URLSearchParams(location.search).has("offline"); } catch (_) { return false; }
}

function emptyPlatformFallback(platform) {
  const p = deepCopy(platform || {});
  p.saves = [];
  p.scripts = [];
  p.recent_assets = [];
  p.stats = { scripts: 0, saves: 0, branches: null, assets: null, api_calls: null };
  return p;
}

function emptyGameStateFallback() {
  return {
    player: { name: "", role: "", background: "", current_location: "", inventory: [] },
    world: {
      time: "",
      weather: "",
      known_events: [],
      timeline: { anchor_state: null, current_label: "", current_phase: "", pending_jump: null, anchors: [] },
    },
    relationships: {},
    permissions: { mode: "full_access", pending_writes: [], pending_questions: [] },
    worldline: { user_variables: {}, constraints: [], last_projection: "" },
    memory: {
      mode: "normal",
      main_quest: "",
      current_objective: "",
      pinned: [],
      facts: [],
      notes: [],
      last_retrieval: "",
      last_context: {},
    },
    suggestions: [],
    history: [],
    turn: 0,
    ruleset: {},
    player_character: {},
    scene: {},
    encounter: {},
    dice_log: [],
    content_pack: {},
    active_entities: {},
    app: {},
    models: {},
  };
}

function fmtBytes(n) {
  if (!n && n !== 0) return "—";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " MB";
  return (n / 1024 / 1024 / 1024).toFixed(2) + " GB";
}
function fmtAgo(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return i18n.t("relative_time.just_now");
  if (diff < 3600) return i18n.t("relative_time.minutes_ago", { n: Math.floor(diff / 60) });
  if (diff < 86400) return i18n.t("relative_time.hours_ago", { n: Math.floor(diff / 3600) });
  if (diff < 86400 * 7) return i18n.t("relative_time.days_ago", { n: Math.floor(diff / 86400) });
  return d.toLocaleDateString();
}
// zh-CN 24 小时制完整时间戳(各页 fmtTime 共用,坏值原样返回)
function fmtTime(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleString("zh-CN", { hour12: false }); } catch (_) { return iso; }
}
// fmtTimeFallback — window.__fmt 未就绪时的兜底体,权威单一实现(收口自 components/admin/shared.jsx、
// mobile/admin/shared.jsx、pages/feedback.jsx、mobile/caps/FeedbackSection.jsx 4 处逐字复制的同款 try/catch)。
// 与 fmtTime 算法等价(此处即复用),各调用点自己保留 `if (window.__fmt?.time) return window.__fmt.time(x)`
// 单例检查那一行不动,只把兜底体换成这个 import。
export function fmtTimeFallback(iso) { return fmtTime(iso); }
// YYYY-MM-DD(各页 fmtDate 共用)
function fmtDate(iso) {
  if (!iso) return "—";
  try { return new Date(iso).toISOString().slice(0, 10); } catch (_) { return "—"; }
}
// HH:MM(消息时间戳),用 now 或传入时间
function nowHHMM(d) {
  try { return (d ? new Date(d) : new Date()).toLocaleTimeString().slice(0, 5); } catch (_) { return ""; }
}
// null 安全千分位(非数字原样转字符串);不做 K/M 缩写
function fmtNum(n) {
  return n == null ? "—" : (typeof n === "number" ? n.toLocaleString() : String(n));
}
// K/M 缩写(语义统一 #30,与 fmtNum 分立 —— 后者是纯千分位,不缩写)。
// 规范语义:falsy → "—";≥1e6 → 整数 M;≥1e3 → 整数 K;否则原样。
// (= settings.jsx / MobileSettings 的 fmtCtx;platform-app fmtN 是 2/1 位小数的不同变体,不收。)
function fmtCompact(n) {
  if (!n) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(0)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}
window.__fmt = { bytes: fmtBytes, ago: fmtAgo, time: fmtTime, date: fmtDate, nowHHMM, n: fmtNum, compact: fmtCompact };
window.__guessKind = function () { return guessKind.apply(null, arguments); };

// ----------------------------------------------------------
//  Hydrators
// ----------------------------------------------------------
// 把 mock 的 admin 用户名抹成中性匿名占位，
// 防止真没登录的页面看到 mock 用户误以为已经登录。任何依赖 platform.user
// 判断登录态的 UI 都应该转用 window.RPG_AUTH.authed。
function anonymizeUser(u) {
  return {
    ...(u || {}),
    username: "guest",
    display_name: "未登录",
    role: "anonymous",
    uid: "",
    bio: "",
    id: null,
  };
}

async function hydratePlatform() {
  if (!window.api) return { platform: baseline().platform, authed: false };
  const platform = deepCopy(baseline().platform);
  let authed = false;
  try {
    const me = await window.api.auth.me();
    if (me && me.user) {
      authed = true;
      // base:同一 P0 根修的配套加固 —— baseline().platform 兜底为 {} 后 platform.user
      // 可能为 undefined,而下面的空值回退链(如 bio 为空串时读 platform.user.bio)会再抛
      // TypeError。统一经 base 访问,与求值顺序/兜底形态彻底解耦。
      const base = platform.user || {};
      platform.user = {
        ...base,
        username: me.user.username || base.username,
        display_name: me.user.display_name || me.user.username || base.display_name,
        role: me.user.role || base.role,
        uid: me.user.uid || "u_" + (me.user.id || ""),
        bio: me.user.bio || base.bio,
        avatar_url: me.user.avatar_url || '',
        id: me.user.id,
        // 透传首登须知弹窗状态(null=从未 dismiss),否则前端 === null 判断永远拿不到字段
        welcome_dismissed_at: me.user.welcome_dismissed_at ?? null,
      };
    } else {
      // 后端确认匿名：把 mock admin 抹掉，避免「未登录看到管理员名片」
      platform.user = anonymizeUser(platform.user);
    }
  } catch (e) {
    // /api/auth/me 本身失败（一般是后端挂了）也按匿名处理，宁可空着
    platform.user = anonymizeUser(platform.user);
  }
  try {
    const info = await window.api.platform.info();
    if (info) {
      if (info.database) platform.database = { ...platform.database, ...info.database };
      if (info.stats) platform.stats = { ...platform.stats, ...info.stats };
    }
  } catch (e) { /* keep baseline */ }
  // 未登录就别打需要登录的业务接口：/api/scripts、/api/saves、/api/library
  // 这些在匿名访问下必 401，DevTools 控制台会刷红，影响审计噪音和登录页体验。
  // 同时也把 saves/scripts/recent_assets 抹空，防止 UI 上看到 mock 列表当真。
  if (!authed) {
    platform.saves = [];
    platform.scripts = [];
    platform.recent_assets = [];
    return { platform, authed };
  }
  // 登录态禁止保留 designer baseline：接口慢/失败时宁可显示空态或 loading，
  // 也不能把示例剧本、示例存档、示例统计误渲染成用户数据。
  Object.assign(platform, emptyPlatformFallback(platform));
  try {
    const scripts = await window.api.scripts.list();
    // task 24：后端走 page_payload 返 {items, page}；旧代码只看 .scripts 漏掉新条目。
    // 统一兼容形态：数组 / {items} / {scripts}。
    const scriptList = Array.isArray(scripts) ? scripts : (scripts?.items || scripts?.scripts || []);
    platform.scripts = scriptList.map(normalizeScript);
  } catch (e) { platform.scripts = []; }
  try {
    const saves = await window.api.saves.list();
    // task 24: 同 scripts，兼容 {items} / {saves} / 数组
    const list = Array.isArray(saves) ? saves : (saves?.items || saves?.saves || []);
    // 登录用户 → 真实 saves，哪怕是空数组也要覆盖（防止 mock 11/12/13/14 残留）
    platform.saves = list.map(normalizeSave);
  } catch (e) { platform.saves = []; }
  try {
    const lib = await window.api.library.list({ path: "" });
    const entries = (lib && (lib.entries || lib.items)) || [];
    if (entries.length) {
      platform.recent_assets = entries.slice(0, 8).map((e) => ({
        name: e.name || e.path || "未命名",
        size: e.size || 0,
        kind: e.kind || guessKind(e.name),
        at: fmtAgo(e.updated_at || e.mtime),
      }));
    } else {
      platform.recent_assets = [];
    }
  } catch (e) { platform.recent_assets = []; }
  // task 12：把统计派生成「真实数据 + 缺失标 null」，不再回退到 mock 12/38/67/21.4K。
  // ProfilePage 读这里的 stats；缺的字段（branches 没汇总接口、api_calls 需 usage 接口）置 null，
  // 渲染层判断 null → 显示「—」而不是 mock 数字。
  const branchSum = (platform.saves || []).reduce(
    (acc, s) => acc + (Number.isFinite(s.branch_count) ? s.branch_count : 0), 0,
  );
  platform.stats = {
    scripts: (platform.scripts || []).length,
    saves: (platform.saves || []).length,
    branches: branchSum || null,   // 0 / 缺数据都显示 —（branchSum 是粗略：每存档分支数之和，无 backend 汇总接口）
    assets: (platform.recent_assets || []).length || null,
    api_calls: null,               // 真实总调用要走 /api/me/usage，本页不强行拉
  };
  return { platform, authed };
}

function guessKind(name) {
  if (!name) return "file";
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["png","jpg","jpeg","gif","webp","svg"].includes(ext)) return "image";
  if (["mp3","wav","ogg","flac"].includes(ext)) return "audio";
  if (["mp4","mov","mkv","webm"].includes(ext)) return "video";
  if (["zip","tar","gz","7z","rar"].includes(ext)) return "archive";
  if (["md","markdown"].includes(ext)) return "markdown";
  if (["txt","log"].includes(ext)) return "text";
  if (["pdf"].includes(ext)) return "pdf";
  if (["json","yaml","yml","toml"].includes(ext)) return "data";
  return "file";
}
window.__normalizeScript = function (s) { return normalizeScript(s); };
window.__normalizeSave = function (s) { return normalizeSave(s); };

function normalizeScript(s) {
  return {
    id: s.id,
    uid: s.uid || ("scr_" + (s.id || "")),
    title: s.title || s.name || "未命名剧本",
    description: s.description || s.subtitle || "",
    chapter_count: s.chapter_count || s.chapters || 0,
    word_count: s.word_count || s.words || 0,
    import_report: s.import_report || { mode_label: s.mode_label, confidence: s.confidence, problem_label: s.problem_label },
    updated_at: fmtAgo(s.updated_at) || s.updated_at_human || "—",
    is_public: !!s.is_public,
    clone_count: s.clone_count || 0,
    // owner 判定字段必须透传(原来只在 _raw 里 → ScriptDetailPanel 的 s.owner_id===currentUserId
    // 恒 undefined===id → isOwner 恒 false,作者改不了自己剧本的叙事风格/分享模式)。
    owner_id: s.owner_id,
    is_subscribed: !!s.is_subscribed,
    _raw: s,
  };
}
function normalizeSave(s) {
  return {
    id: s.id,
    uid: s.uid || ("sv_" + (s.id || "")),
    title: s.title || s.name || ("存档 #" + s.id),
    script_id: s.script_id || (s.script && s.script.id),
    branch_count: s.branch_count || s.branches || 0,
    updated_at: fmtAgo(s.updated_at) || s.updated_at_human || "—",
    last_played_at: fmtAgo(s.last_played_at || s.updated_at) || "—",
    last_played_ts: s.last_played_at || s.updated_at || null,  // 原始时间戳,供排序
    created_ts: s.created_at || null,
    current: !!s.current,
    // save_kind 透传到顶层:酒馆存档(save_kind==='tavern')与游戏存档区分,
    // __openContinue / ProfilePage 据此分流(酒馆走 #tavern,不进游戏台)。
    save_kind: s.save_kind || 'game',
    _raw: s,
  };
}

// 深合并：对象走递归合并，数组/标量整体替换。
// 用途：backend /api/state 只回部分字段时，缺失的子对象/嵌套字段保留调用方给的安全骨架，
// 避免下游组件 `state.world.timeline.current_label` 这种链式访问炸 undefined。
function deepMergeInto(target, source) {
  if (!source || typeof source !== "object" || Array.isArray(source)) return target;
  for (const k of Object.keys(source)) {
    const v = source[k];
    if (v === undefined) continue;
    const cur = target[k];
    if (v && typeof v === "object" && !Array.isArray(v)
        && cur && typeof cur === "object" && !Array.isArray(cur)) {
      deepMergeInto(cur, v);
    } else {
      // 数组 / 标量 / null：整体替换（数组合并语义太歧义，宁可让 backend 出全）
      target[k] = v;
    }
  }
  return target;
}

async function hydrateGameState() {
  const allowMockFallback = !window.api || useDesignerFallback();
  const fallback = () => allowMockFallback ? deepCopy(baseline().state) : emptyGameStateFallback();
  if (!window.api) return fallback();
  try {
    const data = await window.api.game.state();
    if (!data || data.error) return fallback();
    // 深合并：backend 返回的 partial state 覆盖到安全骨架上，缺的字段不会冲掉
    // 老的 apply() 是「整段替换」语义，backend 没有 inventory/timeline 等子字段时
    // 会让 PanelStatus / ConfirmStrip 等链式访问炸 undefined（Game Console 白屏）。
    const merged = allowMockFallback ? deepCopy(baseline().state) : emptyGameStateFallback();
    deepMergeInto(merged, {
      player: data.player,
      world: data.world,
      relationships: data.relationships,
      memory: data.memory,
      worldline: data.worldline,
      permissions: data.permissions,
      suggestions: data.suggestions,
      turn: data.turn,
    });
    // history 是数组：backend 给了就整段替换，没给保留 baseline
    if (Array.isArray(data.history)) merged.history = data.history;
    // permissions.pending_* 必须是数组（ConfirmStrip 直接 .map）；baseline 有兜底，
    // 但 backend 给了一个没这两个字段的 permissions 会被深合并保留缺失 — 这里强制兜底。
    merged.permissions = merged.permissions || {};
    if (!Array.isArray(merged.permissions.pending_writes)) merged.permissions.pending_writes = [];
    if (!Array.isArray(merged.permissions.pending_questions)) merged.permissions.pending_questions = [];
    // task 21（Chrome OOM 修复）：之前 merged._raw = data 把整个后端响应（含 tools/models/
    // 完整 history/app 元数据）塞进 window.MOCK_STATE。admin 用户的 tools+models 可能 200KB+，
    // 加上 100+ 条 history，整体会被 React 在每次 setState 时通过引用传播到所有子树，
    // 配合 React DevTools 的对象遍历就有可能把 renderer 撑爆。只保留诊断需要的 save 上下文。
    merged._raw = {
      save_id: data.save_id ?? null,
      save_title: data.save_title ?? null,
      turn: data.turn ?? null,
    };
    return merged;
  } catch (e) {
    return fallback();
  }
}

// ----------------------------------------------------------
//  Public bootstrap
// ----------------------------------------------------------
const readyResolvers = [];
const ready = new Promise((res) => readyResolvers.push(res));
window.RPG_DATA_READY = ready;

// task 45：匿名访客顶部加可见『示例数据预览模式』横幅，避免用户把 mock 顾承砚/
// 北港码头/残页等示例当成自己存档。横幅永远固定在屏顶，提供登录链接。
function injectDemoBanner() {
  try {
    if (document.getElementById("rpg-demo-banner")) return;
    const div = document.createElement("div");
    div.id = "rpg-demo-banner";
    div.setAttribute("role", "status");
    div.style.cssText = [
      "position:fixed", "top:0", "left:0", "right:0", "z-index:99999",
      "background:#7a4f1c", "color:#fff8e1",
      "padding:8px 14px", "font-size:12.5px", "line-height:1.4",
      "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
      "letter-spacing:0.02em", "text-align:center",
      "box-shadow:0 2px 6px rgba(0,0,0,0.25)",
      "display:flex", "align-items:center", "justify-content:center", "gap:14px",
    ].join(";");
    const span = document.createElement("span");
    const strong = document.createElement("strong");
    strong.textContent = "示例数据预览模式";
    span.appendChild(strong);
    span.appendChild(document.createTextNode("：当前显示的存档 / 角色 / 剧本均为内置示例，不会落库。登录后可创建自己的真实数据。"));

    const link = document.createElement("a");
    link.href = "Login.html";
    link.textContent = "立即登录";
    link.style.cssText = "color:#ffd591;text-decoration:underline;font-weight:600;";

    const closeBtn = document.createElement("button");
    closeBtn.id = "rpg-demo-banner-close";
    closeBtn.textContent = "隐藏";
    closeBtn.style.cssText = "background:transparent;border:1px solid #ffd591;color:#fff8e1;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:11px;";

    div.appendChild(span);
    div.appendChild(link);
    div.appendChild(closeBtn);
    document.body.appendChild(div);
    // banner 高度补偿
    document.body.style.paddingTop = (parseFloat(getComputedStyle(document.body).paddingTop) || 0) + 36 + "px";
    closeBtn.addEventListener("click", () => {
      div.remove();
      document.body.style.paddingTop = (parseFloat(getComputedStyle(document.body).paddingTop) || 36) - 36 + "px";
    });
  } catch (_) { /* 容错：不挂 banner 也不能阻塞 boot */ }
}

async function bootstrap() {
  if (!window.api) {
    console.warn("[data-loader] window.api not present, staying on baseline mocks");
    // 没接 API 的纯设计预览：明确告诉调用方 authed=false + online=false
    window.RPG_AUTH = { authed: false, online: false };
    injectDemoBanner();
    readyResolvers.forEach((r) => r({ online: false, authed: false }));
    return;
  }
  const [{ platform, authed }, state] = await Promise.all([hydratePlatform(), hydrateGameState()]);
  window.MOCK_PLATFORM = platform;
  window.MOCK_STATE = state;
  // 让 mount 脚本可同步读到登录态（不必 await 整个 ready Promise）
  // owner 判定(剧本级叙事风格/分享模式/版本回滚等"仅作者可写"的 UI)依赖 RPG_AUTH.user_id,
  // 历史上这里只写 {authed,online} → user_id 恒 undefined → currentUserId 恒 null →
  // isOwner 对作者本人也恒 false(实测:剧本作者改不了自己剧本的叙事风格)。补齐 user_id + user。
  window.RPG_AUTH = {
    authed,
    online: true,
    user_id: (authed && platform && platform.user && platform.user.id != null) ? platform.user.id : null,
    user: (authed && platform) ? platform.user : null,
  };
  // task 45：未登录 / Login 页除外的所有页面都给横幅
  if (!authed && !/Login\.html/.test(location.pathname)) {
    injectDemoBanner();
  }
  // Novel / runSteps remain on baseline until backend exposes them.
  window.dispatchEvent(new CustomEvent("rpg-data-ready", { detail: { platform, state, authed } }));
  readyResolvers.forEach((r) => r({ online: true, authed, platform, state }));
}

// task 50：暴露 __refreshPlatform 让 PlatformShell 顶栏「刷新」按钮 / ValidateModal
// 「全部添加」/ CapPage addOpen 等可以触发一次完整重拉。比各自手动 fetch /api/platform
// 然后再 dispatch event 更一致，也避免 race。
window.__refreshPlatform = async function () {
  if (!window.api) return;
  try {
    const [{ platform, authed }, state] = await Promise.all([hydratePlatform(), hydrateGameState()]);
    window.MOCK_PLATFORM = platform;
    window.MOCK_STATE = state;
    // owner 判定(剧本级叙事风格/分享模式/版本回滚等"仅作者可写"的 UI)依赖 RPG_AUTH.user_id,
  // 历史上这里只写 {authed,online} → user_id 恒 undefined → currentUserId 恒 null →
  // isOwner 对作者本人也恒 false(实测:剧本作者改不了自己剧本的叙事风格)。补齐 user_id + user。
  window.RPG_AUTH = {
    authed,
    online: true,
    user_id: (authed && platform && platform.user && platform.user.id != null) ? platform.user.id : null,
    user: (authed && platform) ? platform.user : null,
  };
    window.dispatchEvent(new CustomEvent("rpg-data-ready", { detail: { platform, state, authed } }));
    // 顶栏「刷新」按钮调本函数时,各 page 走自己的 reload(剧本/存档/角色卡 useEffect
    // 监听这些事件触发重拉数据)。否则 platform 元数据更新了,但 ScriptsPage 列表
    // 还是上次的快照,用户感觉"点了没反应"。
    try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
    try { window.dispatchEvent(new CustomEvent("rpg-saves-updated")); } catch (_) {}
    try { window.dispatchEvent(new CustomEvent("rpg-user-cards-updated")); } catch (_) {}
    return { platform, state, authed };
  } catch (e) {
    console.warn("[data-loader] __refreshPlatform failed:", e?.message || e);
    throw e;
  }
};

// P0 根修配套:启动推迟一个 microtask —— 模块图求值是同步的,求值期间入队的
// microtask 必然在整张图(含 mock-data.js / api-client.js 的副作用)完成后才跑,
// 恢复「boot 时全局已就绪」的原始语义,不再依赖 chunk 内模块排序。
queueMicrotask(() => {
  if (window.api) bootstrap();
  else window.addEventListener("api-ready", bootstrap, { once: true });
});

export {};
