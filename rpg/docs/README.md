# API 文档

## 在线交互

启动 dev server 后访问:

| URL | 用途 |
|---|---|
| http://127.0.0.1:7860/docs | Swagger UI (交互式测试) |
| http://127.0.0.1:7860/redoc | ReDoc (优美阅读体验) |
| http://127.0.0.1:7860/openapi.json | OpenAPI 3 JSON schema |

## 静态导出

```bash
cd rpg/
../rpg_env/bin/python -m scripts.gen_openapi
# 生成 rpg/docs/openapi.json
```

每次新增或修改 endpoint 后重新运行以更新 `docs/openapi.json`。

## 用 Redocly 生成 standalone HTML

```bash
# 全局安装 (一次)
npm install -g @redocly/cli

# 生成
cd rpg/
npx @redocly/cli build-docs docs/openapi.json -o docs/api.html
open docs/api.html
```

## 用 swagger-codegen 生成前端 TypeScript

```bash
# 拉 openapi 文档
curl http://127.0.0.1:7860/openapi.json > docs/openapi.json

# 生成 TypeScript axios client (示例)
npx @openapitools/openapi-generator-cli generate \
  -i docs/openapi.json \
  -g typescript-axios \
  -o ../frontend/src/api-client-generated
```

## API 主题分布

| 主题 | Endpoint 数 | 路径前缀 |
|---|---|---|
| 主游戏 (routes/) | 59 | `/api/*` (chat / state / new / save / opening / 等) |
| 平台 (platform_app/api/) | 82 | `/api/auth/*` / `/api/scripts/*` / `/api/saves/*` / `/api/me/*` / `/api/library/*` 等 |
| Frontend pages | 27 | `/api/profile/*` / `/api/admin/*` 等 |

实际导出总数见 `openapi.json` 中 `paths` 键的条目数(当前 159 个路径)。

## Schema 双向覆盖

- **请求 schema**: routes/ 37 个 POST endpoint 全用 Pydantic `BaseModel`
- **响应 schema**: routes/ 37 个 endpoint 全用 `response_model` (StateResponse / OkResponse / GenericOkResponse)
- **错误 schema**: 统一 `responses={400, 401: {model: ErrorResponse}}`

## 字段约定

- 所有 request model `extra="ignore"` (前端额外字段静默丢弃)
- 4 个 transparent passthrough model 用 `extra="allow"` (ModelsUpsertApiRequest 等)
- error response 统一 `{ok: false, error: "..."}` 格式
