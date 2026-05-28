# rpg/ — 后端快速参考

完整文档见 [../README.md](../README.md)。本文件只列后端独有的速查信息。

---

## 启动

```bash
# 在 我蕾穆丽娜不爱你/ 根目录执行
./scripts/dev.sh start

# 或直接
cd rpg
../rpg_env/bin/uvicorn app:app --reload --port 7860
```

API 文档: http://127.0.0.1:7860/docs

---

## 主要 API 端点速览

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/chat` | 玩家行动输入 → GM 响应 |
| `GET` | `/state` | 当前游戏状态快照 |
| `POST` | `/branch/commit` | 提交存档快照 |
| `POST` | `/branch/checkout` | 切换到历史分支 |
| `GET` | `/platform/scripts` | 可用剧本列表 |
| `GET` | `/platform/saves` | 存档列表 |
| `POST` | `/platform/auth/login` | 登录获取 token |

---

## 测试

```bash
cd rpg
../rpg_env/bin/python -m unittest discover -s tests -t .
```

基线: pass ≥ 754 / error = 0

---

## 关键配置

- 入口: `rpg/app.py`
- 环境变量: `rpg/.env` (复制 `.env.example` 并填写)
- 剧本配置: `rpg/modules/_script_overrides/`
- 部署模式: `RPG_DEPLOYMENT_MODE=local|self_hosted|server`
