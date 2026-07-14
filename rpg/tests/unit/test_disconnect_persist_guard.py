"""断连「打断即落库」源码守卫(结构断言,e2e 见生产验证)。

不变量:
1. api_chat 的 stream() 里,GeneratorExit/CancelledError 兜底分支存在且排在
   except Exception 之前,含【网络中断】标注与 _done_streamed 防双落库闸。
2. chat_pipeline 的 GM token 分支保持 ctx.response 实时新鲜(断连时 routes 层
   拿得到半截正文——原先只在循环退出点赋值,partial 恒空)。
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_routes_disconnect_persist_clause():
    src = (_ROOT / "routes" / "game.py").read_text(encoding="utf-8")
    ge = src.index("except (GeneratorExit, asyncio.CancelledError):")
    exc = src.index("except Exception as exc:", ge)
    clause = src[ge:exc]
    assert "网络中断,已保留部分内容" in clause
    assert "_done_streamed" in clause
    assert "_persist_chat_turn(" in clause
    assert "raise" in clause  # 落库后必须继续向上抛,不吞取消


def test_pipeline_keeps_ctx_response_fresh():
    src = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((_ROOT / "chat_pipeline").glob("*.py")))
    i = src.index("response += chunk")
    window = src[i:i + 400]
    assert "ctx.response = response" in window, "GM token 分支必须实时刷新 ctx.response"


def test_done_tracking_covers_all_phase_loops():
    src = (_ROOT / "routes" / "game.py").read_text(encoding="utf-8")
    # 每个 `yield _sse(evt, data)` 之前都应有 done 追踪(5 个 phase 透传点)
    assert src.count("yield _sse(evt, data)") == src.count('if evt == "done":')
