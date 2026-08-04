"""command_tools_script_write —— N (MD 编辑器) §5: script scope 「读 + 直写库」工具。

给 MD 编辑器右栏 agent(console_assistant)读现状 + 端到端直写剧本知识资产的工具。

读 vs 写鉴权铁律:
  · 「读」工具用 _user_can_read_script(owner 或订阅者),destructive=False;
  · 「写」工具用 perms.script_owned 严格 owner 闸(订阅者可读,写会越权)。
jsonb vs text[] 绑定:worldbook keys/... = Jsonb([...]);canon aliases/attrs = Jsonb(...);
anchor keywords = 原生 text[](直接绑 list);npc card aliases/... 由 upsert 内部 Jsonb 化。

── 2026-07-14 拆包说明(纯机械搬家,零行为变化)────────────────────────────
原单文件(1906 行)按职责拆为子包;本 __init__ 是薄门面,逐名 re-export 原模块的全部
顶层名(含下划线名与顶层 import 进来的名);3 处生产 import 点(command_tools_register)
与全部测试引用均零改动(个别 patch-where-used / read-source 测试已按新住址重定向)。
  _helpers.py   — origin 常量 + _resolve_sid/_user_can_read_script/_strlist
  chapters.py   — 章节读写族 + 拖入文档拆章(get/search/update/create/import)
  worldbook.py  — 世界书族(list/upsert/批量/delete + 缓存失效 + _wb_upsert_one)
  anchors.py    — 时间线锚点族(list/update/create/delete)
  canon.py      — canon 实体族(list/upsert)
  npc_cards.py  — NPC 角色卡族(update/create)
  extract.py    — 选区提取 + 委派 BYOK 子模型
  registry.py   — register_script_write_tools 注册表
"""
from __future__ import annotations

# 原顶层 import 的名字(测试/调用方可能以 module.X 形式引用)——保持可见
import json
from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry

from ._helpers import (
    _SCRIPT_READ_ORIGINS,
    _SCRIPT_WRITE_ORIGINS,
    _resolve_sid,
    _strlist,
    _user_can_read_script,
)
from .anchors import (
    _t_create_anchor,
    _t_delete_anchor,
    _t_list_anchors,
    _t_update_anchor,
)
from .canon import (
    _t_list_canon_entities,
    _t_upsert_canon_entity,
)
from .chapters import (
    _split_doc_or_err,
    _t_create_script_chapter,
    _t_get_chapter_context,
    _t_get_chapter_text,
    _t_import_document_as_chapters,
    _t_preview_document_split,
    _t_read_uploaded_document,
    _t_search_manuscript,
    _t_update_script_chapter,
)
from .extract import (
    _t_delegate_writing_task,
    _t_extract_from_selection,
)
from .npc_cards import (
    _t_create_npc_card,
    _t_update_npc_card,
)
from .registry import register_script_write_tools
from .worldbook import (
    _invalidate_worldbook_cache,
    _t_delete_worldbook_entry,
    _t_list_worldbook_entries,
    _t_upsert_worldbook_entries,
    _t_upsert_worldbook_entry,
    _wb_upsert_one,
)

__all__ = ["register_script_write_tools"]
