# TypeScript 渐进迁移指南

## 当前状态

- `tsconfig.json` 已配置 `allowJs: true` + `checkJs: false`
- 现有 `.jsx` / `.js` 文件**不受**类型检查约束(初始阶段)
- 新建 `.ts` / `.tsx` 文件**受完整类型检查**
- `npm run typecheck` 当前应零错误退出

## 渐进迁移策略

### 第一阶段(当前):新文件用 TS

- 所有新功能一律写 `.ts` / `.tsx`
- 已有 `.jsx` 可暂时保持不动
- ts-rs 生成的 Rust 类型(`src/types/rust/`) 可直接 import

### 第二阶段:逐步迁移 .jsx → .tsx

按以下顺序逐个迁移,每次改一个文件,修完 typecheck 再合并:

1. 纯工具函数文件(无 JSX): `api-client.js`, `data-loader.js`
2. 叶子组件(无子组件依赖): `game-icons.jsx`, `markdown-render.jsx`
3. 中层组件,最后迁移入口文件(`entries/`)

### 第三阶段:开启 checkJs

全部迁完后,在 `tsconfig.json` 把 `checkJs` 改回 `true`。

## 使用 ts-rs 生成的类型

```ts
// 单个类型
import type { GameStateData } from "@/types/GameStateData";

// 事件类型
import type { WorldlineCreated } from "@/events/WorldlineCreated";
```

路径别名 `@/types/*` 对应 `src/types/rust/*`,`@/events/*` 对应 `src/types/rust/events/*`。

## 常用命令

```bash
npm run typecheck      # 运行 tsc 类型检查(不生成文件)
npm run gen:types      # 从 Rust crate 重新生成 .ts 类型定义
```

## 已知限制

现有代码大量使用 `window.__xxx` 动态属性和 UMD 风格的 `window.React`。
这些在迁移时需要:
1. 声明扩展接口: `declare global { interface Window { api: ...; } }`
2. 或改为正常 ESM import
```
