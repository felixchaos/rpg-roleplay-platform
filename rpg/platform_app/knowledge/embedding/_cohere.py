"""platform_app.knowledge.embedding._cohere — Cohere embed API v2 通道。

拆包前住在单文件 embedding.py。纯机械搬家,行为零变化。
"""
from __future__ import annotations

from . import _breaker
from ._base import log


def _embed_via_cohere(model: str, api_key: str, texts: list[str]) -> list[list[float]] | None:
    """Cohere embed API v2。"""
    try:
        import cohere  # type: ignore
        co = cohere.Client(api_key)
        resp = co.embed(texts=texts, model=model, input_type="search_document")
        return [list(e) for e in resp.embeddings]
    except ImportError:
        log.warning("[embedding] cohere SDK not installed; pip install cohere")
        _breaker.note(_breaker.KIND_CONFIG, friendly="服务器没有安装 Cohere SDK,请换一个向量嵌入供应商。")
        return None
    except Exception as e:
        log.warning("[embedding] cohere embed failed: %s", e)
        code = getattr(e, "status_code", None)
        if isinstance(code, int):
            _breaker.note_http(code, body=str(e), friendly=f"Cohere 向量嵌入请求失败(HTTP {code})。")
        else:
            _breaker.note_exception(e, "api.cohere.com")
        return None
