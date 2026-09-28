/**
 * mcp-edit-form.test.jsx — MCP 服务器「编辑」表单(网页能力页 CapPage / 手机 McpSection)。
 *
 * 后端把表单的整行命令拆成 command + args 落库(npx @modelcontextprotocol/server-filesystem /data
 * → command=npx, args=[包名, /data]),列表里的名字存在 display_name(_normalize_mcp_server 不输出 name)。
 * 以前两端的编辑表单:
 *   - 命令框只回填 command(「npx」),提交也不带 args → 后端收到裸 npx,400「npx 至少需要 1 个参数」;
 *     python3 -m my_mcp 则被悄悄存成裸 python3,服务器再也起不来;
 *   - 列表按 s.name || s.id 取名 → 中文名服务器显示成 mcp-<哈希>,编辑时名称框默认是这个 id,
 *     一保存就把 display_name 覆盖成 id,用户起的名字没了。
 * 两端各锁一遍(孪生奇偶):显示原名、保存带回原名、命令与参数原样还原、改过命令则按整行重拆。
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import '../i18n/index.js';
import { CapPage } from '../components/platform/CapPages.jsx';
import { McpSection } from '../mobile/caps/McpSection.jsx';
import { mcpCommandLine, mcpStdioFields, mcpDisplayName } from '../lib/mcp-form.js';

vi.mock('../mobile/icons.jsx', () => ({ Icon: () => null }));
vi.mock('../game-icons.jsx', () => ({ Icon: () => null }));

// 与后端 _normalize_mcp_server 同口径:给了 args 就用 args,否则按空白拆整行。
function launchLine(body) {
  const args = Array.isArray(body.args) ? body.args : [];
  if (args.length) return [body.command, ...args];
  return String(body.command || '').trim().split(/\s+/).filter(Boolean);
}

const FS = {
  id: 'mcp-42949b7f', display_name: '文件系统', transport: 'stdio',
  command: 'npx', args: ['@modelcontextprotocol/server-filesystem', '/data'],
  env: { ROOT: '/data' }, enabled: true, scope: 'local', url: '', headers: {},
};
const PY = {
  id: 'py-local', display_name: 'py-local', transport: 'stdio',
  command: 'python3', args: ['-m', 'my_mcp', '/Users/me/My Docs'],
  env: {}, enabled: false, scope: 'local', url: '', headers: {},
};

beforeEach(() => {
  window.__apiToast = vi.fn();
  window.api = {
    tools: { list: vi.fn().mockResolvedValue({ tools: { mcp: { servers: [FS, PY] } } }) },
    mcp: {
      runtime: vi.fn().mockResolvedValue({ running: [] }),
      upsert: vi.fn().mockResolvedValue({ ok: true, server_id: 'x' }),
      validate: vi.fn().mockResolvedValue({ ok: true, result: { ready_to_launch: true } }),
      enabled: vi.fn(), start: vi.fn(), stop: vi.fn(), remove: vi.fn(),
    },
  };
});

describe('lib/mcp-form', () => {
  it('显示名取后端的 display_name', () => {
    expect(mcpDisplayName(FS)).toBe('文件系统');
    expect(mcpDisplayName({ id: 'a', name: 'n' })).toBe('n');
    expect(mcpDisplayName({ id: 'a' })).toBe('a');
  });
  it('命令框回填整行,没改就原样带回 command + args(参数里的空格不被重拆)', () => {
    expect(mcpCommandLine(FS)).toBe('npx @modelcontextprotocol/server-filesystem /data');
    expect(mcpStdioFields(mcpCommandLine(PY), PY)).toEqual({ command: 'python3', args: ['-m', 'my_mcp', '/Users/me/My Docs'] });
  });
  it('改过命令 / 新增 → 只发整行,由后端拆', () => {
    expect(mcpStdioFields(' python3 -m other ', PY)).toEqual({ command: 'python3 -m other' });
    expect(mcpStdioFields('npx pkg', null)).toEqual({ command: 'npx pkg' });
  });
});

describe('网页能力页 CapPage(kind=mcp)编辑', () => {
  async function openEdit(label) {
    render(<CapPage kind="mcp" />);
    const title = await screen.findByText(label);
    const card = title.closest('.pl-cap');
    fireEvent.click(card.querySelector('[data-tip="编辑"]'));
    return card;
  }

  it('列表显示用户起的名字,不显示派生 id', async () => {
    render(<CapPage kind="mcp" />);
    expect(await screen.findByText('文件系统')).toBeInTheDocument();
    expect(screen.queryByText('mcp-42949b7f')).toBe(null);
  });

  it('不改命令直接保存:原名 + 完整命令参数都带回', async () => {
    const card = await openEdit('文件系统');
    const cmd = within(card).getByLabelText('命令');
    expect(cmd.value).toBe('npx @modelcontextprotocol/server-filesystem /data');
    expect(within(card).getByLabelText('名称').value).toBe('文件系统');
    await act(async () => { fireEvent.click(within(card).getByText('保存')); });
    await waitFor(() => expect(window.api.mcp.upsert).toHaveBeenCalledTimes(1));
    const body = window.api.mcp.upsert.mock.calls[0][0];
    expect(body.id).toBe('mcp-42949b7f');
    expect(body.name).toBe('文件系统');
    expect(launchLine(body)).toEqual(['npx', '@modelcontextprotocol/server-filesystem', '/data']);
  });

  it('python3 服务器:参数不被清空,带空格的参数原样保留', async () => {
    const card = await openEdit('py-local');
    await act(async () => { fireEvent.click(within(card).getByText('保存')); });
    await waitFor(() => expect(window.api.mcp.upsert).toHaveBeenCalledTimes(1));
    const body = window.api.mcp.upsert.mock.calls[0][0];
    expect(launchLine(body)).toEqual(['python3', '-m', 'my_mcp', '/Users/me/My Docs']);
    expect(body.enabled).toBe(false);
  });

  it('改了命令:按新整行提交', async () => {
    const card = await openEdit('py-local');
    fireEvent.change(within(card).getByLabelText('命令'), { target: { value: 'python3 -m other_mcp' } });
    await act(async () => { fireEvent.click(within(card).getByText('保存')); });
    await waitFor(() => expect(window.api.mcp.upsert).toHaveBeenCalledTimes(1));
    expect(launchLine(window.api.mcp.upsert.mock.calls[0][0])).toEqual(['python3', '-m', 'other_mcp']);
  });
});

describe('手机 McpSection 编辑', () => {
  async function openEdit(label) {
    const toast = vi.fn();
    render(<McpSection toast={toast} />);
    const title = await screen.findByText(label);
    let row = title;
    while (row && !within(row).queryByText('编辑')) row = row.parentElement;
    fireEvent.click(within(row).getByText('编辑'));
    return toast;
  }

  it('列表显示用户起的名字,不显示派生 id', async () => {
    render(<McpSection toast={vi.fn()} />);
    expect(await screen.findByText('文件系统')).toBeInTheDocument();
    expect(screen.queryByText('mcp-42949b7f')).toBe(null);
  });

  it('不改命令直接保存:原名 + 完整命令参数都带回', async () => {
    await openEdit('文件系统');
    expect(await screen.findByDisplayValue('npx @modelcontextprotocol/server-filesystem /data')).toBeInTheDocument();
    expect(screen.getByDisplayValue('文件系统')).toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByText('保存')); });
    await waitFor(() => expect(window.api.mcp.upsert).toHaveBeenCalledTimes(1));
    const body = window.api.mcp.upsert.mock.calls[0][0];
    expect(body.id).toBe('mcp-42949b7f');
    expect(body.name).toBe('文件系统');
    expect(launchLine(body)).toEqual(['npx', '@modelcontextprotocol/server-filesystem', '/data']);
  });

  it('python3 服务器:参数不被清空;停用中的服务器编辑后仍是停用', async () => {
    await openEdit('py-local');
    await screen.findByDisplayValue('python3 -m my_mcp /Users/me/My Docs');
    await act(async () => { fireEvent.click(screen.getByText('保存')); });
    await waitFor(() => expect(window.api.mcp.upsert).toHaveBeenCalledTimes(1));
    const body = window.api.mcp.upsert.mock.calls[0][0];
    expect(launchLine(body)).toEqual(['python3', '-m', 'my_mcp', '/Users/me/My Docs']);
    expect(body.enabled).toBe(false);
  });

  it('http 服务器:手机表单没有 Headers 栏,编辑保存不把原 Headers 清掉', async () => {
    const WEB = { id: 'remote', display_name: '远程', transport: 'http', command: '', args: [], env: {},
      enabled: true, url: 'https://mcp.example.com', headers: { Authorization: 'Bearer t' } };
    window.api.tools.list.mockResolvedValue({ tools: { mcp: { servers: [WEB] } } });
    await openEdit('远程');
    await screen.findByDisplayValue('https://mcp.example.com');
    await act(async () => { fireEvent.click(screen.getByText('保存')); });
    await waitFor(() => expect(window.api.mcp.upsert).toHaveBeenCalledTimes(1));
    const body = window.api.mcp.upsert.mock.calls[0][0];
    expect(body.url).toBe('https://mcp.example.com');
    expect(body.headers).toEqual({ Authorization: 'Bearer t' });
    expect(body.enabled).toBe(true);
  });

  it('改了命令:按新整行提交', async () => {
    await openEdit('py-local');
    const input = await screen.findByDisplayValue('python3 -m my_mcp /Users/me/My Docs');
    fireEvent.change(input, { target: { value: 'python3 -m other_mcp' } });
    await act(async () => { fireEvent.click(screen.getByText('保存')); });
    await waitFor(() => expect(window.api.mcp.upsert).toHaveBeenCalledTimes(1));
    expect(launchLine(window.api.mcp.upsert.mock.calls[0][0])).toEqual(['python3', '-m', 'other_mcp']);
  });
});
