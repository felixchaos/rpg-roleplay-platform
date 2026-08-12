/* eslint.config.js — Flat config for ESLint 9.x
 *
 *   npx eslint src/
 *
 * 覆盖范围(维护者修订):**真正在跑的 .js / .jsx**,而不是只有 .ts/.tsx。
 * 原始提交只 lint `src/**\/*.ts(x)` —— 但本仓 43 个 .ts 文件**全部**在 `src/types/rust/` 下,
 * 是已废弃的 Rust 迁移留下的死类型(main 分支永远是 Python,见 CLAUDE.md),
 * 真正在跑的 353 个 .js/.jsx 一个都不检查。等于装了个只扫死代码的 linter。
 *
 * 规则取舍(目标:当前代码库零 error,让它能真正进 CI 卡住**新增**问题):
 *   · 浏览器/Node/测试全局按目录声明 —— 否则 window/document/localStorage 全报 no-undef(420 处),
 *     那是配置缺失不是代码问题。
 *   · `catch (_) {}` 是本仓的既有惯用写法(大量"失败不阻断"路径)→ no-empty 放行空 catch。
 *   · 未使用变量降为 warn + 允许 `_` 前缀,避免一次性铺开几百条 error 把 lint 变成噪音。
 *   · react-hooks/rules-of-hooks 暂为 warn(存量 18 处欠账,见下方 TODO);.ts/.tsx 新代码仍是 error。
 *   · exhaustive-deps 为 warn:本仓有大量**刻意**的依赖省略,都带 eslint-disable 注释说明理由。
 */
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactPlugin from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";
import globals from "globals";

export default tseslint.config(
  {
    ignores: [
      "node_modules/",
      "dist/",
      "vite.config.js",
      "vitest.config.js",
      "playwright.config.js",
      "e2e/",
      // 废弃的 Rust 迁移遗留类型(main 永远是 Python);不是活代码,不必 lint
      "src/types/rust/",
    ],
  },

  // ── 真正在跑的前端源码(.js / .jsx)────────────────────────────────
  {
    files: ["src/**/*.js", "src/**/*.jsx"],
    ...js.configs.recommended,
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.es2021,
        // 项目刻意安装的运行时全局(不是漏声明):
        //   getCaps —— catalog-helpers.js 装到 window,model-modals 顶部有注释说明
        //   __UI_ATLAS —— ui-atlas.js 的 window 单例
        getCaps: "readonly",
        __UI_ATLAS: "readonly",
      },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: {
      react: reactPlugin,
      "react-hooks": reactHooks,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      ...js.configs.recommended.rules,
      // 存量欠账 18 处(16 处在 mobile/pages/MobileSaves.jsx),真修是独立重构;
      // 先降 warn 让基线绿 —— linter 能进 CI 卡住**新增**问题才是它的价值所在。
      // TODO: 清完 MobileSaves 的条件式 hook 后升回 error。
      "react-hooks/rules-of-hooks": "warn",
      "react-hooks/exhaustive-deps": "warn",
      "no-empty": ["error", { allowEmptyCatch: true }],
      // 以下三条命中的都是**刻意**的正则写法(markdown 清洗器要匹配控制字符、
      // CJK 组合字符类),不是错误:
      "no-control-regex": "off",
      "no-misleading-character-class": "warn",
      "no-useless-escape": "warn",
      "no-unused-vars": ["warn", {
        argsIgnorePattern: "^_",
        varsIgnorePattern: "^_",
        caughtErrorsIgnorePattern: "^_",
      }],
    },
  },

  // ── TypeScript(新代码走这条;当前仅少量非 types/rust 的 ts)────────
  {
    files: ["src/**/*.ts", "src/**/*.tsx"],
    extends: [...tseslint.configs.recommended],
    languageOptions: {
      globals: { ...globals.browser, ...globals.es2021 },
    },
    plugins: {
      react: reactPlugin,
      "react-hooks": reactHooks,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-unused-vars": ["warn", {
        argsIgnorePattern: "^_",
        varsIgnorePattern: "^_",
        caughtErrorsIgnorePattern: "^_",
      }],
    },
  },

  // ── 测试文件:补 vitest 全局 ─────────────────────────────────────
  {
    files: ["src/__tests__/**/*.{js,jsx,ts,tsx}", "src/**/*.test.{js,jsx,ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node, ...globals.vitest },
    },
    rules: {
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": "off",
    },
  },
);
