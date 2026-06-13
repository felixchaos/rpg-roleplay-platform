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
import { Icon } from '../icons.jsx';
// 不复用电脑端 cards.jsx 的 UI 组件 —— 移动原生重写卡片读视图/persona 表单 + 纯数据 helper。
const _CARD_FIELDS = [
  ['name', '名称'], ['identity', '身份'], ['background', '背景'], ['appearance', '外貌'],
  ['personality', '性格'], ['language_style', '语言风格'], ['current_status', '当前状态'],
  ['secret', '秘密'], ['sample_dialogue', '对话示例'],
];
const _CARD_MULTILINE = new Set(['background', 'appearance', 'personality', 'current_status', 'secret', 'sample_dialogue']);
function cardFormInit(c) {
  c = c || {};
  const o = {};
  for (const [k] of _CARD_FIELDS) o[k] = c[k] || (k === 'identity' ? (c.role || '') : '');
  o.tags = Array.isArray(c.tags) ? c.tags.join(', ') : (c.tags || '');
  o.aliases = Array.isArray(c.aliases) ? c.aliases.join(', ') : (c.aliases || '');
  return o;
}
function cardFormPayload(f, base) {
  const splitList = (s) => String(s || '').split(',').map((x) => x.trim()).filter(Boolean);
  const o = { ...(base || {}) };
  for (const [k] of _CARD_FIELDS) o[k] = f[k] || '';
  o.tags = splitList(f.tags); o.aliases = splitList(f.aliases);
  return o;
}
function CardReadout({ card }) {
  if (!card) return null;
  return (
    <div style={{ padding: '14px 0', display: 'flex', flexDirection: 'column', gap: 12 }}>
      {_CARD_FIELDS.map(([k, l]) => {
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
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {_CARD_FIELDS.map(([k, l]) => (
        <label key={k} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>{l}</span>
          {_CARD_MULTILINE.has(k)
            ? <textarea className="tv-m-input" rows={3} value={form[k] || ''} onChange={(e) => u(k, e.target.value)} />
            : <input className="tv-m-input" value={form[k] || ''} onChange={(e) => u(k, e.target.value)} />}
        </label>
      ))}
      <label style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        <span style={{ fontSize: 12, color: 'var(--muted)' }}>标签(逗号分隔)</span>
        <input className="tv-m-input" value={form.tags || ''} onChange={(e) => u('tags', e.target.value)} />
      </label>
    </div>
  );
}

/* ─── 工具函数 ─────────────────────────────────────────────────────── */
function relTime(ts) {
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
  const [open, setOpen] = useState(false);
  if (!Array.isArray(ops) || ops.length === 0) return null;
  const n = ops.length;
  const firstName = (ops[0] && ops[0].tool) || '工具';
  const summary = n === 1 ? `调用工具 · ${firstName}` : `调用 ${n} 个工具 · ${firstName}…`;
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
                {(op && op.tool) || '工具'}
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
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="tv-m-thinking">
      <button className="tv-m-thinking-toggle" onClick={() => setOpen(v => !v)}>
        <Icon name={open ? 'chevron_down' : 'chevron_right'} size={11} />
        <span className="tv-m-thinking-label">思考过程</span>
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
  if (!chat) return null;
  const archived = !!chat.archived;
  return (
    <BottomSheet show={show} onClose={onClose}>
      <div className="sheet-title">{chat.title || chat.character_name || `对话 #${chat.id}`}</div>
      <div className="sheet-sub">{archived ? '已归档对话' : '对话操作'}</div>
      <div className="sheet-list">
        <button className="sheet-item" onClick={() => { onClose(); onAutotitle(chat); }}>
          <span className="sheet-ico"><Icon name="spark" size={17} /></span>
          <span className="sheet-tx"><strong>自动命名</strong><span>按对话内容生成标题</span></span>
        </button>
        <button className="sheet-item" onClick={() => { onClose(); onSystemPrompt(chat); }}>
          <span className="sheet-ico"><Icon name="braces" size={17} /></span>
          <span className="sheet-tx"><strong>系统提示词</strong><span>编辑本对话的 system prompt</span></span>
        </button>
        <button className="sheet-item" onClick={() => { onClose(); onRename(chat); }}>
          <span className="sheet-ico"><Icon name="edit" size={17} /></span>
          <span className="sheet-tx"><strong>重命名</strong><span>给这段对话改个标题</span></span>
        </button>
        {onExport && (
          <a className="sheet-item" href={onExport} target="_blank" rel="noopener" onClick={onClose}>
            <span className="sheet-ico"><Icon name="download" size={17} /></span>
            <span className="sheet-tx"><strong>导出 JSONL</strong><span>下载 SillyTavern 格式聊天记录</span></span>
          </a>
        )}
        <button className="sheet-item" onClick={() => { onClose(); onArchive(chat, !archived); }}>
          <span className="sheet-ico"><Icon name="folder" size={17} /></span>
          <span className="sheet-tx">
            <strong>{archived ? '取消归档' : '归档'}</strong>
            <span>{archived ? '移回对话列表' : '收进已归档,稍后再聊'}</span>
          </span>
        </button>
        <button className="sheet-item danger" onClick={() => { onClose(); onDelete(chat); }}>
          <span className="sheet-ico"><Icon name="trash" size={17} /></span>
          <span className="sheet-tx"><strong>删除对话</strong><span>永久删除全部聊天记录</span></span>
        </button>
      </div>
    </BottomSheet>
  );
}

/* ─── 删除确认 sheet ─────────────────────────────────────────────── */
function DeleteConfirmSheet({ show, chat, onClose, onConfirm }) {
  if (!chat) return null;
  const title = chat.title || chat.character_name || `对话 #${chat.id}`;
  return (
    <BottomSheet show={show} onClose={onClose}>
      <div className="sheet-title">删除对话？</div>
      <div className="confirm-preview">「{title}」</div>
      <div className="confirm-note">这将永久删除该对话及其全部聊天记录，<strong>无法恢复</strong>。</div>
      <div className="sheet-actions">
        <button className="sheet-btn" onClick={onClose}>取消</button>
        <button className="sheet-btn danger" onClick={onConfirm}>
          <Icon name="trash" size={15} /> 删除
        </button>
      </div>
    </BottomSheet>
  );
}

/* ─── 重命名 sheet ───────────────────────────────────────────────── */
function RenameSheet({ show, chat, onClose, onConfirm }) {
  const [val, setVal] = useState('');
  useEffect(() => { if (chat) setVal(chat.title || chat.character_name || ''); }, [chat]);
  if (!chat) return null;
  const commit = () => { const t = val.trim(); if (t) onConfirm(chat, t); };
  return (
    <BottomSheet show={show} onClose={onClose}>
      <div className="sheet-title">重命名对话</div>
      <div style={{ padding: '4px 4px 12px' }}>
        <input
          className="tv-m-input"
          autoFocus
          value={val}
          onChange={e => setVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && val.trim()) { e.preventDefault(); commit(); } }}
          placeholder="对话标题"
          maxLength={200}
        />
      </div>
      <div className="sheet-actions">
        <button className="sheet-btn" onClick={onClose}>取消</button>
        <button className="sheet-btn primary" onClick={commit} disabled={!val.trim()}>
          <Icon name="check" size={14} /> 保存
        </button>
      </div>
    </BottomSheet>
  );
}

/* ─── 系统提示词编辑 sheet ───────────────────────────────────────── */
function SystemPromptSheet({ show, chat, systemPrompt, onClose, onSave }) {
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
      <div className="sheet-title">系统提示词</div>
      <div className="sheet-sub">仅影响本对话的 AI 行为 / 人设</div>
      <div style={{ padding: '4px 4px 10px' }}>
        <textarea
          className="tv-m-input"
          rows={10}
          value={val}
          onChange={e => setVal(e.target.value)}
          placeholder="输入系统提示词(人设 / 行为约束 / 越狱指令等)…"
          style={{ resize: 'none', minHeight: 180 }}
        />
      </div>
      <div className="sheet-actions">
        <button className="sheet-btn" onClick={onClose} disabled={saving}>取消</button>
        <button className="sheet-btn primary" onClick={doSave} disabled={saving}>
          <Icon name="check" size={14} /> {saving ? '保存中…' : '保存'}
        </button>
      </div>
    </BottomSheet>
  );
}

/* ─── 双卡抽屉(右侧滑入 drawer):AI 角色卡 + 我的 persona + 系统提示 ── */
function TwoCardDrawer({ open, character, persona, systemPrompt, onClose, onSavePersona, onSaveSystemPrompt }) {
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

  return (
    <>
      <div className={`scrim${open ? ' show' : ''}`} onClick={onClose} />
      <aside className={`drawer drawer-right${open ? ' open' : ''}`}>
        <div className="drawer-head">
          <button className="drawer-x" onClick={onClose}><Icon name="close" size={15} /></button>
          <h2 style={{ fontFamily: 'var(--font-serif)', fontSize: 16 }}>角色 / Persona</h2>
        </div>
        {/* 三 Tab 分段 */}
        <div className="tv-m-drawer-tabs">
          <button
            className={`tv-m-drawer-tab${tab === 'character' ? ' active' : ''}`}
            onClick={() => setTab('character')}
          >
            <Icon name="cards" size={13} /> AI 角色
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
            <Icon name="braces" size={13} /> 系统提示
          </button>
        </div>

        <div className="drawer-body" style={{ padding: '0 14px' }}>
          {/* ── AI 角色卡 ── */}
          {tab === 'character' && (
            character
              ? <CardReadout card={character} />
              : <div className="muted-2" style={{ padding: '28px 0', textAlign: 'center', fontSize: 13 }}>未找到该对话的角色卡。</div>
          )}

          {/* ── Persona ── */}
          {tab === 'persona' && (
            !editing ? (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 0 10px' }}>
                  <strong style={{ fontSize: 14, fontFamily: 'var(--font-serif)' }}>
                    {(persona && persona.name) || '你的 persona'}
                  </strong>
                  {persona && (
                    <button
                      className="sheet-btn"
                      style={{ flex: 'none', width: 'auto', height: 34, padding: '0 12px', fontSize: 13 }}
                      onClick={() => setEditing(true)}
                    >
                      <Icon name="edit" size={12} /> 编辑
                    </button>
                  )}
                </div>
                {persona
                  ? <CardReadout card={persona} />
                  : <div className="muted-2" style={{ padding: '28px 0', textAlign: 'center', fontSize: 13 }}>本对话未设置 persona 卡。</div>
                }
              </>
            ) : (
              <>
                <div style={{ paddingTop: 14 }}>
                  <PersonaFields form={form} u={u} />
                </div>
                <div className="sheet-actions" style={{ marginTop: 14, paddingBottom: 14 }}>
                  <button className="sheet-btn" onClick={() => setEditing(false)} disabled={saving}>取消</button>
                  <button className="sheet-btn primary" onClick={doSave} disabled={saving}>
                    <Icon name="check" size={12} /> {saving ? '保存中…' : '保存'}
                  </button>
                </div>
              </>
            )
          )}

          {/* ── 系统提示词 ── */}
          {tab === 'system' && (
            <div style={{ paddingTop: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                <strong style={{ fontSize: 14 }}>系统提示词</strong>
                {!spEditing && onSaveSystemPrompt && (
                  <button
                    className="sheet-btn"
                    style={{ flex: 'none', width: 'auto', height: 34, padding: '0 12px', fontSize: 13 }}
                    onClick={() => setSpEditing(true)}
                  >
                    <Icon name="edit" size={12} /> 编辑
                  </button>
                )}
              </div>
              {!spEditing ? (
                (spVal || '').trim()
                  ? <div style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.7, color: 'var(--text-quiet)' }}>{spVal}</div>
                  : <div className="muted-2" style={{ fontSize: 13, lineHeight: 1.7 }}>本对话未设置系统提示词。点「编辑」自定义 AI 行为。</div>
              ) : (
                <>
                  <textarea
                    className="tv-m-input"
                    value={spVal}
                    onChange={e => setSpVal(e.target.value)}
                    rows={10}
                    placeholder="输入系统提示词…"
                    style={{ resize: 'vertical', minHeight: 160 }}
                  />
                  <div className="sheet-actions" style={{ marginTop: 12, paddingBottom: 14 }}>
                    <button className="sheet-btn" onClick={() => { setSpVal(systemPrompt || ''); setSpEditing(false); }} disabled={spSaving}>取消</button>
                    <button className="sheet-btn primary" onClick={doSaveSP} disabled={spSaving}>
                      <Icon name="check" size={12} /> {spSaving ? '保存中…' : '保存'}
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
function ImportSheet({ show, onClose, onDropCard, onPickFile, onJsonlFile, onCreateFromCard }) {
  const fileRef = useRef(null);
  const jsonlRef = useRef(null);

  /* 拖卡区(移动端没有 drag,所以只有 click 触发文件选择) */
  return (
    <BottomSheet show={show} onClose={onClose} maxHeight="88%">
      <div className="sheet-title">新对话</div>
      <div className="sheet-sub">选一张酒馆角色卡开始,兼容 SillyTavern V2 / V3</div>

      {/* 拖/选卡 —— 用 <label> 包裹 input 原生触发文件选择器:
          手机浏览器对 display:none input 的 .click() 多会拦截,改 label 关联 + 视觉隐藏(非 display:none)。 */}
      <label className="tv-m-import-btn" style={{ position: 'relative' }}>
        <span className="tv-m-import-ic"><Icon name="upload" size={20} /></span>
        <span className="tv-m-import-tx">
          <strong>导入角色卡</strong>
          <span>支持 .png(嵌入元数据) / .json / .webp</span>
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
          <strong>导入聊天记录</strong>
          <span>SillyTavern .jsonl → 转成一段新对话</span>
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
  const initial = (chat.character_name || chat.title || '?').trim().slice(0, 1);
  const curTitle = chat.title || chat.character_name || `对话 #${chat.id}`;

  return (
    <button
      className={`tv-m-chat-item${active ? ' active' : ''}`}
      onClick={() => onOpen(chat)}
    >
      <span className="tv-m-chat-av serif">{initial}</span>
      <span className="tv-m-chat-main">
        <span className="tv-m-chat-row">
          <span className="tv-m-chat-title">{curTitle}</span>
          <span className="tv-m-chat-time muted-2">{relTime(chat.updated_at)}</span>
        </span>
        {chat.last_snippet
          ? <span className="tv-m-chat-snippet muted-2">{chat.last_snippet}</span>
          : <span className="tv-m-chat-snippet muted-2" style={{ fontStyle: 'italic' }}>{chat.character_name || '酒馆角色'}</span>}
      </span>
      <button
        className="tv-m-chat-menu-btn"
        onClick={e => { e.stopPropagation(); onMenu(chat); }}
        aria-label="更多"
      >
        <Icon name="more" size={17} />
      </button>
    </button>
  );
}

/* ─── 列表屏 ──────────────────────────────────────────────────────── */
function ListView({ chats, archivedChats, activeId, loading, onExit, onOpen, onMenu, onNew }) {
  const [showArchived, setShowArchived] = useState(false);
  const empty = !loading && chats.length === 0 && archivedChats.length === 0;

  return (
    <div className="tv-m-screen">
      {/* 顶栏 */}
      <div className="topbar">
        <button className="tb-exit" onClick={onExit}>
          <Icon name="chevron_left" size={15} /> 应用
        </button>
        <div className="tb-title">
          <strong>酒馆</strong>
          <span className="sub"><Icon name="feedback" size={11} /> 1:1 角色对话</span>
        </div>
        <button className="tb-btn accent" onClick={onNew} aria-label="新对话">
          <Icon name="plus" size={18} />
        </button>
      </div>

      {/* 正文 */}
      {loading ? (
        <div className="tv-m-empty muted-2">加载中…</div>
      ) : empty ? (
        <div className="tv-m-hero">
          <div className="tv-m-hero-mark">✻</div>
          <h1 className="tv-m-hero-title serif">想和谁聊聊？</h1>
          <p className="tv-m-hero-sub muted">导入一张酒馆角色卡，立刻开始一段对话。</p>
          <button className="tv-m-hero-drop" onClick={onNew}>
            <span className="tv-m-hero-drop-ic"><Icon name="upload" size={22} /></span>
            <span className="tv-m-hero-drop-main">选择或拖入角色卡</span>
            <span className="tv-m-hero-drop-sub">.png（嵌入元数据）/ .json / .webp</span>
          </button>
        </div>
      ) : (
        <div className="tv-m-list scroll" style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
          {/* 新对话按钮(列表非空时) */}
          <button className="tv-m-newchat" onClick={onNew}>
            <span className="tv-m-newchat-ic"><Icon name="plus" size={20} /></span>
            <span className="tv-m-newchat-tx">
              <strong>新对话</strong>
              <span>选一张角色卡,或导入 SillyTavern 卡</span>
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
                已归档 ({archivedChats.length})
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
  onBack, onSend, onStop, onRetry, onOpenDrawer, onOpenMenu,
}) {
  const [text, setText] = useState('');
  const [pressedIdx, setPressedIdx] = useState(null);
  const [msgSheet, setMsgSheet] = useState(null); // 长按消息 → 操作 sheet(与游戏台同一套交互)
  const lpTimer = useRef(null);
  const openMsgSheet = (i) => { setPressedIdx(i); try { if (navigator.vibrate) navigator.vibrate(12); } catch (_) {} setMsgSheet({ idx: i }); };
  const startPress = (i) => { lpTimer.current = setTimeout(() => openMsgSheet(i), 420); };
  const cancelPress = () => clearTimeout(lpTimer.current);
  const closeMsgSheet = () => { setMsgSheet(null); setPressedIdx(null); };
  const threadRef = useRef(null);
  const taRef = useRef(null);
  const atBottomRef = useRef(true);
  const isFirstLoadRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const charName = (character && character.name) || (activeChat && activeChat.character_name) || '角色';

  /* 自动滚底 */
  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    const onScroll = () => {
      const at = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      atBottomRef.current = at;
      setShowJump(!at);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // ① 第一次进入或刷新页面时，强制滚动到最底部; ② 自己刚发(末条=玩家)→ 滚到底; ③ 否则双守卫:已上滚 或 实时距底>360 → 不跟随(输出完成不拽回)
  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    const last = history && history[history.length - 1];
    if (history.length > 0 && isFirstLoadRef.current) {
      isFirstLoadRef.current = false;
      atBottomRef.current = true;
    } else if (last && last.role === 'user') {
      atBottomRef.current = true;
    } else if (!atBottomRef.current || (el.scrollHeight - el.scrollTop - el.clientHeight) > 360) {
      return;
    }
    const id = requestAnimationFrame(() => {
      if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
  }, [history.length, running]);

  /* textarea 自动增高 */
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
  }, [text]);

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
    window.__apiToast?.('已复制', { kind: 'ok', duration: 1400 });
  };

  return (
    <div className="tv-m-screen" style={{ display: 'flex', flexDirection: 'column' }}>
      {/* 顶栏 */}
      <div className="topbar">
        <button className="tb-btn" onClick={onBack} aria-label="返回列表">
          <Icon name="chevron_left" size={18} />
        </button>
        {charName ? (
          <button className="tb-title" onClick={onOpenDrawer} style={{ cursor: 'pointer' }}>
            <strong style={{ maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis' }}>{charName}</strong>
            <span className="sub">
              <Icon name="chevron_down" size={11} style={{ opacity: 0.45 }} />
              角色卡 / Persona
            </span>
          </button>
        ) : (
          <div className="tb-title">
            <strong>{activeChat?.title || '对话'}</strong>
          </div>
        )}
        <div style={{ display: 'flex', gap: 6 }}>
          {charName && (
            <button className="tb-btn" onClick={onOpenDrawer} aria-label="角色卡 / persona">
              <Icon name="cards" size={16} />
            </button>
          )}
          <button className="tb-btn" onClick={onOpenMenu} aria-label="更多操作">
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
            对话尚未开始。
          </div>
        )}

        {history.map((m, i) => {
          if (m.role === 'assistant') {
            const toolOps = m._toolOps;
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
                  {m._thinking && <ThinkingBlock text={m._thinking} />}
                  <div className="msg-body">
                    <Paras text={m.content} />
                    {isStreaming && (
                      <span className="tv-m-cursor" aria-hidden="true" />
                    )}
                  </div>
                  <div className="msg-hint"><Icon name="menu" size={10} /> 长按这一段查看操作</div>
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
                <span className="msg-tag">{(persona && persona.name) || '你'}</span>
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
                {charName} 正在思考…
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
                <strong style={{ fontSize: 13 }}>生成失败</strong>
                <p style={{ margin: '4px 0 10px', fontSize: 12, color: 'var(--text-quiet)', lineHeight: 1.6 }}>
                  {typeof hasError === 'string' && hasError ? hasError : '请求中断,已保留你的上一条输入,可重试。'}
                </p>
                <button
                  className="sheet-btn primary"
                  style={{ height: 36, padding: '0 14px', width: 'auto', flex: 'none', fontSize: 13 }}
                  onClick={onRetry}
                >
                  重试
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 回到最新按钮 */}
        {showJump && (
          <button
            onClick={() => {
              if (threadRef.current) {
                threadRef.current.scrollTo({ top: threadRef.current.scrollHeight, behavior: 'smooth' });
                atBottomRef.current = true;
                setShowJump(false);
              }
            }}
            style={{
              position: 'sticky', bottom: 8, left: '50%', transform: 'translateX(-50%)',
              display: 'flex', alignItems: 'center', gap: 5, width: 'fit-content',
              padding: '7px 14px', borderRadius: 999, fontSize: 12,
              background: 'var(--panel-3)', border: '1px solid var(--line-strong)',
              color: 'var(--text-quiet)', zIndex: 5,
            }}
          >
            <Icon name="chevron_down" size={13} /> 回到最新
          </button>
        )}
      </div>

      {/* Composer */}
      <div className="composer-zone">
        <div className={`composer${text ? '' : ''}`}>
          <div className="composer-input-row">
            <textarea
              ref={taRef}
              className="c-text"
              rows={1}
              value={text}
              placeholder={`给 ${charName} 写点什么…`}
              onChange={e => setText(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  submit();
                }
              }}
              disabled={running}
            />
            {running ? (
              <button className="c-send" onClick={onStop} aria-label="停止" style={{ background: 'var(--danger)' }}>
                <Icon name="stop" size={14} />
              </button>
            ) : (
              <button
                className={`c-send${!text.trim() ? ' idle' : ''}`}
                onClick={submit}
                disabled={!text.trim()}
                aria-label="发送"
              >
                <Icon name="send" size={14} />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* 长按消息 → 操作 sheet(与游戏台同一套交互;酒馆无存档/分支,故仅 复制 + 重新生成) */}
      <BottomSheet show={!!msgSheet} onClose={closeMsgSheet} maxHeight="50%">
        <div className="sheet-title">{msgSheet && history[msgSheet.idx]?.role === 'assistant' ? '这一段对话' : '你这句话'}</div>
        <div className="sheet-list">
          <button className="sheet-item" onClick={() => { const t = (msgSheet && history[msgSheet.idx]?.content) || ''; closeMsgSheet(); copy(t); }}>
            <span className="sheet-ico"><Icon name="copy" size={18} /></span>
            <span className="sheet-tx"><strong>复制</strong><span>拷贝这一段文字</span></span>
          </button>
          {msgSheet && msgSheet.idx === lastAssistantIdx && !running && (
            <button className="sheet-item" onClick={() => { closeMsgSheet(); onRetry(); }}>
              <span className="sheet-ico"><Icon name="refresh" size={18} /></span>
              <span className="sheet-tx"><strong>重新生成</strong><span>换个写法重说这一句</span></span>
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

  /* ── SSE 控制 ref(照搬 tavern-app.jsx)───────────────────────── */
  const runRef = useRef({ stopped: false, sse: null, runId: 0, inactivityTimer: null });

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

  /* ── applyState(照搬 tavern-app.jsx)─────────────────────────── */
  const applyState = useCallback((data) => {
    if (!data) return;
    const tavern = data.tavern || (data.data && data.data.tavern) || {};
    const char = tavern.character || null;
    setCharacter(char || null);
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
      setActiveChat(prev => ({
        id: data.save_id,
        title: data.save_title || prev?.title || `对话 #${data.save_id}`,
        character_name: (char && char.name) || prev?.character_name || '',
        updated_at: data.save_updated_at || prev?.updated_at || '',
      }));
    }
    // 同步 systemPrompt
    if (tavern.system_prompt !== undefined) {
      setSystemPrompt(tavern.system_prompt || '');
    }
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
      fireToast('打开对话失败', 'danger');
    }
  }, [applyState, fireToast]);

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
  useEffect(() => () => {
    const rc = runRef.current;
    rc.stopped = true;
    if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
    if (rc.sse) { try { rc.sse.stop('unmount'); } catch (_) {} rc.sse = null; }
  }, []);

  /* ── openSaveId ──────────────────────────────────────────────── */
  const openSaveId = useCallback(async (saveId, fallbackName) => {
    await reloadList();
    await openChat({ id: saveId, title: fallbackName || `对话 #${saveId}`, character_name: fallbackName || '' });
  }, [reloadList, openChat]);

  /* ── 文件导入(角色卡 + JSONL)──────────────────────────────────── */
  const onPickCardFile = useCallback(async (file) => {
    if (!file) return;
    if (!/\.(png|json|webp)$/i.test(file.name || '')) {
      fireToast('仅支持 .png / .json / .webp 角色卡', 'warn');
      return;
    }
    try {
      const r = await window.api.tavern.importCharacter(file);
      if (r && r.ok === false) throw new Error(r.error || '导入失败');
      await openSaveId(r.save_id, r.character_name);
      fireToast(`已导入「${r.character_name || '角色'}」`, 'ok');
    } catch (e) {
      fireToast('导入失败' + (e?.message ? ': ' + e.message : ''), 'danger');
    }
  }, [openSaveId, fireToast]);

  const onPickJsonlFile = useCallback(async (file) => {
    if (!file) return;
    try {
      const r = await window.api.tavern.importJsonl(file);
      if (r && r.ok === false) throw new Error(r.error || '导入失败');
      await openSaveId(r.save_id, r.title || '导入对话');
      fireToast(`已导入聊天记录(${r.commits_imported || 0} 条)`, 'ok');
    } catch (e) {
      fireToast('导入失败' + (e?.message ? ': ' + e.message : ''), 'danger');
    }
  }, [openSaveId, fireToast]);

  /* ── stopRun(照搬 tavern-app.jsx)────────────────────────────── */
  const stopRun = useCallback(() => {
    runRef.current.stopped = true;
    if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
    runRef.current.runId = (runRef.current.runId || 0) + 1;
    if (runRef.current.sse) { try { runRef.current.sse.stop('manual_stop'); } catch (_) {} runRef.current.sse = null; }
    try { window.api.game.stop(); } catch (_) {}
    setRunning(false);
  }, []);

  /* ── startRun(逐字照搬 tavern-app.jsx 行 709–887)────────────── */
  const startRun = useCallback(async (playerText) => {
    const saveId = activeId;
    if (saveId == null) { fireToast('请先选择或新建一个对话', 'warn'); return; }
    const rc = runRef.current;
    if (rc.sse) { rc.runId = (rc.runId || 0) + 1; try { rc.sse.stop('superseded'); } catch (_) {} rc.sse = null; try { window.api.game.stop(); } catch (_) {} }
    if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
    const runId = (rc.runId || 0) + 1;
    rc.runId = runId; rc.stopped = false;
    const isCurrentRun = () => rc.runId === runId;

    const ts = tvNow();
    setHistory(h => [...h, { role: 'user', content: playerText, ts }]);
    setLastPlayerText(playerText);
    setHasError(false);
    setRunning(true);

    let openedAssistant = false;
    let gotDone = false;
    const STREAM_IDLE_TIMEOUT_MS = 120000;

    const restoreFailedDraft = () => {
      if (!isCurrentRun() || openedAssistant) return;
      setHistory(h => {
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
        fireToast('生成停滞,120 秒无响应', 'warn');
      }, STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();

    rc.sse = window.api.game.chat(
      { message: playerText, text: playerText, save_id: saveId },
      {
        onError: (err) => {
          if (!isCurrentRun()) return;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          const detail = (err && err.payload && err.payload.message) || (err && err.message) || '请求失败';
          setRunning(false); setHasError(detail);
          fireToast('请求失败', 'danger');
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
          void data;
        },
        on_reasoning: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          const piece = (data && (data.text || data.delta)) || '';
          if (!piece) return;
          setHistory(h => {
            let arr = h;
            if (!openedAssistant) { openedAssistant = true; arr = [...h, { role: 'assistant', content: '', ts, streaming: true }]; }
            const last = arr[arr.length - 1];
            if (!last || last.role !== 'assistant') return arr;
            return [...arr.slice(0, -1), { ...last, _thinking: (last._thinking || '') + piece }];
          });
        },
        on_tool_call: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          const op = { tool: (data && data.tool) || '?', args: (data && (data.args_summary || data.args)) || null, _pending: true };
          setHistory(h => {
            let arr = h;
            if (!openedAssistant) { openedAssistant = true; arr = [...h, { role: 'assistant', content: '', ts, streaming: true }]; }
            const last = arr[arr.length - 1];
            if (!last || last.role !== 'assistant') return arr;
            return [...arr.slice(0, -1), { ...last, _toolOps: [...(last._toolOps || []), op] }];
          });
        },
        on_tool_result: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          setHistory(h => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant' || !Array.isArray(last._toolOps) || !last._toolOps.length) return h;
            const ops = [...last._toolOps];
            for (let i = ops.length - 1; i >= 0; i--) {
              if (ops[i]._pending) {
                ops[i] = { ...ops[i], ok: !!(data && data.ok), result: (data && data.result_snippet) || null, error: (data && data.error) || null, _pending: false };
                break;
              }
            }
            return [...h.slice(0, -1), { ...last, _toolOps: ops }];
          });
        },
        on_token: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          const piece = (data && (data.text || data.delta)) || '';
          if (!piece) return;
          setHistory(h => {
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
            const msg = data && data.interrupted ? '本轮已中断,已恢复你的输入。' : '本轮没有收到回复,请重试。';
            setHasError(msg);
            fireToast(data && data.interrupted ? '生成中断' : '空回复', 'warn');
            rc.sse = null;
            return;
          }
          setHistory(h => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant') return h;
            return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
          });
          const payload = (data && data.status) || null;
          if (payload) applyState(payload);
          else { window.api.game.state().then(applyState).catch(() => {}); }
          reloadList();
          rc.sse = null;
        },
        on_error: (data) => {
          if (!isCurrentRun()) return;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          const realMsg = (data && (data.message || data.detail || data.error)) || '';
          setRunning(false); setHasError(realMsg || true);
          fireToast('生成失败', 'danger');
          restoreFailedDraft();
        },
        onClose: () => {
          if (!isCurrentRun()) return;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          if (gotDone || rc.stopped) { rc.sse = null; return; }
          setRunning(r => {
            if (!r) return r;
            setHasError('连接中断,上一条输入已保留,可重试。');
            restoreFailedDraft();
            return false;
          });
          setHistory(h => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant' || !last.streaming) return h;
            return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
          });
        },
      }
    );
  }, [activeId, applyState, reloadList, fireToast]);

  /* ── onRetry(照搬 tavern-app.jsx)────────────────────────────── */
  const onRetry = useCallback(() => {
    if (running) return;
    let t2 = (lastPlayerText && lastPlayerText.trim()) || '';
    if (!t2) {
      for (let i = history.length - 1; i >= 0; i--) {
        if (history[i]?.role === 'user' && (history[i].content || '').trim()) { t2 = history[i].content.trim(); break; }
      }
    }
    if (!t2) { fireToast('没有可重试的输入', 'warn'); return; }
    setHasError(false);
    setHistory(h => {
      const out = [...h];
      while (out.length && out[out.length - 1].role === 'assistant' && !(out[out.length - 1].content || '').trim()) out.pop();
      if (out.length && out[out.length - 1].role === 'user' && (out[out.length - 1].content || '').trim() === t2) out.pop();
      return out;
    });
    startRun(t2);
  }, [running, lastPlayerText, history, startRun, fireToast]);

  /* ── rail 操作 ───────────────────────────────────────────────── */
  const doRename = useCallback(async (chat, title) => {
    if (title == null) { setRenameTarget(chat); setRenameOpen(true); return; }
    try {
      await window.api.tavern.rename(chat.id, title);
      fireToast('已重命名', 'ok');
      reloadList();
      if (String(chat.id) === String(activeId)) setActiveChat(p => ({ ...(p || {}), title }));
    } catch (e) { fireToast('重命名失败', 'danger'); }
    setRenameOpen(false);
  }, [reloadList, activeId, fireToast]);

  const doArchive = useCallback(async (chat, archived) => {
    try {
      await window.api.tavern.archive(chat.id, archived);
      fireToast(archived ? '已归档' : '已取消归档', 'ok');
      reloadList();
    } catch (e) { fireToast('归档失败', 'danger'); }
  }, [reloadList, fireToast]);

  const doDelete = useCallback(async (chat) => {
    setDeleteOpen(false); setDeleteTarget(null);
    try {
      await window.api.tavern.remove(chat.id);
      fireToast('已删除', 'ok');
      if (String(chat.id) === String(activeId)) {
        setActiveId(null); setActiveChat(null); setHistory([]); setCharacter(null); setPersona(null);
        setView('list');
      }
      reloadList();
    } catch (e) { fireToast('删除失败', 'danger'); }
  }, [reloadList, activeId, fireToast]);

  const doAutotitle = useCallback(async (chat) => {
    try {
      await window.api.tavern.autotitle(chat.id);
      fireToast('已自动命名', 'ok');
      reloadList();
      if (String(chat.id) === String(activeId)) {
        const data = await window.api.game.state();
        applyState(data);
      }
    } catch (e) { fireToast('自动命名失败', 'danger'); }
  }, [reloadList, activeId, applyState, fireToast]);

  const onSaveSystemPrompt = useCallback(async (sp) => {
    if (!activeId) return;
    try {
      await window.api.tavern.setSystemPrompt(activeId, sp);
      setSystemPrompt(sp || '');
      fireToast('系统提示词已保存', 'ok');
    } catch (e) { fireToast('保存失败', 'danger'); throw e; }
  }, [activeId, fireToast]);

  const onSavePersona = useCallback(async (payload) => {
    try {
      const saved = await window.api.cards.myUpsert(payload);
      fireToast('persona 已保存', 'ok');
      try { const d = await window.api.game.state(); applyState(d); } catch (_) {}
      return saved;
    } catch (e) {
      fireToast('保存失败', 'danger');
      throw e;
    }
  }, [applyState, fireToast]);

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
