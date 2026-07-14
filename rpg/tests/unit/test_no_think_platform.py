"""平台侧结构化 JSON 微任务禁深思 + 空正文护栏接线回归(268 实锤族,2026-07-10)。

思考类模型跑结构化 JSON 微任务会无界思考吃光 max_tokens、正文恒空,空正文被下游
静默当「没有结果」。修法:六处平台 LLM 微任务从裸 `call_agent_json` 换成
`call_agent_json_guarded`(空正文 → 告警 + 扩预算重试一次),并统一带 `no_think=True`。

六个站点每处一条测试(真调用或接线锁):
- import_pipeline._stage_story_phase_llm  (log_tag=story_phase)   → 真调用(空→重试→解析落库)
- import_pipeline._stage_cards            (log_tag=card_extract)  → 接线锁
- import_pipeline._stage_worldbook        (log_tag=worldbook_extract) → 接线锁
- import_pipeline._stage_npc_voices       (log_tag=npc_voice)     → 接线锁
- tavern_cards.llm_structure_description   (log_tag=card_import)   → 真调用(tool_schema + no_think)
- knowledge/card_audit.audit_character_cards (log_tag=card_audit) → 接线锁

护栏内部引用 `_harness` 模块全局 `call_agent_json`;调用方走 guarded,故在 harness
边界 monkeypatch.setattr(_harness, "call_agent_json", fake) 即可拦到全部真调用。

接线锁站点(card_extract / worldbook_extract / npc_voice / card_audit)的真调用夹具需
要真 db(connect + owner 校验 + 多表 SQL 分发)+ 控制器 + 凭证预检,夹具成本过高;按
既定策略退化为读源码文本的接线锁(断言函数区间内已换 guarded、无裸调用、带 no_think 与
对应 log_tag),真调用覆盖交给 story_phase 与 tavern 两条。
"""
import json
import os
import re

from agents import _harness

HERE = os.path.dirname(os.path.abspath(__file__))
RPG = os.path.abspath(os.path.join(HERE, "..", ".."))
# import_pipeline 已拆包:四个 LLM 微任务站点(story_phase/cards/worldbook/npc_voice)
# 全部住在子模块 stages_llm.py,接线锁读它。
IP_PATH = os.path.join(RPG, "platform_app", "import_pipeline", "stages_llm.py")
TAVERN_PATH = os.path.join(RPG, "platform_app", "tavern_cards.py")
AUDIT_PATH = os.path.join(RPG, "platform_app", "knowledge", "card_audit.py")


# ── 接线锁工具 ───────────────────────────────────────────────────────────
def _func_region(path: str, func: str) -> str:
    """截取顶层 `def func` 到下一个顶层 `def ` 之间的源码文本。"""
    src = open(path, encoding="utf-8").read()
    m = re.search(rf"(?m)^def {re.escape(func)}\b", src)
    assert m, f"{func} 未在 {path} 找到"
    start = m.start()
    nxt = re.search(r"(?m)^def ", src[start + 1:])
    end = (start + 1 + nxt.start()) if nxt else len(src)
    return src[start:end]


def _assert_wired(region: str, log_tag: str) -> None:
    assert "call_agent_json_guarded(" in region, "必须已换成护栏调用"
    # "call_agent_json(" 只会匹配裸调用:护栏名后紧跟 "_guarded(" 不含该子串
    assert "call_agent_json(" not in region, "不许残留裸 call_agent_json 调用"
    assert "no_think=True" in region, "结构化微任务必须禁深思"
    assert f'log_tag="{log_tag}"' in region, f"必须带 log_tag={log_tag}"


def test_wiring_card_extract():
    _assert_wired(_func_region(IP_PATH, "_stage_cards"), "card_extract")


def test_wiring_worldbook_extract():
    _assert_wired(_func_region(IP_PATH, "_stage_worldbook"), "worldbook_extract")


def test_wiring_npc_voice():
    _assert_wired(_func_region(IP_PATH, "_stage_npc_voices"), "npc_voice")


def test_wiring_card_audit():
    _assert_wired(_func_region(AUDIT_PATH, "audit_character_cards"), "card_audit")


# ── 真调用:tavern card_import(强 schema + no_think 透传)────────────────
def test_tavern_card_import_no_think_and_schema(monkeypatch):
    """llm_structure_description 走 guarded:tool_schema 保留、no_think=True 透传,
    成功返回被正常解析成字段 dict(护栏不动成功路径)。"""
    from platform_app import tavern_cards as TC

    captured: dict = {}

    def fake_call(api_id, model, system_prompt, user_prompt, user_id, **kw):
        captured.update(kw)
        captured["api_id"] = api_id
        return (json.dumps({
            "identity": "x", "background": "y",
            "appearance": "z", "personality": "w",
        }), {})

    monkeypatch.setattr(_harness, "call_agent_json", fake_call)
    monkeypatch.setattr(_harness, "resolve_api_and_model", lambda *a, **k: ("relay", "m"))

    out = TC.llm_structure_description("一段角色描述", user_id=1)

    assert captured.get("no_think") is True, "card_import 必须透传 no_think"
    assert captured.get("tool_schema") is not None, "强 schema 必须仍然传入"
    assert captured["tool_schema"].get("name") == "emit_card_fields"
    assert out.get("identity") == "x", "成功正文必须正常落到字段解析层"


# ── 真调用:story_phase(空正文→扩预算重试→解析结果落库)────────────────
def test_story_phase_empty_body_retries_and_parse_lands(monkeypatch):
    """第一跳空正文(reasoning 吃光)→ guarded 扩预算 max(400*2,1200)=1200 重试;
    重试拿到合法 JSON 数组 → 解析出的 phase 区间落到 chapter_facts UPDATE。
    锁「静默零结果无人发觉」的病根,并证明结果确实穿到解析+写库层。"""
    # patch-where-defined:_stage_story_phase_llm 定义在子模块 stages_llm,
    # 其 connect/_resolve_extractor_llm 补丁必须打在定义模块上。
    from platform_app.import_pipeline import stages_llm as IP
    from platform_app import usage as USAGE

    budgets: list = []
    nothink: list = []

    def fake_call(api_id, model, system_prompt, user_prompt, user_id, **kw):
        budgets.append(kw.get("max_tokens"))
        nothink.append(kw.get("no_think"))
        if len(budgets) == 1:
            return ("", {"reasoning_tokens": 400})  # 无界思考,正文恒空
        # total=2 章的 even-split 兜底只会产出 开端(1,1)/发展前期(2,2);
        # 用 结局(1,2) 让「LLM 解析出的区间落库」可与兜底显著区分。
        return (json.dumps([{"phase": "结局", "start": 1, "end": 2}]),
                {"output_tokens": 100})

    monkeypatch.setattr(_harness, "call_agent_json", fake_call)
    monkeypatch.setattr(IP, "_resolve_extractor_llm", lambda uid: ("relay", "m"))
    monkeypatch.setattr(USAGE, "compute_cost", lambda *a, **k: 0.0)

    executed: list = []

    class _Cur:
        rowcount = 1

        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class _DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            executed.append((sql, params))
            s = sql.lower()
            if s.startswith("select") and "from chapter_facts" in s:
                return _Cur([
                    {"chapter": 1, "summary": "开局", "title": "第一章"},
                    {"chapter": 2, "summary": "推进", "title": "第二章"},
                ])
            return _Cur([])

    monkeypatch.setattr(IP, "connect", lambda: _DB())

    class _Ctl:
        def add_usage(self, *a, **k):
            pass

        def update(self, *a, **k):
            pass

    IP._stage_story_phase_llm(_Ctl(), 1, 123)

    assert budgets == [400, 1200], f"空正文必须扩预算重试到 1200,实得 {budgets}"
    assert nothink == [True, True], "两跳都必须 no_think=True"
    assert any(p == ("结局", 123, 1, 2) for _sql, p in executed), \
        "LLM 解析出的 phase 区间必须落到 chapter_facts UPDATE(证明穿到解析+写库层)"
