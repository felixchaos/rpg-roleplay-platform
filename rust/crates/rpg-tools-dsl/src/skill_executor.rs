//! skill_executor — Skill 沙箱执行（Rust 版）
//! 对应 Python: rpg/skill_executor.py
//!
//! subprocess → tokio::process::Command
//! rlimit     → Linux: rlimit crate pre_exec; macOS: unsafe libc::setrlimit before spawn

use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use tokio::{
    io::AsyncReadExt,
    process::Command,
    time::timeout,
};
use tracing::{debug, warn};

use crate::DslError;

// ── Skill bundle 常量 ─────────────────────────────────────────────────────────
pub const MAX_SKILL_BYTES: usize = 2 * 1024 * 1024;       // 2 MB 原始 zip
pub const MAX_SKILL_FILES: usize = 80;
pub const MAX_SKILL_UNPACKED_BYTES: usize = 4 * 1024 * 1024; // 4 MB 解压后

// ── 限制常量 ──────────────────────────────────────────────────────────────────

pub const DEFAULT_TIMEOUT_SEC: u64 = 30;
pub const MAX_TIMEOUT_SEC: u64 = 300;
pub const MAX_STDOUT_BYTES: usize = 1 * 1024 * 1024;   // 1 MB
pub const MAX_STDERR_BYTES: usize = 256 * 1024;          // 256 KB
pub const RLIMIT_CPU_SEC: u64 = 60;
pub const RLIMIT_AS_BYTES: u64 = 512 * 1024 * 1024;     // 512 MB
pub const RLIMIT_FSIZE_BYTES: u64 = 16 * 1024 * 1024;   // 16 MB
pub const RLIMIT_NOFILE: u64 = 64;

/// 环境变量白名单
const ENV_ALLOW: &[&str] = &[
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "HOME", "TMPDIR", "USER", "SHELL",
];

// ── 输出结构体 ────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillOutput {
    pub ok: bool,
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub duration_ms: u64,
    pub truncated_stdout: bool,
    pub truncated_stderr: bool,
    pub timeout: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl SkillOutput {
    fn err(msg: impl Into<String>) -> Self {
        Self {
            ok: false,
            exit_code: -1,
            stdout: String::new(),
            stderr: String::new(),
            duration_ms: 0,
            truncated_stdout: false,
            truncated_stderr: false,
            timeout: false,
            error: Some(msg.into()),
        }
    }
}

// ── 公开 API ──────────────────────────────────────────────────────────────────

/// 高级入口：按 skill_id 执行（从 GLOBAL_REGISTRY 查路径）
///
/// 实际调用 `run_skill_command` 执行 skill 目录下的脚本。
/// skill_id 必须已在 GLOBAL_REGISTRY 注册且 kind == Skill。
pub async fn execute_skill(
    skill_id: &str,
    payload: serde_json::Value,
) -> Result<SkillOutput, DslError> {
    use crate::tool_registry::{GLOBAL_REGISTRY, ToolKind};

    let (skill_root, entrypoint) = {
        let reg = GLOBAL_REGISTRY.read();
        let def = reg
            .get(skill_id)
            .ok_or_else(|| DslError::SkillNotFound(skill_id.to_owned()))?;

        if def.kind != ToolKind::Skill {
            return Err(DslError::Other(format!(
                "{skill_id} is not a Skill (kind = {:?})",
                def.kind
            )));
        }

        // meta 里存 path / entrypoint（import 时写入）
        let path = def
            .meta
            .get("path")
            .and_then(|v| v.as_str())
            .map(PathBuf::from)
            .ok_or_else(|| DslError::Other(format!("{skill_id}: missing meta.path")))?;

        let entry = def
            .meta
            .get("entrypoint")
            .and_then(|v| v.as_str())
            .unwrap_or("main.py")
            .to_owned();

        (path, entry)
    };

    // 把 payload 序列化后通过 stdin 传给 skill
    let stdin_text = serde_json::to_string(&payload)?;

    let cmd = vec!["python3".to_owned(), entrypoint];
    run_skill_command(
        &cmd,
        &skill_root,
        DEFAULT_TIMEOUT_SEC,
        Some(stdin_text),
        None,
    )
    .await
    .map_err(DslError::from)
}

/// 在沙箱里运行一条命令（对应 Python `run_skill_command`）
pub async fn run_skill_command(
    cmd: &[String],
    skill_root: &Path,
    timeout_sec: u64,
    stdin_text: Option<String>,
    extra_env: Option<HashMap<String, String>>,
) -> Result<SkillOutput, DslError> {
    let timeout_sec = timeout_sec.clamp(1, MAX_TIMEOUT_SEC);

    if !skill_root.exists() || !skill_root.is_dir() {
        return Ok(SkillOutput::err(format!(
            "skill_root 不存在: {}",
            skill_root.display()
        )));
    }

    // 创建临时工作目录并复制 skill 文件
    let workdir = copy_to_tempdir(skill_root).await?;

    let result = run_in_workdir(cmd, &workdir, timeout_sec, stdin_text, extra_env).await;

    // 清理临时目录
    if let Err(e) = tokio::fs::remove_dir_all(&workdir).await {
        warn!("cleanup tempdir failed: {e}");
    }

    result
}

// ── 内部实现 ──────────────────────────────────────────────────────────────────

async fn copy_to_tempdir(src: &Path) -> Result<PathBuf, DslError> {
    let dir = tokio::task::spawn_blocking({
        let src = src.to_path_buf();
        move || -> std::io::Result<PathBuf> {
            let tmp = tempfile::Builder::new()
                .prefix("skill_exec_")
                .tempdir()?
                .keep();
            copy_dir_all(&src, &tmp)?;
            Ok(tmp)
        }
    })
    .await
    .map_err(|e| DslError::Other(e.to_string()))?
    .map_err(DslError::Io)?;
    Ok(dir)
}

fn copy_dir_all(src: &Path, dst: &Path) -> std::io::Result<()> {
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let dest = dst.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            std::fs::create_dir_all(&dest)?;
            copy_dir_all(&entry.path(), &dest)?;
        } else {
            std::fs::copy(entry.path(), dest)?;
        }
    }
    Ok(())
}

async fn run_in_workdir(
    cmd: &[String],
    workdir: &Path,
    timeout_sec: u64,
    stdin_text: Option<String>,
    extra_env: Option<HashMap<String, String>>,
) -> Result<SkillOutput, DslError> {
    if cmd.is_empty() {
        return Ok(SkillOutput::err("cmd is empty"));
    }

    let env = build_env(extra_env.as_ref());

    let mut command = Command::new(&cmd[0]);
    command
        .args(&cmd[1..])
        .current_dir(workdir)
        .envs(&env)
        .env("SKILL_WORKDIR", workdir)
        .stdin(if stdin_text.is_some() {
            std::process::Stdio::piped()
        } else {
            std::process::Stdio::null()
        })
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);

    // Linux: pre_exec hook 在 fork 后 exec 前运行
    #[cfg(target_os = "linux")]
    unsafe {
        command.pre_exec(preexec_setrlimit);
    }
    // macOS: setrlimit 在 spawn 前调用（作用于当前进程 + 继承到子进程）
    #[cfg(target_os = "macos")]
    apply_macos_rlimit();

    debug!(?cmd, ?workdir, "launching skill");

    let start = Instant::now();

    let mut child = match command.spawn() {
        Ok(c) => c,
        Err(e) => {
            return Ok(SkillOutput::err(format!("spawn failed: {e}")));
        }
    };

    // 写 stdin
    if let Some(text) = &stdin_text {
        if let Some(mut stdin) = child.stdin.take() {
            use tokio::io::AsyncWriteExt;
            let _ = stdin.write_all(text.as_bytes()).await;
            // drop closes the pipe
        }
    }

    // 读 stdout / stderr（带超时）
    let fut = async {
        let mut stdout_handle = child.stdout.take().expect("stdout piped");
        let mut stderr_handle = child.stderr.take().expect("stderr piped");

        let mut out_buf = Vec::new();
        let mut err_buf = Vec::new();

        let (r1, r2) = tokio::join!(
            stdout_handle.read_to_end(&mut out_buf),
            stderr_handle.read_to_end(&mut err_buf),
        );
        r1?;
        r2?;

        let status = child.wait().await?;
        Ok::<_, std::io::Error>((status, out_buf, err_buf))
    };

    let timeout_hit;
    let (exit_code, out_bytes, err_bytes);

    match timeout(Duration::from_secs(timeout_sec), fut).await {
        Ok(Ok((status, out, err))) => {
            timeout_hit = false;
            exit_code = status.code().unwrap_or(-1);
            out_bytes = out;
            err_bytes = err;
        }
        Ok(Err(e)) => {
            return Ok(SkillOutput::err(format!("io error: {e}")));
        }
        Err(_) => {
            // timeout — try kill
            let _ = child.kill().await;
            timeout_hit = true;
            exit_code = -1;
            out_bytes = Vec::new();
            err_bytes = Vec::new();
        }
    }

    let duration_ms = start.elapsed().as_millis() as u64;

    let truncated_stdout = out_bytes.len() > MAX_STDOUT_BYTES;
    let truncated_stderr = err_bytes.len() > MAX_STDERR_BYTES;

    let mut stdout = String::from_utf8_lossy(
        &out_bytes[..out_bytes.len().min(MAX_STDOUT_BYTES)],
    )
    .into_owned();
    let mut stderr = String::from_utf8_lossy(
        &err_bytes[..err_bytes.len().min(MAX_STDERR_BYTES)],
    )
    .into_owned();

    if truncated_stdout {
        stdout.push_str("\n...[stdout truncated]");
    }
    if truncated_stderr {
        stderr.push_str("\n...[stderr truncated]");
    }

    Ok(SkillOutput {
        ok: exit_code == 0 && !timeout_hit,
        exit_code,
        stdout,
        stderr,
        duration_ms,
        truncated_stdout,
        truncated_stderr,
        timeout: timeout_hit,
        error: if timeout_hit {
            Some(format!("timed out after {timeout_sec}s"))
        } else {
            None
        },
    })
}

fn build_env(extra: Option<&HashMap<String, String>>) -> HashMap<String, String> {
    let mut env: HashMap<String, String> = std::env::vars()
        .filter(|(k, _)| ENV_ALLOW.contains(&k.as_str()))
        .collect();
    env.entry("LANG".into()).or_insert_with(|| "en_US.UTF-8".into());
    env.entry("LC_ALL".into()).or_insert_with(|| "en_US.UTF-8".into());
    env.entry("PATH".into())
        .or_insert_with(|| "/usr/local/bin:/usr/bin:/bin".into());
    if let Some(extra) = extra {
        for (k, v) in extra {
            if v.len() < 4096 {
                env.insert(k.clone(), v.clone());
            }
        }
    }
    env
}

/// Linux-only: preexec rlimit 设置（在子进程 fork 后 exec 前运行）
#[cfg(target_os = "linux")]
fn preexec_setrlimit() -> std::io::Result<()> {
    use rlimit::{setrlimit, Resource};

    let _ = setrlimit(Resource::CPU, RLIMIT_CPU_SEC, RLIMIT_CPU_SEC);
    let _ = setrlimit(Resource::AS, RLIMIT_AS_BYTES, RLIMIT_AS_BYTES);
    let _ = setrlimit(Resource::FSIZE, RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES);
    let _ = setrlimit(Resource::NOFILE, RLIMIT_NOFILE, RLIMIT_NOFILE);

    // 与父进程脱离会话
    unsafe {
        libc::setsid();
    }
    Ok(())
}

/// macOS: spawn 前设置 rlimit（子进程 fork-inherit）。
///
/// macOS 上 `tokio::process::Command::pre_exec` 可用但 `rlimit` crate 的
/// `Resource::AS` 无效（macOS 内核忽略 RLIMIT_AS）。改用 unsafe libc 直调，
/// 只设置 RLIMIT_CPU / RLIMIT_FSIZE / RLIMIT_NOFILE，跳过 RLIMIT_AS。
#[cfg(target_os = "macos")]
fn apply_macos_rlimit() {
    unsafe {
        set_rlimit_macos(libc::RLIMIT_CPU, RLIMIT_CPU_SEC);
        set_rlimit_macos(libc::RLIMIT_FSIZE, RLIMIT_FSIZE_BYTES);
        set_rlimit_macos(libc::RLIMIT_NOFILE, RLIMIT_NOFILE);
        // RLIMIT_AS: macOS 内核不支持，跳过
    }
}

#[cfg(target_os = "macos")]
unsafe fn set_rlimit_macos(resource: libc::c_int, value: u64) {
    let rl = libc::rlimit {
        rlim_cur: value as libc::rlim_t,
        rlim_max: value as libc::rlim_t,
    };
    libc::setrlimit(resource, &rl);
}

// ── Skill bundle 导入 ─────────────────────────────────────────────────────────

/// Skill 元数据（import_skill_bundle 返回）
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ImportedSkill {
    pub id: String,
    pub name: String,
    pub path: String,
    pub enabled: bool,
}

/// 将 ZIP 格式的 skill bundle 解压到 `skill_dir/<id>/`。
///
/// - `zip_bytes`：原始 zip 内容（不超过 `MAX_SKILL_BYTES`）
/// - `skill_name`：给这个 skill 起的 slug（用于目录名；自动 slugify）
/// - `skill_dir`：安装根目录（对应 Python `USER_SKILL_DIR`）
///
/// 对应 Python `_extract_skill_zip` + `import_skill_bundle`（只处理 zip 路径）。
pub fn import_skill_bundle(
    zip_bytes: &[u8],
    skill_name: &str,
    skill_dir: &Path,
) -> Result<ImportedSkill, DslError> {
    if zip_bytes.len() > MAX_SKILL_BYTES {
        return Err(DslError::Other("Skill 文件过大".into()));
    }

    let skill_id = slugify_skill_name(skill_name);
    if skill_id.is_empty() {
        return Err(DslError::Other("skill_name 无效".into()));
    }

    // 找一个不冲突的目标目录（对应 Python _dedupe_dir）
    let target = dedupe_dir(skill_dir, &skill_id);
    std::fs::create_dir_all(&target)?;

    // 解压 zip
    let cursor = std::io::Cursor::new(zip_bytes);
    let mut archive = zip::ZipArchive::new(cursor)
        .map_err(|e| DslError::Other(format!("zip open: {e}")))?;

    // 先找 SKILL.md 所在前缀
    let skill_md_entry = (0..archive.len())
        .filter_map(|i| {
            archive.by_index(i).ok().and_then(|f| {
                if f.name().ends_with("SKILL.md") {
                    Some(f.name().to_owned())
                } else {
                    None
                }
            })
        })
        .next();
    let Some(skill_md_path) = skill_md_entry else {
        std::fs::remove_dir_all(&target).ok();
        return Err(DslError::Other("导入包里没有 SKILL.md".into()));
    };

    let root_prefix = std::path::Path::new(&skill_md_path)
        .parent()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default();

    let mut total_size: usize = 0;
    let mut extracted_count: usize = 0;

    for i in 0..archive.len() {
        let mut file = archive
            .by_index(i)
            .map_err(|e| DslError::Other(format!("zip entry {i}: {e}")))?;

        let member = file.name().to_owned();
        let member_path = std::path::Path::new(&member);

        // 安全检查：跳过绝对路径、path traversal、目录条目
        if member_path.is_absolute()
            || member_path.components().any(|c| c.as_os_str() == "..")
            || member.ends_with('/')
        {
            continue;
        }

        extracted_count += 1;
        total_size += file.size() as usize;

        if extracted_count > MAX_SKILL_FILES || total_size > MAX_SKILL_UNPACKED_BYTES {
            std::fs::remove_dir_all(&target).ok();
            return Err(DslError::Other("Skill 压缩包展开后过大".into()));
        }

        // 去掉公共前缀
        let relative = if !root_prefix.is_empty() && !matches!(root_prefix.as_str(), "." | "")
            && member.starts_with(&format!("{root_prefix}/"))
        {
            std::path::PathBuf::from(&member[root_prefix.len() + 1..])
        } else {
            member_path.to_path_buf()
        };

        let out_path = target.join(&relative);
        if let Some(parent) = out_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let mut content = Vec::new();
        std::io::Read::read_to_end(&mut file, &mut content)?;
        std::fs::write(&out_path, &content)?;
    }

    // 再次确认 SKILL.md 存在
    let skill_file = target.join("SKILL.md");
    if !skill_file.exists() {
        std::fs::remove_dir_all(&target).ok();
        return Err(DslError::Other("解压后 SKILL.md 不存在".into()));
    }

    let skill_title = read_skill_title(&skill_file).unwrap_or_else(|| target.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| skill_id.clone()));

    Ok(ImportedSkill {
        id: target
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| skill_id.clone()),
        name: skill_title,
        path: skill_file.to_string_lossy().into_owned(),
        enabled: true,
    })
}

// ── 内部辅助 ──────────────────────────────────────────────────────────────────

fn slugify_skill_name(s: &str) -> String {
    let s = s.trim().to_lowercase();
    // 取文件名部分（去掉扩展名）
    let stem = std::path::Path::new(&s)
        .file_stem()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or(s.clone());
    let slug: String = stem
        .chars()
        .map(|c| if c.is_alphanumeric() || c == '-' { c } else { '_' })
        .collect();
    slug.trim_matches('_').to_owned()
}

fn dedupe_dir(base: &Path, name: &str) -> PathBuf {
    let candidate = base.join(name);
    if !candidate.exists() {
        return candidate;
    }
    for i in 1..=999 {
        let c = base.join(format!("{name}_{i}"));
        if !c.exists() {
            return c;
        }
    }
    base.join(format!("{name}_new"))
}

fn read_skill_title(skill_md: &Path) -> Option<String> {
    let content = std::fs::read_to_string(skill_md).ok()?;
    for line in content.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("# ") {
            return Some(trimmed[2..].trim().to_owned());
        }
    }
    None
}
