# Stellatrix 桌面服务端(渠道 B)

本地一键部署的桌面应用。一个 app,两种模式:

- **在线模式**:窗口直接连云端 `play.stellatrix.icu`,即开即用,数据在你的云端账号。
- **本地模式**:在本机启动**捆绑的 PostgreSQL + Python 后端**,数据完全离线,NSFW 自主。

分发渠道 = 开源仓 **GitHub Releases**(notarized DMG / 签名 NSIS),`electron-updater` 自动更新。
**不上 Mac App Store**:沙盒内核级封死 PostgreSQL(System V IPC / 信号量),且 NSFW 触 App Store 内容红线 —— 详见根目录设计讨论。

---

## 架构

```
Electron 主进程 (src/main.js)
  ├─ 控制台窗口  src/control-panel/   起停服务 / 日志 / 配置 / 检查更新(纯 HTML,走 preload 白名单)
  ├─ 应用窗口    在线→云端URL / 本地→localhost:后端端口(加载现有 React 前端)
  └─ 服务监督器  src/supervisor.js   本地模式的全生命周期:
        同步可写后端副本 → initdb(首启)→ pg_ctl start → createdb
        → migrate full → uvicorn(serve API+前端)→ 健康检查 → 优雅停机
```

**只读捆绑资源 → 用户可写数据**(`src/paths.js`):
- 捆绑(随包,签名只读):`runtime/python`(便携 Python+依赖)、`pg`(便携 PostgreSQL+pgvector)、`app-template`(后端源码+前端 dist)。
- 可写(`userData`,跨更新保留):`app/`(后端可写副本,cwd 在此 → `platform_data` 等相对写入落这里)、`pgdata/`、`logs/`、`config.json`。

关键环境(监督器注入后端):`DATABASE_URL`(trust 直连本地 PG)、`RPG_DEPLOYMENT_MODE=desktop`(跳登录/放行 loopback)、`RPG_MASTER_KEY`(首启生成存 config,避免后端往只读区写 master.key)、`RPG_SKIP_AUTO_MIGRATE=1`(迁移由监督器跑)。

---

## 本地开发

```bash
cd desktop
npm install
# 先组装捆绑资源(下载便携 Python/PG、装依赖、编 pgvector、复制后端+前端)
npm run bundle:backend        # Windows: npm run bundle:backend:win
npm start                     # 启动 Electron 控制台
```

打未签名的本地包(冒烟):`npm run dist:dir`(产物在 `release/`,跳过签名/公证)。

---

## 发版(全自动 CI)

打 tag 即触发 `.github/workflows/desktop-release.yml`:

```bash
# 用根目录脚本统一 bump 版本(VERSION 单一真源)
bash scripts/bump_version.sh 0.6.0
git commit -am "chore(release): v0.6.0"
git tag -a v0.6.0 -m v0.6.0
git push origin v0.6.0        # → CI 构建三机(mac arm64 / mac x64 / win x64)→ 发布 Release + 更新 feed
```

CI 之后**唯一不可自动化**的只有:Apple 要求的人工 App Review —— 但渠道 B 不走 App Store,所以**整条零人工**。

### 需要的 GitHub Secrets

| Secret | 用途 |
|---|---|
| `MAC_CERT_P12_BASE64` / `MAC_CERT_PASSWORD` | Developer ID Application 证书(`base64 -i cert.p12`) |
| `APPLE_API_KEY_P8_BASE64` / `APPLE_API_KEY_ID` / `APPLE_API_ISSUER` | 公证用 App Store Connect API Key |
| `WIN_CERT_P12_BASE64` / `WIN_CERT_PASSWORD` | Windows Authenticode 证书(可缺省) |
| `GITHUB_TOKEN` | 自动提供,发布到本仓 Releases |

### 一次性人工步骤(你的 Apple 账号,我代劳不了)

1. **Apple Developer**($99/年):生成 **Developer ID Application** 证书(Account Holder 角色,每账号限 5 个)。
2. 生成 **App Store Connect API Key**(.p8,**只能下载一次**,立刻转 base64 存 Secret;权限 ≥ App Manager)。
3. 把上述证书/Key 转 base64 填入 GitHub Secrets。
4. 在 `package.json` 的 `build.publish.owner` 填开源仓所有者(现为占位 `REPLACE_GH_OWNER`)。
5. (可选)买 Windows 代码签名证书填 `WIN_CERT_*`;不买则首装 SmartScreen 告警。
6. 真机(干净 mac / win)验证首次安装 + 启动 + 卸载。

---

## 已知约束 / 待办

- **Windows pgvector 默认跳过**:需 MSVC/nmake 构建较脆;后端 pgvector 是软依赖,缺失自动降级 jsonb(语义检索弱化但可用)。打通后把 `bundle-backend.ps1` 的 `$BuildPgvector` 设 `$true`。
- **整包体积** ~180–280MB(便携 PG ~120MB + Python 依赖 ~100MB 是底盘,壳只占小头)。
- **嵌套二进制签名**:psycopg-binary 自带整套 `libpq/libssl/libcrypto` dylib、cryptography/pydantic-core 等 `.so` —— electron-builder 递归签 .app 内所有 Mach-O 并套用 `entitlements.mac.inherit.plist`(含 `disable-library-validation`,漏了是静默崩溃)。
- **版本号**:与根 `VERSION` 单一真源对齐,`package.json` 的 `version` 由 `scripts/bump_version.sh` 同步(`__APP_VERSION__` / `/api/health` 也读它)。
- **`resources-staged/` 不入库**:体积巨大,由 CI 现场 `bundle-backend` 生成。
- 下载源版本号(Python/PG/pgvector)写在 `bundle-backend.*` 顶部 `[ADJUST]` 区,会随时间漂移,发版前核对。

---

## ⚠️ 这套脚手架尚未端到端验证

代码完整且按核实过的 Apple/electron-builder 规则编写,但**还没在真机端到端跑过**(需 `npm install`、便携 PG 二进制、你的签名证书、CI 跑一轮)。首次真跑预计要调:便携 PG/Python 下载源的确切资产名、pgvector 编译、嵌套二进制签名覆盖、首启 DB bootstrap 时序。
