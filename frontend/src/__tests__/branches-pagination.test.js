/**
 * branches-pagination.test.js — 分支树必须翻页拉完。
 *
 * 群反馈(行者无疆 2026-07-27):「想在分支树里切分支的时候发现下午以后的进度都没存下来」
 * 「手动存档显示存了但分支树里没有」。**一条都没丢**——后端 tree() 默认 page_limit=1000
 * 且 `order by id`(升序),客户端不跟 page.next_cursor 就只拿到**最老的** 1000 个 commit。
 * 生产实证 save 268(1035 个 commit):第 1 页 1000 个、最大 turn_index=878,第 2 页 35 个、
 * 到 turn 909,**当前活跃 commit 5688 根本不在第 1 页里** → 树看着停在下午之前。
 * 玩家截图里「分支图 1000 commits · 110 refs」的 1000 就是 page_limit 本身(refs 不分页
 * 所以是全的,一眼能看出是截断)。
 *
 * 孪生(同批次已修,无法在 vitest 里覆盖,靠本文件的注释与 CHANGELOG 锁同步义务):
 *   mobile/src/api/index.ts   branches.list
 *   ios/Sources/API.swift     branchTree
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const PAGE = 1000;

function makeNodes(from, n) {
  return Array.from({ length: n }, (_, i) => ({ id: from + i, turn_index: from + i }));
}

/** 复刻后端 tree():order by id 升序 + limit + cursor(id > cursor)。 */
function fakeServer(total) {
  const all = makeNodes(1, total);
  return (url) => {
    const m = /cursor=(\d+)/.exec(url || '');
    const after = m ? Number(m[1]) : 0;
    const rest = all.filter((n) => n.id > after);
    const nodes = rest.slice(0, PAGE);
    const hasMore = rest.length > PAGE;
    return Promise.resolve({
      save: { id: 268 },
      nodes,
      refs: [{ name: 'main' }],
      active_commit_id: total,   // 活跃 commit 永远是最新那个
      page: { limit: PAGE, has_more: hasMore, next_cursor: hasMore ? String(nodes[nodes.length - 1].id) : null },
    });
  };
}

let server;

beforeEach(async () => {
  global.window = global.window || {};
  vi.resetModules();
});

afterEach(() => { vi.restoreAllMocks(); });

async function loadClientWith(total) {
  server = vi.fn(fakeServer(total));
  // api-client 内部走 GET(path, query);这里在 fetch 层拦,保持真实调用链。
  global.fetch = vi.fn(async (url) => ({
    ok: true, status: 200,
    headers: { get: () => 'application/json' },
    json: async () => server(String(url)),
    text: async () => JSON.stringify(await server(String(url))),
  }));
  const mod = await import('../api-client.js');
  return mod;
}

describe('branches.list 分页累积', () => {
  it('1035 个 commit → 全部拿到,不是被截成 1000', async () => {
    await loadClientWith(1035);
    const r = await window.api.branches.list(268);
    expect(r.nodes).toHaveLength(1035);
    expect(server).toHaveBeenCalledTimes(2);
  });

  it('活跃 commit 必须在返回集合里(截断时它恰好落在第 2 页)', async () => {
    await loadClientWith(1035);
    const r = await window.api.branches.list(268);
    expect(r.nodes.some((n) => n.id === r.active_commit_id)).toBe(true);
  });

  it('最大 turn_index 覆盖到最新一回合', async () => {
    await loadClientWith(1035);
    const r = await window.api.branches.list(268);
    expect(Math.max(...r.nodes.map((n) => n.turn_index))).toBe(1035);
  });

  it('拉完后 has_more 归 false,调用方不会以为还有下一页', async () => {
    await loadClientWith(1035);
    const r = await window.api.branches.list(268);
    expect(r.page.has_more).toBe(false);
    expect(r.page.next_cursor).toBeNull();
  });

  it('单页装得下时只发一次请求(不给小存档加开销)', async () => {
    await loadClientWith(120);
    const r = await window.api.branches.list(268);
    expect(r.nodes).toHaveLength(120);
    expect(server).toHaveBeenCalledTimes(1);
  });

  it('refs / save / active_commit_id 取首页(后端不分页这几项)', async () => {
    await loadClientWith(1035);
    const r = await window.api.branches.list(268);
    expect(r.refs).toHaveLength(1);
    expect(r.save.id).toBe(268);
  });
});
