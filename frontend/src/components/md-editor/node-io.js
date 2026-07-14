// node-io.js — 节点内容读写(加载 → toMd;保存 → diff-only fromMd,含 front-matter 冻结闸)(机械搬出,逐字节不变)。
import i18n from '../../i18n';
import { api } from './helpers.js';
import { toMd, fromMd, splitFrontMatter } from '../../lib/md-serialize.js';

// ── 节点内容 加载:GET 行 → md-serialize.toMd ─────────────────────────────
async function loadNodeContentMeta(kind, sid, id) {
  const row = await loadRow(kind, sid, id);
  // updatedAt=章节乐观锁基线(P0:AI 写库与未保存改动互覆盖);非章节实体走全量合并保存,冲突面小,V1 不带。
  return { content: toMd(kind, row), updatedAt: (kind === 'chapter' && row && row.updated_at) || null };
}
async function loadNodeContent(kind, sid, id) {
  return (await loadNodeContentMeta(kind, sid, id)).content;
}

async function loadRow(kind, sid, id) {
  const A = api();
  if (kind === 'chapter') {
    const r = await A.scripts.chapterDetail(sid, id);
    return r?.chapter ?? r ?? {};
  }
  if (kind === 'card') {
    const r = await A.cards.scriptGet(sid, id);
    return r?.card ?? r ?? {};
  }
  if (kind === 'worldbook') {
    const r = await A.scripts.worldbook(sid);
    const arr = r?.entries || r?.items || (Array.isArray(r) ? r : []);
    return arr.find((x) => String(x.id) === String(id)) || {};
  }
  if (kind === 'anchor') {
    // timeline 端点按 phase 聚合,锚点字段是子集(无 keywords/sample_title);
    // diff-based 保存只发改动字段,故未加载字段不会被覆盖(见 saveNodeContent)。
    const r = await A.scripts.timeline(sid);
    for (const ph of (r?.phases || [])) for (const a of (ph.anchors || [])) {
      if (String(a.anchor_id || a.id) === String(id)) {
        return { ...a, id: a.anchor_id || a.id, story_phase: a.story_phase || ph.phase_label || '' };
      }
    }
    return { id };
  }
  if (kind === 'canon') {
    if (A.scripts.canonGet) { const r = await A.scripts.canonGet(sid, id); return r?.entity ?? r ?? {}; }
    // 兜底:列表里找
    if (A.scripts.canonList) {
      const r = await A.scripts.canonList(sid);
      const arr = r?.entities || r?.items || [];
      return arr.find((e) => String(e.logical_key) === String(id)) || { logical_key: id };
    }
    return { logical_key: id };
  }
  return {};
}

// ── 节点内容 保存:fromMd(当前) vs fromMd(原始) 求 diff,只发改动字段 ──────────
async function saveNodeContent(kind, sid, id, content, original, baseUpdatedAt) {
  const A = api();
  // front-matter 结构冻结(权威闸):顶层字段集合不可增删改名,只能改值。编辑层 frontMatterGuard 已挡掉
  // 改键名/破围栏的交互;此处兜底拦「新增/删除顶层字段」(加项目)—— 否则 fromMd 会静默丢弃非 schema 键,
  // 用户加了字段保存后凭空消失,体验更差。差异化报错让用户知道哪个字段越界。
  if (original != null) {
    try {
      const ka = Object.keys(splitFrontMatter(original).fm || {}).sort();
      const kb = Object.keys(splitFrontMatter(content).fm || {}).sort();
      if (ka.join('') !== kb.join('')) {
        const added = kb.filter((k) => !ka.includes(k));
        const removed = ka.filter((k) => !kb.includes(k));
        const parts = [];
        if (added.length) parts.push(i18n.t('md_editor.errors.fm_added', { fields: added.join(', ') }));
        if (removed.length) parts.push(i18n.t('md_editor.errors.fm_removed', { fields: removed.join(', ') }));
        throw new Error(i18n.t('md_editor.errors.fm_frozen', { parts: parts.join(';') }));
      }
    } catch (e) {
      if (e instanceof Error && /front-matter/.test(e.message)) throw e;
      /* YAML 解析失败等:交给下面 fromMd 抛更具体的错 */
    }
  }
  const cur = fromMd(kind, content);
  const orig = original != null ? fromMd(kind, original) : {};
  const diff = diffPatch(orig, cur);
  if (Object.keys(diff).length === 0) return;   // 无实际改动

  if (kind === 'chapter') {
    // base_updated_at=乐观锁:服务端已被他方(AI 工具/另一标签)改过时 409+服务端版本,
    // 调用方转三方合并。返回新 updated_at 供刷新基线。
    const body = baseUpdatedAt ? { ...diff, base_updated_at: baseUpdatedAt } : diff;
    const r = await A.scripts.updateChapter(sid, id, body);
    return (r && r.chapter && r.chapter.updated_at) || null;
  }
  if (kind === 'card') {
    // 后端 upsert_character_card 是「全量覆盖」(缺字段→清空,含 SCHEMA 不覆盖的 avatar/metadata/
    // token_budget/priority 等)。只发 diff 会抹掉这些 → 重新拉全卡、叠加本次编辑的可写字段、整卡回写。
    const full = await A.cards.scriptGet(sid, id);
    const base = (full && full.card) ? full.card : (full || {});
    await A.cards.scriptUpsert(sid, { ...base, id, ...cur });
    return;
  }
  if (kind === 'worldbook') {
    await A.scripts.worldbookUpdate(sid, id, diff);
    return;
  }
  if (kind === 'anchor') {
    if (!A.scripts.anchorUpdate) throw new Error(i18n.t('md_editor.errors.anchor_write_not_ready'));
    await A.scripts.anchorUpdate(sid, id, diff);
    return;
  }
  if (kind === 'canon') {
    if (!A.scripts.canonUpsert) throw new Error(i18n.t('md_editor.errors.canon_write_not_ready'));
    await A.scripts.canonUpsert(sid, { logical_key: id, ...diff });
    return;
  }
  throw new Error(i18n.t('md_editor.errors.unknown_kind', { kind }));
}

// 浅 diff:返回 cur 中与 orig 不同(深比较值)的键。
function diffPatch(orig, cur) {
  const out = {};
  for (const k of Object.keys(cur)) {
    if (!deepEq(orig[k], cur[k])) out[k] = cur[k];
  }
  return out;
}
function deepEq(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a && b && typeof a === 'object') {
    const ka = Object.keys(a), kb = Object.keys(b);
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    if (ka.length !== kb.length) return false;
    return ka.every((k) => deepEq(a[k], b[k]));
  }
  return false;
}

export { loadNodeContentMeta, saveNodeContent };
