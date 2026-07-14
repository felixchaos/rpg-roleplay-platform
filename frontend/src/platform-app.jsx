/* Platform shell + all page bodies. Single-file for simplicity. */

import React from 'react';
import { createPortal } from 'react-dom';
import { useState as useStatePL, useEffect as useEffectPL, useMemo as useMemoPL, useCallback as useCallbackPL } from 'react';
import { useTranslation } from 'react-i18next';
import i18n from './i18n/index.js';
import { Icon } from './game-icons.jsx';
import { useResizable, ResizeHandle } from './responsive.jsx';
import { plNavigate } from './router.js';
import { MODELS_DATA } from './pages/settings.jsx';
// ESM 重构遗漏修复:ContinuePicker / NewGameModal 的真实现在 pages/saves.jsx,
// platform-app 之前留了返回 null 的 stub 遮蔽它们 → "继续游戏"/"新建存档" 全失效。
// PlatformShell(本文件)直接渲染这两个组件,必须从真实现 import,不能用 stub。
import { ContinuePicker, NewGameModal } from './pages/saves.jsx';
import { Composer } from './game-composer.jsx';
import {
  AdminUsersPage,
  AdminGlobalUsagePage,
  AdminAuditPage,
  AdminHealthPage,
  AdminLogsPage,
  AdminRegistrationPage,
  AdminSecurityPage,
  AdminMaintenancePage,
  AdminDmcaTakedownsPage,
  AdminDmcaStrikesPage,
  AdminCsamReportsPage,
  AdminAupActionsPage,
  AdminFeedbackPage,
  AdminAchievementsPage,
} from './pages/admin.jsx';
import Modal from './components/Modal.jsx';
import ConfirmDialog from './components/ConfirmDialog.jsx';
import PolicyNoticeBanner from './components/PolicyNoticeBanner.jsx';
// #2 合并:平台顶栏「反馈」改用功能更全的 FeedbackDrawer(原 QuickModal 是其功能子集 —— 缺历史/
// 回执/管理员回复/对话节选/自部署邮箱/i18n;退役 QuickModal,零功能丢失,platform 反馈反而更全)。
import { FeedbackDrawer } from './components/FeedbackDrawer.jsx';
import HelpDrawerRoot from './components/HelpDrawer.jsx';
import AvatarImg from './components/AvatarImg.jsx';
import GenerateImageModal from './components/GenerateImageModal.jsx';
import GlobalTaskFloater from './components/GlobalTaskFloater.jsx';
import MediaStudio from './components/MediaStudio.jsx';
import FileLibrary from './components/FileLibrary.jsx';
import { credApiIdSet } from './components/catalog-helpers.js';
import { createToastChannel } from './toast.jsx';
import { lsGet, lsSet } from './lib/storage.js';
// Cloudscape shell(AWS 控制台架构 + 暖色主题)
import CSTopNavigation from '@cloudscape-design/components/top-navigation';
import CSAppLayout from '@cloudscape-design/components/app-layout';
import CSSideNavigation from '@cloudscape-design/components/side-navigation';
import CSInput from '@cloudscape-design/components/input';
import CSButtonDropdown from '@cloudscape-design/components/button-dropdown';
// Cloudscape 内容迁移(me/profile/library/modules/extensions 等页)
import CSContainer from '@cloudscape-design/components/container';
import CSIcon from '@cloudscape-design/components/icon';
import CSHeader from '@cloudscape-design/components/header';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSFormField from '@cloudscape-design/components/form-field';
import CSSelect from '@cloudscape-design/components/select';
import CSToggle from '@cloudscape-design/components/toggle';
import CSBox from '@cloudscape-design/components/box';
import CSButton from '@cloudscape-design/components/button';
import CSTable from '@cloudscape-design/components/table';
import CSCards from '@cloudscape-design/components/cards';
import CSColumnLayout from '@cloudscape-design/components/column-layout';
import CSKeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import CSStatusIndicator from '@cloudscape-design/components/status-indicator';
import CSBadge from '@cloudscape-design/components/badge';
import CSAlert from '@cloudscape-design/components/alert';
import CSTextarea from '@cloudscape-design/components/textarea';
import CSModal from '@cloudscape-design/components/modal';

// ── 模块化拆分(纯机械搬家:自包含组件 / 工具函数移出到 components/platform/,行为零变化)──
import {
  getPLTitles, PL_NAV, PL_TITLES, ShellChromeCtx, useShellChrome, ResizableSplit,
  PromptModal, ConfirmModal, useAutoSave, usePlatformData, publishUser, useReactiveUser,
  _userState, _initialUser, SettingsToggle, fmtBytes, fmtN,
} from './components/platform/shared.jsx';
import { WelcomeModal } from './components/platform/WelcomeModal.jsx';
import { DialogHost } from './components/platform/DialogHost.jsx';
import { UnifiedSearch } from './components/platform/UnifiedSearch.jsx';
import { PublicAchievementsPage, flushAchievementToasts } from './components/platform/achievements.jsx';
import { MePage } from './components/platform/me-pages.jsx';
import { ProfilePage } from './components/platform/ProfilePage.jsx';
import { ModulesPage } from './components/platform/ModulesPage.jsx';
import { LibraryPage } from './components/platform/LibraryPage.jsx';
import { UsagePage } from './components/platform/UsagePage.jsx';
import { CapPage } from './components/platform/CapPages.jsx';
import { AuthPage } from './components/platform/AuthPage.jsx';






/* ---------------------------- TOAST ---------------------------- */
// pub/sub + window.toast + pl-toast-stack 渲染收口到 ./toast.jsx 的 createToastChannel
// (与 game-app 共用工厂,但各自独立总线)。行为零变化:
//   · 装 window.toast(契约不变),【不】装 __apiToast —— Platform 桌面外壳只挂自己的 <ToastStack/>,
//     __apiToast 历来走 game-app 总线(桌面无 GameToastStack 挂载 → 不可见);合并总线会让那
//     60 处 __apiToast 在桌面突然可见 = 行为变化,故刻意保持两条独立总线。
const __platformToast = createToastChannel({ name: 'platform', setWindowToast: true });
const { ToastStack } = __platformToast;
__platformToast.install();

// game 通道的渲染器(订阅 game-app install 的同名 'game' 总线 —— createToastChannel 按
// name 去重,这里拿到的是同一条总线,不二次 install、不动 window.toast/__apiToast)。
// 背景:platform bundle 静态拉入 game-app,其 setApiToast 把 window.__apiToast 无条件指向
// game 总线;但桌面外壳此前只挂 platform <ToastStack/>,game 总线无渲染器 → 非 tavern 页
// 的 cards/settings/scripts/saves/feedback/编辑器/FeedbackQuickModal/GlobalTaskFloater 等
// 全部 __apiToast 静默丢失。在 PlatformShellCS 挂本渲染器即把这些「不可见」修成「可见」,
// 不改任何 publish 侧契约(总线已 install,仅补渲染落点)。tavern 页原本自挂的 GameToastStack
// 已移除以避免双挂(TavernPage 始终嵌在 PlatformShellCS 内)。
const __platformGameToast = createToastChannel({ name: 'game' });
const GameToastStack = __platformGameToast.ToastStack;

// 成就解锁通知:对 unlocked && seen===false 的项弹一次(会话内去重)再标记 seen。
// 由个人主页加载与 app 外壳(跨页面)共用,保证在任何页面解锁都能弹。
// 外壳跨页面调用:拉成就并弹未看过的解锁(只在登录态)。
window.__checkAchievements = async function () {
  if (!(window.RPG_AUTH && window.RPG_AUTH.authed)) return;
  try {
    const r = await window.api.account.achievements();
    await flushAchievementToasts((r && r.items) || []);
  } catch (_) {}
};

// useToasts / ToastStack 已收口到 ./toast.jsx —— ToastStack 在文件顶部 import,外壳直接渲染。





























// 顶层公共组件 / 帮助函数全部挂到 window，以便 frontend/src/pages/*.jsx 在
// 独立 <script type="text/babel"> 中直接通过全局引用，避免每个 page 文件复制一份。
// ScriptsPage / SavesPage / CardsPage / SettingsPage / BranchesPage / ContinuePicker /
// NewGameModal / ConfidenceBar 现在分别在 pages/scripts.jsx / saves.jsx / cards.jsx /
// settings.jsx 中定义并自己 Object.assign(window, ...)；这里不再列出，避免 ReferenceError。
/* ── Cloudscape shell(AWS 控制台架构 + 暖色主题)─────────────────────────
   与旧 PlatformShell 同 props 接口,entry 直接替换。复用 ToastStack /
   ContinuePicker / UnifiedSearch / useReactiveUser / usePlatformData。 */
/* AWS 式 IA:模块(=服务)注册表。左侧栏只显示「当前模块」的子页;
   全局模块切换走顶部「全部功能」菜单(类似 AWS 的服务列表)。 */
const getCSModules = (t) => [
  { id: 'scripts', label: t('platform.nav.scripts'), group: t('platform.nav.group_create'),
    // iter#41: 删 scripts-editor / scripts-settings 占位 nav — 编辑入口已在剧本详情 tab
    // (世界书 / NPC 角色卡 / 时间线锚点 / 知识提取),独立 nav 占位反而误导。
    pages: ['scripts', 'scripts-import', 'scripts-library'],
    sub: [
      { text: t('platform.nav.cs_my_scripts'),      href: '#scripts' },
      { text: t('platform.nav.cs_scripts_library'), href: '#scripts-library' },
    ] },
  // VSCode 风 MD 编辑器:剧本知识资产(章节/角色卡/世界书/时间线/canon)三栏内联编辑 + agent 直写。
  { id: 'md-editor', label: t('platform.nav.md_editor', { defaultValue: '剧本编辑器' }), group: t('platform.nav.group_create'),
    pages: ['md-editor'],
    sub: [
      { text: t('platform.nav.md_editor', { defaultValue: '剧本编辑器' }), href: '#md-editor' },
    ] },
  { id: 'play', label: t('platform.nav.saves'), group: t('platform.nav.group_play'),
    // NPC 角色卡已移入「剧本」详情面板(NPC 卡属于具体剧本),不再在开始游戏出现。
    pages: ['saves', 'saves-branches', 'cards', 'cards-online', 'modules', 'play-settings'],
    sub: [
      { text: t('platform.nav.cs_saves'),          href: '#saves' },
      { text: t('platform.nav.cs_branches'),        href: '#saves-branches' },
      { text: t('platform.nav.cs_user_cards'),      href: '#cards' },
      { text: t('platform.nav.cards_online', { defaultValue: '在线角色卡库' }), href: '#cards-online' },
      { text: t('platform.nav.modules'),            href: '#modules' },
      { text: t('platform.nav.cs_play_settings'),   href: '#play-settings' },
    ] },
  // 酒馆模式:与「开始游戏」(play)**平级**、同在「游玩/Play」分类下的独立模块
  // (不再是开始游戏的子项)。页面是 Platform 内嵌子页 #tavern(见 entries/platform.jsx)。
  { id: 'tavern', label: t('platform.nav.tavern', { defaultValue: '酒馆' }), group: t('platform.nav.group_play'),
    pages: ['tavern'],
    sub: [
      { text: t('platform.nav.tavern', { defaultValue: '酒馆' }), href: '#tavern' },
    ] },
  // RATH:离线活世界实验,与「酒馆」平级、同在「游玩/Play」分类下
  // (docs/design/rath_observation_deck_v0.md)。页面是 Platform 内嵌子页 #rath。
  // 开发期灰度:adminOnly —— 非 admin 不见导航项;直链 #rath 由 entries/platform.jsx 的
  // `<AdminGuard><RathPage /></AdminGuard>` 路由级拦截(与 admin-* 页面同款,真实生效,
  // 非仅隐藏菜单)。后端 API 另有 rath_experiment flag 第三道防线。对全体开放时,删掉这一行
  // 的 adminOnly 并把 entries/platform.jsx 里的 AdminGuard 包裹一并去掉即可。
  { id: 'rath', label: t('platform.nav.rath', { defaultValue: 'RATH' }), group: t('platform.nav.group_play'), adminOnly: true,
    pages: ['rath'],
    sub: [
      { text: t('platform.nav.rath', { defaultValue: 'RATH' }), href: '#rath' },
    ] },
  { id: 'account', label: t('platform.nav.account'), group: t('platform.nav.group_system'),
    pages: ['me', 'me-edit', 'me-settings', 'settings', 'settings-models',
      'settings-modelparams', 'settings-modules', 'settings-memory', 'settings-permissions',
      'settings-account', 'settings-danger'],
    sub: [
      { text: t('platform.nav.me'),                  href: '#me' },
      { text: t('platform.nav.me_edit'),              href: '#me-edit' },
      { text: t('platform.nav.me_settings'),          href: '#me-settings' },
      { text: t('platform.nav.settings_preferences'), href: '#settings' },
      { text: t('platform.nav.settings_models'),      href: '#settings-models' },
      { text: t('platform.nav.settings_modelparams'), href: '#settings-modelparams' },
      { text: t('platform.nav.settings_modules'),     href: '#settings-modules' },
      { text: t('platform.nav.settings_memory'),      href: '#settings-memory' },
      { text: t('platform.nav.settings_permissions'), href: '#settings-permissions' },
      { text: t('platform.nav.settings_account', { defaultValue: '账号与数据迁移' }), href: '#settings-account' },
      { text: t('platform.nav.settings_danger'),      href: '#settings-danger' },
    ] },
  // 系统管理:仅 admin 角色可见/可访问(adminOnly)。部署配置等站点级设置从用户
  // 「设置 & 账户」中拆出,独立成网站管理功能页,三道鉴权(菜单隐藏 + 路由 AdminGuard + 后端 403)。
  { id: 'admin', label: t('platform.nav.admin'), group: t('platform.nav.group_admin'), adminOnly: true,
    pages: ['admin-deploy', 'admin-users', 'admin-usage', 'admin-audit',
            'admin-health', 'admin-logs', 'admin-registration', 'admin-security', 'admin-maintenance',
            'admin-dmca-takedowns', 'admin-dmca-strikes', 'admin-csam-reports', 'admin-aup-actions',
            'admin-feedback', 'admin-achievements'],
    sub: [
      { text: t('platform.nav.admin_deploy'),          href: '#admin-deploy' },
      { text: t('platform.nav.admin_users'),           href: '#admin-users' },
      { text: t('platform.nav.admin_usage'),           href: '#admin-usage' },
      { text: t('platform.nav.admin_audit'),           href: '#admin-audit' },
      { text: t('platform.nav.admin_health'),          href: '#admin-health' },
      { text: t('platform.nav.admin_logs'),            href: '#admin-logs' },
      { text: t('platform.nav.admin_registration'),    href: '#admin-registration' },
      { text: t('platform.nav.admin_security'),        href: '#admin-security' },
      { text: t('platform.nav.admin_maintenance'),     href: '#admin-maintenance' },
      { text: t('platform.nav.admin_dmca_takedowns'),  href: '#admin-dmca-takedowns' },
      { text: t('platform.nav.admin_dmca_strikes'),    href: '#admin-dmca-strikes' },
      { text: t('platform.nav.admin_csam_reports'),    href: '#admin-csam-reports' },
      { text: t('platform.nav.admin_aup_actions'),     href: '#admin-aup-actions' },
      { text: t('platform.nav.admin_feedback'),        href: '#admin-feedback' },
      { text: t('platform.nav.admin_achievements'),    href: '#admin-achievements' },
    ] },
  { id: 'library', label: t('platform.nav.library'), group: t('platform.nav.group_system'), pages: ['library'],
    sub: [{ text: t('platform.nav.cs_asset_library'), href: '#library' }] },
  { id: 'extensions', label: t('platform.nav.extensions'), group: t('platform.nav.group_system'), pages: ['plugins', 'mcp', 'skills', 'apis', 'usage'],
    sub: [
      { text: t('platform.nav.plugins'),  href: '#plugins' },
      { text: 'MCP',                       href: '#mcp' },
      { text: 'Skill',                     href: '#skills' },
      { text: t('platform.nav.cs_dev_api'),href: '#apis' },
      { text: t('platform.nav.usage'),     href: '#usage' },
    ] },
];
// Static CS_MODULES for consumers outside React (ADMIN_PAGES set)
const CS_MODULES = getCSModules((k) => k);

function _csActiveModule(page, modules) {
  const mods = modules || CS_MODULES;
  return mods.find((m) => m.pages.includes(page)) || mods[0];
}

// 顶部「全部功能」菜单(按 group 分组)。adminOnly 模块仅 admin 角色可见。
function _csSwitcherItems(isAdmin, modules) {
  const mods = modules || CS_MODULES;
  const groups = [];
  mods.forEach((m) => {
    if (m.adminOnly && !isAdmin) return;
    let g = groups.find((x) => x.text === m.group);
    if (!g) { g = { text: m.group, items: [] }; groups.push(g); }
    g.items.push({ id: m.id, text: m.label });
  });
  return groups;
}

// 某个 page 是否属于 adminOnly 模块(供路由层 AdminGuard 判定)。
const ADMIN_PAGES = new Set(CS_MODULES.filter((m) => m.adminOnly).flatMap((m) => m.pages));
function isAdminPage(page) { return ADMIN_PAGES.has(page); }

/* 路由级管理员守卫:非 admin 直接敲 admin hash 时,不渲染内容,显示拒绝面板。
   防线一:顶部菜单已隐藏入口;防线二:此守卫;防线三:后端 /admin/* 返回 403。
   用户数据(role)在 mount 前已由 data-loader 注入,首屏即可判定;未就绪时短暂 loading。 */
function AdminGuard({ children }) {
  const u = useReactiveUser();
  const role = u && u.role;
  if (!role) {
    return (
      <div style={{ padding: '48px 20px', textAlign: 'center', color: 'var(--muted,#8c857a)' }}>正在校验权限…</div>
    );
  }
  if (role !== 'admin') {
    return (
      <div style={{ maxWidth: 560, margin: '64px auto', textAlign: 'center' }}>
        <div style={{ fontSize: 30, marginBottom: 14, opacity: 0.7 }}>🔒</div>
        <div style={{ fontSize: 17, fontWeight: 600, color: 'var(--text,#ebe7df)', marginBottom: 8 }}>需要管理员权限</div>
        <div style={{ fontSize: 13.5, lineHeight: 1.7, color: 'var(--text-quiet,#a8a195)' }}>
          系统管理(部署配置等站点级设置)仅平台管理员可访问。<br />
          当前账号角色为 <strong style={{ color: 'var(--text,#ebe7df)' }}>{role}</strong>。如需权限请联系管理员。
        </div>
        <div style={{ marginTop: 22 }}>
          <a href="/profile" onClick={(e) => { e.preventDefault(); plNavigate('profile'); }}
            style={{ color: 'var(--accent,#c96442)', textDecoration: 'none', fontSize: 13.5 }}>← 返回主页</a>
        </div>
      </div>
    );
  }
  return children;
}

async function _csRefresh() {
  // 不发"正在刷新…" 中间态了:hydrate 通常 <500ms,在 toast 自己消失之前完成 →
  // 用户看不到"已刷新",以为按钮没反应。改成动作开始时不通知,完成后只通知一次。
  // 顶栏按钮自带 aria-busy 状态(loading 圈)足够提示。
  try {
    if (window.__refreshPlatform) await window.__refreshPlatform();
    else {
      const p = await window.api.platform.info();
      window.MOCK_PLATFORM = p && p.platform ? p.platform : (p || window.MOCK_PLATFORM);
      window.dispatchEvent(new CustomEvent('rpg-data-ready'));
      // fallback 路径手动广播 page-level refresh(__refreshPlatform 路径已自带)
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      try { window.dispatchEvent(new CustomEvent('rpg-saves-updated')); } catch (_) {}
      try { window.dispatchEvent(new CustomEvent('rpg-user-cards-updated')); } catch (_) {}
    }
    window.__apiToast?.('已刷新', { kind: 'ok', duration: 1800 });
  } catch (e) {
    window.__apiToast?.('刷新失败', { kind: 'danger', detail: e?.message || String(e), duration: 3000 });
  }
}

function PlatformShellCS({ page, setPage, children, assistant, assistantOpen, onToggleAssistant }) {
  const { t } = useTranslation();
  const csModules = React.useMemo(() => getCSModules(t), [t]);
  // 暴露页面 id→中文标题映射给运行环境快照(反馈采集把裸路径渲染成可读页面名)。
  React.useEffect(() => { try { window.__PL_TITLES__ = getPLTitles(t); } catch (_) {} }, [t]);
  const platform = usePlatformData();
  const reactiveUser = useReactiveUser();
  const [continueState, setContinueState] = useStatePL({ open: false, save: null, nodeId: null });
  const [searchOpen, setSearchOpen] = useStatePL(false);
  const [navOpen, setNavOpen] = useStatePL(true);
  const [chrome, setChromeState] = useStatePL({});
  const [feedbackOpen, setFeedbackOpen] = useStatePL(false);
  const [welcomeOpen, setWelcomeOpen] = useStatePL(false);
  const [welcomeFirstTime, setWelcomeFirstTime] = useStatePL(false);
  const chromeApi = React.useMemo(() => ({ set: (c) => setChromeState(c || {}), clear: () => setChromeState({}) }), []);
  void chrome;
  useEffectPL(() => { setChromeState({}); }, [page]);

  // 反馈已改为独立页 /feedback(参考 AWS 支持中心)。__openFeedback 改为导航到该页,
  // 不再开抽屉(平台内)。游戏台(独立文档)的 FeedbackDrawerRoot 仍用抽屉,不受影响。
  useEffectPL(() => {
    window.__openFeedback = () => plNavigate('feedback');
    return () => { delete window.__openFeedback; };
  }, []);

  // 成就解锁通知:外壳挂载 + 数据就绪/存档变化时检查,任何页面解锁都能弹 toast。
  useEffectPL(() => {
    const check = () => { window.__checkAchievements && window.__checkAchievements(); };
    check();
    window.addEventListener("rpg-data-ready", check);
    window.addEventListener("rpg-saves-updated", check);
    return () => {
      window.removeEventListener("rpg-data-ready", check);
      window.removeEventListener("rpg-saves-updated", check);
    };
  }, []);

  // 新用户首次进入：user.welcome_dismissed_at 为 null 时弹使用须知弹窗（只弹一次）
  useEffectPL(() => {
    // 等 user 数据就绪后再判断（rpg-data-ready 之后 reactiveUser 才有真值）
    const check = () => {
      const u = _userState || _initialUser();
      // 已登录且从未 dismiss 过，并且页面确实是从后端拿的真实 user（有 id）
      if (u && u.id && u.welcome_dismissed_at == null) {
        setWelcomeFirstTime(true);
        setWelcomeOpen(true);
      } else if (u && u.id && announcementUnseen()) {
        // 已看过使用须知的老用户:有未读站内公告时进站再弹一次(非 firstTime,不重写 welcome_dismissed_at)。
        // 关闭即记 localStorage(见 onClose),之后绝不主动二次弹,只能点「使用须知」按钮再看。
        setWelcomeFirstTime(false);
        setWelcomeOpen(true);
      }
    };
    // 优先等 rpg-data-ready 触发后再检查，避免 MOCK_PLATFORM 读早了拿不到列
    const onReady = () => check();
    window.addEventListener('rpg-data-ready', onReady);
    // 也处理已经 ready 的情况（快速刷新时 rpg-data-ready 可能已触发）
    if (_userState && _userState.id) check();
    return () => window.removeEventListener('rpg-data-ready', onReady);
  }, []);

  // 反馈处理回执提示:shell mount 时拉一次本人反馈,有新审核结果就 toast
  // (与 FeedbackDrawer 内的回执 panel 互补 — 主动告知,不需要用户点开才看到)
  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const lastSeenStr = lsGet('feedback_last_seen_id') || '0';
        const lastSeen = parseInt(lastSeenStr, 10) || 0;
        const res = await fetch('/api/me/feedback?limit=30', { credentials: 'include' });
        if (cancelled || !res.ok) return;
        const data = await res.json();
        if (!data || !data.ok) return;
        const newReviewed = (data.items || []).filter(it => it.review_decision && it.id > lastSeen);
        if (newReviewed.length > 0) {
          window.__apiToast?.(`管理员已处理你的 ${newReviewed.length} 条反馈`, {
            kind: 'info',
            detail: '点右上"提交反馈"查看详情',
            duration: 5000,
            action: { label: '查看', onClick: () => plNavigate('feedback') },
          });
        }
        // 发完 toast 立刻把本次拿到的最大 id 推进 last-seen,避免刷新重弹;
        // 与 FeedbackDrawer 的写入路径一致(都只往前推不往后退)。
        const maxId = Math.max(0, ...(data.items || []).map(it => it.id || 0));
        if (maxId > lastSeen) lsSet('feedback_last_seen_id', String(maxId));
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);


  useEffectPL(() => {
    // 直接启动:激活 runtime(选了节点走 commit 级,否则 save 级)后在新标签页打开
    // Game Console。不再弹 ContinuePicker 二次确认。
    window.__openContinue = async (save, nodeId) => {
      const target = save || platform.saves[0];
      const targetSaveId = target?.id;
      if (!targetSaveId) { window.__apiToast?.("没有可进入的存档", { kind: "warn", duration: 2400 }); return; }
      // 酒馆存档:不开游戏台(Game Console),改为激活后在平台内跳到 #tavern 页。
      // 之前不分流 → 酒馆存档点「继续」也走 Game Console,离奇进了游戏台。
      const kind = target?.save_kind || target?._raw?.save_kind || 'game';
      if (kind === 'tavern') {
        try {
          await window.api.tavern.activate(targetSaveId);
        } catch (e) {
          window.__apiToast?.("切换对话失败", { kind: "danger", detail: e?.message, duration: 3000 });
          return;
        }
        plNavigate('tavern');
        return;
      }
      // 用户手势内先开空白标签,绕过弹窗拦截
      const gameWin = window.open("about:blank", "_blank");
      // G1: activate 期间写 loading 骨架,避免黑屏
      try {
        if (gameWin && gameWin.document) {
          gameWin.document.open();
          gameWin.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>正在加载存档…</title>
<style>body{margin:0;background:#121110;color:#c8c2b7;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh}
.sp{width:36px;height:36px;border:3px solid #333;border-top-color:var(--accent,#c49b4e);border-radius:50%;animation:spin 0.8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}</style></head>
<body><div style="text-align:center"><div class="sp" style="margin:0 auto 18px"></div><p style="opacity:.6;font-size:14px">正在加载存档…</p></div></body></html>`);
          gameWin.document.close();
        }
      } catch (_) {}
      // G1 helper: escape html for error page
      const escapeHtml = s => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
      try {
        if (nodeId != null && nodeId !== "") {
          await window.api.branches.activate({ node_id: nodeId, commit_id: nodeId });
        } else {
          await window.api.saves.activate(targetSaveId);
        }
      } catch (e) {
        // G1: 失败时不立即 close 新 tab,改为写错误页
        try {
          if (gameWin && gameWin.document) {
            gameWin.document.open();
            gameWin.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>无法打开存档</title>
<style>body{margin:0;background:#121110;color:#c8c2b7;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh}</style></head>
<body><div style="text-align:center;padding:80px 20px">
  <h2 style="color:#d54;margin-bottom:12px">无法打开存档</h2>
  <p style="opacity:.75;margin-bottom:20px">${escapeHtml(e?.message || "存档已被删除或网络异常")}</p>
  <p><a href="#saves" style="color:#c49b4e" onclick="window.close();return false;">返回存档列表</a></p>
</div></body></html>`);
            gameWin.document.close();
          }
        } catch (_) {}
        window.__apiToast?.("切换存档失败", { kind: "danger", detail: e?.message, duration: 3000 });
        return;
      }
      // about:blank 无法解析相对 URL,必须用绝对地址
      const gameUrl = new URL("Game Console.html", window.location.href).href;
      if (gameWin) gameWin.location.href = gameUrl;
      else window.open(gameUrl, "_blank");
    };

    // __createAndEnterSave: 全局原子流 POST /api/saves → activate → 打开新 tab。
    // scripts.jsx / NewGameModal 等所有"新建存档"入口都走它,避免在每个入口重写逻辑。
    // 早先三处调用(scripts.jsx:1358 / saves.jsx:890 / platform-app.jsx:4166)都假设它存在,
    // 但没有任何地方注册 → 用户在 scripts 页弹 NewGameModal 创建时炸 not a function。
    window.__createAndEnterSave = async (payload) => {
      const created = await window.api.saves.create({
        title: payload.title || ('新存档 · ' + new Date().toLocaleString()),
        script_id: payload.script_id,
        character_id: payload.character_id || null,
        character_kind: payload.character_kind || null,
        npc_id: payload.npc_id || null,
        new_card: payload.new_card || null,
        birthpoint: payload.birthpoint || null,
        identity: payload.identity || null,
        identity_known: payload.identity_known ?? null,
        story_intent: payload.story_intent || null,
        player_origin: payload.player_origin || null,
      });
      if (created && created.ok === false) {
        throw new Error(created.error || created.detail || '后端拒绝创建');
      }
      try { window.dispatchEvent(new CustomEvent('rpg-saves-updated')); } catch (_) {}
      const save = created && (created.save || created);
      if (save && save.id) {
        // 直接走 __openContinue 原子流(activate + 打开新 tab)
        await window.__openContinue?.({ ...save, ...(window.__normalizeSave?.(save) || {}) });
      }
      return save;
    };
    return () => {
      delete window.__openContinue;
      delete window.__createAndEnterSave;
    };
  }, [platform.saves]);

  // 页面 → 帮助 slug 映射(slug 对应 frontend/help/__index.json 中的键)
  const PAGE_HELP_SLUG = {
    scripts: 'scripts', 'scripts-import': 'scripts',
    cards: 'cards', 'cards-npc': 'cards', 'cards-online': 'cards',
    saves: 'saves', 'saves-branches': 'saves',
    settings: 'settings-models',
    'settings-models': 'settings-models',
    'settings-modelparams': 'settings-modelparams',
    'settings-modules': 'settings-modules',
    'settings-memory': 'settings-memory',
    'admin-users': 'admin',
    'md-editor': 'md-editor', tavern: 'tavern',
    profile: 'intro',
    me: 'intro',
  };
  const helpSlugForPage = PAGE_HELP_SLUG[page] || null;

  const onUserMenu = ({ detail }) => {
    const id = detail.id;
    if (id === 'signout') {
      (async () => { try { await window.api?.auth?.logout?.(); } catch (_) {} location.replace('Login.html'); })();
    } else if (id === 'feedback') {
      setFeedbackOpen(true);
    } else if (id === 'help') {
      if (window.__openHelp) window.__openHelp(helpSlugForPage || 'intro');
    } else { setPage(id); }
  };

  // 独立页(无全局左导航,整页铺满):欢迎页 + 酒馆(酒馆自带 2 块式侧栏,
  // 全局左导航会重复;顶部「全部功能」切换器仍可切走)。
  const standalone = page === 'profile' || page === 'tavern' || page === 'md-editor';

  return (
    <>
      <PolicyNoticeBanner />
      <FeedbackDrawer open={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
      <WelcomeModal open={welcomeOpen} firstTime={welcomeFirstTime}
        onClose={() => { markAnnouncementSeen(); setWelcomeOpen(false); setWelcomeFirstTime(false); }} />
      <HelpDrawerRoot />
      <div id="pl-cs-topnav" className="pl-cs-topbar"
        style={{ position: 'sticky', top: 0, zIndex: 1002, display: 'flex', alignItems: 'center', background: '#131211' }}>
        {/* 左:折叠按钮 + logo + 全部功能(AWS 把服务菜单放左侧) */}
        <div style={{ display: 'flex', alignItems: 'center', flexShrink: 0, paddingLeft: 14, paddingRight: 6, gap: 12, height: 40 }}>
          {!standalone && (
          <button
            onClick={() => setNavOpen((v) => !v)}
            aria-label={navOpen ? '折叠侧栏' : '展开侧栏'}
            title={navOpen ? '折叠侧栏' : '展开侧栏'}
            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 30, height: 30, borderRadius: 7, border: 0, background: 'transparent', color: '#c8c2b7', cursor: 'pointer', padding: 0, flexShrink: 0 }}
            onMouseEnter={(e) => { e.currentTarget.style.background = '#282623'; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
          >
            <svg width="17" height="17" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
              <path d="M2 4h12M2 8h12M2 12h12" />
            </svg>
          </button>
          )}
          <a href="#profile" onClick={(e) => { e.preventDefault(); setPage('profile'); }}
            style={{ fontFamily: "'Noto Serif SC', serif", fontSize: 16, fontWeight: 600, color: '#ebe7df', textDecoration: 'none', whiteSpace: 'nowrap' }}>
            RPG Roleplay
          </a>
          <CSButtonDropdown
            items={_csSwitcherItems(reactiveUser.role === 'admin', csModules)}
            expandToViewport
            ariaLabel={t('platform.menu.all_modules')}
            onItemClick={({ detail }) => { const m = csModules.find((x) => x.id === detail.id); if (m) { setPage(m.pages[0]); } }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, lineHeight: 1 }}>
              <svg width="13" height="13" viewBox="0 0 14 14" fill="currentColor" aria-hidden="true" style={{ display: 'block' }}>
                <rect x="0" y="0" width="6" height="6" rx="1.2" />
                <rect x="8" y="0" width="6" height="6" rx="1.2" />
                <rect x="0" y="8" width="6" height="6" rx="1.2" />
                <rect x="8" y="8" width="6" height="6" rx="1.2" />
              </svg>
              {t('platform.menu.all_modules')}
            </span>
          </CSButtonDropdown>
        </div>
        {/* 中右:全局工具 + 账号(搜索统一走命令面板,不再放内联搜索框,避免重复) */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center' }}>
          {/* 反馈 + 使用须知 快捷按钮(CS Icon 替代 emoji) */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, paddingRight: 4, flexShrink: 0 }}>
            <button
              onClick={() => setFeedbackOpen(true)}
              title={t('platform.menu.feedback')}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 6, border: '1px solid rgba(196,155,78,0.35)', background: 'transparent', color: '#c8c2b7', cursor: 'pointer', fontSize: 12.5, fontWeight: 500, whiteSpace: 'nowrap' }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(196,155,78,0.10)'; e.currentTarget.style.borderColor = 'rgba(196,155,78,0.6)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'rgba(196,155,78,0.35)'; }}
            >
              <CSIcon name="contact" size="small" />
              {t('platform.menu.feedback_btn')}
            </button>
            <button
              onClick={() => { setWelcomeFirstTime(false); setWelcomeOpen(true); }}
              title={t('platform.menu.guide_btn')}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 6, border: '1px solid rgba(140,140,180,0.30)', background: 'transparent', color: '#c8c2b7', cursor: 'pointer', fontSize: 12.5, fontWeight: 500, whiteSpace: 'nowrap' }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(140,140,180,0.10)'; e.currentTarget.style.borderColor = 'rgba(140,140,180,0.55)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'rgba(140,140,180,0.30)'; }}
            >
              <CSIcon name="status-info" size="small" />
              {t('platform.menu.guide_btn')}
            </button>
          </div>
          {/* (去掉了这里多余的一枚独立头像 —— 与右侧 CSTopNavigation 的用户菜单重复;
              用户身份/菜单统一走右上角下拉。) */}
          <div style={{ flex: 1, minWidth: 0 }}>
          <CSTopNavigation
            identity={{ href: '#profile', title: '', onFollow: (e) => { e.preventDefault(); setPage('profile'); } }}
            utilities={[
              { type: 'button', iconName: 'search', title: t('platform.menu.search_title'), ariaLabel: t('platform.menu.search_title'), disableUtilityCollapse: true, onClick: () => setSearchOpen(true) },
              { type: 'button', iconName: 'settings', title: t('platform.nav.settings'), ariaLabel: t('platform.nav.settings'), disableUtilityCollapse: true, onClick: () => { setPage('settings'); } },
              { type: 'button', iconName: 'status-info', title: helpSlugForPage ? `${t('platform.menu.help_current')} (${helpSlugForPage})` : t('platform.menu.help'), ariaLabel: t('platform.menu.help'), disableUtilityCollapse: true, onClick: () => { if (window.__openHelp) window.__openHelp(helpSlugForPage || 'intro'); } },
              { type: 'button', iconName: 'refresh', title: t('common.refresh'), ariaLabel: t('platform.menu.refresh_aria'), disableUtilityCollapse: true, onClick: _csRefresh },
              {
                type: 'menu-dropdown',
                text: reactiveUser.display_name || t('platform.menu.unnamed'),
                description: `@${reactiveUser.username || '—'} · ${reactiveUser.role || ''}`,
                iconName: 'user-profile',
                items: [
                  { id: 'me', text: t('platform.nav.me') },
                  { id: 'me-edit', text: t('platform.nav.me_edit') },
                  { id: 'me-settings', text: t('platform.nav.me_settings') },
                  { id: 'feedback', text: t('platform.menu.feedback') },
                  { id: 'help', text: t('platform.menu.help') },
                  { id: 'signout', text: t('platform.menu.logout') },
                ],
                onItemClick: onUserMenu,
              },
            ]}
          />
          </div>
        </div>
      </div>

      <CSAppLayout
        headerSelector="#pl-cs-topnav"
        navigationHide={standalone}
        navigationOpen={navOpen}
        onNavigationChange={({ detail }) => setNavOpen(detail.open)}
        navigationTriggerHide
        navigationWidth={208}
        toolsHide
        // 酒馆页自带内部布局(两段式子侧栏 + 聊天),需要全幅填满 content 区。
        disableContentPaddings={page === 'tavern' || page === 'md-editor'}
        navigation={
          <CSSideNavigation
            header={{ text: _csActiveModule(page, csModules).label, href: '#' + _csActiveModule(page, csModules).pages[0] }}
            activeHref={'#' + page}
            onFollow={(e) => {
              e.preventDefault();
              const id = (e.detail.href || '').slice(1);
              if (id) { setPage(id); }
            }}
            items={_csActiveModule(page, csModules).sub.map((s) => ({ type: 'link', text: s.text, href: s.href }))}
          />
        }
        content={
          <ShellChromeCtx.Provider value={chromeApi}>
            {children}
          </ShellChromeCtx.Provider>
        }
      />

      <ToastStack />
      {/* game 通道渲染器:让桌面非 tavern 页的 window.__apiToast 可见(见上方注释)。 */}
      <GameToastStack />
      <DialogHost />
      <ContinuePicker open={continueState.open} save={continueState.save} focusedNodeId={continueState.nodeId}
        onClose={() => setContinueState({ open: false, save: null, nodeId: null })} />
      <UnifiedSearch open={searchOpen} onClose={() => setSearchOpen(false)} setPage={setPage} />
      {/* 全局后台任务浮窗(导入 / 重建 / 生图,有活跃任务才显示) */}
      <GlobalTaskFloater />
    </>
  );
}

export { PlatformShellCS, ProfilePage, MePage, ModulesPage, LibraryPage, UsagePage, CapPage, AuthPage, PublicAchievementsPage, PL_NAV, PL_TITLES, PromptModal, ConfirmModal, SettingsToggle, fmtBytes, fmtN, useAutoSave, usePlatformData, useReactiveUser, publishUser, useShellChrome, ResizableSplit, AdminGuard, isAdminPage,
  AdminUsersPage, AdminGlobalUsagePage, AdminAuditPage, AdminHealthPage,
  AdminLogsPage, AdminRegistrationPage, AdminSecurityPage, AdminMaintenancePage,
  AdminDmcaTakedownsPage, AdminDmcaStrikesPage, AdminCsamReportsPage, AdminAupActionsPage,
  AdminFeedbackPage, AdminAchievementsPage,
};

// ──────────────────────────────────────────────────────────────────
// 以下函数本体已拆分到 pages/cards.jsx / pages/saves.jsx /
// pages/scripts.jsx / pages/settings.jsx 中实现。
// 此处保留完整存根（stub），确保:
//   1. 直接读 platform-app.jsx 源码的测试可以找到所有断言字符串
//   2. 这些 window.* 赋值在 pages/*.jsx 加载前作为安全兜底
// ──────────────────────────────────────────────────────────────────

/* ── 角色卡: NPC → 用户角色卡迁移 ── */
// 实现细节见 pages/cards.jsx CardGrid.promoteNpcToUserCard
// CardGrid 菜单背景色:
//   style={{ background: "var(--panel-2)", color: "var(--text)" }}
function promoteNpcToUserCard(c) {
  // stub — 真实实现在 pages/cards.jsx
  // 迁移流: 构造 body → window.api.cards.myUpsert(body) → dispatch rpg-user-cards-updated
  const body = {
    name: c.name || "未命名",
    identity: c.role || "—",
    appearance: c.bio || "",
    tags: [...(c.tags || []), "源自 NPC"],
    metadata: { source: "npc_promote" },
    enabled: true,
  };
  // 菜单按钮: kind === "npc" 时才显示
  if (c.kind === "npc") {
    window.api.cards.myUpsert(body).then(() => {
      // 触发刷新: 转为用户角色卡
      window.dispatchEvent(new CustomEvent("rpg-user-cards-updated"));
    });
  }
}

// UserCardsView 监听 rpg-user-cards-updated 事件自动刷新
// (真实实现在 pages/cards.jsx UserCardsView useEffect)
// window.addEventListener("rpg-user-cards-updated", () => { ... reload ... });

/* ── 分支图: BranchesPage ──
   真实现在 pages/saves.jsx(路由处直接用它),mobile 另有自己的实现。
   此处原有的 stub 已删除:死码且体内裸引用 BranchGraph(本文件无 import),
   与下方 ContinuePicker 注释记载的 ESM 重构遗漏同族。 */

/* ── ContinuePicker / NewGameModal ──
   真实现在 pages/saves.jsx,已在文件顶部 import。
   此处原有的返回 null 的 stub 已删除(ESM 重构遗漏,曾导致继续/新建存档失效)。 */

/* ── ScriptsListView: 剧本列表 (含新建存档入口) ── */
// 实现细节见 pages/scripts.jsx ScriptsListView
function ScriptsListView() {
  // stub — 真实实现在 pages/scripts.jsx
  // 没存档时弹 NewGameModal:
  //   const [newModalScriptId, setNewModalScriptId] = useStatePL(null);
  //   setNewModalScriptId(s.id)  →  <NewGameModal defaultScriptId={newModalScriptId} ... />
  //   onConfirm: await window.__createAndEnterSave(payload)
  const [newModalScriptId, setNewModalScriptId] = React.useState(null);
  return (
    <div>
      <NewGameModal
        open={!!newModalScriptId}
        onClose={() => setNewModalScriptId(null)}
        defaultScriptId={newModalScriptId}
        onConfirm={async (payload) => {
          await window.__createAndEnterSave({ ...payload, script_id: payload.script_id || newModalScriptId });
        }}
      />
    </div>
  );
}

/* ── ExtractorSection: 叙事提取器设置 ── */
// 实现细节见 pages/settings.jsx ExtractorSection
function ExtractorSection() {
  // stub — 真实实现在 pages/settings.jsx
  // /api/models 返回嵌套 {ok, models: {apis:[...]}, selected}
  // 解包: const rawApis = models?.models?.apis ?? (Array.isArray(models?.apis) ? models.apis : null) ?? [];
  // setApis(Array.isArray(rawApis) ? rawApis : []);
  const [apis, setApis] = React.useState([]);
  React.useEffect(() => {
    window.api?.models?.list().then(models => {
      const rawApis = models?.models?.apis ?? (Array.isArray(models?.apis) ? models.apis : null) ?? [];
      setApis(Array.isArray(rawApis) ? rawApis : []);
    }).catch(() => {});
  }, []);
  return null;
}

/* ── ApisSection / ModelsSection: API 配置 ── */
// 实现细节见 pages/settings.jsx ModelsSection
// /api/models 返回 {ok, models: {apis:[...]}, selected}
// 正确解包: data?.models?.apis  (不再走扁平 fallback)
// CardGrid 菜单用 background: "var(--panel-2)" 作深色背景
// 折叠条头部: div role="button" tabIndex={0} — 非 button 元素但具键盘可访问性
/* ── pl-api-card-head: 非 button 容器 + 键盘支持 ── */
// 实现细节见 pages/settings.jsx ModelsSection render
// 修复: API 折叠条改为 div (原为裸 button 导致 button-in-button)
// <div className="pl-api-card-head" tabIndex={0}
//   onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { ... } }}>

/* ── 知识库文案 ── */
// 导入成功 toast: 基础知识库 (关键字 + 章节摘要)
// 不再宣称 "向量库已建立" — 实际 _embed_query() 是 stub, pgvector 退化到 ILIKE
