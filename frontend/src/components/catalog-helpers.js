/* catalog-helpers — Wave 11.5-A
 *
 * 共享的 model catalog 工具:
 *   - getCaps(modelInfo)   归一化 capabilities 数组(老 string[] / 新 typed object 全兼容)
 *   - capFlags(typedCaps)  把 typed { tools:true, vision:false, ... } 拍扁成 ["tools"]
 *   - CAP_LABEL            capability key → 中文显示
 *   - normalizeProviderId(p)  老 "vertex" / "vertex_ai" → "AgentPlatform" 归一化
 *
 * 老用法(settings.jsx / ModelPicker.jsx 各自定义)统一到这里,
 * 后续新加 capability / provider 改一处即可。
 *
 * 以全局挂载方式分发 —— 项目其它 JSX 文件全是 script-mode,
 * 没有 ESM import;先 import 这个文件做 side-effect 即可。
 *
 * 见: rust/crates/model_catalog/src/schema.rs::{ModelCapabilities, ProviderId}
 */

/** capability key → 中文显示标签 (typed + 兼容旧字符串 cap) */
export const CAP_LABEL = {
  streaming:          "流式输出",
  tools:              "工具调用",
  tool_use:           "工具调用",
  vision:             "视觉",
  audio:              "音频",
  structured_output:  "结构化输出",
  extended_thinking:  "深度思考",
  embedding:          "向量嵌入",
  function_calling:   "函数调用",
  prompt_caching:     "提示词缓存",
  web_search:         "联网搜索",
  pdf_input:          "PDF 输入",
  image_input:        "图像输入",
  file_input:         "文件输入",
  json_mode:          "JSON 模式",
  computer_use:       "电脑操作",
  code_exec:          "代码执行",
  audio_input:        "音频输入",
  video_input:        "视频输入",
  // 兼容旧字符串 capability (catalog 迁移前旧条目)
  text:               "文本",
  "tool-use":         "工具",
  reasoning:          "推理",
  fast:               "快",
  long:               "长上下文",
  cn:                 "中文",
  rpg:                "RPG 调优",
};

/**
 * 把 typed ModelCapabilities object 拍扁成 string[] (只保留 true 的 key)。
 * @param {Record<string, boolean> | null | undefined} caps
 * @returns {string[]}
 */
export function capFlags(caps) {
  if (!caps || typeof caps !== "object") return [];
  return Object.entries(caps).filter(([, v]) => v === true).map(([k]) => k);
}

/**
 * 归一化模型的 capabilities:
 *   - 老 shape: m.capabilities = ["fast", "vision"]   → 直接返回
 *   - 新 shape: m.capabilities = { vision: true, ... } → 转 ["vision", ...]
 *   - null/undefined → []
 * @param {{ capabilities?: string[] | Record<string, boolean> | null }} m
 * @returns {string[]}
 */
export function getCaps(m) {
  if (!m) return [];
  const c = m.capabilities;
  if (Array.isArray(c)) return c;
  return capFlags(c);
}

/* Provider id 别名全表(语义统一 #16:原 settings.jsx / MobileSettings.jsx 各抄一份)。
   把后端各处写法(显示名 / 老 id / 大小写差异)归一到 canonical credential api_id。
   注意 AgentPlatform 是 Vertex 的 SA 凭据 id;catalog 侧用 canonical "vertex_ai"。 */
export const API_ID_ALIASES = {
  OpenAI: "openai",
  OpenRouter: "openrouter",
  DeepSeek: "deepseek",
  Anthropic: "anthropic",
  AlibabaQwen: "dashscope",
  DashScope: "dashscope",
  TencentHunyuan: "hunyuan",
  Hunyuan: "hunyuan",
  XiaomiMimo: "xiaomi_mimo",
  MiMo: "xiaomi_mimo",
  SiliconFlow: "siliconflow",
  MiniMax: "minimax",
  Doubao: "doubao",
  AgentPlatform: "AgentPlatform",
  agent_platform: "AgentPlatform",
  vertex: "AgentPlatform",
  vertex_ai: "AgentPlatform",
};

/**
 * Provider id 归一化:走全别名表(显示名 / 老 id / 大小写),命中则归一,否则原样。
 * 兼顾旧语义("vertex" / "vertex_ai" → "AgentPlatform" 仍由别名表覆盖)。
 * 前端在 filter / 分组 / 比较 provider、读凭据 api_id 时统一调用。
 * @param {string | null | undefined} p
 * @returns {string}
 */
export function normalizeProviderId(p) {
  const value = String(p || "").trim();
  if (!value) return "";
  return API_ID_ALIASES[value] || API_ID_ALIASES[value.toLowerCase()] || value;
}

/**
 * 凭据 api_id → catalog api_id(credential→catalog 方向)。
 * 先走全别名表归一,再把 AgentPlatform 还原成 catalog canonical "vertex_ai"。
 * (= 旧 settings.jsx catalogApiIdForCredential)
 * @param {string | null | undefined} aid
 * @returns {string}
 */
export function credentialToCatalogId(aid) {
  const normalized = normalizeProviderId(aid);
  return normalized === "AgentPlatform" ? "vertex_ai" : normalized;
}

/**
 * catalog api_id → 凭据 api_id(catalog→credential 方向,与上者相反)。
 * catalog 的 "vertex_ai" 在凭据侧是 SA 凭据 "AgentPlatform";其余走别名表归一。
 * (= 旧 settings.jsx credentialApiIdForCatalog)
 * @param {string | null | undefined} aid
 * @returns {string}
 */
export function catalogToCredentialId(aid) {
  return aid === "vertex_ai" ? "AgentPlatform" : normalizeProviderId(aid);
}

/**
 * 从凭据列表构建「已配置且启用」的 catalog api_id 去重 Set。
 * 接受 creds.items / creds.credentials 数组(也可直接传数组)。
 * 过滤掉 enabled===false 以及无凭据的条目,AgentPlatform→vertex_ai 归一化。
 * @param {Array | { items?: Array, credentials?: Array } | null | undefined} creds
 * @returns {Set<string>}
 */
export function credApiIdSet(creds) {
  const list = Array.isArray(creds) ? creds : ((creds && (creds.items || creds.credentials)) || []);
  const ids = new Set();
  for (const c of list) {
    if (!c) continue;
    if (c.enabled === false) continue;
    // 「这条凭据算不算配好了」以后端 list_credentials 的 configured 为唯一判据。
    // 免鉴权凭据(auth_mode='none' —— 本地 Ollama / vLLM / LM Studio)本来就没有 key,
    // 按 has_credential 判会被整条丢掉:模型管理页显示「已配置」,而每一个模型选择器
    // 都报「尚未配置任何 API key」+ 供应商下拉置灰 —— 同一个问题两套判据。
    // 与 settings/models-section.jsx 的 configured 保持同源,老后端(无该字段)回退旧谓词。
    const configured = (c.configured != null)
      ? !!c.configured
      : !!(c.has_credential || c.has_key || c.key_hint !== undefined || c.auth_mode === "none");
    if (!configured) continue;
    const aid = (c.api_id || c.id || "").trim();
    ids.add(credentialToCatalogId(aid));
  }
  return ids;
}

// ── 全局挂载 (script-mode JSX 用) ────────────────────────────────────────────
if (typeof window !== "undefined") {
  window.CAP_LABEL = CAP_LABEL;
  window.capFlags = capFlags;
  window.getCaps = getCaps;
  window.normalizeProviderId = normalizeProviderId;
  window.credentialToCatalogId = credentialToCatalogId;
  window.catalogToCredentialId = catalogToCredentialId;
  window.API_ID_ALIASES = API_ID_ALIASES;
  window.credApiIdSet = credApiIdSet;
}
