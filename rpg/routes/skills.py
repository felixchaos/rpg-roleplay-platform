"""skills.py — Skill 导入与运行路由 (/api/skills/*)。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from routes._deps_fastapi import get_current_admin
from schemas._common import COMMON_ERROR_RESPONSES, ErrorResponse, GenericOkResponse
from schemas.skills import SkillRunRequest, SkillsImportRequest

router = APIRouter()


@router.post("/api/skills/import", response_model=GenericOkResponse, responses=COMMON_ERROR_RESPONSES)
async def api_skills_import(
    body: SkillsImportRequest,
    api_user: dict[str, Any] | None = Depends(get_current_admin),
) -> JSONResponse:
    from app import import_skill_bundle, tool_payload
    body_dict = body.model_dump(exclude_none=True)
    try:
        skill = import_skill_bundle(body_dict.get("file", {}))
        return JSONResponse({"ok": True, "skill": skill, "tools": tool_payload()})
    except (PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/skills/{skill_id}/run", response_model=GenericOkResponse, responses={**COMMON_ERROR_RESPONSES, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}})
async def api_skill_run(
    body: SkillRunRequest,
    skill_id: str,
    api_user: dict[str, Any] = Depends(get_current_admin),
) -> JSONResponse:
    """在沙箱里跑某个 imported skill。

    Body: {"cmd": ["bash", "script.sh", "arg1"], "stdin": "...", "timeout_sec": 30}

    安全：admin only。local 模式下也强制要求 admin —— 不再做匿名豁免，避免
    "本地未鉴权 + cmd 可任意" 的 RCE 链。
    """
    body_dict = body.model_dump(exclude_none=True)
    cmd = body_dict.get("cmd") or body_dict.get("command")
    if not isinstance(cmd, list) or not cmd:
        return JSONResponse({"ok": False, "error": "cmd 必须是非空 list"}, status_code=400)
    if not all(isinstance(x, str) for x in cmd):
        return JSONResponse({"ok": False, "error": "cmd 所有元素必须是字符串"}, status_code=400)
    if len(cmd) > 64 or any(len(x) > 4096 for x in cmd):
        return JSONResponse({"ok": False, "error": "cmd 元素或长度超限"}, status_code=400)

    # P0-1 SEC: cmd[0] 白名单 + 路径穿越防御
    _CMD_WHITELIST = {"bash", "sh", "python3", "python", "node", "ruby"}
    cmd0 = cmd[0]
    if "/" in cmd0:
        return JSONResponse({"ok": False, "error": "cmd[0] 不能包含 /，必须是裸文件名"}, status_code=400)
    if cmd0 not in _CMD_WHITELIST:
        return JSONResponse({"ok": False, "error": f"cmd[0] 必须在白名单内: {sorted(_CMD_WHITELIST)}"}, status_code=400)
    if any(".." in part for part in cmd):
        return JSONResponse({"ok": False, "error": "cmd 元素不能包含 .."}, status_code=400)

    # 找 skill_id 对应的目录
    from tools_dsl.tool_registry import list_imported_skills
    skill = next((s for s in list_imported_skills() if s.get("id") == skill_id), None)
    if not skill:
        return JSONResponse({"ok": False, "error": f"skill 不存在: {skill_id}"}, status_code=404)
    skill_path = skill.get("path") or ""
    if not skill_path:
        return JSONResponse({"ok": False, "error": "skill 路径丢失"}, status_code=500)

    # 找 skill 根目录（SKILL.md 的父目录）
    from pathlib import Path as _Path
    skill_root = _Path(skill_path).parent

    import skill_executor
    result = skill_executor.run_skill_command(
        cmd=cmd,
        skill_root=skill_root,
        timeout_sec=int(body_dict.get("timeout_sec") or skill_executor.DEFAULT_TIMEOUT_SEC),
        stdin_text=body_dict.get("stdin"),
    )
    return JSONResponse({"ok": True, **result})
