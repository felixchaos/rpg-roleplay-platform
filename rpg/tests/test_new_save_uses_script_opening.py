"""
test_new_save_uses_script_opening.py — task 34 回归

复现：从 Platform#scripts-import 导入测试剧本 timeline_set_test_novel.md（首章雾港码头/
申时三刻/蓝色罗盘/灯塔星门），然后在 Platform#saves 创建 save。期望 state_snapshot 反映
导入剧本第一章，但实际仍是 DEFAULT_STATE 的柏林剧情：
  - world.time="图卢兹失守后翌日，柏林"
  - player.current_location="柏林，哈布斯堡庄园附近"
  - world.known_events=["宴会上调令伪造事件已曝光", "图卢兹战役...", "蛇信..."]
  - memory.current_objective="观察柏林局势，保护蕾穆丽娜"

修复：workspace._apply_script_opening(state, user_id, script_id) 在 _build_initial_snapshot
里调用，读 script_chapters 首章解析:
  当前地点：X  → state.update_location(X)
  当前目标：Y  → memory.current_objective = Y
  时间锚点：Z  → state.update_time(Z)
并把 known_events 替换为「开场：<标题>」+ 首两行非元数据正文，
last_retrieval 写入首章前 ~400 字预览。
"""
from __future__ import annotations

import json
import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


# 测试剧本首章内容（取自真实 output/playwright/timeline_set_test_novel.md）
TEST_CHAPTER_TITLE = "第一章 雾港入夜"
TEST_CHAPTER_CONTENT = """申时三刻，雾港码头的铜钟敲了六下。玩家角色『测试旅人』刚从破损的渡船上醒来，手里只有一枚蓝色罗盘。守灯人沈知微告诉他：今晚子时，灯塔会出现只持续一刻钟的星门。

当前地点：雾港码头。
当前目标：确认蓝色罗盘是否能打开灯塔星门。
时间锚点：申时三刻。
"""


class NewSaveUsesScriptOpening(unittest.TestCase):
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

    def _mk_script_with_chapter(self, uid: int, title: str) -> int:
        """模拟剧本导入：建 scripts 行 + 写 script_chapters 第 1 章"""
        from platform_app.db import connect
        with connect() as db:
            scr = db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, title),
            ).fetchone()
            sid = int(scr["id"])
            db.execute(
                """
                insert into script_chapters(script_id, chapter_index, title, content, word_count)
                values (%s, %s, %s, %s, %s)
                """,
                (sid, 1, TEST_CHAPTER_TITLE, TEST_CHAPTER_CONTENT, len(TEST_CHAPTER_CONTENT)),
            )
        return sid

    def test_new_save_state_reflects_script_first_chapter_not_berlin_default(self):
        """核心回归：创建 save 后 state_snapshot 必须含雾港/申时三刻/灯塔/罗盘，
        且 NOT 含柏林/图卢兹/蛇信/哈布斯堡。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        script_id = self._mk_script_with_chapter(uid, "E2E_UI_完整游戏_test_34")

        payload = {
            "title": "task34 save",
            "script_id": script_id,
            "new_card": {
                "name": "测试旅人",
                "role": "时间线测试者",
                "background": "用于从导入剧本开始验证。",
            },
        }
        r = self.client.post("/api/saves", json=payload, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)
        self.assertGreater(save_id, 0)

        # 直接看 save_detail 的 state_snapshot
        r2 = self.client.get(f"/api/saves/{save_id}", cookies=cookies)
        self.assertEqual(r2.status_code, 200, r2.text[:300])
        snap = ((r2.json() or {}).get("save") or {}).get("state_snapshot") or {}
        if isinstance(snap, str):
            snap = json.loads(snap)

        world = snap.get("world") or {}
        player = snap.get("player") or {}
        memory = snap.get("memory") or {}
        events = world.get("known_events") or []
        events_blob = " | ".join(str(e) for e in events)

        # ── 必须含导入剧本内容 ─────────────
        self.assertEqual(player.get("current_location"), "雾港码头",
            f"task 34：current_location 应从首章『当前地点』派生为『雾港码头』；"
            f"实际 {player.get('current_location')!r}")
        self.assertIn("申时三刻", str(world.get("time", "")),
            f"task 34：world.time 应从首章『时间锚点』派生为『申时三刻』；"
            f"实际 {world.get('time')!r}")
        self.assertIn("蓝色罗盘", str(memory.get("current_objective", "")),
            f"task 34：memory.current_objective 应从首章『当前目标』派生（含蓝色罗盘）；"
            f"实际 {memory.get('current_objective')!r}")
        self.assertIn("雾港入夜", events_blob,
            f"task 34：known_events 应含开场章节标题；实际 {events!r}")

        # ── NOT 含柏林默认 ─────────────
        DEFAULT_BERLIN_MARKERS = [
            "柏林",  # current_location/known_events/time 都含柏林
            "图卢兹",  # known_events 默认
            "蛇信",   # known_events 默认
            "哈布斯堡",  # current_location 默认
            "宴会上调令伪造",  # known_events 默认
        ]
        snap_blob = json.dumps(snap, ensure_ascii=False)
        for marker in DEFAULT_BERLIN_MARKERS:
            self.assertNotIn(marker, snap_blob,
                f"task 34：从导入剧本创建的 save state_snapshot 不应含柏林默认『{marker}』；"
                f"snap 含相关片段，known_events={events!r} player={player!r} time={world.get('time')!r}")

        # ── 角色卡仍写入（task 29 不能被破坏） ─────────────
        self.assertEqual(player.get("name"), "测试旅人",
            f"task 29 兼容：player.name 仍应 = 测试旅人；实际 {player.get('name')!r}")
        self.assertEqual(player.get("role"), "时间线测试者",
            f"task 29 兼容：player.role 仍应 = 时间线测试者；实际 {player.get('role')!r}")

        # ── last_retrieval 应该被填充（含首章正文片段） ─────────────
        last_retrieval = str(memory.get("last_retrieval") or "")
        self.assertIn("雾港", last_retrieval,
            f"task 34：memory.last_retrieval 应含首章正文片段；实际前 200 字={last_retrieval[:200]!r}")

    def test_branches_root_snapshot_also_uses_script_opening(self):
        """task 25 + task 34：seed_tree 写的 root commit 同样必须用 script-opening state"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        script_id = self._mk_script_with_chapter(uid, "E2E_branches_seed_test_34")

        r = self.client.post("/api/saves", json={
            "title": "root snap test",
            "script_id": script_id,
            "new_card": {"name": "P", "role": "R", "background": "B"},
        }, cookies=cookies)
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)
        r2 = self.client.get(f"/api/branches/{save_id}", cookies=cookies)
        self.assertEqual(r2.status_code, 200)
        nodes = (r2.json() or {}).get("nodes") or (r2.json() or {}).get("commits") or []
        self.assertGreaterEqual(len(nodes), 1)
        root = nodes[0]
        rsnap = root.get("state_snapshot") or {}
        if isinstance(rsnap, str):
            rsnap = json.loads(rsnap)
        rplayer = rsnap.get("player") or {}
        rworld = rsnap.get("world") or {}
        self.assertEqual(rplayer.get("current_location"), "雾港码头",
            f"task 34：branches root state_snapshot.player.current_location 应=雾港码头；"
            f"实际 {rplayer.get('current_location')!r}")
        self.assertIn("申时三刻", str(rworld.get("time", "")),
            f"task 34：branches root.world.time 应含申时三刻；实际 {rworld.get('time')!r}")
        # 同样不应含柏林
        rsnap_blob = json.dumps(rsnap, ensure_ascii=False)
        self.assertNotIn("哈布斯堡", rsnap_blob,
            f"task 34：branches root 也不应含柏林默认；rsnap={rsnap_blob[:400]}")

    def test_script_without_chapters_falls_back_to_default(self):
        """对照：没有 script_chapters 行（只建空 script）→ 退到默认 state，不应抛"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        from platform_app.db import connect
        with connect() as db:
            sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "no chapters script"),
            ).fetchone()["id"])

        r = self.client.post("/api/saves", json={
            "title": "no-chap save",
            "script_id": sid,
            "new_card": {"name": "X", "role": "Y", "background": "Z"},
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)
        self.assertGreater(save_id, 0, "无章节 script 仍应能创建 save（不应抛）")

    def test_script_chapter_without_inline_meta_uses_title_as_event(self):
        """对照：章节没有『当前地点/当前目标/时间锚点』inline 元数据 → location/time/objective
        保持安全空，但 known_events 仍应被替换为开场章节摘要，避免泄露柏林默认"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        from platform_app.db import connect
        with connect() as db:
            sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "plain chapter script"),
            ).fetchone()["id"])
            db.execute(
                """
                insert into script_chapters(script_id, chapter_index, title, content, word_count)
                values (%s, %s, %s, %s, %s)
                """,
                (sid, 1, "第一章 测试开场",
                 "这是测试开场的第一段。\n这是测试开场的第二段。\n这是测试开场的第三段。",
                 100),
            )
        r = self.client.post("/api/saves", json={
            "title": "plain chap save",
            "script_id": sid,
            "new_card": {"name": "X", "role": "Y", "background": "Z"},
        }, cookies=cookies)
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)
        snap = ((self.client.get(f"/api/saves/{save_id}", cookies=cookies).json() or {})
                .get("save") or {}).get("state_snapshot") or {}
        if isinstance(snap, str):
            snap = json.loads(snap)
        events_blob = " | ".join(str(e) for e in (snap.get("world") or {}).get("known_events") or [])
        self.assertIn("测试开场", events_blob,
            f"known_events 应含开场章节标题；实际 {events_blob!r}")
        # 仍不应含柏林
        for m in ("哈布斯堡", "图卢兹", "宴会上调令伪造"):
            self.assertNotIn(m, json.dumps(snap, ensure_ascii=False),
                f"无 inline meta 时也不应残留柏林默认『{m}』")

    def test_real_import_shape_skips_doc_title_then_uses_chapter_2(self):
        """task 40 关键回归：真实 markdown 导入后 chapter_index=1 是『# 文档总标题』
        word_count=0，chapter_index=2 才是『## 第一章 雾港入夜』含正文+inline meta。
        修复前 _apply_script_opening 只读 chapter 1 → content 空 → return early →
        柏林默认完全没被 scrub。修复后必须扫到 chapter 2 并应用。

        证据：output/playwright/e2e-full-game-ui-1779693636258.json 显示
          chapter 1: {title: "# 时间线与 Set 功能测试剧本", word_count: 0, content: ""}
          chapter 2: {title: "## 第一章 雾港入夜", word_count: 124,
                      content: "...灯塔。  当前地点：雾港码头。 当前目标：...灯塔星门。 时间锚点：申时三刻。"}
        """
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        from platform_app.db import connect
        with connect() as db:
            sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "real_import_shape_test"),
            ).fetchone()["id"])
            # chapter 1：纯文档标题，content 空
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content, word_count) "
                "values (%s, %s, %s, %s, %s)",
                (sid, 1, "# 时间线与 Set 功能测试剧本", "", 0),
            )
            # chapter 2：真实首章，inline meta 在一行内（chapter_splitter 折叠了换行）
            real_chapter2_content = (
                "申时三刻，雾港码头的铜钟敲了六下。"
                "玩家角色『测试旅人』刚从破损的渡船上醒来，手里只有一枚蓝色罗盘。"
                "守灯人沈知微告诉他：今晚子时，灯塔会出现只持续一刻钟的星门。"
                "  当前地点：雾港码头。 当前目标：确认蓝色罗盘是否能打开灯塔星门。 时间锚点：申时三刻。"
            )
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content, word_count) "
                "values (%s, %s, %s, %s, %s)",
                (sid, 2, "## 第一章 雾港入夜", real_chapter2_content, 124),
            )
            # chapter 3,4 也写入但不应被选中
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content, word_count) "
                "values (%s, %s, %s, %s, %s)",
                (sid, 3, "## 第二章 子时灯塔", "子时正... 当前地点：雾港灯塔。", 50),
            )

        r = self.client.post("/api/saves", json={
            "title": "real-import-shape save",
            "script_id": sid,
            "new_card": {
                "name": "测试旅人",
                "role": "时间线测试者",
                "background": "用于从真实导入剧本开始验证。",
            },
        }, cookies=cookies)
        self.assertEqual(r.status_code, 200, r.text[:300])
        save_id = int(((r.json() or {}).get("save") or {}).get("id") or 0)
        self.assertGreater(save_id, 0)

        snap = ((self.client.get(f"/api/saves/{save_id}", cookies=cookies).json() or {})
                .get("save") or {}).get("state_snapshot") or {}
        if isinstance(snap, str):
            snap = json.loads(snap)
        world = snap.get("world") or {}
        player = snap.get("player") or {}
        memory = snap.get("memory") or {}
        events = world.get("known_events") or []
        snap_blob = json.dumps(snap, ensure_ascii=False)

        # ── 必须用 chapter 2 的 opening，不是 chapter 3 ─────────
        self.assertEqual(player.get("current_location"), "雾港码头",
            f"task 40：应使用 chapter 2 的『当前地点：雾港码头』，"
            f"实际 {player.get('current_location')!r}\nsnap.world={world!r}")
        self.assertIn("申时三刻", str(world.get("time", "")),
            f"task 40：应使用 chapter 2 的『时间锚点：申时三刻』；实际 {world.get('time')!r}")
        self.assertIn("蓝色罗盘", str(memory.get("current_objective", "")),
            f"task 40：应使用 chapter 2 的『当前目标：...蓝色罗盘...』；"
            f"实际 {memory.get('current_objective')!r}")
        # 应使用 chapter 2 的清理后标题（去掉 ##）
        events_blob = " | ".join(str(e) for e in events)
        self.assertIn("第一章 雾港入夜", events_blob,
            f"task 40：known_events 应含 chapter 2 标题；实际 {events!r}")
        # 不应出现纯文档总标题（带 # 的）作为开场事件
        for ev in events:
            self.assertNotIn("时间线与 Set 功能测试剧本", str(ev),
                f"task 40：known_events 不该把『# 文档总标题』当开场事件；ev={ev!r}")

        # ── 关键：不应残留柏林默认 ─────────
        for marker in ("柏林", "图卢兹", "蛇信", "哈布斯堡", "宴会上调令伪造"):
            self.assertNotIn(marker, snap_blob,
                f"task 40：真实 import shape 下不应残留柏林默认『{marker}』；"
                f"world={world!r} player={player!r} memory.objective={memory.get('current_objective')!r}")

        # ── branches root 也必须同步 ─────────
        rsnap = ((self.client.get(f"/api/branches/{save_id}", cookies=cookies).json() or {})
                 .get("nodes") or [{}])[0].get("state_snapshot") or {}
        if isinstance(rsnap, str):
            rsnap = json.loads(rsnap)
        rplayer = rsnap.get("player") or {}
        self.assertEqual(rplayer.get("current_location"), "雾港码头",
            f"task 40：branches root snapshot 也必须用 chapter 2 opening；"
            f"实际 {rplayer.get('current_location')!r}")
        for marker in ("柏林", "哈布斯堡"):
            self.assertNotIn(marker, json.dumps(rsnap, ensure_ascii=False),
                f"task 40：branches root 也不应残留『{marker}』")

    def test_first_chapter_with_only_doc_title_string_skipped(self):
        """对照：chapter 1 是『# Foo』string content（不是 word_count=0 空），但 content == title
        也应被识别为『纯文档标题』并跳过。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        from platform_app.db import connect
        with connect() as db:
            sid = int(db.execute(
                "insert into scripts(owner_id, title) values (%s, %s) returning id",
                (uid, "doc_title_str_test"),
            ).fetchone()["id"])
            # content 跟 title 一致（除 # 之外）
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content, word_count) "
                "values (%s, %s, %s, %s, %s)",
                (sid, 1, "# 总标题", "# 总标题", 0),
            )
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content, word_count) "
                "values (%s, %s, %s, %s, %s)",
                (sid, 2, "## 真章节", "正文。 当前地点：测试地。 时间锚点：测试时。", 80),
            )
        r = self.client.post("/api/saves", json={
            "title": "doc-title-str save",
            "script_id": sid,
            "new_card": {"name": "X", "role": "Y", "background": "Z"},
        }, cookies=cookies)
        snap = ((self.client.get(
            f"/api/saves/{int(((r.json() or {}).get('save') or {}).get('id') or 0)}",
            cookies=cookies).json() or {}).get("save") or {}).get("state_snapshot") or {}
        if isinstance(snap, str):
            snap = json.loads(snap)
        self.assertEqual((snap.get("player") or {}).get("current_location"), "测试地",
            "应跳过『# 总标题』，使用 chapter 2 的 inline meta")


if __name__ == "__main__":
    unittest.main(verbosity=2)
