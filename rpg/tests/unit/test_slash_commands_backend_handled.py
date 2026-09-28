"""test_slash_commands_backend_handled.py — 斜杠菜单里列出的每条命令,后端都得真的认。

斜杠菜单(frontend/src/components/game/GameComposerMenus.jsx 的 SLASH_COMMANDS)是游戏台 /
手机游戏 / 酒馆三端共用的单一来源。其中 /retry /save /status /debug 由前端本地执行,
/set 走 chat_pipeline.directives,其余都要靠 app._command_response 处理 —— 没被处理的
「命令」会原样当成一句台词交给 GM。

前科:v1.77.0 删了「记忆模式」(它什么都不控制),选择器删了,菜单里的
「/memory normal|deep|off」却一直留着,发出去就是一句写给 GM 的台词。
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
from unittest.mock import MagicMock

_RPG = pathlib.Path(__file__).resolve().parents[2]
REPO = _RPG.parent
if str(_RPG) not in sys.path:
    sys.path.insert(0, str(_RPG))
os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

_MENU = REPO / "frontend" / "src" / "components" / "game" / "GameComposerMenus.jsx"
# 前端本地执行的命令(entries/game-console.jsx 的 CLIENT_CMDS)与走 directives 的 /set
_CLIENT_SIDE = {"retry", "save", "status", "debug"}
_DIRECTIVE = {"set"}


def _menu_commands() -> list[tuple[str, str]]:
    src = _MENU.read_text(encoding="utf-8")
    body = src[src.index("const SLASH_COMMANDS"):src.index("];", src.index("const SLASH_COMMANDS"))]
    return re.findall(r'\{\s*id:\s*"(\w+)",\s*trigger:\s*"([^"]+)"', body)


def test_menu_parsed():
    cmds = _menu_commands()
    assert len(cmds) >= 8, cmds


def test_every_server_side_slash_command_is_handled():
    from app import _command_response

    unhandled = []
    for cid, trigger in _menu_commands():
        if cid in _CLIENT_SIDE or cid in _DIRECTIVE:
            continue
        reply, _changed = _command_response(trigger.strip() + " 示例", MagicMock())
        if not reply:
            unhandled.append(trigger.strip())
    assert not unhandled, f"斜杠菜单里这些命令后端不认,发出去会被当成台词交给 GM: {unhandled}"
