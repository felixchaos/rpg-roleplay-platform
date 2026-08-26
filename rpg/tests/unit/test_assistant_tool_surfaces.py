"""test_assistant_tool_surfaces.py — 助手工具「面」划分的守卫(v1.82.1)。

同一个 `console_assistant` origin 上跑着两个 agent:平台控制台助手、剧本编辑器写作搭档。
`build_system_prompt` 早就按面换了人格与工作方式,**工具表却一直共用一份硬编码名单** ——
于是注册时声明了本 origin、名字却没进名单的工具**静默不可见**(get_chapter_facts /
get_worldbook 就是这样在编辑器里失踪的)。与 GM 侧直发窗口是同一族缺陷。

本文件锁四件事:
  ① 没有工具落在四个集合之外 —— 新工具不选面就红,不再有「静默不可见」这条路
  ② 名单里没有死项(写了名字却根本没注册给本 origin)
  ③ 编辑器面拿得到剧本资产工具
  ④ 两个面用的判据与 build_system_prompt 完全一致
"""
from __future__ import annotations

import pytest

from console_assistant.surfaces import is_editor_surface, surface_of
from console_assistant.tools import (
    CONSOLE_ONLY,
    EDITOR_ONLY,
    NOT_SERVED,
    SHARED,
    list_assistant_tools,
)
from tools_dsl import command_tools_register as _reg
from tools_dsl.command_dispatcher import get_registry


@pytest.fixture(scope="module", autouse=True)
def _registered():
    _reg.ensure_registered()


def _origin_tools() -> set[str]:
    return {s.name for s in get_registry().list_for_origin("console_assistant")}


def test_registry_is_not_empty():
    """自检:注册表空的话下面每条断言都会假绿。"""
    assert len(_origin_tools()) > 50


def test_every_registered_tool_is_classified():
    stray = sorted(_origin_tools() - (SHARED | EDITOR_ONLY | CONSOLE_ONLY | NOT_SERVED))
    assert not stray, (
        "这些工具注册给了 console_assistant,却没有落进任何一个面,会静默不可见。"
        "请在 console_assistant/tools.py 里给它们选一个集合"
        "(SHARED / EDITOR_ONLY / CONSOLE_ONLY / NOT_SERVED,后者要写理由):\n  "
        + "\n  ".join(stray))


def test_no_dead_names_in_the_lists():
    """名单里写了却没注册给本 origin 的名字 —— 改名/删工具后留下的残渣。"""
    dead = sorted((SHARED | EDITOR_ONLY | CONSOLE_ONLY | NOT_SERVED) - _origin_tools())
    assert not dead, f"名单里有死项(未注册给 console_assistant): {dead}"


def test_surfaces_do_not_overlap():
    assert not (EDITOR_ONLY & CONSOLE_ONLY)
    assert not (SHARED & NOT_SERVED)
    assert not (EDITOR_ONLY & NOT_SERVED)
    assert not (CONSOLE_ONLY & NOT_SERVED)


def test_editor_gets_the_script_asset_tools():
    """记录在案的失踪工具:编辑器的系统提示词一直教 agent「动笔前先把设定吃透」,
    而它够不到设定。"""
    edi = {t["name"] for t in list_assistant_tools("editor")}
    for name in ("get_chapter_facts", "get_worldbook", "ask_user_text"):
        assert name in edi, f"编辑器写作搭档仍够不到 {name}"


def test_editor_is_a_superset_of_console_for_now():
    """CONSOLE_ONLY 目前刻意为空(不做减法)。哪天要做减法,这条会红,提醒同步改说明。"""
    con = {t["name"] for t in list_assistant_tools("console")}
    edi = {t["name"] for t in list_assistant_tools("editor")}
    assert con <= edi
    assert edi - con == set(EDITOR_ONLY)


def test_unknown_surface_falls_back_to_console():
    """未知 surface 退最保守的那面,不许退成「全给」。"""
    assert ({t["name"] for t in list_assistant_tools("nonsense")}
            == {t["name"] for t in list_assistant_tools("console")})


def test_surface_predicate_matches_the_prompt_side():
    """判据必须与 prompts.build_system_prompt 用的是同一个函数 ——
    否则会出现「提示词说你是写作搭档、工具表却是控制台那套」。"""
    import inspect

    from console_assistant import prompts
    src = inspect.getsource(prompts.build_system_prompt)
    assert "is_editor_surface" in src, "prompts 没走共享判据,两处会漂移"
    assert 'tab" == "md-editor"' not in src, "prompts 里还留着判据的第二份拷贝"


@pytest.mark.parametrize("ctx,expected", [
    ({"tab": "md-editor", "script_id": 1}, "editor"),
    ({"md_editor": True}, "editor"),
    ({"open_file": "【章节】「第一章」"}, "editor"),
    ({"page": "platform.saves"}, "console"),
    ({}, "console"),
    (None, "console"),
])
def test_surface_detection(ctx, expected):
    assert surface_of(ctx) == expected
    assert is_editor_surface(ctx) is (expected == "editor")
