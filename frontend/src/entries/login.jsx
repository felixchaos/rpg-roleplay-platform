// Login 页入口 — Vite ESM 版
import '../web-vitals-rum.js';
import * as React from 'react';
import * as ReactDOM from 'react-dom/client';

import '../api-client.js';
import '../i18n/index.js';
import { LoginApp } from '../login-app.jsx';

const __mount = () => {
  const root = document.getElementById('root');
  if (!root) return;
  ReactDOM.createRoot(root).render(<LoginApp />);
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', __mount, { once: true });
} else {
  __mount();
}
