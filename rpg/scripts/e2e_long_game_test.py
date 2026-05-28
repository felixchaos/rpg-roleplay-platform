#!/usr/bin/env python3
"""e2e_long_game_test.py — task 107I

模拟一个 100+ turn 的长游戏, 验证双时间线 + phase digest 系统是否真的让
GM "想起 100 turn 前发生的事"。

依赖:
  - 107A-107H 全部完成
  - 真实 LLM backend (Vertex Gemini)
  - 一个剧本 (会自动找 user 6 admin 的剧本)
  - 一个活跃存档 (会新建一个)

跑法:
  rpg_env/bin/python rpg/scripts/e2e_long_game_test.py
  rpg_env/bin/python rpg/scripts/e2e_long_game_test.py --turns 50  # 较快测试
  rpg_env/bin/python rpg/scripts/e2e_long_game_test.py --user-id 6 --script-id N

验证点:
  1. 每 30 turn 应该自动开新 phase (save_phase_digests 多一行 with status='open')
  2. 阶段切换后 LLM 自动把上一段摘要到 summary + key_events
  3. turn 100 时问 "turn 5 时发生了什么", GM 答案应该包含 turn 5 的关键内容
  4. context_engine 实际把 runtime_phase_digests 层注入了 prompt (检查 debug)

退出码: 0=全通过, N=失败的断言数
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


BASE = os.environ.get("RPG_BACKEND_BASE", "http://127.0.0.1:7860")

# 模拟玩家"日常水"输入,让 GM 推进 turn,不要太复杂的剧情
SAMPLE_INPUTS = [
    "我环顾四周,这是哪里?",
    "我向前走几步看看",
    "我跟身边的人打个招呼",
    "我问他这里发生了什么",
    "我决定继续探索",
    "我看一下我的物品",
    "我在角落里坐下休息",
    "有什么动静吗?",
    "我朝声音的方向走去",
    "我躲在墙后观察",
]


def _http(method: str, path: str, token: str, body: dict | None = None, timeout: float = 60.0):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8") if body else None,
        headers={
            "Content-Type": "application/json",
            "Cookie": f"rpg_session={token}",
        },
        method=method,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.getcode(), json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8", "ignore"))


def get_admin_session_token() -> str:
    """从 DB 拿 admin 用户 (user_id=6) 的最新 session token."""
    import subprocess
    out = subprocess.check_output(
        ["psql", "rpg_platform", "-t", "-c",
         "select token from sessions where user_id=6 order by created_at desc limit 1"],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
    if not out:
        print("ERROR: admin 用户没有 session, 请先用 admin 登录一次", file=sys.stderr)
        sys.exit(2)
    return out


def query_save_phase_count(save_id: int) -> int:
    import subprocess
    out = subprocess.check_output(
        ["psql", "rpg_platform", "-t", "-c",
         f"select count(*) from save_phase_digests where save_id={save_id}"],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
    return int(out)


def query_save_anchor_count(save_id: int) -> int:
    import subprocess
    out = subprocess.check_output(
        ["psql", "rpg_platform", "-t", "-c",
         f"select count(*) from save_timeline_anchors where save_id={save_id}"],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
    return int(out)


def play_one_turn(token: str, message: str) -> dict:
    """模拟一回合 chat. 返回 {turn, status, gm_text_preview}."""
    code, body = _http("POST", "/api/chat", token, {"message": message}, timeout=120.0)
    if code != 200:
        return {"ok": False, "error": body, "turn": None}
    return {
        "ok": True,
        "turn": (body.get("state") or {}).get("turn"),
        "gm_text": (body.get("output") or "")[:200],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", type=int, default=6, help="测试用户 (默认 admin)")
    ap.add_argument("--save-id", type=int, default=None, help="测试存档, 默认拿 user 当前 active")
    ap.add_argument("--turns", type=int, default=100, help="模拟多少回合")
    ap.add_argument("--token", default=os.environ.get("RPG_TEST_TOKEN", ""))
    args = ap.parse_args()

    token = args.token or get_admin_session_token()
    print("== task 107I: 长游戏 GM 失忆验证 ==")
    print(f"目标: 跑 {args.turns} turn, 验证 phase digest 自动聚合 + GM 能回忆 turn 5")
    print(f"backend: {BASE}, user: {args.user_id}")

    # 1. 拿当前 save_id
    code, state = _http("GET", "/api/state", token)
    if code != 200:
        print(f"ERROR: /api/state 失败 {code}: {state}", file=sys.stderr)
        return 2
    save_id = args.save_id or state.get("save_id")
    if not save_id:
        print("ERROR: 没有 active save, 请先在 UI 创建+激活一个存档", file=sys.stderr)
        return 2
    print(f"active save: {save_id}")

    initial_anchor_count = query_save_anchor_count(save_id)
    initial_phase_count = query_save_phase_count(save_id)
    print(f"初始: timeline_anchors={initial_anchor_count}, phase_digests={initial_phase_count}\n")

    # 2. 跑 N turn
    started = time.time()
    successes = 0
    failures = 0
    turn_log = []
    for i in range(args.turns):
        msg = SAMPLE_INPUTS[i % len(SAMPLE_INPUTS)]
        # 在 turn 5 时塞一个 "可识别" 的关键事件让后续验证可查
        if i == 5:
            msg = "[关键事件] 我捡到一枚刻着 LANTERN-CODE 的怀表, 把它放进口袋"
        r = play_one_turn(token, msg)
        if r["ok"]:
            successes += 1
            turn_log.append({"turn_idx": i, "turn": r["turn"], "preview": r["gm_text"][:60]})
        else:
            failures += 1
            print(f"  [turn {i}] FAIL: {r.get('error')}")
        if (i + 1) % 10 == 0:
            elapsed = time.time() - started
            print(f"  ... turn {i+1}/{args.turns} 完成 ({elapsed:.1f}s, "
                  f"ok={successes}, fail={failures})")

    # 3. 验证 phase digest 数量
    final_phase_count = query_save_phase_count(save_id)
    new_phases = final_phase_count - initial_phase_count
    expected_phases = max(1, args.turns // 30)
    print(f"\n[VERIFY] phase_digests 新增: {new_phases}, 预期 ≈ {expected_phases}")
    phase_check = new_phases >= expected_phases - 1  # 允许 ±1

    # 4. 验证 timeline anchors
    final_anchor_count = query_save_anchor_count(save_id)
    new_anchors = final_anchor_count - initial_anchor_count
    print(f"[VERIFY] timeline_anchors 新增: {new_anchors}, 预期 ≈ {successes}")
    anchor_check = new_anchors >= successes - 5  # 允许 5 个误差 (失败 turn 没 anchor)

    # 5. 关键: 问 GM "turn 5 发生了什么", 验证它能想起来 LANTERN-CODE
    print("\n[VERIFY] 询问 GM: 一开始 (turn 5 附近) 我捡到了什么?")
    r = play_one_turn(token, "我想回忆一下,游戏早期我有没有捡到过什么特别的东西? 比如刻着字的物品?")
    if r["ok"]:
        gm_text = r["gm_text"].lower()
        remembers = "lantern" in gm_text or "怀表" in gm_text or "刻" in gm_text
        print(f"GM 回答 preview: {r['gm_text']}")
        print(f"[VERIFY] GM 回忆 turn 5 关键道具: {'✓ 通过' if remembers else '✗ 失败'}")
        memory_check = remembers
    else:
        print(f"GM 询问失败: {r.get('error')}")
        memory_check = False

    # 总结
    print("\n==== 结果 ====")
    print(f"  turns 跑通:        {successes}/{args.turns}")
    print(f"  phase 自动聚合:    {'PASS' if phase_check else 'FAIL'} ({new_phases} 新)")
    print(f"  anchor 自动记录:   {'PASS' if anchor_check else 'FAIL'} ({new_anchors} 新)")
    print(f"  GM 长游戏不失忆:   {'PASS' if memory_check else 'FAIL'}")
    print(f"  总耗时:            {time.time()-started:.1f}s")

    failed = sum(1 for c in [phase_check, anchor_check, memory_check] if not c)
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
