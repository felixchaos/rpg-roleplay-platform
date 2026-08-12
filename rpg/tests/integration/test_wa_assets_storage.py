"""test_wa_assets_storage — W1+W2 统一资产/存储 DB 运行时验证

6 项验证：
1. storage 往返：store_bytes / resolve_path / 穿越防护 / delete_file
2. register_asset 幂等：唯一约束 + url update 不新增行
3. list_user_assets：按 kind 过滤 / 全列 / 按 created_at desc / owner 隔离
4. find_references：character_cards / users / scripts 三表 + 无引用返回空
5. delete_asset 关联检查：force=False 拒绝 / force=True 成功删除
6. backfill 三来源 + 幂等：ai_images / users.avatar_url / scripts.source_path
"""
from __future__ import annotations

import os
import random
import string
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")


def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _make_user(db) -> int:
    uname = f"wa_test_{_rand()}"
    row = db.execute(
        """
        insert into users(username, display_name, password_hash, email)
        values (%s, %s, 'x', %s) returning id
        """,
        (uname, uname, f"{uname}@example.test"),
    ).fetchone()
    return int(row["id"])


def _make_script(db, owner_id: int, source_path: str = "") -> int:
    row = db.execute(
        """
        insert into scripts(owner_id, title, description, source_path)
        values (%s, %s, '', %s) returning id
        """,
        (owner_id, f"script_{_rand()}", source_path),
    ).fetchone()
    return int(row["id"])


def _make_card(db, user_id: int, avatar_path: str = "") -> int:
    """插一个 pc 类型的用户卡（满足 character_cards check 约束）。"""
    name = f"card_{_rand()}"
    # 用一个随机 slug 避免 uq_character_cards_user_slug 冲突
    slug = f"slug_{_rand()}"
    row = db.execute(
        """
        insert into character_cards(user_id, name, card_type, source, avatar_path, slug)
        values (%s, %s, 'pc', 'user', %s, %s) returning id
        """,
        (user_id, name, avatar_path, slug),
    ).fetchone()
    return int(row["id"])


# ══════════════════════════════════════════════════════════════════════
# 1. storage 往返
# ══════════════════════════════════════════════════════════════════════

class TestStorageRoundtrip(unittest.TestCase):
    """store_bytes / resolve_path / 穿越防护 / delete_file"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db.init import init_db
        init_db()

    def test_store_bytes_returns_key_and_url(self):
        """store_bytes 返回 (storage_key, url)，文件实际落盘。"""
        import platform_app.storage as storage
        tmpdir = tempfile.mkdtemp()
        orig_root = storage.PLATFORM_DATA_ROOT
        try:
            storage.PLATFORM_DATA_ROOT = Path(tmpdir)
            key, url = storage.store_bytes(b"hello", kind="ai_images", filename="t.png")
            self.assertEqual(key, "ai_images/t.png")
            self.assertTrue(url.startswith("/"), f"url should be relative: {url!r}")
            self.assertIn("ai_images", url)
            self.assertIn("t.png", url)
        finally:
            storage.PLATFORM_DATA_ROOT = orig_root
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_resolve_path_is_under_root(self):
        """resolve_path 返回 PLATFORM_DATA_ROOT 下的路径。"""
        import platform_app.storage as storage
        tmpdir = tempfile.mkdtemp()
        orig_root = storage.PLATFORM_DATA_ROOT
        try:
            storage.PLATFORM_DATA_ROOT = Path(tmpdir)
            # 先写文件
            storage.store_bytes(b"x", kind="ai_images", filename="check.png")
            p = storage.resolve_path("ai_images/check.png")
            # 路径在 tmpdir 下（macOS /var→/private/var 符号链接，用 resolve() 比较）
            tmpdir_resolved = Path(tmpdir).resolve()
            self.assertTrue(
                str(p).startswith(str(tmpdir_resolved)),
                f"resolved path {p} not under tmpdir {tmpdir_resolved}",
            )
        finally:
            storage.PLATFORM_DATA_ROOT = orig_root
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_resolve_path_traversal_raises(self):
        """路径穿越应当抛 ValueError。"""
        import platform_app.storage as storage
        tmpdir = tempfile.mkdtemp()
        orig_root = storage.PLATFORM_DATA_ROOT
        try:
            storage.PLATFORM_DATA_ROOT = Path(tmpdir)
            with self.assertRaises(ValueError):
                storage.resolve_path("../../etc/passwd")
        finally:
            storage.PLATFORM_DATA_ROOT = orig_root
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_delete_file_removes_file(self):
        """delete_file 删除物理文件，不存在时静默。"""
        import platform_app.storage as storage
        tmpdir = tempfile.mkdtemp()
        orig_root = storage.PLATFORM_DATA_ROOT
        try:
            storage.PLATFORM_DATA_ROOT = Path(tmpdir)
            key, _ = storage.store_bytes(b"del_me", kind="ai_images", filename="del.png")
            p = storage.resolve_path(key)
            self.assertTrue(p.exists())
            storage.delete_file(key)
            self.assertFalse(p.exists())
            # 删不存在的文件不报错
            storage.delete_file(key)
        finally:
            storage.PLATFORM_DATA_ROOT = orig_root
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════════
# 2. register_asset 幂等
# ══════════════════════════════════════════════════════════════════════

class TestRegisterAssetIdempotent(unittest.TestCase):
    """register_asset on conflict do update，同 user_id+storage_key 不新增行，url 可更新。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.user_id = _make_user(db)

    def test_register_twice_only_one_row(self):
        """同 storage_key 登记两次，user_assets 只有 1 行。"""
        from platform_app.assets_registry import register_asset
        from platform_app.db import connect
        sk = f"ai_images/idem_{_rand()}.png"
        id1 = register_asset(
            user_id=self.user_id,
            kind="ai_image",
            storage_key=sk,
            url=f"/api/storage/{sk}",
            source="image_gen",
        )
        id2 = register_asset(
            user_id=self.user_id,
            kind="ai_image",
            storage_key=sk,
            url=f"/api/storage/{sk}",
            source="image_gen",
        )
        # 返回同一 id
        self.assertEqual(id1, id2, "幂等调用应返回相同 id")
        with connect() as db:
            cnt = db.execute(
                "select count(*) as c from user_assets where user_id=%s and storage_key=%s",
                (self.user_id, sk),
            ).fetchone()["c"]
        self.assertEqual(cnt, 1, f"重复 register_asset 产生了多行: {cnt}")

    def test_register_updates_url(self):
        """第二次 url 变了 → update 不新增。"""
        from platform_app.assets_registry import get_asset, register_asset
        from platform_app.db import connect
        sk = f"ai_images/url_up_{_rand()}.png"
        id1 = register_asset(
            user_id=self.user_id,
            kind="ai_image",
            storage_key=sk,
            url="/api/storage/old_url.png",
            source="image_gen",
        )
        register_asset(
            user_id=self.user_id,
            kind="ai_image",
            storage_key=sk,
            url="/api/storage/new_url.png",
            source="image_gen",
        )
        with connect() as db:
            cnt = db.execute(
                "select count(*) as c from user_assets where user_id=%s and storage_key=%s",
                (self.user_id, sk),
            ).fetchone()["c"]
        self.assertEqual(cnt, 1, "url 更新后仍应只有 1 行")
        asset = get_asset(self.user_id, id1)
        self.assertIsNotNone(asset)
        self.assertEqual(asset["url"], "/api/storage/new_url.png")


# ══════════════════════════════════════════════════════════════════════
# 3. list_user_assets
# ══════════════════════════════════════════════════════════════════════

class TestListUserAssets(unittest.TestCase):
    """列表过滤 / 排序 / owner 隔离。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        cls.connect = connect
        with connect() as db:
            cls.user1 = _make_user(db)
            cls.user2 = _make_user(db)

        import time

        from platform_app.assets_registry import register_asset
        # user1: 2 ai_image + 1 avatar
        register_asset(
            user_id=cls.user1, kind="ai_image",
            storage_key=f"ai_images/list1_{_rand()}.png",
            url="/api/storage/list1.png", source="image_gen",
        )
        time.sleep(0.01)
        register_asset(
            user_id=cls.user1, kind="ai_image",
            storage_key=f"ai_images/list2_{_rand()}.png",
            url="/api/storage/list2.png", source="image_gen",
        )
        time.sleep(0.01)
        register_asset(
            user_id=cls.user1, kind="avatar",
            storage_key=f"avatars/av_{_rand()}.png",
            url="/api/storage/av.png", source="avatar_upload",
        )

    def test_list_by_kind_ai_image(self):
        """kind='ai_image' 过滤 → 2 条。"""
        from platform_app.assets_registry import list_user_assets
        rows = list_user_assets(self.user1, kind="ai_image")
        self.assertEqual(len(rows), 2, f"期望 2 条 ai_image，实际 {len(rows)}")

    def test_list_all_kinds(self):
        """不传 kind → 3 条。"""
        from platform_app.assets_registry import list_user_assets
        rows = list_user_assets(self.user1)
        self.assertEqual(len(rows), 3, f"期望 3 条，实际 {len(rows)}")

    def test_list_sorted_created_at_desc(self):
        """结果按 created_at 倒序。"""
        from platform_app.assets_registry import list_user_assets
        rows = list_user_assets(self.user1)
        times = [r["created_at"] for r in rows]
        self.assertEqual(times, sorted(times, reverse=True),
                         f"结果未按 created_at desc 排序: {times}")

    def test_list_owner_isolation(self):
        """user2 查 user1 的资产 → 0 条。"""
        from platform_app.assets_registry import list_user_assets
        rows = list_user_assets(self.user2)
        self.assertEqual(len(rows), 0, f"user2 不应看到 user1 的资产，实际 {len(rows)}")


# ══════════════════════════════════════════════════════════════════════
# 4. find_references 三表
# ══════════════════════════════════════════════════════════════════════

class TestFindReferences(unittest.TestCase):
    """character_cards / users / scripts 三表关联反查 + 无引用返回空。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cls.user_id = _make_user(db)

    def test_find_card_reference(self):
        """character_cards.avatar_path 引用 → find_references 返回含该 card 的引用。"""
        import platform_app.storage as storage
        from platform_app.db import connect
        url = "/api/storage/ai_images/ref_card_test.png"
        with connect() as db:
            card_id = _make_card(db, self.user_id, avatar_path=url)
        refs = storage.find_references(url)
        self.assertTrue(len(refs) >= 1, f"期望至少 1 个 card 引用，实际 {refs}")
        kinds = [r["kind"] for r in refs]
        self.assertIn("card_avatar", kinds)

    def test_find_user_reference(self):
        """users.avatar_url 引用 → find_references 返回含该 user 的引用。"""
        import platform_app.storage as storage
        from platform_app.db import connect
        url = f"/api/storage/avatars/ref_user_{_rand()}.png"
        with connect() as db:
            uid = _make_user(db)
            db.execute("update users set avatar_url=%s where id=%s", (url, uid))
        refs = storage.find_references(url)
        self.assertTrue(len(refs) >= 1, f"期望至少 1 个 user 引用，实际 {refs}")
        kinds = [r["kind"] for r in refs]
        self.assertIn("avatar", kinds)

    def test_find_script_reference(self):
        """scripts.cover_image_url 引用 → find_references 返回含该 script 的引用。"""
        import platform_app.storage as storage
        from platform_app.db import connect
        url = f"/api/storage/ai_images/ref_script_{_rand()}.png"
        with connect() as db:
            uid = _make_user(db)
            sid = _make_script(db, uid)
            # scripts 表有 cover_image_url 列，直接 update
            try:
                db.execute("update scripts set cover_image_url=%s where id=%s", (url, sid))
            except Exception as e:
                # 若列不存在则跳过（兼容旧 schema）
                self.skipTest(f"scripts.cover_image_url 不存在: {e}")
        refs = storage.find_references(url)
        self.assertTrue(len(refs) >= 1, f"期望至少 1 个 script 引用，实际 {refs}")
        kinds = [r["kind"] for r in refs]
        self.assertIn("cover", kinds)

    def test_no_reference_returns_empty(self):
        """无任何引用 → find_references 返回 []。"""
        import platform_app.storage as storage
        url = "/api/storage/ai_images/nonexistent_url_xyz.png"
        refs = storage.find_references(url)
        self.assertEqual(refs, [], f"期望空列表，实际 {refs}")


# ══════════════════════════════════════════════════════════════════════
# 5. delete_asset 关联检查
# ══════════════════════════════════════════════════════════════════════

class TestDeleteAssetReferenceCheck(unittest.TestCase):
    """force=False 有引用时拒绝；force=True 直接删除。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cls.user_id = _make_user(db)

    def _setup_asset_with_card_ref(self):
        """插入一条资产，并让一个 character_card.avatar_path 引用其 url。"""
        from platform_app.assets_registry import register_asset
        from platform_app.db import connect
        url = f"/api/storage/ai_images/del_test_{_rand()}.png"
        sk = f"ai_images/del_test_{_rand()}.png"
        asset_id = register_asset(
            user_id=self.user_id,
            kind="ai_image",
            storage_key=sk,
            url=url,
            source="image_gen",
        )
        with connect() as db:
            _make_card(db, self.user_id, avatar_path=url)
        return asset_id, sk, url

    def test_delete_with_references_force_false(self):
        """有引用且 force=False → ok=False, error='has_references'。"""
        from platform_app.assets_registry import delete_asset
        asset_id, sk, url = self._setup_asset_with_card_ref()
        result = delete_asset(self.user_id, asset_id, force=False)
        self.assertFalse(result["ok"], f"期望 ok=False，实际 {result}")
        self.assertEqual(result.get("error"), "has_references")
        self.assertFalse(result.get("deleted", True))
        self.assertTrue(len(result.get("references", [])) >= 1)

    def test_delete_force_true_succeeds(self):
        """force=True → 删 user_assets 行，deleted=True。"""
        from platform_app.assets_registry import delete_asset, get_asset
        asset_id, sk, url = self._setup_asset_with_card_ref()
        result = delete_asset(self.user_id, asset_id, force=True)
        self.assertTrue(result["ok"], f"force=True 应成功删除，实际 {result}")
        self.assertTrue(result.get("deleted"))
        # user_assets 行应已删
        self.assertIsNone(get_asset(self.user_id, asset_id))


# ══════════════════════════════════════════════════════════════════════
# 6. backfill 三来源 + 幂等
# ══════════════════════════════════════════════════════════════════════

class TestBackfillThreeSources(unittest.TestCase):
    """验证 v72 backfill SQL 的三来源 + 幂等（可重复运行不重复）。"""

    @classmethod
    def setUpClass(cls):
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            cls.user_id = _make_user(db)

    def _run_backfill_sqls(self):
        """从 migrations 提取 v72 的三段 backfill SQL 并重放。"""
        from platform_app.db import connect
        from platform_app.db.migrations import MIGRATIONS
        # 找 v72
        v72_sqls = None
        for (ver, name, sqls) in MIGRATIONS:
            if ver == 72:
                v72_sqls = sqls
                break
        self.assertIsNotNone(v72_sqls, "找不到 v72 migration")

        # backfill SQL 是第 4-6 条（索引 3-5，前三条建表/约束/索引）
        # 更稳妥：只跑含 "insert into user_assets" 的 SQL 片段
        backfill_sqls = [s for s in v72_sqls if "insert into user_assets" in s]
        self.assertEqual(len(backfill_sqls), 3, f"期望 3 条 backfill SQL，实际 {len(backfill_sqls)}")

        with connect() as db:
            for sql in backfill_sqls:
                db.execute(sql)

    def test_backfill_ai_images(self):
        """ai_images(status=done) 回填 → user_assets 出现 kind='ai_image'。"""
        from platform_app.db import connect
        filename = f"bf_{_rand()}.png"
        url = f"/api/images/file/{filename}"
        with connect() as db:
            db.execute(
                """
                insert into ai_images(user_id, kind, prompt, model, status, url)
                values (%s, 'card_avatar', 'test', 'test_model', 'done', %s)
                """,
                (self.user_id, url),
            )
        self._run_backfill_sqls()
        with connect() as db:
            row = db.execute(
                """
                select id, kind, storage_key from user_assets
                where user_id=%s and url=%s
                """,
                (self.user_id, url),
            ).fetchone()
        self.assertIsNotNone(row, f"ai_images backfill 未产生 user_assets 行: {url}")
        self.assertEqual(row["kind"], "ai_image")
        self.assertTrue(row["storage_key"].startswith("ai_images/"),
                        f"storage_key 前缀应是 ai_images/: {row['storage_key']}")
        self.assertEqual(row["storage_key"], f"ai_images/{filename}")

    def test_backfill_user_avatar(self):
        """users.avatar_url 回填 → user_assets 出现 kind='avatar'。"""
        from platform_app.db import connect
        filename = f"av_{_rand()}.png"
        avatar_url = f"/api/profile/avatar/file/{filename}"
        with connect() as db:
            uid = _make_user(db)
            db.execute("update users set avatar_url=%s where id=%s", (avatar_url, uid))
        self._run_backfill_sqls()
        with connect() as db:
            row = db.execute(
                """
                select id, kind, storage_key from user_assets
                where user_id=%s and url=%s
                """,
                (uid, avatar_url),
            ).fetchone()
        self.assertIsNotNone(row, f"users.avatar_url backfill 未产生 user_assets 行: {avatar_url}")
        self.assertEqual(row["kind"], "avatar")
        self.assertTrue(row["storage_key"].startswith("avatars/"),
                        f"storage_key 前缀应是 avatars/: {row['storage_key']}")
        self.assertEqual(row["storage_key"], f"avatars/{filename}")

    def test_backfill_script_txt(self):
        """scripts.source_path 回填 → user_assets 出现 kind='script_txt'。"""
        from platform_app.db import connect
        source_path = f"platform_data/scripts/user_{self.user_id}/import_{_rand()}.txt"
        with connect() as db:
            sid = _make_script(db, self.user_id, source_path=source_path)
        self._run_backfill_sqls()
        with connect() as db:
            row = db.execute(
                """
                select id, kind, storage_key from user_assets
                where user_id=%s and ref_id=%s and kind='script_txt'
                """,
                (self.user_id, sid),
            ).fetchone()
        self.assertIsNotNone(row, "scripts.source_path backfill 未产生 user_assets 行")
        self.assertEqual(row["kind"], "script_txt")
        expected_sk = source_path[len("platform_data/"):]
        self.assertEqual(row["storage_key"], expected_sk,
                         f"storage_key 不符预期: {row['storage_key']} vs {expected_sk}")

    def test_backfill_idempotent(self):
        """多次重跑 backfill SQL → user_assets 行数不增加（on conflict do nothing）。"""
        from platform_app.db import connect
        with connect() as db:
            cnt_before = db.execute(
                "select count(*) as c from user_assets where user_id=%s",
                (self.user_id,),
            ).fetchone()["c"]

        # 再跑一次 backfill
        self._run_backfill_sqls()

        with connect() as db:
            cnt_after = db.execute(
                "select count(*) as c from user_assets where user_id=%s",
                (self.user_id,),
            ).fetchone()["c"]

        self.assertEqual(
            cnt_before,
            cnt_after,
            f"backfill 不幂等：第二次跑后行数从 {cnt_before} → {cnt_after}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
