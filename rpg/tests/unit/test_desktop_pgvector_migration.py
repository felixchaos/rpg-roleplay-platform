"""桌面捆绑版 pgvector:存量库升级迁移的守卫(纯文本断言,不连库)。

从 test_desktop_pgvector_bundle.py 拆出来:这条断言的是具体 migration 编号(开源线
100 = adopt_pgvector_after_it_becomes_available),而生产线的 migration 编号与开源线不同、
也没有这条只对桌面库有意义的迁移。拆开后 test_desktop_pgvector_bundle.py 两条线逐字节相同,
本文件只在开源线。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # → rpg/
MIGRATIONS = (ROOT / "platform_app" / "db" / "migrations.py").read_text(encoding="utf-8")


# ── ④ 存量库升级路径:pgvector 从无到有时要把 jsonb 占位列换成真向量列 ────

def test_migration_100_adopts_pgvector_for_existing_dbs():
    """光让新包带 pgvector 不够:老库里 v89 建的是 jsonb 占位列,
    udt_name != 'vector' → 检索永远退化。必须有迁移把它们换成真向量列。"""
    assert '(100, "adopt_pgvector_after_it_becomes_available"' in MIGRATIONS, \
        "缺 migration 100:存量桌面库升级后不会真正启用 pgvector"
    seg = MIGRATIONS[MIGRATIONS.find('(100, "adopt_pgvector_after_it_becomes_available"'):]
    seg = seg[: seg.find("]\n\n\ndef _assert_migrations_monotonic")]
    # 无 pgvector 的部署上必须整块不动(否则会去 drop 人家的占位列却建不出向量列)
    assert "if not exists (select 1 from pg_extension where extname = 'vector') then" in seg
    assert "return;" in seg
    # 只动 jsonb 占位列,健康 prod 库(已是 vector 类型)必须 no-op
    assert "udt_name='jsonb'" in seg
    # canon 占位列可能真存了嵌入(写入路径无 ::vector cast)→ 必须先试保值转换
    assert "using (case when embedding is null then null else embedding::text::vector end)" in seg
    assert "exception when others then" in seg, \
        "转换失败必须兜底,不能让一次迁移把桌面 app 卡在起不来"
