"""test_recall_wiring_not_broken.py — 统一召回新路的接线不能断(断了也必须吼出来)。

生产实况(2026-07-28 发现):`kb/recall.py` 从 `context_providers.novel` 导入
`_split_anchor_pending`,但该符号在 v1.70.1「函数寻根轮4」(fdea8d321,2026-07-17)已被
收敛到权威缝 `context_engine.core`。于是新路每次 ImportError → 静默降级旧路,
**11 天一次都没跑成**,日志里每天 90 条 warning 没人看见。
而这条路由 `RPG_TKB_RECALL` / `RPG_TKB_RECALL_MIN_SAVE_ID` 灰度门控 —— 只要开了,命中的
存档全在走这条死路:一个自以为已灰度上线的功能,实际是全死的。

能藏这么久的原因是 `except Exception` 把**编程错误**(符号搬家/改名/签名不符)和运行期
故障(DB 抖动/超时)一视同仁当「可降级」。本文件锁两件事:
  ① recall.py 声明要 import 的符号,在它声明的模块里必须真的存在(这类断线在**导入期**就红);
  ② 编程错误走 ERROR + 固定前缀,不许再和普通 warning 混在一起。
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import kb.recall as _recall

_SRC = pathlib.Path(_recall.__file__).read_text(encoding="utf-8")


def _function_local_imports(src: str) -> list[tuple[str, str]]:
    """抓出所有 `from X import a, b` 的 (模块, 符号) 对(含函数内的局部 import)。"""
    out: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                out.append((node.module, alias.name))
    return out


def test_every_symbol_recall_imports_actually_exists():
    """本仓内部模块的每一个 import 目标都必须真的存在 —— 这正是漏掉 11 天的那类断线。"""
    missing: list[str] = []
    for mod, name in _function_local_imports(_SRC):
        top = mod.split(".")[0]
        if top not in {"kb", "context_engine", "context_providers", "retrieval",
                       "agents", "platform_app", "core", "state"}:
            continue  # 只查本仓模块,三方库不在此列
        try:
            m = importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{mod}(模块导入失败: {exc})")
            continue
        if not hasattr(m, name):
            missing.append(f"{mod}.{name}")
    assert not missing, f"kb/recall.py 引用了不存在的符号(新路会静默降级): {missing}"


def test_split_anchor_pending_comes_from_its_authoritative_home():
    """权威缝在 context_engine.core;别再从 context_providers.novel 拿(那是搬家前的旧址)。"""
    import context_providers.novel as _novel
    from context_engine.core import _split_anchor_pending  # noqa: F401
    assert not hasattr(_novel, "_split_anchor_pending"), \
        "novel.py 又出现了同名副本 —— 权威缝被复制回去了,收口失效"
    assert "from context_engine.core import _split_anchor_pending" in _SRC
    assert "from context_providers.novel import _read_progress_and_mode, _split_anchor_pending" not in _SRC


def test_read_progress_and_mode_still_lives_in_novel():
    """同一行 import 里的另一个符号没搬家,别顺手改错。"""
    from context_providers.novel import _read_progress_and_mode  # noqa: F401


# ── 编程错误必须吼出来,不许混进普通 warning ──────────────────────────────
# 说明:这一组是**源码级**断言。行为级(真的触发 ImportError 再看日志级别)需要真 DB 起
# retrieve_context,不适合放 unit;这里锁住「分支存在 + 级别是 error + 前缀可 grep」,
# 已足够防止有人把它改回和普通 warning 混在一起 —— 那正是这次藏了 11 天的原因。

def test_programming_errors_have_their_own_loud_branch():
    assert "except (ImportError, AttributeError, NameError, TypeError)" in _SRC, \
        "编程错误没有独立分支,又会和运行期故障混成同一条 warning"
    assert "log.error" in _SRC and "接线断了" in _SRC, "编程错误分支不是 ERROR 或缺可 grep 前缀"


def test_runtime_failures_still_degrade_quietly():
    """运行期故障(DB 抖动/超时)照旧 warning + 降级,不打断玩家回合。"""
    assert 'log.warning("[recall] 新路异常,降级旧路' in _SRC


def test_every_failure_path_degrades_instead_of_raising():
    """无论哪种失败都必须返回旧路文本,绝不把异常抛进玩家回合。"""
    assert _SRC.count("return old_text") >= 3  # 编程错误 / 运行期异常 / 空召回
