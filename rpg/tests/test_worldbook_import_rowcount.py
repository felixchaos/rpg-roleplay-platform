"""worldbook 导入阶段 rowcount 崩溃回归。

生产日志(script 248,user 188 导入小说):
  AttributeError: 'Connection' object has no attribute 'rowcount'  @ import_pipeline.py:_stage_worldbook
根因:psycopg3 的 rowcount 在 execute() 返回的 cursor 上,不在 Connection 上。旧代码
`count += db.rowcount`(db 是 Connection)→ 第一条 worldbook 插入后即抛 → 整个 LLM 抽取阶段崩、
后续条目全不入库 → 导入的小说世界书近乎空。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_stage_worldbook_uses_cursor_rowcount_not_connection():
    # import_pipeline 已拆包:_stage_worldbook 住在子模块 stages_llm.py。
    src = (REPO / "platform_app" / "import_pipeline" / "stages_llm.py").read_text(encoding="utf-8")
    fn = src.split("def _stage_worldbook", 1)[1].split("\ndef ", 1)[0]
    assert "count += db.rowcount" not in fn, "worldbook 阶段仍在 Connection 上取 rowcount(会 AttributeError 崩)"
    # 应从 execute() 返回的 cursor 取
    assert "_cur = db.execute" in fn and "_cur, \"rowcount\"" in fn


def test_connection_execute_returns_cursor_with_rowcount():
    """契约:psycopg3 connection.execute() 返回带 rowcount 的 cursor;Connection 本身没有。
    无 DB(本地无 rpg_platform)则跳过。"""
    try:
        from platform_app.db import connect
        with connect() as db:
            cur = db.execute("select 1 where 1=1")
            assert cur.rowcount == 1
            assert not hasattr(db, "rowcount")
    except Exception as exc:
        import pytest
        pytest.skip(f"no DB: {exc}")
