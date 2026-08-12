from __future__ import annotations

from pathlib import Path

# 改名式再导出:imports.py 与多个测试模块都从这里 import SCRIPT_ROOT。
# ruff 的 F401 认不出「as 改名」形式的再导出(只认 `X as X`),会把它当未使用直接删掉
# —— 本 PR 的 CI 红就是这么来的(6 个测试模块 ImportError)。noqa 钉死。
from ..storage import SCRIPTS_DIR as SCRIPT_ROOT  # noqa: F401
from ..storage import UPLOAD_CHUNKS_DIR as UPLOAD_CHUNK_ROOT  # noqa: F401

from core.config import (
    script_upload_max_bytes as _script_upload_max_bytes,
)
from core.config import (
    upload_chunk_max_bytes as _upload_chunk_max_bytes,
)

# 拆包前单文件 script_import.py 用 Path(__file__).resolve().parents[1] 定位 rpg/ 根;
# 拆成子包后本文件深一层(rpg/platform_app/script_import/_base.py),parents[2] 才是 rpg/。
BASE = Path(__file__).resolve().parents[2]

MAX_SCRIPT_UPLOAD_BYTES = _script_upload_max_bytes()
MAX_UPLOAD_CHUNK_BYTES = _upload_chunk_max_bytes()  # 8MB / 块
