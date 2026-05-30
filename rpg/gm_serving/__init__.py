"""rpg.gm_serving — Phase D GM serving 辅助层。

三层注入(常驻 constant + 查询工具 + 写工具)、规范世界线引导、影响因子抗污染。
设计 docs/design/D_gm_serving.md。(注:与 agents/gm 的 GameMaster 分开,后者是 LLM 客户端。)
"""
