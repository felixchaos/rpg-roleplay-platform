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
CURRENT_SCHEMA_VERSION = 5

# 剧情开始时的初始状态
DEFAULT_STATE = {
    "schema_version": CURRENT_SCHEMA_VERSION,
    # 规则集元信息。RulesEngine 据此选择规则集（dnd5e 为内部命名，对外文案统一使用
    # "5E compatible / 五版规则兼容"）。不引入官方 D&D 品牌内容。
    "ruleset": {
        "id": "dnd5e",
        "mode": "5e_compatible",
        "public_label": "5E compatible / 五版规则兼容"
    },
    # 5E 角色卡。空骨架——只在 rules_bridge.start_module 加载模组时由
    # make_default_character 填入具体 5E 数值。小说 / freeform 存档不应预填 5E
    # 默认值（hp=9/ac=13/属性等），否则前端 5E 面板会在非模组剧本里误显示一套
    # 用不上的角色卡。HP/AC/conditions 等硬数值受 State Gate 保护，仍只能由
    # RulesEngine 写。
    "player_character": {
        "name": "",
        "level": 0,
        "class_name": "",
        "species": "",
        "background": "",
        "abilities": {},
        "proficiency_bonus": 0,
        "skills": {},
        "saves": {},
        "max_hp": 0,
        "hp": 0,
        "ac": 0,
        "inventory": [],
        "conditions": [],
        "features": [],
        "weapons": {}
    },
    # 当前场景（房间）。模组开启后由 RulesEngine/模组加载器填入。
    "scene": {
        "module_id": "",
        "location_id": "",
        "visited_rooms": [],
        "exits": [],
        "visible_clues": [],
        "flags": {}
    },
    # 战斗遭遇状态。所有 hp/initiative 修改必须经 RulesEngine。
    "encounter": {
        "active": False,
        "round": 0,
        "turn_index": 0,
        "initiative_order": [],
        "combatants": [],
        "encounter_id": "",
        "log": []
    },
    # 最近骰子日志（最多保留 50 条）。append-only by RulesEngine。
    "dice_log": [],
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
        # task 74：结构化记忆 dual-write 槽位。MemoryItem schema:
        # {id, kind, text, source, turn, time_label?, characters?, status, ts}
        # 其中 kind ∈ {canon_fact, runtime_fact, hypothesis, user_constraint}。
        # 旧 facts/notes/pinned/resources/abilities 数组继续工作（向后兼容），
        # 新 items 数组是 task 75/76/77/78 的结构化基础。
        "items": [],
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
        # schema v5：5E-compatible 规则相关字段补全。旧存档没有 ruleset / player_character /
        # scene / encounter / dice_log 时，补上 DEFAULT_STATE 的默认值；保持已有字段不变。
        for rules_key in ("ruleset", "player_character", "scene", "encounter", "dice_log"):
            if rules_key not in migrated:
                migrated[rules_key] = copy.deepcopy(DEFAULT_STATE[rules_key])
        # 兼容旧存档：如果 player_character.hp 为空但 max_hp 有值，回填 hp=max_hp。
        pc = migrated.get("player_character") or {}
        if pc and pc.get("max_hp") and not pc.get("hp"):
            pc["hp"] = pc["max_hp"]
        # task 74：旧存档没有 memory.items，补一个空数组（不回填旧 facts，让 task 78
        # 在确定迁移策略后做。这里只是让新写入能落地）。
        memory_block = migrated.setdefault("memory", {})
        memory_block.setdefault("items", [])
        # task 83（codex §7.1 phase B）：MemoryItem 旧数据 backfill 迁移。
        # task 74 只做 dual-write，新写入同时落 legacy facts/notes/pinned/abilities/
        # resources 和 items；旧存档里 memory.items 还是空。这里补一次迁移：
        # 当 items 为空且任一 legacy bucket 有内容，就把 legacy 数组转成 MemoryItem
        # 注入 items（kind=runtime_fact, source=legacy_migration_v1, turn=0——
        # 旧数据无 turn 可考，用 0 标记"档前"）。
        # 保留 legacy 字段不动：codex 哲学是 6 月观察期 dual-read 兼容，迁移阶段
        # 不删旧字段，等 phase C 才决定是否移除。
        # _migrate 是 staticmethod，调不到 self.add_memory_item，所以内联生成 item。
        if not memory_block.get("items"):
            import secrets as _secrets
            legacy_buckets = ("facts", "notes", "pinned", "abilities", "resources")
            has_legacy = any(memory_block.get(b) for b in legacy_buckets)
            if has_legacy:
                backfilled: list[dict] = []
                now_ts = datetime.now().isoformat(timespec="seconds")
                for bucket in legacy_buckets:
                    legacy_arr = memory_block.get(bucket) or []
                    if not isinstance(legacy_arr, list):
                        continue
                    for raw in legacy_arr:
                        text = _clean_item(raw if isinstance(raw, str) else str(raw))
                        if not text:
                            continue
                        backfilled.append({
                            "id": f"mem_{_secrets.token_urlsafe(6)}",
                            "kind": "runtime_fact",
                            "text": text,
                            "source": "legacy_migration_v1",
                            "turn": 0,
                            "ts": now_ts,
                            "status": "active",
                            "legacy_bucket": bucket,
                        })
                if backfilled:
                    memory_block["items"] = backfilled
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
        # ContentPack manifest 解析（小说 / 模组 / freeform）。让前端按 manifest.kind
        # 选择性渲染 5E 规则面板，避免在小说存档里显示模组 UI。
        try:
            from context_providers import resolve_content_pack
            content_pack = resolve_content_pack(self)
        except Exception:
            content_pack = {"kind": "freeform", "context_providers": [], "ruleset": "none"}
        rules_block = {
            "ruleset": copy.deepcopy(self.data.get("ruleset") or {}),
            "player_character": copy.deepcopy(self.data.get("player_character") or {}),
            "scene": copy.deepcopy(self.data.get("scene") or {}),
            "encounter": copy.deepcopy(self.data.get("encounter") or {}),
            "dice_log": list(self.data.get("dice_log") or [])[-30:],
            "content_pack": {
                "id": content_pack.get("id"),
                "kind": content_pack.get("kind"),
                "ruleset": content_pack.get("ruleset"),
                "context_providers": list(content_pack.get("context_providers") or []),
                "retrieval_policy": dict(content_pack.get("retrieval_policy") or {}),
                "gm_policy": dict(content_pack.get("gm_policy") or {}),
                "title": content_pack.get("title"),
            },
        }
        return {**rules_block, **{
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
        }}

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
            # task 74：dual-write 到结构化 memory.items（旧调用方完全无感知）
            # bucket → kind 映射：当前所有旧 bucket 都标 runtime_fact（本局事实）。
            # 后续 task 76/77/78 会按更细粒度区分 canon_fact / hypothesis 等。
            self.add_memory_item(
                text=text,
                kind="runtime_fact",
                source="legacy_add_memory",
                legacy_bucket=bucket,
            )
            return True
        return False

    # task 74：结构化记忆写入入口。callers (extractor / curator / GM JSON op /
    # 玩家手动 add) 可以直接用这个，带上 kind + source + meta，避免被 bucket
    # 字符串语义局限。返回新建条目的 id（用于后续引用/supersede）。
    def add_memory_item(
        self,
        text: str,
        *,
        kind: str = "runtime_fact",
        source: str = "gm",
        time_label: str | None = None,
        characters: list[str] | None = None,
        status: str = "active",
        supersedes: list[str] | None = None,
        legacy_bucket: str | None = None,
    ) -> str:
        text = _clean_item(text)
        if not text:
            return ""
        # 已知 kind 白名单（task 74 起步只支持 4 种核心，task 76 可扩展）
        valid_kinds = {"canon_fact", "runtime_fact", "hypothesis", "user_constraint"}
        if kind not in valid_kinds:
            kind = "runtime_fact"
        import secrets as _secrets
        item = {
            "id": f"mem_{_secrets.token_urlsafe(6)}",
            "kind": kind,
            "text": text,
            "source": source,
            "turn": int(self.data.get("turn", 0)),
            "ts": datetime.now().isoformat(timespec="seconds"),
            "status": status,
        }
        if time_label:
            item["time_label"] = time_label
        if characters:
            item["characters"] = list(characters)
        if supersedes:
            item["supersedes"] = list(supersedes)
        if legacy_bucket:
            item["legacy_bucket"] = legacy_bucket
        items = self.data.setdefault("memory", {}).setdefault("items", [])
        items.append(item)
        # 软上限：避免无限增长（保留最新 500 条；老 item 不删除元数据，由
        # task 78 migration 阶段决定永久策略）
        if len(items) > 500:
            self.data["memory"]["items"] = items[-500:]
        return item["id"]

    def remove_memory(self, bucket: str, index: int):
        items = self.data["memory"].get(bucket, [])
        if 0 <= index < len(items):
            items.pop(index)

    # task 75：hypothesis 独立 namespace。codex §1 强调"推测不能混进事实"——
    # 这里给 hypothesis 提供专门的写/查/确认/拒绝 API，让 context_engine
    # 渲染层能明显区分"推测"和"事实"。
    def add_hypothesis(
        self,
        text: str,
        *,
        source: str = "gm",
        time_label: str | None = None,
        characters: list[str] | None = None,
    ) -> str:
        """添加一条推测/计划/草稿。返回 item id 供后续 confirm/reject 引用。"""
        return self.add_memory_item(
            text=text,
            kind="hypothesis",
            source=source,
            time_label=time_label,
            characters=characters,
        )

    def list_active_hypotheses(self) -> list[dict]:
        """列出当前 active 的推测（按 turn 倒序，最多 20 条）。"""
        items = self.data.get("memory", {}).get("items", []) or []
        out = [
            i for i in items
            if i.get("kind") == "hypothesis" and i.get("status") == "active"
        ]
        out.sort(key=lambda x: x.get("turn", 0), reverse=True)
        return out[:20]

    def confirm_hypothesis(self, item_id: str, *, source: str = "user") -> bool:
        """把推测升级成 runtime_fact。原 hypothesis status='superseded'，
        新建一条 kind=runtime_fact 引用它的 id（supersedes 链）。"""
        items = self.data.get("memory", {}).get("items", []) or []
        target = next((i for i in items if i.get("id") == item_id), None)
        if not target or target.get("kind") != "hypothesis":
            return False
        if target.get("status") != "active":
            return False
        target["status"] = "superseded"
        self.add_memory_item(
            text=target.get("text", ""),
            kind="runtime_fact",
            source=source,
            time_label=target.get("time_label"),
            characters=target.get("characters"),
            supersedes=[item_id],
        )
        return True

    def reject_hypothesis(self, item_id: str) -> bool:
        """把推测标记为 rejected，不再出现在 active 列表。"""
        items = self.data.get("memory", {}).get("items", []) or []
        target = next((i for i in items if i.get("id") == item_id), None)
        if not target or target.get("kind") != "hypothesis":
            return False
        target["status"] = "rejected"
        return True

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

    def apply_structured_updates(self, gm_response: str, *, skip_regex_fallback: bool = False) -> list[str]:
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
        # 例外：时间跳跃 pending_jump 状态机（confirm/reject）+ 询问玩家 +
        # 设定校验 + 世界线推演 这些没有 path 的"控制流"标签保持原路径，
        # 因为它们不是字段写入而是流程信号。
        def _gm_write_via_gate(path: str, value, *, append=False, overwrite=False, label_for_update: str = "") -> None:
            """统一权限闸门。所有"写状态字段"的 GM 标签都走这里。

            返回的 updates 文案策略：
            - 真生效（apply 返回"状态写入：..."）→ 用 label_for_update（友好文案，
              如 "位置：北港码头"）
            - 入 pending / 被拒 → 用 apply 的原始返回（"状态写入待审：..." / 拒绝），
              让前端 LeftRail 能清楚显示"哪些写入被挡了"，避免假成功 UI
            """
            spec = f"{path}={value}"
            applied = self.apply_state_write(spec, source="gm", append=append, overwrite=overwrite)
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

        # 兼容 GM 没有按结构化标签输出、但文本里出现明确状态变化的情况。
        # task 69：extractor 开启时（task 62 的两步式 GM），第二步会从叙事完整
        # 抽 JSON ops，这里的「作者写死 regex 兜底」会和 extractor 双写同一字段。
        # extractor enabled → 跳过 regex；关闭时保留兜底向后兼容（单步 GM 走旧路径）。
        if not skip_regex_fallback:
            if re.search(r"重力控制|肉身飞行|双脚.*离开|悬浮", gm_response or ""):
                if self.add_memory("abilities", "重力控制/肉身飞行（初步掌握）"):
                    updates.append("能力：重力控制/肉身飞行（初步掌握）")
            if "特殊小队" in (gm_response or ""):
                if self.add_memory("resources", "特殊小队建制"):
                    updates.append("资源：特殊小队建制")

        # task 55：JSON 协议处理。op = "set"/"append"/"overwrite"/"question"
        def _log_op_parse_error(reason: str, op_dump):
            try:
                audit = self.data.setdefault("permissions", {}).setdefault("audit_log", [])
                audit.append({
                    "ts": datetime.now().isoformat(timespec="seconds"),
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
                # task 75：hypothesis op 路由到独立 namespace，不污染 facts
                if kind == "hypothesis":
                    text = op.get("text") or op.get("value") or ""
                    if not text:
                        _log_op_parse_error("hypothesis op 缺 'text' 或 'value' 字段", op)
                        updates.append(f"JSON op 忽略（推测缺文本）：{op}")
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
                if kind == "confirm_hypothesis":
                    hid = op.get("id") or ""
                    if hid and self.confirm_hypothesis(hid, source="gm:json"):
                        updates.append(f"推测确认：{hid}")
                    else:
                        updates.append(f"推测确认失败（id 不存在或非 active）：{hid}")
                    continue
                if kind == "reject_hypothesis":
                    hid = op.get("id") or ""
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
                    "ts": datetime.now().isoformat(timespec="seconds"),
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
        # 5E-compatible：受规则引擎管理的硬数值（HP/AC/initiative/dice_log）只能由
        # RulesEngine 修改。LLM/GM 自由写入或用户 /set 都拒绝并记入 audit。
        if _write_path_rules_managed(path) and not str(source or "").startswith("rules_engine"):
            try:
                audit = self.data.setdefault("permissions", {}).setdefault("audit_log", [])
                audit.append({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "source": source,
                    "path": path,
                    "value": str(value)[:120],
                    "blocked": "rules_managed",
                    "hint": "受规则引擎管理的硬数值（HP/AC/initiative/dice_log）只能由 RulesEngine 写入",
                    "turn": self.data.get("turn", 0),
                })
                if len(audit) > 200:
                    self.data["permissions"]["audit_log"] = audit[-200:]
            except Exception:
                pass
            return f"状态写入拒绝（rules_managed）：{path}"
        # 规则模组运行时，玩家所在房间由 RulesEngine / rules_bridge 的移动结果维护。
        # GM 只能叙事，不能把自然语言里的“当前位置”反写成另一套状态。
        if _write_path_module_managed(path) and _module_scene_active(self.data) and str(source or "").startswith("gm"):
            try:
                audit = self.data.setdefault("permissions", {}).setdefault("audit_log", [])
                audit.append({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "source": source,
                    "path": path,
                    "value": str(value)[:120],
                    "blocked": "module_managed",
                    "hint": "规则模组运行时当前位置由 RulesEngine 房间状态维护，GM 不得写入",
                    "turn": self.data.get("turn", 0),
                })
                if len(audit) > 200:
                    self.data["permissions"]["audit_log"] = audit[-200:]
            except Exception:
                pass
            return f"状态写入拒绝（module_managed）：{path}"
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
        # audit
        try:
            audit = self.data.setdefault("permissions", {}).setdefault("audit_log", [])
            audit.append({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "source": "rules_engine",
                "ops": len(ops or []),
                "reason": reason,
                "turn": self.data.get("turn", 0),
            })
            self.data["permissions"]["audit_log"] = audit[-200:]
        except Exception:
            pass
        return applied

    def append_dice_log(self, entry: dict, cap: int = 50) -> None:
        """RulesEngine 唯一允许的 dice_log 写入入口。"""
        log = self.data.setdefault("dice_log", [])
        log.append(entry)
        if len(log) > cap:
            del log[: len(log) - cap]

    def set_player_character(self, character: dict) -> None:
        """初始化或替换 player_character。仅在模组开局 / 新游戏使用。"""
        self.data["player_character"] = copy.deepcopy(character or {})

    def update_player_hp(self, new_hp: int, reason: str = "") -> int:
        """RulesEngine 专用：直接设定玩家 HP，不超过 max_hp。"""
        pc = self.data.setdefault("player_character", {})
        max_hp = int(pc.get("max_hp", 0) or 0)
        new_hp = max(0, min(int(new_hp), max_hp if max_hp > 0 else int(new_hp)))
        pc["hp"] = new_hp
        return new_hp

    def damage_player(self, amount: int, reason: str = "") -> int:
        pc = self.data.setdefault("player_character", {})
        cur = int(pc.get("hp", 0) or 0)
        actual = max(0, int(amount))
        pc["hp"] = max(0, cur - actual)
        return cur - pc["hp"]

    def set_encounter(self, encounter: dict) -> None:
        """初始化或替换 encounter 状态。RulesEngine 专用。"""
        self.data["encounter"] = copy.deepcopy(encounter or {})

    def clear_encounter(self) -> None:
        self.data["encounter"] = copy.deepcopy(DEFAULT_STATE["encounter"])

    def set_scene(self, scene: dict) -> None:
        self.data["scene"] = copy.deepcopy(scene or {})

    def mark_scene_visit(self, location_id: str) -> None:
        scene = self.data.setdefault("scene", {})
        visited = scene.setdefault("visited_rooms", [])
        if location_id and location_id not in visited:
            visited.append(location_id)

    def set_scene_flag(self, flag: str, value=True) -> None:
        scene = self.data.setdefault("scene", {})
        flags = scene.setdefault("flags", {})
        flags[flag] = value

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

    def add_pending_question(self, text: str, source: str = "gm", options: list | None = None) -> bool:
        if options is None:
            question, parsed_options = _parse_question(text)
        else:
            question = _clean_item(text)
            parsed_options = [_clean_item(str(x)) for x in options if _clean_item(str(x))]
        if not question:
            return False
        permissions = self.data.setdefault("permissions", {})
        questions = permissions.setdefault("pending_questions", [])
        import secrets as _secrets
        item = {
            "id": _secrets.token_urlsafe(8),
            "question": question,
            "options": parsed_options[:4],
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


_JSON_STATE_OPS_RE = re.compile(
    r"```(?:json|state-ops|state)?\s*\n?\s*"
    r"(\{[\s\S]*?\}|\[[\s\S]*?\])"
    r"\s*\n?```",
    re.MULTILINE,
)


def _extract_json_state_ops(text: str) -> tuple[list[dict], str]:
    """task 55：从 GM 输出里剥离 ```json {...}``` 状态操作块，返回 (ops_list, stripped_text)。

    现代 LLM (Claude 3.5+ / GPT-4o / Gemini 2.0+) 对 JSON 比对自定义中文模板
    熟悉得多，错误率低 1-2 个数量级。GM 可选地输出：

        ```json
        [
          {"op": "set", "path": "player.current_location", "value": "北港"},
          {"op": "append", "path": "memory.resources", "value": "怀表"},
          {"op": "question", "question": "去哪", "options": ["东", "西"]}
        ]
        ```

    单个对象（不在数组里）也接受。stripped_text 是剥离 JSON 块后的剩余正文，
    供 【】 协议继续抽。两种协议共存，模型自选熟悉的。
    """
    if not text or "```" not in text:
        return [], text or ""
    ops: list[dict] = []
    stripped_parts: list[str] = []
    last_end = 0
    for m in _JSON_STATE_OPS_RE.finditer(text):
        # 把上一个匹配尾到本次开始之间的文本保留
        stripped_parts.append(text[last_end:m.start()])
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, dict):
                # 启发：必须看着像 state op（含 op 或 path）才接受
                if "op" in parsed or "path" in parsed or "question" in parsed:
                    ops.append(parsed)
                else:
                    # 不是 state op JSON，保留原文（可能是其它结构化数据）
                    stripped_parts.append(m.group(0))
            elif isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and ("op" in item or "path" in item or "question" in item):
                        ops.append(item)
        except Exception:
            # 解析失败保留原 fence 让玩家看到
            stripped_parts.append(m.group(0))
        last_end = m.end()
    stripped_parts.append(text[last_end:])
    return ops, "".join(stripped_parts)


def strip_json_state_ops(text: str) -> str:
    """Return player-facing narrative text without JSON state-op fences."""
    return _extract_json_state_ops(text or "")[1].strip()


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


# 5E-compatible 规则受控字段。这些路径只能由 RulesEngine（source="rules_engine"
# 或 source 以 "rules_engine" 开头）改写。GM 自由写入 / 用户 /set 都被拒绝并 audit，
# 防止 LLM 自行编造 HP/AC/initiative 等硬数值。
_RULES_MANAGED_PATHS = {
    "player_character.hp",
    "player_character.max_hp",
    "player_character.ac",
    "encounter.active",
    "encounter.round",
    "encounter.turn_index",
    "encounter.initiative_order",
    "encounter.combatants",
    "encounter.encounter_id",
    "encounter.log",
    "dice_log",
}
_RULES_MANAGED_PREFIXES = (
    "encounter.combatants.",
    "encounter.initiative_order.",
    "dice_log.",
    "player_character.conditions",  # 条件由 rules 触发（中毒等）
)

_MODULE_MANAGED_PATHS = {
    "player.current_location",
}


def _write_path_hard_forbidden(path: str) -> bool:
    """绝对不能写的路径，无论权限模式或 force 标志。

    permissions.* — 用户/GM 自己改权限模式 = 整套审批失效（自我提权）
    history.*     — 改对话历史 = 篡改可见证据
    schema_version / created_at / is_new — 元数据，破坏会让 state 反序列化崩
    """
    return path in _HARD_FORBIDDEN_PATHS or path.startswith(_HARD_FORBIDDEN_PREFIXES)


def _write_path_rules_managed(path: str) -> bool:
    """5E 规则受控路径。任何非 rules_engine 来源写入都会被 State Gate 拒绝。"""
    if path in _RULES_MANAGED_PATHS:
        return True
    return any(path == prefix.rstrip(".") or path.startswith(prefix) for prefix in _RULES_MANAGED_PREFIXES)


def _write_path_module_managed(path: str) -> bool:
    return path in _MODULE_MANAGED_PATHS


def _module_scene_active(data: dict) -> bool:
    try:
        return bool((data.get("scene") or {}).get("module_id"))
    except Exception:
        return False


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
