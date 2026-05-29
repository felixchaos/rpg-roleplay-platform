//! mcp_broker — MCP stdio 子进程管理
//! 对应 Python: rpg/mcp_broker.py

use std::{
    collections::HashMap,
    sync::Arc,
    time::{Duration, Instant},
};

use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, Command},
    sync::{mpsc, Notify},
    time::timeout,
};
use tracing::{debug, info, warn};

use crate::{mcp::McpServer, DslError};

// ── 常量 ─────────────────────────────────────────────────────────────────────

pub const DEFAULT_INIT_TIMEOUT_SECS: u64 = 8;
pub const DEFAULT_CALL_TIMEOUT_SECS: u64 = 30;
const MAX_RESPONSE_BYTES: usize = 256 * 1024;
const HEALTH_CHECK_INTERVAL_SECS: u64 = 30;
const MAX_CONSECUTIVE_FAILURES: u32 = 2;
const STDERR_RING_SIZE: usize = 50;

// ── 进程状态 ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HealthStatus {
    Starting,
    Healthy,
    Unresponsive,
    Down,
    Restarted,
    RestartFailed,
}

/// 单个 MCP server 的运行时状态快照（用于 status() 返回）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct McpServerStatus {
    pub server_id: String,
    pub alive: bool,
    pub tools_count: usize,
    pub health: HealthStatus,
    pub consecutive_failures: u32,
    pub last_ping_at: Option<f64>,
    pub last_stderr: Vec<String>,
}

// ── 内部：单个进程句柄 ────────────────────────────────────────────────────────

/// 向读取 loop 发送的消息类型（JSON-RPC 响应）
type RpcId = u64;

struct McpProcess {
    spec: McpServer,
    /// stdin 写端（异步写 JSON-RPC 行）
    stdin_tx: mpsc::UnboundedSender<String>,
    /// 待回复的 pending 请求 (id → oneshot sender)
    pending: parking_lot::Mutex<HashMap<RpcId, tokio::sync::oneshot::Sender<serde_json::Value>>>,
    /// 下一个 JSON-RPC id
    next_id: std::sync::atomic::AtomicU64,
    /// 已发现的工具列表
    tools: RwLock<Vec<serde_json::Value>>,
    /// server_info（initialize 返回）
    server_info: RwLock<serde_json::Value>,
    /// 最近 50 行 stderr
    stderr_ring: RwLock<Vec<String>>,
    /// 健康状态
    health: RwLock<HealthStatus>,
    consecutive_failures: std::sync::atomic::AtomicU32,
    last_ping_at: RwLock<Option<f64>>,
    /// 进程是否存活（守护任务置为 false）
    alive: std::sync::atomic::AtomicBool,
    /// 停止信号
    stop_notify: Arc<Notify>,
}

impl McpProcess {
    fn new(spec: McpServer, stdin_tx: mpsc::UnboundedSender<String>) -> Arc<Self> {
        Arc::new(Self {
            spec,
            stdin_tx,
            pending: parking_lot::Mutex::new(HashMap::new()),
            next_id: std::sync::atomic::AtomicU64::new(1),
            tools: RwLock::new(Vec::new()),
            server_info: RwLock::new(serde_json::Value::Null),
            stderr_ring: RwLock::new(Vec::new()),
            health: RwLock::new(HealthStatus::Starting),
            consecutive_failures: std::sync::atomic::AtomicU32::new(0),
            last_ping_at: RwLock::new(None),
            alive: std::sync::atomic::AtomicBool::new(false),
            stop_notify: Arc::new(Notify::new()),
        })
    }

    fn is_alive(&self) -> bool {
        self.alive.load(std::sync::atomic::Ordering::Relaxed)
    }

    /// 发送 JSON-RPC 请求并等待响应。
    async fn request(
        &self,
        method: &str,
        params: serde_json::Value,
        timeout_secs: u64,
    ) -> Result<serde_json::Value, DslError> {
        let id = self
            .next_id
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let msg = serde_json::json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        let line = serde_json::to_string(&msg)?;

        let (tx, rx) = tokio::sync::oneshot::channel();
        self.pending.lock().insert(id, tx);

        self.stdin_tx
            .send(line)
            .map_err(|_| DslError::Other("MCP server stdin closed".into()))?;

        match timeout(Duration::from_secs(timeout_secs), rx).await {
            Ok(Ok(resp)) => {
                if let Some(err) = resp.get("error") {
                    return Err(DslError::Other(format!("MCP error: {err}")));
                }
                Ok(resp.get("result").cloned().unwrap_or(serde_json::Value::Null))
            }
            Ok(Err(_)) => Err(DslError::Other(format!(
                "MCP server {}: response channel dropped",
                self.spec.id
            ))),
            Err(_) => {
                self.pending.lock().remove(&id);
                Err(DslError::Timeout(timeout_secs))
            }
        }
    }

    /// 发送 JSON-RPC 通知（无 id，不等响应）。
    fn notify(&self, method: &str, params: serde_json::Value) {
        let msg = serde_json::json!({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        });
        if let Ok(line) = serde_json::to_string(&msg) {
            let _ = self.stdin_tx.send(line);
        }
    }

    /// 从 reader loop 投递响应。
    fn deliver_response(&self, id: RpcId, msg: serde_json::Value) {
        if let Some(tx) = self.pending.lock().remove(&id) {
            let _ = tx.send(msg);
        }
    }

    fn push_stderr(&self, line: String) {
        let mut ring = self.stderr_ring.write();
        ring.push(line);
        if ring.len() > STDERR_RING_SIZE {
            let drain_to = ring.len() - STDERR_RING_SIZE;
            ring.drain(..drain_to);
        }
    }

    fn status_snapshot(&self) -> McpServerStatus {
        McpServerStatus {
            server_id: self.spec.id.clone(),
            alive: self.is_alive(),
            tools_count: self.tools.read().len(),
            health: self.health.read().clone(),
            consecutive_failures: self
                .consecutive_failures
                .load(std::sync::atomic::Ordering::Relaxed),
            last_ping_at: *self.last_ping_at.read(),
            last_stderr: self.stderr_ring.read().iter().rev().take(3).rev().cloned().collect(),
        }
    }
}

// ── McpBroker ─────────────────────────────────────────────────────────────────

/// MCP 子进程 broker（对应 Python mcp_broker 模块级状态 + 公共 API）。
///
/// 线程安全：内部用 `RwLock<HashMap>` 保护进程表，
/// 所有公开方法均可跨线程并发调用。
pub struct McpBroker {
    running: RwLock<HashMap<String, Arc<McpProcess>>>,
    /// 用于停止 health loop
    health_stop: Arc<Notify>,
    /// 用于通知 health loop 已停止
    health_stopped: Arc<Notify>,
}

impl Default for McpBroker {
    fn default() -> Self {
        Self {
            running: RwLock::new(HashMap::new()),
            health_stop: Arc::new(Notify::new()),
            health_stopped: Arc::new(Notify::new()),
        }
    }
}

impl McpBroker {
    // ── 生命周期 ──────────────────────────────────────────────────────────────

    /// 启动一台 MCP server 进程（已运行则返回现有信息）。
    ///
    /// 对应 Python `start_server`。
    pub async fn start_server(&self, spec: McpServer) -> Result<serde_json::Value, DslError> {
        // 检查是否已运行
        {
            let guard = self.running.read();
            if let Some(proc) = guard.get(&spec.id) {
                if proc.is_alive() {
                    return Ok(serde_json::json!({
                        "ok": true,
                        "server_id": spec.id,
                        "tools": *proc.tools.read(),
                        "server_info": *proc.server_info.read(),
                        "already_running": true,
                    }));
                }
            }
        }

        // 构建环境变量（继承父进程 + 合并 spec.env）
        let mut full_env: HashMap<String, String> = std::env::vars().collect();
        full_env.extend(spec.env.iter().map(|(k, v)| (k.clone(), v.clone())));

        // 启动子进程
        let mut cmd = Command::new(&spec.command);
        cmd.args(&spec.args)
            .envs(&full_env)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);

        let mut child: Child = cmd.spawn().map_err(|e| {
            DslError::SpawnError(format!("MCP server '{}' spawn failed: {e}", spec.id))
        })?;

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| DslError::Other("no stdin".into()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| DslError::Other("no stdout".into()))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| DslError::Other("no stderr".into()))?;

        // stdin writer channel
        let (stdin_tx, stdin_rx) = mpsc::unbounded_channel::<String>();
        let proc = McpProcess::new(spec.clone(), stdin_tx);

        // ── tokio 守护任务 ──

        // stdin writer
        {
            let mut stdin = stdin;
            let mut rx: mpsc::UnboundedReceiver<String> = stdin_rx;
            tokio::spawn(async move {
                while let Some(line) = rx.recv().await {
                    let bytes = format!("{line}\n");
                    if stdin.write_all(bytes.as_bytes()).await.is_err() {
                        break;
                    }
                }
            });
        }

        // stdout reader loop
        {
            let proc_ref = Arc::clone(&proc);
            tokio::spawn(async move {
                let reader = BufReader::new(stdout);
                let mut lines = reader.lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    if line.len() > MAX_RESPONSE_BYTES {
                        continue;
                    }
                    if let Ok(msg) = serde_json::from_str::<serde_json::Value>(&line) {
                        if let Some(id) = msg.get("id").and_then(|v| v.as_u64()) {
                            proc_ref.deliver_response(id, msg);
                        }
                        // notifications (no id) ignored for now
                    }
                }
                proc_ref.alive.store(false, std::sync::atomic::Ordering::Relaxed);
                proc_ref.stop_notify.notify_waiters();
            });
        }

        // stderr collector
        {
            let proc_ref = Arc::clone(&proc);
            tokio::spawn(async move {
                let reader = BufReader::new(stderr);
                let mut lines = reader.lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    proc_ref.push_stderr(line);
                }
            });
        }

        // child waiter（进程退出时标记 alive=false）
        {
            let proc_ref = Arc::clone(&proc);
            tokio::spawn(async move {
                let _ = child.wait().await;
                proc_ref.alive.store(false, std::sync::atomic::Ordering::Relaxed);
                proc_ref.stop_notify.notify_waiters();
            });
        }

        proc.alive.store(true, std::sync::atomic::Ordering::Relaxed);

        // ── MCP 握手 ──
        let init_result = proc
            .request(
                "initialize",
                serde_json::json!({
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "rpg-platform", "version": "0.1"},
                }),
                DEFAULT_INIT_TIMEOUT_SECS,
            )
            .await
            .map_err(|e| {
                proc.alive.store(false, std::sync::atomic::Ordering::Relaxed);
                DslError::Other(format!("MCP initialize failed: {e}"))
            })?;

        {
            *proc.server_info.write() = init_result
                .get("serverInfo")
                .cloned()
                .unwrap_or(serde_json::Value::Null);
        }
        proc.notify("notifications/initialized", serde_json::json!({}));

        // tools/list
        if let Ok(tools_resp) = proc
            .request("tools/list", serde_json::json!({}), DEFAULT_INIT_TIMEOUT_SECS)
            .await
        {
            if let Some(tools) = tools_resp.get("tools").and_then(|v| v.as_array()) {
                *proc.tools.write() = tools.clone();
            }
        }

        *proc.health.write() = HealthStatus::Healthy;

        let tools_snap = proc.tools.read().clone();
        let info_snap = proc.server_info.read().clone();

        self.running.write().insert(spec.id.clone(), proc);

        info!(server_id = %spec.id, "MCP server started");

        Ok(serde_json::json!({
            "ok": true,
            "server_id": spec.id,
            "tools": tools_snap,
            "server_info": info_snap,
            "already_running": false,
        }))
    }

    /// 停止一台 MCP server 进程。
    ///
    /// 对应 Python `stop_server`。
    pub async fn stop_server(&self, id: &str) -> serde_json::Value {
        let proc = self.running.write().remove(id);
        match proc {
            None => serde_json::json!({"ok": true, "noop": true}),
            Some(p) => {
                p.alive.store(false, std::sync::atomic::Ordering::Relaxed);
                p.stop_notify.notify_waiters();
                info!(server_id = %id, "MCP server stopped");
                serde_json::json!({"ok": true})
            }
        }
    }

    /// 停止所有 MCP server 进程。
    ///
    /// 对应 Python `stop_all`。
    pub async fn stop_all(&self) {
        let procs: Vec<Arc<McpProcess>> = self.running.write().drain().map(|(_, v)| v).collect();
        for p in procs {
            p.alive.store(false, std::sync::atomic::Ordering::Relaxed);
            p.stop_notify.notify_waiters();
        }
        info!("McpBroker: all servers stopped");
    }

    /// 调用某 server 的 MCP tool。
    ///
    /// 对应 Python `call_tool`。
    pub async fn call_tool(
        &self,
        server_id: &str,
        tool_name: &str,
        arguments: serde_json::Value,
        timeout_secs: u64,
    ) -> serde_json::Value {
        let proc = {
            let guard = self.running.read();
            guard.get(server_id).cloned()
        };
        let proc = match proc {
            Some(p) if p.is_alive() => p,
            _ => {
                return serde_json::json!({
                    "ok": false,
                    "error": format!("MCP server '{server_id}' 未运行")
                });
            }
        };

        match proc
            .request(
                "tools/call",
                serde_json::json!({"name": tool_name, "arguments": arguments}),
                timeout_secs,
            )
            .await
        {
            Ok(result) => serde_json::json!({"ok": true, "result": result}),
            Err(e) => serde_json::json!({
                "ok": false,
                "error": e.to_string(),
                "stderr_tail": proc.stderr_ring.read().iter().rev().take(5).rev().cloned().collect::<Vec<_>>(),
            }),
        }
    }

    /// 返回所有正在运行的 server 状态快照。
    pub fn status(&self) -> Vec<McpServerStatus> {
        self.running
            .read()
            .values()
            .map(|p| p.status_snapshot())
            .collect()
    }

    // ── Health loop ───────────────────────────────────────────────────────────

    /// 启动后台健康检查 loop（周期 30s ping tools/list，失败 2 次尝试重启）。
    ///
    /// 对应 Python `start_health_loop`。
    pub fn start_health_loop(self: &Arc<Self>) {
        let broker = Arc::clone(self);
        let stop = Arc::clone(&self.health_stop);
        let stopped = Arc::clone(&self.health_stopped);
        tokio::spawn(async move {
            loop {
                // 等待 interval 或 stop 信号
                let sleep = tokio::time::sleep(Duration::from_secs(HEALTH_CHECK_INTERVAL_SECS));
                tokio::select! {
                    _ = sleep => {}
                    _ = stop.notified() => { break; }
                }
                broker.run_health_check_round().await;
            }
            stopped.notify_waiters();
            debug!("McpBroker health loop stopped");
        });
    }

    /// 停止后台健康检查 loop，等待其退出。
    ///
    /// 对应 Python `stop_health_loop`。
    pub async fn stop_health_loop(&self) {
        self.health_stop.notify_one();
        self.health_stopped.notified().await;
    }

    async fn run_health_check_round(&self) {
        let procs: Vec<Arc<McpProcess>> = {
            self.running.read().values().cloned().collect()
        };
        let _start = Instant::now();
        for proc in procs {
            if !proc.is_alive() {
                // 进程已死
                let prev = proc
                    .consecutive_failures
                    .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                *proc.health.write() = HealthStatus::Down;
                if prev + 1 >= MAX_CONSECUTIVE_FAILURES {
                    self.try_restart_proc(&proc).await;
                }
                continue;
            }
            // 发 tools/list 探活
            match proc
                .request("tools/list", serde_json::json!({}), 5)
                .await
            {
                Ok(resp) => {
                    if let Some(tools) = resp.get("tools").and_then(|v| v.as_array()) {
                        *proc.tools.write() = tools.clone();
                    }
                    *proc.health.write() = HealthStatus::Healthy;
                    proc.consecutive_failures
                        .store(0, std::sync::atomic::Ordering::Relaxed);
                    *proc.last_ping_at.write() = Some(
                        std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .unwrap_or_default()
                            .as_secs_f64(),
                    );
                }
                Err(_) => {
                    *proc.health.write() = HealthStatus::Unresponsive;
                    let prev = proc
                        .consecutive_failures
                        .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    if prev + 1 >= MAX_CONSECUTIVE_FAILURES {
                        self.try_restart_proc(&proc).await;
                    }
                }
            }
        }
    }

    async fn try_restart_proc(&self, proc: &Arc<McpProcess>) {
        warn!(server_id = %proc.spec.id, "attempting MCP server restart");
        proc.alive.store(false, std::sync::atomic::Ordering::Relaxed);

        // 重新 start（直接委托 start_server）
        match self.start_server(proc.spec.clone()).await {
            Ok(_) => {
                *proc.health.write() = HealthStatus::Restarted;
                proc.consecutive_failures
                    .store(0, std::sync::atomic::Ordering::Relaxed);
                info!(server_id = %proc.spec.id, "MCP server restarted");
            }
            Err(e) => {
                *proc.health.write() = HealthStatus::RestartFailed;
                warn!(server_id = %proc.spec.id, "MCP server restart failed: {e}");
            }
        }
    }
}
