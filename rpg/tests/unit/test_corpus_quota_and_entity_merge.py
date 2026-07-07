"""库扫描(2026-07-07)两修:
①X4 实证(exp2 档 RATH 产物占 80%):关键词语料分池——玩家事件满额,rath_% 独立小配额,
不再挤兑玩家池(RATH 事件仍可召回=设计意图保留);
②335 对运行时同人两卡(「让」↔「让·保罗」):GM kb_upsert_entity 落库前确定性归并
(canon 别名+save 内互包含唯一命中)。源码结构断言。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EP = (ROOT / "kb" / "episodic.py").read_text(encoding="utf-8")
KT = (ROOT / "tools_dsl" / "command_tools_kb.py").read_text(encoding="utf-8")


def test_keyword_corpus_split_pools():
    i = EP.find("def _fetch_keyword_corpus(")
    body = EP[i:EP.find("\ndef ", i + 1)]
    assert body.count("rath\\_%%") >= 1 and "{rath_op}" in body, "玩家/RATH 两池模板"
    assert '"not like"' in body.replace("'", '"') and '"like"' in body.replace("'", '"')
    assert "_RATH_CORPUS_CAP" in body, "RATH 独立配额"
    assert "_RATH_CORPUS_CAP = 300" in EP


def test_kb_upsert_entity_merges_aliases():
    i = KT.find("def _t_kb_upsert_entity(")
    body = KT[i:KT.find("\ndef ", i + 1)]
    assert "canonical_name_for_save" in body, "canon 别名归并(化名→主名)"
    assert "len(_rows or []) == 1" in body, "互包含唯一命中才归并,歧义不猜"
    assert "status='live'" in body
    assert body.count("except Exception") >= 2, "归并失败必须回退原名(非致命)"
