from __future__ import annotations

import copy
import unittest

from tests.helpers import cleanup_test_users, make_client, random_suffix, register_user


class ModelRoutesFrontendContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cleanup_test_users()
        cls.client = make_client()

    @classmethod
    def tearDownClass(cls):
        cleanup_test_users()

    def test_model_settings_routes_accept_frontend_payloads(self):
        from model_registry import MODEL_CONFIG_FILE, load_model_catalog, save_model_catalog
        from platform_app.db import connect

        original_bytes = MODEL_CONFIG_FILE.read_bytes() if MODEL_CONFIG_FILE.exists() else None
        original_catalog = copy.deepcopy(load_model_catalog())
        try:
            user = register_user(self.client)
            with connect() as db:
                db.execute("update users set role = 'admin' where username = %s", (user["username"],))

            api_id = "openai"
            api = next(a for a in original_catalog["apis"] if a["id"] == api_id)
            resp = self.client.post("/api/models/api", json={"api_id": api_id, "enabled": api.get("enabled", False)})
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(resp.json().get("ok"))

            model_id = f"integtest-model-{random_suffix()}"
            resp = self.client.post(
                "/api/models/model",
                json={
                    "api_id": api_id,
                    "real_name": model_id,
                    "display_name": "Integ Test Model",
                    "enabled": False,
                },
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            models = next(a for a in resp.json()["models"]["apis"] if a["id"] == api_id)["models"]
            self.assertTrue(any(m["id"] == model_id for m in models))

            resp = self.client.post("/api/models/model/delete", json={"api_id": api_id, "real_name": model_id})
            self.assertEqual(resp.status_code, 200, resp.text)
            models = next(a for a in resp.json()["models"]["apis"] if a["id"] == api_id)["models"]
            self.assertFalse(any(m["id"] == model_id or m.get("real_name") == model_id for m in models))
        finally:
            save_model_catalog(original_catalog)
            if original_bytes is None:
                MODEL_CONFIG_FILE.unlink(missing_ok=True)
            else:
                MODEL_CONFIG_FILE.write_bytes(original_bytes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
