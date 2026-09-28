/* Script Review (Phase E.1) — 提取规范层 KB 复核表 + god 编辑。
   自包含新文件,不改既有页面(零回归风险)。需浏览器 e2e 验证渲染/交互。
   后端已 live 验证:GET /api/scripts/{id}/graph · PATCH /api/scripts/{id}/canon */

import React from 'react';
import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';

const API = () => (window.__API_BASE || '');

async function getGraph(scriptId) {
  const r = await fetch(`${API()}/api/scripts/${scriptId}/graph`, { credentials: 'include' });
  return r.json();
}
async function getReviewStatus(scriptId) {
  // 从剧本列表里拿当前 review_status(graph 接口不带,直接读 my scripts)
  const r = await fetch(`${API()}/api/scripts/my`, { credentials: 'include' });
  if (!r.ok) return null;
  const data = await r.json().catch(() => null);
  const arr = (data && (data.scripts || data.items || data)) || [];
  const s = arr.find((x) => String(x.id) === String(scriptId));
  return s ? { review_status: s.review_status, reviewed_at: s.reviewed_at } : null;
}
async function markReviewed(scriptId) {
  const r = await fetch(`${API()}/api/scripts/${scriptId}/mark-reviewed`, {
    method: 'POST', credentials: 'include',
  });
  return r.json().catch(() => ({ ok: r.ok }));
}
async function unmarkReviewed(scriptId) {
  const r = await fetch(`${API()}/api/scripts/${scriptId}/unmark-reviewed`, {
    method: 'POST', credentials: 'include',
  });
  return r.json().catch(() => ({ ok: r.ok }));
}
// TODO(剧本库收敛,处置⑧): 本轮只做提取 UI 五层重复收敛,不合并 canon 编辑端点(面铺太大)。
// 这里的行内"编辑摘要/删除"走 PATCH /canon;script-edit-canon.jsx 的 CanonEntityEditorView
// 走独立的 REST /canon-entities (PUT/POST/DELETE)。两套端点长期应统一到 /canon-entities REST,
// 但那是另一轮改造。
async function patchCanon(scriptId, body) {
  const r = await fetch(`${API()}/api/scripts/${scriptId}/canon`, {
    method: 'PATCH', credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  // 后端异常时可能回纯文本:别让 r.json() 抛成未处理的 rejection(按钮点了没反应),
  // 退成 {ok:false,error} 走页面已有的报错展示。
  return r.json().catch(() => ({ ok: false, error: `${r.status} ${r.statusText || ''}`.trim() }));
}

function ReviewFlags({ flags }) {
  const { t } = useTranslation();
  if (!flags) return null;
  const f = flags;
  return (
    <div className="sr-flags" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '8px 0' }}>
      <span className={f.needs_review ? 'sr-flag warn' : 'sr-flag ok'}>
        {f.needs_review ? t('scripts.review.flags.needs_review') : t('scripts.review.flags.extract_ok')}
      </span>
      <span className="sr-flag">{t('scripts.review.flags.author_notes', { count: (f.author_notes || []).length })}</span>
      <span className="sr-flag">{t('scripts.review.flags.weird_titles', { count: (f.weird_titles || []).length })}</span>
      <span className="sr-flag">{t('scripts.review.flags.gaps', { count: (f.gaps || []).length })}</span>
      <span className="sr-flag">{t('scripts.review.flags.ad_cleaned', { count: ((f.cleaning || {}).by_category || {}).ad || 0 })}</span>
    </div>
  );
}

function ReviewStatusBanner({ scriptId, status, busy, onChange }) {
  const { t } = useTranslation();
  const [acting, setActing] = useState(false);
  const isReviewed = status?.review_status === 'reviewed';
  const reviewedAt = status?.reviewed_at;
  const reviewedAtLabel = reviewedAt
    ? ((window.__fmt && window.__fmt.time)
        ? window.__fmt.time(reviewedAt)
        : new Date(reviewedAt).toLocaleString('zh-CN', { hour12: false }))
    : null;
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      gap: 16, padding: '12px 16px', margin: '8px 0 16px 0',
      borderRadius: 8,
      background: isReviewed ? 'rgba(80,160,90,0.10)' : 'rgba(201,100,66,0.10)',
      border: isReviewed ? '1px solid rgba(80,160,90,0.4)' : '1px solid rgba(201,100,66,0.4)',
    }}>
      <div style={{ display: 'grid', gap: 4 }}>
        <div style={{ fontWeight: 600, fontSize: 14 }}>
          {isReviewed
            ? t('scripts.review.banner.reviewed_title', { time: reviewedAtLabel ? ` · ${reviewedAtLabel}` : '' })
            : t('scripts.review.banner.unreviewed_title')}
        </div>
        <div style={{ fontSize: 12, opacity: 0.7 }}>
          {isReviewed
            ? t('scripts.review.banner.reviewed_hint')
            : t('scripts.review.banner.unreviewed_hint')}
        </div>
      </div>
      <button
        disabled={busy || acting}
        onClick={async () => {
          setActing(true);
          try {
            const r = isReviewed ? await unmarkReviewed(scriptId) : await markReviewed(scriptId);
            onChange?.(r);
          } finally { setActing(false); }
        }}
        style={{
          flexShrink: 0,
          padding: '8px 16px',
          fontSize: 13, fontWeight: 600,
          border: 'none', borderRadius: 6, cursor: (busy || acting) ? 'wait' : 'pointer',
          background: isReviewed ? 'rgba(150,143,133,0.25)' : 'var(--accent, #c96442)',
          color: isReviewed ? 'var(--text, #ebe7df)' : '#fff',
        }}
      >
        {isReviewed ? t('scripts.review.banner.btn_unmark') : t('scripts.review.banner.btn_mark')}
      </button>
    </div>
  );
}

export function ScriptReview({ scriptId, initialStatus, onReviewedChange }) {
  const { t } = useTranslation();
  const [data, setData] = useState(null);
  // 用父级已知的 review_status 初始化,banner 立刻显示正确态(不必等 /my 回来)。
  const [status, setStatus] = useState(initialStatus ? { review_status: initialStatus } : null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState('');
  const [editing, setEditing] = useState(null); // logical_key being edited
  const [draft, setDraft] = useState('');

  const reload = useCallback(async () => {
    setBusy(true); setErr('');
    try {
      const [d, st] = await Promise.all([getGraph(scriptId), getReviewStatus(scriptId)]);
      if (!d.ok) { setErr(d.error || t('scripts.review.err.load_failed')); }
      else setData(d);
      if (st) setStatus(st);
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  }, [scriptId]);

  useEffect(() => { reload(); }, [reload]);

  const saveSummary = async (lk) => {
    const r = await patchCanon(scriptId, { op: 'update_entity', logical_key: lk, summary: draft });
    if (r.ok) { setEditing(null); reload(); } else { setErr(r.error || t('scripts.review.err.save_failed')); }
  };
  const delEntity = async (lk) => {
    if (!(window.__confirm ? await window.__confirm({ title: t('scripts.review.entity.delete_title'), message: t('scripts.review.entity.delete_message', { key: lk }), danger: true, confirmText: t('common.delete') }) : window.confirm(t('scripts.review.entity.delete_message', { key: lk })))) return;
    const r = await patchCanon(scriptId, { op: 'delete_entity', logical_key: lk });
    if (r.ok) reload(); else setErr(r.error || t('scripts.review.err.delete_failed'));
  };

  if (busy) return <div className="sr-loading">{t('scripts.review.loading')}</div>;
  if (err) return <div className="sr-error">{t('scripts.review.err.prefix')}{err}</div>;
  if (!data) return null;

  const ents = data.entities || [];
  const wls = data.worldlines || [];
  return (
    <div className="script-review" style={{ padding: 16 }}>
      <h2>{t('scripts.review.page_title', { title: data.script?.title || scriptId })}</h2>
      <ReviewStatusBanner
        scriptId={scriptId}
        status={status}
        busy={busy}
        onChange={(r) => {
          // 用 mark/unmark 的权威 POST 响应更新 banner(不再依赖 /scripts/my 的 find,
          // 那是 UI 卡在「需复核」的根因)+ 回调父列表同步 review_status。
          if (r && r.review_status) {
            setStatus({ review_status: r.review_status, reviewed_at: r.review_status === 'reviewed' ? new Date().toISOString() : null });
            onReviewedChange?.(scriptId, r.review_status);
          } else { reload(); }
        }}
      />
      <ReviewFlags flags={data.review_flags} />

      <h3>{t('scripts.review.entities_heading', { count: ents.length })}</h3>
      <table className="sr-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr><th>{t('scripts.review.col.name')}</th><th>{t('scripts.review.col.type')}</th><th>{t('scripts.review.col.first_chapter')}</th><th>{t('scripts.review.col.importance')}</th><th>{t('scripts.review.col.summary')}</th><th></th></tr></thead>
        <tbody>
          {ents.map((e) => (
            <tr key={e.logical_key}>
              <td>{e.name}</td>
              <td>{e.type}</td>
              <td>{e.first_revealed_chapter}</td>
              <td>{e.importance}</td>
              <td>
                {editing === e.logical_key ? (
                  <input value={draft} onChange={(ev) => setDraft(ev.target.value)} style={{ width: '90%' }} />
                ) : (e.summary || <span style={{ opacity: 0.4 }}>—</span>)}
              </td>
              <td>
                {editing === e.logical_key ? (
                  <>
                    <button onClick={() => saveSummary(e.logical_key)}>{t('common.save')}</button>
                    <button onClick={() => setEditing(null)}>{t('common.cancel')}</button>
                  </>
                ) : (
                  <>
                    <button onClick={() => { setEditing(e.logical_key); setDraft(e.summary || ''); }}>{t('scripts.review.entity.edit_summary')}</button>
                    <button onClick={() => delEntity(e.logical_key)}>{t('common.delete')}</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>{t('scripts.review.worldlines_heading', { count: wls.length })}</h3>
      <ul>
        {wls.map((w) => (
          <li key={w.wl_key}>
            {w.is_primary ? '★ ' : ''}{w.label} ({w.wl_key})
            {(data.nodes || []).filter((n) => n.wl_key === w.wl_key).map((n) => (
              <span key={n.node_key} className="sr-node"> · {n.seq}.{n.label}</span>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default ScriptReview;
