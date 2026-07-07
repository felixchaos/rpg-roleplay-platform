"""编辑器 P0:章节保存乐观锁(AI 写库与未保存改动互相静默覆盖)。
update_chapter 带 base_updated_at 与服务端不一致→ChapterConflict→端点 409+服务端版本,
前端转三方合并。不带=覆盖语义不变(AI 工具/老客户端)。源码结构断言。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SI = (ROOT / "platform_app" / "script_import.py").read_text(encoding="utf-8")
API = (ROOT / "platform_app" / "api" / "scripts.py").read_text(encoding="utf-8")


def test_update_chapter_optimistic_lock():
    i = SI.find("def update_chapter(")
    body = SI[i:SI.find("\ndef ", i + 1)]
    assert "base_updated_at" in body
    assert "ChapterConflict" in body, "不一致必须抛冲突而非落库"
    assert 'replace("T", " ")[:19]' in body, "秒级+分隔符归一(DB 空格 vs ISO T),防假冲突"
    assert "if base_updated_at:" in body


def test_endpoint_returns_409_with_server_version():
    i = API.find("async def api_chapter_update(")
    body = API[i:i + 1800]
    assert "ChapterConflict" in body
    assert "409" in body
    assert "server_chapter" in body, "409 必须携带服务端当前版本供前端合并"
    assert 'body.get("base_updated_at")' in body
