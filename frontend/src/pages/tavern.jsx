/* Tavern 模式 — Platform 内嵌子页(#tavern,游玩 / Play 导航组下)。
 *
 * 与独立页 src/tavern-app.jsx 的区别:
 *   - 不是整页接管,而是渲染在 Platform 的 Cloudscape AppLayout content 区。
 *   - 左侧自带「两段式」子侧栏(仿 Claude):
 *       上段(不滚动):新建对话 + 角色卡入口 + 快捷模型(AgentModelPicker bare)。
 *       下段(滚动):历史对话(活跃 + 可折叠归档)。
 *   - 主区在 [chat] / [cards] 之间切换,而不离开本页:
 *       view='cards' → 直接内嵌 UserCardsView(它自取 DB,无 props)。
 *       view='chat'  → slim header + 扁平 TavernChatArea + Composer。
 *
 * SSE 状态机(startRun/stopRun/openChat/applyState/import…)从 tavern-app.jsx
 * 原样搬入,保持已测行为不变(切对话前 activate;new chat 由后端 seed first_mes)。
 * 展示性组件(TavernChatArea/TwoCardDrawer/ConfirmModal/RenameModal/relTime)从
 * tavern-app.jsx 复用(已加 export),不重复实现。聊天用 .gc-* / .tavern-chat 样式,
 * 由 Platform.html 追加加载 game-console.css + tavern.css。
 */
import React from 'react';
import { useState, useEffect, useRef, useCallback } from 'react';

import CSButton from '@cloudscape-design/components/button';

import { Icon } from '../game-icons.jsx';
import { GameToastStack } from '../game-app.jsx';
import { Composer } from '../game-composer.jsx';
import { TavernImportModal, UserCardsView } from './cards.jsx';
import { ModelParamsSection } from './settings.jsx';
import {
  TavernChatItem, TavernChatArea, TwoCardDrawer, ConfirmModal, RenameModal,
} from '../tavern-app.jsx';

import './tavern-platform.css';

/* 专门的「选择角色」面板 —— 点一张卡即建对话并进入聊天。
 * 与「角色卡」编辑页(UserCardsView)分离:这里只负责"挑谁聊",不做增删改。 */
function TavernCharacterSelect({ onPick, onCreateNew, onImport }) {
  const [cards, setCards] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    window.api.cards.myList()
      .then((r) => {
        const list = Array.isArray(r) ? r : (r?.cards || r?.items || []);
        if (alive) setCards(list);
      })
      .catch(() => { if (alive) setCards([]); });
    return () => { alive = false; };
  }, []);
  const pick = async (c) => {
    if (busy) return;
    setBusy(true);
    try { await onPick(c); } finally { setBusy(false); }
  };
  return (
    <div className="tvp-select-wrap">
      <div className="tvp-select-head">
        <h2 className="tvp-select-title serif">想和谁聊聊？</h2>
        <div className="tvp-select-actions">
          <CSButton iconName="add-plus" onClick={onCreateNew}>新建角色卡</CSButton>
          <CSButton iconName="upload" onClick={onImport}>导入角色卡</CSButton>
        </div>
      </div>
      {cards == null && <div className="muted-2 tvp-select-empty">加载中…</div>}
      {cards != null && cards.length === 0 && (
        <div className="tvp-select-empty muted-2">
          还没有角色卡。点「新建角色卡」手动创建,或「导入角色卡」拖入 SillyTavern 角色卡(.png / .json / .webp)。
        </div>
      )}
      {cards != null && cards.length > 0 && (
        <div className="tvp-select-grid">
          {cards.map((c) => (
            <button
              key={c.id} className="tvp-select-card" disabled={busy}
              onClick={() => pick(c)} title={`与 ${c.name || '角色'} 对话`}
            >
              <span className="tvp-select-avatar" aria-hidden="true">{(c.name || '?').trim().slice(0, 1)}</span>
              <span className="tvp-select-name">{c.name || '未命名角色'}</span>
              {c.identity ? <span className="tvp-select-identity muted-2">{c.identity}</span> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function TavernPage() {
  /* ── 列表 / 激活态 ───────────────────────────────────────────────── */
  const [chats, setChats] = useState([]);
  const [archivedChats, setArchivedChats] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [showArchived, setShowArchived] = useState(false);

  const [activeId, setActiveId] = useState(null);
  const [activeChat, setActiveChat] = useState(null);
  const [character, setCharacter] = useState(null);
  const [persona, setPersona] = useState(null);
  const [history, setHistory] = useState([]);

  const [text, setText] = useState('');
  const [model, setModel] = useState(null);
  const [running, setRunning] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [lastPlayerText, setLastPlayerText] = useState('');

  // F2:本轮实时秒表(running 时 200ms tick)+ 复用 context 圆环用量
  const [elapsedMs, setElapsedMs] = useState(0);
  const [lastUsage, setLastUsage] = useState(null);
  const tickRef = useRef({ id: null, startedAt: 0 });
  // 类 Claude 自动标题:每个对话本会话只触发一次(后端按 title 是否为空幂等)
  const titledRef = useRef(new Set());

  const [importOpen, setImportOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [paramsOpen, setParamsOpen] = useState(false);   // 采样参数(模型参数)抽屉

  // 输入框「完全复用」游戏页 Composer 所需的状态(与 game-console.jsx 一致):
  const [gameState, setGameState] = useState(null);       // 完整 /api/state → 喂给 Composer(模型名/context 圆环/@mention)
  const [permission, setPermission] = useState('full_access');
  const [showSlash, setShowSlash] = useState(false);
  const [showPlus, setShowPlus] = useState(false);
  const [showModel, setShowModel] = useState(false);
  const [showPerm, setShowPerm] = useState(false);
  const [attachments, setAttachments] = useState([]);
  const [pickedCommand, setPickedCommand] = useState(null);

  // 主区视图:'chat' | 'cards'(在本页内切换,不离开 Tavern)
  const [view, setView] = useState('chat');

  const runRef = useRef({ stopped: false, sse: null, runId: 0, inactivityTimer: null });
  // F1:本轮工具流快照 —— applyState 用后端 history 覆盖时,把它补挂回最末 assistant,
  // 免得 done 后刷新就丢了后台工具流(后端 history 不带 _toolOps,纯前端展示态)。
  const lastTurnToolOpsRef = useRef(null);

  /* ── 列表加载 ──────────────────────────────────────────────────── */
  const reloadList = useCallback(async () => {
    setLoadingList(true);
    try {
      const [a, b] = await Promise.all([
        window.api.tavern.list().catch(() => ({ chats: [] })),
        window.api.tavern.listArchived().catch(() => ({ chats: [] })),
      ]);
      setChats(Array.isArray(a?.chats) ? a.chats : []);
      setArchivedChats(Array.isArray(b?.chats) ? b.chats : []);
    } catch (_) {
      setChats([]); setArchivedChats([]);
    } finally { setLoadingList(false); }
  }, []);

  /* ── 把一份 state 投射进角色/persona/history ──────────────────────── */
  const applyState = useCallback((data) => {
    if (!data) return;
    setGameState(data);  // 完整 state 喂 Composer:模型名/context 圆环/@mention 全靠它
    try {
      const pm = (data.permissions && data.permissions.mode) || (data.data && data.data.permissions && data.data.permissions.mode);
      if (pm) setPermission(pm);
    } catch (_) {}
    const tavern = data.tavern || (data.data && data.data.tavern) || {};
    const char = tavern.character || null;
    setCharacter(char || null);
    const personaCardId = tavern.persona_card_id;
    if (personaCardId != null) {
      window.api.cards.myGet(personaCardId)
        .then((full) => { if (full && full.id) setPersona(full); else setPersona(data.player || null); })
        .catch(() => setPersona(data.player || null));
    } else {
      setPersona(data.player || null);
    }
    if (Array.isArray(data.history)) {
      // 后端 history 的 assistant 消息现在带 tool_ops / reasoning(record_turn 已持久化)→
      // 映射成前端展示字段 _toolOps / _thinking,重开/刷新聊天后工具流 + 思考流仍可见。
      let hist = data.history.map((m) => {
        if (m && m.role === 'assistant') {
          const mm = { ...m };
          if (!mm._toolOps && Array.isArray(m.tool_ops) && m.tool_ops.length) mm._toolOps = m.tool_ops;
          if (!mm._thinking && m.reasoning) mm._thinking = m.reasoning;
          return mm;
        }
        return m;
      });
      // 兜底:刚完成那轮后端可能还没回带(异步持久化时序)→ 用本轮前端快照补挂最末 assistant。
      const ops = lastTurnToolOpsRef.current;
      if (Array.isArray(ops) && ops.length > 0) {
        let lastAssistant = -1;
        for (let i = hist.length - 1; i >= 0; i--) { if (hist[i] && hist[i].role === 'assistant') { lastAssistant = i; break; } }
        if (lastAssistant >= 0 && !(hist[lastAssistant]._toolOps && hist[lastAssistant]._toolOps.length)) {
          hist = hist.map((m, i) => (i === lastAssistant ? { ...m, _toolOps: ops } : m));
        }
        lastTurnToolOpsRef.current = null;
      }
      setHistory(hist);
    }
    if (data.save_id != null) {
      setActiveChat((prev) => ({
        id: data.save_id,
        title: data.save_title || prev?.title || `对话 #${data.save_id}`,
        character_name: (char && char.name) || prev?.character_name || '',
        updated_at: data.save_updated_at || prev?.updated_at || '',
      }));
    }
  }, []);

  /* ── 打开一个对话:激活 → 读 state(含 first_mes seed 的 history)────── */
  const openChat = useCallback(async (chat) => {
    if (!chat || !chat.id) return;
    setView('chat');
    if (runRef.current.sse) { try { runRef.current.sse.stop('switch'); } catch (_) {} runRef.current.sse = null; }
    setRunning(false); setHasError(false); setHistory([]);
    setActiveId(chat.id);
    setActiveChat(chat);
    try {
      await window.api.tavern.activate(chat.id);
      const data = await window.api.game.state();
      applyState(data);
    } catch (e) {
      window.__apiToast?.('打开对话失败', { kind: 'danger', detail: e?.message });
    }
  }, [applyState]);

  useEffect(() => { reloadList(); }, [reloadList]);

  // 首次进入:自动打开最近的活跃对话(如果有)
  useEffect(() => {
    if (activeId != null) return;
    if (loadingList) return;
    if (chats.length > 0) { openChat(chats[0]); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingList, chats]);

  // 卸载:abort 在途流 + 停秒表
  useEffect(() => () => {
    const rc = runRef.current;
    rc.stopped = true;
    if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
    if (rc.sse) { try { rc.sse.stop('unmount'); } catch (_) {} rc.sse = null; }
    if (tickRef.current.id) { clearInterval(tickRef.current.id); tickRef.current.id = null; }
  }, []);

  // F2:本轮秒表 —— 仿 game-console.jsx ~801 的 totalElapsed ticker(200ms)
  const startTicker = useCallback(() => {
    if (tickRef.current.id) clearInterval(tickRef.current.id);
    tickRef.current.startedAt = Date.now();
    setElapsedMs(0);
    tickRef.current.id = setInterval(() => {
      setElapsedMs(Date.now() - tickRef.current.startedAt);
    }, 200);
  }, []);
  const stopTicker = useCallback(() => {
    if (tickRef.current.id) { clearInterval(tickRef.current.id); tickRef.current.id = null; }
  }, []);

  // F3:fork 后(MsgActions 调 branches.continueFrom → dispatch 'rpg-state-reload')
  // 重新拉取当前激活对话的 history;仅当有 tavern 对话激活时才响应。
  useEffect(() => {
    const onReload = () => {
      if (activeId == null) return;
      window.api.game.state().then(applyState).catch(() => {});
    };
    window.addEventListener('rpg-state-reload', onReload);
    return () => window.removeEventListener('rpg-state-reload', onReload);
  }, [activeId, applyState]);

  /* ── 新对话:导入卡 / 现有卡 → 拿 save_id 后打开 ───────────────────── */
  const openSaveId = useCallback(async (saveId, fallbackName) => {
    await reloadList();
    await openChat({ id: saveId, title: fallbackName || `对话 #${saveId}`, character_name: fallbackName || '' });
  }, [reloadList, openChat]);

  const onImportConfirm = useCallback(async (payload) => {
    setImportOpen(false);
    try {
      if (payload.type === 'card') {
        const r = await window.api.tavern.importCharacter(payload.file);
        if (r && r.ok === false) throw new Error(r.error || '导入失败');
        await openSaveId(r.save_id, r.character_name);
        window.__apiToast?.(`已导入「${r.character_name || '角色'}」`, { kind: 'ok', duration: 2000 });
      } else if (payload.type === 'card_json') {
        const r = await window.api.tavern.importCharacter({ json_string: payload.json_string });
        if (r && r.ok === false) throw new Error(r.error || '导入失败');
        await openSaveId(r.save_id, r.character_name);
        window.__apiToast?.(`已导入「${r.character_name || '角色'}」`, { kind: 'ok', duration: 2000 });
      } else if (payload.type === 'chat') {
        const r = await window.api.tavern.importJsonl(payload.jsonl, payload.charName);
        if (r && r.ok === false) throw new Error(r.error || '导入失败');
        await openSaveId(r.save_id, payload.charName);
        window.__apiToast?.(`已导入聊天记录(${r.commits_imported || 0} 条)`, { kind: 'ok', duration: 2200 });
      }
    } catch (e) {
      window.__apiToast?.('导入失败', { kind: 'danger', detail: e?.message });
    }
  }, [openSaveId]);

  // 拖卡进 sidebar / 空状态 → 直接走角色卡导入
  const onDropCard = useCallback(async (file) => {
    if (!file) return;
    if (!/\.(png|json|webp)$/i.test(file.name || '')) {
      window.__apiToast?.('仅支持 .png / .json / .webp 角色卡', { kind: 'warn', duration: 2400 });
      return;
    }
    try {
      const r = await window.api.tavern.importCharacter(file);
      if (r && r.ok === false) throw new Error(r.error || '导入失败');
      await openSaveId(r.save_id, r.character_name);
      window.__apiToast?.(`已导入「${r.character_name || '角色'}」`, { kind: 'ok', duration: 2000 });
    } catch (e) {
      window.__apiToast?.('导入失败', { kind: 'danger', detail: e?.message });
    }
  }, [openSaveId]);

  /* ── 流式发送(复用 api.game.chat,带 idle timeout)──────────────── */
  const stopRun = useCallback(() => {
    runRef.current.stopped = true;
    if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
    runRef.current.runId = (runRef.current.runId || 0) + 1;
    if (runRef.current.sse) { try { runRef.current.sse.stop('manual_stop'); } catch (_) {} runRef.current.sse = null; }
    try { window.api.game.stop(); } catch (_) {}
    setRunning(false);
    stopTicker();
  }, [stopTicker]);

  const startRun = useCallback(async (playerText, opts = {}) => {
    const sentAttachments = Array.isArray(opts.attachments) ? opts.attachments : [];
    const sentCommand = opts.command || null;
    const saveId = activeId;
    if (saveId == null) { window.__apiToast?.('请先选择或新建一个对话', { kind: 'warn', duration: 2400 }); return; }
    const rc = runRef.current;
    if (rc.sse) { rc.runId = (rc.runId || 0) + 1; try { rc.sse.stop('superseded'); } catch (_) {} rc.sse = null; try { window.api.game.stop(); } catch (_) {} }
    if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
    const runId = (rc.runId || 0) + 1;
    rc.runId = runId; rc.stopped = false;
    const isCurrentRun = () => rc.runId === runId;

    const ts = new Date().toLocaleTimeString().slice(0, 5);
    setHistory((h) => [...h, { role: 'user', content: playerText, ts, attachments: sentAttachments.length ? sentAttachments : undefined }]);
    setLastPlayerText(playerText);
    setText('');
    setHasError(false);
    setRunning(true);
    startTicker();

    let openedAssistant = false;
    let gotDone = false;
    // F1:本轮后台工具调用,归组挂到流式 assistant 消息的 _toolOps。
    const turnToolOps = [];
    // 把当前 turnToolOps 写进流式 assistant 气泡(不存在则建占位,使工具流在 token 前也能露出)。
    // 工具通常先于正文 → 第一次 flush 即开占位 assistant 并同步置 openedAssistant,
    // 后续 token 走 append 分支挂到同一气泡。
    const flushToolOps = () => {
      const snapshot = turnToolOps.map((o) => ({ ...o }));
      const hadAssistant = openedAssistant;
      openedAssistant = true;
      setHistory((h) => {
        const last = h[h.length - 1];
        if (hadAssistant && last && last.role === 'assistant') {
          return [...h.slice(0, -1), { ...last, _toolOps: snapshot }];
        }
        return [...h, { role: 'assistant', content: '', ts, streaming: true, _toolOps: snapshot }];
      });
    };
    const STREAM_IDLE_TIMEOUT_MS = 120000;
    const restoreFailedDraft = () => {
      if (!isCurrentRun() || openedAssistant) return;
      setText((cur) => (String(cur || '').trim() ? cur : playerText));
      setHistory((h) => {
        const last = h[h.length - 1];
        if (last && last.role === 'user' && last.content === playerText) return h.slice(0, -1);
        return h;
      });
    };
    const resetIdle = () => {
      if (rc.inactivityTimer) clearTimeout(rc.inactivityTimer);
      rc.inactivityTimer = setTimeout(() => {
        if (!isCurrentRun()) return;
        try { rc.sse && rc.sse.stop && rc.sse.stop('idle_timeout'); } catch (_) {}
        stopTicker();
        restoreFailedDraft();
        setRunning(false);
        setHasError('超过 120 秒没有新输出,已断开。请重试。');
        window.__apiToast?.('生成停滞', { kind: 'warn', detail: '120 秒无响应,已中断', duration: 4000 });
      }, STREAM_IDLE_TIMEOUT_MS);
    };
    resetIdle();

    rc.sse = window.api.game.chat(
      { message: playerText, text: playerText, attachments: sentAttachments, model, command: sentCommand, save_id: saveId },
      {
        onError: (err) => {
          if (!isCurrentRun()) return;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          stopTicker();
          const detail = (err && err.payload && err.payload.message) || (err && err.message) || '请求失败';
          setRunning(false); setHasError(detail);
          window.__apiToast?.('请求失败', { kind: 'danger', detail });
          restoreFailedDraft();
        },
        onAbort: (data) => {
          if (!isCurrentRun()) return;
          const reason = (data && data.reason) || '';
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          stopTicker();
          if (rc.stopped || ['manual_stop', 'superseded', 'unmount', 'switch', 'idle_timeout'].includes(reason)) {
            rc.sse = null; return;
          }
          restoreFailedDraft();
          setRunning(false); setHasError('连接被取消,上一条输入已保留,请重试。');
          rc.sse = null;
        },
        on_status: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          if (data && data.history && Array.isArray(data.history) && !openedAssistant) {
            // 后端可能在 status 里回带最新 history,不强制覆盖流式气泡
          }
        },
        on_reasoning: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          // 思考流挂到(或新建)流式 assistant 气泡的 _thinking,生成中即可见;done 后由后端
          // 持久化的 reasoning 接管(applyState 映射),重开仍可见。
          const piece = (data && (data.text || data.delta)) || '';
          if (!piece) return;
          setHistory((h) => {
            const last = h[h.length - 1];
            if (openedAssistant && last && last.role === 'assistant') {
              return [...h.slice(0, -1), { ...last, _thinking: (last._thinking || '') + piece }];
            }
            openedAssistant = true;
            return [...h, { role: 'assistant', content: '', ts, streaming: true, _thinking: piece }];
          });
        },
        // F1:后台工具流 —— tool_call {tool, arguments};tool_result {ok, result, error}
        on_tool_call: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          turnToolOps.push({
            tool: (data && data.tool) || '工具',
            args: data && data.arguments,
            result: undefined, ok: undefined, error: undefined, _pending: true,
          });
          flushToolOps();
        },
        on_tool_result: (data) => {
          if (!isCurrentRun()) return;
          resetIdle();
          // 关联到最后一个未完成的调用
          let op = null;
          for (let i = turnToolOps.length - 1; i >= 0; i--) { if (turnToolOps[i]._pending) { op = turnToolOps[i]; break; } }
          if (!op) { op = { tool: '工具', args: undefined }; turnToolOps.push(op); }
          op._pending = false;
          op.ok = data ? data.ok !== false : true;
          op.result = data && data.result;
          op.error = data && data.error;
          flushToolOps();
        },
        // F2:用量(context 圆环)—— {total_tokens, context_used, context_max, ...}
        on_usage: (data) => {
          if (!isCurrentRun()) return;
          if (data) setLastUsage(data);
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
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          stopTicker();
          if (data && data.elapsed_ms != null) setElapsedMs(Number(data.elapsed_ms) || 0);
          if (data && data.usage) setLastUsage(data.usage);
          setRunning(false);
          if (!openedAssistant) {
            restoreFailedDraft();
            const msg = data && data.interrupted ? '本轮已中断,已恢复你的输入。' : '本轮没有收到回复,已恢复你的输入。请重试。';
            setHasError(msg);
            window.__apiToast?.(data && data.interrupted ? '生成中断' : '空回复', { kind: 'warn', detail: msg, duration: 4500 });
            rc.sse = null;
            return;
          }
          setHistory((h) => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant') return h;
            return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
          });
          // 保留本轮后台工具流,供 applyState 用后端 history 覆盖后补挂回最末 assistant
          lastTurnToolOpsRef.current = turnToolOps.length ? turnToolOps.map((o) => ({ ...o })) : null;
          const payload = (data && data.status) || null;
          if (payload) applyState(payload);
          // 工具可能在本轮中途换/建了角色或 persona(set_tavern_character 等),done 的
          // status 可能是持久化前快照 → 再拉一次最新 state,确保顶部「她是谁」+ persona 立刻刷新。
          window.api.game.state().then(applyState).catch(() => {});
          // 类 Claude:首轮结束后按内容自动生成标题(后端幂等;每对话本会话只触发一次,失败静默)
          const _sid = activeId;
          if (_sid && !titledRef.current.has(String(_sid))) {
            titledRef.current.add(String(_sid));
            window.api.tavern.autotitle(_sid)
              .then((r) => {
                if (r && r.ok && r.title) {
                  setActiveChat((p) => (p && String(p.id) === String(_sid)) ? { ...p, title: r.title } : p);
                }
              })
              .catch(() => {})
              .finally(() => reloadList());
          } else {
            reloadList();
          }
          rc.sse = null;
        },
        on_error: (data) => {
          if (!isCurrentRun()) return;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          stopTicker();
          const realMsg = (data && (data.message || data.detail || data.error)) || '';
          setRunning(false); setHasError(realMsg || true);
          window.__apiToast?.('生成失败', { kind: 'danger', detail: realMsg || '请重试' });
          restoreFailedDraft();
        },
        onClose: () => {
          if (!isCurrentRun()) return;
          if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
          stopTicker();
          if (gotDone || rc.stopped) { rc.sse = null; return; }
          setRunning((r) => {
            if (!r) return r;
            setHasError('连接中断:流式连接关闭但没有收到完成事件。上一条输入已保留,可重试。');
            restoreFailedDraft();
            return false;
          });
          setHistory((h) => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant' || !last.streaming) return h;
            return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
          });
        },
      }
    );
  }, [activeId, model, applyState, reloadList, startTicker, stopTicker]);

  const onSend = () => {
    if (running) return;
    if (!text.trim() && !attachments.length && !pickedCommand) return;
    const opts = { attachments: attachments.slice(), command: pickedCommand?.id || null };
    setAttachments([]); setPickedCommand(null);
    startRun(text.trim() || (pickedCommand ? (pickedCommand.trigger || '').trim() : '（仅附件,请基于本轮上下文推进。）'), opts);
  };
  const onSendRaw = useCallback((raw) => {
    const t2 = (raw || '').trim();
    if (!t2 || running) return;
    startRun(t2);
  }, [running, startRun]);
  // 输入框「完全复用」游戏页 Composer：斜杠 / 附件 / 清命令 等回调(与 game-console 一致)
  const onSlashPick = (cmd) => {
    if (cmd && typeof cmd.trigger === 'string' && cmd.trigger.endsWith(' ')) {
      setText(cmd.trigger); setPickedCommand(null); setShowSlash(false); return;
    }
    setPickedCommand(cmd); setText(''); setShowSlash(false);
  };
  const onAttachPick = (item) => {
    const fixtures = {
      file: { name: '文件.md', kind: 'file' }, image: { name: '图片.png', kind: 'image' },
      card: { name: '角色卡', kind: 'card' }, world: { name: '世界书', kind: 'world' },
      mcp: { name: 'MCP', kind: 'mcp' }, skill: { name: 'Skill', kind: 'skill' },
    };
    setAttachments((a) => [...a, fixtures[item.id] || { name: item.label || '附件', kind: 'file' }]);
    setShowPlus(false);
  };
  const removeAttachment = (i) => setAttachments((a) => a.filter((_, j) => j !== i));
  const onRetry = useCallback(() => {
    if (running) return;
    let t2 = (lastPlayerText && lastPlayerText.trim()) || '';
    if (!t2) {
      for (let i = history.length - 1; i >= 0; i--) {
        if (history[i]?.role === 'user' && (history[i].content || '').trim()) { t2 = history[i].content.trim(); break; }
      }
    }
    if (!t2) { window.__apiToast?.('没有可重试的输入', { kind: 'warn', duration: 2000 }); return; }
    setHasError(false);
    setHistory((h) => {
      const out = [...h];
      while (out.length && out[out.length - 1].role === 'assistant' && !(out[out.length - 1].content || '').trim()) out.pop();
      if (out.length && out[out.length - 1].role === 'user' && (out[out.length - 1].content || '').trim() === t2) out.pop();
      return out;
    });
    startRun(t2);
  }, [running, lastPlayerText, history, startRun]);

  /* ── rail 操作:rename / archive / delete ───────────────────────── */
  const doRename = useCallback(async (chat, title) => {
    try {
      await window.api.tavern.rename(chat.id, title);
      window.__apiToast?.('已重命名', { kind: 'ok', duration: 1500 });
      reloadList();
      if (String(chat.id) === String(activeId)) setActiveChat((p) => ({ ...(p || {}), title }));
    } catch (e) { window.__apiToast?.('重命名失败', { kind: 'danger', detail: e?.message }); }
  }, [reloadList, activeId]);

  const doArchive = useCallback(async (chat, archived) => {
    try {
      await window.api.tavern.archive(chat.id, archived);
      window.__apiToast?.(archived ? '已归档' : '已取消归档', { kind: 'ok', duration: 1500 });
      reloadList();
    } catch (e) { window.__apiToast?.('归档失败', { kind: 'danger', detail: e?.message }); }
  }, [reloadList]);

  const doDelete = useCallback(async (chat) => {
    setDeleteTarget(null);
    try {
      await window.api.tavern.remove(chat.id);
      window.__apiToast?.('已删除', { kind: 'ok', duration: 1500 });
      if (String(chat.id) === String(activeId)) {
        setActiveId(null); setActiveChat(null); setHistory([]); setCharacter(null); setPersona(null);
      }
      reloadList();
    } catch (e) { window.__apiToast?.('删除失败', { kind: 'danger', detail: e?.message }); }
  }, [reloadList, activeId]);

  const onSavePersona = useCallback(async (payload) => {
    try {
      const saved = await window.api.cards.myUpsert(payload);
      window.__apiToast?.('persona 已保存', { kind: 'ok', duration: 1500 });
      try { const d = await window.api.game.state(); applyState(d); } catch (_) {}
      return saved;
    } catch (e) {
      window.__apiToast?.('保存失败', { kind: 'danger', detail: e?.message });
      throw e;
    }
  }, [applyState]);

  /* ── 派生 ──────────────────────────────────────────────────────── */
  const charName = (character && character.name) || (activeChat && activeChat.character_name) || '角色';
  const charInitial = charName.trim().slice(0, 1);
  const personaName = (persona && persona.name) || '你';
  const exportUrl = activeId != null ? window.api.tavern.exportJsonl(activeId) : null;

  // 空起手(决策1):新建对话默认建「无角色」对话,由 agent 在对话里用 set_tavern_character
  // 自举角色;选卡/导入仍可走拖卡到侧栏 + 角色卡视图。
  const newChat = async () => {
    try {
      const r = await window.api.tavern.create({});
      if (r && r.ok === false) throw new Error(r.error || '新建失败');
      const sid = r && r.save && r.save.id;
      if (sid != null) { setView('chat'); await openSaveId(sid, ''); }
    } catch (e) {
      window.__apiToast?.('新建对话失败', { kind: 'danger', detail: e?.message });
    }
  };

  // 选择角色面板:点一张卡 → 建一段绑定该角色卡的对话 → 直接进入聊天。
  const pickCharacter = async (card) => {
    if (!card || card.id == null) return;
    try {
      const r = await window.api.tavern.create({ character_card_id: card.id });
      if (r && r.ok === false) throw new Error(r.error || '开始对话失败');
      const sid = r && r.save && r.save.id;
      if (sid != null) { setView('chat'); await openSaveId(sid, card.name || ''); }
    } catch (e) {
      window.__apiToast?.('开始对话失败', { kind: 'danger', detail: e?.message });
    }
  };

  // F2:本轮秒表展示 mm:ss(或 s.s)。
  const fmtElapsed = (ms) => {
    const total = Math.max(0, Math.floor((ms || 0) / 1000));
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  };
  // ContextUsage 接 used/cap props(优先用流里的 context_used/max,回退 total_tokens)。
  const usageUsed = lastUsage
    ? (lastUsage.context_used != null ? lastUsage.context_used : lastUsage.total_tokens)
    : null;
  const usageCap = lastUsage && lastUsage.context_max != null ? lastUsage.context_max : null;
  // 本轮 meta(用时 + token + 费用)→ 渲染进**最新一条消息的操作栏**(MsgActions 同一行),
  // 生成中的实时计时改在「正在思考」气泡里显示,都不再浮在页脚右下角。
  const _fmtK = (n) => { n = Number(n) || 0; return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n); };
  const lastMeta = (!running && lastUsage && (lastUsage.input_tokens || lastUsage.output_tokens))
    ? `⏱ ${fmtElapsed(elapsedMs)} · ↑${_fmtK(lastUsage.input_tokens)}${lastUsage.cached_input_tokens ? `(缓存${_fmtK(lastUsage.cached_input_tokens)})` : ''} ↓${_fmtK(lastUsage.output_tokens)}${lastUsage.cost_usd ? ` · $${Number(lastUsage.cost_usd).toFixed(4)}` : ''}`
    : null;

  /* ══════════════════════════════════════════════════════════════ */
  return (
    <div className="tvp-root tavern-chat">
      <GameToastStack />

      {/* ── 左:两段式子侧栏 ──────────────────────────────────────── */}
      <aside className="tvp-side">
        {/* 上段(固定):新建 / 角色卡 / 快捷模型 */}
        <div className="tvp-side-top">
          <CSButton variant="primary" iconName="add-plus" onClick={newChat} fullWidth>
            新建对话
          </CSButton>
          <CSButton
            variant={view === 'select' ? 'normal' : 'link'}
            iconName="user-profile"
            onClick={() => setView('select')}
            fullWidth
          >
            选择角色
          </CSButton>
          <CSButton
            variant={view === 'cards' ? 'normal' : 'link'}
            iconName="contact"
            onClick={() => setView('cards')}
            fullWidth
          >
            角色卡(编辑)
          </CSButton>
          <CSButton
            variant="link"
            iconName="settings"
            onClick={() => setParamsOpen(true)}
            fullWidth
          >
            模型参数
          </CSButton>
        </div>

        {/* 下段(滚动):历史对话 */}
        <div
          className="tvp-side-list"
          onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add('tvp-drop'); }}
          onDragLeave={(e) => e.currentTarget.classList.remove('tvp-drop')}
          onDrop={(e) => { e.preventDefault(); e.currentTarget.classList.remove('tvp-drop'); const f = e.dataTransfer?.files?.[0]; if (f) onDropCard(f); }}
        >
          <div className="tvp-list-label">历史对话</div>
          {loadingList && <div className="tv-rail-empty muted-2">加载中…</div>}
          {!loadingList && chats.length === 0 && (
            <div className="tv-rail-empty muted-2">
              <Icon name="upload" size={20} style={{ opacity: 0.5, marginBottom: 6 }} />
              <div>还没有对话</div>
              <div style={{ fontSize: 11.5, marginTop: 4 }}>点「新建对话」或拖入一张角色卡</div>
            </div>
          )}
          {chats.map((c) => (
            <TavernChatItem
              key={c.id} chat={c} active={String(c.id) === String(activeId)}
              onOpen={openChat}
              onRename={(chat, title) => doRename(chat, title)}
              onArchive={doArchive}
              onDelete={(chat) => setDeleteTarget(chat)}
              archived={false}
            />
          ))}

          {archivedChats.length > 0 && (
            <div className="tv-archived-section">
              <button className="tv-archived-toggle" onClick={() => setShowArchived((v) => !v)}>
                <Icon name={showArchived ? 'chevron_down' : 'chevron_right'} size={12} />
                已归档 ({archivedChats.length})
              </button>
              {showArchived && archivedChats.map((c) => (
                <TavernChatItem
                  key={c.id} chat={c} active={String(c.id) === String(activeId)}
                  onOpen={openChat}
                  onRename={(chat, title) => doRename(chat, title)}
                  onArchive={doArchive}
                  onDelete={(chat) => setDeleteTarget(chat)}
                  archived={true}
                />
              ))}
            </div>
          )}
        </div>
      </aside>

      {/* ── 右:主区(chat / cards 切换)─────────────────────────── */}
      <main className="tvp-main">
        {view === 'select' ? (
          <div className="tvp-cards-wrap">
            <div className="tvp-cards-body">
              <TavernCharacterSelect
                onPick={pickCharacter}
                onCreateNew={() => setView('cards')}
                onImport={() => setImportOpen(true)}
              />
            </div>
          </div>
        ) : view === 'cards' ? (
          /* 角色卡页 = 纯编辑/创建(无「返回对话」按钮;切回对话走左侧栏)。 */
          <div className="tvp-cards-wrap">
            <div className="tvp-cards-body">
              <UserCardsView />
            </div>
          </div>
        ) : activeId == null ? (
          <div className="tvp-hero">
            <div className="tvp-hero-inner">
              <div className="tvp-hero-mark" aria-hidden="true">✻</div>
              <h1 className="tvp-hero-title serif">想和谁聊聊？</h1>
              <p className="tvp-hero-sub muted">新建一段对话,或从角色卡里挑一位。</p>
              <div className="tvp-hero-actions">
                <CSButton variant="primary" iconName="add-plus" onClick={newChat}>新建对话</CSButton>
                <CSButton iconName="user-profile" onClick={() => setView('select')}>选择角色</CSButton>
              </div>
              <div
                className="tvp-hero-drop"
                onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add('tvp-drop'); }}
                onDragLeave={(e) => e.currentTarget.classList.remove('tvp-drop')}
                onDrop={(e) => { e.preventDefault(); e.currentTarget.classList.remove('tvp-drop'); const f = e.dataTransfer?.files?.[0]; if (f) onDropCard(f); }}
                onClick={newChat}
                role="button" tabIndex={0}
              >
                <Icon name="upload" size={22} style={{ color: 'var(--accent)' }} />
                <div className="tvp-hero-drop-sub muted-2">或把角色卡(.png / .json / .webp)拖到这里</div>
              </div>
            </div>
          </div>
        ) : (
          <>
            <header className="tvp-chat-head">
              <button className="tvp-chat-title" onClick={() => setDrawerOpen(true)} data-tip="角色 / persona">
                <span className="tvp-chat-name">{charName}</span>
                <Icon name="chevron_down" size={12} style={{ opacity: 0.5 }} />
              </button>
              <div className="tvp-chat-head-actions">
                {running && (
                  <span className="tvp-timer" aria-live="polite" data-tip="本轮用时">
                    <span className="tvp-timer-dot" />
                    {fmtElapsed(elapsedMs)}
                  </span>
                )}
                {exportUrl && (
                  <a className="iconbtn" href={exportUrl} target="_blank" rel="noopener" data-tip="导出 JSONL">
                    <Icon name="download" size={15} />
                  </a>
                )}
                <button className="iconbtn" onClick={() => setDrawerOpen(true)} data-tip="角色卡 / persona">
                  <Icon name="cards" size={15} />
                </button>
              </div>
            </header>

            <TavernChatArea
              history={history} running={running} saveId={activeId}
              charName={charName} charInitial={charInitial} personaName={personaName}
              hasError={hasError} onRetry={onRetry}
              lastMeta={lastMeta} elapsedLabel={running ? fmtElapsed(elapsedMs) : null}
            />

            <div className="gc-foot-wrap tvp-foot">
              {/* 完全复用游戏页 Composer:同一组件、同一组控件(+ / 继续 / 完全访问 / 模型 / context 圆环),
                  靠 gameState 显示真实模型名 + context 圆环 + @mention。不再 hide 任何控件。 */}
              <Composer
                text={text} setText={setText} onSend={onSend} onStop={stopRun} running={running}
                onSendRaw={onSendRaw} permission={permission} setPermission={setPermission}
                model={model} setModel={setModel} composerMode="writing"
                suggestions={gameState?.suggestions} gameState={gameState}
                attachments={attachments} removeAttachment={removeAttachment}
                onAttachPick={onAttachPick} onSlashPick={onSlashPick}
                pickedCommand={pickedCommand} onClearCommand={() => setPickedCommand(null)}
                showSlash={showSlash} showPlus={showPlus} showModel={showModel} showPerm={showPerm}
                toggleSlash={() => { setShowSlash((s) => !s); setShowPlus(false); setShowModel(false); setShowPerm(false); }}
                togglePlus={() => { setShowPlus((s) => !s); setShowSlash(false); setShowModel(false); setShowPerm(false); }}
                toggleModel={() => { setShowModel((s) => !s); setShowSlash(false); setShowPlus(false); setShowPerm(false); }}
                togglePerm={() => { setShowPerm((s) => !s); setShowSlash(false); setShowPlus(false); setShowModel(false); }}
              />
              {/* 计时器/token/费用 不再浮在页脚:生成中显示在「正在思考」气泡,完成后并入最新消息操作栏。 */}
            </div>
          </>
        )}
      </main>

      {/* ── 弹窗 / 抽屉 ──────────────────────────────────────────── */}
      <TavernImportModal open={importOpen} onClose={() => setImportOpen(false)} onConfirm={onImportConfirm} />

      <TwoCardDrawer
        open={drawerOpen} character={character} persona={persona}
        onClose={() => setDrawerOpen(false)}
        onSavePersona={onSavePersona}
      />

      {/* 采样参数(模型参数)抽屉 —— 复用 settings 的 ModelParamsSection,写同一份偏好,影响所有调用 */}
      {paramsOpen && (
        <div className="tv-drawer-backdrop" onClick={() => setParamsOpen(false)}>
          <div className="tv-drawer" onClick={(e) => e.stopPropagation()}>
            <header className="tv-drawer-head">
              <strong style={{ fontSize: 14 }}>模型参数</strong>
              <button className="iconbtn" onClick={() => setParamsOpen(false)} data-tip="关闭">
                <Icon name="close" size={15} />
              </button>
            </header>
            <div className="tv-drawer-body"><ModelParamsSection /></div>
          </div>
        </div>
      )}

      <RenameModal
        target={renameTarget}
        onClose={() => setRenameTarget(null)}
        onConfirm={(title) => { const tgt = renameTarget; setRenameTarget(null); if (tgt) doRename(tgt, title); }}
      />

      <ConfirmModal
        open={!!deleteTarget}
        title="删除对话?"
        body={<>这将永久删除「<strong>{deleteTarget?.title || deleteTarget?.character_name || ''}</strong>」及其全部聊天记录,无法恢复。</>}
        confirmLabel="删除"
        danger
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && doDelete(deleteTarget)}
      />
    </div>
  );
}
