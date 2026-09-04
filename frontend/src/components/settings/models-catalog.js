// 模型目录数据 + Provider 配置表 + ID 归一化别名 + 展示格式化工具。
// 从 components/settings/models-section.jsx 二次拆分,纯机械搬家,逐字节不动。
import { normalizeProviderId, credentialToCatalogId, catalogToCredentialId } from '../catalog-helpers.js';


// Provider 别名表 + 归一化/方向转换全部上提到 components/catalog-helpers.js(语义统一 #16)。
// 本文件保留原名薄别名,内部调用点与 ESM export(ModelConfigInterceptModal 依赖 normalizeApiId)零变化:
//   normalizeApiId            = normalizeProviderId   (走全别名表)
//   credentialApiIdForCatalog = catalogToCredentialId (catalog→credential:vertex_ai→AgentPlatform)
//   catalogApiIdForCredential = credentialToCatalogId (credential→catalog:AgentPlatform→vertex_ai)
const normalizeApiId = normalizeProviderId;
const credentialApiIdForCatalog = catalogToCredentialId;
const catalogApiIdForCredential = credentialToCatalogId;

/** @param {import("../types/rust/catalog/CatalogSource").CatalogSource} source */
function sourceLabel(source, _t) {
  const MAP = {
    LiveApi:        "Live API",
    StaticCatalog:  "Static",
    UserOverride:   _t ? _t('settings.more.source_user_override') : "User Override",
    OpenRouterProxy:"OpenRouter Proxy",
  };
  return MAP[source] || source || "—";
}

/** @param {number|null|undefined} n context_window 格式化 */
// K/M 缩写统一到 window.__fmt.compact(data-loader.js;语义统一 #30),保留本地别名免改调用点。
function fmtCtx(n) {
  if (window.__fmt && window.__fmt.compact) return window.__fmt.compact(n);
  if (!n) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(0)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

/** @param {number|null|undefined} v 每百万 token 价格 → 格式化 */
function fmtPrice(v) {
  if (v === null || v === undefined) return null;
  return `$${v.toFixed(3)}`;
}

const MODELS_DATA = [
  {
    id: "openai", name: "OpenAI", base_url: "https://api.openai.com/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-…3a9f", proxy: "直连",
    models: [
      { id: "gpt-6-astra", real_name: "gpt-6-astra", display: "GPT-6 Astra · 最强", capabilities: ["text", "vision", "tool-use", "rpg"], enabled: true, price: "$10.00 / $50.00", context: "1.05M", health: "ok", visible: true },
      { id: "gpt-5.6-sol", real_name: "gpt-5.6-sol", display: "GPT-5.6 Sol · 旗舰", capabilities: ["text", "vision", "tool-use", "rpg"], enabled: true, price: "$4.00 / $20.00", context: "1.05M", health: "ok", visible: true },
      { id: "gpt-5.6-terra", real_name: "gpt-5.6-terra", display: "GPT-5.6 Terra · 均衡", capabilities: ["text", "vision", "tool-use"], enabled: true, price: "$2.00 / $12.00", context: "1.05M", health: "ok", visible: true },
      { id: "gpt-5.6-luna", real_name: "gpt-5.6-luna", display: "GPT-5.6 Luna · 廉价", capabilities: ["fast", "vision"], enabled: false, price: "$0.20 / $1.20", context: "1.05M", health: "ok", visible: true },
    ]
  },
  {
    id: "anthropic", name: "Anthropic", base_url: "https://api.anthropic.com/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-***", proxy: "直连",
    models: [
      { id: "claude-opus-5", real_name: "claude-opus-5", display: "Claude Opus 5 · 长文", capabilities: ["long", "tool-use", "rpg"], enabled: true, price: "$5 / $25", context: "1M", health: "ok", visible: true },
      { id: "claude-sonnet-5", real_name: "claude-sonnet-5", display: "Claude Sonnet 5", capabilities: ["text", "fast", "tool-use"], enabled: true, price: "$2 / $10", context: "1M", health: "ok", visible: true },
      { id: "claude-opus-4-7", real_name: "claude-opus-4-7", display: "Claude Opus 4.7 · 上一代", capabilities: ["long", "tool-use"], enabled: false, price: "$5 / $25", context: "1M", health: "ok", visible: true },
      { id: "claude-haiku-4-5", real_name: "claude-haiku-4-5", display: "Claude Haiku 4.5", capabilities: ["fast"], enabled: false, price: "$1.00 / $5", context: "200K", health: "ok", visible: true },
    ]
  },
  {
    id: "google", name: "Google", base_url: "https://generativelanguage.googleapis.com/v1beta",
    enabled: false, status: "未连接", key_set: false, proxy: "需配置 API key",
    models: [
      { id: "gemini-3.8-flash", real_name: "gemini-3.8-flash", display: "Gemini 3.8 Flash · 当前旗舰", capabilities: ["fast", "vision", "tool-use", "reasoning"], enabled: false, price: "$0.75 / $3.75", context: "1M", health: "ok", visible: true },
      { id: "gemini-3.5-flash-lite", real_name: "gemini-3.5-flash-lite", display: "Gemini 3.5 Flash Lite · 最便宜", capabilities: ["fast", "vision"], enabled: false, price: "$0.30 / $2.50", context: "1M", health: "ok", visible: true },
      { id: "gemini-2.5-flash", real_name: "gemini-2.5-flash", display: "Gemini 2.5 Flash · 兜底默认", capabilities: ["fast", "vision", "tool-use"], enabled: false, price: "$0.075 / $0.30", context: "1M", health: "ok", visible: true },
    ]
  },
  {
    id: "qwen", name: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-…c024", proxy: "直连",
    models: [
      { id: "qwen3.8-max", real_name: "qwen3.8-max", display: "Qwen 3.8-Max · 旗舰", capabilities: ["cn", "rpg", "text", "reasoning"], enabled: true, price: "$2.50 / $7.50", context: "1M", health: "ok", visible: true },
      { id: "qwen3.7-max", real_name: "qwen3.7-max", display: "Qwen 3.7-Max · 上一代", capabilities: ["cn", "rpg", "text", "reasoning"], enabled: false, price: "$2.50 / $7.50", context: "1M", health: "ok", visible: true },
      { id: "qwen3.6-flash", real_name: "qwen3.6-flash", display: "Qwen 3.6 Flash", capabilities: ["cn", "fast"], enabled: true, price: "$0.19 / $1.13", context: "131K", health: "ok", visible: true },
      { id: "qwen-turbo", real_name: "qwen-turbo", display: "Qwen Turbo", capabilities: ["cn", "fast"], enabled: false, price: "¥0.04 / ¥0.08", context: "1M", health: "ok", visible: true },
    ]
  },
  {
    id: "deepseek", name: "DeepSeek", base_url: "https://api.deepseek.com/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-…a8d2", proxy: "直连",
    models: [
      { id: "deepseek-v4-pro", real_name: "deepseek-v4-pro", display: "DeepSeek V4-Pro · 旗舰", capabilities: ["reasoning", "cn", "tool-use"], enabled: true, price: "$0.66 / $1.98", context: "1M", health: "ok", visible: true },
      { id: "deepseek-v4-flash", real_name: "deepseek-v4-flash", display: "DeepSeek V4-Flash · 快速", capabilities: ["cn", "fast"], enabled: true, price: "$0.22 / $0.66", context: "1M", health: "ok", visible: true },
    ]
  },
  {
    id: "openrouter", name: "OpenRouter", base_url: "https://openrouter.ai/api/v1",
    enabled: true, status: "online", key_set: true, key_hint: "·sk-or-…f72e", proxy: "直连",
    models: ((() => {
      const data = [
        ["openai/gpt-4o", "GPT-4o", ["text", "vision", "tool-use"], "$2.50 / $10.00", "128K", true],
        ["openai/gpt-4o-mini", "GPT-4o mini", ["fast", "vision"], "$0.15 / $0.60", "128K", true],
        ["openai/o3-mini", "o3-mini", ["reasoning"], "$1.10 / $4.40", "200K", false],
        ["openai/o1", "o1", ["reasoning"], "$15 / $60", "200K", false],
        ["anthropic/claude-opus-5", "Claude Opus 5", ["long", "tool-use"], "$5.25 / $26.25", "1M", true],
        ["anthropic/claude-sonnet-5", "Claude Sonnet 5", ["text", "fast"], "$2.10 / $10.50", "1M", false],
        ["anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6", ["text", "fast"], "$3.15 / $15.75", "1M", false],
        ["anthropic/claude-haiku-4-5", "Claude Haiku 4.5", ["fast"], "$1.05 / $5.25", "200K", false],
        ["google/gemini-pro-1.5", "Gemini Pro 1.5", ["long", "vision"], "$1.25 / $5", "2M", false],
        ["google/gemini-flash-1.5", "Gemini Flash 1.5", ["fast", "vision"], "$0.075 / $0.30", "1M", false],
        ["google/gemini-2.0-flash-exp", "Gemini 2.0 Flash", ["fast", "vision"], "free", "1M", false],
        ["meta-llama/llama-3.1-405b", "Llama 3.1 405B", ["text"], "$2.70 / $2.70", "131K", false],
        ["meta-llama/llama-3.1-70b", "Llama 3.1 70B", ["text"], "$0.40 / $0.40", "131K", false],
        ["meta-llama/llama-3.3-70b", "Llama 3.3 70B", ["text"], "$0.13 / $0.40", "131K", false],
        ["mistralai/mistral-large", "Mistral Large", ["text", "tool-use"], "$2 / $6", "128K", false],
        ["mistralai/mistral-nemo", "Mistral Nemo", ["fast"], "$0.13 / $0.13", "128K", false],
        ["mistralai/codestral", "Codestral", ["text"], "$0.30 / $0.90", "32K", false],
        ["deepseek/deepseek-r1", "DeepSeek R1", ["reasoning", "cn"], "¥4 / ¥16", "64K", false],
        ["deepseek/deepseek-chat", "DeepSeek Chat", ["cn", "fast"], "¥1 / ¥2", "64K", false],
        ["qwen/qwen-2.5-72b", "Qwen 2.5 72B", ["cn", "long"], "$0.35 / $0.40", "131K", false],
        ["qwen/qwen-2.5-coder-32b", "Qwen 2.5 Coder 32B", ["text"], "$0.18 / $0.18", "33K", false],
        ["x-ai/grok-2", "Grok 2", ["text"], "$2 / $10", "128K", false],
        ["x-ai/grok-2-vision", "Grok 2 Vision", ["vision"], "$2 / $10", "8K", false],
        ["nousresearch/hermes-3-llama-3.1-70b", "Hermes 3 70B", ["rpg"], "$0.40 / $0.40", "131K", true],
        ["nousresearch/hermes-3-llama-3.1-405b", "Hermes 3 405B", ["rpg"], "$1.79 / $2.49", "131K", false],
        ["cohere/command-r-plus", "Command R+", ["tool-use"], "$2.50 / $10", "128K", false],
        ["cohere/command-r", "Command R", ["fast"], "$0.15 / $0.60", "128K", false],
        ["perplexity/llama-3.1-sonar-large", "Sonar Large", ["text"], "$1 / $1", "127K", false],
        ["microsoft/phi-3.5-mini", "Phi-3.5 mini", ["fast"], "$0.10 / $0.10", "128K", false],
        ["amazon/nova-pro", "Amazon Nova Pro", ["vision"], "$0.80 / $3.20", "300K", false],
        ["amazon/nova-lite", "Amazon Nova Lite", ["fast", "vision"], "$0.06 / $0.24", "300K", false],
        ["01-ai/yi-large", "Yi Large", ["cn"], "$3 / $3", "32K", false],
        ["zhipu/glm-4-plus", "GLM-4 Plus", ["cn"], "¥0.05 / ¥0.05", "128K", false],
        ["moonshot/kimi-k1.5", "Kimi K1.5", ["cn", "long", "reasoning"], "¥0.30 / ¥3", "200K", false],
        ["minimax/abab-7-preview", "MiniMax abab-7", ["cn"], "¥10 / ¥10", "245K", false],
        ["aetherwiing/mn-starcannon-12b", "Starcannon 12B", ["rpg"], "$0.80 / $1.20", "8K", false],
        ["sao10k/l3-euryale-70b", "Euryale 70B", ["rpg"], "$1.48 / $1.48", "16K", false],
      ];
      const _h = ["ok","ok","ok","ok","degraded","err","ok","ok","untested","ok","ok","ok","ok","err","ok","ok","ok","ok","ok","degraded","ok","ok","ok","ok","ok","ok","err","ok","untested","ok","ok","ok","ok","ok","ok","ok"];
      return data.map(([rn, disp, caps, price, ctx, en], i) => ({
        id: rn, real_name: rn, display: disp, capabilities: caps, price, context: ctx, enabled: en,
        health: _h[i % _h.length], visible: true,
      }));
    })()),
  },
  {
    id: "local", name: "本地 vLLM", base_url: "http://127.0.0.1:8000/v1",
    enabled: false, status: "未启动", key_set: false, proxy: "局域网",
    models: [
      { id: "qwen-72b", real_name: "Qwen2.5-72B-Instruct", display: "Qwen2.5-72B · 本地", capabilities: ["cn", "long"], enabled: false, price: "本地", context: "128K", health: "ok", visible: true },
    ]
  },
];

// Wave 11-C: 10 provider typed 配置表
// /** @type {Array<{id: import("../types/rust/catalog/ProviderId").ProviderId, name: string, kind: "openai_compat"|"native", defaultBase: string, keyEnv: string, note?: string, special?: "agent_platform"|"alibaba_qwen"|"openrouter"}>} */
const PROVIDERS_CONFIG = [
  {
    id: "openai",       name: "OpenAI",         kind: "openai_compat",
    defaultBase: "https://api.openai.com/v1",
    keyEnv: "OPENAI_API_KEY",
  },
  {
    id: "openrouter",   name: "OpenRouter",     kind: "openai_compat",
    defaultBase: "https://openrouter.ai/api/v1",
    keyEnv: "OPENROUTER_API_KEY",
    special: "openrouter",
    noteKey: "settings.more.providers.note_openrouter",
  },
  {
    id: "deepseek",     name: "DeepSeek",       kind: "openai_compat",
    defaultBase: "https://api.deepseek.com/v1",
    keyEnv: "DEEPSEEK_API_KEY",
  },
  {
    id: "xai",          name: "xAI (Grok)",     kind: "openai_compat",
    defaultBase: "https://api.x.ai/v1",
    keyEnv: "XAI_API_KEY",
  },
  {
    id: "xiaomi_mimo",   name: "MiMo (Xiaomi)",  kind: "openai_compat",
    defaultBase: "https://chat.d.xiaomi.net/ai/api/v1",
    keyEnv: "XIAOMI_MIMO_API_KEY",
  },
  {
    id: "hunyuan", name: "Hunyuan (Tencent)", kind: "openai_compat",
    defaultBase: "https://api.hunyuan.cloud.tencent.com/v1",
    keyEnv: "TENCENT_HUNYUAN_API_KEY",
  },
  {
    id: "anthropic",    name: "Anthropic",      kind: "native",
    defaultBase: "https://api.anthropic.com",
    keyEnv: "ANTHROPIC_API_KEY",
  },
  {
    id: "google_ai_studio", name: "Google AI Studio", kind: "native",
    // Gemini 的 OpenAI 兼容端点在 /v1beta/openai;少了这段路径会 404「找不到」。
    defaultBase: "https://generativelanguage.googleapis.com/v1beta/openai",
    keyEnv: "GOOGLE_API_KEY",
    // 下架:Google 从 2026-07-04 起封禁本服务器机房 IP(User location is not supported),
    // AI Studio 连不通。从默认候选(添加下拉 + provider 卡片)移除;仅存量已配置用户仍见其卡片
    // (带下架提示,引导改用 Agent Platform)。Gemini 统一走 Vertex。
    hidden_in_edit_modal: true,
    deprecated: true,
  },
  {
    id: "AgentPlatform", name: "Agent Platform (Service Account)", kind: "native",
    defaultBase: "",
    keyEnv: "",
    special: "agent_platform",
    // 用户级 SA 已真接通 (vertex.py / embedding.py / model_probe 全部走用户 SA)
    // EditApiModal 检测 special === 'agent_platform' 时自动隐藏 base_url + api_key 改 SA JSON textarea
    noteKey: "settings.more.providers.note_agent_platform",
  },
  {
    id: "dashscope",  name: "DashScope (Qwen)", kind: "openai_compat",
    defaultBase: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    keyEnv: "DASHSCOPE_API_KEY",
    special: "alibaba_qwen",
    noteKey: "settings.more.providers.note_dashscope",
  },
  {
    // 火山方舟(Volcengine Ark)。群反馈(行者无疆 2026-07-28)问「火山的能用吗」,自己猜地址猜不中。
    // ⚠️ 本列表是**前端写死的**,与后端 model_registry 的 catalog 是两份东西 —— v1.74.0 只在后端
    // 把 doubao 启用了,这里没加,所以他刷新后「还是没看到」。加 provider 必须两边一起改。
    // 火山同一域名下有两个不同产品的入口,这里给的是 OpenAI 兼容那条(doubao 系模型):
    //   · /api/v3   —— Ark OpenAI 兼容(本条)
    //   · /api/plan —— Agent Plan(Anthropic Messages 原生 + AUTH_TOKEN),走「自定义」配
    id: "doubao", name: "火山方舟 Doubao (Volcengine Ark)", kind: "openai_compat",
    defaultBase: "https://ark.cn-beijing.volces.com/api/v3",
    keyEnv: "ARK_API_KEY",
    noteKey: "settings.more.providers.note_doubao",
  },
  {
    // 火山方舟「Agent Plan」订阅套餐 —— 与上面的 Ark 是同域名下的两个不同产品。
    // 地址与「没有 /models」都由反馈者实测确认(deepseek-v4-pro 返 200;/models 恒 404)。
    id: "ark_agent_plan", name: "火山方舟 Agent Plan (订阅套餐)", kind: "openai_compat",
    defaultBase: "https://ark.cn-beijing.volces.com/api/plan/v3",
    keyEnv: "ARK_API_KEY",
    noteKey: "settings.more.providers.note_ark_agent_plan",
  },
];

export {
  normalizeApiId,
  credentialApiIdForCatalog,
  catalogApiIdForCredential,
  sourceLabel,
  fmtCtx,
  fmtPrice,
  MODELS_DATA,
  PROVIDERS_CONFIG,
};

