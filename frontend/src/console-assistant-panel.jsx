/*
 * ConsoleAssistantPanel — 控制台助手侧栏
 * ---------------------------------------------------------------
 * 模仿 Claude in Chrome 的右侧助手侧栏，挂载在 Platform / Game Console。
 * 通讯协议（POST /api/console_assistant/chat，SSE 流）：
 *   event: meta                 data: {conversation_id, trace_id}
 *   event: token                data: {text | delta}
 *   event: tool_call            data: {call_id, tool, args, server_id?}
 *   event: tool_result          data: {call_id, ok, result?, error?}
 *   event: confirmation_required data: {call_id, tool, args, description, destructive: true}
 *   event: error                data: {message}
 *   event: done                 data: {summary?}
 *
 * 后端未就绪时自动走 mock SSE（顶部小开关）。
 * 暴露：window.ConsoleAssistantPanel = ConsoleAssistantPanel;
 */
(function () {
  const { useState, useEffect, useRef, useMemo, useCallback } = React;

  // ---------------- styles (inject once) -----------------------
  // task 55: 挤压式布局 —— 折叠时完全不可见。
  // task 60: 1:1 复刻 Claude in Chrome 风格:
  //   - cap-head 高度 72px 对齐 platform topbar (.pl-topbar height:72px),
  //     背景 rgba(26,24,23,0.92) + backdrop-filter:blur(12px) 同款,
  //     border-bottom 用 --line-soft 同款,无渐变。
  //   - 头部加 model dropdown + ask-before-acting toggle
  //   - foot 加免责文案
  //   - 气泡 radius 12px,fontSize 13.5/14
  const CSS_ID = "cap-styles-v5";
  if (typeof document !== "undefined" && !document.getElementById(CSS_ID)) {
    // 清掉旧版本样式(若存在),避免旧规则覆盖
    ["cap-styles-v1", "cap-styles-v2", "cap-styles-v3", "cap-styles-v4"].forEach((oid) => {
      const old = document.getElementById(oid);
      if (old && old.parentNode) old.parentNode.removeChild(old);
    });
    const css = `
/* task 93: sticky → 跟随滚动时吸附顶部不滑走, 与左 sidebar (.pl-side) 行为一致
   task 102D: 宽度可拖,默认 360, min 280, max 640 (CSS var --cap-w 由 JSX 内联) */
.cap-root{position:sticky;top:0;align-self:start;height:100vh;width:var(--cap-w,360px);flex-shrink:0;
  background:var(--panel,#211f1d);border-left:1px solid var(--line-soft,#2a2724);
  display:flex;flex-direction:column;z-index:50;
  /* 拖动中不要 transition 防抖动;只 close/open 时过渡 */
  box-shadow:-12px 0 32px -20px rgba(0,0,0,.55);color:var(--text,#ebe7df);
  font-family:var(--font-sans,system-ui);font-size:13.5px;overflow:hidden}
.cap-root:not(.cap-resizing){transition:width .2s cubic-bezier(.2,.7,.2,1),border-left-color .2s}
.cap-root.cap-closed{width:0;border-left-color:transparent;
  display:none;pointer-events:none}
.cap-root-inner{width:100%;flex-shrink:0;display:flex;flex-direction:column;height:100%}
/* task 102D/103: 助手浮窗左边缘拖动手柄 (放在边缘内,避免被 overflow:hidden 切掉) */
.cap-resize-handle{position:absolute;top:0;left:0;bottom:0;width:5px;cursor:col-resize;z-index:60;
  background:transparent;transition:background .15s;touch-action:none}
.cap-resize-handle:hover,.cap-resize-handle:active{background:var(--accent,#c46a4a);opacity:.45}
@media (pointer:coarse){.cap-resize-handle{width:12px}}

/* task 60: 头部高度对齐 platform topbar (72px),背景/border 一致 */
.cap-head{display:flex;align-items:center;gap:8px;padding:0 14px;height:72px;
  flex-shrink:0;
  border-bottom:1px solid var(--line-soft,#2a2724);
  background:rgba(26,24,23,0.92);backdrop-filter:blur(12px);
  position:relative}
.cap-head-left{display:flex;flex-direction:column;flex:1;min-width:0;gap:2px}
.cap-head-title-row{display:flex;align-items:center;gap:6px;min-width:0}
.cap-title{font-weight:600;font-size:14px;letter-spacing:.02em;min-width:0;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text,#ebe7df)}
.cap-dot{width:6px;height:6px;border-radius:50%;background:var(--muted-2,#6b655e);
  display:inline-block;flex-shrink:0}
.cap-dot.cap-on{background:var(--ok,#7eb88e);box-shadow:0 0 0 2px var(--ok-soft,rgba(126,184,142,.12))}
.cap-head-actions{display:flex;gap:2px;align-items:center;flex-shrink:0}
.cap-icon-btn{background:transparent;border:0;color:var(--muted,#968f85);cursor:pointer;
  padding:5px 7px;border-radius:5px;font-size:13px;line-height:1;
  display:inline-flex;align-items:center;justify-content:center}
.cap-icon-btn:hover{background:var(--panel-2,#282623);color:var(--text,#ebe7df)}

/* task 60: 模型下拉 (Haiku 4.5 ▼ 风格) */
.cap-model-pick{display:inline-flex;align-items:center;gap:4px;
  background:transparent;border:0;color:var(--muted,#968f85);
  font-size:11.5px;cursor:pointer;padding:1px 4px;border-radius:3px;
  font-family:var(--font-mono,ui-monospace,monospace);
  max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cap-model-pick:hover{color:var(--text-quiet,#c8c2b7);background:var(--panel-2,#282623)}
.cap-model-pick .cap-caret{font-size:9px;flex-shrink:0;opacity:.7}
.cap-model-pop{position:absolute;top:64px;left:14px;right:14px;z-index:60;
  background:var(--panel-2,#282623);border:1px solid var(--line,#36322d);
  border-radius:8px;box-shadow:0 12px 28px -10px rgba(0,0,0,.6);
  max-height:340px;overflow:auto;padding:4px}
.cap-model-pop-empty{padding:14px;text-align:center;font-size:12px;color:var(--muted,#968f85)}
.cap-model-item{display:flex;flex-direction:column;gap:1px;padding:7px 10px;border-radius:5px;
  cursor:pointer;font-size:12.5px;color:var(--text,#ebe7df)}
.cap-model-item:hover{background:var(--panel-3,#2f2c28)}
.cap-model-item.cap-on{background:var(--accent-soft,rgba(201,100,66,.14));
  color:var(--accent,#c96442)}
.cap-model-item-sub{font-size:10.5px;color:var(--muted,#968f85);
  font-family:var(--font-mono,ui-monospace,monospace)}

.cap-body{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:12px;
  scroll-behavior:smooth}
.cap-body::-webkit-scrollbar{width:6px}
.cap-body::-webkit-scrollbar-thumb{background:var(--line,#36322d);border-radius:3px}

/* task 60: 气泡 radius 12px、字号微调 */
.cap-msg{display:flex;flex-direction:column;max-width:90%;font-size:13.5px;line-height:1.6}
.cap-msg-user{align-self:flex-end;align-items:flex-end}
.cap-msg-assistant{align-self:flex-start;align-items:flex-start;max-width:96%}
.cap-bubble{padding:9px 12px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word;
  word-break:break-word}
.cap-msg-user .cap-bubble{background:var(--accent-soft,rgba(201,100,66,.14));
  border:1px solid var(--accent-edge,rgba(201,100,66,.42));color:var(--text,#ebe7df);
  font-size:13.5px}
.cap-msg-assistant .cap-bubble{background:transparent;border:0;padding:2px 0;
  color:var(--text,#ebe7df);font-size:14px;line-height:1.65}
.cap-bubble code{font-family:var(--font-mono,ui-monospace,monospace);font-size:12.5px;
  background:var(--bg-deep,#131211);border:1px solid var(--line-soft,#2a2724);
  padding:1px 5px;border-radius:4px}
.cap-bubble pre{font-family:var(--font-mono,ui-monospace,monospace);font-size:12px;
  background:var(--bg-deep,#131211);border:1px solid var(--line-soft,#2a2724);
  padding:8px 10px;border-radius:6px;margin:6px 0;overflow-x:auto;line-height:1.5}
.cap-bubble pre code{background:transparent;border:0;padding:0;font-size:inherit}
.cap-bubble strong{font-weight:600;color:var(--text,#ebe7df)}
.cap-bubble em{font-style:italic;color:var(--text-quiet,#c8c2b7)}
.cap-cursor{display:inline-block;width:6px;height:14px;background:var(--accent,#c96442);
  margin-left:2px;vertical-align:text-bottom;animation:cap-blink 1s steps(2) infinite}
@keyframes cap-blink{50%{opacity:0}}
.cap-meta{font-size:11px;color:var(--muted,#968f85);margin-top:4px;padding:0 4px}

.cap-tool{align-self:stretch;border:1px solid var(--line-soft,#2a2724);border-radius:10px;
  background:var(--panel-2,#282623);overflow:hidden;font-size:12.5px}
.cap-tool-head{display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:pointer;
  user-select:none;background:var(--panel-2,#282623)}
.cap-tool-head:hover{background:var(--panel-3,#2f2c28)}
.cap-tool-icon{flex-shrink:0;font-size:13px;line-height:1}
.cap-tool-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  font-family:var(--font-mono,monospace);font-size:12px;color:var(--text-quiet,#c8c2b7)}
/* task 60: 状态用小色点 */
.cap-tool-status-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;
  background:var(--info,#7aa6c2);box-shadow:0 0 0 2px var(--info-soft,rgba(122,166,194,.18))}
.cap-tool-status-dot.cap-running{background:var(--info,#7aa6c2);
  box-shadow:0 0 0 2px var(--info-soft,rgba(122,166,194,.18));animation:cap-pulse 1.4s ease-in-out infinite}
.cap-tool-status-dot.cap-done{background:var(--ok,#7eb88e);
  box-shadow:0 0 0 2px var(--ok-soft,rgba(126,184,142,.18))}
.cap-tool-status-dot.cap-error{background:var(--danger,#c8675d);
  box-shadow:0 0 0 2px var(--danger-soft,rgba(200,103,93,.18))}
@keyframes cap-pulse{0%,100%{opacity:1}50%{opacity:.45}}
.cap-tool-status{font-size:10.5px;padding:2px 6px;border-radius:10px;
  text-transform:uppercase;letter-spacing:.05em}
.cap-tool-status.cap-running{background:var(--info-soft,rgba(122,166,194,.12));
  color:var(--info,#7aa6c2)}
.cap-tool-status.cap-done{background:var(--ok-soft,rgba(126,184,142,.12));color:var(--ok,#7eb88e)}
.cap-tool-status.cap-error{background:var(--danger-soft,rgba(200,103,93,.12));
  color:var(--danger,#c8675d)}
.cap-tool-caret{color:var(--muted,#968f85);font-size:10px;transition:transform .15s}
.cap-tool.cap-open .cap-tool-caret{transform:rotate(90deg)}
.cap-tool-body{padding:8px 10px;border-top:1px solid var(--line,#36322d);
  background:var(--bg-deep,#131211);font-family:var(--font-mono,monospace);
  font-size:11.5px;color:var(--text-quiet,#c8c2b7)}
.cap-tool-body pre{margin:0 0 8px 0;white-space:pre-wrap;word-break:break-word;
  max-height:200px;overflow:auto}
.cap-tool-body pre:last-child{margin-bottom:0}
.cap-tool-label{font-size:10.5px;color:var(--muted,#968f85);letter-spacing:.05em;
  text-transform:uppercase;margin:0 0 3px 0}

.cap-confirm{align-self:stretch;border:1px solid var(--danger,#c8675d);border-radius:8px;
  background:var(--danger-soft,rgba(200,103,93,.12));padding:10px 12px;display:flex;
  flex-direction:column;gap:8px}
.cap-confirm-title{font-weight:600;color:var(--danger,#c8675d);font-size:12.5px}
.cap-confirm-body{font-size:12.5px;line-height:1.5}
.cap-confirm-tool{font-family:var(--font-mono,monospace);font-size:11.5px;
  color:var(--text-quiet,#c8c2b7);background:var(--bg-deep,#131211);padding:6px 8px;
  border-radius:4px;border:1px solid var(--line,#36322d);
  word-break:break-all;white-space:pre-wrap;max-height:120px;overflow:auto}
.cap-confirm-desc{color:var(--text,#ebe7df);font-size:12.5px}
.cap-confirm-actions{display:flex;gap:8px;justify-content:flex-end}
.cap-btn{border:1px solid var(--line-strong,#4a4540);background:var(--panel,#211f1d);
  color:var(--text,#ebe7df);padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px}
.cap-btn:hover{background:var(--panel-2,#282623)}
.cap-btn-danger{background:var(--danger,#c8675d);border-color:var(--danger,#c8675d);color:#fff}
.cap-btn-danger:hover{background:#b25a51}
.cap-btn:disabled{opacity:.5;cursor:not-allowed}
.cap-confirm-resolved{font-size:11.5px;color:var(--muted,#968f85);font-style:italic}

.cap-err{align-self:stretch;border:1px solid var(--danger,#c8675d);
  background:var(--danger-soft,rgba(200,103,93,.12));color:var(--danger,#c8675d);
  padding:8px 10px;border-radius:6px;font-size:12.5px}

/* task 93: 思考中指示 — 三个点 + 文字, 给用户明确"LLM 在工作"信号 */
.cap-thinking{display:flex;align-items:center;gap:5px;padding:6px 4px;color:var(--muted,#968f85);font-size:12.5px}
.cap-thinking-dot{width:5px;height:5px;border-radius:50%;background:var(--accent,#c96442);
  animation:cap-think-pulse 1.1s ease-in-out infinite}
.cap-thinking-dot:nth-child(2){animation-delay:.18s}
.cap-thinking-dot:nth-child(3){animation-delay:.36s}
.cap-thinking-label{margin-left:4px}
@keyframes cap-think-pulse{0%,80%,100%{opacity:.25;transform:scale(.7)}40%{opacity:1;transform:scale(1)}}

.cap-empty{color:var(--muted,#968f85);text-align:center;margin:auto;padding:24px 12px;
  font-size:12.5px;line-height:1.6}
.cap-empty-hint{color:var(--muted-2,#6b655e);font-size:11.5px;margin-top:6px}

.cap-foot{border-top:1px solid var(--line-soft,#2a2724);padding:10px 12px 8px;
  background:var(--panel,#211f1d);display:flex;flex-direction:column;gap:6px}
.cap-context{font-size:10.5px;color:var(--muted-2,#6b655e);
  font-family:var(--font-mono,monospace);overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
/* task 60: ask-before-acting toggle */
.cap-ask-row{display:flex;align-items:center;justify-content:space-between;gap:6px;
  font-size:11.5px;color:var(--muted,#968f85);padding:2px 0}
.cap-ask-label{display:inline-flex;align-items:center;gap:6px;cursor:pointer;user-select:none}
.cap-ask-label:hover{color:var(--text-quiet,#c8c2b7)}
.cap-ask-switch{position:relative;display:inline-block;width:28px;height:14px;
  background:var(--panel-3,#2f2c28);border:1px solid var(--line,#36322d);
  border-radius:8px;transition:background .15s,border-color .15s;flex-shrink:0}
.cap-ask-switch::after{content:"";position:absolute;top:1px;left:1px;width:10px;height:10px;
  border-radius:50%;background:var(--muted,#968f85);transition:left .15s,background .15s}
.cap-ask-switch.cap-on{background:var(--accent-soft,rgba(201,100,66,.14));
  border-color:var(--accent-edge,rgba(201,100,66,.42))}
.cap-ask-switch.cap-on::after{left:15px;background:var(--accent,#c96442)}
.cap-ask-hint{font-size:10.5px;color:var(--muted-2,#6b655e)}

.cap-input-row{display:flex;gap:6px;align-items:flex-end}
/* task 116: 风格与游戏 .gc-composer 统一 — 圆角 12px + 焦点 glow */
.cap-input{flex:1;min-height:36px;max-height:140px;resize:none;border:1px solid var(--line,#36322d);
  background:var(--bg-deep,#131211);color:var(--text,#ebe7df);border-radius:12px;
  padding:9px 11px;font-family:inherit;font-size:13.5px;line-height:1.4;outline:none;
  transition:border-color .15s ease,box-shadow .15s ease,background .15s ease;
  box-shadow:0 1px 0 rgba(0,0,0,.04)}
.cap-input:focus{border-color:var(--accent-edge,rgba(201,100,66,.42));
  box-shadow:0 1px 0 rgba(0,0,0,.04),0 0 0 3px rgba(201,100,66,.06)}
.cap-send-btn{border:0;background:var(--accent,#c96442);color:#fff;padding:8px 14px;
  border-radius:10px;cursor:pointer;font-size:12.5px;font-weight:500;white-space:nowrap}
.cap-send-btn:hover{filter:brightness(1.08)}
.cap-send-btn:disabled{opacity:.5;cursor:not-allowed;filter:none}
.cap-stop-btn{background:var(--danger,#c8675d)}

/* task 60: 底部免责文案 */
.cap-disclaimer{text-align:center;font-size:10.5px;color:var(--muted-2,#6b655e);
  padding:2px 0 0;letter-spacing:.02em}

.cap-settings-pop{position:absolute;top:42px;right:8px;background:var(--panel-2,#282623);
  border:1px solid var(--line,#36322d);border-radius:8px;padding:10px;width:220px;z-index:1;
  box-shadow:0 8px 24px -10px rgba(0,0,0,.6)}
.cap-settings-pop label{display:flex;align-items:center;gap:6px;font-size:12px;
  color:var(--text-quiet,#c8c2b7);padding:4px 0;cursor:pointer}
.cap-settings-pop input[type="checkbox"]{accent-color:var(--accent,#c96442)}
.cap-settings-pop hr{border:0;border-top:1px solid var(--line-soft,#2a2724);margin:6px 0}
.cap-settings-pop .cap-setting-row{font-size:11.5px;color:var(--muted,#968f85)}

/* convmgr1: ContextRing — SVG token usage ring */
.cap-ctx-ring{position:relative;display:inline-flex;align-items:center;justify-content:center;
  flex-shrink:0;cursor:default}
.cap-ctx-ring svg{display:block}
.cap-ctx-ring-label{position:absolute;font-size:9px;font-weight:600;
  font-family:var(--font-mono,ui-monospace,monospace);
  color:var(--text-quiet,#c8c2b7);letter-spacing:-.02em;line-height:1;
  pointer-events:none;user-select:none}
.cap-ctx-ring[title]:hover .cap-ctx-ring-label{color:var(--text,#ebe7df)}

/* convmgr1: 对话列表下拉面板 */
.cap-conv-pop{position:absolute;top:72px;left:0;right:0;z-index:70;
  background:var(--panel-2,#282623);border-bottom:1px solid var(--line,#36322d);
  border-left:1px solid var(--line,#36322d);border-right:1px solid var(--line,#36322d);
  box-shadow:0 12px 28px -10px rgba(0,0,0,.6);
  max-height:300px;overflow-y:auto;padding:4px}
.cap-conv-pop::-webkit-scrollbar{width:5px}
.cap-conv-pop::-webkit-scrollbar-thumb{background:var(--line,#36322d);border-radius:3px}
.cap-conv-item{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:6px;
  cursor:pointer;transition:background .12s}
.cap-conv-item:hover{background:var(--panel-3,#2f2c28)}
.cap-conv-item.cap-active{background:var(--accent-soft,rgba(201,100,66,.14));
  outline:1px solid var(--accent-edge,rgba(201,100,66,.3))}
.cap-conv-item-body{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.cap-conv-preview{font-size:12px;color:var(--text-quiet,#c8c2b7);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cap-conv-meta{font-size:10.5px;color:var(--muted,#968f85);
  font-family:var(--font-mono,ui-monospace,monospace)}
.cap-conv-del{background:transparent;border:0;color:var(--muted-2,#6b655e);
  font-size:12px;cursor:pointer;padding:3px 6px;border-radius:4px;flex-shrink:0;
  line-height:1}
.cap-conv-del:hover{color:var(--danger,#c8675d);background:var(--danger-soft,rgba(200,103,93,.12))}
.cap-conv-empty{padding:16px;text-align:center;font-size:12px;color:var(--muted,#968f85)}

/* task 61: ask_user_choice — 结构化选择题卡片 */
.cap-choices{align-self:stretch;border:1px solid var(--line,#36322d);
  background:var(--panel-2,#282623);border-radius:10px;padding:12px;
  display:flex;flex-direction:column;gap:10px}
.cap-choices-q{font-weight:600;color:var(--text,#ebe7df);font-size:13px;line-height:1.5}
.cap-choices-ctx{font-size:11.5px;color:var(--muted,#968f85);font-style:italic;line-height:1.4}
.cap-choices-chips{display:flex;flex-wrap:wrap;gap:6px}
.cap-chip{background:var(--panel,#211f1d);border:1px solid var(--line-strong,#4a4540);
  color:var(--text,#ebe7df);padding:6px 12px;border-radius:18px;cursor:pointer;
  font-size:12.5px;transition:background .15s,border-color .15s,color .15s;
  font-family:inherit;line-height:1.3}
.cap-chip:hover{background:var(--accent-soft,rgba(201,100,66,.14));
  border-color:var(--accent-edge,rgba(201,100,66,.42));color:var(--text,#ebe7df)}
.cap-chip-free{border-style:dashed;color:var(--muted-2,#6b655e)}
.cap-chip-free:hover{color:var(--text,#ebe7df)}
.cap-choices.cap-answered{opacity:.6}
.cap-choices.cap-answered .cap-chip{cursor:default;pointer-events:none}
.cap-choices-answered-tag{color:var(--accent,#c96442);font-size:11.5px;font-weight:500;
  display:flex;align-items:center;gap:4px}
/* task 70: 权限菜单 — 对齐游戏 PermissionPopover 4 模式 */
.cap-perm-pill{position:relative;display:inline-flex;align-items:center;gap:6px;
  border:1px solid var(--line,#36322d);background:var(--bg-2,#1a1817);
  border-radius:9999px;padding:3px 10px;font-size:11.5px;color:var(--muted,#968f85);
  cursor:pointer;user-select:none}
.cap-perm-pill:hover{color:var(--text,#ebe7df);border-color:var(--accent-edge,rgba(201,100,66,.42))}
.cap-perm-pill[data-mode="full_access"]{color:#82c98a;border-color:#3b6a40}
.cap-perm-pill[data-mode="read_only"]{color:#d0b673;border-color:#6e5b2f}
.cap-perm-pop{position:absolute;bottom:calc(100% + 6px);left:0;z-index:5;
  background:var(--bg-2,#1a1817);border:1px solid var(--line,#36322d);
  border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.5);
  min-width:260px;padding:6px;display:flex;flex-direction:column;gap:2px}
.cap-perm-opt{display:flex;flex-direction:column;align-items:flex-start;gap:2px;
  background:transparent;border:0;color:var(--text,#ebe7df);text-align:left;
  padding:8px 10px;border-radius:6px;cursor:pointer;width:100%}
.cap-perm-opt:hover{background:var(--accent-soft,rgba(201,100,66,.14))}
.cap-perm-opt.active{background:var(--accent-soft,rgba(201,100,66,.20));
  outline:1px solid var(--accent-edge,rgba(201,100,66,.42))}
.cap-perm-opt-row{display:flex;justify-content:space-between;align-items:center;
  width:100%;font-size:13px;font-weight:600}
.cap-perm-opt-desc{font-size:11.5px;color:var(--muted-2,#6b655e);line-height:1.4}
.cap-perm-opt-check{color:var(--accent,#c96442)}
`;
    const style = document.createElement("style");
    style.id = CSS_ID;
    style.textContent = css;
    document.head.appendChild(style);
  }

  // ---------------- mock SSE for offline / unready backend -----
  function mockChat(body, handlers) {
    let cancelled = false;
    const emit = (ev, data) => {
      if (cancelled) return;
      try {
        if (handlers["on_" + ev]) handlers["on_" + ev](data);
        if (handlers.onEvent) handlers.onEvent({ event: ev, data });
      } catch (_) {}
    };
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));

    const text = (body && body.message) || "";
    const wantsDestructive = /(删除|destruct|reset|drop|清空|删档)/i.test(text);
    // task 61: mock 触发选择题 — 关键字 "选择" / "choice" / "性格"
    const wantsChoice = /(选择|choice|性格|分支|路线)/i.test(text);

    (async () => {
      await wait(120);
      emit("meta", { conversation_id: "mock-" + Date.now(), trace_id: "trc-mock" });
      await wait(180);

      const reply1 = "正在分析你的请求…\n（这是 mock SSE 流，后端 endpoint 未就绪。）";
      for (const ch of reply1) {
        if (cancelled) break;
        emit("token", { text: ch });
        await wait(14);
      }
      if (cancelled) { emit("done", { interrupted: true }); return; }

      await wait(200);
      emit("tool_call", { call_id: "c1", tool: "platform.list_saves",
        args: { user_id: "demo" }, server_id: "rpg-dispatcher" });
      await wait(420);
      emit("tool_result", { call_id: "c1", ok: true,
        result: { saves: [{ id: 1, title: "示例存档" }] } });
      await wait(150);

      if (wantsChoice) {
        await wait(200);
        emit("user_choice_required", {
          call_id: "ch1",
          tool: "ask_user_choice",
          question: "demo: 你想选哪种性格?",
          options: ["开朗元气", "冷静腹黑", "傲娇内向", "温柔治愈"],
          allow_free_text: true,
          context: "这是 mock 流, 真实情况由 LLM 调 ask_user_choice 工具触发",
        });
        emit("done", { summary: "mock 等用户选择" });
        return;
      }

      if (wantsDestructive) {
        emit("confirmation_required", {
          call_id: "c2",
          tool: "platform.delete_save",
          args: { save_id: 1 },
          description: "此操作会永久删除存档 #1（示例存档）。该动作不可撤销。",
          destructive: true,
        });
        // 等用户裁决
        const decision = await new Promise((resolve) => {
          handlers.__pendingMockResolve = resolve;
          if (cancelled) resolve("reject");
        });
        if (decision === "approve") {
          await wait(300);
          emit("tool_call", { call_id: "c2", tool: "platform.delete_save",
            args: { save_id: 1 }, server_id: "rpg-dispatcher" });
          await wait(280);
          emit("tool_result", { call_id: "c2", ok: true, result: { deleted: true } });
        } else {
          await wait(120);
          emit("tool_result", { call_id: "c2", ok: false, error: "用户取消了破坏性操作" });
        }
      }

      if (cancelled) { emit("done", { interrupted: true }); return; }
      await wait(140);
      const reply2 = "\n\n好了，已经替你看过相关信息。需要继续操作请告诉我。";
      for (const ch of reply2) {
        if (cancelled) break;
        emit("token", { text: ch });
        await wait(10);
      }
      emit("done", { summary: "mock 完成" });
    })();

    return {
      stop: () => { cancelled = true; if (handlers.__pendingMockResolve) handlers.__pendingMockResolve("reject"); },
      done: Promise.resolve(),
      isMock: true,
    };
  }

  // ---------------- helpers ------------------------------------
  function abbreviate(obj, max) {
    max = max || 64;
    let s;
    try { s = typeof obj === "string" ? obj : JSON.stringify(obj); } catch (_) { s = String(obj); }
    if (!s) return "{}";
    if (s.length <= max) return s;
    return s.slice(0, max - 1) + "…";
  }
  function tryStringify(v) {
    if (v === undefined || v === null) return "";
    if (typeof v === "string") return v;
    try { return JSON.stringify(v, null, 2); } catch (_) { return String(v); }
  }

  // ---------------- markdown-ish renderer ----------------------
  // 极简 markdown: **bold**, *italic*, `code`, ```code block```
  function renderMarkdownish(text) {
    if (!text) return null;
    const parts = [];
    // 先把 ``` 块切出来
    const codeBlockRe = /```([\s\S]*?)```/g;
    let lastIdx = 0;
    let m;
    let key = 0;
    while ((m = codeBlockRe.exec(text)) !== null) {
      if (m.index > lastIdx) {
        parts.push(renderInline(text.slice(lastIdx, m.index), key++));
      }
      parts.push(<pre key={key++}><code>{m[1]}</code></pre>);
      lastIdx = m.index + m[0].length;
    }
    if (lastIdx < text.length) {
      parts.push(renderInline(text.slice(lastIdx), key++));
    }
    return parts;
  }
  function renderInline(text, baseKey) {
    // 把 **xxx** / *xxx* / `xxx` 拆 token
    const tokens = [];
    const re = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`)/g;
    let lastIdx = 0;
    let m;
    let k = 0;
    while ((m = re.exec(text)) !== null) {
      if (m.index > lastIdx) tokens.push(text.slice(lastIdx, m.index));
      if (m[2] !== undefined) tokens.push(<strong key={`${baseKey}b${k++}`}>{m[2]}</strong>);
      else if (m[3] !== undefined) tokens.push(<em key={`${baseKey}i${k++}`}>{m[3]}</em>);
      else if (m[4] !== undefined) tokens.push(<code key={`${baseKey}c${k++}`}>{m[4]}</code>);
      lastIdx = m.index + m[0].length;
    }
    if (lastIdx < text.length) tokens.push(text.slice(lastIdx));
    return <React.Fragment key={baseKey}>{tokens}</React.Fragment>;
  }

  // ---------------- ToolCard -----------------------------------
  function ToolCard({ entry }) {
    const [open, setOpen] = useState(false);
    const statusCls = entry.status === "done" ? "cap-done"
      : entry.status === "error" ? "cap-error" : "cap-running";
    const statusLabel = entry.status === "done" ? "done"
      : entry.status === "error" ? "error" : "running";
    return (
      <div className={"cap-tool" + (open ? " cap-open" : "")}>
        <div className="cap-tool-head" onClick={() => setOpen(o => !o)}>
          <span className={"cap-tool-status-dot " + statusCls} title={statusLabel} />
          <span className="cap-tool-name">{entry.tool}({abbreviate(entry.args, 28)})</span>
          <span className="cap-tool-caret">▶</span>
        </div>
        {open && (
          <div className="cap-tool-body">
            <p className="cap-tool-label">args</p>
            <pre>{tryStringify(entry.args) || "{}"}</pre>
            {entry.status !== "running" && (
              <>
                <p className="cap-tool-label">{entry.status === "error" ? "error" : "result"}</p>
                <pre>{entry.status === "error" ? tryStringify(entry.error) : tryStringify(entry.result)}</pre>
              </>
            )}
            {entry.server_id && (
              <p className="cap-tool-label" style={{ marginTop: 6 }}>via {entry.server_id}</p>
            )}
          </div>
        )}
      </div>
    );
  }

  // ---------------- ModelDropdown ------------------------------
  // task 60: 头部模型下拉。
  //  · 读取 /api/me/profile → preferences.console_assistant_model_override (dict {api_id, model})
  //  · 空时回落到 /api/models 的 catalog.selected (主 GM)
  //  · 选择后 POST /api/me/preference {console_assistant_model_override: {api_id, model}}
  //  · "跟随主 GM" 选项写 null 以删除覆盖
  function ModelDropdown({ apiBase }) {
    const [open, setOpen] = useState(false);
    const [catalog, setCatalog] = useState(null);
    const [override, setOverride] = useState(null); // {api_id, model} | null
    const [busy, setBusy] = useState(false);
    const popRef = useRef(null);

    // 首次加载
    useEffect(() => {
      let alive = true;
      (async () => {
        try {
          // profile (preferences)
          if (window.api && window.api.account && window.api.account.profile) {
            const profile = await window.api.account.profile();
            const ov = profile && profile.preferences && profile.preferences.console_assistant_model_override;
            if (alive && ov && typeof ov === "object" && (ov.api_id || ov.model)) {
              setOverride({ api_id: ov.api_id, model: ov.model });
            }
          }
        } catch (_) {}
        try {
          if (window.api && window.api.models && window.api.models.list) {
            const r = await window.api.models.list();
            const realCat = (r && r.models && Array.isArray(r.models.apis)) ? r.models : r;
            if (alive && realCat) setCatalog(realCat);
          }
        } catch (_) {}
      })();
      return () => { alive = false; };
    }, [apiBase]);

    // 点外面关菜单
    useEffect(() => {
      if (!open) return;
      const onDoc = (e) => {
        if (popRef.current && !popRef.current.contains(e.target)) setOpen(false);
      };
      document.addEventListener("mousedown", onDoc);
      return () => document.removeEventListener("mousedown", onDoc);
    }, [open]);

    const apis = (catalog && Array.isArray(catalog.apis)) ? catalog.apis : [];
    const flat = [];
    apis.forEach((api) => {
      if (api && api.enabled === false) return;
      const aid = api.api_id || api.id;
      (api.models || []).forEach((m) => {
        if (m && m.enabled !== false) {
          flat.push({
            api_id: aid,
            real_name: m.real_name || m.id,
            display: m.display_name || m.real_name || m.id,
            api_label: api.display_name || aid,
          });
        }
      });
    });

    const selected = catalog && catalog.selected;
    const mainSel = selected ? { api_id: selected.api_id, model: selected.model_id || selected.real_name } : null;
    const effective = override || mainSel;
    const effectiveItem = effective
      ? flat.find(f => f.api_id === effective.api_id && f.real_name === effective.model)
      : null;
    const label = effectiveItem ? effectiveItem.display : (effective ? effective.model : "默认模型");

    const onPick = async (item) => {
      setBusy(true);
      try {
        if (window.api && window.api.account && window.api.account.preferences) {
          await window.api.account.preferences({
            console_assistant_model_override: item ? { api_id: item.api_id, model: item.real_name } : null,
          });
          setOverride(item ? { api_id: item.api_id, model: item.real_name } : null);
          if (window.__apiToast) window.__apiToast(
            item ? `助手模型 → ${item.display}` : "助手模型已重置为跟随主 GM",
            { kind: "ok", duration: 1500 });
        }
      } catch (e) {
        if (window.__apiToast) window.__apiToast("切换失败", { kind: "danger", detail: e && e.message });
      } finally {
        setBusy(false);
        setOpen(false);
      }
    };

    const inheritKey = "__inherit__";
    const curKey = override ? `${override.api_id}::${override.model}` : inheritKey;

    return (
      <>
        <button className="cap-model-pick" onClick={() => setOpen(o => !o)} disabled={busy}
                title={effective ? `${effective.api_id} · ${effective.model}` : "选择模型"}>
          <span>{label}</span>
          <span className="cap-caret">▼</span>
        </button>
        {open && (
          <div className="cap-model-pop" ref={popRef} onClick={(e) => e.stopPropagation()}>
            <div className={"cap-model-item" + (curKey === inheritKey ? " cap-on" : "")}
                 onClick={() => onPick(null)}>
              <span>跟随主 GM</span>
              <span className="cap-model-item-sub">
                {mainSel ? `${mainSel.api_id} · ${mainSel.model}` : "未设置主模型"}
              </span>
            </div>
            {flat.length === 0 && (
              <div className="cap-model-pop-empty">没有可用模型</div>
            )}
            {flat.map((m) => {
              const key = `${m.api_id}::${m.real_name}`;
              const active = key === curKey;
              return (
                <div key={key}
                     className={"cap-model-item" + (active ? " cap-on" : "")}
                     onClick={() => onPick(m)}>
                  <span>{m.display}</span>
                  <span className="cap-model-item-sub">{m.api_label} · {m.real_name}</span>
                </div>
              );
            })}
          </div>
        )}
      </>
    );
  }

  // ---------------- ConfirmationCard ---------------------------
  function ConfirmationCard({ entry, onDecide, busy }) {
    const resolved = entry.decided;
    return (
      <div className="cap-confirm" role="alertdialog" aria-label="二次确认">
        <div className="cap-confirm-title">需要你确认</div>
        <div className="cap-confirm-body">
          助手想执行: <code>{entry.tool}({abbreviate(entry.args, 80)})</code>
        </div>
        {entry.description && <div className="cap-confirm-desc">{entry.description}</div>}
        <div className="cap-confirm-tool">{tryStringify(entry.args)}</div>
        {!resolved ? (
          <div className="cap-confirm-actions">
            <button className="cap-btn" disabled={busy} onClick={() => onDecide(entry, "reject")}>取消</button>
            <button className="cap-btn cap-btn-danger" disabled={busy} onClick={() => onDecide(entry, "approve")}>确认执行</button>
          </div>
        ) : (
          <div className="cap-confirm-resolved">已{resolved === "approve" ? "确认" : "取消"}</div>
        )}
      </div>
    );
  }

  // ---------------- ChoicesCard --------------------------------
  // task 61: ask_user_choice 渲染 — 按钮组 chip + 可选自由输入按钮。
  // 用户点选项 → 自动以 "我选: <option>" 发新消息触发下一轮 chat。
  // 点自由输入 → focus 输入框,placeholder 改成"或者直接描述你的想法…"。
  // 选完后 answered=true 卡片变灰展示已选答案。
  function ChoicesCard({ entry, onPick, onFreeText }) {
    const answered = !!entry.answered;
    return (
      <div className={"cap-choices" + (answered ? " cap-answered" : "")}
           role="group" aria-label="结构化选择题">
        <div className="cap-choices-q">{entry.question}</div>
        {entry.context && (
          <div className="cap-choices-ctx">{entry.context}</div>
        )}
        <div className="cap-choices-chips">
          {(entry.options || []).map((opt, i) => (
            <button key={i} className="cap-chip" type="button"
                    disabled={answered}
                    onClick={() => onPick(entry, opt)}>
              {opt}
            </button>
          ))}
          {entry.allow_free_text && (
            <button className="cap-chip cap-chip-free" type="button"
                    disabled={answered}
                    onClick={() => onFreeText(entry)}>
              自由输入…
            </button>
          )}
        </div>
        {answered && (
          <div className="cap-choices-answered-tag">
            已选择: {entry.answered_value || "(自由输入)"}
          </div>
        )}
      </div>
    );
  }

  // ---------------- ContextRing --------------------------------
  // convmgr1: 显示累积 token 使用率的 SVG 圆环。
  // pct=0 时只显示灰色背景环,无文字;pct>0 后显示百分比。
  function ContextRing({ cumIn, cumOut, ctxLimit }) {
    const total = (cumIn || 0) + (cumOut || 0);
    const limit = ctxLimit || 0;
    const pct = (limit > 0) ? Math.min(total / limit, 1) : 0;
    const pctInt = Math.round(pct * 100);

    const R = 10;         // SVG 半径
    const SZ = 26;        // 直径 px
    const CX = SZ / 2;
    const CY = SZ / 2;
    const strokeW = 2.5;
    const r = R - strokeW / 2;
    const circ = 2 * Math.PI * r;
    const dash = pct * circ;

    const fgColor = pct < 0.5
      ? "var(--ok,#7eb88e)"
      : pct < 0.8
        ? "var(--accent,#c96442)"
        : "var(--danger,#c8675d)";

    const titleStr = limit > 0
      ? `上下文使用: ${total.toLocaleString()} / ${limit.toLocaleString()} tokens (${pctInt}%)\n剩余: ${(limit - total).toLocaleString()} tokens`
      : "上下文: 暂无数据";

    return (
      <span className="cap-ctx-ring" title={titleStr} aria-label={`上下文使用率 ${pctInt}%`}>
        <svg width={SZ} height={SZ} viewBox={`0 0 ${SZ} ${SZ}`}>
          {/* 背景环 */}
          <circle cx={CX} cy={CY} r={r}
            fill="none"
            stroke="var(--line,#36322d)"
            strokeWidth={strokeW} />
          {/* 前景弧 */}
          {pct > 0 && (
            <circle cx={CX} cy={CY} r={r}
              fill="none"
              stroke={fgColor}
              strokeWidth={strokeW}
              strokeDasharray={`${dash} ${circ}`}
              strokeLinecap="round"
              transform={`rotate(-90 ${CX} ${CY})`}
              style={{ transition: "stroke-dasharray .4s ease, stroke .3s" }}
            />
          )}
        </svg>
        {pct > 0 && (
          <span className="cap-ctx-ring-label">{pctInt}%</span>
        )}
      </span>
    );
  }

  // ---------------- helper: relative time ----------------------
  function relativeTime(isoStr) {
    if (!isoStr) return "";
    let d;
    try { d = new Date(isoStr); } catch (_) { return ""; }
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    return Math.floor(diff / 86400) + " 天前";
  }

  // ---------------- main component -----------------------------
  function ConsoleAssistantPanel(props) {
    // task 48 fix: apiBase 默认从 window.__API_BASE 取 (api-client.js 已设),
    // 让前端 5173 静态服务也能 fetch 7860 后端,否则 ping 一直 404 永久 mock。
    const _defaultBase = (typeof window !== "undefined" && window.__API_BASE) || "";
    // task 55: 现在 open/onClose 由父组件控制 (受控模式),
    // defaultOpen 仅作为非受控兜底 (老入口未传 open 时使用)。
    const {
      defaultOpen = false,
      open: openProp,
      onClose,
      pageContext = null,
      apiBase = _defaultBase,
    } = props || {};
    const isControlled = openProp !== undefined;
    const [openState, setOpenState] = useState(!!defaultOpen);
    const open = isControlled ? !!openProp : openState;
    const setOpen = useCallback((next) => {
      const v = typeof next === "function" ? next(open) : next;
      if (!isControlled) setOpenState(v);
      if (!v && onClose) onClose();
    }, [isControlled, onClose, open]);
    // task 109: 同步 cap 占位宽度到 :root --cap-effective-w —
    // 让 modal-backdrop 能 right: var(--cap-effective-w) 留出助手区,
    // 用户在 modal 里也能看见+交互助手 (产品哲学: 助手永远在线)
    React.useEffect(() => {
      const root = document.documentElement.style;
      if (open) root.setProperty("--cap-effective-w", capW + "px");
      else root.setProperty("--cap-effective-w", "0px");
      return () => { root.setProperty("--cap-effective-w", "0px"); };
    }, [open, capW]);
    // task 102D/104: 助手浮窗宽度可拖, 拖动期间直写 :root --cap-w 绕过 React
    // (panel 是 1000+ 行组件, setState 每帧重渲会严重卡顿)
    const _useResizable = (typeof window !== "undefined" && window.useResizable);
    const resizable = _useResizable ? _useResizable({
      storageKey: "cap.width",
      defaultSize: 360,
      min: 280,
      max: 640,
      side: "right",
      cssVar: "--cap-w",
    }) : { size: 360, dragHandleProps: {} };
    const capW = resizable.size;
    const capDragProps = resizable.dragHandleProps;
    // task 109: open + capW 同步到 :root --cap-effective-w  ↓ 在 open 算完后挂
    const [showSettings, setShowSettings] = useState(false);
    const [useMock, setUseMock] = useState(true); // 默认 mock；下方 effect 探测后端
    const [autoMockReason, setAutoMockReason] = useState("初始化");
    // task 71: localStorage 持久化 chat — 刷新后恢复 conv_id + messages,
    // 避免之前那种 "F5 一次掉光对话" 的问题。
    const CAP_PERSIST_KEY = "cap.transcript.v1";
    const loadPersisted = () => {
      try {
        const raw = localStorage.getItem(CAP_PERSIST_KEY);
        if (!raw) return { messages: [], convId: null };
        const obj = JSON.parse(raw);
        if (!obj || !Array.isArray(obj.messages)) return { messages: [], convId: null };
        // 24h 之前的丢掉 (避免 stale conv 被后端 GC 后还在前端打转)
        if (obj.ts && Date.now() - obj.ts > 24 * 3600 * 1000) return { messages: [], convId: null };
        return { messages: obj.messages, convId: obj.convId || null };
      } catch (_) {
        return { messages: [], convId: null };
      }
    };
    const initial = loadPersisted();
    const [messages, setMessages] = useState(initial.messages); // {type, ts, ...}
    const [input, setInput] = useState("");
    const [running, setRunning] = useState(false);
    const [convId, setConvId] = useState(initial.convId);
    // 任一者变更就 flush 进 localStorage
    useEffect(() => {
      try {
        localStorage.setItem(CAP_PERSIST_KEY, JSON.stringify({
          messages, convId, ts: Date.now(),
        }));
      } catch (_) {}
    }, [messages, convId]);
    // task 60 → task 70: 权限模式 — 4 档,对齐游戏控制台 PermissionPopover。
    //   default     : 默认 (destructive 弹 confirm)
    //   read_only   : 只读 (auto-reject 任何 destructive)
    //   auto_review : 自动审查 (同 default,占位语义)
    //   full_access : 完全访问 (destructive 自动 approve,不弹 confirm)
    // 旧 askBeforeActing 兼容: 老 localStorage 值 "1" → 升级到 "default";
    // 其它 → default。
    const PERM_MODE_KEY = "cap.permission_mode.v1";
    const [permissionMode, setPermissionModeState] = useState(() => {
      try {
        const m = localStorage.getItem(PERM_MODE_KEY);
        if (m && ["default", "read_only", "auto_review", "full_access"].includes(m)) return m;
      } catch (_) {}
      return "default";
    });
    const setPermissionMode = (m) => {
      setPermissionModeState(m);
      try { localStorage.setItem(PERM_MODE_KEY, m); } catch (_) {}
    };
    // ref 镜像,让 SSE handler 闭包可读最新值
    const permissionModeRef = useRef(permissionMode);
    useEffect(() => { permissionModeRef.current = permissionMode; }, [permissionMode]);
    const [permPopOpen, setPermPopOpen] = useState(false);
    const streamRef = useRef(null); // 当前 SSE 流句柄
    const scrollRef = useRef(null);
    const inputRef = useRef(null);

    // convmgr1: ContextRing state
    const [ctxUsage, setCtxUsage] = useState({ cumIn: 0, cumOut: 0, ctxLimit: 0 });

    // convmgr1: 对话列表
    const [showConvList, setShowConvList] = useState(false);
    const [convList, setConvList] = useState([]);
    const [convListLoading, setConvListLoading] = useState(false);
    const convListRef = useRef(null);

    // 自动滚到底
    useEffect(() => {
      const el = scrollRef.current;
      if (!el) return;
      el.scrollTop = el.scrollHeight;
    }, [messages, running]);

    // 探测后端是否就绪：HEAD/POST 都不可靠（POST 会触发 SSE），改用 OPTIONS / GET ping
    useEffect(() => {
      let alive = true;
      const url = (apiBase || "") + "/api/v1/console_assistant/ping";
      fetch(url, { method: "GET", credentials: "include" })
        .then(r => { if (!alive) return; if (r.ok) { setUseMock(false); setAutoMockReason("后端就绪"); } else { setUseMock(true); setAutoMockReason("后端 " + r.status + "，走 mock"); } })
        .catch(() => { if (!alive) return; setUseMock(true); setAutoMockReason("后端不可达，走 mock"); });
      return () => { alive = false; };
    }, [apiBase]);

    // 清理：组件卸载时中断流
    useEffect(() => () => { if (streamRef.current) try { streamRef.current.stop(); } catch (_) {} }, []);

    // 构造 SSE handler 集合（共用 mock / 真后端）
    const buildHandlers = useCallback(() => {
      let openedAssistant = false;
      return {
        on_meta: (d) => { if (d && d.conversation_id) setConvId(d.conversation_id); },
        on_token: (d) => {
          const piece = (d && (d.text || d.delta)) || "";
          if (!piece) return;
          setMessages(ms => {
            // 找最近的 assistant 消息且 streaming=true
            const lastIdx = (() => {
              for (let i = ms.length - 1; i >= 0; i--) {
                const m = ms[i];
                if (m.type === "assistant" && m.streaming) return i;
                // 如果 user/error 之后又来 token,新建消息
                if (m.type === "user" || m.type === "error") break;
              }
              return -1;
            })();
            if (lastIdx === -1 || !openedAssistant) {
              openedAssistant = true;
              return [...ms, { type: "assistant", text: piece, streaming: true, ts: Date.now() }];
            }
            const next = ms.slice();
            next[lastIdx] = { ...next[lastIdx], text: (next[lastIdx].text || "") + piece };
            return next;
          });
        },
        on_tool_call: (d) => {
          if (!d || !d.call_id) return;
          openedAssistant = false; // 工具调用之后的 token 起新气泡
          // task 70: 仅"自动审查"模式才在每个 tool_call 前插本地拦截卡。
          // 默认/full_access 不插; read_only 走 on_confirmation_required 的拒绝路径。
          if (permissionModeRef.current === "auto_review") {
            setMessages(ms => [...ms, {
              type: "confirm",
              call_id: d.call_id,
              tool: d.tool || "(unknown)",
              args: d.args,
              description: "已开启『自动审查』,逐个工具调用展示。",
              destructive: false,
              _localAck: true,  // 标记为前端本地拦截
              ts: Date.now(),
            }]);
          }
          setMessages(ms => [...ms, {
            type: "tool",
            call_id: d.call_id,
            tool: d.tool || "(unknown)",
            args: d.args,
            server_id: d.server_id,
            status: "running",
            ts: Date.now(),
          }]);
        },
        on_tool_result: (d) => {
          if (!d || !d.call_id) return;
          setMessages(ms => ms.map(m => {
            if (m.type !== "tool" || m.call_id !== d.call_id) return m;
            return { ...m, status: d.ok ? "done" : "error",
              result: d.result, error: d.error };
          }));
        },
        on_confirmation_required: (d) => {
          if (!d || !d.call_id) return;
          openedAssistant = false;
          const mode = permissionModeRef.current;
          // task 70: full_access — destructive 也自动 approve
          // task 70: read_only — destructive 自动 reject
          const autoDecision = mode === "full_access" ? "approve"
                             : mode === "read_only"   ? "reject"
                             : null;
          setMessages(ms => [...ms, {
            type: "confirm",
            call_id: d.call_id,
            tool: d.tool || "(unknown)",
            args: d.args,
            description: d.description,
            destructive: !!d.destructive,
            auto_decision: autoDecision,
            ts: Date.now(),
          }]);
          if (autoDecision) {
            // 用 micro-task 防止跟当前 SSE 帧打架
            setTimeout(() => {
              try {
                decideConfirmRef.current && decideConfirmRef.current(
                  { call_id: d.call_id, tool: d.tool || "(unknown)", args: d.args },
                  autoDecision,
                );
              } catch (_) {}
            }, 0);
          }
        },
        // task 61: user_choice_required - 助手通过 ask_user_choice 工具弹结构化选择题
        // 渲染按钮组 chip, 用户点完后自动以 "我选: xxx" 发新消息触发下一轮。
        // 同时本轮 LLM loop 在后端就已 break, 这里 setRunning(false) 让 UI 解锁等用户。
        on_user_choice_required: (d) => {
          if (!d || !d.question) return;
          openedAssistant = false;
          setMessages(ms => [...ms, {
            type: "choices",
            call_id: d.call_id,
            question: d.question,
            options: Array.isArray(d.options) ? d.options : [],
            allow_free_text: d.allow_free_text !== false,
            context: d.context || "",
            answered: false,
            ts: Date.now(),
          }]);
          // 后端已中断本轮 LLM loop, 标记本地 running=false 等用户裁决
          setRunning(false);
        },
        // task 92: user_text_required - 助手要求文本输入 (姓名/描述等不适合选项的字段)。
        // 之前完全没注册这个 handler → 后端 yield 后前端忽略 → "静默失败"。
        // 现在渲染成一个带 placeholder 的输入提示卡, 用户在主输入框继续打字即可。
        on_user_text_required: (d) => {
          if (!d || !d.question) return;
          openedAssistant = false;
          setMessages(ms => [...ms, {
            type: "text_ask",
            call_id: d.call_id,
            question: d.question,
            placeholder: d.placeholder || "",
            context: d.context || "",
            answered: false,
            ts: Date.now(),
          }]);
          setRunning(false);
          // 给输入框打个 hint
          try {
            const ta = inputRef.current;
            if (ta && d.placeholder) ta.placeholder = d.placeholder;
            if (ta) ta.focus();
          } catch (_) {}
        },
        // task 109b: ui_action - 后端 ui_set_field / ui_click 工具触发,
        // 转发到 window.__UI_ATLAS 执行真实 DOM 操作 (代用户填表/点按钮)
        on_ui_action: (d) => {
          if (!d || !window.__UI_ATLAS) return;
          try {
            let result = null;
            if (d.kind === "set_field") {
              result = window.__UI_ATLAS.setField(d.form_id, d.field_key, d.value);
            } else if (d.kind === "click") {
              result = window.__UI_ATLAS.click(d.form_id, d.action_label);
            }
            // 失败时 toast 一下让用户知道; 成功静默 (用户能看到 DOM 变化)
            if (result && result.ok === false) {
              window.__apiToast?.(`助手填表失败: ${result.error || "未知"}`,
                { kind: "danger", duration: 2500 });
            }
          } catch (e) {
            console.error("[cap ui_action]", e);
            window.__apiToast?.(`助手填表异常: ${e.message}`,
              { kind: "danger", duration: 2500 });
          }
        },
        // task 57: navigation_required - 助手通过 navigate_to_setting 工具引导用户跳转
        on_navigation_required: (d) => {
          if (!d || !d.target) return;
          if (typeof window !== "undefined" && window.handleAssistantNavigation) {
            try { window.handleAssistantNavigation(d.target, d.reason, d.dirty_check !== false); }
            catch (e) {
              setMessages(ms => [...ms, { type: "error",
                message: "导航失败: " + (e && e.message), ts: Date.now() }]);
            }
          }
        },
        // convmgr1: context_usage — 每轮 chat 后后端 SSE 推送
        on_context_usage: (d) => {
          if (!d) return;
          setCtxUsage({
            cumIn:    d.cum_input_tokens  || 0,
            cumOut:   d.cum_output_tokens || 0,
            ctxLimit: d.context_limit     || 0,
          });
        },
        on_error: (d) => {
          openedAssistant = false;
          setMessages(ms => [...ms, { type: "error",
            message: (d && d.message) || "未知错误", ts: Date.now() }]);
        },
        on_done: (_d) => {
          setMessages(ms => ms.map(m => m.type === "assistant" && m.streaming
            ? { ...m, streaming: false } : m));
          setRunning(false);
          streamRef.current = null;
        },
        onError: (err) => {
          setMessages(ms => [...ms, { type: "error",
            message: "网络错误: " + ((err && err.message) || err), ts: Date.now() }]);
          setRunning(false);
          streamRef.current = null;
        },
        onClose: () => {
          setMessages(ms => ms.map(m => m.type === "assistant" && m.streaming
            ? { ...m, streaming: false } : m));
          if (streamRef.current) {
            setRunning(false);
            streamRef.current = null;
          }
        },
      };
    }, []);

    const sendMessage = useCallback((overrideText) => {
      const text = (overrideText !== undefined ? overrideText : input).trim();
      if (!text || running) return;
      setInput("");
      setMessages(ms => [...ms, { type: "user", text, ts: Date.now() }]);
      setRunning(true);
      const handlers = buildHandlers();
      // task 109b-1: 把最新 UI Atlas snapshot 塞入 page_context，供后端感知当前页面字段
      let ui_atlas = null;
      try {
        if (window.__UI_ATLAS && window.__UI_ATLAS.rescan) {
          ui_atlas = window.__UI_ATLAS.rescan();
        }
      } catch (_) {}
      const body = {
        message: text,
        conversation_id: convId,
        page_context: { ...(pageContext || {}), ...(ui_atlas ? { ui_atlas } : {}) },
      };
      let stream;
      if (useMock) {
        stream = mockChat(body, handlers);
      } else {
        try {
          const sseStream = window.api && window.api.raw && window.api.raw.sseStream;
          if (!sseStream) throw new Error("api.raw.sseStream 不可用");
          stream = sseStream("/api/v1/console_assistant/chat", body, handlers);
        } catch (e) {
          // 真后端 fallback 到 mock
          setMessages(ms => [...ms, { type: "error",
            message: "切换到 mock：" + (e && e.message), ts: Date.now() }]);
          stream = mockChat(body, handlers);
        }
      }
      streamRef.current = stream;
    }, [input, running, convId, pageContext, useMock, buildHandlers]);

    const stop = useCallback(() => {
      if (streamRef.current) {
        try { streamRef.current.stop(); } catch (_) {}
        streamRef.current = null;
      }
      setRunning(false);
      setMessages(ms => ms.map(m => m.type === "assistant" && m.streaming
        ? { ...m, streaming: false } : m));
    }, []);

    const decideConfirm = useCallback((entry, decision) => {
      // 1) UI 上立刻标记
      setMessages(ms => ms.map(m => (m.type === "confirm" && m.call_id === entry.call_id && m._localAck === entry._localAck)
        ? { ...m, decided: decision } : m));

      // task 60: 本地拦截卡 (askBeforeActing 触发) — 不向后端发任何 confirm,
      // 只是 UX 展示已确认/已拒绝。后端工具其实已经执行,所以"拒绝"在这条路径里
      // 仅作为 UX 标记 (实际副作用无法撤销)。这是与 Claude in Chrome 同款的语义:
      // 开关只控制"看一眼再放过"。
      if (entry._localAck) return;

      // 2) mock 路径：直接 resolve mock 内部 promise
      const cur = streamRef.current;
      if (cur && cur.isMock && cur.__pendingMockResolve) {
        cur.__pendingMockResolve(decision);
        return;
      }
      // task 58: 真后端 /confirm 现在返 SSE 流 (后端 dispatch 工具 + LLM 续写),
      // 必须像 /chat 一样订阅, 否则 LLM 续写文本丢失、对话断在工具结果。
      // 协议事件 (meta/token/tool_call/tool_result/confirmation_required/
      //          navigation_required/error/done) 与 /chat 完全一致, 复用 buildHandlers。
      setRunning(true);
      const handlers = buildHandlers();
      const body = {
        conversation_id: convId,
        call_id: entry.call_id,
        decision,
        page_context: pageContext || null,
      };
      try {
        const sseStream = window.api && window.api.raw && window.api.raw.sseStream;
        if (!sseStream) throw new Error("api.raw.sseStream 不可用");
        const stream = sseStream("/api/v1/console_assistant/confirm", body, handlers);
        streamRef.current = stream;
      } catch (e) {
        setRunning(false);
        setMessages(ms => [...ms, { type: "error",
          message: "确认请求失败: " + (e && e.message), ts: Date.now() }]);
      }
    }, [convId, apiBase, pageContext, buildHandlers]);

    // task 70: ref 镜像 decideConfirm,供 SSE handler 闭包内 setTimeout 调用
    // (handler 是 useMemo([], [...]) 闭包,直接引用会 stale)。
    const decideConfirmRef = useRef(decideConfirm);
    useEffect(() => { decideConfirmRef.current = decideConfirm; }, [decideConfirm]);

    // convmgr1: 新建对话
    const newConversation = useCallback(async () => {
      // 停止当前流
      if (streamRef.current) {
        try { streamRef.current.stop(); } catch (_) {}
        streamRef.current = null;
      }
      setRunning(false);
      try {
        if (!useMock) {
          const r = await fetch((apiBase || "") + "/api/v1/console_assistant/new_conversation", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({}),
          });
          if (r.ok) {
            const j = await r.json();
            if (j && j.conversation_id) {
              setConvId(j.conversation_id);
            } else {
              setConvId(null);
            }
          } else {
            setConvId(null);
          }
        } else {
          setConvId("mock-new-" + Date.now());
        }
      } catch (_) {
        setConvId(null);
      }
      setMessages([]);
      setCtxUsage({ cumIn: 0, cumOut: 0, ctxLimit: 0 });
      try { localStorage.removeItem(CAP_PERSIST_KEY); } catch (_) {}
      setShowSettings(false);
      setShowConvList(false);
    }, [useMock, apiBase]);

    // convmgr1: 加载对话列表
    const loadConvList = useCallback(async () => {
      setConvListLoading(true);
      try {
        if (!useMock) {
          const r = await fetch((apiBase || "") + "/api/v1/console_assistant/conversations", {
            credentials: "include",
          });
          if (r.ok) {
            const j = await r.json();
            setConvList(Array.isArray(j && j.items) ? j.items : []);
          }
        } else {
          // mock: 伪造一条当前对话
          setConvList(convId ? [{
            id: convId,
            created_at: new Date().toISOString(),
            last_used: new Date().toISOString(),
            message_count: messages.length,
            cum_input_tokens: ctxUsage.cumIn,
            cum_output_tokens: ctxUsage.cumOut,
            context_limit: ctxUsage.ctxLimit,
            last_user_message: messages.filter(m => m.type === "user").slice(-1)[0]?.text || "",
          }] : []);
        }
      } catch (_) {
        setConvList([]);
      } finally {
        setConvListLoading(false);
      }
    }, [useMock, apiBase, convId, messages, ctxUsage]);

    // convmgr1: 切换对话
    const switchConv = useCallback((item) => {
      if (!item || item.id === convId) { setShowConvList(false); return; }
      if (streamRef.current) {
        try { streamRef.current.stop(); } catch (_) {}
        streamRef.current = null;
      }
      setRunning(false);
      setConvId(item.id);
      setMessages([]);   // 历史在内存不可 fetch,先清空
      setCtxUsage({
        cumIn: item.cum_input_tokens || 0,
        cumOut: item.cum_output_tokens || 0,
        ctxLimit: item.context_limit || 0,
      });
      setShowConvList(false);
    }, [convId]);

    // convmgr1: 删除对话
    const deleteConv = useCallback(async (e, item) => {
      e.stopPropagation();
      if (!item || item.id === convId) return; // 不能删当前
      try {
        if (!useMock) {
          await fetch((apiBase || "") + "/api/v1/console_assistant/delete_conversation", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: item.id }),
          });
        }
      } catch (_) {}
      setConvList(cl => cl.filter(c => c.id !== item.id));
    }, [convId, useMock, apiBase]);

    // convmgr1: 打开对话列表时加载; 点外面关闭
    useEffect(() => {
      if (showConvList) {
        loadConvList();
        const onDoc = (e) => {
          if (convListRef.current && !convListRef.current.contains(e.target)) {
            setShowConvList(false);
          }
        };
        document.addEventListener("mousedown", onDoc);
        return () => document.removeEventListener("mousedown", onDoc);
      }
    }, [showConvList, loadConvList]);

    // task 61: 用户点了 ask_user_choice 的某个 chip → 标记 answered + 发新消息触发下一轮。
    const pickChoice = useCallback((entry, option) => {
      setMessages(ms => ms.map(m => (m.type === "choices" && m.call_id === entry.call_id && !m.answered)
        ? { ...m, answered: true, answered_value: option } : m));
      // 触发新一轮 chat,带着用户的选择
      sendMessage("我选: " + option);
    }, [sendMessage]);

    // 用户点了"自由输入"按钮 → 标记 answered + focus 输入框,placeholder 改成提示。
    const freeTextChoice = useCallback((entry) => {
      setMessages(ms => ms.map(m => (m.type === "choices" && m.call_id === entry.call_id && !m.answered)
        ? { ...m, answered: true, answered_value: "(自由输入)" } : m));
      const ta = inputRef.current;
      if (ta) {
        try {
          ta.focus();
          // 临时改 placeholder 提示一下
          ta.setAttribute("placeholder", "或者直接描述你的想法…");
        } catch (_) {}
      }
    }, []);

    // task 115: 统一聊天输入键位 (Claude Code Desktop 同款) — IME composition
    // 中 Enter 留给 IME (中文候选选词), 否则 Enter 发送, Shift+Enter 换行,
    // Cmd/Ctrl+Enter 备用发送。详见 responsive.jsx::chatComposerKey
    const onInputKey = (e) => {
      const fn = (typeof window !== "undefined" && window.chatComposerKey);
      if (fn) {
        fn(e, sendMessage);
      } else {
        // fallback (responsive.jsx 没加载到时)
        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent?.isComposing) {
          e.preventDefault();
          sendMessage();
        }
      }
    };

    const contextLabel = useMemo(() => {
      if (!pageContext) return "no context";
      const parts = [];
      if (pageContext.tab) parts.push(pageContext.tab);
      if (pageContext.save_id) parts.push("save#" + pageContext.save_id);
      if (pageContext.script_id) parts.push("script#" + pageContext.script_id);
      return parts.length ? parts.join(" · ") : abbreviate(pageContext, 40);
    }, [pageContext]);

    // 渲染消息列表
    const messageNodes = messages.map((m, i) => {
      if (m.type === "user") {
        return (
          <div key={i} className="cap-msg cap-msg-user">
            <div className="cap-bubble">{m.text}</div>
          </div>
        );
      }
      if (m.type === "assistant") {
        return (
          <div key={i} className="cap-msg cap-msg-assistant">
            <div className="cap-bubble">
              {/* task 90: 优先用全局 RpgMarkdown.Block (与 GM 同款 markdown);
                  没加载到时回退到老的 renderMarkdownish。 */}
              {(window.RpgMarkdown && window.RpgMarkdown.Block)
                ? <window.RpgMarkdown.Block text={m.text || ""} streaming={!!m.streaming} className="rpg-md cap-md" />
                : (<>{renderMarkdownish(m.text)}{m.streaming && <span className="cap-cursor" />}</>)
              }
            </div>
          </div>
        );
      }
      if (m.type === "tool") {
        return <ToolCard key={i} entry={m} />;
      }
      if (m.type === "confirm") {
        return <ConfirmationCard key={i} entry={m} onDecide={decideConfirm} busy={false} />;
      }
      if (m.type === "choices") {
        return <ChoicesCard key={i} entry={m} onPick={pickChoice} onFreeText={freeTextChoice} />;
      }
      if (m.type === "text_ask") {
        // task 92: user_text_required 渲染 — 显示问题 + 提示用户在下方主输入框作答
        return (
          <div key={i} className={"cap-choices" + (m.answered ? " cap-answered" : "")}
               role="group" aria-label="文本输入提示">
            <div className="cap-choices-q">{m.question}</div>
            {m.placeholder && <div className="cap-choices-ctx">提示: {m.placeholder}</div>}
            {m.context && <div className="cap-choices-ctx">{m.context}</div>}
            <div className="cap-choices-ctx" style={{marginTop:4, fontStyle:'normal'}}>
              请在下方输入框输入答案后回车
            </div>
          </div>
        );
      }
      if (m.type === "error") {
        return <div key={i} className="cap-err">出错: {m.message}</div>;
      }
      return null;
    });

    return (
      <aside className={"cap-root" + (open ? "" : " cap-closed")}
             aria-label="控制台助手" role="complementary"
             aria-hidden={!open}
             style={{ "--cap-w": capW + "px" }}>
        {open && <div className="cap-resize-handle" title="拖动调整宽度 · 双击恢复默认" {...capDragProps} />}
        <div className="cap-root-inner">
        <div className="cap-head">
          <div className="cap-head-left">
            <div className="cap-head-title-row">
              <span className={"cap-dot" + (running ? " cap-on" : "")} />
              <span className="cap-title">控制台助手</span>
            </div>
            <ModelDropdown apiBase={apiBase} />
          </div>
          <div className="cap-head-actions">
            <ContextRing cumIn={ctxUsage.cumIn} cumOut={ctxUsage.cumOut} ctxLimit={ctxUsage.ctxLimit} />
            <button className="cap-icon-btn" title="对话历史"
                    aria-label="对话历史"
                    onClick={() => { setShowConvList(v => !v); setShowSettings(false); }}>
              &#128337;
            </button>
            <button className="cap-icon-btn" title="新对话"
                    aria-label="新对话"
                    onClick={newConversation}
                    style={{ fontWeight: 600, fontSize: 15 }}>
              +
            </button>
            <button className="cap-icon-btn" title="设置"
                    onClick={() => { setShowSettings(s => !s); setShowConvList(false); }}>⚙</button>
            <button className="cap-icon-btn" title="关闭助手"
                    aria-label="关闭助手"
                    onClick={() => setOpen(false)}>✕</button>
          </div>
          {showConvList && (
            <div className="cap-conv-pop" ref={convListRef} onClick={(e) => e.stopPropagation()}>
              {convListLoading && <div className="cap-conv-empty">加载中…</div>}
              {!convListLoading && convList.length === 0 && (
                <div className="cap-conv-empty">暂无历史对话</div>
              )}
              {convList.map((item) => {
                const active = item.id === convId;
                const totalTok = (item.cum_input_tokens || 0) + (item.cum_output_tokens || 0);
                return (
                  <div key={item.id}
                       className={"cap-conv-item" + (active ? " cap-active" : "")}
                       onClick={() => switchConv(item)}>
                    <div className="cap-conv-item-body">
                      <div className="cap-conv-preview">
                        {item.last_user_message
                          ? item.last_user_message.slice(0, 50) + (item.last_user_message.length > 50 ? "…" : "")
                          : "(空对话)"}
                      </div>
                      <div className="cap-conv-meta">
                        {relativeTime(item.last_used || item.created_at)}
                        {" · "}
                        {item.message_count || 0} 条
                        {totalTok > 0 ? " · " + (totalTok / 1000).toFixed(1) + "k tok" : ""}
                      </div>
                    </div>
                    {!active && (
                      <button className="cap-conv-del"
                              title="删除此对话"
                              onClick={(e) => deleteConv(e, item)}>✕</button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {showSettings && (
            <div className="cap-settings-pop" onClick={(e) => e.stopPropagation()}>
              {/* 开发者选项: localStorage.rpg_devmode === "1" 才显示 */}
              {typeof localStorage !== "undefined" && localStorage.getItem("rpg_devmode") === "1" && (
                <>
                  <label>
                    <input type="checkbox" checked={useMock}
                           onChange={(e) => setUseMock(e.target.checked)} />
                    使用演示数据 (开发者)
                  </label>
                  <hr />
                  <div className="cap-setting-row cap-muted-small">{autoMockReason}</div>
                  {convId && <div className="cap-setting-row cap-muted-small">会话 ID: {convId}</div>}
                  <hr />
                </>
              )}
              <button className="cap-btn" style={{ width: "100%" }}
                      onClick={() => {
                        setMessages([]); setConvId(null); setShowSettings(false);
                        try { localStorage.removeItem(CAP_PERSIST_KEY); } catch (_) {}
                      }}>
                清空会话
              </button>
            </div>
          )}
        </div>

        <div className="cap-body" ref={scrollRef}>
          {messageNodes.length === 0 ? (
            <div className="cap-empty">
              控制台助手
              <div className="cap-empty-hint">
                询问关于当前页面的内容、调用工具、或让助手帮你执行操作。
                {useMock ? "（当前 mock 模式）" : ""}
              </div>
            </div>
          ) : messageNodes}
          {/* task 93: 思考中指示 — running=true 且最后一条 msg 不是流式 assistant 时显示 */}
          {running && (() => {
            const last = messages[messages.length - 1];
            const streaming = last && last.type === "assistant" && last.streaming;
            if (streaming) return null;
            return (
              <div className="cap-thinking" aria-label="助手正在思考">
                <span className="cap-thinking-dot" />
                <span className="cap-thinking-dot" />
                <span className="cap-thinking-dot" />
                <span className="cap-thinking-label">思考中…</span>
              </div>
            );
          })()}
        </div>

        <div className="cap-foot">
          <div className="cap-context">ctx: {contextLabel}</div>
          {/* task 70: 4 档权限模式 — 对齐游戏控制台 PermissionPopover */}
          <div className="cap-ask-row">
            <span className="cap-perm-pill" data-mode={permissionMode}
                  onClick={() => setPermPopOpen(v => !v)}
                  role="button" tabIndex={0}
                  aria-haspopup="true" aria-expanded={permPopOpen}>
              {(() => {
                const PERM_LABELS = {
                  default:     "权限: 默认",
                  read_only:   "权限: 只读",
                  auto_review: "权限: 逐个审查",
                  full_access: "权限: 完全放行",
                };
                return PERM_LABELS[permissionMode] || PERM_LABELS.default;
              })()}
              {permPopOpen && (
                <div className="cap-perm-pop" onClick={(e) => e.stopPropagation()}>
                  {[
                    { id: "read_only",   label: "只读",         desc: "助手不能改任何数据,只能查询" },
                    { id: "default",     label: "默认",         desc: "高风险操作 (如删除) 会先弹确认" },
                    { id: "auto_review", label: "逐个审查",     desc: "每一步都展示并等你放行" },
                    { id: "full_access", label: "完全放行",     desc: "高风险操作也自动通过,不弹确认" },
                  ].map((p) => (
                    <button key={p.id}
                            className={"cap-perm-opt" + (permissionMode === p.id ? " active" : "")}
                            onClick={(e) => { e.stopPropagation(); setPermissionMode(p.id); setPermPopOpen(false); }}>
                      <div className="cap-perm-opt-row">
                        <span>{p.label}</span>
                        {permissionMode === p.id && <span className="cap-perm-opt-check">已选</span>}
                      </div>
                      <div className="cap-perm-opt-desc">{p.desc}</div>
                    </button>
                  ))}
                </div>
              )}
            </span>
          </div>
          <div className="cap-input-row">
            <textarea
              ref={inputRef}
              className="cap-input"
              rows={1}
              placeholder={running ? "助手处理中…" : "输入消息，Enter 发送，Shift+Enter 换行"}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onInputKey}
              disabled={running}
            />
            {running ? (
              <button className="cap-send-btn cap-stop-btn" onClick={stop}>停止</button>
            ) : (
              <button className="cap-send-btn" onClick={() => sendMessage()}
                      disabled={!input.trim()}>发送</button>
            )}
          </div>
          <div className="cap-disclaimer">AI 可能犯错,请仔细核对结果</div>
        </div>
        </div>
      </aside>
    );
  }

  // 暴露
  // task 55: 暴露全局 open/close,让 TopBar 图标按钮无需 prop drilling。
  // 父组件 (PlatformApp / Game App) 持有 open state,通过受控 prop 传入;
  // window.openConsoleAssistant 转发到全局 EventTarget,父组件订阅后 setState。
  if (typeof window !== "undefined") {
    window.ConsoleAssistantPanel = ConsoleAssistantPanel;
    const bus = window.__capBus || (window.__capBus = new EventTarget());
    window.openConsoleAssistant = () => bus.dispatchEvent(new CustomEvent("cap-open"));
    window.closeConsoleAssistant = () => bus.dispatchEvent(new CustomEvent("cap-close"));
    window.toggleConsoleAssistant = () => bus.dispatchEvent(new CustomEvent("cap-toggle"));
  }
})();
