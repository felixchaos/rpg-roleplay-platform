"""
Novel-IP glossary loader.

Private data: rpg/config/novel_glossary.json  (gitignored, author's copy)
Open-source:  rpg/config/novel_glossary.example.json  (placeholder values)

The loader always prefers the private file when present.
"""
import json
from functools import lru_cache
from pathlib import Path

_CFG_DIR = Path(__file__).parent
_PRIVATE = _CFG_DIR / "novel_glossary.json"
_EXAMPLE = _CFG_DIR / "novel_glossary.example.json"


@lru_cache(maxsize=1)
def load_glossary() -> dict:
    """Load glossary; prefer private file, fall back to example."""
    path = _PRIVATE if _PRIVATE.exists() else _EXAMPLE
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_term(key: str, default: str = "") -> str:
    """Dot-path accessor, e.g. get_term('world_terms.realm_main')."""
    parts = key.split(".")
    obj = load_glossary()
    for p in parts:
        obj = obj.get(p, {}) if isinstance(obj, dict) else {}
    return obj if isinstance(obj, str) else default


def get_leak_filter_tokens() -> tuple[str, ...]:
    return tuple(load_glossary().get("leak_filter_tokens", []))


def get_concept_seeds() -> list[str]:
    return list(load_glossary().get("concept_seeds", []))


def get_location_seeds() -> list[str]:
    return list(load_glossary().get("location_seeds", []))


def get_npc_name_seeds() -> list[str]:
    return list(load_glossary().get("npc_name_seeds", []))
