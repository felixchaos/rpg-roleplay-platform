/* scripts 页公共常量与纯工具(从 pages/scripts.jsx 拆出,零行为变化)。
   play-block 判定 / 导入状态集 / 分章规则表 —— 被 ScriptsList / ScriptsImport / ScriptDetail 共用。 */

import {
  ACTIVE_IMPORT_STATUSES,
  IMPORT_TERMINAL_STATUSES as IMPORT_JOB_TERMINAL_STATUSES,
  scriptPlayBlockKind,
} from '../../lib/script-play-gate.js';

function readinessLabel(key, t) {
  return t(`scripts.my.readiness_label_${key}`, { defaultValue: key });
}

function activeJobPlayBlockReason(payload, t) {
  const job = payload?.job || payload?.active_job || payload;
  const status = String(job?.status || payload?.status || "").trim().toLowerCase();
  if (status && ACTIVE_IMPORT_STATUSES.has(status) && !IMPORT_JOB_TERMINAL_STATUSES.has(status)) {
    return t('scripts.my.play_block_importing');
  }
  if (payload?.active === true && (!status || !IMPORT_JOB_TERMINAL_STATUSES.has(status))) {
    return t('scripts.my.play_block_importing');
  }
  return "";
}

// 判定在 lib/script-play-gate.js(四端共用),这里只翻文案。readiness.missing 不参与拦截,见那边的注释。
function scriptPlayBlockReason(script, t) {
  const kind = scriptPlayBlockKind(script);
  if (kind === 'importing') return t('scripts.my.play_block_importing');
  if (kind === 'no_chapters') return t('scripts.my.play_block_missing', { items: readinessLabel('chunks', t) });
  return "";
}

// 列表行和详情页的「开始」下拉:开局闸只拦「开新游戏」,已有存档的「继续」永远可点。
// 没有存档又被拦时,整个下拉禁用(里面只剩一个不能点的「开新游戏」)。
function playDropdownState({ saves = [], block = "", t }) {
  const items = [
    ...(saves.length ? [{
      text: t('scripts.my.play_continue_group'),
      items: saves.map((sv) => ({ id: 'continue:' + sv.id, text: sv.title || ('#' + sv.id), iconName: 'caret-right-filled' })),
    }] : []),
    { id: 'new', text: t('scripts.my.play_new_game'), iconName: 'add-plus', disabled: !!block, disabledReason: block || undefined },
  ];
  return { items, disabled: !!block && saves.length === 0 };
}

const SPLIT_RULES = [
  { id: "auto",       labelKey: "scripts.import.rule_auto" },
  { id: "corpus",     labelKey: "scripts.import.rule_corpus" },
  { id: "chapter_cn", labelKey: "scripts.import.rule_chapter_cn" },
  { id: "chapter_en", labelKey: "scripts.import.rule_chapter_en" },
  { id: "number_dot", labelKey: "scripts.import.rule_number_dot" },
  { id: "paren_num",  labelKey: "scripts.import.rule_paren_num" },
  { id: "custom",     labelKey: "scripts.import.rule_custom" },
];

export { scriptPlayBlockReason, activeJobPlayBlockReason, playDropdownState, SPLIT_RULES, IMPORT_JOB_TERMINAL_STATUSES };
