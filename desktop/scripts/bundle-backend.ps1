# bundle-backend.ps1 —— Windows 版组装脚本(对应 bundle-backend.sh)。
# 产出 desktop/resources-staged/{runtime/python, pg, app-template/{rpg,frontend/dist}}。
#
# pgvector:【随包构建】。theseus-rs 的 Windows 包自带 include\server + lib\postgres.lib
# (已核实 17.10.0),pgvector 的 Makefile.win 就地 nmake 即可,产物 vector.dll 落 pg\lib
# ($libdir),控制文件落 pg\share\extension。**不再跳过**——2026-08 用户实测:跳过后
# 桌面版缺 share\extension\vector.control,建 RAG 向量时才暴露,是「装完像好的、用起来才坏」
# 的静默故障。捆绑版必须自带 pgvector,构建不出来就让构建失败(见文末校验),不许静默出包。
$ErrorActionPreference = 'Stop'

# ── [ADJUST] 版本与来源 ──
$PyVer       = '3.12.13'
$PbsTag      = '20260610'
$PgVer       = '17.10.0'
$PgvectorVer = 'v0.8.0'
$PbsBase = "https://github.com/astral-sh/python-build-standalone/releases/download/$PbsTag"
$PgBase  = "https://github.com/theseus-rs/postgresql-binaries/releases/download/$PgVer"
$PgvectorBase = 'https://github.com/pgvector/pgvector/archive/refs/tags'

# ── 路径 ──
$Here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Desk  = Resolve-Path (Join-Path $Here '..')
$Root  = Resolve-Path (Join-Path $Desk '..')
$Stage = Join-Path $Desk 'resources-staged'
$Work  = Join-Path $Desk '.bundle-work'

$PbsTriple = 'x86_64-pc-windows-msvc'
$PgTarget  = 'x86_64-pc-windows-msvc'
Write-Host "== 目标: $PbsTriple / PG $PgTarget =="

if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
if (Test-Path $Work)  { Remove-Item -Recurse -Force $Work }
New-Item -ItemType Directory -Force -Path $Stage, $Work | Out-Null

# 下载带重试:Invoke-WebRequest 自身无重试,GitHub release 偶发 5xx/断流会让整轮构建失败。
function Dl($url, $out) {
  Write-Host "  ↓ $url"
  for ($i = 1; $i -le 3; $i++) {
    try { Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing; return }
    catch {
      if ($i -eq 3) { throw "下载失败(3 次): $url —— $($_.Exception.Message)" }
      Write-Host "  ! 第 $i 次失败,重试: $($_.Exception.Message)"
      Start-Sleep -Seconds (3 * $i)
    }
  }
}

# 出包前的硬校验:缺件必须让构建【失败】,不许静默出一个装完才发现坏的包。
function Assert-Path($path, $what) {
  if (-not (Test-Path $path)) { throw "✗ 捆绑校验失败:缺 $what（$path）" }
}

# ── 运行时缓存(便携 Python+依赖 + 便携 PG+pgvector)→ 跨补丁构建字节一致 → blockmap 差量极小 → 小更新包 ──
# key 必须含 $PgvectorVer:否则「加/升 pgvector」这类只改本脚本的变更会命中旧缓存 →
# 复用一份【没有 vector.dll】的 pg → 修了等于没修(2026-08 复盘:差点踩)。
$ReqHash = (Get-FileHash "$Root\rpg\requirements.txt" -Algorithm SHA256).Hash.Substring(0,12)
$PgvNum = $PgvectorVer.TrimStart('v')
$RuntimeCache = Join-Path $Desk ".runtime-cache\py$PyVer-pg$PgVer-pgv$PgvNum-$PbsTriple-req$ReqHash"
$RuntimeCached = $false
if ((Test-Path "$RuntimeCache\runtime\python\python.exe") -and (Test-Path "$RuntimeCache\pg\bin\postgres.exe")) {
  # 二道闸:key 之外再验缓存内容真含 pgvector(缓存是跨 workflow 共享的外部状态,
  # 光靠 key 约定不足以保证——内容对不上就当未命中重建)。
  if (Test-Path "$RuntimeCache\pg\share\extension\vector.control") {
    Write-Host "== 运行时缓存命中 → 复用 runtime+pg,跳过下载/安装 =="
    Copy-Item "$RuntimeCache\runtime" "$Stage\runtime" -Recurse
    Copy-Item "$RuntimeCache\pg" "$Stage\pg" -Recurse
    $RuntimeCached = $true
  } else {
    Write-Host "== 运行时缓存陈旧(pg 内无 pgvector)→ 丢弃重建 =="
    Remove-Item -Recurse -Force $RuntimeCache
  }
}

if (-not $RuntimeCached) {
# ── 1. 便携 Python ──
Write-Host "== 1/5 便携 Python ($PyVer) =="
$pyTar = "cpython-$PyVer+$PbsTag-$PbsTriple-install_only.tar.gz"
Dl "$PbsBase/$pyTar" "$Work\python.tar.gz"
tar -xzf "$Work\python.tar.gz" -C $Work               # 解出 .\python\
New-Item -ItemType Directory -Force -Path "$Stage\runtime" | Out-Null
Move-Item "$Work\python" "$Stage\runtime\python"
$Py = "$Stage\runtime\python\python.exe"

# ── 2. 安装依赖(剔除 dev)──
Write-Host "== 2/5 安装依赖 =="
$prodReq = "$Work\requirements.prod.txt"
Get-Content "$Root\rpg\requirements.txt" |
  Where-Object { $_ -notmatch '^(mypy|pytest|ruff|pluggy|iniconfig)([=<>~ ]|$)' } |
  Set-Content $prodReq
& $Py -m pip install --no-cache-dir --upgrade pip | Out-Null
& $Py -m pip install --no-cache-dir -r $prodReq
& $Py -m pip uninstall -y pip setuptools wheel 2>$null

# ── 3. 便携 PostgreSQL ──
Write-Host "== 3/5 便携 PostgreSQL ($PgVer) =="
$pgTar = "postgresql-$PgVer-$PgTarget.tar.gz"
Dl "$PgBase/$pgTar" "$Work\pg.tar.gz"
New-Item -ItemType Directory -Force -Path "$Stage\pg" | Out-Null
tar -xzf "$Work\pg.tar.gz" -C "$Stage\pg" --strip-components=1
if ($LASTEXITCODE -ne 0) { throw "解压 PostgreSQL 失败 ($LASTEXITCODE)" }

# 解压完整性校验(与 bundle-backend.sh 对齐:此前 Windows 侧【完全没有校验】,
# 下载/解压残缺会一路走到出包,装完 initdb 才炸)。
Assert-Path "$Stage\pg\bin\postgres.exe" 'postgres.exe'
Assert-Path "$Stage\pg\bin\initdb.exe"   'initdb.exe'
Assert-Path "$Stage\pg\bin\pg_ctl.exe"   'pg_ctl.exe'
Assert-Path "$Stage\pg\share\postgres.bki" 'share\postgres.bki(initdb 引导必需)'
& "$Stage\pg\bin\postgres.exe" --version
if ($LASTEXITCODE -ne 0) { throw "postgres.exe 无法执行 ($LASTEXITCODE)" }

# ── 4. pgvector(随包构建;失败即让整轮构建失败)──
Write-Host "== 4/5 pgvector ($PgvectorVer) =="
$pgvDir = "pgvector-$PgvNum"
Dl "$PgvectorBase/$PgvectorVer.tar.gz" "$Work\pgvector.tar.gz"
tar -xzf "$Work\pgvector.tar.gz" -C $Work
if ($LASTEXITCODE -ne 0) { throw "解压 pgvector 失败 ($LASTEXITCODE)" }

# nmake/cl 只有进了 MSVC 开发者环境才在 PATH 里(GitHub windows runner 装的是 VS,
# 但不预置环境)。用 vswhere 定位 → 在同一个 cmd 会话里 vcvarsall + nmake。
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
Assert-Path $vswhere 'vswhere.exe(需要 Visual Studio 生成工具)'
$vsPath = (& $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath) | Select-Object -First 1
if (-not $vsPath) { throw "✗ 未找到带 MSVC x64 工具集的 Visual Studio 安装" }
$vcvars = Join-Path $vsPath 'VC\Auxiliary\Build\vcvarsall.bat'
Assert-Path $vcvars 'vcvarsall.bat'

$pgvSrc = Join-Path $Work $pgvDir
Push-Location $pgvSrc
try {
  # PGROOT 指向捆绑树:Makefile.win 据此取 include\server 编译、链 lib\postgres.lib,
  # install 把 vector.dll 放进 $(PGROOT)\lib(= $libdir)、控制/SQL 放进 share\extension。
  # 在 PowerShell 侧设 PGROOT 让 cmd 继承 —— 不用 cmd 的 `set X=... &&`:那种写法会把
  # `&&` 前的空格一起吃进变量值,PGROOT 尾多一个空格,后面拼出来的路径全错。
  $env:PGROOT = Join-Path $Stage 'pg'
  # `call` 调批处理再接 `&&` 链,退出码才可靠回传。
  cmd /c "call `"$vcvars`" x64 && nmake /F Makefile.win && nmake /F Makefile.win install"
  if ($LASTEXITCODE -ne 0) { throw "pgvector 构建失败 ($LASTEXITCODE)" }
} finally { Pop-Location }

# 装没装上必须当场判定——这正是用户实测踩的坑(包里没 vector.control,
# 一路到「建 RAG 向量」才暴露)。
Assert-Path "$Stage\pg\share\extension\vector.control" 'pgvector 控制文件 vector.control'
Assert-Path "$Stage\pg\lib\vector.dll" 'pgvector 模块 vector.dll'
Write-Host "  ✓ pgvector 已装入捆绑 pg(vector.dll + vector.control)"

# 填充运行时缓存(供后续补丁构建复用 → 小体积差量更新)
New-Item -ItemType Directory -Force -Path $RuntimeCache | Out-Null
if (Test-Path "$RuntimeCache\runtime") { Remove-Item -Recurse -Force "$RuntimeCache\runtime" }
if (Test-Path "$RuntimeCache\pg")      { Remove-Item -Recurse -Force "$RuntimeCache\pg" }
Copy-Item "$Stage\runtime" "$RuntimeCache\runtime" -Recurse
Copy-Item "$Stage\pg" "$RuntimeCache\pg" -Recurse
}  # ← end「运行时构建/缓存复用」块(命中缓存则跳过上面 1-4 步)

# ── 出包前总闸(覆盖「新构建」与「缓存复用」两条路)──
# 只要捆绑树缺件就地失败。缺 pgvector 的包装完看着正常,直到建 RAG 向量才暴露 —— 那种
# 静默故障靠人肉发现代价太高,宁可 CI 红。
Assert-Path "$Stage\runtime\python\python.exe"          '便携 Python'
Assert-Path "$Stage\pg\bin\postgres.exe"                'postgres.exe'
Assert-Path "$Stage\pg\share\postgres.bki"              'share\postgres.bki'
Assert-Path "$Stage\pg\share\extension\vector.control"  'pgvector vector.control'
Assert-Path "$Stage\pg\lib\vector.dll"                  'pgvector vector.dll'

# 仅预热运行时缓存(CI warm-runtime-cache.yml 在 main 上跑 → 字节一致运行时存 main 作用域缓存,
# 之后每个 release tag 构建从 main 恢复同一份 → blockmap 差量极小)。不需前端/源码,就绪即退出。
if ($env:RUNTIME_ONLY -eq '1') {
  Write-Host "== RUNTIME_ONLY:运行时缓存已就绪,跳过前端+源码组装 =="
  Remove-Item -Recurse -Force $Work
  exit 0
}

# ── 5. 后端源码 + 前端(排除测试/夹具/venv;小说夹具绝不进包)──
Write-Host "== 5/5 后端源码 + 前端 =="
New-Item -ItemType Directory -Force -Path "$Stage\app-template" | Out-Null
if (-not (Test-Path "$Root\frontend\dist")) {
  Write-Host "  前端未构建,执行 npm run build…"
  Push-Location "$Root\frontend"; $env:APP_VERSION = (Get-Content "$Root\VERSION"); npm run build; Pop-Location
}
New-Item -ItemType Directory -Force -Path "$Stage\app-template\frontend" | Out-Null
Copy-Item "$Root\frontend\dist" "$Stage\app-template\frontend\dist" -Recurse
# robocopy 排除(/XD 目录 /XF 文件);robocopy 退出码 <8 视为成功
$xd = @('.venv','__pycache__','tests','.test-fixtures','platform_data','.pytest_cache','.mypy_cache','.ruff_cache')
robocopy "$Root\rpg" "$Stage\app-template\rpg" /E /XD $xd /XF '*.pyc' /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy 失败 ($LASTEXITCODE)" } else { $global:LASTEXITCODE = 0 }

Get-Content "$Root\VERSION" | Set-Content "$Stage\.bundle-version"
Remove-Item -Recurse -Force $Work
Write-Host "== 完成 =="
