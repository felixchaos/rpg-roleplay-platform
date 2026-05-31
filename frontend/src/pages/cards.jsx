/* Cards page — split out of platform-app.jsx (task 52).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: Icon / fmtBytes。 */

import React from 'react';
import { createPortal } from 'react-dom';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import { Icon } from '../game-icons.jsx';
import { fmtBytes, ResizableSplit } from '../platform-app.jsx';
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
  const t = (s) => (s || '').trim();
  return {
    ...(card && card.id ? { id: card.id } : {}),
    name: t(form.name),
    full_name: t(form.full_name),
    identity: t(form.identity),
    aliases: t(form.aliases).split(',').map((s) => s.trim()).filter(Boolean),
    tags: t(form.tags).split(',').map((s) => s.trim()).filter(Boolean),
    background: t(form.background),
    appearance: t(form.appearance),
    personality: t(form.personality),
    speech_style: t(form.speech_style),
    current_status: t(form.current_status),
    secrets: t(form.secrets),
    sample_dialogue: t(form.sample_dialogue).split('\n').map((s) => s.trim()).filter(Boolean),
    importance: Number(form.importance) || 100,
    first_revealed_chapter: Number(form.first_revealed_chapter) || 1,
    token_budget: Number(form.token_budget) || 450,
    priority: Number(form.priority) || 100,
    enabled: !!form.enabled,
    scope: form.scope || 'private',
  };
}

const _SCOPE_OPTS_NPC = [
  { value: 'script', label: '剧本内(随剧本)' },
  { value: 'private', label: '私有(仅自己可见)' },
  { value: 'public', label: '公开(可分享)' },
];
const _SCOPE_OPTS_USER = [
  { value: 'private', label: '私有(仅自己可见)' },
  { value: 'public', label: '公开(可分享)' },
];

// 共享字段组(EC2 区块)。kind: 'npc' | 'user' | 'persona'
function CardEditFields({ form, u, kind = 'user' }) {
  const isNpc = kind === 'npc';
  const scopeOpts = isNpc ? _SCOPE_OPTS_NPC : _SCOPE_OPTS_USER;
  const h2 = (t, d) => <CSHeader variant="h2" description={d}>{t}</CSHeader>;
  return (
    <CSSpaceBetween size="l">
      <CSContainer header={h2('基本信息', '姓名必填;全名为欧美式全名,别名/标签逗号分隔。')}>
        <CSColumnLayout columns={2}>
          <CSFormField label="姓名" constraintText="必填">
            <CSInput value={form.name} onChange={({ detail }) => u('name', detail.value)} autoFocus />
          </CSFormField>
          <CSFormField label="全名 (full name)" description="欧美式全名,留空则与姓名同">
            <CSInput value={form.full_name} onChange={({ detail }) => u('full_name', detail.value)} />
          </CSFormField>
          <CSFormField label="身份 / 职位">
            <CSInput value={form.identity} onChange={({ detail }) => u('identity', detail.value)} />
          </CSFormField>
          <CSFormField label="别名" description="逗号分隔,GM 据此识别同一角色的多种称呼">
            <CSInput value={form.aliases} onChange={({ detail }) => u('aliases', detail.value)} />
          </CSFormField>
          <div style={{ gridColumn: '1 / -1' }}>
            <CSFormField label="标签" description="逗号分隔">
              <CSInput value={form.tags} onChange={({ detail }) => u('tags', detail.value)} />
            </CSFormField>
          </div>
        </CSColumnLayout>
      </CSContainer>

      <CSContainer header={h2('人物画像', '前史 / 外貌 / 性格 / 语气 / 当前状态。')}>
        <CSSpaceBetween size="l">
          <CSFormField label="前史 / 背景" description="角色登场前的来历"><CSTextarea rows={3} value={form.background} onChange={({ detail }) => u('background', detail.value)} /></CSFormField>
          <CSFormField label="外貌"><CSTextarea rows={2} value={form.appearance} onChange={({ detail }) => u('appearance', detail.value)} /></CSFormField>
          <CSFormField label="性格 / 设定"><CSTextarea rows={3} value={form.personality} onChange={({ detail }) => u('personality', detail.value)} /></CSFormField>
          <CSFormField label="语气 / 说话风格"><CSTextarea rows={2} value={form.speech_style} onChange={({ detail }) => u('speech_style', detail.value)} /></CSFormField>
          <CSFormField label="当前状态" description="开局时的处境 / 心境"><CSTextarea rows={2} value={form.current_status} onChange={({ detail }) => u('current_status', detail.value)} /></CSFormField>
        </CSSpaceBetween>
      </CSContainer>

      <CSContainer header={h2('剧情与对话', '关键秘密仅 GM 可见;示例对话每行一句。')}>
        <CSSpaceBetween size="l">
          <CSFormField label="关键秘密" description="GM 可见,不会直接暴露给其他角色"><CSTextarea rows={3} value={form.secrets} onChange={({ detail }) => u('secrets', detail.value)} /></CSFormField>
          <CSFormField label="示例对话" description="每行一句,帮助 GM 模仿口吻"><CSTextarea rows={4} value={form.sample_dialogue} onChange={({ detail }) => u('sample_dialogue', detail.value)} /></CSFormField>
        </CSSpaceBetween>
      </CSContainer>

      <CSContainer header={h2('注入参数', '控制该卡在上下文里的注入预算 / 优先级 / 重要度 / 可见范围。')}>
        <CSColumnLayout columns={2}>
          <CSFormField label="重要度" description="0–100,越高越优先被检索注入">
            <CSInput type="number" value={String(form.importance)} onChange={({ detail }) => u('importance', detail.value)} />
          </CSFormField>
          {isNpc && (
            <CSFormField label="首次揭示章节" description="第几章首次登场(章节闸)">
              <CSInput type="number" value={String(form.first_revealed_chapter)} onChange={({ detail }) => u('first_revealed_chapter', detail.value)} />
            </CSFormField>
          )}
          <CSFormField label="Token 预算" description="注入上下文的最大 token(默认 450)">
            <CSInput type="number" value={String(form.token_budget)} onChange={({ detail }) => u('token_budget', detail.value)} />
          </CSFormField>
          <CSFormField label="优先级" description="数值越大越优先注入(默认 100)">
            <CSInput type="number" value={String(form.priority)} onChange={({ detail }) => u('priority', detail.value)} />
          </CSFormField>
          <CSFormField label="可见范围">
            <CSSelect selectedOption={scopeOpts.find((o) => o.value === form.scope) || scopeOpts[0]}
              options={scopeOpts} onChange={({ detail }) => u('scope', detail.selectedOption.value)} />
          </CSFormField>
          <CSFormField label="启用">
            <CSToggle checked={!!form.enabled} onChange={({ detail }) => u('enabled', detail.checked)}>
              {form.enabled ? '已启用' : '已禁用'}
            </CSToggle>
          </CSFormField>
        </CSColumnLayout>
      </CSContainer>
    </CSSpaceBetween>
  );
}

const _CARD_TYPE_LABEL = { npc: 'NPC', pc: '玩家卡', persona: '玩家身份' };
const _SCOPE_LABEL = { script: '剧本内', private: '私有', public: '公开' };
const _SOURCE_LABEL = { extracted: 'LLM 提取', user: '用户', persona: '身份', platform: '平台' };

// 短摘要(NPC 卡面用):取最有信息量的字段前 N 字,原样不解析
function cardSnippet(c, n = 160) {
  const raw = (c && c._raw) || c || {};
  const s = String(raw.background || raw.appearance || raw.personality || raw.current_status || raw.summary || raw.description || '').trim();
  return s ? (s.length > n ? s.slice(0, n) + '…' : s) : '';
}

/* 只读角色档展示(设定 tab / 详情用)。纯展示 DTO 结构化字段,不做任何文本解析。 */
function CardSheet({ card, kind = 'user' }) {
  const raw = (card && card._raw) || card || {};
  const fullName = raw.full_name && raw.full_name !== raw.name ? raw.full_name : null;
  const aliases = Array.isArray(raw.aliases) ? raw.aliases : [];
  const tags = Array.isArray(raw.tags) ? raw.tags : [];
  const dialogues = Array.isArray(raw.sample_dialogue) ? raw.sample_dialogue : [];
  const chapterGate = (kind === 'npc' && raw.first_revealed_chapter > 1) ? raw.first_revealed_chapter : null;
  const para = (label, value) => value ? (
    <div>
      <CSBox variant="awsui-key-label" padding={{ bottom: 'xxxs' }}>{label}</CSBox>
      <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, color: 'var(--text)' }}>{value}</div>
    </div>
  ) : null;
  const hasBody = raw.background || raw.appearance || raw.personality || raw.speech_style || raw.current_status || raw.secrets || dialogues.length;

  return (
    <CSSpaceBetween size="l">
      {/* 顶部:身份 + 徽章 */}
      <CSSpaceBetween size="xs">
        {(raw.identity || fullName) && (
          <CSBox fontSize="heading-s">
            {raw.identity || '—'}
            {fullName && <CSBox display="inline" color="text-status-inactive" fontSize="body-s" padding={{ left: 's' }}>{fullName}</CSBox>}
          </CSBox>
        )}
        <CSSpaceBetween direction="horizontal" size="xs">
          <CSBadge color="grey">{_CARD_TYPE_LABEL[raw.card_type] || (kind === 'npc' ? 'NPC' : '用户卡')}</CSBadge>
          {raw.source && <CSBadge>{_SOURCE_LABEL[raw.source] || raw.source}</CSBadge>}
          {chapterGate && <CSBadge color="blue">📖 第 {chapterGate} 章揭示</CSBadge>}
          {raw.importance != null && <CSBadge color="grey">重要度 {raw.importance}</CSBadge>}
          <CSBadge color="grey">{_SCOPE_LABEL[raw.scope] || '私有'}</CSBadge>
          {raw.enabled === false && <CSStatusIndicator type="stopped">已禁用</CSStatusIndicator>}
        </CSSpaceBetween>
        {aliases.length > 0 && (
          <CSBox fontSize="body-s" color="text-body-secondary">别名:{aliases.join(' · ')}</CSBox>
        )}
        {tags.length > 0 && (
          <CSSpaceBetween direction="horizontal" size="xxs">{tags.map((t) => <CSBadge key={t}>{t}</CSBadge>)}</CSSpaceBetween>
        )}
      </CSSpaceBetween>

      {/* 主体:各结构化字段原样分段 */}
      {para('前史 / 背景', raw.background)}
      {para('外貌', raw.appearance)}
      {para('性格 / 设定', raw.personality)}
      {para('语气 / 说话风格', raw.speech_style)}
      {para('当前状态', raw.current_status)}
      {para('关键秘密', raw.secrets)}
      {dialogues.length > 0 && (
        <div>
          <CSBox variant="awsui-key-label" padding={{ bottom: 'xxxs' }}>示例对话</CSBox>
          <CSSpaceBetween size="xxs">
            {dialogues.map((d, i) => (
              <CSBox key={i} color="text-body-secondary" fontSize="body-s">
                {typeof d === 'string' ? d : `${d.role ? d.role + ': ' : ''}${d.content || ''}`}
              </CSBox>
            ))}
          </CSSpaceBetween>
        </div>
      )}
      {!hasBody && <CSBox color="text-status-inactive">暂无设定,切到「角色设置」补充。</CSBox>}
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
      {subPage === "npc" ? <NpcCardsView /> : <UserCardsView />}
    </div>
  );
}

function CardGrid({ cards, onEdit, kind, filter, empty, onDeleted, onDuplicate, onPromoteToUser }) {
  // task 50：每张卡片的「更多」走 Cloudscape ButtonDropdown,
  // 内含 导出 PNG / 导出 SillyTavern JSON / 复制 ID / 转用户卡 / 复制为新卡 / 删除。
  const handleDelete = async (c) => {
    if (kind === "npc") {
      window.__apiToast?.("NPC 卡在剧本管理页面删除", { kind: "warn", duration: 2400 });
      return;
    }
    if (!await window.__confirm({ title: '删除角色卡', message: `确认删除角色卡「${c.name}」?该操作无法撤销。`, danger: true, confirmText: '删除' })) return;
    try {
      await window.api.cards.myDelete(c.id);
      window.__apiToast?.("已删除 " + c.name, { kind: "ok" });
      onDeleted && onDeleted(c);
    } catch (e) {
      window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message });
    }
  };
  const copyId = async (c) => {
    try { await navigator.clipboard.writeText(String(c.id)); window.__apiToast?.("已复制 ID", { kind: "ok", duration: 1500 }); }
    catch { window.__apiToast?.("复制失败", { kind: "danger" }); }
  };

  // NPC 卡 → user_card 一键迁移。复用 game-panels 同一套 saveAsUserCard 数据 shape。
  const promoteNpcToUserCard = async (c) => {
    const raw = c._raw || c;
    const body = {
      name: c.name || raw.name || "未命名",
      identity: c.role || raw.identity || raw.role || "—",
      appearance: raw.appearance || c.bio || "",
      personality: raw.personality || "",
      speech_style: raw.speech_style || "",
      current_status: raw.current_status || "",
      secrets: raw.secrets || "",
      sample_dialogue: Array.isArray(raw.sample_dialogue) ? raw.sample_dialogue : [],
      tags: Array.isArray(c.tags) && c.tags.length ? [...c.tags, "源自 NPC"] : ["源自 NPC"],
      metadata: {
        source: "npc_promote",
        source_script_id: c.script_id || null,
        source_npc_id: raw.id ?? c.id,
      },
      enabled: true,
    };
    try {
      const r = await window.api.cards.myUpsert(body);
      if (r && r.ok === false) throw new Error(r.error || r.detail || "迁移失败");
      window.__apiToast?.(`已迁移为用户角色卡：${body.name}`,
        { kind: "ok", duration: 2200, detail: "现可在『角色卡 / 用户角色卡』中使用" });
      if (onPromoteToUser) onPromoteToUser(r?.card || body);
    } catch (e) {
      window.__apiToast?.("迁移失败", { kind: "danger", detail: e?.message || String(e) });
    }
  };

  const menuItems = (c) => {
    if (kind === 'npc') {
      return [
        { id: 'promote', text: '转为用户角色卡', iconName: 'add-plus' },
        { id: 'copyid', text: '复制 ID', iconName: 'copy' },
        { id: 'delete', text: '删除', iconName: 'remove' },
      ];
    }
    return [
      { id: 'png', text: '导出 PNG(带卡数据)', href: window.api.cards.exportPng(c.id), external: true, iconName: 'file' },
      { id: 'tavern', text: '导出 SillyTavern JSON', href: window.api.cards.exportTavern(c.id), external: true, iconName: 'download' },
      { id: 'copyid', text: '复制 ID', iconName: 'copy' },
      ...(onDuplicate ? [{ id: 'dup', text: '复制为新卡', iconName: 'copy' }] : []),
      { id: 'delete', text: '删除', iconName: 'remove' },
    ];
  };
  const onMenu = (c, id) => {
    if (id === 'copyid') copyId(c);
    else if (id === 'dup') onDuplicate?.(c);
    else if (id === 'delete') handleDelete(c);
    else if (id === 'promote') promoteNpcToUserCard(c);
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
            <CSBox key="name" variant="h3" padding="n">{c.name}</CSBox>
            {c.pinned && <CSBadge key="pin" color="blue">已置顶</CSBadge>}
          </CSSpaceBetween>
        ),
        sections: [
          { id: 'meta', content: (c) => (
            <CSSpaceBetween direction="horizontal" size="xs">
              {c.role && c.role !== '—' && <CSBadge key="role">{c.role}</CSBadge>}
              {c.tone && c.tone !== '—' && <CSBadge key="tone" color="grey">{c.tone}</CSBadge>}
            </CSSpaceBetween>
          ) },
          { id: 'bio', content: (c) => <CSBox color="text-body-secondary">{c.bio || '—'}</CSBox> },
          { id: 'tags', content: (c) => (c.tags?.length
            ? <CSSpaceBetween direction="horizontal" size="xxs">{c.tags.map((t) => <CSBadge key={t}>{t}</CSBadge>)}</CSSpaceBetween>
            : null) },
          { id: 'foot', content: (c) => (
            <CSBox fontSize="body-s" color="text-status-inactive">
              {(kind === 'npc' ? c.save : c.origin)} · {c.uses} 次使用 · {c.updated}
            </CSBox>
          ) },
          { id: 'actions', content: (c) => (
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton variant="inline-link" iconName="edit" onClick={() => onEdit(c)}>编辑</CSButton>
              <CSButtonDropdown variant="inline-icon" ariaLabel="更多操作" expandToViewport
                items={menuItems(c)} onItemClick={({ detail }) => onMenu(c, detail.id)} />
            </CSSpaceBetween>
          ) },
        ],
      }}
    />
  );
}

function UserCardsView() {
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
      if (list.length) {
        setCards(list.map(c => ({
          id: String(c.id),
          name: c.name,
          role: c.identity || c.role || "—",
          tone: c.tone || "—",
          origin: c.origin || "通用",
          bio: c.description || c.summary || c.bio || c.personality || c.current_status || c.appearance || "",
          tags: c.tags || [],
          pinned: !!c.pinned,
          uses: c.uses || 0,
          updated: window.__fmt?.ago(c.updated_at) || c.updated_at || "—",
          _raw: c,
        })));
      }
    } catch (_) {}
  }, []);
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
      window.__apiToast?.(adding ? "已新增" : "已保存", { kind: "ok" });
      setAdding(false);
      reload();
    } catch (e) {
      window.__apiToast?.("保存失败", { kind: "danger", detail: e?.message });
    }
  };

  const onImport = async (payload) => {
    try {
      if (payload?.file) {
        await window.api.cards.importTavern(payload.file);
      } else if (payload?.json) {
        await window.api.cards.importJson({ json: payload.json });
      }
      window.__apiToast?.("已导入", { kind: "ok" });
      setImporting(false);
      reload();
    } catch (e) {
      window.__apiToast?.("导入失败", { kind: "danger", detail: e?.message });
    }
  };

  let filtered = cards;
  if (filter === "pinned") filtered = filtered.filter(c => c.pinned);
  if (q) filtered = filtered.filter(c => (c.name + c.role + c.bio + (c.tags || []).join(" ")).toLowerCase().includes(q.toLowerCase()));

  const selected = cards.find((x) => x.id === selectedId) || null;
  const onDuplicate = async (c) => {
    try {
      const src = c._raw || {};
      const body = { ...src, id: undefined, slug: undefined, name: (src.name || c.name) + " 副本" };
      await window.api.cards.myUpsert(body);
      window.__apiToast?.("已复制", { kind: "ok" });
      reload();
    } catch (e) { window.__apiToast?.("复制失败", { kind: "danger", detail: e?.message }); }
  };
  const onDeleteCard = async (c) => {
    if (!await window.__confirm({ title: '删除角色卡', message: `确认删除角色卡「${c.name}」?该操作无法撤销。`, danger: true, confirmText: '删除' })) return;
    try {
      await window.api.cards.myDelete(c.id);
      window.__apiToast?.("已删除 " + c.name, { kind: "ok" });
      setSelectedId(null);
      setCards(cs => cs.filter(x => x.id !== c.id)); reload();
    } catch (e) { window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message }); }
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
      empty={<CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>{q ? '没有匹配的角色卡' : '还没有用户角色卡,点右上「新增角色卡」开始。'}</CSBox>}
      columnDefinitions={[
        { id: 'name', header: '角色卡', cell: (c) => (
          <div><CSBox fontWeight="bold">{c.name}</CSBox><CSBox fontSize="body-s" color="text-body-secondary">{c.role !== '—' ? c.role : (c.bio || '').slice(0, 40)}</CSBox></div>
        ) },
        { id: 'tags', header: '标签', cell: (c) => (c.tags?.length
          ? <CSSpaceBetween direction="horizontal" size="xxs">{c.tags.slice(0, 4).map((t) => <CSBadge key={t}>{t}</CSBadge>)}</CSSpaceBetween>
          : <CSBox color="text-status-inactive">—</CSBox>) },
        { id: 'uses', header: '使用', cell: (c) => `${c.uses} 次` },
        { id: 'updated', header: '更新', cell: (c) => c.updated },
      ]}
    />
  );

  return (
    <>
      <CSSpaceBetween size="l">
        <CSHeader
          variant="h1"
          counter={`(${cards.length})`}
          description="跨剧本 / 跨存档共享的用户角色卡。"
          actions={
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton iconName="download" onClick={() => setImporting(true)}>导入酒馆卡</CSButton>
              <CSButton variant="primary" iconName="add-plus" onClick={() => setAdding(true)}>新增角色卡</CSButton>
            </CSSpaceBetween>
          }
        >用户角色卡</CSHeader>

        <CSSpaceBetween direction="horizontal" size="xs">
          <div style={{ minWidth: 260 }}>
            <CSTextFilter filteringText={q} filteringPlaceholder="搜索名称 / 身份 / 标签"
              onChange={({ detail }) => setQ(detail.filteringText)} />
          </div>
          <CSSegmentedControl selectedId={filter}
            options={[{ id: 'all', text: '全部' }, { id: 'pinned', text: '置顶' }]}
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

/* 角色卡详情面板 —— 选中后在列表下方展开(对齐剧本/存档)。
   Tabs:角色信息(KeyValuePairs)/ 设定(只读展示)/ 角色设置(内联编辑表单)。 */
function CardDetailPanel({ card, kind, onSave, onDuplicate, onDelete }) {
  const raw = card._raw || card;
  const [tab, setTab] = useStatePL('info');
  const [form, setForm] = useStatePL(null);
  const [saving, setSaving] = useStatePL(false);
  useEffectPL(() => {
    setTab('info');
    setForm(cardFormInit(raw));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.id]);
  const u = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const doSave = async () => {
    if (!form?.name?.trim()) { window.__apiToast?.('姓名必填', { kind: 'warn' }); return; }
    setSaving(true);
    try { await onSave(cardFormPayload(form, card)); }
    finally { setSaving(false); }
  };
  const setting = (label, value) => value
    ? <div><CSBox variant="awsui-key-label">{label}</CSBox><CSBox color="text-body-secondary" variant="p">{value}</CSBox></div>
    : null;
  const fullName = raw.full_name && raw.full_name !== raw.name ? raw.full_name : null;
  const chapterGate = (kind === 'npc' && raw.first_revealed_chapter > 1) ? raw.first_revealed_chapter : null;

  return (
    <CSContainer header={
      <CSHeader variant="h2"
        actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="primary" iconName="check" loading={saving} onClick={doSave}>保存</CSButton>
            <CSButton iconName="copy" onClick={onDuplicate}>复制为新卡</CSButton>
            {kind === 'user' && <CSButton href={window.api.cards.exportTavern(card.id)} target="_blank" iconName="download">导出</CSButton>}
            <CSButton iconName="remove" onClick={onDelete}>删除</CSButton>
          </CSSpaceBetween>
        }
      >{card.name}{fullName && <CSBox display="inline" color="text-status-inactive" fontSize="body-s" padding={{ left: 's' }}>{fullName}</CSBox>}</CSHeader>
    }>
      <CSTabs activeTabId={tab} onChange={({ detail }) => setTab(detail.activeTabId)} tabs={[
        { id: 'info', label: '角色信息', content: (
          <CSKeyValuePairs columns={4} items={[
            { label: '身份 / 职位', value: raw.identity || raw.role || '—' },
            ...(fullName ? [{ label: '全名', value: fullName }] : []),
            { label: '类型', value: ({ npc: 'NPC', pc: '玩家卡', persona: '玩家身份' })[raw.card_type] || (kind === 'npc' ? 'NPC' : '用户卡') },
            { label: '来源', value: ({ extracted: 'LLM 提取', user: '用户', persona: '身份', platform: '平台' })[raw.source] || card.origin || '通用' },
            { label: '重要度', value: raw.importance != null ? String(raw.importance) : '—' },
            ...(chapterGate ? [{ label: '章节闸', value: <CSStatusIndicator type="info">📖 第 {chapterGate} 章揭示</CSStatusIndicator> }] : []),
            { label: '可见范围', value: ({ script: '剧本内', private: '私有', public: '公开' })[raw.scope] || '—' },
            { label: '状态', value: raw.enabled === false ? <CSStatusIndicator type="stopped">已禁用</CSStatusIndicator> : <CSStatusIndicator type="success">启用</CSStatusIndicator> },
            { label: '标签', value: (Array.isArray(raw.tags) && raw.tags.length) ? raw.tags.join(' · ') : '—' },
            { label: '更新', value: card.updated || '—' },
            { label: '卡 ID', value: <span className="mono">{card.id}</span> },
          ]} />
        ) },
        { id: 'setting', label: '设定', content: <CardSheet card={card} kind={kind} /> },
        { id: 'edit', label: '角色设置', content: form && (
          <CSSpaceBetween size="l">
            <CardEditFields form={form} u={u} kind={kind} />
            <CSBox><CSButton variant="primary" iconName="check" loading={saving} onClick={doSave}>保存</CSButton></CSBox>
          </CSSpaceBetween>
        ) },
      ]} />
    </CSContainer>
  );
}

function TavernImportModal({ open, onClose, onConfirm }) {
  const [mode, setMode] = useStatePL("file");
  const [json, setJson] = useStatePL("");
  const [files, setFiles] = useStatePL([]);
  const [dragOver, setDragOver] = useStatePL(false);
  const [parseError, setParseError] = useStatePL(null);
  const [parsed, setParsed] = useStatePL(null);

  React.useEffect(() => {
    if (!open) return;
    setMode("file"); setJson(""); setFiles([]); setParseError(null); setParsed(null);
  }, [open]);

  const handleFiles = (list) => {
    const arr = [...list].slice(0, 8);
    setFiles(arr);
    // mock: parse first file
    if (arr[0]) {
      setTimeout(() => {
        setParsed({
          name: arr[0].name.replace(/\.(png|json|webp)$/i, "").replace(/[_-]/g, " "),
          format: arr[0].name.match(/\.png$/i) ? "SillyTavern · PNG v2" : "SillyTavern · JSON",
          description: "从酒馆角色卡导入 · 设定文本约 1240 字，含 4 个对话示例和 6 个标签。",
          tags: ["导入", "酒馆"],
          first_mes: "「你是谁？」她抬起头，目光在你脸上停了一会儿。",
          example_count: 4,
        });
      }, 400);
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
      const name = obj.name || obj.char_name || obj.data?.name || "未命名";
      const desc = obj.description || obj.data?.description || "无描述";
      setParsed({
        name,
        format: obj.spec ? `${obj.spec} · ${obj.spec_version || "v1"}` : "SillyTavern · JSON",
        description: desc.length > 160 ? desc.slice(0, 160) + "…" : desc,
        tags: obj.tags || obj.data?.tags || [],
        first_mes: obj.first_mes || obj.data?.first_mes || "—",
        example_count: (obj.mes_example || obj.data?.mes_example || "").split(/<START>/).filter(Boolean).length,
      });
    } catch (e) {
      setParseError("JSON 解析失败：" + e.message);
      setParsed(null);
    }
  };

  if (!open) return null;
  const canSubmit = parsed && !parseError;
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(620px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">导入酒馆角色卡</div>
            <h2 className="pl-modal-title">支持 SillyTavern / Chub / TavernAI 格式</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form">
          <div className="seg" style={{display: "flex"}}>
            <button className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}>
              <Icon name="upload" size={12} /> 上传文件
            </button>
            <button className={mode === "paste" ? "active" : ""} onClick={() => setMode("paste")}>
              <Icon name="file" size={12} /> 粘贴 JSON
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
                  {dragOver ? "松手以导入" : "把角色卡拖到这里"}
                </strong>
                <span>支持 .png（嵌入元数据）/ .json / .webp · 单次最多 8 个</span>
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
                <label>粘贴角色卡 JSON</label>
                <textarea rows={10} value={json} onChange={(e) => setJson(e.target.value)}
                  className="mono" style={{fontSize: 11.5}}
                  placeholder='{\n  "name": "沈知微",\n  "description": "...",\n  "first_mes": "...",\n  "tags": ["医师"]\n}' />
              </div>
              <button className="btn ghost" onClick={tryParseJson} disabled={!json.trim()} style={{width: "fit-content"}}>
                <Icon name="check" size={12} /> 解析并预览
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
              <div className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em"}}>解析预览 · {parsed.format}</div>
              <div className="pl-card-head" style={{margin: 0}}>
                <div className="pl-card-avatar serif">{parsed.name.slice(0, 1)}</div>
                <div className="pl-card-id" style={{flex: 1}}>
                  <strong>{parsed.name}</strong>
                  <span className="muted-2" style={{fontSize: 11.5}}>{parsed.example_count} 段对话示例 · {parsed.tags.length} 个标签</span>
                </div>
              </div>
              <p className="pl-card-bio serif" style={{margin: 0, WebkitLineClamp: 2}}>{parsed.description}</p>
              <div style={{padding: 8, background: "var(--bg-deep)", borderRadius: 4, fontFamily: "var(--font-serif)", fontSize: 12.5, color: "var(--text-quiet)", borderLeft: "2px solid var(--accent-edge)"}}>
                <span className="muted-2 mono" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em", display: "block", marginBottom: 4}}>开场白</span>
                {parsed.first_mes}
              </div>
              {parsed.tags?.length > 0 && (
                <div className="pl-card-tags">
                  {parsed.tags.map(t => <span key={t} className="pl-cap-tag">{t}</span>)}
                </div>
              )}
            </div>
          )}
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> POST /api/v1/characters/import · 导入后可在用户角色卡库编辑
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            <button className="btn primary" onClick={onConfirm} disabled={!canSubmit}>
              <Icon name="check" size={12} /> 导入 {files.length > 1 ? `${files.length} 个` : ""}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
  return createPortal(node, document.body);
}

function NpcCardsView() {
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

  const reload = React.useCallback(async () => {
    setLoading(true); setError("");
    try {
      // 1) 拉所有 scripts；2) 对每个 script 并行拉 character-cards
      const sr = await window.api.scripts.list();
      const scripts = Array.isArray(sr) ? sr : (sr?.items || sr?.scripts || []);
      if (!scripts.length) { setCards([]); setLoading(false); return; }
      const lists = await Promise.all(scripts.map(async (s) => {
        try {
          const r = await window.api.cards.scriptList(s.id);
          const arr = Array.isArray(r) ? r : (r?.items || r?.cards || []);
          return arr.map(c => ({
            id: String(c.id),
            name: c.name || "未命名",
            role: c.identity || c.role || "—",
            tone: c.tone || "中立",
            save: s.title || `剧本 #${s.id}`,
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
      setError(e?.message || "加载 NPC 角色卡失败");
      // 匿名 / API 不可达 → 兜底到 mock（designer offline preview）
      if (!(window.RPG_AUTH && window.RPG_AUTH.authed)) {
        setCards((NPC_CARDS || []).map(c => ({ ...c, script_id: null })));
      } else {
        setCards([]);
      }
    } finally { setLoading(false); }
  }, []);
  React.useEffect(() => { reload(); }, [reload]);

  const allSaves = ["all", ...new Set(cards.map(c => c.save))];
  let filtered = cards;
  if (saveFilter !== "all") filtered = filtered.filter(c => c.save === saveFilter);
  if (q) filtered = filtered.filter(c =>
    (String(c.name) + String(c.role) + String(c.bio) + (c.tags || []).join(" "))
      .toLowerCase().includes(q.toLowerCase())
  );

  const saveOpts = allSaves.map((s) => ({ value: s, label: s === 'all' ? '所有剧本' : s }));
  return (
    <>
      <CSSpaceBetween size="l">
        <CSHeader
          variant="h1"
          counter={`(${cards.length})`}
          description={`从剧本提取的 NPC 角色卡,按存档分组。${loading ? ' 加载中…' : ''}`}
          actions={<CSButton variant="primary" iconName="add-plus" onClick={() => setAdding(true)}>新增 NPC</CSButton>}
        >NPC 角色卡</CSHeader>
        {error && <CSAlert type="error" header="加载失败">{error}</CSAlert>}
        <CardGrid cards={filtered} onEdit={setEdit} kind="npc"
          empty={
            <CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
              {loading ? '加载中…' : <>你的剧本里还没有任何 NPC 角色卡。<br />点右上「新增 NPC」创建,或先去「剧本 / 上传剧本」导入含角色设定的剧本。</>}
            </CSBox>
          }
          filter={
            <CSSpaceBetween direction="horizontal" size="xs">
              <div style={{ minWidth: 240 }}>
                <CSTextFilter filteringText={q} filteringPlaceholder="搜索 NPC"
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
          }} />
      </CSSpaceBetween>
      {(edit || adding) && (
        <CardEditModal
          card={edit?._raw || edit}
          isNew={adding}
          kind="npc"
          onClose={() => { setEdit(null); setAdding(false); }}
          onSave={() => { setEdit(null); setAdding(false); reload(); }}
        />
      )}
    </>
  );
}

/* 角色卡编辑器 —— EC2 式单页多模块全屏表单(对齐新建存档)。
   覆盖 user_character_cards 全部角色相关列:name / identity / aliases / tags /
   appearance / personality / speech_style / current_status / secrets /
   sample_dialogue / token_budget / priority / enabled / scope。 */
function CardEditModal({ card, isNew, kind, onClose, onSave }) {
  const [form, setForm] = useStatePL(() => cardFormInit(card));
  const [submitting, setSubmitting] = useStatePL(false);
  const u = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const nameOk = !!form.name.trim();

  const doSave = async () => {
    if (!nameOk || submitting) return;
    setSubmitting(true);
    try { await onSave?.(cardFormPayload(form, card)); }
    catch (_) { /* 父级 onSaveCard 已 toast */ }
    finally { setSubmitting(false); }
  };

  const node = (
    <div style={{ position: 'fixed', top: 53, left: 0, right: 0, bottom: 0, zIndex: 1000, background: 'var(--bg, #1a1817)', overflow: 'auto' }}>
      {/* 顶部栏(位于平台顶栏下方,保留平台导航) */}
      <div style={{ position: 'sticky', top: 0, zIndex: 3, background: '#131211', borderBottom: '1px solid #36322d' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto', padding: '13px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
          <div style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 600, color: '#ebe7df' }}>
            {isNew ? '新增' : '编辑'}{kind === 'user' ? '用户角色卡' : 'NPC 角色卡'}
          </div>
          <CSButton iconName="close" variant="link" onClick={onClose}>取消</CSButton>
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
            <CSContainer header={<CSHeader variant="h2">概要</CSHeader>}>
              <CSSpaceBetween size="m">
                <CSStatusIndicator type={nameOk ? 'success' : 'pending'}>姓名(必填)</CSStatusIndicator>
                <CSKeyValuePairs columns={1} items={[
                  { label: '姓名', value: form.name.trim() || '—' },
                  { label: '身份', value: form.identity.trim() || '—' },
                  { label: '可见范围', value: form.scope === 'public' ? '公开' : '私有' },
                  { label: '状态', value: form.enabled ? '启用' : '禁用' },
                ]} />
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <CSButton variant="primary" disabled={!nameOk || submitting} loading={submitting} onClick={doSave}>
                    {isNew ? '创建' : '保存'}
                  </CSButton>
                  <CSButton variant="link" onClick={onClose}>取消</CSButton>
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

export { CardsPage, CardGrid, UserCardsView, NpcCardsView, CardEditModal, TavernImportModal, CardSheet, cardSnippet };
