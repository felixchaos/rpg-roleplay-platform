/* tavern-chat-run — 酒馆三宿主共用的 SSE 状态机(收口蓝图 #11/#38)。
 *
 * 此前 tavern-app.jsx(独立页)、pages/tavern.jsx(平台内嵌 #tavern)、
 * mobile/pages/MobileTavern.jsx 各自抄了一份逐字相同的 startRun/stopRun/applyState
 * SSE 折叠逻辑(run-id 守卫 + 120s idle + restoreFailedDraft + 七个 on_* handler)。
 * 三份漂移风险高(合错=所有酒馆聊天一起挂),故收口为同一份。
 *
 * 设计:**公共骨架在此,宿主差异走回调/参数,绝不抹平。**
 *   - 折叠语义逐字保留:token 追加正文、reasoning 挂 _thinking、tool_* 挂 _toolOps、
 *     done 收尾 + applyState + 二次拉 state、run-id 守卫、120s idle、restoreFailedDraft、
 *     isCurrentRun —— 全在此处统一实现。
 *   - 宿主特有分支(toast 通道、秒表 ticker、tool-op 模型 anchor vs flush、
 *     on_usage、autotitle、needsCreds、setGameState/setPermission、附件/命令、
 *     restoreFailedDraft 是否回填输入框)由调用方以选项/回调注入。
 *
 * 注意:本模块是「纯逻辑工厂」,不直接 import React —— useTavernChatRun 这个 hook
 * 在 hooks/useTavernChatRun.js 里包一层 useRef/useCallback。这样 applyTavernState
 * 等纯函数可单测,无需 React 运行时。
 */

const STREAM_IDLE_TIMEOUT_MS = 120000;
/* abort reason 属于「主动/受控中断」→ 不当作错误,不恢复草稿、不报红。 */
const CONTROLLED_ABORTS = ['manual_stop', 'superseded', 'unmount', 'switch', 'idle_timeout'];

/** 本轮时间戳:优先 window.__fmt.nowHHMM,回退 HH:MM。 */
export function nowHHMM() {
  if (typeof window !== 'undefined' && window.__fmt && window.__fmt.nowHHMM) {
    try { return window.__fmt.nowHHMM(); } catch (_) { /* fall through */ }
  }
  const d = new Date();
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

/**
 * stopRun —— 用户主动停止本轮(或外部中止)。三宿主完全一致(pages 额外停秒表,经
 * onStopExtra 注入)。
 * @param {object} rc            runRef.current
 * @param {function} setRunning  React setState
 * @param {object}  [api]        window.api(默认取全局)
 * @param {function} [onStopExtra] 宿主附加收尾(如 stopTicker)
 */
export function makeStopRun(rc, setRunning, api, onStopExtra) {
  return function stopRun() {
    rc.stopped = true;
    if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
    rc.runId = (rc.runId || 0) + 1;
    if (rc.sse) { try { rc.sse.stop('manual_stop'); } catch (_) {} rc.sse = null; }
    try { (api || (typeof window !== 'undefined' && window.api))?.game?.stop(); } catch (_) {}
    setRunning(false);
    if (onStopExtra) { try { onStopExtra(); } catch (_) {} }
  };
}

/**
 * 卸载/切换时的统一清理:标记 stopped、清 idle timer、停在途 sse。
 * 宿主在 unmount effect 里调用(pages 还要额外 clearInterval ticker,自己处理)。
 */
export function abortRun(rc, reason) {
  rc.stopped = true;
  if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
  if (rc.sse) { try { rc.sse.stop(reason || 'unmount'); } catch (_) {} rc.sse = null; }
}

/**
 * applyTavernState —— 把一份 /api/state(或 done 的 status 快照)投射进 UI。
 *
 * 核心三段(三宿主等价):
 *   1. character ← state.(data.)tavern.character
 *   2. persona  ← 有 persona_card_id 则拉全卡(编辑保存需真 id),否则用 data.player 投影
 *   3. activeChat ← {id,title,character_name,updated_at}(有 save_id 时)
 *   4. history ← data.history(默认原样;pages 用 mapHistory 映射 tool_ops/_thinking + 回填)
 *
 * 宿主叠加(可选 setters,缺省即跳过):
 *   - setGameState / setPermission(pages 喂 Composer)
 *   - setSystemPrompt(MobileTavern 同步系统提示)
 *   - mapHistory(data.history) → 自定义 history 映射(pages 的持久化 tool_ops/_thinking)
 *
 * @param {object} data    state 数据
 * @param {object} setters { setCharacter, setPersona, setHistory, setActiveChat,
 *                           setGameState?, setPermission?, setSystemPrompt?, mapHistory?, api? }
 */
export function applyTavernState(data, setters) {
  if (!data || !setters) return;
  const {
    setCharacter, setPersona, setHistory, setActiveChat,
    setGameState, setPermission, setSystemPrompt, setImmersive, mapHistory,
  } = setters;
  const api = setters.api || (typeof window !== 'undefined' && window.api);

  // pages:完整 state 喂 Composer(模型名/context 圆环/@mention)。
  if (setGameState) setGameState(data);
  // pages:权限模式回填(给 Composer 的权限选择器)。
  if (setPermission) {
    try {
      const pm = (data.permissions && data.permissions.mode)
        || (data.data && data.data.permissions && data.data.permissions.mode);
      if (pm) setPermission(pm);
    } catch (_) {}
  }

  const tavern = data.tavern || (data.data && data.data.tavern) || {};
  const char = tavern.character || null;
  if (setCharacter) setCharacter(char || null);

  // data.player 是 persona 投影(无 id),编辑保存需真正的卡 id → 用 persona_card_id 拉全卡。
  const personaCardId = tavern.persona_card_id;
  if (setPersona) {
    if (personaCardId != null && api && api.me && api.me.personas && api.me.personas.get) {
      // persona 卡是 card_type='persona',必须走 /api/me/personas/{id};走 character-cards 的
      // pc 端点会 404 → 回退到建档时的名字快照,persona 永远卡在首个默认卡(芙兰朵露)且无人设图。
      // 反馈#76(persona 卡死)+ #75(头像只剩首字母,因回退快照无 avatar_path)。
      api.me.personas.get(personaCardId)
        .then((r) => { const full = (r && r.persona) || r; if (full && full.id) setPersona(full); else setPersona(data.player || null); })
        .catch(() => setPersona(data.player || null));
    } else {
      setPersona(data.player || null);
    }
  }

  if (Array.isArray(data.history) && setHistory) {
    setHistory(mapHistory ? mapHistory(data.history) : data.history);
  }

  // MobileTavern:同步系统提示词编辑态。
  if (setSystemPrompt && tavern.system_prompt !== undefined) {
    setSystemPrompt(tavern.system_prompt || '');
  }

  // 沉浸式拟人模式开关回填(state.data.tavern.immersive,经 /api/state 顶层 tavern 透出)。
  if (setImmersive) setImmersive(!!tavern.immersive);

  if (data.save_id != null && setActiveChat) {
    setActiveChat((prev) => ({
      id: data.save_id,
      title: data.save_title || prev?.title || `对话 #${data.save_id}`,
      character_name: (char && char.name) || prev?.character_name || '',
      updated_at: data.save_updated_at || prev?.updated_at || '',
    }));
  }
}

/**
 * startTavernRun —— 发起一轮流式对话,装配并返回 SSE 句柄(已挂 runRef.current.sse)。
 *
 * 公共骨架在此(run-id 守卫 + 120s idle + restoreFailedDraft + 七个 on_* 折叠语义),
 * 宿主差异由 cfg 注入。返回前已把 rc.sse 设好。saveId 为空时 toast 提示并返回 null。
 *
 * @param {object} cfg
 *   @param {object}   cfg.rc          runRef.current(含 stopped/sse/runId/inactivityTimer)
 *   @param {*}        cfg.saveId      目标 save(已由宿主算好:opts.saveId ?? activeId)
 *   @param {*}        cfg.model       模型
 *   @param {string}   cfg.playerText  用户输入
 *   @param {object}   cfg.api         window.api
 *   @param {function} cfg.applyState  applyTavernState 的宿主绑定版
 *   @param {function} cfg.setHistory  React setState
 *   @param {function} cfg.setRunning
 *   @param {function} cfg.setText     用于 restoreFailedDraft 回填(MobileTavern 传 null=不回填)
 *   @param {function} cfg.setHasError
 *   @param {function} cfg.setLastPlayerText
 *   @param {function} cfg.toast       (title, opts) 适配后的 toast(宿主把通道差异封进去)
 *   @param {function} [cfg.reloadList]
 *   @param {object}   [cfg.chatExtra] 额外 chat body 字段(pages:attachments/command)
 *   @param {Array}    [cfg.userAttachments] 用户气泡附件(pages)
 *   @param {function} [cfg.onStart]   提交前钩子(pages:startTicker + setNeedsCreds(false))
 *   @param {function} [cfg.onIdleExtra] idle 超时附加(pages:stopTicker)
 *   @param {function} [cfg.onStreamEndExtra] 任何「本轮结束」附加(stopTicker;onError/onAbort/on_done/on_error/onClose 都调)
 *   @param {function} [cfg.onToolCall]  自定义 tool_call 折叠(默认=inline anchor 模型)
 *   @param {function} [cfg.onToolResult] 自定义 tool_result 折叠(默认=inline 模型)
 *   @param {function} [cfg.onUsage]   on_usage(pages:setLastUsage)
 *   @param {function} [cfg.onDoneAlways] on_done 无条件收尾(空回复也跑;pages:setElapsedMs/setLastUsage)
 *   @param {function} [cfg.onDoneExtra] on_done 成功收尾(applyState 前;pages:autotitle/lastTurnToolOps)
 *   @param {function} [cfg.onErrorEvent] 自定义 on_error(pages:needsCreds 分流);缺省=默认报红
 *   @param {function|string} [cfg.ts]    本轮时间戳(默认 nowHHMM;MobileTavern 传 tvNow)
 *   @param {function} [cfg.doneEmptyMsg] (interrupted)=>string 空回复文案(MobileTavern 覆盖)
 *   @param {string}   [cfg.closeMsg]    onClose 连接中断文案(MobileTavern 更短)
 *   @param {boolean}  [cfg.doneAlwaysRefetch] on_done 即使有 payload 也再拉一次 state(pages=true)
 *   @param {boolean}  [cfg.skipDoneReload] on_done 不调默认 reloadList(pages 由 onDoneExtra 的 autotitle 负责)
 * @returns {object|null} SSE 句柄(.stop(reason)),或 saveId 为空时 null
 */
export function startTavernRun(cfg) {
  const {
    rc, saveId, model, playerText, applyState,
    setHistory, setRunning, setText, setHasError, setLastPlayerText,
    toast, reloadList,
    chatExtra, userAttachments,
    onStart, onIdleExtra, onStreamEndExtra,
    onToolCall, onToolResult, onUsage, onDoneAlways, onDoneExtra, onErrorEvent,
  } = cfg;
  const api = cfg.api || (typeof window !== 'undefined' && window.api);

  if (saveId == null) {
    toast?.('请先选择或新建一个对话', { kind: 'warn', duration: 2400, code: 'pick_chat' });
    return null;
  }

  // abort 残留流 + bump runId。
  if (rc.sse) {
    rc.runId = (rc.runId || 0) + 1;
    try { rc.sse.stop('superseded'); } catch (_) {}
    rc.sse = null;
    try { api?.game?.stop(); } catch (_) {}
  }
  if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
  const runId = (rc.runId || 0) + 1;
  rc.runId = runId; rc.stopped = false;
  const isCurrentRun = () => rc.runId === runId;

  // 本轮时间戳:默认 nowHHMM()(= tavern-app/pages 的 __fmt.nowHHMM 回退);
  // 宿主可覆盖(MobileTavern 用自有 tvNow() 的零填充 HH:MM,与 __fmt slice 行为可能不同)。
  const ts = (typeof cfg.ts === 'function') ? cfg.ts() : (cfg.ts != null ? cfg.ts : nowHHMM());
  const userMsg = { role: 'user', content: playerText, ts };
  if (userAttachments && userAttachments.length) userMsg.attachments = userAttachments;
  setHistory((h) => [...h, userMsg]);
  setLastPlayerText(playerText);
  setHasError(false);
  setText?.('');               // pages 在 onStart 里已 setText('');这里再清是幂等
  setRunning(true);
  if (onStart) onStart();

  let openedAssistant = false;
  let gotDone = false;

  const restoreFailedDraft = () => {
    if (!isCurrentRun() || openedAssistant) return;
    // MobileTavern 不回填输入框(setText 传 null),tavern-app/pages 回填。
    if (setText) setText((cur) => (String(cur || '').trim() ? cur : playerText));
    setHistory((h) => {
      const last = h[h.length - 1];
      if (last && last.role === 'user' && last.content === playerText) return h.slice(0, -1);
      return h;
    });
  };

  const clearIdle = () => { if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; } };
  const endStream = () => { if (onStreamEndExtra) { try { onStreamEndExtra(); } catch (_) {} } };

  const resetIdle = () => {
    if (rc.inactivityTimer) clearTimeout(rc.inactivityTimer);
    rc.inactivityTimer = setTimeout(() => {
      if (!isCurrentRun()) return;
      try { rc.sse && rc.sse.stop && rc.sse.stop('idle_timeout'); } catch (_) {}
      if (onIdleExtra) { try { onIdleExtra(); } catch (_) {} }
      restoreFailedDraft();
      setRunning(false);
      setHasError('超过 120 秒没有新输出,已断开。请重试。');
      toast?.('生成停滞', { kind: 'warn', detail: '120 秒无响应,已中断', duration: 4000, code: 'idle' });
    }, STREAM_IDLE_TIMEOUT_MS);
  };
  resetIdle();

  // 工具流上下文:把 setHistory/资源暴露给 tool-op 折叠(宿主可自定义模型)。
  const toolCtx = {
    setHistory, ts,
    isOpened: () => openedAssistant,
    markOpened: () => { openedAssistant = true; },
  };

  // 默认 tool-op 折叠(tavern-app 的 inline anchor 模型;MobileTavern 也走 inline 但无 anchor,
  // 故各宿主仍以 onToolCall/onToolResult 注入自己那份,保证逐字一致)。
  const handleToolCall = onToolCall || (() => {});
  const handleToolResult = onToolResult || (() => {});

  const body = Object.assign(
    { message: playerText, text: playerText, model, save_id: saveId },
    chatExtra || {},
  );

  rc.sse = api.game.chat(body, {
    onError: (err) => {
      if (!isCurrentRun()) return;
      clearIdle();
      endStream();
      const detail = (err && err.payload && err.payload.message) || (err && err.message) || '请求失败';
      setRunning(false); setHasError(detail);
      toast?.('请求失败', { kind: 'danger', detail, code: 'request_failed' });
      restoreFailedDraft();
    },
    onAbort: (data) => {
      if (!isCurrentRun()) return;
      const reason = (data && data.reason) || '';
      clearIdle();
      endStream();
      if (rc.stopped || CONTROLLED_ABORTS.includes(reason)) { rc.sse = null; return; }
      restoreFailedDraft();
      setRunning(false); setHasError('连接被取消,上一条输入已保留,请重试。');
      rc.sse = null;
    },
    on_status: (data) => {
      if (!isCurrentRun()) return;
      resetIdle();
      // 后端可能在 status 里回带最新 history,不强制覆盖流式气泡。
      void data;
    },
    // 思考流(reasoning)实时累积到流式 assistant 气泡的 _thinking → 可折叠思考块。
    on_reasoning: (data) => {
      if (!isCurrentRun()) return;
      resetIdle();
      const piece = (data && (data.text || data.delta)) || '';
      if (!piece) return;
      setHistory((h) => {
        let arr = h;
        if (!openedAssistant) { openedAssistant = true; arr = [...h, { role: 'assistant', content: '', ts, streaming: true }]; }
        const last = arr[arr.length - 1];
        if (!last || last.role !== 'assistant') return arr;
        return [...arr.slice(0, -1), { ...last, _thinking: (last._thinking || '') + piece }];
      });
    },
    on_tool_call: (data) => {
      if (!isCurrentRun()) return;
      resetIdle();
      handleToolCall(data, toolCtx);
    },
    on_tool_result: (data) => {
      if (!isCurrentRun()) return;
      resetIdle();
      handleToolResult(data, toolCtx);
    },
    on_usage: (data) => {
      if (!isCurrentRun()) return;
      if (onUsage && data) onUsage(data);
    },
    on_token: (data) => {
      if (!isCurrentRun()) return;
      resetIdle();
      const piece = (data && (data.text || data.delta)) || '';
      if (!piece) return;
      setHistory((h) => {
        if (!openedAssistant) { openedAssistant = true; return [...h, { role: 'assistant', content: piece, ts, streaming: true }]; }
        const last = h[h.length - 1];
        if (!last || last.role !== 'assistant') return [...h, { role: 'assistant', content: piece, ts, streaming: true }];
        return [...h.slice(0, -1), { ...last, content: (last.content || '') + piece }];
      });
    },
    on_done: (data) => {
      if (!isCurrentRun()) return;
      gotDone = true;
      clearIdle();
      endStream();
      // 无条件 done 收尾(空回复也跑):pages 用它写 elapsed_ms / usage(原实现在 !openedAssistant 之前)。
      if (onDoneAlways) { try { onDoneAlways(data); } catch (_) {} }
      setRunning(false);
      if (!openedAssistant) {
        const interrupted = !!(data && data.interrupted);
        const showEmpty = () => {
          restoreFailedDraft();
          // 文案宿主可覆盖(MobileTavern 的空回复文案不带「已恢复你的输入」)。
          const msg = cfg.doneEmptyMsg
            ? cfg.doneEmptyMsg(interrupted)
            : (interrupted ? '本轮已中断,已恢复你的输入。' : '本轮没有收到回复,已恢复你的输入。请重试。');
          setHasError(msg);
          toast?.(interrupted ? '生成中断' : '空回复', { kind: 'warn', detail: msg, duration: 4500, code: interrupted ? 'interrupted' : 'empty' });
          rc.sse = null;
        };
        // 「要刷新才出响应」(线上反馈):本轮没收到增量 token(provider 非增量流式 / SSE 丢事件),
        // 但回复其实已生成并落库 → 旧逻辑直接判「空回复」逼用户刷新。先回查存档,有本轮 assistant
        // 回复就 applyState 渲染;确为空/被中断才提示。与 game-console on_done 同款兜底。
        if (interrupted || !(api && api.game && typeof api.game.state === 'function')) { showEmpty(); return; }
        if (onDoneExtra) { try { onDoneExtra(data); } catch (_) {} }
        api.game.state().then((d2) => {
          if (!isCurrentRun()) return;
          const hist = (d2 && Array.isArray(d2.history)) ? d2.history : null;
          const lastSrv = (hist && hist.length) ? hist[hist.length - 1] : null;
          if (lastSrv && lastSrv.role === 'assistant' && String(lastSrv.content || '').trim()) {
            applyState(d2);
            if (reloadList && !cfg.skipDoneReload) reloadList();
            rc.sse = null;
          } else {
            showEmpty();
          }
        }).catch(() => { if (isCurrentRun()) showEmpty(); });
        return;
      }
      setHistory((h) => {
        const last = h[h.length - 1];
        if (!last || last.role !== 'assistant') return h;
        return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
      });
      // reasoning 流中断:已有助手气泡但被中断(如服务端强制截断),需恢复草稿并提示用户。
      if (data && data.interrupted) {
        restoreFailedDraft();
        const msg = cfg.doneEmptyMsg ? cfg.doneEmptyMsg(true) : '本轮已中断,已恢复你的输入。';
        setHasError(msg);
        toast?.('生成中断', { kind: 'warn', detail: msg, duration: 4500, code: 'interrupted' });
        rc.sse = null;
        return;
      }
      // 宿主成功收尾附加(pages:保存本轮 toolOps 快照 / setElapsedMs / setLastUsage)。
      // 在 applyState 之前调,以便 pages 把 lastTurnToolOps 写好供 applyState 回填。
      if (onDoneExtra) { try { onDoneExtra(data); } catch (_) {} }
      const payload = (data && data.status) || null;
      if (payload) applyState(payload);
      // 工具可能中途换/建了角色或 persona → 再拉一次最新 state 确保顶部刷新。
      // (tavern-app/Mobile:仅在无 payload 时拉;pages:总是再拉一次。差异由 doneAlwaysRefetch 控制。)
      if (!payload || cfg.doneAlwaysRefetch) {
        api.game.state().then((d) => { if (isCurrentRun()) applyState(d); }).catch(() => {});
      }
      // 刷新列表(更新 last_snippet / updated_at 排序);autotitle 由 onDoneExtra 负责(pages)。
      if (reloadList && !cfg.skipDoneReload) reloadList();
      rc.sse = null;
    },
    on_error: (data) => {
      if (!isCurrentRun()) return;
      clearIdle();
      endStream();
      if (onErrorEvent) { onErrorEvent(data, { setRunning, setHasError, toast, restoreFailedDraft }); return; }
      const realMsg = (data && (data.message || data.detail || data.error)) || '';
      setRunning(false); setHasError(realMsg || true);
      toast?.('生成失败', { kind: 'danger', detail: realMsg || '请重试', code: 'gen_failed' });
      restoreFailedDraft();
    },
    onClose: () => {
      if (!isCurrentRun()) return;
      clearIdle();
      endStream();
      if (gotDone || rc.stopped) { rc.sse = null; return; }
      setRunning((r) => {
        if (!r) return r;
        setHasError(cfg.closeMsg || '连接中断:流式连接关闭但没有收到完成事件。上一条输入已保留,可重试。');
        restoreFailedDraft();
        return false;
      });
      setHistory((h) => {
        const last = h[h.length - 1];
        if (!last || last.role !== 'assistant' || !last.streaming) return h;
        return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
      });
      rc.sse = null;
    },
  });

  return rc.sse;
}

/* ── tool-op 折叠模型(两种,宿主按需注入)──────────────────────────── */

/**
 * inline-anchor 模型(tavern-app):每个 op 带 anchor=触发时正文长度,渲染按 anchor 内联。
 * 用本地 content 长度(精确),回退后端 anchor。
 */
export function toolCallInlineAnchor(data, ctx) {
  ctx.setHistory((h) => {
    let arr = h;
    if (!ctx.isOpened()) { ctx.markOpened(); arr = [...h, { role: 'assistant', content: '', ts: ctx.ts, streaming: true }]; }
    const last = arr[arr.length - 1];
    if (!last || last.role !== 'assistant') return arr;
    const anchor = (last.content || '').length || (data && Number.isFinite(data.anchor) ? data.anchor : 0);
    const op = { tool: (data && data.tool) || '?', args: (data && (data.args_summary || data.args)) || null, anchor, _pending: true };
    return [...arr.slice(0, -1), { ...last, _toolOps: [...(last._toolOps || []), op] }];
  });
}

/** inline 模型 tool_result:回填最后一个 _pending op(tavern-app + MobileTavern 共用)。 */
export function toolResultInline(data, ctx) {
  ctx.setHistory((h) => {
    const last = h[h.length - 1];
    if (!last || last.role !== 'assistant' || !Array.isArray(last._toolOps) || !last._toolOps.length) return h;
    const ops = [...last._toolOps];
    for (let i = ops.length - 1; i >= 0; i--) {
      if (ops[i]._pending) {
        ops[i] = { ...ops[i], ok: !!(data && data.ok), result: (data && data.result_snippet) || null, error: (data && data.error) || null, _pending: false };
        break;
      }
    }
    return [...h.slice(0, -1), { ...last, _toolOps: ops }];
  });
}

/** inline 模型(无 anchor)tool_call:MobileTavern 用(op 不带 anchor)。 */
export function toolCallInline(data, ctx) {
  const op = { tool: (data && data.tool) || '?', args: (data && (data.args_summary || data.args)) || null, _pending: true };
  ctx.setHistory((h) => {
    let arr = h;
    if (!ctx.isOpened()) { ctx.markOpened(); arr = [...h, { role: 'assistant', content: '', ts: ctx.ts, streaming: true }]; }
    const last = arr[arr.length - 1];
    if (!last || last.role !== 'assistant') return arr;
    return [...arr.slice(0, -1), { ...last, _toolOps: [...(last._toolOps || []), op] }];
  });
}
