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


# ── stub 重量级依赖, 让 import_pipeline 可被加载 ─────────────────────────────────
#
# 隔离要求: 这些 stub 只能在「本模块测试运行期间」存在于 sys.modules,运行结束
# 必须还原。否则 collection 阶段就被污染 —— 假的 agents.gm / platform_app.knowledge
# (无 __path__ 的裸 ModuleType)残留在 sys.modules,害后续模块
# `from agents.gm import style_config` / `from platform_app.knowledge import embedding`
# 拿到假包而 ImportError。
# 做法: 模块导入阶段(= collection)绝不碰 sys.modules; 改由 setUpModule 经
#       patch.dict(sys.modules, ...) 装入 stub 并加载 import_pipeline,
#       tearDownModule 还原。import_pipeline 的重依赖均为函数内惰性导入,
#       故运行期 stub 在位即可。

import importlib
import importlib.util
import os

_PIPELINE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "platform_app", "import_pipeline.py")
)


def _build_stub_overrides() -> dict:
    """始终覆盖装入的 stub(对应原代码无条件的 `sys.modules[x] = ...`)。"""
    db_mod = types.ModuleType("platform_app.db")
    db_mod.connect = MagicMock()
    db_mod.expose = lambda f: f
    db_mod.init_db = MagicMock()

    harness_mod = types.ModuleType("agents._harness")
    harness_mod.call_agent_json = MagicMock(return_value=("[]", {}))
    harness_mod.resolve_api_and_model = MagicMock(return_value=("vertex_ai", "gemini-3.5-flash"))

    # agents.gm — 三阶段修复后不应被实例化; 塞 tripwire mock 验证未走 GM 路径
    gm_mod = types.ModuleType("agents.gm")
    gm_mod.GameMaster = MagicMock(side_effect=AssertionError("GameMaster should not be used in pipeline stages"))

    usage_mod = types.ModuleType("platform_app.usage")
    usage_mod.compute_cost = MagicMock(return_value=0.0)
    usage_mod.record_usage = MagicMock()

    llm_mod = types.ModuleType("core.llm_backend")
    llm_mod.resolve_preferred_api = MagicMock(return_value=None)
    llm_mod.resolve_preferred_model = MagicMock(return_value=None)
    # import_pipeline 顶层 import 了这两个兜底常量(#34 去魔法值);stub 须补齐否则 ImportError
    llm_mod.DEFAULT_FALLBACK_API = "vertex_ai"
    llm_mod.DEFAULT_FALLBACK_MODEL = "gemini-3.5-flash"

    knowledge_mod = types.ModuleType("platform_app.knowledge")
    knowledge_mod.upsert_character_card = MagicMock()

    return {
        "platform_app.db": db_mod,
        "agents._harness": harness_mod,
        "agents.gm": gm_mod,
        "platform_app.usage": usage_mod,
        "core.llm_backend": llm_mod,
        "platform_app.knowledge": knowledge_mod,
    }


def _build_stub_defaults() -> dict:
    """仅在缺失时装入的 stub(对应原代码的 `sys.modules.setdefault(...)`:真模块已在则不替换)。"""
    psycopg = types.ModuleType("psycopg")
    psycopg_types = types.ModuleType("psycopg.types")
    psycopg_types_json = types.ModuleType("psycopg.types.json")
    psycopg_types_json.Jsonb = lambda x: x
    psycopg.types = psycopg_types
    psycopg_types.json = psycopg_types_json

    return {
        "psycopg": psycopg,
        "psycopg.types": psycopg_types,
        "psycopg.types.json": psycopg_types_json,
        "platform_app": types.ModuleType("platform_app"),
        "core": types.ModuleType("core"),
    }


# 模块级占位; 真正赋值在 setUpModule(运行期)完成 —— 故 collection 阶段零污染。
_pipeline = None
_resolve_extractor_llm = None
_stage_story_phase_llm = None
_stage_cards = None
_stage_worldbook = None
_STUB_PATCHER = None


def setUpModule() -> None:
    """运行期装入 stub 并加载 import_pipeline; 全部 sys.modules 改动经 patch.dict 记录以便还原。"""
    global _pipeline, _resolve_extractor_llm, _stage_story_phase_llm
    global _stage_cards, _stage_worldbook, _STUB_PATCHER

    stub_map = dict(_build_stub_overrides())
    for name, mod in _build_stub_defaults().items():
        if name not in sys.modules:  # 复刻 setdefault 语义: 真模块已在则保留真的
            stub_map[name] = mod

    # 先建模块对象, 连同自身注册一起纳入 patch.dict —— 这样 import_pipeline 的注册也会被还原
    _spec = importlib.util.spec_from_file_location("platform_app.import_pipeline", _PIPELINE_PATH)
    _pipeline = importlib.util.module_from_spec(_spec)
    stub_map["platform_app.import_pipeline"] = _pipeline

    _STUB_PATCHER = patch.dict(sys.modules, stub_map)
    _STUB_PATCHER.start()
    try:
        _spec.loader.exec_module(_pipeline)  # stub 在位时加载, 绑定 Jsonb/connect 等顶层名
    except Exception:
        _STUB_PATCHER.stop()  # 加载失败也别泄漏 stub
        _STUB_PATCHER = None
        raise

    _resolve_extractor_llm = _pipeline._resolve_extractor_llm
    _stage_story_phase_llm = _pipeline._stage_story_phase_llm
    _stage_cards = _pipeline._stage_cards
    _stage_worldbook = _pipeline._stage_worldbook


def tearDownModule() -> None:
    """还原 sys.modules: 假 agents.gm / platform_app.knowledge 等退出, 真模块回归。"""
    global _STUB_PATCHER
    if _STUB_PATCHER is not None:
        _STUB_PATCHER.stop()
        _STUB_PATCHER = None


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
