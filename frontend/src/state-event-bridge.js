/**
 * state-event-bridge.js — task 69
 *
 * 长连 SSE `/api/v1/state_events`,后端推送来 (topic, op) 事件后,
 * 转 CustomEvent("rpg-{topic}-updated"),触发各页面已有的 reload listener。
 *
 * 现有总线 (frontend/src/platform-app.jsx 已订阅):
 *   - rpg-saves-updated
 *   - rpg-scripts-updated
 *   - rpg-user-updated
 *   - 一并新增:rpg-cards-updated, rpg-personas-updated, rpg-permissions-updated,
 *     rpg-preferences-updated, rpg-branches-updated, rpg-mcp-updated
 *
 * 自动重连 (5s 退避),tab 隐藏时不重连。authed 才连。
 * W4-7: 加 watchdog — 每 10s 检查,45s 无事件强制重连。
 */
(function () {
  "use strict";
  if (window.__rpg_state_bridge_inited__) return;
  window.__rpg_state_bridge_inited__ = true;

  // api-client.js exposes the resolved cross-origin base via window.__API_BASE
  const BASE = (window.__API_BASE || window.RPG_API_BASE || "").replace(/\/+$/, "");
  const URL = (BASE || "") + "/api/v1/state_events";
  let es = null;
  let backoff = 1000;
  let stopped = false;
  let connectedAt = 0;
  let lastEventAt = 0;
  let watchdogTimer = null;

  const WATCHDOG_INTERVAL = 10_000;  // 每 10s 检查一次
  const WATCHDOG_TIMEOUT  = 45_000;  // 45s 无事件 → 强制重连

  function touchLastEvent() {
    lastEventAt = Date.now();
  }

  function startWatchdog() {
    stopWatchdog();
    watchdogTimer = setInterval(() => {
      if (stopped || !es) { stopWatchdog(); return; }
      if (lastEventAt > 0 && Date.now() - lastEventAt > WATCHDOG_TIMEOUT) {
        console.warn("[state-event-bridge] watchdog: no event for 45s, forcing reconnect");
        try { es.close(); } catch (_) {}
        es = null;
        stopWatchdog();
        scheduleReconnect();
      }
    }, WATCHDOG_INTERVAL);
  }

  function stopWatchdog() {
    if (watchdogTimer !== null) { clearInterval(watchdogTimer); watchdogTimer = null; }
  }

  function connect() {
    if (stopped) return;
    if (es) { try { es.close(); } catch (_) {} es = null; }
    try {
      es = new EventSource(URL, { withCredentials: true });
    } catch (e) {
      console.warn("[state-event-bridge] EventSource ctor failed:", e?.message);
      scheduleReconnect();
      return;
    }
    lastEventAt = Date.now();  // 连接建立时初始化时间戳
    startWatchdog();
    es.addEventListener("hello", (ev) => {
      backoff = 1000;
      connectedAt = Date.now();
      touchLastEvent();
    });
    es.addEventListener("state_change", (ev) => {
      touchLastEvent();
      let data;
      try { data = JSON.parse(ev.data || "{}"); } catch { return; }
      const topic = (data && data.topic) || "";
      const op = (data && data.op) || "updated";
      if (!topic) return;
      try {
        window.dispatchEvent(new CustomEvent(`rpg-${topic}-updated`, {
          detail: { op, payload: data.payload || {}, ts: data.ts || Date.now() },
        }));
      } catch (_) {}
    });
    es.addEventListener("error", (_ev) => {
      // EventSource 自己有重连,但失败多次后我们也手动重置
      const elapsed = Date.now() - connectedAt;
      if (elapsed > 60_000) backoff = 1000;  // 连上一分钟以上的连接,重置退避
      stopWatchdog();
      try { es.close(); } catch (_) {}
      es = null;
      scheduleReconnect();
    });
  }

  function scheduleReconnect() {
    if (stopped) return;
    const delay = Math.min(30_000, backoff);
    backoff = Math.min(30_000, backoff * 2);
    setTimeout(connect, delay);
  }

  function start() {
    if (!window.RPG_AUTH || !window.RPG_AUTH.authed) return;
    stopped = false;
    connect();
  }

  function stop() {
    stopped = true;
    stopWatchdog();
    if (es) { try { es.close(); } catch (_) {} es = null; }
  }

  // tab 切到后台时停,回来时再起 — 省网络。
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      // 停连接但保留 stopped=false 让 visible 时自动重连
      stopWatchdog();
      if (es) { try { es.close(); } catch (_) {} es = null; }
    } else if (document.visibilityState === "visible") {
      if (window.RPG_AUTH && window.RPG_AUTH.authed && !es && !stopped) {
        connect();
      }
    }
  });

  // 用户登录登出时启停
  window.addEventListener("rpg-data-ready", (ev) => {
    if (ev?.detail?.authed) start();
    else stop();
  });

  // 暴露给调试
  window.__rpgStateEventBridge = { start, stop, isConnected: () => !!es };

  // 如果 data-loader 已经跑完, 立刻试 start
  if (window.RPG_AUTH && window.RPG_AUTH.authed) start();
})();
