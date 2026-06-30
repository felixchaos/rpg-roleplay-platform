/* MobileCards — 移动端角色卡管理(我的 / NPC / 在线库)
   覆盖路由: cards / cards-npc / cards-online
   铁律:
   - 零 Cloudscape/CS* 组件
   - 数据层 100% 复用 window.api.cards.*
   - 样式只用 mobile.css 已有 class + inline style
   - 子视图(列表→详情→编辑)用 useState 管理,不依赖外部路由 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import AvatarImg from '../../components/AvatarImg.jsx';
// 卡表单读/写 helper 与桌面端字段集逐字一致 → 复用单一规范实现,避免 shape 漂移。
import { cardFormInit, cardFormPayload } from '../../pages/cards.jsx';
// 开关行统一到 mobile/Field.jsx(语义统一 #36);本地原 SetRow(toggle)与之 DOM/CSS 逐字节一致,
// import 为同名 SetRow,调用点零变化。本文件的竖排 Field(内置 input,非通用控件)保留本地实现。
import { ToggleRow as SetRow } from '../Field.jsx';

/* ── helpers ─────────────────────────────────────────────────────── */
const clamp2 = { display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: 2, overflow: 'hidden', wordBreak: 'break-word' };
const clamp3 = { ...clamp2, WebkitLineClamp: 3 };

// 语义统一 #40(needs-care,保留):falsy → '0 B'(非 window.__fmt.bytes 的 '—'),且无 GB 档,
// 改用统一版会改显示(空值文案 + ≥1GB 档),刻意不动。
function fmtBytes(n) {
  if (!n) return '0 B';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

/* cardFormInit / cardFormPayload 复用 pages/cards.jsx 的规范实现(见顶部 import)。 */

/* ── shared sub-components ──────────────────────────────────────── */

/** 顶部 back 头 */
function SubHead({ title, sub, onBack, actions }) {
  const { t } = useTranslation();
  return (
    <div className="pl-head">
      <button className="pl-back" onClick={onBack} aria-label={t('mobile.cards.back')}>
        <Icon name="chevron_left" size={20} />
      </button>
      <div className="pl-head-title">
        <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 16 }}>{title}</strong>
        {sub && <span className="sub">{sub}</span>}
      </div>
      {actions && <div className="pl-head-actions">{actions}</div>}
    </div>
  );
}

/** 角色 avatar — 有 src 则渲图片(AvatarImg 内部 onError 自动回退)，无 src 则首字母色块
 *  fill 模式:网格卡用全幅 92px banner(mc-card-av-wrap/img/letter CSS 控制尺寸),
 *  渲与原内联手写 img→onError→首字母完全等价的两元素结构(行为零变化),
 *  badge(off-dot/pinned/public)由调用方作为兄弟节点叠加。 */
function CardAv({ src, name, enabled, size = 72, radius = 20, colorClass = 'accent', zoomable = false, fill = false }) {
  const initial = (name || '?').trim().slice(0, 1);

  if (fill) {
    return (
      <>
        {src ? (
          <img
            src={src}
            alt={name}
            loading="lazy"
            className="mc-card-av-img"
            onError={(e) => { e.currentTarget.style.display = 'none'; e.currentTarget.nextSibling && (e.currentTarget.nextSibling.style.display = 'grid'); }}
          />
        ) : null}
        <div className="mc-card-av-letter" style={{ display: src ? 'none' : 'grid' }}>
          {(name || '').slice(0, 1)}
        </div>
      </>
    );
  }

  const shapeStyle = { width: size, height: size, borderRadius: radius, flexShrink: 0 };

  if (src) {
    return (
      <div style={{ ...shapeStyle, position: 'relative', overflow: 'hidden' }}>
        <AvatarImg
          src={src}
          name={name}
          size={size}
          shape="square"
          zoomable={zoomable}
          className="mc-card-av-img"
        />
        {enabled === false && (
          <span style={{ position: 'absolute', top: 7, right: 7, width: 9, height: 9, borderRadius: 999, background: 'var(--muted-3)', zIndex: 1 }} />
        )}
      </div>
    );
  }

  return (
    <div style={{
      ...shapeStyle,
      display: 'grid', placeItems: 'center', position: 'relative',
      font: `600 ${Math.round(size * 0.42)}px var(--font-serif)`,
      background: colorClass === 'accent'
        ? 'linear-gradient(140deg, rgba(201,100,66,0.26), rgba(201,100,66,0.05))'
        : colorClass === 'info'
          ? 'linear-gradient(140deg, rgba(122,166,194,0.24), rgba(122,166,194,0.05))'
          : 'linear-gradient(140deg, var(--panel-3), var(--panel-2))',
      color: colorClass === 'accent' ? 'var(--accent)' : colorClass === 'info' ? 'var(--info)' : 'var(--text)',
    }}>
      {initial}
      {enabled === false && (
        <span style={{ position: 'absolute', top: 7, right: 7, width: 9, height: 9, borderRadius: 999, background: 'var(--muted-3)' }} />
      )}
    </div>
  );
}

/** 标签 pill */
function Tag({ label, color }) {
  const map = {
    green: { color: 'var(--ok)', border: 'rgba(126,184,142,0.3)', bg: 'var(--ok-soft)' },
    accent: { color: 'var(--accent)', border: 'var(--accent-edge)', bg: 'var(--accent-soft)' },
    default: { color: 'var(--text-quiet)', border: 'var(--line-soft)', bg: 'var(--bg)' },
  };
  const s = map[color] || map.default;
  return (
    <span style={{ fontSize: 11, padding: '3px 8px', borderRadius: 7, border: `1px solid ${s.border}`, background: s.bg, color: s.color, whiteSpace: 'nowrap' }}>
      {label}
    </span>
  );
}

/** 只读档案文字块 */
function ProseBlock({ label, value }) {
  if (!value) return null;
  return (
    <div className="pl-prose-block">
      <div className="lbl">{label}</div>
      <div className="tx serif" style={{ whiteSpace: 'pre-wrap' }}>{value}</div>
    </div>
  );
}

/** 表单字段(input / textarea) */
// 语义统一 #36(保留):此 Field 内置 input/textarea 控件(非通用 children 控件),且 desc 用
// <div className="desc">(mobile/Field.jsx 的 Field 用 <span className="desc">)→ 形态不同,保留本地实现。
function Field({ label, value, rows, placeholder, desc, onChange, type = 'text' }) {
  return (
    <div className="pl-field">
      <label>{label}</label>
      {desc && <div className="desc">{desc}</div>}
      {rows
        ? <textarea className="pl-input" rows={rows} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
        : <input className="pl-input" type={type} inputMode={type === 'number' ? 'numeric' : undefined} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
      }
    </div>
  );
}

/** Scope 选择器 */
function ScopeSelect({ value, onChange, isNpc = false }) {
  const { t } = useTranslation();
  const opts = isNpc
    ? [{ v: 'script', l: t('mobile.cards.scope.script') }, { v: 'private', l: t('mobile.cards.scope.private') }, { v: 'public', l: t('mobile.cards.scope.public') }]
    : [{ v: 'private', l: t('mobile.cards.scope.private') }, { v: 'public', l: t('mobile.cards.scope.public') }];
  return (
    <div className="pl-field">
      <label>{t('mobile.cards.scope.label')}</label>
      <div style={{ display: 'flex', gap: 8 }}>
        {opts.map((o) => (
          <button key={o.v} onClick={() => onChange(o.v)} style={{
            flex: 1, height: 40, borderRadius: 11, fontSize: 13.5,
            border: `1px solid ${value === o.v ? 'var(--accent-edge)' : 'var(--line-soft)'}`,
            background: value === o.v ? 'var(--accent-soft)' : 'var(--bg-deep)',
            color: value === o.v ? 'var(--accent)' : 'var(--muted)',
            fontWeight: value === o.v ? 600 : 400,
          }}>{o.l}</button>
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   卡片编辑表单(用户卡 + NPC 共用)
   ═══════════════════════════════════════════════════════════════════ */
function CardEditForm({ form, u, kind = 'user' }) {
  const { t } = useTranslation();
  const isNpc = kind === 'npc';
  return (
    <>
      {/* 基本信息 */}
      <div className="pl-sec-head" style={{ marginTop: 0, marginBottom: 12, padding: '0 2px' }}>
        <h2>{t('mobile.cards.form.section_basic')}</h2>
      </div>
      <Field label={t('mobile.cards.form.name_label')} value={form.name} placeholder={t('mobile.cards.form.name_placeholder')} onChange={(v) => u('name', v)} />
      <Field label={t('mobile.cards.form.full_name_label')} value={form.full_name} placeholder={t('mobile.cards.form.full_name_placeholder')} desc={t('mobile.cards.form.full_name_desc')} onChange={(v) => u('full_name', v)} />
      <Field label={t('mobile.cards.form.identity_label')} value={form.identity} placeholder={t('mobile.cards.form.identity_placeholder')} onChange={(v) => u('identity', v)} />
      <Field label={t('mobile.cards.form.aliases_label')} value={form.aliases} placeholder={t('mobile.cards.form.comma_separated')} desc={t('mobile.cards.form.aliases_desc')} onChange={(v) => u('aliases', v)} />
      <Field label={t('mobile.cards.form.tags_label')} value={form.tags} placeholder={t('mobile.cards.form.comma_separated')} desc={t('mobile.cards.form.tags_desc')} onChange={(v) => u('tags', v)} />

      {/* 人物档案 */}
      <div className="pl-sec-head" style={{ marginTop: 8, marginBottom: 12, padding: '0 2px' }}>
        <h2>{t('mobile.cards.form.section_profile')}</h2>
      </div>
      <Field label={t('mobile.cards.form.background_label')} value={form.background} rows={3} placeholder={t('mobile.cards.form.background_placeholder')} onChange={(v) => u('background', v)} />
      <Field label={t('mobile.cards.form.appearance_label')} value={form.appearance} rows={2} placeholder={t('mobile.cards.form.appearance_placeholder')} onChange={(v) => u('appearance', v)} />
      <Field label={t('mobile.cards.form.personality_label')} value={form.personality} rows={3} placeholder={t('mobile.cards.form.personality_placeholder')} onChange={(v) => u('personality', v)} />
      <Field label={t('mobile.cards.form.speech_style_label')} value={form.speech_style} rows={2} placeholder={t('mobile.cards.form.speech_style_placeholder')} onChange={(v) => u('speech_style', v)} />
      <Field label={t('mobile.cards.form.current_status_label')} value={form.current_status} rows={2} placeholder={t('mobile.cards.form.current_status_placeholder')} desc={t('mobile.cards.form.current_status_desc')} onChange={(v) => u('current_status', v)} />

      {/* 叙事设定 */}
      <div className="pl-sec-head" style={{ marginTop: 8, marginBottom: 12, padding: '0 2px' }}>
        <h2>{t('mobile.cards.form.section_story')}</h2>
      </div>
      <Field label={t('mobile.cards.form.secrets_label')} value={form.secrets} rows={3} placeholder={t('mobile.cards.form.secrets_placeholder')} desc={t('mobile.cards.form.secrets_desc')} onChange={(v) => u('secrets', v)} />
      <Field label={t('mobile.cards.form.sample_dialogue_label')} value={form.sample_dialogue} rows={4} placeholder={t('mobile.cards.form.sample_dialogue_placeholder')} desc={t('mobile.cards.form.sample_dialogue_desc')} onChange={(v) => u('sample_dialogue', v)} />

      {/* 注入参数 */}
      <div className="pl-sec-head" style={{ marginTop: 8, marginBottom: 12, padding: '0 2px' }}>
        <h2>{t('mobile.cards.form.section_inject')}</h2>
      </div>
      <div className="pl-field">
        <div className="pl-slider-head">
          <span className="lab">{t('mobile.cards.form.token_budget_label')}</span>
          <span className="val">{form.token_budget}</span>
        </div>
        <input className="pl-slider" type="range" min={100} max={1200} step={20}
          value={form.token_budget} onChange={(e) => u('token_budget', +e.target.value)} />
        <div className="pl-slider-desc">{t('mobile.cards.form.token_budget_desc')}</div>
      </div>
      <Field label={t('mobile.cards.form.importance_label')} value={String(form.importance)} type="number" placeholder="100" desc={t('mobile.cards.form.importance_desc')} onChange={(v) => u('importance', v)} />
      {isNpc && (
        <Field label={t('mobile.cards.form.first_revealed_label')} value={String(form.first_revealed_chapter)} type="number" placeholder="1" desc={t('mobile.cards.form.first_revealed_desc')} onChange={(v) => u('first_revealed_chapter', v)} />
      )}
      <Field label={t('mobile.cards.form.priority_label')} value={String(form.priority)} type="number" placeholder="100" desc={t('mobile.cards.form.priority_desc')} onChange={(v) => u('priority', v)} />
      <ScopeSelect value={form.scope} onChange={(v) => u('scope', v)} isNpc={isNpc} />
      <div className="pl-group" style={{ marginBottom: 18 }}>
        <SetRow label={t('mobile.cards.form.enabled_label')} desc={t('mobile.cards.form.enabled_desc')} checked={!!form.enabled} onChange={(v) => u('enabled', v)} />
      </div>
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   卡片详情只读面板(信息 / 设定 两 Tab)
   ═══════════════════════════════════════════════════════════════════ */
function CardDetail({ card, kind, onEdit, onDuplicate, onDelete, onBack, onExportTavern, children }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState('info');
  const raw = card._raw || card;
  const fullName = raw.full_name && raw.full_name !== raw.name ? raw.full_name : null;
  const aliases = Array.isArray(raw.aliases) ? raw.aliases : [];
  const tags = Array.isArray(raw.tags) ? raw.tags : [];
  const dialogues = Array.isArray(raw.sample_dialogue) ? raw.sample_dialogue : [];
  const isPublic = !!(raw.is_public ?? card.is_public);

  const scopeLabel = { script: t('mobile.cards.scope.script'), private: t('mobile.cards.scope.private'), public: t('mobile.cards.scope.public') };
  const sourceLabel = { extracted: t('mobile.cards.detail.source_extracted'), user: t('mobile.cards.detail.source_user'), persona: t('mobile.cards.detail.source_persona'), platform: t('mobile.cards.detail.source_platform') };
  const cardTypeLabel = { npc: 'NPC', pc: t('mobile.cards.detail.type_pc'), persona: t('mobile.cards.detail.type_persona') };

  return (
    <>
      <SubHead
        title={card.name || t('mobile.cards.unnamed')}
        sub={kind === 'npc' ? t('mobile.cards.detail.sub_npc') : t('mobile.cards.detail.sub_user')}
        onBack={onBack}
        actions={
          <button className="pl-headbtn accent" onClick={onEdit} aria-label={t('common.edit')}>
            <Icon name="edit" size={17} />
          </button>
        }
      />
      <div className="pl-body tabbed">
        {/* Tab 切换 */}
        <div style={{ display: 'flex', gap: 7, padding: '10px 16px 4px', borderBottom: '1px solid var(--line-soft)' }}>
          {[{ id: 'info', l: t('mobile.cards.detail.tab_info') }, { id: 'lore', l: t('mobile.cards.detail.tab_lore') }].map((tb) => (
            <button key={tb.id} className={'pl-pill' + (tab === tb.id ? ' active' : '')} onClick={() => setTab(tb.id)}>
              {tb.l}
            </button>
          ))}
        </div>

        <div className="pl-pad">
          {/* 头像 + 名字 */}
          <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start', marginBottom: 18 }}>
            <CardAv src={raw.avatar_path || raw.avatar_url} name={card.name} enabled={raw.enabled} size={72} radius={20} zoomable />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: 'var(--font-serif)', fontSize: 20, fontWeight: 600, color: 'var(--text)' }}>
                {card.name || t('mobile.cards.unnamed')}
              </div>
              {fullName && <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 2, fontStyle: 'italic' }}>{fullName}</div>}
              {(raw.identity || card.role) && (
                <div style={{ fontSize: 13, color: 'var(--accent)', marginTop: 4 }}>
                  {String(raw.identity || card.role).slice(0, 60)}
                </div>
              )}
              {(aliases.length > 0 || tags.length > 0) && (
                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 8 }}>
                  {aliases.map((a) => <Tag key={'a' + a} label={a} />)}
                  {tags.map((tg) => <Tag key={'t' + tg} label={tg} color="green" />)}
                </div>
              )}
            </div>
          </div>

          {tab === 'info' && (
            <div className="pl-kvgrid">
              {[
                [t('mobile.cards.detail.type'), cardTypeLabel[raw.card_type] || (kind === 'npc' ? 'NPC' : t('mobile.cards.detail.type_pc'))],
                [t('mobile.cards.detail.source'), sourceLabel[raw.source] || card.origin || '—'],
                [t('mobile.cards.detail.scope'), scopeLabel[raw.scope] || t('mobile.cards.scope.private')],
                [t('mobile.cards.detail.status'), raw.enabled === false ? t('mobile.cards.detail.status_disabled') : t('mobile.cards.detail.status_enabled')],
                [t('mobile.cards.detail.importance'), raw.importance != null ? String(raw.importance) : '—'],
                ...(kind === 'npc' && raw.first_revealed_chapter > 1 ? [[t('mobile.cards.detail.first_chapter'), t('mobile.cards.detail.chapter_n', { n: raw.first_revealed_chapter })]] : []),
                [t('mobile.cards.detail.token_budget'), String(raw.token_budget ?? 450)],
                [t('mobile.cards.detail.priority'), String(raw.priority ?? 100)],
                [t('mobile.cards.detail.uses'), String(card.uses || 0)],
                [t('mobile.cards.detail.updated'), card.updated || '—'],
              ].map(([k, v]) => (
                <div key={k} className="pl-kv">
                  <div className="k">{k}</div>
                  <div className="v">{v}</div>
                </div>
              ))}
              {isPublic && kind !== 'npc' && (
                <div className="pl-kv" style={{ gridColumn: '1/-1' }}>
                  <div className="k">{t('mobile.cards.detail.public_status')}</div>
                  <div className="v" style={{ color: 'var(--ok)' }}>{t('mobile.cards.detail.published')}</div>
                </div>
              )}
            </div>
          )}

          {tab === 'lore' && (
            <>
              {[
                [t('mobile.cards.form.background_label'), raw.background],
                [t('mobile.cards.form.appearance_label'), raw.appearance],
                [t('mobile.cards.form.personality_label'), raw.personality],
                [t('mobile.cards.form.speech_style_label'), raw.speech_style],
                [t('mobile.cards.form.current_status_label'), raw.current_status],
                [t('mobile.cards.form.secrets_label'), raw.secrets],
              ].map(([lbl, val]) => <ProseBlock key={lbl} label={lbl} value={val} />)}
              {dialogues.length > 0 && (
                <div className="pl-prose-block">
                  <div className="lbl">{t('mobile.cards.form.sample_dialogue_label')}</div>
                  <div style={{ display: 'grid', gap: 7 }}>
                    {dialogues.map((d, i) => (
                      <div key={i} style={{ borderLeft: '2px solid var(--accent-edge)', paddingLeft: 10, color: 'var(--text-quiet)', fontSize: 13, lineHeight: 1.65, fontFamily: 'var(--font-serif)' }}>
                        {typeof d === 'string' ? d : `${d.role ? d.role + ': ' : ''}${d.content || ''}`}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {!raw.background && !raw.appearance && !raw.personality && !raw.speech_style && !raw.current_status && !raw.secrets && dialogues.length === 0 && (
                <div className="pl-empty">{t('mobile.cards.detail.no_lore')}</div>
              )}
            </>
          )}

          {/* 操作区 */}
          <div style={{ display: 'grid', gap: 9, marginTop: 22 }}>
            <button className="pl-btn-primary" onClick={onEdit}>
              <Icon name="edit" size={16} /> {t('mobile.cards.detail.btn_edit')}
            </button>
            {kind === 'user' && (
              <button className="pl-btn-ghost" onClick={onExportTavern}>
                <Icon name="download" size={16} /> {t('mobile.cards.detail.btn_export_tavern')}
              </button>
            )}
            {kind === 'user' && (
              <button className="pl-btn-ghost" onClick={() => {
                const url = window.api?.cards?.exportPng?.(card.id);
                if (url) window.open(url, '_blank');
              }}>
                <Icon name="image" size={16} /> {t('mobile.cards.detail.btn_export_png')}
              </button>
            )}
            <button className="pl-btn-ghost" onClick={onDuplicate}>
              <Icon name="copy" size={16} /> {t('mobile.cards.detail.btn_duplicate')}
            </button>
            <button className="pl-btn-ghost danger" onClick={onDelete}>
              <Icon name="trash" size={16} /> {t('mobile.cards.detail.btn_delete')}
            </button>
            {children}
          </div>
        </div>
      </div>
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   卡片编辑器子视图
   ═══════════════════════════════════════════════════════════════════ */
function CardEditor({ card, isNew, kind, onBack, onSave, targetScripts = [], targetScriptId = '', onTargetScriptChange }) {
  const { t } = useTranslation();
  const [form, setForm] = useState(() => cardFormInit(card));
  const [saving, setSaving] = useState(false);
  const u = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const nameOk = !!form.name.trim();

  const doSave = async () => {
    if (!nameOk || saving) return;
    if (!nameOk) { nav?.toast?.(t('mobile.cards.editor.name_required'), 'warn', 'warn'); return; }
    setSaving(true);
    try {
      await onSave(cardFormPayload(form, card));
    } catch (_) {
      // 父级已 toast
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <SubHead
        title={isNew ? t('mobile.cards.editor.title_new') : t('mobile.cards.editor.title_edit', { name: card?.name || '' })}
        sub={kind === 'npc' ? 'NPC' : t('mobile.cards.editor.sub_user')}
        onBack={onBack}
        actions={
          <button className="pl-headbtn accent" onClick={doSave} disabled={!nameOk || saving} aria-label={t('common.save')}>
            {saving
              ? <span style={{ width: 17, height: 17, border: '2px solid var(--accent)', borderTopColor: 'transparent', borderRadius: 999, display: 'inline-block', animation: 'spin 0.7s linear infinite' }} />
              : <Icon name="check" size={19} />
            }
          </button>
        }
      />
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {/* 头像预览 */}
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 20 }}>
            <CardAv src={card?.avatar_path || card?.avatar_url} name={form.name} enabled={form.enabled} size={76} radius={22} />
          </div>

          {/* 新建 NPC 时选剧本 */}
          {isNew && kind === 'npc' && targetScripts.length > 0 && (
            <div className="pl-field" style={{ marginBottom: 20 }}>
              <label>{t('mobile.cards.editor.target_script_label')}</label>
              <div className="desc">{t('mobile.cards.editor.target_script_desc')}</div>
              <select className="pl-input" value={targetScriptId} onChange={(e) => onTargetScriptChange?.(e.target.value)}
                style={{ height: 46, paddingTop: 0, paddingBottom: 0 }}>
                {targetScripts.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
          )}

          <CardEditForm form={form} u={u} kind={kind} />

          <button className="pl-btn-primary" onClick={doSave} disabled={!nameOk || saving}
            style={{ opacity: nameOk && !saving ? 1 : 0.5 }}>
            {saving ? t('mobile.cards.editor.saving') : isNew ? t('mobile.cards.editor.btn_create') : t('mobile.cards.editor.btn_save')}
          </button>
        </div>
      </div>
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   酒馆导入 Sheet(底部滑出)
   ═══════════════════════════════════════════════════════════════════ */
function ImportSheet({ show, onClose, onConfirm }) {
  const { t } = useTranslation();
  const [importType, setImportType] = useState('card'); // 'card' | 'chat'
  const [mode, setMode] = useState('file'); // 'file' | 'paste'
  const [files, setFiles] = useState([]);
  const [json, setJson] = useState('');
  const [parsed, setParsed] = useState(null);
  const [parseError, setParseError] = useState('');
  const [aiSplit, setAiSplit] = useState(false);
  const [chatFile, setChatFile] = useState(null);
  const [chatParsed, setChatParsed] = useState(null);
  const [chatError, setChatError] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef(null);
  const chatFileRef = useRef(null);

  // 重置
  useEffect(() => {
    if (!show) return;
    setImportType('card'); setMode('file'); setFiles([]); setJson('');
    setParsed(null); setParseError(''); setAiSplit(false);
    setChatFile(null); setChatParsed(null); setChatError('');
  }, [show]);

  const handleFiles = (list) => {
    const MAX = 10 * 1024 * 1024;  // 10MB(对齐后端 + 桌面端;群反馈 #92:>5MB 角色卡传不上)
    const MAX_FILES = 8;
    const arr = [...list].slice(0, MAX_FILES);
    const valid = arr.filter((f) => {
      if (!f) return false;
      if (!/\.(png|json|webp)$/i.test(f.name || '')) return false;
      if (f.size > MAX) return false;
      return true;
    });
    setFiles(valid);
    if (valid[0]) {
      const f = valid[0];
      const sizeKb = (f.size / 1024).toFixed(1);
      const fmt = f.name.match(/\.png$/i) ? 'SillyTavern · PNG v2' : 'SillyTavern · JSON';
      setParsed({
        name: f.name.replace(/\.(png|json|webp)$/i, '').replace(/[_-]/g, ' '),
        format: fmt,
        description: `${sizeKb} KB · ${t('mobile.cards.import.pending_parse')}`,
        tags: [t('mobile.cards.import.tag_import')],
        first_mes: t('mobile.cards.import.parse_after_submit'),
        _file: f,
      });
    }
  };

  const tryParseJson = () => {
    setParseError('');
    try {
      const obj = JSON.parse(json);
      // 解包常见的外层包装（如 {"ok":true,"card":{...}}）
      const inner = obj.card?.data ? obj.card : obj.character?.data ? obj.character : obj;
      const d = inner.data || {};
      const name = inner.name || inner.char_name || d.name || t('mobile.cards.unnamed');
      const desc = inner.description || d.description || t('mobile.cards.import.no_desc');
      const spec = inner.spec || obj.spec;
      const specVersion = inner.spec_version || obj.spec_version;
      setParsed({
        name,
        format: spec ? `${spec} · ${specVersion || 'v1'}` : 'JSON',
        description: desc.length > 120 ? desc.slice(0, 120) + '…' : desc,
        tags: inner.tags || d.tags || [],
        first_mes: inner.first_mes || d.first_mes || '—',
        _jsonString: json,
      });
    } catch (e) {
      setParseError(t('mobile.cards.import.json_parse_fail', { msg: e.message }));
      setParsed(null);
    }
  };

  const handleChatFile = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (!/\.(jsonl?)$/i.test(f.name || '')) { setChatError(t('mobile.cards.import.chat_type_error')); return; }
    if (f.size > 20 * 1024 * 1024) { setChatError(t('mobile.cards.import.chat_size_error')); return; }
    setChatFile(f); setChatError('');
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const lines = ev.target.result.split('\n').filter((l) => l.trim());
        const header = JSON.parse(lines[0] || '{}');
        setChatParsed({
          charName: header.character_name || header.char_name || f.name.replace(/\.jsonl?$/i, ''),
          userName: header.user_name || 'User',
          msgCount: lines.slice(1).filter((l) => l.trim()).length,
          sizeKb: (f.size / 1024).toFixed(1),
          _text: ev.target.result,
        });
      } catch { setChatError(t('mobile.cards.import.file_parse_fail')); }
    };
    reader.readAsText(f);
  };

  const doConfirm = () => {
    if (importType === 'card') {
      if (!parsed) return;
      if (parsed._file) onConfirm({ type: 'card', file: parsed._file, aiSplit });
      else if (parsed._jsonString) onConfirm({ type: 'card_json', json_string: parsed._jsonString, aiSplit });
    } else {
      if (!chatParsed?._text) return;
      onConfirm({ type: 'chat', jsonl: chatParsed._text, charName: chatParsed.charName });
    }
  };

  const canSubmit = importType === 'card' ? !!parsed && !parseError : !!chatParsed && !chatError;

  return (
    <div className="sheet-wrap" style={{ position: 'fixed', inset: 0, zIndex: 60, pointerEvents: show ? 'auto' : 'none' }}>
      <div className="sheet-scrim" style={{ opacity: show ? 1 : 0 }} onClick={onClose} />
      <div className="sheet" style={{ transform: show ? 'translateY(0)' : 'translateY(101%)', maxHeight: '88%' }}>
        <div className="sheet-grip" />
        <div className="sheet-title">{t('mobile.cards.import.title')}</div>
        <div className="sheet-sub">{t('mobile.cards.import.subtitle')}</div>

        {/* 顶层类型切换 */}
        <div style={{ display: 'flex', gap: 7, marginBottom: 14 }}>
          {[{ id: 'card', l: t('mobile.cards.import.tab_card') }, { id: 'chat', l: t('mobile.cards.import.tab_chat') }].map((tb) => (
            <button key={tb.id} className={'pl-pill' + (importType === tb.id ? ' active' : '')} onClick={() => setImportType(tb.id)}>
              {tb.l}
            </button>
          ))}
        </div>

        {/* ── 角色卡导入 ── */}
        {importType === 'card' && (
          <>
            <div style={{ display: 'flex', gap: 7, marginBottom: 14 }}>
              {[{ id: 'file', l: t('mobile.cards.import.mode_file') }, { id: 'paste', l: t('mobile.cards.import.mode_paste') }].map((tb) => (
                <button key={tb.id} className={'pl-pill' + (mode === tb.id ? ' active' : '')} onClick={() => setMode(tb.id)}>
                  {tb.l}
                </button>
              ))}
            </div>

            {mode === 'file' && (
              <>
                <div
                  style={{
                    border: `2px dashed ${dragOver ? 'var(--accent)' : 'var(--line-strong)'}`,
                    borderRadius: 14, padding: '28px 16px', textAlign: 'center',
                    background: dragOver ? 'var(--accent-soft)' : 'var(--bg)',
                    cursor: 'pointer', marginBottom: 12,
                  }}
                  onClick={() => fileRef.current?.click()}
                  onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
                >
                  <Icon name="upload" size={28} style={{ color: dragOver ? 'var(--accent)' : 'var(--muted)', marginBottom: 10, display: 'block', margin: '0 auto 10px' }} />
                  <div style={{ fontSize: 14.5, fontWeight: 600, color: dragOver ? 'var(--accent)' : 'var(--text)' }}>
                    {dragOver ? t('mobile.cards.import.drop_release') : t('mobile.cards.import.drop_hint')}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--muted-2)', marginTop: 5 }}>{t('mobile.cards.import.drop_limits')}</div>
                  <input ref={fileRef} type="file" accept=".png,.json,.webp" multiple style={{ display: 'none' }}
                    onChange={(e) => handleFiles(e.target.files)} />
                </div>
                {files.length > 0 && (
                  <div style={{ display: 'grid', gap: 4, marginBottom: 12 }}>
                    {files.map((f, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderRadius: 9, background: 'var(--bg-deep)', fontSize: 12 }}>
                        <Icon name={f.name.endsWith('.png') || f.name.endsWith('.webp') ? 'image' : 'file'} size={13} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
                        <span style={{ color: 'var(--muted-2)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{fmtBytes(f.size)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

            {mode === 'paste' && (
              <>
                <div className="pl-field" style={{ marginBottom: 10 }}>
                  <label>{t('mobile.cards.import.json_label')}</label>
                  <textarea className="pl-input" rows={6} value={json} onChange={(e) => setJson(e.target.value)}
                    placeholder={'{\n  "name": "...",\n  "description": "...",\n  "first_mes": "..."\n}'}
                    style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }} />
                </div>
                <button className="pl-btn-ghost" style={{ marginBottom: 12 }} onClick={tryParseJson} disabled={!json.trim()}>
                  <Icon name="check" size={14} /> {t('mobile.cards.import.btn_parse_json')}
                </button>
                {parseError && (
                  <div style={{ padding: '9px 12px', borderRadius: 9, background: 'var(--danger-soft)', border: '1px solid rgba(200,103,93,0.3)', color: 'var(--danger)', fontSize: 12.5, marginBottom: 10 }}>
                    <Icon name="warn" size={12} style={{ marginRight: 6 }} /> {parseError}
                  </div>
                )}
              </>
            )}

            {/* 预览卡 */}
            {parsed && (
              <div style={{ border: '1px solid var(--line-soft)', borderRadius: 12, padding: '12px 14px', background: 'var(--panel)', marginBottom: 12 }}>
                <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--muted-2)', marginBottom: 8 }}>
                  {t('mobile.cards.import.preview_label')} · {parsed.format}
                </div>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 10 }}>
                  <div style={{ width: 44, height: 44, borderRadius: 13, background: 'var(--info-soft)', border: '1px solid rgba(122,166,194,0.3)', display: 'grid', placeItems: 'center', color: 'var(--info)', font: '600 20px var(--font-serif)', flexShrink: 0, overflow: 'hidden', position: 'relative' }}>
                    {parsed._imageUrl ? (
                      <img src={parsed._imageUrl} alt={parsed.name} loading="lazy" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block', position: 'absolute', inset: 0 }} onError={(e) => { e.currentTarget.style.display = 'none'; }} />
                    ) : null}
                    {parsed.name.slice(0, 1)}
                  </div>
                  <div>
                    <div style={{ fontSize: 15, fontFamily: 'var(--font-serif)', color: 'var(--text)', fontWeight: 600 }}>{parsed.name}</div>
                    <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 2 }}>{parsed.description}</div>
                  </div>
                </div>
                {parsed.tags?.length > 0 && (
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                    {parsed.tags.map((tg) => <Tag key={tg} label={tg} />)}
                  </div>
                )}
              </div>
            )}

            {/* AI 字段拆分 opt-in */}
            <div className="pl-group" style={{ marginBottom: 12 }}>
              <SetRow
                label={t('mobile.cards.import.ai_split_label')}
                desc={t('mobile.cards.import.ai_split_desc')}
                checked={aiSplit}
                onChange={setAiSplit}
              />
            </div>
          </>
        )}

        {/* ── 聊天记录导入 ── */}
        {importType === 'chat' && (
          <>
            <div className="pl-note" style={{ marginBottom: 12 }}>
              {t('mobile.cards.import.chat_hint_pre')} <strong>SillyTavern .jsonl</strong> {t('mobile.cards.import.chat_hint_post')}
            </div>
            <button className="pl-btn-ghost" style={{ marginBottom: 12 }}
              onClick={() => chatFileRef.current?.click()}>
              <Icon name="upload" size={15} /> {t('mobile.cards.import.chat_btn_file')}
            </button>
            <input ref={chatFileRef} type="file" accept=".jsonl,.json" style={{ display: 'none' }} onChange={handleChatFile} />
            {chatFile && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px', borderRadius: 9, background: 'var(--bg-deep)', fontSize: 12, marginBottom: 10 }}>
                <Icon name="file" size={13} style={{ color: 'var(--accent)', flexShrink: 0 }} />
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{chatFile.name}</span>
                <span style={{ color: 'var(--muted-2)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{(chatFile.size / 1024).toFixed(1)} KB</span>
              </div>
            )}
            {chatError && (
              <div style={{ padding: '9px 12px', borderRadius: 9, background: 'var(--danger-soft)', color: 'var(--danger)', fontSize: 12.5, marginBottom: 10 }}>
                <Icon name="warn" size={12} style={{ marginRight: 6 }} />{chatError}
              </div>
            )}
            {chatParsed && (
              <div style={{ border: '1px solid var(--line-soft)', borderRadius: 12, padding: '12px 14px', background: 'var(--panel)', marginBottom: 12 }}>
                <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--muted-2)', marginBottom: 8 }}>{t('mobile.cards.import.chat_preview_label')}</div>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                  <div style={{ width: 40, height: 40, borderRadius: 12, background: 'var(--ok-soft)', display: 'grid', placeItems: 'center', color: 'var(--ok)', font: '600 18px var(--font-serif)', flexShrink: 0 }}>
                    {chatParsed.charName.slice(0, 1)}
                  </div>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>{chatParsed.charName}</div>
                    <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 2 }}>
                      {t('mobile.cards.import.chat_preview_stats', { count: chatParsed.msgCount, size: chatParsed.sizeKb, user: chatParsed.userName })}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {/* 底部按钮 */}
        <div className="sheet-actions" style={{ marginTop: 4 }}>
          <button className="sheet-btn" onClick={onClose}>{t('common.cancel')}</button>
          <button className="sheet-btn primary" onClick={doConfirm} disabled={!canSubmit}
            style={{ opacity: canSubmit ? 1 : 0.45 }}>
            <Icon name="check" size={15} /> {importType === 'chat' ? t('mobile.cards.import.btn_import_chat') : (files.length > 1 ? t('mobile.cards.import.btn_import_n', { n: files.length }) : t('mobile.cards.import.btn_import'))}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   删除确认 Sheet
   ═══════════════════════════════════════════════════════════════════ */
function DeleteSheet({ show, name, onClose, onConfirm }) {
  const { t } = useTranslation();
  return (
    <div className="sheet-wrap" style={{ position: 'fixed', inset: 0, zIndex: 61, pointerEvents: show ? 'auto' : 'none' }}>
      <div className="sheet-scrim" style={{ opacity: show ? 1 : 0 }} onClick={onClose} />
      <div className="sheet" style={{ transform: show ? 'translateY(0)' : 'translateY(101%)' }}>
        <div className="sheet-grip" />
        <div className="sheet-title">{t('mobile.cards.delete.title')}</div>
        <div className="confirm-preview">{t('mobile.cards.delete.message', { name })}</div>
        <div className="confirm-note"><strong>{t('mobile.cards.delete.irreversible')}</strong></div>
        <div className="sheet-actions">
          <button className="sheet-btn" onClick={onClose}>{t('common.cancel')}</button>
          <button className="sheet-btn danger" onClick={onConfirm}>
            <Icon name="trash" size={15} /> {t('mobile.cards.delete.confirm_btn')}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   我的角色卡(user)列表视图
   ═══════════════════════════════════════════════════════════════════ */
function UserView({ nav }) {
  const { t } = useTranslation();
  const [view, setView] = useState('list'); // 'list' | 'detail' | 'edit'
  const [cards, setCards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [filter, setFilter] = useState('all'); // 'all' | 'pinned' | 'public'
  const [selected, setSelected] = useState(null);
  const [showImport, setShowImport] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);

  const reload = useCallback(async () => {
    try {
      const r = await window.api.cards.myList();
      const list = Array.isArray(r) ? r : (r?.cards || r?.items || []);
      setCards(list.map((c) => ({
        id: String(c.id),
        name: c.name || t('mobile.cards.unnamed'),
        role: c.identity || c.role || '—',
        origin: c.origin || '—',
        bio: c.description || c.summary || c.bio || c.personality || c.current_status || c.appearance || '',
        tags: c.tags || [],
        pinned: !!c.pinned,
        is_public: !!c.is_public,
        uses: c.uses || 0,
        updated: window.__fmt?.ago(c.updated_at) || c.updated_at || '—',
        _raw: c,
      })));
    } catch (_) {
      // 匿名/离线下忽略
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { reload(); }, [reload]);
  useEffect(() => {
    const h = () => reload();
    window.addEventListener('rpg-user-cards-updated', h);
    return () => window.removeEventListener('rpg-user-cards-updated', h);
  }, [reload]);

  let filtered = cards;
  if (filter === 'pinned') filtered = filtered.filter((c) => c.pinned);
  if (filter === 'public') filtered = filtered.filter((c) => c.is_public);
  if (q) filtered = filtered.filter((c) =>
    (c.name + c.role + c.bio + (c.tags || []).join(' ')).toLowerCase().includes(q.toLowerCase())
  );

  const onSave = async (vals) => {
    try {
      await window.api.cards.myUpsert(vals);
      nav.toast(vals.id ? t('mobile.cards.toast.saved') : t('mobile.cards.toast.created'), 'ok', 'check');
      setView('list');
      setSelected(null);
      reload();
    } catch (e) {
      nav.toast(t('mobile.cards.toast.save_fail'), 'danger', 'warn');
    }
  };

  const onImport = async (payload) => {
    try {
      if (payload.type === 'card' && payload.file) {
        await window.api.cards.importTavern(payload.file, { aiSplit: payload.aiSplit });
      } else if (payload.type === 'card_json' && payload.json_string) {
        await window.api.cards.importJson({ json_string: payload.json_string, ai_split: payload.aiSplit });
      } else if (payload.type === 'chat' && payload.jsonl) {
        const title = payload.charName ? t('mobile.cards.import.chat_save_title', { name: payload.charName }) : undefined;
        await window.api.chats.importTavern({ jsonl: payload.jsonl, title });
        nav.toast(t('mobile.cards.toast.chat_imported'), 'ok', 'check');
        setShowImport(false);
        return;
      }
      nav.toast(t('mobile.cards.toast.imported'), 'ok', 'check');
      setShowImport(false);
      reload();
    } catch (e) {
      nav.toast(t('mobile.cards.toast.import_fail', { msg: e?.message || '' }), 'danger', 'warn');
    }
  };

  const onDuplicate = async (c) => {
    try {
      const src = c._raw || {};
      const body = { ...src, id: undefined, slug: undefined, name: (src.name || c.name) + t('mobile.cards.toast.duplicate_suffix') };
      await window.api.cards.myUpsert(body);
      nav.toast(t('mobile.cards.toast.duplicated'), 'ok', 'copy');
      reload();
    } catch (e) {
      nav.toast(t('mobile.cards.toast.duplicate_fail'), 'danger', 'warn');
    }
  };

  const onDelete = async () => {
    if (!deleteTarget) return;
    try {
      await window.api.cards.myDelete(deleteTarget.id);
      nav.toast(t('mobile.cards.toast.deleted', { name: deleteTarget.name }), 'ok', 'trash');
      setDeleteTarget(null);
      setView('list');
      setSelected(null);
      reload();
    } catch (e) {
      nav.toast(t('mobile.cards.toast.delete_fail'), 'danger', 'warn');
    }
  };

  const onExportTavern = (c) => {
    const url = window.api.cards.exportTavern(c.id);
    window.open(url, '_blank');
  };

  const onSetPublic = async (c, pub) => {
    try {
      await window.api.cards.setPublic(c.id, pub);
      nav.toast(pub ? t('mobile.cards.toast.published') : t('mobile.cards.toast.unpublished'), 'ok', 'check');
      reload();
    } catch (e) {
      nav.toast(t('mobile.cards.toast.op_fail'), 'danger', 'warn');
    }
  };

  // ── 编辑子视图 ──
  if (view === 'edit') {
    return (
      <>
        <CardEditor
          card={selected?._raw || selected}
          isNew={!selected}
          kind="user"
          onBack={() => setView(selected ? 'detail' : 'list')}
          onSave={onSave}
        />
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </>
    );
  }

  // ── 详情子视图 ──
  if (view === 'detail' && selected) {
    return (
      <>
        <CardDetail
          card={selected}
          kind="user"
          onBack={() => setView('list')}
          onEdit={() => setView('edit')}
          onDuplicate={() => onDuplicate(selected)}
          onDelete={() => setDeleteTarget(selected)}
          onExportTavern={() => onExportTavern(selected)}
        />
        <DeleteSheet
          show={!!deleteTarget}
          name={deleteTarget?.name || ''}
          onClose={() => setDeleteTarget(null)}
          onConfirm={onDelete}
        />
      </>
    );
  }

  // ── 列表视图 ──
  return (
    <>
      <div className="pl-head">
        <div className="pl-head-title">
          <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 20 }}>{t('mobile.cards.user.title')}</strong>
          <span className="sub">{t('mobile.cards.user.count', { count: cards.length })}</span>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={() => setShowImport(true)} aria-label={t('mobile.cards.user.btn_import')}>
            <Icon name="upload" size={17} />
          </button>
          <button className="pl-headbtn accent" onClick={() => { setSelected(null); setView('edit'); }} aria-label={t('mobile.cards.user.btn_new')}>
            <Icon name="plus" size={20} />
          </button>
        </div>
      </div>

      {/* 搜索 */}
      <div className="pl-toolbar">
        <div className="pl-search">
          <Icon name="search" size={15} style={{ flexShrink: 0 }} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('mobile.cards.user.search_placeholder')} />
        </div>
      </div>

      {/* 筛选 pill */}
      <div className="pl-seg-scroll" style={{ paddingTop: 0, paddingBottom: 10 }}>
        {[{ id: 'all', l: t('common.all') }, { id: 'pinned', l: t('mobile.cards.user.filter_pinned') }, { id: 'public', l: t('mobile.cards.user.filter_public') }].map((tb) => (
          <button key={tb.id} className={'pl-pill' + (filter === tb.id ? ' active' : '')} onClick={() => setFilter(tb.id)}>
            {tb.l}
          </button>
        ))}
      </div>

      <div className="pl-body tabbed">
        <div className="pl-pad" style={{ paddingTop: 4 }}>
          {loading && cards.length === 0 && (
            <div className="pl-empty">{t('common.loading')}</div>
          )}
          {!loading && filtered.length === 0 && (
            <div className="pl-empty">
              {q ? t('mobile.cards.user.empty_search') : filter !== 'all' ? t('mobile.cards.user.empty_filter') : t('mobile.cards.user.empty_all')}
            </div>
          )}
          {/* 卡网格 */}
          <div className="pl-grid">
            {filtered.map((c) => (
              <button key={c.id} className="pl-charcard" onClick={() => { setSelected(c); setView('detail'); }}>
                <div className="av accent mc-card-av-wrap" style={{ position: 'relative' }}>
                  <CardAv fill src={c._raw?.avatar_path || c._raw?.avatar_url} name={c.name} />
                  {c.enabled === false && <span className="off-dot" />}
                  {c.pinned && <span style={{ position: 'absolute', top: 7, left: 7, fontSize: 10, color: 'var(--accent)', background: 'var(--accent-soft)', padding: '2px 5px', borderRadius: 6, zIndex: 1 }}>{t('mobile.cards.user.badge_pinned')}</span>}
                  {c.is_public && <span style={{ position: 'absolute', bottom: 7, right: 7, fontSize: 9, color: 'var(--ok)', background: 'var(--ok-soft)', padding: '2px 5px', borderRadius: 6, zIndex: 1 }}>{t('mobile.cards.user.badge_public')}</span>}
                </div>
                <div className="cc-body">
                  <div className="cc-name">{c.name}</div>
                  <div className="cc-id">{c.role !== '—' ? c.role : ''}</div>
                  <div className="cc-desc" style={{ minHeight: 34 }}>{c.bio || '—'}</div>
                  <div className="cc-foot">
                    <Icon name="layers" size={11} />
                    {c.origin !== '—' ? c.origin : t('mobile.cards.user.origin_generic')}
                    <span style={{ flex: 1 }} />
                    {c.uses > 0 ? t('mobile.cards.user.uses_count', { count: c.uses }) : c.updated}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>

      <ImportSheet show={showImport} onClose={() => setShowImport(false)} onConfirm={onImport} />
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   NPC 卡视图
   ═══════════════════════════════════════════════════════════════════ */
function NpcView({ nav }) {
  const { t } = useTranslation();
  const [view, setView] = useState('list');
  const [cards, setCards] = useState([]);
  const [scripts, setScripts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [scriptFilter, setScriptFilter] = useState('all');
  const [selected, setSelected] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [newScriptId, setNewScriptId] = useState('');

  const reload = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const sr = await window.api.scripts.list();
      const scriptList = Array.isArray(sr) ? sr : (sr?.items || sr?.scripts || []);
      setScripts(scriptList);
      if (!scriptList.length) { setCards([]); setLoading(false); return; }
      const lists = await Promise.all(scriptList.map(async (s) => {
        try {
          const r = await window.api.cards.scriptList(s.id);
          const arr = Array.isArray(r) ? r : (r?.items || r?.cards || []);
          return arr.map((c) => ({
            id: String(c.id),
            name: c.name || t('mobile.cards.unnamed'),
            role: c.identity || c.role || '—',
            save: s.title || t('mobile.cards.npc.script_n', { id: s.id }),
            script_id: s.id,
            bio: c.appearance || c.personality || c.summary || c.description || '',
            tags: Array.isArray(c.tags) ? c.tags : [],
            uses: c.uses || 0,
            updated: window.__fmt?.ago(c.updated_at) || c.updated_at || '—',
            _raw: c,
          }));
        } catch (_) { return []; }
      }));
      setCards(lists.flat());
    } catch (e) {
      setError(e?.message || t('mobile.cards.npc.load_fail'));
      setCards([]);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const scriptKeys = [...new Set(cards.map((c) => String(c.script_id)))].filter((k) => k && k !== 'null');
  const titleOfScript = (sid) => {
    const s = scripts.find((x) => String(x.id) === String(sid));
    return s?.title || cards.find((c) => String(c.script_id) === String(sid))?.save || t('mobile.cards.npc.script_n', { id: sid });
  };

  let filtered = cards;
  if (scriptFilter !== 'all') filtered = filtered.filter((c) => String(c.script_id) === scriptFilter);
  if (q) filtered = filtered.filter((c) =>
    (String(c.name) + String(c.role) + String(c.bio) + (c.tags || []).join(' ')).toLowerCase().includes(q.toLowerCase())
  );

  const scriptOptions = scripts.map((s) => ({ value: String(s.id), label: s.title || t('mobile.cards.npc.script_n', { id: s.id }) }));

  useEffect(() => {
    const fallback = scriptFilter !== 'all' ? scriptFilter : scripts[0]?.id ? String(scripts[0].id) : '';
    setNewScriptId((prev) => (prev && scripts.some((s) => String(s.id) === prev) ? prev : fallback));
  }, [scripts, scriptFilter]);

  const onSaveNpc = async (vals) => {
    const sid = selected?.script_id || selected?._raw?.script_id || (scriptFilter !== 'all' ? scriptFilter : newScriptId) || (scripts.length === 1 ? String(scripts[0].id) : null);
    if (!sid) { nav.toast(t('mobile.cards.toast.npc_script_required'), 'warn', 'warn'); throw new Error('script_id required'); }
    try {
      const body = { ...vals, id: selected?._raw?.id ?? selected?.id ?? vals?.id };
      const r = await window.api.cards.scriptUpsert(sid, body);
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('mobile.cards.toast.save_fail'));
      nav.toast(vals.id ? t('mobile.cards.toast.saved') : t('mobile.cards.toast.created'), 'ok', 'check');
      setView('list');
      setSelected(null);
      reload();
    } catch (e) {
      nav.toast(t('mobile.cards.toast.save_fail_detail', { msg: e?.message || '' }), 'danger', 'warn');
      throw e;
    }
  };

  const onDelete = async () => {
    if (!deleteTarget) return;
    const sid = deleteTarget.script_id || deleteTarget._raw?.script_id;
    if (!sid) { nav.toast(t('mobile.cards.toast.npc_no_script'), 'danger', 'warn'); setDeleteTarget(null); return; }
    try {
      await window.api.cards.scriptDelete(sid, deleteTarget.id);
      nav.toast(t('mobile.cards.toast.deleted', { name: deleteTarget.name }), 'ok', 'trash');
      setDeleteTarget(null);
      setView('list');
      setSelected(null);
      reload();
    } catch (e) {
      nav.toast(t('mobile.cards.toast.delete_fail'), 'danger', 'warn');
    }
  };

  const onPromoteToUser = async (c) => {
    const raw = c._raw || c;
    const body = {
      name: c.name || raw.name || t('mobile.cards.unnamed'),
      identity: c.role || raw.identity || raw.role || '—',
      appearance: raw.appearance || c.bio || '',
      personality: raw.personality || '',
      speech_style: raw.speech_style || '',
      current_status: raw.current_status || '',
      secrets: raw.secrets || '',
      sample_dialogue: Array.isArray(raw.sample_dialogue) ? raw.sample_dialogue : [],
      tags: [...(Array.isArray(c.tags) && c.tags.length ? c.tags : []), t('mobile.cards.npc.promote_tag')],
      enabled: true,
    };
    try {
      const r = await window.api.cards.myUpsert(body);
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('mobile.cards.toast.promote_fail'));
      nav.toast(t('mobile.cards.toast.promoted', { name: body.name }), 'ok', 'check');
      try { window.dispatchEvent(new CustomEvent('rpg-user-cards-updated')); } catch (_) {}
    } catch (e) {
      nav.toast(t('mobile.cards.toast.promote_fail'), 'danger', 'warn');
    }
  };

  // ── 编辑 ──
  if (view === 'edit') {
    return (
      <>
        <CardEditor
          card={selected?._raw || selected}
          isNew={!selected}
          kind="npc"
          onBack={() => setView(selected ? 'detail' : 'list')}
          onSave={onSaveNpc}
          targetScripts={!selected ? scriptOptions : []}
          targetScriptId={!selected ? newScriptId : ''}
          onTargetScriptChange={setNewScriptId}
        />
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </>
    );
  }

  // ── 详情 ──
  if (view === 'detail' && selected) {
    return (
      <>
        <CardDetail
          card={selected}
          kind="npc"
          onBack={() => setView('list')}
          onEdit={() => setView('edit')}
          onDuplicate={() => {}}
          onDelete={() => setDeleteTarget(selected)}
          onExportTavern={() => {}}
        >
          <button className="pl-btn-ghost" style={{ marginTop: 9 }} onClick={() => onPromoteToUser(selected)}>
            <Icon name="user" size={15} /> {t('mobile.cards.npc.btn_promote')}
          </button>
        </CardDetail>
        <DeleteSheet
          show={!!deleteTarget}
          name={deleteTarget?.name || ''}
          onClose={() => setDeleteTarget(null)}
          onConfirm={onDelete}
        />
      </>
    );
  }

  // ── 列表 ──
  return (
    <>
      <div className="pl-head">
        <div className="pl-head-title">
          <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 20 }}>{t('mobile.cards.npc.title')}</strong>
          <span className="sub">{loading ? t('common.loading') : t('mobile.cards.npc.count', { count: cards.length })}</span>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={reload} aria-label={t('common.refresh')}>
            <Icon name="refresh" size={17} />
          </button>
          <button className="pl-headbtn accent" onClick={() => { setSelected(null); setView('edit'); }} aria-label={t('mobile.cards.user.btn_new')}>
            <Icon name="plus" size={20} />
          </button>
        </div>
      </div>

      {/* 搜索 */}
      <div className="pl-toolbar">
        <div className="pl-search">
          <Icon name="search" size={15} style={{ flexShrink: 0 }} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('mobile.cards.npc.search_placeholder')} />
        </div>
      </div>

      {/* 剧本筛选 */}
      <div className="pl-seg-scroll" style={{ paddingTop: 0, paddingBottom: 10 }}>
        <button className={'pl-pill' + (scriptFilter === 'all' ? ' active' : '')} onClick={() => setScriptFilter('all')}>
          {t('mobile.cards.npc.filter_all_scripts')}
        </button>
        {scriptKeys.map((sid) => (
          <button key={sid} className={'pl-pill' + (scriptFilter === sid ? ' active' : '')} onClick={() => setScriptFilter(sid)}>
            {titleOfScript(sid)}
          </button>
        ))}
      </div>

      <div className="pl-body tabbed">
        <div className="pl-pad" style={{ paddingTop: 4 }}>
          {error && (
            <div style={{ padding: '10px 14px', borderRadius: 10, background: 'var(--danger-soft)', color: 'var(--danger)', fontSize: 13, marginBottom: 14 }}>
              <Icon name="warn" size={13} style={{ marginRight: 6 }} />{error}
            </div>
          )}
          {loading && cards.length === 0 && <div className="pl-empty">{t('common.loading')}</div>}
          {!loading && filtered.length === 0 && (
            <div className="pl-empty">
              {q ? t('mobile.cards.npc.empty_search') : scriptFilter !== 'all' ? t('mobile.cards.npc.empty_script') : t('mobile.cards.npc.empty_all')}
            </div>
          )}
          <div className="pl-grid">
            {filtered.map((c) => (
              <button key={c.id} className="pl-charcard" onClick={() => { setSelected(c); setView('detail'); }}>
                <div className="av mc-card-av-wrap" style={{ position: 'relative' }}>
                  <CardAv fill src={c._raw?.avatar_path || c._raw?.avatar_url} name={c.name} />
                </div>
                <div className="cc-body">
                  <div className="cc-name">{c.name}</div>
                  <div className="cc-id">{c.role !== '—' ? c.role : ''}</div>
                  <div className="cc-desc" style={{ minHeight: 34 }}>{c.bio || '—'}</div>
                  <div className="cc-foot">
                    <Icon name="book_open" size={11} />
                    {c.save}
                    <span style={{ flex: 1 }} />
                    {c.uses > 0 ? t('mobile.cards.user.uses_count', { count: c.uses }) : c.updated}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   在线卡库视图
   ═══════════════════════════════════════════════════════════════════ */
function OnlineView({ nav }) {
  const { t } = useTranslation();
  const [items, setItems] = useState(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [error, setError] = useState('');
  const [importing, setImporting] = useState({});
  const [selected, setSelected] = useState(null); // 在线卡详情

  const load = useCallback(async (query) => {
    setLoading(true); setError('');
    try {
      const r = await window.api.cards.publicList(query ? { q: query } : undefined);
      setItems((r && r.items) || []);
    } catch (e) {
      setError(e?.message || t('mobile.cards.online.load_fail'));
      setItems([]);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { load(''); }, [load]);

  const doImport = async (c) => {
    setImporting((p) => ({ ...p, [c.id]: true }));
    try {
      await window.api.cards.cloneFromPublic(c.id);
      nav.toast(t('mobile.cards.online.imported', { name: c.name }), 'ok', 'download');
      try { window.dispatchEvent(new CustomEvent('rpg-user-cards-updated')); } catch (_) {}
      load(q);
    } catch (e) {
      nav.toast(t('mobile.cards.online.import_fail', { msg: e?.payload?.error || e?.message || '' }), 'danger', 'warn');
    } finally {
      setImporting((p) => ({ ...p, [c.id]: false }));
    }
  };

  // 在线卡详情
  if (selected) {
    const c = selected;
    const tags = c.tags || [];
    return (
      <>
        <SubHead
          title={c.name || t('mobile.cards.unnamed')}
          sub={t('mobile.cards.online.detail_sub', { author: c.owner_name || t('mobile.cards.online.anon') })}
          onBack={() => setSelected(null)}
        />
        <div className="pl-body tabbed">
          <div className="pl-pad">
            {/* 封面块 */}
            <div style={{ height: 108, borderRadius: 16, marginBottom: 16, background: 'linear-gradient(135deg, rgba(201,100,66,0.28), rgba(201,100,66,0.05))', position: 'relative', display: 'flex', alignItems: 'flex-end', padding: '12px 16px' }}>
              <span style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 5, background: 'var(--accent)', opacity: 0.6, borderRadius: '16px 0 0 16px' }} />
              <div style={{ position: 'relative' }}>
                <div style={{ fontFamily: 'var(--font-serif)', fontSize: 22, fontWeight: 600, color: 'var(--text)' }}>{c.name}</div>
                {c.identity && <div style={{ fontSize: 12, color: 'var(--text-quiet)', marginTop: 3 }}>{String(c.identity).slice(0, 50)}</div>}
              </div>
            </div>

            <p style={{ margin: '0 0 16px', fontSize: 13.5, color: 'var(--text-quiet)', lineHeight: 1.75, fontFamily: 'var(--font-serif)' }}>
              {c.personality || c.background || c.appearance || t('mobile.cards.online.no_desc')}
            </p>

            <div className="pl-kvgrid" style={{ marginBottom: 16 }}>
              {[
                [t('mobile.cards.online.kv_author'), c.owner_name || t('mobile.cards.online.anon')],
                [t('mobile.cards.online.kv_imports'), String(c.clone_count || 0)],
                [t('mobile.cards.online.kv_tags'), String((c.tags || []).length)],
                [t('mobile.cards.online.kv_identity'), c.identity ? String(c.identity).slice(0, 30) : '—'],
              ].map(([k, v]) => (
                <div key={k} className="pl-kv"><div className="k">{k}</div><div className="v">{v}</div></div>
              ))}
            </div>

            {tags.length > 0 && (
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 18 }}>
                {tags.map((tg) => <Tag key={tg} label={tg} />)}
              </div>
            )}

            <button className="pl-btn-primary" onClick={() => doImport(c)} disabled={!!importing[c.id]}
              style={{ opacity: importing[c.id] ? 0.6 : 1 }}>
              <Icon name="download" size={16} />
              {importing[c.id] ? t('mobile.cards.online.importing') : t('mobile.cards.online.btn_import')}
            </button>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="pl-head">
        <div className="pl-head-title">
          <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 20 }}>{t('mobile.cards.online.title')}</strong>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={() => load(q)} aria-label={t('common.refresh')}>
            <Icon name="refresh" size={17} />
          </button>
        </div>
      </div>

      {/* 搜索 */}
      <div className="pl-toolbar">
        <div className="pl-search">
          <Icon name="search" size={15} style={{ flexShrink: 0 }} />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') load(q); }}
            placeholder={t('mobile.cards.online.search_placeholder')} />
        </div>
        <button className="pl-headbtn" onClick={() => load(q)} aria-label={t('mobile.cards.online.btn_search')}>
          <Icon name="search" size={16} />
        </button>
      </div>

      <div className="pl-body tabbed">
        <div className="pl-pad" style={{ paddingTop: 4 }}>
          <div className="pl-note" style={{ marginBottom: 16 }}>
            {t('mobile.cards.online.browse_hint')}
          </div>

          {error && (
            <div style={{ padding: '10px 14px', borderRadius: 10, background: 'var(--danger-soft)', color: 'var(--danger)', fontSize: 13, marginBottom: 14 }}>
              <Icon name="warn" size={13} style={{ marginRight: 6 }} />{error}
            </div>
          )}

          {loading && items == null && <div className="pl-empty">{t('mobile.cards.online.loading')}</div>}
          {!loading && items?.length === 0 && (
            <div className="pl-empty">{t('mobile.cards.online.empty')}</div>
          )}

          <div style={{ display: 'grid', gap: 12 }}>
            {(items || []).map((c) => (
              <button key={c.id} className="pl-row" onClick={() => setSelected(c)}>
                <div style={{
                  width: 44, height: 44, borderRadius: 12, flexShrink: 0,
                  display: 'grid', placeItems: 'center',
                  font: '600 20px var(--font-serif)',
                  background: 'linear-gradient(140deg, rgba(201,100,66,0.2), rgba(201,100,66,0.04))',
                  color: 'var(--accent)',
                }}>
                  {(c.name || '?').slice(0, 1)}
                </div>
                <div className="pl-row-tx">
                  <strong className="serif">{c.name || t('mobile.cards.unnamed')}</strong>
                  <span style={{ color: 'var(--accent)', fontSize: 11.5 }}>
                    {c.identity ? String(c.identity).slice(0, 36) : ''}
                  </span>
                  <span style={{ ...clamp2, fontSize: 11, color: 'var(--muted)' }}>
                    {(c.personality || c.background || c.appearance || '').slice(0, 80)}
                  </span>
                  <span style={{ display: 'flex', gap: 5, alignItems: 'center', flexWrap: 'wrap', marginTop: 3 }}>
                    {(c.tags || []).slice(0, 3).map((tg) => <Tag key={tg} label={tg} />)}
                    <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--muted-2)' }}>
                      by {c.owner_name || t('mobile.cards.online.anon')} · ♥ {c.clone_count || 0}
                    </span>
                  </span>
                </div>
                <button className="pl-headbtn accent" style={{ height: 36, width: 60, borderRadius: 10, fontSize: 12.5 }}
                  onClick={(e) => { e.stopPropagation(); doImport(c); }}
                  disabled={!!importing[c.id]}>
                  {importing[c.id] ? '…' : t('mobile.cards.online.btn_import_short')}
                </button>
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

/* ═══════════════════════════════════════════════════════════════════
   根组件 MobileCards
   pageId 区分: 'cards' → 用户卡, 'cards-npc' → NPC, 'cards-online' → 在线库
   ═══════════════════════════════════════════════════════════════════ */
export function MobileCards({ nav }) {
  const { t } = useTranslation();
  // 由 pageId 决定初始 tab,也支持底部 tab pill 切换
  const initTab = () => {
    const pid = nav?.pageId || 'cards';
    if (pid === 'cards-npc') return 'npc';
    if (pid === 'cards-online') return 'online';
    return 'user';
  };
  const [tab, setTab] = useState(initTab);

  const TABS = [
    { id: 'user', l: t('mobile.cards.tabs.my') },
    { id: 'npc', l: 'NPC' },
    { id: 'online', l: t('mobile.cards.tabs.online') },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Tab 切换条(固定在顶部下方) */}
      <div className="pl-seg-scroll" style={{ flexShrink: 0, padding: '8px 16px 0', borderBottom: '1px solid var(--line-soft)', background: 'var(--bg)' }}>
        {TABS.map((t) => (
          <button key={t.id} className={'pl-pill' + (tab === t.id ? ' active' : '')} onClick={() => setTab(t.id)}>
            {t.l}
          </button>
        ))}
        <div style={{ flex: 1 }} />
      </div>

      {/* 内容区 */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {tab === 'user' && <UserView key="user" nav={nav} />}
        {tab === 'npc' && <NpcView key="npc" nav={nav} />}
        {tab === 'online' && <OnlineView key="online" nav={nav} />}
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

export default MobileCards;
