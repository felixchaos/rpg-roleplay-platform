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

/**
 * Provider id 归一化:把老的 "vertex" / "vertex_ai" 映射到新的 "AgentPlatform"。
 * 其它值原样返回。前端在 filter / 分组 / 比较 provider 时统一调用此函数。
 * @param {string | null | undefined} p
 * @returns {string}
 */
export function normalizeProviderId(p) {
  if (!p) return "";
  if (p === "vertex" || p === "vertex_ai") return "AgentPlatform";
  return p;
}

// ── 全局挂载 (script-mode JSX 用) ────────────────────────────────────────────
if (typeof window !== "undefined") {
  window.CAP_LABEL = CAP_LABEL;
  window.capFlags = capFlags;
  window.getCaps = getCaps;
  window.normalizeProviderId = normalizeProviderId;
}
