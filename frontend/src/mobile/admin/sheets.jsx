/* MobileAdmin 底部操作 sheet(ConfirmSheet/InputSheet)。纯机械从 pages/MobileAdmin.jsx 拆出,逐字节等价。
   【移动端铁律·防误合】ConfirmSheet 的 .sheet-sub 文案样式 + busy 守护(处理中禁止 scrim 点关)+ sheet-actions
   marginTop 与桌面/统一版有真实视觉·行为差,绝不与桌面 Sheet 合并、绝不收口到 mobile/Sheet.jsx——
   原样保留(细节见下方 ConfirmSheet 上的 GUARD 注释)。 */
import React from 'react';
import { useTranslation } from 'react-i18next';

/* 底部操作确认 sheet
   语义统一 Batch 6b GUARD:本站不收口到 mobile/Sheet.jsx。差异点(迁则改视觉/行为):
   ① body 走 .sheet-sub(11.5px/lh1.5)而非统一版的 .confirm-note(12px/lh1.65)
   ② scrim 点关由 busy 守护(处理中禁止关闭)③ sheet-actions marginTop:14(统一版 8)
   ④ 与本文件 InputSheet 共用 position:fixed 内联模式 + busy/onCancel 契约(非 open/loading)。
   1:1 复刻不了 → 按铁律保留原样。 */
function ConfirmSheet({ title, body, confirmLabel, danger = false, busy, onConfirm, onCancel }) {
  const { t } = useTranslation();
  const label = confirmLabel ?? t('common.confirm');
  return (
    <div className="sheet-wrap show" style={{ position: 'fixed', inset: 0, zIndex: 60, pointerEvents: 'auto' }}>
      <div className="sheet-scrim" onClick={!busy ? onCancel : undefined} />
      <div className="sheet show" style={{ position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 61 }}>
        <div className="sheet-grip" />
        <div className="sheet-title">{title}</div>
        {body && <div className="sheet-sub">{body}</div>}
        <div className="sheet-actions" style={{ marginTop: 14 }}>
          <button className="sheet-btn" onClick={onCancel} disabled={busy}>{t('common.cancel')}</button>
          <button className={`sheet-btn ${danger ? 'danger' : 'primary'}`} onClick={onConfirm} disabled={busy}>
            {busy ? t('mobile.admin.processing') : label}
          </button>
        </div>
      </div>
    </div>
  );
}

/* 输入 sheet */
function InputSheet({ title, fields, busy, onConfirm, onCancel }) {
  const { t } = useTranslation();
  const [vals, setVals] = React.useState(() => {
    const v = {};
    fields.forEach((f) => { v[f.key] = f.default || ''; });
    return v;
  });
  return (
    <div className="sheet-wrap show" style={{ position: 'fixed', inset: 0, zIndex: 60, pointerEvents: 'auto' }}>
      <div className="sheet-scrim" onClick={!busy ? onCancel : undefined} />
      <div className="sheet show" style={{ position: 'fixed', left: 0, right: 0, bottom: 0, zIndex: 61 }}>
        <div className="sheet-grip" />
        <div className="sheet-title">{title}</div>
        <div style={{ display: 'grid', gap: 12, margin: '12px 0' }}>
          {fields.map((f) => (
            <div key={f.key}>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4 }}>{f.label}</div>
              {Array.isArray(f.options) ? (
                // 枚举字段用下拉:值必须是后端认的那几个,不能让人手打(打错 / 打中文后端恒 400)。
                <select
                  value={vals[f.key]}
                  onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))}
                  style={{ width: '100%', background: 'var(--panel-2)', border: '1px solid var(--line)', borderRadius: 10, padding: '8px 10px', color: 'var(--text)', fontSize: 14, fontFamily: 'inherit', boxSizing: 'border-box' }}
                >
                  <option value="">{f.placeholder || ''}</option>
                  {f.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              ) : f.multiline ? (
                <textarea
                  value={vals[f.key]}
                  onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))}
                  placeholder={f.placeholder || ''}
                  rows={3}
                  style={{ width: '100%', background: 'var(--panel-2)', border: '1px solid var(--line)', borderRadius: 10, padding: '8px 10px', color: 'var(--text)', fontSize: 14, fontFamily: 'inherit', resize: 'vertical', boxSizing: 'border-box' }}
                />
              ) : (
                <input
                  type={f.type || 'text'}
                  value={vals[f.key]}
                  onChange={(e) => setVals((v) => ({ ...v, [f.key]: e.target.value }))}
                  placeholder={f.placeholder || ''}
                  style={{ width: '100%', background: 'var(--panel-2)', border: '1px solid var(--line)', borderRadius: 10, padding: '8px 10px', color: 'var(--text)', fontSize: 14, fontFamily: 'inherit', boxSizing: 'border-box' }}
                />
              )}
            </div>
          ))}
        </div>
        <div className="sheet-actions">
          <button className="sheet-btn" onClick={onCancel} disabled={busy}>{t('common.cancel')}</button>
          <button className="sheet-btn primary" onClick={() => onConfirm(vals)} disabled={busy}>{busy ? t('mobile.admin.processing') : t('common.confirm')}</button>
        </div>
      </div>
    </div>
  );
}

export { ConfirmSheet, InputSheet };
