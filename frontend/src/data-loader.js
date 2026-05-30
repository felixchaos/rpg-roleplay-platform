/* ============================================================
 *  RPG Roleplay · Data Loader
 *  -----------------------------------------------------------
 *  Bridges the real backend (window.api) with the Claude Design
 *  mock globals (window.MOCK_*). On first load it tries to fill
 *  the mocks from /api/* and keeps the static designer fallback
 *  values if the backend is unreachable.
 *
 *  Pages can wait for `window.RPG_DATA_READY` (a Promise) or the
 *  `rpg-data-ready` DOM event before rendering — the included
 *  HTML uses it to delay initial paint when the API is reachable.
 * ============================================================ */
// Capture the designer baseline so we can fall back / extend it.
const BASELINE = {
  novel: deepCopy(window.MOCK_NOVEL),
  state: deepCopy(window.MOCK_STATE),
  runSteps: deepCopy(window.MOCK_RUN_STEPS),
  platform: deepCopy(window.MOCK_PLATFORM),
};
window.__MOCK_BASELINE = BASELINE;

function deepCopy(o) { try { return JSON.parse(JSON.stringify(o)); } catch (_) { return o; } }

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
  if (diff < 60) return "刚刚";
  if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + " 天前";
  return d.toLocaleDateString();
}
window.__fmt = { bytes: fmtBytes, ago: fmtAgo };
window.__guessKind = function () { return guessKind.apply(null, arguments); };

// ----------------------------------------------------------
//  Hydrators
// ----------------------------------------------------------
// 把 mock 的 admin 用户名（"晓卡 @yanling · admin"）抹成中性匿名占位，
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
  if (!window.api) return { platform: BASELINE.platform, authed: false };
  const platform = deepCopy(BASELINE.platform);
  let authed = false;
  try {
    const me = await window.api.auth.me();
    if (me && me.user) {
      authed = true;
      platform.user = {
        ...platform.user,
        username: me.user.username || platform.user.username,
        display_name: me.user.display_name || me.user.username || platform.user.display_name,
        role: me.user.role || platform.user.role,
        uid: me.user.uid || "u_" + (me.user.id || ""),
        bio: me.user.bio || platform.user.bio,
        id: me.user.id,
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
  try {
    const scripts = await window.api.scripts.list();
    // task 24：后端走 page_payload 返 {items, page}；旧代码只看 .scripts 漏掉新条目。
    // 统一兼容形态：数组 / {items} / {scripts}。
    const scriptList = Array.isArray(scripts) ? scripts : (scripts?.items || scripts?.scripts || []);
    platform.scripts = scriptList.map(normalizeScript);
  } catch (e) { /* keep baseline */ }
  try {
    const saves = await window.api.saves.list();
    // task 24: 同 scripts，兼容 {items} / {saves} / 数组
    const list = Array.isArray(saves) ? saves : (saves?.items || saves?.saves || []);
    // 登录用户 → 真实 saves，哪怕是空数组也要覆盖（防止 mock 11/12/13/14 残留）
    platform.saves = list.map(normalizeSave);
  } catch (e) { /* keep baseline */ }
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
  } catch (e) { /* keep baseline */ }
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
    _raw: s,
  };
}

// 深合并：对象走递归合并，数组/标量整体替换。
// 用途：backend /api/state 只回部分字段时，缺失的子对象/嵌套字段保留 baseline，
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
  if (!window.api) return BASELINE.state;
  try {
    const data = await window.api.game.state();
    if (!data || data.error) return BASELINE.state;
    // 深合并：backend 返回的 partial state 覆盖到 baseline 上，缺的字段不会冲掉
    // 老的 apply() 是「整段替换」语义，backend 没有 inventory/timeline 等子字段时
    // 会让 PanelStatus / ConfirmStrip 等链式访问炸 undefined（Game Console 白屏）。
    const merged = deepCopy(BASELINE.state);
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
    return BASELINE.state;
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
    div.innerHTML =
      '<span><strong>示例数据预览模式</strong>：当前显示的存档 / 角色 / 剧本均为内置示例，不会落库。' +
      '登录后可创建自己的真实数据。</span>' +
      '<a href="Login.html" style="color:#ffd591;text-decoration:underline;font-weight:600;">立即登录</a>' +
      '<button id="rpg-demo-banner-close" style="background:transparent;border:1px solid #ffd591;color:#fff8e1;border-radius:4px;padding:2px 8px;cursor:pointer;font-size:11px;">隐藏</button>';
    document.body.appendChild(div);
    // banner 高度补偿
    document.body.style.paddingTop = (parseFloat(getComputedStyle(document.body).paddingTop) || 0) + 36 + "px";
    div.querySelector("#rpg-demo-banner-close").addEventListener("click", () => {
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
  window.RPG_AUTH = { authed, online: true };
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
    window.RPG_AUTH = { authed, online: true };
    window.dispatchEvent(new CustomEvent("rpg-data-ready", { detail: { platform, state, authed } }));
    return { platform, state, authed };
  } catch (e) {
    console.warn("[data-loader] __refreshPlatform failed:", e?.message || e);
    throw e;
  }
};

if (window.api) bootstrap();
else window.addEventListener("api-ready", bootstrap, { once: true });

export {};
