"""
state.py — GameState：玩家档案、对话历史、存档/读档
"""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Any
from timeline_state import detect_time_directives, is_time_key, clean_time_value, looks_like_time_value

BASE = Path(__file__).parent
SAVE_FILE = BASE / "saves" / "game_state.json"
CURRENT_SCHEMA_VERSION = 4

# 剧情开始时的初始状态
DEFAULT_STATE = {
    "schema_version": CURRENT_SCHEMA_VERSION,
    "player": {
        "name": "",
        "role": "",          # 玩家选择的角色定位
        "background": "",    # 玩家自定义背景
        "current_location": "柏林，哈布斯堡庄园附近"
    },
    "world": {
        "time": "图卢兹失守后翌日，柏林",
        "timeline": {
            "anchor_state": "locked",
            "current_label": "图卢兹失守后翌日，柏林",
            "current_phase": "柏林暗流篇",
            "anchor_source": "initial",
            "anchor_turn": 0,
            "pending_jump": None,
            "last_transition": None,
        },
        "known_events": [
            "宴会上调令伪造事件已曝光",
            "图卢兹战役：薇瑟帝国八位渊戮大胜，地联溃败",
            "娅赛兰决定暂留柏林",
            "蛇信在外围全程监视"
        ]
    },
    "relationships": {},    # {角色名: "信任/警惕/未知"...}
    "history": [],           # 完整对话 [{"role":"user","content":...}, ...]
    "permissions": {
        "mode": "full_access",  # default / auto_review / full_access
        "pending_writes": [],
        "pending_questions": [],
        "audit_log": []
    },
    "worldline": {
        "user_variables": {},
        "constraints": [
            "用户变量优先级高于世界线推演。",
            "世界线推演必须先满足玩家设定，再外推局势。",
            "若推演与用户变量冲突，必须报告冲突，不得写回为事实。"
        ],
        "last_projection": None,
        "pending_projection": None,
        "last_validation": {
            "status": "none",
            "message": "",
            "turn": 0
        },
        "custom_ui": {}
    },
    "memory": {
        "mode": "normal",    # concise / normal / deep
        "main_quest": "",
        "current_objective": "观察柏林局势，保护蕾穆丽娜",
        "resources": [],
        "abilities": [],
        "facts": [],
        "pinned": [],
        "notes": [],
        "last_retrieval": "",
        "last_context": {},
        "last_context_agent": {},
        "last_structured_updates": []
    },
    "turn": 0,
    "is_new": True,
    "created_at": ""
}

MAX_HISTORY_TURNS = 6  # 保留最近6轮（12条消息）


class GameState:
    def __init__(self, data: dict):
        self.data = self._migrate(data)

    # ── 读档 / 新档 ────────────────────────────────────────────────
    @classmethod
    def load_or_new(cls) -> "GameState":
        SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SAVE_FILE.exists():
            try:
                with open(SAVE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data = cls._migrate(data)
                print(f"[读档] {data['player']['name']} · 第{data['turn']}回合 · {data['world']['time']}")
                return cls(data)
            except Exception as e:
                print(f"[读档失败：{e}，开始新游戏]")
        return cls.new()

    @classmethod
    def new(cls) -> "GameState":
        data = copy.deepcopy(DEFAULT_STATE)
        data["created_at"] = datetime.now().isoformat(timespec="seconds")
        return cls(data)

    @staticmethod
    def _migrate(data: dict) -> dict:
        source = data or {}
        source_world = source.get("world", {}) if isinstance(source, dict) else {}
        source_timeline = source_world.get("timeline", {}) if isinstance(source_world, dict) else {}
        migrated = copy.deepcopy(DEFAULT_STATE)
        _deep_update(migrated, source)
        migrated["schema_version"] = CURRENT_SCHEMA_VERSION
        migrated.setdefault("history", [])
        migrated.setdefault("relationships", {})
        permissions = migrated.setdefault("permissions", {})
        permissions.setdefault("mode", "full_access")
        permissions.setdefault("pending_writes", [])
        permissions.setdefault("pending_questions", [])
        permissions.setdefault("audit_log", [])
        worldline = migrated.setdefault("worldline", {})
        worldline.setdefault("user_variables", {})
        worldline.setdefault("constraints", list(DEFAULT_STATE["worldline"]["constraints"]))
        worldline.setdefault("last_projection", None)
        worldline.setdefault("pending_projection", None)
        worldline.setdefault("last_validation", {"status": "none", "message": "", "turn": 0})
        worldline.setdefault("custom_ui", {})
        timeline = migrated.setdefault("world", {}).setdefault("timeline", {})
        if not source_timeline or not source_timeline.get("current_label"):
            timeline["current_label"] = migrated["world"].get("time", "")
            timeline["anchor_source"] = "migrated"
        timeline.setdefault("anchor_state", "locked")
        timeline.setdefault("current_phase", "柏林暗流篇")
        timeline.setdefault("anchor_turn", migrated.get("turn", 0))
        timeline.setdefault("pending_jump", None)
        timeline.setdefault("last_transition", None)
        return migrated

    # ── 存档 ──────────────────────────────────────────────────────
    def save(self, target_path: "Path | str | None" = None) -> str:
        """写 state 到磁盘。

        多用户安全：在服务器模式下，**绝对不能写全局 SAVE_FILE**——这是并发污染源。
        - 显式 target_path：写到该路径（推荐让调用方传 user-specific runtime_state_path）
        - 否则按部署模式决定：
          - server / 强制鉴权：拒绝写盘，返回空串（数据应该走 DB 持久化）
          - 本地匿名：写 SAVE_FILE（兼容旧逻辑）

        返回实际写入的路径，调用方可用于审计。空串表示没写盘。
        """
        import os as _os
        out_path: Path | None = None
        if target_path:
            out_path = Path(target_path)
        else:
            # 服务器模式：禁止落到全局 SAVE_FILE
            mode = _os.environ.get("RPG_DEPLOYMENT_MODE", "local").strip().lower()
            require_auth = _os.environ.get("RPG_REQUIRE_AUTH", "")
            is_server = require_auth == "1" or mode not in {"local", "desktop", "self_hosted", "self-hosted"}
            if is_server:
                return ""  # 不写盘；DB 是权威源，runtime_state_path 由 branches.persist_runtime_state 管
            out_path = SAVE_FILE

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = out_path.with_suffix(out_path.suffix + ".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(out_path)
        return str(out_path)

    # ── 玩家设置 ──────────────────────────────────────────────────
    def setup_player(self, name: str, role: str, background: str):
        self.data["player"]["name"] = name
        self.data["player"]["role"] = role
        self.data["player"]["background"] = background
        self.data["is_new"] = False
        if "穿越者" in role:
            self.add_memory("facts", "玩家读过原著，但亲历世界与书中记忆可能出现偏差。")
        if "魔女" in role or "魔力∞" in background:
            self.add_memory("abilities", "魔力∞，潜力极高但控制方式仍需在剧情中摸索。")

    # ── 记录对话 ──────────────────────────────────────────────────
    def record_turn(self, player_input: str, gm_response: str):
        self.data["history"].append({"role": "user",      "content": player_input})
        self.data["history"].append({"role": "assistant", "content": gm_response})
        self.data["turn"] += 1

    # ── 给 GM 用的历史消息列表 ────────────────────────────────────
    def history_messages(self, limit_turns: int = MAX_HISTORY_TURNS) -> list[dict]:
        max_msgs = limit_turns * 2
        return list(self.data["history"][-max_msgs:])

    def chat_history(self) -> list[dict]:
        return list(self.data["history"])

    # ── 状态简报（注入 system prompt）────────────────────────────
    def short_summary(self) -> str:
        p = self.data["player"]
        w = self.data["world"]
        m = self.data["memory"]
        permissions = self.data.get("permissions", {})
        worldline = self.data.get("worldline", {})
        rel_lines = []
        for char, status in self.data["relationships"].items():
            rel_lines.append(f"  · {char}：{status}")
        rel_text = "\n".join(rel_lines) if rel_lines else "  （尚未与任何人建立明确关系）"

        known = "\n".join(f"  · {e}" for e in w["known_events"])
        memory_lines = []
        if m["main_quest"]:
            memory_lines.append(f"主线：{m['main_quest']}")
        if m["current_objective"]:
            memory_lines.append(f"当前目标：{m['current_objective']}")
        memory_lines.extend(f"能力：{x}" for x in m["abilities"][:6])
        memory_lines.extend(f"资源：{x}" for x in m["resources"][:6])
        memory_lines.extend(f"固定记忆：{x}" for x in m["pinned"][:6])
        if m["mode"] == "deep":
            memory_lines.extend(f"事实：{x}" for x in m["facts"][:10])
            memory_lines.extend(f"笔记：{x}" for x in m["notes"][:8])
        elif m["mode"] == "normal":
            memory_lines.extend(f"事实：{x}" for x in m["facts"][:5])
            memory_lines.extend(f"笔记：{x}" for x in m["notes"][:3])
        memory_text = "\n".join(f"  · {line}" for line in memory_lines) or "  （暂无长期记忆）"
        variables = worldline.get("user_variables", {})
        if variables:
            variable_text = "\n".join(
                f"  · {name}={info.get('value', '')}"
                for name, info in list(variables.items())[:12]
            )
        else:
            variable_text = "  （暂无用户变量）"
        return f"""【玩家档案】
姓名：{p['name']}
定位：{p['role']}
背景：{p['background']}
当前位置：{p['current_location']}

【当前时间线】{w['time']}
【时间线锚定】
  · 状态：{w.get('timeline', {}).get('anchor_state', 'locked')}
  · 阶段：{w.get('timeline', {}).get('current_phase', '未知')}
  · 待确认跳跃：{_format_pending_timeline(w.get('timeline', {}).get('pending_jump'))}

【已知事件】
{known}

【关系状态】
{rel_text}

【长期记忆】
{memory_text}

【权限与世界线】
  · LLM写入权限：{_permission_label(permissions.get('mode', 'full_access'))}
  · 用户变量：
{variable_text}

【当前回合】第 {self.data['turn']} 回合"""

    def status_payload(self) -> dict:
        p = self.data["player"]
        w = self.data["world"]
        m = self.data["memory"]
        return {
            "player": dict(p),
            "world": dict(w),
            "relationships": dict(self.data["relationships"]),
            "permissions": copy.deepcopy(self.data.get("permissions", {})),
            "worldline": copy.deepcopy(self.data.get("worldline", {})),
            "memory": copy.deepcopy(m),
            "turn": self.data["turn"],
            "schema_version": self.data.get("schema_version", CURRENT_SCHEMA_VERSION),
            "is_new": self.data["is_new"],
            "created_at": self.data["created_at"],
            "summary": self.short_summary(),
            "history": self.chat_history(),
            "suggestions": self.suggestions(),
        }

    def suggestions(self) -> list[str]:
        latest = _latest_assistant_text(self.data["history"])
        player = self.data["player"]
        world = self.data["world"]
        memory = self.data["memory"]
        context = "\n".join([
            latest,
            player.get("current_location", ""),
            world.get("time", ""),
            memory.get("main_quest", ""),
            memory.get("current_objective", ""),
            "\n".join(world.get("known_events", [])),
            "\n".join(memory.get("resources", [])),
            "\n".join(memory.get("abilities", [])),
            "\n".join(memory.get("facts", [])),
        ])
        # task 41：判断当前 state 是不是 MuMuAINovel 默认柏林剧情上下文。
        # 导入剧本（task 34/40 已 scrub 柏林默认）context 不应再出现 柏林/图卢兹/哈布斯堡/蛇信
        # 等 token —— 这时不能把"要求一份柏林当前势力图..." fallback 推给用户。
        is_default_novel = any(
            tok in context for tok in
            ("柏林", "图卢兹", "哈布斯堡", "蛇信", "薇瑟帝国", "扎兹巴鲁姆", "蕾穆丽娜",
             "斯雷因", "伊奈帆", "甲胄骑士", "Kataphrakt", "调令伪造")
        )

        candidates: list[tuple[int, str]] = []

        def add(score: int, text: str, *needles: str):
            if needles and not any(n in context for n in needles):
                return
            candidates.append((score + _hit_score(context, needles), _player_action_text(text)))

        # 命名 needle 的候选保留——它们本身就要求 context 含对应 token 才会被加入。
        add(120, "追问斯雷因：界冢伊奈帆一行的位置、人数和目的。", "斯雷因", "伊奈帆", "新动作")
        add(112, "确认蕾穆丽娜的安置、护卫和通讯权限。", "蕾穆丽娜", "内宅", "安置")
        add(106, "要求扎兹巴鲁姆交出柏林战役情报图和权限边界。", "扎兹巴鲁姆", "伯爵")
        add(102, "召集特殊小队，建立柏林城内侦察与撤离预案。", "特殊小队", "整备班")
        add(98, "检查两台甲胄骑士配置，挑选不易背叛的驾驶员。", "甲胄骑士", "Kataphrakt")
        add(94, "摸清基地核心机密库、通讯室和医疗区的位置。", "基地", "核心机密库")
        add(90, "反查蛇信的监视线路，确认能否反向利用。", "蛇信", "监视")
        add(86, "复盘图卢兹战报，寻找地联下一步反扑窗口。", "图卢兹", "地联溃败")
        add(82, "核对调令伪造事件的受益者和内鬼线索。", "调令伪造", "宴会")
        add(78, "测试重力控制的精度上限，避免误伤友方。", "重力控制", "肉身飞行")
        add(74, "设定魔力输出分级，避免再次摧毁基地设施。", "魔力∞", "百分之十", "10%")

        if latest and re.search(r"[？?]\s*$", latest):
            add(125, "直接回应当前抉择，并要求列出风险与代价。")

        # task 41：fallback 拆成『通用』+『默认柏林剧情专属』两组。
        # 通用 fallback 跨剧本都安全；柏林专属的『要求一份柏林当前势力图...』只在
        # is_default_novel=True 时才推。导入剧本的 UI 不再泄漏。
        fallback_generic = [
            "观察当前场景的可见人物、出口和风险点。",
            "整理当下已知情报，标出最危险变量。",
            "确认下一步目标、可用资源和不可触碰底线。",
            "先和关键人物单独谈话，判断真实立场。",
            "回顾当前剧本开场设定，校准核心动机。",
        ]
        fallback_default_novel = [
            "要求一份柏林当前势力图和行动时限。",
        ]
        fallback = (
            fallback_generic + fallback_default_novel
            if is_default_novel else fallback_generic
        )
        for index, text in enumerate(fallback):
            add(20 - index, text)

        suggestions: list[str] = []
        seen: set[str] = set()
        for _, text in sorted(candidates, reverse=True):
            key = re.sub(r"\W+", "", text)
            if key not in seen:
                suggestions.append(text)
                seen.add(key)
            if len(suggestions) >= 5:
                break
        return suggestions

    # ── 长期记忆与结构化更新 ──────────────────────────────────────
    def set_memory_mode(self, mode: str):
        if mode in {"concise", "normal", "deep"}:
            self.data["memory"]["mode"] = mode

    def add_memory(self, bucket: str, text: str) -> bool:
        text = _clean_item(text)
        if not text:
            return False
        bucket = bucket if bucket in {"resources", "abilities", "facts", "pinned", "notes"} else "notes"
        items = self.data["memory"].setdefault(bucket, [])
        if text not in items:
            items.append(text)
            return True
        return False

    def remove_memory(self, bucket: str, index: int):
        items = self.data["memory"].get(bucket, [])
        if 0 <= index < len(items):
            items.pop(index)

    def set_permission_mode(self, mode: str):
        mode = _normalize_permission_mode(mode)
        self.data.setdefault("permissions", {})["mode"] = mode

    def set_user_variable(self, key: str, value: str, source: str = "user") -> bool:
        key = _clean_item(key)
        value = _clean_item(value)
        if not key or not value:
            return False
        variables = self.data.setdefault("worldline", {}).setdefault("user_variables", {})
        old = variables.get(key, {})
        variables[key] = {
            "value": value,
            "source": source,
            "locked": True,
            "turn": self.data.get("turn", 0),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        return old.get("value") != value

    def apply_set_directive(self, text: str) -> list[str]:
        directive = _extract_set_directive(text)
        if not directive:
            return []

        updates: list[str] = []
        set_key = f"set_{self.data.get('turn', 0) + 1}_{len(self.data.setdefault('worldline', {}).setdefault('user_variables', {})) + 1}"
        if self.set_user_variable(set_key, directive, source="user:/set"):
            updates.append(f"强制设定：{directive}")
        if self.add_memory("pinned", f"玩家强制设定：{directive}"):
            updates.append("固定记忆：玩家强制设定")

        # task 28：调整应用顺序——时间/位置等自动派生的更新先做，
        # 显式 path=value 最后兜底覆盖。
        # 原顺序是先 _extract_set_assignments → 写 world.timeline.current_phase=X，
        # 然后 _extract_set_time_targets → update_time() → _phase_for_time() 又把
        # current_phase 推回『玩家分支』/『柏林暗流篇』，把用户的显式值冲掉。
        # 用户显式 path=value 是硬约束，必须最后跑、最后赢。
        for target in _extract_set_time_targets(directive):
            if target and target != self.data["world"]["time"]:
                self.update_time(target, source="user_set")
                updates.append(f"时间线强制设定：{target}")

        location = _extract_location_override(directive)
        if location:
            self.update_location(location)
            updates.append(f"位置强制设定：{location}")

        for spec in _extract_set_assignments(directive):
            result = self.apply_state_write(spec, source="user:/set", force=True, overwrite=True)
            updates.append(result)

        return updates

    def remove_user_variable(self, key: str):
        variables = self.data.setdefault("worldline", {}).setdefault("user_variables", {})
        variables.pop(_clean_item(key), None)

    def set_last_retrieval(self, text: str):
        self.data["memory"]["last_retrieval"] = text or ""

    def set_last_context(self, context: dict):
        self.data["memory"]["last_context"] = context or {}

    def set_last_context_agent(self, agent: dict):
        self.data["memory"]["last_context_agent"] = agent or {}

    def apply_structured_updates(self, gm_response: str) -> list[str]:
        updates: list[str] = []
        memory = self.data["memory"]
        tags = [_clean_item(raw) for raw in re.findall(r"【([^】]+)】", gm_response or "") if _clean_item(raw)]
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

        for item in tags:
            if not item:
                continue
            key, value = _split_label(item)
            if "当前位置" in key or key in {"地点", "位置"}:
                self.update_location(value)
                updates.append(f"位置：{value}")
            elif is_time_key(key):
                # 待确认中 + GM 正文在询问 → 不要锁；目标如果和 pending 一致就视为复述
                if pending_jump and asking_for_confirm:
                    updates.append(f"时间提案保留待确认：{value}")
                    continue
                self.update_time(value, source="gm")
                updates.append(f"时间线锁定：{value}")
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
                if var_key and self.set_user_variable(var_key, var_value, source="gm"):
                    updates.append(f"用户变量：{var_key}={var_value}")
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
                memory["current_objective"] = value
                updates.append(f"目标：{value}")
            elif "主线任务更新" in key or "主线" in key:
                memory["main_quest"] = value
                memory["current_objective"] = value
                updates.append(f"主线：{value}")
            elif "当前可支配资源" in key or "资源" in key:
                for part in _split_items(value):
                    if self.add_memory("resources", part):
                        updates.append(f"资源：{part}")
            elif "能力" in key or "技能" in key or "掌握" in key:
                if self.add_memory("abilities", value):
                    updates.append(f"能力：{value}")
            elif "关系" in key:
                rel_name, rel_status = _split_relation(value)
                if rel_name and rel_status:
                    self.update_relationship(rel_name, rel_status)
                    updates.append(f"关系：{rel_name} -> {rel_status}")
                elif self.add_memory("facts", item):
                    updates.append(f"事实：{item}")
            elif "获得新身份" in key or "身份" in key or item.startswith("你已获得"):
                if self.add_memory("facts", item):
                    updates.append(f"事实：{item}")
            else:
                if self.add_memory("facts", item):
                    updates.append(f"事实：{item}")

        for value in _extract_explicit_time_updates(gm_response or ""):
            if value == self.data["world"]["time"]:
                continue
            # task 22 兜底：待确认 + 询问语境时，不要把询问句里出现的目标时间当成确认
            if pending_jump and asking_for_confirm:
                updates.append(f"时间提案保留待确认：{value}")
                continue
            self.update_time(value, source="gm")
            updates.append(f"时间线锁定：{value}")

        # 兼容 GM 没有按结构化标签输出、但文本里出现明确状态变化的情况。
        if re.search(r"重力控制|肉身飞行|双脚.*离开|悬浮", gm_response or ""):
            if self.add_memory("abilities", "重力控制/肉身飞行（初步掌握）"):
                updates.append("能力：重力控制/肉身飞行（初步掌握）")
        if "特殊小队" in (gm_response or ""):
            if self.add_memory("resources", "特殊小队建制"):
                updates.append("资源：特殊小队建制")

        memory["last_structured_updates"] = updates[-12:]
        return updates

    def apply_player_directives(self, player_input: str) -> list[str]:
        updates: list[str] = []
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
            return f"状态写入忽略：{spec}"
        # P0 #1：硬黑名单（permissions.* / history.* / schema_version / created_at /
        # is_new）任何 force 都不能突破。原代码 `if not allowed and not force` 让
        # /set permissions.mode=full_access （force=True）直接落地，玩家可一句话
        # 关闭整套权限审批 + 篡改 audit_log + 改 history。
        if _write_path_hard_forbidden(path):
            try:
                audit = self.data.setdefault("permissions", {}).setdefault("audit_log", [])
                audit.append({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "source": source,
                    "path": path,
                    "value": str(value)[:120],
                    "blocked": "hard_forbidden",
                    "turn": self.data.get("turn", 0),
                })
                if len(audit) > 200:
                    self.data["permissions"]["audit_log"] = audit[-200:]
            except Exception:
                pass
            return f"状态写入拒绝（硬黑名单）：{path}"
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
            if overwrite:
                _set_path(self.data, path, items)
            else:
                target = _get_path(self.data, path)
                if not isinstance(target, list):
                    _set_path(self.data, path, [])
                    target = _get_path(self.data, path)
                for item in items:
                    if item and item not in target:
                        target.append(item)
        elif kind == "relationship":
            name = path.split(".", 1)[1]
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

    def _pop_pending_write(self, *, id: str | None = None, index: int | None = None) -> dict | None:
        """按 id 优先 / index fallback 弹出 pending_write。两者都不命中返回 None。"""
        permissions = self.data.setdefault("permissions", {})
        pending = permissions.setdefault("pending_writes", [])
        if id:
            for i, item in enumerate(pending):
                if str(item.get("id", "")) == str(id):
                    return pending.pop(i)
            return None
        if index is not None and 0 <= int(index) < len(pending):
            return pending.pop(int(index))
        return None

    def approve_pending_write(self, index: int | None = None, *, id: str | None = None) -> str:
        item = self._pop_pending_write(id=id, index=index)
        if item is None:
            return "待审写入不存在"
        spec = f"{item.get('path', '')}={item.get('value', '')}"
        return self.apply_state_write(
            spec,
            source=f"{item.get('source', 'gm')}:approved",
            append=bool(item.get("append")),
            overwrite=bool(item.get("overwrite")),
            force=True,
        )

    def reject_pending_write(self, index: int | None = None, *, id: str | None = None) -> str:
        item = self._pop_pending_write(id=id, index=index)
        if item is None:
            return "待审写入不存在"
        permissions = self.data.setdefault("permissions", {})
        permissions.setdefault("audit_log", []).append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "path": item.get("path", ""),
            "value": item.get("value", ""),
            "source": f"{item.get('source', 'gm')}:rejected",
            "mode": _normalize_permission_mode(permissions.get("mode", "full_access")),
            "turn": self.data.get("turn", 0),
        })
        permissions["audit_log"] = permissions["audit_log"][-200:]
        return f"状态写入拒绝：{item.get('path', '')}"

    def add_pending_question(self, text: str, source: str = "gm") -> bool:
        question, options = _parse_question(text)
        if not question:
            return False
        permissions = self.data.setdefault("permissions", {})
        questions = permissions.setdefault("pending_questions", [])
        import secrets as _secrets
        item = {
            "id": _secrets.token_urlsafe(8),
            "question": question,
            "options": options,
            "source": source,
            "turn": self.data.get("turn", 0),
        }
        # 比较时忽略 id（防止"同样的问题"被重复 push）
        def _same(a, b):
            return (a.get("question") == b.get("question")
                    and a.get("options") == b.get("options"))
        if not any(_same(item, q) for q in questions):
            questions.append(item)
            permissions["pending_questions"] = questions[-8:]
            return True
        return False

    def clear_pending_question(self, index: int | None = None, *, id: str | None = None, choice: str | None = None) -> dict | None:
        """同 _pop_pending_write：按 id 优先，index fallback。
        choice：玩家选择的答案，写进 audit_log 留痕（默认 None = 强制跳过）。
        """
        permissions = self.data.setdefault("permissions", {})
        questions = permissions.setdefault("pending_questions", [])
        popped = None
        if id:
            for i, q in enumerate(questions):
                if str(q.get("id", "")) == str(id):
                    popped = questions.pop(i)
                    break
        elif index is not None and 0 <= int(index) < len(questions):
            popped = questions.pop(int(index))
        if popped is not None:
            permissions.setdefault("audit_log", []).append({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "kind": "question_answered",
                "question": popped.get("question", ""),
                "choice": choice or "(skipped)",
                "source": popped.get("source", "gm"),
                "turn": self.data.get("turn", 0),
            })
            permissions["audit_log"] = permissions["audit_log"][-200:]
        return popped

    def _scan_worldline_validation(self, tags: list[str]) -> dict[str, str]:
        status = "none"
        message = ""
        for item in tags:
            key, value = _split_label(item)
            if "设定冲突" in key:
                return {"status": "conflict", "message": value or item}
            if "设定校验" in key:
                if any(word in value for word in ("通过", "满足", "无冲突", "ok", "OK")):
                    status = "passed"
                else:
                    status = "review"
                message = value
        if self.data.get("worldline", {}).get("user_variables") and any("世界线推演" in tag for tag in tags):
            if status == "none":
                return {"status": "review", "message": "推演缺少【设定校验：通过】"}
        return {"status": status, "message": message}

    def _set_worldline_validation(self, status: str, message: str):
        self.data.setdefault("worldline", {})["last_validation"] = {
            "status": status,
            "message": message,
            "turn": self.data.get("turn", 0),
        }

    def _store_worldline_projection(self, text: str, validated: bool) -> bool:
        projection = {
            "text": _clean_item(text),
            "turn": self.data.get("turn", 0),
            "validated": validated,
            "time": self.data.get("world", {}).get("time", ""),
            "variables": copy.deepcopy(self.data.get("worldline", {}).get("user_variables", {})),
        }
        worldline = self.data.setdefault("worldline", {})
        if validated or not worldline.get("user_variables"):
            worldline["last_projection"] = projection
            worldline["pending_projection"] = None
            return True
        worldline["pending_projection"] = projection
        return False

    # ── 便捷属性 ──────────────────────────────────────────────────
    @property
    def is_new(self) -> bool:
        return self.data["is_new"]

    @property
    def player_name(self) -> str:
        return self.data["player"]["name"]

    def update_location(self, loc: str):
        self.data["player"]["current_location"] = loc

    def update_relationship(self, char: str, status: str):
        self.data["relationships"][char] = status

    def update_time(self, time_desc: str, source: str = "system"):
        time_desc = clean_time_value(time_desc)
        if time_desc:
            self.data["world"]["time"] = time_desc
            timeline = self._timeline()
            old = timeline.get("current_label")
            timeline["current_label"] = time_desc
            # task 36：用户曾用 /set 显式 world.timeline.current_phase=X 时（被记入 user_locked_fields），
            # update_time 不要再用 _phase_for_time(time_desc) 推断覆盖。
            # 这条规则同时覆盖 GM 任何【时间：Y】tag 触发的二次 update_time。
            if not self._is_user_locked("world.timeline.current_phase"):
                timeline["current_phase"] = _phase_for_time(time_desc)
            timeline["anchor_state"] = "locked"
            timeline["anchor_source"] = source
            timeline["anchor_turn"] = self.data["turn"]
            timeline["last_transition"] = {
                "from": old,
                "to": time_desc,
                "source": source,
                "turn": self.data["turn"],
            }
            timeline["pending_jump"] = None

    # ── task 36：用户显式写入字段保护注册表 ─────────────────────────
    def _user_locked_fields(self) -> list[str]:
        wl = self.data.setdefault("worldline", {})
        locked = wl.setdefault("user_locked_fields", [])
        if not isinstance(locked, list):
            wl["user_locked_fields"] = []
            locked = wl["user_locked_fields"]
        return locked

    def _is_user_locked(self, path: str) -> bool:
        try:
            return str(path) in self._user_locked_fields()
        except Exception:
            return False

    def mark_user_locked(self, path: str) -> None:
        """记录某个 state path 是用户显式写入的，后续自动派生（如 _phase_for_time）
        不得覆盖。/set / apply_state_write 在 source=user* 或 force=True 时调。"""
        path = str(path or "").strip()
        if not path:
            return
        locked = self._user_locked_fields()
        if path not in locked:
            locked.append(path)

    def request_time_jump(self, target: str, raw: str):
        target = clean_time_value(target)
        if not target:
            return
        timeline = self._timeline()
        timeline["anchor_state"] = "pending_confirmation"
        timeline["pending_jump"] = {
            "from": self.data["world"].get("time", ""),
            "to": target,
            "raw": raw,
            "turn": self.data["turn"],
            "status": "awaiting_gm_confirmation",
        }

    def confirm_time_jump(self, target: str | None = None):
        timeline = self._timeline()
        pending = timeline.get("pending_jump") or {}
        self.update_time(target or pending.get("to") or self.data["world"].get("time", ""), source="gm_confirmed")

    def reject_time_jump(self, reason: str):
        timeline = self._timeline()
        pending = timeline.get("pending_jump")
        timeline["last_transition"] = {
            "from": pending.get("from") if pending else self.data["world"].get("time", ""),
            "to": pending.get("to") if pending else "",
            "source": "gm_rejected",
            "reason": reason,
            "turn": self.data["turn"],
        }
        timeline["anchor_state"] = "locked"
        timeline["pending_jump"] = None

    def _timeline(self) -> dict:
        world = self.data.setdefault("world", {})
        timeline = world.setdefault("timeline", {})
        timeline.setdefault("anchor_state", "locked")
        timeline.setdefault("current_label", world.get("time", ""))
        timeline.setdefault("current_phase", "柏林暗流篇")
        timeline.setdefault("anchor_source", "legacy")
        timeline.setdefault("anchor_turn", self.data.get("turn", 0))
        timeline.setdefault("pending_jump", None)
        timeline.setdefault("last_transition", None)
        return timeline


def _deep_update(target: dict, source: dict):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _latest_assistant_text(history: list[dict]) -> str:
    for msg in reversed(history or []):
        if msg.get("role") == "assistant":
            return str(msg.get("content") or "")
    return ""


def _hit_score(context: str, needles: tuple[str, ...]) -> int:
    return sum(8 for needle in needles if needle and needle in context)


def _player_action_text(text: str) -> str:
    text = _clean_item(text)
    text = re.sub(r"^(?:我|我们|你)\s*", "", text)
    text = re.sub(r"^(先|然后)\s*(?:我|我们|你)\s*", r"\1", text)
    return text.rstrip("。.!！?？")


def _clean_item(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip(" \n\t:：-—")).strip()


def _split_label(text: str) -> tuple[str, str]:
    for sep in ("：", ":"):
        if sep in text:
            key, value = text.split(sep, 1)
            return _clean_item(key), _clean_item(value)
    return text, text


def _split_items(text: str) -> list[str]:
    return [_clean_item(x) for x in re.split(r"[、,，;；]\s*", text) if _clean_item(x)]


def _split_relation(text: str) -> tuple[str, str]:
    for sep in ("：", ":", "->", "→", "-"):
        if sep in text:
            left, right = text.split(sep, 1)
            return _clean_item(left), _clean_item(right)
    return "", ""


def _extract_player_time_directives(text: str) -> list[str]:
    return [d.target for d in detect_time_directives(text or "")]


def _extract_set_directive(text: str) -> str:
    raw = str(text or "").strip()
    match = re.match(r"^/(?:set|设定|设置)\s+(.+)$", raw, re.I | re.S)
    if not match:
        return ""
    return _clean_item(match.group(1))


def _extract_set_assignments(text: str) -> list[str]:
    assignments: list[str] = []
    chunks: list[str] = []
    for segment in re.split(r"[；;\n]+", text or ""):
        chunks.extend(re.split(r"[，,]\s*(?=[^，,。！？；;\n]{1,32}(?:=|：|:))", segment))
    for raw in chunks:
        item = _clean_item(raw)
        if not item or not any(sep in item for sep in ("=", "：", ":")):
            continue
        path, value = _parse_assignment(item)
        if path and value:
            assignments.append(f"{path}={value}")
    return assignments


def _extract_location_override(text: str) -> str:
    patterns = [
        r"(?:当前位置|地点|位置)\s*(?:改为|设为|设置为|切到|跳到|在|位于|=|：|:)\s*([^，。！？\n；;]{1,48})",
        r"(?:现在|当前)\s*(?:在|位于)\s*([^，。！？\n；;]{1,48})",
        r"(?:不在|不是)\s*[^，。！？\n；;]{1,32}[，,；; ]+(?:而是|现在在|应在|改在)\s*([^，。！？\n；;]{1,48})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            value = _clean_item(match.group(1))
            if value:
                return value
    return ""


def _extract_set_time_targets(text: str) -> list[str]:
    values: list[str] = []
    for value in _extract_player_time_directives(text):
        if value not in values:
            values.append(value)
    patterns = [
        r"(?:当前时间线|时间线|当前时间|时间|时点)\s*(?:改为|设为|设置为|锁定为|=|：|:)\s*([^，。！？\n；;]{2,80})",
    ]
    for value in _extract_time_matches(text, patterns):
        if value not in values:
            values.append(value)
    return values


_ASKING_FOR_CONFIRM_PATTERNS = (
    r"是否(?:要|要不要|确认|继续|推进|跳到|跳转)",
    r"请(?:玩家|你)?(?:确认|选择|决定|回答)",
    r"等(?:待|待玩家)?(?:玩家|你)?(?:确认|选择|决定|回答|回应)",
    r"待确认",
    r"awaiting[_ ]?(?:gm|player)?[_ ]?confirm",
    r"pending[_ ]?confirm",
    r"询问玩家",
    r"向玩家提问",
    r"先(?:让|请)?(?:子代理|GM|你)?(?:检查|确认|核对)",
    r"不要(?:直接|立即)?(?:跳过|改写|锁定)",
)


def _gm_is_asking_for_time_confirm(gm_response: str, tags: list[str]) -> bool:
    """task 22 + task 32：判断 GM 这一轮是在询问/标 pending，而不是在锁定时间。

    task 32 真实案例：GM 同时输出了 `【时间跳跃确认：待确认（当前处于 pending_confirmation 状态）】`
    和 `【询问玩家：...】`/`【设定校验：冲突】`。原 task 22 实现一旦看到任何含
    "时间跳跃确认" 的标签就立刻 return False，把后面所有"等待玩家回答""冲突"信号全无视，
    导致主 GM 锁定时间线。

    新规则（更保守）：
      1. 先扫一遍 tags 把信号分类：
         - has_explicit_confirm  ← "时间跳跃确认" 且 value 里没有 pending/待确认 等回退措辞
         - has_pending_signal    ← 任一意图标 OR "时间跳跃确认" 的 value 含 pending/待确认 OR "等待玩家"等
      2. 正文里如果命中 _ASKING_FOR_CONFIRM_PATTERNS → 也算 pending 信号
      3. has_pending_signal 优先于 has_explicit_confirm（user 报告里两者会同时出现）
    """
    blob = gm_response or ""
    has_explicit_confirm = False
    has_pending_signal = False

    pending_value_markers = ("待确认", "未确认", "暂不", "暂缓", "pending", "awaiting")
    pending_tag_keywords = (
        "询问玩家", "向玩家提问", "澄清问题",
        "时间跳跃待确认", "时间提案", "时间冲突",
        "设定冲突", "设定校验",  # 冲突/校验通常表示"先不要写入"
        "等待玩家回答", "等待玩家",
    )

    for tag in tags or []:
        if not tag:
            continue
        # 把 "key：value" 拆开看 value
        if "：" in tag:
            _key, _val = tag.split("：", 1)
        elif ":" in tag:
            _key, _val = tag.split(":", 1)
        else:
            _key, _val = tag, ""
        if "时间跳跃确认" in _key or "时间跳跃确认" in tag:
            val_low = _val.lower()
            # value 里出现"待确认/pending/awaiting"=不是真的同意确认
            if any(m in _val for m in pending_value_markers) or any(m in val_low for m in pending_value_markers):
                has_pending_signal = True
            else:
                has_explicit_confirm = True
            continue
        if any(kw in tag for kw in pending_tag_keywords):
            has_pending_signal = True

    if not has_pending_signal:
        for pat in _ASKING_FOR_CONFIRM_PATTERNS:
            if re.search(pat, blob, flags=re.IGNORECASE):
                has_pending_signal = True
                break

    # 关键决定：pending 信号优先；只有完全没有 pending 信号且有显式 confirm 才视为真确认
    if has_pending_signal:
        return True
    if has_explicit_confirm:
        return False
    # 兼容老返回值：纯正文询问也算 asking
    return False
    return False


def _extract_explicit_time_updates(text: str) -> list[str]:
    patterns = [
        r"(?:时间线|时间|剧情|镜头|场景)\s*(?:跳到|跳转到|快进到|切到|来到|推进到|过渡到|直接进入|进入)\s*([^，。！？\n]{2,40})",
        r"(?:时间来到|时间推进至|时间推进到|时间跳至|时间跳到|镜头切到|画面切到|场景切到|场景来到)\s*([^，。！？\n]{2,40})",
    ]
    return _extract_time_matches(text, patterns)


def _extract_time_matches(text: str, patterns: list[str]) -> list[str]:
    values: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = clean_time_value(match)
            if looks_like_time_value(value) and value not in values:
                values.append(value)
    return values


def _clean_time_value(text: str) -> str:
    return clean_time_value(text)


def _looks_like_time_value(value: str) -> bool:
    return looks_like_time_value(value)


def _format_pending_timeline(pending: dict | None) -> str:
    if not pending:
        return "无"
    return f"{pending.get('from', '')} → {pending.get('to', '')}"


def _phase_for_time(time_desc: str) -> str:
    if any(key in time_desc for key in ("柏林", "图卢兹", "哈布斯堡", "北城", "内城", "基地")):
        return "柏林暗流篇"
    return "玩家分支"


def _normalize_permission_mode(mode: str) -> str:
    text = str(mode or "").strip().lower()
    mapping = {
        # task 53：新增 read_only（对齐 codex 的 suggest 模式）
        "只读": "read_only",
        "只读模式": "read_only",
        "suggest": "read_only",
        "read": "read_only",
        "read_only": "read_only",
        "plan": "read_only",
        "默认权限": "default",
        "default": "default",
        "auto": "auto_review",
        "自动审查": "auto_review",
        "auto_review": "auto_review",
        "review": "auto_review",
        "完全访问权限": "full_access",
        "full": "full_access",
        "full_access": "full_access",
    }
    return mapping.get(text, "full_access")


def _permission_label(mode: str) -> str:
    return {
        "read_only": "只读模式（仅叙事）",
        "default": "默认权限",
        "auto_review": "自动审查",
        "full_access": "完全访问权限",
    }.get(_normalize_permission_mode(mode), "完全访问权限")


# 风险评级。前端 ConfirmStrip 根据 risk 染色（low/medium/high）显示给玩家，
# 让玩家在批量待审时快速看到"高风险动作"先决策。
_HIGH_RISK_PREFIXES = (
    "world.timeline.",       # 改时间线 = 改剧情走向
    "worldline.",            # 世界线变量 = 全局推演规则
    "memory.pinned",         # 固定记忆 = 长期影响
)
_HIGH_RISK_EXACT = {
    "player.name", "player.role", "player.background",
    "world.time",
    "memory.main_quest",
}
_MEDIUM_RISK_PREFIXES = (
    "relationships.",
    "memory.facts",
    "memory.abilities",
    "memory.resources",
)


def _risk_label(path: str) -> str:
    """给路径派一个风险等级，前端按颜色分组显示。"""
    if path in _HIGH_RISK_EXACT or path.startswith(_HIGH_RISK_PREFIXES):
        return "high"
    if path.startswith(_MEDIUM_RISK_PREFIXES):
        return "medium"
    return "low"


def _validation_label(status: str) -> str:
    return {
        "passed": "通过",
        "conflict": "冲突",
        "review": "待审",
        "none": "无",
    }.get(status, status)


def _parse_assignment(text: str) -> tuple[str, str]:
    text = _clean_item(text)
    for sep in ("+=", "=", "：", ":"):
        if sep in text:
            left, right = text.split(sep, 1)
            return _clean_path(left), _clean_item(right)
    return "", text


def _parse_question(value: str) -> tuple[str, list[str]]:
    text = _clean_item(value)
    if not text:
        return "", []
    question = text
    option_text = ""
    if "｜" in text:
        question, option_text = text.split("｜", 1)
    elif "|" in text:
        question, option_text = text.split("|", 1)
    if not option_text:
        match = re.search(r"(.*?)(?:选项|可选|choices?)[:：]\s*(.+)$", text, re.I)
        if match:
            question = match.group(1)
            option_text = match.group(2)
    if option_text:
        option_text = re.sub(r"^(?:选项|可选|choices?)[:：]\s*", "", option_text, flags=re.I)
    options = [_clean_item(x) for x in re.split(r"[、,，/]|(?:\s+or\s+)", option_text) if _clean_item(x)]
    return _clean_item(question), options[:4]


def _clean_path(path: str) -> str:
    path = re.sub(r"\s+", "", str(path).strip())
    aliases = {
        "姓名": "player.name",
        "角色": "player.role",
        "定位": "player.role",
        "背景": "player.background",
        "当前位置": "player.current_location",
        "位置": "player.current_location",
        "当前时间线": "world.time",
        "时间线": "world.time",
        "当前目标": "memory.current_objective",
        "目标": "memory.current_objective",
        "主线": "memory.main_quest",
        "记忆模式": "memory.mode",
        "权限": "permissions.mode",
    }
    return aliases.get(path, path)


_HARD_FORBIDDEN_PATHS = {"schema_version", "history", "created_at", "is_new"}
_HARD_FORBIDDEN_PREFIXES = ("history.", "permissions.")


def _write_path_hard_forbidden(path: str) -> bool:
    """绝对不能写的路径，无论权限模式或 force 标志。

    permissions.* — 用户/GM 自己改权限模式 = 整套审批失效（自我提权）
    history.*     — 改对话历史 = 篡改可见证据
    schema_version / created_at / is_new — 元数据，破坏会让 state 反序列化崩
    """
    return path in _HARD_FORBIDDEN_PATHS or path.startswith(_HARD_FORBIDDEN_PREFIXES)


def _write_path_allowed(path: str, mode: str) -> bool:
    mode = _normalize_permission_mode(mode)
    if _write_path_hard_forbidden(path):
        return False
    # task 53：新增 read_only 模式 — 对齐 codex 的 suggest 模式。
    # 任何 LLM 自动写入都入 pending，不立即应用；玩家完全掌控。
    # /set（force=True）仍能通过，让玩家维护自己的状态。
    if mode == "read_only":
        return False
    if mode == "full_access":
        return True
    if path.startswith("worldline.custom_ui.") or path.startswith("ui."):
        return mode == "full_access"
    allowed = {
        "player.name",
        "player.role",
        "player.background",
        "player.current_location",
        "world.time",
        "world.timeline.current_phase",
        "world.timeline.anchor_state",
        "world.known_events",
        "memory.mode",
        "memory.main_quest",
        "memory.current_objective",
        "memory.resources",
        "memory.abilities",
        "memory.facts",
        "memory.pinned",
        "memory.notes",
    }
    if mode == "auto_review":
        return path in allowed or path.startswith("relationships.") or path.startswith("worldline.user_variables.")
    if mode == "default":
        return path in {
            "player.current_location",
            "world.time",
            "memory.main_quest",
            "memory.current_objective",
            "memory.resources",
            "memory.abilities",
            "memory.facts",
            "world.known_events",
        } or path.startswith("relationships.")
    return False


def _write_path_kind(path: str) -> str:
    if path == "player.current_location":
        return "location"
    if path == "world.time":
        return "time"
    if path in {"world.known_events", "memory.resources", "memory.abilities", "memory.facts", "memory.pinned", "memory.notes"}:
        return "list"
    if path.startswith("relationships."):
        return "relationship"
    if path.startswith("worldline.user_variables."):
        return "user_variable"
    if path.startswith("worldline.custom_ui.") or path.startswith("ui."):
        return "custom_ui"
    return "scalar"


def _set_path(root: dict, path: str, value: Any):
    parts = path.split(".")
    target = root
    for part in parts[:-1]:
        if not isinstance(target.get(part), dict):
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value


def _get_path(root: dict, path: str) -> Any:
    target: Any = root
    for part in path.split("."):
        if not isinstance(target, dict):
            return None
        target = target.get(part)
    return target
