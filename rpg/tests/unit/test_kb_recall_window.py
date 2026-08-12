"""kb.recall 原文片段近进度窗口(save 268 行者实锤回归)。

症状:「剧本阶段摘要/章节事实都到 17 章了,Postgres 原文片段还是 6-9」。
根因:chunks 检索 chapter_min=None 只有防剧透上界,全书 ≤ceil 纯相似度排序,
旧章大剧情恒赢当前章。修=两级窗口:[ceil-3, ceil] 优先,不足才向更早章补位。
"""
from unittest import mock

from kb.recall import CHUNK_RECENT_WINDOW, recall


class _Res:
    def __init__(self, rows=None, one=None):
        self._rows, self._one = rows or [], one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class _DB:
    def execute(self, sql, params=None):
        s = sql.lower()
        if "from game_saves" in s:
            return _Res(one={"script_id": 143, "user_id": 115})
        return _Res(rows=[])  # kb_nodes 向量/关键词路全空


def _run_recall(chunk_calls, first_batch):
    """跑一次 recall,捕获 _search_chunks 调用参数。first_batch=首窗返回的行。"""
    batches = [list(first_batch)]

    def _fake_search_chunks(db, script_id, tokens, cmin, cmax, top_k, *, user_id=None):
        chunk_calls.append({"cmin": cmin, "cmax": cmax, "top_k": top_k})
        return batches.pop(0) if batches else [
            {"id": 900 + i, "chapter_index": 5, "content": "旧章片段"} for i in range(top_k)]

    with mock.patch("platform_app.knowledge._search._search_chunks", _fake_search_chunks), \
         mock.patch("platform_app.knowledge._search._embed_query", lambda *a, **k: None):
        return recall(268, "神殿 试炼 队伍", mode="none", progress_chapter=17, db=_DB())


def test_chunks_prefer_recent_progress_window():
    calls = []
    full = [{"id": i, "chapter_index": 17 - i, "content": f"近章{i}"} for i in range(4)]
    out = _run_recall(calls, full)
    # 首窗 = [ceil-窗宽+1, ceil] = [14, 17]
    assert calls[0] == {"cmin": 17 - CHUNK_RECENT_WINDOW + 1, "cmax": 17, "top_k": 4}
    # 首窗已满 4 条 → 不再查更早章
    assert len(calls) == 1
    assert len(out.chunks) == 4


def test_chunks_backfill_from_older_chapters_when_window_thin():
    calls = []
    thin = [{"id": 1, "chapter_index": 17, "content": "近章唯一命中"}]
    out = _run_recall(calls, thin)
    assert len(calls) == 2, "首窗不足须向更早章补位(玩家问旧事仍召得到)"
    assert calls[1]["cmin"] is None and calls[1]["cmax"] == 13 and calls[1]["top_k"] == 3
    assert len(out.chunks) == 4
    assert out.chunks[0]["chapter_index"] == 17, "近窗片段排在前"


def test_chunks_no_backfill_when_ceiling_small():
    calls = []
    with mock.patch("platform_app.knowledge._search._search_chunks",
                    lambda *a, **k: (calls.append({"cmin": a[3], "cmax": a[4]}), [])[1]), \
         mock.patch("platform_app.knowledge._search._embed_query", lambda *a, **k: None):
        recall(268, "开局", mode="none", progress_chapter=2, db=_DB())
    # ceil=2 → floor=1,不存在更早章,只查一次
    assert calls == [{"cmin": 1, "cmax": 2}]
