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

id 还得能被原生工具调用「编回来」:三个 backend 把工具名编成
`<server_id 里非 [A-Za-z0-9_-] 换下划线>__<工具名>` 并截到 64,模型调回来时按第一个 "__"
拆出 server_id。所以从名字派生的 id 只能用 [a-z0-9-](不含下划线就不会出现 "__",也不会有
结尾 "_" 把分隔符拉长),并且要短;中文名没有可用 ASCII 时用 "mcp-" + 名字的短哈希。
"""
from __future__ import annotations

import pathlib
import re

import pytest

from agents.gm.backends._tiered import SEP, tool_full_name
from tools_dsl.tool_registry import _normalize_mcp_server, mcp_server_id

ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_name_becomes_display_and_id():
    a = _normalize_mcp_server({"name": "文件系统", "command": "npx", "args": ["@modelcontextprotocol/server-filesystem"]})
    b = _normalize_mcp_server({"name": "My FS", "transport": "http", "url": "https://mcp.example.test"})
    assert a["display_name"] == "文件系统", "显示名要保留原名"
    assert re.fullmatch(r"mcp-[0-9a-f]{8}", a["id"]), a["id"]
    assert b["id"] == "my-fs" and b["display_name"] == "My FS"
    assert a["id"] != b["id"], "两个不同名字的服务器撞成同一个 id,后加的会覆盖先加的"


# 各种会把「按第一个 __ 拆回 server_id」弄坏的名字:纯中文、中英混排、结尾下划线、
# 名字里本身带 "__"、超长(截到 64 时把分隔符截掉)。
_NAMES = ["文件系统", "我的 filesystem", "你的 filesystem", "My FS", "my_server_", "a__b",
          "_lead", "x" * 70, "工具" * 40, "fs.v2 (beta)"]


@pytest.mark.parametrize("name", _NAMES)
def test_derived_id_round_trips_through_native_tool_name(name):
    sid = mcp_server_id({"name": name})
    assert re.fullmatch(r"[a-z0-9-]+", sid), sid
    assert _normalize_mcp_server({"name": name, "transport": "http",
                                  "url": "https://mcp.example.test"})["id"] == sid
    # 用 MCP 里常见的较长工具名压一下 64 字符截断
    for tool in ("read_file", "list_allowed_directories"):
        full = tool_full_name({"server_id": sid, "name": tool})
        back_sid, _, back_tool = full.partition(SEP)
        assert back_sid == sid, f"{name!r}: id {sid!r} 编码成 {full!r} 后拆回 {back_sid!r}"
        assert back_tool == tool, f"{name!r}: 工具名被截断成 {back_tool!r}"


def test_distinct_names_get_distinct_ids():
    ids = [mcp_server_id({"name": n}) for n in _NAMES]
    assert len(set(ids)) == len(ids), ids


def test_derived_id_is_stable():
    assert mcp_server_id({"name": "文件系统"}) == mcp_server_id({"name": " 文件系统 "})
    assert mcp_server_id({"display_name": "文件系统"}) == mcp_server_id({"name": "文件系统"})


def test_explicit_id_still_wins():
    s = _normalize_mcp_server({"id": "fs", "name": "文件系统", "command": "npx",
                               "args": ["@modelcontextprotocol/server-filesystem"]})
    assert s["id"] == "fs" and s["display_name"] == "文件系统"
    # 编辑时带回的存量 id(包括历史上兜底出来的 mcp_server)原样保留,不重新派生
    for legacy in ("mcp_server", "my_server"):
        assert mcp_server_id({"id": legacy, "name": "别的名字"}) == legacy


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


# ── 编辑表单 → 后端(巡检第二轮整合审查)────────────────────────────────────
# 后端输出只有 display_name 没有 name,命令落库成 command + args。两端编辑表单以前按 name || id
# 取名(中文名服务器显示成 mcp-<哈希>,保存时把原名覆盖成 id)、命令框只回填 command 且不带 args
# (npx 服务器 400,python3 -m pkg 被存成裸 python3)。现在两端都经 lib/mcp-form.js:
# 名字读 display_name;命令框回填整行,没改就原样带回 command + args,改过就发整行由这里重拆。

@pytest.mark.parametrize("line", [
    "npx @modelcontextprotocol/server-filesystem /data",
    "python3 -m my_mcp --root /data",
])
def test_edit_form_body_round_trips(line):
    stored = _normalize_mcp_server({"name": "文件系统", "transport": "stdio", "command": line, "enabled": True})
    assert "name" not in stored, "后端开始输出 name 了,前端 mcpDisplayName 的取名顺序要跟着核对"
    edit = {"id": stored["id"], "server_id": stored["id"], "name": stored["display_name"],
            "transport": "stdio", "enabled": True, "url": "",
            "command": stored["command"], "args": stored["args"]}
    assert _normalize_mcp_server(edit) == stored
    # 命令框改过:只发整行
    whole = {k: v for k, v in edit.items() if k != "args"}
    whole["command"] = " ".join([stored["command"], *stored["args"]])
    assert _normalize_mcp_server(whole) == stored


def test_old_edit_body_lost_args():
    """改前的编辑请求体:名字 = id、命令只有 command。npx 直接 400,python3 被存成裸命令。"""
    npx = _normalize_mcp_server({"name": "文件系统", "command": "npx @modelcontextprotocol/server-filesystem /data"})
    with pytest.raises(ValueError):
        _normalize_mcp_server({"id": npx["id"], "name": npx["id"], "command": npx["command"]})
    py = _normalize_mcp_server({"name": "py", "command": "python3 -m my_mcp"})
    assert _normalize_mcp_server({"id": py["id"], "name": py["id"], "command": py["command"]})["args"] == []


@pytest.mark.parametrize("path", [
    "frontend/src/components/platform/CapPages.jsx",
    "frontend/src/mobile/caps/McpSection.jsx",
])
def test_frontend_edit_uses_shared_form_helpers(path):
    """孪生奇偶:两端的列表取名 / 编辑回填 / 提交都走 lib/mcp-form.js,别再各写一份。"""
    src = (ROOT / path).read_text(encoding="utf-8")
    for fn in ("mcpDisplayName(", "mcpCommandLine(", "mcpStdioFields("):
        assert fn in src, f"{path} 没用 {fn[:-1]}"
    helper = (ROOT / "frontend/src/lib/mcp-form.js").read_text(encoding="utf-8")
    assert "display_name" in helper
