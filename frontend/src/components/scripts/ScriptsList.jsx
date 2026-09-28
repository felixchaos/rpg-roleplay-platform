/* 剧本列表 / 在线剧本库 / 章节管理弹窗 / overrides 弹窗。
   从 pages/scripts.jsx 拆出,JSX / props 流逐字节不变。 */

import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import Modal from '../Modal.jsx';
import { usePlatformData, ResizableSplit } from '../../platform-app.jsx';
import { NewGameModal } from '../../pages/saves.jsx';
import { ScriptReview } from '../../pages/script-review.jsx';
import { ScriptDetailPanel } from './ScriptDetail.jsx';
import { ScriptsImportView } from './ScriptsImport.jsx';
import { ScriptsLibraryView } from './ScriptsLibraryView.jsx';
import { ChaptersModal } from './ChaptersModal.jsx';
import { OverridesModal } from './OverridesModal.jsx';
import { scriptPlayBlockReason, activeJobPlayBlockReason, playDropdownState } from './shared.js';
import CSHeader from '@cloudscape-design/components/header';
import CSTable from '@cloudscape-design/components/table';
import CSContainer from '@cloudscape-design/components/container';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSButtonDropdown from '@cloudscape-design/components/button-dropdown';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSTextFilter from '@cloudscape-design/components/text-filter';
import CSPagination from '@cloudscape-design/components/pagination';
import { referenceInfo } from '../../lib/script-sharing.js';

function ScriptsListView() {
  // task 19: 永远以 /api/scripts 真实回包为准；空列表也覆盖 mock，不再混 MOCK_PLATFORM.scripts。
  // task 51：之前 onClick 里用了 `platform?.saves` 但 ScriptsListView 没拿过 platform，
  // 永远是 ReferenceError → 整个按钮 throw 后被 React 静默吞掉 → 用户点了无反应。
  const { t } = useTranslation();
  const { saves: platSaves = [] } = usePlatformData();
  const [scripts, setScripts] = useStatePL([]);
  const [loaded, setLoaded] = useStatePL(false);
  const [busyId, setBusyId] = useStatePL(null);
  // Codex P0-2 修复:没有现成存档时,不再传 fake save {id:null}。
  // 改成弹 NewGameModal,默认填好 script_id,走 saves.create 原子流。
  const [newModalScriptId, setNewModalScriptId] = useStatePL(null);
  // B1: export pack
  const [exportingId, setExportingId] = useStatePL(null);
  // B2: import pack
  const importPackRef = React.useRef(null);
  const [importPackBusy, setImportPackBusy] = useStatePL(false);
  // B3: overrides editor
  const [overridesScript, setOverridesScript] = useStatePL(null);
  // task 51: vector embedding 状态 per script (key: script_id → {running, chunks, cards, worldbook, model})
  const [embedStatus, setEmbedStatus] = useStatePL({});
  // 选中行 + 搜索(对齐存档页:选中 → 下方详情面板)
  const [selectedId, setSelectedId] = useStatePL(null);
  // "状态"列下拉跳转用:点 "去补 worldbook" → 选中剧本 + 详情面板默认到 world tab
  const [pendingTab, setPendingTab] = useStatePL(null);
  const [query, setQuery] = useStatePL("");
  const [scriptPage, setScriptPage] = useStatePL(1);
  const SCRIPT_PAGE_SIZE = 50;

  // 收敛处置②:triggerEmbed(POST /api/scripts/{id}/embed,后端自认废弃 alias)已删——
  // 触发入口收敛到知识库中心(ScriptDetailPanel → rebuild/embeddings)。这里只保留
  // GET /embed/status 的只读轮询,喂概览 tab 的"向量索引"只读进度子卡。
  // task 51: 自动 poll 所有 running 状态的 script,每 3s 刷一次 progress
  useEffectPL(() => {
    const runningIds = Object.entries(embedStatus).filter(([, v]) => v && v.running).map(([k]) => k);
    if (runningIds.length === 0) return;
    const iv = setInterval(async () => {
      for (const sid of runningIds) {
        try {
          const r = await fetch(`${window.__API_BASE || ""}/api/scripts/${sid}/embed/status`, { credentials: "include" });
          if (!r.ok) continue;
          const j = await r.json();
          if (j.ok && j.status) {
            setEmbedStatus(s => ({ ...s, [sid]: j.status }));
            if (!j.status.running) {
              window.toast?.(t('scripts.toast.embed_done'), {
                kind: "ok",
                detail: `chunks ${j.status.chunks.done} · cards ${j.status.cards.done} · worldbook ${j.status.worldbook.done}`,
                duration: 4000,
              });
            }
          }
        } catch (_) {}
      }
    }, 3000);
    return () => clearInterval(iv);
  }, [embedStatus]);

  const reload = React.useCallback(async () => {
    try {
      const r = await window.api.scripts.list();
      const list = Array.isArray(r) ? r : (r?.items || r?.scripts || []);
      const normed = list.map(window.__normalizeScript || ((x) => x));
      setScripts(normed);
      // task 51: 拉每个剧本的 embed 进度,UI 显示已建索引的剧本(check icon)
      // 失败不影响列表加载(各自 catch)
      Promise.all(normed.map(async (s) => {
        try {
          const sr = await fetch(`${window.__API_BASE || ""}/api/scripts/${s.id}/embed/status`, { credentials: "include" });
          const sj = await sr.json();
          if (sj.ok && sj.status) {
            setEmbedStatus(es => ({ ...es, [s.id]: sj.status }));
          }
        } catch (_) {}
      })).catch(() => {});
    } catch (_) {
      setScripts([]);
    } finally {
      setLoaded(true);
    }
  }, []);
  useEffectPL(() => {
    reload();
    const refresh = () => reload();
    // 兼容老事件名 + task 17 新事件名
    window.addEventListener("rpg:scripts:changed", refresh);
    window.addEventListener("rpg-scripts-updated", refresh);
    return () => {
      window.removeEventListener("rpg:scripts:changed", refresh);
      window.removeEventListener("rpg-scripts-updated", refresh);
    };
  }, [reload]);

  const onDelete = async (s) => {
    if (!await window.__confirm({ title: t('scripts.confirm.delete_title'), message: t('scripts.confirm.delete_msg', { title: s.title }), danger: true, confirmText: t('common.delete') })) return;
    setBusyId(s.id);
    try {
      const result = await window.api.scripts.delete(s.id, { force: true });
      if (!result || result.ok !== true || result.deleted !== true) {
        throw new Error(result?.error || result?.detail || t('scripts.toast.delete_fail'));
      }
      window.__apiToast?.(t('scripts.toast.deleted'), { kind: "ok" });
      reload();
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.delete_fail'), { kind: "danger", detail: e?.message });
    } finally {
      setBusyId(null);
    }
  };

  const onUnsubscribe = async (s) => {
    if (!await window.__confirm({ title: t('scripts.confirm.unsubscribe_title'), message: t('scripts.confirm.unsubscribe_msg', { title: s.title }), danger: false, confirmText: t('scripts.confirm.unsubscribe_btn') })) return;
    setBusyId(s.id);
    try {
      const result = await window.api.scripts.unsubscribe(s.id);
      if (!result || result.ok !== true) {
        throw new Error(result?.error || result?.detail || t('scripts.toast.unsubscribe_fail'));
      }
      window.__apiToast?.(t('scripts.toast.unsubscribed'), { kind: "ok" });
      reload();
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.unsubscribe_fail'), { kind: "danger", detail: e?.message });
    } finally {
      setBusyId(null);
    }
  };

  const onImportPackFile = async (file) => {
    if (!file) return;
    setImportPackBusy(true);
    try {
      const result = await window.api.scripts.importPack(file);
      if (result && result.ok === false) throw new Error(result.error || result.detail || t('scripts.toast.import_fail'));
      const sid = result?.script_id;
      const warnings = result?.warnings;
      window.__apiToast?.(
        t('scripts.toast.pack_import_ok'),
        { kind: "ok", detail: warnings?.length ? t('scripts.toast.pack_warnings', { msg: warnings.join("; ") }) : (sid ? `script #${sid}` : "") }
      );
      reload();
    } catch (e) {
      const detail = e?.payload?.detail || e?.message || t('scripts.toast.unknown_error');
      window.__apiToast?.(t('scripts.toast.import_fail'), { kind: "danger", detail });
    } finally {
      setImportPackBusy(false);
      if (importPackRef.current) importPackRef.current.value = "";
    }
  };

  const onExportPack = async (s) => {
    setExportingId(s.id);
    try {
      const filename = (s.title || "script").replace(/[\\/:*?"<>|]/g, "_") + "_pack.zip";
      await window.api.scripts.exportPack(s.id, filename);
      window.__apiToast?.(t('scripts.toast.export_ok'), { kind: "ok", detail: filename });
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.export_fail'), { kind: "danger", detail: e?.message });
    } finally {
      setExportingId(null);
    }
  };

  // task 52：之前 onPreview 只 alert 第一章前 400 字，章节多了无法浏览/编辑。
  // 改成开 ChaptersModal —— 真正展示章节列表 + 内容预览 + 重命名 + 重切分。
  const [chaptersOpen, setChaptersOpen] = useStatePL(null); // script row
  const [reviewScript, setReviewScript] = useStatePL(null); // Phase E.1: KB 复核 modal
  const [importOpen, setImportOpen] = useStatePL(false); // 导入剧本全页覆盖(替代侧栏 #scripts-import)

  // 每行操作下拉项(收敛处置①:去 action_review/embed——复核入口唯一化到详情面板顶部按钮,
  // 嵌入收敛到知识库中心;列表下拉只保留列表级动作)
  const rowActions = (s) => {
    return [
      { id: 'chapters', text: t('scripts.my.action_chapters'), iconName: 'file' },
      { id: 'overrides', text: t('scripts.my.action_overrides'), iconName: 'edit' },
      { id: 'visibility', text: s.is_public ? t('scripts.my.action_unpublish') : t('scripts.my.action_publish'), iconName: s.is_public ? 'lock-private' : 'share' },
      { id: 'export', text: t('scripts.my.action_export'), iconName: 'download', disabled: exportingId === s.id },
      s.is_subscribed
        ? { id: 'unsubscribe', text: t('scripts.my.action_unsubscribe'), iconName: 'remove', disabled: busyId === s.id }
        : { id: 'delete', text: t('scripts.my.action_delete'), iconName: 'remove', disabled: busyId === s.id },
    ];
  };
  const onRowAction = (s, id) => {
    if (id === 'chapters') setChaptersOpen(s);
    else if (id === 'overrides') setOverridesScript(s);
    else if (id === 'export') onExportPack(s);
    else if (id === 'visibility') onToggleVisibility(s);
    else if (id === 'delete') onDelete(s);
    else if (id === 'unsubscribe') onUnsubscribe(s);
  };
  const onToggleVisibility = async (s) => {
    const next = !s.is_public;
    if (next) {
      // 发布到公开库前的设定核对闸:未核对直接引导去「设定核对」,不发请求。
      if ((s.review_status || 'unreviewed') !== 'reviewed') {
        window.__apiToast?.(t('scripts.page.publish_review_required'), { kind: 'warn', detail: t('scripts.page.publish_review_required_detail'), duration: 5500 });
        setReviewScript(s);
        return;
      }
      if (!await window.__confirm({ title: t('scripts.confirm.publish_title'), message: t('scripts.confirm.publish_msg', { title: s.title }), confirmText: t('scripts.confirm.publish_btn') })) return;
    }
    try {
      const r = await window.api.scripts.setVisibility(s.id, next);
      if (r && r.ok === false) throw new Error(r.message || r.error || t('scripts.toast.op_fail'));
      window.__apiToast?.(next ? t('scripts.toast.published') : t('scripts.toast.unpublished'), { kind: 'ok', duration: 2000 });
      setScripts((arr) => arr.map((x) => x.id === s.id ? { ...x, is_public: next } : x));
    } catch (e) {
      // 后端核对闸兜底(前端 review_status 陈旧时返回 409 REVIEW_REQUIRED)
      if (e?.payload?.error === 'REVIEW_REQUIRED') {
        window.__apiToast?.(t('scripts.page.publish_review_required'), { kind: 'warn', detail: e?.payload?.message || t('scripts.page.publish_review_required_fallback'), duration: 5500 });
        setReviewScript(s);
        return;
      }
      window.__apiToast?.(t('scripts.toast.op_fail'), { kind: 'danger', detail: e?.message });
    }
  };
  // 反馈#3:开始游戏不再「有存档就直接进后台」,改成下拉让用户选——继续某个存档 / 开新游戏。
  const onContinueSave = (sv) => { if (sv) window.__openContinue?.(sv); };
  const onNewGame = async (s) => {
    const localBlock = scriptPlayBlockReason(s, t);
    if (localBlock) {
      window.__apiToast?.(t('scripts.my.play_block_title'), { kind: 'warn', detail: localBlock, duration: 6500 });
      return;
    }
    setBusyId(s.id);
    try {
      const active = await window.api.scripts.activeJob(s.id).catch(() => null);
      const liveBlock = activeJobPlayBlockReason(active, t);
      if (liveBlock) {
        window.__apiToast?.(t('scripts.my.play_block_title'), { kind: 'warn', detail: liveBlock, duration: 6500 });
        await reload();
        return;
      }
      setNewModalScriptId(s.id);
    } finally {
      setBusyId(null);
    }
  };
  // 兼容:列表行等单按钮入口仍走「有存档继续最近,无则开新」的一键默认
  const onPlay = async (s) => {
    const sv = platSaves.find(x => x.script_id === s.id);
    if (sv) { onContinueSave(sv); return; }
    await onNewGame(s);
  };

  const visibleScripts = query
    ? scripts.filter((s) => (`${s.title} ${s.uid}`).toLowerCase().includes(query.toLowerCase()))
    : scripts;

  // 分页切片(每页 50 条)
  const scriptPageCount = Math.max(1, Math.ceil(visibleScripts.length / SCRIPT_PAGE_SIZE));
  const pagedScripts = visibleScripts.slice((scriptPage - 1) * SCRIPT_PAGE_SIZE, scriptPage * SCRIPT_PAGE_SIZE);
  // 查询变化时重置到第 1 页
  React.useEffect(() => { setScriptPage(1); }, [query]);

  const selected = scripts.find((x) => x.id === selectedId) || null;

  // [内部] 前缀 = 开发中占位剧本,显示"敬请期待"而非常规详情
  const isInternalPlaceholder = (s) => s && typeof s.title === 'string' && s.title.startsWith('[内部]');

  const detailEl = selected ? (
    isInternalPlaceholder(selected) ? (
      <CSContainer header={<CSHeader variant="h2">{selected.title}</CSHeader>}>
        <div style={{ padding: '36px 20px', textAlign: 'center' }}>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>{t('scripts.page.placeholder_coming_soon')}</div>
          <div style={{ fontSize: 13.5, color: 'var(--muted)', maxWidth: 480, margin: '0 auto 8px' }}>
            {t('scripts.page.placeholder_dnd_desc')}
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted-2)', marginTop: 16 }}>{t('scripts.page.placeholder_eta')}</div>
        </div>
      </CSContainer>
    ) : (
      <ScriptDetailPanel
        script={selected}
        savesCount={platSaves.filter((x) => x.script_id === selected.id).length}
        scriptSaves={platSaves.filter((x) => x.script_id === selected.id)}
        embedStatus={embedStatus}
        currentUserId={window.RPG_AUTH?.user_id ?? null}
        pendingTab={pendingTab}
        onPendingTabConsumed={() => setPendingTab(null)}
        onPlay={onPlay}
        onContinueSave={onContinueSave}
        onNewGame={onNewGame}
        onChapters={setChaptersOpen}
        onReview={setReviewScript}
        onExtractDone={reload}
        onExport={onExportPack}
        onToggleVisibility={onToggleVisibility}
        onDelete={onDelete}
        onUnsubscribe={onUnsubscribe}
        onEditOverrides={setOverridesScript}
        onReload={(newId) => { reload(); if (newId) setSelectedId(newId); }}
      />
    )
  ) : null;

  const tableEl = (
    <CSTable
      variant="container"
      trackBy="id"
      selectionType="single"
      loadingText={t('scripts.my.loading')}
      loading={!loaded}
      items={pagedScripts}
      selectedItems={selected ? [selected] : []}
      onSelectionChange={({ detail }) => { const x = detail.selectedItems[0]; if (x) setSelectedId(x.id); }}
      onRowClick={({ detail }) => setSelectedId(detail.item.id)}
      empty={<CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>{query ? t('scripts.my.empty_search') : t('scripts.my.empty')}</CSBox>}
      pagination={
        scriptPageCount > 1
          ? <CSPagination currentPageIndex={scriptPage} pagesCount={scriptPageCount} onChange={({ detail }) => setScriptPage(detail.currentPageIndex)} />
          : undefined
      }
      columnDefinitions={[
        { id: 'title', header: t('scripts.my.col_script'), cell: (s) => (
          isInternalPlaceholder(s) ? (
            <div style={{ opacity: 0.55 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <CSBox fontWeight="bold" color="text-status-inactive">{s.title}</CSBox>
                <CSBadge color="grey">{t('scripts.page.placeholder_coming_soon')}</CSBadge>
              </div>
              <CSBox fontSize="body-s" color="text-status-inactive">{s.uid} · {t('scripts.page.placeholder_unavailable')}</CSBox>
            </div>
          ) : (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                <CSBox fontWeight="bold">{s.title}</CSBox>
                {/* 引用徽标:口径见 lib/script-sharing.js(commit id 是整数,不能 .slice;「公开」看 is_public 列,不是 sharing_mode) */}
                {(() => { const ref = referenceInfo(s); return ref && (ref.mode === 'floating-latest'
                  ? <CSBadge color="blue">{t('scripts.share.badge_floating')}</CSBadge>
                  : <CSBadge color="grey">{t('scripts.share.badge_pinned', { id: ref.commitId || '-' })}</CSBadge>); })()}
                {s.forked_from_script_id && <CSBadge color="severity-neutral">fork</CSBadge>}
              </div>
              <CSBox fontSize="body-s" color="text-body-secondary">{s.uid} · {t('scripts.my.updated')} {s.updated_at}</CSBox>
            </div>
          )
        ) },
        { id: 'chapters', header: t('scripts.my.chapters'), cell: (s) => isInternalPlaceholder(s) ? <CSBox color="text-status-inactive">—</CSBox> : (s.chapter_count || 0).toLocaleString() },
        { id: 'words', header: t('scripts.my.words'), cell: (s) => isInternalPlaceholder(s) ? <CSBox color="text-status-inactive">—</CSBox> : `${((s.word_count || 0) / 10000).toFixed(1)} ${t('scripts.my.wan')}` },
        { id: 'mode', header: t('scripts.my.split_mode'), cell: (s) => isInternalPlaceholder(s) ? <CSBox color="text-status-inactive">—</CSBox> : (s.import_report?.mode_label || '—') },
        { id: 'problem', header: t('scripts.my.problem'), cell: (s) => {
          if (isInternalPlaceholder(s)) return <CSStatusIndicator type="pending">{t('scripts.page.placeholder_in_dev')}</CSStatusIndicator>;
          const r = s.readiness || null;
          // phase_rebuild_panel: 没 readiness 字段就不撒谎"就绪",改返 unknown 占位 — 别让破壳数据冒充 ready
          if (!r) {
            if (s.import_report?.problem_label && s.import_report.problem_label !== t('scripts.my.no_problem')) {
              return <CSStatusIndicator type="warning">{s.import_report.problem_label}</CSStatusIndicator>;
            }
            return <CSBox color="text-status-inactive">—</CSBox>;
          }
          if (r.ok) return <CSStatusIndicator type="success">{t('scripts.my.readiness_ready')}</CSStatusIndicator>;
          // 缺项 → ButtonDropdown,每条 = 一个缺失维度,点击 = 选中剧本 + 跳对应 tab
          // key 到 detail panel tab id 的映射:chunks→overview, embeddings→extract,
          // canon→canon-editor (P0 #2: 拆 NPC 与知识库人物), worldbook→world, anchors→timeline
          const tabFor = { chunks: 'overview', embeddings: 'overview', canon: 'canon-editor', worldbook: 'world', anchors: 'timeline' };
          const items = (r.items || []).filter(it => !it.ok).map(it => ({
            id: it.key,
            text: t(`scripts.my.readiness_jump_${it.key}`),
            description: it.total > 0
              ? `${t(`scripts.my.readiness_label_${it.key}`)} ${it.count}/${it.total}`
              : t(`scripts.my.readiness_label_${it.key}`),
          }));
          return (
            <CSButtonDropdown
              variant="inline-icon"
              expandToViewport
              items={items}
              onItemClick={({ detail }) => {
                setSelectedId(s.id);
                const tab = tabFor[detail.id];
                if (tab) setPendingTab(tab);
              }}
              ariaLabel={t('scripts.my.problem')}
            >
              <CSStatusIndicator type="warning">
                {t('scripts.my.readiness_missing', { n: (r.missing || []).length })}
              </CSStatusIndicator>
            </CSButtonDropdown>
          );
        } },
        { id: 'saves', header: t('scripts.my.saves'), cell: (s) => {
          if (isInternalPlaceholder(s)) return <CSBox color="text-status-inactive">—</CSBox>;
          const n = platSaves.filter((x) => x.script_id === s.id).length;
          return n > 0 ? <CSBadge color="green">{t('scripts.my.saves_count', { n })}</CSBadge> : <CSBox color="text-status-inactive">—</CSBox>;
        } },
        { id: 'public', header: t('scripts.my.share'), cell: (s) => s.is_public ? <CSStatusIndicator type="success">{t('scripts.my.is_public')}</CSStatusIndicator> : <CSBox color="text-status-inactive">—</CSBox> },
        { id: 'go', header: '', cell: (s) => {
          if (isInternalPlaceholder(s)) return <CSButton variant="inline-link" iconName="status-pending" disabled>{t('scripts.my.play')}</CSButton>;
          const block = scriptPlayBlockReason(s, t);
          // 反馈#3:列表「开始」也改下拉——选存档继续 / 开新游戏,不再一键直进后台
          const svs = platSaves.filter((x) => x.script_id === s.id);
          // 开局闸只拦「开新游戏」,已有存档照样能从这里继续
          const dd = playDropdownState({ saves: svs, block, t });
          return (
            <CSButtonDropdown variant="normal" expandToViewport disabled={busyId === s.id || dd.disabled}
              items={dd.items}
              onItemClick={({ detail }) => {
                if (detail.id === 'new') { onNewGame(s); return; }
                if (typeof detail.id === 'string' && detail.id.startsWith('continue:')) {
                  const sv = svs.find((x) => String(x.id) === detail.id.slice('continue:'.length));
                  if (sv) onContinueSave(sv);
                }
              }}
            >{dd.disabled ? t('scripts.my.play_blocked') : t('scripts.my.play')}</CSButtonDropdown>
          );
        } },
      ]}
    />
  );

  return (
    <CSSpaceBetween size="l">
      {/* hidden file input lives outside SpaceBetween so it doesn't create a 27px slot-div */}
      <input ref={importPackRef} type="file" accept=".zip" style={{ display: 'none' }} onChange={(e) => onImportPackFile(e.target.files?.[0])} />
      <CSHeader
        variant="h1"
        counter={`(${scripts.length})`}
        description={t('scripts.my.description')}
        actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton iconName="download" loading={importPackBusy} onClick={() => importPackRef.current?.click()}>{t('scripts.my.import_pack')}</CSButton>
            <CSButton variant="primary" iconName="upload" onClick={() => setImportOpen(true)}>{t('scripts.my.import_script')}</CSButton>
          </CSSpaceBetween>
        }
      >{t('scripts.my.title')}</CSHeader>

      <div style={{ maxWidth: 360 }}>
        <CSTextFilter filteringText={query} filteringPlaceholder={t('scripts.my.search_placeholder')}
          onChange={({ detail }) => setQuery(detail.filteringText)} />
      </div>

      {selected
        ? <ResizableSplit storageKey="scripts" top={tableEl} bottom={detailEl} />
        : tableEl}

      <ChaptersModal script={chaptersOpen} onClose={() => setChaptersOpen(null)} onChanged={reload} />
      {importOpen && (
        <div style={{ position: 'fixed', top: 53, left: 0, right: 0, bottom: 0, zIndex: 1000, background: 'var(--bg, #1a1817)', overflow: 'auto' }}>
          <div style={{ position: 'sticky', top: 0, zIndex: 3, background: '#131211', borderBottom: '1px solid #36322d' }}>
            <div style={{ maxWidth: 1240, margin: '0 auto', padding: '13px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 }}>
              <div style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 18, fontWeight: 600, color: '#ebe7df' }}>{t('scripts.my.import_script')}</div>
              <CSButton iconName="close" variant="link" onClick={() => { setImportOpen(false); reload(); }}>{t('common.close')}</CSButton>
            </div>
          </div>
          <div style={{ maxWidth: 1240, margin: '0 auto', padding: '20px 24px 80px' }}>
            <ScriptsImportView embedded onClose={() => { setImportOpen(false); reload(); }} />
          </div>
        </div>
      )}
      <OverridesModal script={overridesScript} onClose={() => setOverridesScript(null)} />
      {reviewScript && (
        <Modal
          open
          eyebrow={t('scripts.review.eyebrow')}
          title={reviewScript.title || t('scripts.review.script_id', { id: reviewScript.id })}
          width={900}
          panelStyle={{ maxHeight: "85vh", overflow: "auto" }}
          onClose={() => setReviewScript(null)}
        >
            <ScriptReview
              scriptId={reviewScript.id}
              initialStatus={reviewScript.review_status}
              onReviewedChange={(sid, rs) => {
                // 复核状态变更 → 同步剧本列表 + 当前 reviewScript,卡片/发布闸读到的是最新值
                setScripts((arr) => arr.map((x) => x.id === sid ? { ...x, review_status: rs } : x));
                setReviewScript((cur) => cur && cur.id === sid ? { ...cur, review_status: rs } : cur);
              }}
            />
        </Modal>
      )}
      {/* Codex P0-2 修复:基于此剧本"新建存档"流。无现成 save 时弹这个 modal,
          走 window.__createAndEnterSave 原子流 (POST /api/saves → activate → 跳页),
          不再走 ContinuePicker 假 save 跳过建档的旧路径。 */}
      <NewGameModal
        open={!!newModalScriptId}
        onClose={() => setNewModalScriptId(null)}
        defaultScriptId={newModalScriptId}
        onConfirm={async (payload) => {
          await window.__createAndEnterSave({
            ...payload,
            script_id: payload.script_id || newModalScriptId,
          });
        }}
      />
    </CSSpaceBetween>
  );
}

export { ScriptsListView, ScriptsLibraryView, ChaptersModal, OverridesModal };
