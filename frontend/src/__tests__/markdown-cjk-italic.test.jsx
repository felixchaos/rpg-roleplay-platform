// 回归测试:markdown `_..._` 斜体规则不应在中文(CJK)字符旁误命中。
// 起因:旧规则 /(?<!\w)_(...)_(?!\w)/ 的 \w 不含 CJK,导致「设定_补充_」这类
// 中文里的下划线被渲染成 <em>。修复后两侧断言扩展到 [\w一-鿿]。
import { describe, it, expect } from "vitest";
import { RpgMarkdown } from "../markdown-render.jsx";

// 递归收集所有指定 tag 的 React 元素
function collectTags(nodes, tag, out = []) {
  const arr = Array.isArray(nodes) ? nodes : [nodes];
  for (const n of arr) {
    if (n && typeof n === "object" && n.type === tag) out.push(n);
    if (n && typeof n === "object" && n.props && n.props.children != null) {
      collectTags(n.props.children, tag, out);
    }
  }
  return out;
}

describe("markdown 行内斜体 — CJK 误命中防护", () => {
  it("中文字符旁的下划线不渲染为斜体", () => {
    const out = RpgMarkdown.renderInline("这是设定_补充_内容,变量_x_也保留", "k");
    expect(collectTags(out, "em").length).toBe(0);
  });

  it("拉丁文的 _italic_ 仍正常渲染为斜体", () => {
    const out = RpgMarkdown.renderInline("this is _italic_ here", "k");
    const ems = collectTags(out, "em");
    expect(ems.length).toBe(1);
  });

  it("空白/行首边界的下划线仍正常斜体(中文词也可显式强调)", () => {
    // 这里下划线外侧是行首与空格(非 CJK、非 \w 邻接)→ 属合法 markdown 强调,应斜体。
    // 修复只拦「被汉字夹住」的下划线(如 设定_补充_内容),不影响这种显式强调。
    const out = RpgMarkdown.renderInline("_提示_ 开头", "k");
    expect(collectTags(out, "em").length).toBe(1);
  });
});
