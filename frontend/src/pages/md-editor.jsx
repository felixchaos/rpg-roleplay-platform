// md-editor.jsx — VSCode 风 Markdown 编辑器(剧本知识资产内联编辑 + agent 直写)。
// 设计:docs/design/N_md_editor.md。三栏:左文件树 / 中多标签 CodeMirror / 右 agent。
// 本文件是页面壳 + 状态编排;CodeMirror 包在 components/CodeMirrorEditor.jsx(P3),
// 序列化在 lib/md-serialize.js(P2),agent 面板在 components/MdEditorAgent.jsx(P5)。
import React from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import './md-editor.css';
import { lsGet, lsSet, lsGetJSON } from '../lib/storage.js';
import CodeMirrorEditor from '../components/CodeMirrorEditor.jsx';
import MdEditorAgent from '../components/MdEditorAgent.jsx';
import EditorKbPanel, { useKbHealthBadge } from '../components/EditorKbPanel.jsx';
import { usePlaytest } from '../components/EditorPlaytest.jsx';
import { toMd, fromMd, splitFrontMatter } from '../lib/md-serialize.js';
import { runContinue } from '../lib/md-continue.js';
import { showChapterDiff, hasChapterDiff } from '../lib/md-diff.js';

// OS 自适应:Mac=⌘ / Win·Linux=Ctrl。快捷键标签 + 全局键判断都用它。
const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
const MOD_LABEL = IS_MAC ? '⌘' : 'Ctrl';
import { undo, redo, selectAll } from '@codemirror/commands';
import { openSearchPanel } from '@codemirror/search';

const { useState, useEffect, useCallback, useRef } = React;

// 顶栏图标(feather 风,单色 stroke=currentColor,非 emoji)。
const TB_PATHS = {
  undo: <><polyline points="1 4 1 10 7 10" /><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" /></>,
  redo: <><polyline points="23 4 23 10 17 10" /><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" /></>,
  copy: <><rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>,
  cut: <><circle cx="6" cy="6" r="3" /><circle cx="6" cy="18" r="3" /><line x1="20" y1="4" x2="8.12" y2="15.88" /><line x1="14.47" y1="14.48" x2="20" y2="20" /><line x1="8.12" y1="8.12" x2="12" y2="12" /></>,
  paste: <><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" /><rect x="8" y="2" width="8" height="4" rx="1" ry="1" /></>,
  // 右侧栏开合(VSCode 副边栏图标:外框 + 右侧栏分隔线)
  panelRight: <><rect x="3" y="3" width="18" height="18" rx="2" /><line x1="15" y1="3" x2="15" y2="21" /></>,
};
const TbIcon = ({ name }) => (
  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{TB_PATHS[name]}</svg>
);

// ── 可复用右键菜单(VSCode 风:定位 / 视口夹取 / 点外关 / Esc / 分隔线 / 快捷键提示 / 禁用) ──
// 文件树、标签页、编辑器正文三处共用,保证交互一致。
// items: 数组,每项 { label, kbd?, danger?, disabled?, onClick } 或 { sep:true };falsy 项自动跳过。
function ContextMenu({ x, y, items, onClose }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ x, y });
  useEffect(() => {
    // 捕获阶段:点到菜单外(含在别处再次右键)即关。监听在打开后的下一帧才挂,
    // 不会被「打开本菜单的那次 mousedown」立刻关掉。
    const onDown = (e) => { if (!ref.current || !ref.current.contains(e.target)) onClose(); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    window.addEventListener('mousedown', onDown, true);
    window.addEventListener('keydown', onKey, true);
    return () => { window.removeEventListener('mousedown', onDown, true); window.removeEventListener('keydown', onKey, true); };
  }, [onClose]);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const r = el.getBoundingClientRect();
    let nx = x, ny = y;
    if (x + r.width > window.innerWidth) nx = Math.max(4, window.innerWidth - r.width - 4);
    if (y + r.height > window.innerHeight) ny = Math.max(4, window.innerHeight - r.height - 4);
    setPos({ x: nx, y: ny });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [x, y]);
  const list = (items || []).filter(Boolean);
  return (
    <div className="mde-ctx" ref={ref} style={{ left: pos.x, top: pos.y }} onContextMenu={(e) => e.preventDefault()}>
      {list.map((it, i) => it.sep
        ? <div key={'s' + i} className="mde-ctx-sep" />
        : (
          <button key={i} className={'mde-ctx-item' + (it.danger ? ' danger' : '')} disabled={it.disabled}
            onClick={() => { onClose(); try { it.onClick && it.onClick(); } catch (_) {} }}>
            <span className="mde-ctx-label">{it.label}</span>
            {it.kbd ? <span className="mde-ctx-kbd">{it.kbd}</span> : null}
          </button>
        ))}
    </div>
  );
}

// 文件树节点类型 → 排序。label 由 t('md_editor.tree.group.KIND') 在组件内动态取。
const NODE_GROUPS = [
  { kind: 'chapter',   icon: '§' },
  { kind: 'card',      icon: '@' },
  { kind: 'worldbook', icon: '#' },
  { kind: 'anchor',    icon: '~' },
  { kind: 'canon',     icon: '*' },
];

const api = () => (typeof window !== 'undefined' ? window.api : null);
const toast = (msg, opts) => { try { window.__apiToast?.(msg, opts); } catch (_) {} };
// 章节标题存「裸标题」(不含「第N章」),显示时由前端加序号前缀。剥掉任何已混入的前缀,防重命名/重建出现「第5章 第5章 …」双序号。
const stripChapterPrefix = (s) => String(s || '').replace(/^\s*第\s*[0-9一二三四五六七八九十百千零〇两]+\s*章\s*/, '');
// Canon 实体类型本地化 → i18n md_editor.canon_type.* 键。含常见同义词回退。
const CANON_TYPE_KEYS = { character: 'character', person: 'character', faction: 'faction', organization: 'faction', org: 'faction', location: 'location', place: 'location', item: 'item', concept: 'concept', event: 'event' };
const canonTypeZh = (tp) => { const k = CANON_TYPE_KEYS[String(tp || '').toLowerCase()]; return k ? i18n.t(`md_editor.canon_type.${k}`) : (tp || i18n.t('md_editor.canon_type.concept')); };

// 每类实体图标 + 能力。章节删除走后端 delete_chapters(删一批 → 单次重排,与 merge/split 同语义:
// 结构改动后 RAG(chunks/facts/锚点按 chapter_index 外键)需重新提取对齐)。
// 拖拽重排仅世界书安全(按 priority);其余实体有结构语义,不做乱序拖拽。
const KIND_ICON = { chapter: '§', card: '@', worldbook: '#', anchor: '~', canon: '*' };
const CAN_DELETE = { chapter: true, card: true, worldbook: true, anchor: true, canon: true };
const CAN_RENAME = { chapter: true, card: true, worldbook: true, anchor: true, canon: true };
const CAN_DRAG = { worldbook: true };

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

// ── 文件树:VSCode 风资源管理器(多组展开 / 搜索 / 图标 / 工具栏 / 键盘 / 右键 / 增删改 / 拖拽)──
function FileTree({ scriptId, openNode, activeKey, reloadKey, onMutate }) {
  const { t } = useTranslation();
  const groupLabel = (kind) => t(`md_editor.tree.group.${kind}`);
  const [expanded, setExpanded] = useState(() => new Set(lsGet('mde.tree.expanded2', ['chapter']) || ['chapter']));
  const [lists, setLists] = useState({});   // kind → {loading, error, items}
  const [filter, setFilter] = useState('');
  const [sel, setSel] = useState(null);     // 键盘/焦点游标 nodeKey(单个;上下移动 / F2 / active)
  const [selSet, setSelSet] = useState(() => new Set());  // 多选集合(shift 范围 / Cmd·Ctrl 切换);批量删用
  const [anchor, setAnchor] = useState(null);             // shift 范围选的锚点 nodeKey
  const [ctx, setCtx] = useState(null);     // 右键菜单 {x,y,kind,item|null}
  const [editing, setEditing] = useState(null); // 就地编辑 {kind, id|'__new__', value}
  const [busy, setBusy] = useState(false);
  const [dragK, setDragK] = useState(null); // 拖拽中的 worldbook nodeKey
  const bodyRef = useRef(null);
  const submittingRef = useRef(false);      // 提交锁:防 Enter(onKeyDown)+ disabled 翻转引发的 onBlur 二次提交→重复新建

  const persistExpanded = (s) => lsSet('mde.tree.expanded2', [...s]);
  const loadGroup = useCallback(async (kind) => {
    if (!scriptId) return;
    setLists((s) => ({ ...s, [kind]: { ...(s[kind] || {}), loading: true } }));
    try {
      const items = await fetchGroupList(kind, scriptId);
      setLists((s) => ({ ...s, [kind]: { loading: false, items } }));
    } catch (e) {
      setLists((s) => ({ ...s, [kind]: { loading: false, error: e?.message || String(e), items: [] } }));
    }
  }, [scriptId]);

  // 切剧本 → 清缓存 + 清多选(旧 nodeKey 失效),重载所有当前展开的组。
  useEffect(() => { setLists({}); setSelSet(new Set()); setSel(null); setAnchor(null); if (scriptId) [...expanded].forEach(loadGroup); /* eslint-disable-next-line */ }, [scriptId]);
  // agent / CRUD 写库后(reloadKey 变)→ 重载所有展开组(名称/数量可能变)。
  useEffect(() => { if (reloadKey && scriptId) [...expanded].forEach(loadGroup); /* eslint-disable-next-line */ }, [reloadKey]);
  // 有搜索词时:自动加载所有组(才能跨组搜),搜索时分组全展开命中。
  useEffect(() => {
    if (!scriptId || !filter.trim()) return;
    NODE_GROUPS.forEach((g) => { if (!lists[g.kind]) loadGroup(g.kind); });
    /* eslint-disable-next-line */
  }, [filter, scriptId]);

  const toggle = (kind) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind); else { next.add(kind); if (!lists[kind]) loadGroup(kind); }
      persistExpanded(next); return next;
    });
  };
  const collapseAll = () => { setExpanded((p) => { const n = new Set(); persistExpanded(n); return n; }); };

  const q = filter.trim().toLowerCase();
  const groupItems = (kind) => ((lists[kind]?.items) || []).filter((it) => !q || (it.label || '').toLowerCase().includes(q));
  const isOpen = (kind) => q ? true : expanded.has(kind);  // 搜索时所有组展开

  // 扁平可见条目(供键盘上下移动)。
  const flat = [];
  for (const g of NODE_GROUPS) if (isOpen(g.kind)) for (const it of groupItems(g.kind)) flat.push({ kind: g.kind, id: it.id, label: it.label, meta: it });

  const startNew = (kind) => { if (!isOpen(kind)) toggle(kind); setEditing({ kind, id: '__new__', value: '' }); setCtx(null); };
  const startRename = (kind, it) => { setEditing({ kind, id: it.id, value: (kind === 'chapter') ? stripChapterPrefix(it.meta?.title ?? it.label) : it.label }); setCtx(null); };

  const commitEdit = async () => {
    if (submittingRef.current) return;        // 已在提交中(Enter 已触发,onBlur 别再发一次)
    const e = editing; if (!e) return;
    // 章节标题强制剥前缀:存裸标题,显示前端再加「第N章」,杜绝双序号。
    const nm = (e.kind === 'chapter' ? stripChapterPrefix(e.value) : (e.value || '')).trim();
    if (!nm) { setEditing(null); return; }
    submittingRef.current = true;
    setBusy(true);
    try {
      if (e.id === '__new__') {
        const created = await createNode(e.kind, scriptId, nm);
        await loadGroup(e.kind);
        onMutate?.('create', e.kind, created.id, created.label);
        openNode({ kind: e.kind, id: created.id, label: created.label });
        toast(t('md_editor.toast.created'), { kind: 'ok', duration: 1100 });
      } else {
        await renameNode(e.kind, scriptId, e.id, nm);
        await loadGroup(e.kind);
        const disp = e.kind === 'chapter' ? `${t('md_editor.chapter_prefix', { index: e.id })} ${nm}`.trim() : nm;
        onMutate?.('rename', e.kind, e.id, disp);
        toast(t('md_editor.toast.renamed'), { kind: 'ok', duration: 1100 });
      }
    } catch (err) { toast(t('md_editor.toast.op_failed'), { kind: 'danger', detail: err?.message }); }
    finally { setBusy(false); setEditing(null); submittingRef.current = false; }
  };

  const doDelete = async (kind, it) => {
    setCtx(null);
    if (!CAN_DELETE[kind]) { toast(t('md_editor.toast.delete_unsupported'), { kind: 'warning' }); return; }
    const extra = kind === 'chapter' ? '\n' + t('md_editor.confirm.chapter_delete_extra') : '';
    const ok = await (window.__confirm
      ? window.__confirm({ title: t('md_editor.confirm.delete_item'), message: `${it.label}\n${t('md_editor.confirm.irreversible')}${extra}`, danger: true, confirmText: t('common.delete') })
      : Promise.resolve(confirm(t('md_editor.confirm.delete_item_plain', { label: it.label }))));
    if (!ok) return;
    setBusy(true);
    try {
      await deleteNode(kind, scriptId, it.id);
      await loadGroup(kind);
      onMutate?.('delete', kind, it.id);
      setSelSet(new Set());
      toast(t('md_editor.toast.deleted'), { kind: 'ok', duration: 1100 });
    } catch (err) { toast(t('md_editor.toast.delete_failed'), { kind: 'danger', detail: err?.message }); }
    finally { setBusy(false); }
  };

  // 批量删除(多选)。章节走批量端点(删整批 → 单次重排,逐章删会 index 漂移删错);其余逐条删各自端点。
  const doDeleteSelected = async (keys) => {
    setCtx(null);
    const byKind = {};
    for (const g of NODE_GROUPS) for (const it of groupItems(g.kind)) {
      const k = nodeKey(g.kind, it.id);
      if (keys.has(k) && CAN_DELETE[g.kind]) (byKind[g.kind] = byKind[g.kind] || []).push(it);
    }
    const total = Object.values(byKind).reduce((n, a) => n + a.length, 0);
    if (!total) { toast(t('md_editor.toast.nothing_to_delete'), { kind: 'warning' }); return; }
    const hasChapter = (byKind.chapter || []).length > 0;
    const extra = hasChapter ? '\n' + t('md_editor.confirm.chapter_batch_delete_extra') : '';
    const ok = await (window.__confirm
      ? window.__confirm({ title: t('md_editor.confirm.delete_selected', { count: total }), message: `${t('md_editor.confirm.irreversible')}${extra}`, danger: true, confirmText: t('common.delete') })
      : Promise.resolve(confirm(t('md_editor.confirm.delete_selected', { count: total }))));
    if (!ok) return;
    setBusy(true);
    let failed = 0;
    const A = api();
    try {
      if ((byKind.chapter || []).length) {
        try { await A.scripts.deleteChapters(scriptId, byKind.chapter.map((it) => it.id)); byKind.chapter.forEach((it) => onMutate?.('delete', 'chapter', it.id)); }
        catch (e) { failed += byKind.chapter.length; toast(t('md_editor.toast.chapter_batch_delete_failed'), { kind: 'danger', detail: e?.message }); }
      }
      for (const kind of Object.keys(byKind)) {
        if (kind === 'chapter') continue;
        for (const it of byKind[kind]) {
          try { await deleteNode(kind, scriptId, it.id); onMutate?.('delete', kind, it.id); }
          catch (_) { failed++; }
        }
      }
      await Promise.all(Object.keys(byKind).map((k) => loadGroup(k)));
    } finally { setSelSet(new Set()); setBusy(false); }
    toast(failed ? t('md_editor.toast.delete_partial', { failed }) : t('md_editor.toast.deleted_count', { count: total }), { kind: failed ? 'warn' : 'ok', duration: 1500 });
  };

  // 文件树条目点击:普通=单选并打开;Cmd/Ctrl=切换多选(不打开);Shift=从锚点到此的范围选(不打开)。
  const onItemClick = (g, it, ev) => {
    const k = nodeKey(g.kind, it.id);
    if (ev.metaKey || ev.ctrlKey) {
      setSelSet((prev) => { const n = new Set(prev); if (n.has(k)) n.delete(k); else n.add(k); return n; });
      setSel(k); setAnchor(k); return;
    }
    if (ev.shiftKey && anchor) {
      const order = flat.map((f) => nodeKey(f.kind, f.id));
      const ia = order.indexOf(anchor), ik = order.indexOf(k);
      if (ia >= 0 && ik >= 0) {
        const [lo, hi] = ia < ik ? [ia, ik] : [ik, ia];
        setSelSet(new Set(order.slice(lo, hi + 1))); setSel(k); return;
      }
    }
    setSelSet(new Set([k])); setSel(k); setAnchor(k);
    openNode({ kind: g.kind, id: it.id, label: it.label, meta: it });
  };

  const duplicate = async (kind, it) => {
    setCtx(null);
    if (!CAN_RENAME[kind] || kind === 'chapter') { toast(t('md_editor.toast.copy_unsupported'), { kind: 'warning' }); return; }
    setBusy(true);
    try {
      const created = await createNode(kind, scriptId, `${it.label} ${t('md_editor.copy_suffix')}`);
      await loadGroup(kind); onMutate?.('create', kind, created.id, created.label);
      toast(t('md_editor.toast.copied'), { kind: 'ok', duration: 1100 });
    } catch (err) { toast(t('md_editor.toast.copy_failed'), { kind: 'danger', detail: err?.message }); }
    finally { setBusy(false); }
  };

  // 键盘:↑↓ 移动游标(Shift=范围扩选)/ Cmd·Ctrl+A 全选 / Enter 打开 / F2 改名 / Delete 删(支持多选)。
  const onKeyDown = (ev) => {
    if (editing) return;
    if (!flat.length) return;
    const order = flat.map((f) => nodeKey(f.kind, f.id));
    const idx = order.indexOf(sel);
    const moveTo = (ni) => {
      const n = flat[ni]; const nk = order[ni]; setSel(nk);
      if (ev.shiftKey && anchor) { const ia = order.indexOf(anchor); const [lo, hi] = ia < ni ? [ia, ni] : [ni, ia]; setSelSet(new Set(order.slice(lo, hi + 1))); }
      else { setSelSet(new Set([nk])); setAnchor(nk); }
    };
    if (ev.key === 'ArrowDown') { ev.preventDefault(); moveTo(idx < 0 ? 0 : Math.min(flat.length - 1, idx + 1)); }
    else if (ev.key === 'ArrowUp') { ev.preventDefault(); moveTo(idx < 0 ? 0 : Math.max(0, idx - 1)); }
    else if ((ev.key === 'a' || ev.key === 'A') && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); setSelSet(new Set(order)); }
    else if (ev.key === 'Enter' && idx >= 0) { ev.preventDefault(); const n = flat[idx]; openNode({ kind: n.kind, id: n.id, label: n.label, meta: n.meta }); }
    else if (ev.key === 'F2' && idx >= 0) { ev.preventDefault(); const n = flat[idx]; if (CAN_RENAME[n.kind]) startRename(n.kind, n); }
    else if (ev.key === 'Delete' || ev.key === 'Backspace') {
      ev.preventDefault();
      if (selSet.size > 1) doDeleteSelected(selSet);
      else if (idx >= 0) doDelete(flat[idx].kind, flat[idx]);
    }
  };

  // 世界书拖拽重排 → 按落点重排 priority(spaced 重编号,只 PUT 变化项)。
  const onDrop = async (kind, targetIt) => {
    if (kind !== 'worldbook' || !dragK) { setDragK(null); return; }
    const items = groupItems('worldbook');
    const from = items.findIndex((x) => nodeKey('worldbook', x.id) === dragK);
    const to = items.findIndex((x) => x.id === targetIt.id);
    setDragK(null);
    if (from < 0 || to < 0 || from === to) return;
    const reordered = items.slice(); const [moved] = reordered.splice(from, 1); reordered.splice(to, 0, moved);
    setBusy(true);
    try {
      const A = api(); const n = reordered.length;
      await Promise.all(reordered.map((it, i) => {
        const np = (n - i) * 10; // 自顶向下 priority 递减
        return (it.meta?.priority === np) ? null : A.scripts.worldbookUpdate(scriptId, it.id, { priority: np });
      }).filter(Boolean));
      await loadGroup('worldbook'); onMutate?.('reorder', 'worldbook');
      toast(t('md_editor.toast.reordered'), { kind: 'ok', duration: 1000 });
    } catch (err) { toast(t('md_editor.toast.reorder_failed'), { kind: 'danger', detail: err?.message }); }
    finally { setBusy(false); }
  };

  return (
    <div className="mde-tree" tabIndex={0} ref={bodyRef} onKeyDown={onKeyDown} onClick={() => ctx && setCtx(null)}>
      <div className="mde-tree-toolbar">
        <input className="mde-tree-filter" value={filter} placeholder={t('md_editor.tree.search_placeholder')} onChange={(e) => setFilter(e.target.value)} />
        <NewMenu onPick={startNew} />
        <button className="mde-tree-tbbtn" title={t('md_editor.tree.collapse_all')} onClick={collapseAll}>⊟</button>
        <button className="mde-tree-tbbtn" title={t('common.refresh')} onClick={() => [...expanded].forEach(loadGroup)}>⟳</button>
      </div>
      <div className="mde-tree-body">
        {NODE_GROUPS.map((g) => {
          const st = lists[g.kind] || {};
          const open = isOpen(g.kind);
          const items = groupItems(g.kind);
          if (q && open && items.length === 0 && (st.items || []).length) return null; // 搜索时无命中的组隐藏
          return (
            <div key={g.kind} className="mde-tree-group">
              <div className="mde-tree-grouprow" onContextMenu={(e) => { e.preventDefault(); setCtx({ x: e.clientX, y: e.clientY, kind: g.kind, item: null }); }}>
                <button className={'mde-tree-grouphead' + (open ? ' open' : '')} onClick={() => toggle(g.kind)}>
                  <span className="mde-tree-caret">{open ? '▾' : '▸'}</span>
                  <span className="mde-tree-gicon">{g.icon}</span>
                  <span className="mde-tree-glabel">{groupLabel(g.kind)}</span>
                  {st.items && <span className="mde-tree-count">{q ? items.length : st.items.length}</span>}
                </button>
                {CAN_CREATE_KIND(g.kind) && <button className="mde-tree-additem" title={t('md_editor.tree.new_item', { label: groupLabel(g.kind) })} onClick={(e) => { e.stopPropagation(); startNew(g.kind); }}>＋</button>}
              </div>
              {open && (
                <div className="mde-tree-children">
                  {st.loading && <div className="mde-tree-hint">{t('common.loading')}</div>}
                  {st.error && <div className="mde-tree-hint err">{t('md_editor.tree.load_failed', { error: st.error })}</div>}
                  {editing && editing.kind === g.kind && editing.id === '__new__' && (
                    <input className="mde-tree-edit" autoFocus value={editing.value}
                      placeholder={t('md_editor.tree.new_name_placeholder', { label: groupLabel(g.kind) })} disabled={busy}
                      onChange={(e) => setEditing((s) => ({ ...s, value: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === 'Enter') commitEdit(); if (e.key === 'Escape') setEditing(null); }}
                      onBlur={commitEdit} />
                  )}
                  {!st.loading && !st.error && items.length === 0 && !(editing && editing.id === '__new__' && editing.kind === g.kind) && <div className="mde-tree-hint">{t('md_editor.tree.empty')}</div>}
                  {items.map((it) => {
                    const k = nodeKey(g.kind, it.id);
                    if (editing && editing.kind === g.kind && editing.id === it.id) {
                      return (
                        <input key={k} className="mde-tree-edit" autoFocus value={editing.value} disabled={busy}
                          onChange={(e) => setEditing((s) => ({ ...s, value: e.target.value }))}
                          onKeyDown={(e) => { if (e.key === 'Enter') commitEdit(); if (e.key === 'Escape') setEditing(null); }}
                          onBlur={commitEdit} />
                      );
                    }
                    return (
                      <div
                        key={k}
                        className={'mde-tree-item' + (activeKey === k ? ' active' : '') + (selSet.has(k) ? ' sel' : '') + (sel === k ? ' cursor' : '') + (dragK === k ? ' dragging' : '')}
                        title={it.label}
                        draggable={!!CAN_DRAG[g.kind]}
                        onDragStart={() => CAN_DRAG[g.kind] && setDragK(k)}
                        onDragOver={(e) => CAN_DRAG[g.kind] && dragK && e.preventDefault()}
                        onDrop={() => onDrop(g.kind, it)}
                        onClick={(e) => onItemClick(g, it, e)}
                        onDoubleClick={() => CAN_RENAME[g.kind] && startRename(g.kind, it)}
                        onContextMenu={(e) => { e.preventDefault(); if (!selSet.has(k)) { setSelSet(new Set([k])); setSel(k); setAnchor(k); } setCtx({ x: e.clientX, y: e.clientY, kind: g.kind, item: it }); }}
                      >
                        <span className="mde-tree-iicon">{KIND_ICON[g.kind]}</span>
                        <span className="mde-tree-ilabel">{it.label || `(${g.kind} ${it.id})`}</span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {ctx && (() => {
        const k = ctx.item ? nodeKey(ctx.kind, ctx.item.id) : null;
        const multi = selSet.size > 1 && k && selSet.has(k);
        const gLabel = groupLabel(ctx.kind);
        const items = multi ? [
          { label: t('md_editor.ctx.open_count', { count: selSet.size }), onClick: () => { for (const g of NODE_GROUPS) for (const it of groupItems(g.kind)) { if (selSet.has(nodeKey(g.kind, it.id))) openNode({ kind: g.kind, id: it.id, label: it.label, meta: it }); } } },
          { sep: true },
          { label: t('md_editor.ctx.delete_selected', { count: selSet.size }), kbd: 'Del', danger: true, onClick: () => doDeleteSelected(selSet) },
        ] : ctx.item ? [
          { label: t('common.open'), onClick: () => openNode({ kind: ctx.kind, id: ctx.item.id, label: ctx.item.label, meta: ctx.item }) },
          CAN_RENAME[ctx.kind] && { label: t('md_editor.ctx.rename'), kbd: 'F2', onClick: () => startRename(ctx.kind, ctx.item) },
          (CAN_RENAME[ctx.kind] && ctx.kind !== 'chapter') && { label: t('md_editor.ctx.duplicate'), onClick: () => duplicate(ctx.kind, ctx.item) },
          CAN_CREATE_KIND(ctx.kind) && { sep: true },
          CAN_CREATE_KIND(ctx.kind) && { label: t('md_editor.ctx.new_item', { label: gLabel }), onClick: () => startNew(ctx.kind) },
          { sep: true },
          { label: t('common.delete'), kbd: 'Del', danger: true, disabled: !CAN_DELETE[ctx.kind], onClick: () => doDelete(ctx.kind, ctx.item) },
        ] : [
          CAN_CREATE_KIND(ctx.kind) && { label: t('md_editor.ctx.new_item', { label: gLabel }), onClick: () => startNew(ctx.kind) },
        ];
        return <ContextMenu x={ctx.x} y={ctx.y} items={items} onClose={() => setCtx(null)} />;
      })()}
    </div>
  );
}

const CAN_CREATE_KIND = () => true; // 5 类都支持新建
function NewMenu({ onPick }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="mde-newmenu">
      <button className="mde-tree-tbbtn" title={t('md_editor.tree.new')} onClick={() => setOpen((o) => !o)}>＋</button>
      {open && (
        <div className="mde-newmenu-pop" onMouseLeave={() => setOpen(false)}>
          {NODE_GROUPS.map((g) => (
            <button key={g.kind} onClick={() => { setOpen(false); onPick(g.kind); }}>
              <span className="mde-tree-gicon">{g.icon}</span> {t(`md_editor.tree.group.${g.kind}`)}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export const nodeKey = (kind, id) => `${kind}:${id}`;

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

// ── 标签编辑器(P0:textarea;P3 替换为 CodeMirror)──────────────────────
function EditorPane({ tab, onChange, scriptId, onViewReady, onContinueAccept, chapterIndex, onSelectionChange, ghostEnabled, ghostFetch }) {
  const { t } = useTranslation();
  if (!tab) {
    return <div className="mde-empty">{t('md_editor.editor.empty_hint')}<br /><span className="muted">{t('md_editor.editor.empty_kinds')}</span></div>;
  }
  if (tab.loading) return <div className="mde-empty">{t('common.loading')}</div>;
  if (tab.error) return <div className="mde-empty err">{t('md_editor.editor.load_failed', { error: tab.error })}</div>;
  return (
    <CodeMirrorEditor
      value={tab.content}
      docKey={tab.key}
      onChange={(v) => onChange(tab.key, v)}
      scriptId={scriptId}
      onViewReady={onViewReady}
      onContinueAccept={onContinueAccept}
      chapterIndex={chapterIndex}
      onSelectionChange={onSelectionChange}
      ghostEnabled={ghostEnabled && tab.kind === 'chapter'}
      ghostFetch={ghostFetch}
    />
  );
}

const KIND_LABEL_KEY = { chapter: 'md_editor.tree.group_chapter', card: 'md_editor.tree.group_card', worldbook: 'md_editor.tree.group_worldbook', anchor: 'md_editor.tree.group_anchor', canon: 'md_editor.tree.group_canon' };
function kindLabelZh(kind) { return i18n.t(KIND_LABEL_KEY[kind] || '', { defaultValue: ({ chapter: '章节', card: '角色卡', worldbook: '世界书', anchor: '时间线', canon: '设定' })[kind] || kind }); }

// 快速打开(Mod+P,VSCode 风):跨所有实体类型模糊过滤 + 键盘选择 + 回车打开。
function QuickOpen({ scriptId, openNode, onClose }) {
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const [items, setItems] = useState(null);
  const [sel, setSel] = useState(0);
  const inputRef = useRef(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const lists = await Promise.all(NODE_GROUPS.map((g) =>
          fetchGroupList(g.kind, scriptId).then((arr) => arr.map((it) => ({ kind: g.kind, id: it.id, label: it.label, icon: g.icon }))).catch(() => [])));
        if (!cancelled) setItems(lists.flat());
      } catch (_) { if (!cancelled) setItems([]); }
    })();
    setTimeout(() => inputRef.current && inputRef.current.focus(), 30);
    return () => { cancelled = true; };
  }, [scriptId]);
  const filtered = (items || []).filter((it) => !q || (it.label || '').toLowerCase().includes(q.toLowerCase())).slice(0, 60);
  const choose = (it) => { if (!it) return; try { openNode({ kind: it.kind, id: it.id, label: it.label, meta: {} }); } catch (_) {} onClose(); };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(filtered[sel]); }
  };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-qopen" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={inputRef} className="mde-qopen-input" value={q}
          placeholder={t('md_editor.quickopen.placeholder', { defaultValue: '快速打开:章节 / 角色卡 / 世界书 / 时间线 / 设定…' })}
          onChange={(e) => { setQ(e.target.value); setSel(0); }} onKeyDown={onKey} />
        <div className="mde-qopen-list">
          {items === null ? <div className="mde-qopen-empty">{t('common.loading')}</div>
            : filtered.length === 0 ? <div className="mde-qopen-empty">{t('md_editor.quickopen.none', { defaultValue: '无匹配' })}</div>
              : filtered.map((it, i) => (
                <div key={it.kind + ':' + it.id} className={'mde-qopen-item' + (i === sel ? ' sel' : '')}
                  onMouseEnter={() => setSel(i)} onMouseDown={() => choose(it)}>
                  <span className="mde-qopen-icon">{it.icon || '·'}</span>
                  <span className="mde-qopen-label">{it.label}</span>
                  <span className="mde-qopen-kind">{kindLabelZh(it.kind)}</span>
                </div>
              ))}
        </div>
      </div>
    </div>
  );
}

// 全书检索(Mod+Shift+F):调 owner-scoped /search 端点,结果点击跳到对应章节。
function GlobalSearch({ scriptId, openNode, onClose }) {
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const [regex, setRegex] = useState(false);
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);
  useEffect(() => { setTimeout(() => inputRef.current && inputRef.current.focus(), 30); }, []);
  const run = useCallback(async () => {
    const query = q.trim();
    if (!query) { setRes(null); return; }
    setLoading(true);
    try {
      const r = await api().scripts.search(scriptId, query, { regex: regex ? 'true' : 'false', limit: 100 });
      setRes(r && r.ok ? r : { results: [], total: 0, error: (r && r.error) || '搜索失败' });
    } catch (e) { setRes({ results: [], total: 0, error: e?.message || '搜索失败' }); }
    finally { setLoading(false); }
  }, [q, regex, scriptId]);
  const onKey = (e) => { if (e.key === 'Enter') { e.preventDefault(); run(); } else if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
  const choose = (h) => { try { openNode({ kind: 'chapter', id: h.chapter_index, label: `${i18n.t('md_editor.chapter_prefix', { index: h.chapter_index })} ${stripChapterPrefix(h.title || '')}`.trim(), meta: {} }); } catch (_) {} onClose(); };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-search" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mde-search-bar">
          <input ref={inputRef} className="mde-search-input" value={q}
            placeholder={t('md_editor.search.placeholder', { defaultValue: '全书检索(回车搜)' })}
            onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} />
          <button type="button" className={'mde-search-rx' + (regex ? ' on' : '')} title={t('md_editor.search.regex', { defaultValue: '正则' })} onClick={() => setRegex((v) => !v)}>.*</button>
          <button type="button" className="mde-search-go" onClick={run} disabled={loading}>{loading ? '…' : t('md_editor.search.go', { defaultValue: '搜索' })}</button>
        </div>
        <div className="mde-search-list">
          {res === null ? <div className="mde-qopen-empty">{t('md_editor.search.hint', { defaultValue: '输入关键词,回车搜全书' })}</div>
            : res.error ? <div className="mde-qopen-empty">{res.error}</div>
              : (res.results || []).length === 0 ? <div className="mde-qopen-empty">{t('md_editor.search.none', { defaultValue: '0 命中' })}</div>
                : (
                  <>
                    <div className="mde-search-count">{t('md_editor.search.count', { total: res.total, n: res.chapters || 0, defaultValue: '{{total}} 处命中 · {{n}} 章' })}{res.capped ? ' +' : ''}</div>
                    {res.results.map((h, i) => (
                      <div key={i} className="mde-search-item" onMouseDown={() => choose(h)}>
                        <div className="mde-search-loc">{i18n.t('md_editor.chapter_prefix', { index: h.chapter_index })} {stripChapterPrefix(h.title || '')}</div>
                        <div className="mde-search-snip">{h.pre}{h.snippet}{h.suf}</div>
                      </div>
                    ))}
                  </>
                )}
        </div>
      </div>
    </div>
  );
}

// 章节版本历史(checkpoints):列 AI 改动 + 一键回滚到历史任意版本之前。
function ChapterHistory({ scriptId, chapterIndex, onClose, onRestored }) {
  const { t } = useTranslation();
  const [versions, setVersions] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try { const r = await api().scripts.chapterHistory(scriptId, chapterIndex); if (!cancelled) setVersions(r && r.ok ? (r.versions || []) : []); }
      catch (_) { if (!cancelled) setVersions([]); }
    })();
    return () => { cancelled = true; };
  }, [scriptId, chapterIndex]);
  const restore = async (cid) => {
    if (busy) return; setBusy(true);
    try {
      const r = await api().scripts.chapterRestore(scriptId, chapterIndex, cid);
      if (r && r.ok) { toast(t('md_editor.history.restored', { defaultValue: '已恢复到该版本之前' }), { kind: 'ok' }); onRestored && onRestored(); }
      else toast((r && r.error) || t('md_editor.history.restore_fail', { defaultValue: '恢复失败' }), { kind: 'error' });
    } catch (_) { toast(t('md_editor.history.restore_fail', { defaultValue: '恢复失败' }), { kind: 'error' }); }
    finally { setBusy(false); }
  };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-history" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mde-history-head">{t('md_editor.history.title', { n: chapterIndex, defaultValue: '第{{n}}章 · AI 改动历史' })}</div>
        <div className="mde-history-list">
          {versions === null ? <div className="mde-qopen-empty">{t('common.loading')}</div>
            : versions.length === 0 ? <div className="mde-qopen-empty">{t('md_editor.history.none', { defaultValue: '本章暂无 AI 改动历史' })}</div>
              : versions.map((v) => (
                <div key={v.id} className="mde-history-item">
                  <div className="mde-history-meta">
                    <span className="mde-history-msg">{v.message || v.kind}</span>
                    <span className="mde-history-time">{v.created_at ? new Date(v.created_at).toLocaleString() : ''}</span>
                  </div>
                  {v.kind === 'chapter_edit' && v.has_before && !v.undone
                    ? <button type="button" className="mde-history-restore" disabled={busy} onMouseDown={() => restore(v.id)}>{t('md_editor.history.restore', { defaultValue: '恢复到此前' })}</button>
                    : <span className="mde-history-tag">{v.kind === 'chapter_revert' ? t('md_editor.history.revert', { defaultValue: '回滚' }) : (v.undone ? t('md_editor.history.undone', { defaultValue: '已撤销' }) : '')}</span>}
                </div>
              ))}
        </div>
      </div>
    </div>
  );
}

// 写作规范(.cursorrules 风):per-script 风格/连贯/禁忌规则,保存后注入编辑器 agent 最高优先上下文。
function WritingRules({ scriptId, onClose }) {
  const { t } = useTranslation();
  const [text, setText] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try { const r = await api().scripts.writingRules(scriptId); if (!cancelled) setText(r && r.ok ? (r.rules || '') : ''); }
      catch (_) { if (!cancelled) setText(''); }
    })();
    return () => { cancelled = true; };
  }, [scriptId]);
  const save = async () => {
    if (busy) return; setBusy(true);
    try {
      const r = await api().scripts.saveWritingRules(scriptId, text || '');
      if (r && r.ok) { toast(t('md_editor.rules.saved', { defaultValue: '写作规范已保存,下次对话起生效' }), { kind: 'ok' }); onClose(); }
      else toast((r && r.error) || t('md_editor.rules.fail', { defaultValue: '保存失败' }), { kind: 'error' });
    } catch (_) { toast(t('md_editor.rules.fail', { defaultValue: '保存失败' }), { kind: 'error' }); }
    finally { setBusy(false); }
  };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-rules" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mde-rules-head">{t('md_editor.rules.title', { defaultValue: '写作规范(注入 AI 上下文,务必遵守)' })}</div>
        <div className="mde-rules-desc">{t('md_editor.rules.desc', { defaultValue: '像给 AI 立的规矩:风格/人称/视角、连贯禁忌、命名约定…AI 续写/改写时最高优先遵守。' })}</div>
        {text === null ? <div className="mde-qopen-empty">{t('common.loading')}</div>
          : <textarea className="mde-rules-ta" value={text} onChange={(e) => setText(e.target.value)}
              placeholder={t('md_editor.rules.placeholder', { defaultValue: '例:全程第三人称限制视角(只跟主角)；禁止上帝视角剧透；地名统一用「云墟城」不用「云墟」；台词不带现代网络词。' })} />}
        <div className="mde-rules-btns">
          <button type="button" className="mde-rules-save" disabled={busy || text === null} onClick={save}>{t('common.save', { defaultValue: '保存' })}</button>
          <button type="button" className="mde-rules-cancel" onClick={onClose}>{t('common.cancel')}</button>
        </div>
      </div>
    </div>
  );
}

// 审稿问题面板(VSCode Problems 风):编辑器 agent 调 report_writing_issues 持久化的问题清单,
// 按严重度排序,可逐条「跳转章节」/「消除」,或一键清空。owner-scoped(后端 script_owned 校验)。
function ProblemsPanel({ scriptId, reloadKey, onJump, onClose, onCountChange }) {
  const { t } = useTranslation();
  const [issues, setIssues] = useState(null);
  const load = useCallback(async () => {
    try { const r = await api().scripts.issues(scriptId); const arr = (r && r.ok) ? (r.issues || []) : []; setIssues(arr); onCountChange && onCountChange(arr.length); }
    catch (_) { setIssues([]); }
  }, [scriptId]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setIssues(null); load(); }, [scriptId, reloadKey, load]);
  const dismiss = async (iid) => {
    try { await api().scripts.dismissIssue(scriptId, iid); } catch (_) {}
    setIssues((cur) => { const next = (cur || []).filter((x) => x.id !== iid); onCountChange && onCountChange(next.length); return next; });
  };
  const clearAll = async () => {
    try { await api().scripts.clearIssues(scriptId); } catch (_) {}
    setIssues([]); onCountChange && onCountChange(0);
  };
  const sevClass = (sev) => {
    const s = String(sev || '').toLowerCase();
    if (s === '高' || s === 'high') return 'high';
    if (s === '中' || s === 'medium') return 'mid';
    if (s === '低' || s === 'low') return 'low';
    return '';
  };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-problems" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mde-problems-head">
          <span className="mde-problems-title">{t('md_editor.problems.title', { defaultValue: '审稿问题' })}{Array.isArray(issues) ? ` · ${issues.length}` : ''}</span>
          {Array.isArray(issues) && issues.length > 0 && (
            <button type="button" className="mde-problems-clear" onClick={clearAll}>{t('md_editor.problems.clear', { defaultValue: '全部清空' })}</button>
          )}
          <button type="button" className="mde-problems-x" title={t('common.close')} onClick={onClose}>×</button>
        </div>
        {issues === null ? <div className="mde-qopen-empty">{t('common.loading')}</div>
          : issues.length === 0 ? (
            <div className="mde-problems-empty">{t('md_editor.problems.empty', { defaultValue: '暂无审稿问题。让 AI 通读/查矛盾/查伏笔回收后会在这里列出。' })}</div>
          ) : (
            <ul className="mde-problems-list">
              {issues.map((it) => (
                <li key={it.id} className="mde-problem">
                  <div className="mde-problem-meta">
                    {it.severity && <span className={'mde-problem-sev ' + sevClass(it.severity)}>{String(it.severity)}</span>}
                    {it.type && <span className="mde-problem-type">{String(it.type)}</span>}
                    {it.chapter != null && (
                      <button type="button" className="mde-problem-jump" onClick={() => onJump(it.chapter)}>
                        {t('md_editor.problems.chapter', { n: it.chapter, defaultValue: '第{{n}}章 ↗' })}
                      </button>
                    )}
                    <span className="mde-problem-spacer" />
                    <button type="button" className="mde-problem-dismiss" title={t('md_editor.problems.dismiss', { defaultValue: '消除' })} onClick={() => dismiss(it.id)}>×</button>
                  </div>
                  <div className="mde-problem-detail">{String(it.detail || '')}</div>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  );
}

// ── 主页面 ───────────────────────────────────────────────────────────
export default function MdEditorPage() {
  const { t } = useTranslation();
  const [scripts, setScripts] = useState(null);
  // lsGet 返回裸字符串;剧本 id 是整数 → 必须 Number 化,否则 `s.id === scriptId`(数===串)恒不等 → 刷新后工作区显示「未选择」。
  const [scriptId, setScriptId] = useState(() => { const v = lsGet('mde.scriptId', null); return (v == null || v === '') ? null : (Number(v) || v); });
  const [tabs, setTabs] = useState([]);          // [{key, kind, id, label, content, original, loading, error, dirty}]
  const [activeKey, setActiveKey] = useState(null);
  const [treeReloadKey, setTreeReloadKey] = useState(0);   // agent 写库后 bump,触发文件树重载
  const activeViewRef = useRef(null);                      // 当前 CodeMirror 视图(供侧栏「续写到正文」)
  const agentRef = useRef(null);                           // MdEditorAgent 命令句柄(续写后同步桥接)
  const activeRef = useRef(null);                          // 当前标签(在接受续写的回调里读最新 label)
  const [syncNudge, setSyncNudge] = useState(null);        // 接受续写后提示同步:{text, label, rewrite} | null
  const [selLen, setSelLen] = useState(0);                 // 正文当前选中字数(右栏「选区改写」+ 选区上下文芯片)
  const [cursor, setCursor] = useState({ line: 1, col: 1, total: 0 });   // 底部状态栏:光标行:列 + 总字数
  const [leftHidden, setLeftHidden] = useState(false);     // 左侧文件树折叠(Mod+B,VSCode 风)
  const [quickOpen, setQuickOpen] = useState(false);       // Mod+P 快速打开
  const [searchOpen, setSearchOpen] = useState(false);     // Mod+Shift+F 全书检索
  const [historyFor, setHistoryFor] = useState(null);      // 章节版本历史(chapter_index | null)
  const [rulesOpen, setRulesOpen] = useState(false);       // 写作规范(.cursorrules)编辑弹窗
  const [kbOpen, setKbOpen] = useState(false);             // 知识库中心抽屉(P1:治理能力接进 IDE)
  const kbBadge = useKbHealthBadge(scriptId);              // 顶栏徽标:{stale_count, ready} | null
  const { playtest, busy: playtestBusy } = usePlaytest(scriptId);   // P1:写完即试玩
  const [issuesOpen, setIssuesOpen] = useState(false);     // 审稿问题面板(VSCode Problems 风)
  const [issueCount, setIssueCount] = useState(0);         // 顶栏「问题」徽标计数
  const [issuesReloadKey, setIssuesReloadKey] = useState(0); // 编辑器 agent 汇报问题后 bump → 重载
  const [autoComplete, setAutoComplete] = useState(() => lsGet('mde.autocomplete', '0') === '1');  // 内联续写(Copilot ghost)开关,默认关(BYO 计费)
  // 选区/光标上报(对象):右栏要 selLen(数字),状态栏要 line/col/total。
  const onSel = useCallback((info) => {
    if (info && typeof info === 'object') {
      setSelLen(info.len || 0);
      setCursor({ line: info.line || 1, col: info.col || 1, total: info.total || 0 });
    } else { setSelLen(info || 0); }
  }, []);
  // 读当前编辑器选区 + 上下文(供右栏 agent 把选中正文作为上下文)。在发送时实时读,保证拿到最新选区。
  const getSelectionContext = useCallback(() => {
    const v = activeViewRef.current;
    if (!v) return null;
    const sel = v.state.selection.main;
    if (sel.empty) return null;
    const doc = v.state.doc;
    return {
      selection: doc.sliceString(sel.from, sel.to),
      before: doc.sliceString(Math.max(0, sel.from - 1200), sel.from),
      after: doc.sliceString(sel.to, Math.min(doc.length, sel.to + 600)),
    };
  }, []);

  // 拉剧本列表(仅自己拥有的可编辑)。
  useEffect(() => {
    (async () => {
      try {
        const r = await api().scripts.list();
        const arr = r?.items || r?.scripts || (Array.isArray(r) ? r : []);
        const owned = arr.filter((s) => s.is_owner !== false && s.role !== 'subscriber');
        setScripts(owned);
        if (!scriptId && owned[0]) { setScriptId(owned[0].id); lsSet('mde.scriptId', owned[0].id); }
      } catch (e) { setScripts([]); toast(t('md_editor.toast.scripts_load_failed'), { kind: 'danger', detail: e?.message }); }
    })();
    // eslint-disable-next-line
  }, []);

  const pickScript = (id) => { setScriptId(id); lsSet('mde.scriptId', id); setTabs([]); setActiveKey(null); setMenu(null); };

  // ── 顶栏菜单 + 可拖拽分栏 ──────────────────────────────────────────────
  const [menu, setMenu] = useState(null);   // 'ws' | 'file' | 'edit' | null
  const [tabCtx, setTabCtx] = useState(null);     // 标签页右键菜单 {x,y,key}
  const [editorCtx, setEditorCtx] = useState(null); // 编辑器正文右键菜单 {x,y}
  const panesRef = useRef(null);
  const [leftW, setLeftW] = useState(() => { const n = Number(lsGet('mde.leftW', 240)); return n >= 150 && n <= 480 ? n : 240; });
  const [rightW, setRightW] = useState(() => { const n = Number(lsGet('mde.rightW', 320)); return n >= 220 && n <= 560 ? n : 320; });
  // 右栏(AI 助手)开合:有持久化用之;否则宽屏默认开、窄屏默认关(避免小屏一进来就被浮层盖住,且提供入口)。
  const [rightOpen, setRightOpen] = useState(() => {
    const v = lsGet('mde.rightOpen', null);
    if (v === '1' || v === true || v === 1) return true;
    if (v === '0' || v === false || v === 0) return false;
    return typeof window !== 'undefined' ? window.innerWidth > 1100 : true;
  });
  const toggleRight = useCallback(() => setRightOpen((v) => { const n = !v; lsSet('mde.rightOpen', n ? '1' : '0'); return n; }), []);
  const dragRef = useRef(null);
  const onSplitDown = (side) => (e) => {
    e.preventDefault();
    const startX = e.clientX, startLeft = leftW, startRight = rightW;
    const move = (ev) => {
      const dx = ev.clientX - startX;
      const w = side === 'left'
        ? Math.max(150, Math.min(480, startLeft + dx))
        : Math.max(220, Math.min(560, startRight - dx));
      panesRef.current?.style.setProperty(side === 'left' ? '--mde-left-w' : '--mde-right-w', w + 'px');
      dragRef.current = { side, w };
    };
    const up = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up);
      document.body.style.cursor = ''; document.body.style.userSelect = '';
      const d = dragRef.current; dragRef.current = null;
      if (d) { if (d.side === 'left') { setLeftW(d.w); lsSet('mde.leftW', d.w); } else { setRightW(d.w); lsSet('mde.rightW', d.w); } }
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up);
    document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none';
  };

  // 顶栏「编辑」操作:全部作用于当前 CodeMirror 视图(activeViewRef)。
  const withView = useCallback((fn) => { const v = activeViewRef.current; if (!v) { toast(t('md_editor.toast.open_file_first'), { kind: 'warn', duration: 1400 }); return; } v.focus(); fn(v); }, [t]);
  const doUndo = useCallback(() => withView((v) => undo(v)), [withView]);
  const doRedo = useCallback(() => withView((v) => redo(v)), [withView]);
  const doSelectAll = useCallback(() => withView((v) => selectAll(v)), [withView]);
  const doFind = useCallback(() => withView((v) => openSearchPanel(v)), [withView]);
  const doCopy = useCallback(() => withView(async (v) => { const s = v.state.sliceDoc(v.state.selection.main.from, v.state.selection.main.to); if (!s) return; try { await navigator.clipboard.writeText(s); } catch (_) { toast(t('md_editor.toast.copy_failed_kbd', { kbd: '⌘C' }), { kind: 'warn' }); } }), [withView, t]);
  const doCut = useCallback(() => withView(async (v) => { const sel = v.state.selection.main; const s = v.state.sliceDoc(sel.from, sel.to); if (!s) return; try { await navigator.clipboard.writeText(s); v.dispatch({ changes: { from: sel.from, to: sel.to } }); } catch (_) { toast(t('md_editor.toast.cut_failed_kbd', { kbd: '⌘X' }), { kind: 'warn' }); } }), [withView, t]);
  const doPaste = useCallback(() => withView(async (v) => { try { const txt = await navigator.clipboard.readText(); if (!txt) return; const sel = v.state.selection.main; v.dispatch({ changes: { from: sel.from, to: sel.to, insert: txt }, selection: { anchor: sel.from + txt.length } }); } catch (_) { toast(t('md_editor.toast.paste_failed_kbd', { kbd: '⌘V' }), { kind: 'warn' }); } }), [withView, t]);
  const doGotoLine = useCallback(() => withView((v) => { const raw = window.prompt(t('md_editor.prompt.goto_line')); const n = Number(raw); if (!n || n < 1) return; const line = v.state.doc.line(Math.min(Math.floor(n), v.state.doc.lines)); v.dispatch({ selection: { anchor: line.from }, scrollIntoView: true }); }), [withView, t]);

  // 文件菜单:重命名 / 删除当前剧本(严格 owner,后端 403 兜底)。
  const renameScript = useCallback(async () => {
    setMenu(null);
    if (!scriptId) return;
    const cur = (scripts || []).find((s) => s.id === scriptId);
    const name = window.prompt(t('md_editor.prompt.rename_script'), cur?.title || '');
    if (name == null) return;
    const nm = name.trim(); if (!nm) return;
    try { await api().scripts.rename(scriptId, nm); setScripts((prev) => (prev || []).map((s) => s.id === scriptId ? { ...s, title: nm } : s)); toast(t('md_editor.toast.renamed'), { kind: 'ok', duration: 1200 }); }
    catch (e) { toast(t('md_editor.toast.rename_failed'), { kind: 'danger', detail: e?.message }); }
  }, [scriptId, scripts]);
  const deleteScript = useCallback(async () => {
    setMenu(null);
    if (!scriptId) return;
    const cur = (scripts || []).find((s) => s.id === scriptId);
    const ok = await (window.__confirm
      ? window.__confirm({ title: t('md_editor.confirm.delete_script'), message: t('md_editor.confirm.delete_script_msg', { title: cur?.title || scriptId }), danger: true, confirmText: t('common.delete') })
      : Promise.resolve(window.confirm(t('md_editor.confirm.delete_script_plain'))));
    if (!ok) return;
    try {
      await api().scripts.delete(scriptId, { force: true });
      const rest = (scripts || []).filter((s) => s.id !== scriptId);
      setScripts(rest);
      if (rest[0]) pickScript(rest[0].id);
      else { setScriptId(null); lsSet('mde.scriptId', null); setTabs([]); setActiveKey(null); }
      toast(t('md_editor.toast.script_deleted'), { kind: 'ok', duration: 1400 });
    } catch (e) { toast(t('md_editor.toast.delete_failed'), { kind: 'danger', detail: e?.message }); }
  }, [scriptId, scripts]);

  // 顶栏菜单:点击外部关闭。
  useEffect(() => {
    if (!menu) return;
    const onDown = (e) => { if (!e.target.closest?.('.mde-menuwrap')) setMenu(null); };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [menu]);

  // 作者优先:从零新建空白剧本 → 切到它(自动带第1章)。
  const createBlankScript = async () => {
    try {
      const r = await api().scripts.createBlank(t('md_editor.node_defaults.script'));
      if (!r?.script_id) throw new Error(r?.error || t('md_editor.errors.create_failed'));
      setScripts((prev) => [{ id: r.script_id, title: r.title }, ...(prev || [])]);
      pickScript(r.script_id);
      toast(t('md_editor.toast.blank_script_created'), { kind: 'ok', duration: 1400 });
    } catch (e) { toast(t('md_editor.toast.script_create_failed'), { kind: 'danger', detail: e?.message }); }
  };
  // 给当前剧本追加一章并打开。
  const addChapter = async () => {
    if (!scriptId) return;
    try {
      const r = await api().scripts.addChapter(scriptId, '');
      if (!r?.chapter_index) throw new Error(r?.error || t('md_editor.errors.create_failed'));
      setTreeReloadKey((x) => x + 1);
      openNode({ kind: 'chapter', id: r.chapter_index, label: `${t('md_editor.chapter_prefix', { index: r.chapter_index })} ${r.title || ''}`.trim() });
      toast(t('md_editor.toast.chapter_created'), { kind: 'ok', duration: 1200 });
    } catch (e) { toast(t('md_editor.toast.chapter_create_failed'), { kind: 'danger', detail: e?.message }); }
  };

  // 打开节点 → 新标签(或激活已开)。
  const openNode = useCallback(async (node) => {
    const key = nodeKey(node.kind, node.id);
    setActiveKey(key);
    setTabs((cur) => {
      if (cur.some((t) => t.key === key)) return cur;
      return [...cur, { key, kind: node.kind, id: node.id, label: node.label, content: '', original: '', loading: true, error: null, dirty: false }];
    });
    try {
      const meta = await loadNodeContentMeta(node.kind, scriptId, node.id);
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, content: meta.content, original: meta.content, baseUpdatedAt: meta.updatedAt, loading: false } : t));
    } catch (e) {
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, loading: false, error: e?.message || String(e) } : t));
    }
  }, [scriptId]);

  const onEdit = useCallback((key, val) => {
    setTabs((cur) => cur.map((t) => t.key === key ? { ...t, content: val, dirty: val !== t.original } : t));
  }, []);

  // 顶栏「问题」徽标:载入剧本/agent 汇报后拉一次计数(面板未开也显示数量)。
  useEffect(() => {
    if (!scriptId) { setIssueCount(0); return; }
    let cancelled = false;
    (async () => {
      try { const r = await api().scripts.issues(scriptId); if (!cancelled) setIssueCount(r && r.ok ? (r.issues || []).length : 0); }
      catch (_) {}
    })();
    return () => { cancelled = true; };
  }, [scriptId, issuesReloadKey]);

  // 刷新 / 切换工作区:恢复该剧本上次打开的标签页 + 激活标签(用户缓存,刷新不丢上下文)。
  useEffect(() => {
    if (!scriptId) return;
    const saved = lsGetJSON('mde.tabs.' + scriptId, null);
    const savedActive = lsGet('mde.activeKey.' + scriptId, null);
    if (!Array.isArray(saved) || !saved.length) return;
    let cancelled = false;
    (async () => {
      for (const t of saved) { if (cancelled) return; await openNode({ kind: t.kind, id: t.id, label: t.label }); }
      if (!cancelled && savedActive) setActiveKey(savedActive);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scriptId]);

  // 持久化已打开标签:只在非空时写,避免切换工作区瞬间的空态把缓存清掉。
  useEffect(() => {
    if (!scriptId || !tabs.length) return;
    try {
      lsSet('mde.tabs.' + scriptId, JSON.stringify(tabs.map((t) => ({ kind: t.kind, id: t.id, label: t.label }))));
      lsSet('mde.activeKey.' + scriptId, activeKey || '');
    } catch (_) {}
  }, [tabs, activeKey, scriptId]);

  const closeTab = useCallback(async (key) => {
    // 修复:局部 `const t` 曾遮蔽 useTranslation 的 t() → t('…') 把 tab 对象当函数调 = TypeError;
    // 且 message: tab.label 引用了未定义的 `tab`(实际变量被命名为遮蔽的 t)→ ReferenceError。
    // 关闭「有未保存改动」的标签必崩。改名局部为 tab,恢复 t() 为翻译函数。
    const tab = tabs.find((x) => x.key === key);
    if (tab?.dirty) {
      const ok = await (window.__confirm ? window.__confirm({ title: t('md_editor.confirm.discard_changes'), message: tab.label, danger: true, confirmText: t('md_editor.confirm.discard') }) : Promise.resolve(confirm(t('md_editor.confirm.discard_changes'))));
      if (!ok) return;
    }
    setTabs((cur) => {
      const idx = cur.findIndex((x) => x.key === key);
      const next = cur.filter((x) => x.key !== key);
      if (activeKey === key) setActiveKey(next[Math.max(0, idx - 1)]?.key || null);
      return next;
    });
  }, [tabs, activeKey]);

  // 批量关闭标签(关闭其他/右侧/已保存/全部):若集合里有未保存的,先确认一次。
  const closeTabs = useCallback(async (keys) => {
    const set = keys instanceof Set ? keys : new Set(keys);
    const targets = tabs.filter((t) => set.has(t.key));
    if (!targets.length) return;
    const dirtyCnt = targets.filter((t) => t.dirty).length;
    if (dirtyCnt) {
      const ok = await (window.__confirm
        ? window.__confirm({ title: t('md_editor.confirm.discard_tabs', { count: dirtyCnt }), message: t('md_editor.confirm.discard_tabs_msg'), danger: true, confirmText: t('md_editor.confirm.discard_and_close') })
        : Promise.resolve(confirm(t('md_editor.confirm.discard_tabs_plain', { count: dirtyCnt }))));
      if (!ok) return;
    }
    setTabs((cur) => {
      const next = cur.filter((t) => !set.has(t.key));
      if (set.has(activeKey)) setActiveKey(next[next.length - 1]?.key || null);
      return next;
    });
  }, [tabs, activeKey]);

  // 冲突三方合并(P0):把「服务端新版 vs 本地未保存」丢进现成逐块 diff 审阅。
  // 全部批准=采用对方;全部拒绝=保留自己(以服务端版本为基线强制落库);逐块=合并保存。
  // 仅当该章节是激活标签(有 EditorView)时可用,返回 false 让调用方走降级提示。
  const openConflictMerge = useCallback((key, localContent, serverMd, serverTs) => {
    const view = activeViewRef.current;
    const a = activeRef.current;
    if (!view || !a || a.key !== key || a.kind !== 'chapter') return false;
    const applyTab = (content, ts) => setTabs((cur) => cur.map((x) => x.key === key
      ? { ...x, content, original: content, dirty: false, saving: false, conflict: false, baseUpdatedAt: ts } : x));
    const forceSave = async (finalText, okMsg) => {
      try {
        const ts = await saveNodeContent('chapter', scriptId, a.id, finalText, serverMd, serverTs);
        applyTab(finalText, ts || serverTs);
        toast(okMsg, { kind: 'ok', duration: 1600 });
      } catch (e2) {
        toast(t('md_editor.toast.save_failed'), { kind: 'danger', detail: e2?.message });
      }
    };
    const ok = showChapterDiff(view, localContent, serverMd, {
      onAccept: () => { applyTab(serverMd, serverTs); toast(t('md_editor.conflict.took_theirs'), { kind: 'ok', duration: 1600 }); },
      onReject: () => { forceSave(localContent, t('md_editor.conflict.kept_yours')); },
      onMixed: (finalText) => { forceSave(finalText, t('md_editor.conflict.mixed_saved')); },
    });
    if (ok) toast(t('md_editor.conflict.merge_prompt'), { kind: 'warn', duration: 3200 });
    return ok;
  }, [scriptId, t]);

  const saveTab = useCallback(async (key, opts) => {
    const tab = tabs.find((x) => x.key === key);
    if (!tab || !tab.dirty) return;
    setTabs((cur) => cur.map((x) => x.key === key ? { ...x, saving: true } : x));
    try {
      const newTs = await saveNodeContent(tab.kind, scriptId, tab.id, tab.content, tab.original, tab.baseUpdatedAt);
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, original: tab.content, dirty: false, saving: false, conflict: false, baseUpdatedAt: newTs || x.baseUpdatedAt } : x));
      toast(t('md_editor.toast.saved'), { kind: 'ok', duration: 1200 });
    } catch (e) {
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, saving: false } : x));
      if (e && e.status === 409 && e.payload && e.payload.conflict) {
        const server = e.payload.server_chapter || {};
        const serverMd = toMd('chapter', server);
        const serverTs = server.updated_at || null;
        if (opts && opts.auto) {
          setTabs((cur) => cur.map((x) => x.key === key ? { ...x, conflict: true } : x));
          toast(t('md_editor.conflict.auto_deferred'), { kind: 'warn', duration: 3600 });
          return;
        }
        if (openConflictMerge(key, tab.content, serverMd, serverTs)) return;
        setTabs((cur) => cur.map((x) => x.key === key ? { ...x, conflict: true } : x));
        toast(t('md_editor.conflict.other_tab'), { kind: 'warn', duration: 3600 });
        return;
      }
      toast(t('md_editor.toast.save_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [tabs, scriptId, t, openConflictMerge]);

  // agent 写库后:重载受影响的标签(若打开且无未保存改动)+ 刷新文件树。
  const refreshTab = useCallback(async (kind, id) => {
    setTreeReloadKey((x) => x + 1);
    const key = nodeKey(kind, id);
    const tab = tabs.find((x) => x.key === key);
    if (!tab) return;
    try {
      const meta = await loadNodeContentMeta(kind, scriptId, id);
      if (!tab.dirty) {
        setTabs((cur) => cur.map((x) => x.key === key ? { ...x, content: meta.content, original: meta.content, baseUpdatedAt: meta.updatedAt, dirty: false, conflict: false } : x));
        toast(t('md_editor.toast.ai_refreshed'), { kind: 'ok', duration: 1400 });
        return;
      }
      // P0:有未保存改动时不再只 toast(旧行为=用户随后 ⌘S 用旧底稿覆盖 AI 改动,静默丢数据)。
      // 激活章节→直接进三方合并;其他情况标记冲突,乐观锁在保存时兜底。
      if (kind === 'chapter' && openConflictMerge(key, tab.content, meta.content, meta.updatedAt)) return;
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, conflict: true } : x));
      toast(t('md_editor.toast.ai_edited_has_unsaved'), { kind: 'warn', duration: 3200 });
    } catch (_) { /* 静默 */ }
  }, [tabs, scriptId, t, openConflictMerge]);

  // 资源管理器增删改后:同步已打开的标签(删→关、改名→更新标题),并触发树重载。
  const onTreeMutate = useCallback((action, kind, id, label) => {
    const key = nodeKey(kind, id);
    if (action === 'delete') {
      setTabs((cur) => {
        const idx = cur.findIndex((t) => t.key === key);
        const next = cur.filter((t) => t.key !== key);
        if (activeKey === key) setActiveKey(next[Math.max(0, idx - 1)]?.key || null);
        return next;
      });
    } else if (action === 'rename' && label) {
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, label } : t));
    }
  }, [activeKey]);

  // 接受一段续写/改写后的桥接:够长就提示「要不要让助手把新设定同步进知识库」。
  // (续写引擎只产纯文本不落库,知识同步只能由右栏 agent 触发 —— 这条桥接把两路打通。)
  const onProseAccepted = useCallback((text, info) => {
    const tx = (text || '').trim();
    if (tx.length < 12) return;   // 太短(单词级)不打扰
    setSyncNudge({ text: tx, label: activeRef.current?.label || t('md_editor.sync.prose_label'), rewrite: !!(info && info.rewrite) });
  }, [t]);

  // 侧栏「续写到正文」:对当前打开的章节正文,在光标处(或选中段)用 AI 续写/改写。
  const onContinue = useCallback((instruction) => {
    const view = activeViewRef.current;
    if (!view) { toast(t('md_editor.toast.open_file_first_continue'), { kind: 'warn' }); return; }
    const _a = activeRef.current;
    const _ci = (_a && _a.kind === 'chapter') ? _a.id : null;   // 章号→后端装配相关设定+防剧透
    runContinue(view, { scriptId, instruction, onAccept: onProseAccepted, chapterIndex: _ci });
  }, [scriptId, onProseAccepted]);

  // agent 提议改某章 → 在中间编辑器内联 diff(绿增/红删)+ 顶栏「全部批准/拒绝」。
  // 仅当该章正是当前激活的 tab 时拦截到编辑器;否则返回 false → 侧栏走原确认。cbs.onAccept/onReject
  // 在用户点批准/拒绝时回调(触发对 agent 的 /confirm approve|reject)。任何异常都回 false 退回侧栏。
  const proposeChapterDiff = useCallback((chapterIndex, newText, cbs) => {
    try {
      const view = activeViewRef.current;
      const a = activeRef.current;
      if (!view || !a || a.kind !== 'chapter' || String(a.id) !== String(chapterIndex)) return false;
      if (newText == null) return false;
      const oldText = view.state.doc.toString();
      // 逐块取舍(混合):后端 approve 会写【整段 newText】→ 不能用 approve。改为 reject 掉 agent 提议,
      // 再把编辑器里玩家逐块选定的实际文本经 owner 校验端点直接落库(云端隔离)。
      const onMixed = async (finalText) => {
        try { cbs.onReject?.(); } catch (_) {}
        try {
          await saveNodeContent('chapter', scriptId, chapterIndex, finalText, oldText);
          const key = nodeKey('chapter', chapterIndex);
          setTabs((cur) => cur.map((x) => x.key === key ? { ...x, content: finalText, original: finalText, dirty: false } : x));
          setTreeReloadKey((x) => x + 1);
          toast(t('md_editor.diff.mixed_saved', { defaultValue: '已按你的逐段取舍保存' }), { kind: 'ok', duration: 1600 });
        } catch (e) {
          toast(t('md_editor.toast.save_failed'), { kind: 'danger', detail: e?.message });
        }
      };
      return showChapterDiff(view, oldText, newText, { ...cbs, onMixed });
    } catch (_) { return false; }
  }, [scriptId, t]);

  // 内联续写(Copilot ghost):据光标前文向后端要一句短续写。owner-scoped + 用户自有模型/key(后端隔离)。
  const ghostFetch = useCallback(async (before) => {
    try {
      const _a = activeRef.current;
      const ci = (_a && _a.kind === 'chapter') ? _a.id : null;
      const r = await api().scripts.autocomplete(scriptId, { before, chapter_index: ci });
      return (r && r.ok && r.text) ? String(r.text) : '';
    } catch (_) { return ''; }
  }, [scriptId]);
  const toggleAutoComplete = useCallback(() => {
    setAutoComplete((on) => { const next = !on; lsSet('mde.autocomplete', next ? '1' : '0'); return next; });
  }, []);
  // AI 复审本章(对标 Copilot /review):让右栏 agent 通读本章 + 汇总问题到「问题」面板(沿用 Batch 6 管线)。
  const reviewActiveChapter = useCallback(() => {
    const a = activeRef.current;
    if (!a || a.kind !== 'chapter') return;
    setRightOpen(true); lsSet('mde.rightOpen', '1');
    const ok = agentRef.current?.reviewChapter?.(a.id, a.label);
    if (ok === false) { toast(t('md_editor.review.busy', { defaultValue: 'AI 正忙,请稍候再试' }), { kind: 'warning' }); return; }
    toast(t('md_editor.review.started', { defaultValue: '正在复审本章,问题会汇总到「问题」面板' }), { kind: 'ok', duration: 2400 });
  }, [t]);

  // 「同步设定」:把刚接受的正文丢给右栏 agent,按 rule 4 读现状 + 同步知识资产。
  const doSync = useCallback(() => {
    const n = syncNudge;
    if (!n) return;
    setSyncNudge(null);
    try { agentRef.current?.syncFromProse(n.text, n.label, n.rewrite); } catch (_) { /* 静默 */ }
  }, [syncNudge]);

  // 全局快捷键(OS 自适应:Mac=⌘ / 其它=Ctrl):保存 / 折叠左树 / 快速打开 / 替换。
  useEffect(() => {
    const onKey = (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      const k = (e.key || '').toLowerCase();
      if (k === 's') { e.preventDefault(); if (activeKey) saveTab(activeKey); }
      else if (k === 'b' && !e.shiftKey && !e.altKey) { e.preventDefault(); setLeftHidden((v) => !v); }
      else if (k === 'p' && !e.shiftKey && !e.altKey) { e.preventDefault(); setQuickOpen(true); }
      else if (k === 'f' && e.shiftKey && !e.altKey) { e.preventDefault(); setSearchOpen(true); }
      // 替换:Mac=⌘⌥F(系统占用 ⌘H);Win/Linux=Ctrl+H。两者都打开 CM 搜索面板(含替换)。
      else if ((IS_MAC && e.altKey && k === 'f') || (!IS_MAC && k === 'h')) {
        e.preventDefault();
        const v = activeViewRef.current; if (v) { v.focus(); try { openSearchPanel(v); } catch (_) {} }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeKey, saveTab]);


  const active = tabs.find((t) => t.key === activeKey) || null;
  activeRef.current = active;

  // 自动保存:正文 dirty 后 2.5s 空闲存盘;内联 diff 审阅中【绝不】自动保存(否则把未批准的 diff
  // 文本落库)。云端多用户安全:saveTab 走 owner 校验的 updateChapter 端点。
  useEffect(() => {
    if (!active || !active.dirty) return undefined;
    if (active.conflict) return undefined;   // 冲突已标记:等用户 ⌘S 进合并,不再重试(否则每 2.5s 409+toast 轰炸)
    const key = active.key;
    const id = setTimeout(() => {
      const v = activeViewRef.current;
      if (v && hasChapterDiff(v)) return;   // diff 审阅中,跳过
      saveTab(key, { auto: true });
    }, 2500);
    return () => clearTimeout(id);
  }, [active && active.key, active && active.content, active && active.dirty, active && active.conflict, saveTab]);

  return (
    <div className="mde-root">
      {/* 顶栏:工作区 + 编辑图标 + 文件/编辑菜单 */}
      <div className="mde-topbar">
        {/* 工作区切换(剧本) */}
        <div className="mde-menuwrap">
          <button className="mde-ws" onClick={() => setMenu(menu === 'ws' ? null : 'ws')} title={t('md_editor.ws.switch_title')}>
            <span className="mde-ws-kicker">{t('md_editor.ws.label')}</span>
            <span className="mde-ws-name">{(scripts || []).find((s) => s.id === scriptId)?.title || (scripts === null ? t('common.loading') : t('md_editor.ws.none_selected'))}</span>
            <span className="mde-ws-caret">▾</span>
          </button>
          {menu === 'ws' && (
            <div className="mde-menu mde-ws-menu">
              {scripts === null && <div className="mde-menu-hint">{t('md_editor.ws.loading')}</div>}
              {scripts && scripts.length === 0 && <div className="mde-menu-hint">{t('md_editor.ws.no_scripts')}</div>}
              {(scripts || []).map((s) => (
                <button key={s.id} className={'mde-menu-item' + (s.id === scriptId ? ' on' : '')} onClick={() => pickScript(s.id)}>{s.title || t('md_editor.ws.script_fallback', { id: s.id })}</button>
              ))}
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" onClick={() => { setMenu(null); createBlankScript(); }}>＋ {t('md_editor.menu.new_blank_script')}</button>
            </div>
          )}
        </div>

        {/* 编辑操作图标组 */}
        <div className="mde-tb-icons">
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.undo')} title={t('md_editor.toolbar.undo')} onClick={doUndo}><TbIcon name="undo" /></button>
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.redo')} title={t('md_editor.toolbar.redo')} onClick={doRedo}><TbIcon name="redo" /></button>
          <span className="mde-tb-divider" />
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.copy')} title={t('md_editor.toolbar.copy')} onClick={doCopy}><TbIcon name="copy" /></button>
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.cut')} title={t('md_editor.toolbar.cut')} onClick={doCut}><TbIcon name="cut" /></button>
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.paste')} title={t('md_editor.toolbar.paste')} onClick={doPaste}><TbIcon name="paste" /></button>
        </div>

        {/* 文件菜单 */}
        <div className="mde-menuwrap">
          <button className={'mde-menubtn' + (menu === 'file' ? ' on' : '')} onClick={() => setMenu(menu === 'file' ? null : 'file')}>{t('md_editor.menu.file')}</button>
          {menu === 'file' && (
            <div className="mde-menu">
              <button className="mde-menu-item" disabled={!scriptId} onClick={() => { setMenu(null); addChapter(); }}>{t('md_editor.menu.new_chapter')}</button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); createBlankScript(); }}>{t('md_editor.menu.new_blank_script')}</button>
              <button className="mde-menu-item" disabled={!scriptId} onClick={renameScript}>{t('md_editor.menu.rename_script')}</button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item danger" disabled={!scriptId} onClick={deleteScript}>{t('md_editor.menu.delete_script')}</button>
            </div>
          )}
        </div>

        {/* 编辑菜单 */}
        <div className="mde-menuwrap">
          <button className={'mde-menubtn' + (menu === 'edit' ? ' on' : '')} onClick={() => setMenu(menu === 'edit' ? null : 'edit')}>{t('md_editor.menu.edit')}</button>
          {menu === 'edit' && (
            <div className="mde-menu">
              <button className="mde-menu-item" onClick={() => { setMenu(null); doUndo(); }}>{t('md_editor.menu.undo')}<span className="mde-menu-kbd">⌘Z</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doRedo(); }}>{t('md_editor.menu.redo')}<span className="mde-menu-kbd">⌘⇧Z</span></button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" onClick={() => { setMenu(null); doCopy(); }}>{t('md_editor.menu.copy')}<span className="mde-menu-kbd">⌘C</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doCut(); }}>{t('md_editor.menu.cut')}<span className="mde-menu-kbd">⌘X</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doPaste(); }}>{t('md_editor.menu.paste')}<span className="mde-menu-kbd">⌘V</span></button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" onClick={() => { setMenu(null); doFind(); }}>{t('md_editor.menu.find')}<span className="mde-menu-kbd">⌘F</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doSelectAll(); }}>{t('md_editor.menu.select_all')}<span className="mde-menu-kbd">⌘A</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doGotoLine(); }}>{t('md_editor.menu.goto_line')}</button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" disabled={!active || !active.dirty} onClick={() => { setMenu(null); if (active) saveTab(active.key); }}>{t('common.save')}<span className="mde-menu-kbd">⌘S</span></button>
            </div>
          )}
        </div>

        <div className="mde-tb-spacer" />
        {scriptId && active && active.kind === 'chapter' && <button className={'mde-tb-txt' + (autoComplete ? ' on' : '')} data-tip={t('md_editor.ghost.tip', { defaultValue: '内联续写:打字停顿后给灰色续写建议,Tab 采纳(用你自己的模型)' })} title={t('md_editor.ghost.btn', { defaultValue: 'AI 续写' })} onClick={toggleAutoComplete}>{t('md_editor.ghost.btn', { defaultValue: 'AI 续写' })}{autoComplete ? ' ·开' : ''}</button>}
        {scriptId && <button className={'mde-tb-txt' + (issueCount > 0 ? ' has-badge' : '')} data-tip={t('md_editor.problems.tip', { defaultValue: 'AI 审稿发现的问题(可跳转章节)' })} title={t('md_editor.problems.btn', { defaultValue: '问题' })} onClick={() => setIssuesOpen(true)}>{t('md_editor.problems.btn', { defaultValue: '问题' })}{issueCount > 0 ? <span className="mde-tb-badge">{issueCount}</span> : null}</button>}
        {scriptId && <button className="mde-tb-txt" data-tip={t('md_editor.rules.tip', { defaultValue: '给 AI 立写作规范(注入上下文)' })} title={t('md_editor.rules.btn', { defaultValue: '写作规范' })} onClick={() => setRulesOpen(true)}>{t('md_editor.rules.btn', { defaultValue: '写作规范' })}</button>}
        {scriptId && <button className={'mde-tb-txt' + (kbBadge && !kbBadge.ready ? ' has-badge' : '')} data-tip={t('md_editor.kb.tip', { defaultValue: '本剧本知识库健康度与重建(摘要精炼/世界书充实/复核卡/嵌入)' })} title={t('md_editor.kb.title', { defaultValue: '知识库中心' })} onClick={() => setKbOpen(true)}>{t('md_editor.kb.title', { defaultValue: '知识库中心' })}{kbBadge && !kbBadge.ready ? <span className="mde-tb-badge">{kbBadge.stale_count}</span> : null}</button>}
        {active && active.dirty && <button className="mde-save" onClick={() => saveTab(active.key)} disabled={active.saving}>{active.saving ? t('md_editor.save_btn.saving') : t('md_editor.save_btn.save')}</button>}
        <button className={'mde-tb-ic' + (rightOpen ? ' on' : '')} data-tip={rightOpen ? t('md_editor.panel.hide_ai') : t('md_editor.panel.show_ai')} title={rightOpen ? t('md_editor.panel.hide_ai') : t('md_editor.panel.show_ai')} onClick={toggleRight}><TbIcon name="panelRight" /></button>
      </div>

      {/* 窄屏右栏浮层的背景遮罩:点击/触摸关闭(CSS 只在 <1100px 显示;宽屏右栏是网格列不显示)。 */}
      {rightOpen && <div className="mde-scrim" onClick={toggleRight} aria-hidden="true" />}
      <div className={'mde-panes' + (rightOpen ? '' : ' right-collapsed') + (leftHidden ? ' left-collapsed' : '')} ref={panesRef} style={{ '--mde-left-w': leftW + 'px', '--mde-right-w': rightW + 'px' }}>
        {/* 左:文件树 */}
        <aside className="mde-left">
          {scriptId ? <FileTree scriptId={scriptId} openNode={openNode} activeKey={activeKey} reloadKey={treeReloadKey} onMutate={onTreeMutate} /> : <div className="mde-tree-hint">{t('md_editor.ws.select_first')}</div>}
        </aside>

        <div className="mde-splitter mde-splitter-left" onPointerDown={onSplitDown('left')} title={t('md_editor.splitter.left')} />

        {/* 中:标签 + 编辑器 */}
        <main className="mde-center">
          <div className="mde-tabs">
            {tabs.map((tb) => (
              <div key={tb.key} className={'mde-tab' + (tb.key === activeKey ? ' active' : '')} onClick={() => setActiveKey(tb.key)}
                title={tb.label}
                onContextMenu={(e) => { e.preventDefault(); setTabCtx({ x: e.clientX, y: e.clientY, key: tb.key }); }}
                onMouseDown={(e) => { if (e.button === 1) { e.preventDefault(); closeTab(tb.key); } /* 中键关闭(VSCode) */ }}>
                <span className={'mde-tab-label' + (tb.conflict ? ' mde-tab-conflict' : '')} title={tb.conflict ? t('md_editor.conflict.tab_tip') : undefined}>{tb.conflict ? '! ' : (tb.dirty ? '● ' : '')}{tb.label}</span>
                <span className="mde-tab-close" title={t('common.close')} onClick={(e) => { e.stopPropagation(); closeTab(tb.key); }}>×</span>
              </div>
            ))}
          </div>
          <div className="mde-editorwrap" onContextMenu={(e) => { if (!active) return; e.preventDefault(); setEditorCtx({ x: e.clientX, y: e.clientY }); }}>
            <EditorPane tab={active} onChange={onEdit} scriptId={scriptId} onViewReady={(v) => { activeViewRef.current = v; }} onContinueAccept={onProseAccepted} chapterIndex={active && active.kind === 'chapter' ? active.id : null} onSelectionChange={onSel} ghostEnabled={autoComplete} ghostFetch={ghostFetch} />
          </div>
          {/* 底部状态栏(VSCode 风):字数 + 光标行:列 + 选中 + 保存态 */}
          <div className="mde-statusbar">
            {active ? (
              <>
                <span className="mde-sb-item">{t('md_editor.statusbar.words', { n: (cursor.total || (active.content || '').length).toLocaleString(), defaultValue: '{{n}} 字' })}</span>
                <span className="mde-sb-item">{t('md_editor.statusbar.lncol', { line: cursor.line, col: cursor.col, defaultValue: '行 {{line}}, 列 {{col}}' })}</span>
                {selLen > 0 && <span className="mde-sb-item">{t('md_editor.statusbar.selected', { n: selLen, defaultValue: '选中 {{n}}' })}</span>}
                <span className="mde-sb-spacer" />
                {active.kind === 'chapter' && (
                  <button type="button" className="mde-sb-btn" onClick={reviewActiveChapter}>{t('md_editor.review.btn', { defaultValue: 'AI 复审本章' })}</button>
                )}
                {active.kind === 'chapter' && (
                  <button type="button" className="mde-sb-btn" onClick={() => setHistoryFor(active.id)}>{t('md_editor.history.btn', { defaultValue: '版本历史' })}</button>
                )}
                {active.kind === 'chapter' && (
                  <button type="button" className="mde-sb-btn" disabled={playtestBusy} onClick={() => playtest(active.id, active.label)}>{playtestBusy ? t('md_editor.playtest.busy', { defaultValue: '试玩中…' }) : t('md_editor.playtest.btn', { defaultValue: '从本章试玩' })}</button>
                )}
                <span className={'mde-sb-item' + (active.dirty ? ' dirty' : '')}>{active.dirty ? t('md_editor.statusbar.unsaved', { defaultValue: '未保存' }) : t('md_editor.statusbar.saved', { defaultValue: '已保存' })}</span>
              </>
            ) : <span className="mde-sb-item muted">{t('md_editor.statusbar.no_file', { defaultValue: '未打开文件' })}</span>}
          </div>
          {tabCtx && (() => {
            const idx = tabs.findIndex((t) => t.key === tabCtx.key);
            const others = tabs.filter((t) => t.key !== tabCtx.key).map((t) => t.key);
            const toRight = tabs.slice(idx + 1).map((t) => t.key);
            const saved = tabs.filter((t) => !t.dirty).map((t) => t.key);
            const items = [
              { label: t('common.close'), kbd: '⌘W', onClick: () => closeTab(tabCtx.key) },
              { label: t('md_editor.tab_ctx.close_others'), disabled: others.length === 0, onClick: () => { setActiveKey(tabCtx.key); closeTabs(others); } },
              { label: t('md_editor.tab_ctx.close_to_right'), disabled: toRight.length === 0, onClick: () => closeTabs(toRight) },
              { sep: true },
              { label: t('md_editor.tab_ctx.close_saved'), disabled: saved.length === 0, onClick: () => closeTabs(saved) },
              { label: t('md_editor.tab_ctx.close_all'), disabled: tabs.length === 0, onClick: () => closeTabs(tabs.map((tb) => tb.key)) },
            ];
            return <ContextMenu x={tabCtx.x} y={tabCtx.y} items={items} onClose={() => setTabCtx(null)} />;
          })()}
          {editorCtx && (
            <ContextMenu x={editorCtx.x} y={editorCtx.y} onClose={() => setEditorCtx(null)} items={[
              { label: t('md_editor.menu.cut'), kbd: '⌘X', onClick: doCut },
              { label: t('md_editor.menu.copy'), kbd: '⌘C', onClick: doCopy },
              { label: t('md_editor.menu.paste'), kbd: '⌘V', onClick: doPaste },
              { sep: true },
              { label: t('md_editor.menu.select_all'), kbd: '⌘A', onClick: doSelectAll },
              { sep: true },
              { label: t('md_editor.menu.undo'), kbd: '⌘Z', onClick: doUndo },
              { label: t('md_editor.menu.redo'), kbd: '⌘⇧Z', onClick: doRedo },
            ]} />
          )}
          {syncNudge && (
            <div className="mde-syncbar">
              <span className="mde-syncbar-text">
                {syncNudge.rewrite ? t('md_editor.sync.nudge_rewrite') : t('md_editor.sync.nudge_continue')}
              </span>
              <button className="mde-syncbar-go" onClick={doSync}>{t('md_editor.sync.sync_btn')}</button>
              <button className="mde-syncbar-no" onClick={() => setSyncNudge(null)}>{t('md_editor.sync.ignore_btn')}</button>
            </div>
          )}
        </main>

        <div className="mde-splitter mde-splitter-right" onPointerDown={onSplitDown('right')} title={t('md_editor.splitter.right')} />

        {/* 右:agent 直写面板(console_assistant SSE)+ 续写到正文 */}
        <aside className="mde-right">
          {scriptId
            ? <MdEditorAgent ref={agentRef} scriptId={scriptId} activeTab={active} onWriteComplete={refreshTab} onContinue={onContinue} onProposeChapterEdit={proposeChapterDiff} selLen={selLen} getSelectionContext={getSelectionContext} onIssuesReported={() => setIssuesReloadKey((x) => x + 1)} />
            : <div className="mde-tree-hint">{t('md_editor.ws.select_first')}</div>}
        </aside>
      </div>
      {quickOpen && scriptId && <QuickOpen scriptId={scriptId} openNode={openNode} onClose={() => setQuickOpen(false)} />}
      {searchOpen && scriptId && <GlobalSearch scriptId={scriptId} openNode={openNode} onClose={() => setSearchOpen(false)} />}
      {historyFor != null && scriptId && <ChapterHistory scriptId={scriptId} chapterIndex={historyFor} onClose={() => setHistoryFor(null)} onRestored={() => { try { refreshTab('chapter', historyFor); } catch (_) {} setHistoryFor(null); }} />}
      {rulesOpen && scriptId && <WritingRules scriptId={scriptId} onClose={() => setRulesOpen(false)} />}
      {kbOpen && scriptId && <EditorKbPanel scriptId={scriptId} open={kbOpen} onClose={() => setKbOpen(false)} />}
      {issuesOpen && scriptId && <ProblemsPanel scriptId={scriptId} reloadKey={issuesReloadKey} onCountChange={setIssueCount}
        onJump={(ch) => { if (ch != null) openNode({ kind: 'chapter', id: ch, label: t('md_editor.chapter_prefix', { index: ch }) }); setIssuesOpen(false); }}
        onClose={() => setIssuesOpen(false)} />}
    </div>
  );
}

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
