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
import { useTranslation } from 'react-i18next';

import CSButton from '@cloudscape-design/components/button';

import { Icon } from '../game-icons.jsx';
import { Composer, ConfirmStrip } from '../game-composer.jsx';
import { TavernImportModal, UserCardsView } from './cards.jsx';
import { ModelParamsSection } from './settings.jsx';
import AgentModelPicker from '../components/AgentModelPicker.jsx';
import ModelConfigInterceptModal, { capConfig } from '../components/ModelConfigInterceptModal.jsx';
import { isCredentialsError } from '../lib/creds.js';
import {
  useTavernChatRun, applyTavernState, abortRun,
} from '../hooks/useTavernChatRun.js';
import {
  TavernChatItem, TavernChatArea, TwoCardDrawer, ConfirmModal, RenameModal,
} from '../tavern-app.jsx';

import './tavern-platform.css';

/* 专门的「选择角色」面板 —— 点一张卡即建对话并进入聊天。
 * 与「角色卡」编辑页(UserCardsView)分离:这里只负责"挑谁聊",不做增删改。 */
function TavernCharacterSelect({ onPick, onCreateNew, onImport }) {
  const { t } = useTranslation();
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
        <h2 className="tvp-select-title serif">{t('tavern_page.select.heading')}</h2>
        <div className="tvp-select-actions">
          <CSButton iconName="add-plus" onClick={onCreateNew}>{t('tavern_page.select.new_card_btn')}</CSButton>
          <CSButton iconName="upload" onClick={onImport}>{t('tavern_page.select.import_card_btn')}</CSButton>
        </div>
      </div>
      {cards == null && <div className="muted-2 tvp-select-empty">{t('common.loading')}</div>}
      {cards != null && cards.length === 0 && (
        <div className="tvp-select-empty muted-2">
          {t('tavern_page.select.empty_hint')}
        </div>
      )}
      {cards != null && cards.length > 0 && (
        <div className="tvp-select-grid">
          {cards.map((c) => (
            <button
              key={c.id} className="tvp-select-card" disabled={busy}
              onClick={() => pick(c)} title={t('tavern_page.select.card_title', { name: c.name || t('tavern_page.char_fallback') })}
            >
              <span className="tvp-select-avatar" aria-hidden="true">{(c.name || '?').trim().slice(0, 1)}</span>
              <span className="tvp-select-name">{c.name || t('tavern_page.select.unnamed_card')}</span>
              {c.identity ? <span className="tvp-select-identity muted-2">{c.identity}</span> : null}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function TavernPage() {
  const { t } = useTranslation();
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
  const [immersive, setImmersive] = useState(false);
  const [aiReplyLoading, setAiReplyLoading] = useState(false);
  // Item 3:发消息时后端报「缺 LLM key」(credentials_required / needs_credentials)→ 在 Composer 上方
  // 内联一张引导卡片,让用户就地加 key,然后重试上一条输入(复用 onRetry → lastPlayerText)。
  const [needsCreds, setNeedsCreds] = useState(false);
  // config_card hard 拦截弹窗(mode model_not_configured)
  const [hardConfigItem, setHardConfigItem] = useState(null);

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
  const [exportTarget, setExportTarget] = useState(null);  // 导出二次确认
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

  // F1:本轮工具流快照 —— applyState 用后端 history 覆盖时,把它补挂回最末 assistant,
  // 免得 done 后刷新就丢了后台工具流(后端 history 不带 _toolOps,纯前端展示态)。
  const lastTurnToolOpsRef = useRef(null);
  // F1:本轮(run-scoped)后台工具调用累积数组,每轮 onStart 时重置;flushToolOps 用它整组快照。
  const turnToolOpsRef = useRef([]);

  // 收口的酒馆 SSE 状态机(runRef + 共用 startRun/stopRun,折叠语义见 lib/tavern-chat-run.js)。
  // pages 的秒表(stopTicker)在下方本地 stopRun 包装里追加,故此处只交 setRunning。
  const { runRef, startRun: runChat, stopRun: hookStopRun } = useTavernChatRun({ setRunning });

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

  /* ── 把一份 state 投射进角色/persona/history(收口到 applyTavernState 核心三段 +
   *   pages 叠加 setGameState/setPermission + 持久化 tool_ops/_thinking 的 history 映射)──── */
  // 后端 history 的 assistant 消息带 tool_ops / reasoning(record_turn 已持久化)→ 映射成
  // 前端展示字段 _toolOps / _thinking,重开/刷新后工具流 + 思考流仍可见;再用本轮前端快照
  // (lastTurnToolOpsRef)兜底补挂最末 assistant(异步持久化时序未回带时)。
  const mapHistoryWithToolOps = useCallback((rawHistory) => {
    let hist = rawHistory.map((m) => {
      if (m && m.role === 'assistant') {
        const mm = { ...m };
        if (!mm._toolOps && Array.isArray(m.tool_ops) && m.tool_ops.length) mm._toolOps = m.tool_ops;
        if (!mm._thinking && m.reasoning) mm._thinking = m.reasoning;
        return mm;
      }
      return m;
    });
    const ops = lastTurnToolOpsRef.current;
    if (Array.isArray(ops) && ops.length > 0) {
      let lastAssistant = -1;
      for (let i = hist.length - 1; i >= 0; i--) { if (hist[i] && hist[i].role === 'assistant') { lastAssistant = i; break; } }
      if (lastAssistant >= 0 && !(hist[lastAssistant]._toolOps && hist[lastAssistant]._toolOps.length)) {
        hist = hist.map((m, i) => (i === lastAssistant ? { ...m, _toolOps: ops } : m));
      }
      lastTurnToolOpsRef.current = null;
    }
    return hist;
  }, []);

  const applyState = useCallback((data) => {
    applyTavernState(data, {
      setCharacter, setPersona, setHistory, setActiveChat,
      setGameState, setPermission,
      setImmersive,
      mapHistory: mapHistoryWithToolOps,
    });
  }, [mapHistoryWithToolOps]);

  // 平台首页空态输入框新建对话后,把首句暂存在 sessionStorage(rpg_tavern_pending_first);
  // 打开命中同一 save 时自动发出。openChat 在激活完成后把首句记进 pendingFirstRef 并 bump
  // pendingFirstTick → 下方 effect 触发 startRun(显式带 saveId,绕开 activeId 闭包旧值)。
  const pendingFirstRef = useRef(null);
  const [pendingFirstTick, setPendingFirstTick] = useState(0);
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
      // 首页空态输入框转交的首句:命中本 save → 记下 + bump tick,交给下方 effect 自动发送。
      try {
        const raw = sessionStorage.getItem('rpg_tavern_pending_first');
        if (raw) {
          const p = JSON.parse(raw);
          if (p && String(p.save_id) === String(chat.id) && (p.text || '').trim()) {
            sessionStorage.removeItem('rpg_tavern_pending_first');
            pendingFirstRef.current = { saveId: chat.id, text: String(p.text).trim() };
            setPendingFirstTick((n) => n + 1);
          }
        }
      } catch (_) {}
    } catch (e) {
      window.__apiToast?.(t('tavern_page.toast.open_chat_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [applyState]);

  useEffect(() => { reloadList(); }, [reloadList]);

  // 首次进入:自动打开最近的活跃对话(如果有)。
  // 若首页空态输入框刚转交了一个 pending save(rpg_tavern_pending_first),优先打开它,
  // 让首句能在那个对话里自动发出(而非默认 chats[0])。
  useEffect(() => {
    if (activeId != null) return;
    if (loadingList) return;
    let pendingSaveId = null;
    try {
      const raw = sessionStorage.getItem('rpg_tavern_pending_first');
      if (raw) { const p = JSON.parse(raw); if (p && p.save_id != null) pendingSaveId = p.save_id; }
    } catch (_) {}
    if (pendingSaveId != null) {
      const hit = chats.find((c) => String(c.id) === String(pendingSaveId));
      openChat(hit || { id: pendingSaveId, title: t('tavern_page.new_chat_title'), character_name: '' });
      return;
    }
    if (chats.length > 0) { openChat(chats[0]); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingList, chats]);

  // 卸载:abort 在途流 + 停秒表
  useEffect(() => () => {
    abortRun(runRef.current, 'unmount');
    if (tickRef.current.id) { clearInterval(tickRef.current.id); tickRef.current.id = null; }
  // eslint-disable-next-line react-hooks/exhaustive-deps
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
    await openChat({ id: saveId, title: fallbackName || t('tavern_page.chat_id_fallback', { id: saveId }), character_name: fallbackName || '' });
  }, [reloadList, openChat]);

  const onImportConfirm = useCallback(async (payload) => {
    setImportOpen(false);
    try {
      if (payload.type === 'card') {
        const r = await window.api.tavern.importCharacter(payload.file);
        if (r && r.ok === false) throw new Error(r.error || t('tavern_page.toast.import_failed'));
        await openSaveId(r.save_id, r.character_name);
        window.__apiToast?.(t('tavern_page.toast.imported_card', { name: r.character_name || t('tavern_page.char_fallback') }), { kind: 'ok', duration: 2000 });
      } else if (payload.type === 'card_json') {
        const r = await window.api.tavern.importCharacter({ json_string: payload.json_string });
        if (r && r.ok === false) throw new Error(r.error || t('tavern_page.toast.import_failed'));
        await openSaveId(r.save_id, r.character_name);
        window.__apiToast?.(t('tavern_page.toast.imported_card', { name: r.character_name || t('tavern_page.char_fallback') }), { kind: 'ok', duration: 2000 });
      } else if (payload.type === 'chat') {
        const r = await window.api.tavern.importJsonl(payload.jsonl, payload.charName);
        if (r && r.ok === false) throw new Error(r.error || t('tavern_page.toast.import_failed'));
        await openSaveId(r.save_id, payload.charName);
        window.__apiToast?.(t('tavern_page.toast.imported_chat', { count: r.commits_imported || 0 }), { kind: 'ok', duration: 2200 });
      }
    } catch (e) {
      window.__apiToast?.(t('tavern_page.toast.import_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [openSaveId]);

  // 拖卡进 sidebar / 空状态 → 直接走角色卡导入
  const onDropCard = useCallback(async (file) => {
    if (!file) return;
    if (!/\.(png|json|webp)$/i.test(file.name || '')) {
      window.__apiToast?.(t('tavern_page.toast.unsupported_file'), { kind: 'warn', duration: 2400 });
      return;
    }
    try {
      const r = await window.api.tavern.importCharacter(file);
      if (r && r.ok === false) throw new Error(r.error || t('tavern_page.toast.import_failed'));
      await openSaveId(r.save_id, r.character_name);
      window.__apiToast?.(t('tavern_page.toast.imported_card', { name: r.character_name || t('tavern_page.char_fallback') }), { kind: 'ok', duration: 2000 });
    } catch (e) {
      window.__apiToast?.(t('tavern_page.toast.import_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [openSaveId]);

  /* ── 流式发送(收口到 useTavernChatRun;折叠语义见 lib/tavern-chat-run.js)──────── */
  // 秒表是 pages 独有 → 包一层:共用停流逻辑(hookStopRun)+ 本端停秒表。
  const stopRun = useCallback(() => {
    hookStopRun();
    stopTicker();
  }, [hookStopRun, stopTicker]);

  // F1:pages 的 tool-op 模型 = turnToolOps 整组快照 + flushToolOps(工具先于正文也能露出占位)。
  // 与 tavern-app 的 inline-anchor 模型不同,逐字保留;turnToolOpsRef 每轮 onStart 重置。
  const flushPagesToolOps = useCallback((ctx) => {
    const turnToolOps = turnToolOpsRef.current;
    const snapshot = turnToolOps.map((o) => ({ ...o }));
    const hadAssistant = ctx.isOpened();
    ctx.markOpened();
    ctx.setHistory((h) => {
      const last = h[h.length - 1];
      if (hadAssistant && last && last.role === 'assistant') {
        return [...h.slice(0, -1), { ...last, _toolOps: snapshot }];
      }
      return [...h, { role: 'assistant', content: '', ts: ctx.ts, streaming: true, _toolOps: snapshot }];
    });
  }, []);

  const startRun = useCallback(async (playerText, opts = {}) => {
    const sentAttachments = Array.isArray(opts.attachments) ? opts.attachments : [];
    const sentCommand = opts.command || null;
    // opts.saveId:显式指定目标 save(自动发首句时用,绕开 activeId 闭包可能还是旧值的问题)。
    const saveId = (opts.saveId != null) ? opts.saveId : activeId;
    const _sid = activeId;   // autotitle 用(逐字保留:沿用旧实现读 activeId)

    runChat({
      saveId, model, playerText, applyState,
      setHistory, setRunning, setText, setHasError, setLastPlayerText,
      toast: (title, o) => window.__apiToast?.(title, o),
      reloadList,
      // pages 额外:chat body 带附件/命令;用户气泡带附件;done 总是再拉一次 state(刷新「她是谁」);
      // 列表刷新由 onDoneExtra 的 autotitle finally 负责,故跳过 hook 默认 reload。
      chatExtra: { attachments: sentAttachments, command: sentCommand },
      userAttachments: sentAttachments.length ? sentAttachments : undefined,
      doneAlwaysRefetch: true,
      skipDoneReload: true,
      // 提交前:重置本轮工具数组 + 撤「缺 key」引导卡 + 起秒表。
      onStart: () => { turnToolOpsRef.current = []; setNeedsCreds(false); startTicker(); },
      // idle/任何结束:停秒表。
      onIdleExtra: stopTicker,
      onStreamEndExtra: stopTicker,
      // tool-op:flush 模型(turnToolOps 整组快照)。
      onToolCall: (data, ctx) => {
        turnToolOpsRef.current.push({
          tool: (data && data.tool) || t('tavern_page.tool_fallback'),
          args: data && data.arguments,
          result: undefined, ok: undefined, error: undefined, _pending: true,
        });
        flushPagesToolOps(ctx);
      },
      onToolResult: (data, ctx) => {
        const turnToolOps = turnToolOpsRef.current;
        let op = null;
        for (let i = turnToolOps.length - 1; i >= 0; i--) { if (turnToolOps[i]._pending) { op = turnToolOps[i]; break; } }
        if (!op) { op = { tool: t('tavern_page.tool_fallback'), args: undefined }; turnToolOps.push(op); }
        op._pending = false;
        op.ok = data ? data.ok !== false : true;
        op.result = data && data.result;
        op.error = data && data.error;
        flushPagesToolOps(ctx);
      },
      // F2:用量(context 圆环)。
      onUsage: (data) => setLastUsage(data),
      // 无条件收尾(空回复也跑,原实现在 !openedAssistant 之前):写本轮 elapsed/usage。
      onDoneAlways: (data) => {
        if (data && data.elapsed_ms != null) setElapsedMs(Number(data.elapsed_ms) || 0);
        if (data && data.usage) setLastUsage(data.usage);
      },
      // 成功收尾(applyState 之前调):保留本轮工具流快照(供 applyState 回填)
      // + 类 Claude 首轮自动标题(后端幂等;每对话本会话只触发一次,失败静默)。
      onDoneExtra: () => {
        const ops = turnToolOpsRef.current;
        lastTurnToolOpsRef.current = ops.length ? ops.map((o) => ({ ...o })) : null;
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
      },
      // Item 3:「发消息时没 key」→ 内联引导卡片(非普通报错红条),让用户就地配 key 再重试。
      onErrorEvent: (data, h) => {
        const realMsg = (data && (data.message || data.detail || data.error)) || '';
        h.setRunning(false);
        if (isCredentialsError(realMsg)) {
          setNeedsCreds(true);
          h.setHasError(false);
          window.__apiToast?.(t('tavern_page.toast.needs_key_title'), { kind: 'warn', detail: t('tavern_page.toast.needs_key_detail'), duration: 4500 });
        } else {
          h.setHasError(realMsg || true);
          window.__apiToast?.(t('tavern_page.toast.gen_failed'), { kind: 'danger', detail: realMsg || t('tavern_page.toast.retry_hint') });
        }
        h.restoreFailedDraft();
      },
    });
  }, [activeId, model, applyState, reloadList, startTicker, stopTicker, runChat, flushPagesToolOps]);

  const onSend = () => {
    if (running) return;
    if (!text.trim() && !attachments.length && !pickedCommand) return;
    const opts = { attachments: attachments.slice(), command: pickedCommand?.id || null };
    setAttachments([]); setPickedCommand(null);
    startRun(text.trim() || (pickedCommand ? (pickedCommand.trigger || '').trim() : t('tavern_page.attachment_only_msg')), opts);
  };
  const onSendRaw = useCallback((raw) => {
    const t2 = (raw || '').trim();
    if (!t2 || running) return;
    startRun(t2);
  }, [running, startRun]);

  // 首页空态输入框转交的首句自动发送:openChat 激活完成后 bump pendingFirstTick 触发本 effect,
  // 直接带显式 saveId 调 startRun(不依赖 activeId 闭包)。失败兜底:预填到输入框让用户手发。
  useEffect(() => {
    const p = pendingFirstRef.current;
    if (!p || running) return;
    pendingFirstRef.current = null;
    try {
      startRun(p.text, { saveId: p.saveId });
    } catch (_) {
      setText(p.text);  // 兜底:自动发送失败则预填,用户手动点发送
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingFirstTick]);

  // F#2 弹出式选择:ask_player_choice 工具写 pending_questions → ConfirmStrip 渲染。
  // 玩家点选 → 清掉该 pending(后端+乐观)→ 把选择作为下一条消息发回角色。
  const pendingQuestions = (gameState && (
    (gameState.permissions && gameState.permissions.pending_questions) ||
    (gameState.data && gameState.data.permissions && gameState.data.permissions.pending_questions)
  )) || [];
  // ConfirmStrip 契约:onAnswer(handleId, choice)(两参),onDismiss(handleId)(一参);handleId={id,index}。
  const _dropPending = (id, index) => setGameState((gs) => {
    if (!gs) return gs;
    const perms = gs.permissions || (gs.data && gs.data.permissions) || {};
    const pq = (perms.pending_questions || []).filter((q, i) => !((id != null && q.id === id) || (id == null && i === index)));
    if (gs.permissions) return { ...gs, permissions: { ...gs.permissions, pending_questions: pq } };
    return { ...gs, data: { ...(gs.data || {}), permissions: { ...((gs.data && gs.data.permissions) || {}), pending_questions: pq } } };
  });
  const onChoiceAnswer = useCallback(async (handleId, choice) => {
    const id = handleId && handleId.id; const index = handleId && handleId.index;
    try { await window.api.game.clearQuestions({ id, index, choice }); } catch (_) {}
    _dropPending(id, index);
    const c = (choice == null ? '' : String(choice)).trim();
    if (c && !running) startRun(c);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, startRun]);
  const onChoiceDismiss = useCallback(async (handleId) => {
    const id = handleId && handleId.id; const index = handleId && handleId.index;
    try { await window.api.game.clearQuestions({ id, index, choice: null }); } catch (_) {}
    _dropPending(id, index);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── config_card(agent:config_card)处理 —— 与 game-console 行为对齐,复用 _dropPending + startRun ──
  const _clearConfig = useCallback(async (handleId) => {
    const id = handleId && handleId.id; const index = handleId && handleId.index;
    try { await window.api.game.clearQuestions({ id, index, choice: null }); } catch (_) {}
    _dropPending(id, index);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // mode "ask_default":持久化能力偏好 → 清卡片 → startRun("用 X 生成")
  const onConfigDefault = useCallback(async (handleId, item, model) => {
    const cap = capConfig(item.capability);
    const aid = item.api_id || '';
    if (aid && model) {
      try {
        await window.api.account.preferences({
          [`${cap.prefPrefix}.api_id`]: aid,
          [`${cap.prefPrefix}.model_real_name`]: model,
        });
      } catch (e) { window.__apiToast?.(t('tavern_page.toast.save_prefs_failed'), { kind: 'danger', detail: e?.message }); }
    }
    await _clearConfig(handleId);
    if (!running) startRun(t('tavern_page.config_gen_prompt', { model: model || cap.label }));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, startRun]);
  // mode "missing_key":配好后「继续」
  const onConfigContinue = useCallback(async (handleId, item, label) => {
    await _clearConfig(handleId);
    if (!running) startRun(label || t('tavern_page.config_continue_prompt'));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, startRun]);
  const onConfigSettings = useCallback(() => { try { window.location.hash = 'settings-models'; } catch (_) {} }, []);
  // mode "model_not_configured"(hard):开阻塞弹窗
  const onHardConfig = useCallback((item) => setHardConfigItem(item), []);
  const onHardResolve = useCallback(async (chosenModel) => {
    const item = hardConfigItem; if (!item) return;
    setHardConfigItem(null);
    await _clearConfig({ id: item.id != null ? item.id : null, index: null });
    if (!running) startRun(t('tavern_page.config_gen_prompt', { model: chosenModel || item.model || '' }));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hardConfigItem, running, startRun]);
  const onHardCancel = useCallback(async () => {
    const item = hardConfigItem; if (!item) { setHardConfigItem(null); return; }
    setHardConfigItem(null);
    await _clearConfig({ id: item.id != null ? item.id : null, index: null });
    window.__apiToast?.(t('tavern_page.toast.gen_cancelled'), { kind: 'info', duration: 2000 });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hardConfigItem]);

  // 输入框「完全复用」游戏页 Composer：斜杠 / 附件 / 清命令 等回调(与 game-console 一致)
  const onSlashPick = (cmd) => {
    if (cmd && typeof cmd.trigger === 'string' && cmd.trigger.endsWith(' ')) {
      setText(cmd.trigger); setPickedCommand(null); setShowSlash(false); return;
    }
    setPickedCommand(cmd); setText(''); setShowSlash(false);
  };
  // F#1:真实文件上传 —— file/image/card 走文件选择器读成 data_url 真实附件;
  // 角色卡(.png/.json/.webp)上传后,agent 可用 import_character_card 工具解析导入。
  const fileInputRef = useRef(null);
  const pendingAttachRef = useRef({ kind: 'file' });
  const onAttachPick = (item) => {
    setShowPlus(false);
    if (item.id === 'file' || item.id === 'image' || item.id === 'card') {
      pendingAttachRef.current = { kind: item.id };
      const inp = fileInputRef.current;
      if (inp) {
        inp.value = '';
        inp.accept = item.id === 'card' ? '.png,.json,.webp' : (item.id === 'image' ? 'image/*' : '');
        inp.click();
      }
      return;
    }
    const fixtures = { world: { name: t('tavern_page.attach.worldbook'), kind: 'world' }, mcp: { name: 'MCP', kind: 'mcp' }, skill: { name: 'Skill', kind: 'skill' } };
    setAttachments((a) => [...a, fixtures[item.id] || { name: item.label || t('tavern_page.attach.file_fallback'), kind: 'file' }]);
  };
  const onFilePicked = (e) => {
    const f = e.target && e.target.files && e.target.files[0];
    if (!f) return;
    const kind = (pendingAttachRef.current && pendingAttachRef.current.kind) || 'file';
    if (f.size > 12 * 1024 * 1024) { window.__apiToast?.(t('tavern_page.toast.file_too_large'), { kind: 'warn', duration: 2400 }); return; }
    const reader = new FileReader();
    reader.onload = () => setAttachments((a) => [...a, { name: f.name, type: f.type || 'application/octet-stream', data_url: String(reader.result || ''), kind }]);
    reader.readAsDataURL(f);
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
    if (!t2) { window.__apiToast?.(t('tavern_page.toast.no_retry_input'), { kind: 'warn', duration: 2000 }); return; }
    setHasError(false);
    setHistory((h) => {
      const out = [...h];
      while (out.length && out[out.length - 1].role === 'assistant' && !(out[out.length - 1].content || '').trim()) out.pop();
      if (out.length && out[out.length - 1].role === 'user' && (out[out.length - 1].content || '').trim() === t2) out.pop();
      return out;
    });
    startRun(t2);
  }, [running, lastPlayerText, history, startRun]);

  // 监听 MsgActions 派发的 rpg-regenerate 事件(消息气泡「重新生成」按钮)
  useEffect(() => {
    const handler = () => onRetry();
    window.addEventListener('rpg-regenerate', handler);
    return () => window.removeEventListener('rpg-regenerate', handler);
  }, [onRetry]);

  /* ── rail 操作:rename / archive / delete ───────────────────────── */
  const doRename = useCallback(async (chat, title) => {
    try {
      await window.api.tavern.rename(chat.id, title);
      window.__apiToast?.(t('tavern_page.toast.renamed'), { kind: 'ok', duration: 1500 });
      reloadList();
      if (String(chat.id) === String(activeId)) setActiveChat((p) => ({ ...(p || {}), title }));
    } catch (e) { window.__apiToast?.(t('tavern_page.toast.rename_failed'), { kind: 'danger', detail: e?.message }); }
  }, [reloadList, activeId]);

  const doArchive = useCallback(async (chat, archived) => {
    try {
      await window.api.tavern.archive(chat.id, archived);
      window.__apiToast?.(archived ? t('tavern_page.toast.archived') : t('tavern_page.toast.unarchived'), { kind: 'ok', duration: 1500 });
      reloadList();
    } catch (e) { window.__apiToast?.(t('tavern_page.toast.archive_failed'), { kind: 'danger', detail: e?.message }); }
  }, [reloadList]);

  const doDelete = useCallback(async (chat) => {
    setDeleteTarget(null);
    try {
      await window.api.tavern.remove(chat.id);
      window.__apiToast?.(t('tavern_page.toast.deleted'), { kind: 'ok', duration: 1500 });
      if (String(chat.id) === String(activeId)) {
        setActiveId(null); setActiveChat(null); setHistory([]); setCharacter(null); setPersona(null);
      }
      reloadList();
    } catch (e) { window.__apiToast?.(t('tavern_page.toast.delete_failed'), { kind: 'danger', detail: e?.message }); }
  }, [reloadList, activeId]);

  const onSavePersona = useCallback(async (payload) => {
    try {
      const saved = await window.api.cards.myUpsert(payload);
      window.__apiToast?.(t('tavern_page.toast.persona_saved'), { kind: 'ok', duration: 1500 });
      try { const d = await window.api.game.state(); applyState(d); } catch (_) {}
      return saved;
    } catch (e) {
      window.__apiToast?.(t('tavern_page.toast.save_failed'), { kind: 'danger', detail: e?.message });
      throw e;
    }
  }, [applyState]);

  /* ── 沉浸式拟人模式开关 ── */
  const onToggleImmersive = useCallback(async (enabled) => {
    if (!activeId) return;
    setImmersive(enabled); // 乐观更新
    try {
      await window.api.tavern.setImmersive(activeId, enabled);
      window.__apiToast?.(
        enabled ? t('tavern_app.drawer.immersive_on_toast') : t('tavern_app.drawer.immersive_off_toast'),
        { kind: 'ok', duration: 1500 },
      );
    } catch (e) {
      setImmersive(!enabled); // 回滚
      window.__apiToast?.(t('tavern_page.toast.save_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [activeId, t]);

  /* ── AI 帮回:以玩家自己的角色生成一条回复 → 填入输入框(不自动发送)── */
  const onAiReply = useCallback(async () => {
    if (!activeId || aiReplyLoading) return;
    setAiReplyLoading(true);
    try {
      const r = await window.api.tavern.aiReply(activeId);
      const reply = (r && r.reply) || '';
      if (!reply) {
        window.__apiToast?.(t('tavern_app.ai_reply.empty'), { kind: 'warn', duration: 2000 });
        return;
      }
      setText(reply);
    } catch (e) {
      window.__apiToast?.(t('tavern_app.ai_reply.fail'), { kind: 'danger', detail: e?.message });
    } finally {
      setAiReplyLoading(false);
    }
  }, [activeId, aiReplyLoading, t]);

  // F#3:系统提示词(本对话)= state.data.tavern.system_prompt;编辑经专用端点持久化后刷新。
  const systemPrompt = (gameState && (
    (gameState.tavern && gameState.tavern.system_prompt) ||
    (gameState.data && gameState.data.tavern && gameState.data.tavern.system_prompt)
  )) || '';
  const onSaveSystemPrompt = useCallback(async (val) => {
    if (activeId == null) return;
    try {
      await window.api.tavern.setSystemPrompt(activeId, val);
      window.__apiToast?.(t('tavern_page.toast.system_prompt_saved'), { kind: 'ok', duration: 1500 });
      try { const d = await window.api.game.state(); applyState(d); } catch (_) {}
    } catch (e) {
      window.__apiToast?.(t('tavern_page.toast.save_failed'), { kind: 'danger', detail: e?.message });
      throw e;
    }
  }, [activeId, applyState]);

  /* ── 派生 ──────────────────────────────────────────────────────── */
  const charName = (character && character.name) || (activeChat && activeChat.character_name) || t('tavern_page.char_fallback');
  const charInitial = charName.trim().slice(0, 1);
  const charAvatar = (character && character.avatar_path) || (activeChat && activeChat.avatar_path) || null;
  const personaName = (persona && persona.name) || t('tavern_page.persona_fallback');
  const exportUrl = activeId != null ? window.api.tavern.exportJsonl(activeId) : null;

  // 空起手(决策1):新建对话默认建「无角色」对话,由 agent 在对话里用 set_tavern_character
  // 自举角色;选卡/导入仍可走拖卡到侧栏 + 角色卡视图。
  const newChat = async () => {
    try {
      const r = await window.api.tavern.create({});
      if (r && r.ok === false) throw new Error(r.error || t('tavern_page.toast.new_chat_failed'));
      const sid = r && r.save && r.save.id;
      if (sid != null) { setView('chat'); await openSaveId(sid, ''); }
    } catch (e) {
      window.__apiToast?.(t('tavern_page.toast.new_chat_failed'), { kind: 'danger', detail: e?.message });
    }
  };

  // 选择角色面板:点一张卡 → 建一段绑定该角色卡的对话 → 直接进入聊天。
  const pickCharacter = async (card) => {
    if (!card || card.id == null) return;
    try {
      const r = await window.api.tavern.create({ character_card_id: card.id });
      if (r && r.ok === false) throw new Error(r.error || t('tavern_page.toast.start_chat_failed'));
      const sid = r && r.save && r.save.id;
      if (sid != null) { setView('chat'); await openSaveId(sid, card.name || ''); }
    } catch (e) {
      window.__apiToast?.(t('tavern_page.toast.start_chat_failed'), { kind: 'danger', detail: e?.message });
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
    ? `⏱ ${fmtElapsed(elapsedMs)} · ↑${_fmtK(lastUsage.input_tokens)}${lastUsage.cached_input_tokens ? `(${t('tavern_page.meta.cached')}${_fmtK(lastUsage.cached_input_tokens)})` : ''} ↓${_fmtK(lastUsage.output_tokens)}${lastUsage.cost_usd ? ` · $${Number(lastUsage.cost_usd).toFixed(4)}` : ''}`
    : null;

  /* ══════════════════════════════════════════════════════════════ */
  return (
    <div className="tvp-root tavern-chat">
      {/* GameToastStack 已上移到 PlatformShellCS 统一挂载(TavernPage 始终嵌在其中),
          此处移除以避免 game 总线双订阅 → 重复 toast。 */}

      {/* ── 左:两段式子侧栏 ──────────────────────────────────────── */}
      <aside className="tvp-side">
        {/* 上段(固定):新建 / 角色卡 / 快捷模型 */}
        <div className="tvp-side-top">
          <CSButton variant="primary" iconName="add-plus" onClick={newChat} fullWidth>
            {t('tavern_page.sidebar.new_chat')}
          </CSButton>
          <CSButton
            variant={view === 'select' ? 'normal' : 'link'}
            iconName="user-profile"
            onClick={() => setView('select')}
            fullWidth
          >
            {t('tavern_page.sidebar.select_char')}
          </CSButton>
          <CSButton
            variant={view === 'cards' ? 'normal' : 'link'}
            iconName="contact"
            onClick={() => setView('cards')}
            fullWidth
          >
            {t('tavern_page.sidebar.char_cards')}
          </CSButton>
          <CSButton
            variant="link"
            iconName="settings"
            onClick={() => setParamsOpen(true)}
            fullWidth
          >
            {t('tavern_page.sidebar.model_params')}
          </CSButton>
        </div>

        {/* 下段(滚动):历史对话 */}
        <div
          className="tvp-side-list"
          onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add('tvp-drop'); }}
          onDragLeave={(e) => e.currentTarget.classList.remove('tvp-drop')}
          onDrop={(e) => { e.preventDefault(); e.currentTarget.classList.remove('tvp-drop'); const f = e.dataTransfer?.files?.[0]; if (f) onDropCard(f); }}
        >
          <div className="tvp-list-label">{t('tavern_page.sidebar.history_label')}</div>
          {loadingList && <div className="tv-rail-empty muted-2">{t('common.loading')}</div>}
          {!loadingList && chats.length === 0 && (
            <div className="tv-rail-empty muted-2">
              <Icon name="upload" size={20} style={{ opacity: 0.5, marginBottom: 6 }} />
              <div>{t('tavern_page.sidebar.no_chats')}</div>
              <div style={{ fontSize: 11.5, marginTop: 4 }}>{t('tavern_page.sidebar.no_chats_hint')}</div>
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
                {t('tavern_page.sidebar.archived_label', { count: archivedChats.length })}
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
              <h1 className="tvp-hero-title serif">{t('tavern_page.hero.heading')}</h1>
              <p className="tvp-hero-sub muted">{t('tavern_page.hero.sub')}</p>
              <div className="tvp-hero-actions">
                <CSButton variant="primary" iconName="add-plus" onClick={newChat}>{t('tavern_page.sidebar.new_chat')}</CSButton>
                <CSButton iconName="user-profile" onClick={() => setView('select')}>{t('tavern_page.sidebar.select_char')}</CSButton>
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
                <div className="tvp-hero-drop-sub muted-2">{t('tavern_page.hero.drop_hint')}</div>
              </div>
            </div>
          </div>
        ) : (
          <>
            <header className="tvp-chat-head">
              <button className="tvp-chat-title" onClick={() => setDrawerOpen(true)} data-tip={t('tavern_page.header.char_persona_tip')}>
                <span className="tvp-chat-name">{charName}</span>
                <Icon name="chevron_down" size={12} style={{ opacity: 0.5 }} />
              </button>
              <div className="tvp-chat-head-actions">
                {running && (
                  <span className="tvp-timer" aria-live="polite" data-tip={t('tavern_page.header.elapsed_tip')}>
                    <span className="tvp-timer-dot" />
                    {fmtElapsed(elapsedMs)}
                  </span>
                )}
                {exportUrl && (
                  <button className="iconbtn" onClick={() => setExportTarget(activeChat || { id: activeId })} data-tip={t('tavern_page.header.export_tip')}>
                    <Icon name="download" size={15} />
                  </button>
                )}
                <button className="iconbtn" onClick={() => setDrawerOpen(true)} data-tip={t('tavern_page.header.char_persona_tip')}>
                  <Icon name="cards" size={15} />
                </button>
              </div>
            </header>

            <TavernChatArea
              history={history} running={running} saveId={activeId}
              charName={charName} charInitial={charInitial} charAvatar={charAvatar} personaName={personaName}
              hasError={hasError} onRetry={onRetry}
              lastMeta={lastMeta} elapsedLabel={running ? fmtElapsed(elapsedMs) : null}
            />

            <div className="gc-foot-wrap tvp-foot">
              {/* F#2:agent 调 ask_player_choice → pending_questions → 复用 ConfirmStrip 渲染可点选择题。
                  玩家点选 → onChoiceAnswer 把选择作为下一条消息发回(复用现有模组,非新造 UI)。 */}
              {pendingQuestions.length > 0 && (
                <ConfirmStrip
                  pendingQuestions={pendingQuestions}
                  onAnswer={onChoiceAnswer}
                  onDismiss={onChoiceDismiss}
                  onConfigDefault={onConfigDefault}
                  onConfigContinue={onConfigContinue}
                  onHardConfig={onHardConfig}
                  onConfigSettings={onConfigSettings}
                />
              )}
              {/* Item 3:发消息时缺 LLM key → Composer 上方内联引导卡片(复用 AgentModelPicker bare,LLM 不过滤),
                  配好后「重试」复用 onRetry 重跑上一条输入。 */}
              {needsCreds && (
                <div className="gc-confirm gc-confirm-config tvp-creds-card">
                  <div className="gc-confirm-marker"><Icon name="warn" size={12} /></div>
                  <div className="gc-confirm-body">
                    <div className="gc-confirm-row1">
                      <span className="gc-confirm-tag">{t('tavern_page.creds_card.tag')}</span>
                      <span className="gc-confirm-text serif">{t('tavern_page.creds_card.body')}</span>
                    </div>
                    <div className="gc-config-inline">
                      <AgentModelPicker
                        prefPrefix="gm"
                        variant="bare"
                        configHash="settings-models"
                      />
                      <div className="gc-confirm-actions">
                        <button className="gc-chip-btn gc-chip-primary" onClick={() => { setNeedsCreds(false); onRetry(); }}>
                          {t('tavern_page.creds_card.retry_btn')}
                        </button>
                        <button className="gc-chip-btn" onClick={() => { try { window.location.hash = 'settings-models'; } catch (_) {} }}>
                          <Icon name="settings" size={11} /> {t('tavern_page.creds_card.settings_btn')}
                        </button>
                      </div>
                    </div>
                  </div>
                  <button className="iconbtn" onClick={() => setNeedsCreds(false)} title={t('tavern_page.creds_card.dismiss_tip')}><Icon name="close" size={11} /></button>
                </div>
              )}
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
                onAiReply={onAiReply}
                aiReplyOnly
              />
              {/* 计时器/token/费用 不再浮在页脚:生成中显示在「正在思考」气泡,完成后并入最新消息操作栏。 */}
            </div>
          </>
        )}
      </main>

      {/* ── 弹窗 / 抽屉 ──────────────────────────────────────────── */}
      {/* F#1:真实文件上传隐藏输入(onAttachPick 触发)。 */}
      <input ref={fileInputRef} type="file" style={{ display: 'none' }} onChange={onFilePicked} />
      <TavernImportModal open={importOpen} onClose={() => setImportOpen(false)} onConfirm={onImportConfirm} />
      {/* config_card hard 拦截弹窗(mode model_not_configured) */}
      <ModelConfigInterceptModal open={!!hardConfigItem} item={hardConfigItem} onResolve={onHardResolve} onCancel={onHardCancel} />

      <TwoCardDrawer
        inline
        open={drawerOpen} character={character} persona={persona}
        systemPrompt={systemPrompt}
        onClose={() => setDrawerOpen(false)}
        onSavePersona={onSavePersona}
        onSaveSystemPrompt={onSaveSystemPrompt}
        immersive={immersive}
        onToggleImmersive={onToggleImmersive}
      />

      {/* 采样参数(模型参数)抽屉 —— 复用 settings 的 ModelParamsSection,写同一份偏好,影响所有调用 */}
      {paramsOpen && (
        <div className="tv-drawer-backdrop" onClick={() => setParamsOpen(false)}>
          <div className="tv-drawer" onClick={(e) => e.stopPropagation()}>
            <header className="tv-drawer-head">
              <strong style={{ fontSize: 14 }}>{t('tavern_page.params_drawer.title')}</strong>
              <button className="iconbtn" onClick={() => setParamsOpen(false)} data-tip={t('common.close')}>
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
        title={t('tavern_page.delete_modal.title')}
        body={<>{t('tavern_page.delete_modal.body_prefix')}<strong>{deleteTarget?.title || deleteTarget?.character_name || ''}</strong>{t('tavern_page.delete_modal.body_suffix')}</>}
        confirmLabel={t('common.delete')}
        danger
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && doDelete(deleteTarget)}
      />

      <ConfirmModal
        open={!!exportTarget}
        title={t('tavern_page.export_modal.title')}
        body={<>{t('tavern_page.export_modal.body_prefix')}<strong>{exportTarget?.title || exportTarget?.character_name || charName}</strong>{t('tavern_page.export_modal.body_suffix')}</>}
        confirmLabel={t('tavern_page.export_modal.confirm_btn')}
        onClose={() => setExportTarget(null)}
        onConfirm={() => { const u = exportUrl; setExportTarget(null); if (u) window.open(u, '_blank', 'noopener'); }}
      />
    </div>
  );
}
