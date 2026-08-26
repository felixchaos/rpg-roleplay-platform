"""console_assistant.surfaces — 助手「面」的单一真相源(v1.82.1)。

同一个 `console_assistant` origin 上跑着**两个面貌完全不同的 agent**:

  · `console` —— 平台控制台助手:建卡 / 建档 / 改设置 / 代填表单。
  · `editor`  —— 剧本编辑器右栏的写作搭档:读章节、改正文、维护世界书与时间线。

`build_system_prompt` 早就分了面(见 prompts.py 里 `tab == "md-editor"` 那段,给编辑器换了
一整套人格与工作方式),**但工具表没分** —— 两个面共用一份硬编码名单。后果有两面:

  ① 编辑器 agent 拿不到本该属于它的剧本工具(`get_chapter_facts` / `get_worldbook` /
     `ask_user_text` 等)。注册时明明声明了 `origin="console_assistant"`,只因为名字没进
     那份名单就**静默不可见** —— 与 GM 侧直发窗口那个宿疾同族。
  ② 编辑器 agent 同时拿着一堆平台工具(改设置 / 管存档 / 页面导航),对写作场景是噪声。

本模块只负责回答「当前请求是哪个面」,判据一处定义、两处引用(prompts + tools)。
"""
from __future__ import annotations

from typing import Any

# 前端推上来的显式标记。MdEditorAgent.jsx 的 pageContext() 恒带 tab:'md-editor';
# md_editor / open_file 是更早版本的兼容标记,一并认。
_EDITOR_MARKERS = ("md_editor", "open_file")


def is_editor_surface(page_context: dict[str, Any] | None) -> bool:
    """当前请求是否来自剧本编辑器右栏。

    与 prompts.build_system_prompt 注入编辑器上下文块的判据**必须完全一致** ——
    否则会出现「系统提示词说你是写作搭档、工具表却是控制台那套」的错位。
    """
    if not page_context:
        return False
    if page_context.get("tab") == "md-editor":
        return True
    return any(page_context.get(k) for k in _EDITOR_MARKERS)


def surface_of(page_context: dict[str, Any] | None) -> str:
    """返回 "editor" / "console"。"""
    return "editor" if is_editor_surface(page_context) else "console"


__all__ = ["is_editor_surface", "surface_of"]
