/* 通用 inline editor(PanelWorldbook / CharacterCard 共用)—— 纯机械从 game-panels.jsx 搬出,零行为变化。 */
import React from 'react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';

// ── 通用 inline editor:click-to-edit 文本字段 ────────────────────
// 用于 PanelWorldbook 的 time/weather/location、PanelCharacters 的关系状态、
// PanelStatus 的玩家卡字段(multiline=true:外貌/性格/语气/背景,长文本用 textarea)。
function InlineEditField({ value, placeholder, emptyLabel, onSubmit, busy, multiline, className }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value || "");
  React.useEffect(() => { if (!editing) setDraft(value || ""); }, [value, editing]);
  const submittingRef = React.useRef(false);
  const commit = async () => {
    if (submittingRef.current) return;
    const v = (draft || "").trim();
    if (!v || v === (value || "")) { setEditing(false); return; }
    submittingRef.current = true;
    try { await onSubmit(v); setEditing(false); }
    catch (e) { window.__apiToast?.(t('game.inline_edit.save_failed'), { kind: "danger", detail: e?.message }); }
    finally { setTimeout(() => { submittingRef.current = false; }, 100); }
  };
  if (!editing) {
    return (
      <span style={{cursor: "pointer", display: "inline-flex", gap: 4, alignItems: multiline ? "flex-start" : "center"}}
            onClick={() => setEditing(true)}
            title={t('game.inline_edit.click_to_edit')}>
        <span style={multiline ? { whiteSpace: "pre-wrap" } : undefined}>{value || (emptyLabel || "—")}</span>
        <Icon name="edit" size={10} style={{opacity: 0.4, flexShrink: 0, marginTop: multiline ? 2 : 0}} />
      </span>
    );
  }
  if (multiline) {
    return (
      <textarea className={className || "gp-inline-input"} autoFocus disabled={busy}
        rows={3}
        value={draft}
        placeholder={placeholder || ""}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Escape") { setDraft(value || ""); setEditing(false); }
          else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); commit(); }
        }}
        style={{
          background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.2)",
          borderRadius: 4, padding: "4px 6px", color: "inherit", font: "inherit",
          width: "100%", resize: "vertical", boxSizing: "border-box",
        }}
      />
    );
  }
  return (
    <input className={className || "gp-inline-input"} autoFocus disabled={busy}
      value={draft}
      placeholder={placeholder || ""}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        else if (e.key === "Escape") { setDraft(value || ""); setEditing(false); }
      }}
      style={{
        background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.2)",
        borderRadius: 4, padding: "2px 6px", color: "inherit", font: "inherit",
        minWidth: 80, maxWidth: 260,
      }}
    />
  );
}

export { InlineEditField };
