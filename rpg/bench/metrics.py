"""RP harness 基准 — 指标注册表。

每个指标是纯函数 metric(resp, ctx) -> dict[str, float|bool],只读、确定性、可单测。
ctx = {player_input, prior_assistant: list[str], canon_aliases: dict, script_id, ...}。
新增指标只需 @metric("name") 装饰一个函数 —— 这就是"框架"的扩展点。

首版只放【确定性、方向明确、能抓真实 harness 故障】的指标(避开 revived_dead 那类
语义不可靠的)。布尔指标在 scorecard 里聚合成"命中率",连续指标聚合成均值/分位。
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable

_METRICS: dict[str, Callable[[str, dict], dict]] = {}
# 每个指标声明它的字段方向:'bad_rate'=布尔(越低越好)/'lower'/'higher'/'info'(仅观测)
_FIELD_KIND: dict[str, str] = {}


def metric(name: str, fields: dict[str, str]):
    def deco(fn):
        _METRICS[name] = fn
        for f, k in fields.items():
            _FIELD_KIND[f] = k
        return fn
    return deco


def all_metrics() -> dict[str, Callable]:
    return dict(_METRICS)


def field_kind(field: str) -> str:
    return _FIELD_KIND.get(field, "info")


# ── 退化/复读(LLM 经典故障:n-gram 自重复)──────────────────────────────────
@metric("degeneration", {"repeat_ratio": "lower", "max_run": "lower"})
def m_degeneration(resp: str, ctx: dict) -> dict:
    s = resp or ""
    n = 8
    # len(s)-n+1 个窗口:旧写法 len(s)-n 丢最后一个 gram,恰好 n 长的串产出 0 gram(拆库审计回灌)
    grams = [s[i:i + n] for i in range(max(0, len(s) - n + 1))]
    repeat_ratio = 0.0
    if grams:
        c = Counter(grams)
        repeat_ratio = sum(v for v in c.values() if v > 1) / len(grams)
    # 最长连续重复(同一短语反复):粗测最长重复子串运行
    max_run = 0
    for m in re.finditer(r"(.{2,12}?)\1{2,}", s):   # 某 2-12 字片段连续出现 ≥3 次
        max_run = max(max_run, len(m.group(0)))
    return {"repeat_ratio": round(repeat_ratio, 4), "max_run": max_run}


# ── 语言纯度(中文 RP 整轮降级成英文 = 模型串台)──────────────────────────
# 注:不能用"出现一段英文"判坏 —— 真实剧本里有 AI 角色/术语/武器名说英文是正常的
# (实测 268 的红后就讲英文)。改判:全轮字母里 CJK 占比 < 0.55 = 整轮基本非中文 = 降级。
@metric("language", {"cjk_ratio": "higher", "degraded_lang": "bad_rate"})
def m_language(resp: str, ctx: dict) -> dict:
    s = resp or ""
    letters = [ch for ch in s if ch.isalpha()]
    cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
    cjk_ratio = round(cjk / len(letters), 4) if letters else 1.0
    return {"cjk_ratio": cjk_ratio, "degraded_lang": bool(letters) and cjk_ratio < 0.55}


# ── 出戏/AI 自曝/拒答(破角色,玩家最反感)──────────────────────────────────
_OOC = re.compile(
    r"作为(一个|一名)?\s*(AI|人工智能|语言模型|大模型|助手)"
    r"|as an AI|I'?m an AI|language model"
    r"|我(只是|不过是|其实是)(一个|一名)?\s*(AI|人工智能|程序|助手)"
    r"|我(无法|不能|没法)(满足|提供|继续|协助|完成)"
    r"|对不起[，,].{0,8}(我不能|我无法|不能提供|无法满足)"
    r"|I (cannot|can'?t|won'?t) (help|continue|assist|provide)"
    r"|抱歉[，,].{0,8}(无法|不能)"
    r"|违反.{0,6}(政策|准则|规定)|内容政策"
)


@metric("ooc_leakage", {"ooc": "bad_rate"})
def m_ooc(resp: str, ctx: dict) -> dict:
    return {"ooc": bool(_OOC.search(resp or ""))}


# ── 协议泄漏(把工具调用机制/原始 JSON/系统标记当正文吐给玩家)──────────────
# 实测真 bug:GM narrate "执行了 search_canon(...) 返回的原始结果:```json"(存档2)。
# 收紧:只认明确的工具/协议泄漏,不再用裸"系统"(会撞 in-fiction "检索系统")。
_PROTO = re.compile(
    r"<<\s*/?\s*TOOL_CALL|<<\s*/?\s*(tool|op|call)\b"
    r"|```json|```tool|\"tool_name\"|\"arguments\"\s*:|apply_ops|state_snapshot"
    r"|search_canon\s*\(|search_manuscript\s*\(|执行了\s*[`\"]?\w+\s*\("
    r"|Save\s*ID\s*[:：]|返回的原始结果|检索系统返回"
    r"|【系统[】:]|<\|\w+\|>"
)


@metric("protocol", {"leak": "bad_rate"})
def m_protocol(resp: str, ctx: dict) -> dict:
    return {"leak": bool(_PROTO.search(resp or ""))}


# ── 长度健康(过短=截断/空轮;过长=失控)──────────────────────────────────
# 澄清/询问玩家轮天然短(【需要先确认】A/B/C),不算坏 → 单独标 clarify,从 too_short 排除。
_CLARIFY = re.compile(r"【\s*(需要先确认|询问玩家|请确认|需要确认)\s*】|请用完整句子描述")


@metric("length", {"chars": "info", "clarify": "info", "too_short": "bad_rate", "runaway": "bad_rate"})
def m_length(resp: str, ctx: dict) -> dict:
    s = resp or ""
    n = len(s)
    clarify = bool(_CLARIFY.search(s))
    return {"chars": n, "clarify": clarify, "too_short": (n < 80) and not clarify, "runaway": n > 6000}


# ── canon 接地(回应里引用了多少该剧本的既定角色)──────────────────────────
@metric("canon", {"canon_hits": "info", "engaged": "higher"})
def m_canon(resp: str, ctx: dict) -> dict:
    s = resp or ""
    aliases: dict[str, list[str]] = ctx.get("canon_aliases") or {}
    hits = 0
    for names in aliases.values():
        if any(nm and len(nm) >= 2 and nm in s for nm in names):
            hits += 1
    return {"canon_hits": hits, "engaged": bool(hits)}


# ── 脱离 canon 的"开口说话者"(确定性核心检查·grounding)──────────────────────
# 抽"名字+(语气)+说话动词"的说话者,凡不在 canon 角色表、且非代词 → 计为脱离 canon 的
# 临场角色。不是非黑即白(新 NPC 合法),作 info 信号:某 harness 凭空造越多说话者 = 越脱离设定。
_SPEAKER_RE = re.compile(
    r"([一-鿿]{2,4})(?:[一-鿿]{0,3}地|[一-鿿]{0,2})?"
    r"(?:说道|说|道|问道|问|答道|答|喊道|喊|叫道|低声|沉声|冷笑|轻声)[:：\"「]"
)
_PRONOUNS = {"你", "我", "他", "她", "它", "我们", "你们", "他们", "她们", "对方", "众人", "有人", "那人"}


@metric("unknown_speaker", {"off_canon_speakers": "info"})
def m_unknown_speaker(resp: str, ctx: dict) -> dict:
    s = resp or ""
    aliases: dict[str, list[str]] = ctx.get("canon_aliases") or {}
    canon_names = {nm for names in aliases.values() for nm in names if nm}
    speakers = {m.group(1) for m in _SPEAKER_RE.finditer(s)}
    off = {sp for sp in speakers
           if sp not in _PRONOUNS and not any(sp in cn or cn in sp for cn in canon_names)}
    return {"off_canon_speakers": len(off)}


# ── 复述上一轮(确定性核心检查·degeneration):GM 把前一轮的句子原样回炒 = 卡住/失忆 ──
@metric("prior_echo", {"echo_ratio": "lower"})
def m_prior_echo(resp: str, ctx: dict) -> dict:
    prior = ctx.get("prior_assistant") or []
    prev = prior[-1] if prior else ""
    if not prev or not resp:
        return {"echo_ratio": 0.0}
    n = 10
    # len-n+1 窗口修复同 m_degeneration:旧写法下恰 10 字的回复复读永远漏检(拆库审计回灌)
    prev_grams = {prev[i:i + n] for i in range(max(0, len(prev) - n + 1))}
    if not prev_grams:
        return {"echo_ratio": 0.0}
    resp_grams = [resp[i:i + n] for i in range(max(0, len(resp) - n + 1))]
    if not resp_grams:
        return {"echo_ratio": 0.0}
    echoed = sum(1 for g in resp_grams if g in prev_grams)
    return {"echo_ratio": round(echoed / len(resp_grams), 4)}
