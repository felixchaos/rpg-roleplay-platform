/* eslint.config.js — Flat config for ESLint 9.x
 *
 * 安装依赖后运行:
 *   npx eslint src/
 *
 * 渐进策略:
 *   - 当前只检查 .ts/.tsx 文件（新代码）
 *   - .js/.jsx 文件暂不检查（checkJs=false，平滑迁移）
 */
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactPlugin from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

export default tseslint.config(
  // 基础推荐规则
  js.configs.recommended,
  ...tseslint.configs.recommended,

  // 全局忽略模式
  {
    ignores: [
      "node_modules/",
      "dist/",
      "vite.config.js",
      "vitest.config.js",
      "playwright.config.js",
      "e2e/",
    ],
  },

  // TypeScript / TSX 文件规则
  {
    files: ["src/**/*.ts", "src/**/*.tsx"],
    plugins: {
      react: reactPlugin,
      "react-hooks": reactHooks,
      "jsx-a11y": jsxA11y,
    },
    languageOptions: {
      globals: {
        ...globals.browser,
      },
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
    },
    settings: {
      react: { version: "19.0" },
    },
    rules: {
      // React 核心规则
      "react/jsx-key": "error",             // map 必须带 key
      "react/jsx-no-target-blank": "warn",  // target=_blank 需 rel=noreferrer
      "react/no-unescaped-entities": "warn",

      // Hooks 规则
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",

      // 可访问性 (渐进: warn 而非 error)
      "jsx-a11y/alt-text": "warn",
      "jsx-a11y/anchor-has-content": "warn",
      "jsx-a11y/click-events-have-key-events": "warn",
      "jsx-a11y/no-static-element-interactions": "warn",

      // TypeScript 严格度
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/no-explicit-any": "warn",

      // 代码质量
      "no-console": ["warn", { allow: ["error"] }],  // 允许 console.error，警告 console.log
      "no-debugger": "error",
    },
  },
);
