// md-diff.js — Cursor/VSCode 风「AI 提议改章 → 编辑器内联 diff 审阅」(CodeMirror 6)。
// 设计:agent 提议把某章改成 newText → 行级 diff 把新增行标绿(line deco)、被删行以红色删除线块挂在
// 原位(block widget)。审阅粒度有两级:① 顶栏「全部批准 / 拒绝」② 每个改动块(hunk)自己的
// 「接受 / 拒绝」小工具条(Copilot 逐块审阅)。批准=保留新文,拒绝=还原旧文,逐块取舍=两者混合。
// 复用 md-continue.js 同款 StateField+Decoration+showPanel 原语(零新依赖)。
import { StateField, StateEffect } from '@codemirror/state';
import { EditorView, Decoration, WidgetType, showPanel } from '@codemirror/view';
import { setPanelVisible } from './md-panel.js';

// LCS 表是 n×m 的 Uint32:一章 2 万行 × 2 万行 = 1.6 GB,浏览器标签页当场死。
// 原注释写着「超大文本退化由调用方控」—— 而调用方从来没控(路线图 P2 记在案)。
// 现在两道闸都落在这里:① 先裁掉首尾完全相同的行(典型「只改中间几段」能把 n·m 砍掉一两个
// 数量级,而且 hunk 更紧凑);② 裁完仍超过 MAX_LCS_CELLS 就退化成「整段全删全增」,
// 让作者仍能整体接受/拒绝,而不是编辑器卡死。
const MAX_LCS_CELLS = 4_000_000;   // ≈16 MB Uint32 表,单章正常量级远够用

// ── 行级 LCS diff → ops:[{type:'same'|'add'|'del', text}] ──────────────────
export function lineDiff(oldText, newText) {
  const a = (oldText || '').split('\n');
  const b = (newText || '').split('\n');
  // ① 首尾公共行裁剪 —— 它们必然是 same,不必进 LCS 表
  let lo = 0;
  while (lo < a.length && lo < b.length && a[lo] === b[lo]) lo++;
  let hi = 0;
  while (hi < a.length - lo && hi < b.length - lo
         && a[a.length - 1 - hi] === b[b.length - 1 - hi]) hi++;
  const head = a.slice(0, lo).map((text) => ({ type: 'same', text }));
  const tail = a.slice(a.length - hi).map((text) => ({ type: 'same', text }));
  const midA = a.slice(lo, a.length - hi);
  const midB = b.slice(lo, b.length - hi);
  // ② 退化闸:裁完还是太大就整段全删全增(仍可整体接受/拒绝,不卡死)
  if (midA.length * midB.length > MAX_LCS_CELLS) {
    return [
      ...head,
      ...midA.map((text) => ({ type: 'del', text })),
      ...midB.map((text) => ({ type: 'add', text })),
      ...tail,
    ];
  }
  return [...head, ..._lcsDiff(midA, midB), ...tail];
}

function _lcsDiff(a, b) {
  const n = a.length, m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { ops.push({ type: 'same', text: a[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push({ type: 'del', text: a[i] }); i++; }
    else { ops.push({ type: 'add', text: b[j] }); j++; }
  }
  while (i < n) { ops.push({ type: 'del', text: a[i] }); i++; }
  while (j < m) { ops.push({ type: 'add', text: b[j] }); j++; }
  return ops;
}

// 给 ops 标 hunk 号:连续的非 same 段=一个 hunk。返回 { opHunk:number[]（-1=same）, hunkCount }。
function tagHunks(ops) {
  const opHunk = new Array(ops.length).fill(-1);
  let count = 0, inHunk = false;
  for (let k = 0; k < ops.length; k++) {
    if (ops[k].type === 'same') { inHunk = false; }
    else { if (!inHunk) { inHunk = true; count++; } opHunk[k] = count - 1; }
  }
  return { opHunk, hunkCount: count };
}

// 据 ops + 每 hunk 决策重建「应显示的文本」:same 总在;add 除非该 hunk 被拒;del 仅当该 hunk 被拒(还原)。
function reconstruct(ops, opHunk, decisions) {
  const out = [];
  for (let k = 0; k < ops.length; k++) {
    const op = ops[k]; const d = decisions[opHunk[k]];
    if (op.type === 'same') out.push(op.text);
    else if (op.type === 'add') { if (d !== 'reject') out.push(op.text); }
    else { if (d === 'reject') out.push(op.text); }
  }
  return out.join('\n');
}

function allResolved(hunkCount, decisions) {
  for (let h = 0; h < hunkCount; h++) if (!decisions[h]) return false;
  return true;
}

// ── 被删行块组件(红色删除线)──────────────────────────────────────────────
class DelWidget extends WidgetType {
  constructor(text) { super(); this.text = text; }
  eq(o) { return o.text === this.text; }
  toDOM() {
    const d = document.createElement('div');
    d.className = 'mde-diff-del';
    d.textContent = this.text || ' ';
    return d;
  }
  ignoreEvent() { return true; }
}

// ── 单个改动块的「接受 / 拒绝」小工具条(逐块审阅)─────────────────────────
// 点击时用 EditorView.findFromDOM 现取 view(不在 widget 里存 view,以便从 StateField 计算装饰)。
class HunkBarWidget extends WidgetType {
  constructor(hunkIdx, nAdd, nDel) { super(); this.hunkIdx = hunkIdx; this.nAdd = nAdd; this.nDel = nDel; }
  eq(o) { return o.hunkIdx === this.hunkIdx && o.nAdd === this.nAdd && o.nDel === this.nDel; }
  toDOM() {
    const idx = this.hunkIdx;
    const bar = document.createElement('div');
    bar.className = 'mde-diff-hunkbar';
    const label = document.createElement('span');
    label.className = 'mde-diff-hunk-label';
    label.textContent = `本段 +${this.nAdd} / -${this.nDel}`;
    const mk = (cls, txt, decision) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = cls; b.textContent = txt;
      b.onmousedown = (e) => { e.preventDefault(); };
      b.onclick = (e) => { e.preventDefault(); const root = b.closest('.cm-editor'); const view = root && EditorView.findFromDOM(root); if (view) setHunkDecision(view, idx, decision); };
      return b;
    };
    bar.append(label, mk('mde-diff-hunk-acc', '接受', 'accept'), mk('mde-diff-hunk-rej', '拒绝', 'reject'));
    return bar;
  }
  ignoreEvent() { return false; }
}

// ── 状态:{ops, opHunk, hunkCount, oldText, decisions, onAccept, onReject, onMixed} | null ──
const setChapterDiff = StateEffect.define();

export const chapterDiffField = StateField.define({
  create: () => null,
  update(val, tr) {
    let v = val;
    for (const e of tr.effects) if (e.is(setChapterDiff)) v = e.value;
    return v;
  },
});

// 块装饰(删除块 / 逐块工具条)CM6 不允许由 ViewPlugin 提供,必须从 StateField 经
// EditorView.decorations 提供 → buildDeco 直接吃 state(无 view)。
function buildDeco(state) {
  const v = state.field(chapterDiffField, false);
  if (!v || !Array.isArray(v.ops)) return Decoration.none;
  const doc = state.doc;
  const ranges = [];
  const dec = v.decisions || {};
  let ln = 1;   // 当前显示文档的行号(doc 即 reconstruct 结果,行号对齐)
  let prevHunk = -1;
  for (let k = 0; k < v.ops.length; k++) {
    const op = v.ops[k];
    const h = v.opHunk[k];
    const d = h >= 0 ? dec[h] : undefined;
    // 进入一个「未决」hunk 的第一个 op → 在此处挂逐块工具条(side -2,排在删除块/绿行之上)。
    if (h >= 0 && h !== prevHunk && !d) {
      const at = ln <= doc.lines ? doc.line(ln).from : doc.length;
      // 统计该 hunk 的 +/-(只为标签)
      let nA = 0, nD = 0;
      for (let q = k; q < v.ops.length && v.opHunk[q] === h; q++) { if (v.ops[q].type === 'add') nA++; else if (v.ops[q].type === 'del') nD++; }
      ranges.push(Decoration.widget({ widget: new HunkBarWidget(h, nA, nD), block: true, side: -2 }).range(at));
    }
    if (h >= 0) prevHunk = h;

    if (op.type === 'same') { ln++; }
    else if (op.type === 'add') {
      if (d === 'reject') { /* 被拒的新增行不在 doc 里 */ }
      else { // 未决=标绿;已接受=普通文本
        if (!d && ln <= doc.lines) ranges.push(Decoration.line({ class: 'mde-diff-add' }).range(doc.line(ln).from));
        ln++;
      }
    } else { // del
      if (d === 'reject') { ln++; /* 还原的旧行=普通文本 */ }
      else if (!d) { // 未决=红色删除块挂在当前行起点前
        const at = ln <= doc.lines ? doc.line(ln).from : doc.length;
        ranges.push(Decoration.widget({ widget: new DelWidget(op.text), block: true, side: -1 }).range(at));
      } /* 已接受=该行被删,不显示 */
    }
  }
  return Decoration.set(ranges, true);   // sort=true:让 CM 自己排序,避免手排出错
}

const diffDecorations = EditorView.decorations.compute([chapterDiffField], buildDeco);

export function hasChapterDiff(view) { return !!(view && view.state.field(chapterDiffField, false)); }

// 应用一组新决策:重建 doc;若全部 hunk 已决→收尾(清 diff + 路由回调),否则只更新 decisions。
function applyDecisions(view, newDecisions) {
  const v = view.state.field(chapterDiffField, false);
  if (!v) return false;
  const finalText = reconstruct(v.ops, v.opHunk, newDecisions);
  if (allResolved(v.hunkCount, newDecisions)) {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: finalText }, effects: setChapterDiff.of(null) });
    view.focus();
    const vals = Object.values(newDecisions);
    const allAcc = vals.length > 0 && vals.every((d) => d === 'accept');
    const allRej = vals.length > 0 && vals.every((d) => d === 'reject');
    try {
      if (allAcc) { if (typeof v.onAccept === 'function') v.onAccept(); }
      else if (allRej) { if (typeof v.onReject === 'function') v.onReject(); }
      else if (typeof v.onMixed === 'function') v.onMixed(finalText);
      else if (typeof v.onAccept === 'function') v.onAccept();   // 兜底
    } catch (_) { /* 静默 */ }
  } else {
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: finalText }, effects: setChapterDiff.of({ ...v, decisions: newDecisions }) });
    view.focus();
  }
  return true;
}

// 单块决策(逐块工具条调用)。
function setHunkDecision(view, hunkIdx, decision) {
  const v = view.state.field(chapterDiffField, false);
  if (!v) return false;
  return applyDecisions(view, { ...(v.decisions || {}), [hunkIdx]: decision });
}

// 顶栏「全部批准」=把所有未决块设为 accept(已拒的保留→可能混合);「拒绝」=未决全设 reject。
export function acceptChapterDiff(view) {
  const v = view.state.field(chapterDiffField, false);
  if (!v) return false;
  const nd = { ...(v.decisions || {}) };
  for (let h = 0; h < v.hunkCount; h++) if (!nd[h]) nd[h] = 'accept';
  return applyDecisions(view, nd);
}

export function rejectChapterDiff(view) {
  const v = view.state.field(chapterDiffField, false);
  if (!v) return false;
  const nd = { ...(v.decisions || {}) };
  for (let h = 0; h < v.hunkCount; h++) if (!nd[h]) nd[h] = 'reject';
  return applyDecisions(view, nd);
}

// 入口:在 view 里展示「旧文→新文」的内联 diff。
// cbs.onAccept(全接受)/onReject(全拒绝)/onMixed(finalText,逐块取舍后的混合文本)。
export function showChapterDiff(view, oldText, newText, cbs = {}) {
  if (!view) return false;
  const ops = lineDiff(oldText || '', newText || '');
  const { opHunk, hunkCount } = tagHunks(ops);
  // doc→newText 与 diff 数据一并 dispatch(一笔事务):装饰由 chapterDiffField 计算,需在 doc 更新后生效。
  view.dispatch({
    changes: { from: 0, to: view.state.doc.length, insert: newText || '' },
    effects: setChapterDiff.of({
      ops, opHunk, hunkCount, oldText: oldText || '', decisions: {},
      onAccept: cbs.onAccept, onReject: cbs.onReject, onMixed: cbs.onMixed,
    }),
  });
  view.focus();
  return true;
}

// ── 顶栏:改动统计(剩余未决)+ 全部批准 / 拒绝 ────────────────────────────
function diffPanel(view) {
  const dom = document.createElement('div');
  dom.className = 'mde-diff-panel';
  dom.style.display = 'none';
  const render = (vw) => {
    const v = vw.state.field(chapterDiffField, false);
    setPanelVisible(dom, !!v);
    dom.textContent = '';
    if (!v) return;
    const dec = v.decisions || {};
    // 只统计仍未决 hunk 的 +/-(已逐块处理的不再计入顶栏)。
    let nAdd = 0, nDel = 0, pending = 0;
    const seen = new Set();
    for (let k = 0; k < v.ops.length; k++) {
      const h = v.opHunk[k]; if (h < 0 || dec[h]) continue;
      if (!seen.has(h)) { seen.add(h); pending++; }
      if (v.ops[k].type === 'add') nAdd++; else if (v.ops[k].type === 'del') nDel++;
    }
    const label = document.createElement('span');
    label.className = 'mde-diff-panel-label';
    label.textContent = pending > 0 ? `AI 改动 · ${pending} 段待审 · +${nAdd} / -${nDel} 行` : 'AI 改动 · 全部已处理';
    const ok = document.createElement('button');
    ok.className = 'mde-diff-accept'; ok.type = 'button'; ok.textContent = '全部批准';
    ok.onclick = () => acceptChapterDiff(vw);
    const no = document.createElement('button');
    no.className = 'mde-diff-reject'; no.type = 'button'; no.textContent = '全部拒绝';
    no.onclick = () => rejectChapterDiff(vw);
    dom.append(label, ok, no);
  };
  render(view);
  return { dom, top: true, update: (u) => render(u.view) };
}

export function chapterDiffExtension() {
  return [chapterDiffField, diffDecorations, showPanel.of(diffPanel)];
}
