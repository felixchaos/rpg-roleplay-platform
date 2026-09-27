"""检索前先看「这个剧本有没有可比的向量」+ 向量路没结果时退关键词(巡检 2026-09-28 F7 孪生)。

两件事:
1. 省请求:55/78 个剧本没有向量、桌面版没有 pgvector,此前每回合照样先嵌入查询、再发现没东西
   可比 —— 白花用户的钱、占中转站配额。现在先查存在性(带缓存),没有就不嵌入。
2. 召回归零:嵌入成功但表里没有可比的向量(零向量 / 只嵌了一张表 / 窗口内没嵌过)时,
   _search_chunks 直接返回空列表、_search_entities 不再走 ILIKE —— 原文片段和人物卡 / 世界书
   召回静默为空。现在向量路一条没拿到的表退关键词。
"""
from __future__ import annotations

from unittest import mock

import pytest

from platform_app.knowledge import _search


class _R:
    def __init__(self, rows=None, one=None):
        self._rows, self._one = rows or [], one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class _DB:
    """vec_cols:有真 pgvector 列的表;has_vec:该剧本在其中有非空向量的表;
    vec_rows / kw_rows:按表给向量路 / 关键词路的返回。"""

    def __init__(self, *, vec_cols=(), has_vec=(), vec_rows=None, kw_rows=None):
        self.vec_cols, self.has_vec = set(vec_cols), set(has_vec)
        self.vec_rows, self.kw_rows = vec_rows or {}, kw_rows or {}
        self.sql: list[str] = []

    @staticmethod
    def _table(sql: str) -> str:
        for t in ("document_chunks", "character_cards", "worldbook_entries", "kb_nodes"):
            if f"from {t}" in sql:
                return t
        return ""

    def execute(self, sql, params=None):
        self.sql.append(sql)
        s = " ".join(sql.split())
        if "information_schema.columns" in s:
            return _R(one={"x": 1} if params[0] in self.vec_cols else None)
        if "select embed_api_id, embed_model from scripts" in s:
            return _R(one={"embed_api_id": "openai", "embed_model": "text-embedding-3-small"})
        t = self._table(s)
        if s.startswith("select 1 from") and "embedding_vec is not null limit 1" in s:
            return _R(one={"x": 1} if t in self.has_vec else None)
        if "<=>" in s:
            return _R(rows=list(self.vec_rows.get(t, [])))
        if "ilike" in s:
            return _R(rows=list(self.kw_rows.get(t, [])))
        return _R()


@pytest.fixture
def spy_embed():
    calls = []

    def _fake(text, **kw):
        calls.append(text)
        return "[0.1,0.2]"

    with mock.patch.object(_search, "_embed_query", _fake):
        yield calls


_KW_CHUNK = [{"id": 9, "chapter_index": 3, "content": "关键词命中的原文", "score": 1}]
_VEC_CHUNK = [{"id": 1, "chapter_index": 3, "content": "向量命中的原文", "score": 0.9}]


# ── 原文片段 ─────────────────────────────────────────────────────────────────
def test_zero_vector_script_does_not_embed_and_uses_keywords(spy_embed):
    db = _DB(vec_cols={"document_chunks"}, has_vec=set(), kw_rows={"document_chunks": _KW_CHUNK})
    out = _search._search_chunks(db, 7, ["康拉德", "仓库"], None, 5, 4, user_id=1)
    assert spy_embed == [], "剧本没有向量 → 不嵌入查询"
    assert out == _KW_CHUNK


def test_no_pgvector_desktop_does_not_embed(spy_embed):
    db = _DB(vec_cols=set(), kw_rows={"document_chunks": _KW_CHUNK})
    out = _search._search_chunks(db, 7, ["康拉德"], None, 5, 4, user_id=1)
    assert spy_embed == []
    assert out == _KW_CHUNK


def test_vector_path_empty_falls_back_to_keywords(spy_embed):
    """剧本有向量、但这个章窗口里一条已嵌入的都没有 → 退关键词,别整段为空。"""
    db = _DB(vec_cols={"document_chunks"}, has_vec={"document_chunks"},
             vec_rows={"document_chunks": []}, kw_rows={"document_chunks": _KW_CHUNK})
    out = _search._search_chunks(db, 7, ["康拉德"], 1, 5, 4, user_id=1)
    assert spy_embed == ["康拉德"]
    assert out == _KW_CHUNK


def test_vector_hits_are_returned_without_keyword_query(spy_embed):
    db = _DB(vec_cols={"document_chunks"}, has_vec={"document_chunks"},
             vec_rows={"document_chunks": _VEC_CHUNK}, kw_rows={"document_chunks": _KW_CHUNK})
    out = _search._search_chunks(db, 7, ["康拉德"], None, 5, 4, user_id=1)
    assert out == _VEC_CHUNK
    assert not any("ilike" in q for q in db.sql)


# ── 人物卡 / 世界书 ──────────────────────────────────────────────────────────
_KW_CARD = [{"id": 3, "name": "康拉德", "score": 0.5}]
_VEC_CARD = [{"id": 4, "name": "莉娜", "score": 0.8}]
_KW_WB = [{"id": 5, "title": "仓库", "score": 0.5}]


def test_entities_no_vectors_anywhere_skip_embedding(spy_embed):
    db = _DB(vec_cols={"character_cards", "worldbook_entries"}, has_vec=set(),
             kw_rows={"character_cards": _KW_CARD, "worldbook_entries": _KW_WB})
    out = _search._search_entities(db, 7, "康拉德 仓库", chapter_max=5, user_id=1)
    assert spy_embed == []
    assert out == {"cards": _KW_CARD, "worldbook": _KW_WB}


def test_entities_only_one_table_embedded_other_falls_back(spy_embed):
    """只嵌了人物卡:人物卡走向量,世界书走关键词(此前世界书恒为空)。"""
    db = _DB(vec_cols={"character_cards", "worldbook_entries"}, has_vec={"character_cards"},
             vec_rows={"character_cards": _VEC_CARD},
             kw_rows={"character_cards": _KW_CARD, "worldbook_entries": _KW_WB})
    out = _search._search_entities(db, 7, "康拉德 仓库", chapter_max=5, user_id=1)
    assert spy_embed == ["康拉德 仓库"]
    assert out["cards"] == _VEC_CARD
    assert out["worldbook"] == _KW_WB


def test_entities_vector_empty_in_window_falls_back(spy_embed):
    db = _DB(vec_cols={"character_cards", "worldbook_entries"},
             has_vec={"character_cards", "worldbook_entries"},
             vec_rows={"character_cards": [], "worldbook_entries": []},
             kw_rows={"character_cards": _KW_CARD, "worldbook_entries": _KW_WB})
    out = _search._search_entities(db, 7, "康拉德 仓库", chapter_max=5, user_id=1)
    assert out == {"cards": _KW_CARD, "worldbook": _KW_WB}


def test_entities_desktop_without_pgvector_uses_keywords(spy_embed):
    """桌面版(jsonb 占位列):此前嵌入成功时实体层恒为空。"""
    db = _DB(vec_cols=set(), kw_rows={"character_cards": _KW_CARD, "worldbook_entries": _KW_WB})
    out = _search._search_entities(db, 7, "康拉德 仓库", chapter_max=5, user_id=1)
    assert spy_embed == []
    assert out == {"cards": _KW_CARD, "worldbook": _KW_WB}


# ── kb_nodes(统一召回新路)────────────────────────────────────────────────────
def test_recall_skips_embedding_when_kb_nodes_have_no_vectors(spy_embed):
    from kb.recall import recall

    class _RecallDB(_DB):
        def execute(self, sql, params=None):
            if "from game_saves" in sql.lower():
                return _R(one={"script_id": 7, "user_id": 1})
            return super().execute(sql, params)

    db = _RecallDB(vec_cols={"kb_nodes"}, has_vec=set())
    with mock.patch("platform_app.knowledge._search._search_chunks", lambda *a, **k: []), \
         mock.patch("kb.reveal.reveal_clause_v2", lambda *a, **k: ("true", [])):
        recall(1, "康拉德 仓库", progress_chapter=5, db=db)
    assert spy_embed == [], "kb_nodes 没有向量 → 不嵌入查询"
    assert not any("<=>" in q for q in db.sql)


# ── 存在性缓存 ────────────────────────────────────────────────────────────────
def test_presence_cache_ttl_and_invalidate():
    db = _DB(vec_cols={"document_chunks"}, has_vec=set())
    t = [100.0]
    with mock.patch.object(_search.time, "monotonic", lambda: t[0]):
        assert _search._script_has_vectors(db, 7, "document_chunks") is False
        db.has_vec = {"document_chunks"}
        t[0] += 30
        assert _search._script_has_vectors(db, 7, "document_chunks") is False, "「没有」缓存 60s"
        t[0] += 31
        assert _search._script_has_vectors(db, 7, "document_chunks") is True, "「没有」只缓存 60s"
        db.has_vec = set()
        t[0] += 200
        assert _search._script_has_vectors(db, 7, "document_chunks") is True, "「有」缓存 300s"
        _search.invalidate_script_vector_presence(7)
        assert _search._script_has_vectors(db, 7, "document_chunks") is False, "写库结束主动作废"


# ── search_canon 工具(kb_canon_entities.embedding,列名与其它表不同)──────────────
def test_canon_presence_uses_its_own_column_name():
    db = _DB()
    seen: list[tuple] = []
    orig = db.execute

    def _spy(sql, params=None):
        seen.append((" ".join(sql.split()), params))
        if "information_schema.columns" in sql:
            return _R(one={"x": 1} if params == ("kb_canon_entities", "embedding") else None)
        return orig(sql, params)

    db.execute = _spy
    assert _search._script_has_vectors(db, 7, "kb_canon_entities") is False
    assert any(p == ("kb_canon_entities", "embedding") for _s, p in seen)
    assert any("from kb_canon_entities where script_id = %s and embedding is not null" in s for s, _p in seen)


def test_search_canon_skips_embedding_when_script_has_no_canon_vectors(monkeypatch):
    from contextlib import contextmanager

    import platform_app.db as dbmod
    import platform_app.knowledge.embedding as emb
    from tools_dsl import command_tools_kb as kbt

    @contextmanager
    def _connect():
        yield _DB(vec_cols=set())

    monkeypatch.setattr(dbmod, "connect", _connect)
    monkeypatch.setattr(kbt, "_save_ctx", lambda db, sid, uid: {"script_id": 7, "progress_chapter": 3})
    monkeypatch.setattr(emb, "embed_query", lambda *a, **k: pytest.fail("没有 canon 向量不该嵌入查询"))
    out = kbt._t_search_canon(1, {"save_id": 5, "query": "康拉德"})
    assert out.startswith("检索不可用")
