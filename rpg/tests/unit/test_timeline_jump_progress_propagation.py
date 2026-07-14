"""群反馈(白玖):/set 世界线跳转到乌坦城事件后——剧情跳成功(step5 写 timeline,GM 材料
正确到 ch91)但面板「当前」钉在第1章:面板判定优先读 worldline.progress_chapter,而
step5 只写 timeline 六字段。修=显式跳转与出生点/advance_story_progress 同语义推进
progress_chapter+user_progress_floor(max-only)。源码结构断言。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((ROOT / "chat_pipeline").glob("*.py")))


def _step5():
    i = SRC.find("# step 5: timeline anchor")
    assert i != -1
    return SRC[i:i + 3500]


def test_timeline_jump_advances_progress_and_floor():
    b = _step5()
    assert "advance_progress" in b, "跳转成功必须推进 worldline.progress_chapter(面板/锚点窗口/揭示天花板同源)"
    assert "set_user_progress_floor" in b, "显式跳转=玩家权威地板,揭示钳制须放行"
    # 推进用锚点起始章,且 state 侧同步(persist 前内存一致)
    assert '_anchor["chapter_min"]' in b
    assert '"progress_chapter"' in b.replace("'", '"')


def test_jump_progress_failure_is_nonfatal():
    b = _step5()
    assert "非致命" in b, "进度推进失败不得阻断跳转本体(timeline 写入)"
