"""base_url 规整:剥掉用户误填的 /chat/completions 尾巴(中转站文档普遍把完整端点写成「接口地址」)。

回归:EvoMap 等中转站把 https://host/v1/chat/completions 标为接口地址,用户整段填进 base_url →
SDK 再拼 /chat/completions、/models 双双 404 →「不可访问 / 0 模型」。写时+读时都规整,自愈历史误填。
"""
import pytest

from platform_app.user_credentials import _normalize_openai_base_url as norm


@pytest.mark.parametrize("raw,expected", [
    # 核心:剥掉完整 chat 端点尾巴
    ("https://api.evomap.ai/v1/chat/completions", "https://api.evomap.ai/v1"),
    ("https://api.evomap.ai/v1/chat/completions/", "https://api.evomap.ai/v1"),
    ("  https://api.evomap.ai/v1/chat/completions  ", "https://api.evomap.ai/v1"),
    # 大小写无关
    ("https://api.evomap.ai/v1/CHAT/Completions", "https://api.evomap.ai/v1"),
    # 合法 base 不动
    ("https://api.evomap.ai/v1", "https://api.evomap.ai/v1"),
    ("https://api.openai.com/v1", "https://api.openai.com/v1"),
    ("https://x.com/v1beta/openai", "https://x.com/v1beta/openai"),  # Gemini 兼容路径不能误剥
    # 边界
    ("", ""),
    ("   ", ""),
])
def test_normalize_openai_base_url(raw, expected):
    assert norm(raw) == expected
