/**
 * api-config-save-resilience.test.jsx — 反馈 #107「本地版配 API 全部失败」的前端一侧。
 *
 * 后端那头:保存凭据后内联拉一次模型列表,以前没上限、还阻塞事件循环,最坏 62s;前端
 * 默认 15s 就超时,于是弹「保存失败」,而后端其实已经把 key 存上了(假失败),列表却一直
 * 显示「还没有配置」,顶栏也跟着掉成「未登录」。这里锁前端的四件事:
 *
 * 1. api-client:探测类请求给够超时(拉模型 / 校验 / 可用性 45s,保存凭据 30s),GET 也能
 *    带 signal;保存凭据网络层失败(结果未知)时照样广播 rpg-credentials-updated。
 * 2. 设置页 onConfirm:
 *    - 只改连接方式(代理)不重填 key 也要落库(走 keep_key 并带 proxy,地址没改就回写原覆盖值);
 *    - 写凭据只有一个 catch,keep_key 路径失败不再冒出「元数据已保存」的假警告
 *      (catalogWritten 门控,F10 第 5 条);
 *    - 失败后回读一次后端真实状态。
 * 3. 首配拦截弹窗的内联供应商卡片:不带 proxy 键(后端据此保留已存代理),失败后回读凭据。
 * 4. __refreshPlatform:auth.me 网络失败 ≠ 未登录,保留原登录态。
 */
import React from 'react';
import { describe, it, expect, beforeAll, beforeEach, afterEach, vi } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { render, waitFor, act } from '@testing-library/react';

// ── 设置页的重依赖换成桩:只关心 onConfirm 的请求与提示 ─────────────────────────
vi.mock('../platform-app.jsx', () => ({
  SettingsToggle: () => null,
  ResizableSplit: ({ top, bottom }) => (<div>{top}{bottom}</div>),
}));
vi.mock('../components/settings/model-list.jsx', () => ({
  ApiDetailPanel: () => null, ApiModelsList: () => null, ModelNameCell: () => null, HealthDot: () => null,
}));
vi.mock('../components/settings/provider-config.jsx', () => ({
  ProviderCard: () => null, ProviderConfigSection: () => null,
}));
vi.mock('../components/settings/model-modals.jsx', () => ({
  EditApiModal: (props) => { globalThis.__editApiProps = props; return null; },
  AddModelModal: () => null, VisibilityModal: () => null, ValidateModal: () => null,
}));
// 首配拦截弹窗:ProviderCard 换成桩,拿到 onSaveKey 直接调;选择器不相关。
vi.mock('../pages/settings.jsx', async () => {
  const catalog = await import('../components/settings/models-catalog.js');
  return {
    ProviderCard: (props) => { globalThis.__providerCardProps = props; return null; },
    PROVIDERS_CONFIG: catalog.PROVIDERS_CONFIG,
    normalizeApiId: catalog.normalizeApiId,
  };
});
vi.mock('../components/AgentModelPicker.jsx', () => ({ default: () => null }));

const apiClientSource = readFileSync(resolve(__dirname, '../api-client.js'), 'utf-8');

// ════════════════════════════════════════════════════════════════════════
// 1. api-client
// ════════════════════════════════════════════════════════════════════════
describe('api-client:探测类请求的超时与结果未知时的广播', () => {
  let realApi;
  beforeAll(() => {
    new Function(apiClientSource).call(window);
    realApi = window.api;
  });
  afterEach(() => { vi.restoreAllMocks(); });

  function okFetch() {
    return vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200, headers: { 'content-type': 'application/json' },
    }));
  }

  it.each([
    ['models.syncRemote', (a) => a.models.syncRemote({ api_id: 'openai' }), 45000],
    ['models.remote(GET)', (a) => a.models.remote({ api_id: 'openai' }), 45000],
    ['models.diff(GET)', (a) => a.models.diff({ api_id: 'openai' }), 45000],
    ['models.report(GET)', (a) => a.models.report({ api_id: 'openai' }), 45000],
    ['models.probe', (a) => a.models.probe({ api_id: 'openai' }), 45000],
    ['models.validate', (a) => a.models.validate({ api_id: 'openai' }), 45000],
    ['credentials.test(GET)', (a) => a.credentials.test({ api_id: 'openai' }), 45000],
    ['credentials.set', (a) => a.credentials.set({ api_id: 'openai', api_key: 'sk' }), 30000],
  ])('%s 用专用超时(不是默认 15s)', async (_name, call, ms) => {
    expect(typeof AbortSignal.timeout).toBe('function');
    const spy = vi.spyOn(AbortSignal, 'timeout');
    window.fetch = okFetch();
    await call(realApi);
    const used = spy.mock.calls.map((c) => c[0]);
    expect(used).toContain(ms);
    // 真正发出去的请求带的是专用 signal,不是默认 15s 那个
    const init = window.fetch.mock.calls[0][1];
    const idx = used.indexOf(ms);
    expect(init.signal).toBe(spy.mock.results[idx].value);
  });

  it('普通 GET 仍是默认 15s', async () => {
    const spy = vi.spyOn(AbortSignal, 'timeout');
    window.fetch = okFetch();
    await realApi.credentials.list();
    expect(spy.mock.calls.map((c) => c[0])).toEqual([15000]);
  });

  it('保存凭据网络层失败(超时)→ 仍广播 rpg-credentials-updated 再抛错', async () => {
    const onUpdated = vi.fn();
    window.addEventListener('rpg-credentials-updated', onUpdated);
    try {
      window.fetch = vi.fn().mockRejectedValue(new DOMException('signal timed out', 'TimeoutError'));
      await expect(realApi.credentials.set({ api_id: 'openai', api_key: 'sk' })).rejects.toMatchObject({ code: 'network' });
      expect(onUpdated).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener('rpg-credentials-updated', onUpdated);
    }
  });

  it('后端明确拒绝(400)→ 什么都没写,不广播', async () => {
    const onUpdated = vi.fn();
    window.addEventListener('rpg-credentials-updated', onUpdated);
    try {
      window.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: false, error: '代理地址格式不对' }), {
        status: 400, headers: { 'content-type': 'application/json' },
      }));
      await expect(realApi.credentials.set({ api_id: 'openai', api_key: 'sk' })).rejects.toMatchObject({ status: 400 });
      expect(onUpdated).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener('rpg-credentials-updated', onUpdated);
    }
  });
});

// ════════════════════════════════════════════════════════════════════════
// 2. 设置页 ModelsSection.onConfirm
// ════════════════════════════════════════════════════════════════════════
function installSettingsApi({ role = 'user', credSet, proxyUrl = '', override = '' } = {}) {
  window.RPG_AUTH = { authed: true, online: true };
  window.MOCK_PLATFORM = { user: { role } };
  window.__apiToast = vi.fn();
  window.__refreshPlatform = vi.fn().mockResolvedValue({});
  window.api = {
    models: {
      list: vi.fn().mockResolvedValue({ models: { apis: [
        { id: 'deepseek', display_name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', models: [] },
      ] } }),
      syncRemote: vi.fn().mockResolvedValue({ ok: true, models: [], synced: 0, remote_total: 0 }),
      upsertApi: vi.fn().mockResolvedValue({ ok: true }),
    },
    credentials: {
      list: vi.fn().mockResolvedValue({ items: [
        { api_id: 'deepseek', has_credential: true, key_hint: 'abcd', base_url_override: override, proxy_url: proxyUrl },
      ] }),
      set: credSet || vi.fn().mockResolvedValue({ ok: true }),
    },
  };
}

async function mountSettings() {
  const { ModelsSection } = await import('../components/settings/models-section.jsx');
  globalThis.__editApiProps = null;
  render(<ModelsSection />);
  // 自动同步发出 = 已配凭据的行已经进了 state,拿到的 onConfirm 闭包里有 existing
  await waitFor(() => expect(window.api.models.syncRemote).toHaveBeenCalled());
  return () => globalThis.__editApiProps.onConfirm;
}

const EDIT = { id: 'deepseek', name: 'DeepSeek', api_key: '', proxy: 'direct', proxy_url: '', no_auth: false };
const toastKinds = () => window.__apiToast.mock.calls.map((c) => (c[1] || {}).kind);

describe('设置页保存凭据', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('只改连接方式(代理)不重填 key → 走 keep_key 并带 proxy,地址回写原覆盖值', async () => {
    installSettingsApi();
    const onConfirm = await mountSettings();
    await act(async () => {
      await onConfirm()({ ...EDIT, base_url: 'https://api.deepseek.com/v1', proxy: 'http_proxy', proxy_url: ' http://127.0.0.1:7890 ' });
    });
    expect(window.api.credentials.set).toHaveBeenCalledTimes(1);
    expect(window.api.credentials.set.mock.calls[0][0]).toEqual({
      api_id: 'deepseek', api_key: '', keep_key: true,
      base_url_override: '',   // 凭据原本没有覆盖地址:不能把目录地址钉成覆盖
      proxy: 'http://127.0.0.1:7890',
    });
  });

  it('代理从 HTTP 改回直连 → keep_key 带空串(清掉已存代理)', async () => {
    installSettingsApi({ proxyUrl: 'http://127.0.0.1:7890' });
    const onConfirm = await mountSettings();
    await act(async () => {
      await onConfirm()({ ...EDIT, base_url: 'https://api.deepseek.com/v1', proxy: 'direct', proxy_url: '' });
    });
    expect(window.api.credentials.set.mock.calls[0][0]).toMatchObject({ keep_key: true, proxy: '' });
  });

  it('什么都没改 → 不发请求', async () => {
    installSettingsApi({ proxyUrl: 'http://127.0.0.1:7890' });
    const onConfirm = await mountSettings();
    await act(async () => {
      await onConfirm()({ ...EDIT, base_url: 'https://api.deepseek.com/v1', proxy: 'http_proxy', proxy_url: 'http://127.0.0.1:7890' });
    });
    expect(window.api.credentials.set).not.toHaveBeenCalled();
  });

  it('普通用户 keep_key 失败 → 只有一条 save_fail,没有「元数据已保存」假警告', async () => {
    const credSet = vi.fn().mockRejectedValue(Object.assign(new Error('base_url 主机无法解析'), { status: 400 }));
    installSettingsApi({ credSet });
    const onConfirm = await mountSettings();
    await act(async () => {
      await onConfirm()({ ...EDIT, base_url: 'https://relay.typo.example/v1' });
    });
    expect(credSet.mock.calls[0][0]).toMatchObject({ keep_key: true, base_url_override: 'https://relay.typo.example/v1' });
    expect(toastKinds()).toEqual(['danger']);
  });

  it('管理员先写了目录、keep_key 再失败 → 这时才提示半截状态', async () => {
    const credSet = vi.fn().mockRejectedValue(new Error('boom'));
    installSettingsApi({ role: 'admin', credSet });
    const onConfirm = await mountSettings();
    await act(async () => {
      await onConfirm()({ ...EDIT, base_url: 'https://relay.example.com/v1' });
    });
    expect(window.api.models.upsertApi).toHaveBeenCalled();
    expect(toastKinds()).toEqual(['warn', 'danger']);
  });

  it('保存超时 → 报失败后回读一次后端(已落库的凭据要能显示出来)', async () => {
    const credSet = vi.fn().mockRejectedValue(Object.assign(new Error('网络异常：signal timed out'), { code: 'network', status: 0 }));
    installSettingsApi({ credSet });
    const onConfirm = await mountSettings();
    const before = window.api.credentials.list.mock.calls.length;
    await act(async () => {
      await onConfirm()({ ...EDIT, base_url: 'https://api.deepseek.com/v1', api_key: 'sk-new' });
    });
    expect(credSet.mock.calls[0][0]).toMatchObject({ api_key: 'sk-new', proxy: '' });
    expect(toastKinds()).toEqual(['danger']);
    expect(window.api.credentials.list.mock.calls.length).toBeGreaterThan(before);
  });
});

// ════════════════════════════════════════════════════════════════════════
// 3. 首配拦截弹窗的内联供应商卡片
// ════════════════════════════════════════════════════════════════════════
describe('首配拦截弹窗:内联保存 key', () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it('不带 proxy 键(保留已存代理);失败后回读凭据,失败提示停留够久', async () => {
    const credSet = vi.fn().mockRejectedValue(Object.assign(new Error('网络异常：signal timed out'), { code: 'network' }));
    window.__apiToast = vi.fn();
    window.api = {
      credentials: { list: vi.fn().mockResolvedValue({ items: [] }), set: credSet },
      models: { upsertApi: vi.fn().mockResolvedValue({}) },
    };
    const { InlineProviderConfig } = await import('../components/ModelConfigInterceptModal.jsx');
    globalThis.__providerCardProps = null;
    render(<InlineProviderConfig capability="llm" defaultApiId="deepseek" />);
    await waitFor(() => expect(window.api.credentials.list).toHaveBeenCalledTimes(1));
    await act(async () => {
      await globalThis.__providerCardProps.onSaveKey('deepseek', ' sk-x ', '');
    });
    expect(credSet).toHaveBeenCalledTimes(1);
    expect('proxy' in credSet.mock.calls[0][0]).toBe(false);
    expect(window.api.credentials.list).toHaveBeenCalledTimes(2);
    const [, opts] = window.__apiToast.mock.calls[0];
    expect(opts).toMatchObject({ kind: 'danger', duration: 9000 });
  });
});

// ════════════════════════════════════════════════════════════════════════
// 4. __refreshPlatform
// ════════════════════════════════════════════════════════════════════════
describe('__refreshPlatform:后端忙 ≠ 掉登录', () => {
  it('auth.me 网络超时 → 保留原登录态,刷新按失败处理', async () => {
    const me = vi.fn().mockResolvedValue({ user: { id: 7, username: 'felix', role: 'admin' } });
    window.api = {
      auth: { me },
      platform: { info: vi.fn().mockResolvedValue({}) },
      scripts: { list: vi.fn().mockResolvedValue([]) },
      saves: { list: vi.fn().mockResolvedValue([]) },
      library: { list: vi.fn().mockResolvedValue({ entries: [] }) },
      game: { state: vi.fn().mockResolvedValue({}) },
    };
    await import('../data-loader.js');
    await waitFor(() => expect(window.RPG_AUTH && window.RPG_AUTH.authed).toBe(true));
    const platformBefore = window.MOCK_PLATFORM;
    const onReady = vi.fn();
    window.addEventListener('rpg-data-ready', onReady);
    try {
      me.mockRejectedValueOnce(Object.assign(new Error('网络异常：signal timed out'), { code: 'network', status: 0 }));
      await expect(window.__refreshPlatform()).rejects.toThrow();
      expect(window.RPG_AUTH.authed).toBe(true);
      expect(window.RPG_AUTH.user_id).toBe(7);
      expect(window.MOCK_PLATFORM).toBe(platformBefore);
      expect(onReady).not.toHaveBeenCalled();

      // 后端明确说没登录(返回无 user)→ 照常切成匿名
      me.mockResolvedValueOnce({ user: null });
      await window.__refreshPlatform();
      expect(window.RPG_AUTH.authed).toBe(false);
    } finally {
      window.removeEventListener('rpg-data-ready', onReady);
    }
  });
});
