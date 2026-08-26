/**
 * md-diff-degradation.test.js — lineDiff 的规模保护(v1.82.1)。
 *
 * LCS 表是 n×m 的 Uint32。原实现把「超大文本退化」推给调用方,而调用方从来没做 ——
 * 拿一整卷/整书当 newText 提议改稿时,编辑器会先吃掉 GB 级内存再卡死。
 */
import { describe, it, expect } from 'vitest';
import { lineDiff } from '../lib/md-diff.js';

const apply = (ops) => ops.filter((o) => o.type !== 'del').map((o) => o.text).join('\n');
const keepOld = (ops) => ops.filter((o) => o.type !== 'add').map((o) => o.text).join('\n');

describe('lineDiff 首尾裁剪', () => {
  it('只改中间一行时,前后仍是 same,且结果可还原两侧文本', () => {
    const oldT = ['a', 'b', 'c', 'd', 'e'].join('\n');
    const newT = ['a', 'b', 'X', 'd', 'e'].join('\n');
    const ops = lineDiff(oldT, newT);
    expect(apply(ops)).toBe(newT);
    expect(keepOld(ops)).toBe(oldT);
    // 裁剪不该把公共行拆成 add/del
    expect(ops.filter((o) => o.type === 'same').map((o) => o.text)).toEqual(['a', 'b', 'd', 'e']);
  });

  it('完全相同 → 全是 same,零改动块', () => {
    const t = ['x', 'y', 'z'].join('\n');
    const ops = lineDiff(t, t);
    expect(ops.every((o) => o.type === 'same')).toBe(true);
    expect(apply(ops)).toBe(t);
  });

  it('纯追加(常见:续写)只产生 add', () => {
    const oldT = ['a', 'b'].join('\n');
    const newT = ['a', 'b', 'c', 'd'].join('\n');
    const ops = lineDiff(oldT, newT);
    expect(ops.filter((o) => o.type === 'del')).toHaveLength(0);
    expect(apply(ops)).toBe(newT);
    expect(keepOld(ops)).toBe(oldT);
  });
});

describe('lineDiff 退化闸', () => {
  it('超大差异不再建 n×m 表,退化成全删全增且仍可整体取舍', () => {
    // 2500×2500 = 625 万 > MAX_LCS_CELLS(400 万)
    const oldT = Array.from({ length: 2500 }, (_, i) => `旧第${i}行`).join('\n');
    const newT = Array.from({ length: 2500 }, (_, i) => `新第${i}行`).join('\n');
    const t0 = Date.now();
    const ops = lineDiff(oldT, newT);
    const ms = Date.now() - t0;
    expect(ms, `退化闸没生效,耗时 ${ms}ms`).toBeLessThan(2000);
    expect(ops.some((o) => o.type === 'same')).toBe(false);
    expect(apply(ops)).toBe(newT);   // 全部接受 = 新文
    expect(keepOld(ops)).toBe(oldT); // 全部拒绝 = 旧文
  });

  it('体量大但首尾大量相同 → 裁剪后仍走精确 LCS', () => {
    const head = Array.from({ length: 3000 }, (_, i) => `头${i}`);
    const tail = Array.from({ length: 3000 }, (_, i) => `尾${i}`);
    const oldT = [...head, '中间旧', ...tail].join('\n');
    const newT = [...head, '中间新', ...tail].join('\n');
    const ops = lineDiff(oldT, newT);
    // 裁剪奏效的判据:只有一处改动块,而不是 6000 行全删全增
    expect(ops.filter((o) => o.type === 'del')).toEqual([{ type: 'del', text: '中间旧' }]);
    expect(ops.filter((o) => o.type === 'add')).toEqual([{ type: 'add', text: '中间新' }]);
    expect(apply(ops)).toBe(newT);
  });
});
