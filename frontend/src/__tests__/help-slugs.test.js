/**
 * help-slugs.test.js — 站内帮助 slug 必须指向文档站真实存在的页面。
 *
 * 背景:PAGE_HELP_SLUG 曾把欢迎页、个人页和默认兜底都指向 'intro',而文档站从来
 * 没有 intro 页,站内帮助一打开就是 404 页(页上还挂着反馈组件,收到过一批
 * page=/embed/intro/ 的反馈)。这里把前端这一侧锁死:
 *   · components/help-slugs.js 里的每个 slug 在 docs-site/src/content/docs 下有 .md/.mdx;
 *   · slug 符合嵌入页生成规则(pages/embed/[slug].astro:不是 index,不含 '/');
 *   · 任何 __openHelp('字面量') 调用传的也必须是真实 slug;
 *   · platform-app.jsx 不再私藏一份映射表。
 * 文档站那一侧(构建产物里真的有 /embed/<slug>/)由 docs-site/scripts/check-dist.mjs 断言。
 */
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync, readdirSync, statSync } from 'fs';
import { resolve, join } from 'path';
import { PAGE_HELP_SLUG, DEFAULT_HELP_SLUG, helpSlugFor } from '../components/help-slugs.js';

const SRC = resolve(__dirname, '..');
const DOCS_DIR = resolve(__dirname, '../../../docs-site/src/content/docs');

function docExists(slug) {
  return existsSync(join(DOCS_DIR, `${slug}.md`)) || existsSync(join(DOCS_DIR, `${slug}.mdx`));
}

const ALL_SLUGS = [...new Set([...Object.values(PAGE_HELP_SLUG), DEFAULT_HELP_SLUG])];

describe('站内帮助 slug 指向真实文档页', () => {
  it('docs-site 文档目录存在', () => {
    expect(existsSync(DOCS_DIR)).toBe(true);
  });

  it.each(ALL_SLUGS)('%s 有对应的文档页且能生成嵌入页', (slug) => {
    expect(typeof slug).toBe('string');
    expect(slug).not.toBe('');
    expect(slug).not.toBe('index');
    expect(slug.includes('/')).toBe(false);
    expect(docExists(slug)).toBe(true);
  });

  it('helpSlugFor 只认映射表里的页面', () => {
    expect(helpSlugFor('scripts')).toBe('scripts');
    expect(helpSlugFor('no-such-page')).toBeNull();
    expect(helpSlugFor('constructor')).toBeNull();
  });
});

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === '__tests__' || name === 'node_modules') continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(jsx?|tsx?)$/.test(name)) out.push(p);
  }
  return out;
}

describe('__openHelp 调用点不写死无效 slug', () => {
  const files = walk(SRC);

  it('扫描到了源码文件', () => {
    expect(files.length).toBeGreaterThan(10);
  });

  it('所有 __openHelp(...) 里的字符串字面量都是真实 slug', () => {
    const bad = [];
    for (const f of files) {
      const src = readFileSync(f, 'utf-8');
      for (const call of src.matchAll(/__openHelp\s*\(([^)]*)\)/g)) {
        for (const lit of call[1].matchAll(/['"`]([^'"`]*)['"`]/g)) {
          const slug = lit[1];
          if (slug === 'index' || slug.includes('/') || !docExists(slug)) bad.push(`${f}: ${slug}`);
        }
      }
    }
    expect(bad).toEqual([]);
  });

  it('platform-app.jsx 从 help-slugs.js 取映射,不再自带一份', () => {
    const src = readFileSync(join(SRC, 'platform-app.jsx'), 'utf-8');
    expect(src).toMatch(/from\s+['"]\.\/components\/help-slugs\.js['"]/);
    expect(src).not.toMatch(/const\s+PAGE_HELP_SLUG\s*=/);
  });
});
