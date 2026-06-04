"""Acceptance retry must have its write-context helpers in scope."""

from pathlib import Path

SRC = (Path(__file__).resolve().parents[2] / "chat_pipeline.py").read_text(encoding="utf-8")


def test_acceptance_retry_imports_write_context_helpers():
    retry_section = SRC.split("_retry_ctx = ChatWriteContext", 1)[0].rsplit(
        "if _retry_response:", 1
    )[1]
    assert "from state_write_context import" in retry_section
    assert "ChatWriteContext" in retry_section
    assert "_set_write_ctx" in retry_section
    assert "_clear_write_ctx" in retry_section
    assert "import secrets as _ctx_secrets" in retry_section
