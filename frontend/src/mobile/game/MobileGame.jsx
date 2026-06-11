/* MobileGame — 移动原生游戏台(P2)。
   ★ UI 按设计稿(离线版 file 03)重写为移动原生组件,不复用电脑端 LeftRail/TopBar/
     ChatArea/RightPanel/Composer/ConfirmStrip。
   ★ 逻辑/数据全部复用 game-console.jsx 的 run-loop:通过 `gc` prop 透传 state + handler。
   ★ 内容对齐电脑端真实功能(设计稿砍掉的逐一找回):历史回顾 / 搜索本档 / 游戏内设置 /
     反馈 / 剧本版本 / 本轮结构化更新 / 手动保存 / 多tab冲突 / 套路提示 / SSE 调试 / 调试面板。

   面板内容(状态/记忆/世界书/人物/时间线/上下文/规则/调试)的移动原生实装在 ./panels.jsx
   (P2c);本文件负责外壳 + 聊天 + composer + 抽屉/sheet 骨架 + 找回的功能入口。 */
import React from 'react';
import { useState, useRef, useEffect, useCallback } from 'react';
import { Icon } from '../icons.jsx';
import { MobilePanel, MOBILE_PANEL_TABS } from './panels.jsx';

const SLASH_GROUPS = [
  { title: '查询', items: [
    { id: 'status', trig: '/status', label: '查看状态摘要', tab: 'status' },
    { id: 'debug', trig: '/debug', label: '查看上轮检索', sse: true },
  ] },
  { title: '状态写入', items: [
    { id: 'set', trig: '/set ', label: '强制改参 / 设定(自然语言)' },
    { id: 'loc', trig: '/loc ', label: '更新所在位置' },
    { id: 'time', trig: '/time ', label: '推进时间线' },
    { id: 'rel', trig: '/rel ', label: '更新人物关系' },
    { id: 'var', trig: '/var ', label: '设置世界线变量' },
  ] },
  { title: '记忆', items: [
    { id: 'pin', trig: '/pin ', label: '加入固定记忆' },
    { id: 'note', trig: '/note ', label: '玩家笔记' },
  ] },
  { title: '工程', items: [
    { id: 'save', trig: '/save', label: '手动存档' },
    { id: 'retry', trig: '/retry', label: '重试上一轮 GM 输出' },
  ] },
];

const PERMISSIONS = [
  { id: 'read_only', icon: 'eye', label: '只读', desc: 'GM 不写世界状态,只叙事。' },
  { id: 'review', icon: 'shield', label: '审阅', desc: 'GM 想写状态时先列出待确认,你批准才生效。' },
  { id: 'full_access', icon: 'unlock', label: '完全', desc: 'GM 可直接改写世界状态。' },
];

function paras(text) {
  return String(text || '').split(/\n\n+/).map((p, j) => (
    <p key={j}>{p.split(/\n/).map((ln, k) => <React.Fragment key={k}>{k ? <br /> : null}{ln}</React.Fragment>)}</p>
  ));
}

/* ===================== 左抽屉:存档 / 记忆 / 分支 / 结构化更新 ===================== */
function LeftDrawer({ open, onClose, gc }) {
  const { game, activeSave, realSaves, onSwitchSave, onMemoryMode, onSave, onNew, onExit } = gc;
  const memMode = (game.memory && game.memory.mode) || 'normal';
  const updates = Array.isArray(game?.memory?.last_structured_updates) ? game.memory.last_structured_updates : [];
  const MEM = [
    { id: 'normal', icon: 'memory', label: '普通', desc: '每轮召回 6 段历史与原文' },
    { id: 'deep', icon: 'spark', label: '深度', desc: '每轮召回 14 段,更慢但更连贯' },
    { id: 'off', icon: 'eye_off', label: '关闭', desc: '不召回历史,只用当前上下文' },
  ];
  const md = MEM.find((m) => m.id === memMode) || MEM[0];
  return (
    <div className={`drawer drawer-left ${open ? 'open' : ''}`}>
      <div className="drawer-head">
        <div className="save-thumb" style={{ width: 32, height: 32, borderRadius: 9 }}><Icon name="logo" size={15} /></div>
        <h2>存档 · 分支</h2>
        <button className="drawer-x" onClick={onClose}><Icon name="close" size={17} /></button>
      </div>
      <div className="drawer-body scroll">
        {onExit && (
          <div className="ld-section" style={{ paddingBottom: 0, borderBottom: 'none' }}>
            <button className="ld-backapp" onClick={onExit}>
              <Icon name="chevron_left" size={17} /><span>返回应用主页</span><Icon name="home" size={15} style={{ marginLeft: 'auto', opacity: 0.6 }} />
            </button>
          </div>
        )}
        <div className="ld-section">
          <div className="ld-head"><span>存档</span><button className="add" onClick={() => { onClose(); onNew && onNew(); }}><Icon name="plus" size={14} /></button></div>
          {(realSaves || []).map((s) => (
            <button key={s.id} className={`save-card ${s.id === activeSave?.id ? 'current' : ''}`} onClick={() => { onClose(); onSwitchSave && onSwitchSave(s.id); }} style={{ width: '100%', textAlign: 'left' }}>
              <span className="save-thumb"><Icon name={s.id === activeSave?.id ? 'play' : 'save'} size={16} /></span>
              <span className="save-info">
                <strong>{s.title || `存档 #${s.id}`}</strong>
                <span className="meta">{s.script_title || ''}<span className="mono">· {Number(s.branch_count) || 0} 分支{s.updated_at ? ` · ${s.updated_at}` : ''}</span></span>
              </span>
              {s.id === activeSave?.id && <span className="save-check"><Icon name="check" size={17} /></span>}
            </button>
          ))}
          {(!realSaves || !realSaves.length) && <div className="pl-empty" style={{ padding: '12px 4px', fontSize: 12.5 }}>尚未创建存档</div>}
          <button className="btn ghost" style={{ marginTop: 8, width: '100%' }} onClick={() => { onClose(); onSave && onSave(); }}><Icon name="save" size={13} /> 手动保存</button>
        </div>

        <div className="ld-section">
          <div className="ld-head"><span>记忆模式</span></div>
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
          <div className="ld-head"><span>本轮结构化更新</span><span className="mono" style={{ fontSize: 9.5, color: 'var(--muted-3)' }}>{updates.length}</span></div>
          {updates.length ? updates.map((u, i) => (
            <div key={i} className="mono" style={{ fontSize: 11.5, color: 'var(--text-quiet)', padding: '3px 2px', borderBottom: '1px dashed var(--line-soft)' }}>
              {typeof u === 'string' ? u : (u.field ? `${u.field}: ${u.value ?? ''}` : JSON.stringify(u))}
            </div>
          )) : <div className="pl-empty" style={{ padding: '8px 2px', fontSize: 12 }}>本轮暂无状态写入</div>}
        </div>
      </div>
    </div>
  );
}

/* ===================== 右抽屉:世界面板(移动原生面板, panels.jsx) ===================== */
function RightDrawer({ open, onClose, gc }) {
  const [tab, setTab] = useState(gc.activeTab || 'status');
  useEffect(() => { if (gc.activeTab) setTab(gc.activeTab); }, [gc.activeTab]);
  return (
    <div className={`drawer drawer-right ${open ? 'open' : ''}`}>
      <div className="drawer-head">
        <h2>世界面板</h2>
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
  const type = sheet?.type;
  let title = '', sub = '', body = null;

  if (type === 'slash') {
    title = '命令'; sub = '/ 用命令直接改状态、记忆、模式。';
    body = (
      <div style={{ display: 'grid', gap: 14 }}>
        {SLASH_GROUPS.map((g) => (
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
    title = '添加到这一轮'; sub = '引用本地文件、剧本资源,或调用能力。';
    const groups = [
      { title: '本地', items: [{ id: 'file', ic: 'file', t: '文件 / 原文片段' }, { id: 'image', ic: 'image', t: '图片' }] },
      { title: '剧本', items: [{ id: 'chapter', ic: 'book', t: '章节' }, { id: 'card', ic: 'cards', t: '角色卡 @提及' }, { id: 'world', ic: 'world', t: '世界书条目' }] },
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
    title = '对话模型'; sub = '切换 GM 的生成引擎。';
    body = <ModelSheetBody gc={gc} onClose={onClose} />;
  } else if (type === 'permission') {
    title = 'LLM 写入权限'; sub = '决定 GM 能否、以及如何改写世界状态。';
    body = (
      <div className="sheet-list">
        {PERMISSIONS.map((pm) => (
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
    title = '上下文用量';
    sub = u.context_max ? `${Number(u.context_used || 0).toLocaleString()} / ${Number(u.context_max).toLocaleString()} · 已用 ${pct}%` : '本轮尚无用量数据';
    body = (
      <>
        {u.context_max ? <div className="ctx-bar"><i style={{ width: pct + '%', background: 'var(--accent)' }} /></div> : null}
        <div className="ctx-list">
          {u.input_tokens != null && <div className="ctx-row"><span className="ctx-label">输入</span><span className="ctx-tok mono">{Number(u.input_tokens).toLocaleString()}</span></div>}
          {u.cached_input_tokens ? <div className="ctx-row"><span className="ctx-label">命中缓存</span><span className="ctx-tok mono">{Number(u.cached_input_tokens).toLocaleString()}</span></div> : null}
          {u.output_tokens != null && <div className="ctx-row"><span className="ctx-label">输出</span><span className="ctx-tok mono">{Number(u.output_tokens).toLocaleString()}</span></div>}
          {u.reasoning_tokens ? <div className="ctx-row"><span className="ctx-label">思考</span><span className="ctx-tok mono">{Number(u.reasoning_tokens).toLocaleString()}</span></div> : null}
          {u.cost_usd ? <div className="ctx-row"><span className="ctx-label">本轮费用</span><span className="ctx-tok mono">${Number(u.cost_usd).toFixed(4)}</span></div> : null}
          {u.model ? <div className="ctx-row"><span className="ctx-label">模型</span><span className="ctx-tok mono">{u.model}</span></div> : null}
        </div>
        <p className="confirm-note" style={{ paddingTop: 12 }}>详细分项见右侧「上下文」面板。记忆模式在左侧抽屉调整。</p>
      </>
    );
  } else if (type === 'msg') {
    const idx = sheet.data;
    const m = gc.history[idx] || {};
    const isGM = m.role === 'assistant';
    title = isGM ? '这一段 GM 叙事' : '这条玩家行动';
    body = (
      <div className="sheet-list">
        <button className="sheet-item" onClick={() => { onClose(); msgActions.copy(idx); }}>
          <span className="sheet-ico"><Icon name="copy" size={18} /></span>
          <span className="sheet-tx"><strong>复制</strong><span>拷贝这一段文字</span></span>
        </button>
        <button className="sheet-item" onClick={() => { onClose(); msgActions.fork(idx); }}>
          <span className="sheet-ico"><Icon name="fork" size={18} /></span>
          <span className="sheet-tx"><strong>从这里新建分支</strong><span>保留原线,在此另起一条世界线</span></span>
        </button>
        {isGM && (
          <button className="sheet-item" onClick={() => { onClose(); msgActions.regen(idx); }}>
            <span className="sheet-ico"><Icon name="refresh" size={18} /></span>
            <span className="sheet-tx"><strong>重新生成这一轮</strong><span>换个写法重走这段剧情</span></span>
          </button>
        )}
        <div className="sheet-divider" />
        <button className="sheet-item danger" onClick={() => { onClose(); msgActions.rollback(idx); }}>
          <span className="sheet-ico"><Icon name="trash" size={18} /></span>
          <span className="sheet-tx"><strong>回滚到此之前</strong><span>删除这一段及其之后所有,旧分支自动保留</span></span>
        </button>
      </div>
    );
  } else if (type === 'overflow') {
    title = '更多';
    const items = [
      { id: 'history', ic: 'history', t: '历史回顾', fn: gc.onOpenHistory },
      { id: 'search', ic: 'search', t: '搜索本档', fn: gc.onOpenSearch },
      { id: 'settings', ic: 'settings', t: '游戏内设置', fn: gc.onOpenSettings },
      { id: 'sse', ic: 'braces', t: '本轮 SSE 调试', fn: gc.onShowSse },
      { id: 'feedback', ic: 'feedback', t: '提交反馈', fn: gc.onOpenFeedback },
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

function ModelSheetBody({ gc, onClose }) {
  const [models, setModels] = useState(null);
  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const r = await window.api.models.list();
        // /api/models 返回 {ok, models:{apis:[...], selected:{...}}}
        const apis = (r && r.models && Array.isArray(r.models.apis))
          ? r.models.apis
          : (Array.isArray(r?.apis) ? r.apis : []);
        const flat = [];
        for (const api of apis) {
          if (api.enabled === false) continue;
          if (api.has_credential === false) continue;
          const apiId = api.id || api.api_id || '';
          const mods = Array.isArray(api.models) ? api.models : [];
          for (const m of mods) {
            if (m.enabled === false) continue;
            // 跳过 embedding 等非聊天模型
            const caps = Array.isArray(m.capabilities) ? m.capabilities : [];
            if (caps.includes('embedding')) continue;
            flat.push({ ...m, api_id: apiId, label: m.display_name || m.real_name || m.id });
          }
        }
        if (!dead) setModels(flat);
      } catch (_) { if (!dead) setModels([]); }
    })();
    return () => { dead = true; };
  }, []);
  if (models === null) return <div className="pl-empty" style={{ padding: 20 }}>加载模型…</div>;
  if (!models.length) return <div className="pl-empty" style={{ padding: 20 }}>没有可用模型,请先在「设置 · 模型」配置 API Key。</div>;
  const pick = async (m) => {
    try {
      await window.api.models.select({ api_id: m.api_id || m.provider, model_id: m.real_name || m.model_id || m.id });
      gc.setModel && gc.setModel({ id: m.real_name || m.id, api_id: m.api_id || m.provider, label: m.label || m.real_name });
      window.__apiToast?.(`GM 模型 → ${m.label || m.real_name || m.id}`, { kind: 'ok', duration: 1500 });
    } catch (e) { window.__apiToast?.('切换失败', { kind: 'danger', detail: e?.message }); }
    onClose();
  };
  const curId = gc.model && (gc.model.id || gc.model.model_id);
  return (
    <div className="sheet-list">
      {models.map((m, i) => {
        const id = m.real_name || m.id;
        return (
          <button key={i} className={`sheet-item ${id === curId ? 'active' : ''}`} onClick={() => pick(m)}>
            <span className="sheet-ico"><Icon name="sparkle" size={18} /></span>
            <span className="sheet-tx"><span className="vendor">{m.api_id || m.provider || ''}</span><strong>{m.label || id}</strong>{m.desc && <span>{m.desc}</span>}</span>
            {id === curId && <span className="sheet-check"><Icon name="check" size={18} /></span>}
          </button>
        );
      })}
    </div>
  );
}

/* ===================== 主组件 ===================== */
export function MobileGame(gc) {
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

  const atBottomRef = useRef(true);
  const scrollBottom = useCallback((smooth) => {
    const el = chatRef.current; if (!el) return;
    requestAnimationFrame(() => el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' }));
  }, []);
  // 此前手机游戏【完全没有守卫】→ 每次输出无条件拽回底部。补 onScroll 记录是否在底部。
  useEffect(() => {
    const el = chatRef.current; if (!el) return;
    const onScroll = () => { atBottomRef.current = (el.scrollHeight - el.scrollTop - el.clientHeight) < 80; };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);
  useEffect(() => { scrollBottom(false); }, []);  // 挂载滚底
  // ① 自己刚发(末条=玩家)→ 滚底;② 否则双守卫:已上滚 或 实时距底>360 → 不跟随(GM 输出完成不拽回)
  useEffect(() => {
    const el = chatRef.current; if (!el) return;
    const last = history && history[history.length - 1];
    if (last && last.role === 'user') { atBottomRef.current = true; }
    else if (!atBottomRef.current || (el.scrollHeight - el.scrollTop - el.clientHeight) > 360) { return; }
    scrollBottom(true);
  }, [history.length, running]);

  // 手机 GM 询问可折叠(抽屉):默认展开;新询问到达自动展开;用户可手动点头部收起腾出视野。
  // 发消息时 running=true → confirm-zone 自动隐藏(相当于关上一条),新询问回来 qSig 变→自动展开。
  const [qCollapsed, setQCollapsed] = useState(false);
  const qSig = (pendingQuestions || []).map((q) => q && q.id).join(',');
  useEffect(() => { setQCollapsed(false); }, [qSig]);
  useEffect(() => { const ta = taRef.current; if (!ta) return; ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'; }, [text]);

  const closeAll = () => { setLeftOpen(false); setRightOpen(false); };

  // 长按消息 → 操作 sheet
  const startPress = (idx) => { lpTimer.current = setTimeout(() => { setPressed(idx); if (navigator.vibrate) navigator.vibrate(12); setSheet({ type: 'msg', data: idx }); }, 420); };
  const cancelPress = () => clearTimeout(lpTimer.current);

  const msgActions = {
    copy: (idx) => { try { navigator.clipboard?.writeText(gc.history[idx]?.content || ''); } catch (_) {} setPressed(null); window.__apiToast?.('已复制', { kind: 'ok', duration: 1200 }); },
    regen: (idx) => { setPressed(null); gc.onRegenerate && gc.onRegenerate(idx); },
    fork: async (idx) => {
      setPressed(null);
      try {
        const sid = activeSave?.id;
        if (!sid) return;
        await window.api.branches.continueFrom({ save_id: sid, message_index: idx });
        gc.reloadState && gc.reloadState();
        window.__apiToast?.('已从此节点新建分支', { kind: 'ok', icon: 'fork' });
      } catch (e) { window.__apiToast?.('分支失败', { kind: 'danger', detail: e?.message }); }
    },
    rollback: async (idx) => {
      setPressed(null);
      if (!confirm('回滚到这一段之前?这一段及其之后的对话、世界线、阶段摘要都会被丢弃(旧分支自动保留,可切回)。')) return;
      try {
        const sid = activeSave?.id;
        await window.api.branches.rollbackToMessage(sid, idx);
        gc.reloadState && gc.reloadState();
        window.__apiToast?.('已回滚 · 旧分支已留存', { kind: 'ok', icon: 'history' });
      } catch (e) { window.__apiToast?.('回滚失败', { kind: 'danger', detail: e?.message }); }
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
            <Icon name="warn" size={14} /> 已在另一窗口打开此存档
            <button onClick={() => setTabConflictBanner && setTabConflictBanner(null)}>继续</button>
          </div>
        )}

        {/* 顶栏 */}
        <div className="topbar">
          <button className="tb-exit" onClick={gc.onExit} aria-label="返回应用"><Icon name="chevron_left" size={17} /><span>应用</span></button>
          <button className="tb-btn" onClick={() => setLeftOpen(true)} aria-label="存档与分支"><Icon name="menu" size={19} /></button>
          <div className="tb-title">
            <strong>{activeSave?.title || game?._raw?.save_title || '游戏'}</strong>
            <span className="sub"><span className="tb-save-dot" /> {(game.app?.script_title) || (w.phase) || (game.content_pack?.title) || '自由模式'}</span>
          </div>
          <button className="tb-btn" onClick={() => setSheet({ type: 'overflow' })} aria-label="更多"><Icon name="more" size={19} /></button>
          <button className="tb-btn accent" onClick={() => setRightOpen(true)} aria-label="世界面板"><Icon name="compass" size={19} /></button>
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
                  <div className="peek-cell"><span className="k"><Icon name="clock" size={11} /> 时刻</span><span className="v">{w.time || '—'}</span></div>
                  <div className="peek-cell"><span className="k"><Icon name="cloud" size={11} /> 天气</span><span className="v">{w.weather || '—'}</span></div>
                  {objective ? <div className="peek-cell wide"><span className="k"><Icon name="flag" size={11} /> 当前目标</span><span className="v q">{objective}</span></div> : null}
                </div>
                {onStage.length > 0 && (
                  <div>
                    <div className="k" style={{ fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--muted-2)', marginBottom: 7, display: 'flex', alignItems: 'center', gap: 5 }}><Icon name="cards" size={11} /> 在场</div>
                    <div className="peek-stage">
                      {onStage.map((c, i) => { const nm = c.name || c.id || ('实体' + i); return <span key={i} className="who"><span className="peek-av serif">{String(nm).slice(0, 1)}</span>{nm}</span>; })}
                    </div>
                  </div>
                )}
                <button className="peek-full" onClick={() => { setPeekOpen(false); gc.setActiveTab && gc.setActiveTab('status'); setRightOpen(true); }}>查看完整状态面板 <Icon name="arrow_right" size={13} /></button>
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
                <div className="msg-meta"><span className="msg-tag">GM</span>{m._thinking ? <span className="msg-gts mono">思考中…</span> : null}</div>
                <div className="msg-body serif">{paras(m.content)}</div>
              </div>
            ) : (
              <div key={i} className={`msg msg-player ${pressed === i ? 'pressed' : ''}`}
                onTouchStart={() => startPress(i)} onTouchEnd={cancelPress} onTouchMove={cancelPress}
                onContextMenu={(e) => { e.preventDefault(); setPressed(i); setSheet({ type: 'msg', data: i }); }}>
                <div className="msg-meta"><span className="msg-tag muted">玩家</span>{m.ts ? <span className="msg-gts mono">{m.ts}</span> : null}</div>
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
              <div style={{ flex: 1 }}><div className="think-label">{runState.label || '生成中'}</div></div>
              <span className="think-elapsed">{((runState.totalElapsed || 0) / 1000).toFixed(1)}s</span>
            </div>
          )}

          {hasError && !running && (
            <div className="mg-error"><Icon name="warn" size={14} /> {String(hasError)} <button onClick={() => gc.onRetry && gc.onRetry()}>重试</button></div>
          )}
        </div>

        {/* 待确认(找回 pending writes/questions + cliche) */}
        {!running && (pendCount > 0 || clicheNotice) && (
          <div className="confirm-zone">
            <div className="confirmbar">
              {clicheNotice && (
                <div className="cb-q">
                  <div className="cb-q-row"><span className="cb-tag gm">提示</span><span className="cb-q-text">{typeof clicheNotice === 'string' ? clicheNotice : (clicheNotice.message || '检测到套路化表达')}</span></div>
                  <div className="cb-choices">
                    <button className="cb-choice primary" onClick={onRetryCliche}>重写这一轮</button>
                    <button className="cb-choice" onClick={onDismissCliche}>保留</button>
                  </div>
                </div>
              )}
              {pendCount > 0 && (
                <>
                  {/* 折叠头(点击/下拉展开收起,抽屉式)——腾出视野;新询问到达自动展开 */}
                  <button className="cb-head cb-toggle" onClick={() => setQCollapsed((c) => !c)} aria-expanded={!qCollapsed}>
                    <span className="cb-head-l"><Icon name="warn" size={13} /> 待确认 · {pendCount}</span>
                    <span className={'cb-chev' + (qCollapsed ? '' : ' open')}><Icon name="chevron_down" size={15} /></span>
                  </button>
                  <div className={'cb-body' + (qCollapsed ? ' collapsed' : '')}>
                    {(pendingQuestions || []).map((q) => {
                      // 兼容新旧数据形态:question/text 取一,options/choices 取一
                      const qText = q.question || q.text;
                      const qOpts = q.options || q.choices;
                      return (
                        <div key={q.id} className="cb-q">
                          <div className="cb-q-row"><span className="cb-tag gm">GM 询问</span><span className="cb-q-text">{qText}</span></div>
                          <div className="cb-choices">
                            {(qOpts || []).map((c, i) => {
                              const label = typeof c === 'string' ? c : (c.text || c.label || c.id || JSON.stringify(c));
                              return (
                                <button key={i} className={'cb-choice' + (i === 0 ? ' primary' : '')} onClick={() => onAnswerQuestion(q, c)}>{label}</button>
                              );
                            })}
                            <button className="cb-choice" onClick={() => onDismissConfirm(q)}>忽略</button>
                          </div>
                        </div>
                      );
                    })}
                    {(pendingWrites || []).map((wr) => (
                      <div key={wr.id} className={'cb-w ' + (wr.risk || 'low')}>
                        <div className="cb-w-row"><span className={'cb-tag risk ' + (wr.risk || 'low')}><Icon name="warn" size={11} /> 写入</span><span className="cb-field mono">{wr.field || wr.path || ''}</span></div>
                        {wr.to != null && <div className="cb-to">→ {typeof wr.to === 'object' ? JSON.stringify(wr.to) : String(wr.to)}</div>}
                        {wr.reason && <div className="cb-w-reason">{wr.reason}</div>}
                        <div className="cb-actions">
                          <button className="cb-allow" onClick={() => onApprove(wr)}>允许</button>
                          <button className="cb-deny" onClick={() => onReject(wr)}>拒绝</button>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* composer */}
        <div className="composer-zone">
          {!running && suggestions.length > 0 && (
            <div className="suggestions scroll">
              {suggestions.map((s, i) => <button key={i} className="sugg" onClick={() => { setText(s); setTimeout(() => taRef.current?.focus(), 50); }}>{s}</button>)}
            </div>
          )}
          <div className="composer">
            <div className="composer-input-row">
              <button className="c-plus" onClick={() => setSheet({ type: 'attach' })} aria-label="附件"><Icon name="plus" size={20} /></button>
              <textarea ref={taRef} className="c-text" rows={1} placeholder="此刻你做什么…  / 命令  + 附件"
                value={text} onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent?.isComposing) { e.preventDefault(); submit(); } }} />
              <button className={`c-send ${(String(text || '').trim() && !running) || running ? '' : 'idle'}`} onClick={() => (running ? onStop() : submit())}>
                <Icon name={running ? 'stop' : 'send'} size={18} />
              </button>
            </div>
            <div className="composer-foot">
              <button className="c-chip" onClick={() => setSheet({ type: 'slash' })} aria-label="命令"><Icon name="slash" size={14} /></button>
              <button className="c-chip" onClick={() => setSheet({ type: 'model' })}><Icon name="sparkle" size={13} /><span className="lbl">{(gc.model && (gc.model.label || gc.model.id)) || '模型'}</span><Icon name="chevron_down" size={12} /></button>
              <button className={`c-chip perm ${permission}`} onClick={() => setSheet({ type: 'permission' })}><Icon name={(PERMISSIONS.find((x) => x.id === permission) || PERMISSIONS[2]).icon} size={13} /></button>
              <span className="c-spacer" />
              <button className="c-ctx" onClick={() => setSheet({ type: 'context' })} aria-label="上下文用量">
                <svg width="19" height="19" viewBox="0 0 19 19" style={{ transform: 'rotate(-90deg)' }}>
                  <circle cx="9.5" cy="9.5" r="7" fill="none" stroke="var(--line-strong)" strokeWidth="2.4" />
                  <circle cx="9.5" cy="9.5" r="7" fill="none" stroke="var(--accent)" strokeWidth="2.4" strokeLinecap="round" strokeDasharray={`${((gc.lastUsage?.context_pct || 0) / 100) * 2 * Math.PI * 7} 999`} />
                </svg>
                <span className="c-ctx-pct mono">{Math.round(gc.lastUsage?.context_pct || 0)}%</span>
              </button>
            </div>
          </div>
        </div>

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
