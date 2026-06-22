/* MobileGame — 移动原生游戏台(P2)。
   ★ UI 按设计稿(离线版 file 03)重写为移动原生组件,不复用电脑端 LeftRail/TopBar/
     ChatArea/RightPanel/Composer/ConfirmStrip。
   ★ 逻辑/数据全部复用 game-console.jsx 的 run-loop:通过 `gc` prop 透传 state + handler。
   ★ 内容对齐电脑端真实功能(设计稿砍掉的逐一找回):历史回顾 / 搜索本档 / 游戏内设置 /
     反馈 / 剧本版本 / 本轮结构化更新 / 手动保存 / 多tab冲突 / 套路提示 / SSE 调试 / 调试面板。

   面板内容(状态/记忆/世界书/人物/时间线/上下文/规则/调试)的移动原生实装在 ./panels.jsx
   (P2c);本文件负责外壳 + 聊天 + composer + 抽屉/sheet 骨架 + 找回的功能入口。 */
import React from 'react';
import { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';
import { Icon } from '../icons.jsx';
import { MobileComposer } from '../Composer.jsx';
import { MobilePanel, MOBILE_PANEL_TABS } from './panels.jsx';
import AgentModelPicker from '../../components/AgentModelPicker.jsx';
import { useStickToBottom } from '../../hooks/useStickToBottom.js';

const SLASH_GROUPS = () => [
  { title: i18n.t('mobile.game.slash.group_query'), items: [
    { id: 'status', trig: '/status', label: i18n.t('mobile.game.slash.status'), tab: 'status' },
    { id: 'debug', trig: '/debug', label: i18n.t('mobile.game.slash.debug'), sse: true },
  ] },
  { title: i18n.t('mobile.game.slash.group_state'), items: [
    { id: 'set', trig: '/set ', label: i18n.t('mobile.game.slash.set') },
    { id: 'loc', trig: '/loc ', label: i18n.t('mobile.game.slash.loc') },
    { id: 'time', trig: '/time ', label: i18n.t('mobile.game.slash.time') },
    { id: 'rel', trig: '/rel ', label: i18n.t('mobile.game.slash.rel') },
    { id: 'var', trig: '/var ', label: i18n.t('mobile.game.slash.var') },
  ] },
  { title: i18n.t('mobile.game.slash.group_memory'), items: [
    { id: 'pin', trig: '/pin ', label: i18n.t('mobile.game.slash.pin') },
    { id: 'note', trig: '/note ', label: i18n.t('mobile.game.slash.note') },
  ] },
  { title: i18n.t('mobile.game.slash.group_system'), items: [
    { id: 'save', trig: '/save', label: i18n.t('mobile.game.slash.save') },
    { id: 'retry', trig: '/retry', label: i18n.t('mobile.game.slash.retry') },
  ] },
];

const PERMISSIONS = () => [
  { id: 'read_only', icon: 'eye', label: i18n.t('mobile.game.permission.read_only_label'), desc: i18n.t('mobile.game.permission.read_only_desc') },
  { id: 'review', icon: 'shield', label: i18n.t('mobile.game.permission.review_label'), desc: i18n.t('mobile.game.permission.review_desc') },
  { id: 'full_access', icon: 'unlock', label: i18n.t('mobile.game.permission.full_access_label'), desc: i18n.t('mobile.game.permission.full_access_desc') },
];

function paras(text) {
  return String(text || '').split(/\n\n+/).map((p, j) => (
    <p key={j}>{p.split(/\n/).map((ln, k) => <React.Fragment key={k}>{k ? <br /> : null}{ln}</React.Fragment>)}</p>
  ));
}

/* ===================== 左抽屉:存档 / 记忆 / 分支 / 结构化更新 ===================== */
function LeftDrawer({ open, onClose, gc }) {
  const { t } = useTranslation();
  const { game, activeSave, realSaves, onSwitchSave, onMemoryMode, onSave, onNew, onExit } = gc;
  const memMode = (game.memory && game.memory.mode) || 'normal';
  const updates = Array.isArray(game?.memory?.last_structured_updates) ? game.memory.last_structured_updates : [];
  const MEM = [
    { id: 'normal', icon: 'memory', label: t('mobile.game.memory.normal_label'), desc: t('mobile.game.memory.normal_desc') },
    { id: 'deep', icon: 'spark', label: t('mobile.game.memory.deep_label'), desc: t('mobile.game.memory.deep_desc') },
    { id: 'off', icon: 'eye_off', label: t('mobile.game.memory.off_label'), desc: t('mobile.game.memory.off_desc') },
  ];
  const md = MEM.find((m) => m.id === memMode) || MEM[0];
  return (
    <div className={`drawer drawer-left ${open ? 'open' : ''}`}>
      <div className="drawer-head">
        <div className="save-thumb" style={{ width: 32, height: 32, borderRadius: 9 }}><Icon name="logo" size={15} /></div>
        <h2>{t('mobile.game.left_drawer.title')}</h2>
        <button className="drawer-x" onClick={onClose}><Icon name="close" size={17} /></button>
      </div>
      <div className="drawer-body scroll">
        {onExit && (
          <div className="ld-section" style={{ paddingBottom: 0, borderBottom: 'none' }}>
            <button className="ld-backapp" onClick={onExit}>
              <Icon name="chevron_left" size={17} /><span>{t('mobile.game.left_drawer.back_home')}</span><Icon name="home" size={15} style={{ marginLeft: 'auto', opacity: 0.6 }} />
            </button>
          </div>
        )}
        <div className="ld-section">
          <div className="ld-head"><span>{t('mobile.game.left_drawer.saves_section')}</span><button className="add" onClick={() => { onClose(); onNew && onNew(); }}><Icon name="plus" size={14} /></button></div>
          {(realSaves || []).map((s) => (
            <button key={s.id} className={`save-card ${s.id === activeSave?.id ? 'current' : ''}`} onClick={() => { onClose(); onSwitchSave && onSwitchSave(s.id); }} style={{ width: '100%', textAlign: 'left' }}>
              <span className="save-thumb"><Icon name={s.id === activeSave?.id ? 'play' : 'save'} size={16} /></span>
              <span className="save-info">
                <strong>{s.title || t('mobile.game.left_drawer.save_default_title', { id: s.id })}</strong>
                <span className="meta">{s.script_title || ''}<span className="mono">· {Number(s.branch_count) || 0} {t('mobile.game.left_drawer.branches')}{s.updated_at ? ` · ${s.updated_at}` : ''}</span></span>
              </span>
              {s.id === activeSave?.id && <span className="save-check"><Icon name="check" size={17} /></span>}
            </button>
          ))}
          {(!realSaves || !realSaves.length) && <div className="pl-empty" style={{ padding: '12px 4px', fontSize: 12.5 }}>{t('mobile.game.left_drawer.no_saves')}</div>}
          <button className="btn ghost" style={{ marginTop: 8, width: '100%' }} onClick={() => { onClose(); onSave && onSave(); }}><Icon name="save" size={13} /> {t('mobile.game.left_drawer.manual_save')}</button>
        </div>

        <div className="ld-section">
          <div className="ld-head"><span>{t('mobile.game.memory.section_title')}</span></div>
          <div className="seg">
            {MEM.map((m) => (
              <button key={m.id} className={`${memMode === m.id ? 'active' : ''} ${m.id === 'deep' ? 'deep' : ''}`} onClick={() => onMemoryMode && onMemoryMode(m.id)}>
                <Icon name={m.icon} size={16} />{m.label}
              </button>
            ))}
          </div>
          <p className="mem-desc"><strong>{md.label}</strong> · {md.desc}</p>
        </div>

        {/* 找回:本轮结构化更新(电脑端 LeftRail 有,设计稿无) */}
        <div className="ld-section" style={{ borderBottom: 'none' }}>
          <div className="ld-head"><span>{t('mobile.game.left_drawer.structured_updates')}</span><span className="mono" style={{ fontSize: 9.5, color: 'var(--muted-3)' }}>{updates.length}</span></div>
          {updates.length ? updates.map((u, i) => (
            <div key={i} className="mono" style={{ fontSize: 11.5, color: 'var(--text-quiet)', padding: '3px 2px', borderBottom: '1px dashed var(--line-soft)' }}>
              {typeof u === 'string' ? u : (u.field ? `${u.field}: ${u.value ?? ''}` : JSON.stringify(u))}
            </div>
          )) : <div className="pl-empty" style={{ padding: '8px 2px', fontSize: 12 }}>{t('mobile.game.left_drawer.no_updates')}</div>}
        </div>
      </div>
    </div>
  );
}

/* ===================== 右抽屉:世界面板(移动原生面板, panels.jsx) ===================== */
function RightDrawer({ open, onClose, gc }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState(gc.activeTab || 'status');
  useEffect(() => { if (gc.activeTab) setTab(gc.activeTab); }, [gc.activeTab]);
  return (
    <div className={`drawer drawer-right ${open ? 'open' : ''}`}>
      <div className="drawer-head">
        <h2>{t('mobile.game.right_drawer.title')}</h2>
        <button className="drawer-x" onClick={onClose}><Icon name="close" size={17} /></button>
      </div>
      <div className="panel-tabs scroll">
        {MOBILE_PANEL_TABS.map((tb) => (
          <button key={tb.id} className={`ptab ${tab === tb.id ? 'active' : ''}`} onClick={() => { setTab(tb.id); gc.setActiveTab && gc.setActiveTab(tb.id); }}>
            <Icon name={tb.icon} size={15} />{tb.label}
          </button>
        ))}
      </div>
      <div className="drawer-body scroll">
        <MobilePanel tab={tab} state={gc.game} />
      </div>
    </div>
  );
}

/* ===================== 底部 sheet 宿主 ===================== */
function SheetHost({ sheet, onClose, gc, msgActions }) {
  const { t } = useTranslation();
  const type = sheet?.type;
  let title = '', sub = '', body = null;

  if (type === 'slash') {
    title = t('mobile.game.sheet.slash_title'); sub = t('mobile.game.sheet.slash_sub');
    const slashGroups = SLASH_GROUPS();
    body = (
      <div style={{ display: 'grid', gap: 14 }}>
        {slashGroups.map((g) => (
          <div key={g.title}>
            <div className="sheet-group-label">{g.title}</div>
            <div className="sheet-list">
              {g.items.map((c) => (
                <button key={c.id} className="sheet-item" onClick={() => { onClose(); gc.runSlash(c); }}>
                  <span className="sheet-ico" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent)' }}>/</span>
                  <span className="sheet-tx"><strong>{c.label}</strong><span className="mono">{c.trig.trim()}</span></span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  } else if (type === 'attach') {
    title = t('mobile.game.sheet.attach_title'); sub = t('mobile.game.sheet.attach_sub');
    const groups = [
      { title: t('mobile.game.sheet.attach_group_local'), items: [{ id: 'file', ic: 'file', t: t('mobile.game.sheet.attach_file') }, { id: 'image', ic: 'image', t: t('mobile.game.sheet.attach_image') }] },
      { title: t('mobile.game.sheet.attach_group_script'), items: [{ id: 'chapter', ic: 'book', t: t('mobile.game.sheet.attach_chapter') }, { id: 'card', ic: 'cards', t: t('mobile.game.sheet.attach_card') }, { id: 'world', ic: 'world', t: t('mobile.game.sheet.attach_worldbook') }] },
    ];
    body = (
      <div style={{ display: 'grid', gap: 14 }}>
        {groups.map((g) => (
          <div key={g.title}>
            <div className="sheet-group-label">{g.title}</div>
            <div className="sheet-list">
              {g.items.map((it) => (
                <button key={it.id} className="sheet-item" onClick={() => { onClose(); gc.onAttachPick && gc.onAttachPick(it); }}>
                  <span className="sheet-ico"><Icon name={it.ic} size={18} /></span>
                  <span className="sheet-tx"><strong>{it.t}</strong></span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  } else if (type === 'model') {
    title = t('mobile.game.sheet.model_title'); sub = t('mobile.game.sheet.model_sub');
    body = <ModelSheetBody gc={gc} onClose={onClose} />;
  } else if (type === 'permission') {
    title = t('mobile.game.sheet.permission_title'); sub = t('mobile.game.sheet.permission_sub');
    const permissions = PERMISSIONS();
    body = (
      <div className="sheet-list">
        {permissions.map((pm) => (
          <button key={pm.id} className={`sheet-item ${pm.id === gc.permission ? 'active' : ''}`} onClick={() => { gc.setPermission && gc.setPermission(pm.id); onClose(); }}>
            <span className="sheet-ico"><Icon name={pm.icon} size={18} /></span>
            <span className="sheet-tx"><strong>{pm.label}</strong><span>{pm.desc}</span></span>
            {pm.id === gc.permission && <span className="sheet-check"><Icon name="check" size={18} /></span>}
          </button>
        ))}
      </div>
    );
  } else if (type === 'context') {
    const u = gc.lastUsage || {};
    const pct = Math.round(u.context_pct || 0);
    title = t('mobile.game.sheet.context_title');
    sub = u.context_max ? t('mobile.game.sheet.context_sub_usage', { used: Number(u.context_used || 0).toLocaleString(), max: Number(u.context_max).toLocaleString(), pct }) : t('mobile.game.sheet.context_sub_empty');
    body = (
      <>
        {u.context_max ? <div className="ctx-bar"><i style={{ width: pct + '%', background: 'var(--accent)' }} /></div> : null}
        <div className="ctx-list">
          {u.input_tokens != null && <div className="ctx-row"><span className="ctx-label">{t('mobile.game.sheet.ctx_input')}</span><span className="ctx-tok mono">{Number(u.input_tokens).toLocaleString()}</span></div>}
          {u.cached_input_tokens ? <div className="ctx-row"><span className="ctx-label">{t('mobile.game.sheet.ctx_cache_hit')}</span><span className="ctx-tok mono">{Number(u.cached_input_tokens).toLocaleString()}</span></div> : null}
          {u.output_tokens != null && <div className="ctx-row"><span className="ctx-label">{t('mobile.game.sheet.ctx_output')}</span><span className="ctx-tok mono">{Number(u.output_tokens).toLocaleString()}</span></div>}
          {u.reasoning_tokens ? <div className="ctx-row"><span className="ctx-label">{t('mobile.game.sheet.ctx_reasoning')}</span><span className="ctx-tok mono">{Number(u.reasoning_tokens).toLocaleString()}</span></div> : null}
          {u.cost_usd ? <div className="ctx-row"><span className="ctx-label">{t('mobile.game.sheet.ctx_cost')}</span><span className="ctx-tok mono">${Number(u.cost_usd).toFixed(4)}</span></div> : null}
          {u.model ? <div className="ctx-row"><span className="ctx-label">{t('mobile.game.sheet.ctx_model')}</span><span className="ctx-tok mono">{u.model}</span></div> : null}
        </div>
        <p className="confirm-note" style={{ paddingTop: 12 }}>{t('mobile.game.sheet.context_note')}</p>
      </>
    );
  } else if (type === 'msg') {
    const idx = sheet.data;
    const m = gc.history[idx] || {};
    const isGM = m.role === 'assistant';
    title = isGM ? t('mobile.game.sheet.msg_title_gm') : t('mobile.game.sheet.msg_title_player');
    body = (
      <div className="sheet-list">
        <button className="sheet-item" onClick={() => { onClose(); msgActions.copy(idx); }}>
          <span className="sheet-ico"><Icon name="copy" size={18} /></span>
          <span className="sheet-tx"><strong>{t('mobile.game.sheet.msg_copy')}</strong><span>{t('mobile.game.sheet.msg_copy_desc')}</span></span>
        </button>
        <button className="sheet-item" onClick={() => { onClose(); msgActions.fork(idx); }}>
          <span className="sheet-ico"><Icon name="fork" size={18} /></span>
          <span className="sheet-tx"><strong>{t('mobile.game.sheet.msg_fork')}</strong><span>{t('mobile.game.sheet.msg_fork_desc')}</span></span>
        </button>
        {isGM && (
          <button className="sheet-item" onClick={() => { onClose(); msgActions.regen(idx); }}>
            <span className="sheet-ico"><Icon name="refresh" size={18} /></span>
            <span className="sheet-tx"><strong>{t('mobile.game.sheet.msg_regen')}</strong><span>{t('mobile.game.sheet.msg_regen_desc')}</span></span>
          </button>
        )}
        <div className="sheet-divider" />
        <button className="sheet-item danger" onClick={() => { onClose(); msgActions.rollback(idx); }}>
          <span className="sheet-ico"><Icon name="trash" size={18} /></span>
          <span className="sheet-tx"><strong>{t('mobile.game.sheet.msg_rollback')}</strong><span>{t('mobile.game.sheet.msg_rollback_desc')}</span></span>
        </button>
      </div>
    );
  } else if (type === 'overflow') {
    title = t('mobile.game.sheet.overflow_title');
    const items = [
      { id: 'history', ic: 'history', t: t('mobile.game.sheet.overflow_history'), fn: gc.onOpenHistory },
      { id: 'search', ic: 'search', t: t('mobile.game.sheet.overflow_search'), fn: gc.onOpenSearch },
      { id: 'settings', ic: 'settings', t: t('mobile.game.sheet.overflow_settings'), fn: gc.onOpenSettings },
      { id: 'sse', ic: 'braces', t: t('mobile.game.sheet.overflow_sse'), fn: gc.onShowSse },
      { id: 'feedback', ic: 'feedback', t: t('mobile.game.sheet.overflow_feedback'), fn: gc.onOpenFeedback },
    ];
    body = (
      <div className="sheet-list">
        {items.map((it) => (
          <button key={it.id} className="sheet-item" onClick={() => { onClose(); it.fn && it.fn(); }}>
            <span className="sheet-ico"><Icon name={it.ic} size={18} /></span>
            <span className="sheet-tx"><strong>{it.t}</strong></span>
          </button>
        ))}
      </div>
    );
  } else if (type === 'rollback_confirm') {
    const idx = sheet.data;
    title = t('mobile.game.sheet.msg_rollback');
    body = (
      <div className="sheet-list">
        <p className="confirm-note" style={{ padding: '0 4px 12px' }}>{t('mobile.game.toast.rollback_confirm')}</p>
        <button className="sheet-item danger" onClick={() => { onClose(); msgActions._doRollback(idx); }}>
          <span className="sheet-ico"><Icon name="trash" size={18} /></span>
          <span className="sheet-tx"><strong>{t('mobile.game.sheet.msg_rollback')}</strong></span>
        </button>
      </div>
    );
  }

  return (
    <div className={`sheet-wrap ${sheet ? 'show' : ''}`}>
      <div className="sheet-scrim" onClick={onClose} />
      <div className="sheet">
        <div className="sheet-grip" />
        {title && <div className="sheet-title">{title}</div>}
        {sub && <div className="sheet-sub">{sub}</div>}
        {body}
      </div>
    </div>
  );
}

/* ModelSheetBody — 游戏内「对话模型」切换。
   重构:不再自造第二套选择器(自 fetch /api/models + 自过滤 + 直调 models.select),
   而是复用全站唯一规范组件 AgentModelPicker(与桌面 game-composer ModelPopover 同路径:
   persistShape="models_select" + saveId → 有存档时存档级切换,否则改全局 gm 偏好)。
   它自带:已配 key 过滤 / cheap 档 / health / pricing / 自定义手填 等收敛行为。
   onChange(apiId, modelReal):① 回填 gc.model 供底部 chip 标签刷新;② 广播 game-state-refresh;
   ③ 关闭 sheet(与桌面 onPick→toggleModel 一致)。 */
function ModelSheetBody({ gc, onClose }) {
  // saveId 解析与桌面端 Composer 同源:优先 activeSave.id,回退 game._raw.save_id。
  const saveId = (gc.activeSave && gc.activeSave.id)
    || (gc.game && gc.game._raw && gc.game._raw.save_id)
    || null;
  const onPicked = (apiId, modelReal, source) => {
    if (!apiId || !modelReal) return;
    gc.setModel && gc.setModel({ id: modelReal, api_id: apiId, label: modelReal });
    // source='init' 是挂载时「当前模型」回声(非用户换模型)—— 只回填底部 chip,绝不关 sheet/弹 toast/刷新,
    // 否则 sheet 一打开就被这条回声立刻关掉(与桌面 ModelPopover 同一个 bug)。只有 'user' 才收口。
    if (source !== 'user') return;
    try { window.dispatchEvent(new CustomEvent('game-state-refresh')); } catch (_) {}
    window.__apiToast?.(i18n.t('mobile.game.model_sheet.toast_switched', { model: modelReal }), { kind: 'ok', duration: 1500 });
    onClose();
  };
  return (
    <AgentModelPicker
      prefPrefix="gm"
      persistShape="models_select"
      saveId={saveId}
      variant="popover"
      showHealth
      showPricing
      onChange={onPicked}
    />
  );
}

/* ===================== 主组件 ===================== */
export function MobileGame(gc) {
  const { t } = useTranslation();
  const {
    game, history, runState, text, setText, onSend, onStop, attachments,
    permission, pendingWrites, pendingQuestions, onApprove, onReject, onAnswerQuestion, onDismissConfirm,
    clicheNotice, onRetryCliche, onDismissCliche, hasError, activeSave, tabConflictBanner, setTabConflictBanner,
  } = gc;

  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const [peekOpen, setPeekOpen] = useState(false);
  const [sheet, setSheet] = useState(null);
  const [pressed, setPressed] = useState(null);
  const chatRef = useRef(null);
  const taRef = useRef(null);
  const lpTimer = useRef(null);

  const running = runState.running;
  const anyOverlay = leftOpen || rightOpen || !!sheet;

  // 此前手机游戏【完全没有守卫】→ 每次输出无条件拽回底部。粘底守卫收口到 useStickToBottom
  // (逐字等价:threshold 80 / 双守卫 360 / 首屏·末条玩家策略 / mode 'smooth' 即原 scrollBottom(true) /
  // scrollOnMount 即挂载 scrollBottom(false))。手机游戏无「回到最新」按钮 → withButton:false。
  const _last = history && history[history.length - 1];
  useStickToBottom(chatRef, {
    deps: [history.length, running],
    lastIsUser: !!(_last && _last.role === 'user'),
    hasContent: history.length > 0,
    mode: 'smooth',
    withButton: false,
    scrollOnMount: true,
  });

  // 手机 GM 询问可折叠(抽屉):默认展开;新询问到达自动展开;用户可手动点头部收起腾出视野。
  // 发消息时 running=true → confirm-zone 自动隐藏(相当于关上一条),新询问回来 qSig 变→自动展开。
  const [qCollapsed, setQCollapsed] = useState(false);
  const qSig = (pendingQuestions || []).map((q) => q && q.id).join(',');
  useEffect(() => { setQCollapsed(false); }, [qSig]);
  useEffect(() => { const ta = taRef.current; if (!ta) return; ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'; }, [text]);

  const closeAll = () => { setLeftOpen(false); setRightOpen(false); };

  // 长按消息 → 操作 sheet；unmount 时清理挂起的 timer 防内存泄漏
  useEffect(() => () => clearTimeout(lpTimer.current), []);
  const startPress = (idx) => { lpTimer.current = setTimeout(() => { setPressed(idx); if (navigator.vibrate) navigator.vibrate(12); setSheet({ type: 'msg', data: idx }); }, 420); };
  const cancelPress = () => clearTimeout(lpTimer.current);

  const msgActions = {
    copy: (idx) => { try { navigator.clipboard?.writeText(gc.history[idx]?.content || ''); } catch (_) {} setPressed(null); window.__apiToast?.(t('mobile.game.toast.copied'), { kind: 'ok', duration: 1200 }); },
    regen: (idx) => { setPressed(null); gc.onRegenerate && gc.onRegenerate(idx); },
    fork: async (idx) => {
      setPressed(null);
      try {
        const sid = activeSave?.id;
        if (!sid) return;
        await window.api.branches.continueFrom({ save_id: sid, message_index: idx });
        gc.reloadState && gc.reloadState();
        window.__apiToast?.(t('mobile.game.toast.fork_ok'), { kind: 'ok', icon: 'fork' });
      } catch (e) { window.__apiToast?.(t('mobile.game.toast.fork_fail'), { kind: 'danger', detail: e?.message }); }
    },
    rollback: (idx) => {
      setPressed(null);
      setSheet({ type: 'rollback_confirm', data: idx });
    },
    _doRollback: async (idx) => {
      try {
        const sid = activeSave?.id;
        if (!sid) { window.__apiToast?.(t('mobile.game.toast.rollback_fail'), { kind: 'danger' }); return; }
        await window.api.branches.rollbackToMessage(sid, idx);
        gc.reloadState && gc.reloadState();
        window.__apiToast?.(t('mobile.game.toast.rollback_ok'), { kind: 'ok', icon: 'history' });
      } catch (e) { window.__apiToast?.(t('mobile.game.toast.rollback_fail'), { kind: 'danger', detail: e?.message }); }
    },
  };

  // 斜杠命令分发(复用电脑端 onSlashPick / onSendRaw,recover /save /retry /debug /status)
  const runSlash = (cmd) => {
    if (cmd.id === 'status') { gc.setActiveTab && gc.setActiveTab('status'); setRightOpen(true); return; }
    if (cmd.id === 'debug') { gc.onShowSse && gc.onShowSse(); return; }
    if (cmd.id === 'save') { gc.onSave && gc.onSave(); return; }
    if (cmd.id === 'retry') { gc.onRetry && gc.onRetry(); return; }
    // 其余:插入命令前缀到输入框(对齐电脑端 onSlashPick 的"挑命令"语义)
    if (gc.onSlashPick) gc.onSlashPick(cmd);
    setText((v) => (v && v !== '/' ? v.replace(/\/$/, '') : '') + cmd.trig);
    setTimeout(() => taRef.current?.focus(), 60);
  };

  const w = game.world || {};
  const p = game.player || {};
  const onStage = Array.isArray(game.active_entities) ? game.active_entities
    : (game.encounter && Array.isArray(game.encounter.combatants) ? game.encounter.combatants : []);
  const objective = (game.memory && game.memory.current_objective) || '';
  const pendCount = (pendingWrites?.length || 0) + (pendingQuestions?.length || 0);
  const suggestions = Array.isArray(game.suggestions) ? game.suggestions : [];

  const submit = () => { const tt = String(text || '').trim(); if (!tt || running) return; onSend(); };

  return (
    <div className="m-root">
      <div className="app">
        {/* 多 tab 冲突横幅(找回) */}
        {tabConflictBanner && (
          <div className="mg-banner warn">
            <Icon name="warn" size={14} /> {t('mobile.game.topbar.tab_conflict')}
            <button onClick={() => setTabConflictBanner && setTabConflictBanner(null)}>{t('mobile.game.topbar.tab_conflict_continue')}</button>
          </div>
        )}

        {/* 顶栏 */}
        <div className="topbar">
          <button className="tb-exit" onClick={gc.onExit} aria-label={t('mobile.game.topbar.back_app_aria')}><Icon name="chevron_left" size={17} /><span>{t('mobile.game.topbar.back_app_label')}</span></button>
          <button className="tb-btn" onClick={() => setLeftOpen(true)} aria-label={t('mobile.game.topbar.saves_aria')}><Icon name="menu" size={19} /></button>
          <div className="tb-title">
            <strong>{activeSave?.title || game?._raw?.save_title || t('mobile.game.topbar.game_fallback')}</strong>
            <span className="sub"><span className="tb-save-dot" /> {(game.app?.script_title) || (w.phase) || (game.content_pack?.title) || t('mobile.game.topbar.free_mode')}</span>
          </div>
          <button className="tb-btn" onClick={() => setSheet({ type: 'overflow' })} aria-label={t('mobile.game.topbar.more_aria')}><Icon name="more" size={19} /></button>
          <button className="tb-btn accent" onClick={() => setRightOpen(true)} aria-label={t('mobile.game.topbar.world_panel_aria')}><Icon name="compass" size={19} /></button>
        </div>

        {/* 世界 peek */}
        <div className={`peek ${peekOpen ? 'open' : ''}`}>
          <button className="peek-bar" onClick={() => setPeekOpen((v) => !v)}>
            <span className="peek-ico"><Icon name="clock" size={15} /></span>
            <span className="peek-line"><b>{(w.time || '—')}</b>{w.weather ? <><span className="sep">·</span>{w.weather}</> : null}{p.current_location ? <><span className="sep">·</span>{p.current_location}</> : null}</span>
            <span className="peek-chev"><Icon name="chevron_down" size={16} /></span>
          </button>
          {peekOpen && (
            <div className="peek-body">
              <div className="peek-card">
                <div className="peek-grid">
                  <div className="peek-cell"><span className="k"><Icon name="clock" size={11} /> {t('mobile.game.peek.time')}</span><span className="v">{w.time || '—'}</span></div>
                  <div className="peek-cell"><span className="k"><Icon name="cloud" size={11} /> {t('mobile.game.peek.weather')}</span><span className="v">{w.weather || '—'}</span></div>
                  {objective ? <div className="peek-cell wide"><span className="k"><Icon name="flag" size={11} /> {t('mobile.game.peek.objective')}</span><span className="v q">{objective}</span></div> : null}
                </div>
                {onStage.length > 0 && (
                  <div>
                    <div className="k" style={{ fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--muted-2)', marginBottom: 7, display: 'flex', alignItems: 'center', gap: 5 }}><Icon name="cards" size={11} /> {t('mobile.game.peek.on_stage')}</div>
                    <div className="peek-stage">
                      {onStage.map((c, i) => { const nm = c.name || c.id || (t('mobile.game.peek.entity_fallback', { n: i })); return <span key={i} className="who"><span className="peek-av serif">{String(nm).slice(0, 1)}</span>{nm}</span>; })}
                    </div>
                  </div>
                )}
                <button className="peek-full" onClick={() => { setPeekOpen(false); gc.setActiveTab && gc.setActiveTab('status'); setRightOpen(true); }}>{t('mobile.game.peek.view_full_status')} <Icon name="arrow_right" size={13} /></button>
              </div>
            </div>
          )}
        </div>

        {/* 聊天 */}
        <div className="chat scroll" ref={chatRef}>
          {history.map((m, i) => (
            m.role === 'assistant' ? (
              <div key={i} className={`msg msg-gm ${pressed === i ? 'pressed' : ''}`}
                onTouchStart={() => startPress(i)} onTouchEnd={cancelPress} onTouchMove={cancelPress}
                onContextMenu={(e) => { e.preventDefault(); setPressed(i); setSheet({ type: 'msg', data: i }); }}>
                <div className="msg-meta"><span className="msg-tag">GM</span>{m._thinking ? <span className="msg-gts mono">{t('mobile.game.chat.thinking')}</span> : null}</div>
                <div className="msg-body serif">{paras(m.content)}</div>
              </div>
            ) : (
              <div key={i} className={`msg msg-player ${pressed === i ? 'pressed' : ''}`}
                onTouchStart={() => startPress(i)} onTouchEnd={cancelPress} onTouchMove={cancelPress}
                onContextMenu={(e) => { e.preventDefault(); setPressed(i); setSheet({ type: 'msg', data: i }); }}>
                <div className="msg-meta"><span className="msg-tag muted">{t('mobile.game.chat.player_tag')}</span>{m.ts ? <span className="msg-gts mono">{m.ts}</span> : null}</div>
                <div className="msg-body">{m.content}</div>
              </div>
            )
          ))}

          {running && (
            <div className="think">
              <svg className="think-ring" width="24" height="24" viewBox="0 0 24 24">
                <circle cx="12" cy="12" r="9.5" fill="none" stroke="rgba(201,100,66,0.22)" strokeWidth="2.5" />
                <circle cx="12" cy="12" r="9.5" fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinecap="round" strokeDasharray="44 999" className="mg-spin" />
              </svg>
              <div style={{ flex: 1 }}><div className="think-label">{runState.label || t('mobile.game.chat.generating')}</div></div>
              <span className="think-elapsed">{((runState.totalElapsed || 0) / 1000).toFixed(1)}s</span>
            </div>
          )}

          {hasError && !running && (
            <div className="mg-error"><Icon name="warn" size={14} /> {String(hasError)} <button onClick={() => gc.onRetry && gc.onRetry()}>{t('mobile.game.chat.retry')}</button></div>
          )}
        </div>

        {/* 待确认(找回 pending writes/questions + cliche) */}
        {!running && (pendCount > 0 || clicheNotice) && (
          <div className="confirm-zone">
            <div className="confirmbar">
              {clicheNotice && (
                <div className="cb-q">
                  <div className="cb-q-row"><span className="cb-tag gm">{t('mobile.game.confirm.cliche_tag')}</span><span className="cb-q-text">{typeof clicheNotice === 'string' ? clicheNotice : (clicheNotice.message || t('mobile.game.confirm.cliche_default'))}</span></div>
                  <div className="cb-choices">
                    <button className="cb-choice primary" onClick={onRetryCliche}>{t('mobile.game.confirm.cliche_rewrite')}</button>
                    <button className="cb-choice" onClick={onDismissCliche}>{t('mobile.game.confirm.cliche_keep')}</button>
                  </div>
                </div>
              )}
              {pendCount > 0 && (
                <>
                  {/* 折叠头(点击/下拉展开收起,抽屉式)——腾出视野;新询问到达自动展开 */}
                  <button className="cb-head cb-toggle" onClick={() => setQCollapsed((c) => !c)} aria-expanded={!qCollapsed}>
                    <span className="cb-head-l"><Icon name="warn" size={13} /> {t('mobile.game.confirm.pending_label', { count: pendCount })}</span>
                    <span className={'cb-chev' + (qCollapsed ? '' : ' open')}><Icon name="chevron_down" size={15} /></span>
                  </button>
                  <div className={'cb-body' + (qCollapsed ? ' collapsed' : '')}>
                    {(pendingQuestions || []).map((q) => {
                      // 兼容新旧数据形态:question/text 取一,options/choices 取一
                      const qText = q.question || q.text;
                      const qOpts = q.options || q.choices;
                      return (
                        <div key={q.id} className="cb-q">
                          <div className="cb-q-row"><span className="cb-tag gm">{t('mobile.game.confirm.gm_question_tag')}</span><span className="cb-q-text">{qText}</span></div>
                          <div className="cb-choices">
                            {(qOpts || []).map((c, i) => {
                              const label = typeof c === 'string' ? c : (c.text || c.label || c.id || JSON.stringify(c));
                              return (
                                <button key={i} className={'cb-choice' + (i === 0 ? ' primary' : '')} onClick={() => onAnswerQuestion(q, c)}>{label}</button>
                              );
                            })}
                            <button className="cb-choice" onClick={() => onDismissConfirm(q)}>{t('mobile.game.confirm.dismiss')}</button>
                          </div>
                        </div>
                      );
                    })}
                    {(pendingWrites || []).map((wr) => (
                      <div key={wr.id} className={'cb-w ' + (wr.risk || 'low')}>
                        <div className="cb-w-row"><span className={'cb-tag risk ' + (wr.risk || 'low')}><Icon name="warn" size={11} /> {t('mobile.game.confirm.write_tag')}</span><span className="cb-field mono">{wr.field || wr.path || ''}</span></div>
                        {wr.to != null && <div className="cb-to">→ {typeof wr.to === 'object' ? JSON.stringify(wr.to) : String(wr.to)}</div>}
                        {wr.reason && <div className="cb-w-reason">{wr.reason}</div>}
                        <div className="cb-actions">
                          <button className="cb-allow" onClick={() => onApprove(wr)}>{t('mobile.game.confirm.allow')}</button>
                          <button className="cb-deny" onClick={() => onReject(wr)}>{t('mobile.game.confirm.deny')}</button>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* composer(统一组件 MobileComposer:leading=附件 / footer=chip 行 / topSlot=建议词) */}
        <MobileComposer
          value={text}
          onChange={setText}
          onSubmit={submit}
          onStop={onStop}
          running={running}
          placeholder={t('mobile.game.composer.placeholder')}
          sendAria={t('mobile.game.composer.send_aria')}
          stopAria={t('mobile.game.composer.stop_aria')}
          taRef={taRef}
          topSlot={!running && suggestions.length > 0 ? (
            <div className="suggestions scroll">
              {suggestions.map((s, i) => <button key={i} className="sugg" onClick={() => { setText(s); setTimeout(() => taRef.current?.focus(), 50); }}>{s}</button>)}
            </div>
          ) : null}
          leading={(
            <button className="c-plus" onClick={() => setSheet({ type: 'attach' })} aria-label={t('mobile.game.composer.attach_aria')}><Icon name="plus" size={20} /></button>
          )}
          footer={(
            <>
              <button className="c-chip" onClick={() => setSheet({ type: 'slash' })} aria-label={t('mobile.game.composer.slash_aria')}><Icon name="slash" size={14} /></button>
              <button className={`c-chip${gc.game && gc.game.models && gc.game.models.needs_model_config ? ' needs-config' : ''}`} onClick={() => setSheet({ type: 'model' })}><Icon name="sparkle" size={13} /><span className="lbl">{(gc.game && gc.game.models && gc.game.models.needs_model_config) ? t('mobile.game.composer.model_needs_config') : ((gc.model && (gc.model.label || gc.model.id)) || t('mobile.game.composer.model_fallback'))}</span><Icon name="chevron_down" size={12} /></button>
              <button className={`c-chip perm ${permission}`} onClick={() => setSheet({ type: 'permission' })}><Icon name={(PERMISSIONS().find((x) => x.id === permission) || PERMISSIONS()[2]).icon} size={13} /></button>
              <span className="c-spacer" />
              <button className="c-ctx" onClick={() => setSheet({ type: 'context' })} aria-label={t('mobile.game.composer.context_aria')}>
                <svg width="19" height="19" viewBox="0 0 19 19" style={{ transform: 'rotate(-90deg)' }}>
                  <circle cx="9.5" cy="9.5" r="7" fill="none" stroke="var(--line-strong)" strokeWidth="2.4" />
                  <circle cx="9.5" cy="9.5" r="7" fill="none" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" strokeDasharray={`${((gc.lastUsage?.context_pct || 0) / 100) * 2 * Math.PI * 7} 999`} />
                </svg>
                <span className="c-ctx-pct mono">{Math.round(gc.lastUsage?.context_pct || 0)}%</span>
              </button>
            </>
          )}
        />

        {/* 抽屉 + scrim + sheets */}
        <div className={`scrim ${leftOpen || rightOpen ? 'show' : ''}`} onClick={closeAll} />
        <LeftDrawer open={leftOpen} onClose={() => setLeftOpen(false)} gc={gc} />
        <RightDrawer open={rightOpen} onClose={() => setRightOpen(false)} gc={gc} />
        <SheetHost sheet={sheet} onClose={() => { setSheet(null); setPressed(null); }} gc={{ ...gc, runSlash, setActiveTab: gc.setActiveTab }} msgActions={msgActions} />
      </div>
    </div>
  );
}

export default MobileGame;
