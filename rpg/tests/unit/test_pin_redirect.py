"""剧本分享审计修复:pin 检索重定向(引用剧本读 pin 目标,非 pin 零影响)。"""

from platform_app.knowledge._pin import effective_kb_script_id as _resolve_effective_script_id


class _DB:
    def __init__(self, row):
        self._row = row
    def execute(self, sql, params):
        self._params = params
        return self
    def fetchone(self):
        return self._row


def test_private_script_unchanged():
    db = _DB({"sharing_mode": "private", "current_pin_script_id": None})
    assert _resolve_effective_script_id(db, 10) == 10


def test_public_script_unchanged():
    db = _DB({"sharing_mode": "public", "current_pin_script_id": None})
    assert _resolve_effective_script_id(db, 11) == 11


def test_floating_latest_redirects_to_target():
    db = _DB({"sharing_mode": "floating-latest", "current_pin_script_id": 99})
    assert _resolve_effective_script_id(db, 11) == 99


def test_pinned_snapshot_redirects_to_target():
    db = _DB({"sharing_mode": "pinned-snapshot", "current_pin_script_id": 77})
    assert _resolve_effective_script_id(db, 11) == 77


def test_pinned_but_no_target_stays():
    # 标了 pin 模式但没设目标 → 不重定向(读自身,不炸)
    db = _DB({"sharing_mode": "floating-latest", "current_pin_script_id": None})
    assert _resolve_effective_script_id(db, 11) == 11


def test_missing_row_returns_self():
    db = _DB(None)
    assert _resolve_effective_script_id(db, 11) == 11


def test_db_error_returns_self():
    class _Boom:
        def execute(self, *a):
            raise RuntimeError("db down")
    assert _resolve_effective_script_id(_Boom(), 11) == 11
