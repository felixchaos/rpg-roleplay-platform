/**
 * HelpDrawer.jsx — 软件内帮助系统基础设施
 *
 * 暴露:
 *   <HelpDrawerRoot />  —— 挂到 platform-app 根节点一次即可
 *   window.__openHelp(slug)  —— 任意代码打开对应帮助页
 *
 * Props (HelpDrawer 直接使用):
 *   open    : bool
 *   slug    : string   // __index.json 里的键
 *   onClose : () => void
 *
 * 加载机制:
 *   import.meta.glob 在构建时静态收录 frontend/help/*.md 的原始文本,
 *   运行时按 slug 查表 → 无额外网络请求。
 *   Vite 5 原生支持 { query: '?raw', eager: true },无需改 vite.config.js。
 */
import React from 'react';
import CSModal  from '@cloudscape-design/components/modal';
import Box      from '@cloudscape-design/components/box';
import Button   from '@cloudscape-design/components/button';
import { RpgMarkdown } from '../markdown-render.jsx';
import INDEX from '../../help/__index.json';

// ── 静态收录所有帮助 md(相对于本文件: src/components → ../../help/) ─────────
const MD_MODULES = import.meta.glob('../../help/*.md', { query: '?raw', eager: true });

/**
 * 将 glob 结果 key(如 "../../help/scripts.md")映射到原始文本。
 * Vite raw import 的 default export 即文件文本。
 */
function resolveContent(slug) {
  const entry = INDEX[slug];
  if (!entry) return null;
  const key = `../../help/${entry.file}`;
  const mod = MD_MODULES[key];
  return mod ? (mod.default ?? mod) : null;
}

// ── HelpDrawer ──────────────────────────────────────────────────────────────
export function HelpDrawer({ open, slug, onClose }) {
  const entry   = INDEX[slug] ?? null;
  const title   = entry?.title ?? slug ?? '帮助';
  const content = slug ? resolveContent(slug) : null;

  return (
    <CSModal
      visible={open}
      onDismiss={onClose}
      header={
        <Box variant="h2">
          <span style={{ fontSize: '0.8em', color: 'var(--color-text-body-secondary, #687078)', marginRight: 8 }}>
            帮助
          </span>
          {title}
        </Box>
      }
      footer={
        <Box float="right">
          <Button variant="primary" onClick={onClose}>关闭</Button>
        </Box>
      }
      size="large"
    >
      {content != null
        ? <RpgMarkdown.Block text={content} streaming={false} />
        : <Box color="text-body-secondary">
            {slug ? `未找到帮助文档: ${slug}` : '请指定帮助主题。'}
          </Box>
      }
    </CSModal>
  );
}

// ── HelpDrawerRoot —— 挂载一次,监听 window.__openHelp ─────────────────────
const OPEN_EVENT = 'help:open';

export function HelpDrawerRoot() {
  const [state, setState] = React.useState({ open: false, slug: '' });

  // 挂全局 API
  React.useEffect(() => {
    window.__openHelp = (slug) => {
      window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { slug } }));
    };
    const handler = (e) => setState({ open: true, slug: e.detail?.slug ?? '' });
    window.addEventListener(OPEN_EVENT, handler);
    return () => {
      window.removeEventListener(OPEN_EVENT, handler);
      delete window.__openHelp;
    };
  }, []);

  const handleClose = React.useCallback(() => setState(s => ({ ...s, open: false })), []);

  return (
    <HelpDrawer
      open={state.open}
      slug={state.slug}
      onClose={handleClose}
    />
  );
}

export default HelpDrawerRoot;
