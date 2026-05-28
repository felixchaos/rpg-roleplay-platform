"""
demo.py — 主入口：CLI 游戏循环

运行方式：
    cd rpg/
    ../rpg_env/bin/python demo.py

命令：
    /save    — 手动存档
    /status  — 显示玩家状态
    /debug   — 显示上轮召回内容
    q / 退出 — 存档并退出
"""
from __future__ import annotations
import sys
import os
from pathlib import Path

# 加载 .env（位于项目根目录，rpg/ 的上一级）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass  # dotenv 不可用时直接读环境变量

from state     import GameState
from retrieval import retrieve_context
from gm        import GameMaster

DIVIDER   = "─" * 52
THIN_DIV  = "·" * 52


def _print_gm(text: str):
    print()
    print(DIVIDER)
    print(text)
    print(DIVIDER)
    print()


def setup_character(state: GameState) -> None:
    """首次进入时，引导玩家创建角色"""
    print()
    print("=" * 52)
    print("  《我蕾穆丽娜不爱你》— 柏林弧 RPG Demo")
    print("=" * 52)
    print()
    print("图卢兹刚刚失守。")
    print("薇瑟帝国的前线距柏林越来越近。")
    print("你即将进入这座城市，卷入其中。")
    print()
    print(THIN_DIV)
    print()

    name = input("你叫什么名字？\n> ").strip()
    if not name:
        name = "无名者"

    print()
    print("你在这个故事里扮演什么角色？")
    print("  1. 欧洲世家的信使，在各方势力间传递消息")
    print("  2. 地联太平洋方面的情报协力人员")
    print("  3. 在柏林流亡的薇瑟帝国边缘贵族")
    print("  4. 自定义...")
    print()
    choice = input("选择（1-4）：\n> ").strip()

    roles = {
        "1": "欧洲世家信使",
        "2": "地联太平洋方面情报协力人员",
        "3": "薇瑟帝国流亡贵族",
    }
    if choice in roles:
        role = roles[choice]
    else:
        role = input("请描述你的角色定位：\n> ").strip() or "过客"

    print()
    background = input(f"用一两句话描述 {name} 来柏林的原因（可留空）：\n> ").strip()
    if not background:
        background = "原因不明，只是来了。"

    state.setup_player(name, role, background)
    state.save()
    print()
    print(THIN_DIV)


def game_loop(state: GameState, gm: GameMaster) -> None:
    last_context = ""

    # 开场白
    print("\n正在生成开场……")
    opening_ctx = retrieve_context("柏林 图卢兹 娅赛兰 蛇信", verbose=False)
    opening     = gm.generate_opening(state, retrieved_context=opening_ctx)
    _print_gm(opening)

    # 游戏主循环
    while True:
        try:
            user_input = input(f"[{state.player_name}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[存档并退出]")
            state.save()
            break

        if not user_input:
            continue

        # ── 内置命令 ──
        if user_input.lower() in ("q", "quit", "exit", "退出"):
            print("[存档并退出]")
            state.save()
            break

        if user_input == "/save":
            state.save()
            print("[已存档]")
            continue

        if user_input == "/status":
            print()
            print(state.short_summary())
            print()
            continue

        if user_input == "/debug":
            print()
            print("=== 上轮召回内容 ===")
            print(last_context or "（本轮无召回）")
            print()
            continue

        if user_input.startswith("/loc "):
            loc = user_input[5:].strip()
            if loc:
                state.update_location(loc)
                print(f"[位置更新：{loc}]")
            continue

        if user_input.startswith("/rel "):
            # /rel 角色名 状态
            parts = user_input[5:].strip().split(" ", 1)
            if len(parts) == 2:
                state.update_relationship(parts[0], parts[1])
                print(f"[关系更新：{parts[0]} → {parts[1]}]")
            continue

        # ── 正常游戏输入 ──
        last_context = retrieve_context(user_input, verbose=False)
        response     = gm.respond(user_input, last_context, state)
        state.record_turn(user_input, response)
        state.save()
        _print_gm(response)


def main():
    state = GameState.load_or_new()

    if state.is_new:
        setup_character(state)

    try:
        gm = GameMaster()
    except ValueError as e:
        print(f"[错误] {e}")
        sys.exit(1)
    except Exception as e:
        if "401" in str(e) or "authentication" in str(e).lower():
            print("[错误] API Key 无效（401）。")
            print("请在项目根目录 .env 文件中设置有效的 ANTHROPIC_API_KEY：")
            print("  ANTHROPIC_API_KEY=sk-ant-...")
        else:
            print(f"[错误] {e}")
        sys.exit(1)

    game_loop(state, gm)


if __name__ == "__main__":
    main()
