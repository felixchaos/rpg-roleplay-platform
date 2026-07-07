"""群反馈(斗破档)运行时名字治理三闸:
①GM 关系写入过 canon 别名归并(云芝→云韵,否则人物面板同人两卡);
②史官确证未揭示实体降级(不抄 identity/summary,防"药老徒弟"真身份进 KB 放大);
③GM 上下文注入命名禁区(后文角色名只给名字不给身份)。源码结构断言。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALIAS = (ROOT / "kb" / "alias.py").read_text(encoding="utf-8")
OPS = (ROOT / "state" / "_mixins" / "apply_ops.py").read_text(encoding="utf-8")
SAVEKB = (ROOT / "kb" / "save_kb.py").read_text(encoding="utf-8")
RET = (ROOT / "retrieval.py").read_text(encoding="utf-8")


def test_alias_resolver_uses_canon_aliases():
    assert "aliases ? %s" in ALIAS, "jsonb 别名命中查询"
    assert "name <> %s" in ALIAS, "主名本身不算别名(避免自归并空转)"
    assert "lru_cache" in ALIAS


def test_relationship_write_resolves_alias():
    i = OPS.find('elif "关系" in key:')
    body = OPS[i:i + 900]
    assert "canonical_name_for_save" in body, "关系键名写入前必须过别名归并"
    assert "except Exception" in body, "归并失败必须回退原名(非致命)"


def test_premature_entity_confirmation_downgraded():
    i = SAVEKB.find("def maintain_structured_kb(")
    body = SAVEKB[i:i + 4200]
    assert "_premature" in body
    assert '"premature": True' in body, "未揭示实体确证需打标"
    assert 'if not _premature:' in body, "premature 不抄 identity/background"
    assert "get_progress_window" in body, "进度用权威读取器"


def test_naming_ban_injected_names_only():
    i = RET.find("命名禁区")
    body = RET[i:i + 1600]
    assert i != -1
    assert "type='character'" in body
    assert "first_revealed_chapter,0) > %s" in body, "只禁未揭示的"
    assert "importance,0) >= 40" in body and "limit 30" in body, "名单有界"
    assert "select name from" in body, "只查名字,绝不带身份字段"
