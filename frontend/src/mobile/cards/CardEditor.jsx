/* MobileCards 卡片编辑器子视图 CardEditor —— 从 pages/MobileCards.jsx 拆出,逐字节不变。 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { SubHead, CardAv } from './shared.jsx';
import { CardEditForm } from './CardForm.jsx';
// 卡表单读/写 helper 与桌面端字段集逐字一致 → 复用单一规范实现,避免 shape 漂移。
import { cardFormInit, cardFormPayload } from '../../pages/cards.jsx';

/* cardFormInit / cardFormPayload 复用 pages/cards.jsx 的规范实现(见顶部 import)。 */

/* ═══════════════════════════════════════════════════════════════════
   卡片编辑器子视图
   ═══════════════════════════════════════════════════════════════════ */
function CardEditor({ card, isNew, kind, onBack, onSave, targetScripts = [], targetScriptId = '', onTargetScriptChange }) {
  const { t } = useTranslation();
  const [form, setForm] = useState(() => cardFormInit(card));
  const [saving, setSaving] = useState(false);
  const u = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const nameOk = !!form.name.trim();

  const doSave = async () => {
    if (!nameOk || saving) return;
    // 上一行已 `if (!nameOk || saving) return;`,这里恒不可达;原本还裸引用了未定义的 nav
    // (裸标识符即使配 ?. 也照抛 ReferenceError)。直接删掉死分支。
    setSaving(true);
    try {
      await onSave(cardFormPayload(form, card));
    } catch (_) {
      // 父级已 toast
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <SubHead
        title={isNew ? t('mobile.cards.editor.title_new') : t('mobile.cards.editor.title_edit', { name: card?.name || '' })}
        sub={kind === 'npc' ? 'NPC' : t('mobile.cards.editor.sub_user')}
        onBack={onBack}
        actions={
          <button className="pl-headbtn accent" onClick={doSave} disabled={!nameOk || saving} aria-label={t('common.save')}>
            {saving
              ? <span style={{ width: 17, height: 17, border: '2px solid var(--accent)', borderTopColor: 'transparent', borderRadius: 999, display: 'inline-block', animation: 'spin 0.7s linear infinite' }} />
              : <Icon name="check" size={19} />
            }
          </button>
        }
      />
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {/* 头像预览 */}
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 20 }}>
            <CardAv src={card?.avatar_path || card?.avatar_url} name={form.name} enabled={form.enabled} size={76} radius={22} />
          </div>

          {/* 新建 NPC 时选剧本 */}
          {isNew && kind === 'npc' && targetScripts.length > 0 && (
            <div className="pl-field" style={{ marginBottom: 20 }}>
              <label>{t('mobile.cards.editor.target_script_label')}</label>
              <div className="desc">{t('mobile.cards.editor.target_script_desc')}</div>
              <select className="pl-input" value={targetScriptId} onChange={(e) => onTargetScriptChange?.(e.target.value)}
                style={{ height: 46, paddingTop: 0, paddingBottom: 0 }}>
                {targetScripts.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
          )}

          <CardEditForm form={form} u={u} kind={kind} />

          <button className="pl-btn-primary" onClick={doSave} disabled={!nameOk || saving}
            style={{ opacity: nameOk && !saving ? 1 : 0.5 }}>
            {saving ? t('mobile.cards.editor.saving') : isNew ? t('mobile.cards.editor.btn_create') : t('mobile.cards.editor.btn_save')}
          </button>
        </div>
      </div>
    </>
  );
}

export { CardEditor };
