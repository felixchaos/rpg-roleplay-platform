/* 剧本引用状态(剧本详情页,仅 owner 可见)。
 *
 * 以前这里是一个四选一的「共享模式」选择器(私有 / 公开 / 锁定快照 / 跟随最新),三个选项都不成立:
 *   · 「公开」不是 sharing_mode —— 发 mode='public' 给 /pin,后端必回 400。公开发布走剧本的
 *     「发布」操作(is_public),详情页右上角的操作菜单里已经有;
 *   · 两种引用模式把 target_script_id 填成剧本自己,保存成功但检索读的还是自己,等于没做;
 *     真要引用别的剧本得先有目标剧本选择器,这是新功能,不在这里假装有。
 * 现在只做一件真实的事:剧本确实引用了另一个剧本时(历史数据或其它入口设置的),把引用状态
 * 摆出来,并能「解除引用」恢复成读自己的设定。判定与文案口径见 lib/script-sharing.js。 */

import React from 'react';
import { useState as useStatePL } from 'react';
import { useTranslation } from 'react-i18next';
import CSAlert from '@cloudscape-design/components/alert';
import CSButton from '@cloudscape-design/components/button';
import { referenceInfo } from '../../lib/script-sharing.js';

function SharingModeSelector({ script, currentUserId, onChanged }) {
  const { t } = useTranslation();
  const [saving, setSaving] = useStatePL(false);

  const isOwner = script && currentUserId && script.owner_id === currentUserId;
  const ref = referenceInfo(script);
  if (!isOwner || !ref) return null;

  const onUnpin = async () => {
    setSaving(true);
    try {
      await window.api.scripts.unpin(script.id);
      window.__apiToast?.(t('scripts.share.unpin_ok'), { kind: 'ok', duration: 2000 });
      onChanged && onChanged();
    } catch (e) {
      window.__apiToast?.(t('scripts.share.unpin_fail'), { kind: 'danger', detail: e?.message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <CSAlert
      type="info"
      header={t('scripts.share.ref_title')}
      action={<CSButton loading={saving} disabled={saving} onClick={onUnpin}>{t('scripts.share.unpin_btn')}</CSButton>}
    >
      {t('scripts.share.ref_desc', { id: ref.targetId })}
      {' '}
      {ref.mode === 'pinned-snapshot'
        ? t('scripts.share.ref_mode_pinned', { commit: ref.commitId || '-' })
        : t('scripts.share.ref_mode_floating')}
    </CSAlert>
  );
}

export { SharingModeSelector };
