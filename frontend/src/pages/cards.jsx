/* Cards page — split out of platform-app.jsx (task 52).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: Icon / fmtBytes。 */

const { useState: useStatePL, useEffect: useEffectPL, useMemo: useMemoPL, useCallback: useCallbackPL } = React;

const USER_CARDS = [
  { id: "uc1", name: "顾承砚", role: "漂流的史官", tone: "—", origin: "雾港未尽 · 默认主角",
    bio: "南陵旧学世家出身，因雾港事件获得在三个王朝间穿越的能力。能记录但难以改变。",
    tags: ["史官", "记录者", "穿越"], pinned: true, uses: 14, updated: "12 分钟前" },
  { id: "uc2", name: "沈知微", role: "雾港医师", tone: "中立",  origin: "雾港未尽",
    bio: "雾港医馆的女医师，掌握『若残页足三，则可推时』的旧学。",
    tags: ["医师", "知情人", "女"], pinned: false, uses: 6, updated: "今天" },
  { id: "uc3", name: "阿衡", role: "灯塔守人之女", tone: "亲近", origin: "通用",
    bio: "年十四，性格倔强，会替父亲守灯塔。", tags: ["少女", "灯塔"], pinned: false, uses: 2, updated: "3 天前" },
  { id: "uc4", name: "无名旅人", role: "—", tone: "中立", origin: "通用",
    bio: "默认观察者视角，不参与剧情核心。", tags: ["观察者", "通用"], pinned: false, uses: 8, updated: "上周" },
];

const NPC_CARDS = [
  { id: "n1", name: "韩司直", role: "南陵巡检", tone: "戒备", save: "雾港·主线·顾承砚",
    bio: "南陵驻雾港巡检，正在追查史官残页线索。", tags: ["巡检", "敌意", "权威"], uses: 9, updated: "12 分钟前" },
  { id: "n2", name: "童守人", role: "灯塔守人", tone: "失踪", save: "雾港·主线·顾承砚",
    bio: "灯塔守人，与南陵童氏同源，昨夜失踪。", tags: ["失踪", "线索"], uses: 3, updated: "今天" },
  { id: "n3", name: "税吏甲", role: "码头税吏", tone: "敌意", save: "雾港·主线·顾承砚",
    bio: "正在码头打听史官的下落。", tags: ["敌意", "次要"], uses: 4, updated: "今天" },
  { id: "n4", name: "陈渡海", role: "船工", tone: "中立", save: "雾港·支线·沈知微视角",
    bio: "雾港老船工，知道海路的人。", tags: ["导引"], uses: 2, updated: "昨天" },
  { id: "n5", name: "尚书令", role: "南陵权臣", tone: "高位", save: "南陵旧灯录·开场",
    bio: "南陵当权派，掌握光绪十三年的卷宗。", tags: ["权臣", "高位"], uses: 1, updated: "上周" },
];

function CardsPage({ subPage = "user" }) {
  return (
    <div className="pl-stack">
      {subPage === "npc" ? <NpcCardsView /> : <UserCardsView />}
    </div>
  );
}

function CardGrid({ cards, onEdit, kind, onDeleted, onDuplicate, onPromoteToUser }) {
  // task 50：之前每张卡片的 "更多" 按钮都是 dead button。现在打开一个下拉菜单，
  // 内含 导出 PNG / 导出 SillyTavern JSON / 复制 ID / 删除（user_card 走真后端）。
  const [openMenuId, setOpenMenuId] = useStatePL(null);
  const closeMenu = () => setOpenMenuId(null);
  useEffectPL(() => {
    if (!openMenuId) return;
    const h = (e) => { if (!e.target.closest?.(".pl-card-menu")) closeMenu(); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [openMenuId]);

  const handleDelete = async (c) => {
    if (kind === "npc") {
      window.__apiToast?.("NPC 卡在剧本管理页面删除", { kind: "warn", duration: 2400 });
      return;
    }
    if (!window.confirm(`确认删除角色卡「${c.name}」？该操作无法撤销。`)) return;
    try {
      await window.api.cards.myDelete(c.id);
      window.__apiToast?.("已删除 " + c.name, { kind: "ok" });
      onDeleted && onDeleted(c);
    } catch (e) {
      window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message });
    }
  };
  const copyId = async (c) => {
    try { await navigator.clipboard.writeText(String(c.id)); window.__apiToast?.("已复制 ID", { kind: "ok", duration: 1500 }); }
    catch { window.__apiToast?.("复制失败", { kind: "danger" }); }
  };

  // NPC 卡 → user_card 一键迁移。复用 game-panels 同一套 saveAsUserCard 数据 shape。
  const promoteNpcToUserCard = async (c) => {
    const raw = c._raw || c;
    const body = {
      name: c.name || raw.name || "未命名",
      identity: c.role || raw.identity || raw.role || "—",
      appearance: raw.appearance || c.bio || "",
      personality: raw.personality || "",
      speech_style: raw.speech_style || "",
      current_status: raw.current_status || "",
      secrets: raw.secrets || "",
      sample_dialogue: Array.isArray(raw.sample_dialogue) ? raw.sample_dialogue : [],
      tags: Array.isArray(c.tags) && c.tags.length ? [...c.tags, "源自 NPC"] : ["源自 NPC"],
      metadata: {
        source: "npc_promote",
        source_script_id: c.script_id || null,
        source_npc_id: raw.id ?? c.id,
      },
      enabled: true,
    };
    try {
      const r = await window.api.cards.myUpsert(body);
      if (r && r.ok === false) throw new Error(r.error || r.detail || "迁移失败");
      window.__apiToast?.(`已迁移为用户角色卡：${body.name}`,
        { kind: "ok", duration: 2200, detail: "现可在『角色卡 / 用户角色卡』中使用" });
      if (onPromoteToUser) onPromoteToUser(r?.card || body);
    } catch (e) {
      window.__apiToast?.("迁移失败", { kind: "danger", detail: e?.message || String(e) });
    }
  };

  return (
    <div className="pl-cards-grid">
      {cards.map(c => (
        <div key={c.id} className="pl-card-card" style={{position: "relative"}}>
          <div className="pl-card-head">
            <div className="pl-card-avatar serif">{c.name.slice(0, 1)}</div>
            <div className="pl-card-id">
              <strong>{c.name}</strong>
              <span className="muted-2 mono">{c.id}</span>
            </div>
            <button className="iconbtn" data-tip="编辑" onClick={() => onEdit(c)}><Icon name="edit" size={12} /></button>
            <div style={{position: "relative"}}>
              <button className="iconbtn" data-tip="更多操作"
                onClick={(e) => { e.stopPropagation(); setOpenMenuId(openMenuId === c.id ? null : c.id); }}>
                <Icon name="more" size={12} />
              </button>
              {openMenuId === c.id && (
                <div className="pl-card-menu" onClick={(e) => e.stopPropagation()}
                  style={{position: "absolute", top: "calc(100% + 4px)", right: 0, zIndex: 20,
                    /* 修 CSS：原 var(--surface) 在 tokens.css 未定义，回退透明
                       → 下拉菜单叠在 bio 文字上看起来是乱码。改用真实存在的 panel-2。 */
                    background: "var(--panel-2)", color: "var(--text)",
                    border: "1px solid var(--line-strong)", borderRadius: 6,
                    padding: 4, minWidth: 184, boxShadow: "0 6px 20px rgba(0,0,0,0.45)"}}>
                  {kind !== "npc" && (
                    <a className="btn ghost" href={window.api.cards.exportPng(c.id)} target="_blank" rel="noopener"
                      style={{display: "flex", justifyContent: "flex-start", gap: 8, width: "100%", padding: "6px 10px"}}
                      onClick={closeMenu}>
                      <Icon name="image" size={12} /> 导出 PNG（带卡数据）
                    </a>
                  )}
                  {kind !== "npc" && (
                    <a className="btn ghost" href={window.api.cards.exportTavern(c.id)} target="_blank" rel="noopener"
                      style={{display: "flex", justifyContent: "flex-start", gap: 8, width: "100%", padding: "6px 10px"}}
                      onClick={closeMenu}>
                      <Icon name="download" size={12} /> 导出 SillyTavern JSON
                    </a>
                  )}
                  {kind === "npc" && (
                    <button className="btn ghost" style={{display: "flex", justifyContent: "flex-start", gap: 8, width: "100%", padding: "6px 10px"}}
                      onClick={() => { closeMenu(); promoteNpcToUserCard(c); }}>
                      <Icon name="cards" size={12} /> 转为用户角色卡
                    </button>
                  )}
                  <button className="btn ghost" style={{display: "flex", justifyContent: "flex-start", gap: 8, width: "100%", padding: "6px 10px"}}
                    onClick={() => { closeMenu(); copyId(c); }}>
                    <Icon name="link" size={12} /> 复制 ID
                  </button>
                  {onDuplicate && (
                    <button className="btn ghost" style={{display: "flex", justifyContent: "flex-start", gap: 8, width: "100%", padding: "6px 10px"}}
                      onClick={() => { closeMenu(); onDuplicate(c); }}>
                      <Icon name="copy" size={12} /> 复制为新卡
                    </button>
                  )}
                  <button className="btn ghost danger" style={{display: "flex", justifyContent: "flex-start", gap: 8, width: "100%", padding: "6px 10px", color: "var(--danger)"}}
                    onClick={() => { closeMenu(); handleDelete(c); }}>
                    <Icon name="trash" size={12} /> 删除
                  </button>
                </div>
              )}
            </div>
          </div>
          <div className="pl-card-meta">
            <span className="muted">{c.role}</span>
            {c.tone && c.tone !== "—" && <span className="pill">{c.tone}</span>}
            {c.pinned && <span className="pill accent"><Icon name="pin" size={9} /> 已置顶</span>}
          </div>
          <p className="pl-card-bio serif">{c.bio}</p>
          <div className="pl-card-tags">
            {c.tags.map(t => <span key={t} className="pl-cap-tag">{t}</span>)}
          </div>
          <div className="pl-card-foot">
            <span className="muted-2">{kind === "npc" ? c.save : c.origin}</span>
            <span className="muted-2 mono">{c.uses} 次使用 · {c.updated}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function UserCardsView() {
  // task 47：登录态零 mock。原 useState(USER_CARDS) 初始就显示 顾承砚/沈知微/阿衡/无名旅人
  // 这套示例卡片，reload 拿到真数据再覆盖。匿名时 reload 失败仍保留 USER_CARDS（designer offline）。
  const IS_ANON = !(window.RPG_AUTH && window.RPG_AUTH.authed);
  const [cards, setCards] = useStatePL(IS_ANON ? USER_CARDS : []);
  const [filter, setFilter] = useStatePL("all");
  const [q, setQ] = useStatePL("");
  const [edit, setEdit] = useStatePL(null);
  const [adding, setAdding] = useStatePL(false);
  const [importing, setImporting] = useStatePL(false);

  const reload = React.useCallback(async () => {
    try {
      const r = await window.api.cards.myList();
      const list = Array.isArray(r) ? r : (r?.cards || r?.items || []);
      if (list.length) {
        setCards(list.map(c => ({
          id: String(c.id),
          name: c.name,
          role: c.identity || c.role || "—",
          tone: c.tone || "—",
          origin: c.origin || "通用",
          bio: c.description || c.summary || c.bio || c.personality || c.current_status || c.appearance || "",
          tags: c.tags || [],
          pinned: !!c.pinned,
          uses: c.uses || 0,
          updated: window.__fmt?.ago(c.updated_at) || c.updated_at || "—",
          _raw: c,
        })));
      }
    } catch (_) {}
  }, []);
  useEffectPL(() => { reload(); }, [reload]);
  // 监听 NPC 迁移事件 → 自动刷新用户角色卡列表，
  // 让用户切到用户卡 tab 就能看到刚迁移过来的卡。
  useEffectPL(() => {
    const onUpd = () => reload();
    window.addEventListener("rpg-user-cards-updated", onUpd);
    return () => window.removeEventListener("rpg-user-cards-updated", onUpd);
  }, [reload]);

  // task 100: modal 现在直接发 DB 字段名 (name/identity/personality/appearance/
  // speech_style/secrets/tags),不再做中间映射,也不再传 tone/pinned 等死字段。
  const onSaveCard = async (vals) => {
    try {
      await window.api.cards.myUpsert(vals);
      window.__apiToast?.(adding ? "已新增" : "已保存", { kind: "ok" });
      setEdit(null); setAdding(false);
      reload();
    } catch (e) {
      window.__apiToast?.("保存失败", { kind: "danger", detail: e?.message });
    }
  };

  const onImport = async (payload) => {
    try {
      if (payload?.file) {
        await window.api.cards.importTavern(payload.file);
      } else if (payload?.json) {
        await window.api.cards.importJson({ json: payload.json });
      }
      window.__apiToast?.("已导入", { kind: "ok" });
      setImporting(false);
      reload();
    } catch (e) {
      window.__apiToast?.("导入失败", { kind: "danger", detail: e?.message });
    }
  };

  let filtered = cards;
  if (filter === "pinned") filtered = filtered.filter(c => c.pinned);
  if (q) filtered = filtered.filter(c => (c.name + c.role + c.bio + (c.tags || []).join(" ")).toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <section className="pl-sec" data-cap-anchor="cards.user">
        <div className="pl-sec-head">
          <h2>用户角色卡 <span className="muted-2">{cards.length} 个 · 跨剧本 / 跨存档共享</span></h2>
          <div className="pl-sec-tools">
            <div className="pl-model-search" style={{flex: "1 1 160px", maxWidth: 240}}>
              <Icon name="search" size={12} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索名称 / 身份 / 标签" />
            </div>
            <div className="seg">
              <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>全部</button>
              <button className={filter === "pinned" ? "active" : ""} onClick={() => setFilter("pinned")}>置顶</button>
            </div>
            <button className="btn ghost" onClick={() => setImporting(true)} data-tip="支持 SillyTavern PNG / JSON / 粘贴">
              <Icon name="download" size={12} /> 导入酒馆卡
            </button>
            <button className="btn primary" onClick={() => setAdding(true)} data-tip="新增角色卡">
              <Icon name="plus" size={12} /> 新增角色卡
            </button>
          </div>
        </div>
        <CardGrid cards={filtered} onEdit={setEdit} kind="user"
          onDeleted={(c) => { setCards(cs => cs.filter(x => x.id !== c.id)); reload(); }}
          onDuplicate={async (c) => {
            try {
              const src = c._raw || {};
              const body = { ...src, id: undefined, slug: undefined, name: (src.name || c.name) + " 副本" };
              await window.api.cards.myUpsert(body);
              window.__apiToast?.("已复制", { kind: "ok" });
              reload();
            } catch (e) { window.__apiToast?.("复制失败", { kind: "danger", detail: e?.message }); }
          }}
        />
      </section>
      {(edit || adding) && (
        <CardEditModal
          card={edit?._raw || edit}
          isNew={adding}
          kind="user"
          onClose={() => { setEdit(null); setAdding(false); }}
          onSave={onSaveCard}
        />
      )}
      <TavernImportModal open={importing} onClose={() => setImporting(false)} onConfirm={onImport} />
    </>
  );
}

function TavernImportModal({ open, onClose, onConfirm }) {
  const [mode, setMode] = useStatePL("file");
  const [json, setJson] = useStatePL("");
  const [files, setFiles] = useStatePL([]);
  const [dragOver, setDragOver] = useStatePL(false);
  const [parseError, setParseError] = useStatePL(null);
  const [parsed, setParsed] = useStatePL(null);

  React.useEffect(() => {
    if (!open) return;
    setMode("file"); setJson(""); setFiles([]); setParseError(null); setParsed(null);
  }, [open]);

  const handleFiles = (list) => {
    const arr = [...list].slice(0, 8);
    setFiles(arr);
    // mock: parse first file
    if (arr[0]) {
      setTimeout(() => {
        setParsed({
          name: arr[0].name.replace(/\.(png|json|webp)$/i, "").replace(/[_-]/g, " "),
          format: arr[0].name.match(/\.png$/i) ? "SillyTavern · PNG v2" : "SillyTavern · JSON",
          description: "从酒馆角色卡导入 · 设定文本约 1240 字，含 4 个对话示例和 6 个标签。",
          tags: ["导入", "酒馆"],
          first_mes: "「你是谁？」她抬起头，目光在你脸上停了一会儿。",
          example_count: 4,
        });
      }, 400);
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
      const name = obj.name || obj.char_name || obj.data?.name || "未命名";
      const desc = obj.description || obj.data?.description || "无描述";
      setParsed({
        name,
        format: obj.spec ? `${obj.spec} · ${obj.spec_version || "v1"}` : "SillyTavern · JSON",
        description: desc.length > 160 ? desc.slice(0, 160) + "…" : desc,
        tags: obj.tags || obj.data?.tags || [],
        first_mes: obj.first_mes || obj.data?.first_mes || "—",
        example_count: (obj.mes_example || obj.data?.mes_example || "").split(/<START>/).filter(Boolean).length,
      });
    } catch (e) {
      setParseError("JSON 解析失败：" + e.message);
      setParsed(null);
    }
  };

  if (!open) return null;
  const canSubmit = parsed && !parseError;
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(620px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">导入酒馆角色卡</div>
            <h2 className="pl-modal-title">支持 SillyTavern / Chub / TavernAI 格式</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form">
          <div className="seg" style={{display: "flex"}}>
            <button className={mode === "file" ? "active" : ""} onClick={() => setMode("file")}>
              <Icon name="upload" size={12} /> 上传文件
            </button>
            <button className={mode === "paste" ? "active" : ""} onClick={() => setMode("paste")}>
              <Icon name="file" size={12} /> 粘贴 JSON
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
                  {dragOver ? "松手以导入" : "把角色卡拖到这里"}
                </strong>
                <span>支持 .png（嵌入元数据）/ .json / .webp · 单次最多 8 个</span>
                <input id="tavern-file-input" type="file" accept=".png,.json,.webp" multiple
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
                <label>粘贴角色卡 JSON</label>
                <textarea rows={10} value={json} onChange={(e) => setJson(e.target.value)}
                  className="mono" style={{fontSize: 11.5}}
                  placeholder='{\n  "name": "沈知微",\n  "description": "...",\n  "first_mes": "...",\n  "tags": ["医师"]\n}' />
              </div>
              <button className="btn ghost" onClick={tryParseJson} disabled={!json.trim()} style={{width: "fit-content"}}>
                <Icon name="check" size={12} /> 解析并预览
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
              <div className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em"}}>解析预览 · {parsed.format}</div>
              <div className="pl-card-head" style={{margin: 0}}>
                <div className="pl-card-avatar serif">{parsed.name.slice(0, 1)}</div>
                <div className="pl-card-id" style={{flex: 1}}>
                  <strong>{parsed.name}</strong>
                  <span className="muted-2" style={{fontSize: 11.5}}>{parsed.example_count} 段对话示例 · {parsed.tags.length} 个标签</span>
                </div>
              </div>
              <p className="pl-card-bio serif" style={{margin: 0, WebkitLineClamp: 2}}>{parsed.description}</p>
              <div style={{padding: 8, background: "var(--bg-deep)", borderRadius: 4, fontFamily: "var(--font-serif)", fontSize: 12.5, color: "var(--text-quiet)", borderLeft: "2px solid var(--accent-edge)"}}>
                <span className="muted-2 mono" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em", display: "block", marginBottom: 4}}>开场白</span>
                {parsed.first_mes}
              </div>
              {parsed.tags?.length > 0 && (
                <div className="pl-card-tags">
                  {parsed.tags.map(t => <span key={t} className="pl-cap-tag">{t}</span>)}
                </div>
              )}
            </div>
          )}
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> POST /api/v1/characters/import · 导入后可在用户角色卡库编辑
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            <button className="btn primary" onClick={onConfirm} disabled={!canSubmit}>
              <Icon name="check" size={12} /> 导入 {files.length > 1 ? `${files.length} 个` : ""}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
  return ReactDOM.createPortal(node, document.body);
}

function NpcCardsView() {
  // task 47：之前完全用硬编码 NPC_CARDS（韩司直/童守人/税吏甲/陈渡海/尚书令），
  // 跟登录用户的真实剧本毫无关系。改成跨所有用户剧本聚合
  // /api/scripts/{id}/character-cards，按真实存档分组。
  // 用户的真实"NPC 角色卡"= 后端每个 script 下的 character_cards 表。
  const [cards, setCards] = useStatePL([]);
  const [loading, setLoading] = useStatePL(true);
  const [error, setError] = useStatePL("");
  const [saveFilter, setSaveFilter] = useStatePL("all");
  const [q, setQ] = useStatePL("");
  const [edit, setEdit] = useStatePL(null);
  const [adding, setAdding] = useStatePL(false);

  const reload = React.useCallback(async () => {
    setLoading(true); setError("");
    try {
      // 1) 拉所有 scripts；2) 对每个 script 并行拉 character-cards
      const sr = await window.api.scripts.list();
      const scripts = Array.isArray(sr) ? sr : (sr?.items || sr?.scripts || []);
      if (!scripts.length) { setCards([]); setLoading(false); return; }
      const lists = await Promise.all(scripts.map(async (s) => {
        try {
          const r = await window.api.cards.scriptList(s.id);
          const arr = Array.isArray(r) ? r : (r?.items || r?.cards || []);
          return arr.map(c => ({
            id: String(c.id),
            name: c.name || "未命名",
            role: c.identity || c.role || "—",
            tone: c.tone || "中立",
            save: s.title || `剧本 #${s.id}`,
            script_id: s.id,
            bio: c.appearance || c.personality || c.summary || c.description || "",
            tags: Array.isArray(c.tags) ? c.tags : [],
            uses: c.uses || 0,
            updated: window.__fmt?.ago(c.updated_at) || c.updated_at || "—",
            pinned: !!c.pinned,
            _raw: c,
          }));
        } catch (_) { return []; }
      }));
      setCards(lists.flat());
    } catch (e) {
      setError(e?.message || "加载 NPC 角色卡失败");
      // 匿名 / API 不可达 → 兜底到 mock（designer offline preview）
      if (!(window.RPG_AUTH && window.RPG_AUTH.authed)) {
        setCards((NPC_CARDS || []).map(c => ({ ...c, script_id: null })));
      } else {
        setCards([]);
      }
    } finally { setLoading(false); }
  }, []);
  React.useEffect(() => { reload(); }, [reload]);

  const allSaves = ["all", ...new Set(cards.map(c => c.save))];
  let filtered = cards;
  if (saveFilter !== "all") filtered = filtered.filter(c => c.save === saveFilter);
  if (q) filtered = filtered.filter(c =>
    (String(c.name) + String(c.role) + String(c.bio) + (c.tags || []).join(" "))
      .toLowerCase().includes(q.toLowerCase())
  );

  return (
    <>
      <section className="pl-sec" data-cap-anchor="cards.npc">
        <div className="pl-sec-head">
          <h2>NPC 角色卡 <span className="muted-2">{cards.length} 个 · 按存档分组{loading ? " · 加载中…" : ""}</span></h2>
          <div className="pl-sec-tools">
            <div className="pl-model-search" style={{flex: "1 1 160px", maxWidth: 240}}>
              <Icon name="search" size={12} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索 NPC" />
            </div>
            <select value={saveFilter} onChange={(e) => setSaveFilter(e.target.value)} style={{height: 28, fontSize: 12, padding: "0 10px", width: "auto"}} disabled={loading}>
              {allSaves.map(s => <option key={s} value={s}>{s === "all" ? "所有剧本" : s}</option>)}
            </select>
            <button className="btn primary" onClick={() => setAdding(true)} data-tip="新增 NPC 角色卡">
              <Icon name="plus" size={12} /> 新增 NPC
            </button>
          </div>
        </div>
        {error && (
          <div className="pl-auth-error" style={{margin: "12px 0", padding: "10px 14px", border: "1px solid var(--danger,#c0392b)", borderRadius: 6, fontSize: 13}}>
            {error}
          </div>
        )}
        {!loading && cards.length === 0 && !error && (
          <div className="muted-2" style={{padding: "40px 12px", textAlign: "center", fontSize: 13, lineHeight: 1.7}}>
            你的剧本里还没有任何 NPC 角色卡。<br />
            <span className="muted">点击右上『新增 NPC』开始创建，或先去『剧本 / 导入』上传一部含角色设定的剧本。</span>
          </div>
        )}
        {!loading && cards.length > 0 && (
          <CardGrid cards={filtered} onEdit={setEdit} kind="npc"
            onPromoteToUser={() => {
              // 迁移到 user_card 后通知用户角色卡列表刷新（如果当前 mounted）
              try { window.dispatchEvent(new CustomEvent("rpg-user-cards-updated")); } catch (_) {}
            }} />
        )}
      </section>
      {(edit || adding) && (
        <CardEditModal
          card={edit?._raw || edit}
          isNew={adding}
          kind="npc"
          onClose={() => { setEdit(null); setAdding(false); }}
          onSave={() => { setEdit(null); setAdding(false); reload(); }}
        />
      )}
    </>
  );
}

function CardEditModal({ card, isNew, kind, onClose, onSave }) {
  // task 100: 字段全部直接对齐 DB 列 (之前的 role/bio/tone/speech/pinned 命名错位 +
  // appearance/speech/secrets 硬编码 "" 会清空编辑现有卡片的数据)。
  const [form, setForm] = useStatePL({
    name: card?.name || "",
    identity: card?.identity || "",
    personality: card?.personality || "",
    appearance: card?.appearance || "",
    speech_style: card?.speech_style || "",
    secrets: card?.secrets || "",
    tags: Array.isArray(card?.tags) ? card.tags.join(", ") : "",
  });
  const [showMore, setShowMore] = useStatePL(
    !!(card?.appearance || card?.speech_style || card?.secrets ||
       (Array.isArray(card?.tags) && card.tags.length))
  );
  const u = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const node = (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(560px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">{isNew ? "新增" : "编辑"} {kind === "user" ? "用户角色卡" : "NPC 角色卡"}</div>
            <h2 className="pl-modal-title">{form.name || "新角色"}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        <div className="pl-modal-form">
          {/* 核心 3 字段 — 任何一张卡都至少有 */}
          <div className="pl-field">
            <label>姓名 <span className="pl-field-req">*</span></label>
            <input value={form.name} onChange={(e) => u("name", e.target.value)} autoFocus />
          </div>
          <div className="pl-field">
            <label>身份</label>
            <input value={form.identity} onChange={(e) => u("identity", e.target.value)} />
          </div>
          <div className="pl-field">
            <label>设定</label>
            <textarea rows={3} value={form.personality} onChange={(e) => u("personality", e.target.value)} />
          </div>

          {/* 折叠区: 大多数卡不需要 */}
          <button
            type="button"
            className="btn ghost"
            style={{justifyContent: "flex-start", padding: "6px 10px", fontSize: 12.5}}
            onClick={() => setShowMore(v => !v)}
          >
            <Icon name={showMore ? "chevron-down" : "chevron-right"} size={12} />
            <span style={{marginLeft: 6}}>{showMore ? "收起更多字段" : "更多字段（可选）"}</span>
          </button>
          {showMore && (
            <>
              <div className="pl-field">
                <label>外貌</label>
                <textarea rows={2} value={form.appearance} onChange={(e) => u("appearance", e.target.value)} />
              </div>
              <div className="pl-field">
                <label>语气</label>
                <textarea rows={2} value={form.speech_style} onChange={(e) => u("speech_style", e.target.value)} />
              </div>
              <div className="pl-field">
                <label>关键秘密</label>
                <textarea rows={2} value={form.secrets} onChange={(e) => u("secrets", e.target.value)} />
              </div>
              {kind === "user" && (
                <div className="pl-field">
                  <label>标签 <span className="muted-2" style={{textTransform: "none", letterSpacing: 0, marginLeft: 6}}>逗号分隔</span></label>
                  <input value={form.tags} onChange={(e) => u("tags", e.target.value)} />
                </div>
              )}
            </>
          )}
        </div>
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> POST /api/me/character-cards
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            <button className="btn primary"
              onClick={() => onSave?.({
                ...(card?.id ? {id: card.id} : {}),
                name: form.name.trim(),
                identity: form.identity.trim(),
                personality: form.personality.trim(),
                appearance: form.appearance.trim(),
                speech_style: form.speech_style.trim(),
                secrets: form.secrets.trim(),
                tags: (form.tags || "").split(",").map(s => s.trim()).filter(Boolean),
              })}
              disabled={!form.name.trim()}>
              <Icon name="check" size={12} /> {isNew ? "创建" : "保存"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
  return ReactDOM.createPortal(node, document.body);
}

Object.assign(window, {
  CardsPage, CardGrid, UserCardsView, NpcCardsView, CardEditModal, TavernImportModal,
});
