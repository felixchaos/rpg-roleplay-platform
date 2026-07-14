// ProblemsPanel.jsx — 审稿问题面板(VSCode Problems 风)(机械搬出,逐字节不变)。
import React from 'react';
import { useTranslation } from 'react-i18next';
import { api } from './helpers.js';
const { useState, useEffect, useCallback } = React;

// 审稿问题面板(VSCode Problems 风):编辑器 agent 调 report_writing_issues 持久化的问题清单,
// 按严重度排序,可逐条「跳转章节」/「消除」,或一键清空。owner-scoped(后端 script_owned 校验)。
function ProblemsPanel({ scriptId, reloadKey, onJump, onClose, onCountChange }) {
  const { t } = useTranslation();
  const [issues, setIssues] = useState(null);
  const load = useCallback(async () => {
    try { const r = await api().scripts.issues(scriptId); const arr = (r && r.ok) ? (r.issues || []) : []; setIssues(arr); onCountChange && onCountChange(arr.length); }
    catch (_) { setIssues([]); }
  }, [scriptId]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setIssues(null); load(); }, [scriptId, reloadKey, load]);
  const dismiss = async (iid) => {
    try { await api().scripts.dismissIssue(scriptId, iid); } catch (_) {}
    setIssues((cur) => { const next = (cur || []).filter((x) => x.id !== iid); onCountChange && onCountChange(next.length); return next; });
  };
  const clearAll = async () => {
    try { await api().scripts.clearIssues(scriptId); } catch (_) {}
    setIssues([]); onCountChange && onCountChange(0);
  };
  const sevClass = (sev) => {
    const s = String(sev || '').toLowerCase();
    if (s === '高' || s === 'high') return 'high';
    if (s === '中' || s === 'medium') return 'mid';
    if (s === '低' || s === 'low') return 'low';
    return '';
  };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-problems" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mde-problems-head">
          <span className="mde-problems-title">{t('md_editor.problems.title', { defaultValue: '审稿问题' })}{Array.isArray(issues) ? ` · ${issues.length}` : ''}</span>
          {Array.isArray(issues) && issues.length > 0 && (
            <button type="button" className="mde-problems-clear" onClick={clearAll}>{t('md_editor.problems.clear', { defaultValue: '全部清空' })}</button>
          )}
          <button type="button" className="mde-problems-x" title={t('common.close')} onClick={onClose}>×</button>
        </div>
        {issues === null ? <div className="mde-qopen-empty">{t('common.loading')}</div>
          : issues.length === 0 ? (
            <div className="mde-problems-empty">{t('md_editor.problems.empty', { defaultValue: '暂无审稿问题。跑一次「复审本章」,矛盾与伏笔回收问题会列在这里。' })}</div>
          ) : (
            <ul className="mde-problems-list">
              {issues.map((it) => (
                <li key={it.id} className="mde-problem">
                  <div className="mde-problem-meta">
                    {it.severity && <span className={'mde-problem-sev ' + sevClass(it.severity)}>{String(it.severity)}</span>}
                    {it.type && <span className="mde-problem-type">{String(it.type)}</span>}
                    {it.chapter != null && (
                      <button type="button" className="mde-problem-jump" onClick={() => onJump(it.chapter)}>
                        {t('md_editor.problems.chapter', { n: it.chapter, defaultValue: '第{{n}}章 ↗' })}
                      </button>
                    )}
                    <span className="mde-problem-spacer" />
                    <button type="button" className="mde-problem-dismiss" title={t('md_editor.problems.dismiss', { defaultValue: '消除' })} onClick={() => dismiss(it.id)}>×</button>
                  </div>
                  <div className="mde-problem-detail">{String(it.detail || '')}</div>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  );
}

export { ProblemsPanel };
