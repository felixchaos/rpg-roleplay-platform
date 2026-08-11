/* Game Console 聊天消息簇(MsgActions / Fork+DeleteConfirmModal / NarrativeBlock /
   PlayerBlock / ChatImageGroup / useSaveImages / SaveImagesStrip /
   renderNarrativeWithInlineTools)—— 纯机械从 game-app.jsx 搬出,DOM/视觉/行为零变化。 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { createPortal } from 'react-dom';
import { useState as useStateA, useEffect as useEffectA, useRef as useRefA, useMemo as useMemoA } from 'react';
import { Icon } from '../../game-icons.jsx';
import Modal from '../Modal.jsx';
import { RpgMarkdown } from '../../markdown-render.jsx';
import AvatarImg from '../AvatarImg.jsx';
import { stripNarrativeOps } from '../../narrative-strip.js';
import { lsGetJSON, lsSetJSON } from '../../lib/storage.js';
import { copyText } from '../../lib/clipboard.js';

// ----------------------------- CHAT --------------------------------------
function MsgActions({ text, ts, msgIndex, totalMsgs, commitId, saveId, role, meta, memoryText }) {
  // task 38：以前 msgIndex / saveId / commitId 全是 undefined，doFork 就发
  // {label} 给后端 → 后端 int(None) 直接 500。现在 NarrativeBlock / PlayerBlock
  // 把 idx + saveId 透传进来，doFork 至少发 {save_id, message_index, label}，
  // 后端通过 resolve_commit_id_by_message 解析。
  const { t } = useTranslation();
  const [copied, setCopied] = useStateA(false);
  const [forkOpen, setForkOpen] = useStateA(false);
  // task 116c: 删除消息 (软回滚) — 弹窗确认 + 进度
  const [delOpen, setDelOpen] = useStateA(false);
  const [delBusy, setDelBusy] = useStateA(false);

  const [copyWithMemory, setCopyWithMemory] = useStateA(false);
  const [copyAskOpen, setCopyAskOpen] = useStateA(false);
  const hasMemory = !!(memoryText && memoryText.trim());
  const doCopy = async (includeMemory) => {
    const txt = includeMemory && hasMemory ? memoryText.trim() + "\n\n---\n\n" + (text || "") : (text || "");
    const ok = await copyText(txt);
    setCopied(true);
    if (window.toast) {
      if (ok) window.toast(t('game.app.msg.copied'), { kind: "ok", detail: txt.slice(0, 40) + (txt.length > 40 ? "…" : ""), duration: 1600 });
      else window.toast(t('game.app.msg.copy_failed'), { kind: "danger", detail: t('game.app.msg.clipboard_denied'), duration: 2400 });
    }
    setTimeout(() => setCopied(false), 1400);
    setCopyAskOpen(false);
  };
  const onCopy = () => {
    if (hasMemory) {
      setCopyAskOpen(true);
    } else {
      doCopy(false);
    }
  };
  // task 38：禁用条件——必须有 saveId 或 commitId 之一，否则后端无法定位 commit。
  // 缺信息时按钮 disabled + tooltip 解释，比让用户点进去看 toast 失败强。
  const canFork = (commitId != null && commitId !== "") || (saveId != null && msgIndex != null);
  const onFork = () => {
    if (!canFork) {
      window.toast?.(t('game.app.msg.fork_failed'), {
        kind: "warn",
        detail: t('game.app.msg.fork_no_ctx'),
        duration: 2400,
      });
      return;
    }
    setForkOpen(true);
  };
  // 反馈:每条消息加「重新生成这一轮」快捷按钮(就在分支按钮边上)。
  // 实际逻辑在 game-console 顶层 onRegenerate:fork 到本轮之前(复用 resolve_commit_id_by_message)
  // → 截断历史 → 用同样的玩家输入重走完整 GM 流程。这里只派发事件(避免 prop 一路透传)。
  const canRegen = saveId != null && msgIndex != null && msgIndex >= 0;
  const onRegenerate = () => {
    if (!canRegen) {
      window.toast?.(t('game.app.msg.regen_failed'), { kind: "warn", detail: t('game.app.msg.fork_no_ctx'), duration: 2400 });
      return;
    }
    window.dispatchEvent(new CustomEvent("rpg-regenerate", { detail: { save_id: saveId, message_index: msgIndex } }));
  };
  const doFork = async () => {
    setForkOpen(false);
    // 优先 node_id (commitId)；否则发 save_id + message_index 让后端 resolve。
    const body = { label: t('game.app.msg.fork_label') };
    if (commitId != null && commitId !== "") {
      body.node_id = commitId;
    } else if (saveId != null && msgIndex != null) {
      body.save_id = saveId;
      body.message_index = msgIndex;
    }
    try {
      const r = await window.api.branches.continueFrom(body);
      if (r && r.ok === false) {
        throw new Error(r.error || r.detail || "branch create denied");
      }
      // task 87：后端已经把新分支设为 active ref + 切换 runtime。
      // 必须 dispatch event 让 Game Console 顶层重载 /api/state（chat
      // history / activeSave / right panel / branch tree 全部刷新），
      // 否则用户只看到 toast，UI 完全没动 → 看着像"按了没反应"。
      const newCommitId = r?.active_branch_node_id || r?.active_commit_id;
      const branchHint =
        (r?.active_ref?.name && r.active_ref.name.split("/").pop()) ||
        (newCommitId ? t('game.app.msg.node_label', { id: newCommitId }) : t('game.app.msg.new_branch'));
      try {
        window.dispatchEvent(new CustomEvent("rpg-state-reload", {
          detail: { reason: "branch_fork", new_commit_id: newCommitId },
        }));
        window.dispatchEvent(new CustomEvent("rpg-saves-updated"));
      } catch (_) {}
      // task 141: 从玩家消息 fork → 那条消息其实是玩家想"在这里换说法重发",
      // 把它塞回输入框,不要让玩家手动复制粘贴。仅对 role='user' 触发。
      if (role === "user" && text) {
        // 等 state reload 完(rpg-state-reload 触发的 fetch 跑完),再写输入框,
        // 否则 Composer 重渲染会清空。延迟一帧足够让大部分 reload 完成。
        setTimeout(() => {
          try {
            window.dispatchEvent(new CustomEvent("rpg-composer-restore", {
              detail: { text },
            }));
          } catch (_) {}
        }, 250);
      }
      window.toast?.(t('game.app.msg.fork_switched'), {
        kind: "ok",
        detail: branchHint + (role === "user" ? " · " + t('game.app.msg.fork_restore_input') : " · " + t('game.app.msg.fork_on_branch')),
        duration: 2400,
      });
    } catch (e) {
      window.toast?.(t('game.app.msg.fork_create_failed'), { kind: "danger", detail: e?.message, duration: 3000 });
    }
  };
  // task 116c: 删除条件 — 必须有 saveId + msgIndex >= 0
  const canDelete = saveId != null && msgIndex != null && msgIndex >= 0;
  const doDelete = async () => {
    if (!canDelete || delBusy) return;
    setDelBusy(true);
    try {
      const r = await window.api.branches.rollbackToMessage(saveId, msgIndex);
      if (r && r.ok === false) {
        throw new Error(r.error || r.detail || "delete denied");
      }
      setDelOpen(false);
      const d = r?.deleted || {};
      // 让 Game Console 重载 state — 同 fork 路径
      try {
        window.dispatchEvent(new CustomEvent("rpg-state-reload", {
          detail: { reason: "rollback_delete", new_commit_id: r?.active_commit_id },
        }));
        window.dispatchEvent(new CustomEvent("rpg-saves-updated"));
      } catch (_) {}
      const detail = t('game.app.msg.delete_detail', { count: d.messages || 0, turn: (r?.restored_turn ?? -1) + 1 })
        + (r?.trash_ref ? " · " + t('game.app.msg.delete_trash', { name: r.trash_ref.name || "trash" }) : "");
      window.toast?.(t('game.app.msg.deleted'), { kind: "ok", detail, duration: 3200 });
    } catch (e) {
      window.toast?.(t('game.app.msg.delete_failed'), { kind: "danger", detail: e?.message, duration: 3000 });
    } finally {
      setDelBusy(false);
    }
  };
  // 编辑消息:GM 正文与玩家发言都可编辑 → 派发事件让对应 Block 进入内联编辑。
  // 玩家发言此前被这里的 role === "assistant" 挡掉,想补一句话只能"复制→删除→重打"
  // (群反馈:白玖)。后端 /api/message/edit 本来就明确不限角色,是前端单方面收窄了。
  const canEdit = (role === "assistant" || role === "user")
    && saveId != null && msgIndex != null && msgIndex >= 0;
  const onEdit = () => {
    if (!canEdit) return;
    window.dispatchEvent(new CustomEvent("rpg-edit-message", { detail: { saveId, msgIndex } }));
  };
  return (
    <>
      <div className="gc-msg-actions">
        <button className="iconbtn gc-msg-act" data-tip={copied ? t('game.app.msg.copied') : t('game.app.msg.copy')} data-tip-pos="below" onClick={onCopy} aria-label={copied ? t('game.app.msg.copied') : t('game.app.msg.copy')}>
          <Icon name={copied ? "check" : "file"} size={12} />
        </button>
        {copyAskOpen && (
          <div className="gc-copy-ask" role="dialog" aria-label={t('game.app.msg.copy_ask_aria')}>
            <label className="gc-copy-ask-label">
              <input type="checkbox" checked={copyWithMemory} onChange={(e) => setCopyWithMemory(e.target.checked)} />
              {" "}{t('game.app.msg.copy_with_memory')}
            </label>
            <div className="gc-copy-ask-actions">
              <button className="btn ghost" onClick={() => { doCopy(copyWithMemory); }}>{t('game.app.msg.copy')}</button>
              <button className="iconbtn" onClick={() => setCopyAskOpen(false)} aria-label={t('common.close')}><Icon name="close" size={11} /></button>
            </div>
          </div>
        )}
        <button
          className="iconbtn gc-msg-act"
          data-tip={canFork ? t('game.app.msg.fork_tip') : t('game.app.msg.fork_no_ctx_tip')}
          data-tip-pos="below"
          disabled={!canFork}
          aria-label={canFork ? t('game.app.msg.fork_tip') : t('game.app.msg.fork_no_ctx_tip')} onClick={onFork}>
          <Icon name="fork" size={12} />
        </button>
        <button
          className="iconbtn gc-msg-act"
          data-tip={canRegen ? t('game.app.msg.regen_tip') : t('game.app.msg.regen_no_ctx_tip')}
          data-tip-pos="below"
          disabled={!canRegen}
          aria-label={canRegen ? t('game.app.msg.regen_tip') : t('game.app.msg.regen_no_ctx_tip')} onClick={onRegenerate}>
          <Icon name="refresh" size={12} />
        </button>
        {canEdit && (
          <button
            className="iconbtn gc-msg-act"
            data-tip={t('game.app.msg.edit_tip')}
            data-tip-pos="below"
            onClick={onEdit}>
            <Icon name="edit" size={12} />
          </button>
        )}
        <button
          className="iconbtn gc-msg-act gc-msg-act-danger"
          data-tip={canDelete ? t('game.app.msg.delete_tip') : t('game.app.msg.delete_no_ctx_tip')}
          data-tip-pos="below"
          disabled={!canDelete}
          aria-label={canDelete ? t('game.app.msg.delete_tip') : t('game.app.msg.delete_no_ctx_tip')} onClick={() => setDelOpen(true)}>
          <Icon name="trash" size={12} />
        </button>
        <span className="gc-msg-ts mono">{ts}</span>
        {meta ? <span className="gc-msg-meta mono muted-2" data-tip={t('game.app.msg.meta_tip')}>{meta}</span> : null}
      </div>
      <ForkConfirmModal open={forkOpen} text={text} onClose={() => setForkOpen(false)} onConfirm={doFork} />
      <DeleteConfirmModal
        open={delOpen}
        text={text}
        msgIndex={msgIndex}
        role={role}
        busy={delBusy}
        onClose={() => !delBusy && setDelOpen(false)}
        onConfirm={doDelete}
      />

    </>
  );
}

// task 116c: 删除消息 → 软回滚到 turn N-1 的确认弹窗。
// 警告用户:这会丢弃后续所有对话和世界线;但 git-style 保留了旧分支(refs/trash/...)可恢复。
function DeleteConfirmModal({ open, text, msgIndex, role, busy, onClose, onConfirm }) {
  const { t } = useTranslation();
  if (!open) return null;
  const preview = (text || "").slice(0, 80) + ((text || "").length > 80 ? "…" : "");
  const turnOfMsg = msgIndex != null && msgIndex >= 0 ? Math.floor(msgIndex / 2) : null;
  const restoreTurn = turnOfMsg != null ? turnOfMsg - 1 : null;
  const isAssistant = role === "assistant";
  const node = (
    <Modal
      open
      width={480}
      closeDisabled={busy}
      onClose={onClose}
      header={
        <div>
          <div className="pl-modal-eyebrow" style={{color: "var(--danger)"}}>{t('game.app.delete_modal.eyebrow')}</div>
          <h2 className="pl-modal-title">{t('game.app.delete_modal.title')}</h2>
        </div>
      }
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> POST /api/branches/rollback
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose} disabled={busy}>{t('common.cancel')}</button>
          <button className="btn danger" onClick={onConfirm} disabled={busy}>
            {busy
              ? <><span className="gc-spinner spin" /> {t('game.app.delete_modal.deleting')}</>
              : <><Icon name="trash" size={12} /> {t('game.app.delete_modal.confirm_delete')}</>}
          </button>
        </div>
      </>}
    >
      <div style={{fontSize: 13.5, lineHeight: 1.7, color: "var(--text-quiet)"}}>
        {t('game.app.delete_modal.irreversible')} {isAssistant ? t('game.app.delete_modal.this_gm_reply') : t('game.app.delete_modal.this_message')}<strong style={{color: "var(--danger)"}}>{t('game.app.delete_modal.all_after')}</strong>{t('game.app.delete_modal.discarded')}
        <div style={{
          marginTop: 10, padding: "10px 12px",
          background: "var(--bg-deep)", border: "1px solid var(--line-soft)",
          borderRadius: 6, fontFamily: "var(--font-serif)", fontSize: 13,
          color: "var(--text-quiet)", borderLeft: "2px solid var(--danger)",
        }}>
          {preview || t('game.app.delete_modal.empty_msg')}
        </div>
        <div style={{marginTop: 10, fontSize: 12, color: "var(--muted)"}}>
          {isAssistant
            ? <>{t('game.app.delete_modal.restore_before_gm')}</>
            : restoreTurn != null && restoreTurn >= 0
            ? <>{t('game.app.delete_modal.restore_turn', { turn: restoreTurn + 1 })}</>
            : <>{t('game.app.delete_modal.restore_start')}</>}
          <br />
          {t('game.app.delete_modal.trash_hint')} <code style={{fontFamily: "var(--font-mono)", fontSize: 11}}>refs/trash/...</code>
          {t('game.app.delete_modal.trash_recover')}
        </div>
      </div>
    </Modal>
  );
  return createPortal(node, document.body);
}

function ForkConfirmModal({ open, text, onClose, onConfirm }) {
  const { t } = useTranslation();
  if (!open) return null;
  const preview = (text || "").slice(0, 80) + ((text || "").length > 80 ? "…" : "");
  const node = (
    <Modal
      open
      eyebrow={t('game.app.fork_modal.eyebrow')}
      title={t('game.app.fork_modal.title')}
      width={460}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> POST /api/branches/continue
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="btn primary" onClick={onConfirm}>
            <Icon name="fork" size={12} /> {t('game.app.fork_modal.new_branch')}
          </button>
        </div>
      </>}
    >
      <div style={{fontSize: 13.5, lineHeight: 1.7, color: "var(--text-quiet)"}}>
        {t('game.app.fork_modal.body')}
        <div style={{
          marginTop: 10, padding: "10px 12px",
          background: "var(--bg-deep)", border: "1px solid var(--line-soft)",
          borderRadius: 6, fontFamily: "var(--font-serif)", fontSize: 13,
          color: "var(--text-quiet)", borderLeft: "2px solid var(--accent-edge)",
        }}>
          {preview}
        </div>
      </div>
    </Modal>
  );
  return createPortal(node, document.body);
}

function stripStateOpsForDisplay(text) {
  // 旧版本只剥 fenced JSON,裸数组([{...,"op":...}])漏过 — 改走统一的 stripNarrativeOps。
  // opening message 写回 history 时未 strip,主聊天区也得展示层兜底过滤。
  return stripNarrativeOps(text);
}

// 把工具调用按 anchor(触发时的正文长度)内联进正文 —— Claude 风,工具卡片出现在它实际发生
// 的文本位置,而不是永远置顶。anchor 是【原始 content】的偏移(与后端 len(response) 一致),
// 故先按 anchor 切原始文本、每段再 stripStateOpsForDisplay,避免 strip 改变长度造成错位。
// renderTool(opsAtAnchor) 由调用方提供(酒馆传 ToolCallBlock)。同一 anchor 的多个工具合并成一组。
function renderNarrativeWithInlineTools(rawText, toolOps, renderTool, streaming, MdBlock) {
  const text = rawText || "";
  const ops = toolOps
    .map((o) => ({ op: o, a: Math.max(0, Math.min(Number.isFinite(o && o.anchor) ? o.anchor : text.length, text.length)) }))
    .sort((x, y) => x.a - y.a);
  const groups = [];
  for (const it of ops) {
    const g = groups[groups.length - 1];
    if (g && g.anchor === it.a) g.ops.push(it.op);
    else groups.push({ anchor: it.a, ops: [it.op] });
  }
  const nodes = [];
  let prev = 0;
  groups.forEach((g, gi) => {
    const chunk = stripStateOpsForDisplay(text.slice(prev, g.anchor));
    if (chunk.trim()) {
      nodes.push(MdBlock
        ? <MdBlock key={`tx-${gi}`} text={chunk} streaming={false} className="rpg-md" />
        : <p key={`tx-${gi}`}>{chunk}</p>);
    }
    nodes.push(<React.Fragment key={`tl-${gi}`}>{renderTool(g.ops)}</React.Fragment>);
    prev = g.anchor;
  });
  const tail = stripStateOpsForDisplay(text.slice(prev));
  if (tail.trim() || nodes.length === 0) {
    nodes.push(MdBlock
      ? <MdBlock key="tx-tail" text={tail} streaming={!!streaming} className="rpg-md" />
      : <p key="tx-tail">{tail}{streaming && <span className="gc-cursor" />}</p>);
  }
  return nodes;
}

/* 消息内联编辑 —— GM 正文(NarrativeBlock)与玩家发言(PlayerBlock)共用同一套状态机,
   避免两边各写一份、日后只改其中一份(本仓固有的「修 A 漏 B」)。
   激活方式:MsgActions 的编辑按钮派发 rpg-edit-message {saveId, msgIndex}。 */
function useInlineMessageEdit({ saveId, msgIndex, sourceText }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useStateA(false);
  const [draft, setDraft] = useStateA("");
  const [saving, setSaving] = useStateA(false);
  const ref = useRefA(null);
  useEffectA(() => {
    const handler = (e) => {
      const d = e.detail || {};
      if (d.saveId === saveId && d.msgIndex === msgIndex) {
        setDraft(sourceText || "");
        setEditing(true);
      }
    };
    window.addEventListener("rpg-edit-message", handler);
    return () => window.removeEventListener("rpg-edit-message", handler);
  }, [saveId, msgIndex, sourceText]);
  // 进入编辑模式后自动 focus textarea 并定位到末尾
  useEffectA(() => {
    if (editing && ref.current) {
      const el = ref.current;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }, [editing]);
  const cancel = () => setEditing(false);
  const save = async () => {
    if (saving) return;
    setSaving(true);
    try {
      const r = await window.api.game.editMessage({ save_id: saveId, message_index: msgIndex, content: draft });
      if (r && r.ok === false) throw new Error(r.error || "edit failed");
      setEditing(false);
      try {
        window.dispatchEvent(new CustomEvent("rpg-state-reload", { detail: { reason: "message_edit" } }));
      } catch (_) {}
      window.toast?.(t('game.app.msg.edit_saved'), { kind: "ok", duration: 1800 });
    } catch (e) {
      window.toast?.(t('game.app.msg.edit_failed'), { kind: "danger", detail: e?.message, duration: 3000 });
    } finally {
      setSaving(false);
    }
  };
  return { editing, draft, setDraft, saving, save, cancel, ref };
}

/* 内联编辑器视图(textarea + 取消/确认)。minHeight 由调用方给:GM 正文长、玩家发言短。 */
function InlineMessageEditor({ edit, minHeight = 180 }) {
  const { t } = useTranslation();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <textarea
        ref={edit.ref}
        value={edit.draft}
        onChange={e => edit.setDraft(e.target.value)}
        disabled={edit.saving}
        onKeyDown={e => {
          if (e.key === "s" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); edit.save(); }
          if (e.key === "Escape") { edit.cancel(); }
        }}
        style={{
          width: "100%", minHeight, maxHeight: "60vh", resize: "vertical",
          fontFamily: "var(--font-serif, inherit)", fontSize: "var(--d-narrative, 16.5px)",
          lineHeight: "var(--d-line, 1.78)", padding: "10px 12px", borderRadius: 8,
          border: "1px solid var(--accent-edge, var(--line))",
          background: "var(--bg-deep, #1a1816)", color: "var(--text)", outline: "none",
          letterSpacing: "0.02em",
        }}
      />
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn ghost" disabled={edit.saving} onClick={edit.cancel}>
          {t('common.cancel')}
        </button>
        <button className="btn primary" disabled={edit.saving || !edit.draft.trim()} onClick={edit.save}>
          {edit.saving
            ? <><span className="gc-spinner spin" /> {t('game.app.edit_modal.saving')}</>
            : <><Icon name="check" size={12} /> {t('game.app.edit_modal.confirm')}</>}
        </button>
      </div>
    </div>
  );
}

// 酒馆模式复用:speakerName/speakerAvatar/tag 可选覆盖默认的 GM/主代理 标签。
// 不传时与 Game Console 行为完全一致(默认 tag="GM", subtitle="主代理")。
function NarrativeBlock({ text, streaming, ts, msgIndex, saveId, commitId, thinking, speakerName, speakerAvatar, tag, hideMeta, meta, images, toolOps, renderTool, memoryText }) {
  const { t } = useTranslation();
  const displayText = stripStateOpsForDisplay(text);
  // 内联编辑状态:点击 MsgActions 的编辑按钮时通过事件激活(与 PlayerBlock 共用同一 hook)
  const edit = useInlineMessageEdit({ saveId, msgIndex, sourceText: displayText });
  // task 90: 用 RpgMarkdown.Block 渲染 markdown (** / # / list / code / link...)
  // window.RpgMarkdown 由 markdown-render.jsx 提供,加载顺序在 game-app.jsx 之前。
  const MdBlock = RpgMarkdown.Block;
  const tagLabel = tag || "GM";
  // 酒馆模式显式传 speakerName="" → 隐藏副标题(只显示角色名 tag);
  // Game Console 不传(undefined)→ 默认"主代理"(零回归)。
  const subLabel = speakerName === "" ? "" : (speakerName || t('game.app.narrative.main_agent'));
  // task 121a: thinking 状态显示带 spinner 的 italic 文字,跟正式 narrative 区分
  // speakerAvatar 兼容:若为 URL(/ 或 http 开头)则渲 AvatarImg,否则保持首字母 span(向后兼容)。
  const isAvatarUrl = speakerAvatar && (speakerAvatar.startsWith('/') || speakerAvatar.startsWith('http'));
  const avatarNode = speakerAvatar
    ? (isAvatarUrl
        ? <AvatarImg src={speakerAvatar} size={28} shape="circle" />
        : <span className="gc-msg-avatar serif">{speakerAvatar}</span>)
    : null;

  if (thinking) {
    return (
      <div className="gc-msg gc-msg-gm gc-msg-thinking">
        {!hideMeta && (
          <div className="gc-msg-meta">
            {avatarNode}
            <span className="gc-msg-tag">{tagLabel}</span>
            <span className="muted-2" style={{ fontSize: 11.5 }}>{t('game.app.narrative.preparing')}</span>
          </div>
        )}
        <div className="gc-msg-body" style={{ fontStyle: "italic", color: "var(--text-quiet)", opacity: 0.85 }}>
          <span className="gc-spinner spin" /> {text || t('game.app.narrative.please_wait')}
        </div>
      </div>
    );
  }
  return (
    <div className="gc-msg gc-msg-gm">
      {!hideMeta && (
        <div className="gc-msg-meta">
          {avatarNode}
          <span className="gc-msg-tag">{tagLabel}</span>
          {subLabel && <span className="muted-2" style={{ fontSize: 11.5 }}>{subLabel}</span>}
        </div>
      )}
      <div className="gc-msg-body serif">
        {edit.editing ? (
          <InlineMessageEditor edit={edit} minHeight={180} />
        ) : (
          <>
            {(Array.isArray(toolOps) && toolOps.length > 0 && typeof renderTool === 'function')
              ? renderNarrativeWithInlineTools(text, toolOps, renderTool, streaming, MdBlock)
              : (MdBlock
                  ? <MdBlock text={displayText || ""} streaming={!!streaming} className="rpg-md" />
                  : (displayText || "").split(/\n\n+/).map((p, i) =>
                      <p key={i}>{p}{streaming && i === (displayText || "").split(/\n\n+/).length - 1 && <span className="gc-cursor" />}</p>
                    )
                )
            }
            <ChatImageGroup images={images} />
          </>
        )}
      </div>
      {!streaming && <MsgActions text={displayText} ts={ts || "—"} msgIndex={msgIndex} saveId={saveId} commitId={commitId} role="assistant" meta={meta} memoryText={memoryText} />}
    </div>);

}

// 酒馆模式复用:speakerName/tag 可选覆盖默认「玩家」标签(persona 名等)。
function PlayerBlock({ text, ts, attachments, msgIndex, saveId, commitId, speakerName, speakerAvatar, tag, hideMeta, memoryText }) {
  const { t } = useTranslation();
  // 玩家发言同样可内联编辑(群反馈:白玖 ——「只是想加一句话,要复制删除粘贴太麻烦」)。
  // 与 GM 正文共用 useInlineMessageEdit;后端 /api/message/edit 本就不限角色。
  const edit = useInlineMessageEdit({ saveId, msgIndex, sourceText: text });
  const tagLabel = tag || speakerName || t('game.app.narrative.player');
  // speakerAvatar 兼容:若为 URL(/ 或 http 开头)则渲 AvatarImg,否则保持首字母 span(向后兼容)。
  const isAvatarUrl = speakerAvatar && (speakerAvatar.startsWith('/') || speakerAvatar.startsWith('http'));
  const avatarNode = speakerAvatar
    ? (isAvatarUrl
        ? <AvatarImg src={speakerAvatar} size={28} shape="circle" />
        : <span className="gc-msg-avatar serif">{speakerAvatar}</span>)
    : null;
  return (
    <div className="gc-msg gc-msg-player">
      {!hideMeta && (
        <div className="gc-msg-meta">
          {avatarNode}
          <span className="gc-msg-tag muted">{tagLabel}</span>
        </div>
      )}
      <div className="gc-msg-body">
        {edit.editing ? (
          /* 玩家发言通常只有一两行,编辑框给个更贴身的高度(GM 正文用 180) */
          <InlineMessageEditor edit={edit} minHeight={96} />
        ) : (
          <>
            <p>{text}</p>
            {attachments?.length > 0 &&
            <div className="gc-attachments" style={{ marginTop: 6 }}>
                {attachments.map((a, i) =>
              <span key={i} className="gc-attachment">
                    <Icon name={a.kind === "image" ? "image" : "file"} size={12} />
                    {a.name}
                  </span>
              )}
              </div>
            }
          </>
        )}
      </div>
      <MsgActions text={text} ts={ts} msgIndex={msgIndex} saveId={saveId} commitId={commitId} role="user" memoryText={memoryText} />
    </div>);

}

// ── 聊天内嵌图片(GPT 风:图片是回复的一部分,渲在助手消息气泡内)─────────────
// 关联策略:实时到达 → 归到当前最后一条助手消息(lastKeyRef);并把 {imageId: msgKey}
// 持久化到 localStorage(按 saveId),刷新后位置仍在。未映射的旧图回退到最后助手消息。
// msgKey = 助手消息的绝对索引字符串(append-only history 跨刷新稳定)。
function _imgMapKey(saveId) { return `rpg.imgmsg.${saveId}`; }
function _loadImgMap(saveId) {
  return lsGetJSON(_imgMapKey(saveId), {});
}
function _saveImgMap(saveId, map) {
  lsSetJSON(_imgMapKey(saveId), map);
}

// 返回 { msgKey: images[] };未映射的归入 '__last' 桶(由调用方挂到最后助手消息)。
export function useSaveImages(saveId, lastKeyRef) {
  const [images, setImages] = useStateA([]);   // [{id,url,kind,key}]
  const mapRef = useRefA({});

  // 拉历史图片 + 应用持久化映射
  useEffectA(() => {
    if (saveId == null) { setImages([]); mapRef.current = {}; return; }
    let cancelled = false;
    mapRef.current = _loadImgMap(saveId);
    (async () => {
      try {
        const list = await window.api.images.list(saveId);
        if (cancelled) return;
        const done = Array.isArray(list) ? list.filter((im) => im.status === 'done' && im.url) : [];
        const map = mapRef.current;
        // 反馈#74:优先用后端权威 message_index(刷新后确定性还原),旧行回退 localStorage 映射。
        setImages(done.map((im) => ({
          id: im.id, url: im.url, kind: im.kind || 'game',
          key: (im.message_index != null ? String(im.message_index)
                : (map[im.id] != null ? String(map[im.id]) : null)),
        })));
      } catch (_) { /* 后端未实装时静默 */ }
    })();
    return () => { cancelled = true; };
  }, [saveId]);

  // SSE 实时追加,归到当前最后助手消息
  useEffectA(() => {
    if (saveId == null) return;
    const handler = (ev) => {
      const { op, payload } = (ev && ev.detail) || {};
      if (op !== 'ready') return;
      const { image_id, url, kind } = payload || {};
      if (!image_id || !url) return;
      const key = (lastKeyRef && lastKeyRef.current != null) ? String(lastKeyRef.current) : null;
      if (key != null) { mapRef.current[image_id] = key; _saveImgMap(saveId, mapRef.current); }
      setImages((prev) => prev.some((im) => im.id === image_id) ? prev
        : [...prev, { id: image_id, url, kind: kind || 'game', key }]);
    };
    window.addEventListener('rpg-image-updated', handler);
    return () => window.removeEventListener('rpg-image-updated', handler);
  }, [saveId]);

  return useMemoA(() => {
    const g = {};
    for (const im of images) {
      const k = im.key != null ? im.key : '__last';
      (g[k] = g[k] || []).push(im);
    }
    return g;
  }, [images]);
}

// 助手消息气泡内的图片组(单图自然比例,多图方形拼贴),点击全屏。
function ChatImageGroup({ images }) {
  const { t } = useTranslation();
  const [lightbox, setLightbox] = useStateA(null);
  useEffectA(() => {
    if (!lightbox) return;
    const h = (e) => { if (e.key === 'Escape') setLightbox(null); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [lightbox]);
  if (!images || !images.length) return null;
  const multi = images.length > 1;
  return (
    <div className="rpg-chat-imgs">
      {images.map((im) => (
        <button key={im.id} type="button" title={im.kind || t('game.app.image.generated')}
          className={`rpg-chat-img ${multi ? 'rpg-chat-img--multi' : 'rpg-chat-img--single'}`}
          onClick={() => setLightbox(im.url)}>
          <img src={im.url} alt="" loading="lazy" decoding="async" />
        </button>
      ))}
      {lightbox && (
        <div className="mlb-backdrop" onClick={() => setLightbox(null)} role="dialog" aria-modal="true">
          <img src={lightbox} alt="" style={{ maxWidth: '92vw', maxHeight: '90vh', objectFit: 'contain', borderRadius: 10, boxShadow: '0 12px 60px rgba(0,0,0,.7)' }} onClick={(e) => e.stopPropagation()} />
          <button onClick={() => setLightbox(null)} aria-label={t('common.close')} style={{ position: 'absolute', top: 20, right: 24, width: 38, height: 38, borderRadius: 99, border: 0, background: 'rgba(255,255,255,.14)', color: '#fff', fontSize: 19, cursor: 'pointer' }}>×</button>
        </div>
      )}
    </div>
  );
}

// ── Phase 3: 会话生成图片区(旧:底部独立 strip — 已退役为内嵌,保留定义供兼容)──────
// 挂载/saveId 变化时拉取已有图片(status==='done' && url),并订阅 SSE image topic 实时追加。
// 组件卸载时取消订阅,防泄漏。
function SaveImagesStrip({ saveId }) {
  const { t } = useTranslation();
  const [images, setImages] = useStateA([]);
  const [lightbox, setLightbox] = useStateA(null); // 当前放大的 url

  // 1. 挂载/saveId 变化时拉取历史图片
  useEffectA(() => {
    if (saveId == null) { setImages([]); return; }
    let cancelled = false;
    (async () => {
      try {
        const list = await window.api.images.list(saveId);
        if (cancelled) return;
        const done = Array.isArray(list)
          ? list.filter((img) => img.status === 'done' && img.url)
          : [];
        setImages(done);
      } catch (_) { /* 静默:后端未实装时不崩 */ }
    })();
    return () => { cancelled = true; };
  }, [saveId]);

  // 2. 订阅 SSE image topic，实时追加 ready 事件
  useEffectA(() => {
    if (saveId == null) return;
    const handler = (ev) => {
      const { op, payload } = (ev && ev.detail) || {};
      if (op !== 'ready') return;
      const { image_id, url, kind } = payload || {};
      if (!image_id || !url) return;
      setImages((prev) => {
        if (prev.some((img) => img.id === image_id)) return prev;
        return [...prev, { id: image_id, url, kind: kind || 'game', status: 'done' }];
      });
    };
    window.addEventListener('rpg-image-updated', handler);
    return () => window.removeEventListener('rpg-image-updated', handler);
  }, [saveId]);

  if (!images.length) return null;

  return (
    <div style={{
      margin: '12px 0 4px',
      padding: '10px 12px',
      background: 'var(--surface-2, rgba(255,255,255,0.03))',
      border: '1px solid var(--line-soft, rgba(255,255,255,0.07))',
      borderRadius: 8,
    }}>
      <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
        {t('game.app.image.strip_title', { count: images.length })}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {images.map((img) => (
          <button
            key={img.id}
            onClick={() => setLightbox(img.url)}
            style={{
              border: 0, padding: 0, background: 'transparent', cursor: 'pointer',
              borderRadius: 6, overflow: 'hidden', flexShrink: 0,
            }}
            title={img.prompt || img.kind || t('game.app.image.generated')}
          >
            <AvatarImg
              src={img.url}
              name={img.kind || 'img'}
              size={80}
              shape="rounded"
              className=""
            />
          </button>
        ))}
      </div>
      {lightbox && (
        <div
          onClick={() => setLightbox(null)}
          style={{
            position: 'fixed', inset: 0, zIndex: 8000,
            background: 'rgba(0,0,0,0.82)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <img
            src={lightbox}
            alt={t('game.app.image.generated')}
            style={{ maxWidth: '90vw', maxHeight: '90vh', borderRadius: 8, boxShadow: '0 8px 32px rgba(0,0,0,0.6)' }}
            onClick={(e) => e.stopPropagation()}
          />
          <button
            onClick={() => setLightbox(null)}
            style={{
              position: 'absolute', top: 20, right: 24,
              background: 'rgba(255,255,255,0.12)', border: 0, color: '#fff',
              borderRadius: 99, width: 36, height: 36, fontSize: 18,
              cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >×</button>
        </div>
      )}
    </div>
  );
}

export { NarrativeBlock, PlayerBlock, renderNarrativeWithInlineTools, SaveImagesStrip };
