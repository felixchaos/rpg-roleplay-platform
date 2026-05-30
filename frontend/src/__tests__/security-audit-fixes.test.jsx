// 安全审计修复回归测试:#4 markdown URL scheme 白名单 / #5 ui-atlas 敏感字段脱敏
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { RpgMarkdown } from "../markdown-render.jsx";

// 把 renderInline 的输出(React 元素/字符串数组)拍平,找出所有 <a> 的 href
function hrefsOf(nodes) {
  const arr = Array.isArray(nodes) ? nodes : [nodes];
  const hrefs = [];
  for (const n of arr) {
    if (n && typeof n === "object" && n.type === "a") hrefs.push(n.props.href);
  }
  return hrefs;
}
function textOf(nodes) {
  const arr = Array.isArray(nodes) ? nodes : [nodes];
  return arr
    .map((n) => (typeof n === "string" ? n : n?.props?.children ?? ""))
    .join("");
}

describe("#4 markdown 链接 scheme 白名单", () => {
  const blocked = [
    "[x](javascript:alert(1))",
    "[x](JaVaScript:alert(1))",
    "[x](  javascript:alert(1))",
    "[x](java\tscript:alert(1))",
    "[x](data:text/html,<script>alert(1)</script>)",
    "[x](vbscript:msgbox(1))",
  ];
  it.each(blocked)("拦截危险 scheme: %s", (md) => {
    const out = RpgMarkdown.renderInline(md, "k");
    expect(hrefsOf(out)).toHaveLength(0); // 不渲染任何 href
    expect(textOf(out)).toContain("x"); // 降级为纯文本,文字仍在
  });

  const allowed = [
    ["[x](https://example.com)", "https://example.com"],
    ["[x](http://example.com)", "http://example.com"],
    ["[x](mailto:a@b.com)", "mailto:a@b.com"],
    ["[x](/library/123)", "/library/123"],
    ["[x](#anchor)", "#anchor"],
  ];
  it.each(allowed)("放行安全 URL: %s", (md, expected) => {
    const out = RpgMarkdown.renderInline(md, "k");
    expect(hrefsOf(out)).toContain(expected);
  });
});

describe("#5 ui-atlas 敏感字段脱敏", () => {
  let atlas;
  let origOffsetParent;
  beforeEach(async () => {
    document.body.innerHTML = "";
    // jsdom 无布局,offsetParent 恒为 null → isVisible 会全砍。stub 之让扫描跑起来。
    origOffsetParent = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetParent");
    Object.defineProperty(HTMLElement.prototype, "offsetParent", {
      configurable: true,
      get() {
        return this.parentNode;
      },
    });
    await import("../ui-atlas.js"); // import 时安装 window.__UI_ATLAS 单例
    atlas = window.__UI_ATLAS;
  });
  afterEach(() => {
    if (origOffsetParent) Object.defineProperty(HTMLElement.prototype, "offsetParent", origOffsetParent);
  });

  it("password / API key / SMTP 字段值不出现在 snapshot,普通字段保留", () => {
    // <main> 是扫描器识别的页面主体 scope 之一
    document.body.innerHTML = `
      <main>
        <label>Anthropic API Key<input name="anthropic_api_key" type="password" value="sk-LEAKED-123"></label>
        <label>SMTP 密码<input name="smtp_pw" type="text" value="hunter2"></label>
        <label>显示名<input name="display_name" type="text" value="alice"></label>
      </main>`;
    const snap = atlas.rescan();
    const blob = JSON.stringify(snap);
    expect(snap.forms.length).toBeGreaterThan(0); // 确认扫描确实采到了字段
    expect(blob).not.toContain("sk-LEAKED-123"); // API key 不外传
    expect(blob).not.toContain("hunter2"); // SMTP 密码不外传
    expect(blob).toContain("[REDACTED]"); // 敏感字段被脱敏占位
    expect(blob).toContain("alice"); // 非敏感字段照常采集
  });
});
