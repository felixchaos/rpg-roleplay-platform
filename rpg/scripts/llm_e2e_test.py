#!/usr/bin/env python3
"""
llm_e2e_test.py — task 73: LLM 自测 harness。

针对侧栏控制台助手 (console_assistant) 打 SSE 测试。
每条 case 指定:
  · user_msg       : 输入消息
  · expect_action  : 期望最终被 ui_invoke 的 action_id (若 None 表示不应 invoke)
  · expect_ask     : 期望最终 yield user_choice_required / user_text_required 之一
                     (即 LLM 应当先问而不是直接调)
  · note           : 描述

跑法:
  ./scripts/llm_e2e_test.py
  ./scripts/llm_e2e_test.py --token <session_token>
  ./scripts/llm_e2e_test.py --filter cards   # 只跑 cards 类

退出码 0 表示全部通过,非 0 是失败数。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from typing import Any


BASE = os.environ.get("RPG_BACKEND_BASE", "http://127.0.0.1:7860")


def _post_sse(path: str, body: dict, token: str, timeout: float = 60.0) -> list[dict]:
    """返 SSE events 列表 [{event: str, data: any}, ...]"""
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Cookie": f"rpg_session={token}",
        },
        method="POST",
    )
    out: list[dict] = []
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return [{"event": "http_error", "data": {"status": exc.code,
                                                 "body": exc.read().decode("utf-8", "ignore")}}]
    except Exception as exc:
        return [{"event": "network_error", "data": {"err": str(exc)}}]
    raw = resp.read().decode("utf-8", "ignore")
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        ev = "message"
        data_lines = []
        for ln in block.split("\n"):
            if ln.startswith("event:"):
                ev = ln[6:].strip()
            elif ln.startswith("data:"):
                data_lines.append(ln[5:].strip())
        data_str = "\n".join(data_lines)
        try:
            data = json.loads(data_str) if data_str else None
        except Exception:
            data = data_str
        out.append({"event": ev, "data": data})
    return out


# ── 测试用例 ────────────────────────────────────────────────


CASES: list[dict[str, Any]] = [
    # ── 角色卡 ─────────────────────────────────────────
    {
        "id": "card-create-minimal",
        "user_msg": "创建一个用户角色 测试-轻量",
        "expect_action": None,         # 不应直接 invoke (缺 summary)
        "expect_ask": True,            # 应先问
        "note": "建卡只给名字 → 应先问性格",
    },
    {
        "id": "card-create-full",
        "user_msg": "创建用户角色卡 测试-完整,性格 开朗,身份 流亡贵族",
        "expect_action": "create_character_card",
        "expect_ask": False,
        "note": "建卡信息完整 → 直接 invoke",
    },
    {
        "id": "card-list",
        "user_msg": "列出我的角色卡",
        "expect_action": "list_my_character_cards",
        "expect_ask": False,
        "note": "list 类不需要参数",
    },
    # ── 存档 ───────────────────────────────────────────
    {
        "id": "save-create-minimal",
        "user_msg": "新建一个存档",
        "expect_action": None,
        "expect_ask": True,
        "note": "建存档不给 script_id/title → 应先问",
    },
    {
        "id": "save-list",
        "user_msg": "看看我的存档",
        "expect_action": "list_my_saves",
        "expect_ask": False,
        "note": "list 类",
    },
    # ── 剧本 ───────────────────────────────────────────
    {
        "id": "script-list",
        "user_msg": "我有哪些剧本",
        "expect_action": "list_scripts",
        "expect_ask": False,
        "note": "list 类",
    },
    # ── persona ───────────────────────────────────────
    {
        "id": "persona-list",
        "user_msg": "列出我的 persona",
        "expect_action": "list_my_personas",
        "expect_ask": False,
        "note": "list 类",
    },
    # ── 设置 ───────────────────────────────────────────
    {
        "id": "models-list",
        "user_msg": "有哪些可用模型",
        "expect_action": "list_available_models",
        "expect_ask": False,
        "note": "全局 read",
    },
    # ── 易混淆: "改剧情里玩家名" — 助手不该碰 ──
    {
        "id": "story-rename-refuse",
        "user_msg": "把剧情里玩家名字改成 阿狸",
        "expect_action": None,        # 没有合适 action,LLM 应文本回复"去 Game Console"
        "expect_ask": False,
        "note": "save 内剧情字段不在助手可见域 — 应文本拒绝",
    },
]


# ── 评测一条 case ──────────────────────────────────────────


def evaluate(events: list[dict]) -> dict[str, Any]:
    """task 96: LLM 现在直调具体工具 (create_character_card / list_my_saves ...),
    不再嵌套 ui_invoke。本评估读 native tool_call 事件,捕获"最后一次成功调的
    业务工具"作为 invoked_action。失败 (dispatcher 报 missing_required 等) 不算。
    ask_user_choice / user_choice_required → asked=True。
    """
    pending_action: str | None = None
    invoked_action: str | None = None
    asked = False
    error: str | None = None
    text_acc = ""
    META = {"ui_describe", "ask_user_choice", "ask_user_text", "navigate_to_setting"}
    for ev in events:
        e = ev.get("event")
        d = ev.get("data") or {}
        if e == "tool_call" and isinstance(d, dict):
            tool = d.get("tool") or ""
            if tool in META:
                if tool == "ask_user_choice":
                    asked = True
                pending_action = None  # meta tools 不算 invoked
            elif tool:
                pending_action = tool
        elif e == "tool_result" and isinstance(d, dict) and pending_action:
            res = d.get("result") or ""
            ok_flag = d.get("ok")
            success = (ok_flag is True) or (
                isinstance(res, str) and not res.startswith("失败")
            )
            if success:
                invoked_action = pending_action
            pending_action = None
        elif e in ("user_choice_required", "user_text_required"):
            asked = True
        elif e == "error":
            error = (d or {}).get("message") if isinstance(d, dict) else str(d)
        elif e == "token" and isinstance(d, dict):
            text_acc += d.get("text", "")
        elif e in ("http_error", "network_error"):
            error = json.dumps(d, ensure_ascii=False)
    return {
        "invoked_action": invoked_action,
        "asked": asked,
        "error": error,
        "text": text_acc[:140],
    }


def run_case(case: dict[str, Any], token: str) -> dict[str, Any]:
    events = _post_sse(
        "/api/console_assistant/chat",
        {"message": case["user_msg"]},
        token,
        timeout=60.0,
    )
    result = evaluate(events)
    # 判断 pass
    exp_action = case.get("expect_action")
    exp_ask = case.get("expect_ask")
    ok = True
    reasons = []
    if exp_action is not None and result["invoked_action"] != exp_action:
        ok = False
        reasons.append(f"expected_action={exp_action!r}, got={result['invoked_action']!r}")
    if exp_action is None and result["invoked_action"]:
        # 允许不 invoke 的 case 实际也没 invoke
        ok = False
        reasons.append(f"should NOT have invoked, but did: {result['invoked_action']}")
    if exp_ask and not result["asked"]:
        ok = False
        reasons.append("expected to ask user but did not")
    if not exp_ask and result["asked"] and exp_action is not None:
        # 期望直接 invoke 但 LLM 也可能合理地先问 — 算 soft warning, 不 fail
        reasons.append("(soft) unexpected ask")
    if result["error"]:
        reasons.append(f"runtime error: {result['error']}")
    return {**case, **result, "ok": ok, "reasons": reasons}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("RPG_TEST_TOKEN", ""))
    ap.add_argument("--filter", default="")
    ap.add_argument("--json", action="store_true", help="结果 JSON 输出")
    args = ap.parse_args()
    if not args.token:
        # 试 DB 里挑一个最新 session
        try:
            import subprocess
            out = subprocess.check_output(
                ["psql", "rpg_platform", "-t", "-c",
                 "select token from sessions order by created_at desc limit 1"],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
            args.token = out
        except Exception:
            pass
    if not args.token:
        print("ERROR: 没有 session token,传 --token 或设 RPG_TEST_TOKEN", file=sys.stderr)
        return 2

    cases = CASES
    if args.filter:
        cases = [c for c in cases if args.filter.lower() in c["id"].lower()
                 or args.filter.lower() in c["user_msg"]]
    passed = 0
    failed: list[dict] = []
    started = time.time()
    for case in cases:
        r = run_case(case, args.token)
        tag = "PASS" if r["ok"] else "FAIL"
        if not args.json:
            print(f"[{tag}] {case['id']:30s} note={case['note']}")
            if r["reasons"]:
                for reason in r["reasons"]:
                    print(f"    · {reason}")
            print(f"    invoked={r['invoked_action']!r:30s} asked={r['asked']}  text={r['text']!r}")
        if r["ok"]:
            passed += 1
        else:
            failed.append(r)
    if args.json:
        print(json.dumps({"passed": passed, "failed": failed,
                          "elapsed": time.time() - started},
                         ensure_ascii=False, indent=2))
    else:
        elapsed = time.time() - started
        print()
        print(f"==== {passed}/{len(cases)} passed in {elapsed:.1f}s ====")
        if failed:
            print(f"Failures: {[f['id'] for f in failed]}")
    return 0 if not failed else len(failed)


if __name__ == "__main__":
    raise SystemExit(main())
