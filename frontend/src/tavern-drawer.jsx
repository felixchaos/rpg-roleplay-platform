/* 酒馆「角色 / Persona / 设定」抽屉 —— 垂直图标 rail + 独立滚动内容区。
 *
 * 从 tavern-app.jsx 整体迁出(该文件曾把 TwoCardDrawer 及其子组件与顶层 TavernApp
 * 混在一起,1233 行里塞了一个完整的抽屉子系统)。迁移原则:
 *   - CardSheet / CardPickerSheet / PersonaHero / 表单逻辑(cardFormInit/Payload)/
 *     portal 退场两段式 / 绑卡回调 —— 原样搬运,零行为变化。
 *   - 唯一的变化面:旧版 5 个页签挤在一条 .seg 分段控件里,内嵌窄宽度下互相挤压
 *     (图标压成圆点残渣、「正则」页签被截断),且沉浸式开关错放在「AI 角色」页签内。
 *     新版把页签换成左侧 44px 垂直图标 rail(上组「角色」/下组「设定」),内容区
 *     独立滚动、节头 sticky;沉浸式开关移到 rail 底部固定位。
 *   - props 契约完全不变;导出 `TavernDrawer` 新名 + `TwoCardDrawer` 兼容别名,
 *     后者供 pages/tavern.jsx 现有 `import { TwoCardDrawer } from '../tavern-app.jsx'`
 *     不用改。
 *
 * 窄容器降级(<380px,独立页手机宽/内嵌窄栏):用 CSS container query(平台已有
 * 先例 —— platform.css 的 `.pl-side { container-type: inline-size; }` +
 * `@container side (max-width: 169px)`),而非 ResizeObserver:布局响应完全交给
 * CSS,拖拽/窗口变化时零 JS 重渲染开销,且与既有模式一致。
 */
import React from 'react';
import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { Icon } from './game-icons.jsx';
import { WorldbookOverlaySection, RegexScriptsSection } from './game-panels.jsx';
import { CardSheet, CardEditFields, cardFormInit, cardFormPayload } from './pages/cards.jsx';

/* ── rail 页签清单(顺序即视觉顺序 = 键盘 Home/End 两端)──────────────────
 * 两组:「角色」(character/persona)/「设定」(system/worldbook/regex)。 */
const GROUP_CHARACTER = ['character', 'persona'];
const GROUP_SETTINGS = ['system', 'worldbook', 'regex'];
const TAB_ORDER = [...GROUP_CHARACTER, ...GROUP_SETTINGS];
const TAB_ICON = {
  character: 'cards',
  persona: 'user',
  system: 'settings',
  worldbook: 'world',
  regex: 'braces',
};
const TAB_LABEL_KEY = {
  character: 'tavern_app.drawer.tab_character',
  persona: 'tavern_app.drawer.tab_persona',
  system: 'tavern_app.drawer.tab_system',
  worldbook: 'tavern_app.drawer.tab_worldbook',
  regex: 'tavern_app.drawer.tab_regex',
};

const SESSION_TAB_KEY = 'tvd.tab';
function loadSavedTab() {
  try {
    const v = sessionStorage.getItem(SESSION_TAB_KEY);
    return TAB_ORDER.includes(v) ? v : null;
  } catch (_) { return null; }
}
function saveTab(tab) {
  try { sessionStorage.setItem(SESSION_TAB_KEY, tab); } catch (_) {}
}

/* ── 人设图海报(侧栏顶部):拉 persona-images,当前图做大图 + 缩略条。
 * 原样从 tavern-app.jsx 搬运,零行为变化。 */
function PersonaHero({ cardId, avatar }) {
  const { t } = useTranslation();
  const [imgs, setImgs] = useState([]);
  const [zoom, setZoom] = useState(null);
  useEffect(() => {
    if (!cardId) { setImgs([]); return; }
    let alive = true;
    Promise.resolve(window.api.cards.personaImages(cardId))
      .then((r) => { if (alive) setImgs(Array.isArray(r) ? r : (r && r.items) || []); })
      .catch(() => {});
    return () => { alive = false; };
  }, [cardId]);
  // 无人设图时回退展示头像(始终给一张大图),都没有才不渲染。
  const cur = imgs.find((i) => i.is_current) || imgs[0] || (avatar ? { id: '_av', image_url: avatar } : null);
  if (!cur || !cur.image_url) return null;
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: 1.2, color: 'var(--accent)', marginBottom: 8 }}>
        {t('tavern_app.drawer.persona_image')}
      </div>
      {cur && cur.image_url && (
        <img src={cur.image_url} alt="" onClick={() => setZoom(cur.image_url)}
          style={{ width: '100%', maxHeight: 380, objectFit: 'contain', borderRadius: 12,
                   border: '1px solid var(--line)', background: 'var(--panel-2, #282623)', cursor: 'zoom-in', display: 'block' }} />
      )}
      {imgs.length > 1 && (
        <div style={{ display: 'flex', gap: 6, marginTop: 8, overflowX: 'auto', paddingBottom: 2 }}>
          {imgs.map((i) => (
            <img key={i.id} src={i.image_url} alt="" onClick={() => setZoom(i.image_url)}
              style={{ width: 50, height: 50, objectFit: 'cover', borderRadius: 8, flexShrink: 0, cursor: 'zoom-in',
                       border: i.is_current ? '2px solid var(--accent)' : '1px solid var(--line)' }} />
          ))}
        </div>
      )}
      {zoom && (
        <div onClick={() => setZoom(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.92)', zIndex: 9999,
                   display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'zoom-out' }}>
          <img src={zoom} alt="" style={{ maxWidth: '92%', maxHeight: '92%', objectFit: 'contain', borderRadius: 8 }} />
        </div>
      )}
    </div>
  );
}

/* ── 从统一角色卡库选卡(酒馆侧栏「选择 / 更换角色卡 / 我的角色」)。
 * 原样从 tavern-app.jsx 搬运,零行为变化。 */
function CardPickerSheet({ role, onPick, onClose }) {
  const { t } = useTranslation();
  const [cards, setCards] = useState([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    Promise.resolve(window.api.cards.myList())
      .then((r) => {
        if (!alive) return;
        const list = Array.isArray(r) ? r : (r && (r.items || r.cards)) || [];
        setCards(list);
      })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);
  const title = role === 'character'
    ? t('tavern_app.drawer.choose_character')
    : t('tavern_app.drawer.choose_persona');
  return (
    <div onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 9998,
               display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }}>
      <div onClick={(e) => e.stopPropagation()}
        style={{ background: 'var(--bg, #1a1817)', borderTop: '1px solid var(--line)',
                 borderRadius: '16px 16px 0 0', width: '100%', maxWidth: 520, maxHeight: '72vh',
                 display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--line)',
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <strong style={{ fontSize: 14 }}>{title}</strong>
          <button className="iconbtn" onClick={onClose} aria-label={t('common.close')}>
            <Icon name="close" size={15} />
          </button>
        </div>
        <div style={{ overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {loading ? (
            <div className="muted-2" style={{ padding: 24, textAlign: 'center' }}>…</div>
          ) : cards.length === 0 ? (
            <div className="muted-2" style={{ padding: 24, textAlign: 'center' }}>
              {t('tavern_app.drawer.no_cards')}
            </div>
          ) : (
            cards.map((c) => (
              <button key={c.id} onClick={() => onPick(c.id)}
                style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '10px 12px',
                         background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 12,
                         cursor: 'pointer', textAlign: 'left' }}>
                {c.avatar_path ? (
                  <img src={c.avatar_path} alt=""
                    style={{ width: 44, height: 44, borderRadius: 8, objectFit: 'cover', flexShrink: 0 }} />
                ) : (
                  <div style={{ width: 44, height: 44, borderRadius: 8, background: 'var(--panel-2, #282623)',
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                                flexShrink: 0, fontFamily: 'serif', fontSize: 18, color: 'var(--accent)' }}>
                    {(c.name || '?').slice(0, 1)}
                  </div>
                )}
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{c.name || t('tavern_app_extra.card_unnamed')}</div>
                  {(c.identity || c.summary) && (
                    <div className="muted-2" style={{ fontSize: 12, overflow: 'hidden',
                         textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.identity || c.summary}</div>
                  )}
                </div>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════
 * TavernDrawer —— rail(role=tablist,垂直图标导航)+ 内容区(独立滚动)。
 * props 契约与旧 TwoCardDrawer 完全一致。
 * ══════════════════════════════════════════════════════════════════ */
export function TavernDrawer({ open, character, persona, onClose, onSavePersona,
                                inline = false, systemPrompt = '', onSaveSystemPrompt,
                                chatId = null, onBindCard,
                                immersive = false, onToggleImmersive }) {
  const { t } = useTranslation();
  const [form, setForm] = useState(() => cardFormInit(persona));
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [pickRole, setPickRole] = useState(null); // null | 'character' | 'persona' —— 选卡器
  const [tab, setTab] = useState(() => loadSavedTab() || 'character'); // 记住上次页签(sessionStorage)
  const [spVal, setSpVal] = useState(systemPrompt || '');
  const [spEditing, setSpEditing] = useState(false);
  const [spSaving, setSpSaving] = useState(false);
  const tabRefs = useRef({});
  // portal 分支退场动效:open 变 false 后不立刻卸载,先带 closing class 渲染 160ms 再真正消失。
  const [visible, setVisible] = useState(open);
  const [closing, setClosing] = useState(false);
  const prevOpenRef = useRef(open);
  useEffect(() => {
    const wasOpen = prevOpenRef.current;
    prevOpenRef.current = open;
    if (open) { setVisible(true); setClosing(false); return; }
    if (!wasOpen) return; // 从未打开过(如初始 open=false),无需退场
    setClosing(true);
    const timer = setTimeout(() => { setVisible(false); setClosing(false); }, 160);
    return () => clearTimeout(timer);
  }, [open]);

  useEffect(() => { setForm(cardFormInit(persona)); setEditing(false); }, [persona, open]);
  useEffect(() => { setSpVal(systemPrompt || ''); setSpEditing(false); }, [systemPrompt, open]);
  useEffect(() => { saveTab(tab); }, [tab]);
  const u = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  // 非 inline(独立页 portal):open=false 且退场动效已结束(visible=false)才不渲染。inline:始终渲染,靠 collapsed 类收起。
  if (!inline && !open && !visible) return null;
  const personaName = (persona && persona.name) || t('tavern_app.drawer.persona_fallback');

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

  // 键盘导航:↑↓ 垂直态 / ←→ 窄容器横向态,同一 handler(prev/next 语义不看实际 CSS 朝向,
  // 两套按键都认;Home/End 到两端)。焦点跟随 aria-selected(roving tabindex:只有当前
  // 页签 tabIndex=0,切换后手动 focus 对应按钮)。
  const onRailKeyDown = (e) => {
    const idx = TAB_ORDER.indexOf(tab);
    let next = null;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') next = TAB_ORDER[(idx + 1) % TAB_ORDER.length];
    else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') next = TAB_ORDER[(idx - 1 + TAB_ORDER.length) % TAB_ORDER.length];
    else if (e.key === 'Home') next = TAB_ORDER[0];
    else if (e.key === 'End') next = TAB_ORDER[TAB_ORDER.length - 1];
    if (next) {
      e.preventDefault();
      setTab(next);
      tabRefs.current[next]?.focus();
    }
  };

  const renderTabBtn = (key) => (
    <button
      key={key}
      ref={(el) => { tabRefs.current[key] = el; }}
      type="button"
      role="tab"
      id={`tvd-tab-${key}`}
      aria-controls={`tvd-panel-${key}`}
      aria-selected={tab === key}
      tabIndex={tab === key ? 0 : -1}
      className="iconbtn tvd-rail-btn"
      aria-label={t(TAB_LABEL_KEY[key])}
      data-tip={t(TAB_LABEL_KEY[key])}
      data-tip-pos="right"
      onClick={() => setTab(key)}
    >
      <Icon name={TAB_ICON[key]} size={16} />
    </button>
  );

  const rail = (
    <div
      className="tvd-rail"
      role="tablist"
      aria-orientation="vertical"
      onKeyDown={onRailKeyDown}
    >
      <div className="tvd-rail-group" role="group" aria-label={t('tavern_app.drawer.rail_group_character')}>
        {GROUP_CHARACTER.map(renderTabBtn)}
      </div>
      <div className="tvd-rail-divider" aria-hidden="true" />
      <div className="tvd-rail-group" role="group" aria-label={t('tavern_app.drawer.rail_group_settings')}>
        {GROUP_SETTINGS.map(renderTabBtn)}
      </div>
      {onToggleImmersive && (
        <div className="tvd-rail-foot">
          <button
            type="button"
            role="switch"
            aria-checked={!!immersive}
            aria-label={t('tavern_app.drawer.immersive_label')}
            className={`tvd-immersive-btn${immersive ? ' on' : ''}`}
            data-tip={t('tavern_app.drawer.immersive_desc')}
            data-tip-pos="right"
            onClick={() => onToggleImmersive(!immersive)}
          >
            <Icon name="eye" size={15} />
            <span className="tvd-immersive-dot" aria-hidden="true" />
          </button>
        </div>
      )}
    </div>
  );

  const content = (
    <div
      className="tvd-content"
      role="tabpanel"
      id={`tvd-panel-${tab}`}
      aria-labelledby={`tvd-tab-${tab}`}
    >
      {tab === 'character' && (
        <div className="tvd-content-inner">
          <div className="tvd-section-head">
            <div className="tvd-section-head-title"><Icon name="cards" size={15} /><span>{t(TAB_LABEL_KEY.character)}</span></div>
            {onBindCard && chatId != null && (
              <div className="tvd-section-head-actions">
                <button className="btn ghost" onClick={() => setPickRole('character')}>
                  <Icon name="cards" size={12} /> {t('tavern_app.drawer.choose_character')}
                </button>
              </div>
            )}
          </div>
          {character
            ? <><PersonaHero cardId={character.id} avatar={character.avatar_path} /><CardSheet card={character} kind="user" dense /></>
            : <div className="tvd-empty">{t('tavern_app.drawer.char_not_found')}</div>}
        </div>
      )}

      {tab === 'persona' && (
        <div className="tvd-content-inner">
          {!editing ? (
            <>
              <div className="tvd-section-head">
                <div className="tvd-section-head-title"><Icon name="user" size={15} /><span>{t(TAB_LABEL_KEY.persona)}</span></div>
                <div className="tvd-section-head-actions">
                  {onBindCard && chatId != null && (
                    <button className="btn ghost" onClick={() => setPickRole('persona')}>
                      <Icon name="cards" size={12} /> {t('tavern_app.drawer.choose_persona')}
                    </button>
                  )}
                  {persona && (
                    <button className="btn ghost" onClick={() => setEditing(true)}><Icon name="edit" size={12} /> {t('common.edit')}</button>
                  )}
                </div>
              </div>
              <div style={{ marginBottom: 12 }}>
                <strong style={{ fontSize: 14 }}>{personaName}</strong>
              </div>
              {persona && persona.id ? <PersonaHero cardId={persona.id} avatar={persona.avatar_path} /> : null}
              {persona
                ? <CardSheet card={persona} kind="persona" dense />
                : <div className="tvd-empty">{t('tavern_app.drawer.persona_not_set')}</div>}
            </>
          ) : (
            <>
              <div className="tvd-section-head">
                <div className="tvd-section-head-title"><Icon name="user" size={15} /><span>{t(TAB_LABEL_KEY.persona)}</span></div>
              </div>
              <CardEditFields form={form} u={u} kind="persona" />
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                <button className="btn ghost" onClick={() => setEditing(false)} disabled={saving}>{t('common.cancel')}</button>
                <button className="btn primary" onClick={doSave} disabled={saving}>
                  <Icon name="check" size={12} /> {saving ? t('tavern_app.drawer.saving') : t('common.save')}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {tab === 'system' && (
        <div className="tvd-content-inner">
          <div className="tvd-section-head">
            <div className="tvd-section-head-title"><Icon name="settings" size={15} /><span>{t(TAB_LABEL_KEY.system)}</span></div>
            {!spEditing && onSaveSystemPrompt && (
              <div className="tvd-section-head-actions">
                <button className="btn ghost" onClick={() => setSpEditing(true)}><Icon name="edit" size={12} /> {t('common.edit')}</button>
              </div>
            )}
          </div>
          {!spEditing ? (
            (spVal || '').trim()
              ? <div className="tv-sysprompt-view" style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.7 }}>{spVal}</div>
              : <div className="tvd-empty">{t('tavern_app.drawer.sysprompt_empty')}</div>
          ) : (
            <>
              <textarea
                value={spVal} onChange={(e) => setSpVal(e.target.value)} rows={14}
                placeholder={t('tavern_app.drawer.sysprompt_placeholder')}
                style={{ width: '100%', resize: 'vertical', fontSize: 13, lineHeight: 1.6 }}
              />
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                <button className="btn ghost" onClick={() => { setSpVal(systemPrompt || ''); setSpEditing(false); }} disabled={spSaving}>{t('common.cancel')}</button>
                <button className="btn primary" onClick={doSaveSP} disabled={spSaving}>
                  <Icon name="check" size={12} /> {spSaving ? t('tavern_app.drawer.saving') : t('common.save')}
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {tab === 'worldbook' && (
        <div className="tvd-content-inner">
          <div className="tvd-section-head">
            <div className="tvd-section-head-title"><Icon name="world" size={15} /><span>{t(TAB_LABEL_KEY.worldbook)}</span></div>
          </div>
          <WorldbookOverlaySection />
        </div>
      )}

      {tab === 'regex' && (
        <div className="tvd-content-inner">
          <div className="tvd-section-head">
            <div className="tvd-section-head-title"><Icon name="braces" size={15} /><span>{t(TAB_LABEL_KEY.regex)}</span></div>
          </div>
          <RegexScriptsSection />
        </div>
      )}
    </div>
  );

  const drawerInner = (
    <div className="tvd-root">
      <header className="tvd-header">
        <div className="tvd-header-title">
          <Icon name={TAB_ICON[tab]} size={15} />
          <span>{t(TAB_LABEL_KEY[tab])}</span>
        </div>
        <button className="iconbtn" onClick={onClose} data-tip={inline ? t('tavern_app.drawer.collapse') : t('common.close')} aria-label={inline ? t('tavern_app.drawer.collapse') : t('common.close')}>
          <Icon name={inline ? 'chevron_right' : 'close'} size={15} />
        </button>
      </header>
      <div className="tvd-body">
        {rail}
        {content}
      </div>
      {pickRole && (
        <CardPickerSheet
          role={pickRole}
          onClose={() => setPickRole(null)}
          onPick={(cid) => { const r = pickRole; setPickRole(null); if (onBindCard) onBindCard(r, cid); }}
        />
      )}
    </div>
  );

  if (inline) {
    return (
      <aside className={'tvp-drawer-panel' + (open ? '' : ' collapsed')} aria-hidden={!open}>
        <div className="tvp-drawer-panel-inner">{drawerInner}</div>
      </aside>
    );
  }
  return createPortal(
    <div className="tv-drawer-backdrop" onClick={onClose}>
      <div className={'tv-drawer' + (closing ? ' closing' : '')} onClick={(e) => e.stopPropagation()}>{drawerInner}</div>
    </div>,
    document.body,
  );
}

// 兼容旧 import 名(pages/tavern.jsx 现有 `import { TwoCardDrawer } from '../tavern-app.jsx'`
// 经 tavern-app.jsx 的 `export { TwoCardDrawer } from './tavern-drawer.jsx'` 落到这里)。
export { TavernDrawer as TwoCardDrawer };
