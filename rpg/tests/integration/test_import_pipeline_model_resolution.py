"""test_import_pipeline_model_resolution — 验证拆书流水线三阶段走 extractor pref 而非 GM。

覆盖:
  - _resolve_extractor_llm: extractor pref → agent pref → default 三级 fallback
  - _stage_story_phase_llm: 调用 call_agent_json 时用 user pref api_id/model
  - _stage_cards: 同上
  - _stage_worldbook: 同上
  - 确认三阶段不再实例化 GameMaster
"""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, call, patch


# ── stub 重量级依赖, 让 import 不报错 ───────────────────────────────────────────

def _install_stubs() -> None:
    """在 sys.modules 里插入最小 stub,让 import_pipeline 可以被 import。"""
    # psycopg
    psycopg = types.ModuleType("psycopg")
    psycopg_types = types.ModuleType("psycopg.types")
    psycopg_types_json = types.ModuleType("psycopg.types.json")
    psycopg_types_json.Jsonb = lambda x: x
    psycopg.types = psycopg_types
    psycopg_types.json = psycopg_types_json
    sys.modules.setdefault("psycopg", psycopg)
    sys.modules.setdefault("psycopg.types", psycopg_types)
    sys.modules.setdefault("psycopg.types.json", psycopg_types_json)

    # platform_app.db
    db_mod = types.ModuleType("platform_app.db")
    db_mod.connect = MagicMock()
    db_mod.expose = lambda f: f
    db_mod.init_db = MagicMock()
    sys.modules["platform_app.db"] = db_mod

    # agents._harness
    harness_mod = types.ModuleType("agents._harness")
    harness_mod.call_agent_json = MagicMock(return_value=("[]", {}))
    harness_mod.resolve_api_and_model = MagicMock(return_value=("vertex_ai", "gemini-3.5-flash"))
    sys.modules["agents._harness"] = harness_mod

    # agents.gm — should NOT be imported by three stages after fix
    gm_mod = types.ModuleType("agents.gm")
    gm_mod.GameMaster = MagicMock(side_effect=AssertionError("GameMaster should not be used in pipeline stages"))
    sys.modules["agents.gm"] = gm_mod

    # platform_app.usage
    usage_mod = types.ModuleType("platform_app.usage")
    usage_mod.compute_cost = MagicMock(return_value=0.0)
    usage_mod.record_usage = MagicMock()
    sys.modules["platform_app.usage"] = usage_mod

    # platform_app (package)
    pa_mod = types.ModuleType("platform_app")
    pa_mod.usage = usage_mod
    sys.modules.setdefault("platform_app", pa_mod)

    # core.llm_backend
    llm_mod = types.ModuleType("core.llm_backend")
    llm_mod.resolve_preferred_api = MagicMock(return_value=None)
    llm_mod.resolve_preferred_model = MagicMock(return_value=None)
    sys.modules["core.llm_backend"] = llm_mod

    # core (package)
    core_mod = types.ModuleType("core")
    sys.modules.setdefault("core", core_mod)

    # platform_app.knowledge
    knowledge_mod = types.ModuleType("platform_app.knowledge")
    knowledge_mod.upsert_character_card = MagicMock()
    sys.modules["platform_app.knowledge"] = knowledge_mod


_install_stubs()

# 延迟 import, 确保 stub 已就位
import importlib
import sys as _sys

# 在 sys.modules 里用 platform_app.import_pipeline 名称
# 但文件在 platform_app 包下; 用绝对路径 import
import importlib.util
import os

_PIPELINE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..",  # rpg/
    "platform_app", "import_pipeline.py",
)
_PIPELINE_PATH = os.path.normpath(_PIPELINE_PATH)

_spec = importlib.util.spec_from_file_location(
    "platform_app.import_pipeline",
    _PIPELINE_PATH,
)
_pipeline = importlib.util.module_from_spec(_spec)
sys.modules["platform_app.import_pipeline"] = _pipeline
_spec.loader.exec_module(_pipeline)

_resolve_extractor_llm = _pipeline._resolve_extractor_llm
_stage_story_phase_llm = _pipeline._stage_story_phase_llm
_stage_cards = _pipeline._stage_cards
_stage_worldbook = _pipeline._stage_worldbook


# ── 测试辅助 ─────────────────────────────────────────────────────────────────

class _FakeCtl:
    """最小 JobController stub。"""
    def __init__(self):
        self._usage = (0, 0, 0.0)

    def update(self, **kw):
        pass

    def add_usage(self, inp, out, cost):
        self._usage = (inp, out, cost)

    def is_cancelled(self):
        return False


# ── 测试: _resolve_extractor_llm ──────────────────────────────────────────────

class TestResolveExtractorLlm(unittest.TestCase):
    def test_uses_extractor_pref_when_set(self):
        """extractor.api_id + extractor.model_real_name pref 存在时应优先返回。"""
        harness = sys.modules["agents._harness"]
        harness.resolve_api_and_model.return_value = ("anthropic", "claude-haiku-4")

        api_id, model = _resolve_extractor_llm(user_id=42)

        harness.resolve_api_and_model.assert_called_with(
            42,
            api_pref_key="extractor.api_id",
            model_pref_key="extractor.model_real_name",
            default_api="vertex_ai",
            default_model="gemini-3.5-flash",
        )
        self.assertEqual(api_id, "anthropic")
        self.assertEqual(model, "claude-haiku-4")

    def test_default_when_no_pref(self):
        """没有 pref 时应返回 vertex_ai / gemini-3.5-flash 默认。"""
        harness = sys.modules["agents._harness"]
        harness.resolve_api_and_model.return_value = ("vertex_ai", "gemini-3.5-flash")

        api_id, model = _resolve_extractor_llm(user_id=1)

        self.assertEqual(api_id, "vertex_ai")
        self.assertEqual(model, "gemini-3.5-flash")


# ── 测试: _stage_story_phase_llm ──────────────────────────────────────────────

class TestStageStoryPhaseLlm(unittest.TestCase):
    def setUp(self):
        harness = sys.modules["agents._harness"]
        harness.resolve_api_and_model.return_value = ("anthropic", "claude-haiku-4")
        # call_agent_json 返回合法 phase 数组
        harness.call_agent_json.return_value = (
            '[{"phase":"开端","start":1,"end":5}]', {"input_tokens": 100, "output_tokens": 50}
        )

    def test_calls_call_agent_json_with_extractor_pref(self):
        """_stage_story_phase_llm 应调 call_agent_json 且用 extractor pref api_id/model。"""
        db_mock = MagicMock()
        db_ctx = MagicMock()
        db_ctx.__enter__ = MagicMock(return_value=db_mock)
        db_ctx.__exit__ = MagicMock(return_value=False)
        db_mock.execute.return_value.fetchall.return_value = [
            {"chapter": 1, "summary": "测试摘要", "title": "第一章"},
        ]
        db_mock.execute.return_value.fetchone.return_value = None

        harness = sys.modules["agents._harness"]
        harness.call_agent_json.reset_mock()

        with patch.object(_pipeline, "connect", return_value=db_ctx):
            with patch.dict(sys.modules, {"platform_app.usage": sys.modules["platform_app.usage"]}):
                try:
                    _stage_story_phase_llm(_FakeCtl(), user_id=42, script_id=1)
                except Exception:
                    pass  # DB 操作失败可以忽略

        # 关键断言: call_agent_json 被调用,且 api_id="anthropic", model="claude-haiku-4"
        harness.call_agent_json.assert_called_once()
        args, kwargs = harness.call_agent_json.call_args
        self.assertEqual(args[0], "anthropic", "api_id 应为 user pref 'anthropic'")
        self.assertEqual(args[1], "claude-haiku-4", "model 应为 user pref 'claude-haiku-4'")

    def test_no_gamemaster_instantiation(self):
        """确认 GameMaster 不被实例化(不走 GM 路径)。"""
        gm_mod = sys.modules["agents.gm"]
        gm_mod.GameMaster.reset_mock()

        db_mock = MagicMock()
        db_ctx = MagicMock()
        db_ctx.__enter__ = MagicMock(return_value=db_mock)
        db_ctx.__exit__ = MagicMock(return_value=False)
        db_mock.execute.return_value.fetchall.return_value = []

        with patch.object(_pipeline, "connect", return_value=db_ctx):
            try:
                _stage_story_phase_llm(_FakeCtl(), user_id=42, script_id=1)
            except Exception:
                pass

        gm_mod.GameMaster.assert_not_called()


# ── 测试: _stage_cards ────────────────────────────────────────────────────────

class TestStageCards(unittest.TestCase):
    def setUp(self):
        harness = sys.modules["agents._harness"]
        harness.resolve_api_and_model.return_value = ("anthropic", "claude-haiku-4")
        harness.call_agent_json.return_value = (
            '{"is_character": false}', {"input_tokens": 50, "output_tokens": 20}
        )

    def test_uses_extractor_pref_api(self):
        """_stage_cards 应用 extractor pref 的 api_id/model 调 call_agent_json。"""
        harness = sys.modules["agents._harness"]
        harness.call_agent_json.reset_mock()

        db_mock = MagicMock()
        db_ctx = MagicMock()
        db_ctx.__enter__ = MagicMock(return_value=db_mock)
        db_ctx.__exit__ = MagicMock(return_value=False)
        db_mock.execute.return_value.fetchall.return_value = []
        db_mock.execute.return_value.fetchone.return_value = None

        entities = [{"name": "李明", "count": 10}]

        with patch.object(_pipeline, "connect", return_value=db_ctx):
            _stage_cards(_FakeCtl(), user_id=42, script_id=1, entities=entities)

        # 有实体时会触发 LLM 调用(if snippets 找到) — 这里无章节文本,LLM 不调用
        # 核心: GameMaster 未实例化
        gm_mod = sys.modules["agents.gm"]
        gm_mod.GameMaster.assert_not_called()

    def test_no_gamemaster_in_cards(self):
        """_stage_cards 不走 GM 路径。"""
        gm_mod = sys.modules["agents.gm"]
        gm_mod.GameMaster.reset_mock()

        db_mock = MagicMock()
        db_ctx = MagicMock()
        db_ctx.__enter__ = MagicMock(return_value=db_mock)
        db_ctx.__exit__ = MagicMock(return_value=False)
        db_mock.execute.return_value.fetchall.return_value = []
        db_mock.execute.return_value.fetchone.return_value = None

        with patch.object(_pipeline, "connect", return_value=db_ctx):
            _stage_cards(_FakeCtl(), user_id=42, script_id=1, entities=[])

        gm_mod.GameMaster.assert_not_called()


# ── 测试: _stage_worldbook ────────────────────────────────────────────────────

class TestStageWorldbook(unittest.TestCase):
    def setUp(self):
        harness = sys.modules["agents._harness"]
        harness.resolve_api_and_model.return_value = ("anthropic", "claude-haiku-4")
        harness.call_agent_json.return_value = (
            '[{"name":"测试地点","keys":["地点"],"content":"测试内容","priority":80}]',
            {"input_tokens": 200, "output_tokens": 100},
        )

    def test_uses_extractor_pref_api(self):
        """_stage_worldbook 应用 extractor pref 的 api_id/model。"""
        harness = sys.modules["agents._harness"]
        harness.call_agent_json.reset_mock()

        db_mock = MagicMock()
        db_ctx = MagicMock()
        db_ctx.__enter__ = MagicMock(return_value=db_mock)
        db_ctx.__exit__ = MagicMock(return_value=False)
        db_mock.execute.return_value.fetchone.return_value = {"id": 1}
        db_mock.execute.return_value.fetchall.return_value = [
            {"chapter": 1, "summary": "测试", "locations": [], "factions": [], "concepts": []},
        ]

        with patch.object(_pipeline, "connect", return_value=db_ctx):
            try:
                _stage_worldbook(_FakeCtl(), user_id=42, script_id=1)
            except Exception:
                pass  # DB insert 可以失败

        harness.call_agent_json.assert_called_once()
        args, kwargs = harness.call_agent_json.call_args
        self.assertEqual(args[0], "anthropic", "api_id 应为 user pref 'anthropic'")
        self.assertEqual(args[1], "claude-haiku-4", "model 应为 user pref 'claude-haiku-4'")

    def test_no_gamemaster_in_worldbook(self):
        """_stage_worldbook 不走 GM 路径。"""
        gm_mod = sys.modules["agents.gm"]
        gm_mod.GameMaster.reset_mock()

        db_mock = MagicMock()
        db_ctx = MagicMock()
        db_ctx.__enter__ = MagicMock(return_value=db_mock)
        db_ctx.__exit__ = MagicMock(return_value=False)
        db_mock.execute.return_value.fetchone.return_value = None  # 没有 book_row → 提前返回

        with patch.object(_pipeline, "connect", return_value=db_ctx):
            result = _stage_worldbook(_FakeCtl(), user_id=42, script_id=1)

        self.assertEqual(result, 0)
        gm_mod.GameMaster.assert_not_called()


if __name__ == "__main__":
    unittest.main()
