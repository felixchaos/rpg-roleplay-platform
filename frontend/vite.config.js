import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],

  // 把 React / ReactDOM 注入为全局变量，兼容现有 JSX 里大量 `React.xxx` / `ReactDOM.xxx` 的写法
  // （JSX 文件均未 import React，延续零构建 UMD 风格）
  define: {
    // 不需要额外 define：@vitejs/plugin-react 已通过 automatic JSX runtime 注入
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

  build: {
    rollupOptions: {
      input: {
        // 6 个 HTML 多页入口，保留原有多页结构
        index:          resolve(__dirname, 'index.html'),
        overview:       resolve(__dirname, 'Overview.html'),
        platform:       resolve(__dirname, 'Platform.html'),
        game_console:   resolve(__dirname, 'Game Console.html'),
        login:          resolve(__dirname, 'Login.html'),
        design_canvas:  resolve(__dirname, 'Design Canvas.html'),
      },
    },
  },
});
