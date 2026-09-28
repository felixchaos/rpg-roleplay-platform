/* WorldbookEditorView — Cloudscape 风格世界书可视化编辑器
   设计规范:
   - 主视图 = CSTable (可排序、可分页、inline-edit: title/priority/enabled)
   - 行选中 → 右侧 SplitPanel 展开详情编辑
   - 新建条目 → 同一 SplitPanel(清空表单模式)
   - 权限态: owner ≠ me → 只读 CSAlert + "另存为"按钮触发 fork
   - 禁止 modal — 使用 inline confirmation 替代
*/

import React from 'react';
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { usePlatformData } from '../platform-app.jsx';

import CSHeader from '@cloudscape-design/components/header';
import CSTable from '@cloudscape-design/components/table';
import CSContainer from '@cloudscape-design/components/container';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSFormField from '@cloudscape-design/components/form-field';
import CSInput from '@cloudscape-design/components/input';
import CSAlert from '@cloudscape-design/components/alert';
import CSTextFilter from '@cloudscape-design/components/text-filter';
import CSPagination from '@cloudscape-design/components/pagination';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSToggle from '@cloudscape-design/components/toggle';
import CSTokenGroup from '@cloudscape-design/components/token-group';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import DetailDrawer from '../components/DetailDrawer.jsx';

const WB_PAGE_SIZE = 50;

/* 推断条目的 subtype 标签 */
function inferSubtype(entry) {
  const sub = entry?.metadata?.subtype || entry?.metadata?.type || entry?.subtype || '';
  if (sub) return sub;
  const priority = Number(entry?.priority ?? 0);
  if (priority >= 80) return 'core';
  if (priority >= 50) return 'major';
  if (priority >= 20) return 'minor';
  return 'detail';
}

/* 条目标签:后端存在 metadata.tags(列表行没有顶层 tags);本地刚保存过的行才有顶层 tags。
   以前只读顶层 → 已有标签永远显示为空,且任何一次保存都回写 tags:[] 把它们清掉。 */
function entryTags(e) {
  if (Array.isArray(e?.tags)) return [...e.tags];
  if (Array.isArray(e?.metadata?.tags)) return [...e.metadata.tags];
  return [];
}

/* 深拷贝一个 entry 用于编辑 */
function cloneEntry(e) {
  return {
    id: e?.id ?? null,
    title: e?.title || e?.keyword || e?.name || e?.key || '',
    content: e?.content || e?.text || e?.description || e?.value || '',
    priority: e?.priority ?? 50,
    enabled: e?.enabled !== false,
    tags: entryTags(e),
    metadata: e?.metadata || {},
  };
}

/* 空条目模板 */
function emptyEntry() {
  return { id: null, title: '', content: '', priority: 50, enabled: true, tags: [], metadata: {} };
}

/* ────────────────────── 主组件 ────────────────────── */
export function WorldbookEditorView({ script }) {
  const { t } = useTranslation();
  const platform = usePlatformData();
  const currentUserId = platform?.user?.id;

  /* 权限判断 */
  const isOwner = !script?.owner_id || script.owner_id === currentUserId;

  /* ── 世界书条目状态 ── */
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);

  /* ── 表格 UI 状态 ── */
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState(null); // 当前选中行 id
  const [sortCol, setSortCol] = useState('priority');
  const [sortAsc, setSortAsc] = useState(false);

  /* ── SplitPanel 状态 ── */
  const [panelOpen, setPanelOpen] = useState(false);
  const [isNew, setIsNew] = useState(false); // 是否新建模式
  const [draft, setDraft] = useState(null);  // 编辑中的副本
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(false); // inline delete confirm
  const [tagInput, setTagInput] = useState('');

  /* ── 批量操作 ── */
  const [selectedItems, setSelectedItems] = useState([]);
  const [batchPriority, setBatchPriority] = useState('');  // 批量设优先级的输入值
  const [batching, setBatching] = useState(false);          // 批量请求进行中(禁用按钮防重复)

  /* ── Fork 状态 ── */
  const [forking, setForking] = useState(false);

  /* ────── 加载数据 ────── */
  useEffect(() => {
    if (!script?.id) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        // fetch_all:一次性拉全量(后端绕开游标分页漏条);否则大世界书只能看到前 50 条。
        const r = await window.api.scripts.worldbook(script.id, { fetch_all: 1 });
        if (!cancelled) setEntries(Array.isArray(r) ? r : (r?.items || r?.entries || []));
      } catch (_) {
        if (!cancelled) setEntries([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [script?.id, reloadTick]);

  /* ────── 过滤 + 排序 + 分页 ────── */
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    let arr = q
      ? entries.filter(e => {
          const title = String(e.title || e.keyword || e.name || e.key || '');
          const content = String(e.content || e.text || e.description || e.value || '');
          return title.toLowerCase().includes(q) || content.toLowerCase().includes(q);
        })
      : [...entries];

    arr.sort((a, b) => {
      let va, vb;
      if (sortCol === 'title') {
        va = String(a.title || a.keyword || '').toLowerCase();
        vb = String(b.title || b.keyword || '').toLowerCase();
        return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
      }
      if (sortCol === 'priority') {
        va = Number(a.priority ?? 0);
        vb = Number(b.priority ?? 0);
        return sortAsc ? va - vb : vb - va;
      }
      if (sortCol === 'enabled') {
        va = a.enabled !== false ? 1 : 0;
        vb = b.enabled !== false ? 1 : 0;
        return sortAsc ? va - vb : vb - va;
      }
      return 0;
    });
    return arr;
  }, [entries, query, sortCol, sortAsc]);

  useEffect(() => { setPage(1); }, [query]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / WB_PAGE_SIZE));
  const paged = filtered.slice((page - 1) * WB_PAGE_SIZE, page * WB_PAGE_SIZE);
  const selectedEntry = entries.find(e => e.id === selectedId) || null;

  /* ────── 打开 SplitPanel ────── */
  const openPanel = useCallback((entry, newMode = false) => {
    setDraft(newMode ? emptyEntry() : cloneEntry(entry));
    setIsNew(newMode);
    setSelectedId(newMode ? null : entry?.id ?? null);
    setDirty(false);
    setDeleteConfirm(false);
    setTagInput('');
    setPanelOpen(true);
  }, []);

  const closePanel = useCallback(() => {
    setPanelOpen(false);
    setDraft(null);
    setDirty(false);
    setDeleteConfirm(false);
    setTagInput('');
    // 不清 selectedId,让行保持高亮
  }, []);

  /* ────── 选行 → 打开 panel ────── */
  // 复选框 selection 变化时:仅更新 selectedItems,不强制打开面板。
  // 面板由 onRowClick(单行点击)和"详情"按钮负责打开,避免批量勾选时
  // 右侧 480px 面板展开挤压左侧表格列宽。
  // 当 selection 清空时仍关闭面板(取消全选 → 面板收起)。
  const onRowSelect = useCallback((items) => {
    setSelectedItems(items);
    if (items.length === 0) closePanel();
  }, [closePanel]);

  /* ────── inline edit (title / priority / enabled) ────── */
  const onSubmitEdit = useCallback(async ({ item, column, newValue }) => {
    if (!isOwner) return;
    const field = column.id;
    const body = {
      title: item.title || item.keyword || item.name || item.key || '',
      content: item.content || item.text || item.description || item.value || '',
      priority: item.priority ?? 50,
      enabled: item.enabled !== false,
      tags: entryTags(item),
    };
    if (field === 'title') body.title = newValue;
    if (field === 'priority') body.priority = Number(newValue) || 0;
    if (field === 'enabled') body.enabled = newValue === true || newValue === 'true';

    try {
      await _wbPut(script.id, item.id, body);
      setEntries(arr => arr.map(e => e.id === item.id ? { ...e, ...body } : e));
      // 如果 panel 里正在编辑同一条,同步更新 draft
      setDraft(d => d && d.id === item.id ? { ...d, ...body } : d);
      window.__apiToast?.(t('scripts.toast.saved'), { kind: 'ok', duration: 1500 });
    } catch (err) {
      window.__apiToast?.(t('scripts.toast.save_fail'), { kind: 'danger', detail: err?.message });
    }
  }, [isOwner, script?.id, t]);

  /* ────── SplitPanel 保存 ────── */
  const onPanelSave = useCallback(async () => {
    if (!draft || !isOwner) return;
    setSaving(true);
    try {
      if (isNew) {
        const r = await _wbPost(script.id, {
          title: draft.title,
          content: draft.content,
          priority: draft.priority,
          enabled: draft.enabled,
          tags: draft.tags,
        });
        window.__apiToast?.(t('scripts.edit.worldbook.toast_created'), { kind: 'ok' });
        setReloadTick(x => x + 1);
        // 后端回 {ok, entry:{id,…}}:以前读 r.id / r.entry_id(都不存在)→ 草稿 id 成 null,
        // 接着再改再存就打 PUT /worldbook/null(422),删除同样落空。
        const newId = r?.entry?.id ?? r?.id ?? r?.entry_id ?? null;
        setIsNew(false);
        setSelectedId(newId);
        setDraft(d => d ? { ...d, id: newId } : d);
        setDirty(false);
      } else {
        await _wbPut(script.id, draft.id, {
          title: draft.title,
          content: draft.content,
          priority: draft.priority,
          enabled: draft.enabled,
          tags: draft.tags,
        });
        setEntries(arr => arr.map(e => e.id === draft.id ? { ...e, ...draft } : e));
        setDirty(false);
        window.__apiToast?.(t('scripts.toast.saved'), { kind: 'ok', duration: 1500 });
      }
    } catch (err) {
      window.__apiToast?.(t('scripts.toast.save_fail'), { kind: 'danger', detail: err?.message });
    } finally {
      setSaving(false);
    }
  }, [draft, isNew, isOwner, script?.id, t]);

  /* ────── SplitPanel 撤销 ────── */
  const onPanelDiscard = useCallback(() => {
    if (isNew) { closePanel(); return; }
    setDraft(cloneEntry(selectedEntry));
    setDirty(false);
    setDeleteConfirm(false);
  }, [isNew, selectedEntry, closePanel]);

  /* ────── SplitPanel 删除 ────── */
  const onPanelDelete = useCallback(async () => {
    if (!draft?.id || !isOwner) return;
    setSaving(true);
    try {
      await _wbDelete(script.id, draft.id);
      window.__apiToast?.(t('scripts.edit.worldbook.toast_deleted'), { kind: 'ok' });
      setEntries(arr => arr.filter(e => e.id !== draft.id));
      setSelectedId(null);
      setSelectedItems([]);
      closePanel();
    } catch (err) {
      window.__apiToast?.(t('scripts.toast.op_fail'), { kind: 'danger', detail: err?.message });
    } finally {
      setSaving(false);
      setDeleteConfirm(false);
    }
  }, [draft, isOwner, script?.id, t, closePanel]);

  /* ────── 批量 enable / disable(走单事务批量端点,不再逐条 PUT) ────── */
  const onBatchEnable = useCallback(async (enable) => {
    if (!isOwner || selectedItems.length === 0 || batching) return;
    const ids = selectedItems.map(i => i.id);
    setBatching(true);
    try {
      await _wbBatch(script.id, { entry_ids: ids, action: enable ? 'enable' : 'disable' });
      setEntries(arr => arr.map(e => ids.includes(e.id) ? { ...e, enabled: enable } : e));
      window.__apiToast?.(enable
        ? t('scripts.edit.worldbook.toast_batch_enabled')
        : t('scripts.edit.worldbook.toast_batch_disabled'),
        { kind: 'ok', duration: 1500 });
      setSelectedItems([]);
    } catch (err) {
      window.__apiToast?.(t('scripts.toast.op_fail'), { kind: 'danger', detail: err?.message });
    } finally {
      setBatching(false);
    }
  }, [isOwner, selectedItems, script?.id, t, batching]);

  /* ────── 批量删除(物理删除,带确认 + editor 源警示) ────── */
  const onBatchDelete = useCallback(async () => {
    if (!isOwner || selectedItems.length === 0 || batching) return;
    const ids = selectedItems.map(i => i.id);
    const n = ids.length;
    // 删除是物理删除。重点提示「会在重建知识库后复活」的那批:source 非 'editor' 的条目
    // (原著自动提取 / 旧无 source)——它们会被 resolve.py 重建从 canon 重新生成,删了又回来。
    // source==='editor'(用户/AI 手写)受重建豁免,删了是真没了,不复活,无需此提示。
    const rebuildCount = selectedItems.filter(
      i => ((i.metadata && i.metadata.source) || '') !== 'editor'
    ).length;
    let message = t('scripts.edit.worldbook.batch_delete_confirm', { n });
    if (rebuildCount > 0) {
      message += '\n' + t('scripts.edit.worldbook.batch_delete_rebuild_warn', { n: rebuildCount });
    }
    const ok = await (window.__confirm
      ? window.__confirm({
          title: t('scripts.edit.worldbook.batch_delete_title'),
          message,
          danger: true,
          confirmText: t('common.delete'),
        })
      : Promise.resolve(window.confirm(message)));
    if (!ok) return;
    setBatching(true);
    try {
      const r = await _wbBatch(script.id, { entry_ids: ids, action: 'delete' });
      setEntries(arr => arr.filter(e => !ids.includes(e.id)));
      setSelectedItems([]);
      if (selectedId && ids.includes(selectedId)) { setSelectedId(null); closePanel(); }
      window.__apiToast?.(
        t('scripts.edit.worldbook.toast_batch_deleted', { n: r?.affected ?? n }),
        { kind: 'ok', duration: 1500 });
    } catch (err) {
      window.__apiToast?.(t('scripts.toast.op_fail'), { kind: 'danger', detail: err?.message });
    } finally {
      setBatching(false);
    }
  }, [isOwner, selectedItems, script?.id, t, batching, selectedId, closePanel]);

  /* ────── 批量设置优先级 ────── */
  const onBatchSetPriority = useCallback(async () => {
    if (!isOwner || selectedItems.length === 0 || batching) return;
    const p = parseInt(batchPriority, 10);
    if (Number.isNaN(p)) {
      window.__apiToast?.(t('scripts.edit.worldbook.batch_priority_invalid'), { kind: 'warning' });
      return;
    }
    const clamped = Math.max(0, Math.min(1000, p));
    const ids = selectedItems.map(i => i.id);
    setBatching(true);
    try {
      await _wbBatch(script.id, { entry_ids: ids, action: 'set_priority', priority: clamped });
      setEntries(arr => arr.map(e => ids.includes(e.id) ? { ...e, priority: clamped } : e));
      window.__apiToast?.(
        t('scripts.edit.worldbook.toast_batch_priority', { n: ids.length, p: clamped }),
        { kind: 'ok', duration: 1500 });
      setSelectedItems([]);
      setBatchPriority('');
    } catch (err) {
      window.__apiToast?.(t('scripts.toast.op_fail'), { kind: 'danger', detail: err?.message });
    } finally {
      setBatching(false);
    }
  }, [isOwner, selectedItems, script?.id, t, batching, batchPriority]);

  /* ────── Fork ────── */
  const onFork = useCallback(async () => {
    setForking(true);
    try {
      const newTitle = `${script.title || t('scripts.edit.worldbook.fork_copy')} (${t('scripts.edit.worldbook.copy_suffix')})`;
      const r = await _fork(script.id, { title: newTitle });
      window.__apiToast?.(t('scripts.edit.worldbook.toast_forked'), { kind: 'ok' });
      // 触发剧本列表刷新
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      return r;
    } catch (err) {
      window.__apiToast?.(t('scripts.edit.worldbook.toast_fork_fail'), { kind: 'danger', detail: err?.message });
    } finally {
      setForking(false);
    }
  }, [script, t]);

  /* ────── Tag 操作 ────── */
  const addTag = useCallback(() => {
    const tag = tagInput.trim();
    if (!tag || !draft) return;
    if (!draft.tags.includes(tag)) {
      setDraft(d => ({ ...d, tags: [...d.tags, tag] }));
      setDirty(true);
    }
    setTagInput('');
  }, [tagInput, draft]);

  const removeTag = useCallback((idx) => {
    setDraft(d => ({ ...d, tags: d.tags.filter((_, i) => i !== idx) }));
    setDirty(true);
  }, []);

  /* ────── 列定义 ────── */
  const columnDefinitions = useMemo(() => [
    {
      id: 'title',
      header: t('scripts.edit.worldbook.col_title'),
      sortingField: 'title',
      cell: (e) => (
        <div>
          <CSBox fontWeight="bold">{e.title || e.keyword || e.name || e.key || '—'}</CSBox>
          {inferSubtype(e) && (
            <CSBox fontSize="body-s" color="text-body-secondary">{inferSubtype(e)}</CSBox>
          )}
        </div>
      ),
      minWidth: 180,
    },
    {
      id: 'content',
      header: t('scripts.edit.worldbook.col_content'),
      cell: (e) => {
        const text = String(e.content || e.text || e.description || e.value || '');
        return (
          <CSBox color="text-body-secondary" fontSize="body-s">
            <span style={{ wordBreak: 'break-word', overflowWrap: 'anywhere' }}>
              {text.slice(0, 60)}{text.length > 60 ? '…' : ''}
            </span>
          </CSBox>
        );
      },
      minWidth: 240,
    },
    {
      id: 'priority',
      header: t('scripts.edit.worldbook.col_priority'),
      sortingField: 'priority',
      width: 100,
      cell: (e) => <CSBox>{e.priority ?? 50}</CSBox>,
    },
    {
      id: 'enabled',
      header: t('scripts.edit.worldbook.col_enabled'),
      sortingField: 'enabled',
      width: 100,
      cell: (e) => e.enabled !== false
        ? <CSStatusIndicator type="success">{t('common.enabled')}</CSStatusIndicator>
        : <CSStatusIndicator type="stopped">{t('common.disabled')}</CSStatusIndicator>,
    },
    {
      id: 'actions',
      header: '',
      minWidth: 110,
      // 点「详情」从右侧滑出抽屉编辑全字段(行内编辑铅笔已去掉,编辑统一在抽屉)。
      cell: (e) => (
        <span style={{ whiteSpace: 'nowrap' }}>
          <CSButton
            variant="inline-link"
            iconName="external"
            onClick={(ev) => { ev?.stopPropagation?.(); openPanel(e, false); }}
          >
            {t('scripts.edit.worldbook.col_detail', { defaultValue: '详情' })}
          </CSButton>
        </span>
      ),
    },
  ], [t, isOwner, openPanel]);

  /* ────── render ────── */

  /* 只读 banner */
  const readonlyBanner = !isOwner && (
    <CSAlert
      type="warning"
      header={t('scripts.edit.worldbook.readonly_header')}
      action={
        <CSButton loading={forking} onClick={onFork} iconName="copy">
          {t('scripts.edit.worldbook.fork_btn')}
        </CSButton>
      }
    >
      {t('scripts.edit.worldbook.readonly_body')}
    </CSAlert>
  );

  /* Table 顶栏 */
  const tableHeader = (
    <CSHeader
      variant="h3"
      counter={`(${filtered.length})`}
      description={t('scripts.edit.worldbook.header_desc')}
      actions={
        isOwner && (
          <CSButton
            variant="primary"
            iconName="add-plus"
            onClick={() => openPanel(null, true)}
          >
            {t('scripts.edit.worldbook.btn_new')}
          </CSButton>
        )
      }
    >
      {t('scripts.edit.worldbook.title')}
    </CSHeader>
  );

  /* 批量操作工具条:仅选中条目时出现,独立一行不挤标题栏(避免折行)。 */
  const selectionToolbar = isOwner && selectedItems.length > 0 && (
    <div style={{
      display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8,
      padding: '8px 12px', borderRadius: 8,
      background: 'var(--info-soft, rgba(122,166,194,.10))',
      border: '1px solid var(--line-soft, #2a2724)',
    }}>
      <CSBox fontWeight="bold">{t('scripts.edit.worldbook.batch_selected', { n: selectedItems.length, defaultValue: `已选 ${selectedItems.length} 条` })}</CSBox>
      <CSButton iconName="status-positive" loading={batching} onClick={() => onBatchEnable(true)}>
        {t('scripts.edit.worldbook.batch_enable')}
      </CSButton>
      <CSButton iconName="status-negative" loading={batching} onClick={() => onBatchEnable(false)}>
        {t('scripts.edit.worldbook.batch_disable')}
      </CSButton>
      <div style={{ width: 96 }}>
        <CSInput
          type="number"
          value={batchPriority}
          placeholder={t('scripts.edit.worldbook.batch_priority_ph')}
          onChange={({ detail }) => setBatchPriority(detail.value)}
        />
      </div>
      <CSButton iconName="edit" loading={batching} disabled={batchPriority === ''} onClick={onBatchSetPriority}>
        {t('scripts.edit.worldbook.batch_set_priority')}
      </CSButton>
      <CSButton iconName="remove" loading={batching} onClick={onBatchDelete}>
        {t('scripts.edit.worldbook.batch_delete')}
      </CSButton>
    </div>
  );

  /* SplitPanel 内容 */
  const splitPanelContent = draft && (
    <CSSpaceBetween size="m">
      {/* 顶部操作栏 */}
      <CSSpaceBetween direction="horizontal" size="xs">
        <CSButton
          variant="primary"
          iconName="check"
          loading={saving}
          disabled={!dirty || !isOwner}
          onClick={onPanelSave}
        >
          {t('common.save')}
        </CSButton>
        <CSButton
          disabled={!dirty || saving}
          onClick={onPanelDiscard}
        >
          {t('scripts.edit.worldbook.btn_discard')}
        </CSButton>
        {!isNew && isOwner && (
          deleteConfirm ? (
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSBox color="text-status-warning" fontSize="body-s" padding={{ top: 'xs' }}>
                {t('scripts.edit.worldbook.delete_confirm_msg')}
              </CSBox>
              <CSButton
                variant="normal"
                loading={saving}
                onClick={onPanelDelete}
              >
                {t('scripts.edit.worldbook.delete_confirm_yes')}
              </CSButton>
              <CSButton onClick={() => setDeleteConfirm(false)}>
                {t('common.cancel')}
              </CSButton>
            </CSSpaceBetween>
          ) : (
            <CSButton
              iconName="remove"
              disabled={saving}
              onClick={() => setDeleteConfirm(true)}
            >
              {t('common.delete')}
            </CSButton>
          )
        )}
      </CSSpaceBetween>

      {/* 表单字段(抽屉单列竖排,适配窄宽) */}
      <CSColumnLayout columns={1}>
        {/* title + content */}
        <CSSpaceBetween size="s">
          <CSFormField label={t('scripts.edit.worldbook.field_title')}>
            <CSInput
              value={draft.title}
              disabled={!isOwner}
              onChange={({ detail }) => { setDraft(d => ({ ...d, title: detail.value })); setDirty(true); }}
            />
          </CSFormField>
          <CSFormField label={t('scripts.edit.worldbook.field_content')}>
            <CSTextarea
              value={draft.content}
              rows={10}
              disabled={!isOwner}
              onChange={({ detail }) => { setDraft(d => ({ ...d, content: detail.value })); setDirty(true); }}
            />
          </CSFormField>
        </CSSpaceBetween>

        {/* priority + enabled + tags */}
        <CSSpaceBetween size="s">
          <CSFormField label={t('scripts.edit.worldbook.field_priority')}>
            <CSInput
              type="number"
              value={String(draft.priority)}
              disabled={!isOwner}
              onChange={({ detail }) => { setDraft(d => ({ ...d, priority: Number(detail.value) || 0 })); setDirty(true); }}
            />
          </CSFormField>

          <CSFormField label={t('scripts.edit.worldbook.field_enabled')}>
            <CSToggle
              checked={draft.enabled}
              disabled={!isOwner}
              onChange={({ detail }) => { setDraft(d => ({ ...d, enabled: detail.checked })); setDirty(true); }}
            >
              {draft.enabled ? t('common.enabled') : t('common.disabled')}
            </CSToggle>
          </CSFormField>

          <CSFormField
            label={t('scripts.edit.worldbook.field_tags')}
            description={isOwner ? t('scripts.edit.worldbook.field_tags_hint') : undefined}
          >
            <CSSpaceBetween size="xs">
              {isOwner && (
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSInput
                    placeholder={t('scripts.edit.worldbook.tag_placeholder')}
                    value={tagInput}
                    onChange={({ detail }) => setTagInput(detail.value)}
                    onKeyDown={({ detail }) => { if (detail.key === 'Enter') addTag(); }}
                  />
                  <CSButton iconName="add-plus" onClick={addTag}>{t('scripts.edit.worldbook.tag_add')}</CSButton>
                </CSSpaceBetween>
              )}
              {draft.tags.length > 0 && (
                <CSTokenGroup
                  disableOuterPadding
                  items={draft.tags.map((tag, i) => ({
                    label: tag,
                    dismissLabel: isOwner ? t('scripts.edit.worldbook.tag_remove', { tag }) : undefined,
                  }))}
                  onDismiss={isOwner ? ({ detail }) => removeTag(detail.itemIndex) : undefined}
                  readOnly={!isOwner}
                />
              )}
              {draft.tags.length === 0 && (
                <CSBox color="text-status-inactive" fontSize="body-s">
                  {t('scripts.edit.worldbook.tags_empty')}
                </CSBox>
              )}
            </CSSpaceBetween>
          </CSFormField>
        </CSSpaceBetween>
      </CSColumnLayout>
    </CSSpaceBetween>
  );

  return (
    <CSSpaceBetween size="m">
      {readonlyBanner}
      {selectionToolbar}

      {/* 全宽表格:详情编辑改成右侧覆盖抽屉(见下),表格不再被侧面板挤压,
          列头不再换行 / 不再被压窄。stickyHeader 让列头随滚动吸顶不跑。 */}
      <CSTable
        variant="container"
        stickyHeader
        trackBy="id"
        selectionType="multi"
        loading={loading}
        loadingText={t('scripts.editor.loading_worldbook')}
        items={paged}
        selectedItems={selectedItems}
        onSelectionChange={({ detail }) => onRowSelect(detail.selectedItems)}
        onRowClick={({ detail }) => openPanel(detail.item, false)}
        sortingColumn={columnDefinitions.find(c => c.id === sortCol)}
        sortingDescending={!sortAsc}
        onSortingChange={({ detail }) => {
          setSortCol(detail.sortingColumn?.id || 'priority');
          setSortAsc(!detail.isDescending);
        }}
        header={tableHeader}
        filter={
          <div style={{ maxWidth: 360 }}>
            <CSTextFilter
              filteringText={query}
              filteringPlaceholder={t('scripts.edit.worldbook.search_placeholder')}
              onChange={({ detail }) => setQuery(detail.filteringText)}
            />
          </div>
        }
        pagination={
          pageCount > 1 ? (
            <CSPagination
              currentPageIndex={page}
              pagesCount={pageCount}
              onChange={({ detail }) => setPage(detail.currentPageIndex)}
            />
          ) : undefined
        }
        columnDefinitions={columnDefinitions}
        wrapLines
        empty={
          <CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
            {query ? t('scripts.edit.worldbook.empty_search') : t('scripts.editor.wb_empty')}
          </CSBox>
        }
      />

      {/* ── 右侧覆盖抽屉(替代 Cloudscape SplitPanel,共用 DetailDrawer):固定宽、无伸缩手柄、
          覆盖在内容上、不挤压表格。点遮罩或 ✕ 关闭。 ── */}
      <DetailDrawer
        open={panelOpen && !!draft}
        title={isNew
          ? t('scripts.edit.worldbook.panel_title_new')
          : (draft?.title || t('scripts.edit.worldbook.panel_title_edit'))}
        onClose={closePanel}
        closeLabel={t('common.close')}
      >
        {splitPanelContent}
      </DetailDrawer>
    </CSSpaceBetween>
  );
}

/* ─────────── API helpers ─────────── */
function _wbPut(scriptId, entryId, body) {
  if (window.api?.scripts?.worldbookUpdate) {
    return window.api.scripts.worldbookUpdate(scriptId, entryId, body);
  }
  // fallback: raw fetch
  return fetch(`/api/v1/scripts/${scriptId}/worldbook/${entryId}`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => {
    if (!r.ok) return r.json().catch(() => ({})).then(j => { throw new Error(j.detail || j.error || r.statusText); });
    return r.json();
  });
}

function _wbPost(scriptId, body) {
  if (window.api?.scripts?.worldbookCreate) {
    return window.api.scripts.worldbookCreate(scriptId, body);
  }
  return fetch(`/api/v1/scripts/${scriptId}/worldbook`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => {
    if (!r.ok) return r.json().catch(() => ({})).then(j => { throw new Error(j.detail || j.error || r.statusText); });
    return r.json();
  });
}

function _wbDelete(scriptId, entryId) {
  if (window.api?.scripts?.worldbookDelete) {
    return window.api.scripts.worldbookDelete(scriptId, entryId);
  }
  return fetch(`/api/v1/scripts/${scriptId}/worldbook/${entryId}`, {
    method: 'DELETE',
    credentials: 'include',
  }).then(r => {
    if (!r.ok) return r.json().catch(() => ({})).then(j => { throw new Error(j.detail || j.error || r.statusText); });
    return r.json().catch(() => ({}));
  });
}

function _wbBatch(scriptId, body) {
  if (window.api?.scripts?.worldbookBatch) {
    return window.api.scripts.worldbookBatch(scriptId, body);
  }
  return fetch(`/api/v1/scripts/${scriptId}/worldbook/batch`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => {
    if (!r.ok) return r.json().catch(() => ({})).then(j => { throw new Error(j.detail || j.error || r.statusText); });
    return r.json();
  });
}

function _fork(scriptId, body) {
  if (window.api?.scripts?.fork) {
    return window.api.scripts.fork(scriptId, body);
  }
  return fetch(`/api/v1/scripts/${scriptId}/fork`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => {
    if (!r.ok) return r.json().catch(() => ({})).then(j => { throw new Error(j.detail || j.error || r.statusText); });
    return r.json();
  });
}

export default WorldbookEditorView;
