//! sandbox — Skill 执行沙箱抽象与实现
//!
//! # 为什么需要这一层
//!
//! `skill_executor` 负责跑 **用户上传** 的 python3 脚本。历史实现仅用 `rlimit`
//! 限制 CPU / 文件大小 / fd 数量。但 rlimit 有两个根本缺陷:
//!
//! 1. **无隔离能力**：rlimit 只能限制资源「数量」,完全管不到
//!    - 网络(脚本可向任意外部主机发请求、外联回传)
//!    - 文件系统(脚本可读 `~/.ssh`、`master.key`、环境里的密钥路径)
//!    - 进程/IPC 命名空间
//! 2. **macOS 上几乎形同虚设**：macOS 内核忽略 `RLIMIT_AS`(地址空间 = 内存上限),
//!    且本仓库历史实现是在 **父进程** 调 `setrlimit`,既影响宿主自身,又无法在
//!    `fork`/`exec` 边界精确套用到子进程;只剩 CPU / FSIZE / NOFILE 三项,
//!    内存彻底不受控,网络/FS 也毫无防护。
//!
//! 因此 **生产环境强烈建议使用 [`ContainerSandbox`]**(docker / podman / nsjail),
//! 通过 `--network=none --read-only --memory --cpus` 等真正的内核级隔离来运行
//! 不可信脚本。[`RlimitSandbox`] 仅作开发态默认,并不构成可信安全边界。
//!
//! 模式由环境变量 `RPG_SANDBOX_MODE` 选择:
//! - `RPG_SANDBOX_MODE=container` → [`ContainerSandbox`]
//! - 其它/未设置 → [`RlimitSandbox`](开发默认)
//!
//! 容器运行时由 `RPG_SANDBOX_RUNTIME` 选择(`docker` / `podman` / `nsjail`),
//! 镜像由 `RPG_SANDBOX_IMAGE` 选择(默认 `python:3.12-slim`)。

use std::path::Path;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tracing::warn;

use crate::skill_executor::{
    run_command_in_dir, DEFAULT_TIMEOUT_SEC, RLIMIT_AS_BYTES, RLIMIT_CPU_SEC,
    RLIMIT_FSIZE_BYTES, SkillOutput,
};
use crate::DslError;

// ── 资源/隔离限制 ───────────────────────────────────────────────────────────────

/// 沙箱资源 / 隔离限制集合。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SandboxLimits {
    /// CPU 时间上限(秒)。对应 RLIMIT_CPU / `--cpus` 折算。
    pub cpu_secs: u64,
    /// 内存上限(字节)。对应 RLIMIT_AS / `--memory`。
    pub mem_bytes: u64,
    /// 单文件写入上限(字节)。对应 RLIMIT_FSIZE。
    pub fsize_bytes: u64,
    /// 是否禁用网络(容器模式映射为 `--network=none`)。
    pub no_network: bool,
    /// 墙钟超时(秒),超时强杀。
    pub timeout_secs: u64,
}

impl SandboxLimits {
    /// 安全默认值:沿用历史 rlimit 常量 + 默认禁网 + 默认超时。
    pub fn safe_default() -> Self {
        Self {
            cpu_secs: RLIMIT_CPU_SEC,
            mem_bytes: RLIMIT_AS_BYTES,
            fsize_bytes: RLIMIT_FSIZE_BYTES,
            no_network: true,
            timeout_secs: DEFAULT_TIMEOUT_SEC,
        }
    }

    /// 仅指定墙钟超时,其余沿用安全默认。
    pub fn with_timeout(timeout_secs: u64) -> Self {
        Self {
            timeout_secs,
            ..Self::safe_default()
        }
    }
}

impl Default for SandboxLimits {
    fn default() -> Self {
        Self::safe_default()
    }
}

// ── trait ───────────────────────────────────────────────────────────────────

/// Skill 执行沙箱抽象。
///
/// 实现负责在受限环境中运行 `skill_dir` 下的入口脚本,并把 `payload`
/// 通过 stdin 传入,返回 [`SkillOutput`]。
#[async_trait]
pub trait SkillSandbox: Send + Sync {
    /// 在沙箱内运行 skill。
    ///
    /// - `skill_dir`:已解压/已落盘的 skill 目录(只读挂载/拷贝)。
    /// - `payload`:序列化为 JSON 后经 stdin 传给脚本。
    /// - `limits`:资源与隔离限制。
    async fn run(
        &self,
        skill_dir: &Path,
        payload: Value,
        limits: SandboxLimits,
    ) -> Result<SkillOutput, DslError>;
}

// ── 入口脚本探测 ────────────────────────────────────────────────────────────────

/// 在 skill 目录里挑选入口脚本(main.py 优先,否则首个 .py)。
fn detect_entrypoint(skill_dir: &Path) -> String {
    let main = skill_dir.join("main.py");
    if main.exists() {
        return "main.py".to_owned();
    }
    if let Ok(rd) = std::fs::read_dir(skill_dir) {
        for e in rd.flatten() {
            let p = e.path();
            if p.extension().and_then(|s| s.to_str()) == Some("py") {
                if let Some(name) = p.file_name().and_then(|n| n.to_str()) {
                    return name.to_owned();
                }
            }
        }
    }
    "main.py".to_owned()
}

// ── RlimitSandbox(开发默认,macOS 弱) ──────────────────────────────────────────

/// 基于 `tokio::process` + `rlimit` 的沙箱。
///
/// **安全级别:弱**。Linux 上 rlimit 在子进程 `pre_exec` 中生效,可限制
/// CPU / 内存(AS) / 文件大小 / fd;但 **无网络与文件系统隔离**,脚本仍可
/// 读宿主任意可访问文件、发起外联。macOS 上更弱(忽略 RLIMIT_AS、内存不受控)。
///
/// 仅供开发态使用;生产请用 [`ContainerSandbox`]。
#[derive(Debug, Default, Clone)]
pub struct RlimitSandbox;

#[async_trait]
impl SkillSandbox for RlimitSandbox {
    async fn run(
        &self,
        skill_dir: &Path,
        payload: Value,
        limits: SandboxLimits,
    ) -> Result<SkillOutput, DslError> {
        if !skill_dir.exists() || !skill_dir.is_dir() {
            return Ok(SkillOutput::err(format!(
                "skill_dir 不存在: {}",
                skill_dir.display()
            )));
        }
        let entry = detect_entrypoint(skill_dir);
        let stdin_text = serde_json::to_string(&payload)?;
        let cmd = vec!["python3".to_owned(), entry];
        // RlimitSandbox 不做拷贝(由历史 run_skill_command 自行拷贝);此处直接在
        // skill_dir 内执行,套用 rlimit。
        run_command_in_dir(&cmd, skill_dir, &limits, Some(stdin_text), None, false).await
    }
}

// ── ContainerSandbox(生产推荐) ──────────────────────────────────────────────────

/// 受支持的容器运行时。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContainerRuntime {
    Docker,
    Podman,
    Nsjail,
}

impl ContainerRuntime {
    fn from_env_str(s: &str) -> Option<Self> {
        match s.trim().to_lowercase().as_str() {
            "docker" => Some(Self::Docker),
            "podman" => Some(Self::Podman),
            "nsjail" => Some(Self::Nsjail),
            _ => None,
        }
    }

    /// 运行时可执行文件名。
    pub fn binary(&self) -> &'static str {
        match self {
            Self::Docker => "docker",
            Self::Podman => "podman",
            Self::Nsjail => "nsjail",
        }
    }
}

/// 基于容器 / nsjail 的强隔离沙箱(**生产推荐**)。
///
/// - docker / podman:`run --rm --network=none --memory --cpus --read-only`
///   + 只读挂载 skill 目录 + 丢弃所有 capability + 禁止提权。
/// - nsjail:`--disable_clone_newnet`(禁网)+ 只读绑定 + rlimit。
///
/// 实际执行依赖宿主已安装对应运行时;若缺失则返回清晰错误(不静默降级)。
#[derive(Debug, Clone)]
pub struct ContainerSandbox {
    pub runtime: ContainerRuntime,
    pub image: String,
}

impl ContainerSandbox {
    /// 从环境变量构造:
    /// - `RPG_SANDBOX_RUNTIME`:docker(默认) / podman / nsjail
    /// - `RPG_SANDBOX_IMAGE`:容器镜像(默认 `python:3.12-slim`)
    pub fn from_env() -> Self {
        let runtime = std::env::var("RPG_SANDBOX_RUNTIME")
            .ok()
            .and_then(|s| ContainerRuntime::from_env_str(&s))
            .unwrap_or(ContainerRuntime::Docker);
        let image = std::env::var("RPG_SANDBOX_IMAGE")
            .unwrap_or_else(|_| "python:3.12-slim".to_owned());
        Self { runtime, image }
    }

    /// 构造容器运行时的完整命令行参数(不含运行时可执行名本身)。
    ///
    /// 纯函数,便于单测,不触碰任何 IO。
    ///
    /// - `skill_dir`:宿主侧 skill 目录,只读挂载到容器内 `/skill`。
    /// - `entry`:容器内入口脚本(相对 `/skill`)。
    pub fn build_args(
        &self,
        skill_dir: &Path,
        entry: &str,
        limits: &SandboxLimits,
    ) -> Vec<String> {
        let mut a: Vec<String> = Vec::new();
        let skill_disp = skill_dir.display().to_string();
        match self.runtime {
            ContainerRuntime::Docker | ContainerRuntime::Podman => {
                a.push("run".into());
                a.push("--rm".into());
                a.push("-i".into()); // 接收 stdin payload
                if limits.no_network {
                    a.push("--network=none".into());
                }
                // 内存上限
                a.push("--memory".into());
                a.push(format!("{}b", limits.mem_bytes));
                // CPU:把 cpu_secs 折算为相对 timeout 的 cpu 配额(下限 1 核)。
                let cpus = if limits.timeout_secs > 0 {
                    let v = limits.cpu_secs as f64 / limits.timeout_secs as f64;
                    if v < 1.0 { 1.0 } else { v }
                } else {
                    1.0
                };
                a.push("--cpus".into());
                a.push(format!("{cpus:.2}"));
                // 文件系统:根只读,挂载 skill 只读,工作目录置于 tmpfs。
                a.push("--read-only".into());
                a.push("--tmpfs".into());
                a.push("/tmp:rw,size=16m".into());
                // 安全加固:丢弃所有 capability、禁止提权。
                a.push("--cap-drop=ALL".into());
                a.push("--security-opt".into());
                a.push("no-new-privileges".into());
                a.push("--pids-limit".into());
                a.push("128".into());
                // 只读挂载 skill 目录到 /skill
                a.push("-v".into());
                a.push(format!("{skill_disp}:/skill:ro"));
                a.push("-w".into());
                a.push("/skill".into());
                // 最小 env(容器内默认 PATH 已含 python)
                a.push("-e".into());
                a.push("LANG=C.UTF-8".into());
                a.push("-e".into());
                a.push("LC_ALL=C.UTF-8".into());
                // 镜像 + 命令
                a.push(self.image.clone());
                a.push("python3".into());
                a.push(entry.to_owned());
            }
            ContainerRuntime::Nsjail => {
                // nsjail:挂载 skill 只读 + 默认禁网(不开 newnet 即无网络)。
                a.push("--quiet".into());
                a.push("--time_limit".into());
                a.push(limits.timeout_secs.to_string());
                a.push("--rlimit_cpu".into());
                a.push(limits.cpu_secs.to_string());
                a.push("--rlimit_as".into());
                a.push((limits.mem_bytes / (1024 * 1024)).to_string()); // MB
                a.push("--rlimit_fsize".into());
                a.push((limits.fsize_bytes / (1024 * 1024)).to_string()); // MB
                // 只读绑定 skill_dir → /skill
                a.push("--bindmount_ro".into());
                a.push(format!("{skill_disp}:/skill"));
                a.push("--cwd".into());
                a.push("/skill".into());
                if !limits.no_network {
                    a.push("--disable_clone_newnet".into());
                }
                a.push("--".into());
                a.push("/usr/bin/python3".into());
                a.push(entry.to_owned());
            }
        }
        a
    }
}

#[async_trait]
impl SkillSandbox for ContainerSandbox {
    async fn run(
        &self,
        skill_dir: &Path,
        payload: Value,
        limits: SandboxLimits,
    ) -> Result<SkillOutput, DslError> {
        if !skill_dir.exists() || !skill_dir.is_dir() {
            return Ok(SkillOutput::err(format!(
                "skill_dir 不存在: {}",
                skill_dir.display()
            )));
        }

        let binary = self.runtime.binary();
        // 检查运行时是否可用;缺失 → 清晰报错,不静默降级。
        if which::which(binary).is_err() {
            return Ok(SkillOutput::err(format!(
                "容器运行时 `{binary}` 未安装/不在 PATH(RPG_SANDBOX_MODE=container)。\
                 请安装对应运行时,或将 RPG_SANDBOX_MODE 设为空以回退 rlimit(仅开发)。"
            )));
        }

        let entry = detect_entrypoint(skill_dir);
        let args = self.build_args(skill_dir, &entry, &limits);
        let stdin_text = serde_json::to_string(&payload)?;

        // 容器命令:cmd[0]=运行时二进制,其余为 build_args。
        let mut cmd = Vec::with_capacity(args.len() + 1);
        cmd.push(binary.to_owned());
        cmd.extend(args);

        // 容器内已隔离,故不在宿主侧再套 rlimit(skip_rlimit=true);
        // 也不再拷贝(只读挂载由容器负责)。current_dir 用 skill_dir 即可。
        run_command_in_dir(&cmd, skill_dir, &limits, Some(stdin_text), None, true).await
    }
}

// ── 工厂 ─────────────────────────────────────────────────────────────────────

/// 根据 `RPG_SANDBOX_MODE` 选择沙箱实现。
///
/// - `RPG_SANDBOX_MODE=container` → [`ContainerSandbox`](生产推荐)
/// - 其它 → [`RlimitSandbox`](开发默认,**非可信安全边界**)
pub fn default_sandbox() -> Box<dyn SkillSandbox> {
    match std::env::var("RPG_SANDBOX_MODE").as_deref() {
        Ok("container") => {
            let sb = ContainerSandbox::from_env();
            warn!(
                runtime = sb.runtime.binary(),
                image = %sb.image,
                "skill 沙箱: container 模式(强隔离)"
            );
            Box::new(sb)
        }
        _ => {
            warn!(
                "skill 沙箱: rlimit 模式(开发默认,无网络/FS 隔离;生产请设 \
                 RPG_SANDBOX_MODE=container)"
            );
            Box::new(RlimitSandbox)
        }
    }
}

// ── 测试 ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn limits_safe_default() {
        let l = SandboxLimits::safe_default();
        assert!(l.no_network);
        assert_eq!(l.cpu_secs, RLIMIT_CPU_SEC);
        assert_eq!(l.mem_bytes, RLIMIT_AS_BYTES);
        assert_eq!(l.fsize_bytes, RLIMIT_FSIZE_BYTES);
        assert_eq!(l.timeout_secs, DEFAULT_TIMEOUT_SEC);
    }

    #[test]
    fn limits_with_timeout() {
        let l = SandboxLimits::with_timeout(99);
        assert_eq!(l.timeout_secs, 99);
        assert!(l.no_network);
    }

    #[test]
    fn runtime_from_env_str() {
        assert_eq!(
            ContainerRuntime::from_env_str("docker"),
            Some(ContainerRuntime::Docker)
        );
        assert_eq!(
            ContainerRuntime::from_env_str("PODMAN"),
            Some(ContainerRuntime::Podman)
        );
        assert_eq!(
            ContainerRuntime::from_env_str(" nsjail "),
            Some(ContainerRuntime::Nsjail)
        );
        assert_eq!(ContainerRuntime::from_env_str("lxc"), None);
        assert_eq!(ContainerRuntime::Docker.binary(), "docker");
        assert_eq!(ContainerRuntime::Nsjail.binary(), "nsjail");
    }

    #[test]
    fn docker_args_have_isolation_flags() {
        let sb = ContainerSandbox {
            runtime: ContainerRuntime::Docker,
            image: "python:3.12-slim".into(),
        };
        let limits = SandboxLimits::safe_default();
        let args = sb.build_args(Path::new("/srv/skills/foo"), "main.py", &limits);
        // 关键隔离 flag 必须存在
        assert!(args.contains(&"run".to_string()));
        assert!(args.contains(&"--rm".to_string()));
        assert!(args.contains(&"--network=none".to_string()));
        assert!(args.contains(&"--read-only".to_string()));
        assert!(args.contains(&"--cap-drop=ALL".to_string()));
        assert!(args.contains(&"no-new-privileges".to_string()));
        // 内存 flag 携带字节值
        let mem_idx = args.iter().position(|a| a == "--memory").unwrap();
        assert_eq!(args[mem_idx + 1], format!("{}b", limits.mem_bytes));
        // 只读挂载 + 工作目录
        assert!(args.contains(&"/srv/skills/foo:/skill:ro".to_string()));
        assert!(args.contains(&"/skill".to_string()));
        // 镜像与命令在末尾
        assert_eq!(args[args.len() - 3], "python:3.12-slim");
        assert_eq!(args[args.len() - 2], "python3");
        assert_eq!(args[args.len() - 1], "main.py");
    }

    #[test]
    fn docker_args_respect_no_network_false() {
        let sb = ContainerSandbox {
            runtime: ContainerRuntime::Docker,
            image: "img".into(),
        };
        let mut limits = SandboxLimits::safe_default();
        limits.no_network = false;
        let args = sb.build_args(Path::new("/s"), "main.py", &limits);
        assert!(!args.contains(&"--network=none".to_string()));
    }

    #[test]
    fn nsjail_args_have_rlimits_and_bind() {
        let sb = ContainerSandbox {
            runtime: ContainerRuntime::Nsjail,
            image: "ignored".into(),
        };
        let limits = SandboxLimits::safe_default();
        let args = sb.build_args(Path::new("/srv/s"), "run.py", &limits);
        assert!(args.contains(&"--time_limit".to_string()));
        assert!(args.contains(&"--rlimit_cpu".to_string()));
        assert!(args.contains(&"--rlimit_as".to_string()));
        assert!(args.contains(&"--bindmount_ro".to_string()));
        assert!(args.contains(&"/srv/s:/skill".to_string()));
        // 末尾为解释器 + 入口
        assert_eq!(args[args.len() - 2], "/usr/bin/python3");
        assert_eq!(args[args.len() - 1], "run.py");
    }

    #[test]
    fn cpus_floors_at_one() {
        let sb = ContainerSandbox {
            runtime: ContainerRuntime::Docker,
            image: "img".into(),
        };
        // cpu_secs < timeout_secs → 折算 < 1 → 应下限到 1.00
        let limits = SandboxLimits {
            cpu_secs: 5,
            mem_bytes: 1024,
            fsize_bytes: 1024,
            no_network: true,
            timeout_secs: 30,
        };
        let args = sb.build_args(Path::new("/s"), "main.py", &limits);
        let i = args.iter().position(|a| a == "--cpus").unwrap();
        assert_eq!(args[i + 1], "1.00");
    }
}
