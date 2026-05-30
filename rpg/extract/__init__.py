"""rpg.extract — Phase A 提取重构(discover-then-link 五阶段)。

替换 chapter_fact_indexer._extract_fact 关键词匹配。设计 docs/design/A_extraction.md。
铁律:纪元当种子钉死(永不让 LLM 推);逐章输出固定 schema 三元组;模型分层(便宜模型逐章)。
"""
