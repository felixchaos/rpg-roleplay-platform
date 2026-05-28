/**
 * worldbook-status-toast.js — task 88
 *
 * 监听 rpg-worldbook-status CustomEvent (由 api-client._wbHook 在 SSE
 * worldbook_consulting / worldbook_ready 时 dispatch),在屏幕顶部居中
 * 显示 "GM 正在翻阅设定…" 半透明小条;ready 后淡出。
 *
 * 不依赖 React, 兼容 Game Console / Platform 两个页面。
 */
(function () {
  "use strict";
  if (window.__rpg_wb_toast_inited__) return;
  window.__rpg_wb_toast_inited__ = true;

  const CSS = `
  .rpg-wb-toast {
    position: fixed; top: 14px; left: 50%; transform: translateX(-50%);
    z-index: 9999;
    background: rgba(26, 24, 23, 0.94);
    color: #ebe7df;
    border: 1px solid #36322d;
    border-radius: 9999px;
    padding: 7px 14px;
    font-size: 12.5px;
    box-shadow: 0 4px 18px rgba(0,0,0,0.55);
    display: flex; align-items: center; gap: 8px;
    opacity: 0;
    transition: opacity 0.18s ease;
    pointer-events: none;
    max-width: 480px;
    white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .rpg-wb-toast.rpg-wb-show { opacity: 1; }
  .rpg-wb-spinner {
    width: 10px; height: 10px;
    border: 2px solid rgba(255,255,255,0.18);
    border-top-color: #c96442;
    border-radius: 50%;
    animation: rpg-wb-spin 0.85s linear infinite;
  }
  @keyframes rpg-wb-spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
  }
  `;

  let el = null;
  let textEl = null;
  let hideTimer = null;
  // 待 dispatch 的 pending events,在 DOM 还没就绪时排队
  const pending = [];

  function ensureDom() {
    if (el) return el;
    if (!document.body) return null;
    if (!document.getElementById("rpg-wb-toast-css")) {
      const style = document.createElement("style");
      style.id = "rpg-wb-toast-css";
      style.textContent = CSS;
      document.head.appendChild(style);
    }
    el = document.createElement("div");
    el.className = "rpg-wb-toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    el.innerHTML = '<span class="rpg-wb-spinner"></span><span class="rpg-wb-text"></span>';
    document.body.appendChild(el);
    textEl = el.querySelector(".rpg-wb-text");
    // flush pending
    while (pending.length) handle(pending.shift());
    return el;
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ensureDom, { once: true });
  } else {
    ensureDom();
  }

  function show(text) {
    if (!el && !ensureDom()) return;
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    textEl.textContent = text;
    el.classList.add("rpg-wb-show");
  }
  function hideSoon(text, delay) {
    if (!el && !ensureDom()) return;
    textEl.textContent = text;
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(() => { el.classList.remove("rpg-wb-show"); }, delay || 600);
  }

  function handle(d) {
    if (d.state === "consulting") {
      const q = (d.query || "").slice(0, 32);
      const phase = (d.phase || "").slice(0, 28);
      const parts = ["GM 正在翻阅设定"];
      if (phase) parts.push(" · " + phase);
      else if (q) parts.push(" · " + q);
      show(parts.join(""));
    } else if (d.state === "ready") {
      const conf = typeof d.confidence === "number" ? d.confidence : null;
      let msg;
      if (conf !== null && conf < 0.4) {
        msg = "翻阅未找到精确锚点 (GM 将谨慎处理)";
      } else if (d.phase) {
        msg = "已翻阅 · " + d.phase;
      } else {
        msg = "翻阅完成";
      }
      hideSoon(msg, 1200);
    }
  }

  window.addEventListener("rpg-worldbook-status", (ev) => {
    const d = (ev && ev.detail) || {};
    if (!el) { pending.push(d); return; }
    handle(d);
  });
})();
