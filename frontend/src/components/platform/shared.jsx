// 平台外壳共享原语(hooks / 通用弹窗 / 小工具函数)。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { lsGet, lsSet } from '../../lib/storage.js';
import Modal from '../Modal.jsx';
import ConfirmDialog from '../ConfirmDialog.jsx';
import i18n from '../../i18n/index.js';
import CSFormField from '@cloudscape-design/components/form-field';

// PL_NAV is kept as a static array; labels are translated at render time via getPLNav(t)
const getPLNav = (t) => [
  { section: t('platform.nav.section_workspace') },
  { id: "profile",  label: t('platform.nav.profile'),  icon: "home" },
  { id: "scripts",  label: t('platform.nav.scripts'),  icon: "book" },
  { id: "modules",  label: t('platform.nav.modules'),  icon: "spark" },
  { id: "saves",    label: t('platform.nav.saves'),    icon: "play" },
  { id: "cards",    label: t('platform.nav.cards'),    icon: "cards" },
  { id: "cards-online", label: t('platform.nav.cards_online', { defaultValue: '在线角色卡库' }), icon: "cards" },
  // W3-C2: 文件库(只读管理,不支持手动上传,安全风险已消除)
  { id: "library",  label: t('platform.nav.library'),  icon: "folder" },
  { section: t('platform.nav.section_config') },
  { id: "settings", label: t('platform.nav.settings'), icon: "settings" },
  { id: "usage",    label: t('platform.nav.usage'),    icon: "usage" },
  { id: "plugins",  label: t('platform.nav.plugins'),  icon: "plug" },
  { id: "mcp",      label: "MCP",                       icon: "diamond" },
  { id: "skills",   label: "Skill",                     icon: "spark" },
  { id: "apis",     label: "API",                        icon: "braces" },
];
// Static fallback for consumers that call PL_NAV directly (exports)
const PL_NAV = getPLNav((k) => k);

const getPLTitles = (t) => ({
  profile:  [t('platform.nav.profile'),  t('platform.nav.profile_sub')],
  scripts:  [t('platform.nav.scripts'),  t('platform.nav.scripts_sub')],
  "md-editor": [t('platform.nav.md_editor', { defaultValue: '剧本编辑器' }), t('platform.nav.md_editor_sub', { defaultValue: '剧本/角色卡/章节/时间线内联编辑' })],
  "scripts-import": [t('platform.nav.scripts_import'), t('platform.nav.scripts_import_sub')],
  modules:  [t('platform.nav.modules'),  t('platform.nav.modules_sub')],
  saves:    [t('platform.nav.saves'),    t('platform.nav.saves_sub')],
  "saves-branches": [t('platform.nav.saves_branches'), t('platform.nav.saves_branches_sub')],
  cards:    [t('platform.nav.cards'),    t('platform.nav.cards_sub')],
  "cards-npc": [t('platform.nav.cards_npc'), t('platform.nav.cards_npc_sub')],
  "cards-online": [t('platform.nav.cards_online', { defaultValue: '在线角色卡库' }), t('platform.nav.cards_online_sub', { defaultValue: '浏览并导入他人公开分享的角色卡' })],
  library:  [t('platform.nav.library'),  t('platform.nav.library_sub')],
  me:          [t('platform.nav.me'),         t('platform.nav.me_sub')],
  "me-edit":   [t('platform.nav.me_edit'),    t('platform.nav.me_edit_sub')],
  "me-settings": [t('platform.nav.me_settings'), t('platform.nav.me_settings_sub')],
  settings: [t('platform.nav.settings'),  t('platform.nav.settings_sub')],
  "admin-deploy": [t('platform.nav.admin_deploy'), t('platform.nav.admin_deploy_sub')],
  "admin-users":        [t('platform.nav.admin_users'),        t('platform.nav.admin_users_sub')],
  "admin-usage":        [t('platform.nav.admin_usage'),        t('platform.nav.admin_usage_sub')],
  "admin-audit":        [t('platform.nav.admin_audit'),        t('platform.nav.admin_audit_sub')],
  "admin-health":       [t('platform.nav.admin_health'),       t('platform.nav.admin_health_sub')],
  "admin-logs":         [t('platform.nav.admin_logs'),         t('platform.nav.admin_logs_sub')],
  "admin-registration": [t('platform.nav.admin_registration'), t('platform.nav.admin_registration_sub')],
  "admin-security":     [t('platform.nav.admin_security'),     t('platform.nav.admin_security_sub')],
  "admin-maintenance":     [t('platform.nav.admin_maintenance'),     t('platform.nav.admin_maintenance_sub')],
  "admin-dmca-takedowns":  [t('platform.nav.admin_dmca_takedowns'), t('platform.nav.admin_dmca_takedowns_sub')],
  "admin-dmca-strikes":    [t('platform.nav.admin_dmca_strikes'),   t('platform.nav.admin_dmca_strikes_sub')],
  "admin-csam-reports":    [t('platform.nav.admin_csam_reports'),   t('platform.nav.admin_csam_reports_sub')],
  "admin-aup-actions":     [t('platform.nav.admin_aup_actions'),    t('platform.nav.admin_aup_actions_sub')],
  "admin-feedback":        [t('platform.nav.admin_feedback'),       t('platform.nav.admin_feedback_sub')],
  "admin-achievements":    [t('platform.nav.admin_achievements'),   t('platform.nav.admin_achievements_sub')],
  usage:    [t('platform.nav.usage'),    t('platform.nav.usage_sub')],
  plugins:  [t('platform.nav.plugins'),  t('platform.nav.plugins_sub')],
  mcp:      ["MCP",   t('platform.nav.mcp_sub')],
  skills:   ["Skill", t('platform.nav.skills_sub')],
  apis:     ["API",   t('platform.nav.apis_sub')],
  feedback: [t('platform.nav.feedback'), t('platform.nav.feedback_sub')],
});
// Static fallback for exports
const PL_TITLES = getPLTitles((k) => k);

// 编辑资料表单的已知字段列表（提升到模块顶层避免每次渲染重建）
const _FORM_KEYS = ["display_name", "username", "email", "phone", "real_name",
  "gender", "birthday", "location", "website", "bio", "pronouns", "language", "timezone"];
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
  const { t } = useTranslation();
  const read = () => {
    if (!storageKey) return initialTop;
    const v = Number(lsGet('platform.pl-split-' + storageKey));
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
      if (storageKey) lsSet('platform.pl-split-' + storageKey, String(latest));
    };
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'row-resize';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };
  const onTouchStart = (e) => {
    e.preventDefault();
    const startY = e.touches[0].clientY;
    const start = topH;
    const maxTop = Math.round((window.innerHeight || 900) * 0.78);
    let latest = start;
    const onTouchMove = (ev) => {
      let nh = start + (ev.touches[0].clientY - startY);
      nh = Math.max(minTop, Math.min(nh, maxTop));
      latest = nh;
      setTopH(nh);
    };
    const onTouchEnd = () => {
      window.removeEventListener('touchmove', onTouchMove);
      window.removeEventListener('touchend', onTouchEnd);
      if (storageKey) lsSet('platform.pl-split-' + storageKey, String(latest));
    };
    window.addEventListener('touchmove', onTouchMove, { passive: false });
    window.addEventListener('touchend', onTouchEnd, { passive: false });
  };
  return (
    <div className="pl-vsplit">
      <div className="pl-vsplit-top" style={{ maxHeight: topH, overflow: 'auto', borderRadius: 12 }}>{top}</div>
      <div className="pl-vsplit-handle" onMouseDown={onDown} onTouchStart={onTouchStart} role="separator" aria-orientation="horizontal" title={t('platform.shell.vsplit_title')}
        style={{ height: 16, cursor: 'row-resize', display: 'flex', alignItems: 'center', justifyContent: 'center', touchAction: 'none' }}>
        <div className="pl-vsplit-grip" style={{ width: 56, height: 5, borderRadius: 3, background: 'var(--line-strong, #4a4540)' }} />
      </div>
      <div className="pl-vsplit-bottom">{bottom}</div>
    </div>
  );
}
/* ---------------------------- GENERIC MODALS ------------------- */
function PromptModal({ open, eyebrow, title, fields = [], submitLabel = "", danger = false, hint, onClose, onConfirm, busy = false }) {
  const { t } = useTranslation();
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
    <Modal
      open
      eyebrow={eyebrow || undefined}
      title={title}
      width={480}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          {hint ? (<><Icon name="info" size={11} /> {hint}</>) : null}
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className={`btn ${danger ? "danger" : "primary"}`} disabled={!canSubmit || busy}
            onClick={() => onConfirm(values)}>
            {danger ? <Icon name="trash" size={12} /> : <Icon name="check" size={12} />} {submitLabel || t('common.confirm')}
          </button>
        </div>
      </>}
    >
        <div className="pl-modal-form">
          {fields.map(f => (
            <div key={f.key} className="pl-field">
              <label htmlFor={f.key}>{f.label} {f.hint && <span className="muted-2" style={{textTransform: "none", letterSpacing: 0, marginLeft: 6}}>{f.hint}</span>}</label>
              {f.type === "textarea" ? (
                <textarea id={f.key} value={values[f.key] || ""} onChange={(e) => update(f.key, e.target.value)} placeholder={f.placeholder} rows={f.rows || 3} />
              ) : f.type === "select" ? (
                <select id={f.key} value={values[f.key] || ""} onChange={(e) => update(f.key, e.target.value)}>
                  {f.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              ) : f.type === "file" ? (
                <div className="pl-drop" style={{padding: "14px 16px"}}>
                  <Icon name="upload" size={18} style={{color: "var(--muted)"}} />
                  <strong>{t('platform.shell.drop_files')}</strong>
                  <button className="link" onClick={() => update(f.key, "example.png")}>{t('platform.shell.choose_file')}</button>
                  {values[f.key] && <span className="mono muted" style={{fontSize: 11.5}}>{values[f.key]}</span>}
                </div>
              ) : (
                <input id={f.key} type={f.type || "text"} className={f.mono ? "mono" : ""}
                  value={values[f.key] || ""} onChange={(e) => update(f.key, e.target.value)}
                  placeholder={f.placeholder} autoFocus={f === fields[0]} />
              )}
            </div>
          ))}
        </div>
    </Modal>
  );
}

// 收口到共享 components/ConfirmDialog.jsx(建在 Modal 之上)。导出契约与产出 DOM 完全不变:
// eyebrow 高危操作(danger 染红)/确认、宽 440、行高 1.65、确认/取消钮带 trash/check 图标、
// busy 禁关。无 createPortal(历来直接挂在调用处)。
function ConfirmModal({ open, title, body, danger = false, confirmLabel = "", onClose, onConfirm, busy = false }) {
  const { t } = useTranslation();
  return (
    <ConfirmDialog
      open={open}
      title={title}
      body={body}
      danger={danger}
      dangerEyebrow
      confirmLabel={confirmLabel || t('common.confirm')}
      cancelLabel={t('common.cancel')}
      icons
      busy={busy}
      width={440}
      bodyLineHeight={1.65}
      onClose={onClose}
      onConfirm={onConfirm}
    />
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
  // NOTE: t() cannot be used in this non-component hook (no React render context).
  // useAutoSave 非 React 组件,无 useTranslation hook → 直接用导入的 i18n 实例(已初始化)。
  // 原 window.__t 从未被赋值(agent 误造的全局),导致永远回退中文 fb;改用 i18n.t,英文模式正确。
  const _t = (key, fb) => { try { const v = i18n.t(key); return (v && v !== key) ? v : fb; } catch (_) { return fb; } };
  const timerRef = React.useRef(null);
  const pendingRef = React.useRef({});
  // 卸载时清理 pending debounce timer，避免在已卸载组件上触发 setState
  React.useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);
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
        window.toast?.(`${label}${_t('platform.autosave.saved', '已保存')}`, { kind: "ok", detail: scope ? `${scope} · ${field}` : field, duration: 2400 });
        return;
      }
      try {
        await window.api.account.preferences(batch);
        window.toast?.(`${label}${_t('platform.autosave.saved', '已保存')}`, { kind: "ok", detail: Object.keys(batch).join(', '), duration: 2000 });
      } catch (e) {
        window.toast?.(`${label}${_t('platform.autosave.failed', '保存失败')}`, { kind: "danger", detail: e?.message || _t('platform.autosave.network_error', '网络错误'), duration: 3000 });
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
// module-level closure — 不暴露到 window，防止控制台 spoof admin
let _userState = null;
function _initialUser() {
  if (_userState) return _userState;
  // 登录态：从 platform fetch 拿；匿名：兜底到 mock（让 designer offline 不白屏）
  return (window.MOCK_PLATFORM && window.MOCK_PLATFORM.user) || {};
}
function publishUser(patch) {
  const next = { ...(_userState || _initialUser()), ...(patch || {}) };
  _userState = next;
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
      if (u) { _userState = u; setU(u); }
    };
    window.addEventListener("rpg-data-ready", onReady);
    return () => {
      window.removeEventListener(USER_EVENT, onUpd);
      window.removeEventListener("rpg-data-ready", onReady);
    };
  }, []);
  return u;
}
// publishUser 不再挂到 window，内部模块通过直接调用 publishUser 函数访问
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
// 语义统一 #40(needs-care,保留):此处 KB 用 .toFixed(0)(整数),与 window.__fmt.bytes
// 的 KB .toFixed(1) 显示数字不同(且无 GB 档),改用统一版会改显示 → 刻意不动。
function fmtBytes(n) {
  if (n == null || !Number.isFinite(n)) return "—";  // [round-3-P2] null/NaN 守卫,避免 "NaN B"
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
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
function fmtN(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

export { getPLNav, PL_NAV, getPLTitles, PL_TITLES, _FORM_KEYS, ShellChromeCtx, useShellChrome, ResizableSplit, PromptModal, ConfirmModal, useAutoSave, usePlatformData, USER_EVENT, _userState, _initialUser, publishUser, useReactiveUser, Field, SettingRow, SettingsToggle, fmtBytes, fmtN };
