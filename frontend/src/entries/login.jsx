// Login 页面入口 — Vite ESM 版
import * as React from 'react';
import * as ReactDOM from 'react-dom/client';
window.React = React;
window.ReactDOM = ReactDOM;

import '../mock-data.js';
import '../api-client.js';
import '../data-loader.js';

import '../game-icons.jsx';
import '../platform-app.jsx';

// 挂载（等价原 HTML inline babel script）
const __mount = () =>
  ReactDOM.createRoot(document.getElementById('root')).render(<AuthPage />);
if (window.RPG_DATA_READY) {
  window.RPG_DATA_READY.then(__mount);
} else {
  __mount();
}
