/* MobileMe 共用组件 —— 从 pages/MobileMe.jsx 拆出,逐字节不变(搬家时点)。
   语义统一 Batch 6b GUARD 的保护范围仅限 ConfirmSheet 一项:它是纯 inline-style 抽屉
   (滚入动画/圆角20px/scrim rgba(0.6)),与 mobile/Sheet.jsx 或桌面 .sheet 视觉/行为真实不同,
   防误合清单点名,绝不合并;详见 ConfirmSheet 上方 GUARD 注释。**本文件其余组件不在此保护范围
   内,可正常演进**——例如 Toggle 曾是第三份视觉分叉实现(44×26 内联样式),已于
   2026-07-17 统一到 mobile/Toggle.jsx(46×28 pl-toggle 权威版,见下方 re-export)。 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';
import { Icon } from '../icons.jsx';
export { Toggle } from '../Toggle.jsx';

/* ── 共用头部 ──────────────────────────────────────────────────── */
function PageHead({ title, sub, onBack, actions }) {
  const { t } = useTranslation();
  return (
    <div className="pl-head">
      {onBack && (
        <button className="pl-back" onClick={onBack} aria-label={t('mobile.me.back')}>
          <Icon name="chevron_left" size={20} />
        </button>
      )}
      <div className={'pl-head-title' + (onBack ? '' : ' center')}>
        <strong style={{ fontSize: 15 }}>{title}</strong>
        {sub && <span className="sub">{sub}</span>}
      </div>
      {actions && <div className="pl-head-actions">{actions}</div>}
    </div>
  );
}

/* ── SetRow 设置行 ─────────────────────────────────────────────── */
// 语义统一 #36(保留):此 SetRow 是「信息行」(label+desc+右侧 children)且纯内联样式
// (13px 上下内距 + danger 变体),与 mobile/Field.jsx 的竖排 Field / 开关 ToggleRow 结构都不同,
// 强并会改布局/语义,刻意保留本地实现。信息行 ≠ 开关行。
function SetRow({ label, desc, children, danger }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '13px 0', borderBottom: '1px solid var(--line-soft)' }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, fontWeight: 500, color: danger ? 'var(--danger)' : 'var(--text)', lineHeight: 1.4 }}>{label}</div>
        {desc && <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3, lineHeight: 1.55 }}>{desc}</div>}
      </div>
      <div style={{ flexShrink: 0 }}>{children}</div>
    </div>
  );
}

/* ── 底部操作按钮 ──────────────────────────────────────────────── */
function ActionBtn({ label, icon, onClick, danger, loading, style: s }) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        height: 36, padding: '0 14px', borderRadius: 10,
        fontSize: 13, fontWeight: 500,
        color: danger ? 'var(--danger)' : 'var(--text-quiet)',
        background: danger ? 'var(--danger-soft)' : 'var(--panel-2)',
        border: '1px solid ' + (danger ? 'rgba(200,103,93,0.3)' : 'var(--line-soft)'),
        opacity: loading ? 0.6 : 1, flexShrink: 0, ...s,
      }}
    >
      {icon && <Icon name={icon} size={14} />}
      {loading ? i18n.t('mobile.me.processing') : label}
    </button>
  );
}

/* ── 文本输入框 ────────────────────────────────────────────────── */
function Input({ label, hint, value, onChange, type = 'text', placeholder, multiline, rows = 3 }) {
  const inputStyle = {
    width: '100%', background: 'var(--panel)', border: '1px solid var(--line)',
    borderRadius: 10, color: 'var(--text)', fontSize: 16, padding: '10px 12px',
    outline: 'none', fontFamily: 'var(--font-sans)', lineHeight: 1.5,
    boxSizing: 'border-box',
  };
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 5, letterSpacing: '0.04em' }}>{label}</div>
      {multiline ? (
        <textarea
          value={value} onChange={e => onChange(e.target.value)}
          rows={rows} placeholder={placeholder}
          style={{ ...inputStyle, resize: 'vertical', minHeight: 80 }}
        />
      ) : (
        <input
          type={type} value={value} onChange={e => onChange(e.target.value)}
          placeholder={placeholder} style={inputStyle}
        />
      )}
      {hint && <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 4 }}>{hint}</div>}
    </div>
  );
}

/* ── Select ────────────────────────────────────────────────────── */
function Select({ label, value, onChange, options }) {
  return (
    <div style={{ marginBottom: 14 }}>
      {label && <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 5 }}>{label}</div>}
      <select
        value={value} onChange={e => onChange(e.target.value)}
        style={{
          width: '100%', background: 'var(--panel)', border: '1px solid var(--line)',
          borderRadius: 10, color: 'var(--text)', fontSize: 16, padding: '10px 12px',
          outline: 'none', fontFamily: 'var(--font-sans)',
        }}
      >
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

/* ── 底部动作 Sheet ───────────────────────────────────────────────
   语义统一 Batch 6b GUARD:本站不收口到 mobile/Sheet.jsx。本实现是纯 inline-style 抽屉,
   与 class-based .sheet 视觉/行为不同:scrim rgba(0.6)≠.sheet-scrim(0.5)、圆角 20px≠22px、
   无 .sheet-wrap.show 的从底滑入 transform 动画。强迁会改变视觉/行为 → 按铁律保留原样。 */
function ConfirmSheet({ open, title, body, confirmLabel, onClose, onConfirm, danger, loading }) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(10,9,8,0.6)', display: 'flex', alignItems: 'flex-end' }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%', background: 'var(--panel)', borderRadius: '20px 20px 0 0',
          padding: '20px 18px calc(var(--safe-bottom,20px) + 16px)',
          borderTop: '1px solid var(--line)',
        }}
      >
        <div style={{ width: 36, height: 4, borderRadius: 2, background: 'var(--line-strong)', margin: '0 auto 16px' }} />
        <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>{title}</div>
        {body && <div style={{ fontSize: 13, color: 'var(--text-quiet)', marginBottom: 18, lineHeight: 1.65 }}>{body}</div>}
        <div style={{ display: 'flex', gap: 10 }}>
          <button onClick={onClose} style={{ flex: 1, height: 46, borderRadius: 12, fontSize: 14, fontWeight: 500, background: 'var(--panel-2)', border: '1px solid var(--line)', color: 'var(--text-quiet)' }}>{t('common.cancel')}</button>
          <button
            onClick={onConfirm} disabled={loading}
            style={{ flex: 1, height: 46, borderRadius: 12, fontSize: 14, fontWeight: 600, background: danger ? 'var(--danger)' : 'var(--accent)', border: 'none', color: '#fff8f3', opacity: loading ? 0.7 : 1 }}
          >
            {loading ? t('mobile.me.processing') : (confirmLabel || t('common.confirm'))}
          </button>
        </div>
      </div>
    </div>
  );
}

export { PageHead, SetRow, ActionBtn, Input, Select, ConfirmSheet };
