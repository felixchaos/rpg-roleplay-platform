"""锚点确定性签名匹配层(268 实锤「演过了没验收,多次触发」的回归锁)。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

import sys

sys.path.insert(0, str(ROOT))
from gm_serving.anchor_signature import (  # noqa: E402
    MAX_DET_MARKS_PER_TURN,
    deterministic_hits,
    extract_signatures,
    match_anchor_in_text,
)

# 行者 268 的真实场景形状:锚点 summary 与 GM 重演正文高度逐字重合。
_ANCHOR = ('张杰眯着眼睛说道:"一是创造人物,根据主神提供的条件,不限种族、年龄、性别、'
           '能力,理论上连神都可以创造,不过有隐藏限制。"')
_TURN_REPLAY = ('…寒暄,"一是创造人物——根据主神提供的条件,不限种族、年龄、性别、能力,'
                '似乎连神都可以造。昨天我试了试,其实有隐藏限制。"他朝张杰身边的女人看了一眼。')
_TURN_UNRELATED = '郑吒推开石门,外面是一望无际的沙漠,风卷着细沙打在护目镜上。'


def test_extract_signatures_prefers_quoted_dialogue():
    sigs = extract_signatures(_ANCHOR)
    assert any('创造人物' in g for g in sigs)
    assert any('不限种族' in g for g in sigs)
    assert all(5 <= len(g) <= 24 for g in sigs)


def test_replay_text_hits():
    m = match_anchor_in_text(_ANCHOR, _TURN_REPLAY)
    assert m['hit'] and m['hits'] >= 2, m


def test_unrelated_text_misses():
    m = match_anchor_in_text(_ANCHOR, _TURN_UNRELATED)
    assert not m['hit'], m


def test_empty_inputs_safe():
    assert not match_anchor_in_text('', _TURN_REPLAY)['hit']
    assert not match_anchor_in_text(_ANCHOR, '')['hit']
    assert deterministic_hits([], _TURN_REPLAY) == []


def test_deterministic_hits_caps_and_shapes():
    pending = [
        {'anchor_key': 'chapter:4:event:9', 'summary': _ANCHOR},
        {'anchor_key': 'chapter:4:event:8', 'summary': _ANCHOR},
        {'anchor_key': 'chapter:5:event:1', 'summary': _ANCHOR},
        {'anchor_key': 'chapter:9:event:0', 'summary': '完全无关的另一段剧情摘要,不该命中。'},
    ]
    hits = deterministic_hits(pending, _TURN_REPLAY)
    assert 1 <= len(hits) <= MAX_DET_MARKS_PER_TURN
    for h in hits:
        assert h['drift_score'] == 0.25 and h['anchor_key'].startswith('chapter:')
    assert all(h['anchor_key'] != 'chapter:9:event:0' for h in hits)


def test_reconciler_wired_after_intro_layer():
    src = (ROOT / 'gm_serving' / 'anchor_reconcile.py').read_text(encoding='utf-8')
    i_intro = src.find('_deterministic_intro_hits(save_id, pending, text)')
    i_sig = src.find('deterministic_hits as _sig_hits')
    i_log = src.find('[anchor_reconcile] save=%s 候选=%d')
    assert 0 < i_intro < i_sig, '签名层必须在 intro 兜底之后(只补前两层漏的)'
    assert i_log > 0, '可观测日志必须存在(268 静默不可诊断的教训)'
    seg = src[i_sig:i_sig + 800]
    assert '_mark_ceiling' in seg, '签名层同受防跳章上界约束'


def test_judge_no_think_and_empty_guard_wired():
    """268 真凶回归锁:判定器必须 no_think(思考模型无界思考吃光预算正文恒空)+
    空正文不再静默当 reached=[](告警+扩预算重试一次)。"""
    src = (ROOT / 'gm_serving' / 'anchor_reconcile.py').read_text(encoding='utf-8')
    body = src[src.find('def _default_judge'):src.find('def _normalize_judge_result')]
    assert body.count('no_think=True') >= 2, '首调+重试都必须禁深思'
    assert '判定器返回空正文' in body and 'max_tokens=2400' in body
    h = (ROOT / 'agents' / '_harness.py').read_text(encoding='utf-8')
    assert '"thinking": {"type": "disabled"}' in h, '实测有效方言(op/deepseek-v4-pro)'
    assert '拒绝 extra_body 参数' in h, '严格 provider 400 退参自愈'
