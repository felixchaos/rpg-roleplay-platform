// 全局 Promise 化 confirm/prompt 宿主。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSFormField from '@cloudscape-design/components/form-field';
import CSInput from '@cloudscape-design/components/input';
import CSModal from '@cloudscape-design/components/modal';
import CSSpaceBetween from '@cloudscape-design/components/space-between';

/* DialogHost — 全局 Promise 化的 Cloudscape 弹窗,接管浏览器原生 confirm/prompt。
   用法: await window.__confirm({title, message, danger, confirmText})  → bool
         await window.__prompt({title, label, default, confirmText})    → string|null */
function DialogHost() {
  const { t } = useTranslation();
  const [dlg, setDlg] = useStatePL(null);
  useEffectPL(() => {
    window.__confirm = (o = {}) => new Promise((resolve) => setDlg({
      type: 'confirm', resolve,
      title: o.title || t('common.confirm'), message: o.message || '',
      danger: !!o.danger, confirmText: o.confirmText || t('common.confirm'),
    }));
    window.__prompt = (o = {}) => new Promise((resolve) => setDlg({
      type: 'prompt', resolve,
      title: o.title || t('platform.shell.prompt_title'), label: o.label || '', value: o.default || '',
      confirmText: o.confirmText || t('common.confirm'),
    }));
    return () => { delete window.__confirm; delete window.__prompt; };
  }, [t]);
  if (!dlg) return null;
  const close = (val) => { try { dlg.resolve(val); } catch (_) {} setDlg(null); };
  const cancelVal = dlg.type === 'prompt' ? null : false;
  const okVal = dlg.type === 'prompt' ? (dlg.value || '') : true;
  return (
    <CSModal
      visible
      onDismiss={() => close(cancelVal)}
      header={dlg.title}
      footer={
        <CSBox float="right">
          <CSSpaceBetween direction="horizontal" size="xs">
            <CSButton variant="link" onClick={() => close(cancelVal)}>{t('common.cancel')}</CSButton>
            <CSButton variant="primary" onClick={() => close(okVal)}>{dlg.confirmText}</CSButton>
          </CSSpaceBetween>
        </CSBox>
      }
    >
      {dlg.type === 'confirm'
        ? <CSBox>{dlg.message}</CSBox>
        : <CSFormField label={dlg.label}>
            <CSInput value={dlg.value} autoFocus
              onChange={({ detail }) => setDlg((d) => ({ ...d, value: detail.value }))}
              onKeyDown={({ detail }) => { if (detail.key === 'Enter') close(dlg.value || ''); }} />
          </CSFormField>}
    </CSModal>
  );
}

export { DialogHost };
