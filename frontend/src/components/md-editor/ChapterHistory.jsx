// ChapterHistory.jsx — 章节版本历史(checkpoints)(机械搬出,逐字节不变)。
import React from 'react';
import { useTranslation } from 'react-i18next';
import { api, toast } from './helpers.js';
const { useState, useEffect } = React;

// 章节版本历史(checkpoints):列 AI 改动 + 一键回滚到历史任意版本之前。
function ChapterHistory({ scriptId, chapterIndex, onClose, onRestored }) {
  const { t } = useTranslation();
  const [versions, setVersions] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try { const r = await api().scripts.chapterHistory(scriptId, chapterIndex); if (!cancelled) setVersions(r && r.ok ? (r.versions || []) : []); }
      catch (_) { if (!cancelled) setVersions([]); }
    })();
    return () => { cancelled = true; };
  }, [scriptId, chapterIndex]);
  const restore = async (cid) => {
    if (busy) return; setBusy(true);
    try {
      const r = await api().scripts.chapterRestore(scriptId, chapterIndex, cid);
      if (r && r.ok) { toast(t('md_editor.history.restored', { defaultValue: '已恢复到该版本之前' }), { kind: 'ok' }); onRestored && onRestored(); }
      else toast((r && r.error) || t('md_editor.history.restore_fail', { defaultValue: '恢复失败' }), { kind: 'error' });
    } catch (_) { toast(t('md_editor.history.restore_fail', { defaultValue: '恢复失败' }), { kind: 'error' }); }
    finally { setBusy(false); }
  };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-history" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mde-history-head">{t('md_editor.history.title', { n: chapterIndex, defaultValue: '第{{n}}章 · AI 改动历史' })}</div>
        <div className="mde-history-list">
          {versions === null ? <div className="mde-qopen-empty">{t('common.loading')}</div>
            : versions.length === 0 ? <div className="mde-qopen-empty">{t('md_editor.history.none', { defaultValue: '本章暂无 AI 改动历史' })}</div>
              : versions.map((v) => (
                <div key={v.id} className="mde-history-item">
                  <div className="mde-history-meta">
                    <span className="mde-history-msg">{v.message || v.kind}</span>
                    <span className="mde-history-time">{v.created_at ? new Date(v.created_at).toLocaleString() : ''}</span>
                  </div>
                  {v.kind === 'chapter_edit' && v.has_before && !v.undone
                    ? <button type="button" className="mde-history-restore" disabled={busy} onMouseDown={() => restore(v.id)}>{t('md_editor.history.restore', { defaultValue: '恢复到此前' })}</button>
                    : <span className="mde-history-tag">{v.kind === 'chapter_revert' ? t('md_editor.history.revert', { defaultValue: '回滚' }) : (v.undone ? t('md_editor.history.undone', { defaultValue: '已撤销' }) : '')}</span>}
                </div>
              ))}
        </div>
      </div>
    </div>
  );
}

export { ChapterHistory };
