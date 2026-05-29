// Design Canvas 页面入口 — Vite ESM 版
import * as React from 'react';
import * as ReactDOM from 'react-dom/client';
window.React = React;
window.ReactDOM = ReactDOM;

import '../design-canvas.jsx';

function Screen({ src, label }) {
  return (
    <div className="ovr-iframe-wrap">
      <iframe src={src} title={label} loading="lazy" />
      <a className="ovr-open-btn" href={src} target="_blank" rel="noreferrer">打开 ↗</a>
    </div>
  );
}

function App() {
  return (
    <DesignCanvas
      title="RPG Roleplay · 设计画板"
      subtitle="所有页面同屏对比 · 可拖动、缩放、单卡聚焦"
    >
      <DCSection id="game" title="主游戏页 /" subtitle="Codex 风格 RPG 控制台">
        <DCArtboard id="game-desktop" label="桌面 · 1440 ✕ 900" width={1440} height={900}>
          <Screen src="Game Console.html" label="Game Console" />
        </DCArtboard>
        <DCArtboard id="game-tablet" label="平板 · 1024" width={1024} height={1366}>
          <Screen src="Game Console.html" label="Game Console · tablet" />
        </DCArtboard>
        <DCArtboard id="game-mobile" label="移动 · 390" width={390} height={844}>
          <Screen src="Game Console.html" label="Game Console · mobile" />
        </DCArtboard>
      </DCSection>

      <DCSection id="platform" title="平台 /app · 工作台" subtitle="主页 · 剧本 · 开始游戏 · 分支 · 库">
        <DCArtboard id="profile" label="主页" width={1440} height={900}>
          <Screen src="Platform.html#profile" label="Profile" />
        </DCArtboard>
        <DCArtboard id="scripts" label="剧本" width={1440} height={900}>
          <Screen src="Platform.html#scripts" label="Scripts" />
        </DCArtboard>
        <DCArtboard id="saves" label="开始游戏" width={1440} height={900}>
          <Screen src="Platform.html#saves" label="Saves" />
        </DCArtboard>
        <DCArtboard id="branches" label="分支" width={1440} height={900}>
          <Screen src="Platform.html#saves-branches" label="Branches" />
        </DCArtboard>
        <DCArtboard id="library" label="库" width={1440} height={900}>
          <Screen src="Platform.html#library" label="Library" />
        </DCArtboard>
      </DCSection>

      <DCSection id="config" title="平台 /app · 配置" subtitle="设置 · 插件 · MCP · Skill · API">
        <DCArtboard id="settings" label="设置" width={1440} height={900}>
          <Screen src="Platform.html#settings" label="Settings" />
        </DCArtboard>
        <DCArtboard id="plugins" label="插件" width={1440} height={900}>
          <Screen src="Platform.html#plugins" label="Plugins" />
        </DCArtboard>
        <DCArtboard id="mcp" label="MCP" width={1440} height={900}>
          <Screen src="Platform.html#mcp" label="MCP" />
        </DCArtboard>
        <DCArtboard id="skills" label="Skill" width={1440} height={900}>
          <Screen src="Platform.html#skills" label="Skills" />
        </DCArtboard>
        <DCArtboard id="apis" label="API" width={1440} height={900}>
          <Screen src="Platform.html#apis" label="API" />
        </DCArtboard>
      </DCSection>

      <DCSection id="auth-and-notes" title="认证 + 设计说明">
        <DCArtboard id="login" label="登录 / 注册" width={1440} height={900}>
          <Screen src="Login.html" label="Login" />
        </DCArtboard>
        <DCArtboard id="notes" label="设计 token & 信息架构" width={680} height={900}>
          <div className="ovr-note">
            <h3>信息架构</h3>
            <p>
              系统分为<strong>主游戏页 /</strong>与<strong>平台 /app</strong>两端。共享底层数据形：
              <code>player</code> / <code>world</code> / <code>memory</code> / <code>permissions</code> /
              <code>worldline</code> / <code>history</code>，分别由 <code>/api/state</code> 与
              <code>/api/platform</code> 驱动。
            </p>
            <h3 style={{ marginTop: 18 }}>主游戏页布局</h3>
            <p>
              三栏：左侧存档 / 记忆模式 / 运行状态；中部叙事流 + Codex 风格运行步骤 + 非阻塞确认；
              右侧 7 tab（状态 / 记忆 / 世界书 / 角色卡 / 世界线 / 上下文 / 调试）。
            </p>
            <h3 style={{ marginTop: 18 }}>平台导航</h3>
            <p>
              双区导航：<strong>工作台</strong>（主页 / 剧本 / 开始游戏 / 分支 / 库）与
              <strong>配置</strong>（设置 / 插件 / MCP / Skill / API）。
            </p>
            <h3 style={{ marginTop: 18 }}>关键 token</h3>
            <ul style={{ paddingLeft: 18, lineHeight: 1.9 }}>
              <li>底色 <code>#1a1817</code> · 面板 <code>#211f1d</code> · 强调 <code>#c96442</code></li>
              <li>圆角 4 / 6 / 8（最大）；不出现卡片嵌套</li>
              <li>叙事 Noto Serif SC · UI Noto Sans SC · 路径 / 变量 JetBrains Mono</li>
              <li>状态：dot / pill / chip 三种轻量元素</li>
            </ul>
          </div>
        </DCArtboard>
      </DCSection>
    </DesignCanvas>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
