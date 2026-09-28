/* Game Console composer — 斜杠命令菜单(CommandMenu)/ 附件菜单(AttachMenu)/ @提及菜单(MentionMenu)
   + 命令与附件常量。纯机械从 game-composer.jsx 搬出,DOM/视觉/行为零变化。 */
import React from 'react';
import { useState as useStateC, useRef as useRefC } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import { useDismissOnOutsideOrEscape } from '../../hooks/useDismissOnOutsideOrEscape.js';

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
  // 「/memory normal|deep|off」已删:v1.77.0 起记忆模式什么都不控制(后端没有这条指令,发出去会被当成
  //  一句台词交给 GM)。召回量 / 预算 / 分桶在「设置 → 记忆」里调。
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

function CommandMenu({ query, onPick, onClose, triggerRef }) {
  const { t } = useTranslation();
  const menuRef = useRefC(null);
  // task 141: outside click + Esc 关闭 (之前 CommandMenu 漏修,点空白点不掉)
  useDismissOnOutsideOrEscape(menuRef, triggerRef, onClose);
  // task 141: max-height 自适应 trigger 上方可用空间,popover 不冲出 viewport 顶。
  // PR #14: 再加 55vh 上限 + resize 响应,防止菜单过高挡住整个界面。
  const calcCmdHeight = React.useCallback(() => {
    if (!menuRef.current || !triggerRef?.current) return;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const aboveSpace = Math.max(120, triggerRect.top - 16);
    menuRef.current.style.maxHeight = Math.min(aboveSpace, window.innerHeight * 0.55) + "px";
    menuRef.current.style.overflowY = "auto";
  }, [triggerRef]);
  React.useLayoutEffect(calcCmdHeight, [calcCmdHeight, query]);
  React.useEffect(() => {
    window.addEventListener("resize", calcCmdHeight);
    return () => window.removeEventListener("resize", calcCmdHeight);
  }, [calcCmdHeight]);
  const q = query.replace(/^\//, "").trim().toLowerCase();
  const filtered = SLASH_COMMANDS.filter(c =>
    c.trigger.toLowerCase().includes("/" + q) || t(c.labelKey).includes(query.replace(/^\//, ""))
  );
  const groups = {};
  filtered.forEach(c => { (groups[c.groupKey] = groups[c.groupKey] || []).push(c); });
  return (
    <div ref={menuRef} className="gc-menu gc-cmd-menu">
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

function AttachMenu({ onPick, onClose, triggerRef, onAiReply, aiReplyOnly = false }) {
  const menuRef = useRefC(null);
  // PR #14: 55vh 上限 + resize,防止菜单过高挡界面。
  const calcHeight = React.useCallback(() => {
    if (!menuRef.current || !triggerRef?.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const aboveSpace = Math.max(160, rect.top - 16);
    menuRef.current.style.maxHeight = Math.min(aboveSpace, window.innerHeight * 0.55) + "px";
    menuRef.current.style.overflowY = "auto";
  }, [triggerRef]);
  React.useLayoutEffect(calcHeight, [calcHeight]);
  React.useEffect(() => {
    window.addEventListener("resize", calcHeight);
    return () => window.removeEventListener("resize", calcHeight);
  }, [calcHeight]);
  useDismissOnOutsideOrEscape(menuRef, triggerRef, onClose);

  const { t } = useTranslation();
  return (
    <div ref={menuRef} className="gc-menu gc-attach-menu">
      <div className="gc-menu-head">
        <Icon name="plus" size={12} />
        <span>{t('game.attach.title')}</span>
        {!aiReplyOnly && <span className="muted-2" style={{marginLeft: "auto", fontSize: 11}}>{t('game.attach.drag_hint')}</span>}
      </div>
      <div className="gc-attach-groups">
        {onAiReply && (
          <div className="gc-attach-group">
            <div className="gc-attach-group-title">{t('tavern_app.ai_reply.label')}</div>
            <div className="gc-attach-items">
              <button className="gc-attach-item" onClick={() => { onClose && onClose(); onAiReply(); }}>
                <span className="gc-attach-icon"><Icon name="sparkle" size={16} /></span>
                <span className="gc-attach-label">
                  <strong>{t('tavern_app.ai_reply.label')}</strong>
                  <span className="muted-2">{t('tavern_app.ai_reply.desc')}</span>
                </span>
              </button>
            </div>
          </div>
        )}
        {!aiReplyOnly && ATTACH_GROUPS.map(g => (
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

export { SLASH_COMMANDS, CommandMenu, AttachMenu, MentionMenu };
