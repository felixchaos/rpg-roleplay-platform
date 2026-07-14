// helpers.js — md 编辑器纯常量 + 无 UI 工具(从 pages/md-editor.jsx 机械搬出,逐字节不变)。
import i18n from '../../i18n';

// OS 自适应:Mac=⌘ / Win·Linux=Ctrl。快捷键标签 + 全局键判断都用它。
const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
const MOD_LABEL = IS_MAC ? '⌘' : 'Ctrl';

// 文件树节点类型 → 排序。label 由 t('md_editor.tree.group.KIND') 在组件内动态取。
const NODE_GROUPS = [
  { kind: 'chapter',   icon: '§' },
  { kind: 'card',      icon: '@' },
  { kind: 'worldbook', icon: '#' },
  { kind: 'anchor',    icon: '~' },
  { kind: 'canon',     icon: '*' },
];

const api = () => (typeof window !== 'undefined' ? window.api : null);
const toast = (msg, opts) => { try { window.__apiToast?.(msg, opts); } catch (_) {} };
// 章节标题存「裸标题」(不含「第N章」),显示时由前端加序号前缀。剥掉任何已混入的前缀,防重命名/重建出现「第5章 第5章 …」双序号。
const stripChapterPrefix = (s) => String(s || '').replace(/^\s*第\s*[0-9一二三四五六七八九十百千零〇两]+\s*章\s*/, '');
// Canon 实体类型本地化 → i18n md_editor.canon_type.* 键。含常见同义词回退。
const CANON_TYPE_KEYS = { character: 'character', person: 'character', faction: 'faction', organization: 'faction', org: 'faction', location: 'location', place: 'location', item: 'item', concept: 'concept', event: 'event' };
const canonTypeZh = (tp) => { const k = CANON_TYPE_KEYS[String(tp || '').toLowerCase()]; return k ? i18n.t(`md_editor.canon_type.${k}`) : (tp || i18n.t('md_editor.canon_type.concept')); };

// 每类实体图标 + 能力。章节删除走后端 delete_chapters(删一批 → 单次重排,与 merge/split 同语义:
// 结构改动后 RAG(chunks/facts/锚点按 chapter_index 外键)需重新提取对齐)。
// 拖拽重排仅世界书安全(按 priority);其余实体有结构语义,不做乱序拖拽。
const KIND_ICON = { chapter: '§', card: '@', worldbook: '#', anchor: '~', canon: '*' };
const CAN_DELETE = { chapter: true, card: true, worldbook: true, anchor: true, canon: true };
const CAN_RENAME = { chapter: true, card: true, worldbook: true, anchor: true, canon: true };
const CAN_DRAG = { worldbook: true };

const CAN_CREATE_KIND = () => true; // 5 类都支持新建

export const nodeKey = (kind, id) => `${kind}:${id}`;

const KIND_LABEL_KEY = { chapter: 'md_editor.tree.group_chapter', card: 'md_editor.tree.group_card', worldbook: 'md_editor.tree.group_worldbook', anchor: 'md_editor.tree.group_anchor', canon: 'md_editor.tree.group_canon' };
function kindLabelZh(kind) { return i18n.t(KIND_LABEL_KEY[kind] || '', { defaultValue: ({ chapter: '章节', card: '角色卡', worldbook: '世界书', anchor: '时间线', canon: '设定' })[kind] || kind }); }

export { IS_MAC, MOD_LABEL, NODE_GROUPS, api, toast, stripChapterPrefix, canonTypeZh, KIND_ICON, CAN_DELETE, CAN_RENAME, CAN_DRAG, CAN_CREATE_KIND, kindLabelZh };
