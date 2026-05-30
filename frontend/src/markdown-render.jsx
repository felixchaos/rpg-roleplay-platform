/**
 * markdown-render.jsx — 与 Claude 网页同款的轻量 Markdown 渲染。
 *
 * 暴露 window.RpgMarkdown:
 *   <RpgMarkdown.Block text="..." streaming={false} />
 *
 * 支持:
 *   · 段落 (空行分隔)
 *   · 标题 # / ## / ###
 *   · 无序列表 - / *
 *   · 有序列表 1. / 2.
 *   · 引用 > ...
 *   · 代码块 ```lang\n...\n```
 *   · 行内: **bold** / __bold__ / *italic* / _italic_ / `code` / [text](url) /
 *           ~~删除线~~
 *   · 水平线 ---
 *
 * 设计:
 *   block 级 parser 一次扫描产出 AST,再用 React 渲染。
 *   inline 用 token 化的方式渲染,避免 dangerouslySetInnerHTML (安全 + 流式 OK)。
 *   流式 (streaming=true) 模式下,允许最后一段未闭合标记 (例如刚打到 `**` 还没第二个 `**`),
 *   按字面文本显示,等下一帧自然补齐。
 */
import React from 'react';

// ── inline 解析 ─────────────────────────────────────────────
// 顺序很重要: 长 token 在前
const INLINE_RULES = [
  { re: /\*\*([^*\n]+?)\*\*/g, tag: "strong" },
  { re: /__([^_\n]+?)__/g, tag: "strong" },
  { re: /~~([^~\n]+?)~~/g, tag: "del" },
  { re: /\*([^*\n]+?)\*/g, tag: "em" },
  { re: /(?<!\w)_([^_\n]+?)_(?!\w)/g, tag: "em" },
  { re: /`([^`\n]+?)`/g, tag: "code" },
  { re: /\[([^\]\n]+?)\]\(([^)\s]+)\)/g, tag: "a" },
];

// scheme 白名单:只放行 http/https/mailto/tel + 站内相对/锚点。
// 其余(javascript:/data:/vbscript: 等)一律视为不安全 → 渲染纯文本不带 href(CWE-79)。
function safeUrl(url) {
  if (!url) return null;
  // 去掉控制字符/空白/零宽字符,防 "java\tscript:" / 零宽绕过
  // eslint-disable-next-line no-control-regex
  const cleaned = String(url)
    .replace(/[\u0000-\u0020\u007f-\u00a0\u200b-\u200f\u2028\u2029\ufeff]/g, "")
    .trim();
  if (!cleaned) return null;
  // 带 scheme 的(形如 "xxx:")必须命中白名单;相对路径/锚点(无 scheme)放行
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(cleaned)) {
    return /^(https?|mailto|tel):/i.test(cleaned) ? cleaned : null;
  }
  return cleaned;
}

function renderInline(text, keyPrefix) {
  // 找出所有 match,按位置排序,non-overlap
  const hits = [];
  INLINE_RULES.forEach((rule, ri) => {
    rule.re.lastIndex = 0;
    let m;
    while ((m = rule.re.exec(text)) !== null) {
      hits.push({
        start: m.index,
        end: m.index + m[0].length,
        tag: rule.tag,
        text: m[1],
        href: rule.tag === "a" ? m[2] : null,
      });
    }
  });
  hits.sort((a, b) => a.start - b.start || a.end - b.end);
  // greedy non-overlap
  const picked = [];
  let lastEnd = 0;
  for (const h of hits) {
    if (h.start >= lastEnd) {
      picked.push(h);
      lastEnd = h.end;
    }
  }
  if (!picked.length) return text;
  const out = [];
  let cur = 0;
  let kid = 0;
  for (const h of picked) {
    if (h.start > cur) out.push(text.slice(cur, h.start));
    const k = `${keyPrefix}-${kid++}`;
    if (h.tag === "a") {
      const href = safeUrl(h.href);
      if (href) {
        out.push(React.createElement("a", {
          key: k, href, target: "_blank", rel: "noopener noreferrer",
        }, h.text));
      } else {
        // 不安全 scheme(javascript:/data: 等)→ 降级为纯文本,绝不进 href
        out.push(h.text);
      }
    } else {
      out.push(React.createElement(h.tag, { key: k }, h.text));
    }
    cur = h.end;
  }
  if (cur < text.length) out.push(text.slice(cur));
  return out;
}

// ── block 级解析 ────────────────────────────────────────────
function parseBlocks(text) {
  const lines = (text || "").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // 空行 → 段落分隔
    if (!line.trim()) { i++; continue; }
    // 水平线
    if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(line)) {
      blocks.push({ type: "hr" });
      i++;
      continue;
    }
    // 标题
    const hm = /^(#{1,6})\s+(.+)$/.exec(line);
    if (hm) {
      blocks.push({ type: "heading", level: hm[1].length, text: hm[2].trim() });
      i++;
      continue;
    }
    // 代码块
    if (/^```/.test(line)) {
      const lang = line.slice(3).trim();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) {
        buf.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++; // skip closing ```
      blocks.push({ type: "code", lang, text: buf.join("\n") });
      continue;
    }
    // 引用 - 连续 > 行合并
    if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      blocks.push({ type: "quote", text: buf.join("\n") });
      continue;
    }
    // 无序列表
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
        i++;
      }
      blocks.push({ type: "ul", items });
      continue;
    }
    // 有序列表
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      blocks.push({ type: "ol", items });
      continue;
    }
    // 段落 - 连续非空非特殊行合并
    const buf = [line];
    i++;
    while (i < lines.length) {
      const nl = lines[i];
      if (!nl.trim()) break;
      if (/^(#{1,6})\s+/.test(nl)) break;
      if (/^```/.test(nl)) break;
      if (/^>\s?/.test(nl)) break;
      if (/^\s*[-*]\s+/.test(nl)) break;
      if (/^\s*\d+\.\s+/.test(nl)) break;
      if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(nl)) break;
      buf.push(nl);
      i++;
    }
    blocks.push({ type: "p", text: buf.join("\n") });
  }
  return blocks;
}

// ── React 渲染 ──────────────────────────────────────────────
function Block({ text, streaming, className }) {
  const blocks = React.useMemo(() => parseBlocks(text || ""), [text]);
  return React.createElement(
    "div",
    { className: className || "rpg-md" },
    ...blocks.map((b, i) => renderBlock(b, i, streaming && i === blocks.length - 1))
  );
}

function renderBlock(b, i, isLast) {
  const k = `b${i}`;
  if (b.type === "hr") return React.createElement("hr", { key: k });
  if (b.type === "heading") {
    return React.createElement(
      "h" + Math.min(6, b.level), { key: k }, renderInline(b.text, k)
    );
  }
  if (b.type === "code") {
    return React.createElement(
      "pre", { key: k, "data-lang": b.lang || "" },
      React.createElement("code", null, b.text + (isLast ? "" : ""))
    );
  }
  if (b.type === "quote") {
    // 引用内部允许多行 inline
    const lines = b.text.split("\n");
    return React.createElement(
      "blockquote", { key: k },
      ...lines.map((ln, j) => React.createElement(
        "p", { key: `${k}-${j}` }, renderInline(ln, `${k}-${j}`),
        isLast && j === lines.length - 1 ? React.createElement("span", { className: "gc-cursor", key: "c" }) : null,
      ))
    );
  }
  if (b.type === "ul") {
    return React.createElement(
      "ul", { key: k },
      ...b.items.map((it, j) => React.createElement(
        "li", { key: `${k}-${j}` }, renderInline(it, `${k}-${j}`)
      ))
    );
  }
  if (b.type === "ol") {
    return React.createElement(
      "ol", { key: k },
      ...b.items.map((it, j) => React.createElement(
        "li", { key: `${k}-${j}` }, renderInline(it, `${k}-${j}`)
      ))
    );
  }
  // paragraph (默认)
  return React.createElement(
    "p", { key: k },
    ...[].concat(renderInline(b.text, k)),
    isLast ? React.createElement("span", { className: "gc-cursor", key: "c" }) : null
  );
}

window.RpgMarkdown = { Block, parseBlocks, renderInline };
export const RpgMarkdown = { Block, parseBlocks, renderInline };
