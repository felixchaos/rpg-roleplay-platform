// 插件 / MCP / Skill / API 能力页。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { Icon } from '../../game-icons.jsx';
import Modal from '../Modal.jsx';
import {
  PromptModal, SettingsToggle,
} from './shared.jsx';
import CSAlert from '@cloudscape-design/components/alert';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';

/* ---------------------------- PLUGINS / MCP / SKILLS / API ----- */
// task 50：原本 decks 是 5 项 plugins / 5 项 mcp / 4 项 skills 全部硬编码示例
// （filesystem·本地 / 时间线可视化 / 角色一致性 等），整页零 API 调用，
// 「校验」按钮是 dead button。现在改为：
//   - kind="plugins"  → /api/tools → tools.plugins[]（用户可看可改但少 toggle，所有 enabled）
//   - kind="mcp"      → /api/tools → tools.mcp.servers[] + /api/mcp/runtime 拼运行状态
//   - kind="skills"   → /api/tools → tools.skills[]（来自本地 sandbox）
//   - kind="apis"     → /api/platform.commands（真后端 commands 列表）
function CapPage({ kind }) {
  const [addOpen, setAddOpen] = useStatePL(false);
  const [items, setItems] = useStatePL([]);
  const [loading, setLoading] = useStatePL(false);
  const [err, setErr] = useStatePL("");
  const [reloadTick, setReloadTick] = useStatePL(0);

  useEffectPL(() => {
    if (kind === "apis") return;
    let cancelled = false;
    setLoading(true); setErr("");
    (async () => {
      try {
        const r = await window.api.tools.list();
        if (cancelled) return;
        const t = (r && r.tools) || {};
        let list = [];
        if (kind === "plugins") {
          list = (t.plugins || []).map(p => ({
            id: p.id || p.name, name: p.name || p.id, desc: p.description || "平台内置插件",
            tag: p.kind || "plugin", on: p.enabled !== false, status: p.enabled === false ? "未启用" : "已启用",
            _raw: p,
          }));
        } else if (kind === "mcp") {
          const servers = ((t.mcp || {}).servers) || [];
          // 拉运行状态以判断"已连接" vs "未连接"
          let running = [];
          try { const rt = await window.api.mcp.runtime(); running = (rt && (rt.running || [])) || []; } catch (_) {}
          const runSet = new Set(running.map(r => r.id || r.server_id || r.name));
          list = servers.map(s => {
            const isOn = !!s.enabled;
            const isRunning = isOn && (runSet.has(s.id) || runSet.has(s.server_id) || runSet.has(s.name));
            return {
              id: s.id || s.server_id || s.name, name: s.name || s.id,
              desc: s.description || (s.transport === "http" ? `HTTP · ${s.url || s.endpoint || "—"}` : `stdio · ${s.command || "—"}`),
              tag: s.transport || (s.url || s.endpoint ? "http" : "stdio"),
              on: isOn,
              status: isRunning ? "已连接" : (isOn ? "未连接" : "未启用"),
              _raw: s,
            };
          });
        } else if (kind === "skills") {
          list = (t.skills || []).map(s => ({
            id: s.id || s.slug || s.name, name: s.name || s.id, desc: s.description || s.summary || "",
            tag: s.version || s.kind || "v1", on: s.enabled !== false, status: s.enabled !== false ? "已部署" : "未启用",
            _raw: s,
          }));
        }
        if (cancelled) return;
        setItems(list);
      } catch (e) {
        if (!cancelled) setErr(e?.message || "拉取失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [kind, reloadTick]);

  if (kind === "apis") return <ApiList />;

  // 校验：对 MCP 走 mcp.validate 逐条；其他类型只刷新 /api/tools 即可。
  const onValidateAll = async () => {
    setLoading(true);
    if (kind === "mcp") {
      const results = await Promise.all(
        items.filter(it => it.on).map(it =>
          window.api.mcp.validate({ id: it.id, server_id: it.id }).catch(() => null)
        )
      );
      const ok = results.filter(r => r && r.ok !== false).length;
      const fail = results.length - ok;
      window.__apiToast?.(`校验完成 · ${ok} ok / ${fail} fail`, { kind: fail ? "warn" : "ok", duration: 2400 });
    }
    setReloadTick(t => t + 1);
  };

  const emptyMsg = kind === "mcp"
    ? "尚未配置 MCP 服务器。点击「新增服务器」添加。"
    : kind === "skills"
    ? "尚未导入 Skill 包。点击「导入 Skill」上传。"
    : "暂无插件。";

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" dismissible={false}>加载失败：{err}</CSAlert>}
      <CSContainer header={
        <CSHeader
          variant="h2"
          counter={loading ? undefined : `(${items.length} 项 · ${items.filter(i => i.on).length} 已启用)`}
          actions={
            <CSSpaceBetween size="xs" direction="horizontal">
              <CSButton
                iconName="refresh"
                onClick={onValidateAll}
                loading={loading}
              >
                校验
              </CSButton>
              <CSButton
                variant="primary"
                iconName="add-plus"
                onClick={() => setAddOpen(true)}
              >
                {kind === "mcp" ? "新增服务器" : kind === "skills" ? "导入 Skill" : "新增插件"}
              </CSButton>
            </CSSpaceBetween>
          }
        >
          {kind === "plugins" ? "插件" : kind === "mcp" ? "MCP 服务器" : "Skill 包"}
        </CSHeader>
      }>
        {loading && items.length === 0 ? (
          <CSBox textAlign="center" color="text-body-secondary" padding="l">加载中…</CSBox>
        ) : items.length === 0 ? (
          <CSBox textAlign="center" color="text-body-secondary" padding="l">{emptyMsg}</CSBox>
        ) : (
          <div className="pl-cap-grid">
            {items.map((it, i) => <CapCard key={it.id || i} {...it} kind={kind} onChanged={() => setReloadTick(t => t + 1)} />)}
          </div>
        )}
      </CSContainer>
      <PromptModal
        open={addOpen}
        eyebrow={kind === "mcp" ? "新增 MCP 服务器" : kind === "skills" ? "导入 Skill" : "新增插件"}
        title={kind === "mcp" ? "配置一个 MCP 端点" : kind === "skills" ? "选择 Skill 包" : "添加一个平台插件"}
        hint={kind === "mcp" ? "POST /api/v1/mcp/server" : kind === "skills" ? "POST /api/v1/skills/import" : "POST /api/v1/plugins"}
        fields={
          kind === "mcp" ? [
            { key: "name", label: "名称", required: true, placeholder: "例：filesystem · 本地" },
            { key: "transport", label: "传输", type: "select", default: "stdio",
              options: [{ value: "stdio", label: "stdio · 本地命令" }, { value: "http", label: "http · 远程 HTTP" }] },
            { key: "command", label: "命令 / URL", required: true, mono: true,
              placeholder: "stdio: uvx my-mcp\nhttp: https://host:port" },
            { key: "env", label: "环境变量 / Headers", type: "textarea",
              placeholder: "每行一个：KEY=VALUE", rows: 3 },
          ] : kind === "skills" ? [
            { key: "repo_url", label: "GitHub 链接", mono: true, placeholder: "https://github.com/owner/repo（人格 skill → 角色卡 + 人设图）" },
            { key: "file", label: "本地文件", type: "file", hint: ".md → 角色卡 / .zip · .tar.gz → 可执行 Skill 包(管理员)" },
          ] : [
            { key: "id", label: "插件 ID", required: true, mono: true, placeholder: "例：timeline-viz" },
            { key: "name", label: "显示名", required: true, placeholder: "例：时间线可视化" },
            { key: "desc", label: "说明", type: "textarea", placeholder: "做什么，何时触发" },
          ]
        }
        submitLabel={kind === "mcp" ? "校验并启用" : kind === "skills" ? "导入并部署" : "添加"}
        onClose={() => setAddOpen(false)}
        onConfirm={async (vals) => {
          // task 50：原 onConfirm = () => setAddOpen(false)，纯关闭。
          // 现在按 kind 真打后端，失败把错误吐给用户。
          try {
            if (kind === "mcp") {
              // 解析 KEY=VALUE 行（env）
              const envObj = {};
              for (const line of String(vals.env || "").split("\n")) {
                const m = line.trim().match(/^([^=]+)=(.*)$/);
                if (m) envObj[m[1].trim()] = m[2];
              }
              const body = { name: vals.name, transport: vals.transport || "stdio", enabled: true };
              if (body.transport === "http") body.url = vals.command;
              else body.command = vals.command;
              if (Object.keys(envObj).length) body.env = envObj;
              await window.api.mcp.upsert(body);
              window.__apiToast?.("MCP 服务器已添加 · 正在校验", { kind: "ok", duration: 2000 });
              try { await window.api.mcp.validate({ name: vals.name }); } catch (_) {}
            } else if (kind === "skills") {
              const repo = String(vals.repo_url || "").trim();
              const f = vals.file;
              const isMd = f && /\.md$/i.test(f.name || "");
              if (!repo && !f) throw new Error("请填 GitHub 链接或选择文件");
              if (repo || isMd) {
                // 人格 skill → 蒸馏成角色卡 + 人设图(纯数据,每用户隔离)
                const body = repo
                  ? { source: "github", repo_url: repo }
                  : { source: "upload", files: [{ name: f.name, content: await f.text() }] };
                const r = await window.api.personaSkills.import(body);
                if (!r || !r.ok) throw new Error((r && r.error) || "导入失败");
                const nm = (r.card && r.card.name) || "角色卡";
                const img = r.image_status === "queued" ? "(人设图生成中)" : "";
                window.__apiToast?.(`已生成角色卡「${nm}」${img}`, { kind: "ok", duration: 2600 });
              } else {
                // .zip/.tar.gz 可执行 Skill 包(管理员)
                await window.api.skills.importPack(f);
                window.__apiToast?.("Skill 已导入", { kind: "ok", duration: 1800 });
              }
            } else {
              // plugins 没有专用 POST，只能在前端打 toast 解释
              window.__apiToast?.("插件由平台预置 · 暂不支持自定义新增", { kind: "warn", duration: 2400 });
            }
            setAddOpen(false);
            setReloadTick(t => t + 1);
          } catch (e) {
            window.__apiToast?.("添加失败", { kind: "danger", detail: e?.message || String(e) });
          }
        }}
      />
    </CSSpaceBetween>
  );
}

function CapCard({ id, name, desc, tag, on, status, kind, onChanged, _raw }) {
  const [v, setV] = useStatePL(!!on);
  const [editOpen, setEditOpen] = useStatePL(false);
  const [logOpen, setLogOpen] = useStatePL(false);
  const [logText, setLogText] = useStatePL("");
  const [logBusy, setLogBusy] = useStatePL(false);
  const [confirmDel, setConfirmDel] = useStatePL(false);
  const [delBusy, setDelBusy] = useStatePL(false);
  React.useEffect(() => { setV(!!on); }, [on]);
  // task 50：toggle 之前只改本地 state，没动后端 → 重新拉数据后状态被冲掉。
  // 现在 MCP/Skill 切换走真后端：MCP /api/mcp/server/enabled，Skill 暂没专用 toggle
  // 接口（后端默认全启用），只本地视觉切换并 toast 提示。
  const handleToggle = async (next) => {
    setV(next);
    if (kind === "mcp") {
      try {
        await window.api.mcp.enabled({ id, server_id: id, enabled: !!next });
        window.__apiToast?.(next ? "已启用" : "已停用", { kind: "ok", duration: 1500 });
        if (next) {
          try { await window.api.mcp.start({ id, server_id: id }); } catch (_) {}
        } else {
          try { await window.api.mcp.stop({ id, server_id: id }); } catch (_) {}
        }
        onChanged && onChanged();
      } catch (e) {
        setV(!next);
        window.__apiToast?.("切换失败", { kind: "danger", detail: e?.message });
      }
    } else if (kind === "skills") {
      // 后端目前无 skill enable toggle；不假装成功
      window.__apiToast?.("Skill 默认全部启用 · 暂不支持单独停用", { kind: "warn", duration: 2400 });
      setV(true);
    } else if (kind === "plugins") {
      window.__apiToast?.("插件状态由平台管理 · 暂不支持手动切换", { kind: "warn", duration: 2400 });
      setV(true);
    }
  };
  // 删除 MCP 服务器
  const handleDelete = async () => {
    if (kind !== "mcp") {
      window.__apiToast?.("暂不支持删除该类型", { kind: "warn", duration: 2000 });
      setConfirmDel(false);
      return;
    }
    setDelBusy(true);
    try {
      await window.api.mcp.remove({ id, server_id: id });
      window.__apiToast?.(`已删除 ${name}`, { kind: "ok", duration: 1500 });
      setConfirmDel(false);
      onChanged && onChanged();
    } catch (e) {
      window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message });
    }
    setDelBusy(false);
  };
  // task 50：查看日志 → 拉真后端运行时（admin 看到 stderr）。导出 → 下载文本。
  const loadLog = React.useCallback(async () => {
    setLogBusy(true);
    try {
      if (kind === "mcp") {
        const r = await window.api.mcp.runtime();
        const list = (r && r.running) || [];
        const me = list.find(x => x.id === id || x.server_id === id || x.name === name);
        if (me) {
          const stderr = me.stderr || me.last_stderr || "";
          const meta = `pid: ${me.pid || "-"} · status: ${me.status || (me.alive ? "alive" : "—")}\nlast_seen: ${me.last_seen_at || me.last_heartbeat_at || "—"}\n`;
          setLogText(meta + (stderr ? "\n--- stderr (recent) ---\n" + stderr : "\n（无 stderr 输出，可能 admin 权限不足 / 日志为空）"));
        } else {
          setLogText("（运行时未发现该服务器实例 · 可能未启用 / 未启动）");
        }
      } else {
        setLogText("（该类型暂不支持运行时日志查询，仅 MCP 走 /api/mcp/runtime）");
      }
    } catch (e) {
      setLogText("读取日志失败：" + (e?.message || String(e)));
    }
    setLogBusy(false);
  }, [kind, id, name]);
  React.useEffect(() => { if (logOpen) loadLog(); }, [logOpen, loadLog]);
  const editFields = kind === "mcp" ? (() => {
    const rawTransport = (_raw || {}).transport || tag || "stdio";
    const rawCommand = (_raw || {}).command || "";
    const rawEnv = (() => { const e = (_raw || {}).env || {}; return Object.keys(e).length ? Object.entries(e).map(([k,v]) => `${k}=${v}`).join("\n") : ""; })();
    const rawUrl = (_raw || {}).url || "";
    const rawHeaders = (() => { const h = (_raw || {}).headers || {}; return Object.keys(h).length ? JSON.stringify(h, null, 2) : ""; })();
    return [
      { key: "name", label: "名称", required: true, default: name },
      { key: "transport", label: "传输", type: "select", default: rawTransport,
        options: [{ value: "stdio", label: "stdio · 本地命令" }, { value: "http", label: "http · 远程 HTTP" }] },
      { key: "url", label: "URL", mono: true, default: rawUrl, placeholder: "https://example.com/mcp" },
      { key: "command", label: "命令", mono: true, default: rawCommand, placeholder: "uvx my-mcp" },
      { key: "headers", label: "Headers (JSON)", type: "textarea", rows: 3, default: rawHeaders, placeholder: '{"Authorization":"Bearer xxx"}' },
      { key: "env", label: "环境变量", type: "textarea", placeholder: "KEY=VALUE", rows: 3, default: rawEnv },
    ];
  })() : kind === "skills" ? [
    { key: "name", label: "显示名", required: true, default: name },
    { key: "version", label: "版本", default: tag },
    { key: "manifest", label: "manifest 配置", type: "textarea", rows: 4,
      placeholder: '{"hooks": ["before_turn", "after_state_write"]}' },
  ] : [
    { key: "id", label: "插件 ID", required: true, mono: true, default: tag },
    { key: "name", label: "显示名", required: true, default: name },
    { key: "desc", label: "说明", type: "textarea", default: desc, rows: 3 },
  ];
  return (
    <div className="pl-cap">
      <div className="pl-cap-head">
        <div className="pl-cap-icon">
          <Icon name={kind === "mcp" ? "diamond" : kind === "skills" ? "spark" : "plug"} size={16} />
        </div>
        <div style={{minWidth: 0, flex: 1}}>
          <strong>{name}</strong>
          <div className="muted-2">{tag}</div>
        </div>
        <SettingsToggle on={v} set={handleToggle} />
      </div>
      <p className="pl-cap-desc">{desc}</p>
      <div className="pl-cap-foot">
        <span className={`pill ${v ? "ok" : ""}`}>
          <span className={`dot ${v ? "ok" : ""}`} /> {v ? status : "未启用"}
        </span>
        <div style={{display: "flex", gap: 4}}>
          <button className="iconbtn" data-tip="编辑" onClick={() => setEditOpen(true)}><Icon name="edit" size={12} /></button>
          <button className="iconbtn" data-tip="查看日志" onClick={() => setLogOpen(true)}><Icon name="debug" size={12} /></button>
          {kind === "mcp" && <button className="iconbtn" data-tip="删除" onClick={() => setConfirmDel(true)}><Icon name="trash" size={12} /></button>}
        </div>
      </div>
      <PromptModal
        open={editOpen}
        eyebrow={`编辑 ${kind === "mcp" ? "MCP 服务器" : kind === "skills" ? "Skill" : "插件"}`}
        title={name}
        hint={kind === "mcp" ? "POST /api/mcp/server" : kind === "skills" ? "暂未提供编辑接口" : "暂未提供编辑接口"}
        fields={editFields}
        submitLabel="保存"
        onClose={() => setEditOpen(false)}
        onConfirm={async (vals) => {
          // task 50：之前是 () => setEditOpen(false) 纯关闭，没保存任何东西。
          // MCP 现在走真 /api/mcp/server upsert；其他类型说明不支持。
          if (kind === "mcp") {
            try {
              const envObj = {};
              for (const line of String(vals.env || "").split("\n")) {
                const m = line.trim().match(/^([^=]+)=(.*)$/);
                if (m) envObj[m[1].trim()] = m[2];
              }
              const body = { id, server_id: id, name: vals.name || name, transport: vals.transport || tag, enabled: v };
              if ((vals.transport || tag) === "http") {
                body.url = vals.url || "";
                body.command = "";
                try { body.headers = vals.headers ? JSON.parse(vals.headers) : {}; } catch (_) { body.headers = {}; }
              } else {
                body.command = vals.command || "";
                body.url = "";
              }
              if (Object.keys(envObj).length) body.env = envObj;
              await window.api.mcp.upsert(body);
              window.__apiToast?.("已保存", { kind: "ok", duration: 1500 });
              setEditOpen(false);
              onChanged && onChanged();
            } catch (e) {
              window.__apiToast?.("保存失败", { kind: "danger", detail: e?.message });
            }
          } else {
            window.__apiToast?.("该类型暂不支持后端编辑", { kind: "warn", duration: 2400 });
            setEditOpen(false);
          }
        }}
      />
      {logOpen && (
        <Modal
          open
          eyebrow={`日志 · ${name}`}
          title="最近 50 条"
          width={640}
          onClose={() => setLogOpen(false)}
          footer={<>
            <span className="muted-2" style={{fontSize: 11.5}}>
              <Icon name="info" size={11} /> {kind === "mcp" ? "GET /api/mcp/runtime · admin 可见 stderr" : "本类型暂无运行时日志"}
            </span>
            <div style={{display: "flex", gap: 8}}>
              <button className="btn ghost" onClick={loadLog} disabled={logBusy}><Icon name="refresh" size={12} /> 刷新</button>
              <button className="btn ghost" onClick={() => setLogOpen(false)}>关闭</button>
              <button className="btn primary" disabled={!logText} onClick={() => {
                // task 50：之前是 dead button。下载日志文本为 .log 文件。
                try {
                  const blob = new Blob([logText || ""], { type: "text/plain;charset=utf-8" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  const safe = String(name || id || "log").replace(/[^\w.-]+/g, "_");
                  a.href = url; a.download = `${safe}.log`;
                  document.body.appendChild(a); a.click();
                  document.body.removeChild(a);
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                } catch (e) { window.__apiToast?.("导出失败", { kind: "danger", detail: e?.message }); }
              }}><Icon name="download" size={12} /> 导出</button>
            </div>
          </>}
        >
            <pre className="mono" style={{
              maxHeight: 320, overflow: "auto", margin: 0, padding: "12px 14px",
              background: "var(--bg-deep)", border: "1px solid var(--line-soft)",
              borderRadius: "var(--r-2)", fontSize: 11.5, lineHeight: 1.7, color: "var(--text-quiet)"
            }}>{logBusy ? "加载中…" : logText || "（暂无内容）"}</pre>
        </Modal>
      )}
      {confirmDel && (
        <Modal eyebrow="删除确认" title="删除 MCP 服务器" width={420}
          onClose={() => setConfirmDel(false)} closeDisabled={delBusy}
          footer={<>
            <span />
            <div style={{display: "flex", gap: 8}}>
              <button className="btn ghost" onClick={() => setConfirmDel(false)} disabled={delBusy}>取消</button>
              <button className="btn danger" onClick={handleDelete} disabled={delBusy}>
                {delBusy ? "删除中…" : <><Icon name="trash" size={12} /> 删除</>}
              </button>
            </div>
          </>}>
          <div style={{padding: "0 16px 16px", fontSize: 13.5, lineHeight: 1.7}}>
            确定要删除 <strong>{name}</strong> 吗？此操作不可撤销。
          </div>
        </Modal>
      )}
    </div>
  );
}
const API_ROWS = [
  { m: "GET",  p: "/",                              d: "文字 RPG 主游戏界面",                       group: "主页" },
  { m: "GET",  p: "/app",                           d: "多用户平台 / 创作平台界面",                   group: "主页" },
  { m: "GET",  p: "/api/v1/state",                     d: "读取当前可玩存档状态",                       group: "存档" },
  { m: "POST", p: "/api/v1/new",                       d: "创建新游戏并保留旧档备份",                   group: "存档" },
  { m: "POST", p: "/api/v1/chat",                      d: "发送玩家行动，返回 SSE 流",                  group: "存档" },
  { m: "POST", p: "/api/v1/stop",                      d: "打断当前生成",                               group: "存档" },
  { m: "POST", p: "/api/v1/save",                      d: "手动保存当前游戏",                           group: "存档" },
  { m: "POST", p: "/api/v1/permissions",               d: "设置 LLM 写入权限",                          group: "权限" },
  { m: "POST", p: "/api/v1/memory/add",                d: "新增长期记忆条目",                           group: "记忆" },
  { m: "POST", p: "/api/v1/memory/remove",             d: "移除长期记忆条目",                           group: "记忆" },
  { m: "GET",  p: "/api/v1/models",                    d: "读取 API / 模型清单",                        group: "模型" },
  { m: "POST", p: "/api/v1/models/select",             d: "选择当前前端模型",                           group: "模型" },
  { m: "GET",  p: "/api/v1/scripts",                   d: "剧本列表",                                   group: "剧本" },
  { m: "POST", p: "/api/v1/scripts/import",            d: "导入 TXT / MD 剧本并自动识别章节",           group: "剧本" },
  { m: "GET",  p: "/api/v1/scripts/{id}/chapters",     d: "读取剧本章节目录与预览",                     group: "剧本" },
  { m: "GET",  p: "/api/v1/saves",                     d: "游戏存档目录",                               group: "平台" },
  { m: "POST", p: "/api/v1/saves",                     d: "基于剧本创建新存档",                         group: "平台" },
  { m: "GET",  p: "/api/v1/branches/{save_id}",        d: "读取分支树",                                 group: "分支" },
  { m: "POST", p: "/api/v1/branches/continue",         d: "从节点继续并创建新分支",                     group: "分支" },
  { m: "POST", p: "/api/v1/branches/delete",           d: "删除某条连线下的整条分支",                   group: "分支" },
  { m: "GET",  p: "/api/v1/library",                   d: "库文件列表",                                 group: "库" },
  { m: "POST", p: "/api/v1/library/upload",            d: "上传文件",                                   group: "库" },
  { m: "POST", p: "/api/v1/library/mkdir",             d: "创建文件夹",                                 group: "库" },
  { m: "GET",  p: "/api/v1/library/download",          d: "下载文件",                                   group: "库" },
  { m: "POST", p: "/api/v1/mcp/server",                d: "新增 / 更新 MCP 服务器配置",                 group: "能力" },
  { m: "POST", p: "/api/v1/skills/import",             d: "本地部署导入 Skill 包",                       group: "能力" },
];

function ApiList() {
  const [q, setQ] = useStatePL("");
  const filtered = API_ROWS.filter(r => !q || r.p.includes(q) || r.d.includes(q));
  const groups = {};
  filtered.forEach(r => { (groups[r.group] = groups[r.group] || []).push(r); });
  return (
    <div className="pl-stack">
      <section className="pl-sec">
        <div className="pl-sec-head">
          <h2>稳定接口 <span className="muted-2">v1 · {filtered.length} 条 · {Object.keys(groups).length} 组</span></h2>
          <div className="pl-sec-tools" style={{flex: 1, maxWidth: 320}}>
            <input style={{height: 28, fontSize: 12}} placeholder="搜索路径或描述..." aria-label="搜索路径或描述" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
        </div>
        {Object.entries(groups).map(([group, items]) => (
          <div key={group} style={{display: "grid", gap: 8}}>
            <div className="pl-stat-label" style={{padding: "8px 4px 0"}}>{group}</div>
            <div className="pl-api">
              <div className="pl-api-row head"><div>METHOD</div><div>路径</div><div>说明</div><div></div></div>
              {items.map((r, i) => (
                <div key={i} className="pl-api-row">
                  <div><span className={`pl-api-method ${r.m}`}>{r.m}</span></div>
                  <div className="pl-api-path">{r.p}</div>
                  <div className="pl-api-desc">{r.d}</div>
                  <div className="pl-table-actions">
                    <button className="iconbtn" data-tip="复制路径" onClick={async () => {
                      // task 50：之前是 dead button
                      try {
                        await navigator.clipboard.writeText(r.p);
                        window.__apiToast?.("已复制 " + r.p, { kind: "ok", duration: 1500 });
                      } catch {
                        window.__apiToast?.("复制失败", { kind: "danger", detail: "浏览器拒绝访问剪贴板" });
                      }
                    }}><Icon name="link" size={12} /></button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}

export { CapPage };
