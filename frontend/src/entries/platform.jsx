// Platform 页面入口 — Vite ESM 版
import '../web-vitals-rum.js';
import React from 'react';
import { useState, useEffect } from 'react';
import * as ReactDOM from 'react-dom/client';

// 基础设施 side-effect 模块(设置 window.api / window.MOCK_* / SSE bridge 等)
import '../mock-data.js';
import '../api-client.js';
import '../data-loader.js';
import '../state-event-bridge.js';
import '../worldbook-status-toast.js';
import '../ui-atlas.js';
import '../console-assistant-navigation.jsx';

// 组件模块 — named import(ESM 自动拉入传递依赖)
import { PlatformShell, ProfilePage, MePage, ModulesPage, LibraryPage, UsagePage, CapPage, PL_NAV } from '../platform-app.jsx';
import { SavesPage } from '../pages/saves.jsx';
import { ScriptsPage } from '../pages/scripts.jsx';
import { CardsPage } from '../pages/cards.jsx';
import { SettingsPage } from '../pages/settings.jsx';
import { ConsoleAssistantPanel } from '../console-assistant-panel.jsx';

// ── 挂载 ──

const TWEAK_DEFAULTS = {
  startPage: 'profile',
  sidebarWidth: 244,
  accent: 'terracotta',
};

const HASH_ALIASES = { branches: 'saves-branches' };
function parsePageFromHash() {
  const raw = location.hash.replace('#', '');
  const hash = HASH_ALIASES[raw] || raw;
  const ids = [
    ...((PL_NAV || []).filter((i) => i.id).map((i) => i.id)),
    'me', 'me-edit', 'me-settings', 'saves-branches', 'scripts-import', 'cards-npc',
  ];
  if (!ids.includes(hash)) return null;
  if (raw !== hash) {
    try { history.replaceState(null, '', '#' + hash); } catch (_) {}
  }
  return hash;
}

function PlatformApp() {
  const t = TWEAK_DEFAULTS;
  const [page, setPage] = useState(parsePageFromHash() || t.startPage || 'profile');
  const [assistantOpen, setAssistantOpen] = useState(false);

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

  useEffect(() => {
    const onHashChange = () => {
      const p = parsePageFromHash();
      if (p) setPage(p);
    };
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const go = (id) => {
    setPage(id);
    history.replaceState(null, '', '#' + id);
  };

  let body = null;
  if (page === 'profile') body = <ProfilePage />;
  else if (page === 'me') body = <MePage subPage="overview" />;
  else if (page === 'me-edit') body = <MePage subPage="edit" />;
  else if (page === 'me-settings') body = <MePage subPage="settings" />;
  else if (page === 'scripts') body = <ScriptsPage subPage="list" />;
  else if (page === 'scripts-import') body = <ScriptsPage subPage="import" />;
  else if (page === 'modules') body = <ModulesPage />;
  else if (page === 'saves') body = <SavesPage subPage="list" />;
  else if (page === 'saves-branches') body = <SavesPage subPage="branches" />;
  else if (page === 'library') body = <LibraryPage />;
  else if (page === 'cards') body = <CardsPage subPage="user" />;
  else if (page === 'cards-npc') body = <CardsPage subPage="npc" />;
  else if (page === 'settings') body = <SettingsPage />;
  else if (page === 'usage') body = <UsagePage />;
  else if (page === 'plugins') body = <CapPage kind="plugins" />;
  else if (page === 'mcp') body = <CapPage kind="mcp" />;
  else if (page === 'skills') body = <CapPage kind="skills" />;
  else if (page === 'apis') body = <CapPage kind="apis" />;
  else body = <ProfilePage />;

  return (
    <>
      <PlatformShell
        page={page}
        setPage={go}
        assistantOpen={assistantOpen}
        onOpenAssistant={() => setAssistantOpen(true)}
        assistant={
          <ConsoleAssistantPanel
            open={assistantOpen}
            onClose={() => setAssistantOpen(false)}
            pageContext={{ tab: 'platform.' + page }}
          />
        }
      >
        {body}
      </PlatformShell>
    </>
  );
}

const __mount = () =>
  ReactDOM.createRoot(document.getElementById('root')).render(<PlatformApp />);
const __gateThenMount = (info) => {
  const offline = new URLSearchParams(location.search).has('offline');
  if (info && info.online && !info.authed && !offline) {
    const next = encodeURIComponent(
      location.pathname + location.search + location.hash
    );
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
