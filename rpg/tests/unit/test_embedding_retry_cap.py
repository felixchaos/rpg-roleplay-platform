"""embedding 批处理失败须有连续失败熔断,不可无限 30s 重试(provider 永久故障时
daemon 线程永 spin + _EMBED_QUEUE_RUNNING flag 永 True → 该 script 再不能重 embed)。"""
import unittest
from contextlib import contextmanager
from unittest import mock

from platform_app.knowledge import embedding

# 拆包后 _embed_chunks_loop / _embed_chunks_loop_inner 及其依赖 _resolve_embed_config /
# _embed_batch 住 embedding._writer;patch-where-defined:loop 内部按 _writer 命名空间解析。
# (embedding.time 仍是共享 time 模块,patch time.sleep 对 _writer 生效,无需重定向。)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows
    def fetchall(self):
        return self._rows


class _FakeDB:
    """每次 execute 都返回一批未 embed 的 row(模拟永远有活干 → 靠熔断退出)。"""
    def execute(self, sql, params=None):
        # 拉批查询返回非空;其它(meta bind 等)返回空
        if "embedding_vec is null" in sql:
            return _FakeResult([{"id": 1, "content": "x" * 50}])
        return _FakeResult([])


@contextmanager
def _fake_connect():
    yield _FakeDB()


class EmbeddingRetryCap(unittest.TestCase):
    def test_persistent_failure_raises_after_cap(self):
        import platform_app.db as _dbmod
        with mock.patch.object(_dbmod, "connect", _fake_connect), \
             mock.patch.object(embedding._writer, "_resolve_embed_config",
                               return_value=("api1", "model1", None, None)), \
             mock.patch.object(embedding._writer, "_embed_batch", return_value=None), \
             mock.patch.object(embedding.time, "sleep", lambda *_a, **_k: None):
            # _embed_batch 始终 None → 连续失败应在 _MAX_EMBED_BATCH_RETRIES 次后 raise(而非无限循环)
            with self.assertRaises(RuntimeError):
                embedding._embed_chunks_loop_inner(script_id=999, user_id=1)

    def test_wrapper_swallows_and_clears_flag(self):
        # 包装层 _embed_chunks_loop 捕获异常 + finally 清 flag → 不会卡 already_running
        embedding._EMBED_QUEUE_RUNNING[999] = True
        import platform_app.db as _dbmod
        with mock.patch.object(_dbmod, "connect", _fake_connect), \
             mock.patch.object(embedding._writer, "_resolve_embed_config",
                               return_value=("api1", "model1", None, None)), \
             mock.patch.object(embedding._writer, "_embed_batch", return_value=None), \
             mock.patch.object(embedding.time, "sleep", lambda *_a, **_k: None):
            embedding._embed_chunks_loop(script_id=999, user_id=1)  # 不应抛(wrapper 吞)
        self.assertFalse(embedding._EMBED_QUEUE_RUNNING.get(999, False),
                         "flag 应被 finally 清除,否则该 script 永远卡 already_running")

    def test_cap_is_bounded(self):
        self.assertLessEqual(embedding._MAX_EMBED_BATCH_RETRIES, 10)
        self.assertGreaterEqual(embedding._MAX_EMBED_BATCH_RETRIES, 2)


if __name__ == "__main__":
    unittest.main()
