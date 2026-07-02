"""无 embedding 接口的 provider 被选成嵌入器 → 快速失败 + 各层拦截。

生产:用户把 deepseek 选成嵌入器(embed.api_id=deepseek + 默认模型 text-embedding-004=Google 模型)
→ 每批 404、重试 5 次(~2.5 分钟)才放弃、导入的小说 RAG 坏掉。各层校验都漏了(picker 只按模型名
heuristic、preflight 只查有无该 provider 凭据)。本文件锁住后端权威闸 + 前端 picker 排除。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
FRONTEND = REPO.parent / "frontend" / "src"


def test_provider_lacks_embedding():
    from platform_app.knowledge.embedding import provider_lacks_embedding
    for api in ("deepseek", "DeepSeek", "anthropic", "moonshot"):
        assert provider_lacks_embedding(api) is True, api
    for api in ("openai", "dashscope", "vertex_ai", "siliconflow", "cohere"):
        assert provider_lacks_embedding(api) is False, api
    # 自定义别名靠 base_url host 兜底
    assert provider_lacks_embedding("alias", "https://api.deepseek.com/v1") is True
    assert provider_lacks_embedding("alias", "https://api.anthropic.com") is True
    assert provider_lacks_embedding("proxy", "https://api.openai.com/v1") is False


def test_embed_loop_fast_fails_on_no_embedding_provider():
    """绑定前的权威闸:选了无 embedding 的 provider → 立刻抛清晰错误,不进 404 重试循环。"""
    src = (REPO / "platform_app" / "knowledge" / "embedding.py").read_text(encoding="utf-8")
    loop = src.split("_bind_api_id, _bind_model, _bind_key, _bind_base = _resolve_embed_config", 1)[1][:800]
    assert "provider_lacks_embedding(_bind_api_id, _bind_base)" in loop
    assert "raise RuntimeError" in loop


def test_preflight_flags_no_embedding_provider():
    src = (REPO / "platform_app" / "api" / "me.py").read_text(encoding="utf-8")
    assert "provider_lacks_embedding" in src
    assert "embed_provider_hint" in src


def test_frontend_picker_excludes_no_embedding_providers():
    picker = (FRONTEND / "components" / "AgentModelPicker.jsx").read_text(encoding="utf-8")
    assert "NO_EMBEDDING_PROVIDERS" in picker
    assert "capabilityFilter === 'embedding'" in picker
    assert "'deepseek'" in picker and "'anthropic'" in picker
