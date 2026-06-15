import { describe, it, expect } from 'vitest';
import { toMd, fromMd, splitFrontMatter, SCHEMAS } from '../lib/md-serialize.js';

// 取 row 的「可写子集」(write* + body),用于校验无损往返。
function writableSubset(kind, row) {
  const sc = SCHEMAS[kind];
  const out = {};
  for (const k of sc.writeScalars) out[k] = row[k];
  for (const k of Object.keys(sc.writeStrArrays)) out[k] = Array.isArray(row[k]) ? row[k] : [];
  for (const k of sc.writeObjLists) out[k] = Array.isArray(row[k]) ? row[k] : [];
  for (const k of sc.writeOpenObjs) out[k] = row[k] && typeof row[k] === 'object' ? row[k] : {};
  out[sc.bodyField] = row[sc.bodyField] != null ? String(row[sc.bodyField]) : '';
  return out;
}

describe('md-serialize 无损往返', () => {
  it('chapter:正文 + 标题往返', () => {
    const row = { id: 9, chapter_index: 42, word_count: 1234, title: '第四十二章 烟雨', volume_title: '第二卷', content: '正文第一行\n第二行\n\n空行后段落' };
    const patch = fromMd('chapter', toMd('chapter', row));
    expect(patch).toEqual(writableSubset('chapter', row));
    expect(patch.id).toBeUndefined();          // readonly 剔除
    expect(patch.chapter_index).toBeUndefined();
    expect(patch.content).toBe(row.content);   // 正文逐字
  });

  it('worldbook:含 jsonb 字符串数组 + 数值 + 正则键', () => {
    const row = {
      id: 7, title: '铁人团', content: '组织架构……', priority: 80, enabled: true,
      token_budget: 600, insertion_position: 'worldbook', sticky_turns: 0, cooldown_turns: 2,
      probability: 100, first_revealed_chapter: 5,
      keys: ['铁人团', 'Eisenmänner'], regex_keys: ['铁人.{0,3}团', 'a:b#c'],
      character_filter: ['穆蕾莉娅'], scene_filter: [],
    };
    const patch = fromMd('worldbook', toMd('worldbook', row));
    expect(patch).toEqual(writableSubset('worldbook', row));
    expect(patch.regex_keys).toEqual(['铁人.{0,3}团', 'a:b#c']);  // 特殊字符无损
    expect(patch.id).toBeUndefined();
  });

  it('anchor:keywords 是 text[] 风格的字符串数组', () => {
    const row = {
      id: 50, chapter_count: 18, story_phase: '柏林暗流篇', story_time_label: '1943冬',
      chapter_min: 38, chapter_max: 55, confidence: 0.92, sample_title: '雪夜密谋',
      sample_summary: '锚点摘要正文', keywords: ['柏林', '铁人团', '异端'],
    };
    const patch = fromMd('anchor', toMd('anchor', row));
    expect(patch).toEqual(writableSubset('anchor', row));
    expect(patch.keywords).toEqual(['柏林', '铁人团', '异端']);
    expect(patch.chapter_count).toBeUndefined();  // readonly
    expect(patch.sample_summary).toBe('锚点摘要正文');
  });

  it('canon:开放 attrs 对象 + aliases 往返', () => {
    const row = {
      id: 300, logical_key: 'char_x', type: 'character', entity_subtype: '', parent_logical_key: '',
      name: '穆蕾莉娅', full_name: '穆蕾莉娅·冯', identity: '审判官', summary: '一句话',
      background: '详细背景\n第二段', aliases: ['莉娅', '铁血少女'],
      first_revealed_chapter: 1, public_knowledge: false, importance: 90,
      attrs: { gender: '女', age_approx: '20多岁', tags: ['a', 'b'] }, created_at: '2026-01-01',
    };
    const patch = fromMd('canon', toMd('canon', row));
    expect(patch).toEqual(writableSubset('canon', row));
    expect(patch.attrs).toEqual({ gender: '女', age_approx: '20多岁', tags: ['a', 'b'] });
    expect(patch.background).toBe('详细背景\n第二段');
    expect(patch.created_at).toBeUndefined();  // readonly
  });

  it('card:多行文本字段(block scalar)+ 对象数组 sample_dialogue', () => {
    const row = {
      id: 101, card_type: 'npc', source: 'extracted', slug: '', avatar_path: '/x.png',
      name: '穆蕾莉娅', full_name: '穆蕾莉娅·冯', aliases: ['莉娅'], tags: [],
      identity: '审判官', appearance: '银发\n红瞳', personality: '冷静\n\n偶尔毒舌',
      speech_style: '简短', current_status: '活跃', secrets: '其实是卧底',
      background: '出身贵族,自幼……\n第二段背景', first_revealed_chapter: 1, importance: 90,
      token_budget: 450, priority: 100, enabled: true,
      sample_dialogue: [{ role: 'user', content: '你是谁' }, { role: 'assistant', content: '审判官' }],
    };
    const patch = fromMd('card', toMd('card', row));
    expect(patch).toEqual(writableSubset('card', row));
    expect(patch.personality).toBe('冷静\n\n偶尔毒舌');   // 多行 + 空行无损
    expect(patch.sample_dialogue).toEqual(row.sample_dialogue);
    expect(patch.avatar_path).toBeUndefined();           // readonly(走专用端点)
    expect(patch.card_type).toBeUndefined();
  });
});

describe('md-serialize 边界', () => {
  it('正文以换行开头也无损(分隔符只剥一个 \\n)', () => {
    const row = { chapter_index: 1, title: 'T', volume_title: '', content: '\n以空行开头\n结尾' };
    const patch = fromMd('chapter', toMd('chapter', row));
    expect(patch.content).toBe('\n以空行开头\n结尾');
  });

  it('正文含 --- 分隔线不破坏解析', () => {
    const row = { chapter_index: 1, title: 'T', volume_title: '', content: '上\n---\n下' };
    const patch = fromMd('chapter', toMd('chapter', row));
    expect(patch.content).toBe('上\n---\n下');
  });

  it('字符串数组:单值/缺失归一', () => {
    expect(fromMd('worldbook', toMd('worldbook', { title: 'T', content: '', keys: 'solo' })).keys).toEqual(['solo']);
    expect(fromMd('worldbook', toMd('worldbook', { title: 'T', content: '' })).keys).toEqual([]);
  });

  it('splitFrontMatter:无 front-matter → 整体当正文', () => {
    const r = splitFrontMatter('就是一段纯文本没有 front-matter');
    expect(r.fm).toEqual({});
    expect(r.body).toBe('就是一段纯文本没有 front-matter');
  });

  it('未知实体类型抛错', () => {
    expect(() => toMd('nope', {})).toThrow();
    expect(() => fromMd('nope', '')).toThrow();
  });
});
