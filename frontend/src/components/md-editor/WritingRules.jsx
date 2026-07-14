// WritingRules.jsx — 写作规范(.cursorrules 风)(机械搬出,逐字节不变)。
import React from 'react';
import { useTranslation } from 'react-i18next';
import { api, toast } from './helpers.js';
const { useState, useEffect } = React;

// 写作规范(.cursorrules 风):per-script 风格/连贯/禁忌规则,保存后注入编辑器 agent 最高优先上下文。
function WritingRules({ scriptId, onClose }) {
  const { t } = useTranslation();
  const [text, setText] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try { const r = await api().scripts.writingRules(scriptId); if (!cancelled) setText(r && r.ok ? (r.rules || '') : ''); }
      catch (_) { if (!cancelled) setText(''); }
    })();
    return () => { cancelled = true; };
  }, [scriptId]);
  const save = async () => {
    if (busy) return; setBusy(true);
    try {
      const r = await api().scripts.saveWritingRules(scriptId, text || '');
      if (r && r.ok) { toast(t('md_editor.rules.saved', { defaultValue: '写作规范已保存,下次对话起生效' }), { kind: 'ok' }); onClose(); }
      else toast((r && r.error) || t('md_editor.rules.fail', { defaultValue: '保存失败' }), { kind: 'error' });
    } catch (_) { toast(t('md_editor.rules.fail', { defaultValue: '保存失败' }), { kind: 'error' }); }
    finally { setBusy(false); }
  };
  return (
    <div className="mde-qopen-scrim" onMouseDown={onClose}>
      <div className="mde-rules" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mde-rules-head">{t('md_editor.rules.title', { defaultValue: '写作规范(注入 AI 上下文,务必遵守)' })}</div>
        <div className="mde-rules-desc">{t('md_editor.rules.desc', { defaultValue: '这部剧本的写作规矩:风格/人称/视角、连贯禁忌、命名约定…续写与改写时最高优先遵守。' })}</div>
        {text === null ? <div className="mde-qopen-empty">{t('common.loading')}</div>
          : <textarea className="mde-rules-ta" value={text} onChange={(e) => setText(e.target.value)}
              placeholder={t('md_editor.rules.placeholder', { defaultValue: '例:全程第三人称限制视角(只跟主角)；禁止上帝视角剧透；地名统一用「云墟城」不用「云墟」；台词不带现代网络词。' })} />}
        <div className="mde-rules-btns">
          <button type="button" className="mde-rules-save" disabled={busy || text === null} onClick={save}>{t('common.save', { defaultValue: '保存' })}</button>
          <button type="button" className="mde-rules-cancel" onClick={onClose}>{t('common.cancel')}</button>
        </div>
      </div>
    </div>
  );
}

export { WritingRules };
