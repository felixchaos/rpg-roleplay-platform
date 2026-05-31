/* Scripts page — split out of platform-app.jsx (task 52: 拆 platform-app.jsx 按页面).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: PromptModal / Icon / usePlatformData / fmtBytes / fmtN
   以及 saves.jsx 注入的 NewGameModal（顺序保证：platform-app.jsx → saves.jsx → scripts.jsx 在 Platform.html 中按序加载）。 */

import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../game-icons.jsx';
import { PromptModal, usePlatformData, fmtBytes, fmtN, ResizableSplit } from '../platform-app.jsx';
import { CardEditModal, cardSnippet } from './cards.jsx';
import { NewGameModal } from './saves.jsx';
import { ScriptReview } from './script-review.jsx';
// Cloudscape 原生组件(内容迁移,统一基线对齐)
import CSHeader from '@cloudscape-design/components/header';
import CSTable from '@cloudscape-design/components/table';
import CSContainer from '@cloudscape-design/components/container';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSButton from '@cloudscape-design/components/button';
import CSButtonDropdown from '@cloudscape-design/components/button-dropdown';
import CSBox from '@cloudscape-design/components/box';
import CSBadge from '@cloudscape-design/components/badge';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSFormField from '@cloudscape-design/components/form-field';
import CSInput from '@cloudscape-design/components/input';
import CSSelect from '@cloudscape-design/components/select';
import CSFileUpload from '@cloudscape-design/components/file-upload';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import CSAlert from '@cloudscape-design/components/alert';
import CSProgressBar from '@cloudscape-design/components/progress-bar';
import CSModal from '@cloudscape-design/components/modal';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSSegmentedControl from '@cloudscape-design/components/segmented-control';
import CSCards from '@cloudscape-design/components/cards';
import CSTextFilter from '@cloudscape-design/components/text-filter';
import CSTabs from '@cloudscape-design/components/tabs';
import CSPagination from '@cloudscape-design/components/pagination';

function ScriptPreviewModal({ open, busy, data, rule, onClose, onRetryRule, onConfirm }) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(720px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">{t('scripts.import.preview_eyebrow')} · {rule || t('scripts.import.rule_auto')}</div>
            <h2 className="pl-modal-title">{busy ? t('scripts.import.preview_splitting') : (data?.title || t('scripts.import.unnamed'))}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip={t('common.close')}><Icon name="close" size={14} /></button>
        </header>
        {busy ? (
          <div className="pl-validate-progress">
            <div className="pl-validate-step done"><span className="dot ok" /> {t('scripts.import.preview_step1')}</div>
            <div className="pl-validate-step done"><span className="dot ok" /> {t('scripts.import.preview_step2')}</div>
            <div className="pl-validate-step running"><Icon name="spinner" size={12} className="spin" /> {t('scripts.import.preview_step3')}</div>
          </div>
        ) : data ? (
          <>
            <div className="pl-validate-result" style={{flex: "0 0 auto"}}>
              <div className="pl-validate-stat-row">
                <div className="pl-validate-stat">
                  <span className="pl-stat-label">{t('scripts.my.chapters')}</span>
                  <span className="pl-stat-value" style={{fontSize: 20}}>{data.chapter_count}</span>
                </div>
                <div className="pl-validate-stat">
                  <span className="pl-stat-label">{t('scripts.my.words')}</span>
                  <span className="pl-stat-value" style={{fontSize: 20}}>{(data.word_count / 10000).toFixed(1)}<span style={{fontSize: 12, color: "var(--muted)", marginLeft: 3}}>{t('scripts.my.wan')}</span></span>
                </div>
                <div className="pl-validate-stat">
                  <span className="pl-stat-label">{t('scripts.import.confidence')}</span>
                  <span className="pl-stat-value" style={{fontSize: 20, color: data.confidence >= 0.85 ? "var(--ok)" : "var(--warn)"}}>{Math.round(data.confidence * 100)}<span style={{fontSize: 12, marginLeft: 2}}>%</span></span>
                </div>
                <div className="pl-validate-stat">
                  <span className="pl-stat-label">{t('scripts.import.problem')}</span>
                  <span className="pl-stat-value" style={{fontSize: 13, lineHeight: 1.5, fontFamily: "var(--font-sans)", color: data.problem_kind === "ok" ? "var(--ok)" : "var(--warn)"}}>{data.problem_label}</span>
                </div>
              </div>
              {data.notes?.length > 0 && (
                <ul className="pl-flat-list" style={{listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 4}}>
                  {data.notes.map((n, i) => (
                    <li key={i} className="muted-2" style={{fontSize: 11.5, paddingLeft: 14, position: "relative"}}>
                      <span style={{position: "absolute", left: 0}}>•</span> {n}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div style={{overflowY: "auto", overflowX: "hidden", minHeight: 0, flex: "1 1 auto", border: "1px solid var(--line-soft)", borderRadius: "var(--r-2)"}}>
              <table className="pl-table" style={{margin: 0}}>
                <thead><tr><th style={{width: 50}}>#</th><th>{t('scripts.import.col_title')}</th><th>{t('scripts.import.col_volume')}</th><th style={{textAlign: "right"}}>{t('scripts.my.words')}</th></tr></thead>
                <tbody>
                  {data.preview.map(p => (
                    <tr key={p.idx} style={{background: p.ok ? "transparent" : "var(--warn-soft)"}}>
                      <td className="mono muted-2">{String(p.idx).padStart(3, "0")}</td>
                      <td>
                        <strong style={{fontFamily: "var(--font-serif)", fontSize: 14}}>{p.title}</strong>
                        {!p.ok && <span className="pill warn" style={{marginLeft: 8, fontSize: 10.5}}><span className="dot warn" /> {p.hint}</span>}
                      </td>
                      <td className="muted">{p.volume}</td>
                      <td className="mono" style={{textAlign: "right"}}>{p.words.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> {t('scripts.import.preview_footer', { count: data?.preview?.length || 0 })}
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
            {!busy && (
              <>
                <button className="btn ghost" onClick={() => onRetryRule?.("chapter_cn")} data-tip={t('scripts.import.retry_tip')}>
                  <Icon name="refresh" size={12} /> {t('scripts.import.retry_rule')}
                </button>
                <button className="btn primary" onClick={onConfirm} disabled={!data}>
                  <Icon name="check" size={12} /> {t('scripts.import.confirm_import')}
                </button>
              </>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

function ConfidenceBar({ value }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.85 ? "var(--ok)" : value >= 0.7 ? "var(--warn)" : "var(--danger)";
  return (
    <div style={{display: "flex", alignItems: "center", gap: 8}}>
      <div style={{width: 60, height: 4, borderRadius: 999, background: "var(--line-soft)", overflow: "hidden"}}>
        <div style={{width: pct + "%", height: "100%", background: color}} />
      </div>
      <span className="mono" style={{fontSize: 11, color: "var(--muted)"}}>{pct}%</span>
    </div>
  );
}

/* ---------------------------- SCRIPTS -------------------------- */
const SPLIT_RULES = [
  { id: "auto",       labelKey: "scripts.import.rule_auto" },
  { id: "corpus",     labelKey: "scripts.import.rule_corpus" },
  { id: "chapter_cn", labelKey: "scripts.import.rule_chapter_cn" },
  { id: "chapter_en", labelKey: "scripts.import.rule_chapter_en" },
  { id: "number_dot", labelKey: "scripts.import.rule_number_dot" },
  { id: "paren_num",  labelKey: "scripts.import.rule_paren_num" },
  { id: "custom",     labelKey: "scripts.import.rule_custom" },
];

function ScriptsPage({ subPage = "list" }) {
  return (
    <div className="pl-stack">
      {subPage === "import" ? <ScriptsImportView />
        : subPage === "library" ? <ScriptsLibraryView />
        : <ScriptsListView />}
    </div>
  );
}

/* 剧本详情面板 —— 选中某剧本后在列表下方展开(对齐存档页结构)。
   Tabs:概览 / 参数(overrides) / 世界观(worldbook) / NPC 角色卡 / 时间线。
   世界书 / NPC 卡 / 时间线按需懒加载。 */
function ScriptDetailPanel({ script: s, savesCount, embedStatus,
  onPlay, onChapters, onReview, onExtractDone, onEmbed, onExport, onToggleVisibility, onDelete, onEditOverrides }) {
  const { t } = useTranslation();
  const [tab, setTab] = useStatePL('overview');
  const [wb, setWb] = useStatePL(null);
  const [npc, setNpc] = useStatePL(null);
  const [tl, setTl] = useStatePL(null);
  const [ov, setOv] = useStatePL(null);
  const [loading, setLoading] = useStatePL(false);
  const [npcEdit, setNpcEdit] = useStatePL(null); // { card, isNew } | null — NPC 卡编辑(复用 CardEditModal)

  useEffectPL(() => { setWb(null); setNpc(null); setTl(null); setOv(null); setTab('overview'); }, [s.id]);

  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        if (tab === 'world' && wb == null) {
          setLoading(true);
          const r = await window.api.scripts.worldbook(s.id);
          if (!cancelled) setWb(Array.isArray(r) ? r : (r?.items || r?.entries || []));
        } else if (tab === 'npc' && npc == null) {
          setLoading(true);
          const r = await window.api.cards.scriptList(s.id);
          if (!cancelled) setNpc(Array.isArray(r) ? r : (r?.items || r?.cards || []));
        } else if (tab === 'timeline' && tl == null) {
          setLoading(true);
          const r = await window.api.scripts.timeline(s.id);
          if (!cancelled) setTl(r?.phases || []);
        } else if (tab === 'params' && ov == null) {
          setLoading(true);
          const r = await window.api.scripts.getOverrides(s.id);
          if (!cancelled) setOv(r?.data ?? r ?? {});
        }
      } catch (_) {
        if (!cancelled) { if (tab === 'world') setWb([]); else if (tab === 'npc') setNpc([]); else if (tab === 'timeline') setTl([]); else if (tab === 'params') setOv({}); }
      } finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [tab, s.id]);

  const es = embedStatus[s.id];
  const embedLabel = (() => {
    if (!es) return t('scripts.my.embed_none');
    const done = es.chunks.done + es.cards.done + es.worldbook.done;
    const all = es.chunks.total + es.cards.total + es.worldbook.total;
    if (es.running) return t('scripts.my.embed_running', { pct: all ? Math.round(done / all * 100) : 0 });
    return all > 0 && done >= all ? t('scripts.my.embed_done', { n: all }) : t('scripts.my.embed_none');
  })();

  return (
    <CSContainer header={
      <CSHeader variant="h2"
        actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="primary" iconName="caret-right-filled" onClick={() => onPlay(s)}>{t('scripts.my.play_game')}</CSButton>
            <CSButton iconName="file" onClick={() => onChapters(s)}>{t('scripts.my.view_chapters')}</CSButton>
            <CSButton iconName="status-info" onClick={() => onReview(s)}>{t('scripts.my.kb_review')}</CSButton>
            <CSButtonDropdown expandToViewport
              items={[
                { id: 'embed', text: es?.running ? t('scripts.my.embedding') : t('scripts.my.embed_start'), iconName: 'search', disabled: !!es?.running },
                { id: 'export', text: t('scripts.my.action_export'), iconName: 'download' },
                { id: 'visibility', text: s.is_public ? t('scripts.my.action_unpublish') : t('scripts.my.action_publish'), iconName: s.is_public ? 'lock-private' : 'share' },
                { id: 'delete', text: t('scripts.my.action_delete'), iconName: 'remove' },
              ]}
              onItemClick={({ detail }) => {
                const id = detail.id;
                if (id === 'embed') onEmbed(s);
                else if (id === 'export') onExport(s);
                else if (id === 'visibility') onToggleVisibility(s);
                else if (id === 'delete') onDelete(s);
              }}>{t('scripts.my.more')}</CSButtonDropdown>
          </CSSpaceBetween>
        }
      >{s.title}</CSHeader>
    }>
      <CSTabs activeTabId={tab} onChange={({ detail }) => setTab(detail.activeTabId)} tabs={[
        { id: 'overview', label: t('scripts.editor.tab_overview'), content: (
          <CSKeyValuePairs columns={4} items={[
            { label: t('scripts.my.chapters'), value: (s.chapter_count || 0).toLocaleString() },
            { label: t('scripts.my.words'), value: `${((s.word_count || 0) / 10000).toFixed(1)} ${t('scripts.my.wan')}` },
            { label: t('scripts.editor.split_mode'), value: s.import_report?.mode_label || '—' },
            { label: t('scripts.editor.split_confidence'), value: s.import_report?.confidence != null ? `${Math.round(s.import_report.confidence * 100)}%` : '—' },
            { label: t('scripts.editor.saves_count'), value: t('scripts.editor.saves_n', { n: savesCount }) },
            { label: t('scripts.editor.embed_index'), value: embedLabel },
            { label: t('scripts.my.share'), value: s.is_public ? <CSStatusIndicator type="success">{t('scripts.my.is_public')}</CSStatusIndicator> : <CSStatusIndicator type="stopped">{t('scripts.editor.not_public')}</CSStatusIndicator> },
            { label: t('scripts.editor.script_id'), value: <span className="mono">{s.uid}</span> },
          ]} />
        ) },
        { id: 'params', label: t('scripts.editor.tab_params'), content: (
          <CSSpaceBetween size="s">
            <CSBox color="text-body-secondary" fontSize="body-s">{t('scripts.editor.overrides_desc')}</CSBox>
            <pre style={{ margin: 0, padding: '10px 12px', background: 'var(--bg-deep)', border: '1px solid var(--line-soft)', borderRadius: 8, fontSize: 12.5, lineHeight: 1.55, maxHeight: 280, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
              {ov ? JSON.stringify(ov, null, 2) : (loading ? t('common.loading') : '{}')}
            </pre>
            <CSButton iconName="edit" onClick={() => onEditOverrides(s)}>{t('scripts.editor.edit_overrides')}</CSButton>
          </CSSpaceBetween>
        ) },
        { id: 'world', label: t('scripts.editor.tab_world'), content: (
          <CSTable variant="embedded" loading={loading && wb == null} loadingText={t('scripts.editor.loading_worldbook')}
            items={wb || []} trackBy="id"
            columnDefinitions={[
              { id: 'kw', header: t('scripts.editor.wb_col_kw'), cell: (e) => <CSBox fontWeight="bold">{e.keyword || e.title || e.name || e.key || '—'}</CSBox> },
              { id: 'content', header: t('scripts.editor.wb_col_content'), cell: (e) => <CSBox color="text-body-secondary">{String(e.content || e.text || e.description || e.value || '').slice(0, 220)}</CSBox> },
            ]}
            empty={<CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>{t('scripts.editor.wb_empty')}</CSBox>} />
        ) },
        { id: 'npc', label: t('scripts.editor.tab_npc'), content: (
          <CSCards loading={loading && npc == null} loadingText={t('scripts.editor.loading_npc')}
            items={npc || []} trackBy="id"
            cardsPerRow={[{ cards: 1 }, { minWidth: 480, cards: 2 }]}
            header={
              <CSHeader counter={`(${(npc || []).length})`}
                actions={<CSButton iconName="add-plus" onClick={() => setNpcEdit({ card: null, isNew: true })}>{t('scripts.editor.add_npc')}</CSButton>}>
                {t('scripts.editor.tab_npc')}
              </CSHeader>
            }
            cardDefinition={{
              header: (c) => (
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8 }}>
                  <CSBox variant="h3" padding="n">
                    {c.name || t('scripts.editor.unnamed_npc')}
                    {c.full_name && c.full_name !== c.name && (
                      <CSBox display="inline" color="text-status-inactive" fontSize="body-s" padding={{ left: 'xs' }}>{c.full_name}</CSBox>
                    )}
                  </CSBox>
                  {c.enabled === false && <CSStatusIndicator type="stopped">{t('common.disabled')}</CSStatusIndicator>}
                </div>
              ),
              sections: [
                { id: 'identity', content: (c) => (
                  <CSBox color="text-label" fontSize="body-s" fontWeight="bold">{c.identity || c.role || 'NPC'}</CSBox>
                ) },
                { id: 'meta', content: (c) => (
                  ((c.first_revealed_chapter > 1) || (c.importance != null) || (Array.isArray(c.aliases) && c.aliases.length)) ? (
                    <CSSpaceBetween direction="horizontal" size="xxs">
                      {c.first_revealed_chapter > 1 && <CSBadge color="blue">{t('scripts.editor.npc_chapter', { n: c.first_revealed_chapter })}</CSBadge>}
                      {c.importance != null && <CSBadge color="grey">{t('scripts.editor.npc_importance', { n: c.importance })}</CSBadge>}
                      {Array.isArray(c.aliases) && c.aliases.slice(0, 3).map((a) => <CSBadge key={a}>{a}</CSBadge>)}
                    </CSSpaceBetween>
                  ) : null
                ) },
                { id: 'bio', content: (c) => (
                  <CSBox color="text-body-secondary" fontSize="body-s">{cardSnippet(c, 200) || '—'}</CSBox>
                ) },
                { id: 'act', content: (c) => (
                  <CSButton variant="inline-link" iconName="edit" onClick={() => setNpcEdit({ card: c, isNew: false })}>{t('scripts.editor.view_edit')}</CSButton>
                ) },
              ],
            }}
            empty={<CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>{t('scripts.editor.npc_empty')}</CSBox>} />
        ) },
        { id: 'timeline', label: t('scripts.editor.tab_timeline'), content: (
          (loading && tl == null)
            ? <CSBox color="text-body-secondary">{t('common.loading')}</CSBox>
            : (!tl || tl.length === 0)
              ? <CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>{t('scripts.editor.timeline_empty')}</CSBox>
              : <CSSpaceBetween size="l">
                  {tl.map((p, i) => (
                    <div key={i}>
                      <CSBox variant="h4" padding="n">{p.phase_label} <CSBox display="inline" color="text-status-inactive" fontSize="body-s">{t('scripts.editor.chapter_range', { min: p.chapter_min, max: p.chapter_max })}</CSBox></CSBox>
                      {p.summary && <CSBox color="text-body-secondary" fontSize="body-s">{p.summary}</CSBox>}
                      <CSSpaceBetween size="xxs">
                        {(p.anchors || []).map((a) => {
                          const label = (a.story_time_label || '').trim();
                          const summary = String(a.sample_summary || '').replace(/\s+/g, ' ').trim().slice(0, 80);
                          return (
                            <CSBox key={a.anchor_id} fontSize="body-s">
                              <span className="mono" style={{ color: 'var(--accent)' }}>{label || t('scripts.editor.chapter_range', { min: a.chapter_min, max: a.chapter_max })}</span>
                              {summary ? ` · ${summary}${summary.length >= 80 ? '…' : ''}` : ''}
                            </CSBox>
                          );
                        })}
                      </CSSpaceBetween>
                    </div>
                  ))}
                </CSSpaceBetween>
        ) },
        { id: 'extract', label: t('scripts.editor.tab_extract'), content: (
          <KbExtractPanel script={s} onDone={onExtractDone} />
        ) },
      ]} />
      {npcEdit && (
        <CardEditModal
          card={npcEdit.card}
          isNew={npcEdit.isNew}
          kind="npc"
          onClose={() => setNpcEdit(null)}
          onSave={async (payload) => {
            try {
              await window.api.cards.scriptUpsert(s.id, payload);
              window.__apiToast?.(npcEdit.isNew ? t('scripts.toast.npc_added') : t('scripts.toast.npc_saved'), { kind: 'ok' });
              setNpcEdit(null);
              setNpc(null); // 触发 NPC 列表重新拉取
            } catch (e) {
              window.__apiToast?.(t('scripts.toast.save_fail'), { kind: 'danger', detail: e?.message });
            }
          }}
        />
      )}
    </CSContainer>
  );
}

/* 在线剧本库 — 浏览并导入其他用户公开分享的剧本。
   GET /api/scripts/public · POST /api/scripts/public/{id}/clone */
function ScriptsLibraryView() {
  const { t } = useTranslation();
  const [items, setItems] = useStatePL([]);
  const [loading, setLoading] = useStatePL(true);
  const [q, setQ] = useStatePL("");
  const [cloningId, setCloningId] = useStatePL(null);
  const [importedIds, setImportedIds] = useStatePL({}); // 本会话内已导入的 source id

  const reload = React.useCallback(async (query) => {
    setLoading(true);
    try {
      const r = await window.api.scripts.publicList(query ? { q: query } : undefined);
      setItems(Array.isArray(r?.items) ? r.items : []);
    } catch (e) {
      window.__apiToast?.(t('scripts.public.load_fail'), { kind: "danger", detail: e?.message });
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffectPL(() => { reload(""); }, [reload]);

  const onSearch = () => reload(q);

  const onClone = async (s) => {
    setCloningId(s.id);
    try {
      const r = await window.api.scripts.cloneFromPublic(s.id);
      if (r && r.ok === false) throw new Error(r.error || t('scripts.toast.import_fail'));
      window.toast?.(t('scripts.public.clone_ok'), {
        kind: "ok",
        detail: `${s.title} · script #${r?.script_id ?? "?"}`,
        duration: 3000,
      });
      setImportedIds((m) => ({ ...m, [s.id]: true }));
      setItems((arr) => arr.map((x) => x.id === s.id ? { ...x, clone_count: (x.clone_count || 0) + 1 } : x));
      try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.import_fail'), { kind: "danger", detail: e?.message || String(e) });
    } finally {
      setCloningId(null);
    }
  };

  return (
    <CSSpaceBetween size="l">
      <CSHeader
        variant="h1"
        counter={`(${items.length})`}
        description={t('scripts.public.description')}
        actions={<CSButton iconName="refresh" onClick={() => reload(q)}>{t('common.refresh')}</CSButton>}
      >{t('scripts.public.title')}</CSHeader>

      <CSCards
        items={items}
        loading={loading}
        loadingText={t('scripts.public.loading')}
        trackBy="id"
        cardsPerRow={[{ cards: 1 }, { minWidth: 480, cards: 2 }, { minWidth: 920, cards: 3 }]}
        filter={
          <div style={{ minWidth: 320 }}>
            <CSTextFilter filteringText={q} filteringPlaceholder={t('scripts.public.search_placeholder')}
              onChange={({ detail }) => setQ(detail.filteringText)}
              onDelayedChange={onSearch} />
          </div>
        }
        empty={<CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>
          {loading ? t('common.loading') : (q ? t('scripts.public.empty_search') : t('scripts.public.empty'))}
        </CSBox>}
        cardDefinition={{
          header: (s) => (
            <CSSpaceBetween direction="horizontal" size="xs" alignItems="center">
              <CSBox key="t" variant="h3" padding="n">{s.title}</CSBox>
              {(s.mine || importedIds[s.id]) && <CSBadge key="b" color="green">{s.mine ? t('scripts.public.mine_badge') : t('scripts.public.imported_badge')}</CSBadge>}
            </CSSpaceBetween>
          ),
          sections: [
            { id: 'author', content: (s) => (
              <CSBox fontSize="body-s" color="text-body-secondary">{t('scripts.public.shared_by', { author: s.author || s.author_username || t('scripts.public.anon') })}</CSBox>
            ) },
            { id: 'stats', content: (s) => (
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSBadge key="ch">{t('scripts.public.stat_chapters', { n: (s.chapter_count || 0).toLocaleString() })}</CSBadge>
                <CSBadge key="wd">{t('scripts.public.stat_words', { n: ((s.word_count || 0) / 10000).toFixed(0) })}</CSBadge>
                <CSBadge key="cl" color="grey">{t('scripts.public.stat_clones', { n: s.clone_count || 0 })}</CSBadge>
              </CSSpaceBetween>
            ) },
            { id: 'desc', content: (s) => s.description
              ? <CSBox color="text-body-secondary">{s.description}</CSBox> : null },
            { id: 'actions', content: (s) => (
              (s.mine || importedIds[s.id])
                ? <CSButton disabled iconName="check">{s.mine ? t('scripts.public.is_mine') : t('scripts.public.imported_badge')}</CSButton>
                : <CSButton variant="primary" iconName="download"
                    loading={cloningId === s.id} disabled={!!cloningId}
                    onClick={() => onClone(s)}>{t('scripts.public.import_btn')}</CSButton>
            ) },
          ],
        }}
      />
    </CSSpaceBetween>
  );
}

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
  const [query, setQuery] = useStatePL("");
  const [scriptPage, setScriptPage] = useStatePL(1);
  const SCRIPT_PAGE_SIZE = 50;

  // task 51: 触发某 script 的向量化(GET status 也走这里 polling)
  const triggerEmbed = React.useCallback(async (sid) => {
    try {
      const r = await fetch(`${window.__API_BASE || ""}/api/scripts/${sid}/embed`, {
        method: "POST", credentials: "include",
      });
      const j = await r.json();
      if (j.ok === false) {
        window.__apiToast?.(t('scripts.toast.embed_fail'), { kind: "danger", detail: j.error || t('scripts.toast.unknown_error'), duration: 5000 });
        return;
      }
      window.toast?.(t('scripts.toast.embed_started'), { kind: "ok", detail: t('scripts.toast.embed_started_detail'), duration: 3000 });
      setEmbedStatus(s => ({ ...s, [sid]: j.status }));
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.embed_fail'), { kind: "danger", detail: String(e), duration: 3000 });
    }
  }, []);

  // task 51: 自动 poll 所有 running 状态的 script,每 3s 刷一次 progress
  useEffectPL(() => {
    const runningIds = Object.entries(embedStatus).filter(([, v]) => v && v.running).map(([k]) => k);
    if (runningIds.length === 0) return;
    const iv = setInterval(async () => {
      for (const sid of runningIds) {
        try {
          const r = await fetch(`${window.__API_BASE || ""}/api/scripts/${sid}/embed/status`, { credentials: "include" });
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
      await window.api.scripts.delete(s.id);
      window.__apiToast?.(t('scripts.toast.deleted'), { kind: "ok" });
      reload();
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.delete_fail'), { kind: "danger", detail: e?.message });
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

  // 每行操作下拉项 + 向量化状态(task 51)
  const rowActions = (s) => {
    const es = embedStatus[s.id];
    const totalDone = es ? (es.chunks.done + es.cards.done + es.worldbook.done) : 0;
    const totalAll = es ? (es.chunks.total + es.cards.total + es.worldbook.total) : 0;
    const pct = totalAll > 0 ? Math.round((totalDone / totalAll) * 100) : 0;
    const fullyDone = es && !es.running && totalAll > 0 && totalDone >= totalAll;
    const running = es && es.running;
    const embedText = running ? t('scripts.my.embed_running', { pct })
      : fullyDone ? t('scripts.my.embed_done', { n: totalAll })
      : t('scripts.my.embed_start');
    return [
      { id: 'chapters', text: t('scripts.my.action_chapters'), iconName: 'file' },
      { id: 'overrides', text: t('scripts.my.action_overrides'), iconName: 'edit' },
      { id: 'review', text: t('scripts.my.action_review'), iconName: 'status-info' },
      { id: 'embed', text: embedText, iconName: fullyDone ? 'status-positive' : 'gen-ai', disabled: !!running },
      { id: 'visibility', text: s.is_public ? t('scripts.my.action_unpublish') : t('scripts.my.action_publish'), iconName: s.is_public ? 'lock-private' : 'share' },
      { id: 'export', text: t('scripts.my.action_export'), iconName: 'download', disabled: exportingId === s.id },
      { id: 'delete', text: t('scripts.my.action_delete'), iconName: 'remove', disabled: busyId === s.id },
    ];
  };
  const onRowAction = (s, id) => {
    if (id === 'chapters') setChaptersOpen(s);
    else if (id === 'overrides') setOverridesScript(s);
    else if (id === 'review') setReviewScript(s);
    else if (id === 'embed') triggerEmbed(s.id);
    else if (id === 'export') onExportPack(s);
    else if (id === 'visibility') onToggleVisibility(s);
    else if (id === 'delete') onDelete(s);
  };
  const onToggleVisibility = async (s) => {
    const next = !s.is_public;
    if (next && !await window.__confirm({ title: t('scripts.confirm.publish_title'), message: t('scripts.confirm.publish_msg', { title: s.title }), confirmText: t('scripts.confirm.publish_btn') })) return;
    try {
      const r = await window.api.scripts.setVisibility(s.id, next);
      if (r && r.ok === false) throw new Error(r.error || t('scripts.toast.op_fail'));
      window.__apiToast?.(next ? t('scripts.toast.published') : t('scripts.toast.unpublished'), { kind: 'ok', duration: 2000 });
      setScripts((arr) => arr.map((x) => x.id === s.id ? { ...x, is_public: next } : x));
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.op_fail'), { kind: 'danger', detail: e?.message });
    }
  };
  const onPlay = (s) => {
    // 有存档 → 直接进入(__openContinue 现已直接启动新标签);无 → 走建档向导
    const sv = platSaves.find(x => x.script_id === s.id);
    if (sv) window.__openContinue?.(sv);
    else setNewModalScriptId(s.id);
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

  const detailEl = selected ? (
    <ScriptDetailPanel
      script={selected}
      savesCount={platSaves.filter((x) => x.script_id === selected.id).length}
      embedStatus={embedStatus}
      onPlay={onPlay}
      onChapters={setChaptersOpen}
      onReview={setReviewScript}
      onExtractDone={reload}
      onEmbed={(s) => triggerEmbed(s.id)}
      onExport={onExportPack}
      onToggleVisibility={onToggleVisibility}
      onDelete={onDelete}
      onEditOverrides={setOverridesScript}
    />
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
          <div><CSBox fontWeight="bold">{s.title}</CSBox><CSBox fontSize="body-s" color="text-body-secondary">{s.uid} · {t('scripts.my.updated')} {s.updated_at}</CSBox></div>
        ) },
        { id: 'chapters', header: t('scripts.my.chapters'), cell: (s) => (s.chapter_count || 0).toLocaleString() },
        { id: 'words', header: t('scripts.my.words'), cell: (s) => `${((s.word_count || 0) / 10000).toFixed(1)} ${t('scripts.my.wan')}` },
        { id: 'mode', header: t('scripts.my.split_mode'), cell: (s) => s.import_report?.mode_label || '—' },
        { id: 'problem', header: t('scripts.my.problem'), cell: (s) => (
          (!s.import_report?.problem_label || s.import_report.problem_label === t('scripts.my.no_problem'))
            ? <CSStatusIndicator type="success">{t('scripts.my.clean')}</CSStatusIndicator>
            : <CSStatusIndicator type="warning">{s.import_report.problem_label}</CSStatusIndicator>
        ) },
        { id: 'saves', header: t('scripts.my.saves'), cell: (s) => {
          const n = platSaves.filter((x) => x.script_id === s.id).length;
          return n > 0 ? <CSBadge color="green">{t('scripts.my.saves_count', { n })}</CSBadge> : <CSBox color="text-status-inactive">—</CSBox>;
        } },
        { id: 'public', header: t('scripts.my.share'), cell: (s) => s.is_public ? <CSStatusIndicator type="success">{t('scripts.my.is_public')}</CSStatusIndicator> : <CSBox color="text-status-inactive">—</CSBox> },
        { id: 'go', header: '', cell: (s) => <CSButton variant="inline-link" iconName="caret-right-filled" disabled={busyId === s.id} onClick={() => onPlay(s)}>{t('scripts.my.play')}</CSButton> },
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
        <div className="pl-modal-backdrop" onClick={() => setReviewScript(null)}>
          <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{ width: "min(900px, 100%)", maxHeight: "85vh", overflow: "auto" }}>
            <header className="pl-modal-head">
              <div>
                <div className="pl-modal-eyebrow">{t('scripts.review.eyebrow')}</div>
                <h2 className="pl-modal-title">{reviewScript.title || t('scripts.review.script_id', { id: reviewScript.id })}</h2>
              </div>
              <button className="iconbtn" onClick={() => setReviewScript(null)} data-tip={t('common.close')}><Icon name="close" size={14} /></button>
            </header>
            <ScriptReview scriptId={reviewScript.id} />
          </div>
        </div>
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

/* B3: overrides editor — GET/POST /api/v1/scripts/{id}/overrides (JSONB)。
   显示当前 script_overrides 的 raw JSON，支持 edit/save。 */
function OverridesModal({ script, onClose }) {
  const { t } = useTranslation();
  const [raw, setRaw] = useStatePL("");
  const [loading, setLoading] = useStatePL(false);
  const [saving, setSaving] = useStatePL(false);
  const [err, setErr] = useStatePL("");
  const [dirty, setDirty] = useStatePL(false);

  React.useEffect(() => {
    if (!script) return;
    setLoading(true); setErr(""); setRaw(""); setDirty(false);
    (async () => {
      try {
        const r = await window.api.scripts.getOverrides(script.id);
        const data = r?.data ?? r ?? {};
        setRaw(JSON.stringify(data, null, 2));
      } catch (e) {
        setErr(e?.message || t('scripts.editor.load_fail'));
        setRaw("{}");
      } finally {
        setLoading(false);
      }
    })();
  }, [script?.id]);

  if (!script) return null;

  const onSave = async () => {
    let parsed;
    try { parsed = JSON.parse(raw); } catch (e) {
      window.__apiToast?.(t('scripts.editor.json_error'), { kind: "danger", detail: e.message });
      return;
    }
    setSaving(true);
    try {
      await window.api.scripts.saveOverrides(script.id, parsed);
      window.__apiToast?.(t('scripts.toast.saved'), { kind: "ok" });
      setDirty(false);
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.save_fail'), { kind: "danger", detail: e?.message });
    } finally {
      setSaving(false);
    }
  };

  let jsonValid = true;
  try { JSON.parse(raw); } catch (_) { jsonValid = false; }

  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(700px, 96vw)", maxHeight: "90vh", display: "flex", flexDirection: "column"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">{t('scripts.editor.overrides_eyebrow')} · {script.title}</div>
            <h2 className="pl-modal-title">{loading ? t('common.loading') : "script_overrides JSONB"}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip={t('common.close')}><Icon name="close" size={14} /></button>
        </header>
        {err && <div style={{padding: "8px 16px", color: "var(--danger)", fontSize: 13}}>{err}</div>}
        {!loading && (
          <div style={{flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: "0 16px 0"}}>
            <div style={{fontSize: 11.5, color: "var(--muted-2)", marginBottom: 6, paddingTop: 12}}>
              {t('scripts.editor.overrides_hint')}
              {!jsonValid && <span style={{color: "var(--danger)", marginLeft: 8}}>{t('scripts.editor.json_invalid')}</span>}
            </div>
            <textarea
              value={raw}
              onChange={(e) => { setRaw(e.target.value); setDirty(true); }}
              spellCheck={false}
              style={{
                flex: 1, minHeight: 320, fontFamily: "var(--font-mono, monospace)", fontSize: 12.5,
                lineHeight: 1.55, resize: "vertical", background: "var(--surface-2)",
                border: "1px solid " + (jsonValid ? "var(--line-soft)" : "var(--danger)"),
                borderRadius: "var(--r-2)", padding: "10px 12px", color: "var(--text)",
                outline: "none",
              }}
            />
          </div>
        )}
        <footer className="pl-modal-foot" style={{marginTop: 12}}>
          <span className="muted-2" style={{fontSize: 11.5}}>
            GET/POST /api/v1/scripts/{script.id}/overrides
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>{t('common.close')}</button>
            <button className="btn primary" onClick={onSave} disabled={saving || !dirty || !jsonValid}>
              {saving ? <><Icon name="spinner" size={12} className="spin" /> {t('scripts.editor.saving')}</> : <><Icon name="check" size={12} /> {t('common.save')}</>}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

/* task 52：之前剧本只有"alert 章节前 400 字"假预览。补一个真章节浏览/编辑器：
   - GET /api/scripts/{id}/chapters 分页列出
   - GET /api/scripts/{id}/chapter-facts 拿事实摘要（如果有）
   - POST /api/scripts/{id}/chapters/{idx} 重命名 / 改正文
   - POST /api/scripts/{id}/chapters/merge 合并相邻章节
   - POST /api/scripts/{id}/chapters/{idx}/split 拆分单章
   - POST /api/scripts/{id}/resplit 整本重切（rule+pattern）
   全部 BE wrappers 已存，但 FE 之前无入口。 */
function ChaptersModal({ script, onClose, onChanged }) {
  const { t } = useTranslation();
  const [chapters, setChapters] = useStatePL([]);
  const [loading, setLoading] = useStatePL(false);
  const [err, setErr] = useStatePL("");
  const [activeIdx, setActiveIdx] = useStatePL(0);
  const [edit, setEdit] = useStatePL(null); // {idx, title, content}
  const [resplitOpen, setResplitOpen] = useStatePL(false);
  const [reloadTick, setReloadTick] = useStatePL(0);
  React.useEffect(() => {
    if (!script) return;
    setLoading(true); setErr(""); setActiveIdx(0);
    (async () => {
      try {
        const r = await window.api.scripts.chapters(script.id, { limit: 1000, offset: 0 });
        const list = (r && (r.chapters || r.items)) || [];
        setChapters(list);
      } catch (e) { setErr(e?.message || t('scripts.editor.fetch_fail')); }
      finally { setLoading(false); }
    })();
  }, [script?.id, reloadTick]);
  if (!script) return null;
  const cur = chapters[activeIdx];
  const onRename = async () => {
    if (!cur) return;
    const newTitle = await window.__prompt({ title: t('scripts.editor.rename_title'), label: t('scripts.editor.rename_label'), default: cur.title || '' });
    if (!newTitle || newTitle === cur.title) return;
    try {
      await window.api.scripts.updateChapter(script.id, cur.index ?? activeIdx, { title: newTitle });
      window.__apiToast?.(t('scripts.toast.renamed'), { kind: "ok" });
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.(t('scripts.toast.op_fail'), { kind: "danger", detail: e?.message }); }
  };
  const onMergeNext = async () => {
    if (!cur || activeIdx >= chapters.length - 1) return;
    if (!await window.__confirm({ title: t('scripts.editor.merge_title'), message: t('scripts.editor.merge_msg', { a: activeIdx + 1, b: activeIdx + 2 }), confirmText: t('scripts.editor.merge_btn') })) return;
    try {
      await window.api.scripts.mergeChapter(script.id, { first: cur.index ?? activeIdx, second: (chapters[activeIdx + 1]?.index ?? (activeIdx + 1)) });
      window.__apiToast?.(t('scripts.toast.merged'), { kind: "ok" });
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.(t('scripts.toast.op_fail'), { kind: "danger", detail: e?.message }); }
  };
  const onSplit = async () => {
    if (!cur) return;
    const pos = await window.__prompt({ title: t('scripts.editor.split_title'), label: t('scripts.editor.split_label'), default: '' });
    const n = parseInt(pos, 10);
    if (!n || n < 1) return;
    try {
      await window.api.scripts.splitChapter(script.id, cur.index ?? activeIdx, { offset: n });
      window.__apiToast?.(t('scripts.toast.split'), { kind: "ok" });
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.(t('scripts.toast.op_fail'), { kind: "danger", detail: e?.message }); }
  };
  const onResplit = async (vals) => {
    try {
      await window.api.scripts.resplit(script.id, { split_rule: vals.rule || "auto", custom_pattern: vals.pattern || "" });
      window.__apiToast?.(t('scripts.toast.resplit'), { kind: "ok" });
      setResplitOpen(false);
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.(t('scripts.toast.resplit_fail'), { kind: "danger", detail: e?.message }); }
  };
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(960px, 96vw)", maxHeight: "90vh", display: "flex", flexDirection: "column"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">{t('scripts.editor.chapters_eyebrow')} · {script.title}</div>
            <h2 className="pl-modal-title">{loading ? t('common.loading') : t('scripts.editor.chapters_title', { total: chapters.length, cur: activeIdx + 1 })}</h2>
          </div>
          <div style={{display: "flex", gap: 6}}>
            <button className="btn ghost" onClick={() => setResplitOpen(true)} title={t('scripts.editor.resplit_tip')}><Icon name="refresh" size={12} /> {t('scripts.editor.resplit_btn')}</button>
            <button className="iconbtn" onClick={onClose} data-tip={t('common.close')}><Icon name="close" size={14} /></button>
          </div>
        </header>
        {err && <div className="pl-model-empty" style={{padding: "16px"}}><span className="danger">{t('scripts.editor.load_fail_detail', { err })}</span></div>}
        {!err && chapters.length === 0 && !loading && (
          <div className="pl-model-empty" style={{padding: "24px"}}>{t('scripts.editor.chapters_empty')}</div>
        )}
        {chapters.length > 0 && (
          <div style={{display: "grid", gridTemplateColumns: "220px 1fr", gap: 0, flex: 1, minHeight: 0}}>
            <div style={{borderRight: "1px solid var(--line-soft)", overflow: "auto", maxHeight: 480}}>
              {chapters.map((c, i) => (
                <button key={c.index ?? i}
                  className="btn ghost"
                  style={{display: "flex", justifyContent: "flex-start", width: "100%", padding: "8px 12px", borderRadius: 0,
                    background: i === activeIdx ? "var(--accent-soft)" : "transparent",
                    fontWeight: i === activeIdx ? 600 : 400,
                    borderBottom: "1px solid var(--line-soft)"}}
                  onClick={() => setActiveIdx(i)}>
                  <span className="muted-2 mono" style={{minWidth: 36, fontSize: 11}}>#{String(i + 1).padStart(3, "0")}</span>
                  <span style={{overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, textAlign: "left", fontSize: 12.5}}>
                    {c.title || t('scripts.editor.unnamed_chapter')}
                  </span>
                </button>
              ))}
            </div>
            <div style={{overflow: "auto", padding: 16, maxHeight: 480}}>
              {cur && <>
                <div style={{display: "flex", alignItems: "center", gap: 8, marginBottom: 12}}>
                  <strong style={{fontSize: 15}}>{cur.title || t('scripts.editor.unnamed_chapter')}</strong>
                  <span className="muted-2 mono" style={{fontSize: 11}}>{(cur.content || "").length.toLocaleString()} {t('scripts.my.char_unit')}</span>
                  <div style={{marginLeft: "auto", display: "flex", gap: 6}}>
                    <button className="btn ghost" onClick={onRename}><Icon name="edit" size={12} /> {t('scripts.editor.rename_btn')}</button>
                    <button className="btn ghost" onClick={onSplit}><Icon name="branch" size={12} /> {t('scripts.editor.split_chapter_btn')}</button>
                    {activeIdx < chapters.length - 1 && (
                      <button className="btn ghost" onClick={onMergeNext}><Icon name="link" size={12} /> {t('scripts.editor.merge_next_btn')}</button>
                    )}
                  </div>
                </div>
                <pre style={{whiteSpace: "pre-wrap", fontFamily: "var(--font-serif)", fontSize: 13.5, lineHeight: 1.7, margin: 0}}>
                  {(cur.content || "").slice(0, 4000)}{cur.content && cur.content.length > 4000 ? t('scripts.editor.content_truncated') : ""}
                </pre>
              </>}
            </div>
          </div>
        )}
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> GET /api/scripts/{script.id}/chapters · POST /chapters/{`{idx}`} / merge / split / resplit
          </span>
          <button className="btn ghost" onClick={onClose}>{t('common.close')}</button>
        </footer>
      </div>
      <PromptModal
        open={resplitOpen}
        eyebrow={t('scripts.editor.resplit_btn')}
        title={`${script.title} · ${t('scripts.editor.resplit_prompt_title')}`}
        hint="POST /api/scripts/{id}/resplit"
        fields={[
          { key: "rule", label: t('scripts.import.field_rule'), type: "select", default: "auto",
            options: [
              { value: "auto",     label: t('scripts.editor.resplit_rule_auto') },
              { value: "blank",    label: t('scripts.editor.resplit_rule_blank') },
              { value: "marker",   label: t('scripts.editor.resplit_rule_marker') },
              { value: "regex",    label: t('scripts.editor.resplit_rule_regex') },
            ] },
          { key: "pattern", label: t('scripts.import.field_custom_regex'), placeholder: t('scripts.import.field_custom_regex_placeholder') },
        ]}
        submitLabel={t('scripts.editor.resplit_submit')}
        onClose={() => setResplitOpen(false)}
        onConfirm={onResplit}
      />
    </div>
  );
}

const IMPORT_STAGES = [
  { id: "split",    labelKey: "scripts.import.stage_split",    hintKey: "scripts.import.stage_split_hint",    tok_per_chap: 0 },
  { id: "save",     labelKey: "scripts.import.stage_save",     hintKey: "scripts.import.stage_save_hint",     tok_per_chap: 0 },
  { id: "extract",  labelKey: "scripts.import.stage_extract",  hintKey: "scripts.import.stage_extract_hint",  tok_per_chap: 120 },
  { id: "card",     labelKey: "scripts.import.stage_card",     hintKey: "scripts.import.stage_card_hint",     tok_per_chap: 60 },
  { id: "world",    labelKey: "scripts.import.stage_world",    hintKey: "scripts.import.stage_world_hint",    tok_per_chap: 90 },
  { id: "timeline", labelKey: "scripts.import.stage_timeline", hintKey: "scripts.import.stage_timeline_hint", tok_per_chap: 40 },
];

function ScriptsImportView({ embedded = false, onClose } = {}) {
  void onClose;
  const { t } = useTranslation();
  const [rule, setRule] = useStatePL("auto");
  const [pattern, setPattern] = useStatePL("");
  const [title, setTitle] = useStatePL("");
  const [job, setJob] = useStatePL(null); // { id, status, stages, currentStage, file, ... } | null
  const [estimate, setEstimate] = useStatePL(null);
  const [previewBusy, setPreviewBusy] = useStatePL(false);
  const [selectedFile, setSelectedFile] = useStatePL(null);
  const [dragOver, setDragOver] = useStatePL(false);
  const fileInputRef = React.useRef(null);
  const tickRef = React.useRef(null);

  // Restore job from localStorage on mount (page-refresh resilient)
  React.useEffect(() => {
    try {
      const cached = localStorage.getItem("rpg.import.job");
      if (cached) {
        const j = JSON.parse(cached);
        if (j && j.status === "running") setJob(j);
        else if (j && j.status === "estimating") setJob(j);
      }
    } catch {}
  }, []);

  // Persist job state
  React.useEffect(() => {
    if (job) localStorage.setItem("rpg.import.job", JSON.stringify(job));
    else localStorage.removeItem("rpg.import.job");
  }, [job]);

  // task 39: real job 必须轮询后端拿真实进度。之前 job.real=true 直接 return 没轮询,
  // 所以 UI 永远卡 0%/0s,直到用户手动刷新页面才能看到剧本已 import 完。
  // backend ks_<sid>_<hex> job kind=knowledge_sync,目前是 1-stage(done/error),
  // 简化映射:status==done → 全部 stages 标 done; status==error → 标 error。
  React.useEffect(() => {
    if (!job || !job.real || job.status !== "running") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const resp = await window.api.scripts.jobStatus(job.id);
        if (cancelled) return;
        const jb = resp && (resp.job || resp);
        if (!jb || !jb.status) return;
        if (jb.status === "done") {
          setJob(j => j ? { ...j,
            status: "done",
            finished_at: Date.now(),
            stages: j.stages.map(s => ({ ...s, status: "done", progress: 1, tokens_used: s.tokens_est, done_at: Date.now() })),
            knowledge_result: jb.usage_actual?.result || null,
          } : j);
          window.toast?.(t('scripts.import.result_done'), { kind: "ok", detail: `script #${jb.script_id}`, duration: 2400 });
          try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
        } else if (jb.status === "error" || jb.status === "failed") {
          setJob(j => j ? { ...j, status: "cancelled", finished_at: Date.now(), error: jb.error || t('scripts.toast.import_fail') } : j);
          window.__apiToast?.(t('scripts.toast.import_fail'), { kind: "danger", detail: jb.error || t('scripts.toast.unknown_error'), duration: 4000 });
        }
      } catch (_) { /* 单次失败不影响下一次轮询 */ }
    };
    poll();
    const iv = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [job?.id, job?.real, job?.status]);

  // task 17/18/19: 之前这个 setInterval 是「假任务模拟器」：
  //   - 进度条 ticks 是 Math.random，假的
  //   - 完成时直接把假行塞进 window.MOCK_PLATFORM.scripts → 这是 task 19 真后端只有 1 条
  //     却 UI 显示 5 条的原因
  //   - 完成 toast 在 setJob 的 updater 里同步发出 → React 抱怨「setState while rendering」
  // 现在：real 导入由后端同步返回（task 17 之后），不需要模拟；只在没接后端的 demo
  // 模式（job.real=false 且非 done/cancelled）才走一次性 mock tick，不再 mutate MOCK_PLATFORM。
  React.useEffect(() => {
    if (!job || job.status !== "running" || job.real) {
      if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
      return;
    }
    // demo / 离线预览模式：纯视觉 tick，不动 MOCK_PLATFORM，不在 updater 里发 toast
    tickRef.current = setInterval(() => {
      setJob(j => {
        if (!j || j.status !== "running" || j.real) return j;
        const stages = j.stages.map(s => ({ ...s }));
        let cur = j.currentStage;
        const s = stages[cur];
        if (!s) return j;
        s.progress = Math.min(1, s.progress + 0.05 + Math.random() * 0.07);
        if (s.progress >= 1) {
          s.progress = 1; s.status = "done";
          s.tokens_used = s.tokens_est; s.done_at = Date.now();
          if (cur + 1 < stages.length) {
            stages[cur + 1].status = "running";
            stages[cur + 1].started_at = Date.now();
            cur += 1;
          } else {
            return { ...j, stages, currentStage: cur, status: "done", finished_at: Date.now(), demo: true };
          }
        }
        return { ...j, stages, currentStage: cur };
      });
    }, 500);
    return () => { if (tickRef.current) clearInterval(tickRef.current); };
  }, [job?.status, job?.real]);

  // task 49：原 fakeFile = {chapters: 162, words: 410_000} 是凭空写的"示例规模"，
  // 不选文件时会展示出来误导用户。删除 fakeFile，未选文件时 startEstimate 直接
  // 提示"请先选择本地文件"，不假装真实，不生成假预算。

  const onPickFile = (file) => {
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
      window.__apiToast?.(t('scripts.import.file_too_large'), { kind: "danger", detail: t('scripts.import.file_max_size'), duration: 2400 });
      return;
    }
    setSelectedFile(file);
    if (!title) setTitle(file.name.replace(/\.(txt|md)$/i, ""));
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onPickFile(f);
  };

  // task 16: 读 File → 纯 base64（去掉 data URL 前缀），喂给后端 decode_upload()。
  // 之前发的 {rule, pattern, title, filename, size} 后端 file=None → 必 400 → 静默回退到 fakeFile。
  const readFileAsBase64 = (file) => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const s = String(r.result || "");
      const idx = s.indexOf(",");
      resolve(idx >= 0 ? s.slice(idx + 1) : s);
    };
    r.onerror = () => reject(r.error || new Error(t('scripts.import.file_read_fail')));
    r.readAsDataURL(file);
  });

  const startEstimate = async () => {
    setPreviewBusy(true);
    setEstimate(null);
    // task 49：不选文件时彻底不出预算（之前给假的 162 章 41 万字）
    if (!selectedFile) {
      setEstimate({
        file: null, chapters: 0, words: 0,
        stages: [], totalTokens: 0, totalSec: 0, cost: 0,
        model: "—",
        warnings: [t('scripts.import.warn_no_file')],
        previewError: t('scripts.import.no_file_selected'),
      });
      setPreviewBusy(false);
      return;
    }
    // 选了真实文件：必须打真后端；失败就给用户看清楚错误，绝不回退 fakeFile
    let result = null;
    try {
      const base64 = await readFileAsBase64(selectedFile);
      const body = {
        file: { name: selectedFile.name, base64 },
        split_rule: rule || "auto",
        custom_pattern: pattern || "",
        sample_limit: 20,
      };
      result = await window.api.scripts.preview(body);
    } catch (e) {
      const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
      window.__apiToast?.(t('scripts.toast.preview_fail'), { kind: "danger", detail, duration: 5000 });
      setEstimate({
        file: { name: selectedFile.name, size: selectedFile.size, chapters: 0, words: 0 },
        chapters: 0, words: 0,
        stages: [], totalTokens: 0, totalSec: 0, cost: 0,
        model: "—",
        warnings: [t('scripts.import.preview_fail_detail', { detail })],
        previewError: detail,
      });
      setPreviewBusy(false);
      return;
    }
    // 成功路径：用后端真实数字
    const chapters = Number(result.total_chapters) || (Array.isArray(result.preview) ? result.preview.length : 0);
    const words = Number(result.total_words) || 0;
    const stages = IMPORT_STAGES.map(s => ({
      id: s.id, label: t(s.labelKey), hint: t(s.hintKey),
      tokens_est: s.tok_per_chap * Math.max(chapters, 1),
      time_est_sec: Math.round(s.tok_per_chap * Math.max(chapters, 1) / 800),
    }));
    const totalTokens = stages.reduce((a, s) => a + s.tokens_est, 0);
    const totalSec = stages.reduce((a, s) => a + s.time_est_sec, 0);
    const cost = totalTokens * 0.75 / 1_000_000;
    const warnings = [];
    if (Array.isArray(result.warnings)) warnings.push(...result.warnings);
    if (result.report && result.report.mode_label) {
      warnings.push(`切分模式：${result.report.mode_label}（置信 ${result.report.confidence ?? "—"}）`);
    }
    setEstimate({
      file: { name: selectedFile.name, size: selectedFile.size, chapters, words },
      chapters, words,
      stages, totalTokens, totalSec, cost,
      model: result.model || "GPT-4o · RPG 调优",
      preview: result.preview,
      report: result.report,
      warnings,
    });
    setPreviewBusy(false);
  };

  const startImport = async () => {
    // task 17: 真正打通分片上传 → /api/scripts/import 流水线。
    // 之前发的 init 字段 {size, kind, chunk_size} 全不对（后端要 total_bytes/total_chunks）→ 400。
    // 之前任何一步失败仍会创建 fake job 让 UI 假装在跑 → 用户误以为成功。
    // 现在：选了真实文件就必须真传成功；任一步失败 toast 报错并停止，不再造 job。
    const CHUNK_SIZE = 1024 * 1024;
    if (selectedFile) {
      let uploadId = null;
      try {
        const totalBytes = selectedFile.size;
        const totalChunks = Math.max(1, Math.ceil(totalBytes / CHUNK_SIZE));
        const init = await window.api.uploads.init({
          filename: selectedFile.name,
          total_bytes: totalBytes,
          total_chunks: totalChunks,
        });
        uploadId = init.upload_id || init.id;
        if (!uploadId) throw new Error(t('scripts.import.no_upload_id'));
        for (let i = 0; i < totalChunks; i++) {
          const blob = selectedFile.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
          await window.api.uploads.chunk(uploadId, blob, i);
        }
        await window.api.uploads.finish(uploadId, {});
        const importResp = await window.api.scripts.importScript({
          upload_id: uploadId,
          title: title || selectedFile.name.replace(/\.(txt|md)$/i, ""),
          split_rule: rule || "auto",
          custom_pattern: pattern || "",
        });
        if (!importResp || importResp.ok === false) {
          throw new Error((importResp && (importResp.error || importResp.detail)) || t('scripts.import.api_fail'));
        }
        const sc = importResp.script || {};
        // task 41: importScript 只跑简化 sync (facts/chunks),没跑 LLM cards/worldbook。
        // 必须额外调 import-pipeline 启动完整 5-stage LLM 流水线,否则角色卡 + 世界书全是 0,
        // 后面 chat 上下文严重缺失。优先用 imp_ job_id 跟踪进度(完整 5-stage),
        // ks_ job_id 是降级 fallback。
        let pipelineJobId = null;
        try {
          const pipelineResp = await window.api.scripts.importPipeline(sc.id, {
            enable_cards: true,
            enable_worldbook: true,
          });
          if (pipelineResp && pipelineResp.ok !== false) {
            pipelineJobId = pipelineResp.job_id;
          }
        } catch (e) {
          // pipeline 启动失败不致命,fallback 用 ks_ job_id 至少能看到 facts/chunks 进度
          console.warn("import-pipeline failed to start:", e);
        }
        const stages = estimate.stages.map((s, i) => ({
          ...s,
          status: i === 0 ? "running" : "pending",
          progress: 0, tokens_used: 0,
          started_at: i === 0 ? Date.now() : null, done_at: null,
        }));
        const j = {
          id: pipelineJobId
            || (importResp.knowledge && importResp.knowledge.job_id)
            || ("script_" + (sc.id || "?")),
          file: estimate.file,
          title: sc.title || title || estimate.file.name,
          script_id: sc.id,
          mode: (() => { const _r = SPLIT_RULES.find(r => r.id === rule); return _r ? t(_r.labelKey) : rule; })(),
          stages, currentStage: 0,
          totalTokens: estimate.totalTokens,
          status: "running",
          started_at: Date.now(),
          real: true,
        };
        setJob(j);
        setEstimate(null);
        // 通知外部 ScriptsPage 刷新真实列表（task 19 联动）
        try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
        window.toast && window.toast(t('scripts.toast.import_ok'), {
          kind: "ok",
          // Codex #8:不假装"向量库"。后端 _embed_query() 是 stub (返回 None),
          // pgvector 查询自动退化到 ILIKE 关键字匹配 + 章节摘要召回。
          // 文案如实表达,避免用户误以为已建立完整向量库。
          detail: t('scripts.toast.import_ok_detail', { id: sc.id, title: sc.title || "" }),
          duration: 3000,
        });
      } catch (e) {
        // 取消任何已经初始化的 upload，让服务器释放临时块
        if (uploadId) { try { await window.api.uploads.cancel(uploadId); } catch (_) {} }
        const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
        window.__apiToast?.(t('scripts.toast.import_fail'), { kind: "danger", detail, duration: 5000 });
        // 关键：不要建 fake job 让用户误以为在跑
        setJob(null);
        // estimate 保留，以便用户修改设置后重试
      }
      return;
    }
    // 没选文件：仅在 isMockEstimate（明确示例）下允许 demo job
    if (estimate && estimate.isMockEstimate) {
      window.__apiToast?.(t('scripts.toast.mock_warn'), { kind: "warn", detail: t('scripts.toast.mock_warn_detail'), duration: 3000 });
      return;
    }
    window.__apiToast?.(t('scripts.toast.select_file_first'), { kind: "warn" });
  };

  const cancelJob = async () => {
    if (!job) return;
    if (job.real) {
      try { await window.api.scripts.jobCancel(job.id); } catch (e) {}
    }
    setJob(j => ({ ...j, status: "cancelled", cancelled_at: Date.now() }));
    window.toast?.(t('scripts.toast.import_cancelled'), { kind: "warn", detail: "job " + job.id, duration: 2400 });
  };

  const dismissJob = () => {
    setJob(null);
  };

  const ruleOpt = SPLIT_RULES.find(r => r.id === rule) || SPLIT_RULES[0];
  const ruleLabel = t(ruleOpt.labelKey);
  const fileName = (selectedFile && selectedFile.name) || (estimate && estimate.file && estimate.file.name) || null;
  const jobRunning = job && job.status !== 'done' && job.status !== 'cancelled';

  return (
    <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
      {/* 左:模块平铺 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <CSSpaceBetween size="l">
          {jobRunning && <ImportJobBanner job={job} onCancel={cancelJob} />}
          {job && (job.status === 'done' || job.status === 'cancelled') && (
            <ImportJobResult job={job} onDismiss={dismissJob} onReuse={() => { setJob(null); setEstimate(null); }} />
          )}

          <CSContainer header={<CSHeader variant="h2" description={t('scripts.import.basic_desc')}>{t('scripts.import.basic_title')}</CSHeader>}>
            <CSColumnLayout columns={2}>
              <CSFormField label={t('scripts.import.field_title')} description={t('scripts.import.field_title_desc')}>
                <CSInput value={title} onChange={({ detail }) => setTitle(detail.value)} placeholder={t('scripts.import.field_title_desc')} />
              </CSFormField>
              <CSFormField label={t('scripts.import.field_rule')}>
                <CSSelect selectedOption={{ value: ruleOpt.id, label: ruleLabel }}
                  options={SPLIT_RULES.map(r => ({ value: r.id, label: t(r.labelKey) }))}
                  onChange={({ detail }) => setRule(detail.selectedOption.value)} />
              </CSFormField>
              <div style={{ gridColumn: '1 / -1' }}>
                <CSFormField label={t('scripts.import.field_custom_regex')} description={t('scripts.import.field_custom_regex_desc')}>
                  <CSInput value={pattern} onChange={({ detail }) => setPattern(detail.value)}
                    disabled={rule !== 'custom'} placeholder={t('scripts.import.field_custom_regex_placeholder')} />
                </CSFormField>
              </div>
            </CSColumnLayout>
          </CSContainer>

          <CSContainer header={<CSHeader variant="h2" description={t('scripts.import.file_desc')}>{t('scripts.import.file_title')}</CSHeader>}>
            <CSFileUpload
              value={selectedFile ? [selectedFile] : []}
              onChange={({ detail }) => {
                const f = detail.value?.[0];
                if (f) onPickFile(f); else setSelectedFile(null);
              }}
              accept=".txt,.md"
              showFileSize
              constraintText={t('scripts.import.file_constraint')}
              i18nStrings={{
                uploadButtonText: () => t('scripts.import.file_btn'),
                dropzoneText: () => t('scripts.import.file_drop'),
                removeFileAriaLabel: (i) => t('scripts.import.file_remove', { i: i + 1 }),
                limitShowFewer: t('scripts.import.file_collapse'),
                limitShowMore: t('scripts.import.file_expand'),
                errorIconAriaLabel: t('scripts.import.file_error'),
              }}
            />
          </CSContainer>

          {estimate && !job && (
            <ImportEstimateView estimate={estimate} rule={rule} hideActions />
          )}
        </CSSpaceBetween>
      </div>

      {/* 右:概要 + 主操作(sticky) */}
      <div style={{ width: 320, flexShrink: 0, position: 'sticky', top: 72 }}>
        <CSContainer header={<CSHeader variant="h2">{t('scripts.import.summary_title')}</CSHeader>}>
          <CSSpaceBetween size="m">
            <CSKeyValuePairs columns={1} items={[
              { label: t('scripts.import.summary_file'), value: fileName || '—' },
              { label: t('scripts.import.field_rule'), value: ruleLabel },
              ...(estimate ? [
                { label: t('scripts.my.chapters'), value: String(estimate.chapters) },
                { label: t('scripts.my.words'), value: `${(estimate.words / 10000).toFixed(1)} ${t('scripts.my.wan')}` },
                { label: t('scripts.import.est_cost'), value: <CSBox color="text-status-info" fontWeight="bold">${estimate.cost.toFixed(2)}</CSBox> },
                { label: t('scripts.import.est_time'), value: t('scripts.import.est_time_val', { min: Math.round(estimate.totalSec / 60) }) },
              ] : []),
            ]} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {!estimate && (
                <CSButton variant="primary" iconName="search" loading={previewBusy} disabled={!selectedFile || !!job} onClick={startEstimate}>
                  {previewBusy ? t('scripts.import.calculating') : t('scripts.import.preview_split')}
                </CSButton>
              )}
              {estimate && !job && (
                <>
                  <CSButton variant="primary" iconName="check" onClick={startImport}>{t('scripts.import.confirm_import_bg')}</CSButton>
                  <CSButton onClick={() => setEstimate(null)}>{t('scripts.import.re_estimate')}</CSButton>
                </>
              )}
              {jobRunning && <CSBox color="text-body-secondary" fontSize="body-s">{t('scripts.import.importing_bg')}</CSBox>}
              {onClose && <CSButton variant="link" onClick={onClose}>{t('common.close')}</CSButton>}
            </div>
          </CSSpaceBetween>
        </CSContainer>
      </div>
    </div>
  );
}

function ImportJobBanner({ job, onCancel }) {
  const { t } = useTranslation();
  const overallProgress = job.stages.reduce((a, s) => a + s.progress, 0) / job.stages.length;
  const elapsed = Math.round((Date.now() - job.started_at) / 1000);
  return (
    <CSContainer
      header={
        <CSHeader
          variant="h2"
          description={t('scripts.import.banner_desc', { id: job.id, elapsed })}
          actions={<CSButton iconName="close" onClick={onCancel}>{t('scripts.import.cancel_import')}</CSButton>}
        >
          <CSStatusIndicator type="in-progress">{t('scripts.import.importing')} · {job.title}</CSStatusIndicator>
        </CSHeader>
      }
    >
      <CSSpaceBetween size="m">
        <CSProgressBar value={overallProgress * 100} label={t('scripts.import.overall_progress')} />
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
          {job.stages.map((s, i) => {
            const type = s.status === 'done' ? 'success' : s.status === 'running' ? 'in-progress' : 'pending';
            const meta = s.status === 'running' ? `${Math.round(s.progress * 100)}%`
              : s.status === 'done' ? `${fmtN(s.tokens_used)} tok`
              : `~${fmtN(s.tokens_est)} tok`;
            return (
              <div key={s.id}>
                <CSStatusIndicator type={type}>{String(i + 1).padStart(2, '0')} · {s.label}</CSStatusIndicator>
                <CSBox fontSize="body-s" color="text-body-secondary">{s.hint} · {meta}</CSBox>
              </div>
            );
          })}
        </div>
      </CSSpaceBetween>
    </CSContainer>
  );
}

function ImportJobResult({ job, onDismiss, onReuse }) {
  const { t } = useTranslation();
  const ok = job.status === "done";
  const totalTokens = job.stages.reduce((a, s) => a + (s.tokens_used || 0), 0);
  return (
    <CSAlert
      type={ok ? 'success' : 'warning'}
      dismissible
      onDismiss={onDismiss}
      header={`${ok ? t('scripts.import.result_done') : t('scripts.import.result_cancelled')} · ${job.title}`}
      action={
        <CSSpaceBetween direction="horizontal" size="xs">
          {ok && <CSButton variant="primary" href="#scripts" onClick={onDismiss}>{t('scripts.import.go_manage')}</CSButton>}
          <CSButton onClick={onReuse}>{ok ? t('scripts.import.import_another') : t('scripts.import.retry')}</CSButton>
        </CSSpaceBetween>
      }
    >
      {ok ? t('scripts.import.tok_consumed', { n: fmtN(totalTokens) }) : `job ${job.id}`}
    </CSAlert>
  );
}

function ImportEstimateView({ estimate, rule, onCancel, onConfirm, hideActions = false }) {
  const { t } = useTranslation();
  const ruleEntry = SPLIT_RULES.find(r => r.id === rule);
  const ruleLabel = ruleEntry ? t(ruleEntry.labelKey) : rule;
  return (
    <CSContainer
      header={
        <CSHeader
          variant="h2"
          description={t('scripts.import.estimate_desc', { file: estimate.file.name, rule: ruleLabel, model: estimate.model })}
          actions={hideActions ? undefined : (
            <CSSpaceBetween direction="horizontal" size="xs">
              <CSButton onClick={onCancel}>{t('common.cancel')}</CSButton>
              <CSButton variant="primary" iconName="check" onClick={onConfirm}>{t('scripts.import.confirm_import_bg')}</CSButton>
            </CSSpaceBetween>
          )}
        >{t('scripts.import.estimate_title')}</CSHeader>
      }
    >
      <CSSpaceBetween size="l">
        <CSKeyValuePairs columns={5} items={[
          { label: t('scripts.my.chapters'), value: String(estimate.chapters) },
          { label: t('scripts.my.words'), value: `${(estimate.words / 10000).toFixed(1)} ${t('scripts.my.wan')}` },
          { label: t('scripts.import.est_tokens'), value: fmtN(estimate.totalTokens) },
          { label: t('scripts.import.est_cost'), value: <CSBox color="text-status-info" fontWeight="bold">${estimate.cost.toFixed(2)}</CSBox> },
          { label: t('scripts.import.est_time'), value: t('scripts.import.est_time_val', { min: Math.round(estimate.totalSec / 60) }) },
        ]} />
        <CSTable
          variant="embedded"
          items={estimate.stages}
          trackBy="id"
          columnDefinitions={[
            { id: 'n', header: '#', cell: (s) => estimate.stages.indexOf(s) + 1, width: 50 },
            { id: 'label', header: t('scripts.import.stage_col'), cell: (s) => <CSBox fontWeight="bold">{s.label}</CSBox> },
            { id: 'hint', header: t('scripts.import.hint_col'), cell: (s) => s.hint },
            { id: 'tok', header: t('scripts.import.est_tokens'), cell: (s) => fmtN(s.tokens_est) },
            { id: 'time', header: t('scripts.import.est_time'), cell: (s) => s.time_est_sec < 60 ? s.time_est_sec + 's' : Math.round(s.time_est_sec / 60) + 'min' },
          ]}
        />
        {estimate.warnings?.length > 0 && (
          <CSAlert type="warning" header={t('scripts.import.warnings_header')}>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {estimate.warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </CSAlert>
        )}
      </CSSpaceBetween>
    </CSContainer>
  );
}

/* ── LLM 知识提取(异步 job + import-jobs SSE) ─────────────────
   后端 POST /scripts/{id}/llm-extract 立即返 job_id,kind='llm_extract',
   复用 streamImport SSE。4 阶段:seed / arc_extract(或 per_chapter)/ resolve / embed。
   完成后剧本 review_status 自动重置为 unreviewed(需复核)。 */
const _EXTRACT_STAGE_LABEL_KEYS = {
  seed: 'scripts.review.stage_seed',
  arc_extract: 'scripts.review.stage_arc_extract',
  per_chapter: 'scripts.review.stage_per_chapter',
  resolve: 'scripts.review.stage_resolve',
  embed: 'scripts.review.stage_embed',
};
function _stageIndicator(status) {
  if (status === 'done') return 'success';
  if (status === 'running') return 'in-progress';
  if (status === 'error' || status === 'failed') return 'error';
  return 'pending';
}

function KbExtractPanel({ script, onDone }) {
  const { t } = useTranslation();
  const sid = script.id;
  const [algorithm, setAlgorithm] = useStatePL('arc');
  const [model, setModel] = useStatePL('deepseek-v4-flash');
  const [apiId, setApiId] = useStatePL('deepseek');
  const [targetArcs, setTargetArcs] = useStatePL('40');
  const [concurrency, setConcurrency] = useStatePL('15');
  const [authorEra, setAuthorEra] = useStatePL('');
  const [maxUsd, setMaxUsd] = useStatePL('10');
  const [estimate, setEstimate] = useStatePL(null);
  const [estimating, setEstimating] = useStatePL(false);
  const [job, setJob] = useStatePL(null);
  const [phase, setPhase] = useStatePL('config'); // config | running | done | error
  const [err, setErr] = useStatePL('');
  const [apis, setApis] = useStatePL([]); // 模型管理:已配置的 provider + 模型
  const esRef = React.useRef(null);

  React.useEffect(() => () => { try { esRef.current && esRef.current.close && esRef.current.close(); } catch (_) {} }, []);

  // 接入模型管理系统:拉 /api/models,默认套用「叙事提取器」已配的 provider/model
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [profile, models] = await Promise.all([
          window.api.account.profile().catch(() => ({})),
          window.api.models.list().catch(() => ({})),
        ]);
        if (cancelled) return;
        const list = models?.models?.apis || (Array.isArray(models?.apis) ? models.apis : []) || [];
        setApis(Array.isArray(list) ? list : []);
        const p = (profile && profile.preferences) || {};
        if (p['extractor.api_id']) setApiId(p['extractor.api_id']);
        if (p['extractor.model_real_name']) setModel(p['extractor.model_real_name']);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const cfgBody = () => ({
    algorithm,
    model: (model || '').trim() || 'deepseek-v4-flash',
    api_id: (apiId || '').trim() || 'deepseek',
    target_arcs: Number(targetArcs) || 40,
    concurrency: Number(concurrency) || 15,
    author_era: (authorEra || '').trim(),
    max_book_usd: Number(maxUsd) || 10,
  });

  const doEstimate = async () => {
    setEstimating(true); setEstimate(null); setErr('');
    try {
      const r = await window.api.scripts.llmExtractEstimate(sid, cfgBody());
      setEstimate(r);
    } catch (e) {
      setErr((e && (e.payload?.error || e.message)) || t('scripts.review.estimate_fail'));
    } finally { setEstimating(false); }
  };

  const startStream = (jobId) => {
    setPhase('running');
    setJob((j) => j || { kind: 'llm_extract', status: 'running', stages: [], job_id: jobId });
    esRef.current = window.api.scripts.streamImport(jobId, {
      on_message: (jb) => { if (jb && typeof jb === 'object') setJob({ ...jb, job_id: jb.job_id || jb.id || jobId }); },
      on_done: () => {
        setPhase('done');
        window.__apiToast?.(t('scripts.review.extract_done'), { kind: 'ok', detail: t('scripts.review.extract_done_detail'), duration: 3200 });
        try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
        onDone && onDone();
      },
      on_error: () => { /* SSE 在 done 后会正常关闭,不当错误处理 */ },
    });
  };

  const doStart = async () => {
    setErr('');
    try {
      const r = await window.api.scripts.llmExtract(sid, { ...cfgBody(), confirmed: true });
      const jid = r && (r.job_id || r.id);
      if (jid) startStream(jid);
      else { setErr((r && r.error) || t('scripts.review.dispatch_fail')); setPhase('error'); }
    } catch (e) {
      const p = (e && e.payload) || {};
      if (p.job_id) { startStream(p.job_id); return; } // 409 复用已在跑的任务
      setErr(p.error || (e && e.message) || t('scripts.review.dispatch_fail'));
      setPhase('error');
    }
  };

  const doCancel = async () => {
    const jid = job && job.job_id;
    if (!jid) return;
    try { await window.api.scripts.jobCancel(jid); window.__apiToast?.(t('scripts.review.cancel_requested'), { kind: 'warn', duration: 2400 }); } catch (_) {}
  };

  const stages = (job && Array.isArray(job.stages)) ? job.stages : [];
  const overall = job ? (job.overall_progress || 0) : 0;
  const overallTotal = job ? (job.overall_total || 4) : 4;
  const usage = job && job.usage_actual;

  // 模型管理:provider + 模型联动下拉
  const currentApi = apis.find(a => (a.api_id || a.id) === apiId) || null;
  const modelList = (currentApi && (currentApi.models || currentApi.entries)) || [];
  const apiOptions = apis.map(a => ({ value: a.api_id || a.id, label: a.display_name || a.name || (a.api_id || a.id) }));
  if (apiId && !apiOptions.some(o => o.value === apiId)) apiOptions.unshift({ value: apiId, label: apiId + t('scripts.review.api_not_in_mgr') });
  const modelOptions = modelList.map(m => ({ value: m.real_name || m.id, label: m.display_name || m.real_name || m.id }));
  if (model && !modelOptions.some(o => o.value === model)) modelOptions.unshift({ value: model, label: model + t('scripts.review.model_custom') });
  const onPickApi = (v) => {
    setApiId(v);
    const a = apis.find(x => (x.api_id || x.id) === v);
    const m0 = a && (a.models || a.entries || [])[0];
    if (m0) setModel(m0.real_name || m0.id);
  };

  return (
    <CSSpaceBetween size="l">
      <CSSpaceBetween direction="horizontal" size="xs">
        {phase === 'config' && <CSButton onClick={doEstimate} loading={estimating}>{t('scripts.review.estimate_cost')}</CSButton>}
        {(phase === 'config' || phase === 'error') && <CSButton variant="primary" iconName="gen-ai" onClick={doStart}>{t('scripts.review.start_extract')}</CSButton>}
        {phase === 'running' && <CSButton onClick={doCancel}>{t('scripts.review.cancel_job')}</CSButton>}
      </CSSpaceBetween>
      {err && <CSAlert type="error">{err}</CSAlert>}

        {(phase === 'config' || phase === 'error') && (
          <CSSpaceBetween size="l">
            <CSBox color="text-body-secondary" fontSize="body-s">
              {t('scripts.review.desc')}
            </CSBox>
            <CSFormField label={t('scripts.review.algorithm')}>
              <CSSegmentedControl selectedId={algorithm}
                options={[{ id: 'arc', text: t('scripts.review.algo_arc') }, { id: 'per_chapter', text: t('scripts.review.algo_per_chapter') }]}
                onChange={({ detail }) => setAlgorithm(detail.selectedId)} />
            </CSFormField>
            <CSColumnLayout columns={2}>
              <CSFormField label="Provider" description={t('scripts.review.provider_desc')}>
                <CSSelect
                  selectedOption={apiOptions.find(o => o.value === apiId) || (apiId ? { value: apiId, label: apiId } : null)}
                  options={apiOptions}
                  placeholder={t('scripts.review.provider_placeholder')}
                  empty={t('scripts.review.provider_empty')}
                  onChange={({ detail }) => onPickApi(detail.selectedOption.value)}
                />
              </CSFormField>
              <CSFormField label={t('scripts.review.model')} description={t('scripts.review.model_desc')}>
                <CSSelect
                  selectedOption={modelOptions.find(o => o.value === model) || (model ? { value: model, label: model } : null)}
                  options={modelOptions}
                  placeholder={t('scripts.review.model_placeholder')}
                  empty={t('scripts.review.model_empty')}
                  onChange={({ detail }) => setModel(detail.selectedOption.value)}
                />
              </CSFormField>
              {algorithm === 'arc' && (
                <CSFormField label={t('scripts.review.target_arcs')} description={t('scripts.review.target_arcs_desc')}><CSInput type="number" value={targetArcs} onChange={({ detail }) => setTargetArcs(detail.value)} /></CSFormField>
              )}
              <CSFormField label={t('scripts.review.concurrency')}><CSInput type="number" value={concurrency} onChange={({ detail }) => setConcurrency(detail.value)} /></CSFormField>
              <CSFormField label={t('scripts.review.author_era')} description={t('scripts.review.author_era_desc')}><CSInput value={authorEra} onChange={({ detail }) => setAuthorEra(detail.value)} /></CSFormField>
              <CSFormField label={t('scripts.review.max_usd')}><CSInput type="number" value={maxUsd} onChange={({ detail }) => setMaxUsd(detail.value)} /></CSFormField>
            </CSColumnLayout>

            {estimate && estimate.ok !== false && (
              <CSAlert type="info" header={t('scripts.review.cost_estimate')}>
                <CSKeyValuePairs columns={4} items={[
                  { label: t('scripts.import.est_cost'), value: estimate.est_usd != null ? `$${Number(estimate.est_usd).toFixed(3)}` : '—' },
                  { label: t('scripts.review.arcs'), value: estimate.arcs != null ? String(estimate.arcs) : '—' },
                  { label: t('scripts.review.input_tokens'), value: estimate.est_input_tokens != null ? Number(estimate.est_input_tokens).toLocaleString() : '—' },
                  { label: t('scripts.review.output_tokens'), value: estimate.est_output_tokens != null ? Number(estimate.est_output_tokens).toLocaleString() : '—' },
                ]} />
                {estimate.note && <CSBox fontSize="body-s" color="text-body-secondary" padding={{ top: 'xs' }}>{estimate.note}</CSBox>}
              </CSAlert>
            )}
            {estimate && estimate.ok === false && <CSAlert type="warning">{estimate.error || estimate.note || t('scripts.review.cannot_estimate')}</CSAlert>}
          </CSSpaceBetween>
        )}

        {(phase === 'running' || phase === 'done') && (
          <CSSpaceBetween size="m">
            <CSProgressBar value={overallTotal ? Math.round(overall / overallTotal * 100) : 0}
              label={t('scripts.review.overall_progress')} additionalInfo={t('scripts.review.stage_info', { cur: overall, total: overallTotal })}
              status={phase === 'done' ? 'success' : 'in-progress'} />
            <CSSpaceBetween size="xs">
              {stages.map((st) => (
                <div key={st.id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <CSStatusIndicator type={_stageIndicator(st.status)}>
                    {st.label || (_EXTRACT_STAGE_LABEL_KEYS[st.id] ? t(_EXTRACT_STAGE_LABEL_KEYS[st.id]) : st.id)}
                  </CSStatusIndicator>
                  {st.stage_total ? <CSBox fontSize="body-s" color="text-body-secondary">{st.stage_progress || 0} / {st.stage_total}</CSBox> : null}
                </div>
              ))}
              {stages.length === 0 && <CSBox color="text-body-secondary" fontSize="body-s">{t('scripts.review.dispatching')}</CSBox>}
            </CSSpaceBetween>
            {job && job.budget_estimate && job.budget_estimate.arcs ? (
              <CSBox fontSize="body-s" color="text-body-secondary">{t('scripts.review.split_arcs', { n: job.budget_estimate.arcs })}</CSBox>
            ) : null}
            {usage && (
              <CSAlert type={phase === 'done' ? 'success' : 'info'} header={t('scripts.review.usage')}>
                <CSKeyValuePairs columns={4} items={[
                  { label: t('scripts.review.spent'), value: usage.usd != null ? `$${Number(usage.usd).toFixed(3)}` : '—' },
                  { label: t('scripts.review.input_tokens'), value: usage.input_tokens != null ? Number(usage.input_tokens).toLocaleString() : '—' },
                  { label: t('scripts.review.output_tokens'), value: usage.output_tokens != null ? Number(usage.output_tokens).toLocaleString() : '—' },
                  { label: t('scripts.review.llm_calls'), value: usage.llm_calls != null ? String(usage.llm_calls) : '—' },
                ]} />
              </CSAlert>
            )}
            {phase === 'done' && <CSAlert type="success">{t('scripts.review.extract_complete')}</CSAlert>}
          </CSSpaceBetween>
        )}
      </CSSpaceBetween>
  );
}

export { ScriptsPage, ScriptsListView, ScriptsLibraryView, ChaptersModal, OverridesModal, ScriptsImportView, ImportJobBanner, ImportJobResult, ImportEstimateView, ScriptPreviewModal, ConfidenceBar, KbExtractPanel };
