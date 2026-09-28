"""桌面捆绑版 pgvector 的守卫测试(纯文本断言,不连库、不跑构建)。

背景(2026-08 用户实测):Windows 开箱即用版装完后 `pg\\share\\extension\\vector.control`
不存在 —— 组装脚本把 pgvector 标成「默认跳过」。表面一切正常,直到建 RAG 向量才暴露。
根因不止「没编」:还有两个会让修复【静默失效】的坑 —— CI 运行时缓存 key 不带 pgvector
版本(命中旧缓存 = 修了等于没修),以及存量库里的 jsonb 占位列没人换成真向量列。

前者锁在本文件。后者的守卫断言的是具体 migration 编号,而生产线与开源线的 migration
编号不同,所以放在开源线单独的 test_desktop_pgvector_migration.py 里;本文件两条线逐字节相同。

风格延续 test_rath_engine_v4.py:直接读源码/CI 配置文本核实关键约定是否还在。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # → rpg/
REPO = ROOT.parent                                   # → 仓库根

PS1 = (REPO / "desktop" / "scripts" / "bundle-backend.ps1").read_text(encoding="utf-8")
SH = (REPO / "desktop" / "scripts" / "bundle-backend.sh").read_text(encoding="utf-8")
RELEASE_WF = (REPO / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
WARM_WF = (REPO / ".github" / "workflows" / "warm-runtime-cache.yml").read_text(encoding="utf-8")


def _ps1_pgvector_version() -> str:
    m = re.search(r"\$PgvectorVer\s*=\s*'v?([0-9.]+)'", PS1)
    assert m, "bundle-backend.ps1 里找不到 $PgvectorVer"
    return m.group(1)


# ── ① Windows 必须真的编 pgvector,且缺件要让构建失败 ──────────────────

def test_windows_bundle_builds_pgvector():
    """不能再回到「默认跳过」。$BuildPgvector 开关本身已删,若有人加回来必须是 $true。"""
    assert "nmake /F Makefile.win" in PS1, "Windows 侧必须就地编 pgvector"
    assert re.search(r"\$BuildPgvector\s*=\s*\$false", PS1) is None, \
        "pgvector 不许再默认跳过——跳过就是出一个「装完才发现坏」的包"


def test_windows_bundle_fails_when_pgvector_missing():
    """静默出包是这个 bug 的真正代价:缺件必须当场让构建红,不许一路走到发布。"""
    assert "vector.control" in PS1, "组装脚本必须校验 vector.control 落地"
    assert "vector.dll" in PS1, "组装脚本必须校验 vector.dll 落地"


# ── ② CI 运行时缓存 key 必须带 pgvector 版本(否则修了等于没修)──────────

def test_runtime_cache_key_carries_pgvector_version():
    """运行时缓存把整棵 pg/ 树缓起来。只改组装脚本不动 key,CI 会恢复一份【不含
    vector.dll 的旧 pg】直接复用 → 修复静默失效。两个 workflow 的 key 必须同步带版本。"""
    ver = _ps1_pgvector_version()
    for name, text in (("desktop-release.yml", RELEASE_WF), ("warm-runtime-cache.yml", WARM_WF)):
        keys = re.findall(r"key:\s*rt-.*", text)
        assert keys, f"{name} 里找不到运行时缓存 key"
        for key in keys:
            assert f"pgv{ver}" in key, (
                f"{name} 的缓存 key 未带当前 pgvector 版本 pgv{ver}:{key}\n"
                f"升 pgvector 必须同步改 bundle-backend.ps1 + 两个 workflow 的 key。"
            )


# ── ③ macOS 刻意不带 pgvector:是决策不是遗漏,理由必须留在代码里 ─────────

def test_macos_skip_is_documented_not_accidental():
    """mac 用 zonky(minos 12.0)换 theseus-rs(minos 26.0)会打死大批用户,
    所以刻意保持降级。别让后人以为这是忘了做而顺手「对齐」。"""
    assert "minos" in SH, "macOS 跳过 pgvector 的理由(zonky/theseus 部署目标差异)必须写在脚本里"

