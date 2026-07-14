// EditorPane.jsx — 中栏标签编辑器外壳(机械搬出,逐字节不变)。
import React from 'react';
import { useTranslation } from 'react-i18next';
import CodeMirrorEditor from '../CodeMirrorEditor.jsx';

// ── 标签编辑器(P0:textarea;P3 替换为 CodeMirror)──────────────────────
function EditorPane({ tab, onChange, scriptId, onViewReady, onContinueAccept, chapterIndex, onSelectionChange, ghostEnabled, ghostFetch }) {
  const { t } = useTranslation();
  if (!tab) {
    return <div className="mde-empty">{t('md_editor.editor.empty_hint')}<br /><span className="muted">{t('md_editor.editor.empty_kinds')}</span></div>;
  }
  if (tab.loading) return <div className="mde-empty">{t('common.loading')}</div>;
  if (tab.error) return <div className="mde-empty err">{t('md_editor.editor.load_failed', { error: tab.error })}</div>;
  return (
    <CodeMirrorEditor
      value={tab.content}
      docKey={tab.key}
      onChange={(v) => onChange(tab.key, v)}
      scriptId={scriptId}
      onViewReady={onViewReady}
      onContinueAccept={onContinueAccept}
      chapterIndex={chapterIndex}
      onSelectionChange={onSelectionChange}
      ghostEnabled={ghostEnabled && tab.kind === 'chapter'}
      ghostFetch={ghostFetch}
    />
  );
}

export { EditorPane };
