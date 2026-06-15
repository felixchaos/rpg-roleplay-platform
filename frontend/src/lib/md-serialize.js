// md-serialize.js — 剧本各实体 ⇄ markdown(YAML front-matter + 正文)无损序列化层。
// 设计:docs/design/N_md_editor.md §2。每实体一份 schema;toMd(row)→text / fromMd(text)→可写 patch。
//
// 铁律(无损):fromMd(toMd(row)) 必须等于 row 的【可写子集】(write* + body)。
// 只读字段(id/created_at/word_count/avatar_path…)在 front-matter 里回显供参考,**保存时一律剔除**。
// 多行文本走 YAML block scalar(`yaml` 库自动选择,无损);字符串数组/开放对象同理由 YAML 原生承载。
//
// 字段类别:
//   bodyField    —— 落 markdown 正文的那一列(实体的主文本)
//   writeScalars —— 标量(string/number/bool),原样往返
//   writeStrArrays —— 字符串数组(jsonb 字符串数组 或 PostgreSQL text[]);保存时归一成 string[]
//   writeObjLists —— 对象数组(如 sample_dialogue);原样往返
//   writeOpenObjs —— 开放对象(如 canon.attrs,按 type 字段不定);原样往返
//   readonly     —— front-matter 回显但保存剔除
import YAML from 'yaml';

export const SCHEMAS = {
  chapter: {
    label: '章节正文', idField: 'chapter_index',
    bodyField: 'content',
    writeScalars: ['title', 'volume_title'],
    writeStrArrays: {}, writeObjLists: [], writeOpenObjs: [],
    readonly: ['chapter_index', 'word_count', 'id'],
    order: ['chapter_index', 'title', 'volume_title', 'word_count', 'id'],
  },
  worldbook: {
    label: '世界书', idField: 'id',
    bodyField: 'content',
    writeScalars: ['title', 'priority', 'enabled', 'token_budget', 'insertion_position',
      'sticky_turns', 'cooldown_turns', 'probability', 'first_revealed_chapter'],
    writeStrArrays: { keys: 1, regex_keys: 1, character_filter: 1, scene_filter: 1 },
    writeObjLists: [], writeOpenObjs: [],
    readonly: ['id'],
    order: ['id', 'title', 'enabled', 'priority', 'token_budget', 'insertion_position',
      'first_revealed_chapter', 'sticky_turns', 'cooldown_turns', 'probability',
      'keys', 'regex_keys', 'character_filter', 'scene_filter'],
  },
  anchor: {
    label: '时间线锚点', idField: 'id',
    bodyField: 'sample_summary',
    writeScalars: ['story_phase', 'story_time_label', 'chapter_min', 'chapter_max', 'confidence', 'sample_title'],
    writeStrArrays: { keywords: 1 },   // 注意:后端是 PostgreSQL text[](非 jsonb)
    writeObjLists: [], writeOpenObjs: [],
    readonly: ['id', 'chapter_count'],
    order: ['id', 'story_phase', 'story_time_label', 'chapter_min', 'chapter_max',
      'chapter_count', 'confidence', 'sample_title', 'keywords'],
  },
  canon: {
    label: 'Canon 实体', idField: 'logical_key',
    bodyField: 'background',
    writeScalars: ['logical_key', 'name', 'full_name', 'type', 'entity_subtype',
      'parent_logical_key', 'summary', 'identity', 'first_revealed_chapter', 'public_knowledge', 'importance'],
    writeStrArrays: { aliases: 1 },
    writeObjLists: [], writeOpenObjs: ['attrs'],
    readonly: ['id', 'created_at'],
    order: ['id', 'logical_key', 'type', 'entity_subtype', 'parent_logical_key', 'name', 'full_name',
      'identity', 'summary', 'aliases', 'first_revealed_chapter', 'public_knowledge', 'importance', 'attrs'],
  },
  card: {
    label: '角色卡', idField: 'id',
    bodyField: 'background',
    writeScalars: ['name', 'full_name', 'identity', 'appearance', 'personality', 'speech_style',
      'current_status', 'secrets', 'first_revealed_chapter', 'importance', 'token_budget', 'priority', 'enabled'],
    writeStrArrays: { aliases: 1, tags: 1 },
    writeObjLists: ['sample_dialogue'], writeOpenObjs: [],
    readonly: ['id', 'card_type', 'source', 'slug', 'avatar_path'],
    order: ['id', 'card_type', 'source', 'name', 'full_name', 'aliases', 'enabled', 'importance',
      'first_revealed_chapter', 'token_budget', 'priority', 'identity', 'appearance', 'personality',
      'speech_style', 'current_status', 'secrets', 'tags', 'sample_dialogue'],
  },
};

export const EDITABLE_KINDS = Object.keys(SCHEMAS);

const isPlainObj = (v) => v && typeof v === 'object' && !Array.isArray(v);

function toStrArray(v) {
  if (Array.isArray(v)) return v.map((x) => (x == null ? '' : String(x)));
  if (v == null || v === '') return [];
  return [String(v)];
}

// 拆 front-matter:`---\n<yaml>\n---\n\n<body>`。无 front-matter → 整体当正文。
export function splitFrontMatter(text) {
  const m = /^---\n([\s\S]*?)\n---\n/.exec(text || '');
  if (!m) return { fm: {}, body: text || '' };
  let fm = {};
  try { fm = YAML.parse(m[1]) || {}; } catch (e) { throw new Error('YAML front-matter 解析失败:' + (e?.message || e)); }
  if (!isPlainObj(fm)) fm = {};
  // toMd 写的是 `---\n<yaml>---\n\n<body>`(yaml 末尾自带 \n),分隔符是紧跟的一个 \n;剔除它即得无损正文。
  const body = (text.slice(m[0].length)).replace(/^\n/, '');
  return { fm, body };
}

// row → markdown。
export function toMd(kind, row) {
  const sc = SCHEMAS[kind];
  if (!sc) throw new Error('未知实体类型:' + kind);
  row = row || {};
  const fm = {};
  const seen = new Set();
  const put = (k) => {
    if (k === sc.bodyField || seen.has(k)) return;
    seen.add(k);
    if (k in sc.writeStrArrays) { fm[k] = toStrArray(row[k]); return; }
    if (sc.writeObjLists.includes(k)) { fm[k] = Array.isArray(row[k]) ? row[k] : []; return; }
    if (sc.writeOpenObjs.includes(k)) { fm[k] = isPlainObj(row[k]) ? row[k] : {}; return; }
    let v = row[k];
    if (v === undefined || v === null) v = '';
    fm[k] = v;
  };
  // 先按 order 排;order 未覆盖的可写字段补在后面(保证不漏)。
  for (const k of (sc.order || [])) put(k);
  for (const k of sc.writeScalars) put(k);
  for (const k of Object.keys(sc.writeStrArrays)) put(k);
  for (const k of sc.writeObjLists) put(k);
  for (const k of sc.writeOpenObjs) put(k);
  for (const k of sc.readonly) put(k);

  const yamlStr = YAML.stringify(fm, { lineWidth: 0 });   // lineWidth:0 关闭自动折行,防长行被切
  const body = row[sc.bodyField] != null ? String(row[sc.bodyField]) : '';
  return `---\n${yamlStr}---\n\n${body}`;
}

// markdown → 可写 patch(只含 write* 字段 + body;readonly 一律剔除)。
export function fromMd(kind, text) {
  const sc = SCHEMAS[kind];
  if (!sc) throw new Error('未知实体类型:' + kind);
  const { fm, body } = splitFrontMatter(text);
  const patch = {};
  for (const k of sc.writeScalars) if (k in fm) patch[k] = fm[k];
  for (const k of Object.keys(sc.writeStrArrays)) if (k in fm) patch[k] = toStrArray(fm[k]);
  for (const k of sc.writeObjLists) if (k in fm) patch[k] = Array.isArray(fm[k]) ? fm[k] : [];
  for (const k of sc.writeOpenObjs) if (k in fm) patch[k] = isPlainObj(fm[k]) ? fm[k] : {};
  patch[sc.bodyField] = body;
  for (const k of sc.readonly) delete patch[k];   // 双保险:readonly 绝不进 patch
  return patch;
}

// 取实体在树/标签里的显示标题(尽量用人类可读字段)。
export function nodeTitle(kind, row) {
  if (!row) return '';
  if (kind === 'chapter') return `第${row.chapter_index}章 ${row.title || ''}`.trim();
  if (kind === 'card') return row.name + (row.full_name && row.full_name !== row.name ? ` (${row.full_name})` : '');
  if (kind === 'worldbook') return row.title || `(条目 ${row.id})`;
  if (kind === 'anchor') return `${row.story_time_label || row.story_phase || ''}（${row.chapter_min}-${row.chapter_max}）`;
  if (kind === 'canon') return `${row.name}（${row.type}）`;
  return String(row.id ?? '');
}
