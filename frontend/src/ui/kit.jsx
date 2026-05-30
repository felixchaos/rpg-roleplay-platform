// === AWS-Console-style component kit ==========================================
// 暖色暗主题(基于 tokens.css)+ AWS 交互模式:列表+详情分栏 / 全页向导 /
// 就地分组表单 / 抽屉 / 顶部横幅。页面只「拼装」这些原语,不再手搓页面级 CSS。
// 类名前缀 aw- 以与历史 pl- 隔离。
import React, { useEffect, useRef, useState } from 'react';

// ── 零件 ──────────────────────────────────────────────────────────────────
export function Btn({ variant = 'default', size, icon, loading, disabled, onClick, children, title, type = 'button', full }) {
  const cls = `aw-btn aw-btn-${variant}${size ? ' aw-btn-' + size : ''}${full ? ' aw-btn-full' : ''}`;
  return (
    <button type={type} className={cls} disabled={disabled || loading} onClick={onClick} title={title}>
      {loading ? <span className="aw-spin" aria-hidden /> : icon ? <span className="aw-btn-ic">{icon}</span> : null}
      {children != null && <span>{children}</span>}
    </button>
  );
}

export function Badge({ tone = 'neutral', children }) {
  return <span className={`aw-badge aw-badge-${tone}`}>{children}</span>;
}

const _STATUS = {
  ok: '●', error: '✕', warn: '▲', info: 'ℹ', pending: '◌', loading: '◌',
};
export function StatusIndicator({ type = 'info', children }) {
  return (
    <span className={`aw-status aw-status-${type}`}>
      <span className={`aw-status-dot${type === 'loading' ? ' aw-spin' : ''}`}>{_STATUS[type] || '●'}</span>
      <span>{children}</span>
    </span>
  );
}

export function KeyValue({ items = [], cols = 2 }) {
  return (
    <div className="aw-kv" style={{ '--aw-kv-cols': cols }}>
      {items.map((it, i) => (
        <div className="aw-kv-cell" key={i}>
          <div className="aw-kv-label">{it.label}</div>
          <div className="aw-kv-value">{it.value ?? <span className="aw-muted">—</span>}</div>
        </div>
      ))}
    </div>
  );
}

// ── 表单零件 ────────────────────────────────────────────────────────────────
export function Field({ label, hint, error, children, htmlFor }) {
  return (
    <label className="aw-field" htmlFor={htmlFor}>
      {label && <span className="aw-field-label">{label}</span>}
      {children}
      {error ? <span className="aw-field-error">{error}</span> : hint ? <span className="aw-field-hint">{hint}</span> : null}
    </label>
  );
}

export function TextInput({ value, onChange, placeholder, id, type = 'text', multiline, rows = 4, disabled }) {
  if (multiline) {
    return (
      <textarea id={id} className="aw-input aw-textarea" rows={rows} value={value ?? ''} placeholder={placeholder}
        disabled={disabled} onChange={(e) => onChange?.(e.target.value)} />
    );
  }
  return (
    <input id={id} className="aw-input" type={type} value={value ?? ''} placeholder={placeholder}
      disabled={disabled} onChange={(e) => onChange?.(e.target.value)} />
  );
}

export function Select({ value, onChange, options = [], id, disabled }) {
  return (
    <div className="aw-select-wrap">
      <select id={id} className="aw-input aw-select" value={value ?? ''} disabled={disabled}
        onChange={(e) => onChange?.(e.target.value)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <span className="aw-select-chevron" aria-hidden>▾</span>
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }) {
  return (
    <button type="button" className={`aw-toggle${checked ? ' on' : ''}`} disabled={disabled}
      onClick={() => onChange?.(!checked)} role="switch" aria-checked={!!checked}>
      <span className="aw-toggle-track"><span className="aw-toggle-knob" /></span>
      {label && <span className="aw-toggle-label">{label}</span>}
    </button>
  );
}

// ── 页头 + 面包屑 ────────────────────────────────────────────────────────────
export function PageHeader({ title, description, breadcrumb, actions, counter }) {
  return (
    <header className="aw-pagehead">
      {breadcrumb && breadcrumb.length > 0 && (
        <nav className="aw-crumbs">
          {breadcrumb.map((b, i) => (
            <span key={i} className="aw-crumb">
              {b.onClick ? <button className="aw-crumb-link" onClick={b.onClick}>{b.label}</button> : <span>{b.label}</span>}
              {i < breadcrumb.length - 1 && <span className="aw-crumb-sep">/</span>}
            </span>
          ))}
        </nav>
      )}
      <div className="aw-pagehead-row">
        <div className="aw-pagehead-titles">
          <h1 className="aw-pagehead-title">
            {title}
            {counter != null && <span className="aw-pagehead-counter">{counter}</span>}
          </h1>
          {description && <p className="aw-pagehead-desc">{description}</p>}
        </div>
        {actions && <div className="aw-pagehead-actions">{actions}</div>}
      </div>
    </header>
  );
}

// ── 列表 + 详情分栏 ──────────────────────────────────────────────────────────
export function SplitLayout({ list, detail, detailOpen, onCloseDetail }) {
  return (
    <div className={`aw-split${detailOpen ? ' aw-split-open' : ''}`}>
      <div className="aw-split-list">{list}</div>
      {detailOpen && (
        <aside className="aw-split-detail">
          <button className="aw-split-close" onClick={onCloseDetail} title="收起详情" aria-label="收起详情">✕</button>
          {detail}
        </aside>
      )}
    </div>
  );
}

// 通用资源行列表(单选)
export function ResourceList({ items = [], selectedId, onSelect, getKey = (x) => x.id, renderItem, empty }) {
  if (!items.length) return <div className="aw-empty">{empty || '暂无数据'}</div>;
  return (
    <div className="aw-rlist" role="listbox">
      {items.map((it) => {
        const k = getKey(it);
        const sel = k === selectedId;
        return (
          <button key={k} className={`aw-rlist-item${sel ? ' sel' : ''}`} role="option" aria-selected={sel}
            onClick={() => onSelect?.(it)}>
            {renderItem(it, sel)}
          </button>
        );
      })}
    </div>
  );
}

// ── Tabs ────────────────────────────────────────────────────────────────────
export function Tabs({ tabs = [], active, onChange }) {
  return (
    <div className="aw-tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={active === t.id}
          className={`aw-tab${active === t.id ? ' active' : ''}`} onClick={() => onChange?.(t.id)}>
          {t.label}
          {t.badge != null && <span className="aw-tab-badge">{t.badge}</span>}
        </button>
      ))}
    </div>
  );
}

// ── 分组表单容器(就地编辑/保存) ─────────────────────────────────────────────
export function FormSection({ title, description, actions, footer, children, dense }) {
  return (
    <section className={`aw-section${dense ? ' dense' : ''}`}>
      {(title || actions) && (
        <div className="aw-section-head">
          <div>
            {title && <h3 className="aw-section-title">{title}</h3>}
            {description && <p className="aw-section-desc">{description}</p>}
          </div>
          {actions && <div className="aw-section-actions">{actions}</div>}
        </div>
      )}
      <div className="aw-section-body">{children}</div>
      {footer && <div className="aw-section-foot">{footer}</div>}
    </section>
  );
}

// ── 全页向导(左侧步骤条) ────────────────────────────────────────────────────
export function Wizard({ steps = [], active = 0, onNav, onCancel, onSubmit, submitLabel = '完成', submitting }) {
  const last = active >= steps.length - 1;
  return (
    <div className="aw-wizard">
      <ol className="aw-wizard-rail">
        {steps.map((s, i) => (
          <li key={i} className={`aw-wizard-step${i === active ? ' active' : ''}${i < active ? ' done' : ''}`}>
            <button className="aw-wizard-step-btn" onClick={() => i < active && onNav?.(i)} disabled={i > active}>
              <span className="aw-wizard-step-no">{i < active ? '✓' : i + 1}</span>
              <span className="aw-wizard-step-label">{s.title}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="aw-wizard-panel">
        <div className="aw-wizard-content">
          <h2 className="aw-wizard-title">{steps[active]?.title}</h2>
          {steps[active]?.content}
        </div>
        <div className="aw-wizard-foot">
          {onCancel && <Btn variant="link" onClick={onCancel}>取消</Btn>}
          <div className="aw-wizard-foot-right">
            {active > 0 && <Btn onClick={() => onNav?.(active - 1)}>上一步</Btn>}
            {last
              ? <Btn variant="primary" loading={submitting} onClick={onSubmit}>{submitLabel}</Btn>
              : <Btn variant="primary" onClick={() => onNav?.(active + 1)}>下一步</Btn>}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── 右侧抽屉 ──────────────────────────────────────────────────────────────────
export function Drawer({ open, onClose, title, children, footer, width = 480 }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === 'Escape' && onClose?.();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="aw-drawer-root">
      <div className="aw-drawer-scrim" onClick={onClose} />
      <aside className="aw-drawer" style={{ '--aw-drawer-w': width + 'px' }} role="dialog" aria-modal="true">
        <div className="aw-drawer-head">
          <h3 className="aw-drawer-title">{title}</h3>
          <button className="aw-drawer-close" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        <div className="aw-drawer-body">{children}</div>
        {footer && <div className="aw-drawer-foot">{footer}</div>}
      </aside>
    </div>
  );
}

// ── 确认弹窗(唯一保留的居中弹窗,仅破坏性操作) ────────────────────────────────
export function ConfirmDialog({ open, title, body, confirmLabel = '确认', cancelLabel = '取消', danger, loading, onConfirm, onCancel }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === 'Escape' && onCancel?.();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="aw-confirm-root" role="dialog" aria-modal="true">
      <div className="aw-confirm-scrim" onClick={onCancel} />
      <div className="aw-confirm">
        <h3 className="aw-confirm-title">{title}</h3>
        {body && <div className="aw-confirm-body">{body}</div>}
        <div className="aw-confirm-foot">
          <Btn variant="link" onClick={onCancel}>{cancelLabel}</Btn>
          <Btn variant={danger ? 'danger' : 'primary'} loading={loading} onClick={onConfirm}>{confirmLabel}</Btn>
        </div>
      </div>
    </div>
  );
}

// ── 顶部横幅(成功/错误/警告/信息),取代提示弹窗 ─────────────────────────────
export function Flashbar({ items = [] }) {
  if (!items.length) return null;
  return (
    <div className="aw-flashbar">
      {items.map((it, i) => (
        <div key={it.id ?? i} className={`aw-flash aw-flash-${it.type || 'info'}`}>
          <span className="aw-flash-ic" aria-hidden>{_STATUS[it.type === 'success' ? 'ok' : it.type === 'error' ? 'error' : it.type] || 'ℹ'}</span>
          <div className="aw-flash-content">{it.content}</div>
          {it.onDismiss && <button className="aw-flash-x" onClick={it.onDismiss} aria-label="关闭">✕</button>}
        </div>
      ))}
    </div>
  );
}

// 轻量 flash 状态 hook
export function useFlash() {
  const [items, setItems] = useState([]);
  const seq = useRef(1);
  const push = (type, content, ttl = 3000) => {
    const id = seq.current++;
    setItems((xs) => [...xs, { id, type, content, onDismiss: () => setItems((ys) => ys.filter((y) => y.id !== id)) }]);
    if (ttl) setTimeout(() => setItems((ys) => ys.filter((y) => y.id !== id)), ttl);
    return id;
  };
  return {
    items,
    ok: (c, ttl) => push('success', c, ttl),
    err: (c, ttl) => push('error', c, ttl ?? 6000),
    info: (c, ttl) => push('info', c, ttl),
    warn: (c, ttl) => push('warn', c, ttl),
    clear: () => setItems([]),
  };
}
