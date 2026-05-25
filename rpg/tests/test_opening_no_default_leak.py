"""
test_opening_no_default_leak.py — task 43 回归

用户报告：/api/opening 的 query 硬编码『柏林 图卢兹 娅赛兰 蛇信 蕾穆丽娜』，并且没把
script_id 传给 retrieve_context → retrieval.py 退化到 is_default=True → 拉 MuMu
.webnovel SQLite/indexes JSON。即便存档来自导入剧本，opening 的 memory.last_retrieval
仍含默认 MuMu 内容。

修复：
  - /api/opening 拿 _active_script_id(api_user)，按非默认/默认两条路径构 query
  - retrieve_context 收 script_id —— 非默认走 task 42 script-scoped 路径，不读 MuMu 私有源
"""
from __future__ import annotations

import unittest

from tests.helpers import cleanup_test_users, make_client, register_user


TEST_CHAPTER_CONTENT = (
    "申时三刻，雾港码头的铜钟敲了六下。"
    "玩家角色『测试旅人』刚从破损的渡船上醒来，手里只有一枚蓝色罗盘。"
    "守灯人沈知微告诉他：今晚子时，灯塔会出现只持续一刻钟的星门。"
    "  当前地点：雾港码头。 当前目标：确认蓝色罗盘是否能打开灯塔星门。 时间锚点：申时三刻。"
)

FORBIDDEN_DEFAULT_TOKENS = (
    "柏林", "图卢兹", "哈布斯堡", "蛇信", "薇瑟", "扎兹巴鲁姆",
    "蕾穆丽娜", "斯雷因", "伊奈帆", "甲胄骑士", "Kataphrakt",
    "调令伪造", "娅赛兰", "宴会上调令伪造",
)


class OpeningRetrievalNoDefaultLeak(unittest.TestCase):
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

    def _mk_imported_save(self, uid: int) -> int:
        """建一个 script + chapter + save（带 new_card），并 activate 这个 save。"""
        from platform_app.db import connect
        from platform_app import workspace, branches
        with connect() as db:
            scr = db.execute(
                "insert into scripts(owner_id, title, source_path) values (%s, %s, %s) returning id",
                (uid, "task43_imported", "platform_data/scripts/user_x/foo.txt"),
            ).fetchone()
            sid = int(scr["id"])
            # 模拟真实 import shape：chapter 1 是文档总标题、chapter 2 是真章节
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content, word_count) "
                "values (%s, %s, %s, %s, %s)",
                (sid, 1, "# 时间线与 Set 功能测试剧本", "", 0),
            )
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content, word_count) "
                "values (%s, %s, %s, %s, %s)",
                (sid, 2, "## 第一章 雾港入夜", TEST_CHAPTER_CONTENT, 124),
            )
        save = workspace.create_save(uid, sid, "task43 save", new_card={
            "name": "测试旅人", "role": "时间线测试者", "background": "用于 task43 opening 测试。",
        })
        save_id = int(save["id"])
        # activate
        branches.activate_save(uid, save_id)
        return save_id

    def _consume_sse(self, resp) -> list[dict]:
        events: list[dict] = []
        ev = "message"
        data_lines: list[str] = []
        for raw_line in resp.iter_lines():
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if line == "":
                if data_lines:
                    import json as _json
                    try:
                        d = _json.loads("\n".join(data_lines))
                    except Exception:
                        d = "\n".join(data_lines)
                    events.append({"event": ev, "data": d})
                ev = "message"; data_lines = []
                continue
            if line.startswith("event:"):
                ev = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        return events

    def _patch_gm_to_canned_opening(self):
        """让 /api/opening 不真打 LLM —— 直接返回固定的开场文本。"""
        import app as ui_mod

        class _Stub:
            api_id = "stub"
            class _B:
                model_name = "stub"
                last_usage = {}
            _backend = _B()
            def generate_opening(self, state, retrieved_context=""):
                return "（测试存根开场：雾港码头雾色未散。）"
            def respond_stream_with_tools(self, *a, **kw):
                if False: yield {}
                return
            def curate_context(self, *a, **kw):
                return ""

        orig_get = ui_mod._get_gm
        ui_mod._get_gm = lambda u: _Stub()
        return ui_mod, orig_get

    def _restore_gm(self, ui_mod, orig_get):
        ui_mod._get_gm = orig_get

    def test_opening_for_imported_script_no_berlin_leak(self):
        """核心回归：导入剧本 save → POST /api/opening → /api/state.memory.last_retrieval
        不应含任何柏林/MuMu 默认 token；同时不应出现 ChapterFact时间线/相关原文片段/
        最近剧情摘要/相关角色 这 4 个默认来源 section header。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        save_id = self._mk_imported_save(uid)

        ui_mod, orig_get = self._patch_gm_to_canned_opening()
        try:
            # 走 ui 的状态缓存：先清掉避免上一个 save 的缓存污染（task 30 已有此机制）
            ui_mod._invalidate_user_cache({"id": uid, "username": u["username"], "role": "user"})

            with self.client.stream("POST", "/api/opening", json={}, cookies=cookies) as resp:
                self.assertEqual(resp.status_code, 200, "POST /api/opening 应 200")
                events = self._consume_sse(resp)
            event_names = [e["event"] for e in events]
            for ev in events:
                if ev["event"] == "error":
                    self.fail(f"opening 不应 error；err={ev['data']!r}")
            self.assertIn("done", event_names, f"应有 done event；got={event_names}")

            # 查 state
            r2 = self.client.get("/api/state", cookies=cookies)
            self.assertEqual(r2.status_code, 200, r2.text[:200])
            state_payload = r2.json() or {}
            self.assertEqual(int(state_payload.get("save_id") or 0), save_id,
                f"opening 后 state.save_id 应仍是导入 save={save_id}；实际 {state_payload.get('save_id')}")
            memory = state_payload.get("memory") or {}
            last_retrieval = str(memory.get("last_retrieval") or "")

            # 关键断言：last_retrieval 不应含任何默认 MuMu 柏林 token
            leaked = [t for t in FORBIDDEN_DEFAULT_TOKENS if t in last_retrieval]
            self.assertEqual(leaked, [],
                f"task 43：导入剧本 /api/opening 后 last_retrieval 不应含柏林 token；"
                f"leaked={leaked}\nlast_retrieval(前 800 字)={last_retrieval[:800]!r}")

            # 同样不应含 4 个默认来源 section header
            for header in ("ChapterFact时间线", "相关原文片段", "最近剧情摘要", "相关角色"):
                self.assertNotIn(header, last_retrieval,
                    f"task 43：导入剧本 last_retrieval 不应含默认来源『{header}』；"
                    f"head={last_retrieval[:600]!r}")

            # task 43 用户加强要求：导入剧本 token 必须能在 last_retrieval 出现
            # （证明 retrieval 真的拿了当前剧本的内容，不是空白）。
            # opening 之前 _apply_script_opening 已写过 last_retrieval 含『剧本开场 · 第一章 雾港入夜』；
            # opening 后 set_last_retrieval 覆盖为 retrieve_context 输出 —— 那段不一定立刻含『雾港』，
            # 但至少『时间线检索锚点』section 必须出现，且 query 里的 player.current_location='雾港码头'
            # / world.time='申时三刻' 会被 _build_initial_snapshot 写到 state，
            # /api/opening 动态构 query 时拼成"雾港码头 申时三刻 ..."一并落到检索锚点行。
            self.assertIn("时间线检索锚点", last_retrieval,
                f"task 43：opening 后 last_retrieval 应包含『时间线检索锚点』section；"
                f"head={last_retrieval[:600]!r}")
            imported_required_any = ["雾港", "申时三刻", "蓝色罗盘", "灯塔星门"]
            matched = [t for t in imported_required_any if t in last_retrieval]
            self.assertTrue(matched,
                f"task 43：opening 后 last_retrieval 应至少含一项导入剧本 token "
                f"[{ ' / '.join(imported_required_any) }]；"
                f"head={last_retrieval[:800]!r}")

            # opening 文本本身应保留（GM stub 写死了）
            history = state_payload.get("history") or []
            self.assertTrue(any("测试存根开场" in str(m.get("content", "")) for m in history),
                f"history 应含 GM stub opening；history={history!r}")
        finally:
            self._restore_gm(ui_mod, orig_get)

    def test_default_mumu_save_opening_unchanged(self):
        """对照：默认 MuMu script 的 save（source_path='rpg/indexes'）走原硬编码 query
        + 完整 SQLite/JSON 默认来源；行为不被 task 43 破坏。"""
        u = register_user(self.client)
        cookies = u["cookies"]
        uid = self._uid(u["username"])
        # 用真实 ensure_default 创默认 MuMu 剧本 + save
        from platform_app import workspace, branches
        from platform_app.db import connect
        workspace.ensure_default(uid)
        with connect() as db:
            sid = int(db.execute(
                "select id from scripts where owner_id = %s and source_path like 'rpg/indexes%%' limit 1",
                (uid,),
            ).fetchone()["id"])
            save_row = db.execute(
                "select id from game_saves where user_id = %s and script_id = %s order by id limit 1",
                (uid, sid),
            ).fetchone()
            save_id = int(save_row["id"])
        branches.activate_save(uid, save_id)

        ui_mod, orig_get = self._patch_gm_to_canned_opening()
        try:
            ui_mod._invalidate_user_cache({"id": uid, "username": u["username"], "role": "user"})
            with self.client.stream("POST", "/api/opening", json={}, cookies=cookies) as resp:
                self.assertEqual(resp.status_code, 200)
                _ = self._consume_sse(resp)
            r2 = self.client.get("/api/state", cookies=cookies)
            self.assertEqual(r2.status_code, 200)
            payload = r2.json() or {}
            # 默认 MuMu save 仍允许出现柏林 token（这是它的正常剧情）—— 不破坏老 user
            # 至少 last_retrieval 应非空、应出现『时间线检索锚点』section
            mem = payload.get("memory") or {}
            lr = str(mem.get("last_retrieval") or "")
            self.assertIn("时间线检索锚点", lr,
                f"对照：默认 MuMu save opening 仍应生成 last_retrieval 并含时间线锚点；"
                f"head={lr[:400]!r}")
        finally:
            self._restore_gm(ui_mod, orig_get)


class OldUIEmptyStateCopy(unittest.TestCase):
    """单元：旧 UI 的『准备继续柏林弧』空状态文案被去硬编码。"""

    def test_initial_html_uses_generic_copy(self):
        from pathlib import Path
        ui_src = Path(__file__).resolve().parents[1] / "app.py"
        text = ui_src.read_text(encoding="utf-8")
        # task 43：旧 UI 初始 <div class="empty"> 不应仍硬编码"准备继续柏林弧"
        # 出现的应只是注释（task 43 注释里有提）；找 <h1>...</h1> 块
        import re
        h1_blocks = re.findall(r'<h1>([^<]+)</h1>', text)
        for block in h1_blocks:
            self.assertNotIn("准备继续柏林弧", block,
                f"task 43：<h1>{block}</h1> 仍硬编码柏林弧；"
                f"旧 UI 初始空状态应改为通用文案『准备开始游戏』")

    def test_render_messages_js_reads_payload_title(self):
        from pathlib import Path
        ui_src = Path(__file__).resolve().parents[1] / "app.py"
        text = ui_src.read_text(encoding="utf-8")
        # renderMessages 内必须按 state.payload.save_title / app.title 动态构标题，
        # 不能再写死『准备继续柏林弧』
        self.assertIn("准备开始游戏", text,
            "task 43：JS 空状态 fallback 应含通用『准备开始游戏』")
        # 任何 JS string literal 含『准备继续柏林弧』都算回归
        import re
        # 找 innerHTML = "..." 含柏林弧 模式
        suspicious = re.findall(r'innerHTML\s*=\s*[`"\'][^`"\']*准备继续柏林弧[^`"\']*[`"\']', text)
        self.assertEqual(suspicious, [],
            f"task 43：renderMessages 仍硬编码『准备继续柏林弧』：{suspicious!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
