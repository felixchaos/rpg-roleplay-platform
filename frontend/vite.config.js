import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig(({ mode }) => {
  // mode 暂未驱动分支(原 dev-only Design Canvas 入口已删);保留 sig 兼容。
  void mode;

  const inputs = {
    // 产品入口:登录 + 多用户创作工作台 + RPG 游戏控制台。
    // 旧 Claude Design 原型(Overview / index 设计评审)+ Design Canvas 已删;
    // landing 另起项目独立部署。Login 由本仓库提供(配套后端鉴权)。
    login:        resolve(__dirname, 'Login.html'),
    platform:     resolve(__dirname, 'Platform.html'),
    game_console: resolve(__dirname, 'Game Console.html'),
  };

  // ── SPA history fallback(仅 dev server)─────────────────────────────
  // Platform 用 History API 路由(/settings、/saves、/wall 等)。vite dev 默认无
  // SPA 回退,直接访问/刷新这些干净 URL 会 404 白屏。这里在 configureServer 注入
  // connect 中间件,把非 /api、非静态文件的请求回退到 Platform.html。
  // 仅作用于 dev;preview/生产由各自服务器做 history-fallback,不受影响。
  // (来自社区贡献者 xingzhiyou 的 PR #14)
  function spaHistoryFallbackPlugin() {
    return {
      name: 'spa-history-fallback',
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          const url = req.url || '';
          if (
            url.startsWith('/api') ||
            url.startsWith('/assets/') ||
            url.startsWith('/@') ||
            url.startsWith('/node_modules/') ||
            /\.\w+(\?|$)/.test(url) ||
            url === '/Login.html' ||
            url === '/Platform.html' ||
            url === '/Game Console.html' ||
            url === '/favicon.svg'
          ) {
            return next();
          }
          req.url = '/Platform.html';
          next();
        });
      },
    };
  }

  return {
    // jsxRuntime: 'classic' — 所有 JSX 文件已显式 import React,
    // classic runtime 用 React.createElement 替代 automatic 的 _jsx()。
    plugins: [react({ jsxRuntime: 'classic' }), spaHistoryFallbackPlugin()],

    // ── 永久根治 dev 黑屏 ────────────────────────────────────────────────
    // 根因:Cloudscape 每个组件是独立子入口,Vite 默认懒发现依赖;运行中遇到
    // 没预打包过的新组件导入 → 中途 re-optimize + 强制整页 reload,页面卡在
    // reload 空窗 = 黑屏(日志 "new dependencies optimized... reloading")。
    // 解法:把全部用到的子入口一次性列进 optimizeDeps.include,启动时全部预打包,
    // 运行中不再 re-optimize。新增组件时把对应子路径加到这里即可。
    optimizeDeps: {
      include: [
        'react', 'react-dom', 'react-dom/client',
        '@cloudscape-design/global-styles',
        '@cloudscape-design/components/theming',
        '@cloudscape-design/components/alert',
        '@cloudscape-design/components/app-layout',
        '@cloudscape-design/components/badge',
        '@cloudscape-design/components/box',
        '@cloudscape-design/components/button',
        '@cloudscape-design/components/button-dropdown',
        '@cloudscape-design/components/cards',
        '@cloudscape-design/components/column-layout',
        '@cloudscape-design/components/container',
        '@cloudscape-design/components/expandable-section',
        '@cloudscape-design/components/file-upload',
        '@cloudscape-design/components/form-field',
        '@cloudscape-design/components/header',
        '@cloudscape-design/components/input',
        '@cloudscape-design/components/key-value-pairs',
        '@cloudscape-design/components/modal',
        '@cloudscape-design/components/progress-bar',
        '@cloudscape-design/components/segmented-control',
        '@cloudscape-design/components/select',
        '@cloudscape-design/components/side-navigation',
        '@cloudscape-design/components/space-between',
        '@cloudscape-design/components/split-panel',
        '@cloudscape-design/components/status-indicator',
        '@cloudscape-design/components/table',
        '@cloudscape-design/components/tabs',
        '@cloudscape-design/components/text-filter',
        '@cloudscape-design/components/textarea',
        '@cloudscape-design/components/toggle',
        '@cloudscape-design/components/top-navigation',
        '@cloudscape-design/components/wizard',
      ],
    },

    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://localhost:7860',
          changeOrigin: true,
        },
      },
    },

    // vite preview(服务 dist 构建,稳定无 dep 优化抖动)同样代理 /api
    preview: {
      port: 5173,
      proxy: {
        '/api': {
          target: 'http://localhost:7860',
          changeOrigin: true,
        },
      },
    },

    build: {
      cssCodeSplit: true,
      reportCompressedSize: true,
      sourcemap: false,
      rollupOptions: {
        input: inputs,
        output: {
          assetFileNames: 'assets/[name]-[hash][extname]',
          chunkFileNames: 'assets/[name]-[hash].js',
          entryFileNames: 'assets/[name]-[hash].js',
          manualChunks: (id) => {
            // React 单独 vendor chunk,跨页面缓存,减少 hash 抖动
            if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/') ||
                id.includes('node_modules/scheduler/')) {
              return 'react-vendor';
            }
            // Cloudscape 是 platform 主 bundle 的大头(~500KB),拆出来跨页缓存
            if (id.includes('node_modules/@cloudscape-design/')) {
              return 'cloudscape';
            }
            // i18next 跨页缓存
            if (id.includes('node_modules/i18next') || id.includes('node_modules/react-i18next')) {
              return 'i18n';
            }
            // ace editor 系列 + 其他大型 vendor(若有),独立 chunk
            if (id.includes('node_modules/ace-builds')) {
              return 'ace-editor';
            }
            // 新加的编辑器视图组件,首屏不需要 → 单独 chunk,后续 lazy() 可包,
            // 即便不 lazy,浏览器解析这个 chunk 也比塞主 bundle 快
            if (id.includes('/pages/script-edit-')) {
              return 'script-editors';
            }
            // 其他保持默认(Vite 按 entry 分)
          },
        },
      },
    },
  };
});
