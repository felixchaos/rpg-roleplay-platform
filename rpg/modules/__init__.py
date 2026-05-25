"""
rpg.modules — 冒险模组数据目录。每个子目录为一个模组（pure JSON + markdown）。
"""
from pathlib import Path
import json

MODULES_DIR = Path(__file__).parent


def list_modules() -> list[dict]:
    """枚举可用模组（读 module.json 摘要）。"""
    out: list[dict] = []
    for sub in sorted(MODULES_DIR.iterdir()):
        if not sub.is_dir():
            continue
        manifest = sub / "module.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        # ruleset：兼容旧 dict 格式与新 string 格式
        rs = data.get("ruleset_meta") or data.get("ruleset")
        if isinstance(rs, str):
            rs = {"id": rs, "mode": rs, "public_label": rs}
        out.append({
            "id": data.get("id") or sub.name,
            "kind": data.get("kind", "module_adventure"),
            "name": data.get("name"),
            "name_cn": data.get("name_cn"),
            "tagline": data.get("tagline"),
            "ruleset": rs,
            "context_providers": list(data.get("context_providers") or []),
            "level_range": data.get("level_range"),
            "estimated_minutes": data.get("estimated_minutes"),
            "path": str(sub),
        })
    return out


def load_module(module_id: str) -> dict:
    """加载一个模组的所有 JSON/markdown 数据。"""
    sub = MODULES_DIR / module_id
    if not sub.exists():
        raise FileNotFoundError(f"未知模组：{module_id}")

    def _read_json(name: str) -> dict | list | None:
        p = sub / name
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    def _read_text(name: str) -> str:
        p = sub / name
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8")

    bundle = {
        "id": module_id,
        "manifest": _read_json("module.json") or {},
        "rooms": _read_json("rooms.json") or [],
        "encounters": _read_json("encounters.json") or [],
        "npcs": _read_json("npcs.json") or [],
        "loot": _read_json("loot.json") or [],
        "worldbook": _read_json("worldbook.json") or {},
        "opening": _read_text("opening.md"),
    }
    return bundle
