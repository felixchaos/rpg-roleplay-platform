"""
command_tools_imports.py — script import / probe 工具 (拆自 command_tools_misc.py)

包含:
  start_script_import       user mutate
  get_import_status         user read
  list_my_import_jobs       user read
  cancel_import_job         user mutate
  resplit_script            user destructive
  delete_script             user destructive
  probe_models              user mutate
"""
from __future__ import annotations

import json

from tools_dsl._arg_guards import require_int_arg
from tools_dsl.command_dispatcher import ToolSpec, get_registry

_USER_READ = frozenset({"ui_button", "api_direct", "llm_set", "llm_chat", "console_assistant"})
_USER_MUTATE = frozenset({"ui_button", "api_direct", "console_assistant"})
_USER_DEST = frozenset({"ui_button", "api_direct", "console_assistant"})


def _t_start_script_import(user_id: int, args: dict) -> str:
    """从已上传的 upload_id 启动剧本导入。upload 走 /api/uploads (二进制,保留 HTTP)。"""
    upload_id = (args.get("upload_id") or "").strip()
    title = (args.get("title") or "").strip()
    if not upload_id or not title:
        return "失败: upload_id 与 title 都必填"
    try:
        from platform_app import script_import
        result = script_import.import_script(
            user_id=user_id,
            upload_id=upload_id,
            title=title,
            split_rule=(args.get("mode") or "regex").strip() or "regex",
        )
        sid = result.get("script_id")
        return f"导入剧本启动: script_id={sid} (事件流: /api/scripts/import-jobs/{result.get('job_id','?')}/stream)"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_get_import_status(user_id: int, args: dict) -> str:
    script_id = args.get("script_id")
    _chk = require_int_arg(script_id, "script_id")
    if isinstance(_chk, str):
        return _chk
    try:
        from platform_app import script_import
        status = script_import.get_sync_status(user_id, int(script_id))
        return json.dumps(status, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_list_my_import_jobs(user_id: int, args: dict) -> str:
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select id, script_id, status, progress, created_at, updated_at "
                "from script_import_jobs where user_id = %s "
                "order by created_at desc limit 30",
                (user_id,),
            ).fetchall() or []
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_cancel_import_job(user_id: int, args: dict) -> str:
    job_id = (args.get("job_id") or "").strip()
    if not job_id:
        return "失败: job_id 为空"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "update script_import_jobs set status = 'cancelled', "
                "updated_at = now() where id = %s and user_id = %s "
                "and status in ('pending','running') returning id",
                (job_id, user_id),
            ).fetchone()
            if not row:
                return f"失败: job {job_id} 不属于当前用户、不存在,或已终止"
        return f"取消导入 job {job_id} ✓"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_resplit_script(user_id: int, args: dict) -> str:
    script_id = args.get("script_id")
    mode = (args.get("mode") or "regex").strip() or "regex"
    _chk = require_int_arg(script_id, "script_id")
    if isinstance(_chk, str):
        return _chk
    try:
        from platform_app import script_import
        result = script_import.resplit_script(user_id=user_id, script_id=int(script_id), split_rule=mode)
        return f"重新拆分: chapters={result.get('chapter_count','?')} (mode={mode})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_delete_script(user_id: int, args: dict) -> str:
    script_id = args.get("script_id")
    force = bool(args.get("force"))
    _chk = require_int_arg(script_id, "script_id")
    if isinstance(_chk, str):
        return _chk
    try:
        from platform_app import script_import
        result = script_import.delete_script(user_id=user_id, script_id=int(script_id), force=force)
        return f"剧本 {script_id} 已删除 (chapters_dropped={result.get('chapters_dropped',0)})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# 友好别名 → import_pipeline 的规范 module key(REBUILD_MODULES)
_REBUILD_MODULE_ALIASES = {
    "facts": "chapter-facts", "chapter_facts": "chapter-facts", "fact": "chapter-facts",
    "timeline": "anchors", "anchor": "anchors",
    "embedding": "embeddings", "embed": "embeddings", "vector": "embeddings", "vectors": "embeddings",
    "characters": "cards", "character": "cards", "card": "cards", "人设卡": "cards", "角色卡": "cards",
    "wb": "worldbook", "world_book": "worldbook", "世界书": "worldbook",
    "chunk": "chunks",
    "规范实体": "canon", "时间线": "anchors",
}
_REBUILD_MODULE_NAMES = (
    "chunks", "chapter-facts", "canon", "cards", "worldbook", "anchors", "embeddings",
)


def _t_rebuild_script_module(user_id: int, args: dict) -> str:
    """对已导入剧本重建单个知识模块(切块/章节事实/规范实体/角色卡/世界书/时间线/向量),
    并【闭环等真实结果】回灌。剧本编辑器(console_assistant)里「只重做某一个模块」用它。"""
    script_id = args.get("script_id")
    _chk = require_int_arg(script_id, "script_id")
    if isinstance(_chk, str):
        return _chk
    raw_module = (args.get("module") or "").strip()
    module = _REBUILD_MODULE_ALIASES.get(raw_module.lower(), raw_module.lower())
    if module not in _REBUILD_MODULE_NAMES:
        return (f"失败: 未知模块 '{raw_module}'。可选: "
                f"{', '.join(_REBUILD_MODULE_NAMES)} "
                "(别名: facts/timeline/embed/cards/worldbook 等)")
    source = (args.get("source") or args.get("mode") or "").strip() or None
    try:
        from platform_app import import_pipeline as _ip
        body: dict = {}
        if source:
            body["source"] = source
        sched = _ip.schedule_module_rebuild(user_id, int(script_id), module, body=body)
        job_id = sched.get("job_id")
        if not job_id:
            return f"失败: 调度未返回 job_id ({sched})"
        if sched.get("reused"):
            # 已有同模块任务在跑:不重复触发,直接等它
            pass
        label = dict(zip(_REBUILD_MODULE_NAMES,
                         ("切块", "章节事实", "规范实体", "角色卡", "世界书", "时间线", "向量重嵌"))).get(module, module)
        # 上限 100s:压在前端 SSE 120s idle 窗口内(工具阻塞期间无 SSE 事件,超 120s 前端会 idle_timeout)。
        # 轻量/确定性重建多在此内完成→GM 拿真结果;LLM 丰富等长任务超时返「仍在后台」,交由后台任务浮窗收尾。
        res = _ip.wait_for_import_job(user_id, job_id, timeout_s=100.0, poll_s=2.5)
        return _ip.summarize_job_result(res, f"剧本 {int(script_id)} 的「{label}」重建")
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_probe_models(user_id: int, args: dict) -> str:
    api_id = (args.get("api_id") or "").strip() or None
    try:
        import model_probe
        result = model_probe.probe(user_id=user_id, api_id_filter=api_id) if hasattr(model_probe, "probe") else None
        if result is None:
            return "失败: model_probe.probe 未提供"
        return json.dumps(result, ensure_ascii=False, indent=2)[:1500]
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def register_imports_tools() -> None:
    registry = get_registry()

    user_specs = [
        # Phase 4 异步 - 启动/取消任务都禁 LLM (会消耗资源/触发外部 LLM 调用)
        ("start_script_import",
         "从已上传 upload_id 启动剧本导入 (上传走 /api/uploads,这里只触发导入). "
         "返回 script_id 与 job_id,事件流走 /api/scripts/import-jobs/{job_id}/stream",
         {"type": "object",
          "properties": {
              "upload_id": {"type": "string"},
              "title": {"type": "string"},
              "mode": {"type": "string", "default": "regex"},
          }, "required": []},  # handler 自行校验并返回"必填"友好消息
         _t_start_script_import, _USER_MUTATE, False),
        ("get_import_status", "查询剧本导入进度",
         {"type": "object", "properties": {"script_id": {"type": "integer"}}, "required": ["script_id"]},
         _t_get_import_status, _USER_READ, False),  # read OK
        ("list_my_import_jobs", "列出当前用户的导入任务",
         {"type": "object", "properties": {}}, _t_list_my_import_jobs, _USER_READ, False),
        ("cancel_import_job", "取消进行中的导入任务",
         {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": []},
         _t_cancel_import_job, _USER_MUTATE, False),  # 跨任务 mutate,LLM 禁
        ("resplit_script", "对已导入剧本重新切章",
         {"type": "object",
          "properties": {"script_id": {"type": "integer"}, "mode": {"type": "string", "default": "regex"}},
          "required": ["script_id"]},
         _t_resplit_script, _USER_DEST, True),
        ("rebuild_script_module",
         "对已导入剧本【重建单个知识模块】并等真实结果回灌。"
         "module 可选: chunks(切块)/chapter-facts(章节事实)/canon(规范实体)/cards(角色卡)/"
         "worldbook(世界书)/anchors(时间线)/embeddings(向量),也认 facts/timeline/embed 等别名。"
         "source='llm' 时 canon/worldbook/cards 走 LLM 丰富(需 BYOK 凭证),默认走零 LLM 的确定性重建。"
         "剧本编辑器里『只重做某一个模块』用它;会覆盖该模块已派生数据(destructive)。",
         {"type": "object",
          "properties": {
              "script_id": {"type": "integer"},
              "module": {"type": "string",
                         "enum": list(_REBUILD_MODULE_NAMES)},
              "source": {"type": "string",
                         "description": "可选: 'llm' 走 LLM 丰富(canon/worldbook/cards),默认确定性零 LLM"},
          },
          "required": ["script_id", "module"]},
         _t_rebuild_script_module, _USER_DEST, True),
        ("delete_script", "永久删除剧本及其所有派生数据",
         {"type": "object",
          "properties": {"script_id": {"type": "integer"}, "force": {"type": "boolean", "default": False}},
          "required": ["script_id"]},
         _t_delete_script, _USER_DEST, True),
        ("probe_models", "探测可用模型 (异步,可能耗时)",
         {"type": "object", "properties": {"api_id": {"type": "string"}}},
         _t_probe_models, _USER_MUTATE, False),  # 触发外部 LLM 调用,LLM 不能自启
    ]
    for name, desc, schema, exec_, origins, destructive in user_specs:
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope="user", origins=origins, destructive=destructive,
            ))


__all__ = ["register_imports_tools"]
