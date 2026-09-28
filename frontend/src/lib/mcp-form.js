// mcp-form.js — MCP 服务器列表 / 编辑表单的字段口径(web 能力页 CapPages / 手机 McpSection 共用)。
//
// 后端 _normalize_mcp_server(rpg/tools_dsl/tool_registry.py)的输出:
//   - 名字只在 display_name(不输出 name)。中文名服务器的 id 是 mcp-<哈希>,按 s.name || s.id 取名
//     就会显示成哈希,编辑时名称框默认值也是它 —— 一保存就把原名覆盖掉。
//   - 表单整行命令被拆成 command + args 落库。编辑表单只回填 command、提交又不带 args,
//     npx 服务器变成裸 npx(后端 400),python3 -m pkg 变成裸 python3(存进去了,但起不来)。

export function mcpDisplayName(server) {
  const s = server || {};
  return s.display_name || s.name || s.id || s.server_id || '';
}

// 编辑表单命令框的回填值:command 与 args 合回一整行。
export function mcpCommandLine(raw) {
  const r = raw || {};
  const args = Array.isArray(r.args) ? r.args : [];
  return [r.command, ...args].map((x) => (x == null ? '' : String(x))).filter(Boolean).join(' ');
}

// stdio 服务器提交时的命令字段。
// 命令框没改过(与回填值一致)→ 原样带回 command + args,参数里带空格(路径)也不会被重拆;
// 改过或新增 → 只发整行,由后端按空白拆成 command + args 并照常做白名单 / 参数校验。
export function mcpStdioFields(line, raw) {
  const text = String(line || '').trim();
  const rawArgs = raw && Array.isArray(raw.args) ? raw.args.map(String) : [];
  if (raw && rawArgs.length && text === mcpCommandLine(raw)) {
    return { command: String(raw.command || ''), args: rawArgs };
  }
  return { command: text };
}
