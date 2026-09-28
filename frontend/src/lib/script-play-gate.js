/* 开局闸:判断一个剧本现在能不能「开新档」—— web 剧本列表/详情、web 新游戏弹窗、手机剧本页、
   手机新游戏向导共用这一个判定,各端只负责把结果翻成自己的文案。

   只拦两种情况:
     - importing:导入或重建任务还在后台跑(开局会卡在出生点选择、丢已填内容);
     - no_chapters:一章都没有,没东西可玩。
   不按 readiness.missing 拦。readiness 是展示用的就绪度(列表「状态」列),其中 chunks / anchors
   缺了后端照样能开局:fork 出来的剧本不复制 document_chunks,空白剧本有章节但没有切片和锚点,
   md-editor 的「从本章开始」也从来不过这道闸。以前四处各写一份、都按 missing 拦,
   normalizeScript 透传 readiness 以后就会把这些能玩的剧本挡在门外。

   这道闸只管开新档;继续已有存档不受它影响。 */

const ACTIVE_IMPORT_STATUSES = new Set(['queued', 'pending', 'running', 'processing', 'importing', 'started']);
const IMPORT_TERMINAL_STATUSES = new Set(['done', 'done_with_errors', 'partial', 'failed', 'cancelled']);

function isActiveImportStatus(status) {
  const s = String(status || '').trim().toLowerCase();
  return !!s && ACTIVE_IMPORT_STATUSES.has(s) && !IMPORT_TERMINAL_STATUSES.has(s);
}

/** 返回 '' | 'importing' | 'no_chapters'。 */
function scriptPlayBlockKind(script) {
  if (!script) return '';
  const status = script.import_status
    || script.job_status
    || script.active_job?.status
    || script.readiness?.active_job?.status
    || '';
  if (isActiveImportStatus(status)) return 'importing';
  if (Number(script.chapter_count || 0) <= 0) return 'no_chapters';
  return '';
}

export { ACTIVE_IMPORT_STATUSES, IMPORT_TERMINAL_STATUSES, isActiveImportStatus, scriptPlayBlockKind };
