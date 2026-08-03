"""子代理的【最近对话】块必须真的是「最近」。

群反馈(行者无疆):「子代理有时候抽风认为现在在小说序章-生化危机-激光通道,
好几次了,且每次都是识别为在激光通道」。

生产复现(save 268,玩家已在第 116 章):
  · `history_messages()` 把**已 closed phase 的前情提要顶在最前面**(最旧的开局摘要);
  · `_curator_task_prompt` 又对 `json.dumps(...)` 做**头部**截断 `[:2400]`。
  该档前情提要的 json 前缀 2409 字符 > 2400 ⇒ 【最近对话】块 100% 是第 1-12 回合的
  「激光通道」摘要,当前回合原文一条都进不来。玩家输入自带场景信号时子代理还能纠正,
  一点「继续」这种无信号输入就照着开局摘要往下编 —— 这就是「有时候」。

锁死两条:①不吃前情提要 ②预算从**最新**一条往回装(保尾不保头)。
"""
from __future__ import annotations

import ast
import json
import pathlib

from agents.context_agent import (_RECENT_BUDGET, _curator_task_prompt,
                                  _recent_dialogue_json)

_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[2] / "agents" / "context_agent.py"
).read_text(encoding="utf-8")

_DIGEST = ("【前情提要 · 已压缩历史阶段】\n\n## Phase 1: T病毒爆发前夜 (turn 7-12)\n"
           "赵时决定跟随郑吒进入激光通道以保存实力。" + "激光" * 500)


class _FakeState:
    """记录 history_messages 的调用参数;按 include_digest 返回不同结构。"""

    def __init__(self, turns: list[tuple[str, str]]):
        self.data = {"world": {"time": "第116章·蜂巢外"}, "memory": {}}
        self._turns = turns
        self.calls: list[dict] = []

    def history_messages(self, limit_turns=6, *, save_id=None, include_digest=True):
        self.calls.append({"limit_turns": limit_turns, "include_digest": include_digest})
        msgs = [{"role": r, "content": c} for r, c in self._turns]
        if include_digest:
            msgs = [{"role": "user", "content": _DIGEST},
                    {"role": "assistant", "content": "[已收到前情提要,继续在此基础上叙事]"},
                    *msgs]
        return msgs


def _long_turns() -> list[tuple[str, str]]:
    """3 轮真实体量的对话(GM 正文动辄上千字)。"""
    out: list[tuple[str, str]] = []
    for i in range(1, 4):
        out.append(("user", f"玩家输入{i}。" + "走" * 200))
        out.append(("assistant", f"GM正文{i}。" + "叙" * 1200))
    return out


def test_digest_never_enters_recent_dialogue():
    st = _FakeState(_long_turns())
    block = _recent_dialogue_json(st)
    assert st.calls and st.calls[0]["include_digest"] is False, \
        "【最近对话】必须显式要求不带前情提要"
    assert "前情提要" not in block and "激光通道" not in block, \
        "开局的已压缩阶段摘要漏进了【最近对话】"


def test_keeps_newest_turn_not_oldest():
    """核心回归:预算不够时丢**最旧**的,当前回合永远在。"""
    st = _FakeState(_long_turns())
    block = _recent_dialogue_json(st)
    assert "GM正文3" in block, "最新一轮 GM 正文被截掉了(头部截断的老病)"
    assert "玩家输入3" in block, "最新一轮玩家输入被截掉了"
    assert "玩家输入1" not in block, "预算应先丢最旧的一轮"


def test_within_budget_and_chronological():
    st = _FakeState(_long_turns())
    block = _recent_dialogue_json(st)
    assert len(block) <= _RECENT_BUDGET * 1.2, f"块超预算:{len(block)}"
    parsed = json.loads(block)
    assert parsed, "不该渲染成空"
    order = [m["content"][:6] for m in parsed]
    assert order == sorted(order, key=lambda s: order.index(s)), "顺序必须是时间正序"
    assert parsed[-1]["content"].startswith("GM正文3"), "最后一条必须是最新的"


def test_single_oversized_message_still_present():
    """只有一条、且超预算 —— 也必须进得去(截断,不是丢弃)。"""
    st = _FakeState([("assistant", "巨长正文" + "字" * 9000)])
    parsed = json.loads(_recent_dialogue_json(st))
    assert len(parsed) == 1 and parsed[0]["content"].startswith("巨长正文")


def test_prompt_uses_the_helper_not_head_truncation():
    """源码守卫:_curator_task_prompt 不许再自己对 history 做头部截断。"""
    tree = ast.parse(_SRC)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_curator_task_prompt")
    called = {getattr(n.func, "id", None) or getattr(n.func, "attr", "")
              for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "_recent_dialogue_json" in called, "【最近对话】必须走保尾渲染器"
    assert "history_messages" not in called, \
        "别再在 prompt 里直接取 history_messages —— 那条路会带上前情提要"


def test_prompt_end_to_end_has_current_scene():
    st = _FakeState(_long_turns())
    prompt = _curator_task_prompt(st, "（继续推进剧情）", [])
    assert "【最近对话】" in prompt
    assert "GM正文3" in prompt, "无信号输入(点「继续」)时,当前场景是子代理唯一的锚"
    assert "激光通道" not in prompt
