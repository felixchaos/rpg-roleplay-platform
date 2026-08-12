"""回归:开场不得把结构化 ops JSON / 工具元叙述漏给玩家。

根因(基准 rpg/bench 首跑测出):routes/game.py 开场只抽尾部 markdown 选项,未走 chat
路径落库前那套清洗 → ```json ops 围栏被存进历史并显示给玩家。修:开场复用同一套 stripper。
本测试锁定"开场可见文本 = 三层 stripper(strip_json_state_ops→meta_preamble→leaked_scaffold)"
确实剥净 ops,而结构化解析仍能从含 ops 原文拿到 ops。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from state import (  # noqa: E402
    strip_json_state_ops,
    strip_leaked_scaffold,
    strip_meta_tool_preamble,
)


def _opening_visible(raw: str) -> str:
    # 与 routes/game.py 开场清洗同一链
    return strip_leaked_scaffold(strip_meta_tool_preamble(strip_json_state_ops(raw)))


def test_fenced_ops_removed_from_opening():
    raw = (
        "你睁开眼，发现自己置身于一座废弃的车站。月台上空无一人。\n\n"
        "```json\n[{\"op\": \"set\", \"path\": \"world.location\", \"value\": \"废弃车站\"}]\n```"
    )
    vis = _opening_visible(raw)
    assert "```json" not in vis
    assert '"op"' not in vis
    assert "废弃的车站" in vis  # 叙事正文保留


def test_bare_trailing_ops_removed():
    raw = "她转身离去，背影消失在雨幕中。\n[{\"op\":\"set\",\"path\":\"npc.她.state\",\"value\":\"离开\"}]"
    vis = _opening_visible(raw)
    assert '"op"' not in vis and "[{" not in vis
    assert "雨幕" in vis


def test_clean_opening_unchanged():
    raw = "晨光洒进房间，新的一天开始了。你深吸一口气。"
    assert _opening_visible(raw).strip() == raw.strip()
