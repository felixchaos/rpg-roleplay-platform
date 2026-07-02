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
    assert "on_acceptance_alt" in gc
    assert "AcceptanceAbPanel" in gc
    assert "setRewriteAlt(null)" in gc  # 新回合清掉待选候选

    panel = (FRONTEND / "components" / "AcceptanceAbPanel.jsx").read_text(encoding="utf-8")
    assert "onChoose('rewrite')" in panel and "onChoose('original')" in panel


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
