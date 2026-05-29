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

  return {
    // jsxRuntime: 'classic' — 所有 JSX 文件已显式 import React,
    // classic runtime 用 React.createElement 替代 automatic 的 _jsx()。
    plugins: [react({ jsxRuntime: 'classic' })],

    server: {
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
          manualChunks: {
            // React 单独 vendor chunk，跨页面缓存，减少 hash 抖动
            'react-vendor': ['react', 'react-dom'],
          },
        },
      },
    },
  };
});
