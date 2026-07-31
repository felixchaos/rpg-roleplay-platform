"""state._mixins.apply_ops — apply_* 系列方法 mixin。

承载:
- apply_structured_updates  (GM 响应里的 【…】 标签 + ```json``` state-ops 处理)
- apply_player_directives   (玩家 /set 等指令)
- apply_state_write         (字符串 spec 写入,权限闸门)
- apply_state_write_typed   (typed value 写入,dispatcher 路由 + 权限闸门)
- apply_rules_state_ops     (RulesEngine state_op 应用)

mixin 内方法通过 self.xxx 调用 GameState 其它方法 (add_memory / add_hypothesis /
update_time / set_user_variable / confirm_time_jump / mark_user_locked 等),
通过多继承 + MRO 解析。
"""
from __future__ import annotations

import re

from core.clock import now_iso
from state.extractors import (
    _extract_explicit_time_updates,
)
from state.json_ops import _extract_json_state_ops
from state.labels import _risk_label, _validation_label
from state.parsers import (
    _clean_item,
    _parse_assignment,
    _split_items,
    _split_label,
    _split_relation,
)
from state.path_ops import (
    _get_path,
    _module_scene_active,
    _set_path,
    _write_path_allowed,
    _write_path_hard_forbidden,
    _write_path_kind,
    _write_path_module_managed,
    _write_path_rules_managed,
)
from state.permissions import _normalize_permission_mode, _permission_label
from state.time_ops import _gm_is_asking_for_time_confirm
from timeline_state import detect_time_directives, is_time_key

# 玩家人设卡字段(玩家手动定制后锁定 → GM/史官不可自动改写)。锁闸主战场在工具执行器
# (_set_player_profile_field);这里是 map_op_to_tool 路由失败 fall-through 老路径时的
# 纵深防御,与工具执行器内锁闸同语义,防「GM op → 无对应工具 → 直写老路径」绕过锁。
_PLAYER_PROFILE_LOCK_PATHS = {
    "player.appearance", "player.personality", "player.speech_style", "player.background",
}

# 玩家可见记忆桶 —— 与 GameState.add_memory 的白名单同一套。老路径对这些 path 的
# 追加必须回到 add_memory(单点根闸:去重 + memory.items dual-write + 洪泛闸)。
_MEMORY_BUCKETS = {"resources", "abilities", "facts", "pinned", "notes"}


class ApplyOpsMixin:
    """apply_* 系列方法 — GM/玩家/RulesEngine 对 state 的写入入口。"""

    # 后果账本 v1（docs/design/consequence_ledger_v1.md）：薄封装，纯逻辑在
    # state.consequence_ledger（不做 IO，方便单测）。dispatcher 工具
    # schedule_consequence 与 JSON op "consequence" 分支都走这一个方法。
    def register_consequence(
        self,
        *,
        text: str,
        due_turns: int | None = None,
        due_location: str | None = None,
        origin: str = "gm",
    ) -> tuple[bool, str]:
        from state.consequence_ledger import register_consequence as _register
        return _register(
            self.data,
            text=text,
            due_turns=due_turns,
            due_location=due_location,
            origin=origin,
        )

    # NPC 议程 v0（docs/design/npc_agenda_v0.md）：薄封装，纯逻辑在
    # state.npc_agenda（不做 IO，方便单测）。JSON op "agenda" 分支走这一个方法。
    def upsert_npc_agenda(
        self,
        *,
        name: str,
        goal: str | None = None,
        stance: str | None = None,
        extra_known: set[str] | None = None,
    ) -> tuple[bool, str]:
        from state.npc_agenda import upsert_agenda as _upsert
        return _upsert(
            self.data,
            name=name,
            goal=goal,
            stance=stance,
            turn=self.data.get("turn", 0),
            extra_known=extra_known,
        )

    def _gm_narrative_needs_pending(self) -> bool:
        """GM 自主叙事记忆写入(consequence / npc_agenda / hypothesis 系 JSON op)是否
        需先入 pending 而非直写。read_only / default 模式入 pending,full_access 直写
        (台账修复口径;auto_review 归入直写侧,与其「自动应用+审计」语义一致)。

        这些 op 原直调 register_consequence / upsert_npc_agenda / add_hypothesis 等
        专用方法,完全绕过 apply_state_write_typed 的权限闸门 —— read_only 拦不住,破坏
        「任何 LLM 自动写入都入 pending」承诺。核实:它们【确实进 GM 上下文注入】——
        consequence_echo(后果回响)/ npc_agenda(NPC 议程)provider + memory provider
        以「未确认推测」注入,confirm_hypothesis 更升级出注入的 runtime_fact,有真实
        叙事影响,故必须并入闸门。"""
        from state.permissions import _normalize_permission_mode
        mode = _normalize_permission_mode(
            self.data.setdefault("permissions", {}).get("mode", "full_access")
        )
        return mode in ("read_only", "default")

    def apply_structured_updates(self, gm_response: str) -> list[str]:
        updates: list[str] = []
        memory = self.data["memory"]
        # task 55：双协议。先剥离 ```json state-ops``` 代码块（更可靠的协议）
        # 再走传统 【...】 提取（向后兼容）。两者都受同一闸门管。
        text = gm_response or ""
        json_ops, text_stripped = _extract_json_state_ops(text)
        # 用剥离过 json 块的文本再做 【】 抽取，避免双重计算
        tags: list[str] = []
        for match in re.finditer(r"【([^】]+)】", text_stripped):
            line_start = text_stripped.rfind("\n", 0, match.start()) + 1
            line_end = text_stripped.find("\n", match.end())
            if line_end < 0:
                line_end = len(text_stripped)
            line = text_stripped[line_start:line_end]
            # Markdown option labels such as "- **【搜寻车厢】** ..." are UI copy,
            # not durable facts. JSON/state-ops still carry the real question.
            if "**【" in line and "】**" in line and (" - " in line or line.lstrip().startswith("-")):
                continue
            item = _clean_item(match.group(1))
            if item:
                tags.append(item)
        validation = self._scan_worldline_validation(tags)
        if validation["status"] != "none":
            self._set_worldline_validation(validation["status"], validation["message"])
            updates.append(f"设定校验：{_validation_label(validation['status'])}")

        # task 22：先看一眼有没有 pending_jump + 询问/待确认语境。
        #   - 玩家用自然语言发起的时间跳跃会调 request_time_jump → 设 pending_jump=awaiting_gm_confirmation。
        #   - GM 这一轮如果只是「请确认是否推进到 X？」，正文/结构化标签里都会出现目标时间 X。
        #   - 原代码不分意图，看到 X 就 update_time 锁定，把待确认状态冲掉。
        # 用 _gm_is_asking_for_time_confirm 兜底：发现「待确认 / 请确认 / 是否 / 询问玩家 / awaiting / pending」
        # 等语境，时间写回（不论结构化还是 prose 抽取）都跳过，保持 pending_jump。
        timeline_now = (self.data.get("world", {}) or {}).get("timeline", {}) or {}
        pending_jump = timeline_now.get("pending_jump") or None
        asking_for_confirm = _gm_is_asking_for_time_confirm(gm_response or "", tags)
        # task 35：玩家本轮自然语言触发的 pending_jump（pending.turn == 当前 turn）
        # → GM 同一轮不准锁，无论 GM 文本是否含 pending 信号。
        # request_time_jump 是 apply_player_directives 在本轮入口调的；turn 还没递增。
        # /set 不走 pending（直接 update_time），所以不受这条规则影响。
        try:
            _player_pending_this_turn = bool(
                pending_jump
                and int(pending_jump.get("turn", -1)) == int(self.data.get("turn", 0))
            )
        except Exception:
            _player_pending_this_turn = False
        if _player_pending_this_turn:
            asking_for_confirm = True

        # task 54：审批层统一化。原来每个 GM 标签按自己的 if 分支直接调
        # update_location / update_time / update_relationship / add_memory 等
        # 专用方法，绕过 _write_path_allowed 权限闸门 —— read_only / default 模式
        # 形同虚设（只有显式【状态写入：】走 apply_state_write 受管）。
        #
        # 现在所有"实质改 state"的标签都通过 _gm_write_via_gate(path, value, ...) 走，
        # 让 apply_state_write 统一做：
        #   1. 硬黑名单（permissions.* / history.*）拒绝
        #   2. 权限模式（read_only 全挡 / default 白名单 / ...）入 pending
        #   3. 路由到具体的 update_* 方法（apply_state_write 内部 kind dispatch）
        #
        # 例外(刻意豁免,理由已逐条核实显式化)：以下没有 path 的"控制流"标签保持
        # 原路径,因为它们不是"GM 自主写游戏态",而是流程信号 / 玩家意志落地：
        #   · 时间跳跃 pending_jump 状态机(confirm/reject_time_jump)——pending_jump
        #     由玩家自然语言 request_time_jump 发起,GM confirm 只是落地玩家已表达的
        #     意志(且对 pending 语境有重防御,见下方分支),非 GM 自主写。
        #   · 询问玩家(add_pending_question)——只入队一条待答问题,不改任何游戏态字段;
        #     真正的状态变化发生在玩家回答之后。
        #   · 设定校验(_scan/_set_worldline_validation)——只写校验结论标记,不改剧情态。
        #   · 世界线推演(_store_worldline_projection)——自带确认闸:未过校验且已有
        #     user_variables 时只落 pending_projection 返回 False(待用户确认),不直写。
        # ⚠️ 注意区分:上面是【标签路径】的控制流豁免。而下方【JSON op 路径】的
        # consequence / npc_agenda / hypothesis / confirm_/reject_hypothesis 系
        # **不在**此豁免内 —— 它们是 GM 自主写入的叙事记忆且【会进 GM 上下文注入】
        # (consequence_echo / npc_agenda / memory provider 消费),故走
        # _gm_narrative_needs_pending 权限闸门(read_only/default 入 pending),
        # 不能沿用"控制流信号"的豁免(它们有真实叙事影响,非纯流程信号)。
        def _gm_write_via_gate(path: str, value, *, append=False, overwrite=False, label_for_update: str = "") -> None:
            """统一权限闸门。所有"写状态字段"的 GM 标签都走这里。

            返回的 updates 文案策略：
            - 真生效（apply 返回"状态写入：..."）→ 用 label_for_update（友好文案，
              如 "位置：北港码头"）
            - 入 pending / 被拒 → 用 apply 的原始返回（"状态写入待审：..." / 拒绝），
              让前端 LeftRail 能清楚显示"哪些写入被挡了"，避免假成功 UI

            Bug 5：直接传 typed value 给 apply_state_write_typed，不再走
            `spec = f"{path}={value}"` 把 list 序列化成 "['a','b']"。
            """
            applied = self.apply_state_write_typed(
                path, value, source="gm", append=append, overwrite=overwrite,
            )
            if applied.startswith("状态写入：") and label_for_update:
                updates.append(label_for_update)
            else:
                updates.append(applied)

        for item in tags:
            if not item:
                continue
            key, value = _split_label(item)
            if "当前位置" in key or key in {"地点", "位置"}:
                # update_location 内部还会写 worldline.location_history，
                # apply_state_write kind=="location" 也会路由到 update_location，
                # 所以走 gate 行为一致。
                _gm_write_via_gate("player.current_location", value, label_for_update=f"位置：{value}")
            elif is_time_key(key):
                # 待确认中 + GM 正文在询问 → 不要锁；目标如果和 pending 一致就视为复述
                if pending_jump and asking_for_confirm:
                    updates.append(f"时间提案保留待确认：{value}")
                    continue
                _gm_write_via_gate("world.time", value, label_for_update=f"时间线锁定：{value}")
            elif "时间跳跃确认" in key:
                # task 32：GM 真实输出会出现【时间跳跃确认：待确认（当前处于 pending_confirmation 状态）】
                # 这种"标签 key 是确认，但 value 在说还在等"的混合形态。直接 confirm_time_jump
                # 会把 world.time 锁成"待确认"或 pending 的目标，把 pending_jump 清空。
                # 双重防御：
                #   1. value 含『待确认/未确认/暂不/pending/awaiting』→ 视为询问，不要 confirm
                #   2. asking_for_confirm 已识别为询问语境（含其它"等待玩家""设定冲突"等信号）→ 不 confirm
                _val_low = (value or "").lower()
                _value_pending = any(m in (value or "") for m in ("待确认", "未确认", "暂不", "暂缓")) \
                                 or any(m in _val_low for m in ("pending", "awaiting"))
                if pending_jump and (asking_for_confirm or _value_pending):
                    updates.append(f"时间跳跃确认保留待确认：{value or key}")
                    continue
                self.confirm_time_jump(value or key)
                updates.append(f"时间跳跃确认：{self.data['world']['time']}")
            elif "时间跳跃拒绝" in key:
                self.reject_time_jump(value)
                updates.append(f"时间跳跃拒绝：{value}")
            elif "设定校验" in key or "设定冲突" in key:
                continue
            elif "世界线推演" in key or "世界线预测" in key or "推演结果" in key:
                if self._store_worldline_projection(value, validation["status"] == "passed"):
                    updates.append("世界线推演：已写回")
                else:
                    updates.append("世界线推演：待用户确认")
            elif "用户变量" in key or key in {"变量", "设定变量", "玩家变量"}:
                var_key, var_value = _parse_assignment(value)
                if var_key:
                    # 走统一权限闸门(硬黑名单 / read_only 全挡入 pending / default 白名单),
                    # 与 JSON-op 等价路径 worldline.user_variables.X(kind=="user_variable")对齐,
                    # 别再直调 set_user_variable 绕过 gate(read_only 也拦不住)。
                    _gm_write_via_gate(
                        f"worldline.user_variables.{var_key}", var_value,
                        label_for_update=f"用户变量：{var_key}={var_value}",
                    )
            elif "询问玩家" in key or "向玩家提问" in key or "澄清问题" in key:
                if self.add_pending_question(value or key, source="gm"):
                    updates.append("等待玩家回答")
            elif "状态写入" in key or "UI变量" in key or "界面变量" in key:
                applied = self.apply_state_write(value, source="gm")
                updates.append(applied)
            elif "状态追加" in key or "追加变量" in key:
                applied = self.apply_state_write(value, source="gm", append=True)
                updates.append(applied)
            elif "状态覆盖" in key or "覆盖变量" in key:
                applied = self.apply_state_write(value, source="gm", overwrite=True)
                updates.append(applied)
            elif "当前目标" in key or key == "目标":
                _gm_write_via_gate("memory.current_objective", value, label_for_update=f"目标：{value}")
            elif "主线任务更新" in key or "主线" in key:
                # 主线同时写两个 path，按白名单都允许（在 default 模式下都自动）
                _gm_write_via_gate("memory.main_quest", value, label_for_update=f"主线：{value}")
                _gm_write_via_gate("memory.current_objective", value)
            elif "当前可支配资源" in key or "资源" in key:
                # 列表追加，apply_state_write kind="list" + append=False（不 overwrite）会去重追加
                for part in _split_items(value):
                    _gm_write_via_gate("memory.resources", part, append=True, label_for_update=f"资源：{part}")
            elif "能力" in key or "技能" in key or "掌握" in key:
                _gm_write_via_gate("memory.abilities", value, append=True, label_for_update=f"能力：{value}")
            elif "关系" in key:
                rel_name, rel_status = _split_relation(value)
                if rel_name and rel_status:
                    # 群反馈(斗破档):GM 用化名写关系(relationships.云芝)与真名(云韵)并存
                    # =人物面板同人两卡。写入前过 canon 别名归并(确定性,失败原名)。
                    try:
                        from kb.alias import canonical_name_for_save
                        _rn = canonical_name_for_save(self.data.get("_active_save_id"), rel_name)
                        if _rn and _rn != rel_name:
                            rel_name = _rn
                    except Exception:
                        pass
                    _gm_write_via_gate(f"relationships.{rel_name}", rel_status, label_for_update=f"关系：{rel_name} -> {rel_status}")
                elif self.add_memory("facts", item):
                    # facts 是低风险积累，仍走 add_memory 但记到 updates
                    updates.append(f"事实：{item}")
            elif "获得新身份" in key or "身份" in key or item.startswith("你已获得"):
                _gm_write_via_gate("memory.facts", item, append=True, label_for_update=f"事实：{item}")
            else:
                _gm_write_via_gate("memory.facts", item, append=True, label_for_update=f"事实：{item}")

        for value in _extract_explicit_time_updates(gm_response or ""):
            if value == self.data["world"]["time"]:
                continue
            # task 22 兜底：待确认 + 询问语境时，不要把询问句里出现的目标时间当成确认
            if pending_jump and asking_for_confirm:
                updates.append(f"时间提案保留待确认：{value}")
                continue
            # task 54：走 gate（之前直接 update_time 绕过 read_only 权限）
            _gm_write_via_gate("world.time", value, label_for_update=f"时间线锁定：{value}")

        # 历史遗留（已彻底移除，不再回退此路径）：此处曾有「作者写死 regex 兜底」——
        # 只要 GM 叙事正文里出现「重力控制/肉身飞行/悬浮/特殊小队」等关键词，就无条件给
        # 玩家注入《无限恐怖》某具体存档的能力/资源（"重力控制/肉身飞行（初步掌握）"、
        # "特殊小队建制"）。这对任何其它剧本都是错误注入：玩家反馈「开新档突然提示掌握了
        # 重力控制」即源于此，且「悬浮」这类通用词极易误触发；连 GM 只是在正文里『提及/
        # 伏笔』该能力（并未授予）也会被当成已掌握。能力/资源的授予只能由 GM 显式结构化
        # 标签（「能力：X」分支，见上）、JSON op 或 extractor 抽取写入——绝不靠对正文做
        # 关键词匹配。故整块删除。

        # task 55：JSON 协议处理。op = "set"/"append"/"overwrite"/"question"
        def _log_op_parse_error(reason: str, op_dump):
            try:
                audit = self.data.setdefault("permissions", {}).setdefault("audit_log", [])
                audit.append({
                    "ts": now_iso(),
                    "kind": "parse_error",
                    "raw_spec": str(op_dump)[:160],
                    "source": "gm:json",
                    "hint": reason,
                    "turn": self.data.get("turn", 0),
                })
                if len(audit) > 200:
                    self.data["permissions"]["audit_log"] = audit[-200:]
            except Exception:
                pass

        # 同批指纹去重:双源头(GM 自带 fence + 史官追加 fence)或模型重复输出时,同 op
        # 只 apply 一次。set 幂等但 updates 双报/审计翻倍;未来 add/subtract 类是真损坏。
        from state.json_ops import dedupe_json_ops
        json_ops = dedupe_json_ops(json_ops)

        for op in json_ops:
            try:
                kind = (op.get("op") or "set").lower()
                if kind == "question":
                    q = op.get("question") or op.get("text") or ""
                    options = op.get("options") or []
                    if q:
                        if self.add_pending_question(q, source="gm:json", options=options if isinstance(options, list) else None):
                            updates.append("等待玩家回答")
                    else:
                        # task 60：缺 question 文本时不静默
                        _log_op_parse_error("question op 缺 'question' 或 'text' 字段", op)
                        updates.append(f"JSON op 忽略（询问缺文本）：{op}")
                    continue
                # 后果账本 v1（docs/design/consequence_ledger_v1.md）：登记一条待兑现的
                # 后果。触发扫描/注入在 context provider 侧做，这里只负责写入登记。
                if kind == "consequence":
                    c_text = op.get("text") or ""
                    if not c_text:
                        _log_op_parse_error("consequence op 缺 'text' 字段", op)
                        updates.append(f"JSON op 忽略（后果缺文本）：{op}")
                        continue
                    # 权限闸门:GM 自主写后果账本(consequence_echo provider 注入 GM
                    # 上下文,有真实叙事影响)。read_only/default 入 pending 不直写。
                    if self._gm_narrative_needs_pending():
                        updates.append(self.add_pending_narrative_op(
                            "consequence",
                            {"text": c_text, "due_turns": op.get("due_turns"),
                             "due_location": op.get("due_location")},
                            source="gm:json",
                            display=f"后果登记：{c_text[:40]}",
                        ))
                        continue
                    ok, msg = self.register_consequence(
                        text=c_text,
                        due_turns=op.get("due_turns"),
                        due_location=op.get("due_location"),
                        origin="gm",
                    )
                    updates.append(f"状态写入：{msg}" if ok else msg)
                    continue
                # NPC 议程 v0（docs/design/npc_agenda_v0.md）：登记/更新一条 NPC
                # 当下议程（想要什么/对玩家什么态度）。goal/stance 至少给一，部分
                # 更新合并（不整条覆盖）。名字校验（防臆造路人）在纯函数层做。
                if kind == "agenda":
                    a_name = op.get("name") or ""
                    if not a_name:
                        _log_op_parse_error("agenda op 缺 'name' 字段", op)
                        updates.append(f"JSON op 忽略（议程缺 name）：{op}")
                        continue
                    # 测玩实证修复:首次登场 NPC 尚未进 relationships/active_entities,会被
                    # 白名单误拒(最该登议程的「角色初登场」恰恰失败)。放行条件放宽:名字
                    # 在本回合 GM【叙事正文】里真实出现 → 视为已知(史官正是从正文提议程,
                    # 正文出现=真实存在;仍挡凭空臆造的路人)。⚠️必须用剥掉 json fence 的
                    # text_stripped,不能用 gm_response —— 后者含 op 本身,名字恒在里面,
                    # 会废掉整个防臆造闸。
                    _agenda_extra = {a_name} if a_name and a_name in text_stripped else None
                    # 权限闸门:GM 自主写 NPC 议程(npc_agenda provider 注入 GM 上下文)。
                    # read_only/default 入 pending。extra_known 是 set,存 list(JSON 可
                    # 序列化,随存档持久化),approve 时重建 set —— 保留「本回合正文出现过
                    # 该名字=已知」这一登记时刻的信号,不受审批时回合漂移影响。
                    if self._gm_narrative_needs_pending():
                        updates.append(self.add_pending_narrative_op(
                            "npc_agenda",
                            {"name": a_name, "goal": op.get("goal"),
                             "stance": op.get("stance"),
                             "extra_known": list(_agenda_extra) if _agenda_extra else None},
                            source="gm:json",
                            display=f"NPC议程：{a_name}",
                        ))
                        continue
                    ok, msg = self.upsert_npc_agenda(
                        name=a_name,
                        goal=op.get("goal"),
                        stance=op.get("stance"),
                        extra_known=_agenda_extra,
                    )
                    updates.append(f"状态写入：{msg}" if ok else msg)
                    continue
                # task 75：hypothesis op 路由到独立 namespace，不污染 facts
                if kind == "hypothesis":
                    text = op.get("text") or op.get("value") or ""
                    if not text:
                        _log_op_parse_error("hypothesis op 缺 'text' 或 'value' 字段", op)
                        updates.append(f"JSON op 忽略（推测缺文本）：{op}")
                        continue
                    # 权限闸门:GM 自主写推测(memory provider 以「未确认推测」注入 GM
                    # 上下文)。read_only/default 入 pending。
                    if self._gm_narrative_needs_pending():
                        updates.append(self.add_pending_narrative_op(
                            "hypothesis",
                            {"text": text, "time_label": op.get("time_label"),
                             "characters": op.get("characters")},
                            source="gm:json",
                            display=f"推测登记：{text[:40]}",
                        ))
                        continue
                    mid = self.add_hypothesis(
                        text=text,
                        source="gm:json",
                        time_label=op.get("time_label"),
                        characters=op.get("characters"),
                    )
                    updates.append(f"推测登记：{mid} {text[:40]}")
                    continue
                # task 75：confirm/reject hypothesis（玩家或 GM 后续轮可触发）
                # 权限闸门:confirm 升级推测为 runtime_fact(注入 GM 上下文的叙事事实),
                # reject 删活跃推测 —— 都是 GM 自主改叙事记忆。read_only/default 入 pending
                # (有 id 才闸;缺 id 走原失败分支保持既有报错行为)。
                if kind == "confirm_hypothesis":
                    hid = op.get("id") or ""
                    if hid and self._gm_narrative_needs_pending():
                        updates.append(self.add_pending_narrative_op(
                            "confirm_hypothesis", {"id": hid},
                            source="gm:json", display=f"确认推测：{hid}",
                        ))
                        continue
                    if hid and self.confirm_hypothesis(hid, source="gm:json"):
                        updates.append(f"推测确认：{hid}")
                    else:
                        updates.append(f"推测确认失败（id 不存在或非 active）：{hid}")
                    continue
                if kind == "reject_hypothesis":
                    hid = op.get("id") or ""
                    if hid and self._gm_narrative_needs_pending():
                        updates.append(self.add_pending_narrative_op(
                            "reject_hypothesis", {"id": hid},
                            source="gm:json", display=f"拒绝推测：{hid}",
                        ))
                        continue
                    if hid and self.reject_hypothesis(hid):
                        updates.append(f"推测拒绝：{hid}")
                    else:
                        updates.append(f"推测拒绝失败（id 不存在）：{hid}")
                    continue
                path = (op.get("path") or "").strip()
                value = op.get("value", "")
                if not path:
                    # task 60：写 audit，让下轮 LLM 看见
                    _log_op_parse_error("set/append op 缺 'path' 字段", op)
                    updates.append(f"JSON op 忽略（缺 path）：{op}")
                    continue
                _gm_write_via_gate(
                    path, value,
                    append=(kind == "append"),
                    overwrite=(kind == "overwrite"),
                    label_for_update=f"{kind}: {path}",
                )
            except Exception as e:
                _log_op_parse_error(f"运行时异常：{e}", op)
                updates.append(f"JSON op 失败：{e}")

        memory["last_structured_updates"] = updates[-12:]
        return updates

    def apply_player_directives(self, player_input: str) -> list[str]:
        updates: list[str] = []
        # task 138: /reveal <text> 主动揭示秘密给 GM。
        # 默认 player_private.* 永不进 system prompt;玩家可以在本轮临时把
        # <text> 注入到 GM prompt 的【玩家本轮揭示】块。short_summary 会读
        # player_private.flags["revealed_this_turn"]。record_turn 清掉。
        # 先清上一轮残留(防御:正常 record_turn 会清,但异常路径可能漏)。
        _pp = self.data.setdefault("player_private", {})
        _flags = _pp.setdefault("flags", {})
        if "revealed_this_turn" in _flags:
            _flags["revealed_this_turn"] = ""
        _raw = (player_input or "").strip()
        if _raw.startswith("/reveal "):
            _reveal_text = _raw[len("/reveal "):].strip()
            if _reveal_text:
                _flags["revealed_this_turn"] = _reveal_text
                # 同时累加到 secrets 历史(玩家显式揭示的秘密下次自动可见)
                _sec_list = _pp.setdefault("secrets", [])
                if _reveal_text not in _sec_list:
                    _sec_list.append(_reveal_text)
                updates.append(f"玩家揭示秘密(本轮)：{_reveal_text[:40]}")
        updates.extend(self.apply_set_directive(player_input or ""))
        for directive in detect_time_directives(player_input or ""):
            value = directive.target
            if value != self.data["world"]["time"]:
                self.request_time_jump(value, player_input)
                updates.append(f"时间跳跃待确认：{value}")
        self.data["memory"]["last_structured_updates"] = updates[-12:] or self.data["memory"].get("last_structured_updates", [])
        return updates

    def apply_state_write(self, spec: str, source: str = "gm", append: bool = False, overwrite: bool = False, force: bool = False) -> str:
        path, value = _parse_assignment(spec)
        if not path:
            # task 60：原来解析失败直接 return，LLM 下一轮不知道这条丢了，
            # 还会继续输出同样格式重复失败。现在写 audit_log kind=parse_error，
            # context_engine.write_results 层下轮会把它告诉 LLM 让自纠。
            try:
                audit = self.data.setdefault("permissions", {}).setdefault("audit_log", [])
                audit.append({
                    "ts": now_iso(),
                    "kind": "parse_error",
                    "raw_spec": str(spec)[:160],
                    "source": source,
                    "hint": "无法解析 path=value；检查冒号是否是半角 `:` 或 `=`，path 不要含空格",
                    "turn": self.data.get("turn", 0),
                })
                if len(audit) > 200:
                    self.data["permissions"]["audit_log"] = audit[-200:]
            except Exception:
                pass
            return f"状态写入忽略（解析失败）：{spec[:60]}"
        return self.apply_state_write_typed(path, value, source=source, append=append, overwrite=overwrite, force=force)

    def _deny_write(self, path, value, source, blocked, reason_tag, hint=None) -> str:
        """三处硬闸(硬黑名单/rules_managed/module_managed)拒绝写入的共用 audit 落库 + 返回文案。
        逐字节保留各分支原有的 audit 字段与返回文案(仅参数化 blocked/hint/reason_tag 差异点)。"""
        try:
            audit = self.data.setdefault("permissions", {}).setdefault("audit_log", [])
            entry = {
                "ts": now_iso(),
                "source": source,
                "path": path,
                "value": str(value)[:120],
                "blocked": blocked,
            }
            if hint is not None:
                entry["hint"] = hint
            entry["turn"] = self.data.get("turn", 0)
            audit.append(entry)
            if len(audit) > 200:
                self.data["permissions"]["audit_log"] = audit[-200:]
        except Exception:
            pass
        return f"状态写入拒绝（{reason_tag}）：{path}"

    def apply_state_write_typed(self, path: str, value, source: str = "gm",
                                append: bool = False, overwrite: bool = False, force: bool = False) -> str:
        """Bug 5：与 apply_state_write 等价，但接受已解析的 typed value（list/dict/...
        不再走 f"{path}={value}" 字符串往返）。

        修复路径：GM op `{"value": ["a","b","c"]}` → _gm_write_via_gate / approve_pending_write
        之前都走 `spec = f"{path}={value}"` 把 list 变成字符串 "['a', 'b', 'c']"，
        审批落地时按逗号拆成 ["['a'", "'b'", "'c']"] 污染数组。

        task 87 Phase 6: 如果 source 以 "gm" 开头且 chat write context 在场,
        尝试通过 dispatcher 路由 (path → tool 映射)。成功就走 dispatcher,
        获得统一审计 + destructive 检查;无对应工具就 fall through 老路径。"""
        # 玩家身份保护(确定性):GM/史官 绝不能写玩家姓名。原著正文里出现角色名(如 郑吒/林有德)时,
        # 史官会误把它当成 player.name 写回 → 把玩家主角改成原著男主(群反馈)。玩家姓名只由玩家本人
        # (/set,source=player*)或建档时设定。**任何**把它改成不同值的 gm 写入一律拒——含「空姓名→
        # 原著男主」的首次填充(旧实现只挡覆盖、漏了空→填,导致角色无名时被史官命名为原著主角)。
        # 不指望史官提示词自觉(确定性铁律)。
        if path == "player.name" and str(source or "").startswith("gm"):
            _cur_name = str(((self.data.get("player") or {}).get("name") or "")).strip()
            _new_name = str(value or "").strip()
            if _new_name != _cur_name:  # 含 空→郑吒;仅同值 no-op 放行(不刷拒绝噪音)
                return f"状态写入拒绝:玩家姓名只由玩家本人设定,GM/史官 不可写(拦截「{_cur_name or '(空)'}」→「{_new_name}」)"
        # 玩家人设卡锁闸(纵深防御):map_op_to_tool 路由失败 fall-through 到老路径时,GM/史官
        # 仍可能直写玩家手动定制过的 appearance/personality/speech_style/background。与工具执行器内
        # _set_player_profile_field 同语义:玩家锁定过就拒 gm 写入。/set(source=user*/force)不受此闸。
        if path in _PLAYER_PROFILE_LOCK_PATHS and str(source or "").startswith("gm") and self._is_user_locked(path):
            return f"状态写入拒绝:{path} 已被玩家手动定制(锁定),GM/史官不可自动改写"
        # 1-c fork 收编:/set story_intent=X 之前写到顶层 data["story_intent"](没人读),而
        # WorldlineProvider 读的是 player_private.story_intent(建档 dual-write 的字段)→ 游戏中
        # 改 story_intent 对 GM 导演层无效。统一路由到 player_private.story_intent(与建档+读取同源)。
        if path == "story_intent":
            path = "player_private.story_intent"
        # task 87 Phase 6: dispatcher 路由
        if str(source or "").startswith("gm") and not force:
            try:
                from state_op_tool_map import map_op_to_tool
                from state_write_context import get_context as _get_chat_ctx
                from tools_dsl.command_dispatcher import (
                    ToolCallEnvelope,
                    ToolDispatcher,
                    get_registry,
                )
                _ctx = _get_chat_ctx()
                if _ctx is not None:
                    mapped = map_op_to_tool(
                        path, value,
                        op_kind="append" if append else "set",
                        append=append,
                    )
                    if mapped is not None:
                        tool_name, tool_args = mapped
                        # 用 chat handler 当前的 context 构造 envelope
                        _disp = ToolDispatcher(
                            registry=get_registry(),
                            state_provider=lambda env, _s=self: _s,
                        )
                        env = ToolCallEnvelope(
                            user_id=_ctx.user_id,
                            save_id=_ctx.save_id,
                            script_id=_ctx.script_id,
                            tool=tool_name,
                            args=tool_args,
                            origin=_ctx.origin,
                            trace_id=_ctx.trace_id,
                            depth=2,  # 在 GM 路径内,depth=1 已是 GM,这里 depth=2
                        )
                        result = _disp.dispatch_sync(env)
                        if result.ok:
                            return f"状态写入: {tool_name} → {(result.result or '')[:60]}"
                        # dispatcher 拒了 (destructive / origin / rate) — 不走老路径,
                        # 直接返回错误,让 audit_log 留下 rejected 记录
                        if "destructive_blocked" in (result.error or "") or "origin_forbidden" in (result.error or ""):
                            return f"状态写入拒绝（dispatcher）: {result.error}"
                        # 其它错误 (rate_limited / depth_exceeded) → fall through 到老路径
            except Exception as _exc:
                # dispatcher 路由失败不阻塞,fall through 老路径
                pass

        # P0 #1：硬黑名单（permissions.* / history.* / schema_version / created_at /
        # is_new）任何 force 都不能突破。原代码 `if not allowed and not force` 让
        # /set permissions.mode=full_access （force=True）直接落地，玩家可一句话
        # 关闭整套权限审批 + 篡改 audit_log + 改 history。
        if _write_path_hard_forbidden(path):
            return self._deny_write(path, value, source, "hard_forbidden", "硬黑名单")
        # 5E-compatible：受规则引擎管理的硬数值（HP/AC/initiative/dice_log）只能由
        # RulesEngine 修改。LLM/GM 自由写入或用户 /set 都拒绝并记入 audit。
        if _write_path_rules_managed(path) and not str(source or "").startswith("rules_engine"):
            return self._deny_write(
                path, value, source, "rules_managed", "rules_managed",
                hint="受规则引擎管理的硬数值（HP/AC/initiative/dice_log）只能由 RulesEngine 写入",
            )
        # 规则模组运行时，玩家所在房间由 RulesEngine / rules_bridge 的移动结果维护。
        # GM 只能叙事，不能把自然语言里的“当前位置”反写成另一套状态。
        if _write_path_module_managed(path) and _module_scene_active(self.data) and str(source or "").startswith("gm"):
            return self._deny_write(
                path, value, source, "module_managed", "module_managed",
                hint="规则模组运行时当前位置由 RulesEngine 房间状态维护，GM 不得写入",
            )
        permissions = self.data.setdefault("permissions", {})
        mode = _normalize_permission_mode(permissions.get("mode", "full_access"))
        allowed = _write_path_allowed(path, mode)
        if not allowed and not force:
            # 给每条 pending 加稳定 id（前端按 id 审批）。本来用 list index
            # 但 index 在 pop 之后会全部前移，导致前端"先点第一条→服务端处理
            # 完后 index 0 变成原 index 1"这种 race。
            import secrets as _secrets
            pending = {
                "id": _secrets.token_urlsafe(8),
                "path": path,
                "value": value,
                "source": source,
                "turn": self.data.get("turn", 0),
                "append": append,
                "overwrite": overwrite,
                "risk": _risk_label(path),
                "field": path,
                "from": _get_path(self.data, path),
                "to": value,
                "reason": f"{_permission_label(mode)}未授权此字段自动写入",
            }
            permissions.setdefault("pending_writes", []).append(pending)
            permissions["pending_writes"] = permissions["pending_writes"][-20:]
            return f"状态写入待审：{path}"

        kind = _write_path_kind(path)
        if kind == "location":
            self.update_location(value)
        elif kind == "time":
            self.update_time(value, source=source)
        elif kind == "scalar":
            _set_path(self.data, path, value)
        elif kind == "list":
            items = _split_items(value)
            # fork 收编(污染回路 B):master.py 曾指示 GM 把 acceptance 跳过理由写进 memory.facts,
            # 被 MemoryProvider/short_summary 每回合回读 → curator 验收元信息污染活事实库、自我强化。
            # acceptance 元信息属审计,不该进任何玩家级列表桶(facts/known_events/notes/pinned/
            # resources/abilities —— master.py 提示词自己都点名了 notes,闸必须覆盖全部 list kind)。
            # 确定性拦截(不依赖 GM 遵守提示词);add_memory 内另有单点根闸兜工具通道。
            from state.json_ops import is_acceptance_meta
            items = [it for it in items if not is_acceptance_meta(it)]
            # Bug 5 (retest)：value 是 list 且 op=set（既非 append 也非 overwrite）→
            # 视为完整替换。GM 给的「资源完整列表」语义就是"现在背包只剩这些"。
            # 之前 set 走 dedupe-append 路径会把新 Torch ×1 追加到老 Torch ×2 旁边，
            # 列表里同时出现矛盾的两条。
            is_full_list_replacement = isinstance(value, (list, tuple)) and not append
            if overwrite or is_full_list_replacement:
                _set_path(self.data, path, items)
            elif path.startswith("memory.") and path.split(".", 1)[1] in _MEMORY_BUCKETS:
                # 记忆桶的追加**一律回到 add_memory**(单点根闸)。这里原本手写 append,
                # 于是 dispatcher 路由失败 fall-through 到老路径时,既跳过了 memory.items
                # 的 dual-write,也绕过了洪泛闸 —— 典型的「修 A 漏 B」平行实现,现已收口。
                bucket = path.split(".", 1)[1]
                _blocked: list[str] = []
                for item in items:
                    if not item:
                        continue
                    _ok, _why = self.add_memory_ex(bucket, item, origin=source)
                    if _why and "去重" not in _why:
                        # 逐条继续,不因一条被拦就丢掉同批次里其它合法条目
                        _blocked.append(_why)
                if _blocked:
                    return f"状态写入部分拒绝({bucket}·{len(_blocked)} 条): {_blocked[0]}"
            else:
                target = _get_path(self.data, path)
                if not isinstance(target, list):
                    _set_path(self.data, path, [])
                    target = _get_path(self.data, path)
                for item in items:
                    if item and item not in target:
                        target.append(item)
        elif kind == "relationship":
            name = path.split(".", 1)[1].strip()
            # 空名守卫:`/set relationships.=X`(force 路径绕过 dispatcher)会让 name=""
            # 写入一条空 key 关系,注入成"· ：X"无主体噪声。与 set_relationship 工具的
            # `if not ch` 守卫对齐,空名直接跳过。
            if name:
                self.update_relationship(name, value)
        elif kind == "user_variable":
            key = path.split(".", 2)[2]
            self.set_user_variable(key, value, source=source)
        elif kind == "custom_ui":
            key = path.split(".", 1)[1] if path.startswith("ui.") else path.split(".", 2)[2]
            self.data.setdefault("worldline", {}).setdefault("custom_ui", {})[key] = value
        else:
            _set_path(self.data, path, value)

        # task 36：用户显式写入（/set / 任何 force=True 或 source=user* 调用）
        # 要登记到 user_locked_fields，使后续 update_time / _phase_for_time 等自动
        # 派生不能覆盖。GM 自己的写入不登记，仍允许自动派生。
        try:
            if force or str(source or "").startswith("user"):
                self.mark_user_locked(path)
        except Exception:
            pass

        audit = {
            "path": path,
            "value": value,
            "source": source,
            "mode": mode,
            "turn": self.data.get("turn", 0),
        }
        permissions.setdefault("audit_log", []).append(audit)
        permissions["audit_log"] = permissions["audit_log"][-30:]
        return f"状态写入：{path}"

    # ── 规则引擎专用入口 ────────────────────────────────────────
    # 这些方法走 source="rules_engine"，因此能通过 State Gate 写入受保护字段。
    # 任何对 HP/AC/initiative/dice_log 的修改都必须经此入口或下方专用 helper。

    def apply_rules_state_ops(self, ops: list[dict], reason: str = "") -> list[str]:
        """应用 RulesEngine 返回的 state_ops 列表。

        op 字典格式：{"op": "set"|"add"|"subtract"|"append", "path": "...", "value": ...}
        path 支持特殊前缀 "_combatant.<id>.<field>" → 解析为 encounter.combatants 中
        对应 id 的字段。其它 path 直接写到 self.data。
        """
        applied: list[str] = []
        encounter = self.data.setdefault("encounter", {})
        combatants = encounter.setdefault("combatants", [])
        comb_by_id = {c.get("id"): c for c in combatants}
        for op in ops or []:
            kind = op.get("op", "set")
            path = str(op.get("path", "") or "")
            value = op.get("value")
            if not path:
                continue
            if path.startswith("_combatant."):
                parts = path.split(".", 2)
                if len(parts) < 3:
                    continue
                _, cid, field = parts
                target = comb_by_id.get(cid)
                if not target:
                    continue
                if kind == "subtract":
                    target[field] = max(0, int(target.get(field, 0) or 0) - int(value or 0))
                elif kind == "add":
                    target[field] = int(target.get(field, 0) or 0) + int(value or 0)
                else:
                    target[field] = value
                # HP 落 0 自动 defeated
                if field == "hp" and int(target.get("hp", 0) or 0) <= 0:
                    target["defeated"] = True
                applied.append(f"combatant {cid}.{field}={target.get(field)}")
                continue
            # 通用 path：rules_engine 直写。规则路径走专用 set，绕过字符串解析。
            try:
                if kind == "subtract":
                    cur = int(_get_path(self.data, path) or 0)
                    _set_path(self.data, path, max(0, cur - int(value or 0)))
                elif kind == "add":
                    cur = int(_get_path(self.data, path) or 0)
                    _set_path(self.data, path, cur + int(value or 0))
                elif kind == "append":
                    cur = _get_path(self.data, path)
                    if not isinstance(cur, list):
                        _set_path(self.data, path, [])
                        cur = _get_path(self.data, path)
                    cur.append(value)
                else:
                    _set_path(self.data, path, value)
                applied.append(f"set {path}={_get_path(self.data, path)}")
            except Exception as e:
                applied.append(f"failed {path}: {e}")
        # audit — 与战斗直改路径共用 append_rules_audit 单一落法(见 rules_gameplay mixin)。
        self.append_rules_audit(reason=reason, ops=len(ops or []))
        return applied

