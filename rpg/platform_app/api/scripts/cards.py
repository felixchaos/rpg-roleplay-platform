"""platform_app.api.scripts.cards —— 剧本 NPC 角色卡族 + AI 复核端点。

列表/详情/upsert/删除/enabled/protagonist + import-tavern(酒馆卡导入)+ audit-cards。
"""
from __future__ import annotations

import asyncio

from fastapi import Depends, Request

from ... import knowledge
from .._card_import import parse_card_import_request
from .._deps import json_response, require_user, value_error_response
from ._shared import router


@router.get("/api/scripts/{script_id}/character-cards")
async def api_script_character_cards(script_id: int, limit: int | None = None, cursor: str | None = None, user=Depends(require_user)):
    try:
        return json_response({"ok": True, **knowledge.list_character_cards(user["id"], script_id, limit, cursor)})
    except ValueError as exc:
        return value_error_response(exc)


@router.get("/api/scripts/{script_id}/character-cards/{card_id}")
async def api_script_character_card(script_id: int, card_id: int, user=Depends(require_user)):
    """单条剧本角色卡详情。"""
    try:
        card = knowledge.get_character_card(user["id"], script_id, card_id)
    except ValueError as exc:
        return value_error_response(exc, status_code=403)
    if not card:
        return json_response({"ok": False, "error": "character_card 不存在"}, status_code=404)
    return json_response({"ok": True, "card": card})


@router.post("/api/scripts/{script_id}/character-cards")
async def api_script_upsert_character_card(request: Request, script_id: int, user=Depends(require_user)):
    """创建/更新剧本角色卡（payload 传 id 则 update，否则 insert）。"""
    body = await request.json()
    try:
        return json_response({"ok": True, "card": knowledge.upsert_character_card(user["id"], script_id, body)})
    except ValueError as exc:
        return value_error_response(exc)
    except Exception as exc:
        # 兜底:改名撞同名等唯一约束冲突(罕见竞态/其它路径)别冒成 500「保存没反应」,
        # 转成可行动 400。upsert 内 with connect() 已回滚,连接干净归还。
        try:
            from psycopg.errors import UniqueViolation
            if isinstance(exc, UniqueViolation):
                return json_response(
                    {"ok": False, "error": "该剧本已存在同名 NPC 角色卡,请改用不同的名字"},
                    status_code=400,
                )
        except Exception:
            pass
        raise


@router.post("/api/scripts/{script_id}/character-cards/import-tavern")
async def api_script_import_tavern_card(request: Request, script_id: int, user=Depends(require_user)):
    """把酒馆(SillyTavern)角色卡导入为**本剧本的 NPC 角色卡**。**仅 owner**。

    群反馈(白玖):剧本详情「NPC 角色卡」只能一个字段一个字段手建,手里现成的酒馆卡
    没有入口。请求解析(multipart 文件 / 粘贴 JSON / PNG 内嵌卡 / base64)与用户卡导入
    共用 api/_card_import;字段映射共用 tavern_cards.tavern_to_npc_card;同名卡走
    knowledge.import_character_card 合并(保住该卡在本剧本里的章节/重要度/主角锁)。

    返回 {"ok", "card", "replaced", "imported_from", "llm_structured"}。
    """
    from ... import tavern_cards
    from ..me._shared import _store_imported_card_image

    try:
        # image_bytes: PNG/WEBP 卡的原图(卡本身即头像);ai_split: 用户显式 opt-in 才挂 LLM
        v2, image_bytes, ai_split = await parse_card_import_request(request)
        payload = tavern_cards.tavern_to_npc_card(v2)
        if ai_split:
            # LLM 兜底拆字段(同步调用包进线程,失败不阻断导入)。模型走 card_import 统一配置。
            try:
                payload, _used = await asyncio.to_thread(
                    tavern_cards.apply_llm_structure, payload, user["id"]
                )
            except Exception:
                pass
        result = knowledge.import_character_card(user["id"], script_id, payload)
        card = result["card"]
        # PNG/WEBP 卡自带立绘 → 存为该 NPC 卡头像 + 登记文件库。失败不阻断导入(卡已落库)。
        if image_bytes and isinstance(card, dict) and card.get("id"):
            try:
                _store_imported_card_image(user["id"], int(card["id"]), image_bytes, script_id=script_id)
                card = knowledge.get_character_card(user["id"], script_id, int(card["id"])) or card
            except Exception as _img_exc:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "[script-import-tavern] store card image failed: %s", _img_exc)
        return json_response({
            "ok": True, "card": card, "replaced": bool(result["replaced"]),
            "imported_from": "tavern_v2",
            "llm_structured": bool((payload.get("metadata") or {}).get("llm_structured_description")),
        })
    except ValueError as exc:
        return value_error_response(exc)


@router.post("/api/scripts/{script_id}/character-cards/{card_id}/delete")
async def api_script_delete_character_card(script_id: int, card_id: int, user=Depends(require_user)):
    try:
        return json_response(knowledge.delete_character_card(user["id"], script_id, card_id))
    except ValueError as exc:
        return value_error_response(exc, status_code=403)


@router.post("/api/scripts/{script_id}/character-cards/{card_id}/enabled")
async def api_script_card_enabled(request: Request, script_id: int, card_id: int, user=Depends(require_user)):
    """快捷切换 enabled（检索中临时屏蔽某角色）。"""
    body = await request.json()
    try:
        return json_response({"ok": True, "card": knowledge.set_character_card_enabled(
            user["id"], script_id, card_id, bool(body.get("enabled", True))
        )})
    except ValueError as exc:
        return value_error_response(exc)


@router.post("/api/scripts/{script_id}/character-cards/{card_id}/protagonist")
async def api_script_card_protagonist(script_id: int, card_id: int, user=Depends(require_user)):
    """手动把某 NPC 卡设为该剧本主角（仅 owner）。

    canon importance 误判会把配角标成主角；此接口清掉其它卡的主角标记 + 锁定目标卡,
    锁定后重新提取(canon 重排)不会再覆盖人工指定。
    """
    try:
        return json_response({"ok": True, "card": knowledge.set_character_card_protagonist(
            user["id"], script_id, card_id
        )})
    except ValueError as exc:
        return value_error_response(exc)


@router.post("/api/scripts/{script_id}/audit-cards")
async def api_audit_character_cards(request: Request, script_id: int, user=Depends(require_user)):
    """按需 AI 复核本剧本全部 NPC 角色卡(仅 owner)。

    用前端公用模型选择器选的模型(body.api_id/model,缺省读 card_audit.* 偏好→提取器默认)对全部
    NPC 卡做一次批量裁决:合并同人卡 / 锁定真主角 / 删非人名卡。按需触发,不进导入流水线 → 零自动成本。
    """
    body = await request.json()
    api_id = str(body.get("api_id") or "").strip()
    model = str(body.get("model") or body.get("model_real_name") or "").strip()
    from platform_app import import_pipeline
    try:
        # 异步:进 import_jobs → 全局后台任务浮窗跟踪,前端可关弹窗/离开页面;完成后读回摘要。
        from platform_app.knowledge.card_audit import schedule_card_audit
        return json_response({"ok": True, **schedule_card_audit(user["id"], script_id, api_id, model)})
    except import_pipeline.MissingUserCredentialError as exc:
        return json_response({
            "ok": False, "code": "credentials_required", "needs_credentials": True,
            "api_id": exc.api_id, "model": exc.model, "credential_api_id": exc.credential_api_id,
            "settings_hash": "settings-models", "error": str(exc),
        }, status_code=400)
    except ValueError as exc:
        return value_error_response(exc)
