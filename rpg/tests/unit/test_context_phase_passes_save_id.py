"""run_context_phase 必须把 save_id 透传给 run_context_agent,否则 ProviderServices.save_id
恒为 None → RuntimePhaseDigestProvider(本存档历史摘要)+ 锚点 NPC 强制登场
(_extract_anchor_npc_names)永远 skipped(task 107E 整条 provider 死代码)。"""
import re
import unittest
from pathlib import Path

SRC = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((Path(__file__).resolve().parents[2] / "chat_pipeline").glob("*.py")))


class ContextPhasePassesSaveId(unittest.TestCase):
    def test_rca_call_passes_save_id(self):
        # 定位 run_context_phase 里桥接 _rca 的调用块
        i = SRC.find("def run_context_phase(")
        self.assertNotEqual(i, -1, "找不到 run_context_phase")
        end = SRC.find("\nasync def ", i + 1)
        if end == -1:
            end = SRC.find("\ndef ", i + 1)
        body = SRC[i:end]
        # 桥接调用必须透传 save_id(否则 provider 恒 skipped)
        self.assertIn("_bridge_sync_generator_to_async(", body)
        self.assertTrue(
            re.search(r"save_id\s*=\s*ctx\.early_active_save_id", body),
            "run_context_phase 的 _rca 调用未透传 save_id=ctx.early_active_save_id",
        )

    def test_run_context_agent_accepts_save_id(self):
        ca = (Path(__file__).resolve().parents[2] / "agents" / "context_agent.py").read_text(encoding="utf-8")
        i = ca.find("def run_context_agent(")
        sig = ca[i:ca.find(")", i)]
        self.assertIn("save_id", sig, "run_context_agent 签名缺 save_id 形参")


if __name__ == "__main__":
    unittest.main()
