/* 时间线面板 + 世界线锚点子区(时间线 tab)—— 纯机械从 game-panels.jsx 搬出,零行为变化。 */
import React from 'react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { anchorStatuses } from '../../lib/timeline-status.js';

// task 136h: 世界线收束·锚点 子组件 — 嵌入 PanelTimeline 底部
// 从 /api/saves/:id/anchors 拉取, 跟 timeline 数据互相独立。
function WorldlineAnchorsSection({ saveId, refreshKey = 0, onAnchorSatisfied }) {
  const { t } = useTranslation();
  const { useEffect, useRef } = React;
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [expandedPhase, setExpandedPhase] = useState({});
  const [satisfying, setSatisfying] = useState("");  // 正在标记的 anchor_key(禁用按钮)
  const lastFetchKey = useRef(null);

  useEffect(() => {
    if (!saveId) { setData(null); setError(""); return; }
    const fetchKey = `${saveId}:${refreshKey}`;
    if (fetchKey === lastFetchKey.current && data !== null) return;
    lastFetchKey.current = fetchKey;
    let cancelled = false;
    setLoading(true);
    setError("");
    const base = (typeof window !== "undefined" && window.__API_BASE) || "";
    fetch(`${base}/api/saves/${saveId}/anchors`, { credentials: "include" })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(json => {
        if (!cancelled) { setData(json); setLoading(false); }
      })
      .catch(e => {
        if (!cancelled) { setError(String(e?.message || e)); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, [saveId, refreshKey]);

  // FIX2: 玩家确定性推进 — 把一个非 fatal 的 pending 锚点标记为已到达。
  const markSatisfied = async (anchorKey) => {
    if (!saveId || !anchorKey || satisfying) return;
    if (!await window.__confirm({ message: t('game.timeline.satisfy_confirm'), danger: true })) return;
    setSatisfying(anchorKey);
    try {
      const base = (typeof window !== "undefined" && window.__API_BASE) || "";
      const r = await fetch(
        `${base}/api/saves/${saveId}/anchors/${encodeURIComponent(anchorKey)}/satisfy`,
        { method: "POST", credentials: "include" },
      );
      const json = await r.json().catch(() => ({}));
      if (!r.ok || !json.ok) {
        throw new Error(json.error || `HTTP ${r.status}`);
      }
      window.__apiToast?.(t('game.timeline.satisfy_ok'), { kind: "ok" });
      // 触发父级整面板刷新(剧本线高亮 + 本锚点子区都重拉)。
      if (typeof onAnchorSatisfied === "function") onAnchorSatisfied();
    } catch (e) {
      window.__apiToast?.(t('game.timeline.satisfy_failed'), { kind: "danger", detail: e?.message });
    } finally {
      setSatisfying("");
    }
  };

  if (!saveId) return null;
  if (error) {
    return (
      <div className="gp-section">
        <div className="section-head"><h3>{t('game.timeline.anchors_section')}</h3></div>
        <p style={{fontSize: 12.5, color: "var(--danger)", padding: "4px"}}>{t('game.timeline.anchors_load_failed', { error })}</p>
      </div>
    );
  }
  if (loading || data === null) {
    return (
      <div className="gp-section">
        <div className="section-head"><h3>{t('game.timeline.anchors_section')}</h3></div>
        <p className="muted-2" style={{fontSize: 12.5, padding: "4px"}}>{t('game.timeline.anchors_loading')}</p>
      </div>
    );
  }
  const summary = data.summary || {};
  const byPhase = Array.isArray(data.by_phase) ? data.by_phase : [];
  const recentPending = Array.isArray(data.recent_pending) ? data.recent_pending : [];
  const recentOccurred = Array.isArray(data.recent_occurred) ? data.recent_occurred : [];
  const total = summary.total || 0;

  if (total === 0) {
    return (
      <div className="gp-section">
        <div className="section-head"><h3>{t('game.timeline.anchors_section')}</h3></div>
        <p className="muted-2" style={{fontSize: 12.5, padding: "4px"}}>
          {t('game.timeline.anchors_empty')}
        </p>
      </div>
    );
  }

  const driftPct = Math.round((summary.avg_drift || 0) * 100);
  const driftColor = driftPct >= 60 ? "var(--danger)" : driftPct >= 30 ? "var(--warn)" : "var(--ok)";

  return (
    <>
      {/* 总览 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.timeline.anchors_section')}</h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{t('game.timeline.anchors_total', { count: total })}</span>
        </div>
        <div className="gp-kv" style={{marginBottom: 6}}>
          <div className="gp-row">
            <span className="gp-label">{t('game.timeline.convergence_overall')}</span>
            <span className="serif">
              <span style={{color: "var(--muted-2)"}}>{t('game.timeline.pending_stat')}</span>
              <strong>{summary.pending || 0}</strong>
              <span style={{color: "var(--muted-2)"}}>{t('game.timeline.occurred_stat')}</span>
              <strong style={{color: "var(--ok)"}}>{summary.occurred || 0}</strong>
              <span style={{color: "var(--muted-2)"}}>{t('game.timeline.variant_stat')}</span>
              <strong style={{color: "var(--warn)"}}>{summary.variant || 0}</strong>
              <span style={{color: "var(--muted-2)"}}>{t('game.timeline.superseded_stat')}</span>
              <strong style={{color: "var(--danger)"}}>{summary.superseded || 0}</strong>
            </span>
          </div>
          <div className="gp-row">
            <span className="gp-label">{t('game.timeline.avg_drift')}</span>
            <span className="mono" style={{color: driftColor}}>
              {(summary.avg_drift || 0).toFixed(2)} ({driftPct}%)
            </span>
          </div>
          {summary.fatal_pending > 0 && (
            <div className="gp-row">
              <span className="gp-label">{t('game.timeline.fatal_pending')}</span>
              <span className="mono" style={{color: "var(--danger)", fontWeight: 600}}>
                {t('game.timeline.fatal_pending_count', { count: summary.fatal_pending })}
              </span>
            </div>
          )}
        </div>
        {/* drift 进度条 */}
        <div style={{height: 4, background: "var(--panel-3)", borderRadius: 2, overflow: "hidden", marginBottom: 4}}>
          <div style={{width: driftPct + "%", height: "100%", background: driftColor, transition: "width 0.3s"}} />
        </div>
        <p className="muted-2" style={{fontSize: 11, margin: "4px 0 0"}}>
          {t('game.timeline.drift_hint')}
        </p>
      </div>

      {/* 按 phase 分组 */}
      {byPhase.length > 0 && (
        <div className="gp-section">
          <div className="section-head"><h3>{t('game.timeline.by_phase')}</h3></div>
          <div className="gp-track">
            {byPhase.map((ph, i) => {
              const pressure = ph.convergence_pressure || 0;
              const pressureColor = pressure >= 0.6 ? "var(--danger)" :
                                    pressure >= 0.3 ? "var(--warn)" : "var(--ok)";
              const expanded = !!expandedPhase[ph.phase_label];
              return (
                <div key={i} className="gp-anchor">
                  <div className="gp-anchor-dot" style={{background: pressureColor, border: "2px solid var(--line)"}} />
                  <div className="gp-anchor-body">
                    <div
                      className="gp-anchor-label"
                      style={{cursor: "pointer"}}
                      onClick={() => setExpandedPhase(prev => ({...prev, [ph.phase_label]: !prev[ph.phase_label]}))}
                    >
                      {ph.phase_label || t('game.timeline.no_phase')}
                      <span className="muted-2" style={{marginLeft: 6, fontSize: 10}}>
                        {t('game.timeline.convergence_label', { done: ph.occurred + ph.variant, total: ph.total })}
                      </span>
                      {ph.fatal_pending > 0 && (
                        <span className="pill" style={{marginLeft: 6, fontSize: 10, background: "var(--danger)", color: "#fff"}}>
                          {t('game.timeline.fatal_must', { count: ph.fatal_pending })}
                        </span>
                      )}
                    </div>
                    <div className="gp-anchor-phase" style={{color: "var(--muted-2)", fontSize: 11}}>
                      {t('game.timeline.drift_pressure', { drift: Number(ph.avg_drift || 0).toFixed(2), pressure: Math.round(pressure * 100) })}
                      {expanded ? " · ▲" : " · ▼"}
                    </div>
                  </div>
                  {i < byPhase.length - 1 && <div className="gp-anchor-line" />}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 待发生锚点 (top 12) */}
      {recentPending.length > 0 && (
        <div className="gp-section">
          <div className="section-head">
            <h3>{t('game.timeline.pending_anchors')}</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{t('game.timeline.top_n', { count: recentPending.length })}</span>
          </div>
          <ul className="gp-flat-list">
            {recentPending.map((a, i) => (
              <li key={"p:" + i}>
                <span>
                  <span className="mono" style={{fontSize: 10.5, color: "var(--muted-2)", marginRight: 6}}>
                    ch{a.chapter}
                  </span>
                  {a.is_fatal && (
                    <span className="pill" style={{fontSize: 10, marginRight: 4, background: "var(--danger)", color: "#fff"}}>{t('game.timeline.must_happen')}</span>
                  )}
                  {a.summary || a.anchor_key}
                </span>
                <span style={{display: "inline-flex", alignItems: "center", gap: 6}}>
                  {/* FIX2: 非 fatal pending 锚点给「标记已到达」按钮 — 玩家确定性推进。
                      fatal 锚点须在剧情里由 GM 触发,不给按钮。 */}
                  {!a.is_fatal && a.anchor_key && (
                    <button
                      className="iconbtn"
                      style={{fontSize: 10.5, padding: "2px 8px", width: "auto"}}
                      disabled={satisfying === a.anchor_key}
                      title={t('game.timeline.satisfy_title')}
                      onClick={() => markSatisfied(a.anchor_key)}
                    >
                      {satisfying === a.anchor_key ? t('game.timeline.satisfy_busy') : t('game.timeline.satisfy_btn')}
                    </button>
                  )}
                  <span className="mono" style={{fontSize: 10.5, color: "var(--muted-2)"}}>
                    imp {a.importance}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 已发生锚点 */}
      {recentOccurred.length > 0 && (
        <div className="gp-section">
          <div className="section-head">
            <h3>{t('game.timeline.occurred_anchors')}</h3>
            <span className="muted-2 mono" style={{fontSize: 11}}>{t('game.timeline.recent_n', { count: recentOccurred.length })}</span>
          </div>
          <ul className="gp-flat-list">
            {recentOccurred.map((a, i) => {
              const statusColor = a.status === "occurred" ? "var(--ok)" : "var(--warn)";
              const driftPctOne = Math.round((a.drift_score || 0) * 100);
              return (
                <li key={"o:" + i}>
                  <span>
                    <span className="mono" style={{fontSize: 10.5, color: "var(--muted-2)", marginRight: 6}}>
                      ch{a.chapter}
                    </span>
                    <span style={{color: statusColor, fontWeight: 600, marginRight: 4}}>
                      {a.status === "occurred" ? t('game.timeline.original') : t('game.timeline.variant')}
                    </span>
                    {a.summary || a.anchor_key}
                    {a.how_it_happened && (
                      <div className="muted-2" style={{fontSize: 11, marginTop: 2, paddingLeft: 12}}>
                        → {a.how_it_happened}
                      </div>
                    )}
                  </span>
                  <span className="mono" style={{fontSize: 10.5, color: statusColor}}>
                    drift {driftPctOne}%
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </>
  );
}


// task 107G: 双时间线 panel — 剧本期望线 + 实际足迹线
// 从 /api/saves/:id/timeline 按需拉取,saveId 由 state._raw.save_id 提供。
function PanelTimeline({ state }) {
  const { t } = useTranslation();
  const { useEffect, useRef } = React;
  const saveId = state && state._raw && state._raw.save_id;
  const [data, setData] = useState(null);    // null = 未加载, {} = 加载中/完毕
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [expandedPhase, setExpandedPhase] = useState({});  // {phase_index: bool}
  // 玩家「标记已到达」后刷新整个时间线面板(剧本线高亮 + 锚点子区都跟着重拉)。
  const [refreshKey, setRefreshKey] = useState(0);
  const lastFetchKey = useRef(null);

  useEffect(() => {
    if (!saveId) { setData(null); setError(""); return; }
    const fetchKey = `${saveId}:${refreshKey}`;
    if (fetchKey === lastFetchKey.current && data !== null) return;  // 已加载且无变化
    lastFetchKey.current = fetchKey;
    let cancelled = false;
    setLoading(true);
    setError("");
    // task 107G fix: 前端 5173, backend 7860 — 必须绝对 URL + credentials
    const base = (typeof window !== "undefined" && window.__API_BASE) || "";
    fetch(`${base}/api/saves/${saveId}/timeline`, { credentials: "include" })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(json => {
        if (!cancelled) { setData(json); setLoading(false); }
      })
      .catch(e => {
        if (!cancelled) { setError(String(e?.message || e)); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, [saveId, refreshKey]);

  // 回到之前的世界线节点:把进度显式设回该节点章节 + 重新上锁其后锚点。
  const [rewinding, setRewinding] = useState(false);
  const doRewind = async (targetCh, label) => {
    if (rewinding || !saveId || !(targetCh >= 1)) return;
    const body = t('game.timeline.rewind_confirm_body', { chapter: targetCh, label: label || "" });
    const ok = (typeof window !== "undefined" && typeof window.__confirm === "function")
      ? await window.__confirm({ title: t('game.timeline.rewind_confirm_title'), body, danger: true,
                                 confirmLabel: t('game.timeline.rewind_confirm_ok') })
      : window.confirm(body);
    if (!ok) return;
    setRewinding(true);
    try {
      const base = (typeof window !== "undefined" && window.__API_BASE) || "";
      const r = await fetch(`${base}/api/saves/${saveId}/progress/rewind`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_chapter: targetCh }),
      });
      const j = await r.json().catch(() => ({}));
      if (r.ok && j.ok) {
        window.__apiToast?.(t('game.timeline.rewind_ok', { chapter: targetCh }), { kind: "ok" });
        setRefreshKey(k => k + 1);
        try { window.dispatchEvent(new CustomEvent('game-state-refresh')); } catch (_) {}
      } else {
        window.__apiToast?.(t('game.timeline.rewind_failed'), { kind: "danger", detail: j.error });
      }
    } catch (e) {
      window.__apiToast?.(t('game.timeline.rewind_failed'), { kind: "danger", detail: e?.message });
    } finally {
      setRewinding(false);
    }
  };

  if (!saveId) {
    return (
      <div className="gp-stack">
        <div className="gp-section">
          <p className="muted-2" style={{fontSize: 12.5, padding: "12px 4px"}}>
            {t('game.timeline.no_save')}
          </p>
        </div>
      </div>
    );
  }

  // task 107G fix: error 检查提前于 loading, 否则 fetch 失败 + data===null 永远显示加载中
  if (error) {
    return (
      <div className="gp-stack">
        <div className="gp-section">
          <p style={{fontSize: 12.5, color: "var(--danger)", padding: "12px 4px"}}>
            {t('game.timeline.load_failed', { error })}
          </p>
          <p className="muted-2" style={{fontSize: 11.5, padding: "0 4px"}}>
            {t('game.timeline.load_failed_hint')}
          </p>
        </div>
      </div>
    );
  }

  if (loading || data === null) {
    return (
      <div className="gp-stack">
        <div className="gp-section">
          <p className="muted-2" style={{fontSize: 12.5, padding: "12px 4px"}}>{t('game.timeline.loading')}</p>
        </div>
      </div>
    );
  }

  const scriptAnchors = Array.isArray(data.script_anchors) ? data.script_anchors : [];
  const savePhases   = Array.isArray(data.save_phases)    ? data.save_phases    : [];
  const currentPhaseIndex = data.current_phase_index ?? 0;  // 兼容字段,不再用于高亮判定
  // FIX1: 高亮按真实剧情章节(current_chapter),不再用 active_phase_index(恒卡 0)。
  const currentChapter = data.current_chapter ?? 1;
  // FIX5: 全线状态一次算好(唯一「当前」由共享缝裁定,见 lib/timeline-status.js)
  const anchorStatus_ = anchorStatuses(scriptAnchors, currentChapter);

  return (
    <div className="gp-stack">
      {/* 剧本期望线 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.timeline.expected')}</h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{t('game.timeline.anchors_count', { count: scriptAnchors.length })}</span>
        </div>
        {scriptAnchors.length === 0 ? (
          <p className="muted-2" style={{fontSize: 12.5, margin: "4px 0 0"}}>{t('game.timeline.no_anchors')}</p>
        ) : (
          <div className="gp-track">
            {scriptAnchors.map((a, i) => {
              // FIX1: 状态按真实章节区间判定(确定性),不再用序列下标对 active_phase_index。
              // FIX5(群反馈「世界线有三个当前」):锚点区间**允许嵌套重叠**(生产 script 322
              //   第 100 章同时落在 [1,292]/[43,283]/[57,128]/[100,107] 四条里),逐条独立判
              //   「区间包含即当前」就会并排挂出多个「当前」。判定收敛到共享缝 anchorStatuses:
              //   包含当前章的锚点里只有最贴切(最窄)的一条算「当前」,其余标「进行中」。
              //   移动端 mobile/game/panels.jsx 用同一个缝,两端不会再各判各的。
              const st = anchorStatus_[i];
              const chMin = a.chapter_min;
              const chMax = a.chapter_max != null ? a.chapter_max : a.chapter_min;
              const isDone    = st === 'done';
              const isCurrent = st === 'current';
              const isOngoing = st === 'ongoing';
              const isPending = st === 'pending';
              // FIX4: 主标题用 story_time_label(场景/章名);story_phase(开端…)降为弱副标,
              //   连续同 phase 只在该组首条显示一次。
              const phase = a.phase_label || "";
              const prevPhase = i > 0 ? (scriptAnchors[i - 1].phase_label || "") : null;
              const showPhaseGroup = phase && phase !== prevPhase;
              const mainTitle = a.story_time_label
                || a.phase_label
                || (chMin != null ? t('game.timeline.chapter_label', { chapter: chMin }) : "");
              return (
                <div
                  key={i}
                  className={`gp-anchor ${isCurrent ? "current" : ""} ${isDone ? "done" : ""} ${isOngoing ? "ongoing" : ""} ${isPending ? "pending" : ""}`}
                >
                  {/* ongoing = 跨度更大、把当前章包在里面的外层弧:不实心高亮(那是「当前」专属),
                      但也不像未解锁那样打灰 —— 它确实正在进行中。 */}
                  <div className="gp-anchor-dot" style={{
                    background: isDone ? "var(--ok)" : isCurrent ? "var(--accent)" : "var(--panel-3)",
                    border: isCurrent ? "2px solid var(--accent)"
                      : isOngoing ? "2px solid var(--accent-edge, var(--accent))" : "2px solid var(--line)",
                  }} />
                  <div className="gp-anchor-body">
                    {showPhaseGroup && (
                      <div className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 2}}>
                        {phase}
                      </div>
                    )}
                    <div className="gp-anchor-label" style={{
                      color: isPending ? "var(--muted-2)" : undefined,
                      fontWeight: isCurrent ? 600 : undefined,
                    }}>
                      {mainTitle}
                      {isCurrent && <span className="pill" style={{marginLeft: 6, fontSize: 10, background: "var(--accent)", color: "#fff"}}>{t('game.timeline.current_pill')}</span>}
                      {isOngoing && <span className="muted-2" style={{marginLeft: 6, fontSize: 10}}>{t('game.timeline.in_progress_pill')}</span>}
                      {isDone && <span className="muted-2" style={{marginLeft: 6, fontSize: 10}}>{t('game.timeline.done_label')}</span>}
                      {isPending && <span className="muted-2" style={{marginLeft: 6, fontSize: 10}}>{t('game.timeline.pending_label')}</span>}
                    </div>
                    <div className="gp-anchor-phase" style={{color: "var(--muted-2)"}}>
                      {chMin != null
                        ? `${t('game.timeline.chapter_label', { chapter: chMin })}${chMax != null && chMax !== chMin ? `–${chMax}` : ""}`
                        : ""}
                    </div>
                    {isDone && chMin != null && (
                      <button
                        className="gp-anchor-rewind"
                        disabled={rewinding}
                        onClick={() => doRewind(chMin, mainTitle)}
                        title={t('game.timeline.rewind_btn')}
                        style={{ marginTop: 3, fontSize: 10.5, background: "none",
                                 border: "1px solid var(--line)", borderRadius: 4, padding: "1px 7px",
                                 color: "var(--muted-2)", cursor: rewinding ? "default" : "pointer" }}>
                        {t('game.timeline.rewind_btn')}
                      </button>
                    )}
                  </div>
                  {i < scriptAnchors.length - 1 && <div className="gp-anchor-line" />}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 实际足迹线 */}
      <div className="gp-section">
        <div className="section-head">
          <h3>{t('game.timeline.footprint')}</h3>
          <span className="muted-2 mono" style={{fontSize: 11}}>{t('game.timeline.phases_count', { count: savePhases.length })}</span>
        </div>
        {savePhases.length === 0 ? (
          <p className="muted-2" style={{fontSize: 12.5, margin: "4px 0 0"}}>
            {t('game.timeline.no_footprint')}
          </p>
        ) : (
          <div className="gp-track">
            {savePhases.map((ph, i) => {
              const isOpen    = ph.status === "open";
              const isCurrent = ph.phase_index === currentPhaseIndex && isOpen;
              const expanded  = !!expandedPhase[ph.phase_index];
              const keyEvents = Array.isArray(ph.key_events) ? ph.key_events : [];
              return (
                <div key={ph.phase_index}
                  className={`gp-anchor ${isCurrent ? "current" : ""}`}
                  style={{cursor: keyEvents.length ? "pointer" : undefined}}
                  onClick={() => keyEvents.length && setExpandedPhase(s => ({...s, [ph.phase_index]: !s[ph.phase_index]}))}
                >
                  <div className="gp-anchor-dot" style={{
                    background: isCurrent ? "var(--accent)" : "var(--ok)",
                    border: isCurrent ? "2px solid var(--accent)" : "2px solid var(--ok)",
                  }} />
                  <div className="gp-anchor-body">
                    <div className="gp-anchor-label" style={{fontWeight: isCurrent ? 600 : undefined}}>
                      <span className="muted-2 mono" style={{fontSize: 11, marginRight: 6}}>
                        Phase {ph.phase_index}
                      </span>
                      {ph.phase_label || `(turn ${ph.turn_start}–${isOpen ? "…" : ph.turn_end})`}
                      {isCurrent && <span className="pill" style={{marginLeft: 6, fontSize: 10, background: "var(--accent)", color: "#fff"}}>{t('game.timeline.in_progress_pill')}</span>}
                    </div>
                    <div className="gp-anchor-phase" style={{color: "var(--muted-2)"}}>
                      {`turn ${ph.turn_start}–${isOpen ? "…" : ph.turn_end}`}
                      {ph.story_time_label ? ` · ${ph.story_time_label}` : ""}
                    </div>
                    {ph.summary ? (
                      <p className="gp-bio" style={{marginTop: 4, fontSize: 12}}>{ph.summary}</p>
                    ) : null}
                    {expanded && keyEvents.length > 0 && (
                      <ul className="gp-flat-list" style={{marginTop: 4}}>
                        {keyEvents.map((ev, ei) => {
                          const evText = typeof ev === "string" ? ev
                            : (ev && (ev.summary || ev.text || ev.label || JSON.stringify(ev)));
                          const evTurn = ev && ev.turn != null ? `turn ${ev.turn}` : "";
                          return (
                            <li key={ei}>
                              <span>{evText}</span>
                              {evTurn && <span className="muted-2 mono" style={{fontSize: 10.5}}>{evTurn}</span>}
                            </li>
                          );
                        })}
                      </ul>
                    )}
                    {keyEvents.length > 0 && (
                      <div className="muted-2" style={{fontSize: 10.5, marginTop: 2, cursor: "pointer"}}>
                        {expanded ? t('game.timeline.collapse_events') : t('game.timeline.expand_events', { count: keyEvents.length })}
                      </div>
                    )}
                  </div>
                  {i < savePhases.length - 1 && <div className="gp-anchor-line" />}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* task 136h: 世界线收束·锚点 */}
      <WorldlineAnchorsSection
        saveId={saveId}
        refreshKey={refreshKey}
        onAnchorSatisfied={() => setRefreshKey(k => k + 1)}
      />
    </div>
  );
}

export { WorldlineAnchorsSection, PanelTimeline };
