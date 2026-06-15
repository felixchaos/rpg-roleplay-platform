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

  // API prefix constant: overridable via <meta name="api-prefix" content="/api/v1" />
  const API_PREFIX = document.querySelector('meta[name="api-prefix"]')?.content || '/api/v1';

  // Base URL: either same-origin (when served by FastAPI) or
  // local backend (when opened as file:// or via static server).
  function detectBase() {
    try {
      // 1. meta tag 优先 — 生产部署可直接设 content="https://api.example.com"
      const metaBase = document.querySelector('meta[name="api-base"]')?.content;
      if (metaBase != null) return metaBase;
      if (location.protocol === "file:") return "http://127.0.0.1:7860";
      // If we're already on the FastAPI port → same origin.
      if (location.port === "7860") return "";
      // vite dev server 已把 /api 代理到后端(见 vite.config.js),走同源代理可避开
      // 跨域 + Cookie(SameSite)问题。用 vite 注入的 HMR client 脚本判定是否在 vite 下,
      // 比猜端口可靠;生产构建无此脚本 → 走原逻辑。(来自 PR #14)
      if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
        if (document.querySelector('script[type="module"][src*="/@vite/client"]')) return "";
        // 静态 dev server(如 python -m http.server)另起端口 → 跨域直连后端。
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

  // One-shot guard: prevent redirect loop when a 401 fires on a page
  // that is itself mid-flight (e.g. Login.html calling /api/auth/me).
  // 设计预览 / 离线模式(?offline):401 不跳登录页,让 mock UI 正常渲染。
  let _AUTH_REDIRECT_ARMED = (() => {
    try { return !new URLSearchParams(location.search).has("offline"); } catch (_) { return true; }
  })();

  // ---- core fetch helpers ------------------------------------
  function timeoutSignal(ms) {
    return AbortSignal.timeout(ms);
  }

  async function _send(path, opts) {
    const url = (path.startsWith("http") ? path : BASE + path);
    // Default 15 s timeout; caller may pass opts.signal to override.
    const defaultSignal = timeoutSignal(15000);
    const init = Object.assign(
      {
        credentials: "include",
        headers: { "Accept": "application/json" },
        signal: defaultSignal,
      },
      opts || {}
    );
    // opts.signal (if supplied) already overwrote the default above via Object.assign.
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
      const msg = (payload && payload.detail) || (payload && payload.error) || res.statusText || `请求失败 (HTTP ${res.status})`;

      // 401 — session expired; redirect once, then still throw so caller knows.
      if (res.status === 401) {
        if (_AUTH_REDIRECT_ARMED && !location.pathname.endsWith("Login.html")) {
          _AUTH_REDIRECT_ARMED = false;
          try {
            window.dispatchEvent(new CustomEvent("rpg-auth-expired"));
          } catch (_) {}
          location.replace("Login.html?next=" + encodeURIComponent(location.pathname + location.search + location.hash));
        }
        // Fall through: throw ApiError so callers that catch can handle it too.
      }

      // 429 — rate limited; read Retry-After and surface to user.
      if (res.status === 429) {
        const retryAfter = res.headers.get("Retry-After");
        const detail = retryAfter ? ("请求过于频繁，请 " + retryAfter + " 秒后重试") : "请求过于频繁，请稍后重试";
        try { toast(detail, { kind: "warn", duration: 4000 }); } catch (_) {}
        throw new ApiError(payload && payload.code, res.status, detail, payload);
      }

      // 503 — service unavailable.
      if (res.status === 503) {
        try { toast("服务暂不可用", { kind: "danger", duration: 3600 }); } catch (_) {}
      }

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
      // telemetry hook:每个 ApiError 构造时回调,让 runtime-telemetry 记录失败 API
      // (旧实现 monkey-patch window.ApiError 无效——throw 用的是这里的闭包类,不是全局槽位)
      try {
        if (typeof window !== "undefined" && typeof window.__onApiError === "function") {
          window.__onApiError(this);
        }
      } catch (_) {}
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
  const POST = (path, body, opts) => _send(path, Object.assign({ method: "POST", body: body || {} }, opts || {}));
  const PATCH = (path, body, opts) => _send(path, Object.assign({ method: "PATCH", body: body || {} }, opts || {}));
  const PUT = (path, body, opts) => _send(path, Object.assign({ method: "PUT", body: body || {} }, opts || {}));
  const DEL = (path, body, opts) => _send(path, Object.assign({ method: "DELETE", body: body || {} }, opts || {}));

  // 凭据变更后广播 → 模型选择器(AgentModelPicker / 游戏台 ModelPopover)即时重拉,
  // 修 issue #22:换/删 API Key 后模型列表仍显示旧 key 的模型。
  const _emitCredsUpdated = (r) => {
    try { if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("rpg-credentials-updated")); } catch (_) {}
    return r;
  };

  // ---- SSE helper for /api/chat & /api/opening ---------------
  // Posts a JSON body and parses the streaming response into
  // structured event objects: { event, data }.
  function sseStream(path, body, handlers) {
    handlers = handlers || {};
    const url = (path.startsWith("http") ? path : BASE + path);
    const ctl = new AbortController();
    const isAbort = (e) => ctl.signal.aborted || (e && e.name === "AbortError");
    const abortPayload = (e) => ({
      reason: ctl.signal.reason || null,
      message: (e && e.message) || "请求已取消",
      url,
    });
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
        if (isAbort(e)) {
          if (handlers.onAbort) handlers.onAbort(abortPayload(e));
          return;
        }
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
        try { chunk = await reader.read(); } catch (e) {
          if (isAbort(e)) {
            if (handlers.onAbort) handlers.onAbort(abortPayload(e));
            return;
          }
          if (handlers.onError) handlers.onError(new ApiError("stream_read", 0, (e && e.message) || "流式读取失败", { url }));
          return;
        }
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
    return {
      stop: (reason) => {
        try { ctl.abort(reason || "client_stop"); } catch (_) { ctl.abort(); }
      },
      done: promise,
      signal: ctl.signal,
    };
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
      register: (body) => POST(`${API_PREFIX}/auth/register`, body),
      login: (body) => POST(`${API_PREFIX}/auth/login`, body),
      loginCodeRequest: (body) => POST(`${API_PREFIX}/auth/login-code/request`, body),
      loginCodeVerify: (body) => POST(`${API_PREFIX}/auth/login-code/verify`, body),
      logout: () => POST(`${API_PREFIX}/auth/logout`, {}),
      me: () => GET(`${API_PREFIX}/auth/me`),
      // Frontend wishlist – mapped to new endpoints we will add.
      changePassword: (body) => POST(`${API_PREFIX}/auth/password`, body),
      loginHistory: () => GET(`${API_PREFIX}/auth/login-history`),
      sessionsList: () => GET(`${API_PREFIX}/auth/sessions`),
      sessionsRevoke: (sid) => POST(`${API_PREFIX}/auth/sessions/revoke`, { session_id: sid }),
      smsCode: (phone) => POST(`${API_PREFIX}/auth/sms-code`, { phone }),
      smsVerify: (body) => POST(`${API_PREFIX}/auth/sms-verify`, body),
      revokeAllSessions: () => POST(`${API_PREFIX}/auth/sessions/revoke-all`, {}),
    },

    // ---------- Account / profile ----------
    account: {
      profile: () => GET(`${API_PREFIX}/me/profile`),
      saveProfile: (body) => POST(`${API_PREFIX}/profile`, body),
      avatar: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send(`${API_PREFIX}/profile/avatar`, { method: "POST", body: fd });
      },
      // task 50：BE 有 avatar reset 但 FE 没 wrapper（直接 raw POST 也行，加 wrapper 更清晰）
      avatarReset: () => POST(`${API_PREFIX}/profile/avatar/reset`, {}),
      // MediaStudio 图库：从已有资产 URL 设个人头像（不重新上传）
      setAvatarUrl: (url) => POST(`/api/profile/avatar-url`, { url }),
      visibility: (body) => POST(`${API_PREFIX}/profile/visibility`, body),
      exportData: (body) => POST(`${API_PREFIX}/account/export`, body || {}),
      // 账号数据迁移(免部署 → 本地):聚合剧本/存档/角色卡/偏好为单个 zip
      migrateEstimate: () => GET(`${API_PREFIX}/me/account/export/estimate`),
      migrateExportUrl: (includeChunks) => BASE + `${API_PREFIX}/me/account/export` + (includeChunks ? "?include_chunks=1" : ""),
      migrateImport: (file) => {
        const fd = new FormData(); fd.append("file", file);
        // 多剧本账号包串行恢复,可能数十秒~分钟级 → 给 10 分钟,避免默认 15s 误中断。
        return _send(`${API_PREFIX}/me/account/import`, { method: "POST", body: fd, signal: timeoutSignal(600000) });
      },
      deactivate: () => POST(`${API_PREFIX}/account/deactivate`, {}),
      deleteAccount: (body) => POST(`${API_PREFIX}/account/delete`, body || {}),
      usage: (days) => GET(`${API_PREFIX}/me/usage`, days ? { days } : undefined),
      usageTimeline: (days, group_by) => GET(`${API_PREFIX}/me/usage/timeline`, { days: days || 30, group_by: group_by || "day" }),
      stats: () => GET(`${API_PREFIX}/me/stats`),
      activity: (limit) => GET(`${API_PREFIX}/me/activity`, limit ? { limit } : undefined),
      // 成就(见 docs/design/I_achievements.md)
      achievements: () => GET(`${API_PREFIX}/me/achievements`),
      achievementsSeen: () => POST(`${API_PREFIX}/me/achievements/seen`, {}),
      achievementsCatalog: () => GET(`/api/achievements`),  // 公开目录(匿名可拉)
      publicWall: (username) => GET(`/api/u/${encodeURIComponent(username)}/achievements`),  // 公开成就墙
      preferences: (body) => POST(`${API_PREFIX}/me/preference`, body),
      gmStyleSchema: () => GET(`${API_PREFIX}/gm-style/schema`),
      getGmStyle: () => GET(`${API_PREFIX}/me/gm-style`),
      setGmStyle: (gm_style) => POST(`${API_PREFIX}/me/gm-style`, { gm_style }),
      personas: {
        list: () => GET(`${API_PREFIX}/me/personas`),
        get: (id) => GET(`${API_PREFIX}/me/personas/` + encodeURIComponent(id)),
        upsert: (body) => POST(`${API_PREFIX}/me/personas`, body),
        remove: (id) => POST(`${API_PREFIX}/me/personas/` + encodeURIComponent(id) + "/delete", {}),
      },
      // 账户注销流程 (30 天宽限期)
      requestDelete: () => POST(`/api/account/request-delete`, {}),
      cancelDelete: () => POST(`/api/account/cancel-delete`, {}),
      deleteStatus: () => GET(`/api/account/delete-status`),
    },

    // ---------- Platform meta ----------
    platform: {
      info: () => GET(`${API_PREFIX}/platform`),
      settings: () => GET(`${API_PREFIX}/settings`),
      saveSetting: (body) => POST(`${API_PREFIX}/settings`, body),
      commands: () => GET(`${API_PREFIX}/platform/commands`),
      search: (q, scope) => GET(`${API_PREFIX}/search`, { q, scope }),
    },

    // ---------- Admin ----------
    admin: {
      deploymentConfig: () => GET(`${API_PREFIX}/admin/deployment-config`),
      saveDeploymentConfig: (body) => POST(`${API_PREFIX}/admin/deployment-config`, body),
      smtpTest: () => POST(`${API_PREFIX}/admin/smtp/test`, {}),
      // 用户管理
      users: (params) => GET(`${API_PREFIX}/admin/users`, params),
      updateUser: (id, body) => PATCH(`${API_PREFIX}/admin/users/${id}`, body),
      deactivateUser: (id) => POST(`${API_PREFIX}/admin/users/${id}/deactivate`, {}),
      reactivateUser: (id) => POST(`${API_PREFIX}/admin/users/${id}/reactivate`, {}),
      forceLogout: (id) => POST(`${API_PREFIX}/admin/users/${id}/force-logout`, {}),
      // 全局用量
      globalUsage: (params) => GET(`${API_PREFIX}/admin/usage`, params),
      // 审计日志
      auditLog: (params) => GET(`${API_PREFIX}/admin/audit`, params),
      // 系统健康
      health: () => GET(`${API_PREFIX}/admin/health`),
      // 系统日志
      logs: (params) => GET(`${API_PREFIX}/admin/logs`, params),
      // 注册与邀请
      registration: () => GET(`${API_PREFIX}/admin/registration`),
      saveRegistration: (body) => POST(`${API_PREFIX}/admin/registration`, body),
      inviteCodes: (params) => GET(`${API_PREFIX}/admin/invite-codes`, params),
      createInviteCodes: (body) => POST(`${API_PREFIX}/admin/invite-codes`, body),
      deleteInviteCode: (code) => POST(`${API_PREFIX}/admin/invite-codes/${encodeURIComponent(code)}/delete`, {}),
      // 安全配置
      securityConfig: () => GET(`${API_PREFIX}/admin/security-config`),
      saveSecurityConfig: (body) => POST(`${API_PREFIX}/admin/security-config`, body),
      // 维护模式
      maintenance: () => GET(`${API_PREFIX}/admin/maintenance`),
      saveMaintenance: (body) => POST(`${API_PREFIX}/admin/maintenance`, body),
      // 服务重启
      restart: () => POST(`${API_PREFIX}/admin/restart`, {}),
      // 成就目录管理(见 docs/design/I_achievements.md)
      achievements: {
        list: () => GET(`/api/admin/achievements`),
        create: (body) => POST(`/api/admin/achievements`, body),
        update: (id, body) => PUT(`/api/admin/achievements/${encodeURIComponent(id)}`, body),
        remove: (id) => DEL(`/api/admin/achievements/${encodeURIComponent(id)}`),
      },
      // DMCA Takedowns
      dmcaTakedowns: {
        list: ({ status, limit } = {}) => GET(`/api/admin/dmca/takedowns`, { status: status || 'open', limit: limit || 50 }),
        create: (body) => POST(`/api/admin/dmca/takedowns`, body),
        action: (id, body) => POST(`/api/admin/dmca/takedowns/${id}/action`, body),
        counter: (id, body) => POST(`/api/admin/dmca/takedowns/${id}/counter`, body),
      },
      // DMCA Strikes
      dmcaStrikes: {
        list: () => GET(`/api/admin/dmca/strikes`),
        increment: (userId, body) => POST(`/api/admin/dmca/strikes/${userId}/increment`, body),
      },
      // CSAM Reports
      csamReports: {
        list: ({ status } = {}) => GET(`/api/admin/csam/reports`, status ? { status } : undefined),
        decision: (id, body) => POST(`/api/admin/csam/reports/${id}/decision`, body),
      },
      // AUP 用户操作 (suspend / unsuspend / terminate)
      suspendUser: (userId, body) => POST(`/api/admin/users/${userId}/suspend`, body),
      unsuspendUser: (userId) => POST(`/api/admin/users/${userId}/unsuspend`, {}),
      terminateUser: (userId, body) => POST(`/api/admin/users/${userId}/terminate`, body),
      // Feedback 管理
      feedback: {
        list: ({ status } = {}) => GET(`/api/admin/feedback`, status ? { status } : undefined),
        decision: (id, body) => POST(`/api/admin/feedback/${id}/decision`, body),
      },
      // Policy notices
      policy: {
        notices: () => GET(`/api/admin/policy/notices`),
        dispatch: (id) => POST(`/api/admin/policy/notices/${id}/dispatch`, {}),
        activate: (id) => POST(`/api/admin/policy/notices/${id}/activate`, {}),
      },
    },

    // ---------- Scripts ----------
    scripts: {
      list: () => GET(`${API_PREFIX}/scripts`),
      preview: (body) => POST(`${API_PREFIX}/scripts/preview`, body, { signal: timeoutSignal(90000) }),
      importScript: (body) => POST(`${API_PREFIX}/scripts/import`, body, { signal: timeoutSignal(90000) }),
      delete: (sid, body = {}) => POST(`${API_PREFIX}/scripts/` + sid + "/delete", body),
      unsubscribe: (sid) => POST(`${API_PREFIX}/scripts/` + sid + "/unsubscribe", {}),
      chapters: (sid, q) => GET(`${API_PREFIX}/scripts/` + sid + "/chapters", q),
      // 单章节完整 content(列表只回 180-char preview;点入章节后 lazy 拉真正文)
      chapterDetail: (sid, idx) => GET(`${API_PREFIX}/scripts/${sid}/chapters/${idx}`),
      updateChapter: (sid, idx, body) => POST(`${API_PREFIX}/scripts/${sid}/chapters/${idx}`, body),
      mergeChapter: (sid, body) => POST(`${API_PREFIX}/scripts/${sid}/chapters/merge`, body),
      splitChapter: (sid, idx, body) => POST(`${API_PREFIX}/scripts/${sid}/chapters/${idx}/split`, body),
      resplit: (sid, body) => POST(`${API_PREFIX}/scripts/${sid}/resplit`, body),
      chapterFacts: (sid, q) => GET(`${API_PREFIX}/scripts/${sid}/chapter-facts`, q),
      worldbook: (sid, q) => GET(`${API_PREFIX}/scripts/${sid}/worldbook`, q),
      worldbookCreate: (sid, body) => POST(`${API_PREFIX}/scripts/${sid}/worldbook`, body),
      worldbookUpdate: (sid, eid, body) => PUT(`${API_PREFIX}/scripts/${sid}/worldbook/${eid}`, body),
      worldbookDelete: (sid, eid) => DEL(`${API_PREFIX}/scripts/${sid}/worldbook/${eid}`, {}),
      // 批量:body={entry_ids:[...], action:'delete'|'enable'|'disable'|'set_priority', priority?}
      worldbookBatch: (sid, body) => POST(`${API_PREFIX}/scripts/${sid}/worldbook/batch`, body),
      // canon 实体(MD 编辑器):list/get 读 + upsert(有 logical_key → PUT,否则 POST)/delete
      canonList: (sid) => GET(`${API_PREFIX}/scripts/${sid}/canon-entities`),
      canonGet: (sid, key) => GET(`${API_PREFIX}/scripts/${sid}/canon-entities/${encodeURIComponent(key)}`),
      canonUpsert: (sid, body) => (body && body.logical_key
        ? PUT(`${API_PREFIX}/scripts/${sid}/canon-entities/${encodeURIComponent(body.logical_key)}`, body)
        : POST(`${API_PREFIX}/scripts/${sid}/canon-entities`, body)),
      canonDelete: (sid, key) => DEL(`${API_PREFIX}/scripts/${sid}/canon-entities/${encodeURIComponent(key)}`, {}),
      // 出生点(玩家选择从哪个章节起场)
      birthpoints: (sid) => GET(`${API_PREFIX}/scripts/${sid}/birthpoints`),
      // 真实剧本时间线锚点(script_timeline_anchors,LLM 抽出的 story-time 段,
      // 之前 typo 调成了 birthpoints,因此时间线 tab 永远空)
      timeline: (sid) => GET(`${API_PREFIX}/scripts/${sid}/timeline`),
      // 时间线锚点(MD 编辑器):update/create/delete(读取走 timeline()）
      anchorUpdate: (sid, anchorId, body) => PUT(`${API_PREFIX}/scripts/${sid}/anchors/${anchorId}`, body),
      anchorCreate: (sid, body) => POST(`${API_PREFIX}/scripts/${sid}/anchors`, body),
      anchorDelete: (sid, anchorId) => DEL(`${API_PREFIX}/scripts/${sid}/anchors/${anchorId}`, {}),
      // knowledgeSync / importBudget 前端不再调用:
      // - knowledge/sync 是后端 import 内部 fallback,不该 UI 触发
      // - import-budget 字段格式跟前端不兼容,Wizard 用本地 preview 估算
      // 保留后端路由(internal use),前端 wrapper 删掉防止再被误用
      importStatus: (sid) => GET(`${API_PREFIX}/scripts/${sid}/import-status`),
      // 切走又切回 tab 时复活 extract 进度面板:最近一条 import_job + active 标志
      activeJob: (sid) => GET(`${API_PREFIX}/scripts/${sid}/active-job`),
      importPipeline: (sid, body) => POST(`${API_PREFIX}/scripts/${sid}/import-pipeline`, body || {}),
      // LLM 知识提取(异步 job,复用 import-jobs SSE / streamImport)
      llmExtract: (sid, body) => POST(`${API_PREFIX}/scripts/${sid}/llm-extract`, body || {}),
      llmExtractEstimate: (sid, body) => POST(`${API_PREFIX}/scripts/${sid}/llm-extract/estimate`, body || {}),
      llmExtractUsage: (sid, days) => GET(`${API_PREFIX}/scripts/${sid}/llm-extract/usage`, days ? { days } : undefined),
      jobStatus: (jobId) => GET(`${API_PREFIX}/scripts/import-jobs/` + jobId),
      jobCancel: (jobId) => POST(`${API_PREFIX}/scripts/import-jobs/` + jobId + "/cancel", {}),
      myJobs: () => GET(`${API_PREFIX}/me/import-jobs`),
      // SSE stream for live import progress
      streamImport: (jobId, handlers) => {
        const url = BASE + `${API_PREFIX}/scripts/import-jobs/` + jobId + "/stream";
        return openEventSource(url, handlers);
      },
      // 别名:重建/重做某剧本的完整流水线 — 内部走同一 endpoint /import-jobs/{id}/stream,
      // wizard 这边语义上叫 "rebuild"。
      streamRebuild: (jobId, handlers) => {
        const url = BASE + `${API_PREFIX}/scripts/import-jobs/` + jobId + "/stream";
        return openEventSource(url, handlers);
      },
      // B3: script overrides CRUD (JSONB)
      getOverrides: (sid) => GET(`${API_PREFIX}/scripts/` + sid + "/overrides"),
      saveOverrides: (sid, data) => POST(`${API_PREFIX}/scripts/` + sid + "/overrides", { data }),
      getGmStyle: (sid) => GET(`${API_PREFIX}/scripts/` + sid + "/gm-style"),
      setGmStyle: (sid, gm_style) => POST(`${API_PREFIX}/scripts/` + sid + "/gm-style", { gm_style }),
      // B2: upload script pack zip — POST /api/v1/scripts/import-pack multipart
      importPack: (file) => {
        const fd = new FormData();
        fd.append("file", file);
        return _send(`${API_PREFIX}/scripts/import-pack`, { method: "POST", body: fd });
      },
      // B1: download script pack zip — GET /api/v1/scripts/{id}/export-pack → blob download
      exportPack: async (sid, filename) => {
        const url = (BASE || "") + `${API_PREFIX}/scripts/` + sid + "/export-pack";
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
      // 在线剧本库:公开分享 / 浏览 / 导入
      setVisibility: (sid, isPublic) => POST(`${API_PREFIX}/scripts/` + sid + "/visibility", { is_public: !!isPublic }),
      publicList: (q) => GET(`${API_PREFIX}/scripts/public`, q),
      publicGet: (sid) => GET(`${API_PREFIX}/scripts/public/` + sid),
      // task 74: 公开剧本「导入」改 O(1) subscribe — 几毫秒 INSERT,默认 15s timeout 够
      cloneFromPublic: (sid) => POST(`${API_PREFIX}/scripts/public/` + sid + "/clone", {}, {}),
      // task 74: 「另存为可编辑副本」走旧 clone 全表复制(30-60s,给 3 分钟 timeout)
      forkFromPublic: (sid) => POST(`${API_PREFIX}/scripts/public/` + sid + "/fork", {}, { signal: timeoutSignal(180000) }),
      // phase_rebuild_panel: 模块级重做矩阵
      // - getModulesStatus 拿 7 模块 + active_job 快照
      // - rebuildEstimate 估算 (零 LLM 也走,统一 prereq 检查)
      // - rebuild 派发任务,返回 job_id (复用 import_jobs 表, kind='rebuild_<module>')
      // - rebuildEmbeddings 单独路由,body.include = ['chunks'|'cards'|'worldbook'|'canon']
      // - streamRebuild = streamImport 别名,保留命名一致性
      getModulesStatus: (sid) => GET(`${API_PREFIX}/scripts/` + sid + "/modules-status"),
      rebuildEstimate: (sid, mod, body) => POST(`${API_PREFIX}/scripts/` + sid + "/rebuild/" + mod + "/estimate", body || {}),
      rebuild: (sid, mod, body) => POST(`${API_PREFIX}/scripts/` + sid + "/rebuild/" + mod, body || {}),
      rebuildEmbeddings: (sid, body) => POST(`${API_PREFIX}/scripts/` + sid + "/rebuild/embeddings", body || {}),
      streamRebuild(jobId, handlers) {
        // 别名: rebuild jobs 跟 import-jobs 同一张表 / 同一 SSE 路由
        return this.streamImport(jobId, handlers);
      },
      // W3-C1: 手动上传剧本封面 → POST multipart /api/scripts/{id}/cover → {ok, url}
      uploadCover: (id, file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send(`/api/scripts/` + id + "/cover", { method: "POST", body: fd });
      },
      // MediaStudio 图库：从已有资产 URL 设封面（不重新上传）
      setCoverUrl: (id, url) => POST(`/api/scripts/` + id + "/cover-url", { url }),
      // v44: Git 模式版本控制 / fork / 共享剧本同步
      fork: (sid, body) => POST(`${API_PREFIX}/scripts/` + sid + "/fork", body || {}),
      commits: (sid, q) => GET(`${API_PREFIX}/scripts/` + sid + "/commits", q),
      pin: (sid, body) => POST(`${API_PREFIX}/scripts/` + sid + "/pin", body || {}),
      unpin: (sid) => POST(`${API_PREFIX}/scripts/` + sid + "/unpin", {}),
      checkout: (sid, commitId) => POST(`${API_PREFIX}/scripts/${sid}/checkout/${commitId}`, {}),
    },

    // ---------- 功能 B:本地↔在线剧本库联邦 ----------
    federation: {
      // 本实例是否为在线库提供方(server 模式开;本地客户端关)→ 决定是否展示 /device、令牌管理
      providerInfo: () => GET(`${API_PREFIX}/ext/provider-info`),
      // 在线提供方:PAT 管理(本服务签发给外部客户端)
      patList: () => GET(`${API_PREFIX}/me/pat`),
      patCreate: (body) => POST(`${API_PREFIX}/me/pat`, body),
      patRevoke: (id) => POST(`${API_PREFIX}/me/pat/` + id + "/revoke", {}),
      // 在线提供方:设备码批准页(登录用户在浏览器批准外部客户端)
      deviceLookup: (userCode) => GET(`${API_PREFIX}/me/device/lookup`, { user_code: userCode }),
      deviceApprove: (userCode, deny) => POST(`${API_PREFIX}/me/device/approve`, { user_code: userCode, deny: !!deny }),
      // 本地客户端:连接器(指向在线服务)
      connectorGet: () => GET(`${API_PREFIX}/me/library-connector`),
      connectorSet: (base_url, token) => POST(`${API_PREFIX}/me/library-connector`, { base_url, token }),
      connectorTest: () => POST(`${API_PREFIX}/me/library-connector/test`, {}),
      connectorScripts: (q) => GET(`${API_PREFIX}/me/library-connector/scripts`, q ? { q } : undefined),
      // 整包下载/上传 + DB 恢复,可能数十秒 → 给 3 分钟,避免默认 15s 误中断。
      connectorImport: (remote_script_id) => POST(`${API_PREFIX}/me/library-connector/import`, { remote_script_id }, { signal: timeoutSignal(180000) }),
      connectorPublish: (script_id) => POST(`${API_PREFIX}/me/library-connector/publish`, { script_id }, { signal: timeoutSignal(180000) }),
      // 本地客户端:设备码流(引导用户在浏览器授权在线服务)
      deviceStart: (base_url, scopes) => POST(`${API_PREFIX}/me/library-connector/device/start`, { base_url, scopes }),
      devicePoll: (base_url, device_code) => POST(`${API_PREFIX}/me/library-connector/device/poll`, { base_url, device_code }),
    },

    // ---------- Saves & branches ----------
    saves: {
      list: () => GET(`${API_PREFIX}/saves`),
      create: (body) => POST(`${API_PREFIX}/saves`, body),
      detail: (sid) => GET(`${API_PREFIX}/saves/` + sid),
      contextRuns: (sid, q) => GET(`${API_PREFIX}/saves/` + sid + "/context-runs", q),
      // task 50：BE 早就有这些 endpoint 但 FE 一直没 wrapper
      rename: (sid, title) => POST(`${API_PREFIX}/saves/` + sid + "/rename", { title }),
      remove: (sid) => POST(`${API_PREFIX}/saves/` + sid + "/delete", {}),
      activate: (sid) => POST(`${API_PREFIX}/saves/` + sid + "/activate", {}),
      exportUrl: (sid) => BASE + `${API_PREFIX}/saves/` + sid + "/export",
      importFile: (file) => {
        const fd = new FormData(); fd.append("file", file);
        // 自包含 bundle(.zip)导入要整包恢复,可能超默认 15s → 给 3 分钟。
        return _send(`${API_PREFIX}/saves/import`, { method: "POST", body: fd, signal: timeoutSignal(180000) });
      },
    },
    branches: {
      list: (saveId) => GET(`${API_PREFIX}/branches/` + saveId),
      continueFrom: (body) => POST(`${API_PREFIX}/branches/continue`, body),
      activate: (body) => POST(`${API_PREFIX}/branches/activate`, body),
      delete: (body) => POST(`${API_PREFIX}/branches/delete`, body),
      // task 116c: 软回滚 — 删除 message 及之后所有
      rollbackToMessage: (saveId, messageIndex) =>
        POST(`${API_PREFIX}/branches/rollback`, { save_id: saveId, message_index: messageIndex }),
    },

    // ---------- 5E-compatible Rules (Ash Mine 等原创模组) ----------
    // 内部 ruleset id "dnd5e"；对外文案 "5E compatible / 五版规则兼容"。
    // 不引入官方 D&D 商标或非 SRD IP。
    rules: {
      modules: () => GET(`${API_PREFIX}/rules/modules`),
      // 低层原语：mutate 当前 save 加载模组。日常用 launchModule 建独立存档。
      startModule: (moduleId, character) => POST(`${API_PREFIX}/rules/module/start`, { module_id: moduleId, character }),
      // 标准入口：建独立 save + 激活 + 加载模组 一步完成，不污染当前 save。
      launchModule: (moduleId, opts) => POST(`${API_PREFIX}/rules/module/launch`, {
        module_id: moduleId, character: (opts || {}).character, title: (opts || {}).title,
      }),
      scene: () => GET(`${API_PREFIX}/rules/scene`),
      move: (to) => POST(`${API_PREFIX}/rules/move`, { to }),
      action: (body) => POST(`${API_PREFIX}/rules/action`, body),
      encounterStart: (encounterId, seed) => POST(`${API_PREFIX}/rules/encounter/start`, { encounter_id: encounterId, seed }),
      encounterNext: () => POST(`${API_PREFIX}/rules/encounter/next`, {}),
      encounterEnemy: (attackerId, targetId, seed) => POST(`${API_PREFIX}/rules/encounter/enemy`, {
        attacker_id: attackerId, target_id: targetId || "player", seed,
      }),
      suggest: (text) => POST(`${API_PREFIX}/rules/suggest`, { text }),
    },

    // ---------- Character cards (user + script) ----------
    cards: {
      myList: () => GET(`${API_PREFIX}/me/character-cards`),
      myGet: (id) => GET(`${API_PREFIX}/me/character-cards/` + id),
      myUpsert: (body) => POST(`${API_PREFIX}/me/character-cards`, body),
      myDelete: (id) => POST(`${API_PREFIX}/me/character-cards/` + id + "/delete", {}),
      // 在线角色卡库:发布/取消公开自己的卡 · 浏览公开卡 · 完整克隆进自己卡库
      setPublic: (id, isPublic) => POST(`${API_PREFIX}/me/character-cards/` + id + "/visibility", { public: !!isPublic }),
      publicList: (q) => GET(`${API_PREFIX}/cards/public`, q),
      cloneFromPublic: (id) => POST(`${API_PREFIX}/cards/public/` + id + "/clone", {}),
      importTavern: (file, opts = {}) => {
        const fd = new FormData(); fd.append("file", file);
        if (opts.aiSplit) fd.append("ai_split", "true");
        return _send(`${API_PREFIX}/me/character-cards/import-tavern`, { method: "POST", body: fd });
      },
      // task 50：BE 有 import-json 但 FE 没 wrapper
      importJson: (body) => POST(`${API_PREFIX}/me/character-cards/import-json`, body),
      exportTavern: (id) => BASE + `${API_PREFIX}/me/character-cards/` + id + "/export-tavern",
      exportPng: (id) => BASE + `${API_PREFIX}/me/character-cards/` + id + "/export-png",
      // Script-scoped (NPCs/world cards)
      scriptList: (sid) => GET(`${API_PREFIX}/scripts/` + sid + "/character-cards"),
      scriptGet: (sid, cid) => GET(`${API_PREFIX}/scripts/` + sid + "/character-cards/" + cid),
      scriptUpsert: (sid, body) => POST(`${API_PREFIX}/scripts/` + sid + "/character-cards", body),
      scriptDelete: (sid, cid) => POST(`${API_PREFIX}/scripts/` + sid + "/character-cards/" + cid + "/delete", {}),
      scriptEnabled: (sid, cid, on) => POST(`${API_PREFIX}/scripts/` + sid + "/character-cards/" + cid + "/enabled", { enabled: !!on }),
      // 手动指定剧本主角(清其它卡主角标记 + 锁定不被重新提取覆盖)。仅 owner。
      scriptSetProtagonist: (sid, cid) => POST(`${API_PREFIX}/scripts/` + sid + "/character-cards/" + cid + "/protagonist", {}),
      // 按需 AI 复核全部 NPC 卡(合并同人/锁定主角/删非人名)。model 由公用选择器传入(可空,后端读偏好兜底)。
      auditCards: (sid, api_id, model) => POST(`${API_PREFIX}/scripts/` + sid + "/audit-cards", { api_id, model }),
      // Phase 4 — 人设图自动维护(persona/pc 类卡)
      // POST /api/me/character-cards/{id}/auto-image-sync  {enabled}
      personaAutoSync: (id, enabled) => POST(`${API_PREFIX}/me/character-cards/` + id + "/auto-image-sync", { enabled: !!enabled }),
      // POST /api/me/character-cards/{id}/generate-persona-image  → {image_id, status}
      personaGenerate: (id) => POST(`${API_PREFIX}/me/character-cards/` + id + "/generate-persona-image", {}),
      // GET  /api/me/character-cards/{id}/persona-images  → [{id, image_url, source, is_current, created_at, persona_hash, status}]
      personaImages: (id) => GET(`${API_PREFIX}/me/character-cards/` + id + "/persona-images"),
      // POST /api/me/character-cards/{id}/persona-images/{image_id}/set-current  → {ok}
      personaSetCurrent: (id, imageId) => POST(`${API_PREFIX}/me/character-cards/` + id + "/persona-images/" + imageId + "/set-current", {}),
      // W3-C1: 手动上传头像 → POST multipart /api/me/character-cards/{id}/avatar → {ok, url}
      uploadAvatar: (id, file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send(`${API_PREFIX}/me/character-cards/` + id + "/avatar", { method: "POST", body: fd });
      },
      // W3-C1: 手动上传人设图 → POST multipart /api/me/character-cards/{id}/persona-images/upload → {ok, url}
      uploadPersonaImage: (id, file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send(`${API_PREFIX}/me/character-cards/` + id + "/persona-images/upload", { method: "POST", body: fd });
      },
      // MediaStudio 图库：从已有资产 URL 设头像（不重新上传）
      setAvatarUrl: (id, url) => POST(`${API_PREFIX}/me/character-cards/` + id + "/avatar-url", { url }),
      // MediaStudio 图库：从已有资产 URL 设人设图（插 card_persona_images + 设为 current）
      setPersonaImageUrl: (id, url) => POST(`${API_PREFIX}/me/character-cards/` + id + "/persona-images/url", { url }),
      // NPC 角色卡头像（剧本 owner 管）：上传 / 从图库 URL 设置
      scriptUploadCardAvatar: (sid, cid, file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send(`${API_PREFIX}/scripts/` + sid + "/character-cards/" + cid + "/avatar", { method: "POST", body: fd });
      },
      scriptSetCardAvatarUrl: (sid, cid, url) => POST(`${API_PREFIX}/scripts/` + sid + "/character-cards/" + cid + "/avatar-url", { url }),
    },

    // ---------- Chat history (SillyTavern JSONL import) ----------
    chats: {
      importTavern: (body) => POST(`${API_PREFIX}/me/chats/import-tavern`, body),
    },

    // ---------- Tavern mode (SillyTavern-style 1:1 character chat) ----------
    // 注意:酒馆端点挂在 /api/tavern/*(无 /v1 前缀),与上面的 ${API_PREFIX} 不同。
    // 流式发送复用现有 api.game.chat({message, save_id}) + api.game.stop()。
    tavern: {
      // 活跃对话列表(updated_at desc)
      list: () => GET(`/api/tavern/chats`),
      // 归档对话列表
      listArchived: () => GET(`/api/tavern/chats`, { archived: 1 }),
      // 用一张已有 pc 卡建对话 body {character_card_id, persona_card_id?, title?}
      create: (body) => POST(`/api/tavern/chats`, body),
      // 导入酒馆角色卡:File(.png/.json/.webp)→ multipart;否则 JSON body
      // ({json}/{json_string}/{base64}/{png_base64}) → 建+激活对话
      importCharacter: (fileOrBody) => {
        if (fileOrBody instanceof File || fileOrBody instanceof Blob) {
          const fd = new FormData();
          fd.append("file", fileOrBody);
          return _send(`/api/tavern/import-character`, { method: "POST", body: fd, signal: timeoutSignal(60000) });
        }
        return POST(`/api/tavern/import-character`, fileOrBody || {});
      },
      // 激活某对话(切换对话前必须先激活,/api/chat 才会落到正确的 save)
      activate: (id) => POST(`/api/tavern/chats/${id}/activate`, {}),
      // 归档 / 取消归档 body {archived: bool}
      archive: (id, archived) => PATCH(`/api/tavern/chats/${id}/archive`, { archived: !!archived }),
      // 重命名 body {title}
      rename: (id, title) => POST(`/api/tavern/chats/${id}/rename`, { title }),
      // F#3:编辑本对话系统提示词
      setSystemPrompt: (id, sp) => POST(`/api/tavern/chats/${id}/system-prompt`, { system_prompt: sp }),
      // 类 Claude:按对话内容自动生成标题(后端幂等,仅 title 为空时生成)
      autotitle: (id) => POST(`/api/tavern/chats/${id}/autotitle`, {}),
      // 删除对话
      remove: (id) => DEL(`/api/tavern/chats/${id}`, {}),
      // 导入 SillyTavern 聊天记录 JSONL:File → multipart;否则 {jsonl, title?}
      importJsonl: (fileOrBody, title) => {
        if (fileOrBody instanceof File || fileOrBody instanceof Blob) {
          const fd = new FormData();
          fd.append("file", fileOrBody);
          if (title) fd.append("title", title);
          return _send(`/api/tavern/chats/import-jsonl`, { method: "POST", body: fd, signal: timeoutSignal(60000) });
        }
        if (typeof fileOrBody === "string") {
          return POST(`/api/tavern/chats/import-jsonl`, { jsonl: fileOrBody, title: title || undefined });
        }
        return POST(`/api/tavern/chats/import-jsonl`, fileOrBody || {});
      },
      // 导出对话为 JSONL(返回可直接下载的 URL,后端响应是 attachment)
      exportJsonl: (id) => BASE + `/api/tavern/chats/${id}/export-jsonl`,
    },

    // ---------- Library / files ----------
    // W3-C1: S5 文件库在线服务化(只读管理,不支持手动上传)。
    // GET /api/library?kind=X → {items:[{id,kind,url,source,ref_kind,ref_id,size,created_at,...}]}
    // GET /api/library/asset/{id} → 单个资产(owner 校验)
    // GET /api/library/asset/{id}/download → 带 Content-Disposition 的下载
    // POST /api/library/asset/{id}/delete {confirm:true} → 删除(关联检查由后端做)
    library: {
      list: (kind) => GET(`/api/library`, kind ? { kind } : undefined),
      get: (id) => GET(`/api/library/asset/` + encodeURIComponent(id)),
      downloadUrl: (id) => BASE + `/api/library/asset/` + encodeURIComponent(id) + `/download`,
      deleteAsset: (id, confirm) => {
        const fd_body = { confirm: confirm === undefined ? true : !!confirm };
        return _send(`/api/library/asset/` + encodeURIComponent(id) + `/delete`, { method: "POST", body: fd_body });
      },
      // 旧接口保留(内部用,别再从 UI 调)
      _legacyUpload: (file, path) => {
        const fd = new FormData();
        fd.append("file", file);
        if (path) fd.append("path", path);
        return _send(`${API_PREFIX}/library/upload`, { method: "POST", body: fd });
      },
      _legacyMkdir: (body) => POST(`${API_PREFIX}/library/mkdir`, body),
      _legacyDelete: (body) => POST(`${API_PREFIX}/library/delete`, body),
      _legacyDownloadUrl: (path) => BASE + `${API_PREFIX}/library/download?path=` + encodeURIComponent(path),
    },

    // ---------- Uploads (chunked) ----------
    // task 17: 后端 /api/uploads/init 要 {filename, total_bytes, total_chunks}（不是 size/chunk_size）。
    // 后端 /api/uploads/{id}/chunk 要 JSON {chunk_index, base64}（不是 multipart）。
    // 这里把 chunk 重写成读 Blob → base64 → JSON POST。
    uploads: {
      init: (body) => POST(`${API_PREFIX}/uploads/init`, body, { signal: timeoutSignal(30000) }),
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
        return POST(`${API_PREFIX}/uploads/` + id + "/chunk", { chunk_index: Number(index) || 0, base64 }, { signal: timeoutSignal(60000) });
      },
      finish: (id, body) => POST(`${API_PREFIX}/uploads/` + id + "/finish", body || {}, { signal: timeoutSignal(60000) }),
      cancel: (id) => POST(`${API_PREFIX}/uploads/` + id + "/cancel", {}),
    },

    // ---------- Credentials (per-user API keys) ----------
    credentials: {
      list: () => GET(`${API_PREFIX}/me/credentials`),
      set: (body) => POST(`${API_PREFIX}/me/credentials`, body).then(_emitCredsUpdated),
      remove: (body) => POST(`${API_PREFIX}/me/credentials/delete`, body).then(_emitCredsUpdated),
      test: (q) => GET(`${API_PREFIX}/me/credentials/test`, q),
    },

    // ---------- Models & APIs ----------
    models: {
      // GET /api/models — 主入口；返回 {ok, models:{apis:[...]}, selected:{...}}
      list: () => GET(`${API_PREFIX}/models`),
      // GET /api/models/catalog — 同源别名（Phase 0 由 Agent C 添加），返回完全相同 payload。
      // ModelPicker.jsx 改用 list()；catalog() 保留供兼容老调用方。
      catalog: () => GET(`${API_PREFIX}/models/catalog`),
      // 强制重拉所有 provider live /models,清 TTL cache
      refresh: () => POST(`${API_PREFIX}/models/refresh`, {}),
      select: (body) => POST(`${API_PREFIX}/models/select`, body),
      upsertApi: (body) => POST(`${API_PREFIX}/models/api`, body),
      upsertModel: (body) => POST(`${API_PREFIX}/models/model`, body),
      deleteModel: (body) => POST(`${API_PREFIX}/models/model/delete`, body),
      visibility: (body) => POST(`${API_PREFIX}/models/visibility`, body),
      // per-user:隐藏/显示自己同步来的(overlay)模型,任何用户可调,且 re-sync 不重置。
      meVisibility: (body) => POST(`${API_PREFIX}/me/models/visibility`, body),
      validate: (body) => POST(`${API_PREFIX}/models/validate`, body),
      remote: (q) => GET(`${API_PREFIX}/models/remote`, q),
      syncRemote: (body) => POST(`${API_PREFIX}/models/remote/sync`, body),
      diff: (q) => GET(`${API_PREFIX}/models/diff`, q),
      probe: (body) => POST(`${API_PREFIX}/models/probe`, body),
      pricing: () => GET(`${API_PREFIX}/models/pricing`),
      report: (q) => GET(`${API_PREFIX}/models/report`, q),
      capabilities: () => GET(`${API_PREFIX}/models/capabilities`),
      capabilityLabels: () => GET(`${API_PREFIX}/models/capabilities/labels`),
    },

    // ---------- Images (AI 生图, Phase 1/2) ----------
    // POST /api/images/generate → {image_id, status:'pending'}
    // GET  /api/images/{id}    → {id, status, url, error, kind}
    // GET  /api/images/file/{name} → FileResponse(静态文件)
    // GET  /api/images/list?save_id=X → [{id,url,kind,prompt,status,created_at}]
    images: {
      generate: (body) => POST(`/api/images/generate`, body),
      get: (id) => GET(`/api/images/` + encodeURIComponent(id)),
      file: (name) => BASE + `/api/images/file/` + encodeURIComponent(name),
      list: (saveId) => GET(`/api/images/list?save_id=` + encodeURIComponent(saveId)),
      cancel: (id) => POST(`/api/images/` + encodeURIComponent(id) + `/cancel`, {}),
    },
    tasks: {
      // 全局后台任务浮窗数据源:本人进行中 + 最近刚结束的后台任务(导入/各模块重建/生图)
      active: () => GET(`/api/me/tasks/active`),
    },

    // ---------- Tools / MCP / Skills ----------
    tools: {
      list: () => GET(`${API_PREFIX}/tools`),
    },
    mcp: {
      upsert: (body) => POST(`${API_PREFIX}/mcp/server`, body),
      enabled: (body) => POST(`${API_PREFIX}/mcp/server/enabled`, body),
      remove: (body) => POST(`${API_PREFIX}/mcp/server/delete`, body),
      validate: (body) => POST(`${API_PREFIX}/mcp/server/validate`, body),
      start: (body) => POST(`${API_PREFIX}/mcp/server/start`, body),
      stop: (body) => POST(`${API_PREFIX}/mcp/server/stop`, body),
      runtime: () => GET(`${API_PREFIX}/mcp/runtime`),
      tools: () => GET(`${API_PREFIX}/mcp/tools`),
      call: (body) => POST(`${API_PREFIX}/mcp/tool/call`, body),
    },
    skills: {
      list: () => GET(`${API_PREFIX}/skills`),
      run: (skillId, body) => POST(`${API_PREFIX}/skills/` + encodeURIComponent(skillId) + "/run", body || {}),
      importPack: (file) => {
        const fd = new FormData(); fd.append("file", file);
        return _send(`${API_PREFIX}/skills/import`, { method: "POST", body: fd });
      },
    },
    // task 50：plugins 列表 (BE 已有，FE 之前没 wrapper)
    plugins: {
      list: () => GET(`${API_PREFIX}/plugins`),
    },

    // ---------- In-game state / chat ----------
    game: {
      state: () => GET(`${API_PREFIX}/state`),
      newGame: (body) => POST(`${API_PREFIX}/new`, body || {}),
      saveGame: () => POST(`${API_PREFIX}/save`, {}),
      stop: () => POST(`${API_PREFIX}/stop`, {}),
      // SSE: opening / chat
      // task 88: 包一层让 worldbook_consulting/ready 自动 dispatch CustomEvent,
      // 任何 UI 监听 window.addEventListener("rpg-worldbook-status", ...) 即可。
      opening: (body, handlers) => sseStream(`${API_PREFIX}/opening`, body || {}, _wbHook(handlers)),
      chat: (body, handlers) => sseStream(`${API_PREFIX}/chat`, body || {}, _wbHook(handlers)),
      chatEstimate: (body) => POST(`${API_PREFIX}/chat/estimate`, body),
      contextBreakdown: () => GET(`${API_PREFIX}/chat/context-breakdown`),
      memoryMode: (mode) => POST(`${API_PREFIX}/memory/mode`, { mode }),
      memoryAdd: (body) => POST(`${API_PREFIX}/memory/add`, body),
      memoryRemove: (body) => POST(`${API_PREFIX}/memory/remove`, body),
      permissions: (body) => POST(`${API_PREFIX}/permissions`, body),
      pendingWrite: (body) => POST(`${API_PREFIX}/permissions/pending-write`, body),
      clearQuestions: (body) => POST(`${API_PREFIX}/questions/clear`, body || {}),
      // 侧栏 inline-edit(运行时状态镜像 + 用户直接修改)
      relationshipSet: (body) => POST(`${API_PREFIX}/relationships/set`, body),
      relationshipDelete: (body) => POST(`${API_PREFIX}/relationships/delete`, body),
      worldSet: (body) => POST(`${API_PREFIX}/world/set`, body),
    },

    // ---------- Worldline ----------
    worldline: {
      list: () => GET(`${API_PREFIX}/worldline/variables`),
      set: (body) => POST(`${API_PREFIX}/worldline/variable`, body),
      remove: (body) => POST(`${API_PREFIX}/worldline/variable/remove`, body),
    },

    // ---------- Memories ----------
    memories: {
      list: (q) => GET(`${API_PREFIX}/memories`, q),
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

  // 别名:部分组件(GmStyleEditor 等)用 window.api.me.* 访问「我的账户」端点,
  // 而账户方法定义在 api.account 命名空间。补这条别名,二者等价,避免 undefined 报错。
  if (!api.me) api.me = api.account;
  window.api = api;
  window.dispatchEvent(new CustomEvent("api-ready", { detail: { base: BASE } }));
})();
