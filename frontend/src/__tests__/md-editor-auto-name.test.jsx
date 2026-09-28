/**
 * md-editor-auto-name.test.jsx — 剧本编辑器资源管理器里「系统代起的名字」不许撞名。
 *
 * 角色卡新建是 create_only(同名报 400),锚点按 (阶段, 时间标签) 唯一(同名 409)。系统替用户起的
 * 名字 —— 空名时的默认名「新角色」「新时点」,以及右键「复制」生成的「X 副本」—— 第二次用就撞:
 * 复制同一张卡两次、或连着新建两个默认名锚点,第二次直接报错。用户没法对一个不是自己起的名字负责,
 * 这类名字必须自动避让同组已有名字(加序号);用户自己输入的名字照旧,撞了就如实报错。
 */
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import '../i18n/index.js';

import { createNode, uniqueName } from '../components/md-editor/node-crud.js';

const apiClientSource = readFileSync(resolve(__dirname, '../api-client.js'), 'utf-8');

let calls = [];
let routes = [];
function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}
function route(method, re, handler) { routes.push({ method, re, handler }); }
async function fakeFetch(url, init = {}) {
  const u = new URL(String(url), 'http://t');
  const method = (init.method || 'GET').toUpperCase();
  const body = init.body ? JSON.parse(init.body) : undefined;
  calls.push({ method, path: u.pathname, body });
  for (const r of routes) {
    if (r.method === method && u.pathname.match(r.re)) return r.handler({ body });
  }
  return json(404, { ok: false, error: `no route ${method} ${u.pathname}` });
}

beforeAll(() => { new Function(apiClientSource).call(window); });
beforeEach(() => {
  calls = []; routes = [];
  window.fetch = vi.fn(fakeFetch);
  window.__apiToast = vi.fn();
});
afterEach(() => { vi.restoreAllMocks(); });

describe('uniqueName', () => {
  it('没撞名原样返回,撞了从 2 开始加序号', () => {
    expect(uniqueName('新角色', [])).toBe('新角色');
    expect(uniqueName('新角色', ['新角色'])).toBe('新角色 2');
    expect(uniqueName('新角色', ['新角色', '新角色 2'])).toBe('新角色 3');
  });
});

describe('createNode 自动避让', () => {
  it('角色卡:空名 → 默认名已存在时改用「新角色 2」', async () => {
    route('GET', /\/scripts\/7\/character-cards$/, () => json(200, { ok: true, items: [{ id: 1, name: '新角色' }] }));
    route('POST', /\/scripts\/7\/character-cards$/, ({ body }) => json(200, { ok: true, card: { id: 2, name: body.name } }));
    const r = await createNode('card', 7, '');
    const post = calls.find((c) => c.method === 'POST');
    expect(post.body.name).toBe('新角色 2');
    expect(r.label).toBe('新角色 2');
  });

  it('锚点:复制两次同一个锚点,第二份叫「X 副本 2」而不是撞 409', async () => {
    route('GET', /\/scripts\/7\/timeline$/, () => json(200, { phases: [
      { phase_label: '', anchors: [{ anchor_id: 1, story_time_label: '雨夜' }, { anchor_id: 2, story_time_label: '雨夜 副本' }] },
    ] }));
    route('POST', /\/scripts\/7\/anchors$/, ({ body }) => json(200, { ok: true, anchor: { id: 3, story_time_label: body.story_time_label, chapter_min: 1, chapter_max: 1 } }));
    await createNode('anchor', 7, '雨夜 副本', { autoName: true });
    const post = calls.find((c) => c.method === 'POST');
    expect(post.body.story_time_label).toBe('雨夜 副本 2');
  });

  it('用户自己输入的名字不改写(撞了就让后端如实报错)', async () => {
    route('POST', /\/scripts\/7\/character-cards$/, () => json(400, { ok: false, error: '该剧本已存在同名 NPC 角色卡,请改用不同的名字' }));
    await expect(createNode('card', 7, '沈知微')).rejects.toThrow(/同名/);
    expect(calls.filter((c) => c.method === 'GET')).toHaveLength(0);
    expect(calls.find((c) => c.method === 'POST').body.name).toBe('沈知微');
  });
});
