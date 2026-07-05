/* agent-modules — 单一「模块模型」清单(语义统一 #19)
 *
 * 桌面端 ModuleModelsSection(pages/settings.jsx)与移动端(mobile/pages/MobileSettings.jsx)
 * 此前各抄一份逐字相同的模块结构数组(id / prefPrefix / persistShape / dictKey /
 * capabilityFilter / inherit / defaultModel / preferProvider / fallbackPrefix)。
 * 这些是「落库形态 + 能力过滤」的载荷字段,改一处必须改两处,极易漂移 —— 抽到此处单一来源。
 *
 * ⚠️ label / tip 文案两端刻意不同(桌面端详尽、移动端精简),且后续要 i18n,
 *    因此「显示文案」不放进这里(放进来会强行统一 → 改变某端现有显示 = 行为变化)。
 *    各渲染端用 i18nKey 走自己的 i18n / 本地文案表,结构字段则全部来自本数组。
 *
 * ModelConfigInterceptModal 的 CAP_CONFIG(image/embedding/llm 三态)是本表的子集投影:
 *    capability → { prefPrefix, capabilityFilter },由 moduleByPrefix 派生,避免再抄一份 key。
 *
 * 见: pages/settings.jsx::ModuleModelsSection · mobile/pages/MobileSettings.jsx::ModuleModelsSection
 *     components/ModelConfigInterceptModal.jsx::CAP_CONFIG
 */

/**
 * @typedef {Object} AgentModule
 * @property {string}  id               模块稳定标识(渲染端按 id 取本地/i18n 文案)
 * @property {string=} prefPrefix       flat 落库前缀(<prefPrefix>.api_id / .model_real_name)
 * @property {string}  i18nKey          i18n / 本地文案表的键(label/tip 走它)
 * @property {('flat'|'dict')=} persistShape  落库形态(默认 flat)
 * @property {string=} dictKey          dict 形态的单 key({api_id, model})
 * @property {string=} capabilityFilter AgentModelPicker 只展示含此 capability 的模型
 * @property {boolean=} inherit         是否允许「跟随主 GM」
 * @property {string=} defaultModel     无偏好时的默认模型
 * @property {string=} preferProvider   默认优先 provider
 * @property {string=} fallbackPrefix   未配时回退到哪个模块的偏好
 */

// 功能分组(渲染端按此顺序分块 + 出小标题)。同组放一起,不再上下散落(#优化)。
// 渲染端用 i18nKey 走 settings.more.modules.group_<id> / mobile.settings.modules.group.<id>。
export const MODULE_GROUPS = [
  { id: "core",   i18nKey: "core" },    // 对话核心:三贤者 + GM 管线
  { id: "script", i18nKey: "script" },  // 剧本与角色卡
  { id: "world",  i18nKey: "world" },   // 世界模拟与历史
  { id: "gen",    i18nKey: "gen" },     // 检索与生成
  { id: "misc",   i18nKey: "misc" },    // 通用兜底
];

/** @type {AgentModule[]} */
export const MODULES = [
  // ── 对话核心(三贤者:文宗 gm / 司命 sub_agent / 史官 recorder + GM 管线辅助)──
  { id: "gm",            group: "core",   i18nKey: "gm",            prefPrefix: "gm" },
  { id: "sub_agent",     group: "core",   i18nKey: "sub_agent",     persistShape: "dict", dictKey: "sub_agent_model_override", inherit: true },
  { id: "recorder",      group: "core",   i18nKey: "recorder",      prefPrefix: "recorder",  fallbackPrefix: "extractor", inherit: true },
  { id: "extractor",     group: "core",   i18nKey: "extractor",     prefPrefix: "extractor",                inherit: true },
  { id: "verifier",      group: "core",   i18nKey: "verifier",      prefPrefix: "acceptance_verifier",      inherit: true },
  { id: "set_parser",    group: "core",   i18nKey: "set_parser",    prefPrefix: "set_parser",               inherit: true },
  // ── 剧本与角色卡 ──
  { id: "editor",        group: "script", i18nKey: "editor",        prefPrefix: "editor",                   inherit: true },
  { id: "console",       group: "script", i18nKey: "console",       persistShape: "dict", dictKey: "console_assistant_model_override", inherit: true },
  { id: "card_gen",      group: "script", i18nKey: "card_gen",      prefPrefix: "character_card_generator", inherit: true },
  { id: "card_import",   group: "script", i18nKey: "card_import",   prefPrefix: "card_import",              inherit: true },
  { id: "critic",        group: "script", i18nKey: "critic",        prefPrefix: "critic",                   inherit: true },
  // ── 世界模拟与历史 ──
  { id: "black_swan",    group: "world",  i18nKey: "black_swan",    prefPrefix: "black_swan_agent",         inherit: true },
  { id: "phase_digest",  group: "world",  i18nKey: "phase_digest",  prefPrefix: "phase_digest",             inherit: true },
  // ── 检索与生成 ──
  { id: "embedder",      group: "gen",    i18nKey: "embedder",      prefPrefix: "embed",     capabilityFilter: "embedding", inherit: false, defaultModel: "text-embedding-004", preferProvider: "vertex_ai" },
  { id: "image_gen",     group: "gen",    i18nKey: "image_gen",     prefPrefix: "image_gen", capabilityFilter: "image_gen", inherit: false, fallbackPrefix: "gm" },
  // ── 通用兜底 ──
  { id: "agent",         group: "misc",   i18nKey: "agent",         prefPrefix: "agent",                    inherit: true },
];

/**
 * @typedef {Object} EngineFeature
 * @property {string} key      偏好键前缀(后端 core.feature_flags 同名);落库为 `<key>.enabled` 布尔
 * @property {string} group    所属功能分组(与 MODULE_GROUPS.id 对齐,同组同块显示)
 * @property {string} i18nKey  文案键(label/tip 走 settings/mobile 的 features.<i18nKey>)
 */

// 引擎特性开关(每用户、默认开)。与「模块模型」同组同块显示 —— 每个功能分组里:
// 先列该组的特性开关(布尔 switch),再列该组可配的模型。前后端键名单一来源(core.feature_flags)。
/** @type {EngineFeature[]} */
export const FEATURES = [
  // 对话核心:三贤者流水线
  { key: "ctx_tiered",       group: "core",  i18nKey: "ctx_tiered" },       // 分层上下文缓存(司命)
  { key: "recorder_unified", group: "core",  i18nKey: "recorder_unified" }, // 史官三合一
  { key: "narrator_slim",    group: "core",  i18nKey: "narrator_slim" },    // 文宗精简(去工具循环)
  // 检索与生成
  { key: "rag_gate",         group: "gen",   i18nKey: "rag_gate" },         // RAG 检索闸
  // 世界模拟与历史
  { key: "anchor_pace",      group: "world", i18nKey: "anchor_pace" },      // 世界线锚点节奏
  { key: "kb_state",         group: "world", i18nKey: "kb_state" },         // 存档知识库 DB 化
  { key: "consequence_ledger", group: "world", i18nKey: "consequence_ledger" }, // 后果账本(承诺到期回响)
  { key: "world_heartbeat",    group: "world", i18nKey: "world_heartbeat" },   // 世界心跳(玩家不在场时的世界侧小事)
  { key: "channel_fallback",  group: "world", i18nKey: "channel_fallback" },  // 跨渠道自动备援(主渠道故障切备用凭据)
  { key: "npc_agenda",        group: "world", i18nKey: "npc_agenda" },        // NPC 议程(当下活跃 NPC 的意图/态度)
];

/** prefPrefix → 模块(供 CAP_CONFIG 等子集投影派生 capabilityFilter)。 */
export const moduleByPrefix = MODULES.reduce((acc, m) => {
  if (m.prefPrefix) acc[m.prefPrefix] = m;
  return acc;
}, {});

if (typeof window !== "undefined") {
  window.AGENT_MODULES = MODULES;
}
