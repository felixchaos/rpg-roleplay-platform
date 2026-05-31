/* Composer + slash command menu + plus/attach menu + non-blocking confirm strip
   for the Game Console. */

import React from 'react';
import { useState as useStateC, useRef as useRefC, useEffect as useEffectC } from 'react';
import { Icon } from './game-icons.jsx';
import { chatComposerKey } from './responsive.jsx';
import { useTranslation } from 'react-i18next';

const SLASH_COMMANDS = [
  { id: "status", trigger: "/status", labelKey: "game.command.status_label", groupKey: "game.command.group_query", hint: "/status" },
  { id: "debug", trigger: "/debug", labelKey: "game.command.debug_label", groupKey: "game.command.group_query", hint: "/debug" },
  // task 39：用户报告命令菜单缺 /set；后端 state.apply_set_directive 已支持 /set|/设置|/设定。
  // 这是用自然语言强制改一组游戏参数的总入口（位置/时间/timeline.current_phase/
  // worldline.user_variables.X 等都可以一次塞进去），写入即落盘（task 27），优先级高于 GM 自动派生（task 28/36）。
  { id: "set", trigger: "/set ", labelKey: "game.command.set_label", groupKey: "game.command.group_state_write",
    hint: "/set time=dawn; location=harbor; player.name=TestTraveler; world.timeline.current_phase=harbor-dusk" },
  { id: "loc", trigger: "/loc ", labelKey: "game.command.loc_label", groupKey: "game.command.group_state_write", hint: "/loc <location>" },
  { id: "time", trigger: "/time ", labelKey: "game.command.time_label", groupKey: "game.command.group_state_write", hint: "/time <time>" },
  { id: "rel", trigger: "/rel ", labelKey: "game.command.rel_label", groupKey: "game.command.group_state_write", hint: "/rel <character> <status>" },
  { id: "var", trigger: "/var ", labelKey: "game.command.var_label", groupKey: "game.command.group_state_write", hint: "/var variable=value" },
  { id: "pin", trigger: "/pin ", labelKey: "game.command.pin_label", groupKey: "game.command.group_memory", hint: "/pin <text>" },
  { id: "note", trigger: "/note ", labelKey: "game.command.note_label", groupKey: "game.command.group_memory", hint: "/note <text>" },
  { id: "memory", trigger: "/memory ", labelKey: "game.command.memory_label", groupKey: "game.command.group_mode", hint: "/memory normal|deep|off" },
  { id: "permission", trigger: "/permission ", labelKey: "game.command.permission_label", groupKey: "game.command.group_mode", hint: "/permission default|review|full_access" },
  { id: "save", trigger: "/save", labelKey: "game.command.save_label", groupKey: "game.command.group_engineering", hint: "/save" },
  { id: "retry", trigger: "/retry", labelKey: "game.command.retry_label", groupKey: "game.command.group_engineering", hint: "/retry" },
];

const ATTACH_GROUPS = [
  {
    titleKey: "game.attach.group_local",
    items: [
      { id: "file", icon: "file", labelKey: "game.attach.item_file", hintKey: "game.attach.item_file_hint" },
      { id: "image", icon: "image", labelKey: "game.attach.item_image", hintKey: "game.attach.item_image_hint" },
    ],
  },
  {
    titleKey: "game.attach.group_script",
    items: [
      { id: "chapter", icon: "book", labelKey: "game.attach.item_chapter", hintKey: "game.attach.item_chapter_hint" },
      { id: "card", icon: "cards", labelKey: "game.attach.item_card", hintKey: "game.attach.item_card_hint" },
      { id: "world", icon: "world", labelKey: "game.attach.item_world", hintKey: "game.attach.item_world_hint" },
    ],
  },
  {
    titleKey: "game.attach.group_capability",
    items: [
      { id: "mcp", icon: "diamond", labelKey: "game.attach.item_mcp", hintKey: "game.attach.item_mcp_hint" },
      { id: "skill", icon: "spark", labelKey: "game.attach.item_skill", hintKey: "game.attach.item_skill_hint" },
      { id: "plan", icon: "compass", labelKey: "game.attach.item_plan", hintKey: "game.attach.item_plan_hint" },
    ],
  },
];

// task 39 收尾：MODEL_OPTIONS（GPT-4o · RPG / Claude Opus 4.1 / Gemini 3 Flash ...）
// 是早期 mock fallback；只要它存在，任何 fallback 路径都可能让用户误以为"模型列表是 mock"。
// 现在 ModelPopover 强绑 catalog（gameState.models or /api/models）；当前模型标签强绑
// gameState.app.model。删掉这个 constant，彻底杜绝 mock 出现的可能。
//
// 历史回顾：原来 5 项是
//   gpt-4o-mini-rpg / claude-opus-4-1 / gemini-3-flash / qwen-max / deepseek-r1
// 后端 model_registry 里现在的真名是
//   vertex_ai/gemini-3.5-flash, anthropic/claude-opus-4-7, openai/gpt-5.5, ...
// 不一致 → mock 就是 mock，不当 fallback。

// task 53：补 read_only 模式（对齐 codex suggest）；id 用后端 normalize 接受的形式。
// 注意 "review" 对应后端 auto_review；保持 backward-compat。
const PERMISSION_OPTIONS = [
  { id: "read_only",   labelKey: "game.permission.read_only_label",   descKey: "game.permission.read_only_desc",   icon: "eye" },
  { id: "default",     labelKey: "game.permission.default_label",     descKey: "game.permission.default_desc",     icon: "lock" },
  { id: "review",      labelKey: "game.permission.review_label",      descKey: "game.permission.review_desc",      icon: "shield" },
  { id: "full_access", labelKey: "game.permission.full_access_label", descKey: "game.permission.full_access_desc", icon: "unlock" },
];

// task 53：onApprove/onReject/onAnswer 现在签名是 (it) → 调用方拿 {id, index}
// 双字段发后端（id 优先；老数据没 id 时走 index 兜底，确保历史 pending 也能清掉）。
function ConfirmStrip({ pendingWrites, pendingQuestions, onApprove, onReject, onAnswer, onDismiss }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useStateC({});
  // 防御：后端 /api/state 返回的 permissions 可能不带这两个数组（partial state），
  // 没兜底就 .map -> 白屏。task 5 修复点之一。
  const writes = Array.isArray(pendingWrites) ? pendingWrites : [];
  const questions = Array.isArray(pendingQuestions) ? pendingQuestions : [];
  // 关键：复合 key。原来用 `key={it.id}` 在三种场景下会重复触发 React key warning：
  //   1) backend 不给 id → 多个 undefined key
  //   2) question 和 write 各自有 id=1（不同列表里数字重合）
  //   3) backend 偶尔重复推送同一 pending 项
  // 用 `${kind}:${id ?? idx}` 保证跨 kind 不撞，缺 id 也用 index 兜底；任意原始数据形态都唯一。
  // 同时把 ridx 留作展开/动作回调的稳定句柄，避免依赖可能缺失的 it.id。
  const items = [
    ...questions.map((q, i) => ({ kind: "question", id: q.id, _ridx: i, key: `q:${q && q.id != null ? q.id : `idx${i}`}`, data: q || {} })),
    ...writes.map((w, i) => ({ kind: "write", id: w.id, _ridx: i, key: `w:${w && w.id != null ? w.id : `idx${i}`}`, data: w || {} })),
  ];
  if (!items.length) return null;
  // expanded/onAnswer/onApprove/onReject/onDismiss 仍按 it.id 走（与父组件原契约一致）；
  // 缺 id 时回退到 key（复合字符串），父组件 filter(x => x.id !== id) 拿不到 undefined 不会误删。
  // task 53：返回 {id, index} 双字段。id 是后端 v2+ 给的稳定 id；老 pending
  // 没 id（如本地已有的 8 条 zombie question）走 index 兜底，后端 _pop_*_pending
  // 会按 id 优先 / index fallback 来弹出，保证所有历史 pending 都能被清掉。
  const handleId = (it) => ({ id: (it.id != null ? it.id : null), index: it._ridx });
  const tog = (id) => setExpanded(e => ({ ...e, [id]: !e[id] }));
  return (
    <div className="gc-confirm-strip">
      <div className="gc-confirm-strip-head">
        <span className="dot warn pulse" />
        <span>{t('game.confirm.pending_count', { count: items.length })}</span>
      </div>
      {items.map(it => it.kind === "question" ? (
        <div key={it.key} className="gc-confirm gc-confirm-q">
          <div className="gc-confirm-marker"><Icon name="info" size={12} /></div>
          <div className="gc-confirm-body">
            <div className="gc-confirm-row1">
              <span className="gc-confirm-tag">{t('game.confirm.gm_question')}</span>
              {/* task 46：后端 state.add_pending_question 写 {question, options, source, turn}；
                  旧前端读 it.data.text / it.data.choices 永远为空 → UI 显示『GM 询问』但内容为空。
                  双向兼容（question/text 取一，options/choices 取一）。 */}
              <span className="gc-confirm-text serif">{it.data.question || it.data.text || t('game.confirm.question_empty')}</span>
            </div>
            <div className="gc-confirm-actions">
              {((it.data.options || it.data.choices) || []).map((c, ci) => (
                // c 本身可能重复 / null，复合 (key, ci, c) 保证唯一；
                // 即便 backend 给两个相同 "继续" 也不会撞 key。
                <button key={`${it.key}:${ci}:${c}`} className="gc-chip-btn"
                  onClick={() => onAnswer(handleId(it), c)}>{c}</button>
              ))}
            </div>
          </div>
          <button className="iconbtn" onClick={() => onDismiss(handleId(it))} title={t('game.confirm.no_answer_tip')}><Icon name="close" size={11} /></button>
        </div>
      ) : (
        <div key={it.key} className={`gc-confirm gc-confirm-w gc-confirm-risk-${it.data.risk}`}>
          <div className="gc-confirm-marker">
            <Icon name={it.data.risk === "high" ? "warn" : "info"} size={12} />
          </div>
          <div className="gc-confirm-body">
            <div className="gc-confirm-row1">
              <span className="gc-confirm-tag">{it.data.risk === "high" ? t('game.confirm.write_risk_high') : it.data.risk === "medium" ? t('game.confirm.write_risk_medium') : t('game.confirm.write_risk_low')}</span>
              <span className="gc-confirm-diff mono">
                <span className="gc-confirm-field">{it.data.field}</span>
                <span className="gc-diff-arrow"><Icon name="arrow_right" size={10} /></span>
                <span className="gc-diff-to">{formatVal(it.data.to)}</span>
              </span>
              <button className="gc-confirm-toggle muted-2" onClick={() => tog(it.key)} title={t('game.confirm.detail_tip')}>
                <Icon name={expanded[it.key] ? "chevron_up" : "chevron_down"} size={11} />
              </button>
            </div>
            {expanded[it.key] && (
              <div className="gc-confirm-expand">
                <div className="gc-confirm-diff-full mono">
                  <span className="gc-diff-from">{formatVal(it.data.from)}</span>
                  <Icon name="arrow_right" size={11} style={{color: "var(--muted-2)"}} />
                  <span className="gc-diff-to">{formatVal(it.data.to)}</span>
                </div>
                <div className="gc-confirm-reason muted">{it.data.reason}</div>
              </div>
            )}
            <div className="gc-confirm-actions">
              <button className="gc-chip-btn gc-chip-primary" onClick={() => onApprove(handleId(it))}>
                <Icon name="check" size={11} /> {t('game.confirm.allow')}
              </button>
              <button className="gc-chip-btn" onClick={() => onReject(handleId(it))}>
                <Icon name="close" size={11} /> {t('game.confirm.reject')}
              </button>
            </div>
          </div>
          <button className="iconbtn" onClick={() => onDismiss(handleId(it))} title={t('game.confirm.later_tip')}><Icon name="close" size={11} /></button>
        </div>
      ))}
    </div>
  );
}

function formatVal(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "object" && v.label) return v.label;
  return JSON.stringify(v);
}

function CommandMenu({ query, onPick, onClose }) {
  const { t } = useTranslation();
  const q = query.replace(/^\//, "").trim().toLowerCase();
  const filtered = SLASH_COMMANDS.filter(c =>
    c.trigger.toLowerCase().includes("/" + q) || t(c.labelKey).includes(query.replace(/^\//, ""))
  );
  const groups = {};
  filtered.forEach(c => { (groups[c.groupKey] = groups[c.groupKey] || []).push(c); });
  return (
    <div className="gc-menu gc-cmd-menu">
      <div className="gc-menu-head">
        <Icon name="slash" size={12} />
        <span className="mono">{query || "/"}</span>
        <span className="muted-2" style={{marginLeft: "auto", fontSize: 11}}>{t('game.command.title')}</span>
      </div>
      <div className="gc-cmd-cols">
        {Object.entries(groups).map(([groupKey, items]) => (
          <div key={groupKey} className="gc-cmd-col">
            <div className="gc-cmd-group">{t(groupKey)}</div>
            {items.map(c => (
              <button key={c.id} className="gc-cmd-item" onClick={() => onPick(c)}>
                <span className="mono gc-cmd-trigger">{c.trigger.trim()}</span>
                <span className="gc-cmd-label">{t(c.labelKey)}</span>
                <span className="muted-2 mono gc-cmd-hint">{c.hint}</span>
              </button>
            ))}
          </div>
        ))}
        {!filtered.length && (
          <div className="gc-cmd-col empty"><div className="muted">{t('game.command.no_match')}</div></div>
        )}
      </div>
      <div className="gc-menu-foot">
        <span className="kbd">↑↓</span><span className="muted">{t('game.command.nav_hint')}</span>
        <span className="kbd">⏎</span><span className="muted">{t('game.command.confirm_hint')}</span>
        <span className="kbd">Esc</span><span className="muted">{t('game.command.cancel_hint')}</span>
      </div>
    </div>
  );
}

function AttachMenu({ onPick, onClose, triggerRef }) {
  const menuRef = useRefC(null);
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    const onOutside = (e) => {
      const inMenu = menuRef.current && menuRef.current.contains(e.target);
      const inTrigger = triggerRef && triggerRef.current && triggerRef.current.contains(e.target);
      if (!inMenu && !inTrigger) onClose && onClose();
    };
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onOutside, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onOutside, true);
    };
  }, [onClose, triggerRef]);

  const { t } = useTranslation();
  return (
    <div ref={menuRef} className="gc-menu gc-attach-menu">
      <div className="gc-menu-head">
        <Icon name="plus" size={12} />
        <span>{t('game.attach.title')}</span>
        <span className="muted-2" style={{marginLeft: "auto", fontSize: 11}}>{t('game.attach.drag_hint')}</span>
      </div>
      <div className="gc-attach-groups">
        {ATTACH_GROUPS.map(g => (
          <div key={g.titleKey} className="gc-attach-group">
            <div className="gc-attach-group-title">{t(g.titleKey)}</div>
            <div className="gc-attach-items">
              {g.items.map(it => (
                <button key={it.id} className="gc-attach-item" onClick={() => onPick(it)}>
                  <span className="gc-attach-icon"><Icon name={it.icon} size={16} /></span>
                  <span className="gc-attach-label">
                    <strong>{t(it.labelKey)}</strong>
                    <span className="muted-2">{t(it.hintKey)}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ModelPopover({ current, onPick, align = "left", gameState, onClose, triggerRef }) {
  const { t } = useTranslation();
  // A1: 取当前存档 id（从 /api/state 的 gameState.save_id）用于存档级模型切换
  const saveId = (gameState && gameState.save_id != null) ? gameState.save_id : null;
  const menuRef = useRefC(null);
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    const onOutside = (e) => {
      const inMenu = menuRef.current && menuRef.current.contains(e.target);
      const inTrigger = triggerRef && triggerRef.current && triggerRef.current.contains(e.target);
      if (!inMenu && !inTrigger) onClose && onClose();
    };
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onOutside, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onOutside, true);
    };
  }, [onClose, triggerRef]);

  // 真实模型目录走后端 /api/models 拉新鲜数据(包含 _inject_health 的 health 字段)。
  // 不再用 gameState.models 缓存 — 那来自 /api/state 不带 health,UI 会全显 untested。
  // task 42: picker 必须知道每个模型的 health 状态才能灰掉不可达项。
  const [catalog, setCatalog] = useStateC(null);
  const [busy, setBusy] = useStateC(false);
  const [err, setErr] = useStateC("");
  React.useEffect(() => {
    if (!window.api || !window.api.models || !window.api.models.list) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.models.list();
        const realCatalog = (r && r.models && Array.isArray(r.models.apis)) ? r.models : r;
        if (!cancelled && realCatalog) setCatalog(realCatalog);
      } catch (e) {
        if (!cancelled) setErr(String(e?.message || e));
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // 把 catalog 扁平化为可选模型列表（只显示 enabled 的）
  // task 42: 注入 health 状态(ok/err/untested),picker 灰掉 err 项防止用户选 404 模型
  const flat = [];
  const apis = (catalog && Array.isArray(catalog.apis)) ? catalog.apis : [];
  apis.forEach((api) => {
    if (api && api.enabled === false) return;
    const mods = api.models || [];
    mods.forEach((m) => {
      if (m && m.enabled !== false) {
        // 价格 & context 来自 m.pricing（后端 model_probe 注入）
        const pricing = m.pricing || {};
        const priceIn = pricing.input != null ? pricing.input : null;
        const priceOut = pricing.output != null ? pricing.output : null;
        const ctxRaw = pricing.context != null ? pricing.context : null;

        // 格式化 context window：>= 1M → "1M"，>= 1K → "xxxK"
        let ctxLabel = null;
        if (ctxRaw != null && ctxRaw > 0) {
          if (ctxRaw >= 1000000) ctxLabel = `${Math.round(ctxRaw / 1000000)}M`;
          else if (ctxRaw >= 1000) ctxLabel = `${Math.round(ctxRaw / 1000)}K`;
          else ctxLabel = String(ctxRaw);
        }

        // 格式化价格行："$X / $Y per M" 或 "免费"
        let priceLabel = null;
        if (priceIn != null && priceOut != null) {
          if (priceIn === 0 && priceOut === 0) {
            priceLabel = t('game.composer.model_price_free');
          } else {
            priceLabel = `$${priceIn.toFixed(2)} / $${priceOut.toFixed(2)} per M`;
          }
        }

        flat.push({
          id: m.id,
          real_name: m.real_name || m.id,
          label: m.display_name || m.real_name || m.id,
          api_id: api.id,
          api_label: api.display_name || api.id,
          desc: (m.capabilities || []).slice(0, 3).join(" · "),
          health: m.health || "untested",
          health_error: m.health_error || "",
          health_latency_ms: m.health_latency_ms,
          priceLabel,
          ctxLabel,
        });
      }
    });
  });
  // 排序:可用优先,err 沉底
  flat.sort((a, b) => {
    const order = { ok: 0, untested: 1, degraded: 2, err: 3 };
    return (order[a.health] ?? 4) - (order[b.health] ?? 4);
  });

  // A1: 当前选中优先级：存档 session_model > catalog.selected > gameState.app（全局回退）
  const _sessionModel = gameState && gameState.session_model;
  const selected = (catalog && catalog.selected) || {};
  const selectedKey = (_sessionModel && _sessionModel.api_id && _sessionModel.model_id)
    ? `${_sessionModel.api_id}::${_sessionModel.model_id}`
    : (selected.api_id && selected.model_id
        ? `${selected.api_id}::${selected.model_id}`
        : (gameState && gameState.app
            ? `${gameState.app.api_id || ""}::${gameState.app.model_real_name || ""}`
            : ""));

  const pickModel = async (item) => {
    // M5: 记录调用前的选中态，失败时回滚
    const prevSelectedKey = selectedKey;
    setBusy(true); setErr("");
    // A1: 游戏内 picker 带 save_id → 存档级切换，不动全局 catalog
    const isSaveScope = saveId != null;
    try {
      const r = await window.api.models.select({
        api_id: item.api_id,
        model_id: item.real_name,
        ...(isSaveScope ? { save_id: saveId } : {}),
      });
      if (r && r.ok === false) throw new Error(r.error || r.detail || t('game.composer.model_switch_failed'));
      if (isSaveScope) {
        window.__apiToast?.(t('game.composer.model_switched_save', { label: item.label }), { kind: "ok", duration: 2800 });
        // 存档级切换：不广播 game-state-refresh（避免干扰其他 tab/存档）
      } else {
        window.__apiToast?.(t('game.composer.model_switched', { label: item.label }), { kind: "ok", duration: 1800 });
        // 全局切换：通知所有 tab 同步
        try { window.dispatchEvent(new CustomEvent("game-state-refresh")); } catch (_) {}
      }
      onPick && onPick(item.id);
    } catch (e) {
      const msg = String(e?.message || e);
      setErr(msg);
      // M5: 尝试触发带重试按钮的 toast，回退到普通 danger toast
      if (window.__apiToast) {
        window.__apiToast(t('game.composer.model_switch_failed'), {
          kind: "danger",
          detail: msg,
          action: { label: t('game.composer.retry'), onClick: () => pickModel(item) },
        });
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={menuRef} className={`gc-menu gc-pop-menu ${align === "right" ? "gc-menu-right" : ""}`}>
      <div className="gc-menu-head">
        <Icon name="sparkle" size={12} /><span>{t('game.composer.model_placeholder')}</span>
        {busy ? <span className="muted-2" style={{marginLeft: "auto", fontSize: 11}}>{t('game.composer.model_switching')}</span> : null}
      </div>
      {err ? <div className="muted-2" style={{padding: "6px 10px", fontSize: 11.5, color: "var(--danger)"}}>{err}</div> : null}
      <ul className="gc-pop-list">
        {flat.length === 0 && (
          <li><div style={{padding: "8px 10px", fontSize: 12, color: "var(--muted)"}}>
            {t('game.composer.model_none')}
          </div></li>
        )}
        {flat.map((m) => {
          const key = `${m.api_id}::${m.real_name}`;
          const active = key === selectedKey;
          const unavailable = m.health === "err";
          // M1: degraded → 橙色
          const dotColor = m.health === "ok" ? "var(--ok)"
            : m.health === "degraded" ? "#e89b3a"
            : m.health === "err" ? "var(--danger)"
            : "var(--muted)";
          const dotTip = m.health === "ok" ? `ok · ${m.health_latency_ms}ms`
            : m.health === "degraded" ? `degraded · ${m.health_latency_ms != null ? m.health_latency_ms + "ms" : "high latency"}`
            : m.health === "err" ? `unreachable · ${(m.health_error || "").slice(0, 80)}`
            : "untested";
          return (
            <li key={key}>
              <button
                onClick={() => !busy && !unavailable && pickModel(m)}
                className={active ? "active" : ""}
                disabled={busy || unavailable}
                title={unavailable ? `unreachable:${(m.health_error || "").slice(0, 120)}` : undefined}
                style={unavailable ? { opacity: 0.45 } : undefined}
              >
                <div>
                  <span
                    className="dot"
                    style={{display: "inline-block", width: 6, height: 6, borderRadius: "50%", background: dotColor, marginRight: 6, verticalAlign: "middle"}}
                    title={dotTip}
                  />
                  <strong>{m.label}</strong>
                  <span className="muted-2 mono" style={{marginLeft: 6, fontSize: 11}}>{m.api_label}</span>
                  {unavailable && (
                    <span className="muted-2" style={{marginLeft: 6, fontSize: 10.5, color: "var(--danger)"}}>unreachable</span>
                  )}
                </div>
                {(m.desc || m.priceLabel || m.ctxLabel) ? (
                  <span className="muted" style={{fontSize: 12}}>
                    {m.desc || null}
                    {m.priceLabel ? (
                      <span style={{marginLeft: m.desc ? 6 : 0, opacity: 0.85}}>{m.priceLabel}</span>
                    ) : null}
                    {m.ctxLabel ? (
                      <span style={{marginLeft: (m.desc || m.priceLabel) ? 6 : 0, opacity: 0.7}}>ctx {m.ctxLabel}</span>
                    ) : null}
                  </span>
                ) : null}
                {active && <Icon name="check" size={14} style={{color: "var(--accent)"}} />}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function PermissionPopover({ current, onPick, onClose, triggerRef }) {
  const { t } = useTranslation();
  const menuRef = useRefC(null);
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    const onOutside = (e) => {
      const inMenu = menuRef.current && menuRef.current.contains(e.target);
      const inTrigger = triggerRef && triggerRef.current && triggerRef.current.contains(e.target);
      if (!inMenu && !inTrigger) onClose && onClose();
    };
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onOutside, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onOutside, true);
    };
  }, [onClose, triggerRef]);

  return (
    <div ref={menuRef} className="gc-menu gc-pop-menu">
      <div className="gc-menu-head">
        <Icon name="lock" size={12} /><span>{t('game.composer.perm_title')}</span>
      </div>
      <ul className="gc-pop-list">
        {PERMISSION_OPTIONS.map(p => (
          <li key={p.id}>
            <button onClick={() => onPick(p.id)} className={p.id === current ? "active" : ""}>
              <div>
                <Icon name={p.icon} size={12} style={{verticalAlign: "-2px", marginRight: 6, color: "var(--muted)"}} />
                <strong>{t(p.labelKey)}</strong>
              </div>
              <span className="muted" style={{fontSize: 12}}>{t(p.descKey)}</span>
              {p.id === current && <Icon name="check" size={14} style={{color: "var(--accent)"}} />}
            </button>
          </li>
        ))}
      </ul>
      <div className="gc-menu-foot">
        <span className="muted" style={{fontSize: 11.5}}>
          {t('game.composer.perm_footer')}
        </span>
      </div>
    </div>
  );
}

function SuggestionRow({ suggestions, onPick }) {
  const { t } = useTranslation();
  if (!suggestions?.length) return null;
  return (
    <div className="gc-suggestions">
      <div className="gc-suggestions-label muted-2">
        <Icon name="compass" size={12} /> {t('game.composer.based_on_story')}
      </div>
      <div className="gc-suggestions-row">
        {suggestions.map((s, i) => (
          <button key={i} className="gc-suggestion serif" onClick={() => onPick(s)}>{s}</button>
        ))}
      </div>
    </div>
  );
}

function Composer({
  text, setText,
  onSend, onStop, running,
  onSendRaw,   // task 130: 一键继续 — 直接发任意文本不经过 textarea
  permission, setPermission,
  model, setModel,
  composerMode,
  suggestions,
  attachments,
  removeAttachment,
  onAttachPick,
  onSlashPick,
  pickedCommand,
  onClearCommand,
  showSlash, showPlus, showModel, showPerm,
  toggleSlash, togglePlus, toggleModel, togglePerm,
  gameState,   // task 48：透传 game state 拿 relationships，让 @ mention 用真角色
}) {
  const { t } = useTranslation();
  const taRef = useRefC(null);
  const plusTriggerRef = useRefC(null);
  const modelTriggerRef = useRefC(null);
  const permTriggerRef = useRefC(null);
  const isWriting = composerMode === "writing";

  // task 50：暴露 window.__rpgInsertMention(name)，让外部（右侧 PanelCharacters
  // 卡片的 @ 按钮等 dead button 修复）一键插入 @角色 到输入框尾部。
  React.useEffect(() => {
    window.__rpgInsertMention = (name) => {
      if (!name) return;
      const cur = text || "";
      const insertion = (cur && !cur.endsWith(" ") && !cur.endsWith("\n") ? " " : "") + "@" + name + " ";
      setText(cur + insertion);
      // 聚焦到输入框尾部
      setTimeout(() => {
        const ta = taRef.current;
        if (ta && ta.focus) {
          ta.focus();
          try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (_) {}
        }
      }, 0);
    };
    return () => { if (window.__rpgInsertMention) delete window.__rpgInsertMention; };
  }, [text, setText]);

  // @ mention picker state
  const [mention, setMention] = useStateC(null); // { start, query }
  // task 48：原硬编码 6 个角色（顾承砚/沈知微/韩司直/阿衡/童守人/税吏甲），
  // 跟当前剧本完全无关。改为从 gameState.relationships 派生；
  // 加上 player.name 让玩家自己也可被 @ 到（自言自语 / 旁白）。
  // 完全没数据（新存档第一轮）才显示一条提示。
  const CHARS = (() => {
    const out = [];
    const seen = new Set();
    const push = (name, role) => {
      const n = String(name || "").trim();
      if (!n || seen.has(n)) return;
      seen.add(n);
      out.push({ name: n, role: String(role || "") });
    };
    const p = (gameState && gameState.player) || {};
    if (p.name) push(p.name, (p.role || t('game.status.player')) + " · 你");
    const rels = (gameState && gameState.relationships) || {};
    for (const [name, info] of Object.entries(rels)) {
      const tone = typeof info === "string" ? info : (info?.tone || "");
      push(name, tone ? `${t('game.characters.relationships')}：${tone}` : "");
    }
    return out;
  })();
  const onTextChange = (e) => {
    const newText = e.target.value;
    setText(newText);
    const caret = e.target.selectionStart || 0;
    // find nearest @ before caret with no whitespace in-between
    const upto = newText.slice(0, caret);
    const m = upto.match(/@([^\s@]{0,12})$/);
    if (m) setMention({ start: caret - m[0].length, query: m[1] });
    else setMention(null);
  };
  const filteredChars = !mention ? [] : CHARS.filter(c =>
    c.name.includes(mention.query) || c.role.includes(mention.query) || mention.query === ""
  );
  const insertMention = (name) => {
    if (!mention) return;
    const before = text.slice(0, mention.start);
    const after = text.slice((taRef.current?.selectionStart) || mention.start + mention.query.length + 1);
    const next = before + "@" + name + " " + after;
    setText(next);
    setMention(null);
    setTimeout(() => {
      if (taRef.current) {
        const pos = before.length + 1 + name.length + 1;
        taRef.current.focus();
        taRef.current.setSelectionRange(pos, pos);
      }
    }, 0);
  };
  return (
    <div className={`gc-composer-wrap ${isWriting ? "writing" : "compact"}`}>
      {/* task 129: 删 SuggestionRow — "基于当前剧情" 的建议多次修不好,直接砍 */}
      {attachments?.length > 0 && (
        <div className="gc-attachments">
          {attachments.map((a, i) => (
            <span key={i} className="gc-attachment">
              <Icon name={a.kind === "image" ? "image" : a.kind === "skill" ? "spark" : a.kind === "mcp" ? "diamond" : "file"} size={12} />
              <span className="truncate">{a.name}</span>
              <button onClick={() => removeAttachment(i)} className="iconbtn" style={{width: 18, height: 18}}><Icon name="close" size={10} /></button>
            </span>
          ))}
        </div>
      )}
      <div className={`gc-composer ${isWriting ? "writing" : ""} ${pickedCommand ? "with-cmd" : ""}`}>
        <div className="gc-composer-row gc-composer-top">
          {pickedCommand && (
            <div className="gc-cmd-chip">
              <span className="mono">{pickedCommand.trigger.trim()}</span>
              <span className="gc-cmd-chip-label">{pickedCommand.label}</span>
              <button className="iconbtn" data-tip={t('game.composer.remove_command_tip')} onClick={onClearCommand} style={{width: 18, height: 18}}>
                <Icon name="close" size={10} />
              </button>
            </div>
          )}
          <textarea
            ref={taRef}
            className={`gc-textarea ${isWriting ? "serif" : ""} gc-textarea-autogrow`}
            placeholder={pickedCommand
              ? (pickedCommand.hint.replace(pickedCommand.trigger, "").trim() || t('game.composer.placeholder_command'))
              : (isWriting
              ? t('game.composer.placeholder_writing')
              : t('game.composer.placeholder_compact'))}
            rows={1}
            value={text}
            onChange={(e) => {
              // task 91: 自适应高度 — 重置 scrollHeight 让 textarea 自动撑开。
              // max-height 在 CSS 里限,超过就 scroll。
              const ta = e.target;
              ta.style.height = "auto";
              ta.style.height = Math.min(ta.scrollHeight, 280) + "px";
              if (onTextChange) onTextChange(e);
            }}
            onKeyDown={(e) => {
              if (mention && (e.key === "Escape")) { e.preventDefault(); setMention(null); return; }
              if (pickedCommand && e.key === "Backspace" && text === "") {
                e.preventDefault(); onClearCommand?.();
                return;
              }
              // task 115: 统一聊天输入键位 (Claude Code Desktop 同款)
              // Enter 发送, Shift+Enter 换行, IME composition 时 Enter 不发,
              // Cmd/Ctrl+Enter 也发送 (备用)
              const fn = chatComposerKey;
              if (fn) {
                fn(e, () => onSend && onSend());
              } else if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent?.isComposing) {
                e.preventDefault();
                onSend && onSend();
              }
            }}
            onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; e.currentTarget.classList.add("drop-active"); }}
            onDragLeave={(e) => { e.currentTarget.classList.remove("drop-active"); }}
            onDrop={(e) => {
              e.preventDefault();
              e.currentTarget.classList.remove("drop-active");
              const t = e.dataTransfer.getData("text/plain");
              if (t) setText((text || "") + (text && !text.endsWith(" ") ? " " : "") + t);
            }}
          />
        </div>
        <div className="gc-composer-row gc-composer-bottom">
          <div className="gc-composer-left">
            <button ref={plusTriggerRef} className={`iconbtn ${showPlus ? "active" : ""}`} onClick={togglePlus} data-tip={t('game.composer.attach_tip')}>
              <Icon name="plus" size={14} />
            </button>
            <button className={`iconbtn ${showSlash ? "active" : ""}`} onClick={toggleSlash} data-tip={t('game.composer.command_tip')}>
              <Icon name="slash" size={14} />
            </button>
            {/* task 130: 一键继续推进 — 玩家被动场景 (昏迷/旁观/过场) 直接让 GM 推一段 */}
            {!running && (
              <button
                className="gc-pop-trigger"
                onClick={() => onSendRaw && onSendRaw(t('game.composer.continue_text'))}
                data-tip={t('game.composer.continue_tip')}
                disabled={!onSendRaw}>
                <Icon name="play" size={12} />
                <span>{t('game.composer.continue')}</span>
              </button>
            )}
            <button ref={permTriggerRef} className="gc-pop-trigger" onClick={togglePerm}>
              <Icon name={PERMISSION_OPTIONS.find(p => p.id === permission)?.icon || "lock"} size={12} />
              <span>{t(PERMISSION_OPTIONS.find(p => p.id === permission)?.labelKey || 'game.permission.default_label')}</span>
              <Icon name="chevron_down" size={11} />
            </button>
          </div>
          <div className="gc-composer-right">
            <ContextUsage gameState={gameState} />
            <button ref={modelTriggerRef} className="gc-pop-trigger" onClick={toggleModel}>
              <Icon name="sparkle" size={12} />
              <span className="gc-model-label">{_currentModelLabel(gameState, model, t)}</span>
              <Icon name="chevron_down" size={11} />
            </button>
            <span className="muted-2" style={{fontSize: 11.5}}>
              <span className="kbd">⌘</span> + <span className="kbd">⏎</span>
            </span>
            {running ? (
              <button className="btn danger" onClick={onStop}>
                <Icon name="stop" size={12} /> {t('game.composer.stop')}
              </button>
            ) : (
              <button
                className="btn primary"
                onClick={onSend}
                disabled={!text.trim() && !attachments?.length}
              >
                <Icon name="send" size={12} /> {t('game.composer.send')}
              </button>
            )}
          </div>
        </div>
        {/* popovers */}
        {showSlash && <CommandMenu query={text} onPick={onSlashPick} onClose={toggleSlash} />}
        {mention && filteredChars.length > 0 && (
          <MentionMenu chars={filteredChars} query={mention.query} onPick={insertMention} onClose={() => setMention(null)} />
        )}
        {showPlus && <AttachMenu onPick={onAttachPick} onClose={togglePlus} triggerRef={plusTriggerRef} />}
        {showModel && <ModelPopover current={model} onPick={(id) => { setModel(id); toggleModel(); }} align="right" gameState={gameState} onClose={toggleModel} triggerRef={modelTriggerRef} />}
        {showPerm && <PermissionPopover current={permission} onPick={(id) => { setPermission(id); togglePerm(); }} onClose={togglePerm} triggerRef={permTriggerRef} />}
      </div>
    </div>
  );
}

function MentionMenu({ chars, query, onPick, onClose }) {
  const { t } = useTranslation();
  const [idx, setIdx] = useStateC(0);
  React.useEffect(() => { setIdx(0); }, [query]);
  React.useEffect(() => {
    const onKey = (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); setIdx(i => Math.min(i + 1, chars.length - 1)); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setIdx(i => Math.max(i - 1, 0)); }
      else if (e.key === "Enter" || e.key === "Tab") {
        if (chars[idx]) { e.preventDefault(); onPick(chars[idx].name); }
      }
      else if (e.key === "Escape") { onClose(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [chars, idx]);
  return (
    <div className="gc-menu gc-mention-menu">
      <div className="gc-menu-head">
        <span style={{color: "var(--accent)"}}>@</span>
        <span className="muted">{t('game.mention.title')}</span>
        <span className="muted-2" style={{marginLeft: "auto", fontSize: 11}}>{query ? t('game.mention.match', { query }) : t('game.mention.all')}</span>
      </div>
      <ul className="gc-mention-list">
        {chars.map((c, i) => (
          <li key={c.name} className={i === idx ? "active" : ""}
              onClick={() => onPick(c.name)}
              onMouseEnter={() => setIdx(i)}>
            <span className="gc-mention-avatar serif">{c.name.slice(0, 1)}</span>
            <div className="gc-mention-body">
              <strong>{c.name}</strong>
              <span className="muted-2">{c.role}</span>
            </div>
          </li>
        ))}
      </ul>
      <div className="gc-menu-foot">
        <span className="kbd">↑↓</span><span className="muted">{t('game.mention.nav_hint')}</span>
        <span className="kbd">⏎</span><span className="muted">{t('game.mention.insert_hint')}</span>
        <span className="kbd">Esc</span><span className="muted">{t('game.mention.close_hint')}</span>
      </div>
    </div>
  );
}

// task 39 收尾：MODEL_OPTIONS 已删，不再 export。
function ContextBreakdownPanel({ used, cap, onClose, triggerRef }) {
  const { t } = useTranslation();
  const [data, setData] = useStateC(null);
  const [loading, setLoading] = useStateC(true);
  const panelRef = useRefC(null);

  React.useEffect(() => {
    let cancelled = false;
    const doFetch = async () => {
      setLoading(true);
      try {
        if (window.api && window.api.game && window.api.game.contextBreakdown) {
          const r = await window.api.game.contextBreakdown();
          if (!cancelled && r && r.ok !== false) setData(r);
        }
      } catch (_) {}
      if (!cancelled) setLoading(false);
    };
    doFetch();
    return () => { cancelled = true; };
  }, []);

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    const onOutside = (e) => {
      const inPanel = panelRef.current && panelRef.current.contains(e.target);
      const inTrigger = triggerRef && triggerRef.current && triggerRef.current.contains(e.target);
      if (!inPanel && !inTrigger) onClose();
    };
    window.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onOutside, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.removeEventListener("mousedown", onOutside, true);
    };
  }, [onClose, triggerRef]);

  const fmt = (n) => n >= 1_000_000 ? (n / 1_000_000).toFixed(2) + "M"
                   : n >= 1_000     ? (n / 1_000).toFixed(1) + "k"
                   : String(n);
  const total = data ? (data.total_tokens || 0) : used;
  const limit = data ? (data.ctx_limit || cap) : cap;
  const pct = limit > 0 ? Math.max(0, Math.min(1, total / limit)) : 0;
  const pctTxt = (pct * 100).toFixed(0);
  const barColor = pct > 0.9 ? "var(--danger)" : pct > 0.7 ? "var(--warn)" : "var(--accent)";
  const breakdown = (data && data.breakdown) || [];
  const nonFree = breakdown.filter(b => b.key !== "free" && b.tokens > 0);

  return (
    <div className="gc-ctx-breakdown" ref={panelRef}>
      <div className="gc-ctx-breakdown-head">
        <span className="gc-ctx-breakdown-title">
          <svg width="13" height="13" viewBox="0 0 20 20" style={{display:"inline-block",verticalAlign:"-1px"}}>
            <circle cx="10" cy="10" r="8" fill="none" stroke={barColor} strokeWidth="2.5"
              strokeDasharray={`${pct * 50.27} 50.27`} strokeLinecap="round"
              transform="rotate(-90 10 10)" />
            <circle cx="10" cy="10" r="8" fill="none" stroke="var(--line)" strokeWidth="2.5" />
          </svg>
          {t('game.composer.ctx_usage_title')}
        </span>
        <span className="gc-ctx-breakdown-total">{fmt(total)} / {fmt(limit)} ({pctTxt}%)</span>
      </div>
      <div className="gc-ctx-breakdown-bar-wrap">
        <div className="gc-ctx-breakdown-bar">
          {nonFree.map(b => (
            <div key={b.key} className="gc-ctx-breakdown-bar-seg"
              style={{width: (b.pct || 0) + "%", background: b.color}} />
          ))}
        </div>
      </div>
      {loading && <div style={{padding:"12px",textAlign:"center",fontSize:12,color:"var(--muted)"}}>{t('game.composer.ctx_loading')}</div>}
      {!loading && breakdown.length > 0 && (
        <ul className="gc-ctx-breakdown-list">
          {breakdown.map(b => (
            <li key={b.key} className={`gc-ctx-breakdown-row${b.key === "free" ? " gc-ctx-breakdown-free" : ""}`}>
              <div className="gc-ctx-breakdown-dot" style={{background: b.color}} />
              <span className="gc-ctx-breakdown-label">{b.label}</span>
              <span className="gc-ctx-breakdown-tok">{fmt(b.tokens)}</span>
              <span className="gc-ctx-breakdown-pct">{b.pct}%</span>
            </li>
          ))}
        </ul>
      )}
      {!loading && breakdown.length === 0 && (
        <div style={{padding:"10px 12px",fontSize:12,color:"var(--muted)"}}>{t('game.composer.ctx_no_data')}</div>
      )}
    </div>
  );
}

function ContextUsage({ gameState, used: usedProp, cap: capProp }) {
  const { t } = useTranslation();
  const liveUsed = (gameState && gameState.memory && gameState.memory.last_context
                    && gameState.memory.last_context.estimated_tokens) || 0;
  const liveCap = (gameState && gameState.app && gameState.app.context_window) || 0;
  const used = usedProp != null ? usedProp : liveUsed;
  const cap = capProp != null ? capProp : (liveCap > 0 ? liveCap : 1_000_000);

  const [open, setOpen] = useStateC(false);
  const wrapRef = useRefC(null);

  const pct = Math.max(0, Math.min(1, used / cap));
  const r = 8;
  const c = 2 * Math.PI * r;
  const fmt = (n) => n >= 1_000_000 ? (n / 1_000_000).toFixed(2) + "M"
                   : n >= 1_000     ? (n / 1_000).toFixed(1) + "k"
                   : String(n);
  const pctTxt = (pct * 100).toFixed(0);
  const color = pct > 0.9 ? "var(--danger)" : pct > 0.7 ? "var(--warn)" : "var(--accent)";

  return (
    <span className={`gc-context-usage gc-context-usage-ring${open ? " active" : ""}`}
      ref={wrapRef}
      onClick={() => setOpen(o => !o)}
      title={t('game.composer.context_usage_tip')}>
      <svg width="20" height="20" viewBox="0 0 20 20" style={{display: "block"}}>
        <circle cx="10" cy="10" r={r} fill="none" stroke="var(--line)" strokeWidth="2" />
        <circle cx="10" cy="10" r={r} fill="none" stroke={color} strokeWidth="2"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct)} strokeLinecap="round"
          transform="rotate(-90 10 10)"
          style={{transition: "stroke-dashoffset 320ms cubic-bezier(0.16, 1, 0.3, 1)"}} />
      </svg>
      {open && <ContextBreakdownPanel used={used} cap={cap} onClose={() => setOpen(false)} triggerRef={wrapRef} />}
    </span>
  );
}


// 取当前模型的展示标签。只读后端：gameState.app.model（/api/state 里）。
// 没有就显示『模型』占位符 — 不再 fallback 到 mock label，避免 UI 假装有数据。
// 若 gameState.app 缺失，多半是没登录 / reloadState 还没回来 / 后端崩；
// 让占位符肉眼可见才能引导用户去查问题，而不是被 mock 字串骗。
function _currentModelLabel(gameState, _ignored, t) {
  if (gameState && gameState.app && gameState.app.model) return gameState.app.model;
  return t ? t('game.composer.model_placeholder') : "Model";
}


export { Composer, ConfirmStrip, SuggestionRow, MentionMenu, SLASH_COMMANDS, PERMISSION_OPTIONS, ContextUsage, ContextBreakdownPanel };
