/**
 * cm-ide-basics.test.js — 编辑器 IDE 基本功(v1.82.1)在**真 EditorView** 上的回归。
 *
 * 折叠 / 多光标 / Mod-d 的依赖(@codemirror/language、@codemirror/search)一直在
 * package.json 里,却从没在 baseExtensions 里开过。本文件锁「真的开了」而不是「import 了」——
 * 编辑器改动纪律:光看代码在不算数(前科:diff 的块装饰代码在、运行时直接抛)。
 */
import { describe, it, expect } from 'vitest';
import { EditorState, EditorSelection } from '@codemirror/state';
import { EditorView } from '@codemirror/view';
import { foldable, foldEffect, foldedRanges } from '@codemirror/language';
import { selectNextOccurrence } from '@codemirror/search';
import { baseExtensions } from '../components/CodeMirrorEditor.jsx';

const DOC = [
  '# 第一卷',
  '',
  '## 第一章 雪夜',
  '正文正文正文。她说:别去。',
  '再一行正文。别去,她又说了一遍。',
  '',
  '## 第二章 归途',
  '第二章的正文。',
].join('\n');

function mkView(doc = DOC) {
  const parent = document.createElement('div');
  document.body.appendChild(parent);
  return new EditorView({
    state: EditorState.create({
      doc,
      extensions: baseExtensions(() => {}, false, () => null, () => null, () => null, () => {}, () => false, () => null),
    }),
    parent,
  });
}

describe('CM6 折叠', () => {
  it('markdown 标题可折叠(foldService 真的接上了)', () => {
    const view = mkView();
    const headingLine = view.state.doc.line(3);           // '## 第一章 雪夜'
    const range = foldable(view.state, headingLine.from, headingLine.to);
    expect(range, 'markdown 标题行不可折叠 → codeFolding/lang-markdown 没接上').toBeTruthy();
    view.dispatch({ effects: foldEffect.of(range) });
    let folded = 0;
    foldedRanges(view.state).between(0, view.state.doc.length, () => { folded += 1; });
    expect(folded).toBe(1);
    view.destroy();
  });

  it('折叠槽渲染出来了', () => {
    const view = mkView();
    expect(view.dom.querySelector('.cm-foldGutter'), '折叠槽没渲染').toBeTruthy();
    view.destroy();
  });
});

describe('CM6 多光标', () => {
  it('allowMultipleSelections facet 是开的', () => {
    const view = mkView();
    expect(view.state.facet(EditorState.allowMultipleSelections)).toBe(true);
    view.destroy();
  });

  it('两个选区不会被压成一个', () => {
    const view = mkView();
    view.dispatch({
      selection: EditorSelection.create(
        [EditorSelection.range(0, 2), EditorSelection.range(10, 12)], 1,
      ),
    });
    expect(view.state.selection.ranges.length, '多重选区被压成一个 → 开关没生效').toBe(2);
    view.destroy();
  });

  it('多光标同时输入,两处都改到', () => {
    const view = mkView('甲甲\n甲甲');
    view.dispatch({
      selection: EditorSelection.create(
        [EditorSelection.cursor(0), EditorSelection.cursor(3)], 1,
      ),
    });
    view.dispatch(view.state.changeByRange((r) => ({
      changes: { from: r.from, insert: 'X' },
      range: EditorSelection.cursor(r.from + 1),
    })));
    expect(view.state.doc.toString()).toBe('X甲甲\nX甲甲');
    view.destroy();
  });
});

describe('CM6 Mod-d 选中下一处', () => {
  it('selectNextOccurrence 能加选,且不会删字符', () => {
    const view = mkView();
    const doc = view.state.doc.toString();
    const at = doc.indexOf('别去');
    expect(at).toBeGreaterThan(-1);
    view.dispatch({ selection: EditorSelection.single(at, at + 2) });
    const ok = selectNextOccurrence(view);
    expect(ok, 'selectNextOccurrence 对本文档无效').toBe(true);
    expect(view.state.selection.ranges.length, '没有加选第二处').toBe(2);
    expect(view.state.doc.toString(), 'Mod-d 变成了删除字符').toBe(doc);
    view.destroy();
  });

  it('Mod-d 的绑定排在 defaultKeymap 之前(否则被 deleteCharForward 吃掉)', () => {
    // baseExtensions 里两条 keymap.of 的先后就是优先级。读源码断言顺序 —— jsdom 里
    // 真实按键派发不稳,但「谁在前」是确定性的,而且正是这条 bug 的成因。
    const src = baseExtensions.toString();
    const mine = src.indexOf('Mod-d');
    const dflt = src.indexOf('defaultKeymap');
    expect(mine).toBeGreaterThan(-1);
    expect(mine, 'Mod-d 绑定排在 defaultKeymap 之后,会被它的 deleteCharForward 覆盖')
      .toBeLessThan(dflt);
  });
});
