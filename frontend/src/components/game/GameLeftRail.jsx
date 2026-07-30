/* Game Console 左栏 + 运行状态簇(LeftRail / RunSteps* / ThinkingPill /
   RunStateSection / RunDetailRail / BranchTreeRail)—— 纯机械从 game-app.jsx
   搬出,DOM/视觉/行为零变化。 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { useState as useStateA, useEffect as useEffectA } from 'react';
import { Icon } from '../../game-icons.jsx';
import { BranchGraph } from '../../branch-graph.jsx';

// ----------------------------- LEFT RAIL ---------------------------------
function LeftRail({ collapsed, onToggle, state, runState, onNew, onSave, onSwitchSave, currentSaveId, saves, resizeHandle, mobileOpen }) {
  // task 102E: resizeHandle 是 React 节点 (一般是 <ResizeHandle />),
  // 由 App 层注入,放在 <aside> 内绝对定位
  const { t } = useTranslation();
  const m = state.memory || { mode: "normal" };
  const [branchOpen, setBranchOpen] = useStateA(false);
  return (
    <aside className={`gc-rail ${collapsed ? "collapsed" : ""} ${mobileOpen ? "gc-rail-mobile-open" : ""}`} aria-hidden={collapsed && !mobileOpen}>
      {!collapsed && resizeHandle}
      <div className="gc-rail-inner">
      <div className="gc-rail-head">
        <div className="gc-brand">
          <div className="gc-brand-mark"><Icon name="logo" size={14} /></div>
          <div className="gc-brand-text">
            {/* task 45：剧本名/阶段从真实 state 派生。已登录态加载中不再退到 MOCK_NOVEL，
                避免首屏慢半拍闪出 designer 示例小说名。 */}
            <strong>{(() => {
              const realTitle = state && (state._raw?.save_title || state.app?.title);
              const allowMockTitle = !(window.RPG_AUTH && window.RPG_AUTH.authed);
              return realTitle || (allowMockTitle && window.MOCK_NOVEL && window.MOCK_NOVEL.script_title) || "RPG Roleplay";
            })()}</strong>
            <span className="muted-2" style={{ fontSize: 11 }}>RPG Roleplay · {(state && state.world && state.world.timeline && state.world.timeline.current_phase) || "—"}</span>
          </div>
        </div>
        <button className="iconbtn" onClick={onToggle} data-tip={t('game.app.rail.collapse_tip')} data-tip-pos="below" aria-label={t('game.app.rail.collapse_tip')}>
          <Icon name="chevron_left" size={14} />
        </button>
      </div>

      <div className="gc-rail-section">
        <div className="gc-rail-section-head">
          <span>{t('game.app.rail.current_save')}</span>
          <button className="iconbtn" data-tip={t('game.app.rail.new_game_tip')} onClick={onNew} aria-label={t('game.app.rail.new_game_tip')}><Icon name="plus" size={12} /></button>
        </div>
        <div className="gc-rail-save-display">
          {(() => {
            // task 10：先按 currentSaveId 命中真实 saves；命中不到再退到 saves 第一条；
            // saves 列表为空才显示「尚未创建存档」并引导新游戏。
            const cur = (Array.isArray(saves) ? saves : []).find(s => s.id === currentSaveId)
              || (Array.isArray(saves) && saves.length ? saves[0] : null);
            if (!cur) {
              return (
                <>
                  <strong className="muted">{t('game.app.rail.no_save')}</strong>
                  <span className="muted-2 mono" style={{fontSize: 11}}>{t('game.app.rail.no_save_hint')}</span>
                </>
              );
            }
            return (
              <>
                <strong>{cur.title || t('game.app.rail.save_label', { id: cur.id })}</strong>
                <span className="muted-2 mono">{cur.updated_at || ""}</span>
              </>
            );
          })()}
        </div>
        <div className="gc-rail-quick">
          <button className="btn ghost" onClick={onSave} data-tip={t('game.app.rail.manual_save_tip')}><Icon name="save" size={12} /> {t('common.save')}</button>
          <button className="btn ghost" onClick={() => setBranchOpen(o => !o)} data-tip={t('game.app.rail.branch_tip')} aria-label={t('game.app.rail.branch_tip')}><Icon name="branch" size={12} /> {t('game.app.rail.branch_btn')}</button>
        </div>
        {/* task 48：传 currentSaveId / state._raw.save_id，BranchTreeRail 走真 /api/branches */}
        {branchOpen && <BranchTreeRail saveId={currentSaveId || state?._raw?.save_id || null} />}
      </div>

      {/* 「记忆模式」选择器已整块移除(v1.77.0)。它是**装饰性**的:全仓没有任何一处
          据 memory.mode 改变行为(MemoryProvider 只把它塞进 debug 字段),真正决定召回量的是
          MemorySettings.recall_depth / token_budget —— 那套在「设置 → 记忆」里有完整 UI,
          还带三个分桶开关(pinned/world/character),正是这里的「关闭」想做却没做到的事。
          而且提示文案「每轮召回 6 段 / 14 段」这两个数字在代码里根本不存在。
          症状(群反馈):点「关闭」自动弹回普通 —— 后端白名单只有 concise/normal/deep,
          off 被静默丢弃,前端的空 catch 又把 400 吞了。删掉假旋钮,不再骗人。 */}

      {/* task 48：原硬编码两行『memory.facts +1: 童氏与南陵同源』『relationships.沈知微.tone +』。
          改为读 state.memory.last_structured_updates；空就空态。 */}
      {(() => {
        const updates = Array.isArray(state?.memory?.last_structured_updates) ? state.memory.last_structured_updates : [];
        return (
          <div className="gc-rail-section compact">
            <div className="gc-rail-section-head"><span>{t('game.app.rail.structured_updates')}</span><span className="pill mono">{updates.length}</span></div>
            <ul className="gc-rail-updates">
              {updates.length === 0 && (
                <li><span className="muted-2" style={{fontSize: 11.5}}>{t('game.app.rail.no_updates')}</span></li>
              )}
              {updates.slice(-6).map((u, i) => {
                const text = typeof u === "string" ? u : (u?.text || JSON.stringify(u));
                // 把 "状态写入：path=value" 这种形态切成 field + value 显示
                const m = String(text).match(/^([^：:]+)[：:](.+)$/);
                return (
                  <li key={i} title={text}>
                    <span className="dot accent" />
                    <span className="mono gc-rail-field">{m ? m[1] : text}</span>
                    {m && <span className="muted-2">{m[2].slice(0, 20)}{m[2].length > 20 ? "…" : ""}</span>}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })()}

      <div className="gc-rail-spacer" />

      {/* task 129 + 141: 运行详情默认隐藏,只在 running 时自动展开,
          空闲时折叠;用户点"空闲·等待玩家"行可手动 toggle 看上一轮历史。 */}
      <RunStateSection runState={runState} />

      <div className="gc-rail-foot">
        {/* task 37：CSS 已把 a 改成 inline-flex 占满 foot，icon 的 verticalAlign/marginRight
            可以删掉，避免和 flex align-items 打架（之前是这个让 SVG 视觉外溢、点击命中
            落到父 div，触发 'gc-rail-foot intercepts pointer events'）。 */}
        <a href="Platform.html" className="muted" data-tip={t('game.app.rail.back_home_tip')} style={{ fontSize: 12, borderBottom: "0" }}>
          <Icon name="home" size={12} />
          {t('game.app.rail.back_home')}
        </a>
      </div>
      </div>
    </aside>
  );
}

// ----------------------------- RUN STEPS ---------------------------------
function RunStepsLine({ steps }) {
  const { t } = useTranslation();
  return (
    <div className="gc-run gc-run-line">
      {steps.map((s, i) =>
      <div key={i} className={`gc-run-line-row ${s.status}`}>
          <span className={`gc-run-dot ${s.status}`} />
          <span className="gc-run-label">{s.message}</span>
          <span className="muted-2 mono gc-run-elapsed">{(s.elapsed_ms / 1000).toFixed(1)}s</span>
          {s.detail && s.status === "done" &&
        <details className="gc-run-detail">
              <summary className="muted-2"><Icon name="chevron_down" size={10} /> {t('game.app.run.expand')}</summary>
              <div className="muted">{s.detail}</div>
            </details>
        }
        </div>
      )}
    </div>);

}

function RunStepsCard({ steps }) {
  return (
    <div className="gc-run gc-run-cards">
      {steps.map((s, i) =>
      <div key={i} className={`gc-run-card ${s.status}`}>
          <div className="gc-run-card-head">
            <span className={`gc-run-dot ${s.status}`} />
            <span className="gc-run-card-title">{s.message}</span>
            <span className="muted-2 mono">{(s.elapsed_ms / 1000).toFixed(1)}s</span>
          </div>
          {s.detail && <div className="gc-run-card-detail muted" style={{ fontSize: 12.5 }}>{s.detail}</div>}
        </div>
      )}
    </div>);

}

function RunStepsTimeline({ steps }) {
  return (
    <div className="gc-run gc-run-timeline">
      {steps.map((s, i) =>
      <div key={i} className={`gc-run-tl-row ${s.status}`}>
          <div className="gc-run-tl-rail">
            <span className={`gc-run-dot ${s.status}`} />
            {i < steps.length - 1 && <span className="gc-run-tl-line" />}
          </div>
          <div className="gc-run-tl-body">
            <div className="gc-run-tl-title">
              <span>{s.message}</span>
              <span className="muted-2 mono">{(s.elapsed_ms / 1000).toFixed(1)}s</span>
            </div>
            {s.detail && <div className="muted gc-run-tl-detail">{s.detail}</div>}
          </div>
        </div>
      )}
    </div>);

}

function RunSteps({ steps, style }) {
  if (!steps?.length) return null;
  if (style === "cards") return <RunStepsCard steps={steps} />;
  if (style === "timeline") return <RunStepsTimeline steps={steps} />;
  return <RunStepsLine steps={steps} />;
}

// ----------------------------- THINKING PILL -----------------------------
// task 92：把后端 agent SSE 事件展示成一行 Codex 风格的"高层思考状态"。
// 玩家只看到 4 段易懂进度（context→rules→gm→save），完成后短暂显示「已完成 · X.Xs」
// 再自动收起。完整 raw phase 流（prompt/intent/llm_curator/manifest/provider:*/assembly
// /rules_engine/main_gm/acceptance_check ...）藏在「详情」折叠里，要看时再展开，
// 不会再铺满聊天区。
// Stage labels resolved via t() inside ThinkingPill — these keys are referenced there.
const PUBLIC_STAGE_KEYS = {
  context: "game.app.thinking.stage_context",
  rules:   "game.app.thinking.stage_context",
  gm:      "game.app.thinking.stage_gm",
  save:    "game.app.thinking.stage_save",
  system:  "game.app.thinking.stage_context",
};
// stage → 0-100% for progress ring (context/rules=25%, gm=60%, save=90%, done=100%)
const PUBLIC_STAGE_PCT = {
  context: 25,
  rules:   45,
  gm:      70,
  save:    90,
  system:  20,
};

// task 64: ThinkingPill — SVG 圆环 + 百分比 + 简短文案
function ThinkingPill({ runState, runStyle }) {
  const { t } = useTranslation();
  const running = !!runState?.running;
  const completedAt = runState?.completedAt || 0;
  const showCompleted = !running && completedAt > 0;
  if (!running && !showCompleted) return null;

  const stageId = runState?.publicStage || "system";
  const label = running
    ? t(PUBLIC_STAGE_KEYS[stageId] || PUBLIC_STAGE_KEYS.system)
    : t('game.app.thinking.done');
  const elapsedMs = running ? (runState?.totalElapsed || 0) : (runState?.completedElapsed || 0);
  const elapsedSec = (elapsedMs / 1000).toFixed(1);
  const pct = running ? (PUBLIC_STAGE_PCT[stageId] || 20) : 100;

  // SVG ring: r=9 → circumference ≈ 56.5
  const R = 9;
  const C = 2 * Math.PI * R;
  const dash = (pct / 100) * C;

  return (
    <div className={`gc-think ${running ? "running" : "done"}`}
         aria-live="polite" aria-busy={running}>
      <div className="gc-think-row">
        <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"
             style={{ flexShrink: 0, transform: "rotate(-90deg)" }}>
          {/* track */}
          <circle cx="11" cy="11" r={R}
            fill="none"
            stroke="rgba(201,100,66,0.22)"
            strokeWidth="2.5" />
          {/* progress */}
          <circle cx="11" cy="11" r={R}
            fill="none"
            stroke="var(--accent, #c96442)"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeDasharray={`${dash} ${C}`}
            style={{ transition: "stroke-dasharray 0.4s ease" }} />
        </svg>
        <span className="gc-think-pct mono" style={{ fontSize: 11, minWidth: "2.4em", textAlign: "right", opacity: 0.75 }}>{pct}%</span>
        <span className="gc-think-label">{label}</span>
        <span className="gc-think-elapsed mono muted-2">{elapsedSec}s</span>
      </div>
    </div>
  );
}

// task 141: 合一组件 = 状态行 + 详情列表。
// running 时:展示状态 + 自动展开 rawSteps。
// 空闲(完成 1.8s 后):rawSteps 已被清空 → 只剩"空闲·等待玩家"行,点行可展开
// 暂存的 rawSteps(虽然此时为空,但 UI 保留 toggle 一致体验)。
function RunStateSection({ runState }) {
  const { t } = useTranslation();
  const running = !!runState?.running;
  const rawSteps = Array.isArray(runState?.rawSteps) ? runState.rawSteps : [];
  // running 时强制展开;空闲时默认折叠
  const [manualExpanded, setManualExpanded] = useStateA(false);
  const expanded = running || manualExpanded;
  const canToggle = !running && rawSteps.length > 0;
  return (
    <div className="gc-rail-section compact">
      <div className="gc-rail-runstate"
        onClick={canToggle ? () => setManualExpanded((v) => !v) : undefined}
        style={canToggle ? { cursor: "pointer" } : undefined}
        title={canToggle ? t('game.app.run.click_last_detail') : undefined}
      >
        <div className="gc-rail-runstate-line">
          <span className={`dot ${running ? "accent pulse" : "ok"}`} style={{ marginRight: 6 }} />
          {running ? <span>{runState.label}</span> :
            <span className="muted">
              {t('game.app.run.idle')}
              {rawSteps.length > 0 && (
                <span className="muted-2" style={{ marginLeft: 8, fontSize: 10.5 }}>
                  {manualExpanded ? "▾" : "▸"} {t('game.app.run.last_detail')}
                </span>
              )}
            </span>
          }
        </div>
        {running && <div className="gc-rail-runstate-detail muted-2 mono">{runState.detail}</div>}
      </div>
      {expanded && rawSteps.length > 0 && <RunDetailRail runState={runState} />}
    </div>
  );
}


// task 129: LeftRail 显示运行详情 (raw phase trace),Claude 同款的展开视图但放左侧
// task 141: 不再自带 gc-rail-section 容器,由父 RunStateSection 控制是否展示
function RunDetailRail({ runState }) {
  const { t } = useTranslation();
  const rawSteps = Array.isArray(runState?.rawSteps) ? runState.rawSteps : [];
  const [expanded, setExpanded] = useStateA(false);
  if (!rawSteps.length) return null;
  const visible = expanded ? rawSteps : rawSteps.slice(-6); // 默认只显示最新 6 步
  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--line-soft, rgba(255,255,255,.06))" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
        <span className="muted-2" style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em" }}>
          {t('game.app.run.detail_title')}
        </span>
        {rawSteps.length > 6 && (
          <button className="iconbtn" style={{ padding: "2px 8px", fontSize: 10.5, whiteSpace: "nowrap", width: "auto", height: "auto" }}
            onClick={(e) => { e.stopPropagation(); setExpanded(v => !v); }}
            aria-label={expanded ? t('game.app.run.collapse') : t('game.app.run.show_all', { count: rawSteps.length })}>
            {expanded ? t('game.app.run.collapse') : t('game.app.run.show_all', { count: rawSteps.length })}
          </button>
        )}
      </div>
      <div style={{ maxHeight: expanded ? "60vh" : "auto", overflowY: "auto", display: "grid", gap: 3 }}>
        {visible.map((step, i) => {
          const msg = step.message || step.label || step.phase || step.type || "step";
          const status = step.status || (step.completedAt ? "done" : (step.startedAt ? "running" : ""));
          const elapsed = step.elapsedMs != null ? (step.elapsedMs / 1000).toFixed(1) + "s" : "";
          return (
            <div key={i} style={{ display: "flex", gap: 6, alignItems: "baseline", fontSize: 11, lineHeight: 1.45 }}>
              <span className={`dot ${status === "running" ? "accent pulse" : status === "error" ? "danger" : "ok"}`}
                style={{ marginTop: 5 }} />
              <span className="muted-2" style={{ flex: 1, wordBreak: "break-word" }}>{msg}</span>
              {elapsed && <span className="mono muted-2" style={{ fontSize: 10 }}>{elapsed}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// VSCode-style branch tree inline in the rail
// 用户要求"一个存档一个 git 系统",UI 一模一样 VSCode Git Graph。
// 后端已经是完整 git 语义 (branch_commits + branch_refs + parent_id 树),
// 前端这里只做 wrapper:拉 /api/branches/{saveId},喂给 BranchGraph 组件
// (variant="compact" 紧凑型,适合右侧栏)。
function BranchTreeRail({ saveId }) {
  const { t } = useTranslation();
  const [data, setData] = useStateA({ loading: false, payload: null, error: "" });
  const [refreshTick, setRefreshTick] = useStateA(0);
  useEffectA(() => {
    const onReload = () => setRefreshTick(t => t + 1);
    window.addEventListener("rpg-state-reload", onReload);
    window.addEventListener("rpg-saves-updated", onReload);
    return () => {
      window.removeEventListener("rpg-state-reload", onReload);
      window.removeEventListener("rpg-saves-updated", onReload);
    };
  }, []);
  useEffectA(() => {
    if (!saveId) { setData({ loading: false, payload: null, error: "" }); return; }
    let cancelled = false;
    setData(d => ({ ...d, loading: true, error: "" }));
    (async () => {
      try {
        const r = await window.api.branches.list(saveId);
        if (cancelled) return;
        // 后端返回 {nodes, refs, active_commit_id, ...}。BranchGraph 直接消费。
        // 兼容老字段:r.commits → r.nodes
        const payload = r ? {
          nodes: r.nodes || r.commits || [],
          refs: r.refs || [],
          active_commit_id: r.active_commit_id || r.active_branch_node_id || null,
        } : null;
        setData({ loading: false, payload, error: "" });
      } catch (e) {
        if (!cancelled) setData({ loading: false, payload: null, error: e?.message || t('game.app.branch.load_failed') });
      }
    })();
    return () => { cancelled = true; };
  }, [saveId, refreshTick]);
  const nodes = (data.payload && data.payload.nodes) || [];
  return (
    <div className="gc-rail-branch-tree">
      <div className="gc-rail-branch-head">
        <span className="muted-2 mono" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em"}}>{t('game.app.branch.current_branches')}</span>
        <span className="muted-2 mono" style={{fontSize: 10.5, marginLeft: "auto"}}>{t('game.app.branch.head_history')}</span>
        <a className="iconbtn" href="/saves-branches"
           target="_blank" rel="noopener noreferrer"
           data-tip={t('game.app.branch.open_full_tip')} data-tip-pos="below"
           aria-label={t('game.app.branch.open_full_tip')}
           style={{width: 18, height: 18}}>
          <Icon name="arrow_right" size={10} />
        </a>
      </div>
      {data.loading && <div className="muted-2" style={{padding: "10px 8px", fontSize: 11.5}}>{t('common.loading')}</div>}
      {!data.loading && data.error && (
        <div className="muted-2" style={{padding: "10px 8px", fontSize: 11.5}}>{t('game.app.branch.error_prefix')}{data.error}</div>
      )}
      {!data.loading && !data.error && data.payload && (
        <BranchGraph
          data={data.payload}
          variant="compact"
          // Codex P0 三连修复:游戏内分支图必须能切分支 / 从某节点继续。
          // 之前没传 callback,BranchGraph 默认隐藏按钮 → 用户报"什么都没发生"。
          // 调用后端 activate / continueFrom 后 dispatch rpg-state-reload,
          // 让 Game Console 重新拉 /api/state (现在 _ensure_loaded 已加
          // save_id 一致性自检,会自动 reload 到新 commit)。
          onActivate={async (commitId) => {
            try {
              const r = await window.api.branches.activate({ node_id: commitId, commit_id: commitId });
              if (r && r.ok === false) throw new Error(r.error || r.detail || t('game.app.branch.switch_failed'));
              window.__apiToast?.(t('game.app.branch.switched'), { kind: "ok", duration: 1500 });
              window.dispatchEvent(new CustomEvent("rpg-state-reload"));
              window.dispatchEvent(new CustomEvent("rpg-saves-updated"));
            } catch (e) {
              window.__apiToast?.(t('game.app.branch.switch_failed'), { kind: "danger", detail: e?.message || String(e) });
            }
          }}
          onContinue={async (commitId) => {
            try {
              const r = await window.api.branches.continueFrom({ node_id: commitId });
              if (r && r.ok === false) throw new Error(r.error || r.detail || t('game.app.branch.continue_failed'));
              window.__apiToast?.(t('game.app.branch.continued'), { kind: "ok", duration: 1500 });
              window.dispatchEvent(new CustomEvent("rpg-state-reload"));
              window.dispatchEvent(new CustomEvent("rpg-saves-updated"));
            } catch (e) {
              window.__apiToast?.(t('game.app.branch.continue_failed'), { kind: "danger", detail: e?.message || String(e) });
            }
          }}
        />
      )}
    </div>
  );
}

export { LeftRail, RunSteps, ThinkingPill };
