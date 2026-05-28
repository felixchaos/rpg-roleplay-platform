"""
test_suggestions_cross_setting_filter.py — task 86 回归

用户报告（实际玩存档复盘）:
  玩家在 turn 7-9 用 /set 把剧情从『柏林扎府内宅』跳到『剧情月球时期』,
  player.current_location='月球基地·穹顶通道',世界 phase='月球风云篇'。
  但 Game Console 仍出现建议:
    "召集特殊小队,建立柏林城内侦察与撤离预案"
  原因: suggestions() 用 memory.facts/pinned/known_events 累加成 context,
  历史柏林事实(扎兹巴鲁姆/特殊小队/蛇信...)即使跨剧情后仍命中 needle,
  让建议文本含"柏林城内"的过时地理词。

修复 (task 86):
  add(score, text, *needles) 加 location-aware gate ——
  当前剧情位置 (current_location / world.time / current_phase / current_label)
  不再含柏林 token 时,跳过含"柏林"/"扎府"等明确柏林地理词的建议。
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RPG_REQUIRE_AUTH", "0")

from state import GameState, DEFAULT_STATE  # noqa: E402


def _state_moon_with_berlin_history() -> GameState:
    """模拟玩家在柏林玩了几回合,然后 /set 跳到月球——保留柏林记忆。"""
    s = GameState(copy.deepcopy(DEFAULT_STATE))
    s.data["turn"] = 11
    # 当前剧情在月球 (跨剧情跳跃后)
    s.data["player"]["current_location"] = "月球基地·穹顶通道"
    s.data["world"]["time"] = "剧情月球时期"
    s.data["world"]["timeline"]["current_label"] = "剧情月球时期"
    s.data["world"]["timeline"]["current_phase"] = "月球风云篇"
    # 但历史 memory 含柏林事实 (玩家在柏林时记下的)
    s.data["memory"]["facts"] = [
        "扎兹巴鲁姆在地下基地的态度有所松动",
        "柏林战役期间薇瑟帝国压制地联",
    ]
    s.data["memory"]["pinned"] = ["特殊小队待整编"]
    s.data["world"]["known_events"] = [
        "图卢兹战役地联溃败",
        "蛇信在外围监视过",
    ]
    s.data["relationships"] = {"蕾穆丽娜": "信任在深化", "斯雷因": "暂时合作"}
    return s


class SuggestionsFilterBerlinTextWhenSettingLeft(unittest.TestCase):
    """玩家跳到月球后,suggestions 不应再含柏林专属地理词。"""

    def test_no_berlin_geo_text_in_suggestions_on_moon(self):
        s = _state_moon_with_berlin_history()
        sugs = s.suggestions()
        blob = " | ".join(sugs)
        # 这些是含"柏林"地理词的建议,跨剧情后不该再出现
        for tok in ("柏林城内", "柏林战役", "柏林当前势力图", "柏林势力图", "扎府"):
            self.assertNotIn(
                tok, blob,
                f"task 86: 玩家已在月球,suggestions 不应出现『{tok}』;实际 {sugs!r}",
            )

    def test_non_geo_specific_needles_still_work(self):
        """非柏林地理词的 needle 仍可命中。比如"摸清基地核心机密库"
        (needle=基地/核心机密库) — 不含"柏林"地理词,即便玩家跳到月球
        (现在 location 还是"基地"),应仍能出现。"""
        s = _state_moon_with_berlin_history()
        # 让 needle 命中: memory.facts 加一条不含柏林的"核心机密库"
        s.data["memory"]["facts"].append("听说月球基地有核心机密库")
        sugs = s.suggestions()
        blob = " | ".join(sugs)
        self.assertIn(
            "核心机密库", blob,
            f"task 86: 非柏林地理词的 needle 命中后仍应保留;实际 {sugs!r}",
        )
        # 同时,这条建议本身不含柏林词
        for sug in sugs:
            if "核心机密库" in sug:
                self.assertNotIn("柏林", sug)
                self.assertNotIn("扎府", sug)

    def test_fallback_general_still_emitted(self):
        """fallback_generic 不含柏林词,应保留。"""
        s = _state_moon_with_berlin_history()
        sugs = s.suggestions()
        blob = " | ".join(sugs)
        # 通用 fallback 应至少有一条命中,或者命名 needle 满 5 条
        if len(sugs) < 5:
            generic_markers = [
                "观察当前场景", "整理当下已知情报",
                "确认下一步目标", "和关键人物单独谈话", "回顾当前剧本开场",
            ]
            self.assertTrue(
                any(m in blob for m in generic_markers),
                f"task 86: 通用 fallback 不应被错误过滤;实际 {sugs!r}",
            )


class SuggestionsKeepsBerlinWhenStillInBerlin(unittest.TestCase):
    """对照: 玩家仍在柏林剧情时,柏林专属建议仍应出现(不应被新 filter 误杀)。"""

    def test_berlin_setting_still_gets_berlin_suggestions(self):
        s = GameState(copy.deepcopy(DEFAULT_STATE))
        s.data["turn"] = 5
        s.data["player"]["current_location"] = "柏林扎府内宅"
        s.data["world"]["time"] = "柏林暗流时期"
        s.data["world"]["timeline"]["current_label"] = "柏林暗流时期"
        s.data["world"]["timeline"]["current_phase"] = "柏林暗流篇"
        s.data["memory"]["facts"] = ["扎兹巴鲁姆在地下基地的态度有所松动"]
        s.data["memory"]["pinned"] = ["特殊小队待整编"]
        s.data["world"]["known_events"] = ["图卢兹战役地联溃败", "蛇信监视"]
        sugs = s.suggestions()
        blob = " | ".join(sugs)
        # 柏林剧情下,至少一条柏林相关建议应保留
        self.assertTrue(
            any(tok in blob for tok in ("柏林", "扎府")),
            f"task 86: 玩家仍在柏林时柏林建议不该被过滤;实际 {sugs!r}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
