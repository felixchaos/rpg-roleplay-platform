"""
test_save_activate_switches_runtime.py — task 30 回归

复现：
  - 用户有两个 save：A（玩过，runtime 当前激活）和 B（新建，干净）
  - POST /api/saves/{B}/activate 返回 {ok:true, active_save_id:B}
  - 但 GET /api/state 仍然返回 A 的 player/world（旧 active save 的 state）
  - 原因：原 frontend_routes.api_save_activate 只 select 1 ownership 就返
    ok=True，既不写 user_runtime，也不清 ui._state_by_user 内存缓存。

修复：
  - branches.activate_save(user_id, save_id)：找该 save 的 active commit（无则取 root）
    → runtime.activate_state_snapshot 写 user_runtime
  - frontend_routes.api_save_activate 调上面 + ui._invalidate_user_cache
  - 前端继续向导的 confirm() 也补一发 /api/saves/{id}/activate（在前端 smoke 锚里覆盖）
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class SaveActivateSwitchesRuntime(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _uid(self, username: str) -> int:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute("select id from users where username = %s", (username,)).fetchone()
        return int(row["id"])

    def _mk_script(self, uid: int, title: str) -> int:
        from platform_app.db import connect
        with connect() as db:
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, title),
            ).fetchone()
        return int(scr["id"])

    def test_activate_save_switches_runtime_state(self):
        """核心：activate B 后 GET /api/state 必须看到 B 的 player/world，不是 A 的"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        script_id = self._mk_script(uid, "integtest_activate")

        # 创建 save A（带 new_card）
        a_payload = {
            "title": "save A · 旧档",
            "script_id": script_id,
            "new_card": {"name": "A玩家", "role": "A身份", "background": "save A 的旧背景"},
        }
        ra = self.client.post("/api/v1/saves", json=a_payload, cookies=cookies)
        self.assertEqual(ra.status_code, 200, ra.text[:200])
        a_id = int(((ra.json() or {}).get("save") or {}).get("id") or 0)
        self.assertGreater(a_id, 0)

        # activate A 让它成为"当前 active"
        r_act_a = self.client.post(f"/api/v1/saves/{a_id}/activate", json={}, cookies=cookies)
        self.assertEqual(r_act_a.status_code, 200, r_act_a.text[:200])
        ba = r_act_a.json() or {}
        self.assertTrue(ba.get("ok"), f"activate A 应 ok=True：{ba}")
        self.assertEqual(int(ba.get("active_save_id") or 0), a_id)

        # GET /api/state 应反映 A 的 player
        r_s_a = self.client.get("/api/v1/state", cookies=cookies)
        self.assertEqual(r_s_a.status_code, 200)
        s_a = r_s_a.json() or {}
        self.assertEqual(int(s_a.get("save_id") or 0), a_id,
            f"activate A 后 /api/state.save_id 应=A；实际 {s_a.get('save_id')!r}")
        self.assertEqual((s_a.get("player") or {}).get("name"), "A玩家",
            f"/api/v1/state.player.name 应=A玩家；实际 {(s_a.get('player') or {}).get('name')!r}")

        # 创建 save B（带不同 new_card）
        b_payload = {
            "title": "save B · 新档",
            "script_id": script_id,
            "new_card": {"name": "B玩家", "role": "B身份", "background": "save B 的新背景"},
        }
        rb = self.client.post("/api/v1/saves", json=b_payload, cookies=cookies)
        self.assertEqual(rb.status_code, 200, rb.text[:200])
        b_id = int(((rb.json() or {}).get("save") or {}).get("id") or 0)
        self.assertGreater(b_id, 0)
        self.assertNotEqual(a_id, b_id)

        # activate B
        r_act_b = self.client.post(f"/api/v1/saves/{b_id}/activate", json={}, cookies=cookies)
        self.assertEqual(r_act_b.status_code, 200, r_act_b.text[:200])
        bb = r_act_b.json() or {}
        self.assertTrue(bb.get("ok"))
        self.assertEqual(int(bb.get("active_save_id") or 0), b_id,
            f"activate B 后 active_save_id 应=B={b_id}；实际 {bb.get('active_save_id')!r}")

        # 关键：GET /api/state 必须切到 B，不能停在 A
        r_s_b = self.client.get("/api/v1/state", cookies=cookies)
        self.assertEqual(r_s_b.status_code, 200)
        s_b = r_s_b.json() or {}
        self.assertEqual(int(s_b.get("save_id") or 0), b_id,
            f"task 30：activate B 后 /api/state.save_id 应=B={b_id}；"
            f"实际 {s_b.get('save_id')!r}（说明 ui 缓存没清，或 user_runtime 没写）")
        player_b = (s_b.get("player") or {})
        self.assertEqual(player_b.get("name"), "B玩家",
            f"task 30：/api/state.player.name 应=B玩家；实际 {player_b.get('name')!r}"
            f"（说明拿到的还是 save A 的旧 state）")
        self.assertEqual(player_b.get("role"), "B身份",
            f"task 30：/api/state.player.role 应=B身份；实际 {player_b.get('role')!r}")

        # 切回 A 也应该立刻反映
        self.client.post(f"/api/v1/saves/{a_id}/activate", json={}, cookies=cookies)
        s_back = (self.client.get("/api/v1/state", cookies=cookies).json() or {})
        self.assertEqual(int(s_back.get("save_id") or 0), a_id)
        self.assertEqual((s_back.get("player") or {}).get("name"), "A玩家",
            "activate 应该是可逆双向的，再次 activate A 应该看到 A 的 player")

    def test_activate_save_writes_user_runtime(self):
        """activate 后 user_runtime 表里 save_id 必须就是目标 save"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        script_id = self._mk_script(uid, "integtest_act_runtime")

        r = self.client.post("/api/v1/saves", json={
            "title": "act-runtime save",
            "script_id": script_id,
            "new_card": {"name": "runtime-player", "role": "r", "background": "rt"},
        }, cookies=cookies)
        sid = int(((r.json() or {}).get("save") or {}).get("id") or 0)

        r_act = self.client.post(f"/api/v1/saves/{sid}/activate", json={}, cookies=cookies)
        self.assertEqual(r_act.status_code, 200, r_act.text[:200])

        from platform_app.runtime import read_runtime
        meta = read_runtime(user_id=uid) or {}
        self.assertEqual(int(meta.get("save_id") or 0), sid,
            f"activate 后 user_runtime.save_id 应=目标 save={sid}；实际 {meta.get('save_id')!r}")
        self.assertIsNotNone(meta.get("active_commit_id"),
            "activate 应写 active_commit_id（用于 ui 加载 commit_state）")

    def test_activate_unowned_save_returns_403(self):
        """不属于自己的 save 不能 activate"""
        u1 = register_user(self.client)
        u2 = register_user(self.client)
        uid1 = self._uid(u1["username"])
        sid_a = self._mk_script(uid1, "u1 script")
        r = self.client.post("/api/v1/saves", json={
            "title": "u1 save",
            "script_id": sid_a,
            "new_card": {"name": "u1p", "role": "u1r", "background": "x"},
        }, cookies=u1["cookies"])
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)
        # u2 尝试 activate u1 的 save
        r2 = self.client.post(f"/api/v1/saves/{save_id}/activate", json={}, cookies=u2["cookies"])
        self.assertEqual(r2.status_code, 403, f"应 403；实际 {r2.status_code}: {r2.text[:200]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
