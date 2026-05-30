/* Script Review (Phase E.1) — 提取规范层 KB 复核表 + god 编辑。
   自包含新文件,不改既有页面(零回归风险)。需浏览器 e2e 验证渲染/交互。
   后端已 live 验证:GET /api/scripts/{id}/graph · PATCH /api/scripts/{id}/canon */

import React from 'react';
import { useState, useEffect, useCallback } from 'react';

const API = () => (window.__API_BASE || '');

async function getGraph(scriptId) {
  const r = await fetch(`${API()}/api/scripts/${scriptId}/graph`, { credentials: 'include' });
  return r.json();
}
async function patchCanon(scriptId, body) {
  const r = await fetch(`${API()}/api/scripts/${scriptId}/canon`, {
    method: 'PATCH', credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  return r.json();
}

function ReviewFlags({ flags }) {
  if (!flags) return null;
  const f = flags;
  return (
    <div className="sr-flags" style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '8px 0' }}>
      <span className={f.needs_review ? 'sr-flag warn' : 'sr-flag ok'}>
        {f.needs_review ? '⚠ 需复核' : '✓ 摄入正常'}
      </span>
      <span className="sr-flag">作者非正文 {(f.author_notes || []).length}</span>
      <span className="sr-flag">怪标题 {(f.weird_titles || []).length}</span>
      <span className="sr-flag">编号缺口 {(f.gaps || []).length}</span>
      <span className="sr-flag">广告清洗 {((f.cleaning || {}).by_category || {}).ad || 0} 行</span>
    </div>
  );
}

export function ScriptReview({ scriptId }) {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState('');
  const [editing, setEditing] = useState(null); // logical_key being edited
  const [draft, setDraft] = useState('');

  const reload = useCallback(async () => {
    setBusy(true); setErr('');
    try {
      const d = await getGraph(scriptId);
      if (!d.ok) { setErr(d.error || '加载失败'); }
      else setData(d);
    } catch (e) { setErr(String(e)); }
    setBusy(false);
  }, [scriptId]);

  useEffect(() => { reload(); }, [reload]);

  const saveSummary = async (lk) => {
    const r = await patchCanon(scriptId, { op: 'update_entity', logical_key: lk, summary: draft });
    if (r.ok) { setEditing(null); reload(); } else { setErr(r.error || '保存失败'); }
  };
  const delEntity = async (lk) => {
    if (!(window.__confirm ? await window.__confirm({ title: '删除实体', message: `删除实体「${lk}」?`, danger: true, confirmText: '删除' }) : window.confirm(`删除实体「${lk}」?`))) return;
    const r = await patchCanon(scriptId, { op: 'delete_entity', logical_key: lk });
    if (r.ok) reload(); else setErr(r.error || '删除失败');
  };

  if (busy) return <div className="sr-loading">加载复核数据…</div>;
  if (err) return <div className="sr-error">错误:{err}</div>;
  if (!data) return null;

  const ents = data.entities || [];
  const wls = data.worldlines || [];
  return (
    <div className="script-review" style={{ padding: 16 }}>
      <h2>剧本复核 · {data.script?.title || scriptId}</h2>
      <ReviewFlags flags={data.review_flags} />

      <h3>规范实体({ents.length})</h3>
      <table className="sr-table" style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr><th>名称</th><th>类型</th><th>首现章</th><th>重要度</th><th>摘要</th><th></th></tr></thead>
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
                    <button onClick={() => saveSummary(e.logical_key)}>存</button>
                    <button onClick={() => setEditing(null)}>取消</button>
                  </>
                ) : (
                  <>
                    <button onClick={() => { setEditing(e.logical_key); setDraft(e.summary || ''); }}>改摘要</button>
                    <button onClick={() => delEntity(e.logical_key)}>删</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>规范世界线({wls.length})</h3>
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
