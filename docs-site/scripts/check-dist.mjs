// 文档站构建产物检查,接在 `astro build` 之后运行(npm run build)。任何一条不满足就
// 以非零码退出,别把有问题的 dist 部署上去。
//
//  1. 404 页不挂反馈组件(data-doc-feedback / data-vote 都不能出现)。Starlight 升级后
//     路由结构若有变化,DocFooter 里按 id 排除 404 的判断会先在这里暴露。
//  2. 每个 html 至多 1 个反馈组件;每个嵌入页 dist/embed/<slug>/index.html 恰好 1 个;
//     快速开始页恰好 1 个(确认主站页脚没有整体丢掉反馈组件)。
//  3. 站内帮助要打开的每个 slug 都生成了嵌入页:前端 help-slugs.js 的全部映射值和默认页,
//     加上 src/embed-aliases.mjs 登记的旧 slug(已发出的客户端还在用,至少包含 intro)。
//  4. _redirects 已拷进 dist 且带着主站 /intro 的兼容规则。
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const DIST = join(ROOT, 'dist');
const HELP_SLUGS_JS = resolve(ROOT, '../frontend/src/components/help-slugs.js');
// 已发出的客户端里写死、必须一直能打开的旧 slug。只增不删。
const SHIPPED_LEGACY_SLUGS = ['intro'];

const errors = [];
const fail = (m) => errors.push(m);

// 只数 HTML 属性形式的出现(前面是空白,后面是 = / 空白 / > / /),脚本里的
// '[data-doc-feedback]' 选择器字符串不算,免得 Astro 哪天把脚本内联进页面后误报。
function countAttr(html, name) {
  const re = new RegExp('(?<=\\s)' + name + '(?=[\\s=>/])', 'g');
  return (html.match(re) || []).length;
}

function walkHtml(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walkHtml(p, out);
    else if (name.endsWith('.html')) out.push(p);
  }
  return out;
}

if (!existsSync(DIST)) {
  console.error('check-dist: 找不到 dist/,先跑 astro build');
  process.exit(1);
}

const feedbackCount = (file) => countAttr(readFileSync(file, 'utf-8'), 'data-doc-feedback');

// 1. 404 页
const page404 = join(DIST, '404.html');
if (!existsSync(page404)) {
  fail('dist/404.html 不存在');
} else {
  const html = readFileSync(page404, 'utf-8');
  if (countAttr(html, 'data-doc-feedback') !== 0) fail('dist/404.html 里有反馈组件(data-doc-feedback)');
  if (countAttr(html, 'data-vote') !== 0) fail('dist/404.html 里有投票按钮(data-vote)');
}

// 2. 每页的反馈组件数量
const htmlFiles = walkHtml(DIST);
for (const f of htmlFiles) {
  const n = feedbackCount(f);
  if (n > 1) fail(`${relative(DIST, f)} 有 ${n} 个反馈组件,至多 1 个`);
}
const embedDir = join(DIST, 'embed');
const embedPages = existsSync(embedDir)
  ? readdirSync(embedDir)
      .map((slug) => join(embedDir, slug, 'index.html'))
      .filter((p) => existsSync(p))
  : [];
if (embedPages.length === 0) fail('dist/embed/ 下没有任何嵌入页');
for (const f of embedPages) {
  const n = feedbackCount(f);
  if (n !== 1) fail(`${relative(DIST, f)} 有 ${n} 个反馈组件,应恰好 1 个`);
}
const canary = join(DIST, 'getting-started', 'index.html');
if (!existsSync(canary)) fail('dist/getting-started/index.html 不存在');
else if (feedbackCount(canary) !== 1) fail('dist/getting-started/index.html 应恰好 1 个反馈组件');

// 3. 站内帮助会打开的 slug
const { EMBED_SLUG_ALIASES } = await import(pathToFileURL(join(ROOT, 'src/embed-aliases.mjs')).href);
if (!existsSync(HELP_SLUGS_JS)) {
  fail(`找不到前端的帮助映射 ${relative(ROOT, HELP_SLUGS_JS)},无法核对站内帮助 slug`);
} else {
  const { PAGE_HELP_SLUG, DEFAULT_HELP_SLUG } = await import(pathToFileURL(HELP_SLUGS_JS).href);
  const wanted = new Map();
  for (const [page, slug] of Object.entries(PAGE_HELP_SLUG)) wanted.set(slug, `前端 PAGE_HELP_SLUG.${page}`);
  wanted.set(DEFAULT_HELP_SLUG, '前端 DEFAULT_HELP_SLUG');
  for (const slug of Object.keys(EMBED_SLUG_ALIASES)) wanted.set(slug, 'embed-aliases.mjs 兼容别名');
  for (const slug of SHIPPED_LEGACY_SLUGS) {
    if (!wanted.has(slug)) wanted.set(slug, '已发出客户端的旧 slug');
  }
  for (const [slug, from] of wanted) {
    if (!existsSync(join(embedDir, slug, 'index.html'))) {
      fail(`dist/embed/${slug}/index.html 不存在(来源:${from})`);
    }
  }
}

// 4. _redirects
const redirects = join(DIST, '_redirects');
if (!existsSync(redirects)) {
  fail('dist/_redirects 不存在(public/_redirects 没被拷进产物)');
} else {
  const rules = readFileSync(redirects, 'utf-8')
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'));
  for (const from of ['/intro', '/intro/']) {
    if (!rules.some((l) => l.split(/\s+/)[0] === from)) fail(`dist/_redirects 缺少 ${from} 的兼容规则`);
  }
  if (rules.some((l) => /^\/embed\/.*\*/.test(l.split(/\s+/)[0]))) {
    fail('dist/_redirects 里有 /embed/ 下的通配规则,会把正常的嵌入页一起重定向走');
  }
}

if (errors.length) {
  console.error(`check-dist: ${errors.length} 项不通过`);
  for (const e of errors) console.error('  - ' + e);
  process.exit(1);
}
console.log(`check-dist: 通过(${htmlFiles.length} 个 html,${embedPages.length} 个嵌入页)`);
