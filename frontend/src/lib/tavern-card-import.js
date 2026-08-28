/* 酒馆角色卡导入的共享执行段(TavernImportModal 的 onConfirm payload → 后端调用)。

   两个卡库入口共用:
     · 用户角色卡库(components/cards/CardViews.jsx)→ /api/v1/me/character-cards/import-*
     · 剧本 NPC 角色卡(components/scripts/ScriptDetail.jsx)→ /api/v1/scripts/{id}/character-cards/import-tavern
   两边只有「调哪个 API」不同,「多文件怎么循环 / 失败怎么统计 / ok:false 怎么当失败」
   必须一致 —— 否则同一个弹窗在两个页面表现不同。

   弹窗允许一次选最多 8 个文件,但 payload 长期只带 parsed._file(预览的那一张),
   其余文件被静默丢弃 —— 用户选 5 张只进 1 张且没有任何提示。现在 payload 带 files[],
   本模块按序逐张导入(串行:避免同名卡并发 upsert 撞唯一约束),返回汇总给调用方 toast。 */

// payload → 要导入的文件数组(兼容只带单个 file 的旧形态)。
function cardImportFiles(payload) {
  if (Array.isArray(payload?.files) && payload.files.length) return payload.files;
  return payload?.file ? [payload.file] : [];
}

// 后端统一信封:HTTP 200 也可能是 {ok:false,error}。别把它当成功。
function _unwrap(r) {
  if (r && r.ok === false) throw new Error(r.error || r.detail || '');
  return r || {};
}

/* 执行一次卡导入。
   importFile(file) / importJson(body) 由调用方注入(决定落点 = 用户卡库还是某剧本)。
   返回 { imported, replaced, failures: [{name, message}] }。 */
async function runCardImport(payload, { importFile, importJson }) {
  const summary = { imported: 0, replaced: 0, failures: [] };
  const files = cardImportFiles(payload);
  if (files.length) {
    for (const f of files) {
      try {
        const r = _unwrap(await importFile(f));
        summary.imported += 1;
        if (r.replaced) summary.replaced += 1;
      } catch (e) {
        summary.failures.push({ name: f?.name || '', message: e?.message || String(e) });
      }
    }
    return summary;
  }
  const body = payload?.json_string ? { json_string: payload.json_string }
    : (payload?.json ? { json: payload.json } : null);
  if (!body) return summary;
  if (payload?.aiSplit) body.ai_split = true;
  try {
    const r = _unwrap(await importJson(body));
    summary.imported += 1;
    if (r.replaced) summary.replaced += 1;
  } catch (e) {
    summary.failures.push({ name: '', message: e?.message || String(e) });
  }
  return summary;
}

export { cardImportFiles, runCardImport };
