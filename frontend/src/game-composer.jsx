/* Composer + slash command menu + plus/attach menu + non-blocking confirm strip
   for the Game Console. */

import React from 'react';
import { useState as useStateC, useRef as useRefC, useEffect as useEffectC } from 'react';
import { Icon } from './game-icons.jsx';
import { chatComposerKey } from './responsive.jsx';
import { useTranslation } from 'react-i18next';
import GenerateImageModal from './components/GenerateImageModal.jsx';
import { lsGet, lsSet } from './lib/storage.js';
import { CommandMenu, AttachMenu, MentionMenu } from './components/game/GameComposerMenus.jsx';
import { ModelPopover, PermissionPopover, PERMISSION_OPTIONS } from './components/game/GameComposerPopovers.jsx';
import { ContextUsage } from './components/game/GameContextUsage.jsx';
// 拆分后仍从本文件转发的具名导出(消费者:game-console / tavern / mobile / ProfilePage / MdEditorAgent)。
export { ConfirmStrip } from './components/game/GameConfirmStrip.jsx';
export { SuggestionRow } from './components/game/GameSuggestionRow.jsx';
export { SLASH_COMMANDS } from './components/game/GameComposerMenus.jsx';
export { ContextBreakdownPanel } from './components/game/GameContextUsage.jsx';

import { SuggestionRow } from './components/game/GameSuggestionRow.jsx';

function Composer({
  text, setText,
  onSend, onStop, running,
  onSendRaw,   // task 130: 一键继续 — 直接发任意文本不经过 textarea
  permission, setPermission,
  model, setModel,
  // 复用方自定义模型选择的落库目标。默认 null = 全局 gm / 存档级(游戏·酒馆不变)。剧本编辑器传
  // {persistShape:'dict', dictKey:'console_assistant_model_override'} → 切模型只改编辑器 agent 模型,
  // 不污染游戏 GM 模型。
  modelPersist = null,
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
  // 酒馆模式复用:可选隐藏左下角的控制按钮 + 自定义占位符。默认 false → Game Console 不受影响。
  hideSlash = false, hidePermission = false, hideContinue = false, hideAttach = false,
  // 剧本编辑器右栏复用(窄栏):隐藏模型选择 + 上下文用量环(agent 用 console_assistant 默认模型,无每条模型选择)。默认 false → 游戏/酒馆不受影响。
  hideModel = false, hideContextUsage = false,
  // 复用方可限制权限档(传 id 数组,如 ['read_only','review','full_access'])+ 用独立的 enterToSend 持久化键(默认沿用游戏键)。
  permissionOptions = null,
  enterToSendKey = "rpg.game.enterToSend",
  placeholder,
  // 生图按钮相关
  saveId: composerSaveId,
  imageGenKind = 'game',
  hideImageGen = false,
  // 酒馆专属:AI 帮回回调(提供时在 + 菜单内追加「AI 帮回」入口,仅限酒馆上下文,游戏控制台不受影响)。
  onAiReply,
  // 酒馆专属:+ 菜单只显示「AI 帮回」,隐藏游戏附件组(file/image/章节/卡/世界书等)。
  aiReplyOnly = false,
}) {
  const { t } = useTranslation();
  const taRef = useRefC(null);
  // 发送后(text 被清空)收回自适应高度 → 变回 1 行。onChange 不会因程序性清空触发,故这里补一发。
  useEffectC(() => {
    const ta = taRef.current;
    if (ta && !text) ta.style.height = "auto";
  }, [text]);
  const plusTriggerRef = useRefC(null);
  const modelTriggerRef = useRefC(null);
  const permTriggerRef = useRefC(null);
  const slashTriggerRef = useRefC(null);  // task 141: 让 CommandMenu 能识别 trigger 不误关
  const [showImageGen, setShowImageGen] = useStateC(false);
  const isWriting = composerMode === "writing";
  const [enterToSend, setEnterToSend] = useStateC(() => {
    return lsGet(enterToSendKey) !== "0";
  });

  React.useEffect(() => {
    lsSet(enterToSendKey, enterToSend ? "1" : "0");
  }, [enterToSend, enterToSendKey]);

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

  // task 141: 从玩家消息新建分支后,把那条玩家消息塞回输入框 — 让用户能改
  // (默认 fork 行为是消息全消失,玩家会觉得自己输入丢了)。MsgActions doFork
  // 检测 role==='user' 时 dispatch rpg-composer-restore event 触发。
  React.useEffect(() => {
    const handler = (ev) => {
      const restored = (ev && ev.detail && ev.detail.text) || "";
      if (!restored) return;
      setText(restored);
      setTimeout(() => {
        const ta = taRef.current;
        if (ta && ta.focus) {
          ta.focus();
          try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (_) {}
        }
      }, 100);
    };
    window.addEventListener("rpg-composer-restore", handler);
    return () => window.removeEventListener("rpg-composer-restore", handler);
  }, [setText]);

  // PR #14: 选择斜杠命令后自动聚焦输入框,可直接回车发送或继续输入参数。
  React.useEffect(() => {
    if (!pickedCommand) return;
    const id = setTimeout(() => {
      const ta = taRef.current;
      if (ta && ta.focus) {
        ta.focus();
        try { ta.setSelectionRange(ta.value.length, ta.value.length); } catch (_) {}
      }
    }, 50);
    return () => clearTimeout(id);
  }, [pickedCommand]);

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
    if (p.name) push(p.name, (p.role || t('game.status.player')) + " · " + t('game.composer.mention_you'));
    const rels = (gameState && gameState.relationships) || {};
    for (const [name, info] of Object.entries(rels)) {
      const tone = typeof info === "string" ? info : (info?.tone || "");
      push(name, tone ? t('game_composer_extra.relationship_role', { tone }) : "");
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
    // task 141: 输入 "/foo " 后空格 = 命令选定结束,自动关闭 / 命令栏
    // 同样行为也 cover "/" 后只有空格(等于放弃命令选择)
    if (showSlash) {
      // 简单规则:文本不再以 "/" 开头,或者已经包含空格 → 关闭
      if (!newText.startsWith("/") || /\s/.test(newText)) {
        toggleSlash();
      }
    }
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
      {/* 对话建议:基于当前剧情状态常驻生成,帮助玩家决定下一步行动 */}
      {!running && <SuggestionRow suggestions={suggestions} onPick={(s) => setText(s)} />}
      {attachments?.length > 0 && (
        <div className="gc-attachments">
          {attachments.map((a, i) => (
            <span key={i} className="gc-attachment">
              <Icon name={a.kind === "image" ? "image" : a.kind === "skill" ? "spark" : a.kind === "mcp" ? "diamond" : "file"} size={12} />
              <span className="truncate">{a.name}</span>
              <button onClick={() => removeAttachment(i)} className="iconbtn" style={{width: 18, height: 18}} aria-label={t('game.composer.remove_attachment')}><Icon name="close" size={10} /></button>
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
              <button className="iconbtn" data-tip={t('game.composer.remove_command_tip')} onClick={onClearCommand} style={{width: 18, height: 18}} aria-label={t('game.composer.remove_command_tip')}>
                <Icon name="close" size={10} />
              </button>
            </div>
          )}
          <textarea
            ref={taRef}
            className={`gc-textarea ${isWriting ? "serif" : ""} gc-textarea-autogrow`}
            placeholder={pickedCommand
              ? (pickedCommand.hint.replace(pickedCommand.trigger, "").trim() || t('game.composer.placeholder_command'))
              : (placeholder
              || (isWriting
              ? t(enterToSend ? 'game.composer.placeholder_writing_enter_send' : 'game.composer.placeholder_writing_newline')
              : t('game.composer.placeholder_compact')))}
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
                fn(e, () => onSend && onSend(), { enterToSend });
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
            {!hideAttach && (
              <button ref={plusTriggerRef} className={`iconbtn ${showPlus ? "active" : ""}`} onClick={togglePlus} data-tip={t('game.composer.attach_tip')}>
                <Icon name="plus" size={14} />
              </button>
            )}
            {!hideSlash && (
              <button ref={slashTriggerRef} className={`iconbtn ${showSlash ? "active" : ""}`} onClick={toggleSlash} data-tip={t('game.composer.command_tip')}>
                <Icon name="slash" size={14} />
              </button>
            )}
            {!hideImageGen && (
              <button className="iconbtn" onClick={() => setShowImageGen(true)} data-tip={t('game.composer.image_gen_tip')}>
                <Icon name="image" size={14} />
              </button>
            )}
            {/* task 130: 一键继续推进 — 玩家被动场景 (昏迷/旁观/过场) 直接让 GM 推一段 */}
            {!hideContinue && !running && (
              <button
                className="gc-pop-trigger"
                onClick={() => onSendRaw && onSendRaw(t('game.composer.continue_text'))}
                data-tip={t('game.composer.continue_tip')}
                disabled={!onSendRaw}>
                <Icon name="play" size={12} />
                <span>{t('game.composer.continue')}</span>
              </button>
            )}
            {!hidePermission && (
              <button ref={permTriggerRef} className="gc-pop-trigger" onClick={togglePerm}>
                <Icon name={PERMISSION_OPTIONS.find(p => p.id === permission)?.icon || "lock"} size={12} />
                <span>{t(PERMISSION_OPTIONS.find(p => p.id === permission)?.labelKey || 'game.permission.default_label')}</span>
                <Icon name="chevron_down" size={11} />
              </button>
            )}
          </div>
          <div className="gc-composer-right">
            {!hideContextUsage && <ContextUsage gameState={gameState} />}
            {!hideModel && (
            <button ref={modelTriggerRef} className="gc-pop-trigger" onClick={toggleModel}>
              <Icon name="sparkle" size={12} />
              <span className="gc-model-label" title={_currentModelLabel(gameState, model, t)}>{_currentModelLabel(gameState, model, t)}</span>
              <Icon name="chevron_down" size={11} />
            </button>
            )}
            <span className="muted-2" style={{fontSize: 11.5}}>
              {enterToSend
                ? <><span className="kbd">Enter</span></>
                : <><span className="kbd">⌘</span> + <span className="kbd">⏎</span></>}
            </span>
            <button
              className={`iconbtn ${enterToSend ? "active" : ""}`}
              onClick={() => setEnterToSend(v => !v)}
              data-tip={t(enterToSend ? 'game.composer.enter_send_on_tip' : 'game.composer.enter_send_off_tip')}>
              <span className="mono" style={{fontSize: 11}}>↵</span>
            </button>
            {running ? (
              <button className="btn danger" onClick={onStop}>
                <Icon name="stop" size={12} /> {t('game.composer.stop')}
              </button>
            ) : (
              <button
                className="btn primary"
                onClick={onSend}
                disabled={!text.trim() && !attachments?.length && !pickedCommand}
              >
                <Icon name="send" size={12} /> {t('game.composer.send')}
              </button>
            )}
          </div>
        </div>
        {/* popovers */}
        {showSlash && <CommandMenu query={text} onPick={onSlashPick} onClose={toggleSlash} triggerRef={slashTriggerRef} />}
        {mention && filteredChars.length > 0 && (
          <MentionMenu chars={filteredChars} query={mention.query} onPick={insertMention} onClose={() => setMention(null)} />
        )}
        {showPlus && <AttachMenu onPick={onAttachPick} onClose={togglePlus} triggerRef={plusTriggerRef} onAiReply={onAiReply} aiReplyOnly={aiReplyOnly} />}
        {showModel && <ModelPopover current={model} onPick={(id) => { setModel(id); toggleModel(); }} align="right" gameState={gameState} onClose={toggleModel} triggerRef={modelTriggerRef} persist={modelPersist} />}
        {showPerm && <PermissionPopover current={permission} optionIds={permissionOptions} onPick={(id) => { setPermission(id); togglePerm(); }} onClose={togglePerm} triggerRef={permTriggerRef} />}
        {showImageGen && (
          <GenerateImageModal
            open={showImageGen}
            onClose={() => setShowImageGen(false)}
            kind={imageGenKind}
            saveId={composerSaveId}
            defaultPrompt=""
            onDone={() => {}}
          />
        )}
      </div>
    </div>
  );
}


// 取当前模型的展示标签。
// 优先级：localModel（pickModel 后立即乐观更新）> gameState.app.model（后端刷新后）> 占位符。
// Bug fix: 原来 _ignored 完全忽略 local model，导致切换后底部标签不更新直到 reloadState。
// 用 gameState.models(catalog) 把 model_id 解析为 display_name；找不到就直接显示 id。
function _currentModelLabel(gameState, localModel, t) {
  const _placeholder = () => (t ? t('game.composer.model_placeholder') : "Model");
  const catalog = gameState && gameState.models;
  const apis = (catalog && Array.isArray(catalog.apis)) ? catalog.apis : null;
  // 把 id 解析成 {label, cred}。cred = 所属 provider 是否已配置 key。
  // 不在 catalog 里的(自定义模型)按可用处理,直接显示 id。
  const _resolve = (id) => {
    if (!id) return null;
    if (apis) {
      for (const api of apis) {
        for (const m of (api.models || [])) {
          if (m.id === id || m.real_name === id) {
            return { label: m.display_name || m.real_name || m.id, cred: api.has_credential !== false };
          }
        }
      }
    }
    return { label: id, cred: true };  // 自定义/未在 catalog → 直接显示
  };
  // catalog 已加载但没有任何「已配置 key」的 provider → 用户无可用模型,
  // 绝不回退显示一个他用不了的默认模型(否则删光 key 仍显示 Opus,误导)。
  // 后端权威标记:用户无任何可用(已配 key)模型 → 提示去配置,绝不显示用不了的全局默认。
  if (catalog && catalog.needs_model_config) return (t ? t('game.composer.model_needs_config') : 'Set up model');
  if (apis && !apis.some((a) => a.has_credential && (a.models || []).length)) return _placeholder();
  // 解析优先级:localModel(乐观更新) > 存档 session_model > catalog.selected(per-user 默认) > 后端全局 app。
  // 必须含 catalog.selected —— 否则刷新后掉到 app.model(可能是全局默认 opus)而显示用不了的模型;
  // 且与 ModelPopover 选中态(selectedKey)同源,避免「勾在 A、底部显示 B」。只显示「有凭证」的那个。
  const sessionModel = gameState && gameState.session_model;
  const catSel = catalog && catalog.selected;
  const candidates = [
    localModel,
    sessionModel && (sessionModel.model_id || sessionModel.model_real_name),
    catSel && (catSel.model_id || catSel.model_real_name),
    gameState && gameState.app && gameState.app.model,
  ];
  for (const id of candidates) {
    const r = _resolve(id);
    if (r && r.cred) return r.label;
  }
  return _placeholder();
}


export { Composer, MentionMenu, PERMISSION_OPTIONS, ContextUsage };
