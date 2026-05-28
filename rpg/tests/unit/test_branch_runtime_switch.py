"""
test_branch_runtime_switch.py
=============================

用户报告:游戏内点"分叉"+ 分支树侧边栏点"切到分支"/"从此继续"全失败 —
runtime 没切换,/api/state 还显示旧 state。

根因 (Codex 三连诊断):
  1. branches.activate_node / continue_from 写完 DB 不清 app.py 的
     _state_by_user[uid] 内存缓存 → 下次 /api/state 返回旧 GameState
  2. _ensure_loaded 没校验 cached.save_id 跟 user_runtime.save_id 一致
  3. 游戏内 BranchTreeRail 没给 BranchGraph 传 onActivate / onContinue
     → 按钮等于隐形

修复:
  Backend (rpg/app.py):
    · 加 _state_save_id_by_user 字典记录 cached state 对应 save_id
    · _ensure_loaded 顶部加一致性自检:读 user_runtime.save_id,跟
      _state_save_id_by_user[uid] 比较;不一致 → 缓存失效,reload
    · load 后写 _state_save_id_by_user[uid] = runtime_meta.save_id
    · _invalidate_user_cache 也清这个 dict
  Frontend (game-app.jsx):
    · BranchTreeRail 给 BranchGraph 传 onActivate + onContinue
    · 回调里调 branches.activate / continueFrom + dispatch
      rpg-state-reload 让 Game Console 重新拉 /api/state

本测试 5 层:
  Layer A — _state_save_id_by_user 字典存在 + ensure_loaded 自检 + invalidate 清
  Layer B — 一致性自检端到端:模拟 activate 后再读 state 拿到新 save
  Layer C — 前端 BranchTreeRail 传 onActivate + onContinue 给 BranchGraph
  Layer D — 回调调对 API + dispatch 事件
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
APP_PY = (PROJECT / "rpg" / "app.py").read_text(encoding="utf-8")
GAME_APP = (PROJECT / "frontend" / "src" / "game-app.jsx").read_text(encoding="utf-8")


# ────────────────────────────────────────────────────────────
# Layer A: 后端缓存一致性基础设施
# ────────────────────────────────────────────────────────────


class StateCacheSaveIdTracking(unittest.TestCase):
    def test_state_save_id_by_user_dict_defined(self):
        self.assertIn("_state_save_id_by_user", APP_PY,
            "app.py 应有 _state_save_id_by_user 字典记录 cached state 对应 save_id")
        # 类型声明
        self.assertTrue(
            re.search(r"_state_save_id_by_user\s*:\s*dict\[int,\s*int\]", APP_PY) is not None,
            "_state_save_id_by_user 应声明为 dict[int, int]",
        )

    def test_ensure_loaded_has_consistency_check(self):
        idx = APP_PY.find("def _ensure_loaded(")
        self.assertGreater(idx, 0)
        end = APP_PY.find("\ndef ", idx + 1)
        body = APP_PY[idx:end if end > 0 else len(APP_PY)]
        # 必读 user_runtime.save_id 跟 cached 比较
        self.assertIn("read_runtime", body,
            "_ensure_loaded 必须调 read_runtime 拿当前 user_runtime.save_id")
        self.assertIn("_state_save_id_by_user", body,
            "_ensure_loaded 必须比较 cached _state_save_id_by_user[uid]")
        # 不一致 → cached = None
        self.assertTrue(
            "_rt_save != _cached_save" in body or "rt_save != cached_save" in body or "rt_save_id != cached_save_id" in body,
            "_ensure_loaded 必须在 cached.save_id 跟 runtime.save_id 不一致时 invalidate",
        )

    def test_invalidate_user_cache_clears_save_id_dict(self):
        idx = APP_PY.find("def _invalidate_user_cache(")
        self.assertGreater(idx, 0)
        end = APP_PY.find("\ndef ", idx + 1)
        body = APP_PY[idx:end if end > 0 else len(APP_PY)]
        self.assertIn("_state_save_id_by_user.pop", body,
            "_invalidate_user_cache 也应清 _state_save_id_by_user")


# ────────────────────────────────────────────────────────────
# Layer B: 一致性自检端到端
# ────────────────────────────────────────────────────────────


class EnsureLoadedReloadsOnRuntimeSwitch(unittest.TestCase):
    """模拟:cached state 对应 save A,user_runtime 被切到 save B,
    再次 _ensure_loaded 必须 reload 新 state (而不是返回 cached)。"""

    def test_simulated_runtime_switch_triggers_reload(self):
        """直接构造 _state_by_user[uid] + _state_save_id_by_user[uid] = oldSaveA,
        mock read_runtime 返回 saveB,调 _ensure_loaded(api_user),验证返回的 state
        不是 cached 那个 (因为缓存失效后会重新 load,即便 load_active_state 也 mock
        return 一个新对象)。"""
        import sys
        sys.path.insert(0, str(PROJECT / "rpg"))
        # 重要:不要真启动后端,只单元测 _ensure_loaded 的分支逻辑
        import copy
        from unittest import mock

        import app as _app
        from state import DEFAULT_STATE, GameState
        uid = 9999  # 不会冲突的测试 uid
        old_state = GameState(copy.deepcopy(DEFAULT_STATE))
        old_state.data["player"] = {"name": "OldPlayer"}
        _app._state_by_user[uid] = old_state
        _app._state_save_id_by_user[uid] = 100  # cached 对应 save 100

        # mock read_runtime 返回 save 200 (用户在别处切到 save 200)
        new_state = GameState(copy.deepcopy(DEFAULT_STATE))
        new_state.data["player"] = {"name": "NewPlayer"}
        with mock.patch("platform_app.runtime.read_runtime",
                        return_value={"user_id": uid, "save_id": 200}), \
             mock.patch("state_repository.load_active_state",
                        return_value=(new_state, {"save_id": 200})):
            # 注:_user_key 接受 dict 形 api_user;构造一个
            api_user = {"id": uid, "username": "tester"}
            returned = _app._ensure_loaded(api_user)
        try:
            self.assertIs(returned, new_state,
                "user_runtime 已切到新 save,_ensure_loaded 应返回新加载的 state 而非 cached old")
            self.assertEqual(_app._state_save_id_by_user.get(uid), 200,
                "reload 后 _state_save_id_by_user[uid] 应更新成新 save_id")
        finally:
            _app._state_by_user.pop(uid, None)
            _app._state_save_id_by_user.pop(uid, None)
            _app._gm_by_user.pop(uid, None)


# ────────────────────────────────────────────────────────────
# Layer C: 前端 BranchTreeRail 把 callback 传给 BranchGraph
# ────────────────────────────────────────────────────────────


class GameRailPassesCallbacks(unittest.TestCase):
    def test_branch_tree_rail_passes_onActivate(self):
        # 找 BranchTreeRail 函数体
        idx = GAME_APP.find("function BranchTreeRail(")
        self.assertGreater(idx, 0)
        end = GAME_APP.find("\nfunction ", idx + 1)
        body = GAME_APP[idx:end if end > 0 else len(GAME_APP)]
        # 必须给 BranchGraph 传 onActivate
        self.assertIn("onActivate", body,
            "BranchTreeRail 必须给 BranchGraph 传 onActivate 回调,否则按钮隐藏 (game-app.jsx)")
        # 必须传 onContinue
        self.assertIn("onContinue", body,
            "BranchTreeRail 必须给 BranchGraph 传 onContinue 回调")

    def test_callbacks_call_correct_api(self):
        idx = GAME_APP.find("function BranchTreeRail(")
        end = GAME_APP.find("\nfunction ", idx + 1)
        body = GAME_APP[idx:end if end > 0 else len(GAME_APP)]
        # onActivate 应该调 window.api.branches.activate
        self.assertIn("window.api.branches.activate", body,
            "BranchTreeRail onActivate 应调 window.api.branches.activate(node_id)")
        # onContinue 应该调 window.api.branches.continueFrom
        self.assertIn("window.api.branches.continueFrom", body,
            "BranchTreeRail onContinue 应调 window.api.branches.continueFrom(node_id)")

    def test_callbacks_dispatch_reload_event(self):
        """成功后必须 dispatch rpg-state-reload + rpg-saves-updated 让前端刷新。"""
        idx = GAME_APP.find("function BranchTreeRail(")
        end = GAME_APP.find("\nfunction ", idx + 1)
        body = GAME_APP[idx:end if end > 0 else len(GAME_APP)]
        self.assertIn("rpg-state-reload", body,
            "BranchTreeRail callback 必须 dispatch rpg-state-reload 让 Game Console 重拉 /api/state")
        self.assertIn("rpg-saves-updated", body,
            "BranchTreeRail callback 也应 dispatch rpg-saves-updated 刷 saves 列表")


if __name__ == "__main__":
    unittest.main(verbosity=2)
