/* RebuildEstimateModal — 重做前的估算+确认弹窗.
   Editorial × 古籍数字化:保留 CSModal shell (portal/focus-trap),
   内容层全部替换为 editorial.module.css 风格.
*/

import React from 'react';
import { useTranslation } from 'react-i18next';
import CSModal from '@cloudscape-design/components/modal';
import CSButton from '@cloudscape-design/components/button';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import s from './editorial.module.css';

export function RebuildEstimateModal({ open, module, scriptId, estimate, loading, onClose, onConfirm }) {
  const { t } = useTranslation();
  if (!open) return null;

  const ok              = estimate && estimate.ok !== false;
  const tokens          = estimate?.tokens_est ?? estimate?.est_input_tokens;
  const cost            = estimate?.cost_est ?? estimate?.est_usd;
  const approx          = !!estimate?.approximate;  // LLM 路径=粗略估算,标「≈」避免看着像精确值
  const model           = estimate?.model;
  const affects         = Array.isArray(estimate?.affects) ? estimate.affects : [];
  const prereqs         = Array.isArray(estimate?.prereqs) ? estimate.prereqs : [];
  const hasBlockingPrereq = prereqs.some(p => p && p.ok === false);
  const isZeroLlm       = (tokens === 0 || tokens == null) && (cost === 0 || cost == null);

  const moduleName = t(`modules.${module}.title`, { defaultValue: module });

  return (
    <CSModal
      visible={open}
      onDismiss={onClose}
      header={
        <span style={{ fontFamily: 'var(--font-serif)', fontWeight: 600, letterSpacing: '0.03em' }}>
          {t('modules.estimate.title', { defaultValue: '重做估算' })}
          <span style={{ color: 'var(--accent)', marginLeft: 8 }}>· {moduleName}</span>
        </span>
      }
      footer={
        <CSSpaceBetween direction="horizontal" size="xs">
          <CSButton onClick={onClose} disabled={loading}>
            {t('common.cancel', { defaultValue: '取消' })}
          </CSButton>
          <CSButton
            variant="primary"
            disabled={loading || !ok || hasBlockingPrereq}
            onClick={() => onConfirm && onConfirm({ module, scriptId })}
          >
            {isZeroLlm
              ? t('modules.estimate.confirm_zero', { defaultValue: '确认重做（免费）' })
              : t('modules.estimate.confirm_llm',  { defaultValue: '确认重做（消耗 LLM）' })}
          </CSButton>
        </CSSpaceBetween>
      }
    >
      <div className={s.estimateBody}>
        {/* Loading state */}
        {loading && (
          <div className={s.estimateLoading}>
            {t('modules.estimate.loading', { defaultValue: '估算中…' })}
          </div>
        )}

        {/* Error state */}
        {!loading && estimate && estimate.ok === false && (
          <div className={s.estimateErrorBanner}>
            {estimate.error || estimate.note || t('modules.estimate.fail', { defaultValue: '无法估算' })}
          </div>
        )}

        {/* Loaded + ok */}
        {!loading && ok && (
          <>
            {/* KV grid: tokens / cost / model */}
            <div className={s.estimateKVRow}>
              <div className={s.estimateKVItem}>
                <span className={s.estimateKVLabel}>
                  {t('modules.estimate.tokens', { defaultValue: 'Tokens' })}
                </span>
                <span className={`${s.estimateKVValue} ${isZeroLlm ? s.estimateKVValueFree : ''}`}>
                  {tokens != null ? `${approx ? '≈ ' : ''}${Number(tokens).toLocaleString()}` : '0'}
                </span>
              </div>
              <div className={s.estimateKVItem}>
                <span className={s.estimateKVLabel}>
                  {t('modules.estimate.cost', { defaultValue: '预估成本' })}
                </span>
                <span className={`${s.estimateKVValue} ${isZeroLlm ? s.estimateKVValueFree : ''}`}>
                  {cost != null ? `${approx ? '≈ ' : ''}$${Number(cost).toFixed(3)}` : '$0.000'}
                </span>
              </div>
              <div className={s.estimateKVItem}>
                <span className={s.estimateKVLabel}>
                  {t('modules.estimate.model', { defaultValue: '模型' })}
                </span>
                <span className={`${s.estimateKVValue}`} style={{ fontSize: 13, color: 'var(--muted)' }}>
                  {model || (isZeroLlm ? '—' : '—')}
                </span>
              </div>
            </div>

            {/* Affects */}
            {affects.length > 0 && (
              <div>
                <div className={s.estimateSectionLabel}>
                  {t('modules.estimate.affects', { defaultValue: '影响的表' })}
                </div>
                <div className={s.estimateTagRow}>
                  {affects.map((a) => (
                    <span key={a} className={s.estimateTag}>{a}</span>
                  ))}
                </div>
              </div>
            )}

            {/* Prereqs */}
            {prereqs.length > 0 && (
              <div>
                <div className={s.estimateSectionLabel}>
                  {t('modules.estimate.prereqs', { defaultValue: '前置条件' })}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 4 }}>
                  {prereqs.map((p, i) => (
                    <div key={i} className={s.estimatePrereqRow}>
                      <span className={p.ok ? s.prereqOk : s.prereqWarn}>
                        {p.ok ? '✓' : '△'}
                      </span>
                      <span>
                        {p.label || p.key}
                        {p.total != null ? ` ${p.count || 0} / ${p.total}` : ''}
                      </span>
                      {p.hint && (
                        <span style={{ color: 'var(--muted-2)', fontSize: 11 }}>{p.hint}</span>
                      )}
                    </div>
                  ))}
                </div>
                {hasBlockingPrereq && (
                  <div className={s.estimateBlockAlert} style={{ marginTop: 8 }}>
                    △ {t('modules.estimate.prereq_block_header', { defaultValue: '前置条件未满足' })}
                    <div style={{ fontWeight: 400, marginTop: 2, fontSize: 11, color: 'var(--muted)' }}>
                      {t('modules.estimate.prereq_block_body', { defaultValue: '请先重做上面缺失的模块。' })}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Note */}
            {estimate.note && (
              <div className={s.estimateNote}>{estimate.note}</div>
            )}
          </>
        )}
      </div>
    </CSModal>
  );
}

export default RebuildEstimateModal;
