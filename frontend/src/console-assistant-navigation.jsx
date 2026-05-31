/*
 * console-assistant-navigation.jsx — task 57: 控制台助手页面导航 + 高亮
 * ---------------------------------------------------------------
 * 暴露:
 *   window.handleAssistantNavigation(target, reason, dirtyCheck)
 *
 * 流程:
 *   1. 解析 target → pageId + anchor
 *   2. 未保存检测: 读 window.__cap_dirty_pages (Map<pageId, true>)
 *      当前页 dirty 时弹 confirm modal,用户拒绝 → 取消跳转
 *   3. 触发跳转:
 *      · Platform.html: 同页内通过 history.replaceState + dispatchEvent("hashchange")
 *        (PlatformApp 已监听 hashchange)
 *      · Game Console.html: 如果 target 属于平台页,window.open Platform.html#<pageId>
 *   4. 跳转完成后,找 [data-cap-anchor="<target>"] 元素 → 加 cap-highlight class
 *      + scrollIntoView + 一次性 click listener (capture) 用于消高亮
 *
 * 全局 dirty 跟踪:
 *   window.__cap_dirty_pages = new Map()
 *   表单输入: window.__cap_dirty_pages.set("settings.profile", true)
 *   保存/重置: window.__cap_dirty_pages.delete("settings.profile")
 *
 * 这个文件没有 React 组件 — 只挂一段 helper + 注入一段 CSS。
 * Platform.html / Game Console.html 在 console-assistant-panel.jsx 之前 load。
 */
// ESM 模块只执行一次,不需要幂等 guard
window.__cap_navigation_installed = true;

// ---------- 全局 dirty 表 ----------
if (!(window.__cap_dirty_pages instanceof Map)) {
  window.__cap_dirty_pages = new Map();
}

// ---------- 注入高亮 CSS ----------
const CSS_ID = "cap-navigation-styles-v1";
if (!document.getElementById(CSS_ID)) {
  const css = `
.cap-highlight {
position: relative;
animation: cap-highlight-pulse 1.5s ease-in-out infinite;
outline: 2px solid var(--accent, #c96442);
outline-offset: 4px;
border-radius: 6px;
scroll-margin-top: 80px;
}
@keyframes cap-highlight-pulse {
0%, 100% {
  outline-color: rgba(255, 140, 60, 0.9);
  box-shadow: 0 0 0 0 rgba(255, 140, 60, 0.4);
}
50% {
  outline-color: rgba(255, 140, 60, 0.5);
  box-shadow: 0 0 0 10px rgba(255, 140, 60, 0);
}
}

/* ---- nav confirm modal (vanilla,不依赖 React) ---- */
.cap-nav-modal-mask {
position: fixed; inset: 0;
background: rgba(0, 0, 0, 0.55);
z-index: 10500;
display: flex; align-items: center; justify-content: center;
animation: cap-fade-in .15s ease-out;
}
@keyframes cap-fade-in { from { opacity: 0 } to { opacity: 1 } }
.cap-nav-modal {
width: min(420px, 92vw);
background: var(--panel, #211f1d);
color: var(--text, #ebe7df);
border: 1px solid var(--line, #36322d);
border-radius: 10px;
padding: 18px 20px 14px 20px;
font-family: var(--font-sans, system-ui);
font-size: 13.5px;
line-height: 1.55;
box-shadow: 0 12px 40px -10px rgba(0, 0, 0, 0.6);
}
.cap-nav-modal h3 {
margin: 0 0 8px 0;
font-size: 14.5px;
color: var(--danger, #c8675d);
font-weight: 600;
}
.cap-nav-modal p { margin: 0 0 14px 0; color: var(--text, #ebe7df); }
.cap-nav-modal .cap-nav-reason {
font-size: 12px;
color: var(--muted, #968f85);
font-style: italic;
margin-bottom: 14px;
}
.cap-nav-modal .cap-nav-actions {
display: flex;
gap: 8px;
justify-content: flex-end;
}
.cap-nav-modal button {
padding: 6px 14px;
border-radius: 6px;
font-size: 12.5px;
cursor: pointer;
border: 1px solid var(--line-strong, #4a4540);
background: var(--panel-2, #282623);
color: var(--text, #ebe7df);
}
.cap-nav-modal button.primary {
background: var(--accent, #c96442);
border-color: var(--accent, #c96442);
color: #fff;
}
.cap-nav-modal button.primary:hover { filter: brightness(1.08); }
.cap-nav-modal button:hover { background: var(--panel-3, #2f2c28); }
`;
  const style = document.createElement("style");
  style.id = CSS_ID;
  style.textContent = css;
  document.head.appendChild(style);
}

// ---------- target → pageId 解析 ----------
// 大多数 target 形如 "<pageId>.<anchor>" 或 "<pageId>";pageId 对应 PL_NAV
// 的 nav id(parsePageFromHash 接受 saves-branches / scripts-import 这种)。
function targetToPageId(target) {
  if (!target) return null;
  const t = String(target);
  // 显式映射(target → 平台 pageId)
  const MAP = {
    "settings": "settings",
    "settings.preferences": "settings",
    "settings.models": "settings",
    "settings.models.gm": "settings",
    "settings.models.console_assistant": "settings",
    "settings.modelparams": "settings",
    "settings.memory": "settings",
    "settings.permissions": "settings",
    "settings.deploy": "admin-deploy",
    "admin.users":        "admin-users",
    "admin.usage":        "admin-usage",
    "admin.audit":        "admin-audit",
    "admin.health":       "admin-health",
    "admin.logs":         "admin-logs",
    "admin.registration": "admin-registration",
    "admin.security":     "admin-security",
    "admin.maintenance":  "admin-maintenance",
    "settings.danger": "settings",
    "settings.profile": "me-edit",
    "settings.api": "settings",
    "scripts": "scripts",
    "scripts.list": "scripts",
    "scripts.import": "scripts-import",
    "saves": "saves",
    "saves.list": "saves",
    "saves.branches": "saves-branches",
    "cards": "cards",
    "cards.user": "cards",
    "cards.npc": "cards-npc",
    "personas": "me-settings",
    "library": "library",
    "usage": "usage",
    "modules": "modules",
    "me": "me",
    "me.edit": "me-edit",
    "me.settings": "me-settings",
    // task 110: 跨 SPA 跳转 — Game Console 是独立 SPA, 不是 platform sub-page
    "game_console": "__GAME_CONSOLE__",
    "game": "__GAME_CONSOLE__",
  };
  if (MAP[t]) return MAP[t];
  // 兜底:取第一段
  const head = t.split(".")[0];
  return head || null;
}

// settings 的子 section 锚点需要在跳到 settings 后通过 hash fragment 提示。
// SettingsPage 内部 section state 由 setSection 控制,我们额外抛事件让 settings
// 监听后自动切到正确 sub-section(若加监听)。这里只负责发事件,
// SettingsPage 是否监听由后续 patch。
function dispatchSubSectionHint(target) {
  try {
    window.dispatchEvent(new CustomEvent("cap-navigate-subsection", {
      detail: { target },
    }));
  } catch (_) {}
}

// ---------- 弹未保存确认 ----------
function confirmDirtyNav(reason) {
  return new Promise((resolve) => {
    const mask = document.createElement("div");
    mask.className = "cap-nav-modal-mask";
    mask.innerHTML = `
      <div class="cap-nav-modal" role="alertdialog" aria-label="未保存确认">
        <h3>当前页面有未保存的修改</h3>
        <p>跳转到目标页面会丢失这些修改。继续吗?</p>
        ${reason ? `<div class="cap-nav-reason">原因: ${escapeHtml(reason)}</div>` : ""}
        <div class="cap-nav-actions">
          <button data-cap-act="cancel">取消跳转</button>
          <button class="primary" data-cap-act="confirm">放弃修改并跳转</button>
        </div>
      </div>
    `;
    const cleanup = (decision) => {
      try { mask.remove(); } catch (_) {}
      document.removeEventListener("keydown", onKey);
      resolve(decision);
    };
    const onKey = (e) => {
      if (e.key === "Escape") cleanup(false);
    };
    mask.addEventListener("click", (e) => {
      if (e.target === mask) cleanup(false);
      const act = e.target && e.target.getAttribute && e.target.getAttribute("data-cap-act");
      if (act === "cancel") cleanup(false);
      if (act === "confirm") cleanup(true);
    });
    document.addEventListener("keydown", onKey);
    document.body.appendChild(mask);
  });
}
function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// ---------- 高亮目标元素 ----------
function applyHighlight(target) {
  if (!target) return false;
  // 先清掉之前残留的高亮
  document.querySelectorAll(".cap-highlight").forEach((el) => {
    el.classList.remove("cap-highlight");
  });
  // 找元素;尝试精确,再 fallback 到截短 target 的 prefix
  let el = document.querySelector('[data-cap-anchor="' + cssEscape(target) + '"]');
  if (!el) {
    // 退化:settings.models.gm → settings.models → settings
    const parts = String(target).split(".");
    for (let i = parts.length - 1; i > 0; i--) {
      const prefix = parts.slice(0, i).join(".");
      el = document.querySelector('[data-cap-anchor="' + cssEscape(prefix) + '"]');
      if (el) break;
    }
  }
  if (!el) return false;
  el.classList.add("cap-highlight");
  try { el.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (_) {}
  // 一次性 click listener (capture) 移除高亮
  const off = () => {
    try { el.classList.remove("cap-highlight"); } catch (_) {}
    document.removeEventListener("click", off, true);
  };
  // 用 setTimeout 避免「触发跳转的 click」立刻把高亮清掉
  setTimeout(() => {
    document.addEventListener("click", off, true);
  }, 200);
  return true;
}
function cssEscape(s) {
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(s);
  }
  return String(s).replace(/["\\]/g, "\\$&");
}

// ---------- 触发页面跳转 ----------
function navigateTo(pageId) {
  // task 110: 跨 SPA 跳转到 Game Console (独立 HTML)
  if (pageId === "__GAME_CONSOLE__") {
    try {
      location.href = "Game Console.html";
      return true;
    } catch (_) { return false; }
  }
  const isPlatform = /Platform\.html/i.test(location.pathname)
    || (document.body && document.body.getAttribute("data-screen-label") === "Platform");
  if (isPlatform) {
    try { history.replaceState(null, "", "#" + pageId); } catch (_) {}
    try { window.dispatchEvent(new HashChangeEvent("hashchange")); } catch (_) {
      // 老浏览器兜底
      const ev = document.createEvent("Event");
      ev.initEvent("hashchange", true, false);
      window.dispatchEvent(ev);
    }
    return true;
  }
  // Game Console: 跨页跳到 Platform
  try {
    window.open("Platform.html#" + pageId, "_blank");
    return true;
  } catch (_) { return false; }
}

// ---------- 主入口 ----------
async function handleAssistantNavigation(target, reason, dirtyCheck) {
  target = (target || "").trim();
  if (!target) return { ok: false, error: "target 为空" };

  // 当前页 dirty 检查
  if (dirtyCheck && window.__cap_dirty_pages instanceof Map) {
    // 任一 dirty 都需要询问 (我们没法精确知道用户当前在哪一页,保守做法)
    const anyDirty = Array.from(window.__cap_dirty_pages.values()).some(Boolean);
    if (anyDirty) {
      const proceed = await confirmDirtyNav(reason);
      if (!proceed) {
        return { ok: false, cancelled: true, error: "用户取消跳转" };
      }
      // 用户确认丢弃 → 清表
      window.__cap_dirty_pages.clear();
    }
  }

  const pageId = targetToPageId(target);
  if (!pageId) {
    return { ok: false, error: "无法解析 target=" + target };
  }
  navigateTo(pageId);
  // settings 类 target 提示子 section 切换
  if (target.startsWith("settings.")) {
    dispatchSubSectionHint(target);
  }
  // 等 DOM 渲染完再找锚点;React state 切换需要至少 1 帧
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  // 多 try 几次,React 子组件可能 lazy 渲染
  let highlighted = false;
  for (let i = 0; i < 8; i++) {
    highlighted = applyHighlight(target);
    if (highlighted) break;
    await new Promise((r) => setTimeout(r, 80));
  }
  return { ok: true, target, pageId, highlighted };
}

window.handleAssistantNavigation = handleAssistantNavigation;
// 辅助 API 给页面用
window.__capMarkDirty = (pageId) => {
  if (!(window.__cap_dirty_pages instanceof Map)) window.__cap_dirty_pages = new Map();
  window.__cap_dirty_pages.set(pageId, true);
};
window.__capClearDirty = (pageId) => {
  if (!(window.__cap_dirty_pages instanceof Map)) return;
  if (pageId) window.__cap_dirty_pages.delete(pageId);
  else window.__cap_dirty_pages.clear();
};

export { handleAssistantNavigation };
