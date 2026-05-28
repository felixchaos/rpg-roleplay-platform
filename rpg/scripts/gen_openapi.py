"""scripts.gen_openapi — 导出 OpenAPI schema 到 docs/openapi.json。

用法:
    cd rpg/
    ../rpg_env/bin/python -m scripts.gen_openapi

输出: docs/openapi.json (FastAPI 完整 schema)
然后可用 redocly/swagger-ui 等工具生成 standalone HTML。
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# 让 import 找到 app
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import app


def main():
    schema = app.openapi()
    # Rewrite internal /api/ paths to public /api/v1/ for documentation.
    # At runtime the middleware strips /v1/ back to /api/; docs should show the public surface.
    old_paths = schema.get("paths", {})
    new_paths: dict = {}
    for path, item in old_paths.items():
        if path.startswith("/api/") and not path.startswith("/api/v1/"):
            new_paths["/api/v1/" + path[len("/api/"):]] = item
        else:
            new_paths[path] = item
    schema["paths"] = new_paths
    out = Path(__file__).parent.parent / "docs" / "openapi.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 写入 {out} ({len(schema.get('paths', {}))} endpoints)")


if __name__ == "__main__":
    main()
