// node-crud.js — 文件树实体增删改 + 分组列表拉取(从 pages/md-editor.jsx 机械搬出,逐字节不变)。
import i18n from '../../i18n';
import { api, stripChapterPrefix, canonTypeZh } from './helpers.js';

// ── 实体 CRUD(树内增删改) ─────────────────────────────────────────────
async function createNode(kind, sid, name) {
  const A = api(); const nm = (name || '').trim();
  if (kind === 'chapter')   { const r = await A.scripts.addChapter(sid, nm); return { id: r.chapter_index, label: `${i18n.t('md_editor.chapter_prefix', { index: r.chapter_index })} ${r.title || ''}`.trim() }; }
  if (kind === 'worldbook') { const _def = i18n.t('md_editor.node_defaults.worldbook'); const r = await A.scripts.worldbookCreate(sid, { title: nm || _def, content: '' }); const e = r?.entry || r; return { id: e.id, label: e.title || nm || _def }; }
  if (kind === 'card')      { const _def = i18n.t('md_editor.node_defaults.card'); const r = await A.scripts.cardUpsert(sid, { name: nm || _def }); const c = r?.card || r; return { id: c.id, label: c.name || nm || _def }; }
  if (kind === 'canon')     { const _def = i18n.t('md_editor.node_defaults.canon'); const r = await A.scripts.canonUpsert(sid, { name: nm || _def, type: 'concept' }); const e = r?.entity || r; return { id: e.logical_key, label: `${e.name || nm || _def} (${canonTypeZh(e.type || 'concept')})` }; }
  if (kind === 'anchor')    { const _def = i18n.t('md_editor.node_defaults.anchor'); const r = await A.scripts.anchorCreate(sid, { story_time_label: nm || _def, chapter_min: 1, chapter_max: 1 }); const a = r?.anchor || r; return { id: a.id, label: nm || _def }; }
  throw new Error(i18n.t('md_editor.errors.create_unsupported'));
}
async function renameNode(kind, sid, id, name) {
  const A = api(); const nm = (name || '').trim(); if (!nm) return;
  if (kind === 'chapter')   { await A.scripts.updateChapter(sid, id, { title: nm }); return; }
  if (kind === 'worldbook') { await A.scripts.worldbookUpdate(sid, id, { title: nm }); return; }
  if (kind === 'anchor')    { await A.scripts.anchorUpdate(sid, id, { story_phase: nm }); return; }
  // card/canon 是全覆盖 upsert → 必须 re-fetch 全字段再改名,否则抹掉头像/属性等(历史 data-loss 坑)。
  if (kind === 'card')      { const cur = await A.scripts.cardGet(sid, id); const c = cur?.card || cur; await A.scripts.cardUpsert(sid, { ...c, id, name: nm }); return; }
  if (kind === 'canon')     { const cur = await A.scripts.canonGet(sid, id); const e = cur?.entity || cur; await A.scripts.canonUpsert(sid, { ...e, logical_key: id, name: nm }); return; }
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
    return arr.map((c) => ({ id: c.id, label: c.name + (c.full_name && c.full_name !== c.name ? ` (${c.full_name})` : '') }));
  }
  if (kind === 'worldbook') {
    const r = await A.scripts.worldbook(sid);
    const arr = r?.entries || r?.items || (Array.isArray(r) ? r : []);
    return arr.map((w) => ({ id: w.id, label: w.title || i18n.t('md_editor.tree.entry_fallback', { id: w.id }) }));
  }
  if (kind === 'anchor') {
    const r = await A.scripts.timeline(sid);
    const phases = r?.phases || [];
    const out = [];
    for (const ph of phases) for (const a of (ph.anchors || [])) {
      out.push({ id: a.anchor_id || a.id, label: `${a.story_time_label || ph.phase_label || ''} (${a.chapter_min}-${a.chapter_max})` });
    }
    return out;
  }
  if (kind === 'canon') {
    // canon-entities 列表端点在 P1 新增;暂经 graph 端点兜底。
    if (A.scripts.canonList) {
      const r = await A.scripts.canonList(sid);
      const arr = r?.entities || r?.items || [];
      return arr.map((e) => ({ id: e.logical_key, label: `${e.name} (${canonTypeZh(e.type)})` }));
    }
    try {
      const r = await A.scripts.graph(sid);
      const arr = r?.entities || [];
      return arr.map((e) => ({ id: e.logical_key, label: `${e.name} (${canonTypeZh(e.type)})` }));
    } catch (_) { return []; }
  }
  return [];
}

export { createNode, renameNode, deleteNode, fetchGroupList };
