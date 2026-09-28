/* new-game/helpers.js — MobileNewGame 向导的常量 & 工具。
   从 pages/MobileNewGame.jsx 纯机械搬出(区块逐字节等价,DOM/视觉/行为零变化)。 */
import i18n from '../../i18n';
import { ACTIVE_IMPORT_STATUSES, IMPORT_TERMINAL_STATUSES, scriptPlayBlockKind } from '../../lib/script-play-gate.js';

/* ================================================================
   常量 & 工具
   ================================================================ */

// 出身×身份来源约束(与 saves.jsx IdentityStep 保持完全一致)
const ALLOWED_SOURCES = {
  soul:   ['none', 'npc', 'ai', 'manual'],  // 灵魂穿越:占据原住民肉身 → 全开
  body:   ['none'],                          // 整体穿越:彻底外来者无本地身份 → 仅「不挂」
  dual:   ['npc', 'ai', 'manual'],           // 双魂同体:须有本地本体 → 不能不挂
  native: ['none', 'ai', 'manual'],          // 本世界人:你就是该角色 → 不能再选另一个原著人物
};

const ORIGIN_OPTIONS = [
  {
    value: 'soul', icon: '◈', labelKey: 'mobile.new_game.origin.soul.label',
    essenceKey: 'mobile.new_game.origin.soul.essence',
    mappingKey: 'mobile.new_game.origin.soul.mapping',
    hintKey: 'mobile.new_game.origin.soul.hint',
    accentColor: '#8db4e8', accentBg: 'rgba(85,130,200,.14)', accentBorder: 'rgba(85,130,200,.38)',
  },
  {
    value: 'body', icon: '◉', labelKey: 'mobile.new_game.origin.body.label',
    essenceKey: 'mobile.new_game.origin.body.essence',
    mappingKey: 'mobile.new_game.origin.body.mapping',
    hintKey: 'mobile.new_game.origin.body.hint',
    accentColor: '#e8a87c', accentBg: 'rgba(220,140,80,.14)', accentBorder: 'rgba(220,140,80,.38)',
  },
  {
    value: 'dual', icon: '◑', labelKey: 'mobile.new_game.origin.dual.label',
    essenceKey: 'mobile.new_game.origin.dual.essence',
    mappingKey: 'mobile.new_game.origin.dual.mapping',
    hintKey: 'mobile.new_game.origin.dual.hint',
    accentColor: '#b8a0e8', accentBg: 'rgba(160,130,210,.14)', accentBorder: 'rgba(160,130,210,.38)',
  },
  {
    value: 'native', icon: '◎', labelKey: 'mobile.new_game.origin.native.label',
    essenceKey: 'mobile.new_game.origin.native.essence',
    mappingKey: 'mobile.new_game.origin.native.mapping',
    hintKey: 'mobile.new_game.origin.native.hint',
    accentColor: '#b8b0a5', accentBg: 'rgba(150,143,133,.14)', accentBorder: 'rgba(150,143,133,.32)',
  },
];

// SOURCE_LABELS: keys rendered via t() inside StepIdentity

const STEPS = [
  { n: 0, titleKey: 'mobile.new_game.steps.script_birth' },
  { n: 1, titleKey: 'mobile.new_game.steps.role' },
  { n: 2, titleKey: 'mobile.new_game.steps.identity' },
  { n: 3, titleKey: 'mobile.new_game.steps.meta' },
  { n: 4, titleKey: 'mobile.new_game.steps.confirm' },
];

const TOTAL_STEPS = STEPS.length;

const NEWGAME_ACTIVE_IMPORT_STATUSES = ACTIVE_IMPORT_STATUSES;
const NEWGAME_IMPORT_TERMINAL_STATUSES = IMPORT_TERMINAL_STATUSES;

// 出生点 sentinel:剧本有出生点锚点数据时,「从故事开头开始」也必须是用户主动选中的一项
// (而非未选择的静默默认),对齐桌面 saves.jsx 的强制必填语义。提交时转换为 null。
const BIRTHPOINT_FROM_START = '__from_start__';
const isFromStartBirthpoint = (bp) => !!bp && bp.anchor_id === BIRTHPOINT_FROM_START;

// 判定在 lib/script-play-gate.js(web / 手机四处共用),这里只翻文案。readiness.missing 不参与拦截。
function scriptBlockReason(script) {
  const kind = scriptPlayBlockKind(script);
  if (kind === 'importing') return i18n.t('mobile.new_game.script_block.importing');
  if (kind === 'no_chapters') return i18n.t('mobile.new_game.script_block.no_chapters');
  return '';
}

export {
  ALLOWED_SOURCES, ORIGIN_OPTIONS, STEPS, TOTAL_STEPS,
  NEWGAME_ACTIVE_IMPORT_STATUSES, NEWGAME_IMPORT_TERMINAL_STATUSES,
  BIRTHPOINT_FROM_START, isFromStartBirthpoint, scriptBlockReason,
};
