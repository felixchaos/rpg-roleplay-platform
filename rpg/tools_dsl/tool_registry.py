"""
tool_registry.py - MCP server and local skill registry.

Local deployments may import skills and edit MCP server launch configs. Hosted
server deployments can set RPG_DEPLOYMENT_MODE=server to expose read-only tool
metadata without allowing arbitrary code/config imports to non-admin users.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from core.text import slugify

BASE = Path(__file__).parent.parent  # rpg/tools_dsl/ → rpg/
CONFIG_DIR = BASE / "config"
MCP_CONFIG_FILE = CONFIG_DIR / "mcp_servers.json"
USER_SKILL_DIR = BASE / "user_skills"
MAX_SKILL_BYTES = 2 * 1024 * 1024
MAX_SKILL_FILES = 80
MAX_SKILL_UNPACKED_BYTES = 4 * 1024 * 1024

# 无预置插件 — 旧列表是早期 demo 数据（Documents/Spreadsheets/Chrome 等 Claude connector
# 风格名称），与 RPG Roleplay 实际功能无关，上线前清空。
# 若将来接入真实插件市场，在此追加或从 DB/config 动态加载。
DEFAULT_PLUGIN_TOOLS: list[dict] = []

DEFAULT_MCP_CATALOG = {
    "schema_version": 1,
    "servers": [],
}


def deployment_capabilities() -> dict[str, Any]:
    from core.config import (
        enable_mcp_config_write as _enable_mcp_config_write,
    )
    from core.config import (
        enable_skill_import as _enable_skill_import,
    )
    from core.config import (
        is_local_mode as _is_local_mode,
    )
    mode = "local" if _is_local_mode() else "server"
    is_local = _is_local_mode()
    allow_skill = _enable_skill_import()
    skill_import_enabled = is_local if allow_skill is None else allow_skill == "1"
    allow_mcp_write = _enable_mcp_config_write()
    mcp_config_write_enabled = is_local if allow_mcp_write is None else allow_mcp_write == "1"
    return {
        "deployment_mode": mode,
        "skill_import_enabled": skill_import_enabled,
        "mcp_config_write_enabled": mcp_config_write_enabled,
        "mcp_enabled": True,
    }


def tool_payload() -> dict[str, Any]:
    return {
        "capabilities": deployment_capabilities(),
        "plugins": copy.deepcopy(DEFAULT_PLUGIN_TOOLS),
        "mcp": load_mcp_catalog(),
        "skills": list_imported_skills(),
    }


def load_mcp_catalog() -> dict[str, Any]:
    db_catalog = _load_mcp_catalog_from_db()
    if db_catalog is not None:
        if not db_catalog.get("servers"):
            file_catalog = _load_mcp_catalog_from_file()
            if file_catalog.get("servers"):
                save_mcp_catalog(file_catalog)
                return file_catalog
        return db_catalog
    return _load_mcp_catalog_from_file()


def _load_mcp_catalog_from_file() -> dict[str, Any]:
    # 只读 seed:仓库内 config/mcp_servers.json 仅作默认种子,运行时绝不写它(见 save_mcp_catalog)。
    # 缺失时直接用内置默认,不落盘 → 不污染 git 工作树。
    if not MCP_CONFIG_FILE.exists():
        return copy.deepcopy(DEFAULT_MCP_CATALOG)
    try:
        with open(MCP_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    return _migrate_mcp_catalog(data, validate=False)


def save_mcp_catalog(catalog: dict[str, Any]) -> None:
    # 权威存储是 DB(mcp_servers 表):多 worker 读同一真相源、写走 server_id 行级 upsert。
    # **不再写 config/mcp_servers.json** —— 运行时写它会造成 git churn(用户反馈:创建 MCP server
    # 后该文件进 git 改动列表)+ 部署互相覆盖 + 多 worker 文件不一致。该 JSON 退化为只读 seed
    # (DB 为空 / 无 DB 时兜底,见 load_mcp_catalog)。与 model_registry.save_model_catalog 同策略。
    _save_mcp_catalog_to_db(catalog)


def upsert_mcp_server(server: dict[str, Any]) -> dict[str, Any]:
    if not deployment_capabilities()["mcp_config_write_enabled"]:
        raise PermissionError("当前部署模式不允许写入 MCP 服务器配置")
    catalog = load_mcp_catalog()
    normalized = _normalize_mcp_server(server)
    existing = next((item for item in catalog["servers"] if item["id"] == normalized["id"]), None)
    if existing:
        existing.clear()
        existing.update(normalized)
    else:
        catalog["servers"].append(normalized)
    save_mcp_catalog(catalog)
    return load_mcp_catalog()


def set_mcp_server_enabled(server_id: str, enabled: bool) -> dict[str, Any]:
    if not deployment_capabilities()["mcp_config_write_enabled"]:
        raise PermissionError("当前部署模式不允许写入 MCP 服务器配置")
    catalog = load_mcp_catalog()
    for server in catalog["servers"]:
        if server["id"] == server_id:
            server["enabled"] = bool(enabled)
            break
    save_mcp_catalog(catalog)
    return load_mcp_catalog()


def delete_mcp_server(server_id: str) -> dict[str, Any]:
    if not deployment_capabilities()["mcp_config_write_enabled"]:
        raise PermissionError("当前部署模式不允许写入 MCP 服务器配置")
    catalog = load_mcp_catalog()
    catalog["servers"] = [server for server in catalog["servers"] if server["id"] != server_id]
    save_mcp_catalog(catalog)
    return load_mcp_catalog()


def validate_mcp_server(server_id: str) -> dict[str, Any]:
    catalog = load_mcp_catalog()
    server = next((item for item in catalog["servers"] if item["id"] == server_id), None)
    if not server:
        raise ValueError(f"未知 MCP 服务器：{server_id}")

    transport = server.get("transport", "stdio")
    command = server.get("command", "")
    url = server.get("url", "")

    if transport == "http":
        # HTTP transport: 验证 URL 格式
        ready_to_launch = bool(url and (url.startswith("http://") or url.startswith("https://")))
        return {
            "id": server_id,
            "transport": "http",
            "url": url,
            "command": "",
            "command_resolved": None,
            "ready_to_launch": ready_to_launch,
        }
    else:
        # stdio transport: 验证 command 是否存在
        resolved = shutil.which(command) if command else None
        return {
            "id": server_id,
            "transport": "stdio",
            "command": command,
            "command_resolved": resolved,
            "ready_to_launch": bool(resolved and transport == "stdio"),
        }


def list_imported_skills() -> list[dict[str, Any]]:
    USER_SKILL_DIR.mkdir(parents=True, exist_ok=True)
    db_skills = _load_skills_from_db()
    if db_skills is not None:
        if not db_skills:
            fs_skills = _scan_skill_dir()
            for skill in fs_skills:
                _save_skill_to_db(skill)
            return fs_skills
        return db_skills
    return _scan_skill_dir()


def _scan_skill_dir() -> list[dict[str, Any]]:
    skills = []
    for path in sorted(USER_SKILL_DIR.iterdir()):
        skill_file = path / "SKILL.md"
        if not path.is_dir() or not skill_file.exists():
            continue
        skills.append({
            "id": path.name,
            "name": _skill_title(skill_file) or path.name,
            "path": str(skill_file),
            "enabled": True,
        })
    return skills


def import_skill_bundle(item: dict[str, Any]) -> dict[str, Any]:
    if not deployment_capabilities()["skill_import_enabled"]:
        raise PermissionError("当前部署模式不允许导入 Skill")
    name = Path(str(item.get("name") or "skill.md")).name
    data = _decode_upload(item)
    if len(data) > MAX_SKILL_BYTES:
        raise ValueError("Skill 文件过大")
    USER_SKILL_DIR.mkdir(parents=True, exist_ok=True)
    skill_id = _slugify(Path(name).stem or "skill")
    target = _dedupe_dir(USER_SKILL_DIR / skill_id)
    target.mkdir(parents=True, exist_ok=False)
    if name.lower().endswith(".zip"):
        _extract_skill_zip(data, target)
    else:
        (target / "SKILL.md").write_bytes(data)
    skill_file = target / "SKILL.md"
    if not skill_file.exists():
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("导入包里没有 SKILL.md")
    skill = {
        "id": target.name,
        "name": _skill_title(skill_file) or target.name,
        "path": str(skill_file),
        "enabled": True,
    }
    _save_skill_to_db(skill)
    return skill


def _migrate_mcp_catalog(data: dict[str, Any], *, validate: bool = True) -> dict[str, Any]:
    catalog = copy.deepcopy(DEFAULT_MCP_CATALOG)
    if isinstance(data, dict) and isinstance(data.get("servers"), list):
        catalog["servers"] = [_normalize_mcp_server(item, validate=validate) for item in data["servers"]]
    catalog["schema_version"] = 1
    return catalog


_MCP_CMD_WHITELIST = {"python3", "python", "node", "npx"}
# SEC(C-1): 旧实现有 _MCP_CMD_SAFE_RE = ^[a-zA-Z0-9_-]{1,32}$ 兜底,会把 bash/sh/curl/nc/socat
# 当合法命令放过 → 配合 self-hosted 模式 admin 短路 = 未认证 RCE。现彻底删除正则兜底:
# command 必须严格命中白名单,且每种解释器都校验 args 禁止内联代码执行。
_MCP_PY_FORBIDDEN_FLAGS = {"-c", "-e"}                          # python 内联代码执行
_MCP_NODE_FORBIDDEN_FLAGS = {"-e", "--eval", "-p", "--print"}   # node 内联代码执行


def _validate_interpreter_args(command: str, args: list[str]) -> None:
    """python/node 解释器禁止内联代码执行 flag(-c/-e/-p),防止 `python3 -c '...'` RCE。

    MCP server 只应以模块(`-m pkg`)或脚本(`server.py`)形式启动,绝不接受内联代码。
    """
    forbidden = _MCP_PY_FORBIDDEN_FLAGS if command in {"python", "python3"} else _MCP_NODE_FORBIDDEN_FLAGS
    for raw in args:
        a = str(raw)
        if any(a == f or a.startswith(f) for f in forbidden):
            raise ValueError(f"{command} 禁用内联执行 flag: {a!r}(MCP server 必须以模块/脚本形式启动)")

# P1-3 SEC: npx 专项 args 校验 ─────────────────────────────────────────────
# 允许的包名：@modelcontextprotocol/<slug> 或普通小写短名
_MCP_NPX_PACKAGE_RE = re.compile(
    r"^(@modelcontextprotocol/[a-z0-9][a-z0-9\-]{0,63}|[a-z][a-z0-9\-]{1,32})$"
)
# 禁止的 flag —— 可用于下载任意包或执行任意命令
_MCP_NPX_FORBIDDEN_FLAGS = {
    "--package", "-p",
    "--call", "-c",
    "--ignore-existing",
    "-y", "--yes",
}


def _validate_npx_args(args: list[str]) -> None:
    """npx 调用必须是 npx <package> [sub-args …].

    规则：
    - args[0] 必须是包名（匹配白名单正则），不能是 flag。
    - 任意位置出现禁用 flag（--package / -p / -c / --call 等）立即拒绝。
    - 允许包名后跟子命令字符串（传给该包内部），但禁止 -- 后跟 shell 分隔。
    """
    if not args:
        raise ValueError("npx 至少需要 1 个参数（包名）")
    pkg = args[0]
    if pkg.startswith("-"):
        raise ValueError(
            f"npx 第一个参数必须是包名，不能是 flag: {pkg!r}"
        )
    if not _MCP_NPX_PACKAGE_RE.match(pkg):
        raise ValueError(
            f"npx 包名不在白名单（要求 @modelcontextprotocol/<slug> 或 [a-z][a-z0-9-]{{1,32}}）: {pkg!r}"
        )
    for arg in args:
        # 精确匹配（如 --package）或前缀匹配（如 --package=evil）
        if arg in _MCP_NPX_FORBIDDEN_FLAGS or any(
            arg.startswith(f"{flag}=") for flag in _MCP_NPX_FORBIDDEN_FLAGS
        ):
            raise ValueError(
                f"npx 禁用 flag: {arg!r}（该 flag 可下载并执行任意代码）"
            )


def _mcp_display_name(server: dict[str, Any]) -> str:
    # 前端「新增 MCP 服务器」表单发的是 name(web / 手机都是);存量 / 种子配置用 display_name。
    return str(server.get("display_name") or server.get("name") or "").strip()


# 从名字派生的 id 的 ASCII 部分最长多少。原生工具名 = `<server_id>__<工具名>` 截到 64,
# MCP 工具名常见 20 来个字符(list_allowed_directories 24),id 超长会把分隔符和工具名截掉。
_MCP_DERIVED_ID_MAX = 32


def _mcp_id_from_name(name: str) -> str:
    """把显示名变成能在原生工具调用里来回编解码的 id:只含 [a-z0-9-]、不长。

    三个 backend 把工具名编成「server_id 里 [A-Za-z0-9_-] 以外换成下划线」+ "__" + 工具名,
    模型调回来时按第一个 "__" 拆出 server_id(见 agents/gm/backends/_tiered.tool_full_name)。
    id 里有中文(变成一串下划线)、有 "__"、以下划线结尾、或长到被截断,都拆不回原 id,
    工具调用一律「server_id 不存在」。所以这里不留下划线(也就不会有 "__"),非 ASCII 字符丢掉;
    丢过字符或截过长度时补一段名字的短哈希,让不同名字仍得到不同 id(纯中文名 → mcp-<哈希>)。
    """
    raw = name.strip()
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    lossy = any(ord(ch) > 127 for ch in raw) or len(ascii_slug) > _MCP_DERIVED_ID_MAX
    if not lossy:
        return ascii_slug
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    head = ascii_slug[: _MCP_DERIVED_ID_MAX - len(digest) - 1].strip("-") or "mcp"
    return f"{head}-{digest}"


def mcp_server_id(server: dict[str, Any]) -> str:
    """该配置落库后的 server id(与 _normalize_mcp_server 同一口径)。

    以前只看 id / display_name,表单发的 name 被丢掉 → 所有新服务器 id 都是 "mcp_server",
    加第二个就覆盖第一个。upsert 路由用它把 id 回给前端,前端拿 id 去校验。

    显式给了 id(编辑时带回的、库里存量的)照旧只做 slugify,不重新派生 —— 否则存量服务器
    一编辑就换 id。没给 id 时从名字派生,规则见 _mcp_id_from_name。
    """
    explicit = str(server.get("id") or "").strip()
    if explicit:
        return _slugify(explicit)
    name = _mcp_display_name(server)
    return (_mcp_id_from_name(name) if name else "") or "mcp_server"


def _normalize_mcp_server(server: dict[str, Any], *, validate: bool = True) -> dict[str, Any]:
    server_id = mcp_server_id(server)
    transport = str(server.get("transport") or "stdio").strip()

    # HTTP transport 配置
    url = str(server.get("url") or "").strip()
    headers = server.get("headers") or {}
    if not isinstance(headers, dict):
        headers = {}

    # stdio transport 配置
    args = server.get("args") or []
    if isinstance(args, str):
        args = [part for part in args.split(" ") if part]
    env = server.get("env") or {}
    if not isinstance(env, dict):
        env = {}
    command = str(server.get("command") or "").strip()
    # 表单的命令框是一整行(「npx @modelcontextprotocol/server-filesystem /data」)。没单独给 args
    # 时按空白拆成命令 + 参数,否则整行被当命令名去比白名单,带参数的 stdio 服务器一律被拒。
    # 拆完照样走下面的白名单与参数校验,安全边界不变。
    if command and not args and len(command.split()) > 1:
        command, *args = command.split()

    # 根据 transport 类型进行不同的验证（validate=False 用于加载已有配置时跳过校验）
    if transport == "http" and validate:
        # HTTP transport: 需要 URL，不需要 command
        if not url:
            raise ValueError("HTTP transport 的 MCP server 必须提供 url")
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"MCP server URL 必须以 http:// 或 https:// 开头: {url!r}")
    else:
        # stdio transport: 需要 command，不需要 url
        # P1-2 SEC: command 白名单 + 安全字符集校验（禁止 / 和 ..）
        if command:
            if "/" in command or ".." in command:
                raise ValueError(f"MCP server command 不能包含路径分隔符: {command!r}")
            if command not in _MCP_CMD_WHITELIST:
                raise ValueError(f"MCP server command 不在白名单(仅允许 {sorted(_MCP_CMD_WHITELIST)}): {command!r}")
            # P1-3 SEC + C-1: 每种命令都校验 args,禁止内联代码执行(npx evil-pkg / python -c / node -e)
            if command == "npx":
                _validate_npx_args([str(a) for a in args])
            else:
                _validate_interpreter_args(command, [str(a) for a in args])

    return {
        "id": server_id,
        "display_name": _mcp_display_name(server) or server_id,
        "transport": transport,
        "command": command,
        "args": [str(item) for item in args],
        "env": {str(k): str(v) for k, v in env.items()},
        "enabled": bool(server.get("enabled", False)),
        "scope": str(server.get("scope") or "local").strip(),
        "url": url,
        "headers": {str(k): str(v) for k, v in headers.items()},
    }


def _load_mcp_catalog_from_db() -> dict[str, Any] | None:
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            rows = db.execute("select * from mcp_servers order by server_id").fetchall()
        return {
            "schema_version": 1,
            "servers": [
                _normalize_mcp_server(
                    {
                        "id": row["server_id"],
                        "display_name": row["display_name"],
                        "transport": row["transport"],
                        "command": row["command"],
                        "args": list(row.get("args") or []),
                        "env": dict(row.get("env") or {}),
                        "enabled": row["enabled"],
                        "scope": row["scope"],
                        "url": (dict(row.get("metadata") or {})).get("url", ""),
                        "headers": (dict(row.get("metadata") or {})).get("headers", {}),
                    },
                    validate=False,
                )
                for row in rows
            ],
        }
    except Exception:
        return None


def _save_mcp_catalog_to_db(catalog: dict[str, Any]) -> None:
    try:
        from platform_app.db import connect, init_db

        init_db()
        catalog = _migrate_mcp_catalog(catalog, validate=False)
        with connect() as db:
            db.execute("delete from mcp_servers")
            for server in catalog.get("servers", []):
                metadata: dict[str, Any] = {}
                if server.get("url"):
                    metadata["url"] = server["url"]
                if server.get("headers"):
                    metadata["headers"] = server["headers"]
                db.execute(
                    """
                    insert into mcp_servers(server_id, display_name, transport, command, args, env, enabled, scope, metadata)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict(server_id) do update set
                      display_name = excluded.display_name,
                      transport = excluded.transport,
                      command = excluded.command,
                      args = excluded.args,
                      env = excluded.env,
                      enabled = excluded.enabled,
                      scope = excluded.scope,
                      metadata = excluded.metadata,
                      updated_at = now()
                    """,
                    (
                        server["id"],
                        server.get("display_name") or server["id"],
                        server.get("transport") or "stdio",
                        server.get("command") or "",
                        Jsonb(list(server.get("args") or [])),
                        Jsonb(dict(server.get("env") or {})),
                        bool(server.get("enabled", False)),
                        server.get("scope") or "local",
                        Jsonb(metadata),
                    ),
                )
    except Exception:
        return


def _load_skills_from_db() -> list[dict[str, Any]] | None:
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            rows = db.execute("select * from imported_skills where enabled = true order by skill_id").fetchall()
        skills: list[dict[str, Any]] = []
        for row in rows:
            path = Path(row["path"])
            if not path.exists():
                continue
            skills.append(
                {
                    "id": row["skill_id"],
                    "name": row["name"],
                    "path": row["path"],
                    "enabled": row["enabled"],
                }
            )
        return skills
    except Exception:
        return None


def _save_skill_to_db(skill: dict[str, Any]) -> None:
    try:
        from platform_app.db import connect, init_db

        init_db()
        with connect() as db:
            db.execute(
                """
                insert into imported_skills(skill_id, name, path, enabled)
                values (%s, %s, %s, %s)
                on conflict(skill_id) do update set
                  name = excluded.name,
                  path = excluded.path,
                  enabled = excluded.enabled,
                  updated_at = now()
                """,
                (
                    skill["id"],
                    skill.get("name") or skill["id"],
                    skill.get("path") or "",
                    bool(skill.get("enabled", True)),
                ),
            )
    except Exception:
        return


def _decode_upload(item: dict[str, Any]) -> bytes:
    data_url = str(item.get("data_url") or item.get("dataUrl") or "")
    encoded = str(item.get("base64") or "")
    if "," in data_url:
        encoded = data_url.split(",", 1)[1]
    if not encoded:
        raise ValueError("上传内容为空")
    try:
        return base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("上传内容不是有效 base64") from exc


def _extract_skill_zip(data: bytes, target: Path) -> None:
    """解压 Skill zip 包到 target 目录。

    P1-1 SEC: 使用流式读写 + 实际写入字节计数，不依赖可伪造的 info.file_size。
    """
    _CHUNK = 65536
    zip_path = target / "_upload.zip"
    zip_path.write_bytes(data)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            skill_members = [name for name in zf.namelist() if name.endswith("SKILL.md")]
            if not skill_members:
                return
            root_prefix = str(Path(skill_members[0]).parent)
            total_written = 0
            extracted_count = 0
            for info in zf.infolist():
                member = info.filename
                member_path = Path(member)
                if member_path.is_absolute() or ".." in member_path.parts or member.endswith("/"):
                    continue
                extracted_count += 1
                if extracted_count > MAX_SKILL_FILES:
                    raise ValueError("Skill 压缩包文件数超限")
                relative = member_path
                if root_prefix not in {"", "."} and str(member_path).startswith(root_prefix + "/"):
                    relative = Path(str(member_path)[len(root_prefix) + 1:])
                out_path = target / relative
                out_path.parent.mkdir(parents=True, exist_ok=True)
                # 流式写入，按实际写入字节计数，防止 zip 炸弹（info.file_size 可伪造）
                with zf.open(info) as src, open(out_path, "wb") as dst:
                    while True:
                        chunk = src.read(_CHUNK)
                        if not chunk:
                            break
                        total_written += len(chunk)
                        if total_written > MAX_SKILL_UNPACKED_BYTES:
                            dst.close()
                            out_path.unlink(missing_ok=True)
                            raise ValueError("Skill 压缩包展开后超过大小限制")
                        dst.write(chunk)
    finally:
        zip_path.unlink(missing_ok=True)


def _skill_title(skill_file: Path) -> str:
    try:
        for line in skill_file.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("name:"):
                return stripped.split(":", 1)[1].strip().strip('"')
            if stripped.startswith("# "):
                return stripped[2:].strip()
    except Exception:
        return ""
    return ""


def _slugify(text: str) -> str:
    return slugify(text)


def _dedupe_dir(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise ValueError("无法分配 Skill 目录名")
