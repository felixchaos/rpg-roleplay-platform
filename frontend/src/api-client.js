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
      register: (body) => POST("/api/v1/auth/register", body),
      login: (body) => POST("/api/v1/auth/login", body),
      logout: () => POST("/api/v1/auth/logout", {}),
      me: () => GET("/api/v1/auth/me"),
      // Frontend wishlist – mapped to new endpoints we will add.
      changePassword: (body) => POST("/api/v1/auth/password", body),
      loginHistory: () => GET("/api/v1/auth/login-history"),
      sessionsList: () => GET("/api/v1/auth/sessions"),
      sessionsRevoke: (sid) => POST("/api/v1/auth/sessions/revoke", { session_id: sid }),
      smsCode: (phone) => POST("/api/v1/auth/sms-code", { phone }),
      smsVerify: (body) => POST("/api/v1/auth/sms-verify", body),
      revokeAllSessions: () => POST("/api/v1/auth/sessions/revoke-all", {}),
    },

    // ---------- Account / profile ----------
    account: {
      profile: () => GET("/api/v1/me/profile"),
      saveProfile: (body) => POST("/api/v1/profile", body),
      avatar: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send("/api/v1/profile/avatar", { method: "POST", body: fd });
      },
      // task 50：BE 有 avatar reset 但 FE 没 wrapper（直接 raw POST 也行，加 wrapper 更清晰）
      avatarReset: () => POST("/api/v1/profile/avatar/reset", {}),
      visibility: (body) => POST("/api/v1/profile/visibility", body),
      exportData: (body) => POST("/api/v1/account/export", body || {}),
      deactivate: () => POST("/api/v1/account/deactivate", {}),
      deleteAccount: (body) => POST("/api/v1/account/delete", body || {}),
      usage: (days) => GET("/api/v1/me/usage", days ? { days } : undefined),
      usageTimeline: (days, group_by) => GET("/api/v1/me/usage/timeline", { days: days || 30, group_by: group_by || "day" }),
      stats: () => GET("/api/v1/me/stats"),
      preferences: (body) => POST("/api/v1/me/preference", body),
      personas: {
        list: () => GET("/api/v1/me/personas"),
        get: (id) => GET("/api/v1/me/personas/" + encodeURIComponent(id)),
        upsert: (body) => POST("/api/v1/me/personas", body),
        remove: (id) => POST("/api/v1/me/personas/" + encodeURIComponent(id) + "/delete", {}),
      },
    },

    // ---------- Platform meta ----------
    platform: {
      info: () => GET("/api/v1/platform"),
      settings: () => GET("/api/v1/settings"),
      saveSetting: (body) => POST("/api/v1/settings", body),
      commands: () => GET("/api/v1/platform/commands"),
      search: (q, scope) => GET("/api/v1/search", { q, scope }),
    },

    // ---------- Scripts ----------
    scripts: {
      list: () => GET("/api/v1/scripts"),
      preview: (body) => POST("/api/v1/scripts/preview", body),
      importScript: (body) => POST("/api/v1/scripts/import", body),
      delete: (sid) => POST("/api/v1/scripts/" + sid + "/delete", {}),
      chapters: (sid, q) => GET("/api/v1/scripts/" + sid + "/chapters", q),
      updateChapter: (sid, idx, body) => POST(`/api/v1/scripts/${sid}/chapters/${idx}`, body),
      mergeChapter: (sid, body) => POST(`/api/v1/scripts/${sid}/chapters/merge`, body),
      splitChapter: (sid, idx, body) => POST(`/api/v1/scripts/${sid}/chapters/${idx}/split`, body),
      resplit: (sid, body) => POST(`/api/v1/scripts/${sid}/resplit`, body),
      chapterFacts: (sid, q) => GET(`/api/v1/scripts/${sid}/chapter-facts`, q),
      worldbook: (sid) => GET(`/api/v1/scripts/${sid}/worldbook`),
      knowledgeSync: (sid, body) => POST(`/api/v1/scripts/${sid}/knowledge/sync`, body || {}),
      importStatus: (sid) => GET(`/api/v1/scripts/${sid}/import-status`),
      importBudget: (sid, body) => POST(`/api/v1/scripts/${sid}/import-budget`, body || {}),
      importPipeline: (sid, body) => POST(`/api/v1/scripts/${sid}/import-pipeline`, body || {}),
      jobStatus: (jobId) => GET("/api/v1/scripts/import-jobs/" + jobId),
      jobCancel: (jobId) => POST("/api/v1/scripts/import-jobs/" + jobId + "/cancel", {}),
      myJobs: () => GET("/api/v1/me/import-jobs"),
      // SSE stream for live import progress
      streamImport: (jobId, handlers) => {
        const url = BASE + "/api/v1/scripts/import-jobs/" + jobId + "/stream";
        return openEventSource(url, handlers);
      },
      // B1: download script pack zip — GET /api/v1/scripts/{id}/export-pack → blob download
      exportPack: async (sid, filename) => {
        const url = (BASE || "") + "/api/v1/scripts/" + sid + "/export-pack";
        const res = await fetch(url, { credentials: "include" });
        if (!res.ok) {
          let msg = res.statusText;
          try { const j = await res.json(); msg = j.detail || j.error || msg; } catch (_) {}
          throw new ApiError("http", res.status, msg);
        }
        const blob = await res.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = filename || "script_pack.zip";
        document.body.appendChild(a);
        a.click();
        setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
      },
    },

    // ---------- Saves & branches ----------
    saves: {
      list: () => GET("/api/v1/saves"),
      create: (body) => POST("/api/v1/saves", body),
      detail: (sid) => GET("/api/v1/saves/" + sid),
      contextRuns: (sid, q) => GET("/api/v1/saves/" + sid + "/context-runs", q),
      // task 50：BE 早就有这些 endpoint 但 FE 一直没 wrapper
      rename: (sid, title) => POST("/api/v1/saves/" + sid + "/rename", { title }),
      remove: (sid) => POST("/api/v1/saves/" + sid + "/delete", {}),
      activate: (sid) => POST("/api/v1/saves/" + sid + "/activate", {}),
      exportUrl: (sid) => BASE + "/api/v1/saves/" + sid + "/export",
      importFile: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send("/api/v1/saves/import", { method: "POST", body: fd });
      },
    },
    branches: {
      list: (saveId) => GET("/api/v1/branches/" + saveId),
      continueFrom: (body) => POST("/api/v1/branches/continue", body),
      activate: (body) => POST("/api/v1/branches/activate", body),
      delete: (body) => POST("/api/v1/branches/delete", body),
      // task 116c: 软回滚 — 删除 message 及之后所有
      rollbackToMessage: (saveId, messageIndex) =>
        POST("/api/v1/branches/rollback", { save_id: saveId, message_index: messageIndex }),
    },

    // ---------- 5E-compatible Rules (Ash Mine 等原创模组) ----------
    // 内部 ruleset id "dnd5e"；对外文案 "5E compatible / 五版规则兼容"。
    // 不引入官方 D&D 商标或非 SRD IP。
    rules: {
      modules: () => GET("/api/v1/rules/modules"),
      // 低层原语：mutate 当前 save 加载模组。日常用 launchModule 建独立存档。
      startModule: (moduleId, character) => POST("/api/v1/rules/module/start", { module_id: moduleId, character }),
      // 标准入口：建独立 save + 激活 + 加载模组 一步完成，不污染当前 save。
      launchModule: (moduleId, opts) => POST("/api/v1/rules/module/launch", {
        module_id: moduleId, character: (opts || {}).character, title: (opts || {}).title,
      }),
      scene: () => GET("/api/v1/rules/scene"),
      move: (to) => POST("/api/v1/rules/move", { to }),
      action: (body) => POST("/api/v1/rules/action", body),
      encounterStart: (encounterId, seed) => POST("/api/v1/rules/encounter/start", { encounter_id: encounterId, seed }),
      encounterNext: () => POST("/api/v1/rules/encounter/next", {}),
      encounterEnemy: (attackerId, targetId, seed) => POST("/api/v1/rules/encounter/enemy", {
        attacker_id: attackerId, target_id: targetId || "player", seed,
      }),
      suggest: (text) => POST("/api/v1/rules/suggest", { text }),
    },

    // ---------- Character cards (user + script) ----------
    cards: {
      myList: () => GET("/api/v1/me/character-cards"),
      myGet: (id) => GET("/api/v1/me/character-cards/" + id),
      myUpsert: (body) => POST("/api/v1/me/character-cards", body),
      myDelete: (id) => POST("/api/v1/me/character-cards/" + id + "/delete", {}),
      importTavern: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send("/api/v1/me/character-cards/import-tavern", { method: "POST", body: fd });
      },
      // task 50：BE 有 import-json 但 FE 没 wrapper
      importJson: (body) => POST("/api/v1/me/character-cards/import-json", body),
      exportTavern: (id) => BASE + "/api/v1/me/character-cards/" + id + "/export-tavern",
      exportPng: (id) => BASE + "/api/v1/me/character-cards/" + id + "/export-png",
      // Script-scoped (NPCs/world cards)
      scriptList: (sid) => GET("/api/v1/scripts/" + sid + "/character-cards"),
      scriptGet: (sid, cid) => GET("/api/v1/scripts/" + sid + "/character-cards/" + cid),
      scriptUpsert: (sid, body) => POST("/api/v1/scripts/" + sid + "/character-cards", body),
      scriptDelete: (sid, cid) => POST("/api/v1/scripts/" + sid + "/character-cards/" + cid + "/delete", {}),
      scriptEnabled: (sid, cid, on) => POST("/api/v1/scripts/" + sid + "/character-cards/" + cid + "/enabled", { enabled: !!on }),
    },

    // ---------- Library / files ----------
    library: {
      list: (q) => GET("/api/v1/library", q),
      upload: (file, path) => {
        const fd = new FormData();
        fd.append("file", file);
        if (path) fd.append("path", path);
        return _send("/api/v1/library/upload", { method: "POST", body: fd });
      },
      mkdir: (body) => POST("/api/v1/library/mkdir", body),
      delete: (body) => POST("/api/v1/library/delete", body),
      downloadUrl: (path) => BASE + "/api/v1/library/download?path=" + encodeURIComponent(path),
    },

    // ---------- Uploads (chunked) ----------
    // task 17: 后端 /api/uploads/init 要 {filename, total_bytes, total_chunks}（不是 size/chunk_size）。
    // 后端 /api/uploads/{id}/chunk 要 JSON {chunk_index, base64}（不是 multipart）。
    // 这里把 chunk 重写成读 Blob → base64 → JSON POST。
    uploads: {
      init: (body) => POST("/api/v1/uploads/init", body),
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
        return POST("/api/v1/uploads/" + id + "/chunk", { chunk_index: Number(index) || 0, base64 });
      },
      finish: (id, body) => POST("/api/v1/uploads/" + id + "/finish", body || {}),
      cancel: (id) => POST("/api/v1/uploads/" + id + "/cancel", {}),
    },

    // ---------- Credentials (per-user API keys) ----------
    credentials: {
      list: () => GET("/api/v1/me/credentials"),
      set: (body) => POST("/api/v1/me/credentials", body),
      remove: (body) => POST("/api/v1/me/credentials/delete", body),
      test: (q) => GET("/api/v1/me/credentials/test", q),
    },

    // ---------- Models & APIs ----------
    models: {
      list: () => GET("/api/v1/models"),
      select: (body) => POST("/api/v1/models/select", body),
      upsertApi: (body) => POST("/api/v1/models/api", body),
      upsertModel: (body) => POST("/api/v1/models/model", body),
      deleteModel: (body) => POST("/api/v1/models/model/delete", body),
      visibility: (body) => POST("/api/v1/models/visibility", body),
      validate: (body) => POST("/api/v1/models/validate", body),
      remote: (q) => GET("/api/v1/models/remote", q),
      diff: (q) => GET("/api/v1/models/diff", q),
      probe: (body) => POST("/api/v1/models/probe", body),
      pricing: () => GET("/api/v1/models/pricing"),
      report: (q) => GET("/api/v1/models/report", q),
      capabilities: () => GET("/api/v1/models/capabilities"),
      capabilityLabels: () => GET("/api/v1/models/capabilities/labels"),
    },

    // ---------- Tools / MCP / Skills ----------
    tools: {
      list: () => GET("/api/v1/tools"),
    },
    mcp: {
      upsert: (body) => POST("/api/v1/mcp/server", body),
      enabled: (body) => POST("/api/v1/mcp/server/enabled", body),
      remove: (body) => POST("/api/v1/mcp/server/delete", body),
      validate: (body) => POST("/api/v1/mcp/server/validate", body),
      start: (body) => POST("/api/v1/mcp/server/start", body),
      stop: (body) => POST("/api/v1/mcp/server/stop", body),
      runtime: () => GET("/api/v1/mcp/runtime"),
      tools: () => GET("/api/v1/mcp/tools"),
      call: (body) => POST("/api/v1/mcp/tool/call", body),
    },
    skills: {
      list: () => GET("/api/v1/skills"),
      run: (skillId, body) => POST("/api/v1/skills/" + encodeURIComponent(skillId) + "/run", body || {}),
      importPack: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send("/api/v1/skills/import", { method: "POST", body: fd });
      },
    },
    // task 50：plugins 列表 (BE 已有，FE 之前没 wrapper)
    plugins: {
      list: () => GET("/api/v1/plugins"),
    },

    // ---------- In-game state / chat ----------
    game: {
      state: () => GET("/api/v1/state"),
      newGame: (body) => POST("/api/v1/new", body || {}),
      saveGame: () => POST("/api/v1/save", {}),
      stop: () => POST("/api/v1/stop", {}),
      // SSE: opening / chat
      // task 88: 包一层让 worldbook_consulting/ready 自动 dispatch CustomEvent,
      // 任何 UI 监听 window.addEventListener("rpg-worldbook-status", ...) 即可。
      opening: (body, handlers) => sseStream("/api/v1/opening", body || {}, _wbHook(handlers)),
      chat: (body, handlers) => sseStream("/api/v1/chat", body || {}, _wbHook(handlers)),
      chatEstimate: (body) => POST("/api/v1/chat/estimate", body),
      memoryMode: (mode) => POST("/api/v1/memory/mode", { mode }),
      memoryAdd: (body) => POST("/api/v1/memory/add", body),
      memoryRemove: (body) => POST("/api/v1/memory/remove", body),
      permissions: (body) => POST("/api/v1/permissions", body),
      pendingWrite: (body) => POST("/api/v1/permissions/pending-write", body),
      clearQuestions: (body) => POST("/api/v1/questions/clear", body || {}),
    },

    // ---------- Worldline ----------
    worldline: {
      list: () => GET("/api/v1/worldline/variables"),
      set: (body) => POST("/api/v1/worldline/variable", body),
      remove: (body) => POST("/api/v1/worldline/variable/remove", body),
    },

    // ---------- Memories ----------
    memories: {
      list: (q) => GET("/api/v1/memories", q),
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
