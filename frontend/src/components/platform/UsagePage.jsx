// 用量统计页 + Spark 迷你图。纯机械从 platform-app.jsx 搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import {
  fmtN,
} from './shared.jsx';
import CSAlert from '@cloudscape-design/components/alert';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSContainer from '@cloudscape-design/components/container';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSTable from '@cloudscape-design/components/table';

/* ---------------------------- USAGE ---------------------------- */
const USAGE_RANGES = [
  { id: "24h", label: "24 小时", days: 1 },
  { id: "7d",  label: "7 天",   days: 7 },
  { id: "30d", label: "30 天",  days: 30 },
  { id: "90d", label: "90 天",  days: 90 },
];

// task 49：原 USAGE_BY_API / USAGE_BY_MODEL / USAGE_RECENT 是凭空捏的假调用日志
// （OpenAI 4128 次 · $8.42 / Claude Opus 4.1 · $18.74 / GPT-4o-mini 16:42:11 等），
// genSeries 用 Math.sin/cos 假装真实时序。UsagePage 现整页改接 /api/me/usage 与
// /api/me/usage/timeline；这些常量与 genSeries 已删除，没人再引用。
function Spark({ values, w = 600, h = 90, color = "var(--accent)" }) {
  // 每个 Spark 实例使用唯一 gradient id，避免多个 Spark 共用 id="sparkfill" 串色
  const gradId = React.useId ? `sparkfill-${React.useId()}` : `sparkfill-${Math.random().toString(36).slice(2)}`;
  // 防御：过滤非有限数（NaN/Infinity/null/string），避免 SVG path 出现 "NaN"
  const safe = Array.isArray(values) ? values.filter(v => Number.isFinite(v)) : [];
  // 0 个 / 1 个数据点：i/(n-1) 会除零得 NaN（"24 小时"档常触发）；
  // 退化为水平中线即可，不再生成可能炸 SVG 的坐标。
  if (safe.length < 2) {
    const midY = (h / 2).toFixed(1);
    const flat = `M0 ${midY} L${w} ${midY}`;
    return (
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" width="100%" height={h}>
        <path d={flat} fill="none" stroke={color} strokeOpacity="0.35"
              strokeWidth="1.5" strokeDasharray="3 4" strokeLinecap="round" />
      </svg>
    );
  }
  const max = Math.max(...safe, 1);
  const min = Math.min(...safe, 0);
  const range = max - min || 1;
  const denom = safe.length - 1; // 已确保 ≥ 1
  const pts = safe.map((v, i) => [(i / denom) * w, h - ((v - min) / range) * (h - 10) - 5]);
  const linePath = "M" + pts.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L");
  const areaPath = `M0 ${h} L` + pts.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L") + ` L${w} ${h} Z`;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" width="100%" height={h}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.22" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradId})`} />
      <path d={linePath} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

// B4: 直接 fetch /api/me/usage 带 recent_offset 参数（api-client.js wrapper 不传该参数）
async function _fetchUsage(days, recentOffset) {
  const base = window.__API_BASE || "";
  const url = `${base}/api/me/usage?days=${days}&recent_offset=${recentOffset}`;
  const res = await fetch(url, { credentials: "include", headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function UsagePage() {
  // task 49：整页重写。原 UsagePage 用 USAGE_BY_API / USAGE_BY_MODEL / USAGE_RECENT
  // + genSeries(Math.sin/cos) 凭空伪造所有数据，整页零 API 调用。现接 /api/me/usage
  // 与 /api/me/usage/timeline，没真实数据的字段（延迟 / 错误率 / 月预算 / 同比 ↑12%
  // 这些后端 token_usage 表里就没有的列）一律显示 "—"，不再造假数字。
  const [range, setRange] = useStatePL("30d");
  const days = USAGE_RANGES.find(r => r.id === range)?.days || 30;
  const [data, setData] = useStatePL(null);
  const [series, setSeries] = useStatePL(null);
  const [loading, setLoading] = useStatePL(false);
  const [err, setErr] = useStatePL("");
  const [tick, setTick] = useStatePL(0);
  // B4: 翻页状态
  const [recentPage, setRecentPage] = useStatePL(0);
  const RECENT_LIMIT = 20;

  useEffectPL(() => {
    let cancelled = false;
    setLoading(true); setErr("");
    (async () => {
      try {
        const [u, t] = await Promise.all([
          _fetchUsage(days, recentPage * RECENT_LIMIT),
          window.api.account.usageTimeline(days, "day"),
        ]);
        if (cancelled) return;
        setData(u || null);
        setSeries(t || null);
      } catch (e) {
        if (!cancelled) setErr(e?.message || "拉取用量失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [days, tick, recentPage]);

  // 切换时间范围时重置到第 0 页
  const handleSetRange = useCallbackPL((r) => { setRange(r); setRecentPage(0); }, []);

  const totals = (data && data.totals) || {};
  const byModel = (data && data.by_model) || [];
  const recent = (data && data.recent_turns) || [];
  const recentTotal = (data && data.recent_total) || 0;
  const forecast = (data && data.forecast) || null;
  const bucketSeries = (series && series.series) || [];
  const totalTurns = Number(totals.turns || 0);
  const totalTokIn = Number(totals.input_tokens || 0);
  const totalTokOut = Number(totals.output_tokens || 0);
  const totalCost = Number(totals.cost_usd || 0);
  const totalCachedIn = Number(totals.cached_input_tokens || 0);

  // A3: by_scenario — 后端有该字段时渲染；没有时整个区块不渲染（向后兼容）
  const byScenario = (data && data.by_scenario) || null; // null = 旧 API，不渲染
  const SCENARIO_META = {
    chat:      { label: "对话", icon: "💬" },
    opening:   { label: "开场", icon: "🎬" },
    extract:   { label: "提取", icon: "🔍" },
    embedding: { label: "向量化", icon: "📐" },
    assistant: { label: "助手", icon: "🤖" },
    tool:      { label: "工具", icon: "🔧" },
  };

  // 按 API 聚合（后端只提供 by_model，自己按 api_id 汇总）
  const byApi = useMemoPL(() => {
    const map = new Map();
    for (const r of byModel) {
      const k = r.api_id || "—";
      const cur = map.get(k) || { id: k, requests: 0, tokens_in: 0, tokens_out: 0, cost: 0 };
      cur.requests += Number(r.turns || 0);
      cur.tokens_in += Number(r.input_tokens || 0);
      cur.tokens_out += Number(r.output_tokens || 0);
      cur.cost += Number(r.cost_usd || 0);
      map.set(k, cur);
    }
    return [...map.values()].sort((a, b) => b.requests - a.requests);
  }, [byModel]);

  const reqSeriesVals = bucketSeries.map(b => Number(b.turns || 0));
  const costSeriesVals = bucketSeries.map(b => Number(b.cost_usd || 0));
  // U2: token trend series
  const tokInSeriesVals = bucketSeries.map(b => Number(b.input_tokens || 0));
  const tokOutSeriesVals = bucketSeries.map(b => Number(b.output_tokens || 0));

  return (
    <CSSpaceBetween size="l">
      {err && <CSAlert type="error" dismissible={false}>{err}</CSAlert>}

      {/* 统计卡 */}
      <CSContainer header={
        <CSHeader
          variant="h2"
          description={loading ? "加载中…" : undefined}
          actions={
            <CSSpaceBetween size="xs" direction="horizontal">
              {USAGE_RANGES.map(r => (
                <CSButton
                  key={r.id}
                  variant={range === r.id ? "primary" : "normal"}
                  onClick={() => handleSetRange(r.id)}
                >
                  {r.label}
                </CSButton>
              ))}
              <CSButton iconName="refresh" variant="icon" onClick={() => setTick(t => t + 1)} ariaLabel="刷新" />
            </CSSpaceBetween>
          }
        >
          用量 <span style={{fontWeight: "normal", fontSize: "0.85em", color: "var(--color-text-body-secondary)"}}>最近 {USAGE_RANGES.find(r => r.id === range)?.label}</span>
        </CSHeader>
      }>
        <CSColumnLayout columns={5} variant="text-grid">
          <div>
            <CSBox variant="awsui-key-label">请求数</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{fmtN(totalTurns)}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{totalTurns ? `日均 ${Math.round(totalTurns / days)}` : "—"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">Token 输入</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{fmtN(totalTokIn)}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">{totalTokOut ? `输出 ${fmtN(totalTokOut)} · 比 1 : ${(totalTokIn / Math.max(1, totalTokOut)).toFixed(1)}` : "输出 —"}</CSBox>
          </div>
          <div>
            <CSBox variant="awsui-key-label">成本</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">${totalCost.toFixed(2)}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">本窗口累计</CSBox>
          </div>
          {/* U1: 缓存输入 token */}
          <div>
            <CSBox variant="awsui-key-label">缓存输入 Token</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">{totalCachedIn ? fmtN(totalCachedIn) : "—"}</CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">
              {totalTokIn > 0 ? `占输入 ${Math.round(totalCachedIn / totalTokIn * 100)}%` : "—"}
            </CSBox>
          </div>
          {/* U1: 缓存节省估算 (cached_input_tokens × pricing.input × 75%) */}
          <div>
            <CSBox variant="awsui-key-label">缓存节省估算</CSBox>
            <CSBox fontSize="display-l" fontWeight="bold">
              {totalCachedIn > 0 && totalTokIn > 0
                ? `$${(totalCost > 0
                    ? (totalCachedIn / totalTokIn) * totalCost * 0.75
                    : 0
                  ).toFixed(3)}`
                : "—"}
            </CSBox>
            <CSBox color="text-body-secondary" fontSize="body-s">75% 折扣估算</CSBox>
          </div>
        </CSColumnLayout>
      </CSContainer>

      {/* B2: 消费预测卡 — 固定读 forecast（后端固定查 7 天，不随时间范围切换器变化） */}
      {forecast && (() => {
        const avgCost = Number(forecast.avg_daily_cost_usd || 0);
        const proj30 = Number(forecast.projected_30d_cost || 0);
        const trendPct = Number(forecast.trend_7d_vs_prev_7d_pct || 0);
        const hasData = avgCost > 0;
        const trendColor = trendPct > 20 ? "#e53935" : trendPct > 10 ? "#f57c00" : "#2e7d32";
        const trendSign = trendPct > 0 ? "+" : "";
        const alertBanner = proj30 > 200 ? (
          <div style={{ marginTop: 12, padding: "8px 12px", borderRadius: 6, background: "rgba(229,57,53,0.12)", border: "1px solid rgba(229,57,53,0.35)", color: "#e53935", fontSize: 13 }}>
            {"🔴 显著超出常规消费速率（30 天投影 $" + proj30.toFixed(2) + "）"}
          </div>
        ) : proj30 > 50 ? (
          <div style={{ marginTop: 12, padding: "8px 12px", borderRadius: 6, background: "rgba(245,124,0,0.10)", border: "1px solid rgba(245,124,0,0.30)", color: "#f57c00", fontSize: 13 }}>
            {"⚠️ 较高消费速率（30 天投影 $" + proj30.toFixed(2) + "）"}
          </div>
        ) : null;
        return (
          <CSContainer header={<CSHeader variant="h2" description="基于过去 7 天平均日消耗（固定窗口，不随时间范围切换器变化）">消费预测</CSHeader>}>
            {hasData ? (
              <div>
                <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 32, fontWeight: 700, fontFamily: "var(--font-mono,monospace)", color: "var(--color-text-heading)" }}>
                    {"$" + avgCost.toFixed(4)}
                    <span style={{ fontSize: 16, fontWeight: 400, color: "var(--color-text-body-secondary)", marginLeft: 4 }}>{" / 日"}</span>
                  </span>
                  <span style={{ fontSize: 13, color: "var(--color-text-body-secondary)" }}>
                    {"预计 30 天 "}
                    <strong style={{ color: "var(--color-text-heading)" }}>{"$" + proj30.toFixed(2)}</strong>
                    {" · "}
                    <span style={{ color: trendColor, fontWeight: 600 }}>{trendSign + trendPct.toFixed(1) + "%"}</span>
                    {" vs 前 7 天"}
                  </span>
                </div>
                {alertBanner}
              </div>
            ) : (
              <CSBox color="text-body-secondary" padding="s">暂无足够数据（需至少 1 天用量记录）</CSBox>
            )}
          </CSContainer>
        );
      })()}

      {/* A3: 按场景拆分 — 仅后端返回 by_scenario 时渲染，向后兼容旧 API */}
      {byScenario && Object.keys(byScenario).length > 0 && (() => {
        const scenarioKeys = Object.keys(byScenario);
        const scenarioTotalTurns = scenarioKeys.reduce((s, k) => s + Number(byScenario[k]?.turns || 0), 0) || 1;
        return (
          <CSContainer header={<CSHeader variant="h2" description="按场景统计本窗口请求分布">按场景拆分</CSHeader>}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 12 }}>
              {scenarioKeys.map(key => {
                const meta = SCENARIO_META[key] || { label: key, icon: "📊" };
                const sc = byScenario[key] || {};
                const turns = Number(sc.turns || 0);
                const cost = Number(sc.cost_usd || 0);
                const pct = Math.round(turns / scenarioTotalTurns * 100);
                return (
                  <div key={key} style={{
                    padding: '12px 14px', borderRadius: 6,
                    background: 'var(--color-background-container-content, rgba(255,255,255,0.03))',
                    border: '1px solid var(--color-border-divider-default, rgba(255,255,255,0.08))',
                  }}>
                    <div style={{ fontSize: 18, marginBottom: 4 }}>{meta.icon}</div>
                    <div style={{ fontSize: 12, color: 'var(--color-text-label, #aaa)', marginBottom: 6, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em' }}>{meta.label}</div>
                    <div style={{ fontSize: 20, fontWeight: 700, fontFamily: 'var(--font-mono,monospace)' }}>{fmtN(turns)}</div>
                    <div style={{ fontSize: 11, color: 'var(--color-text-body-secondary)' }}>${cost.toFixed(3)}</div>
                    <div style={{ marginTop: 6, height: 3, borderRadius: 999, background: 'var(--color-background-control-default, #333)', overflow: 'hidden' }}>
                      <div style={{ width: pct + '%', height: '100%', background: 'var(--color-text-accent, #d4a45e)', borderRadius: 999 }} />
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--color-text-body-secondary)', marginTop: 3 }}>{pct}% 占比</div>
                  </div>
                );
              })}
            </div>
          </CSContainer>
        );
      })()}

      {/* 趋势图（保留原生 SVG Spark 自绘） */}
      <CSContainer header={<CSHeader variant="h2" description="每日聚合">趋势</CSHeader>}>
        {bucketSeries.length === 0 ? (
          <CSBox textAlign="center" color="text-body-secondary" padding="l">
            {loading ? "加载中…" : "近期没有用量记录"}
          </CSBox>
        ) : (
          <CSSpaceBetween size="m">
            <CSColumnLayout columns={2} variant="text-grid">
              <div>
                <div style={{display: "flex", justifyContent: "space-between", marginBottom: 4}}>
                  <CSBox variant="awsui-key-label">请求</CSBox>
                  <span className="mono" style={{fontSize: 12, color: "var(--color-text-body-secondary)"}}>{fmtN(reqSeriesVals.reduce((a, x) => a + x, 0))}</span>
                </div>
                <Spark values={reqSeriesVals} color="var(--accent)" />
              </div>
              <div>
                <div style={{display: "flex", justifyContent: "space-between", marginBottom: 4}}>
                  <CSBox variant="awsui-key-label">成本 $</CSBox>
                  <span className="mono" style={{fontSize: 12, color: "var(--color-text-body-secondary)"}}>${costSeriesVals.reduce((a, x) => a + x, 0).toFixed(2)}</span>
                </div>
                <Spark values={costSeriesVals} color="var(--ok)" />
              </div>
            </CSColumnLayout>
            {/* U2: token 趋势 */}
            <div>
              <CSBox variant="awsui-key-label" style={{marginBottom: 6}}>Token 趋势</CSBox>
              <CSColumnLayout columns={2} variant="text-grid">
                <div>
                  <div style={{display: "flex", justifyContent: "space-between", marginBottom: 4}}>
                    <span style={{fontSize: 12, color: "var(--color-text-body-secondary)"}}>输入 Token</span>
                    <span className="mono" style={{fontSize: 12, color: "var(--color-text-body-secondary)"}}>{fmtN(tokInSeriesVals.reduce((a, x) => a + x, 0))}</span>
                  </div>
                  <Spark values={tokInSeriesVals} color="#7c9fce" />
                </div>
                <div>
                  <div style={{display: "flex", justifyContent: "space-between", marginBottom: 4}}>
                    <span style={{fontSize: 12, color: "var(--color-text-body-secondary)"}}>输出 Token</span>
                    <span className="mono" style={{fontSize: 12, color: "var(--color-text-body-secondary)"}}>{fmtN(tokOutSeriesVals.reduce((a, x) => a + x, 0))}</span>
                  </div>
                  <Spark values={tokOutSeriesVals} color="#c49b4e" />
                </div>
              </CSColumnLayout>
            </div>
          </CSSpaceBetween>
        )}
      </CSContainer>

      {/* 按 API 拆分 */}
      <CSContainer header={<CSHeader variant="h2">按 API 拆分</CSHeader>}>
        <CSTable
          columnDefinitions={[
            {
              id: "api",
              header: "API",
              cell: r => <strong style={{fontFamily: "var(--font-serif)", fontSize: 13.5}}>{r.id}</strong>,
            },
            {
              id: "requests",
              header: "请求",
              cell: r => <span className="mono">{fmtN(r.requests)}</span>,
            },
            {
              id: "tokens",
              header: "Token (入 / 出)",
              cell: r => <span className="mono"><span className="muted">{fmtN(r.tokens_in)}</span> <span className="muted-2">/</span> {fmtN(r.tokens_out)}</span>,
            },
            {
              id: "cost",
              header: "成本",
              cell: r => <span className="mono">${r.cost.toFixed(2)}</span>,
            },
            {
              id: "pct",
              header: "占比",
              cell: r => (
                <div style={{display: "flex", alignItems: "center", gap: 8}}>
                  <div style={{width: 60, height: 4, borderRadius: 999, background: "var(--color-background-control-default)", overflow: "hidden"}}>
                    <div style={{width: (totalTurns ? r.requests / totalTurns * 100 : 0) + "%", height: "100%", background: "var(--color-text-accent)"}} />
                  </div>
                  <span className="muted-2 mono" style={{fontSize: 11}}>{totalTurns ? Math.round(r.requests / totalTurns * 100) : 0}%</span>
                </div>
              ),
            },
          ]}
          items={byApi}
          trackBy="id"
          empty={
            <CSBox textAlign="center" color="text-body-secondary" padding="l">
              {loading ? "加载中…" : "暂无调用记录"}
            </CSBox>
          }
        />
      </CSContainer>

      {/* Top 模型 */}
      <CSContainer header={
        <CSHeader
          variant="h2"
          description="按请求数"
          actions={
            /* U7: 导出 CSV */
            <CSButton
              iconName="download"
              variant="normal"
              disabled={byModel.length === 0}
              onClick={() => {
                const sorted = [...byModel].sort((a, b) => Number(b.turns || 0) - Number(a.turns || 0));
                const headers = ["排名", "模型", "API", "请求数", "输入Token", "输出Token", "成本"];
                const rows = sorted.map((m, i) => [
                  i + 1,
                  m.model || "",
                  m.api_id || "",
                  m.turns || 0,
                  m.input_tokens || 0,
                  m.output_tokens || 0,
                  Number(m.cost_usd || 0).toFixed(4),
                ]);
                const csv = [headers, ...rows].map(row =>
                  row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(",")
                ).join("\n");
                const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a"); a.href = url;
                a.download = `usage_by_model_${new Date().toISOString().slice(0,10)}.csv`;
                a.click(); URL.revokeObjectURL(url);
              }}
            >
              导出 CSV
            </CSButton>
          }
        >
          Top 模型
        </CSHeader>
      }>
        <CSTable
          columnDefinitions={[
            {
              id: "rank",
              header: "#",
              width: 40,
              cell: m => <span className="mono muted-2">{String((m._rank ?? 0) + 1).padStart(2, "0")}</span>,
            },
            {
              id: "model",
              header: "模型",
              cell: m => <strong style={{fontSize: 13.5}}>{m.model}</strong>,
            },
            {
              id: "api",
              header: "API",
              cell: m => <span className="muted">{m.api_id}</span>,
            },
            {
              id: "requests",
              header: "请求",
              cell: m => <span className="mono">{fmtN(Number(m.turns || 0))}</span>,
            },
            {
              id: "tokens",
              header: "Token (入 / 出)",
              cell: m => <span className="mono"><span className="muted">{fmtN(Number(m.input_tokens || 0))}</span> <span className="muted-2">/</span> {fmtN(Number(m.output_tokens || 0))}</span>,
            },
            {
              id: "cost",
              header: "成本",
              cell: m => <span className="mono">${Number(m.cost_usd || 0).toFixed(2)}</span>,
            },
            {
              id: "pct",
              header: "占比",
              cell: m => (
                <div style={{display: "flex", alignItems: "center", gap: 8}}>
                  <div style={{width: 60, height: 4, borderRadius: 999, background: "var(--color-background-control-default)", overflow: "hidden"}}>
                    <div style={{width: (totalTurns ? Number(m.turns || 0) / totalTurns * 100 : 0) + "%", height: "100%", background: "var(--color-text-accent)"}} />
                  </div>
                  <span className="muted-2 mono" style={{fontSize: 11}}>{totalTurns ? Math.round(Number(m.turns || 0) / totalTurns * 100) : 0}%</span>
                </div>
              ),
            },
          ]}
          items={[...byModel].sort((a, b) => Number(b.turns || 0) - Number(a.turns || 0)).map((m, i) => ({ ...m, _rank: i }))}
          trackBy={m => `${m.api_id}/${m.model}`}
          empty={
            <CSBox textAlign="center" color="text-body-secondary" padding="l">
              {loading ? "加载中…" : "暂无调用记录"}
            </CSBox>
          }
        />
      </CSContainer>

      {/* 最近请求 */}
      <CSContainer header={
        <CSHeader
          variant="h2"
          description={`显示第 ${recentPage * RECENT_LIMIT + 1}–${recentPage * RECENT_LIMIT + Math.min(RECENT_LIMIT, recent.length)} 条（共 ${recentTotal} 条）· GET /api/me/usage`}
          actions={
            /* U7: 导出 CSV */
            <CSButton
              iconName="download"
              variant="normal"
              disabled={recent.length === 0}
              onClick={() => {
                const headers = ["时间", "API", "模型", "输入Token", "输出Token", "缓存输入", "成本", "上下文used", "上下文max", "存档ID"];
                const rows = recent.map(r => [
                  r.at || "",
                  r.api_id || "",
                  r.model || "",
                  r.input_tokens || 0,
                  r.output_tokens || 0,
                  r.cached_input_tokens || 0,
                  Number(r.cost_usd || 0).toFixed(4),
                  r.context_used || 0,
                  r.context_max || 0,
                  r.save_id || r.context_run_id || "",
                ]);
                const csv = [headers, ...rows].map(row =>
                  row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(",")
                ).join("\n");
                const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a"); a.href = url;
                a.download = `usage_recent_${new Date().toISOString().slice(0,10)}.csv`;
                a.click(); URL.revokeObjectURL(url);
              }}
            >
              导出 CSV
            </CSButton>
          }
        >
          最近请求
        </CSHeader>
      }>
        <CSTable
          columnDefinitions={[
            {
              id: "at",
              header: "时间",
              /* U8: 行可点击跳存档 */
              cell: r => {
                const saveId = r.save_id || r.context_run_id;
                const timeStr = r.at ? (window.__fmt?.ago(r.at) || r.at) : "—";
                return saveId
                  ? <a
                      href={`#play?save=${saveId}`}
                      className="mono"
                      style={{color: "var(--accent)", textDecoration: "none", cursor: "pointer"}}
                      title="点击跳转至存档"
                    >{timeStr}</a>
                  : <span className="mono">{timeStr}</span>;
              },
            },
            {
              id: "api",
              header: "API",
              cell: r => <span className="muted">{r.api_id}</span>,
            },
            {
              id: "model",
              header: "模型",
              cell: r => <span className="mono" style={{fontSize: 11.5}}>{r.model}</span>,
            },
            {
              id: "tokens",
              header: "Token in / out",
              cell: r => <span className="mono"><span className="muted">{fmtN(Number(r.input_tokens || 0))}</span> <span className="muted-2">/</span> {fmtN(Number(r.output_tokens || 0))}</span>,
            },
            {
              /* U1: 缓存输入列 */
              id: "cached_in",
              header: "缓存 in",
              cell: r => <span className="mono muted">{r.cached_input_tokens ? fmtN(Number(r.cached_input_tokens)) : "—"}</span>,
            },
            {
              id: "cost",
              header: "成本",
              cell: r => <span className="mono">${Number(r.cost_usd || 0).toFixed(3)}</span>,
            },
            {
              /* U4: context 进度条 */
              id: "ctx",
              header: "上下文",
              cell: r => {
                const used = Number(r.context_used || 0);
                const max = Number(r.context_max || 0);
                if (!max) return <span className="mono muted">—</span>;
                const pct = Math.min(100, Math.round(used / max * 100));
                const danger = pct >= 90;
                return (
                  <div style={{minWidth: 100}}>
                    <span className="mono" style={{fontSize: 11}}>{fmtN(used)}/{fmtN(max)} ({pct}%)</span>
                    <div style={{marginTop: 3, height: 3, borderRadius: 999, background: "var(--color-background-control-default)", overflow: "hidden"}}>
                      <div style={{width: pct + "%", height: "100%", background: danger ? "#d54" : "var(--color-text-accent)", borderRadius: 999}} />
                    </div>
                  </div>
                );
              },
            },
          ]}
          items={recent}
          trackBy={r => `${r.at || ""}/${r.api_id || ""}/${r.model || ""}/${r.input_tokens || ""}`}
          empty={
            <CSBox textAlign="center" color="text-body-secondary" padding="l">
              {loading ? "加载中…" : "暂无最近调用"}
            </CSBox>
          }
        />
        {/* B4: 翻页器 */}
        {recentTotal > RECENT_LIMIT && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 16, marginTop: 12, fontSize: 13 }}>
            <CSButton
              variant="normal"
              disabled={recentPage === 0 || loading}
              onClick={() => setRecentPage(p => Math.max(0, p - 1))}
            >{"< 上一页"}</CSButton>
            <span style={{ color: "var(--color-text-body-secondary)" }}>
              {"第 " + (recentPage + 1) + " / " + Math.ceil(recentTotal / RECENT_LIMIT) + " 页"}
            </span>
            <CSButton
              variant="normal"
              disabled={(recentPage + 1) * RECENT_LIMIT >= recentTotal || loading}
              onClick={() => setRecentPage(p => p + 1)}
            >{"下一页 >"}</CSButton>
          </div>
        )}
      </CSContainer>
    </CSSpaceBetween>
  );
}

export { UsagePage };
