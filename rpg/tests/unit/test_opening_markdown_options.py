import unittest

from state.parsers import _extract_trailing_markdown_options


class OpeningMarkdownOptionsTest(unittest.TestCase):
    def test_extracts_trailing_bold_bullet_choices(self):
        body, options = _extract_trailing_markdown_options(
            "你在陌生的房间醒来。\n\n"
            "- **观察这间房间的细节**\n"
            "- **检查自己身上的东西**\n"
            "- **站起身，走到窗边去**\n"
        )

        self.assertEqual(body, "你在陌生的房间醒来。")
        self.assertEqual(options, ["观察这间房间的细节", "检查自己身上的东西", "站起身，走到窗边去"])

    def test_ignores_non_trailing_lists(self):
        text = "可见物品：\n- 手账\n- 书桌\n\n你决定先坐下。"

        body, options = _extract_trailing_markdown_options(text)

        self.assertEqual(body, text)
        self.assertEqual(options, [])

    def test_requires_at_least_two_choices(self):
        text = "你在门口停下。\n- **敲门**"

        body, options = _extract_trailing_markdown_options(text)

        self.assertEqual(body, text)
        self.assertEqual(options, [])


if __name__ == "__main__":
    unittest.main()
