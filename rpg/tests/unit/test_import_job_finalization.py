"""取消/僵尸收尾 —— import_jobs 状态泄漏 bug 的回归测试。

bug:用户取消导入(stop_event → job_runner.cb raise InterruptedError)后,
import_jobs.status 仍停在 'running' 变僵尸(前端"导入中"卡死 + 重启前活跃检查被误导)。
根因:extract/pipeline.py 与 arc_pipeline.py 的 _emit 把 InterruptedError 当普通异常吞掉
→ 取消信号丢失。修复三层:① emit_progress 上抛取消信号 ② finally 块 finalize 兜底
③ startup 僵尸回收。

测试不依赖活 DB(用 fake connect),与套件其余 unit 测试一致(OSS fork 无 psycopg/PG)。
"""
from __future__ import annotations

import contextlib

import pytest


# ── fake DB:够真,能驱动 finalize/reap 的 Python 分支 ────────────────────────
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


_TERMINAL = ("done", "done_with_errors", "failed", "cancelled")


class _FakeDB:
    """jobs: dict[job_id -> row dict]。模拟 import_jobs 表的相关行为。

    reaper 的真实选择条件(make_interval / not exists token_usage)跑在 SQL 里,fake 无法
    复刻日期数学 —— 这里用行上的测试标记 `_zombie=True` 代表"已满足回收条件",以验证
    Python wrapper 的行处理(returning→count→映射、跳过 knowledge_sync、跳过非 running)。
    SQL 谓词语义由代码审查 + 生产保证。
    """

    def __init__(self, jobs):
        self.jobs = jobs
        self.sql_log: list[tuple[str, tuple | None]] = []

    def execute(self, sql, params=None):
        self.sql_log.append((sql, params))
        norm = " ".join(sql.lower().split())
        # 1) finalize 的 SELECT
        if norm.startswith("select status, cancel_requested from import_jobs"):
            jid = params[0]
            row = self.jobs.get(jid)
            return _Cur([dict(row)] if row else [])
        # 2) reaper 的 UPDATE ... RETURNING(SQL 是 "update import_jobs j set ...")
        if norm.startswith("update import_jobs j"):
            reaped = []
            for jid, row in self.jobs.items():
                if (
                    row.get("status") == "running"
                    and row.get("kind") != "knowledge_sync"
                    and row.get("_zombie")
                ):
                    row["status"] = "failed"
                    row.setdefault("finished_at", "now()")
                    if not row.get("error"):
                        row["error"] = "reaped_zombie_stale_running"
                    reaped.append(
                        {"job_id": jid, "kind": row.get("kind"), "script_id": row.get("script_id")}
                    )
            return _Cur(reaped)
        # 3) finalize 的 UPDATE(带 where status not in terminal 的幂等护栏)
        if norm.startswith("update import_jobs set status = %s"):
            final, note, jid = params
            row = self.jobs.get(jid)
            if row and row.get("status") not in _TERMINAL:
                row["status"] = final
                row.setdefault("finished_at", "now()")
                if not row.get("error"):
                    row["error"] = note
            return _Cur([])
        return _Cur([])


@pytest.fixture()
def ip(monkeypatch):
    """patch import_pipeline 的 connect/init_db 为 fake,返回 (module, install_db)。

    拆包后 patch-where-defined:finalize_job_if_unterminated / reap_zombie_import_jobs
    定义在子模块 runner,monkeypatch 它们内部用的 connect/init_db 必须打在定义模块上。
    """
    from platform_app.import_pipeline import runner as _ip

    def _install(jobs):
        db = _FakeDB(jobs)

        @contextlib.contextmanager
        def _fake_connect():
            yield db

        monkeypatch.setattr(_ip, "connect", _fake_connect)
        monkeypatch.setattr(_ip, "init_db", lambda: None)
        return db

    return _ip, _install


# ── finalize_job_if_unterminated ────────────────────────────────────────────
def test_cancel_requested_running_becomes_cancelled(ip):
    """模拟取消 → 断言 job 终态非 running(任务要求的核心断言)。"""
    mod, install = ip
    jobs = {"llm_1": {"status": "running", "cancel_requested": True, "error": ""}}
    install(jobs)
    assert mod.finalize_job_if_unterminated("llm_1") == "cancelled"
    assert jobs["llm_1"]["status"] == "cancelled"
    assert jobs["llm_1"]["status"] != "running"
    assert jobs["llm_1"].get("finished_at")


def test_running_without_cancel_becomes_failed(ip):
    """worker 线程已结束却没正常收尾(被吞的异常/早退漏标)→ failed,不留 running。"""
    mod, install = ip
    jobs = {"imp_2": {"status": "running", "cancel_requested": False, "error": ""}}
    install(jobs)
    assert mod.finalize_job_if_unterminated("imp_2") == "failed"
    assert jobs["imp_2"]["status"] == "failed"
    assert jobs["imp_2"].get("finished_at")


def test_pending_without_cancel_becomes_failed(ip):
    """queued/pending 也是非终态,同样兜底。"""
    mod, install = ip
    jobs = {"imp_3": {"status": "pending", "cancel_requested": False, "error": ""}}
    install(jobs)
    assert mod.finalize_job_if_unterminated("imp_3") == "failed"
    assert jobs["imp_3"]["status"] == "failed"


@pytest.mark.parametrize("terminal", ["done", "done_with_errors", "failed", "cancelled"])
def test_terminal_status_is_noop(ip, terminal):
    """已收尾的行 finalize 是 no-op(幂等),不覆盖正常收尾结果。"""
    mod, install = ip
    jobs = {"j": {"status": terminal, "cancel_requested": True, "error": "orig"}}
    install(jobs)
    assert mod.finalize_job_if_unterminated("j") is None
    assert jobs["j"]["status"] == terminal  # 未被改动
    assert jobs["j"]["error"] == "orig"


def test_missing_row_returns_none(ip):
    mod, install = ip
    install({})
    assert mod.finalize_job_if_unterminated("nope") is None


def test_finalize_never_raises_on_db_error(ip, monkeypatch):
    """finally 兜底铁律:finalize 自身绝不能抛,否则会 mask 掉原始异常。"""
    mod, _ = ip
    monkeypatch.setattr(mod, "init_db", lambda: None)

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mod, "connect", _boom)
    assert mod.finalize_job_if_unterminated("x") is None  # 吞掉异常,返回 None


# ── reap_zombie_import_jobs ──────────────────────────────────────────────────
def test_reaper_marks_only_stale_running_failed(ip):
    mod, install = ip
    jobs = {
        "imp_zombie": {"status": "running", "kind": "full_pipeline", "script_id": 63, "_zombie": True, "error": ""},
        "llm_zombie": {"status": "running", "kind": "llm_extract", "script_id": 63, "_zombie": True, "error": ""},
        "imp_active": {"status": "running", "kind": "full_pipeline", "script_id": 64, "error": ""},   # 无 _zombie:仍活跃
        "sync_stale": {"status": "running", "kind": "knowledge_sync", "script_id": 65, "_zombie": True, "error": ""},  # 归 sync 恢复管
        "done_old":   {"status": "done", "kind": "full_pipeline", "script_id": 66, "error": ""},
    }
    db = install(jobs)
    res = mod.reap_zombie_import_jobs(stale_hours=6)
    assert res["ok"] is True
    assert res["reaped"] == 2
    reaped_ids = {j["job_id"] for j in res["jobs"]}
    assert reaped_ids == {"imp_zombie", "llm_zombie"}
    # 被回收的标 failed
    assert jobs["imp_zombie"]["status"] == "failed"
    assert jobs["llm_zombie"]["status"] == "failed"
    # 活跃的、knowledge_sync 的、已终态的 —— 一律不动
    assert jobs["imp_active"]["status"] == "running"
    assert jobs["sync_stale"]["status"] == "running"
    assert jobs["done_old"]["status"] == "done"
    # SQL 必须带关键护栏(防回归到误杀)
    reap_sql = " ".join(db.sql_log[-1][0].lower().split())
    assert "status = 'running'" in reap_sql
    assert "kind <> 'knowledge_sync'" in reap_sql
    assert "token_usage" in reap_sql
    assert "make_interval" in reap_sql


def test_reaper_stale_hours_from_env(ip, monkeypatch):
    mod, install = ip
    install({})
    monkeypatch.setenv("IMPORT_ZOMBIE_STALE_HOURS", "12")
    res = mod.reap_zombie_import_jobs()  # 不传参 → 读 env,不报错
    assert res["ok"] is True and res["reaped"] == 0


# ── emit_progress(取消信号传播)──────────────────────────────────────────────
def test_emit_progress_reraises_interrupted():
    from extract.progress import emit_progress

    def cb(stage, info):
        raise InterruptedError("cancelled")

    with pytest.raises(InterruptedError):
        emit_progress(cb, "per_chapter", {"done": 300}, source="extract.pipeline")


def test_emit_progress_reraises_keyboard_interrupt():
    from extract.progress import emit_progress

    def cb(stage, info):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        emit_progress(cb, "seed", {}, source="extract.arc_pipeline")


def test_emit_progress_swallows_other_exceptions():
    from extract.progress import emit_progress

    def cb(stage, info):
        raise ValueError("progress write hiccup")

    # 普通"进度上报失败"吞掉,不拖垮提取
    assert emit_progress(cb, "resolve", {}, source="extract.pipeline") is None


def test_emit_progress_none_cb_is_noop():
    from extract.progress import emit_progress

    assert emit_progress(None, "done", {}, source="extract.pipeline") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
