import unittest

from state.parsers import _parse_question


class QuestionOptionParserTest(unittest.TestCase):
    def test_inline_parenthesized_letter_options(self):
        question, options = _parse_question(
            "你想让故事如何继续？(A) 走进庭院与少女交谈 "
            "(B) 先观察周围环境再决定 "
            "(C) 尝试回忆自己是如何来到这里的 "
            "(D) 其他具体行动"
        )

        self.assertEqual(question, "你想让故事如何继续？")
        self.assertEqual(options, [
            "走进庭院与少女交谈",
            "先观察周围环境再决定",
            "尝试回忆自己是如何来到这里的",
            "其他具体行动",
        ])

    def test_existing_label_options_still_parse(self):
        question, options = _parse_question("如何行动？｜A、搜索、B、返回")

        self.assertEqual(question, "如何行动？")
        self.assertEqual(options, ["搜索", "返回"])


if __name__ == "__main__":
    unittest.main()
