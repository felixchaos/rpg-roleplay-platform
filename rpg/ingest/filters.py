"""ingest/filters.py — 摄入三道过滤之 作者非正文 + 怪标题 (Phase A.0 §4.b/c)。

确定性优先。嵌入只在怪标题判定可选用一次,**不可用时降级纯启发式**(设计 §4.c)。
不静默删:作者非正文 → 标注 is_author_note/exclude_from_extraction(原文保留);
怪标题 → title_confidence + content_descriptor(下游用描述符,原标题只显示)。

chapter dict 新增字段:is_author_note / exclude_from_extraction / title_confidence / content_descriptor。
"""
from __future__ import annotations

import re
from typing import Callable

# ─── §4.b 作者非正文 ─────────────────────────────────────────────────────────
# 标题强信号:卷末通知/感言/请假/上架/完本 等
AUTHOR_NOTE_TITLE_PATTERNS = re.compile(
    r"卷末|卷首|小结|感言|后记|请假|通知|上架|月票|加更|爆肝|作者的话|作者有话|有话(?:要)?说|"
    r"完本|完结撒花|新书|求票|求订阅|求月票|断更|停更|恢复更新|说明|公告|致谢|"
    r"^\s*[（(]?\s*完\s*[）)]?\s*$|本卷完|第[零一二三四五六七八九十百千万〇两\d０-９]+卷\s*完"
)
# 元叙述关键词 (第一人称 + 更新/读者语境)
_META_FIRST = re.compile(r"我|笔者|作者|本人|咱")
_META_CONTEXT = re.compile(r"明天|今天|这章|这一?[章卷节]|下[一]?[章卷]|更新|码字|存稿|大家|读者|票|订阅|抱歉|不好意思|请假|加更|继续努力|谢谢")
# 对白标记
_DIALOGUE = re.compile(r"[「」『』“”‘’]|说道|问道|答道|喊道|笑道|冷笑|低声")
_SENT_SPLIT = re.compile(r"[。！？!?\n]")


def _dialogue_ratio(body: str) -> float:
    sents = [s for s in _SENT_SPLIT.split(body) if s.strip()]
    if not sents:
        return 0.0
    d = sum(1 for s in sents if _DIALOGUE.search(s))
    return d / len(sents)


def _structure_authorish(title: str, body: str) -> float:
    """结构密度打分 0..1:越像作者的话越高。"""
    b = body.strip()
    score = 0.0
    if len(b) < 300:
        score += 0.4
    if len(b) < 120:
        score += 0.15
    if _dialogue_ratio(b) < 0.05:
        score += 0.2
    # 元叙述密度:第一人称 + 更新语境邻近出现
    if _META_FIRST.search(b) and _META_CONTEXT.search(b):
        score += 0.35
    elif _META_CONTEXT.search(b) and len(b) < 400:
        score += 0.2
    return min(1.0, score)


def filter_non_content(chapters: list[dict]) -> list[dict]:
    """标注作者非正文 (不删)。设置 is_author_note / exclude_from_extraction。

    判定:标题强命中 或 结构强命中 → note;或 标题弱命中 且 结构弱命中 → note。
    """
    for ch in chapters:
        title = (ch.get("title") or "").strip()
        body = ch.get("content") or ""
        title_strong = bool(AUTHOR_NOTE_TITLE_PATTERNS.search(title))
        struct = _structure_authorish(title, body)
        # 标题弱命中:含"卷"/"话"等但非剧情(粗略),这里用 title 是否含元词
        title_weak = title_strong or bool(_META_CONTEXT.search(title))
        is_note = (
            title_strong
            or struct >= 0.8
            or (title_weak and struct >= 0.5)
        )
        ch["is_author_note"] = bool(is_note)
        ch["exclude_from_extraction"] = bool(is_note)
        if is_note:
            ch["_note_reason"] = (
                "title" if title_strong else ("title+structure" if title_weak else "structure")
            )
    return chapters


# ─── §4.c 怪标题 ─────────────────────────────────────────────────────────────
# 玩梗/口语标题模式
_MEME_WORDS = re.compile(
    r"说好的|推迟|鸽|咕|爆发|爆肝|卡文|存稿|码字|sorry|抱歉|emmm+|2333+|orz|"
    r"终于|憋不住|憋大招|来了来了|这就是|没想到|居然|竟然|真香|破防|裂开|摆烂|"
    r"日常|吐槽|内心|os|OS|彩蛋|番外篇?之?|嗯哼|哈哈|呜呜|啊这"
)
# 名词/实体感弱的纯口语连接词密度
_COLLOQUIAL = re.compile(r"的|了|吗|呢|啊|吧|嘛|哦|呀|咯|嘞|哈")
_ORDINAL_PREFIX = re.compile(r"^\s*(?:第[零一二三四五六七八九十百千万〇两\d０-９]+[章节回卷集部篇话]|[0-9０-９]+[.、]|[（(]\s*[零一二三四五六七八九十百千万〇两\d０-９]+\s*[)）]|【[^】]*】)\s*")


def _content_descriptor(body: str, max_len: int = 15) -> str:
    """正文首句/首事件 8-15 字描述符 (A.0 规则版,Phase A 提取后回填更准)。"""
    b = (body or "").strip()
    if not b:
        return ""
    first = next((s.strip() for s in _SENT_SPLIT.split(b) if s.strip()), "")
    return first[:max_len]


def _strip_ordinal(title: str) -> str:
    return _ORDINAL_PREFIX.sub("", title or "").strip()


def annotate_weird_titles(
    chapters: list[dict],
    *,
    embedder: Callable[[list[str]], list] | None = None,
    sim_threshold: float = 0.18,
) -> list[dict]:
    """标注 title_confidence + 低可信生成 content_descriptor。

    embedder: 可选 BGE-M3 嵌入函数 (Phase B 注入真实模型)。不传则纯启发式降级。
    """
    # 嵌入路径 (可选):标题 vs 正文首 ~500 字相似度
    sims: dict[int, float] = {}
    if embedder is not None:
        try:
            texts: list[str] = []
            index: list[int] = []
            for i, ch in enumerate(chapters):
                title_core = _strip_ordinal(ch.get("title") or "")
                body_head = (ch.get("content") or "")[:500]
                if title_core and body_head:
                    texts.append(title_core)
                    texts.append(body_head)
                    index.append(i)
            if texts:
                vecs = embedder(texts)
                for k, i in enumerate(index):
                    sims[i] = _cosine(vecs[2 * k], vecs[2 * k + 1])
        except Exception:
            sims = {}  # 嵌入失败 → 全降级启发式

    for i, ch in enumerate(chapters):
        title = (ch.get("title") or "").strip()
        body = ch.get("content") or ""
        core = _strip_ordinal(title)
        conf = 1.0

        # 启发 1:玩梗词
        if _MEME_WORDS.search(title):
            conf -= 0.5
        # 启发 2:去掉序号后空 (纯"第X章") → 无信息但不算怪,中性
        if not core:
            conf -= 0.0
        else:
            # 启发 3:口语连接词密度高 + 短 → 像吐槽
            colloq = len(_COLLOQUIAL.findall(core))
            if len(core) <= 16 and colloq >= 3:
                conf -= 0.3
            # 启发 4:核心几乎无"名词感"(全是口语/标点/数字)
            cjk = re.findall(r"[一-鿿]", core)
            non_colloq_cjk = [c for c in cjk if not _COLLOQUIAL.match(c)]
            if cjk and len(non_colloq_cjk) / max(1, len(cjk)) < 0.3:
                conf -= 0.3

        # 嵌入信号 (若有):标题 vs 正文相似度低 → 标题不代表内容
        if i in sims and sims[i] < sim_threshold:
            conf -= 0.3

        conf = max(0.0, min(1.0, conf))
        ch["title_confidence"] = round(conf, 3)
        # 低可信 → 生成内容描述符;下游提取/时间线用它而非原标题
        ch["content_descriptor"] = _content_descriptor(body) if conf < 0.6 else ""
    return chapters


def _cosine(a, b) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return num / (na * nb)
