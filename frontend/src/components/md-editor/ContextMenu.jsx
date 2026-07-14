// ContextMenu.jsx — 可复用右键菜单(从 pages/md-editor.jsx 机械搬出,逐字节不变)。
import React from 'react';
const { useState, useEffect, useRef } = React;

// ── 可复用右键菜单(VSCode 风:定位 / 视口夹取 / 点外关 / Esc / 分隔线 / 快捷键提示 / 禁用) ──
// 文件树、标签页、编辑器正文三处共用,保证交互一致。
// items: 数组,每项 { label, kbd?, danger?, disabled?, onClick } 或 { sep:true };falsy 项自动跳过。
function ContextMenu({ x, y, items, onClose }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ x, y });
  useEffect(() => {
    // 捕获阶段:点到菜单外(含在别处再次右键)即关。监听在打开后的下一帧才挂,
    // 不会被「打开本菜单的那次 mousedown」立刻关掉。
    const onDown = (e) => { if (!ref.current || !ref.current.contains(e.target)) onClose(); };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    window.addEventListener('mousedown', onDown, true);
    window.addEventListener('keydown', onKey, true);
    return () => { window.removeEventListener('mousedown', onDown, true); window.removeEventListener('keydown', onKey, true); };
  }, [onClose]);
  useEffect(() => {
    const el = ref.current; if (!el) return;
    const r = el.getBoundingClientRect();
    let nx = x, ny = y;
    if (x + r.width > window.innerWidth) nx = Math.max(4, window.innerWidth - r.width - 4);
    if (y + r.height > window.innerHeight) ny = Math.max(4, window.innerHeight - r.height - 4);
    setPos({ x: nx, y: ny });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [x, y]);
  const list = (items || []).filter(Boolean);
  return (
    <div className="mde-ctx" ref={ref} style={{ left: pos.x, top: pos.y }} onContextMenu={(e) => e.preventDefault()}>
      {list.map((it, i) => it.sep
        ? <div key={'s' + i} className="mde-ctx-sep" />
        : (
          <button key={i} className={'mde-ctx-item' + (it.danger ? ' danger' : '')} disabled={it.disabled}
            onClick={() => { onClose(); try { it.onClick && it.onClick(); } catch (_) {} }}>
            <span className="mde-ctx-label">{it.label}</span>
            {it.kbd ? <span className="mde-ctx-kbd">{it.kbd}</span> : null}
          </button>
        ))}
    </div>
  );
}

export { ContextMenu };
