#!/usr/bin/env bash
# bundle-backend.sh —— 把「便携 Python+依赖 / 便携 PostgreSQL+pgvector / 后端源码+前端」
# 组装进 desktop/resources-staged/,供 electron-builder 当 extraResources 打包(macOS / Linux)。
#
# 产物布局(= 只读资源根):
#   resources-staged/runtime/python/{bin/python3, lib/...}    python-build-standalone(install_only)
#   resources-staged/pg/{bin,lib,share}/...                   便携 PostgreSQL + pgvector(vector.so + 控制文件)
#   resources-staged/app-template/rpg/...                     后端源码(app.py 在此),已剔除测试/夹具/venv
#   resources-staged/app-template/frontend/dist/...           前端构建产物(app.py 用 parent.parent/frontend 找它)
#
# ⚠️ 版本号/下载源会随时间漂移,带 [ADJUST] 的请按需更新。
set -euo pipefail

# ── [ADJUST] 版本与来源 ──
PY_VER="3.12.13"
PBS_TAG="20260610"                 # astral/python-build-standalone release tag(核实:含 cpython-3.12.13)
PG_VER="17.10.0"                   # zonky embedded PG(低部署目标,跨 macOS 版本兼容 + 含 include 头文件可编 pgvector)
PGVECTOR_VER="v0.8.0"
PBS_BASE="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}"
ZONKY_BASE="https://repo1.maven.org/maven2/io/zonky/test/postgres"

# ── 路径 ──
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESK="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$DESK/.." && pwd)"
STAGE="$DESK/resources-staged"
WORK="$DESK/.bundle-work"

# ── 目标平台三元组 ──
uname_s="$(uname -s)"; uname_m="$(uname -m)"
case "$uname_s/$uname_m" in
  Darwin/arm64)  PBS_TRIPLE="aarch64-apple-darwin";        ZONKY_ARTIFACT="darwin-arm64v8" ;;
  Darwin/x86_64) PBS_TRIPLE="x86_64-apple-darwin";         ZONKY_ARTIFACT="darwin-amd64" ;;
  Linux/aarch64) PBS_TRIPLE="aarch64-unknown-linux-gnu";   ZONKY_ARTIFACT="linux-arm64v8" ;;
  Linux/x86_64)  PBS_TRIPLE="x86_64-unknown-linux-gnu";    ZONKY_ARTIFACT="linux-amd64" ;;
  *) echo "✗ 不支持的平台: $uname_s/$uname_m" >&2; exit 1 ;;
esac
echo "== 目标: $PBS_TRIPLE / PG zonky $ZONKY_ARTIFACT =="

rm -rf "$STAGE" "$WORK"; mkdir -p "$STAGE" "$WORK"

dl() { echo "  ↓ $1"; curl -fL --retry 3 -o "$2" "$1"; }

# ── 1. 便携 Python ──
echo "== 1/5 便携 Python ($PY_VER) =="
PY_TARBALL="cpython-${PY_VER}+${PBS_TAG}-${PBS_TRIPLE}-install_only.tar.gz"
dl "${PBS_BASE}/${PY_TARBALL}" "$WORK/python.tar.gz"
tar -xzf "$WORK/python.tar.gz" -C "$WORK"          # 解出 ./python/
mkdir -p "$STAGE/runtime"; mv "$WORK/python" "$STAGE/runtime/python"
PY="$STAGE/runtime/python/bin/python3"

# ── 2. 安装后端依赖(剔除 dev:mypy/ruff/pytest/pip 体积大头)──
echo "== 2/5 安装依赖 =="
PROD_REQ="$WORK/requirements.prod.txt"
grep -viE '^(mypy|pytest|ruff|pluggy|iniconfig)([=<>~ ]|$)' "$ROOT/rpg/requirements.txt" > "$PROD_REQ"
"$PY" -m pip install --no-cache-dir --upgrade pip >/dev/null
"$PY" -m pip install --no-cache-dir -r "$PROD_REQ"
# 瘦身:去掉 pip / setuptools / __pycache__ / 测试目录
"$PY" -m pip uninstall -y pip setuptools wheel 2>/dev/null || true
find "$STAGE/runtime/python" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/runtime/python" -type d \( -name 'tests' -o -name 'test' \) -prune -exec rm -rf {} + 2>/dev/null || true

# ── 3. 便携 PostgreSQL(zonky:Maven 上的 jar 内含 postgres-<artifact>.txz)──
echo "== 3/5 便携 PostgreSQL (zonky $PG_VER $ZONKY_ARTIFACT) =="
ZONKY_JAR="embedded-postgres-binaries-${ZONKY_ARTIFACT}-${PG_VER}.jar"
dl "${ZONKY_BASE}/embedded-postgres-binaries-${ZONKY_ARTIFACT}/${PG_VER}/${ZONKY_JAR}" "$WORK/pg.jar"
( cd "$WORK" && unzip -oq pg.jar 'postgres-*.txz' )
mkdir -p "$STAGE/pg"
tar -xf "$WORK"/postgres-*.txz -C "$STAGE/pg"     # 解出 bin/lib/share(zonky 精简:initdb/pg_ctl/postgres + pgcrypto/pg_trgm 扩展)
[ -x "$STAGE/pg/bin/postgres" ] || { echo "✗ postgres 二进制未找到" >&2; exit 1; }
"$STAGE/pg/bin/postgres" --version || { echo "✗ postgres 无法执行(部署目标不兼容?)" >&2; exit 1; }

# ── 4. pgvector:zonky 便携包不含 pg_config/头文件,无法就地编译 → 跳过 ──
# 后端 pgvector 是软依赖:缺失时自动降级 jsonb(语义检索弱化但全功能可用,与 Windows 一致)。
# 待后续提供与 zonky PG ABI 匹配的预编译 vector 模块,再放进 pg/lib + pg/share/extension 即可启用。
echo "== 4/5 pgvector 跳过(zonky 精简包无构建链;软依赖降级 jsonb)=="

# ── 5. 后端源码 + 前端产物(剔除测试/夹具/venv;小说夹具绝不进包)──
echo "== 5/5 后端源码 + 前端 =="
mkdir -p "$STAGE/app-template"
# 前端:确保已 build
if [ ! -d "$ROOT/frontend/dist" ]; then
  echo "  前端未构建,执行 npm run build…"
  ( cd "$ROOT/frontend" && APP_VERSION="$(cat "$ROOT/VERSION")" npm run build )
fi
mkdir -p "$STAGE/app-template/frontend"
cp -R "$ROOT/frontend/dist" "$STAGE/app-template/frontend/dist"
# 后端 rpg/:rsync 排除一切不该进包的(.venv / 测试 / 夹具 / 缓存 / 本地数据)
rsync -a \
  --exclude '.venv/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude 'tests/' --exclude '.test-fixtures/' \
  --exclude 'platform_data/' --exclude '.pytest_cache/' --exclude '.mypy_cache/' \
  --exclude '.ruff_cache/' \
  "$ROOT/rpg/" "$STAGE/app-template/rpg/"

# ── 收尾 ──
echo "$(cat "$ROOT/VERSION")" > "$STAGE/.bundle-version"
rm -rf "$WORK"
echo "== 完成 == resources-staged 体积:$(du -sh "$STAGE" | cut -f1)"
