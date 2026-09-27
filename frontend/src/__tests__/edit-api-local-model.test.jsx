/**
 * edit-api-local-model.test.jsx — 只收公网 https 的实例拒绝本机/局域网 base_url 的**提示层**回归测试。
 *
 * 群反馈(dali):「本地模型怎么添加 / 提示我一定要 https,但是本地模型不是只有 http」。
 * 真相:开了多用户鉴权的实例,后端 user_credentials._validate_base_url 必拒 http / 本机 /
 * 局域网地址(服务器到不了用户自己机器上的模型服务,换 https 也没用)。以前用户只能在填完
 * API key、点保存之后从一条「服务器模式下 base_url 必须是 https」的 toast 里撞见这件事。
 *
 * 现在 EditApiModal 按 /api/state 的 app.base_url_public_only 提前判定。这个字段与后端
 * require_auth() 同源(就是 _validate_base_url 用的那个谓词),所以:
 *   · flag=true + http:// 或本机/私网 host → base_url 字段直接报错 + 禁用提交,
 *     不管 deployment 串是什么(multiuser、local 加 RPG_REQUIRE_AUTH=1 都一样拦);
 *   · flag=false → 不拦,哪怕 deployment 是 server(server 加 RPG_REQUIRE_AUTH=0 后端放行);
 *   · /api/state 取不到或老后端没这个字段 → 不拦,交给后端权威判定。
 * 后端仍是唯一权威(浏览器解析不了 DNS),这里只锁「不让用户白填一遍」的确定性行为。
 *
 * 提示文案按 i18n 键断言(两条发布线措辞不同,键相同)。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import i18n from '../i18n/index.js';
import { EditApiModal } from '../components/settings/model-modals.jsx';
import { __resetDeploymentModeCache } from '../lib/deployment.js';

const HINT_KEY = 'settings.edit_api.cloud_no_local_model';
// 惰性取:渲染时的语言由 setup.js 设为 zh-CN,断言时与组件的 t() 同源。
const hint = () => i18n.t(HINT_KEY);

/** 让 useEffect 里的判据探测(await /api/state)彻底落地,「不拦」的断言才有意义。 */
async function flush() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

function installState(app) {
  window.api = { game: { state: vi.fn().mockResolvedValue({ app }) } };
}

/** 打开「编辑」态(provider 固定),把 base_url 改成 url,返回取保存按钮的函数。 */
async function renderWithBaseUrl(app, url) {
  installState(app);
  render(
    <EditApiModal
      open
      isNew={false}
      api={{ id: 'custom_relay', name: '本地模型', base_url: 'https://relay.example.com/v1', kind: 'openai_compat' }}
      onClose={() => {}}
      onConfirm={() => {}}
    />,
  );
  // 等判据探测落地(useEffect 里的 await)
  await waitFor(() => expect(window.api.game.state).toHaveBeenCalled());
  await flush();
  const input = document.querySelector('input[placeholder="https://your-relay.example.com/v1"]');
  fireEvent.change(input, { target: { value: url } });
  return () => screen.getByRole('button', { name: '保存' });
}

const PUBLIC_ONLY = { deployment: 'server', base_url_public_only: true };

describe('EditApiModal — 只收公网 https 的实例不接本地模型的提前提示', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.api = undefined;
    __resetDeploymentModeCache();   // 每 case 换判据,不能吃上一 case 的缓存
  });

  it('提示文案有真实翻译(不是裸键)', () => {
    expect(hint()).toBeTruthy();
    expect(hint()).not.toBe(HINT_KEY);
  });

  it.each([
    ['http://127.0.0.1:11434/v1', 'Ollama 本机'],
    ['http://192.168.1.20:1234/v1', 'LM Studio 局域网'],
    ['https://localhost:8080/v1', '换成 https 也一样连不到'],
    ['http://10.0.0.5:8000/v1', '私网 10/8'],
    ['http://172.20.3.4:8000/v1', '私网 172.16/12'],
    ['http://api.example.com/v1', '公网域名但明文 http'],
  ])('flag=true + %s → 报错并禁用保存(%s)', async (url) => {
    const saveBtn = await renderWithBaseUrl(PUBLIC_ONLY, url);
    await waitFor(() => expect(screen.getByText(hint())).toBeInTheDocument());
    expect(saveBtn()).toBeDisabled();
  });

  it.each([
    ['multiuser', '开源 docker-compose 默认模式'],
    ['local', 'local 加 RPG_REQUIRE_AUTH=1'],
  ])('flag=true 且 deployment=%s → 照样拦(%s),判据不看模式串', async (deployment) => {
    const saveBtn = await renderWithBaseUrl({ deployment, base_url_public_only: true }, 'http://127.0.0.1:11434/v1');
    await waitFor(() => expect(screen.getByText(hint())).toBeInTheDocument());
    expect(saveBtn()).toBeDisabled();
  });

  it('flag=true + 公网 https 中转站 → 不拦', async () => {
    const saveBtn = await renderWithBaseUrl(PUBLIC_ONLY, 'https://relay.example.com/v1');
    await waitFor(() => expect(screen.queryByText(hint())).toBeNull());
    expect(saveBtn()).not.toBeDisabled();
  });

  it('flag=false(桌面 / 单用户自部署)+ 本机 http → 不拦(本地模型的正解)', async () => {
    const saveBtn = await renderWithBaseUrl({ deployment: 'desktop', base_url_public_only: false }, 'http://127.0.0.1:11434/v1');
    await waitFor(() => expect(screen.queryByText(hint())).toBeNull());
    expect(saveBtn()).not.toBeDisabled();
  });

  it('flag=false 且 deployment=server(RPG_REQUIRE_AUTH=0)→ 不拦,后端这时也放行', async () => {
    const saveBtn = await renderWithBaseUrl({ deployment: 'server', base_url_public_only: false }, 'http://192.168.1.20:1234/v1');
    await waitFor(() => expect(screen.queryByText(hint())).toBeNull());
    expect(saveBtn()).not.toBeDisabled();
  });

  it('老后端不返回 base_url_public_only → 不拦', async () => {
    const saveBtn = await renderWithBaseUrl({ deployment: 'server' }, 'http://127.0.0.1:11434/v1');
    await waitFor(() => expect(screen.queryByText(hint())).toBeNull());
    expect(saveBtn()).not.toBeDisabled();
  });

  it('/api/state 取不到(抖动)→ 不拦,交给后端权威判定', async () => {
    window.api = { game: { state: vi.fn().mockRejectedValue(new Error('network')) } };
    render(
      <EditApiModal open isNew={false} onClose={() => {}} onConfirm={() => {}}
        api={{ id: 'custom_relay', name: '本地模型', base_url: 'https://relay.example.com/v1', kind: 'openai_compat' }} />,
    );
    await waitFor(() => expect(window.api.game.state).toHaveBeenCalled());
    await flush();
    const input = document.querySelector('input[placeholder="https://your-relay.example.com/v1"]');
    fireEvent.change(input, { target: { value: 'http://127.0.0.1:11434/v1' } });
    await waitFor(() => expect(screen.queryByText(hint())).toBeNull());
    expect(screen.getByRole('button', { name: '保存' })).not.toBeDisabled();
  });

  it('连接方式不再提供「局域网 / 本地」这个纯装饰选项', async () => {
    await renderWithBaseUrl(PUBLIC_ONLY, 'https://relay.example.com/v1');
    expect(screen.queryByText('局域网 / 本地')).toBeNull();
  });
});
