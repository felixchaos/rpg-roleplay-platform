/* Game Console — main app shell: top bar, left rail, chat area with run-state, right panel. */

const { useState: useStateA, useEffect: useEffectA, useRef: useRefA, useMemo: useMemoA, useCallback: useCallbackA } = React;

// ----------------------------- LEFT RAIL ---------------------------------
function LeftRail({ collapsed, onToggle, state, runState, onNew, onSave, onSwitchSave, onMemoryMode, currentSaveId, saves, resizeHandle }) {
  // task 102E: resizeHandle 是 React 节点 (一般是 <ResizeHandle />),
  // 由 App 层注入,放在 <aside> 内绝对定位
  const m = state.memory || { mode: "normal" };
  const [branchOpen, setBranchOpen] = useStateA(false);
  return (
    <aside className={`gc-rail ${collapsed ? "collapsed" : ""}`} aria-hidden={collapsed}>
      {!collapsed && resizeHandle}
      <div className="gc-rail-inner">
      <div className="gc-rail-head">
        <div className="gc-brand">
          <div className="gc-brand-mark"><Icon name="logo" size={14} /></div>
          <div className="gc-brand-text">
            {/* task 45：剧本名/阶段从真实 state 派生。优先 state._raw.save_title（task 21 写入），
                兜底 state.app.title，最后才走 MOCK_NOVEL（仅 designer offline 还会保留）。 */}
            <strong>{(state && (state._raw?.save_title || state.app?.title)) || (window.MOCK_NOVEL && window.MOCK_NOVEL.script_title) || "RPG Roleplay"}</strong>
            <span className="muted-2" style={{ fontSize: 11 }}>RPG Roleplay · {(state && state.world && state.world.timeline && state.world.timeline.current_phase) || "—"}</span>
          </div>
        </div>
        <button className="iconbtn" onClick={onToggle} data-tip="折叠侧栏" data-tip-pos="below">
          <Icon name="chevron_left" size={14} />
        </button>
      </div>

      <div className="gc-rail-section">
        <div className="gc-rail-section-head">
          <span>当前存档</span>
          <button className="iconbtn" data-tip="新游戏" onClick={onNew}><Icon name="plus" size={12} /></button>
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
                  <strong className="muted">尚未创建存档</strong>
                  <span className="muted-2 mono" style={{fontSize: 11}}>点 ＋ 新建游戏开始</span>
                </>
              );
            }
            return (
              <>
                <strong>{cur.title || `存档 #${cur.id}`}</strong>
                <span className="muted-2 mono">{cur.updated_at || ""}</span>
              </>
            );
          })()}
        </div>
        <div className="gc-rail-quick">
          <button className="btn ghost" onClick={onSave} data-tip="手动保存"><Icon name="save" size={12} /> 保存</button>
          <button className="btn ghost" onClick={() => setBranchOpen(o => !o)} data-tip="切换分支树视图"><Icon name="branch" size={12} /> 分支</button>
        </div>
        {/* task 48：传 currentSaveId / state._raw.save_id，BranchTreeRail 走真 /api/branches */}
        {branchOpen && <BranchTreeRail saveId={currentSaveId || state?._raw?.save_id || null} />}
      </div>

      <div className="gc-rail-section">
        <div className="gc-rail-section-head"><span>记忆模式</span></div>
        <div className="seg gc-mem-seg">
          <button className={m.mode === "normal" ? "active" : ""} data-tip="每轮召回 6 段历史与原文" onClick={() => onMemoryMode?.("normal")}>
            <Icon name="memory" /> 普通
          </button>
          <button className={m.mode === "deep" ? "active" : ""} data-tip="每轮召回 14 段，更慢但更连贯" onClick={() => onMemoryMode?.("deep")}>
            <Icon name="sparkle" /> 深度
          </button>
          <button className={m.mode === "off" ? "active" : ""} data-tip="不召回历史，只用当前上下文" onClick={() => onMemoryMode?.("off")}>
            <Icon name="eye_off" /> 关闭
          </button>
        </div>
        <p className="gc-mem-desc">
          {m.mode === "deep" ? <><strong>深度</strong> · 额外召回 8 段，延迟 +30%</>
            : m.mode === "off" ? <><strong>关闭</strong> · 只用当前面板上下文</>
            : <><strong>普通</strong> · 平衡速度和连贯性</>}
        </p>
      </div>

      {/* task 48：原硬编码两行『memory.facts +1: 童氏与南陵同源』『relationships.沈知微.tone +』。
          改为读 state.memory.last_structured_updates；空就空态。 */}
      {(() => {
        const updates = Array.isArray(state?.memory?.last_structured_updates) ? state.memory.last_structured_updates : [];
        return (
          <div className="gc-rail-section compact">
            <div className="gc-rail-section-head"><span>本轮结构化更新</span><span className="pill mono">{updates.length}</span></div>
            <ul className="gc-rail-updates">
              {updates.length === 0 && (
                <li><span className="muted-2" style={{fontSize: 11.5}}>暂无</span></li>
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

      <div className="gc-rail-section compact">
        <div className="gc-rail-runstate">
          <div className="gc-rail-runstate-line">
            <span className="muted-2" style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em", marginRight: 2 }}>运行</span>
            <span className={`dot ${runState.running ? "accent pulse" : "ok"}`} />
            {runState.running ?
              <span>{runState.label}</span> :
              <span className="muted">空闲 · 等待玩家</span>
            }
          </div>
          {runState.running &&
            <div className="gc-rail-runstate-detail muted-2 mono">{runState.detail}</div>
          }
        </div>
      </div>

      {/* task 129: 详细 rawSteps 移到这里, chat 区只剩一行 thinking pill */}
      <RunDetailRail runState={runState} />

      <div className="gc-rail-foot">
        {/* task 37：CSS 已把 a 改成 inline-flex 占满 foot，icon 的 verticalAlign/marginRight
            可以删掉，避免和 flex align-items 打架（之前是这个让 SVG 视觉外溢、点击命中
            落到父 div，触发 'gc-rail-foot intercepts pointer events'）。 */}
        <a href="Platform.html" className="muted" data-tip="返回平台主页" style={{ fontSize: 12, borderBottom: "0" }}>
          <Icon name="home" size={12} />
          返回主页
        </a>
      </div>
      </div>
    </aside>
  );
}

// ----------------------------- RUN STEPS ---------------------------------
function RunStepsLine({ steps }) {
  return (
    <div className="gc-run gc-run-line">
      {steps.map((s, i) =>
      <div key={i} className={`gc-run-line-row ${s.status}`}>
          <span className={`gc-run-dot ${s.status}`} />
          <span className="gc-run-label">{s.message}</span>
          <span className="muted-2 mono gc-run-elapsed">{(s.elapsed_ms / 1000).toFixed(1)}s</span>
          {s.detail && s.status === "done" &&
        <details className="gc-run-detail">
              <summary className="muted-2"><Icon name="chevron_down" size={10} /> 展开</summary>
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
const PUBLIC_STAGE_LABELS = {
  context: "正在整理剧情上下文",
  rules:   "正在检查规则与权限",
  gm:      "正在生成 GM 回复",
  save:    "正在保存本轮结果",
  system:  "正在处理本轮行动",
};

// task 129: 简化 ThinkingPill — chat 区只显示一行 (Claude 风格),
// 详细 rawSteps 移到 LeftRail (RunDetailRail). 不再 inline 展开 chat。
function ThinkingPill({ runState, runStyle }) {
  const running = !!runState?.running;
  const completedAt = runState?.completedAt || 0;
  const showCompleted = !running && completedAt > 0;
  if (!running && !showCompleted) return null;

  const stageId = runState?.publicStage || "system";
  const label = running
    ? (runState?.label || PUBLIC_STAGE_LABELS[stageId] || PUBLIC_STAGE_LABELS.system)
    : "已完成";
  const elapsedMs = running ? (runState?.totalElapsed || 0) : (runState?.completedElapsed || 0);
  const elapsedSec = (elapsedMs / 1000).toFixed(1);

  return (
    <div className={`gc-think ${running ? "running" : "done"}`}
         aria-live="polite" aria-busy={running}>
      <div className="gc-think-row">
        <span className={`gc-think-dot ${running ? "running" : "done"}`} aria-hidden="true" />
        <span className="gc-think-label">{label}</span>
        <span className="gc-think-elapsed mono muted-2">{elapsedSec}s</span>
      </div>
    </div>
  );
}

// task 129: LeftRail 显示运行详情 (raw phase trace),Claude 同款的展开视图但放左侧
function RunDetailRail({ runState }) {
  const rawSteps = Array.isArray(runState?.rawSteps) ? runState.rawSteps : [];
  const [expanded, setExpanded] = useStateA(false);
  const running = !!runState?.running;
  if (!running && !rawSteps.length) return null;
  const visible = expanded ? rawSteps : rawSteps.slice(-6); // 默认只显示最新 6 步
  return (
    <div className="gc-rail-section compact" style={{ marginTop: 4 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
        <span className="muted-2" style={{ fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em" }}>
          运行详情
        </span>
        {rawSteps.length > 6 && (
          <button className="iconbtn" style={{ padding: "2px 8px", fontSize: 10.5, whiteSpace: "nowrap", width: "auto", height: "auto" }}
            onClick={() => setExpanded(e => !e)}>
            {expanded ? "收起" : `全部 (${rawSteps.length})`}
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
function MsgActions({ text, ts, msgIndex, totalMsgs, commitId, saveId }) {
  // task 38：以前 msgIndex / saveId / commitId 全是 undefined，doFork 就发
  // {label} 给后端 → 后端 int(None) 直接 500。现在 NarrativeBlock / PlayerBlock
  // 把 idx + saveId 透传进来，doFork 至少发 {save_id, message_index, label}，
  // 后端通过 resolve_commit_id_by_message 解析。
  const [copied, setCopied] = useStateA(false);
  const [forkOpen, setForkOpen] = useStateA(false);
  // task 116c: 删除消息 (软回滚) — 弹窗确认 + 进度
  const [delOpen, setDelOpen] = useStateA(false);
  const [delBusy, setDelBusy] = useStateA(false);
  const onCopy = async () => {
    const txt = text || "";
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
      if (ok) window.toast("已复制", { kind: "ok", detail: txt.slice(0, 40) + (txt.length > 40 ? "…" : ""), duration: 1600 });
      else window.toast("复制失败", { kind: "danger", detail: "浏览器拒绝剪贴板访问", duration: 2400 });
    }
    setTimeout(() => setCopied(false), 1400);
  };
  // task 38：禁用条件——必须有 saveId 或 commitId 之一，否则后端无法定位 commit。
  // 缺信息时按钮 disabled + tooltip 解释，比让用户点进去看 toast 失败强。
  const canFork = (commitId != null && commitId !== "") || (saveId != null && msgIndex != null);
  const onFork = () => {
    if (!canFork) {
      window.toast?.("无法新建分支", {
        kind: "warn",
        detail: "缺存档上下文：未拿到 save_id 或消息索引",
        duration: 2400,
      });
      return;
    }
    setForkOpen(true);
  };
  const doFork = async () => {
    setForkOpen(false);
    // 优先 node_id (commitId)；否则发 save_id + message_index 让后端 resolve。
    const body = { label: "从消息分支" };
    if (commitId != null && commitId !== "") {
      body.node_id = commitId;
    } else if (saveId != null && msgIndex != null) {
      body.save_id = saveId;
      body.message_index = msgIndex;
    }
    try {
      const r = await window.api.branches.continueFrom(body);
      if (r && r.ok === false) {
        throw new Error(r.error || r.detail || "后端拒绝创建分支");
      }
      // task 87：后端已经把新分支设为 active ref + 切换 runtime。
      // 必须 dispatch event 让 Game Console 顶层重载 /api/state（chat
      // history / activeSave / right panel / branch tree 全部刷新），
      // 否则用户只看到 toast，UI 完全没动 → 看着像"按了没反应"。
      const newCommitId = r?.active_branch_node_id || r?.active_commit_id;
      const branchHint =
        (r?.active_ref?.name && r.active_ref.name.split("/").pop()) ||
        (newCommitId ? `节点 #${newCommitId}` : "新分支");
      try {
        window.dispatchEvent(new CustomEvent("rpg-state-reload", {
          detail: { reason: "branch_fork", new_commit_id: newCommitId },
        }));
        window.dispatchEvent(new CustomEvent("rpg-saves-updated"));
      } catch (_) {}
      window.toast?.("已切换到新分支", {
        kind: "ok",
        detail: branchHint + " · 当前消息流已是新分支",
        duration: 2400,
      });
    } catch (e) {
      window.toast?.("分支创建失败", { kind: "danger", detail: e?.message, duration: 3000 });
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
        throw new Error(r.error || r.detail || "后端拒绝删除");
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
      const detail = `已删 ${d.messages || 0} 条消息 · 回到第 ${(r?.restored_turn ?? -1) + 1} 回合`
        + (r?.trash_ref ? " · 旧分支已存为 " + (r.trash_ref.name || "trash") : "");
      window.toast?.("消息已删除", { kind: "ok", detail, duration: 3200 });
    } catch (e) {
      window.toast?.("删除失败", { kind: "danger", detail: e?.message, duration: 3000 });
    } finally {
      setDelBusy(false);
    }
  };
  return (
    <>
      <div className="gc-msg-actions">
        <button className="iconbtn gc-msg-act" data-tip={copied ? "已复制" : "复制"} data-tip-pos="below" onClick={onCopy}>
          <Icon name={copied ? "check" : "file"} size={12} />
        </button>
        <button
          className="iconbtn gc-msg-act"
          data-tip={canFork ? "从这里新建分支" : "缺存档上下文，无法分支"}
          data-tip-pos="below"
          disabled={!canFork}
          onClick={onFork}>
          <Icon name="fork" size={12} />
        </button>
        <button
          className="iconbtn gc-msg-act gc-msg-act-danger"
          data-tip={canDelete ? "删除此消息及之后所有(可恢复)" : "缺存档上下文,无法删除"}
          data-tip-pos="below"
          disabled={!canDelete}
          onClick={() => setDelOpen(true)}>
          <Icon name="trash" size={12} />
        </button>
        <span className="gc-msg-ts mono">{ts}</span>
      </div>
      <ForkConfirmModal open={forkOpen} text={text} onClose={() => setForkOpen(false)} onConfirm={doFork} />
      <DeleteConfirmModal
        open={delOpen}
        text={text}
        msgIndex={msgIndex}
        busy={delBusy}
        onClose={() => !delBusy && setDelOpen(false)}
        onConfirm={doDelete}
      />
    </>
  );
}

// task 116c: 删除消息 → 软回滚到 turn N-1 的确认弹窗。
// 警告用户:这会丢弃后续所有对话和世界线;但 git-style 保留了旧分支(refs/trash/...)可恢复。
function DeleteConfirmModal({ open, text, msgIndex, busy, onClose, onConfirm }) {
  if (!open) return null;
  const preview = (text || "").slice(0, 80) + ((text || "").length > 80 ? "…" : "");
  const turnOfMsg = msgIndex != null && msgIndex >= 0 ? Math.floor(msgIndex / 2) : null;
  const restoreTurn = turnOfMsg != null ? turnOfMsg - 1 : null;
  const node = (
    <div className="pl-modal-backdrop" onClick={busy ? null : onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(480px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow" style={{color: "var(--danger)"}}>危险操作</div>
            <h2 className="pl-modal-title">删除此消息及之后所有?</h2>
          </div>
          <button className="iconbtn" onClick={onClose} disabled={busy} data-tip="关闭">
            <Icon name="close" size={14} />
          </button>
        </header>
        <div style={{fontSize: 13.5, lineHeight: 1.7, color: "var(--text-quiet)"}}>
          这是不可逆操作。下面这条消息及其之后的<strong style={{color: "var(--danger)"}}>所有对话、世界线、阶段摘要</strong>都会被丢弃。
          <div style={{
            marginTop: 10, padding: "10px 12px",
            background: "var(--bg-deep)", border: "1px solid var(--line-soft)",
            borderRadius: 6, fontFamily: "var(--font-serif)", fontSize: 13,
            color: "var(--text-quiet)", borderLeft: "2px solid var(--danger)",
          }}>
            {preview || "(空消息)"}
          </div>
          <div style={{marginTop: 10, fontSize: 12, color: "var(--muted)"}}>
            {restoreTurn != null && restoreTurn >= 0
              ? <>存档会回到 <strong>第 {restoreTurn + 1} 回合</strong> 结束时的状态。</>
              : <>存档会回到 <strong>开局前</strong> 的状态。</>}
            <br />
            旧分支会自动保留在 <code style={{fontFamily: "var(--font-mono)", fontSize: 11}}>refs/trash/...</code>,
            通过分支树可以切回去恢复。
          </div>
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> POST /api/branches/rollback
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose} disabled={busy}>取消</button>
            <button className="btn danger" onClick={onConfirm} disabled={busy}>
              {busy
                ? <><span className="gc-spinner spin" /> 删除中…</>
                : <><Icon name="trash" size={12} /> 确认删除</>}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
  return ReactDOM.createPortal(node, document.body);
}

function ForkConfirmModal({ open, text, onClose, onConfirm }) {
  if (!open) return null;
  const preview = (text || "").slice(0, 80) + ((text || "").length > 80 ? "…" : "");
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(460px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">从这条消息新建分支</div>
            <h2 className="pl-modal-title">在此节点开新分支</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div style={{fontSize: 13.5, lineHeight: 1.7, color: "var(--text-quiet)"}}>
          当前节点之后的消息会保留在原分支，新分支从这里继续。
          <div style={{
            marginTop: 10, padding: "10px 12px",
            background: "var(--bg-deep)", border: "1px solid var(--line-soft)",
            borderRadius: 6, fontFamily: "var(--font-serif)", fontSize: 13,
            color: "var(--text-quiet)", borderLeft: "2px solid var(--accent-edge)",
          }}>
            {preview}
          </div>
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> POST /api/branches/continue
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            <button className="btn primary" onClick={onConfirm}>
              <Icon name="fork" size={12} /> 新建分支
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
  return ReactDOM.createPortal(node, document.body);
}

function stripStateOpsForDisplay(text) {
  return String(text || "")
    .replace(/```(?:json|state-ops|state)?\s*\n?\s*(?:\{[\s\S]*?\}|\[[\s\S]*?\])\s*\n?```/g, "")
    .trim();
}

function NarrativeBlock({ text, streaming, ts, msgIndex, saveId, commitId, thinking }) {
  const displayText = stripStateOpsForDisplay(text);
  // task 90: 用 RpgMarkdown.Block 渲染 markdown (** / # / list / code / link...)
  // window.RpgMarkdown 由 markdown-render.jsx 提供,加载顺序在 game-app.jsx 之前。
  const MdBlock = (typeof window !== "undefined" && window.RpgMarkdown && window.RpgMarkdown.Block) || null;
  // task 121a: thinking 状态显示带 spinner 的 italic 文字,跟正式 narrative 区分
  if (thinking) {
    return (
      <div className="gc-msg gc-msg-gm gc-msg-thinking">
        <div className="gc-msg-meta">
          <span className="gc-msg-tag">GM</span>
          <span className="muted-2" style={{ fontSize: 11.5 }}>正在准备</span>
        </div>
        <div className="gc-msg-body" style={{ fontStyle: "italic", color: "var(--text-quiet)", opacity: 0.85 }}>
          <span className="gc-spinner spin" /> {text || "请稍候…"}
        </div>
      </div>
    );
  }
  return (
    <div className="gc-msg gc-msg-gm">
      <div className="gc-msg-meta">
        <span className="gc-msg-tag">GM</span>
        <span className="muted-2" style={{ fontSize: 11.5 }}>主代理</span>
      </div>
      <div className="gc-msg-body serif">
        {MdBlock
          ? <MdBlock text={displayText || ""} streaming={!!streaming} className="rpg-md" />
          : (displayText || "").split(/\n\n+/).map((p, i) =>
              <p key={i}>{p}{streaming && i === (displayText || "").split(/\n\n+/).length - 1 && <span className="gc-cursor" />}</p>
            )
        }
      </div>
      {!streaming && <MsgActions text={displayText} ts={ts || "—"} msgIndex={msgIndex} saveId={saveId} commitId={commitId} />}
    </div>);

}

function PlayerBlock({ text, ts, attachments, msgIndex, saveId, commitId }) {
  return (
    <div className="gc-msg gc-msg-player">
      <div className="gc-msg-meta">
        <span className="gc-msg-tag muted">玩家</span>
      </div>
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
      <MsgActions text={text} ts={ts} msgIndex={msgIndex} saveId={saveId} commitId={commitId} />
    </div>);

}

function ChatArea({ history, runState, runStyle, narrativeFont, narrativeSize, hasError, errorMessage, saveId, onRetry, onShowSse }) {
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

  // task 133: Claude 风格自动滚动 — 用户上滚后停止跟随 + 回到底部按钮
  const isAtBottomRef = useRefA(true);
  const [showJumpBtn, setShowJumpBtn] = useStateA(false);
  // 用户滚动时检测是否离开底部
  useEffectA(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => {
      const threshold = 80;  // 距底部 80px 内算"在底部"
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
      isAtBottomRef.current = atBottom;
      setShowJumpBtn(!atBottom);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  // 新内容时:只在用户原本就在底部才跟随,否则不动 (Claude 风格 scroll lock)
  useEffectA(() => {
    if (!ref.current) return;
    if (!isAtBottomRef.current) return;  // 用户主动上滚 → 不强制跟随
    const id = requestAnimationFrame(() => {
      if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
  }, [visible.length, runState.running, runState.rawSteps?.length]);
  const jumpToBottom = () => {
    if (!ref.current) return;
    ref.current.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
    isAtBottomRef.current = true;
    setShowJumpBtn(false);
  };

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
            隐藏了较早的 {hiddenCount} 条 ·{" "}
            <a href="#" onClick={(e) => { e.preventDefault(); setExtra(x => x + HISTORY_WINDOW); }}>
              再加载 {Math.min(HISTORY_WINDOW, hiddenCount)} 条
            </a>
            {" · "}
            <span className="muted">完整历史走顶栏「历史回顾」抽屉</span>
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
            streaming={!m.streaming_done && idx === totalLen - 1 && runState.running} /> :
          <PlayerBlock key={`pl-${idx}`} text={m.content} ts={m.ts} attachments={m.attachments}
            msgIndex={idx} saveId={saveId} commitId={commitId} />;
        })}

        {/* task 92：原 gc-run-wrap 直接渲染 runState.steps，把后端 raw phase trace
            （prompt / intent / llm_curator / manifest / provider:xxx / assembly /
            rules_engine / main_gm / acceptance_check ...）整页铺给玩家。
            改用 ThinkingPill：一行高层进度 + "已完成 · X.Xs" 短暂收尾；
            详情藏在折叠里，玩家好奇时再点开看 rawSteps。 */}
        <ThinkingPill runState={runState} runStyle={runStyle} />

        {hasError &&
        <div className="gc-error">
            <Icon name="warn" size={14} style={{ color: "var(--danger)" }} />
            <div>
              <strong>生成失败</strong>
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12.5 }}>
                {/* task 31：以前这里硬编码"请求中断：上游 504"，把空消息/字段契约错全都误报成网络超时。
                    现在显示后端 error.message 的真实文本（hasError 为字符串时是错误正文，为 true 时回退）。 */}
                {(typeof hasError === "string" && hasError) || errorMessage || "请求中断。已保留你的上一条输入，可重试或修改。"}
              </p>
              <div className="gc-error-actions">
                <button className="btn" onClick={onRetry} disabled={!onRetry}>重试本轮</button>
                <button className="btn ghost" onClick={onShowSse} disabled={!onShowSse}>查看 SSE</button>
              </div>
            </div>
          </div>
        }
      </div>
      {/* task 133: Claude 风格"回到底部"浮按钮 — 用户上滚时显示 */}
      {showJumpBtn && (
        <button
          onClick={jumpToBottom}
          className="btn"
          style={{
            position: "absolute", right: "calc(50% - 70px)", bottom: 24,
            background: "var(--panel)", border: "1px solid var(--line)",
            borderRadius: 999, padding: "6px 14px", fontSize: 12.5,
            boxShadow: "var(--shadow-3, 0 6px 18px -6px rgba(0,0,0,0.5))",
            zIndex: 5, cursor: "pointer",
          }}
          data-tip="跳到最新">
          <Icon name="chevron_down" size={12} /> 回到最新
        </button>
      )}
    </div>);

}

// VSCode-style branch tree inline in the rail
// 用户要求"一个存档一个 git 系统",UI 一模一样 VSCode Git Graph。
// 后端已经是完整 git 语义 (branch_commits + branch_refs + parent_id 树),
// 前端这里只做 wrapper:拉 /api/branches/{saveId},喂给 BranchGraph 组件
// (variant="compact" 紧凑型,适合右侧栏)。
function BranchTreeRail({ saveId }) {
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
        if (!cancelled) setData({ loading: false, payload: null, error: e?.message || "加载失败" });
      }
    })();
    return () => { cancelled = true; };
  }, [saveId, refreshTick]);
  const nodes = (data.payload && data.payload.nodes) || [];
  return (
    <div className="gc-rail-branch-tree">
      <div className="gc-rail-branch-head">
        <span className="muted-2 mono" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em"}}>当前子分支</span>
        <span className="muted-2 mono" style={{fontSize: 10.5, marginLeft: "auto"}}>HEAD 历史</span>
        <a className="iconbtn" href="Platform.html#saves-branches"
           target="_blank" rel="noopener noreferrer"
           data-tip="在新标签打开完整分支图(查看所有分支路线)" data-tip-pos="below"
           style={{width: 18, height: 18}}>
          <Icon name="arrow_right" size={10} />
        </a>
      </div>
      {data.loading && <div className="muted-2" style={{padding: "10px 8px", fontSize: 11.5}}>加载中…</div>}
      {!data.loading && data.error && (
        <div className="muted-2" style={{padding: "10px 8px", fontSize: 11.5}}>加载失败：{data.error}</div>
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
              if (r && r.ok === false) throw new Error(r.error || r.detail || "切换分支失败");
              window.__apiToast?.("已切到该分支", { kind: "ok", duration: 1500 });
              window.dispatchEvent(new CustomEvent("rpg-state-reload"));
              window.dispatchEvent(new CustomEvent("rpg-saves-updated"));
            } catch (e) {
              window.__apiToast?.("切换分支失败", { kind: "danger", detail: e?.message || String(e) });
            }
          }}
          onContinue={async (commitId) => {
            try {
              const r = await window.api.branches.continueFrom({ node_id: commitId });
              if (r && r.ok === false) throw new Error(r.error || r.detail || "从此节点继续失败");
              window.__apiToast?.("已从此节点新建分支", { kind: "ok", duration: 1500 });
              window.dispatchEvent(new CustomEvent("rpg-state-reload"));
              window.dispatchEvent(new CustomEvent("rpg-saves-updated"));
            } catch (e) {
              window.__apiToast?.("从此节点继续失败", { kind: "danger", detail: e?.message || String(e) });
            }
          }}
        />
      )}
    </div>
  );
}

// ----------------------------- IN-GAME SETTINGS --------------------------
// task 89：原本这里有 9 个开关（自动存档/敏感前置/行动建议/显示运行步骤/
// 显示思考过程/低风险自动放行/流式速度/失败重试/静音），但全是 local
// useState，没接 /api/me/preference，没影响任何游戏行为，"保存到本档"
// 按钮 onClick={onClose} 实际只关弹窗。等于 9 个装饰品 + 1 个谎言按钮。
//
// 改成诚实版：明确告诉用户"本档专属设置仍在开发"，列出当前已经在哪些
// 地方真正生效的偏好（全局设置面板里的扣子，task 64/85 加的 extractor /
// curator threshold 等），并提供新标签直达全局设置的链接。
// 未来真的有本档级 override 时再加回来 toggle，且必须 wire 到后端 prefs。
function GameSettingsModal({ open, onClose, saveTitle }) {
  if (!open) return null;
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(520px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">游戏内设置 · 本档</div>
            <h2 className="pl-modal-title">{saveTitle || "本档设置"}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form" style={{paddingTop: 4}}>
          <div style={{
            padding: "10px 12px", marginBottom: 12,
            background: "var(--warn-soft, rgba(212,179,102,0.12))",
            border: "1px solid var(--warn, #d4a857)", borderRadius: 6,
            fontSize: 12.5, color: "var(--text-quiet)", lineHeight: 1.7,
          }}>
            本档专属设置（autosave / 行动建议 / 流式速度 / 自动重试等）正在重做。
            当前对游戏行为生效的开关请在 <strong>全局设置</strong> 调整：
          </div>
          <ul style={{margin: 0, padding: "0 0 0 18px", fontSize: 13, color: "var(--text-quiet)", lineHeight: 2}}>
            <li>偏好 → 界面语言 / 叙述字体 / 自动存档（生效）</li>
            <li>偏好 → <strong>叙事提取器</strong>（task 64：GM 第二步开关 + API + 模型）</li>
            <li>偏好 → <strong>Curator 反问阈值</strong>（task 85：confidence 低于此值跳过主 GM 直接问玩家）</li>
            <li>写入权限 → 默认权限模式 + 审计日志</li>
            <li>模型 → 切换主 GM / 子代理 / 提取器使用的模型与 API</li>
          </ul>
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> 本面板不再有假开关
          </span>
          <div style={{display: "flex", gap: 8}}>
            <a className="btn ghost" href="Platform.html#settings"
               target="_blank" rel="noopener noreferrer"
               style={{textDecoration: "none"}}>
              <Icon name="settings" size={12} /> 打开全局设置 ↗
            </a>
            <button className="btn primary" onClick={onClose}>
              <Icon name="check" size={12} /> 知道了
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
  return ReactDOM.createPortal(node, document.body);
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
// 修法：在 game-app.jsx 里独立装 toast pub/sub + 复用 platform 的 pl-toast-stack 样式，
// 由 Game Console 渲染 <GameToastStack/>。如果 platform-app.jsx 已经先一步装了 window.toast
// （例如某些跨页面共用脚本场景），不重复挂；当前 Game Console.html 不载入 platform-app.jsx，
// 所以这里 register 一定生效。
(function () {
  if (window.__GAME_TOAST_INSTALLED) return;
  const listeners = [];
  let nextId = 1;
  const fire = (msg, opts) => {
    const t = {
      id: ++nextId,
      kind: (opts && opts.kind) || "ok",
      message: msg,
      detail: (opts && opts.detail) || null,
      duration: (opts && Number.isFinite(opts.duration)) ? opts.duration : 2400,
      action: opts && opts.action,
    };
    listeners.forEach((fn) => fn(t));
    return t.id;
  };
  // 不覆盖 Platform 已注入的同名函数（同源容错）
  if (typeof window.toast !== "function") window.toast = fire;
  // api-client.js 在加载时也设过 __apiToast = local fallback，这里再覆盖为真正可见版本
  window.__apiToast = fire;
  window.__gameToastSubscribe = (fn) => {
    listeners.push(fn);
    return () => {
      const i = listeners.indexOf(fn);
      if (i >= 0) listeners.splice(i, 1);
    };
  };
  window.__GAME_TOAST_INSTALLED = true;
})();

function GameToastStack() {
  const [items, setItems] = useStateA([]);
  React.useEffect(() => {
    const unsub = window.__gameToastSubscribe((t) => {
      setItems((arr) => [...arr, t]);
      if (t.duration > 0) {
        setTimeout(() => setItems((arr) => arr.filter((x) => x.id !== t.id)), t.duration);
      }
    });
    return unsub;
  }, []);
  const dismiss = (id) => setItems((arr) => arr.filter((x) => x.id !== id));
  if (!items.length) return null;
  const node = (
    <div className="pl-toast-stack" aria-live="polite">
      {items.map((t) => (
        <div key={`toast-${t.id}`} className={`pl-toast pl-toast-${t.kind}`}>
          <span className={`pl-toast-icon dot ${t.kind === "ok" ? "ok" : t.kind === "warn" ? "warn" : t.kind === "danger" ? "danger" : "info"}`} />
          <div className="pl-toast-body">
            <div className="pl-toast-msg">{t.message}</div>
            {t.detail && <div className="pl-toast-detail muted-2">{t.detail}</div>}
          </div>
          {t.action && (
            <button className="pl-toast-action" onClick={() => { try { t.action.onClick && t.action.onClick(); } catch (_) {} dismiss(t.id); }}>
              {t.action.label}
            </button>
          )}
          <button className="iconbtn pl-toast-close" onClick={() => dismiss(t.id)} aria-label="关闭">
            <Icon name="close" size={11} />
          </button>
        </div>
      ))}
    </div>
  );
  return ReactDOM.createPortal(node, document.body);
}

// ---------------------- 历史回顾 / 搜索本档 抽屉 -------------------------
// task 9：之前 TopBar 两个按钮一个空实现、一个 state 设了但没渲染。
// 这里用同一套 pl-modal-backdrop 风格做两个 portal-mount 抽屉。
// 数据源：history（来自 setHistory）、state.memory、state.world，本地纯前端检索。
// 后续后端给出全文搜索接口时，可在 SearchDrawer 内挂 await 调用替换 localSearch。

function HistoryDrawer({ open, history, onClose }) {
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
    <div className="pl-modal-backdrop" onClick={onClose} role="dialog" aria-label="历史回顾">
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(720px, 100%)", maxHeight: "80vh", display: "flex", flexDirection: "column"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">本档历史 · {items.length} 条</div>
            <h2 className="pl-modal-title">历史回顾</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭" aria-label="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form" style={{overflow: "auto", paddingTop: 8}}>
          {items.length === 0 ? (
            <div className="muted" style={{padding: "32px 8px", textAlign: "center", fontSize: 13}}>
              这一档还没有对话历史。开始与 GM 对话后，所有轮次会在这里聚合可回看。
            </div>
          ) : items.map((h, i) => (
            <div key={`hist-${i}`} className="pl-setting-row" style={{alignItems: "flex-start", gap: 12, padding: "10px 4px", borderBottom: "1px solid var(--line-soft, #eee)"}}>
              <div style={{minWidth: 64, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted-2, #999)"}}>
                {h && h.ts ? h.ts : `#${i + 1}`}
              </div>
              <div style={{flex: 1, minWidth: 0}}>
                <div style={{fontSize: 11, color: "var(--muted, #777)", marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.04em"}}>
                  {h && h.role === "assistant" ? "GM" : (h && h.role === "user" ? "玩家" : (h && h.role) || "—")}
                </div>
                <div className="serif" style={{fontSize: 13, lineHeight: 1.55, whiteSpace: "pre-wrap", wordBreak: "break-word"}}>
                  {(h && h.content) || ""}
                </div>
              </div>
            </div>
          ))}
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            提示：Esc 关闭 · 当前为本会话内存历史；完整分支历史见 Platform / 分支页
          </span>
          <button className="btn ghost" onClick={onClose}>关闭</button>
        </footer>
      </div>
    </div>
  );
  return ReactDOM.createPortal(node, document.body);
}

function SearchDrawer({ open, history, state, onClose }) {
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
      const role = h && h.role === "assistant" ? "GM" : (h && h.role === "user" ? "玩家" : "—");
      push("对话", `${role} · #${i + 1}`, (h && h.content) || "", { i });
    });
    const mem = (state && state.memory) || {};
    if (mem.main_quest) push("记忆", "主线", mem.main_quest, {});
    if (mem.current_objective) push("记忆", "当前目标", mem.current_objective, {});
    (Array.isArray(mem.pinned) ? mem.pinned : []).forEach((t, i) => push("记忆", `固定 #${i + 1}`, t, {}));
    const world = (state && state.world) || {};
    (Array.isArray(world.known_events) ? world.known_events : []).forEach((t, i) => push("世界", `已知事件 #${i + 1}`, t, {}));
    return out.slice(0, 40);
  }, [q, history, state]);

  if (!open) return null;
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose} role="dialog" aria-label="搜索本档">
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(640px, 100%)", maxHeight: "80vh", display: "flex", flexDirection: "column"}}>
        <header className="pl-modal-head">
          <div style={{flex: 1}}>
            <div className="pl-modal-eyebrow">本档搜索 · 前端聚合（对话 / 记忆 / 世界）</div>
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="输入关键词，回车即可…"
              aria-label="搜索关键词"
              style={{width: "100%", marginTop: 6, padding: "8px 10px", fontSize: 14,
                      border: "1px solid var(--line, #ddd)", borderRadius: 6, background: "var(--bg, #fff)"}}
            />
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭" aria-label="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form" style={{overflow: "auto", paddingTop: 8}}>
          {!q.trim() ? (
            <div className="muted" style={{padding: "24px 8px", textAlign: "center", fontSize: 13}}>
              输入关键词搜索本档对话历史 / 记忆 / 已知事件。<br />
              （后端全文检索接口接入前，先做前端本地匹配。）
            </div>
          ) : results.length === 0 ? (
            <div className="muted" style={{padding: "24px 8px", textAlign: "center", fontSize: 13}}>
              没有匹配 "<span style={{color: "var(--text, #333)"}}>{q}</span>" 的条目。
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
            {q.trim() ? `匹配 ${results.length} 条（最多展示 40 条）` : "Esc 关闭"}
          </span>
          <button className="btn ghost" onClick={onClose}>关闭</button>
        </footer>
      </div>
    </div>
  );
  return ReactDOM.createPortal(node, document.body);
}

// ----------------------------- TOP BAR -----------------------------------
// task 55: 新增 assistantCollapsed / onExpandAssistant —— 助手折叠时显示"展开助手"图标按钮。
function TopBar({ state, saveUpdatedAt, onOpenTweaks, onOpenSearch, onOpenHistory, onOpenSettings, railCollapsed, onExpandRail, panelCollapsed, onExpandPanel, assistantCollapsed, onExpandAssistant }) {
  // task 49：原 "已存档 · 12 分钟前" 写死。改成读真实 save 的 updated_at（来自 /api/saves）。
  const savedAgo = (saveUpdatedAt && window.__fmt && window.__fmt.ago)
    ? window.__fmt.ago(saveUpdatedAt)
    : (saveUpdatedAt || "—");
  return (
    <header className="gc-topbar">
      <div className="gc-topbar-left">
        {railCollapsed && (
          <button className="iconbtn gc-topbar-expand" onClick={onExpandRail} data-tip="展开侧栏" data-tip-pos="below">
            <Icon name="chevron_right" size={14} />
          </button>
        )}
        <span className="pill"><span className="dot ok" /> {saveUpdatedAt ? `已存档 · ${savedAgo}` : "尚未保存"}</span>
      </div>
      <div className="gc-topbar-right">
        <button className="iconbtn" data-tip="历史回顾" data-tip-pos="below" onClick={onOpenHistory}><Icon name="history" size={14} /></button>
        <button className="iconbtn" data-tip="搜索本档" data-tip-pos="below" onClick={onOpenSearch}><Icon name="search" size={14} /></button>
        <button className="iconbtn" data-tip="游戏内设置" data-tip-pos="below" onClick={onOpenSettings}><Icon name="settings" size={14} /></button>
        {/* task 55: 助手折叠时显示展开按钮 */}
        {assistantCollapsed && onExpandAssistant && (
          <button className="iconbtn" data-tip="展开控制台助手" data-tip-pos="below"
                  aria-label="展开控制台助手"
                  onClick={onExpandAssistant}>
            <Icon name="sparkle" size={14} />
          </button>
        )}
        {panelCollapsed && (
          <button className="iconbtn gc-topbar-expand-right" data-tip="展开右侧面板" data-tip-pos="below" onClick={onExpandPanel}>
            <Icon name="chevron_left" size={14} />
          </button>
        )}
        {/* task 127: 删 Tweaks 调试按钮 — 用户不要这个内部入口 */}
      </div>
    </header>);

}

Object.assign(window, { LeftRail, RunSteps, ThinkingPill, ChatArea, TopBar, HistoryDrawer, SearchDrawer, GameToastStack });
