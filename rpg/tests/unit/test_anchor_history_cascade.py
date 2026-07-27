"""test_anchor_history_cascade.py — 锚点脱离 pending → 自动补写历史锚点(反向级联)。

背景:`record_history_anchor` 早有**正向**级联(带 linked_pending_anchors 写历史 → 同事务把
对应 pending 标 satisfied),反向一直空着。生产实证:883 回合的存档 `mark_anchor_*` 真调了
90 次,`record_history_anchor` 0 次;全站 1140 次 vs 10 条 —— GM 会用够得着的工具,但不会自己
想起留档。所以留档不能挂提示词,必须在 mark 成功的确定性缝上补。

本文件锁三件:importance 映射、两条 mark 路径都真的接了级联、级联的去重与失败隔离。
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

from tools_dsl import command_tools_anchors as A

_SRC = pathlib.Path(A.__file__).read_text(encoding="utf-8")


def _func_src(name: str) -> str:
    i = _SRC.index(f"def {name}(")
    j = _SRC.find("\ndef ", i + 1)
    return _SRC[i: j if j != -1 else len(_SRC)]


class _FakeCur:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDB:
    """只认去重查询的假连接。execute 返回预置 row。"""

    def __init__(self, dup_row):
        self.dup_row = dup_row
        self.queries: list[str] = []

    def execute(self, sql, params=None):
        self.queries.append(sql)
        return _FakeCur(self.dup_row)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def patched(monkeypatch):
    """替掉 connect/init_db/record_history_anchor,拿到级联的真实写入参数。"""
    written: list[dict] = []

    def _install(dup_row):
        import platform_app.db as _db
        import agents.save_history as _sh
        monkeypatch.setattr(_db, "init_db", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(_db, "connect", lambda *a, **k: _FakeDB(dup_row), raising=False)

        def _rec(save_id, **kw):
            written.append({"save_id": save_id, **kw})
            return {"ok": True, "id": 1, "turn_occurred": kw.get("turn_occurred") or 0,
                    "importance": kw.get("importance")}

        monkeypatch.setattr(_sh, "record_history_anchor", _rec, raising=False)
        return written

    return _install


# ── importance 映射 ──────────────────────────────────────────────────────
@pytest.mark.parametrize("status,expected", [
    ("superseded", 80),   # 原著走向被改写
    ("variant", 70),      # 发生了但走样
    ("occurred", 60),     # 基本照原著(仍达文档建议的留档线 60)
    ("weird", 60),        # 未知状态退化到留档线,不会低于阈值被后续过滤吃掉
])
def test_cascade_importance(status, expected):
    assert A._cascade_importance(status) == expected


def test_importance_never_below_record_threshold():
    """全部取值 ≥60 —— record_history_anchor 文档串的「建议 60 起留档」线。"""
    assert min(A._CASCADE_IMPORTANCE.values()) >= 60


# ── 两条 mark 路径都接了级联,且在连接之外调 ────────────────────────────────
@pytest.mark.parametrize("fn", ["_t_mark_anchor_satisfied", "_t_mark_anchor_superseded"])
def test_both_mark_paths_wire_the_cascade(fn):
    body = _func_src(fn)
    assert "_cascade_history_from_anchor(" in body, f"{fn} 没接反向级联"


@pytest.mark.parametrize("fn", ["_t_mark_anchor_satisfied", "_t_mark_anchor_superseded"])
def test_cascade_is_called_outside_the_open_connection(fn):
    """级联自己开连接,嵌在已持连接的 `with` 里会在 PgBouncer 上叠连接(本仓有前科)。

    判据:调用行缩进必须 ≤ `with ... connect() as db:` 那行的缩进(即已退出该块)。
    """
    body = _func_src(fn)
    with_line = next(l for l in body.splitlines() if "connect() as db:" in l)
    call_line = next(l for l in body.splitlines() if "_cascade_history_from_anchor(" in l)
    ind = lambda s: len(s) - len(s.lstrip())  # noqa: E731
    assert ind(call_line) <= ind(with_line), f"{fn} 的级联调用还在 connect() 块里"


def test_superseded_selects_anchor_key():
    """superseded 分支原本没 select anchor_key,不补上级联就永远拿不到 key、静默跳过。"""
    body = _func_src("_t_mark_anchor_superseded")
    for sel in re.findall(r'"select id, status[^"]*"', body):
        assert "anchor_key" in sel, f"superseded 的 SELECT 漏了 anchor_key: {sel}"


# ── 行为:去重 / 正常写入 / 缺 key 跳过 ──────────────────────────────────────
def test_cascade_writes_history_anchor(patched):
    written = patched(dup_row=None)  # 无既有关联 → 应写
    A._cascade_history_from_anchor(
        7, anchor_key="ch12_meet", anchor_summary="主角与B初遇",
        new_status="variant", detail="提前了两章,在码头而非车站", turn_occurred=33)
    assert len(written) == 1
    w = written[0]
    assert w["save_id"] == 7
    assert w["importance"] == 70
    assert w["turn_occurred"] == 33
    assert w["linked_pending_anchors"] == ["ch12_meet"]
    assert w["source"] == "gm_generated"  # 面板的「GM 写 N」要如实反映,不另立第三种 source
    assert w["metadata"]["via"] == "anchor_cascade"
    assert "ch12_meet" in w["summary"] and "码头" in w["summary"]


def test_cascade_skips_when_anchor_already_linked(patched):
    """GM 已手动 record_history_anchor 并填了 linked_pending_anchors → 不双写。"""
    written = patched(dup_row={"?column?": 1})
    A._cascade_history_from_anchor(
        7, anchor_key="ch12_meet", anchor_summary="x", new_status="occurred", detail="y")
    assert written == []


def test_cascade_noop_without_anchor_key(patched):
    written = patched(dup_row=None)
    A._cascade_history_from_anchor(7, anchor_key="  ", anchor_summary="x",
                                   new_status="occurred", detail="y")
    assert written == []


def test_cascade_swallows_errors(monkeypatch):
    """级联炸了绝不能把 mark 本身带崩。"""
    import platform_app.db as _db
    monkeypatch.setattr(_db, "init_db", lambda *a, **k: None, raising=False)

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(_db, "connect", _boom, raising=False)
    A._cascade_history_from_anchor(7, anchor_key="k", anchor_summary="s",
                                   new_status="occurred", detail="d")  # 不抛即通过


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
