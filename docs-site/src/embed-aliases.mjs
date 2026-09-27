// 嵌入视图的旧 slug 兼容表:已发出的客户端里写死的 slug → 现在真实存在的文档页 id。
//
// 站内帮助(frontend/src/components/HelpDrawer.jsx)以 iframe 打开 /embed/<slug>/,slug
// 编进了前端产物。桌面端安装包带着一份冻结的前端,改名或删页之后旧客户端仍会打旧
// slug,拿到的是 404。这里登记的每一项,pages/embed/[slug].astro 都会额外生成一张
// /embed/<旧 slug>/ 页面,内容取自右侧的真实页面(纯静态,不依赖 CF 的 _redirects,
// 本地 astro preview 也能看到)。
//
// 只增不删:桌面端 v1.88.1 及之前的前端把站内帮助的默认页、欢迎页、个人页都指向
// 'intro',而文档站从来没有 intro 页。scripts/check-dist.mjs 在构建后逐项断言这些
// 别名页存在。
export const EMBED_SLUG_ALIASES = Object.freeze({
  intro: 'getting-started',
});
