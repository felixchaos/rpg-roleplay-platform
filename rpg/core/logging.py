"""core.logging — 项目级 logger 入口。

当前项目大量用 print()，后续可改用 logger。此模块提供统一 get_logger()
入口，避免子模块各自 logging.getLogger(__name__)。
"""
from __future__ import annotations

import logging


def get_logger(name: str = "rpg") -> logging.Logger:
    """获取一个 logger 实例。"""
    return logging.getLogger(name)


def setup_default_logging(level: int = logging.INFO) -> None:
    """配置默认 logger (basicConfig)。一般在 app.py 启动时调一次。"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
