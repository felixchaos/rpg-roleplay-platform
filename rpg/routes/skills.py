"""skills.py — Skill 导入与运行路由 (/api/skills/*)。"""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from schemas.skills import SkillsImportRequest, SkillRunRequest

router = APIRouter()


@router.post("/api/skills/import")
async def api_skills_import(body: SkillsImportRequest, request: Request) -> JSONResponse:
    from app import _require_api_user, tool_payload, import_skill_bundle
    _require_api_user(request, admin=True)
    body_dict = body.model_dump(exclude_none=True)
    try:
        skill = import_skill_bundle(body_dict.get("file", {}))
        return JSONResponse({"ok": True, "skill": skill, "tools": tool_payload()})
    except (PermissionError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/skills/{skill_id}/run")
async def api_skill_run(body: SkillRunRequest, request: Request, skill_id: str) -> JSONResponse:
    """在沙箱里跑某个 imported skill。

    Body: {"cmd": ["bash", "script.sh", "arg1"], "stdin": "...", "timeout_sec": 30}

    安全：admin only；本地匿名也允许（开发场景）。
    """
    from app import _require_api_user, _api_auth_required
    api_user = _require_api_user(request)
    if _api_auth_required() and (not api_user or api_user.get("role") != "admin"):
        return JSONResponse({"ok": False, "error": "需要管理员权限"}, status_code=403)

    body_dict = body.model_dump(exclude_none=True)
    cmd = body_dict.get("cmd") or body_dict.get("command")
    if not isinstance(cmd, list) or not cmd:
        return JSONResponse({"ok": False, "error": "cmd 必须是非空 list"}, status_code=400)

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
