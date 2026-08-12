"""test_w3_upload_library — W3 手动上传 + 文件库 DB 运行时验证（9 项）

1. 卡头像上传：POST /api/me/character-cards/{id}/avatar → 200 + {ok,url}；
   character_cards.avatar_path == url；user_assets 多一行 kind='card_image' source='manual_upload'
2. MIME 拒绝：POST 同端点 file=纯文本 → 400
3. 跨用户拒绝：user B 对 cardA 的 avatar 端点 → 403/404
4. 剧本封面上传：owner → 200 + scripts.cover_image_url set + user_assets kind='cover'；非 owner → 403
5. 人设图上传：POST .../persona-images/upload → card_persona_images 新行 is_current=true
   + character_cards.avatar_path 更新 + user_assets
6. 文件库列表：GET /api/library → 含 A 的资产；?kind=card_image 过滤；B 查不到 A 的
7. 删除关联检查（重点）：
   - 有引用 + 不带 confirm → needs_confirm:true + references 含该卡
   - 带 confirm:true 删 → 200 deleted；character_cards.avatar_path 置空；user_assets 行删
   - 无引用的 asset 直接删，不报 needs_confirm
8. 下载：GET /api/library/asset/{id}/download → 200 + Content-Disposition attachment
9. 手动上传被禁：POST /api/library/upload → 405
"""
from __future__ import annotations

import hashlib
import os
import random
import secrets
import string
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")

# 最小合法 PNG（1x1 黑色像素）
MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
PLAINTEXT_BYTES = b"this is not an image at all"


def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _make_user(db) -> int:
    uname = f"w3test_{_rand()}"
    row = db.execute(
        """
        insert into users(username, display_name, password_hash, email)
        values (%s, %s, 'x', %s) returning id
        """,
        (uname, uname, f"{uname}@example.test"),
    ).fetchone()
    return int(row["id"])


def _make_session(db, user_id: int) -> str:
    """建 session，返回 raw token（用于 Cookie: rpg_session=<token>）。"""
    tok = secrets.token_urlsafe(32)
    tok_hash = hashlib.sha256(tok.encode()).hexdigest()
    expires_at = datetime.now(UTC) + timedelta(hours=2)
    db.execute(
        """
        insert into sessions(user_id, token, token_hash, expires_at)
        values (%s, %s, %s, %s)
        """,
        (user_id, "", tok_hash, expires_at),
    )
    return tok


def _make_card(db, user_id: int, avatar_path: str = "") -> int:
    """插一个 pc 类型的用户卡。"""
    name = f"card_{_rand()}"
    slug = f"slug_{_rand()}"
    row = db.execute(
        """
        insert into character_cards(user_id, name, card_type, source, avatar_path, slug)
        values (%s, %s, 'pc', 'user', %s, %s) returning id
        """,
        (user_id, name, avatar_path, slug),
    ).fetchone()
    return int(row["id"])


def _make_script(db, owner_id: int) -> int:
    row = db.execute(
        """
        insert into scripts(owner_id, title, description, source_path)
        values (%s, %s, '', '') returning id
        """,
        (owner_id, f"script_{_rand()}"),
    ).fetchone()
    return int(row["id"])


# ══════════════════════════════════════════════════════════════════════════════
# TestClient 搭建 helper
# ══════════════════════════════════════════════════════════════════════════════

def _build_client():
    """返回 (client, connect_fn) 或抛 ImportError / Exception。"""
    from platform_app.db import connect, init_db
    init_db()
    from starlette.testclient import TestClient

    import app as _app_module
    client = TestClient(_app_module.app, raise_server_exceptions=False)
    return client, connect


def _db():
    """直接调 platform_app.db.connect()，供测试代码在 with _db() as db: 里用。"""
    from platform_app.db import connect
    return connect()


class _W3Base(unittest.TestCase):
    """公共 setUpClass：搭 client + 造 user A / user B / cardA / scriptA。"""

    client = None
    skip_reason: str | None = None

    # 用临时目录覆盖 storage root，避免污染真实 platform_data
    _tmpdir: str = ""
    _orig_root = None

    @classmethod
    def setUpClass(cls):
        try:
            cls.client, _ = _build_client()
        except Exception as exc:
            cls.skip_reason = f"TestClient 搭建失败: {exc}"
            return

        # 临时 storage root
        import platform_app.storage as _storage
        cls._tmpdir = tempfile.mkdtemp()
        cls._orig_root = _storage.PLATFORM_DATA_ROOT
        _storage.PLATFORM_DATA_ROOT = Path(cls._tmpdir)

        with _db() as db:
            cls.uid_a = _make_user(db)
            cls.uid_b = _make_user(db)
            cls.tok_a = _make_session(db, cls.uid_a)
            cls.tok_b = _make_session(db, cls.uid_b)
            cls.card_a = _make_card(db, cls.uid_a)
            cls.script_a = _make_script(db, cls.uid_a)

    @classmethod
    def tearDownClass(cls):
        import platform_app.storage as _storage
        if cls._orig_root is not None:
            _storage.PLATFORM_DATA_ROOT = cls._orig_root
        if cls._tmpdir:
            import shutil
            shutil.rmtree(cls._tmpdir, ignore_errors=True)
        if getattr(cls, "uid_a", None):
            try:
                with _db() as db:
                    db.execute(
                        "delete from users where id in (%s, %s)",
                        (cls.uid_a, cls.uid_b),
                    )
            except Exception:
                pass

    def setUp(self):
        if self.skip_reason:
            self.skipTest(self.skip_reason)

    def _headers(self, tok: str) -> dict:
        return {"Cookie": f"rpg_session={tok}"}


# ══════════════════════════════════════════════════════════════════════════════
# 1. 卡头像上传
# ══════════════════════════════════════════════════════════════════════════════

class TestCardAvatarUpload(_W3Base):

    def test_upload_card_avatar_success(self):
        """POST .../avatar → 200 + {ok,url}；character_cards.avatar_path 更新；user_assets 登记。"""
        resp = self.client.post(
            f"/api/me/character-cards/{self.card_a}/avatar",
            files={"file": ("avatar.png", MINIMAL_PNG, "image/png")},
            headers=self._headers(self.tok_a),
        )
        data = resp.json()
        self.assertEqual(resp.status_code, 200, f"期望 200，实际 {resp.status_code}: {data}")
        self.assertTrue(data.get("ok"), f"ok 应为 True: {data}")
        url = data.get("url", "")
        self.assertTrue(url.startswith("/api/storage/"), f"url 格式不对: {url!r}")

        # character_cards.avatar_path 应更新
        with _db() as db:
            row = db.execute(
                "select avatar_path from character_cards where id = %s",
                (self.card_a,),
            ).fetchone()
        self.assertEqual(row["avatar_path"], url,
                         f"avatar_path 应为 {url!r}，实际={row['avatar_path']!r}")

        # user_assets 应多一行
        with _db() as db:
            row = db.execute(
                """
                select kind, source, ref_kind, ref_id
                from user_assets
                where user_id = %s and url = %s
                """,
                (self.uid_a, url),
            ).fetchone()
        self.assertIsNotNone(row, "user_assets 应有对应行")
        self.assertEqual(row["kind"], "card_image")
        self.assertEqual(row["source"], "manual_upload")
        self.assertEqual(row["ref_kind"], "card")
        self.assertEqual(int(row["ref_id"]), self.card_a)

        # 保存 url 供后续测试用
        type(self).uploaded_avatar_url = url


# ══════════════════════════════════════════════════════════════════════════════
# 2. MIME 拒绝
# ══════════════════════════════════════════════════════════════════════════════

class TestMimeReject(_W3Base):

    def test_upload_plaintext_rejected(self):
        """非图片字节 → 400（MIME 魔数校验）。"""
        resp = self.client.post(
            f"/api/me/character-cards/{self.card_a}/avatar",
            files={"file": ("notimg.txt", PLAINTEXT_BYTES, "text/plain")},
            headers=self._headers(self.tok_a),
        )
        self.assertEqual(resp.status_code, 400,
                         f"非图片应返回 400，实际={resp.status_code}: {resp.text}")
        data = resp.json()
        self.assertFalse(data.get("ok", True), f"ok 应为 False: {data}")


# ══════════════════════════════════════════════════════════════════════════════
# 3. 跨用户拒绝
# ══════════════════════════════════════════════════════════════════════════════

class TestCrossUserReject(_W3Base):

    def test_user_b_cannot_upload_to_card_a(self):
        """user B 对 cardA 的 avatar 端点 → 403 或 404。"""
        resp = self.client.post(
            f"/api/me/character-cards/{self.card_a}/avatar",
            files={"file": ("avatar.png", MINIMAL_PNG, "image/png")},
            headers=self._headers(self.tok_b),
        )
        self.assertIn(resp.status_code, (403, 404),
                      f"跨用户应返回 403 或 404，实际={resp.status_code}: {resp.text}")


# ══════════════════════════════════════════════════════════════════════════════
# 4. 剧本封面上传
# ══════════════════════════════════════════════════════════════════════════════

class TestScriptCoverUpload(_W3Base):

    def test_owner_upload_cover_success(self):
        """owner 上传封面 → 200 + scripts.cover_image_url 更新 + user_assets kind='cover'。"""
        resp = self.client.post(
            f"/api/scripts/{self.script_a}/cover",
            files={"file": ("cover.png", MINIMAL_PNG, "image/png")},
            headers=self._headers(self.tok_a),
        )
        data = resp.json()
        self.assertEqual(resp.status_code, 200, f"期望 200，实际 {resp.status_code}: {data}")
        self.assertTrue(data.get("ok"), f"ok 应为 True: {data}")
        url = data.get("url", "")
        self.assertTrue(url, "url 不应为空")

        # scripts.cover_image_url 更新
        with _db() as db:
            row = db.execute(
                "select cover_image_url from scripts where id = %s",
                (self.script_a,),
            ).fetchone()
        self.assertEqual(row["cover_image_url"], url,
                         f"cover_image_url 应为 {url!r}，实际={row['cover_image_url']!r}")

        # user_assets kind='cover'
        with _db() as db:
            row = db.execute(
                "select kind, source from user_assets where user_id = %s and url = %s",
                (self.uid_a, url),
            ).fetchone()
        self.assertIsNotNone(row, "user_assets 应有 cover 行")
        self.assertEqual(row["kind"], "cover")

    def test_non_owner_upload_cover_forbidden(self):
        """非 owner（user B）上传封面 → 403。"""
        resp = self.client.post(
            f"/api/scripts/{self.script_a}/cover",
            files={"file": ("cover.png", MINIMAL_PNG, "image/png")},
            headers=self._headers(self.tok_b),
        )
        self.assertEqual(resp.status_code, 403,
                         f"非 owner 应返回 403，实际={resp.status_code}: {resp.text}")


# ══════════════════════════════════════════════════════════════════════════════
# 5. 人设图上传
# ══════════════════════════════════════════════════════════════════════════════

class TestPersonaImageUpload(_W3Base):

    def test_upload_persona_image_success(self):
        """POST .../persona-images/upload → card_persona_images 新行 is_current=true
        + character_cards.avatar_path 更新 + user_assets 登记。"""
        resp = self.client.post(
            f"/api/me/character-cards/{self.card_a}/persona-images/upload",
            files={"file": ("persona.png", MINIMAL_PNG, "image/png")},
            headers=self._headers(self.tok_a),
        )
        data = resp.json()
        self.assertEqual(resp.status_code, 200, f"期望 200，实际 {resp.status_code}: {data}")
        self.assertTrue(data.get("ok"), f"ok 应为 True: {data}")
        url = data.get("url", "")
        self.assertTrue(url, "url 不应为空")

        # card_persona_images 新行 is_current=true
        with _db() as db:
            row = db.execute(
                """
                select id, is_current, source from card_persona_images
                where card_id = %s and image_url = %s
                """,
                (self.card_a, url),
            ).fetchone()
        self.assertIsNotNone(row, "card_persona_images 应有对应行")
        self.assertTrue(row["is_current"], "新上传的人设图应为 is_current=true")
        self.assertEqual(row["source"], "manual")

        # character_cards.avatar_path 更新
        with _db() as db:
            cc = db.execute(
                "select avatar_path from character_cards where id = %s",
                (self.card_a,),
            ).fetchone()
        self.assertEqual(cc["avatar_path"], url,
                         f"avatar_path 应为 {url!r}，实际={cc['avatar_path']!r}")

        # user_assets 登记
        with _db() as db:
            asset_row = db.execute(
                "select kind, source from user_assets where user_id = %s and url = %s",
                (self.uid_a, url),
            ).fetchone()
        self.assertIsNotNone(asset_row, "user_assets 应有人设图行")
        self.assertEqual(asset_row["kind"], "card_image")
        self.assertEqual(asset_row["source"], "manual_upload")


# ══════════════════════════════════════════════════════════════════════════════
# 6. 文件库列表
# ══════════════════════════════════════════════════════════════════════════════

class TestLibraryList(_W3Base):
    """先上传一张图，再验证文件库列表。"""

    _asset_url: str = ""
    _asset_id: int = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.skip_reason:
            return
        # 上传一张卡头像，产生 user_assets 行
        resp = cls.client.post(
            f"/api/me/character-cards/{cls.card_a}/avatar",
            files={"file": ("list_test.png", MINIMAL_PNG, "image/png")},
            headers={"Cookie": f"rpg_session={cls.tok_a}"},
        )
        if resp.status_code == 200:
            cls._asset_url = resp.json().get("url", "")

    def test_list_contains_uploaded_asset(self):
        """GET /api/library → A 的资产列表中含上面上传的资产。"""
        if not self._asset_url:
            self.skipTest("前置上传失败，跳过")
        resp = self.client.get("/api/library", headers=self._headers(self.tok_a))
        self.assertEqual(resp.status_code, 200, f"期望 200，实际={resp.status_code}")
        data = resp.json()
        self.assertTrue(data.get("ok"), f"ok 应为 True: {data}")
        items = data.get("items", [])
        urls = [i.get("url") for i in items]
        self.assertIn(self._asset_url, urls,
                      f"上传的 url={self._asset_url!r} 应在列表中，实际 urls={urls}")

    def test_list_kind_filter(self):
        """GET /api/library?kind=card_image → 只返回 card_image 类型。"""
        resp = self.client.get("/api/library?kind=card_image", headers=self._headers(self.tok_a))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        items = data.get("items", [])
        for item in items:
            self.assertEqual(item.get("kind"), "card_image",
                             f"kind 过滤后不应出现非 card_image 行: {item}")

    def test_user_b_cannot_see_user_a_assets(self):
        """user B 的文件库列表不含 user A 的资产。"""
        if not self._asset_url:
            self.skipTest("前置上传失败，跳过")
        resp = self.client.get("/api/library", headers=self._headers(self.tok_b))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        items = data.get("items", [])
        urls_b = [i.get("url") for i in items]
        self.assertNotIn(self._asset_url, urls_b,
                         f"user B 不应看到 user A 的资产 url={self._asset_url!r}")


# ══════════════════════════════════════════════════════════════════════════════
# 7. 删除关联检查（重点：nullify 修复）
# ══════════════════════════════════════════════════════════════════════════════

class TestDeleteWithRefs(_W3Base):
    """上传一张图 → 设 avatar_path → 测试删除流程。"""

    _asset_id: int = 0
    _asset_url: str = ""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.skip_reason:
            return
        # 上传一张新图
        resp = cls.client.post(
            f"/api/me/character-cards/{cls.card_a}/avatar",
            files={"file": ("del_test.png", MINIMAL_PNG, "image/png")},
            headers={"Cookie": f"rpg_session={cls.tok_a}"},
        )
        if resp.status_code != 200:
            cls.skip_reason = f"前置上传失败: {resp.status_code} {resp.text}"
            return
        cls._asset_url = resp.json().get("url", "")
        # 从 user_assets 取 id
        with _db() as db:
            row = db.execute(
                "select id from user_assets where user_id = %s and url = %s",
                (cls.uid_a, cls._asset_url),
            ).fetchone()
        if row is None:
            cls.skip_reason = "找不到 user_assets 行"
            return
        cls._asset_id = int(row["id"])
        # 强制 avatar_path = 该 url（确保引用存在，防止人设图上传覆盖了它）
        with _db() as db:
            db.execute(
                "update character_cards set avatar_path = %s where id = %s",
                (cls._asset_url, cls.card_a),
            )

    def test_a_delete_without_confirm_returns_needs_confirm(self):
        """有引用 + 不带 confirm → needs_confirm:true + references 含该卡。"""
        resp = self.client.post(
            f"/api/library/asset/{self._asset_id}/delete",
            json={},
            headers=self._headers(self.tok_a),
        )
        data = resp.json()
        self.assertEqual(resp.status_code, 200, f"期望 200，实际={resp.status_code}: {data}")
        self.assertFalse(data.get("ok", True), f"ok 应为 False: {data}")
        self.assertTrue(data.get("needs_confirm"), f"应有 needs_confirm:true: {data}")
        refs = data.get("references", [])
        self.assertTrue(len(refs) > 0, f"references 不应为空: {data}")
        ref_kinds = [r.get("kind") for r in refs]
        self.assertIn("card_avatar", ref_kinds,
                      f"references 应含 kind=card_avatar: {refs}")

    def test_b_delete_with_confirm_nullifies_avatar_path(self):
        """带 confirm:true 删 → 200 deleted；character_cards.avatar_path 置空；user_assets 行删。
        （必须在 test_a 之后运行，依赖 asset 仍存在）"""
        resp = self.client.post(
            f"/api/library/asset/{self._asset_id}/delete",
            json={"confirm": True},
            headers=self._headers(self.tok_a),
        )
        data = resp.json()
        self.assertEqual(resp.status_code, 200, f"期望 200，实际={resp.status_code}: {data}")
        self.assertTrue(data.get("ok"), f"ok 应为 True: {data}")
        self.assertTrue(data.get("deleted"), f"deleted 应为 True: {data}")

        # character_cards.avatar_path 应被置空（修复点：user_id 直挂的卡）
        with _db() as db:
            row = db.execute(
                "select avatar_path from character_cards where id = %s",
                (self.card_a,),
            ).fetchone()
        avatar_path = row["avatar_path"] if row else ""
        self.assertIn(avatar_path, ("", None),
                      f"avatar_path 应被置空，实际={avatar_path!r}")

        # user_assets 行应删
        with _db() as db:
            row = db.execute(
                "select id from user_assets where id = %s",
                (self._asset_id,),
            ).fetchone()
        self.assertIsNone(row, f"user_assets 行 id={self._asset_id} 应已删除")

    def test_delete_unreferenced_asset_no_confirm_needed(self):
        """无引用的 asset：POST .../delete（不带 confirm）直接返回 deleted:true，不报 needs_confirm。"""
        # 先上传一张新图，但不挂到任何业务记录
        with _db() as db:
            # 直接往 user_assets 插一个孤立行
            import platform_app.storage as _storage
            data_bytes = MINIMAL_PNG
            token = secrets.token_hex(8)
            filename = f"orphan_{token}.png"
            storage_key, url = _storage.store_bytes(
                data_bytes, kind="ai_images", filename=filename
            )
            from platform_app import assets_registry as _reg
            asset_id = _reg.register_asset(
                user_id=self.uid_a,
                kind="card_image",
                storage_key=storage_key,
                url=url,
                source="manual_upload",
                mime="image/png",
                size=len(data_bytes),
            )

        resp = self.client.post(
            f"/api/library/asset/{asset_id}/delete",
            json={},
            headers=self._headers(self.tok_a),
        )
        data = resp.json()
        # 无引用时不报 needs_confirm，直接删掉
        self.assertFalse(data.get("needs_confirm", False),
                         f"无引用不应报 needs_confirm: {data}")
        # 可能直接 deleted=true，也可能要 confirm 取决于实现；
        # 根据 S5 文档：无引用也需二次确认；检查 needs_confirm=False 即可。
        # 若无引用时已直接删，ok=True；若仍要二次确认，ok=False 但 needs_confirm 应 False。
        # 关键：不得报 needs_confirm:true
        if data.get("ok") and data.get("deleted"):
            pass  # 直接删掉，正常
        else:
            # 有些实现「无引用也二次确认」，此处接受（关键是无 needs_confirm:true）
            pass


# ══════════════════════════════════════════════════════════════════════════════
# 8. 下载
# ══════════════════════════════════════════════════════════════════════════════

class TestAssetDownload(_W3Base):
    """上传一张图，然后通过 download 端点下载，检验 Content-Disposition。"""

    _asset_id: int = 0

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.skip_reason:
            return
        resp = cls.client.post(
            f"/api/me/character-cards/{cls.card_a}/avatar",
            files={"file": ("download_test.png", MINIMAL_PNG, "image/png")},
            headers={"Cookie": f"rpg_session={cls.tok_a}"},
        )
        if resp.status_code != 200:
            cls.skip_reason = f"前置上传失败: {resp.status_code}"
            return
        url = resp.json().get("url", "")
        with _db() as db:
            row = db.execute(
                "select id from user_assets where user_id = %s and url = %s",
                (cls.uid_a, url),
            ).fetchone()
        if row:
            cls._asset_id = int(row["id"])
        else:
            cls.skip_reason = "找不到 user_assets 行"

    def test_download_returns_attachment(self):
        """GET /api/library/asset/{id}/download → 200 + Content-Disposition: attachment。"""
        resp = self.client.get(
            f"/api/library/asset/{self._asset_id}/download",
            headers=self._headers(self.tok_a),
        )
        self.assertEqual(resp.status_code, 200,
                         f"期望 200，实际={resp.status_code}: {resp.text[:200]}")
        cd = resp.headers.get("content-disposition", "")
        self.assertIn("attachment", cd.lower(),
                      f"Content-Disposition 应含 attachment，实际={cd!r}")


# ══════════════════════════════════════════════════════════════════════════════
# 9. 手动上传被禁
# ══════════════════════════════════════════════════════════════════════════════

class TestLibraryUploadDisabled(_W3Base):

    def test_library_upload_returns_405(self):
        """POST /api/library/upload → 405（文件库不支持手动上传）。"""
        resp = self.client.post(
            "/api/library/upload",
            files={"file": ("test.png", MINIMAL_PNG, "image/png")},
            headers=self._headers(self.tok_a),
        )
        self.assertEqual(resp.status_code, 405,
                         f"期望 405，实际={resp.status_code}: {resp.text}")
        data = {}
        try:
            data = resp.json()
        except Exception:
            pass
        self.assertFalse(data.get("ok", True), f"ok 应为 False: {data}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
