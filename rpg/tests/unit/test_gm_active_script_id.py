"""深度审计修复:_active_script_id 必须能拿到 script_id(剧本级 gm_style 才生效)。

回归点:resolve_content_pack(state) 不传 script_id 会落到 '__legacy_novel__',
只靠 content_pack.id 会让剧本级旋钮对大多数存档静默失效 → 必须有 _active_save_id
→ game_saves.script_id 的权威兜底。
"""
import contextlib
import types

from agents.gm.master import GameMaster


def _fake_self(data):
    return types.SimpleNamespace(_active_state=types.SimpleNamespace(data=data))


def test_uses_content_pack_id_when_script_form():
    s = _fake_self({"content_pack": {"id": "script:77"}})
    assert GameMaster._active_script_id(s) == 77


def test_falls_back_to_save_id_lookup(monkeypatch):
    # content_pack.id 是 __legacy_novel__(真实存档常态)→ 必须走 save_id 兜底
    class _Row(dict):
        pass

    class _DB:
        def execute(self, sql, params):
            assert params == (55,)
            self._row = _Row(script_id=903)
            return self
        def fetchone(self):
            return self._row

    @contextlib.contextmanager
    def _connect():
        yield _DB()

    import platform_app.db as dbmod
    monkeypatch.setattr(dbmod, "connect", _connect)

    s = _fake_self({"content_pack": {"id": "__legacy_novel__"}, "_active_save_id": 55})
    assert GameMaster._active_script_id(s) == 903


def test_none_when_no_state():
    s = types.SimpleNamespace(_active_state=None)
    assert GameMaster._active_script_id(s) is None


def test_none_when_no_clues():
    s = _fake_self({"content_pack": {"id": "__freeform__"}})  # 无 save_id、非 script
    assert GameMaster._active_script_id(s) is None
