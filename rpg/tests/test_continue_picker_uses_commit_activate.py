"""
test_continue_picker_uses_commit_activate.py
============================================

用户报告:点"从某个节点继续",弹出来的 ContinuePicker 第二步选了 #13 节点
点"继续游戏"后,进 Game Console 看到的不是 #13 内容,而是别的剧情(乱码存档)。

根因 (两层):
  1. 前端 ContinuePicker.confirm() 完全丢掉用户选的 pickedNode,只调
     saves.activate(targetSaveId) 切 save 级,后端按 game_saves.active_commit_id
     加载该 save 当前活跃 commit (可能是末尾另一 commit),用户选 #13 的意图被
     忽略 → 看到错 state
  2. 后端 /api/branches/activate 和 /api/branches/continue handler 没清
     app._state_by_user 缓存。我之前加的 _ensure_loaded 自检只比较 save_id,
     **同 save 内换 commit 缓存依然命中** → 即便 ContinuePicker 改对了,Game
     Console 进去 /api/state 仍读旧 cached state

修复 (两层都要):
  Frontend (platform-app.jsx ContinuePicker.confirm):
    · pickedNode 存在 → 调 branches.activate({node_id: pickedNode, commit_id: pickedNode})
      这会同时切 save + commit + runtime snapshot (git checkout 语义)
    · pickedNode 不存在 → fallback saves.activate(savesId) (只切 save 级)
  Backend (platform_app/api.py):
    · /api/branches/activate handler 末尾 import app + _invalidate_user_cache(user)
    · /api/branches/continue handler 同上
    · 不再依赖 _ensure_loaded 自检对 commit_id 变化的识别

本测试 3 层:
  Layer A — ContinuePicker.confirm 用 commit-level branches.activate
  Layer B — 后端两个 handler 都调 _invalidate_user_cache
  Layer C — confirm 没选节点时仍 fallback 到 saves.activate (向后兼容)
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
PLATFORM_JSX = (PROJECT / "frontend" / "src" / "platform-app.jsx").read_text(encoding="utf-8")
API_PY = (PROJECT / "rpg" / "platform_app" / "api.py").read_text(encoding="utf-8")


def _continue_picker_confirm_body() -> str:
    """提取 ContinuePicker.confirm 函数体。"""
    idx = PLATFORM_JSX.find("function ContinuePicker(")
    assert idx > 0
    end = PLATFORM_JSX.find("\nfunction ", idx + 1)
    body = PLATFORM_JSX[idx:end if end > 0 else len(PLATFORM_JSX)]
    # 进一步 narrow 到 confirm
    cidx = body.find("const confirm = async ()")
    if cidx < 0:
        cidx = body.find("confirm = async ()")
    assert cidx > 0, "ContinuePicker 内应有 confirm async 函数"
    cend_marker = body.find("location.href", cidx)
    cend = body.find("};", cend_marker) + 2 if cend_marker > 0 else len(body)
    return body[cidx:cend]


# ────────────────────────────────────────────────────────────
# Layer A: ContinuePicker.confirm 用 commit-level activate
# ────────────────────────────────────────────────────────────


class ContinuePickerUsesBranchActivate(unittest.TestCase):
    def test_confirm_calls_branches_activate_when_picked_node(self):
        body = _continue_picker_confirm_body()
        # 必须调 branches.activate({node_id: pickedNode})
        self.assertIn("window.api.branches.activate", body,
            "confirm 必须用 branches.activate 切 commit 级,不能只 saves.activate")
        self.assertIn("node_id: pickedNode", body,
            "branches.activate 调用必须传 node_id=pickedNode (用户选的 commit)")

    def test_confirm_picks_node_branch_for_specific_commit(self):
        body = _continue_picker_confirm_body()
        # 必须有 if (pickedNode != null) 分支
        self.assertTrue(
            re.search(r"pickedNode\s*!=\s*null", body) is not None,
            "confirm 必须按 pickedNode 是否存在分流",
        )

    def test_confirm_fallback_to_saves_activate_when_no_node(self):
        """没选节点时仍走 saves.activate (向后兼容,例如直接选了存档没翻到 branch step)。"""
        body = _continue_picker_confirm_body()
        # 保留 saves.activate 作为 fallback
        self.assertIn("window.api.saves.activate", body,
            "没选节点时应 fallback 到 saves.activate")

    def test_confirm_aborts_without_target_save(self):
        body = _continue_picker_confirm_body()
        # 缺 targetSaveId → toast + return,不带旧 runtime 进游戏
        self.assertIn("没选目标存档", body)
        self.assertTrue(
            "return;" in body and re.search(r"if\s*\(!\s*targetSaveId\s*\)", body) is not None,
            "缺 save id 必须 abort 不进游戏",
        )


# ────────────────────────────────────────────────────────────
# Layer B: 后端 branches handler 显式清 app 缓存
# ────────────────────────────────────────────────────────────


class BranchesHandlerInvalidatesCache(unittest.TestCase):
    """/api/branches/activate 和 /api/branches/continue 在 commit 级操作后,
    必须显式清 app._state_by_user 缓存 (不能依赖 _ensure_loaded 的 save_id
    自检 — 同 save 内换 commit 时自检不会触发)。"""

    def test_activate_handler_invalidates_cache(self):
        idx = API_PY.find("@router.post(\"/api/branches/activate\")")
        self.assertGreater(idx, 0)
        end = API_PY.find("@router.", idx + 1)
        body = API_PY[idx:end if end > 0 else len(API_PY)]
        self.assertIn("_invalidate_user_cache", body,
            "/api/branches/activate handler 必须调 app._invalidate_user_cache(user)")
        self.assertIn("import app", body,
            "handler 必须 import app 才能调 _invalidate_user_cache")

    def test_continue_handler_invalidates_cache(self):
        idx = API_PY.find("@router.post(\"/api/branches/continue\")")
        self.assertGreater(idx, 0)
        end = API_PY.find("@router.", idx + 1)
        body = API_PY[idx:end if end > 0 else len(API_PY)]
        self.assertIn("_invalidate_user_cache", body,
            "/api/branches/continue handler 必须调 app._invalidate_user_cache(user)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
