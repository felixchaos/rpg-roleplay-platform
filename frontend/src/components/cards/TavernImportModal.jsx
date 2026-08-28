/* 酒馆角色卡 / 聊天记录导入弹窗(文件 / 粘贴 JSON;AI 整理字段 opt-in)。

   三个入口共用:用户角色卡库 / 剧本 NPC 角色卡 / 酒馆(导入即开新对话)。
   props:
     allowChat  —— 是否显示「聊天记录」页签(剧本 NPC 卡侧无意义,关掉)
     multiple   —— 是否允许一次选多张卡。只有会循环 payload.files 的调用方才该开
                   (酒馆侧导入一张=开一个新对话,多张没有"开哪个"的语义,故关)
     footerHint —— 底部说明(默认是用户卡库的文案) */

import React from 'react';
import { createPortal } from 'react-dom';
import { useState as useStatePL } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../../game-icons.jsx';
import Modal from '../Modal.jsx';
import AvatarImg from '../AvatarImg.jsx';
import AgentModelPicker from '../AgentModelPicker.jsx';
import { fmtBytes } from '../../platform-app.jsx';

function TavernImportModal({ open, onClose, onConfirm, allowChat = true, multiple = true, footerHint = null }) {
  const { t } = useTranslation();
  // importType: "card" | "chat"
  const [importType, setImportType] = useStatePL("card");
  const [mode, setMode] = useStatePL("file");
  const [json, setJson] = useStatePL("");
  const [files, setFiles] = useStatePL([]);
  const [dragOver, setDragOver] = useStatePL(false);
  const [parseError, setParseError] = useStatePL(null);
  const [parsed, setParsed] = useStatePL(null);
  const [aiSplit, setAiSplit] = useStatePL(false);  // 用 AI 整理字段(消耗额度)
  // 整理用模型不在此重复选择 — 统一在「设置 → 模型 → AI 整理卡字段」配置。
  // chat-specific
  const [chatText, setChatText] = useStatePL("");
  const [chatFile, setChatFile] = useStatePL(null);
  const [chatParsed, setChatParsed] = useStatePL(null);
  const [chatError, setChatError] = useStatePL(null);

  React.useEffect(() => {
    if (!open) return;
    setImportType("card"); setMode("file"); setJson(""); setFiles([]);
    setParseError(null); setParsed(null); setAiSplit(false);
    setChatText(""); setChatFile(null); setChatParsed(null); setChatError(null);
  }, [open]);

  const handleFiles = (list) => {
    // task 68: size + ext 校验,防内存炸 / 类型混淆
    const MAX_BYTES = 10 * 1024 * 1024;  // 10MB / 文件(对齐后端 PNG 导入上限;群反馈 #92:>5MB 角色卡传不上)
    const MAX_FILES = multiple ? 8 : 1;
    const arr = [...list].slice(0, MAX_FILES);
    if (list.length > MAX_FILES) {
      window.__apiToast?.(t('cards.page.import.too_many_files', { max: MAX_FILES }), { kind: 'warn', duration: 2400 });
    }
    const valid = arr.filter(f => {
      if (!f) return false;
      if (!/\.(png|json|webp)$/i.test(f.name || '')) {
        window.__apiToast?.(t('cards.page.import.invalid_type', { name: f.name }), { kind: 'danger', duration: 2400 });
        return false;
      }
      if (f.size > MAX_BYTES) {
        window.__apiToast?.(t('cards.page.import.file_too_large', { name: f.name, mb: MAX_BYTES / 1024 / 1024 }), { kind: 'danger', duration: 2400 });
        return false;
      }
      return true;
    });
    setFiles(valid);
    if (valid[0]) {
      const f = valid[0];
      const sizeKb = (f.size / 1024).toFixed(1);
      const fmt = f.name.match(/\.png$/i) ? "SillyTavern · PNG v2" : f.name.match(/\.json$/i) ? "SillyTavern · JSON" : f.type || "unknown";
      setParsed({
        name: f.name.replace(/\.(png|json|webp)$/i, "").replace(/[_-]/g, " "),
        format: fmt,
        description: t('cards.import.parse_pending_hint', { size: sizeKb, mime: f.type || "—" }),
        tags: [t('cards.import.tag_imported')],
        first_mes: t('cards.import.parse_pending_first_mes'),
        example_count: 0,
        _file: f,
      });
    }
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    if (e.dataTransfer?.files?.length) handleFiles(e.dataTransfer.files);
  };

  const tryParseJson = () => {
    setParseError(null);
    try {
      const obj = JSON.parse(json);
      // 解包常见的外层包装（如 {"ok":true,"card":{...}}）
      const inner = obj.card?.data ? obj.card : obj.character?.data ? obj.character : obj;
      const d = inner.data || {};
      const name = inner.name || inner.char_name || d.name || t('cards.detail.unnamed');
      const desc = inner.description || d.description || t('cards.import.no_desc');
      const spec = inner.spec || obj.spec;
      const specVersion = inner.spec_version || obj.spec_version;
      setParsed({
        name,
        format: spec ? `${spec} · ${specVersion || "v1"}` : "SillyTavern · JSON",
        description: desc.length > 160 ? desc.slice(0, 160) + "…" : desc,
        tags: inner.tags || d.tags || [],
        first_mes: inner.first_mes || d.first_mes || "—",
        example_count: (inner.mes_example || d.mes_example || "").split(/<START>/).filter(Boolean).length,
        _jsonString: json,
      });
    } catch (e) {
      setParseError(t('cards.import.parse_fail', { msg: e.message }));
      setParsed(null);
    }
  };

  // chat tab: read .jsonl file
  const handleChatFile = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    // task 68: size + ext 校验
    if (!/\.(jsonl?)$/i.test(f.name || '')) {
      setChatError(t('cards.page.import.chat_invalid_ext'));
      return;
    }
    if (f.size > 20 * 1024 * 1024) {  // 20MB / chat (聊天记录可能较长)
      setChatError(t('cards.page.import.chat_too_large'));
      return;
    }
    setChatFile(f); setChatError(null); setChatParsed(null);
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target.result;
      // quick local preview: count lines, extract header
      try {
        const lines = text.split('\n').filter(l => l.trim());
        const header = JSON.parse(lines[0] || '{}');
        const msgCount = lines.slice(1).filter(l => l.trim()).length;
        setChatParsed({
          charName: header.character_name || header.char_name || f.name.replace(/\.jsonl?$/i, ""),
          userName: header.user_name || "User",
          msgCount,
          sizeKb: (f.size / 1024).toFixed(1),
          _text: text,
        });
      } catch {
        setChatError(t('cards.import.chat_parse_fail'));
      }
    };
    reader.readAsText(f);
  };

  const doConfirmCard = () => {
    if (!parsed) return;
    // 整理用模型统一走「设置 → 模型 → AI 整理卡字段」配置,这里不再透传 per-import 模型。
    if (parsed._file) {
      // files: 用户选的**全部**文件。此前只传 parsed._file(预览的第一张),
      // 其余在弹窗里列出来的文件被静默丢弃 —— 选 5 张只进 1 张且无提示。
      onConfirm({ type: "card", file: parsed._file, files, aiSplit });
    } else if (parsed._jsonString) {
      onConfirm({ type: "card_json", json_string: parsed._jsonString, aiSplit });
    }
  };

  const doConfirmChat = () => {
    if (!chatParsed?._text) return;
    onConfirm({ type: "chat", jsonl: chatParsed._text, charName: chatParsed.charName });
  };

  if (!open) return null;
  const canSubmitCard = parsed && !parseError;
  const canSubmitChat = chatParsed && !chatError;

  const node = (
    <Modal
      open
      eyebrow={t('cards.import.modal_eyebrow')}
      title={t('cards.import.modal_title')}
      width={640}
      onClose={onClose}
      footer={<>
        <span className="muted-2" style={{fontSize: 11.5}}>
          <Icon name="info" size={11} /> {importType === "chat" ? t('cards.import.chat_footer_hint') : (footerHint || t('cards.import.footer_hint'))}
        </span>
        <div style={{display: "flex", gap: 8}}>
          <button className="btn ghost" onClick={onClose}>{t('cards.import.btn_cancel')}</button>
          {importType === "card" ? (
            <button className="btn primary" onClick={doConfirmCard} disabled={!canSubmitCard}>
              <Icon name="check" size={12} /> {files.length > 1
                ? t('cards.import.btn_confirm_n', { count: files.length })
                : t('cards.import.btn_confirm')}
            </button>
          ) : (
            <button className="btn primary" onClick={doConfirmChat} disabled={!canSubmitChat}>
              <Icon name="check" size={12} /> {t('cards.import.chat_btn_confirm')}
            </button>
          )}
        </div>
      </>}
    >
        <div className="pl-modal-form">
          {/* top-level type switcher(剧本 NPC 卡侧没有"导入聊天记录"这回事,整条隐藏) */}
          {allowChat && <div className="seg" style={{display: "flex"}}>
            <button className={importType === "card" ? "active" : ""} onClick={() => setImportType("card")}>
              <Icon name="user" size={12} /> {t('cards.import.type_card')}
            </button>
            <button className={importType === "chat" ? "active" : ""} onClick={() => setImportType("chat")}>
              <Icon name="chat" size={12} /> {t('cards.import.type_chat')}
            </button>
          </div>}

          {/* ── Card import ─────────────────────────────────────────── */}
          {importType === "card" && (
            <>
              <div className="seg" style={{display: "flex"}}>
                <button className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}>
                  <Icon name="upload" size={12} /> {t('cards.import.tab_file')}
                </button>
                <button className={mode === "paste" ? "active" : ""} onClick={() => setMode("paste")}>
                  <Icon name="file" size={12} /> {t('cards.import.tab_paste')}
                </button>
              </div>
              {mode === "file" && (
                <>
                  <div
                    className={`pl-drop ${dragOver ? "drop-active" : ""}`}
                    onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                    onDragLeave={() => setDragOver(false)}
                    onDrop={onDrop}
                    style={{padding: "32px 16px", cursor: "pointer"}}
                    onClick={() => document.getElementById("tavern-file-input")?.click()}
                  >
                    <Icon name="upload" size={24} style={{color: dragOver ? "var(--accent)" : "var(--muted)"}} />
                    <strong style={{color: dragOver ? "var(--accent)" : "var(--text)"}}>
                      {dragOver ? t('cards.import.drop_release') : t('cards.import.drop_hint')}
                    </strong>
                    <span>{multiple ? t('cards.import.drop_formats') : t('cards.import.drop_formats_single')}</span>
                    <input id="tavern-file-input" type="file" accept=".png,.json,.webp" multiple={multiple}
                      style={{display: "none"}} onChange={(e) => handleFiles(e.target.files)} />
                  </div>
                  {files.length > 0 && (
                    <div style={{display: "grid", gap: 4}}>
                      {files.map((f, i) => (
                        <div key={i} style={{
                          display: "flex", alignItems: "center", gap: 8,
                          padding: "6px 10px", borderRadius: 4,
                          background: "var(--bg-deep)", fontSize: 12,
                        }}>
                          <Icon name={f.name.endsWith(".png") || f.name.endsWith(".webp") ? "image" : "file"} size={12} style={{color: "var(--accent)"}} />
                          <span className="mono" style={{flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{f.name}</span>
                          <span className="muted-2 mono" style={{fontSize: 11}}>{fmtBytes(f.size)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
              {mode === "paste" && (
                <>
                  <div className="pl-field">
                    <label>{t('cards.import.paste_label')}</label>
                    <textarea rows={10} value={json} onChange={(e) => setJson(e.target.value)}
                      className="mono" style={{fontSize: 11.5}}
                      placeholder={'{\n  "name": "...",\n  "description": "...",\n  "first_mes": "...",\n  "tags": []\n}'} />
                  </div>
                  <button className="btn ghost" onClick={tryParseJson} disabled={!json.trim()} style={{width: "fit-content"}}>
                    <Icon name="check" size={12} /> {t('cards.import.btn_parse')}
                  </button>
                  {parseError && (
                    <div className="pl-validate-step" style={{color: "var(--danger)", borderColor: "rgba(200, 103, 93, 0.32)", background: "var(--danger-soft)"}}>
                      <Icon name="warn" size={12} /> {parseError}
                    </div>
                  )}
                </>
              )}
              {parsed && (
                <div className="pl-import" style={{borderStyle: "solid", gap: 8, padding: "12px 14px"}}>
                  <div className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em"}}>{t('cards.import.preview_label')} · {parsed.format}</div>
                  <div className="pl-card-head" style={{margin: 0}}>
                    <AvatarImg src={parsed.avatar_url || parsed.avatar_path || null} name={parsed.name} size={64} shape="rounded" className="pl-card-avatar serif" />
                    <div className="pl-card-id" style={{flex: 1}}>
                      <strong>{parsed.name}</strong>
                      <span className="muted-2" style={{fontSize: 11.5}}>{t('cards.import.preview_stats', { dialogues: parsed.example_count, tags: parsed.tags?.length || 0 })}</span>
                    </div>
                  </div>
                  <p className="pl-card-bio serif" style={{margin: 0, WebkitLineClamp: 2}}>{parsed.description}</p>
                  <div style={{padding: 8, background: "var(--bg-deep)", borderRadius: 4, fontFamily: "var(--font-serif)", fontSize: 12.5, color: "var(--text-quiet)", borderLeft: "2px solid var(--accent-edge)"}}>
                    <span className="muted-2 mono" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em", display: "block", marginBottom: 4}}>{t('cards.import.first_mes_label')}</span>
                    {parsed.first_mes}
                  </div>
                  {parsed.tags?.length > 0 && (
                    <div className="pl-card-tags">
                      {parsed.tags.map(tg => <span key={tg} className="pl-cap-tag">{tg}</span>)}
                    </div>
                  )}
                </div>
              )}
              {/* AI 整理字段 opt-in:确定性规则拆不开的自由文本卡才需要,默认关闭、消耗额度 */}
              <label style={{
                display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer",
                padding: "10px 12px", borderRadius: 6,
                border: `1px solid ${aiSplit ? "var(--accent-edge, rgba(201,100,66,.5))" : "var(--line-soft, #2a2724)"}`,
                background: aiSplit ? "var(--accent-soft, rgba(201,100,66,.1))" : "var(--bg-deep)",
              }}>
                <input type="checkbox" checked={aiSplit} onChange={(e) => setAiSplit(e.target.checked)}
                  style={{ marginTop: 2, accentColor: "var(--accent, #c96442)" }} />
                <span style={{ fontSize: 12.5, lineHeight: 1.5 }}>
                  <strong style={{ color: "var(--text)" }}>{t('cards.import.ai_split_label')}</strong>
                  <span className="muted-2" style={{ display: "block", fontSize: 11.5 }}>{t('cards.import.ai_split_hint')}</span>
                </span>
              </label>
              {aiSplit && (
                <AgentModelPicker
                  prefPrefix="card_import"
                  variant="bare"
                  defaultModel={null}
                  configHash="settings-models"
                  persistOnMount
                />
              )}
            </>
          )}

          {/* ── Chat import ─────────────────────────────────────────── */}
          {importType === "chat" && (
            <>
              <div className="pl-field" style={{display: "flex", flexDirection: "column", gap: 8}}>
                <label style={{fontSize: 12.5}}>{t('cards.import.chat_hint')}</label>
                <label className="btn ghost" style={{width: "fit-content", cursor: "pointer"}}>
                  <Icon name="upload" size={12} /> {t('cards.import.chat_btn_file')}
                  <input type="file" accept=".jsonl,.json" style={{display: "none"}} onChange={handleChatFile} />
                </label>
                {chatFile && (
                  <div style={{display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", borderRadius: 4, background: "var(--bg-deep)", fontSize: 12}}>
                    <Icon name="file" size={12} style={{color: "var(--accent)"}} />
                    <span className="mono" style={{flex: 1}}>{chatFile.name}</span>
                    <span className="muted-2 mono" style={{fontSize: 11}}>{fmtBytes(chatFile.size)}</span>
                  </div>
                )}
                {chatError && (
                  <div className="pl-validate-step" style={{color: "var(--danger)", borderColor: "rgba(200, 103, 93, 0.32)", background: "var(--danger-soft)"}}>
                    <Icon name="warn" size={12} /> {chatError}
                  </div>
                )}
              </div>
              {chatParsed && (
                <div className="pl-import" style={{borderStyle: "solid", gap: 8, padding: "12px 14px"}}>
                  <div className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em"}}>{t('cards.import.chat_preview_label')}</div>
                  <div className="pl-card-head" style={{margin: 0}}>
                    <AvatarImg src={chatParsed?.avatar_url || null} name={chatParsed.charName} size={64} shape="rounded" className="pl-card-avatar serif" />
                    <div className="pl-card-id" style={{flex: 1}}>
                      <strong>{chatParsed.charName}</strong>
                      <span className="muted-2" style={{fontSize: 11.5}}>{t('cards.import.chat_preview_stats', { msgs: chatParsed.msgCount, user: chatParsed.userName })}</span>
                    </div>
                  </div>
                  <div style={{fontSize: 12, color: "var(--text-quiet)", padding: "6px 10px", background: "var(--bg-deep)", borderRadius: 4}}>
                    <Icon name="info" size={11} /> {t('cards.import.chat_new_save_hint')}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
    </Modal>
  );
  return createPortal(node, document.body);
}

export { TavernImportModal };
