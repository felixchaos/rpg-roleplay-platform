// 站内帮助:平台页面 → 文档站嵌入页 slug 的唯一映射表。
//
// slug 就是 docs-site/src/content/docs/<slug>.md 的文件名。HelpDrawer 以 iframe 打开
// https://docs.stellatrix.icu/embed/<slug>/,嵌入页由 docs-site/src/pages/embed/[slug].astro
// 生成,规则是「根目录下除 index 以外的每一页」,所以 slug 不能是 'index',也不能带 '/'。
//
// 两道守卫:
//   · frontend/src/__tests__/help-slugs.test.js 断言这里的每个 slug 在 docs-site 里有对应文档页;
//   · docs-site/scripts/check-dist.mjs 在文档站构建后断言每个 slug 都生成了 /embed/<slug>/。
// 改名或删掉文档页时,已发出的桌面端还会打旧 slug,要在
// docs-site/src/embed-aliases.mjs 里登记兼容别名。
export const PAGE_HELP_SLUG = Object.freeze({
  scripts: 'scripts', 'scripts-import': 'scripts',
  cards: 'cards', 'cards-npc': 'cards', 'cards-online': 'cards',
  saves: 'saves', 'saves-branches': 'saves',
  settings: 'settings-models',
  'settings-models': 'settings-models',
  'settings-modelparams': 'settings-modelparams',
  'settings-modules': 'settings-modules',
  'settings-memory': 'settings-memory',
  'admin-users': 'admin',
  'md-editor': 'md-editor', tavern: 'tavern',
  profile: 'getting-started',
  me: 'account',
});

// 当前页面没有专属帮助时打开的页面。
export const DEFAULT_HELP_SLUG = 'getting-started';

export function helpSlugFor(page) {
  return Object.prototype.hasOwnProperty.call(PAGE_HELP_SLUG, page) ? PAGE_HELP_SLUG[page] : null;
}
