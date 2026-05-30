"""rpg.kb — 关系型 KB 读写 (Phase B 规范层 + Phase C 行级 COW 世界树)。

设计 docs/design/BC_kb_schema_worldtree.md。世界知识从 state blob 迁出进 kb_* 行级表,
kb_* 成唯一真相源;每行 born_commit;分支=沿 commit 谱系 recursive CTE 取 newest-per-key。
"""
