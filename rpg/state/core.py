"""state/core.py — GameState class + module constants (DEFAULT_STATE, SAVE_FILE, etc.)"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from state._mixins import ApplyOpsMixin, PendingMixin, RulesGameplayMixin
from state.extractors import (
    _extract_explicit_time_updates,
    _extract_location_override,
    _extract_player_time_directives,
    _extract_set_assignments,
    _extract_set_directive,
    _extract_set_time_targets,
    _extract_time_matches,
)
from state.json_ops import _extract_json_state_ops, strip_json_state_ops
from state.labels import _risk_label, _validation_label
from state.parsers import (
    _clean_item,
    _parse_assignment,
    _parse_question,
    _split_items,
    _split_label,
    _split_relation,
)
from state.path_ops import (
    _HARD_FORBIDDEN_PATHS,
    _HARD_FORBIDDEN_PREFIXES,
    _MODULE_MANAGED_PATHS,
    _RULES_MANAGED_PATHS,
    _RULES_MANAGED_PREFIXES,
    _clean_path,
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
from state.time_ops import (
    _clean_time_value,
    _format_pending_timeline,
    _gm_is_asking_for_time_confirm,
    _looks_like_time_value,
    _phase_for_time,
)

# ── helpers imported from sub-modules ──────────────────────────────────────
from state.utils import _deep_update, _hit_score, _latest_assistant_text, _player_action_text
from timeline_state import (
    clean_time_value,
    detect_time_directives,
    is_time_key,
    looks_like_time_value,
)

BASE = Path(__file__).parent.parent  # state/core.py 比原 state.py 深一层,SAVE_FILE 必须回到 rpg/saves/
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
    # 当前在场轻量实体索引(NPC / 敌人 / 临时角色)。NOT 完整角色卡 —
    # 完整角色卡是长期资产,在 user_cards db 表里;active_entities 是运行时索引,
    # 当 GM 在场景里遇到 / 引入角色,先进这里;真正重要才手动 promote 成 user_card
    # (只在平台『角色卡』页操作,游戏内不创建)。
    # 来源 (source 字段):
    #   "room_data"       — module 当前房间 npcs/enemies (自动同步)
    #   "encounter"       — RulesEngine 启动的合法遭遇里 combatants
    #   "gm_provisional"  — GM 正文提及的新角色 (待玩家确认 / 规则验证)
    # 字段:
    #   id, name, kind ("npc"|"enemy"|"ally"|"unknown"),
    #   role, disposition ("friendly"|"hostile"|"neutral"|"unknown"),
    #   source, first_seen_turn, last_seen_turn, location, status,
    #   confidence (0..1), card_id?, stat_block_id?
    "active_entities": [],
    # 通用 RPG 底座：DEFAULT_STATE 不再写入任何具体剧本（如《我蕾穆丽娜不爱你》的柏林开场）
    # 的人名/地名/事件。剧本/模组 opening 由 workspace._apply_script_opening、
    # rules_bridge.start_module、context_providers/module_adventure 等加载器填入。
    # 默认仅保留结构骨架与中性默认值。
    "player": {
        "name": "",
        "role": "",          # 玩家选择的角色定位
        "background": "",    # 玩家自定义背景
        "current_location": ""
    },
    "world": {
        "time": "",
        "timeline": {
            "anchor_state": "locked",
            "current_label": "",
            "current_phase": "",
            "anchor_source": "initial",
            "anchor_turn": 0,
            "pending_jump": None,
            "last_transition": None,
        },
        "known_events": []
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
        # 通用 RPG 底座：current_objective 默认空，由剧本/模组 opening 写入。
        "current_objective": "",
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


@lru_cache(maxsize=1)
def _load_script_overrides() -> dict:
    """加载所有 rpg/modules/_script_overrides/*.json,返回 {script_key: data}。"""
    overrides_dir = BASE / "modules" / "_script_overrides"
    out: dict[str, dict] = {}
    if not overrides_dir.is_dir():
        return out
    for f in sorted(overrides_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            key = data.get("script_key")
            if key:
                out[key] = data
        except Exception:
            continue
    return out


def _detect_active_script_key(context: str) -> str | None:
    """根据 context 中出现的 signature tokens 推测当前剧本 key。返回 None 表示未识别 (用通用 fallback)。"""
    for key, ov in _load_script_overrides().items():
        tokens = ov.get("novel_signature_tokens") or []
        if tokens and any(t in context for t in tokens):
            return key
    return None


class GameState(ApplyOpsMixin, RulesGameplayMixin, PendingMixin):
    def __init__(self, data: dict):
        self.data = self._migrate(data)

    # ── 读档 / 新档 ────────────────────────────────────────────────
    @classmethod
    def load_or_new(cls) -> GameState:
        SAVE_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SAVE_FILE.exists():
            try:
                with open(SAVE_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                data = cls._migrate(data)
                print(f"[读档] {data['player']['name']} · 第{data['turn']}回合 · {data['world']['time']}")
                return cls(data)
            except Exception as e:
                print(f"[读档失败：{e}，开始新游戏]")
        return cls.new()

    @classmethod
    def new(cls) -> GameState:
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
        # 通用 RPG 底座：旧存档默认 phase 不再硬编码《我蕾穆丽娜不爱你》的『柏林暗流篇』。
        # 真实剧本/模组 opening 会在 _apply_script_opening 写入对应阶段；遗留 Berlin 存档
        # 已经把『柏林暗流篇』写在自己的 state_snapshot 里，不依赖 setdefault。
        timeline.setdefault("current_phase", "")
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
    def save(self, target_path: Path | str | None = None) -> str:
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
            # 三层人物系统:轻量在场实体索引 (NPC / 敌人 / 临时角色)。
            # 前端 PanelCharacters 读这个,不再依赖 GM 写 relationships。
            "active_entities": list(self.data.get("active_entities") or []),
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

        # 识别当前剧本 key (None 表示未识别 → 用通用 fallback)
        active_script_key = _detect_active_script_key(context)
        active_overrides = _load_script_overrides().get(active_script_key, {}) if active_script_key else {}

        # task 86：剧情位置一致性检查。
        # 历史 memory.facts / pinned / known_events 可能积累了之前柏林剧情的事实
        # （比如"扎兹巴鲁姆"、"特殊小队"），跨剧情跳跃到月球/火星后这些 needle 仍
        # 会命中,但建议内容含"柏林城内/柏林战役"等当前位置已不再适用的地理词,
        # 让玩家困惑。这里检查**当前剧情位置**是否仍在 setting lock 范围内,决定是否允许含
        # 锁定地理词的建议出现。当前位置以 player.current_location / world.time /
        # timeline.current_phase / timeline.current_label 为准（这些是"此时此刻"，
        # 不受过往记忆污染）。
        _setting_blob = " ".join([
            str(player.get("current_location") or ""),
            str(world.get("time") or ""),
            str((world.get("timeline") or {}).get("current_phase") or ""),
            str((world.get("timeline") or {}).get("current_label") or ""),
        ])
        _setting_lock_tokens = active_overrides.get("setting_lock_tokens") or []
        _setting_is_active = any(tok in _setting_blob for tok in _setting_lock_tokens) if _setting_lock_tokens else True
        _setting_locked_text_tokens = tuple(active_overrides.get("setting_locked_text_tokens") or ())

        candidates: list[tuple[int, str]] = []

        def add(score: int, text: str, *needles: str):
            if needles and not any(n in context for n in needles):
                return
            # setting lock: 含锁定 token 的文本只在当前剧情匹配 setting 时允许
            if not _setting_is_active and _setting_locked_text_tokens and any(
                tok in text for tok in _setting_locked_text_tokens
            ):
                return
            candidates.append((score + _hit_score(context, needles), _player_action_text(text)))

        # 从 overrides 加载剧本专属规则 (替代原 11 条硬编码)
        for rule in active_overrides.get("rules") or []:
            add(rule["score"], rule["text"], *rule.get("needles", []))

        if latest and re.search(r"[？?]\s*$", latest):
            add(125, "直接回应当前抉择，并要求列出风险与代价。")

        # 通用 fallback 跨剧本都安全；剧本专属 fallback 从 overrides 加载
        fallback_generic = [
            "观察当前场景的可见人物、出口和风险点。",
            "整理当下已知情报，标出最危险变量。",
            "确认下一步目标、可用资源和不可触碰底线。",
            "先和关键人物单独谈话，判断真实立场。",
            "回顾当前剧本开场设定，校准核心动机。",
        ]
        fallback_novel = active_overrides.get("default_novel_fallbacks") or []
        fallback = fallback_generic + fallback_novel
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
            # task 86：guard 独立标志。GM 在响应中可能调 update_time(source="gm")
            # 把 last_transition.source 改为 "gm"，让 detect_time_jump_violations
            # 错过本回合的 user_set 跳跃检测。这里只在 source=="user_set" 时记录
            # 跳跃回合号，**不**让后续非 user_set 的 update_time 清掉它——
            # 这样 guard 可以可靠判断"本回合是否发生过用户硬跳跃"。
            if source == "user_set":
                timeline["user_set_jump_turn"] = self.data["turn"]

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
        # 通用 RPG 底座：不再硬编码《我蕾穆丽娜不爱你》的『柏林暗流篇』；阶段由
        # 剧本/模组 opening、/set、time_directive 等显式来源写入。
        timeline.setdefault("current_phase", "")
        timeline.setdefault("anchor_source", "legacy")
        timeline.setdefault("anchor_turn", self.data.get("turn", 0))
        timeline.setdefault("pending_jump", None)
        timeline.setdefault("last_transition", None)
        return timeline


