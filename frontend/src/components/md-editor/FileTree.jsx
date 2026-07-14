// FileTree.jsx — 左栏 VSCode 风资源管理器 + 新建菜单(机械搬出,逐字节不变)。
import React from 'react';
import { useTranslation } from 'react-i18next';
import { lsGet, lsSet } from '../../lib/storage.js';
import { ContextMenu } from './ContextMenu.jsx';
import { NODE_GROUPS, nodeKey, KIND_ICON, CAN_DELETE, CAN_RENAME, CAN_DRAG, CAN_CREATE_KIND, stripChapterPrefix, api, toast } from './helpers.js';
import { createNode, renameNode, deleteNode, fetchGroupList } from './node-crud.js';
const { useState, useEffect, useCallback, useRef } = React;

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

export { FileTree };
