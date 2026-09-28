"""test_mcp_add_contract.py — 「新增 MCP 服务器」表单 → 后端的契约。

web / 手机端的新增表单发 {name, transport, command, env}:
  · 后端 _normalize_mcp_server 只认 id / display_name,name 被丢掉 → 每个新服务器的 id 都是
    兜底的 "mcp_server",加第二个就把第一个整条覆盖掉;
  · 随后的「校验」发 mcp.validate({name}),后端读 id → id 为空 → 「未知 MCP 服务器」,
    校验从来没跑过(前端 catch 吞掉);
  · 表单的命令框是一整行(「npx @modelcontextprotocol/server-filesystem /data」),后端把整行
    当命令名去比白名单 → 带参数的 stdio 服务器一律 400。
修:name 作为 display_name 的别名并参与生成 id;命令框整行在没单独给 args 时拆成命令 + 参数
(拆完照样过白名单与参数校验);upsert 返回 server_id,前端拿它去校验。
"""
from __future__ import annotations

import pathlib

import pytest

from tools_dsl.tool_registry import _normalize_mcp_server, mcp_server_id

ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_name_becomes_display_and_id():
    a = _normalize_mcp_server({"name": "文件系统", "command": "npx", "args": ["@modelcontextprotocol/server-filesystem"]})
    b = _normalize_mcp_server({"name": "My FS", "transport": "http", "url": "https://mcp.example.test"})
    assert a["id"] == "文件系统" and a["display_name"] == "文件系统"
    assert b["id"] == "my-fs" and b["display_name"] == "My FS"
    assert a["id"] != b["id"], "两个不同名字的服务器撞成同一个 id,后加的会覆盖先加的"


def test_explicit_id_still_wins():
    s = _normalize_mcp_server({"id": "fs", "name": "文件系统", "command": "npx",
                               "args": ["@modelcontextprotocol/server-filesystem"]})
    assert s["id"] == "fs" and s["display_name"] == "文件系统"


def test_mcp_server_id_matches_normalize():
    body = {"name": "文件系统", "command": "npx", "args": ["@modelcontextprotocol/server-filesystem"]}
    assert mcp_server_id(body) == _normalize_mcp_server(body)["id"]


def test_one_line_command_is_split_into_args():
    s = _normalize_mcp_server({"name": "fs", "command": "npx @modelcontextprotocol/server-filesystem /data"})
    assert s["command"] == "npx"
    assert s["args"] == ["@modelcontextprotocol/server-filesystem", "/data"]


@pytest.mark.parametrize("line", [
    "npx -y evil-pkg",
    "python3 -c print(1)",
    "bash -c id",
    "node -e process.exit()",
])
def test_split_command_still_passes_security_validation(line):
    with pytest.raises(ValueError):
        _normalize_mcp_server({"name": "x", "command": line})


def test_upsert_route_returns_server_id():
    src = (ROOT / "rpg" / "routes" / "mcp.py").read_text(encoding="utf-8")
    seg = src[src.index('@router.post("/api/mcp/server",'):]
    seg = seg[: seg.index("@router.", 10)]
    assert "server_id" in seg


@pytest.mark.parametrize("path", [
    "frontend/src/components/platform/CapPages.jsx",
    "frontend/src/mobile/caps/McpSection.jsx",
])
def test_frontend_validates_by_id(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    assert "mcp.validate({ name" not in src, "校验又按 name 发了,后端只认 id"
    assert "server_id" in src
