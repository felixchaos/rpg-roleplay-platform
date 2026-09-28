/**
 * mcp-validate.test.js — 新增 MCP 服务器后的校验:按 id 发,并把「启动不了」如实告诉用户。
 * 以前两端都发 validate({name}),后端只认 id → 「未知 MCP 服务器」被 catch 吞掉;
 * 返回 200 但 ready_to_launch=false(命令在服务器上找不到)也被当成成功。
 */
import { describe, it, expect, vi } from 'vitest';
import '../i18n/index.js';
import { validateNewMcpServer, mcpLaunchable } from '../lib/mcp-validate.js';

const api = (resp) => ({ mcp: { validate: vi.fn().mockResolvedValue(resp) } });

describe('validateNewMcpServer', () => {
  it('按 upsert 回的 server_id 校验', async () => {
    const a = api({ ok: true, result: { ready_to_launch: true, transport: 'stdio', command: 'npx' } });
    const v = await validateNewMcpServer(a, '文件系统');
    expect(a.mcp.validate).toHaveBeenCalledWith({ id: '文件系统' });
    expect(v.ok).toBe(true);
  });
  it('命令找不到 → 不算成功,消息里带命令名', async () => {
    const v = await validateNewMcpServer(api({ ok: true, result: { ready_to_launch: false, transport: 'stdio', command: 'npx' } }), 'fs');
    expect(v.ok).toBe(false);
    expect(v.message).toContain('npx');
  });
  it('没有 server_id 就不发请求', async () => {
    const a = api({});
    const v = await validateNewMcpServer(a, undefined);
    expect(a.mcp.validate).not.toHaveBeenCalled();
    expect(v.ok).toBe(false);
  });
  it('mcpLaunchable 只认 ready_to_launch', () => {
    expect(mcpLaunchable({ ok: true, result: { ready_to_launch: false } })).toBe(false);
    expect(mcpLaunchable({ ok: true, result: { ready_to_launch: true } })).toBe(true);
    expect(mcpLaunchable(null)).toBe(false);
  });
});
