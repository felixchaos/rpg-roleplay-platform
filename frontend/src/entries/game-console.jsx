// Game Console 页面入口 — Vite ESM 版
import '../web-vitals-rum.js';
import React from 'react';
import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import * as ReactDOM from 'react-dom/client';

// 基础设施 side-effect 模块
import '../mock-data.js';
import '../api-client.js';
import '../data-loader.js';
import '../state-event-bridge.js';
import '../worldbook-status-toast.js';
import '../ui-atlas.js';
import '../console-assistant-navigation.jsx';

// 组件模块 — named import
import { useResizable } from '../responsive.jsx';
import { LeftRail, TopBar, ChatArea, HistoryDrawer, SearchDrawer, GameToastStack, RunSteps, GameSettingsModal } from '../game-app.jsx';
import { Composer, ConfirmStrip } from '../game-composer.jsx';
import { RightPanel, PANEL_TABS } from '../game-panels.jsx';
import { ConsoleAssistantPanel } from '../console-assistant-panel.jsx';
import ModelPicker from '../components/ModelPicker.jsx';

// density preset + narrative font init（等价原 HTML 非 babel inline script）
(function () {
  const VALID_DENSITY = { compact: 1, default: 1, spacious: 1 };
  function _applyDensity(d) {
    if (!VALID_DENSITY[d]) d = 'default';
    document.documentElement.setAttribute('data-density', d);
    try { localStorage.setItem('rpg.density', d); } catch (_) {}
    window.dispatchEvent(new CustomEvent('rpg-density-change', { detail: d }));
  }
  let storedDensity = 'default';
  try { storedDensity = localStorage.getItem('rpg.density') || 'default'; } catch (_) {}
  _applyDensity(storedDensity);
  window.RPG_setDensity = _applyDensity;

  const FONT_MAP = { serif: 'var(--font-serif)', sans: 'var(--font-sans)', mono: 'var(--font-mono)' };
  let storedFont = 'serif';
  try { storedFont = localStorage.getItem('rpg.narrativeFont') || 'serif'; } catch (_) {}
  if (FONT_MAP[storedFont]) {
    document.documentElement.style.setProperty('--narrative-font', FONT_MAP[storedFont]);
  }
})();

// ---- App ----

const TWEAK_DEFAULTS = {
  composerMode: 'compact',
  runStyle: 'line',
  defaultRightTab: 'status',
  rightPanelWidth: 320,
  narrativeFont: 'serif',
  monoFont: 'jetbrains',
  uiSize: 13,
  narrativeSize: 15,
  density: 'normal',
  showRail: true,
};

const PUBLIC_STAGES = {
  context: { id: 'context', label: '正在整理剧情上下文', order: 1 },
  rules:   { id: 'rules',   label: '正在检查规则与权限', order: 2 },
  gm:      { id: 'gm',      label: '正在生成 GM 回复',   order: 3 },
  save:    { id: 'save',    label: '正在保存本轮结果',   order: 4 },
  system:  { id: 'system',  label: '正在处理本轮行动',   order: 0 },
};
function mapAgentPhase(phase) {
  if (!phase) return null;
  if (
    phase === 'prompt' || phase === 'intent' || phase === 'llm_curator' ||
    phase === 'manifest' || phase === 'assembly' || phase === 'context_retrieve' ||
    phase === 'context_agent' || phase === 'world_check' || phase === 'prompt_assemble' ||
    phase === 'aborted' || (typeof phase === 'string' && phase.startsWith('provider:'))
  ) return PUBLIC_STAGES.context;
  if (phase === 'rules_engine' || phase === 'acceptance_check') return PUBLIC_STAGES.rules;
  if (phase === 'main_gm') return PUBLIC_STAGES.gm;
  return PUBLIC_STAGES.system;
}
function advancePublicStage(prevId, nextStage) {
  if (!nextStage) return prevId;
  const prev = (prevId && PUBLIC_STAGES[prevId]) || null;
  if (!prev) return nextStage.id;
  return nextStage.order >= prev.order ? nextStage.id : prev.id;
}

const STREAM_CHUNKS = [
  '你转过身去看沈知微，雾灯把她的侧脸照得发青。她没再追问残页，反而把腰上的铜针袋解下来，递到你手里。',
  '\n\n『先收着。』她说，『北港有人来了——是从南陵跟过来的。』',
  '\n\n海雾忽然又厚一层。你借着雾色看向北港，看见三个穿青衣的人正在台阶下停步。其中走在最前的一个，腰间挂着南陵巡检的腰牌——是韩司直。',
  '\n\n他抬头朝你的方向看了一眼，又像是没看见，绕过石阶往灯塔的方向去了。',
  '\n\n沈知微低声道：『他在等天黑。等天黑你就走不掉了。』',
];

function App() {
  // 旧 useTweaks/setTweak 用法迁出(tweaks-panel.jsx 已删,只是设计原型工具);
  // 这里仅消费默认值,改成普通常量即可。
  const t = TWEAK_DEFAULTS;
  const openTweaks = () => window.postMessage({ type: '__activate_edit_mode' }, '*');

  const IS_ANON = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  const EMPTY_STATE = {
    player: { name: '', role: '', background: '', current_location: '' },
    world: { time: '', weather: '', known_events: [], timeline: {} },
    relationships: {},
    memory: {},
    worldline: {},
    ruleset: {},
    player_character: {},
    scene: {},
    encounter: {},
    dice_log: [],
    permissions: { mode: 'full_access', pending_writes: [], pending_questions: [] },
    suggestions: [],
    turn: 0,
    history: [],
  };
  const INITIAL_STATE = IS_ANON && window.MOCK_STATE ? structuredClone(window.MOCK_STATE) : structuredClone(EMPTY_STATE);
  const [game, setGame] = useState(INITIAL_STATE);
  const [history, setHistory] = useState(INITIAL_STATE.history || []);
  const [text, setText] = useState('');
  const [attachments, setAttachments] = useState([]);
  const [model, setModel] = useState(null);
  const [permission, setPermission] = useState(
    (INITIAL_STATE.permissions && INITIAL_STATE.permissions.mode) || 'full_access'
  );
  const getRightTabForLocation = (fallback) => {
    const hash = String(location.hash || '').replace(/^#/, '');
    const tabs = PANEL_TABS || [];
    return tabs.some((tab) => tab.id === hash) ? hash : fallback;
  };
  const [activeTab, setActiveTab] = useState(() => getRightTabForLocation(t.defaultRightTab || 'status'));
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [showSlash, setShowSlash] = useState(false);
  const [showPlus, setShowPlus] = useState(false);
  const [showModel, setShowModel] = useState(false);
  const [showPerm, setShowPerm] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [showHistoryDrawer, setShowHistoryDrawer] = useState(false);
  const [showSearchDrawer, setShowSearchDrawer] = useState(false);
  const [showInGameSettings, setShowInGameSettings] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const _railResize = useResizable({
    storageKey: 'gc.rail.w', defaultSize: 240, min: 180, max: 360, side: 'left',
    cssVar: '--gc-rail-w',
  });
  const gcRailW = _railResize.size;
  const gcRailDragProps = _railResize.dragHandleProps;
  const _panelResize = useResizable({
    storageKey: 'gc.panel.w', defaultSize: 320, min: 180, max: 520, side: 'right',
  });
  const gcPanelW = _panelResize.size;
  const gcPanelDragProps = _panelResize.dragHandleProps;

  useEffect(() => {
    if (gcPanelW < 180 && !panelCollapsed) setPanelCollapsed(true);
    else if (gcPanelW >= 180 && panelCollapsed && gcPanelW !== 320) setPanelCollapsed(false);
  }, [gcPanelW]);
  useEffect(() => {
    const bus = window.__capBus || (window.__capBus = new EventTarget());
    const onOpen = () => setAssistantOpen(true);
    const onClose = () => setAssistantOpen(false);
    const onToggle = () => setAssistantOpen((v) => !v);
    bus.addEventListener('cap-open', onOpen);
    bus.addEventListener('cap-close', onClose);
    bus.addEventListener('cap-toggle', onToggle);
    return () => {
      bus.removeEventListener('cap-open', onOpen);
      bus.removeEventListener('cap-close', onClose);
      bus.removeEventListener('cap-toggle', onToggle);
    };
  }, []);

  const [pickedCommand, setPickedCommand] = useState(null);
  const [lastPlayerText, setLastPlayerText] = useState('');
  const [sseLog, setSseLog] = useState([]);
  const [sseLogOpen, setSseLogOpen] = useState(false);

  const [runState, setRunState] = useState({
    running: false, publicStage: null, label: '', detail: '',
    totalElapsed: 0, completedAt: 0, completedElapsed: 0, rawSteps: [],
  });
  const runRef = useRef({ timers: [], stopped: false, sse: null, doneTimer: null });

  const [pendingWrites, setPendingWrites] = useState(
    (INITIAL_STATE.permissions && INITIAL_STATE.permissions.pending_writes) || []
  );
  const [pendingQuestions, setPendingQuestions] = useState(
    (INITIAL_STATE.permissions && INITIAL_STATE.permissions.pending_questions) || []
  );
  const [realSaves, setRealSaves] = useState([]);
  const [activeSave, setActiveSave] = useState(null);

  const PICK_STATE_KEYS = [
    'player','world','relationships','memory','worldline','permissions','suggestions','turn',
    'ruleset','player_character','scene','encounter','dice_log','content_pack',
    'active_entities','app','models',
  ];
  const RESETTABLE_KEYS = new Set(['suggestions']);

  const reloadState = useCallback(async () => {
    try {
      const data = await window.api.game.state();
      if (data && data.player) {
        setGame((g) => {
          const next = { ...g };
          for (const k of PICK_STATE_KEYS) {
            if (data[k] !== undefined) next[k] = data[k];
            else if (RESETTABLE_KEYS.has(k)) {
              next[k] = Array.isArray(g[k]) ? [] : (typeof g[k] === 'object' ? {} : null);
            }
          }
          next._raw = { save_id: data.save_id ?? null, save_title: data.save_title ?? null, turn: data.turn ?? null };
          return next;
        });
        if (Array.isArray(data.history)) setHistory(data.history);
        if (data.permissions) {
          setPermission(data.permissions.mode || 'full_access');
          setPendingWrites(data.permissions.pending_writes || []);
          setPendingQuestions(data.permissions.pending_questions || []);
        }
        try {
          const isFresh = (
            (!Array.isArray(data.history) || data.history.length === 0) &&
            (data.turn === 0 || data.turn == null) && data.save_id != null
          );
          const seenKey = 'gc.opened_save.' + data.save_id;
          const alreadyOpened = sessionStorage.getItem(seenKey);
          if (isFresh && !alreadyOpened) {
            sessionStorage.setItem(seenKey, '1');
            setTimeout(() => {
              try {
                const sse = window.api && window.api.raw && window.api.raw.sseStream;
                if (!sse) return;
                let openingText = '';
                setHistory((h) => {
                  const arr = Array.isArray(h) ? [...h] : [];
                  arr.push({ role: 'assistant', content: '正在为你拉开剧本帷幕…', _opening: true, _thinking: 'starting' });
                  return arr;
                });
                sse('/api/v1/opening', {}, {
                  on_stage: (d) => {
                    const label = (d && d.label) || '';
                    const phase = (d && d.phase) || '';
                    if (!label && phase !== 'done') return;
                    setHistory((h) => {
                      const arr = Array.isArray(h) ? [...h] : [];
                      if (arr.length && arr[arr.length - 1]._opening && !openingText) {
                        arr[arr.length - 1] = { ...arr[arr.length - 1], content: label || arr[arr.length - 1].content, _thinking: phase };
                      }
                      return arr;
                    });
                  },
                  on_token: (d) => {
                    const tok = (d && d.text) || '';
                    if (tok) {
                      openingText += tok;
                      setHistory((h) => {
                        const arr = Array.isArray(h) ? [...h] : [];
                        if (arr.length && arr[arr.length - 1].role === 'assistant' && arr[arr.length - 1]._opening) {
                          arr[arr.length - 1] = { ...arr[arr.length - 1], content: openingText, _thinking: null };
                        } else {
                          arr.push({ role: 'assistant', content: openingText, _opening: true });
                        }
                        return arr;
                      });
                    }
                  },
                  on_done: () => {
                    setTimeout(async () => {
                      try {
                        const d2 = await window.api.game.state();
                        if (d2 && d2.player) {
                          setGame((g) => {
                            const next = { ...g };
                            for (const k of PICK_STATE_KEYS) {
                              if (k === 'suggestions') { if (d2[k] !== undefined) next[k] = d2[k]; }
                              else if (d2[k] !== undefined) next[k] = d2[k];
                            }
                            return next;
                          });
                        }
                      } catch (_) {}
                    }, 300);
                  },
                  on_error: () => {
                    setHistory((h) => {
                      const arr = Array.isArray(h) ? [...h] : [];
                      if (arr.length && arr[arr.length - 1]._opening && arr[arr.length - 1]._thinking) arr.pop();
                      return arr;
                    });
                  },
                });
              } catch (e) { console.warn('[opening] trigger error', e); }
            }, 800);
          }
        } catch (_) {}
      }
      if (data && data.save_id != null) {
        setActiveSave({ id: data.save_id, title: data.save_title || `存档 #${data.save_id}`, updated_at: data.save_updated_at || '' });
      }
      // 返回是否真正拿到了可玩状态(供 mount 重试判断是否还要再拉一次)。
      return !!(data && data.player);
    } catch (_) { return false; }
  }, []);

  const reloadSaves = useCallback(async () => {
    try {
      const r = await window.api.saves.list();
      const list = Array.isArray(r) ? r : (r?.items || r?.saves || []);
      const norm = list.map(window.__normalizeSave || ((x) => x));
      setRealSaves(norm);
      setActiveSave((prev) => {
        if (prev && norm.some((s) => s.id === prev.id)) return prev;
        const cur = norm.find((s) => s.current) || norm[0];
        return cur ? { id: cur.id, title: cur.title, updated_at: cur.updated_at || '' } : null;
      });
    } catch (_) { setRealSaves([]); }
  }, []);

  useEffect(() => {
    let cancelled = false;
    // 后端 per-user 运行时状态在页面首次加载/导航后可能尚未热(_ensure_loaded 冷启动),
    // 首次 /api/state 可能返回空(无 player/save_id),导致首屏停在 INITIAL_STATE
    // ("尚未创建存档")。带界限重试直到拿到可玩状态;对 100 并发用户首进游戏的
    // 冷缓存同样有韧性。拿到即停,不过度轮询。
    (async () => {
      for (let i = 0; i < 6 && !cancelled; i++) {
        const ok = await reloadState();
        await reloadSaves();
        if (ok || cancelled) break;
        await new Promise((r) => setTimeout(r, 400));
      }
    })();
    return () => { cancelled = true; };
  }, [reloadState, reloadSaves]);

  useEffect(() => {
    const onReload = () => { reloadState(); reloadSaves(); };
    window.addEventListener('rpg-state-reload', onReload);
    window.addEventListener('game-state-refresh', onReload);
    return () => {
      window.removeEventListener('rpg-state-reload', onReload);
      window.removeEventListener('game-state-refresh', onReload);
    };
  }, [reloadState, reloadSaves]);

  useEffect(() => { setActiveTab(getRightTabForLocation(t.defaultRightTab || 'status')); }, [t.defaultRightTab]);
  useEffect(() => {
    const onHashChange = () => setActiveTab(getRightTabForLocation(t.defaultRightTab || 'status'));
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [t.defaultRightTab]);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') { setShowSlash(false); setShowPlus(false); setShowModel(false); setShowPerm(false); }
      if (e.key === '/' && document.activeElement === document.body) { e.preventDefault(); setShowSlash(true); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    if (pickedCommand) return;
    if (text.startsWith('/') && !showSlash) setShowSlash(true);
    if (!text.startsWith('/') && showSlash && text !== '') setShowSlash(false);
  }, [text, pickedCommand]);

  const stopRun = useCallback(() => {
    runRef.current.stopped = true;
    runRef.current.timers.forEach(clearTimeout);
    runRef.current.timers.forEach((t) => { try { clearInterval(t); } catch (_) {} });
    runRef.current.timers = [];
    if (runRef.current.doneTimer) { clearTimeout(runRef.current.doneTimer); runRef.current.doneTimer = null; }
    if (runRef.current.sse) { try { runRef.current.sse.stop(); } catch (_) {} runRef.current.sse = null; }
    try { window.api.game.stop(); } catch (_) {}
    setRunState((r) => ({ ...r, running: false, label: '已停止', detail: '', publicStage: null, completedAt: 0, completedElapsed: r.totalElapsed }));
  }, []);

  const startRunReal = useCallback(async (playerText) => {
    const ts = new Date().toLocaleTimeString().slice(0, 5);
    const sentAttachments = attachments;
    setHistory((h) => [...h, { role: 'user', content: playerText, ts, attachments: sentAttachments }]);
    setLastPlayerText(playerText);
    setSseLog([{ t: Date.now(), kind: 'send', payload: { message: playerText, model: model && model.id } }]);
    setText(''); setAttachments([]);
    setShowSlash(false); setShowPlus(false);
    setPendingQuestions((arr) => (arr || []).filter((q) => {
      const src = String(q && q.source || '');
      const systemPrefix = ['gm', 'rules_engine', 'curator', 'extractor', 'set_parser'];
      return !systemPrefix.some((s) => src === s || src.startsWith(s + ':'));
    }));
    const startedAt = Date.now();
    setRunState({ running: true, publicStage: 'context', label: PUBLIC_STAGES.context.label, detail: '', totalElapsed: 0, completedAt: 0, completedElapsed: 0, rawSteps: [] });
    if (runRef.current.doneTimer) { clearTimeout(runRef.current.doneTimer); runRef.current.doneTimer = null; }
    runRef.current.stopped = false;
    const logEvent = (kind, payload) => setSseLog((l) => (l.length >= 500 ? l : [...l, { t: Date.now(), kind, payload }]));
    const tickerId = setInterval(() => {
      if (runRef.current.stopped) { clearInterval(tickerId); return; }
      setRunState((r) => ({ ...r, totalElapsed: Date.now() - startedAt }));
    }, 200);
    runRef.current.timers.push(tickerId);
    const STREAM_IDLE_TIMEOUT_MS = 30000;
    const resetInactivityTimer = () => {
      if (runRef.current.inactivityTimer) clearTimeout(runRef.current.inactivityTimer);
      runRef.current.inactivityTimer = setTimeout(() => {
        try { runRef.current.sse && runRef.current.sse.stop && runRef.current.sse.stop(); } catch (_) {}
        setRunState((r) => {
          if (!r.running) return r;
          setHasError('超过 30 秒没有新输出, 已主动断开。可能是模型卡死或网络慢, 请重试。');
          window.__apiToast?.('生成停滞', { kind: 'warn', detail: '30 秒无响应, 已中断', duration: 4000 });
          return { ...r, running: false, label: '超时', detail: '30 秒无响应', publicStage: null, completedAt: 0 };
        });
      }, STREAM_IDLE_TIMEOUT_MS);
    };
    resetInactivityTimer();
    let openedAssistant = false;
    runRef.current.sse = await window.api.game.chat(
      { message: playerText, text: playerText, attachments: sentAttachments, model, command: pickedCommand?.id || null },
      {
        on_status: (data) => {
          logEvent('status', data);
          if (data && data.player) setGame((g) => {
            const n = { ...g };
            for (const k of PICK_STATE_KEYS) if (data[k] !== undefined) n[k] = data[k];
            n._raw = { save_id: data.save_id ?? n._raw?.save_id, save_title: data.save_title ?? n._raw?.save_title, turn: data.turn ?? n._raw?.turn };
            return n;
          });
          if (data && data.save_id != null && (!activeSave || activeSave.id !== data.save_id)) {
            setActiveSave({ id: data.save_id, title: data.save_title || `存档 #${data.save_id}`, updated_at: data.save_updated_at || '' });
          }
        },
        on_token: (data) => {
          resetInactivityTimer();
          logEvent('token', { len: ((data && (data.text || data.delta)) || '').length });
          const piece = (data && (data.text || data.delta)) || '';
          if (!piece) return;
          setHistory((h) => {
            if (!openedAssistant) { openedAssistant = true; return [...h, { role: 'assistant', content: piece, ts, streaming: true }]; }
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant') return [...h, { role: 'assistant', content: piece, ts, streaming: true }];
            return [...h.slice(0, -1), { ...last, content: (last.content || '') + piece }];
          });
        },
        on_agent: (data) => {
          resetInactivityTimer();
          logEvent('agent', data);
          if (!data || !data.phase) return;
          const mapped = mapAgentPhase(data.phase);
          setRunState((r) => {
            const rawSteps = Array.isArray(r.rawSteps) ? r.rawSteps.slice() : [];
            const idx = rawSteps.findIndex((s) => s.phase === data.phase);
            const merged = { phase: data.phase, message: data.message || (idx >= 0 ? rawSteps[idx].message : data.phase), status: data.status || 'running', elapsed_ms: data.elapsed_ms ?? (idx >= 0 ? rawSteps[idx].elapsed_ms : 0), detail: data.detail || (idx >= 0 ? rawSteps[idx].detail : undefined) };
            if (idx >= 0) rawSteps[idx] = { ...rawSteps[idx], ...merged }; else rawSteps.push(merged);
            const nextStageId = advancePublicStage(r.publicStage, mapped);
            const nextLabel = (nextStageId && PUBLIC_STAGES[nextStageId]) ? PUBLIC_STAGES[nextStageId].label : r.label;
            if (data.status === 'stopped') return { ...r, rawSteps, publicStage: null, label: '已停止', detail: '' };
            return { ...r, rawSteps, publicStage: nextStageId, label: nextLabel, detail: '' };
          });
        },
        on_updates: (data) => {
          logEvent('updates', data);
          const stage = data && data.stage;
          if (stage === 'pre_llm' || stage === 'rules_engine') return;
          setRunState((r) => {
            const nextStageId = advancePublicStage(r.publicStage, PUBLIC_STAGES.save);
            return { ...r, publicStage: nextStageId, label: PUBLIC_STAGES[nextStageId].label, detail: '' };
          });
        },
        on_done: (data) => {
          if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
          logEvent('done', { status: !!data && data.status ? 'ok' : 'noop', interrupted: data && data.interrupted, usage: data && data.usage });
          clearInterval(tickerId);
          const stripOps = (txt) => {
            if (!txt) return txt;
            // Robust: find JSON arrays containing "op": and remove them.
            // Strategy: locate `[` followed by `"op"` within 80 chars, then find matching `]`.
            let out = txt;
            // 1. fenced code blocks wrapping ops
            out = out.replace(/```(?:json)?\s*\[[\s\S]*?"op"\s*:[\s\S]*?\]\s*```/gi, '');
            out = out.replace(/```(?:json)?\s*\{[\s\S]*?"op"\s*:[\s\S]*?\}\s*```/gi, '');
            // 2. Bare JSON ops array: find `[` + within 80 chars `"op":` + greedy to last `]`
            // Use a function-based replace to find the matching bracket
            let idx;
            while ((idx = out.search(/\[\s*\{[^[\]]{0,80}"op"\s*:/)) !== -1) {
              // Find matching ] by counting brackets
              let depth = 0, end = -1;
              for (let i = idx; i < out.length; i++) {
                if (out[i] === '[') depth++;
                else if (out[i] === ']') { depth--; if (depth === 0) { end = i; break; } }
              }
              if (end === -1) break; // malformed, stop
              // Remove including leading newlines
              let start = idx;
              while (start > 0 && out[start - 1] === '\n') start--;
              out = out.slice(0, start) + out.slice(end + 1);
            }
            return out.trimEnd();
          };
          setHistory((h) => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant') return h;
            const cleaned = stripOps(last.content || '');
            return [...h.slice(0, -1), { ...last, content: cleaned, streaming: false, streaming_done: true }];
          });
          setRunState((r) => ({ ...r, running: false, label: '本轮完成', detail: '', completedAt: Date.now(), completedElapsed: r.totalElapsed }));
          if (runRef.current.doneTimer) clearTimeout(runRef.current.doneTimer);
          runRef.current.doneTimer = setTimeout(() => {
            runRef.current.doneTimer = null;
            setRunState((r) => (r.running ? r : { ...r, publicStage: null, completedAt: 0, label: '' }));
          }, 1800);
          const payload = (data && data.status) || null;
          if (payload && payload.player) setGame((g) => {
            const n = { ...g };
            for (const k of PICK_STATE_KEYS) if (payload[k] !== undefined) n[k] = payload[k];
            n._raw = { save_id: payload.save_id ?? n._raw?.save_id, save_title: payload.save_title ?? n._raw?.save_title, turn: payload.turn ?? n._raw?.turn };
            return n;
          });
          if (payload && Array.isArray(payload.history)) setHistory(payload.history);
          if (payload && payload.permissions) {
            setPermission(payload.permissions.mode || 'full_access');
            setPendingWrites(payload.permissions.pending_writes || []);
            setPendingQuestions(payload.permissions.pending_questions || []);
          }
          if (payload && payload.save_id != null && (!activeSave || activeSave.id !== payload.save_id)) {
            setActiveSave({ id: payload.save_id, title: payload.save_title || `存档 #${payload.save_id}`, updated_at: payload.save_updated_at || '' });
          }
          runRef.current.sse = null;
          setPickedCommand(null);
        },
        on_error: (data) => {
          logEvent('error', data);
          clearInterval(tickerId);
          if (runRef.current.doneTimer) { clearTimeout(runRef.current.doneTimer); runRef.current.doneTimer = null; }
          if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
          const realMsg = (data && (data.message || data.detail || data.error)) || '';
          setRunState((r) => ({ ...r, running: false, label: '生成失败', detail: realMsg, publicStage: null, completedAt: 0 }));
          setHasError(realMsg || true);
          window.__apiToast?.('生成失败', { kind: 'danger', detail: realMsg || '请重试' });
        },
        onClose: () => {
          clearInterval(tickerId);
          if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
          setRunState((r) => {
            if (!r.running) return r;
            setHasError('流式输出意外中断,可能是模型 safety filter 或网络问题。请重试。');
            window.__apiToast?.('生成中断', { kind: 'warn', detail: '流式连接关闭但没收到完成事件,可能是模型 safety filter 截断', duration: 4000 });
            return { ...r, running: false, label: '中断', detail: '连接关闭但未收到完成事件', publicStage: null, completedAt: 0 };
          });
          setHistory((h) => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant' || !last.streaming) return h;
            return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
          });
        },
      }
    );
  }, [attachments, model, pickedCommand]);

  const startRun = useCallback((playerText) => {
    if (window.api && window.api.base !== undefined) return startRunReal(playerText);
    const ts = ['申时三刻', '酉时初', '酉时一刻', '酉时二刻'][history.length % 4];
    setHistory((h) => [...h, { role: 'user', content: playerText, ts, attachments }]);
    setText(''); setAttachments([]); setShowSlash(false); setShowPlus(false);
    runRef.current.stopped = false; runRef.current.timers = [];
    if (runRef.current.doneTimer) { clearTimeout(runRef.current.doneTimer); runRef.current.doneTimer = null; }
    const startedAt = Date.now();
    const MOCK_PHASES = [
      { phase: 'prompt',       message: '加载上下文子代理运行提示（模式：local_fallback）。', duration: 220 },
      { phase: 'intent',       message: '未发现显式时间跳跃；沿用当前锁定时间线。', duration: 180 },
      { phase: 'manifest',     message: '已解析 ContentPack：novel · woaileni', duration: 260 },
      { phase: 'provider:novel_retrieval', message: 'novel_retrieval 贡献 4 层、6 条事实', duration: 620 },
      { phase: 'assembly',     message: '已生成主 GM 上下文清单。', duration: 200 },
      { phase: 'rules_engine', message: 'RulesEngine 已完成本轮规则裁定。', duration: 340 },
      { phase: 'main_gm',      message: '主 GM 正在读取上下文并生成正文。', duration: 2200 },
    ];
    setRunState({ running: true, publicStage: 'context', label: PUBLIC_STAGES.context.label, detail: '', totalElapsed: 0, completedAt: 0, completedElapsed: 0, rawSteps: [] });
    const tickerId = setInterval(() => {
      if (runRef.current.stopped) { clearInterval(tickerId); return; }
      setRunState((r) => ({ ...r, totalElapsed: Date.now() - startedAt }));
    }, 200);
    runRef.current.timers.push(tickerId);
    const runStep = (i) => {
      if (runRef.current.stopped) return;
      if (i >= MOCK_PHASES.length) {
        clearInterval(tickerId);
        setRunState((r) => ({ ...r, running: false, label: '本轮完成', detail: '', completedAt: Date.now(), completedElapsed: Date.now() - startedAt }));
        if (runRef.current.doneTimer) clearTimeout(runRef.current.doneTimer);
        runRef.current.doneTimer = setTimeout(() => { runRef.current.doneTimer = null; setRunState((r) => (r.running ? r : { ...r, publicStage: null, completedAt: 0, label: '' })); }, 1800);
        setPendingWrites((arr) => arr.some((w) => w.id === 'pw-3') ? arr : [...arr, { id: 'pw-3', field: 'memory.facts', from: null, to: '韩司直已抵达北港', risk: 'low', reason: 'GM 提议加入事实库（低风险）' }]);
        return;
      }
      const step = MOCK_PHASES[i];
      const mapped = mapAgentPhase(step.phase);
      setRunState((r) => {
        const rawSteps = [...r.rawSteps, { phase: step.phase, message: step.message, status: 'running', elapsed_ms: 0 }];
        const nextStageId = advancePublicStage(r.publicStage, mapped);
        return { ...r, rawSteps, publicStage: nextStageId, label: PUBLIC_STAGES[nextStageId].label, detail: '' };
      });
      if (step.phase === 'main_gm') {
        setHistory((h) => [...h, { role: 'assistant', content: '', ts, streaming: true }]);
        let chunkIdx = 0;
        const chunkInterval = setInterval(() => {
          if (runRef.current.stopped) { clearInterval(chunkInterval); return; }
          if (chunkIdx >= STREAM_CHUNKS.length) { clearInterval(chunkInterval); return; }
          const piece = STREAM_CHUNKS[chunkIdx++];
          setHistory((h) => { const last = h[h.length - 1]; if (!last || last.role !== 'assistant') return h; return [...h.slice(0, -1), { ...last, content: (last.content || '') + piece }]; });
        }, step.duration / (STREAM_CHUNKS.length + 1));
        runRef.current.timers.push(chunkInterval);
      }
      const timerB = setTimeout(() => {
        if (runRef.current.stopped) return;
        setRunState((r) => { const rawSteps = r.rawSteps.slice(); const idx = rawSteps.findIndex((s) => s.phase === step.phase); if (idx >= 0) rawSteps[idx] = { ...rawSteps[idx], status: 'done', elapsed_ms: step.duration }; return { ...r, rawSteps }; });
        if (step.phase === 'main_gm') { setHistory((h) => { const last = h[h.length - 1]; if (!last || last.role !== 'assistant') return h; return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }]; }); }
        runStep(i + 1);
      }, step.duration);
      runRef.current.timers.push(timerB);
    };
    runStep(0);
  }, [history.length, attachments]);

  const onSend = () => {
    if (!text.trim() && !attachments.length) return;
    if (runState.running) return;
    setHasError(false);
    startRun(text.trim() || '（仅附件，请基于本轮上下文推进。）');
  };
  const onSendRaw = useCallback((raw) => {
    if (runState.running) return;
    const t2 = (raw || '').trim();
    if (!t2) return;
    setHasError(false);
    startRun(t2);
  }, [runState.running, startRun]);
  const onStop = () => stopRun();
  const onRetry = useCallback(() => {
    if (runState.running) return;
    const t2 = lastPlayerText && lastPlayerText.trim();
    if (!t2) { window.__apiToast?.('没有可重试的输入', { kind: 'warn', duration: 2000 }); return; }
    setHasError(false);
    setHistory((h) => {
      const out = [...h];
      while (out.length && out[out.length - 1].role === 'assistant' && !(out[out.length - 1].content || '').trim()) out.pop();
      if (out.length && out[out.length - 1].role === 'user' && (out[out.length - 1].content || '').trim() === t2) out.pop();
      return out;
    });
    startRun(t2);
  }, [lastPlayerText, runState.running]);
  const onShowSse = useCallback(() => setSseLogOpen(true), []);

  const onSlashPick = (cmd) => {
    if (cmd && typeof cmd.trigger === 'string' && cmd.trigger.endsWith(' ')) {
      setText(cmd.trigger); setPickedCommand(null); setShowSlash(false); return;
    }
    setPickedCommand(cmd); setText(''); setShowSlash(false);
  };
  const onAttachPick = (item) => {
    const fixtures = { file: { name: '南陵卷宗.md', kind: 'file' }, image: { name: '雾港地图.png', kind: 'image' }, chapter: { name: '第 314 章 · 北港', kind: 'chapter' }, card: { name: '角色卡 · 沈知微', kind: 'card' }, world: { name: '世界书 · 残页', kind: 'world' }, mcp: { name: 'MCP · 文件检索', kind: 'mcp' }, skill: { name: 'Skill · 角色一致性', kind: 'skill' }, plan: { name: '计划模式', kind: 'skill' } };
    setAttachments((a) => [...a, fixtures[item.id] || { name: item.label, kind: 'file' }]);
    setShowPlus(false);
  };

  const _matchPending = (target) => (item, idx) => {
    if (target.id != null && item.id != null) return item.id !== target.id;
    return idx !== target.index;
  };
  const onApprove = async (target) => {
    setPendingWrites((arr) => arr.filter(_matchPending(target)));
    try { await window.api.game.pendingWrite({ id: target.id, index: target.index, action: 'approve' }); } catch (e) { window.__apiToast?.('审批失败', { kind: 'danger', detail: e?.message }); }
    try { const d = await window.api.game.state(); if (d && d.permissions) { setPendingWrites(d.permissions.pending_writes || []); setPendingQuestions(d.permissions.pending_questions || []); } } catch (_) {}
  };
  const onReject = async (target) => {
    setPendingWrites((arr) => arr.filter(_matchPending(target)));
    try { await window.api.game.pendingWrite({ id: target.id, index: target.index, action: 'reject' }); } catch (e) { window.__apiToast?.('拒绝失败', { kind: 'danger', detail: e?.message }); }
    try { const d = await window.api.game.state(); if (d && d.permissions) { setPendingWrites(d.permissions.pending_writes || []); setPendingQuestions(d.permissions.pending_questions || []); } } catch (_) {}
  };
  const onAnswerQuestion = async (target, choice) => {
    setPendingQuestions((arr) => arr.filter(_matchPending(target)));
    try { await window.api.game.clearQuestions({ id: target.id, index: target.index, choice }); } catch (e) { window.__apiToast?.('回答失败', { kind: 'danger', detail: e?.message }); }
    try { const d = await window.api.game.state(); if (d && d.permissions) { setPendingWrites(d.permissions.pending_writes || []); setPendingQuestions(d.permissions.pending_questions || []); } } catch (_) {}
    const nextAction = String(choice || '').trim();
    if (nextAction) startRun(nextAction);
  };
  const onDismissConfirm = (target) => {
    setPendingWrites((arr) => arr.filter(_matchPending(target)));
    setPendingQuestions((arr) => arr.filter(_matchPending(target)));
  };

  useEffect(() => {
    if (!window.api) return;
    window.api.game.permissions({ mode: permission }).catch(() => {});
  }, [permission]);

  const rootStyle = useMemo(() => {
    const densityMap = { compact: 0.92, normal: 1, comfy: 1.1 };
    return { '--density': densityMap[t.density] || 1, '--ui-size': t.uiSize + 'px', '--narrative-size': t.narrativeSize + 'px' };
  }, [t.density, t.uiSize, t.narrativeSize]);

  const [mountStage, setMountStage] = useState(0);
  useEffect(() => {
    if (mountStage >= 2) return;
    const id = requestAnimationFrame(() => { requestAnimationFrame(() => setMountStage((s) => Math.min(2, s + 1))); });
    return () => cancelAnimationFrame(id);
  }, [mountStage]);

  return (
    <div className="gc-shell" style={{ ...rootStyle, '--gc-rail-w': gcRailW + 'px' }}>
      {mountStage >= 2 && <GameToastStack />}
      {mountStage >= 1 ? <LeftRail
        resizeHandle={<div className="gc-rail-resize-handle" title="拖动调整宽度 · 双击恢复默认" {...gcRailDragProps} />}
        collapsed={railCollapsed}
        onToggle={() => setRailCollapsed((c) => !c)}
        state={game} runState={runState}
        onNew={() => { if (!confirm('新建存档需要选择剧本与角色,将跳到平台『存档目录』走正规创建流。\n\n确认跳转?')) return; window.open('Platform.html#saves-list', '_blank'); }}
        onSave={async () => { try { await window.api.game.saveGame(); window.__apiToast?.('已保存', { kind: 'ok' }); } catch (e) { window.__apiToast?.('保存失败', { kind: 'danger', detail: e?.message }); } }}
        onSwitchSave={async (sid) => { try { await window.api.saves.activate(sid); reloadState(); } catch (e) { window.__apiToast?.('切换失败', { kind: 'danger', detail: e?.message }); } }}
        onMemoryMode={async (mode) => { setGame((g) => ({ ...g, memory: { ...(g.memory || {}), mode } })); try { await window.api.game.memoryMode(mode); } catch (_) {} }}
        currentSaveId={activeSave?.id ?? null}
        saves={realSaves.length ? realSaves : ((window.RPG_AUTH && window.RPG_AUTH.authed) ? [] : (window.MOCK_PLATFORM?.saves || []))}
      /> : <aside className="gc-rail" aria-hidden="true" />}

      <main className="gc-main">
        {mountStage >= 1 && <TopBar
          state={game}
          saveUpdatedAt={activeSave?.updated_at || ''}
          onOpenTweaks={openTweaks}
          onOpenSearch={() => setShowSearchDrawer(true)}
          onOpenHistory={() => setShowHistoryDrawer(true)}
          onOpenSettings={() => setShowInGameSettings(true)}
          railCollapsed={railCollapsed}
          onExpandRail={() => setRailCollapsed(false)}
          panelCollapsed={panelCollapsed}
          onExpandPanel={() => setPanelCollapsed(false)}
          assistantCollapsed={!assistantOpen}
          onExpandAssistant={() => setAssistantOpen(true)}
        />}
        {mountStage >= 2 && <>
          <GameSettingsModal open={showInGameSettings} onClose={() => setShowInGameSettings(false)} saveTitle={activeSave?.title || game?._raw?.save_title || ''} permission={permission} />
          <HistoryDrawer open={showHistoryDrawer} history={history} onClose={() => setShowHistoryDrawer(false)} />
          <SearchDrawer open={showSearchDrawer} history={history} state={game} onClose={() => setShowSearchDrawer(false)} />
        </>}
        {/* Wave 11-D: GM 模型选择 — ModelPicker modal overlay */}
        {showModel && (() => {
          const _MP = ModelPicker;
          const _currentModelId = (game && game.app && (game.app.model_real_name || game.app.model)) || '';
          const _handleModelChange = async (modelId, _provider) => {
            try {
              if (window.api && window.api.models && window.api.models.select) {
                // 找到 provider api_id 对应关系
                const cat = await (window.api.models.catalog ? window.api.models.catalog() : Promise.resolve({ models: [] }));
                const info = cat && Array.isArray(cat.models) ? cat.models.find(m => m.id === modelId) : null;
                const apiId = info ? String(info.provider) : '';
                await window.api.models.select({ api_id: apiId, model_id: modelId });
                window.__apiToast?.(`GM 模型 → ${modelId}`, { kind: 'ok', duration: 1500 });
              }
            } catch (e) { window.__apiToast?.('切换失败', { kind: 'danger', detail: e && e.message }); }
            setShowModel(false);
          };
          return (
            <div
              style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 9998, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
              onClick={() => setShowModel(false)}
            >
              <div
                onClick={e => e.stopPropagation()}
                style={{ width: 'min(620px, 94vw)', maxHeight: '82vh', display: 'flex', flexDirection: 'column', borderRadius: 'var(--r-3,8px)', overflow: 'hidden', boxShadow: 'var(--shadow-3)' }}
              >
                <div style={{ background: 'var(--panel,#211f1d)', borderBottom: '1px solid var(--line-soft,#2a2724)', padding: '12px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 14, letterSpacing: '0.03em' }}>选择 GM 模型</strong>
                  <button className="iconbtn" onClick={() => setShowModel(false)} title="关闭" style={{ border: 0, background: 'transparent', color: 'var(--muted)', cursor: 'pointer', padding: '4px 8px', borderRadius: 4 }}>✕</button>
                </div>
                <div style={{ overflow: 'auto', flex: 1 }}>
                  <_MP
                    value={_currentModelId}
                    onChange={_handleModelChange}
                    filter={{ capability: 'streaming' }}
                  />
                </div>
              </div>
            </div>
          );
        })()}
        {mountStage >= 1 ? <ChatArea
          history={history} runState={runState} runStyle={t.runStyle}
          narrativeFont={t.narrativeFont} narrativeSize={t.narrativeSize}
          hasError={hasError}
          saveId={(activeSave && activeSave.id) || (game && game._raw && game._raw.save_id) || null}
          onRetry={onRetry} onShowSse={onShowSse}
        /> : <div className="gc-chat" aria-busy="true" />}
        <div className="gc-foot-wrap">
          <ConfirmStrip pendingWrites={pendingWrites} pendingQuestions={pendingQuestions} onApprove={onApprove} onReject={onReject} onAnswer={onAnswerQuestion} onDismiss={onDismissConfirm} />
          <Composer
            text={text} setText={setText} onSend={onSend} onStop={onStop} running={runState.running}
            onSendRaw={onSendRaw} permission={permission} setPermission={setPermission}
            model={model} setModel={setModel} composerMode={t.composerMode}
            suggestions={game.suggestions} gameState={game}
            attachments={attachments} removeAttachment={(i) => setAttachments((a) => a.filter((_, j) => j !== i))}
            onAttachPick={onAttachPick} onSlashPick={onSlashPick}
            pickedCommand={pickedCommand} onClearCommand={() => setPickedCommand(null)}
            showSlash={showSlash} showPlus={showPlus} showModel={false} showPerm={showPerm}
            toggleSlash={() => { setShowSlash((s) => !s); setShowPlus(false); setShowModel(false); setShowPerm(false); }}
            togglePlus={() => { setShowPlus((s) => !s); setShowSlash(false); setShowModel(false); setShowPerm(false); }}
            toggleModel={() => { setShowModel((s) => !s); setShowSlash(false); setShowPlus(false); setShowPerm(false); }}
            togglePerm={() => { setShowPerm((s) => !s); setShowSlash(false); setShowPlus(false); setShowModel(false); }}
          />
        </div>
      </main>

      {sseLogOpen && (
        <div className="gc-overlay" style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setSseLogOpen(false)}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 'min(860px, 92vw)', maxHeight: '82vh', background: 'var(--surface, #1a1d22)', color: 'var(--text, #e6e6e6)', borderRadius: 8, border: '1px solid var(--line, #333)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid var(--line, #333)' }}>
              <strong>本轮 SSE 事件流（{sseLog.length} 条）</strong>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn ghost" onClick={async () => { try { await navigator.clipboard.writeText(JSON.stringify(sseLog, null, 2)); window.__apiToast?.('已复制全部事件', { kind: 'ok', duration: 1500 }); } catch { window.__apiToast?.('复制失败', { kind: 'danger' }); } }}>复制 JSON</button>
                <button className="btn ghost" onClick={() => setSseLogOpen(false)}>关闭</button>
              </div>
            </div>
            <div style={{ overflow: 'auto', padding: '8px 16px', fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)', fontSize: 12, lineHeight: 1.5 }}>
              {sseLog.length === 0 && <div style={{ padding: '24px 0', color: 'var(--muted, #888)' }}>暂无事件（本轮未开始或已被清空）</div>}
              {sseLog.map((ev, i) => (
                <div key={i} style={{ padding: '4px 0', borderBottom: '1px dashed var(--line-soft, #2a2d33)' }}>
                  <span style={{ color: 'var(--muted-2, #777)' }}>[{new Date(ev.t).toISOString().slice(11, 23)}]</span>{' '}
                  <span style={{ color: 'var(--accent, #d4a45e)' }}>{ev.kind}</span>{' '}
                  <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{JSON.stringify(ev.payload)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {mountStage >= 2 && <RightPanel state={game} activeTab={activeTab} setActiveTab={setActiveTab} sidebarWidth={gcPanelW} density={t.density} collapsed={panelCollapsed} onToggle={() => setPanelCollapsed((c) => !c)} resizeHandle={<div className="gp-panel-resize-handle" title="拖动调整宽度 · 双击恢复默认" {...gcPanelDragProps} />} />}
      <button className="gc-float-panel-btn" onClick={() => { setPanelCollapsed(false); _panelResize.setSize(320); }} title="打开状态面板">⌖</button>
      {mountStage >= 2 && <ConsoleAssistantPanel open={assistantOpen} onClose={() => setAssistantOpen(false)} pageContext={{ tab: 'game_console', save_id: activeSave?.id ?? null }} />}
    </div>
  );
}

const __mount = () => ReactDOM.createRoot(document.getElementById('root')).render(<App />);
const __gateThenMount = (info) => {
  const offline = new URLSearchParams(location.search).has('offline');
  if (info && info.online && !info.authed && !offline) {
    const next = encodeURIComponent(location.pathname + location.search + location.hash);
    location.replace('Login.html?next=' + next);
    return;
  }
  __mount();
};
if (window.RPG_DATA_READY) {
  window.RPG_DATA_READY.then(__gateThenMount);
} else {
  __mount();
}
