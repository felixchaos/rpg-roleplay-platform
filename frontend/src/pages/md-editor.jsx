// md-editor.jsx — VSCode 风 Markdown 编辑器(剧本知识资产内联编辑 + agent 直写)。
// 设计:docs/design/N_md_editor.md。三栏:左文件树 / 中多标签 CodeMirror / 右 agent。
// 本文件是页面壳 + 状态编排;CodeMirror 包在 components/CodeMirrorEditor.jsx(P3),
// 序列化在 lib/md-serialize.js(P2),agent 面板在 components/MdEditorAgent.jsx(P5)。
import React from 'react';
import './md-editor.css';
import { lsGet, lsSet, lsGetJSON } from '../lib/storage.js';
import CodeMirrorEditor from '../components/CodeMirrorEditor.jsx';
import MdEditorAgent from '../components/MdEditorAgent.jsx';
import { toMd, fromMd, splitFrontMatter } from '../lib/md-serialize.js';
import { runContinue } from '../lib/md-continue.js';
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

// 文件树节点类型 → 中文标签 + 排序。
const NODE_GROUPS = [
  { kind: 'chapter',   label: '章节正文', icon: '§' },
  { kind: 'card',      label: '角色卡',   icon: '@' },
  { kind: 'worldbook', label: '世界书',   icon: '#' },
  { kind: 'anchor',    label: '时间线',   icon: '~' },
  { kind: 'canon',     label: '知识库人物', icon: '*' },
];

const api = () => (typeof window !== 'undefined' ? window.api : null);
const toast = (msg, opts) => { try { window.__apiToast?.(msg, opts); } catch (_) {} };
// 章节标题存「裸标题」(不含「第N章」),显示时由前端加序号前缀。剥掉任何已混入的前缀,防重命名/重建出现「第5章 第5章 …」双序号。
const stripChapterPrefix = (s) => String(s || '').replace(/^\s*第\s*[0-9一二三四五六七八九十百千零〇两]+\s*章\s*/, '');
// Canon 实体类型 → 中文(与剧本管理「知识库人物」标签同步:i18n scripts.edit.canon.type_*)。含常见同义词回退。
const CANON_TYPE_ZH = { character: '人物', person: '人物', faction: '势力', organization: '势力', org: '势力', location: '地点', place: '地点', item: '物品', concept: '概念', event: '事件' };
const canonTypeZh = (tp) => CANON_TYPE_ZH[String(tp || '').toLowerCase()] || (tp || '概念');

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
  if (kind === 'chapter')   { const r = await A.scripts.addChapter(sid, nm); return { id: r.chapter_index, label: `第${r.chapter_index}章 ${r.title || ''}`.trim() }; }
  if (kind === 'worldbook') { const r = await A.scripts.worldbookCreate(sid, { title: nm || '新条目', content: '' }); const e = r?.entry || r; return { id: e.id, label: e.title || nm || '新条目' }; }
  if (kind === 'card')      { const r = await A.scripts.cardUpsert(sid, { name: nm || '新角色' }); const c = r?.card || r; return { id: c.id, label: c.name || nm || '新角色' }; }
  if (kind === 'canon')     { const r = await A.scripts.canonUpsert(sid, { name: nm || '新实体', type: 'concept' }); const e = r?.entity || r; return { id: e.logical_key, label: `${e.name || nm || '新实体'}（${canonTypeZh(e.type || 'concept')}）` }; }
  if (kind === 'anchor')    { const r = await A.scripts.anchorCreate(sid, { story_time_label: nm || '新时点', chapter_min: 1, chapter_max: 1 }); const a = r?.anchor || r; return { id: a.id, label: nm || '新时点' }; }
  throw new Error('不支持新建');
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
  throw new Error('该类型不支持删除');
}

// ── 文件树:VSCode 风资源管理器(多组展开 / 搜索 / 图标 / 工具栏 / 键盘 / 右键 / 增删改 / 拖拽)──
function FileTree({ scriptId, openNode, activeKey, reloadKey, onMutate }) {
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
        toast('已新建', { kind: 'ok', duration: 1100 });
      } else {
        await renameNode(e.kind, scriptId, e.id, nm);
        await loadGroup(e.kind);
        const disp = e.kind === 'chapter' ? `第${e.id}章 ${nm}`.trim() : nm;
        onMutate?.('rename', e.kind, e.id, disp);
        toast('已重命名', { kind: 'ok', duration: 1100 });
      }
    } catch (err) { toast('操作失败', { kind: 'danger', detail: err?.message }); }
    finally { setBusy(false); setEditing(null); submittingRef.current = false; }
  };

  const doDelete = async (kind, it) => {
    setCtx(null);
    if (!CAN_DELETE[kind]) { toast('该类型不支持删除', { kind: 'warning' }); return; }
    const extra = kind === 'chapter' ? '\n删除后本剧本会重新连续编号(与合并/拆分一致);RAG 索引需重新提取才能完全对齐。' : '';
    const ok = await (window.__confirm
      ? window.__confirm({ title: '删除该条目?', message: `${it.label}\n此操作不可恢复。${extra}`, danger: true, confirmText: '删除' })
      : Promise.resolve(confirm(`删除「${it.label}」?`)));
    if (!ok) return;
    setBusy(true);
    try {
      await deleteNode(kind, scriptId, it.id);
      await loadGroup(kind);
      onMutate?.('delete', kind, it.id);
      setSelSet(new Set());
      toast('已删除', { kind: 'ok', duration: 1100 });
    } catch (err) { toast('删除失败', { kind: 'danger', detail: err?.message }); }
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
    if (!total) { toast('所选项没有可删除的', { kind: 'warning' }); return; }
    const hasChapter = (byKind.chapter || []).length > 0;
    const extra = hasChapter ? '\n含章节:删除后会重新连续编号,RAG 索引需重新提取对齐。' : '';
    const ok = await (window.__confirm
      ? window.__confirm({ title: `删除选中的 ${total} 项?`, message: `此操作不可恢复。${extra}`, danger: true, confirmText: '删除' })
      : Promise.resolve(confirm(`删除选中的 ${total} 项?`)));
    if (!ok) return;
    setBusy(true);
    let failed = 0;
    const A = api();
    try {
      if ((byKind.chapter || []).length) {
        try { await A.scripts.deleteChapters(scriptId, byKind.chapter.map((it) => it.id)); byKind.chapter.forEach((it) => onMutate?.('delete', 'chapter', it.id)); }
        catch (e) { failed += byKind.chapter.length; toast('章节批量删除失败', { kind: 'danger', detail: e?.message }); }
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
    toast(failed ? `删除完成,${failed} 项失败` : `已删除 ${total} 项`, { kind: failed ? 'warn' : 'ok', duration: 1500 });
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
    if (!CAN_RENAME[kind] || kind === 'chapter') { toast('该类型不支持复制', { kind: 'warning' }); return; }
    setBusy(true);
    try {
      const created = await createNode(kind, scriptId, `${it.label} 副本`);
      await loadGroup(kind); onMutate?.('create', kind, created.id, created.label);
      toast('已复制', { kind: 'ok', duration: 1100 });
    } catch (err) { toast('复制失败', { kind: 'danger', detail: err?.message }); }
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
      toast('已重排序', { kind: 'ok', duration: 1000 });
    } catch (err) { toast('重排失败', { kind: 'danger', detail: err?.message }); }
    finally { setBusy(false); }
  };

  return (
    <div className="mde-tree" tabIndex={0} ref={bodyRef} onKeyDown={onKeyDown} onClick={() => ctx && setCtx(null)}>
      <div className="mde-tree-toolbar">
        <input className="mde-tree-filter" value={filter} placeholder="搜索全部资源…" onChange={(e) => setFilter(e.target.value)} />
        <NewMenu onPick={startNew} />
        <button className="mde-tree-tbbtn" title="折叠全部" onClick={collapseAll}>⊟</button>
        <button className="mde-tree-tbbtn" title="刷新" onClick={() => [...expanded].forEach(loadGroup)}>⟳</button>
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
                  <span className="mde-tree-glabel">{g.label}</span>
                  {st.items && <span className="mde-tree-count">{q ? items.length : st.items.length}</span>}
                </button>
                {CAN_CREATE_KIND(g.kind) && <button className="mde-tree-additem" title={`新建${g.label}`} onClick={(e) => { e.stopPropagation(); startNew(g.kind); }}>＋</button>}
              </div>
              {open && (
                <div className="mde-tree-children">
                  {st.loading && <div className="mde-tree-hint">加载中…</div>}
                  {st.error && <div className="mde-tree-hint err">加载失败:{st.error}</div>}
                  {editing && editing.kind === g.kind && editing.id === '__new__' && (
                    <input className="mde-tree-edit" autoFocus value={editing.value}
                      placeholder={`新${g.label}名称`} disabled={busy}
                      onChange={(e) => setEditing((s) => ({ ...s, value: e.target.value }))}
                      onKeyDown={(e) => { if (e.key === 'Enter') commitEdit(); if (e.key === 'Escape') setEditing(null); }}
                      onBlur={commitEdit} />
                  )}
                  {!st.loading && !st.error && items.length === 0 && !(editing && editing.id === '__new__' && editing.kind === g.kind) && <div className="mde-tree-hint">（空）</div>}
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
        const gLabel = NODE_GROUPS.find((g) => g.kind === ctx.kind)?.label;
        const items = multi ? [
          { label: `打开 ${selSet.size} 项`, onClick: () => { for (const g of NODE_GROUPS) for (const it of groupItems(g.kind)) { if (selSet.has(nodeKey(g.kind, it.id))) openNode({ kind: g.kind, id: it.id, label: it.label, meta: it }); } } },
          { sep: true },
          { label: `删除选中的 ${selSet.size} 项`, kbd: 'Del', danger: true, onClick: () => doDeleteSelected(selSet) },
        ] : ctx.item ? [
          { label: '打开', onClick: () => openNode({ kind: ctx.kind, id: ctx.item.id, label: ctx.item.label, meta: ctx.item }) },
          CAN_RENAME[ctx.kind] && { label: '重命名', kbd: 'F2', onClick: () => startRename(ctx.kind, ctx.item) },
          (CAN_RENAME[ctx.kind] && ctx.kind !== 'chapter') && { label: '复制', onClick: () => duplicate(ctx.kind, ctx.item) },
          CAN_CREATE_KIND(ctx.kind) && { sep: true },
          CAN_CREATE_KIND(ctx.kind) && { label: `新建${gLabel}`, onClick: () => startNew(ctx.kind) },
          { sep: true },
          { label: '删除', kbd: 'Del', danger: true, disabled: !CAN_DELETE[ctx.kind], onClick: () => doDelete(ctx.kind, ctx.item) },
        ] : [
          CAN_CREATE_KIND(ctx.kind) && { label: `新建${gLabel}`, onClick: () => startNew(ctx.kind) },
        ];
        return <ContextMenu x={ctx.x} y={ctx.y} items={items} onClose={() => setCtx(null)} />;
      })()}
    </div>
  );
}

const CAN_CREATE_KIND = () => true; // 5 类都支持新建
function NewMenu({ onPick }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mde-newmenu">
      <button className="mde-tree-tbbtn" title="新建" onClick={() => setOpen((o) => !o)}>＋</button>
      {open && (
        <div className="mde-newmenu-pop" onMouseLeave={() => setOpen(false)}>
          {NODE_GROUPS.map((g) => (
            <button key={g.kind} onClick={() => { setOpen(false); onPick(g.kind); }}>
              <span className="mde-tree-gicon">{g.icon}</span> {g.label}
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
    return arr.map((c) => ({ id: c.chapter_index, title: stripChapterPrefix(c.title || ''), label: `第${c.chapter_index}章 ${stripChapterPrefix(c.title || '')}`.trim(), word_count: c.word_count }));
  }
  if (kind === 'card') {
    const r = await A.cards.scriptList(sid);
    const arr = Array.isArray(r) ? r : (r?.items || []);
    return arr.map((c) => ({ id: c.id, label: c.name + (c.full_name && c.full_name !== c.name ? ` (${c.full_name})` : '') }));
  }
  if (kind === 'worldbook') {
    const r = await A.scripts.worldbook(sid);
    const arr = r?.entries || r?.items || (Array.isArray(r) ? r : []);
    return arr.map((w) => ({ id: w.id, label: w.title || `(条目 ${w.id})` }));
  }
  if (kind === 'anchor') {
    const r = await A.scripts.timeline(sid);
    const phases = r?.phases || [];
    const out = [];
    for (const ph of phases) for (const a of (ph.anchors || [])) {
      out.push({ id: a.anchor_id || a.id, label: `${a.story_time_label || ph.phase_label || ''}（${a.chapter_min}-${a.chapter_max}）` });
    }
    return out;
  }
  if (kind === 'canon') {
    // canon-entities 列表端点在 P1 新增;暂经 graph 端点兜底。
    if (A.scripts.canonList) {
      const r = await A.scripts.canonList(sid);
      const arr = r?.entities || r?.items || [];
      return arr.map((e) => ({ id: e.logical_key, label: `${e.name}（${canonTypeZh(e.type)}）` }));
    }
    try {
      const r = await A.scripts.graph(sid);
      const arr = r?.entities || [];
      return arr.map((e) => ({ id: e.logical_key, label: `${e.name}（${canonTypeZh(e.type)}）` }));
    } catch (_) { return []; }
  }
  return [];
}

// ── 标签编辑器(P0:textarea;P3 替换为 CodeMirror)──────────────────────
function EditorPane({ tab, onChange, scriptId, onViewReady, onContinueAccept, chapterIndex }) {
  if (!tab) {
    return <div className="mde-empty">从左侧选择一个文件开始编辑<br /><span className="muted">章节正文 / 角色卡 / 世界书 / 时间线 / Canon</span></div>;
  }
  if (tab.loading) return <div className="mde-empty">加载中…</div>;
  if (tab.error) return <div className="mde-empty err">加载失败:{tab.error}</div>;
  return (
    <CodeMirrorEditor
      value={tab.content}
      docKey={tab.key}
      onChange={(v) => onChange(tab.key, v)}
      scriptId={scriptId}
      onViewReady={onViewReady}
      onContinueAccept={onContinueAccept}
      chapterIndex={chapterIndex}
    />
  );
}

// ── 主页面 ───────────────────────────────────────────────────────────
export default function MdEditorPage() {
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

  // 拉剧本列表(仅自己拥有的可编辑)。
  useEffect(() => {
    (async () => {
      try {
        const r = await api().scripts.list();
        const arr = r?.items || r?.scripts || (Array.isArray(r) ? r : []);
        const owned = arr.filter((s) => s.is_owner !== false && s.role !== 'subscriber');
        setScripts(owned);
        if (!scriptId && owned[0]) { setScriptId(owned[0].id); lsSet('mde.scriptId', owned[0].id); }
      } catch (e) { setScripts([]); toast('剧本列表加载失败', { kind: 'danger', detail: e?.message }); }
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
  const withView = useCallback((fn) => { const v = activeViewRef.current; if (!v) { toast('请先打开一个文件', { kind: 'warn', duration: 1400 }); return; } v.focus(); fn(v); }, []);
  const doUndo = useCallback(() => withView((v) => undo(v)), [withView]);
  const doRedo = useCallback(() => withView((v) => redo(v)), [withView]);
  const doSelectAll = useCallback(() => withView((v) => selectAll(v)), [withView]);
  const doFind = useCallback(() => withView((v) => openSearchPanel(v)), [withView]);
  const doCopy = useCallback(() => withView(async (v) => { const s = v.state.sliceDoc(v.state.selection.main.from, v.state.selection.main.to); if (!s) return; try { await navigator.clipboard.writeText(s); } catch (_) { toast('复制失败,请用 ⌘C', { kind: 'warn' }); } }), [withView]);
  const doCut = useCallback(() => withView(async (v) => { const sel = v.state.selection.main; const s = v.state.sliceDoc(sel.from, sel.to); if (!s) return; try { await navigator.clipboard.writeText(s); v.dispatch({ changes: { from: sel.from, to: sel.to } }); } catch (_) { toast('剪切失败,请用 ⌘X', { kind: 'warn' }); } }), [withView]);
  const doPaste = useCallback(() => withView(async (v) => { try { const txt = await navigator.clipboard.readText(); if (!txt) return; const sel = v.state.selection.main; v.dispatch({ changes: { from: sel.from, to: sel.to, insert: txt }, selection: { anchor: sel.from + txt.length } }); } catch (_) { toast('粘贴失败,请用 ⌘V', { kind: 'warn' }); } }), [withView]);
  const doGotoLine = useCallback(() => withView((v) => { const raw = window.prompt('转到行号:'); const n = Number(raw); if (!n || n < 1) return; const line = v.state.doc.line(Math.min(Math.floor(n), v.state.doc.lines)); v.dispatch({ selection: { anchor: line.from }, scrollIntoView: true }); }), [withView]);

  // 文件菜单:重命名 / 删除当前剧本(严格 owner,后端 403 兜底)。
  const renameScript = useCallback(async () => {
    setMenu(null);
    if (!scriptId) return;
    const cur = (scripts || []).find((s) => s.id === scriptId);
    const name = window.prompt('重命名剧本:', cur?.title || '');
    if (name == null) return;
    const t = name.trim(); if (!t) return;
    try { await api().scripts.rename(scriptId, t); setScripts((prev) => (prev || []).map((s) => s.id === scriptId ? { ...s, title: t } : s)); toast('已重命名', { kind: 'ok', duration: 1200 }); }
    catch (e) { toast('重命名失败', { kind: 'danger', detail: e?.message }); }
  }, [scriptId, scripts]);
  const deleteScript = useCallback(async () => {
    setMenu(null);
    if (!scriptId) return;
    const cur = (scripts || []).find((s) => s.id === scriptId);
    const ok = await (window.__confirm
      ? window.__confirm({ title: '删除整个剧本?', message: `「${cur?.title || scriptId}」及其下所有存档都会被永久删除,不可恢复。`, danger: true, confirmText: '删除' })
      : Promise.resolve(window.confirm('删除整个剧本?(连带其下所有存档,不可恢复)')));
    if (!ok) return;
    try {
      await api().scripts.delete(scriptId, { force: true });
      const rest = (scripts || []).filter((s) => s.id !== scriptId);
      setScripts(rest);
      if (rest[0]) pickScript(rest[0].id);
      else { setScriptId(null); lsSet('mde.scriptId', null); setTabs([]); setActiveKey(null); }
      toast('已删除剧本', { kind: 'ok', duration: 1400 });
    } catch (e) { toast('删除失败', { kind: 'danger', detail: e?.message }); }
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
      const r = await api().scripts.createBlank('新剧本');
      if (!r?.script_id) throw new Error(r?.error || '创建失败');
      setScripts((prev) => [{ id: r.script_id, title: r.title }, ...(prev || [])]);
      pickScript(r.script_id);
      toast('已新建空白剧本', { kind: 'ok', duration: 1400 });
    } catch (e) { toast('新建剧本失败', { kind: 'danger', detail: e?.message }); }
  };
  // 给当前剧本追加一章并打开。
  const addChapter = async () => {
    if (!scriptId) return;
    try {
      const r = await api().scripts.addChapter(scriptId, '');
      if (!r?.chapter_index) throw new Error(r?.error || '创建失败');
      setTreeReloadKey((x) => x + 1);
      openNode({ kind: 'chapter', id: r.chapter_index, label: `第${r.chapter_index}章 ${r.title || ''}`.trim() });
      toast('已新建章节', { kind: 'ok', duration: 1200 });
    } catch (e) { toast('新建章节失败', { kind: 'danger', detail: e?.message }); }
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
      const content = await loadNodeContent(node.kind, scriptId, node.id);
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, content, original: content, loading: false } : t));
    } catch (e) {
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, loading: false, error: e?.message || String(e) } : t));
    }
  }, [scriptId]);

  const onEdit = useCallback((key, val) => {
    setTabs((cur) => cur.map((t) => t.key === key ? { ...t, content: val, dirty: val !== t.original } : t));
  }, []);

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
    const t = tabs.find((x) => x.key === key);
    if (t?.dirty) {
      const ok = await (window.__confirm ? window.__confirm({ title: '放弃未保存的修改?', message: t.label, danger: true, confirmText: '放弃' }) : Promise.resolve(confirm('放弃未保存的修改?')));
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
        ? window.__confirm({ title: `放弃 ${dirtyCnt} 个未保存的标签?`, message: '这些标签的未保存修改会丢失。', danger: true, confirmText: '放弃并关闭' })
        : Promise.resolve(confirm(`放弃 ${dirtyCnt} 个未保存的修改?`)));
      if (!ok) return;
    }
    setTabs((cur) => {
      const next = cur.filter((t) => !set.has(t.key));
      if (set.has(activeKey)) setActiveKey(next[next.length - 1]?.key || null);
      return next;
    });
  }, [tabs, activeKey]);

  const saveTab = useCallback(async (key) => {
    const t = tabs.find((x) => x.key === key);
    if (!t || !t.dirty) return;
    setTabs((cur) => cur.map((x) => x.key === key ? { ...x, saving: true } : x));
    try {
      await saveNodeContent(t.kind, scriptId, t.id, t.content, t.original);
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, original: t.content, dirty: false, saving: false } : x));
      toast('已保存', { kind: 'ok', duration: 1200 });
    } catch (e) {
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, saving: false } : x));
      toast('保存失败', { kind: 'danger', detail: e?.message });
    }
  }, [tabs, scriptId]);

  // agent 写库后:重载受影响的标签(若打开且无未保存改动)+ 刷新文件树。
  const refreshTab = useCallback(async (kind, id) => {
    setTreeReloadKey((x) => x + 1);
    const key = nodeKey(kind, id);
    const t = tabs.find((x) => x.key === key);
    if (!t) return;
    if (t.dirty) { toast('AI 改了这条,但你有未保存修改,未自动刷新', { kind: 'warn', duration: 2600 }); return; }
    try {
      const content = await loadNodeContent(kind, scriptId, id);
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, content, original: content, dirty: false } : x));
      toast('AI 已修改,已刷新', { kind: 'ok', duration: 1400 });
    } catch (_) { /* 静默 */ }
  }, [tabs, scriptId]);

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
    const t = (text || '').trim();
    if (t.length < 12) return;   // 太短(单词级)不打扰
    setSyncNudge({ text: t, label: activeRef.current?.label || '正文', rewrite: !!(info && info.rewrite) });
  }, []);

  // 侧栏「续写到正文」:对当前打开的章节正文,在光标处(或选中段)用 AI 续写/改写。
  const onContinue = useCallback((instruction) => {
    const view = activeViewRef.current;
    if (!view) { toast('请先打开一个文件再续写', { kind: 'warn' }); return; }
    const _a = activeRef.current;
    const _ci = (_a && _a.kind === 'chapter') ? _a.id : null;   // 章号→后端装配相关设定+防剧透
    runContinue(view, { scriptId, instruction, onAccept: onProseAccepted, chapterIndex: _ci });
  }, [scriptId, onProseAccepted]);

  // 「同步设定」:把刚接受的正文丢给右栏 agent,按 rule 4 读现状 + 同步知识资产。
  const doSync = useCallback(() => {
    const n = syncNudge;
    if (!n) return;
    setSyncNudge(null);
    try { agentRef.current?.syncFromProse(n.text, n.label, n.rewrite); } catch (_) { /* 静默 */ }
  }, [syncNudge]);

  // Cmd/Ctrl+S 保存当前标签。
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault();
        if (activeKey) saveTab(activeKey);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeKey, saveTab]);

  const active = tabs.find((t) => t.key === activeKey) || null;
  activeRef.current = active;

  return (
    <div className="mde-root">
      {/* 顶栏:工作区 + 编辑图标 + 文件/编辑菜单 */}
      <div className="mde-topbar">
        {/* 工作区切换(剧本) */}
        <div className="mde-menuwrap">
          <button className="mde-ws" onClick={() => setMenu(menu === 'ws' ? null : 'ws')} title="切换工作区(剧本)">
            <span className="mde-ws-kicker">工作区</span>
            <span className="mde-ws-name">{(scripts || []).find((s) => s.id === scriptId)?.title || (scripts === null ? '加载中…' : '未选择')}</span>
            <span className="mde-ws-caret">▾</span>
          </button>
          {menu === 'ws' && (
            <div className="mde-menu mde-ws-menu">
              {scripts === null && <div className="mde-menu-hint">加载剧本…</div>}
              {scripts && scripts.length === 0 && <div className="mde-menu-hint">（无可编辑剧本）</div>}
              {(scripts || []).map((s) => (
                <button key={s.id} className={'mde-menu-item' + (s.id === scriptId ? ' on' : '')} onClick={() => pickScript(s.id)}>{s.title || `剧本 ${s.id}`}</button>
              ))}
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" onClick={() => { setMenu(null); createBlankScript(); }}>＋ 新建空白剧本</button>
            </div>
          )}
        </div>

        {/* 编辑操作图标组 */}
        <div className="mde-tb-icons">
          <button className="mde-tb-ic" data-tip="撤销 ⌘Z" title="撤销 ⌘Z" onClick={doUndo}><TbIcon name="undo" /></button>
          <button className="mde-tb-ic" data-tip="重做 ⌘⇧Z" title="重做 ⌘⇧Z" onClick={doRedo}><TbIcon name="redo" /></button>
          <span className="mde-tb-divider" />
          <button className="mde-tb-ic" data-tip="复制 ⌘C" title="复制 ⌘C" onClick={doCopy}><TbIcon name="copy" /></button>
          <button className="mde-tb-ic" data-tip="剪切 ⌘X" title="剪切 ⌘X" onClick={doCut}><TbIcon name="cut" /></button>
          <button className="mde-tb-ic" data-tip="粘贴 ⌘V" title="粘贴 ⌘V" onClick={doPaste}><TbIcon name="paste" /></button>
        </div>

        {/* 文件菜单 */}
        <div className="mde-menuwrap">
          <button className={'mde-menubtn' + (menu === 'file' ? ' on' : '')} onClick={() => setMenu(menu === 'file' ? null : 'file')}>文件</button>
          {menu === 'file' && (
            <div className="mde-menu">
              <button className="mde-menu-item" disabled={!scriptId} onClick={() => { setMenu(null); addChapter(); }}>新建章节</button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); createBlankScript(); }}>新建空白剧本</button>
              <button className="mde-menu-item" disabled={!scriptId} onClick={renameScript}>重命名剧本…</button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item danger" disabled={!scriptId} onClick={deleteScript}>删除当前剧本</button>
            </div>
          )}
        </div>

        {/* 编辑菜单 */}
        <div className="mde-menuwrap">
          <button className={'mde-menubtn' + (menu === 'edit' ? ' on' : '')} onClick={() => setMenu(menu === 'edit' ? null : 'edit')}>编辑</button>
          {menu === 'edit' && (
            <div className="mde-menu">
              <button className="mde-menu-item" onClick={() => { setMenu(null); doUndo(); }}>撤销<span className="mde-menu-kbd">⌘Z</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doRedo(); }}>重做<span className="mde-menu-kbd">⌘⇧Z</span></button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" onClick={() => { setMenu(null); doCopy(); }}>复制<span className="mde-menu-kbd">⌘C</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doCut(); }}>剪切<span className="mde-menu-kbd">⌘X</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doPaste(); }}>粘贴<span className="mde-menu-kbd">⌘V</span></button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" onClick={() => { setMenu(null); doFind(); }}>查找<span className="mde-menu-kbd">⌘F</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doSelectAll(); }}>全选<span className="mde-menu-kbd">⌘A</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doGotoLine(); }}>转到行…</button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" disabled={!active || !active.dirty} onClick={() => { setMenu(null); if (active) saveTab(active.key); }}>保存<span className="mde-menu-kbd">⌘S</span></button>
            </div>
          )}
        </div>

        <div className="mde-tb-spacer" />
        {active && active.dirty && <button className="mde-save" onClick={() => saveTab(active.key)} disabled={active.saving}>{active.saving ? '保存中…' : '保存 ⌘S'}</button>}
        <button className={'mde-tb-ic' + (rightOpen ? ' on' : '')} data-tip={rightOpen ? '隐藏 AI 助手栏' : '显示 AI 助手栏'} title={rightOpen ? '隐藏 AI 助手栏' : '显示 AI 助手栏'} onClick={toggleRight}><TbIcon name="panelRight" /></button>
      </div>

      <div className={'mde-panes' + (rightOpen ? '' : ' right-collapsed')} ref={panesRef} style={{ '--mde-left-w': leftW + 'px', '--mde-right-w': rightW + 'px' }}>
        {/* 左:文件树 */}
        <aside className="mde-left">
          {scriptId ? <FileTree scriptId={scriptId} openNode={openNode} activeKey={activeKey} reloadKey={treeReloadKey} onMutate={onTreeMutate} /> : <div className="mde-tree-hint">先选剧本</div>}
        </aside>

        <div className="mde-splitter mde-splitter-left" onPointerDown={onSplitDown('left')} title="拖拽调整左栏宽度" />

        {/* 中:标签 + 编辑器 */}
        <main className="mde-center">
          <div className="mde-tabs">
            {tabs.map((t) => (
              <div key={t.key} className={'mde-tab' + (t.key === activeKey ? ' active' : '')} onClick={() => setActiveKey(t.key)}
                title={t.label}
                onContextMenu={(e) => { e.preventDefault(); setTabCtx({ x: e.clientX, y: e.clientY, key: t.key }); }}
                onMouseDown={(e) => { if (e.button === 1) { e.preventDefault(); closeTab(t.key); } /* 中键关闭(VSCode) */ }}>
                <span className="mde-tab-label">{t.dirty ? '● ' : ''}{t.label}</span>
                <span className="mde-tab-close" title="关闭" onClick={(e) => { e.stopPropagation(); closeTab(t.key); }}>×</span>
              </div>
            ))}
          </div>
          <div className="mde-editorwrap" onContextMenu={(e) => { if (!active) return; e.preventDefault(); setEditorCtx({ x: e.clientX, y: e.clientY }); }}>
            <EditorPane tab={active} onChange={onEdit} scriptId={scriptId} onViewReady={(v) => { activeViewRef.current = v; }} onContinueAccept={onProseAccepted} chapterIndex={active && active.kind === 'chapter' ? active.id : null} />
          </div>
          {tabCtx && (() => {
            const idx = tabs.findIndex((t) => t.key === tabCtx.key);
            const others = tabs.filter((t) => t.key !== tabCtx.key).map((t) => t.key);
            const toRight = tabs.slice(idx + 1).map((t) => t.key);
            const saved = tabs.filter((t) => !t.dirty).map((t) => t.key);
            const items = [
              { label: '关闭', kbd: '⌘W', onClick: () => closeTab(tabCtx.key) },
              { label: '关闭其他', disabled: others.length === 0, onClick: () => { setActiveKey(tabCtx.key); closeTabs(others); } },
              { label: '关闭右侧标签页', disabled: toRight.length === 0, onClick: () => closeTabs(toRight) },
              { sep: true },
              { label: '关闭已保存', disabled: saved.length === 0, onClick: () => closeTabs(saved) },
              { label: '全部关闭', disabled: tabs.length === 0, onClick: () => closeTabs(tabs.map((t) => t.key)) },
            ];
            return <ContextMenu x={tabCtx.x} y={tabCtx.y} items={items} onClose={() => setTabCtx(null)} />;
          })()}
          {editorCtx && (
            <ContextMenu x={editorCtx.x} y={editorCtx.y} onClose={() => setEditorCtx(null)} items={[
              { label: '剪切', kbd: '⌘X', onClick: doCut },
              { label: '复制', kbd: '⌘C', onClick: doCopy },
              { label: '粘贴', kbd: '⌘V', onClick: doPaste },
              { sep: true },
              { label: '全选', kbd: '⌘A', onClick: doSelectAll },
              { sep: true },
              { label: '撤销', kbd: '⌘Z', onClick: doUndo },
              { label: '重做', kbd: '⌘⇧Z', onClick: doRedo },
            ]} />
          )}
          {syncNudge && (
            <div className="mde-syncbar">
              <span className="mde-syncbar-text">
                刚{syncNudge.rewrite ? '改写' : '续写'}的内容若引入或改变了设定,要让助手同步进角色卡 / 世界书 / 时间线吗?
              </span>
              <button className="mde-syncbar-go" onClick={doSync}>同步设定</button>
              <button className="mde-syncbar-no" onClick={() => setSyncNudge(null)}>忽略</button>
            </div>
          )}
        </main>

        <div className="mde-splitter mde-splitter-right" onPointerDown={onSplitDown('right')} title="拖拽调整右栏宽度" />

        {/* 右:agent 直写面板(console_assistant SSE)+ 续写到正文 */}
        <aside className="mde-right">
          {scriptId
            ? <MdEditorAgent ref={agentRef} scriptId={scriptId} activeTab={active} onWriteComplete={refreshTab} onContinue={onContinue} />
            : <div className="mde-tree-hint">先选剧本</div>}
        </aside>
      </div>
    </div>
  );
}

// ── 节点内容 加载:GET 行 → md-serialize.toMd ─────────────────────────────
async function loadNodeContent(kind, sid, id) {
  const row = await loadRow(kind, sid, id);
  return toMd(kind, row);
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
async function saveNodeContent(kind, sid, id, content, original) {
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
        if (added.length) parts.push('新增了字段「' + added.join('、') + '」');
        if (removed.length) parts.push('删除/改名了字段「' + removed.join('、') + '」');
        throw new Error('front-matter 字段被冻结,只能改值不能增删字段:你' + parts.join(';') + '。请改回字段名,只编辑冒号后的值。');
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
    await A.scripts.updateChapter(sid, id, diff);   // 收 {title?, content?, volume_title?}
    return;
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
    if (!A.scripts.anchorUpdate) throw new Error('时间线写端点未就绪(需后端 P1)');
    await A.scripts.anchorUpdate(sid, id, diff);
    return;
  }
  if (kind === 'canon') {
    if (!A.scripts.canonUpsert) throw new Error('canon 写端点未就绪(需后端 P1)');
    await A.scripts.canonUpsert(sid, { logical_key: id, ...diff });
    return;
  }
  throw new Error(`未知实体类型:${kind}`);
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
