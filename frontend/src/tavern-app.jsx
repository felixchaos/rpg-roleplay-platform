/* Tavern Mode — SillyTavern 风格 1:1 角色对话(独立页,镜像 Game Console)。
 *
 * 设计:像 Claude 网页版 —— 左侧对话历史 rail + 居中单栏对话 + composer。
 * 复用件(不重写):
 *   - Composer / NarrativeBlock / PlayerBlock (game-composer.jsx / game-app.jsx)
 *   - RpgMarkdown.Block(由 NarrativeBlock 内部使用)
 *   - TavernImportModal / CardSheet / CardEditFields / cardFormInit/Payload(pages/cards.jsx)
 *   - useResizable(responsive.jsx)、Icon(game-icons.jsx)
 *   - SSE:api.game.chat({message, save_id}) + api.game.stop()
 *   - 历史加载:对话即 game_saves(save_kind='tavern'),激活后用 api.game.state() 读 history
 * 关键约束:
 *   - 切换对话前必须先 api.tavern.activate(id),/api/chat 才落到正确 save。
 *   - 新对话的 first_mes 已由后端 seed 为首条 assistant 消息 → 不调任何 opening 端点。
 */
import React from 'react';
import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';

import { Icon } from './game-icons.jsx';
import { useResizable } from './responsive.jsx';
import { NarrativeBlock, PlayerBlock, GameToastStack } from './game-app.jsx';
import { Composer } from './game-composer.jsx';
import { TavernImportModal, CardSheet, CardEditFields, cardFormInit, cardFormPayload } from './pages/cards.jsx';

/* ── 相对时间 ─────────────────────────────────────────────────────── */
export function relTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 60) return '刚刚';
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  if (sec < 86400 * 7) return `${Math.floor(sec / 86400)} 天前`;
  return d.toLocaleDateString();
}

/* ── 确认弹窗(仿 game-app.jsx 的 pl-modal)─────────────────────────── */
export function ConfirmModal({ open, title, body, confirmLabel = '确认', danger, onClose, onConfirm }) {
  if (!open) return null;
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{ width: 'min(420px, 100%)' }}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">{danger ? '危险操作' : '请确认'}</div>
            <h2 className="pl-modal-title">{title}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div style={{ fontSize: 13.5, lineHeight: 1.7, color: 'var(--text-quiet)' }}>{body}</div>
        <footer className="pl-modal-foot">
          <span />
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            <button className={`btn ${danger ? 'danger' : 'primary'}`} onClick={onConfirm}>{confirmLabel}</button>
          </div>
        </footer>
      </div>
    </div>
  );
  return createPortal(node, document.body);
}

/* ── 单条对话行(标题 + last_snippet + 相对时间 + hover ⋯ 菜单)──────── */
export function TavernChatItem({ chat, active, onOpen, onRename, onArchive, onDelete, archived }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef(null);
  const initial = (chat.character_name || chat.title || '?').trim().slice(0, 1);
  const curTitle = chat.title || chat.character_name || `对话 #${chat.id}`;

  // 类 Claude:双击标题 / 菜单「重命名」→ 原地变输入框,Enter 或失焦保存,Esc 取消。
  const startEdit = () => { setMenuOpen(false); setDraft(chat.title || chat.character_name || ''); setEditing(true); };
  const commit = () => {
    setEditing(false);
    const t = (draft || '').trim();
    if (t && t !== curTitle) onRename(chat, t);
  };
  useEffect(() => {
    if (editing && inputRef.current) { inputRef.current.focus(); inputRef.current.select(); }
  }, [editing]);

  return (
    <div
      className={`tv-chat-item ${active ? 'active' : ''}`}
      onClick={() => { if (!editing) onOpen(chat); }}
      role="button"
      tabIndex={0}
    >
      <div className="tv-chat-avatar serif">{initial}</div>
      <div className="tv-chat-main">
        <div className="tv-chat-title-row">
          {editing ? (
            <input
              ref={inputRef}
              className="tv-chat-title-edit"
              value={draft}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); commit(); }
                else if (e.key === 'Escape') { e.preventDefault(); setEditing(false); }
              }}
              onBlur={commit}
              maxLength={200}
            />
          ) : (
            <span
              className="tv-chat-title"
              title="双击重命名"
              onDoubleClick={(e) => { e.stopPropagation(); startEdit(); }}
            >{curTitle}</span>
          )}
          <span className="tv-chat-time muted-2">{relTime(chat.updated_at)}</span>
        </div>
        {chat.last_snippet
          ? <div className="tv-chat-snippet muted-2">{chat.last_snippet}</div>
          : <div className="tv-chat-snippet muted-2" style={{ fontStyle: 'italic' }}>{chat.character_name || '酒馆角色'}</div>}
      </div>
      <div className="tv-chat-menu-wrap" onClick={(e) => e.stopPropagation()}>
        <button className="iconbtn tv-chat-menu-btn" onClick={() => setMenuOpen((v) => !v)} data-tip="更多">
          <Icon name="more" size={14} />
        </button>
        {menuOpen && (
          <>
            <div className="tv-menu-scrim" onClick={() => setMenuOpen(false)} />
            <div className="tv-menu">
              <button onClick={startEdit}>
                <Icon name="edit" size={13} /> 重命名
              </button>
              <button onClick={() => { setMenuOpen(false); onArchive(chat, !archived); }}>
                <Icon name="folder" size={13} /> {archived ? '取消归档' : '归档'}
              </button>
              <button className="tv-menu-danger" onClick={() => { setMenuOpen(false); onDelete(chat); }}>
                <Icon name="trash" size={13} /> 删除
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ── 左侧对话历史 rail ────────────────────────────────────────────── */
function TavernSidebar({
  chats, archivedChats, activeId, loading, collapsed, railW, dragHandleProps,
  onNewChat, onOpenChat, onRename, onArchive, onDelete, onDropCard, mobileOpen,
}) {
  const [showArchived, setShowArchived] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    const f = e.dataTransfer?.files?.[0];
    if (f) onDropCard(f);
  };

  return (
    <aside
      className={`gc-rail tv-rail ${collapsed ? 'collapsed' : ''} ${mobileOpen ? 'gc-rail-mobile-open' : ''}`}
      style={{ width: railW }}
    >
      <div className="gc-rail-inner tv-rail-inner">
        <div className="tv-rail-head">
          <div className="tv-rail-brand">
            <Icon name="message_square" size={16} style={{ color: 'var(--accent)' }} />
            <strong>酒馆</strong>
          </div>
          <button className="btn primary tv-new-btn" onClick={onNewChat}>
            <Icon name="plus" size={13} /> 新对话
          </button>
        </div>

        <div
          className={`tv-rail-list ${dragOver ? 'drop-active' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          {loading && <div className="tv-rail-empty muted-2">加载中…</div>}
          {!loading && chats.length === 0 && (
            <div className="tv-rail-empty muted-2">
              <Icon name="upload" size={20} style={{ opacity: 0.5, marginBottom: 6 }} />
              <div>还没有对话</div>
              <div style={{ fontSize: 11.5, marginTop: 4 }}>点「新对话」或拖入一张酒馆角色卡</div>
            </div>
          )}
          {chats.map((c) => (
            <TavernChatItem
              key={c.id} chat={c} active={String(c.id) === String(activeId)}
              onOpen={onOpenChat} onRename={onRename} onArchive={onArchive} onDelete={onDelete}
              archived={false}
            />
          ))}

          {archivedChats.length > 0 && (
            <div className="tv-archived-section">
              <button className="tv-archived-toggle" onClick={() => setShowArchived((v) => !v)}>
                <Icon name={showArchived ? 'chevron_down' : 'chevron_right'} size={12} />
                已归档 ({archivedChats.length})
              </button>
              {showArchived && archivedChats.map((c) => (
                <TavernChatItem
                  key={c.id} chat={c} active={String(c.id) === String(activeId)}
                  onOpen={onOpenChat} onRename={onRename} onArchive={onArchive} onDelete={onDelete}
                  archived={true}
                />
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="gc-rail-resize-handle" title="拖动调整宽度" {...dragHandleProps} />
    </aside>
  );
}

/* ── 顶栏:persona ⇄ character 两枚 chip + 操作 ─────────────────────── */
function TavernHeader({ chat, character, persona, onOpenDrawer, onExport, onOpenNav, railCollapsed, onExpandRail }) {
  const charName = (character && character.name) || (chat && chat.character_name) || '';
  return (
    <header className="tv-header">
      <button className="iconbtn tv-mobile-nav" onClick={onOpenNav} data-tip="对话列表">
        <Icon name="menu" size={16} />
      </button>
      {railCollapsed && (
        <button className="iconbtn" onClick={onExpandRail} data-tip="展开列表">
          <Icon name="chevron_right" size={16} />
        </button>
      )}
      {charName ? (
        <button className="tv-title" onClick={onOpenDrawer} data-tip="查看 / 编辑角色 · persona">
          <span className="tv-title-name">{charName}</span>
          <Icon name="chevron_down" size={13} style={{ opacity: 0.45 }} />
        </button>
      ) : (
        <span className="tv-title tv-title-empty">酒馆</span>
      )}
      <div className="tv-header-actions">
        {chat && onExport && (
          <a className="iconbtn" href={onExport} target="_blank" rel="noopener" data-tip="导出 JSONL">
            <Icon name="download" size={15} />
          </a>
        )}
        {charName && (
          <button className="iconbtn" onClick={onOpenDrawer} data-tip="角色卡 / persona">
            <Icon name="cards" size={15} />
          </button>
        )}
      </div>
    </header>
  );
}

/* ── 转录区(居中单栏)──── m.role assistant/user 等价 SillyTavern is_user ── */
/* ── F1:后台工具流(可折叠、默认折叠、沉浸优先)──────────────────────────
 * 把一轮内连续的工具调用归组,默认折叠成一行摘要(如「⚙ 调用 2 个工具 · set_tavern_character…」)。
 * 展开后逐个列出工具名 + args + result。与角色扮演正文(NarrativeBlock)视觉分离,
 * 静音/后台风,不抢沉浸主体。ops 形如 [{tool, args, result, ok}]。
 */
function _fmtToolValue(v) {
  if (v == null) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
}

export function ToolCallBlock({ ops }) {
  const [open, setOpen] = useState(false);
  if (!Array.isArray(ops) || ops.length === 0) return null;
  const n = ops.length;
  const firstName = (ops[0] && ops[0].tool) || '工具';
  const summary = n === 1
    ? `调用工具 · ${firstName}`
    : `调用 ${n} 个工具 · ${firstName}…`;
  return (
    <div className={`tvp-tools${open ? ' open' : ''}`}>
      <button
        type="button"
        className="tvp-tools-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="tvp-tools-gear" aria-hidden="true">⚙</span>
        <Icon name={open ? 'chevron_down' : 'chevron_right'} size={11} />
        <span className="tvp-tools-summary">{summary}</span>
      </button>
      {open && (
        <div className="tvp-tools-detail">
          {ops.map((op, i) => (
            <div className="tvp-tool-item" key={i}>
              <div className="tvp-tool-name">
                <span className={`tvp-tool-dot${op && op.ok === false ? ' err' : ''}`} aria-hidden="true" />
                {(op && op.tool) || '工具'}
              </div>
              {op && op.args != null && (
                <pre className="tvp-tool-kv"><span className="tvp-tool-kv-k">args</span>{_fmtToolValue(op.args)}</pre>
              )}
              {op && (op.result != null || op.error != null) && (
                <pre className={`tvp-tool-kv${op.ok === false ? ' err' : ''}`}>
                  <span className="tvp-tool-kv-k">{op.ok === false ? 'error' : 'result'}</span>
                  {_fmtToolValue(op.ok === false ? (op.error != null ? op.error : op.result) : op.result)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function TavernChatArea({ history, running, saveId, charName, charInitial, personaName, hasError, errorMsg, onRetry, lastMeta, elapsedLabel }) {
  const ref = useRef(null);
  const atBottomRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      atBottomRef.current = atBottom;
      setShowJump(!atBottom);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (!ref.current || !atBottomRef.current) return;
    const id = requestAnimationFrame(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; });
    return () => cancelAnimationFrame(id);
  }, [history.length, running]);

  const total = history.length;
  const isWaiting = running && (total === 0 || history[total - 1]?.role === 'user');

  return (
    <div ref={ref} className="gc-chat tv-chat">
      <div className="gc-chat-inner">
        {total === 0 && !running && (
          <div className="tv-chat-empty muted-2">
            <Icon name="message_square" size={28} style={{ opacity: 0.4, marginBottom: 8 }} />
            <div>对话尚未开始。</div>
          </div>
        )}
        {history.map((m, i) => {
          const commitId = m && (m.commit_id || m.node_id);
          if (m.role === 'assistant') {
            const toolOps = m && m._toolOps;
            return (
              <React.Fragment key={`a-${i}`}>
                {Array.isArray(toolOps) && toolOps.length > 0 && <ToolCallBlock ops={toolOps} />}
                <NarrativeBlock
                  text={m.content} ts={m.ts}
                  msgIndex={i} saveId={saveId} commitId={commitId}
                  thinking={m._thinking}
                  hideMeta
                  streaming={!m.streaming_done && i === total - 1 && running}
                  meta={i === total - 1 ? lastMeta : null}
                />
              </React.Fragment>
            );
          }
          return (
            <PlayerBlock
              key={`u-${i}`} text={m.content} ts={m.ts} attachments={m.attachments}
              msgIndex={i} saveId={saveId} commitId={commitId} hideMeta
            />
          );
        })}
        {isWaiting && (
          <div className="gc-waiting-gm" aria-live="polite">
            <span className="gc-waiting-gm-dot" />
            <span className="gc-waiting-gm-dot" style={{ animationDelay: '0.2s' }} />
            <span className="gc-waiting-gm-dot" style={{ animationDelay: '0.4s' }} />
            <span className="gc-waiting-gm-label">{charName ? `${charName} ` : ''}正在思考…</span>
            {elapsedLabel ? <span className="gc-waiting-gm-elapsed mono muted-2">{elapsedLabel}</span> : null}
          </div>
        )}
        {hasError && (
          <div className="gc-error">
            <Icon name="warn" size={14} style={{ color: 'var(--danger)' }} />
            <div>
              <strong>生成失败</strong>
              <p className="muted" style={{ margin: '4px 0 0', fontSize: 12.5 }}>
                {(typeof hasError === 'string' && hasError) || errorMsg || '请求中断,已保留你的上一条输入,可重试。'}
              </p>
              <div className="gc-error-actions">
                <button className="btn" onClick={onRetry} disabled={!onRetry}>重试</button>
              </div>
            </div>
          </div>
        )}
      </div>
      {showJump && (
        <button
          onClick={() => { if (ref.current) { ref.current.scrollTo({ top: ref.current.scrollHeight, behavior: 'smooth' }); atBottomRef.current = true; setShowJump(false); } }}
          className="btn"
          style={{ position: 'absolute', right: 20, bottom: 90, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 999, padding: '6px 14px', fontSize: 12.5, zIndex: 5, cursor: 'pointer' }}
          data-tip="跳到最新"
        >
          <Icon name="chevron_down" size={12} /> 回到最新
        </button>
      )}
    </div>
  );
}

/* ── 两张卡抽屉:persona(可编辑)+ AI 角色卡(只读)──────────────────── */
export function TwoCardDrawer({ open, character, persona, onClose, onSavePersona }) {
  const [form, setForm] = useState(() => cardFormInit(persona));
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState('character'); // 'character' | 'persona'

  useEffect(() => { setForm(cardFormInit(persona)); setEditing(false); }, [persona, open]);
  const u = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  if (!open) return null;
  const charName = (character && character.name) || '角色';
  const personaName = (persona && persona.name) || '你的 persona';

  const doSave = async () => {
    setSaving(true);
    try {
      await onSavePersona(cardFormPayload(form, persona));
      setEditing(false);
    } finally { setSaving(false); }
  };

  const node = (
    <div className="tv-drawer-backdrop" onClick={onClose}>
      <div className="tv-drawer" onClick={(e) => e.stopPropagation()}>
        <header className="tv-drawer-head">
          <div className="seg" style={{ display: 'flex' }}>
            <button className={tab === 'character' ? 'active' : ''} onClick={() => setTab('character')}>
              <Icon name="cards" size={12} /> AI 角色
            </button>
            <button className={tab === 'persona' ? 'active' : ''} onClick={() => setTab('persona')}>
              <Icon name="user" size={12} /> 我的 persona
            </button>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={15} /></button>
        </header>
        <div className="tv-drawer-body">
          {tab === 'character' && (
            character
              ? <CardSheet card={character} kind="user" />
              : <div className="muted-2" style={{ padding: 24, textAlign: 'center' }}>未找到该对话的角色卡。</div>
          )}
          {tab === 'persona' && (
            <>
              {!editing ? (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                    <strong style={{ fontSize: 14 }}>{personaName}</strong>
                    {persona && (
                      <button className="btn ghost" onClick={() => setEditing(true)}>
                        <Icon name="edit" size={12} /> 编辑
                      </button>
                    )}
                  </div>
                  {persona
                    ? <CardSheet card={persona} kind="persona" />
                    : <div className="muted-2" style={{ padding: 24, textAlign: 'center' }}>本对话未设置 persona 卡。</div>}
                </>
              ) : (
                <>
                  <CardEditFields form={form} u={u} kind="persona" />
                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                    <button className="btn ghost" onClick={() => setEditing(false)} disabled={saving}>取消</button>
                    <button className="btn primary" onClick={doSave} disabled={saving}>
                      <Icon name="check" size={12} /> {saving ? '保存中…' : '保存'}
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
  return createPortal(node, document.body);
}

/* ══════════════════════════════════════════════════════════════════
 *  TavernApp — 顶层
 * ══════════════════════════════════════════════════════════════════ */
export default function TavernApp() {
  const [chats, setChats] = useState([]);
  const [archivedChats, setArchivedChats] = useState([]);
  const [loadingList, setLoadingList] = useState(true);

  const [activeId, setActiveId] = useState(null);
  const [activeChat, setActiveChat] = useState(null);   // {id,title,character_name,...}
  const [character, setCharacter] = useState(null);     // AI 角色卡 DTO(来自 state.data.tavern.character)
  const [persona, setPersona] = useState(null);         // persona 卡 DTO(来自 state.data.player)
  const [history, setHistory] = useState([]);

  const [text, setText] = useState('');
  const [model, setModel] = useState(null);
  const [running, setRunning] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [lastPlayerText, setLastPlayerText] = useState('');

  const [importOpen, setImportOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [railCollapsed, setRailCollapsed] = useState(false);

  const runRef = useRef({ stopped: false, sse: null, runId: 0, inactivityTimer: null });

  const _railResize = useResizable({ storageKey: 'tavern.rail.w', defaultSize: 280, min: 220, max: 420, side: 'left' });

  /* ── 列表加载 ──────────────────────────────────────────────────── */
  const reloadList = useCallback(async () => {
    setLoadingList(true);
    try {
      const [a, b] = await Promise.all([
        window.api.tavern.list().catch(() => ({ chats: [] })),
        window.api.tavern.listArchived().catch(() => ({ chats: [] })),
      ]);
      setChats(Array.isArray(a?.chats) ? a.chats : []);
      setArchivedChats(Array.isArray(b?.chats) ? b.chats : []);
    } catch (_) {
      setChats([]); setArchivedChats([]);
    } finally { setLoadingList(false); }
  }, []);

  /* ── 把一份 state 投射进角色/persona/history ──────────────────────── */
  const applyState = useCallback((data) => {
    if (!data) return;
    const tavern = data.tavern || (data.data && data.data.tavern) || {};
    const char = tavern.character || null;
    setCharacter(char || null);
    // data.player 是 persona 投影(无 id),编辑保存需要真正的卡 id → 用 persona_card_id 拉全卡。
    const personaCardId = tavern.persona_card_id;
    if (personaCardId != null) {
      window.api.cards.myGet(personaCardId)
        .then((full) => { if (full && full.id) setPersona(full); else setPersona(data.player || null); })
        .catch(() => setPersona(data.player || null));
    } else {
      setPersona(data.player || null);
    }
    if (Array.isArray(data.history)) setHistory(data.history);
    if (data.save_id != null) {
      setActiveChat((prev) => ({
        id: data.save_id,
        title: data.save_title || prev?.title || `对话 #${data.save_id}`,
        character_name: (char && char.name) || prev?.character_name || '',
        updated_at: data.save_updated_at || prev?.updated_at || '',
      }));
    }
  }, []);

  /* ── 打开一个对话:激活 → 读 state(含 first_mes seed 的 history)────── */
  const openChat = useCallback(async (chat) => {
    if (!chat || !chat.id) return;
    setMobileNav(false);
    // 切对话先停掉任何在途流
    if (runRef.current.sse) { try { runRef.current.sse.stop('switch'); } catch (_) {} runRef.current.sse = null; }
    setRunning(false); setHasError(false); setHistory([]);
    setActiveId(chat.id);
    setActiveChat(chat);
    try {
      await window.api.tavern.activate(chat.id);
      const data = await window.api.game.state();
      applyState(data);
    } catch (e) {
      window.__apiToast?.('打开对话失败', { kind: 'danger', detail: e?.message });
    }
  }, [applyState]);

  useEffect(() => { reloadList(); }, [reloadList]);

  // 首次进入:自动打开最近的活跃对话(如果有)
  useEffect(() => {
    if (activeId != null) return;
    if (loadingList) return;
    if (chats.length > 0) { openChat(chats[0]); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingList, chats]);

  // 卸载:abort 在途流
  useEffect(() => () => {
    const rc = runRef.current;
    rc.stopped = true;
    if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
    if (rc.sse) { try { rc.sse.stop('unmount'); } catch (_) {} rc.sse = null; }
  }, []);

  /* ── 新对话:导入卡 / 现有卡 → 拿 save_id 后打开 ───────────────────── */
  const openSaveId = useCallback(async (saveId, fallbackName) => {
    await reloadList();
    await openChat({ id: saveId, title: fallbackName || `对话 #${saveId}`, character_name: fallbackName || '' });
  }, [reloadList, openChat]);

  const onImportConfirm = useCallback(async (payload) => {
    setImportOpen(false);
    try {
      if (payload.type === 'card') {
        const r = await window.api.tavern.importCharacter(payload.file);
        if (r && r.ok === false) throw new Error(r.error || '导入失败');
        await openSaveId(r.save_id, r.character_name);
        window.__apiToast?.(`已导入「${r.character_name || '角色'}」`, { kind: 'ok', duration: 2000 });
      } else if (payload.type === 'card_json') {
        const r = await window.api.tavern.importCharacter({ json_string: payload.json_string });
        if (r && r.ok === false) throw new Error(r.error || '导入失败');
        await openSaveId(r.save_id, r.character_name);
        window.__apiToast?.(`已导入「${r.character_name || '角色'}」`, { kind: 'ok', duration: 2000 });
      } else if (payload.type === 'chat') {
        const r = await window.api.tavern.importJsonl(payload.jsonl, payload.charName);
        if (r && r.ok === false) throw new Error(r.error || '导入失败');
        await openSaveId(r.save_id, payload.charName);
        window.__apiToast?.(`已导入聊天记录(${r.commits_imported || 0} 条)`, { kind: 'ok', duration: 2200 });
      }
    } catch (e) {
      window.__apiToast?.('导入失败', { kind: 'danger', detail: e?.message });
    }
  }, [openSaveId]);

  // 拖卡进 sidebar / 空状态 → 直接走角色卡导入
  const onDropCard = useCallback(async (file) => {
    if (!file) return;
    if (!/\.(png|json|webp)$/i.test(file.name || '')) {
      window.__apiToast?.('仅支持 .png / .json / .webp 角色卡', { kind: 'warn', duration: 2400 });
      return;
    }
    try {
      const r = await window.api.tavern.importCharacter(file);
      if (r && r.ok === false) throw new Error(r.error || '导入失败');
      await openSaveId(r.save_id, r.character_name);
      window.__apiToast?.(`已导入「${r.character_name || '角色'}」`, { kind: 'ok', duration: 2000 });
    } catch (e) {
      window.__apiToast?.('导入失败', { kind: 'danger', detail: e?.message });
    }
  }, [openSaveId]);

  /* ── 流式发送(复用 api.game.chat,带 idle timeout)──────────────── */
  const stopRun = useCallback(() => {
    runRef.current.stopped = true;
    if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
    runRef.current.runId = (runRef.current.runId || 0) + 1;
    if (runRef.current.sse) { try { runRef.current.sse.stop('manual_stop'); } catch (_) {} runRef.current.sse = null; }
    try { window.api.game.stop(); } catch (_) {}
    setRunning(false);
  }, []);

  const startRun = useCallback(async (playerText) => {
    const saveId = activeId;
    if (saveId == null) { window.__apiToast?.('请先选择或新建一个对话', { kind: 'warn', duration: 2400 }); return; }
    // abort 残留流
    const rc = runRef.current;
    if (rc.sse) { rc.runId = (rc.runId || 0) + 1; try { rc.sse.stop('superseded'); } catch (_) {} rc.sse = null; try { window.api.game.stop(); } catch (_) {} }
    if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
    const runId = (rc.runId || 0) + 1;
    rc.runId = runId; rc.stopped = false;
    const isCurrentRun = () => rc.runId === runId;

    const ts = new Date().toLocaleTimeString().slice(0, 5);
    setHistory((h) => [...h, { role: 'user', content: playerText, ts }]);
    setLastPlayerText(playerText);
    setText('');
    setHasError(false);
    setRunning(true);

    let openedAssistant = false;
    let gotDone = false;
    const STREAM_IDLE_TIMEOUT_MS = 120000;
    const restoreFailedDraft = () => {
      if (!isCurrentRun() || openedAssistant) return;
      setText((cur) => (String(cur || '').trim() ? cur : playerText));
      setHistory((h) => {
        const last = h[h.length - 1];
        if (last && last.role === 'user' && last.content === playerText) return h.slice(0, -1);
        return h;
      });
    };
    const resetIdle = () => {
      if (rc.inactivityTimer) clearTimeout(rc.inactivityTimer);
      rc.inactivityTimer = setTimeout(() => {
        if (!isCurrentRun()) return;
        try { rc.sse && rc.sse.stop && rc.sse.stop('idle_timeout'); } catch (_) {}
        restoreFailedDraft();
        setRunning(false);
        setHasError('超过 120 秒没有新输出,已断开。请重试。');
        window.__apiToast?.('生成停滞', { kind: 'warn', detail: '120 秒无响应,已中断', duration: 4000 });
      }, STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();

    rc.sse = window.api.game.chat(
      { message: playerText, text: playerText, model, save_id: saveId },
      {
        onError: (err) => {
          if (!isCurrentRun()) return;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          const detail = (err && err.payload && err.payload.message) || (err && err.message) || '请求失败';
          setRunning(false); setHasError(detail);
          window.__apiToast?.('请求失败', { kind: 'danger', detail });
          restoreFailedDraft();
        },
        onAbort: (data) => {
          if (!isCurrentRun()) return;
          const reason = (data && data.reason) || '';
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          if (rc.stopped || ['manual_stop', 'superseded', 'unmount', 'switch', 'idle_timeout'].includes(reason)) {
            rc.sse = null; return;
          }
          restoreFailedDraft();
          setRunning(false); setHasError('连接被取消,上一条输入已保留,请重试。');
          rc.sse = null;
        },
        on_status: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          if (data && data.history && Array.isArray(data.history) && !openedAssistant) {
            // 后端可能在 status 里回带最新 history(含 persona/world 更新),不强制覆盖流式气泡
          }
        },
        on_reasoning: () => { if (isCurrentRun()) resetIdle(); },
        on_token: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          const piece = (data && (data.text || data.delta)) || '';
          if (!piece) return;
          setHistory((h) => {
            if (!openedAssistant) { openedAssistant = true; return [...h, { role: 'assistant', content: piece, ts, streaming: true }]; }
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant') return [...h, { role: 'assistant', content: piece, ts, streaming: true }];
            return [...h.slice(0, -1), { ...last, content: (last.content || '') + piece }];
          });
        },
        on_done: (data) => {
          if (!isCurrentRun()) return;
          gotDone = true;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          setRunning(false);
          if (!openedAssistant) {
            restoreFailedDraft();
            const msg = data && data.interrupted ? '本轮已中断,已恢复你的输入。' : '本轮没有收到回复,已恢复你的输入。请重试。';
            setHasError(msg);
            window.__apiToast?.(data && data.interrupted ? '生成中断' : '空回复', { kind: 'warn', detail: msg, duration: 4500 });
            rc.sse = null;
            return;
          }
          setHistory((h) => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant') return h;
            return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
          });
          const payload = (data && data.status) || null;
          if (payload) applyState(payload);
          else { window.api.game.state().then(applyState).catch(() => {}); }
          // 刷新列表(更新 last_snippet / updated_at 排序)
          reloadList();
          rc.sse = null;
        },
        on_error: (data) => {
          if (!isCurrentRun()) return;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          const realMsg = (data && (data.message || data.detail || data.error)) || '';
          setRunning(false); setHasError(realMsg || true);
          window.__apiToast?.('生成失败', { kind: 'danger', detail: realMsg || '请重试' });
          restoreFailedDraft();
        },
        onClose: () => {
          if (!isCurrentRun()) return;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          if (gotDone || rc.stopped) { rc.sse = null; return; }
          setRunning((r) => {
            if (!r) return r;
            setHasError('连接中断:流式连接关闭但没有收到完成事件。上一条输入已保留,可重试。');
            restoreFailedDraft();
            return false;
          });
          setHistory((h) => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant' || !last.streaming) return h;
            return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
          });
        },
      }
    );
  }, [activeId, model, applyState, reloadList]);

  const onSend = () => {
    if (!text.trim() || running) return;
    startRun(text.trim());
  };
  const onSendRaw = useCallback((raw) => {
    const t2 = (raw || '').trim();
    if (!t2 || running) return;
    startRun(t2);
  }, [running, startRun]);
  const onRetry = useCallback(() => {
    if (running) return;
    let t2 = (lastPlayerText && lastPlayerText.trim()) || '';
    if (!t2) {
      for (let i = history.length - 1; i >= 0; i--) {
        if (history[i]?.role === 'user' && (history[i].content || '').trim()) { t2 = history[i].content.trim(); break; }
      }
    }
    if (!t2) { window.__apiToast?.('没有可重试的输入', { kind: 'warn', duration: 2000 }); return; }
    setHasError(false);
    setHistory((h) => {
      const out = [...h];
      while (out.length && out[out.length - 1].role === 'assistant' && !(out[out.length - 1].content || '').trim()) out.pop();
      if (out.length && out[out.length - 1].role === 'user' && (out[out.length - 1].content || '').trim() === t2) out.pop();
      return out;
    });
    startRun(t2);
  }, [running, lastPlayerText, history, startRun]);

  /* ── rail 操作:rename / archive / delete ───────────────────────── */
  const doRename = useCallback(async (chat, title) => {
    try {
      await window.api.tavern.rename(chat.id, title);
      window.__apiToast?.('已重命名', { kind: 'ok', duration: 1500 });
      reloadList();
      if (String(chat.id) === String(activeId)) setActiveChat((p) => ({ ...(p || {}), title }));
    } catch (e) { window.__apiToast?.('重命名失败', { kind: 'danger', detail: e?.message }); }
  }, [reloadList, activeId]);

  const doArchive = useCallback(async (chat, archived) => {
    try {
      await window.api.tavern.archive(chat.id, archived);
      window.__apiToast?.(archived ? '已归档' : '已取消归档', { kind: 'ok', duration: 1500 });
      reloadList();
    } catch (e) { window.__apiToast?.('归档失败', { kind: 'danger', detail: e?.message }); }
  }, [reloadList]);

  const doDelete = useCallback(async (chat) => {
    setDeleteTarget(null);
    try {
      await window.api.tavern.remove(chat.id);
      window.__apiToast?.('已删除', { kind: 'ok', duration: 1500 });
      if (String(chat.id) === String(activeId)) {
        setActiveId(null); setActiveChat(null); setHistory([]); setCharacter(null); setPersona(null);
      }
      reloadList();
    } catch (e) { window.__apiToast?.('删除失败', { kind: 'danger', detail: e?.message }); }
  }, [reloadList, activeId]);

  const onSavePersona = useCallback(async (payload) => {
    try {
      const saved = await window.api.cards.myUpsert(payload);
      window.__apiToast?.('persona 已保存', { kind: 'ok', duration: 1500 });
      // 重新读 state 让 player 镜像刷新(persona 编辑可能立即影响下一轮)
      try { const d = await window.api.game.state(); applyState(d); } catch (_) {}
      return saved;
    } catch (e) {
      window.__apiToast?.('保存失败', { kind: 'danger', detail: e?.message });
      throw e;
    }
  }, [applyState]);

  const charName = (character && character.name) || (activeChat && activeChat.character_name) || '角色';
  const charInitial = charName.trim().slice(0, 1);
  const personaName = (persona && persona.name) || '你';
  const exportUrl = activeId != null ? window.api.tavern.exportJsonl(activeId) : null;

  return (
    <div className="gc-shell tavern-chat" style={{ '--gc-rail-w': _railResize.size + 'px' }}>
      <GameToastStack />

      <TavernSidebar
        chats={chats} archivedChats={archivedChats} activeId={activeId}
        loading={loadingList} collapsed={railCollapsed} railW={_railResize.size}
        dragHandleProps={_railResize.dragHandleProps}
        onNewChat={() => setImportOpen(true)}
        onOpenChat={openChat}
        onRename={(chat, title) => doRename(chat, title)}
        onArchive={doArchive}
        onDelete={(chat) => setDeleteTarget(chat)}
        onDropCard={onDropCard}
        mobileOpen={mobileNav}
      />

      <main className="gc-main tv-main">
        <TavernHeader
          chat={activeChat} character={character} persona={persona}
          onOpenDrawer={() => setDrawerOpen(true)}
          onExport={exportUrl}
          onOpenNav={() => setMobileNav(true)}
          railCollapsed={railCollapsed}
          onExpandRail={() => setRailCollapsed(false)}
        />

        {activeId == null ? (
          <div className="tv-hero">
            <div className="tv-hero-inner">
              <div className="tv-hero-mark" aria-hidden="true">✻</div>
              <h1 className="tv-hero-title serif">想和谁聊聊？</h1>
              <p className="tv-hero-sub muted">拖入一张酒馆角色卡，立刻开始一段对话。</p>
              <div
                className="tv-hero-drop"
                onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add('drop-active'); }}
                onDragLeave={(e) => e.currentTarget.classList.remove('drop-active')}
                onDrop={(e) => { e.preventDefault(); e.currentTarget.classList.remove('drop-active'); const f = e.dataTransfer?.files?.[0]; if (f) onDropCard(f); }}
                onClick={() => setImportOpen(true)}
                role="button" tabIndex={0}
              >
                <Icon name="upload" size={24} style={{ color: 'var(--accent)' }} />
                <div className="tv-hero-drop-main">把角色卡拖到这里</div>
                <div className="tv-hero-drop-sub muted-2">支持 .png（嵌入元数据）/ .json / .webp，或点此选择</div>
              </div>
            </div>
          </div>
        ) : (
          <TavernChatArea
            history={history} running={running}
            charName={charName} charInitial={charInitial} personaName={personaName}
            hasError={hasError} onRetry={onRetry}
          />
        )}

        {activeId != null && (
          <div className="gc-foot-wrap tv-foot">
            <Composer
              text={text} setText={setText} onSend={onSend} onStop={stopRun} running={running}
              onSendRaw={onSendRaw}
              model={model} setModel={setModel}
              composerMode="writing"
              placeholder={`给 ${charName} 写点什么…`}
              hideSlash hidePermission hideContinue hideAttach
              attachments={[]} removeAttachment={() => {}}
              showSlash={false} showPlus={false} showModel={false} showPerm={false}
              toggleSlash={() => {}} togglePlus={() => {}} toggleModel={() => {}} togglePerm={() => {}}
            />
          </div>
        )}
      </main>

      {mobileNav && <div className="gc-nav-backdrop" onClick={() => setMobileNav(false)} aria-hidden="true" />}

      <TavernImportModal open={importOpen} onClose={() => setImportOpen(false)} onConfirm={onImportConfirm} />

      <TwoCardDrawer
        open={drawerOpen} character={character} persona={persona}
        onClose={() => setDrawerOpen(false)}
        onSavePersona={onSavePersona}
      />

      <RenameModal
        target={renameTarget}
        onClose={() => setRenameTarget(null)}
        onConfirm={(title) => { const tgt = renameTarget; setRenameTarget(null); if (tgt) doRename(tgt, title); }}
      />

      <ConfirmModal
        open={!!deleteTarget}
        title="删除对话?"
        body={<>这将永久删除「<strong>{deleteTarget?.title || deleteTarget?.character_name || ''}</strong>」及其全部聊天记录,无法恢复。</>}
        confirmLabel="删除"
        danger
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && doDelete(deleteTarget)}
      />
    </div>
  );
}

/* ── 重命名输入弹窗 ───────────────────────────────────────────────── */
export function RenameModal({ target, onClose, onConfirm }) {
  const [val, setVal] = useState('');
  useEffect(() => { setVal(target?.title || target?.character_name || ''); }, [target]);
  if (!target) return null;
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{ width: 'min(420px, 100%)' }}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">重命名对话</div>
            <h2 className="pl-modal-title">新标题</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-field">
          <input
            autoFocus value={val} onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && val.trim()) onConfirm(val.trim()); }}
            placeholder="对话标题"
            style={{ width: '100%', padding: '8px 10px', borderRadius: 6, border: '1px solid var(--line-soft)', background: 'var(--bg-deep)', color: 'var(--text)' }}
          />
        </div>
        <footer className="pl-modal-foot">
          <span />
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            <button className="btn primary" onClick={() => val.trim() && onConfirm(val.trim())} disabled={!val.trim()}>保存</button>
          </div>
        </footer>
      </div>
    </div>
  );
  return createPortal(node, document.body);
}
