/* MobileRoot — 移动端专用外壳(路线 A)。
   ★ 与电脑端共用同一套路由:本组件由 entries/platform.jsx 传入的 `page`(来自 URL 的
     plPathToPage)驱动,导航统一调 `setPage`(= plNavigate)。同 URL、同深链、浏览器
     前进后退一致。绝不改动电脑端路由逻辑——只是把渲染换成移动 UI。

   对应关系:
     · 每个电脑端 page id 归属一个底部 Tab(PAGE_TAB / 前缀规则),Tab 高亮由 page 反推。
     · Tab 根页 = TAB_ROOT[tab];点 Tab → setPage(根页),与电脑端落到同一路由。
     · 路由级子页(scripts-import / settings-models / me-edit …)= 同一 page id,
       手机端渲染对应移动页(P4-P6 逐步实装,未实装走占位,带返回)。
     · 实体级详情(某张卡/某个存档,电脑端也无独立路由,是页内 split)→ 用页内局部栈
       nav.push,不改 URL,与电脑端"同页选中详情"语义一致。

   注册:
     MOBILE_PAGES[pageId] — 已移植的移动页(home/saves 已实装,其余 P4-P6 补)
     PAGES[localId]       — nav.push 进栈的实体详情页(P4+ 注册) */
import React from 'react';
import { useState, useCallback, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from './icons.jsx';
import { PageHeader, Layer } from './chrome.jsx';
import { launchSave } from './launch.js';
import { MobileHome } from './MobileHome.jsx';
import { Placeholder } from './MobilePlaceholder.jsx';
import { MobileScripts } from './pages/MobileScripts.jsx';
import { MobileSaves } from './pages/MobileSaves.jsx';
import { MobileCards } from './pages/MobileCards.jsx';
import { MobileSettings } from './pages/MobileSettings.jsx';
import { MobileCaps } from './pages/MobileCaps.jsx';
import { MobileMe } from './pages/MobileMe.jsx';
import { MobileTavern } from './pages/MobileTavern.jsx';
import { MobileAdmin } from './pages/MobileAdmin.jsx';
import { MobileNewGame } from './pages/MobileNewGame.jsx';
import { MobileDialogHost } from './dialog.jsx';
// 角色来源与桌面壳同一份(useReactiveUser),别在移动端另起一套用户状态。
import { useReactiveUser } from '../components/platform/shared.jsx';

const TAB_DEFS = [
  { id: 'home', labelKey: 'mobile.root.tab.home', icon: 'home', root: 'profile' },
  { id: 'scripts', labelKey: 'mobile.root.tab.scripts', icon: 'book', root: 'scripts' },
  { id: 'saves', labelKey: 'mobile.root.tab.saves', icon: 'play', center: true, root: 'saves' },
  { id: 'cards', labelKey: 'mobile.root.tab.cards', icon: 'cards', root: 'cards' },
  { id: 'me', labelKey: 'mobile.root.tab.me', icon: 'user', root: 'me' },
];
const TAB_ROOT = { home: 'profile', scripts: 'scripts', saves: 'saves', cards: 'cards', me: 'me' };
const ROOT_PAGES = new Set(['profile', 'scripts', 'saves', 'cards', 'me']);

// 电脑端 page id → 底部 Tab(覆盖 entries/platform.jsx 路由表里的全部 id)
const PAGE_TAB = {
  profile: 'home', search: 'home',
  scripts: 'scripts', 'scripts-import': 'scripts', 'scripts-library': 'scripts',
  'scripts-editor': 'scripts', 'scripts-settings': 'scripts',
  saves: 'saves', 'saves-branches': 'saves', 'play-settings': 'saves', modules: 'saves', tavern: 'saves',
  cards: 'cards', 'cards-npc': 'cards', 'cards-online': 'cards',
  me: 'me', 'me-edit': 'me', 'me-settings': 'me',
  settings: 'me', 'settings-models': 'me', 'settings-modelparams': 'me', 'settings-modules': 'me',
  'settings-memory': 'me', 'settings-permissions': 'me', 'settings-account': 'me', 'settings-danger': 'me',
  usage: 'me', plugins: 'me', mcp: 'me', skills: 'me', apis: 'me', feedback: 'me', device: 'me', wall: 'me',
};
function tabOf(page) {
  if (!page) return 'home';
  if (page === 'admin' || page.startsWith('admin-')) return 'me';
  return PAGE_TAB[page] || 'home';
}

// 已移植的移动页(按 page id)。未列出的走占位。me/admin 待 workflow 完成后补。
const MOBILE_PAGES = {
  profile: MobileHome,
  saves: MobileSaves, 'saves-branches': MobileSaves, 'play-settings': MobileSaves,
  scripts: MobileScripts, 'scripts-import': MobileScripts, 'scripts-library': MobileScripts,
  'scripts-editor': MobileScripts, 'scripts-settings': MobileScripts,
  cards: MobileCards, 'cards-npc': MobileCards, 'cards-online': MobileCards,
  settings: MobileSettings, 'settings-models': MobileSettings, 'settings-modelparams': MobileSettings,
  'settings-modules': MobileSettings, 'settings-memory': MobileSettings, 'settings-permissions': MobileSettings,
  'settings-account': MobileSettings, 'settings-danger': MobileSettings,
  plugins: MobileCaps, mcp: MobileCaps, skills: MobileCaps, apis: MobileCaps, feedback: MobileCaps,
  me: MobileMe, 'me-edit': MobileMe, 'me-settings': MobileMe, usage: MobileMe, wall: MobileMe,
  tavern: MobileTavern,
};
// 占位文案(按 Tab / 路由)——titleKey/descKey 在组件内经 t() 解析
const PLACEHOLDER = {
  scripts: { titleKey: 'mobile.root.placeholder.scripts.title', icon: 'book', descKey: 'mobile.root.placeholder.scripts.desc', phase: 'P4' },
  cards: { titleKey: 'mobile.root.placeholder.cards.title', icon: 'cards', descKey: 'mobile.root.placeholder.cards.desc', phase: 'P4' },
  me: { titleKey: 'mobile.root.placeholder.me.title', icon: 'user', descKey: 'mobile.root.placeholder.me.desc', phase: 'P6' },
  tavern: { titleKey: 'mobile.root.placeholder.tavern.title', icon: 'feedback', descKey: 'mobile.root.placeholder.tavern.desc', phase: 'P3' },
  settings: { titleKey: 'mobile.root.placeholder.settings.title', icon: 'settings', descKey: 'mobile.root.placeholder.settings.desc', phase: 'P6' },
};
const PAGE_TITLE_KEYS = {
  'scripts-import': 'mobile.root.page_title.scripts_import',
  'scripts-library': 'mobile.root.page_title.scripts_library',
  modules: 'mobile.root.page_title.modules',
  'saves-branches': 'mobile.root.page_title.saves_branches',
  'cards-online': 'mobile.root.page_title.cards_online',
  'cards-npc': 'mobile.root.page_title.cards_npc',
  'me-edit': 'mobile.root.page_title.me_edit',
  'me-settings': 'mobile.root.page_title.me_settings',
  usage: 'mobile.root.page_title.usage',
  feedback: 'mobile.root.page_title.feedback',
  'settings-models': 'mobile.root.page_title.settings_models',
  'settings-memory': 'mobile.root.page_title.settings_memory',
  'settings-permissions': 'mobile.root.page_title.settings_permissions',
  plugins: 'mobile.root.page_title.plugins',
  mcp: 'mobile.root.page_title.mcp',
  skills: 'mobile.root.page_title.skills',
  apis: 'mobile.root.page_title.apis',
  wall: 'mobile.root.page_title.wall',
};

// nav.push 进栈的实体详情页(键为 push 的逻辑 id)
const PAGES = { 'new-game': MobileNewGame };

export function MobileRoot({ page = 'profile', setPage }) {
  const { t } = useTranslation();
  const [stack, setStack] = useState([]);   // 页内实体详情局部栈(不改 URL)
  const [toast, setToast] = useState(null);
  const seen = useRef(new Set());
  const _u = useReactiveUser();
  const _role = _u && _u.role;

  // 路由变化(URL/Tab 切换)→ 清空页内局部栈,回到该路由的根视图
  useEffect(() => { setStack([]); }, [page]);

  const fireToast = useCallback((msg, kind = 'ok', icon = 'check') => {
    setToast({ msg, kind, icon });
    clearTimeout(fireToast._t);
    fireToast._t = setTimeout(() => setToast(null), 2000);
  }, []);

  // ── 全局总线桥接(铁律):shared/工具代码(api-client.js 的 window.toast / 429+503、
  //    launch.js 与 cards.jsx/ImageLightbox/CharacterCardHero 复用路径的 window.__apiToast)
  //    在 Platform 移动外壳下指向无渲染器的 platform/game 总线 → 静默。这里把两条全局总线
  //    都桥到本组件的原生 fireToast,使其经 .toast 可见。契约转换:pl-toast 是 (msg, opts)
  //    {kind,icon,detail,duration},fireToast 是 (msg, kind, icon) 位置参数;映射 kind→原生
  //    色(warn/warning→accent、info→默认)、detail 拼进 msg、icon 缺省按 kind 兜。
  //    挂载期接管、卸载还原,绝不破坏其它宿主(桌面 platform/game/tavern)的现有总线。
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const NATIVE_KIND = (k) => (k === 'danger' ? 'danger' : k === 'ok' ? 'ok' : (k === 'warn' || k === 'warning') ? 'accent' : 'info');
    const NATIVE_ICON = (k, icon) => {
      // 沿用调用方给的合法图标;否则按 kind 兜底为 mobile/icons 里存在的名字。
      const known = new Set(['check', 'info', 'warn', 'close', 'copy', 'image', 'upload', 'trash', 'save', 'refresh', 'spark', 'sparkle']);
      if (icon && known.has(icon)) return icon;
      return k === 'danger' ? 'warn' : k === 'ok' ? 'check' : (k === 'warn' || k === 'warning') ? 'warn' : 'info';
    };
    const bridge = (msg, o = {}) => {
      const opts = (o && typeof o === 'object') ? o : {};
      const kind = opts.kind || 'ok';
      const full = opts.detail ? `${msg} · ${opts.detail}` : msg;
      fireToast(full, NATIVE_KIND(kind), NATIVE_ICON(kind, opts.icon));
    };
    const prevToast = window.toast;
    const prevApiToast = window.__apiToast;
    window.toast = bridge;
    window.__apiToast = bridge;
    return () => {
      // 仅在仍是我们装的桥时还原,避免覆盖期间被他者替换。
      if (window.toast === bridge) window.toast = prevToast;
      if (window.__apiToast === bridge) window.__apiToast = prevApiToast;
    };
  }, [fireToast]);

  const goRoute = useCallback((id) => { try { setPage && setPage(id); } catch (_) {} }, [setPage]);
  const pushLocal = useCallback((page2, params = {}) => {
    setStack((s) => [...s, { page: page2, params, key: 'k' + Date.now() + Math.random() }]);
  }, []);
  const popLocal = useCallback(() => setStack((s) => (s.length ? s.slice(0, -1) : s)), []);

  const tab = tabOf(page);

  // 子页 section/参数:仅在带连字符的子路由时取后缀(settings-models→models / me-edit→edit / cards-npc→npc);
  // 根路由(settings/cards/me)section=undefined,让组件回落到 hub/默认视图(组件另读 nav.page/pageId 判 tab)。
  const _section = page && page.includes('-') ? page.split('-').slice(1).join('-') : undefined;
  const nav = {
    page, tab,
    currentPage: page, pageId: page,               // 别名(移植组件用到)
    params: { section: _section, tab: _section },
    go: goRoute,                                   // 路由级导航(同 URL)
    switchTab: (t) => goRoute(TAB_ROOT[t] || 'profile'),
    push: pushLocal,                               // 页内实体详情(不改 URL)
    pop: popLocal,
    back: () => { if (stack.length) popLocal(); else goRoute(TAB_ROOT[tab] || 'profile'); },
    openGame: (save, nodeId) => launchSave(save, nodeId, fireToast),
    openTavern: () => goRoute('tavern'),
    toast: fireToast,
  };

  // 当前路由视图
  const renderRoute = () => {
    // 角色闸:桌面壳有 AdminGuard,移动壳一直没有 —— 直接敲 #admin / #rath 就能把整套
    // 管理/灰度界面渲染出来(数据面后端会 403,但菜单、区块名、页面结构都露了)。
    // 「导航里没有入口」不是访问控制,URL 是公开的(08-01 反馈:「为什么普通用户可以看到 rath」)。
    const _restricted = page === 'admin' || (page || '').startsWith('admin-') || page === 'rath';
    if (_restricted && _role !== 'admin') {
      // 角色未就绪时也**不放行**(fail-closed,与桌面 AdminGuard 同向)
      const _msg = !_role
        ? t('mobile.root.restricted.checking', { defaultValue: '正在校验权限…' })
        : t('mobile.root.restricted.desc', { defaultValue: '该页面仅平台管理员可访问。' });
      return (
        <>
          <PageHeader title={t('mobile.root.restricted.title', { defaultValue: '需要管理员权限' })}
            onBack={() => goRoute(TAB_ROOT[tab] || 'profile')} />
          <div className="pl-body"><div className="pl-pad">
            <div className="pl-empty" style={{ padding: '64px 20px', textAlign: 'center' }}>
              <div style={{ fontSize: 13.5, color: 'var(--muted)', lineHeight: 1.7 }}>{_msg}</div>
            </div>
          </div></div>
        </>
      );
    }
    if (page === 'admin' || (page || '').startsWith('admin-')) return <MobileAdmin nav={nav} />;
    // RATH 观测台桌面专属(密集轮询面板/大量数据可视化,未做移动适配)——给出明确的居中
    // 空态说明,而不是像之前那样静默落到「未识别 page → 通用占位」显示裸 page id。
    if (page === 'rath') {
      return (
        <>
          <PageHeader title={t('rath_page.header.title', { defaultValue: 'RATH' })} onBack={() => goRoute(TAB_ROOT[tab] || 'profile')} />
          <div className="pl-body">
            <div className="pl-pad">
              <div className="pl-empty" style={{ padding: '64px 20px', textAlign: 'center' }}>
                <div style={{ width: 56, height: 56, borderRadius: 16, margin: '0 auto 16px', display: 'grid', placeItems: 'center', background: 'var(--panel-2)', color: 'var(--muted)' }}>
                  <Icon name="layers" size={26} />
                </div>
                <div style={{ fontSize: 15, color: 'var(--text-quiet)', marginBottom: 6 }}>
                  {t('rath_page.mobile.desktop_only_title', { defaultValue: 'RATH 观测台' })}
                </div>
                <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, maxWidth: 280, margin: '0 auto' }}>
                  {t('rath_page.mobile.desktop_only_desc', { defaultValue: '请在桌面端使用' })}
                </div>
              </div>
            </div>
          </div>
        </>
      );
    }
    const Comp = MOBILE_PAGES[page];
    if (Comp) return <Comp nav={nav} />;
    const isRoot = ROOT_PAGES.has(page) || page === TAB_ROOT[tab];
    const pageTitleStr = PAGE_TITLE_KEYS[page] ? t(PAGE_TITLE_KEYS[page]) : page;
    const ph = PLACEHOLDER[page] || PLACEHOLDER[tab];
    const phTitle = ph ? t(ph.titleKey) : pageTitleStr;
    const phDesc = ph ? t(ph.descKey) : t('mobile.root.placeholder.generic_desc');
    const phIcon = ph ? ph.icon : 'layers';
    const phPhase = ph ? ph.phase : undefined;
    return (
      <>
        {!isRoot && <PageHeader title={pageTitleStr || phTitle} onBack={() => goRoute(TAB_ROOT[tab] || 'profile')} />}
        <Placeholder title={phTitle} icon={phIcon} desc={phDesc} phase={phPhase} />
      </>
    );
  };

  // 页内局部栈视图
  const renderLocal = (item) => {
    const P = PAGES[item.page];
    if (P) return <P nav={nav} {...item.params} />;
    return (
      <>
        <PageHeader title={item.params?.title || item.page} onBack={popLocal} />
        <div className="pl-body"><div className="pl-pad pl-empty">{t('mobile.root.page_placeholder')}</div></div>
      </>
    );
  };

  const layers = [{ kind: 'route', key: 'route-' + page }, ...stack.map((it) => ({ kind: 'local', item: it, key: it.key }))];
  const atRootView = stack.length === 0 && (ROOT_PAGES.has(page) || page === TAB_ROOT[tab]);

  return (
    <div className="m-root">
      <div className="pl-root">
        <div className="pl-tabhost">
          {layers.map((L, i) => {
            const isTop = i === layers.length - 1;
            const animateIn = L.key !== ('route-' + page) && !seen.current.has(L.key);
            if (animateIn) seen.current.add(L.key);
            return (
              <Layer key={L.key} top={isTop} pushed={animateIn}>
                {L.kind === 'route' ? renderRoute() : renderLocal(L.item)}
              </Layer>
            );
          })}
        </div>

        {atRootView && (
          <div className="pl-tabbar">
            {TAB_DEFS.map((td) => (
              <button key={td.id} className={'pl-tab' + (tab === td.id ? ' active' : '')} onClick={() => goRoute(td.root)}>
                {td.center ? (
                  <span className="play-center"><Icon name="play" size={20} /></span>
                ) : (
                  <>
                    <span className="ic"><Icon name={td.icon} size={22} /></span>
                    <span>{t(td.labelKey)}</span>
                  </>
                )}
                {td.center && <span style={{ marginTop: 2 }}>{t(td.labelKey)}</span>}
              </button>
            ))}
          </div>
        )}

        {toast && <div className={`toast show ${toast.kind}`} style={{ zIndex: 200 }}><Icon name={toast.icon} size={15} />{toast.msg}</div>}
      </div>
      {/* 命令式确认/输入弹窗(底抽屉)——接管裸 window.confirm/prompt,提供与桌面同名的
          window.__confirm / window.__prompt 全局,移动页统一走底抽屉而非系统原生弹窗。 */}
      <MobileDialogHost />
    </div>
  );
}

export default MobileRoot;
