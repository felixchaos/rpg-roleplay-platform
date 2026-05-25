"""
mcp_broker.py — MCP server stdio 进程管理 + JSON-RPC 调用

设计目标（来自交接 TODO #9）：
- 启动 mcp_servers 表里 enabled=true 的 server 进程（stdio transport）
- MCP 握手 (initialize / initialized)
- tools/list 发现
- tools/call 路由
- 进程生命周期管理：start / stop / health check / restart on failure

协议参考：MCP 用 JSON-RPC 2.0 over stdio
- 请求：{"jsonrpc":"2.0","id":N,"method":"...","params":{...}}
- 响应：{"jsonrpc":"2.0","id":N,"result":{...}} 或 {"error":{...}}
- 通知：{"jsonrpc":"2.0","method":"...","params":{...}}（无 id）

不实现的事：
- prompts / resources / sampling 这些 MCP 扩展能力（先做 tools 最常用）
- 复杂的 schema 校验（依赖 server 自己校验）
- 远程 transport（HTTP/SSE），只做 stdio
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


# ── 全局 server 注册表（运行时） ──────────────────────────────────────────────
_RUNNING: dict[str, "MCPServerConn"] = {}
_LOCK = threading.RLock()

DEFAULT_INIT_TIMEOUT = 8       # 启动 + 握手超时
DEFAULT_CALL_TIMEOUT = 30      # tools/call 超时
MAX_RESPONSE_BYTES = 256 * 1024  # 单条响应最大 256 KB


class MCPServerConn:
    """单个 MCP server 的 stdio 进程连接 + JSON-RPC 客户端。"""

    def __init__(self, server_id: str, command: str, args: list[str], env: dict[str, str]):
        self.server_id = server_id
        self.command = command
        self.args = list(args)
        self.env = dict(env)
        self.proc: subprocess.Popen | None = None
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._writer_lock = threading.Lock()
        self._pending: dict[int, dict[str, Any]] = {}
        self._pending_lock = threading.Condition()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._closed = False
        self._init_error: str | None = None
        self.tools: list[dict[str, Any]] = []
        self.server_info: dict[str, Any] = {}
        self.last_stderr: list[str] = []  # 最近 50 行

    # ── 生命周期 ──────────────────────────────────────────────────
    def start(self, init_timeout: int = DEFAULT_INIT_TIMEOUT) -> bool:
        """启动子进程并完成 initialize 握手。"""
        if self.proc and self.proc.poll() is None:
            return True

        full_env = {**os.environ.copy(), **self.env}
        try:
            self.proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=full_env,
                bufsize=0,  # 不缓冲，及时刷
                text=False,  # 自己解码，避免行边界问题
            )
        except FileNotFoundError:
            self._init_error = f"找不到可执行文件: {self.command}"
            return False
        except Exception as e:
            self._init_error = f"启动失败: {e}"
            return False

        # 启动读线程
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()

        # initialize 握手
        try:
            init_result = self._request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "rpg-platform", "version": "0.1"},
                },
                timeout=init_timeout,
            )
            self.server_info = init_result.get("serverInfo", {})
            self._notify("notifications/initialized", {})
        except Exception as e:
            self._init_error = f"initialize 失败: {e}"
            self.stop()
            return False

        # 抓 tools 列表
        try:
            tools_result = self._request("tools/list", {}, timeout=init_timeout)
            self.tools = tools_result.get("tools") or []
        except Exception:
            self.tools = []

        return True

    def stop(self) -> None:
        self._closed = True
        if not self.proc:
            return
        try:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        except Exception:
            pass
        self.proc = None
        with self._pending_lock:
            self._pending_lock.notify_all()

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # ── JSON-RPC 调用 ─────────────────────────────────────────────
    def call_tool(self, name: str, arguments: dict[str, Any], timeout: int = DEFAULT_CALL_TIMEOUT) -> dict[str, Any]:
        if not self.is_alive():
            raise RuntimeError(f"MCP server {self.server_id} 未启动")
        return self._request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)

    def list_tools(self, refresh: bool = False, timeout: int = DEFAULT_INIT_TIMEOUT) -> list[dict[str, Any]]:
        if refresh and self.is_alive():
            try:
                result = self._request("tools/list", {}, timeout=timeout)
                self.tools = result.get("tools") or []
            except Exception:
                pass
        return self.tools

    # ── 内部：JSON-RPC ────────────────────────────────────────────
    def _request(self, method: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
        with self._id_lock:
            req_id = self._next_id
            self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        self._write(payload)
        deadline = time.monotonic() + timeout
        with self._pending_lock:
            while req_id not in self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"等待 {method} 响应超时")
                if self._closed or not self.is_alive():
                    raise RuntimeError(f"MCP server 进程退出")
                self._pending_lock.wait(timeout=min(remaining, 1.0))
            resp = self._pending.pop(req_id)
        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
        return resp.get("result") or {}

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, obj: dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise RuntimeError("stdin closed")
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._writer_lock:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()

    def _reader_loop(self) -> None:
        """从 stdout 逐行读 JSON-RPC 响应。"""
        assert self.proc and self.proc.stdout
        while not self._closed:
            try:
                line = self.proc.stdout.readline()
            except Exception:
                break
            if not line:
                break  # EOF
            if len(line) > MAX_RESPONSE_BYTES:
                continue
            try:
                msg = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                continue
            req_id = msg.get("id")
            if req_id is not None:
                with self._pending_lock:
                    self._pending[int(req_id)] = msg
                    self._pending_lock.notify_all()
            # else: 是 notification，目前不处理
        with self._pending_lock:
            self._pending_lock.notify_all()

    def _stderr_loop(self) -> None:
        """收集 stderr 用于诊断。"""
        assert self.proc and self.proc.stderr
        while not self._closed:
            try:
                line = self.proc.stderr.readline()
            except Exception:
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            self.last_stderr.append(text)
            if len(self.last_stderr) > 50:
                self.last_stderr = self.last_stderr[-50:]


# ══════════════════════════════════════════════════════════════════════
#  公共 API
# ══════════════════════════════════════════════════════════════════════
def start_server(server_id: str) -> dict[str, Any]:
    """从注册表加载配置，启动 server。已运行则返回现有连接信息。

    兼容两种字段名：catalog 用 `id`（_normalize_mcp_server 输出），broker 也允许 `server_id` 别名。
    """
    from tool_registry import load_mcp_catalog
    catalog = load_mcp_catalog()
    server_config = None
    for s in catalog.get("servers", []):
        if s.get("server_id") == server_id or s.get("id") == server_id:
            server_config = s
            break
    if not server_config:
        return {"ok": False, "error": f"server_id 不存在: {server_id}"}
    if not server_config.get("enabled"):
        return {"ok": False, "error": "server 未启用"}

    with _LOCK:
        existing = _RUNNING.get(server_id)
        if existing and existing.is_alive():
            return {
                "ok": True,
                "server_id": server_id,
                "tools": existing.tools,
                "server_info": existing.server_info,
                "already_running": True,
            }
        if existing:
            existing.stop()

        conn = MCPServerConn(
            server_id=server_id,
            command=server_config.get("command", ""),
            args=server_config.get("args", []),
            env=server_config.get("env", {}) or {},
        )
        ok = conn.start()
        if not ok:
            return {"ok": False, "error": conn._init_error or "启动失败", "stderr": conn.last_stderr[-10:]}
        _RUNNING[server_id] = conn
        return {
            "ok": True,
            "server_id": server_id,
            "tools": conn.tools,
            "server_info": conn.server_info,
            "already_running": False,
        }


def stop_server(server_id: str) -> dict[str, Any]:
    with _LOCK:
        conn = _RUNNING.pop(server_id, None)
        if not conn:
            return {"ok": True, "noop": True}
        conn.stop()
        return {"ok": True}


def stop_all() -> None:
    with _LOCK:
        for conn in list(_RUNNING.values()):
            conn.stop()
        _RUNNING.clear()


def status() -> dict[str, Any]:
    with _LOCK:
        return {
            "ok": True,
            "running": [
                {
                    "server_id": sid,
                    "alive": c.is_alive(),
                    "tools_count": len(c.tools),
                    "server_info": c.server_info,
                    "last_stderr": c.last_stderr[-3:],
                    "health": getattr(c, "_health", "unknown"),
                    "consecutive_failures": getattr(c, "_consecutive_failures", 0),
                    "last_ping_at": getattr(c, "_last_ping_at", 0),
                }
                for sid, c in _RUNNING.items()
            ],
        }


# ── 健康检查后台线程 ────────────────────────────────────────────────
_HEALTH_THREAD: threading.Thread | None = None
_HEALTH_STOP = threading.Event()
HEALTH_CHECK_INTERVAL = 30  # 秒
MAX_CONSECUTIVE_FAILURES = 2


def _health_loop():
    """每 30s 对所有 alive 的 MCP server 跑一次 tools/list 探活。
    连续 2 次失败 → 尝试重启进程。
    """
    while not _HEALTH_STOP.is_set():
        try:
            with _LOCK:
                servers = list(_RUNNING.items())
            for sid, conn in servers:
                if _HEALTH_STOP.is_set():
                    break
                if not conn.is_alive():
                    conn._health = "down"
                    conn._consecutive_failures = getattr(conn, "_consecutive_failures", 0) + 1
                    if conn._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        # 重启
                        try:
                            conn.stop()
                            ok = conn.start()
                            conn._health = "restarted" if ok else "restart_failed"
                            conn._consecutive_failures = 0
                        except Exception:
                            conn._health = "restart_failed"
                    continue
                # 进程在跑 → tools/list 探测
                try:
                    conn._request("tools/list", {}, timeout=5)
                    conn._health = "healthy"
                    conn._consecutive_failures = 0
                    conn._last_ping_at = time.time()
                except Exception:
                    conn._health = "unresponsive"
                    conn._consecutive_failures = getattr(conn, "_consecutive_failures", 0) + 1
                    if conn._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        try:
                            conn.stop()
                            ok = conn.start()
                            conn._health = "restarted" if ok else "restart_failed"
                            conn._consecutive_failures = 0
                        except Exception:
                            conn._health = "restart_failed"
        except Exception:
            pass
        _HEALTH_STOP.wait(HEALTH_CHECK_INTERVAL)


def start_health_loop():
    global _HEALTH_THREAD
    if _HEALTH_THREAD and _HEALTH_THREAD.is_alive():
        return
    _HEALTH_STOP.clear()
    _HEALTH_THREAD = threading.Thread(target=_health_loop, daemon=True, name="mcp-health")
    _HEALTH_THREAD.start()


def stop_health_loop():
    _HEALTH_STOP.set()


_AUDIT_LOG: list[dict[str, Any]] = []
_AUDIT_LIMIT = 500


def _audit_call(server_id: str, tool_name: str, user_id: int | None, ok: bool, error: str = "") -> None:
    """P0 #3：MCP server 进程跨用户共享（架构选择，保留性能）。
    用每次调用的审计 trail 让管理员事后能查到"哪个用户在何时调了哪个工具"。
    """
    try:
        _AUDIT_LOG.append({
            "ts": time.time(),
            "user_id": user_id,
            "server_id": server_id,
            "tool": tool_name,
            "ok": bool(ok),
            "error": (error or "")[:200],
        })
        if len(_AUDIT_LOG) > _AUDIT_LIMIT:
            del _AUDIT_LOG[:len(_AUDIT_LOG) - _AUDIT_LIMIT]
    except Exception:
        pass


def get_audit_log(user_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """返回最近 N 条调用记录。admin 看全部；其他用户只看自己的。"""
    with _LOCK:
        rows = list(_AUDIT_LOG)
    if user_id is not None:
        rows = [r for r in rows if r.get("user_id") == user_id]
    return rows[-max(1, limit):]


def call_tool(server_id: str, tool_name: str, arguments: dict[str, Any], timeout: int = DEFAULT_CALL_TIMEOUT, user_id: int | None = None) -> dict[str, Any]:
    """从主 GM 路由调用 MCP server 的工具。

    P0 #3：增加 user_id 参数：
    - 不会让 MCP server 进程隔离（成本太高），但会写入 _AUDIT_LOG
    - 后续可以审计"用户 X 通过 MCP 调用了什么"
    - 调用方（gm.py / context_agent.py）需要往下透传 user_id；老调用站点
      没传 user_id 不会报错（兼容），只是 audit 里看到 user_id=None
    """
    with _LOCK:
        conn = _RUNNING.get(server_id)
        if not conn or not conn.is_alive():
            start_result = start_server(server_id)
            if not start_result["ok"]:
                _audit_call(server_id, tool_name, user_id, False, start_result.get("error", "start failed"))
                return {"ok": False, "error": start_result.get("error", "无法启动 server")}
            conn = _RUNNING[server_id]
    try:
        result = conn.call_tool(tool_name, arguments or {}, timeout=timeout)
        _audit_call(server_id, tool_name, user_id, True)
        return {"ok": True, "result": result}
    except Exception as e:
        _audit_call(server_id, tool_name, user_id, False, str(e))
        return {"ok": False, "error": str(e), "stderr_tail": conn.last_stderr[-5:]}


def discover_all_tools() -> list[dict[str, Any]]:
    """列出所有启用 server 的可用工具，给主 GM 注入工具清单用。"""
    from tool_registry import load_mcp_catalog
    catalog = load_mcp_catalog()
    out = []
    for s in catalog.get("servers", []):
        if not s.get("enabled"):
            continue
        sid = s.get("server_id") or s.get("id")
        with _LOCK:
            conn = _RUNNING.get(sid)
        if not conn or not conn.is_alive():
            # 不主动启动；让调用方决定是否启动
            continue
        for tool in conn.tools:
            out.append({
                "server_id": sid,
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "schema": tool.get("inputSchema") or {},
            })
    return out
