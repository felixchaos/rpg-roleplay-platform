"""scripts/dev.sh 的 check_db:连接串从哪来、什么时候拦、什么时候只提示。

背景:开源线早先加的 check_db 只认 rpg/.env —— 文件不在就直接 exit 1。可后端取连接串的
来源不止它:仓库根 .env、shell 环境变量都会被读到,哪里都没配时还有默认库
postgresql:///rpg_platform。于是「只用仓库根 .env」或「直接用默认库」的开发机,
dev.sh start 会被一个并不存在的问题拦死。

锁的不变量(与 platform_app/db/connection.py 的取法、app.py 的 .env 加载语义对齐):
  · rpg/.env、仓库根 .env、环境变量三处任一处配了 DATABASE_URL 都认;
  · 同一个键的优先级 rpg/.env > shell 环境变量 > 仓库根 .env(app.py 先以 override=False
    加载仓库根 .env、再以 override=True 加载 rpg/.env);写了但值为空也算设了,会遮住后面的来源;
  · 配了但连不上 → start 必须拦下(否则后端在 psycopg 重试里挂 300s);
  · 哪里都没配 → 回落默认库,只提示、不拦;默认库连不上也只提示;
  · 连接串里的密码不回显到终端:URL 的 user:pass@(密码里带 @ 也算)、查询参数 password=、
    libpq 的 key=value 形式(password=... / password='...')都要遮。

做法:把 dev.sh 拷进临时目录树,用桩替换 lsof / curl / psql,只跑到 start_backend
(临时树里没有 .venv,必然在那一步停),不起任何真实进程。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEV_SH = REPO / "scripts" / "dev.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="需要 bash")

_BLOCKED = "数据库未就绪"
_REACHED_BACKEND = ".venv/bin/uvicorn 不存在"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _run(tmp_path: Path, cmd: str, *, rpg_env: str | None = None, root_env: str | None = None,
         shell_env: dict[str, str] | None = None, psql_ok: bool = True):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "rpg").mkdir()
    (root / "frontend").mkdir()
    shutil.copy2(DEV_SH, root / "scripts" / "dev.sh")
    if rpg_env is not None:
        (root / "rpg" / ".env").write_text(rpg_env, encoding="utf-8")
    if root_env is not None:
        (root / ".env").write_text(root_env, encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    psql_log = tmp_path / "psql.log"
    # 只有 Postgres 端口在听;后端 / 前端端口都空着
    _stub(bin_dir, "lsof", 'case "$*" in *:5432*) echo 4242 ;; esac\nexit 0\n')
    _stub(bin_dir, "curl", "echo 000\n")
    _stub(bin_dir, "psql", f'printf "%s\\n" "$1" >> "{psql_log}"\nexit {0 if psql_ok else 2}\n')

    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C.UTF-8"}
    env.update(shell_env or {})
    proc = subprocess.run(
        ["bash", str(root / "scripts" / "dev.sh"), cmd],
        env=env, capture_output=True, text=True, timeout=60,
    )
    urls = psql_log.read_text(encoding="utf-8").splitlines() if psql_log.exists() else []
    return proc, urls


def test_nothing_configured_falls_back_to_default_db_without_blocking(tmp_path):
    proc, urls = _run(tmp_path, "start")
    assert _BLOCKED not in proc.stdout, proc.stdout
    assert "postgresql:///rpg_platform" in proc.stdout, "要告诉用户后端会用哪个默认库"
    assert _REACHED_BACKEND in proc.stdout, "没配连接串不该挡住 start"
    assert urls == ["postgresql:///rpg_platform"]


def test_default_db_unreachable_only_warns(tmp_path):
    proc, _ = _run(tmp_path, "start", psql_ok=False)
    assert _BLOCKED not in proc.stdout, proc.stdout
    assert "setup.sh" in proc.stdout, "默认库连不上时要给出下一步指引"
    assert _REACHED_BACKEND in proc.stdout


def test_root_env_is_recognized_and_password_is_redacted(tmp_path):
    url = "postgresql://rpg:s3cretpw@127.0.0.1:5432/rpg"
    proc, urls = _run(tmp_path, "status", root_env=f"DATABASE_URL={url}\n")
    assert urls == [url]
    assert "仓库根 .env" in proc.stdout
    assert "s3cretpw" not in proc.stdout


def test_shell_env_is_recognized(tmp_path):
    url = "postgresql:///from_shell"
    proc, urls = _run(tmp_path, "status", shell_env={"DATABASE_URL": url})
    assert urls == [url]
    assert "环境变量" in proc.stdout


def test_rpg_env_wins_over_root_env(tmp_path):
    _, urls = _run(tmp_path, "status",
                   rpg_env="DATABASE_URL=postgresql:///from_rpg_env\n",
                   root_env="DATABASE_URL=postgresql:///from_root_env\n")
    assert urls == ["postgresql:///from_rpg_env"]


def test_export_prefix_quotes_and_crlf_are_tolerated(tmp_path):
    _, urls = _run(tmp_path, "status", rpg_env='export DATABASE_URL="postgresql:///quoted"\r\n')
    assert urls == ["postgresql:///quoted"]


def test_postgres_url_is_a_fallback_key(tmp_path):
    """后端在 DATABASE_URL 缺席时认 POSTGRES_URL,这里也得认,否则会误报「没配」。"""
    _, urls = _run(tmp_path, "status", rpg_env="POSTGRES_URL=postgresql:///via_postgres_url\n")
    assert urls == ["postgresql:///via_postgres_url"]


def test_configured_but_unreachable_blocks_start(tmp_path):
    proc, _ = _run(tmp_path, "start", rpg_env="DATABASE_URL=postgresql:///not_created\n",
                   psql_ok=False)
    assert proc.returncode == 1
    assert _BLOCKED in proc.stdout, proc.stdout
    assert _REACHED_BACKEND not in proc.stdout


# ── 巡检第二轮整合审查 ────────────────────────────────────────────────────────
# 一、优先级:以前按 rpg/.env → 仓库根 .env → 环境变量找,可 app.py 加载仓库根 .env 时
# override=False —— shell 里 export 的值优先于仓库根 .env。仓库根 .env 留着一条过期连接串时,
# dev.sh start 去测过期的串、报「连不上」exit 1,后端其实会用 shell 那条、本来能起来。

def test_shell_env_wins_over_root_env(tmp_path):
    proc, urls = _run(tmp_path, "start",
                      root_env="DATABASE_URL=postgresql:///stale_root\n",
                      shell_env={"DATABASE_URL": "postgresql:///from_shell"})
    assert urls == ["postgresql:///from_shell"], urls
    assert "环境变量" in proc.stdout
    assert _REACHED_BACKEND in proc.stdout


def test_rpg_env_wins_over_shell_env(tmp_path):
    """rpg/.env 以 override=True 加载,连 shell 里 export 的也盖掉。"""
    _, urls = _run(tmp_path, "status",
                   rpg_env="DATABASE_URL=postgresql:///from_rpg_env\n",
                   shell_env={"DATABASE_URL": "postgresql:///from_shell"})
    assert urls == ["postgresql:///from_rpg_env"]


def test_empty_value_masks_later_sources(tmp_path):
    """写了但值为空也算设了(python-dotenv 照样写进环境):rpg/.env 的 DATABASE_URL= 会把 shell 的
    盖成空串,后端于是落到下一个键;shell 里 export 了空值,仓库根 .env 的同名键也不会生效。"""
    _, urls = _run(tmp_path, "status",
                   rpg_env="DATABASE_URL=\nPOSTGRES_URL=postgresql:///via_pg\n",
                   shell_env={"DATABASE_URL": "postgresql:///from_shell"})
    assert urls == ["postgresql:///via_pg"]
    _, urls = _run(tmp_path / "b", "status",
                   root_env="DATABASE_URL=postgresql:///from_root\n",
                   shell_env={"DATABASE_URL": ""})
    assert urls == ["postgresql:///rpg_platform"]


# 二、脱敏:以前只处理 scheme://user:pass@host 一种形式。

@pytest.mark.parametrize("url, secret, keep", [
    ("host=/tmp dbname=rpg password=s3cretpw", "s3cretpw", "dbname=rpg"),
    ("host=/tmp password = s3cretpw dbname=rpg", "s3cretpw", "dbname=rpg"),
    ("host=/tmp password='s3cret pw' dbname=rpg", "s3cret", "dbname=rpg"),
    ("host=/tmp sslpassword=s3cretpw dbname=rpg", "s3cretpw", "dbname=rpg"),
    ("postgresql:///rpg?password=s3cretpw&sslmode=require", "s3cretpw", "sslmode=require"),
    ("postgresql://rpg@127.0.0.1/rpg?sslmode=disable&password=s3cretpw", "s3cretpw", "127.0.0.1"),
    ("postgresql://rpg:s3c@retpw@127.0.0.1:5432/rpg", "retpw", "127.0.0.1:5432/rpg"),
    ("postgresql://rpg:s3c?ret@127.0.0.1/rpg", "ret", "127.0.0.1/rpg"),
])
def test_password_forms_are_redacted(tmp_path, url, secret, keep):
    proc, urls = _run(tmp_path, "status", shell_env={"DATABASE_URL": url})
    assert urls == [url], "测连通性用的是原串,只有回显脱敏"
    out = proc.stdout + proc.stderr
    assert secret not in out, out
    assert keep in out, out


def test_redaction_keeps_passwordless_urls_intact(tmp_path):
    url = "postgresql://rpg@127.0.0.1:5432/rpg?application_name=dev"
    proc, _ = _run(tmp_path, "status", shell_env={"DATABASE_URL": url})
    assert url in proc.stdout
