"""世界树历史祖先裁剪(O(n²)→O(n))。源码结构断言(功能验证=生产 352 e2e+268 备份逐条比对)。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HE = (ROOT / "platform_app" / "branches" / "history_elide.py").read_text(encoding="utf-8")
REFS = (ROOT / "platform_app" / "branches" / "refs.py").read_text(encoding="utf-8")
DEL = (ROOT / "platform_app" / "branches" / "deletion.py").read_text(encoding="utf-8")


def test_elide_protects_rebuild_donors():
    body = HE[HE.find("def elide_save("):]
    assert "protected_commit_ids" in HE
    assert "active_commit_id" in HE and "target_commit_id" in HE, "活跃头+全部 ref 目标受保护"
    assert "exists (select 1 from branch_commits k" in body, "只裁有后代的(叶子=供体必须全量)"
    assert "not (c.state_snapshot ? '_history_elided')" in body, "幂等:已裁剪不重裁"
    assert "MIN_HISTORY_TO_ELIDE = 20" in HE


def test_hydrate_fails_loud_without_donor():
    body = HE[HE.find("def hydrate_commit_state("):HE.find("def unelide_commit(")]
    assert "raise RuntimeError" in body, "无足量供体必须炸而非静默丢历史"
    assert "order by depth asc limit 1" in body, "取最近供体"
    assert "hist[:n]" in body, "严格前 N 条前缀重建"


def test_all_restore_paths_hydrate():
    i = REFS.find("def _write_checkout(")
    assert "hydrate_commit_state" in REFS[i:i + 1200], "commit→工作树单一入口必须 hydrate"
    assert "unelide_commit" in REFS[i:i + 1200], "恢复为活跃头必须 un-elide 回写(成为新供体)"
    for fn in ("delete_subtree", "rollback_to_message", "rewind_last_round"):
        j = DEL.find(f"def {fn}(")
        seg = DEL[j:DEL.find("\ndef ", j + 1)]
        assert "hydrate_commit_state" in seg, f"{fn} 的恢复快照必须 hydrate(file 后端用它写文件)"
