/* ============================================================
 *  RPG Roleplay · Frontend API Client
 *  -----------------------------------------------------------
 *  Browser-side wrapper around the FastAPI backend.
 *  - Cookie-based session (rpg_session) via credentials: "include"
 *  - SSE helper for /api/chat and /api/opening
 *  - All known endpoints typed as window.api.<group>.<method>(...)
 *  - Falls back to MOCK_* globals when offline (so the static
 *    Claude Design pages still render even when backend is down)
 * ============================================================ */
(function () {
  "use strict";

  // Base URL: either same-origin (when served by FastAPI) or
  // local backend (when opened as file:// or via static server).
  function detectBase() {
    try {
      if (location.protocol === "file:") return "http://127.0.0.1:7860";
      // If we're already on the FastAPI port → same origin.
      if (location.port === "7860") return "";
      // Static dev server (e.g. python -m http.server 5173) on
      // another port → cross-origin to backend.
      if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
        return "http://127.0.0.1:7860";
      }
      // Production / hosted: rely on same-origin proxy.
      return "";
    } catch (_) {
      return "http://127.0.0.1:7860";
    }
  }

  const BASE = detectBase();
  window.__API_BASE = BASE;

  // ---- core fetch helpers ------------------------------------
  async function _send(path, opts) {
    const url = (path.startsWith("http") ? path : BASE + path);
    const init = Object.assign(
      {
        credentials: "include",
        headers: { "Accept": "application/json" },
      },
      opts || {}
    );
    if (init.body && typeof init.body === "object" && !(init.body instanceof FormData)) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(init.body);
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      throw new ApiError("network", 0, "网络异常：" + (e && e.message), { url });
    }
    const isJson = (res.headers.get("content-type") || "").indexOf("application/json") >= 0;
    let payload = null;
    if (isJson) {
      try { payload = await res.json(); } catch (_) { payload = null; }
    } else {
      payload = await res.text();
    }
    if (!res.ok) {
      const msg = (payload && payload.detail) || (payload && payload.error) || res.statusText;
      throw new ApiError(payload && payload.code, res.status, msg || ("HTTP " + res.status), payload);
    }
    return payload;
  }

  class ApiError extends Error {
    constructor(code, status, message, payload) {
      super(message);
      this.code = code || "error";
      this.status = status;
      this.payload = payload;
    }
  }
  window.ApiError = ApiError;

  const GET = (path, query) => {
    let p = path;
    if (query && Object.keys(query).length) {
      const usp = new URLSearchParams();
      for (const k of Object.keys(query)) {
        const v = query[k];
        if (v === undefined || v === null || v === "") continue;
        usp.set(k, v);
      }
      p = path + (path.indexOf("?") >= 0 ? "&" : "?") + usp.toString();
    }
    return _send(p, { method: "GET" });
  };
  const POST = (path, body) => _send(path, { method: "POST", body: body || {} });
  const PATCH = (path, body) => _send(path, { method: "PATCH", body: body || {} });
  const PUT = (path, body) => _send(path, { method: "PUT", body: body || {} });
  const DEL = (path, body) => _send(path, { method: "DELETE", body: body || {} });

  // ---- SSE helper for /api/chat & /api/opening ---------------
  // Posts a JSON body and parses the streaming response into
  // structured event objects: { event, data }.
  async function sseStream(path, body, handlers) {
    handlers = handlers || {};
    const url = (path.startsWith("http") ? path : BASE + path);
    const ctl = new AbortController();
    const promise = (async () => {
      let res;
      try {
        res = await fetch(url, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
          body: JSON.stringify(body || {}),
          signal: ctl.signal,
        });
      } catch (e) {
        if (handlers.onError) handlers.onError(new ApiError("network", 0, e && e.message));
        return;
      }
      if (!res.ok || !res.body) {
        let payload = null;
        try { payload = await res.json(); } catch (_) {}
        if (handlers.onError) {
          handlers.onError(new ApiError("http", res.status, (payload && payload.detail) || res.statusText, payload));
        }
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        let chunk;
        try { chunk = await reader.read(); } catch (e) { break; }
        if (chunk.done) break;
        buf += decoder.decode(chunk.value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const evt = parseSseBlock(raw);
          if (!evt) continue;
          if (handlers.onEvent) handlers.onEvent(evt);
          const cb = handlers["on_" + evt.event];
          if (cb) try { cb(evt.data); } catch (e) { console.error(e); }
        }
      }
      if (handlers.onClose) handlers.onClose();
    })();
    return { stop: () => ctl.abort(), done: promise };
  }
  function parseSseBlock(raw) {
    if (!raw) return null;
    let event = "message"; let dataLines = [];
    for (const line of raw.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
    }
    const data = dataLines.join("\n");
    let parsed;
    try { parsed = data ? JSON.parse(data) : null; } catch (_) { parsed = data; }
    return { event, data: parsed };
  }

  // task 88: 给 game.opening / game.chat 的 handlers 注入"世界书子代理"事件转发,
  // SSE 收到 worldbook_consulting / worldbook_ready 时同时 dispatch CustomEvent,
  // 任何 UI 监听 window.addEventListener("rpg-worldbook-status", ...) 都能拿到。
  function _wbHook(handlers) {
    handlers = handlers || {};
    const origConsult = handlers.on_worldbook_consulting;
    const origReady = handlers.on_worldbook_ready;
    handlers.on_worldbook_consulting = (d) => {
      try { window.dispatchEvent(new CustomEvent("rpg-worldbook-status",
            { detail: { state: "consulting", ...(d || {}) } })); } catch (_) {}
      if (origConsult) try { origConsult(d); } catch (_) {}
    };
    handlers.on_worldbook_ready = (d) => {
      try { window.dispatchEvent(new CustomEvent("rpg-worldbook-status",
            { detail: { state: "ready", ...(d || {}) } })); } catch (_) {}
      if (origReady) try { origReady(d); } catch (_) {}
    };
    return handlers;
  }

  // ============================================================
  //                       API SURFACE
  // ============================================================

  const api = {
    base: BASE,
    raw: { GET, POST, PATCH, PUT, DEL, sseStream },

    // ---------- Auth & session ----------
    auth: {
      register: (body) => POST("/api/auth/register", body),
      login: (body) => POST("/api/auth/login", body),
      logout: () => POST("/api/auth/logout", {}),
      me: () => GET("/api/auth/me"),
      // Frontend wishlist – mapped to new endpoints we will add.
      changePassword: (body) => POST("/api/auth/password", body),
      loginHistory: () => GET("/api/auth/login-history"),
      sessionsList: () => GET("/api/auth/sessions"),
      sessionsRevoke: (sid) => POST("/api/auth/sessions/revoke", { session_id: sid }),
      smsCode: (phone) => POST("/api/auth/sms-code", { phone }),
      smsVerify: (body) => POST("/api/auth/sms-verify", body),
      revokeAllSessions: () => POST("/api/auth/sessions/revoke-all", {}),
    },

    // ---------- Account / profile ----------
    account: {
      profile: () => GET("/api/me/profile"),
      saveProfile: (body) => POST("/api/profile", body),
      avatar: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send("/api/profile/avatar", { method: "POST", body: fd });
      },
      // task 50：BE 有 avatar reset 但 FE 没 wrapper（直接 raw POST 也行，加 wrapper 更清晰）
      avatarReset: () => POST("/api/profile/avatar/reset", {}),
      visibility: (body) => POST("/api/profile/visibility", body),
      exportData: (body) => POST("/api/account/export", body || {}),
      deactivate: () => POST("/api/account/deactivate", {}),
      deleteAccount: (body) => POST("/api/account/delete", body || {}),
      usage: (days) => GET("/api/me/usage", days ? { days } : undefined),
      usageTimeline: (days, group_by) => GET("/api/me/usage/timeline", { days: days || 30, group_by: group_by || "day" }),
      stats: () => GET("/api/me/stats"),
      preferences: (body) => POST("/api/me/preference", body),
      personas: {
        list: () => GET("/api/me/personas"),
        get: (id) => GET("/api/me/personas/" + encodeURIComponent(id)),
        upsert: (body) => POST("/api/me/personas", body),
        remove: (id) => POST("/api/me/personas/" + encodeURIComponent(id) + "/delete", {}),
      },
    },

    // ---------- Platform meta ----------
    platform: {
      info: () => GET("/api/platform"),
      settings: () => GET("/api/settings"),
      saveSetting: (body) => POST("/api/settings", body),
      commands: () => GET("/api/platform/commands"),
      search: (q, scope) => GET("/api/search", { q, scope }),
    },

    // ---------- Scripts ----------
    scripts: {
      list: () => GET("/api/scripts"),
      preview: (body) => POST("/api/scripts/preview", body),
      importScript: (body) => POST("/api/scripts/import", body),
      delete: (sid) => POST("/api/scripts/" + sid + "/delete", {}),
      chapters: (sid, q) => GET("/api/scripts/" + sid + "/chapters", q),
      updateChapter: (sid, idx, body) => POST(`/api/scripts/${sid}/chapters/${idx}`, body),
      mergeChapter: (sid, body) => POST(`/api/scripts/${sid}/chapters/merge`, body),
      splitChapter: (sid, idx, body) => POST(`/api/scripts/${sid}/chapters/${idx}/split`, body),
      resplit: (sid, body) => POST(`/api/scripts/${sid}/resplit`, body),
      chapterFacts: (sid, q) => GET(`/api/scripts/${sid}/chapter-facts`, q),
      worldbook: (sid) => GET(`/api/scripts/${sid}/worldbook`),
      knowledgeSync: (sid, body) => POST(`/api/scripts/${sid}/knowledge/sync`, body || {}),
      importStatus: (sid) => GET(`/api/scripts/${sid}/import-status`),
      importBudget: (sid, body) => POST(`/api/scripts/${sid}/import-budget`, body || {}),
      importPipeline: (sid, body) => POST(`/api/scripts/${sid}/import-pipeline`, body || {}),
      jobStatus: (jobId) => GET("/api/scripts/import-jobs/" + jobId),
      jobCancel: (jobId) => POST("/api/scripts/import-jobs/" + jobId + "/cancel", {}),
      myJobs: () => GET("/api/me/import-jobs"),
      // SSE stream for live import progress
      streamImport: (jobId, handlers) => {
        const url = BASE + "/api/scripts/import-jobs/" + jobId + "/stream";
        return openEventSource(url, handlers);
      },
    },

    // ---------- Saves & branches ----------
    saves: {
      list: () => GET("/api/saves"),
      create: (body) => POST("/api/saves", body),
      detail: (sid) => GET("/api/saves/" + sid),
      contextRuns: (sid, q) => GET("/api/saves/" + sid + "/context-runs", q),
      // task 50：BE 早就有这些 endpoint 但 FE 一直没 wrapper
      rename: (sid, title) => POST("/api/saves/" + sid + "/rename", { title }),
      remove: (sid) => POST("/api/saves/" + sid + "/delete", {}),
      activate: (sid) => POST("/api/saves/" + sid + "/activate", {}),
      exportUrl: (sid) => BASE + "/api/saves/" + sid + "/export",
      importFile: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send("/api/saves/import", { method: "POST", body: fd });
      },
    },
    branches: {
      list: (saveId) => GET("/api/branches/" + saveId),
      continueFrom: (body) => POST("/api/branches/continue", body),
      activate: (body) => POST("/api/branches/activate", body),
      delete: (body) => POST("/api/branches/delete", body),
      // task 116c: 软回滚 — 删除 message 及之后所有
      rollbackToMessage: (saveId, messageIndex) =>
        POST("/api/branches/rollback", { save_id: saveId, message_index: messageIndex }),
    },

    // ---------- 5E-compatible Rules (Ash Mine 等原创模组) ----------
    // 内部 ruleset id "dnd5e"；对外文案 "5E compatible / 五版规则兼容"。
    // 不引入官方 D&D 商标或非 SRD IP。
    rules: {
      modules: () => GET("/api/rules/modules"),
      // 低层原语：mutate 当前 save 加载模组。日常用 launchModule 建独立存档。
      startModule: (moduleId, character) => POST("/api/rules/module/start", { module_id: moduleId, character }),
      // 标准入口：建独立 save + 激活 + 加载模组 一步完成，不污染当前 save。
      launchModule: (moduleId, opts) => POST("/api/rules/module/launch", {
        module_id: moduleId, character: (opts || {}).character, title: (opts || {}).title,
      }),
      scene: () => GET("/api/rules/scene"),
      move: (to) => POST("/api/rules/move", { to }),
      action: (body) => POST("/api/rules/action", body),
      encounterStart: (encounterId, seed) => POST("/api/rules/encounter/start", { encounter_id: encounterId, seed }),
      encounterNext: () => POST("/api/rules/encounter/next", {}),
      encounterEnemy: (attackerId, targetId, seed) => POST("/api/rules/encounter/enemy", {
        attacker_id: attackerId, target_id: targetId || "player", seed,
      }),
      suggest: (text) => POST("/api/rules/suggest", { text }),
    },

    // ---------- Character cards (user + script) ----------
    cards: {
      myList: () => GET("/api/me/character-cards"),
      myGet: (id) => GET("/api/me/character-cards/" + id),
      myUpsert: (body) => POST("/api/me/character-cards", body),
      myDelete: (id) => POST("/api/me/character-cards/" + id + "/delete", {}),
      importTavern: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send("/api/me/character-cards/import-tavern", { method: "POST", body: fd });
      },
      // task 50：BE 有 import-json 但 FE 没 wrapper
      importJson: (body) => POST("/api/me/character-cards/import-json", body),
      exportTavern: (id) => BASE + "/api/me/character-cards/" + id + "/export-tavern",
      exportPng: (id) => BASE + "/api/me/character-cards/" + id + "/export-png",
      // Script-scoped (NPCs/world cards)
      scriptList: (sid) => GET("/api/scripts/" + sid + "/character-cards"),
      scriptGet: (sid, cid) => GET("/api/scripts/" + sid + "/character-cards/" + cid),
      scriptUpsert: (sid, body) => POST("/api/scripts/" + sid + "/character-cards", body),
      scriptDelete: (sid, cid) => POST("/api/scripts/" + sid + "/character-cards/" + cid + "/delete", {}),
      scriptEnabled: (sid, cid, on) => POST("/api/scripts/" + sid + "/character-cards/" + cid + "/enabled", { enabled: !!on }),
    },

    // ---------- Library / files ----------
    library: {
      list: (q) => GET("/api/library", q),
      upload: (file, path) => {
        const fd = new FormData();
        fd.append("file", file);
        if (path) fd.append("path", path);
        return _send("/api/library/upload", { method: "POST", body: fd });
      },
      mkdir: (body) => POST("/api/library/mkdir", body),
      delete: (body) => POST("/api/library/delete", body),
      downloadUrl: (path) => BASE + "/api/library/download?path=" + encodeURIComponent(path),
    },

    // ---------- Uploads (chunked) ----------
    // task 17: 后端 /api/uploads/init 要 {filename, total_bytes, total_chunks}（不是 size/chunk_size）。
    // 后端 /api/uploads/{id}/chunk 要 JSON {chunk_index, base64}（不是 multipart）。
    // 这里把 chunk 重写成读 Blob → base64 → JSON POST。
    uploads: {
      init: (body) => POST("/api/uploads/init", body),
      chunk: async (id, chunk, index) => {
        const base64 = await new Promise((resolve, reject) => {
          const r = new FileReader();
          r.onload = () => {
            const s = String(r.result || "");
            const i = s.indexOf(",");
            resolve(i >= 0 ? s.slice(i + 1) : s);
          };
          r.onerror = () => reject(r.error || new Error("分片读取失败"));
          r.readAsDataURL(chunk);
        });
        return POST("/api/uploads/" + id + "/chunk", { chunk_index: Number(index) || 0, base64 });
      },
      finish: (id, body) => POST("/api/uploads/" + id + "/finish", body || {}),
      cancel: (id) => POST("/api/uploads/" + id + "/cancel", {}),
    },

    // ---------- Credentials (per-user API keys) ----------
    credentials: {
      list: () => GET("/api/me/credentials"),
      set: (body) => POST("/api/me/credentials", body),
      remove: (body) => POST("/api/me/credentials/delete", body),
      test: (q) => GET("/api/me/credentials/test", q),
    },

    // ---------- Models & APIs ----------
    models: {
      list: () => GET("/api/models"),
      select: (body) => POST("/api/models/select", body),
      upsertApi: (body) => POST("/api/models/api", body),
      upsertModel: (body) => POST("/api/models/model", body),
      deleteModel: (body) => POST("/api/models/model/delete", body),
      visibility: (body) => POST("/api/models/visibility", body),
      validate: (body) => POST("/api/models/validate", body),
      remote: (q) => GET("/api/models/remote", q),
      diff: (q) => GET("/api/models/diff", q),
      probe: (body) => POST("/api/models/probe", body),
      pricing: () => GET("/api/models/pricing"),
      report: (q) => GET("/api/models/report", q),
      capabilities: () => GET("/api/models/capabilities"),
      capabilityLabels: () => GET("/api/models/capabilities/labels"),
    },

    // ---------- Tools / MCP / Skills ----------
    tools: {
      list: () => GET("/api/tools"),
    },
    mcp: {
      upsert: (body) => POST("/api/mcp/server", body),
      enabled: (body) => POST("/api/mcp/server/enabled", body),
      remove: (body) => POST("/api/mcp/server/delete", body),
      validate: (body) => POST("/api/mcp/server/validate", body),
      start: (body) => POST("/api/mcp/server/start", body),
      stop: (body) => POST("/api/mcp/server/stop", body),
      runtime: () => GET("/api/mcp/runtime"),
      tools: () => GET("/api/mcp/tools"),
      call: (body) => POST("/api/mcp/tool/call", body),
    },
    skills: {
      list: () => GET("/api/skills"),
      run: (skillId, body) => POST("/api/skills/" + encodeURIComponent(skillId) + "/run", body || {}),
      importPack: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send("/api/skills/import", { method: "POST", body: fd });
      },
    },
    // task 50：plugins 列表 (BE 已有，FE 之前没 wrapper)
    plugins: {
      list: () => GET("/api/plugins"),
    },

    // ---------- In-game state / chat ----------
    game: {
      state: () => GET("/api/state"),
      newGame: (body) => POST("/api/new", body || {}),
      saveGame: () => POST("/api/save", {}),
      stop: () => POST("/api/stop", {}),
      // SSE: opening / chat
      // task 88: 包一层让 worldbook_consulting/ready 自动 dispatch CustomEvent,
      // 任何 UI 监听 window.addEventListener("rpg-worldbook-status", ...) 即可。
      opening: (body, handlers) => sseStream("/api/opening", body || {}, _wbHook(handlers)),
      chat: (body, handlers) => sseStream("/api/chat", body || {}, _wbHook(handlers)),
      chatEstimate: (body) => POST("/api/chat/estimate", body),
      memoryMode: (mode) => POST("/api/memory/mode", { mode }),
      memoryAdd: (body) => POST("/api/memory/add", body),
      memoryRemove: (body) => POST("/api/memory/remove", body),
      permissions: (body) => POST("/api/permissions", body),
      pendingWrite: (body) => POST("/api/permissions/pending-write", body),
      clearQuestions: (body) => POST("/api/questions/clear", body || {}),
    },

    // ---------- Worldline ----------
    worldline: {
      list: () => GET("/api/worldline/variables"),
      set: (body) => POST("/api/worldline/variable", body),
      remove: (body) => POST("/api/worldline/variable/remove", body),
    },

    // ---------- Memories ----------
    memories: {
      list: (q) => GET("/api/memories", q),
    },
  };

  // Generic EventSource opener (for plain SSE pulls; chat uses sseStream with POST).
  function openEventSource(url, handlers) {
    handlers = handlers || {};
    const ev = new EventSource(url, { withCredentials: true });
    ev.onmessage = (e) => {
      let d = e.data; try { d = JSON.parse(d); } catch (_) {}
      handlers.onEvent && handlers.onEvent({ event: "message", data: d });
      handlers.on_message && handlers.on_message(d);
    };
    ev.addEventListener("done", (e) => { handlers.on_done && handlers.on_done(e.data); ev.close(); });
    ev.addEventListener("error", (e) => { handlers.on_error && handlers.on_error(e); });
    return ev;
  }

  // ============================================================
  //  TOAST + ERROR HELPERS (used by buttons)
  // ============================================================
  function toast(msg, opts) {
    if (typeof window.toast === "function") return window.toast(msg, opts);
    if (opts && opts.kind === "danger") console.warn("[toast.danger]", msg, opts);
    else console.log("[toast]", msg, opts);
  }
  window.__apiToast = toast;

  async function withToast(promise, okMsg, failMsg) {
    try {
      const r = await promise;
      if (okMsg) toast(okMsg, { kind: "ok", duration: 1800 });
      return r;
    } catch (e) {
      const detail = (e && (e.message || (e.payload && e.payload.detail))) || "未知错误";
      toast(failMsg || "请求失败", { kind: "danger", detail, duration: 3600 });
      throw e;
    }
  }
  window.withToast = withToast;

  window.api = api;
  window.dispatchEvent(new CustomEvent("api-ready", { detail: { base: BASE } }));
})();
