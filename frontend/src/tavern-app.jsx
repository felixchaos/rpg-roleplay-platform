/* Tavern Mode — SillyTavern 风格 1:1 角色对话(独立页,镜像 Game Console)。
 *
 * 设计:像 Claude 网页版 —— 左侧对话历史 rail + 居中单栏对话 + composer。
 * 复用件(不重写):
 *   - Composer / NarrativeBlock / PlayerBlock (game-composer.jsx / game-app.jsx)
 *   - RpgMarkdown.Block(由 NarrativeBlock 内部使用)
 *   - TavernImportModal(pages/cards.jsx)
 *   - useResizable(responsive.jsx)、Icon(game-icons.jsx)
 *   - SSE:api.game.chat({message, save_id}) + api.game.stop()
 *   - 历史加载:对话即 game_saves(save_kind='tavern'),激活后用 api.game.state() 读 history
 * 关键约束:
 *   - 切换对话前必须先 api.tavern.activate(id),/api/chat 才落到正确 save。
 *   - 新对话的 first_mes 已由后端 seed 为首条 assistant 消息 → 不调任何 opening 端点。
 *
 * 「角色 / Persona / 设定」抽屉(TwoCardDrawer/CardSheet/CardPickerSheet/PersonaHero
 * 及表单逻辑)已整体迁出到 ./tavern-drawer.jsx(rail + 独立滚动内容区重设计)。
 * 本文件只保留 `export { TwoCardDrawer } from './tavern-drawer.jsx'` 兼容旧 import
 * (pages/tavern.jsx 现有 `import { TwoCardDrawer } from '../tavern-app.jsx'` 不用改)。
 */
import React from 'react';
import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { Icon } from './game-icons.jsx';
import Modal from './components/Modal.jsx';
import ConfirmDialog from './components/ConfirmDialog.jsx';
import { useResizable } from './responsive.jsx';
import { NarrativeBlock, PlayerBlock, GameToastStack, SaveImagesStrip, useSaveImages } from './game-app.jsx';
import { ToolCallBlock } from './components/ToolCallBlock.jsx';
import { Composer } from './game-composer.jsx';
import { TavernImportModal } from './pages/cards.jsx';
import { TavernDrawer } from './tavern-drawer.jsx';
import AvatarImg from './components/AvatarImg.jsx';
import { useStickToBottom } from './hooks/useStickToBottom.js';
import {
  useTavernChatRun, applyTavernState, abortRun,
  toolCallInlineAnchor, toolResultInline,
} from './hooks/useTavernChatRun.js';

// 兼容旧 import 名:pages/tavern.jsx 仍 `import { TwoCardDrawer } from '../tavern-app.jsx'`。
export { TavernDrawer, TwoCardDrawer } from './tavern-drawer.jsx';

/* ── 相对时间 ─────────────────────────────────────────────────────── */
// 桶算法委托 data-loader.js 规范 window.__fmt.ago(语义统一 #25);仅本端的「空/坏值 → ''」
// 语义(__fmt.ago 是 '—' / 原样 ts)在此薄包装里保留,故显示零变化。
export function relTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  const ago = (typeof window !== 'undefined' && window.__fmt && window.__fmt.ago);
  return ago ? ago(ts) : d.toLocaleDateString();
}

/* ── 确认弹窗 ──────────────────────────────────────────────────────
   收口到共享 components/ConfirmDialog.jsx(建在 Modal 之上)。导出契约与产出 DOM
   完全不变:eyebrow 危险操作/请确认、宽 420、行高 1.7、createPortal、确认钮无图标。 */
export function ConfirmModal({ open, title, body, confirmLabel, danger, onClose, onConfirm }) {
  const { t } = useTranslation();
  return (
    <ConfirmDialog
      open={open}
      title={title}
      body={body}
      eyebrow={danger ? t('tavern_app.confirm_modal.eyebrow_danger') : t('tavern_app.confirm_modal.eyebrow_default')}
      danger={danger}
      confirmLabel={confirmLabel ?? t('common.confirm')}
      cancelLabel={t('common.cancel')}
      icons={false}
      width={420}
      bodyLineHeight={1.7}
      portal
      onClose={onClose}
      onConfirm={onConfirm}
    />
  );
}

/* ── 单条对话行(标题 + last_snippet + 相对时间 + hover ⋯ 菜单)──────── */
export function TavernChatItem({ chat, active, onOpen, onRename, onArchive, onDelete, archived }) {
  const { t: tl } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef(null);
  const initial = (chat.character_name || chat.title || '?').trim().slice(0, 1);
  const curTitle = chat.title || chat.character_name || tl('tavern_app.chat_item.default_title', { id: chat.id });

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
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!editing) onOpen(chat); } }}
    >
      <AvatarImg src={chat.avatar_path || null} name={chat.character_name || chat.title || '?'} size={36} shape="circle" className="tv-chat-avatar" />
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
              title={tl('tavern_app.chat_item.dblclick_rename')}
              onDoubleClick={(e) => { e.stopPropagation(); startEdit(); }}
            >{curTitle}</span>
          )}
          <span className="tv-chat-time muted-2">{relTime(chat.updated_at)}</span>
        </div>
        {chat.last_snippet
          ? <div className="tv-chat-snippet muted-2">{chat.last_snippet}</div>
          : <div className="tv-chat-snippet muted-2" style={{ fontStyle: 'italic' }}>{chat.character_name || tl('tavern_app.chat_item.fallback_char')}</div>}
      </div>
      <div className="tv-chat-menu-wrap" onClick={(e) => e.stopPropagation()}>
        <button className="iconbtn tv-chat-menu-btn" onClick={() => setMenuOpen((v) => !v)} data-tip={tl('tavern_app.chat_item.menu_more')} aria-label={tl('tavern_app.chat_item.menu_more')}>
          <Icon name="more" size={14} />
        </button>
        {menuOpen && (
          <>
            <div className="tv-menu-scrim" onClick={() => setMenuOpen(false)} />
            <div className="tv-menu">
              <button onClick={startEdit}>
                <Icon name="edit" size={13} /> {tl('tavern_app.chat_item.menu_rename')}
              </button>
              <button onClick={() => { setMenuOpen(false); onArchive(chat, !archived); }}>
                <Icon name="folder" size={13} /> {archived ? tl('tavern_app.chat_item.menu_unarchive') : tl('tavern_app.chat_item.menu_archive')}
              </button>
              <button className="tv-menu-danger" onClick={() => { setMenuOpen(false); onDelete(chat); }}>
                <Icon name="trash" size={13} /> {tl('common.delete')}
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
  const { t } = useTranslation();
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
            <strong>{t('tavern_app.sidebar.brand')}</strong>
          </div>
          <button className="btn primary tv-new-btn" onClick={onNewChat}>
            <Icon name="plus" size={13} /> {t('tavern_app.sidebar.new_chat')}
          </button>
        </div>

        <div
          className={`tv-rail-list ${dragOver ? 'drop-active' : ''}`}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          {loading && <div className="tv-rail-empty muted-2">{t('common.loading')}</div>}
          {!loading && chats.length === 0 && (
            <div className="tv-rail-empty muted-2">
              <Icon name="upload" size={20} style={{ opacity: 0.5, marginBottom: 6 }} />
              <div>{t('tavern_app.sidebar.empty_title')}</div>
              <div style={{ fontSize: 11.5, marginTop: 4 }}>{t('tavern_app.sidebar.empty_hint')}</div>
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
                {t('tavern_app.sidebar.archived_label', { count: archivedChats.length })}
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
      <div className="gc-rail-resize-handle" title={t('tavern_app.sidebar.drag_resize')} {...dragHandleProps} />
    </aside>
  );
}

/* ── 顶栏:persona ⇄ character 两枚 chip + 操作 ─────────────────────── */
function TavernHeader({ chat, character, persona, onOpenDrawer, onExport, onExportTxt, onOpenNav, railCollapsed, onExpandRail }) {
  const { t } = useTranslation();
  const charName = (character && character.name) || (chat && chat.character_name) || '';
  return (
    <header className="tv-header">
      <button className="iconbtn tv-mobile-nav" onClick={onOpenNav} data-tip={t('tavern_app.header.chat_list')} aria-label={t('tavern_app.header.chat_list')}>
        <Icon name="menu" size={16} />
      </button>
      {railCollapsed && (
        <button className="iconbtn" onClick={onExpandRail} data-tip={t('tavern_app.header.expand_rail')} aria-label={t('tavern_app.header.expand_rail')}>
          <Icon name="chevron_right" size={16} />
        </button>
      )}
      {charName ? (
        <button className="tv-title" onClick={onOpenDrawer} data-tip={t('tavern_app.header.view_edit_char')}>
          <span className="tv-title-name">{charName}</span>
          <Icon name="chevron_down" size={13} style={{ opacity: 0.45 }} />
        </button>
      ) : (
        <span className="tv-title tv-title-empty">{t('tavern_app.sidebar.brand')}</span>
      )}
      <div className="tv-header-actions">
        {chat && onExportTxt && (
          <a className="iconbtn" href={onExportTxt} download data-tip={t('tavern_app.header.export_txt')} aria-label={t('tavern_app.header.export_txt')}>
            <Icon name="book" size={15} />
          </a>
        )}
        {chat && onExport && (
          <a className="iconbtn" href={onExport} target="_blank" rel="noopener" data-tip={t('tavern_app.header.export_jsonl')} aria-label={t('tavern_app.header.export_jsonl')}>
            <Icon name="download" size={15} />
          </a>
        )}
        {charName && (
          <button className="iconbtn" onClick={onOpenDrawer} data-tip={t('tavern_app.header.char_persona')} aria-label={t('tavern_app.header.char_persona')}>
            <Icon name="cards" size={15} />
          </button>
        )}
      </div>
    </header>
  );
}

/* ── 转录区(居中单栏)──── m.role assistant/user 等价 SillyTavern is_user ── */
/* ── F1:后台工具流(可折叠、默认折叠、沉浸优先)──────────────────────────
 * ToolCallBlock 已抽为独立模块 components/ToolCallBlock.jsx(game-app 的消息渲染
 * 也要用它,而本文件 import 了 game-app 的组件 —— 原地定义会迫使 game-app 反向
 * import 本文件成环)。此处 re-export 兼容旧 import 路径(同 TwoCardDrawer 先例)。
 */
export { ToolCallBlock };

/* ── 思考流折叠块(reasoning)─────────────────────────────────────────
 * 与正文(NarrativeBlock)上下分区共存,绝不互斥:正文永远以正常散文样式渲染,
 * 思考流单独折叠成一行(默认折叠,标签「思考过程」),展开看完整推理文本。
 * thinking=true(本轮 content 尚未到达)时显示「思考中…」+ spinner;一旦正文开始
 * 到达就把 spinner 收掉、退回可折叠条。流结束(streaming=false)绝不再显示 spinner。
 * 复用 mobile ThinkingBlock 的同构形态(分区共存),非 NarrativeBlock 的互斥旧逻辑。 */
export function TavernThinkingBlock({ text, thinking }) {
  const { t: tr } = useTranslation();
  const [open, setOpen] = useState(false);
  const t = (text == null ? '' : String(text));
  if (!t.trim() && !thinking) return null;
  return (
    <div className={`tvp-thinking${open ? ' open' : ''}`}>
      <button
        type="button"
        className="tvp-thinking-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {thinking
          ? <span className="gc-spinner spin" aria-hidden="true" />
          : <Icon name={open ? 'chevron_down' : 'chevron_right'} size={11} />}
        <span className="tvp-thinking-label">{thinking ? tr('tavern_app.thinking.in_progress') : tr('tavern_app.thinking.label')}</span>
      </button>
      {open && t.trim() && (
        <div className="tvp-thinking-body">{t}</div>
      )}
    </div>
  );
}

export function TavernChatArea({ history, running, saveId, charName, charInitial, charAvatar, personaName, personaAvatar, hasError, errorMsg, onRetry, lastMeta, elapsedLabel, emptyExtra }) {
  const { t } = useTranslation();
  const ref = useRef(null);

  // 内嵌聊天图片:最后一条助手消息绝对索引 + 图片按消息分发(复用游戏端 hook)
  const total0 = Array.isArray(history) ? history.length : 0;
  let lastAsstIdx = -1;
  for (let _i = total0 - 1; _i >= 0; _i--) { if (history[_i] && history[_i].role === 'assistant') { lastAsstIdx = _i; break; } }
  const lastKeyRef = useRef(null);
  lastKeyRef.current = lastAsstIdx >= 0 ? String(lastAsstIdx) : null;
  const imagesByKey = useSaveImages(saveId, lastKeyRef);

  // 粘底守卫收口到 useStickToBottom(逐字等价:threshold 80 / 双守卫 360 / 首屏·末条玩家策略 / instant scrollTop)。
  const _last = history && history[history.length - 1];
  const { showJump, jumpToBottom } = useStickToBottom(ref, {
    deps: [history.length, running],
    lastIsUser: !!(_last && _last.role === 'user'),
    hasContent: history.length > 0,
    mode: 'instant',
    withButton: true,
  });

  const total = history.length;
  const isWaiting = running && (total === 0 || history[total - 1]?.role === 'user');

  return (
    <div ref={ref} className="gc-chat tv-chat">
      <div className="gc-chat-inner">
        {total === 0 && !running && (
          <div className="tv-chat-empty muted-2">
            <Icon name="message_square" size={28} style={{ opacity: 0.4, marginBottom: 8 }} />
            <div>{t('tavern_app.chat_area.empty')}</div>
            {emptyExtra}
          </div>
        )}
        {history.map((m, i) => {
          const commitId = m && (m.commit_id || m.node_id);
          if (m.role === 'assistant') {
            // 工具调用按 anchor 内联进正文(Claude 风,不再永远置顶)。
            // 流式累积在 _toolOps;重载从持久化的 tool_ops 取(record_turn 落库的字段)。
            const rawToolOps = (m && (m._toolOps || m.tool_ops)) || null;
            const toolOps = Array.isArray(rawToolOps) && rawToolOps.length > 0 ? rawToolOps : null;
            const isStreaming = !m.streaming_done && i === total - 1 && running;
            const hasContent = !!(m.content && String(m.content).trim());
            // 思考流是独立可折叠块,与正文分区共存(绝不互斥)。
            // 「思考中…」spinner 只在:本条仍在流式 && 正文还没到 时显示;
            // 一旦正文到达或流结束,退回静态「思考过程」折叠条(无 spinner)。
            const thinkingSpinner = isStreaming && !hasContent;
            // 流式 _thinking;重载 reasoning(record_turn 落库字段)。
            const thinkingText = m._thinking || m.reasoning;
            return (
              <React.Fragment key={`a-${i}`}>
                {(thinkingText || thinkingSpinner) && (
                  <TavernThinkingBlock text={thinkingText} thinking={thinkingSpinner} />
                )}
                {/* 正文走 NarrativeBlock;工具卡片由 renderTool 按 anchor 内联到正文对应位置。 */}
                <NarrativeBlock
                  text={m.content} ts={m.ts}
                  msgIndex={i} saveId={saveId} commitId={commitId}
                  tag={charName} speakerName="" speakerAvatar={charAvatar || charInitial}
                  images={imagesByKey[String(i)] || (i === lastAsstIdx ? imagesByKey['__last'] : undefined)}
                  streaming={isStreaming}
                  meta={i === total - 1 ? lastMeta : null}
                  toolOps={toolOps}
                  renderTool={(ops) => <ToolCallBlock ops={ops} />}
                />
              </React.Fragment>
            );
          }
          return (
            <PlayerBlock
              key={`u-${i}`} text={m.content} ts={m.ts} attachments={m.attachments}
              msgIndex={i} saveId={saveId} commitId={commitId}
              tag={personaName} speakerAvatar={personaAvatar || undefined}
            />
          );
        })}
        {isWaiting && (
          // 等待首 token:复用「思考过程」折叠条的克制样式(标签 + 右侧转圈),
          // 不再用突兀的大圆角浮条。正文/思考流一到达就由 TavernThinkingBlock 接管。
          <div className="tvp-thinking tvp-thinking-waiting" aria-live="polite" role="status">
            <span className="tvp-thinking-label">{charName ? t('tavern_app.chat_area.waiting_named', { name: charName }) : t('tavern_app.thinking.in_progress')}</span>
            <span className="gc-spinner spin" aria-hidden="true" />
            {elapsedLabel ? <span className="gc-waiting-gm-elapsed mono muted-2">{elapsedLabel}</span> : null}
          </div>
        )}
        {hasError && (
          <div className="gc-error">
            <Icon name="warn" size={14} style={{ color: 'var(--danger)' }} />
            <div>
              <strong>{t('tavern_app.chat_area.error_title')}</strong>
              <p className="muted" style={{ margin: '4px 0 0', fontSize: 12.5 }}>
                {(typeof hasError === 'string' && hasError) || errorMsg || t('tavern_app.chat_area.error_default')}
              </p>
              <div className="gc-error-actions">
                <button className="btn" onClick={onRetry} disabled={!onRetry}>{t('tavern_app.chat_area.retry')}</button>
              </div>
            </div>
          </div>
        )}
        {/* 图片已内嵌进对应角色消息气泡(useSaveImages + ChatImageGroup),不再底部独立 strip */}
        {/* 「回到最新」必须 sticky 在滚动容器内(而非 absolute):absolute 在 overflow 滚动容器里
            会随内容滚走、且因祖先无 position:relative 而锚到页面最右。改 sticky + justify-self:end
            → 钉在阅读列右下、不随滚动飘走。游戏版同理(game-app.jsx)。 */}
        {showJump && (
          <button
            onClick={jumpToBottom}
            className="btn"
            style={{ position: 'sticky', bottom: 16, justifySelf: 'end', marginLeft: 'auto', width: 'fit-content', background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 999, padding: '6px 14px', fontSize: 12.5, boxShadow: 'var(--shadow-3, 0 6px 18px -6px rgba(0,0,0,0.5))', zIndex: 5, cursor: 'pointer' }}
            data-tip={t('tavern_app.chat_area.jump_tip')}
          >
            <Icon name="chevron_down" size={12} /> {t('tavern_app.chat_area.jump_btn')}
          </button>
        )}
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════
 *  TavernApp — 顶层
 * ══════════════════════════════════════════════════════════════════ */
export default function TavernApp() {
  const { t } = useTranslation();
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
  const [immersive, setImmersive] = useState(false);
  const [aiReplyLoading, setAiReplyLoading] = useState(false);

  const [importOpen, setImportOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [showPlus_, setShowPlus_] = useState(false);
  const [showSlash_, setShowSlash_] = useState(false);
  // 斜杠命令(/set /loc /time /rel /var …):挑一条把前缀塞进输入框,玩家补参数后发送。
  // 状态写指令由后端 apply_player_directives 处理(酒馆走 /api/chat,与游戏台同源),回执见 tavern-chat-run。
  const onSlashPick = (cmd) => { if (cmd && cmd.trigger) setText(cmd.trigger); setShowSlash_(false); };

  // 收口的酒馆 SSE 状态机(runRef + startRun/stopRun 在 hook 内,折叠语义见 lib/tavern-chat-run.js)。
  const { runRef, startRun: runChat, stopRun } = useTavernChatRun({ setRunning });

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

  /* ── 把一份 state 投射进角色/persona/history(收口到 applyTavernState 核心三段)──── */
  const applyState = useCallback((data) => {
    applyTavernState(data, { setCharacter, setPersona, setHistory, setActiveChat, setImmersive });
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
      window.__apiToast?.(t('tavern_app.toast.open_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [applyState, t]);

  useEffect(() => { reloadList(); }, [reloadList]);

  // 首次进入:自动打开最近的活跃对话(如果有)
  useEffect(() => {
    if (activeId != null) return;
    if (loadingList) return;
    if (chats.length > 0) { openChat(chats[0]); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingList, chats]);

  // 卸载:abort 在途流
  useEffect(() => () => { abortRun(runRef.current, 'unmount'); }, [runRef]);

  /* ── 新对话:导入卡 / 现有卡 → 拿 save_id 后打开 ───────────────────── */
  const openSaveId = useCallback(async (saveId, fallbackName) => {
    await reloadList();
    await openChat({ id: saveId, title: fallbackName || t('tavern_app.chat_item.default_title', { id: saveId }), character_name: fallbackName || '' });
  }, [reloadList, openChat, t]);

  const onImportConfirm = useCallback(async (payload) => {
    setImportOpen(false);
    try {
      if (payload.type === 'card') {
        const r = await window.api.tavern.importCharacter(payload.file);
        if (r && r.ok === false) throw new Error(r.error || t('tavern_app.toast.import_failed'));
        await openSaveId(r.save_id, r.character_name);
        window.__apiToast?.(t('tavern_app.toast.imported_char', { name: r.character_name || t('tavern_app.toast.char_fallback') }), { kind: 'ok', duration: 2000 });
      } else if (payload.type === 'card_json') {
        const r = await window.api.tavern.importCharacter({ json_string: payload.json_string });
        if (r && r.ok === false) throw new Error(r.error || t('tavern_app.toast.import_failed'));
        await openSaveId(r.save_id, r.character_name);
        window.__apiToast?.(t('tavern_app.toast.imported_char', { name: r.character_name || t('tavern_app.toast.char_fallback') }), { kind: 'ok', duration: 2000 });
      } else if (payload.type === 'chat') {
        const r = await window.api.tavern.importJsonl(payload.jsonl, payload.charName);
        if (r && r.ok === false) throw new Error(r.error || t('tavern_app.toast.import_failed'));
        await openSaveId(r.save_id, payload.charName);
        window.__apiToast?.(t('tavern_app.toast.imported_chat', { count: r.commits_imported || 0 }), { kind: 'ok', duration: 2200 });
      }
    } catch (e) {
      window.__apiToast?.(t('tavern_app.toast.import_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [openSaveId, t]);

  // 拖卡进 sidebar / 空状态 → 直接走角色卡导入
  const onDropCard = useCallback(async (file) => {
    if (!file) return;
    if (!/\.(png|json|webp)$/i.test(file.name || '')) {
      window.__apiToast?.(t('tavern_app.toast.drop_unsupported'), { kind: 'warn', duration: 2400 });
      return;
    }
    try {
      const r = await window.api.tavern.importCharacter(file);
      if (r && r.ok === false) throw new Error(r.error || t('tavern_app.toast.import_failed'));
      await openSaveId(r.save_id, r.character_name);
      window.__apiToast?.(t('tavern_app.toast.imported_char', { name: r.character_name || t('tavern_app.toast.char_fallback') }), { kind: 'ok', duration: 2000 });
    } catch (e) {
      window.__apiToast?.(t('tavern_app.toast.import_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [openSaveId, t]);

  /* ── 流式发送(收口到 useTavernChatRun;折叠语义见 lib/tavern-chat-run.js)──────── */
  const startRun = useCallback(async (playerText) => {
    runChat({
      saveId: activeId, model, playerText, applyState,
      setHistory, setRunning, setText, setHasError, setLastPlayerText,
      toast: (title, o) => window.__apiToast?.(title, o),
      reloadList,
      // tool-op:inline anchor 模型(按触发时正文长度内联,不再置顶)。
      onToolCall: toolCallInlineAnchor,
      onToolResult: toolResultInline,
    });
  }, [activeId, model, applyState, reloadList, runChat, setRunning]);

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
    if (!t2) { window.__apiToast?.(t('tavern_app.toast.no_retry_input'), { kind: 'warn', duration: 2000 }); return; }
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
      window.__apiToast?.(t('tavern_app.toast.renamed'), { kind: 'ok', duration: 1500 });
      reloadList();
      if (String(chat.id) === String(activeId)) setActiveChat((p) => ({ ...(p || {}), title }));
    } catch (e) { window.__apiToast?.(t('tavern_app.toast.rename_failed'), { kind: 'danger', detail: e?.message }); }
  }, [reloadList, activeId, t]);

  const doArchive = useCallback(async (chat, archived) => {
    try {
      await window.api.tavern.archive(chat.id, archived);
      window.__apiToast?.(archived ? t('tavern_app.toast.archived') : t('tavern_app.toast.unarchived'), { kind: 'ok', duration: 1500 });
      reloadList();
    } catch (e) { window.__apiToast?.(t('tavern_app.toast.archive_failed'), { kind: 'danger', detail: e?.message }); }
  }, [reloadList, t]);

  const doDelete = useCallback(async (chat) => {
    setDeleteTarget(null);
    try {
      await window.api.tavern.remove(chat.id);
      window.__apiToast?.(t('tavern_app.toast.deleted'), { kind: 'ok', duration: 1500 });
      if (String(chat.id) === String(activeId)) {
        setActiveId(null); setActiveChat(null); setHistory([]); setCharacter(null); setPersona(null);
      }
      reloadList();
    } catch (e) { window.__apiToast?.(t('tavern_app.toast.delete_failed'), { kind: 'danger', detail: e?.message }); }
  }, [reloadList, activeId, t]);

  const onSavePersona = useCallback(async (payload) => {
    try {
      const saved = await window.api.cards.myUpsert(payload);
      window.__apiToast?.(t('tavern_app.toast.persona_saved'), { kind: 'ok', duration: 1500 });
      // 重新读 state 让 player 镜像刷新(persona 编辑可能立即影响下一轮)
      try { const d = await window.api.game.state(); applyState(d); } catch (_) {}
      return saved;
    } catch (e) {
      window.__apiToast?.(t('tavern_app.toast.save_failed'), { kind: 'danger', detail: e?.message });
      throw e;
    }
  }, [applyState, t]);

  /* ── 绑定/更换 AI 角色卡 / 我的角色卡(role: 'character'|'persona')── */
  const onBindCard = useCallback(async (role, cardId) => {
    if (activeId == null) return;
    try {
      await window.api.tavern.bindCard(activeId, role, cardId);
      window.__apiToast?.(t('tavern_app.toast.card_bound'), { kind: 'ok', duration: 1500 });
      try { const d = await window.api.game.state(); applyState(d); } catch (_) {}
    } catch (e) {
      window.__apiToast?.(t('tavern_app.toast.save_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [activeId, applyState, t]);

  /* ── 沉浸式拟人模式开关(持久写后端 state.tavern.immersive)── */
  const onToggleImmersive = useCallback(async (enabled) => {
    if (!activeId) return;
    setImmersive(enabled); // 乐观更新
    try {
      await window.api.tavern.setImmersive(activeId, enabled);
      window.__apiToast?.(
        enabled ? t('tavern_app.drawer.immersive_on_toast') : t('tavern_app.drawer.immersive_off_toast'),
        { kind: 'ok', duration: 1500 },
      );
    } catch (e) {
      setImmersive(!enabled); // 回滚
      window.__apiToast?.(t('tavern_app.toast.save_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [activeId, t]);

  /* ── AI 帮回:以玩家自己的角色生成一条回复 → 填入输入框(不自动发送)── */
  const onAiReply = useCallback(async () => {
    if (!activeId || aiReplyLoading) return;
    setAiReplyLoading(true);
    try {
      const r = await window.api.tavern.aiReply(activeId);
      const reply = (r && r.reply) || '';
      if (!reply) {
        window.__apiToast?.(t('tavern_app.ai_reply.empty'), { kind: 'warn', duration: 2000 });
        return;
      }
      setText(reply);
    } catch (e) {
      window.__apiToast?.(t('tavern_app.ai_reply.fail'), { kind: 'danger', detail: e?.message });
    } finally {
      setAiReplyLoading(false);
    }
  }, [activeId, aiReplyLoading, t]);

  const charName = (character && character.name) || (activeChat && activeChat.character_name) || t('tavern_app.toast.char_fallback');
  const charInitial = charName.trim().slice(0, 1);
  const charAvatar = (character && character.avatar_path) || (activeChat && activeChat.avatar_path) || null;
  const personaName = (persona && persona.name) || t('tavern_app.persona.you');
  const personaAvatar = (persona && persona.avatar_path) || null;
  const exportUrl = activeId != null ? window.api.tavern.exportJsonl(activeId) : null;
  // 酒馆对话即 game_saves 行 → 复用 /api/saves/{id}/export/txt 出人类可读 .txt(当小说分享)
  const exportTxtUrl = activeId != null ? `${window.__API_BASE || ''}/api/saves/${activeId}/export/txt` : null;

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
          onExportTxt={exportTxtUrl}
          onOpenNav={() => setMobileNav(true)}
          railCollapsed={railCollapsed}
          onExpandRail={() => setRailCollapsed(false)}
        />

        {activeId == null ? (
          <div className="tv-hero">
            <div className="tv-hero-inner">
              <div className="tv-hero-mark" aria-hidden="true">✻</div>
              <h1 className="tv-hero-title serif">{t('tavern_app.hero.title')}</h1>
              <p className="tv-hero-sub muted">{t('tavern_app.hero.subtitle')}</p>
              <div
                className="tv-hero-drop"
                onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add('drop-active'); }}
                onDragLeave={(e) => e.currentTarget.classList.remove('drop-active')}
                onDrop={(e) => { e.preventDefault(); e.currentTarget.classList.remove('drop-active'); const f = e.dataTransfer?.files?.[0]; if (f) onDropCard(f); }}
                onClick={() => setImportOpen(true)}
                role="button" tabIndex={0}
              >
                <Icon name="upload" size={24} style={{ color: 'var(--accent)' }} />
                <div className="tv-hero-drop-main">{t('tavern_app.hero.drop_main')}</div>
                <div className="tv-hero-drop-sub muted-2">{t('tavern_app.hero.drop_sub')}</div>
              </div>
            </div>
          </div>
        ) : (
          <TavernChatArea
            history={history} running={running}
            saveId={activeId}
            charName={charName} charInitial={charInitial} charAvatar={charAvatar} personaName={personaName} personaAvatar={personaAvatar}
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
              placeholder={t('tavern_app.composer.placeholder', { name: charName })}
              hidePermission hideContinue
              onSlashPick={onSlashPick} pickedCommand={null} onClearCommand={() => {}}
              attachments={[]} removeAttachment={() => {}}
              showSlash={showSlash_} showPlus={showPlus_} showModel={false} showPerm={false}
              toggleSlash={() => { setShowSlash_(s => !s); setShowPlus_(false); }} togglePlus={() => { setShowPlus_(s => !s); setShowSlash_(false); }} toggleModel={() => {}} togglePerm={() => {}}
              saveId={activeId != null ? String(activeId) : null}
              imageGenKind="chat"
              onAiReply={onAiReply}
              aiReplyOnly
              onAttachPick={() => {}}
            />
          </div>
        )}
      </main>

      {mobileNav && <div className="gc-nav-backdrop" onClick={() => setMobileNav(false)} aria-hidden="true" />}

      {/* multiple={false}:酒馆侧「导入一张卡 = 开一个新对话」,多张没有"开哪个"的语义,
          所以不给多选(卡库侧才循环 payload.files)。 */}
      <TavernImportModal open={importOpen} onClose={() => setImportOpen(false)} onConfirm={onImportConfirm} multiple={false} />

      <TavernDrawer
        open={drawerOpen} character={character} persona={persona}
        onClose={() => setDrawerOpen(false)}
        onSavePersona={onSavePersona}
        chatId={activeId} onBindCard={onBindCard}
        immersive={immersive}
        onToggleImmersive={onToggleImmersive}
      />

      <RenameModal
        target={renameTarget}
        onClose={() => setRenameTarget(null)}
        onConfirm={(title) => { const tgt = renameTarget; setRenameTarget(null); if (tgt) doRename(tgt, title); }}
      />

      <ConfirmModal
        open={!!deleteTarget}
        title={t('tavern_app.delete_dialog.title')}
        body={<>{t('tavern_app.delete_dialog.body_prefix')}<strong>{deleteTarget?.title || deleteTarget?.character_name || ''}</strong>{t('tavern_app.delete_dialog.body_suffix')}</>}
        confirmLabel={t('common.delete')}
        danger
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && doDelete(deleteTarget)}
      />
    </div>
  );
}

/* ── 重命名输入弹窗 ───────────────────────────────────────────────── */
export function RenameModal({ target, onClose, onConfirm }) {
  const { t } = useTranslation();
  const [val, setVal] = useState('');
  useEffect(() => { setVal(target?.title || target?.character_name || ''); }, [target]);
  if (!target) return null;
  const node = (
    <Modal
      open
      eyebrow={t('tavern_app.rename_modal.eyebrow')}
      title={t('tavern_app.rename_modal.title')}
      width={420}
      onClose={onClose}
      footer={<>
        <span />
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" onClick={() => val.trim() && onConfirm(val.trim())} disabled={!val.trim()}>{t('common.save')}</button>
        </div>
      </>}
    >
      <div className="pl-field">
        <input
          autoFocus value={val} onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && val.trim()) onConfirm(val.trim()); }}
          placeholder={t('tavern_app.rename_modal.placeholder')}
          style={{ width: '100%', padding: '8px 10px', borderRadius: 6, border: '1px solid var(--line-soft)', background: 'var(--bg-deep)', color: 'var(--text)' }}
        />
      </div>
    </Modal>
  );
  return createPortal(node, document.body);
}
