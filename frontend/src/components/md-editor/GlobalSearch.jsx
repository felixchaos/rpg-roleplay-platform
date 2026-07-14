// GlobalSearch.jsx — 全书检索(Mod+Shift+F)(机械搬出,逐字节不变)。
import React from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';
import { api, stripChapterPrefix } from './helpers.js';
const { useState, useEffect, useCallback, useRef } = React;

// 全书检索(Mod+Shift+F):调 owner-scoped /search 端点,结果点击跳到对应章节。
function GlobalSearch({ scriptId, openNode, onClose }) {
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const [regex, setRegex] = useState(false);
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef(null);
  useEffect(() => { setTimeout(() => inputRef.current && inputRef.current.focus(), 30); }, []);
  const run = useCallback(async () => {
    const query = q.trim();
    if (!query) { setRes(null); return; }
    setLoading(true);
    try {
      const r = await api().scripts.search(scriptId, query, { regex: regex ? 'true' : 'false', limit: 100 });
      setRes(r && r.ok ? r : { results: [], total: 0, error: (r && r.error) || '搜索失败' });
    } catch (e) { setRes({ results: [], total: 0, error: e?.message || '搜索失败' }); }
    finally { setLoading(false); }
  }, [q, regex, scriptId]);
  const onKey = (e) => { if (e.key === 'Enter') { e.preventDefault(); run(); } else if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
  const choose = (h) => { try { openNode({ kind: 'chapter', id: h.chapter_index, label: `${i18n.t('md_editor.chapter_prefix', { index: h.chapter_index })} ${stripChapterPrefix(h.title || '')}`.trim(), meta: {} }); } catch (_) {} onClose(); };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-search" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mde-search-bar">
          <input ref={inputRef} className="mde-search-input" value={q}
            placeholder={t('md_editor.search.placeholder', { defaultValue: '全书检索(回车搜)' })}
            onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} />
          <button type="button" className={'mde-search-rx' + (regex ? ' on' : '')} title={t('md_editor.search.regex', { defaultValue: '正则' })} onClick={() => setRegex((v) => !v)}>.*</button>
          <button type="button" className="mde-search-go" onClick={run} disabled={loading}>{loading ? '…' : t('md_editor.search.go', { defaultValue: '搜索' })}</button>
        </div>
        <div className="mde-search-list">
          {res === null ? <div className="mde-qopen-empty">{t('md_editor.search.hint', { defaultValue: '输入关键词,回车搜全书' })}</div>
            : res.error ? <div className="mde-qopen-empty">{res.error}</div>
              : (res.results || []).length === 0 ? <div className="mde-qopen-empty">{t('md_editor.search.none', { defaultValue: '0 命中' })}</div>
                : (
                  <>
                    <div className="mde-search-count">{t('md_editor.search.count', { total: res.total, n: res.chapters || 0, defaultValue: '{{total}} 处命中 · {{n}} 章' })}{res.capped ? ' +' : ''}</div>
                    {res.results.map((h, i) => (
                      <div key={i} className="mde-search-item" onMouseDown={() => choose(h)}>
                        <div className="mde-search-loc">{i18n.t('md_editor.chapter_prefix', { index: h.chapter_index })} {stripChapterPrefix(h.title || '')}</div>
                        <div className="mde-search-snip">{h.pre}{h.snippet}{h.suf}</div>
                      </div>
                    ))}
                  </>
                )}
        </div>
      </div>
    </div>
  );
}

export { GlobalSearch };
