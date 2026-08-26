"""test_context_layer_budget_registry.py — 层预算登记表的 AST 守卫。

背景(同一个病,已经犯了至少 8 次):
  `build_context_bundle` 用 `MAX_LAYER_CHARS.get(layer_id, 1800)` 取每层上限。层 id 没进
  这张表**不会报错**,只会被砍到 1800 字符(≈900 token),而症状(「GM 不记得」「世界书像
  摆设」「角色卡不全」)与「被截断」没有任何字面关联,每次都是几个月后靠玩家反馈找出来的。

  已知受害者:novel_retrieval(真正的小说正文 RAG)、novel_worldbook、timeline_pending、
  酒馆四层(card_system / character / persona / module_scene),以及 v1.82.0 由本守卫扫出的
  episodic_recall(长程历史召回!)、world_pulse、npc_agenda、consequence_echo。

本文件是那条「miss 必须可诊断」的闸:静态扫出所有 make_layer/_layer 的字面量 id,
要求它们全部在登记表里。动态生成的 id 绕得过本守卫,但绕不过 build_context_bundle 里
的 _warn_unregistered_layers(会 warn 并写进 debug.budget.unregistered_layers)。
"""
from __future__ import annotations

import ast
from pathlib import Path

from context_engine._constants import MAX_LAYER_CHARS

_RPG_ROOT = Path(__file__).resolve().parents[2]
_SCAN_DIRS = ("context_providers", "context_engine")
_LAYER_FACTORIES = {"make_layer", "_layer"}


def _static_layer_ids() -> dict[str, str]:
    """{layer_id: "文件:行号"} —— 源码里所有字面量层 id。"""
    found: dict[str, str] = {}
    for d in _SCAN_DIRS:
        for path in sorted((_RPG_ROOT / d).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (
                    fn.id if isinstance(fn, ast.Name) else "")
                if name not in _LAYER_FACTORIES:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    found.setdefault(first.value, f"{path.relative_to(_RPG_ROOT)}:{node.lineno}")
    return found


def test_scanner_actually_finds_layers():
    """守卫自检:扫不到东西的守卫是永远绿的假守卫。"""
    ids = _static_layer_ids()
    assert len(ids) >= 20, f"只扫到 {len(ids)} 个层 id,扫描器可能坏了"
    assert "user_input" in ids and "novel_retrieval" in ids


def test_every_static_layer_id_has_a_budget():
    ids = _static_layer_ids()
    missing = {k: v for k, v in ids.items() if k not in MAX_LAYER_CHARS}
    assert not missing, (
        "这些层 id 没进 context_engine/_constants.py 的 _BASE_LAYER_CHARS,"
        "会被静默截断到默认上限:\n  "
        + "\n  ".join(f"{k}  ({v})" for k, v in sorted(missing.items())))


def test_previously_lost_layers_are_registered():
    """回归锁:这批是历史上真的丢过的,别再被谁顺手删掉登记。"""
    for lid in ("novel_retrieval", "novel_worldbook", "timeline_pending",
                "tavern_card_system", "tavern_character", "tavern_persona",
                "module_scene", "module_encounter",
                "episodic_recall", "world_pulse", "npc_agenda", "consequence_echo"):
        assert lid in MAX_LAYER_CHARS, f"{lid} 的层预算登记被删了"


def test_episodic_recall_budget_is_not_a_token_stub():
    """长程历史召回是长局记忆的主通道,给它 1800 字符等于「召回了但塞不进去」。"""
    from context_engine._constants import DEFAULT_LAYER_CHARS
    assert MAX_LAYER_CHARS["episodic_recall"] > DEFAULT_LAYER_CHARS * 3
