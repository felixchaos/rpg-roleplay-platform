/**
 * edit-api-local-model.test.jsx — 云端实例拒绝本机/局域网 base_url 的**提示层**回归测试。
 *
 * 群反馈(dali):「本地模型怎么添加 / 提示我一定要 https,但是本地模型不是只有 http」。
 * 真相:云端服务器根本到不了用户自己机器上的模型服务,换 https 也没用 —— 后端
 * user_credentials._validate_base_url 在 server 模式必拒。以前用户只能在填完 API key、
 * 点保存之后从一条「服务器模式下 base_url 必须是 https」的 toast 里撞见这件事。
 *
 * 现在 EditApiModal 按 /api/state 的 app.deployment 提前判定:
 *   · 云端(server) + http:// 或本机/私网 host → base_url 字段直接报错 + 禁用提交;
 *   · 自部署(desktop/local/self_hosted) → 不拦(本地模型本来就该填 http)。
 * 后端仍是唯一权威(浏览器解析不了 DNS),这里只锁「不让用户白填一遍」的确定性行为。
 */
import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { EditApiModal } from '../components/settings/model-modals.jsx';
import { __resetDeploymentModeCache } from '../lib/deployment.js';

const HINT = /云端版连不到你本机/;

function installState(deployment) {
  window.api = { game: { state: vi.fn().mockResolvedValue({ app: { deployment } }) } };
}

/** 打开「编辑」态(provider 固定),把 base_url 改成 url,返回保存按钮。 */
async function renderWithBaseUrl(deployment, url) {
  installState(deployment);
  render(
    <EditApiModal
      open
      isNew={false}
      api={{ id: 'custom_relay', name: '本地模型', base_url: 'https://relay.example.com/v1', kind: 'openai_compat' }}
      onClose={() => {}}
      onConfirm={() => {}}
    />,
  );
  // 等部署模式探测落地(useEffect 里的 await)
  await waitFor(() => expect(window.api.game.state).toHaveBeenCalled());
  const input = document.querySelector('input[placeholder="https://your-relay.example.com/v1"]');
  fireEvent.change(input, { target: { value: url } });
  return () => screen.getByRole('button', { name: '保存' });
}

describe('EditApiModal — 云端不接本地模型的提前提示', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.api = undefined;
    __resetDeploymentModeCache();   // 每 case 换部署模式,不能吃上一 case 的缓存
  });

  it.each([
    ['http://127.0.0.1:11434/v1', 'Ollama 本机'],
    ['http://192.168.1.20:1234/v1', 'LM Studio 局域网'],
    ['https://localhost:8080/v1', '换成 https 也一样连不到'],
    ['http://10.0.0.5:8000/v1', '私网 10/8'],
    ['http://172.20.3.4:8000/v1', '私网 172.16/12'],
  ])('云端 + %s → 报错并禁用保存(%s)', async (url) => {
    const saveBtn = await renderWithBaseUrl('server', url);
    await waitFor(() => expect(screen.getByText(HINT)).toBeInTheDocument());
    expect(saveBtn()).toBeDisabled();
  });

  it('云端 + 公网 https 中转站 → 不拦', async () => {
    const saveBtn = await renderWithBaseUrl('server', 'https://relay.example.com/v1');
    await waitFor(() => expect(screen.queryByText(HINT)).toBeNull());
    expect(saveBtn()).not.toBeDisabled();
  });

  it('自部署(desktop) + 本机 http → 不拦(本地模型的正解)', async () => {
    const saveBtn = await renderWithBaseUrl('desktop', 'http://127.0.0.1:11434/v1');
    await waitFor(() => expect(screen.queryByText(HINT)).toBeNull());
    expect(saveBtn()).not.toBeDisabled();
  });

  it('部署模式取不到(/api/state 抖动)→ 不拦,交给后端权威判定', async () => {
    window.api = { game: { state: vi.fn().mockRejectedValue(new Error('network')) } };
    render(
      <EditApiModal open isNew={false} onClose={() => {}} onConfirm={() => {}}
        api={{ id: 'custom_relay', name: '本地模型', base_url: 'https://relay.example.com/v1', kind: 'openai_compat' }} />,
    );
    await waitFor(() => expect(window.api.game.state).toHaveBeenCalled());
    const input = document.querySelector('input[placeholder="https://your-relay.example.com/v1"]');
    fireEvent.change(input, { target: { value: 'http://127.0.0.1:11434/v1' } });
    await waitFor(() => expect(screen.queryByText(HINT)).toBeNull());
    expect(screen.getByRole('button', { name: '保存' })).not.toBeDisabled();
  });

  it('连接方式不再提供「局域网 / 本地」这个纯装饰选项', async () => {
    await renderWithBaseUrl('server', 'https://relay.example.com/v1');
    expect(screen.queryByText('局域网 / 本地')).toBeNull();
  });
});
