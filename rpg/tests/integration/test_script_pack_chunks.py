"""test_script_pack_chunks.py — chunks export/import round-trip 回归测试。

覆盖:
- export include_chunks=True → chunks.jsonl 在 zip 里, manifest 含 chunks_included
- import 含 chunks 的 zip → document_chunks 正确还原 (round-trip)
- backward compat: 不含 chunks 的旧 zip 依然能正常 import (无 error)
- fallback: manifest 声明 chunks_included 但 chunks.jsonl 缺失 → import 不报错
"""
from __future__ import annotations

import io
import json
import unittest
import zipfile

from tests.helpers import cleanup_test_users, make_client, register_user


def _get_uid(username: str) -> int:
    from platform_app.db import connect
    with connect() as db:
        row = db.execute("SELECT id FROM users WHERE username = %s", (username,)).fetchone()
    return int(row["id"])


def _make_script_with_book_and_chunks(uid: int, title: str) -> tuple[int, int, int]:
    """建 scripts + books + documents + script_chapters + document_chunks。
    返回 (script_id, chapter_id, chunk_count)。
    """
    from platform_app.db import connect
    with connect() as db:
        sid = int(db.execute(
            "INSERT INTO scripts(owner_id, title) VALUES (%s, %s) RETURNING id",
            (uid, title),
        ).fetchone()["id"])

        ch = db.execute(
            """
            INSERT INTO script_chapters(script_id, chapter_index, title, content, word_count)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (sid, 1, "第一章 测试章节", "这是测试正文内容，分成两个 chunk。", 20),
        ).fetchone()
        chapter_id = int(ch["id"])

        # slug must be unique per owner; use sid to avoid collisions
        slug = f"test-pack-{sid}"
        book = db.execute(
            "INSERT INTO books(owner_id, script_id, title, slug) VALUES (%s, %s, %s, %s) RETURNING id",
            (uid, sid, title, slug),
        ).fetchone()
        book_id = int(book["id"])

        doc = db.execute(
            """
            INSERT INTO documents(book_id, script_id, chapter_id, source_kind, source_ref,
                                  title, content, metadata)
            VALUES (%s, %s, %s, 'chapter', %s, %s, %s, '{}') RETURNING id
            """,
            (book_id, sid, chapter_id, "1", "第一章 测试章节",
             "这是测试正文内容，分成两个 chunk。"),
        ).fetchone()
        doc_id = int(doc["id"])

        for i, text in enumerate(["这是测试正文内容，", "分成两个 chunk。"]):
            db.execute(
                """
                INSERT INTO document_chunks
                  (document_id, book_id, script_id, chapter_id, chapter_index,
                   chunk_index, content, token_count, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '{}')
                """,
                (doc_id, book_id, sid, chapter_id, 1, i, text, len(text) // 2),
            )

        chunk_count = int(db.execute(
            "SELECT COUNT(*) as n FROM document_chunks WHERE script_id = %s", (sid,)
        ).fetchone()["n"])

    return sid, chapter_id, chunk_count


class ScriptPackChunksRoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_export_include_chunks_true_puts_chunks_in_zip(self):
        u = register_user(self.client)
        uid = _get_uid(u["username"])
        sid, _, chunk_count = _make_script_with_book_and_chunks(uid, "pack_chunks_export_test")
        self.assertGreater(chunk_count, 0)

        from platform_app.knowledge.script_pack import export_script_pack
        zip_bytes, filename = export_script_pack(sid, uid, include_chunks=True)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            self.assertIn("chunks.jsonl", names, "include_chunks=True 时 zip 必须含 chunks.jsonl")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            self.assertTrue(manifest.get("chunks_included"), "manifest.chunks_included 应为 True")
            self.assertIsNotNone(manifest.get("chunks_version"), "manifest.chunks_version 应有值")

            chunks = [json.loads(line) for line in zf.read("chunks.jsonl").decode("utf-8").split("\n") if line.strip()]
            self.assertEqual(len(chunks), chunk_count, f"导出的 chunk 数应={chunk_count}")
            self.assertIn("content", chunks[0])
            self.assertIn("chunk_index", chunks[0])
            self.assertIn("chapter_index", chunks[0])
            self.assertIn("source_ref", chunks[0])

    def test_export_default_no_chunks_in_zip(self):
        u = register_user(self.client)
        uid = _get_uid(u["username"])
        sid, _, _ = _make_script_with_book_and_chunks(uid, "pack_no_chunks_export_test")

        from platform_app.knowledge.script_pack import export_script_pack
        zip_bytes, _ = export_script_pack(sid, uid)  # include_chunks=False (默认)

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = zf.namelist()
            self.assertNotIn("chunks.jsonl", names, "默认导出不应含 chunks.jsonl")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            self.assertFalse(manifest.get("chunks_included"), "默认 manifest.chunks_included=False")

    def test_import_with_chunks_restores_document_chunks(self):
        u = register_user(self.client)
        uid = _get_uid(u["username"])
        sid, _, chunk_count = _make_script_with_book_and_chunks(uid, "pack_roundtrip_src")

        from platform_app.knowledge.script_pack import export_script_pack, import_script_pack
        zip_bytes, _ = export_script_pack(sid, uid, include_chunks=True)

        result = import_script_pack(zip_bytes, uid)
        self.assertTrue(result["ok"])
        new_sid = result["script_id"]
        self.assertNotEqual(new_sid, sid)

        from platform_app.db import connect
        with connect() as db:
            restored = int(db.execute(
                "SELECT COUNT(*) as n FROM document_chunks WHERE script_id = %s", (new_sid,)
            ).fetchone()["n"])
        self.assertEqual(restored, chunk_count,
            f"round-trip 后 chunks 数应={chunk_count}, 实际={restored}")

    def test_import_chunk_content_matches(self):
        u = register_user(self.client)
        uid = _get_uid(u["username"])
        sid, _, _ = _make_script_with_book_and_chunks(uid, "pack_content_match_src")

        from platform_app.db import connect
        from platform_app.knowledge.script_pack import export_script_pack, import_script_pack

        with connect() as db:
            orig_chunks = db.execute(
                "SELECT chunk_index, content FROM document_chunks WHERE script_id = %s ORDER BY chunk_index",
                (sid,)
            ).fetchall()
        orig_contents = {r["chunk_index"]: r["content"] for r in orig_chunks}

        zip_bytes, _ = export_script_pack(sid, uid, include_chunks=True)
        result = import_script_pack(zip_bytes, uid)
        new_sid = result["script_id"]

        with connect() as db:
            new_chunks = db.execute(
                "SELECT chunk_index, content FROM document_chunks WHERE script_id = %s ORDER BY chunk_index",
                (new_sid,)
            ).fetchall()
        for r in new_chunks:
            self.assertEqual(r["content"], orig_contents[r["chunk_index"]],
                f"chunk_index={r['chunk_index']} 内容应与原始一致")

    def test_backward_compat_import_old_zip_no_chunks(self):
        """旧格式 zip (无 chunks.jsonl, manifest 无 chunks_included) 能正常 import。"""
        u = register_user(self.client)
        uid = _get_uid(u["username"])
        sid, _, _ = _make_script_with_book_and_chunks(uid, "pack_backward_compat_src")

        from platform_app.knowledge.script_pack import export_script_pack, import_script_pack
        zip_bytes, _ = export_script_pack(sid, uid, include_chunks=False)

        # 验证旧 zip 确实没有 chunks 字段
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            json.loads(zf.read("manifest.json").decode("utf-8"))

        # 强制去掉 chunks_included 模拟真正的旧格式 zip
        old_buf = io.BytesIO(zip_bytes)
        new_buf = io.BytesIO()
        with zipfile.ZipFile(old_buf, "r") as src_zf, zipfile.ZipFile(new_buf, "w", zipfile.ZIP_DEFLATED) as dst_zf:
            for item in src_zf.infolist():
                data = src_zf.read(item.filename)
                if item.filename == "manifest.json":
                    m = json.loads(data.decode("utf-8"))
                    m.pop("chunks_included", None)
                    m.pop("chunks_version", None)
                    data = json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8")
                dst_zf.writestr(item, data)
        old_format_zip = new_buf.getvalue()

        result = import_script_pack(old_format_zip, uid)
        self.assertTrue(result["ok"], f"旧格式 zip 应能 import 成功: {result}")

    def test_fallback_chunks_missing_in_zip_despite_manifest(self):
        """manifest 声明 chunks_included=True 但 zip 里没有 chunks.jsonl → import 不报错, 只是没有 chunks。"""
        u = register_user(self.client)
        uid = _get_uid(u["username"])
        sid, _, _ = _make_script_with_book_and_chunks(uid, "pack_missing_chunks_src")

        from platform_app.db import connect
        from platform_app.knowledge.script_pack import (
            export_script_pack,
            import_script_pack,
        )

        zip_bytes, _ = export_script_pack(sid, uid, include_chunks=True)

        # 从 zip 里删掉 chunks.jsonl，但保留 manifest 里的声明
        old_buf = io.BytesIO(zip_bytes)
        new_buf = io.BytesIO()
        with zipfile.ZipFile(old_buf, "r") as src_zf, zipfile.ZipFile(new_buf, "w", zipfile.ZIP_DEFLATED) as dst_zf:
            for item in src_zf.infolist():
                if item.filename == "chunks.jsonl":
                    continue  # 故意删掉
                dst_zf.writestr(item, src_zf.read(item.filename))
        corrupt_zip = new_buf.getvalue()

        result = import_script_pack(corrupt_zip, uid)
        self.assertTrue(result["ok"], f"chunks 缺失不应导致 import 失败: {result}")
        new_sid = result["script_id"]

        with connect() as db:
            n = int(db.execute(
                "SELECT COUNT(*) as n FROM document_chunks WHERE script_id = %s", (new_sid,)
            ).fetchone()["n"])
        self.assertEqual(n, 0, "chunks 缺失时还原的 chunks 数应=0 (fallback)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
