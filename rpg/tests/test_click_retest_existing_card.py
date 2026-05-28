"""
test_click_retest_existing_card.py — Codex 点击复测 Bug 1：

Platform #cards 新建角色「林晚舟点击复测」→ 开始游戏 → 新建存档 →
默认选中该卡 → 创建并进入 → ContinuePicker 继续游戏 → /api/state
要返回 player.name = 林晚舟点击复测。
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


class ClickPathExistingCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def _uid(self, username):
        from platform_app.db import connect
        with connect() as db:
            row = db.execute("select id from users where username = %s",
                             (username,)).fetchone()
        return int(row["id"])

    def _create_script_with_chapters(self, uid: int, title: str) -> int:
        """模拟用户已导入过的真实剧本（有章节，会触发 _apply_script_opening）。"""
        from platform_app.db import connect
        with connect() as db:
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, title),
            ).fetchone()
            sid = int(scr["id"])
            # 加一个有 inline meta 的章节，模拟真实导入
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content) "
                "values (%s, %s, %s, %s)",
                (sid, 1, "第一章 测试开场",
                 "当前地点：废弃矿道入口。当前目标：测试 inline meta。"),
            )
        return sid

    def test_full_click_path_existing_user_card(self):
        """完整 6 步点击路径：注册 → 建 card → 建 save → activate → /api/state。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])

        # 1. 注册（已 register_user）✓

        # 2. Platform #cards → 新建角色卡
        r = self.client.post("/api/me/character-cards", json={
            "name": "林晚舟点击复测",
            "identity": "QA 探险者",
            "appearance": "黑发披风，背着旧皮包。",
            "personality": "冷静细致。",
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        card = (r.json() or {}).get("card") or {}
        card_id = int(card.get("id") or 0)
        self.assertGreater(card_id, 0)

        # 3. 准备一个真实剧本（有章节，模拟实际场景）
        script_id = self._create_script_with_chapters(uid, "qa_click_retest_script")

        # 4. POST /api/saves（FE NewGameModal 默认选中刚才那张 user_card）
        r = self.client.post("/api/saves", json={
            "title": "QA click existing card retest",
            "script_id": script_id,
            "character_id": card_id,
            "character_kind": "user_card",
            "npc_id": None,
            "new_card": None,
            "role_mode": "existing",
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:400])
        save = (r.json() or {}).get("save") or {}
        save_id = int(save.get("id") or 0)
        self.assertGreater(save_id, 0)

        # 4a. 验证 game_saves.state_snapshot.player 已 setup（saves list 派生）
        snap = save.get("state_snapshot") or {}
        if isinstance(snap, str):
            import json as _j
            snap = _j.loads(snap)
        player_in_snapshot = (snap.get("player") or {})
        self.assertEqual(
            player_in_snapshot.get("name"), "林晚舟点击复测",
            f"create_save 后 state_snapshot.player.name 应=林晚舟点击复测；"
            f"实际={player_in_snapshot}"
        )

        # 5. ContinuePicker.confirm: POST /api/saves/{id}/activate
        r = self.client.post(f"/api/saves/{save_id}/activate", cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()
        self.assertTrue(body.get("ok"), body)
        self.assertEqual(int(body.get("active_save_id") or 0), save_id)

        # 6. GET /api/state（Game Console.html mount → window.api.game.state()）
        r = self.client.get("/api/state", cookies=cookies)
        state = r.json()
        self.assertEqual(int(state.get("save_id") or 0), save_id,
            f"/api/state.save_id 应=新建 save；实际={state.get('save_id')}")

        # 关键断言：player.name 必须从 user_card 注入
        player = state.get("player") or {}
        self.assertEqual(
            player.get("name"), "林晚舟点击复测",
            f"Bug 1 (click retest)：activate + /api/state 后 player.name 应="
            f"林晚舟点击复测；实际={player}\n"
            f"saves list 看到 player_name 正常 = game_saves.state_snapshot 有 player；"
            f"如本断言失败说明 runtime_checkouts 没拿到正确 snapshot。"
        )
        self.assertIn("QA 探险者", str(player.get("role") or ""),
            f"role 应来自 user_card.identity；实际={player}")
        self.assertTrue(
            player.get("background"),
            f"background 应来自 user_card.appearance/personality；实际={player}"
        )

    def test_full_click_path_with_prior_active_module_save(self):
        """更接近 Codex 真实场景：先有一个 ash_mine 模组 save 活跃，
        再走"新建普通存档 + 用户卡"路径。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])

        # 先 launch 模组（之前的测试可能留下活跃 save）
        r = self.client.post("/api/rules/module/launch",
                             json={"module_id": "ash_mine"}, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])

        # 建 user_card
        r = self.client.post("/api/me/character-cards", json={
            "name": "林晚舟点击复测2",
            "identity": "测试探险者",
            "appearance": "—",
            "personality": "—",
        }, cookies=cookies)
        card_id = int(((r.json() or {}).get("card") or {}).get("id") or 0)

        # 建普通 save
        script_id = self._create_script_with_chapters(uid, "qa_click_after_module")
        r = self.client.post("/api/saves", json={
            "title": "after module - existing card",
            "script_id": script_id,
            "character_id": card_id,
            "character_kind": "user_card",
            "role_mode": "existing",
        }, cookies=cookies)
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)

        # activate
        self.client.post(f"/api/saves/{save_id}/activate", cookies=cookies)

        # /api/state
        state = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual(int(state.get("save_id") or 0), save_id)
        self.assertEqual(
            (state.get("player") or {}).get("name"), "林晚舟点击复测2",
            f"切换 save 后 player 应来自 user_card；实际={state.get('player')}"
        )

    def test_selfheal_when_runtime_checkout_is_corrupt(self):
        """模拟 runtime_checkouts.state_snapshot 缺 player 的故障态：
        _ensure_loaded 必须从 game_saves.state_snapshot 自愈，让 /api/state
        仍能返回正确 player。这是 Bug 1 click-retest 的兜底防线。"""
        from platform_app.db import connect
        from psycopg.types.json import Jsonb

        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])

        # 1. 走完正常 click path 拿到 save_id
        r = self.client.post("/api/me/character-cards", json={
            "name": "selfheal 测试",
            "identity": "selfheal tester",
            "appearance": "—",
            "personality": "—",
        }, cookies=cookies)
        card_id = int(((r.json() or {}).get("card") or {}).get("id") or 0)
        script_id = self._create_script_with_chapters(uid, "selfheal_script")
        r = self.client.post("/api/saves", json={
            "title": "selfheal",
            "script_id": script_id,
            "character_id": card_id,
            "character_kind": "user_card",
        }, cookies=cookies)
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)
        self.client.post(f"/api/saves/{save_id}/activate", cookies=cookies)

        # 2. 故意把 runtime_checkouts.state_snapshot 弄空（模拟 activate 时序故障）
        broken_snapshot = {"history": [], "turn": 0, "player": {"name": "", "role": "", "background": ""}}
        with connect() as db:
            db.execute(
                "update runtime_checkouts set state_snapshot = %s where save_id = %s and user_id = %s",
                (Jsonb(broken_snapshot), save_id, uid),
            )

        # 3. 清后端缓存让 _ensure_loaded 重新从 DB 加载
        import app as ui_mod
        with ui_mod._state_lock:
            ui_mod._state_by_user.pop(uid, None)

        # 4. GET /api/state — 应该 self-heal 出来
        state = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual(
            (state.get("player") or {}).get("name"), "selfheal 测试",
            f"self-heal 应从 game_saves.state_snapshot 恢复 player；"
            f"实际={state.get('player')}"
        )

    def test_character_id_as_string_still_resolved(self):
        """防御：FE 可能传 character_id 为 string（picked.slug 是 string）。
        backend 必须能处理 int 或 str。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])

        r = self.client.post("/api/me/character-cards", json={
            "name": "string id test",
            "identity": "tester",
            "appearance": "",
            "personality": "tester body",
        }, cookies=cookies)
        card = (r.json() or {}).get("card") or {}
        card_id = card.get("id")

        script_id = self._create_script_with_chapters(uid, "qa_string_id_script")
        # 故意把 character_id 当字符串传（FE 可能这么发）
        r = self.client.post("/api/saves", json={
            "title": "string id save",
            "script_id": script_id,
            "character_id": str(card_id),
            "character_kind": "user_card",
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)

        self.client.post(f"/api/saves/{save_id}/activate", cookies=cookies)
        state = self.client.get("/api/state", cookies=cookies).json()
        self.assertEqual(
            (state.get("player") or {}).get("name"), "string id test",
            f"character_id=string 应仍能解析；实际 player={state.get('player')}"
        )


class AcceptancePolarityFix(unittest.TestCase):
    """click retest minor：否定/否定式成功条款不应在 rule 模式被误报 unmet。"""

    def test_negative_form_gm_did_not_decide_check_passes(self):
        from app import _verify_acceptance_rule
        # GM 实际叙事提到了 "Investigation 检定" 因为 rules engine 跑了，
        # 但 "未自行决定" 的意思是 GM 没自己拍板 —— 实际通过。
        unmet = _verify_acceptance_rule(
            acceptance=["GM 未自行决定检定成败"],
            response_text="你蹲下查看脚印，Investigation 检定结果由系统裁定。",
            updates=[],
        )
        self.assertEqual(unmet, [],
            f"否定式成功条款不应被规则版报 unmet；实际={unmet}")

    def test_negative_form_no_hp_ac_modification_passes(self):
        from app import _verify_acceptance_rule
        unmet = _verify_acceptance_rule(
            acceptance=["未出现直接修改 HP/AC 的操作"],
            response_text="你站起身，向矿车深处望了望。",
            updates=[],
        )
        self.assertEqual(unmet, [])

    def test_positive_unmet_still_reported(self):
        """对照：肯定式条款命中不到关键词的仍要 unmet —— 极性方向没翻。"""
        from app import _verify_acceptance_rule
        unmet = _verify_acceptance_rule(
            acceptance=["GM 必须回应玩家询问灯塔守护者的来历"],
            response_text="你环顾四周。",  # 没提"灯塔"也没"守护者"
            updates=[],
        )
        self.assertEqual(unmet, ["GM 必须回应玩家询问灯塔守护者的来历"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
