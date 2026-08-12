/**
 * browser-compat-shims.test.js — 老浏览器/App 内置 WebView 兼容(反馈 #95)。
 *
 * 群反馈(世界引擎):「注册成功后,点开这个在浏览器打开,登录会显示
 * AbortSignal.timeout is not a function?」
 *
 * `AbortSignal.timeout` 是 Chrome 103+ / Safari 16+ 的新 API,而它是 api-client `_send`
 * 的**唯一**超时来源 —— 缺了它不是"超时不生效",是每个 API 调用当场抛 TypeError,整站点不动。
 * 同族:`structuredClone`(Chrome 98+)在 game-console 顶层裸调,缺了整个游戏台白屏。
 *
 * 这里锁两件事:① 降级实现本身行为正确;② 源码里不再出现裸调(防回流)。
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { safeStructuredClone } from '../lib/clone-safe.js';

describe('safeStructuredClone — structuredClone 缺失时的降级', () => {
  const orig = globalThis.structuredClone;
  afterEach(() => { globalThis.structuredClone = orig; });

  it('有原生时用原生', () => {
    const spy = vi.fn((v) => JSON.parse(JSON.stringify(v)));
    globalThis.structuredClone = spy;
    const out = safeStructuredClone({ a: 1 });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(out).toEqual({ a: 1 });
  });

  it('没有原生时不抛,且返回深拷贝(不是同一引用)', () => {
    globalThis.structuredClone = undefined;
    const src = { turn: 0, history: [{ role: 'user', content: 'x' }], nested: { deep: [1, 2] } };
    const out = safeStructuredClone(src);
    expect(out).toEqual(src);
    expect(out).not.toBe(src);
    expect(out.history).not.toBe(src.history);
    expect(out.nested.deep).not.toBe(src.nested.deep);
  });

  it('原生存在但对该输入抛(某些 WebView)时也能回退', () => {
    globalThis.structuredClone = () => { throw new Error('DataCloneError'); };
    expect(safeStructuredClone({ a: 1 })).toEqual({ a: 1 });
  });

  it('连 JSON 都过不去(循环引用)时返回原值而不是崩', () => {
    globalThis.structuredClone = undefined;
    const cyc = { n: 1 }; cyc.self = cyc;
    expect(() => safeStructuredClone(cyc)).not.toThrow();
  });
});

describe('源码防回流:关键路径不准裸调新 API', () => {
  const read = (rel) => readFileSync(resolve(__dirname, '../' + rel), 'utf-8');
  // 只看真代码:注释里提到 API 名字是正常的(解释为什么要 shim),不该触发守卫
  const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');

  it('api-client 的 AbortSignal.timeout 必须有能力检测', () => {
    const src = read('api-client.js');
    expect(src).toMatch(/typeof\s+AbortSignal\.timeout\s*===\s*["']function["']/);
    // 必须提供 AbortController 回退,而不是只做检测然后返回 undefined
    expect(src).toMatch(/new AbortController\(\)/);
  });

  it('game-console 不准再裸调 structuredClone', () => {
    const src = read('entries/game-console.jsx');
    expect(src).toMatch(/safeStructuredClone/);
    expect(stripComments(src)).not.toMatch(/(?<![A-Za-z])structuredClone\s*\(/);
  });
});
