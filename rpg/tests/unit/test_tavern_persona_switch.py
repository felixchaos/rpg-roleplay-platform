"""酒馆「我的角色」切换双源病根修(用户实锤:GM 编叙事没切+UI 切了面板变 GM 不变)。

三层锁:①投影纯函数 ②写穿层 SQL 形状(FK+game_saves 快照+工作树快照+snapshot_hash)
③bind 端点与新工具都走共享层(单一源);工具歧义拒执行。
"""
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TP = (ROOT / "platform_app" / "tavern_persona.py").read_text(encoding="utf-8")
RT = (ROOT / "routes" / "tavern.py").read_text(encoding="utf-8")
TT = (ROOT / "tools_dsl" / "command_tools_tavern.py").read_text(encoding="utf-8")

import sys

sys.path.insert(0, str(ROOT))
from platform_app.tavern_persona import persona_card_to_player_fields  # noqa: E402


def test_projection_four_fields_same_caliber_as_creation():
    f = persona_card_to_player_fields({"name": "杭雁菱", "identity": "工程师", "background": "b", "appearance": "a"})
    assert f == {"name": "杭雁菱", "role": "工程师", "background": "b", "appearance": "a"}
    assert persona_card_to_player_fields({})["name"] == "你"


def test_write_through_covers_all_three_layers():
    assert "tavern_persona_card_id = %s" in TP, "FK 列"
    assert TP.count("state_snapshot") >= 4, "game_saves 快照 + 工作树快照都要合并"
    assert "runtime_checkouts" in TP, "工作树=回合真相源,必须写穿"
    assert "snapshot_hash = md5" in TP, "跨 worker 缓存失效(v1.28.3 家族病防复发)"
    assert "'player'" in TP and "persona_card_id" in TP


def test_bind_endpoint_uses_shared_layer_and_memory_sync():
    i = RT.find("def api_tavern_bind_card")
    seg = RT[i:i + 4000]
    assert "apply_persona_card_to_chat" in seg, "UI bind 必须走共享写穿层"
    assert "_persist_runtime_checkpoint" in seg, "活跃档内存同步(F#94 同款)"
    assert '.setdefault("player", {}).update' in seg


def test_tool_registered_with_anti_hallucination_hint():
    assert 'name="switch_tavern_persona_card"' in TT
    assert "只在叙事里描写切换而不调用它" in TT, "描述必须点破幻觉切换(本 bug 的直接病灶)"
    assert "apply_persona_card_to_chat" in TT, "工具与 UI 同一写穿层"


def test_tool_ambiguity_refuses_to_guess():
    import tools_dsl.command_tools_tavern as m

    class _S:
        data = {"_active_save_id": 42}

    rows = [
        {"id": 1, "name": "杭雁菱", "identity": "", "role": "", "background": "", "appearance": ""},
        {"id": 2, "name": "杭雁菱(旧)", "identity": "", "role": "", "background": "", "appearance": ""},
    ]

    class _DB:
        def execute(self, sql, params=None):
            class _R:
                @staticmethod
                def fetchall():
                    return rows
            return _R()

        def commit(self):
            pass

    class _Conn:
        def __enter__(self):
            return _DB()

        def __exit__(self, *a):
            return False

    with mock.patch.object(m, "_resolve_user_id", lambda *a, **k: 7), \
         mock.patch("platform_app.db.connect", lambda: _Conn()), \
         mock.patch("platform_app.db.init_db", lambda: None):
        out = m._t_switch_tavern_persona_card(_S(), {"card": "杭雁菱(旧"})
        # 子串命中唯一 → 直接切;换个真歧义查询
        out2 = m._t_switch_tavern_persona_card(_S(), {"card": "杭"})
        assert "歧义" in out2 and "#1" in out2 and "#2" in out2
        # 精确名命中唯一
        with mock.patch("platform_app.tavern_persona.apply_persona_card_to_chat",
                        lambda db, u, c, card: {"name": card["name"], "role": "", "background": "", "appearance": ""}):
            out3 = m._t_switch_tavern_persona_card(_S(), {"card": "杭雁菱"})
            assert "已切换玩家角色卡" in out3 and "杭雁菱" in out3
