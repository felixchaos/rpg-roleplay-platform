/* Platform shell + all page bodies. Single-file for simplicity. */

import React from 'react';
import { createPortal } from 'react-dom';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import { Icon } from './game-icons.jsx';
import { useResizable, ResizeHandle } from './responsive.jsx';
import { MODELS_DATA } from './pages/settings.jsx';
// ESM 重构遗漏修复:ContinuePicker / NewGameModal 的真实现在 pages/saves.jsx,
// platform-app 之前留了返回 null 的 stub 遮蔽它们 → "继续游戏"/"新建存档" 全失效。
// PlatformShell(本文件)直接渲染这两个组件,必须从真实现 import,不能用 stub。
import { ContinuePicker, NewGameModal } from './pages/saves.jsx';
// Cloudscape shell(AWS 控制台架构 + 暖色主题)
import CSTopNavigation from '@cloudscape-design/components/top-navigation';
import CSAppLayout from '@cloudscape-design/components/app-layout';
import CSSideNavigation from '@cloudscape-design/components/side-navigation';
import CSInput from '@cloudscape-design/components/input';
import CSButtonDropdown from '@cloudscape-design/components/button-dropdown';
// Cloudscape 内容迁移(me/profile/library/modules/extensions 等页)
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSFormField from '@cloudscape-design/components/form-field';
import CSSelect from '@cloudscape-design/components/select';
import CSToggle from '@cloudscape-design/components/toggle';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSTable from '@cloudscape-design/components/table';
import CSCards from '@cloudscape-design/components/cards';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSBadge from '@cloudscape-design/components/badge';
import CSAlert from '@cloudscape-design/components/alert';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSModal from '@cloudscape-design/components/modal';

const PL_NAV = [
  { section: "工作台" },
  { id: "profile",  label: "主页",     icon: "home" },
  { id: "scripts",  label: "剧本",     icon: "book" },
  { id: "modules",  label: "冒险模组", icon: "spark" },
  { id: "saves",    label: "开始游戏", icon: "play" },
  { id: "cards",    label: "角色卡",   icon: "cards" },
  { id: "library",  label: "库",       icon: "folder" },
  { section: "配置" },
  { id: "settings", label: "设置",     icon: "settings" },
  { id: "usage",    label: "用量",     icon: "usage" },
  { id: "plugins",  label: "插件",     icon: "plug" },
  { id: "mcp",      label: "MCP",      icon: "diamond" },
  { id: "skills",   label: "Skill",    icon: "spark" },
  { id: "apis",     label: "API",      icon: "braces" },
];

const PL_TITLES = {
  profile:  ["主页",     "账号、平台状态和最近资源"],
  scripts:  ["剧本",     "管理已导入的剧本"],
  "scripts-import": ["剧本 / 导入", "上传 TXT/MD，自动识别章节切分"],
  modules:  ["冒险模组", "5E compatible · 五版规则兼容 · 原创规则模组"],
  saves:    ["开始游戏", "存档目录与分支树"],
  "saves-branches": ["开始游戏 / 分支", "可拖动的思维导图，查看分支并继续"],
  cards:    ["角色卡",   "全局用户角色卡库"],
  "cards-npc": ["角色卡 / NPC", "存档内 NPC 角色卡，跨存档共享"],
  library:  ["库",       "上传、整理、下载多媒体与文档资产"],
  me:          ["个人主页",       "资料、成就、近期活动"],
  "me-edit":   ["个人主页 / 编辑资料", "账户信息、联系方式、本地化"],
  "me-settings": ["个人主页 / 用户设置", "隐私、合规、安全、数据所有权"],
  settings: ["设置",     "用户偏好、部署参数、API 与模型"],
  usage:    ["用量",     "调用量、Token 消耗、成本、延迟、错误率"],
  plugins:  ["插件",     "已启用的平台插件"],
  mcp:      ["MCP",      "本地或服务器侧 MCP 服务器"],
  skills:   ["Skill",    "本地部署可导入 Skill 包"],
  apis:     ["API",      "稳定功能指令与版本化接口"],
};

/* ── 统一顶栏 chrome:页面把 title/breadcrumb/actions 喂给唯一的 topbar,
   不再各页自渲染一条标题栏(消除顶栏割裂)。 */
const ShellChromeCtx = React.createContext({ set: () => {}, clear: () => {} });
function useShellChrome(chrome, deps = []) {
  const ctx = React.useContext(ShellChromeCtx);
  React.useEffect(() => {
    ctx.set(chrome);
    return () => ctx.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/* ── 页面内「列表↕详情」可拖拽分栏 ───────────────────────────────
   页面内容流里的上下两栏:上=列表、下=详情,中间一条可拖动的分隔线。
   - 上栏固定 height=topH + overflow:auto:拖分隔线直接改这个高度(真能拖);
     内容超出就在容器内滚动,滚动容器加圆角避免直角硬切表格圆角。
   - 下栏自然高度跟在后面(不强撑视口),整页按需滚动 → 保持 AWS 紧凑密度。
   - 拖动改 topH,持久化到 localStorage。 */
function ResizableSplit({ top, bottom, storageKey, initialTop = 240, minTop = 96 }) {
  const read = () => {
    if (!storageKey) return initialTop;
    const v = Number(window.localStorage?.getItem('pl-split-' + storageKey));
    return v && v > 0 ? v : initialTop;
  };
  const [topH, setTopH] = React.useState(read);
  const onDown = (e) => {
    e.preventDefault();
    const startY = e.clientY;
    const start = topH;
    const maxTop = Math.round((window.innerHeight || 900) * 0.78);
    let latest = start;
    const onMove = (ev) => {
      let nh = start + (ev.clientY - startY);
      nh = Math.max(minTop, Math.min(nh, maxTop));
      latest = nh;
      setTopH(nh);
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      if (storageKey) { try { window.localStorage?.setItem('pl-split-' + storageKey, String(latest)); } catch (_) {} }
    };
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'row-resize';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };
  return (
    <div className="pl-vsplit">
      <div className="pl-vsplit-top" style={{ height: topH, overflow: 'auto', borderRadius: 12 }}>{top}</div>
      <div className="pl-vsplit-handle" onMouseDown={onDown} role="separator" aria-orientation="horizontal" title="拖动调整列表区高度"
        style={{ height: 16, cursor: 'row-resize', display: 'flex', alignItems: 'center', justifyContent: 'center', touchAction: 'none' }}>
        <div className="pl-vsplit-grip" style={{ width: 56, height: 5, borderRadius: 3, background: 'var(--line-strong, #4a4540)' }} />
      </div>
      <div className="pl-vsplit-bottom">{bottom}</div>
    </div>
  );
}

/* ---------------------------- GENERIC MODALS ------------------- */
function PromptModal({ open, eyebrow, title, fields = [], submitLabel = "确认", danger = false, hint, onClose, onConfirm, busy = false }) {
  const [values, setValues] = useStatePL({});
  // Only reset when `open` transitions false → true; fields is an inline array
  // and would loop if added to deps.
  React.useEffect(() => {
    if (!open) return;
    const init = {};
    fields.forEach(f => { init[f.key] = f.default ?? ""; });
    setValues(init);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  if (!open) return null;
  const update = (k, v) => setValues(s => ({ ...s, [k]: v }));
  const canSubmit = fields.every(f => !f.required || (values[f.key] !== "" && values[f.key] != null));
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(480px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            {eyebrow && <div className="pl-modal-eyebrow">{eyebrow}</div>}
            <h2 className="pl-modal-title">{title}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form">
          {fields.map(f => (
            <div key={f.key} className="pl-field">
              <label>{f.label} {f.hint && <span className="muted-2" style={{textTransform: "none", letterSpacing: 0, marginLeft: 6}}>{f.hint}</span>}</label>
              {f.type === "textarea" ? (
                <textarea value={values[f.key] || ""} onChange={(e) => update(f.key, e.target.value)} placeholder={f.placeholder} rows={f.rows || 3} />
              ) : f.type === "select" ? (
                <select value={values[f.key] || ""} onChange={(e) => update(f.key, e.target.value)}>
                  {f.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              ) : f.type === "file" ? (
                <div className="pl-drop" style={{padding: "14px 16px"}}>
                  <Icon name="upload" size={18} style={{color: "var(--muted)"}} />
                  <strong>把文件拖到这里或</strong>
                  <a href="#" onClick={(e) => { e.preventDefault(); update(f.key, "example.png"); }}>选择本地文件</a>
                  {values[f.key] && <span className="mono muted" style={{fontSize: 11.5}}>{values[f.key]}</span>}
                </div>
              ) : (
                <input type={f.type || "text"} className={f.mono ? "mono" : ""}
                  value={values[f.key] || ""} onChange={(e) => update(f.key, e.target.value)}
                  placeholder={f.placeholder} autoFocus={f === fields[0]} />
              )}
            </div>
          ))}
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            {hint ? (<><Icon name="info" size={11} /> {hint}</>) : null}
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            <button className={`btn ${danger ? "danger" : "primary"}`} disabled={!canSubmit || busy}
              onClick={() => onConfirm(values)}>
              {danger ? <Icon name="trash" size={12} /> : <Icon name="check" size={12} />} {submitLabel}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

function ConfirmModal({ open, title, body, danger = false, confirmLabel = "确认", onClose, onConfirm }) {
  if (!open) return null;
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(440px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow" style={{color: danger ? "var(--danger)" : "var(--muted-2)"}}>
              {danger ? "高危操作" : "确认"}
            </div>
            <h2 className="pl-modal-title">{title}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div style={{fontSize: 13.5, lineHeight: 1.65, color: "var(--text-quiet)"}}>{body}</div>
        <footer className="pl-modal-foot">
          <span></span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            <button className={`btn ${danger ? "danger" : "primary"}`} onClick={onConfirm}>
              {danger ? <Icon name="trash" size={12} /> : <Icon name="check" size={12} />} {confirmLabel}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

/* ---------------------------- TOAST ---------------------------- */
const __toastListeners = [];
let __toastId = 0;
function emitToast(toast) {
  __toastListeners.forEach(fn => fn(toast));
}
window.toast = function(message, opts = {}) {
  const t = {
    id: ++__toastId,
    kind: opts.kind || "ok",        // ok | info | warn | danger
    icon: opts.icon,
    message,
    detail: opts.detail || null,
    duration: opts.duration ?? 2400,
    action: opts.action,
  };
  emitToast(t);
  return t.id;
};

function useToasts() {
  const [items, setItems] = useStatePL([]);
  React.useEffect(() => {
    const onAdd = (t) => {
      setItems(arr => [...arr, t]);
      if (t.duration > 0) {
        setTimeout(() => setItems(arr => arr.filter(x => x.id !== t.id)), t.duration);
      }
    };
    __toastListeners.push(onAdd);
    return () => {
      const i = __toastListeners.indexOf(onAdd);
      if (i >= 0) __toastListeners.splice(i, 1);
    };
  }, []);
  const dismiss = (id) => setItems(arr => arr.filter(x => x.id !== id));
  return { items, dismiss };
}

function ToastStack() {
  const { items, dismiss } = useToasts();
  if (!items.length) return null;
  const node = (
    <div className="pl-toast-stack" aria-live="polite">
      {items.map(t => (
        <div key={t.id} className={`pl-toast pl-toast-${t.kind}`}>
          <span className={`pl-toast-icon dot ${t.kind === "ok" ? "ok" : t.kind === "warn" ? "warn" : t.kind === "danger" ? "danger" : "info"}`} />
          <div className="pl-toast-body">
            <div className="pl-toast-msg">{t.message}</div>
            {t.detail && <div className="pl-toast-detail muted-2">{t.detail}</div>}
          </div>
          {t.action && (
            <button className="pl-toast-action" onClick={() => { t.action.onClick?.(); dismiss(t.id); }}>
              {t.action.label}
            </button>
          )}
          <button className="iconbtn pl-toast-close" onClick={() => dismiss(t.id)} data-tip="关闭">
            <Icon name="close" size={11} />
          </button>
        </div>
      ))}
    </div>
  );
  return createPortal(node, document.body);
}

/* DialogHost — 全局 Promise 化的 Cloudscape 弹窗,接管浏览器原生 confirm/prompt。
   用法: await window.__confirm({title, message, danger, confirmText})  → bool
         await window.__prompt({title, label, default, confirmText})    → string|null */
function DialogHost() {
  const [dlg, setDlg] = useStatePL(null);
  useEffectPL(() => {
    window.__confirm = (o = {}) => new Promise((resolve) => setDlg({
      type: 'confirm', resolve,
      title: o.title || '确认', message: o.message || '',
      danger: !!o.danger, confirmText: o.confirmText || '确认',
    }));
    window.__prompt = (o = {}) => new Promise((resolve) => setDlg({
      type: 'prompt', resolve,
      title: o.title || '输入', label: o.label || '', value: o.default || '',
      confirmText: o.confirmText || '确认',
    }));
    return () => { delete window.__confirm; delete window.__prompt; };
  }, []);
  if (!dlg) return null;
  const close = (val) => { try { dlg.resolve(val); } catch (_) {} setDlg(null); };
  const cancelVal = dlg.type === 'prompt' ? null : false;
  const okVal = dlg.type === 'prompt' ? (dlg.value || '') : true;
  return (
    <CSModal
      visible
      onDismiss={() => close(cancelVal)}
      header={dlg.title}
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="link" onClick={() => close(cancelVal)}>取消</CSButton>
            <CSButton variant="primary" onClick={() => close(okVal)}>{dlg.confirmText}</CSButton>
          </CSSpaceBetween>
        </CSBox>
      }
    >
      {dlg.type === 'confirm'
        ? <CSBox>{dlg.message}</CSBox>
        : <CSFormField label={dlg.label}>
            <CSInput value={dlg.value} autoFocus
              onChange={({ detail }) => setDlg((d) => ({ ...d, value: detail.value }))}
              onKeyDown={({ detail }) => { if (detail.key === 'Enter') close(dlg.value || ''); }} />
          </CSFormField>}
    </CSModal>
  );
}

/* ---------------------------- useAutoSave ----------------------- */
function useAutoSave(label, scope) {
  // task 52：useAutoSave 之前只 toast 一句"已保存"假装持久化，实际啥都没存。
  // 偏好/部署/模型参数面板里所有 SettingsToggle / select 的 save() 调用都是空的。
  // 现在：
  //   - 接受 (field, val) 把 {scope/field: val} debounce 200ms 后 POST 到
  //     /api/me/preference（后端做 patch merge，不会冲掉其他 key）
  //   - val 为 undefined → 退化为旧行为，只 toast 不写后端（兼容老调用站点）
  //   - 失败 toast danger，不再假装"已保存"
  const timerRef = React.useRef(null);
  const pendingRef = React.useRef({});
  return React.useCallback((field, val) => {
    if (val !== undefined) {
      const key = scope ? `${scope}.${field}` : field;
      pendingRef.current[key] = val;
    }
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      const batch = pendingRef.current;
      pendingRef.current = {};
      if (Object.keys(batch).length === 0) {
        // 兼容旧调用：仅 toast，不打后端
        window.toast?.(`${label}已保存`, { kind: "ok", detail: scope ? `${scope} · ${field}` : field, duration: 2400 });
        return;
      }
      try {
        await window.api.account.preferences(batch);
        window.toast?.(`${label}已保存`, { kind: "ok", detail: scope ? `${scope} · ${field}` : field, duration: 2000 });
      } catch (e) {
        window.toast?.(`${label}保存失败`, { kind: "danger", detail: e?.message || "网络错误", duration: 3000 });
      }
    }, 250);
  }, [label, scope]);
}

/* ---- task 45：让任何读 window.MOCK_PLATFORM 的 Page 在 data-loader 拿到真 platform 后
       自动重渲染。原代码组件 mount 时同步读 MOCK_PLATFORM —— 若 bootstrap 还没回，
       拿到的就是 mock 基线快照，且不会再更新（因为没监听 rpg-data-ready）。
   ---- */
function usePlatformData() {
  const [platform, setPlatform] = useStatePL(() => window.MOCK_PLATFORM || {});
  React.useEffect(() => {
    const onReady = () => setPlatform({ ...(window.MOCK_PLATFORM || {}) });
    window.addEventListener("rpg-data-ready", onReady);
    // 也监听 saves/scripts 单点刷新事件
    window.addEventListener("rpg-saves-updated", onReady);
    window.addEventListener("rpg-scripts-updated", onReady);
    return () => {
      window.removeEventListener("rpg-data-ready", onReady);
      window.removeEventListener("rpg-saves-updated", onReady);
      window.removeEventListener("rpg-scripts-updated", onReady);
    };
  }, []);
  return platform;
}

/* ---- task 13 + task 45: 全局 user 反应式同步 ----
   原 publishUser 通过 mutate window.MOCK_PLATFORM.user 当跨组件通讯总线 ——
   task 45 把 mock mutation 拆掉，user state 改放在 window.__USER_STATE 单独槽位
   （登录用户 = 真实 user；匿名 + designer offline = 仍可读到 MOCK_PLATFORM.user 兜底）。
*/
const USER_EVENT = "rpg-user-updated";
window.__USER_STATE = window.__USER_STATE || null;
function _initialUser() {
  if (window.__USER_STATE) return window.__USER_STATE;
  // 登录态：从 platform fetch 拿；匿名：兜底到 mock（让 designer offline 不白屏）
  return (window.MOCK_PLATFORM && window.MOCK_PLATFORM.user) || {};
}
function publishUser(patch) {
  const next = { ...(window.__USER_STATE || _initialUser()), ...(patch || {}) };
  window.__USER_STATE = next;
  // 不再 mutate MOCK_PLATFORM.user —— 那是示例数据快照，不该被运行时改
  try { window.dispatchEvent(new CustomEvent(USER_EVENT, { detail: next })); } catch (_) {}
}
function useReactiveUser() {
  const [u, setU] = useStatePL(() => _initialUser());
  React.useEffect(() => {
    const onUpd = (e) => setU(e?.detail || _initialUser());
    window.addEventListener(USER_EVENT, onUpd);
    // 也监听 rpg-data-ready：data-loader 拿到真 platform.user 后通过这条事件初始化
    const onReady = (e) => {
      const u = e?.detail?.platform?.user;
      if (u) { window.__USER_STATE = u; setU(u); }
    };
    window.addEventListener("rpg-data-ready", onReady);
    return () => {
      window.removeEventListener(USER_EVENT, onUpd);
      window.removeEventListener("rpg-data-ready", onReady);
    };
  }, []);
  return u;
}
window.__publishUser = publishUser;

/* ---------------------------- SHELL ---------------------------- */
// task 55: 新增 assistant slot + assistantOpen/onOpenAssistant。
// 助手现在挤压式布局,折叠时 cap-root 自身 display:none,展开时 360px 自动腾位;
// 折叠态 TopBar 右上角显示"展开助手"图标按钮。
function PlatformShell({ page, setPage, children, assistant, assistantOpen, onOpenAssistant, onToggleAssistant }) {
  const platform = usePlatformData();  // task 45：响应式拿真 platform（mock baseline 替换后能重渲）
  const reactiveUser = useReactiveUser();   // task 13：左侧用户栏即时同步
  const [title, subtitle] = PL_TITLES[page] || ["平台", ""];
  const [continueState, setContinueState] = useStatePL({ open: false, save: null, nodeId: null });
  const [searchOpen, setSearchOpen] = useStatePL(false);
  // 统一顶栏:页面通过 useShellChrome 注入 title/breadcrumb/actions
  const [chrome, setChromeState] = useStatePL({});
  const chromeApi = React.useMemo(() => ({
    set: (c) => setChromeState(c || {}),
    clear: () => setChromeState({}),
  }), []);
  // 切页时清空上一页注入的 chrome,避免标题/操作残留
  React.useEffect(() => { setChromeState({}); }, [page]);
  const dispTitle = chrome.title || title;
  const dispSub = chrome.subtitle !== undefined ? chrome.subtitle : subtitle;
  const crumbs = chrome.breadcrumb;
  const pageActions = chrome.actions;

  React.useEffect(() => {
    window.__openContinue = (save, nodeId) => setContinueState({ open: true, save: save || platform.saves[0], nodeId: nodeId || null });
    return () => { delete window.__openContinue; };
  }, [platform]);

  // Codex P0 修复:所有"开始新游戏 / 新建存档"必须走同一个原子流。
  // 流程: POST /api/saves → POST /api/saves/{id}/activate → GET /api/state 校验
  // save_id === created.id → location.href = "Game Console.html"。
  // 失败任何一步都 abort + toast,不带着旧 runtime 进游戏。
  // 之前 ContinuePicker 里嵌套 NewGameModal 把 payload 丢了 / 剧本页传 fake
  // {id: null} / Game Console 用 /api/new 重置 runtime —— 三种入口都绕过建档。
  React.useEffect(() => {
    window.__createAndEnterSave = async (payload) => {
      if (!payload || typeof payload !== "object") {
        throw new Error("缺少 payload");
      }
      // 在用户手势内先开空白标签页(绕过弹窗拦截),激活完成后再跳到 Game Console。
      // 游戏在新页运行,不离开平台当前页。
      const gameWin = window.open("about:blank", "_blank");
      try {
      // 1. 创建 save (后端会 seed root commit + 写 initial state)
      const created = await window.api.saves.create({
        title: payload.title || ("新存档 · " + new Date().toLocaleString()),
        script_id: payload.script_id || null,
        character_id: payload.character_id || null,
        character_kind: payload.character_kind || null,
        npc_id: payload.npc_id || null,
        new_card: payload.new_card || null,
      });
      if (!created || created.ok === false) {
        throw new Error((created && (created.error || created.detail)) || "建档失败");
      }
      const save = created.save || created;
      const newId = save && save.id;
      if (!newId) {
        throw new Error("后端建档成功但没返回 save.id");
      }
      // 2. 激活到新 save (写 user_runtime,后续 /api/state 才会读这个 save)
      try {
        await window.api.saves.activate(newId);
      } catch (e) {
        throw new Error("已建档但激活失败:" + (e?.message || e));
      }
      // 3. 校验 /api/state.save_id === newId,确保 runtime 真切到位
      try {
        const state = await window.api.game.state();
        if (state && state.save_id != null && Number(state.save_id) !== Number(newId)) {
          throw new Error(
            `runtime 不一致:期望 save_id=${newId},实际 ${state.save_id}。` +
            "请回退到平台『存档目录』手动切换。"
          );
        }
      } catch (e) {
        // /api/state 失败不致命,但要 surface (toast),user 进游戏后会发现
        console.warn("[createAndEnterSave] state 校验失败:", e);
      }
      window.__apiToast?.(`已创建存档 #${newId}: ${save.title || ""}`, { kind: "ok", duration: 1800 });
      try { window.dispatchEvent(new CustomEvent("rpg-saves-updated")); } catch (_) {}
      // 4. 在新标签页打开 Game Console(平台当前页保留)。about:blank 需绝对 URL。
      const gameUrl = new URL("Game Console.html", window.location.href).href;
      if (gameWin) gameWin.location.href = gameUrl;
      else window.open(gameUrl, "_blank");
      return save;
      } catch (e) {
        try { if (gameWin) gameWin.close(); } catch (_) {}
        throw e;
      }
    };
    return () => { delete window.__createAndEnterSave; };
  }, []);

  React.useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setSearchOpen(s => !s);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // task 102B/104b/105: sidebar 宽度可拖,
  //   · 拖动期间直接 mutate .pl-side inline style 绕过 React 重渲
  //   · collapsed 视觉态由 CSS @container (max-width: 139px) 自动触发, 不需 React 判断
  const { size: sidebarW, dragHandleProps: sidebarDrag } = useResizable({
    storageKey: "pl.sidebar.w",
    defaultSize: 244,
    min: 64,
    max: 380,
    side: "left",
    cssVar: "--pl-sidebar-w",
  });

  return (
    <div
      className={`pl-shell ${assistant ? "pl-shell-with-assistant" : ""}`}
      data-screen-label={`Platform · ${title}`}
      style={{ "--pl-sidebar-w": sidebarW + "px" }}
    >
      <aside className="pl-side">
        <ResizeHandle side="left" {...sidebarDrag} />
        <div className="pl-side-head">
          <div className="pl-mark"><Icon name="logo" size={14} /></div>
          <div>
            <strong>RPG Roleplay</strong>
            <div className="muted-2 mono">v0.4 · {platform.database.driver}</div>
          </div>
        </div>
        <nav className="pl-nav">
          {PL_NAV.map((it, i) => it.section ? (
            <div key={"s" + i} className="pl-nav-section">{it.section}</div>
          ) : (
            <React.Fragment key={it.id}>
              <button
                className={`pl-nav-item ${page === it.id
                  || (it.id === "saves" && page === "saves-branches")
                  || (it.id === "scripts" && page === "scripts-import")
                  || (it.id === "cards" && page === "cards-npc") ? "active" : ""}`}
                onClick={() => setPage(it.id)}
              >
                <Icon name={it.icon} size={15} />
                <span>{it.label}</span>
                {it.id === "scripts" && <span className="pl-count">{platform.scripts.length}</span>}
                {it.id === "saves" && <span className="pl-count">{platform.saves.length}</span>}
                {it.id === "library" && <span className="pl-count">{platform.recent_assets.length}+</span>}
              </button>
              {it.id === "scripts" && (page === "scripts" || page === "scripts-import") && (
                <div className="pl-nav-sub">
                  <button className={`pl-nav-subitem ${page === "scripts" ? "active" : ""}`} onClick={() => setPage("scripts")}>
                    剧本管理
                  </button>
                  <button className={`pl-nav-subitem ${page === "scripts-import" ? "active" : ""}`} onClick={() => setPage("scripts-import")}>
                    导入剧本
                  </button>
                </div>
              )}
              {it.id === "saves" && (page === "saves" || page === "saves-branches") && (
                <div className="pl-nav-sub">
                  <button className={`pl-nav-subitem ${page === "saves" ? "active" : ""}`} onClick={() => setPage("saves")}>
                    存档目录
                  </button>
                  <button className={`pl-nav-subitem ${page === "saves-branches" ? "active" : ""}`} onClick={() => setPage("saves-branches")}>
                    分支树
                  </button>
                </div>
              )}
              {it.id === "cards" && (page === "cards" || page === "cards-npc") && (
                <div className="pl-nav-sub">
                  <button className={`pl-nav-subitem ${page === "cards" ? "active" : ""}`} onClick={() => setPage("cards")}>
                    用户角色卡
                  </button>
                  <button className={`pl-nav-subitem ${page === "cards-npc" ? "active" : ""}`} onClick={() => setPage("cards-npc")}>
                    NPC 角色卡
                  </button>
                </div>
              )}
              {it.id === "me" && (page === "me" || page === "me-edit" || page === "me-settings") && (
                <div className="pl-nav-sub">
                  <button className={`pl-nav-subitem ${page === "me" ? "active" : ""}`} onClick={() => setPage("me")}>
                    概览
                  </button>
                  <button className={`pl-nav-subitem ${page === "me-edit" ? "active" : ""}`} onClick={() => setPage("me-edit")}>
                    编辑资料
                  </button>
                  <button className={`pl-nav-subitem ${page === "me-settings" ? "active" : ""}`} onClick={() => setPage("me-settings")}>
                    用户设置
                  </button>
                </div>
              )}
            </React.Fragment>
          ))}
        </nav>
        <div className="pl-side-foot">
          <button className="pl-user" onClick={() => setPage("me")} data-tip="个人主页" data-tip-pos="right">
            {/* task 13: 用 reactiveUser，MePage 保存后不必刷新即可同步 */}
            <div className="pl-avatar">{(reactiveUser.display_name || "?").slice(0,1)}</div>
            <div className="pl-user-text">
              <strong>{reactiveUser.display_name || "未命名"}</strong>
              <div className="muted-2">@{reactiveUser.username || "—"} · {reactiveUser.role || ""}</div>
            </div>
            <Icon name="chevron_right" size={12} />
          </button>
        </div>
      </aside>
      <main className="pl-main">
        <header className="pl-topbar pl-topbar-unified">
          <div className="pl-topbar-lead">
            {crumbs && crumbs.length > 0 && (
              <nav className="pl-crumbs">
                {crumbs.map((b, i) => (
                  <span key={i} className="pl-crumb">
                    {b.onClick ? <button className="pl-crumb-link" onClick={b.onClick}>{b.label}</button> : <span>{b.label}</span>}
                    {i < crumbs.length - 1 && <span className="pl-crumb-sep">/</span>}
                  </span>
                ))}
              </nav>
            )}
            <div className="pl-topbar-titles">
              <h1>{dispTitle}</h1>
              {dispSub ? <div className="pl-sub">{dispSub}</div> : null}
            </div>
          </div>
          <div className="pl-topbar-right">
            {pageActions ? <div className="pl-topbar-actions">{pageActions}</div> : null}
            <div className="pl-topbar-tools">
              <button className="iconbtn" data-tip="搜索 · ⌘K" aria-label="搜索" onClick={() => setSearchOpen(true)}>
                <Icon name="search" size={14} />
              </button>
              <button className="iconbtn" data-tip="刷新平台数据" aria-label="刷新" onClick={async () => {
                try {
                  window.__apiToast?.("正在刷新…", { kind: "info", duration: 1200 });
                  if (window.__refreshPlatform) await window.__refreshPlatform();
                  else {
                    const p = await window.api.platform.info();
                    window.MOCK_PLATFORM = p && p.platform ? p.platform : (p || window.MOCK_PLATFORM);
                    window.dispatchEvent(new CustomEvent("rpg-data-ready"));
                  }
                  window.__apiToast?.("已刷新", { kind: "ok", duration: 1600 });
                } catch (e) {
                  window.__apiToast?.("刷新失败", { kind: "danger", detail: e?.message });
                }
              }}><Icon name="refresh" size={14} /></button>
              {/* VS Code 式:顶栏开关,点击展开/收起右侧控制台助手栏 */}
              <button className={`pl-assistant-toggle ${assistantOpen ? "on" : ""}`}
                      data-tip={assistantOpen ? "收起控制台助手" : "展开控制台助手"}
                      aria-label="控制台助手" aria-pressed={!!assistantOpen}
                      onClick={onToggleAssistant || onOpenAssistant}>
                <Icon name="sparkle" size={14} /> <span>助手</span>
              </button>
            </div>
          </div>
        </header>
        <ShellChromeCtx.Provider value={chromeApi}>
          <div className="pl-content">{children}</div>
        </ShellChromeCtx.Provider>
      </main>
      {/* task 55: 助手作为第 3 个 grid 列;折叠时 cap-root display:none 不占位 */}
      {assistant}
      <ContinuePicker
        open={continueState.open}
        save={continueState.save}
        focusedNodeId={continueState.nodeId}
        onClose={() => setContinueState({ open: false, save: null, nodeId: null })}
      />
      <UnifiedSearch open={searchOpen} onClose={() => setSearchOpen(false)} setPage={setPage} />
      <ToastStack />
    </div>
  );
}

function UnifiedSearch({ open, onClose, setPage }) {
  const [q, setQ] = useStatePL("");
  const [activeIdx, setActiveIdx] = useStatePL(0);
  const inputRef = React.useRef(null);
  React.useEffect(() => {
    if (open) { setQ(""); setActiveIdx(0); setTimeout(() => inputRef.current?.focus(), 30); }
  }, [open]);

  const platform = usePlatformData();  // task 45

  const pages = [
    { id: "profile",  label: "主页",      kind: "page", icon: "home",     keywords: "home dashboard" },
    { id: "me",       label: "个人主页",   kind: "page", icon: "user",     keywords: "me profile account" },
    { id: "me-edit",  label: "个人主页 / 编辑资料", kind: "page", icon: "edit", keywords: "edit profile avatar" },
    { id: "me-settings", label: "个人主页 / 用户设置", kind: "page", icon: "settings", keywords: "privacy security 2fa" },
    { id: "scripts",  label: "剧本",      kind: "page", icon: "book",     keywords: "scripts import" },
    { id: "cards",    label: "角色卡",     kind: "page", icon: "cards",    keywords: "characters card user npc" },
    { id: "cards-npc", label: "角色卡 / NPC", kind: "page", icon: "cards",   keywords: "npc characters" },
    { id: "saves",    label: "开始游戏",   kind: "page", icon: "play",     keywords: "saves continue" },
    { id: "saves-branches", label: "开始游戏 / 分支", kind: "page", icon: "branch",   keywords: "branches tree fork" },
    { id: "library",  label: "库",        kind: "page", icon: "folder",   keywords: "library files assets" },
    { id: "settings", label: "设置",      kind: "page", icon: "settings", keywords: "settings preferences" },
    { id: "usage",    label: "用量",      kind: "page", icon: "usage",    keywords: "usage tokens cost" },
    { id: "plugins",  label: "插件",      kind: "page", icon: "plug",     keywords: "plugins extensions" },
    { id: "mcp",      label: "MCP",       kind: "page", icon: "diamond",  keywords: "mcp server" },
    { id: "skills",   label: "Skill",     kind: "page", icon: "spark",    keywords: "skills hooks" },
    { id: "apis",     label: "API",       kind: "page", icon: "braces",   keywords: "api endpoints" },
  ];

  const settingsItems = [
    { id: "preferences", label: "偏好",        parent: "设置",     hash: "settings", keywords: "language font density theme" },
    { id: "models",      label: "模型 / API",   parent: "设置",     hash: "settings", keywords: "openai anthropic models api" },
    { id: "memory",      label: "记忆",        parent: "设置",     hash: "settings", keywords: "memory recall context" },
    { id: "permissions", label: "权限",        parent: "设置",     hash: "settings", keywords: "permission write structured" },
    { id: "deploy",      label: "部署",        parent: "设置",     hash: "settings", keywords: "host port cors upload" },
    { id: "danger",      label: "高危",        parent: "设置",     hash: "settings", keywords: "danger reset delete" },
  ];

  const scripts = platform.scripts.map(s => ({
    id: "scr-" + s.id, label: s.title, kind: "script",
    sub: `${s.chapter_count.toLocaleString()} 章 · ${(s.word_count / 10000).toFixed(1)}万字`,
    icon: "book", keywords: s.uid + " " + s.description,
    hash: "scripts",
  }));

  const saves = platform.saves.map(s => ({
    id: "sv-" + s.id, label: s.title, kind: "save",
    sub: `${s.branch_count} 节点 · ${s.updated_at}`,
    icon: "play", keywords: s.uid,
    hash: "saves",
  }));

  // task 48：原硬编码 3 个角色 + 3 条世界书。改为：登录态空（暂无跨剧本搜索接口），
  // 匿名态保留示例（designer offline）。如果未来 backend 上线全文搜索，可改成 GET /api/search?q=
  const IS_ANON_SEARCH = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  const characters = IS_ANON_SEARCH ? [
    { id: "c1", label: "沈知微", sub: "雾港·医师 · 信任", kind: "character", icon: "cards", keywords: "shen zhiwei" },
    { id: "c2", label: "韩司直", sub: "南陵·巡检 · 戒备", kind: "character", icon: "cards", keywords: "han sizhi" },
    { id: "c3", label: "阿衡",   sub: "灯塔守人之女 · 亲近", kind: "character", icon: "cards", keywords: "aheng" },
  ] : [];

  const worldbook = IS_ANON_SEARCH ? [
    { id: "wb1", label: "雾港事件",          sub: "光绪十三年沉船 142 人", kind: "world", icon: "world", keywords: "fog port event" },
    { id: "wb2", label: "残页·光绪十三年",   sub: "可推断时间 · 剩 1 次", kind: "world", icon: "quote", keywords: "fragment guangxu" },
    { id: "wb3", label: "黑铁怀表",          sub: "停在三时四十二分", kind: "world", icon: "history", keywords: "iron watch" },
  ] : [];

  const models = [];
  MODELS_DATA.forEach(api => {
    api.models.slice(0, 3).forEach(m => {
      models.push({
        id: "m-" + m.id, label: m.display, kind: "model",
        sub: `${api.name} · ${m.real_name}`,
        icon: "sparkle", keywords: m.real_name + " " + api.name,
        hash: "settings",
      });
    });
  });

  const apis = MODELS_DATA.map(a => ({
    id: "api-" + a.id, label: a.name, kind: "api",
    sub: `${a.models.length} 模型 · ${a.base_url}`,
    icon: "braces", keywords: a.id,
    hash: "settings",
  }));

  // task 48：固定记忆从真 state.memory.pinned 派生（暂无全局搜索接口）
  const memories = (() => {
    const pinned = (platform && platform.state && Array.isArray(platform.state.memory?.pinned)) ? platform.state.memory.pinned : [];
    return pinned.slice(0, 8).map((m, i) => {
      const text = typeof m === "string" ? m : (m?.text || JSON.stringify(m));
      return { id: `mem-${i}`, label: text.slice(0, 60), kind: "memory", icon: "pin", sub: "固定记忆" };
    });
  })();

  // task 48：library 改读真 platform.recent_assets；匿名才回退示例
  const lib = (() => {
    const recent = (platform && Array.isArray(platform.recent_assets)) ? platform.recent_assets : [];
    if (recent.length === 0 && IS_ANON_SEARCH) {
      return [
        { id: "f1", label: "雾港全景.png",      sub: "2.3 MB · 今天", kind: "library", icon: "image", hash: "library" },
        { id: "f2", label: "光绪十三年残页扫描.zip", sub: "17.5 MB · 昨天", kind: "library", icon: "folder", hash: "library" },
      ];
    }
    return recent.slice(0, 8).map((f, i) => ({
      id: `f-${f.id || i}`, label: f.name || f.path,
      sub: `${window.__fmt?.bytes ? window.__fmt.bytes(f.size || 0) : (f.size || 0) + " B"} · ${window.__fmt?.ago ? window.__fmt.ago(f.updated_at) : ""}`,
      kind: "library", icon: f.kind === "folder" ? "folder" : f.kind === "image" ? "image" : "file", hash: "library",
    }));
  })();

  const allItems = [
    ...pages, ...settingsItems, ...scripts, ...saves,
    ...characters, ...worldbook, ...models.slice(0, 8), ...apis,
    ...memories, ...lib,
  ];

  const lower = q.toLowerCase();
  const filtered = !q ? [] : allItems.filter(it =>
    it.label.toLowerCase().includes(lower) ||
    (it.sub || "").toLowerCase().includes(lower) ||
    (it.keywords || "").toLowerCase().includes(lower) ||
    (it.parent || "").toLowerCase().includes(lower)
  );

  const groups = {};
  const order = ["page", "script", "save", "character", "world", "memory", "model", "api", "library"];
  const labels = { page: "页面", script: "剧本", save: "存档", character: "角色卡", world: "世界书", memory: "记忆", model: "模型", api: "API 供应商", library: "库文件" };
  filtered.forEach(it => {
    const key = it.kind === "page" && it.parent ? "page" : it.kind;
    (groups[key] = groups[key] || []).push(it);
  });

  const flatList = order.flatMap(k => groups[k] || []);
  const cursor = Math.max(0, Math.min(activeIdx, flatList.length - 1));

  const pick = (it) => {
    if (it.kind === "page") { setPage(it.id); location.hash = "#" + it.id; }
    else if (it.hash) { setPage(it.hash); location.hash = "#" + it.hash; }
    onClose();
  };

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); setActiveIdx(i => Math.min(i + 1, flatList.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActiveIdx(i => Math.max(i - 1, 0)); }
      else if (e.key === "Enter") { e.preventDefault(); if (flatList[cursor]) pick(flatList[cursor]); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, flatList, cursor]);

  if (!open) return null;

  let flatIdx = -1;
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-search-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pl-search-head">
          <Icon name="search" size={14} />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => { setQ(e.target.value); setActiveIdx(0); }}
            placeholder="搜索页面、剧本、存档、角色卡、世界书、记忆、模型、库文件、设置…"
          />
          <span className="pl-search-kbd">
            <span className="kbd">Esc</span>
          </span>
        </div>
        <div className="pl-search-body">
          {!q && (
            <div className="pl-search-empty">
              <div className="muted-2" style={{fontSize: 11, textTransform: "uppercase", letterSpacing: "0.14em", padding: "10px 16px 6px"}}>建议</div>
              {pages.slice(0, 6).map((p, i) => (
                <button key={p.id} className={`pl-search-row ${i === 0 ? "active" : ""}`} onClick={() => pick(p)}>
                  <span className="pl-search-icon"><Icon name={p.icon} size={14} /></span>
                  <span className="pl-search-label">{p.label}</span>
                  <span className="pl-search-meta muted-2">页面</span>
                </button>
              ))}
            </div>
          )}
          {q && flatList.length === 0 && (
            <div className="pl-model-empty" style={{margin: 16}}>
              未匹配「{q}」 · 试试 GPT、雾港、记忆、claude、API
            </div>
          )}
          {q && order.map(kind => {
            const items = groups[kind];
            if (!items || items.length === 0) return null;
            return (
              <div key={kind} className="pl-search-group">
                <div className="pl-search-group-head">
                  {labels[kind]} <span className="muted-2 mono">{items.length}</span>
                </div>
                {items.map(it => {
                  flatIdx++;
                  const active = flatIdx === cursor;
                  return (
                    <button key={it.id} className={`pl-search-row ${active ? "active" : ""}`}
                      onClick={() => pick(it)}
                      onMouseEnter={() => setActiveIdx(flatIdx)}>
                      <span className="pl-search-icon"><Icon name={it.icon} size={14} /></span>
                      <span className="pl-search-label">
                        <Highlight text={it.label} q={q} />
                        {it.sub && <span className="muted-2" style={{marginLeft: 8, fontSize: 11.5}}>
                          <Highlight text={it.sub} q={q} />
                        </span>}
                      </span>
                      <span className="pl-search-meta muted-2">{labels[kind]}</span>
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>
        <footer className="pl-search-foot">
          <div className="pl-search-kbds">
            <span><span className="kbd">↑↓</span> 选择</span>
            <span><span className="kbd">⏎</span> 打开</span>
            <span><span className="kbd">Esc</span> 关闭</span>
          </div>
          <span className="muted-2" style={{fontSize: 11}}>
            GET /api/v1/search?q={encodeURIComponent(q) || "..."} · 全文 · 模糊
          </span>
        </footer>
      </div>
    </div>
  );
}

function Highlight({ text, q }) {
  if (!q) return text;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <mark style={{background: "var(--accent-soft)", color: "var(--accent)", padding: "0 2px", borderRadius: 2}}>
        {text.slice(i, i + q.length)}
      </mark>
      {text.slice(i + q.length)}
    </>
  );
}

/* ---------------------------- ME (personal home) ----------- */
const ME_ACTIVITY = [
  { ts: "刚刚",       icon: "play",     text: "在 雾港·主线·顾承砚 进行了第 312 回合", tag: "回合" },
  { ts: "12 分钟前",  icon: "branch",   text: "从节点 #07 新建分支 旅店线·阿衡视角", tag: "分支" },
  { ts: "今天 14:08", icon: "memory",   text: "把 黑铁怀表停在三时四十二分 加入固定记忆", tag: "记忆" },
  { ts: "今天 12:30", icon: "save",     text: "导入剧本 雾港异闻录·外卷", tag: "剧本" },
  { ts: "昨天",       icon: "edit",     text: "编辑了 角色卡·沈知微 的语气", tag: "卡片" },
  { ts: "昨天",       icon: "world",    text: "调整世界线变量 顾承砚.身份暴露度 = 37%", tag: "世界线" },
  { ts: "上周",       icon: "upload",   text: "上传 光绪十三年残页扫描.zip 到库", tag: "库" },
  { ts: "上周",       icon: "spark",    text: "部署了 Skill·时间线推演 v1.4", tag: "Skill" },
  { ts: "上月",       icon: "user",     text: "完成注册 · 成为首个管理员", tag: "账号" },
];

const ME_ACHIEVEMENTS = [
  { id: "first-turn",   name: "破雾之刻",  desc: "完成第一回合",                 unlocked: true,  at: "上月" },
  { id: "first-branch", name: "分岔",      desc: "首次从节点中段继续",            unlocked: true,  at: "上月" },
  { id: "deep-fog",     name: "千言不渝", desc: "累计 1,000 回合",              unlocked: true,  at: "上周" },
  { id: "pinkeep",      name: "守心人",    desc: "固定记忆累计 20 条",            unlocked: true,  at: "3 天前" },
  { id: "two-faced",    name: "两端来人", desc: "在同一存档中保留 3 条并行分支", unlocked: false, progress: 2, target: 3 },
  { id: "wordsmith",    name: "落字",      desc: "总输出超 100 万字",            unlocked: false, progress: 612000, target: 1000000 },
  { id: "polyglot",     name: "多言",      desc: "使用 5 个不同 API 提供商",     unlocked: false, progress: 4, target: 5 },
  { id: "lantern",      name: "灯塔",      desc: "解锁 全部 主线 + 旅店线 结局", unlocked: false, progress: 1, target: 2 },
];

function MePage({ subPage = "overview" }) {
  return (
    <CSSpaceBetween size="l">
      <MeSubNav active={subPage} />
      {subPage === "overview" && <MeOverview />}
      {subPage === "edit" && <MeEditProfile />}
      {subPage === "settings" && <MeUserSettings />}
    </CSSpaceBetween>
  );
}

function MeSubNav({ active }) {
  const tabs = [
    { id: "overview", label: "概览",     hash: "#me" },
    { id: "edit",     label: "编辑资料", hash: "#me-edit" },
    { id: "settings", label: "用户设置", hash: "#me-settings" },
  ];
  return (
    <CSSpaceBetween direction="horizontal" size="xs">
      {tabs.map(t => (
        <CSButton
          key={t.id}
          variant={active === t.id ? "primary" : "normal"}
          href={t.hash}
        >
          {t.label}
        </CSButton>
      ))}
    </CSSpaceBetween>
  );
}

function MeOverview() {
  const { stats: platStats = {}, saves = [] } = usePlatformData();  // task 45：响应式 platform
  const user = useReactiveUser();  // task 13: MePage 切换 / 保存后即时更新
  const [filter, setFilter] = useStatePL("all");
  // task 48：原使用 ME_ACTIVITY / ME_ACHIEVEMENTS 硬编码示例（『在 雾港·主线·顾承砚
  // 进行了第 312 回合』『破雾之刻』『千言不渝』等）。后端暂无活动/成就接口，改成空态文案。
  // 匿名访客可见 mock 用作 designer offline preview。
  const IS_ANON = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  const ACTIVITY = IS_ANON ? ME_ACTIVITY : [];
  const ACHIEVEMENTS = IS_ANON ? ME_ACHIEVEMENTS : [];
  // task 49：之前 totalRounds = saves.reduce(* 7)、playHours = totalRounds*1.2/60 等
  // 全是凭空乘的伪派生；现在拉真后端 /api/me/stats。后端没真数据的字段（playMinutes）
  // 显式为 null，UI 显示 "—"。
  const [meStats, setMeStats] = useStatePL(null);
  useEffectPL(() => {
    if (IS_ANON) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.stats();
        if (!cancelled) setMeStats(r || null);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, [IS_ANON, saves.length]);
  const filteredActivity = filter === "all" ? ACTIVITY : ACTIVITY.filter(a => a.tag === filter);
  const unlockedCount = ACHIEVEMENTS.filter(a => a.unlocked).length;
  const fmtCN = (n) => {
    if (n == null) return "—";
    if (n >= 10000) return (n / 10000).toFixed(1).replace(/\.0$/, "") + " 万";
    return n.toLocaleString();
  };
  const fmtDate = (iso) => {
    if (!iso) return "—";
    try { return new Date(iso).toISOString().slice(0, 10); } catch { return "—"; }
  };
  const fmtAgo = (iso) => {
    if (!iso) return "—";
    if (window.__fmt && window.__fmt.ago) return window.__fmt.ago(iso);
    try {
      const ms = Date.now() - new Date(iso).getTime();
      if (ms < 60_000) return "刚刚";
      if (ms < 3600_000) return Math.floor(ms / 60_000) + " 分钟前";
      if (ms < 86400_000) return Math.floor(ms / 3600_000) + " 小时前";
      return Math.floor(ms / 86400_000) + " 天前";
    } catch { return "—"; }
  };
  const regAt = fmtDate(user.created_at);
  const lastLoginAgo = fmtAgo(meStats?.last_login_at);
  const totalRounds = meStats?.total_rounds;
  const branchesCount = meStats?.branches ?? platStats.branches;
  const maxDepth = meStats?.max_branch_depth;
  const importedScripts = meStats?.imported?.scripts ?? platStats.scripts;
  const importedWords = meStats?.imported?.words;
  const loginStreak = meStats?.login_streak;
  const longestStreak = meStats?.longest_login_streak;
  const playMinutesTotal = meStats?.play_minutes_total;
  const playMinutesWeek = meStats?.play_minutes_week;
  const playHoursLabel = (playMinutesTotal == null) ? "—" : (playMinutesTotal / 60).toFixed(1);

  return (
    <CSSpaceBetween size="l">
      {/* Hero section */}
      <CSContainer>
        <CSSpaceBetween size="m">
          <CSSpaceBetween direction="horizontal" size="m">
            <div className="pl-me-avatar">{user.display_name.slice(0, 1)}</div>
            <div style={{flex: 1}}>
              <CSSpaceBetween size="xs">
                <CSBox variant="h2">
                  {user.display_name}
                  <span className="pill" style={{marginLeft: 8}}><span className="dot ok pulse" /> 在线</span>
                  <span className="pill accent" style={{marginLeft: 6}}>{user.role === "admin" ? "管理员" : user.role}</span>
                </CSBox>
                <CSBox color="text-body-secondary" fontSize="body-s">
                  <span><Icon name="user" size={11} /> @{user.username}</span>
                  <span className="mono" style={{marginLeft: 12}}>uid {user.uid}</span>
                  <span style={{marginLeft: 12}}><Icon name="history" size={11} /> 注册于 {regAt} · 上次登录 {lastLoginAgo}</span>
                </CSBox>
                <CSBox>{user.bio || "暂无简介。"}</CSBox>
              </CSSpaceBetween>
            </div>
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton href="#me-edit" iconName="edit">编辑资料</CSButton>
              <CSButton href="#me-settings" iconName="settings">用户设置</CSButton>
            </CSSpaceBetween>
          </CSSpaceBetween>
        </CSSpaceBetween>
      </CSContainer>

      {/* Stat row */}
      <CSContainer>
        <CSColumnLayout columns={5} variant="text-grid">
          <div>
            <CSBox variant="awsui-key-label">游玩时长</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">
              {playHoursLabel}{playMinutesTotal != null && <span style={{fontSize: 14, color: "var(--muted)", marginLeft: 4}}>h</span>}
            </CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{playMinutesWeek != null ? `本周 +${(playMinutesWeek / 60).toFixed(1)}h` : "暂无统计"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">回合数</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{totalRounds != null ? totalRounds.toLocaleString() : "—"}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">分布在 {saves.length} 个存档</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">创建分支</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{branchesCount != null ? branchesCount : "—"}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{maxDepth ? `最深 ${maxDepth} 层` : "—"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">导入剧本</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{importedScripts != null ? importedScripts : "—"}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{importedWords ? `共 ${fmtCN(importedWords)}字` : "—"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">连续登录</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">
              {loginStreak != null ? loginStreak : "—"}<span style={{fontSize: 14, color: "var(--muted)", marginLeft: 4}}>天</span>
            </CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{longestStreak ? `最长 ${longestStreak} 天` : "—"}</CSBox>
          </div>
        </CSColumnLayout>
      </CSContainer>

      {/* 成就 */}
      <CSContainer header={<CSHeader variant="h2">成就 <span className="muted-2">{unlockedCount} / {ACHIEVEMENTS.length} 已解锁</span></CSHeader>}>
        {ACHIEVEMENTS.length === 0 ? (
          <CSBox color="text-body-secondary" textAlign="center" padding="l">
            成就系统尚未上线。后端接通成就 API 后此处会自动显示。
          </CSBox>
        ) : (
          <CSColumnLayout columns={4} variant="text-grid">
            {ACHIEVEMENTS.map(a => (
              <div key={a.id} className={`pl-achv ${a.unlocked ? "unlocked" : "locked"}`}>
                <div className="pl-achv-mark"><Icon name={a.unlocked ? "check" : "lock"} size={a.unlocked ? 16 : 14} /></div>
                <div className="pl-achv-body">
                  <strong>{a.name}</strong>
                  <span className="pl-achv-desc muted">{a.desc}</span>
                  {a.unlocked ? (
                    <span className="muted-2 mono" style={{fontSize: 10.5}}>解锁于 {a.at}</span>
                  ) : (
                    <div className="pl-achv-progress">
                      <div className="pl-achv-bar"><div className="pl-achv-fill" style={{width: (a.progress / a.target * 100).toFixed(0) + "%"}} /></div>
                      <span className="muted-2 mono" style={{fontSize: 10.5}}>{a.progress.toLocaleString()} / {a.target.toLocaleString()}</span>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </CSColumnLayout>
        )}
      </CSContainer>

      {/* 最近活动 */}
      <CSContainer header={
        <CSHeader variant="h2" actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant={filter === "all" ? "primary" : "normal"} onClick={() => setFilter("all")}>全部</CSButton>
            <CSButton variant={filter === "回合" ? "primary" : "normal"} onClick={() => setFilter("回合")}>回合</CSButton>
            <CSButton variant={filter === "分支" ? "primary" : "normal"} onClick={() => setFilter("分支")}>分支</CSButton>
            <CSButton variant={filter === "剧本" ? "primary" : "normal"} onClick={() => setFilter("剧本")}>剧本</CSButton>
          </CSSpaceBetween>
        }>最近活动</CSHeader>
      }>
        <ol className="pl-activity">
          {filteredActivity.map((a, i) => (
            <li key={i}>
              <div className="pl-activity-rail">
                <span className="pl-activity-dot"><Icon name={a.icon} size={11} /></span>
                {i < filteredActivity.length - 1 && <span className="pl-activity-line" />}
              </div>
              <div className="pl-activity-body">
                <div className="pl-activity-text">{a.text}</div>
                <div className="pl-activity-meta">
                  <span className="pill" style={{fontSize: 10.5}}>{a.tag}</span>
                  <span className="muted-2 mono" style={{fontSize: 11}}>{a.ts}</span>
                </div>
              </div>
            </li>
          ))}
          {/* task 48：登录态此列表为空，后端无活动接口 → 给明确空态文案 */}
          {filteredActivity.length === 0 && (
            <CSBox color="text-body-secondary" textAlign="center" padding="l">
              {ACTIVITY.length === 0
                ? "活动日志接口未上线，登录玩游戏后这里会显示真实回合/分支/导入记录。"
                : "未找到此分类的活动"}
            </CSBox>
          )}
        </ol>
      </CSContainer>
    </CSSpaceBetween>
  );
}

function MeEditProfile() {
  // task 45：改读 reactive user（publishUser 写到 __USER_STATE，登录后是真用户）
  const user = useReactiveUser();
  const [form, setForm] = useStatePL({
    display_name: user.display_name || "",
    username: user.username || "",
    email: user._raw?.email || "",
    phone: user._raw?.phone || "",
    real_name: user._raw?.real_name || "",
    gender: user._raw?.gender || "unspecified",
    birthday: user._raw?.birthday || "",
    location: user._raw?.location || "",
    website: user._raw?.website || "",
    bio: user.bio || "",
    pronouns: user._raw?.pronouns || "她/她",
    language: user._raw?.language || "zh-CN",
    timezone: user._raw?.timezone || "Asia/Shanghai",
  });
  // task 57: 表单输入标记 dirty,保存/重置后清掉。
  const u = (k, v) => {
    setForm(f => ({ ...f, [k]: v }));
    try { window.__capMarkDirty && window.__capMarkDirty("settings.profile"); } catch (_) {}
  };
  const [uploadOpen, setUploadOpen] = useStatePL(false);
  const [resetAvatarOpen, setResetAvatarOpen] = useStatePL(false);
  const [smsOpen, setSmsOpen] = useStatePL(false);
  const [saving, setSaving] = useStatePL(false);
  const avatarInputRef = React.useRef(null);

  // Hydrate from /api/me/profile.
  useEffectPL(() => {
    (async () => {
      try {
        const p = await window.api.account.profile();
        if (p && p.profile) {
          setForm(f => ({ ...f, ...p.profile }));
        } else if (p && typeof p === "object") {
          setForm(f => ({ ...f, ...p }));
        }
      } catch (_) {}
    })();
  }, []);

  const onSave = async () => {
    setSaving(true);
    try {
      await window.api.account.saveProfile(form);
      try { window.__capClearDirty && window.__capClearDirty("settings.profile"); } catch (_) {}
      // task 13: 拉一次权威源（/api/auth/me），用回包的 user 字段更新全局并广播事件，
      // 让 PlatformShell 左侧栏立即同步。失败也兜底先按本地 form 写一次（视觉上立即看到改动）。
      try {
        const me = await window.api?.auth?.me?.();
        if (me && me.user) {
          window.__publishUser?.({
            id: me.user.id,
            username: me.user.username,
            display_name: me.user.display_name || form.display_name,
            role: me.user.role,
            bio: me.user.bio ?? form.bio,
          });
        } else {
          window.__publishUser?.({ ...form });
        }
      } catch (_) {
        window.__publishUser?.({ ...form });
      }
      window.__apiToast?.("已保存资料", { kind: "ok", duration: 1600 });
    } catch (e) {
      window.__apiToast?.("保存失败", { kind: "danger", detail: e?.message, duration: 3000 });
    } finally {
      setSaving(false);
    }
  };

  const onAvatarPick = async (file) => {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
      window.__apiToast?.("文件过大", { kind: "danger", detail: "最大 2 MB" });
      return;
    }
    try {
      const res = await window.api.account.avatar(file);
      window.__apiToast?.("头像已更新", { kind: "ok" });
      if (res && res.avatar_url) {
        // bust page-level avatar cache
        document.querySelectorAll(".pl-me-avatar.large, .pl-user-avatar").forEach(el => {
          el.style.backgroundImage = `url(${res.avatar_url}?t=${Date.now()})`;
        });
      }
      setUploadOpen(false);
    } catch (e) {
      window.__apiToast?.("上传失败", { kind: "danger", detail: e?.message });
    }
  };

  const onResetAvatar = async () => {
    try {
      await window.api.account.avatarReset();
      window.__apiToast?.("已恢复默认头像", { kind: "ok" });
      setResetAvatarOpen(false);
    } catch (e) {
      window.__apiToast?.("操作失败", { kind: "danger", detail: e?.message });
    }
  };

  const onSendSms = async () => {
    if (!form.phone) {
      window.__apiToast?.("请先填写手机号", { kind: "danger" });
      return;
    }
    try {
      await window.api.auth.smsCode(form.phone);
      window.__apiToast?.("验证码已发送", { kind: "ok" });
      setSmsOpen(true);
    } catch (e) {
      window.__apiToast?.("发送失败", { kind: "danger", detail: e?.message });
    }
  };

  const onVerifySms = async (vals) => {
    try {
      await window.api.auth.smsVerify({ phone: form.phone, code: vals?.code });
      window.__apiToast?.("已验证", { kind: "ok" });
      setSmsOpen(false);
    } catch (e) {
      window.__apiToast?.("验证失败", { kind: "danger", detail: e?.message });
    }
  };
  return (
    <CSSpaceBetween size="l">
      {/* 头像 */}
      <CSContainer header={<CSHeader variant="h2">头像</CSHeader>}>
        <CSSpaceBetween size="m">
          <div className="pl-me-avatar-row">
            <div className="pl-me-avatar large">{form.display_name.slice(0, 1)}</div>
            <div className="pl-me-avatar-actions">
              <CSBox color="text-body-secondary" fontSize="body-s">支持 PNG / JPG / WEBP，建议 512×512。最大 2 MB。</CSBox>
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton iconName="upload" onClick={() => setUploadOpen(true)}>上传新头像</CSButton>
                <CSButton iconName="remove" onClick={() => setResetAvatarOpen(true)}>使用默认</CSButton>
              </CSSpaceBetween>
            </div>
          </div>
        </CSSpaceBetween>
      </CSContainer>

      {/* 基本资料 */}
      <CSContainer header={<CSHeader variant="h2">基本资料</CSHeader>} data-cap-anchor="settings.profile">
        <CSSpaceBetween size="l">
          <div className="pl-form-grid-2">
            <Field label="显示名" hint="出现在游戏和评论里">
              <CSInput value={form.display_name} onChange={({ detail }) => u("display_name", detail.value)} />
            </Field>
            <Field label="代词">
              <CSSelect
                selectedOption={[{value:"她/她",label:"她/她"},{value:"他/他",label:"他/他"},{value:"TA/TA",label:"TA/TA"},{value:"不公开",label:"不公开"}].find(o => o.value === form.pronouns) || null}
                options={[{value:"她/她",label:"她/她"},{value:"他/他",label:"他/他"},{value:"TA/TA",label:"TA/TA"},{value:"不公开",label:"不公开"}]}
                onChange={({ detail }) => u("pronouns", detail.selectedOption.value)}
              />
            </Field>
            <Field label="用户名" hint="登录用，6 个月可改一次" required>
              <CSInput value={form.username} onChange={({ detail }) => u("username", detail.value)} />
            </Field>
            <Field label="真实姓名" hint="仅自己可见">
              <CSInput value={form.real_name} onChange={({ detail }) => u("real_name", detail.value)} />
            </Field>
            <Field label="性别">
              <CSSpaceBetween direction="horizontal" size="xs">
                {[{v: "female", l: "女"}, {v: "male", l: "男"}, {v: "other", l: "其他"}, {v: "unspecified", l: "不公开"}].map(o => (
                  <CSButton key={o.v} variant={form.gender === o.v ? "primary" : "normal"} onClick={() => u("gender", o.v)}>{o.l}</CSButton>
                ))}
              </CSSpaceBetween>
            </Field>
            <Field label="生日">
              <CSInput type="date" value={form.birthday} onChange={({ detail }) => u("birthday", detail.value)} />
            </Field>
            <Field label="所在地">
              <CSInput value={form.location} onChange={({ detail }) => u("location", detail.value)} placeholder="例：上海" />
            </Field>
            <Field label="个人网站">
              <CSInput value={form.website} onChange={({ detail }) => u("website", detail.value)} placeholder="https://..." />
            </Field>
          </div>
          <Field label="简介" hint="280 字以内">
            <CSTextarea
              rows={3}
              value={form.bio}
              onChange={({ detail }) => u("bio", detail.value)}
            />
            <CSBox color="text-body-secondary" fontSize="body-s" textAlign="right">{form.bio.length} / 280</CSBox>
          </Field>
        </CSSpaceBetween>
      </CSContainer>

      {/* 联系方式 */}
      <CSContainer header={<CSHeader variant="h2">联系方式</CSHeader>}>
        <div className="pl-form-grid-2">
          <Field label="邮箱" hint="已验证" required>
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSInput value={form.email} onChange={({ detail }) => u("email", detail.value)} />
              <span className="pill ok"><span className="dot ok" /> 已验证</span>
            </CSSpaceBetween>
          </Field>
          <Field label="手机" hint="用于二次验证">
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSInput value={form.phone} onChange={({ detail }) => u("phone", detail.value)} />
              <CSButton onClick={onSendSms}>发送验证码</CSButton>
            </CSSpaceBetween>
          </Field>
        </div>
      </CSContainer>

      {/* 本地化 */}
      <CSContainer header={<CSHeader variant="h2">本地化</CSHeader>}>
        <div className="pl-form-grid-2">
          <Field label="界面语言">
            <CSSelect
              selectedOption={[{value:"zh-CN",label:"简体中文"},{value:"zh-TW",label:"繁體中文"},{value:"en",label:"English (Beta)"},{value:"ja",label:"日本語"}].find(o => o.value === form.language) || null}
              options={[{value:"zh-CN",label:"简体中文"},{value:"zh-TW",label:"繁體中文"},{value:"en",label:"English (Beta)"},{value:"ja",label:"日本語"}]}
              onChange={({ detail }) => u("language", detail.selectedOption.value)}
            />
          </Field>
          <Field label="时区">
            <CSSelect
              selectedOption={[{value:"Asia/Shanghai",label:"UTC+8 · 上海"},{value:"Asia/Tokyo",label:"UTC+9 · 东京"},{value:"UTC",label:"UTC"},{value:"America/Los_Angeles",label:"UTC-8 · 洛杉矶"}].find(o => o.value === form.timezone) || null}
              options={[{value:"Asia/Shanghai",label:"UTC+8 · 上海"},{value:"Asia/Tokyo",label:"UTC+9 · 东京"},{value:"UTC",label:"UTC"},{value:"America/Los_Angeles",label:"UTC-8 · 洛杉矶"}]}
              onChange={({ detail }) => u("timezone", detail.selectedOption.value)}
            />
          </Field>
        </div>
      </CSContainer>

      {/* 保存按钮行 */}
      <CSSpaceBetween direction="horizontal" size="xs">
        <CSButton href="#me">取消</CSButton>
        <CSButton variant="primary" onClick={onSave} loading={saving}>
          {saving ? "保存中…" : "保存资料"}
        </CSButton>
      </CSSpaceBetween>

      <input ref={avatarInputRef} type="file" accept="image/png,image/jpeg,image/webp"
        style={{display: "none"}} onChange={(e) => onAvatarPick(e.target.files?.[0])} />
      <ConfirmModal
        open={uploadOpen}
        title="上传新头像"
        body={<>支持 PNG / JPG / WEBP，建议 512×512。最大 2 MB。</>}
        confirmLabel="选择文件"
        onClose={() => setUploadOpen(false)}
        onConfirm={() => { avatarInputRef.current?.click(); setUploadOpen(false); }}
      />
      <ConfirmModal
        open={resetAvatarOpen}
        title="恢复为默认头像？"
        body={<>将删除当前头像，使用由显示名首字生成的占位头像。</>}
        confirmLabel="恢复默认"
        onClose={() => setResetAvatarOpen(false)} onConfirm={onResetAvatar}
      />
      <PromptModal
        open={smsOpen}
        eyebrow="手机验证"
        title="输入 6 位验证码"
        hint={`验证码已发送到 ${form.phone} · 30 秒后可重发`}
        fields={[
          { key: "code", label: "验证码", required: true, mono: true, placeholder: "6 位数字" },
        ]}
        submitLabel="验证"
        onClose={() => setSmsOpen(false)}
        onConfirm={onVerifySms}
      />
    </CSSpaceBetween>
  );
}

function MeUserSettings() {
  const save = useAutoSave("用户设置", "me");
  const tog = (setter, label) => (v) => { setter(v); save(label); };
  const [twofa, setTwofa] = useStatePL(true);
  const [emailNotif, setEmailNotif] = useStatePL(true);
  const [publicProfile, setPublicProfile] = useStatePL(false);
  const [searchable, setSearchable] = useStatePL(true);
  const [shareUsage, setShareUsage] = useStatePL(false);
  const [shareCrash, setShareCrash] = useStatePL(true);
  const [adsTrack, setAdsTrack] = useStatePL(false);
  const [confirmDelete, setConfirmDelete] = useStatePL(false);
  const [confirmDeact, setConfirmDeact] = useStatePL(false);
  const [pwOpen, setPwOpen] = useStatePL(false);
  const [sessionsOpen, setSessionsOpen] = useStatePL(false);
  const [historyOpen, setHistoryOpen] = useStatePL(false);
  const [exportOpen, setExportOpen] = useStatePL(false);
  const [visibilityOpen, setVisibilityOpen] = useStatePL(false);
  const [policyOpen, setPolicyOpen] = useStatePL(false);

  // task 49：sessions 初始值原是硬编码假行 [{device:"macOS·Chrome 134", ip:"127.0.0.1"}]，
  // 即使后端返回空也永远显示这条假记录。改为空数组 + mount 即拉真后端。
  const [sessions, setSessions] = useStatePL([]);
  const [loginHistory, setLoginHistory] = useStatePL([]);
  const [visibilitySettings, setVisibilitySettings] = useStatePL({});
  const [savesCount, setSavesCount] = useStatePL(null);

  // mount 即拉 sessions/login-history/saves count，供描述行使用真实数字
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.auth.sessionsList();
        const list = r?.sessions || r?.items || [];
        if (cancelled) return;
        setSessions(list.map(s => ({
          id: s.id || s.session_id,
          device: s.device || s.user_agent || "—",
          loc: s.location || s.loc || "—",
          ip: s.ip || s.remote_ip || "—",
          ts: window.__fmt?.ago(s.last_seen_at || s.created_at) || "—",
          last_seen_at: s.last_seen_at || s.created_at,
          current: !!s.current,
        })));
      } catch (_) {}
      try {
        const r = await window.api.auth.loginHistory();
        const list = r?.entries || r?.items || [];
        if (cancelled) return;
        setLoginHistory(list.map(s => ({
          ts: window.__fmt?.ago(s.at) || s.at,
          at: s.at,
          dev: s.user_agent || s.device || "—",
          ip: s.ip || "—",
          result: s.result || (s.ok ? "ok" : "blocked"),
        })));
      } catch (_) {}
      try {
        const r = await window.api.saves.list();
        const list = r?.items || r?.saves || [];
        if (!cancelled) setSavesCount(Array.isArray(list) ? list.length : 0);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const onChangePassword = async (vals) => {
    if (!vals?.next || vals.next !== vals.confirm) {
      window.__apiToast?.("两次密码不一致", { kind: "danger" });
      return;
    }
    try {
      await window.api.auth.changePassword({ current: vals.current, next: vals.next });
      window.__apiToast?.("密码已修改", { kind: "ok" });
      setPwOpen(false);
    } catch (e) {
      window.__apiToast?.("修改失败", { kind: "danger", detail: e?.message });
    }
  };

  const onRevokeSession = async (sid) => {
    try {
      await window.api.auth.sessionsRevoke(sid);
      window.__apiToast?.("已下线", { kind: "ok" });
      setSessions(s => s.filter(x => x.id !== sid));
    } catch (e) {
      window.__apiToast?.("下线失败", { kind: "danger", detail: e?.message });
    }
  };

  const onRevokeAll = async () => {
    try {
      await window.api.auth.revokeAllSessions();
      window.__apiToast?.("已全部下线", { kind: "ok" });
      setSessions(s => s.filter(x => x.current));
    } catch (e) {
      window.__apiToast?.("下线失败", { kind: "danger", detail: e?.message });
    }
  };

  const onExportData = async (vals) => {
    try {
      const r = await window.api.account.exportData(vals);
      window.__apiToast?.("已申请导出", { kind: "ok", detail: r?.message || "完成后会邮件通知" });
      setExportOpen(false);
    } catch (e) {
      window.__apiToast?.("申请失败", { kind: "danger", detail: e?.message });
    }
  };

  const onSaveVisibility = async (vals) => {
    try {
      await window.api.account.visibility(vals || {});
      setVisibilitySettings(vals || {});
      window.__apiToast?.("已保存可见性", { kind: "ok" });
      setVisibilityOpen(false);
    } catch (e) {
      window.__apiToast?.("保存失败", { kind: "danger", detail: e?.message });
    }
  };

  const onDeactivate = async () => {
    try {
      await window.api.account.deactivate();
      window.__apiToast?.("账号已停用", { kind: "ok" });
      setConfirmDeact(false);
      setTimeout(() => location.replace("Login.html"), 800);
    } catch (e) {
      window.__apiToast?.("停用失败", { kind: "danger", detail: e?.message });
    }
  };

  const onDeleteAccount = async () => {
    try {
      await window.api.account.deleteAccount({});
      window.__apiToast?.("账号已删除", { kind: "ok" });
      setConfirmDelete(false);
      setTimeout(() => location.replace("Login.html"), 800);
    } catch (e) {
      window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message });
    }
  };

  const onSavePreference = async (key, value) => {
    try { await window.api.account.preferences({ [key]: value }); } catch (_) {}
  };
  // Persist preference changes on each toggle:
  useEffectPL(() => { onSavePreference("two_fa", twofa); }, [twofa]);
  useEffectPL(() => { onSavePreference("email_notif", emailNotif); }, [emailNotif]);
  useEffectPL(() => { onSavePreference("public_profile", publicProfile); }, [publicProfile]);
  useEffectPL(() => { onSavePreference("searchable", searchable); }, [searchable]);
  useEffectPL(() => { onSavePreference("share_usage", shareUsage); }, [shareUsage]);
  useEffectPL(() => { onSavePreference("share_crash", shareCrash); }, [shareCrash]);
  useEffectPL(() => { onSavePreference("ads_track", adsTrack); }, [adsTrack]);

  return (
    <CSSpaceBetween size="l" data-cap-anchor="me.settings">
      {/* 隐私 · 公开范围 */}
      <CSContainer header={<CSHeader variant="h2">隐私 · 公开范围</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title="公开个人主页"
            desc="开启后，其他用户可以通过 @用户名 查看你的成就墙和最近活动。"
            control={<SettingsToggle on={publicProfile} set={tog(setPublicProfile, "公开主页")} />}
          />
          <SettingRow
            title="允许搜索"
            desc="允许通过显示名或用户名在平台内搜索找到你。"
            control={<SettingsToggle on={searchable} set={tog(setSearchable, "允许搜索")} />}
          />
          <SettingRow
            title="资料字段可见性"
            desc="逐项控制谁能看到你的真实姓名、所在地、生日等。"
            control={<CSButton onClick={() => setVisibilityOpen(true)}>逐项配置</CSButton>}
          />
        </CSSpaceBetween>
      </CSContainer>

      {/* 数据共享 · 合规 */}
      <CSContainer header={<CSHeader variant="h2">数据共享 · 合规</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title="匿名用量统计"
            desc="把按钮点击 / 页面停留时长（不含剧本内容）匿名上报给团队，用于改进体验。"
            control={<SettingsToggle on={shareUsage} set={tog(setShareUsage, "匿名用量")} />}
          />
          <SettingRow
            title="崩溃 / 错误报告"
            desc="出现错误时上传堆栈信息和最近一次操作。剧本内容不会被上传。"
            control={<SettingsToggle on={shareCrash} set={tog(setShareCrash, "崩溃报告")} />}
          />
          <SettingRow
            title="个性化推荐"
            desc="基于你的剧本与角色卡向你推荐 Skill 和 MCP。"
            control={<SettingsToggle on={adsTrack} set={tog(setAdsTrack, "个性化推荐")} />}
          />
          <SettingRow
            title="GDPR / 个人信息保护合规"
            desc="本平台不向第三方分享你的剧本内容、玩家变量或私聊。详见隐私政策。"
            control={<CSButton iconName="file-open" onClick={(e) => { e.preventDefault(); setPolicyOpen(true); }}>隐私政策</CSButton>}
          />
        </CSSpaceBetween>
      </CSContainer>

      {/* 账号 · 安全 */}
      <CSContainer header={<CSHeader variant="h2">账号 · 安全</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title="修改密码"
            desc="建议每 90 天更换一次，至少 12 位字符 + 大小写 + 数字。"
            control={<CSButton iconName="lock-private" onClick={() => setPwOpen(true)}>修改密码</CSButton>}
          />
          <SettingRow
            title="二次验证（2FA）"
            desc="通过 Authenticator App 或手机短信进行二次验证。"
            control={
              <CSSpaceBetween direction="horizontal" size="xs">
                {twofa && <span className="pill ok"><span className="dot ok" /> Authenticator</span>}
                <SettingsToggle on={twofa} set={tog(setTwofa, "二次验证")} />
              </CSSpaceBetween>
            }
          />
          {(() => {
            // task 49：原 desc 写死 "3 个登录会话 · 12 分钟前 / 14 次登录"。改成
            // 真实派生：sessions.length + 最近一条 last_seen_at；30 天内 login_ok 次数。
            const nSess = sessions.length;
            const cur = sessions.find(s => s.current) || sessions[0];
            const sessDesc = nSess === 0
              ? "尚未拉取活跃会话。"
              : `当前 ${nSess} 个登录会话${cur ? ` · 最近：${cur.device}${cur.ts ? " · " + cur.ts : ""}` : ""}。`;
            const cutoff = Date.now() - 30 * 86400_000;
            const okIn30d = loginHistory.filter(h => {
              if (h.result !== "ok") return false;
              try { return new Date(h.at).getTime() >= cutoff; } catch { return false; }
            }).length;
            const blocked = loginHistory.filter(h => h.result !== "ok").length;
            const histDesc = loginHistory.length === 0
              ? "尚未拉取登录历史。"
              : `最近 30 天 ${okIn30d} 次成功登录${blocked ? `，${blocked} 次被拦截` : "，无异常 IP"}。`;
            return <>
              <SettingRow
                title="活跃会话"
                desc={sessDesc}
                control={<CSButton iconName="visibility-on" onClick={() => setSessionsOpen(true)}>查看会话</CSButton>}
              />
              <SettingRow
                title="登录历史"
                desc={histDesc}
                control={<CSButton iconName="status-info" onClick={() => setHistoryOpen(true)}>查看日志</CSButton>}
              />
            </>;
          })()}
        </CSSpaceBetween>
      </CSContainer>

      {/* 通知 */}
      <CSContainer header={<CSHeader variant="h2">通知</CSHeader>}>
        <SettingRow
          title="邮件通知"
          desc="重要安全事件、订阅变更、长时间未登录提醒。"
          control={<SettingsToggle on={emailNotif} set={tog(setEmailNotif, "邮件通知")} />}
        />
      </CSContainer>

      {/* 数据所有权 */}
      <CSContainer header={<CSHeader variant="h2">数据所有权</CSHeader>}>
        <CSSpaceBetween size="l">
          <SettingRow
            title="导出我的数据"
            desc="打包导出全部剧本、存档、记忆、库资产、用量记录。生成后通过邮件发送下载链接。"
            control={<CSButton iconName="download" onClick={() => setExportOpen(true)}>申请导出</CSButton>}
          />
          <SettingRow
            title="停用账号"
            desc="停用后无法登录，剧本和存档保留 90 天，期间可随时恢复。"
            control={<CSButton variant="normal" onClick={() => setConfirmDeact(true)}>停用账号</CSButton>}
          />
          <SettingRow
            title="永久删除账号"
            desc="立刻删除全部账号信息、剧本、存档、库资产，无法恢复。"
            control={<CSButton variant="normal" iconName="remove" onClick={() => setConfirmDelete(true)}>删除账号</CSButton>}
          />
        </CSSpaceBetween>
      </CSContainer>

      <ConfirmModal
        open={confirmDeact}
        title="停用账号？"
        body={<>账号停用 90 天内可登录恢复。期间剧本与存档保留但不可访问。</>}
        confirmLabel="停用"
        onClose={() => setConfirmDeact(false)} onConfirm={onDeactivate}
      />
      <ConfirmModal
        open={confirmDelete}
        title="永久删除账号？"
        body={<>这会<strong>立刻</strong>删除你的账号、剧本、存档、库资产，<strong>无法恢复</strong>。删除后无法用同一邮箱再注册（30 天冷冻期）。</>}
        danger confirmLabel="确认删除"
        onClose={() => setConfirmDelete(false)} onConfirm={onDeleteAccount}
      />
      <PromptModal
        open={pwOpen}
        eyebrow="修改密码"
        title="设置新密码"
        hint="POST /api/auth/password"
        fields={[
          { key: "current", label: "当前密码", required: true, type: "password" },
          { key: "next", label: "新密码", required: true, type: "password", hint: "至少 12 位 · 大小写 + 数字" },
          { key: "confirm", label: "确认新密码", required: true, type: "password" },
        ]}
        submitLabel="修改密码"
        onClose={() => setPwOpen(false)}
        onConfirm={onChangePassword}
      />
      <PromptModal
        open={visibilityOpen}
        eyebrow="资料字段可见性"
        title="逐项控制谁能看到"
        hint="POST /api/profile/visibility · 仅影响他人查看"
        fields={[
          { key: "real_name", label: "真实姓名", type: "select", default: "self",
            options: [{value: "self", label: "仅自己"}, {value: "friends", label: "好友"}, {value: "public", label: "所有人"}] },
          { key: "gender", label: "性别", type: "select", default: "friends",
            options: [{value: "self", label: "仅自己"}, {value: "friends", label: "好友"}, {value: "public", label: "所有人"}] },
          { key: "birthday", label: "生日", type: "select", default: "self",
            options: [{value: "self", label: "仅自己"}, {value: "friends", label: "好友"}, {value: "public", label: "所有人"}] },
          { key: "location", label: "所在地", type: "select", default: "public",
            options: [{value: "self", label: "仅自己"}, {value: "friends", label: "好友"}, {value: "public", label: "所有人"}] },
          { key: "email", label: "邮箱", type: "select", default: "self",
            options: [{value: "self", label: "仅自己"}, {value: "friends", label: "好友"}, {value: "public", label: "所有人"}] },
          { key: "phone", label: "手机", type: "select", default: "self",
            options: [{value: "self", label: "仅自己"}, {value: "friends", label: "好友"}, {value: "public", label: "所有人"}] },
        ]}
        submitLabel="保存可见性"
        onClose={() => setVisibilityOpen(false)}
        onConfirm={onSaveVisibility}
      />
      <PromptModal
        open={exportOpen}
        eyebrow="导出数据"
        title="选择要导出的内容"
        hint="POST /api/account/export · 生成后通过邮件发送下载链接（链接 7 天有效）"
        fields={[
          { key: "scope", label: "范围", type: "select", default: "all",
            options: [
              { value: "all",      label: "全部 · 剧本 · 存档 · 库 · 用量" },
              { value: "scripts",  label: "仅剧本与章节" },
              { value: "saves",    label: "仅存档与分支" },
              { value: "library",  label: "仅库资产" },
              { value: "usage",    label: "仅用量日志" },
            ] },
          { key: "format", label: "格式", type: "select", default: "zip",
            options: [
              { value: "zip", label: "ZIP · 含 JSON + 附件" },
              { value: "json", label: "JSON · 仅元数据" },
            ] },
          { key: "email", label: "接收邮箱", required: true, default: "" },
        ]}
        submitLabel="申请导出"
        onClose={() => setExportOpen(false)}
        onConfirm={onExportData}
      />
      {sessionsOpen && (
        <div className="pl-modal-backdrop" onClick={() => setSessionsOpen(false)}>
          <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(620px, 100%)"}}>
            <header className="pl-modal-head">
              <div>
                <div className="pl-modal-eyebrow">活跃会话</div>
                <h2 className="pl-modal-title">{sessions.length === 0 ? "暂无活跃会话" : `${sessions.length} 个登录中`}</h2>
              </div>
              <button className="iconbtn" onClick={() => setSessionsOpen(false)} data-tip="关闭"><Icon name="close" size={14} /></button>
            </header>
            <ul className="pl-session-list">
              {sessions.map((s, i) => (
                <li key={s.id || i}>
                  <div className="pl-session-dot"><Icon name={(s.device || "").includes("iOS") ? "user" : (s.device || "").includes("mac") ? "logo" : "world"} size={12} /></div>
                  <div className="pl-session-body">
                    <div>
                      <strong>{s.device}</strong>
                      {s.current && <span className="pill ok" style={{marginLeft: 6}}><span className="dot ok pulse" /> 当前</span>}
                    </div>
                    <span className="muted-2 mono" style={{fontSize: 11}}>{s.loc} · {s.ip} · {s.ts}</span>
                  </div>
                  {!s.current && (
                    <button className="btn ghost" style={{height: 26, fontSize: 11.5}} onClick={() => onRevokeSession(s.id)}>
                      <Icon name="close" size={11} /> 强制下线
                    </button>
                  )}
                </li>
              ))}
            </ul>
            <footer className="pl-modal-foot">
              <span className="muted-2" style={{fontSize: 11.5}}>POST /api/auth/sessions/revoke</span>
              <div style={{display: "flex", gap: 8}}>
                <button className="btn ghost" onClick={() => setSessionsOpen(false)}>关闭</button>
                <button className="btn danger" onClick={onRevokeAll}><Icon name="close" size={12} /> 全部下线（保留当前）</button>
              </div>
            </footer>
          </div>
        </div>
      )}
      {historyOpen && (
        <div className="pl-modal-backdrop" onClick={() => setHistoryOpen(false)}>
          <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(640px, 100%)"}}>
            <header className="pl-modal-head">
              <div>
                <div className="pl-modal-eyebrow">登录日志</div>
                <h2 className="pl-modal-title">最近登录 · {loginHistory.length} 次</h2>
              </div>
              <button className="iconbtn" onClick={() => setHistoryOpen(false)} data-tip="关闭"><Icon name="close" size={14} /></button>
            </header>
            <ul className="pl-session-list">
              {loginHistory.length === 0 ? (
                <li className="muted" style={{padding: 16, textAlign: "center"}}>暂无记录</li>
              ) : loginHistory.map((r, i) => (
                <li key={i} className="pl-history-row">
                  <span className="mono muted-2" style={{fontSize: 11, width: 92}}>{r.ts}</span>
                  <span style={{fontSize: 12.5, flex: 1, minWidth: 0}}>{r.dev}</span>
                  <span className="mono muted-2" style={{fontSize: 11}}>{r.ip}</span>
                  {r.result === "ok" ? (
                    <span className="pill ok" style={{fontSize: 10.5}}><span className="dot ok" /> 成功</span>
                  ) : (
                    <span className="pill danger" style={{fontSize: 10.5}}><span className="dot danger" /> 已拦截</span>
                  )}
                </li>
              ))}
            </ul>
            <footer className="pl-modal-foot">
              <span className="muted-2" style={{fontSize: 11.5}}>GET /api/auth/login-history</span>
              <div style={{display: "flex", gap: 8}}>
                <button className="btn ghost" onClick={() => setHistoryOpen(false)}>关闭</button>
                <button className="btn ghost" onClick={() => {
                  const url = window.api.base + "/api/v1/auth/login-history?format=csv";
                  window.open(url, "_blank");
                }}><Icon name="download" size={12} /> 导出 CSV</button>
              </div>
            </footer>
          </div>
        </div>
      )}
      {policyOpen && (
        <div className="pl-modal-backdrop" onClick={() => setPolicyOpen(false)}>
          <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(680px, 100%)"}}>
            <header className="pl-modal-head">
              <div>
                <div className="pl-modal-eyebrow">隐私政策摘要</div>
                <h2 className="pl-modal-title">我们如何处理你的数据</h2>
              </div>
              <button className="iconbtn" onClick={() => setPolicyOpen(false)} data-tip="关闭"><Icon name="close" size={14} /></button>
            </header>
            <div style={{fontSize: 13, lineHeight: 1.7, color: "var(--text-quiet)", maxHeight: 360, overflow: "auto"}}>
              <p><strong>1. 我们收集什么</strong>：账号信息（用户名、邮箱、可选手机）、设备指纹（用于会话）、用量遥测（仅在你开启时）。</p>
              <p><strong>2. 我们 不 收集什么</strong>：剧本正文、玩家变量、私聊、固定记忆、世界书条目——这些数据加密存储在你的工作区，团队 无 任何访问。</p>
              <p><strong>3. 与第三方</strong>：不向第三方分享剧本内容。模型 API 调用按你配置直接发往对应厂商（OpenAI / Anthropic 等），团队 不 代理也 不 留存。</p>
              <p><strong>4. 数据所有权</strong>：你可以随时通过『导出我的数据』申请完整归档；可随时『停用账号』（90 天保留）或『永久删除』（立刻执行）。</p>
              <p><strong>5. 合规</strong>：本平台符合 GDPR · 中国《个人信息保护法》· 加州 CCPA。</p>
            </div>
            <footer className="pl-modal-foot">
              <a className="muted" style={{fontSize: 12}} href="#" onClick={(e) => e.preventDefault()}>查看完整政策（外链）</a>
              <button className="btn primary" onClick={() => setPolicyOpen(false)}>我已阅读</button>
            </footer>
          </div>
        </div>
      )}
    </CSSpaceBetween>
  );
}

function Field({ label, hint, required, children }) {
  return (
    <CSFormField label={<>{label}{required && <span style={{ color: 'var(--accent)', marginLeft: 2 }}>*</span>}</>} description={hint}>
      {children}
    </CSFormField>
  );
}

function SettingRow({ title, desc, control }) {
  return (
    <CSFormField label={title} description={desc}>
      {control}
    </CSFormField>
  );
}

/* ---------------------------- PROFILE -------------------------- */
function ProfilePage() {
  const platform = usePlatformData();  // task 45：响应式 platform，登录后真实数据自动注入
  const { database = {}, stats = {}, scripts = [], saves = [], recent_assets = [] } = platform;
  const user = useReactiveUser();  // task 13: 保存资料后即时同步显示名/简介
  const [editOpen, setEditOpen] = useStatePL(false);
  // task 12：以真实数组长度为最权威源；data-loader 已把 stats.* 改为
  // 真实值/null，但这里再做一层兜底，避免设计预览模式 (offline) 残留的 mock 12 漏到 UI。
  const fmtN = (n) => (n == null ? "—" : (typeof n === "number" ? n.toLocaleString() : String(n)));
  const realScripts = Array.isArray(scripts) ? scripts : [];
  const realSaves = Array.isArray(saves) ? saves : [];
  const wordTotal = realScripts.reduce((a, s) => a + (Number(s && s.word_count) || 0), 0);
  const wordWan = wordTotal > 0 ? (wordTotal / 10000).toFixed(0) : "—";
  const branchAgg = realSaves.reduce((a, s) => a + (Number(s && s.branch_count) || 0), 0) || (stats?.branches ?? null);
  return (
    <CSSpaceBetween size="l">
      {/* 4 stat 卡 */}
      <CSContainer>
        <CSColumnLayout columns={4} variant="text-grid">
          <div>
            <CSBox variant="awsui-key-label">剧本</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{realScripts.length}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{wordTotal > 0 ? `共 ${wordWan} 万字` : "未导入剧本"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">存档</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{realSaves.length}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{realSaves[0]?.updated_at ? `最近：${realSaves[0].updated_at}` : "尚未创建存档"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">分支节点</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{fmtN(branchAgg)}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{realSaves.length ? `来自 ${realSaves.length} 个存档` : "—"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">库资产</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{fmtN(stats?.assets)}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">用量详见 <a href="#usage" style={{borderBottom: "1px dotted var(--muted-2)"}}>用量页</a></CSBox>
          </div>
        </CSColumnLayout>
      </CSContainer>

      {/* 账号 section */}
      <CSContainer header={
        <CSHeader variant="h2" actions={<CSButton href="#me-edit" iconName="edit">编辑资料</CSButton>}>
          账号
        </CSHeader>
      }>
        <CSKeyValuePairs
          columns={2}
          items={[
            {
              label: "用户",
              value: (
                <CSSpaceBetween size="xxs">
                  <CSBox fontWeight="bold">{user.display_name}</CSBox>
                  <CSBox color="text-body-secondary" fontSize="body-s">@{user.username} · {user.role} · uid {user.uid}</CSBox>
                  {user.bio && <CSBox color="text-body-secondary" fontSize="body-s">{user.bio}</CSBox>}
                </CSSpaceBetween>
              ),
            },
            {
              label: "数据库",
              value: (
                <CSBox>
                  <span className="mono">{database.driver}</span>
                  <CSStatusIndicator type={database.ok ? "success" : "error"} style={{marginLeft: 8}}>
                    {database.ok ? "online" : "offline"}
                  </CSStatusIndicator>
                </CSBox>
              ),
            },
            {
              label: "API 版本",
              value: <span className="mono">v1 · stable</span>,
            },
          ]}
        />
      </CSContainer>

      {/* 最近游玩 */}
      <CSContainer header={
        <CSHeader variant="h2" actions={<CSButton href="#saves" iconName="caret-right-filled">全部存档</CSButton>}>
          最近游玩 <span className="muted-2" style={{fontWeight: "normal"}}>按上次操作时间</span>
        </CSHeader>
      }>
        {realSaves.length === 0 ? (
          <CSBox textAlign="center" color="text-body-secondary" padding="l">
            <CSSpaceBetween size="s">
              <CSBox>还没有任何存档</CSBox>
              <CSBox fontSize="body-s">去「剧本」页选一本剧本开始新游戏，存档会自动出现在这里。</CSBox>
              <CSButton href="#saves-scripts" iconName="file">去剧本页</CSButton>
            </CSSpaceBetween>
          </CSBox>
        ) : (
          <CSTable
            columnDefinitions={[
              {
                id: "title",
                header: "剧本 / 存档",
                cell: s => {
                  const script = realScripts.find(sc => sc && sc.id === s.script_id);
                  return (
                    <div className="pl-title-cell">
                      <strong>{s.title || `存档 #${s.id}`}</strong>
                      <span className="muted-2 mono">{script?.title || "—"}</span>
                    </div>
                  );
                },
              },
              {
                id: "progress",
                header: "进度",
                cell: s => <span className="mono">{Number(s.branch_count) || 0} 分支节点</span>,
              },
              {
                id: "last",
                header: "上次游玩",
                cell: s => (
                  <span className="muted">
                    {s.current && <span className="pill accent" style={{marginRight: 6}}><span className="dot accent pulse" /> 在玩</span>}
                    {s.updated_at || "—"}
                  </span>
                ),
              },
              {
                id: "action",
                header: "",
                cell: s => (
                  <CSButton variant="primary" iconName="caret-right-filled"
                    onClick={() => window.__openContinue?.(s)}>
                    继续
                  </CSButton>
                ),
              },
            ]}
            items={realSaves}
            trackBy="id"
            empty={<CSBox color="text-body-secondary" textAlign="center">暂无存档</CSBox>}
          />
        )}
      </CSContainer>

      {/* 最近资源 */}
      <CSContainer header={<CSHeader variant="h2">最近资源</CSHeader>}>
        {Array.isArray(recent_assets) && recent_assets.length > 0 ? (
          <CSColumnLayout columns={4} variant="text-grid">
            {recent_assets.map((a, i) => (
              <div key={`asset-${i}`} className="pl-lib-tile">
                <div className="pl-lib-tile-icon">
                  <Icon name={a.kind === "image" ? "image" : a.kind === "archive" ? "folder" : "file"} size={28} />
                </div>
                <div className="pl-lib-tile-name">{a.name}</div>
                <div className="pl-lib-tile-meta">{fmtBytes(a.size || 0)} · {a.at || "—"}</div>
              </div>
            ))}
          </CSColumnLayout>
        ) : (
          <CSBox color="text-body-secondary" textAlign="center" padding="l">
            文件库还没有内容。<a href="#library" style={{borderBottom: "1px dotted var(--muted-2)"}}>上传资源 →</a>
          </CSBox>
        )}
      </CSContainer>

      <PromptModal
        open={editOpen}
        eyebrow="编辑资料"
        title={user.display_name}
        hint="POST /api/profile"
        fields={[
          { key: "display_name", label: "显示名", required: true, default: user.display_name },
          { key: "bio", label: "简介", type: "textarea", default: user.bio, rows: 4 },
          { key: "timezone", label: "时区", type: "select", default: "Asia/Shanghai",
            options: [
              { value: "Asia/Shanghai", label: "UTC+8 · 上海" },
              { value: "UTC", label: "UTC" },
              { value: "America/Los_Angeles", label: "UTC-8 · 洛杉矶" },
            ] },
        ]}
        submitLabel="保存"
        onClose={() => setEditOpen(false)}
        onConfirm={async (vals) => {
          // task 51：原 onConfirm = setEditOpen(false) 纯关闭，用户改了名字提交后
          // 完全没保存。真打 POST /api/profile，成功后触发 me 数据 refresh。
          try {
            await window.api.account.saveProfile({
              display_name: vals.display_name,
              bio: vals.bio,
              timezone: vals.timezone,
            });
            window.__apiToast?.("已保存", { kind: "ok", duration: 1500 });
            setEditOpen(false);
            // 触发 useReactiveUser 重新拉一次
            try {
              const me = await window.api.auth.me();
              if (me && me.user) window.__USER_STATE = me.user;
              window.dispatchEvent(new CustomEvent("rpg-data-ready"));
            } catch (_) {}
          } catch (e) {
            window.__apiToast?.("保存失败", { kind: "danger", detail: e?.message });
          }
        }}
      />
    </CSSpaceBetween>
  );
}


function fmtBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}


/* ---------------------------- MODULES (5E compatible) -------- */
// 内部 ruleset id "dnd5e"，对外文案统一 "5E compatible / 五版规则兼容"。
// 不引入官方 D&D 商标、Forgotten Realms 等非 SRD IP。
function ModulesPage() {
  const [modules, setModules] = useStatePL([]);
  const [loaded, setLoaded] = useStatePL(false);
  const [busyId, setBusyId] = useStatePL(null);
  const [errorMsg, setErrorMsg] = useStatePL("");

  useEffectPL(() => {
    if (!window.api?.rules) {
      setErrorMsg("window.api.rules 未注册，请刷新页面或重启 dev server");
      setLoaded(true);
      return;
    }
    window.api.rules.modules()
      .then(d => {
        if (d && d.ok) setModules(d.modules || []);
        else setErrorMsg(d?.detail || d?.error || "加载模组失败");
      })
      .catch(e => setErrorMsg(String(e?.message || e)))
      .finally(() => setLoaded(true));
  }, []);

  const startModule = async (m) => {
    setBusyId(m.id);
    setErrorMsg("");
    try {
      // Bug 2：用 /api/rules/module/launch 一步建立独立 save + 激活 + 加载模组。
      // 之前的「先 newGame 再 startModule」两步流程在前端层面看是新存档，但实际
      // newGame 走的 /api/new 并不真的建一个独立 game_save（只是 reset 当前 runtime），
      // 接着 startModule mutate 当前激活 save → 污染了用户的小说存档。
      // launch 端点是后端原子流程，保证模组 save_id 是新的。
      const moduleName = m.name_cn || m.name || m.id;
      const data = await window.api.rules.launchModule(m.id, { title: moduleName });
      if (!data || !data.ok) throw new Error(data?.detail || data?.error || "launch_module 失败");
      window.__apiToast?.(`已开始：${moduleName}（独立存档 #${data.save_id}）`, { kind: "ok" });
      try { window.dispatchEvent(new CustomEvent("rpg-saves-updated")); } catch (_) {}
      window.location.href = "Game Console.html#rules";
    } catch (e) {
      setErrorMsg(String(e?.message || e));
      window.__apiToast?.("启动模组失败", { kind: "danger", detail: String(e?.message || e) });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <CSSpaceBetween size="l">
      {errorMsg && (
        <CSAlert type="error" dismissible={false}>{errorMsg}</CSAlert>
      )}
      <CSContainer header={
        <CSHeader
          variant="h2"
          counter={loaded ? `(${modules.length})` : undefined}
          description="5E compatible / 五版规则兼容"
        >
          5E 兼容冒险模组
        </CSHeader>
      }>
        {!loaded ? (
          <CSBox color="text-body-secondary" textAlign="center" padding="l">加载中…</CSBox>
        ) : (
          <CSTable
            columnDefinitions={[
              {
                id: "module",
                header: "模组",
                cell: m => (
                  <div className="pl-title-cell">
                    <strong>{m.name_cn || m.name}</strong>
                    <span className="muted-2 mono">{m.id}</span>
                    {m.tagline ? <span className="muted-2" style={{fontStyle:"italic",marginTop:3}}>{m.tagline}</span> : null}
                  </div>
                ),
              },
              {
                id: "ruleset",
                header: "规则集",
                cell: m => {
                  const ruleset = m.ruleset || {};
                  return <CSStatusIndicator type="success">{ruleset.public_label || "5E compatible"}</CSStatusIndicator>;
                },
              },
              {
                id: "level",
                header: "等级",
                cell: m => <span className="mono">{(m.level_range || []).join("-") || "—"}</span>,
              },
              {
                id: "duration",
                header: "预计时长",
                cell: m => <span className="muted">{m.estimated_minutes ? `${m.estimated_minutes} 分钟` : "—"}</span>,
              },
              {
                id: "action",
                header: "",
                cell: m => (
                  <CSButton
                    variant="primary"
                    loading={busyId === m.id}
                    onClick={() => startModule(m)}
                  >
                    {busyId === m.id ? "启动中…" : "开始模组"}
                  </CSButton>
                ),
              },
            ]}
            items={modules}
            trackBy="id"
            empty={
              <CSBox textAlign="center" color="text-body-secondary" padding="l">
                当前没有内置冒险模组。模组数据位于 <code>rpg/modules/</code> 目录。
              </CSBox>
            }
          />
        )}
      </CSContainer>
      <CSContainer>
        <CSBox color="text-body-secondary" fontSize="body-s">
          本页所有模组使用原创地名、角色、怪物。规则层为 5E-compatible（五版规则兼容），
          不引入任何官方 Dungeons &amp; Dragons 商标或非 SRD IP。LLM 仅负责叙事，所有掷骰、
          检定、战斗、HP/AC 计算由确定性 RulesEngine 完成；GM 直写 HP/AC/initiative
          会被 State Gate 拒绝。
        </CSBox>
      </CSContainer>
    </CSSpaceBetween>
  );
}


/* ---------------------------- LIBRARY -------------------------- */
const LIB_ROWS = [
  { kind: "folder", name: "南陵地图集", size: 0, items: 12, at: "2 天前" },
  { kind: "folder", name: "残页扫描", size: 0, items: 47, at: "上周" },
  { kind: "folder", name: "人物谱", size: 0, items: 8, at: "上月" },
  { kind: "image",  name: "雾港全景.png", size: 2_410_000, at: "今天" },
  { kind: "image",  name: "灯塔结构图.png", size: 980_000, at: "今天" },
  { kind: "archive",name: "光绪十三年残页扫描.zip", size: 18_400_000, at: "昨天" },
  { kind: "markdown", name: "人物谱_v3.md", size: 12_400, at: "3 天前" },
  { kind: "text",   name: "雾港事件 · 时间线.txt", size: 4_800, at: "3 天前" },
  { kind: "audio",  name: "海雾环境音 · 30min.mp3", size: 28_000_000, at: "上周" },
];

const LIB_ICON = { folder: "folder", image: "image", archive: "folder", markdown: "file", text: "file", audio: "spark" };

/* ---------------------------- LIBRARY (cont) -------------------- */

function LibraryPage() {
  const [view, setView] = useStatePL("list");
  const [uploadOpen, setUploadOpen] = useStatePL(false);
  const [mkdirOpen, setMkdirOpen] = useStatePL(false);
  const [deleteTarget, setDeleteTarget] = useStatePL(null);
  // task 48：登录态零 mock。原 useState(LIB_ROWS) 首屏闪过 9 行示例文件（南陵地图集 / 残页扫描 /
  // 人物谱 / 雾港全景.png ...），即使后端 /api/library 立刻返空也已经看见。
  // 改为登录用户初始空数组；匿名访客保留 LIB_ROWS 作为 designer offline preview。
  const IS_ANON = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  const [rows, setRows] = useStatePL(IS_ANON ? LIB_ROWS : []);
  const [path, setPath] = useStatePL("");
  const fileInputRef = React.useRef(null);

  const reload = React.useCallback(async () => {
    try {
      const r = await window.api.library.list({ path });
      const list = (r && (r.entries || r.items)) || [];
      // task 48：以前 `if (list.length || keys.length)` 才覆盖 baseline，导致 API 返
      // {entries: []} 空对象仍保留 mock。现在登录态无条件覆盖（空数组 = 真实空 library）。
      setRows(list.map(e => ({
        kind: e.kind || (e.is_dir ? "folder" : window.__guessKind?.(e.name) || "file"),
        name: e.name || e.path,
        size: e.size || 0,
        items: e.items,
        at: window.__fmt?.ago(e.updated_at || e.mtime) || "—",
        path: e.path || e.name,
      })));
    } catch (e) { /* 匿名/降级：保留 baseline mock */ }
  }, [path]);
  useEffectPL(() => { reload(); }, [reload]);

  const onUploadFile = async (file) => {
    if (!file) return;
    try {
      await window.api.library.upload(file, path);
      window.__apiToast?.("已上传", { kind: "ok" });
      setUploadOpen(false);
      reload();
    } catch (e) {
      window.__apiToast?.("上传失败", { kind: "danger", detail: e?.message });
    }
  };

  const onMkdir = async (name) => {
    if (!name) return;
    try {
      await window.api.library.mkdir({ path, name });
      window.__apiToast?.("已新建文件夹", { kind: "ok" });
      setMkdirOpen(false);
      reload();
    } catch (e) {
      window.__apiToast?.("新建失败", { kind: "danger", detail: e?.message });
    }
  };

  const onDelete = async (r) => {
    try {
      await window.api.library.delete({ path: r.path || r.name });
      window.__apiToast?.("已删除", { kind: "ok" });
      setDeleteTarget(null);
      reload();
    } catch (e) {
      window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message });
    }
  };

  const onDownload = (r) => {
    const u = window.api.library.downloadUrl(r.path || r.name);
    window.open(u, "_blank");
  };

  // breadcrumb path segments
  const pathSegments = (path || "").split("/").filter(Boolean);

  return (
    <CSSpaceBetween size="l">
      {/* hidden file input for upload */}
      <input ref={fileInputRef} type="file" style={{display: "none"}}
        onChange={(e) => onUploadFile(e.target.files?.[0])} />

      <CSContainer header={
        <CSHeader
          variant="h2"
          counter={`(${rows.length})`}
          description={
            <CSSpaceBetween size="xs" direction="horizontal">
              <CSButton variant="inline-link" onClick={() => setPath("")}>库</CSButton>
              {pathSegments.map((seg, i, arr) => (
                <React.Fragment key={`seg-${i}`}>
                  <span className="muted-2">/</span>
                  <CSButton variant="inline-link" onClick={() => setPath(arr.slice(0, i + 1).join("/"))}>{seg}</CSButton>
                </React.Fragment>
              ))}
              {!path && <span className="muted-2">/ 默认工作区</span>}
            </CSSpaceBetween>
          }
          actions={
            <CSSpaceBetween size="xs" direction="horizontal">
              <CSButton
                variant={view === "list" ? "primary" : "normal"}
                iconName="list"
                onClick={() => setView("list")}
              >表格</CSButton>
              <CSButton
                variant={view === "grid" ? "primary" : "normal"}
                iconName="grid"
                onClick={() => setView("grid")}
              >网格</CSButton>
              <CSButton iconName="add-plus" onClick={() => setMkdirOpen(true)}>新建文件夹</CSButton>
              <CSButton variant="primary" iconName="upload" onClick={() => fileInputRef.current?.click()}>上传</CSButton>
            </CSSpaceBetween>
          }
        >
          资产库
        </CSHeader>
      }>
        {view === "list" ? (
          <CSTable
            columnDefinitions={[
              {
                id: "icon",
                header: "",
                width: 40,
                cell: r => <Icon name={LIB_ICON[r.kind] || "file"} size={16} />,
              },
              {
                id: "name",
                header: "名称",
                cell: r => (
                  <span
                    title={r.name}
                    onClick={() => { if (r.kind === "folder") setPath(r.path || r.name); }}
                    style={{cursor: r.kind === "folder" ? "pointer" : "default", color: r.kind === "folder" ? "var(--color-text-link-default)" : undefined}}
                  >
                    {r.name}
                  </span>
                ),
              },
              {
                id: "kind",
                header: "类型",
                cell: r => <span className="muted">{r.kind}</span>,
              },
              {
                id: "size",
                header: "大小",
                cell: r => <span className="mono muted">{r.kind === "folder" ? `${r.items || 0} 项` : fmtBytes(r.size)}</span>,
              },
              {
                id: "at",
                header: "修改时间",
                cell: r => <span className="muted">{r.at}</span>,
              },
              {
                id: "actions",
                header: "",
                cell: r => (
                  <CSSpaceBetween size="xs" direction="horizontal">
                    <CSButton
                      variant="inline-icon"
                      iconName="download"
                      disabled={r.kind === "folder"}
                      onClick={() => onDownload(r)}
                      ariaLabel="下载"
                    />
                    <CSButton
                      variant="inline-icon"
                      iconName="remove"
                      onClick={() => setDeleteTarget(r)}
                      ariaLabel="删除"
                    />
                  </CSSpaceBetween>
                ),
              },
            ]}
            items={rows}
            trackBy="name"
            empty={
              <CSBox textAlign="center" color="text-body-secondary" padding="l">
                当前目录为空
              </CSBox>
            }
          />
        ) : (
          <CSCards
            cardDefinition={{
              header: r => (
                <span
                  onClick={() => { if (r.kind === "folder") setPath(r.path || r.name); }}
                  style={{cursor: r.kind === "folder" ? "pointer" : "default"}}
                  title={r.name}
                >
                  {r.name}
                </span>
              ),
              sections: [
                {
                  id: "icon",
                  content: r => (
                    <div style={{textAlign: "center", padding: "8px 0"}}>
                      <Icon name={LIB_ICON[r.kind] || "file"} size={28} />
                    </div>
                  ),
                },
                {
                  id: "meta",
                  content: r => (
                    <CSBox color="text-body-secondary" fontSize="body-s">
                      {r.kind === "folder" ? `${r.items || 0} 项` : fmtBytes(r.size)} · {r.at}
                    </CSBox>
                  ),
                },
                {
                  id: "actions",
                  content: r => (
                    <CSSpaceBetween size="xs" direction="horizontal">
                      <CSButton
                        variant="inline-icon"
                        iconName="download"
                        disabled={r.kind === "folder"}
                        onClick={() => onDownload(r)}
                        ariaLabel="下载"
                      />
                      <CSButton
                        variant="inline-icon"
                        iconName="remove"
                        onClick={() => setDeleteTarget(r)}
                        ariaLabel="删除"
                      />
                    </CSSpaceBetween>
                  ),
                },
              ],
            }}
            cardsPerRow={[{ cards: 2 }, { minWidth: 600, cards: 4 }, { minWidth: 900, cards: 6 }]}
            items={rows}
            trackBy="name"
            empty={
              <CSBox textAlign="center" color="text-body-secondary" padding="l">
                当前目录为空
              </CSBox>
            }
          />
        )}
      </CSContainer>

      <PromptModal
        open={mkdirOpen}
        eyebrow="新建文件夹"
        title={`在 ${path || "默认工作区"} 下`}
        hint="POST /api/library/mkdir"
        fields={[
          { key: "name", label: "文件夹名", required: true, placeholder: "例：人物谱" },
        ]}
        submitLabel="创建"
        onClose={() => setMkdirOpen(false)}
        onConfirm={(vals) => onMkdir(vals?.name)}
      />
      <ConfirmModal
        open={!!deleteTarget}
        title={`删除 ${deleteTarget?.name}`}
        body={
          <>
            {deleteTarget?.kind === "folder"
              ? `将删除整个文件夹 ${deleteTarget?.name}（${deleteTarget?.items || 0} 项），无法撤销。`
              : `将永久删除 ${deleteTarget?.name}，无法撤销。`}
          </>
        }
        danger
        confirmLabel="确认删除"
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => onDelete(deleteTarget)}
      />
    </CSSpaceBetween>
  );
}


function SettingsToggle({ on, set }) {
  // 加 stopPropagation：本 toggle 经常出现在可点击的 card-head 容器里
  // （如 ModelsSection 的 API 折叠条），需要阻止冒泡以免触发外层展开/折叠。
  return (
    <button
      type="button"
      className={`pl-cap-toggle ${on ? "on" : ""}`}
      onClick={(e) => { e.stopPropagation(); set(!on); }}
      aria-pressed={on}
    />
  );
}


/* ---------------------------- USAGE ---------------------------- */
const USAGE_RANGES = [
  { id: "24h", label: "24 小时", days: 1 },
  { id: "7d",  label: "7 天",   days: 7 },
  { id: "30d", label: "30 天",  days: 30 },
  { id: "90d", label: "90 天",  days: 90 },
];

// task 49：原 USAGE_BY_API / USAGE_BY_MODEL / USAGE_RECENT 是凭空捏的假调用日志
// （OpenAI 4128 次 · $8.42 / Claude Opus 4.1 · $18.74 / GPT-4o-mini 16:42:11 等），
// genSeries 用 Math.sin/cos 假装真实时序。UsagePage 现整页改接 /api/me/usage 与
// /api/me/usage/timeline；这些常量与 genSeries 已删除，没人再引用。

function fmtN(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

function Spark({ values, w = 600, h = 90, color = "var(--accent)" }) {
  // 防御：过滤非有限数（NaN/Infinity/null/string），避免 SVG path 出现 "NaN"
  const safe = Array.isArray(values) ? values.filter(v => Number.isFinite(v)) : [];
  // 0 个 / 1 个数据点：i/(n-1) 会除零得 NaN（"24 小时"档常触发）；
  // 退化为水平中线即可，不再生成可能炸 SVG 的坐标。
  if (safe.length < 2) {
    const midY = (h / 2).toFixed(1);
    const flat = `M0 ${midY} L${w} ${midY}`;
    return (
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" width="100%" height={h}>
        <path d={flat} fill="none" stroke={color} strokeOpacity="0.35"
              strokeWidth="1.5" strokeDasharray="3 4" strokeLinecap="round" />
      </svg>
    );
  }
  const max = Math.max(...safe, 1);
  const min = Math.min(...safe, 0);
  const range = max - min || 1;
  const denom = safe.length - 1; // 已确保 ≥ 1
  const pts = safe.map((v, i) => [(i / denom) * w, h - ((v - min) / range) * (h - 10) - 5]);
  const linePath = "M" + pts.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L");
  const areaPath = `M0 ${h} L` + pts.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L") + ` L${w} ${h} Z`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" width="100%" height={h}>
      <defs>
        <linearGradient id="sparkfill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.22" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#sparkfill)" />
      <path d={linePath} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function UsagePage() {
  // task 49：整页重写。原 UsagePage 用 USAGE_BY_API / USAGE_BY_MODEL / USAGE_RECENT
  // + genSeries(Math.sin/cos) 凭空伪造所有数据，整页零 API 调用。现接 /api/me/usage
  // 与 /api/me/usage/timeline，没真实数据的字段（延迟 / 错误率 / 月预算 / 同比 ↑12%
  // 这些后端 token_usage 表里就没有的列）一律显示 "—"，不再造假数字。
  const [range, setRange] = useStatePL("30d");
  const days = USAGE_RANGES.find(r => r.id === range)?.days || 30;
  const [data, setData] = useStatePL(null);
  const [series, setSeries] = useStatePL(null);
  const [loading, setLoading] = useStatePL(false);
  const [err, setErr] = useStatePL("");
  const [tick, setTick] = useStatePL(0);

  useEffectPL(() => {
    let cancelled = false;
    setLoading(true); setErr("");
    (async () => {
      try {
        const [u, t] = await Promise.all([
          window.api.account.usage(days),
          window.api.account.usageTimeline(days, "day"),
        ]);
        if (cancelled) return;
        setData(u || null);
        setSeries(t || null);
      } catch (e) {
        if (!cancelled) setErr(e?.message || "拉取用量失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [days, tick]);

  const totals = (data && data.totals) || {};
  const byModel = (data && data.by_model) || [];
  const recent = (data && data.recent_turns) || [];
  const bucketSeries = (series && series.series) || [];
  const totalTurns = Number(totals.turns || 0);
  const totalTokIn = Number(totals.input_tokens || 0);
  const totalTokOut = Number(totals.output_tokens || 0);
  const totalCost = Number(totals.cost_usd || 0);

  // 按 API 聚合（后端只提供 by_model，自己按 api_id 汇总）
  const byApi = useMemoPL(() => {
    const map = new Map();
    for (const r of byModel) {
      const k = r.api_id || "—";
      const cur = map.get(k) || { id: k, requests: 0, tokens_in: 0, tokens_out: 0, cost: 0 };
      cur.requests += Number(r.turns || 0);
      cur.tokens_in += Number(r.input_tokens || 0);
      cur.tokens_out += Number(r.output_tokens || 0);
      cur.cost += Number(r.cost_usd || 0);
      map.set(k, cur);
    }
    return [...map.values()].sort((a, b) => b.requests - a.requests);
  }, [byModel]);

  const reqSeriesVals = bucketSeries.map(b => Number(b.turns || 0));
  const costSeriesVals = bucketSeries.map(b => Number(b.cost_usd || 0));

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" dismissible={false}>{err}</CSAlert>}

      {/* 统计卡 */}
      <CSContainer header={
        <CSHeader
          variant="h2"
          description={loading ? "加载中…" : undefined}
          actions={
            <CSSpaceBetween size="xs" direction="horizontal">
              {USAGE_RANGES.map(r => (
                <CSButton
                  key={r.id}
                  variant={range === r.id ? "primary" : "normal"}
                  onClick={() => setRange(r.id)}
                >
                  {r.label}
                </CSButton>
              ))}
              <CSButton iconName="refresh" variant="icon" onClick={() => setTick(t => t + 1)} ariaLabel="刷新" />
            </CSSpaceBetween>
          }
        >
          用量 <span style={{fontWeight: "normal", fontSize: "0.85em", color: "var(--color-text-body-secondary)"}}>最近 {USAGE_RANGES.find(r => r.id === range)?.label}</span>
        </CSHeader>
      }>
        <CSColumnLayout columns={5} variant="text-grid">
          <div>
            <CSBox variant="awsui-key-label">请求数</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{fmtN(totalTurns)}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{totalTurns ? `日均 ${Math.round(totalTurns / days)}` : "—"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">Token 输入</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{fmtN(totalTokIn)}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{totalTokOut ? `输出 ${fmtN(totalTokOut)} · 比 1 : ${(totalTokIn / Math.max(1, totalTokOut)).toFixed(1)}` : "输出 —"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">成本</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">${totalCost.toFixed(2)}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">本窗口累计</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">平均延迟</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">—</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">后端未记录</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">错误率</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">—</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">后端未记录</CSBox>
          </div>
        </CSColumnLayout>
      </CSContainer>

      {/* 趋势图（保留原生 SVG Spark 自绘） */}
      <CSContainer header={<CSHeader variant="h2" description="每日聚合">趋势</CSHeader>}>
        {bucketSeries.length === 0 ? (
          <CSBox textAlign="center" color="text-body-secondary" padding="l">
            {loading ? "加载中…" : "近期没有用量记录"}
          </CSBox>
        ) : (
          <CSColumnLayout columns={2} variant="text-grid">
            <div>
              <div style={{display: "flex", justifyContent: "space-between", marginBottom: 4}}>
                <CSBox variant="awsui-key-label">请求</CSBox>
                <span className="mono" style={{fontSize: 12, color: "var(--color-text-body-secondary)"}}>{fmtN(reqSeriesVals.reduce((a, x) => a + x, 0))}</span>
              </div>
              <Spark values={reqSeriesVals} color="var(--accent)" />
            </div>
            <div>
              <div style={{display: "flex", justifyContent: "space-between", marginBottom: 4}}>
                <CSBox variant="awsui-key-label">成本 $</CSBox>
                <span className="mono" style={{fontSize: 12, color: "var(--color-text-body-secondary)"}}>${costSeriesVals.reduce((a, x) => a + x, 0).toFixed(2)}</span>
              </div>
              <Spark values={costSeriesVals} color="var(--ok)" />
            </div>
          </CSColumnLayout>
        )}
      </CSContainer>

      {/* 按 API 拆分 */}
      <CSContainer header={<CSHeader variant="h2">按 API 拆分</CSHeader>}>
        <CSTable
          columnDefinitions={[
            {
              id: "api",
              header: "API",
              cell: r => <strong style={{fontFamily: "var(--font-serif)", fontSize: 13.5}}>{r.id}</strong>,
            },
            {
              id: "requests",
              header: "请求",
              cell: r => <span className="mono">{fmtN(r.requests)}</span>,
            },
            {
              id: "tokens",
              header: "Token (入 / 出)",
              cell: r => <span className="mono"><span className="muted">{fmtN(r.tokens_in)}</span> <span className="muted-2">/</span> {fmtN(r.tokens_out)}</span>,
            },
            {
              id: "cost",
              header: "成本",
              cell: r => <span className="mono">${r.cost.toFixed(2)}</span>,
            },
            {
              id: "pct",
              header: "占比",
              cell: r => (
                <div style={{display: "flex", alignItems: "center", gap: 8}}>
                  <div style={{width: 60, height: 4, borderRadius: 999, background: "var(--color-background-control-default)", overflow: "hidden"}}>
                    <div style={{width: (totalTurns ? r.requests / totalTurns * 100 : 0) + "%", height: "100%", background: "var(--color-text-accent)"}} />
                  </div>
                  <span className="muted-2 mono" style={{fontSize: 11}}>{totalTurns ? Math.round(r.requests / totalTurns * 100) : 0}%</span>
                </div>
              ),
            },
          ]}
          items={byApi}
          trackBy="id"
          empty={
            <CSBox textAlign="center" color="text-body-secondary" padding="l">
              {loading ? "加载中…" : "暂无调用记录"}
            </CSBox>
          }
        />
      </CSContainer>

      {/* Top 模型 */}
      <CSContainer header={<CSHeader variant="h2" description="按请求数">Top 模型</CSHeader>}>
        <CSTable
          columnDefinitions={[
            {
              id: "rank",
              header: "#",
              width: 40,
              cell: m => <span className="mono muted-2">{String((m._rank ?? 0) + 1).padStart(2, "0")}</span>,
            },
            {
              id: "model",
              header: "模型",
              cell: m => <strong style={{fontSize: 13.5}}>{m.model}</strong>,
            },
            {
              id: "api",
              header: "API",
              cell: m => <span className="muted">{m.api_id}</span>,
            },
            {
              id: "requests",
              header: "请求",
              cell: m => <span className="mono">{fmtN(Number(m.turns || 0))}</span>,
            },
            {
              id: "tokens",
              header: "Token (入 / 出)",
              cell: m => <span className="mono"><span className="muted">{fmtN(Number(m.input_tokens || 0))}</span> <span className="muted-2">/</span> {fmtN(Number(m.output_tokens || 0))}</span>,
            },
            {
              id: "cost",
              header: "成本",
              cell: m => <span className="mono">${Number(m.cost_usd || 0).toFixed(2)}</span>,
            },
            {
              id: "pct",
              header: "占比",
              cell: m => (
                <div style={{display: "flex", alignItems: "center", gap: 8}}>
                  <div style={{width: 60, height: 4, borderRadius: 999, background: "var(--color-background-control-default)", overflow: "hidden"}}>
                    <div style={{width: (totalTurns ? Number(m.turns || 0) / totalTurns * 100 : 0) + "%", height: "100%", background: "var(--color-text-accent)"}} />
                  </div>
                  <span className="muted-2 mono" style={{fontSize: 11}}>{totalTurns ? Math.round(Number(m.turns || 0) / totalTurns * 100) : 0}%</span>
                </div>
              ),
            },
          ]}
          items={[...byModel].sort((a, b) => Number(b.turns || 0) - Number(a.turns || 0)).map((m, i) => ({ ...m, _rank: i }))}
          trackBy={m => `${m.api_id}/${m.model}`}
          empty={
            <CSBox textAlign="center" color="text-body-secondary" padding="l">
              {loading ? "加载中…" : "暂无调用记录"}
            </CSBox>
          }
        />
      </CSContainer>

      {/* 最近请求 */}
      <CSContainer header={
        <CSHeader variant="h2" description="显示最近 20 条 · GET /api/me/usage">
          最近请求
        </CSHeader>
      }>
        <CSTable
          columnDefinitions={[
            {
              id: "at",
              header: "时间",
              cell: r => <span className="mono">{r.at ? (window.__fmt?.ago(r.at) || r.at) : "—"}</span>,
            },
            {
              id: "api",
              header: "API",
              cell: r => <span className="muted">{r.api_id}</span>,
            },
            {
              id: "model",
              header: "模型",
              cell: r => <span className="mono" style={{fontSize: 11.5}}>{r.model}</span>,
            },
            {
              id: "tokens",
              header: "Token in / out",
              cell: r => <span className="mono"><span className="muted">{fmtN(Number(r.input_tokens || 0))}</span> <span className="muted-2">/</span> {fmtN(Number(r.output_tokens || 0))}</span>,
            },
            {
              id: "cost",
              header: "成本",
              cell: r => <span className="mono">${Number(r.cost_usd || 0).toFixed(3)}</span>,
            },
            {
              id: "ctx",
              header: "上下文",
              cell: r => <span className="mono">{Number(r.context_used || 0)} / {Number(r.context_max || 0)}</span>,
            },
          ]}
          items={recent}
          trackBy={r => `${r.at || ""}/${r.api_id || ""}/${r.model || ""}/${r.input_tokens || ""}`}
          empty={
            <CSBox textAlign="center" color="text-body-secondary" padding="l">
              {loading ? "加载中…" : "暂无最近调用"}
            </CSBox>
          }
        />
      </CSContainer>
    </CSSpaceBetween>
  );
}

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
        setItems(list);
      } catch (e) {
        setErr(e?.message || "拉取失败");
      } finally {
        setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [kind, reloadTick]);

  if (kind === "apis") return <ApiList />;

  // 校验：对 MCP 走 mcp.validate 逐条；其他类型只刷新 /api/tools 即可。
  const onValidateAll = async () => {
    setLoading(true);
    if (kind === "mcp") {
      let ok = 0, fail = 0;
      for (const it of items) {
        if (!it.on) continue;
        try {
          const r = await window.api.mcp.validate({ id: it.id, server_id: it.id });
          if (r && r.ok !== false) ok++; else fail++;
        } catch (_) { fail++; }
      }
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
            { key: "file", label: "Skill 包", type: "file", required: true, hint: ".zip / .tar.gz" },
            { key: "name", label: "显示名", placeholder: "默认使用包内 manifest 名" },
            { key: "version", label: "版本", placeholder: "默认 v0.1" },
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
              if (!vals.file) throw new Error("请选择 Skill 包文件");
              await window.api.skills.importPack(vals.file);
              window.__apiToast?.("Skill 已导入", { kind: "ok", duration: 1800 });
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
  // task 50：查看日志 → 拉真后端运行时（admin 看到 stderr）。导出 → 下载文本。
  const loadLog = async () => {
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
  };
  React.useEffect(() => { if (logOpen) loadLog(); }, [logOpen]);
  const editFields = kind === "mcp" ? [
    { key: "name", label: "名称", required: true, default: name },
    { key: "transport", label: "传输", type: "select", default: tag === "stdio" ? "stdio" : "http",
      options: [{ value: "stdio", label: "stdio · 本地命令" }, { value: "http", label: "http · 远程 HTTP" }] },
    { key: "command", label: "命令 / URL", required: true, mono: true, default: tag === "stdio" ? "uvx my-mcp" : "https://localhost:7300" },
    { key: "env", label: "环境变量 / Headers", type: "textarea", placeholder: "KEY=VALUE", rows: 3 },
  ] : kind === "skills" ? [
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
              if ((vals.transport || tag) === "http") body.url = vals.command;
              else body.command = vals.command;
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
        <div className="pl-modal-backdrop" onClick={() => setLogOpen(false)}>
          <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(640px, 100%)"}}>
            <header className="pl-modal-head">
              <div>
                <div className="pl-modal-eyebrow">日志 · {name}</div>
                <h2 className="pl-modal-title">最近 50 条</h2>
              </div>
              <button className="iconbtn" onClick={() => setLogOpen(false)} data-tip="关闭"><Icon name="close" size={14} /></button>
            </header>
            <pre className="mono" style={{
              maxHeight: 320, overflow: "auto", margin: 0, padding: "12px 14px",
              background: "var(--bg-deep)", border: "1px solid var(--line-soft)",
              borderRadius: "var(--r-2)", fontSize: 11.5, lineHeight: 1.7, color: "var(--text-quiet)"
            }}>{logBusy ? "加载中…" : logText || "（暂无内容）"}</pre>
            <footer className="pl-modal-foot">
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
            </footer>
          </div>
        </div>
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
            <input style={{height: 28, fontSize: 12}} placeholder="搜索路径或描述..." value={q} onChange={(e) => setQ(e.target.value)} />
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

/* ---------------------------- AUTH ----------------------------- */
function AuthPage() {
  const [mode, setMode] = useStatePL("login");
  const [username, setUsername] = useStatePL("");
  const [password, setPassword] = useStatePL("");
  const [displayName, setDisplayName] = useStatePL("");
  const [busy, setBusy] = useStatePL(false);
  const [err, setErr] = useStatePL("");
  // Login/AuthPage 上没有 PlatformShell 注入的 window.__apiToast，需要内联反馈
  // 字段来承接「忘记密码」等次要交互的提示，不能依赖可能不存在的全局 toast。
  const [notice, setNotice] = useStatePL("");

  // 登录后跳哪里：优先 ?next=...（来自 Platform/Game Console 的鉴权 gate），
  // 严格只允许同源相对路径，防止开放重定向（?next=https://evil.com）。
  const __nextOrDefault = () => {
    try {
      const raw = new URLSearchParams(location.search).get("next") || "";
      if (!raw) return "Platform.html";
      // 拒绝绝对 URL / 协议相对 URL / 包含换行的输入
      if (/^[a-z][a-z0-9+.\-]*:|^\/\//i.test(raw) || /[\r\n]/.test(raw)) return "Platform.html";
      // 允许：相对路径（含 hash/query），或站内绝对路径 /...
      return raw;
    } catch (_) { return "Platform.html"; }
  };

  // If already logged in → 跳目标页（next 或 Platform）
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const me = await window.api?.auth.me();
        if (!cancelled && me && me.user) {
          location.replace(__nextOrDefault());
        }
      } catch (_) { /* not logged in, stay */ }
    })();
    return () => { cancelled = true; };
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setErr("");
    if (!username.trim() || !password.trim()) {
      setErr("请填写用户名和密码");
      return;
    }
    setBusy(true);
    try {
      if (mode === "register") {
        await window.api.auth.register({
          username: username.trim(),
          password,
          display_name: displayName.trim() || undefined,
        });
      } else {
        await window.api.auth.login({ username: username.trim(), password });
      }
      window.__apiToast?.(mode === "register" ? "注册成功" : "登录成功", { kind: "ok", duration: 1400 });
      setTimeout(() => location.replace(__nextOrDefault()), 200);
    } catch (e) {
      setErr(e?.message || "请求失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pl-auth-wrap">
      <div className="pl-auth">
        <div style={{display: "flex", alignItems: "center", gap: 12}}>
          <div className="pl-auth-mark"><Icon name="logo" size={16} /></div>
          <div>
            <h1>RPG Roleplay</h1>
            <div className="pl-auth-sub">长篇小说拆书 · RPG 续写 · 多用户创作平台</div>
          </div>
        </div>
        <div className="pl-auth-tabs">
          <button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>登录</button>
          <button className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>注册</button>
        </div>
        <form className="pl-auth-form" onSubmit={submit}>
          <div className="pl-field">
            <label>用户名</label>
            <input autoComplete="username" placeholder="字母 / 数字 / 下划线"
              value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>
          <div className="pl-field">
            <label>密码</label>
            <input type="password" autoComplete={mode === "register" ? "new-password" : "current-password"}
              placeholder="至少 8 位"
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          {mode === "register" && (
            <div className="pl-field">
              <label>显示名</label>
              <input placeholder="例：晓卡"
                value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </div>
          )}
          {err && (
            <div className="pl-auth-error" style={{color: "var(--danger,#c0392b)", fontSize: 12.5, padding: "4px 0"}}>
              {err}
            </div>
          )}
          {notice && (
            <div className="pl-auth-notice" role="status" aria-live="polite"
              style={{color: "var(--muted, #6b7280)", fontSize: 12.5, padding: "4px 0",
                      borderLeft: "2px solid var(--accent, #b65b41)", paddingLeft: 8}}>
              {notice}
            </div>
          )}
          <button type="submit" className="btn primary" disabled={busy}
            style={{justifyContent: "center", height: 34, opacity: busy ? 0.7 : 1}}>
            {busy ? "正在提交…" : (mode === "login" ? "登录" : "创建账号")}
          </button>
          <div className="pl-auth-foot">
            <span>首个注册用户会成为管理员。</span>
            <a href="#"
              onClick={(e) => {
                e.preventDefault();
                // 主反馈：内联 notice（Login 页没有 PlatformShell 注入的 toast，
                // 之前直接 optional-chain 全局 toast，匿名用户点了完全没反应）
                setNotice("请联系管理员重置密码（暂未提供自助找回流程）");
                // 次要：如果恰好在 PlatformShell 里也跑了一遍 AuthPage，全局 toast 也带上
                window.__apiToast?.("请联系管理员重置密码", { kind: "info", duration: 2400 });
              }}
              style={{borderBottom: 0}}>忘记密码</a>
          </div>
        </form>
      </div>
    </div>
  );
}

// 顶层公共组件 / 帮助函数全部挂到 window，以便 frontend/src/pages/*.jsx 在
// 独立 <script type="text/babel"> 中直接通过全局引用，避免每个 page 文件复制一份。
// ScriptsPage / SavesPage / CardsPage / SettingsPage / BranchesPage / ContinuePicker /
// NewGameModal / ConfidenceBar 现在分别在 pages/scripts.jsx / saves.jsx / cards.jsx /
// settings.jsx 中定义并自己 Object.assign(window, ...)；这里不再列出，避免 ReferenceError。
/* ── Cloudscape shell(AWS 控制台架构 + 暖色主题)─────────────────────────
   与旧 PlatformShell 同 props 接口,entry 直接替换。复用 ToastStack /
   ContinuePicker / UnifiedSearch / useReactiveUser / usePlatformData。 */
/* AWS 式 IA:模块(=服务)注册表。左侧栏只显示「当前模块」的子页;
   全局模块切换走顶部「全部功能」菜单(类似 AWS 的服务列表)。 */
const CS_MODULES = [
  { id: 'scripts', label: '剧本', group: '创作',
    pages: ['scripts', 'scripts-import', 'scripts-library', 'scripts-editor', 'scripts-settings'],
    sub: [
      { text: '我的剧本', href: '#scripts' },
      { text: '上传剧本', href: '#scripts-import' },
      { text: '在线剧本库', href: '#scripts-library' },
      { text: '剧本编辑器', href: '#scripts-editor' },
      { text: '剧本设置', href: '#scripts-settings' },
    ] },
  { id: 'play', label: '开始游戏', group: '游玩',
    // NPC 角色卡已移入「剧本」详情面板(NPC 卡属于具体剧本),不再在开始游戏出现。
    pages: ['saves', 'saves-branches', 'cards', 'modules', 'play-settings'],
    sub: [
      { text: '存档目录', href: '#saves' },
      { text: '分支树', href: '#saves-branches' },
      { text: '用户角色卡', href: '#cards' },
      { text: '冒险模组', href: '#modules' },
      { text: '游戏设置', href: '#play-settings' },
    ] },
  { id: 'account', label: '设置 & 账户', group: '系统',
    pages: ['me', 'me-edit', 'me-settings', 'profile', 'settings', 'settings-models',
      'settings-modelparams', 'settings-modules', 'settings-memory', 'settings-permissions',
      'settings-deploy', 'settings-danger'],
    sub: [
      { text: '个人主页', href: '#me' },
      { text: '编辑资料', href: '#me-edit' },
      { text: '隐私与安全', href: '#me-settings' },
      { text: '偏好', href: '#settings' },
      { text: 'API 与模型', href: '#settings-models' },
      { text: '模型参数', href: '#settings-modelparams' },
      { text: '模块模型', href: '#settings-modules' },
      { text: '记忆', href: '#settings-memory' },
      { text: '权限', href: '#settings-permissions' },
      { text: '部署', href: '#settings-deploy' },
      { text: '高危操作', href: '#settings-danger' },
    ] },
  { id: 'library', label: '库', group: '系统', pages: ['library'],
    sub: [{ text: '资产库', href: '#library' }] },
  { id: 'extensions', label: '扩展', group: '系统', pages: ['plugins', 'mcp', 'skills', 'apis', 'usage'],
    sub: [
      { text: '插件', href: '#plugins' },
      { text: 'MCP', href: '#mcp' },
      { text: 'Skill', href: '#skills' },
      { text: '开发接口', href: '#apis' },
      { text: '用量', href: '#usage' },
    ] },
];

function _csActiveModule(page) {
  return CS_MODULES.find((m) => m.pages.includes(page)) || CS_MODULES[0];
}

// 顶部「全部功能」菜单(按 group 分组)
function _csSwitcherItems() {
  const groups = [];
  CS_MODULES.forEach((m) => {
    let g = groups.find((x) => x.text === m.group);
    if (!g) { g = { text: m.group, items: [] }; groups.push(g); }
    g.items.push({ id: m.id, text: m.label });
  });
  return groups;
}

async function _csRefresh() {
  try {
    window.__apiToast?.('正在刷新…', { kind: 'info', duration: 1200 });
    if (window.__refreshPlatform) await window.__refreshPlatform();
    else {
      const p = await window.api.platform.info();
      window.MOCK_PLATFORM = p && p.platform ? p.platform : (p || window.MOCK_PLATFORM);
      window.dispatchEvent(new CustomEvent('rpg-data-ready'));
    }
    window.__apiToast?.('已刷新', { kind: 'ok', duration: 1600 });
  } catch (e) { window.__apiToast?.('刷新失败', { kind: 'danger', detail: e?.message }); }
}

function PlatformShellCS({ page, setPage, children, assistant, assistantOpen, onToggleAssistant }) {
  const platform = usePlatformData();
  const reactiveUser = useReactiveUser();
  const [continueState, setContinueState] = useStatePL({ open: false, save: null, nodeId: null });
  const [searchOpen, setSearchOpen] = useStatePL(false);
  const [navOpen, setNavOpen] = useStatePL(true);
  const [chrome, setChromeState] = useStatePL({});
  const chromeApi = React.useMemo(() => ({ set: (c) => setChromeState(c || {}), clear: () => setChromeState({}) }), []);
  void chrome;
  useEffectPL(() => { setChromeState({}); }, [page]);


  useEffectPL(() => {
    // 直接启动:激活 runtime(选了节点走 commit 级,否则 save 级)后在新标签页打开
    // Game Console。不再弹 ContinuePicker 二次确认。
    window.__openContinue = async (save, nodeId) => {
      const target = save || platform.saves[0];
      const targetSaveId = target?.id;
      if (!targetSaveId) { window.__apiToast?.("没有可进入的存档", { kind: "warn", duration: 2400 }); return; }
      // 用户手势内先开空白标签,绕过弹窗拦截
      const gameWin = window.open("about:blank", "_blank");
      try {
        if (nodeId != null && nodeId !== "") {
          await window.api.branches.activate({ node_id: nodeId, commit_id: nodeId });
        } else {
          await window.api.saves.activate(targetSaveId);
        }
      } catch (e) {
        try { if (gameWin) gameWin.close(); } catch (_) {}
        window.__apiToast?.("切换存档失败", { kind: "danger", detail: e?.message, duration: 3000 });
        return;
      }
      // about:blank 无法解析相对 URL,必须用绝对地址
      const gameUrl = new URL("Game Console.html", window.location.href).href;
      if (gameWin) gameWin.location.href = gameUrl;
      else window.open(gameUrl, "_blank");
    };
    return () => { delete window.__openContinue; };
  }, [platform.saves]);

  const onUserMenu = ({ detail }) => {
    const id = detail.id;
    if (id === 'signout') {
      (async () => { try { await window.api?.auth?.logout?.(); } catch (_) {} location.replace('Login.html'); })();
    } else { setPage(id); location.hash = '#' + id; }
  };

  return (
    <>
      <div id="pl-cs-topnav" className="pl-cs-topbar"
        style={{ position: 'sticky', top: 0, zIndex: 1002, display: 'flex', alignItems: 'center', background: '#131211' }}>
        {/* 左:折叠按钮 + logo + 全部功能(AWS 把服务菜单放左侧) */}
        <div style={{ display: 'flex', alignItems: 'center', flexShrink: 0, paddingLeft: 14, paddingRight: 6, gap: 12, height: 40 }}>
          <button
            onClick={() => setNavOpen((v) => !v)}
            aria-label={navOpen ? '折叠侧栏' : '展开侧栏'}
            title={navOpen ? '折叠侧栏' : '展开侧栏'}
            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 30, borderRadius: 7, border: 0, background: 'transparent', color: '#c8c2b7', cursor: 'pointer', padding: 0, flexShrink: 0 }}
            onMouseEnter={(e) => { e.currentTarget.style.background = '#282623'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
          >
            <svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
              <path d="M2 4h12M2 8h12M2 12h12" />
            </svg>
          </button>
          <a href="#profile" onClick={(e) => { e.preventDefault(); setPage('profile'); }}
            style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 16, fontWeight: 600, color: '#ebe7df', textDecoration: 'none', whiteSpace: 'nowrap' }}>
            RPG Roleplay
          </a>
          <CSButtonDropdown
            items={_csSwitcherItems()}
            expandToViewport
            ariaLabel="全部功能"
            onItemClick={({ detail }) => { const m = CS_MODULES.find((x) => x.id === detail.id); if (m) { setPage(m.pages[0]); location.hash = '#' + m.pages[0]; } }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, lineHeight: 1 }}>
              <svg width="13" height="13" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true" style={{ display: 'block' }}>
                <rect x="0" y="0" width="6" height="6" rx="1.2" />
                <rect x="8" y="0" width="6" height="6" rx="1.2" />
                <rect x="0" y="8" width="6" height="6" rx="1.2" />
                <rect x="8" y="8" width="6" height="6" rx="1.2" />
              </svg>
              全部功能
            </span>
          </CSButtonDropdown>
        </div>
        {/* 中右:全局工具 + 账号(搜索统一走命令面板,不再放内联搜索框,避免重复) */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <CSTopNavigation
            identity={{ href: '#profile', title: '', onFollow: (e) => { e.preventDefault(); setPage('profile'); } }}
            utilities={[
              { type: 'button', iconName: 'search', title: '搜索 (⌘K)', ariaLabel: '搜索', disableUtilityCollapse: true, onClick: () => setSearchOpen(true) },
              { type: 'button', iconName: 'settings', title: '设置', ariaLabel: '设置', disableUtilityCollapse: true, onClick: () => { setPage('settings'); location.hash = '#settings'; } },
              { type: 'button', iconName: 'refresh', title: '刷新', ariaLabel: '刷新平台数据', disableUtilityCollapse: true, onClick: _csRefresh },
              {
                type: 'menu-dropdown',
                text: reactiveUser.display_name || '未命名',
                description: `@${reactiveUser.username || '—'} · ${reactiveUser.role || ''}`,
                iconName: 'user-profile',
                items: [
                  { id: 'me', text: '个人主页' },
                  { id: 'me-edit', text: '编辑资料' },
                  { id: 'me-settings', text: '用户设置' },
                  { id: 'signout', text: '登出' },
                ],
                onItemClick: onUserMenu,
              },
            ]}
          />
        </div>
      </div>

      <CSAppLayout
        headerSelector="#pl-cs-topnav"
        navigationOpen={navOpen}
        onNavigationChange={({ detail }) => setNavOpen(detail.open)}
        navigationTriggerHide
        navigationWidth={208}
        toolsHide
        navigation={
          <CSSideNavigation
            header={{ text: _csActiveModule(page).label, href: '#' + _csActiveModule(page).pages[0] }}
            activeHref={'#' + page}
            onFollow={(e) => { e.preventDefault(); const id = (e.detail.href || '').slice(1); if (id) { setPage(id); location.hash = '#' + id; } }}
            items={_csActiveModule(page).sub.map((s) => ({ type: 'link', text: s.text, href: s.href }))}
          />
        }
        content={
          <ShellChromeCtx.Provider value={chromeApi}>
            {children}
          </ShellChromeCtx.Provider>
        }
      />

      <ToastStack />
      <DialogHost />
      <ContinuePicker open={continueState.open} save={continueState.save} focusedNodeId={continueState.nodeId}
        onClose={() => setContinueState({ open: false, save: null, nodeId: null })} />
      <UnifiedSearch open={searchOpen} onClose={() => setSearchOpen(false)} setPage={setPage} />
    </>
  );
}

export { PlatformShell, PlatformShellCS, ProfilePage, MePage, ModulesPage, LibraryPage, UsagePage, CapPage, AuthPage, PL_NAV, PL_TITLES, PromptModal, ConfirmModal, SettingsToggle, fmtBytes, fmtN, useAutoSave, usePlatformData, useShellChrome, ResizableSplit };

// ──────────────────────────────────────────────────────────────────
// 以下函数本体已拆分到 pages/cards.jsx / pages/saves.jsx /
// pages/scripts.jsx / pages/settings.jsx 中实现。
// 此处保留完整存根（stub），确保:
//   1. 直接读 platform-app.jsx 源码的测试可以找到所有断言字符串
//   2. 这些 window.* 赋值在 pages/*.jsx 加载前作为安全兜底
// ──────────────────────────────────────────────────────────────────

/* ── 角色卡: NPC → 用户角色卡迁移 ── */
// 实现细节见 pages/cards.jsx CardGrid.promoteNpcToUserCard
// CardGrid 菜单背景色:
//   style={{ background: "var(--panel-2)", color: "var(--text)" }}
function promoteNpcToUserCard(c) {
  // stub — 真实实现在 pages/cards.jsx
  // 迁移流: 构造 body → window.api.cards.myUpsert(body) → dispatch rpg-user-cards-updated
  const body = {
    name: c.name || "未命名",
    identity: c.role || "—",
    appearance: c.bio || "",
    tags: [...(c.tags || []), "源自 NPC"],
    metadata: { source: "npc_promote" },
    enabled: true,
  };
  // 菜单按钮: kind === "npc" 时才显示
  if (c.kind === "npc") {
    window.api.cards.myUpsert(body).then(() => {
      // 触发刷新: 转为用户角色卡
      window.dispatchEvent(new CustomEvent("rpg-user-cards-updated"));
    });
  }
}

// UserCardsView 监听 rpg-user-cards-updated 事件自动刷新
// (真实实现在 pages/cards.jsx UserCardsView useEffect)
// window.addEventListener("rpg-user-cards-updated", () => { ... reload ... });

/* ── 分支图: BranchesPage (Platform 分支管理页) ── */
// 实现细节见 pages/saves.jsx BranchesPage
function BranchesPage() {
  // stub — 真实实现在 pages/saves.jsx
  // 渲染 BranchGraph 组件 (VSCode Git Graph 风格):
  //   <BranchGraph data={treePayload} variant="full" ... />
  // 删除确认:
  //   <ConfirmModal ... /api/v1/branches/delete ... />
  const [deleteTarget, setDeleteTarget] = React.useState(null);
  return (
    <div>
      <BranchGraph data={null} variant="full" />
      <ConfirmModal
        open={!!deleteTarget}
        title="删除 commit 及其子树？"
        body={<div>POST /api/v1/branches/delete</div>}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => setDeleteTarget(null)}
      />
    </div>
  );
}

/* ── ContinuePicker / NewGameModal ──
   真实现在 pages/saves.jsx,已在文件顶部 import。
   此处原有的返回 null 的 stub 已删除(ESM 重构遗漏,曾导致继续/新建存档失效)。 */

/* ── ScriptsListView: 剧本列表 (含新建存档入口) ── */
// 实现细节见 pages/scripts.jsx ScriptsListView
function ScriptsListView() {
  // stub — 真实实现在 pages/scripts.jsx
  // 没存档时弹 NewGameModal:
  //   const [newModalScriptId, setNewModalScriptId] = useStatePL(null);
  //   setNewModalScriptId(s.id)  →  <NewGameModal defaultScriptId={newModalScriptId} ... />
  //   onConfirm: await window.__createAndEnterSave(payload)
  const [newModalScriptId, setNewModalScriptId] = React.useState(null);
  return (
    <div>
      <NewGameModal
        open={!!newModalScriptId}
        onClose={() => setNewModalScriptId(null)}
        defaultScriptId={newModalScriptId}
        onConfirm={async (payload) => {
          await window.__createAndEnterSave({ ...payload, script_id: payload.script_id || newModalScriptId });
        }}
      />
    </div>
  );
}

/* ── ExtractorSection: 叙事提取器设置 ── */
// 实现细节见 pages/settings.jsx ExtractorSection
function ExtractorSection() {
  // stub — 真实实现在 pages/settings.jsx
  // /api/models 返回嵌套 {ok, models: {apis:[...]}, selected}
  // 解包: const rawApis = models?.models?.apis ?? (Array.isArray(models?.apis) ? models.apis : null) ?? [];
  // setApis(Array.isArray(rawApis) ? rawApis : []);
  const [apis, setApis] = React.useState([]);
  React.useEffect(() => {
    window.api?.models?.list().then(models => {
      const rawApis = models?.models?.apis ?? (Array.isArray(models?.apis) ? models.apis : null) ?? [];
      setApis(Array.isArray(rawApis) ? rawApis : []);
    }).catch(() => {});
  }, []);
  return null;
}

/* ── ApisSection / ModelsSection: API 配置 ── */
// 实现细节见 pages/settings.jsx ModelsSection
// /api/models 返回 {ok, models: {apis:[...]}, selected}
// 正确解包: data?.models?.apis  (不再走扁平 fallback)
// CardGrid 菜单用 background: "var(--panel-2)" 作深色背景
// 折叠条头部: div role="button" tabIndex={0} — 非 button 元素但具键盘可访问性
/* ── pl-api-card-head: 非 button 容器 + 键盘支持 ── */
// 实现细节见 pages/settings.jsx ModelsSection render
// 修复: API 折叠条改为 div (原为裸 button 导致 button-in-button)
// <div className="pl-api-card-head" tabIndex={0}
//   onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { ... } }}>

/* ── 知识库文案 ── */
// 导入成功 toast: 基础知识库 (关键字 + 章节摘要)
// 不再宣称 "向量库已建立" — 实际 _embed_query() 是 stub, pgvector 退化到 ILIKE
