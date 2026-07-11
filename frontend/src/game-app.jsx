/* Game Console — main app shell: top bar, left rail, chat area with run-state, right panel. */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { createPortal } from 'react-dom';
import { useState as useStateA, useEffect as useEffectA, useRef as useRefA, useMemo as useMemoA, useCallback as useCallbackA } from 'react';
import { Icon } from './game-icons.jsx';
import Modal from './components/Modal.jsx';
import { RpgMarkdown } from './markdown-render.jsx';
import { BranchGraph } from './branch-graph.jsx';
import { useBreakpoint, useResizable, ResizeHandle } from './responsive.jsx';
import { stripNarrativeOps } from './narrative-strip.js';
import AvatarImg from './components/AvatarImg.jsx';
import { useStickToBottom } from './hooks/useStickToBottom.js';
import { createToastChannel } from './toast.jsx';
import { lsGet, lsSet, lsGetJSON, lsSetJSON } from './lib/storage.js';
import { ToolCallBlock } from './components/ToolCallBlock.jsx';

// ----------------------------- LEFT RAIL ---------------------------------
function LeftRail({ collapsed, onToggle, state, runState, onNew, onSave, onSwitchSave, onMemoryMode, currentSaveId, saves, resizeHandle, mobileOpen }) {
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

      <div className="gc-rail-section">
        <div className="gc-rail-section-head"><span>{t('game.app.rail.memory_mode')}</span></div>
        <div className="seg gc-mem-seg">
          <button className={m.mode === "normal" ? "active" : ""} data-tip={t('game.app.rail.memory_normal_tip')} onClick={() => onMemoryMode?.("normal")} aria-label={t('game.app.rail.memory_normal_tip')}>
            <Icon name="memory" /> {t('game.app.rail.memory_normal')}
          </button>
          <button className={m.mode === "deep" ? "active" : ""} data-tip={t('game.app.rail.memory_deep_tip')} onClick={() => onMemoryMode?.("deep")} aria-label={t('game.app.rail.memory_deep_tip')}>
            <Icon name="sparkle" /> {t('game.app.rail.memory_deep')}
          </button>
          <button className={m.mode === "off" ? "active" : ""} data-tip={t('game.app.rail.memory_off_tip')} onClick={() => onMemoryMode?.("off")} aria-label={t('game.app.rail.memory_off_tip')}>
            <Icon name="eye_off" /> {t('common.close')}
          </button>
        </div>
        <p className="gc-mem-desc">
          {m.mode === "deep" ? <><strong>{t('game.app.rail.memory_deep')}</strong> · {t('game.app.rail.memory_deep_desc')}</>
            : m.mode === "off" ? <><strong>{t('common.close')}</strong> · {t('game.app.rail.memory_off_desc')}</>
            : <><strong>{t('game.app.rail.memory_normal')}</strong> · {t('game.app.rail.memory_normal_desc')}</>}
        </p>
      </div>

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

// ----------------------------- CHAT --------------------------------------
function MsgActions({ text, ts, msgIndex, totalMsgs, commitId, saveId, role, meta, memoryText }) {
  // task 38：以前 msgIndex / saveId / commitId 全是 undefined，doFork 就发
  // {label} 给后端 → 后端 int(None) 直接 500。现在 NarrativeBlock / PlayerBlock
  // 把 idx + saveId 透传进来，doFork 至少发 {save_id, message_index, label}，
  // 后端通过 resolve_commit_id_by_message 解析。
  const { t } = useTranslation();
  const [copied, setCopied] = useStateA(false);
  const [forkOpen, setForkOpen] = useStateA(false);
  // task 116c: 删除消息 (软回滚) — 弹窗确认 + 进度
  const [delOpen, setDelOpen] = useStateA(false);
  const [delBusy, setDelBusy] = useStateA(false);

  const [copyWithMemory, setCopyWithMemory] = useStateA(false);
  const [copyAskOpen, setCopyAskOpen] = useStateA(false);
  const hasMemory = !!(memoryText && memoryText.trim());
  const doCopy = async (includeMemory) => {
    const txt = includeMemory && hasMemory ? memoryText.trim() + "\n\n---\n\n" + (text || "") : (text || "");
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(txt);
        ok = true;
      }
    } catch (e) {}
    if (!ok) {
      try {
        const ta = document.createElement("textarea");
        ta.value = txt;
        ta.style.position = "fixed";
        ta.style.top = "-1000px";
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand("copy");
        document.body.removeChild(ta);
      } catch (e) {}
    }
    setCopied(true);
    if (window.toast) {
      if (ok) window.toast(t('game.app.msg.copied'), { kind: "ok", detail: txt.slice(0, 40) + (txt.length > 40 ? "…" : ""), duration: 1600 });
      else window.toast(t('game.app.msg.copy_failed'), { kind: "danger", detail: t('game.app.msg.clipboard_denied'), duration: 2400 });
    }
    setTimeout(() => setCopied(false), 1400);
    setCopyAskOpen(false);
  };
  const onCopy = () => {
    if (hasMemory) {
      setCopyAskOpen(true);
    } else {
      doCopy(false);
    }
  };
  // task 38：禁用条件——必须有 saveId 或 commitId 之一，否则后端无法定位 commit。
  // 缺信息时按钮 disabled + tooltip 解释，比让用户点进去看 toast 失败强。
  const canFork = (commitId != null && commitId !== "") || (saveId != null && msgIndex != null);
  const onFork = () => {
    if (!canFork) {
      window.toast?.(t('game.app.msg.fork_failed'), {
        kind: "warn",
        detail: t('game.app.msg.fork_no_ctx'),
        duration: 2400,
      });
      return;
    }
    setForkOpen(true);
  };
  // 反馈:每条消息加「重新生成这一轮」快捷按钮(就在分支按钮边上)。
  // 实际逻辑在 game-console 顶层 onRegenerate:fork 到本轮之前(复用 resolve_commit_id_by_message)
  // → 截断历史 → 用同样的玩家输入重走完整 GM 流程。这里只派发事件(避免 prop 一路透传)。
  const canRegen = saveId != null && msgIndex != null && msgIndex >= 0;
  const onRegenerate = () => {
    if (!canRegen) {
      window.toast?.(t('game.app.msg.regen_failed'), { kind: "warn", detail: t('game.app.msg.fork_no_ctx'), duration: 2400 });
      return;
    }
    window.dispatchEvent(new CustomEvent("rpg-regenerate", { detail: { save_id: saveId, message_index: msgIndex } }));
  };
  const doFork = async () => {
    setForkOpen(false);
    // 优先 node_id (commitId)；否则发 save_id + message_index 让后端 resolve。
    const body = { label: t('game.app.msg.fork_label') };
    if (commitId != null && commitId !== "") {
      body.node_id = commitId;
    } else if (saveId != null && msgIndex != null) {
      body.save_id = saveId;
      body.message_index = msgIndex;
    }
    try {
      const r = await window.api.branches.continueFrom(body);
      if (r && r.ok === false) {
        throw new Error(r.error || r.detail || "branch create denied");
      }
      // task 87：后端已经把新分支设为 active ref + 切换 runtime。
      // 必须 dispatch event 让 Game Console 顶层重载 /api/state（chat
      // history / activeSave / right panel / branch tree 全部刷新），
      // 否则用户只看到 toast，UI 完全没动 → 看着像"按了没反应"。
      const newCommitId = r?.active_branch_node_id || r?.active_commit_id;
      const branchHint =
        (r?.active_ref?.name && r.active_ref.name.split("/").pop()) ||
        (newCommitId ? t('game.app.msg.node_label', { id: newCommitId }) : t('game.app.msg.new_branch'));
      try {
        window.dispatchEvent(new CustomEvent("rpg-state-reload", {
          detail: { reason: "branch_fork", new_commit_id: newCommitId },
        }));
        window.dispatchEvent(new CustomEvent("rpg-saves-updated"));
      } catch (_) {}
      // task 141: 从玩家消息 fork → 那条消息其实是玩家想"在这里换说法重发",
      // 把它塞回输入框,不要让玩家手动复制粘贴。仅对 role='user' 触发。
      if (role === "user" && text) {
        // 等 state reload 完(rpg-state-reload 触发的 fetch 跑完),再写输入框,
        // 否则 Composer 重渲染会清空。延迟一帧足够让大部分 reload 完成。
        setTimeout(() => {
          try {
            window.dispatchEvent(new CustomEvent("rpg-composer-restore", {
              detail: { text },
            }));
          } catch (_) {}
        }, 250);
      }
      window.toast?.(t('game.app.msg.fork_switched'), {
        kind: "ok",
        detail: branchHint + (role === "user" ? " · " + t('game.app.msg.fork_restore_input') : " · " + t('game.app.msg.fork_on_branch')),
        duration: 2400,
      });
    } catch (e) {
      window.toast?.(t('game.app.msg.fork_create_failed'), { kind: "danger", detail: e?.message, duration: 3000 });
    }
  };
  // task 116c: 删除条件 — 必须有 saveId + msgIndex >= 0
  const canDelete = saveId != null && msgIndex != null && msgIndex >= 0;
  const doDelete = async () => {
    if (!canDelete || delBusy) return;
    setDelBusy(true);
    try {
      const r = await window.api.branches.rollbackToMessage(saveId, msgIndex);
      if (r && r.ok === false) {
        throw new Error(r.error || r.detail || "delete denied");
      }
      setDelOpen(false);
      const d = r?.deleted || {};
      // 让 Game Console 重载 state — 同 fork 路径
      try {
        window.dispatchEvent(new CustomEvent("rpg-state-reload", {
          detail: { reason: "rollback_delete", new_commit_id: r?.active_commit_id },
        }));
        window.dispatchEvent(new CustomEvent("rpg-saves-updated"));
      } catch (_) {}
      const detail = t('game.app.msg.delete_detail', { count: d.messages || 0, turn: (r?.restored_turn ?? -1) + 1 })
        + (r?.trash_ref ? " · " + t('game.app.msg.delete_trash', { name: r.trash_ref.name || "trash" }) : "");
      window.toast?.(t('game.app.msg.deleted'), { kind: "ok", detail, duration: 3200 });
    } catch (e) {
      window.toast?.(t('game.app.msg.delete_failed'), { kind: "danger", detail: e?.message, duration: 3000 });
    } finally {
      setDelBusy(false);
    }
  };
  // 编辑消息:仅 assistant(GM) 消息可编辑 → 派发事件让 NarrativeBlock 进入内联编辑
  const canEdit = role === "assistant" && saveId != null && msgIndex != null && msgIndex >= 0;
  const onEdit = () => {
    if (!canEdit) return;
    window.dispatchEvent(new CustomEvent("rpg-edit-message", { detail: { saveId, msgIndex } }));
  };
  return (
    <>
      <div className="gc-msg-actions">
        <button className="iconbtn gc-msg-act" data-tip={copied ? t('game.app.msg.copied') : t('game.app.msg.copy')} data-tip-pos="below" onClick={onCopy} aria-label={copied ? t('game.app.msg.copied') : t('game.app.msg.copy')}>
          <Icon name={copied ? "check" : "file"} size={12} />
        </button>
        {copyAskOpen && (
          <div className="gc-copy-ask" role="dialog" aria-label="复制选项">
            <label className="gc-copy-ask-label">
              <input type="checkbox" checked={copyWithMemory} onChange={(e) => setCopyWithMemory(e.target.checked)} />
              {" "}同时复制长记忆
            </label>
            <div className="gc-copy-ask-actions">
              <button className="btn ghost" onClick={() => { doCopy(copyWithMemory); }}>复制</button>
              <button className="iconbtn" onClick={() => setCopyAskOpen(false)} aria-label="取消">×</button>
            </div>
          </div>
        )}
        <button
          className="iconbtn gc-msg-act"
          data-tip={canFork ? t('game.app.msg.fork_tip') : t('game.app.msg.fork_no_ctx_tip')}
          data-tip-pos="below"
          disabled={!canFork}
          aria-label={canFork ? t('game.app.msg.fork_tip') : t('game.app.msg.fork_no_ctx_tip')} onClick={onFork}>
          <Icon name="fork" size={12} />
        </button>
        <button
          className="iconbtn gc-msg-act"
          data-tip={canRegen ? t('game.app.msg.regen_tip') : t('game.app.msg.regen_no_ctx_tip')}
          data-tip-pos="below"
          disabled={!canRegen}
          aria-label={canRegen ? t('game.app.msg.regen_tip') : t('game.app.msg.regen_no_ctx_tip')} onClick={onRegenerate}>
          <Icon name="refresh" size={12} />
        </button>
        {canEdit && (
          <button
            className="iconbtn gc-msg-act"
            data-tip={t('game.app.msg.edit_tip')}
            data-tip-pos="below"
            onClick={onEdit}>
            <Icon name="edit" size={12} />
          </button>
        )}
        <button
          className="iconbtn gc-msg-act gc-msg-act-danger"
          data-tip={canDelete ? t('game.app.msg.delete_tip') : t('game.app.msg.delete_no_ctx_tip')}
          data-tip-pos="below"
          disabled={!canDelete}
          aria-label={canDelete ? t('game.app.msg.delete_tip') : t('game.app.msg.delete_no_ctx_tip')} onClick={() => setDelOpen(true)}>
          <Icon name="trash" size={12} />
        </button>
        <span className="gc-msg-ts mono">{ts}</span>
        {meta ? <span className="gc-msg-meta mono muted-2" data-tip={t('game.app.msg.meta_tip')}>{meta}</span> : null}
      </div>
      <ForkConfirmModal open={forkOpen} text={text} onClose={() => setForkOpen(false)} onConfirm={doFork} />
      <DeleteConfirmModal
        open={delOpen}
        text={text}
        msgIndex={msgIndex}
        role={role}
        busy={delBusy}
        onClose={() => !delBusy && setDelOpen(false)}
        onConfirm={doDelete}
      />

    </>
  );
}

// task 116c: 删除消息 → 软回滚到 turn N-1 的确认弹窗。
// 警告用户:这会丢弃后续所有对话和世界线;但 git-style 保留了旧分支(refs/trash/...)可恢复。
function DeleteConfirmModal({ open, text, msgIndex, role, busy, onClose, onConfirm }) {
  const { t } = useTranslation();
  if (!open) return null;
  const preview = (text || "").slice(0, 80) + ((text || "").length > 80 ? "…" : "");
  const turnOfMsg = msgIndex != null && msgIndex >= 0 ? Math.floor(msgIndex / 2) : null;
  const restoreTurn = turnOfMsg != null ? turnOfMsg - 1 : null;
  const isAssistant = role === "assistant";
  const node = (
    <Modal
      open
      width={480}
      closeDisabled={busy}
      onClose={onClose}
      header={
        <div>
          <div className="pl-modal-eyebrow" style={{color: "var(--danger)"}}>{t('game.app.delete_modal.eyebrow')}</div>
          <h2 className="pl-modal-title">{t('game.app.delete_modal.title')}</h2>
        </div>
      }
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> POST /api/branches/rollback
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose} disabled={busy}>{t('common.cancel')}</button>
          <button className="btn danger" onClick={onConfirm} disabled={busy}>
            {busy
              ? <><span className="gc-spinner spin" /> {t('game.app.delete_modal.deleting')}</>
              : <><Icon name="trash" size={12} /> {t('game.app.delete_modal.confirm_delete')}</>}
          </button>
        </div>
      </>}
    >
      <div style={{fontSize: 13.5, lineHeight: 1.7, color: "var(--text-quiet)"}}>
        {t('game.app.delete_modal.irreversible')} {isAssistant ? t('game.app.delete_modal.this_gm_reply') : t('game.app.delete_modal.this_message')}<strong style={{color: "var(--danger)"}}>{t('game.app.delete_modal.all_after')}</strong>{t('game.app.delete_modal.discarded')}
        <div style={{
          marginTop: 10, padding: "10px 12px",
          background: "var(--bg-deep)", border: "1px solid var(--line-soft)",
          borderRadius: 6, fontFamily: "var(--font-serif)", fontSize: 13,
          color: "var(--text-quiet)", borderLeft: "2px solid var(--danger)",
        }}>
          {preview || t('game.app.delete_modal.empty_msg')}
        </div>
        <div style={{marginTop: 10, fontSize: 12, color: "var(--muted)"}}>
          {isAssistant
            ? <>{t('game.app.delete_modal.restore_before_gm')}</>
            : restoreTurn != null && restoreTurn >= 0
            ? <>{t('game.app.delete_modal.restore_turn', { turn: restoreTurn + 1 })}</>
            : <>{t('game.app.delete_modal.restore_start')}</>}
          <br />
          {t('game.app.delete_modal.trash_hint')} <code style={{fontFamily: "var(--font-mono)", fontSize: 11}}>refs/trash/...</code>
          {t('game.app.delete_modal.trash_recover')}
        </div>
      </div>
    </Modal>
  );
  return createPortal(node, document.body);
}

function ForkConfirmModal({ open, text, onClose, onConfirm }) {
  const { t } = useTranslation();
  if (!open) return null;
  const preview = (text || "").slice(0, 80) + ((text || "").length > 80 ? "…" : "");
  const node = (
    <Modal
      open
      eyebrow={t('game.app.fork_modal.eyebrow')}
      title={t('game.app.fork_modal.title')}
      width={460}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> POST /api/branches/continue
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" onClick={onConfirm}>
            <Icon name="fork" size={12} /> {t('game.app.fork_modal.new_branch')}
          </button>
        </div>
      </>}
    >
      <div style={{fontSize: 13.5, lineHeight: 1.7, color: "var(--text-quiet)"}}>
        {t('game.app.fork_modal.body')}
        <div style={{
          marginTop: 10, padding: "10px 12px",
          background: "var(--bg-deep)", border: "1px solid var(--line-soft)",
          borderRadius: 6, fontFamily: "var(--font-serif)", fontSize: 13,
          color: "var(--text-quiet)", borderLeft: "2px solid var(--accent-edge)",
        }}>
          {preview}
        </div>
      </div>
    </Modal>
  );
  return createPortal(node, document.body);
}

function stripStateOpsForDisplay(text) {
  // 旧版本只剥 fenced JSON,裸数组([{...,"op":...}])漏过 — 改走统一的 stripNarrativeOps。
  // opening message 写回 history 时未 strip,主聊天区也得展示层兜底过滤。
  return stripNarrativeOps(text);
}

// 把工具调用按 anchor(触发时的正文长度)内联进正文 —— Claude 风,工具卡片出现在它实际发生
// 的文本位置,而不是永远置顶。anchor 是【原始 content】的偏移(与后端 len(response) 一致),
// 故先按 anchor 切原始文本、每段再 stripStateOpsForDisplay,避免 strip 改变长度造成错位。
// renderTool(opsAtAnchor) 由调用方提供(酒馆传 ToolCallBlock)。同一 anchor 的多个工具合并成一组。
function renderNarrativeWithInlineTools(rawText, toolOps, renderTool, streaming, MdBlock) {
  const text = rawText || "";
  const ops = toolOps
    .map((o) => ({ op: o, a: Math.max(0, Math.min(Number.isFinite(o && o.anchor) ? o.anchor : text.length, text.length)) }))
    .sort((x, y) => x.a - y.a);
  const groups = [];
  for (const it of ops) {
    const g = groups[groups.length - 1];
    if (g && g.anchor === it.a) g.ops.push(it.op);
    else groups.push({ anchor: it.a, ops: [it.op] });
  }
  const nodes = [];
  let prev = 0;
  groups.forEach((g, gi) => {
    const chunk = stripStateOpsForDisplay(text.slice(prev, g.anchor));
    if (chunk.trim()) {
      nodes.push(MdBlock
        ? <MdBlock key={`tx-${gi}`} text={chunk} streaming={false} className="rpg-md" />
        : <p key={`tx-${gi}`}>{chunk}</p>);
    }
    nodes.push(<React.Fragment key={`tl-${gi}`}>{renderTool(g.ops)}</React.Fragment>);
    prev = g.anchor;
  });
  const tail = stripStateOpsForDisplay(text.slice(prev));
  if (tail.trim() || nodes.length === 0) {
    nodes.push(MdBlock
      ? <MdBlock key="tx-tail" text={tail} streaming={!!streaming} className="rpg-md" />
      : <p key="tx-tail">{tail}{streaming && <span className="gc-cursor" />}</p>);
  }
  return nodes;
}

// 酒馆模式复用:speakerName/speakerAvatar/tag 可选覆盖默认的 GM/主代理 标签。
// 不传时与 Game Console 行为完全一致(默认 tag="GM", subtitle="主代理")。
function NarrativeBlock({ text, streaming, ts, msgIndex, saveId, commitId, thinking, speakerName, speakerAvatar, tag, hideMeta, meta, images, toolOps, renderTool }) {
  const { t } = useTranslation();
  const displayText = stripStateOpsForDisplay(text);
  // 内联编辑状态:点击 MsgActions 的编辑按钮时通过事件激活
  const [editing, setEditing] = useStateA(false);
  const [editDraft, setEditDraft] = useStateA("");
  const [editSaving, setEditSaving] = useStateA(false);
  const editRef = useRefA(null);
  useEffectA(() => {
    const handler = (e) => {
      const d = e.detail || {};
      if (d.saveId === saveId && d.msgIndex === msgIndex) {
        setEditDraft(displayText || "");
        setEditing(true);
      }
    };
    window.addEventListener("rpg-edit-message", handler);
    return () => window.removeEventListener("rpg-edit-message", handler);
  }, [saveId, msgIndex, displayText]);
  // 进入编辑模式后自动 focus textarea 并定位到末尾
  useEffectA(() => {
    if (editing && editRef.current) {
      const el = editRef.current;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }, [editing]);
  const doEditSave = async () => {
    if (editSaving) return;
    setEditSaving(true);
    try {
      const r = await window.api.game.editMessage({ save_id: saveId, message_index: msgIndex, content: editDraft });
      if (r && r.ok === false) throw new Error(r.error || "edit failed");
      setEditing(false);
      try {
        window.dispatchEvent(new CustomEvent("rpg-state-reload", { detail: { reason: "message_edit" } }));
      } catch (_) {}
      window.toast?.(t('game.app.msg.edit_saved'), { kind: "ok", duration: 1800 });
    } catch (e) {
      window.toast?.(t('game.app.msg.edit_failed'), { kind: "danger", detail: e?.message, duration: 3000 });
    } finally {
      setEditSaving(false);
    }
  };
  // task 90: 用 RpgMarkdown.Block 渲染 markdown (** / # / list / code / link...)
  // window.RpgMarkdown 由 markdown-render.jsx 提供,加载顺序在 game-app.jsx 之前。
  const MdBlock = RpgMarkdown.Block;
  const tagLabel = tag || "GM";
  // 酒馆模式显式传 speakerName="" → 隐藏副标题(只显示角色名 tag);
  // Game Console 不传(undefined)→ 默认"主代理"(零回归)。
  const subLabel = speakerName === "" ? "" : (speakerName || t('game.app.narrative.main_agent'));
  // task 121a: thinking 状态显示带 spinner 的 italic 文字,跟正式 narrative 区分
  // speakerAvatar 兼容:若为 URL(/ 或 http 开头)则渲 AvatarImg,否则保持首字母 span(向后兼容)。
  const isAvatarUrl = speakerAvatar && (speakerAvatar.startsWith('/') || speakerAvatar.startsWith('http'));
  const avatarNode = speakerAvatar
    ? (isAvatarUrl
        ? <AvatarImg src={speakerAvatar} size={28} shape="circle" />
        : <span className="gc-msg-avatar serif">{speakerAvatar}</span>)
    : null;

  if (thinking) {
    return (
      <div className="gc-msg gc-msg-gm gc-msg-thinking">
        {!hideMeta && (
          <div className="gc-msg-meta">
            {avatarNode}
            <span className="gc-msg-tag">{tagLabel}</span>
            <span className="muted-2" style={{ fontSize: 11.5 }}>{t('game.app.narrative.preparing')}</span>
          </div>
        )}
        <div className="gc-msg-body" style={{ fontStyle: "italic", color: "var(--text-quiet)", opacity: 0.85 }}>
          <span className="gc-spinner spin" /> {text || t('game.app.narrative.please_wait')}
        </div>
      </div>
    );
  }
  return (
    <div className="gc-msg gc-msg-gm">
      {!hideMeta && (
        <div className="gc-msg-meta">
          {avatarNode}
          <span className="gc-msg-tag">{tagLabel}</span>
          {subLabel && <span className="muted-2" style={{ fontSize: 11.5 }}>{subLabel}</span>}
        </div>
      )}
      <div className="gc-msg-body serif">
        {editing ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <textarea
              ref={editRef}
              value={editDraft}
              onChange={e => setEditDraft(e.target.value)}
              disabled={editSaving}
              onKeyDown={e => {
                if (e.key === "s" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); doEditSave(); }
                if (e.key === "Escape") { setEditing(false); }
              }}
              style={{
                width: "100%", minHeight: 180, maxHeight: "60vh", resize: "vertical",
                fontFamily: "var(--font-serif, inherit)", fontSize: "var(--d-narrative, 16.5px)",
                lineHeight: "var(--d-line, 1.78)", padding: "10px 12px", borderRadius: 8,
                border: "1px solid var(--accent-edge, var(--line))",
                background: "var(--bg-deep, #1a1816)", color: "var(--text)", outline: "none",
                letterSpacing: "0.02em",
              }}
            />
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn ghost" disabled={editSaving} onClick={() => setEditing(false)}>
                {t('common.cancel')}
              </button>
              <button className="btn primary" disabled={editSaving || !editDraft.trim()} onClick={doEditSave}>
                {editSaving
                  ? <><span className="gc-spinner spin" /> {t('game.app.edit_modal.saving')}</>
                  : <><Icon name="check" size={12} /> {t('game.app.edit_modal.confirm')}</>}
              </button>
            </div>
          </div>
        ) : (
          <>
            {(Array.isArray(toolOps) && toolOps.length > 0 && typeof renderTool === 'function')
              ? renderNarrativeWithInlineTools(text, toolOps, renderTool, streaming, MdBlock)
              : (MdBlock
                  ? <MdBlock text={displayText || ""} streaming={!!streaming} className="rpg-md" />
                  : (displayText || "").split(/\n\n+/).map((p, i) =>
                      <p key={i}>{p}{streaming && i === (displayText || "").split(/\n\n+/).length - 1 && <span className="gc-cursor" />}</p>
                    )
                )
            }
            <ChatImageGroup images={images} />
          </>
        )}
      </div>
      {!streaming && <MsgActions text={displayText} ts={ts || "—"} msgIndex={msgIndex} saveId={saveId} commitId={commitId} role="assistant" meta={meta} />}
    </div>);

}

// 酒馆模式复用:speakerName/tag 可选覆盖默认「玩家」标签(persona 名等)。
function PlayerBlock({ text, ts, attachments, msgIndex, saveId, commitId, speakerName, speakerAvatar, tag, hideMeta }) {
  const { t } = useTranslation();
  const tagLabel = tag || speakerName || t('game.app.narrative.player');
  // speakerAvatar 兼容:若为 URL(/ 或 http 开头)则渲 AvatarImg,否则保持首字母 span(向后兼容)。
  const isAvatarUrl = speakerAvatar && (speakerAvatar.startsWith('/') || speakerAvatar.startsWith('http'));
  const avatarNode = speakerAvatar
    ? (isAvatarUrl
        ? <AvatarImg src={speakerAvatar} size={28} shape="circle" />
        : <span className="gc-msg-avatar serif">{speakerAvatar}</span>)
    : null;
  return (
    <div className="gc-msg gc-msg-player">
      {!hideMeta && (
        <div className="gc-msg-meta">
          {avatarNode}
          <span className="gc-msg-tag muted">{tagLabel}</span>
        </div>
      )}
      <div className="gc-msg-body">
        <p>{text}</p>
        {attachments?.length > 0 &&
        <div className="gc-attachments" style={{ marginTop: 6 }}>
            {attachments.map((a, i) =>
          <span key={i} className="gc-attachment">
                <Icon name={a.kind === "image" ? "image" : "file"} size={12} />
                {a.name}
              </span>
          )}
          </div>
        }
      </div>
      <MsgActions text={text} ts={ts} msgIndex={msgIndex} saveId={saveId} commitId={commitId} role="user" />
    </div>);

}

// ── 聊天内嵌图片(GPT 风:图片是回复的一部分,渲在助手消息气泡内)─────────────
// 关联策略:实时到达 → 归到当前最后一条助手消息(lastKeyRef);并把 {imageId: msgKey}
// 持久化到 localStorage(按 saveId),刷新后位置仍在。未映射的旧图回退到最后助手消息。
// msgKey = 助手消息的绝对索引字符串(append-only history 跨刷新稳定)。
function _imgMapKey(saveId) { return `rpg.imgmsg.${saveId}`; }
function _loadImgMap(saveId) {
  return lsGetJSON(_imgMapKey(saveId), {});
}
function _saveImgMap(saveId, map) {
  lsSetJSON(_imgMapKey(saveId), map);
}

// 返回 { msgKey: images[] };未映射的归入 '__last' 桶(由调用方挂到最后助手消息)。
export function useSaveImages(saveId, lastKeyRef) {
  const [images, setImages] = useStateA([]);   // [{id,url,kind,key}]
  const mapRef = useRefA({});

  // 拉历史图片 + 应用持久化映射
  useEffectA(() => {
    if (saveId == null) { setImages([]); mapRef.current = {}; return; }
    let cancelled = false;
    mapRef.current = _loadImgMap(saveId);
    (async () => {
      try {
        const list = await window.api.images.list(saveId);
        if (cancelled) return;
        const done = Array.isArray(list) ? list.filter((im) => im.status === 'done' && im.url) : [];
        const map = mapRef.current;
        // 反馈#74:优先用后端权威 message_index(刷新后确定性还原),旧行回退 localStorage 映射。
        setImages(done.map((im) => ({
          id: im.id, url: im.url, kind: im.kind || 'game',
          key: (im.message_index != null ? String(im.message_index)
                : (map[im.id] != null ? String(map[im.id]) : null)),
        })));
      } catch (_) { /* 后端未实装时静默 */ }
    })();
    return () => { cancelled = true; };
  }, [saveId]);

  // SSE 实时追加,归到当前最后助手消息
  useEffectA(() => {
    if (saveId == null) return;
    const handler = (ev) => {
      const { op, payload } = (ev && ev.detail) || {};
      if (op !== 'ready') return;
      const { image_id, url, kind } = payload || {};
      if (!image_id || !url) return;
      const key = (lastKeyRef && lastKeyRef.current != null) ? String(lastKeyRef.current) : null;
      if (key != null) { mapRef.current[image_id] = key; _saveImgMap(saveId, mapRef.current); }
      setImages((prev) => prev.some((im) => im.id === image_id) ? prev
        : [...prev, { id: image_id, url, kind: kind || 'game', key }]);
    };
    window.addEventListener('rpg-image-updated', handler);
    return () => window.removeEventListener('rpg-image-updated', handler);
  }, [saveId]);

  return useMemoA(() => {
    const g = {};
    for (const im of images) {
      const k = im.key != null ? im.key : '__last';
      (g[k] = g[k] || []).push(im);
    }
    return g;
  }, [images]);
}

// 助手消息气泡内的图片组(单图自然比例,多图方形拼贴),点击全屏。
function ChatImageGroup({ images }) {
  const { t } = useTranslation();
  const [lightbox, setLightbox] = useStateA(null);
  useEffectA(() => {
    if (!lightbox) return;
    const h = (e) => { if (e.key === 'Escape') setLightbox(null); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [lightbox]);
  if (!images || !images.length) return null;
  const multi = images.length > 1;
  return (
    <div className="rpg-chat-imgs">
      {images.map((im) => (
        <button key={im.id} type="button" title={im.kind || t('game.app.image.generated')}
          className={`rpg-chat-img ${multi ? 'rpg-chat-img--multi' : 'rpg-chat-img--single'}`}
          onClick={() => setLightbox(im.url)}>
          <img src={im.url} alt="" loading="lazy" decoding="async" />
        </button>
      ))}
      {lightbox && (
        <div className="mlb-backdrop" onClick={() => setLightbox(null)} role="dialog" aria-modal="true">
          <img src={lightbox} alt="" style={{ maxWidth: '92vw', maxHeight: '90vh', objectFit: 'contain', borderRadius: 10, boxShadow: '0 12px 60px rgba(0,0,0,.7)' }} onClick={(e) => e.stopPropagation()} />
          <button onClick={() => setLightbox(null)} aria-label={t('common.close')} style={{ position: 'absolute', top: 20, right: 24, width: 38, height: 38, borderRadius: 99, border: 0, background: 'rgba(255,255,255,.14)', color: '#fff', fontSize: 19, cursor: 'pointer' }}>×</button>
        </div>
      )}
    </div>
  );
}

// ── Phase 3: 会话生成图片区(旧:底部独立 strip — 已退役为内嵌,保留定义供兼容)──────
// 挂载/saveId 变化时拉取已有图片(status==='done' && url),并订阅 SSE image topic 实时追加。
// 组件卸载时取消订阅,防泄漏。
function SaveImagesStrip({ saveId }) {
  const { t } = useTranslation();
  const [images, setImages] = useStateA([]);
  const [lightbox, setLightbox] = useStateA(null); // 当前放大的 url

  // 1. 挂载/saveId 变化时拉取历史图片
  useEffectA(() => {
    if (saveId == null) { setImages([]); return; }
    let cancelled = false;
    (async () => {
      try {
        const list = await window.api.images.list(saveId);
        if (cancelled) return;
        const done = Array.isArray(list)
          ? list.filter((img) => img.status === 'done' && img.url)
          : [];
        setImages(done);
      } catch (_) { /* 静默:后端未实装时不崩 */ }
    })();
    return () => { cancelled = true; };
  }, [saveId]);

  // 2. 订阅 SSE image topic，实时追加 ready 事件
  useEffectA(() => {
    if (saveId == null) return;
    const handler = (ev) => {
      const { op, payload } = (ev && ev.detail) || {};
      if (op !== 'ready') return;
      const { image_id, url, kind } = payload || {};
      if (!image_id || !url) return;
      setImages((prev) => {
        if (prev.some((img) => img.id === image_id)) return prev;
        return [...prev, { id: image_id, url, kind: kind || 'game', status: 'done' }];
      });
    };
    window.addEventListener('rpg-image-updated', handler);
    return () => window.removeEventListener('rpg-image-updated', handler);
  }, [saveId]);

  if (!images.length) return null;

  return (
    <div style={{
      margin: '12px 0 4px',
      padding: '10px 12px',
      background: 'var(--surface-2, rgba(255,255,255,0.03))',
      border: '1px solid var(--line-soft, rgba(255,255,255,0.07))',
      borderRadius: 8,
    }}>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
        {t('game.app.image.strip_title', { count: images.length })}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {images.map((img) => (
          <button
            key={img.id}
            onClick={() => setLightbox(img.url)}
            style={{
              border: 0, padding: 0, background: 'transparent', cursor: 'pointer',
              borderRadius: 6, overflow: 'hidden', flexShrink: 0,
            }}
            title={img.prompt || img.kind || t('game.app.image.generated')}
          >
            <AvatarImg
              src={img.url}
              name={img.kind || 'img'}
              size={80}
              shape="rounded"
              className=""
            />
          </button>
        ))}
      </div>
      {lightbox && (
        <div
          onClick={() => setLightbox(null)}
          style={{
            position: 'fixed', inset: 0, zIndex: 8000,
            background: 'rgba(0,0,0,0.82)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <img
            src={lightbox}
            alt={t('game.app.image.generated')}
            style={{ maxWidth: '90vw', maxHeight: '90vh', borderRadius: 8, boxShadow: '0 8px 32px rgba(0,0,0,0.6)' }}
            onClick={(e) => e.stopPropagation()}
          />
          <button
            onClick={() => setLightbox(null)}
            style={{
              position: 'absolute', top: 20, right: 24,
              background: 'rgba(255,255,255,0.12)', border: 0, color: '#fff',
              borderRadius: 99, width: 36, height: 36, fontSize: 18,
              cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >×</button>
        </div>
      )}
    </div>
  );
}

function ChatArea({ history, runState, runStyle, narrativeFont, narrativeSize, hasError, errorMessage, saveId, onRetry, onShowSse }) {
  const { t } = useTranslation();
  const ref = useRefA(null);
  // task 21：实战存档 history 可能有 100+ 条；一次性渲染整个数组 + 每次 setGame
  // 都重渲全部 NarrativeBlock 会拖死主线程（用户报 Playwright 简单 DOM 访问也 45s 不返回）。
  // 默认只渲染最近 80 条；用户可点 "显示更早" 一次性扩 80 条。完整历史走顶栏「历史回顾」抽屉。
  const HISTORY_WINDOW = 80;
  const [extra, setExtra] = useStateA(0);
  const totalLen = Array.isArray(history) ? history.length : 0;
  const visibleStart = Math.max(0, totalLen - HISTORY_WINDOW - extra);
  const hiddenCount = visibleStart;
  const visible = totalLen > 0 ? history.slice(visibleStart) : [];

  // 内嵌聊天图片:最后一条助手消息的绝对索引(实时图归属 + __last 兜底)
  let lastAsstIdx = -1;
  for (let _i = totalLen - 1; _i >= 0; _i--) { if (history[_i] && history[_i].role === "assistant") { lastAsstIdx = _i; break; } }
  const lastKeyRef = useRefA(null);
  lastKeyRef.current = lastAsstIdx >= 0 ? String(lastAsstIdx) : null;
  const imagesByKey = useSaveImages(saveId, lastKeyRef);

  // task 133: Claude 风格自动滚动 — 用户上滚后停止跟随 + 回到底部按钮。
  // 收口到 useStickToBottom(逐字等价):首屏门控用 visible.length(窗口化渲染),
  // 「末条=玩家」判定读完整 history,deps 逐字保留 [visible.length, running, rawSteps?.length]。
  const _last = history && history[history.length - 1];
  const { showJump: showJumpBtn, jumpToBottom } = useStickToBottom(ref, {
    deps: [visible.length, runState.running, runState.rawSteps?.length],
    lastIsUser: !!(_last && _last.role === "user"),
    hasContent: visible.length > 0,
    mode: "instant",
    withButton: true,
  });

  return (
    <div
      ref={ref}
      className="gc-chat"
      style={{
        "--narrative-font": narrativeFont === "serif" ? "var(--font-serif)" : "var(--font-sans)",
        "--narrative-size": narrativeSize + "px"
      }}>

      <div className="gc-chat-inner">
        {hiddenCount > 0 && (
          <div className="muted-2" style={{textAlign: "center", padding: "8px 0", fontSize: 12}}>
            {t('game.app.chat.hidden_count', { count: hiddenCount })} ·{" "}
            <button className="link" onClick={() => setExtra(x => x + HISTORY_WINDOW)}>
              {t('game.app.chat.load_more', { count: Math.min(HISTORY_WINDOW, hiddenCount) })}
            </button>
            {" · "}
            <span className="muted">{t('game.app.chat.full_history_hint')}</span>
          </div>
        )}
        {visible.map((m, i) => {
          const idx = visibleStart + i;
          // task 38：把 history 索引和当前 saveId 传给消息块，再透给 MsgActions
          // 之前 idx/saveId/commitId 全是 undefined → /api/branches/continue 收到 {label} 后端崩。
          const commitId = m && (m.commit_id || m.node_id);
          return m.role === "assistant" ?
          <NarrativeBlock key={`gm-${idx}`} text={m.content} ts={m.ts}
            msgIndex={idx} saveId={saveId} commitId={commitId}
            thinking={m._thinking}
            images={imagesByKey[String(idx)] || (idx === lastAsstIdx ? imagesByKey['__last'] : undefined)}
            streaming={!m.streaming_done && idx === totalLen - 1 && runState.running}
            toolOps={m._toolOps || m.tool_ops || []}
            renderTool={(ops) => <ToolCallBlock ops={ops} />} /> :
          <PlayerBlock key={`pl-${idx}`} text={m.content} ts={m.ts} attachments={m.attachments}
            msgIndex={idx} saveId={saveId} commitId={commitId} />;
        })}

        {/* 思考指示器统一(2026-06-23):此前等待首 token 时,这里的 gc-waiting-gm 三点气泡
            与下方 ThinkingPill 圆环【同时出现】= 一个回合内两种"正在思考"UI 并存,跨回合反复
            出现(用户反馈"思考ui有两种循环出现")。ThinkingPill 自身已覆盖整段运行(running 全程,
            含等待首 token 阶段:整理上下文 → 生成正文 → 落库),且带阶段进度/百分比/计时/"已完成"
            收尾,信息更全。故移除冗余的 gc-waiting-gm 三点气泡,全程只用 ThinkingPill 这一种指示器。 */}

        {/* task 92:高层思考状态(整理上下文→生成正文→落库 + 百分比 + 计时 + "已完成 · X.Xs"),
            是回合全程唯一的"正在思考"指示器。完整 raw phase trace 折叠在内,需要时展开。 */}
        <ThinkingPill runState={runState} runStyle={runStyle} />

        {hasError &&
        <div className="gc-error">
            <Icon name="warn" size={14} style={{ color: "var(--danger)" }} />
            <div>
              <strong>{t('game.app.chat.gen_failed')}</strong>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                {/* task 31：以前这里硬编码"请求中断：上游 504"，把空消息/字段契约错全都误报成网络超时。
                    现在显示后端 error.message 的真实文本（hasError 为字符串时是错误正文，为 true 时回退）。 */}
                {(typeof hasError === "string" && hasError) || errorMessage || t('game.app.chat.request_aborted')}
              </p>
              <div className="gc-error-actions">
                <button className="btn" onClick={onRetry} disabled={!onRetry}>{t('game.app.chat.retry')}</button>
                <button className="btn ghost" onClick={onShowSse} disabled={!onShowSse}>{t('game.app.chat.view_sse')}</button>
              </div>
            </div>
          </div>
        }
        {/* 图片已内嵌进对应助手消息气泡(useSaveImages + ChatImageGroup),不再底部独立 strip */}
        {/* task 133: Claude 风格"回到底部"按钮 — 用户上滚时显示。**必须 sticky 在滚动容器内**
            (而非 absolute):absolute 在 overflow 滚动容器里会随内容滚走、且祖先无 position:relative
            时锚到页面最右(群反馈酒馆/游戏同症)。sticky + justify-self:end → 钉在阅读列右下、
            不随滚动飘。bottom:16 贴 composer 上方。 */}
        {showJumpBtn && (
          <button
            onClick={jumpToBottom}
            className="btn"
            style={{
              position: "sticky", bottom: 16, justifySelf: "end",
              marginLeft: "auto", width: "fit-content",
              background: "var(--panel)", border: "1px solid var(--line)",
              borderRadius: 999, padding: "6px 14px", fontSize: 12.5,
              boxShadow: "var(--shadow-3, 0 6px 18px -6px rgba(0,0,0,0.5))",
              zIndex: 5, cursor: "pointer",
            }}
            data-tip={t('game.app.chat.jump_latest_tip')}>
            <Icon name="chevron_down" size={12} /> {t('game.app.chat.jump_latest')}
          </button>
        )}
      </div>
    </div>);

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

// ----------------------------- IN-GAME SETTINGS --------------------------
// task 89 → task 135: 用真实可用的设置面板替换 placeholder。
// MVP 范围: 密度预设 / 叙事字体 / 自动存档 / 权限模式只读展示 / 全局设置链接。
// 所有改动均为纯前端 localStorage — 不需要后端。
function _readDensity() {
  return lsGet("rpg.density") || "default";
}
function _readNarrativeFont() {
  return lsGet("rpg.narrativeFont") || "serif";
}
function _readAutosave() {
  return lsGet("rpg.autosave") !== "off";
}
// #11: token 用量显示开关 — 默认关闭(=== "on")
function _readShowUsage() {
  return lsGet("rpg.showTokenUsage") === "on";
}
// A#2:未响应/失败时保留本轮对话(可重试),不回退玩家气泡 — 默认关闭(=== "on")
function _readKeepFailedTurn() {
  return lsGet("gc.keepFailedTurn") === "on";
}

function GameSettingsModal({ open, onClose, saveTitle, permission, saveId }) {
  const { t } = useTranslation();
  const [density, setDensityState] = useStateA(_readDensity);
  const [narrativeFont, setNarrativeFontState] = useStateA(_readNarrativeFont);
  const [autosave, setAutosaveState] = useStateA(_readAutosave);
  const [showUsage, setShowUsageState] = useStateA(_readShowUsage);
  const [keepFailedTurn, setKeepFailedTurnState] = useStateA(_readKeepFailedTurn);
  // null = 尚未从后端拉到本档真实值;加载期不高亮任何档,避免先闪默认「软引导」再跳真值(被误读成"自己回跳")
  const [steerStrength, setSteerStrength] = useStateA(null);
  // acceptance 改写建议开关(用户级 user_preferences['acceptance_ab.enabled'],默认开)。行者无疆诉求:可手动关。
  const [abEnabled, setAbEnabled] = useStateA(true);

  // sync density state with external RPG_setDensity calls
  useEffectA(() => {
    const onDensityChange = (e) => setDensityState(e.detail || "default");
    window.addEventListener("rpg-density-change", onDensityChange);
    return () => window.removeEventListener("rpg-density-change", onDensityChange);
  }, []);

  // 打开时拉一次存档设置,取 steering_strength 当前值
  useEffectA(() => {
    if (!open || saveId == null) return;
    const base = (window.__API_BASE || '');
    fetch(`${base}/api/saves/${saveId}/settings`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      // 成功就落定真值(缺字段才回退默认),失败保持 null 不假装默认 → 不会误显回跳
      .then(d => { if (d?.ok && d.settings) setSteerStrength(d.settings.steering_strength || "guided"); })
      .catch(() => {});
  }, [open, saveId]);

  // 打开时拉用户偏好,取 acceptance 改写建议开关(默认开)
  useEffectA(() => {
    if (!open) return;
    const base = (window.__API_BASE || '');
    fetch(`${base}/api/me/profile`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        const v = d && d.preferences && d.preferences['acceptance_ab.enabled'];
        setAbEnabled(!(String(v).toLowerCase() === 'false' || v === false));
      })
      .catch(() => {});
  }, [open]);

  const handleAbEnabled = (v) => {
    setAbEnabled(v);
    const base = (window.__API_BASE || '');
    fetch(`${base}/api/me/preference`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 'acceptance_ab.enabled': v }),
    }).catch(() => {});
  };

  const handleDensity = (d) => {
    setDensityState(d);
    if (typeof window.RPG_setDensity === "function") window.RPG_setDensity(d);
  };

  const handleNarrativeFont = (f) => {
    setNarrativeFontState(f);
    lsSet("rpg.narrativeFont", f);
    const fontMap = {
      serif: "var(--font-serif)",
      sans: "var(--font-sans)",
      mono: "var(--font-mono)",
    };
    document.documentElement.style.setProperty("--narrative-font", fontMap[f] || fontMap.serif);
    window.dispatchEvent(new CustomEvent("rpg-narrative-font-change", { detail: f }));
  };

  const handleAutosave = (v) => {
    setAutosaveState(v);
    lsSet("rpg.autosave", v ? "on" : "off");
  };

  const handleShowUsage = (v) => {
    setShowUsageState(v);
    lsSet("rpg.showTokenUsage", v ? "on" : "off");
    // App(game-console)监听此事件即时显隐 footer,无需刷新
    window.dispatchEvent(new CustomEvent("rpg-show-usage-change", { detail: v }));
  };

  const handleKeepFailedTurn = (v) => {
    setKeepFailedTurnState(v);
    // 纯前端:game-console 的 restoreFailedDraft 在失败时即时读 localStorage,无需事件/刷新
    lsSet("gc.keepFailedTurn", v ? "on" : "off");
  };

  const handleSteerStrength = (v) => {
    setSteerStrength(v);
    if (saveId == null) return;
    const base = (window.__API_BASE || '');
    fetch(`${base}/api/saves/${saveId}/settings`, {
      method: 'PATCH', credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ updates: { steering_strength: v } }),
    }).catch(() => {});
  };

  if (!open) return null;

  const PERM_OPT = (typeof window.PERMISSION_OPTIONS !== "undefined" && window.PERMISSION_OPTIONS) || [
    { id: "read_only",   label: t('game.app.settings.perm_read_only'), icon: "eye" },
    { id: "default",     label: t('game.app.settings.perm_default'),   icon: "lock" },
    { id: "review",      label: t('game.app.settings.perm_review'),    icon: "shield" },
    { id: "full_access", label: t('game.app.settings.perm_full'),      icon: "unlock" },
  ];
  const currentPerm = PERM_OPT.find(p => p.id === permission) || PERM_OPT[1];

  const DENSITY_OPTS = [
    { id: "compact",  label: t('game.app.settings.density_compact') },
    { id: "default",  label: t('game.app.settings.density_default') },
    { id: "spacious", label: t('game.app.settings.density_spacious') },
  ];
  const FONT_OPTS = [
    { id: "serif", label: t('game.app.settings.font_serif') },
    { id: "sans",  label: t('game.app.settings.font_sans') },
    { id: "mono",  label: t('game.app.settings.font_mono') },
  ];
  const STEER_OPTS = [
    { id: "rail",    label: t('game.app.settings.steer_rail') },
    { id: "guided",  label: t('game.app.settings.steer_guided') },
    { id: "free",    label: t('game.app.settings.steer_free') },
  ];

  const rowStyle = {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "10px 0", borderBottom: "1px solid var(--line-soft)",
    gap: 16,
  };
  const labelStyle = { fontSize: 13, color: "var(--text)", flex: 1 };
  const sublabelStyle = { fontSize: 11.5, color: "var(--muted)", marginTop: 2 };

  const node = (
    <Modal
      open
      eyebrow={t('game.app.settings.eyebrow')}
      title={saveTitle || t('game.app.settings.title')}
      width={480}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> {t('game.app.settings.instant_hint')}
        </span>
        <div style={{display: "flex", gap: 8}}>
          <a className="btn ghost" href="/settings"
             target="_blank" rel="noopener noreferrer"
             style={{textDecoration: "none"}}>
            <Icon name="settings" size={12} /> {t('game.app.settings.global_settings')}
          </a>
          <button className="btn primary" onClick={onClose}>
            <Icon name="check" size={12} /> {t('game.app.settings.done')}
          </button>
        </div>
      </>}
    >
      <div className="pl-modal-form" style={{paddingTop: 4}}>

          {/* ── 信息密度 ── */}
          <div style={rowStyle}>
            <div style={labelStyle}>
              <div>{t('game.app.settings.density_label')}</div>
              <div style={sublabelStyle}>{t('game.app.settings.density_desc')}</div>
            </div>
            <div className="seg" style={{flexShrink: 0}}>
              {DENSITY_OPTS.map(d => (
                <button key={d.id} className={density === d.id ? "active" : ""}
                        onClick={() => handleDensity(d.id)}>
                  {d.label}
                </button>
              ))}
            </div>
          </div>

          {/* ── 叙事字体 ── */}
          <div style={rowStyle}>
            <div style={labelStyle}>
              <div>{t('game.app.settings.font_label')}</div>
              <div style={sublabelStyle}>{t('game.app.settings.font_desc')}</div>
            </div>
            <div className="seg" style={{flexShrink: 0}}>
              {FONT_OPTS.map(f => (
                <button key={f.id} className={narrativeFont === f.id ? "active" : ""}
                        onClick={() => handleNarrativeFont(f.id)}>
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {/* ── 自动存档 ── */}
          <div style={rowStyle}>
            <div style={labelStyle}>
              <div>{t('game.app.settings.autosave_label')}</div>
              <div style={sublabelStyle}>{t('game.app.settings.autosave_desc')}</div>
            </div>
            <label style={{display: "flex", alignItems: "center", gap: 8, cursor: "pointer", flexShrink: 0}}>
              <input type="checkbox" checked={autosave}
                     onChange={(e) => handleAutosave(e.target.checked)}
                     style={{width: 15, height: 15, cursor: "pointer"}} />
              <span style={{fontSize: 12.5, color: "var(--text-quiet)"}}>{autosave ? t('game.app.settings.on') : t('game.app.settings.off')}</span>
            </label>
          </div>

          {/* ── 显示 token 用量 ── */}
          <div style={rowStyle}>
            <div style={labelStyle}>
              <div>{t('game.app.settings.show_usage_label')}</div>
              <div style={sublabelStyle}>{t('game.app.settings.show_usage_desc')}</div>
            </div>
            <label style={{display: "flex", alignItems: "center", gap: 8, cursor: "pointer", flexShrink: 0}}>
              <input type="checkbox" checked={showUsage}
                     onChange={(e) => handleShowUsage(e.target.checked)}
                     style={{width: 15, height: 15, cursor: "pointer"}} />
              <span style={{fontSize: 12.5, color: "var(--text-quiet)"}}>{showUsage ? t('game.app.settings.on') : t('game.app.settings.off')}</span>
            </label>
          </div>

          {/* ── 保留未响应轮(可重试) ── */}
          <div style={rowStyle}>
            <div style={labelStyle}>
              <div>{t('game.app.settings.keep_failed_label')}</div>
              <div style={sublabelStyle}>{t('game.app.settings.keep_failed_desc')}</div>
            </div>
            <label style={{display: "flex", alignItems: "center", gap: 8, cursor: "pointer", flexShrink: 0}}>
              <input type="checkbox" checked={keepFailedTurn}
                     onChange={(e) => handleKeepFailedTurn(e.target.checked)}
                     style={{width: 15, height: 15, cursor: "pointer"}} />
              <span style={{fontSize: 12.5, color: "var(--text-quiet)"}}>{keepFailedTurn ? t('game.app.settings.on') : t('game.app.settings.off')}</span>
            </label>
          </div>

          {/* ── AI 改写建议(acceptance A/B) ── */}
          <div style={rowStyle}>
            <div style={labelStyle}>
              <div>{t('game.app.settings.ab_label', 'AI 改写建议')}</div>
              <div style={sublabelStyle}>{t('game.app.settings.ab_desc', 'AI 偶尔漏掉本回合该体现的剧情点时,并排给你一个改写版本供选择(最多每 5 回合一次)。关掉则始终只用首稿。')}</div>
            </div>
            <label style={{display: "flex", alignItems: "center", gap: 8, cursor: "pointer", flexShrink: 0}}>
              <input type="checkbox" checked={abEnabled}
                     onChange={(e) => handleAbEnabled(e.target.checked)}
                     style={{width: 15, height: 15, cursor: "pointer"}} />
              <span style={{fontSize: 12.5, color: "var(--text-quiet)"}}>{abEnabled ? t('game.app.settings.on') : t('game.app.settings.off')}</span>
            </label>
          </div>

          {/* ── 剧情引导强度 ── */}
          {saveId != null && (
            <div style={rowStyle}>
              <div style={labelStyle}>
                <div>{t('game.app.settings.steering_label')}</div>
                <div style={sublabelStyle}>{t('game.app.settings.steering_desc')}</div>
              </div>
              <div className="seg" style={{flexShrink: 0}}>
                {STEER_OPTS.map(s => (
                  <button key={s.id} className={steerStrength === s.id ? "active" : ""}
                          onClick={() => handleSteerStrength(s.id)}>
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* ── 写入权限（只读展示） ── */}
          <div style={{...rowStyle, borderBottom: "none"}}>
            <div style={labelStyle}>
              <div>{t('game.app.settings.perm_label')}</div>
              <div style={sublabelStyle}>{t('game.app.settings.perm_desc')}</div>
            </div>
            <div className="pill" style={{flexShrink: 0, gap: 6}}>
              <Icon name={currentPerm.icon} size={11} />
              {currentPerm.label}
            </div>
          </div>

      </div>
    </Modal>
  );
  return createPortal(node, document.body);
}

function SettingRow({ title, desc, control }) {
  return (
    <div className="pl-setting-row">
      <div className="pl-setting-label">
        <strong>{title}</strong>
        <p className="muted">{desc}</p>
      </div>
      <div className="pl-setting-control">{control}</div>
    </div>
  );
}

function SwitchTiny({ on, set }) {
  return <button className={`pl-cap-toggle ${on ? "on" : ""}`} onClick={() => set(!on)} aria-pressed={on} />;
}

// ----------------------- TOAST 容器 (task 14) ----------------------------
// 现象：Game Console 调 window.__apiToast / window.toast 但只落到 console.log，
// 因为 ToastStack 只挂在 Platform Shell，Game Console 页没人渲染它。
// 修法：toast pub/sub + window.toast/__apiToast + pl-toast-stack 渲染已收口到 ./toast.jsx 的
// createToastChannel(与 platform-app 共用工厂)。game 通道逐字保留原 IIFE 语义:
//   · window.toast 仅在未被 Platform 装过时才装(guardWindowToast);
//   · __apiToast 无条件指向本通道 fire(setApiToast,覆盖 api-client 的 console 兜底);
//   · GameToastStack = 本通道的 ToastStack(兼容现有 import { GameToastStack })。
// install() 在模块加载即执行(等价原 IIFE 时机);幂等(__GAME_TOAST 由通道 installed 标记承接)。
const __gameToast = createToastChannel({ name: 'game', setWindowToast: true, guardWindowToast: true, setApiToast: true });
const GameToastStack = __gameToast.ToastStack;
__gameToast.install();

// ---------------------- 历史回顾 / 搜索本档 抽屉 -------------------------
// task 9：之前 TopBar 两个按钮一个空实现、一个 state 设了但没渲染。
// 这里用同一套 pl-modal-backdrop 风格做两个 portal-mount 抽屉。
// 数据源：history（来自 setHistory）、state.memory、state.world，本地纯前端检索。
// 后续后端给出全文搜索接口时，可在 SearchDrawer 内挂 await 调用替换 localSearch。

function HistoryDrawer({ open, history, onClose }) {
  const { t } = useTranslation();
  // Esc 关闭
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  const items = Array.isArray(history) ? history : [];
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose} role="dialog" aria-label={t('game.app.history.aria_label')}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(720px, 100%)", maxHeight: "80vh", display: "flex", flexDirection: "column"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">{t('game.app.history.eyebrow', { count: items.length })}</div>
            <h2 className="pl-modal-title">{t('game.app.history.title')}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip={t('common.close')} aria-label={t('common.close')}><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form" style={{overflow: "auto", paddingTop: 8}}>
          {items.length === 0 ? (
            <div className="muted" style={{padding: "32px 8px", textAlign: "center", fontSize: 13}}>
              {t('game.app.history.empty')}
            </div>
          ) : items.map((h, i) => (
            <div key={`hist-${i}`} className="pl-setting-row" style={{alignItems: "flex-start", gap: 12, padding: "10px 4px", borderBottom: "1px solid var(--line-soft, #eee)"}}>
              <div style={{minWidth: 64, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted-2, #999)"}}>
                {h && h.ts ? h.ts : `#${i + 1}`}
              </div>
              <div style={{flex: 1, minWidth: 0}}>
                <div style={{fontSize: 11, color: "var(--muted, #777)", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.04em"}}>
                  {h && h.role === "assistant" ? "GM" : (h && h.role === "user" ? t('game.app.narrative.player') : (h && h.role) || "—")}
                </div>
                <div className="serif" style={{fontSize: 13, lineHeight: 1.55, whiteSpace: "pre-wrap", wordBreak: "break-word"}}>
                  {/* 展示层 strip JSON ops fence — state.history 原文保留(后端 apply_structured_updates 已落库) */}
                  {stripNarrativeOps((h && h.content) || "")}
                </div>
              </div>
            </div>
          ))}
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            {t('game.app.history.footer_hint')}
          </span>
          <button className="btn ghost" onClick={onClose}>{t('common.close')}</button>
        </footer>
      </div>
    </div>
  );
  return createPortal(node, document.body);
}

function SearchDrawer({ open, history, state, onClose }) {
  const { t } = useTranslation();
  const [q, setQ] = useStateA("");
  const inputRef = React.useRef(null);
  React.useEffect(() => {
    if (!open) return;
    setQ("");
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    const t = setTimeout(() => { try { inputRef.current?.focus(); } catch (_) {} }, 30);
    return () => { window.removeEventListener("keydown", onKey); clearTimeout(t); };
  }, [open, onClose]);

  const results = useMemoA(() => {
    const term = (q || "").trim().toLowerCase();
    if (!term) return [];
    const out = [];
    const push = (group, label, text, meta) => {
      const lc = String(text || "").toLowerCase();
      const idx = lc.indexOf(term);
      if (idx < 0) return;
      const start = Math.max(0, idx - 24);
      const end = Math.min(text.length, idx + term.length + 60);
      out.push({ group, label, snippet: (start > 0 ? "…" : "") + text.slice(start, end) + (end < text.length ? "…" : ""), meta });
    };
    (Array.isArray(history) ? history : []).forEach((h, i) => {
      const role = h && h.role === "assistant" ? "GM" : (h && h.role === "user" ? t('game.app.narrative.player') : "—");
      // 搜索 index 走干净文本,避免搜 "op"/"set" 命中 JSON 而不是叙事
      push(t('game.app.search.group_dialog'), `${role} · #${i + 1}`, stripNarrativeOps((h && h.content) || ""), { i });
    });
    const mem = (state && state.memory) || {};
    if (mem.main_quest) push(t('game.app.search.group_memory'), t('game.app.search.main_quest'), mem.main_quest, {});
    if (mem.current_objective) push(t('game.app.search.group_memory'), t('game.app.search.current_objective'), mem.current_objective, {});
    (Array.isArray(mem.pinned) ? mem.pinned : []).forEach((pinItem, i) => push(t('game.app.search.group_memory'), t('game.app.search.pinned_n', { n: i + 1 }), pinItem, {}));
    const world = (state && state.world) || {};
    (Array.isArray(world.known_events) ? world.known_events : []).forEach((evItem, i) => push(t('game.app.search.group_world'), t('game.app.search.known_event_n', { n: i + 1 }), evItem, {}));
    return out.slice(0, 40);
  }, [q, history, state, t]);

  if (!open) return null;
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose} role="dialog" aria-label={t('game.app.search.aria_label')}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(640px, 100%)", maxHeight: "80vh", display: "flex", flexDirection: "column"}}>
        <header className="pl-modal-head">
          <div style={{flex: 1}}>
            <div className="pl-modal-eyebrow">{t('game.app.search.eyebrow')}</div>
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t('game.app.search.placeholder')}
              aria-label={t('game.app.search.input_aria')}
              style={{width: "100%", marginTop: 6, padding: "8px 10px", fontSize: 14,
                      border: "1px solid var(--line, #ddd)", borderRadius: 6, background: "var(--bg, #fff)"}}
            />
          </div>
          <button className="iconbtn" onClick={onClose} data-tip={t('common.close')} aria-label={t('common.close')}><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form" style={{overflow: "auto", paddingTop: 8}}>
          {!q.trim() ? (
            <div className="muted" style={{padding: "24px 8px", textAlign: "center", fontSize: 13}}>
              {t('game.app.search.empty_hint')}
            </div>
          ) : results.length === 0 ? (
            <div className="muted" style={{padding: "24px 8px", textAlign: "center", fontSize: 13}}>
              {t('game.app.search.no_results_prefix')}"<span style={{color: "var(--text, #333)"}}>{q}</span>"{t('game.app.search.no_results_suffix')}
            </div>
          ) : results.map((r, i) => (
            <div key={`sr-${i}`} className="pl-setting-row" style={{alignItems: "flex-start", gap: 10, padding: "8px 4px", borderBottom: "1px solid var(--line-soft, #eee)"}}>
              <span className="pill" style={{flexShrink: 0, fontSize: 11}}>{r.group}</span>
              <div style={{flex: 1, minWidth: 0}}>
                <div style={{fontSize: 11, color: "var(--muted, #777)", marginBottom: 2}}>{r.label}</div>
                <div style={{fontSize: 12.5, lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word"}}>{r.snippet}</div>
              </div>
            </div>
          ))}
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            {q.trim() ? t('game.app.search.result_count', { count: results.length }) : t('game.app.search.esc_hint')}
          </span>
          <button className="btn ghost" onClick={onClose}>{t('common.close')}</button>
        </footer>
      </div>
    </div>
  );
  return createPortal(node, document.body);
}

// ----------------------------- TOP BAR -----------------------------------
// task 55: 新增 assistantCollapsed / onExpandAssistant —— 助手折叠时显示"展开助手"图标按钮。
function TopBar({ state, saveUpdatedAt, onOpenTweaks, onOpenSearch, onOpenHistory, onOpenSettings, railCollapsed, onExpandRail, panelCollapsed, onExpandPanel, assistantCollapsed, onExpandAssistant, versionSelectEl, onOpenNav }) {
  const { t } = useTranslation();
  // task 49：原 "已存档 · 12 分钟前" 写死。改成读真实 save 的 updated_at（来自 /api/saves）。
  const savedAgo = (saveUpdatedAt && window.__fmt && window.__fmt.ago)
    ? window.__fmt.ago(saveUpdatedAt)
    : (saveUpdatedAt || "—");
  const scriptName = state?._raw?.save_title || state?.app?.script_name || "";
  // M7 修复：state.app.current_chapter 后端从不写，恒 undefined；权威值来自
  // GET /api/saves/:id/timeline 的 current_chapter（game-panels.jsx PanelTimeline 同款读法）。
  // 存档切换时（saveId 变化）才重新拉取，避免每次 render 重复请求。
  const saveId = state?._raw?.save_id ?? null;
  const [currentChapter, setCurrentChapter] = useStateA(null);
  // 白玖实锤:面板「回到此节点」成功后广播 game-state-refresh,但顶栏章号只挂 saveId
  // 依赖 → rewind 后顶栏停在旧章(数据层已回退,纯显示滞后)。监听同一事件重拉。
  const [chapterTick, setChapterTick] = useStateA(0);
  useEffectA(() => {
    const bump = () => setChapterTick((k) => k + 1);
    window.addEventListener("game-state-refresh", bump);
    return () => window.removeEventListener("game-state-refresh", bump);
  }, []);
  useEffectA(() => {
    if (!saveId) { setCurrentChapter(null); return; }
    let cancelled = false;
    const base = (typeof window !== "undefined" && window.__API_BASE) || "";
    fetch(`${base}/api/saves/${saveId}/timeline`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => { if (!cancelled && json) setCurrentChapter(json.current_chapter ?? null); })
      .catch(() => { if (!cancelled) setCurrentChapter(null); });
    return () => { cancelled = true; };
  }, [saveId, chapterTick]);
  const chapter = currentChapter ? t('game.app.topbar.chapter', { n: currentChapter }) : "";
  const phase = state?.world?.timeline?.current_phase || "";
  return (
    <header className="gc-topbar">
      <div className="gc-topbar-left">
        {/* #手机端: 汉堡按钮打开 rail 抽屉(存档/记忆/分支/运行状态),仅移动端显示 */}
        <button className="iconbtn gc-nav-toggle" onClick={onOpenNav} data-tip={t('game.app.topbar.menu_tip')} data-tip-pos="below" aria-label={t('game.app.topbar.open_menu')}>
          <Icon name="menu" size={16} />
        </button>
        {railCollapsed && (
          <button className="iconbtn gc-topbar-expand" onClick={onExpandRail} data-tip={t('game.app.topbar.expand_rail')} data-tip-pos="below">
            <Icon name="chevron_right" size={14} />
          </button>
        )}
        <span className="pill"><span className="dot ok" /> {saveUpdatedAt ? t('game.app.topbar.saved_ago', { ago: savedAgo }) : t('game.app.topbar.unsaved')}</span>
        {versionSelectEl}
      </div>
      <div className="gc-topbar-center">
        {scriptName && <span style={{maxWidth:200,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{scriptName}</span>}
        {chapter && <><span>·</span><span>{chapter}</span></>}
        {phase && <><span>·</span><span style={{color:'var(--text)'}}>{phase}</span></>}
      </div>
      <div className="gc-topbar-right">
        <button className="iconbtn" data-tip={t('game.app.topbar.history_tip')} data-tip-pos="below" onClick={onOpenHistory}><Icon name="history" size={14} /></button>
        <button className="iconbtn" data-tip={t('game.app.topbar.search_tip')} data-tip-pos="below" onClick={onOpenSearch}><Icon name="search" size={14} /></button>
        {/* 导出可读 .txt(当小说分享用)— 链接走 cookie 鉴权,Content-Disposition: attachment 直接下载 */}
        {state?._raw?.save_id ? (
          <a className="iconbtn" download
             href={`${window.__API_BASE || ''}/api/saves/${state._raw.save_id}/export/txt`}
             data-tip={t('game.app.topbar.export_txt_tip')} data-tip-pos="below"
             aria-label={t('game.app.topbar.export_txt_tip')}>
            <Icon name="book" size={14} />
          </a>
        ) : null}
        <button className="iconbtn" data-tip={t('game.app.topbar.settings_tip')} data-tip-pos="below" onClick={onOpenSettings}><Icon name="settings" size={14} /></button>
        {/* 反馈入口 — 玩家遇 bug 时不用切回 Platform tab,直接报。
            runtime-telemetry 已装钩子,提交时自动附带最近 20 errors + 10 失败
            API + 最近对话快照,无需手动复制日志(FeedbackDrawer.jsx:154
            window.__getRuntimeSnapshot({includeRecentDialog: true})) */}
        <button className="iconbtn" data-tip={t('game.app.topbar.feedback_tip')} data-tip-pos="below"
                aria-label={t('game.app.topbar.feedback_tip')}
                onClick={() => {
                  if (window.__openFeedback) window.__openFeedback();
                  else window.dispatchEvent(new CustomEvent('feedback:open'));
                }}>
          <Icon name="message_square" size={14} />
        </button>
        {/* task: 游戏内不再放使用须知按钮(只保留反馈),减少顶栏干扰。
            想看须知到 Platform 点「📖 使用须知」即可 */}
        {/* task 55: 助手折叠时显示展开按钮 */}
        {assistantCollapsed && onExpandAssistant && (
          <button className="iconbtn" data-tip={t('game.app.topbar.expand_assistant')} data-tip-pos="below"
                  aria-label={t('game.app.topbar.expand_assistant')}
                  onClick={onExpandAssistant}>
            <Icon name="sparkle" size={14} />
          </button>
        )}
        {panelCollapsed && (
          <button className="iconbtn gc-topbar-expand-right" data-tip={t('game.app.topbar.expand_panel')} data-tip-pos="below" onClick={onExpandPanel}>
            <Icon name="chevron_left" size={14} />
          </button>
        )}
        {/* task 127: 删 Tweaks 调试按钮 — 用户不要这个内部入口 */}
      </div>
    </header>);

}

export { LeftRail, RunSteps, ThinkingPill, ChatArea, NarrativeBlock, PlayerBlock, TopBar, HistoryDrawer, SearchDrawer, GameToastStack, GameSettingsModal, SaveImagesStrip, renderNarrativeWithInlineTools };
