"""
test_unified_create_save_flow.py
================================

Codex P0 审计:存档系统三处入口都绕过建档。修复要求所有用户可见的
"开始新游戏 / 新建存档"必须走统一原子流:
  create save → activate save(runtime 切到新档)→ 打开 Game Console

修复对应:
- P0-1: ContinuePicker 内嵌 NewGameModal 的 onConfirm 不再丢 payload,
  改 await window.__createAndEnterSave(payload)
- P0-2: ScriptsListView "基于此剧本"按钮没存档时不再传 {id:null} 假 save 给
  ContinuePicker (会直接跳页跳过建档),改为弹 NewGameModal + 走原子流
- P0-3: Game Console "新建游戏"按钮不再调 /api/new (那只重置 runtime,
  不建 game_save),改为回平台存档页走正规建档流

2026-09 更新(测试陈旧,不是回归):
  这些源码级断言原本读 platform-app.jsx 与「Game Console.html」内联脚本。之后的模块化把
  ContinuePicker 搬到 components/saves/Branches.jsx、ScriptsListView 搬到
  components/scripts/ScriptsList.jsx、NewGameModal 搬到 components/saves/NewGameModal.jsx、
  游戏台逻辑搬到 entries/game-console.jsx(HTML 只剩一个 module script),
  __createAndEnterSave 改为建档后复用 __openContinue(activate + 开新标签页)。契约本身都还在,
  只是换了位置 —— 6 条失败全是读错了文件。为了让旧断言「通过」,platform-app.jsx 里还留过一个
  永远不渲染的 ScriptsListView 假实现,已删除。
  「前端 GET /api/state 校验 save_id」这一步已不在前端做:activate 成功即服务端 runtime 已切换,
  这条契约由 Layer E 在后端真打一遍锁住(以前 Layer E 因为建档缺 script_id 恒 skip,
  现在先建空白剧本再建档,真的跑完整链)。

本测试层:
  Layer A — window.__createAndEnterSave 原子流定义 + 关键步骤完整
  Layer B — ContinuePicker 内 NewGameModal 接 payload (P0-1)
  Layer C — ScriptsListView 没存档时弹 NewGameModal (P0-2)
  Layer D — Game Console onNew 不再调 /api/new (P0-3)
  Layer E — 后端 /api/saves + /api/saves/{id}/activate + /api/state 端到端
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.helpers import make_client, register_user

PROJECT = Path(__file__).resolve().parents[3]
SRC = PROJECT / "frontend" / "src"
PLATFORM_JSX = (SRC / "platform-app.jsx").read_text(encoding="utf-8")
BRANCHES_JSX = (SRC / "components" / "saves" / "Branches.jsx").read_text(encoding="utf-8")
SCRIPTS_LIST_JSX = (SRC / "components" / "scripts" / "ScriptsList.jsx").read_text(encoding="utf-8")
NEW_GAME_MODAL_JSX = (SRC / "components" / "saves" / "NewGameModal.jsx").read_text(encoding="utf-8")
GAME_CONSOLE_JSX = (SRC / "entries" / "game-console.jsx").read_text(encoding="utf-8")


def _function_body(src: str, signature: str) -> str:
    idx = src.find(signature)
    assert idx >= 0, f"找不到 {signature}"
    end = src.find("\nfunction ", idx + 1)
    return src[idx:end if end > 0 else len(src)]


def _assign_body(src: str, marker: str, window: int = 4000) -> str:
    idx = src.find(marker)
    assert idx >= 0, f"找不到 {marker}"
    return src[idx:idx + window]


# ────────────────────────────────────────────────────────────
# Layer A: 统一原子流
# ────────────────────────────────────────────────────────────


class CreateAndEnterSaveAtomic(unittest.TestCase):
    """window.__createAndEnterSave:建档 → 失败即中止 → 复用 __openContinue(activate + 开游戏台)。"""

    def test_function_registered_on_window(self):
        self.assertIn("window.__createAndEnterSave = async", PLATFORM_JSX,
            "platform-app.jsx 必须挂 window.__createAndEnterSave 全局函数")

    def test_atomic_steps_in_order(self):
        body = _assign_body(PLATFORM_JSX, "window.__createAndEnterSave = async", 1500)
        i_create = body.find("window.api.saves.create(")
        i_enter = body.find("window.__openContinue")
        self.assertGreaterEqual(i_create, 0, "Step 1: 必须调 saves.create 建 game_save")
        self.assertGreater(i_enter, i_create, "Step 2: 建档成功后才进入(__openContinue)")
        # __openContinue 负责 activate(把 runtime 切到新档)+ 打开 Game Console
        cont = _assign_body(PLATFORM_JSX, "window.__openContinue = async", 5000)
        self.assertIn("window.api.saves.activate(", cont,
            "进入前必须 saves.activate(newId) 把 runtime 切到新 save")
        self.assertIn('"Game Console.html"', cont, "activate 成功后打开 Game Console")
        i_act = cont.find("window.api.saves.activate(")
        i_open = cont.find('"Game Console.html"')
        self.assertLess(i_act, i_open, "必须先 activate 再跳页")

    def test_atomic_aborts_on_create_failure(self):
        body = _assign_body(PLATFORM_JSX, "window.__createAndEnterSave = async", 1500)
        i_throw = body.find("throw new Error")
        self.assertGreaterEqual(i_throw, 0, "后端拒绝建档必须抛错")
        self.assertLess(i_throw, body.find("window.__openContinue"),
            "建档失败必须在进入(activate)之前就中止")

    def test_activate_failure_does_not_open_game(self):
        cont = _assign_body(PLATFORM_JSX, "window.__openContinue = async", 5000)
        seg = cont[cont.find("window.api.saves.activate("):cont.find('"Game Console.html"')]
        self.assertIn("catch (e)", seg)
        self.assertIn("return;", seg, "activate 失败必须 return,不能照样跳进游戏台读旧档")


# ────────────────────────────────────────────────────────────
# Layer B: P0-1 ContinuePicker 内嵌 NewGameModal
# ────────────────────────────────────────────────────────────


class ContinuePickerEmbeddedModalAcceptsPayload(unittest.TestCase):
    """ContinuePicker 内嵌的 NewGameModal onConfirm 必须接 payload + 调原子流。"""

    def test_onConfirm_receives_payload_and_calls_atomic(self):
        body = _function_body(BRANCHES_JSX, "function ContinuePicker(")
        self.assertIn("<NewGameModal", body)
        # 旧错误模式:onConfirm={() => { setNewOpen(false); confirm(); }}
        self.assertNotIn("onConfirm={() => { setNewOpen(false); confirm();", body,
            "ContinuePicker 不应再用 onConfirm={() => confirm()} 丢 payload")
        self.assertRegex(body, r"__createAndEnterSave\(payload\)",
            "ContinuePicker 内嵌 NewGameModal 的 onConfirm 必须调原子流 __createAndEnterSave(payload)")


# ────────────────────────────────────────────────────────────
# Layer C: P0-2 ScriptsListView 没存档时弹 NewGameModal
# ────────────────────────────────────────────────────────────


class ScriptsListViewOpensModalForNoSave(unittest.TestCase):
    """ScriptsListView "基于此剧本"按钮:没存档时弹 NewGameModal 走原子流。"""

    def test_no_more_fake_save_id_null(self):
        for name, src in (("platform-app.jsx", PLATFORM_JSX), ("ScriptsList.jsx", SCRIPTS_LIST_JSX)):
            self.assertNotIn("{ id: null, script_id:", src,
                f"{name} 不应再传 fake {{id:null, script_id}} 给 ContinuePicker —— 那会绕过建档")

    def test_scripts_list_view_has_new_modal_with_default_script_id(self):
        body = _function_body(SCRIPTS_LIST_JSX, "function ScriptsListView(")
        self.assertIn("setNewModalScriptId", body, "ScriptsListView 应有 newModalScriptId state")
        self.assertIn("defaultScriptId", body, "ScriptsListView 渲染 <NewGameModal defaultScriptId=... />")
        self.assertIn("__createAndEnterSave", body, "ScriptsListView 的 NewGameModal onConfirm 必须调原子流")

    def test_new_game_modal_accepts_defaultScriptId(self):
        self.assertIsNotNone(
            re.search(r"function NewGameModal\(\s*\{[^}]*defaultScriptId", NEW_GAME_MODAL_JSX),
            "NewGameModal 应接 defaultScriptId prop",
        )

    def test_platform_shell_has_no_fake_scripts_list_view(self):
        """platform-app.jsx 不许再留一个永远不渲染、只为让源码断言通过的 ScriptsListView 假实现。"""
        self.assertNotIn("function ScriptsListView(", PLATFORM_JSX)


# ────────────────────────────────────────────────────────────
# Layer D: P0-3 Game Console "新建游戏"不再调 /api/new
# ────────────────────────────────────────────────────────────


class GameConsoleNewGameButtonRedirects(unittest.TestCase):
    """游戏台的 onNew 不应再调 window.api.game.newGame()。
    /api/new 只重置 runtime 不建 game_save,UI 入口必须回平台存档页走正规建档流。"""

    def _blocks(self):
        out = []
        start = 0
        while True:
            idx = GAME_CONSOLE_JSX.find("onNew={", start)
            if idx < 0:
                break
            out.append(GAME_CONSOLE_JSX[idx:idx + 400])
            start = idx + 1
        self.assertTrue(out, "entries/game-console.jsx 里找不到 onNew")
        return out

    def test_on_new_does_not_call_game_new_game(self):
        for block in self._blocks():
            self.assertNotIn("window.api.game.newGame(", block,
                "onNew 不应再调 window.api.game.newGame() —— /api/new 只重置 runtime")

    def test_on_new_redirects_to_platform(self):
        for block in self._blocks():
            self.assertIn("'/saves'", block, "onNew 应回平台存档页走正规建档流")


# ────────────────────────────────────────────────────────────
# Layer E: 后端原子流端到端
# ────────────────────────────────────────────────────────────


class CreateThenActivateThenState(unittest.TestCase):
    """saves.create → saves.activate → /api/state.save_id 必须串通。"""

    def setUp(self):
        self.client = make_client()
        self.user = register_user(self.client)
        self.cookies = self.user["cookies"]

    def tearDown(self):
        from platform_app.db import connect
        with connect() as db:
            db.execute("delete from users where username = %s", (self.user["username"],))

    def test_full_atomic_chain(self):
        c, ck = self.client, self.cookies
        # 建档前置:新档的 GM 若解析到 vertex_ai,没传 Service Account 会被拒(与本契约无关)。
        # 显式指定一个非 vertex 的偏好,让测试只验证建档 → 激活 → 读状态这条链。
        self.assertEqual(c.post("/api/v1/me/preference", json={"gm.api_id": "deepseek"}, cookies=ck).status_code, 200)
        r0 = c.post("/api/v1/scripts/blank", json={"title": "原子流测试剧本"}, cookies=ck)
        self.assertEqual(r0.status_code, 200, r0.text[:300])
        script_id = r0.json().get("script_id")
        self.assertTrue(script_id)
        r = c.post("/api/v1/saves", json={"title": "原子流测试存档", "script_id": script_id}, cookies=ck)
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()
        self.assertTrue(body.get("ok") is not False, body)
        save = body.get("save") or body
        save_id = save.get("id")
        self.assertIsNotNone(save_id, "建档成功必须返回 save.id")
        r2 = c.post(f"/api/v1/saves/{save_id}/activate", json={}, cookies=ck)
        self.assertEqual(r2.status_code, 200, r2.text[:300])
        r3 = c.get("/api/v1/state", cookies=ck)
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(int(r3.json().get("save_id") or 0), int(save_id),
            "原子流走完后 /api/state.save_id 必须等于新建 save.id")

    def test_old_api_new_endpoint_still_exists_but_for_dev_only(self):
        """/api/v1/new 暂留兼容(开发可能用),但 UI 入口已下线 — 仅锁端点存在。
        未来若改名 /api/runtime/reset,把这测试删掉。"""
        r = self.client.post("/api/v1/new", json={}, cookies=self.cookies)
        self.assertIn(r.status_code, (200, 400),
            "/api/v1/new 端点应仍存在 (200) 或返回 400 (空请求);不应 404")


if __name__ == "__main__":
    unittest.main(verbosity=2)
