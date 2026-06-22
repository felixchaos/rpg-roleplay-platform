/* Scripts page — split out of platform-app.jsx (task 52: 拆 platform-app.jsx 按页面).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: PromptModal / Icon / usePlatformData / fmtBytes / fmtN
   以及 saves.jsx 注入的 NewGameModal（顺序保证：platform-app.jsx → saves.jsx → scripts.jsx 在 Platform.html 中按序加载）。 */

import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../game-icons.jsx';
import Modal from '../components/Modal.jsx';
import { plNavigate } from '../router.js';
import { PromptModal, usePlatformData, fmtBytes, fmtN, ResizableSplit } from '../platform-app.jsx';
import { CardEditModal, cardSnippet, npcToUserCardBody } from './cards.jsx';
import { NewGameModal } from './saves.jsx';
import { ScriptReview } from './script-review.jsx';
import { WorldbookEditorView } from './script-edit-worldbook.jsx';
import { credApiIdSet } from '../components/catalog-helpers.js';
import { isCredentialsError } from '../lib/creds.js';
import { lsGetJSON, lsSetJSON, lsRemove } from '../lib/storage.js';
// phase_rebuild_panel: 模块矩阵重做面板
import { useScriptRebuild, ModuleRebuildPanel } from './script-modules-panel.jsx';
import AgentModelPicker from '../components/AgentModelPicker.jsx';
import GmStyleEditor from '../components/GmStyleEditor.jsx';
import AvatarImg from '../components/AvatarImg.jsx';
import GenerateImageModal from '../components/GenerateImageModal.jsx';
import MediaStudio from '../components/MediaStudio.jsx';
import { ModuleStatusCard } from '../components/ModuleStatusCard.jsx';
import { ModuleMatrixOverview } from '../components/ModuleMatrixOverview.jsx';
import { RebuildJobBanner } from '../components/RebuildJobBanner.jsx';
import { RebuildEstimateModal } from '../components/RebuildEstimateModal.jsx';
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
import CSToggle from '@cloudscape-design/components/toggle';
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
import CSDrawer from '@cloudscape-design/components/drawer';

const PENDING_IMPORT_KEY = "rpg.import.pendingImport";
const PENDING_IMPORT_PIPELINE_KEY = "rpg.import.pendingPipeline";
const IMPORT_JOB_TERMINAL_STATUSES = new Set(["done", "done_with_errors", "partial", "failed", "cancelled"]);
const ACTIVE_IMPORT_STATUSES = new Set(["queued", "pending", "running", "processing", "importing", "started"]);
const PLAY_BLOCKING_READINESS_KEYS = new Set(["chunks", "anchors"]);

function readinessLabel(key, t) {
  return t(`scripts.my.readiness_label_${key}`, { defaultValue: key });
}

function activeJobPlayBlockReason(payload, t) {
  const job = payload?.job || payload?.active_job || payload;
  const status = String(job?.status || payload?.status || "").trim().toLowerCase();
  if (status && ACTIVE_IMPORT_STATUSES.has(status) && !IMPORT_JOB_TERMINAL_STATUSES.has(status)) {
    return t('scripts.my.play_block_importing');
  }
  if (payload?.active === true && (!status || !IMPORT_JOB_TERMINAL_STATUSES.has(status))) {
    return t('scripts.my.play_block_importing');
  }
  return "";
}

function scriptPlayBlockReason(script, t) {
  if (!script) return "";
  const status = String(
    script.import_status
    || script.job_status
    || script.active_job?.status
    || script.readiness?.active_job?.status
    || ""
  ).trim().toLowerCase();
  if (status && ACTIVE_IMPORT_STATUSES.has(status) && !IMPORT_JOB_TERMINAL_STATUSES.has(status)) {
    return t('scripts.my.play_block_importing');
  }
  const missing = Array.isArray(script.readiness?.missing) ? script.readiness.missing : [];
  const blocking = missing.filter((key) => PLAY_BLOCKING_READINESS_KEYS.has(key));
  if (blocking.length > 0) {
    return t('scripts.my.play_block_missing', { items: blocking.map((key) => readinessLabel(key, t)).join('、') });
  }
  if (Number(script.chapter_count || 0) <= 0) {
    return t('scripts.my.play_block_missing', { items: readinessLabel('chunks', t) });
  }
  return "";
}

// 统一到共享 isCredentialsError(lib/creds.js):它检测同样的 code/error_key/needs_credentials
// (含 payload)并额外覆盖错误信息字符串里夹带的信号,是原实现的超集。保留本地别名以免改全部调用点。
const isCredentialsRequiredError = isCredentialsError;

function ScriptPreviewModal({ open, busy, data, rule, onClose, onRetryRule, onConfirm }) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <Modal
      open
      eyebrow={`${t('scripts.import.preview_eyebrow')} · ${rule || t('scripts.import.rule_auto')}`}
      title={busy ? t('scripts.import.preview_splitting') : (data?.title || t('scripts.import.unnamed'))}
      width={720}
      onClose={onClose}
      footer={<>
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
      </>}
    >
        {busy ? (
          // 这里之前是 3 个伪 step (校验文件 / 解析分章 / 计算预算) — 前端没法真知道
          // 后端预览到了哪一步。改成单一 spinner,不撒谎。
          <div className="pl-validate-progress">
            <div className="pl-validate-step running">
              <Icon name="spinner" size={12} className="spin" /> {t('scripts.import.preview_splitting')}
            </div>
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
                      <td className="mono" style={{textAlign: "right"}}>{Number(p.words || 0).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
    </Modal>
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

function isExpiredUploadError(e) {
  const text = [
    e?.message,
    e?.error,
    e?.detail,
    e?.payload?.error,
    e?.payload?.detail,
    e?.payload?.message,
  ].filter(Boolean).join(" ");
  return /upload_id.*(不存在|过期|expired|not found)|uploaded file.*(expired|missing)/i.test(text);
}

function ScriptsPage({ subPage = "list" }) {
  return (
    <div className="pl-stack">
      {subPage === "import" ? <ScriptsImportView />
        : subPage === "library" ? <ScriptsLibraryView />
        : <ScriptsListView />}
    </div>
  );
}

/* ─── 版本历史 Drawer ────────────────────────────────────────────
   GET /api/scripts/{id}/commits?limit=30&cursor=X
   支持 cursor 翻页;当前 head_commit_id 行标 "current" badge;
   owner 可点回滚,非 owner disabled。 */
function VersionHistoryDrawer({ script, currentUserId, onClose }) {
  const { t } = useTranslation();
  const [commits, setCommits] = useStatePL([]);
  const [loading, setLoading] = useStatePL(false);
  const [cursor, setCursor] = useStatePL(null);
  const [hasMore, setHasMore] = useStatePL(false);
  const [rollingBack, setRollingBack] = useStatePL(null);

  const loadCommits = React.useCallback(async (c = null) => {
    if (!script) return;
    setLoading(true);
    try {
      const params = { limit: 30 };
      if (c) params.cursor = c;
      const r = await window.api.scripts.commits(script.id, params);
      const list = Array.isArray(r) ? r : (r?.items || r?.commits || []);
      const nextCursor = r?.next_cursor || null;
      if (c) {
        setCommits(prev => [...prev, ...list]);
      } else {
        setCommits(list);
      }
      setCursor(nextCursor);
      setHasMore(!!nextCursor);
    } catch (_) {
      window.__apiToast?.(t('scripts.version.load_fail'), { kind: 'danger' });
    } finally {
      setLoading(false);
    }
  }, [script?.id]);

  useEffectPL(() => {
    if (script) loadCommits(null);
  }, [script?.id, loadCommits]);

  const isOwner = script && currentUserId && script.owner_id === currentUserId;

  const onRollback = async (commit) => {
    if (!await window.__confirm({
      title: t('scripts.version.rollback_confirm', { id: String(commit.id ?? '').slice(0, 8) }),
      danger: true,
      confirmText: t('scripts.version.rollback_btn'),
    })) return;
    setRollingBack(commit.id);
    try {
      await window.api.scripts.checkout(script.id, commit.id);
      window.__apiToast?.(t('scripts.version.rollback_ok', { id: String(commit.id ?? '').slice(0, 8) }), { kind: 'ok' });
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      onClose && onClose();
    } catch (e) {
      window.__apiToast?.(t('scripts.version.rollback_fail'), { kind: 'danger', detail: e?.message });
    } finally {
      setRollingBack(null);
    }
  };

  // ESC 关闭 + 点 backdrop 关闭
  useEffectPL(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose && onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!script) return null;

  return (
    <>
    {/* 半透明 backdrop:点击关闭 + 阻止鼠标事件穿透到下层主页面 */}
    <div onClick={onClose} style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.35)', zIndex: 899,
    }} />
    <div style={{
      position: 'fixed', top: 0, right: 0, bottom: 0, width: 'min(560px, 92vw)',
      background: 'var(--panel, #1a1d22)', borderLeft: '1px solid var(--line-soft)',
      zIndex: 900, display: 'flex', flexDirection: 'column', overflowY: 'auto',
      boxShadow: '-4px 0 16px rgba(0,0,0,0.35)',
    }}>
      <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
        <CSBox variant="h3" padding="n">{t('scripts.version.drawer_title')} · {script.title}</CSBox>
        <CSButton variant="normal" iconName="close" onClick={onClose}>{t('common.close')}</CSButton>
      </div>
      <div style={{ flex: 1, padding: '12px 16px' }}>
        <CSTable
          variant="embedded"
          loading={loading && commits.length === 0}
          loadingText={t('common.loading')}
          items={commits}
          trackBy="id"
          columnDefinitions={[
            {
              id: 'commit', header: t('scripts.version.col_commit'), width: 110,
              cell: (c) => (
                <CSSpaceBetween direction="horizontal" size="xxs" alignItems="center">
                  <span className="mono" style={{ fontSize: 12 }}>{String(c.id || '').slice(0, 8)}</span>
                  {script.head_commit_id && c.id === script.head_commit_id && (
                    <CSBadge color="green">{t('scripts.version.badge_current')}</CSBadge>
                  )}
                </CSSpaceBetween>
              ),
            },
            {
              id: 'message', header: t('scripts.version.col_message'),
              cell: (c) => <CSBox fontSize="body-s">{c.message || '—'}</CSBox>,
            },
            {
              id: 'kind', header: t('scripts.version.col_kind'), width: 90,
              cell: (c) => <CSBox fontSize="body-s" color="text-body-secondary">{c.kind || '—'}</CSBox>,
            },
            {
              id: 'date', header: t('scripts.version.col_date'), width: 130,
              cell: (c) => <CSBox fontSize="body-s" color="text-body-secondary">{c.created_at ? new Date(c.created_at).toLocaleString() : '—'}</CSBox>,
            },
            {
              id: 'action', header: '', width: 120,
              cell: (c) => (
                <CSButton
                  variant="inline-link"
                  disabled={!isOwner || rollingBack === c.id}
                  loading={rollingBack === c.id}
                  title={!isOwner ? t('scripts.version.rollback_disabled_tip') : ''}
                  onClick={() => onRollback(c)}
                >{t('scripts.version.rollback_btn')}</CSButton>
              ),
            },
          ]}
          empty={<CSBox textAlign="center" padding={{ vertical: 'l' }} color="inherit">{t('scripts.version.empty')}</CSBox>}
        />
        {hasMore && (
          <div style={{ paddingTop: 12, textAlign: 'center' }}>
            <CSButton loading={loading} onClick={() => loadCommits(cursor)}>{t('common.load_more', { defaultValue: '加载更多' })}</CSButton>
          </div>
        )}
      </div>
    </div>
    </>
  );
}

/* ─── 共享模式选择器 ─────────────────────────────────────────────
   CSSegmentedControl: private / public / pinned-snapshot / floating-latest
   pinned 时显示 commit 下拉选择器。
   POST /api/scripts/{id}/pin 设置 */
function SharingModeSelector({ script, currentUserId, onChanged }) {
  const { t } = useTranslation();
  const [mode, setMode] = useStatePL(script?.sharing_mode || 'private');
  const [commits, setCommits] = useStatePL([]);
  const [pinCommitId, setPinCommitId] = useStatePL(script?.current_pin_commit_id || null);
  const [saving, setSaving] = useStatePL(false);

  const isOwner = script && currentUserId && script.owner_id === currentUserId;

  useEffectPL(() => {
    setMode(script?.sharing_mode || 'private');
    setPinCommitId(script?.current_pin_commit_id || null);
  }, [script?.id, script?.sharing_mode, script?.current_pin_commit_id]);

  useEffectPL(() => {
    if (!script || !isOwner) return;
    (async () => {
      try {
        const r = await window.api.scripts.commits(script.id, { limit: 30 });
        const list = Array.isArray(r) ? r : (r?.items || r?.commits || []);
        setCommits(list);
      } catch (_) {}
    })();
  }, [script?.id, isOwner]);

  if (!script || !isOwner) return null;

  const onSave = async (newMode, newPinCommitId) => {
    setSaving(true);
    try {
      if (newMode === 'private') {
        await window.api.scripts.unpin(script.id);
      } else {
        await window.api.scripts.pin(script.id, {
          mode: newMode,
          target_script_id: script.id,
          commit_id: newMode === 'pinned-snapshot' ? (newPinCommitId || undefined) : undefined,
        });
      }
      window.__apiToast?.(t('scripts.share.pin_ok'), { kind: 'ok', duration: 2000 });
      onChanged && onChanged();
    } catch (e) {
      window.__apiToast?.(t('scripts.share.pin_fail'), { kind: 'danger', detail: e?.message });
    } finally {
      setSaving(false);
    }
  };

  const handleModeChange = ({ detail }) => {
    const m = detail.selectedId;
    setMode(m);
    if (m !== 'pinned-snapshot') onSave(m, null);
  };

  const commitOptions = commits.map(c => ({
    value: c.id,
    label: `${String(c.id || '').slice(0, 8)} · ${c.message || c.kind || ''}`,
  }));
  const selectedCommitOpt = commitOptions.find(o => o.value === pinCommitId) || (pinCommitId ? { value: pinCommitId, label: String(pinCommitId).slice(0, 8) } : null);

  return (
    <CSSpaceBetween size="xs">
      <CSFormField label={t('scripts.share.mode_label')}>
        <CSSegmentedControl
          selectedId={mode}
          options={[
            { id: 'private',          text: t('scripts.share.mode_private') },
            { id: 'public',           text: t('scripts.share.mode_public') },
            { id: 'pinned-snapshot',  text: t('scripts.share.mode_pinned') },
            { id: 'floating-latest',  text: t('scripts.share.mode_floating') },
          ]}
          onChange={handleModeChange}
          disabled={saving}
        />
      </CSFormField>
      {mode === 'pinned-snapshot' && (
        <CSSpaceBetween direction="horizontal" size="xs" alignItems="flex-end">
          <CSFormField
            label={t('scripts.share.pin_commit_label')}
            description={t('scripts.share.pin_commit_hint', { defaultValue: '选定版本作记录;当前 GM 检索按【目标剧本的最新内容】读取(精确版本回放为后续功能)。floating-latest 则始终跟随目标最新。' })}
            stretch
          >
            <CSSelect
              selectedOption={selectedCommitOpt}
              options={commitOptions}
              placeholder={t('scripts.share.pin_commit_placeholder')}
              onChange={({ detail }) => setPinCommitId(detail.selectedOption.value)}
              disabled={saving}
            />
          </CSFormField>
          <CSButton loading={saving} disabled={!pinCommitId || saving} onClick={() => onSave('pinned-snapshot', pinCommitId)}>
            {t('common.save', { defaultValue: '保存' })}
          </CSButton>
        </CSSpaceBetween>
      )}
    </CSSpaceBetween>
  );
}

/* 剧本详情面板 —— 选中某剧本后在列表下方展开(对齐存档页结构)。
   Tabs:概览 / 参数(剧本覆盖设定) / 世界书(worldbook) / 知识库人物 / NPC 角色卡 / 时间线锚点。
   世界书 / NPC 角色卡 / 时间线锚点按需懒加载。 */
// 剧本封面:宽高比自适应海报(模糊填充 + contain),竖/方/横封面都完整显示;悬停更换 + 点击放大。
function CoverFrame({ src, title, isOwner, onEdit }) {
  const { t } = useTranslation();
  const [aspect, setAspect] = React.useState(null);
  const [light, setLight] = React.useState(false);
  React.useEffect(() => { setAspect(null); }, [src]);
  React.useEffect(() => {
    if (!light) return;
    const h = (e) => { if (e.key === 'Escape') setLight(false); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [light]);
  const onLoad = (e) => {
    const w = e.target && e.target.naturalWidth, h = e.target && e.target.naturalHeight;
    if (w && h) { const r = Math.max(0.62, Math.min(1.78, w / h)); setAspect(`${r.toFixed(4)} / 1`); }
  };
  return (
    <div className="mh-hero" style={{ ...(aspect ? { aspectRatio: aspect } : { aspectRatio: '16 / 9' }), cursor: 'zoom-in' }} onClick={() => setLight(true)}>
      <img src={src} className="mh-hero__fill" alt="" aria-hidden="true" loading="lazy" />
      <img src={src} className="mh-hero__img" alt={title} loading="lazy" onLoad={onLoad} />
      <div className="mh-hero__scrim" />
      <div className="mh-hero__meta"><div className="mh-hero__name" style={{ fontSize: 20 }}>{title}</div></div>
      {isOwner && (
        <div className="mh-hero__actions">
          <span className="mh-chip" onClick={(e) => { e.stopPropagation(); onEdit && onEdit(); }}>{t('scripts.page.change_cover')}</span>
        </div>
      )}
      {light && (
        <div className="mlb-backdrop" onClick={() => setLight(false)} role="dialog" aria-modal="true">
          <img src={src} alt={title} style={{ maxWidth: '92vw', maxHeight: '90vh', objectFit: 'contain', borderRadius: 10, boxShadow: '0 12px 60px rgba(0,0,0,.7)' }} onClick={(e) => e.stopPropagation()} />
          <button onClick={() => setLight(false)} aria-label={t('common.close')} style={{ position: 'absolute', top: 20, right: 24, width: 38, height: 38, borderRadius: 99, border: 0, background: 'rgba(255,255,255,.14)', color: '#fff', fontSize: 19, cursor: 'pointer' }}>×</button>
        </div>
      )}
    </div>
  );
}

function ScriptDetailPanel({ script: s, savesCount, scriptSaves = [], embedStatus, currentUserId,
  pendingTab, onPendingTabConsumed,
  onPlay, onContinueSave, onNewGame, onChapters, onReview, onExtractDone, onEmbed, onExport, onToggleVisibility, onDelete, onUnsubscribe, onEditOverrides, onReload }) {
  const { t } = useTranslation();
  const [tab, setTab] = useStatePL('overview');

  // 列表"状态"下拉点击 → 父组件 setPendingTab(id) → 这里听到后切 tab,
  // 立刻 consume 防止下次同样的 id 又触发(虽然父端会清 null,这是双保险)
  React.useEffect(() => {
    if (pendingTab) {
      setTab(pendingTab);
      onPendingTabConsumed && onPendingTabConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingTab, s.id]);
  const [wb, setWb] = useStatePL(null);
  const [npc, setNpc] = useStatePL(null);
  const [tl, setTl] = useStatePL(null);
  const [ov, setOv] = useStatePL(null);
  const [loading, setLoading] = useStatePL(false);
  const [npcEdit, setNpcEdit] = useStatePL(null); // { card, isNew } | null — NPC 卡编辑(复用 CardEditModal)
  // Version history drawer
  const [historyOpen, setHistoryOpen] = useStatePL(false);
  // Fork inline confirmation state
  const [forkBusy, setForkBusy] = useStatePL(false);
  const [forkConfirm, setForkConfirm] = useStatePL(false);
  // 封面:统一 MediaStudio
  const [coverStudioOpen, setCoverStudioOpen] = useStatePL(false);
  const [coverUrl, setCoverUrl] = useStatePL(s.cover_image_url || null);

  useEffectPL(() => {
    setWb(null); setNpc(null); setTl(null); setOv(null);
    // 若有 pendingTab 跳转(列表"状态"下拉触发),别用 overview 覆盖它 —
    // pendingTab effect 与本 reset effect 在 s.id 变化时会同帧触发,
    // 后者若无条件 setTab('overview') 会盖掉前者设的目标 tab。
    if (!pendingTab) setTab('overview');
    setHistoryOpen(false); setForkConfirm(false);
    setCoverUrl(s.cover_image_url || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.id]);

  const isOwner = currentUserId && s.owner_id === currentUserId;


  // 手动把某 NPC 卡设为主角(AI canon importance 误判时纠正)。设完重拉列表刷新「主角」徽标。
  const [protagBusy, setProtagBusy] = useStatePL(null); // 正在设置的 card id
  const onSetProtagonist = async (c) => {
    if (!c || !c.id) return;
    setProtagBusy(c.id);
    try {
      await window.api.cards.scriptSetProtagonist(s.id, c.id);
      window.__apiToast?.(t('scripts.toast.protagonist_set', { name: c.name || 'NPC', defaultValue: `已将「${c.name || 'NPC'}」设为主角` }), { kind: 'ok' });
      setNpc(null); // 触发 NPC 列表重新拉取
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.protagonist_fail', { defaultValue: '设为主角失败' }), { kind: 'danger', detail: e?.message });
    } finally {
      setProtagBusy(null);
    }
  };

  // NPC 卡 → 用户角色卡(card_type='pc')。复制一份独立用户卡(含头像),后端 myUpsert
  // (POST /api/me/character-cards),与 agent 工具 clone_npc_to_user_card 等价。订阅者也可转
  // (转到自己名下,不改原剧本)。body shape 与角色卡页共用 npcToUserCardBody,避免漂移。
  const [promoteBusy, setPromoteBusy] = useStatePL(null);
  const onPromoteNpc = async (c) => {
    if (!c) return;
    setPromoteBusy(c.id);
    try {
      const body = npcToUserCardBody(c, {
        fromNpcTag: t('cards.list.tag_from_npc', { defaultValue: '来自NPC' }),
        unnamed: t('scripts.editor.unnamed_npc', { defaultValue: '无名角色' }),
      });
      const r = await window.api.cards.myUpsert(body);
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('scripts.page.promote_fail'));
      window.__apiToast?.(t('scripts.toast.npc_promoted', { name: body.name, defaultValue: `已把「${body.name}」转为你的用户角色卡` }),
        { kind: 'ok', duration: 2600, detail: t('scripts.toast.npc_promoted_detail', { defaultValue: '在「角色卡 · 我的」里可编辑/挂到任意剧本' }) });
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.npc_promote_fail', { defaultValue: '转为用户角色卡失败' }), { kind: 'danger', detail: e?.message || String(e) });
    } finally {
      setPromoteBusy(null);
    }
  };

  // 按需 AI 复核全部 NPC 卡:弹公用模型选择器(默认用户常用模型,可改)→ 用所选模型批量裁决
  // (合并同人卡 / 锁定真主角 / 删非人名卡)。on-demand,不进导入流水线 → 零自动成本。
  const [auditOpen, setAuditOpen] = useStatePL(false);
  const [auditSel, setAuditSel] = useStatePL({ api_id: '', model: '' });
  const [auditBusy, setAuditBusy] = useStatePL(false);
  const runAudit = async () => {
    setAuditBusy(true);
    try {
      const r = await window.api.cards.auditCards(s.id, auditSel.api_id, auditSel.model);
      if (r && r.ok === false) {
        if (r.needs_credentials) {
          window.__apiToast?.(t('scripts.audit.need_key', { defaultValue: '该模型还没配 API Key' }),
            { kind: 'warn', detail: t('scripts.audit.need_key_hint', { defaultValue: '去「设置 → API 与模型」配置后重试,或在上面换一个已配置的模型。' }) });
        } else {
          window.__apiToast?.(t('scripts.audit.fail', { defaultValue: 'AI 复核失败' }), { kind: 'danger', detail: r.error });
        }
        return;
      }
      const sm = (r && r.summary) || {};
      const parts = [];
      if (sm.protagonist) parts.push(t('scripts.page.audit_protagonist_set', { name: sm.protagonist }));
      if (Array.isArray(sm.merged) && sm.merged.length) parts.push(t('scripts.page.audit_merged', { n: sm.merged.length }));
      if (Array.isArray(sm.dropped) && sm.dropped.length) parts.push(t('scripts.page.audit_dropped', { n: sm.dropped.length }));
      window.__apiToast?.(parts.length ? t('scripts.page.audit_done_detail', { detail: parts.join('、') }) : t('scripts.page.audit_done_no_changes'), { kind: 'ok' });
      setAuditOpen(false);
      setNpc(null); // 触发 NPC 列表重新拉取
    } catch (e) {
      window.__apiToast?.(t('scripts.audit.fail', { defaultValue: 'AI 复核失败' }), { kind: 'danger', detail: e?.message });
    } finally {
      setAuditBusy(false);
    }
  };

  const doFork = async () => {
    setForkBusy(true);
    try {
      const newTitle = t('scripts.page.fork_title_suffix', { title: s.title });
      const r = await window.api.scripts.fork(s.id, { title: newTitle });
      if (!r || r.ok === false) throw new Error(r?.error || t('scripts.share.fork_fail'));
      window.__apiToast?.(t('scripts.toast.fork_ok'), { kind: 'ok' });
      setForkConfirm(false);
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      // 跳转到新 script (如果后端返回 script_id/id)
      const newId = r.script_id || r.id || r.script?.id;
      if (newId && onReload) onReload(newId);
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.fork_fail'), { kind: 'danger', detail: e?.message });
    } finally {
      setForkBusy(false);
    }
  };

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
    // 必须把 wb/npc/tl/ov 纳入依赖:保存/重建后用 setNpc(null) 触发重拉的模式靠的就是
    // 这些值变 null 重跑本 effect(== null 守卫天然防循环)。此前漏了它们 → 保存后置 null
    // 却不重拉 → 列表渲染 npc||[]=空,刷新切 tab 才恢复(用户反馈"保存后变空,刷新恢复"真因)。
  }, [tab, s.id, wb, npc, tl, ov]);

  const es = embedStatus[s.id];
  const embedLabel = (() => {
    if (!es) return t('scripts.my.embed_none');
    const done = es.chunks.done + es.cards.done + es.worldbook.done;
    const all = es.chunks.total + es.cards.total + es.worldbook.total;
    if (es.running) return t('scripts.my.embed_running', { pct: all ? Math.round(done / all * 100) : 0 });
    return all > 0 && done >= all ? t('scripts.my.embed_done', { n: all }) : t('scripts.my.embed_none');
  })();
  // 章节向量未建警告:chunks.done=0 但 cards/worldbook>0 时,RAG 检索退化到 keyword,玩起来会塌房
  const embedWarning = (() => {
    if (!es) return null;
    if ((es.chunks?.done || 0) === 0 && ((es.cards?.done || 0) > 0 || (es.worldbook?.done || 0) > 0)) {
      return t('scripts.editor.embed_warn_chunks_missing', { defaultValue: '章节向量未建,RAG 退化到关键字' });
    }
    return null;
  })();

  // phase_rebuild_panel: 7 模块状态 + 估算/重做 SSE 协调器.
  // hook 自己拉 /modules-status,在 active rebuild job 时禁用其他卡按钮 + 顶部 banner 实时进度.
  const rb = useScriptRebuild(s.id);
  const playBlock = scriptPlayBlockReason(s, t);

  // 默认提示词:用剧本简介(描述故事内容,适合作画面参考),而非标题(标题不是画面描述词)。
  // 无简介则留空,由 MediaStudio 的占位示例引导用户自己写。
  const genCoverDefaultPrompt = (s.description || '').trim().slice(0, 300);

  return (
    <>
    {coverStudioOpen && (
      <MediaStudio
        open={coverStudioOpen}
        onClose={() => setCoverStudioOpen(false)}
        target={{ type: 'script_cover', id: s.id }}
        name={s.title}
        defaultPrompt={genCoverDefaultPrompt}
        onApplied={(url) => {
          setCoverUrl(url);
          setCoverStudioOpen(false);
        }}
      />
    )}
    {historyOpen && (
      <VersionHistoryDrawer
        script={s}
        currentUserId={currentUserId}
        onClose={() => setHistoryOpen(false)}
      />
    )}
    <CSContainer header={
      <CSHeader variant="h2"
        actions={
          <CSSpaceBetween direction="horizontal" size="xs">
            {isOwner && (
              <CSButton iconName="gen-ai" onClick={() => setCoverStudioOpen(true)}>{t('scripts.page.change_cover')}</CSButton>
            )}
            {/* 反馈#3:开始游戏改下拉——可选继续某个存档 / 开新游戏,不再有存档就直接进后台 */}
            <CSButtonDropdown variant="primary" expandToViewport disabled={!!playBlock}
              items={[
                ...(scriptSaves.length ? [{
                  text: t('scripts.my.play_continue_group'),
                  items: scriptSaves.map((sv) => ({
                    id: 'continue:' + sv.id,
                    text: sv.title || ('#' + sv.id),
                    iconName: 'caret-right-filled',
                  })),
                }] : []),
                { id: 'new', text: t('scripts.my.play_new_game'), iconName: 'add-plus' },
              ]}
              onItemClick={({ detail }) => {
                if (detail.id === 'new') { onNewGame && onNewGame(s); return; }
                if (typeof detail.id === 'string' && detail.id.startsWith('continue:')) {
                  const sv = scriptSaves.find((x) => String(x.id) === detail.id.slice('continue:'.length));
                  if (sv) onContinueSave && onContinueSave(sv);
                }
              }}
            >{t('scripts.my.play_game')}</CSButtonDropdown>
            <CSButton iconName="file" onClick={() => onChapters(s)}>{t('scripts.my.view_chapters')}</CSButton>
            <CSButton iconName="status-info" onClick={() => onReview(s)}>{t('scripts.my.kb_review')}</CSButton>
            <CSButton iconName="settings" onClick={() => setHistoryOpen(v => !v)}>{t('scripts.version.history_btn')}</CSButton>
            <CSButtonDropdown expandToViewport
              items={[
                { id: 'embed', text: es?.running ? t('scripts.my.embedding') : t('scripts.my.embed_start'), iconName: 'search', disabled: !!es?.running },
                { id: 'export', text: t('scripts.my.action_export'), iconName: 'download' },
                { id: 'visibility', text: s.is_public ? t('scripts.my.action_unpublish') : t('scripts.my.action_publish'), iconName: s.is_public ? 'lock-private' : 'share' },
                s.is_subscribed
                  ? { id: 'unsubscribe', text: t('scripts.my.action_unsubscribe'), iconName: 'remove' }
                  : { id: 'delete', text: t('scripts.my.action_delete'), iconName: 'remove' },
              ]}
              onItemClick={({ detail }) => {
                const id = detail.id;
                if (id === 'embed') onEmbed(s);
                else if (id === 'export') onExport(s);
                else if (id === 'visibility') onToggleVisibility(s);
                else if (id === 'delete') onDelete(s);
                else if (id === 'unsubscribe') onUnsubscribe && onUnsubscribe(s);
              }}>{t('scripts.my.more')}</CSButtonDropdown>
          </CSSpaceBetween>
        }
      >{s.title}</CSHeader>
    }>
      {/* Fork alert — non-owner script */}
      {!isOwner && s.owner_id && (
        <CSSpaceBetween size="s">
          <CSAlert
            type="info"
            header={t('scripts.share.fork_alert_header')}
            action={
              forkConfirm ? (
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSButton variant="primary" loading={forkBusy} onClick={doFork}>{t('scripts.share.fork_btn')}</CSButton>
                  <CSButton disabled={forkBusy} onClick={() => setForkConfirm(false)}>{t('common.cancel', { defaultValue: '取消' })}</CSButton>
                </CSSpaceBetween>
              ) : (
                <CSButton iconName="copy" onClick={() => setForkConfirm(true)}>{t('scripts.share.fork_btn')}</CSButton>
              )
            }
          >
            {forkConfirm
              ? t('scripts.share.fork_confirm_body', { title: s.title })
              : t('scripts.share.fork_alert_body')}
          </CSAlert>
        </CSSpaceBetween>
      )}
      {/* Sharing mode selector — owner only */}
      {isOwner && (
        <SharingModeSelector script={s} currentUserId={currentUserId} onChanged={onReload} />
      )}
      {/* phase_rebuild_panel: 活跃重做任务通知条,所有 tab 共享 */}
      <RebuildJobBanner {...rb.bannerProps} />
      {playBlock && (
        <CSAlert type="warning" header={t('scripts.my.play_block_title')}>
          {playBlock}
        </CSAlert>
      )}
      {/* phase_rebuild_panel: 估算确认弹窗,所有卡片重做按钮共享 */}
      <RebuildEstimateModal {...rb.modalProps} />
      {/* tab 栏滚下去就消失了用户找不到当前 tab — Cloudscape Tabs 不暴露
          单独的 tablist 组件,只能 scope CSS 给本组件根节点下的 [role=tablist] 加
          position: sticky。不会影响别处 CSTabs(scope 在 data-detail-tabs 节点)。 */}
      <style>{`
        [data-detail-tabs] > [class*="tabs-header"],
        [data-detail-tabs] [role="tablist"] {
          position: sticky !important;
          top: 0 !important;
          z-index: 30 !important;
          background: var(--color-background-layout-main, #1c1b1a) !important;
        }
      `}</style>
      <div data-detail-tabs>
      <CSTabs activeTabId={tab} onChange={({ detail }) => setTab(detail.activeTabId)} tabs={[
        { id: 'overview', label: t('scripts.editor.tab_overview'), content: (
          <div className="msplit">
            <div className="msplit__media">
              {/* 剧本封面:图片优先 + 宽高比自适应(竖/方/横都完整显示) */}
              {coverUrl ? (
                <CoverFrame src={coverUrl} title={s.title} isOwner={isOwner} onEdit={() => setCoverStudioOpen(true)} />
              ) : (
                <div className="mh-hero mh-hero--empty" style={{ aspectRatio: '16 / 9', cursor: isOwner ? 'pointer' : 'default' }}
                  onClick={isOwner ? () => setCoverStudioOpen(true) : undefined}>
                  <div className="mh-empty__inner">
                    <div className="mh-empty__icon">🎬</div>
                    <div className="mh-empty__title">{s.title}</div>
                    <div className="mh-empty__hint">{isOwner ? t('scripts.page.cover_empty_hint_owner') : t('scripts.page.cover_empty_hint')}</div>
                  </div>
                </div>
              )}
            </div>
            <div className="msplit__body">
            <CSSpaceBetween size="l">
            <CSKeyValuePairs columns={4} items={[
              { label: t('scripts.my.chapters'), value: (s.chapter_count || 0).toLocaleString() },
              { label: t('scripts.my.words'), value: `${((s.word_count || 0) / 10000).toFixed(1)} ${t('scripts.my.wan')}` },
              { label: t('scripts.editor.split_mode'), value: s.import_report?.mode_label || '—' },
              { label: t('scripts.editor.split_confidence'), value: s.import_report?.confidence != null ? `${Math.round(s.import_report.confidence * 100)}%` : '—' },
              { label: t('scripts.editor.saves_count'), value: t('scripts.editor.saves_n', { n: savesCount }) },
              { label: t('scripts.editor.embed_index'), value: (
                <CSSpaceBetween direction="horizontal" size="xxs">
                  <span>{embedLabel}</span>
                  {embedWarning && <CSStatusIndicator type="warning">{embedWarning}</CSStatusIndicator>}
                </CSSpaceBetween>
              ) },
              { label: t('scripts.my.share'), value: s.is_public ? <CSStatusIndicator type="success">{t('scripts.my.is_public')}</CSStatusIndicator> : <CSStatusIndicator type="stopped">{t('scripts.editor.not_public')}</CSStatusIndicator> },
              { label: t('scripts.editor.script_id'), value: <span className="mono">{s.uid}</span> },
            ]} />
            {/* phase_rebuild_panel: 7 模块状态矩阵 — 取代旧 embed 单卡 */}
            <ModuleMatrixOverview {...rb.matrixProps} />
            {/* embed 4 子卡:chunks / cards / worldbook / canon,各独立 include 重嵌 */}
            <CSSpaceBetween size="s">
              <CSHeader variant="h3" description={t('scripts.editor.embed_breakdown_desc', { defaultValue: '向量索引按内容类型拆分,可选择性重嵌。' })}>
                {t('scripts.editor.embed_breakdown_title', { defaultValue: '向量索引' })}
              </CSHeader>
              <CSColumnLayout columns={2} variant="text-grid" minColumnWidth={300}>
                {['chunks', 'cards', 'worldbook', 'canon'].map((kind) => {
                  const s2 = es ? es[kind] : null;
                  const done = s2?.done || 0;
                  const total = s2?.total || 0;
                  const status = !s2 || total === 0
                    ? 'unknown'
                    : (done >= total ? 'ready' : (done > 0 ? 'partial' : 'missing'));
                  return (
                    <ModuleStatusCard
                      key={kind}
                      module="embeddings"
                      scriptId={s.id}
                      status={es?.running ? 'running' : status}
                      doneCount={done}
                      totalCount={total}
                      activeJobId={rb.activeJob ? (rb.activeJob.job_id || rb.activeJob.id) : null}
                      title={t(`scripts.editor.embed_kind_${kind}`, { defaultValue: kind })}
                      description={t('scripts.editor.embed_kind_desc', { defaultValue: 'pgvector embedding_vec 列' })}
                      onRebuild={() => rb.openEstimate({ module: 'embeddings', options: { include: [kind] } })}
                    />
                  );
                })}
              </CSColumnLayout>
            </CSSpaceBetween>
            </CSSpaceBetween>
            </div>
          </div>
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
          <CSSpaceBetween size="l">
            <ModuleStatusCard
              {...rb.cardProps('worldbook')}
              extraActions={
                <CSSpaceBetween direction="horizontal" size="xxs">
                  <CSButton iconName="add-plus"
                    onClick={() => rb.openEstimate({ module: 'worldbook', options: { source: 'canon' } })}>
                    {t('scripts.editor.wb_from_canon', { defaultValue: '从知识库人物反推(免费)' })}
                  </CSButton>
                  <CSButton iconName="gen-ai" variant="primary"
                    onClick={() => rb.openEstimate({ module: 'worldbook', options: { source: 'llm' } })}>
                    {t('scripts.editor.wb_llm_rich', { defaultValue: 'LLM 重提富化' })}
                  </CSButton>
                </CSSpaceBetween>
              }
            />
            <WorldbookEditorView script={s} />
          </CSSpaceBetween>
        ) },
        { id: 'npc', label: t('scripts.editor.tab_npc'), content: (
          <CSSpaceBetween size="l">
            <ModuleStatusCard
              {...rb.cardProps('cards')}
              title={t('scripts.editor.tab_npc')}
              description={t('scripts.editor.cards_desc', { defaultValue: 'NPC 角色卡(可玩),与知识库人物条目不同' })}
            />
            <CSCards loading={loading && npc == null} loadingText={t('scripts.editor.loading_npc')}
            items={npc || []} trackBy="id"
            cardsPerRow={[{ cards: 1 }, { minWidth: 480, cards: 2 }]}
            header={
              <CSHeader counter={`(${(npc || []).length})`}
                actions={
                  <CSSpaceBetween direction="horizontal" size="xs">
                    {isOwner && (npc || []).length >= 2 && (
                      <CSButton iconName="search" onClick={() => setAuditOpen(true)}>
                        {t('scripts.audit.btn', { defaultValue: 'AI 复核人名/语义' })}
                      </CSButton>
                    )}
                    <CSButton iconName="add-plus" onClick={() => setNpcEdit({ card: null, isNew: true })}>{t('scripts.editor.add_npc')}</CSButton>
                  </CSSpaceBetween>
                }>
                {t('scripts.editor.tab_npc')}
              </CSHeader>
            }
            cardDefinition={{
              header: (c) => (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <AvatarImg src={c.avatar_path || null} name={c.name || '?'} size={48} shape="rounded" zoomable />
                    <CSBox variant="h3" padding="n">
                      {c.name || t('scripts.editor.unnamed_npc')}
                      {c.full_name && c.full_name !== c.name && (
                        <CSBox display="inline" color="text-status-inactive" fontSize="body-s" padding={{ left: 'xs' }}>{c.full_name}</CSBox>
                      )}
                      {/* 主角 badge — 后端 _stage_cards canon importance 第 1 名标记 */}
                      {c.metadata && c.metadata.is_protagonist && (
                        <CSBox display="inline" padding={{ left: 'xs' }}>
                          <CSBadge color="severity-high">{t('scripts.page.badge_protagonist')}</CSBadge>
                        </CSBox>
                      )}
                    </CSBox>
                  </div>
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
                  <CSSpaceBetween direction="horizontal" size="xs">
                    <CSButton variant="inline-link" iconName="edit" onClick={() => setNpcEdit({ card: c, isNew: false })}>{t('scripts.editor.view_edit')}</CSButton>
                    {isOwner && !(c.metadata && c.metadata.is_protagonist) && (
                      <CSButton variant="inline-link" iconName="user-profile"
                        loading={protagBusy === c.id}
                        onClick={() => onSetProtagonist(c)}>
                        {t('scripts.editor.set_protagonist', { defaultValue: '设为主角' })}
                      </CSButton>
                    )}
                    {/* NPC → 用户角色卡:任何查看者(含订阅者)都可复制到自己名下,不改原剧本 */}
                    <CSButton variant="inline-link" iconName="add-plus"
                      loading={promoteBusy === c.id}
                      onClick={() => onPromoteNpc(c)}>
                      {t('scripts.editor.promote_npc', { defaultValue: '转为用户角色卡' })}
                    </CSButton>
                  </CSSpaceBetween>
                ) },
              ],
            }}
            empty={<CSBox textAlign="center" color="inherit" padding={{ vertical: 'l' }}>{t('scripts.editor.npc_empty')}</CSBox>} />
          </CSSpaceBetween>
        ) },
        { id: 'canon-editor', label: t('scripts.editor.tab_canon', { defaultValue: '知识库人物' }), content: (
          <CSSpaceBetween size="l">
            <ModuleStatusCard
              {...rb.cardProps('canon')}
              description={t('scripts.editor.canon_desc', { defaultValue: 'kb_canon_entities — LLM 抽出的人物/组织/地点等规范化条目;NPC 角色卡是另一码事。' })}
            />
            <CSBox color="text-body-secondary" fontSize="body-s">
              {t('scripts.editor.canon_editor_todo', { defaultValue: 'kb_canon_entities 表格编辑器:见 /api/scripts/{id}/canon — 表格视图在另一 phase 落地。当前可用"重做"按钮重新抽取。' })}
            </CSBox>
          </CSSpaceBetween>
        ) },
        { id: 'timeline', label: t('scripts.editor.tab_timeline'), content: (
          <CSSpaceBetween size="l">
            <ModuleStatusCard
              {...rb.cardProps('anchors')}
              description={t('scripts.editor.anchors_desc', { defaultValue: 'script_timeline_anchors,从 chapter_facts 的故事时间标签构建,零 LLM' })}
            />
            {(loading && tl == null)
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
                            const summary = String(a.sample_summary || '').replace(/\s+/g, ' ').trim();
                            return (
                              <div
                                key={a.anchor_id}
                                style={{
                                  borderTop: '1px solid var(--line-soft)',
                                  paddingTop: 8,
                                  overflowWrap: 'anywhere',
                                }}
                              >
                                <CSBox fontSize="body-s">
                                  <span className="mono" style={{ color: 'var(--accent)', whiteSpace: 'normal', overflowWrap: 'anywhere' }}>{label || t('scripts.editor.chapter_range', { min: a.chapter_min, max: a.chapter_max })}</span>
                                </CSBox>
                                {summary && (
                                  <CSBox color="text-body-secondary" fontSize="body-s">
                                    <span style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{summary}</span>
                                  </CSBox>
                                )}
                              </div>
                            );
                          })}
                        </CSSpaceBetween>
                      </div>
                    ))}
                  </CSSpaceBetween>}
          </CSSpaceBetween>
        ) },
        { id: 'modules', label: t('scripts.editor.tab_modules', { defaultValue: '模块' }), content: (
          /* 集中视图:7 模块矩阵汇总,跟 overview 的 matrix 数据源一致,但这里独占整 tab 方便高密度操作 */
          <ModuleRebuildPanel scriptId={s.id} />
        ) },
        { id: 'extract', label: t('scripts.editor.tab_extract'), content: (
          /* KbExtractPanel 现仅承担"一键全量 LLM 抽取"(scope=full);单模块重做下放到上述各 tab */
          <KbExtractPanel script={s} onDone={onExtractDone} />
        ) },
        { id: 'gm-style', label: t('scripts.page.tab_gm_style'), content: (
          /* GM 倾向性 6 滑块(剧本级):篇幅/镜头/戏剧密度/心理/悬念/引导,仅 owner 可写 */
          <GmStyleEditor scope="script" scriptId={s.id} canWrite={!!isOwner} />
        ) },
      ]} />
      </div>
      {auditOpen && (
        <CSModal
          visible
          onDismiss={() => { if (!auditBusy) setAuditOpen(false); }}
          header={t('scripts.audit.title', { defaultValue: 'AI 复核 NPC 角色卡' })}
          footer={
            <CSBox float="right">
              <CSSpaceBetween direction="horizontal" size="xs">
                <CSButton onClick={() => setAuditOpen(false)} disabled={auditBusy}>{t('common.cancel', { defaultValue: '取消' })}</CSButton>
                <CSButton variant="primary" loading={auditBusy} onClick={runAudit}>
                  {t('scripts.audit.run', { defaultValue: '开始复核' })}
                </CSButton>
              </CSSpaceBetween>
            </CSBox>
          }
        >
          <CSSpaceBetween size="m">
            <CSBox color="text-body-secondary" fontSize="body-s">
              {t('scripts.audit.desc', { defaultValue: '用所选模型对本剧本全部 NPC 卡做一次复核:合并同一人的多张卡(如 金玉/玉儿/小玉)、识别并锁定真主角、删除官职/地名等非人名卡。按需触发,不影响导入流程与成本。' })}
            </CSBox>
            <AgentModelPicker
              prefPrefix="card_audit"
              fallbackPrefix="gm"
              variant="bare"
              header={undefined}
              description={t('scripts.audit.model_desc', { defaultValue: '选择本次复核用的模型(默认你设置的默认模型,可改;复核质量越好的模型越准)。' })}
              configHash="settings-models"
              onChange={(api_id, model) => setAuditSel({ api_id, model })}
            />
          </CSSpaceBetween>
        </CSModal>
      )}
      {npcEdit && (
        <CardEditModal
          card={npcEdit.card}
          isNew={npcEdit.isNew}
          kind="npc"
          onClose={() => setNpcEdit(null)}
          onPromote={async (c) => { await onPromoteNpc(c || npcEdit.card); }}
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
    </>
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
            { id: 'cover', content: (s) => s.cover_image_url ? (
              <AvatarImg
                src={s.cover_image_url}
                name={s.title}
                size={140}
                shape="rounded"
                aspectRatio="16/9"
                zoomable
              />
            ) : null },
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
  // "状态"列下拉跳转用:点 "去补 worldbook" → 选中剧本 + 详情面板默认到 world tab
  const [pendingTab, setPendingTab] = useStatePL(null);
  const [query, setQuery] = useStatePL("");
  const [scriptPage, setScriptPage] = useStatePL(1);
  const SCRIPT_PAGE_SIZE = 50;

  // task 51: 触发某 script 的向量化(GET status 也走这里 polling)
  const triggerEmbed = React.useCallback(async (sid) => {
    try {
      const r = await fetch(`${window.__API_BASE || ""}/api/scripts/${sid}/embed`, {
        method: "POST", credentials: "include",
      });
      if (!r.ok && r.status !== 200) {
        window.__apiToast?.(t('scripts.toast.embed_fail'), { kind: "danger", detail: `HTTP ${r.status}`, duration: 5000 });
        return;
      }
      const j = await r.json();
      if (j.ok === false) {
        // credentials_required → 用人话引导去 RAG 设置，而不是裸技术错
        const isCredsError = j.code === 'credentials_required' || j.error_key === 'credentials_required' || j.needs_credentials;
        if (isCredsError) {
          const hint = j.hint || j.error || t('scripts.toast.embed_no_embedder_hint');
          window.__apiToast?.(t('scripts.toast.embed_no_embedder'), {
            kind: "warn",
            detail: hint + ' — ' + t('scripts.toast.embed_go_rag_settings'),
            duration: 8000,
            action: { label: t('scripts.import.go_api_settings'), onClick: () => { plNavigate('settings-models'); } },
          });
        } else {
          // 其他失败(含 405 relay 错误被翻译成人话后从 j.error 传出)
          window.__apiToast?.(t('scripts.toast.embed_fail'), { kind: "danger", detail: j.error || t('scripts.toast.unknown_error'), duration: 5000 });
        }
        return;
      }
      window.toast?.(t('scripts.toast.embed_started'), { kind: "ok", detail: t('scripts.toast.embed_started_detail'), duration: 3000 });
      setEmbedStatus(s => ({ ...s, [sid]: j.status }));
    } catch (e) {
      window.__apiToast?.(t('scripts.toast.embed_fail'), { kind: "danger", detail: String(e), duration: 3000 });
    }
  }, [t]);

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
      s.is_subscribed
        ? { id: 'unsubscribe', text: t('scripts.my.action_unsubscribe'), iconName: 'remove', disabled: busyId === s.id }
        : { id: 'delete', text: t('scripts.my.action_delete'), iconName: 'remove', disabled: busyId === s.id },
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
          <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.7 }}>🚧</div>
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
        onEmbed={(s) => triggerEmbed(s.id)}
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
                {s.sharing_mode === 'floating-latest' && <CSBadge color="blue">{t('scripts.share.badge_floating')}</CSBadge>}
                {s.sharing_mode === 'pinned-snapshot' && <CSBadge color="grey">{t('scripts.share.badge_pinned', { id: (s.current_pin_commit_id || '').slice(0, 7) })}</CSBadge>}
                {s.sharing_mode === 'public' && <CSBadge color="green">{t('scripts.share.badge_public')}</CSBadge>}
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
          return (
            <CSButtonDropdown variant="normal" expandToViewport disabled={busyId === s.id || !!block}
              items={[
                ...(svs.length ? [{
                  text: t('scripts.my.play_continue_group'),
                  items: svs.map((sv) => ({ id: 'continue:' + sv.id, text: sv.title || ('#' + sv.id), iconName: 'caret-right-filled' })),
                }] : []),
                { id: 'new', text: t('scripts.my.play_new_game'), iconName: 'add-plus' },
              ]}
              onItemClick={({ detail }) => {
                if (detail.id === 'new') { onNewGame(s); return; }
                if (typeof detail.id === 'string' && detail.id.startsWith('continue:')) {
                  const sv = svs.find((x) => String(x.id) === detail.id.slice('continue:'.length));
                  if (sv) onContinueSave(sv);
                }
              }}
            >{block ? t('scripts.my.play_blocked') : t('scripts.my.play')}</CSButtonDropdown>
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
  // 当前选中章节的完整正文(lazy fetch — 列表 API 只回 180 字符 preview)
  const [activeContent, setActiveContent] = useStatePL("");
  const [activeLoading, setActiveLoading] = useStatePL(false);
  React.useEffect(() => {
    if (!script) return;
    setLoading(true); setErr(""); setActiveIdx(0);
    (async () => {
      try {
        // 一次拉完整本(后端 limit 上限已放到 5000)
        const r = await window.api.scripts.chapters(script.id, { limit: 5000 });
        const list = (r && (r.chapters || r.items)) || [];
        setChapters(list);
      } catch (e) { setErr(e?.message || t('scripts.editor.fetch_fail')); }
      finally { setLoading(false); }
    })();
  }, [script?.id, reloadTick]);
  // 选中章节变化时,lazy fetch 真正文(不预拉全文,避免一次性 12MB 响应)
  React.useEffect(() => {
    if (!script || chapters.length === 0) { setActiveContent(""); return; }
    const cur = chapters[activeIdx];
    if (!cur) { setActiveContent(""); return; }
    // 后端返字段是 chapter_index,不是 index
    const chIdx = cur.chapter_index ?? cur.index ?? activeIdx;
    setActiveLoading(true);
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.scripts.chapterDetail(script.id, chIdx);
        if (cancelled) return;
        setActiveContent((r && r.chapter && r.chapter.content) || "");
      } catch (_) {
        if (!cancelled) setActiveContent(cur.content_preview || "");
      } finally { if (!cancelled) setActiveLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [script?.id, activeIdx, chapters]);
  if (!script) return null;
  const cur = chapters[activeIdx];
  const curIdx = cur ? (cur.chapter_index ?? cur.index ?? activeIdx) : activeIdx;
  const onRename = async () => {
    if (!cur) return;
    const newTitle = await window.__prompt({ title: t('scripts.editor.rename_title'), label: t('scripts.editor.rename_label'), default: cur.title || '' });
    if (!newTitle || newTitle === cur.title) return;
    try {
      await window.api.scripts.updateChapter(script.id, curIdx, { title: newTitle });
      window.__apiToast?.(t('scripts.toast.renamed'), { kind: "ok" });
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.(t('scripts.toast.op_fail'), { kind: "danger", detail: e?.message }); }
  };
  const onMergeNext = async () => {
    if (!cur || activeIdx >= chapters.length - 1) return;
    if (!await window.__confirm({ title: t('scripts.editor.merge_title'), message: t('scripts.editor.merge_msg', { a: activeIdx + 1, b: activeIdx + 2 }), confirmText: t('scripts.editor.merge_btn') })) return;
    try {
      const nextCh = chapters[activeIdx + 1];
      const nextIdx = nextCh ? (nextCh.chapter_index ?? nextCh.index ?? (activeIdx + 1)) : (activeIdx + 1);
      await window.api.scripts.mergeChapter(script.id, { first_index: curIdx, second_index: nextIdx });
      window.__apiToast?.(t('scripts.toast.merged'), { kind: "ok" });
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.(t('scripts.toast.op_fail'), { kind: "danger", detail: e?.message }); }
  };
  // 合并上一章:把前面那章折进【当前章】,保留当前章标题(用户反馈:序章/前言没办法合并到第一章)。
  const onMergePrev = async () => {
    if (!cur || activeIdx <= 0) return;
    if (!await window.__confirm({ title: t('scripts.editor.merge_title'), message: t('scripts.editor.merge_prev_msg', { a: activeIdx, b: activeIdx + 1, defaultValue: `把第 ${activeIdx} 章合并进当前的第 ${activeIdx + 1} 章(保留当前章标题)?` }), confirmText: t('scripts.editor.merge_btn') })) return;
    try {
      const prevCh = chapters[activeIdx - 1];
      const prevIdx = prevCh ? (prevCh.chapter_index ?? prevCh.index ?? (activeIdx - 1)) : (activeIdx - 1);
      await window.api.scripts.mergeChapter(script.id, { first_index: prevIdx, second_index: curIdx, keep_title_index: curIdx });
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
      await window.api.scripts.splitChapter(script.id, curIdx, { split_at: n });
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
                <button key={c.chapter_index ?? c.index ?? i}
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
                  {/* 字数读 word_count 列(后端 import 时已计算),不要算 content.length —
                      列表 API 只回 180 字符 preview,算出来全是 0 字 */}
                  <span className="muted-2 mono" style={{fontSize: 11}}>
                    {(cur.word_count || 0).toLocaleString()} {t('scripts.my.char_unit')}
                  </span>
                  <div style={{marginLeft: "auto", display: "flex", gap: 6}}>
                    <button className="btn ghost" onClick={onRename}><Icon name="edit" size={12} /> {t('scripts.editor.rename_btn')}</button>
                    <button className="btn ghost" onClick={onSplit}><Icon name="branch" size={12} /> {t('scripts.editor.split_chapter_btn')}</button>
                    {activeIdx > 0 && (
                      <button className="btn ghost" onClick={onMergePrev}><Icon name="link" size={12} /> {t('scripts.editor.merge_prev_btn', { defaultValue: '合并上一章' })}</button>
                    )}
                    {activeIdx < chapters.length - 1 && (
                      <button className="btn ghost" onClick={onMergeNext}><Icon name="link" size={12} /> {t('scripts.editor.merge_next_btn')}</button>
                    )}
                  </div>
                </div>
                {/* 正文 lazy 加载;先放 preview,等 chapterDetail 回来再换全文 */}
                <pre style={{whiteSpace: "pre-wrap", fontFamily: "var(--font-serif)", fontSize: 13.5, lineHeight: 1.7, margin: 0}}>
                  {activeLoading
                    ? (cur.content_preview || "") + "\n\n" + t('common.loading')
                    : (activeContent || cur.content_preview || "").slice(0, 8000)
                       + ((activeContent && activeContent.length > 8000) ? t('scripts.editor.content_truncated') : "")}
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
          // 复用与「导入」完全一致的规则列表(= 后端 chapter_splitter.RULE_PATTERNS 的真实键)。
          // 旧版这里硬编码了 blank/marker/regex,后端没有这些规则 → 静默退化成 auto、且 regex
          // 不等于 custom 导致自定义正则被忽略(用户反馈:整本重切识别不出第X章,导入却能)。
          { key: "rule", label: t('scripts.import.field_rule'), type: "select", default: "auto",
            options: SPLIT_RULES.map(r => ({ value: r.id, label: t(r.labelKey) })) },
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
  // extract(知识库人物 arc 抽取)按 arc 算法真实标定:后端 extract/budget.py arc ≈ 1.16M/507章
  // ≈ 2280 tok/章(in+out)。原值 120 严重低估 ~18 倍(群反馈:400万字只预估十几万 token)。
  { id: "extract",  labelKey: "scripts.import.stage_extract",  hintKey: "scripts.import.stage_extract_hint",  tok_per_chap: 2280 },
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
  const [previewProgress, setPreviewProgress] = useStatePL({ value: 0, label: "" });
  const [importBusy, setImportBusy] = useStatePL(false);
  const [importProgress, setImportProgress] = useStatePL("");
  const [importPercent, setImportPercent] = useStatePL(0);
  const [selectedFile, setSelectedFile] = useStatePL(null);
  const [dragOver, setDragOver] = useStatePL(false);
  const [pendingImport, setPendingImport] = useStatePL(null);
  const [pendingPipeline, setPendingPipeline] = useStatePL(null);
  // 拆书流水线 LLM 选择(写入 user prefs.extractor.*,后端 _resolve_extractor_llm 读)
  const [extractApiId, setExtractApiId] = useStatePL('');
  const [extractModel, setExtractModel] = useStatePL('');
  // 完整流水线开关 — 之前 3 处 importPipeline() 都硬编码 true,UI 上没暴露
  // 让用户能关掉(只导入章节/索引,不调 LLM 生 NPC 角色卡/世界书)
  const [enableCards, setEnableCards] = useStatePL(true);
  const [enableWorldbook, setEnableWorldbook] = useStatePL(true);
  const [extractApis, setExtractApis] = useStatePL([]);
  const [credApiIds, setCredApiIds] = useStatePL(new Set()); // 用户已配 key 的 api_id 集合
  const [extractSaving, setExtractSaving] = useStatePL(false);
  // embedder preflight — 导入前检查向量嵌入是否已配置。
  // null = 未加载; {ok, effective_source, preflight:{...}} = 已加载。
  const [embedderStatus, setEmbedderStatus] = useStatePL(null);
  const fileInputRef = React.useRef(null);
  const tickRef = React.useRef(null);

  // 拉 catalog + user prefs + 已配凭证 + embedder preflight,预填提取模型选择
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [profile, models, creds, embedSt] = await Promise.all([
          window.api.account.profile().catch(() => ({})),
          window.api.models.list().catch(() => ({})),
          window.api.credentials.list().catch(() => ({ items: [] })),
          fetch(`${window.__API_BASE || ""}/api/me/embedder/status`, { credentials: 'include' })
            .then(r => r.json()).catch(() => null),
        ]);
        if (cancelled) return;
        const list = models?.models?.apis || (Array.isArray(models?.apis) ? models.apis : []) || [];
        setExtractApis(Array.isArray(list) ? list : []);
        // AgentPlatform 是 Vertex 的 SA 凭证 — UI 里用 vertex_ai
        setCredApiIds(credApiIdSet(creds));
        const p = (profile && profile.preferences) || {};
        // 默认值优先级:用户 prefs > deepseek(如果已配) > 用户第一个已配的 provider
        const preferred = p['extractor.api_id']
          || (ids.has('deepseek') ? 'deepseek' : null)
          || Array.from(ids)[0]
          || 'deepseek';
        setExtractApiId(preferred);
        setExtractModel(p['extractor.model_real_name'] || '');
        if (embedSt?.ok) setEmbedderStatus(embedSt);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const persistExtractor = async (apiId, model) => {
    if (!apiId || !model) return;
    setExtractSaving(true);
    try {
      await window.api.account.preferences({
        'extractor.api_id': apiId,
        'extractor.model_real_name': model,
      });
    } catch (_) {} finally { setExtractSaving(false); }
  };

  // Restore job from localStorage on mount (page-refresh resilient)
  React.useEffect(() => {
    const j = lsGetJSON("rpg.import.job", null);
    if (j && j.status === "running") setJob(j);
    else if (j && j.status === "estimating") setJob(j);
  }, []);

  // Persist job state
  React.useEffect(() => {
    if (job) lsSetJSON("rpg.import.job", job);
    else lsRemove("rpg.import.job");
  }, [job]);

  React.useEffect(() => {
    const item = lsGetJSON(PENDING_IMPORT_KEY, null);
    if (item && item.upload_id) setPendingImport(item);
    const pipe = lsGetJSON(PENDING_IMPORT_PIPELINE_KEY, null);
    if (pipe && pipe.script_id) setPendingPipeline(pipe);
  }, []);

  const persistPendingImport = useCallbackPL((item) => {
    if (!item || !item.upload_id) return;
    const payload = { ...item, updated_at: Date.now() };
    setPendingImport(payload);
    lsSetJSON(PENDING_IMPORT_KEY, payload);
  }, []);

  const clearPendingImport = useCallbackPL(() => {
    setPendingImport(null);
    lsRemove(PENDING_IMPORT_KEY);
  }, []);

  const persistPendingPipeline = useCallbackPL((item) => {
    if (!item || !item.script_id) return;
    const payload = { ...item, updated_at: Date.now() };
    setPendingPipeline(payload);
    lsSetJSON(PENDING_IMPORT_PIPELINE_KEY, payload);
  }, []);

  const clearPendingPipeline = useCallbackPL(() => {
    setPendingPipeline(null);
    lsRemove(PENDING_IMPORT_PIPELINE_KEY);
  }, []);

  const cancelUploadQuietly = useCallbackPL((uploadId) => {
    if (!uploadId) return;
    try { window.api.uploads.cancel(uploadId).catch(() => {}); } catch (_) {}
  }, []);

  const discardEstimate = useCallbackPL((notify = false) => {
    const oldUploadId = estimate?.upload_id;
    if (oldUploadId) cancelUploadQuietly(oldUploadId);
    setEstimate(null);
    setPreviewProgress({ value: 0, label: "" });
    if (notify) {
      window.__apiToast?.(t('scripts.import.preview_invalidated'), {
        kind: "info",
        detail: t('scripts.import.preview_invalidated_detail'),
        duration: 2600,
      });
    }
  }, [estimate, cancelUploadQuietly, t]);

  // 任务真实进度完全由 ImportJobBanner 内部订阅的 SSE 推上来。
  // wizard 这一层不再:
  //  - 轮询 jobStatus 然后把 stages 全部强写 done (那是撒谎)
  //  - 用 setInterval + Math.random 跑 mock tick (那是更撒谎)
  //  - 接 demo / 离线模式 (没有"离线"路径,要么真上传成功要么 toast 失败)
  // 这里只在 mount 时清掉历史残留 tickRef,防御性兜底。
  React.useEffect(() => {
    if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
    return () => { if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; } };
  }, []);

  // task 49：原 fakeFile = {chapters: 162, words: 410_000} 是凭空写的"示例规模"，
  // 不选文件时会展示出来误导用户。删除 fakeFile，未选文件时 startEstimate 直接
  // 提示"请先选择本地文件"，不假装真实，不生成假预算。

  const onPickFile = (file) => {
    if (!file) return;
    // task 141: 测试期只允许 .txt / .md 剧本文本,前端二次校验(配合后端 ext 白名单)
    const name = (file.name || "").toLowerCase();
    if (!/\.(txt|md)$/.test(name)) {
      window.__apiToast?.(t('scripts.page.file_type_unsupported'), { kind: "danger", detail: t('scripts.page.file_type_unsupported_detail'), duration: 2800 });
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      window.__apiToast?.(t('scripts.import.file_too_large'), { kind: "danger", detail: t('scripts.import.file_max_size'), duration: 2400 });
      return;
    }
    discardEstimate(false);
    clearPendingImport();
    setSelectedFile(file);
    setPreviewProgress({ value: 0, label: "" });
    if (!title) setTitle(file.name.replace(/\.(txt|md)$/i, ""));
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onPickFile(f);
  };

  const uploadFileChunks = async (file, onProgress) => {
    // 每片会 base64 编码后再 POST(膨胀 ~1.37×)+ JSON 包裹。512KB raw → ~700KB body,
    // 稳稳低于 nginx 默认 client_max_body_size=1MB。原来 1MB raw → ~1.4MB body 会被默认
    // nginx 直接拒/掐连接,浏览器表现为「网络异常 Failed to fetch」—— 自建/开源用户必踩。
    const CHUNK_SIZE = 512 * 1024;
    const totalBytes = file.size;
    const totalChunks = Math.max(1, Math.ceil(totalBytes / CHUNK_SIZE));
    onProgress?.({ stage: "init", done: 0, total: totalChunks, percent: 0 });
    const init = await window.api.uploads.init({
      filename: file.name,
      total_bytes: totalBytes,
      total_chunks: totalChunks,
    });
    const uploadId = init.upload_id || init.id;
    if (!uploadId) throw new Error(t('scripts.import.no_upload_id'));
    for (let i = 0; i < totalChunks; i++) {
      const blob = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
      await window.api.uploads.chunk(uploadId, blob, i);
      onProgress?.({ stage: "chunk", done: i + 1, total: totalChunks, percent: Math.round(((i + 1) / totalChunks) * 100) });
    }
    onProgress?.({ stage: "finish", done: totalChunks, total: totalChunks, percent: 100 });
    await window.api.uploads.finish(uploadId, {});
    return uploadId;
  };

  // 只构造 label/hint 占位 — SSE 还没推第一帧时 banner 空表很难看。
  // 注意:这里绝对不允许塞 status/progress/tokens_used 的初值,以免在 SSE
  // 推 stage 真实状态前就显示出"已 running"或"已 done"的虚假进度。
  // 真实 status / stage_progress / stage_total / tokens 全部由 SSE 推上来。
  const buildRunningStages = (baseStages) => {
    const source = Array.isArray(baseStages) && baseStages.length
      ? baseStages
      : IMPORT_STAGES.map(s => ({
          id: s.id,
          label: t(s.labelKey),
          hint: t(s.hintKey),
        }));
    return source.map((s) => ({
      id: s.id,
      label: s.label,
      hint: s.hint,
    }));
  };

  const goApiSettings = () => {
    if (pendingImport) persistPendingImport(pendingImport);
    if (pendingPipeline) persistPendingPipeline(pendingPipeline);
    plNavigate("settings-models");
  };

  const resumePendingPipeline = async () => {
    if (!pendingPipeline?.script_id || importBusy) return;
    setImportBusy(true);
    setImportPercent(0);
    setImportProgress(t('scripts.import.pipeline_resuming'));
    try {
      const resp = await window.api.scripts.importPipeline(pendingPipeline.script_id, {
        enable_cards: enableCards,
        enable_worldbook: enableWorldbook,
        budget: pendingPipeline.budget || {},
      });
      if (!resp || resp.ok === false || !resp.job_id) {
        throw new Error((resp && (resp.error || resp.detail)) || t('scripts.import.api_fail'));
      }
      const stages = buildRunningStages(pendingPipeline.stages);
      setJob({
        id: resp.job_id,
        file: pendingPipeline.file || { name: pendingPipeline.file_name || pendingPipeline.title || "script" },
        title: pendingPipeline.title || pendingPipeline.file_name || "script",
        script_id: pendingPipeline.script_id,
        mode: pendingPipeline.mode || "",
        stages,
        totalTokens: pendingPipeline.totalTokens || 0,
        status: "running",
        started_at: Date.now(),
        real: true,
      });
      clearPendingPipeline();
      window.__apiToast?.(t('scripts.import.pipeline_resumed'), { kind: "ok", duration: 2600 });
      // 不写 setImportPercent(100):流水线是 banner 内部 SSE 接管,不再扯 importPercent。
      // 这里只关掉局部 wizard 的 busy 态,banner 单独显示。
    } catch (e) {
      if (isCredentialsRequiredError(e)) {
        const payload = e?.payload || {};
        persistPendingPipeline({ ...pendingPipeline, api_id: payload.api_id, model: payload.model, credential_api_id: payload.credential_api_id });
        window.__apiToast?.(t('scripts.import.api_key_required_title'), {
          kind: "warn",
          detail: t('scripts.import.api_key_required_toast'),
          duration: 5000,
        });
      } else {
        const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
        window.__apiToast?.(t('scripts.import.pipeline_resume_fail'), { kind: "danger", detail, duration: 5000 });
      }
    } finally {
      setImportBusy(false);
      setImportPercent(0);
      setImportProgress("");
    }
  };

  const startEstimate = async () => {
    if (previewBusy || importBusy) return;
    setPreviewBusy(true);
    setEstimate(null);
    setPreviewProgress({ value: 0, label: t('scripts.import.preview_upload_init') });
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
      setPreviewProgress({ value: 0, label: "" });
      return;
    }
    // 选了真实文件：必须打真后端；失败就给用户看清楚错误，绝不回退 fakeFile
    let result = null;
    let uploadId = null;
    try {
      uploadId = await uploadFileChunks(selectedFile, ({ stage, done, total, percent }) => {
        if (stage === "init") {
          setPreviewProgress({ value: 2, label: t('scripts.import.preview_upload_init') });
        } else if (stage === "chunk") {
          setPreviewProgress({
            value: Math.min(80, 2 + Math.round(percent * 0.78)),
            label: t('scripts.import.preview_upload_progress', { done, total }),
          });
        } else if (stage === "finish") {
          setPreviewProgress({ value: 84, label: t('scripts.import.preview_upload_finish') });
        }
      });
      setPreviewProgress({ value: 90, label: t('scripts.import.preview_analyzing') });
      const body = {
        upload_id: uploadId,
        split_rule: rule || "auto",
        custom_pattern: pattern || "",
        sample_limit: 20,
      };
      result = await window.api.scripts.preview(body);
      setPreviewProgress({ value: 100, label: t('scripts.import.preview_done') });
    } catch (e) {
      if (uploadId) { try { await window.api.uploads.cancel(uploadId); } catch (_) {} }
      let detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
      // 网络级失败(fetch 直接抛,没拿到响应)对自建/反代用户最常见的原因是反向代理
      // (nginx/caddy)的请求体积上限太小,或后端没起。给一句可操作的提示,别让用户只看到
      // 一个无解的「Failed to fetch」。
      const isNetErr = (e && (e.code === 'network' || e.status === 0)) || /Failed to fetch|NetworkError|网络异常/i.test(String(detail));
      if (isNetErr) {
        detail = `${detail} —— ${t('scripts.page.upload_net_error_hint')}`;
      }
      window.__apiToast?.(t('scripts.toast.preview_fail'), { kind: "danger", detail, duration: 8000 });
      setEstimate({
        file: { name: selectedFile.name, size: selectedFile.size, chapters: 0, words: 0 },
        chapters: 0, words: 0,
        stages: [], totalTokens: 0, totalSec: 0, cost: 0,
        model: "—",
        warnings: [t('scripts.import.preview_fail_detail', { detail })],
        previewError: detail,
      });
      setPreviewBusy(false);
      setPreviewProgress({ value: 0, label: "" });
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
      warnings.push(t('scripts.page.split_mode_warn', { mode: result.report.mode_label, conf: result.report.confidence ?? "—" }));
    }
    setEstimate({
      file: { name: selectedFile.name, size: selectedFile.size, chapters, words },
      chapters, words,
      stages, totalTokens, totalSec, cost,
      model: result.model || "GPT-4o · RPG 调优",
      preview: result.preview,
      report: result.report,
      warnings,
      upload_id: uploadId,
    });
    setPreviewBusy(false);
  };

  const startImport = async () => {
    // task 17: 真正打通分片上传 → /api/scripts/import 流水线。
    // 之前发的 init 字段 {size, kind, chunk_size} 全不对（后端要 total_bytes/total_chunks）→ 400。
    // 之前任何一步失败仍会创建 fake job 让 UI 假装在跑 → 用户误以为成功。
    // 现在：选了真实文件就必须真传成功；任一步失败 toast 报错并停止，不再造 job。
    if (importBusy) {
      window.__apiToast?.(t('scripts.import.import_busy'), { kind: "info" });
      return;
    }
    if (selectedFile) {
      if (!estimate || !Array.isArray(estimate.stages)) {
        window.__apiToast?.(t('scripts.import.preview_required'), { kind: "warn" });
        return;
      }
      let uploadId = estimate.upload_id || null;
      setImportBusy(true);
      setImportPercent(0);
      setImportProgress(uploadId ? t('scripts.import.import_reuse_upload') : t('scripts.import.upload_init'));
      try {
        // ── 阶段 A: 文件分片上传 — 这是前端唯一真知道进度的环节,占 0-30% ──
        if (!uploadId) {
          uploadId = await uploadFileChunks(selectedFile, ({ stage, done, total, percent }) => {
            if (stage === "init") {
              setImportPercent(1);
              setImportProgress(t('scripts.import.upload_init'));
            } else if (stage === "chunk") {
              // 0-30% 是文件 chunk;30% 之后交给后端 SSE 推 stage 进度,wizard 不再写死 milestone
              setImportPercent(Math.min(30, Math.round((percent || 0) * 0.30)));
              setImportProgress(t('scripts.import.upload_progress', { done, total }));
            } else if (stage === "finish") {
              setImportPercent(30);
              setImportProgress(t('scripts.import.upload_finish'));
            }
          });
        } else {
          // 复用 preview 已传完的 upload — 直接进入 import 创建
          setImportPercent(30);
        }
        // ── 阶段 B: 创建剧本 (importScript) — 不写 milestone 数字,只换文案 ──
        setImportProgress(t('scripts.import.import_creating'));
        const createScriptFromUpload = (nextUploadId) => window.api.scripts.importScript({
          upload_id: nextUploadId,
          title: title || selectedFile.name.replace(/\.(txt|md)$/i, ""),
          split_rule: rule || "auto",
          custom_pattern: pattern || "",
          require_llm_credentials: true,
        });
        const reuploadForExpiredUpload = async () => {
          setImportProgress(t('scripts.import.upload_expired_retry'));
          setImportPercent(0);
          return uploadFileChunks(selectedFile, ({ stage, done, total, percent }) => {
            if (stage === "init") {
              setImportPercent(1);
              setImportProgress(t('scripts.import.upload_init'));
            } else if (stage === "chunk") {
              setImportPercent(Math.min(30, Math.round((percent || 0) * 0.30)));
              setImportProgress(t('scripts.import.upload_progress', { done, total }));
            } else if (stage === "finish") {
              setImportPercent(30);
              setImportProgress(t('scripts.import.upload_finish'));
            }
          });
        };
        let importResp;
        try {
          importResp = await createScriptFromUpload(uploadId);
        } catch (e) {
          if (!isExpiredUploadError(e)) throw e;
          uploadId = await reuploadForExpiredUpload();
          importResp = await createScriptFromUpload(uploadId);
        }
        if (importResp && importResp.ok === false && isExpiredUploadError(importResp)) {
          uploadId = await reuploadForExpiredUpload();
          importResp = await createScriptFromUpload(uploadId);
        }
        if (!importResp || importResp.ok === false) {
          throw new Error((importResp && (importResp.error || importResp.detail)) || t('scripts.import.api_fail'));
        }
        const sc = importResp.script || {};
        // ── 阶段 C: importPipeline 启动 LLM 5-stage 流水线 ─────────────────
        // 之前这里失败被 console.warn 吞掉、wizard 仍然 toast"导入成功"。
        // 现在:启动失败必须 toast danger + 阻断 wizard,不允许进 banner 正常路径。
        let pipelineJobId = null;
        let pipelinePaused = null;
        try {
          setImportProgress(t('scripts.import.import_pipeline'));
          const pipelineResp = await window.api.scripts.importPipeline(sc.id, {
            enable_cards: enableCards,
            enable_worldbook: enableWorldbook,
            budget: estimate,
          });
          if (!pipelineResp || pipelineResp.ok === false || !pipelineResp.job_id) {
            throw new Error((pipelineResp && (pipelineResp.error || pipelineResp.detail)) || t('scripts.import.api_fail'));
          }
          pipelineJobId = pipelineResp.job_id;
        } catch (e) {
          if (isCredentialsRequiredError(e)) {
            const payload = e?.payload || {};
            const createdTitle = sc.title || title || estimate.file.name;
            const modeLabel = (() => { const _r = SPLIT_RULES.find(r => r.id === rule); return _r ? t(_r.labelKey) : rule; })();
            pipelinePaused = {
              script_id: sc.id,
              title: createdTitle,
              file: estimate.file,
              file_name: estimate.file?.name || createdTitle,
              mode: modeLabel,
              stages: estimate.stages,
              totalTokens: estimate.totalTokens,
              budget: estimate,
              api_id: payload.api_id,
              model: payload.model,
              credential_api_id: payload.credential_api_id,
              reason: "credentials_required",
              created_at: Date.now(),
            };
            persistPendingPipeline(pipelinePaused);
            window.__apiToast?.(t('scripts.import.api_key_required_title'), {
              kind: "warn",
              detail: t('scripts.import.api_key_required_toast'),
              duration: 6000,
            });
          } else {
            // 非 credentials 缺失的失败 — 流水线根本没起来,wizard 必须停。
            // 不再 console.warn 静默继续假装"导入成功"。
            const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
            window.__apiToast?.(t('scripts.toast.import_fail'), {
              kind: "danger",
              detail,
              duration: 6000,
            });
            // 剧本壳已存在(章节/chunks 在 importScript 阶段建好了),用户可在 KbExtractPanel 手动重试
            try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
            setJob({
              id: "imp_dispatch_failed_" + sc.id,
              script_id: sc.id,
              title: sc.title || title || estimate.file.name,
              file: estimate.file,
              status: "partial",
              error: detail,
              stages: buildRunningStages(estimate.stages),
              started_at: Date.now(),
              finished_at: Date.now(),
              real: true,
              dispatch_failed: true,
            });
            setEstimate(null);
            return;
          }
        }
        if (pipelinePaused) {
          setJob(null);
          setEstimate(null);
          try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
          window.toast && window.toast(t('scripts.toast.import_ok'), {
            kind: "ok",
            detail: t('scripts.import.import_ok_needs_api', { id: sc.id, title: sc.title || "" }),
            duration: 5000,
          });
          return;
        }
        // ── 阶段 D: 流水线已派发,job_id 拿到了 — banner 内部订 SSE ──
        const stages = buildRunningStages(estimate.stages);
        const j = {
          id: pipelineJobId,
          file: estimate.file,
          title: sc.title || title || estimate.file.name,
          script_id: sc.id,
          mode: (() => { const _r = SPLIT_RULES.find(r => r.id === rule); return _r ? t(_r.labelKey) : rule; })(),
          stages,
          totalTokens: estimate.totalTokens,
          status: "running",
          started_at: Date.now(),
          real: true,
        };
        // 不写 setImportPercent(100):任务还没真完,只是后端已经接手。
        // 真正完成由 banner 内部 SSE on_done → setJob({status:'done'/'failed'/...}) 触发。
        setJob(j);
        setEstimate(null);
        // 通知外部 ScriptsPage 刷新真实列表
        try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
        // 这里只 toast"已派发后台",不是"导入完成" — 完成 toast 由 banner SSE done 时发。
        // 后端已派发流水线,但任务还在跑 — toast 用"导入进行中"(已有 key),
        // 不用 import_ok 那种"导入成功"的撒谎话术
        window.__apiToast?.(t('scripts.import.importing_bg'), {
          kind: "info",
          detail: t('scripts.toast.import_ok_detail', { id: sc.id, title: sc.title || "" }),
          duration: 3000,
        });
      } catch (e) {
        if (isCredentialsRequiredError(e)) {
          const payload = e?.payload || {};
          const draftTitle = title || selectedFile.name.replace(/\.(txt|md)$/i, "");
          persistPendingImport({
            upload_id: uploadId,
            title: draftTitle,
            file: estimate?.file || { name: selectedFile.name, size: selectedFile.size },
            file_name: estimate?.file?.name || selectedFile.name,
            split_rule: rule || "auto",
            custom_pattern: pattern || "",
            stages: estimate?.stages || [],
            totalTokens: estimate?.totalTokens || 0,
            budget: estimate || {},
            api_id: payload.api_id,
            model: payload.model,
            credential_api_id: payload.credential_api_id,
            reason: "credentials_required",
            created_at: Date.now(),
          });
          setJob(null);
          window.__apiToast?.(t('scripts.import.api_key_required_title'), {
            kind: "warn",
            detail: t('scripts.import.api_key_required_preimport_toast'),
            duration: 7000,
          });
          return;
        }
        // 取消任何已经初始化的 upload，让服务器释放临时块
        if (uploadId) { try { await window.api.uploads.cancel(uploadId); } catch (_) {} }
        const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
        window.__apiToast?.(t('scripts.toast.import_fail'), { kind: "danger", detail, duration: 5000 });
        // 关键：不要建 fake job 让用户误以为在跑
        setJob(null);
        // estimate 保留，以便用户修改设置后重试
      } finally {
        setImportBusy(false);
        setImportProgress("");
        setImportPercent(0);
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

  const resumePendingImport = async () => {
    if (!pendingImport?.upload_id || importBusy) return;
    setImportBusy(true);
    setImportPercent(0);
    setImportProgress(t('scripts.import.import_creating'));
    try {
      const importResp = await window.api.scripts.importScript({
        upload_id: pendingImport.upload_id,
        title: pendingImport.title || pendingImport.file_name || "",
        split_rule: pendingImport.split_rule || "auto",
        custom_pattern: pendingImport.custom_pattern || "",
        require_llm_credentials: true,
      });
      if (!importResp || importResp.ok === false) {
        const err = new Error((importResp && (importResp.error || importResp.detail)) || t('scripts.import.api_fail'));
        err.payload = importResp;
        throw err;
      }
      const sc = importResp.script || {};
      // 不写 setImportPercent(92):流水线进度由 banner 内部 SSE 接管。
      setImportProgress(t('scripts.import.import_pipeline'));
      const pipelineResp = await window.api.scripts.importPipeline(sc.id, {
        enable_cards: enableCards,
        enable_worldbook: enableWorldbook,
        budget: pendingImport.budget || {},
      });
      if (!pipelineResp || pipelineResp.ok === false || !pipelineResp.job_id) {
        throw new Error((pipelineResp && (pipelineResp.error || pipelineResp.detail)) || t('scripts.import.api_fail'));
      }
      const baseStages = pendingImport.stages || pendingImport.budget?.stages || [];
      const stages = buildRunningStages(baseStages);
      setJob({
        id: pipelineResp.job_id,
        file: pendingImport.file || { name: pendingImport.file_name || pendingImport.title || "script" },
        title: sc.title || pendingImport.title || pendingImport.file_name || "script",
        script_id: sc.id,
        mode: (() => { const _r = SPLIT_RULES.find(r => r.id === (pendingImport.split_rule || "auto")); return _r ? t(_r.labelKey) : (pendingImport.split_rule || "auto"); })(),
        stages,
        totalTokens: pendingImport.totalTokens || 0,
        status: "running",
        started_at: Date.now(),
        real: true,
      });
      clearPendingImport();
      clearPendingPipeline();
      try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
      // 同 startImport:派发成功仅"已开始",不是"已完成"
      window.__apiToast?.(t('scripts.import.importing_bg'), {
        kind: "info",
        detail: t('scripts.toast.import_ok_detail', { id: sc.id, title: sc.title || "" }),
        duration: 3000,
      });
    } catch (e) {
      if (isCredentialsRequiredError(e)) {
        const payload = e?.payload || {};
        persistPendingImport({
          ...pendingImport,
          api_id: payload.api_id,
          model: payload.model,
          credential_api_id: payload.credential_api_id,
        });
        window.__apiToast?.(t('scripts.import.api_key_required_title'), {
          kind: "warn",
          detail: t('scripts.import.api_key_required_preimport_toast'),
          duration: 6000,
        });
      } else {
        const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || t('scripts.toast.unknown_error');
        if (isExpiredUploadError(e)) {
          clearPendingImport();
          window.__apiToast?.(t('scripts.import.saved_upload_expired'), {
            kind: "warn",
            detail: t('scripts.import.saved_upload_expired_detail'),
            duration: 7000,
          });
        } else {
        window.__apiToast?.(t('scripts.toast.import_fail'), { kind: "danger", detail, duration: 5000 });
        }
      }
    } finally {
      setImportBusy(false);
      setImportProgress("");
      setImportPercent(0);
    }
  };

  const cancelJob = async () => {
    if (!job) return;
    if (job.real) {
      try { await window.api.scripts.jobCancel(job.id); } catch (e) {}
    }
    setJob(j => ({ ...j, status: "cancelled", cancelled_at: Date.now() }));
    window.__apiToast?.(t('scripts.toast.import_cancelled'), {
      kind: "warn",
      detail: t('scripts.import.result_cancelled_detail', { id: job.id }),
      duration: 8000,
    });
  };

  const dismissJob = () => {
    setJob(null);
  };

  // banner 内部 SSE 每帧推上来,merge 进 job state — 注意不能覆盖 wizard 这边的
  // 元数据 (title / file / mode / stages 占位 label / hint) — 这些后端 SSE 不会推。
  const onJobSseUpdate = useCallbackPL((jb) => {
    if (!jb || typeof jb !== 'object') return;
    setJob((prev) => {
      if (!prev) return prev;
      // 把后端字段覆盖上去,但保留 wizard 注入的 file/title/mode/stages 占位。
      // stages: 后端会推 stages 数组(每个元素含 id/label/status/count?);如果有 → 取真值,
      // 否则保留前端占位(只有 id/label/hint)。这是唯一允许的 fallback,
      // 但 status 字段绝对不允许在 SSE 没推时被前端推断。
      const sseStages = Array.isArray(jb.stages) ? jb.stages : null;
      const mergedStages = sseStages && sseStages.length
        ? sseStages.map((s, i) => {
            const placeholder = (prev.stages && prev.stages[i]) || {};
            return {
              ...placeholder,  // label/hint 占位
              ...s,            // 后端真值 (status/count/...)
            };
          })
        : prev.stages;
      return {
        ...prev,
        ...jb,
        id: jb.job_id || jb.id || prev.id,
        stages: mergedStages,
      };
    });
  }, []);

  // SSE done event 触发 — 任务终态 toast。注意 jb 这里只有 {status} 字段,
  // 完整 job 在前面 update 帧已 merge 进 state,从最新 prev 读。
  const onJobSseDone = useCallbackPL(() => {
    setJob((prev) => {
      if (!prev) return prev;
      const stages = Array.isArray(prev.stages) ? prev.stages : [];
      const errored = stages.filter(s => s && (s.status === 'error' || s.status === 'failed'));
      const hasErr = errored.length > 0 || prev.status === 'failed' || prev.status === 'done_with_errors';
      const detail = errored.length
        ? errored.map(s => (s.id || s.label || '?') + ': ' + (s.error || t('scripts.toast.unknown_error'))).join('; ')
        : (prev.error || '');
      if (prev.status === 'cancelled') {
        window.__apiToast?.(t('scripts.toast.import_cancelled'), {
          kind: 'warn',
          detail: t('scripts.import.result_cancelled_detail', { id: prev.id || prev.job_id || '?' }),
          duration: 8000,
        });
      } else if (prev.status === 'failed') {
        window.__apiToast?.(t('scripts.toast.import_fail'), { kind: 'danger', detail: detail || t('scripts.toast.unknown_error'), duration: 5000 });
      } else if (hasErr) {
        window.__apiToast?.(t('scripts.toast.import_partial'), { kind: 'warn', detail, duration: 6000 });
      } else {
        window.__apiToast?.(t('scripts.toast.import_ok'), { kind: 'ok', detail: t('scripts.toast.import_ok_detail', { id: prev.script_id || '?', title: prev.title || '' }), duration: 3000 });
      }
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      const finalStatus = IMPORT_JOB_TERMINAL_STATUSES.has(prev.status)
        ? prev.status
        : (hasErr ? 'done_with_errors' : 'done');
      return { ...prev, status: finalStatus, finished_at: Date.now() };
    });
  }, [t]);

  const onJobSseError = useCallbackPL(() => {
    window.__apiToast?.(t('scripts.toast.sse_disconnected'), { kind: 'warn', duration: 3000 });
  }, [t]);

  const ruleOpt = SPLIT_RULES.find(r => r.id === rule) || SPLIT_RULES[0];
  const ruleLabel = t(ruleOpt.labelKey);
  const fileName = (selectedFile && selectedFile.name) || (estimate && estimate.file && estimate.file.name) || null;
  const jobRunning = job && !IMPORT_JOB_TERMINAL_STATUSES.has(job.status);

  return (
    <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
      {/* 左:模块平铺 */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <CSSpaceBetween size="l">
          {jobRunning && (
            <ImportJobBanner
              job={job}
              onCancel={cancelJob}
              onUpdate={onJobSseUpdate}
              onDone={onJobSseDone}
              onError={onJobSseError}
            />
          )}
          {job && IMPORT_JOB_TERMINAL_STATUSES.has(job.status) && (
            <ImportJobResult job={job} onDismiss={dismissJob} onReuse={() => { setJob(null); setEstimate(null); }} />
          )}
          {pendingImport && !jobRunning && (
            <CSAlert
              type="warning"
              header={t('scripts.import.api_key_required_title')}
              action={
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSButton onClick={resumePendingImport} loading={importBusy} disabled={importBusy}>
                    {t('scripts.import.resume_import')}
                  </CSButton>
                  <CSButton variant="primary" iconName="settings" onClick={goApiSettings}>
                    {t('scripts.import.go_api_settings')}
                  </CSButton>
                </CSSpaceBetween>
              }
            >
              {t('scripts.import.api_key_required_preimport_body', {
                title: pendingImport.title || pendingImport.file_name || t('scripts.import.unnamed'),
                provider: pendingImport.credential_api_id || pendingImport.api_id || 'API',
              })}
            </CSAlert>
          )}
          {pendingPipeline && !jobRunning && (
            <CSAlert
              type="warning"
              header={t('scripts.import.api_key_required_title')}
              action={
                <CSSpaceBetween direction="horizontal" size="xs">
                  <CSButton onClick={resumePendingPipeline} loading={importBusy} disabled={importBusy}>
                    {t('scripts.import.resume_pipeline')}
                  </CSButton>
                  <CSButton variant="primary" iconName="settings" onClick={goApiSettings}>
                    {t('scripts.import.go_api_settings')}
                  </CSButton>
                </CSSpaceBetween>
              }
            >
              {t('scripts.import.api_key_required_body', {
                title: pendingPipeline.title || pendingPipeline.file_name || t('scripts.import.unnamed'),
                provider: pendingPipeline.credential_api_id || pendingPipeline.api_id || 'API',
              })}
            </CSAlert>
          )}

          <CSContainer header={<CSHeader variant="h2" description={t('scripts.import.basic_desc')}>{t('scripts.import.basic_title')}</CSHeader>}>
            <CSColumnLayout columns={2}>
              <CSFormField label={t('scripts.import.field_title')} description={t('scripts.import.field_title_desc')}>
                <CSInput value={title} onChange={({ detail }) => setTitle(detail.value)} placeholder={t('scripts.import.field_title_desc')} />
              </CSFormField>
              <CSFormField label={t('scripts.import.field_rule')}>
                <CSSelect selectedOption={{ value: ruleOpt.id, label: ruleLabel }}
                  options={SPLIT_RULES.map(r => ({ value: r.id, label: t(r.labelKey) }))}
                  onChange={({ detail }) => {
                    const nextRule = detail.selectedOption.value || "auto";
                    if (nextRule !== rule) discardEstimate(true);
                    setRule(nextRule);
                  }} />
              </CSFormField>
              <div style={{ gridColumn: '1 / -1' }}>
                <CSFormField label={t('scripts.import.field_custom_regex')} description={t('scripts.import.field_custom_regex_desc')}>
                  <CSInput value={pattern} onChange={({ detail }) => {
                    if (detail.value !== pattern && estimate) discardEstimate(false);
                    setPattern(detail.value);
                  }}
                    disabled={rule !== 'custom'} placeholder={t('scripts.import.field_custom_regex_placeholder')} />
                </CSFormField>
              </div>
            </CSColumnLayout>
          </CSContainer>

          {/* RAG / embedder 引导:导入后向量索引需要独立配置,与主 LLM Key 无关。
              确定性检查:embedderStatus 来自后端 /api/me/embedder/status preflight,
              不依赖 LLM 判断 — 有配 key + provider_ok 才是 ok。*/}
          {embedderStatus && embedderStatus.effective_source === 'none' && !embedderStatus.preflight?.ok && (
            <CSAlert
              type="info"
              header={t('scripts.import.embedder_not_configured_title', { defaultValue: '未配置 RAG / 向量嵌入模型（可选，但建议配置）' })}
              action={
                <CSButton iconName="settings" variant="primary" onClick={() => { plNavigate('settings-models'); }}>
                  {t('scripts.import.go_rag_settings', { defaultValue: '去设置 RAG 模型' })}
                </CSButton>
              }
            >
              {t('scripts.import.embedder_not_configured_body', {
                defaultValue:
                  '向量索引需要单独配置「向量嵌入（RAG）模型」，与主对话用的 LLM Key 是分开的两件事；没配它就无法生成向量索引，RAG 语义召回会退化为关键字匹配。\n注意：不是所有厂商都有向量嵌入接口——Anthropic、DeepSeek 没有。请在「设置 → RAG / 向量模型」配置一个支持 /embeddings 接口的 API Key（如 OpenAI、通义千问 Qwen、硅基流动、Cohere、Vertex 等）。',
              })}
            </CSAlert>
          )}
          {embedderStatus && embedderStatus.preflight?.last_error_hint && (
            <CSAlert
              type="warning"
              header={t('scripts.import.embedder_error_title', { defaultValue: '向量嵌入配置可能有问题' })}
              action={
                <CSButton iconName="settings" onClick={() => { plNavigate('settings-models'); }}>
                  {t('scripts.import.go_rag_settings', { defaultValue: '去 RAG 设置检查' })}
                </CSButton>
              }
            >
              {embedderStatus.preflight.last_error_hint}
            </CSAlert>
          )}

          {/* 拆书流水线 LLM 选择 — 写入 user prefs.extractor.*,可在「设置 → 模块模型」覆盖 */}
          <CSContainer header={<CSHeader variant="h2" description={t('scripts.page.extractor_model_desc')}>{t('scripts.page.extractor_model_title')}</CSHeader>}>
            {/* 统一共享组件:Provider+Model 选择 + 「未配 key」警告 + 写 user prefs.extractor.*,
                与「设置 → 按模块分配模型」的提取器、cards 的 card_import 同一实现。
                后端 import-pipeline 读 extractor.* prefs(不当场传参),所以这里只需持久化偏好。 */}
            <AgentModelPicker
              prefPrefix="extractor"
              preferProvider="deepseek"
              defaultModel={null}
              variant="bare"
              persistOnMount
              configHash="settings-models"
            />
          </CSContainer>

          {/* 完整流水线开关 — 之前两个 toggle 在代码里硬编码 true,UI 不暴露,
              用户压根不知道导入会自动跑 LLM 生 NPC 角色卡+世界书。现在显式给开关。 */}
          <CSContainer header={<CSHeader variant="h2"
            description={t('scripts.import.pipeline_options_desc')}>
            {t('scripts.import.pipeline_options_title')}
          </CSHeader>}>
            <CSColumnLayout columns={2}>
              <CSFormField label={t('scripts.import.enable_cards_label')}
                description={t('scripts.import.enable_cards_desc')}>
                <CSToggle checked={enableCards} onChange={({ detail }) => setEnableCards(detail.checked)}>
                  {enableCards ? t('common.enabled') : t('common.disabled')}
                </CSToggle>
              </CSFormField>
              <CSFormField label={t('scripts.import.enable_worldbook_label')}
                description={t('scripts.import.enable_worldbook_desc')}>
                <CSToggle checked={enableWorldbook} onChange={({ detail }) => setEnableWorldbook(detail.checked)}>
                  {enableWorldbook ? t('common.enabled') : t('common.disabled')}
                </CSToggle>
              </CSFormField>
            </CSColumnLayout>
            {(!enableCards || !enableWorldbook) && (
              <CSBox fontSize="body-s" color="text-status-warning" padding={{ top: 'xs' }}>
                {t('scripts.import.partial_pipeline_warn')}
              </CSBox>
            )}
          </CSContainer>

          <CSContainer header={<CSHeader variant="h2" description={t('scripts.import.file_desc')}>{t('scripts.import.file_title')}</CSHeader>}>
            <CSFileUpload
              value={selectedFile ? [selectedFile] : []}
              onChange={({ detail }) => {
                const f = detail.value?.[0];
                if (f) onPickFile(f);
                else {
                  discardEstimate(false);
                  clearPendingImport();
                  setSelectedFile(null);
                }
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
                <CSButton variant="primary" iconName="search" loading={previewBusy} disabled={!selectedFile || !!job || importBusy} onClick={startEstimate}>
                  {previewBusy ? t('scripts.import.calculating') : t('scripts.import.preview_split')}
                </CSButton>
              )}
              {previewBusy && (
                <CSProgressBar
                  value={previewProgress.value || 0}
                  label={t('scripts.import.preview_progress')}
                  additionalInfo={previewProgress.label}
                  status="in-progress"
                />
              )}
              {estimate && !job && (
                <>
                  <CSButton variant="primary" iconName="check" loading={importBusy} disabled={importBusy} onClick={startImport}>
                    {importBusy ? t('scripts.import.import_creating') : t('scripts.import.confirm_import_bg')}
                  </CSButton>
                  <CSButton disabled={importBusy} onClick={() => discardEstimate(false)}>{t('scripts.import.re_estimate')}</CSButton>
                </>
              )}
              {importBusy && (
                <CSProgressBar
                  value={importPercent || 0}
                  label={t('scripts.import.import_progress')}
                  additionalInfo={importProgress || t('scripts.import.importing_bg')}
                  status="in-progress"
                />
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

// ImportJobBanner 现在是 SSE 真值 view:
//  - 接 job (含 id) + 回调 onUpdate / onDone / onError / onCancel
//  - 进 mount 后立即对 job.id 订 /api/scripts/import-jobs/{id}/stream
//  - 每帧把 SSE 推上来的 job 对象交给 onUpdate(jb) 由 wizard merge 进 state
//  - 结束 (done event) 时调 onDone 让 wizard 发 toast
//  - 进度条 / stage 状态 / tokens 全部直接 read 自 props.job (wizard 已 merge)
//  - 绝不在前端推断 status / progress / tokens_used — SSE 没推就显示 pending
function ImportJobBanner({ job, onCancel, onUpdate, onDone, onError }) {
  const { t } = useTranslation();
  const esRef = React.useRef(null);
  const pollRef = React.useRef(null);
  const jobId = job && job.id;
  const dispatchFailed = !!(job && job.dispatch_failed);

  React.useEffect(() => {
    // dispatch 失败的"假 job" — 没有真 job_id,不订 SSE
    if (!jobId || dispatchFailed) return undefined;
    if (typeof jobId === 'string' && jobId.startsWith('imp_dispatch_failed_')) return undefined;
    let stopped = false;
    const stopPoll = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
    const startPoll = () => {
      // SSE 断开降级:2s 轮询 jobStatus,stages 字段仍取后端真值,绝不强翻 done
      if (pollRef.current) return;
      const tick = async () => {
        if (stopped) return;
        try {
          const resp = await window.api.scripts.jobStatus(jobId);
          if (stopped) return;
          const jb = resp && (resp.job || resp);
          if (jb && jb.status) {
            if (onUpdate) onUpdate(jb);
            if (IMPORT_JOB_TERMINAL_STATUSES.has(jb.status)) {
              stopPoll();
              if (onDone) onDone();
            }
          }
        } catch (_) { /* 单次失败不影响下次 */ }
      };
      tick();
      pollRef.current = setInterval(tick, 2000);
    };
    try {
      esRef.current = window.api.scripts.streamImport(jobId, {
        on_message: (jb) => { if (onUpdate) onUpdate(jb); },
        on_update: (jb) => { if (onUpdate) onUpdate(jb); },
        on_done: () => { stopPoll(); if (onDone) onDone(); },
        on_error: (err) => {
          if (onError) onError(err);
          startPoll();
        },
      });
    } catch (e) {
      if (onError) onError(e);
      startPoll();
    }
    return () => {
      stopped = true;
      stopPoll();
      try { esRef.current && esRef.current.close && esRef.current.close(); } catch (_) {}
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  const stages = Array.isArray(job.stages) ? job.stages : [];
  // 进度条 100% 由后端 overall_progress / overall_total 推。前端不算 derived 假值。
  const overallProgress = Number(job.overall_progress || 0);
  const overallTotal = Math.max(1, Number(job.overall_total || stages.length || 1));
  const overallPct = Math.min(100, Math.max(0, Math.round((overallProgress / overallTotal) * 100)));
  const elapsed = job.started_at ? Math.round((Date.now() - job.started_at) / 1000) : 0;
  const currentStage = job.stage || null;
  const stageProgress = Number(job.stage_progress || 0);
  const stageTotal = Number(job.stage_total || 0);
  const usage = job.usage_actual || null;

  // task #65: 排队中状态分支
  const isQueued = job.status === 'queued';
  const queuePos = isQueued && job.queue_position != null ? Number(job.queue_position) : 0;
  const queueEta = Math.max(1, queuePos) * 8; // 8 分钟/任务保守估算

  return (
    <CSContainer
      header={
        <CSHeader
          variant="h2"
          description={isQueued
            ? t('scripts.import.queued_desc', { n: queuePos, eta: queueEta })
            : t('scripts.import.banner_desc', { id: jobId, elapsed })}
          actions={<CSButton iconName="close" onClick={onCancel}>{t('scripts.import.cancel_import')}</CSButton>}
        >
          <CSStatusIndicator type={isQueued ? 'pending' : 'in-progress'}>
            {isQueued ? t('scripts.import.queued') : t('scripts.import.importing')} · {job.title}
          </CSStatusIndicator>
        </CSHeader>
      }
    >
      <CSSpaceBetween size="m">
        {isQueued ? (
          /* task #65: 排队中 — 灰色脉冲条 + 队列信息;阶段灯暗 */
          <div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 6 }}>
              {t('scripts.import.overall_progress')}
            </div>
            <div className="pl-import-progress-bar" style={{ background: 'rgba(255,255,255,0.08)' }}>
              <div
                className="pl-import-progress-fill"
                style={{
                  width: '100%',
                  background: 'rgba(180,180,180,0.25)',
                  animation: 'pulse 1.8s ease-in-out infinite',
                }}
              />
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted-2)', marginTop: 5 }}>
              {t('scripts.import.queued_desc', { n: queuePos, eta: queueEta })}
            </div>
          </div>
        ) : (
          <CSProgressBar
            value={overallPct}
            label={t('scripts.import.overall_progress')}
            additionalInfo={currentStage ? `${currentStage}${stageTotal ? ` ${stageProgress}/${stageTotal}` : ''}` : ''}
            status="in-progress"
          />
        )}
        {/* task #65: 排队中时不展示阶段灯,等 running 后才亮 */}
        {!isQueued && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12 }}>
            {stages.map((s, i) => {
              // 严格 SSE 真值:status 必须是 'done'/'running'/'error'/'failed' 之一,
              // 任何其他值/缺失一律视作 pending,绝不强转 done。
              let type = 'pending';
              if (s.status === 'done') type = 'success';
              else if (s.status === 'running') type = 'in-progress';
              else if (s.status === 'error' || s.status === 'failed') type = 'error';
              // 进度文案完全由后端字段决定:有 count 显示 count,有 stage_progress 显示 x/y,
              // 否则保持空 — 不再 Math.round(progress*100) 假装百分比
              let meta = '';
              if (typeof s.count === 'number') meta = `${fmtN(s.count)}`;
              else if (typeof s.tokens_used === 'number') meta = `${fmtN(s.tokens_used)} tok`;
              else if (s.status === 'running' && s.id === currentStage && stageTotal) meta = `${stageProgress}/${stageTotal}`;
              const errDetail = (s.status === 'error' || s.status === 'failed') ? (s.error || '') : '';
              return (
                <div key={s.id || i}>
                  <CSStatusIndicator type={type}>{String(i + 1).padStart(2, '0')} · {s.label || s.id}</CSStatusIndicator>
                  <CSBox fontSize="body-s" color={errDetail ? 'text-status-error' : 'text-body-secondary'}>
                    {errDetail || `${s.hint || ''}${meta ? ' · ' + meta : ''}`}
                  </CSBox>
                </div>
              );
            })}
          </div>
        )}
        {usage && (
          <CSBox fontSize="body-s" color="text-body-secondary">
            {usage.usd != null ? `$${Number(usage.usd).toFixed(3)}` : ''}
            {usage.input_tokens != null ? ` · in ${Number(usage.input_tokens).toLocaleString()}` : ''}
            {usage.output_tokens != null ? ` · out ${Number(usage.output_tokens).toLocaleString()}` : ''}
            {usage.llm_calls != null ? ` · ${usage.llm_calls} calls` : ''}
            {usage.live ? ' · live' : ''}
          </CSBox>
        )}
      </CSSpaceBetween>
    </CSContainer>
  );
}

function ImportJobResult({ job, onDismiss, onReuse }) {
  const { t } = useTranslation();
  const ok = job.status === "done";
  const cancelled = job.status === "cancelled";
  const failed = job.status === "failed" || job.dispatch_failed;
  const partial = job.status === "partial" || job.status === "done_with_errors";
  const stages = Array.isArray(job.stages) ? job.stages : [];
  // 失败 stage 明细 — 给用户看清楚是哪一步崩
  const errored = stages.filter(s => s && (s.status === 'error' || s.status === 'failed'));
  // 真实 token 数:优先用 usage_actual (后端官方账),否则降级到 stages 累加
  const usage = job.usage_actual || {};
  const totalTokens = usage.input_tokens != null
    ? (Number(usage.input_tokens || 0) + Number(usage.output_tokens || 0))
    : stages.reduce((a, s) => a + (Number(s.tokens_used) || 0), 0);
  const type = ok ? 'success' : failed ? 'error' : partial ? 'warning' : 'warning';
  const headerKey = ok ? 'scripts.import.result_done'
    : failed ? 'scripts.toast.import_fail'
    : partial ? 'scripts.toast.import_partial'
    : 'scripts.import.result_cancelled';
  return (
    <CSAlert
      type={type}
      dismissible
      onDismiss={onDismiss}
      header={`${t(headerKey)} · ${job.title || ''}`}
      action={
        <CSSpaceBetween direction="horizontal" size="xs">
          {ok && <CSButton variant="primary" onClick={() => { onDismiss && onDismiss(); plNavigate('scripts'); }}>{t('scripts.import.go_manage')}</CSButton>}
          <CSButton onClick={onReuse}>{ok ? t('scripts.import.import_another') : t('scripts.import.retry')}</CSButton>
        </CSSpaceBetween>
      }
    >
      {ok && t('scripts.import.tok_consumed', { n: fmtN(totalTokens) })}
      {cancelled && t('scripts.import.result_cancelled_detail', { id: job.id })}
      {(failed || partial) && (
        <CSSpaceBetween size="xxs">
          <CSBox>{job.error || (errored.length ? `${errored.length} stage(s) failed` : `job ${job.id}`)}</CSBox>
          {errored.length > 0 && (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {errored.map((s, i) => (
                <li key={i}>{(s.id || s.label || '?')}: {s.error || t('scripts.toast.unknown_error')}</li>
              ))}
            </ul>
          )}
        </CSSpaceBetween>
      )}
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
  // scope: 'full' = 全量重提取(LLM 重型),'embed_only' = 仅重嵌入向量(无 LLM,$0)
  // 旧"提取"按钮其实=重新导入剧本,改成显式 scope 让用户清楚选哪种
  const [scope, setScope] = useStatePL('full');
  const [algorithm, setAlgorithm] = useStatePL('arc');
  // Provider/Model 统一由 AgentModelPicker(prefPrefix=extractor)管理:它解析用户
  // 已配凭据 + 偏好后通过 onChange 回传 {api_id, model_real_name},这里只持有回传值
  // 用于拼请求体。与本文件「提取模型」(L3047)同一套实现,不再自造平行选择器。
  const [model, setModel] = useStatePL('');
  const [apiId, setApiId] = useStatePL('');
  const [targetArcs, setTargetArcs] = useStatePL('100');
  const [concurrency, setConcurrency] = useStatePL('15');
  const [authorEra, setAuthorEra] = useStatePL('');
  const [maxUsd, setMaxUsd] = useStatePL('10');
  // 章节范围(可空 → 全书);用户想"只重做第 1-50 章"时用
  const [chapterMin, setChapterMin] = useStatePL('');
  const [chapterMax, setChapterMax] = useStatePL('');
  const [estimate, setEstimate] = useStatePL(null);
  // 强制估算 — 这个 hash 记估算时的参数,跟当前参数不一致 → 开始按钮锁死
  const [estimatedHash, setEstimatedHash] = useStatePL('');
  const [estimating, setEstimating] = useStatePL(false);
  const [job, setJob] = useStatePL(null);
  const [phase, setPhase] = useStatePL('config'); // config | running | done | error
  const [err, setErr] = useStatePL('');
  const esRef = React.useRef(null);

  React.useEffect(() => () => { try { esRef.current && esRef.current.close && esRef.current.close(); } catch (_) {} }, []);

  // 切走标签页又切回来时,extract 流被本组件 unmount 切断 — 这里复活:
  // 拉本剧本最近一条 import_job;若 pending/running,直接重新订 SSE,
  // 让用户能继续看进度而不是空表 + 不知道 token 在不在烧。
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.scripts.activeJob(sid);
        if (cancelled || !r || !r.ok || !r.active) return;
        const jb = r.job || {};
        const jid = jb.job_id || jb.id;
        if (!jid) return;
        // 立即把已有快照塞进去,SSE 还在建连接时也能先看到进度
        setJob({ ...jb, job_id: jid });
        startStream(jid);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid]);

  const cfgBody = () => {
    const body = {
      scope,
      algorithm,
      // model/api_id 来自 AgentModelPicker(extractor 偏好)解析的用户已配凭据;
      // 不再硬编码 deepseek 兜底(那会让没配 deepseek key 的用户提交到无凭据 provider)。
      model: (model || '').trim(),
      api_id: (apiId || '').trim(),
      target_arcs: Number(targetArcs) || 40,
      concurrency: Number(concurrency) || 15,
      author_era: (authorEra || '').trim(),
      max_book_usd: Number(maxUsd) || 10,
    };
    const cMin = Number(chapterMin);
    const cMax = Number(chapterMax);
    if (chapterMin && Number.isFinite(cMin)) body.chapter_min = cMin;
    if (chapterMax && Number.isFinite(cMax)) body.chapter_max = cMax;
    return body;
  };

  // 估算参数指纹 — 用来锁定"必须估算才能开始"
  const _paramsHash = () => JSON.stringify(cfgBody());

  const doEstimate = async () => {
    setEstimating(true); setEstimate(null); setErr('');
    try {
      const r = await window.api.scripts.llmExtractEstimate(sid, cfgBody());
      setEstimate(r);
      setEstimatedHash(_paramsHash());
    } catch (e) {
      setErr((e && (e.payload?.error || e.message)) || t('scripts.review.estimate_fail'));
      setEstimatedHash('');
    } finally { setEstimating(false); }
  };

  // 当前参数 vs 估算时参数:不一致(用户改了参数)= stale,需要重新估算
  const _estimateStale = !estimatedHash || estimatedHash !== _paramsHash();
  // 零 LLM 的 scope (worldbook_only / anchors_only / embed_only) 都 $0,不调 LLM,
  // **不需要强制估算** — 之前 _canStart 一律要求估算导致这 3 个 scope 永远点不动 "开始提取"。
  const _isZeroLlmScope = scope === 'worldbook_only' || scope === 'anchors_only' || scope === 'embed_only';
  // 开始按钮 gate:full scope 需要非过期估算;零 LLM scope 直接放行。
  const _canStart = _isZeroLlmScope || (!_estimateStale && estimate && estimate.ok !== false);

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

  return (
    <CSSpaceBetween size="l">
      <CSSpaceBetween direction="horizontal" size="xs">
        {/* 零 LLM scope 不需要估算按钮(估算返 $0 没意义) */}
        {phase === 'config' && !_isZeroLlmScope && (
          <CSButton onClick={doEstimate} loading={estimating} variant={_estimateStale ? 'primary' : 'normal'}>{t('scripts.review.estimate_cost')}</CSButton>
        )}
        {(phase === 'config' || phase === 'error') && (
          <CSButton variant={(_isZeroLlmScope || !_estimateStale) ? 'primary' : 'normal'} iconName="gen-ai"
            onClick={doStart} disabled={!_canStart}>
            {t('scripts.review.start_extract')}
          </CSButton>
        )}
        {phase === 'running' && <CSButton onClick={doCancel}>{t('scripts.review.cancel_job')}</CSButton>}
      </CSSpaceBetween>
      {/* 强制估算环节:full scope 才适用;零 LLM scope 直接跳过 */}
      {phase === 'config' && _estimateStale && !_isZeroLlmScope && (
        <CSAlert type="info">{t('scripts.review.must_estimate_first')}</CSAlert>
      )}
      {err && <CSAlert type="error">{err}</CSAlert>}

        {(phase === 'config' || phase === 'error') && (
          <CSSpaceBetween size="l">
            <CSBox color="text-body-secondary" fontSize="body-s">
              {t('scripts.review.desc')}
            </CSBox>
            {/* 提取范围 — 4 选 1。除"全量"外都零 LLM,从已 persist 的中间产物重建 */}
            <CSFormField label={t('scripts.review.scope')}
              description={t('scripts.review.scope_desc')}>
              <CSSegmentedControl selectedId={scope}
                options={[
                  { id: 'full',           text: t('scripts.review.scope_full') },
                  { id: 'worldbook_only', text: t('scripts.review.scope_worldbook_only') },
                  { id: 'anchors_only',   text: t('scripts.review.scope_anchors_only') },
                  { id: 'embed_only',     text: t('scripts.review.scope_embed_only') },
                ]}
                onChange={({ detail }) => setScope(detail.selectedId)} />
            </CSFormField>
            {scope === 'embed_only' && (
              <CSAlert type="info">{t('scripts.review.scope_embed_only_note')}</CSAlert>
            )}
            {scope === 'worldbook_only' && (
              <CSAlert type="info">{t('scripts.review.scope_worldbook_only_note')}</CSAlert>
            )}
            {scope === 'anchors_only' && (
              <CSAlert type="info">{t('scripts.review.scope_anchors_only_note')}</CSAlert>
            )}
            {scope === 'full' && (
            <CSFormField label={t('scripts.review.algorithm')}>
              <CSSegmentedControl selectedId={algorithm}
                options={[{ id: 'arc', text: t('scripts.review.algo_arc') }, { id: 'per_chapter', text: t('scripts.review.algo_per_chapter') }]}
                onChange={({ detail }) => setAlgorithm(detail.selectedId)} />
            </CSFormField>
            )}
            {scope === 'full' && (
              <CSColumnLayout columns={2}>
                <CSFormField label={t('scripts.review.chapter_min')}
                  description={t('scripts.review.chapter_range_desc')}>
                  <CSInput type="number" value={chapterMin}
                    placeholder={t('scripts.review.chapter_min_placeholder')}
                    onChange={({ detail }) => setChapterMin(detail.value)} />
                </CSFormField>
                <CSFormField label={t('scripts.review.chapter_max')}>
                  <CSInput type="number" value={chapterMax}
                    placeholder={t('scripts.review.chapter_max_placeholder')}
                    onChange={({ detail }) => setChapterMax(detail.value)} />
                </CSFormField>
              </CSColumnLayout>
            )}
            {scope === 'full' && (
              <CSSpaceBetween size="l">
                {/* Provider+Model:全站唯一实现 AgentModelPicker(extractor 偏好)。
                    它只列出用户已配凭据的 provider、给「未配 key」告警、解析后通过
                    onChange 回传 {api_id, model_real_name} 供 cfgBody() 拼请求体;
                    persistOnMount 把解析出的默认写回 extractor.* 偏好,与 L3047
                    导入侧「提取模型」完全同源、同持久化键。 */}
                <AgentModelPicker
                  prefPrefix="extractor"
                  preferProvider="deepseek"
                  defaultModel={null}
                  variant="bare"
                  persistOnMount
                  configHash="settings-models"
                  description={t('scripts.review.model_desc')}
                  onChange={(api_id, model_real_name) => { setApiId(api_id || ''); setModel(model_real_name || ''); }}
                />
                <CSColumnLayout columns={2}>
                  {algorithm === 'arc' && (
                    <CSFormField label={t('scripts.review.target_arcs')} description={t('scripts.review.target_arcs_desc')}><CSInput type="number" value={targetArcs} onChange={({ detail }) => setTargetArcs(detail.value)} /></CSFormField>
                  )}
                  <CSFormField label={t('scripts.review.concurrency')}><CSInput type="number" value={concurrency} onChange={({ detail }) => setConcurrency(detail.value)} /></CSFormField>
                  <CSFormField label={t('scripts.review.author_era')} description={t('scripts.review.author_era_desc')}><CSInput value={authorEra} onChange={({ detail }) => setAuthorEra(detail.value)} /></CSFormField>
                  <CSFormField label={t('scripts.review.max_usd')}><CSInput type="number" value={maxUsd} onChange={({ detail }) => setMaxUsd(detail.value)} /></CSFormField>
                </CSColumnLayout>
              </CSSpaceBetween>
            )}

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
