/**
 * ui-atlas.js — 自动 UI 感知系统 (task 109b-1)
 * ---------------------------------------------------------------
 * 挂在 window.__UI_ATLAS 单例，暴露 API：
 *   .current          — 最新 atlas snapshot（DOM 变化自动重算）
 *   .rescan()         — 主动重扫，返回 snapshot
 *   .setField(formId, fieldKey, value) → { ok, error? }
 *   .click(formId | "global", actionLabel) → { ok, error? }
 *   .subscribe(cb)    → unsubscribe 函数
 *
 * 设计要点：
 *   - MutationObserver + input/change capture 双路侦测
 *   - debounce 200ms 智能节流，DOM 持续变动时持续等待
 *   - scan < 50ms（querySelectorAll 一次性拿，不 N+1）
 *   - setField 使用 React 18 兼容 native setter
 *   - subscribe 回调走 microtask，不同步阻塞
 *
 * 字段 key 优先级：
 *   data-cap-field > <label> 文本 > aria-label > name attribute
 *
 * form_id 推断：
 *   [data-form-id] 祖先 > modal 第一个 <h2> 的 hash > modal class 后缀 > "page"
 */
(function () {
  "use strict";

  if (window.__UI_ATLAS) {
    // 已初始化，直接返回（防 HMR 重复执行）
    return;
  }

  // ── 工具函数 ────────────────────────────────────────────────
  function slugify(text) {
    if (!text) return "";
    return String(text)
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "_")
      .replace(/[^\w一-龥]/g, "")
      .slice(0, 40);
  }

  function simpleHash(str) {
    // DJB2 → 4位 hex 后缀，给 form_id 加扰动防冲突
    let h = 5381;
    for (let i = 0; i < str.length; i++) {
      h = ((h << 5) + h) ^ str.charCodeAt(i);
    }
    return (h >>> 0).toString(16).slice(0, 4);
  }

  /** 获取元素可用 CSS 选择器（尽量唯一） */
  function getSelector(el) {
    if (!el) return "";
    if (el.id) return "#" + CSS.escape(el.id);
    // 用 data-cap-field 属性
    const capField = el.getAttribute("data-cap-field");
    if (capField) {
      const tag = el.tagName.toLowerCase();
      return `${tag}[data-cap-field="${CSS.escape(capField)}"]`;
    }
    // 用 name
    if (el.name) {
      const tag = el.tagName.toLowerCase();
      return `${tag}[name="${CSS.escape(el.name)}"]`;
    }
    // 简单路径（最多 4 层）
    const parts = [];
    let cur = el;
    for (let i = 0; i < 4 && cur && cur !== document.body; i++) {
      let seg = cur.tagName.toLowerCase();
      const cls = Array.from(cur.classList).slice(0, 2).join(".");
      if (cls) seg += "." + cls;
      parts.unshift(seg);
      cur = cur.parentElement;
    }
    return parts.join(" > ");
  }

  /** 从最近祖先 label 提取文本 */
  function getLabelText(el) {
    // 1. <label for="id"> 关联
    if (el.id) {
      const lbl = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lbl) return lbl.textContent.replace(/\s*\*\s*/g, "").trim();
    }
    // 2. 包裹的 <label>
    const parentLabel = el.closest("label");
    if (parentLabel) {
      // 排除 input 自身文本
      const clone = parentLabel.cloneNode(true);
      clone.querySelectorAll("input, select, textarea").forEach((e) => e.remove());
      const t = clone.textContent.replace(/\s*\*\s*/g, "").trim();
      if (t) return t;
    }
    // 3. 紧邻的前兄弟 <label>（.pl-field 模式）
    const field = el.closest(".pl-field");
    if (field) {
      const lbl = field.querySelector("label");
      if (lbl) {
        const clone = lbl.cloneNode(true);
        clone.querySelectorAll(".pl-field-req, span, em").forEach((e) => {
          // 仅移除 req 标记（* 号），保留括号说明
          if (e.classList.contains("pl-field-req")) e.remove();
        });
        const t = clone.textContent.replace(/\s*\*\s*/g, "").trim();
        if (t) return t;
      }
    }
    return "";
  }

  /** 元素是否可见（z-index 判断 modal 时用） */
  function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    return (
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.opacity !== "0" &&
      el.offsetParent !== null
    );
  }

  /** 检测 modal / dialog 容器
   * 策略：
   * 1. 查找 [role=dialog] 或"直接子元素里有 header/footer"的 modal 容器
   *    （.pl-modal 直接子元素是 header.pl-modal-head + div.pl-modal-form + footer）
   * 2. 也接受在固定位置 / z-index 极高（>=100）且有字段的元素
   * 3. 去掉祖先-子关系的重复
   */
  function findModalContainers() {
    const raw = [];
    // task 119: 助手 panel / tweaks 不算 "modal" — 这是助手自己的 UI,不该被 LLM 操作
    const _excluded = (el) => el.closest && el.closest(".cap-root, .tweaks-panel, [data-cap-exclude]");

    // 优先：[role=dialog]
    document.querySelectorAll('[role="dialog"]').forEach((el) => {
      if (_excluded(el)) return;
      if (isVisible(el) && el.querySelector("input, select, textarea")) raw.push(el);
    });

    // 次优：找有 header + form-body + footer 结构的容器（.pl-modal 模式）
    // 判断：直接子节点里同时有 header 和 footer（或 .pl-modal-head + .pl-modal-foot）
    const candidates = document.querySelectorAll('[class*="modal"], [class*="Modal"]');
    for (const el of candidates) {
      if (_excluded(el)) continue;
      if (!isVisible(el)) continue;
      // 直接子元素的 class 检测
      const children = Array.from(el.children);
      const hasHead = children.some(
        (c) => c.tagName === "HEADER" || (c.className && String(c.className).includes("head"))
      );
      const hasFoot = children.some(
        (c) => c.tagName === "FOOTER" || (c.className && String(c.className).includes("foot"))
      );
      const hasForm = children.some(
        (c) => c.querySelector && c.querySelector("input, select, textarea") !== null
      );
      if (hasHead && hasFoot && hasForm) {
        raw.push(el);
        continue;
      }
      // 备选：极高 z-index（>=200）且有字段 + h2
      const z = parseInt(window.getComputedStyle(el).zIndex, 10);
      if (!isNaN(z) && z >= 200) {
        if (el.querySelector("h2, h3") && el.querySelector("input, select, textarea")) {
          raw.push(el);
        }
      }
    }

    // 去重：保留最小的（去掉包含其他元素的祖先）
    const deduped = raw.filter(
      (el) => !raw.some((other) => other !== el && el.contains(other))
    );
    return deduped;
  }

  /** 从 modal 容器推断 form_id */
  function inferFormId(container) {
    // 1. [data-form-id] 祖先/自身
    const withFormId =
      container.getAttribute("data-form-id") ||
      container.closest("[data-form-id]")?.getAttribute("data-form-id");
    if (withFormId) return withFormId;

    // 2. modal 第一个 <h2> 文本 hash
    const h2 = container.querySelector("h2");
    if (h2) {
      const t = h2.textContent.trim();
      if (t) {
        const slug = slugify(t).slice(0, 20);
        return slug || "modal_" + simpleHash(t);
      }
    }

    // 3. class 后缀
    const cls = Array.from(container.classList).find(
      (c) => c !== "pl-modal" && (c.includes("modal") || c.includes("Modal"))
    );
    if (cls) return slugify(cls).replace("modal", "").replace(/^_+|_+$/g, "") || "modal";

    // 4. 生成 id
    return "modal_" + simpleHash(container.className + container.tagName);
  }

  /** 从 input 周围环境推断 form_id */
  function inferFieldFormId(el) {
    // 优先找 [data-form-id] 祖先
    const ancestor = el.closest("[data-form-id]");
    if (ancestor) return ancestor.getAttribute("data-form-id");
    // 找 modal 容器：向上走，找到第一个"有 h2 + button"的 [class*=modal] 容器
    // 这样 .pl-modal-form 会被跳过，直到找到 .pl-modal（有 h2）
    let cur = el.parentElement;
    while (cur && cur !== document.body) {
      const cls = cur.className || "";
      if (
        (typeof cls === "string" && (cls.includes("modal") || cls.includes("Modal"))) ||
        cur.getAttribute("role") === "dialog"
      ) {
        const hasTitle = !!(cur.querySelector("h2, h3, .pl-modal-title, [class*=modal-title]"));
        if (hasTitle) return inferFormId(cur);
      }
      cur = cur.parentElement;
    }
    // 找 [data-cap-anchor] 区块
    const anchor = el.closest("[data-cap-anchor]");
    if (anchor) return anchor.getAttribute("data-cap-anchor").replace(/\./g, "_");
    return "page";
  }

  /** 提取单个交互字段信息 */
  function extractField(el) {
    const tag = el.tagName;
    const capField = el.getAttribute("data-cap-field");
    const labelText = getLabelText(el);
    const ariaLabel = el.getAttribute("aria-label") || "";
    const nameAttr = el.getAttribute("name") || "";

    // key 优先级: data-cap-field > label文本 > aria-label > name
    const key = capField || labelText || ariaLabel || nameAttr || el.type || "field";

    // type
    let type = "text";
    if (tag === "SELECT") type = "select";
    else if (tag === "TEXTAREA") type = "textarea";
    else if (tag === "INPUT") {
      const t = (el.type || "text").toLowerCase();
      type = ["checkbox", "radio", "number", "email", "password", "date", "range"].includes(t)
        ? t
        : "text";
    } else if (el.hasAttribute("contenteditable")) type = "contenteditable";

    // value
    let value = "";
    if (type === "checkbox") value = el.checked ? "true" : "false";
    else if (type === "radio") value = el.checked ? (el.value || "true") : "";
    else if (type === "select") {
      const opt = el.options[el.selectedIndex];
      value = opt ? (opt.text || opt.value) : "";
    } else if (type === "contenteditable") value = el.textContent || "";
    else value = el.value || "";

    // options (select 专属)
    let options = undefined;
    if (tag === "SELECT") {
      options = Array.from(el.options).map((o) => ({
        value: o.value,
        label: o.text.trim(),
      }));
    }

    // required
    const required =
      el.hasAttribute("required") ||
      !!el.closest(".pl-field")?.querySelector(".pl-field-req");

    // hint
    const hint = el.getAttribute("data-cap-hint") || "";

    // 敏感字段脱敏:password 类型 或 key/name/label 命中敏感词 → 绝不外传明文。
    // ui_atlas 会被塞进 LLM system prompt 发往模型提供商,明文 API key/SMTP/验证码密钥会泄露(CWE-200)。
    const SENSITIVE_RE = /(pass|pwd|secret|token|api[\s_-]*key|apikey|credential|captcha|smtp|private[\s_-]*key|密码|密钥|令牌)/i;
    const sensitive =
      type === "password" ||
      SENSITIVE_RE.test(key) ||
      SENSITIVE_RE.test(nameAttr) ||
      SENSITIVE_RE.test(labelText);
    let safeValue = value;
    if (sensitive) safeValue = value ? "[REDACTED]" : "";

    return {
      form_id: inferFieldFormId(el),
      key,
      label: labelText || capField || ariaLabel || nameAttr || key,
      type,
      value: safeValue,
      ...(options !== undefined ? { options } : {}),
      required,
      ...(hint ? { hint } : {}),
      selector: getSelector(el),
    };
  }

  /** 提取 form 的 footer 按钮（top_actions） */
  function extractActions(scope, formId) {
    const actions = [];
    // footer 按钮
    const footers = scope.querySelectorAll(
      "footer, .pl-modal-foot, [class*=modal-foot], [class*=modal-footer]"
    );
    const footerBtns = new Set();
    for (const footer of footers) {
      footer.querySelectorAll("button, [role=button]").forEach((b) => footerBtns.add(b));
    }

    // 也收集主体内 btn primary / btn danger（提交按钮）
    scope
      .querySelectorAll("button.btn.primary, button.btn.danger, button[type=submit]")
      .forEach((b) => footerBtns.add(b));

    for (const btn of footerBtns) {
      if (!isVisible(btn)) continue;
      const label =
        btn.getAttribute("aria-label") ||
        btn.textContent.replace(/\s+/g, " ").trim() ||
        btn.getAttribute("title") ||
        "";
      if (!label) continue;
      actions.push({
        form_id: formId,
        label,
        disabled: btn.disabled,
        selector: getSelector(btn),
      });
    }
    return actions;
  }

  /** 全局可点按钮（page header 的 [data-tip] 按钮） */
  function extractGlobalActions() {
    const actions = [];
    const topbarBtns = document.querySelectorAll(
      ".pl-topbar [data-tip], .gc-topbar [data-tip], header [data-tip], " +
      ".pl-topbar button, .gc-topbar button"
    );
    for (const btn of topbarBtns) {
      if (!isVisible(btn)) continue;
      // task 119: 排除助手自己的按钮
      if (btn.closest && btn.closest(".cap-root, .tweaks-panel, [data-cap-exclude]")) continue;
      const label =
        btn.getAttribute("data-tip") ||
        btn.getAttribute("aria-label") ||
        btn.textContent.replace(/\s+/g, " ").trim() ||
        "";
      if (!label) continue;
      actions.push({
        form_id: "global",
        label,
        disabled: btn.disabled,
        selector: getSelector(btn),
      });
    }
    return actions;
  }

  // ── 核心扫描 ────────────────────────────────────────────────
  function doScan() {
    const t0 = performance.now();

    // 1. page 识别
    const hash = location.hash.replace(/^#/, "") || "";
    const titleText = document.title || "";
    const screenLabel = document.body.getAttribute("data-screen-label") || "";
    const pageId = hash
      ? (screenLabel ? screenLabel.toLowerCase().replace(/\s+/g, "_") + "." + hash : hash)
      : (screenLabel ? screenLabel.toLowerCase().replace(/\s+/g, "_") : "unknown");
    const pageLabel = titleText + (hash ? " #" + hash : "");

    // 2. open_modals
    const modalContainers = findModalContainers();
    const open_modals = modalContainers.map((m) => ({
      form_id: inferFormId(m),
      title: (m.querySelector("h2, h3, .pl-modal-title")?.textContent || "").trim(),
      selector: getSelector(m),
    }));

    // 3. forms 抽取
    const INTERACTIVE_SEL =
      'input:not([type="hidden"]):not([type="submit"]):not([type="reset"]):not([type="button"])' +
      ", select, textarea, [contenteditable]";

    // task 119: 助手自己的 panel (.cap-root) 内的所有 DOM 不算 "页面表单"。
    // 否则 LLM 看到唯一 textarea 就是助手输入框 → 把用户请求填回自己 → 死循环。
    // 同样排除 Tweaks 调试面板。
    const EXCLUDE_SELECTOR = ".cap-root, .tweaks-panel, [data-cap-exclude]";
    function isInExcludedZone(el) {
      return el.closest && el.closest(EXCLUDE_SELECTOR) !== null;
    }

    // 扫范围：所有 modal + 页面主体
    const scopes = [...modalContainers];
    // 页面主体（排除已在 modal 内的）
    const mainScopes = document.querySelectorAll(
      ".pl-stack, .pl-main, .gc-main, main, [data-cap-anchor], #root > div"
    );
    for (const s of mainScopes) {
      // 跳过被 modal 包含的
      const inModal = modalContainers.some((m) => m.contains(s) || s.contains(m));
      if (!inModal) scopes.push(s);
    }
    // 去重：排除子元素关系
    const deduped = scopes.filter((s) => !scopes.some((p) => p !== s && p.contains(s)));

    // 用 Map<form_id, {fields, actions}> 聚合
    const formMap = new Map();

    for (const scope of deduped) {
      const els = scope.querySelectorAll(INTERACTIVE_SEL);
      for (const el of els) {
        if (!isVisible(el)) continue;
        if (isInExcludedZone(el)) continue;  // task 119: 跳过助手自身 DOM
        const field = extractField(el);
        if (!formMap.has(field.form_id)) {
          formMap.set(field.form_id, { form_id: field.form_id, fields: [], actions: [] });
        }
        formMap.get(field.form_id).fields.push(field);
      }
    }

    // 提取各 form 的 actions
    // modal 的 actions
    for (const modal of modalContainers) {
      const fid = inferFormId(modal);
      if (!formMap.has(fid)) formMap.set(fid, { form_id: fid, fields: [], actions: [] });
      formMap.get(fid).actions = extractActions(modal, fid);
    }
    // page 区块的 actions
    for (const [fid, form] of formMap) {
      if (form.actions.length === 0 && form.fields.length > 0) {
        // 找包含这些字段的最近 scope
        const firstFieldEl = document.querySelector(form.fields[0].selector);
        if (firstFieldEl) {
          const scope =
            firstFieldEl.closest('[role="dialog"], [class*="pl-modal"]') ||
            firstFieldEl.closest("[data-cap-anchor]") ||
            document.body;
          form.actions = extractActions(scope, fid);
        }
      }
    }

    const forms = Array.from(formMap.values());

    // 4. top_actions（全局 + 每个 form 的）
    const top_actions = [
      ...extractGlobalActions(),
      ...forms.flatMap((f) => f.actions),
    ];

    const elapsed = performance.now() - t0;
    // 性能警告（debug 用，不影响生产）
    if (elapsed > 50 && typeof console !== "undefined") {
      console.warn("[ui-atlas] scan took " + elapsed.toFixed(1) + "ms (> 50ms target)");
    }

    return {
      page: pageId,
      page_label: pageLabel,
      open_modals,
      forms,
      top_actions,
      _scan_ms: Math.round(elapsed * 10) / 10,
      _ts: Date.now(),
    };
  }

  // ── 订阅系统 ────────────────────────────────────────────────
  const subscribers = new Set();

  function notifySubscribers(snapshot) {
    if (subscribers.size === 0) return;
    // microtask，不阻塞当前 call stack
    Promise.resolve().then(() => {
      for (const cb of subscribers) {
        try { cb(snapshot); } catch (_) {}
      }
    });
  }

  // ── setField（React 18 兼容 native setter） ─────────────────
  function setReactInputValue(el, value) {
    const tag = el.tagName;
    const proto =
      tag === "TEXTAREA"
        ? HTMLTextAreaElement.prototype
        : tag === "SELECT"
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (descriptor && descriptor.set) {
      descriptor.set.call(el, value);
    } else {
      el.value = value;
    }
    el.dispatchEvent(
      new Event(tag === "SELECT" ? "change" : "input", { bubbles: true })
    );
  }

  /** fuzzy 匹配：优先精确 value，再 fuzzy label */
  function fuzzyMatchOption(options, query) {
    if (!query) return null;
    const q = String(query).trim().toLowerCase();
    // 精确 value
    let found = options.find((o) => String(o.value).toLowerCase() === q);
    if (found) return found;
    // 精确 label
    found = options.find((o) => String(o.label).toLowerCase() === q);
    if (found) return found;
    // 包含
    found = options.find((o) =>
      String(o.value).toLowerCase().includes(q) ||
      String(o.label).toLowerCase().includes(q)
    );
    return found || null;
  }

  function setField(formId, fieldKey, value) {
    const snapshot = __UI_ATLAS.current;
    // 找到 form
    const form = (snapshot.forms || []).find((f) => f.form_id === formId);
    if (!form) {
      return { ok: false, error: "form_not_found", message: "找不到 form_id: " + formId };
    }
    // 找到字段
    const fkLower = String(fieldKey).toLowerCase();
    const field = form.fields.find(
      (f) =>
        String(f.key).toLowerCase() === fkLower ||
        String(f.label).toLowerCase() === fkLower
    );
    if (!field) {
      return { ok: false, error: "field_not_found", message: "找不到字段: " + fieldKey };
    }
    // 定位元素
    let el = null;
    try {
      el = document.querySelector(field.selector);
    } catch (_) {}
    if (!el) {
      return { ok: false, error: "element_not_found", message: "selector 无法定位: " + field.selector };
    }

    try {
      if (field.type === "checkbox") {
        const checked =
          value === true || value === "true" || value === "1" || value === "checked";
        if (el.checked !== checked) {
          el.click(); // React checkbox 监听 click
        }
      } else if (field.type === "radio") {
        el.click();
      } else if (field.type === "select") {
        // fuzzy 匹配 option
        const opts = Array.from(el.options).map((o) => ({ value: o.value, label: o.text }));
        const match = fuzzyMatchOption(opts, value);
        if (!match) {
          return { ok: false, error: "option_not_found", message: "找不到 option: " + value };
        }
        setReactInputValue(el, match.value);
      } else if (field.type === "contenteditable") {
        el.textContent = value;
        el.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        setReactInputValue(el, String(value));
      }
      // 重扫（让 current 同步）
      __UI_ATLAS.current = doScan();
      notifySubscribers(__UI_ATLAS.current);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: "set_error", message: e && e.message };
    }
  }

  // ── click ───────────────────────────────────────────────────
  function clickAction(formId, actionLabel) {
    const snapshot = __UI_ATLAS.current;
    const lbl = String(actionLabel).trim().toLowerCase();

    // 找候选按钮（top_actions 里找，或直接用 selector）
    const candidates = (snapshot.top_actions || []).filter((a) => {
      if (formId !== "global" && a.form_id !== formId) return false;
      return String(a.label).toLowerCase().includes(lbl);
    });

    if (candidates.length === 0) {
      return { ok: false, error: "action_not_found", message: "找不到 action: " + actionLabel };
    }

    // 优先精确匹配
    const exact = candidates.find(
      (a) => String(a.label).toLowerCase() === lbl
    );
    const target = exact || candidates[0];

    if (target.disabled) {
      return { ok: false, error: "button_disabled", message: "按钮已禁用: " + target.label };
    }

    let el = null;
    try {
      el = document.querySelector(target.selector);
    } catch (_) {}
    if (!el) {
      return { ok: false, error: "element_not_found", message: "selector 无法定位: " + target.selector };
    }
    try {
      el.click();
      return { ok: true };
    } catch (e) {
      return { ok: false, error: "click_error", message: e && e.message };
    }
  }

  // ── debounce + MutationObserver + input/change ───────────────
  let debounceTimer = null;
  const DEBOUNCE_MS = 200;

  function scheduleRescan() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      __UI_ATLAS.current = doScan();
      notifySubscribers(__UI_ATLAS.current);
    }, DEBOUNCE_MS);
  }

  // MutationObserver（监听 DOM 结构 + class/attribute 变化）
  const observer = new MutationObserver(function (mutations) {
    // 粗过滤：只有涉及 childList 或 style/class/open 属性才触发 rescan
    const relevant = mutations.some((m) => {
      if (m.type === "childList") return true;
      if (m.type === "attributes") {
        const attr = m.attributeName || "";
        return ["class", "style", "open", "hidden", "disabled", "aria-hidden"].includes(attr);
      }
      return false;
    });
    if (relevant) scheduleRescan();
  });

  // 全局 input/change capture（input value 变化不触发 MutationObserver）
  function onInputChange() {
    scheduleRescan();
  }

  // ── 初始化 ───────────────────────────────────────────────────
  function init() {
    // 首次扫描
    const initial = doScan();

    const __UI_ATLAS_OBJ = {
      current: initial,

      rescan() {
        this.current = doScan();
        notifySubscribers(this.current);
        return this.current;
      },

      setField(formId, fieldKey, value) {
        return setField(formId, fieldKey, value);
      },

      click(formId, actionLabel) {
        return clickAction(formId, actionLabel);
      },

      subscribe(cb) {
        if (typeof cb !== "function") return () => {};
        subscribers.add(cb);
        return () => subscribers.delete(cb);
      },
    };

    window.__UI_ATLAS = __UI_ATLAS_OBJ;

    // 启动 MutationObserver
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "style", "open", "hidden", "disabled", "aria-hidden"],
    });

    // 全局 input/change
    document.addEventListener("input", onInputChange, true);
    document.addEventListener("change", onInputChange, true);
  }

  // DOM ready guard
  if (document.body) {
    init();
  } else {
    document.addEventListener("DOMContentLoaded", init);
  }

  // 临时占位（init 前的 window.__UI_ATLAS 访问兜底）
  window.__UI_ATLAS = window.__UI_ATLAS || {
    current: null,
    rescan: () => ({ page: "not_ready" }),
    setField: () => ({ ok: false, error: "not_ready" }),
    click: () => ({ ok: false, error: "not_ready" }),
    subscribe: () => () => {},
  };
})();
