// CodeMirrorEditor.jsx — CodeMirror 6 的极简 React 包装(markdown 模式 + 暖色主题)。
// 只在 MD 编辑器页用,经 md-editor 页 chunk 懒加载(CM6 ~2-3MB 不进主 bundle)。
// 受控约定:docKey 变(切标签)→ 整篇替换为 value;同一 docKey 内用户输入由 CM 内部管理、
// 经 onChange 上抛,绝不把 value 回灌(否则光标乱跳)。
import React from 'react';
import { EditorView, keymap, lineNumbers, highlightActiveLine, drawSelection, highlightActiveLineGutter } from '@codemirror/view';
import { EditorState } from '@codemirror/state';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { markdown } from '@codemirror/lang-markdown';
import { syntaxHighlighting, defaultHighlightStyle, bracketMatching, indentOnInput } from '@codemirror/language';
import { searchKeymap, highlightSelectionMatches } from '@codemirror/search';

const { useRef, useEffect } = React;

// 暖色主题:映射 tokens.css 的 CSS 变量,融入平台主题。CM6 theme 接受 var() 字符串。
const warmTheme = EditorView.theme({
  '&': { color: 'var(--text, #ebe7df)', backgroundColor: 'var(--bg, #1a1817)', height: '100%', fontSize: '13.5px' },
  '.cm-content': { fontFamily: 'var(--font-mono, monospace)', padding: '12px 0', caretColor: 'var(--accent, #b5654a)', lineHeight: '1.7' },
  '.cm-scroller': { fontFamily: 'var(--font-mono, monospace)', overflow: 'auto' },
  '.cm-gutters': { backgroundColor: 'var(--bg-deep, #131211)', color: 'var(--muted-2, #6b655e)', border: 'none' },
  '.cm-activeLineGutter': { backgroundColor: 'var(--panel-2, #282623)' },
  '.cm-activeLine': { backgroundColor: 'rgba(255,255,255,0.025)' },
  '.cm-cursor': { borderLeftColor: 'var(--accent, #b5654a)' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': { backgroundColor: 'var(--info-soft, rgba(122,166,194,.22))' },
  '.cm-selectionMatch': { backgroundColor: 'rgba(181,101,74,0.18)' },
  '.cm-searchMatch': { backgroundColor: 'rgba(181,101,74,0.30)', outline: '1px solid var(--accent,#b5654a)' },
  '.cm-matchingBracket': { backgroundColor: 'rgba(122,166,194,.18)', outline: 'none' },
}, { dark: true });

function baseExtensions(onChange, readOnly) {
  return [
    lineNumbers(),
    highlightActiveLineGutter(),
    highlightActiveLine(),
    drawSelection(),
    history(),
    indentOnInput(),
    bracketMatching(),
    highlightSelectionMatches(),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    markdown(),
    EditorView.lineWrapping,
    keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
    warmTheme,
    EditorState.readOnly.of(!!readOnly),
    EditorView.editable.of(!readOnly),
    EditorView.updateListener.of((u) => { if (u.docChanged) onChange?.(u.state.doc.toString()); }),
  ];
}

export default function CodeMirrorEditor({ value, docKey, onChange, readOnly = false }) {
  const hostRef = useRef(null);
  const viewRef = useRef(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const lastKeyRef = useRef(docKey);

  // 建一次。
  useEffect(() => {
    const view = new EditorView({
      state: EditorState.create({
        doc: value || '',
        extensions: baseExtensions((v) => onChangeRef.current?.(v), readOnly),
      }),
      parent: hostRef.current,
    });
    viewRef.current = view;
    lastKeyRef.current = docKey;
    return () => { view.destroy(); viewRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 切标签(docKey 变)→ 整篇替换为新 value;同一 docKey 内不回灌。
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    if (lastKeyRef.current === docKey) return;   // 同标签,跳过(避免回灌)
    lastKeyRef.current = docKey;
    const cur = view.state.doc.toString();
    const next = value || '';
    if (cur !== next) {
      view.dispatch({ changes: { from: 0, to: cur.length, insert: next }, selection: { anchor: 0 } });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docKey]);

  return <div ref={hostRef} className="mde-cm" />;
}
