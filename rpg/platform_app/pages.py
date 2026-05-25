PLATFORM_HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>RPG Platform</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #100f0d;
      --panel: #191815;
      --panel-2: #22201d;
      --panel-3: #2a2723;
      --line: #3c3833;
      --line-soft: #2c2925;
      --text: #f2eee8;
      --muted: #aaa29a;
      --muted-2: #7d766f;
      --accent: #e17842;
      --accent-soft: rgba(225, 120, 66, .16);
      --ok: #7ccf8a;
      --warn: #e1ba66;
      --danger: #d86b5f;
      --shadow: 0 18px 60px rgba(0, 0, 0, .28);
    }
    * { box-sizing: border-box; }
    html, body, #app { min-height: 100%; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.55 ui-sans-serif, -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
      letter-spacing: 0;
    }
    button, input, textarea, select { font: inherit; color: inherit; }
    button {
      min-height: 34px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--panel-2);
      padding: 0 12px;
      cursor: pointer;
    }
    button:hover { border-color: #5a544d; background: var(--panel-3); }
    button.primary { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 800; }
    button.ghost { background: transparent; }
    button.danger { color: #ffd2cc; border-color: rgba(216,107,95,.45); background: rgba(216,107,95,.08); }
    button:disabled { opacity: .45; cursor: default; }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--panel-2);
      padding: 9px 10px;
      outline: 0;
    }
    textarea { min-height: 104px; resize: vertical; }
    label { display: grid; gap: 6px; color: var(--muted); }
    label > span { font-size: 12px; text-transform: uppercase; color: var(--muted-2); }
    a { color: var(--accent); text-decoration: none; }
    code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    pre { overflow: auto; border: 1px solid var(--line); border-radius: 8px; background: #0b0a09; padding: 12px; }
    .shell { display: grid; grid-template-columns: 268px minmax(0, 1fr); min-height: 100vh; }
    .sidebar {
      position: sticky;
      top: 0;
      height: 100vh;
      border-right: 1px solid var(--line);
      background: var(--panel);
      padding: 18px 14px;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 18px;
    }
    .brand { display: flex; gap: 10px; align-items: center; padding: 0 4px; }
    .mark {
      width: 36px;
      height: 36px;
      display: grid;
      place-items: center;
      border: 1px solid rgba(225,120,66,.45);
      border-radius: 8px;
      background: var(--accent-soft);
      font-weight: 900;
    }
    .brand strong { display: block; font-size: 16px; }
    .brand span, .muted { color: var(--muted); }
    .nav { display: grid; align-content: start; gap: 5px; overflow: auto; }
    .nav button {
      display: grid;
      grid-template-columns: 28px 1fr;
      align-items: center;
      gap: 10px;
      text-align: left;
      background: transparent;
      border-color: transparent;
    }
    .nav-icon {
      width: 22px;
      height: 22px;
      display: grid;
      place-items: center;
      color: var(--muted);
    }
    .nav-icon svg { width: 19px; height: 19px; display: block; stroke-width: 2; }
    .nav button.active .nav-icon { color: var(--accent); }
    .nav button.active { background: var(--panel-2); border-color: var(--line); }
    .sidebar-foot { display: grid; gap: 8px; color: var(--muted); font-size: 12px; }
    .main { min-width: 0; display: grid; grid-template-rows: auto 1fr; }
    .topbar {
      position: sticky;
      top: 0;
      z-index: 3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--line-soft);
      background: rgba(16,15,13,.92);
      backdrop-filter: blur(14px);
      padding: 14px 22px;
    }
    .topbar h1 { margin: 0; font-size: 20px; line-height: 1.2; }
    .topbar p { margin: 3px 0 0; color: var(--muted); }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .content { padding: 22px; overflow: auto; }
    .section { display: grid; gap: 16px; width: min(100%, 1480px); }
    .panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 16px;
    }
	    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
	    .import-grid { display: grid; grid-template-columns: minmax(220px, .8fr) minmax(180px, .45fr) minmax(260px, 1fr); gap: 12px; align-items: end; }
	    .split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(320px, .68fr); gap: 12px; }
    .list { display: grid; gap: 8px; }
    .item {
      display: grid;
      gap: 7px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 14px;
    }
    .item-head { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
    .item h2, .panel h2 { margin: 0; font-size: 16px; overflow-wrap: anywhere; }
    .item p, .panel p { margin: 0; }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 0 9px;
      color: var(--muted);
      white-space: nowrap;
    }
    .pill.ok { color: var(--ok); border-color: rgba(124,207,138,.35); background: rgba(124,207,138,.08); }
    .pill.warn { color: var(--warn); border-color: rgba(225,186,102,.35); background: rgba(225,186,102,.08); }
    table { width: 100%; border-collapse: collapse; }
    td, th { border-bottom: 1px solid var(--line-soft); padding: 10px 8px; text-align: left; vertical-align: middle; }
    th { color: var(--muted); font-weight: 600; }
    .data-table { table-layout: fixed; }
    .data-table th:first-child, .data-table td:first-child { width: 34%; }
    .data-table th:last-child, .data-table td:last-child { width: 180px; }
    .title-cell { display: grid; gap: 4px; min-width: 0; }
    .title-cell strong { overflow-wrap: anywhere; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: var(--muted); overflow-wrap: anywhere; }
	    .small { font-size: 12px; color: var(--muted); }
	    .nowrap { white-space: nowrap; }
    .empty { border: 1px dashed var(--line); border-radius: 8px; padding: 26px; color: var(--muted); text-align: center; }
    .auth-wrap {
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
    }
    .auth {
      width: min(440px, 100%);
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel);
      padding: 22px;
      box-shadow: var(--shadow);
      display: grid;
      gap: 14px;
    }
    .auth h1 { margin: 0; font-size: 26px; }
    .tabs { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; padding: 4px; background: #0c0b0a; }
    .tabs button { border: 0; min-height: 32px; background: transparent; }
    .tabs button.active { background: var(--panel-3); }
    .form { display: grid; gap: 11px; }
    .tree-wrap { overflow: auto; min-height: 620px; border: 1px solid var(--line); border-radius: 8px; background: #0b0a09; }
    svg.branch { min-width: 100%; min-height: 720px; display: block; }
    .edge { stroke: #57514a; stroke-width: 2; fill: none; }
    .node rect { fill: #1c1a17; stroke: #47423c; rx: 8; }
    .node.root rect { stroke: var(--accent); }
    .node.branch rect { stroke: var(--warn); }
    .node text { fill: var(--text); font-size: 12px; }
    .node .summary { fill: var(--text); font-size: 13px; font-weight: 800; }
    .node .sub { fill: var(--muted); }
    .node-btn { font: 12px ui-sans-serif, -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif; }
    .node-actions { display: flex; gap: 6px; }
    .node-actions button { min-height: 30px; padding: 0 10px; }
    .breadcrumb { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; color: var(--muted); }
    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      z-index: 10;
      max-width: 420px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-2);
      box-shadow: var(--shadow);
      padding: 12px 14px;
    }
    .hidden { display: none !important; }
    @media (max-width: 920px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar { position: static; height: auto; }
	      .nav { grid-template-columns: repeat(2, minmax(0, 1fr)); }
	      .split { grid-template-columns: 1fr; }
	      .import-grid { grid-template-columns: 1fr; }
	      .topbar { align-items: flex-start; flex-direction: column; }
	    }
  </style>
</head>
<body>
  <div id="app"></div>
  <div id="toast" class="toast hidden"></div>
  <script>
    const API = "/api/v1";
    const routes = [
	      ["profile", "主页", "user"],
	      ["shelf", "剧本", "book"],
      ["saves", "开始游戏", "play"],
      ["branches", "分支", "branch"],
      ["library", "库", "folder"],
      ["settings", "设置", "settings"],
      ["plugins", "插件", "plug"],
      ["mcp", "MCP", "diamond"],
      ["skills", "Skill", "spark"],
      ["apis", "API", "braces"],
    ];
    const state = {
      page: routePage(),
      authMode: "login",
      data: null,
      branch: null,
      branchSaveId: null,
      library: null,
      libraryPath: "",
      loading: false,
    };

    function routePage() {
      const last = location.pathname.split("/").filter(Boolean).pop();
      return routes.some(([id]) => id === last) ? last : "profile";
    }
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
	    const fmtBytes = (n) => {
      const value = Number(n || 0);
      if (value < 1024) return `${value} B`;
      if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
	      return `${(value / 1024 / 1024).toFixed(1)} MB`;
	    };
	    const fmtCount = (n) => Number(n || 0).toLocaleString("zh-CN");
	    const uid = (item) => item?.uid || item?.public_id || item?.id || "";
    const shortUid = (value) => String(value || "").replace(/-/g, "").slice(0, 10);
    const clip = (value, limit = 24) => {
      const text = String(value ?? "").replace(/\s+/g, " ").trim();
      return text.length <= limit ? text : text.slice(0, limit);
    };
    const branchSummary = (node) => clip(node.summary || node.content_preview || node.title || "空回合", 24);
    const branchMeta = (node) => `${node.role === "round" ? "完整回合" : node.role} · ${Number(node.source_node_ids?.length || 1)} 条消息`;

    function iconSvg(name) {
      const common = `viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"`;
      const icons = {
        user: `<circle cx="12" cy="8" r="4"/><path d="M4 20c1.8-4 5-6 8-6s6.2 2 8 6"/>`,
        book: `<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v18H6.5A2.5 2.5 0 0 1 4 18.5z"/><path d="M8 3v18"/>`,
        play: `<path d="M8 5v14l11-7z"/>`,
        branch: `<path d="M6 4v7a5 5 0 0 0 5 5h7"/><path d="M14 12l4 4-4 4"/><circle cx="6" cy="4" r="2"/><circle cx="18" cy="16" r="2"/>`,
        folder: `<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>`,
        settings: `<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>`,
        plug: `<path d="M9 7V3M15 7V3M7 7h10v5a5 5 0 0 1-10 0z"/><path d="M12 17v4"/>`,
        diamond: `<path d="M12 3 21 12 12 21 3 12z"/><path d="M12 8v8M8 12h8"/>`,
        spark: `<path d="M12 3l1.6 5.1L19 10l-5.4 1.9L12 17l-1.6-5.1L5 10l5.4-1.9z"/><path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>`,
        braces: `<path d="M8 4H6a2 2 0 0 0-2 2v3a2 2 0 0 1-2 2 2 2 0 0 1 2 2v3a2 2 0 0 0 2 2h2"/><path d="M16 4h2a2 2 0 0 1 2 2v3a2 2 0 0 0 2 2 2 2 0 0 0-2 2v3a2 2 0 0 1-2 2h-2"/>`,
      };
      return `<svg ${common}>${icons[name] || icons.user}</svg>`;
    }

    async function api(path, options = {}) {
      const headers = { ...(options.headers || {}) };
      if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
      const res = await fetch(path.startsWith("/api") ? path : API + path, { ...options, headers });
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok || data.ok === false) throw new Error(data.error || data.detail || res.statusText);
      return data;
    }
    function notify(message) {
      const el = document.getElementById("toast");
      el.textContent = message;
      el.classList.remove("hidden");
      clearTimeout(window.__toastTimer);
      window.__toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
    }
    function setLoading(value) { state.loading = value; render(); }

    async function loadPlatform() {
      try {
        state.data = await api("/platform");
      } catch (error) {
        state.data = { user: null, error: error.message };
      }
      render();
    }
    function go(page) {
      history.pushState(null, "", `/app/${page}`);
      state.page = page;
      render();
      if (page === "branches") loadBranch();
      if (page === "library") loadLibrary(state.libraryPath || "");
    }
    function render() {
      const root = document.getElementById("app");
      if (!state.data) { root.innerHTML = `<div class="auth-wrap"><div class="auth"><h1>载入中</h1></div></div>`; return; }
      if (!state.data.user) { root.innerHTML = authView(); return; }
      const views = { profile, shelf, saves, branches, library, settings, plugins, mcp, skills, apis };
      root.innerHTML = shell((views[state.page] || profile)());
    }
    function shell(inner) {
      const user = state.data.user;
      const db = state.data.database || {};
      const label = routes.find(([id]) => id === state.page)?.[1] || "平台";
      return `
        <div class="shell">
          <aside class="sidebar">
            <div class="brand">
              <div class="mark">R</div>
              <div><strong>柏林 RPG</strong><span>Platform · ${esc(db.driver || "PostgreSQL")}</span></div>
            </div>
            <nav class="nav">${routes.map(([id, text, icon]) => `<button class="${state.page === id ? "active" : ""}" onclick="go('${id}')"><span class="nav-icon">${iconSvg(icon)}</span><strong>${text}</strong></button>`).join("")}</nav>
            <div class="sidebar-foot">
              <span>${esc(user.display_name)} · ${esc(user.role)}</span>
              <span>DB ${db.ok ? "online" : "offline"} · API v${esc(state.data.meta?.api_version || "1")}</span>
              <a href="/">返回游戏界面</a>
            </div>
          </aside>
          <section class="main">
            <header class="topbar">
              <div><h1>${esc(label)}</h1><p>${subtitle(label)}</p></div>
              <div class="toolbar"><button onclick="reload()">刷新</button><button onclick="logout()">退出</button></div>
            </header>
            <main class="content">${inner}</main>
          </section>
        </div>`;
    }
    function subtitle(label) {
      const map = {
	        "主页": "账号、资料和平台状态",
	        "剧本": "多书籍剧本入口，支持 TXT/MD 导入与章节识别",
        "开始游戏": "每个剧本下的游戏存档目录",
        "分支": "从任意对话节点继续游戏，并创建新的分支",
        "库": "上传、整理和下载多媒体与文档资产",
        "设置": "用户级偏好与部署参数",
        "插件": "已启用的平台插件",
        "MCP": "本地或服务器侧 MCP 配置",
        "Skill": "本地部署可导入 Skill",
        "API": "稳定功能指令和版本化接口",
      };
      return map[label] || "";
    }

    function authView() {
      const isRegister = state.authMode === "register";
      return `<div class="auth-wrap">
        <div class="auth">
	          <div><h1>RPG Platform</h1><p class="muted">登录后进入剧本、存档、分支和库。</p></div>
          <div class="tabs">
            <button class="${!isRegister ? "active" : ""}" onclick="state.authMode='login'; render()">登录</button>
            <button class="${isRegister ? "active" : ""}" onclick="state.authMode='register'; render()">注册</button>
          </div>
          <div class="form">
            <label><span>用户名</span><input id="username" autocomplete="username" /></label>
            <label><span>密码</span><input id="password" type="password" autocomplete="${isRegister ? "new-password" : "current-password"}" /></label>
            ${isRegister ? `<label><span>显示名</span><input id="displayName" /></label>` : ""}
            <button class="primary" onclick="${isRegister ? "register()" : "login()"}">${isRegister ? "创建账号" : "登录"}</button>
          </div>
          <p class="muted">首个注册用户会成为管理员。平台数据使用 PostgreSQL。</p>
        </div>
      </div>`;
    }
    async function login() {
      try {
        const out = await api("/auth/login", { method: "POST", body: JSON.stringify({ username: username.value, password: password.value }) });
        state.data = out.platform;
        notify("已登录");
        render();
      } catch (error) { notify(error.message); }
    }
    async function register() {
      try {
        const out = await api("/auth/register", { method: "POST", body: JSON.stringify({ username: username.value, password: password.value, display_name: displayName.value }) });
        state.data = out.platform;
        notify("账号已创建");
        render();
      } catch (error) { notify(error.message); }
    }
    async function logout() { await api("/auth/logout", { method: "POST" }); location.reload(); }
    async function reload() { await loadPlatform(); notify("已刷新"); }

    function profile() {
      const user = state.data.user;
      const db = state.data.database || {};
      const stats = [
        ["剧本", state.data.scripts?.length || 0],
        ["存档", state.data.saves?.length || 0],
        ["库资产", state.data.assets?.length || 0],
        ["API", state.data.commands?.length || 0],
      ];
      return `<div class="section">
        <div class="grid">${stats.map(([k,v]) => `<div class="panel"><h2>${k}</h2><p class="muted">${v}</p></div>`).join("")}</div>
        <div class="split">
          <div class="panel">
            <h2>${esc(user.display_name)}</h2>
            <p class="muted">@${esc(user.username)} · ${esc(user.role)} · ${esc(user.uid)}</p>
            <p style="margin-top:10px">${esc(user.bio || "暂无简介")}</p>
          </div>
          <div class="panel">
            <h2>编辑资料</h2>
            <div class="form" style="margin-top:12px">
              <label><span>显示名</span><input id="display" value="${esc(user.display_name)}" /></label>
              <label><span>简介</span><textarea id="bio">${esc(user.bio || "")}</textarea></label>
              <button class="primary" onclick="saveProfile()">保存资料</button>
            </div>
          </div>
        </div>
        <div class="panel"><h2>数据库</h2><p class="muted">${esc(db.url)} · ${db.ok ? "online" : "offline"}</p></div>
      </div>`;
    }
    async function saveProfile() {
      try {
        const out = await api("/profile", { method: "POST", body: JSON.stringify({ display_name: display.value, bio: bio.value }) });
        state.data.user = out.user;
        notify("资料已保存");
        render();
      } catch (error) { notify(error.message); }
    }

	    function shelf() {
	      const items = state.data.scripts || [];
	      return `<div class="section">
	        <div class="panel">
	          <h2>导入剧本</h2>
	          <div class="import-grid" style="margin-top:12px">
	            <label><span>标题</span><input id="scriptImportTitle" placeholder="默认使用文件名" /></label>
	            <label><span>章节规则</span><select id="scriptSplitRule">
	              <option value="auto">自动识别</option>
	              <option value="corpus">语料章节</option>
	              <option value="chapter_cn">中文章节</option>
	              <option value="chapter_en">英文章节</option>
	              <option value="number_dot">数字点号</option>
	              <option value="paren_num">括号编号</option>
	              <option value="custom">自定义</option>
	            </select></label>
	            <label><span>自定义模板或正则</span><input id="scriptCustomPattern" placeholder="例如：第*章 或 ^(卷.*第.*章.*)$" /></label>
	          </div>
	          <div class="toolbar" style="margin-top:12px">
	            <input id="scriptFileInput" class="hidden" type="file" accept=".txt,.md,text/plain,text/markdown" onchange="importSelectedScript(this.files && this.files[0])" />
	            <button class="primary" onclick="document.getElementById('scriptFileInput').click()">导入 TXT/MD</button>
	            <button onclick="reload()">刷新剧本</button>
	          </div>
	          <p class="small" style="margin-top:10px">已合并旧项目规则：中文/英文/数字/括号编号、篇章小节、分页标题、分卷和蕾穆丽娜混合标题。</p>
	        </div>
	        ${items.length ? `<div class="panel"><table class="data-table"><thead><tr><th>剧本</th><th>章节</th><th>字数</th><th>来源</th><th>标识</th><th>操作</th></tr></thead><tbody>${items.map(scriptRow).join("")}</tbody></table></div>` : empty("暂无剧本") }
	      </div>`;
	    }
	    function scriptRow(script) {
	      const report = script.import_report || {};
	      const chapters = Number(script.chapter_count || 0);
	      const words = Number(script.word_count || 0);
	      return `<tr>
	        <td><div class="title-cell"><strong>${esc(script.title)}</strong><span class="small">${esc(script.description || "暂无描述")}</span></div></td>
	        <td><span class="nowrap">${fmtCount(chapters)} 章</span><br><span class="small">${esc(report.mode_label || "未导入章节")}</span></td>
	        <td class="mono">${fmtCount(words)}</td>
	        <td class="mono">${esc(script.source_path || "无源路径")}</td>
	        <td><span class="pill">${esc(shortUid(uid(script)))}</span></td>
	        <td><button onclick="createSave(${script.id})">创建存档</button></td>
	      </tr>`;
	    }
	    function readAsDataUrl(file) {
	      return new Promise((resolve, reject) => {
	        const reader = new FileReader();
	        reader.onload = () => resolve(reader.result);
	        reader.onerror = () => reject(reader.error || new Error("读取文件失败"));
	        reader.readAsDataURL(file);
	      });
	    }
	    async function importSelectedScript(file) {
	      if (!file) return;
	      try {
	        notify("正在识别章节...");
	        const out = await api("/scripts/import", {
	          method: "POST",
	          body: JSON.stringify({
	            title: document.getElementById("scriptImportTitle")?.value || "",
	            split_rule: document.getElementById("scriptSplitRule")?.value || "auto",
	            custom_pattern: document.getElementById("scriptCustomPattern")?.value || "",
	            file: { name: file.name, type: file.type || "text/plain", data_url: await readAsDataUrl(file) },
	          }),
	        });
	        state.data = await api("/platform");
	        render();
	        notify(`已导入 ${fmtCount(out.report?.chapter_count || 0)} 章，${out.report?.mode_label || "自动识别"}`);
	      } catch (error) {
	        notify(error.message);
	      } finally {
	        const input = document.getElementById("scriptFileInput");
	        if (input) input.value = "";
	      }
	    }
    async function createSave(scriptId) {
      const title = prompt("存档名称", "新存档");
      if (title === null) return;
      try {
        await api("/saves", { method: "POST", body: JSON.stringify({ script_id: scriptId, title }) });
        await loadPlatform();
        go("saves");
        notify("存档已创建");
      } catch (error) { notify(error.message); }
    }

    function saves() {
      const items = state.data.saves || [];
      return `<div class="section">
        <div class="toolbar"><button class="primary" onclick="createSaveFromFirst()">新建存档</button><a href="/">进入当前游戏</a></div>
        ${items.length ? `<div class="panel"><table class="data-table"><thead><tr><th>存档</th><th>状态文件</th><th>分支</th><th>操作</th></tr></thead><tbody>${items.map(saveRow).join("")}</tbody></table></div>` : empty("暂无存档") }
      </div>`;
    }
    async function createSaveFromFirst() {
      const first = state.data.scripts?.[0];
      if (!first) return notify("没有可用剧本");
      await createSave(first.id);
    }
    function saveRow(save) {
      return `<tr>
        <td><div class="title-cell"><strong>${esc(save.title)}</strong><span class="small">uid ${esc(shortUid(uid(save)))}</span></div></td>
        <td class="mono">${esc(save.state_path)}</td>
        <td><span class="pill">${save.branch_count || 0} 节点</span></td>
        <td><div class="toolbar"><button class="primary" onclick="openBranch(${save.id})">查看分支</button><a href="/">继续游戏</a></div></td>
      </tr>`;
    }
    function openBranch(saveId) { state.branchSaveId = saveId; go("branches"); }

    function branches() {
      const saves = state.data.saves || [];
      const selected = state.branchSaveId || saves[0]?.id || "";
      return `<div class="section">
        <div class="panel">
          <div class="toolbar">
            <select id="saveSelect" onchange="state.branchSaveId=Number(this.value); loadBranch(Number(this.value))">${saves.map(s => `<option value="${s.id}" ${Number(selected)===s.id ? "selected" : ""}>${esc(s.title)}</option>`).join("")}</select>
            <button onclick="loadBranch()">刷新分支</button>
          </div>
        </div>
        ${state.branch ? branchTree(state.branch) : empty("请选择一个存档") }
      </div>`;
    }
    async function loadBranch(saveId) {
      const id = saveId || state.branchSaveId || state.data.saves?.[0]?.id;
      if (!id) return;
      try {
        state.branchSaveId = Number(id);
        state.branch = await api(`/branches/${id}?limit=500`);
        render();
      } catch (error) { notify(error.message); }
    }
    function branchTree(payload) {
      const nodes = payload.nodes || [];
      if (!nodes.length) return empty("暂无分支节点");
      const nodeWidth = 250;
      const nodeHeight = 104;
      const stepX = 304;
      const stepY = 154;
      const midY = Math.floor(nodeHeight / 2);
      const levels = {};
      nodes.forEach(n => {
        const level = Number(n.turn_index || 0);
        levels[level] = (levels[level] || 0) + 1;
        n.x = 40 + level * stepX;
        n.y = 48 + (levels[level] - 1) * stepY;
      });
      const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
      const height = Math.max(720, ...nodes.map(n => n.y + 166));
      const width = Math.max(1480, ...nodes.map(n => n.x + nodeWidth + 80));
      return `<div class="tree-wrap">
        <svg class="branch" width="${width}" height="${height}">
          ${nodes.filter(n => n.parent_id).map(n => {
            const p = byId[n.parent_id];
            return p ? `<path class="edge" d="M${p.x + nodeWidth} ${p.y + midY} C${p.x + nodeWidth + 36} ${p.y + midY},${n.x - 40} ${n.y + midY},${n.x} ${n.y + midY}"/>` : "";
          }).join("")}
          ${nodes.map(n => `<g class="node ${esc(n.role)}" transform="translate(${n.x},${n.y})">
            <rect width="${nodeWidth}" height="${nodeHeight}"></rect>
            <title>${esc(n.content_preview || n.summary || n.title)}</title>
            <text x="12" y="24">${esc(clip(n.title, 18))}</text>
            <text class="summary" x="12" y="52">${esc(branchSummary(n))}</text>
            <text class="sub" x="12" y="78">${esc(branchMeta(n))}</text>
            <text class="sub" x="12" y="96">id ${esc(shortUid(n.uid || n.id))}</text>
            <foreignObject x="0" y="116" width="${nodeWidth}" height="40">
              <div class="node-actions">
                <button class="node-btn" onclick="continueNode(${n.id})">继续</button>
                ${n.parent_id ? `<button class="node-btn danger" onclick="deleteNode(${n.id})">删线</button>` : ""}
              </div>
            </foreignObject>
          </g>`).join("")}
        </svg>
      </div>`;
    }
    async function continueNode(id) {
      try { state.branch = await api("/branches/continue", { method: "POST", body: JSON.stringify({ node_id: id }) }); notify("已创建新分支"); render(); }
      catch (error) { notify(error.message); }
    }
    async function deleteNode(id) {
      if (!confirm("删除这条连线下的整条分支？")) return;
      try { state.branch = await api("/branches/delete", { method: "POST", body: JSON.stringify({ node_id: id }) }); notify("分支已删除"); render(); }
      catch (error) { notify(error.message); }
    }

    function library() {
      const lib = state.library || state.data.library || { items: [], entries: [] };
      const items = lib.items || lib.entries || [];
      return `<div class="section">
        <div class="panel">
          <div class="toolbar">
            <button class="primary" onclick="pickFiles()">上传文件</button>
            <button onclick="mkdir()">新建文件夹</button>
            <button onclick="loadLibrary(parentPath(state.libraryPath))" ${state.libraryPath ? "" : "disabled"}>上一级</button>
            <input id="libInput" type="file" multiple class="hidden" onchange="uploadFiles(this.files)" />
          </div>
          <div class="breadcrumb">路径：${crumbs(state.libraryPath)}</div>
        </div>
        <div class="panel">
          <table><thead><tr><th>名称</th><th>类型</th><th>大小</th><th>操作</th></tr></thead><tbody>
            ${items.length ? items.map(fileRow).join("") : `<tr><td colspan="4" class="muted">这个文件夹是空的</td></tr>`}
          </tbody></table>
        </div>
      </div>`;
    }
    function crumbs(path) {
      if (!path) return `<button onclick="loadLibrary('')">根目录</button>`;
      const parts = path.split("/").filter(Boolean);
      let acc = "";
      return [`<button onclick="loadLibrary('')">根目录</button>`].concat(parts.map(part => {
        acc = [acc, part].filter(Boolean).join("/");
        return `<button onclick="loadLibrary('${esc(acc)}')">${esc(part)}</button>`;
      })).join(" / ");
    }
    function parentPath(path) { const parts = String(path || "").split("/").filter(Boolean); parts.pop(); return parts.join("/"); }
    function fileRow(item) {
      const isDir = item.type === "directory";
      return `<tr>
        <td>${isDir ? `<button onclick="loadLibrary('${esc(item.path)}')">📁 ${esc(item.name)}</button>` : `📄 ${esc(item.name)}`}</td>
        <td>${esc(item.type)} ${item.mime ? `· ${esc(item.mime)}` : ""}</td>
        <td>${fmtBytes(item.size)}</td>
        <td><div class="toolbar">${isDir ? "" : `<a href="${API}/library/download?path=${encodeURIComponent(item.path)}">下载</a>`}<button class="danger" onclick="deleteFile('${esc(item.path)}')">删除</button></div></td>
      </tr>`;
    }
    async function loadLibrary(path = "") {
      try {
        state.libraryPath = path;
        state.library = await api(`/library?path=${encodeURIComponent(path)}&limit=200`);
        render();
      } catch (error) { notify(error.message); }
    }
    function pickFiles() { document.getElementById("libInput").click(); }
    async function uploadFiles(files) {
      const payload = [];
      for (const file of Array.from(files)) payload.push(await filePayload(file));
      try {
        state.library = await api("/library/upload", { method: "POST", body: JSON.stringify({ path: state.libraryPath, files: payload }) });
        notify("文件已上传");
        render();
      } catch (error) { notify(error.message); }
    }
    function filePayload(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve({ name: file.name, type: file.type, data_url: reader.result });
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
    }
    async function mkdir() {
      const name = prompt("文件夹名");
      if (!name) return;
      try {
        state.library = await api("/library/mkdir", { method: "POST", body: JSON.stringify({ path: [state.libraryPath, name].filter(Boolean).join("/") }) });
        notify("文件夹已创建");
        render();
      } catch (error) { notify(error.message); }
    }
    async function deleteFile(path) {
      if (!confirm(`删除 ${path}？`)) return;
      try {
        state.library = await api("/library/delete", { method: "POST", body: JSON.stringify({ path }) });
        notify("已删除");
        render();
      } catch (error) { notify(error.message); }
    }

    function settings() {
      const s = state.data.settings || {};
      const mode = s.memory_mode || "normal";
      return `<div class="section">
        <div class="split">
          <div class="panel">
            <h2>用户设置</h2>
            <div class="form" style="margin-top:12px">
              <label><span>记忆模式</span><select id="memoryMode"><option value="normal" ${mode === "normal" ? "selected" : ""}>标准</option><option value="precise" ${mode === "precise" ? "selected" : ""}>精简</option><option value="deep" ${mode === "deep" ? "selected" : ""}>深入</option></select></label>
              <label><span>默认模型显示名</span><input id="modelAlias" value="${esc(s.model_alias || "Gemini 3.5")}" /></label>
              <label><span>部署备注</span><textarea id="deployNote">${esc(s.deploy_note || "")}</textarea></label>
              <button class="primary" onclick="saveSettings()">保存设置</button>
            </div>
          </div>
          <div class="panel"><h2>当前能力</h2>${capabilityList()}</div>
        </div>
      </div>`;
    }
    function capabilityList() {
      const caps = state.data.tools?.capabilities || {};
      return `<div class="list">${Object.entries(caps).map(([k,v]) => `<div class="item"><div class="item-head"><strong>${esc(k)}</strong><span class="pill ${v ? "ok" : "warn"}">${esc(v)}</span></div></div>`).join("")}</div>`;
    }
    async function saveSettings() {
      try {
        await api("/settings", { method: "POST", body: JSON.stringify({ key: "memory_mode", value: memoryMode.value }) });
        await api("/settings", { method: "POST", body: JSON.stringify({ key: "model_alias", value: modelAlias.value }) });
        await api("/settings", { method: "POST", body: JSON.stringify({ key: "deploy_note", value: deployNote.value }) });
        await loadPlatform();
        go("settings");
        notify("设置已保存");
      } catch (error) { notify(error.message); }
    }

    function plugins() {
      const plugins = state.data.tools?.plugins || [];
      return `<div class="section"><div class="grid">${plugins.map(p => `<div class="item"><div class="item-head"><h2>${esc(p.name)}</h2><span class="pill ${p.enabled ? "ok" : "warn"}">${p.enabled ? "启用" : "停用"}</span></div><p class="muted">${esc(p.id)} · ${esc(p.kind)}</p></div>`).join("")}</div></div>`;
    }
    function mcp() {
      const tools = state.data.tools || {};
      const servers = tools.mcp?.servers || [];
      const writable = tools.capabilities?.mcp_config_write_enabled;
      return `<div class="section">
        <div class="panel">
          <h2>MCP 服务器</h2>
          <p class="muted">${writable ? "当前部署允许写入本地 MCP 配置" : "当前部署为只读 MCP 配置"}</p>
        </div>
        ${writable ? mcpForm() : ""}
        ${servers.length ? `<div class="list">${servers.map(mcpServer).join("")}</div>` : empty("暂无 MCP 服务器")}
      </div>`;
    }
    function mcpForm() {
      return `<div class="panel">
        <h2>添加 / 更新 MCP</h2>
        <div class="form" style="margin-top:12px">
          <label><span>ID</span><input id="mcpId" placeholder="filesystem" /></label>
          <label><span>显示名</span><input id="mcpName" placeholder="Filesystem" /></label>
          <label><span>命令</span><input id="mcpCommand" placeholder="npx" /></label>
          <label><span>参数</span><input id="mcpArgs" placeholder="-y @modelcontextprotocol/server-filesystem /tmp" /></label>
          <button class="primary" onclick="saveMcp()">保存 MCP</button>
        </div>
      </div>`;
    }
    function mcpServer(server) {
      return `<div class="item">
        <div class="item-head"><h2>${esc(server.display_name || server.id)}</h2><span class="pill ${server.enabled ? "ok" : "warn"}">${server.enabled ? "启用" : "停用"}</span></div>
        <p><code>${esc(server.command)} ${esc((server.args || []).join(" "))}</code></p>
        <p class="muted">${esc(server.transport)} · ${esc(server.scope)}</p>
        <div class="toolbar">
          <button onclick="toggleMcp('${esc(server.id)}', ${server.enabled ? "false" : "true"})">${server.enabled ? "停用" : "启用"}</button>
          <button onclick="validateMcp('${esc(server.id)}')">校验</button>
          <button class="danger" onclick="deleteMcp('${esc(server.id)}')">删除</button>
        </div>
      </div>`;
    }
    async function saveMcp() {
      try {
        await api("/mcp/server", { method: "POST", body: JSON.stringify({ id: mcpId.value, display_name: mcpName.value, command: mcpCommand.value, args: mcpArgs.value.split(" ").filter(Boolean), transport: "stdio", enabled: true, scope: "local" }) });
        await loadPlatform();
        go("mcp");
        notify("MCP 已保存");
      } catch (error) { notify(error.message); }
    }
    async function toggleMcp(id, enabled) { await api("/mcp/server/enabled", { method: "POST", body: JSON.stringify({ id, enabled }) }); await loadPlatform(); go("mcp"); }
    async function deleteMcp(id) { if (!confirm("删除 MCP 配置？")) return; await api("/mcp/server/delete", { method: "POST", body: JSON.stringify({ id }) }); await loadPlatform(); go("mcp"); }
    async function validateMcp(id) { const out = await api("/mcp/server/validate", { method: "POST", body: JSON.stringify({ id }) }); notify(out.result?.ready_to_launch ? "命令可启动" : "命令未解析"); }

    function skills() {
      const tools = state.data.tools || {};
      const skills = tools.skills || [];
      const enabled = tools.capabilities?.skill_import_enabled;
      return `<div class="section">
        <div class="panel"><h2>Skill</h2><p class="muted">${enabled ? "本地部署允许导入 SKILL.md 或 zip" : "服务器模式禁用导入"}</p></div>
        ${enabled ? `<div class="panel"><div class="toolbar"><input id="skillInput" type="file" accept=".md,.zip" onchange="importSkill(this.files[0])" /><button onclick="skillInput.click()">选择文件</button></div></div>` : ""}
        ${skills.length ? `<div class="grid">${skills.map(s => `<div class="item"><h2>${esc(s.name)}</h2><p class="muted">${esc(s.id)}</p><p>${esc(s.path)}</p></div>`).join("")}</div>` : empty("暂无导入 Skill")}
      </div>`;
    }
    async function importSkill(file) {
      if (!file) return;
      const payload = await filePayload(file);
      try {
        await api("/skills/import", { method: "POST", body: JSON.stringify({ file: payload }) });
        await loadPlatform();
        go("skills");
        notify("Skill 已导入");
      } catch (error) { notify(error.message); }
    }

    function apis() {
      const commands = state.data.commands || [];
      return `<div class="section">
        <div class="panel"><h2>API 合约</h2><p class="muted">新客户端优先使用 /api/v1，旧 /api 路径继续兼容。</p></div>
        <div class="panel"><table><thead><tr><th>方法</th><th>路径</th><th>说明</th></tr></thead><tbody>${commands.map(c => `<tr><td><span class="pill">${esc(c.method)}</span></td><td><code>${esc(c.path)}</code></td><td>${esc(c.desc)}</td></tr>`).join("")}</tbody></table></div>
      </div>`;
    }
    function empty(text) { return `<div class="empty">${esc(text)}</div>`; }

    addEventListener("popstate", () => { state.page = routePage(); render(); });
    loadPlatform().then(() => {
      if (state.page === "branches") loadBranch();
      if (state.page === "library") loadLibrary("");
    });
  </script>
</body>
</html>
"""
