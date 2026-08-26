/**
 * platform-nav-parity.test.js —— 「去配 key」这类跳转必须真的能跳。
 *
 * 前科(2026-08-26,群反馈「已有 apikey,这个界面点不了配置」):
 * Platform 早已从 hash 路由迁到 History 路由(见 router.js 头部),PlatformApp 只监听
 * popstate / pl-navigate。但迁移漏了一批**裸 hash 跳转**没改:
 *   AgentModelPicker「去配 key」/ GenerateImageModal / MediaStudio 锚点 /
 *   GameConfirmStrip / game-console / tavern×2 / console-assistant-navigation
 * 它们仍写 `window.location.hash = 'settings-models'`,点下去只把 URL 尾巴改掉,
 * 画面纹丝不动。而**没配 key 的用户恰恰只能从这个按钮走到配置页** —— 新人上手路径死锁。
 *
 * 锁两件事:① 源码里不准再出现导航用的 location.hash 写入;② plGoto 两种宿主都要跳对。
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'fs';
import { resolve, join } from 'path';
import { plGoto, plIsPlatformDoc } from '../router.js';
import { credApiIdSet } from '../components/catalog-helpers.js';

const SRC = resolve(__dirname, '..');

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === '__tests__') continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(js|jsx)$/.test(name)) out.push(p);
  }
  return out;
}

/** 去掉注释,免得 router.js 里那段「前科说明」把守卫自己绊倒。 */
function stripComments(code) {
  return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

describe('平台内跳转不准再走 hash 路由', () => {
  it('src 下没有任何 location.hash 赋值', () => {
    const offenders = [];
    for (const file of walk(SRC)) {
      const code = stripComments(readFileSync(file, 'utf-8'));
      if (/location\s*\.\s*hash\s*=/.test(code)) offenders.push(file.replace(SRC, 'src'));
    }
    expect(
      offenders,
      `这些文件在用 location.hash 导航,而 PlatformApp 只监听 popstate / pl-navigate ——\n`
      + `点了不会有任何反应。改用 router.js 的 plGoto(pageId):\n  ${offenders.join('\n  ')}`,
    ).toEqual([]);
  });

  it('没有指向平台页的裸 <a href="#pageId"> 锚点', () => {
    const offenders = [];
    for (const file of walk(SRC)) {
      const code = stripComments(readFileSync(file, 'utf-8'));
      // href="#" (占位) 放行;href="#settings-models" 这种指向 page id 的不放行
      const m = code.match(/href=["']#[a-z][a-z0-9-]+["']/gi);
      if (m) offenders.push(`${file.replace(SRC, 'src')}: ${m.join(', ')}`);
    }
    expect(offenders, `裸 hash 锚点点了不换页,改 plGoto:\n  ${offenders.join('\n  ')}`).toEqual([]);
  });
});

describe('plGoto 两种宿主', () => {
  let openSpy;
  beforeEach(() => {
    openSpy = vi.fn(() => ({}));
    vi.stubGlobal('open', openSpy);
    window.open = openSpy;
  });
  afterEach(() => {
    delete window.__PL_ROUTER__;
    vi.unstubAllGlobals();
  });

  it('Platform SPA 内 → 派发 pl-navigate,不整页刷新', () => {
    window.__PL_ROUTER__ = true;
    expect(plIsPlatformDoc()).toBe(true);
    const seen = [];
    const onNav = (e) => seen.push(e.detail);
    window.addEventListener('pl-navigate', onNav);
    plGoto('settings-models');
    window.removeEventListener('pl-navigate', onNav);
    expect(seen).toEqual(['settings-models']);
    expect(window.location.pathname).toBe('/settings-models');
    expect(openSpy).not.toHaveBeenCalled();
  });

  it('独立文档(游戏台)→ 新标签打开该路径', () => {
    expect(plIsPlatformDoc()).toBe(false);
    plGoto('settings-models');
    expect(openSpy).toHaveBeenCalledWith('/settings-models', '_blank');
  });
});

describe('credApiIdSet:「配好了没」以后端 configured 为准', () => {
  it('免鉴权凭据(auth_mode=none,无 key)算已配置', () => {
    const ids = credApiIdSet({ items: [
      { api_id: 'ollama', enabled: true, auth_mode: 'none', has_credential: false, configured: true },
    ] });
    expect(ids.has('ollama'), '本地免 Key 模型被判「尚未配置任何 API key」,供应商下拉会置灰').toBe(true);
  });

  it('普通 key 凭据照常算已配置', () => {
    const ids = credApiIdSet({ items: [
      { api_id: 'deepseek', enabled: true, auth_mode: 'api_key', has_credential: true, configured: true },
    ] });
    expect(ids.has('deepseek')).toBe(true);
  });

  it('停用 / 未配置的不算', () => {
    const ids = credApiIdSet({ items: [
      { api_id: 'deepseek', enabled: false, has_credential: true, configured: true },
      { api_id: 'openai', enabled: true, has_credential: false, configured: false },
    ] });
    expect(Array.from(ids)).toEqual([]);
  });

  it('老后端(没有 configured 字段)回退旧谓词', () => {
    const ids = credApiIdSet({ items: [
      { api_id: 'deepseek', enabled: true, has_credential: true },
      { api_id: 'ollama', enabled: true, auth_mode: 'none' },
    ] });
    expect(ids.has('deepseek')).toBe(true);
    expect(ids.has('ollama')).toBe(true);
  });
});
