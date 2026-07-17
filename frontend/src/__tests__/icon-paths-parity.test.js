/**
 * icon-paths-parity.test.js — 图标字典双维护收口(2026-07-17 审计)源码级奇偶守卫。
 *
 * 背景:game-icons.jsx(平台端)与 mobile/icons.jsx(移动端)曾各自手抄 57 个同名
 * 图标的 SVG path,其中 43 个视觉等价、14 个真不同形(DIVERGED)。43 个等价条目
 * 先行收口;14 个 DIVERGED 条目经用户拍板逐个裁定胜者版本后(2026-07-17,桌面版
 * 胜出 7 个 + 移动版胜出 7 个)一并归一,DIVERGED 状态清零。现两端从
 * lib/icon-paths.jsx 的 SHARED_ICON_PATHS 共享全部 57 个同名条目
 * (`paths = { ...SHARED_ICON_PATHS, ...本地独有条目 }`)。
 *
 * 这不是渲染测试,是纯源码级静态断言(读文件文本做正则/字符串检查),防止：
 *   · 未来有人绕开共享字典,又在单侧文件里手写一份同名条目(新分叉);
 *   · SHARED_ICON_PATHS 被误删/清空,整体收口悄悄退化。
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

const gameIconsSrc = readFileSync(resolve(__dirname, '../game-icons.jsx'), 'utf-8');
const mobileIconsSrc = readFileSync(resolve(__dirname, '../mobile/icons.jsx'), 'utf-8');
const iconPathsSrc = readFileSync(resolve(__dirname, '../lib/icon-paths.jsx'), 'utf-8');

describe('图标字典双维护收口 — 源码级奇偶守卫', () => {
  it('game-icons.jsx 从 lib/icon-paths.jsx 导入 SHARED_ICON_PATHS', () => {
    expect(gameIconsSrc).toMatch(/import\s*\{\s*SHARED_ICON_PATHS\s*\}\s*from\s*['"]\.\/lib\/icon-paths\.jsx['"]/);
  });

  it('mobile/icons.jsx 从 ../lib/icon-paths.jsx 导入 SHARED_ICON_PATHS', () => {
    expect(mobileIconsSrc).toMatch(/import\s*\{\s*SHARED_ICON_PATHS\s*\}\s*from\s*['"]\.\.\/lib\/icon-paths\.jsx['"]/);
  });

  it('game-icons.jsx 的 paths 字典把 SHARED_ICON_PATHS spread 进去', () => {
    expect(gameIconsSrc).toMatch(/const\s+paths\s*=\s*\{\s*\.\.\.SHARED_ICON_PATHS\s*,/);
  });

  it('mobile/icons.jsx 的 paths 字典把 SHARED_ICON_PATHS spread 进去', () => {
    expect(mobileIconsSrc).toMatch(/const\s+paths\s*=\s*\{\s*\.\.\.SHARED_ICON_PATHS\s*,/);
  });

  it('SHARED_ICON_PATHS 条目数 >= 55(防整体收口退化;含 2026-07-17 归一的 14 项 DIVERGED)', () => {
    const start = iconPathsSrc.indexOf('export const SHARED_ICON_PATHS = {');
    expect(start).toBeGreaterThan(-1);
    const body = iconPathsSrc.slice(start);
    // 顶层条目形如 "  key: <...>," —— 用行首两空格+标识符+冒号粗粒度计数
    // (够用:本文件内没有嵌套的同缩进 "标识符:" 误报源)
    const keyMatches = body.match(/^ {2}[A-Za-z_][A-Za-z0-9_]*:/gm) || [];
    expect(keyMatches.length).toBeGreaterThanOrEqual(55);
  });

  it('两文件里没有与 SHARED_ICON_PATHS 同名的本地条目(防单侧新增造成新分叉)', () => {
    const start = iconPathsSrc.indexOf('export const SHARED_ICON_PATHS = {');
    const body = iconPathsSrc.slice(start);
    const sharedKeys = [...body.matchAll(/^ {2}([A-Za-z_][A-Za-z0-9_]*):/gm)].map((m) => m[1]);
    expect(sharedKeys.length).toBeGreaterThan(0);

    for (const src of [gameIconsSrc, mobileIconsSrc]) {
      const pathsStart = src.indexOf('const paths = {');
      const pathsBody = src.slice(pathsStart);
      const localKeys = [...pathsBody.matchAll(/^ {4}([A-Za-z_][A-Za-z0-9_]*):/gm)].map((m) => m[1]);
      const collisions = localKeys.filter((k) => sharedKeys.includes(k));
      expect(collisions).toEqual([]);
    }
  });
});
