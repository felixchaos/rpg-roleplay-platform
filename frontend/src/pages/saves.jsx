/* Saves / Branches / ContinuePicker / NewGameModal — split out of platform-app.jsx (task 52).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: Icon / ConfirmModal / BranchGraph (来自 branch-graph.jsx)。
   注意：本文件提供 NewGameModal 给 scripts.jsx 与 platform-app.jsx 共享（通过 window.NewGameModal）。 */

import React from 'react';
import { createPortal } from 'react-dom';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../game-icons.jsx';
import { plNavigate } from '../router.js';
import { ConfirmModal, useShellChrome, ResizableSplit } from '../platform-app.jsx';
import { BranchGraph } from '../branch-graph.jsx';
import { NewGameWizard } from './new-game-wizard.jsx';
import { CardSheet, CardEditFields, cardFormInit, cardFormPayload } from './cards.jsx';
import {
  PageHeader, SplitLayout, ResourceList, Tabs, FormSection,
  Btn, Badge, KeyValue, StatusIndicator, ConfirmDialog, Flashbar, useFlash,
  Field as UiField, Select as UiSelect, TextInput as UiInput,
} from '../ui/kit.jsx';
// Cloudscape 原生组件(内容迁移,统一基线对齐)
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
import CSWizard from '@cloudscape-design/components/wizard';
import CSFormField from '@cloudscape-design/components/form-field';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSSegmentedControl from '@cloudscape-design/components/segmented-control';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSAlert from '@cloudscape-design/components/alert';
import CSExpandableSection from '@cloudscape-design/components/expandable-section';
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
          summary: n.summary || n.message || n.content_preview || `节点 #${n.id}`,
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
      actions={<Btn size="sm" onClick={() => { location.hash = 'saves-branches'; }}>{t('saves.branches.btn_open_tree')}</Btn>}>
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
    const mb = bytes / (1024 * 1024);
    if (mb >= 0.1) return t('saves.detail.export_size_mb', { mb: mb < 10 ? mb.toFixed(1) : Math.round(mb) });
    const kb = bytes / 1024;
    return t('saves.detail.export_size_kb', { kb: Math.round(kb) });
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
        <div role="radiogroup" style={{ display: 'grid', gap: 8 }}>
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

/* ---------------------------- SAVES ---------------------------- */
function SavesPage({ subPage = "list" }) {
  return (
    <div className="pl-stack">
      {subPage === "branches" ? <BranchesPage /> : <SavesListView />}
    </div>
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
      const created = await window.api.saves.create({
        title: vals.title || ('新存档 · ' + new Date().toLocaleString()),
        script_id: vals.script_id || (scripts[0] && scripts[0].id),
        character_id: vals.character_id || null,
        character_kind: vals.character_kind || null,
        npc_id: vals.npc_id || null,
        new_card: vals.new_card || null,
        birthpoint: vals.birthpoint || null,
        identity: vals.identity || null,
      });
      if (created && created.ok === false) {
        throw new Error(created.error || created.detail || '后端拒绝创建');
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
      flash.err(t('saves.toast.import_fail', { err: '.json / .zip 格式才支持' }));
      return;
    }
    if (file.size > 200 * 1024 * 1024) {
      flash.err(t('saves.toast.import_fail', { err: '文件过大 (>200MB)' }));
      return;
    }
    try {
      flash.info(t('saves.toast.importing', { name: file.name }));
      const r = await window.api.saves.importFile(file);
      if (r && r.ok === false) throw new Error(r.error || r.detail || '后端拒绝导入');
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

/* ---------------------------- BRANCHES ------------------------- */
const BRANCH_DATA = {
  nodes: [
    { id: 1, x: 80, y: 280, summary: "开场 · 渡海前夜", role: "root", current: false, branch: 0 },
    { id: 2, x: 240, y: 280, summary: "登船后向船工打听", role: "round", branch: 0 },
    { id: 3, x: 400, y: 240, summary: "申时落岸 · 雾未散", role: "round", branch: 0 },
    { id: 4, x: 400, y: 360, summary: "选择借宿渔家旅店", role: "round", branch: 1 },
    { id: 5, x: 560, y: 240, summary: "码头听闻浮尸三具", role: "round", branch: 0 },
    { id: 6, x: 560, y: 360, summary: "旅店遇沈知微", role: "round", branch: 1 },
    { id: 7, x: 720, y: 200, summary: "向税吏隐藏身份", role: "round", branch: 0, current: true, lastExit: true },
    { id: 8, x: 720, y: 320, summary: "暴露残页 · 被巡检盘问", role: "round", branch: 2, deleted: true },
    { id: 9, x: 720, y: 420, summary: "天黑前赶往灯塔", role: "round", branch: 1 },
    { id: 10, x: 880, y: 200, summary: "灯塔下等沈知微", role: "round", branch: 0 },
    { id: 11, x: 880, y: 420, summary: "找到守人女儿阿衡", role: "round", branch: 1 },
  ],
  edges: [
    { from: 1, to: 2, branch: 0 }, { from: 2, to: 3, branch: 0 }, { from: 2, to: 4, branch: 1 },
    { from: 3, to: 5, branch: 0 }, { from: 4, to: 6, branch: 1 },
    { from: 5, to: 7, branch: 0 }, { from: 5, to: 8, branch: 2, deleted: true },
    { from: 6, to: 9, branch: 1 },
    { from: 7, to: 10, branch: 0 }, { from: 9, to: 11, branch: 1 },
  ],
};

const BRANCH_LABELS = {
  0: { name: "主线", desc: "向税吏隐藏身份，灯塔会面" },
  1: { name: "旅店线", desc: "借宿渔家，最早遇到阿衡" },
  2: { name: "暴露线", desc: "残页被巡检发现（已删除）", deleted: true },
};

function BranchesPage() {
  const { t } = useTranslation();
  // 用户要求"git ui 在 vscode 底部终端里的那个" — 改用 BranchGraph 组件 (VSCode Git Graph 风格)。
  // 旧版是自由拖拽 SVG (140×40 矩形 + 贝塞尔曲线),信息密度低、交互复杂、不像 git tool。
  // 新版用 swimlane 算法:每行一个 commit,左侧固定 column 分支线,右侧 message + ref pills + 操作。
  //
  // 后端不变(branch_commits + branch_refs);组件抽到 frontend/src/branch-graph.jsx,
  // 游戏内右侧 BranchTreeRail 和这里共用,只换 variant prop (compact / full)。

  const [saves, setSaves] = useStatePL([]);
  const [selectedSave, setSelectedSave] = useStatePL(undefined);
  const [savesLoaded, setSavesLoaded] = useStatePL(false);
  const [treePayload, setTreePayload] = useStatePL(null);  // {nodes, refs, active_commit_id}
  const [treeLoading, setTreeLoading] = useStatePL(false);
  const [treeError, setTreeError] = useStatePL("");
  const [selectedNodeId, setSelectedNodeId] = useStatePL(null);
  const [deleteTarget, setDeleteTarget] = useStatePL(null);

  // 1) 拉用户的 saves 列表
  useEffectPL(() => {
    (async () => {
      try {
        const r = await window.api.saves.list();
        const list = Array.isArray(r) ? r : (r?.items || r?.saves || []);
        const normalized = list.map(window.__normalizeSave || ((x) => x));
        setSaves(normalized);
        if (normalized.length) {
          setSelectedSave(prev => (
            prev && normalized.some(s => s.id === prev) ? prev : normalized[0].id
          ));
        } else {
          setSelectedSave(undefined);
        }
      } catch (_) {
        setSaves([]);
        setSelectedSave(undefined);
      } finally {
        setSavesLoaded(true);
      }
    })();
  }, []);

  // 2) selectedSave 变 → 拉该存档的 branch tree
  const reloadTree = async () => {
    if (!selectedSave) { setTreePayload(null); return; }
    setTreeLoading(true); setTreeError("");
    try {
      const r = await window.api.branches.list(selectedSave);
      setTreePayload(r ? {
        nodes: r.nodes || r.commits || [],
        refs: r.refs || [],
        active_commit_id: r.active_commit_id || r.active_branch_node_id || null,
      } : null);
    } catch (e) {
      setTreeError(e?.message || t('saves.branches.load_fail', { err: '' }));
      setTreePayload(null);
    } finally {
      setTreeLoading(false);
    }
  };
  useEffectPL(() => { reloadTree(); }, [selectedSave]);

  const onActivate = async (commitId) => {
    try {
      await window.api.branches.activate({ save_id: selectedSave, commit_id: commitId, node_id: commitId });
      window.__apiToast?.(t('saves.branches.toast_activated'), { kind: "ok" });
      reloadTree();
    } catch (e) {
      window.__apiToast?.(t('saves.branches.toast_activate_fail'), { kind: "danger", detail: e?.message });
    }
  };

  const onContinue = (commitId) => {
    window.__openContinue?.(saves.find(s => s.id === selectedSave), commitId);
  };

  const onDeleteRequest = (commitId) => {
    const node = (treePayload?.nodes || []).find(n => (n.commit_id ?? n.id) === commitId);
    if (node) setDeleteTarget(node);
  };

  const onDeleteConfirmed = async () => {
    if (!deleteTarget) return;
    const cid = deleteTarget.commit_id ?? deleteTarget.id;
    try {
      await window.api.branches.delete({ save_id: selectedSave, node_id: cid, commit_id: cid });
      window.__apiToast?.(t('saves.branches.toast_deleted'), { kind: "ok" });
      setDeleteTarget(null);
      reloadTree();
    } catch (e) {
      window.__apiToast?.(t('saves.branches.toast_delete_fail'), { kind: "danger", detail: e?.message });
    }
  };

  // 空态:用户没有任何存档
  if (savesLoaded && saves.length === 0) {
    return (
      <div className="pl-stack">
        <section className="pl-sec" data-cap-anchor="saves.branches">
          <div className="pl-sec-head">
            <h2>{t('saves.branches.page_title')} <span className="muted-2">{t('saves.branches.no_saves_title')}</span></h2>
          </div>
          <div className="pl-empty" style={{padding: "32px 24px", textAlign: "center", color: "var(--muted)"}}>
            <div style={{marginBottom: 12, fontFamily: "var(--font-serif)", fontSize: 15, color: "var(--text)"}}>
              {t('saves.branches.no_saves_body')}
            </div>
            <div style={{marginBottom: 16, fontSize: 13}}>
              {t('saves.branches.no_saves_hint')}
            </div>
            <div style={{display: "inline-flex", gap: 8}}>
              <button className="btn primary" onClick={() => plNavigate("scripts")}>
                <Icon name="bookmark" size={12} /> {t('saves.branches.no_saves_btn_scripts')}
              </button>
              <button className="btn ghost" onClick={() => window.location.hash = "saves-list"}>
                <Icon name="list" size={12} /> {t('saves.branches.no_saves_btn_list')}
              </button>
            </div>
          </div>
        </section>
      </div>
    );
  }

  const nodeCount = (treePayload?.nodes || []).length;
  const refCount = (treePayload?.refs || []).length;

  return (
    <div className="pl-stack">
      <section className="pl-sec" data-cap-anchor="saves.branches">
        <div className="pl-sec-head">
          <h2>
            {t('saves.branches.page_title')}{" "}
            <span className="muted-2">
              {t('saves.branches.page_subtitle', { commits: nodeCount, refs: refCount })}
            </span>
          </h2>
          <div className="pl-sec-tools">
            <select value={selectedSave || ""} onChange={(e) => setSelectedSave(Number(e.target.value))}
              style={{height: 28, fontSize: 12, padding: "0 10px"}}>
              {saves.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
            </select>
            <button className="btn ghost" onClick={reloadTree}><Icon name="refresh" size={12} /> {t('saves.branches.btn_refresh')}</button>
            <button className="btn primary"
              disabled={!selectedSave}
              onClick={() => window.__openContinue?.(saves.find(s => s.id === selectedSave))}>
              <Icon name="play" size={12} /> {t('saves.branches.btn_enter')}
            </button>
          </div>
        </div>
        <div style={{padding: "8px 0"}}>
          {treeLoading && (
            <div className="muted-2" style={{padding: "16px", fontSize: 12.5}}>{t('saves.branches.loading_tree')}</div>
          )}
          {!treeLoading && treeError && (
            <div className="muted-2" style={{padding: "16px", fontSize: 12.5, color: "var(--danger)"}}>{t('saves.branches.load_fail', { err: treeError })}</div>
          )}
          {!treeLoading && !treeError && treePayload && (
            <BranchGraph
              data={treePayload}
              variant="full"
              selectedId={selectedNodeId}
              onSelect={setSelectedNodeId}
              onActivate={onActivate}
              onContinue={onContinue}
              onDelete={onDeleteRequest}
            />
          )}
        </div>
        <div className="muted-2" style={{padding: "6px 4px 0", fontSize: 11, fontFamily: "var(--font-mono)"}}>
          {t('saves.branches.legend')}
        </div>
      </section>
      <ConfirmModal
        open={!!deleteTarget}
        title={t('saves.branches.delete_title', { id: deleteTarget?.commit_id ?? deleteTarget?.id })}
        body={
          <>
            {t('saves.branches.delete_body_suffix')} <strong>{deleteTarget?.summary || deleteTarget?.message || t('saves.branches.delete_body_node', { id: deleteTarget?.commit_id ?? deleteTarget?.id })}</strong>
            {" "}
            {t('saves.branches.delete_body_irrev')}
            <div style={{marginTop: 8, fontSize: 12, color: "var(--muted)"}}>POST /api/branches/delete</div>
          </>
        }
        danger confirmLabel={t('saves.branches.delete_confirm_label')}
        onClose={() => setDeleteTarget(null)}
        onConfirm={onDeleteConfirmed}
      />
    </div>
  );
}

/* ---------------------------- CONTINUE PICKER ------------------ */
function ContinuePicker({ open, save, focusedNodeId, onClose }) {
  const { t } = useTranslation();
  // task 45：原来 allSaves = window.MOCK_PLATFORM.saves —— 登录用户看不到自己的真存档
  // （只看到 mock 的 4 条假 save id=11/12/13/14）。改用 /api/saves 实时拉真存档。
  // 匿名访客（designer preview）才回退到 MOCK_PLATFORM。
  const [allSaves, setAllSaves] = useStatePL([]);
  const [savesLoading, setSavesLoading] = useStatePL(false);
  const [branchTree, setBranchTree] = useStatePL(null);  // task 45：真实分支树 / null=未加载
  const [branchLoading, setBranchLoading] = useStatePL(false);
  const [step, setStep] = useStatePL("save"); // save | branch | new
  const [pickedSave, setPickedSave] = useStatePL(null);
  const [newOpen, setNewOpen] = useStatePL(false);

  // 拉真实 saves
  React.useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setSavesLoading(true);
    (async () => {
      let list = [];
      try {
        const r = await window.api.saves.list();
        list = Array.isArray(r) ? r : (r?.items || r?.saves || []);
      } catch (_) {
        // 匿名访客 / 401：回退到 mock 保留 designer offline preview
        list = (window.RPG_AUTH && window.RPG_AUTH.authed) ? [] : (window.MOCK_PLATFORM?.saves || []);
      }
      if (cancelled) return;
      setAllSaves(list);
      setSavesLoading(false);
      if (save) { setPickedSave(save); setStep("branch"); }
      else if (list.length) { setPickedSave(list[0]); setStep("save"); }
      else { setPickedSave(null); setStep("save"); }
    })();
    return () => { cancelled = true; };
  }, [open, save]);

  // 拉真实 branch tree
  React.useEffect(() => {
    if (!open || !pickedSave?.id) { setBranchTree(null); return; }
    let cancelled = false;
    setBranchLoading(true);
    (async () => {
      let tree = null;
      try {
        const r = await window.api.branches.list(pickedSave.id);
        // 后端真相源是 user_runtime.active_commit_id (改后的 tree() 已经透传)
        const activeId = r?.active_commit_id || r?.active_branch_node_id;
        const nodes = (r?.nodes || r?.commits || []).map((n, i) => {
          // ref_names 是后端 tree() 给的真实分支指针名 ["refs/heads/main", "refs/runtime/user-6"]
          const refNames = Array.isArray(n.ref_names) ? n.ref_names : [];
          // 截短显示 (refs/heads/main → main)
          const shortRefs = refNames.map(rn => {
            const s = String(rn);
            return s.startsWith("refs/") ? s.split("/").slice(2).join("/") : s;
          });
          // 主分支判定:有 main / master ref 算主线;否则用 ref 名
          const isMain = shortRefs.includes("main") || shortRefs.includes("master");
          const branchLabel = shortRefs.length
            ? (isMain ? "main" : shortRefs[0])
            : "(无 ref)";
          return {
            id: n.id,
            summary: n.summary || n.message || n.content_preview || `节点 #${n.id}`,
            turn_index: n.turn_index ?? i,
            kind: n.kind || "round",
            ref_names: refNames,    // 完整 ref 名(用于 hover tooltip)
            short_refs: shortRefs,  // 截短的 ref 名 list
            branch_label: branchLabel,  // 显示的主标签
            current: n.id === activeId,
            lastExit: n.id === activeId,
          };
        });
        tree = { nodes, edges: [] };
      } catch (_) { tree = { nodes: [], edges: [] }; }
      if (cancelled) return;
      setBranchTree(tree);
      setBranchLoading(false);
    })();
    return () => { cancelled = true; };
  }, [open, pickedSave?.id]);

  // task 45：BRANCH_DATA 已退役 —— 真实树为空就显示空态（"新账号还没存档/还没存任何分支节点"），
  // 不再回退到 mock 11 节点
  const nodes = branchTree?.nodes || [];
  const edges = branchTree?.edges || [];
  const lastExit = nodes.find(n => n.lastExit) || nodes[0];
  const childCount = (nodeId) => edges.filter(e => e.from === nodeId).length;
  const initialPick = focusedNodeId || lastExit?.id;
  const [pickedNode, setPickedNode] = useStatePL(initialPick);
  React.useEffect(() => { if (open) setPickedNode(initialPick); }, [open, initialPick]);

  if (!open) return null;

  const picked = nodes.find(n => n.id === pickedNode);
  const isFork = picked && childCount(picked.id) > 0;
  // task 30 + 关键 bug 修复:进入 Game Console 之前必须把 runtime 切到正确的
  // **commit**(不只是 save)。
  //
  // 旧版只调 saves.activate(targetId) — 这只切 save 级 active,后端会按
  // game_saves.active_commit_id 加载该 save 当前活跃的 commit,**完全忽略用户
  // 选的 pickedNode**。结果:
  //   · 用户在第 2 步选了 #13"扎兹巴鲁姆..."节点 (柏林剧情中段),
  //   · saves.activate 把 save 级切到"当前自动存档",但 active_commit_id 还是
  //     #15 末尾(或别的 commit),
  //   · 进 Game Console 看到的是末尾 commit 的 state — 可能是混乱的旧 runtime
  //     (如 ash_mine 内容)而非用户选的 #13 柏林剧情。
  //
  // 修复:如果用户在树里选了具体节点,改调 branches.activate({node_id}) —
  // 这会同时:
  //   1. _set_save_active 写 game_saves.active_commit_id = pickedNode
  //   2. _write_checkout 写 runtime_checkouts
  //   3. runtime.activate_state_snapshot 把 user_runtime 切到 pickedNode +
  //      该 commit 的 state_snapshot
  // 这才是 git "checkout commit_id" 的语义。
  // 没选具体节点(只切了 save 没选 commit)→ fallback 到 saves.activate。
  const confirm = async () => {
    const targetSaveId = pickedSave?.id;
    if (!targetSaveId) {
      // 完全没存档信息,不要带着旧 runtime 进 Game Console
      window.__apiToast?.(t('saves.toast.no_target_save'), { kind: "danger", duration: 2400 });
      return;
    }
    try {
      if (pickedNode != null && pickedNode !== "") {
        // 用户选了具体 commit:走 commit 级 activate,把 runtime 切到该节点 state
        const r = await window.api.branches.activate({
          node_id: pickedNode,
          commit_id: pickedNode,
        });
        if (r && r.ok === false) {
          throw new Error(r.error || r.detail || "commit 级激活失败");
        }
      } else {
        // 只选了 save 没选节点:fallback save 级 activate (切到该 save 的当前 active commit)
        await window.api.saves.activate(targetSaveId);
      }
    } catch (e) {
      window.__apiToast?.(t('saves.toast.branch_activate_fail'), { kind: "danger", detail: e?.message, duration: 3000 });
      return;  // 不要带着旧 runtime 进去
    }
    location.href = "Game Console.html";
  };

  // STEP 1: Save selection
  if (step === "save") {
    return (
      <div className="pl-modal-backdrop" onClick={onClose}>
        <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(620px, 100%)"}}>
          <header className="pl-modal-head">
            <div>
              <div className="pl-modal-eyebrow">{t('saves.continue.step1_eyebrow')}</div>
              <h2 className="pl-modal-title">{t('saves.continue.step1_title')}</h2>
            </div>
            <button className="iconbtn" onClick={onClose} data-tip={t('saves.continue.close_tip')}><Icon name="close" size={14} /></button>
          </header>
          <div className="pl-save-picker">
            {savesLoading && (
              <div className="muted-2" style={{padding: "20px 12px", textAlign: "center", fontSize: 13}}>
                {t('saves.continue.loading_saves')}
              </div>
            )}
            {!savesLoading && allSaves.length === 0 && (
              <div className="muted-2" style={{padding: "20px 12px", textAlign: "center", fontSize: 13, lineHeight: 1.7}}>
                {t('saves.continue.no_saves')}
              </div>
            )}
            {allSaves.map(s => (
              <button key={s.id}
                className={`pl-save-pick-row ${pickedSave?.id === s.id ? "active" : ""}`}
                onClick={() => setPickedSave(s)}
                onDoubleClick={() => { setPickedSave(s); setStep("branch"); }}>
                <div className={`pl-radio ${pickedSave?.id === s.id ? "on" : ""}`} />
                <div className="pl-save-pick-body">
                  <div className="pl-save-pick-title">
                    {s.title}
                    {s.current && <span className="pill accent" style={{marginLeft: 8, fontSize: 10.5}}><span className="dot accent pulse" /> {t('saves.continue.playing_pill')}</span>}
                  </div>
                  <div className="pl-save-pick-meta muted-2 mono">
                    {t('saves.continue.node_meta', { n: s.branch_count, date: s.updated_at })}
                  </div>
                </div>
              </button>
            ))}
            <button className="pl-save-pick-row pl-save-pick-new"
              onClick={() => setNewOpen(true)}>
              <div className="pl-save-pick-mark"><Icon name="plus" size={14} /></div>
              <div className="pl-save-pick-body">
                <div className="pl-save-pick-title">{t('saves.continue.new_game_title')}</div>
                <div className="pl-save-pick-meta muted-2">{t('saves.continue.new_game_desc')}</div>
              </div>
              <Icon name="chevron_right" size={14} style={{color: "var(--muted-2)"}} />
            </button>
          </div>
          <footer className="pl-modal-foot">
            <span className="muted-2" style={{fontSize: 11.5}}>
              <Icon name="info" size={11} /> {t('saves.continue.hint_dblclick')}
            </span>
            <div style={{display: "flex", gap: 8}}>
              <button className="btn ghost" onClick={onClose}>{t('saves.continue.btn_cancel')}</button>
              <button className="btn primary" onClick={() => setStep("branch")} disabled={!pickedSave}>
                {t('saves.continue.btn_next')} <Icon name="arrow_right" size={12} />
              </button>
            </div>
          </footer>
          <NewGameModal
            open={newOpen}
            onClose={() => setNewOpen(false)}
            // Codex P0-1 修复:之前 onConfirm 把 payload 丢了 → 用户填的剧本 / 角色卡
            // 信息没生效,关闭 modal 后直接 confirm() 激活旧 save,看着像"开始新游戏"
            // 实际是继续当前存档。现在走统一原子流:saves.create → activate → 进游戏。
            onConfirm={async (payload) => {
              await window.__createAndEnterSave(payload);
              // 成功会跳页 (location.href),不会执行到下面
            }}
          />
        </div>
      </div>
    );
  }

  // STEP 2: Branch / node selection
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(640px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">
              <button className="pl-back-btn" onClick={() => setStep("save")} data-tip={t('saves.continue.step2_back_tip')}>
                <Icon name="chevron_left" size={11} /> {t('saves.continue.step2_back')}
              </button>
            </div>
            <h2 className="pl-modal-title">{pickedSave?.title || t('saves.continue.step2_fallback_title')}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip={t('saves.continue.close_tip')}><Icon name="close" size={14} /></button>
        </header>

        {/* task 45：真分支树。loading 时显示加载提示；空时显示空态（新账号还没存档的常见情况） */}
        {branchLoading && (
          <div className="muted-2" style={{padding: "20px 24px", textAlign: "center", fontSize: 13}}>
            {t('saves.continue.loading_branches')}
          </div>
        )}
        {!branchLoading && nodes.length === 0 && (
          <div className="muted-2" style={{padding: "32px 24px", textAlign: "center", fontSize: 13, lineHeight: 1.7}}>
            {t('saves.continue.no_branch_nodes')}<br />
            <span className="muted">{t('saves.continue.no_branch_hint')}</span>
          </div>
        )}
        {!branchLoading && lastExit && (
          <button className={`pl-modal-hero ${pickedNode === lastExit.id ? "active" : ""}`}
                  onClick={() => setPickedNode(lastExit.id)} style={{textAlign: "left"}}>
            <div className="pl-modal-hero-mark">
              <span className="dot accent pulse" />
              <span className="mono">{t('saves.continue.last_exit_label')}</span>
            </div>
            <div className="pl-modal-hero-body">
              <div className="pl-modal-hero-title">{t('saves.continue.branch_label', { branch: lastExit.branch })} · {BRANCH_LABELS[lastExit.branch]?.name || t('saves.continue.branch_default')}</div>
              <div className="pl-modal-hero-summary serif">#{String(lastExit.id).padStart(2,"00")} · {lastExit.summary}</div>
              <div className="pl-modal-hero-meta muted-2 mono">turn {lastExit.turn_index ?? "?"} · {lastExit.kind || "round"}</div>
            </div>
            <div className="pl-modal-hero-radio">
              <div className={`pl-radio ${pickedNode === lastExit.id ? "on" : ""}`} />
            </div>
          </button>
        )}

        {!branchLoading && nodes.length > 1 && (
          <div className="pl-modal-section-label">{t('saves.continue.more_nodes_label')} <span className="muted-2" style={{marginLeft: 6, fontSize: 11, textTransform: "none", letterSpacing: 0}}>{t('saves.continue.more_nodes_hint')}</span></div>
        )}

        <div className="pl-modal-branches">
          {nodes.filter(n => n.id !== lastExit?.id && !n.deleted).map(n => {
            const hasChildren = childCount(n.id) > 0;
            return (
              <button key={n.id}
                className={`pl-modal-branch ${pickedNode === n.id ? "active" : ""}`}
                onClick={() => setPickedNode(n.id)}>
                <div className={`pl-radio ${pickedNode === n.id ? "on" : ""}`} />
                <div className="pl-modal-branch-body">
                  <div className="pl-modal-branch-title">
                    #{String(n.id).padStart(2, "0")} · {n.summary}
                    {hasChildren && (
                      <span className="pill" data-tip={t('saves.continue.fork_tip')} style={{marginLeft: 8, fontSize: 10.5, color: "var(--warn)", borderColor: "rgba(212, 179, 102, 0.32)", background: "var(--warn-soft)"}}>
                        <Icon name="fork" size={9} /> {t('saves.continue.fork_pill')}
                      </span>
                    )}
                  </div>
                  <div className="pl-modal-branch-desc">
                    {n.short_refs && n.short_refs.length > 0 ? (
                      <>
                        {n.short_refs.map((rn, i) => (
                          <span key={i} className="pill" style={{
                            marginRight: 6, fontSize: 10.5,
                            color: rn === "main" || rn === "master" ? "var(--accent)" : "var(--info)",
                            borderColor: "var(--line)",
                          }} title={n.ref_names?.[i] || rn}>
                            {n.current ? "HEAD → " : ""}{rn}
                          </span>
                        ))}
                        {n.turn_index != null && (
                          <span className="muted-2 mono" style={{fontSize: 10.5}}>turn {n.turn_index}</span>
                        )}
                      </>
                    ) : (
                      <span className="muted-2 mono" style={{fontSize: 10.5}}>
                        {n.kind === "root" ? t('saves.continue.save_root') : `turn ${n.turn_index}`}
                      </span>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} />{" "}
            {isFork
              ? t('saves.continue.info_fork', { id: String(picked.id).padStart(2, "0") })
              : t('saves.continue.info_continue', { id: String(picked?.id || 0).padStart(2, "0") })}
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={() => setStep("save")}>{t('saves.continue.btn_prev')}</button>
            <button className="btn primary" onClick={confirm} disabled={pickedNode == null}>
              <Icon name="play" size={12} /> {isFork ? t('saves.continue.btn_fork') : t('saves.continue.btn_continue')}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

/* =====================================================================
   NEW GAME WIZARD  (4-step)
   Step 1: 存档名称 + 剧本
   Step 2: 角色卡
   Step 3: 出生点 (按 phase 分组)
   Step 4: 初始身份 (LLM 推荐 + 自定义)
   ===================================================================== */

/* --- mock birthpoints (backend not yet available) --- */
const MOCK_BIRTHPOINTS_PHASES = [
  {
    phase_label: "初期穿越与火星线",
    chapter_min: 1, chapter_max: 299, chapter_count: 255,
    summary: "主角穿越初期，身份混乱，火星阴谋渐浮水面。",
    anchors: [
      { anchor_id: 1001, story_time_label: "初次睁眼", chapter_min: 1, chapter_max: 1, chapter_count: 1, sample_summary: "穿越者第一次在异世界睁开眼睛，一切尚未展开。" },
      { anchor_id: 1002, story_time_label: "宫廷初入", chapter_min: 8, chapter_max: 12, chapter_count: 5, sample_summary: "初次踏入皇宫，身份尚未明确，诸方势力窥探。" },
      { anchor_id: 1003, story_time_label: "火星密谋曝光", chapter_min: 40, chapter_max: 55, chapter_count: 16, sample_summary: "第一条涉及火星的线索浮现，主角卷入阴谋漩涡。" },
      { anchor_id: 1004, story_time_label: "第一次逃亡", chapter_min: 88, chapter_max: 92, chapter_count: 5, sample_summary: "形势急转直下，主角不得不出逃皇都。" },
      { anchor_id: 1005, story_time_label: "结盟关键人物", chapter_min: 150, chapter_max: 160, chapter_count: 11, sample_summary: "主角与关键盟友达成协议，局势暂时稳定。" },
    ],
  },
  {
    phase_label: "权力博弈中期",
    chapter_min: 300, chapter_max: 699, chapter_count: 400,
    summary: "各方势力明争暗斗，主角逐渐掌握更多筹码。",
    anchors: [
      { anchor_id: 2001, story_time_label: "摄政风波", chapter_min: 302, chapter_max: 310, chapter_count: 9, sample_summary: "摄政王势力与皇族正面交锋，朝堂动荡。" },
      { anchor_id: 2002, story_time_label: "秘密组织现身", chapter_min: 380, chapter_max: 395, chapter_count: 16, sample_summary: "隐藏在幕后的秘密组织第一次正式出手。" },
      { anchor_id: 2003, story_time_label: "关键背叛", chapter_min: 450, chapter_max: 455, chapter_count: 6, sample_summary: "信任之人倒戈，主角陷入孤立无援的困境。" },
      { anchor_id: 2004, story_time_label: "反击开始", chapter_min: 510, chapter_max: 530, chapter_count: 21, sample_summary: "主角积蓄力量完毕，全面反击开始。" },
      { anchor_id: 2005, story_time_label: "中期决战", chapter_min: 650, chapter_max: 660, chapter_count: 11, sample_summary: "双方兵力正面碰撞，局势出现根本性转变。" },
    ],
  },
  {
    phase_label: "星际危机爆发",
    chapter_min: 700, chapter_max: 1199, chapter_count: 500,
    summary: "星际殖民地局势失控，地球与火星矛盾激化。",
    anchors: [
      { anchor_id: 3001, story_time_label: "殖民地叛乱", chapter_min: 705, chapter_max: 715, chapter_count: 11, sample_summary: "火星第三殖民地宣告独立，引发连锁反应。" },
      { anchor_id: 3002, story_time_label: "舰队集结", chapter_min: 800, chapter_max: 820, chapter_count: 21, sample_summary: "地球联合政府派遣大规模舰队前往镇压。" },
      { anchor_id: 3003, story_time_label: "太空会战", chapter_min: 950, chapter_max: 975, chapter_count: 26, sample_summary: "双方舰队在火星轨道外展开史诗级对决。" },
      { anchor_id: 3004, story_time_label: "生化武器事件", chapter_min: 1050, chapter_max: 1060, chapter_count: 11, sample_summary: "神秘生化武器被引爆，局势急剧恶化。" },
      { anchor_id: 3005, story_time_label: "停火谈判", chapter_min: 1150, chapter_max: 1165, chapter_count: 16, sample_summary: "各方被迫坐上谈判桌，利益重新分配。" },
    ],
  },
  {
    phase_label: "终局与清算",
    chapter_min: 1200, chapter_max: 1599, chapter_count: 400,
    summary: "所有伏线汇聚，主角做出最终抉择，历史走向改变。",
    anchors: [
      { anchor_id: 4001, story_time_label: "真相揭露", chapter_min: 1205, chapter_max: 1215, chapter_count: 11, sample_summary: "穿越背后的真实原因终于浮出水面。" },
      { anchor_id: 4002, story_time_label: "大清算前夜", chapter_min: 1320, chapter_max: 1325, chapter_count: 6, sample_summary: "各方势力在最终对决前夕静待时机。" },
      { anchor_id: 4003, story_time_label: "最终决战", chapter_min: 1450, chapter_max: 1480, chapter_count: 31, sample_summary: "决定世界命运的终极战役全面爆发。" },
      { anchor_id: 4004, story_time_label: "新秩序建立", chapter_min: 1550, chapter_max: 1570, chapter_count: 21, sample_summary: "旧世界崩塌，新的权力格局逐渐成形。" },
      { anchor_id: 4005, story_time_label: "尾声时间线", chapter_min: 1595, chapter_max: 1599, chapter_count: 5, sample_summary: "时间线最末端，所有人物迎来各自结局。" },
    ],
  },
  {
    phase_label: "番外与支线",
    chapter_min: 1600, chapter_max: 1699, chapter_count: 100,
    summary: "脱离主线的独立故事，探索配角与平行世界。",
    anchors: [
      { anchor_id: 5001, story_time_label: "配角外传·序", chapter_min: 1601, chapter_max: 1605, chapter_count: 5, sample_summary: "从主要配角视角重述关键事件。" },
      { anchor_id: 5002, story_time_label: "平行宇宙节点", chapter_min: 1630, chapter_max: 1640, chapter_count: 11, sample_summary: "如果关键选择不同，历史将走向何方？" },
      { anchor_id: 5003, story_time_label: "后日谈·五年后", chapter_min: 1680, chapter_max: 1690, chapter_count: 11, sample_summary: "五年后的世界，人们如何与历史和解。" },
    ],
  },
];

/* --- Wizard step progress bar --- */
function WizardProgress({ step, total }) {
  return (
    <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
      {Array.from({ length: total }, (_, i) => (
        <div
          key={i}
          style={{
            height: 3,
            flex: 1,
            borderRadius: 99,
            background: i < step ? "var(--accent)" : i === step ? "var(--accent-edge)" : "var(--line)",
            transition: "background 0.2s",
          }}
        />
      ))}
      <span className="muted-2" style={{ fontSize: 11, whiteSpace: "nowrap", marginLeft: 4 }}>
        {step + 1} / {total}
      </span>
    </div>
  );
}

/* --- Inline error bar --- */
function InlineErr({ msg }) {
  if (!msg) return null;
  return (
    <div role="alert" style={{
      color: "var(--danger)", padding: "8px 10px",
      border: "1px solid var(--danger-soft)", borderRadius: 6,
      fontSize: 12.5, background: "var(--danger-soft)",
    }}>
      {msg}
    </div>
  );
}

/* ============================================================
   Step 3: 出生点选择
   ============================================================ */
function BirthpointStep({ scriptId, birthpoint, setBirthpoint }) {
  const { t } = useTranslation();
  const [phases, setPhases] = React.useState([]);
  const [loadingBP, setLoadingBP] = React.useState(true);
  const [bpErr, setBpErr] = React.useState("");
  const [bpEmpty, setBpEmpty] = React.useState(false);
  const [openPhase, setOpenPhase] = React.useState(null); // accordion state

  const fetchBirthpoints = React.useCallback(() => {
    if (!scriptId) return;
    setLoadingBP(true); setBpErr(""); setBpEmpty(false);
    (async () => {
      try {
        const r = await fetch(
          `${window.__API_BASE || ""}/api/scripts/${scriptId}/birthpoints`,
          { credentials: "include", headers: { Accept: "application/json" } }
        );
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        if (data && Array.isArray(data.phases) && data.phases.length > 0) {
          setPhases(data.phases);
          // auto-open first phase
          setOpenPhase(data.phases[0].phase_label);
        } else {
          // backend returned empty — show empty state, do not fall back to mock
          setPhases([]);
          setBpEmpty(true);
        }
      } catch (_) {
        // fetch failed — show empty state, do not fall back to mock
        setPhases([]);
        setBpEmpty(true);
      } finally {
        setLoadingBP(false);
      }
    })();
  }, [scriptId]);

  React.useEffect(() => { fetchBirthpoints(); }, [fetchBirthpoints]);

  if (loadingBP) {
    return (
      <div className="muted" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, padding: "16px 0" }}>
        <Icon name="spinner" size={13} className="spin" /> {t('saves.birthpoint.loading')}
      </div>
    );
  }

  if (bpEmpty) {
    return (
      <div style={{ textAlign: "center", padding: "20px 0" }}>
        <p style={{ color: "var(--text-status-inactive, var(--muted))", marginBottom: 6 }}>
          {t('saves.new_game.birthpoints_empty')}
        </p>
        <p style={{ fontSize: 12, color: "var(--muted)", marginBottom: 14 }}>
          {t('saves.new_game.birthpoints_empty_hint')}
        </p>
        <button
          onClick={fetchBirthpoints}
          style={{
            fontSize: 12, padding: "4px 14px",
            border: "1px solid var(--line)", borderRadius: 6,
            background: "var(--panel-2)", cursor: "pointer", color: "inherit",
          }}
        >
          {t('saves.new_game.retry')}
        </button>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 6 }}>
      <InlineErr msg={bpErr} />
      {phases.map(phase => {
        const isOpen = openPhase === phase.phase_label;
        return (
          <div key={phase.phase_label} style={{
            border: "1px solid var(--line-soft)",
            borderRadius: "var(--r-3, 8px)",
            overflow: "hidden",
          }}>
            {/* accordion header */}
            <button
              onClick={() => setOpenPhase(isOpen ? null : phase.phase_label)}
              style={{
                width: "100%", textAlign: "left",
                display: "flex", alignItems: "center", justifyContent: "space-between",
                gap: 10, padding: "9px 14px",
                background: isOpen ? "var(--panel-2)" : "transparent",
                border: "none", cursor: "pointer",
                borderBottom: isOpen ? "1px solid var(--line-soft)" : "none",
                transition: "background 0.15s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                <Icon
                  name={isOpen ? "chevron_down" : "chevron_right"}
                  size={11}
                  style={{ flexShrink: 0, color: "var(--muted)" }}
                />
                <span style={{ fontFamily: "var(--font-serif)", fontSize: 13.5, letterSpacing: "0.02em" }}>
                  {phase.phase_label}
                </span>
              </div>
              <span className="muted-2" style={{ fontSize: 11, whiteSpace: "nowrap", flexShrink: 0 }}>
                {t('saves.birthpoint.chapter_range', { min: phase.chapter_min, max: phase.chapter_max, count: phase.chapter_count })}
              </span>
            </button>

            {/* accordion body */}
            {isOpen && (
              <div style={{ display: "grid", gap: 4, padding: "8px 10px" }}>
                {phase.anchors.map(anchor => {
                  const isSelected = birthpoint && birthpoint.anchor_id === anchor.anchor_id;
                  return (
                    <label
                      key={anchor.anchor_id}
                      className={`pl-newgame-card${isSelected ? " active" : ""}`}
                      style={{ gridTemplateColumns: "14px 1fr auto", gap: 10, cursor: "pointer" }}
                    >
                      <input
                        type="radio"
                        checked={!!isSelected}
                        onChange={() => setBirthpoint({
                          phase_label: phase.phase_label,
                          anchor_id: anchor.anchor_id,
                          chapter_min: anchor.chapter_min,
                          chapter_max: anchor.chapter_max,
                          story_time_label: anchor.story_time_label,
                        })}
                      />
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontFamily: "var(--font-serif)", fontSize: 13, letterSpacing: "0.02em" }}>
                          {anchor.story_time_label}
                        </div>
                        {anchor.sample_summary && (
                          <div className="muted-2" style={{ fontSize: 11.5, marginTop: 2, lineHeight: 1.5 }}>
                            {anchor.sample_summary}
                          </div>
                        )}
                      </div>
                      <span className="muted-2" style={{ fontSize: 10.5, whiteSpace: "nowrap", alignSelf: "center" }}>
                        {anchor.chapter_max !== anchor.chapter_min
                          ? t('saves.birthpoint.chapter_range_short', { min: anchor.chapter_min, max: anchor.chapter_max })
                          : t('saves.birthpoint.chapter_single', { min: anchor.chapter_min })}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ============================================================
   Step 4: 初始身份
   ============================================================ */
function IdentityStep({ scriptId, birthpoint, pickedCard, allRoleOptions, identity, setIdentity, playerOrigin, setPlayerOrigin, identityKnown, setIdentityKnown, roleMode, newCardForm }) {
  const { t } = useTranslation();
  const [recs, setRecs] = React.useState([]);
  const [recsLoading, setRecsLoading] = React.useState(false);
  const [recsErr, setRecsErr] = React.useState("");
  const [customOpen, setCustomOpen] = React.useState(false);
  const [customName, setCustomName] = React.useState("");
  const [customRole, setCustomRole] = React.useState("");
  const [customBg, setCustomBg] = React.useState("");

  const pickedRole = allRoleOptions ? allRoleOptions.find(o => o.key === pickedCard) : null;
  const pickedName = roleMode === 'new'
    ? (newCardForm?.name?.trim() || t('saves.identity.no_card_selected'))
    : (pickedRole?.name || t('saves.identity.no_card_selected'));

  const fetchAiRecs = React.useCallback(async () => {
    if (!scriptId) {
      setRecsErr(t('saves.identity.no_script'));
      return;
    }
    setRecsLoading(true); setRecsErr(""); setRecs([]);
    const args = {
      birthpoint_phase: birthpoint ? birthpoint.phase_label : "",
      birthpoint_label: birthpoint ? birthpoint.story_time_label : "",
      character_card_id: pickedRole ? (pickedRole.id || null) : null,
      character_card_kind: pickedRole ? pickedRole.kind : null,
      player_origin: playerOrigin,  // 'isekai' | 'native' — 给 LLM prompt 决定身份类型
      n: 4,
    };
    // 新建角色卡时，把用户填的名称传给后端供 LLM 参考
    // 新建角色卡模式：先把卡片保存到数据库再用真实 ID 传给后端
    const _newName = (newCardForm?.name || '').trim();
    if (_newName && !args.character_card_id) {
      try {
        const saved = await window.api.cards.myUpsert(cardFormPayload(newCardForm));
        if (saved?.card?.id) {
          args.character_card_id = saved.card.id;
          args.character_card_kind = 'user_card';
        }
      } catch (_) { /* 保存失败则放弃传卡，走无卡推荐 */ }
    }
    try {
      const r = await fetch(
        `${window.__API_BASE || ""}/api/scripts/${parseInt(scriptId, 10)}/recommend-identity`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify(args),
        }
      );
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        // 502 (LLM 失败) / 500 (工具失败) / 403 (无权) 一律显示后端真实错误
        const msg = (data && data.error) || t('saves.identity.ai_req_fail', { status: r.status });
        setRecsErr(msg);
        return;
      }
      if (data && data.ok === false && data.error) {
        setRecsErr(data.error);
        return;
      }
      if (data && Array.isArray(data.recommendations) && data.recommendations.length > 0) {
        setRecs(data.recommendations);
      } else {
        setRecsErr(t('saves.identity.ai_empty'));
      }
    } catch (e) {
      setRecsErr(t('saves.identity.ai_net_err', { err: e.message || String(e) }));
    } finally {
      setRecsLoading(false);
    }
  }, [scriptId, birthpoint, pickedRole, playerOrigin, roleMode, newCardForm]);

  const pickRec = (rec) => {
    setIdentity({
      name: rec.name || "",
      role: rec.role || "",
      background: rec.background || "",
      source: "ai",
      _from: "ai",
      player_origin: playerOrigin,  // 'isekai' | 'native' — GM 由此判断玩家定位
    });
  };

  const applyCustom = () => {
    const role = customRole.trim();
    const bg = customBg.trim();
    if (!role && !bg) return;
    setIdentity({
      name: customName.trim(),
      role,
      background: bg,
      source: "custom",
      _from: "custom",
      player_origin: playerOrigin,
    });
  };

  const clearIdentity = () => {
    setIdentity(null);
  };

  // playerOrigin 跟身份卡正交:切换时只同步 identity.player_origin 标记,
  // 不动 role/background 字段(身份卡是 role overlay,穿越者是 meta 设定)
  React.useEffect(() => {
    if (identity && identity.player_origin !== playerOrigin) {
      setIdentity({ ...identity, player_origin: playerOrigin });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playerOrigin]);

  const noIdentity = !identity;
  // 暖色面板样式(与角色档 CardSheet 一致)
  const panel = {
    background: 'var(--panel-2, #282623)', border: '1px solid var(--line-soft, #2a2724)',
    borderRadius: 12, padding: '14px 16px',
  };
  const labelEyebrow = { fontSize: 11, letterSpacing: '.06em', color: 'var(--accent, #c96442)', fontWeight: 600, textTransform: 'uppercase' };

  return (
    <CSSpaceBetween size="m">
      {/* 说明 */}
      <CSBox key="intro" color="text-body-secondary" fontSize="body-s">
        {t('saves.identity.intro')}
      </CSBox>

      {/* 当前选择 */}
      <div key="current-identity" style={{
        ...panel,
        borderColor: noIdentity ? 'var(--line-soft, #2a2724)' : 'var(--accent, #c96442)',
        background: noIdentity ? 'var(--panel-2, #282623)' : 'var(--accent-soft, rgba(201,100,66,.12))',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
      }}>
        {noIdentity ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-quiet, #c8c2b7)', fontSize: 13 }}>
            <Icon name="check" size={13} /> {t('saves.identity.no_identity', { name: pickedName })}
          </div>
        ) : (
          <div style={{ display: 'grid', gap: 4, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <CSBadge color={identity._from === 'ai' ? 'blue' : 'grey'}>{identity._from === 'ai' ? t('saves.identity.badge_ai') : t('saves.identity.badge_manual')}</CSBadge>
              {identity.name && <strong style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 15, color: 'var(--text, #ebe7df)' }}>{identity.name}</strong>}
              {identity.role && <span style={{ fontSize: 13, color: 'var(--text-quiet, #c8c2b7)' }}>{identity.role}</span>}
            </div>
            {identity.background && <span style={{ fontSize: 12.5, lineHeight: 1.6, color: 'var(--muted, #968f85)' }}>{identity.background}</span>}
          </div>
        )}
        {!noIdentity && <CSButton iconName="close" variant="inline-link" onClick={clearIdentity}>{t('saves.identity.btn_clear')}</CSButton>}
      </div>

      {/* 玩家出身 4 档单选卡 */}
      <div key="origin-selector" style={{ ...panel, display: 'grid', gap: 12 }}>
        <div style={{ display: 'grid', gap: 4 }}>
          <span style={{ ...labelEyebrow }}>{t('saves.identity.origin_section_label')}</span>
          <span style={{ fontSize: 12, color: 'var(--muted, #968f85)', lineHeight: 1.55 }}>{t('saves.identity.origin_section_hint')}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8 }} role="radiogroup" aria-label={t('saves.identity.origin_section_label')}>
          {[
            { value: 'soul', icon: '◈', labelKey: 'origin_soul_label', essenceKey: 'origin_soul_essence', mappingKey: 'origin_soul_mapping', hintKey: 'origin_soul_hint',
              accentColor: '#8db4e8', accentBg: 'rgba(85,130,200,.14)', accentBorder: 'rgba(85,130,200,.38)' },
            { value: 'body', icon: '◉', labelKey: 'origin_body_label', essenceKey: 'origin_body_essence', mappingKey: 'origin_body_mapping', hintKey: 'origin_body_hint',
              accentColor: '#e8a87c', accentBg: 'rgba(220,140,80,.14)', accentBorder: 'rgba(220,140,80,.38)' },
            { value: 'dual', icon: '◑', labelKey: 'origin_dual_label', essenceKey: 'origin_dual_essence', mappingKey: 'origin_dual_mapping', hintKey: 'origin_dual_hint',
              accentColor: '#b8a0e8', accentBg: 'rgba(160,130,210,.14)', accentBorder: 'rgba(160,130,210,.38)' },
            { value: 'native', icon: '◎', labelKey: 'origin_native_label', essenceKey: 'origin_native_essence', mappingKey: 'origin_native_mapping', hintKey: 'origin_native_hint',
              accentColor: '#b8b0a5', accentBg: 'rgba(150,143,133,.14)', accentBorder: 'rgba(150,143,133,.32)' },
          ].map(({ value, icon, labelKey, essenceKey, mappingKey, hintKey, accentColor, accentBg, accentBorder }) => {
            const selected = playerOrigin === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => setPlayerOrigin(value)}
                style={{
                  textAlign: 'left',
                  padding: '11px 13px',
                  cursor: 'pointer',
                  border: selected ? `1px solid ${accentBorder}` : '1px solid var(--line-soft, #2a2724)',
                  borderRadius: 10,
                  background: selected ? accentBg : 'var(--panel, #211f1d)',
                  display: 'grid',
                  gap: 6,
                  transition: 'border-color .12s, background .12s',
                  outline: 'none',
                }}
                onFocus={(e) => { e.currentTarget.style.outlineOffset = '2px'; e.currentTarget.style.outline = `1px solid ${accentBorder}`; }}
                onBlur={(e) => { e.currentTarget.style.outline = 'none'; }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{
                    fontSize: 18, lineHeight: 1, flexShrink: 0,
                    color: selected ? accentColor : 'var(--muted-2, #6b655e)',
                    transition: 'color .12s',
                    fontFamily: 'var(--font-serif)',
                  }}>{icon}</span>
                  <span style={{
                    fontFamily: 'var(--font-serif)',
                    fontSize: 14, fontWeight: 700,
                    color: selected ? accentColor : 'var(--text, #ebe7df)',
                    transition: 'color .12s',
                    lineHeight: 1.2,
                  }}>{t(`saves.identity.${labelKey}`)}</span>
                </div>
                <span style={{ fontSize: 11.5, fontWeight: 600, color: selected ? accentColor : 'var(--muted, #968f85)', lineHeight: 1.3, transition: 'color .12s' }}>
                  {t(`saves.identity.${essenceKey}`)}
                </span>
                <span style={{ fontSize: 11, color: 'var(--muted-2, #6b655e)', lineHeight: 1.5, letterSpacing: '0.01em' }}>
                  {t(`saves.identity.${mappingKey}`)}
                </span>
                {selected && (
                  <span style={{ fontSize: 11.5, color: 'var(--muted, #968f85)', lineHeight: 1.5, borderTop: `1px solid ${accentBorder}`, paddingTop: 6, marginTop: 2 }}>
                    {t(`saves.identity.${hintKey}`)}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* 知道/不知道身份卡 — 仅挂了身份卡时有效 */}
        {identity && playerOrigin !== 'body' && (
          <div style={{ borderTop: '1px solid var(--line-soft, #2a2724)', paddingTop: 10, display: 'grid', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 12.5, fontWeight: 600, color: 'var(--text-quiet, #c8c2b7)' }}>{t('saves.identity.identity_known_label')}</span>
              <span style={{ fontSize: 11.5, color: 'var(--muted-2, #6b655e)' }}>{t('saves.identity.identity_known_hint')}</span>
            </div>
            <div style={{ display: 'flex', gap: 8 }} role="radiogroup" aria-label={t('saves.identity.identity_known_label')}>
              {[
                { val: true, labelKey: 'identity_known_true_label', descKey: 'identity_known_true_desc' },
                { val: false, labelKey: 'identity_known_false_label', descKey: 'identity_known_false_desc' },
              ].map(({ val, labelKey, descKey }) => {
                const sel = identityKnown === val;
                return (
                  <button
                    key={String(val)}
                    type="button"
                    role="radio"
                    aria-checked={sel}
                    onClick={() => setIdentityKnown(val)}
                    style={{
                      flex: '1 1 0', textAlign: 'left', padding: '9px 12px', cursor: 'pointer',
                      border: sel ? '1px solid var(--accent-edge, rgba(201,100,66,.42))' : '1px solid var(--line-soft, #2a2724)',
                      borderRadius: 8,
                      background: sel ? 'var(--accent-soft, rgba(201,100,66,.12))' : 'var(--panel, #211f1d)',
                      display: 'grid', gap: 3,
                      transition: 'border-color .12s, background .12s',
                      outline: 'none',
                    }}
                    onFocus={(e) => { e.currentTarget.style.outline = '1px solid var(--accent-edge)'; e.currentTarget.style.outlineOffset = '2px'; }}
                    onBlur={(e) => { e.currentTarget.style.outline = 'none'; }}
                  >
                    <span style={{ fontSize: 13, fontWeight: 600, color: sel ? 'var(--accent, #c96442)' : 'var(--text, #ebe7df)', transition: 'color .12s' }}>
                      {t(`saves.identity.${labelKey}`)}
                    </span>
                    <span style={{ fontSize: 11.5, color: 'var(--muted, #968f85)', lineHeight: 1.5 }}>
                      {t(`saves.identity.${descKey}`)}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* AI 生成身份候选 — 穿越者和身份卡是独立维度,这块始终显示 */}
      <div key="ai-gen" style={{ ...panel, display: 'grid', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
          <div style={{ display: 'grid', gap: 3 }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text, #ebe7df)' }}>{t('saves.identity.ai_title')}</span>
            <span style={{ fontSize: 12, color: 'var(--muted, #968f85)' }}>
              {t(`saves.identity.ai_desc_${playerOrigin}`, t('saves.identity.ai_desc'))}
            </span>
          </div>
          <CSButton iconName={recs.length > 0 ? 'refresh' : 'gen-ai'} loading={recsLoading} disabled={recsLoading} onClick={fetchAiRecs}>
            {recs.length > 0 ? t('saves.identity.btn_regen') : t('saves.identity.btn_gen')}
          </CSButton>
        </div>
        {recsErr && <CSAlert type="error">{recsErr}</CSAlert>}
        {recs.length > 0 && (
          <CSColumnLayout columns={2}>
            {recs.map((rec, i) => {
              const isSelected = identity && identity._from === 'ai' && identity.name === rec.name && identity.role === rec.role;
              return (
                <button key={i} type="button" onClick={() => pickRec(rec)} style={{
                  textAlign: 'left', padding: '11px 13px', cursor: 'pointer',
                  border: isSelected ? '1px solid var(--accent, #c96442)' : '1px solid var(--line-soft, #2a2724)',
                  borderRadius: 10, background: isSelected ? 'var(--accent-soft, rgba(201,100,66,.12))' : 'var(--panel, #211f1d)',
                  display: 'grid', gap: 5, transition: 'border-color .12s, background .12s',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    {rec.name && <strong style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 14, color: 'var(--text, #ebe7df)' }}>{rec.name}</strong>}
                    {rec.role && <span style={{ whiteSpace: 'nowrap' }}><CSBadge>{rec.role}</CSBadge></span>}
                    {isSelected && (
                      <span style={{ marginLeft: 'auto', whiteSpace: 'nowrap' }}>
                        <CSBadge color="green">✓ {t('saves.identity.badge_selected')}</CSBadge>
                      </span>
                    )}
                  </div>
                  {rec.background && <span style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--muted, #968f85)' }}>{rec.background}</span>}
                </button>
              );
            })}
          </CSColumnLayout>
        )}
      </div>

      {/* 手动创建 */}
      <CSExpandableSection key="manual"
        variant="container"
        headerText={t('saves.identity.manual_title')}
        headerActions={identity && identity._from === 'custom' ? <CSBadge color="green">{t('saves.identity.badge_filled')}</CSBadge> : undefined}
        expanded={customOpen}
        onChange={({ detail }) => setCustomOpen(detail.expanded)}
      >
        <CSSpaceBetween size="l">
          <CSColumnLayout columns={2}>
            <CSFormField label={t('saves.identity.field_alias')} description={t('saves.identity.field_alias_desc')}>
              <CSInput value={customName} onChange={({ detail }) => setCustomName(detail.value)} placeholder={t('saves.identity.field_alias_placeholder')} />
            </CSFormField>
            <CSFormField label={t('saves.identity.field_role')} constraintText={t('saves.identity.field_role_constraint')}>
              <CSInput value={customRole} onChange={({ detail }) => setCustomRole(detail.value)} placeholder={t('saves.identity.field_role_placeholder')} />
            </CSFormField>
            <div style={{ gridColumn: '1 / -1' }}>
              <CSFormField label={t('saves.identity.field_bg')}>
                <CSTextarea rows={3} value={customBg} onChange={({ detail }) => setCustomBg(detail.value)}
                  placeholder={t('saves.identity.field_bg_placeholder')} />
              </CSFormField>
            </div>
          </CSColumnLayout>
          <div style={{ textAlign: 'right' }}>
            <CSButton variant="primary" iconName="check" onClick={applyCustom}
              disabled={!customRole.trim() && !customBg.trim()}>{t('saves.identity.btn_apply')}</CSButton>
          </div>
        </CSSpaceBetween>
      </CSExpandableSection>
    </CSSpaceBetween>
  );
}

/* ============================================================
   MAIN WIZARD COMPONENT
   ============================================================ */
function NewGameModal({ open, onClose, onConfirm, defaultScriptId = null }) {
  const { t } = useTranslation();
  // ── shared data ──────────────────────────────────────────────
  const [scripts, setScripts] = useStatePL([]);
  const [personas, setPersonas] = useStatePL([]);
  const [userCards, setUserCards] = useStatePL([]);
  const [loading, setLoading] = useStatePL(true);

  // ── Step 1 state ─────────────────────────────────────────────
  const [title, setTitle] = useStatePL("");
  const [scriptId, setScriptId] = useStatePL("");

  // ── Step 2 state ─────────────────────────────────────────────
  const [roleMode, setRoleMode] = useStatePL("existing");
  const [pickedCard, setPickedCard] = useStatePL("");
  // 新建角色卡:复用 cards.jsx 的完整字段表单(与「新建用户角色卡」对齐)
  const [newCardForm, setNewCardForm] = useStatePL(() => cardFormInit(null));
  const uNewCard = (k, v) => setNewCardForm(f => ({ ...f, [k]: v }));
  // 角色卡预览(只读 CardSheet)
  const [previewCard, setPreviewCard] = useStatePL(null);

  // ── Step 3 state ─────────────────────────────────────────────
  const [birthpoint, setBirthpoint] = useStatePL(null);

  // ── Step 4 state ─────────────────────────────────────────────
  const [identity, setIdentity] = useStatePL(null);

  // ── Step 5 state ─────────────────────────────────────────────
  const [storyIntent, setStoryIntent] = useStatePL("");

  // 玩家定位类型 (soul/body/dual/native) — 与身份卡 overlay 正交。
  // 提到 NewGameModal 顶层,IdentityStep 通过 prop 读写,
  // payload 独立带上 player_origin 字段(身份卡为 null 时也要带)。
  const [playerOrigin, setPlayerOrigin] = useStatePL('soul');
  // 是否知道身份卡 — body 时置 null(无身份卡); 其余默认 true(知道)。
  const [identityKnown, setIdentityKnown] = useStatePL(true);

  // ── submit ───────────────────────────────────────────────────
  const [submitErr, setSubmitErr] = useStatePL("");
  const [submitting, setSubmitting] = useStatePL(false);
  const [reviewGateBlocked, setReviewGateBlocked] = useStatePL(false);

  // ── load data when opened ────────────────────────────────────
  React.useEffect(() => {
    if (!open) return;
    // reset transient state
    setTitle(""); setSubmitErr(""); setSubmitting(false); setReviewGateBlocked(false); setLoading(true); setPlayerOrigin('soul'); setIdentityKnown(true);
    setNewCardForm(cardFormInit(null)); setPreviewCard(null);
    setBirthpoint(null); setIdentity(null); setStoryIntent("");
    (async () => {
      let scList = [];
      try {
        const r = await window.api.scripts.list();
        scList = Array.isArray(r) ? r : (r?.items || r?.scripts || []);
      } catch (_) {}
      let psList = [];
      try {
        const p = await window.api.account.personas.list();
        psList = (p && (p.items || p.personas)) || [];
      } catch (_) {}
      let ucList = [];
      try {
        const c = await window.api.cards.myList();
        ucList = (c && (c.items || c.cards)) || [];
      } catch (_) {}
      setScripts(scList);
      setPersonas(psList);
      setUserCards(ucList);
      // task 108: script priority: 1) caller defaultScriptId 2) localStorage 3) first
      let pickId = "";
      if (defaultScriptId && scList.some(x => String(x.id) === String(defaultScriptId))) {
        pickId = String(defaultScriptId);
      } else {
        let remembered = "";
        try { remembered = localStorage.getItem("newgame.lastScriptId") || ""; } catch (_) {}
        if (remembered && scList.some(x => String(x.id) === remembered)) {
          pickId = remembered;
        } else {
          pickId = scList.length ? String(scList[0].id) : "";
        }
      }
      setScriptId(pickId);
      // default character
      if (psList.length) { setRoleMode("existing"); setPickedCard(`persona:${psList[0].id || psList[0].slug}`); }
      else if (ucList.length) { setRoleMode("existing"); setPickedCard(`user:${ucList[0].id || ucList[0].slug}`); }
      else { setRoleMode("new"); setPickedCard(""); }
      // task 127: 默认存档名只用剧本名 — 角色还没选,不要预设角色名
      // (之前用 psList[0].name 但用户还没"选",误导)
      try {
        const sc = scList.find(x => String(x.id) === pickId);
        const scTitle = (sc && (sc.title || "").replace(/^《|》$/g, "")) || "";
        if (scTitle) setTitle(`${scTitle} · 新档`);
        else setTitle(t('saves.new_game.page_title'));
      } catch (_) { setTitle(t('saves.new_game.page_title')); }
      setLoading(false);
    })();
  }, [open]);

  if (!open) return null;

  const allRoleOptions = [
    ...personas.map(p => ({
      key: `persona:${p.id || p.slug}`, kind: "persona", id: p.id || null, slug: p.slug || "",
      name: p.name || t('platform.menu.unnamed'), subtitle: p.role || t('saves.new_game.card_kind_persona'), pinned: !!p.is_default,
    })),
    ...userCards.map(c => ({
      key: `user:${c.id || c.slug}`, kind: "user_card", id: c.id || null, slug: c.slug || "",
      name: c.name || t('platform.menu.unnamed'), subtitle: c.identity || t('saves.new_game.card_kind_user'), pinned: false,
    })),
  ];

  // 各必填模块完成校验(单页:不再按步骤 gating,只用于概要 + 创建按钮)
  const step1Valid = title.trim() && scriptId;
  const step2Valid = (roleMode === "existing" && pickedCard) || (roleMode === "new" && newCardForm.name.trim());
  const step3Valid = !!birthpoint;
  // 身份卡是 overlay,和玩家出身正交。用户可以只选魂穿/肉穿/双魂/原住民定位,
  // 不挂本地身份卡时直接按角色卡开局。
  const step4Valid = true;

  const handleSubmit = async () => {
    setSubmitErr(""); setSubmitting(true);
    try {
      // 新建角色卡:走与「新建用户角色卡」完全相同的创建路径(myUpsert),
      // 落库后当作"现有卡"使用,确保所有字段一致持久化。
      let picked = allRoleOptions.find(o => o.key === pickedCard);
      let charId = roleMode === "existing" && picked ? (picked.id || picked.slug || null) : null;
      let charKind = roleMode === "existing" && picked ? picked.kind : null;
      if (roleMode === "new") {
        const r = await window.api.cards.myUpsert(cardFormPayload(newCardForm));
        const created = r && r.card;
        if (!created || !(created.id || created.slug)) throw new Error(t('saves.new_game.card_create_fail'));
        charId = created.id || created.slug;
        charKind = "user_card";
      }
      const payload = {
        title: title.trim(),
        script_id: parseInt(scriptId, 10),
        character_id: charId,
        character_kind: charKind,
        new_card: null,
        // 新建卡已转成真实 user_card,统一按 existing 处理
        role_mode: roleMode === "new" ? "existing" : roleMode,
        birthpoint: birthpoint || null,
        // v29: 透传 source (custom|ai) 给后端落库 identity_cards.source;identity=null 表示不挂 overlay
        identity: identity ? {
          name: identity.name || "",
          role: identity.role || "",
          background: identity.background || "",
          source: identity.source || "custom",
        } : null,
        story_intent: storyIntent.trim() || null,
        // 独立字段,与 identity 解耦:即使没挂身份卡也要带,后端写到 state.player.player_origin
        player_origin: playerOrigin || 'soul',
        // 没挂身份卡时无需传 identity_known;该字段只描述"是否知道这张身份卡"。
        ...(identity && playerOrigin !== 'body' ? { identity_known: identityKnown } : {}),
      };
      const res = onConfirm?.(payload);
      if (res && typeof res.then === "function") await res;
    } catch (e) {
      const msg = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('saves.new_game.create_fail');
      setSubmitErr(msg);
      // 自动检测 KB 复核 gate, 翻出 inline fast-path 按钮("一键标记并重试")
      if (msg && /KB 复核|review_status|尚未复核|尚未通过/.test(String(msg))) {
        setReviewGateBlocked(true);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const oneClickMarkAndRetry = async () => {
    if (!scriptId) return;
    setSubmitting(true); setSubmitErr("");
    try {
      const r = await fetch(`${window.__API_BASE || ""}/api/scripts/${parseInt(scriptId, 10)}/mark-reviewed`, {
        method: "POST", credentials: "include",
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || data.ok === false) {
        throw new Error((data && (data.error || data.detail)) || `mark-reviewed 失败 (HTTP ${r.status})`);
      }
      setReviewGateBlocked(false);
      // 立刻重试创建
      await handleSubmit();
    } catch (e) {
      setSubmitErr(String(e && (e.message || e)) || "标记复核失败");
      setSubmitting(false);
    }
  };

  /* ── EC2 式单页:基本信息区块 ── */
  const scriptOpts = scripts.map(sc => ({ value: String(sc.id), label: sc.title }));

  const sec_basic = (
    // Cloudscape Container 内部 SpaceBetween 包 [header, children],期望 children 顶层有 key
    <CSColumnLayout key="sec_basic" columns={2}>
      <CSFormField label={t('saves.new_game.field_save_name')} constraintText={t('saves.new_game.field_save_name_req')}>
        <CSInput value={title} onChange={({ detail }) => setTitle(detail.value)} autoFocus />
      </CSFormField>
      <CSFormField label={t('saves.new_game.field_script')} constraintText={t('saves.new_game.field_script_req')}>
        <CSSelect
          selectedOption={scriptOpts.find(o => o.value === scriptId) || null}
          options={scriptOpts}
          disabled={!scripts.length}
          placeholder={scripts.length ? t('saves.new_game.field_script_placeholder') : t('saves.new_game.field_script_no_scripts')}
          onChange={({ detail }) => {
            const v = detail.selectedOption.value;
            setScriptId(v);
            setBirthpoint(null);
            try { if (v) localStorage.setItem('newgame.lastScriptId', v); } catch (_) {}
          }}
        />
      </CSFormField>
    </CSColumnLayout>
  );

  const step1Content = (
    // Cloudscape SpaceBetween 内部用 React.Children.map 加间距,条件渲染的 children 需要稳定 key
    <CSSpaceBetween key="step1" size="l">
      <CSFormField key="mode" label={t('saves.new_game.role_mode_label')}
        description={allRoleOptions.length === 0 ? t('saves.new_game.role_mode_empty') : undefined}>
        <CSSegmentedControl
          selectedId={roleMode}
          options={[
            { id: 'existing', text: t('saves.new_game.role_mode_existing'), disabled: allRoleOptions.length === 0 },
            { id: 'new', text: t('saves.new_game.role_mode_new') },
          ]}
          onChange={({ detail }) => { setRoleMode(detail.selectedId); if (detail.selectedId === 'new') setPickedCard(''); }}
        />
      </CSFormField>
      {roleMode === 'existing' && allRoleOptions.length > 0 && (
        <div key="existing-cards" className="pl-newgame-cards">
          {allRoleOptions.map(c => (
            <label key={c.key} className={`pl-newgame-card ${pickedCard === c.key ? 'active' : ''}`}>
              <input type="radio" checked={pickedCard === c.key} onChange={() => setPickedCard(c.key)} />
              <div className="pl-newgame-card-avatar serif">{c.name.slice(0, 1)}</div>
              <div className="pl-newgame-card-body">
                <strong>{c.name}</strong>
                <span className="muted-2" style={{ fontSize: 11.5 }}>
                  {c.subtitle} · {c.kind === 'persona' ? t('saves.new_game.card_kind_persona') : t('saves.new_game.card_kind_user')}
                </span>
              </div>
              <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                {c.pinned && <span className="pill accent" style={{ fontSize: 10.5 }}><Icon name="pin" size={9} /> {t('saves.new_game.card_default_pill')}</span>}
                <button type="button" className="btn btn-ghost" style={{ fontSize: 11.5, padding: '4px 10px' }}
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); openPreview(c); }}>
                  <Icon name="eye" size={11} /> {t('saves.new_game.card_preview_btn')}
                </button>
              </div>
            </label>
          ))}
          <a className="pl-newgame-card pl-newgame-card-link" href="#cards" onClick={onClose}>
            <Icon name="folder" size={14} /><span>{t('saves.new_game.card_library_link')}</span>
          </a>
        </div>
      )}
      {roleMode === 'new' && (
        <div key="new-card">
          <CSBox color="text-body-secondary" fontSize="body-s" padding={{ bottom: 's' }}>
            {t('saves.new_game.new_card_desc')}
          </CSBox>
          <CardEditFields form={newCardForm} u={uNewCard} kind="user" />
        </div>
      )}
    </CSSpaceBetween>
  );

  const step4Content = (
    // Cloudscape InternalSpaceBetween 用 flattenChildren+map(child=>createElement('div',{key},child)),
    // 子元素没 key 时 wrapper div 的 key 全是 undefined → React 报「Each child should have a unique key」
    <CSSpaceBetween key="step4" size="m">
      <CSBox key="intro" color="text-body-secondary" fontSize="body-s">
        {t('saves.new_game.intent_desc').split('\n').map((line, i) => (
          <div key={`l${i}`}>{line}</div>
        ))}
      </CSBox>
      <CSFormField key="textarea" label={t('saves.new_game.intent_label')}>
        <CSTextarea
          rows={6}
          value={storyIntent}
          onChange={({ detail }) => setStoryIntent(detail.value)}
          placeholder="示例:&#10;· 玩家拒绝任何战斗 — 必须找非战斗解决方案&#10;· 主角穿越者身份是绝对秘密,GM 必须保护&#10;· 优先甜文路线,避免黑深残"
        />
      </CSFormField>
    </CSSpaceBetween>
  );

  // 区块标题:h2 + 说明,可选项加「· 可选」标
  const secHeader = (text, desc, optional) => (
    <CSHeader variant="h2" description={desc}>
      {text}{optional ? <CSBox variant="span" color="text-status-inactive" fontSize="body-s">{t('saves.new_game.sec_optional')}</CSBox> : null}
    </CSHeader>
  );

  // 右侧概要:必填项完成度 + 已选摘要 + 创建按钮
  const reqRows = [
    { label: t('saves.new_game.req_save_script'), ok: step1Valid },
    { label: t('saves.new_game.req_role'), ok: step2Valid },
    { label: t('saves.new_game.req_birthpoint'), ok: step3Valid },
    { label: t('saves.new_game.req_identity'), ok: step4Valid },
  ];
  const allValid = step1Valid && step2Valid && step3Valid && step4Valid;
  const pickedRoleName = roleMode === 'new'
    ? (newCardForm.name.trim() || t('saves.new_game.new_role_default'))
    : (allRoleOptions.find(o => o.key === pickedCard)?.name || '—');

  // 角色卡预览:从原始 personas / userCards 取完整对象供 CardSheet 渲染
  const openPreview = (opt) => {
    const full = opt.kind === 'persona'
      ? personas.find(p => String(p.id || p.slug) === String(opt.id || opt.slug))
      : userCards.find(c => String(c.id || c.slug) === String(opt.id || opt.slug));
    const card = full || { name: opt.name, identity: opt.subtitle };
    setPreviewCard({ card: { ...card, identity: card.identity || card.role || opt.subtitle }, name: opt.name });
  };

  const node = (
    <div style={{ position: 'fixed', top: 53, left: 0, right: 0, bottom: 0, zIndex: 1000, background: 'var(--bg, #1a1817)', overflow: 'auto' }}>
      {/* 顶部栏:标题 + 取消(位于平台顶栏下方,保留平台导航) */}
      <div style={{ position: 'sticky', top: 0, zIndex: 3, background: '#131211', borderBottom: '1px solid #36322d' }}>
        <div style={{ maxWidth: 1240, margin: '0 auto', padding: '13px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
          <div style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 600, color: '#ebe7df' }}>{t('saves.new_game.page_title')}</div>
          <CSButton iconName="close" variant="link" onClick={onClose}>{t('saves.new_game.btn_cancel')}</CSButton>
        </div>
      </div>

      <div style={{ maxWidth: 1240, margin: '0 auto', padding: '20px 24px 80px' }}>
        <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
          {/* 左:各模块平铺 */}
          <div style={{ flex: 1, minWidth: 0 }}>
            <CSSpaceBetween size="l">
              {loading && (
                <CSBox key="loading" color="text-body-secondary"><Icon name="spinner" size={13} className="spin" /> {t('saves.new_game.loading')}</CSBox>
              )}
              {!loading && scripts.length === 0 && (
                <CSAlert key="no-scripts" type="warning" header={t('saves.new_game.no_scripts_title')}>
                  {t('saves.new_game.no_scripts_body')} <a href="#scripts-import" onClick={onClose}>{t('saves.new_game.no_scripts_link')}</a> {t('saves.new_game.no_scripts_suffix')}
                </CSAlert>
              )}
              {/* Cloudscape SpaceBetween 内部用 React.Children.map 加间距,需要 child 显式 key */}
              <CSContainer key="basic" header={secHeader(t('saves.new_game.sec_basic_title'), t('saves.new_game.sec_basic_desc'))}>{sec_basic}</CSContainer>
              <CSContainer key="role" header={secHeader(t('saves.new_game.sec_role_title'), t('saves.new_game.sec_role_desc'))}>{step1Content}</CSContainer>
              <CSContainer key="birthpoint" header={secHeader(t('saves.new_game.sec_birthpoint_title'), scriptId ? t('saves.new_game.sec_birthpoint_desc_ready') : t('saves.new_game.sec_birthpoint_desc_wait'))}>
                {scriptId
                  ? <BirthpointStep key="birthpoint-step" scriptId={scriptId} birthpoint={birthpoint} setBirthpoint={setBirthpoint} />
                  : <CSBox key="birthpoint-empty" color="text-body-secondary" fontSize="body-s">{t('saves.new_game.sec_birthpoint_empty')}</CSBox>}
              </CSContainer>
              <CSContainer key="identity" header={secHeader(t('saves.new_game.sec_identity_title'), t('saves.new_game.sec_identity_desc'))}>
                <IdentityStep key="identity-step" scriptId={scriptId} birthpoint={birthpoint} pickedCard={pickedCard} allRoleOptions={allRoleOptions} identity={identity} setIdentity={(id) => setIdentity(id)} playerOrigin={playerOrigin} setPlayerOrigin={(o) => { setPlayerOrigin(o); if (o === 'body') { /* body 无身份卡,identityKnown 设 null */ } else if (identityKnown === null || identityKnown === undefined) { setIdentityKnown(true); } }} identityKnown={identityKnown} setIdentityKnown={setIdentityKnown} roleMode={roleMode} newCardForm={newCardForm} />
              </CSContainer>
              <CSContainer key="intent" header={secHeader(t('saves.new_game.sec_intent_title'), t('saves.new_game.sec_intent_desc'), true)}>{step4Content}</CSContainer>
            </CSSpaceBetween>
          </div>

          {/* 右:概要 + 创建(sticky)
              CSSpaceBetween 内部 flattenChildren+map, 每个 child 需要 key 否则 wrapper key 为 undefined */}
          <div style={{ width: 320, flexShrink: 0, position: 'sticky', top: 72 }}>
            <CSContainer header={<CSHeader variant="h2">{t('saves.new_game.summary_title')}</CSHeader>}>
              <CSSpaceBetween size="m">
                <CSSpaceBetween key="status" size="xs">
                  {reqRows.map(r => (
                    <CSStatusIndicator key={r.label} type={r.ok ? 'success' : 'pending'}>{r.label}</CSStatusIndicator>
                  ))}
                </CSSpaceBetween>
                <CSKeyValuePairs key="kv" columns={1} items={[
                  { label: t('saves.new_game.summary_save_name'), value: title.trim() || '—' },
                  { label: t('saves.new_game.summary_script'), value: scriptOpts.find(o => o.value === scriptId)?.label || '—' },
                  { label: t('saves.new_game.summary_role'), value: pickedRoleName },
                  { label: t('saves.new_game.summary_birthpoint'), value: birthpoint?.story_time_label || '—' },
                  { label: t('saves.new_game.summary_identity'), value: identity?.name || identity?.role || '—' },
                ]} />
                {submitErr && (
                  <CSAlert
                    key="err"
                    type={reviewGateBlocked ? 'warning' : 'error'}
                    action={reviewGateBlocked ? (
                      <CSButton onClick={oneClickMarkAndRetry} loading={submitting} disabled={submitting}>
                        {t('saves.new_game.mark_reviewed_and_retry')}
                      </CSButton>
                    ) : undefined}
                  >
                    {submitErr}
                  </CSAlert>
                )}
                <div key="btns" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <CSButton variant="primary" disabled={!allValid || submitting} loading={submitting}
                    onClick={() => { if (allValid) handleSubmit(); }}>
                    {submitting ? t('saves.new_game.btn_creating') : t('saves.new_game.btn_create')}
                  </CSButton>
                  <CSButton variant="link" onClick={onClose}>{t('saves.new_game.btn_cancel_link')}</CSButton>
                </div>
              </CSSpaceBetween>
            </CSContainer>
          </div>
        </div>
      </div>

      {/* 角色卡预览(只读) */}
      <CSModal
        visible={!!previewCard}
        onDismiss={() => setPreviewCard(null)}
        header={t('saves.new_game.preview_title', { name: previewCard?.name || '' })}
        size="medium"
        footer={<div style={{ textAlign: 'right' }}><CSButton variant="primary" onClick={() => setPreviewCard(null)}>{t('saves.new_game.preview_close')}</CSButton></div>}
      >
        {previewCard && <CardSheet card={previewCard.card} kind="user" />}
      </CSModal>
    </div>
  );
  return createPortal(node, document.body);
}

export { SavesPage, SavesListView, BranchesPage, ContinuePicker, NewGameModal };
