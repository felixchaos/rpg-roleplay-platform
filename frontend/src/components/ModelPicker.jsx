/**
 * ModelPicker — Wave 11-D
 *
 * props:
 *   value    : string                              当前 model id
 *   onChange : (model_id: string, provider: ProviderId) => void
 *   filter?  : { capability?: keyof ModelCapabilities, kind?: "chat"|"embedding" }
 *
 * 拉 /api/models/catalog，5 分钟内存缓存。
 * 按 ProviderId 分组，顶部 capability filter chip，弃用 model 划线警告，
 * pricing + context window + source 标注。
 * 选中态 cyan border，搜索框 fuzzy。
 */

// typed import — Wave 11-D (barrel index.ts in @/types/rust/catalog/)
// import type { ModelInfo, ProviderId, ModelCapabilities, CatalogSource } from "@/types/rust/catalog/";

// ── 5 分钟内存缓存 ────────────────────────────────────────────────────────────
/** @type {{ data: ModelInfo[] | null, ts: number }} */
const _cache = { data: null, ts: 0 };
const CACHE_TTL_MS = 5 * 60 * 1000;

async function fetchCatalog() {
  const now = Date.now();
  if (_cache.data && now - _cache.ts < CACHE_TTL_MS) return _cache.data;
  try {
    const res = await (window.api && window.api.models && window.api.models.catalog
      ? window.api.models.catalog()
      : fetch("/api/models/catalog", { credentials: "include" }).then((r) => r.json()));
    const arr = (res && Array.isArray(res.models)) ? res.models : [];
    _cache.data = arr;
    _cache.ts = now;
    return arr;
  } catch (_) {
    return _cache.data || [];
  }
}

// ── Provider 显示名 ───────────────────────────────────────────────────────────
const PROVIDER_LABELS = {
  OpenAI:        "OpenAI",
  Anthropic:     "Anthropic",
  GoogleAIStudio:"Google AI Studio",
  AgentPlatform: "Agent Platform",
  OpenRouter:    "OpenRouter",
  DeepSeek:      "DeepSeek",
  XAi:           "xAI",
  XiaomiMimo:    "MiMo",
  AlibabaQwen:   "Qwen",
  TencentHunyuan:"Hunyuan",
};

// 固定分组顺序
const PROVIDER_ORDER = [
  "Anthropic",
  "OpenAI",
  "GoogleAIStudio",
  "AgentPlatform",
  "OpenRouter",
  "DeepSeek",
  "XAi",
  "XiaomiMimo",
  "AlibabaQwen",
  "TencentHunyuan",
];

// ── Capability filter chip 定义 ───────────────────────────────────────────────
const CAP_CHIPS = [
  { key: "streaming",        label: "流式" },
  { key: "tools",            label: "工具" },
  { key: "vision",           label: "视觉" },
  { key: "extended_thinking",label: "深度思考" },
  { key: "function_calling", label: "函数调用" },
  { key: "web_search",       label: "联网搜索" },
];

// ── Source icon ───────────────────────────────────────────────────────────────
function sourceIcon(source) {
  if (source === "LiveApi")        return "🟢";
  if (source === "OpenRouterProxy")return "🔀";
  return "📋";
}
function sourceTitle(source) {
  if (source === "LiveApi")        return "Live API";
  if (source === "OpenRouterProxy")return "OpenRouter Proxy";
  return "Static Catalog";
}

// ── Context window 大标签 ─────────────────────────────────────────────────────
function ctxLabel(tokens) {
  if (!tokens) return null;
  if (tokens >= 900000)  return "1M";
  if (tokens >= 150000)  return "200K";
  if (tokens >= 100000)  return "128K";
  if (tokens >= 50000)   return "64K";
  if (tokens >= 30000)   return "32K";
  return (tokens / 1000).toFixed(0) + "K";
}

// ── Pricing ───────────────────────────────────────────────────────────────────
function fmtPrice(v) {
  if (v === null || v === undefined) return "—";
  return "$" + (Number(v)).toFixed(2);
}

// ── fuzzy match ───────────────────────────────────────────────────────────────
function fuzzyMatch(text, query) {
  if (!query) return true;
  const t = text.toLowerCase();
  const q = query.toLowerCase();
  let ti = 0;
  for (let qi = 0; qi < q.length; qi++) {
    ti = t.indexOf(q[qi], ti);
    if (ti === -1) return false;
    ti++;
  }
  return true;
}

// ── 注入样式(只注一次) ────────────────────────────────────────────────────────
const MP_STYLE_ID = "mp-styles-v1";
if (typeof document !== "undefined" && !document.getElementById(MP_STYLE_ID)) {
  const css = `
/* ModelPicker — Wave 11-D */
.mp-wrap{
  display:flex;flex-direction:column;gap:0;
  background:var(--panel,#211f1d);
  border:1px solid var(--line,#36322d);
  border-radius:var(--r-3,8px);
  overflow:hidden;
  font-family:var(--font-sans,system-ui);
  font-size:13px;
  color:var(--text,#ebe7df);
}
/* 搜索框 */
.mp-search-bar{
  display:flex;align-items:center;gap:6px;
  padding:8px 10px;
  border-bottom:1px solid var(--line-soft,#2a2724);
  background:var(--bg-deep,#131211);
}
.mp-search-bar svg{flex-shrink:0;color:var(--muted-2,#6b655e)}
.mp-search-bar input{
  flex:1;min-width:0;border:0;background:transparent;
  color:var(--text,#ebe7df);font-size:12.5px;outline:none;padding:0;
  font-family:inherit;
}
.mp-search-bar input::placeholder{color:var(--muted-2,#6b655e)}
/* capability filter 行 */
.mp-cap-row{
  display:flex;align-items:center;gap:4px;flex-wrap:wrap;
  padding:6px 10px 4px;
  border-bottom:1px solid var(--line-soft,#2a2724);
  background:var(--bg-deep,#131211);
}
.mp-chip{
  display:inline-flex;align-items:center;
  padding:2px 9px;
  font-size:11px;
  border:1px solid var(--line,#36322d);
  border-radius:999px;
  background:var(--panel-2,#282623);
  color:var(--muted,#968f85);
  cursor:pointer;
  user-select:none;
  transition:border-color .12s,background .12s,color .12s;
  white-space:nowrap;
}
.mp-chip:hover{color:var(--text,#ebe7df);border-color:var(--line-strong,#4a4540)}
.mp-chip.mp-chip-on{
  color:var(--info,#7aa6c2);
  border-color:rgba(122,166,194,.45);
  background:var(--info-soft,rgba(122,166,194,.12));
}
/* 列表滚动区 */
.mp-list{
  flex:1;overflow-y:auto;max-height:420px;
  padding:4px 0;
}
.mp-list::-webkit-scrollbar{width:5px}
.mp-list::-webkit-scrollbar-thumb{background:var(--line,#36322d);border-radius:3px}
/* Provider 分组头 */
.mp-group-head{
  font-size:10.5px;
  text-transform:uppercase;
  letter-spacing:.12em;
  color:var(--muted-2,#6b655e);
  padding:8px 12px 4px;
  display:flex;align-items:center;gap:6px;
}
/* 单个 model 行 */
.mp-model-row{
  display:grid;
  grid-template-columns:minmax(0,1fr) auto auto;
  gap:8px;
  align-items:start;
  padding:7px 12px;
  cursor:pointer;
  border:1px solid transparent;
  border-radius:0;
  transition:background .1s,border-color .1s;
  position:relative;
}
.mp-model-row:hover{background:var(--panel-2,#282623)}
.mp-model-row.mp-selected{
  background:var(--info-soft,rgba(122,166,194,.12));
  border-color:rgba(122,166,194,.45);
  border-radius:var(--r-2,6px);
}
/* 弃用 model */
.mp-model-row.mp-deprecated .mp-model-name{
  text-decoration:line-through;
  color:var(--muted,#968f85);
}
/* model 名称区 */
.mp-model-cell{display:flex;flex-direction:column;gap:2px;min-width:0}
.mp-model-name{
  font-family:var(--font-serif,serif);
  font-size:13.5px;
  letter-spacing:.02em;
  color:var(--text,#ebe7df);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.mp-model-id{
  font-family:var(--font-mono,monospace);
  font-size:10.5px;
  color:var(--muted,#968f85);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.mp-deprecated-tag{
  display:inline-flex;align-items:center;gap:3px;
  font-size:10px;
  color:var(--danger,#c8675d);
  background:var(--danger-soft,rgba(200,103,93,.12));
  border:1px solid rgba(200,103,93,.28);
  border-radius:4px;
  padding:1px 5px;
  margin-top:2px;
  align-self:flex-start;
  font-family:var(--font-mono,monospace);
}
/* 价格列 */
.mp-price-cell{
  text-align:right;
  display:flex;flex-direction:column;gap:1px;
  font-size:10.5px;
  color:var(--muted,#968f85);
  font-family:var(--font-mono,monospace);
  white-space:nowrap;
  flex-shrink:0;
}
.mp-price-cell span{display:block}
/* 右下角: ctx + source */
.mp-meta-cell{
  display:flex;flex-direction:column;align-items:flex-end;gap:3px;
  flex-shrink:0;
}
.mp-ctx-badge{
  font-size:10px;
  color:var(--muted-2,#6b655e);
  background:var(--panel-3,#2f2c28);
  border:1px solid var(--line-soft,#2a2724);
  border-radius:3px;
  padding:1px 5px;
  font-family:var(--font-mono,monospace);
  white-space:nowrap;
}
.mp-source-icon{font-size:11px;cursor:default;line-height:1}
/* 空状态 */
.mp-empty{
  padding:28px 14px;
  text-align:center;
  color:var(--muted,#968f85);
  font-size:12.5px;
}
/* 加载 */
.mp-loading{
  padding:20px 14px;
  text-align:center;
  color:var(--muted-2,#6b655e);
  font-size:12px;
}
`;
  const el = document.createElement("style");
  el.id = MP_STYLE_ID;
  el.textContent = css;
  document.head.appendChild(el);
}

// ── Main component ────────────────────────────────────────────────────────────
/**
 * @param {{
 *   value: string,
 *   onChange: (model_id: string, provider: ProviderId) => void,
 *   filter?: { capability?: keyof ModelCapabilities, kind?: "chat"|"embedding" }
 * }} props
 */
function ModelPicker({ value, onChange, filter }) {
  const { useState, useEffect, useMemo } = React;

  const [models, setModels] = useState(/** @type {ModelInfo[]} */  ([]));
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [capFilter, setCapFilter] = useState(
    /** @type {keyof ModelCapabilities | null} */ (filter && filter.capability ? filter.capability : null)
  );

  // 首次加载 catalog
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchCatalog().then((data) => {
      if (!alive) return;
      setModels(data);
      setLoading(false);
    });
    return () => { alive = false; };
  }, []);

  // 应用 filter prop 的 capability + kind
  const baseFiltered = useMemo(() => {
    let list = models;
    if (filter && filter.kind === "embedding") {
      list = list.filter((m) => m.capabilities && m.capabilities.embedding);
    } else if (filter && filter.kind === "chat") {
      list = list.filter((m) => !(m.capabilities && m.capabilities.embedding));
    }
    return list;
  }, [models, filter]);

  // 应用 capability chip + 搜索
  const displayed = useMemo(() => {
    let list = baseFiltered;
    if (capFilter) {
      list = list.filter((m) => m.capabilities && m.capabilities[capFilter]);
    }
    if (search.trim()) {
      const q = search.trim();
      list = list.filter(
        (m) => fuzzyMatch(m.id, q) || fuzzyMatch(m.display_name || "", q)
      );
    }
    return list;
  }, [baseFiltered, capFilter, search]);

  // 按 provider 分组
  const grouped = useMemo(() => {
    const map = {};
    for (const m of displayed) {
      const p = m.provider || "Unknown";
      if (!map[p]) map[p] = [];
      map[p].push(m);
    }
    // 按固定顺序排列，再追加未知 provider
    const result = [];
    for (const p of PROVIDER_ORDER) {
      if (map[p] && map[p].length > 0) result.push({ provider: p, models: map[p] });
    }
    for (const p of Object.keys(map)) {
      if (!PROVIDER_ORDER.includes(p) && map[p].length > 0) result.push({ provider: p, models: map[p] });
    }
    return result;
  }, [displayed]);

  const toggleCap = (key) => {
    setCapFilter((prev) => (prev === key ? null : key));
  };

  return (
    <div className="mp-wrap">
      {/* 搜索框 */}
      <div className="mp-search-bar">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none">
          <circle cx="6.5" cy="6.5" r="5" stroke="currentColor" strokeWidth="1.5"/>
          <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
        </svg>
        <input
          type="text"
          placeholder="搜索模型 id 或名称…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {search && (
          <button
            style={{ background: "transparent", border: 0, color: "var(--muted-2)", cursor: "pointer", padding: "2px 4px", lineHeight: 1 }}
            onClick={() => setSearch("")}
            title="清除"
          >✕</button>
        )}
      </div>

      {/* Capability filter chips */}
      <div className="mp-cap-row">
        {CAP_CHIPS.map((c) => (
          <span
            key={c.key}
            className={"mp-chip" + (capFilter === c.key ? " mp-chip-on" : "")}
            onClick={() => toggleCap(c.key)}
            title={capFilter === c.key ? "取消筛选" : `只看支持「${c.label}」的模型`}
          >
            {c.label}
          </span>
        ))}
        {capFilter && (
          <span
            className="mp-chip"
            style={{ borderStyle: "dashed", color: "var(--danger,#c8675d)" }}
            onClick={() => setCapFilter(null)}
            title="清除 capability 筛选"
          >✕ 清除</span>
        )}
      </div>

      {/* 模型列表 */}
      <div className="mp-list">
        {loading && <div className="mp-loading">加载模型目录…</div>}
        {!loading && grouped.length === 0 && (
          <div className="mp-empty">没有符合条件的模型</div>
        )}
        {!loading && grouped.map(({ provider, models: grpModels }) => (
          <div key={provider}>
            <div className="mp-group-head">
              {PROVIDER_LABELS[provider] || provider}
              <span style={{ color: "var(--muted-3,#4d4842)", fontWeight: "normal", textTransform: "none", letterSpacing: 0 }}>
                {grpModels.length}
              </span>
            </div>
            {grpModels.map((m) => {
              const isSelected = m.id === value;
              const isDeprecated = !!m.deprecated_at;
              return (
                <div
                  key={m.id}
                  className={
                    "mp-model-row" +
                    (isSelected ? " mp-selected" : "") +
                    (isDeprecated ? " mp-deprecated" : "")
                  }
                  onClick={() => onChange && onChange(m.id, m.provider)}
                  title={isDeprecated && m.retiring_at
                    ? `已弃用。停服时间: ${m.retiring_at}`
                    : isDeprecated ? "已弃用"
                    : m.id}
                >
                  {/* 名称 + id + deprecated 警告 */}
                  <div className="mp-model-cell">
                    <span className="mp-model-name">{m.display_name || m.id}</span>
                    <span className="mp-model-id">{m.id}</span>
                    {isDeprecated && (
                      <span className="mp-deprecated-tag">
                        弃用于 {m.deprecated_at}
                        {m.retiring_at && ` · 停服 ${m.retiring_at}`}
                      </span>
                    )}
                  </div>

                  {/* pricing */}
                  <div className="mp-price-cell">
                    <span title="input / 1M tokens">{fmtPrice(m.input_cost_per_million)}</span>
                    <span title="output / 1M tokens" style={{ color: "var(--muted-2,#6b655e)" }}>{fmtPrice(m.output_cost_per_million)}</span>
                  </div>

                  {/* ctx badge + source icon */}
                  <div className="mp-meta-cell">
                    {m.context_window && (
                      <span className="mp-ctx-badge" title={`Context: ${m.context_window.toLocaleString()} tokens`}>
                        {ctxLabel(m.context_window)}
                      </span>
                    )}
                    <span className="mp-source-icon" title={sourceTitle(m.source)}>
                      {sourceIcon(m.source)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

window.ModelPicker = ModelPicker;
export default ModelPicker;
