"""acceptance A/B 改写候选功能回归(源码级 + 常量;DB/LLM e2e 在集成层)。

把「acceptance 静默重写替换」bug 改造成:节流 + 双栏 A/B 玩家裁决 + 数据采集。
锁住:节流常量、落库 helper、迁移表、选择端点的 IDOR、前端 wiring。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
FRONTEND = REPO.parent / "frontend" / "src"


def test_min_interval_constant():
    import chat_pipeline
    assert chat_pipeline._ACCEPTANCE_AB_MIN_INTERVAL == 5
    assert callable(chat_pipeline._log_acceptance_ab)
    assert callable(chat_pipeline._acceptance_ab_pref_enabled)


def test_gate_gated_on_user_pref():
    """候选生成必须受用户级开关门控(玩家可手动关)。"""
    src = (REPO / "chat_pipeline.py").read_text(encoding="utf-8")
    gate = src.split("def _acceptance_gate", 1)[1].split("# ── W1 容量优化", 1)[0]
    assert "_acceptance_ab_pref_enabled" in gate, "候选条件未接用户开关"


def test_pref_helper_defaults_true(monkeypatch):
    """无 user_id / 读库失败 → 默认开(不因偏好读取抖动而静默关掉功能)。"""
    import chat_pipeline
    assert chat_pipeline._acceptance_ab_pref_enabled(None) is True
    import platform_app.db as pdb
    monkeypatch.setattr(pdb, "connect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")), raising=False)
    assert chat_pipeline._acceptance_ab_pref_enabled(123) is True


def test_rewrite_off_critical_path():
    """严重回归防线(行者无疆:回合卡在生成中/连接超时/2-3分钟):改写候选的第二次 GM 调用
    绝不能在回合关键路径同步跑。async 路径必须【直接调 gate + inline=False】(不 to_thread 阻塞),
    改写丢后台任务 + emit 推前端。"""
    src = (REPO / "chat_pipeline.py").read_text(encoding="utf-8")
    # 后台协程存在,且用 state_event_bus.emit 推候选、用 to_thread 跑阻塞 GM
    assert "async def _gen_candidate_bg" in src
    bg = src.split("async def _gen_candidate_bg", 1)[1].split("def _acceptance_gate", 1)[0]
    assert "from state_event_bus import emit" in bg
    assert "acceptance_alt" in bg
    assert "asyncio.to_thread(_run_gm)" in bg, "后台 GM 调用应走 to_thread 不塞事件循环"
    # async 两处调用点 inline=False(直接调),sync 一处 inline=True
    assert src.count("inline=False") >= 2
    assert "inline=True" in src
    # 绝不再有 to_thread(_acceptance_gate)(那是把第二次 GM 调用塞回关键路径的老 bug)
    import re as _re
    assert not _re.search(r"to_thread\(\s*_acceptance_gate", src), "gate 不应再被 to_thread 阻塞调用"


def test_choice_swap_writes_active_commit_snapshot():
    """选改写的落库必须写【活跃 branch_commit 快照】(kb_native materialize 权威源)+ bump snapshot_hash
    (跨 worker 失效)—— 否则刷新/换 worker 回退首稿(行者无疆二次反馈的根)。"""
    src = (REPO / "routes" / "game.py").read_text(encoding="utf-8")
    fn = src.split("def _amend_history_message", 1)[1].split("\n@router", 1)[0]
    assert "update branch_commits set state_snapshot" in fn
    assert "snapshot_hash" in fn and "runtime_checkouts" in fn


def test_migration_v91_acceptance_ab_log():
    src = (REPO / "platform_app" / "db" / "migrations.py").read_text(encoding="utf-8")
    assert '(91, "acceptance_ab_log"' in src
    seg = src.split('(91, "acceptance_ab_log"', 1)[1][:1200]
    for col in ("user_id", "save_id", "turn", "unmet", "original_text", "rewrite_text", "chosen"):
        assert col in seg, f"acceptance_ab_log 缺列 {col}"


def test_choice_endpoint_registered_and_idor_guarded():
    src = (REPO / "routes" / "game.py").read_text(encoding="utf-8")
    assert '/api/acceptance/choice' in src
    ep = src.split("def api_acceptance_choice", 1)[1].split("\n@router", 1)[0]
    # IDOR:候选必须属于当前用户 + 换消息要 owns_save
    assert 'row["user_id"]' in ep and "!= uid" in ep
    assert "owns_save" in ep
    # 改写稿一律取服务端存的值,不信任前端回传正文
    assert "rewrite_text" in ep


def test_frontend_wiring():
    api = (FRONTEND / "api-client.js").read_text(encoding="utf-8")
    assert "acceptanceChoice" in api and "/acceptance/choice" in api

    gc = (FRONTEND / "entries" / "game-console.jsx").read_text(encoding="utf-8")
    assert "AcceptanceAbPanel" in gc
    assert "setRewriteAlt(null)" in gc  # 新回合清掉待选候选
    # 后台候选经长连事件总线推来:监听 rpg-acceptance_alt-updated
    assert "rpg-acceptance_alt-updated" in gc

    panel = (FRONTEND / "components" / "AcceptanceAbPanel.jsx").read_text(encoding="utf-8")
    assert "onChoose('rewrite')" in panel and "onChoose('original')" in panel

    # 游戏设置里的用户级开关(可手动关)
    app = (FRONTEND / "game-app.jsx").read_text(encoding="utf-8")
    assert "abEnabled" in app and "/api/me/preference" in app and "acceptance_ab.enabled" in app


def test_log_helper_returns_none_on_db_failure(monkeypatch):
    """DB 不可用时 _log_acceptance_ab 必须吞异常返回 None(不炸主回合)。"""
    import chat_pipeline

    def _boom(*a, **k):
        raise RuntimeError("no db")

    monkeypatch.setattr(chat_pipeline, "log", chat_pipeline.log)
    # init_db 抛错 → helper 返回 None
    import platform_app.db as pdb
    monkeypatch.setattr(pdb, "init_db", _boom, raising=False)
    assert chat_pipeline._log_acceptance_ab(1, 2, 3, ["x"], "orig", "rew") is None
