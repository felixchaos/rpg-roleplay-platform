/* CanonEntityEditorView — inline table editor for kb_canon_entities.
   No modal dialogs. SplitPanel for detail. Inline confirmation for delete.
   AWS Cloudscape Design System throughout.
   Mechanically extracted from pages/script-edit-canon.jsx (zero behavior change).

   单元格 / 删除确认 / 详情面板 / 新建表单都定义在模块顶层(以前在组件函数体里,每次渲染都是
   新的组件类型 → 整表单元格全部卸载重挂:行内改名每敲一个键重挂几千行,新建表单每敲一个字
   输入框就丢焦点)。回调经 ref 保持引用稳定,单元格 memo 后只有正在编辑的那一格随输入重渲染。
   canonList 全量拉取后按页渲染(每页 CANON_PAGE_SIZE 行),上级下拉带过滤。 */

import React from 'react';
import { useTranslation } from 'react-i18next';

import CSHeader from '@cloudscape-design/components/header';
import CSTable from '@cloudscape-design/components/table';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSAlert from '@cloudscape-design/components/alert';
import CSInput from '@cloudscape-design/components/input';
import CSSelect from '@cloudscape-design/components/select';
import CSTextFilter from '@cloudscape-design/components/text-filter';
import DetailDrawer from '../DetailDrawer.jsx';
import CSTokenGroup from '@cloudscape-design/components/token-group';
import CSExpandableSection from '@cloudscape-design/components/expandable-section';
import CSFormField from '@cloudscape-design/components/form-field';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSSegmentedControl from '@cloudscape-design/components/segmented-control';
import CSPagination from '@cloudscape-design/components/pagination';

import { snippet } from './helpers.js';

/* ------------------------------------------------------------------ */
/* Constants                                                             */
/* ------------------------------------------------------------------ */
// 与后端白名单一致(kb.canon_repo.CANON_ENTITY_TYPES;organization 是提取链路会产出的类型)。
const ENTITY_TYPES = ['character', 'faction', 'organization', 'location', 'item', 'concept'];
const IMPORTANCE_OPTIONS = [1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: String(n) }));
const CANON_PAGE_SIZE = 100;
const EMPTY_NEW_FORM = { logical_key: '', name: '', type: 'character', entity_subtype: '', importance: '3', summary: '' };

const editableStyle = (readonly, cursor) => ({
  cursor: readonly ? 'default' : cursor,
  borderBottom: readonly ? 'none' : '1px dashed var(--color-border-divider-default, #ccc)',
});

// 引用稳定的回调:返回的函数身份永远不变,调用时总是走最新一次渲染的实现。
// 传给 memo 过的单元格,父组件重渲染时单元格不会因为回调换了新函数而跟着重渲染。
function useStableCallback(fn) {
  const ref = React.useRef(fn);
  ref.current = fn;
  return React.useCallback((...args) => ref.current(...args), []);
}

/* ------------------------------------------------------------------ */
/* Table cells (module level: stable component types, no remount)       */
/* ------------------------------------------------------------------ */
/* inline editable cell — name。editValue:这一格正在编辑时是输入中的值,否则 null。 */
const CellName = React.memo(function CellName({ entity, editValue, readonly, onStartEdit, onEditChange, onCancelEdit, onSave }) {
  if (editValue != null) {
    return (
      <CSInput
        autoFocus
        value={editValue}
        onChange={({ detail }) => onEditChange(detail.value)}
        onKeyDown={({ detail }) => {
          if (detail.key === 'Enter') onSave(entity, 'name', editValue);
          if (detail.key === 'Escape') onCancelEdit();
        }}
        onBlur={() => onSave(entity, 'name', editValue)}
      />
    );
  }
  return (
    <span
      style={editableStyle(readonly, 'text')}
      onClick={() => !readonly && onStartEdit(entity, 'name', entity.name || '')}
    >
      {entity.name || '—'}
    </span>
  );
});

/* inline editable cell — importance */
const CellImportance = React.memo(function CellImportance({ entity, editValue, readonly, onStartEdit, onCancelEdit, onSave }) {
  if (editValue != null) {
    return (
      <CSSelect
        selectedOption={IMPORTANCE_OPTIONS.find((o) => o.value === String(editValue)) || null}
        options={IMPORTANCE_OPTIONS}
        onChange={({ detail }) => onSave(entity, 'importance', detail.selectedOption.value)}
        onBlur={onCancelEdit}
      />
    );
  }
  return (
    <span
      style={editableStyle(readonly, 'pointer')}
      onClick={() => !readonly && onStartEdit(entity, 'importance', String(entity.importance ?? 3))}
    >
      {entity.importance ?? '—'}
    </span>
  );
});

/* inline editable cell — parent。上级选项只在这一格进入编辑时才生成(几千条实体时
   不为每一行都备一份),排除实体自己,并带过滤框。 */
const CellParent = React.memo(function CellParent({ entity, editValue, readonly, parentName, items, onStartEdit, onCancelEdit, onSave }) {
  const { t } = useTranslation();
  const editing = editValue != null;
  const options = React.useMemo(() => {
    if (!editing) return [];
    const opts = [{ value: '', label: t('scripts.edit.canon.no_parent') }];
    items.forEach((e) => {
      if (e.logical_key !== entity.logical_key) opts.push({ value: e.logical_key, label: e.name || e.logical_key });
    });
    return opts;
  }, [editing, items, entity.logical_key, t]);
  if (editing) {
    const curOpt = options.find((o) => o.value === (editValue || '')) || options[0];
    return (
      <CSSelect
        selectedOption={curOpt}
        options={options}
        filteringType="auto"
        onChange={({ detail }) => onSave(entity, 'parent_logical_key', detail.selectedOption.value || null)}
        onBlur={onCancelEdit}
      />
    );
  }
  return (
    <span
      style={editableStyle(readonly, 'pointer')}
      onClick={() => !readonly && onStartEdit(entity, 'parent_logical_key', entity.parent_logical_key || '')}
    >
      {parentName}
    </span>
  );
});

/* inline delete confirmation row */
const DeleteConfirmRow = React.memo(function DeleteConfirmRow({ entity, confirming, readonly, onAsk, onConfirm, onCancel }) {
  const { t } = useTranslation();
  if (!confirming) {
    return (
      <CSButton
        variant="inline-link"
        iconName="remove"
        disabled={readonly}
        onClick={() => onAsk(entity.logical_key)}
      >
        {t('common.delete')}
      </CSButton>
    );
  }
  return (
    <CSSpaceBetween direction="horizontal" size="xs">
      <CSStatusIndicator type="warning">{t('scripts.edit.canon.confirm_delete')}</CSStatusIndicator>
      <CSButton variant="inline-link" iconName="check" onClick={() => onConfirm(entity.logical_key)}>
        {t('common.confirm')}
      </CSButton>
      <CSButton variant="inline-link" iconName="close" onClick={onCancel}>
        {t('common.cancel')}
      </CSButton>
    </CSSpaceBetween>
  );
});

/* ------------------------------------------------------------------ */
/* CanonEntityEditorView                                                 */
/* ------------------------------------------------------------------ */
export function CanonEntityEditorView({ scriptId, ownerId, currentUserId }) {
  const { t } = useTranslation();
  const readonly = ownerId != null && currentUserId != null && ownerId !== currentUserId;

  /* data */
  const [items, setItems] = React.useState([]);
  const [loading, setLoading] = React.useState(true);
  const [reloadTick, setReloadTick] = React.useState(0);

  /* filters */
  const [typeFilter, setTypeFilter] = React.useState('all');
  const [query, setQuery] = React.useState('');
  const [sortDesc, setSortDesc] = React.useState(true);
  const [page, setPage] = React.useState(1);

  /* selection / split panel */
  const [selected, setSelected] = React.useState(null); // entity object
  const [splitOpen, setSplitOpen] = React.useState(false);

  /* inline edit state — { key, field, value } */
  const [editCell, setEditCell] = React.useState(null);

  /* new entity form */
  const [adding, setAdding] = React.useState(false);
  const [newForm, setNewForm] = React.useState(EMPTY_NEW_FORM);

  /* delete confirmation inline */
  const [confirmDelete, setConfirmDelete] = React.useState(null); // logical_key

  /* detail panel edit */
  const [detailEdit, setDetailEdit] = React.useState({}); // pending field values for selected entity
  const [savingDetail, setSavingDetail] = React.useState(false);

  /* ---- fetch ---- */
  // 走 window.api(统一错误归一 + 会话);canonList 默认全量拉取,type 交给后端过滤
  // (之前裸 fetch ?limit=500 被后端夹到 200、type 参数被忽略,类型切换形同虚设)。
  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const q = (typeFilter && typeFilter !== 'all') ? { type: typeFilter } : {};
    Promise.resolve()
      .then(() => window.api.scripts.canonList(scriptId, q))
      .then((j) => { if (!cancelled) setItems(Array.isArray(j) ? j : (j?.items || [])); })
      .catch(() => { if (!cancelled) setItems([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [scriptId, typeFilter, reloadTick]);

  /* ---- derived ---- */
  const filtered = React.useMemo(() => {
    let list = items;
    if (query) {
      const q = query.toLowerCase();
      list = list.filter((e) =>
        (e.name || '').toLowerCase().includes(q) ||
        (e.logical_key || '').toLowerCase().includes(q) ||
        (e.entity_subtype || '').toLowerCase().includes(q)
      );
    }
    list = [...list].sort((a, b) => {
      const ai = a.importance ?? 0;
      const bi = b.importance ?? 0;
      return sortDesc ? bi - ai : ai - bi;
    });
    return list;
  }, [items, query, sortDesc]);

  // 换筛选 / 类型 / 排序回到第一页;删掉条目后页数变少时夹到最后一页。
  React.useEffect(() => { setPage(1); }, [query, typeFilter, sortDesc]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / CANON_PAGE_SIZE));
  const curPage = Math.min(page, pageCount);
  const paged = React.useMemo(
    () => filtered.slice((curPage - 1) * CANON_PAGE_SIZE, curPage * CANON_PAGE_SIZE),
    [filtered, curPage],
  );

  /* lookup parent name */
  const entityMap = React.useMemo(() => {
    const m = {};
    items.forEach((e) => { m[e.logical_key] = e; });
    return m;
  }, [items]);

  /* ---- API calls ---- */
  // 统一走 api-client:非 2xx 抛 ApiError,message 就是后端 error 原文(直接进 toast detail)。
  // 之前裸 fetch + r.json(),后端 500 回纯文本时 detail 变成 JSON 解析异常,看不出原因。
  async function apiPut(logicalKey, body) {
    const j = await window.api.scripts.canonUpdate(scriptId, logicalKey, body);
    if (j && j.ok === false) throw new Error(j.error || t('scripts.toast.save_fail'));
    return j;
  }

  async function apiPost(body) {
    // logical_key 留空 → 不发,后端按名字+类型自动生成;填了就按填的建(撞键后端回 409 + 原因)
    const { logical_key: lk, ...rest } = body;
    const key = String(lk || '').trim();
    const j = await window.api.scripts.canonCreate(scriptId, key ? { ...rest, logical_key: key } : rest);
    if (j && j.ok === false) throw new Error(j.error || t('scripts.toast.save_fail'));
    return j;
  }

  async function apiDelete(logicalKey) {
    const j = await window.api.scripts.canonDelete(scriptId, logicalKey);
    if (j && j.ok === false) throw new Error(j.error || t('scripts.toast.delete_fail'));
    return j;
  }

  /* ---- inline cell save ---- */
  const saveCell = useStableCallback(async (entity, field, value) => {
    if (readonly) return;
    const patch = { [field]: field === 'importance' ? (parseInt(value, 10) || null) : value };
    try {
      await apiPut(entity.logical_key, patch);
      setItems((arr) => arr.map((e) => e.logical_key === entity.logical_key ? { ...e, ...patch } : e));
      if (selected?.logical_key === entity.logical_key) setSelected((s) => s ? { ...s, ...patch } : s);
      window.__apiToast?.(t('scripts.toast.saved'), { kind: 'ok', duration: 1500 });
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.save_fail'), { kind: 'danger', detail: e?.message });
    }
    setEditCell(null);
  });
  const startEdit = React.useCallback((entity, field, value) => setEditCell({ key: entity.logical_key, field, value }), []);
  const changeEdit = React.useCallback((value) => setEditCell((c) => (c ? { ...c, value } : c)), []);
  const cancelEdit = React.useCallback(() => setEditCell(null), []);
  const editValueFor = (entity, field) => (
    editCell && editCell.key === entity.logical_key && editCell.field === field ? editCell.value : null
  );

  /* ---- add new entity ---- */
  const submitAdd = useStableCallback(async () => {
    if (readonly) return;
    const body = { ...newForm, name: (newForm.name || '').trim(), importance: parseInt(newForm.importance, 10) || 3 };
    if (!body.name) {
      window.__apiToast?.(t('scripts.edit.canon.add_required'), { kind: 'warn' });
      return;
    }
    try {
      await apiPost(body);
      setAdding(false);
      setNewForm(EMPTY_NEW_FORM);
      setReloadTick((x) => x + 1);
      window.__apiToast?.(t('scripts.edit.canon.add_ok'), { kind: 'ok' });
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.save_fail'), { kind: 'danger', detail: e?.message });
    }
  });
  const cancelAdd = React.useCallback(() => { setAdding(false); setNewForm(EMPTY_NEW_FORM); }, []);

  /* ---- delete ---- */
  // 后端删除时把挂在它下面的子实体改成无上级,本地同步(否则上级列显示已删除的原始 key)。
  const detachLocal = (e, logicalKey) => (e.parent_logical_key === logicalKey ? { ...e, parent_logical_key: '' } : e);
  const doDelete = useStableCallback(async (logicalKey) => {
    if (readonly) return;
    try {
      await apiDelete(logicalKey);
      setItems((arr) => arr.filter((e) => e.logical_key !== logicalKey).map((e) => detachLocal(e, logicalKey)));
      if (selected?.logical_key === logicalKey) { setSelected(null); setSplitOpen(false); }
      else if (selected) setSelected((s) => (s ? detachLocal(s, logicalKey) : s));
      setConfirmDelete(null);
      window.__apiToast?.(t('scripts.edit.canon.deleted'), { kind: 'ok' });
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.delete_fail'), { kind: 'danger', detail: e?.message });
    }
  });
  const askDelete = React.useCallback((logicalKey) => setConfirmDelete(logicalKey), []);
  const cancelDelete = React.useCallback(() => setConfirmDelete(null), []);

  /* ---- detail panel save ---- */
  const saveDetail = useStableCallback(async () => {
    if (!selected || readonly) return;
    const patch = { ...detailEdit };
    if ('importance' in patch) patch.importance = parseInt(patch.importance, 10) || null;
    if ('aliases' in patch && typeof patch.aliases === 'string') {
      patch.aliases = patch.aliases.split(',').map((s) => s.trim()).filter(Boolean);
    }
    setSavingDetail(true);
    try {
      await apiPut(selected.logical_key, patch);
      const updated = { ...selected, ...patch };
      setSelected(updated);
      setItems((arr) => arr.map((e) => e.logical_key === selected.logical_key ? updated : e));
      setDetailEdit({});
      window.__apiToast?.(t('scripts.toast.saved'), { kind: 'ok' });
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.save_fail'), { kind: 'danger', detail: e?.message });
    } finally { setSavingDetail(false); }
  });

  const openDetail = React.useCallback((e) => { setSelected(e); setDetailEdit({}); setSplitOpen(true); }, []);

  /* ---------------------------------------------------------------- */
  /* Render helpers                                                     */
  /* ---------------------------------------------------------------- */
  function renderTypeFilterControl() {
    const segments = [
      { id: 'all', text: t('scripts.edit.canon.type_all') },
      ...ENTITY_TYPES.map((tp) => ({ id: tp, text: t(`scripts.edit.canon.type_${tp}`) })),
    ];
    return (
      <CSSegmentedControl
        selectedId={typeFilter}
        onChange={({ detail }) => setTypeFilter(detail.selectedId)}
        options={segments}
      />
    );
  }

  /* ---- column definitions ---- */
  const columns = [
    {
      id: 'name',
      header: t('scripts.edit.canon.col_name'),
      cell: (e) => (
        <CellName
          entity={e}
          editValue={editValueFor(e, 'name')}
          readonly={readonly}
          onStartEdit={startEdit}
          onEditChange={changeEdit}
          onCancelEdit={cancelEdit}
          onSave={saveCell}
        />
      ),
      sortingField: 'name',
    },
    {
      id: 'type',
      header: t('scripts.edit.canon.col_type'),
      cell: (e) => <CSBadge color={typeBadgeColor(e.type)}>{t(`scripts.edit.canon.type_${e.type}`) || e.type}</CSBadge>,
    },
    {
      id: 'subtype',
      header: t('scripts.edit.canon.col_subtype'),
      cell: (e) => e.entity_subtype || '—',
    },
    {
      id: 'parent',
      header: t('scripts.edit.canon.col_parent'),
      cell: (e) => (
        <CellParent
          entity={e}
          editValue={editValueFor(e, 'parent_logical_key')}
          readonly={readonly}
          parentName={e.parent_logical_key ? (entityMap[e.parent_logical_key]?.name || e.parent_logical_key) : '—'}
          items={items}
          onStartEdit={startEdit}
          onCancelEdit={cancelEdit}
          onSave={saveCell}
        />
      ),
    },
    {
      id: 'importance',
      header: t('scripts.edit.canon.col_importance'),
      cell: (e) => (
        <CellImportance
          entity={e}
          editValue={editValueFor(e, 'importance')}
          readonly={readonly}
          onStartEdit={startEdit}
          onCancelEdit={cancelEdit}
          onSave={saveCell}
        />
      ),
    },
    {
      id: 'summary',
      header: t('scripts.edit.canon.col_summary'),
      cell: (e) => <CSBox color="text-body-secondary" fontSize="body-s">{snippet(e.summary, 50)}</CSBox>,
    },
    {
      id: 'actions',
      header: '',
      cell: (e) => (
        <CSSpaceBetween direction="horizontal" size="xxs">
          <CSButton
            variant="inline-link"
            iconName="search"
            onClick={() => openDetail(e)}
          >
            {t('scripts.edit.canon.view_detail')}
          </CSButton>
          <DeleteConfirmRow
            entity={e}
            confirming={confirmDelete === e.logical_key}
            readonly={readonly}
            onAsk={askDelete}
            onConfirm={doDelete}
            onCancel={cancelDelete}
          />
        </CSSpaceBetween>
      ),
    },
  ];

  /* ---- main render ---- */
  const tableEl = (
    <CSTable
      variant="container"
      loading={loading}
      loadingText={t('scripts.edit.canon.loading')}
      items={paged}
      trackBy="logical_key"
      selectionType="single"
      selectedItems={selected ? [selected] : []}
      onSelectionChange={({ detail }) => {
        const e = detail.selectedItems[0];
        if (e) openDetail(e);
      }}
      columnDefinitions={columns}
      header={
        <CSHeader
          variant="h2"
          counter={`(${filtered.length})`}
          actions={
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton
                iconName={sortDesc ? 'sort-descending' : 'sort-ascending'}
                variant="icon"
                ariaLabel={t('scripts.edit.canon.sort_importance')}
                onClick={() => setSortDesc((v) => !v)}
              />
              <CSButton iconName="refresh" variant="icon" ariaLabel={t('common.refresh')} onClick={() => setReloadTick((x) => x + 1)} />
              {!readonly && (
                <CSButton iconName="add-plus" variant="primary" onClick={() => setAdding((v) => !v)}>
                  {t('scripts.edit.canon.add_btn')}
                </CSButton>
              )}
            </CSSpaceBetween>
          }
          description={t('scripts.edit.canon.description')}
        >
          {t('scripts.edit.canon.title')}
        </CSHeader>
      }
      filter={
        <CSSpaceBetween direction="horizontal" size="s">
          {renderTypeFilterControl()}
          <CSTextFilter
            filteringText={query}
            filteringPlaceholder={t('scripts.edit.canon.search_ph')}
            onChange={({ detail }) => setQuery(detail.filteringText)}
          />
        </CSSpaceBetween>
      }
      pagination={
        pageCount > 1 ? (
          <CSPagination
            currentPageIndex={curPage}
            pagesCount={pageCount}
            onChange={({ detail }) => setPage(detail.currentPageIndex)}
          />
        ) : undefined
      }
      empty={
        <CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
          {query ? t('scripts.edit.canon.empty_search') : t('scripts.edit.canon.empty')}
        </CSBox>
      }
    />
  );

  return (
    <CSSpaceBetween size="m">
      {readonly && (
        <CSAlert type="info" header={t('scripts.edit.readonly_title')}>
          {t('scripts.edit.readonly_body')}
        </CSAlert>
      )}
      {adding && !readonly && (
        <AddEntityForm newForm={newForm} setNewForm={setNewForm} onSubmit={submitAdd} onCancel={cancelAdd} />
      )}
      <DetailDrawer
        open={splitOpen && !!selected}
        title={selected?.name || selected?.logical_key || ''}
        onClose={() => { setSelected(null); setSplitOpen(false); }}
        closeLabel={t('common.close')}
      >
        {selected && (
          <DetailPanel
            entity={selected}
            readonly={readonly}
            parent={selected.parent_logical_key ? entityMap[selected.parent_logical_key] : null}
            childEntities={items.filter((e) => e.parent_logical_key === selected.logical_key)}
            detailEdit={detailEdit}
            setDetailEdit={setDetailEdit}
            savingDetail={savingDetail}
            onSave={saveDetail}
          />
        )}
      </DetailDrawer>
      {tableEl}
    </CSSpaceBetween>
  );
}

/* ------------------------------------------------------------------ */
/* Detail panel (module level)                                          */
/* ------------------------------------------------------------------ */
function DetailPanel({ entity, readonly, parent, childEntities, detailEdit, setDetailEdit, savingDetail, onSave }) {
  const { t } = useTranslation();
  const detailVal = (field) => (field in detailEdit ? detailEdit[field] : entity[field]);
  const setDF = (field, val) => setDetailEdit((d) => ({ ...d, [field]: val }));
  const isDirty = Object.keys(detailEdit).length > 0;

  const aliases = detailVal('aliases');
  const aliasTokens = Array.isArray(aliases)
    ? aliases.map((a) => ({ label: a, dismissLabel: `Remove ${a}` }))
    : [];

  return (
    <CSSpaceBetween size="m">
      {readonly && (
        <CSAlert type="info" header={t('scripts.edit.readonly_title')}>{t('scripts.edit.readonly_body')}</CSAlert>
      )}

      <CSKeyValuePairs columns={2} items={[
        { label: t('scripts.edit.canon.field_logical_key'), value: <span className="mono">{entity.logical_key}</span> },
        { label: t('scripts.edit.canon.field_type'), value: <CSBadge color={typeBadgeColor(entity.type)}>{t(`scripts.edit.canon.type_${entity.type}`) || entity.type}</CSBadge> },
        { label: t('scripts.edit.canon.field_subtype'), value: entity.entity_subtype || '—' },
        { label: t('scripts.edit.canon.field_importance'), value: entity.importance ?? '—' },
        { label: t('scripts.edit.canon.field_first_chapter'), value: entity.first_revealed_chapter ?? '—' },
      ]} />

      <CSFormField label={t('scripts.edit.canon.field_name')}>
        <CSInput disabled={readonly} value={detailVal('name') || ''} onChange={({ detail }) => setDF('name', detail.value)} />
      </CSFormField>

      <CSFormField label={t('scripts.edit.canon.field_identity')}>
        <CSInput disabled={readonly} value={detailVal('identity') || ''} onChange={({ detail }) => setDF('identity', detail.value)} />
      </CSFormField>

      <CSFormField label={t('scripts.edit.canon.field_summary')}>
        <CSTextarea disabled={readonly} rows={3} value={detailVal('summary') || ''} onChange={({ detail }) => setDF('summary', detail.value)} />
      </CSFormField>

      <CSFormField label={t('scripts.edit.canon.field_background')}>
        <CSTextarea disabled={readonly} rows={4} value={detailVal('background') || ''} onChange={({ detail }) => setDF('background', detail.value)} />
      </CSFormField>

      <CSFormField label={t('scripts.edit.canon.field_aliases')}>
        <CSTokenGroup
          readOnly={readonly}
          items={aliasTokens}
          onDismiss={({ detail }) => {
            const updated = aliasTokens.filter((_, i) => i !== detail.itemIndex).map((tk) => tk.label);
            setDF('aliases', updated);
          }}
          i18nStrings={{ removeButtonAriaLabel: (tk) => `Remove ${tk.label}` }}
        />
        {!readonly && (
          <div style={{ marginTop: 6 }}>
            <AddAliasInput
              onAdd={(alias) => {
                const current = Array.isArray(detailVal('aliases')) ? detailVal('aliases') : (Array.isArray(entity.aliases) ? entity.aliases : []);
                if (alias && !current.includes(alias)) setDF('aliases', [...current, alias]);
              }}
            />
          </div>
        )}
      </CSFormField>

      {/* Tree view: parent → entity → children */}
      <CSExpandableSection headerText={t('scripts.edit.canon.tree_view')} defaultExpanded={false}>
        <CSSpaceBetween size="xs">
          {parent && (
            <div style={{ paddingLeft: 0 }}>
              <CSBox fontSize="body-s" color="text-body-secondary">
                ↑ {t('scripts.edit.canon.parent')}: <strong>{parent.name || parent.logical_key}</strong>
                {parent.entity_subtype ? ` (${parent.entity_subtype})` : ''}
              </CSBox>
            </div>
          )}
          <div style={{ paddingLeft: 16, borderLeft: '2px solid var(--color-border-divider-default, #ccc)' }}>
            <CSBox fontWeight="bold">{entity.name || entity.logical_key}</CSBox>
            <CSBox fontSize="body-s" color="text-body-secondary">
              {t(`scripts.edit.canon.type_${entity.type}`) || entity.type}
              {entity.entity_subtype ? ` · ${entity.entity_subtype}` : ''}
            </CSBox>
          </div>
          {childEntities.length > 0 && (
            <div style={{ paddingLeft: 32 }}>
              <CSBox fontSize="body-s" color="text-body-secondary">
                ↓ {t('scripts.edit.canon.children')} ({childEntities.length}):
              </CSBox>
              {childEntities.map((ch) => (
                <div key={ch.logical_key} style={{ paddingLeft: 8 }}>
                  <CSBox fontSize="body-s">
                    • <strong>{ch.name || ch.logical_key}</strong>
                    {ch.entity_subtype ? ` (${ch.entity_subtype})` : ''}
                  </CSBox>
                </div>
              ))}
            </div>
          )}
        </CSSpaceBetween>
      </CSExpandableSection>

      {!readonly && isDirty && (
        <CSSpaceBetween direction="horizontal" size="xs">
          <CSButton variant="primary" loading={savingDetail} onClick={onSave}>
            {t('common.save')}
          </CSButton>
          <CSButton variant="link" onClick={() => setDetailEdit({})}>
            {t('common.cancel')}
          </CSButton>
        </CSSpaceBetween>
      )}
    </CSSpaceBetween>
  );
}

/* ------------------------------------------------------------------ */
/* New entity add row form (module level)                               */
/* ------------------------------------------------------------------ */
function AddEntityForm({ newForm, setNewForm, onSubmit, onCancel }) {
  const { t } = useTranslation();
  const typeOptions = ENTITY_TYPES.map((tp) => ({ value: tp, label: t(`scripts.edit.canon.type_${tp}`) }));
  return (
    <div style={{ padding: '12px 16px', background: 'var(--color-background-container-content)', border: '1px solid var(--color-border-container-top)', borderRadius: 8, marginBottom: 8 }}>
      <CSBox variant="h3" padding={{ bottom: 's' }}>{t('scripts.edit.canon.add_title')}</CSBox>
      <CSSpaceBetween direction="horizontal" size="s">
        <CSFormField label={t('scripts.edit.canon.field_logical_key')}>
          <CSInput
            placeholder={t('scripts.edit.canon.field_logical_key_ph')}
            value={newForm.logical_key}
            onChange={({ detail }) => setNewForm((f) => ({ ...f, logical_key: detail.value }))}
          />
        </CSFormField>
        <CSFormField label={t('scripts.edit.canon.field_name')}>
          <CSInput
            placeholder={t('scripts.edit.canon.field_name_ph')}
            value={newForm.name}
            onChange={({ detail }) => setNewForm((f) => ({ ...f, name: detail.value }))}
          />
        </CSFormField>
        <CSFormField label={t('scripts.edit.canon.field_type')}>
          <CSSelect
            selectedOption={typeOptions.find((o) => o.value === newForm.type) || null}
            options={typeOptions}
            onChange={({ detail }) => setNewForm((f) => ({ ...f, type: detail.selectedOption.value }))}
          />
        </CSFormField>
        <CSFormField label={t('scripts.edit.canon.field_subtype')}>
          <CSInput
            placeholder={t('script_canon.subtype_ph')}
            value={newForm.entity_subtype}
            onChange={({ detail }) => setNewForm((f) => ({ ...f, entity_subtype: detail.value }))}
          />
        </CSFormField>
        <CSFormField label={t('scripts.edit.canon.field_importance')}>
          <CSSelect
            selectedOption={IMPORTANCE_OPTIONS.find((o) => o.value === newForm.importance) || IMPORTANCE_OPTIONS[2]}
            options={IMPORTANCE_OPTIONS}
            onChange={({ detail }) => setNewForm((f) => ({ ...f, importance: detail.selectedOption.value }))}
          />
        </CSFormField>
      </CSSpaceBetween>
      <CSFormField label={t('scripts.edit.canon.field_summary')}>
        <CSInput
          placeholder={t('scripts.edit.canon.field_summary_ph')}
          value={newForm.summary}
          onChange={({ detail }) => setNewForm((f) => ({ ...f, summary: detail.value }))}
        />
      </CSFormField>
      <div style={{ marginTop: 10 }}>
        <CSSpaceBetween direction="horizontal" size="xs">
          <CSButton variant="primary" iconName="add-plus" onClick={onSubmit}>{t('scripts.edit.canon.add_confirm')}</CSButton>
          <CSButton variant="link" onClick={onCancel}>
            {t('common.cancel')}
          </CSButton>
        </CSSpaceBetween>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Helper: AddAliasInput                                                */
/* ------------------------------------------------------------------ */
function AddAliasInput({ onAdd }) {
  const { t } = useTranslation();
  const [val, setVal] = React.useState('');
  return (
    <CSSpaceBetween direction="horizontal" size="xs">
      <CSInput
        placeholder={t('scripts.edit.canon.alias_ph')}
        value={val}
        onChange={({ detail }) => setVal(detail.value)}
        onKeyDown={({ detail }) => { if (detail.key === 'Enter' && val.trim()) { onAdd(val.trim()); setVal(''); } }}
      />
      <CSButton
        iconName="add-plus"
        variant="icon"
        ariaLabel={t('scripts.edit.canon.alias_add')}
        disabled={!val.trim()}
        onClick={() => { onAdd(val.trim()); setVal(''); }}
      />
    </CSSpaceBetween>
  );
}

/* ------------------------------------------------------------------ */
/* Helper: badge color mapping                                          */
/* ------------------------------------------------------------------ */
function typeBadgeColor(type) {
  switch (type) {
    case 'character': return 'blue';
    case 'faction':   return 'green';
    case 'location':  return 'grey';
    case 'item':      return 'red';
    case 'concept':   return 'severity-neutral';
    default:          return 'grey';
  }
}
