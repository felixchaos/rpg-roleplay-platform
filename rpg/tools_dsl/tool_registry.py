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
import json
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

BASE = Path(__file__).parent.parent  # rpg/tools_dsl/ → rpg/
CONFIG_DIR = BASE / "config"
MCP_CONFIG_FILE = CONFIG_DIR / "mcp_servers.json"
USER_SKILL_DIR = BASE / "user_skills"
MAX_SKILL_BYTES = 2 * 1024 * 1024
MAX_SKILL_FILES = 80
MAX_SKILL_UNPACKED_BYTES = 4 * 1024 * 1024

DEFAULT_PLUGIN_TOOLS = [
    {"id": "documents", "name": "Documents", "kind": "plugin", "enabled": True},
    {"id": "spreadsheets", "name": "Spreadsheets", "kind": "plugin", "enabled": True},
    {"id": "presentations", "name": "Presentations", "kind": "plugin", "enabled": True},
    {"id": "browser", "name": "浏览器", "kind": "plugin", "enabled": True},
    {"id": "chrome", "name": "Chrome", "kind": "plugin", "enabled": True},
    {"id": "computer-use", "name": "电脑", "kind": "plugin", "enabled": True},
    {"id": "figma", "name": "Figma", "kind": "plugin", "enabled": True},
    {"id": "github", "name": "GitHub", "kind": "plugin", "enabled": True},
    {"id": "cloudflare", "name": "Cloudflare", "kind": "plugin", "enabled": True},
    {"id": "build-ios-apps", "name": "Build iOS Apps", "kind": "plugin", "enabled": True},
    {"id": "codex-security", "name": "Codex Security", "kind": "plugin", "enabled": True},
]

DEFAULT_MCP_CATALOG = {
    "schema_version": 1,
    "servers": [],
}


def deployment_capabilities() -> dict[str, Any]:
    from core.config import (
        deployment_mode as _deployment_mode,
    )
    from core.config import (
        enable_mcp_config_write as _enable_mcp_config_write,
    )
    from core.config import (
        enable_skill_import as _enable_skill_import,
    )
    mode = _deployment_mode().strip().lower() or "local"
    is_local = mode in {"local", "desktop", "self_hosted", "self-hosted"}
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
        _mirror_mcp_catalog_file(db_catalog)
        return db_catalog
    return _load_mcp_catalog_from_file()


def _load_mcp_catalog_from_file() -> dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not MCP_CONFIG_FILE.exists():
        save_mcp_catalog(copy.deepcopy(DEFAULT_MCP_CATALOG))
    try:
        with open(MCP_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    return _migrate_mcp_catalog(data)


def save_mcp_catalog(catalog: dict[str, Any]) -> None:
    catalog = _migrate_mcp_catalog(catalog)
    _save_mcp_catalog_to_db(catalog)
    _mirror_mcp_catalog_file(catalog)


def _mirror_mcp_catalog_file(catalog: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = MCP_CONFIG_FILE.with_suffix(".json.tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(_migrate_mcp_catalog(catalog), f, ensure_ascii=False, indent=2)
    tmp_file.replace(MCP_CONFIG_FILE)


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
    command = server.get("command", "")
    resolved = shutil.which(command) if command else None
    return {
        "id": server_id,
        "transport": server.get("transport", "stdio"),
        "command": command,
        "command_resolved": resolved,
        "ready_to_launch": bool(resolved and server.get("transport", "stdio") == "stdio"),
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


def _migrate_mcp_catalog(data: dict[str, Any]) -> dict[str, Any]:
    catalog = copy.deepcopy(DEFAULT_MCP_CATALOG)
    if isinstance(data, dict) and isinstance(data.get("servers"), list):
        catalog["servers"] = [_normalize_mcp_server(item) for item in data["servers"]]
    catalog["schema_version"] = 1
    return catalog


_MCP_CMD_WHITELIST = {"python3", "python", "node", "npx"}
_MCP_CMD_SAFE_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,32}$")


def _normalize_mcp_server(server: dict[str, Any]) -> dict[str, Any]:
    server_id = _slugify(str(server.get("id") or server.get("display_name") or "mcp_server"))
    args = server.get("args") or []
    if isinstance(args, str):
        args = [part for part in args.split(" ") if part]
    env = server.get("env") or {}
    if not isinstance(env, dict):
        env = {}
    command = str(server.get("command") or "").strip()
    # P1-2 SEC: command 白名单 + 安全字符集校验（禁止 / 和 ..）
    if command:
        if "/" in command or ".." in command:
            raise ValueError(f"MCP server command 不能包含路径分隔符: {command!r}")
        if command not in _MCP_CMD_WHITELIST and not _MCP_CMD_SAFE_RE.match(command):
            raise ValueError(f"MCP server command 不合法（仅允许白名单或 [a-zA-Z0-9_-]{{1,32}}）: {command!r}")
    return {
        "id": server_id,
        "display_name": str(server.get("display_name") or server_id).strip(),
        "transport": str(server.get("transport") or "stdio").strip(),
        "command": command,
        "args": [str(item) for item in args],
        "env": {str(k): str(v) for k, v in env.items()},
        "enabled": bool(server.get("enabled", False)),
        "scope": str(server.get("scope") or "local").strip(),
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
                    }
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
        catalog = _migrate_mcp_catalog(catalog)
        with connect() as db:
            db.execute("delete from mcp_servers")
            for server in catalog.get("servers", []):
                db.execute(
                    """
                    insert into mcp_servers(server_id, display_name, transport, command, args, env, enabled, scope)
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict(server_id) do update set
                      display_name = excluded.display_name,
                      transport = excluded.transport,
                      command = excluded.command,
                      args = excluded.args,
                      env = excluded.env,
                      enabled = excluded.enabled,
                      scope = excluded.scope,
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
    slug = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "-", text.strip()).strip("-").lower()
    return slug or "item"


def _dedupe_dir(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise ValueError("无法分配 Skill 目录名")
