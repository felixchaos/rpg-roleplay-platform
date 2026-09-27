/**
 * tavern-chat-run.test.js — 酒馆 SSE 状态机收口(lib/tavern-chat-run.js)回归测试。
 *
 * 这是「全语义统一」蓝图里最高风险的一处(三宿主 startRun/applyState 合一,合错=所有
 * 酒馆聊天一起挂)。本测试锁住公共骨架的折叠语义逐字不变:
 *   · 七个 on_* handler 折叠(token 追加、reasoning 挂 _thinking、tool_* 挂 _toolOps、
 *     done 收尾 + applyState、status no-op)
 *   · run-id 守卫:旧 run 的事件被 isCurrentRun() 拒绝(被新 run superseded 后)
 *   · 120s idle 超时回调
 *   · restoreFailedDraft:openedAssistant 前失败 → 弹回输入框 + 撤用户气泡;之后则不撤
 *   · 两种 tool-op 模型(inline anchor / inline 无 anchor / flush)各自折叠正确
 *   · applyTavernState 核心三段 + 宿主叠加(setGameState/setPermission/setSystemPrompt/mapHistory)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import {
  startTavernRun, applyTavernState, makeStopRun, abortRun, nowHHMM,
  toolCallInlineAnchor, toolResultInline, toolCallInline,
} from '../lib/tavern-chat-run.js';

/* 一个可驱动的假 SSE:捕获 handlers,暴露 .emit(event, data) 与 .stop()。 */
function makeFakeChat() {
  const calls = [];
  const chat = vi.fn((body, handlers) => {
    const h = { stop: vi.fn(), _handlers: handlers, _body: body };
    calls.push(h);
    return h;
  });
  return { chat, calls };
}

/* 收口一个最小 setHistory 状态机(模拟 React useState 的函数式更新)。 */
function makeHistory(initial = []) {
  let h = initial;
  const setHistory = (fn) => { h = typeof fn === 'function' ? fn(h) : fn; };
  return { setHistory, get: () => h };
}

function baseCfg(over = {}) {
  const hist = makeHistory();
  const rc = { stopped: false, sse: null, runId: 0, inactivityTimer: null };
  const calls = {
    running: [], hasError: [], lastPlayer: [], text: [], toast: [],
  };
  const cfg = {
    rc,
    saveId: 7,
    model: 'm1',
    playerText: 'hello',
    applyState: vi.fn(),
    setHistory: hist.setHistory,
    setRunning: (v) => calls.running.push(v),
    setText: (v) => calls.text.push(typeof v === 'function' ? v('') : v),
    setHasError: (v) => calls.hasError.push(v),
    setLastPlayerText: (v) => calls.lastPlayer.push(v),
    toast: (title, o) => calls.toast.push({ title, ...o }),
    reloadList: vi.fn(),
    ts: '12:00',
    ...over,
  };
  return { cfg, hist, rc, calls };
}

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('startTavernRun — 公共骨架折叠语义', () => {
  it('saveId 为空 → toast 提示 + 返回 null,不发流', () => {
    const fake = makeFakeChat();
    const { cfg, calls } = baseCfg({ saveId: null, api: { game: { chat: fake.chat, stop: vi.fn() } } });
    const r = startTavernRun(cfg);
    expect(r).toBe(null);
    expect(fake.chat).not.toHaveBeenCalled();
    expect(calls.toast[0]).toMatchObject({ code: 'pick_chat' });
  });

  it('提交即追加用户气泡 + setRunning(true) + chat body 带 model/save_id', () => {
    const fake = makeFakeChat();
    const { cfg, hist, calls } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    startTavernRun(cfg);
    expect(hist.get()).toEqual([{ role: 'user', content: 'hello', ts: '12:00' }]);
    expect(calls.running).toEqual([true]);
    expect(calls.lastPlayer).toEqual(['hello']);
    expect(fake.calls[0]._body).toMatchObject({ message: 'hello', text: 'hello', model: 'm1', save_id: 7 });
  });

  it('on_token 追加正文(首 token 开 assistant 气泡,后续 append)', () => {
    const fake = makeFakeChat();
    const { cfg, hist, api } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    void api;
    startTavernRun(cfg);
    const H = fake.calls[0]._handlers;
    H.on_token({ text: 'AA' });
    H.on_token({ delta: 'BB' });
    const last = hist.get()[hist.get().length - 1];
    expect(last).toMatchObject({ role: 'assistant', content: 'AABB', streaming: true });
  });

  it('on_reasoning 挂 _thinking(累积)', () => {
    const fake = makeFakeChat();
    const { cfg, hist } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    startTavernRun(cfg);
    const H = fake.calls[0]._handlers;
    H.on_reasoning({ text: '想一' });
    H.on_reasoning({ delta: '想二' });
    const last = hist.get()[hist.get().length - 1];
    expect(last._thinking).toBe('想一想二');
    expect(last.role).toBe('assistant');
  });

  it('on_status 是 no-op(不改 history)', () => {
    const fake = makeFakeChat();
    const { cfg, hist } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    startTavernRun(cfg);
    const before = hist.get();
    fake.calls[0]._handlers.on_status({ anything: 1 });
    expect(hist.get()).toBe(before);
  });

  it('on_done 收尾:封 streaming_done + applyState(payload.status) + reloadList', () => {
    const fake = makeFakeChat();
    const { cfg, hist } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    startTavernRun(cfg);
    const H = fake.calls[0]._handlers;
    H.on_token({ text: 'hi' });
    H.on_done({ status: { save_id: 7 } });
    const last = hist.get()[hist.get().length - 1];
    expect(last).toMatchObject({ streaming: false, streaming_done: true });
    expect(cfg.applyState).toHaveBeenCalledWith({ save_id: 7 });
    expect(cfg.reloadList).toHaveBeenCalled();
  });

  it('on_done 无 payload → 二次拉 state(api.game.state)', async () => {
    const state = vi.fn(() => Promise.resolve({ save_id: 7 }));
    const fake = makeFakeChat();
    const { cfg } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn(), state } } });
    startTavernRun(cfg);
    const H = fake.calls[0]._handlers;
    H.on_token({ text: 'hi' });
    H.on_done({});
    expect(state).toHaveBeenCalled();
  });

  it('on_done 空回复(从未 openedAssistant)→ 撤用户气泡 + 弹回输入框 + 空回复 toast', () => {
    const fake = makeFakeChat();
    const { cfg, hist, calls } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    startTavernRun(cfg);
    fake.calls[0]._handlers.on_done({});
    expect(hist.get()).toEqual([]);                 // 用户气泡被撤
    expect(calls.text).toContain('hello');          // 弹回输入框
    expect(calls.toast.some((t) => t.code === 'empty')).toBe(true);
  });

  it('on_done 无 token 但存档已落库本轮回复 → 回查兜底渲染,不误报空回复(修「要刷新才出响应」)', async () => {
    const fake = makeFakeChat();
    const persisted = { history: [{ role: 'user', content: 'hello' }, { role: 'assistant', content: '落库的回复' }] };
    const stateFn = vi.fn(() => Promise.resolve(persisted));
    const { cfg, calls } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn(), state: stateFn } } });
    startTavernRun(cfg);
    fake.calls[0]._handlers.on_done({});      // 整轮无 on_token(provider 非增量流式 / SSE 丢事件)
    await Promise.resolve(); await Promise.resolve();   // flush api.game.state().then
    expect(stateFn).toHaveBeenCalled();
    expect(cfg.applyState).toHaveBeenCalledWith(persisted);  // 用存档兜底渲染本轮回复
    expect(calls.toast.some((t) => t.code === 'empty')).toBe(false);  // 不误报空回复
    expect(calls.text).not.toContain('hello');  // 本轮其实成功,不撤回/弹回输入
  });

  it('idle 120s 超时:停流 + 撤草稿 + setRunning(false) + idle toast', () => {
    const fake = makeFakeChat();
    const stop = vi.fn();
    const { cfg, calls } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    startTavernRun(cfg);
    fake.calls[0].stop = stop;        // 让 rc.sse.stop 可观测
    cfg.rc.sse.stop = stop;
    vi.advanceTimersByTime(120000);
    expect(stop).toHaveBeenCalledWith('idle_timeout');
    expect(calls.running).toContain(false);
    expect(calls.toast.some((t) => t.code === 'idle')).toBe(true);
  });

  it('run-id 守卫:旧 run 被新 run superseded 后,旧 handler 的事件被忽略', () => {
    const fake = makeFakeChat();
    const { cfg, hist } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    startTavernRun(cfg);
    const oldH = fake.calls[0]._handlers;
    // 同一 rc 再发一轮 → bump runId,旧 run 不再是 current。
    startTavernRun({ ...cfg, playerText: 'world' });
    oldH.on_token({ text: 'STALE' });
    const flat = JSON.stringify(hist.get());
    expect(flat).not.toContain('STALE');
  });

  it('doneEmptyMsg 覆盖空回复文案(MobileTavern 形态)', () => {
    const fake = makeFakeChat();
    const { cfg, calls } = baseCfg({
      api: { game: { chat: fake.chat, stop: vi.fn() } },
      doneEmptyMsg: (interrupted) => (interrupted ? 'X' : '本轮没有收到回复,请重试。'),
    });
    startTavernRun(cfg);
    fake.calls[0]._handlers.on_done({});
    expect(calls.hasError).toContain('本轮没有收到回复,请重试。');
  });
});

describe('失败轮(on_error 之后的 done)—— 空回合分诊文案不被盖掉,思考 / 工具空气泡随本轮撤回', () => {
  const EMPTY_ERR = { message: '模型把这一轮的输出额度全用在了思考上,正文还没开始写就到顶了。', kind: 'empty_response', reason: 'reasoning_exhausted' };

  it('只有思考流就报错:撤掉思考空气泡 + 玩家气泡、回填输入、置失败标记;done 不再 applyState / 不弹第二个 toast', () => {
    const fake = makeFakeChat();
    const state = vi.fn(() => Promise.resolve({ history: [] }));
    const prior = [{ role: 'user', content: '继续' }, { role: 'assistant', content: '上一轮的回复' }];
    const hist = makeHistory(prior);
    const { cfg, calls, rc } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn(), state } }, playerText: '继续' });
    cfg.setHistory = hist.setHistory;
    startTavernRun(cfg);
    const H = fake.calls[0]._handlers;
    H.on_reasoning({ text: '让我想想……' });
    expect(hist.get()[hist.get().length - 1]).toMatchObject({ role: 'assistant', content: '', _thinking: '让我想想……' });
    H.on_error(EMPTY_ERR);
    H.on_done({ status: { history: prior }, interrupted: false, empty: true });
    expect(hist.get()).toEqual(prior);                   // 只剩上一轮,空气泡与本轮玩家气泡都撤了
    expect(calls.text).toContain('继续');                // 输入回填
    expect(rc.lastRunFailedUnpersisted).toBe(true);      // 「重试」据此跳过 rollback,不吃上一个好回合
    expect(cfg.applyState).not.toHaveBeenCalled();
    expect(state).not.toHaveBeenCalled();
    expect(calls.hasError[calls.hasError.length - 1]).toBe(EMPTY_ERR.message);
    expect(calls.toast.map((x) => x.code)).toEqual(['gen_failed']);
  });

  it('只有工具折叠就报错:工具空气泡同样撤回', () => {
    const fake = makeFakeChat();
    const { cfg, hist } = baseCfg({
      api: { game: { chat: fake.chat, stop: vi.fn() } },
      onToolCall: (data, ctx) => toolCallInline(data, ctx),
    });
    startTavernRun(cfg);
    const H = fake.calls[0]._handlers;
    H.on_tool_call({ tool: 'kb_search' });
    expect(hist.get()).toHaveLength(2);
    H.on_error({ message: '模型这一轮只调用了工具(1 次),没有接着写正文。可以直接重试。', kind: 'empty_response', reason: 'tool_only' });
    H.on_done({ empty: true });
    expect(hist.get()).toEqual([]);
  });

  it('没开过气泡就报错(首回合):done 不再补「空回复」toast、不改写横幅、不回查存档', () => {
    const fake = makeFakeChat();
    const state = vi.fn(() => Promise.resolve({ history: [] }));
    const { cfg, calls, hist } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn(), state } } });
    startTavernRun(cfg);
    const H = fake.calls[0]._handlers;
    H.on_error(EMPTY_ERR);
    H.on_done({ empty: true });
    expect(hist.get()).toEqual([]);
    expect(state).not.toHaveBeenCalled();
    expect(calls.toast.map((x) => x.code)).toEqual(['gen_failed']);
    expect(calls.hasError[calls.hasError.length - 1]).toBe(EMPTY_ERR.message);
  });

  it('已出正文后报错:正文气泡保留(只封口),横幅不被「已中断,已恢复你的输入」覆盖', () => {
    const fake = makeFakeChat();
    const { cfg, calls, hist, rc } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    startTavernRun(cfg);
    const H = fake.calls[0]._handlers;
    H.on_token({ text: '她推开门' });
    H.on_error({ message: '上游 502,请重试' });
    H.on_done({ interrupted: true });
    const last = hist.get()[hist.get().length - 1];
    expect(last).toMatchObject({ role: 'assistant', content: '她推开门', streaming: false, streaming_done: true });
    expect(rc.lastRunFailedUnpersisted).toBe(false);
    expect(calls.hasError[calls.hasError.length - 1]).toBe('上游 502,请重试');
    expect(calls.toast.map((x) => x.code)).toEqual(['gen_failed']);
  });

  it('只有思考流时被服务端中断(无 error):思考空气泡撤回并恢复输入', () => {
    const fake = makeFakeChat();
    const { cfg, calls, hist } = baseCfg({ api: { game: { chat: fake.chat, stop: vi.fn() } } });
    startTavernRun(cfg);
    const H = fake.calls[0]._handlers;
    H.on_reasoning({ text: '想' });
    H.on_done({ interrupted: true });
    expect(hist.get()).toEqual([]);
    expect(calls.text).toContain('hello');
    expect(calls.toast.some((x) => x.code === 'interrupted')).toBe(true);
  });
});

describe('失败轮收尾守卫:两个回合 SSE 缝(game-console / tavern-chat-run)同款', () => {
  const read = (rel) => readFileSync(resolve(__dirname, '..', rel), 'utf-8');

  it.each(['entries/game-console.jsx', 'lib/tavern-chat-run.js'])('%s:on_error 记下 gotError,on_done 据此跳过「空回复」兜底', (rel) => {
    const src = read(rel);
    const onError = src.slice(src.indexOf('on_error: (data) => {'));
    expect(onError.slice(0, 200)).toMatch(/gotError = true/);
    const onDone = src.slice(src.indexOf('on_done: (data) => {'), src.indexOf('on_error: (data) => {'));
    const guard = onDone.search(/if \([^)]*gotError[^)]*\)/);
    const emptyBranch = onDone.search(/if \(!openedAssistant( && !gotReceipt)?\) \{/);
    expect(guard).toBeGreaterThan(-1);
    expect(emptyBranch).toBeGreaterThan(guard);
  });
});

describe('tool-op 折叠模型', () => {
  function ctxFor(hist, opened = false) {
    let _opened = opened;
    return {
      ctx: { setHistory: hist.setHistory, ts: '12:00', isOpened: () => _opened, markOpened: () => { _opened = true; } },
    };
  }

  it('inline anchor(tavern-app):op 带 anchor=触发时正文长度', () => {
    // assistant 已开(opened=true,与 hook 内 openedAssistant 同步)→ op 挂到该气泡,anchor=正文长度。
    const hist = makeHistory([{ role: 'assistant', content: 'ABCD', ts: 't', streaming: true }]);
    const { ctx } = ctxFor(hist, true);
    toolCallInlineAnchor({ tool: 'roll', args: { n: 1 } }, ctx);
    const ops = hist.get()[0]._toolOps;
    expect(ops[0]).toMatchObject({ tool: 'roll', anchor: 4, _pending: true });
  });

  it('inline 无 anchor(MobileTavern):op 不带 anchor', () => {
    const hist = makeHistory();
    const { ctx } = ctxFor(hist);
    toolCallInline({ tool: 'roll' }, ctx);
    const last = hist.get()[hist.get().length - 1];
    expect(last._toolOps[0]).toMatchObject({ tool: 'roll', _pending: true });
    expect(last._toolOps[0].anchor).toBeUndefined();
  });

  it('toolResultInline 回填最后一个 _pending op', () => {
    const hist = makeHistory([{ role: 'assistant', content: '', ts: 't', streaming: true, _toolOps: [{ tool: 'a', _pending: true }] }]);
    toolResultInline({ ok: true, result_snippet: 'done' }, { setHistory: hist.setHistory, ts: 't', isOpened: () => true, markOpened: () => {} });
    const op = hist.get()[0]._toolOps[0];
    expect(op).toMatchObject({ ok: true, result: 'done', _pending: false });
  });
});

describe('applyTavernState — 核心三段 + 宿主叠加', () => {
  it('核心:character / history / activeChat 回填', () => {
    const out = {};
    applyTavernState(
      { tavern: { character: { name: '蕾穆' } }, history: [{ role: 'user', content: 'x' }], save_id: 5, save_title: 'T' },
      {
        setCharacter: (v) => { out.char = v; },
        setPersona: (v) => { out.persona = v; },
        setHistory: (v) => { out.history = v; },
        setActiveChat: (fn) => { out.chat = fn(null); },
        api: { cards: { myGet: vi.fn() } },
      },
    );
    expect(out.char).toEqual({ name: '蕾穆' });
    expect(out.history).toEqual([{ role: 'user', content: 'x' }]);
    expect(out.chat).toMatchObject({ id: 5, title: 'T', character_name: '蕾穆' });
  });

  it('persona_card_id 存在 → 拉全卡回填(成功用全卡)', async () => {
    const out = {};
    // #76 修复后:persona 走 api.me.personas.get(id),解包 (r.persona || r)。
    const get = vi.fn(() => Promise.resolve({ persona: { id: 9, name: 'P' } }));
    applyTavernState(
      { tavern: { persona_card_id: 9 }, player: { name: '投影' } },
      { setPersona: (v) => { out.persona = v; }, api: { me: { personas: { get } } } },
    );
    await Promise.resolve();
    await Promise.resolve();
    expect(get).toHaveBeenCalledWith(9);
    expect(out.persona).toEqual({ id: 9, name: 'P' });
  });

  it('宿主叠加:setGameState/setPermission/setSystemPrompt/mapHistory', () => {
    const out = {};
    applyTavernState(
      { permissions: { mode: 'restricted' }, tavern: { system_prompt: 'SYS' }, history: [{ role: 'assistant', content: 'a', tool_ops: [{ tool: 't' }] }] },
      {
        setGameState: (v) => { out.gs = v; },
        setPermission: (v) => { out.perm = v; },
        setSystemPrompt: (v) => { out.sys = v; },
        setHistory: (v) => { out.history = v; },
        mapHistory: (raw) => raw.map((m) => ({ ...m, _toolOps: m.tool_ops })),
      },
    );
    expect(out.gs).toBeTruthy();
    expect(out.perm).toBe('restricted');
    expect(out.sys).toBe('SYS');
    expect(out.history[0]._toolOps).toEqual([{ tool: 't' }]);
  });
});

describe('makeStopRun / abortRun', () => {
  it('makeStopRun:bump runId + 停 sse + setRunning(false) + onStopExtra', () => {
    const rc = { stopped: false, sse: { stop: vi.fn() }, runId: 3, inactivityTimer: null };
    const running = [];
    const extra = vi.fn();
    const stop = makeStopRun(rc, (v) => running.push(v), { game: { stop: vi.fn() } }, extra);
    stop();
    expect(rc.stopped).toBe(true);
    expect(rc.runId).toBe(4);
    expect(rc.sse).toBe(null);
    expect(running).toEqual([false]);
    expect(extra).toHaveBeenCalled();
  });

  it('abortRun:标记 stopped + 停在途流', () => {
    const sse = { stop: vi.fn() };
    const rc = { stopped: false, sse, runId: 1, inactivityTimer: null };
    abortRun(rc, 'unmount');
    expect(rc.stopped).toBe(true);
    expect(sse.stop).toHaveBeenCalledWith('unmount');
    expect(rc.sse).toBe(null);
  });
});

describe('nowHHMM', () => {
  it('无 __fmt 时回退 HH:MM 零填充', () => {
    const saved = window.__fmt;
    delete window.__fmt;
    expect(/^\d{2}:\d{2}$/.test(nowHHMM())).toBe(true);
    window.__fmt = saved;
  });
});
