/**
 * timeline-status.test.js — 剧本期望线「当前」唯一性(反馈 #96)。
 *
 * 群反馈(1335179168):「世界线有三个"当前",不知道是不是显示bug」。
 * 真因:桌面 PanelTimeline 与移动 panels 各写了一份「区间包含即当前」的谓词,
 * 而锚点区间**允许嵌套重叠**。生产 script 322 第 100 章同时落在
 * [1,292]序章 / [43,283]数日后 / [57,128] / [100,107] 四条里 → 四个「当前」。
 *
 * 不变量:
 *   · 任意输入下,'current' 至多一条(核心断言)
 *   · 选中的是最贴切(最窄)的那条,而不是把整本包住的那条
 *   · 包住当前章但没被选中的外层弧 = 'ongoing'(不能误标成已度过/待解锁)
 *   · 边界(恰好等于 min / max)、单章锚点、chapter_max 缺省、chapter_min 缺失
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { anchorStatuses, pickCurrentAnchorIndex } from '../lib/timeline-status.js';

// 生产真实形状(script 322 前 12 条的区间)
const PROD_322 = [
  { chapter_min: 1,   chapter_max: 292, story_time_label: '序章' },
  { chapter_min: 8,   chapter_max: 8 },
  { chapter_min: 15,  chapter_max: 15 },
  { chapter_min: 22,  chapter_max: 22 },
  { chapter_min: 29,  chapter_max: 29 },
  { chapter_min: 29,  chapter_max: 29 },
  { chapter_min: 36,  chapter_max: 36 },
  { chapter_min: 36,  chapter_max: 36 },
  { chapter_min: 43,  chapter_max: 283, story_time_label: '数日后' },
  { chapter_min: 43,  chapter_max: 50 },
  { chapter_min: 57,  chapter_max: 128 },
  { chapter_min: 100, chapter_max: 107 },
];

const countCurrent = (sts) => sts.filter((s) => s === 'current').length;

describe('anchorStatuses — 「当前」唯一', () => {
  it('生产 script 322 第 100 章:修复前 4 条命中,现在只有 1 条 current', () => {
    const sts = anchorStatuses(PROD_322, 100);
    expect(countCurrent(sts)).toBe(1);
    // 最贴切的是 [100,107](最窄),不是 [1,292] 序章
    expect(sts[11]).toBe('current');
    // 其余三条包住当前章的外层弧 = ongoing,不能是 done/pending
    expect(sts[0]).toBe('ongoing');   // [1,292]
    expect(sts[8]).toBe('ongoing');   // [43,283]
    expect(sts[10]).toBe('ongoing');  // [57,128]
  });

  it('整本每一章都最多只有一条 current', () => {
    for (let ch = 1; ch <= 300; ch++) {
      expect(countCurrent(anchorStatuses(PROD_322, ch))).toBeLessThanOrEqual(1);
    }
  });

  it('已过去的窄锚点标 done,未来的标 pending', () => {
    const sts = anchorStatuses(PROD_322, 100);
    expect(sts[1]).toBe('done');      // [8,8]
    expect(sts[9]).toBe('done');      // [43,50]
    const early = anchorStatuses(PROD_322, 5);
    expect(early[1]).toBe('pending'); // [8,8] 还没到
  });

  it('同宽区间取起点更靠后的那条', () => {
    const anchors = [
      { chapter_min: 10, chapter_max: 20 },
      { chapter_min: 15, chapter_max: 25 },
    ];
    expect(pickCurrentAnchorIndex(anchors, 18)).toBe(1);
  });

  it('边界:恰好等于 min 或 max 都算落在区间内', () => {
    const anchors = [{ chapter_min: 10, chapter_max: 20 }];
    expect(anchorStatuses(anchors, 10)).toEqual(['current']);
    expect(anchorStatuses(anchors, 20)).toEqual(['current']);
    expect(anchorStatuses(anchors, 21)).toEqual(['done']);
    expect(anchorStatuses(anchors, 9)).toEqual(['pending']);
  });

  it('chapter_max 缺省 = 单章锚点', () => {
    const anchors = [{ chapter_min: 7 }];
    expect(anchorStatuses(anchors, 7)).toEqual(['current']);
    expect(anchorStatuses(anchors, 8)).toEqual(['done']);
  });

  it('chapter_min 缺失的锚点无法定位 → pending,且不占用 current 名额', () => {
    const anchors = [{ story_time_label: '无章号' }, { chapter_min: 5, chapter_max: 9 }];
    const sts = anchorStatuses(anchors, 6);
    expect(sts[0]).toBe('pending');
    expect(sts[1]).toBe('current');
    expect(countCurrent(sts)).toBe(1);
  });

  it('空/非数组输入不炸', () => {
    expect(anchorStatuses([], 1)).toEqual([]);
    expect(anchorStatuses(null, 1)).toEqual([]);
    expect(pickCurrentAnchorIndex(null, 1)).toBe(-1);
  });

  it('当前章落在所有区间之外时没有 current', () => {
    const anchors = [{ chapter_min: 10, chapter_max: 20 }, { chapter_min: 30, chapter_max: 40 }];
    const sts = anchorStatuses(anchors, 25);
    expect(countCurrent(sts)).toBe(0);
    expect(sts).toEqual(['done', 'pending']);
  });
});

describe('桌面 ↔ 移动 同缝(奇偶守卫)', () => {
  it('两端都必须 import lib/timeline-status,不准各写一份区间谓词', () => {
    const desktop = readFileSync(resolve(__dirname, '../components/game/PanelTimeline.jsx'), 'utf-8');
    const mobile = readFileSync(resolve(__dirname, '../mobile/game/panels.jsx'), 'utf-8');
    for (const [name, src] of [['PanelTimeline', desktop], ['mobile/panels', mobile]]) {
      expect(src, `${name} 未使用共享缝`).toMatch(/anchorStatuses/);
      // 不准再出现「chapter_min <= currentChapter」这类各自为政的判定
      expect(src, `${name} 又手写了区间包含谓词`).not.toMatch(/chapter_min\s*<=\s*currentChapter/);
    }
  });
});
