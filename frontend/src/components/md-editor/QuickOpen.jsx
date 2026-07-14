// QuickOpen.jsx — 快速打开(Mod+P)(机械搬出,逐字节不变)。
import React from 'react';
import { useTranslation } from 'react-i18next';
import { NODE_GROUPS, kindLabelZh } from './helpers.js';
import { fetchGroupList } from './node-crud.js';
const { useState, useEffect, useRef } = React;

// 快速打开(Mod+P,VSCode 风):跨所有实体类型模糊过滤 + 键盘选择 + 回车打开。
function QuickOpen({ scriptId, openNode, onClose }) {
  const { t } = useTranslation();
  const [q, setQ] = useState('');
  const [items, setItems] = useState(null);
  const [sel, setSel] = useState(0);
  const inputRef = useRef(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const lists = await Promise.all(NODE_GROUPS.map((g) =>
          fetchGroupList(g.kind, scriptId).then((arr) => arr.map((it) => ({ kind: g.kind, id: it.id, label: it.label, icon: g.icon }))).catch(() => [])));
        if (!cancelled) setItems(lists.flat());
      } catch (_) { if (!cancelled) setItems([]); }
    })();
    setTimeout(() => inputRef.current && inputRef.current.focus(), 30);
    return () => { cancelled = true; };
  }, [scriptId]);
  const filtered = (items || []).filter((it) => !q || (it.label || '').toLowerCase().includes(q.toLowerCase())).slice(0, 60);
  const choose = (it) => { if (!it) return; try { openNode({ kind: it.kind, id: it.id, label: it.label, meta: {} }); } catch (_) {} onClose(); };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); onClose(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, filtered.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(filtered[sel]); }
  };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-qopen" onMouseDown={(e) => e.stopPropagation()}>
        <input ref={inputRef} className="mde-qopen-input" value={q}
          placeholder={t('md_editor.quickopen.placeholder', { defaultValue: '快速打开:章节 / 角色卡 / 世界书 / 时间线 / 设定…' })}
          onChange={(e) => { setQ(e.target.value); setSel(0); }} onKeyDown={onKey} />
        <div className="mde-qopen-list">
          {items === null ? <div className="mde-qopen-empty">{t('common.loading')}</div>
            : filtered.length === 0 ? <div className="mde-qopen-empty">{t('md_editor.quickopen.none', { defaultValue: '无匹配' })}</div>
              : filtered.map((it, i) => (
                <div key={it.kind + ':' + it.id} className={'mde-qopen-item' + (i === sel ? ' sel' : '')}
                  onMouseEnter={() => setSel(i)} onMouseDown={() => choose(it)}>
                  <span className="mde-qopen-icon">{it.icon || '·'}</span>
                  <span className="mde-qopen-label">{it.label}</span>
                  <span className="mde-qopen-kind">{kindLabelZh(it.kind)}</span>
                </div>
              ))}
        </div>
      </div>
    </div>
  );
}

export { QuickOpen };
