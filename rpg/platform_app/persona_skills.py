"""persona_skills.py —— 用户「人格 skill」导入(skill.md 上传 / GitHub 拉取)。

设计要点(见 project_skill_import 设计):
- **纯数据,绝不执行任何代码**:与 admin-only 的可执行 imported_skills(skill_executor)完全分离。
  导入的 markdown 仅作为「角色档案原文」喂给蒸馏 LLM,产出结构化角色卡字段。
- **每用户隔离**:user_persona_skills.user_id FK cascade + 唯一 slug;所有查询都带 user_id。
- **安全**:GitHub 拉取走 core.outbound.safe_get_bytes(SSRF 防护:DNS 重解析 + IP pin + 禁重定向);
  仅 .md 文件、大小/数量/总量上限(防压缩炸弹/资源耗尽);蒸馏输出走 upsert_user_card 的字段长度上限。
- **生图复用**:打通已有 enqueue_image_generation 人设图管线(BYOK 闸 + 日额度由其把关)。
"""
from __future__ import annotations

import io
import json
import re
import tarfile
from typing import Any

from core.logging import get_logger
from platform_app.db import connect, init_db

log = get_logger(__name__)

# ── 安全上限 ────────────────────────────────────────────────────────────
_MAX_FILES = 80
_MAX_FILE_BYTES = 256 * 1024        # 单文件 ≤256KB
_MAX_TOTAL_BYTES = 2 * 1024 * 1024  # 全部 markdown 合计 ≤2MB
_MAX_FETCH_BYTES = 6 * 1024 * 1024  # GitHub tarball 下载上限 6MB
_MAX_DISTILL_CHARS = 40_000         # 喂给蒸馏 LLM 的合并文本上限(LLM 内部还会再截)
# 优先级排序:核心人格文件靠前,保证 distill 内部截断时留住最重要内容。
_PRIORITY = ["skill.md", "personality.md", "profile.md", "interaction.md",
             "background_story.md", "memory.md", "relations.md", "conflicts.md"]


def _slug_from_name(name: str) -> str:
    from platform_app.user_cards import _slugify
    return _slugify(name) or "persona-skill"


# ── GitHub 拉取 ─────────────────────────────────────────────────────────
def _parse_github_repo(url: str) -> tuple[str, str, str | None]:
    """解析 github 仓库地址 → (owner, repo, branch|None)。支持:
    https://github.com/owner/repo[.git] / .../tree/<branch> / 裸 github.com/owner/repo。
    """
    u = str(url or "").strip()
    if not u:
        raise ValueError("缺少 GitHub 仓库地址")
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www\.)?github\.com/", "", u)
    parts = [p for p in u.split("/") if p]
    if len(parts) < 2:
        raise ValueError("GitHub 地址格式应为 owner/repo")
    owner, repo = parts[0], parts[1]
    repo = re.sub(r"\.git$", "", repo)
    branch: str | None = None
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        branch = parts[3]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub owner/repo 含非法字符")
    return owner, repo, branch


def _fetch_github_markdown(url: str) -> tuple[list[tuple[str, str]], str]:
    """拉取 GitHub 仓库的全部 .md 文件。返回 ([(filename, text)], canonical_repo_url)。"""
    from core.outbound import safe_get_bytes

    owner, repo, branch = _parse_github_repo(url)
    canonical = f"https://github.com/{owner}/{repo}"
    branches = [branch] if branch else ["main", "master"]
    data: bytes | None = None
    for br in branches:
        codeload = f"https://codeload.github.com/{owner}/{repo}/tar.gz/refs/heads/{br}"
        try:
            # 平台自己拉公开仓库,不是某个供应商凭据的出站,没有凭据代理可用(本地模式跟随系统代理)。
            data = safe_get_bytes(codeload, max_bytes=_MAX_FETCH_BYTES, max_redirects=2, proxy=None)
            if data:
                break
        except Exception as exc:  # 404/网络 → 试下一个分支
            log.info("[persona-skill] github fetch %s@%s failed: %s", repo, br, exc)
            data = None
    if not data:
        raise ValueError("拉取失败:仓库不存在或非公开(仅支持公开仓库的 main/master 分支)")
    return _extract_md_from_tar(data), canonical


def _extract_md_from_tar(data: bytes) -> list[tuple[str, str]]:
    """从 tar.gz 字节里安全提取所有 .md 文件(仅常规文件、防穿越、限大小/数量/总量)。"""
    out: list[tuple[str, str]] = []
    total = 0
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except Exception as exc:
        raise ValueError(f"压缩包解析失败: {exc}") from exc
    with tf:
        for member in tf:
            if len(out) >= _MAX_FILES:
                break
            if not member.isreg():            # 跳过目录/符号链接/设备等
                continue
            name = member.name or ""
            if ".." in name or name.startswith("/"):   # 路径穿越防御
                continue
            base = name.rsplit("/", 1)[-1].lower()
            if not base.endswith(".md"):
                continue
            if member.size > _MAX_FILE_BYTES:
                continue
            if total + member.size > _MAX_TOTAL_BYTES:
                break
            try:
                fobj = tf.extractfile(member)
                if fobj is None:
                    continue
                raw = fobj.read(_MAX_FILE_BYTES + 1)
            except Exception:
                continue
            total += len(raw)
            text = raw.decode("utf-8", "ignore").strip()
            if text:
                out.append((base, text))
    if not out:
        raise ValueError("仓库里没有可用的 .md 文件")
    return out


# ── 规范化 + 取名 ────────────────────────────────────────────────────────
def _normalize_upload_files(files: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """上传路径:[{name, content}] → [(name, text)],套用同样的大小/数量上限。"""
    out: list[tuple[str, str]] = []
    total = 0
    for f in (files or [])[:_MAX_FILES]:
        if not isinstance(f, dict):
            continue
        name = str(f.get("name") or "skill.md").rsplit("/", 1)[-1].lower()
        if not name.endswith(".md"):
            name = name + ".md"
        text = str(f.get("content") or "")
        raw_len = len(text.encode("utf-8"))
        if raw_len > _MAX_FILE_BYTES or total + raw_len > _MAX_TOTAL_BYTES:
            continue
        total += raw_len
        text = text.strip()
        if text:
            out.append((name, text))
    if not out:
        raise ValueError("没有可用的 .md 内容")
    return out


def _frontmatter_name(text: str) -> str:
    """从 SKILL.md 的 YAML frontmatter 抓 name(确定性,不调 LLM)。"""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        mm = re.match(r"\s*name\s*:\s*(.+?)\s*$", line)
        if mm:
            return mm.group(1).strip().strip("'\"")
    return ""


def _derive_name(files: list[tuple[str, str]], fallback: str) -> str:
    """取角色名:SKILL.md frontmatter → manifest.json name → README 一级标题 → fallback。"""
    by = {n: t for n, t in files}
    if "skill.md" in by:
        nm = _frontmatter_name(by["skill.md"])
        if nm:
            return nm[:120]
    if "manifest.json" in by:
        try:
            j = json.loads(by["manifest.json"])
            if isinstance(j, dict) and str(j.get("name") or "").strip():
                return str(j["name"]).strip()[:120]
        except Exception:
            pass
    for n, t in files:
        mm = re.search(r"^#\s+(.+?)\s*$", t, re.MULTILINE)
        if mm:
            return mm.group(1).strip()[:120]
    return (fallback or "导入人格")[:120]


def _concat_for_distill(files: list[tuple[str, str]]) -> str:
    """按优先级合并 markdown 给蒸馏 LLM(核心人格文件靠前)。"""
    def rank(n: str) -> int:
        return _PRIORITY.index(n) if n in _PRIORITY else len(_PRIORITY)
    ordered = sorted(files, key=lambda ft: rank(ft[0]))
    chunks: list[str] = []
    used = 0
    for name, text in ordered:
        if name in ("manifest.json",):
            continue
        block = f"## {name}\n\n{text}\n"
        if used + len(block) > _MAX_DISTILL_CHARS:
            block = block[: max(0, _MAX_DISTILL_CHARS - used)]
        chunks.append(block)
        used += len(block)
        if used >= _MAX_DISTILL_CHARS:
            break
    return "\n".join(chunks).strip()


# ── 兼容层解析器(skill 原文直接用,与文件结构无关,不做脆弱的字段映射)────────
# 元信息/测试报告类文件不进角色定义。
_SKIP_FILES = {"manifest.json", "quality-report.md", "roleplay-test-report.md", "readme.md", "license", "license.md"}
_CONTENT_CAP = 30000   # 注入用角色定义原文上限(metadata.skill_content)
_DISPLAY_CAP = 15000   # 卡内 background(人类查看 + 游戏管线兜底注入)上限,略低于 16KB 列限


def _strip_md(text: str) -> str:
    """去掉 YAML frontmatter,留正文(保留标题,因正文整体作为角色定义注入)。"""
    return re.sub(r"^---\s*\n.*?\n---\s*\n", "", text or "", count=1, flags=re.DOTALL).strip()


def _frontmatter_description(text: str) -> str:
    m = re.search(r'^\s*description\s*:\s*["\']?(.+?)["\']?\s*$', text or "", re.MULTILINE)
    return m.group(1).strip() if m else ""


def build_skill_content(files: list[tuple[str, str]]) -> str:
    """把整包 skill 原文拼成一个【角色定义块】(逐字,不拆字段,任何文件结构通用)。
    SKILL.md(扮演规则)在最前,其余按文件名;每段带 `## 文件名` 头便于模型分辨。"""
    ordered = sorted(files, key=lambda ft: (ft[0] != "skill.md", ft[0]))
    parts: list[str] = []
    used = 0
    for fn, text in ordered:
        if fn in _SKIP_FILES:
            continue
        body = _strip_md(text)
        if not body:
            continue
        block = f"## {fn}\n{body}"
        if used + len(block) > _CONTENT_CAP:
            block = block[: max(0, _CONTENT_CAP - used)]
        parts.append(block)
        used += len(block)
        if used >= _CONTENT_CAP:
            break
    return "\n\n".join(parts).strip()


# ── 主流程 ──────────────────────────────────────────────────────────────
def import_persona_skill(
    user_id: int,
    *,
    source: str = "upload",
    files: list[dict[str, Any]] | None = None,
    repo_url: str = "",
    model_api_id: str | None = None,
    model: str | None = None,
    generate_image: bool = False,
    use_llm: bool = False,
) -> dict[str, Any]:
    """导入一个人格 skill → 直接映射成 character_cards(pc) + (仅当显式 generate_image=True 才)入队人设图 + 登记 user_persona_skills。

    **默认不自动生成人设图**(导入只建卡;人设图由用户在角色卡上手动触发,避免每次导入都烧生图额度)。

    默认**确定性直接映射**(skill 原文逐字进卡,扮演时模型直接读 skill 本身,不调 LLM、不有损、零成本)。
    use_llm=True 时再在映射结果上叠一层 LLM 归类整理(可选、要 BYOK)。返回 {ok, skill_id, card_id, card, image_status}。
    """
    if not user_id:
        raise ValueError("需要登录")
    init_db()

    # 1) 取 markdown 文件 + 溯源
    if source == "github":
        md_files, source_ref = _fetch_github_markdown(repo_url)
    else:
        md_files = _normalize_upload_files(files or [])
        source_ref = ""
        if files:
            source_ref = str((files[0] or {}).get("name") or "")[:200]

    # 2) 兼容层解析:取名 + frontmatter 简介 + 整包 skill 原文(不拆字段,与文件结构无关)。
    by = {n: t for n, t in md_files}
    name = _derive_name(md_files, fallback=source_ref or repo_url)
    description = _frontmatter_description(by.get("skill.md", ""))
    content = build_skill_content(md_files)   # 角色定义原文(逐字,priority 96 注入)

    # 3) 落卡(card_type='pc')—— 卡只是【可见把手 + 原始 skill 容器】:
    #    identity=简介(人类查看);background=原文(卡内查看 + 游戏管线兜底注入);
    #    metadata.skill_content=完整原文(扮演时经 tavern provider priority 96 逐字注入)。
    payload: dict[str, Any] = {
        "name": name,
        "identity": (description or name)[:600],   # 简短简介(人类可见);完整 skill 不塞展示字段
        # background 不再塞 30k 原文(每次 /api/state 下发 + 侧栏内联渲染 → 内存爆)。
        # 完整原文只存 metadata.skill_content,扮演时服务端注入、查看时前端按需拉。
        "background": "",
        "tags": ["人格skill"],
        "metadata": {
            "persona_skill": True,
            "skill_content": content,
            "source": source,
            "source_ref": source_ref,
            "skill_files": [n for n, _ in md_files][:_MAX_FILES],
        },
    }
    from platform_app.user_cards import upsert_user_card
    card = upsert_user_card(user_id, payload)
    card_id = int(card.get("id")) if card.get("id") is not None else None

    # 4) 人设图:打通已有生图管线(BYOK + 日额度由 enqueue 自身把关;失败只 log 不阻断)
    image_status = "skipped"
    if generate_image and card_id:
        try:
            from platform_app.image_jobs import enqueue_image_generation
            from platform_app.user_cards import build_persona_prompt
            r = enqueue_image_generation(
                user_id, prompt=build_persona_prompt(card), kind="persona",
                attach={"type": "persona_image", "id": card_id, "source": "persona_skill"},
            )
            image_status = "queued" if (isinstance(r, dict) and r.get("image_id")) else str(
                (r or {}).get("error") or "skipped")
        except Exception as exc:
            log.warning("[persona-skill] enqueue persona image failed: %s", exc)
            image_status = "error"

    # 5) 登记 user_persona_skills(每用户隔离;slug 唯一冲突即更新)
    slug = _slug_from_name(name)
    meta = {"source": source, "source_ref": source_ref,
            "skill_files": [n for n, _ in md_files][:_MAX_FILES]}
    with connect() as db:
        row = db.execute(
            """
            insert into user_persona_skills(user_id, slug, name, source, source_ref, card_id, status, metadata)
            values (%s, %s, %s, %s, %s, %s, 'ready', %s)
            on conflict (user_id, slug) do update set
              name = excluded.name, source = excluded.source, source_ref = excluded.source_ref,
              card_id = excluded.card_id, status = 'ready', metadata = excluded.metadata, updated_at = now()
            returning id
            """,
            (user_id, slug, name, source, source_ref, card_id, _jsonb(meta)),
        ).fetchone()
    skill_id = int(row["id"]) if row else None

    return {"ok": True, "skill_id": skill_id, "card_id": card_id,
            "card": card, "image_status": image_status}


def list_persona_skills(user_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        rows = db.execute(
            """
            select s.id, s.slug, s.name, s.source, s.source_ref, s.card_id, s.status,
                   s.created_at, s.updated_at, c.avatar_path
            from user_persona_skills s
            left join character_cards c on c.id = s.card_id and c.user_id = s.user_id
            where s.user_id = %s
            order by s.updated_at desc, s.id desc
            """,
            (user_id,),
        ).fetchall()
    items = [
        {
            "id": r["id"], "slug": r["slug"], "name": r["name"], "source": r["source"],
            "source_ref": r["source_ref"], "card_id": r["card_id"], "status": r["status"],
            "avatar_path": r.get("avatar_path"),
        }
        for r in rows
    ]
    return {"ok": True, "items": items, "total": len(items)}


def delete_persona_skill(user_id: int, skill_id: int) -> dict[str, Any]:
    """删除 skill 登记记录(不连带删卡;卡由用户自行在角色库删除)。"""
    init_db()
    with connect() as db:
        cur = db.execute(
            "delete from user_persona_skills where id = %s and user_id = %s returning id",
            (int(skill_id), user_id),
        ).fetchone()
    return {"ok": True, "deleted": bool(cur), "id": skill_id}


def _jsonb(obj: Any):
    from psycopg.types.json import Jsonb
    return Jsonb(obj or {})
