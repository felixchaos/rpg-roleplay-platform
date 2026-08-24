"""test_entity_name_growth — 拆书候选人名的确定性定型(反馈 #99 真因)。

老口径:候选池 = 正文里 2–3 字中文 n-gram 的词频 top50。两个硬伤(生产实测):
  1. **4 字以上的名字永远进不了候选池** —— 「猛虎教练」只能以「猛虎教」「虎教练」
     的截断形式出现,真名一张卡都生成不了;
  2. 截断影子还会各自成卡 —— script 321 线上同时躺着「猛虎教」和「猛虎教练」两张 NPC 卡
     (正文出现次数 605 / 604,差 1 —— 前者根本不独立存在)。

修法是确定性的:统计候选每次出现时紧邻的字,某个字占比压倒性(≥_SHADOW_RATIO)就说明
候选只是更长名字的一截 → 吞掉它继续长;长完再按子串关系折叠影子。
"""
from __future__ import annotations

import unittest

from platform_app.import_pipeline.stages_core import (
    _collapse_name_shadows,
    _grow_name,
    _is_particle_fragment,
)


class GrowName(unittest.TestCase):
    def test_grows_truncated_ngram_to_full_name(self):
        text = "猛虎教练大吼。猛虎教练又吼。许荣泰看着猛虎教练,猛虎教练不理他。"
        self.assertEqual(_grow_name(text, "猛虎教")[0], "猛虎教练")

    def test_grows_leftward_too(self):
        # 「荣泰」几乎总跟在「许」后面 → 应长成「许荣泰」
        text = "许荣泰跑了。许荣泰喘气。教练骂许荣泰。许荣泰不服。"
        self.assertEqual(_grow_name(text, "荣泰")[0], "许荣泰")

    def test_independent_name_is_left_alone(self):
        # 「郑吒」后面跟什么都有 → 没有压倒性邻字,不该被乱长
        text = "郑吒说。郑吒走了。郑吒的刀。郑吒和楚轩。郑吒笑。郑吒盯着。"
        self.assertEqual(_grow_name(text, "郑吒")[0], "郑吒")

    def test_stops_at_max_len(self):
        text = "暗夜观察者出现了。暗夜观察者又发消息。李刚看见暗夜观察者。暗夜观察者消失。"
        name, _ = _grow_name(text, "暗夜观")
        self.assertLessEqual(len(name), 6)
        self.assertEqual(name, "暗夜观察者")

    def test_does_not_swallow_following_verb(self):
        # 「郑吒」后面几乎总是「说」——但动词不能吞进名字,否则卡名变成「郑吒说」
        text = "郑吒说。郑吒说。郑吒说。郑吒说。郑吒说。"
        self.assertEqual(_grow_name(text, "郑吒")[0], "郑吒")

    def test_periodic_repetition_does_not_overgrow(self):
        # 周期性重复文本:长到「暗夜观察者」后下一个字恒为它自己的首字 → 必须停
        text = "暗夜观察者" * 20
        self.assertEqual(_grow_name(text, "暗夜观")[0], "暗夜观察者")

    def test_too_few_samples_not_grown(self):
        # 样本 < 3 次不做扩展判断(噪声上乱长会造出假名)
        text = "阿甲乙丙丁"
        self.assertEqual(_grow_name(text, "甲乙")[0], "甲乙")

    def test_returns_real_count_of_final_name(self):
        text = "猛虎教练" * 7
        name, count = _grow_name(text, "猛虎教")
        self.assertEqual(name, "猛虎教练")
        self.assertEqual(count, 7)


class CollapseShadows(unittest.TestCase):
    def test_drops_prefix_shadow_with_near_equal_count(self):
        items = [{"name": "猛虎教", "count": 605}, {"name": "猛虎教练", "count": 604}]
        kept = [x["name"] for x in _collapse_name_shadows(items)]
        self.assertEqual(kept, ["猛虎教练"], "截断影子必须被长名吸收,否则同一个人两张卡")

    def test_keeps_genuinely_independent_short_name(self):
        # 「李威」不是「李威廉」的影子:自己出现得多得多
        items = [{"name": "李威", "count": 300}, {"name": "李威廉", "count": 12}]
        kept = sorted(x["name"] for x in _collapse_name_shadows(items))
        self.assertEqual(kept, ["李威", "李威廉"])

    def test_unrelated_names_all_kept(self):
        items = [{"name": "郑吒", "count": 90}, {"name": "楚轩", "count": 80},
                 {"name": "詹岚", "count": 40}]
        self.assertEqual(len(_collapse_name_shadows(items)), 3)


class ParticleFragments(unittest.TestCase):
    def test_rejects_edge_particles(self):
        for frag in ("教练的", "的说", "着教练", "了一个", "这家伙的", "有德的"):
            self.assertTrue(_is_particle_fragment(frag), frag)

    def test_keeps_real_names(self):
        for name in ("猛虎教练", "许荣泰", "郑吒", "楚轩", "暗夜观察者", "欧老师"):
            self.assertFalse(_is_particle_fragment(name), name)

    def test_particle_in_middle_is_not_rejected(self):
        # 只判首尾 —— 名字中间含虚词的(不知火/花不弃)不能误伤
        self.assertFalse(_is_particle_fragment("花不弃"))
        self.assertFalse(_is_particle_fragment("不知火"))


if __name__ == "__main__":
    unittest.main()
