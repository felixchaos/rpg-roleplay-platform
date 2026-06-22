/* MobileTavern — 移动原生 UI 的酒馆模式。
 *
 * 铁律:
 *  - 不复用任何桌面端 UI 组件(TavernSidebar/TavernHeader/TavernChatArea/TwoCardDrawer/ChatItem 等)。
 *  - 数据/逻辑层全部复用 window.api.tavern.* + window.api.game.*。
 *  - startRun / stopRun 的 SSE handler 逐字照搬 tavern-app.jsx 行 699 起。
 *  - 样式全部走 .m-root 已有 class;新增 class 见文件末 neededCss 注释。
 *
 * 两屏(view='list' | 'chat')用组件内部 useState 切换,不依赖外部路由。
 * nav = { go, push, pop, switchTab, toast, openGame }
 */
import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';
import { Icon } from '../icons.jsx';
import { MobileComposer } from '../Composer.jsx';
import { useStickToBottom } from '../../hooks/useStickToBottom.js';
import {
  useTavernChatRun, applyTavernState, abortRun,
  toolCallInline, toolResultInline,
} from '../../hooks/useTavernChatRun.js';
// 不复用电脑端 cards.jsx 的 UI 组件 —— 移动原生重写卡片读视图/persona 表单 + 纯数据 helper。
// 注:此处 cardFormInit/cardFormPayload 字段集刻意比 pages/cards.jsx 窄(酒馆 persona 用
// language_style/secret,无 full_name/importance/first_revealed_chapter/token_budget/
// priority/enabled/scope),与 _CARD_FIELDS/_CARD_MULTILINE/CardReadout/PersonaFields 强耦合,
// 字段集未对齐 → 不复用桌面版,保留本地实现(语义统一 #3 GUARD:不齐则保留并注释)。
function _cardFields() {
  return [
    ['name', i18n.t('mobile.tavern.card_field.name')],
    ['identity', i18n.t('mobile.tavern.card_field.identity')],
    ['background', i18n.t('mobile.tavern.card_field.background')],
    ['appearance', i18n.t('mobile.tavern.card_field.appearance')],
    ['personality', i18n.t('mobile.tavern.card_field.personality')],
    ['language_style', i18n.t('mobile.tavern.card_field.language_style')],
    ['current_status', i18n.t('mobile.tavern.card_field.current_status')],
    ['secret', i18n.t('mobile.tavern.card_field.secret')],
    ['sample_dialogue', i18n.t('mobile.tavern.card_field.sample_dialogue')],
  ];
}
const _CARD_MULTILINE = new Set(['background', 'appearance', 'personality', 'current_status', 'secret', 'sample_dialogue']);
function cardFormInit(c) {
  c = c || {};
  const o = {};
  for (const [k] of _cardFields()) o[k] = c[k] || (k === 'identity' ? (c.role || '') : '');
  o.tags = Array.isArray(c.tags) ? c.tags.join(', ') : (c.tags || '');
  o.aliases = Array.isArray(c.aliases) ? c.aliases.join(', ') : (c.aliases || '');
  return o;
}
function cardFormPayload(f, base) {
  const splitList = (s) => String(s || '').split(',').map((x) => x.trim()).filter(Boolean);
  const o = { ...(base || {}) };
  for (const [k] of _cardFields()) o[k] = f[k] || '';
  o.tags = splitList(f.tags); o.aliases = splitList(f.aliases);
  return o;
}
function CardReadout({ card }) {
  if (!card) return null;
  return (
    <div style={{ padding: '14px 0', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {_cardFields().map(([k, l]) => {
        const v = card[k] || (k === 'identity' ? card.role : '');
        return v ? (
          <div key={k}>
            <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '.12em', color: 'var(--muted-2)', marginBottom: 4 }}>{l}</div>
            <div style={{ fontSize: 13.5, lineHeight: 1.65, color: 'var(--text-quiet)', whiteSpace: 'pre-wrap' }}>{v}</div>
          </div>
        ) : null;
      })}
      {Array.isArray(card.tags) && card.tags.length ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {card.tags.map((t, i) => <span key={i} className="mono" style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, background: 'var(--panel-3)', color: 'var(--muted)' }}>{t}</span>)}
        </div>
      ) : null}
    </div>
  );
}
function PersonaFields({ form, u }) {
  const { t } = useTranslation();
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {_cardFields().map(([k, l]) => (
        <label key={k} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>{l}</span>
          {_CARD_MULTILINE.has(k)
            ? <textarea className="tv-m-input" rows={3} value={form[k] || ''} onChange={(e) => u(k, e.target.value)} />
            : <input className="tv-m-input" value={form[k] || ''} onChange={(e) => u(k, e.target.value)} />}
        </label>
      ))}
      <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>{t('mobile.tavern.card_field.tags_label')}</span>
        <input className="tv-m-input" value={form.tags || ''} onChange={(e) => u('tags', e.target.value)} />
      </label>
    </div>
  );
}

/* ─── 工具函数 ─────────────────────────────────────────────────────── */
// 桶算法委托 data-loader.js 规范 window.__fmt.ago(语义统一 #25);本端「空/坏值 → ''」语义保留。
function relTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  const ago = (typeof window !== 'undefined' && window.__fmt && window.__fmt.ago);
  return ago ? ago(ts) : d.toLocaleDateString();
}

function tvNow() {
  const d = new Date();
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

/* ─── 移动端 Toast ─────────────────────────────────────────────────── */
function MobileToast({ msg, kind }) {
  if (!msg) return null;
  return (
    <div className={`toast show ${kind || 'ok'}`}>
      <Icon name={kind === 'danger' ? 'warn' : 'check'} size={15} />
      {msg}
    </div>
  );
}

/* ─── 工具调用折叠块(对应桌面端 ToolCallBlock)─────────────────────── */
function ToolCallBlock({ ops }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!Array.isArray(ops) || ops.length === 0) return null;
  const n = ops.length;
  const firstName = (ops[0] && ops[0].tool) || t('mobile.tavern.tool.default_name');
  const summary = n === 1
    ? t('mobile.tavern.tool.call_one', { name: firstName })
    : t('mobile.tavern.tool.call_many', { count: n, name: firstName });
  function fmt(v) {
    if (v == null) return '';
    if (typeof v === 'string') return v;
    try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
  }
  return (
    <div className="tv-m-tools">
      <button className="tv-m-tools-toggle" onClick={() => setOpen(v => !v)}>
        <span style={{ color: 'var(--muted-2)' }}>⚙</span>
        <Icon name={open ? 'chevron_down' : 'chevron_right'} size={11} />
        <span className="tv-m-tools-summary">{summary}</span>
      </button>
      {open && (
        <div className="tv-m-tools-detail">
          {ops.map((op, i) => (
            <div key={i} className="tv-m-tool-item">
              <div className="tv-m-tool-name">
                <span
                  className="tv-m-tool-dot"
                  style={{ background: op && op.ok === false ? 'var(--danger)' : 'var(--ok)' }}
                />
                {(op && op.tool) || t('mobile.tavern.tool.default_name')}
              </div>
              {op && op.args != null && (
                <pre className="tv-m-tool-kv"><span className="tv-m-tool-k">args </span>{fmt(op.args)}</pre>
              )}
              {op && (op.result != null || op.error != null) && (
                <pre className="tv-m-tool-kv" style={{ color: op.ok === false ? 'var(--danger)' : undefined }}>
                  <span className="tv-m-tool-k">{op.ok === false ? 'error ' : 'result '}</span>
                  {fmt(op.ok === false ? (op.error != null ? op.error : op.result) : op.result)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─── 思考流折叠块 ──────────────────────────────────────────────────── */
function ThinkingBlock({ text }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="tv-m-thinking">
      <button className="tv-m-thinking-toggle" onClick={() => setOpen(v => !v)}>
        <Icon name={open ? 'chevron_down' : 'chevron_right'} size={11} />
        <span className="tv-m-thinking-label">{t('mobile.tavern.thinking.label')}</span>
      </button>
      {open && (
        <div className="tv-m-thinking-body">{text}</div>
      )}
    </div>
  );
}

/* ─── 正文段落渲染(把 \n\n 切段)────────────────────────────────────── */
function Paras({ text }) {
  if (!text) return null;
  return (
    <>
      {(text || '').split(/\n\n+/).map((p, i) => (
        <p key={i} style={{ margin: '0 0 0.85em' }}>
          {p.split(/\n/).map((ln, j) => (
            <React.Fragment key={j}>{j ? <br /> : null}{ln}</React.Fragment>
          ))}
        </p>
      ))}
    </>
  );
}

/* ─── 底部 sheet(通用):带 grip + scrim + 滑入动画 ─────────────────── */
function BottomSheet({ show, onClose, children, maxHeight = '82%' }) {
  return (
    <div className={`sheet-wrap${show ? ' show' : ''}`}>
      <div className="sheet-scrim" onClick={onClose} />
      <div className="sheet" style={{ maxHeight }}>
        <div className="sheet-grip" />
        {children}
      </div>
    </div>
  );
}

/* ─── 聊天菜单(重命名 / 归档 / 删除 / 自动命名 / 系统提示 / 导出)────── */
function ChatMenuSheet({ show, chat, onClose, onRename, onArchive, onDelete, onAutotitle, onSystemPrompt, onExport }) {
  const { t } = useTranslation();
  if (!chat) return null;
  const archived = !!chat.archived;
  return (
    <BottomSheet show={show} onClose={onClose}>
      <div className="sheet-title">{chat.title || chat.character_name || t('mobile.tavern.chat.default_title', { id: chat.id })}</div>
      <div className="sheet-sub">{archived ? t('mobile.tavern.menu.archived_label') : t('mobile.tavern.menu.actions_label')}</div>
      <div className="sheet-list">
        <button className="sheet-item" onClick={() => { onClose(); onAutotitle(chat); }}>
          <span className="sheet-ico"><Icon name="spark" size={17} /></span>
          <span className="sheet-tx"><strong>{t('mobile.tavern.menu.autotitle')}</strong><span>{t('mobile.tavern.menu.autotitle_sub')}</span></span>
        </button>
        <button className="sheet-item" onClick={() => { onClose(); onSystemPrompt(chat); }}>
          <span className="sheet-ico"><Icon name="braces" size={17} /></span>
          <span className="sheet-tx"><strong>{t('mobile.tavern.menu.system_prompt')}</strong><span>{t('mobile.tavern.menu.system_prompt_sub')}</span></span>
        </button>
        <button className="sheet-item" onClick={() => { onClose(); onRename(chat); }}>
          <span className="sheet-ico"><Icon name="edit" size={17} /></span>
          <span className="sheet-tx"><strong>{t('common.edit')}</strong><span>{t('mobile.tavern.menu.rename_sub')}</span></span>
        </button>
        {onExport && (
          <a className="sheet-item" href={onExport} target="_blank" rel="noopener" onClick={onClose}>
            <span className="sheet-ico"><Icon name="download" size={17} /></span>
            <span className="sheet-tx"><strong>{t('mobile.tavern.menu.export_jsonl')}</strong><span>{t('mobile.tavern.menu.export_jsonl_sub')}</span></span>
          </a>
        )}
        <button className="sheet-item" onClick={() => { onClose(); onArchive(chat, !archived); }}>
          <span className="sheet-ico"><Icon name="folder" size={17} /></span>
          <span className="sheet-tx">
            <strong>{archived ? t('mobile.tavern.menu.unarchive') : t('mobile.tavern.menu.archive')}</strong>
            <span>{archived ? t('mobile.tavern.menu.unarchive_sub') : t('mobile.tavern.menu.archive_sub')}</span>
          </span>
        </button>
        <button className="sheet-item danger" onClick={() => { onClose(); onDelete(chat); }}>
          <span className="sheet-ico"><Icon name="trash" size={17} /></span>
          <span className="sheet-tx"><strong>{t('mobile.tavern.menu.delete_chat')}</strong><span>{t('mobile.tavern.menu.delete_chat_sub')}</span></span>
        </button>
      </div>
    </BottomSheet>
  );
}

/* ─── 删除确认 sheet ───────────────────────────────────────────────
   语义统一 Batch 6b GUARD:本站不收口到 mobile/Sheet.jsx 的 <ConfirmSheet>。差异点:
   ① 多一个 .confirm-preview 引用框(高亮显示对话标题)统一版无此结构
   ② 删除钮内含 trash Icon(统一版纯文案)③ 包在本文件 <BottomSheet>(show 切换 + 滑入)中,
   与统一版 open 渲染契约不同。1:1 复刻不了 → 保留原样。 */
function DeleteConfirmSheet({ show, chat, onClose, onConfirm }) {
  const { t } = useTranslation();
  if (!chat) return null;
  const title = chat.title || chat.character_name || t('mobile.tavern.chat.default_title', { id: chat.id });
  return (
    <BottomSheet show={show} onClose={onClose}>
      <div className="sheet-title">{t('mobile.tavern.delete.title')}</div>
      <div className="confirm-preview">{t('mobile.tavern.delete.preview', { title })}</div>
      <div className="confirm-note">{t('mobile.tavern.delete.note_prefix')}<strong>{t('mobile.tavern.delete.note_irreversible')}</strong>{t('mobile.tavern.delete.note_suffix')}</div>
      <div className="sheet-actions">
        <button className="sheet-btn" onClick={onClose}>{t('common.cancel')}</button>
        <button className="sheet-btn danger" onClick={onConfirm}>
          <Icon name="trash" size={15} /> {t('common.delete')}
        </button>
      </div>
    </BottomSheet>
  );
}

/* ─── 重命名 sheet ───────────────────────────────────────────────── */
function RenameSheet({ show, chat, onClose, onConfirm }) {
  const { t } = useTranslation();
  const [val, setVal] = useState('');
  useEffect(() => { if (chat) setVal(chat.title || chat.character_name || ''); }, [chat]);
  if (!chat) return null;
  const commit = () => { const v = val.trim(); if (v) onConfirm(chat, v); };
  return (
    <BottomSheet show={show} onClose={onClose}>
      <div className="sheet-title">{t('mobile.tavern.rename.title')}</div>
      <div style={{ padding: '4px 4px 12px' }}>
        <input
          className="tv-m-input"
          autoFocus
          value={val}
          onChange={e => setVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && val.trim()) { e.preventDefault(); commit(); } }}
          placeholder={t('mobile.tavern.rename.placeholder')}
          maxLength={200}
        />
      </div>
      <div className="sheet-actions">
        <button className="sheet-btn" onClick={onClose}>{t('common.cancel')}</button>
        <button className="sheet-btn primary" onClick={commit} disabled={!val.trim()}>
          <Icon name="check" size={14} /> {t('common.save')}
        </button>
      </div>
    </BottomSheet>
  );
}

/* ─── 系统提示词编辑 sheet ───────────────────────────────────────── */
function SystemPromptSheet({ show, chat, systemPrompt, onClose, onSave }) {
  const { t } = useTranslation();
  const [val, setVal] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (show) { setVal(systemPrompt || ''); } }, [show, systemPrompt]);
  if (!chat) return null;
  const doSave = async () => {
    setSaving(true);
    try { await onSave(val); onClose(); } catch (_) {} finally { setSaving(false); }
  };
  return (
    <BottomSheet show={show} onClose={onClose} maxHeight="90%">
      <div className="sheet-title">{t('mobile.tavern.sysprompt.title')}</div>
      <div className="sheet-sub">{t('mobile.tavern.sysprompt.sub')}</div>
      <div style={{ padding: '4px 4px 10px' }}>
        <textarea
          className="tv-m-input"
          rows={10}
          value={val}
          onChange={e => setVal(e.target.value)}
          placeholder={t('mobile.tavern.sysprompt.placeholder')}
          style={{ resize: 'none', minHeight: 180 }}
        />
      </div>
      <div className="sheet-actions">
        <button className="sheet-btn" onClick={onClose} disabled={saving}>{t('common.cancel')}</button>
        <button className="sheet-btn primary" onClick={doSave} disabled={saving}>
          <Icon name="check" size={14} /> {saving ? t('mobile.tavern.sysprompt.saving') : t('common.save')}
        </button>
      </div>
    </BottomSheet>
  );
}

/* ─── 双卡抽屉(右侧滑入 drawer):AI 角色卡 + 我的 persona + 系统提示 ── */
function TwoCardDrawer({ open, character, persona, systemPrompt, immersive, onToggleImmersive, onClose, onSavePersona, onSaveSystemPrompt }) {
  const [tab, setTab] = useState('character');
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(() => cardFormInit(persona));
  const [saving, setSaving] = useState(false);
  const [spVal, setSpVal] = useState(systemPrompt || '');
  const [spEditing, setSpEditing] = useState(false);
  const [spSaving, setSpSaving] = useState(false);

  useEffect(() => { setForm(cardFormInit(persona)); setEditing(false); }, [persona, open]);
  useEffect(() => { setSpVal(systemPrompt || ''); setSpEditing(false); }, [systemPrompt, open]);

  const u = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const doSave = async () => {
    setSaving(true);
    try { await onSavePersona(cardFormPayload(form, persona)); setEditing(false); }
    finally { setSaving(false); }
  };
  const doSaveSP = async () => {
    setSpSaving(true);
    try { await (onSaveSystemPrompt && onSaveSystemPrompt(spVal)); setSpEditing(false); }
    finally { setSpSaving(false); }
  };

  const { t } = useTranslation();
  return (
    <>
      <div className={`scrim${open ? ' show' : ''}`} onClick={onClose} />
      <aside className={`drawer drawer-right${open ? ' open' : ''}`}>
        <div className="drawer-head">
          <button className="drawer-x" onClick={onClose}><Icon name="close" size={15} /></button>
          <h2 style={{ fontFamily: 'var(--font-serif)', fontSize: 16 }}>{t('mobile.tavern.drawer.heading')}</h2>
        </div>
        {/* 三 Tab 分段 */}
        <div className="tv-m-drawer-tabs">
          <button
            className={`tv-m-drawer-tab${tab === 'character' ? ' active' : ''}`}
            onClick={() => setTab('character')}
          >
            <Icon name="cards" size={13} /> {t('mobile.tavern.drawer.tab_character')}
          </button>
          <button
            className={`tv-m-drawer-tab${tab === 'persona' ? ' active' : ''}`}
            onClick={() => setTab('persona')}
          >
            <Icon name="user" size={13} /> Persona
          </button>
          <button
            className={`tv-m-drawer-tab${tab === 'system' ? ' active' : ''}`}
            onClick={() => setTab('system')}
          >
            <Icon name="braces" size={13} /> {t('mobile.tavern.drawer.tab_system')}
          </button>
        </div>

        <div className="drawer-body" style={{ padding: '0 14px' }}>
          {/* ── AI 角色卡 ── */}
          {tab === 'character' && (
            <>
              {/* 沉浸式拟人模式开关:让 AI 以真人(角色卡)口吻实时对话、不替玩家说话/行动 */}
              {onToggleImmersive && (
                <div className="tv-m-immersive-row">
                  <div className="tv-m-immersive-tx">
                    <strong>{t('mobile.tavern.immersive.label')}</strong>
                    <span className="muted-2">{t('mobile.tavern.immersive.desc')}</span>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={!!immersive}
                    className={`tv-m-switch${immersive ? ' on' : ''}`}
                    onClick={() => onToggleImmersive(!immersive)}
                    aria-label={t('mobile.tavern.immersive.label')}
                  >
                    <span className="tv-m-switch-knob" />
                  </button>
                </div>
              )}
              {character
                ? <CardReadout card={character} />
                : <div className="muted-2" style={{ padding: '28px 0', textAlign: 'center', fontSize: 13 }}>{t('mobile.tavern.drawer.no_character')}</div>}
            </>
          )}

          {/* ── Persona ── */}
          {tab === 'persona' && (
            !editing ? (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0 10px' }}>
                  <strong style={{ fontSize: 14, fontFamily: 'var(--font-serif)' }}>
                    {(persona && persona.name) || t('mobile.tavern.drawer.your_persona')}
                  </strong>
                  {persona && (
                    <button
                      className="sheet-btn"
                      style={{ flex: 'none', width: 'auto', height: 34, padding: '0 12px', fontSize: 13 }}
                      onClick={() => setEditing(true)}
                    >
                      <Icon name="edit" size={12} /> {t('common.edit')}
                    </button>
                  )}
                </div>
                {persona
                  ? <CardReadout card={persona} />
                  : <div className="muted-2" style={{ padding: '28px 0', textAlign: 'center', fontSize: 13 }}>{t('mobile.tavern.drawer.no_persona')}</div>
                }
              </>
            ) : (
              <>
                <div style={{ paddingTop: 14 }}>
                  <PersonaFields form={form} u={u} />
                </div>
                <div className="sheet-actions" style={{ marginTop: 14, paddingBottom: 14 }}>
                  <button className="sheet-btn" onClick={() => setEditing(false)} disabled={saving}>{t('common.cancel')}</button>
                  <button className="sheet-btn primary" onClick={doSave} disabled={saving}>
                    <Icon name="check" size={12} /> {saving ? t('mobile.tavern.sysprompt.saving') : t('common.save')}
                  </button>
                </div>
              </>
            )
          )}

          {/* ── 系统提示词 ── */}
          {tab === 'system' && (
            <div style={{ paddingTop: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <strong style={{ fontSize: 14 }}>{t('mobile.tavern.sysprompt.title')}</strong>
                {!spEditing && onSaveSystemPrompt && (
                  <button
                    className="sheet-btn"
                    style={{ flex: 'none', width: 'auto', height: 34, padding: '0 12px', fontSize: 13 }}
                    onClick={() => setSpEditing(true)}
                  >
                    <Icon name="edit" size={12} /> {t('common.edit')}
                  </button>
                )}
              </div>
              {!spEditing ? (
                (spVal || '').trim()
                  ? <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.7, color: 'var(--text-quiet)' }}>{spVal}</div>
                  : <div className="muted-2" style={{ fontSize: 13, lineHeight: 1.7 }}>{t('mobile.tavern.drawer.no_sysprompt')}</div>
              ) : (
                <>
                  <textarea
                    className="tv-m-input"
                    value={spVal}
                    onChange={e => setSpVal(e.target.value)}
                    rows={10}
                    placeholder={t('mobile.tavern.sysprompt.placeholder_short')}
                    style={{ resize: 'vertical', minHeight: 160 }}
                  />
                  <div className="sheet-actions" style={{ marginTop: 12, paddingBottom: 14 }}>
                    <button className="sheet-btn" onClick={() => { setSpVal(systemPrompt || ''); setSpEditing(false); }} disabled={spSaving}>{t('common.cancel')}</button>
                    <button className="sheet-btn primary" onClick={doSaveSP} disabled={spSaving}>
                      <Icon name="check" size={12} /> {spSaving ? t('mobile.tavern.sysprompt.saving') : t('common.save')}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </aside>
    </>
  );
}

/* ─── 导入 / 新对话 sheet ────────────────────────────────────────── */
function ImportSheet({ show, onClose, onPickFile, onJsonlFile, onCreateBlank }) {
  const { t } = useTranslation();
  const fileRef = useRef(null);
  const jsonlRef = useRef(null);

  return (
    <BottomSheet show={show} onClose={onClose} maxHeight="88%">
      <div className="sheet-title">{t('mobile.tavern.import.title')}</div>
      <div className="sheet-sub">{t('mobile.tavern.import.sub')}</div>

      {/* 主入口:空白开始(直接开聊,不预设角色卡)。放最上、accent 样式 = 推荐路径。 */}
      <button className="tv-m-import-btn primary" onClick={onCreateBlank}>
        <span className="tv-m-import-ic"><Icon name="feedback" size={20} /></span>
        <span className="tv-m-import-tx">
          <strong>{t('mobile.tavern.import.blank_btn')}</strong>
          <span>{t('mobile.tavern.import.blank_btn_sub')}</span>
        </span>
      </button>

      <div className="tv-m-import-or muted-2">{t('mobile.tavern.import.or')}</div>

      {/* 拖/选卡 —— 用 <label> 包裹 input 原生触发文件选择器:
          手机浏览器对 display:none input 的 .click() 多会拦截,改 label 关联 + 视觉隐藏(非 display:none)。 */}
      <label className="tv-m-import-btn" style={{ position: 'relative' }}>
        <span className="tv-m-import-ic"><Icon name="upload" size={20} /></span>
        <span className="tv-m-import-tx">
          <strong>{t('mobile.tavern.import.card_btn')}</strong>
          <span>{t('mobile.tavern.import.card_btn_sub')}</span>
        </span>
        <input
          ref={fileRef} type="file" accept=".png,.json,.webp"
          style={{ position: 'absolute', width: 1, height: 1, opacity: 0, overflow: 'hidden', pointerEvents: 'none' }}
          onChange={e => { const f = e.target.files && e.target.files[0]; if (f) { onClose(); onPickFile(f); } e.target.value = ''; }}
        />
      </label>

      {/* 导入聊天记录 JSONL */}
      <label className="tv-m-import-btn" style={{ marginTop: 8, position: 'relative' }}>
        <span className="tv-m-import-ic"><Icon name="download" size={20} /></span>
        <span className="tv-m-import-tx">
          <strong>{t('mobile.tavern.import.jsonl_btn')}</strong>
          <span>{t('mobile.tavern.import.jsonl_btn_sub')}</span>
        </span>
        <span className="tv-m-import-fmt">JSONL</span>
        <input
          ref={jsonlRef} type="file" accept=".jsonl,.json"
          style={{ position: 'absolute', width: 1, height: 1, opacity: 0, overflow: 'hidden', pointerEvents: 'none' }}
          onChange={e => { const f = e.target.files && e.target.files[0]; if (f) { onClose(); onJsonlFile(f); } e.target.value = ''; }}
        />
      </label>
    </BottomSheet>
  );
}

/* ─── 单条对话项(列表页)──────────────────────────────────────────── */
function ChatListItem({ chat, active, onOpen, onMenu }) {
  const { t } = useTranslation();
  const initial = (chat.character_name || chat.title || '?').trim().slice(0, 1);
  const curTitle = chat.title || chat.character_name || t('mobile.tavern.chat.default_title', { id: chat.id });

  return (
    // 外层用 div[role=button] 而非 <button>:内部含「更多」菜单按钮,button 套 button =
    // 非法 HTML / React 注水报错(In HTML, button cannot be a descendant of button)。
    <div
      role="button"
      tabIndex={0}
      className={`tv-m-chat-item${active ? ' active' : ''}`}
      onClick={() => onOpen(chat)}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(chat); } }}
    >
      <span className="tv-m-chat-av serif">{initial}</span>
      <span className="tv-m-chat-main">
        <span className="tv-m-chat-row">
          <span className="tv-m-chat-title">{curTitle}</span>
          <span className="tv-m-chat-time muted-2">{relTime(chat.updated_at)}</span>
        </span>
        {chat.last_snippet
          ? <span className="tv-m-chat-snippet muted-2">{chat.last_snippet}</span>
          : <span className="tv-m-chat-snippet muted-2" style={{ fontStyle: 'italic' }}>{chat.character_name || t('mobile.tavern.chat.default_character')}</span>}
      </span>
      <button
        className="tv-m-chat-menu-btn"
        onClick={e => { e.stopPropagation(); onMenu(chat); }}
        aria-label={t('mobile.tavern.chat.more_aria')}
      >
        <Icon name="more" size={17} />
      </button>
    </div>
  );
}

/* ─── 列表屏 ──────────────────────────────────────────────────────── */
function ListView({ chats, archivedChats, activeId, loading, onExit, onOpen, onMenu, onNew, onQuickStart }) {
  const { t } = useTranslation();
  const [showArchived, setShowArchived] = useState(false);
  const empty = !loading && chats.length === 0 && archivedChats.length === 0;

  return (
    <div className="tv-m-screen">
      {/* 顶栏 */}
      <div className="topbar">
        <button className="tb-exit" onClick={onExit}>
          <Icon name="chevron_left" size={15} /> {t('mobile.tavern.list.back_to_app')}
        </button>
        <div className="tb-title">
          <strong>{t('mobile.tavern.list.heading')}</strong>
          <span className="sub"><Icon name="feedback" size={11} /> {t('mobile.tavern.list.sub')}</span>
        </div>
        <button className="tb-btn accent" onClick={onNew} aria-label={t('mobile.tavern.import.title')}>
          <Icon name="plus" size={18} />
        </button>
      </div>

      {/* 正文 */}
      {loading ? (
        <div className="tv-m-empty muted-2">{t('common.loading')}</div>
      ) : empty ? (
        <div className="tv-m-hero">
          <div className="tv-m-hero-mark">✻</div>
          <h1 className="tv-m-hero-title serif">{t('mobile.tavern.list.hero_title')}</h1>
          <p className="tv-m-hero-sub muted">{t('mobile.tavern.list.hero_sub')}</p>
          {/* 主操作:一键直接开聊(空白起手,不强制上传角色卡)。 */}
          <button className="tv-m-hero-cta" onClick={onQuickStart}>
            <Icon name="feedback" size={18} />
            {t('mobile.tavern.list.hero_quick_start')}
          </button>
          {/* 次操作:导入角色卡 / 聊天记录。 */}
          <button className="tv-m-hero-drop" onClick={onNew}>
            <span className="tv-m-hero-drop-ic"><Icon name="upload" size={22} /></span>
            <span className="tv-m-hero-drop-main">{t('mobile.tavern.list.hero_drop_main')}</span>
            <span className="tv-m-hero-drop-sub">{t('mobile.tavern.list.hero_drop_sub')}</span>
          </button>
        </div>
      ) : (
        <div className="tv-m-list scroll" style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
          {/* 新对话按钮(列表非空时) */}
          <button className="tv-m-newchat" onClick={onNew}>
            <span className="tv-m-newchat-ic"><Icon name="plus" size={20} /></span>
            <span className="tv-m-newchat-tx">
              <strong>{t('mobile.tavern.import.title')}</strong>
              <span>{t('mobile.tavern.list.newchat_sub')}</span>
            </span>
          </button>

          {chats.map(c => (
            <ChatListItem
              key={c.id} chat={c}
              active={String(c.id) === String(activeId)}
              onOpen={onOpen} onMenu={onMenu}
            />
          ))}

          {archivedChats.length > 0 && (
            <div className="tv-m-archived-section">
              <button className="tv-m-archived-toggle" onClick={() => setShowArchived(v => !v)}>
                <Icon name={showArchived ? 'chevron_down' : 'chevron_right'} size={13} />
                {t('mobile.tavern.list.archived_toggle', { count: archivedChats.length })}
              </button>
              {showArchived && archivedChats.map(c => (
                <ChatListItem
                  key={c.id} chat={c}
                  active={String(c.id) === String(activeId)}
                  onOpen={onOpen} onMenu={onMenu}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── 对话屏 ──────────────────────────────────────────────────────── */
function ChatView({
  activeChat, character, persona, history, running, hasError, systemPrompt,
  onBack, onSend, onStop, onRetry, onOpenDrawer, onOpenMenu, onToast,
  onAiReply, aiReplyLoading,
}) {
  const [text, setText] = useState('');
  const [plusOpen, setPlusOpen] = useState(false); // + 附加功能 sheet(AI 帮回 等)
  const [pressedIdx, setPressedIdx] = useState(null);
  const [msgSheet, setMsgSheet] = useState(null); // 长按消息 → 操作 sheet(与游戏台同一套交互)
  const lpTimer = useRef(null);
  const openMsgSheet = (i) => { setPressedIdx(i); try { if (navigator.vibrate) navigator.vibrate(12); } catch (_) {} setMsgSheet({ idx: i }); };
  const startPress = (i) => { lpTimer.current = setTimeout(() => openMsgSheet(i), 420); };
  const cancelPress = () => clearTimeout(lpTimer.current);
  const closeMsgSheet = () => { setMsgSheet(null); setPressedIdx(null); };
  const threadRef = useRef(null);
  const taRef = useRef(null);

  const { t } = useTranslation();
  const charName = (character && character.name) || (activeChat && activeChat.character_name) || t('mobile.tavern.chat.default_char_name');

  /* 自动滚底:收口到 useStickToBottom(逐字等价:threshold 80 / 双守卫 360 / 首屏·末条玩家策略 / instant scrollTop)。 */
  const _last = history && history[history.length - 1];
  const { showJump, jumpToBottom } = useStickToBottom(threadRef, {
    deps: [history.length, running],
    lastIsUser: !!(_last && _last.role === 'user'),
    hasContent: history.length > 0,
    mode: 'instant',
    withButton: true,
  });

  /* textarea 自动增高已下沉到 MobileComposer(taRef 仍传入,供其管理高度)。 */

  const submit = () => {
    const t = text.trim();
    if (!t || running) return;
    onSend(t);
    setText('');
  };

  const total = history.length;
  const isWaiting = running && (total === 0 || history[total - 1]?.role === 'user');
  const lastAssistantIdx = (() => { for (let i = total - 1; i >= 0; i--) { if (history[i]?.role === 'assistant') return i; } return -1; })();

  const copy = async (txt) => {
    try { await navigator.clipboard.writeText(txt || ''); } catch (_) {}
    // 用 MobileTavern 自有的可见 fireToast(经 onToast 传入),不再走无渲染器的 window.__apiToast。
    onToast?.(t('mobile.tavern.chat.copied'), 'ok');
  };

  return (
    <div className="tv-m-screen" style={{ display: 'flex', flexDirection: 'column' }}>
      {/* 顶栏 */}
      <div className="topbar">
        <button className="tb-btn" onClick={onBack} aria-label={t('mobile.tavern.chat.back_aria')}>
          <Icon name="chevron_left" size={18} />
        </button>
        {charName ? (
          <button className="tb-title" onClick={onOpenDrawer} style={{ cursor: 'pointer' }}>
            <strong style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis' }}>{charName}</strong>
            <span className="sub">
              <Icon name="chevron_down" size={11} style={{ opacity: 0.45 }} />
              {t('mobile.tavern.drawer.heading')}
            </span>
          </button>
        ) : (
          <div className="tb-title">
            <strong>{activeChat?.title || t('mobile.tavern.chat.fallback_title')}</strong>
          </div>
        )}
        <div style={{ display: 'flex', gap: 6 }}>
          {charName && (
            <button className="tb-btn" onClick={onOpenDrawer} aria-label={t('mobile.tavern.drawer.heading')}>
              <Icon name="cards" size={16} />
            </button>
          )}
          <button className="tb-btn" onClick={onOpenMenu} aria-label={t('mobile.tavern.chat.more_actions_aria')}>
            <Icon name="more" size={16} />
          </button>
        </div>
      </div>

      {/* 消息流 */}
      <div
        ref={threadRef}
        className="chat scroll"
        style={{ flex: 1, minHeight: 0, overflowY: 'auto', position: 'relative' }}
      >
        {total === 0 && !running && (
          <div className="muted-2" style={{ textAlign: 'center', padding: '60px 24px', fontSize: 13 }}>
            <Icon name="feedback" size={26} style={{ opacity: 0.3, display: 'block', margin: '0 auto 10px' }} />
            {t('mobile.tavern.chat.empty')}
          </div>
        )}

        {history.map((m, i) => {
          if (m.role === 'assistant') {
            const toolOps = m._toolOps || m.tool_ops;
            const isStreaming = !m.streaming_done && i === total - 1 && running;
            return (
              <React.Fragment key={`a-${i}`}>
                {Array.isArray(toolOps) && toolOps.length > 0 && <ToolCallBlock ops={toolOps} />}
                <div
                  className={`msg msg-gm${pressedIdx === i ? ' pressed' : ''}`}
                  onTouchStart={() => startPress(i)} onTouchEnd={cancelPress} onTouchMove={cancelPress}
              onContextMenu={(e) => { e.preventDefault(); openMsgSheet(i); }}
                >
                  <div className="msg-meta">
                    <span className="msg-tag">
                      {(character && character.name) || activeChat?.character_name || 'AI'}
                    </span>
                    {m.ts && <span className="msg-gts">{m.ts}</span>}
                  </div>
                  {(m._thinking || m.reasoning) && <ThinkingBlock text={m._thinking || m.reasoning} />}
                  <div className="msg-body">
                    <Paras text={m.content} />
                    {isStreaming && (
                      <span className="tv-m-cursor" aria-hidden="true" />
                    )}
                  </div>
                  <div className="msg-hint"><Icon name="menu" size={10} /> {t('mobile.tavern.chat.long_press_hint')}</div>
                </div>
              </React.Fragment>
            );
          }
          /* user */
          return (
            <div
              key={`u-${i}`}
              className={`msg msg-player${pressedIdx === i ? ' pressed' : ''}`}
              onTouchStart={() => startPress(i)} onTouchEnd={cancelPress} onTouchMove={cancelPress}
              onContextMenu={(e) => { e.preventDefault(); openMsgSheet(i); }}
            >
              <div className="msg-meta">
                <span className="msg-tag">{(persona && persona.name) || t('mobile.tavern.chat.you')}</span>
                {m.ts && <span className="msg-gts">{m.ts}</span>}
              </div>
              <div className="msg-body">{m.content}</div>
            </div>
          );
        })}

        {/* 等待气泡 */}
        {isWaiting && (
          <div className="msg msg-gm">
            <div className="waiting">
              <span className="d" />
              <span className="d" style={{ animationDelay: '0.2s' }} />
              <span className="d" style={{ animationDelay: '0.4s' }} />
              <span style={{ fontSize: 12, color: 'var(--muted)' }}>
                {t('mobile.tavern.chat.thinking', { name: charName })}
              </span>
            </div>
          </div>
        )}

        {/* 错误提示 */}
        {hasError && (
          <div className="msg" style={{ padding: '10px 14px', margin: '0 6px' }}>
            <div style={{
              borderRadius: 12, border: '1px solid rgba(200,103,93,0.4)',
              background: 'var(--danger-soft)', padding: '12px 14px',
              display: 'flex', gap: 10, alignItems: 'flex-start',
            }}>
              <Icon name="warn" size={15} style={{ color: 'var(--danger)', flex: 'none', marginTop: 1 }} />
              <div>
                <strong style={{ fontSize: 13 }}>{t('mobile.tavern.chat.error_title')}</strong>
                <p style={{ margin: '4px 0 10px', fontSize: 12, color: 'var(--text-quiet)', lineHeight: 1.6 }}>
                  {typeof hasError === 'string' && hasError ? hasError : t('mobile.tavern.chat.error_default')}
                </p>
                <button
                  className="sheet-btn primary"
                  style={{ height: 36, padding: '0 14px', width: 'auto', flex: 'none', fontSize: 13 }}
                  onClick={onRetry}
                >
                  {t('mobile.tavern.chat.retry')}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 回到最新按钮 */}
        {showJump && (
          <button
            onClick={jumpToBottom}
            style={{
              position: 'sticky', bottom: 8, left: '50%', transform: 'translateX(-50%)',
              display: 'flex', alignItems: 'center', gap: 5, width: 'fit-content',
              padding: '7px 14px', borderRadius: 999, fontSize: 12,
              background: 'var(--panel-3)', border: '1px solid var(--line-strong)',
              color: 'var(--text-quiet)', zIndex: 5,
            }}
          >
            <Icon name="chevron_down" size={13} /> {t('mobile.tavern.chat.scroll_to_bottom')}
          </button>
        )}
      </div>

      {/* Composer(统一组件 MobileComposer:酒馆带 + 附加功能=AI 帮回) */}
      <MobileComposer
        value={text}
        onChange={setText}
        onSubmit={submit}
        onStop={onStop}
        running={running}
        placeholder={t('mobile.tavern.chat.composer_placeholder', { name: charName })}
        sendAria={t('mobile.tavern.chat.send_aria')}
        stopAria={t('mobile.tavern.chat.stop_aria')}
        taRef={taRef}
        leading={onAiReply ? (
          <button className="c-plus" onClick={() => setPlusOpen(true)} aria-label={t('mobile.tavern.plus.aria')}>
            <Icon name="plus" size={20} />
          </button>
        ) : null}
      />

      {/* + 附加功能 sheet:AI 帮回(以玩家自己的角色生成一条回复,填入输入框) */}
      <BottomSheet show={plusOpen} onClose={() => setPlusOpen(false)} maxHeight="40%">
        <div className="sheet-title">{t('mobile.tavern.plus.title')}</div>
        <div className="sheet-list">
          <button
            className="sheet-item"
            disabled={aiReplyLoading || running}
            onClick={async () => {
              setPlusOpen(false);
              const reply = await (onAiReply && onAiReply());
              if (reply) { setText(reply); setTimeout(() => taRef.current?.focus(), 50); }
            }}
          >
            <span className="sheet-ico"><Icon name={aiReplyLoading ? 'refresh' : 'sparkle'} size={18} /></span>
            <span className="sheet-tx">
              <strong>{t('mobile.tavern.ai_reply.label')}</strong>
              <span>{t('mobile.tavern.ai_reply.sub')}</span>
            </span>
          </button>
        </div>
      </BottomSheet>

      {/* 长按消息 → 操作 sheet(与游戏台同一套交互;酒馆无存档/分支,故仅 复制 + 重新生成) */}
      <BottomSheet show={!!msgSheet} onClose={closeMsgSheet} maxHeight="50%">
        <div className="sheet-title">{msgSheet && history[msgSheet.idx]?.role === 'assistant' ? t('mobile.tavern.msg_sheet.assistant_title') : t('mobile.tavern.msg_sheet.user_title')}</div>
        <div className="sheet-list">
          <button className="sheet-item" onClick={() => { const txt = (msgSheet && history[msgSheet.idx]?.content) || ''; closeMsgSheet(); copy(txt); }}>
            <span className="sheet-ico"><Icon name="copy" size={18} /></span>
            <span className="sheet-tx"><strong>{t('mobile.tavern.msg_sheet.copy')}</strong><span>{t('mobile.tavern.msg_sheet.copy_sub')}</span></span>
          </button>
          {msgSheet && msgSheet.idx === lastAssistantIdx && !running && (
            <button className="sheet-item" onClick={() => { closeMsgSheet(); onRetry(); }}>
              <span className="sheet-ico"><Icon name="refresh" size={18} /></span>
              <span className="sheet-tx"><strong>{t('mobile.tavern.msg_sheet.regenerate')}</strong><span>{t('mobile.tavern.msg_sheet.regenerate_sub')}</span></span>
            </button>
          )}
        </div>
      </BottomSheet>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════
 *  MobileTavern — 顶层组件
 * ══════════════════════════════════════════════════════════════════ */
export function MobileTavern({ nav }) {
  const { t } = useTranslation();
  /* ── 列表状态 ──────────────────────────────────────────────────── */
  const [chats, setChats] = useState([]);
  const [archivedChats, setArchivedChats] = useState([]);
  const [loadingList, setLoadingList] = useState(true);

  /* ── 当前对话状态 ──────────────────────────────────────────────── */
  const [activeId, setActiveId] = useState(null);
  const [activeChat, setActiveChat] = useState(null);
  const [character, setCharacter] = useState(null);
  const [persona, setPersona] = useState(null);
  const [history, setHistory] = useState([]);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [immersive, setImmersive] = useState(false);
  const [aiReplyLoading, setAiReplyLoading] = useState(false);

  /* ── 流式发送状态 ──────────────────────────────────────────────── */
  const [text, setText] = useState('');
  const [running, setRunning] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [lastPlayerText, setLastPlayerText] = useState('');

  /* ── 视图 ──────────────────────────────────────────────────────── */
  const [view, setView] = useState('list'); // 'list' | 'chat'

  /* ── Sheet/Drawer 开关状态 ─────────────────────────────────────── */
  const [importOpen, setImportOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuTarget, setMenuTarget] = useState(null); // 菜单操作的 chat 对象
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState(null);
  const [syspromptOpen, setSyspromptOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);

  /* ── Toast ─────────────────────────────────────────────────────── */
  const [toast, setToast] = useState(null);
  const toastTimer = useRef(null);
  const fireToast = useCallback((msg, kind = 'ok') => {
    setToast({ msg, kind });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 2000);
  }, []);

  /* ── 收口的酒馆 SSE 状态机(runRef + startRun/stopRun 在 hook 内,折叠语义见
   *    lib/tavern-chat-run.js;移动端 toast 走自有 fireToast)──────────────── */
  const { runRef, startRun: runChat, stopRun } = useTavernChatRun({ setRunning });

  /* ── reloadList ───────────────────────────────────────────────── */
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

  /* ── applyState(收口到 applyTavernState 核心三段 + 移动端叠加 setSystemPrompt)──── */
  const applyState = useCallback((data) => {
    applyTavernState(data, {
      setCharacter, setPersona, setHistory, setActiveChat, setSystemPrompt, setImmersive,
    });
  }, []);

  /* ── openChat(照搬 tavern-app.jsx)───────────────────────────── */
  const openChat = useCallback(async (chat) => {
    if (!chat || !chat.id) return;
    const rc = runRef.current;
    if (rc.sse) { try { rc.sse.stop('switch'); } catch (_) {} rc.sse = null; }
    setRunning(false); setHasError(false); setHistory([]);
    setActiveId(chat.id);
    setActiveChat(chat);
    try {
      await window.api.tavern.activate(chat.id);
      const data = await window.api.game.state();
      applyState(data);
      setView('chat');
    } catch (e) {
      fireToast(t('mobile.tavern.toast.open_fail'), 'danger');
    }
  }, [applyState, fireToast, t]);

  /* ── 首次进入自动打开最近对话 ───────────────────────────────────── */
  useEffect(() => { reloadList(); }, [reloadList]);
  const _autoOpened = useRef(false);
  useEffect(() => {
    if (_autoOpened.current || activeId != null || loadingList || chats.length === 0) return;
    // 直接进对话:进入酒馆自动打开最近一条会话(对齐电脑端"酒馆直接进对话";
    // 会话历史由后端自动维护,无"存档"概念)。空列表时停在 hero 引导新建。
    _autoOpened.current = true;
    openChat(chats[0]);
  }, [loadingList, chats, activeId, openChat]);

  /* ── 卸载停流 ────────────────────────────────────────────────── */
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => { abortRun(runRef.current, 'unmount'); }, []);

  /* ── openSaveId ──────────────────────────────────────────────── */
  const openSaveId = useCallback(async (saveId, fallbackName) => {
    await reloadList();
    await openChat({ id: saveId, title: fallbackName || t('mobile.tavern.chat.default_title', { id: saveId }), character_name: fallbackName || '' });
  }, [reloadList, openChat, t]);

  /* ── 文件导入(角色卡 + JSONL)──────────────────────────────────── */
  const onPickCardFile = useCallback(async (file) => {
    if (!file) return;
    if (!/\.(png|json|webp)$/i.test(file.name || '')) {
      fireToast(t('mobile.tavern.toast.card_format_warn'), 'warn');
      return;
    }
    try {
      const r = await window.api.tavern.importCharacter(file);
      if (r && r.ok === false) throw new Error(r.error || t('mobile.tavern.toast.import_fail'));
      await openSaveId(r.save_id, r.character_name);
      fireToast(t('mobile.tavern.toast.card_imported', { name: r.character_name || t('mobile.tavern.chat.default_char_name') }), 'ok');
    } catch (e) {
      fireToast(t('mobile.tavern.toast.import_fail') + (e?.message ? ': ' + e.message : ''), 'danger');
    }
  }, [openSaveId, fireToast, t]);

  /* ── 空白开始(直接开聊,不预设角色卡)──────────────────────────────
   * 后端 create_tavern_save 支持 character_card_id=None(空起手对话,由 agent 即兴扮演);
   * 桌面端「新建对话」也是 tavern.create({}) → r.save.id。此前移动端 ImportSheet 只给「上传
   * 角色卡 / 导入记录」两个入口 → 用户被强制上传 json 卡才能开聊(反馈:新酒馆聊天被拦住)。 */
  const onCreateBlank = useCallback(async () => {
    setImportOpen(false);
    try {
      const r = await window.api.tavern.create({});
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('mobile.tavern.toast.create_fail'));
      const newId = (r && r.save && r.save.id) || r?.save_id || r?.id;
      if (!newId) throw new Error(t('mobile.tavern.toast.create_fail'));
      await openSaveId(newId, t('mobile.tavern.chat.default_char_name'));
    } catch (e) {
      fireToast(t('mobile.tavern.toast.create_fail') + (e?.message ? ': ' + e.message : ''), 'danger');
    }
  }, [openSaveId, fireToast, t]);

  const onPickJsonlFile = useCallback(async (file) => {
    if (!file) return;
    try {
      const r = await window.api.tavern.importJsonl(file);
      if (r && r.ok === false) throw new Error(r.error || t('mobile.tavern.toast.import_fail'));
      await openSaveId(r.save_id, r.title || t('mobile.tavern.toast.imported_chat_title'));
      fireToast(t('mobile.tavern.toast.jsonl_imported', { count: r.commits_imported || 0 }), 'ok');
    } catch (e) {
      fireToast(t('mobile.tavern.toast.import_fail') + (e?.message ? ': ' + e.message : ''), 'danger');
    }
  }, [openSaveId, fireToast, t]);

  /* ── stopRun:收口到 useTavernChatRun(移动端无秒表,与 hook 默认一致)──── */
  // stopRun 由 hook 提供。

  /* ── startRun(收口到 useTavernChatRun;折叠语义见 lib/tavern-chat-run.js)──── */
  // 移动端差异:toast 走自有 fireToast(只取 kind,不显示 detail);restoreFailedDraft
  // 不回填输入框(setText:null);空回复文案不带「已恢复你的输入」;tool-op = inline 无 anchor。
  const startRun = useCallback(async (playerText) => {
    runChat({
      saveId: activeId, model: undefined, playerText, applyState,
      ts: tvNow,   // 移动端用自有 tvNow()(零填充 HH:MM),不走 __fmt.nowHHMM 的 locale slice。
      setHistory, setRunning, setText: null, setHasError, setLastPlayerText,
      // 移动端 toast:fireToast(msg, kind);detail 不显示(逐字保留旧行为),
      // 仅 idle 旧实现是合并串「生成停滞,120 秒无响应」→ 用 code 还原。
      toast: (title, o) => {
        const kind = o && o.kind;
        if (o && o.code === 'idle') { fireToast(t('mobile.tavern.toast.idle_stall'), 'warn'); return; }
        fireToast(title, kind);
      },
      reloadList,
      // 空回复文案:移动端不带「已恢复你的输入」。
      doneEmptyMsg: (interrupted) => (interrupted ? t('mobile.tavern.toast.interrupted') : t('mobile.tavern.toast.no_reply')),
      // onClose 文案:移动端更短。
      closeMsg: t('mobile.tavern.toast.connection_closed'),
      // tool-op:inline 模型(无 anchor)。
      onToolCall: toolCallInline,
      onToolResult: toolResultInline,
    });
  }, [activeId, applyState, reloadList, fireToast, runChat, t]);

  /* ── onRetry(照搬 tavern-app.jsx)────────────────────────────── */
  const onRetry = useCallback(() => {
    if (running) return;
    let t2 = (lastPlayerText && lastPlayerText.trim()) || '';
    if (!t2) {
      for (let i = history.length - 1; i >= 0; i--) {
        if (history[i]?.role === 'user' && (history[i].content || '').trim()) { t2 = history[i].content.trim(); break; }
      }
    }
    if (!t2) { fireToast(t('mobile.tavern.toast.no_retry_input'), 'warn'); return; }
    setHasError(false);
    setHistory(h => {
      const out = [...h];
      while (out.length && out[out.length - 1].role === 'assistant' && !(out[out.length - 1].content || '').trim()) out.pop();
      if (out.length && out[out.length - 1].role === 'user' && (out[out.length - 1].content || '').trim() === t2) out.pop();
      return out;
    });
    startRun(t2);
  }, [running, lastPlayerText, history, startRun, fireToast, t]);

  /* ── rail 操作 ───────────────────────────────────────────────── */
  const doRename = useCallback(async (chat, title) => {
    if (title == null) { setRenameTarget(chat); setRenameOpen(true); return; }
    try {
      await window.api.tavern.rename(chat.id, title);
      fireToast(t('mobile.tavern.toast.renamed'), 'ok');
      reloadList();
      if (String(chat.id) === String(activeId)) setActiveChat(p => ({ ...(p || {}), title }));
    } catch (e) { fireToast(t('mobile.tavern.toast.rename_fail'), 'danger'); }
    setRenameOpen(false);
  }, [reloadList, activeId, fireToast, t]);

  const doArchive = useCallback(async (chat, archived) => {
    try {
      await window.api.tavern.archive(chat.id, archived);
      fireToast(archived ? t('mobile.tavern.toast.archived') : t('mobile.tavern.toast.unarchived'), 'ok');
      reloadList();
    } catch (e) { fireToast(t('mobile.tavern.toast.archive_fail'), 'danger'); }
  }, [reloadList, fireToast, t]);

  const doDelete = useCallback(async (chat) => {
    setDeleteOpen(false); setDeleteTarget(null);
    try {
      await window.api.tavern.remove(chat.id);
      fireToast(t('mobile.tavern.toast.deleted'), 'ok');
      if (String(chat.id) === String(activeId)) {
        setActiveId(null); setActiveChat(null); setHistory([]); setCharacter(null); setPersona(null);
        setView('list');
      }
      reloadList();
    } catch (e) { fireToast(t('mobile.tavern.toast.delete_fail'), 'danger'); }
  }, [reloadList, activeId, fireToast, t]);

  const doAutotitle = useCallback(async (chat) => {
    try {
      await window.api.tavern.autotitle(chat.id);
      fireToast(t('mobile.tavern.toast.autotitled'), 'ok');
      reloadList();
      if (String(chat.id) === String(activeId)) {
        const data = await window.api.game.state();
        applyState(data);
      }
    } catch (e) { fireToast(t('mobile.tavern.toast.autotitle_fail'), 'danger'); }
  }, [reloadList, activeId, applyState, fireToast, t]);

  const onSaveSystemPrompt = useCallback(async (sp) => {
    if (!activeId) return;
    try {
      await window.api.tavern.setSystemPrompt(activeId, sp);
      setSystemPrompt(sp || '');
      fireToast(t('mobile.tavern.toast.sysprompt_saved'), 'ok');
    } catch (e) { fireToast(t('mobile.tavern.toast.save_fail'), 'danger'); throw e; }
  }, [activeId, fireToast, t]);

  const onSavePersona = useCallback(async (payload) => {
    try {
      const saved = await window.api.cards.myUpsert(payload);
      fireToast(t('mobile.tavern.toast.persona_saved'), 'ok');
      try { const d = await window.api.game.state(); applyState(d); } catch (_) {}
      return saved;
    } catch (e) {
      fireToast(t('mobile.tavern.toast.save_fail'), 'danger');
      throw e;
    }
  }, [applyState, fireToast, t]);

  /* ── 沉浸式拟人模式开关(持久写 state.tavern.immersive,确定性注入 system prompt)── */
  const onToggleImmersive = useCallback(async (enabled) => {
    if (!activeId) return;
    setImmersive(enabled); // 乐观更新
    try {
      await window.api.tavern.setImmersive(activeId, enabled);
      fireToast(enabled ? t('mobile.tavern.immersive.on_toast') : t('mobile.tavern.immersive.off_toast'), 'ok');
    } catch (e) {
      setImmersive(!enabled); // 回滚
      fireToast(t('mobile.tavern.toast.save_fail'), 'danger');
    }
  }, [activeId, fireToast, t]);

  /* ── AI 帮回:以玩家自己的角色生成一条回复 → 填入输入框(不自动发送)── */
  const onAiReply = useCallback(async () => {
    if (!activeId || aiReplyLoading) return null;
    setAiReplyLoading(true);
    try {
      const r = await window.api.tavern.aiReply(activeId);
      const reply = (r && r.reply) || '';
      if (!reply) { fireToast(t('mobile.tavern.ai_reply.empty'), 'warn'); return null; }
      return reply;
    } catch (e) {
      fireToast(t('mobile.tavern.ai_reply.fail'), 'danger');
      return null;
    } finally {
      setAiReplyLoading(false);
    }
  }, [activeId, aiReplyLoading, fireToast, t]);

  /* ── 列表页打开菜单时,记录操作 chat ─────────────────────────── */
  const openMenu = useCallback((chat) => {
    setMenuTarget(chat);
    setMenuOpen(true);
  }, []);

  /* 对话屏顶栏「更多」── 默认操作当前 activeChat */
  const openChatMenu = useCallback(() => {
    if (!activeChat) return;
    setMenuTarget(activeChat);
    setMenuOpen(true);
  }, [activeChat]);

  const exportUrl = activeId != null ? window.api.tavern.exportJsonl(activeId) : null;

  /* ─────────────────────────────────────────────────────────────── */
  return (
    <div style={{ position: 'absolute', inset: 0, background: 'var(--bg)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* ── 两屏切换(层叠滑动感) ── */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {/* 列表屏 */}
        <div style={{
          position: 'absolute', inset: 0,
          transform: view === 'chat' ? 'translateX(-20%)' : 'translateX(0)',
          opacity: view === 'chat' ? 0.5 : 1,
          transition: 'transform 0.35s var(--ease), opacity 0.35s',
          pointerEvents: view === 'chat' ? 'none' : 'auto',
          zIndex: view === 'list' ? 2 : 1,
        }}>
          <ListView
            chats={chats}
            archivedChats={archivedChats}
            activeId={activeId}
            loading={loadingList}
            onExit={() => nav.switchTab('home')}
            onOpen={openChat}
            onMenu={openMenu}
            onNew={() => setImportOpen(true)}
            onQuickStart={onCreateBlank}
          />
        </div>

        {/* 对话屏 */}
        {view === 'chat' && (
          <div style={{
            position: 'absolute', inset: 0,
            transform: 'translateX(0)',
            transition: 'transform 0.35s var(--ease)',
            zIndex: 2,
          }}>
            <ChatView
              activeChat={activeChat}
              character={character}
              persona={persona}
              history={history}
              running={running}
              hasError={hasError}
              systemPrompt={systemPrompt}
              onBack={() => { setView('list'); reloadList(); }}
              onSend={startRun}
              onStop={stopRun}
              onRetry={onRetry}
              onOpenDrawer={() => setDrawerOpen(true)}
              onOpenMenu={openChatMenu}
              onToast={fireToast}
              onAiReply={onAiReply}
              aiReplyLoading={aiReplyLoading}
            />
          </div>
        )}
      </div>

      {/* ── 双卡抽屉 ── */}
      <TwoCardDrawer
        open={drawerOpen}
        character={character}
        persona={persona}
        systemPrompt={systemPrompt}
        immersive={immersive}
        onToggleImmersive={onToggleImmersive}
        onClose={() => setDrawerOpen(false)}
        onSavePersona={onSavePersona}
        onSaveSystemPrompt={onSaveSystemPrompt}
      />

      {/* ── 导入 sheet ── */}
      <ImportSheet
        show={importOpen}
        onClose={() => setImportOpen(false)}
        onPickFile={onPickCardFile}
        onJsonlFile={onPickJsonlFile}
        onCreateBlank={onCreateBlank}
      />

      {/* ── 聊天菜单 sheet ── */}
      <ChatMenuSheet
        show={menuOpen}
        chat={menuTarget}
        onClose={() => { setMenuOpen(false); setMenuTarget(null); }}
        onRename={chat => { setMenuOpen(false); doRename(chat); }}
        onArchive={(chat, archived) => { setMenuOpen(false); setMenuTarget(null); doArchive(chat, archived); }}
        onDelete={chat => { setMenuOpen(false); setDeleteTarget(chat); setDeleteOpen(true); }}
        onAutotitle={chat => { setMenuTarget(null); doAutotitle(chat); }}
        onSystemPrompt={chat => { setMenuOpen(false); setMenuTarget(chat); setSyspromptOpen(true); }}
        onExport={menuTarget && menuTarget.id ? window.api.tavern.exportJsonl(menuTarget.id) : null}
      />

      {/* ── 删除确认 sheet ── */}
      <DeleteConfirmSheet
        show={deleteOpen}
        chat={deleteTarget}
        onClose={() => { setDeleteOpen(false); setDeleteTarget(null); }}
        onConfirm={() => { if (deleteTarget) doDelete(deleteTarget); }}
      />

      {/* ── 重命名 sheet ── */}
      <RenameSheet
        show={renameOpen}
        chat={renameTarget}
        onClose={() => { setRenameOpen(false); setRenameTarget(null); }}
        onConfirm={(chat, title) => { setRenameOpen(false); setRenameTarget(null); doRename(chat, title); }}
      />

      {/* ── 系统提示词 sheet ── */}
      <SystemPromptSheet
        show={syspromptOpen}
        chat={menuTarget}
        systemPrompt={systemPrompt}
        onClose={() => { setSyspromptOpen(false); }}
        onSave={onSaveSystemPrompt}
      />

      {/* ── Toast ── */}
      {toast && <MobileToast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}

export default MobileTavern;

/*
 * ── neededCss (补充到 mobile.css,带 .m-root 前缀)─────────────────
 *
 * .m-root .tv-m-screen {
 *   position: absolute; inset: 0;
 *   display: flex; flex-direction: column;
 *   background: var(--bg);
 * }
 *
 * .m-root .tv-m-empty {
 *   flex: 1; display: grid; place-items: center;
 *   font-size: 13px; padding: 40px;
 * }
 *
 * ─ 列表 hero ─
 * .m-root .tv-m-hero {
 *   flex: 1; display: flex; align-items: center; justify-content: center;
 *   padding: 32px 24px;
 * }
 * .m-root .tv-m-hero .tv-m-hero-mark {
 *   font-size: 40px; color: var(--accent); text-align: center; margin-bottom: 16px;
 * }
 * .m-root .tv-m-hero-title {
 *   margin: 0 0 8px; font-size: 24px; font-weight: 600;
 *   letter-spacing: 0.02em; text-align: center; color: var(--text);
 * }
 * .m-root .tv-m-hero-sub {
 *   margin: 0 0 20px; font-size: 13px; text-align: center; line-height: 1.6;
 *   color: var(--muted);
 * }
 * .m-root .tv-m-hero-drop {
 *   display: flex; flex-direction: column; align-items: center; gap: 6px;
 *   width: 100%; padding: 22px 16px; border-radius: 18px;
 *   border: 1.5px dashed var(--accent-edge); background: var(--accent-soft);
 *   color: var(--accent); cursor: pointer;
 *   transition: background .15s, transform .1s;
 * }
 * .m-root .tv-m-hero-drop:active { transform: scale(0.98); background: var(--panel-3); }
 * .m-root .tv-m-hero-drop-ic { display: grid; place-items: center; margin-bottom: 4px; }
 * .m-root .tv-m-hero-drop-main { font-size: 14.5px; font-weight: 500; color: var(--text); }
 * .m-root .tv-m-hero-drop-sub { font-size: 11.5px; color: var(--muted); }
 *
 * ─ 新对话按钮 ─
 * .m-root .tv-m-newchat {
 *   display: flex; align-items: center; gap: 14px;
 *   padding: 14px 16px; margin: 4px 12px 4px;
 *   border-radius: 14px; border: 1px dashed var(--accent-edge);
 *   background: var(--accent-soft); color: var(--accent);
 *   text-align: left; width: calc(100% - 24px);
 *   transition: background .14s, transform .1s;
 * }
 * .m-root .tv-m-newchat:active { transform: scale(0.98); }
 * .m-root .tv-m-newchat-ic { flex: none; width: 36px; height: 36px; display: grid; place-items: center; }
 * .m-root .tv-m-newchat-tx { flex: 1; min-width: 0; display: grid; gap: 2px; }
 * .m-root .tv-m-newchat-tx strong { font-size: 14px; color: var(--text); }
 * .m-root .tv-m-newchat-tx span { font-size: 11.5px; color: var(--muted-2); }
 *
 * ─ 对话列表项 ─
 * .m-root .tv-m-chat-item {
 *   display: flex; align-items: center; gap: 12px;
 *   padding: 12px 16px; width: 100%; text-align: left;
 *   border-bottom: 1px solid var(--line-soft);
 *   background: transparent;
 *   transition: background .12s;
 * }
 * .m-root .tv-m-chat-item:active, .m-root .tv-m-chat-item.active { background: var(--accent-soft); }
 * .m-root .tv-m-chat-av {
 *   flex: none; width: 42px; height: 42px; border-radius: 13px;
 *   display: grid; place-items: center;
 *   background: var(--panel-3); border: 1px solid var(--line);
 *   font-size: 18px; font-weight: 600; color: var(--text);
 * }
 * .m-root .tv-m-chat-main { flex: 1; min-width: 0; display: grid; gap: 3px; }
 * .m-root .tv-m-chat-row { display: flex; align-items: center; gap: 8px; }
 * .m-root .tv-m-chat-title { flex: 1; font-size: 14.5px; font-weight: 500; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
 * .m-root .tv-m-chat-time { font-size: 10.5px; white-space: nowrap; flex: none; }
 * .m-root .tv-m-chat-snippet { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
 * .m-root .tv-m-chat-menu-btn { flex: none; width: 36px; height: 36px; display: grid; place-items: center; border-radius: 9px; color: var(--muted-2); }
 * .m-root .tv-m-chat-menu-btn:active { background: var(--panel-2); color: var(--text); }
 *
 * ─ 已归档切换 ─
 * .m-root .tv-m-archived-section { padding: 4px 0; }
 * .m-root .tv-m-archived-toggle {
 *   display: inline-flex; align-items: center; gap: 6px;
 *   padding: 8px 16px; font-size: 12px; color: var(--muted);
 *   transition: color .12s;
 * }
 * .m-root .tv-m-archived-toggle:active { color: var(--text); }
 *
 * ─ 工具调用块 ─
 * .m-root .tv-m-tools { padding: 4px 14px; }
 * .m-root .tv-m-tools-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--muted-2); padding: 4px 0; }
 * .m-root .tv-m-tools-summary { color: var(--muted-2); }
 * .m-root .tv-m-tools-detail { margin-top: 6px; display: grid; gap: 6px; }
 * .m-root .tv-m-tool-item { background: var(--bg-deep); border: 1px solid var(--line-soft); border-radius: 10px; padding: 9px 11px; display: grid; gap: 4px; }
 * .m-root .tv-m-tool-name { display: flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 500; color: var(--text); font-family: var(--font-mono); }
 * .m-root .tv-m-tool-dot { width: 7px; height: 7px; border-radius: 999px; flex: none; }
 * .m-root .tv-m-tool-kv { margin: 0; font: 11px var(--font-mono); color: var(--muted); white-space: pre-wrap; overflow-x: auto; }
 * .m-root .tv-m-tool-k { color: var(--accent); }
 *
 * ─ 思考流块 ─
 * .m-root .tv-m-thinking { padding: 4px 14px 8px; }
 * .m-root .tv-m-thinking-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--muted-2); padding: 4px 0; }
 * .m-root .tv-m-thinking-label { color: var(--info); }
 * .m-root .tv-m-thinking-body { font: 11.5px/1.65 var(--font-mono); color: var(--muted); padding: 8px 10px; background: var(--bg-deep); border-radius: 8px; border: 1px solid var(--line-soft); white-space: pre-wrap; overflow-x: auto; }
 *
 * ─ 流式光标 ─
 * @keyframes tv-m-blink { 50% { opacity: 0; } }
 * .m-root .tv-m-cursor { display: inline-block; width: 2px; height: 1.1em; background: var(--accent); border-radius: 1px; vertical-align: text-bottom; margin-left: 2px; animation: tv-m-blink 0.9s steps(1) infinite; }
 *
 * ─ 双卡抽屉 tabs ─
 * .m-root .tv-m-drawer-tabs { display: flex; gap: 4px; padding: 10px 14px 0; border-bottom: 1px solid var(--line-soft); }
 * .m-root .tv-m-drawer-tab { flex: 1; display: flex; align-items: center; justify-content: center; gap: 5px; padding: 8px 4px 10px; font-size: 12.5px; color: var(--muted); border-bottom: 2px solid transparent; transition: color .14s, border-color .14s; }
 * .m-root .tv-m-drawer-tab.active { color: var(--accent); border-color: var(--accent); }
 *
 * ─ 导入按钮 ─
 * .m-root .tv-m-import-btn {
 *   display: flex; align-items: center; gap: 14px;
 *   padding: 14px 12px; border-radius: 14px;
 *   border: 1px solid var(--line-soft); background: var(--panel);
 *   color: var(--text); text-align: left; width: 100%;
 *   transition: background .13s, transform .1s;
 * }
 * .m-root .tv-m-import-btn:active { transform: scale(0.98); background: var(--panel-2); }
 * .m-root .tv-m-import-ic { flex: none; width: 42px; height: 42px; display: grid; place-items: center; border-radius: 12px; background: var(--accent-soft); border: 1px solid var(--accent-edge); color: var(--accent); }
 * .m-root .tv-m-import-tx { flex: 1; min-width: 0; display: grid; gap: 3px; }
 * .m-root .tv-m-import-tx strong { font-size: 14.5px; color: var(--text); }
 * .m-root .tv-m-import-tx span { font-size: 12px; color: var(--muted-2); }
 * .m-root .tv-m-import-fmt { flex: none; font: 600 10px var(--font-mono); text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted-2); padding: 4px 8px; border-radius: 6px; background: var(--bg-deep); border: 1px solid var(--line-soft); }
 *
 * ─ 通用输入框 ─
 * .m-root .tv-m-input { width: 100%; padding: 11px 13px; border-radius: 12px; border: 1px solid var(--line); background: var(--bg-deep); color: var(--text); font: 14.5px/1.6 var(--font-serif); outline: none; }
 * .m-root .tv-m-input:focus { border-color: var(--accent-edge); box-shadow: 0 0 0 3px rgba(201,100,66,0.07); }
 * .m-root textarea.tv-m-input { resize: none; }
 */
