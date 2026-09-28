---
title: "MCP Tools"
description: "The MCP page manages your configured MCP (Model Context Protocol) servers. MCP is a standard protocol that lets the GM call external tools — connecting to local command-line programs or remote HTTP services to add custom capabilities such as file reading, API queries, and search."
---

The MCP page manages your configured MCP (Model Context Protocol) servers. MCP is a standard protocol that lets the GM call external tools, supporting both local command-line programs and remote HTTP services. It can extend the GM with capabilities such as reading files, querying APIs, and executing searches.

Entry point: "MCP" in the left navigation bar (route: `#mcp`). On mobile, switch to the "MCP" tab under "Capabilities & Feedback".

---

## Key Concepts

### Transport Types

Each MCP server requires a transport type:

- **stdio · Local command**: Launches a subprocess on the current machine and communicates via standard input/output. Enter the whole command line, e.g. `npx @modelcontextprotocol/server-filesystem /data`. For safety only `npx` / `node` / `python` may be used (no arbitrary shell commands), and `npx` only accepts official `@modelcontextprotocol/` packages or simple package names. The server is validated right after it is added, and you are told if the command can't be found on the server.
- **http · Remote HTTP**: Connects to a running MCP HTTP service at a remote URL (e.g., `https://host:port`).

### Connection Status

The status label at the bottom of each server card has three states:

- **Connected**: The server is enabled and the platform has confirmed it is running.
- **Not connected**: The server is enabled but cannot be reached (process not started or network unreachable).
- **Disabled**: The server is configured but its toggle is off; the GM will not call its tools.

---

## Common Tasks

### Adding an MCP Server

1. Go to the MCP page and click "Add Server" in the top-right.
2. Enter a "Name" (display name, required).
3. Select a transport type: "stdio · Local command" or "http · Remote HTTP".
4. Enter the "Command / URL" (required): for stdio, the launch command; for http, the service URL.
5. Optionally, fill in "Environment Variables / Headers" — one entry per line in `KEY=VALUE` format.
6. Click "Validate & Enable". The platform saves the configuration and attempts a connection check.

### Enabling or Disabling an MCP Server

Find the server card and toggle the switch in the top-right corner of the card.

- On enable, the platform automatically attempts to start the process (stdio) or establish a connection (http).
- On disable, the platform stops the server process and the GM will no longer use its tools.

### Editing an MCP Server

Click the edit button in the bottom-right of the card to modify the name, transport type, command/URL, environment variables, or Headers. Click "Save" to apply changes.

### Deleting an MCP Server

Click the delete button in the bottom-right of the card and confirm. The configuration is permanently removed and cannot be recovered.

### Viewing Runtime Logs

Click the logs button in the bottom-right of the card to view the server's runtime state (process PID and recent stderr output). Click "Export" to download the log as a text file. Admin users see the full stderr; regular users may see only a status summary.

### Batch Validation

Click the "Validate" button in the top-right of the page. The platform checks each enabled server's connection in sequence and shows a toast with the success and failure counts when done.

---

## FAQ

**The GM isn't calling MCP tools?**
Check all of the following: ① the server toggle is on; ② the card status shows "Connected" rather than "Not connected"; ③ the model in use supports tool calling (some lightweight models do not); ④ for stdio mode, the required command (e.g., `npx`) is installed on the local machine and executable.

**Status stays "Not connected" after adding the server?**
For stdio: verify the command runs successfully in a terminal. For http: confirm the target URL is reachable from the server side and is not blocked by a firewall. Click the "Validate" button to retry, or check the logs for specific error messages.

**How do I format environment variables?**
One entry per line in `KEY=VALUE` format, for example:
```
API_KEY=sk-xxxxx
BASE_URL=https://api.example.com
```
For http mode, request headers can be provided in the "Headers" field as JSON, for example `{"Authorization":"Bearer xxx"}`.

---

## Related

- [Settings · Models](/en/settings-models) — Configure models with tool-calling capability and API credentials
- [Modules](/en/modules) — Configure built-in platform modules
