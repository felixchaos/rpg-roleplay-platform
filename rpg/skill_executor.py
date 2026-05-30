"""
skill_executor.py — Skill 沙箱执行

设计目标（来自交接 TODO #10）：
- subprocess 隔离：禁止访问主项目 working tree
- 资源限制：CPU 时间、内存、文件大小、open files
- 超时：默认 30s，最大 300s
- stdout/stderr 捕获 + 截断
- 临时工作目录：执行完自动清理
- 输出大小限制：避免 stdout 炸内存
- 环境变量白名单：只传必要的 PATH 和 LANG

不防御的事：
- 网络访问（靠容器/防火墙层面）
- 提权（依赖系统 user 隔离）
- 反沙箱攻击（这是单机本地用的工具，不是面对未知用户的）

如果以后要做面向外部用户的部署，建议外层用 Docker / nsjail / firejail 二次隔离。
"""
from __future__ import annotations

import os
import resource
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

# ── 限制常量 ──────────────────────────────────────────────────────────────────
DEFAULT_TIMEOUT_SEC = 30
MAX_TIMEOUT_SEC = 300
MAX_STDOUT_BYTES = 1 * 1024 * 1024   # 1 MB
MAX_STDERR_BYTES = 256 * 1024         # 256 KB
RLIMIT_CPU_SEC = 60                   # CPU 时间硬限制（秒）
RLIMIT_AS_BYTES = 512 * 1024 * 1024   # 地址空间（虚拟内存）512 MB
RLIMIT_FSIZE_BYTES = 16 * 1024 * 1024 # 单文件最大 16 MB
RLIMIT_NOFILE = 64                    # open files


# 环境变量白名单：只传这些，其他全砍掉。
# 不含 HOME/USER/SHELL —— 防 skill 顺 $HOME 读 ~/.ssh、~/.aws、shell rc 等宿主敏感文件
# (CWE-668)。HOME 由 run_skill_command 显式指向临时 workdir。
_ENV_ALLOW = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR"}


def _build_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOW}
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    if extra:
        # 显式 extra 优先（用于 Skill 自己声明的环境）
        for k, v in extra.items():
            if isinstance(v, str) and len(v) < 4096:
                env[k] = v
    return env


def _preexec_setrlimit():
    """子进程 fork 后、exec 前调用。设置资源限制。"""
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (RLIMIT_CPU_SEC, RLIMIT_CPU_SEC))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (RLIMIT_AS_BYTES, RLIMIT_AS_BYTES))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (RLIMIT_NOFILE, RLIMIT_NOFILE))
    except Exception:
        pass
    # 与父进程脱离会话，防止 Ctrl+C 误传
    try:
        os.setsid()
    except Exception:
        pass


def run_skill_command(
    cmd: list[str],
    *,
    skill_root: Path | str,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    stdin_text: str | None = None,
    extra_env: dict[str, str] | None = None,
    allow_write: bool = True,
) -> dict[str, Any]:
    """
    在沙箱里跑一条 Skill 命令。

    Args:
        cmd: 进程命令行（已分词，避免 shell 注入）
        skill_root: skill 包根目录，会被复制到临时工作目录里执行
        timeout_sec: 超时秒数，封顶 MAX_TIMEOUT_SEC
        stdin_text: 可选 stdin
        extra_env: 额外环境变量（白名单外的不会传入，仅 skill 显式需要的）
        allow_write: 是否允许写入临时目录（false 时只读 mount via cp 后 chmod）

    Returns:
        {
            "ok": True/False,
            "exit_code": int,
            "stdout": str (truncated),
            "stderr": str (truncated),
            "duration_ms": int,
            "truncated_stdout": bool,
            "truncated_stderr": bool,
            "timeout": bool,
            "error": str (if any),
        }
    """
    timeout_sec = max(1, min(int(timeout_sec or DEFAULT_TIMEOUT_SEC), MAX_TIMEOUT_SEC))
    skill_root = Path(skill_root).resolve()
    if not skill_root.exists() or not skill_root.is_dir():
        return {"ok": False, "error": f"skill_root 不存在: {skill_root}", "exit_code": -1}

    # 创建临时执行目录，把 skill 文件复制进去
    workdir = Path(tempfile.mkdtemp(prefix="skill_exec_"))
    try:
        # 拷贝 skill 文件（避免污染原目录，也避免脚本读到 .env 等）
        for item in skill_root.iterdir():
            if item.is_dir():
                shutil.copytree(item, workdir / item.name)
            else:
                shutil.copy2(item, workdir / item.name)
        if not allow_write:
            # 把所有文件设为只读
            for p in workdir.rglob("*"):
                try:
                    os.chmod(p, 0o555 if p.is_dir() else 0o444)
                except Exception:
                    pass

        env = _build_env(extra_env)
        env["SKILL_WORKDIR"] = str(workdir)
        # HOME 指向临时工作目录,而非宿主用户家目录(隔离 ~/.ssh / ~/.aws / shell rc)
        env["HOME"] = str(workdir)

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(workdir),
                env=env,
                stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=_preexec_setrlimit,
                start_new_session=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError:
            return {"ok": False, "error": f"找不到可执行文件: {cmd[0]}", "exit_code": -1}
        except Exception as e:
            return {"ok": False, "error": f"启动失败: {e}", "exit_code": -1}

        timeout_hit = False
        try:
            out, err = proc.communicate(input=stdin_text, timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timeout_hit = True
            try:
                # 先 SIGTERM 整个进程组，再 SIGKILL
                os.killpg(os.getpgid(proc.pid), 15)
                proc.wait(timeout=2)
            except Exception:
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                except Exception:
                    pass
            out, err = proc.communicate()

        duration_ms = int((time.monotonic() - start) * 1000)
        out = out or ""
        err = err or ""
        truncated_stdout = len(out) > MAX_STDOUT_BYTES
        truncated_stderr = len(err) > MAX_STDERR_BYTES
        if truncated_stdout:
            out = out[:MAX_STDOUT_BYTES] + "\n...[stdout truncated]"
        if truncated_stderr:
            err = err[:MAX_STDERR_BYTES] + "\n...[stderr truncated]"

        return {
            "ok": (proc.returncode == 0) and not timeout_hit,
            "exit_code": int(proc.returncode if proc.returncode is not None else -1),
            "stdout": out,
            "stderr": err,
            "duration_ms": duration_ms,
            "truncated_stdout": truncated_stdout,
            "truncated_stderr": truncated_stderr,
            "timeout": timeout_hit,
        }
    finally:
        # 清理临时目录
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except Exception:
            pass


def run_python_skill(
    skill_root: Path | str,
    entrypoint: str = "main.py",
    args: list[str] | None = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    stdin_text: str | None = None,
) -> dict[str, Any]:
    """便捷封装：跑 Python 脚本类型的 Skill。"""
    skill_root_p = Path(skill_root)
    entry = skill_root_p / entrypoint
    if not entry.exists():
        return {"ok": False, "error": f"找不到 entrypoint: {entry}", "exit_code": -1}
    cmd = ["python3", entrypoint] + list(args or [])
    return run_skill_command(cmd, skill_root=skill_root, timeout_sec=timeout_sec, stdin_text=stdin_text)


def smoke_test() -> dict[str, Any]:
    """自检：跑一个简单 echo 测试沙箱基础设施"""
    tmp = Path(tempfile.mkdtemp(prefix="skill_smoke_"))
    try:
        (tmp / "SKILL.md").write_text("# Test Skill\n", encoding="utf-8")
        (tmp / "hello.sh").write_text("#!/bin/bash\necho \"hello $1\"\n", encoding="utf-8")
        os.chmod(tmp / "hello.sh", 0o755)
        result = run_skill_command(
            ["bash", "hello.sh", "world"],
            skill_root=tmp,
            timeout_sec=5,
        )
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
