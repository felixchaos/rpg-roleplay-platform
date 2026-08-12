"""persona_skills.py —— 用户人格 skill 导入路由(/api/me/persona-skills/*)。

人格 skill = 纯 markdown 角色档案(skill.md 上传 / GitHub 公开仓库拉取),蒸馏成角色卡 + 人设图。
**绝不执行代码**,与 admin-only 的 /api/skills(可执行 skill)分离。每用户隔离,所有操作经 get_current_user。
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from platform_app.api._deps import json_response, value_error_response
from routes._deps_fastapi import get_current_user

router = APIRouter()


def _uid(api_user: dict[str, Any] | None) -> int | None:
    if not api_user:
        return None
    try:
        return int(api_user.get("id"))
    except Exception:
        return None


@router.post("/api/me/persona-skills/import")
async def api_persona_skill_import(
    request: Request,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    """body:
      {"source":"upload","files":[{"name":"skill.md","content":"..."}], "generate_image":true}
      {"source":"github","repo_url":"https://github.com/owner/repo", "generate_image":true}
    可选 {"model_api_id","model"} 指定蒸馏模型。
    """
    uid = _uid(api_user)
    if not uid:
        return json_response({"ok": False, "error": "需要登录"}, status_code=401)
    body = await request.json() or {}
    source = str(body.get("source") or "upload")
    if source not in ("upload", "github"):
        return json_response({"ok": False, "error": "source 必须是 upload 或 github"}, status_code=400)
    files = body.get("files") if isinstance(body.get("files"), list) else []
    repo_url = str(body.get("repo_url") or "")
    if source == "github" and not repo_url:
        return json_response({"ok": False, "error": "缺少 repo_url"}, status_code=400)
    if source == "upload" and not files:
        return json_response({"ok": False, "error": "缺少上传的 .md 文件"}, status_code=400)

    from platform_app.persona_skills import import_persona_skill
    try:
        result = await asyncio.to_thread(
            import_persona_skill,
            uid,
            source=source,
            files=files,
            repo_url=repo_url,
            model_api_id=(body.get("model_api_id") or None),
            model=(body.get("model") or None),
            generate_image=bool(body.get("generate_image", False)),
            use_llm=bool(body.get("use_llm", False)),
        )
        return json_response(result)
    except ValueError as exc:
        return value_error_response(exc)
    except Exception as exc:  # noqa: BLE001
        return json_response({"ok": False, "error": f"导入失败: {exc}"}, status_code=500)


@router.get("/api/me/persona-skills")
async def api_persona_skills_list(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    uid = _uid(api_user)
    if not uid:
        return json_response({"ok": False, "error": "需要登录"}, status_code=401)
    from platform_app.persona_skills import list_persona_skills
    return json_response(await asyncio.to_thread(list_persona_skills, uid))


@router.post("/api/me/persona-skills/{skill_id}/delete")
async def api_persona_skill_delete(
    skill_id: int,
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> JSONResponse:
    uid = _uid(api_user)
    if not uid:
        return json_response({"ok": False, "error": "需要登录"}, status_code=401)
    from platform_app.persona_skills import delete_persona_skill
    return json_response(await asyncio.to_thread(delete_persona_skill, uid, int(skill_id)))
