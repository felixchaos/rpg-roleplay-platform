"""phase digest backfill 必须挂进 run_cron COMMANDS(被每日 `run_cron all` 跑),
且命令对内部异常必须吞掉(返回 dict、绝不抛),否则一次 compact 失败会中断
`run_cron all` 的后续清理任务。

背景:异步 compact 失败/重启会留下 status='closed' summary='' 的 phase 行,
phase_digest_worker.py 有重试逻辑但此前没挂任何 cron → 永不自动重试。

注意(导入路径 cwd 敏感):run_cron.cmd_phase_digest_backfill 内部按
`from rpg.agents.phase_digest_agent import compact_phase` 优先、
`ModuleNotFoundError` 时回退 `from agents.phase_digest_agent import compact_phase`
的 dual-import 写法(生产 `-m rpg.scripts.run_cron` 跑,`rpg.*` 可导;而本项目
文档化的单测惯例是 cwd=`rpg/`,此时顶层 `rpg` 包不可导,回退分支才会命中)。
若 pytest 从仓库根(`rpg/` 的上一级)而非 `rpg/` 本身发起,repo 根会被
额外插进 sys.path,`rpg` 包意外变得可导,run_cron 会走前一分支 ——
只 mock 后一分支(`agents.phase_digest_agent` / `scripts.phase_digest_worker`)
会打在没被调用的模块对象上,测试假失败。用 `_patch_phase_digest_symbol`
按 `importlib.util.find_spec("rpg")` 探测当前进程里 `rpg.*` 是否可导,
同时 patch 两个候选模块路径(存在几个 patch 几个),让测试与 cwd 无关。
"""
import importlib.util
import unittest
from contextlib import ExitStack
from unittest import mock

from scripts import run_cron


def _patch_phase_digest_symbol(stack: ExitStack, submodule: str, symbol: str, **kwargs):
    """在 `<submodule>` 与 `rpg.<submodule>` 两个候选模块路径上都打 patch(存在几个打几个)。

    run_cron.cmd_phase_digest_backfill 到底 import 到哪一个模块对象,取决于当前
    进程 `rpg` 顶层包是否可导(cwd 敏感,见上方模块 docstring)。测试不应该依赖
    "pytest 恰好从哪个目录启动" 这种环境细节,所以两条路径能 patch 的都 patch。
    """
    targets = [submodule]
    if importlib.util.find_spec("rpg") is not None:
        targets.append(f"rpg.{submodule}")
    for target in targets:
        stack.enter_context(mock.patch(f"{target}.{symbol}", **kwargs))


class CronPhaseDigestBackfill(unittest.TestCase):
    def test_registered_in_commands(self):
        self.assertIn("phase_digest_backfill", run_cron.COMMANDS,
                      "phase_digest_backfill 未注册进 COMMANDS,`run_cron all` 不会跑它")
        self.assertIs(run_cron.COMMANDS["phase_digest_backfill"],
                      run_cron.cmd_phase_digest_backfill)

    def test_command_never_raises_on_find_pending_error(self):
        # find_pending 抛异常时命令必须吞掉(否则中断 run_cron all)
        fake_db = mock.MagicMock()
        with ExitStack() as stack:
            _patch_phase_digest_symbol(
                stack, "scripts.phase_digest_worker", "find_pending",
                side_effect=RuntimeError("db down"),
            )
            result = run_cron.cmd_phase_digest_backfill(fake_db)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["done"], 0)

    def test_command_isolates_per_phase_failure(self):
        # 单个 compact_phase 抛异常不应中断整批,计入 failed
        fake_db = mock.MagicMock()
        pend = [
            {"save_id": 1, "phase_index": 0, "user_id": 9},
            {"save_id": 1, "phase_index": 1, "user_id": 9},
        ]
        with ExitStack() as stack:
            _patch_phase_digest_symbol(
                stack, "scripts.phase_digest_worker", "find_pending", return_value=pend,
            )
            _patch_phase_digest_symbol(
                stack, "agents.phase_digest_agent", "compact_phase",
                side_effect=[{"summary": "ok"}, RuntimeError("llm boom")],
            )
            result = run_cron.cmd_phase_digest_backfill(fake_db)
        self.assertEqual(result["pending"], 2)
        self.assertEqual(result["done"], 1)
        self.assertEqual(result["failed"], 1)

    def test_no_key_error_counted_separately(self):
        fake_db = mock.MagicMock()
        pend = [{"save_id": 1, "phase_index": 0, "user_id": 9}]
        with ExitStack() as stack:
            _patch_phase_digest_symbol(
                stack, "scripts.phase_digest_worker", "find_pending", return_value=pend,
            )
            _patch_phase_digest_symbol(
                stack, "agents.phase_digest_agent", "compact_phase",
                return_value={"error": "no api key configured"},
            )
            result = run_cron.cmd_phase_digest_backfill(fake_db)
        self.assertEqual(result["skipped_no_key"], 1)
        self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
