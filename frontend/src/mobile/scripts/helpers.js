/* MobileScripts 纯工具 / 常量 —— 从 pages/MobileScripts.jsx 拆出,逐字节不变。 */

import i18n from '../../i18n';
import {
  ACTIVE_IMPORT_STATUSES,
  IMPORT_TERMINAL_STATUSES,
  scriptPlayBlockKind,
} from '../../lib/script-play-gate.js';

/* ─── 小工具 ─────────────────────────────────────── */
const fmtWan = (w) => {
  const n = Number(w) || 0;
  return n >= 10000
    ? (n / 10000).toFixed(n >= 100000 ? 0 : 1).replace(/\.0$/, '') + i18n.t('mobile.scripts.unit.wan_chars')
    : n > 0 ? n + i18n.t('mobile.scripts.unit.chars') : '—';
};
const fmtN = (n) => (n == null ? '—' : Number(n).toLocaleString());

const ACTIVE_STATUSES = ACTIVE_IMPORT_STATUSES;
const TERMINAL_STATUSES = IMPORT_TERMINAL_STATUSES;
const getSplitRules = () => [
  { id: 'auto',       label: i18n.t('mobile.scripts.split_rule.auto') },
  { id: 'corpus',     label: i18n.t('mobile.scripts.split_rule.corpus') },
  { id: 'chapter_cn', label: i18n.t('mobile.scripts.split_rule.chapter_cn') },
  { id: 'chapter_en', label: i18n.t('mobile.scripts.split_rule.chapter_en') },
  { id: 'number_dot', label: i18n.t('mobile.scripts.split_rule.number_dot') },
  { id: 'paren_num',  label: i18n.t('mobile.scripts.split_rule.paren_num') },
  { id: 'custom',     label: i18n.t('mobile.scripts.split_rule.custom') },
];

// 判定在 lib/script-play-gate.js(web / 手机四处共用),这里只翻文案。readiness.missing 不参与拦截:
// fork 剧本没有切片、空白剧本没有切片和锚点,后端照样能开局。这道闸只管开新档,继续已有存档不受它影响。
function isPlayBlocked(s) {
  const kind = scriptPlayBlockKind(s);
  if (kind === 'importing') return i18n.t('mobile.scripts.play_block.importing');
  if (kind === 'no_chapters') return i18n.t('mobile.scripts.play_block.no_chapters');
  return '';
}

export { fmtWan, fmtN, ACTIVE_STATUSES, TERMINAL_STATUSES, getSplitRules, isPlayBlocked };
