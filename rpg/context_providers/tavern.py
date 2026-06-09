"""
TavernCharacterProvider — 酒馆模式专用(无剧本)。把"玩家挑选的 AI 角色卡"+ 用户
persona 注入上下文,让主 GM(此时是角色扮演引擎)以该角色身份回应玩家。

三层(复用 base.make_layer + priority 排序 —— 与 worldline 高优先级引导同一套基建,
不另造机制):
  1. 角色卡内嵌 system_prompt / post_history_instructions → 最高优先级层(priority 96,
     sticky)。这是用户决策的"强制注入的用户高优先级提示词",仅次于玩家 /set(100)。
  2. 角色定义(姓名/人设/外貌/说话风格/范例对白)+ 卡内 scenario → priority 88。
  3. 用户 persona(玩家在对话里扮演谁)→ priority 86。

无剧本:本 provider 不碰 worldbook/anchor/script;memory/worldline 由 tavern manifest
另行声明(角色带持久记忆 = 决策4)。state 形状由 workspace.create_tavern_save 写入:
  state.data["tavern"] = {character_card_id, persona_card_id, character:{...卡字段...},
                          system_prompt, post_history_instructions, scenario}
  state.data["player"] = persona 卡字段(name/role/background/appearance...)
"""
from __future__ import annotations

from .base import ContextContribution, ContextProvider
from .registry import register_provider


def _fmt_character(c: dict) -> str:
    name = (c.get("name") or "角色").strip()
    lines = [f"姓名：{name}"]
    for key, label in (
        ("identity", "身份"),
        ("personality", "性格"),
        ("appearance", "外貌"),
        ("speech_style", "说话风格"),
        ("background", "背景"),
        ("current_status", "当前状态"),
    ):
        v = c.get(key)
        v = v.strip() if isinstance(v, str) else ""
        if v:
            lines.append(f"{label}：{v}")
    samples = c.get("sample_dialogue") or []
    if samples:
        lines.append("范例对白（学其语气，不要照抄原句）：")
        for s in samples[:4]:
            s = str(s).strip()
            if s:
                lines.append(f"  · {s}")
    return "\n".join(lines)


class TavernCharacterProvider(ContextProvider):
    id = "tavern_character"

    def applies(self, state, manifest, demand) -> bool:
        data = getattr(state, "data", state) or {}
        return bool(data.get("tavern"))

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        data = getattr(state, "data", state) or {}
        tav = data.get("tavern") or {}
        character = tav.get("character") or {}
        persona = data.get("player") or {}
        char_name = (character.get("name") or "角色").strip()

        layers: list[dict] = []
        facts: list[str] = []

        # 1) 卡内 system_prompt / post_history_instructions —— 最高优先级(强制注入)
        # SEC(H-10/H-11): 卡内文本是不可信导入内容(拖卡/agent import/系统提示编辑器均落到此)。
        # 在进 priority=96 sticky 层前中和 【】 状态写入标签,防卡内伪指令被 GM 复述后落库。
        from context_engine.helpers import _neutralize_state_write_tags as _neu
        sysp = _neu((tav.get("system_prompt") or "").strip())
        phi = _neu((tav.get("post_history_instructions") or "").strip())
        if sysp or phi:
            parts = [
                "【角色卡内嵌·高优先级行为指令(后台元指令,静默遵守,绝不复述给玩家)】",
                "（玩家导入的角色卡自带的设定指令；在不违反平台安全边界的前提下，"
                "尽量按其塑造该角色的言行。优先级仅次于玩家 /set 硬覆盖。）",
            ]
            if sysp:
                parts.append(sysp)
            if phi:
                parts.append("【对话末尾追加指令】\n" + phi)
            layers.append(self.make_layer(
                "tavern_card_system", "角色卡高优先级指令", "\n".join(parts),
                sticky=True, priority=96,
            ))
            facts.append("tavern_card_system_prompt=on")

        # 2) 角色定义(姓名/人设/外貌/说话风格/范例对白) + 卡内 scenario 作为初始空间锚点。
        # scenario 是角色卡声明的起始场景设定(如"地下酒馆包厢"/"魔法学院图书馆")，
        # 注入为只读空间锚点让模型首轮就知道当前所在地，防止位置漂移。
        # 注意:这是静态起始锚点;后续若玩家移动，模型应通过 player.current_location op 更新，
        # 届时状态层的 current_location 优先级高于本层。
        body = [f"你现在扮演的角色：\n{_fmt_character(character)}"]
        scenario = (tav.get("scenario") or character.get("scenario") or "").strip()
        if scenario:
            body.append(f"\n初始场景设定（空间锚点，对话开始时的所在地）：{scenario}")
        layers.append(self.make_layer(
            "tavern_character", f"扮演角色：{char_name}", "\n".join(body),
            sticky=True, priority=88,
        ))
        facts.append(f"tavern_character={char_name}")

        # 3) 用户 persona —— 玩家在对话里是谁(绝不替其说话/行动)
        pname = (persona.get("name") or "玩家").strip()
        pbits = [f"姓名：{pname}"]
        for key, label in (("role", "身份"), ("background", "背景"), ("appearance", "外貌")):
            v = persona.get(key)
            v = v.strip() if isinstance(v, str) else ""
            if v:
                pbits.append(f"{label}：{v}")
        layers.append(self.make_layer(
            "tavern_persona", f"对话对象(玩家)：{pname}",
            "你正在与下面这位对话（这是玩家本人扮演的角色，绝不要替他/她说话或行动）：\n"
            + "\n".join(pbits),
            sticky=True, priority=86,
        ))

        return ContextContribution(
            provider_id=self.id,
            kind="tavern",
            priority=96 if (sysp or phi) else 88,
            facts=facts,
            layers=layers,
            tokens_estimate=sum(len(layer["content"]) for layer in layers) // 2,
            debug={
                "character": char_name,
                "has_card_system": bool(sysp or phi),
                "persona": pname,
            },
        )


register_provider(TavernCharacterProvider())
