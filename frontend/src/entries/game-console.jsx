// Game Console 页面入口 — Vite ESM 版
import '../web-vitals-rum.js';
import React from 'react';
import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import * as ReactDOM from 'react-dom/client';

// 基础设施 side-effect 模块
import '../mock-data.js';
import '../api-client.js';
// 运行环境采集 — 反馈抽屉提交时附带最近 20 个错误 + 10 个失败 API 给管理员排查
import '../runtime-telemetry.js';
import '../data-loader.js';
import '../state-event-bridge.js';
import '../worldbook-status-toast.js';
import '../ui-atlas.js';
import '../a11y-tooltip-labels.js';   // data-tip → aria-label 镜像(屏幕阅读器)
import '../console-assistant-navigation.jsx';
import '../i18n/index.js';   // 初始化 i18next + 接 interfaceLang
import { useTranslation } from 'react-i18next';
import i18n from '../i18n';
import { lsGet, lsSet, lsRemove } from '../lib/storage.js';

// 反馈抽屉使用 Cloudscape 组件；游戏页也必须加载同一套暗色主题。
import '@cloudscape-design/global-styles/index.css';
import { installWarmTheme } from '../cloudscape-theme.js';
installWarmTheme();

// 组件模块 — named import
import { useResizable, useBreakpoint } from '../responsive.jsx';
// 移动原生游戏台(P2)— <600px 且开启 m2 标志时替代电脑端 gc-shell。
import '../mobile.css';
import '../media.css';
import { MobileGame } from '../mobile/game/MobileGame.jsx';
import { safeUUID } from '../lib/crypto-safe.js';
import { LeftRail, TopBar, ChatArea, HistoryDrawer, SearchDrawer, GameToastStack, RunSteps, GameSettingsModal } from '../game-app.jsx';
import { Composer, ConfirmStrip } from '../game-composer.jsx';
import { RightPanel, PANEL_TABS } from '../game-panels.jsx';
// AGE-02: splash gate
import AdultSplash from '../components/AdultSplash.jsx';
import { ErrorBoundary } from '../components/ErrorBoundary.jsx';
import { FeedbackDrawerRoot } from '../components/FeedbackDrawer.jsx';
import GlobalTaskFloater from '../components/GlobalTaskFloater.jsx';
import ModelConfigInterceptModal, { capConfig } from '../components/ModelConfigInterceptModal.jsx';
const SPLASH_VERSION = 'v1.0-2026-05-31';

// density preset + narrative font init（等价原 HTML 非 babel inline script）
(function () {
  const VALID_DENSITY = { compact: 1, default: 1, spacious: 1 };
  function _applyDensity(d) {
    if (!VALID_DENSITY[d]) d = 'default';
    document.documentElement.setAttribute('data-density', d);
    lsSet('rpg.density', d);
    window.dispatchEvent(new CustomEvent('rpg-density-change', { detail: d }));
  }
  const storedDensity = lsGet('rpg.density') || 'default';
  _applyDensity(storedDensity);
  window.RPG_setDensity = _applyDensity;

  const FONT_MAP = { serif: 'var(--font-serif)', sans: 'var(--font-sans)', mono: 'var(--font-mono)' };
  const storedFont = lsGet('rpg.narrativeFont') || 'serif';
  if (FONT_MAP[storedFont]) {
    document.documentElement.style.setProperty('--narrative-font', FONT_MAP[storedFont]);
  }
})();

// ---- Script Version Select — 顶栏当前 script 版本切换 dropdown ----
// 调 GET /api/scripts/{id}/commits?limit=10 拉最近 10 个 commit;
// 选中后调 POST /api/scripts/{id}/checkout/{commit_id}(stub, 返 501 时提示)。
function ScriptVersionSelect({ scriptId, headCommitId }) {
  const { t } = useTranslation();
  const [commits, setCommits] = React.useState([]);
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (!scriptId) return;
    (async () => {
      try {
        const r = await window.api.scripts.commits(scriptId, { limit: 10 });
        const list = Array.isArray(r) ? r : (r?.items || r?.commits || []);
        setCommits(list);
      } catch (_) {}
    })();
  }, [scriptId]);

  if (!scriptId || commits.length === 0) return null;

  const headShort = headCommitId ? headCommitId.slice(0, 8) : '—';

  const onCheckout = async (commitId) => {
    setOpen(false);
    if (!commitId || commitId === headCommitId) return;
    setBusy(true);
    try {
      const r = await window.api.scripts.checkout(scriptId, commitId);
      if (r && r.status === 501) {
        window.__apiToast?.(t('game.console.version.checkout_not_impl'), { kind: 'warn', duration: 3000 });
      } else {
        window.__apiToast?.(t('game.console.version.switched', { sha: commitId.slice(0, 8) }), { kind: 'ok', duration: 2000 });
      }
    } catch (e) {
      const detail = e?.message || '';
      if (detail.includes('501') || detail.includes('not impl') || detail.includes('Not Implemented')) {
        window.__apiToast?.(t('game.console.version.checkout_not_impl'), { kind: 'warn', duration: 3000 });
      } else {
        window.__apiToast?.(t('game.console.version.switch_failed'), { kind: 'danger', detail });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ position: 'relative', display: 'inline-block', marginLeft: 8 }}>
      <button
        className="btn ghost"
        style={{ fontSize: 11.5, padding: '2px 8px', borderRadius: 4, display: 'flex', alignItems: 'center', gap: 4, opacity: busy ? 0.6 : 1 }}
        onClick={() => setOpen(v => !v)}
        title={t('game.console.version.switch_tip')}
        disabled={busy}
      >
        <span>HEAD · {headShort}</span>
        <span style={{ fontSize: 10, opacity: 0.7 }}>▾</span>
      </button>
      {open && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 1499 }} onClick={() => setOpen(false)} />
          <div style={{
            position: 'absolute', top: '100%', left: 0, zIndex: 1500, marginTop: 4,
            background: 'var(--panel, #1a1d22)', border: '1px solid var(--line-soft)',
            borderRadius: 6, minWidth: 280, boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
            overflow: 'hidden',
          }}>
            <div style={{ padding: '6px 10px', fontSize: 11, color: 'var(--muted)', borderBottom: '1px solid var(--line-soft)' }}>
              {t('game.console.version.header', { count: commits.length })}
            </div>
            {commits.map((c) => {
              const isCurrent = headCommitId && c.id === headCommitId;
              return (
                <button
                  key={c.id}
                  className="btn ghost"
                  style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    width: '100%', padding: '6px 10px', borderRadius: 0, gap: 8,
                    background: isCurrent ? 'var(--accent-soft, rgba(212,164,94,0.12))' : 'transparent',
                    fontWeight: isCurrent ? 600 : 400, borderBottom: '1px solid var(--line-soft)',
                  }}
                  onClick={() => onCheckout(c.id)}
                >
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: isCurrent ? 'var(--accent)' : 'inherit' }}>
                    {(c.id || '').slice(0, 8)}
                  </span>
                  <span style={{ flex: 1, textAlign: 'left', fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.message || c.kind || '—'}
                  </span>
                  {isCurrent && (
                    <span style={{ fontSize: 10, color: 'var(--ok)', flexShrink: 0 }}>HEAD</span>
                  )}
                </button>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

// ---- GCWelcomeModal — 游戏控制台内的使用须知弹窗 ----
// 与 platform-app.jsx 的 WelcomeModal 功能等价，独立实现（不跨 bundle 导入）
function GCWelcomeModal({ open, onClose }) {
  const { t } = useTranslation();
  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 2000,
        background: 'rgba(0,0,0,0.65)', display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--panel, #1c1a18)', border: '1px solid var(--line-strong, #4a4540)',
          borderRadius: 12, width: 'min(520px, 96vw)', maxHeight: '88vh', overflowY: 'auto',
          padding: '20px 22px', boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--muted)', marginBottom: 4 }}>{t('game.console.welcome.eyebrow')}</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text)' }}>{t('game.console.welcome.title')}</div>
          </div>
          <button
            onClick={onClose}
            style={{ background: 'transparent', border: 0, cursor: 'pointer', color: 'var(--muted)', fontSize: 18, lineHeight: 1, padding: 4 }}
            aria-label={t('common.close')}
          >×</button>
        </div>
        {/* 测试期免责 */}
        <div style={{ background: 'rgba(220,80,60,0.10)', border: '1px solid rgba(220,80,60,0.3)', borderRadius: 8, padding: '10px 14px', marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#e07060', marginBottom: 4 }}>{t('game.console.welcome.disclaimer_title')}</div>
          <div style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--text-quiet, #9a9590)' }}>
            {t('game.console.welcome.disclaimer_body')}
          </div>
        </div>
        {/* 反馈流程 */}
        <div style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>{t('game.console.welcome.feedback_title')}</div>
          <div style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--text-quiet, #9a9590)' }}>
            {t('game.console.welcome.feedback_body')}
          </div>
        </div>
        {/* API 说明 */}
        <div style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>{t('game.console.welcome.byok_title')}</div>
          <div style={{ fontSize: 13, lineHeight: 1.65, color: 'var(--text-quiet, #9a9590)' }}>
            {t('game.console.welcome.byok_body')}
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button
            onClick={() => { onClose(); window.open('/settings-models', '_blank'); }}
            style={{ padding: '6px 14px', borderRadius: 6, border: '1px solid var(--line-strong)', background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: 13 }}
          >{t('game.console.welcome.configure_key_btn')}</button>
          <button
            onClick={onClose}
            style={{ padding: '6px 14px', borderRadius: 6, border: 0, background: 'var(--accent, #c49b4e)', color: '#1a1610', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}
          >{t('game.console.welcome.got_it_btn')}</button>
        </div>
      </div>
    </div>
  );
}

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

// 移动游戏台灰度开关:迁移期默认关闭(零影响真机用户),开发用 ?m2=1 或 localStorage。P8 改默认开。
const MOBILE_GAME_ENABLED = (() => {
  try {
    const q = new URLSearchParams(location.search);
    if (q.get('m2') === '1') { lsSet('rpg_mobile_v2', '1'); return true; }
    if (q.get('m2') === '0') { lsRemove('rpg_mobile_v2'); return false; }
    return lsGet('rpg_mobile_v2') === '1';
  } catch (_) { return false; }
})();

const PUBLIC_STAGES = {
  context: { id: 'context', get label() { return i18n.t('game.console.stage.context'); }, order: 1 },
  rules:   { id: 'rules',   get label() { return i18n.t('game.console.stage.context'); }, order: 2 },
  // task 64: 精简文案。thinking 期与生成期统一显示"生成中"。
  gm:      { id: 'gm',      get label() { return i18n.t('game.console.stage.generating'); }, order: 3 },
  save:    { id: 'save',    get label() { return i18n.t('game.console.stage.rendering'); }, order: 4 },
  system:  { id: 'system',  get label() { return i18n.t('game.console.stage.context'); }, order: 0 },
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
  const { t } = useTranslation();
  // 旧 useTweaks/setTweak 用法迁出(tweaks-panel.jsx 已删,只是设计原型工具);
  // 这里仅消费默认值,改成普通常量即可。
  const tweaks = TWEAK_DEFAULTS;
  const openTweaks = () => window.postMessage({ type: '__activate_edit_mode' }, '*');

  // P2 移动游戏台:窄屏 + 灰度开关 → 渲染移动原生 UI(复用同一 run-loop,见下方早返)
  const { width: _vpWidth } = useBreakpoint();
  const mobileGame = MOBILE_GAME_ENABLED && _vpWidth > 0 && _vpWidth < 600;

  // A2: 多 tab 冲突检测 — BroadcastChannel
  // 同一 origin 内不同 tab 打开同一 save_id 时，后进者收到 banner 警告。
  const [tabConflictBanner, setTabConflictBanner] = useState(null); // null | { conflictTabId }
  // refs 让 activeSave effect 能向 channel 广播而不需要重新订阅
  const _tabChRef = useRef(null);    // BroadcastChannel instance
  const _tabIdRef = useRef(null);    // 本 tab 的唯一 ID
  const _tabSaveRef = useRef(null);  // 当前广播中的 save_id（字符串）

  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return; // 不支持的环境静默跳过
    const tabId = safeUUID();
    _tabIdRef.current = tabId;
    const ch = new BroadcastChannel('rpg-game-tabs');
    _tabChRef.current = ch;

    const broadcast = (type, saveId) => {
      const sid = saveId ?? _tabSaveRef.current;
      if (!sid) return;
      ch.postMessage({ type, save_id: String(sid), tab_id: tabId, ts: Date.now() });
    };

    // 心跳：让晚打开的 tab 发现已有其他实例
    const heartbeatId = setInterval(() => broadcast('heartbeat'), 30000);

    ch.onmessage = (ev) => {
      const { type, save_id, tab_id } = ev.data || {};
      if (!save_id || !tab_id || tab_id === tabId) return; // 忽略自己
      const curSid = _tabSaveRef.current;
      if (!curSid || String(save_id) !== String(curSid)) return; // 不同存档不冲突

      if (type === 'mounted' || type === 'heartbeat') {
        // 另一个 tab 打开了同一存档
        setTabConflictBanner({ conflictTabId: tab_id });
        // 同时回告知对方我们也在
        if (type === 'mounted') broadcast('heartbeat');
      } else if (type === 'unmounted') {
        // 冲突的那个 tab 已关闭，隐藏 banner
        setTabConflictBanner((prev) => {
          if (prev && prev.conflictTabId === tab_id) return null;
          return prev;
        });
      }
    };

    return () => {
      clearInterval(heartbeatId);
      broadcast('unmounted');
      ch.close();
      _tabChRef.current = null;
    };
  }, []); // 仅 mount/unmount 一次

  // 当 activeSave 变化时由下方 useEffect 更新广播 save_id（activeSave 在下方声明）

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
  const [activeTab, setActiveTab] = useState(() => getRightTabForLocation(tweaks.defaultRightTab || 'status'));
  // 侧栏折叠状态持久化(localStorage,刷新后保留)。来自 PR #14。
  const [railCollapsed, setRailCollapsed] = useState(() => lsGet('gc.rail.collapsed') === 'true');
  const [panelCollapsed, setPanelCollapsed] = useState(() => lsGet('gc.panel.collapsed') === 'true');
  useEffect(() => { lsSet('gc.rail.collapsed', railCollapsed ? 'true' : 'false'); }, [railCollapsed]);
  useEffect(() => { lsSet('gc.panel.collapsed', panelCollapsed ? 'true' : 'false'); }, [panelCollapsed]);
  const [mobileNav, setMobileNav] = useState(false);  // 手机端: 左 rail 改汉堡抽屉的开关
  const [showSlash, setShowSlash] = useState(false);
  const [showPlus, setShowPlus] = useState(false);
  const [showModel, setShowModel] = useState(false);
  const [showPerm, setShowPerm] = useState(false);
  const [hasError, setHasError] = useState(false);
  const [showHistoryDrawer, setShowHistoryDrawer] = useState(false);
  const [showSearchDrawer, setShowSearchDrawer] = useState(false);
  const [showInGameSettings, setShowInGameSettings] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  // AGE-02: null = loading, true = need splash, false = no splash needed
  const [splashNeeded, setSplashNeeded] = useState(null);
  // GC 使用须知弹窗（随时可打开）
  const [welcomeGCOpen, setWelcomeGCOpen] = useState(false);
  useEffect(() => {
    fetch('/api/me/splash/status', { credentials: 'same-origin' })
      .then((r) => r.ok ? r.json() : null)
      .then((j) => { setSplashNeeded(j ? !j.acked : false); })
      .catch(() => { setSplashNeeded(false); });
  }, []);

  // 暴露 window.__openWelcome，供 TopBar 📖 使用须知按钮触发
  useEffect(() => {
    window.__openWelcome = () => setWelcomeGCOpen(true);
    return () => { delete window.__openWelcome; };
  }, []);
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
    // 宽度不足 180 时自动折叠；但不再自动展开——折叠/展开完全由用户控制
    if (gcPanelW < 180 && !panelCollapsed) setPanelCollapsed(true);
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
  // #11: token 用量显示 — lastUsage 存本轮 usage 事件,showUsage 由设置开关控制(默认关)
  const [lastUsage, setLastUsage] = useState(null);
  const [clicheNotice, setClicheNotice] = useState(null);  // 反馈#22: 套路比喻提示
  const [showUsage, setShowUsage] = useState(() => lsGet('rpg.showTokenUsage') === 'on');
  useEffect(() => {
    const onUsageChange = (e) => setShowUsage(!!(e && e.detail));
    window.addEventListener('rpg-show-usage-change', onUsageChange);
    return () => window.removeEventListener('rpg-show-usage-change', onUsageChange);
  }, []);

  const [runState, setRunState] = useState({
    running: false, publicStage: null, label: '', detail: '',
    totalElapsed: 0, completedAt: 0, completedElapsed: 0, rawSteps: [],
  });
  const runRef = useRef({ timers: [], stopped: false, sse: null, doneTimer: null, runId: 0 });

  const [pendingWrites, setPendingWrites] = useState(
    (INITIAL_STATE.permissions && INITIAL_STATE.permissions.pending_writes) || []
  );
  const [pendingQuestions, setPendingQuestions] = useState(
    (INITIAL_STATE.permissions && INITIAL_STATE.permissions.pending_questions) || []
  );
  const [realSaves, setRealSaves] = useState([]);
  const [activeSave, setActiveSave] = useState(null);
  // task #61: activeSave 变化时更新 BroadcastChannel 广播的 save_id
  useEffect(() => {
    const sid = activeSave?.id != null ? String(activeSave.id) : null;
    if (!sid || sid === _tabSaveRef.current) return;
    _tabSaveRef.current = sid;
    const ch = _tabChRef.current;
    const tabId = _tabIdRef.current;
    if (ch && tabId) {
      ch.postMessage({ type: 'mounted', save_id: sid, tab_id: tabId, ts: Date.now() });
    }
  }, [activeSave?.id]);
  const [retryFailed, setRetryFailed] = useState(false);
  // G6: state loaded successfully but player not yet set up (new save pending opening)
  const [stateLoadedNoPlayer, setStateLoadedNoPlayer] = useState(false);

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
        setStateLoadedNoPlayer(false);
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
            // G7: 从 800ms 硬延迟改为 requestAnimationFrame,UI 已 mount 直接发请求
            requestAnimationFrame(() => {
              try {
                const sse = window.api && window.api.raw && window.api.raw.sseStream;
                if (!sse) return;
                let openingText = '';
                let openingRetried = false;
                setHistory((h) => {
                  const arr = Array.isArray(h) ? [...h] : [];
                  arr.push({ role: 'assistant', content: t('game.console.opening.curtain'), _opening: true, _thinking: 'starting' });
                  return arr;
                });
                const triggerOpening = () => {
                  // G8: 开场期间设置 running=true,使 stop 按钮可见可用
                  setRunState((r) => ({ ...r, running: true, publicStage: 'context', label: t('game.console.opening.raising_curtain'), detail: '' }));
                  // sseStream 的 onClose 在 reader EOF 时永远会触发,即使刚收到 done。
                  // 用本地 flag 区分「正常完成后 close」vs「无 done 直接 close」 —
                  // 否则正常完成的 opening 末尾会被错误加上『[连接断开,内容可能不完整]』。
                  let gotDone = false;
                  // G8: 保存 handle 到 runRef.current.sse,使 stop 按钮可中断 opening
                  const handle = sse('/api/v1/opening', {}, {
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
                      gotDone = true;
                      runRef.current.sse = null;
                      // 标记 opening message 流式完成,避免 onClose 误判为断线
                      setHistory((h) => {
                        const arr = Array.isArray(h) ? [...h] : [];
                        const last = arr[arr.length - 1];
                        if (last && last._opening) {
                          arr[arr.length - 1] = { ...last, streaming: false };
                        }
                        return arr;
                      });
                      setRunState((r) => ({ ...r, running: false, publicStage: null, label: '', detail: '' }));
                      setTimeout(async () => {
                        try {
                          const d2 = await window.api.game.state();
                          if (d2 && d2.player) {
                            if (Array.isArray(d2.history)) {
                              setHistory(d2.history);
                            }
                            if (d2.permissions) {
                              setPermission(d2.permissions.mode || 'full_access');
                              setPendingWrites(d2.permissions.pending_writes || []);
                              setPendingQuestions(d2.permissions.pending_questions || []);
                            }
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
                      runRef.current.sse = null;
                      // G9: 断线重试 — 未收到任何 token 时自动重试一次
                      if (!openingText && !openingRetried) {
                        // will retry, keep running=true
                        openingRetried = true;
                        setTimeout(() => { try { triggerOpening(); } catch (e) { console.warn('[opening] retry error', e); } }, 1000);
                      } else {
                        setRunState((r) => ({ ...r, running: false, publicStage: null, label: '', detail: '' }));
                        setHistory((h) => {
                          const arr = Array.isArray(h) ? [...h] : [];
                          if (arr.length && arr[arr.length - 1]._opening && arr[arr.length - 1]._thinking) arr.pop();
                          if (openingText) {
                            arr.push({ role: 'assistant', content: openingText + '\n\n*' + t('game.console.run.disconnected_incomplete') + '*', _opening: true });
                          }
                          return arr;
                        });
                      }
                    },
                    onClose: () => {
                      runRef.current.sse = null;
                      // 正常完成路径:on_done 已经触发并 setRunState/setHistory streaming=false,
                      // 这里直接 noop,绝不能再加『连接断开』。
                      if (gotDone) return;
                      // G9: onClose 也做同样断线处理 — 仅在没收到 done 时执行
                      if (!openingText && !openingRetried) {
                        openingRetried = true;
                        setTimeout(() => { try { triggerOpening(); } catch (e) { console.warn('[opening] retry (onClose) error', e); } }, 1000);
                      } else {
                        setRunState((r) => ({ ...r, running: false, publicStage: null, label: '', detail: '' }));
                        if (openingText) {
                          setHistory((h) => {
                            const arr = Array.isArray(h) ? [...h] : [];
                            const last = arr[arr.length - 1];
                            if (last && last._opening && last.streaming !== false) {
                              arr[arr.length - 1] = { ...last, content: (last.content || '') + '\n\n*' + t('game.console.run.disconnected_incomplete') + '*', _opening: true };
                            }
                            return arr;
                          });
                        }
                      }
                    },
                  });
                  // G8: 挂到 runRef,与 chat SSE 保持一致
                  if (handle && typeof handle === 'object') runRef.current.sse = handle;
                };
                triggerOpening();
              } catch (e) { console.warn('[opening] trigger error', e); }
            });
          }
        } catch (_) {}
      } else if (data && data.save_id != null) {
        // G6: 存档存在但 player 尚未 setup(新建存档等待开场),标记而非静默降级
        setStateLoadedNoPlayer(true);
      }
      if (data && data.save_id != null) {
        setActiveSave({ id: data.save_id, title: data.save_title || t('game.console.save.untitled', { id: data.save_id }), updated_at: data.save_updated_at || '' });
      }
      // 返回是否真正拿到了可玩状态(供 mount 重试判断是否还要再拉一次)。
      return !!(data && data.player);
    } catch (e) {
      console.warn('[reloadState] error', e);
      return false;
    }
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
      let ok = false;
      for (let i = 0; i < 6 && !cancelled; i++) {
        ok = await reloadState();
        await reloadSaves();
        if (ok || cancelled) break;
        await new Promise((r) => setTimeout(r, 400));
      }
      if (!ok && !cancelled) setRetryFailed(true);
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

  useEffect(() => { setActiveTab(getRightTabForLocation(tweaks.defaultRightTab || 'status')); }, [tweaks.defaultRightTab]);
  useEffect(() => {
    const onHashChange = () => setActiveTab(getRightTabForLocation(tweaks.defaultRightTab || 'status'));
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, [tweaks.defaultRightTab]);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') { setShowSlash(false); setShowPlus(false); setShowModel(false); setShowPerm(false); }
      if (e.key === '/' && document.activeElement === document.body) { e.preventDefault(); setShowSlash(true); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  // 卸载清理:回合进行中卸载(SPA 导航 / 重挂 / HMR / ErrorBoundary)时,abort 在途
  // SSE 流 + 清所有计时器,避免孤儿流继续烧 token + 200ms ticker 对已卸载组件 setState。
  // 只操作 runRef(不 setState),unmount 安全。
  useEffect(() => () => {
    const rc = runRef.current;
    rc.stopped = true;
    rc.timers.forEach((t) => { try { clearTimeout(t); } catch (_) {} try { clearInterval(t); } catch (_) {} });
    rc.timers = [];
    if (rc.doneTimer) { clearTimeout(rc.doneTimer); rc.doneTimer = null; }
    if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
    if (rc.sse) { try { rc.sse.stop('unmount'); } catch (_) {} rc.sse = null; }
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
    if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
    runRef.current.runId = (runRef.current.runId || 0) + 1;
    if (runRef.current.sse) { try { runRef.current.sse.stop('manual_stop'); } catch (_) {} runRef.current.sse = null; }
    try { window.api.game.stop(); } catch (_) {}
    setRunState((r) => ({ ...r, running: false, label: t('game.console.run.stopped'), detail: '', publicStage: null, completedAt: 0, completedElapsed: r.totalElapsed }));
  }, [t]);

  const startRunReal = useCallback(async (playerText) => {
    // task #61: 存档冲突时拒绝发送（用户点"继续"后 banner 清空才恢复）
    if (tabConflictBanner) {
      window.__apiToast?.(t('game.console.conflict.toast_title'), { kind: 'warn', detail: t('game.console.conflict.toast_detail'), duration: 4000 });
      return;
    }
    // 防重入/防残留:开新一轮前,先 abort 任何在途流与残留计时器。
    // (规避 onAnswerQuestion / 重新生成 / 连点发送 触发的双流并发——后一个 sse handle
    //  覆盖前一个,前者永远 stop 不掉 → 既串戏又泄漏。)
    {
      const rc = runRef.current;
      if (rc.sse) { rc.runId = (rc.runId || 0) + 1; try { rc.sse.stop('superseded'); } catch (_) {} rc.sse = null; try { window.api.game.stop(); } catch (_) {} }
      rc.timers.forEach((t) => { try { clearTimeout(t); } catch (_) {} try { clearInterval(t); } catch (_) {} });
      rc.timers = [];
      if (rc.inactivityTimer) { clearTimeout(rc.inactivityTimer); rc.inactivityTimer = null; }
    }
    const runId = (runRef.current.runId || 0) + 1;
    runRef.current.runId = runId;
    const isCurrentRun = () => runRef.current.runId === runId;
    const ts = (window.__fmt && window.__fmt.nowHHMM) ? window.__fmt.nowHHMM() : new Date().toLocaleTimeString().slice(0, 5);
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
    setClicheNotice(null);  // 反馈#22: 新一轮清掉上轮的套路比喻提示
    if (runRef.current.doneTimer) { clearTimeout(runRef.current.doneTimer); runRef.current.doneTimer = null; }
    runRef.current.stopped = false;
    let lastSseEvent = { t: Date.now(), kind: 'send', payload: { message_len: String(playerText || '').length } };
    const logEvent = (kind, payload) => {
      const entry = { t: Date.now(), kind, payload };
      lastSseEvent = entry;
      setSseLog((l) => (l.length >= 500 ? l : [...l, entry]));
    };
    const tickerId = setInterval(() => {
      if (runRef.current.stopped) { clearInterval(tickerId); return; }
      setRunState((r) => ({ ...r, totalElapsed: Date.now() - startedAt }));
    }, 200);
    runRef.current.timers.push(tickerId);
    // #7 深度思考: 30s→120s。思考模型(reasoning)首 token 前可能静默 30-90s,30s 太短会把
    // 正常深度思考误判超时。reasoning/status/token 等任何活动都会重置此计时(见各 handler)。
    const STREAM_IDLE_TIMEOUT_MS = 120000;
    let openedAssistant = false;
    let gotReceipt = false;  // #13: 本轮是否收到 system_receipt(斜杠命令回执)
    let gotDone = false;
    let streamFailed = false;
    let reasoningBuf = '';   // #7: 本轮累计的 reasoning(思考过程)文本
    const describeLastSseEvent = () => {
      const ev = lastSseEvent || {};
      const age = ev.t ? Math.max(0, Math.round((Date.now() - ev.t) / 1000)) : null;
      const suffix = age == null ? '' : t('game.console.sse.ago', { seconds: age });
      if (ev.kind === 'token') return t('game.console.sse.last_token') + suffix;
      if (ev.kind === 'reasoning') return t('game.console.sse.last_reasoning') + suffix;
      if (ev.kind === 'agent') return t('game.console.sse.last_agent', { phase: (ev.payload && ev.payload.phase) || 'agent' }) + suffix;
      if (ev.kind === 'status') return t('game.console.sse.last_status') + suffix;
      if (ev.kind === 'usage') return t('game.console.sse.last_usage') + suffix;
      if (ev.kind === 'send') return t('game.console.sse.sent_no_response') + suffix;
      return t('game.console.sse.last_event', { kind: ev.kind || t('common.unknown') }) + suffix;
    };
    const restoreFailedDraft = () => {
      if (!isCurrentRun()) return;
      if (openedAssistant) return;
      setText((cur) => (String(cur || '').trim() ? cur : playerText));
      setAttachments((cur) => (Array.isArray(cur) && cur.length ? cur : sentAttachments));
      setHistory((h) => {
        const last = h[h.length - 1];
        if (last && last.role === 'user' && last.content === playerText) return h.slice(0, -1);
        return h;
      });
    };
    const resetInactivityTimer = () => {
      if (runRef.current.inactivityTimer) clearTimeout(runRef.current.inactivityTimer);
      runRef.current.inactivityTimer = setTimeout(() => {
        if (!isCurrentRun()) return;
        streamFailed = true;
        logEvent('idle_timeout', { ms: STREAM_IDLE_TIMEOUT_MS });
        try { runRef.current.sse && runRef.current.sse.stop && runRef.current.sse.stop('idle_timeout'); } catch (_) {}
        restoreFailedDraft();
        setRunState((r) => {
          if (!r.running) return r;
          setHasError(t('game.console.run.idle_timeout_error'));
          window.__apiToast?.(t('game.console.run.idle_timeout_title'), { kind: 'warn', detail: t('game.console.run.idle_timeout_detail'), duration: 4000 });
          return { ...r, running: false, label: t('game.console.run.timed_out'), detail: t('game.console.run.idle_timeout_detail'), publicStage: null, completedAt: 0 };
        });
      }, STREAM_IDLE_TIMEOUT_MS);
    };
    resetInactivityTimer();
    const _chatSaveId = activeSave?.id ?? null;
    runRef.current.sse = window.api.game.chat(
      { message: playerText, text: playerText, attachments: sentAttachments, model, command: pickedCommand?.id || null, save_id: _chatSaveId },
      {
        // task #61: HTTP 层错误（如 409 save_id_mismatch）
        onError: (err) => {
          if (!isCurrentRun()) return;
          streamFailed = true;
          clearInterval(tickerId);
          if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
          const code = err && err.payload && err.payload.code;
          const detail = err && err.payload && err.payload.message;
          logEvent('http_error', { status: err && err.status, code, message: (err && err.message) || detail || t('game.console.run.request_failed') });
          if (code === 'save_id_mismatch') {
            setRunState((r) => ({ ...r, running: false, label: t('game.console.conflict.toast_title'), detail: detail || t('game.console.conflict.save_switched'), publicStage: null, completedAt: 0 }));
            setHasError(detail || t('game.console.conflict.save_switched_refresh'));
            window.__apiToast?.(t('game.console.conflict.toast_title'), { kind: 'warn', detail: detail || t('game.console.conflict.refresh_hint'), duration: 5000 });
          } else {
            const msg = (err && err.message) || t('game.console.run.request_failed');
            setRunState((r) => ({ ...r, running: false, label: t('game.console.run.request_failed'), detail: msg, publicStage: null, completedAt: 0 }));
            setHasError(msg);
            window.__apiToast?.(t('game.console.run.request_failed'), { kind: 'danger', detail: msg });
          }
          // 撤回本轮用户消息
          restoreFailedDraft();
        },
        onAbort: (data) => {
          if (!isCurrentRun()) return;
          const reason = (data && data.reason) || '';
          logEvent('abort', { reason, stopped: runRef.current.stopped });
          clearInterval(tickerId);
          if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
          if (runRef.current.stopped || reason === 'manual_stop' || reason === 'superseded' || reason === 'unmount' || reason === 'idle_timeout') {
            streamFailed = true;
            runRef.current.sse = null;
            return;
          }
          streamFailed = true;
          const msg = t('game.console.run.browser_abort_error');
          restoreFailedDraft();
          setRunState((r) => ({ ...r, running: false, label: t('game.console.run.cancelled'), detail: t('game.console.run.browser_abort_detail'), publicStage: null, completedAt: 0 }));
          setHasError(msg);
          window.__apiToast?.(t('game.console.run.generation_cancelled'), { kind: 'warn', detail: t('game.console.run.browser_abort_detail'), duration: 5000 });
          runRef.current.sse = null;
        },
        on_status: (data) => {
          if (!isCurrentRun()) return;
          logEvent('status', data);
          if (data && data.player) setGame((g) => {
            const n = { ...g };
            for (const k of PICK_STATE_KEYS) if (data[k] !== undefined) n[k] = data[k];
            n._raw = { save_id: data.save_id ?? n._raw?.save_id, save_title: data.save_title ?? n._raw?.save_title, turn: data.turn ?? n._raw?.turn };
            return n;
          });
          if (data && data.permissions) {
            setPermission(data.permissions.mode || 'full_access');
            setPendingWrites(data.permissions.pending_writes || []);
            setPendingQuestions(data.permissions.pending_questions || []);
          }
          if (data && data.save_id != null && (!activeSave || activeSave.id !== data.save_id)) {
            setActiveSave({ id: data.save_id, title: data.save_title || t('game.console.save.untitled', { id: data.save_id }), updated_at: data.save_updated_at || '' });
          }
        },
        on_reasoning: (data) => {
          if (!isCurrentRun()) return;
          // #7 深度思考: 思考过程流式 — 重置 idle 计时(防止长思考被误判超时)并在
          // thinking pill 显示思考预览。reasoning 不进主叙事 transcript(单独事件)。
          resetInactivityTimer();
          const piece = (data && (data.text || data.delta)) || '';
          if (!piece) return;
          reasoningBuf += piece;
          logEvent('reasoning', { len: piece.length, total: reasoningBuf.length });
          setRunState((r) => (r.running ? { ...r, label: t('game.console.run.thinking'), detail: reasoningBuf.slice(-90).replace(/\s+/g, ' ').trim() } : r));
        },
        on_token: (data) => {
          if (!isCurrentRun()) return;
          resetInactivityTimer();
          logEvent('token', { len: ((data && (data.text || data.delta)) || '').length });
          const piece = (data && (data.text || data.delta)) || '';
          if (!piece) return;
          // task 141: 第一个 token = thinking 结束,切到"正在生成 GM 回复"
          const wasFirstToken = !openedAssistant;
          setHistory((h) => {
            if (!openedAssistant) { openedAssistant = true; return [...h, { role: 'assistant', content: piece, ts, streaming: true }]; }
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant') return [...h, { role: 'assistant', content: piece, ts, streaming: true }];
            return [...h.slice(0, -1), { ...last, content: (last.content || '') + piece }];
          });
          if (wasFirstToken) {
            setRunState((r) => {
              if (r.publicStage !== 'gm') return r;
              const rawSteps = (Array.isArray(r.rawSteps) ? r.rawSteps : []).map((s) =>
                s && s.phase === 'main_gm' ? { ...s, message: t('game.console.run.gm_generating') } : s
              );
              // task 64: 首 token 后 label 保持"生成中"(与 stage label 一致)
              return { ...r, label: t('game.console.stage.generating'), rawSteps };
            });
          }
        },
        on_agent: (data) => {
          if (!isCurrentRun()) return;
          resetInactivityTimer();
          logEvent('agent', data);
          if (!data || !data.phase) return;
          const mapped = mapAgentPhase(data.phase);
          setRunState((r) => {
            const rawSteps = Array.isArray(r.rawSteps) ? r.rawSteps.slice() : [];
            const idx = rawSteps.findIndex((s) => s.phase === data.phase);
            // task 141: main_gm 刚触发(第一个 token 还没来)时,显示"思考中"
            // 而不是后端发的"读取上下文并生成正文" — thinking 模型这段静默期可能 5-20s
            let msg = data.message || (idx >= 0 ? rawSteps[idx].message : data.phase);
            if (data.phase === 'main_gm' && !openedAssistant && data.status !== 'done') {
              msg = t('game.console.stage.generating');  // task 64: 精简文案
            }
            const merged = { phase: data.phase, message: msg, status: data.status || 'running', elapsed_ms: data.elapsed_ms ?? (idx >= 0 ? rawSteps[idx].elapsed_ms : 0), detail: data.detail || (idx >= 0 ? rawSteps[idx].detail : undefined) };
            if (idx >= 0) rawSteps[idx] = { ...rawSteps[idx], ...merged }; else rawSteps.push(merged);
            const nextStageId = advancePublicStage(r.publicStage, mapped);
            const nextLabel = (nextStageId && PUBLIC_STAGES[nextStageId]) ? PUBLIC_STAGES[nextStageId].label : r.label;
            if (data.status === 'stopped') return { ...r, rawSteps, publicStage: null, label: t('game.console.run.stopped'), detail: '' };
            return { ...r, rawSteps, publicStage: nextStageId, label: nextLabel, detail: '' };
          });
        },
        on_rewind: (data) => {
          if (!isCurrentRun()) return;
          // 反馈#42: 重写型 /set —— 后端已回滚上一轮并将以"上一轮的原始输入"在纠正后的状态下
          // 重演本轮。前端同步抹掉乐观插入的 /set 气泡 + 被回滚的那一轮(assistant+user),换上
          // 重演用的原始输入气泡,随后到来的 GM token 会开一条新的 assistant 气泡。
          resetInactivityTimer();
          logEvent('rewind', data);
          const replayUser = (data && data.replay_user) || '';
          setHistory((h) => {
            const arr = h.slice();
            if (arr.length && arr[arr.length - 1].role === 'user') arr.pop();        // 乐观的 /set 气泡
            if (arr.length && arr[arr.length - 1].role === 'assistant') arr.pop();   // 被回滚轮的 GM 回复
            if (arr.length && arr[arr.length - 1].role === 'user') arr.pop();        // 被回滚轮的玩家输入
            if (replayUser) arr.push({ role: 'user', content: replayUser, ts });
            return arr;
          });
          openedAssistant = false;
          window.__apiToast?.(t('game.console.run.rewound_toast'), { kind: 'info', duration: 2500 });
        },
        on_updates: (data) => {
          if (!isCurrentRun()) return;
          logEvent('updates', data);
          const stage = data && data.stage;
          // #13 沉浸感: /set 等 directive 确认(pre_llm)是确定性回执,以 toast 呈现,
          // 不进主聊天 transcript(原本被直接丢弃,现给 /set"修改世界观"一个确认通道)。
          if (stage === 'pre_llm') {
            const items = (data && Array.isArray(data.items)) ? data.items : [];
            if (items.length) window.__apiToast?.(t('game.console.run.settings_updated', { count: items.length }), { kind: 'ok', detail: items.join('\n'), duration: 4000 });
            return;
          }
          if (stage === 'rules_engine') return;
          setRunState((r) => {
            const nextStageId = advancePublicStage(r.publicStage, PUBLIC_STAGES.save);
            return { ...r, publicStage: nextStageId, label: PUBLIC_STAGES[nextStageId].label, detail: '' };
          });
        },
        on_system_receipt: (data) => {
          if (!isCurrentRun()) return;
          // #13 沉浸感: 斜杠命令(/time /loc /rel /var 等)的确定性回执 → toast,不进
          // 主叙事 transcript。gotReceipt 防止 on_done 把本轮误判为"空回复"恢复草稿。
          resetInactivityTimer();
          gotReceipt = true;
          const text = (data && data.text) || '';
          logEvent('system_receipt', { changed: !!(data && data.changed), len: text.length });
          if (!text) return;
          const firstLine = text.replace(/```[a-z]*\n?/gi, '').replace(/```/g, '').trim().split('\n')[0] || t('game.console.run.updated');
          window.__apiToast?.(firstLine, {
            kind: (data && data.changed) ? 'ok' : 'info',
            detail: text.trim().length > firstLine.length ? text.trim() : undefined,
            duration: (data && data.changed) ? 3500 : 7000,
          });
        },
        on_cliche_notice: (data) => {
          if (!isCurrentRun()) return;
          // 反馈#22: 后端检测到套路比喻 → 复用 ConfirmStrip(GM 询问窗口)提示,按钮复用 onRetry
          logEvent('cliche_notice', data);
          if (data && Array.isArray(data.phrases) && data.phrases.length) setClicheNotice(data);
        },
        on_usage: (data) => {
          if (!isCurrentRun()) return;
          // #11: 后端在 done 前发独立 usage 事件(input/output/cached/reasoning tokens
          // + context 占用 + cost_usd),存起来给输入框下方 footer 显示。
          logEvent('usage', data);
          if (data) setLastUsage(data);
        },
        on_done: (data) => {
          if (!isCurrentRun()) return;
          gotDone = true;
          if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
          if (data && data.usage) setLastUsage(data.usage);  // #11: 兜底(若无独立 usage 事件)
          logEvent('done', { status: !!data && data.status ? 'ok' : 'noop', interrupted: data && data.interrupted, usage: data && data.usage });
          clearInterval(tickerId);
          if (!openedAssistant && !gotReceipt) {
            if (runRef.current.doneTimer) { clearTimeout(runRef.current.doneTimer); runRef.current.doneTimer = null; }
            const _interrupted = !!(data && data.interrupted);
            // 「要刷新才出响应」(线上反馈):本轮没收到任何增量 token(provider 非增量流式 / SSE 丢事件),
            // 但 GM 回复其实已生成并落库 → 旧逻辑直接判「空回复」,逼用户手动刷新才看到已存的回复。
            // 先回查一次最新存档(与开场流 on_done 同款 setTimeout(async) 兜底),若 history 真长出本轮
            // assistant 回复就直接渲染;确为空/被中断才提示。回查失败/无新轮 → 维持原空回复提示。
            setTimeout(async () => {
              if (!isCurrentRun()) return;
              if (!_interrupted) {
                try {
                  const d2 = await window.api.game.state();
                  const hist = (d2 && Array.isArray(d2.history)) ? d2.history : null;
                  const lastSrv = (hist && hist.length) ? hist[hist.length - 1] : null;
                  if (lastSrv && lastSrv.role === 'assistant' && String(lastSrv.content || '').trim()) {
                    setHistory(hist);
                    setRunState((r) => ({ ...r, running: false, publicStage: null, label: '', detail: '', completedAt: Date.now() }));
                    runRef.current.sse = null;
                    return;  // 已从存档兜底出本轮回复 → 不再误报空回复
                  }
                } catch (_) { /* 回查失败 → 落到下面的空回复提示 */ }
              }
              restoreFailedDraft();
              const msg = _interrupted ? t('game.console.run.interrupted_msg') : t('game.console.run.empty_reply_msg');
              setRunState((r) => ({
                ...r,
                running: false,
                label: _interrupted ? t('game.console.run.interrupted') : t('game.console.run.empty_reply'),
                detail: msg,
                publicStage: null,
                completedAt: 0,
              }));
              setHasError(msg);
              window.__apiToast?.(_interrupted ? t('game.console.run.generation_interrupted') : t('game.console.run.empty_reply'), {
                kind: 'warn',
                detail: msg,
                duration: 5000,
              });
              runRef.current.sse = null;
            }, 0);
            return;
          }
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
          // 若 on_done payload 包含服务端最终 history,直接用服务端版本(已是净内容),
          // 不必在前端再 stripOps 并写 streaming_done — 否则 React 18 批处理中两次
          // setHistory 并发,最后一次(payload.history)覆盖前一次但前一次的 streaming_done 丢失。
          // 只在 payload 无 history 时才回退到前端 strip+标记。
          const _payloadForStrip = (data && data.status) || null;
          if (!(_payloadForStrip && Array.isArray(_payloadForStrip.history))) {
            setHistory((h) => {
              const last = h[h.length - 1];
              if (!last || last.role !== 'assistant') return h;
              const cleaned = stripOps(last.content || '');
              return [...h.slice(0, -1), { ...last, content: cleaned, streaming: false, streaming_done: true }];
            });
          }
          setRunState((r) => {
            // 修: rawSteps 里所有 status='running' 的步骤 mark 'done' — 否则最后那步
            // ("主 GM 正在读取上下文并生成正文")永远是 pulse 状态,看起来像卡死。
            const rawSteps = (Array.isArray(r.rawSteps) ? r.rawSteps : []).map((s) =>
              s && s.status === 'running' ? { ...s, status: 'done' } : s
            );
            return { ...r, running: false, label: t('game.console.run.turn_complete'), detail: '',
                     completedAt: Date.now(), completedElapsed: r.totalElapsed,
                     rawSteps };
          });
          if (runRef.current.doneTimer) clearTimeout(runRef.current.doneTimer);
          runRef.current.doneTimer = setTimeout(() => {
            runRef.current.doneTimer = null;
            // 1.8s 后清 publicStage/completedAt/label 让顶部 thinking pill 消失。
            // **保留 rawSteps**:LeftRail RunStateSection 默认折叠,但用户点击
            // "空闲·等待玩家"行可展开看上一轮详情。下一轮 setRunState 才会
            // 重新填 rawSteps。
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
            setActiveSave({ id: payload.save_id, title: payload.save_title || t('game.console.save.untitled', { id: payload.save_id }), updated_at: payload.save_updated_at || '' });
          }
          runRef.current.sse = null;
          setPickedCommand(null);
        },
        on_error: (data) => {
          if (!isCurrentRun()) return;
          streamFailed = true;
          logEvent('error', data);
          clearInterval(tickerId);
          if (runRef.current.doneTimer) { clearTimeout(runRef.current.doneTimer); runRef.current.doneTimer = null; }
          if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
          const realMsg = (data && (data.message || data.detail || data.error)) || '';
          setRunState((r) => ({ ...r, running: false, label: t('game.console.run.generation_failed'), detail: realMsg, publicStage: null, completedAt: 0 }));
          setHasError(realMsg || true);
          window.__apiToast?.(t('game.console.run.generation_failed'), { kind: 'danger', detail: realMsg || t('game.console.run.please_retry') });
          restoreFailedDraft();
        },
        onClose: () => {
          if (!isCurrentRun()) return;
          const lastDetail = describeLastSseEvent();
          logEvent('close', { got_done: gotDone, stream_failed: streamFailed, stopped: runRef.current.stopped, last_event: lastSseEvent && lastSseEvent.kind });
          clearInterval(tickerId);
          if (runRef.current.inactivityTimer) { clearTimeout(runRef.current.inactivityTimer); runRef.current.inactivityTimer = null; }
          if (gotDone || streamFailed || runRef.current.stopped) {
            runRef.current.sse = null;
            return;
          }
          setRunState((r) => {
            if (!r.running) return r;
            const msg = t('game.console.run.stream_closed_error', { detail: lastDetail });
            setHasError(msg);
            window.__apiToast?.(t('game.console.run.generation_interrupted'), { kind: 'warn', detail: lastDetail, duration: 5000 });
            restoreFailedDraft();
            return { ...r, running: false, label: t('game.console.run.interrupted'), detail: lastDetail, publicStage: null, completedAt: 0 };
          });
          setHistory((h) => {
            const last = h[h.length - 1];
            if (!last || last.role !== 'assistant' || !last.streaming) return h;
            return [...h.slice(0, -1), { ...last, streaming: false, streaming_done: true }];
          });
        },
      }
    );
  }, [attachments, model, pickedCommand, activeSave, tabConflictBanner]);

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
    setClicheNotice(null);  // 反馈#22: 新一轮清掉上轮的套路比喻提示
    const tickerId = setInterval(() => {
      if (runRef.current.stopped) { clearInterval(tickerId); return; }
      setRunState((r) => ({ ...r, totalElapsed: Date.now() - startedAt }));
    }, 200);
    runRef.current.timers.push(tickerId);
    const runStep = (i) => {
      if (runRef.current.stopped) return;
      if (i >= MOCK_PHASES.length) {
        clearInterval(tickerId);
        setRunState((r) => ({ ...r, running: false, label: t('game.console.run.turn_complete'), detail: '', completedAt: Date.now(), completedElapsed: Date.now() - startedAt }));
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
    // 选了斜杠命令但没填文字时,也允许直接发送(发命令 trigger)。来自 PR #14。
    if (!text.trim() && !attachments.length && !pickedCommand) return;
    if (runState.running) return;
    setHasError(false);
    startRun(text.trim() || (pickedCommand ? pickedCommand.trigger.trim() : t('game.console.send.attachment_only')));
  };
  const onSendRaw = useCallback((raw) => {
    if (runState.running) return;
    const t2 = (raw || '').trim();
    if (!t2) return;
    setHasError(false);
    startRun(t2);
  }, [runState.running, startRun]);
  const onStop = () => stopRun();
  const onRetry = useCallback(async () => {
    if (runState.running) {
      window.__apiToast?.(t('game.console.retry.still_running'), { kind: 'warn', duration: 2400 });
      return;
    }
    // 「重试」语义 = 删除本轮输出(回滚到本轮玩家输入之前)+ 再次发送本轮输入。
    // 找本轮玩家输入及其历史下标:优先 lastPlayerText,空时从历史回捞最后一条非空玩家输入。
    const h = Array.isArray(history) ? history : [];
    let t2 = (lastPlayerText && lastPlayerText.trim()) || '';
    let pIdx = -1;
    for (let i = h.length - 1; i >= 0; i--) {
      if (h[i] && h[i].role === 'user' && (h[i].content || '').trim()) {
        pIdx = i;
        if (!t2) t2 = h[i].content.trim();
        break;
      }
    }
    if (!t2) { window.__apiToast?.(t('game.console.retry.no_input'), { kind: 'warn', duration: 2000 }); return; }
    setHasError(false);
    // 之前只截断【前端】历史、不动后端 → 重发会叠在旧本轮之上(玩家报「没有删除」)。
    // 改:先调 rollback 把本轮(玩家输入及之后)从后端删除并回退活跃指针(旧回合进 trash ref 可恢复),
    // reloadState 后历史截到本轮之前,再 startRun 重走 → 真正实现「删除本轮 + 重发」。
    const saveId = activeSave?.id ?? null;
    let rolled = false;
    if (saveId != null && pIdx >= 0) {
      try {
        const r = await window.api.branches.rollbackToMessage(saveId, pIdx);
        if (r && r.ok === false) throw new Error(r.error || r.detail || 'rollback denied');
        await reloadState();
        rolled = true;
      } catch (e) {
        // 本轮可能尚未提交(纯生成失败,后端无本轮 commit)→ 退化为前端截断,不打断重试。
        rolled = false;
      }
    }
    if (!rolled) {
      setHistory((hh) => {
        const out = [...hh];
        while (out.length && out[out.length - 1].role === 'assistant' && !(out[out.length - 1].content || '').trim()) out.pop();
        if (out.length && out[out.length - 1].role === 'user' && (out[out.length - 1].content || '').trim() === t2) out.pop();
        return out;
      });
    }
    window.__apiToast?.(t('game.console.retry.retrying'), { kind: 'info', duration: 1600 });
    startRun(t2);
  }, [lastPlayerText, history, runState.running, startRun, activeSave, reloadState]);
  // 反馈:每条消息的「重新生成这一轮」(MsgActions 派发 rpg-regenerate 事件,携 message_index)。
  // 语义 = 删除本轮 + 用同一条玩家输入重走 → 换个结果替换旧的(非分叉保留)。故用 rollback(删除,
  // 旧回合进 trash ref 可恢复)而非 continueFrom(那是「建立分支」,保留旧线、玩家反馈「没有删除」)。
  // reloadState 把历史截到本轮前 → 用同一条玩家输入 startRun 重走完整 GM 流程。
  const onRegenerate = useCallback(async (messageIndex) => {
    if (runState.running) { window.__apiToast?.(t('game.console.retry.still_generating'), { kind: 'warn', duration: 2000 }); return; }
    const h = Array.isArray(history) ? history : [];
    let pIdx = Number(messageIndex);
    if (!Number.isFinite(pIdx)) pIdx = h.length - 1;
    pIdx = Math.min(pIdx, h.length - 1);
    // 从本条往前找这一轮的玩家输入(role==='user')
    while (pIdx >= 0 && h[pIdx]?.role !== 'user') pIdx--;
    if (pIdx < 0) { window.__apiToast?.(t('game.console.regen.no_player_input'), { kind: 'warn', duration: 2400 }); return; }
    const playerText = String(h[pIdx]?.content || '').trim();
    if (!playerText) { window.__apiToast?.(t('game.console.regen.empty_input'), { kind: 'warn', duration: 2400 }); return; }
    const saveId = activeSave?.id ?? null;
    if (saveId == null) { window.__apiToast?.(t('game.console.regen.no_save_context'), { kind: 'warn', duration: 2400 }); return; }
    try {
      setHasError(false);
      const r = await window.api.branches.rollbackToMessage(saveId, pIdx);  // 删除本轮(玩家输入及之后),回退活跃指针
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('game.console.regen.branch_rejected'));
      await reloadState();                 // 历史截到本轮之前(本轮玩家输入及之后被移除)
      window.__apiToast?.(t('game.console.regen.regenerating'), { kind: 'ok', duration: 1800 });
      startRun(playerText);                // 用同样的玩家输入重走 GM(完整后端流程)
    } catch (e) {
      window.__apiToast?.(t('game.console.regen.failed'), { kind: 'danger', detail: e?.message, duration: 3000 });
    }
  }, [history, runState.running, activeSave, reloadState, startRun, t]);
  useEffect(() => {
    const onRegen = (e) => onRegenerate(e?.detail?.message_index);
    window.addEventListener('rpg-regenerate', onRegen);
    return () => window.removeEventListener('rpg-regenerate', onRegen);
  }, [onRegenerate]);
  const onShowSse = useCallback(() => setSseLogOpen(true), []);

  const onSlashPick = (cmd) => {
    if (cmd && typeof cmd.trigger === 'string' && cmd.trigger.endsWith(' ')) {
      setText(cmd.trigger); setPickedCommand(null); setShowSlash(false); return;
    }
    setPickedCommand(cmd); setText(''); setShowSlash(false);
  };
  // 真实文件上传(对齐酒馆):file/image/card 走文件选择器读成 data_url 真实附件 →
  // 后端 _save_attachments 取前 6000 字节文本注入 GM,GM 即可读到附件内容(反馈:游戏附件
  // 此前是假占位,GM 读不到)。其余(chapter/world/mcp/skill/plan)是游戏内引用,保留占位。
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
    const fixtures = { chapter: { name: '第 314 章 · 北港', kind: 'chapter' }, world: { name: '世界书 · 残页', kind: 'world' }, mcp: { name: 'MCP · 文件检索', kind: 'mcp' }, skill: { name: 'Skill · 角色一致性', kind: 'skill' }, plan: { name: '计划模式', kind: 'skill' } };
    setAttachments((a) => [...a, fixtures[item.id] || { name: item.label, kind: 'file' }]);
  };
  const onFilePicked = (e) => {
    const f = e.target && e.target.files && e.target.files[0];
    if (!f) return;
    const kind = (pendingAttachRef.current && pendingAttachRef.current.kind) || 'file';
    if (f.size > 12 * 1024 * 1024) { window.__apiToast?.(t('game.console.attach.file_too_large'), { kind: 'warn', duration: 2400 }); return; }
    const reader = new FileReader();
    reader.onload = () => setAttachments((a) => [...a, { name: f.name, type: f.type || 'application/octet-stream', data_url: String(reader.result || ''), kind }]);
    reader.readAsDataURL(f);
  };

  const _matchPending = (target) => (item, idx) => {
    if (target.id != null && item.id != null) return item.id !== target.id;
    return idx !== target.index;
  };
  const onApprove = async (target) => {
    setPendingWrites((arr) => arr.filter(_matchPending(target)));
    try { await window.api.game.pendingWrite({ id: target.id, index: target.index, action: 'approve' }); } catch (e) { window.__apiToast?.(t('game.console.write.approve_failed'), { kind: 'danger', detail: e?.message }); }
    try { const d = await window.api.game.state(); if (d && d.permissions) { setPendingWrites(d.permissions.pending_writes || []); setPendingQuestions(d.permissions.pending_questions || []); } } catch (_) {}
  };
  const onReject = async (target) => {
    setPendingWrites((arr) => arr.filter(_matchPending(target)));
    try { await window.api.game.pendingWrite({ id: target.id, index: target.index, action: 'reject' }); } catch (e) { window.__apiToast?.(t('game.console.write.reject_failed'), { kind: 'danger', detail: e?.message }); }
    try { const d = await window.api.game.state(); if (d && d.permissions) { setPendingWrites(d.permissions.pending_writes || []); setPendingQuestions(d.permissions.pending_questions || []); } } catch (_) {}
  };
  const onAnswerQuestion = async (target, choice) => {
    setPendingQuestions((arr) => arr.filter(_matchPending(target)));
    try { await window.api.game.clearQuestions({ id: target.id, index: target.index, choice }); } catch (e) { window.__apiToast?.(t('game.console.write.answer_failed'), { kind: 'danger', detail: e?.message }); }
    try { const d = await window.api.game.state(); if (d && d.permissions) { setPendingWrites(d.permissions.pending_writes || []); setPendingQuestions(d.permissions.pending_questions || []); } } catch (_) {}
    const nextAction = String(choice || '').trim();
    if (nextAction) startRun(nextAction);
  };
  const onDismissConfirm = (target) => {
    setPendingWrites((arr) => arr.filter(_matchPending(target)));
    setPendingQuestions((arr) => arr.filter(_matchPending(target)));
  };

  // ── config_card(agent:config_card)处理 ──────────────────────────
  // 共用一个清除助手:乐观移除 + 后端 clearQuestions(choice=null) + 刷新 pending。
  const _clearConfigQuestion = async (target) => {
    setPendingQuestions((arr) => arr.filter(_matchPending(target)));
    try { await window.api.game.clearQuestions({ id: target.id, index: target.index, choice: null }); } catch (_) {}
    try { const d = await window.api.game.state(); if (d && d.permissions) { setPendingWrites(d.permissions.pending_writes || []); setPendingQuestions(d.permissions.pending_questions || []); } } catch (_) {}
  };
  // mode "ask_default":持久化该能力偏好 → 清掉卡片 → startRun("用 X 生成") 让 LLM 带着偏好重调工具。
  const onConfigDefault = async (handleId, item, model) => {
    const cap = capConfig(item.capability);
    const aid = item.api_id || '';
    if (aid && model) {
      try {
        await window.api.account.preferences({
          [`${cap.prefPrefix}.api_id`]: aid,
          [`${cap.prefPrefix}.model_real_name`]: model,
        });
      } catch (e) { window.__apiToast?.(t('game.console.config.save_pref_failed'), { kind: 'danger', detail: e?.message }); }
    }
    await _clearConfigQuestion(handleId);
    startRun(`用 ${model || cap.label} 生成`);
  };
  // mode "missing_key":用户在卡片里配好后点「继续」→ 清掉卡片 → startRun("继续") 重试。
  const onConfigContinue = async (handleId, item, label) => {
    await _clearConfigQuestion(handleId);
    startRun(label || '继续');
  };
  const onConfigSettings = () => { try { window.location.hash = 'settings-models'; } catch (_) {} };
  // mode "model_not_configured"(hard):打开阻塞弹窗。
  const [hardConfigItem, setHardConfigItem] = useState(null);
  const onHardConfig = (item) => setHardConfigItem(item);
  const _hardHandleId = (item) => ({ id: (item && item.id != null) ? item.id : null, index: null });
  const onHardResolve = async (chosenModel) => {
    const item = hardConfigItem; if (!item) return;
    setHardConfigItem(null);
    await _clearConfigQuestion(_hardHandleId(item));
    startRun(`用 ${chosenModel || item.model || ''} 生成`);
  };
  const onHardCancel = async () => {
    const item = hardConfigItem; if (!item) { setHardConfigItem(null); return; }
    setHardConfigItem(null);
    await _clearConfigQuestion(_hardHandleId(item));
    window.__apiToast?.(t('game.console.config.cancelled'), { kind: 'info', duration: 2000 });
  };

  useEffect(() => {
    if (!window.api) return;
    window.api.game.permissions({ mode: permission }).catch(() => {});
  }, [permission]);

  const rootStyle = useMemo(() => {
    const densityMap = { compact: 0.92, normal: 1, comfy: 1.1 };
    return { '--density': densityMap[tweaks.density] || 1, '--ui-size': tweaks.uiSize + 'px', '--narrative-size': tweaks.narrativeSize + 'px' };
  }, [tweaks.density, tweaks.uiSize, tweaks.narrativeSize]);

  const [mountStage, setMountStage] = useState(0);
  useEffect(() => {
    if (mountStage >= 2) return;
    // 双 RAF 把重面板(RightPanel 等)推迟到首帧之后,避免首屏卡顿。
    // 但后台标签页(document.hidden)里浏览器会暂停 requestAnimationFrame → mountStage
    // 永远停在 0 → 整个游戏区卡在「正在初始化…」(用户在后台预加载 Game Console、
    // 或切走再切回前的瞬间就会看到)。加 setTimeout 兜底:RAF 不触发时也能推进;
    // 前台时 RAF(~16ms)先到并触发 re-render→cleanup 清掉这个 timeout,几乎无副作用。
    const raf = requestAnimationFrame(() => { requestAnimationFrame(() => setMountStage((s) => Math.min(2, s + 1))); });
    const fallback = setTimeout(() => setMountStage((s) => Math.min(2, s + 1)), 200);
    return () => { cancelAnimationFrame(raf); clearTimeout(fallback); };
  }, [mountStage]);

  // ── P2 移动原生游戏台:窄屏早返。UI 全按设计稿重写(MobileGame),逻辑/数据复用本 App
  //    的全部 state + handler;设计稿砍掉的功能(历史/搜索/游戏内设置/反馈/SSE 调试)用
  //    既有 overlay 找回(它们是模态浮层,非主 UI 本体)。
  if (mobileGame && mountStage >= 1) {
    return (
      <>
        <MobileGame
          game={game} history={
            (stateLoadedNoPlayer && history.length === 0)
              ? [{ role: 'assistant', content: t('game.console.opening.waiting'), _opening: true, _thinking: 'starting' }] : history
          }
          runState={runState} text={text} setText={setText}
          onSend={onSend} onSendRaw={onSendRaw} onStop={onStop}
          attachments={attachments} setAttachments={setAttachments}
          model={model} setModel={setModel}
          permission={permission} setPermission={setPermission}
          pendingWrites={pendingWrites} pendingQuestions={pendingQuestions}
          onApprove={onApprove} onReject={onReject} onAnswerQuestion={onAnswerQuestion} onDismissConfirm={onDismissConfirm}
          onSlashPick={onSlashPick} onAttachPick={onAttachPick} pickedCommand={pickedCommand} onClearCommand={() => setPickedCommand(null)}
          onRetry={onRetry} onRegenerate={onRegenerate} onShowSse={onShowSse}
          clicheNotice={clicheNotice} onRetryCliche={() => { setClicheNotice(null); onRetry(); }} onDismissCliche={() => setClicheNotice(null)}
          hasError={hasError}
          activeSave={activeSave} realSaves={realSaves.length ? realSaves : ((window.RPG_AUTH && window.RPG_AUTH.authed) ? [] : (window.MOCK_PLATFORM?.saves || []))}
          onSwitchSave={async (sid) => { try { if (runRef.current.sse || runState.running) stopRun(); await window.api.saves.activate(sid); reloadState(); } catch (e) { window.__apiToast?.(t('game.console.save.switch_failed'), { kind: 'danger', detail: e?.message }); } }}
          onNew={() => { if (!confirm(t('game.console.save.new_confirm'))) return; location.href = '/saves'; }}
          onSave={async () => { try { await window.api.game.saveGame(); window.__apiToast?.(t('common.save') + ' ✓', { kind: 'ok' }); } catch (e) { window.__apiToast?.(t('game.console.save.save_failed'), { kind: 'danger', detail: e?.message }); } }}
          onMemoryMode={async (mode) => { setGame((g) => ({ ...g, memory: { ...(g.memory || {}), mode } })); try { await window.api.game.memoryMode(mode); } catch (_) {} }}
          reloadState={reloadState}
          activeTab={activeTab} setActiveTab={setActiveTab}
          lastUsage={lastUsage}
          tabConflictBanner={tabConflictBanner} setTabConflictBanner={setTabConflictBanner}
          onExit={() => { try { location.href = new URL('Platform.html', location.href).href; } catch (_) { location.href = 'Platform.html'; } }}
          onOpenHistory={() => setShowHistoryDrawer(true)}
          onOpenSearch={() => setShowSearchDrawer(true)}
          onOpenSettings={() => setShowInGameSettings(true)}
          onOpenFeedback={() => { try { window.__openFeedback?.(); } catch (_) {} }}
        />
        {/* 找回的模态浮层(非主 UI 本体) */}
        <GameSettingsModal open={showInGameSettings} onClose={() => setShowInGameSettings(false)} saveTitle={activeSave?.title || game?._raw?.save_title || ''} permission={permission} saveId={activeSave?.id ?? null} />
        <HistoryDrawer open={showHistoryDrawer} history={history} onClose={() => setShowHistoryDrawer(false)} />
        <SearchDrawer open={showSearchDrawer} history={history} state={game} onClose={() => setShowSearchDrawer(false)} />
        <GCWelcomeModal open={welcomeGCOpen} onClose={() => setWelcomeGCOpen(false)} />
        {splashNeeded && <AdultSplash splashVersion={SPLASH_VERSION} onAcked={() => setSplashNeeded(false)} />}
        {/* 移动早返同样需要 toast 渲染落点:MobileGame 全部 window.__apiToast 指向 game 通道,
            但桌面 return 的 <GameToastStack/> 早返永不到达 → 切档/保存/分支/回滚反馈静默。
            早返条件已是 mountStage>=1,直接挂(不复制桌面 mountStage>=2 守卫)。总线已 install,
            这里只补渲染器,working 总线零变化。 */}
        <GameToastStack />
        {/* 移动早返的真实文件上传 input(与桌面同 ref/handler;onAttachPick 触发)→ GM 读文本附件 */}
        <input ref={fileInputRef} type="file" style={{ display: 'none' }} onChange={onFilePicked} />
        {sseLogOpen && (
          <div className="gc-overlay" style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 9999, display: 'flex', alignItems: 'flex-end' }} onClick={() => setSseLogOpen(false)}>
            <div onClick={(e) => e.stopPropagation()} style={{ width: '100%', maxHeight: '80vh', background: 'var(--panel,#211f1d)', color: 'var(--text)', borderRadius: '14px 14px 0 0', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid var(--line-soft)' }}>
                <strong>{t('game.console.sse_log.title', { count: sseLog.length })}</strong>
                <button className="btn ghost" onClick={() => setSseLogOpen(false)}>{t('common.close')}</button>
              </div>
              <div style={{ overflow: 'auto', padding: '8px 14px', fontFamily: 'var(--font-mono)', fontSize: 11.5, lineHeight: 1.5 }}>
                {sseLog.length === 0 ? <div style={{ padding: '20px 0', color: 'var(--muted)' }}>{t('game.console.sse_log.empty')}</div> : sseLog.map((ev, i) => (
                  <div key={i} style={{ padding: '3px 0', borderBottom: '1px dashed var(--line-soft)' }}>
                    <span style={{ color: 'var(--accent)' }}>{ev.kind}</span> <span style={{ wordBreak: 'break-all' }}>{JSON.stringify(ev.payload)}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="gc-shell" style={{ ...rootStyle, '--gc-rail-w': gcRailW + 'px' }}>
      {/* A2: 多 tab 冲突 banner */}
      {tabConflictBanner && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, zIndex: 10000,
          background: 'rgba(200,130,0,0.93)', color: '#fff',
          padding: '8px 16px', display: 'flex', alignItems: 'center', gap: 12,
          fontSize: 13, boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
        }}>
          <span style={{ flex: 1 }}>
            {t('game.console.conflict.banner_text')}
          </span>
          <button
            style={{ padding: '4px 12px', borderRadius: 4, border: '1px solid rgba(255,255,255,0.5)', background: 'transparent', color: '#fff', cursor: 'pointer', fontSize: 12 }}
            onClick={() => setTabConflictBanner(null)}
          >{t('game.console.conflict.continue_btn')}</button>
          <button
            style={{ padding: '4px 12px', borderRadius: 4, border: '1px solid rgba(255,255,255,0.5)', background: 'rgba(0,0,0,0.2)', color: '#fff', cursor: 'pointer', fontSize: 12 }}
            onClick={() => window.close()}
          >{t('game.console.conflict.close_window_btn')}</button>
        </div>
      )}
      {splashNeeded && (
        <AdultSplash splashVersion={SPLASH_VERSION} onAcked={() => setSplashNeeded(false)} />
      )}
      <GCWelcomeModal open={welcomeGCOpen} onClose={() => setWelcomeGCOpen(false)} />
      {mountStage >= 2 && <GameToastStack />}
      {mountStage >= 1 ? <LeftRail
        resizeHandle={<div className="gc-rail-resize-handle" title={t('game.console.resize_rail_tip')} {...gcRailDragProps} />}
        collapsed={railCollapsed}
        onToggle={() => { setRailCollapsed((c) => !c); setMobileNav(false); }}
        state={game} runState={runState}
        onNew={() => { if (!confirm(t('game.console.save.new_confirm'))) return; window.open('/saves', '_blank'); }}
        onSave={async () => { try { await window.api.game.saveGame(); window.__apiToast?.(t('common.save') + ' ✓', { kind: 'ok' }); } catch (e) { window.__apiToast?.(t('game.console.save.save_failed'), { kind: 'danger', detail: e?.message }); } }}
        onSwitchSave={async (sid) => { setMobileNav(false); try { if (runRef.current.sse || runState.running) stopRun(); await window.api.saves.activate(sid); reloadState(); } catch (e) { window.__apiToast?.(t('game.console.save.switch_failed'), { kind: 'danger', detail: e?.message }); } }}
        onMemoryMode={async (mode) => { setGame((g) => ({ ...g, memory: { ...(g.memory || {}), mode } })); try { await window.api.game.memoryMode(mode); } catch (_) {} }}
        currentSaveId={activeSave?.id ?? null}
        saves={realSaves.length ? realSaves : ((window.RPG_AUTH && window.RPG_AUTH.authed) ? [] : (window.MOCK_PLATFORM?.saves || []))}
        mobileOpen={mobileNav}
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
          onOpenNav={() => setMobileNav(true)}
          versionSelectEl={
            (game?.app?.script_id || game?.content_pack?.script_id)
              ? <ScriptVersionSelect
                  scriptId={game.app?.script_id || game.content_pack?.script_id}
                  headCommitId={game.app?.head_commit_id || null}
                />
              : null
          }
        />}
        {mountStage >= 2 && <>
          <GameSettingsModal open={showInGameSettings} onClose={() => setShowInGameSettings(false)} saveTitle={activeSave?.title || game?._raw?.save_title || ''} permission={permission} saveId={activeSave?.id ?? null} />
          <HistoryDrawer open={showHistoryDrawer} history={history} onClose={() => setShowHistoryDrawer(false)} />
          <SearchDrawer open={showSearchDrawer} history={history} state={game} onClose={() => setShowSearchDrawer(false)} />
        </>}
        {/* config_card hard 拦截弹窗(mode model_not_configured):ConfirmStrip onHardConfig 触发 */}
        <ModelConfigInterceptModal open={!!hardConfigItem} item={hardConfigItem} onResolve={onHardResolve} onCancel={onHardCancel} />
        {/* GM 模型选择走 Composer 内置 ModelPopover(下拉,基于统一规范组件 AgentModelPicker)。
            旧的全屏 ModelPicker modal(dead code)已删除。 */}
        {mountStage >= 1 ? <ChatArea
          history={
            // G6: 存档已加载但 player 还未 setup,显示"等待开场..."占位而非空白
            (stateLoadedNoPlayer && history.length === 0)
              ? [{ role: 'assistant', content: t('game.console.opening.waiting'), _opening: true, _thinking: 'starting' }]
              : history
          } runState={runState} runStyle={tweaks.runStyle}
          narrativeFont={tweaks.narrativeFont} narrativeSize={tweaks.narrativeSize}
          hasError={hasError}
          saveId={(activeSave && activeSave.id) || (game && game._raw && game._raw.save_id) || null}
          onRetry={onRetry} onShowSse={onShowSse}
        /> : (
          retryFailed ? (
            <div className="gc-chat" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
              <div style={{ textAlign: 'center', color: 'var(--muted)', lineHeight: 2 }}>
                <div style={{ marginBottom: 8 }}>{t('game.console.load.failed_msg')}</div>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>
                  <button className="btn ghost" onClick={() => { setRetryFailed(false); reloadState().then(ok => { if (!ok) setRetryFailed(true); }); reloadSaves(); }}>{t('game.console.load.retry_btn')}</button>
                  <button className="btn ghost" onClick={() => { location.href = '/saves'; }}>{t('game.console.load.back_to_saves_btn')}</button>
                </div>
              </div>
            </div>
          ) : (
            <div className="gc-chat" aria-busy="true" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--muted)' }}>
              <span style={{ marginRight: 6 }}>⏳</span>{t('game.console.load.initializing')}
            </div>
          )
        )}
        <div className="gc-foot-wrap">
          <ConfirmStrip pendingWrites={pendingWrites} pendingQuestions={pendingQuestions} onApprove={onApprove} onReject={onReject} onAnswer={onAnswerQuestion} onDismiss={onDismissConfirm}
            clicheNotice={clicheNotice}
            onRetryCliche={() => { setClicheNotice(null); onRetry(); }}
            onDismissCliche={() => setClicheNotice(null)}
            onConfigDefault={onConfigDefault} onConfigContinue={onConfigContinue} onHardConfig={onHardConfig} onConfigSettings={onConfigSettings} />
          <Composer
            text={text} setText={setText} onSend={onSend} onStop={onStop} running={runState.running}
            onSendRaw={onSendRaw} permission={permission} setPermission={setPermission}
            model={model} setModel={setModel} composerMode={tweaks.composerMode}
            suggestions={game.suggestions} gameState={game}
            attachments={attachments} removeAttachment={(i) => setAttachments((a) => a.filter((_, j) => j !== i))}
            onAttachPick={onAttachPick} onSlashPick={onSlashPick}
            pickedCommand={pickedCommand} onClearCommand={() => setPickedCommand(null)}
            showSlash={showSlash} showPlus={showPlus} showModel={showModel} showPerm={showPerm}
            toggleSlash={() => { setShowSlash((s) => !s); setShowPlus(false); setShowModel(false); setShowPerm(false); }}
            togglePlus={() => { setShowPlus((s) => !s); setShowSlash(false); setShowModel(false); setShowPerm(false); }}
            toggleModel={() => { setShowModel((s) => !s); setShowSlash(false); setShowPlus(false); setShowPerm(false); }}
            togglePerm={() => { setShowPerm((s) => !s); setShowSlash(false); setShowPlus(false); setShowModel(false); }}
            saveId={(activeSave && activeSave.id) || (game && game._raw && game._raw.save_id) || null}
            imageGenKind="game"
          />
          {/* 真实文件上传(file/image/card 共用;onAttachPick 触发)→ GM 可读文本附件内容 */}
          <input ref={fileInputRef} type="file" style={{ display: 'none' }} onChange={onFilePicked} />
          {showUsage && lastUsage && (
            <div className="gc-usage-bar" style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', alignItems: 'center', padding: '3px 12px 4px', fontSize: 11, lineHeight: 1.5, color: 'var(--muted)', fontFamily: 'var(--font-mono, ui-monospace, Menlo, monospace)' }}>
              <span title={t('game.console.usage.input_tip')}>↑ {Number(lastUsage.input_tokens || 0).toLocaleString()}{lastUsage.cached_input_tokens ? ` · ${t('game.console.usage.cached')} ${Number(lastUsage.cached_input_tokens).toLocaleString()}` : ''}</span>
              <span title={t('game.console.usage.output_tip')}>↓ {Number(lastUsage.output_tokens || 0).toLocaleString()}{lastUsage.reasoning_tokens ? ` · ${t('game.console.usage.thinking')} ${Number(lastUsage.reasoning_tokens).toLocaleString()}` : ''}</span>
              {lastUsage.context_max ? <span title={t('game.console.usage.context_tip')}>{t('game.console.usage.context')} {Number(lastUsage.context_used || 0).toLocaleString()}/{Number(lastUsage.context_max).toLocaleString()} · {Math.round(lastUsage.context_pct || 0)}%</span> : null}
              {lastUsage.cost_usd ? <span title={t('game.console.usage.cost_tip')}>${Number(lastUsage.cost_usd).toFixed(4)}</span> : null}
              {lastUsage.model ? <span style={{ opacity: 0.6 }}>{lastUsage.model}</span> : null}
            </div>
          )}
        </div>
      </main>

      {sseLogOpen && (
        <div className="gc-overlay" style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setSseLogOpen(false)}>
          <div onClick={(e) => e.stopPropagation()} style={{ width: 'min(860px, 92vw)', maxHeight: '82vh', background: 'var(--surface, #1a1d22)', color: 'var(--text, #e6e6e6)', borderRadius: 8, border: '1px solid var(--line, #333)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid var(--line, #333)' }}>
              <strong>{t('game.console.sse_log.title_detailed', { count: sseLog.length })}</strong>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn ghost" onClick={async () => { try { await navigator.clipboard.writeText(JSON.stringify(sseLog, null, 2)); window.__apiToast?.(t('game.console.sse_log.copied'), { kind: 'ok', duration: 1500 }); } catch { window.__apiToast?.(t('game.console.sse_log.copy_failed'), { kind: 'danger' }); } }}>{t('game.console.sse_log.copy_json_btn')}</button>
                <button className="btn ghost" onClick={() => setSseLogOpen(false)}>{t('common.close')}</button>
              </div>
            </div>
            <div style={{ overflow: 'auto', padding: '8px 16px', fontFamily: 'var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)', fontSize: 12, lineHeight: 1.5 }}>
              {sseLog.length === 0 && <div style={{ padding: '24px 0', color: 'var(--muted, #888)' }}>{t('game.console.sse_log.empty_detailed')}</div>}
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

      {/* 移动端抽屉 backdrop：面板展开时显示，点击关闭面板 */}
      {mobileNav && (
        <div className="gc-nav-backdrop" onClick={() => setMobileNav(false)} aria-hidden="true" />
      )}
      {mountStage >= 2 && !panelCollapsed && (
        <div className="gc-panel-backdrop" onClick={() => setPanelCollapsed(true)} aria-hidden="true" />
      )}
      {mountStage >= 2 && <RightPanel state={game} activeTab={activeTab} setActiveTab={setActiveTab} sidebarWidth={gcPanelW} density={tweaks.density} collapsed={panelCollapsed} onToggle={() => setPanelCollapsed((c) => !c)} resizeHandle={<div className="gp-panel-resize-handle" title={t('game.console.resize_panel_tip')} {...gcPanelDragProps} />} />}
      <button className="gc-float-panel-btn" onClick={() => { setPanelCollapsed(false); _panelResize.setSize(320); }} title={t('game.console.open_panel_tip')}>⌖</button>
    </div>
  );
}

const __mount = () => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <ErrorBoundary>
      <App />
      {/* 反馈抽屉根节点 — 监听 window.__openFeedback 全局事件,
          游戏控制台的顶栏按钮 + Game console-assistant-navigation 都能触发 */}
      <FeedbackDrawerRoot />
      {/* 全局后台任务浮窗(导入 / 重建 / 生图,有活跃任务才显示) */}
      <GlobalTaskFloater />
    </ErrorBoundary>
  );
  // 通知 HTML splash 淡出 + 移除节点(交给 CSS transition + setTimeout)
  try {
    document.body.classList.add('rpg-mounted');
    setTimeout(() => {
      const sp = document.getElementById('rpg-game-splash');
      if (sp && sp.parentNode) sp.parentNode.removeChild(sp);
    }, 300);
  } catch (_) {}
};
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
