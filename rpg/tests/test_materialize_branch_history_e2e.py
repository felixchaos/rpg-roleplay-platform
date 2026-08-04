"""真库 e2e:kb_native 存档 materialize() 历史分支隔离 + 开场空 user 过滤回归。

复现根因(星之游/耀月余辉反馈):
  · messages 表按 (save_id, turn) 存、无分支维度 → 旧 materialize `where save_id` 把【所有
    分支】的消息都读出来(「新建分支又没删除老分支」);
  · 开场把空 player_input 写进 messages → 顶部一条空白玩家气泡(「新建存档顶部空白输入」)。
修复:materialize 改读【本 commit】的 state_snapshot blob(按 commit DAG 分支隔离、开场只含
  assistant);blob 缺失才回退 messages 并滤空行。

本测试在 rpg_platform dev 库的一个事务里建最小图,断言后 ROLLBACK(零污染)。
需要本机 PG (localhost:5432, rpg_platform) — 缺库则 skip。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from kb import save_kb  # noqa: E402

DSN = os.environ.get("TEST_DATABASE_URL", "host=localhost port=5432 dbname=rpg_platform")


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(DSN, row_factory=dict_row, autocommit=False)
    except Exception as exc:  # 本机无 dev 库
        pytest.skip(f"no local rpg_platform DB: {exc}")
    try:
        yield c
    finally:
        c.rollback()  # 全程零污染
        c.close()


def _mk_graph(c):
    """建 user→script→save→session + 两分支 commit(A/B)+ 跨分支/空开场 messages。"""
    uid = c.execute(
        "insert into users(username, display_name) values (%s,%s) returning id",
        ("e2e_mat_user", "e2e"),
    ).fetchone()["id"]
    sid_script = c.execute(
        "insert into scripts(owner_id, title) values (%s,%s) returning id",
        (uid, "e2e script"),
    ).fetchone()["id"]
    save_id = c.execute(
        "insert into game_saves(user_id, script_id, title, state_path) values (%s,%s,%s,%s) returning id",
        (uid, sid_script, "e2e save", ""),
    ).fetchone()["id"]
    sess_id = c.execute(
        "insert into game_sessions(user_id) values (%s) returning id", (uid,)
    ).fetchone()["id"]

    # 开场只含 assistant(routes/game.py 行为);分支 A/B 在 turn1 走向不同。
    hist_a = [
        {"role": "assistant", "content": "开场白共享"},
        {"role": "user", "content": "玩家走左边"},
        {"role": "assistant", "content": "你走进了左边的密林"},
    ]
    hist_b = [
        {"role": "assistant", "content": "开场白共享"},
        {"role": "user", "content": "玩家走右边"},
        {"role": "assistant", "content": "你登上了右边的山崖"},
    ]

    def _commit(history, hsh):
        return c.execute(
            """insert into branch_commits(save_id, object_hash, turn_index, kind, title, state_snapshot)
               values (%s,%s,%s,%s,%s,%s) returning id""",
            (save_id, hsh, len(history) // 2, "turn", "t", Jsonb({"history": history})),
        ).fetchone()["id"]

    commit_a = _commit(hist_a, "hash_a")
    commit_b = _commit(hist_b, "hash_b")
    # 无 history 的 commit(测回退 messages)
    commit_empty = c.execute(
        """insert into branch_commits(save_id, object_hash, turn_index, kind, title, state_snapshot)
           values (%s,%s,%s,%s,%s,%s) returning id""",
        (save_id, "hash_empty", 0, "turn", "t", Jsonb({})),
    ).fetchone()["id"]

    # messages:模拟旧 bug 数据 —— 开场空 user 行 + 两分支消息全挤在同 save_id(跨分支污染)。
    rows = [
        (0, "user", ""),               # 开场空 player_input(应被过滤)
        (0, "assistant", "开场白共享"),
        (1, "user", "玩家走左边"),       # 分支 A turn1
        (1, "assistant", "你走进了左边的密林"),
        (1, "user", "玩家走右边"),       # 分支 B turn1(同 turn,跨分支污染)
        (1, "assistant", "你登上了右边的山崖"),
    ]
    for turn, role, content in rows:
        c.execute(
            "insert into messages(session_id, save_id, turn, role, content) values (%s,%s,%s,%s,%s)",
            (sess_id, save_id, turn, role, content),
        )
    return save_id, commit_a, commit_b, commit_empty


def test_materialize_reads_branch_correct_history(conn):
    save_id, commit_a, commit_b, _ = _mk_graph(conn)

    state_a = save_kb.materialize(conn, save_id, commit_a)
    texts_a = [m["content"] for m in state_a["history"]]
    assert texts_a == ["开场白共享", "玩家走左边", "你走进了左边的密林"], texts_a
    assert "玩家走右边" not in texts_a, "分支 B 内容泄漏到分支 A(老分支没删)"
    assert "" not in texts_a, "开场空 user 行未被过滤(顶部空白气泡)"

    state_b = save_kb.materialize(conn, save_id, commit_b)
    texts_b = [m["content"] for m in state_b["history"]]
    assert texts_b == ["开场白共享", "玩家走右边", "你登上了右边的山崖"], texts_b
    assert "玩家走左边" not in texts_b, "分支 A 内容泄漏到分支 B"


def test_no_blank_player_bubble_at_top(conn):
    save_id, commit_a, _, _ = _mk_graph(conn)
    hist = save_kb.materialize(conn, save_id, commit_a)["history"]
    # 顶部第一条必须是开场 assistant,绝非空白 user。
    assert hist[0]["role"] == "assistant"
    assert all(str(m["content"]).strip() for m in hist), "存在空白气泡"


def test_fallback_to_messages_filters_empty(conn):
    """blob 无 history 时回退 messages,并滤掉开场空 user 行。"""
    save_id, _, _, commit_empty = _mk_graph(conn)
    hist = save_kb.materialize(conn, save_id, commit_empty)["history"]
    # 回退路径:messages 全读(跨分支),但空 user 行被过滤。
    assert all(str(m["content"]).strip() for m in hist), "回退路径未过滤空行"
    assert any(m["content"] == "开场白共享" for m in hist)
