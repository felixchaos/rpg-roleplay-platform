/* CanonEntityEditorView — inline table editor for kb_canon_entities.
   No modal dialogs. SplitPanel for detail. Inline confirmation for delete.
   AWS Cloudscape Design System throughout.
   Mechanically extracted from pages/script-edit-canon.jsx (zero behavior change). */

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

import { snippet } from './helpers.js';

/* ------------------------------------------------------------------ */
/* Constants                                                             */
/* ------------------------------------------------------------------ */
// 与后端白名单一致(kb.canon_repo.CANON_ENTITY_TYPES;organization 是提取链路会产出的类型)。
const ENTITY_TYPES = ['character', 'faction', 'organization', 'location', 'item', 'concept'];
const IMPORTANCE_OPTIONS = [1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: String(n) }));

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

  /* selection / split panel */
  const [selected, setSelected] = React.useState(null); // entity object
  const [splitOpen, setSplitOpen] = React.useState(false);

  /* inline edit state — map of logical_key → { field: pendingValue } */
  const [editCell, setEditCell] = React.useState(null); // { key, field, value }

  /* new entity form */
  const [adding, setAdding] = React.useState(false);
  const [newForm, setNewForm] = React.useState({ logical_key: '', name: '', type: 'character', entity_subtype: '', importance: '3', summary: '' });

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

  /* lookup parent name */
  const entityMap = React.useMemo(() => {
    const m = {};
    items.forEach((e) => { m[e.logical_key] = e; });
    return m;
  }, [items]);

  /* parent options for select */
  const parentOptions = React.useMemo(() => {
    const opts = [{ value: '', label: t('scripts.edit.canon.no_parent') }];
    items.forEach((e) => {
      if (!selected || e.logical_key !== selected.logical_key) {
        opts.push({ value: e.logical_key, label: e.name || e.logical_key });
      }
    });
    return opts;
  }, [items, selected]);

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
  async function saveCell(entity, field, value) {
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
  }

  /* ---- add new entity ---- */
  async function submitAdd() {
    if (readonly) return;
    const body = { ...newForm, name: (newForm.name || '').trim(), importance: parseInt(newForm.importance, 10) || 3 };
    if (!body.name) {
      window.__apiToast?.(t('scripts.edit.canon.add_required'), { kind: 'warn' });
      return;
    }
    try {
      await apiPost(body);
      setAdding(false);
      setNewForm({ logical_key: '', name: '', type: 'character', entity_subtype: '', importance: '3', summary: '' });
      setReloadTick((x) => x + 1);
      window.__apiToast?.(t('scripts.edit.canon.add_ok'), { kind: 'ok' });
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.save_fail'), { kind: 'danger', detail: e?.message });
    }
  }

  /* ---- delete ---- */
  async function doDelete(logicalKey) {
    if (readonly) return;
    try {
      await apiDelete(logicalKey);
      setItems((arr) => arr.filter((e) => e.logical_key !== logicalKey));
      if (selected?.logical_key === logicalKey) { setSelected(null); setSplitOpen(false); }
      setConfirmDelete(null);
      window.__apiToast?.(t('scripts.edit.canon.deleted'), { kind: 'ok' });
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.delete_fail'), { kind: 'danger', detail: e?.message });
    }
  }

  /* ---- detail panel save ---- */
  async function saveDetail() {
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
  }

  /* ---- children lookup ---- */
  function childrenOf(logicalKey) {
    return items.filter((e) => e.parent_logical_key === logicalKey);
  }

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

  /* inline editable cell — name */
  function CellName({ entity }) {
    const editing = editCell?.key === entity.logical_key && editCell?.field === 'name';
    if (editing) {
      return (
        <CSInput
          autoFocus
          value={editCell.value}
          onChange={({ detail }) => setEditCell((c) => ({ ...c, value: detail.value }))}
          onKeyDown={({ detail }) => {
            if (detail.key === 'Enter') saveCell(entity, 'name', editCell.value);
            if (detail.key === 'Escape') setEditCell(null);
          }}
          onBlur={() => saveCell(entity, 'name', editCell.value)}
        />
      );
    }
    return (
      <span
        style={{ cursor: readonly ? 'default' : 'text', borderBottom: readonly ? 'none' : '1px dashed var(--color-border-divider-default, #ccc)' }}
        onClick={() => !readonly && setEditCell({ key: entity.logical_key, field: 'name', value: entity.name || '' })}
      >
        {entity.name || '—'}
      </span>
    );
  }

  /* inline editable cell — importance */
  function CellImportance({ entity }) {
    const editing = editCell?.key === entity.logical_key && editCell?.field === 'importance';
    if (editing) {
      return (
        <CSSelect
          selectedOption={IMPORTANCE_OPTIONS.find((o) => o.value === String(editCell.value)) || null}
          options={IMPORTANCE_OPTIONS}
          onChange={({ detail }) => saveCell(entity, 'importance', detail.selectedOption.value)}
          onBlur={() => setEditCell(null)}
        />
      );
    }
    return (
      <span
        style={{ cursor: readonly ? 'default' : 'pointer', borderBottom: readonly ? 'none' : '1px dashed var(--color-border-divider-default, #ccc)' }}
        onClick={() => !readonly && setEditCell({ key: entity.logical_key, field: 'importance', value: String(entity.importance ?? 3) })}
      >
        {entity.importance ?? '—'}
      </span>
    );
  }

  /* inline editable cell — parent */
  function CellParent({ entity }) {
    const editing = editCell?.key === entity.logical_key && editCell?.field === 'parent_logical_key';
    const parentName = entity.parent_logical_key ? (entityMap[entity.parent_logical_key]?.name || entity.parent_logical_key) : '—';
    if (editing) {
      const curOpt = parentOptions.find((o) => o.value === (editCell.value || '')) || parentOptions[0];
      return (
        <CSSelect
          selectedOption={curOpt}
          options={parentOptions}
          onChange={({ detail }) => saveCell(entity, 'parent_logical_key', detail.selectedOption.value || null)}
          onBlur={() => setEditCell(null)}
        />
      );
    }
    return (
      <span
        style={{ cursor: readonly ? 'default' : 'pointer', borderBottom: readonly ? 'none' : '1px dashed var(--color-border-divider-default, #ccc)' }}
        onClick={() => !readonly && setEditCell({ key: entity.logical_key, field: 'parent_logical_key', value: entity.parent_logical_key || '' })}
      >
        {parentName}
      </span>
    );
  }

  /* inline delete confirmation row */
  function DeleteConfirmRow({ entity }) {
    if (confirmDelete !== entity.logical_key) {
      return (
        <CSButton
          variant="inline-link"
          iconName="remove"
          disabled={readonly}
          onClick={() => setConfirmDelete(entity.logical_key)}
        >
          {t('common.delete')}
        </CSButton>
      );
    }
    return (
      <CSSpaceBetween direction="horizontal" size="xs">
        <CSStatusIndicator type="warning">{t('scripts.edit.canon.confirm_delete')}</CSStatusIndicator>
        <CSButton variant="inline-link" iconName="check" onClick={() => doDelete(entity.logical_key)}>
          {t('common.confirm')}
        </CSButton>
        <CSButton variant="inline-link" iconName="close" onClick={() => setConfirmDelete(null)}>
          {t('common.cancel')}
        </CSButton>
      </CSSpaceBetween>
    );
  }

  /* ---- detail panel ---- */
  function DetailPanel({ entity }) {
    const children = childrenOf(entity.logical_key);
    const parent = entity.parent_logical_key ? entityMap[entity.parent_logical_key] : null;
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
              const updated = aliasTokens.filter((_, i) => i !== detail.itemIndex).map((t) => t.label);
              setDF('aliases', updated);
            }}
            i18nStrings={{ removeButtonAriaLabel: (t) => `Remove ${t.label}` }}
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
            {children.length > 0 && (
              <div style={{ paddingLeft: 32 }}>
                <CSBox fontSize="body-s" color="text-body-secondary">
                  ↓ {t('scripts.edit.canon.children')} ({children.length}):
                </CSBox>
                {children.map((ch) => (
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
            <CSButton variant="primary" loading={savingDetail} onClick={saveDetail}>
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

  /* ---- new entity add row form ---- */
  function AddEntityForm() {
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
              selectedOption={ENTITY_TYPES.map((tp) => ({ value: tp, label: t(`scripts.edit.canon.type_${tp}`) })).find((o) => o.value === newForm.type) || null}
              options={ENTITY_TYPES.map((tp) => ({ value: tp, label: t(`scripts.edit.canon.type_${tp}`) }))}
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
            <CSButton variant="primary" iconName="add-plus" onClick={submitAdd}>{t('scripts.edit.canon.add_confirm')}</CSButton>
            <CSButton variant="link" onClick={() => { setAdding(false); setNewForm({ logical_key: '', name: '', type: 'character', entity_subtype: '', importance: '3', summary: '' }); }}>
              {t('common.cancel')}
            </CSButton>
          </CSSpaceBetween>
        </div>
      </div>
    );
  }

  /* ---- column definitions ---- */
  const columns = [
    {
      id: 'name',
      header: t('scripts.edit.canon.col_name'),
      cell: (e) => <CellName entity={e} />,
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
      cell: (e) => <CellParent entity={e} />,
    },
    {
      id: 'importance',
      header: t('scripts.edit.canon.col_importance'),
      cell: (e) => <CellImportance entity={e} />,
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
            onClick={() => { setSelected(e); setDetailEdit({}); setSplitOpen(true); }}
          >
            {t('scripts.edit.canon.view_detail')}
          </CSButton>
          <DeleteConfirmRow entity={e} />
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
      items={filtered}
      trackBy="logical_key"
      selectionType="single"
      selectedItems={selected ? [selected] : []}
      onSelectionChange={({ detail }) => {
        const e = detail.selectedItems[0];
        if (e) { setSelected(e); setDetailEdit({}); setSplitOpen(true); }
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
      {adding && !readonly && <AddEntityForm />}
      <DetailDrawer
        open={splitOpen && !!selected}
        title={selected?.name || selected?.logical_key || ''}
        onClose={() => { setSelected(null); setSplitOpen(false); }}
        closeLabel={t('common.close')}
      >
        {selected && <DetailPanel entity={selected} />}
      </DetailDrawer>
      {tableEl}
    </CSSpaceBetween>
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
