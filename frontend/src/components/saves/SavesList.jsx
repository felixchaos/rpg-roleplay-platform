/* 存档列表视图 + 就地设置表单 / 分支节点列表 / 导出弹窗。
   从 pages/saves.jsx 拆出,JSX / props 流逐字节不变。 */

import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { plNavigate } from '../../router.js';
import { ResizableSplit } from '../../platform-app.jsx';
import {
  FormSection, Btn, Badge, Flashbar, useFlash,
  Field as UiField, Select as UiSelect, TextInput as UiInput,
} from '../../ui/kit.jsx';
import { NewGameModal } from './NewGame.jsx';
import { formatBytesTier } from '../../lib/format-bytes.js';
import { buildCreateSavePayload } from '../../lib/save-create-payload.js';
import CSHeader from '@cloudscape-design/components/header';
import CSTable from '@cloudscape-design/components/table';
import CSContainer from '@cloudscape-design/components/container';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import CSTabs from '@cloudscape-design/components/tabs';
import CSTextFilter from '@cloudscape-design/components/text-filter';
import CSSelect from '@cloudscape-design/components/select';
import CSModal from '@cloudscape-design/components/modal';
import CSInput from '@cloudscape-design/components/input';
import CSAlert from '@cloudscape-design/components/alert';
import CSPagination from '@cloudscape-design/components/pagination';

const _saveSortOpts = (t) => [
  { value: 'played', label: t('saves.list.sort_played') },
  { value: 'name', label: t('saves.list.sort_name') },
  { value: 'created', label: t('saves.list.sort_created') },
];

const _AWAPI = () => (window.__API_BASE || '');

/* 就地设置表单(取代「游戏设置」弹窗向导)— 一屏展示全部字段,直接 PATCH。
   建档锁死项由后端 enforce:is_create=false 时被拒,前端用 flash 提示。 */
function SaveSettingsForm({ saveId, flash }) {
  const { t } = useTranslation();
  const [schema, setSchema] = useStatePL(null);
  const [vals, setVals] = useStatePL({});
  const [init, setInit] = useStatePL({});
  const [saving, setSaving] = useStatePL(false);
  const [err, setErr] = useStatePL('');
  useEffectPL(() => {
    let c = false; setSchema(null); setErr('');
    fetch(`${_AWAPI()}/api/saves/${saveId}/settings`, { credentials: 'include' })
      .then((r) => r.json())
      .then((d) => {
        if (c) return;
        if (d.ok) {
          setSchema(d.schema);
          const v = {};
          (d.schema.fields || []).forEach((f) => { v[f.key] = (d.settings && d.settings[f.key]) ?? f.default; });
          setVals(v); setInit(v);
        } else setErr(d.error || t('saves.settings_form.load_err'));
      })
      .catch((e) => { if (!c) setErr(String(e)); });
    return () => { c = true; };
  }, [saveId]);

  if (err) return <div className="aw-empty">{t('saves.settings_form.load_fail', { err })}</div>;
  if (!schema) return <div className="aw-empty">{t('saves.settings_form.loading')}</div>;
  const fields = schema.fields || [];
  const dirty = JSON.stringify(vals) !== JSON.stringify(init);

  const save = async () => {
    // 只提交改动过的字段 — 避免把未改的锁死项(如 starting_worldline)发过去触发误报
    const changed = {};
    Object.keys(vals).forEach((k) => { if (vals[k] !== init[k]) changed[k] = vals[k]; });
    if (!Object.keys(changed).length) return;
    setSaving(true);
    try {
      const r = await fetch(`${_AWAPI()}/api/saves/${saveId}/settings`, {
        method: 'PATCH', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: changed, is_create: false }),
      }).then((x) => x.json());
      if (r.applied !== undefined) {
        setInit(vals);
        const rej = r.rejected && Object.keys(r.rejected);
        if (rej && rej.length) flash.warn(t('saves.settings_form.save_locked_warn', { fields: rej.join('/') }));
        else flash.ok(t('saves.settings_form.save_ok'));
      } else flash.err(r.error || t('saves.settings_form.save_fail'));
    } catch (e) { flash.err(String(e)); }
    setSaving(false);
  };

  return (
    <FormSection
      title={t('saves.settings_form.title')}
      description={t('saves.settings_form.description')}
      footer={<Btn variant="primary" disabled={!dirty} loading={saving} onClick={save}>{t('saves.settings_form.btn_save')}</Btn>}
    >
      {fields.map((f) => (
        <UiField key={f.key} label={f.label} hint={f.help}>
          {f.options
            ? <UiSelect value={vals[f.key]} options={f.options.map((o) => ({ value: o, label: o }))}
                onChange={(v) => setVals((p) => ({ ...p, [f.key]: v }))} />
            : <UiInput value={vals[f.key]} onChange={(v) => setVals((p) => ({ ...p, [f.key]: v }))} />}
        </UiField>
      ))}
    </FormSection>
  );
}

/* 就地分支节点列表(取代跳页 / 弹窗)。 */
function SaveBranchList({ save }) {
  const { t } = useTranslation();
  const [nodes, setNodes] = useStatePL(null);
  useEffectPL(() => {
    let c = false; setNodes(null);
    (async () => {
      try {
        const r = await window.api.branches.list(save.id);
        const activeId = r?.active_commit_id || r?.active_branch_node_id;
        const ns = (r?.nodes || r?.commits || []).map((n, i) => ({
          id: n.id,
          summary: n.summary || n.message || n.content_preview || t('saves.page.node_fallback', { id: n.id }),
          turn: n.turn_index ?? i,
          current: n.id === activeId,
        }));
        if (!c) setNodes(ns);
      } catch (_) { if (!c) setNodes([]); }
    })();
    return () => { c = true; };
  }, [save.id]);

  if (!nodes) return <div className="aw-empty">{t('saves.branches.loading')}</div>;
  if (!nodes.length) return <div className="aw-empty">{t('saves.branches.empty')}</div>;
  return (
    <FormSection title={t('saves.branches.title')} description={t('saves.branches.node_count', { n: nodes.length })}
      actions={<Btn size="sm" onClick={() => { plNavigate('saves-branches'); }}>{t('saves.branches.btn_open_tree')}</Btn>}>
      <div className="aw-rlist">
        {nodes.map((n) => (
          <div key={n.id} className="aw-rlist-item" style={{ cursor: 'default' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
              <span>{n.summary}</span>
              {n.current ? <Badge tone="ok">{t('saves.branches.current_badge')}</Badge> : <span className="aw-muted" style={{ fontSize: 12 }}>#{n.turn}</span>}
            </div>
          </div>
        ))}
      </div>
    </FormSection>
  );
}

/* ---------------------------- EXPORT BUNDLE MODAL -------------- */
function ExportBundleModal({ open, save, onClose }) {
  const { t } = useTranslation();
  const [tier, setTier] = useStatePL('no_vectors');
  const [estimate, setEstimate] = useStatePL(null);
  const [estimateLoading, setEstimateLoading] = useStatePL(false);
  const [estimateFail, setEstimateFail] = useStatePL(false);

  // fetch estimate whenever modal opens with a valid save
  useEffectPL(() => {
    if (!open || !save?.id) return;
    let cancelled = false;
    setEstimate(null); setEstimateFail(false); setEstimateLoading(true);
    fetch(`${_AWAPI()}/api/v1/saves/${save.id}/export/estimate`, { credentials: 'include' })
      .then((r) => r.json())
      .then((d) => {
        if (cancelled) return;
        if (d && d.ok !== false && d.tiers) {
          setEstimate(d);
          // use server's default_tier as preselection
          if (d.default_tier) setTier(d.default_tier);
        } else {
          setEstimateFail(true);
        }
      })
      .catch(() => { if (!cancelled) setEstimateFail(true); })
      .finally(() => { if (!cancelled) setEstimateLoading(false); });
    return () => { cancelled = true; };
  }, [open, save?.id]);

  if (!open || !save) return null;

  const _fmtBytes = (bytes) => {
    if (bytes == null) return null;
    const { tier, n } = formatBytesTier(bytes);
    return tier === 'mb' ? t('saves.detail.export_size_mb', { mb: n }) : t('saves.detail.export_size_kb', { kb: n });
  };

  const sizeLabel = (tierKey) => {
    if (estimateLoading) return t('saves.detail.export_size_loading');
    if (estimateFail || !estimate?.tiers) return t('saves.detail.export_size_fail');
    return _fmtBytes(estimate.tiers[tierKey]) ?? t('saves.detail.export_size_fail');
  };

  const defaultTier = estimate?.default_tier || 'no_vectors';

  const doDownload = () => {
    const safeName = (save.title || 'save').replace(/[^\w一-鿿-]+/g, '_');
    const a = document.createElement('a');
    a.href = `${_AWAPI()}/api/v1/saves/${save.id}/export/bundle?tier=${tier}`;
    a.download = `save-${save.id}-${safeName}-${tier}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    onClose();
  };

  const tierCards = [
    {
      key: 'no_vectors',
      labelKey: 'export_tier_standard',
      descKey: 'export_tier_standard_desc',
    },
    {
      key: 'full',
      labelKey: 'export_tier_full',
      descKey: 'export_tier_full_desc',
    },
  ];

  return (
    <CSModal
      visible
      size="medium"
      header={t('saves.detail.export_modal_title')}
      onDismiss={onClose}
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton key="cancel" variant="link" onClick={onClose}>{t('saves.detail.export_btn_cancel')}</CSButton>
            <CSButton key="download" variant="primary" iconName="download" onClick={doDownload}>{t('saves.detail.export_btn_download')}</CSButton>
          </CSSpaceBetween>
        </CSBox>
      }
    >
      <CSSpaceBetween size="m">
        {/* tier selector cards */}
        <div role="radiogroup" aria-label={t('saves.detail.export_tier_label')} style={{ display: 'grid', gap: 8 }}>
          {tierCards.map(({ key, labelKey, descKey }) => {
            const selected = tier === key;
            const isDefault = key === defaultTier;
            return (
              <label
                key={key}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '18px 1fr auto',
                  gap: 12,
                  padding: '12px 14px',
                  border: selected ? '1px solid var(--color-border-control-default, #7d8998)' : '1px solid var(--color-border-divider-default, #414d5c)',
                  borderRadius: 8,
                  cursor: 'pointer',
                  background: selected ? 'var(--color-background-item-selected, rgba(0,115,232,.1))' : 'transparent',
                  transition: 'border-color .12s, background .12s',
                  alignItems: 'start',
                }}
              >
                <input
                  type="radio"
                  name="export-tier"
                  value={key}
                  checked={selected}
                  onChange={() => setTier(key)}
                  style={{ marginTop: 2, accentColor: 'var(--color-text-accent, #0073e6)' }}
                />
                <div style={{ display: 'grid', gap: 4 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, fontSize: 14 }}>
                    {t(`saves.detail.${labelKey}`)}
                    {isDefault && (
                      <span style={{
                        fontSize: 11, padding: '1px 7px', borderRadius: 99,
                        background: 'var(--color-background-badge-green, rgba(30,160,90,.18))',
                        color: 'var(--color-text-status-success, #29ae7f)',
                        border: '1px solid rgba(30,160,90,.3)',
                        fontWeight: 600,
                      }}>
                        {t('saves.detail.export_recommended')}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: 12.5, color: 'var(--color-text-body-secondary, #8d9daf)', lineHeight: 1.5 }}>
                    {t(`saves.detail.${descKey}`)}
                  </div>
                </div>
                <div style={{ fontSize: 13, color: 'var(--color-text-body-secondary, #8d9daf)', whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums', marginTop: 1 }}>
                  {sizeLabel(key)}
                </div>
              </label>
            );
          })}
        </div>

        {/* info note */}
        <CSAlert type="info" dismissible={false}>
          {t('saves.detail.export_modal_desc')}
        </CSAlert>
      </CSSpaceBetween>
    </CSModal>
  );
}

function SavesListView() {
  const { t } = useTranslation();
  const [saves, setSaves] = useStatePL([]);
  const [scripts, setScripts] = useStatePL([]);
  const [selectedId, setSelectedId] = useStatePL(null);
  const [tab, setTab] = useStatePL('overview');
  const [createOpen, setCreateOpen] = useStatePL(false);
  const [deleteTarget, setDeleteTarget] = useStatePL(null);
  const [deleting, setDeleting] = useStatePL(false);
  const [renaming, setRenaming] = useStatePL(false);
  const [renameVal, setRenameVal] = useStatePL('');
  const [exportTarget, setExportTarget] = useStatePL(null); // save obj for bundle export modal
  const [query, setQuery] = useStatePL('');
  const [sortBy, setSortBy] = useStatePL('played'); // played | name | created
  const [savePage, setSavePage] = useStatePL(1);
  const SAVE_PAGE_SIZE = 50;
  const flash = useFlash();
  const importInputRef = React.useRef(null);

  const reload = React.useCallback(async () => {
    try {
      const r = await window.api.saves.list();
      const list = Array.isArray(r) ? r : (r?.items || r?.saves || []);
      setSaves(list.map(window.__normalizeSave || ((x) => x)));
    } catch (_) { setSaves([]); }
    try {
      const s = await window.api.scripts.list();
      const list = Array.isArray(s) ? s : (s?.items || s?.scripts || []);
      setScripts(list.map(window.__normalizeScript || ((x) => x)));
    } catch (_) { setScripts([]); }
  }, []);
  useEffectPL(() => {
    reload();
    const refresh = () => reload();
    window.addEventListener('rpg-scripts-updated', refresh);
    window.addEventListener('rpg-saves-updated', refresh);
    return () => {
      window.removeEventListener('rpg-scripts-updated', refresh);
      window.removeEventListener('rpg-saves-updated', refresh);
    };
  }, [reload]);

  // 自动选中:当前存档 → 否则第一条
  useEffectPL(() => {
    if (selectedId && saves.some((s) => s.id === selectedId)) return;
    const cur = saves.find((s) => s.current) || saves[0];
    setSelectedId(cur ? cur.id : null);
  }, [saves, selectedId]);

  const selected = saves.find((s) => s.id === selectedId) || null;
  const selScript = selected && scripts.find((sc) => sc.id === selected.script_id);

  const onCreate = async (vals) => {
    try {
      // 与平台壳 __createAndEnterSave 共用同一份字段表:以前这里手抄的白名单漏了
      // story_intent / player_origin / identity_known,从存档页新建会静默丢掉玩家的选择。
      const created = await window.api.saves.create(buildCreateSavePayload(vals, {
        defaultTitle: t('saves.page.default_save_title') + ' · ' + new Date().toLocaleString(),
        fallbackScriptId: scripts[0] && scripts[0].id,
      }));
      if (created && created.ok === false) {
        throw new Error(created.error || created.detail || t('saves.page.err_backend_rejected_create'));
      }
      flash.ok(t('saves.toast.created'));
      setCreateOpen(false);
      reload();
      try { window.dispatchEvent(new CustomEvent('rpg-saves-updated')); } catch (_) {}
      const save = created && (created.save || created);
      if (save && save.id) {
        setSelectedId(save.id);
        window.__openContinue?.({ ...save, ...window.__normalizeSave?.(save) });
      }
    } catch (e) {
      flash.err(t('saves.toast.create_fail', { err: e?.message || '' }));
      throw e; // 让 NewGameModal 接住,显示 inline 错误
    }
  };

  const onActivate = async (s) => {
    try { await window.api.saves.activate(s.id); flash.ok(t('saves.toast.activated')); reload(); }
    catch (e) { flash.err(t('saves.toast.activate_fail', { err: e?.message || '' })); }
  };
  const onImportFile = async (file) => {
    if (!file) return;
    // accept .json (legacy) and .zip (self-contained bundle)
    if (!/\.(json|zip)$/i.test(file.name || '')) {
      flash.err(t('saves.toast.import_fail', { err: t('saves.page.err_import_format') }));
      return;
    }
    if (file.size > 200 * 1024 * 1024) {
      flash.err(t('saves.toast.import_fail', { err: t('saves.page.err_import_too_large') }));
      return;
    }
    try {
      flash.info(t('saves.toast.importing', { name: file.name }));
      const r = await window.api.saves.importFile(file);
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('saves.page.err_backend_rejected_import'));
      // bundle response includes save_id/script_id/warnings
      const isBundle = r && (r.save_id != null || r.script_id != null);
      if (isBundle) {
        if (r.warnings?.length) {
          flash.warn(t('saves.toast.imported_bundle_warn', { count: r.warnings.length, first: r.warnings[0] }));
        } else {
          flash.ok(t('saves.toast.imported_bundle', { save_id: r.save_id ?? '?' }));
        }
      } else if (r?.warnings?.length) {
        flash.warn(t('saves.toast.imported_bundle_warn', { count: r.warnings.length, first: r.warnings[0] }));
      } else {
        flash.ok(t('saves.toast.imported'));
      }
      reload();
    } catch (e) { flash.err(t('saves.toast.import_fail', { err: e?.message || '' })); }
  };
  const doRename = async () => {
    const val = renameVal.trim();
    if (!val || !selected || val === selected.title) { setRenaming(false); return; }
    try {
      await window.api.saves.rename(selected.id, val);
      flash.ok(t('saves.toast.renamed')); setRenaming(false); reload();
    } catch (e) { flash.err(t('saves.toast.rename_fail', { err: e?.message || '' })); }
  };
  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await window.api.saves.remove(deleteTarget.id);
      // 乐观地从本地列表过滤掉被删档:reload() 是异步的,自动选中 effect
      // 会在旧 saves 列表上先跑一轮,若不先过滤会把已删档重新选回来。
      const removedId = deleteTarget.id;
      setSaves(prev => prev.filter(s => s.id !== removedId));
      flash.ok(t('saves.toast.deleted')); setDeleteTarget(null); setSelectedId(null); reload();
    } catch (e) { flash.err(t('saves.toast.delete_fail', { err: e?.message || '' })); }
    setDeleting(false);
  };

  // 搜索 + 排序
  const visibleSaves = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    let xs = saves;
    if (q) {
      xs = saves.filter((s) => {
        const sc = scripts.find((x) => x.id === s.script_id);
        return (s.title || '').toLowerCase().includes(q) || (sc?.title || '').toLowerCase().includes(q);
      });
    }
    const ts = (v) => (v ? new Date(v).getTime() || 0 : 0);
    const sorted = [...xs];
    if (sortBy === 'name') sorted.sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh'));
    else if (sortBy === 'created') sorted.sort((a, b) => ts(b.created_ts) - ts(a.created_ts));
    else sorted.sort((a, b) => ts(b.last_played_ts) - ts(a.last_played_ts));
    return sorted;
  }, [saves, scripts, query, sortBy]);

  // 分页切片(每页 50 条)
  const savePageCount = Math.max(1, Math.ceil(visibleSaves.length / SAVE_PAGE_SIZE));
  const pagedSaves = visibleSaves.slice((savePage - 1) * SAVE_PAGE_SIZE, savePage * SAVE_PAGE_SIZE);
  // 过滤条件变化时重置到第 1 页
  React.useEffect(() => { setSavePage(1); }, [query, sortBy]);

  const scriptTitle = (s) => (scripts.find((x) => x.id === s.script_id)?.title || t('saves.list.unknown_script'));
  const saveSortOpts = _saveSortOpts(t);

  return (
    // CSSpaceBetween 的每个 child 都需要 key(Cloudscape InternalSpaceBetween 用 flattenChildren + map 渲染,
    // 无 key 时 wrapper div key 全为 undefined → React 报 unique key warning)
    <CSSpaceBetween size="l">
      <CSHeader
        key="header"
        variant="h1"
        counter={`(${saves.length})`}
        description={t('saves.list.description')}
        actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            {/* accept .json (legacy) and .zip (self-contained bundle) — backend auto-detects */}
            <input key="upload-input" ref={importInputRef} type="file" accept=".json,.zip,application/json,application/zip" style={{ display: 'none' }}
              onChange={(e) => { onImportFile(e.target.files?.[0]); e.target.value = ''; }} />
            <CSButton key="btn-import" iconName="upload" onClick={() => importInputRef.current?.click()}>{t('saves.list.btn_import')}</CSButton>
            <CSButton key="btn-new" iconName="add-plus" onClick={() => setCreateOpen(true)}>{t('saves.list.btn_new')}</CSButton>
            <CSButton key="btn-continue" variant="primary" iconName="caret-right-filled" disabled={!saves.length}
              onClick={() => window.__openContinue?.(saves[0])}>{t('saves.list.btn_continue')}</CSButton>
          </CSSpaceBetween>
        }
      >{t('saves.list.title')}</CSHeader>

      <CSSpaceBetween key="toolbar" direction="horizontal" size="xs">
        <div key="filter" style={{ minWidth: 280 }}>
          <CSTextFilter filteringText={query} filteringPlaceholder={t('saves.list.search_placeholder')}
            onChange={({ detail }) => setQuery(detail.filteringText)} />
        </div>
        <CSSelect key="sort" selectedOption={saveSortOpts.find((o) => o.value === sortBy)}
          options={saveSortOpts} onChange={({ detail }) => setSortBy(detail.selectedOption.value)} />
      </CSSpaceBetween>

      {/* table + detail:Cloudscape SpaceBetween 在 React 18 会 flatten Fragment 导致 children 失 key,
          所以不用 Fragment 包,直接把 IIFE 返回的单一 element 加上 key */}
      {(() => {
      const savesTableEl = (
      <CSTable
        variant="container"
        selectionType="single"
        trackBy="id"
        selectedItems={selected ? [selected] : []}
        onSelectionChange={({ detail }) => { const s = detail.selectedItems[0]; if (s) { setSelectedId(s.id); setTab('overview'); setRenaming(false); } }}
        onRowClick={({ detail }) => { setSelectedId(detail.item.id); setTab('overview'); setRenaming(false); }}
        columnDefinitions={[
          { id: 'title', header: t('saves.list.col_save'), cell: (s) => <CSBox fontWeight="bold">{s.title}</CSBox> },
          { id: 'script', header: t('saves.list.col_script'), cell: (s) => scriptTitle(s) },
          { id: 'player', header: t('saves.list.col_player'), cell: (s) => s._raw?.player_name || '—' },
          { id: 'nodes', header: t('saves.list.col_nodes'), cell: (s) => s.branch_count },
          { id: 'played', header: t('saves.list.col_played'), cell: (s) => s.last_played_at },
          { id: 'status', header: t('saves.list.col_status'), cell: (s) => s.current ? <CSBadge color="green">{t('saves.list.status_active')}</CSBadge> : <CSStatusIndicator type="stopped">{t('saves.list.status_inactive')}</CSStatusIndicator> },
          { id: 'go', header: '', cell: (s) => <CSButton variant="inline-link" iconName="caret-right-filled" onClick={() => window.__openContinue?.(s)}>{t('saves.list.continue_btn')}</CSButton> },
        ]}
        items={pagedSaves}
        empty={<CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>{query ? t('saves.list.empty_filtered') : t('saves.list.empty_no_saves')}</CSBox>}
        pagination={
          savePageCount > 1
            ? <CSPagination currentPageIndex={savePage} pagesCount={savePageCount} onChange={({ detail }) => setSavePage(detail.currentPageIndex)} />
            : undefined
        }
      />
      );
      const savesDetailEl = selected ? (
        <CSContainer
          header={
            <CSHeader
              variant="h2"
              actions={!renaming &&
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSButton variant="primary" iconName="caret-right-filled" onClick={() => window.__openContinue?.(selected)}>{t('saves.detail.btn_continue')}</CSButton>
                  {!selected.current && <CSButton onClick={() => onActivate(selected)}>{t('saves.detail.btn_activate')}</CSButton>}
                  <CSButton onClick={() => { setRenameVal(selected.title); setRenaming(true); }}>{t('saves.detail.btn_rename')}</CSButton>
                  <CSButton onClick={() => setExportTarget(selected)}>{t('saves.detail.btn_export')}</CSButton>
                  <CSButton onClick={() => setDeleteTarget(selected)}>{t('saves.detail.btn_delete')}</CSButton>
                </CSSpaceBetween>
              }
            >
              {renaming
                ? <CSSpaceBetween direction="horizontal" size="xs">
                    <CSInput value={renameVal} onChange={({ detail }) => setRenameVal(detail.value)} />
                    <CSButton variant="primary" onClick={doRename}>{t('saves.detail.btn_save')}</CSButton>
                    <CSButton variant="link" onClick={() => setRenaming(false)}>{t('saves.detail.btn_cancel')}</CSButton>
                  </CSSpaceBetween>
                : selected.title}
            </CSHeader>
          }
        >
          <CSTabs
            activeTabId={tab}
            onChange={({ detail }) => setTab(detail.activeTabId)}
            tabs={[
              { id: 'overview', label: t('saves.detail.tab_overview'), content: (
                <CSSpaceBetween size="m">
                  <CSKeyValuePairs columns={4} items={[
                    { label: t('saves.detail.kv_script'), value: scriptTitle(selected) },
                    { label: t('saves.detail.kv_player'), value: selected._raw?.player_name || t('saves.detail.kv_player_unset') },
                    { label: t('saves.detail.kv_turn'), value: selected._raw?.turn != null ? t('saves.detail.kv_turn_val', { n: selected._raw.turn }) : '—' },
                    { label: t('saves.detail.kv_status'), value: selected.current ? <CSStatusIndicator type="success">{t('saves.detail.kv_status_current')}</CSStatusIndicator> : <CSStatusIndicator type="stopped">{t('saves.list.status_inactive')}</CSStatusIndicator> },
                    { label: t('saves.detail.kv_branches'), value: t('saves.detail.kv_branches_val', { n: selected.branch_count }) },
                    { label: t('saves.detail.kv_world_time'), value: selected._raw?.world_time || '—' },
                    { label: t('saves.detail.kv_played'), value: selected.last_played_at },
                    { label: t('saves.detail.kv_created'), value: selected.created_ts ? new Date(selected.created_ts).toLocaleString('zh-CN') : '—' },
                  ]} />
                  <CSBox variant="p" color="text-body-secondary">
                    {selected._raw?.snippet || selected._raw?.last_message || t('saves.detail.snippet_empty')}
                  </CSBox>
                </CSSpaceBetween>
              ) },
              { id: 'settings', label: t('saves.detail.tab_settings'), content: <SaveSettingsForm saveId={selected.id} flash={flash} /> },
              { id: 'branches', label: t('saves.detail.tab_branches'), content: <SaveBranchList save={selected} /> },
            ]}
          />
        </CSContainer>
      ) : null;
      return selected
        ? <ResizableSplit key="table-area" storageKey="saves" top={savesTableEl} bottom={savesDetailEl} />
        : React.cloneElement(savesTableEl, { key: 'table-area' });
      })()}

      <NewGameModal key="new-modal" open={createOpen} onClose={() => setCreateOpen(false)} onConfirm={onCreate} />
      <ExportBundleModal key="export-bundle-modal" open={!!exportTarget} save={exportTarget} onClose={() => setExportTarget(null)} />
      <CSModal
        key="delete-modal"
        visible={!!deleteTarget}
        header={t('saves.confirm.delete_title')}
        onDismiss={() => setDeleteTarget(null)}
        footer={
          <CSBox float="right">
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton key="cancel" variant="link" onClick={() => setDeleteTarget(null)}>{t('saves.confirm.btn_cancel')}</CSButton>
              <CSButton key="confirm" variant="primary" loading={deleting} onClick={confirmDelete}>{t('saves.confirm.btn_confirm')}</CSButton>
            </CSSpaceBetween>
          </CSBox>
        }
      >
        {deleteTarget ? t('saves.confirm.delete_body', { title: deleteTarget.title }) : ''}
      </CSModal>

      {flash.items.length > 0 && (
        <div key="flashbar" style={{ position: 'fixed', top: 64, right: 20, zIndex: 9999, maxWidth: 360 }}>
          <Flashbar items={flash.items} />
        </div>
      )}
    </CSSpaceBetween>
  );
}

export { SavesListView };
