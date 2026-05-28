"""
test_state_repository_single_source.py
======================================

完整重构后:state 真相源严格走 runtime_checkouts (per-user/save) →
branch_commits[commit_id] (per-commit immutable),**不再退化到 game_saves.state_snapshot
不指定 save 的 fallback**。

这修了 "用户切到 save A,Game Console 看到 save B 的 state" 的核心 bug —
之前 _load_save_snapshot(user_id) 用 ORDER BY updated_at DESC LIMIT 1,
拿到的是用户上次玩的 save (updated_at 最新),不是当前激活的 save。

本测试 4 层:
  Layer A — state_repository 主流程不再调 _load_save_snapshot
  Layer B — _load_runtime_checkout_snapshot / _load_commit_snapshot 都强制 save_id 限制
  Layer C — _ensure_loaded 自检比较 (save_id, commit_id) 双字段
  Layer D — tree() 返回 active_commit_id 用 user_runtime 真相源
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
REPO_PY = (PROJECT / "rpg" / "state_repository.py").read_text(encoding="utf-8")
APP_PY = (PROJECT / "rpg" / "app.py").read_text(encoding="utf-8")
_branches_pkg = PROJECT / "rpg" / "platform_app" / "branches"
_branches_py_path = _branches_pkg if _branches_pkg.is_dir() else (PROJECT / "rpg" / "platform_app" / "branches.py")
if _branches_pkg.is_dir():
    # Phase 5.7: branches 已拆成子包，tree() 在 tree_ops.py
    BRANCHES_PY = (_branches_pkg / "tree_ops.py").read_text(encoding="utf-8")
else:
    BRANCHES_PY = _branches_py_path.read_text(encoding="utf-8")
PLATFORM_JSX = (PROJECT / "frontend" / "src" / "platform-app.jsx").read_text(encoding="utf-8")


class LoadActiveStateSingleSource(unittest.TestCase):
    """load_active_state 主流程必须先 runtime_checkouts 后 commit snapshot,
    且都带 save_id 限制。不再走 _load_save_snapshot(user_id)。"""

    def test_main_path_calls_runtime_checkout_first(self):
        idx = REPO_PY.find("def load_active_state(")
        end = REPO_PY.find("\ndef ", idx + 1)
        body = REPO_PY[idx:end if end > 0 else len(REPO_PY)]
        # 必须有 _load_runtime_checkout_snapshot 调用
        self.assertIn("_load_runtime_checkout_snapshot", body,
            "load_active_state 必须调 _load_runtime_checkout_snapshot 拿 working tree state")

    def test_main_path_calls_commit_snapshot_second(self):
        idx = REPO_PY.find("def load_active_state(")
        end = REPO_PY.find("\ndef ", idx + 1)
        body = REPO_PY[idx:end if end > 0 else len(REPO_PY)]
        self.assertIn("_load_commit_snapshot", body,
            "load_active_state 必须调 _load_commit_snapshot 作 commit 级真相源")

    def test_main_path_does_not_call_legacy_save_snapshot(self):
        idx = REPO_PY.find("def load_active_state(")
        end = REPO_PY.find("\ndef ", idx + 1)
        body = REPO_PY[idx:end if end > 0 else len(REPO_PY)]
        # 不能调 _legacy_load_save_snapshot (那是历史 bug 现场)
        self.assertNotIn("_legacy_load_save_snapshot", body,
            "主路径不应调 _legacy_load_save_snapshot — 那是不指定 save_id 的兜底,会读错 save")
        # 也不应调旧名 _load_save_snapshot
        self.assertNotIn("_load_save_snapshot", body,
            "主路径不应调 _load_save_snapshot — 该函数已 rename + 退役")

    def test_runtime_checkout_snapshot_requires_save_id(self):
        """_load_runtime_checkout_snapshot 必须用 save_id 过滤 query。"""
        idx = REPO_PY.find("def _load_runtime_checkout_snapshot(")
        end = REPO_PY.find("\ndef ", idx + 1)
        body = REPO_PY[idx:end if end > 0 else len(REPO_PY)]
        self.assertIn("save_id", body)
        self.assertIn("user_id", body)
        # SQL where 应有 save_id 限制
        self.assertTrue(
            re.search(r"where\s+save_id\s*=\s*%s", body, re.IGNORECASE) is not None,
            "SQL where 必须用 save_id = %s 限制",
        )

    def test_commit_snapshot_requires_save_id_and_user_check(self):
        idx = REPO_PY.find("def _load_commit_snapshot(")
        end = REPO_PY.find("\ndef ", idx + 1)
        body = REPO_PY[idx:end if end > 0 else len(REPO_PY)]
        # commit query 必须用 save_id 限制
        self.assertTrue(
            re.search(r"where\s+id\s*=\s*%s\s+and\s+save_id\s*=\s*%s", body, re.IGNORECASE) is not None,
            "commit query 应用 (id, save_id) 双限制",
        )
        # user_id 校验:先查 game_saves 归属
        self.assertIn("game_saves where id = %s and user_id = %s", body,
            "_load_commit_snapshot 必须先校验 save 归属当前 user")


class EnsureLoadedTwoFieldDriftCheck(unittest.TestCase):
    """_ensure_loaded 自检要比较 (save_id, commit_id) 双字段。"""

    def test_state_commit_id_dict_defined(self):
        self.assertIn("_state_commit_id_by_user", APP_PY,
            "app.py 应有 _state_commit_id_by_user dict 跟踪 cached 对应 commit_id")

    def test_ensure_loaded_reads_commit_id_from_runtime(self):
        idx = APP_PY.find("def _ensure_loaded(")
        end = APP_PY.find("\ndef ", idx + 1)
        body = APP_PY[idx:end if end > 0 else len(APP_PY)]
        self.assertIn("active_commit_id", body)
        self.assertIn("_rt_commit", body,
            "_ensure_loaded 必须读 runtime.active_commit_id 跟 cached 比较")

    def test_ensure_loaded_invalidates_on_commit_drift(self):
        idx = APP_PY.find("def _ensure_loaded(")
        end = APP_PY.find("\ndef ", idx + 1)
        body = APP_PY[idx:end if end > 0 else len(APP_PY)]
        self.assertIn("commit_drift", body,
            "_ensure_loaded 必须按 commit_drift 判断失效")

    def test_invalidate_user_cache_clears_commit_dict(self):
        idx = APP_PY.find("def _invalidate_user_cache(")
        end = APP_PY.find("\ndef ", idx + 1)
        body = APP_PY[idx:end if end > 0 else len(APP_PY)]
        self.assertIn("_state_commit_id_by_user.pop", body,
            "_invalidate_user_cache 也清 _state_commit_id_by_user")


class BranchesTreeUsesRuntimeActiveCommit(unittest.TestCase):
    """branches.tree() 不再用 game_saves.active_commit_id 作真相源,
    改用 user_runtime.active_commit_id (per-user)。"""

    def test_tree_reads_runtime_active_commit(self):
        idx = BRANCHES_PY.find("def tree(")
        end = BRANCHES_PY.find("\ndef ", idx + 1)
        body = BRANCHES_PY[idx:end if end > 0 else len(BRANCHES_PY)]
        self.assertIn("read_runtime", body,
            "tree() 必须调 runtime.read_runtime 拿真实 active_commit_id")

    def test_tree_exposes_active_commit_id_in_response(self):
        idx = BRANCHES_PY.find("def tree(")
        end = BRANCHES_PY.find("\ndef ", idx + 1)
        body = BRANCHES_PY[idx:end if end > 0 else len(BRANCHES_PY)]
        # return 体里应该有 active_commit_id key
        self.assertIn('"active_commit_id":', body,
            "tree() 返回顶层 active_commit_id (前端 BranchGraph 用这个标 HEAD)")


class ContinuePickerShowsRealRefs(unittest.TestCase):
    """ContinuePicker 第 2 步显示真实 ref 名,不再是硬编码"分支 0 · 主线"。"""

    def test_no_hardcoded_branch_label(self):
        # 不应有 "分支 {n.branch}" 这种硬编码
        self.assertNotIn("分支 {n.branch}", PLATFORM_JSX,
            "ContinuePicker 不应再硬编码 '分支 {n.branch}' — 显示真实 ref")
        # 也不应再用 BRANCH_LABELS[n.branch] mock
        self.assertNotIn("BRANCH_LABELS[n.branch]", PLATFORM_JSX,
            "不再用 BRANCH_LABELS mock 显示分支")

    def test_uses_ref_names_from_backend(self):
        # 必须读 n.ref_names (后端 tree 返回的真实 refs 字段)
        idx = PLATFORM_JSX.find("function ContinuePicker(")
        end = PLATFORM_JSX.find("\nfunction ", idx + 1)
        body = PLATFORM_JSX[idx:end if end > 0 else len(PLATFORM_JSX)]
        self.assertIn("ref_names", body,
            "ContinuePicker 必须读 n.ref_names 显示真实分支名")
        self.assertIn("short_refs", body,
            "ContinuePicker 应把 ref 名截短 (refs/heads/main → main)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
