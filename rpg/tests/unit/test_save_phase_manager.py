from __future__ import annotations

import threading


def test_fire_and_forget_compact_passes_save_owner(monkeypatch):
    import agents.phase_digest_agent as phase_digest_agent
    import save_phase_manager

    captured: dict[str, int | None] = {}
    called = threading.Event()

    def fake_load_save_user_id(save_id: int) -> int:
        captured["lookup_save_id"] = save_id
        return 41

    def fake_compact_phase(save_id: int, phase_index: int, *, user_id: int | None = None):
        captured.update({
            "save_id": save_id,
            "phase_index": phase_index,
            "user_id": user_id,
        })
        called.set()
        return {}

    monkeypatch.setattr(save_phase_manager, "_load_save_user_id", fake_load_save_user_id)
    monkeypatch.setattr(phase_digest_agent, "compact_phase", fake_compact_phase)

    save_phase_manager._fire_and_forget_compact(123, 4)

    assert called.wait(2)
    assert captured == {
        "lookup_save_id": 123,
        "save_id": 123,
        "phase_index": 4,
        "user_id": 41,
    }
