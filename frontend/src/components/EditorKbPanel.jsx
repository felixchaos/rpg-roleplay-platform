/* EditorKbPanel — 「知识库中心」接入剧本编辑器(/md-editor)的自包含右侧抽屉。
   自包含:不改 md-editor.jsx / md-editor.css,样式类名前缀 mde-kb-,内联跟随
   md-editor.css 的暗色审美变量(--panel/--line/--text/--muted/--accent/--bg-deep,
   在这些变量缺失时各自带回退值,不依赖 md-editor.css 已加载)。

   复用既有 phase_rebuild_panel 组件族(零改动其逻辑):
     - useScriptRebuild(scriptId)  来自 pages/script-modules-panel.jsx
     - RebuildJobBanner / ModuleMatrixOverview / RebuildEstimateModal
       来自 components/*.jsx(均已 export,未改动其源码)

   新增能力(既有面板没有的):
     ① 「AI 复核角色卡」按钮 —— POST /api/scripts/{id}/audit-cards(api-client: scripts.auditCards),
        异步任务(cards_audit),不在这里重复造轮子的进度条 —— 全局后台任务浮窗
        (project_task_floater)已经跟踪 import_jobs;这里只需 loading + toast 反馈「已派发」。
     ② 向量索引状态行 —— GET /api/scripts/{id}/embed/status(api-client 未封装,按任务要求直接 fetch),
        展示 chunks/cards/worldbook/canon 四路 done/total + 手动刷新。

   用法:
     <EditorKbPanel scriptId={scriptId} open={kbOpen} onClose={() => setKbOpen(false)} />

   另导出 useKbHealthBadge(scriptId):给顶栏徽标用,失败静默返回 null。
*/
import React from 'react';
import { useTranslation } from 'react-i18next';
import './EditorKbPanel.css';

import { useScriptRebuild } from '../pages/script-modules-panel.jsx';
import { RebuildJobBanner } from './RebuildJobBanner.jsx';
import { ModuleMatrixOverview } from './ModuleMatrixOverview.jsx';
import { RebuildEstimateModal } from './RebuildEstimateModal.jsx';

const api = () => (typeof window !== 'undefined' ? window.api : null);
const toast = (msg, opts) => { try { window.__apiToast?.(msg, opts); } catch (_) {} };

/* ── useKbHealthBadge — 轻量健康徽标 hook ──────────────────────────────
   拉一次 /modules-status,数出「非 ready」的模块个数(stale_count)供顶栏徽标。
   失败静默返回 null(不抛、不重试、不打扰主流程)。 */
export function useKbHealthBadge(scriptId) {
  const [badge, setBadge] = React.useState(null); // { stale_count, ready } | null

  const reload = React.useCallback(async () => {
    if (!scriptId) { setBadge(null); return; }
    try {
      const A = api();
      const r = await A?.scripts?.getModulesStatus?.(scriptId);
      if (!r || r.ok === false) { setBadge(null); return; }
      const arr = Array.isArray(r.modules) ? r.modules : Object.values(r.modules || {});
      let staleCount = 0;
      for (const m of arr) {
        if (!m || typeof m !== 'object') continue;
        const st = m.status || 'unknown';
        if (st !== 'ready') staleCount += 1;
      }
      setBadge({ stale_count: staleCount, ready: staleCount === 0 });
    } catch (_) {
      setBadge(null);
    }
  }, [scriptId]);

  React.useEffect(() => { reload(); }, [reload]);

  return badge;
}

/* ── 向量索引状态行 ── */
function EmbedStatusRow({ scriptId }) {
  const { t } = useTranslation();
  const [status, setStatus] = React.useState(null); // {running, chunks:{done,total}, cards:{...}, worldbook:{...}, canon:{...}}
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState(null);

  const load = React.useCallback(async () => {
    if (!scriptId) return;
    setLoading(true); setErr(null);
    try {
      const res = await fetch(`/api/scripts/${scriptId}/embed/status`, { credentials: 'include' });
      const j = await res.json().catch(() => null);
      if (!res.ok || !j || j.ok === false) throw new Error((j && j.error) || `HTTP ${res.status}`);
      setStatus(j.status || null);
    } catch (e) {
      setErr(e?.message || String(e));
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [scriptId]);

  React.useEffect(() => { load(); }, [load]);

  const rows = status
    ? [
        ['chunks', t('md_editor.kb.embed.row_chunks', { defaultValue: '文本分块' })],
        ['cards', t('md_editor.kb.embed.row_cards', { defaultValue: '角色卡' })],
        ['worldbook', t('md_editor.kb.embed.row_worldbook', { defaultValue: '世界书' })],
        ['canon', t('md_editor.kb.embed.row_canon', { defaultValue: '知识库人物' })],
      ]
    : [];

  return (
    <div className="mde-kb-embed">
      <div className="mde-kb-embed-head">
        <span className="mde-kb-embed-title">{t('md_editor.kb.embed.title', { defaultValue: '向量索引状态' })}</span>
        {status?.running && <span className="mde-kb-embed-running">{t('md_editor.kb.embed.running', { defaultValue: '正在嵌入…' })}</span>}
        <button type="button" className="mde-kb-embed-refresh" onClick={load} disabled={loading}>
          {loading ? t('common.loading', { defaultValue: '加载中…' }) : t('common.refresh', { defaultValue: '刷新' })}
        </button>
      </div>
      {err && <div className="mde-kb-embed-err">{err}</div>}
      {!err && rows.length > 0 && (
        <div className="mde-kb-embed-grid">
          {rows.map(([key, label]) => {
            const cell = status[key] || {};
            const done = cell.done ?? 0;
            const total = cell.total ?? 0;
            const complete = total > 0 && done >= total;
            return (
              <div key={key} className="mde-kb-embed-cell">
                <span className="mde-kb-embed-label">{label}</span>
                <span className={'mde-kb-embed-count' + (complete ? ' done' : '')}>{done}/{total}</span>
              </div>
            );
          })}
        </div>
      )}
      {!err && !loading && !status && (
        <div className="mde-kb-embed-empty">{t('md_editor.kb.embed.empty', { defaultValue: '暂无数据' })}</div>
      )}
    </div>
  );
}

/* ── AI 复核角色卡 按钮 ── */
function AuditCardsButton({ scriptId }) {
  const { t } = useTranslation();
  const [busy, setBusy] = React.useState(false);

  const run = React.useCallback(async () => {
    if (!scriptId || busy) return;
    setBusy(true);
    try {
      const A = api();
      let r;
      if (A?.scripts?.auditCards) {
        r = await A.scripts.auditCards(scriptId, '', '');
      } else {
        const res = await fetch(`/api/scripts/${scriptId}/audit-cards`, {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        r = await res.json().catch(() => null);
        if (!res.ok && (!r || r.ok === false)) throw new Error((r && r.error) || `HTTP ${res.status}`);
      }
      if (r && r.ok !== false) {
        toast(t('md_editor.kb.audit_cards.dispatched', { defaultValue: 'AI 复核已派发,进度见右下角任务浮窗' }), { kind: 'ok', duration: 2600 });
      } else if (r && r.code === 'credentials_required') {
        toast(t('md_editor.kb.audit_cards.needs_credentials', { defaultValue: '缺少模型凭据,请先在设置里配置' }), { kind: 'warning', duration: 3000 });
      } else {
        toast((r && r.error) || t('md_editor.kb.audit_cards.fail', { defaultValue: '派发失败' }), { kind: 'danger' });
      }
    } catch (e) {
      toast(t('md_editor.kb.audit_cards.fail', { defaultValue: '派发失败' }), { kind: 'danger', detail: e?.message });
    } finally {
      setBusy(false);
    }
  }, [scriptId, busy, t]);

  return (
    <button type="button" className="mde-kb-audit-btn" disabled={!scriptId || busy} onClick={run}>
      {busy
        ? t('md_editor.kb.audit_cards.busy', { defaultValue: '复核中…' })
        : t('md_editor.kb.audit_cards.btn', { defaultValue: 'AI 复核角色卡' })}
    </button>
  );
}

/* ── 主组件:右侧抽屉 ── */
export function EditorKbPanel({ scriptId, open, onClose }) {
  const { t } = useTranslation();
  const rb = useScriptRebuild(scriptId);

  // Esc 关闭(跟随 md-editor 其它浮层习惯:mde-qopen-scrim 系同款交互)。
  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose && onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="mde-kb-scrim" onMouseDown={onClose}>
      <div className="mde-kb-drawer" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mde-kb-head">
          <span className="mde-kb-title">{t('md_editor.kb.title', { defaultValue: '知识库中心' })}</span>
          <button type="button" className="mde-kb-x" title={t('common.close', { defaultValue: '关闭' })} onClick={onClose}>×</button>
        </div>
        <div className="mde-kb-body">
          <RebuildJobBanner {...rb.bannerProps} />
          <ModuleMatrixOverview {...rb.matrixProps} />

          <div className="mde-kb-section">
            <AuditCardsButton scriptId={scriptId} />
          </div>

          <div className="mde-kb-section">
            <EmbedStatusRow scriptId={scriptId} />
          </div>
        </div>
      </div>
      <RebuildEstimateModal {...rb.modalProps} />
    </div>
  );
}

export default EditorKbPanel;
