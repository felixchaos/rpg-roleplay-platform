/* 角色卡纯工具 + mock 数据 —— 从 pages/cards.jsx 拆出,逐字节不变。
   DTO 表单 init/payload、NPC→用户卡 body、防溢出文本 helper、短摘要、示例卡数据。
   MobileCards / game / tavern / saves / scripts 侧借用这些 helper(经 pages/cards.jsx 转发)。 */

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

// CSV 输入(别名 / 标签)拆分。分隔符集与后端 user_cards._normalize_list 一致 ——
// 提示文案只写「逗号分隔」,中文输入法打出的是全角「,」/「、」/「;」,只认半角逗号
// 会把「战姬,剑士」整条当成一个标签(用户看到的就是"标签没保存对")。
const _splitCsv = (s) => (s || '').split(/[,，;；、]/).map((x) => x.trim()).filter(Boolean);

function cardFormPayload(form, card) {
  const trim = (s) => (s || '').trim();
  return {
    ...(card && card.id ? { id: card.id } : {}),
    name: trim(form.name),
    full_name: trim(form.full_name),
    identity: trim(form.identity),
    aliases: _splitCsv(form.aliases),
    tags: _splitCsv(form.tags),
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

// 短摘要(NPC 卡面用):取最有信息量的字段前 N 字,原样不解析
function cardSnippet(c, n = 160) {
  const raw = (c && c._raw) || c || {};
  const s = String(raw.background || raw.appearance || raw.personality || raw.current_status || raw.summary || raw.description || '').trim();
  return s ? (s.length > n ? s.slice(0, n) + '…' : s) : '';
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

export { _oneLine, ELLIPSIS_1, clampLines, cardFormInit, cardFormPayload, npcToUserCardBody, cardSnippet, USER_CARDS, NPC_CARDS };
