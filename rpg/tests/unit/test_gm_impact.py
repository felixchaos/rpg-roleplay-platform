"""Phase D — gm_serving.impact 影响因子分级(纯逻辑)。"""
from gm_serving.impact import classify_impact, needs_offband_sim


def test_levels():
    assert classify_impact("我走到窗边看着她说话") == "local"
    assert classify_impact("双方爆发激烈战斗交火") == "scene"
    assert classify_impact("林有德决定刺杀元首") == "faction"
    assert classify_impact("薇瑟帝国向地联宣战") == "world"
    assert classify_impact("") == "local"


def test_offband_gate():
    assert needs_offband_sim("world") and needs_offband_sim("faction")
    assert not needs_offband_sim("local") and not needs_offband_sim("scene")


if __name__ == "__main__":
    test_levels(); test_offband_gate(); print("OK")
