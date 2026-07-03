"""regex_scripts.py — 用户自定义正则脚本(SillyTavern regex parity,v1 = 输出/显示作用域,反馈#93 之三)。

存 user_preferences.preferences.regex_scripts = [
  {id, name, find, replace, flags: "ims" 子集, enabled: bool}, ...
]
apply_output_regex(text, user_id):对 GM 清洗后的**可见正文**按顺序应用启用的脚本(find→replace)。
替换串用 SillyTavern/JS 风格 $1 / $& / $$(不用记 Python 的 \\1 / \\g<0>),由 _expand_replacement 手动展开,
规避 re.sub 对替换串里反斜杠的特殊语义。

⚠️ v1 仅【输出/显示】作用域:对 AI 输出做确定性 find/replace(SillyTavern 最主流用法)。
输入作用域(改玩家消息进模型)与指令/OOC 解析纠缠,留待 v2;故 UI 不提供作用域开关,避免「假选项」。

安全(用户可控正则跑在生成热路径,须防 ReDoS):
- 每条脚本的 sub 丢到线程池跑,带 wall-clock 超时(默认 0.5s);超时/异常 → 跳过该条,**绝不断轮**。
- 输入文本长度上限(超大文本 × 复杂正则是 ReDoS 放大器)。
- 编译结果按 (find, flags) 缓存;无效正则跳过。脚本条数上限。
"""
from __future__ import annotations

import logging
import re
import threading

log = logging.getLogger(__name__)

_MAX_TEXT = 200_000          # 超过不处理(防超大文本 × 复杂正则)
_PER_SCRIPT_TIMEOUT = 0.5    # 每条脚本 wall-clock 上限(秒),ReDoS 兜底
_MAX_SCRIPTS = 50
_MAX_PATTERN_LEN = 2000

_compiled_cache: dict[tuple[str, str], re.Pattern] = {}
_REPL_TOKEN = re.compile(r"\$(\d{1,2}|&|\$)")

# 启发式:拒绝典型的灾难回溯(ReDoS)模式 —— 「含无界量词的分组」本身又被量词修饰,
# 如 (a+)+ / (a*)* / (.+)* / (\w+){2,}。stdlib re 无法中途打断,故在源头(保存+应用)挡掉最坏情形;
# 配合下面 daemon 线程超时(兜底其余情况)。会有少量误伤(可提示用户简化正则)。
_RISKY_PATTERN = re.compile(r"\([^)]*[+*][^)]*\)\s*[*+]|\([^)]*[+*][^)]*\)\s*\{")


def is_risky_pattern(pattern: str) -> bool:
    """嵌套无界量词的高危 ReDoS 结构。True → 拒绝保存 / 应用时跳过。"""
    try:
        return bool(_RISKY_PATTERN.search(pattern or ""))
    except Exception:
        return False


def _sub_with_timeout(pat: re.Pattern, repl_fn, text: str, timeout: float) -> str:
    """在 daemon 线程里跑 pat.sub;超时/异常 → 退回原文。daemon 线程不阻塞进程退出/关停
    (stdlib 无法真正杀死跑飞的正则线程,但它是 daemon,进程重启即回收;请求本身按超时返回)。"""
    box = {"out": text}
    done = threading.Event()

    def _run():
        try:
            box["out"] = pat.sub(repl_fn, text)
        except Exception:
            pass
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True).start()
    if not done.wait(timeout):
        return text  # 超时 → 原文
    return box["out"]


def load_regex_scripts(user_id: int | None) -> list[dict]:
    if not user_id:
        return []
    try:
        from platform_app.db import connect
        with connect() as db:
            r = db.execute(
                "select preferences from user_preferences where user_id=%s", (int(user_id),)
            ).fetchone()
        prefs = dict((r or {}).get("preferences") or {})
    except Exception as exc:
        log.warning(f"[regex] load failed: {exc}")
        return []
    scripts = prefs.get("regex_scripts")
    return scripts if isinstance(scripts, list) else []


def _compile(find: str, flags_str: str) -> re.Pattern:
    key = (find, flags_str)
    cached = _compiled_cache.get(key)
    if cached is not None:
        return cached
    f = 0
    if "i" in flags_str:
        f |= re.IGNORECASE
    if "m" in flags_str:
        f |= re.MULTILINE
    if "s" in flags_str:
        f |= re.DOTALL
    pat = re.compile(find, f)
    if len(_compiled_cache) < 256:
        _compiled_cache[key] = pat
    return pat


def _expand_replacement(m: re.Match, repl: str) -> str:
    """把替换串里的 $1..$99 / $& / $$ 用本次匹配展开(SillyTavern/JS 语义;避开 re.sub 反斜杠陷阱)。"""
    def _one(tok: re.Match) -> str:
        g = tok.group(1)
        if g == "&":
            return m.group(0) or ""
        if g == "$":
            return "$"
        try:
            return m.group(int(g)) or ""
        except (IndexError, ValueError):
            return ""
    return _REPL_TOKEN.sub(_one, repl)


def apply_output_regex(text: str, user_id: int | None) -> str:
    """对 GM 可见正文应用用户启用的输出正则脚本。任何异常都退回原文(绝不断轮)。"""
    if not text or len(text) > _MAX_TEXT:
        return text
    scripts = load_regex_scripts(user_id)
    if not scripts:
        return text
    out = text
    for sc in scripts[:_MAX_SCRIPTS]:
        if not isinstance(sc, dict) or not sc.get("enabled", True):
            continue
        find = sc.get("find")
        if not find or not isinstance(find, str) or len(find) > _MAX_PATTERN_LEN:
            continue
        repl = sc.get("replace")
        repl = repl if isinstance(repl, str) else ""
        if is_risky_pattern(find):
            log.warning(f"[regex] 跳过高危 ReDoS 模式: {str(sc.get('name') or find)[:40]}")
            continue
        flags_str = "".join(c for c in str(sc.get("flags") or "").lower() if c in "ims")
        try:
            pat = _compile(find, flags_str)
        except re.error:
            continue
        try:
            out = _sub_with_timeout(pat, lambda mm, _r=repl: _expand_replacement(mm, _r), out, _PER_SCRIPT_TIMEOUT)
        except Exception as exc:
            log.warning(f"[regex] 脚本出错跳过: {exc}")
            continue
    return out
