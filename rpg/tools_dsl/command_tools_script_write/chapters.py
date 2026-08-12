"""command_tools_script_write §章节读写族(拆包 2026-07-14,纯机械搬家零行为变化)。

章节读(get_chapter_context / get_chapter_text / search_manuscript)+ 章节写
(update_script_chapter / create_script_chapter)+ 拖入文档确定性拆章导入
(read_uploaded_document / preview_document_split / import_document_as_chapters)。
读工具用 _user_can_read_script 读级闸;写工具在函数内用 script_owned 严格 owner 闸。
"""
from __future__ import annotations

from typing import Any

from ._helpers import _resolve_sid, _user_can_read_script


def _t_get_chapter_context(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """一次性取「该章相关编辑环境」(相关世界书/人物/词条/时点/前情)——编辑前建立设定认知,
    免去逐个 list_*/get_* 多轮往返、也防 agent 凭空写。复用阶段1 build_editor_environment,
    按 chapter_index 防剧透截断(只给 ≤当前章 的设定/历史)。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    ci_raw = args.get("chapter_index")
    try:
        ci = int(ci_raw) if ci_raw is not None else None
    except (TypeError, ValueError):
        ci = None
    try:
        from platform_app.db import connect, init_db
        init_db()
        scan = ""
        with connect() as db:
            if not _user_can_read_script(db, sid, user_id):
                return f"失败 (权限): 剧本 #{sid} 不属于当前用户或未订阅"
            if ci is not None:
                row = db.execute(
                    "select content from script_chapters where script_id=%s and chapter_index=%s",
                    (sid, ci),
                ).fetchone()
                scan = str((row or {}).get("content") or "")[:12000]
        from console_assistant.editor_context import build_editor_environment
        env = build_editor_environment(sid, scan, ci)
        return env or "(该章未提取到相关设定;可能本章正文为空,或世界书/人物卡/canon 尚未建立。)"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_get_chapter_text(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """读取某章【完整正文】(script_chapters.content)。修锚点/核对设定/写作参考前读真正文,而不是只看
    可能被垃圾污染的摘要(群反馈 行者无疆:之前 agent 没有直接读完整章节正文的工具)。owner/订阅可读;
    长章用 offset 分段续读。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    try:
        ci = int(args.get("chapter_index"))
    except (TypeError, ValueError):
        return "失败: chapter_index 必填(整数章号)"
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        max_chars = int(args.get("max_chars") or 12000)
    except (TypeError, ValueError):
        max_chars = 12000
    max_chars = max(500, min(max_chars, 20000))
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _user_can_read_script(db, sid, user_id):
                return f"失败 (权限): 剧本 #{sid} 不属于当前用户或未订阅"
            row = db.execute(
                "select title, content from script_chapters where script_id=%s and chapter_index=%s",
                (sid, ci),
            ).fetchone()
        if not row:
            return f"失败: 剧本 #{sid} 第 {ci} 章不存在"
        title = str(row.get("title") or "")
        content = str(row.get("content") or "")
        total = len(content)
        if total == 0:
            return f"【第{ci}章 {title}】(本章正文为空)"
        chunk = content[offset:offset + max_chars]
        end = offset + len(chunk)
        head = f"【第{ci}章 {title}】 正文共 {total} 字符,本段 [{offset}, {end})"
        head += (f";还有更多 → 再调本工具传 offset={end} 续读" if end < total else ";(本章已读完)")
        return head + "\n\n" + chunk
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_search_manuscript(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """全书检索:在剧本所有章节正文里搜一个词/短语/正则,返回命中的【章号 + 标题 + 上下文片段 + 字符偏移】。

    这是「先读后写、避免与全书矛盾」真正落地的关键工具(群反馈 行者无疆:agent 之前只能逐章硬读,
    无法跨全书核对)。审稿查重复、查前文是否已交代过某设定、找某人物/物件上次出场、核对伏笔有没有
    回收 —— 都先用它一次定位,再用 get_chapter_text(chapter_index, offset=@值) 精读上下文。
    默认大小写无关的子串匹配;regex=true 时按 Python 正则。可用 chapter_min/chapter_max 缩范围。
    只读,owner/订阅者可用。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    query = str(args.get("query") or "").strip()
    if not query:
        return "失败: query 必填(要搜索的词/短语/正则)"
    use_regex = bool(args.get("regex"))

    def _opt_int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(int(args.get(key)), hi))
        except (TypeError, ValueError):
            return default

    max_results = _opt_int("max_results", 30, 1, 100)
    ctx_chars = _opt_int("context_chars", 60, 20, 200)

    def _opt_chapter(key: str):
        try:
            return int(args.get(key))
        except (TypeError, ValueError):
            return None
    ch_min, ch_max = _opt_chapter("chapter_min"), _opt_chapter("chapter_max")

    import re as _re
    try:
        pat = _re.compile(query if use_regex else _re.escape(query), _re.I)
    except _re.error as exc:
        return f"失败: 正则无效: {exc}"

    HARD_SCAN_CAP = 1000  # 防病态正则(如匹配每个字符)在 485 万字全书上炸开;到顶即停并提示收窄
    try:
        from platform_app.db import connect, init_db
        init_db()
        where = "script_id=%s"
        params: list[Any] = [sid]
        if ch_min is not None:
            where += " and chapter_index>=%s"
            params.append(ch_min)
        if ch_max is not None:
            where += " and chapter_index<=%s"
            params.append(ch_max)
        # 子串搜索把粗筛下推给 DB(ILIKE 是超集,Python 再精确匹配 → 无漏判);正则则全取在 Python 扫。
        if not use_regex:
            where += " and content ILIKE %s"
            params.append(f"%{query}%")
        with connect() as db:
            if not _user_can_read_script(db, sid, user_id):
                return f"失败 (权限): 剧本 #{sid} 不属于当前用户或未订阅"
            rows = db.execute(
                f"select chapter_index, title, content from script_chapters where {where} order by chapter_index",
                tuple(params),
            ).fetchall()
        hits: list[str] = []
        total_hits = 0
        chapters_with_hits = 0
        capped = False
        for row in rows:
            ci = row.get("chapter_index")
            title = str(row.get("title") or "")
            content = str(row.get("content") or "")
            if not content:
                continue
            ch_hit = False
            for m in pat.finditer(content):
                total_hits += 1
                if len(hits) < max_results:
                    ch_hit = True
                    s = max(0, m.start() - ctx_chars)
                    e = min(len(content), m.end() + ctx_chars)
                    snippet = content[s:e].replace("\n", " ").strip()
                    prefix = "…" if s > 0 else ""
                    suffix = "…" if e < len(content) else ""
                    hits.append(f"【第{ci}章 {title}】@{m.start()}: {prefix}{snippet}{suffix}")
                if total_hits >= HARD_SCAN_CAP:
                    capped = True
                    break
            if ch_hit:
                chapters_with_hits += 1
            if capped:
                break
        if not hits:
            scanned = len(rows) if use_regex else f"{len(rows)} 个含「{query}」的"
            return f"全书检索「{query}」:0 命中(扫了 {scanned} 章)。"
        listed_note = ""
        if len(hits) < total_hits or capped:
            listed_note = (f"(命中较多,只列前 {len(hits)} 条" +
                           ("、且已达扫描上限" if capped else "") +
                           ";可加 chapter_min/chapter_max 收窄或调 max_results)")
        head = (f"全书检索「{query}」:{'≥' if capped else ''}{total_hits} 处命中,分布在 "
                f"{chapters_with_hits} 章{listed_note}。"
                f"用 get_chapter_text(chapter_index, offset=@值) 精读上下文。")
        return head + "\n" + "\n".join(hits)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_update_script_chapter(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    chapter_index = args.get("chapter_index")
    if chapter_index is None:
        return "失败: chapter_index 必填"
    try:
        ci = int(chapter_index)
    except (TypeError, ValueError):
        return "失败: chapter_index 必须是整数"
    title = args.get("title")
    content = args.get("content")
    volume_title = args.get("volume_title")
    if title is None and content is None and volume_title is None:
        return "失败: 至少要传 title / content / volume_title 之一"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        # ② 严格 owner 闸(在 update_chapter 内部也会再查一次 script_owned,这里前置一道
        #    给出统一的工具友好失败串;update_chapter 自身的 ValueError 兜底捕获)。
        prior: dict | None = None
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            # 撤销快照:落库前抓本章当前值,存进 commit.payload.before,让作者一键撤销 AI 改动。
            prior = db.execute(
                "select title, content, volume_title from script_chapters where script_id=%s and chapter_index=%s",
                (sid, ci),
            ).fetchone()
        # ③ 复用现成写函数 platform_app.script_import.update_chapter(自带 owner 校验 + word_count 同步)。
        from platform_app.script_import import update_chapter
        update_chapter(
            user_id, sid, ci,
            title=(str(title) if title is not None else None),
            content=(str(content) if content is not None else None),
            volume_title=(str(volume_title) if volume_title is not None else None),
        )
        # 审计 + 撤销:章节正文走 script_commits(kind=chapter_edit),payload.before 存改前全文供一键撤销。
        try:
            from platform_app.api.script_edit import _write_commit
            changed = [k for k in ("title", "content", "volume_title") if args.get(k) is not None]
            before_content = str((prior or {}).get("content") or "")
            # 内容护栏:超长正文(>100k)不存 before,撤销不可用(极罕见;避免 commit jsonb 爆炸)。
            undoable = bool(prior) and len(before_content) <= 100000
            before_payload = {
                "title": (prior or {}).get("title"),
                "content": before_content if undoable else None,
                "volume_title": (prior or {}).get("volume_title"),
            } if prior else None
            with connect() as adb:
                if script_owned(adb, sid, user_id):
                    _write_commit(
                        adb, script_id=sid, user_id=user_id,
                        kind="chapter_edit",
                        message=f"编辑章节 #{ci}",
                        payload={
                            "table": "script_chapters", "op": "edit",
                            "ids": {"chapter_index": ci},
                            "fields": changed,
                            "before": before_payload,
                            "undoable": undoable,
                            "is_new": prior is None,
                        },
                    )
                    adb.commit()
        except Exception:
            pass  # 审计失败不影响写主流程
        return f"已更新章节 #{ci}(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_create_script_chapter(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """在剧本末尾「新增」一章(title 必填,content 可选)。续写新章时用。
    要改已有章用 update_script_chapter(先 get_script_chapters 看现有章号)。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    title = str(args.get("title") or "").strip()
    if not title:
        return "失败: title 必填(新章标题)"
    content = str(args.get("content") or "")
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            mx = db.execute(
                "select coalesce(max(chapter_index),0) as m from script_chapters where script_id=%s",
                (sid,),
            ).fetchone()
            ci = int(mx["m"]) + 1
            db.execute(
                "insert into script_chapters(script_id, chapter_index, title, content, word_count, "
                "volume_title, source_marker, confidence) values (%s,%s,%s,%s,%s,%s,'manual',1.0)",
                (sid, ci, title[:200], content, len(content), str(args.get("volume_title") or "")),
            )
            db.execute(
                "update scripts set chapter_count=(select count(*) from script_chapters where script_id=%s),"
                " updated_at=now() where id=%s", (sid, sid),
            )
            try:
                from platform_app.api.script_edit import _write_commit
                _write_commit(db, script_id=sid, user_id=user_id, kind="chapter_add",
                              message=f"新增章节 #{ci}「{title[:40]}」",
                              payload={"table": "script_chapters", "op": "add",
                                       "ids": {"chapter_index": ci}})
            except Exception:
                pass
            db.commit()
        return f"已新建章节 #{ci}「{title}」(剧本 #{sid},{len(content)} 字)"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_read_uploaded_document(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """读取用户拖入的暂存文档的一段(offset/limit 分片读)。用于「按文档某段改写/建角色」等指令。"""
    doc_id = str(args.get("doc_id") or "").strip()
    if not doc_id:
        return "失败: doc_id 必填(用户拖入文档后会给到)"
    from platform_app.agent_docs import load_doc
    doc = load_doc(user_id, doc_id)
    if not doc:
        return f"失败: 文档 {doc_id} 不存在或已过期(请用户重新拖入)"
    try:
        offset = max(0, int(args.get("offset") or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = min(20000, max(1, int(args.get("limit") or 6000)))
    except (TypeError, ValueError):
        limit = 6000
    text = doc["content"] or ""
    seg = text[offset:offset + limit]
    nxt = offset + len(seg)
    tail = f"\n…(还有 {len(text) - nxt} 字,继续 offset={nxt})" if nxt < len(text) else ""
    return f"文档「{doc.get('filename') or 'doc'}」[{offset}:{nxt}]/共{len(text)}字:\n{seg}{tail}"


def _split_doc_or_err(user_id: int, args: dict):
    """共用:取暂存文档 + 跑确定性拆分器。返回 (chapters, report, doc) 或 (None, 错误串, None)。"""
    doc_id = str(args.get("doc_id") or "").strip()
    if not doc_id:
        return None, "失败: doc_id 必填", None
    from platform_app.agent_docs import load_doc
    doc = load_doc(user_id, doc_id)
    if not doc:
        return None, f"失败: 文档 {doc_id} 不存在或已过期(请用户重新拖入)", None
    rule = str(args.get("split_rule") or "auto").strip() or "auto"
    custom = str(args.get("custom_pattern") or "")
    if rule == "custom" and not custom.strip():
        return None, "失败: split_rule=custom 时必须给 custom_pattern", None
    try:
        from chapter_splitter import chapter_splitter as _splitter
        chapters, report = _splitter.split_chapters_with_report(
            doc["content"], split_rule=rule, custom_pattern=custom,
            source_name=doc.get("filename") or "doc.txt", title="",
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"失败: 拆分出错: {type(exc).__name__}: {exc}", None
    return chapters, report, doc


def _t_preview_document_split(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """预览:把拖入文档按规则拆成几章、章节标题是什么(只读,不落库)。确认后再 import_document_as_chapters。"""
    chapters, report, doc = _split_doc_or_err(user_id, args)
    if chapters is None:
        return report  # 错误串
    if not chapters:
        return "拆不出任何章节 —— 文档可能没有章节标记。可换 split_rule(chapter_cn/chapter_en/number_dot/custom)再试。"
    titles = [str(c.get("title") or f"第{i + 1}章")[:50] for i, c in enumerate(chapters)]
    head = "、".join(f"#{i + 1}「{t}」" for i, t in enumerate(titles[:30]))
    more = f" …(共 {len(titles)} 章)" if len(titles) > 30 else ""
    mode = (report or {}).get("split_mode") or (report or {}).get("split_rule") or "auto"
    words = sum(len(c.get("content") or "") for c in chapters)
    return (f"文档「{doc.get('filename') or 'doc'}」按[{mode}]可拆成 {len(chapters)} 章、共 {words} 字。"
            f"章节:{head}{more}。确认后用 import_document_as_chapters(doc_id, split_rule, "
            f"mode=append|replace) 落库。")


def _t_import_document_as_chapters(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """把拖入文档【确定性】拆章并写入剧本。mode=append(默认,末尾追加)/replace(清空现有章再导入)。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    chapters, report, doc = _split_doc_or_err(user_id, args)
    if chapters is None:
        return report
    if not chapters:
        return "失败: 拆不出任何章节(换个 split_rule 试,或文档无章节标记)"
    mode = str(args.get("mode") or "append").strip().lower()
    if mode not in ("append", "replace"):
        mode = "append"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            if mode == "replace":
                db.execute("delete from script_chapters where script_id=%s", (sid,))
                start = 1
            else:
                mx = db.execute(
                    "select coalesce(max(chapter_index),0) m from script_chapters where script_id=%s",
                    (sid,),
                ).fetchone()
                start = int(mx["m"]) + 1
            n = 0
            for i, c in enumerate(chapters):
                ci = start + i
                title = str(c.get("title") or f"第{ci}章")[:200]
                content = str(c.get("content") or "")
                db.execute(
                    "insert into script_chapters(script_id, chapter_index, title, content, word_count,"
                    " volume_title, source_marker, confidence) values (%s,%s,%s,%s,%s,%s,'doc_import',%s)",
                    (sid, ci, title, content, len(content),
                     str(c.get("volume_title") or ""), float(c.get("confidence") or 0.8)),
                )
                n += 1
            db.execute(
                "update scripts set chapter_count=(select count(*) from script_chapters where script_id=%s),"
                " word_count=(select coalesce(sum(word_count),0) from script_chapters where script_id=%s),"
                " updated_at=now() where id=%s", (sid, sid, sid),
            )
            try:
                from platform_app.api.script_edit import _write_commit
                _write_commit(db, script_id=sid, user_id=user_id, kind="doc_import_chapters",
                              message=f"文档「{doc.get('filename') or 'doc'}」导入 {n} 章({mode})",
                              payload={"table": "script_chapters", "op": "import",
                                       "count": n, "mode": mode})
            except Exception:
                pass
            db.commit()
        rng = f"第{start}–{start + n - 1}章" if n > 1 else f"第{start}章"
        label = "替换为" if mode == "replace" else "追加"
        return f"已把文档「{doc.get('filename') or 'doc'}」拆成 {n} 章{label}剧本 #{sid}({rng})。"
    except Exception as exc:  # noqa: BLE001
        return f"失败: {type(exc).__name__}: {exc}"


