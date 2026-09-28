// mcp-validate.js — 新增 MCP 服务器后的校验(web 能力页 / 手机 MCP 页共用)。
//
// /api/mcp/server/validate 只认 id。以前两端新增后都发 validate({name}):id 为空 → 后端
// 「未知 MCP 服务器」→ 前端 catch 吞掉,校验从来没跑过;就算跑了,返回的 ready_to_launch
// 也没人看 —— 命令在服务器上找不到时用户照样看到「已添加」。现在用 upsert 返回的 server_id
// 去校验,并把「启动不了」的原因如实告诉用户。
import i18n from '../i18n';

export function mcpLaunchable(resp) {
  return !!(resp && resp.ok !== false && resp.result && resp.result.ready_to_launch);
}

// 返回 { ok, message }:ok=能启动;否则 message 是给用户看的原因。
export async function validateNewMcpServer(api, serverId) {
  if (!serverId) return { ok: false, message: i18n.t('mobile.caps.mcp.toast.validate_failed', { msg: 'server_id' }) };
  let resp;
  try {
    resp = await api.mcp.validate({ id: serverId });
  } catch (e) {
    return { ok: false, message: i18n.t('mobile.caps.mcp.toast.validate_failed', { msg: e?.message || '' }) };
  }
  if (mcpLaunchable(resp)) return { ok: true, message: i18n.t('mobile.caps.mcp.toast.validate_ok') };
  const r = (resp && resp.result) || {};
  const message = r.transport === 'http'
    ? i18n.t('mobile.caps.mcp.toast.not_ready_http', { url: r.url || '' })
    : i18n.t('mobile.caps.mcp.toast.not_ready_cmd', { cmd: r.command || '' });
  return { ok: false, message };
}
