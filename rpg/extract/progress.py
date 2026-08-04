"""extract/progress.py — 进度回调安全包装(pipeline / arc_pipeline 共用)。

铁律:**取消信号必须向上抛**。
job_runner.cb 通过 raise InterruptedError("cancelled") 来表达"用户取消了",
靠它一路冒泡到 job_runner._run 的 except InterruptedError 收尾标 cancelled。
历史 bug:pipeline._emit / arc_pipeline._emit 用 `except Exception` 把这个
InterruptedError 当普通"进度上报失败"吞掉 → 取消信号丢失 → 提取继续(甚至卡死在
未完成的 LLM future)→ import_jobs 行永远停在 status='running' 变僵尸。

因此:InterruptedError / KeyboardInterrupt 一律 re-raise;只有真正的"进度上报失败"
(写 DB 抖动等)才吞掉 + log.warning,不让进度上报本身拖垮提取。
"""
from __future__ import annotations

import logging
from collections.abc import Callable


def emit_progress(
    progress_cb: Callable[[str, dict], None] | None,
    stage: str,
    info: dict,
    *,
    source: str,
) -> None:
    """安全调用 progress_cb;取消信号上抛,其余异常吞掉只 log。

    source: 调用方模块名(__name__),用作 logger 名 + 日志前缀。
    """
    if not progress_cb:
        return
    try:
        progress_cb(stage, info)
    except (InterruptedError, KeyboardInterrupt):
        # 用户取消:必须向上传播(见模块 docstring)。绝不能吞。
        raise
    except Exception as exc:
        logging.getLogger(source).warning(
            "[%s] progress_cb failed for stage=%s: %s", source, stage, exc, exc_info=True,
        )
