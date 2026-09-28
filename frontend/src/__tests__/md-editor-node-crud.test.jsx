/**
 * md-editor-node-crud.test.jsx — 剧本编辑器资源管理器(md-editor FileTree)增删改契约。
 *
 * 群反馈截图「操作失败 / 缺少必填字段 logical_key / name / type」:新建设定实体只发
 * {name, type},后端却要求必须给 logical_key。后端已改为缺省时自动生成;这里锁前端这一侧:
 *   · createNode('canon') 成功路径(真 api-client → POST 不带 logical_key → 用返回的 key 打开)
 *   · 失败时 toast 的 detail 就是后端 error 原文(不是空的「操作失败」)
 *   · 改名 canon 走 PUT 补丁只发 name(不再先 GET 整行再整行回写);改名锚点改的是树上显示的
 *     story_time_label(之前改的是 story_phase,树上看起来没变)
 *   · 改名输入框预填裸名字,不带「(概念)」「(1-1)」这类显示后缀;复制 canon 保留原类型
 *   · 列表走 fetch_all(默认一页 50 条,新建的实体 importance=0 排最后 → 建了在树里看不到)
 *   · canon 的 logical_key 在 front-matter 里只读:改了也不会把 PUT 打到一个不存在的 key 上
 */
import React from 'react';
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

import { createNode, renameNode, fetchGroupList } from '../components/md-editor/node-crud.js';
import { saveNodeContent, loadNodeContentMeta } from '../components/md-editor/node-io.js';
import { FileTree } from '../components/md-editor/FileTree.jsx';
import { toMd, fromMd } from '../lib/md-serialize.js';

const apiClientSource = readFileSync(resolve(__dirname, '../api-client.js'), 'utf-8');

// ── 假后端:按 method + path 路由,记录每次请求 ─────────────────────────────
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
  calls.push({ method, path: u.pathname, query: Object.fromEntries(u.searchParams), body });
  for (const r of routes) {
    const m = r.method === method && u.pathname.match(r.re);
    if (m) return r.handler({ body, query: Object.fromEntries(u.searchParams), m });
  }
  return json(404, { ok: false, error: `no route ${method} ${u.pathname}` });
}

beforeAll(() => {
  new Function(apiClientSource).call(window);
});
beforeEach(() => {
  calls = []; routes = [];
  window.fetch = vi.fn(fakeFetch);
  window.__apiToast = vi.fn();
  try { localStorage.clear(); } catch (_) {}
});
afterEach(() => { vi.restoreAllMocks(); });

const dec = (s) => decodeURIComponent(s);

describe('createNode(canon)', () => {
  it('不带 logical_key 发 POST,用后端返回的 key 打开', async () => {
    route('POST', /\/scripts\/7\/canon-entities$/, ({ body }) => json(200, {
      ok: true, logical_key: `${body.name}_concept`,
      entity: { logical_key: `${body.name}_concept`, name: body.name, type: body.type },
    }));
    const r = await createNode('canon', 7, '灵脉');
    const post = calls.find((c) => c.method === 'POST');
    expect(post.body).toEqual({ name: '灵脉', type: 'concept' });
    expect(r.id).toBe('灵脉_concept');
    expect(r.label).toBe('灵脉 (概念)');
  });

  it('复制时可以带上原类型', async () => {
    route('POST', /\/scripts\/7\/canon-entities$/, ({ body }) => json(200, {
      ok: true, entity: { logical_key: 'x_2', name: body.name, type: body.type },
    }));
    await createNode('canon', 7, '北港 副本', { type: 'location' });
    expect(calls[0].body).toEqual({ name: '北港 副本', type: 'location' });
  });

  it('后端报错原样抛出(FileTree 拿它当 toast detail)', async () => {
    route('POST', /\/scripts\/7\/canon-entities$/, () => json(400, { ok: false, error: '名称不能为空' }));
    await expect(createNode('canon', 7, 'x')).rejects.toThrow('名称不能为空');
  });
});

describe('renameNode', () => {
  it('canon:PUT 补丁只发 name,不先 GET 整行回写', async () => {
    route('PUT', /\/scripts\/7\/canon-entities\/([^/]+)$/, ({ body }) => json(200, { ok: true, entity: body }));
    await renameNode('canon', 7, '灵脉_concept', '龙脉');
    expect(calls.map((c) => c.method)).toEqual(['PUT']);
    expect(dec(calls[0].path)).toMatch(/\/canon-entities\/灵脉_concept$/);
    expect(calls[0].body).toEqual({ name: '龙脉' });
  });

  it('anchor:改的是树上显示的 story_time_label', async () => {
    route('PUT', /\/scripts\/7\/anchors\/(\d+)$/, ({ body }) => json(200, { ok: true, anchor: body }));
    await renameNode('anchor', 7, 12, '十年后');
    expect(calls[0].body).toEqual({ story_time_label: '十年后' });
  });
});

describe('fetchGroupList', () => {
  it('canon 全量拉取(fetch_all)并带裸名字', async () => {
    route('GET', /\/scripts\/7\/canon-entities$/, () => json(200, {
      ok: true, items: [{ logical_key: 'a', name: '甲', type: 'character' }], page: { has_more: false },
    }));
    const items = await fetchGroupList('canon', 7);
    expect(calls[0].query.fetch_all).toBe('true');
    expect(items).toEqual([{ id: 'a', name: '甲', type: 'character', label: '甲 (人物)' }]);
  });

  it('worldbook 全量拉取(fetch_all)', async () => {
    route('GET', /\/scripts\/7\/worldbook$/, () => json(200, {
      ok: true, items: [{ id: 3, title: '铁人团' }], page: { has_more: false },
    }));
    const items = await fetchGroupList('worldbook', 7);
    expect(calls[0].query.fetch_all).toBe('true');
    expect(items[0]).toMatchObject({ id: 3, name: '铁人团', label: '铁人团' });
  });

  it('打开一条世界书只读这一条,不再为一条拉全量列表', async () => {
    route('GET', /\/scripts\/7\/worldbook\/3$/, () => json(200, {
      ok: true, entry: { id: 3, title: '铁人团', content: '正文', keys: [] },
    }));
    route('GET', /\/scripts\/7\/worldbook$/, () => json(200, { ok: true, items: [] }));
    const { content } = await loadNodeContentMeta('worldbook', 7, 3);
    expect(calls.map((c) => `${c.method} ${c.path.replace(/^\/api(\/v1)?/, '')}`)).toEqual(['GET /scripts/7/worldbook/3']);
    expect(content).toContain('铁人团');
  });

  it('anchor 带裸名字(不含章节区间后缀)', async () => {
    route('GET', /\/scripts\/7\/timeline$/, () => json(200, {
      ok: true, phases: [{ phase_label: '序', anchors: [{ id: 5, story_time_label: '三年后', chapter_min: 1, chapter_max: 3 }] }],
    }));
    const items = await fetchGroupList('anchor', 7);
    expect(items[0]).toMatchObject({ id: 5, name: '三年后', label: '三年后 (1-3)' });
  });
});

describe('canon front-matter 的 logical_key 只读', () => {
  it('fromMd 不把 logical_key 放进可写补丁', () => {
    const md = toMd('canon', { logical_key: 'k1', name: '甲', type: 'concept', background: '' });
    expect(fromMd('canon', md).logical_key).toBeUndefined();
  });

  it('保存时 PUT 的目标恒为当前节点 id', async () => {
    route('PUT', /\/scripts\/7\/canon-entities\/([^/]+)$/, ({ body }) => json(200, { ok: true, entity: body }));
    const orig = toMd('canon', { logical_key: 'k1', name: '甲', type: 'concept', background: '' });
    const edited = orig.replace('logical_key: k1', 'logical_key: k2').replace('name: 甲', 'name: 乙');
    await saveNodeContent('canon', 7, 'k1', edited, orig);
    expect(dec(calls[0].path)).toMatch(/\/canon-entities\/k1$/);
    expect(calls[0].body).toMatchObject({ name: '乙' });
    expect(calls[0].body.logical_key).toBeUndefined();
  });
});

describe('FileTree 资源管理器', () => {
  function setupLists() {
    route('GET', /\/scripts\/7\/chapters$/, () => json(200, { ok: true, chapters: [] }));
    route('GET', /\/scripts\/7\/canon-entities$/, () => json(200, {
      ok: true, items: [{ logical_key: 'bg_location', name: '北港', type: 'location' }], page: { has_more: false },
    }));
  }

  it('新建设定实体失败:toast 显示后端给的原因', async () => {
    setupLists();
    route('POST', /\/scripts\/7\/canon-entities$/, () => json(400, {
      ok: false, error: '类型「x」不认识,可选:人物 character / 概念 concept',
    }));
    render(<FileTree scriptId={7} openNode={vi.fn()} activeKey={null} reloadKey={0} onMutate={vi.fn()} />);
    fireEvent.click(screen.getByTitle('新建知识库人物'));
    const input = await screen.findByPlaceholderText('新知识库人物名称');
    fireEvent.change(input, { target: { value: '灵脉' } });
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }); });
    await waitFor(() => expect(window.__apiToast).toHaveBeenCalledWith(
      '操作失败', expect.objectContaining({ kind: 'danger', detail: '类型「x」不认识,可选:人物 character / 概念 concept' }),
    ));
  });

  it('新建设定实体成功:打开后端返回的 key', async () => {
    setupLists();
    route('POST', /\/scripts\/7\/canon-entities$/, ({ body }) => json(200, {
      ok: true, entity: { logical_key: '灵脉_concept', name: body.name, type: body.type },
    }));
    const openNode = vi.fn();
    render(<FileTree scriptId={7} openNode={openNode} activeKey={null} reloadKey={0} onMutate={vi.fn()} />);
    fireEvent.click(screen.getByTitle('新建知识库人物'));
    const input = await screen.findByPlaceholderText('新知识库人物名称');
    fireEvent.change(input, { target: { value: '灵脉' } });
    await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }); });
    await waitFor(() => expect(openNode).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'canon', id: '灵脉_concept' }),
    ));
    expect(window.__apiToast).toHaveBeenCalledWith('已新建', expect.objectContaining({ kind: 'ok' }));
  });

  it('改名输入框预填裸名字(不带类型后缀)', async () => {
    setupLists();
    localStorage.setItem('mde.tree.expanded2', JSON.stringify(['canon']));
    render(<FileTree scriptId={7} openNode={vi.fn()} activeKey={null} reloadKey={0} onMutate={vi.fn()} />);
    const item = await screen.findByText('北港 (地点)');
    fireEvent.doubleClick(item);
    const input = await screen.findByDisplayValue('北港');
    expect(input).toBeTruthy();
  });

  it('复制设定实体保留原类型', async () => {
    setupLists();
    localStorage.setItem('mde.tree.expanded2', JSON.stringify(['canon']));
    route('POST', /\/scripts\/7\/canon-entities$/, ({ body }) => json(200, {
      ok: true, entity: { logical_key: 'bg2', name: body.name, type: body.type },
    }));
    render(<FileTree scriptId={7} openNode={vi.fn()} activeKey={null} reloadKey={0} onMutate={vi.fn()} />);
    const item = await screen.findByText('北港 (地点)');
    fireEvent.contextMenu(item);
    await act(async () => { fireEvent.click(await screen.findByText('复制')); });
    await waitFor(() => expect(calls.some((c) => c.method === 'POST')).toBe(true));
    const post = calls.find((c) => c.method === 'POST');
    expect(post.body).toEqual({ name: '北港 副本', type: 'location' });
  });
});
