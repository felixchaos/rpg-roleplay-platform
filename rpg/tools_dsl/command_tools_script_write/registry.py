"""command_tools_script_write §注册表(拆包 2026-07-14,纯机械搬家零行为变化)。

register_script_write_tools:script 级「直写库」写工具注册(章节/世界书/NPC卡/锚点/canon,
严格 owner 闸)。逐字保留原注册表;executor 从各职责子模块导入。
"""
from __future__ import annotations

from tools_dsl.command_dispatcher import ToolSpec, get_registry

from ._helpers import _SCRIPT_WRITE_ORIGINS
from .anchors import _t_create_anchor, _t_delete_anchor, _t_update_anchor
from .canon import _t_upsert_canon_entity
from .chapters import (
    _t_create_script_chapter,
    _t_get_chapter_text,
    _t_import_document_as_chapters,
    _t_preview_document_split,
    _t_read_uploaded_document,
    _t_search_manuscript,
    _t_update_script_chapter,
)
from .extract import _t_delegate_writing_task
from .npc_cards import _t_create_npc_card, _t_update_npc_card
from .worldbook import (
    _t_delete_worldbook_entry,
    _t_upsert_worldbook_entries,
    _t_upsert_worldbook_entry,
)


def _write_entity_crud_specs() -> list:
    return [
        ToolSpec(
            name="update_script_chapter",
            description=(
                "更新剧本某一章的正文/标题/分卷名(覆盖整章正文,destructive)。"
                "chapter_index 必填;title/content/volume_title 至少传一个。"
                "改前先向用户说清要改哪一章、改成什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "chapter_index": {"type": "integer", "description": "章序号(1-based)"},
                    "title": {"type": "string"},
                    "content": {"type": "string", "description": "整章正文(会覆盖原正文)"},
                    "volume_title": {"type": "string"},
                },
                "required": ["chapter_index"],
            },
            executor=_t_update_script_chapter,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=True,
        ),
        ToolSpec(
            name="upsert_worldbook_entry",
            description=(
                "创建或更新世界书条目。传 entry_id = 更新该条目;不传 = 新建(新建需 title)。"
                "keys/regex_keys/character_filter/scene_filter 是字符串数组。"
                "改前先向用户说清要改什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entry_id": {"type": "integer", "description": "有=更新,无=创建"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "priority": {"type": "integer"},
                    "enabled": {"type": "boolean"},
                    "keys": {"type": "array", "items": {"type": "string"}},
                    "regex_keys": {"type": "array", "items": {"type": "string"}},
                    "character_filter": {"type": "array", "items": {"type": "string"}},
                    "scene_filter": {"type": "array", "items": {"type": "string"}},
                    "token_budget": {"type": "integer"},
                    "sticky_turns": {"type": "integer"},
                    "cooldown_turns": {"type": "integer"},
                    "probability": {"type": "number"},
                    "insertion_position": {"type": "string"},
                },
                "required": ["title"],
            },
            executor=_t_upsert_worldbook_entry,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="upsert_worldbook_entries",
            description=(
                "批量创建/更新世界书条目 —— 一次要建/改多条时用本工具(一次调用一并落库),"
                "不要逐条调用 upsert_worldbook_entry(逐条在审查模式下只会成功第一条)。"
                "entries 是条目数组,每项字段与 upsert_worldbook_entry 相同(新建带 title、改带 entry_id)。"
                "**每次最多放 6 条**:条数太多整个调用会超输出长度被截断导致失败;超过 6 条请分多次调用。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entries": {
                        "type": "array",
                        "description": "世界书条目数组(≤50 条);每项:新建带 title,更新带 entry_id",
                        "items": {
                            "type": "object",
                            "properties": {
                                "entry_id": {"type": "integer", "description": "有=更新,无=创建"},
                                "title": {"type": "string"},
                                "content": {"type": "string"},
                                "priority": {"type": "integer"},
                                "enabled": {"type": "boolean"},
                                "keys": {"type": "array", "items": {"type": "string"}},
                                "regex_keys": {"type": "array", "items": {"type": "string"}},
                                "character_filter": {"type": "array", "items": {"type": "string"}},
                                "scene_filter": {"type": "array", "items": {"type": "string"}},
                                "token_budget": {"type": "integer"},
                                "sticky_turns": {"type": "integer"},
                                "cooldown_turns": {"type": "integer"},
                                "probability": {"type": "number"},
                                "insertion_position": {"type": "string"},
                            },
                        },
                    },
                },
            },
            executor=_t_upsert_worldbook_entries,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="update_npc_card",
            description=(
                "更新剧本内某张 NPC 角色卡。card_id 必填(先用 list_script_npcs 拿 id)。"
                "只传要改的字段(其余保留)。不收 avatar_path(头像走专用端点)。"
                "改前先向用户说清要改什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "full_name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "identity": {"type": "string"},
                    "appearance": {"type": "string"},
                    "personality": {"type": "string"},
                    "speech_style": {"type": "string"},
                    "current_status": {"type": "string"},
                    "secrets": {"type": "string"},
                    "background": {"type": "string"},
                    "sample_dialogue": {"type": "array"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer"},
                    "first_revealed_chapter": {"type": "integer"},
                    "enabled": {"type": "boolean"},
                },
                "required": ["card_id"],
            },
            executor=_t_update_npc_card,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="update_anchor",
            description=(
                "更新时间线锚点。anchor_id 必填。keywords 是字符串数组。"
                "改前先向用户说清要改什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "anchor_id": {"type": "integer"},
                    "story_phase": {"type": "string"},
                    "story_time_label": {"type": "string"},
                    "chapter_min": {"type": "integer"},
                    "chapter_max": {"type": "integer"},
                    "sample_title": {"type": "string"},
                    "sample_summary": {"type": "string"},
                    "confidence": {"type": "number"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["anchor_id"],
            },
            executor=_t_update_anchor,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="create_anchor",
            description=(
                "为剧本「新增」一个时间线锚点 —— 当续写引入了原著时间线里没有的全新事件/时间节点时用。"
                "必填 story_time_label(节点名)+ chapter_min/chapter_max(该事件大致所处章节);"
                "story_phase 可选。新增的锚点来源标记为 editor,时间线重建不会删它。"
                "要改已有锚点用 update_anchor(不要用本工具重复新建)。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "story_time_label": {"type": "string", "description": "新事件/时间节点名"},
                    "chapter_min": {"type": "integer", "description": "该事件大致起始章"},
                    "chapter_max": {"type": "integer", "description": "该事件大致结束章"},
                    "story_phase": {"type": "string", "description": "所属阶段(可空)"},
                    "sample_title": {"type": "string"},
                    "sample_summary": {"type": "string"},
                    "confidence": {"type": "number"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["story_time_label", "chapter_min", "chapter_max"],
            },
            executor=_t_create_anchor,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="create_script_chapter",
            description=(
                "在剧本【末尾】新增一章。title 必填,content 可选(新章正文,可留空之后再写)。"
                "续写出全新一章时用。要改已有章用 update_script_chapter。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "新章标题"},
                    "content": {"type": "string", "description": "新章正文(可空)"},
                    "volume_title": {"type": "string", "description": "所属卷名(可空)"},
                },
                "required": ["title"],
            },
            executor=_t_create_script_chapter,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="create_npc_card",
            description=(
                "为剧本【新建】一张 NPC 角色卡。name 必填,其余字段(identity/appearance/personality/"
                "background/aliases/importance/first_revealed_chapter 等)可选。可结合别的剧本或正文情节"
                "创建新角色。要改已有卡用 update_npc_card(先 list_script_npcs)。同名会被拒(改用 update)。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "角色名(必填)"},
                    "full_name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "identity": {"type": "string"},
                    "appearance": {"type": "string"},
                    "personality": {"type": "string"},
                    "speech_style": {"type": "string"},
                    "current_status": {"type": "string"},
                    "secrets": {"type": "string"},
                    "background": {"type": "string"},
                    "sample_dialogue": {"type": "array"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer"},
                    "first_revealed_chapter": {"type": "integer"},
                },
                "required": ["name"],
            },
            executor=_t_create_npc_card,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="delete_worldbook_entry",
            description=(
                "删除一条世界书条目。entry_id 必填(先 list_worldbook_entries 拿 id)。不可逆,删前向用户确认。"
            ),
            input_schema={
                "type": "object",
                "properties": {"entry_id": {"type": "integer"}},
                "required": ["entry_id"],
            },
            executor=_t_delete_worldbook_entry,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=True,
        ),
        ToolSpec(
            name="delete_anchor",
            description=(
                "删除一个时间线锚点。anchor_id 必填(先 list_anchors 拿 id)。不可逆,删前向用户确认。"
                "原著骨架锚点(source=novel)删后时间线重建可能再生成。"
            ),
            input_schema={
                "type": "object",
                "properties": {"anchor_id": {"type": "integer"}},
                "required": ["anchor_id"],
            },
            executor=_t_delete_anchor,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=True,
        ),
    ]


def _write_document_manuscript_specs() -> list:
    return [
        ToolSpec(
            name="read_uploaded_document",
            description=(
                "读取用户【拖入】的暂存文档的一段(分片 offset/limit)。用户拖入 txt/md 后会给到 doc_id。"
                "用于按文档内容执行指令(如「据这段建角色/改写」)。原文不在上下文里,要看就用本工具读。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "offset": {"type": "integer", "description": "起始字符偏移(默认0)"},
                    "limit": {"type": "integer", "description": "读取字符数(默认6000,上限20000)"},
                },
                "required": ["doc_id"],
            },
            executor=_t_read_uploaded_document,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="preview_document_split",
            description=(
                "预览:把用户拖入的文档按规则【确定性】拆成几章、标题是什么(只读不落库)。"
                "split_rule:auto(默认)/chapter_cn(第N章)/chapter_en(Chapter N)/number_dot(1.)/custom(配 custom_pattern)。"
                "先预览给用户看,确认后再 import_document_as_chapters 落库。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "split_rule": {"type": "string"},
                    "custom_pattern": {"type": "string"},
                },
                "required": ["doc_id"],
            },
            executor=_t_preview_document_split,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="import_document_as_chapters",
            description=(
                "把用户拖入的文档【确定性】拆章并写入当前剧本。mode=append(默认,末尾追加)/"
                "replace(清空现有章再导入,慎用)。建议先 preview_document_split 给用户确认章数/标题。"
                "纯确定性拆分(不消耗 LLM token),适合整段/整章/整本导入。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "doc_id": {"type": "string"},
                    "split_rule": {"type": "string"},
                    "custom_pattern": {"type": "string"},
                    "mode": {"type": "string", "enum": ["append", "replace"]},
                },
                "required": ["doc_id"],
            },
            executor=_t_import_document_as_chapters,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=True,
        ),
        ToolSpec(
            name="delegate_writing_task",
            description=(
                "把一段写作/特定任务【委派】给一个用户自己配置的(BYOK)子模型来做 —— 例如用一个更强/"
                "更擅长某文风的模型写某章某段。可显式指定 model(api_id+model),否则用用户的写作/默认模型。"
                "【只用用户自己配置的模型,不用平台兜底】;调用失败会明确返回失败原因。"
                "产出是【草稿】,需你向用户确认后再用 update_script_chapter/create_script_chapter 落库。"
                "context 可放参考正文(如相邻章节片段)。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "委派的写作任务(越具体越好)"},
                    "api_id": {"type": "string", "description": "指定子模型 provider(可空=用默认写作模型)"},
                    "model": {"type": "string", "description": "指定子模型名(可空)"},
                    "context": {"type": "string", "description": "参考上下文/相邻正文(可空)"},
                    "max_tokens": {"type": "integer", "description": "产出长度上限(默认2500)"},
                },
                "required": ["task"],
            },
            executor=_t_delegate_writing_task,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="get_chapter_text",
            description=(
                "读取某章【完整正文】(章节原著 content)。修锚点 / 核对设定 / 写作参考前,"
                "用它读真正文 —— 不要只看可能被污染的摘要(summary/sample_summary)。"
                "必填 chapter_index;长章用 offset 分段续读(返回会提示下一段 offset)。只读,owner/订阅者可用。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "chapter_index": {"type": "integer", "description": "章号"},
                    "offset": {"type": "integer", "description": "起始字符偏移(分段读长章,默认 0)"},
                    "max_chars": {"type": "integer", "description": "本段最多字符(默认 12000,上限 20000)"},
                },
                "required": ["chapter_index"],
            },
            executor=_t_get_chapter_text,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="search_manuscript",
            description=(
                "全书检索:在剧本所有章节正文里搜一个词/短语/正则,返回命中的【章号 + 标题 + 上下文片段 + 字符偏移】。"
                "这是『先读后写、避免与全书矛盾』的核心工具 —— 审稿查重复、查前文是否已交代过某设定、找某人物/"
                "物件上次出场、核对伏笔是否回收,都先用它一次定位,再用 get_chapter_text(chapter_index, offset=@值) 精读。"
                "默认大小写无关子串匹配;regex=true 走 Python 正则;可用 chapter_min/chapter_max 收窄。只读,owner/订阅者可用。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要搜索的词/短语;regex=true 时为正则"},
                    "regex": {"type": "boolean", "description": "是否按正则匹配(默认 false=子串)"},
                    "chapter_min": {"type": "integer", "description": "只搜该章号及以后(可空)"},
                    "chapter_max": {"type": "integer", "description": "只搜该章号及以前(可空)"},
                    "max_results": {"type": "integer", "description": "最多列出多少条命中(默认 30,上限 100)"},
                    "context_chars": {"type": "integer", "description": "每条命中前后各取多少字符上下文(默认 60)"},
                },
                "required": ["query"],
            },
            input_examples=(
                {"query": "重力控制"},
                {"query": "蜜特·托蕾特", "chapter_min": 1, "chapter_max": 20},
            ),
            executor=_t_search_manuscript,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="upsert_canon_entity",
            description=(
                "创建或更新 canon 实体(按 logical_key)。logical_key 必填;"
                "创建时还需 name 和 type。aliases 是字符串数组,attrs 是开放对象。"
                "改前先向用户说清要改什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "logical_key": {"type": "string"},
                    "name": {"type": "string"},
                    "full_name": {"type": "string"},
                    "type": {"type": "string"},
                    "summary": {"type": "string"},
                    "identity": {"type": "string"},
                    "background": {"type": "string"},
                    "entity_subtype": {"type": "string"},
                    "parent_logical_key": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "attrs": {"type": "object"},
                    "first_revealed_chapter": {"type": "integer"},
                    "public_knowledge": {"type": "boolean"},
                    "importance": {"type": "integer"},
                },
                "required": ["logical_key"],
            },
            executor=_t_upsert_canon_entity,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
    ]


def register_script_write_tools() -> None:
    registry = get_registry()
    specs: list[ToolSpec] = [*_write_entity_crud_specs(), *_write_document_manuscript_specs()]
    for spec in specs:
        if not registry.has(spec.name):
            registry.register(spec)


