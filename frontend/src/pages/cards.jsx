/* Cards page — split out of platform-app.jsx (task 52).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: Icon / fmtBytes。 */

import React from 'react';
import { createPortal } from 'react-dom';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../game-icons.jsx';
import Modal from '../components/Modal.jsx';
import { fmtBytes, ResizableSplit } from '../platform-app.jsx';
import AgentModelPicker from '../components/AgentModelPicker.jsx';
import AvatarImg from '../components/AvatarImg.jsx';
import CharacterCardHero from '../components/CharacterCardHero.jsx';
import ImageLightbox from '../components/ImageLightbox.jsx';
import GenerateImageModal from '../components/GenerateImageModal.jsx';
// Cloudscape 原生组件(内容迁移,统一基线对齐)
import CSHeader from '@cloudscape-design/components/header';
import CSCards from '@cloudscape-design/components/cards';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSButtonDropdown from '@cloudscape-design/components/button-dropdown';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSTextFilter from '@cloudscape-design/components/text-filter';
import CSSegmentedControl from '@cloudscape-design/components/segmented-control';
import CSSelect from '@cloudscape-design/components/select';
import CSAlert from '@cloudscape-design/components/alert';
import CSTable from '@cloudscape-design/components/table';
import CSContainer from '@cloudscape-design/components/container';
import CSTabs from '@cloudscape-design/components/tabs';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import CSFormField from '@cloudscape-design/components/form-field';
import CSInput from '@cloudscape-design/components/input';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSToggle from '@cloudscape-design/components/toggle';
import CSExpandableSection from '@cloudscape-design/components/expandable-section';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';

/* ── v28 统一 CharacterCardDTO 编辑套件(NPC / PC / persona 三态共用) ──────
   后端合并三张表为 character_cards 多态表,所有读卡 API 返回同一 DTO。
   字段:name/full_name/identity/aliases/background/appearance/personality/
   speech_style/current_status/secrets/sample_dialogue/importance/
   first_revealed_chapter/token_budget/priority/enabled/scope/tags。 */
const _asLines = (v) => Array.isArray(v)
  ? v.map((x) => (typeof x === 'string' ? x : (x && (x.content || x.text)) || '')).filter(Boolean).join('\n')
  : (v || '');
const _asCsv = (v) => Array.isArray(v) ? v.join(', ') : (v || '');

// 防溢出工具:导入的酒馆卡常把整段人设塞进一个字段,长文本会把表格 / 详情
// 横向撑爆(用户反馈)。这些 helper 把长字段压成可控的单行预览 / 多行夹断,
// 完整内容仍在「设定」tab 与编辑表单里。
const _oneLine = (v, n = 90) => {
  const s = String(v || '').replace(/\s*\n+\s*/g, ' · ').replace(/\s+/g, ' ').trim();
  return s.length > n ? s.slice(0, n).trimEnd() + '…' : s;
};
// 单行省略号(需配合 maxWidth 生效)
const ELLIPSIS_1 = { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' };
// N 行夹断
const clampLines = (n) => ({
  display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: n,
  overflow: 'hidden', wordBreak: 'break-word',
});

function cardFormInit(card) {
  const c = card || {};
  return {
    name: c.name || '',
    full_name: c.full_name || '',
    identity: c.identity || c.role || '',
    aliases: _asCsv(c.aliases),
    tags: _asCsv(c.tags),
    background: c.background || '',
    appearance: c.appearance || '',
    personality: c.personality || '',
    speech_style: c.speech_style || '',
    current_status: c.current_status || '',
    secrets: c.secrets || '',
    sample_dialogue: _asLines(c.sample_dialogue),
    importance: c.importance ?? 100,
    first_revealed_chapter: c.first_revealed_chapter ?? 1,
    token_budget: c.token_budget ?? 450,
    priority: c.priority ?? 100,
    enabled: c.enabled ?? true,
    scope: c.scope || 'private',
  };
}

function cardFormPayload(form, card) {
  const trim = (s) => (s || '').trim();
  return {
    ...(card && card.id ? { id: card.id } : {}),
    name: trim(form.name),
    full_name: trim(form.full_name),
    identity: trim(form.identity),
    aliases: trim(form.aliases).split(',').map((s) => s.trim()).filter(Boolean),
    tags: trim(form.tags).split(',').map((s) => s.trim()).filter(Boolean),
    background: trim(form.background),
    appearance: trim(form.appearance),
    personality: trim(form.personality),
    speech_style: trim(form.speech_style),
    current_status: trim(form.current_status),
    secrets: trim(form.secrets),
    sample_dialogue: trim(form.sample_dialogue).split('\n').map((s) => s.trim()).filter(Boolean),
    importance: Number(form.importance) || 100,
    first_revealed_chapter: Number(form.first_revealed_chapter) || 1,
    token_budget: Number(form.token_budget) || 450,
    priority: Number(form.priority) || 100,
    enabled: !!form.enabled,
    scope: form.scope || 'private',
  };
}

// NPC 卡 → user_card(card_type='pc')payload。剧本编辑器 / 角色卡页共用,避免 shape 漂移。
// 转换 = 完整复制一份独立用户卡(含头像 URL,站内资产不重存),非指针。后端走 POST
// /api/me/character-cards(myUpsert),与 agent 工具 clone_npc_to_user_card 等价。
function npcToUserCardBody(c, { fromNpcTag = '来自NPC', unnamed = '无名角色' } = {}) {
  const raw = (c && c._raw) || c || {};
  const baseTags = Array.isArray(c && c.tags) && c.tags.length ? [...c.tags] : [];
  return {
    name: (c && c.name) || raw.name || unnamed,
    full_name: raw.full_name || '',
    aliases: Array.isArray(raw.aliases) ? raw.aliases : [],
    identity: (c && c.role) || raw.identity || raw.role || '—',
    background: raw.background || '',
    appearance: raw.appearance || (c && c.bio) || '',
    personality: raw.personality || '',
    speech_style: raw.speech_style || '',
    current_status: raw.current_status || '',
    secrets: raw.secrets || '',
    sample_dialogue: Array.isArray(raw.sample_dialogue) ? raw.sample_dialogue : [],
    avatar_path: raw.avatar_path || '',
    tags: baseTags.includes(fromNpcTag) ? baseTags : [...baseTags, fromNpcTag],
    metadata: { source: 'npc_promote', source_script_id: (c && c.script_id) || null, source_npc_id: raw.id ?? (c && c.id) },
    enabled: true,
  };
}

// 共享字段组(EC2 区块)。kind: 'npc' | 'user' | 'persona'
function CardEditFields({ form, u, kind = 'user' }) {
  const { t } = useTranslation();
  const isNpc = kind === 'npc';
  // 图3:人格 skill 卡 —— 行为由整包 skill 原文驱动(扮演时逐字注入),通用档案字段对它基本无意义。
  // 这类卡的编辑器不再平铺一堆空字段,只留名称/标签,其余原始字段折叠收起。
  const isSkill = String(form.tags || '').split(/[,，]/).map((s) => s.trim()).includes('人格skill');
  const scopeOpts = isNpc
    ? [
        { value: 'script', label: t('cards.editor.scope_script') },
        { value: 'private', label: t('cards.editor.scope_private') },
        { value: 'public', label: t('cards.editor.scope_public') },
      ]
    : [
        { value: 'private', label: t('cards.editor.scope_private') },
        { value: 'public', label: t('cards.editor.scope_public') },
      ];
  return (
    <CSSpaceBetween size="l">
      {isSkill && (
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', padding: '14px 16px', borderRadius: 12,
                      background: 'color-mix(in srgb, var(--accent, #c96442) 12%, transparent)',
                      border: '1px solid color-mix(in srgb, var(--accent, #c96442) 35%, transparent)' }}>
          <Icon name="spark" size={20} style={{ color: 'var(--accent, #c96442)', flexShrink: 0, marginTop: 2 }} />
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600, color: 'var(--text, #ebe7df)', marginBottom: 4 }}>{t('cards_page.skill_card_title')}</div>
            <div style={{ fontSize: 12.5, color: 'var(--muted, #968f85)', lineHeight: 1.6 }}>
              {t('cards_page.skill_card_desc_prefix')}
              <b style={{ color: 'var(--text-quiet, #c8c2b7)' }}>{t('cards_page.skill_card_desc_highlight')}</b>{t('cards_page.skill_card_desc_mid')}
              <b style={{ color: 'var(--text-quiet, #c8c2b7)' }}>{t('cards_page.skill_card_desc_reimport')}</b>{t('cards_page.skill_card_desc_suffix')}
            </div>
          </div>
        </div>
      )}
      <CSExpandableSection variant="container" defaultExpanded
        headerText={t('cards.editor.section_basic')}
        headerDescription={t('cards.editor.section_basic_desc')}>
        <CSColumnLayout columns={2}>
          <CSFormField label={t('cards.editor.name')} constraintText={t('cards.editor.name_required')}>
            <CSInput value={form.name} onChange={({ detail }) => u('name', detail.value)} autoFocus />
          </CSFormField>
          <CSFormField label={t('cards.editor.full_name')} description={t('cards.editor.full_name_desc')}>
            <CSInput value={form.full_name} onChange={({ detail }) => u('full_name', detail.value)} />
          </CSFormField>
          <CSFormField label={t('cards.editor.identity')}>
            <CSInput value={form.identity} onChange={({ detail }) => u('identity', detail.value)} />
          </CSFormField>
          <CSFormField label={t('cards.editor.aliases')} description={t('cards.editor.aliases_desc')}>
            <CSInput value={form.aliases} onChange={({ detail }) => u('aliases', detail.value)} />
          </CSFormField>
          <div style={{ gridColumn: '1 / -1' }}>
            <CSFormField label={t('cards.editor.tags')} description={t('cards.editor.tags_desc')}>
              <CSInput value={form.tags} onChange={({ detail }) => u('tags', detail.value)} />
            </CSFormField>
          </div>
        </CSColumnLayout>
      </CSExpandableSection>

      <CSExpandableSection variant="container" defaultExpanded={!isSkill}
        headerText={isSkill ? (t('cards.editor.section_profile') + t('cards_page.raw_fields_suffix')) : t('cards.editor.section_profile')}
        headerDescription={t('cards.editor.section_profile_desc')}>
        <CSSpaceBetween size="l">
          <CSFormField label={t('cards.editor.background')} description={t('cards.editor.background_desc')}><CSTextarea rows={3} value={form.background} onChange={({ detail }) => u('background', detail.value)} /></CSFormField>
          <CSFormField label={t('cards.editor.appearance')}><CSTextarea rows={2} value={form.appearance} onChange={({ detail }) => u('appearance', detail.value)} /></CSFormField>
          <CSFormField label={t('cards.editor.personality')}><CSTextarea rows={3} value={form.personality} onChange={({ detail }) => u('personality', detail.value)} /></CSFormField>
          <CSFormField label={t('cards.editor.speech_style')}><CSTextarea rows={2} value={form.speech_style} onChange={({ detail }) => u('speech_style', detail.value)} /></CSFormField>
          <CSFormField label={t('cards.editor.current_status')} description={t('cards.editor.current_status_desc')}><CSTextarea rows={2} value={form.current_status} onChange={({ detail }) => u('current_status', detail.value)} /></CSFormField>
        </CSSpaceBetween>
      </CSExpandableSection>

      <CSExpandableSection variant="container" defaultExpanded={!isSkill}
        headerText={isSkill ? (t('cards.editor.section_story') + t('cards_page.raw_fields_suffix')) : t('cards.editor.section_story')}
        headerDescription={t('cards.editor.section_story_desc')}>
        <CSSpaceBetween size="l">
          <CSFormField label={t('cards.editor.secrets')} description={t('cards.editor.secrets_desc')}><CSTextarea rows={3} value={form.secrets} onChange={({ detail }) => u('secrets', detail.value)} /></CSFormField>
          <CSFormField label={t('cards.editor.sample_dialogue')} description={t('cards.editor.sample_dialogue_desc')}><CSTextarea rows={4} value={form.sample_dialogue} onChange={({ detail }) => u('sample_dialogue', detail.value)} /></CSFormField>
        </CSSpaceBetween>
      </CSExpandableSection>

      <CSExpandableSection variant="container" defaultExpanded={!isSkill}
        headerText={t('cards.editor.section_inject')}
        headerDescription={t('cards.editor.section_inject_desc')}>
        <CSColumnLayout columns={2}>
          <CSFormField label={t('cards.editor.importance')} description={t('cards.editor.importance_desc')}>
            <CSInput type="number" value={String(form.importance)} onChange={({ detail }) => u('importance', detail.value)} />
          </CSFormField>
          {isNpc && (
            <CSFormField label={t('cards.editor.first_revealed_chapter')} description={t('cards.editor.first_revealed_chapter_desc')}>
              <CSInput type="number" value={String(form.first_revealed_chapter)} onChange={({ detail }) => u('first_revealed_chapter', detail.value)} />
            </CSFormField>
          )}
          <CSFormField label={t('cards.editor.token_budget')} description={t('cards.editor.token_budget_desc')}>
            <CSInput type="number" value={String(form.token_budget)} onChange={({ detail }) => u('token_budget', detail.value)} />
          </CSFormField>
          <CSFormField label={t('cards.editor.priority')} description={t('cards.editor.priority_desc')}>
            <CSInput type="number" value={String(form.priority)} onChange={({ detail }) => u('priority', detail.value)} />
          </CSFormField>
          <CSFormField label={t('cards.editor.scope')}>
            <CSSelect selectedOption={scopeOpts.find((o) => o.value === form.scope) || scopeOpts[0]}
              options={scopeOpts} onChange={({ detail }) => u('scope', detail.selectedOption.value)} />
          </CSFormField>
          <CSFormField label={t('cards.editor.enabled')}>
            <CSToggle checked={!!form.enabled} onChange={({ detail }) => u('enabled', detail.checked)}>
              {form.enabled ? t('cards.editor.enabled_on') : t('cards.editor.enabled_off')}
            </CSToggle>
          </CSFormField>
        </CSColumnLayout>
      </CSExpandableSection>
    </CSSpaceBetween>
  );
}

// 短摘要(NPC 卡面用):取最有信息量的字段前 N 字,原样不解析
function cardSnippet(c, n = 160) {
  const raw = (c && c._raw) || c || {};
  const s = String(raw.background || raw.appearance || raw.personality || raw.current_status || raw.summary || raw.description || '').trim();
  return s ? (s.length > n ? s.slice(0, n) + '…' : s) : '';
}

/* 只读角色档展示(设定 tab / 详情用)。纯展示 DTO 结构化字段,不做任何文本解析。 */
// 人格 skill 完整定义:折叠 + 按需拉取(GET 单卡)+ 高度封顶滚动 + 渲染长度封顶。
// 关键:不把 30k 原文常驻 DOM(默认折叠)、不随 /api/state 下发(按需拉),避免长 skill 内存爆。
function SkillContentSection({ cardId }) {
  const { t } = useTranslation();
  const [open, setOpen] = useStatePL(false);
  const [text, setText] = useStatePL('');
  const [loading, setLoading] = useStatePL(false);
  const [err, setErr] = useStatePL('');
  const toggle = useCallbackPL(async () => {
    if (text) { setOpen((o) => !o); return; }
    if (loading || cardId == null) return;
    setLoading(true); setErr('');
    try {
      const r = await window.api.cards.myGet(cardId);
      const c = (r && (r.card || r)) || {};
      const sc = (c.metadata && c.metadata.skill_content) || c.background || '';
      setText(sc || t('cards_page.skill_empty')); setOpen(true);
    } catch (e) { setErr(e?.message || t('cards_page.skill_load_fail')); } finally { setLoading(false); }
  }, [cardId, text, loading, t]);
  const MAX = 60000;
  const shown = text.length > MAX ? (text.slice(0, MAX) + '\n' + t('cards_page.skill_truncated')) : text;
  return (
    <div style={{ background: 'var(--panel-2, #282623)', border: '1px solid var(--line-soft, #2a2724)', borderRadius: 10, padding: '12px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <div style={{ fontSize: 11, letterSpacing: '.08em', color: 'var(--accent, #c96442)', fontWeight: 600, textTransform: 'uppercase' }}>{t('cards_page.skill_section_label')}</div>
        <button
          onClick={toggle}
          style={{ fontSize: 12, color: 'var(--accent, #c96442)', background: 'transparent', border: '1px solid var(--line, #3a352f)', borderRadius: 8, padding: '4px 12px', cursor: 'pointer' }}
        >
          {loading ? t('cards_page.skill_loading') : (open ? t('cards_page.skill_collapse') : t('cards_page.skill_view_full'))}
        </button>
      </div>
      {err && <div style={{ color: 'var(--danger, #c8675d)', fontSize: 12, marginTop: 6 }}>{err}</div>}
      {open && text && (
        <div style={{ marginTop: 10, maxHeight: 360, overflow: 'auto', whiteSpace: 'pre-wrap', lineHeight: 1.7, fontSize: 12.5, color: 'var(--text-quiet, #c8c2b7)', fontFamily: 'ui-monospace, monospace', wordBreak: 'break-word' }}>
          {shown}
        </div>
      )}
      {!open && <div style={{ marginTop: 6, fontSize: 11.5, color: 'var(--muted-2, #8a847b)' }}>{t('cards_page.skill_hint')}</div>}
    </div>
  );
}

function CardSheet({ card, kind = 'user', dense = false }) {
  const { t } = useTranslation();
  const raw = (card && card._raw) || card || {};
  const fullName = raw.full_name && raw.full_name !== raw.name ? raw.full_name : null;
  const aliases = Array.isArray(raw.aliases) ? raw.aliases : [];
  const tags = Array.isArray(raw.tags) ? raw.tags : [];
  const dialogues = Array.isArray(raw.sample_dialogue) ? raw.sample_dialogue : [];
  const chapterGate = (kind === 'npc' && raw.first_revealed_chapter > 1) ? raw.first_revealed_chapter : null;
  const initial = (raw.name || '?').trim().slice(0, 1);
  const hasBody = raw.background || raw.appearance || raw.personality || raw.speech_style || raw.current_status || raw.secrets || dialogues.length;

  const cardTypeLabel = {
    npc: t('cards.detail.type_npc'),
    pc: t('cards.detail.type_pc'),
    persona: t('cards.detail.type_persona'),
  };
  const scopeLabel = {
    script: t('cards.detail.scope_script'),
    private: t('cards.detail.scope_private'),
    public: t('cards.detail.scope_public'),
  };
  const sourceLabel = {
    extracted: t('cards.detail.source_extracted'),
    user: t('cards.detail.source_user'),
    persona: t('cards.detail.source_persona'),
    platform: t('cards.detail.source_platform'),
  };

  const block = (label, value) => value ? (
    <div style={{ background: 'var(--panel-2, #282623)', border: '1px solid var(--line-soft, #2a2724)', borderRadius: 10, padding: '12px 16px' }}>
      <div style={{ fontSize: 11, letterSpacing: '.08em', color: 'var(--accent, #c96442)', fontWeight: 600, marginBottom: 7, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.72, color: 'var(--text, #ebe7df)', fontSize: 13.5 }}>{value}</div>
    </div>
  ) : null;

  const attrs = [
    { label: t('cards.detail.type'), value: cardTypeLabel[raw.card_type] || (kind === 'npc' ? t('cards.detail.type_npc') : t('cards.detail.type_user')) },
    { label: t('cards.detail.source'), value: sourceLabel[raw.source] || t('cards.detail.source_generic') },
    { label: t('cards.detail.importance'), value: raw.importance != null ? String(raw.importance) : '—' },
    ...(chapterGate ? [{ label: t('cards.detail.first_revealed'), value: t('cards.detail.chapter_n', { n: chapterGate }) }] : []),
    { label: t('cards.detail.scope'), value: scopeLabel[raw.scope] || t('cards.detail.scope_private') },
    { label: t('cards.detail.status'), value: raw.enabled === false ? <CSStatusIndicator type="stopped">{t('cards.detail.status_disabled')}</CSStatusIndicator> : <CSStatusIndicator type="success">{t('cards.detail.status_enabled')}</CSStatusIndicator> },
    { label: t('cards.detail.token_budget'), value: String(raw.token_budget ?? 450) },
    { label: t('cards.detail.priority'), value: String(raw.priority ?? 100) },
  ];

  return (
    <CSSpaceBetween size="l">
      {/* 头部:头像首字 + 名 + 身份 + 别名/标签 */}
      <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
        <AvatarImg
          src={raw.avatar_path}
          name={raw.name || '?'}
          size={64}
          shape="rounded"
          zoomable
          className="pl-card-avatar serif"
        />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 19, fontWeight: 600, color: 'var(--text, #ebe7df)' }}>
            {raw.name || t('cards.detail.unnamed')}
            {fullName && <span style={{ fontSize: 13, color: 'var(--muted, #968f85)', marginLeft: 8, fontStyle: 'italic' }}>{fullName}</span>}
          </div>
          {raw.identity && <div style={{ fontSize: 13.5, color: 'var(--text-quiet, #c8c2b7)', marginTop: 3, ...clampLines(2) }}>{_oneLine(raw.identity, 160)}</div>}
          {(aliases.length > 0 || tags.length > 0) && (
            <div style={{ marginTop: 9 }}>
              <CSSpaceBetween direction="horizontal" size="xxs">
                {aliases.map((a) => <CSBadge key={'a' + a}>{a}</CSBadge>)}
                {tags.map((tg) => <CSBadge key={'t' + tg} color="green">{tg}</CSBadge>)}
              </CSSpaceBetween>
            </div>
          )}
        </div>
      </div>

      {/* 属性条。dense(酒馆抽屉等窄容器):Cloudscape KVP 的响应式在 ~320px 会塌成单列,
          7 个短值竖排占半屏(用户实锤「特别空看着怪」)——改用自绘双列紧凑网格。 */}
      {dense ? (
        <div className="pl-cardsheet-attrs-dense"
          style={{ background: 'var(--panel, #211f1d)', border: '1px solid var(--line-soft, #2a2724)',
                   borderRadius: 10, padding: '10px 14px', display: 'grid',
                   gridTemplateColumns: '1fr 1fr', gap: '10px 16px' }}>
          {attrs.map((a) => (
            <div key={a.label} style={{ minWidth: 0 }}>
              <div style={{ fontSize: 11, letterSpacing: '.06em', color: 'var(--muted, #968f85)',
                            marginBottom: 2 }}>{a.label}</div>
              <div style={{ fontSize: 13, color: 'var(--text, #ebe7df)', overflow: 'hidden',
                            textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.value}</div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ background: 'var(--panel, #211f1d)', border: '1px solid var(--line-soft, #2a2724)', borderRadius: 10, padding: '12px 16px' }}>
          <CSKeyValuePairs columns={4} items={attrs} />
        </div>
      )}

      {/* 人格 skill 卡:不内联 30k 原文,改折叠按需拉(防内存爆);其余字段照常 */}
      {(raw.metadata && raw.metadata.persona_skill) || tags.includes('人格skill') ? (
        <CSSpaceBetween size="s">
          <SkillContentSection cardId={raw.id} />
          {block(t('cards.detail.appearance'), raw.appearance)}
          {block(t('cards.detail.personality'), raw.personality)}
          {block(t('cards.detail.speech_style'), raw.speech_style)}
        </CSSpaceBetween>
      ) : hasBody ? (
        <CSSpaceBetween size="s">
          {block(t('cards.detail.background'), raw.background)}
          {block(t('cards.detail.appearance'), raw.appearance)}
          {block(t('cards.detail.personality'), raw.personality)}
          {block(t('cards.detail.speech_style'), raw.speech_style)}
          {block(t('cards.detail.current_status'), raw.current_status)}
          {block(t('cards.detail.secrets'), raw.secrets)}
          {dialogues.length > 0 && (
            <div style={{ background: 'var(--panel-2, #282623)', border: '1px solid var(--line-soft, #2a2724)', borderRadius: 10, padding: '12px 16px' }}>
              <div style={{ fontSize: 11, letterSpacing: '.08em', color: 'var(--accent, #c96442)', fontWeight: 600, marginBottom: 8, textTransform: 'uppercase' }}>{t('cards.detail.sample_dialogue')}</div>
              <CSSpaceBetween size="xs">
                {dialogues.map((d, i) => (
                  <div key={i} style={{ borderLeft: '2px solid var(--accent-soft, rgba(201,100,66,.4))', paddingLeft: 10, color: 'var(--text-quiet, #c8c2b7)', fontSize: 13, lineHeight: 1.6 }}>
                    {typeof d === 'string' ? d : `${d.role ? d.role + ':' : ''}${d.content || ''}`}
                  </div>
                ))}
              </CSSpaceBetween>
            </div>
          )}
        </CSSpaceBetween>
      ) : (
        <CSBox color="text-status-inactive">{t('cards.empty.no_settings')}</CSBox>
      )}
    </CSSpaceBetween>
  );
}

const USER_CARDS = [
  { id: "uc1", name: "顾承砚", role: "漂流的史官", tone: "—", origin: "雾港未尽 · 默认主角",
    bio: "南陵旧学世家出身，因雾港事件获得在三个王朝间穿越的能力。能记录但难以改变。",
    tags: ["史官", "记录者", "穿越"], pinned: true, uses: 14, updated: "12 分钟前" },
  { id: "uc2", name: "沈知微", role: "雾港医师", tone: "中立",  origin: "雾港未尽",
    bio: "雾港医馆的女医师，掌握『若残页足三，则可推时』的旧学。",
    tags: ["医师", "知情人", "女"], pinned: false, uses: 6, updated: "今天" },
  { id: "uc3", name: "阿衡", role: "灯塔守人之女", tone: "亲近", origin: "通用",
    bio: "年十四，性格倔强，会替父亲守灯塔。", tags: ["少女", "灯塔"], pinned: false, uses: 2, updated: "3 天前" },
  { id: "uc4", name: "无名旅人", role: "—", tone: "中立", origin: "通用",
    bio: "默认观察者视角，不参与剧情核心。", tags: ["观察者", "通用"], pinned: false, uses: 8, updated: "上周" },
];

const NPC_CARDS = [
  { id: "n1", name: "韩司直", role: "南陵巡检", tone: "戒备", save: "雾港·主线·顾承砚",
    bio: "南陵驻雾港巡检，正在追查史官残页线索。", tags: ["巡检", "敌意", "权威"], uses: 9, updated: "12 分钟前" },
  { id: "n2", name: "童守人", role: "灯塔守人", tone: "失踪", save: "雾港·主线·顾承砚",
    bio: "灯塔守人，与南陵童氏同源，昨夜失踪。", tags: ["失踪", "线索"], uses: 3, updated: "今天" },
  { id: "n3", name: "税吏甲", role: "码头税吏", tone: "敌意", save: "雾港·主线·顾承砚",
    bio: "正在码头打听史官的下落。", tags: ["敌意", "次要"], uses: 4, updated: "今天" },
  { id: "n4", name: "陈渡海", role: "船工", tone: "中立", save: "雾港·支线·沈知微视角",
    bio: "雾港老船工，知道海路的人。", tags: ["导引"], uses: 2, updated: "昨天" },
  { id: "n5", name: "尚书令", role: "南陵权臣", tone: "高位", save: "南陵旧灯录·开场",
    bio: "南陵当权派，掌握光绪十三年的卷宗。", tags: ["权臣", "高位"], uses: 1, updated: "上周" },
];

function CardsPage({ subPage = "user" }) {
  return (
    <div className="pl-stack">
      {subPage === "npc" ? <NpcCardsView />
        : subPage === "online" ? <OnlineCardsView />
        : <UserCardsView />}
    </div>
  );
}

/* 在线角色卡库 — 浏览并完整导入其他用户公开分享的 PC 角色卡。
   GET /api/cards/public · POST /api/cards/public/{id}/clone(完整复制进自己卡库,非指针) */
function OnlineCardsView() {
  const { t } = useTranslation();
  const [items, setItems] = React.useState(null);
  const [q, setQ] = React.useState('');
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState('');
  const [importing, setImporting] = React.useState({});

  const load = React.useCallback(async (query) => {
    setLoading(true); setErr('');
    try {
      const r = await window.api.cards.publicList(query ? { q: query } : undefined);
      setItems((r && r.items) || []);
    } catch (e) { setErr(e?.message || t('cards.page.online.load_fail')); setItems([]); }
    finally { setLoading(false); }
  }, [t]);

  React.useEffect(() => { load(''); }, [load]);

  const doImport = async (c) => {
    setImporting((p) => ({ ...p, [c.id]: true }));
    try {
      await window.api.cards.cloneFromPublic(c.id);
      window.__apiToast?.(t('cards.page.online.import_ok'), { kind: 'ok', duration: 2200, detail: t('cards.page.online.import_ok_detail', { name: c.name }) });
      load(q);  // 刷新热度
    } catch (e) {
      window.__apiToast?.(t('cards.page.online.import_fail'), { kind: 'danger', detail: e?.payload?.error || e?.message });
    } finally {
      setImporting((p) => ({ ...p, [c.id]: false }));
    }
  };

  return (
    <CSSpaceBetween size="l">
      <CSHeader
        variant="h1"
        description={t('cards.page.online.header_desc')}
        actions={<CSButton iconName="refresh" loading={loading} onClick={() => load(q)}>{t('common.refresh')}</CSButton>}
      >{t('cards.page.online.title')}</CSHeader>

      <div style={{ display: 'flex', gap: 8, maxWidth: 460 }}>
        <div style={{ flex: 1 }}>
          <CSInput value={q} onChange={({ detail }) => setQ(detail.value)} placeholder={t('cards.page.online.search_placeholder')}
            onKeyDown={(e) => { if (e.detail.key === 'Enter') load(q); }} type="search" />
        </div>
        <CSButton onClick={() => load(q)}>{t('cards.page.online.btn_search')}</CSButton>
      </div>

      {err && <CSAlert type="error" header={t('cards.page.online.load_fail')}>{err}</CSAlert>}
      {loading && items == null ? <CSBox color="text-body-secondary" padding="m">{t('cards.page.online.loading')}</CSBox>
        : (items && items.length === 0) ? <CSBox textAlign="center" color="text-body-secondary" padding={{ vertical: 'xl' }}>{t('cards.page.online.empty')}</CSBox>
        : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 14 }}>
            {(items || []).map((c) => (
              <div key={c.id} style={{ border: '1px solid var(--line, #36322d)', borderRadius: 10, padding: 14, background: 'var(--panel, #211f1d)', display: 'grid', gap: 8 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <AvatarImg src={c.avatar_path || null} name={c.name || '?'} size={40} shape="rounded" />
                    <strong style={{ fontSize: 15 }}>{c.name || t('cards.detail.unnamed')}</strong>
                  </div>
                  <span style={{ fontSize: 11, color: 'var(--text-quiet, #9a948c)' }}>♥ {c.clone_count || 0}</span>
                </div>
                {c.identity && <div style={{ fontSize: 12, color: 'var(--accent, #c96442)' }}>{String(c.identity).slice(0, 40)}</div>}
                <div style={{ fontSize: 12, color: 'var(--text-quiet, #9a948c)', lineHeight: 1.5, minHeight: 36, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  {(c.personality || c.background || c.appearance || t('cards.page.online.no_bio')).slice(0, 90)}
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {(c.tags || []).slice(0, 3).map((tg) => <CSBadge key={tg}>{tg}</CSBadge>)}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: 2 }}>
                  <span style={{ fontSize: 11, color: 'var(--muted, #b8b2a8)' }}>by {c.owner_name || t('cards.page.online.anon')}</span>
                  <CSButton variant="primary" loading={!!importing[c.id]} onClick={() => doImport(c)}>{t('cards.page.online.btn_import')}</CSButton>
                </div>
              </div>
            ))}
          </div>
        )}
    </CSSpaceBetween>
  );
}

function CardGrid({ cards, onEdit, kind, filter, empty, onDeleted, onDuplicate, onPromoteToUser }) {
  const { t } = useTranslation();
  // task 50：每张卡片的「更多」走 Cloudscape ButtonDropdown,
  // 内含 导出 PNG / 导出 SillyTavern JSON / 复制 ID / 转用户卡 / 复制为新卡 / 删除。
  const handleDelete = async (c) => {
    if (!await window.__confirm({ title: t('cards.confirm.delete_title'), message: t('cards.confirm.delete_message', { name: c.name }), danger: true, confirmText: t('cards.confirm.delete_btn') })) return;
    try {
      if (kind === "npc") {
        const sid = c.script_id || c._raw?.script_id;
        if (!sid) throw new Error(t('cards.toast.npc_script_required'));
        await window.api.cards.scriptDelete(sid, c.id);
      } else {
        await window.api.cards.myDelete(c.id);
      }
      window.__apiToast?.(t('cards.toast.deleted', { name: c.name }), { kind: "ok" });
      onDeleted && onDeleted(c);
    } catch (e) {
      window.__apiToast?.(t('cards.toast.delete_fail'), { kind: "danger", detail: e?.message });
    }
  };
  const copyId = async (c) => {
    try {
      await navigator.clipboard.writeText(String(c.id));
      window.__apiToast?.(t('cards.toast.id_copied'), { kind: "ok", duration: 1500 });
    } catch {
      window.__apiToast?.(t('cards.toast.copy_fail'), { kind: "danger" });
    }
  };

  // NPC 卡 → user_card 一键迁移。body 走共用 npcToUserCardBody(剧本编辑器同款),避免 shape 漂移。
  const promoteNpcToUserCard = async (c) => {
    const body = npcToUserCardBody(c, { fromNpcTag: t('cards.list.tag_from_npc'), unnamed: t('cards.detail.unnamed') });
    try {
      const r = await window.api.cards.myUpsert(body);
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('cards.toast.promote_fail'));
      window.__apiToast?.(t('cards.toast.promoted', { name: body.name }),
        { kind: "ok", duration: 2200, detail: t('cards.toast.promoted_detail') });
      if (onPromoteToUser) onPromoteToUser(r?.card || body);
    } catch (e) {
      window.__apiToast?.(t('cards.toast.promote_fail'), { kind: "danger", detail: e?.message || String(e) });
    }
  };

  const menuItems = (c) => {
    if (kind === 'npc') {
      return [
        { id: 'promote', text: t('cards.list.menu_promote'), iconName: 'add-plus' },
        { id: 'copyid', text: t('cards.list.menu_copy_id'), iconName: 'copy' },
        { id: 'delete', text: t('cards.list.menu_delete'), iconName: 'remove' },
      ];
    }
    const isPub = !!(c._raw?.is_public ?? c.is_public);
    return [
      { id: 'png', text: t('cards.list.menu_export_png'), href: window.api.cards.exportPng(c.id), external: true, iconName: 'file' },
      { id: 'tavern', text: t('cards.list.menu_export_tavern'), href: window.api.cards.exportTavern(c.id), external: true, iconName: 'download' },
      { id: 'copyid', text: t('cards.list.menu_copy_id'), iconName: 'copy' },
      ...(onDuplicate ? [{ id: 'dup', text: t('cards.list.menu_duplicate'), iconName: 'copy' }] : []),
      isPub
        ? { id: 'unpublish', text: t('cards.list.menu_unpublish', { defaultValue: '取消公开' }), iconName: 'lock-private' }
        : { id: 'publish', text: t('cards.list.menu_publish', { defaultValue: '发布到在线库' }), iconName: 'share' },
      { id: 'delete', text: t('cards.list.menu_delete'), iconName: 'remove' },
    ];
  };
  const setPublic = async (c, pub) => {
    try {
      await window.api.cards.setPublic(c.id, pub);
      window.__apiToast?.(pub
        ? t('cards.toast.published', { defaultValue: '已发布到在线角色卡库', name: c.name })
        : t('cards.toast.unpublished', { defaultValue: '已取消公开', name: c.name }), { kind: 'ok' });
      onDeleted && onDeleted(c);  // 复用 reload 信号刷新列表
    } catch (e) {
      window.__apiToast?.(t('cards.toast.publish_fail', { defaultValue: '操作失败' }), { kind: 'danger', detail: e?.message || String(e) });
    }
  };
  const onMenu = (c, id) => {
    if (id === 'copyid') copyId(c);
    else if (id === 'dup') onDuplicate?.(c);
    else if (id === 'delete') handleDelete(c);
    else if (id === 'promote') promoteNpcToUserCard(c);
    else if (id === 'publish') setPublic(c, true);
    else if (id === 'unpublish') setPublic(c, false);
    // png / tavern 由 ButtonDropdown href 自动打开新标签,无需 onMenu 处理
  };

  return (
    <CSCards
      items={cards}
      trackBy="id"
      filter={filter}
      empty={empty}
      cardsPerRow={[{ cards: 1 }, { minWidth: 420, cards: 2 }, { minWidth: 820, cards: 3 }]}
      cardDefinition={{
        header: (c) => (
          <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
            <AvatarImg src={(c._raw?.avatar_path) || c.avatar_path || null} name={c.name} size={56} shape="rounded" zoomable />
            <CSBox key="name" variant="h3" padding="n">{c.name}</CSBox>
            {c.pinned && <CSBadge key="pin" color="blue">{t('cards.list.pinned')}</CSBadge>}
            {(c._raw?.is_public ?? c.is_public) && kind !== 'npc' && (
              <CSBadge key="pub" color="green">{t('cards.list.published', { defaultValue: '已公开' })}</CSBadge>
            )}
          </CSSpaceBetween>
        ),
        sections: [
          { id: 'meta', content: (c) => (
            <CSSpaceBetween direction="horizontal" size="xs">
              {c.role && c.role !== '—' && <CSBadge key="role">{c.role}</CSBadge>}
              {c.tone && c.tone !== '—' && <CSBadge key="tone" color="grey">{c.tone}</CSBadge>}
            </CSSpaceBetween>
          ) },
          { id: 'bio', content: (c) => <div style={{ ...clampLines(3), fontSize: 13, color: 'var(--text-quiet, #968f85)' }}>{c.bio || '—'}</div> },
          { id: 'tags', content: (c) => (c.tags?.length
            ? <CSSpaceBetween direction="horizontal" size="xxs">{c.tags.map((tg) => <CSBadge key={tg}>{tg}</CSBadge>)}</CSSpaceBetween>
            : null) },
          { id: 'foot', content: (c) => (
            <CSBox fontSize="body-s" color="text-status-inactive">
              {(kind === 'npc' ? c.save : c.origin)} · {t('cards.list.uses_count', { count: c.uses })} · {c.updated}
            </CSBox>
          ) },
          { id: 'actions', content: (c) => (
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton variant="inline-link" iconName="edit" onClick={() => onEdit(c)}>{t('cards.list.btn_edit')}</CSButton>
              <CSButtonDropdown variant="inline-icon" ariaLabel={t('cards.list.more_actions')} expandToViewport
                items={menuItems(c)} onItemClick={({ detail }) => onMenu(c, detail.id)} />
            </CSSpaceBetween>
          ) },
        ],
      }}
    />
  );
}

function UserCardsView() {
  const { t } = useTranslation();
  // task 47：登录态零 mock。原 useState(USER_CARDS) 初始就显示 顾承砚/沈知微/阿衡/无名旅人
  // 这套示例卡片，reload 拿到真数据再覆盖。匿名时 reload 失败仍保留 USER_CARDS（designer offline）。
  const IS_ANON = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  const [cards, setCards] = useStatePL(IS_ANON ? USER_CARDS : []);
  const [filter, setFilter] = useStatePL("all");
  const [q, setQ] = useStatePL("");
  const [adding, setAdding] = useStatePL(false);
  const [importing, setImporting] = useStatePL(false);
  const [selectedId, setSelectedId] = useStatePL(null);

  const reload = React.useCallback(async () => {
    try {
      const r = await window.api.cards.myList();
      const list = Array.isArray(r) ? r : (r?.cards || r?.items || []);
      setCards(list.map(c => ({
        id: String(c.id),
        name: c.name,
        role: c.identity || c.role || "—",
        tone: c.tone || "—",
        origin: c.origin || t('cards.list.origin_generic'),
        bio: c.description || c.summary || c.bio || c.personality || c.current_status || c.appearance || "",
        tags: c.tags || [],
        pinned: !!c.pinned,
        is_public: !!c.is_public,
        uses: c.uses || 0,
        updated: window.__fmt?.ago(c.updated_at) || c.updated_at || "—",
        _raw: c,
      })));
    } catch (_) {}
  }, [t]);
  useEffectPL(() => { reload(); }, [reload]);
  // 监听 NPC 迁移事件 → 自动刷新用户角色卡列表，
  // 让用户切到用户卡 tab 就能看到刚迁移过来的卡。
  useEffectPL(() => {
    const onUpd = () => reload();
    window.addEventListener("rpg-user-cards-updated", onUpd);
    return () => window.removeEventListener("rpg-user-cards-updated", onUpd);
  }, [reload]);

  // task 100: modal 现在直接发 DB 字段名 (name/identity/personality/appearance/
  // speech_style/secrets/tags),不再做中间映射,也不再传 tone/pinned 等死字段。
  const onSaveCard = async (vals) => {
    try {
      await window.api.cards.myUpsert(vals);
      window.__apiToast?.(adding ? t('cards.toast.added') : t('cards.toast.saved'), { kind: "ok" });
      setAdding(false);
      reload();
    } catch (e) {
      window.__apiToast?.(t('cards.toast.save_fail'), { kind: "danger", detail: e?.message });
    }
  };

  const onImport = async (payload) => {
    try {
      if (payload?.type === "card" && payload.file) {
        await window.api.cards.importTavern(payload.file, { aiSplit: payload.aiSplit });
      } else if (payload?.type === "card_json" && payload.json_string) {
        await window.api.cards.importJson({ json_string: payload.json_string, ai_split: payload.aiSplit });
      } else if (payload?.type === "chat" && payload.jsonl) {
        const title = payload.charName ? t('cards.page.import.chat_title_prefix', { name: payload.charName }) : undefined;
        await window.api.chats.importTavern({ jsonl: payload.jsonl, title });
        window.__apiToast?.(t('cards.toast.chat_imported'), { kind: "ok" });
        setImporting(false);
        return;
      } else if (payload?.file) {
        // legacy fallback
        await window.api.cards.importTavern(payload.file);
      } else if (payload?.json) {
        await window.api.cards.importJson({ json: payload.json });
      }
      window.__apiToast?.(t('cards.toast.imported'), { kind: "ok" });
      setImporting(false);
      reload();
    } catch (e) {
      window.__apiToast?.(t('cards.toast.import_fail'), { kind: "danger", detail: e?.message });
    }
  };

  let filtered = cards;
  if (filter === "pinned") filtered = filtered.filter(c => c.pinned);
  if (q) filtered = filtered.filter(c => (c.name + c.role + c.bio + (c.tags || []).join(" ")).toLowerCase().includes(q.toLowerCase()));

  const selected = cards.find((x) => x.id === selectedId) || null;
  const onDuplicate = async (c) => {
    try {
      const src = c._raw || {};
      const body = { ...src, id: undefined, slug: undefined, name: (src.name || c.name) + t('cards.list.duplicate_suffix') };
      await window.api.cards.myUpsert(body);
      window.__apiToast?.(t('cards.toast.duplicated'), { kind: "ok" });
      reload();
    } catch (e) { window.__apiToast?.(t('cards.toast.duplicate_fail'), { kind: "danger", detail: e?.message }); }
  };
  const onDeleteCard = async (c) => {
    if (!await window.__confirm({ title: t('cards.confirm.delete_title'), message: t('cards.confirm.delete_message', { name: c.name }), danger: true, confirmText: t('cards.confirm.delete_btn') })) return;
    try {
      await window.api.cards.myDelete(c.id);
      window.__apiToast?.(t('cards.toast.deleted', { name: c.name }), { kind: "ok" });
      setSelectedId(null);
      setCards(cs => cs.filter(x => x.id !== c.id)); reload();
    } catch (e) { window.__apiToast?.(t('cards.toast.delete_fail'), { kind: "danger", detail: e?.message }); }
  };

  const detailEl = selected ? (
    <CardDetailPanel
      card={selected}
      kind="user"
      onSave={async (vals) => { await onSaveCard({ ...(selected._raw?.id ? { id: selected._raw.id } : { id: selected.id }), ...vals }); }}
      onDuplicate={() => onDuplicate(selected)}
      onDelete={() => onDeleteCard(selected)}
    />
  ) : null;

  const tableEl = (
    <CSTable
      variant="container"
      trackBy="id"
      selectionType="single"
      items={filtered}
      selectedItems={selected ? [selected] : []}
      onSelectionChange={({ detail }) => { const x = detail.selectedItems[0]; if (x) setSelectedId(x.id); }}
      onRowClick={({ detail }) => setSelectedId(detail.item.id)}
      empty={<CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>{q ? t('cards.empty.no_match') : t('cards.empty.no_user_cards')}</CSBox>}
      columnDefinitions={[
        { id: 'name', header: t('cards.list.col_card'), cell: (c) => (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, maxWidth: 'min(560px, 46vw)' }}>
            <AvatarImg src={(c._raw?.avatar_path) || c.avatar_path || null} name={c.name} size={36} shape="rounded" />
            <div style={{ minWidth: 0 }}>
              <CSBox fontWeight="bold">{c.name}</CSBox>
              <div style={{ ...ELLIPSIS_1, fontSize: 12.5, color: 'var(--text-quiet, #968f85)' }}>
                {_oneLine(c.role !== '—' ? c.role : c.bio, 80)}
              </div>
            </div>
          </div>
        ) },
        { id: 'tags', header: t('cards.list.col_tags'), cell: (c) => (c.tags?.length
          ? <CSSpaceBetween direction="horizontal" size="xxs">{c.tags.slice(0, 4).map((tg) => <CSBadge key={tg}>{tg}</CSBadge>)}</CSSpaceBetween>
          : <CSBox color="text-status-inactive">—</CSBox>) },
        { id: 'uses', header: t('cards.list.col_uses'), cell: (c) => t('cards.list.uses_count', { count: c.uses }) },
        { id: 'updated', header: t('cards.list.col_updated'), cell: (c) => c.updated },
      ]}
    />
  );

  return (
    <>
      <CSSpaceBetween size="l">
        <CSHeader
          variant="h1"
          counter={`(${cards.length})`}
          description={t('cards.list.user_cards_desc')}
          actions={
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton iconName="download" onClick={() => setImporting(true)}>{t('cards.import.btn_import')}</CSButton>
              <CSButton variant="primary" iconName="add-plus" onClick={() => setAdding(true)}>{t('cards.list.btn_add')}</CSButton>
            </CSSpaceBetween>
          }
        >{t('cards.list.user_cards_title')}</CSHeader>

        <CSSpaceBetween direction="horizontal" size="xs">
          <div style={{ minWidth: 260 }}>
            <CSTextFilter filteringText={q} filteringPlaceholder={t('cards.list.search_placeholder')}
              onChange={({ detail }) => setQ(detail.filteringText)} />
          </div>
          <CSSegmentedControl selectedId={filter}
            options={[{ id: 'all', text: t('cards.list.filter_all') }, { id: 'pinned', text: t('cards.list.filter_pinned') }]}
            onChange={({ detail }) => setFilter(detail.selectedId)} />
        </CSSpaceBetween>

        {selected
          ? <ResizableSplit storageKey="cards" top={tableEl} bottom={detailEl} />
          : tableEl}

      </CSSpaceBetween>
      {adding && (
        <CardEditModal
          card={null}
          isNew
          kind="user"
          onClose={() => setAdding(false)}
          onSave={onSaveCard}
        />
      )}
      <TavernImportModal open={importing} onClose={() => setImporting(false)} onConfirm={onImport} />
    </>
  );
}

/* 人设图历史画廊 — 仅 persona/pc 卡显示 */
function PersonaImageGallery({ cardId, onAvatarRefresh }) {
  const { t } = useTranslation();
  const [images, setImages] = useStatePL(null);   // null=未加载, []=空
  const [loading, setLoading] = useStatePL(false);
  const [setting, setSetting] = useStatePL(null); // 正在 set-current 的 image_id

  const load = useCallbackPL(async () => {
    setLoading(true);
    try {
      const r = await window.api.cards.personaImages(cardId);
      setImages(Array.isArray(r) ? r : (r?.images || r?.items || []));
    } catch (e) {
      window.__apiToast?.(t('cards.page.persona.gallery_load_fail'), { kind: 'danger', detail: e?.message });
      setImages([]);
    } finally { setLoading(false); }
  }, [cardId, t]);

  // 挂载时自动加载
  useEffectPL(() => { load(); }, [load]);
  // 生图完成(SSE rpg-image-updated,kind=persona)→ 自动刷新缩略图,无需手动刷新
  useEffectPL(() => {
    const h = (ev) => { const d = (ev && ev.detail) || {}; if (d.op === 'ready' && (d.payload?.kind || '') === 'persona') load(); };
    window.addEventListener('rpg-image-updated', h);
    return () => window.removeEventListener('rpg-image-updated', h);
  }, [load]);

  const doSetCurrent = async (img) => {
    if (img.is_current || setting) return;
    setSetting(img.id);
    try {
      await window.api.cards.personaSetCurrent(cardId, img.id);
      window.__apiToast?.(t('cards.page.persona.set_current_ok'), { kind: 'ok', duration: 2000 });
      // 刷新列表
      await load();
      // 通知父组件更新头像显示
      if (onAvatarRefresh) onAvatarRefresh(img.image_url);
    } catch (e) {
      window.__apiToast?.(t('cards.page.persona.set_current_fail'), { kind: 'danger', detail: e?.message });
    } finally { setSetting(null); }
  };

  const fmtDate = (s) => {
    if (!s) return '—';
    try {
      const d = new Date(s);
      return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch (_) { return s; }
  };
  const sourceLabel = {
    auto_sync: t('cards.page.persona.source_auto'),
    manual: t('cards.page.persona.source_manual'),
    import: t('cards.page.persona.source_import'),
  };

  if (loading && images === null) {
    return <CSBox color="text-body-secondary" padding="s">{t('cards.page.persona.gallery_loading')}</CSBox>;
  }
  if (!images || images.length === 0) {
    return (
      <CSBox color="text-body-secondary" padding="s">
        {t('cards.page.persona.gallery_empty')}
      </CSBox>
    );
  }

  return (
    <CSSpaceBetween size="m">
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <CSButton iconName="refresh" variant="inline-link" loading={loading} onClick={load}>{t('common.refresh')}</CSButton>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        {images.map((img) => {
          const isCurrent = !!img.is_current;
          const isSettingThis = setting === img.id;
          return (
            <div
              key={img.id}
              onClick={() => doSetCurrent(img)}
              style={{
                width: 110,
                cursor: isCurrent ? 'default' : 'pointer',
                borderRadius: 8,
                border: isCurrent
                  ? '2px solid var(--accent, #c96442)'
                  : '2px solid var(--line, #36322d)',
                overflow: 'hidden',
                background: 'var(--panel, #211f1d)',
                opacity: isSettingThis ? 0.6 : 1,
                transition: 'border-color .15s, opacity .15s',
                flexShrink: 0,
              }}
            >
              <div style={{ width: 110, height: 110, overflow: 'hidden', position: 'relative' }}>
                <AvatarImg
                  src={img.image_url}
                  name="?"
                  size={110}
                  shape="square"
                  style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                />
                {isCurrent && (
                  <div style={{
                    position: 'absolute', bottom: 0, left: 0, right: 0,
                    background: 'rgba(201,100,66,.85)', color: '#fff',
                    fontSize: 10, textAlign: 'center', padding: '2px 0', fontWeight: 600, letterSpacing: '.04em',
                  }}>{t('cards.page.persona.badge_current')}</div>
                )}
              </div>
              <div style={{ padding: '5px 7px', fontSize: 10.5, color: 'var(--text-quiet, #9a948c)', lineHeight: 1.5 }}>
                <div>{sourceLabel[img.source] || img.source || '—'}</div>
                <div style={{ color: 'var(--muted, #b8b2a8)' }}>{fmtDate(img.created_at)}</div>
                {!isCurrent && (
                  <div style={{ marginTop: 3, color: 'var(--accent-soft, rgba(201,100,66,.8))', fontSize: 10 }}>
                    {isSettingThis ? t('cards.page.persona.setting_in_progress') : t('cards.page.persona.click_to_set_current')}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </CSSpaceBetween>
  );
}

/* 人设图缩略条 — 内联在卡详情左侧媒体列(海报下方)的精简画廊。
   点缩略图 → ImageLightbox 预览 + 裁剪(裁剪后存为新的当前人设图);hover 显「设为当前」。
   空(无人设图)时不渲染,不占位;完整管理仍在「人设图」tab。 */
function PersonaThumbStrip({ cardId, onAvatarRefresh }) {
  const { t } = useTranslation();
  const [images, setImages] = useStatePL(null);
  const [preview, setPreview] = useStatePL(null);

  const load = useCallbackPL(async () => {
    try {
      const r = await window.api.cards.personaImages(cardId);
      setImages(Array.isArray(r) ? r : (r?.images || r?.items || []));
    } catch (_) { setImages([]); }
  }, [cardId]);
  useEffectPL(() => { load(); }, [load]);
  // 生图完成(SSE rpg-image-updated,kind=persona)→ 自动刷新缩略图,无需手动刷新
  useEffectPL(() => {
    const h = (ev) => { const d = (ev && ev.detail) || {}; if (d.op === 'ready' && (d.payload?.kind || '') === 'persona') load(); };
    window.addEventListener('rpg-image-updated', h);
    return () => window.removeEventListener('rpg-image-updated', h);
  }, [load]);

  const setCurrent = async (img) => {
    if (img.is_current) return;
    try {
      await window.api.cards.personaSetCurrent(cardId, img.id);
      await load();
      onAvatarRefresh && onAvatarRefresh(img.image_url);
      window.__apiToast?.(t('cards.page.persona.set_current_ok'), { kind: 'ok', duration: 1500 });
    } catch (e) { window.__apiToast?.(t('common.error'), { kind: 'danger', detail: e?.message }); }
  };

  const onCrop = async (blob) => {
    const ext = (blob.type && blob.type.split('/')[1]) || 'jpg';
    const r = await window.api.cards.uploadPersonaImage(cardId, new File([blob], 'crop.' + ext, { type: blob.type || 'image/jpeg' }));
    const url = r && (r.url || r.image_url);
    await load();
    if (url && onAvatarRefresh) onAvatarRefresh(url);
    window.__apiToast?.(t('cards.page.persona.crop_saved'), { kind: 'ok', duration: 2000 });
    setPreview(null);
  };

  if (!images || images.length === 0) return null;

  return (
    <div className="pstrip">
      <div className="pstrip__head">{t('cards.page.persona.strip_title')} <span className="pstrip__count">{images.length}</span></div>
      <div className="pstrip__row">
        {images.map((img) => (
          <div key={img.id} className={`pstrip__cell${img.is_current ? ' is-current' : ''}`}>
            <img src={img.image_url} alt="" loading="lazy" onClick={() => setPreview(img.image_url)} title={t('cards.page.persona.thumb_title')} />
            {img.is_current
              ? <span className="pstrip__badge">{t('cards.page.persona.badge_current')}</span>
              : <button className="pstrip__set" onClick={() => setCurrent(img)}>{t('cards.page.persona.btn_set_current')}</button>}
          </div>
        ))}
      </div>
      <ImageLightbox open={!!preview} src={preview} onClose={() => setPreview(null)}
        onCrop={onCrop} cropHint={t('cards.page.persona.crop_hint')} />
    </div>
  );
}

/* 角色卡详情面板 —— 选中后在列表下方展开(对齐剧本/存档)。
   Tabs:角色信息(KeyValuePairs)/ 设定(只读展示)/ 角色设置(内联编辑表单)。 */
function CardDetailPanel({ card, kind, onSave, onDuplicate, onDelete }) {
  const { t } = useTranslation();
  const raw = card._raw || card;
  // 是否为 persona/pc 卡(显示人设图功能)
  const cardType = raw.card_type || (kind === 'npc' ? 'npc' : kind === 'user' ? 'persona' : kind);
  const isPersonaOrPc = cardType === 'persona' || cardType === 'pc';
  const [tab, setTab] = useStatePL('info');
  const [form, setForm] = useStatePL(null);
  const [saving, setSaving] = useStatePL(false);
  const [genAvatarOpen, setGenAvatarOpen] = useStatePL(false);
  const [avatarUrl, setAvatarUrl] = useStatePL(raw.avatar_path || null);
  // Phase 4: 人设图状态
  const [autoSync, setAutoSync] = useStatePL(!!raw.auto_image_sync);
  const [autoSyncBusy, setAutoSyncBusy] = useStatePL(false);
  const [genPersonaBusy, setGenPersonaBusy] = useStatePL(false);
  // W3-C1: 手动上传状态
  const [uploadAvatarBusy, setUploadAvatarBusy] = useStatePL(false);
  const [uploadPersonaBusy, setUploadPersonaBusy] = useStatePL(false);
  // 图2:分享到在线角色卡库(发布/取消公开)
  const [isPub, setIsPub] = useStatePL(raw.is_public === true || raw.scope === 'public');
  const [pubBusy, setPubBusy] = useStatePL(false);
  const avatarInputRef = React.useRef(null);
  const personaInputRef = React.useRef(null);
  useEffectPL(() => {
    setTab('info');
    setForm(cardFormInit(raw));
    setSaving(false);  // 切卡时重置:防上一张卡的保存挂起态残留 → 新卡保存键卡死 loading
    setAvatarUrl(raw.avatar_path || null);
    setAutoSync(!!raw.auto_image_sync);
    setIsPub(raw.is_public === true || raw.scope === 'public');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.id]);
  const u = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const doSave = async () => {
    if (!form?.name?.trim()) { window.__apiToast?.(t('cards.toast.name_required'), { kind: 'warn' }); return; }
    setSaving(true);
    try { await onSave(cardFormPayload(form, card)); }
    finally { setSaving(false); }
  };

  // 图2:分享/取消分享到在线角色卡库(api.cards.setPublic → POST /me/character-cards/{id}/visibility {public})
  const doShare = async () => {
    const next = !isPub;
    setPubBusy(true);
    try {
      await window.api.cards.setPublic(raw.id ?? card.id, next);
      setIsPub(next);
      window.__apiToast?.(next
        ? t('cards.toast.published', { defaultValue: '已分享到在线角色卡库' })
        : t('cards.toast.unpublished', { defaultValue: '已取消公开' }), { kind: 'ok', duration: 1800 });
    } catch (e) {
      window.__apiToast?.(t('cards.toast.publish_fail', { defaultValue: '操作失败' }), { kind: 'danger', detail: e?.message || String(e) });
    } finally { setPubBusy(false); }
  };

  const doToggleAutoSync = async (checked) => {
    setAutoSync(checked);
    setAutoSyncBusy(true);
    try {
      await window.api.cards.personaAutoSync(raw.id ?? card.id, checked);
      window.__apiToast?.(checked ? t('cards.page.persona.auto_sync_on') : t('cards.page.persona.auto_sync_off'), { kind: 'ok', duration: 1800 });
    } catch (e) {
      setAutoSync(!checked); // 回滚
      window.__apiToast?.(t('common.error'), { kind: 'danger', detail: e?.message });
    } finally { setAutoSyncBusy(false); }
  };

  const doGenPersonaImage = async () => {
    setGenPersonaBusy(true);
    try {
      await window.api.cards.personaGenerate(raw.id ?? card.id);
      window.__apiToast?.(t('cards.page.persona.gen_queued'), { kind: 'ok', duration: 2800 });
    } catch (e) {
      window.__apiToast?.(t('cards.page.persona.gen_fail'), { kind: 'danger', detail: e?.message });
    } finally { setGenPersonaBusy(false); }
  };

  // W3-C1: 上传头像
  const doUploadAvatar = async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    e.target.value = '';
    setUploadAvatarBusy(true);
    window.__apiToast?.(t('cards.page.persona.uploading_avatar'), { kind: 'info', duration: 2000 });
    try {
      const res = await window.api.cards.uploadAvatar(raw.id ?? card.id, file);
      if (res && res.url) setAvatarUrl(res.url);
      window.__apiToast?.(t('cards.page.persona.avatar_updated'), { kind: 'ok', duration: 2000 });
    } catch (e2) {
      window.__apiToast?.(t('cards.page.persona.upload_fail'), { kind: 'danger', detail: e2?.message });
    } finally { setUploadAvatarBusy(false); }
  };

  // W3-C1: 上传人设图
  const doUploadPersonaImage = async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    e.target.value = '';
    setUploadPersonaBusy(true);
    window.__apiToast?.(t('cards.page.persona.uploading_persona'), { kind: 'info', duration: 2000 });
    try {
      const res = await window.api.cards.uploadPersonaImage(raw.id ?? card.id, file);
      if (res && res.url) setAvatarUrl(res.url);
      window.__apiToast?.(t('cards.page.persona.persona_uploaded'), { kind: 'ok', duration: 2200 });
    } catch (e2) {
      window.__apiToast?.(t('cards.page.persona.upload_fail'), { kind: 'danger', detail: e2?.message });
    } finally { setUploadPersonaBusy(false); }
  };

  const fullName = raw.full_name && raw.full_name !== raw.name ? raw.full_name : null;
  const chapterGate = (kind === 'npc' && raw.first_revealed_chapter > 1) ? raw.first_revealed_chapter : null;

  const cardTypeLabel = { npc: t('cards.detail.type_npc'), pc: t('cards.detail.type_pc'), persona: t('cards.detail.type_persona') };
  const sourceLabel = { extracted: t('cards.detail.source_extracted'), user: t('cards.detail.source_user'), persona: t('cards.detail.source_persona'), platform: t('cards.detail.source_platform') };
  const scopeLabel = { script: t('cards.detail.scope_script'), private: t('cards.detail.scope_private'), public: t('cards.detail.scope_public') };

  const genAvatarDefaultPrompt = [raw.name, raw.appearance].filter(Boolean).join('，') || raw.name || '';

  return (
    <>
    {genAvatarOpen && (
      <GenerateImageModal
        open={genAvatarOpen}
        onClose={() => setGenAvatarOpen(false)}
        kind="card"
        attach={{ type: 'card_avatar', id: raw.id ?? card.id }}
        defaultPrompt={genAvatarDefaultPrompt}
        onDone={(url) => {
          setAvatarUrl(url);
          setGenAvatarOpen(false);
        }}
      />
    )}
    {/* W3-C1: 隐藏 file input — 头像上传 */}
    <input
      ref={avatarInputRef}
      type="file"
      accept="image/png,image/jpeg,image/webp"
      style={{ display: 'none' }}
      onChange={doUploadAvatar}
    />
    {/* W3-C1: 隐藏 file input — 人设图上传 */}
    <input
      ref={personaInputRef}
      type="file"
      accept="image/png,image/jpeg,image/webp"
      style={{ display: 'none' }}
      onChange={doUploadPersonaImage}
    />
    <CSContainer header={
      <CSHeader variant="h2"
        actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            {isPersonaOrPc && (
              <CSButton iconName="gen-ai" loading={genPersonaBusy} onClick={doGenPersonaImage}>{t('cards.page.persona.btn_gen_persona')}</CSButton>
            )}
            <CSButton variant="primary" iconName="check" loading={saving} onClick={doSave}>{t('cards.detail.btn_save')}</CSButton>
            {kind === 'user' && (
              <CSButton iconName={isPub ? 'lock-private' : 'share'} loading={pubBusy} onClick={doShare}>
                {isPub ? t('cards.detail.btn_unpublish', { defaultValue: '取消公开' }) : t('cards.detail.btn_publish', { defaultValue: '分享到在线库' })}
              </CSButton>
            )}
            <CSButton iconName="copy" onClick={onDuplicate}>{t('cards.detail.btn_duplicate')}</CSButton>
            {kind === 'user' && <CSButton href={window.api.cards.exportTavern(card.id)} target="_blank" iconName="download">{t('cards.detail.btn_export')}</CSButton>}
            <CSButton iconName="remove" onClick={onDelete}>{t('cards.detail.btn_delete')}</CSButton>
          </CSSpaceBetween>
        }
      >{card.name}{fullName && <CSBox display="inline" color="text-status-inactive" fontSize="body-s" padding={{ left: 's' }}>{fullName}</CSBox>}</CSHeader>
    }>
      {/* 图片优先的角色海报 — 宽屏左右分栏(图列左 sticky + 信息列右),窄屏堆叠 */}
      <div className="msplit">
        <div className="msplit__media">
          <CharacterCardHero
            card={{ id: raw.id, name: raw.name, identity: raw.identity || raw.role, appearance: raw.appearance, avatar_path: avatarUrl }}
            editable
            scriptId={kind === 'npc' ? (raw.script_id || card?._raw?.script_id || null) : null}
            onChanged={(u) => setAvatarUrl(u)}
          />
          {/* 海报下方内联人设图缩略条(仅 persona/pc;无图时不渲染)——点开预览支持裁剪 */}
          {isPersonaOrPc && <PersonaThumbStrip cardId={raw.id ?? card.id} onAvatarRefresh={(u) => setAvatarUrl(u)} />}
        </div>
        <div className="msplit__body">
      <CSTabs activeTabId={tab} onChange={({ detail }) => setTab(detail.activeTabId)} tabs={[
        { id: 'info', label: t('cards.detail.tab_info'), content: (
          <CSKeyValuePairs columns={4} items={[
            { label: t('cards.detail.identity'), value: (raw.identity || raw.role)
                ? <div style={{ ...clampLines(2) }}>{_oneLine(raw.identity || raw.role, 140)}</div>
                : '—' },
            ...(fullName ? [{ label: t('cards.detail.full_name'), value: fullName }] : []),
            { label: t('cards.detail.type'), value: cardTypeLabel[raw.card_type] || (kind === 'npc' ? t('cards.detail.type_npc') : t('cards.detail.type_user')) },
            { label: t('cards.detail.source'), value: sourceLabel[raw.source] || card.origin || t('cards.detail.source_generic') },
            { label: t('cards.detail.importance'), value: raw.importance != null ? String(raw.importance) : '—' },
            ...(chapterGate ? [{ label: t('cards.detail.chapter_gate'), value: <CSStatusIndicator type="info">📖 {t('cards.detail.chapter_n', { n: chapterGate })}</CSStatusIndicator> }] : []),
            { label: t('cards.detail.scope'), value: scopeLabel[raw.scope] || '—' },
            { label: t('cards.detail.status'), value: raw.enabled === false ? <CSStatusIndicator type="stopped">{t('cards.detail.status_disabled')}</CSStatusIndicator> : <CSStatusIndicator type="success">{t('cards.detail.status_enabled')}</CSStatusIndicator> },
            { label: t('cards.detail.tags_label'), value: (Array.isArray(raw.tags) && raw.tags.length) ? raw.tags.join(' · ') : '—' },
            { label: t('cards.detail.updated'), value: card.updated || '—' },
            { label: t('cards.detail.card_id'), value: <span className="mono">{card.id}</span> },
          ]} />
        ) },
        { id: 'setting', label: t('cards.detail.tab_setting'), content: <CardSheet card={card} kind={kind} /> },
        { id: 'edit', label: t('cards.detail.tab_edit'), content: form && (
          <CSSpaceBetween size="l">
            <CardEditFields form={form} u={u} kind={kind} />
            <CSBox><CSButton variant="primary" iconName="check" loading={saving} onClick={doSave}>{t('cards.detail.btn_save')}</CSButton></CSBox>
          </CSSpaceBetween>
        ) },
        // Phase 4: 人设图标签页 — 仅 persona/pc 卡显示
        ...(isPersonaOrPc ? [{
          id: 'persona_images',
          label: t('cards.page.persona.tab_label'),
          content: (
            <CSSpaceBetween size="l">
              {/* 自动维护开关 */}
              <CSContainer header={<CSHeader variant="h3">{t('cards.page.persona.auto_section_title')}</CSHeader>}>
                <CSSpaceBetween size="s">
                  <CSToggle
                    checked={autoSync}
                    disabled={autoSyncBusy}
                    onChange={({ detail }) => doToggleAutoSync(detail.checked)}
                  >
                    {t('cards.page.persona.auto_sync_label')}
                  </CSToggle>
                  <CSBox color="text-body-secondary" fontSize="body-s">
                    {t('cards.page.persona.auto_sync_desc')}
                  </CSBox>
                </CSSpaceBetween>
              </CSContainer>

              {/* 手动生成 */}
              <CSContainer header={<CSHeader variant="h3" actions={
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSButton iconName="gen-ai" loading={genPersonaBusy} onClick={doGenPersonaImage}>{t('cards.page.persona.btn_gen_now')}</CSButton>
                  <CSButton iconName="upload" loading={uploadPersonaBusy} disabled={uploadPersonaBusy}
                    onClick={() => personaInputRef.current && personaInputRef.current.click()}>{t('cards.page.persona.btn_upload_persona')}</CSButton>
                </CSSpaceBetween>
              }>{t('cards.page.persona.manual_section_title')}</CSHeader>}>
                <CSBox color="text-body-secondary" fontSize="body-s">
                  {t('cards.page.persona.manual_section_desc')}
                </CSBox>
              </CSContainer>

              {/* 历史画廊 */}
              <CSExpandableSection
                variant="container"
                headerText={t('cards.page.persona.history_title')}
                headerDescription={t('cards.page.persona.history_desc')}
                defaultExpanded
              >
                {tab === 'persona_images' && (
                  <PersonaImageGallery
                    cardId={raw.id ?? card.id}
                    onAvatarRefresh={(url) => setAvatarUrl(url)}
                  />
                )}
              </CSExpandableSection>
            </CSSpaceBetween>
          ),
        }] : []),
      ]} />
        </div>
      </div>
    </CSContainer>
    </>
  );
}

function TavernImportModal({ open, onClose, onConfirm }) {
  const { t } = useTranslation();
  // importType: "card" | "chat"
  const [importType, setImportType] = useStatePL("card");
  const [mode, setMode] = useStatePL("file");
  const [json, setJson] = useStatePL("");
  const [files, setFiles] = useStatePL([]);
  const [dragOver, setDragOver] = useStatePL(false);
  const [parseError, setParseError] = useStatePL(null);
  const [parsed, setParsed] = useStatePL(null);
  const [aiSplit, setAiSplit] = useStatePL(false);  // 用 AI 整理字段(消耗额度)
  // 整理用模型不在此重复选择 — 统一在「设置 → 模型 → AI 整理卡字段」配置。
  // chat-specific
  const [chatText, setChatText] = useStatePL("");
  const [chatFile, setChatFile] = useStatePL(null);
  const [chatParsed, setChatParsed] = useStatePL(null);
  const [chatError, setChatError] = useStatePL(null);

  React.useEffect(() => {
    if (!open) return;
    setImportType("card"); setMode("file"); setJson(""); setFiles([]);
    setParseError(null); setParsed(null); setAiSplit(false);
    setChatText(""); setChatFile(null); setChatParsed(null); setChatError(null);
  }, [open]);

  const handleFiles = (list) => {
    // task 68: size + ext 校验,防内存炸 / 类型混淆
    const MAX_BYTES = 10 * 1024 * 1024;  // 10MB / 文件(对齐后端 PNG 导入上限;群反馈 #92:>5MB 角色卡传不上)
    const MAX_FILES = 8;
    const arr = [...list].slice(0, MAX_FILES);
    if (list.length > MAX_FILES) {
      window.__apiToast?.(t('cards.page.import.too_many_files', { max: MAX_FILES }), { kind: 'warn', duration: 2400 });
    }
    const valid = arr.filter(f => {
      if (!f) return false;
      if (!/\.(png|json|webp)$/i.test(f.name || '')) {
        window.__apiToast?.(t('cards.page.import.invalid_type', { name: f.name }), { kind: 'danger', duration: 2400 });
        return false;
      }
      if (f.size > MAX_BYTES) {
        window.__apiToast?.(t('cards.page.import.file_too_large', { name: f.name, mb: MAX_BYTES / 1024 / 1024 }), { kind: 'danger', duration: 2400 });
        return false;
      }
      return true;
    });
    setFiles(valid);
    if (valid[0]) {
      const f = valid[0];
      const sizeKb = (f.size / 1024).toFixed(1);
      const fmt = f.name.match(/\.png$/i) ? "SillyTavern · PNG v2" : f.name.match(/\.json$/i) ? "SillyTavern · JSON" : f.type || "unknown";
      setParsed({
        name: f.name.replace(/\.(png|json|webp)$/i, "").replace(/[_-]/g, " "),
        format: fmt,
        description: t('cards.import.parse_pending_hint', { size: sizeKb, mime: f.type || "—" }),
        tags: [t('cards.import.tag_imported')],
        first_mes: t('cards.import.parse_pending_first_mes'),
        example_count: 0,
        _file: f,
      });
    }
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files);
  };

  const tryParseJson = () => {
    setParseError(null);
    try {
      const obj = JSON.parse(json);
      // 解包常见的外层包装（如 {"ok":true,"card":{...}}）
      const inner = obj.card?.data ? obj.card : obj.character?.data ? obj.character : obj;
      const d = inner.data || {};
      const name = inner.name || inner.char_name || d.name || t('cards.detail.unnamed');
      const desc = inner.description || d.description || t('cards.import.no_desc');
      const spec = inner.spec || obj.spec;
      const specVersion = inner.spec_version || obj.spec_version;
      setParsed({
        name,
        format: spec ? `${spec} · ${specVersion || "v1"}` : "SillyTavern · JSON",
        description: desc.length > 160 ? desc.slice(0, 160) + "…" : desc,
        tags: inner.tags || d.tags || [],
        first_mes: inner.first_mes || d.first_mes || "—",
        example_count: (inner.mes_example || d.mes_example || "").split(/<START>/).filter(Boolean).length,
        _jsonString: json,
      });
    } catch (e) {
      setParseError(t('cards.import.parse_fail', { msg: e.message }));
      setParsed(null);
    }
  };

  // chat tab: read .jsonl file
  const handleChatFile = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    // task 68: size + ext 校验
    if (!/\.(jsonl?)$/i.test(f.name || '')) {
      setChatError(t('cards.page.import.chat_invalid_ext'));
      return;
    }
    if (f.size > 20 * 1024 * 1024) {  // 20MB / chat (聊天记录可能较长)
      setChatError(t('cards.page.import.chat_too_large'));
      return;
    }
    setChatFile(f); setChatError(null); setChatParsed(null);
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target.result;
      // quick local preview: count lines, extract header
      try {
        const lines = text.split('\n').filter(l => l.trim());
        const header = JSON.parse(lines[0] || '{}');
        const msgCount = lines.slice(1).filter(l => l.trim()).length;
        setChatParsed({
          charName: header.character_name || header.char_name || f.name.replace(/\.jsonl?$/i, ""),
          userName: header.user_name || "User",
          msgCount,
          sizeKb: (f.size / 1024).toFixed(1),
          _text: text,
        });
      } catch {
        setChatError(t('cards.import.chat_parse_fail'));
      }
    };
    reader.readAsText(f);
  };

  const doConfirmCard = () => {
    if (!parsed) return;
    // 整理用模型统一走「设置 → 模型 → AI 整理卡字段」配置,这里不再透传 per-import 模型。
    if (parsed._file) {
      onConfirm({ type: "card", file: parsed._file, aiSplit });
    } else if (parsed._jsonString) {
      onConfirm({ type: "card_json", json_string: parsed._jsonString, aiSplit });
    }
  };

  const doConfirmChat = () => {
    if (!chatParsed?._text) return;
    onConfirm({ type: "chat", jsonl: chatParsed._text, charName: chatParsed.charName });
  };

  if (!open) return null;
  const canSubmitCard = parsed && !parseError;
  const canSubmitChat = chatParsed && !chatError;

  const node = (
    <Modal
      open
      eyebrow={t('cards.import.modal_eyebrow')}
      title={t('cards.import.modal_title')}
      width={640}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> {importType === "chat" ? t('cards.import.chat_footer_hint') : t('cards.import.footer_hint')}
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose}>{t('cards.import.btn_cancel')}</button>
          {importType === "card" ? (
            <button className="btn primary" onClick={doConfirmCard} disabled={!canSubmitCard}>
              <Icon name="check" size={12} /> {t('cards.import.btn_confirm', { count: files.length > 1 ? files.length : 0 })}
            </button>
          ) : (
            <button className="btn primary" onClick={doConfirmChat} disabled={!canSubmitChat}>
              <Icon name="check" size={12} /> {t('cards.import.chat_btn_confirm')}
            </button>
          )}
        </div>
      </>}
    >
        <div className="pl-modal-form">
          {/* top-level type switcher */}
          <div className="seg" style={{display: "flex"}}>
            <button className={importType === "card" ? "active" : ""} onClick={() => setImportType("card")}>
              <Icon name="user" size={12} /> {t('cards.import.type_card')}
            </button>
            <button className={importType === "chat" ? "active" : ""} onClick={() => setImportType("chat")}>
              <Icon name="chat" size={12} /> {t('cards.import.type_chat')}
            </button>
          </div>

          {/* ── Card import ─────────────────────────────────────────── */}
          {importType === "card" && (
            <>
              <div className="seg" style={{display: "flex"}}>
                <button className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}>
                  <Icon name="upload" size={12} /> {t('cards.import.tab_file')}
                </button>
                <button className={mode === "paste" ? "active" : ""} onClick={() => setMode("paste")}>
                  <Icon name="file" size={12} /> {t('cards.import.tab_paste')}
                </button>
              </div>
              {mode === "file" && (
                <>
                  <div
                    className={`pl-drop ${dragOver ? "drop-active" : ""}`}
                    onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                    onDragLeave={() => setDragOver(false)}
                    onDrop={onDrop}
                    style={{padding: "32px 16px", cursor: "pointer"}}
                    onClick={() => document.getElementById("tavern-file-input")?.click()}
                  >
                    <Icon name="upload" size={24} style={{color: dragOver ? "var(--accent)" : "var(--muted)"}} />
                    <strong style={{color: dragOver ? "var(--accent)" : "var(--text)"}}>
                      {dragOver ? t('cards.import.drop_release') : t('cards.import.drop_hint')}
                    </strong>
                    <span>{t('cards.import.drop_formats')}</span>
                    <input id="tavern-file-input" type="file" accept=".png,.json,.webp" multiple
                      style={{display: "none"}} onChange={(e) => handleFiles(e.target.files)} />
                  </div>
                  {files.length > 0 && (
                    <div style={{display: "grid", gap: 4}}>
                      {files.map((f, i) => (
                        <div key={i} style={{
                          display: "flex", alignItems: "center", gap: 8,
                          padding: "6px 10px", borderRadius: 4,
                          background: "var(--bg-deep)", fontSize: 12,
                        }}>
                          <Icon name={f.name.endsWith(".png") || f.name.endsWith(".webp") ? "image" : "file"} size={12} style={{color: "var(--accent)"}} />
                          <span className="mono" style={{flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{f.name}</span>
                          <span className="muted-2 mono" style={{fontSize: 11}}>{fmtBytes(f.size)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
              {mode === "paste" && (
                <>
                  <div className="pl-field">
                    <label>{t('cards.import.paste_label')}</label>
                    <textarea rows={10} value={json} onChange={(e) => setJson(e.target.value)}
                      className="mono" style={{fontSize: 11.5}}
                      placeholder={'{\n  "name": "...",\n  "description": "...",\n  "first_mes": "...",\n  "tags": []\n}'} />
                  </div>
                  <button className="btn ghost" onClick={tryParseJson} disabled={!json.trim()} style={{width: "fit-content"}}>
                    <Icon name="check" size={12} /> {t('cards.import.btn_parse')}
                  </button>
                  {parseError && (
                    <div className="pl-validate-step" style={{color: "var(--danger)", borderColor: "rgba(200, 103, 93, 0.32)", background: "var(--danger-soft)"}}>
                      <Icon name="warn" size={12} /> {parseError}
                    </div>
                  )}
                </>
              )}
              {parsed && (
                <div className="pl-import" style={{borderStyle: "solid", gap: 8, padding: "12px 14px"}}>
                  <div className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em"}}>{t('cards.import.preview_label')} · {parsed.format}</div>
                  <div className="pl-card-head" style={{margin: 0}}>
                    <AvatarImg src={parsed.avatar_url || parsed.avatar_path || null} name={parsed.name} size={64} shape="rounded" className="pl-card-avatar serif" />
                    <div className="pl-card-id" style={{flex: 1}}>
                      <strong>{parsed.name}</strong>
                      <span className="muted-2" style={{fontSize: 11.5}}>{t('cards.import.preview_stats', { dialogues: parsed.example_count, tags: parsed.tags?.length || 0 })}</span>
                    </div>
                  </div>
                  <p className="pl-card-bio serif" style={{margin: 0, WebkitLineClamp: 2}}>{parsed.description}</p>
                  <div style={{padding: 8, background: "var(--bg-deep)", borderRadius: 4, fontFamily: "var(--font-serif)", fontSize: 12.5, color: "var(--text-quiet)", borderLeft: "2px solid var(--accent-edge)"}}>
                    <span className="muted-2 mono" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em", display: "block", marginBottom: 4}}>{t('cards.import.first_mes_label')}</span>
                    {parsed.first_mes}
                  </div>
                  {parsed.tags?.length > 0 && (
                    <div className="pl-card-tags">
                      {parsed.tags.map(tg => <span key={tg} className="pl-cap-tag">{tg}</span>)}
                    </div>
                  )}
                </div>
              )}
              {/* AI 整理字段 opt-in:确定性规则拆不开的自由文本卡才需要,默认关闭、消耗额度 */}
              <label style={{
                display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer",
                padding: "10px 12px", borderRadius: 6,
                border: `1px solid ${aiSplit ? "var(--accent-edge, rgba(201,100,66,.5))" : "var(--line-soft, #2a2724)"}`,
                background: aiSplit ? "var(--accent-soft, rgba(201,100,66,.1))" : "var(--bg-deep)",
              }}>
                <input type="checkbox" checked={aiSplit} onChange={(e) => setAiSplit(e.target.checked)}
                  style={{ marginTop: 2, accentColor: "var(--accent, #c96442)" }} />
                <span style={{ fontSize: 12.5, lineHeight: 1.5 }}>
                  <strong style={{ color: "var(--text)" }}>{t('cards.import.ai_split_label')}</strong>
                  <span className="muted-2" style={{ display: "block", fontSize: 11.5 }}>{t('cards.import.ai_split_hint')}</span>
                </span>
              </label>
              {aiSplit && (
                <AgentModelPicker
                  prefPrefix="card_import"
                  variant="bare"
                  defaultModel={null}
                  configHash="settings-models"
                  persistOnMount
                />
              )}
            </>
          )}

          {/* ── Chat import ─────────────────────────────────────────── */}
          {importType === "chat" && (
            <>
              <div className="pl-field" style={{display: "flex", flexDirection: "column", gap: 8}}>
                <label style={{fontSize: 12.5}}>{t('cards.import.chat_hint')}</label>
                <label className="btn ghost" style={{width: "fit-content", cursor: "pointer"}}>
                  <Icon name="upload" size={12} /> {t('cards.import.chat_btn_file')}
                  <input type="file" accept=".jsonl,.json" style={{display: "none"}} onChange={handleChatFile} />
                </label>
                {chatFile && (
                  <div style={{display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderRadius: 4, background: "var(--bg-deep)", fontSize: 12}}>
                    <Icon name="file" size={12} style={{color: "var(--accent)"}} />
                    <span className="mono" style={{flex: 1}}>{chatFile.name}</span>
                    <span className="muted-2 mono" style={{fontSize: 11}}>{fmtBytes(chatFile.size)}</span>
                  </div>
                )}
                {chatError && (
                  <div className="pl-validate-step" style={{color: "var(--danger)", borderColor: "rgba(200, 103, 93, 0.32)", background: "var(--danger-soft)"}}>
                    <Icon name="warn" size={12} /> {chatError}
                  </div>
                )}
              </div>
              {chatParsed && (
                <div className="pl-import" style={{borderStyle: "solid", gap: 8, padding: "12px 14px"}}>
                  <div className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em"}}>{t('cards.import.chat_preview_label')}</div>
                  <div className="pl-card-head" style={{margin: 0}}>
                    <AvatarImg src={chatParsed?.avatar_url || null} name={chatParsed.charName} size={64} shape="rounded" className="pl-card-avatar serif" />
                    <div className="pl-card-id" style={{flex: 1}}>
                      <strong>{chatParsed.charName}</strong>
                      <span className="muted-2" style={{fontSize: 11.5}}>{t('cards.import.chat_preview_stats', { msgs: chatParsed.msgCount, user: chatParsed.userName })}</span>
                    </div>
                  </div>
                  <div style={{fontSize: 12, color: "var(--text-quiet)", padding: "6px 10px", background: "var(--bg-deep)", borderRadius: 4}}>
                    <Icon name="info" size={11} /> {t('cards.import.chat_new_save_hint')}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
    </Modal>
  );
  return createPortal(node, document.body);
}

function NpcCardsView() {
  const { t } = useTranslation();
  // task 47：之前完全用硬编码 NPC_CARDS（韩司直/童守人/税吏甲/陈渡海/尚书令），
  // 跟登录用户的真实剧本毫无关系。改成跨所有用户剧本聚合
  // /api/scripts/{id}/character-cards，按真实存档分组。
  // 用户的真实"NPC 角色卡"= 后端每个 script 下的 character_cards 表。
  const [cards, setCards] = useStatePL([]);
  const [loading, setLoading] = useStatePL(true);
  const [error, setError] = useStatePL("");
  const [saveFilter, setSaveFilter] = useStatePL("all");
  const [q, setQ] = useStatePL("");
  const [edit, setEdit] = useStatePL(null);
  const [adding, setAdding] = useStatePL(false);
  const [scripts, setScripts] = useStatePL([]);
  const [newNpcScriptId, setNewNpcScriptId] = useStatePL("");

  const reload = React.useCallback(async () => {
    setLoading(true); setError("");
    try {
      // 1) 拉所有 scripts；2) 对每个 script 并行拉 character-cards
      const sr = await window.api.scripts.list();
      const scripts = Array.isArray(sr) ? sr : (sr?.items || sr?.scripts || []);
      setScripts(scripts);
      if (!scripts.length) { setCards([]); setLoading(false); return; }
      const lists = await Promise.all(scripts.map(async (s) => {
        try {
          const r = await window.api.cards.scriptList(s.id);
          const arr = Array.isArray(r) ? r : (r?.items || r?.cards || []);
          return arr.map(c => ({
            id: String(c.id),
            name: c.name || t('cards.detail.unnamed'),
            role: c.identity || c.role || "—",
            tone: c.tone || t('cards.list.tone_neutral'),
            save: s.title || t('cards.list.script_n', { id: s.id }),
            script_id: s.id,
            bio: c.appearance || c.personality || c.summary || c.description || "",
            tags: Array.isArray(c.tags) ? c.tags : [],
            uses: c.uses || 0,
            updated: window.__fmt?.ago(c.updated_at) || c.updated_at || "—",
            pinned: !!c.pinned,
            _raw: c,
          }));
        } catch (_) { return []; }
      }));
      setCards(lists.flat());
    } catch (e) {
      setError(e?.message || t('cards.toast.npc_load_fail'));
      // 匿名 / API 不可达 → 兜底到 mock（designer offline preview）
      if (!(window.RPG_AUTH && window.RPG_AUTH.authed)) {
        setCards((NPC_CARDS || []).map(c => ({ ...c, script_id: null })));
      } else {
        setCards([]);
      }
    } finally { setLoading(false); }
  }, [t]);
  React.useEffect(() => { reload(); }, [reload]);

  // 按 script_id 筛选(不能用 c.save=剧本标题——同名剧本「未命名/新档」会互相串台,
  // 且 selectedScriptId 反查命中第一个同名剧本 → 新增 NPC 落到错误剧本)。
  const scriptKeys = [...new Set(cards.map((c) => String(c.script_id)))].filter((k) => k && k !== 'null' && k !== 'undefined');
  const titleOfScript = (sid) => {
    const s = scripts.find((x) => String(x.id) === String(sid));
    return (s && s.title) || cards.find((c) => String(c.script_id) === String(sid))?.save || t('cards.list.script_n', { id: sid });
  };
  let filtered = cards;
  if (saveFilter !== "all") filtered = filtered.filter((c) => String(c.script_id) === saveFilter);
  if (q) filtered = filtered.filter(c =>
    (String(c.name) + String(c.role) + String(c.bio) + (c.tags || []).join(" "))
      .toLowerCase().includes(q.toLowerCase())
  );

  const saveOpts = [{ value: "all", label: t('cards.list.all_scripts') }, ...scriptKeys.map((k) => ({ value: k, label: titleOfScript(k) }))];
  const selectedScriptId = saveFilter !== "all" ? saveFilter : null;
  const scriptOptions = scripts.map((s) => ({
    value: String(s.id),
    label: s.title || t('cards.list.script_n', { id: s.id }),
  }));
  useEffectPL(() => {
    const fallback = selectedScriptId || scripts[0]?.id || "";
    setNewNpcScriptId((prev) => (
      prev && scripts.some((s) => String(s.id) === String(prev))
        ? String(prev)
        : String(fallback || "")
    ));
  }, [scripts, selectedScriptId]);
  const onSaveNpc = async (payload) => {
    // #10 编辑/删除补全: 编辑用卡自身 script_id;新增时 filter=all 下无 selectedScriptId,
    // 退到 _raw.script_id / 唯一剧本(常见单档场景),避免"filter=all 新增 NPC 卡报
    // script_required 卡死"。仍无法确定(多剧本且未选)才提示用户先选剧本。
    const sid = edit?.script_id || edit?._raw?.script_id || selectedScriptId || (adding ? newNpcScriptId : null) || (scripts.length === 1 ? scripts[0].id : null);
    if (!sid) {
      window.__apiToast?.(t('cards.toast.npc_script_required'), { kind: "warn", duration: 2600 });
      throw new Error("script_id required");
    }
    const body = {
      ...payload,
      id: edit?._raw?.id ?? edit?.id ?? payload?.id,
    };
    try {
      const r = await window.api.cards.scriptUpsert(sid, body);
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('cards.toast.save_fail'));
      window.__apiToast?.(adding ? t('cards.toast.added') : t('cards.toast.saved'), { kind: "ok" });
      setEdit(null); setAdding(false);
      await reload();
    } catch (e) {
      window.__apiToast?.(t('cards.toast.save_fail'), { kind: "danger", detail: e?.message || String(e) });
      throw e;
    }
  };
  return (
    <>
      <CSSpaceBetween size="l">
        <CSHeader
          variant="h1"
          counter={`(${cards.length})`}
          description={`${t('cards.list.npc_cards_desc')}${loading ? ' ' + t('cards.list.loading') : ''}`}
          actions={<CSButton variant="primary" iconName="add-plus" onClick={() => {
            const fallback = selectedScriptId || newNpcScriptId || scripts[0]?.id || "";
            if (fallback) setNewNpcScriptId(String(fallback));
            setAdding(true);
          }}>{t('cards.list.btn_add_npc')}</CSButton>}
        >{t('cards.list.npc_cards_title')}</CSHeader>
        {error && <CSAlert type="error" header={t('cards.toast.load_fail_header')}>{error}</CSAlert>}
        <CardGrid cards={filtered} onEdit={setEdit} kind="npc"
          empty={
            <CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
              {loading ? t('cards.list.loading') : <>{t('cards.empty.no_npc_cards')}<br />{t('cards.empty.no_npc_hint')}</>}
            </CSBox>
          }
          filter={
            <CSSpaceBetween direction="horizontal" size="xs">
              <div style={{ minWidth: 240 }}>
                <CSTextFilter filteringText={q} filteringPlaceholder={t('cards.list.search_npc_placeholder')}
                  onChange={({ detail }) => setQ(detail.filteringText)} />
              </div>
              <CSSelect selectedOption={saveOpts.find((o) => o.value === saveFilter)}
                options={saveOpts} disabled={loading}
                onChange={({ detail }) => setSaveFilter(detail.selectedOption.value)} />
            </CSSpaceBetween>
          }
          onPromoteToUser={() => {
            // 迁移到 user_card 后通知用户角色卡列表刷新(如果当前 mounted)
            try { window.dispatchEvent(new CustomEvent("rpg-user-cards-updated")); } catch (_) {}
          }}
          onDeleted={() => reload()} />
      </CSSpaceBetween>
      {(edit || adding) && (
        <CardEditModal
          card={edit?._raw || edit}
          isNew={adding}
          kind="npc"
          targetScriptOptions={adding ? scriptOptions : []}
          targetScriptId={adding ? newNpcScriptId : ""}
          onTargetScriptChange={setNewNpcScriptId}
          onClose={() => { setEdit(null); setAdding(false); }}
          onSave={onSaveNpc}
        />
      )}
    </>
  );
}

/* 角色卡编辑器 —— EC2 式单页多模块全屏表单(对齐新建存档)。
   覆盖 user_character_cards 全部角色相关列:name / identity / aliases / tags /
   appearance / personality / speech_style / current_status / secrets /
   sample_dialogue / token_budget / priority / enabled / scope。 */
function CardEditModal({ card, isNew, kind, onClose, onSave, onPromote, targetScriptOptions = [], targetScriptId = "", onTargetScriptChange }) {
  const { t } = useTranslation();
  const [form, setForm] = useStatePL(() => cardFormInit(card));
  const [submitting, setSubmitting] = useStatePL(false);
  const [promoting, setPromoting] = useStatePL(false);
  const [avatarUrl, setAvatarUrl] = useStatePL(card?._raw?.avatar_path || card?.avatar_path || null);
  const u = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const nameOk = !!form.name.trim();
  const editCardId = card?._raw?.id || card?.id || null;
  const editScriptId = kind === 'npc' ? (card?._raw?.script_id || targetScriptId || null) : null;

  const doSave = async () => {
    if (!nameOk || submitting) return;
    setSubmitting(true);
    try {
      // payload 构造放进 try:个别卡字段类型异常时(理论上不应发生)别让整段静默吞掉。
      const payload = cardFormPayload(form, card);
      await onSave?.(payload);
    } catch (e) {
      // 关键:原来这里 catch(_){} 把**任何**错误(payload 构造抛错 / onSave 同步抛错)
      // 静默吞掉,用户表现为「保存按钮点了没反应」(群反馈)。改为显式 toast,暴露真因。
      // 父级 onSave 自己已 toast 的网络错走它那条;这里兜的是 payload/同步异常。
      try {
        window.__apiToast?.(t('cards.editor.save_fail', { defaultValue: '保存失败' }),
          { kind: 'danger', detail: (e && e.message) || String(e) });
      } catch (_) { /* toast 不可用也不该再抛 */ }
      // eslint-disable-next-line no-console
      console.error('[CardEditModal] save failed:', e);
    } finally { setSubmitting(false); }
  };

  const node = (
    <div style={{ position: 'fixed', top: 'var(--nav-h, 53px)', left: 0, right: 0, bottom: 0, zIndex: 1000, background: 'var(--bg, #1a1817)', overflow: 'auto' }}>
      {/* 顶部栏(位于平台顶栏下方,保留平台导航) */}
      <div style={{ position: 'sticky', top: 0, zIndex: 3, background: '#131211', borderBottom: '1px solid #36322d' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', padding: '13px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
          <div style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 600, color: '#ebe7df' }}>
            {isNew ? t('cards.editor.modal_title_new') : t('cards.editor.modal_title_edit')}{kind === 'user' ? t('cards.editor.kind_user') : t('cards.editor.kind_npc')}
          </div>
          <CSButton iconName="close" variant="link" onClick={onClose}>{t('cards.editor.btn_cancel')}</CSButton>
        </div>
      </div>

      <div style={{ maxWidth: 1100, margin: '0 auto', padding: '20px 24px 80px' }}>
        <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
          {/* 左:共享字段组(NPC/PC/persona 三态统一) */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <CardEditFields form={form} u={u} kind={kind} />
          </div>

          {/* 右:概要 + 保存(sticky) */}
          <div style={{ width: 300, flexShrink: 0, position: 'sticky', top: 72 }}>
            <CSContainer header={<CSHeader variant="h2">{t('cards.editor.summary_title')}</CSHeader>}>
              <CSSpaceBetween size="m">
                {/* 当前头像预览 */}
                {/* 头像编辑(海报 + MediaStudio 生成/上传/图库 + 预览裁剪);新卡需先保存才有 id */}
                {!isNew && editCardId ? (
                  <div style={{ maxWidth: 260, margin: '0 auto 4px' }}>
                    <CharacterCardHero
                      card={{ id: editCardId, name: form.name, identity: form.identity, appearance: form.appearance, avatar_path: avatarUrl }}
                      editable scriptId={editScriptId}
                      onChanged={(uu) => { setAvatarUrl(uu); try { window.dispatchEvent(new CustomEvent('rpg-user-cards-updated')); } catch (_) {} }}
                    />
                  </div>
                ) : (isNew ? (
                  <CSBox color="text-body-secondary" fontSize="body-s" textAlign="center">{t('cards.editor.avatar_after_save', { defaultValue: '保存后可设置头像' })}</CSBox>
                ) : null)}
                <CSStatusIndicator type={nameOk ? 'success' : 'pending'}>{t('cards.editor.name_required_status')}</CSStatusIndicator>
                {kind === 'npc' && isNew && targetScriptOptions.length > 0 && (
                  <CSFormField label={t('cards.editor.target_script')} description={t('cards.editor.target_script_desc')}>
                    <CSSelect
                      selectedOption={targetScriptOptions.find((o) => o.value === String(targetScriptId)) || targetScriptOptions[0]}
                      options={targetScriptOptions}
                      disabled={targetScriptOptions.length <= 1}
                      onChange={({ detail }) => onTargetScriptChange?.(detail.selectedOption.value)}
                    />
                  </CSFormField>
                )}
                <CSKeyValuePairs columns={1} items={[
                  { label: t('cards.editor.name'), value: form.name.trim() || '—' },
                  { label: t('cards.editor.identity'), value: form.identity.trim() || '—' },
                  { label: t('cards.editor.scope'), value: form.scope === 'public' ? t('cards.detail.scope_public') : t('cards.detail.scope_private') },
                  { label: t('cards.editor.enabled'), value: form.enabled ? t('cards.editor.enabled_on') : t('cards.editor.enabled_off') },
                ]} />
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <CSButton variant="primary" disabled={!nameOk || submitting} loading={submitting} onClick={doSave}>
                    {isNew ? t('cards.editor.btn_create') : t('cards.editor.btn_save')}
                  </CSButton>
                  {/* NPC 卡:编辑页内直接「转为用户角色卡」(复制到自己名下,不改原剧本)。
                      仅已存在的 NPC 卡 + 调用方传了 onPromote 时显示。 */}
                  {kind === 'npc' && !isNew && onPromote && (
                    <CSButton iconName="add-plus" disabled={promoting} loading={promoting}
                      onClick={async () => { setPromoting(true); try { await onPromote(card); } finally { setPromoting(false); } }}>
                      {t('cards.editor.btn_promote_npc', { defaultValue: '转为用户角色卡' })}
                    </CSButton>
                  )}
                  <CSButton variant="link" onClick={onClose}>{t('cards.editor.btn_cancel')}</CSButton>
                </div>
              </CSSpaceBetween>
            </CSContainer>
          </div>
        </div>
      </div>
    </div>
  );
  return createPortal(node, document.body);
}

export { CardsPage, CardGrid, UserCardsView, NpcCardsView, CardEditModal, TavernImportModal, CardSheet, cardSnippet, CardEditFields, cardFormInit, cardFormPayload, npcToUserCardBody };
