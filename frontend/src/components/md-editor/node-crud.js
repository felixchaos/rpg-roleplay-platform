// node-crud.js — 文件树实体增删改 + 分组列表拉取(从 pages/md-editor.jsx 搬出)。
// 列表条目统一带 name(裸名字,供改名输入框预填 / 复制命名),label 只用于显示
// (canon 带「(类型)」、锚点带「(章节区间)」后缀,拿 label 去改名会把后缀写进名字)。
import i18n from '../../i18n';
import { api, stripChapterPrefix, canonTypeZh } from './helpers.js';

const anchorLabel = (name, min, max) => `${name} (${min}-${max})`;

// ── 实体 CRUD(树内增删改) ─────────────────────────────────────────────
// opts.type:canon 专用(复制时沿用原类型);缺省 concept。
async function createNode(kind, sid, name, opts) {
  const A = api(); const nm = (name || '').trim();
  if (kind === 'chapter')   { const r = await A.scripts.addChapter(sid, nm); return { id: r.chapter_index, label: `${i18n.t('md_editor.chapter_prefix', { index: r.chapter_index })} ${r.title || ''}`.trim() }; }
  if (kind === 'worldbook') { const _def = i18n.t('md_editor.node_defaults.worldbook'); const r = await A.scripts.worldbookCreate(sid, { title: nm || _def, content: '' }); const e = r?.entry || r; return { id: e.id, label: e.title || nm || _def }; }
  if (kind === 'card')      { const _def = i18n.t('md_editor.node_defaults.card'); const r = await A.scripts.cardUpsert(sid, { name: nm || _def }); const c = r?.card || r; return { id: c.id, label: c.name || nm || _def }; }
  // canon:不带 logical_key 发 POST,后端按名字+类型生成 key 并在 entity 里返回(用它打开节点)。
  if (kind === 'canon')     { const _def = i18n.t('md_editor.node_defaults.canon'); const r = await A.scripts.canonCreate(sid, { name: nm || _def, type: (opts && opts.type) || 'concept' }); const e = r?.entity || r; return { id: e.logical_key, label: `${e.name || nm || _def} (${canonTypeZh(e.type || 'concept')})` }; }
  if (kind === 'anchor')    { const _def = i18n.t('md_editor.node_defaults.anchor'); const r = await A.scripts.anchorCreate(sid, { story_time_label: nm || _def, chapter_min: 1, chapter_max: 1 }); const a = r?.anchor || r; return { id: a.id, label: anchorLabel(a.story_time_label || nm || _def, a.chapter_min ?? 1, a.chapter_max ?? 1) }; }
  throw new Error(i18n.t('md_editor.errors.create_unsupported'));
}
async function renameNode(kind, sid, id, name) {
  const A = api(); const nm = (name || '').trim(); if (!nm) return;
  if (kind === 'chapter')   { await A.scripts.updateChapter(sid, id, { title: nm }); return; }
  if (kind === 'worldbook') { await A.scripts.worldbookUpdate(sid, id, { title: nm }); return; }
  // 锚点在树上显示、新建时写的都是 story_time_label;改 story_phase(所属阶段)会让改名看起来没生效。
  if (kind === 'anchor')    { await A.scripts.anchorUpdate(sid, id, { story_time_label: nm }); return; }
  // card 是全覆盖 upsert → 必须 re-fetch 全字段再改名,否则抹掉头像/属性等(历史 data-loss 坑)。
  if (kind === 'card')      { const cur = await A.scripts.cardGet(sid, id); const c = cur?.card || cur; await A.scripts.cardUpsert(sid, { ...c, id, name: nm }); return; }
  // canon 的 PUT 是补丁语义(只改 body 里出现的字段),只发 name,不整行回写(免得覆盖别处刚改的字段)。
  if (kind === 'canon')     { await A.scripts.canonUpdate(sid, id, { name: nm }); return; }
}
async function deleteNode(kind, sid, id) {
  const A = api();
  if (kind === 'chapter')   return A.scripts.deleteChapters(sid, [id]);  // 单删=批量删一项(后端统一重排)
  if (kind === 'worldbook') return A.scripts.worldbookDelete(sid, id);
  if (kind === 'card')      return A.scripts.cardDelete(sid, id);
  if (kind === 'anchor')    return A.scripts.anchorDelete(sid, id);
  if (kind === 'canon')     return A.scripts.canonDelete(sid, id);
  throw new Error(i18n.t('md_editor.errors.delete_unsupported'));
}

// 每组的列表拉取 —— 复用 window.api.scripts.* / api.cards.*。
async function fetchGroupList(kind, sid) {
  const A = api();
  if (kind === 'chapter') {
    const r = await A.scripts.chapters(sid, { limit: 5000 });
    const arr = r?.chapters || r?.items || [];
    return arr.map((c) => ({ id: c.chapter_index, title: stripChapterPrefix(c.title || ''), label: `${i18n.t('md_editor.chapter_prefix', { index: c.chapter_index })} ${stripChapterPrefix(c.title || '')}`.trim(), word_count: c.word_count }));
  }
  if (kind === 'card') {
    const r = await A.cards.scriptList(sid);
    const arr = Array.isArray(r) ? r : (r?.items || []);
    return arr.map((c) => ({ id: c.id, name: c.name, label: c.name + (c.full_name && c.full_name !== c.name ? ` (${c.full_name})` : '') }));
  }
  if (kind === 'worldbook') {
    // fetch_all:默认分页一页 50 条,超出的条目在树里看不到、打开也是空的。
    const r = await A.scripts.worldbook(sid, { fetch_all: true });
    const arr = r?.entries || r?.items || (Array.isArray(r) ? r : []);
    return arr.map((w) => ({ id: w.id, name: w.title || '', label: w.title || i18n.t('md_editor.tree.entry_fallback', { id: w.id }) }));
  }
  if (kind === 'anchor') {
    const r = await A.scripts.timeline(sid);
    const phases = r?.phases || [];
    const out = [];
    for (const ph of phases) for (const a of (ph.anchors || [])) {
      const nm = a.story_time_label || ph.phase_label || '';
      out.push({ id: a.anchor_id || a.id, name: a.story_time_label || '', label: anchorLabel(nm, a.chapter_min, a.chapter_max) });
    }
    return out;
  }
  if (kind === 'canon') {
    // canonList 默认 fetch_all(全量):新建的实体 importance=0 排最后,分页时看不到。
    const r = await A.scripts.canonList(sid);
    const arr = r?.entities || r?.items || [];
    return arr.map((e) => ({ id: e.logical_key, name: e.name, type: e.type, label: `${e.name} (${canonTypeZh(e.type)})` }));
  }
  return [];
}

export { createNode, renameNode, deleteNode, fetchGroupList };
