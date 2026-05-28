/* Scripts page — split out of platform-app.jsx (task 52: 拆 platform-app.jsx 按页面).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: PromptModal / Icon / usePlatformData / fmtBytes / fmtN
   以及 saves.jsx 注入的 NewGameModal（顺序保证：platform-app.jsx → saves.jsx → scripts.jsx 在 Platform.html 中按序加载）。 */

const { useState: useStatePL, useEffect: useEffectPL, useMemo: useMemoPL, useCallback: useCallbackPL } = React;

function ScriptPreviewModal({ open, busy, data, rule, onClose, onRetryRule, onConfirm }) {
  if (!open) return null;
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(720px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">章节切分预览 · {rule || "自动识别"}</div>
            <h2 className="pl-modal-title">{busy ? "正在切分…" : (data?.title || "未命名")}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        {busy ? (
          <div className="pl-validate-progress">
            <div className="pl-validate-step done"><span className="dot ok" /> 1 / 3 · 读取文件并标准化换行</div>
            <div className="pl-validate-step done"><span className="dot ok" /> 2 / 3 · 嗅探章节标题模式</div>
            <div className="pl-validate-step running"><Icon name="spinner" size={12} className="spin" /> 3 / 3 · 切分章节并统计字数…</div>
          </div>
        ) : data ? (
          <>
            <div className="pl-validate-result" style={{flex: "0 0 auto"}}>
              <div className="pl-validate-stat-row">
                <div className="pl-validate-stat">
                  <span className="pl-stat-label">章节</span>
                  <span className="pl-stat-value" style={{fontSize: 20}}>{data.chapter_count}</span>
                </div>
                <div className="pl-validate-stat">
                  <span className="pl-stat-label">字数</span>
                  <span className="pl-stat-value" style={{fontSize: 20}}>{(data.word_count / 10000).toFixed(1)}<span style={{fontSize: 12, color: "var(--muted)", marginLeft: 3}}>万</span></span>
                </div>
                <div className="pl-validate-stat">
                  <span className="pl-stat-label">置信度</span>
                  <span className="pl-stat-value" style={{fontSize: 20, color: data.confidence >= 0.85 ? "var(--ok)" : "var(--warn)"}}>{Math.round(data.confidence * 100)}<span style={{fontSize: 12, marginLeft: 2}}>%</span></span>
                </div>
                <div className="pl-validate-stat">
                  <span className="pl-stat-label">异常</span>
                  <span className="pl-stat-value" style={{fontSize: 13, lineHeight: 1.5, fontFamily: "var(--font-sans)", color: data.problem_kind === "ok" ? "var(--ok)" : "var(--warn)"}}>{data.problem_label}</span>
                </div>
              </div>
              {data.notes?.length > 0 && (
                <ul className="pl-flat-list" style={{listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 4}}>
                  {data.notes.map((n, i) => (
                    <li key={i} className="muted-2" style={{fontSize: 11.5, paddingLeft: 14, position: "relative"}}>
                      <span style={{position: "absolute", left: 0}}>•</span> {n}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div style={{overflowY: "auto", overflowX: "hidden", minHeight: 0, flex: "1 1 auto", border: "1px solid var(--line-soft)", borderRadius: "var(--r-2)"}}>
              <table className="pl-table" style={{margin: 0}}>
                <thead><tr><th style={{width: 50}}>#</th><th>章节标题</th><th>卷</th><th style={{textAlign: "right"}}>字数</th></tr></thead>
                <tbody>
                  {data.preview.map(p => (
                    <tr key={p.idx} style={{background: p.ok ? "transparent" : "var(--warn-soft)"}}>
                      <td className="mono muted-2">{String(p.idx).padStart(3, "0")}</td>
                      <td>
                        <strong style={{fontFamily: "var(--font-serif)", fontSize: 14}}>{p.title}</strong>
                        {!p.ok && <span className="pill warn" style={{marginLeft: 8, fontSize: 10.5}}><span className="dot warn" /> {p.hint}</span>}
                      </td>
                      <td className="muted">{p.volume}</td>
                      <td className="mono" style={{textAlign: "right"}}>{p.words.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> 仅展示前 {data?.preview?.length || 0} 章 · 完整切分见导入后剧本目录 · POST /api/v1/scripts/import
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>取消</button>
            {!busy && (
              <>
                <button className="btn ghost" onClick={() => onRetryRule?.("chapter_cn")} data-tip="尝试不同的切分规则">
                  <Icon name="refresh" size={12} /> 换规则重试
                </button>
                <button className="btn primary" onClick={onConfirm} disabled={!data}>
                  <Icon name="check" size={12} /> 确认导入
                </button>
              </>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}

function ConfidenceBar({ value }) {
  const pct = Math.round(value * 100);
  const color = value >= 0.85 ? "var(--ok)" : value >= 0.7 ? "var(--warn)" : "var(--danger)";
  return (
    <div style={{display: "flex", alignItems: "center", gap: 8}}>
      <div style={{width: 60, height: 4, borderRadius: 999, background: "var(--line-soft)", overflow: "hidden"}}>
        <div style={{width: pct + "%", height: "100%", background: color}} />
      </div>
      <span className="mono" style={{fontSize: 11, color: "var(--muted)"}}>{pct}%</span>
    </div>
  );
}

/* ---------------------------- SCRIPTS -------------------------- */
const SPLIT_RULES = [
  { id: "auto",       label: "自动识别" },
  { id: "corpus",     label: "语料章节" },
  { id: "chapter_cn", label: "中文章节" },
  { id: "chapter_en", label: "英文章节" },
  { id: "number_dot", label: "数字点号" },
  { id: "paren_num",  label: "括号编号" },
  { id: "custom",     label: "自定义" },
];

function ScriptsPage({ subPage = "list" }) {
  return (
    <div className="pl-stack">
      {subPage === "import" ? <ScriptsImportView /> : <ScriptsListView />}
    </div>
  );
}

function ScriptsListView() {
  // task 19: 永远以 /api/scripts 真实回包为准；空列表也覆盖 mock，不再混 MOCK_PLATFORM.scripts。
  // task 51：之前 onClick 里用了 `platform?.saves` 但 ScriptsListView 没拿过 platform，
  // 永远是 ReferenceError → 整个按钮 throw 后被 React 静默吞掉 → 用户点了无反应。
  const { saves: platSaves = [] } = usePlatformData();
  const [scripts, setScripts] = useStatePL([]);
  const [loaded, setLoaded] = useStatePL(false);
  const [busyId, setBusyId] = useStatePL(null);
  // Codex P0-2 修复:没有现成存档时,不再传 fake save {id:null}。
  // 改成弹 NewGameModal,默认填好 script_id,走 saves.create 原子流。
  const [newModalScriptId, setNewModalScriptId] = useStatePL(null);
  // B1: export pack
  const [exportingId, setExportingId] = useStatePL(null);
  // B2: import pack
  const importPackRef = React.useRef(null);
  const [importPackBusy, setImportPackBusy] = useStatePL(false);
  // B3: overrides editor
  const [overridesScript, setOverridesScript] = useStatePL(null);
  // task 51: vector embedding 状态 per script (key: script_id → {running, chunks, cards, worldbook, model})
  const [embedStatus, setEmbedStatus] = useStatePL({});

  // task 51: 触发某 script 的向量化(GET status 也走这里 polling)
  const triggerEmbed = React.useCallback(async (sid) => {
    try {
      const r = await fetch(`${window.__API_BASE || ""}/api/scripts/${sid}/embed`, {
        method: "POST", credentials: "include",
      });
      const j = await r.json();
      if (j.ok === false) {
        window.__apiToast?.("向量化失败", { kind: "danger", detail: j.error || "未知错误", duration: 5000 });
        return;
      }
      window.toast?.("已启动向量化", { kind: "ok", detail: "Vertex text-embedding-004 后台跑,可在按钮上看进度", duration: 3000 });
      setEmbedStatus(s => ({ ...s, [sid]: j.status }));
    } catch (e) {
      window.__apiToast?.("向量化失败", { kind: "danger", detail: String(e), duration: 3000 });
    }
  }, []);

  // task 51: 自动 poll 所有 running 状态的 script,每 3s 刷一次 progress
  useEffectPL(() => {
    const runningIds = Object.entries(embedStatus).filter(([, v]) => v && v.running).map(([k]) => k);
    if (runningIds.length === 0) return;
    const iv = setInterval(async () => {
      for (const sid of runningIds) {
        try {
          const r = await fetch(`${window.__API_BASE || ""}/api/scripts/${sid}/embed/status`, { credentials: "include" });
          const j = await r.json();
          if (j.ok && j.status) {
            setEmbedStatus(s => ({ ...s, [sid]: j.status }));
            if (!j.status.running) {
              window.toast?.("向量化完成", {
                kind: "ok",
                detail: `chunks ${j.status.chunks.done} · cards ${j.status.cards.done} · worldbook ${j.status.worldbook.done}`,
                duration: 4000,
              });
            }
          }
        } catch (_) {}
      }
    }, 3000);
    return () => clearInterval(iv);
  }, [embedStatus]);

  const reload = React.useCallback(async () => {
    try {
      const r = await window.api.scripts.list();
      const list = Array.isArray(r) ? r : (r?.items || r?.scripts || []);
      const normed = list.map(window.__normalizeScript || ((x) => x));
      setScripts(normed);
      // task 51: 拉每个剧本的 embed 进度,UI 显示已建索引的剧本(check icon)
      // 失败不影响列表加载(各自 catch)
      Promise.all(normed.map(async (s) => {
        try {
          const sr = await fetch(`${window.__API_BASE || ""}/api/scripts/${s.id}/embed/status`, { credentials: "include" });
          const sj = await sr.json();
          if (sj.ok && sj.status) {
            setEmbedStatus(es => ({ ...es, [s.id]: sj.status }));
          }
        } catch (_) {}
      })).catch(() => {});
    } catch (_) {
      setScripts([]);
    } finally {
      setLoaded(true);
    }
  }, []);
  useEffectPL(() => {
    reload();
    const refresh = () => reload();
    // 兼容老事件名 + task 17 新事件名
    window.addEventListener("rpg:scripts:changed", refresh);
    window.addEventListener("rpg-scripts-updated", refresh);
    return () => {
      window.removeEventListener("rpg:scripts:changed", refresh);
      window.removeEventListener("rpg-scripts-updated", refresh);
    };
  }, [reload]);

  const onDelete = async (s) => {
    if (!confirm(`确定删除剧本「${s.title}」？相关存档与索引也会清理。`)) return;
    setBusyId(s.id);
    try {
      await window.api.scripts.delete(s.id);
      window.__apiToast?.("已删除", { kind: "ok" });
      reload();
    } catch (e) {
      window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message });
    } finally {
      setBusyId(null);
    }
  };

  const onImportPackFile = async (file) => {
    if (!file) return;
    setImportPackBusy(true);
    try {
      const result = await window.api.scripts.importPack(file);
      if (result && result.ok === false) throw new Error(result.error || result.detail || "导入失败");
      const sid = result?.script_id;
      const warnings = result?.warnings;
      window.__apiToast?.(
        "剧本包导入成功",
        { kind: "ok", detail: warnings?.length ? `警告: ${warnings.join("; ")}` : (sid ? `script #${sid}` : "") }
      );
      reload();
    } catch (e) {
      const detail = e?.payload?.detail || e?.message || "未知错误";
      window.__apiToast?.("导入失败", { kind: "danger", detail });
    } finally {
      setImportPackBusy(false);
      if (importPackRef.current) importPackRef.current.value = "";
    }
  };

  const onExportPack = async (s) => {
    setExportingId(s.id);
    try {
      const filename = (s.title || "script").replace(/[\\/:*?"<>|]/g, "_") + "_pack.zip";
      await window.api.scripts.exportPack(s.id, filename);
      window.__apiToast?.("导出成功", { kind: "ok", detail: filename });
    } catch (e) {
      window.__apiToast?.("导出失败", { kind: "danger", detail: e?.message });
    } finally {
      setExportingId(null);
    }
  };

  // task 52：之前 onPreview 只 alert 第一章前 400 字，章节多了无法浏览/编辑。
  // 改成开 ChaptersModal —— 真正展示章节列表 + 内容预览 + 重命名 + 重切分。
  const [chaptersOpen, setChaptersOpen] = useStatePL(null); // script row

  return (
    <section className="pl-sec" data-cap-anchor="scripts.list">
      <div className="pl-sec-head">
        <h2>已有剧本 <span className="muted-2">{scripts.length} 个</span></h2>
        <div className="pl-sec-tools">
          <button className="btn ghost" onClick={() => importPackRef.current?.click()} disabled={importPackBusy} data-tip="从 zip pack 导入完整剧本（含章节/角色卡/世界书）">
            {importPackBusy ? <Icon name="spinner" size={12} className="spin" /> : <Icon name="download" size={12} />} {importPackBusy ? "导入中…" : "导入剧本包"}
          </button>
          <input ref={importPackRef} type="file" accept=".zip" style={{display:"none"}} onChange={(e) => onImportPackFile(e.target.files?.[0])} />
          <a className="btn primary" href="#scripts-import" data-tip="导入新剧本">
            <Icon name="upload" size={12} /> 导入剧本
          </a>
        </div>
      </div>
      <table className="pl-table">
        <thead><tr><th>剧本</th><th>章节</th><th>字数</th><th>切分</th><th>异常</th><th>置信度</th><th></th></tr></thead>
        <tbody>
          {scripts.map(s => (
            <tr key={s.id}>
              <td>
                <div className="pl-title-cell">
                  <strong>{s.title}</strong>
                  <span className="muted-2 mono">{s.uid} · 更新 {s.updated_at}</span>
                </div>
              </td>
              <td className="mono">{(s.chapter_count || 0).toLocaleString()}</td>
              <td className="mono">{((s.word_count || 0) / 10000).toFixed(1)} 万</td>
              <td className="muted">{s.import_report?.mode_label || "—"}</td>
              <td>
                {(!s.import_report?.problem_label || s.import_report.problem_label === "未发现明显异常") ? (
                  <span className="pill ok"><span className="dot ok" /> 干净</span>
                ) : (
                  <span className="pill warn"><span className="dot warn" /> {s.import_report.problem_label}</span>
                )}
              </td>
              <td><ConfidenceBar value={s.import_report?.confidence || 0} /></td>
              <td className="pl-table-actions">
                <button className="iconbtn" data-tip="基于此剧本继续游戏" disabled={busyId === s.id}
                  onClick={() => {
                    // Codex P0-2 修复:旧代码没存档时传 fake {id:null,...},ContinuePicker.confirm 直接跳页不建档。
                    // 现在拆两条:有 sv 走 ContinuePicker (继续真实存档);没 sv 弹 NewGameModal 走原子建档流。
                    const sv = platSaves.find(x => x.script_id === s.id);
                    if (sv) {
                      window.__openContinue?.(sv);
                    } else {
                      setNewModalScriptId(s.id);
                    }
                  }}>
                  <Icon name="play" size={13} />
                </button>
                <button className="iconbtn" data-tip="查看章节 / 重切分" onClick={() => setChaptersOpen(s)}><Icon name="eye" size={13} /></button>
                <button className="iconbtn" data-tip="剧本覆盖设定 (overrides)" onClick={() => setOverridesScript(s)}>
                  <Icon name="edit" size={13} />
                </button>
                {/* task 51: 向量化按钮 — 触发 Vertex text-embedding-004 + pgvector
                    pipeline。进度从 embedStatus[s.id] 拿,显示 "建立向量索引" /
                    "向量化中 N%" / "已建索引"。 */}
                {(() => {
                  const es = embedStatus[s.id];
                  const totalDone = es ? (es.chunks.done + es.cards.done + es.worldbook.done) : 0;
                  const totalAll = es ? (es.chunks.total + es.cards.total + es.worldbook.total) : 0;
                  const pct = totalAll > 0 ? Math.round((totalDone / totalAll) * 100) : 0;
                  const fullyDone = es && !es.running && totalAll > 0 && totalDone >= totalAll;
                  const running = es && es.running;
                  const tip = running ? `向量化中 ${pct}%(${totalDone}/${totalAll})`
                    : fullyDone ? `已建索引 ${totalAll} 项(Vertex ${es.model || "text-embedding-004"})`
                    : "建立向量索引 — 提醒:需 Vertex API 凭证配置,跑全书 chunks + 角色卡 + 世界书";
                  return (
                    <button
                      className={`iconbtn ${fullyDone ? "ok" : ""}`}
                      data-tip={tip}
                      disabled={running}
                      onClick={() => !running && triggerEmbed(s.id)}
                    >
                      {running
                        ? <Icon name="spinner" size={13} className="spin" />
                        : fullyDone
                          ? <Icon name="check" size={13} />
                          : <Icon name="sparkle" size={13} />}
                    </button>
                  );
                })()}
                <button className="iconbtn" data-tip="导出剧本包 (zip)" disabled={exportingId === s.id} onClick={() => onExportPack(s)}>
                  {exportingId === s.id ? <Icon name="spinner" size={13} className="spin" /> : <Icon name="download" size={13} />}
                </button>
                <button className="iconbtn danger" data-tip="删除剧本" onClick={() => onDelete(s)} disabled={busyId === s.id}>
                  <Icon name="trash" size={13} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ChaptersModal script={chaptersOpen} onClose={() => setChaptersOpen(null)} onChanged={reload} />
      <OverridesModal script={overridesScript} onClose={() => setOverridesScript(null)} />
      {/* Codex P0-2 修复:基于此剧本"新建存档"流。无现成 save 时弹这个 modal,
          走 window.__createAndEnterSave 原子流 (POST /api/saves → activate → 跳页),
          不再走 ContinuePicker 假 save 跳过建档的旧路径。 */}
      <NewGameModal
        open={!!newModalScriptId}
        onClose={() => setNewModalScriptId(null)}
        defaultScriptId={newModalScriptId}
        onConfirm={async (payload) => {
          await window.__createAndEnterSave({
            ...payload,
            script_id: payload.script_id || newModalScriptId,
          });
        }}
      />
    </section>
  );
}

/* B3: overrides editor — GET/POST /api/v1/scripts/{id}/overrides (JSONB)。
   显示当前 script_overrides 的 raw JSON，支持 edit/save。 */
function OverridesModal({ script, onClose }) {
  const [raw, setRaw] = useStatePL("");
  const [loading, setLoading] = useStatePL(false);
  const [saving, setSaving] = useStatePL(false);
  const [err, setErr] = useStatePL("");
  const [dirty, setDirty] = useStatePL(false);

  React.useEffect(() => {
    if (!script) return;
    setLoading(true); setErr(""); setRaw(""); setDirty(false);
    (async () => {
      try {
        const r = await window.api.scripts.getOverrides(script.id);
        const data = r?.data ?? r ?? {};
        setRaw(JSON.stringify(data, null, 2));
      } catch (e) {
        setErr(e?.message || "加载失败");
        setRaw("{}");
      } finally {
        setLoading(false);
      }
    })();
  }, [script?.id]);

  if (!script) return null;

  const onSave = async () => {
    let parsed;
    try { parsed = JSON.parse(raw); } catch (e) {
      window.__apiToast?.("JSON 格式错误", { kind: "danger", detail: e.message });
      return;
    }
    setSaving(true);
    try {
      await window.api.scripts.saveOverrides(script.id, parsed);
      window.__apiToast?.("已保存", { kind: "ok" });
      setDirty(false);
    } catch (e) {
      window.__apiToast?.("保存失败", { kind: "danger", detail: e?.message });
    } finally {
      setSaving(false);
    }
  };

  let jsonValid = true;
  try { JSON.parse(raw); } catch (_) { jsonValid = false; }

  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(700px, 96vw)", maxHeight: "90vh", display: "flex", flexDirection: "column"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">剧本覆盖设定 (overrides) · {script.title}</div>
            <h2 className="pl-modal-title">{loading ? "加载中…" : "script_overrides JSONB"}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>
        {err && <div style={{padding: "8px 16px", color: "var(--danger)", fontSize: 13}}>{err}</div>}
        {!loading && (
          <div style={{flex: 1, minHeight: 0, display: "flex", flexDirection: "column", padding: "0 16px 0"}}>
            <div style={{fontSize: 11.5, color: "var(--muted-2)", marginBottom: 6, paddingTop: 12}}>
              直接编辑 JSON 对象。字段含义由后端解释；不认识的 key 会被保留。
              {!jsonValid && <span style={{color: "var(--danger)", marginLeft: 8}}>⚠ JSON 格式错误，无法保存</span>}
            </div>
            <textarea
              value={raw}
              onChange={(e) => { setRaw(e.target.value); setDirty(true); }}
              spellCheck={false}
              style={{
                flex: 1, minHeight: 320, fontFamily: "var(--font-mono, monospace)", fontSize: 12.5,
                lineHeight: 1.55, resize: "vertical", background: "var(--surface-2)",
                border: "1px solid " + (jsonValid ? "var(--line-soft)" : "var(--danger)"),
                borderRadius: "var(--r-2)", padding: "10px 12px", color: "var(--text)",
                outline: "none",
              }}
            />
          </div>
        )}
        <footer className="pl-modal-foot" style={{marginTop: 12}}>
          <span className="muted-2" style={{fontSize: 11.5}}>
            GET/POST /api/v1/scripts/{script.id}/overrides
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={onClose}>关闭</button>
            <button className="btn primary" onClick={onSave} disabled={saving || !dirty || !jsonValid}>
              {saving ? <><Icon name="spinner" size={12} className="spin" /> 保存中…</> : <><Icon name="check" size={12} /> 保存</>}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

/* task 52：之前剧本只有"alert 章节前 400 字"假预览。补一个真章节浏览/编辑器：
   - GET /api/scripts/{id}/chapters 分页列出
   - GET /api/scripts/{id}/chapter-facts 拿事实摘要（如果有）
   - POST /api/scripts/{id}/chapters/{idx} 重命名 / 改正文
   - POST /api/scripts/{id}/chapters/merge 合并相邻章节
   - POST /api/scripts/{id}/chapters/{idx}/split 拆分单章
   - POST /api/scripts/{id}/resplit 整本重切（rule+pattern）
   全部 BE wrappers 已存，但 FE 之前无入口。 */
function ChaptersModal({ script, onClose, onChanged }) {
  const [chapters, setChapters] = useStatePL([]);
  const [loading, setLoading] = useStatePL(false);
  const [err, setErr] = useStatePL("");
  const [activeIdx, setActiveIdx] = useStatePL(0);
  const [edit, setEdit] = useStatePL(null); // {idx, title, content}
  const [resplitOpen, setResplitOpen] = useStatePL(false);
  const [reloadTick, setReloadTick] = useStatePL(0);
  React.useEffect(() => {
    if (!script) return;
    setLoading(true); setErr(""); setActiveIdx(0);
    (async () => {
      try {
        const r = await window.api.scripts.chapters(script.id, { limit: 1000, offset: 0 });
        const list = (r && (r.chapters || r.items)) || [];
        setChapters(list);
      } catch (e) { setErr(e?.message || "拉取失败"); }
      finally { setLoading(false); }
    })();
  }, [script?.id, reloadTick]);
  if (!script) return null;
  const cur = chapters[activeIdx];
  const onRename = async () => {
    if (!cur) return;
    const t = prompt("新标题", cur.title || "");
    if (!t || t === cur.title) return;
    try {
      await window.api.scripts.updateChapter(script.id, cur.index ?? activeIdx, { title: t });
      window.__apiToast?.("已重命名", { kind: "ok" });
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.("失败", { kind: "danger", detail: e?.message }); }
  };
  const onMergeNext = async () => {
    if (!cur || activeIdx >= chapters.length - 1) return;
    if (!confirm(`合并第 ${activeIdx + 1} 章和第 ${activeIdx + 2} 章？`)) return;
    try {
      await window.api.scripts.mergeChapter(script.id, { first: cur.index ?? activeIdx, second: (chapters[activeIdx + 1]?.index ?? (activeIdx + 1)) });
      window.__apiToast?.("已合并", { kind: "ok" });
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.("失败", { kind: "danger", detail: e?.message }); }
  };
  const onSplit = async () => {
    if (!cur) return;
    const pos = prompt("从该章第几字处拆分？", "");
    const n = parseInt(pos, 10);
    if (!n || n < 1) return;
    try {
      await window.api.scripts.splitChapter(script.id, cur.index ?? activeIdx, { offset: n });
      window.__apiToast?.("已拆分", { kind: "ok" });
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.("失败", { kind: "danger", detail: e?.message }); }
  };
  const onResplit = async (vals) => {
    try {
      await window.api.scripts.resplit(script.id, { split_rule: vals.rule || "auto", custom_pattern: vals.pattern || "" });
      window.__apiToast?.("已重切分", { kind: "ok" });
      setResplitOpen(false);
      setReloadTick(x => x + 1);
      onChanged && onChanged();
    } catch (e) { window.__apiToast?.("重切分失败", { kind: "danger", detail: e?.message }); }
  };
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(960px, 96vw)", maxHeight: "90vh", display: "flex", flexDirection: "column"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">章节管理 · {script.title}</div>
            <h2 className="pl-modal-title">{loading ? "加载中…" : `共 ${chapters.length} 章 · 第 ${activeIdx + 1} 章`}</h2>
          </div>
          <div style={{display: "flex", gap: 6}}>
            <button className="btn ghost" onClick={() => setResplitOpen(true)} title="整本重切（按新规则）"><Icon name="refresh" size={12} /> 整本重切</button>
            <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
          </div>
        </header>
        {err && <div className="pl-model-empty" style={{padding: "16px"}}><span className="danger">加载失败：{err}</span></div>}
        {!err && chapters.length === 0 && !loading && (
          <div className="pl-model-empty" style={{padding: "24px"}}>该剧本暂无章节。试试「整本重切」更换切分规则。</div>
        )}
        {chapters.length > 0 && (
          <div style={{display: "grid", gridTemplateColumns: "220px 1fr", gap: 0, flex: 1, minHeight: 0}}>
            <div style={{borderRight: "1px solid var(--line-soft)", overflow: "auto", maxHeight: 480}}>
              {chapters.map((c, i) => (
                <button key={c.index ?? i}
                  className="btn ghost"
                  style={{display: "flex", justifyContent: "flex-start", width: "100%", padding: "8px 12px", borderRadius: 0,
                    background: i === activeIdx ? "var(--accent-soft)" : "transparent",
                    fontWeight: i === activeIdx ? 600 : 400,
                    borderBottom: "1px solid var(--line-soft)"}}
                  onClick={() => setActiveIdx(i)}>
                  <span className="muted-2 mono" style={{minWidth: 36, fontSize: 11}}>#{String(i + 1).padStart(3, "0")}</span>
                  <span style={{overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1, textAlign: "left", fontSize: 12.5}}>
                    {c.title || "未命名"}
                  </span>
                </button>
              ))}
            </div>
            <div style={{overflow: "auto", padding: 16, maxHeight: 480}}>
              {cur && <>
                <div style={{display: "flex", alignItems: "center", gap: 8, marginBottom: 12}}>
                  <strong style={{fontSize: 15}}>{cur.title || "未命名"}</strong>
                  <span className="muted-2 mono" style={{fontSize: 11}}>{(cur.content || "").length.toLocaleString()} 字</span>
                  <div style={{marginLeft: "auto", display: "flex", gap: 6}}>
                    <button className="btn ghost" onClick={onRename}><Icon name="edit" size={12} /> 重命名</button>
                    <button className="btn ghost" onClick={onSplit}><Icon name="branch" size={12} /> 拆分本章</button>
                    {activeIdx < chapters.length - 1 && (
                      <button className="btn ghost" onClick={onMergeNext}><Icon name="link" size={12} /> 合并下一章</button>
                    )}
                  </div>
                </div>
                <pre style={{whiteSpace: "pre-wrap", fontFamily: "var(--font-serif)", fontSize: 13.5, lineHeight: 1.7, margin: 0}}>
                  {(cur.content || "").slice(0, 4000)}{cur.content && cur.content.length > 4000 ? "\n\n…（截断显示前 4000 字）" : ""}
                </pre>
              </>}
            </div>
          </div>
        )}
        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} /> GET /api/scripts/{script.id}/chapters · POST /chapters/{`{idx}`} / merge / split / resplit
          </span>
          <button className="btn ghost" onClick={onClose}>关闭</button>
        </footer>
      </div>
      <PromptModal
        open={resplitOpen}
        eyebrow="整本重切"
        title={`${script.title} · 用新规则重切`}
        hint="POST /api/scripts/{id}/resplit"
        fields={[
          { key: "rule", label: "切分规则", type: "select", default: "auto",
            options: [
              { value: "auto",     label: "auto · 自动识别" },
              { value: "blank",    label: "blank · 空行分章" },
              { value: "marker",   label: "marker · 第X章" },
              { value: "regex",    label: "regex · 自定义" },
            ] },
          { key: "pattern", label: "自定义正则", placeholder: "rule=regex 时填，例：^第[一二三四五六七八九十百千]+章" },
        ]}
        submitLabel="开始重切"
        onClose={() => setResplitOpen(false)}
        onConfirm={onResplit}
      />
    </div>
  );
}

const IMPORT_STAGES = [
  { id: "split",    label: "拆章节",     hint: "按规则切分原文",            tok_per_chap: 0 },
  { id: "save",     label: "入库",       hint: "写入剧本表 + 全文索引",     tok_per_chap: 0 },
  { id: "extract",  label: "人物提取",   hint: "扫描章节，发现角色名 + 关系", tok_per_chap: 120 },
  { id: "card",     label: "人设卡生成", hint: "为每个识别角色合成卡片",     tok_per_chap: 60 },
  { id: "world",    label: "世界书建立", hint: "地点 / 时代 / 设定词条",     tok_per_chap: 90 },
  { id: "timeline", label: "时间线建立", hint: "事件锚点 + 章节映射",        tok_per_chap: 40 },
];

function ScriptsImportView() {
  const [rule, setRule] = useStatePL("auto");
  const [pattern, setPattern] = useStatePL("");
  const [title, setTitle] = useStatePL("");
  const [job, setJob] = useStatePL(null); // { id, status, stages, currentStage, file, ... } | null
  const [estimate, setEstimate] = useStatePL(null);
  const [previewBusy, setPreviewBusy] = useStatePL(false);
  const [selectedFile, setSelectedFile] = useStatePL(null);
  const [dragOver, setDragOver] = useStatePL(false);
  const fileInputRef = React.useRef(null);
  const tickRef = React.useRef(null);

  // Restore job from localStorage on mount (page-refresh resilient)
  React.useEffect(() => {
    try {
      const cached = localStorage.getItem("rpg.import.job");
      if (cached) {
        const j = JSON.parse(cached);
        if (j && j.status === "running") setJob(j);
        else if (j && j.status === "estimating") setJob(j);
      }
    } catch {}
  }, []);

  // Persist job state
  React.useEffect(() => {
    if (job) localStorage.setItem("rpg.import.job", JSON.stringify(job));
    else localStorage.removeItem("rpg.import.job");
  }, [job]);

  // task 39: real job 必须轮询后端拿真实进度。之前 job.real=true 直接 return 没轮询,
  // 所以 UI 永远卡 0%/0s,直到用户手动刷新页面才能看到剧本已 import 完。
  // backend ks_<sid>_<hex> job kind=knowledge_sync,目前是 1-stage(done/error),
  // 简化映射:status==done → 全部 stages 标 done; status==error → 标 error。
  React.useEffect(() => {
    if (!job || !job.real || job.status !== "running") return;
    let cancelled = false;
    const poll = async () => {
      try {
        const resp = await window.api.scripts.jobStatus(job.id);
        if (cancelled) return;
        const jb = resp && (resp.job || resp);
        if (!jb || !jb.status) return;
        if (jb.status === "done") {
          setJob(j => j ? { ...j,
            status: "done",
            finished_at: Date.now(),
            stages: j.stages.map(s => ({ ...s, status: "done", progress: 1, tokens_used: s.tokens_est, done_at: Date.now() })),
            knowledge_result: jb.usage_actual?.result || null,
          } : j);
          window.toast?.("剧本导入完成", { kind: "ok", detail: `script #${jb.script_id}`, duration: 2400 });
          try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
        } else if (jb.status === "error" || jb.status === "failed") {
          setJob(j => j ? { ...j, status: "cancelled", finished_at: Date.now(), error: jb.error || "导入失败" } : j);
          window.__apiToast?.("导入失败", { kind: "danger", detail: jb.error || "未知错误", duration: 4000 });
        }
      } catch (_) { /* 单次失败不影响下一次轮询 */ }
    };
    poll();
    const iv = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [job?.id, job?.real, job?.status]);

  // task 17/18/19: 之前这个 setInterval 是「假任务模拟器」：
  //   - 进度条 ticks 是 Math.random，假的
  //   - 完成时直接把假行塞进 window.MOCK_PLATFORM.scripts → 这是 task 19 真后端只有 1 条
  //     却 UI 显示 5 条的原因
  //   - 完成 toast 在 setJob 的 updater 里同步发出 → React 抱怨「setState while rendering」
  // 现在：real 导入由后端同步返回（task 17 之后），不需要模拟；只在没接后端的 demo
  // 模式（job.real=false 且非 done/cancelled）才走一次性 mock tick，不再 mutate MOCK_PLATFORM。
  React.useEffect(() => {
    if (!job || job.status !== "running" || job.real) {
      if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
      return;
    }
    // demo / 离线预览模式：纯视觉 tick，不动 MOCK_PLATFORM，不在 updater 里发 toast
    tickRef.current = setInterval(() => {
      setJob(j => {
        if (!j || j.status !== "running" || j.real) return j;
        const stages = j.stages.map(s => ({ ...s }));
        let cur = j.currentStage;
        const s = stages[cur];
        if (!s) return j;
        s.progress = Math.min(1, s.progress + 0.05 + Math.random() * 0.07);
        if (s.progress >= 1) {
          s.progress = 1; s.status = "done";
          s.tokens_used = s.tokens_est; s.done_at = Date.now();
          if (cur + 1 < stages.length) {
            stages[cur + 1].status = "running";
            stages[cur + 1].started_at = Date.now();
            cur += 1;
          } else {
            return { ...j, stages, currentStage: cur, status: "done", finished_at: Date.now(), demo: true };
          }
        }
        return { ...j, stages, currentStage: cur };
      });
    }, 500);
    return () => { if (tickRef.current) clearInterval(tickRef.current); };
  }, [job?.status, job?.real]);

  // task 49：原 fakeFile = {chapters: 162, words: 410_000} 是凭空写的"示例规模"，
  // 不选文件时会展示出来误导用户。删除 fakeFile，未选文件时 startEstimate 直接
  // 提示"请先选择本地文件"，不假装真实，不生成假预算。

  const onPickFile = (file) => {
    if (!file) return;
    if (file.size > 50 * 1024 * 1024) {
      window.__apiToast?.("文件过大", { kind: "danger", detail: "最大 50MB", duration: 2400 });
      return;
    }
    setSelectedFile(file);
    if (!title) setTitle(file.name.replace(/\.(txt|md)$/i, ""));
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) onPickFile(f);
  };

  // task 16: 读 File → 纯 base64（去掉 data URL 前缀），喂给后端 decode_upload()。
  // 之前发的 {rule, pattern, title, filename, size} 后端 file=None → 必 400 → 静默回退到 fakeFile。
  const readFileAsBase64 = (file) => new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const s = String(r.result || "");
      const idx = s.indexOf(",");
      resolve(idx >= 0 ? s.slice(idx + 1) : s);
    };
    r.onerror = () => reject(r.error || new Error("文件读取失败"));
    r.readAsDataURL(file);
  });

  const startEstimate = async () => {
    setPreviewBusy(true);
    setEstimate(null);
    // task 49：不选文件时彻底不出预算（之前给假的 162 章 41 万字）
    if (!selectedFile) {
      setEstimate({
        file: null, chapters: 0, words: 0,
        stages: [], totalTokens: 0, totalSec: 0, cost: 0,
        model: "—",
        warnings: ["请先选择本地剧本文件再生成预算。"],
        previewError: "未选择文件",
      });
      setPreviewBusy(false);
      return;
    }
    // 选了真实文件：必须打真后端；失败就给用户看清楚错误，绝不回退 fakeFile
    let result = null;
    try {
      const base64 = await readFileAsBase64(selectedFile);
      const body = {
        file: { name: selectedFile.name, base64 },
        split_rule: rule || "auto",
        custom_pattern: pattern || "",
        sample_limit: 20,
      };
      result = await window.api.scripts.preview(body);
    } catch (e) {
      const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || "未知错误";
      window.__apiToast?.("预览失败", { kind: "danger", detail, duration: 5000 });
      setEstimate({
        file: { name: selectedFile.name, size: selectedFile.size, chapters: 0, words: 0 },
        chapters: 0, words: 0,
        stages: [], totalTokens: 0, totalSec: 0, cost: 0,
        model: "—",
        warnings: [`预览失败：${detail}`],
        previewError: detail,
      });
      setPreviewBusy(false);
      return;
    }
    // 成功路径：用后端真实数字
    const chapters = Number(result.total_chapters) || (Array.isArray(result.preview) ? result.preview.length : 0);
    const words = Number(result.total_words) || 0;
    const stages = IMPORT_STAGES.map(s => ({
      id: s.id, label: s.label, hint: s.hint,
      tokens_est: s.tok_per_chap * Math.max(chapters, 1),
      time_est_sec: Math.round(s.tok_per_chap * Math.max(chapters, 1) / 800),
    }));
    const totalTokens = stages.reduce((a, s) => a + s.tokens_est, 0);
    const totalSec = stages.reduce((a, s) => a + s.time_est_sec, 0);
    const cost = totalTokens * 0.75 / 1_000_000;
    const warnings = [];
    if (Array.isArray(result.warnings)) warnings.push(...result.warnings);
    if (result.report && result.report.mode_label) {
      warnings.push(`切分模式：${result.report.mode_label}（置信 ${result.report.confidence ?? "—"}）`);
    }
    setEstimate({
      file: { name: selectedFile.name, size: selectedFile.size, chapters, words },
      chapters, words,
      stages, totalTokens, totalSec, cost,
      model: result.model || "GPT-4o · RPG 调优",
      preview: result.preview,
      report: result.report,
      warnings,
    });
    setPreviewBusy(false);
  };

  const startImport = async () => {
    // task 17: 真正打通分片上传 → /api/scripts/import 流水线。
    // 之前发的 init 字段 {size, kind, chunk_size} 全不对（后端要 total_bytes/total_chunks）→ 400。
    // 之前任何一步失败仍会创建 fake job 让 UI 假装在跑 → 用户误以为成功。
    // 现在：选了真实文件就必须真传成功；任一步失败 toast 报错并停止，不再造 job。
    const CHUNK_SIZE = 1024 * 1024;
    if (selectedFile) {
      let uploadId = null;
      try {
        const totalBytes = selectedFile.size;
        const totalChunks = Math.max(1, Math.ceil(totalBytes / CHUNK_SIZE));
        const init = await window.api.uploads.init({
          filename: selectedFile.name,
          total_bytes: totalBytes,
          total_chunks: totalChunks,
        });
        uploadId = init.upload_id || init.id;
        if (!uploadId) throw new Error("后端未返回 upload_id");
        for (let i = 0; i < totalChunks; i++) {
          const blob = selectedFile.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
          await window.api.uploads.chunk(uploadId, blob, i);
        }
        await window.api.uploads.finish(uploadId, {});
        const importResp = await window.api.scripts.importScript({
          upload_id: uploadId,
          title: title || selectedFile.name.replace(/\.(txt|md)$/i, ""),
          split_rule: rule || "auto",
          custom_pattern: pattern || "",
        });
        if (!importResp || importResp.ok === false) {
          throw new Error((importResp && (importResp.error || importResp.detail)) || "导入接口返回失败");
        }
        const sc = importResp.script || {};
        // task 41: importScript 只跑简化 sync (facts/chunks),没跑 LLM cards/worldbook。
        // 必须额外调 import-pipeline 启动完整 5-stage LLM 流水线,否则角色卡 + 世界书全是 0,
        // 后面 chat 上下文严重缺失。优先用 imp_ job_id 跟踪进度(完整 5-stage),
        // ks_ job_id 是降级 fallback。
        let pipelineJobId = null;
        try {
          const pipelineResp = await window.api.scripts.importPipeline(sc.id, {
            enable_cards: true,
            enable_worldbook: true,
          });
          if (pipelineResp && pipelineResp.ok !== false) {
            pipelineJobId = pipelineResp.job_id;
          }
        } catch (e) {
          // pipeline 启动失败不致命,fallback 用 ks_ job_id 至少能看到 facts/chunks 进度
          console.warn("import-pipeline failed to start:", e);
        }
        const stages = estimate.stages.map((s, i) => ({
          ...s,
          status: i === 0 ? "running" : "pending",
          progress: 0, tokens_used: 0,
          started_at: i === 0 ? Date.now() : null, done_at: null,
        }));
        const j = {
          id: pipelineJobId
            || (importResp.knowledge && importResp.knowledge.job_id)
            || ("script_" + (sc.id || "?")),
          file: estimate.file,
          title: sc.title || title || estimate.file.name,
          script_id: sc.id,
          mode: SPLIT_RULES.find(r => r.id === rule)?.label,
          stages, currentStage: 0,
          totalTokens: estimate.totalTokens,
          status: "running",
          started_at: Date.now(),
          real: true,
        };
        setJob(j);
        setEstimate(null);
        // 通知外部 ScriptsPage 刷新真实列表（task 19 联动）
        try { window.dispatchEvent(new CustomEvent("rpg-scripts-updated")); } catch (_) {}
        window.toast && window.toast("导入成功", {
          kind: "ok",
          // Codex #8:不假装"向量库"。后端 _embed_query() 是 stub (返回 None),
          // pgvector 查询自动退化到 ILIKE 关键字匹配 + 章节摘要召回。
          // 文案如实表达,避免用户误以为已建立完整向量库。
          detail: `已建立剧本 #${sc.id} · ${sc.title || ""} · 基础知识库 (关键字 + 章节摘要) 后台同步中`,
          duration: 3000,
        });
      } catch (e) {
        // 取消任何已经初始化的 upload，让服务器释放临时块
        if (uploadId) { try { await window.api.uploads.cancel(uploadId); } catch (_) {} }
        const detail = (e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || "未知错误";
        window.__apiToast?.("导入失败", { kind: "danger", detail, duration: 5000 });
        // 关键：不要建 fake job 让用户误以为在跑
        setJob(null);
        // estimate 保留，以便用户修改设置后重试
      }
      return;
    }
    // 没选文件：仅在 isMockEstimate（明确示例）下允许 demo job
    if (estimate && estimate.isMockEstimate) {
      window.__apiToast?.("仅示例预算，未上传文件", { kind: "warn", detail: "请选择本地文件后再确认导入", duration: 3000 });
      return;
    }
    window.__apiToast?.("请先选择本地文件", { kind: "warn" });
  };

  const cancelJob = async () => {
    if (!job) return;
    if (job.real) {
      try { await window.api.scripts.jobCancel(job.id); } catch (e) {}
    }
    setJob(j => ({ ...j, status: "cancelled", cancelled_at: Date.now() }));
    window.toast?.("已取消导入任务", { kind: "warn", detail: "job " + job.id, duration: 2400 });
  };

  const dismissJob = () => {
    setJob(null);
  };

  return (
    <>
      {/* Persistent job banner — visible even after page refresh */}
      {job && job.status !== "done" && job.status !== "cancelled" && (
        <ImportJobBanner job={job} onCancel={cancelJob} />
      )}
      {job && (job.status === "done" || job.status === "cancelled") && (
        <ImportJobResult job={job} onDismiss={dismissJob} onReuse={() => { setJob(null); setEstimate(null); }} />
      )}

      <section className="pl-sec" data-cap-anchor="scripts.import">
        <div className="pl-sec-head">
          <h2>导入剧本</h2>
          <div className="pl-sec-tools">
            <span className="muted-2" style={{fontSize: 11.5}}>支持 TXT · MD · 最大 50MB</span>
          </div>
        </div>
        <div className="pl-import">
          <div className="pl-import-grid">
            <div className="pl-field">
              <label>标题</label>
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="留空将使用文件名" />
            </div>
            <div className="pl-field">
              <label>切分规则</label>
              <div className="pl-rules">
                {SPLIT_RULES.map(r => (
                  <button key={r.id}
                    className={`pl-rule-chip ${rule === r.id ? "active" : ""}`}
                    onClick={() => setRule(r.id)}>
                    {r.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="pl-field">
              <label>自定义正则或模板</label>
              <input
                value={pattern} onChange={(e) => setPattern(e.target.value)}
                disabled={rule !== "custom"}
                placeholder="例:^第[一二三四五六七八九十百千]+章" />
              <span className="pl-hint">仅在『自定义』下生效。</span>
            </div>
          </div>
          <div
            className={`pl-drop ${dragOver ? "active" : ""} ${selectedFile ? "has-file" : ""}`}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
          >
            <Icon name="upload" size={20} style={{color: "var(--muted)"}} />
            <strong>{selectedFile ? selectedFile.name : "把 TXT / MD 拖到这里"}</strong>
            <span>
              {selectedFile
                ? <>已选择 · {fmtBytes(selectedFile.size)} · <a href="#" onClick={(e) => { e.preventDefault(); setSelectedFile(null); }}>移除</a></>
                : <>或 <a href="#" onClick={(e) => { e.preventDefault(); fileInputRef.current?.click(); }}>选择本地文件</a> · 先预算章节切分 / Token / 成本，再决定是否导入</>
              }
            </span>
            <input ref={fileInputRef} type="file" accept=".txt,.md" style={{display: "none"}}
              onChange={(e) => onPickFile(e.target.files?.[0])} />
          </div>
          <div className="pl-import-foot">
            <span className="muted-2" style={{fontSize: 11.5}}>
              整个过程在后台运行，支持页面刷新；除非取消，导入不会被打断。
            </span>
            <div style={{display: "flex", gap: 6}}>
              <button className="btn ghost" onClick={() => setEstimate(null)} disabled={previewBusy || !estimate}>清空预算</button>
              <button className="btn primary" onClick={startEstimate} disabled={previewBusy || !!job}>
                <Icon name="eye" size={12} /> {previewBusy ? "计算预算中…" : "预览章节切分"}
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Estimate / preview section (no inline modal) */}
      {estimate && !job && (
        <ImportEstimateView
          estimate={estimate}
          rule={rule}
          onCancel={() => setEstimate(null)}
          onConfirm={startImport}
        />
      )}
    </>
  );
}

function ImportJobBanner({ job, onCancel }) {
  const overallProgress = job.stages.reduce((a, s) => a + s.progress, 0) / job.stages.length;
  const elapsed = Math.round((Date.now() - job.started_at) / 1000);
  return (
    <section className="pl-sec">
      <div className="pl-import-job">
        <div className="pl-import-job-head">
          <div className="pl-import-job-title">
            <span className="dot accent pulse" />
            <strong className="serif">正在导入 · {job.title}</strong>
            <span className="muted-2 mono">job {job.id} · 已用 {elapsed}s</span>
          </div>
          <div style={{display: "flex", gap: 6, alignItems: "center"}}>
            <span className="muted-2" style={{fontSize: 11}}>页面刷新不影响 · 任务在后台运行</span>
            <button className="btn ghost" onClick={onCancel} data-tip="终止任务">
              <Icon name="stop" size={12} /> 取消导入
            </button>
          </div>
        </div>
        <div className="pl-import-progress-bar">
          <div className="pl-import-progress-fill" style={{width: (overallProgress * 100).toFixed(1) + "%"}} />
        </div>
        <div className="pl-import-stages">
          {job.stages.map((s, i) => (
            <div key={s.id} className={`pl-import-stage pl-import-stage-${s.status}`}>
              <div className="pl-import-stage-num mono">
                {s.status === "done" ? <Icon name="check" size={11} /> : s.status === "running" ? <Icon name="spinner" size={11} className="spin" /> : String(i + 1).padStart(2, "0")}
              </div>
              <div className="pl-import-stage-body">
                <div className="pl-import-stage-name">
                  <strong>{s.label}</strong>
                  <span className="muted-2" style={{fontSize: 11}}>{s.hint}</span>
                </div>
                <div className="pl-import-stage-meta">
                  <span className="mono muted-2" style={{fontSize: 11}}>
                    {s.status === "running" ? `${Math.round(s.progress * 100)}%` :
                     s.status === "done" ? `${fmtN(s.tokens_used)} tok` :
                     `~${fmtN(s.tokens_est)} tok`}
                  </span>
                  {s.status === "running" && (
                    <div className="pl-import-mini-bar"><div style={{width: (s.progress * 100) + "%"}} /></div>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function ImportJobResult({ job, onDismiss, onReuse }) {
  const ok = job.status === "done";
  const totalTokens = job.stages.reduce((a, s) => a + (s.tokens_used || 0), 0);
  return (
    <section className="pl-sec">
      <div className={`pl-import-job pl-import-job-${ok ? "done" : "cancelled"}`}>
        <div className="pl-import-job-head">
          <div className="pl-import-job-title">
            <span className={`dot ${ok ? "ok" : "warn"}`} />
            <strong className="serif">
              {ok ? "导入完成" : "已取消"} · {job.title}
            </strong>
            <span className="muted-2 mono">{ok ? `${fmtN(totalTokens)} tok 实际消耗` : `job ${job.id}`}</span>
          </div>
          <div style={{display: "flex", gap: 6}}>
            {ok && <a className="btn primary" href="#scripts" onClick={onDismiss}><Icon name="arrow_right" size={12} /> 去剧本管理查看</a>}
            <button className="btn ghost" onClick={onReuse}>{ok ? "再导入一份" : "重试"}</button>
            <button className="iconbtn" onClick={onDismiss} data-tip="隐藏"><Icon name="close" size={12} /></button>
          </div>
        </div>
      </div>
    </section>
  );
}

function ImportEstimateView({ estimate, rule, onCancel, onConfirm }) {
  return (
    <section className="pl-sec">
      <div className="pl-sec-head">
        <h2>导入预算 <span className="muted-2">『{estimate.file.name}』 · {SPLIT_RULES.find(r => r.id === rule)?.label}</span></h2>
        <div className="pl-sec-tools">
          <button className="btn ghost" onClick={onCancel}>取消</button>
          <button className="btn primary" onClick={onConfirm}>
            <Icon name="check" size={12} /> 确认导入（后台运行）
          </button>
        </div>
      </div>
      <div className="pl-import" style={{borderStyle: "solid"}}>
        <div className="pl-validate-stat-row">
          <div className="pl-validate-stat">
            <span className="pl-stat-label">章节</span>
            <span className="pl-stat-value" style={{fontSize: 20}}>{estimate.chapters}</span>
          </div>
          <div className="pl-validate-stat">
            <span className="pl-stat-label">字数</span>
            <span className="pl-stat-value" style={{fontSize: 20}}>{(estimate.words / 10000).toFixed(1)}<span style={{fontSize: 12, color: "var(--muted)", marginLeft: 3}}>万</span></span>
          </div>
          <div className="pl-validate-stat">
            <span className="pl-stat-label">预估 Token</span>
            <span className="pl-stat-value" style={{fontSize: 20}}>{fmtN(estimate.totalTokens)}</span>
          </div>
          <div className="pl-validate-stat">
            <span className="pl-stat-label">预估成本</span>
            <span className="pl-stat-value" style={{fontSize: 20, color: "var(--accent)"}}>${estimate.cost.toFixed(2)}</span>
          </div>
          <div className="pl-validate-stat">
            <span className="pl-stat-label">预计耗时</span>
            <span className="pl-stat-value" style={{fontSize: 20}}>{Math.round(estimate.totalSec / 60)}<span style={{fontSize: 12, color: "var(--muted)", marginLeft: 3}}>分钟</span></span>
          </div>
        </div>
        <div className="muted-2" style={{fontSize: 11, padding: "4px 2px"}}>
          使用模型：<span className="mono">{estimate.model}</span> · 实际消耗取决于章节文本长度 · 后台运行，刷新不影响
        </div>
        <table className="pl-table" style={{margin: 0}}>
          <thead><tr><th style={{width: 36}}>#</th><th>阶段</th><th>说明</th><th style={{textAlign: "right"}}>预估 Token</th><th style={{textAlign: "right"}}>预计耗时</th></tr></thead>
          <tbody>
            {estimate.stages.map((s, i) => (
              <tr key={s.id}>
                <td className="mono muted-2">{String(i + 1).padStart(2, "0")}</td>
                <td><strong style={{fontFamily: "var(--font-serif)", fontSize: 14}}>{s.label}</strong></td>
                <td className="muted">{s.hint}</td>
                <td className="mono" style={{textAlign: "right"}}>{fmtN(s.tokens_est)}</td>
                <td className="mono" style={{textAlign: "right"}}>{s.time_est_sec < 60 ? s.time_est_sec + "s" : Math.round(s.time_est_sec / 60) + "min"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {estimate.warnings?.length > 0 && (
          <div style={{padding: 10, background: "var(--warn-soft)", border: "1px solid rgba(212, 179, 102, 0.32)", borderRadius: 6, fontSize: 12}}>
            <div className="muted-2" style={{fontSize: 10.5, textTransform: "uppercase", letterSpacing: "0.14em", marginBottom: 4}}>注意</div>
            {estimate.warnings.map((w, i) => <div key={i} style={{paddingLeft: 14, position: "relative"}}>
              <span style={{position: "absolute", left: 0}}>•</span> {w}
            </div>)}
          </div>
        )}
      </div>
    </section>
  );
}

Object.assign(window, {
  ScriptsPage, ScriptsListView, ChaptersModal, OverridesModal, ScriptsImportView,
  ImportJobBanner, ImportJobResult, ImportEstimateView,
  ScriptPreviewModal, ConfidenceBar,
});
