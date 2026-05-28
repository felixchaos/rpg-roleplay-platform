"""
test_api_models_envelope.py — /api/models 响应 shape 是 {ok, models: catalog, selected}。

FE 历史上写过 `data?.apis || data?.models` 这种"扁平 fallback"，把整个 catalog 对象
当作数组传给 setApis → 后续 apis.find 抛 TypeError → 整页 React 树崩溃，
就是用户截图里 Platform.html#settings 的 ExtractorSection 报的『apis.find is not a function』。

本测试既锁定后端 shape（防 backend 退回扁平），又锁定 FE 各引用点都正确解嵌套。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.helpers import make_client, register_user


class ModelsEndpointShape(unittest.TestCase):
    def test_api_models_returns_nested_envelope(self):
        client = make_client()
        u = register_user(client)
        r = client.get("/api/models", cookies=u["cookies"])
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()
        # 形态：{ok, models: catalog, selected}
        self.assertTrue(body.get("ok"))
        catalog = body.get("models") or {}
        self.assertIn("apis", catalog,
            "/api/models 嵌套 models.apis 必须存在；FE ExtractorSection / ModelPopover 都从这里取。")
        self.assertIsInstance(catalog["apis"], list)


class FrontendUnwrapsModelsEnvelope(unittest.TestCase):
    """ExtractorSection / ApisSection / ModelPopover 必须先解 .models.apis 嵌套。"""

    @classmethod
    def setUpClass(cls):
        cls.platform = (Path(__file__).resolve().parents[2]
                        / "frontend" / "src" / "platform-app.jsx").read_text(encoding="utf-8")
        cls.composer = (Path(__file__).resolve().parents[2]
                        / "frontend" / "src" / "game-composer.jsx").read_text(encoding="utf-8")

    def test_extractor_section_unwraps_nested(self):
        # 找 ExtractorSection 函数体内的 setApis 调用上下文
        idx = self.platform.find("function ExtractorSection")
        self.assertGreater(idx, 0)
        # 找紧接着的下一个 function 边界
        end = self.platform.find("\nfunction ", idx + 1)
        if end < 0:
            end = len(self.platform)
        body = self.platform[idx:end]
        # 旧错误模式：`const list = models?.apis || models?.models || []`
        # —— models.apis 不存在时把整个 catalog 对象作为 list，进入 setApis 后炸。
        self.assertNotIn("models?.apis || models?.models", body,
            "ExtractorSection 不应再用扁平 fallback，先取 models?.models?.apis")
        self.assertIn("models?.models?.apis", body,
            "ExtractorSection 必须先尝试 models?.models?.apis 解嵌套")
        self.assertIn("Array.isArray", body,
            "ExtractorSection setApis 之前必须 Array.isArray 校验")

    def test_apis_section_unwraps_nested(self):
        # ApisSection 在 platform-app.jsx 另一处（line ~5060），同样的 fix
        # 用更精确 marker：data?.apis || data?.models 之前是这处。
        self.assertNotIn("data?.apis || data?.models", self.platform,
            "ApisSection 也不应再用扁平 fallback")
        self.assertIn("data?.models?.apis", self.platform,
            "ApisSection 必须解 data.models.apis")

    def test_model_popover_unwraps_response(self):
        idx = self.composer.find("function ModelPopover")
        end = self.composer.find("\nfunction ", idx + 1)
        if end < 0:
            end = len(self.composer)
        body = self.composer[idx:end]
        self.assertIn("realCatalog", body,
            "ModelPopover 应先把响应里的 .models 提出来作为真 catalog")
        self.assertIn("r.models.apis", body.replace(" ", "").replace("\n", " "),
            "ModelPopover useEffect 必须读 r.models.apis")
        self.assertIn("Array.isArray(catalog.apis)", body,
            "ModelPopover 渲染前必须确认 catalog.apis 是 Array")


if __name__ == "__main__":
    unittest.main(verbosity=2)
