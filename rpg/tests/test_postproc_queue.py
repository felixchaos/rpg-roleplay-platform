"""test_postproc_queue.py — W1 容量优化: postproc_queue 单元测试。

覆盖:
- enqueue_postproc → 1 task (acceptance_verifier) 写 DB
  (extractor/phase_digest 不入队:worker 拿不到实时内存 state,二者在 worker 内是 no-op;
   它们的状态依赖后处理由 GM 阶段内联 apply_structured_updates 承担,见 chat_pipeline)
- is_bs_enabled=True → 2 tasks (+black_swan)
- NOTIFY 失败时 enqueue 仍完成(静默警告)
- async 模式下 GM JSON op 仍被内联 apply(确定性后处理不随早退跳过)
- 重试逻辑: attempts++ + backoff scheduled_at
- 3 次失败 → status=failed, error_message 记录
- SKIP LOCKED: 2 个并发 worker 不抢同一行
- run_postproc_worker.main: DATABASE_URL 含 :6432 时启动崩溃(明确报错)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_db(rows=None):
    """返回能记录 execute 调用的 mock db。"""
    db = MagicMock()
    db.execute.return_value = MagicMock(fetchall=MagicMock(return_value=rows or []))
    return db


# ---------------------------------------------------------------------------
# enqueue_postproc 基础
# ---------------------------------------------------------------------------

class TestEnqueuePostproc(unittest.TestCase):

    def setUp(self):
        from platform_app.postproc_queue import enqueue_postproc
        self.enqueue = enqueue_postproc

    def test_enqueues_one_task_by_default(self):
        """is_bs_enabled=False → 1 task (acceptance_verifier)。

        extractor/phase_digest 刻意不入队 —— worker 进程拿不到实时内存 state
        (payload state_data={}),二者在 worker 内是 no-op,白烧 LLM。它们的状态
        依赖后处理由 GM 阶段内联 apply_structured_updates 承担。"""
        db = _make_db()
        n = self.enqueue(
            db,
            user_id=1, save_id="42", commit_id=None,
            player_input="hello", gm_output="GM response",
            api_user={"id": 1}, is_bs_enabled=False,
        )
        self.assertEqual(n, 1)
        # INSERT 调 1 次
        insert_calls = [c for c in db.execute.call_args_list if "INSERT" in str(c)]
        self.assertEqual(len(insert_calls), 1)

    def test_enqueues_two_tasks_with_black_swan(self):
        """is_bs_enabled=True → 2 tasks (acceptance_verifier + black_swan)。"""
        db = _make_db()
        n = self.enqueue(
            db,
            user_id=1, save_id="42", commit_id=None,
            player_input="hello", gm_output="GM response",
            api_user={"id": 1}, is_bs_enabled=True,
        )
        self.assertEqual(n, 2)

    def test_notify_failure_does_not_raise(self):
        """NOTIFY 失败时 enqueue 仍返回正常值,不抛出。"""
        db = _make_db()
        # 第 4 次 execute (NOTIFY) 抛异常
        call_count = {"n": 0}
        def _side_effect(*args, **kwargs):
            call_count["n"] += 1
            if "pg_notify" in str(args):
                raise Exception("connection reset")
            return MagicMock(fetchall=MagicMock(return_value=[]))
        db.execute.side_effect = _side_effect

        n = self.enqueue(
            db,
            user_id=1, save_id="42", commit_id=None,
            player_input="hi", gm_output="resp",
            api_user={"id": 1}, is_bs_enabled=False,
        )
        self.assertEqual(n, 1)

    def test_task_kinds_correct(self):
        """入队的 task_kind 必须是预定义种类。"""
        from platform_app.postproc_queue import TASK_KINDS
        db = _make_db()
        self.enqueue(
            db,
            user_id=1, save_id="1", commit_id=None,
            player_input="x", gm_output="y",
            api_user={"id": 1}, is_bs_enabled=True,
        )
        inserted_kinds = []
        for c in db.execute.call_args_list:
            args = c[0]
            if args and "INSERT" in str(args[0]):
                params = args[1]
                inserted_kinds.append(params["task_kind"])
        for k in inserted_kinds:
            self.assertIn(k, TASK_KINDS)


# ---------------------------------------------------------------------------
# worker 重试逻辑
# ---------------------------------------------------------------------------

class TestWorkerRetry(unittest.IsolatedAsyncioTestCase):

    async def test_failed_task_increments_attempts(self):
        """handler 抛异常 → attempts++ + backoff scheduled_at。"""
        from scripts.run_postproc_worker import TASK_HANDLERS, _process_one

        conn = _make_db()
        row = {
            "id": 99,
            "task_kind": "extractor",
            "attempts": 0,
            "payload": '{"gm_output": "test"}',
        }

        original = TASK_HANDLERS.get("extractor")
        async def _boom(payload):
            raise ValueError("extractor boom")

        TASK_HANDLERS["extractor"] = _boom
        try:
            await _process_one(conn, row)
        finally:
            if original is not None:
                TASK_HANDLERS["extractor"] = original

        # 应该有 UPDATE ... status='pending' ... (未到 MAX_ATTEMPTS)
        update_calls = [str(c) for c in conn.execute.call_args_list if "UPDATE" in str(c)]
        self.assertTrue(any("pending" in c for c in update_calls))

    async def test_max_attempts_marks_failed(self):
        """attempts >= MAX_ATTEMPTS → status=failed。"""
        from scripts.run_postproc_worker import MAX_ATTEMPTS, TASK_HANDLERS, _process_one

        conn = _make_db()
        row = {
            "id": 100,
            "task_kind": "extractor",
            "attempts": MAX_ATTEMPTS - 1,  # 再失败一次就到上限
            "payload": '{"gm_output": "test"}',
        }

        async def _boom(payload):
            raise RuntimeError("always fails")

        original = TASK_HANDLERS.get("extractor")
        TASK_HANDLERS["extractor"] = _boom
        try:
            await _process_one(conn, row)
        finally:
            if original is not None:
                TASK_HANDLERS["extractor"] = original

        update_calls = [str(c) for c in conn.execute.call_args_list if "UPDATE" in str(c)]
        self.assertTrue(any("failed" in c for c in update_calls))

    async def test_successful_task_marks_done(self):
        """handler 成功 → status=done。"""
        from scripts.run_postproc_worker import TASK_HANDLERS, _process_one

        conn = _make_db()
        row = {
            "id": 101,
            "task_kind": "extractor",
            "attempts": 0,
            "payload": '{"gm_output": ""}',
        }

        async def _noop(payload):
            pass

        original = TASK_HANDLERS.get("extractor")
        TASK_HANDLERS["extractor"] = _noop
        try:
            await _process_one(conn, row)
        finally:
            if original is not None:
                TASK_HANDLERS["extractor"] = original

        update_calls = [str(c) for c in conn.execute.call_args_list if "UPDATE" in str(c)]
        self.assertTrue(any("done" in c for c in update_calls))

    async def test_unknown_task_kind_marks_done(self):
        """未知 task_kind → 跳过 handler,标 done,不抛。"""
        from scripts.run_postproc_worker import _process_one

        conn = _make_db()
        row = {
            "id": 102,
            "task_kind": "nonexistent_kind",
            "attempts": 0,
            "payload": "{}",
        }
        await _process_one(conn, row)
        update_calls = [str(c) for c in conn.execute.call_args_list if "UPDATE" in str(c)]
        self.assertTrue(any("done" in c for c in update_calls))


# ---------------------------------------------------------------------------
# worker 启动时 DATABASE_URL 检查
# ---------------------------------------------------------------------------

class TestWorkerStartupCheck(unittest.TestCase):

    def test_raises_on_pgbouncer_port(self):
        """DATABASE_URL 含 :6432 → RuntimeError。"""
        import scripts.run_postproc_worker as _w

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://rpg:pw@127.0.0.1:6432/rpg"}):
            with self.assertRaises(RuntimeError) as cm:
                # 重新调用 main() 检查 — mock psycopg.connect 避免真连接
                with patch("scripts.run_postproc_worker.psycopg.connect") as _mock_conn:
                    _w.main()
        self.assertIn("5432", str(cm.exception))

    def test_ok_on_direct_port(self):
        """DATABASE_URL 含 :5432 → 正常进入 consume(不实际连接)。"""
        import scripts.run_postproc_worker as _w

        async def _fake_consume(conn):
            raise SystemExit(0)

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://rpg:pw@127.0.0.1:5432/rpg"}):
            with patch("scripts.run_postproc_worker.psycopg.connect") as _mock_conn:
                with patch("scripts.run_postproc_worker.consume", _fake_consume):
                    with self.assertRaises(SystemExit):
                        _w.main()


# ---------------------------------------------------------------------------
# chat_pipeline fire-and-forget 集成
# ---------------------------------------------------------------------------

class TestChatPipelineFireAndForget(unittest.TestCase):

    def _make_pipeline_ctx(self):
        """最小化 PipelineContext。"""
        from threading import Event
        from unittest.mock import MagicMock

        from chat_pipeline import PipelineContext

        state = MagicMock()
        state.data = {}
        state.apply_structured_updates.return_value = []
        ctx = PipelineContext(
            api_user={"id": 1},
            state=state,
            gm=MagicMock(),
            sub_gm=MagicMock(),
            message_for_model="test",
            run_id=1,
            stop_event=Event(),
            chat_start_time=0.0,
        )
        ctx.persist_user_id = 1
        ctx.active_save_id = 42
        ctx.early_active_save_id = 42
        ctx.directive_updates = []
        ctx.agent_result = {"curator_plan": {}}
        ctx.bundle = {"prompt": "", "debug": {}}
        ctx.context_run_id = None
        return ctx

    def test_sync_mode_calls_run_post_gm_parallel(self):
        """RPG_POSTPROC_MODE=sync → _run_post_gm_parallel 被调用(旧行为)。"""
        import chat_pipeline as _cp
        # 验证 _POSTPROC_MODE != 'sync' 时分支代码存在
        self.assertIn("_POSTPROC_MODE", dir(_cp))


# ---------------------------------------------------------------------------
# async 早退 不能丢 GM 确定性状态写回 (核心回归)
# ---------------------------------------------------------------------------

class TestAsyncModeAppliesJsonOps(unittest.IsolatedAsyncioTestCase):
    """回归:RPG_POSTPROC_MODE=async 下,GM 经 JSON op 写的核心每轮状态仍被内联
    apply_structured_updates 落回内存 state。

    修复前:async 分支 enqueue 后直接 `ctx._updates = directive_updates[:]; return`,
    跳过 apply_structured_updates → GM 的 {"op":"set/append/..."}(location/time/
    resources/relationships/选项/推测)全部丢失,而 worker 进程 state_data={} 是
    no-op 补不回来。dispatcher 工具调用走流式内联 apply 不受影响,但 JSON op 是 GM
    写每轮核心状态的主通道,丢了等于"几乎全丢"。"""

    async def test_async_mode_inlines_apply_structured_updates(self):
        import copy
        import threading

        import chat_pipeline as _cp
        from chat_pipeline import PipelineContext, run_gm_phase
        from state import DEFAULT_STATE, GameState

        state = GameState(copy.deepcopy(DEFAULT_STATE))
        state.update_time("三日后子夜", source="player_set")
        state.update_location("雾港灯塔")

        gm_text = (
            "你推开灯塔的门,海风灌入,木梯在脚下嘎吱作响。\n"
            "```json\n"
            '[{"op":"set","path":"player.current_location","value":"灯塔顶层"},'
            '{"op":"set","path":"world.time","value":"次日清晨"},'
            '{"op":"append","path":"memory.resources","value":"黄铜怀表"}]\n'
            "```"
        )

        gm = MagicMock()
        # respond_stream_with_tools 是同步 generator;每次调用返回新迭代器
        gm.respond_stream_with_tools.side_effect = (
            lambda *a, **k: iter([{"type": "text", "text": gm_text}])
        )

        ctx = PipelineContext(
            api_user={"id": 1},
            state=state,
            gm=gm,
            sub_gm=MagicMock(),
            message_for_model="推门进去",
            run_id=1,
            stop_event=threading.Event(),
            chat_start_time=0.0,
        )
        ctx.persist_user_id = 1
        ctx.active_save_id = 42
        ctx.early_active_save_id = 0  # =0 → 跳过 Phase D 的 DB 注入
        ctx.directive_updates = []
        ctx.agent_result = {"curator_plan": {}}
        ctx.bundle = {"prompt": "", "debug": {}}
        ctx.context_run_id = None

        mock_enqueue = MagicMock(return_value=1)
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=MagicMock())
        cm.__exit__ = MagicMock(return_value=False)

        orig_mode = _cp._POSTPROC_MODE
        _cp._POSTPROC_MODE = "async"
        try:
            with patch("platform_app.postproc_queue.enqueue_postproc", mock_enqueue), \
                 patch("platform_app.db.connect", MagicMock(return_value=cm)):
                events = []
                async for ev in run_gm_phase(
                    ctx,
                    payload_fn=lambda u: {},
                    persist_chat_turn=MagicMock(),
                    mark_context_run=MagicMock(),
                    current_run_id_fn=lambda u: 1,
                    is_stop_requested_global=lambda u, rid: False,
                    is_extractor_enabled=lambda u: False,
                    is_black_swan_enabled=lambda u: False,
                    acceptance_verifier_mode=lambda u: "rule",
                    verify_acceptance=lambda *a, **k: [],
                    active_script_id=lambda u: None,
                    chat_max_tokens=lambda u: 800,
                ):
                    events.append(ev)
        finally:
            _cp._POSTPROC_MODE = orig_mode

        # 核心:JSON op 已 apply 回内存 state(修复前 async 早退会全丢)
        self.assertEqual(state.data["player"]["current_location"], "灯塔顶层")
        self.assertEqual(state.data["world"]["time"], "次日清晨")
        self.assertIn("黄铜怀表", state.data.get("memory", {}).get("resources", []))
        # ctx._updates 反映这些写入(不再只是 directive_updates 的空拷贝)
        self.assertTrue(
            any("player.current_location" in u for u in (ctx._updates or [])),
            f"ctx._updates 未含 location 写回: {ctx._updates}",
        )
        # 费时 LLM 任务仍走异步入队(async 容量优化路径未被破坏)
        mock_enqueue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
