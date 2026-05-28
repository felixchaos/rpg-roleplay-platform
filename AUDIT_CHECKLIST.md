# 前后端 1:1 Mapping 审计 Checklist（task 56）

按 `/goal` 4 条规则系统性遍历。脚本可重跑（见底部）。

## 规则 1：前端有的功能，后端要做适配
**结果**：✅ 0 violation（112/112 FE call 都有 BE 路由覆盖）

## 规则 2：后端有，前端缺少 → 补齐前端
**结果**：✅ 132/133 covered；唯一缺口是 `/api/debug/pending-question`，admin
专用 + `RPG_DEBUG_UI` env 守门，**故意不给生产 UI 入口**。

## 规则 3：前端有，后端没有 → 补齐后端
**结果**：✅ 0 violation。所有 `window.api.*` / `api.raw.*` 调用路径都能在
`grep -rE '@(router|app)\.(get|post)'` 找到匹配 endpoint（含 `{id}` 模式）。

## 规则 4：前端所有按钮都必须有对应功能
**结果**：✅ 9 → 3 dead buttons；剩 3 项为正则误报（`<div>` / `<button type="button">` 多行声明），逐项核对后**无真 dead button**。

历史修过的 dead buttons（按 task 编号倒序）：
- task 56: CAPTCHA reCAPTCHA 版本（3） + Turnstile widget mode（3） → 加 state + 接 useAutoSave 持久化
- task 50: PlatformShell 刷新 / 分支树 放大/缩小/网格 / API 表格 复制路径 / 角色卡更多菜单 / ChatArea 重试&查看SSE / PanelCharacters 拖入 mention（10+）
- task 51: SettingsPage DangerSection 清空存档 / 完全重置 / 编辑资料 保存 / SMTP test（4）

## 运行复检

```bash
cd "/path/to/repo"

# 1. 收集 BE endpoint
grep -rhnE '@(router|app)\.(get|post|delete|put|patch)\(' rpg/ --include="*.py" \
  | grep -oE '"/api/[^"]+"' | sed 's/"//g; s/{[^}]*}/{id}/g' | sort -u > /tmp/be.txt

# 2. 收集 FE 调用
node -e '
const fs = require("fs");
const fc = fs.readFileSync("frontend/src/api-client.js","utf8");
const re = /["`]\/api\/[^"`]+["`]/g;
const set = new Set();
let m; while ((m = re.exec(fc))) set.add(m[0].slice(1,-1).replace(/\$\{[^}]+\}/g,"{id}"));
for (const f of ["frontend/src/platform-app.jsx","frontend/src/game-app.jsx","frontend/Game Console.html"]) {
  let t; try { t=fs.readFileSync(f,"utf8"); } catch { continue; }
  const r2 = /api\.raw\.(GET|POST|DELETE|PUT|PATCH)\(["`]([^"`]+)["`]/g;
  let m; while ((m = r2.exec(t))) set.add(m[2].replace(/\$\{[^}]+\}/g,"{id}"));
}
[...set].sort().forEach(p => console.log(p));
' > /tmp/fe.txt

# 3. cross-reference
node -e '
const fs = require("fs");
const be = fs.readFileSync("/tmp/be.txt","utf8").trim().split("\n");
const fe = fs.readFileSync("/tmp/fe.txt","utf8").trim().split("\n");
function ok(a, b, useRe) {
  for (const x of b) {
    if (x === a) return true;
    if (a.startsWith(x)) return true;
    if (useRe && x.includes("{id}")) {
      if (new RegExp("^" + x.replace(/\{[^}]+\}/g,"[^/]+") + "$").test(a)) return true;
    }
  }
  return false;
}
const miss2 = be.filter(p => !ok(p, fe, true));
const miss3 = fe.filter(p => !ok(p, be, true));
console.log("Rule 2 violations:", miss2.length, miss2);
console.log("Rule 3 violations:", miss3.length, miss3);
'

# 4. dead buttons
node -e '
const fs = require("fs");
const files = ["frontend/src/platform-app.jsx","frontend/src/game-app.jsx","frontend/src/game-composer.jsx","frontend/src/game-panels.jsx","frontend/Game Console.html"];
let dead = [];
for (const f of files) {
  let t; try { t=fs.readFileSync(f,"utf8"); } catch { continue; }
  const ls = t.split("\n");
  for (let i = 0; i < ls.length; i++) {
    if (!/<button\b/.test(ls[i])) continue;
    const ahead = ls.slice(i, Math.min(i+6, ls.length)).join(" ");
    const m = ahead.match(/<button\b[^>]*>/);
    if (!m) continue;
    const open = ahead.slice(0, ahead.indexOf(">")+1);
    if (/onClick|onMouseDown|onMouseUp|type=["\x27]submit|disabled/.test(open)) continue;
    dead.push(f + ":" + (i+1));
  }
}
console.log("dead buttons:", dead.length);
'
```

## 最新数据（task 56 之后）

| 规则 | 状态 |
|---|---|
| 1. FE 功能 → BE 适配 | ✅ 全覆盖 |
| 2. BE endpoint → FE 入口 | ✅ 132/133（剩 1 admin debug 故意不暴露） |
| 3. FE call → BE endpoint | ✅ 112/112 |
| 4. 按钮都有功能 | ✅ 0 真 dead |
