from __future__ import annotations

import unittest

from chat_pipeline import _should_route_to_curator_clarify


class CuratorClarifyRoutingTest(unittest.TestCase):
    def test_high_confidence_question_does_not_interrupt_gm(self):
        self.assertFalse(
            _should_route_to_curator_clarify(
                confidence=0.86,
                threshold=0.5,
                clarify="你想继续交谈还是先观察？",
            )
        )

    def test_low_confidence_question_interrupts_gm(self):
        self.assertTrue(
            _should_route_to_curator_clarify(
                confidence=0.2,
                threshold=0.5,
                clarify="你想从哪里开始？(A) 村庄 (B) 地牢",
            )
        )

    def test_low_confidence_without_question_does_not_emit_empty_prompt(self):
        self.assertFalse(
            _should_route_to_curator_clarify(
                confidence=0.2,
                threshold=0.5,
                clarify="",
            )
        )


if __name__ == "__main__":
    unittest.main()
