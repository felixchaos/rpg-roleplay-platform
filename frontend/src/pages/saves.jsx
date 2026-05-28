/* Saves / Branches / ContinuePicker / NewGameModal — split out of platform-app.jsx (task 52).
   只搬家，UI / props 流 / fetch 路径完全不变。
   依赖 platform-app.jsx 注入的全局: Icon / ConfirmModal / BranchGraph (来自 branch-graph.jsx)。
   注意：本文件提供 NewGameModal 给 scripts.jsx 与 platform-app.jsx 共享（通过 window.NewGameModal）。 */

const { useState: useStatePL, useEffect: useEffectPL, useMemo: useMemoPL, useCallback: useCallbackPL } = React;

/* ---------------------------- SAVES ---------------------------- */
function SavesPage({ subPage = "list" }) {
  return (
    <div className="pl-stack">
      {subPage === "branches" ? <BranchesPage /> : <SavesListView />}
    </div>
  );
}

function SavesListView() {
  // task 19/20: 不再混 mock；空数组也覆盖，让"无存档/无剧本"的真实空态被前端识别到
  const [saves, setSaves] = useStatePL([]);
  const [scripts, setScripts] = useStatePL([]);
  const [createOpen, setCreateOpen] = useStatePL(false);
  const [menuOpen, setMenuOpen] = useStatePL(null);

  const reload = React.useCallback(async () => {
    try {
      const r = await window.api.saves.list();
      const list = Array.isArray(r) ? r : (r?.items || r?.saves || []);
      setSaves(list.map(window.__normalizeSave || ((x) => x)));
    } catch (_) { setSaves([]); }
    try {
      const s = await window.api.scripts.list();
      const list = Array.isArray(s) ? s : (s?.items || s?.scripts || []);
      setScripts(list.map(window.__normalizeScript || ((x) => x)));
    } catch (_) { setScripts([]); }
  }, []);
  useEffectPL(() => {
    reload();
    const refresh = () => reload();
    window.addEventListener("rpg-scripts-updated", refresh);
    window.addEventListener("rpg-saves-updated", refresh);
    return () => {
      window.removeEventListener("rpg-scripts-updated", refresh);
      window.removeEventListener("rpg-saves-updated", refresh);
    };
  }, [reload]);

  const onCreate = async (vals) => {
    // task 20: 失败时 throw，让 NewGameModal 里的 catch 把错误显示在 modal 内（不静默）。
    // 成功才关闭 modal、reload、跳转。
    try {
      const created = await window.api.saves.create({
        title: vals.title || ("新存档 · " + new Date().toLocaleString()),
        script_id: vals.script_id || (scripts[0] && scripts[0].id),
        // task 29：把 character_kind 也透传给后端，否则 character_id 单独到达后端无法
        // 区分 persona / user_card / script_card，应用不了到 initial state。
        character_id: vals.character_id || null,
        character_kind: vals.character_kind || null,
        npc_id: vals.npc_id || null,
        new_card: vals.new_card || null,
        // wizard step 3 & 4: birthpoint + identity
        birthpoint: vals.birthpoint || null,
        identity: vals.identity || null,
      });
      if (created && created.ok === false) {
        throw new Error(created.error || created.detail || "后端拒绝创建");
      }
      window.__apiToast?.("已创建存档", { kind: "ok", duration: 1600 });
      setCreateOpen(false);
      reload();
      try { window.dispatchEvent(new CustomEvent("rpg-saves-updated")); } catch (_) {}
      const save = created && (created.save || created);
      if (save && save.id) {
        window.__openContinue?.({ ...save, ...window.__normalizeSave?.(save) });
      }
    } catch (e) {
      window.__apiToast?.("创建失败", { kind: "danger", detail: e?.message, duration: 3000 });
      throw e;  // 让 NewGameModal 接住，显示 inline 错误
    }
  };

  // task 127: 用平台 ConfirmModal 代替浏览器 confirm()
  const [deleteTarget, setDeleteTarget] = React.useState(null);  // {id, title} | null
  const onDeleteSave = (s) => setDeleteTarget(s);
  const confirmDeleteSave = async () => {
    if (!deleteTarget) return;
    const s = deleteTarget;
    setDeleteTarget(null);
    try {
      await window.api.saves.remove(s.id);
      window.__apiToast?.("已删除", { kind: "ok" });
      reload();
    } catch (e) {
      window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message });
    }
  };
  // task 50：之前 SavesListView 只能"新建 + 继续"。补「导入存档」按钮 + 卡片
  // 弹出菜单加「设为当前」（之前只在分支页有 activate）。BE 都早就有了。
  const importInputRef = React.useRef(null);
  const onImportFile = async (file) => {
    if (!file) return;
    try {
      window.__apiToast?.(`正在导入 ${file.name}…`, { kind: "info", duration: 1500 });
      const r = await window.api.saves.importFile(file);
      if (r && r.ok === false) throw new Error(r.error || r.detail || "后端拒绝导入");
      window.__apiToast?.("存档已导入", { kind: "ok", duration: 2000 });
      reload();
    } catch (e) {
      window.__apiToast?.("导入失败", { kind: "danger", detail: e?.message });
    }
  };
  const onActivate = async (s) => {
    try {
      await window.api.saves.activate(s.id);
      window.__apiToast?.("已设为当前存档", { kind: "ok", duration: 1600 });
      reload();
    } catch (e) {
      window.__apiToast?.("切换失败", { kind: "danger", detail: e?.message });
    }
  };

  return (
    <div className="pl-stack">
      <section className="pl-sec" data-cap-anchor="saves.list">
        <div className="pl-sec-head">
          <h2>存档目录 <span className="muted-2">{saves.length} 个</span></h2>
          <div className="pl-sec-tools">
            <input ref={importInputRef} type="file" accept=".zip,.json,.tar.gz"
              style={{display: "none"}} onChange={(e) => { onImportFile(e.target.files?.[0]); e.target.value = ""; }} />
            <button className="btn ghost" onClick={() => importInputRef.current?.click()} title="从 zip/JSON 导入存档">
              <Icon name="upload" size={12} /> 导入存档
            </button>
            <button className="btn ghost" onClick={() => setCreateOpen(true)} title="新建一个存档">
              <Icon name="plus" size={12} /> 新建存档
            </button>
            <button className="btn primary" onClick={() => window.__openContinue?.(saves[0])} title="继续上次存档"
              disabled={!saves.length} style={{opacity: saves.length ? 1 : 0.5}}>
              <Icon name="play" size={12} /> 进入当前游戏
            </button>
          </div>
        </div>
        <div className="pl-saves-grid">
          {saves.map(s => {
            const script = scripts.find(sc => sc.id === s.script_id);
            return (
              <div key={s.id} className={`pl-save-card ${s.current ? "current" : ""}`}>
                <div className="pl-save-card-head">
                  <h3>{s.title}</h3>
                  {s.current && <span className="pill accent"><span className="dot accent pulse" /> 在玩</span>}
                </div>
                <div className="pl-save-meta">
                  <span><Icon name="book" size={11} /> {script?.title || "未知剧本"}</span>
                  <span><Icon name="branch" size={11} /> {s.branch_count} 节点</span>
                  <span><Icon name="history" size={11} /> {s.updated_at}</span>
                </div>
                <p className="pl-save-snippet">
                  {s._raw?.snippet || s._raw?.last_message || "（暂无最新片段，进入游戏后会自动同步。）"}
                </p>
                <div className="pl-save-card-foot">
                  <button className="btn primary" onClick={() => window.__openContinue?.(s)} title="选择分支继续">
                    <Icon name="play" size={12} /> 继续
                  </button>
                  <button className="btn ghost" onClick={() => location.hash = "saves-branches"} title="查看分支树">
                    <Icon name="branch" size={12} /> 分支
                  </button>
                  {/* task 126: wrap button + popup in a positioned container so absolute 不再跑屏幕外 */}
                  <span style={{position: "relative", marginLeft: "auto", display: "inline-flex"}}>
                    <button className="iconbtn" data-tip="重命名 / 导出 / 删除"
                      onClick={(e) => { e.stopPropagation(); setMenuOpen(menuOpen === s.id ? null : s.id); }}>
                      <Icon name="more" size={14} />
                    </button>
                    {menuOpen === s.id && (
                      <div className="pl-pop" style={{position: "absolute", right: 0, top: "calc(100% + 4px)", zIndex: 30}}>
                        {!s.current && (
                          <button className="pl-pop-item" onClick={() => { onActivate(s); setMenuOpen(null); }}>设为当前</button>
                        )}
                        <button className="pl-pop-item" onClick={async () => {
                          const t = prompt("新名称", s.title);
                          if (!t || t === s.title) return setMenuOpen(null);
                          try {
                            await window.api.saves.rename(s.id, t);
                            window.__apiToast?.("已重命名", { kind: "ok", duration: 1500 });
                            reload();
                          } catch (e) {
                            window.__apiToast?.("重命名失败", { kind: "danger", detail: e?.message });
                          }
                          setMenuOpen(null);
                        }}>重命名</button>
                        <button className="pl-pop-item" onClick={() => {
                          window.open(window.api.saves.exportUrl(s.id), "_blank");
                          setMenuOpen(null);
                        }}>导出</button>
                        <button className="pl-pop-item danger" onClick={() => { onDeleteSave(s); setMenuOpen(null); }}>删除</button>
                      </div>
                    )}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </section>
      <NewGameModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onConfirm={onCreate}
      />
      {/* task 127: 平台 ConfirmModal 取代浏览器 confirm() */}
      {window.ConfirmModal && (
        <window.ConfirmModal
          open={!!deleteTarget}
          title="删除存档"
          body={deleteTarget ? `确定删除存档「${deleteTarget.title}」？此操作不可撤销 (但磁盘 commit 文件仍可恢复)。` : ""}
          danger
          confirmLabel="确认删除"
          onClose={() => setDeleteTarget(null)}
          onConfirm={confirmDeleteSave}
        />
      )}
    </div>
  );
}

/* ---------------------------- BRANCHES ------------------------- */
const BRANCH_DATA = {
  nodes: [
    { id: 1, x: 80, y: 280, summary: "开场 · 渡海前夜", role: "root", current: false, branch: 0 },
    { id: 2, x: 240, y: 280, summary: "登船后向船工打听", role: "round", branch: 0 },
    { id: 3, x: 400, y: 240, summary: "申时落岸 · 雾未散", role: "round", branch: 0 },
    { id: 4, x: 400, y: 360, summary: "选择借宿渔家旅店", role: "round", branch: 1 },
    { id: 5, x: 560, y: 240, summary: "码头听闻浮尸三具", role: "round", branch: 0 },
    { id: 6, x: 560, y: 360, summary: "旅店遇沈知微", role: "round", branch: 1 },
    { id: 7, x: 720, y: 200, summary: "向税吏隐藏身份", role: "round", branch: 0, current: true, lastExit: true },
    { id: 8, x: 720, y: 320, summary: "暴露残页 · 被巡检盘问", role: "round", branch: 2, deleted: true },
    { id: 9, x: 720, y: 420, summary: "天黑前赶往灯塔", role: "round", branch: 1 },
    { id: 10, x: 880, y: 200, summary: "灯塔下等沈知微", role: "round", branch: 0 },
    { id: 11, x: 880, y: 420, summary: "找到守人女儿阿衡", role: "round", branch: 1 },
  ],
  edges: [
    { from: 1, to: 2, branch: 0 }, { from: 2, to: 3, branch: 0 }, { from: 2, to: 4, branch: 1 },
    { from: 3, to: 5, branch: 0 }, { from: 4, to: 6, branch: 1 },
    { from: 5, to: 7, branch: 0 }, { from: 5, to: 8, branch: 2, deleted: true },
    { from: 6, to: 9, branch: 1 },
    { from: 7, to: 10, branch: 0 }, { from: 9, to: 11, branch: 1 },
  ],
};

const BRANCH_LABELS = {
  0: { name: "主线", desc: "向税吏隐藏身份，灯塔会面" },
  1: { name: "旅店线", desc: "借宿渔家，最早遇到阿衡" },
  2: { name: "暴露线", desc: "残页被巡检发现（已删除）", deleted: true },
};

function BranchesPage() {
  // 用户要求"git ui 在 vscode 底部终端里的那个" — 改用 BranchGraph 组件 (VSCode Git Graph 风格)。
  // 旧版是自由拖拽 SVG (140×40 矩形 + 贝塞尔曲线),信息密度低、交互复杂、不像 git tool。
  // 新版用 swimlane 算法:每行一个 commit,左侧固定 column 分支线,右侧 message + ref pills + 操作。
  //
  // 后端不变(branch_commits + branch_refs);组件抽到 frontend/src/branch-graph.jsx,
  // 游戏内右侧 BranchTreeRail 和这里共用,只换 variant prop (compact / full)。

  const [saves, setSaves] = useStatePL([]);
  const [selectedSave, setSelectedSave] = useStatePL(undefined);
  const [savesLoaded, setSavesLoaded] = useStatePL(false);
  const [treePayload, setTreePayload] = useStatePL(null);  // {nodes, refs, active_commit_id}
  const [treeLoading, setTreeLoading] = useStatePL(false);
  const [treeError, setTreeError] = useStatePL("");
  const [selectedNodeId, setSelectedNodeId] = useStatePL(null);
  const [deleteTarget, setDeleteTarget] = useStatePL(null);

  // 1) 拉用户的 saves 列表
  useEffectPL(() => {
    (async () => {
      try {
        const r = await window.api.saves.list();
        const list = Array.isArray(r) ? r : (r?.items || r?.saves || []);
        const normalized = list.map(window.__normalizeSave || ((x) => x));
        setSaves(normalized);
        if (normalized.length) {
          setSelectedSave(prev => (
            prev && normalized.some(s => s.id === prev) ? prev : normalized[0].id
          ));
        } else {
          setSelectedSave(undefined);
        }
      } catch (_) {
        setSaves([]);
        setSelectedSave(undefined);
      } finally {
        setSavesLoaded(true);
      }
    })();
  }, []);

  // 2) selectedSave 变 → 拉该存档的 branch tree
  const reloadTree = async () => {
    if (!selectedSave) { setTreePayload(null); return; }
    setTreeLoading(true); setTreeError("");
    try {
      const r = await window.api.branches.list(selectedSave);
      setTreePayload(r ? {
        nodes: r.nodes || r.commits || [],
        refs: r.refs || [],
        active_commit_id: r.active_commit_id || r.active_branch_node_id || null,
      } : null);
    } catch (e) {
      setTreeError(e?.message || "加载失败");
      setTreePayload(null);
    } finally {
      setTreeLoading(false);
    }
  };
  useEffectPL(() => { reloadTree(); }, [selectedSave]);

  const onActivate = async (commitId) => {
    try {
      await window.api.branches.activate({ save_id: selectedSave, commit_id: commitId, node_id: commitId });
      window.__apiToast?.("已切到该分支", { kind: "ok" });
      reloadTree();
    } catch (e) {
      window.__apiToast?.("切换失败", { kind: "danger", detail: e?.message });
    }
  };

  const onContinue = (commitId) => {
    window.__openContinue?.(saves.find(s => s.id === selectedSave), commitId);
  };

  const onDeleteRequest = (commitId) => {
    const node = (treePayload?.nodes || []).find(n => (n.commit_id ?? n.id) === commitId);
    if (node) setDeleteTarget(node);
  };

  const onDeleteConfirmed = async () => {
    if (!deleteTarget) return;
    const cid = deleteTarget.commit_id ?? deleteTarget.id;
    try {
      await window.api.branches.delete({ save_id: selectedSave, node_id: cid, commit_id: cid });
      window.__apiToast?.("已删除子树", { kind: "ok" });
      setDeleteTarget(null);
      reloadTree();
    } catch (e) {
      window.__apiToast?.("删除失败", { kind: "danger", detail: e?.message });
    }
  };

  // 空态:用户没有任何存档
  if (savesLoaded && saves.length === 0) {
    return (
      <div className="pl-stack">
        <section className="pl-sec" data-cap-anchor="saves.branches">
          <div className="pl-sec-head">
            <h2>分支图 <span className="muted-2">暂无存档</span></h2>
          </div>
          <div className="pl-empty" style={{padding: "32px 24px", textAlign: "center", color: "var(--muted)"}}>
            <div style={{marginBottom: 12, fontFamily: "var(--font-serif)", fontSize: 15, color: "var(--text)"}}>
              你还没有任何存档
            </div>
            <div style={{marginBottom: 16, fontSize: 13}}>
              先去「剧本」页选一本剧本开始新游戏,存档建立后才会出现分支图。
            </div>
            <div style={{display: "inline-flex", gap: 8}}>
              <button className="btn primary" onClick={() => window.location.hash = "saves-scripts"}>
                <Icon name="bookmark" size={12} /> 去选剧本
              </button>
              <button className="btn ghost" onClick={() => window.location.hash = "saves-list"}>
                <Icon name="list" size={12} /> 存档列表
              </button>
            </div>
          </div>
        </section>
      </div>
    );
  }

  const nodeCount = (treePayload?.nodes || []).length;
  const refCount = (treePayload?.refs || []).length;

  return (
    <div className="pl-stack">
      <section className="pl-sec" data-cap-anchor="saves.branches">
        <div className="pl-sec-head">
          <h2>
            分支图{" "}
            <span className="muted-2">
              {nodeCount} commits · {refCount} refs · 一个存档一个 git 系统
            </span>
          </h2>
          <div className="pl-sec-tools">
            <select value={selectedSave || ""} onChange={(e) => setSelectedSave(Number(e.target.value))}
              style={{height: 28, fontSize: 12, padding: "0 10px"}}>
              {saves.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}
            </select>
            <button className="btn ghost" onClick={reloadTree}><Icon name="refresh" size={12} /> 刷新</button>
            <button className="btn primary"
              disabled={!selectedSave}
              onClick={() => window.__openContinue?.(saves.find(s => s.id === selectedSave))}>
              <Icon name="play" size={12} /> 进入当前分支
            </button>
          </div>
        </div>
        <div style={{padding: "8px 0"}}>
          {treeLoading && (
            <div className="muted-2" style={{padding: "16px", fontSize: 12.5}}>加载中…</div>
          )}
          {!treeLoading && treeError && (
            <div className="muted-2" style={{padding: "16px", fontSize: 12.5, color: "var(--danger)"}}>加载失败：{treeError}</div>
          )}
          {!treeLoading && !treeError && treePayload && (
            <BranchGraph
              data={treePayload}
              variant="full"
              selectedId={selectedNodeId}
              onSelect={setSelectedNodeId}
              onActivate={onActivate}
              onContinue={onContinue}
              onDelete={onDeleteRequest}
            />
          )}
        </div>
        <div className="muted-2" style={{padding: "6px 4px 0", fontSize: 11, fontFamily: "var(--font-mono)"}}>
          列(swimlane) = 分支轨道  ·  ○ = commit dot (圆环 = HEAD/active)  ·  虚线 = 已删除子树
        </div>
      </section>
      <ConfirmModal
        open={!!deleteTarget}
        title={`删除 commit #${deleteTarget?.commit_id ?? deleteTarget?.id} 及其子树？`}
        body={
          <>
            将删除 <strong>{deleteTarget?.summary || deleteTarget?.message || `节点 #${deleteTarget?.commit_id ?? deleteTarget?.id}`}</strong>
            {" "}及以它为起点的<strong>所有下游分支</strong>。
            此操作在本存档中不可恢复。
            <div style={{marginTop: 8, fontSize: 12, color: "var(--muted)"}}>POST /api/branches/delete</div>
          </>
        }
        danger confirmLabel="删除整棵子树"
        onClose={() => setDeleteTarget(null)}
        onConfirm={onDeleteConfirmed}
      />
    </div>
  );
}

/* ---------------------------- CONTINUE PICKER ------------------ */
function ContinuePicker({ open, save, focusedNodeId, onClose }) {
  // task 45：原来 allSaves = window.MOCK_PLATFORM.saves —— 登录用户看不到自己的真存档
  // （只看到 mock 的 4 条假 save id=11/12/13/14）。改用 /api/saves 实时拉真存档。
  // 匿名访客（designer preview）才回退到 MOCK_PLATFORM。
  const [allSaves, setAllSaves] = useStatePL([]);
  const [savesLoading, setSavesLoading] = useStatePL(false);
  const [branchTree, setBranchTree] = useStatePL(null);  // task 45：真实分支树 / null=未加载
  const [branchLoading, setBranchLoading] = useStatePL(false);
  const [step, setStep] = useStatePL("save"); // save | branch | new
  const [pickedSave, setPickedSave] = useStatePL(null);
  const [newOpen, setNewOpen] = useStatePL(false);

  // 拉真实 saves
  React.useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setSavesLoading(true);
    (async () => {
      let list = [];
      try {
        const r = await window.api.saves.list();
        list = Array.isArray(r) ? r : (r?.items || r?.saves || []);
      } catch (_) {
        // 匿名访客 / 401：回退到 mock 保留 designer offline preview
        list = (window.RPG_AUTH && window.RPG_AUTH.authed) ? [] : (window.MOCK_PLATFORM?.saves || []);
      }
      if (cancelled) return;
      setAllSaves(list);
      setSavesLoading(false);
      if (save) { setPickedSave(save); setStep("branch"); }
      else if (list.length) { setPickedSave(list[0]); setStep("save"); }
      else { setPickedSave(null); setStep("save"); }
    })();
    return () => { cancelled = true; };
  }, [open, save]);

  // 拉真实 branch tree
  React.useEffect(() => {
    if (!open || !pickedSave?.id) { setBranchTree(null); return; }
    let cancelled = false;
    setBranchLoading(true);
    (async () => {
      let tree = null;
      try {
        const r = await window.api.branches.list(pickedSave.id);
        // 后端真相源是 user_runtime.active_commit_id (改后的 tree() 已经透传)
        const activeId = r?.active_commit_id || r?.active_branch_node_id;
        const nodes = (r?.nodes || r?.commits || []).map((n, i) => {
          // ref_names 是后端 tree() 给的真实分支指针名 ["refs/heads/main", "refs/runtime/user-6"]
          const refNames = Array.isArray(n.ref_names) ? n.ref_names : [];
          // 截短显示 (refs/heads/main → main)
          const shortRefs = refNames.map(rn => {
            const s = String(rn);
            return s.startsWith("refs/") ? s.split("/").slice(2).join("/") : s;
          });
          // 主分支判定:有 main / master ref 算主线;否则用 ref 名
          const isMain = shortRefs.includes("main") || shortRefs.includes("master");
          const branchLabel = shortRefs.length
            ? (isMain ? "main" : shortRefs[0])
            : "(无 ref)";
          return {
            id: n.id,
            summary: n.summary || n.message || n.content_preview || `节点 #${n.id}`,
            turn_index: n.turn_index ?? i,
            kind: n.kind || "round",
            ref_names: refNames,    // 完整 ref 名(用于 hover tooltip)
            short_refs: shortRefs,  // 截短的 ref 名 list
            branch_label: branchLabel,  // 显示的主标签
            current: n.id === activeId,
            lastExit: n.id === activeId,
          };
        });
        tree = { nodes, edges: [] };
      } catch (_) { tree = { nodes: [], edges: [] }; }
      if (cancelled) return;
      setBranchTree(tree);
      setBranchLoading(false);
    })();
    return () => { cancelled = true; };
  }, [open, pickedSave?.id]);

  // task 45：BRANCH_DATA 已退役 —— 真实树为空就显示空态（"新账号还没存档/还没存任何分支节点"），
  // 不再回退到 mock 11 节点
  const nodes = branchTree?.nodes || [];
  const edges = branchTree?.edges || [];
  const lastExit = nodes.find(n => n.lastExit) || nodes[0];
  const childCount = (nodeId) => edges.filter(e => e.from === nodeId).length;
  const initialPick = focusedNodeId || lastExit?.id;
  const [pickedNode, setPickedNode] = useStatePL(initialPick);
  React.useEffect(() => { if (open) setPickedNode(initialPick); }, [open, initialPick]);

  if (!open) return null;

  const picked = nodes.find(n => n.id === pickedNode);
  const isFork = picked && childCount(picked.id) > 0;
  // task 30 + 关键 bug 修复:进入 Game Console 之前必须把 runtime 切到正确的
  // **commit**(不只是 save)。
  //
  // 旧版只调 saves.activate(targetId) — 这只切 save 级 active,后端会按
  // game_saves.active_commit_id 加载该 save 当前活跃的 commit,**完全忽略用户
  // 选的 pickedNode**。结果:
  //   · 用户在第 2 步选了 #13"扎兹巴鲁姆..."节点 (柏林剧情中段),
  //   · saves.activate 把 save 级切到"当前自动存档",但 active_commit_id 还是
  //     #15 末尾(或别的 commit),
  //   · 进 Game Console 看到的是末尾 commit 的 state — 可能是混乱的旧 runtime
  //     (如 ash_mine 内容)而非用户选的 #13 柏林剧情。
  //
  // 修复:如果用户在树里选了具体节点,改调 branches.activate({node_id}) —
  // 这会同时:
  //   1. _set_save_active 写 game_saves.active_commit_id = pickedNode
  //   2. _write_checkout 写 runtime_checkouts
  //   3. runtime.activate_state_snapshot 把 user_runtime 切到 pickedNode +
  //      该 commit 的 state_snapshot
  // 这才是 git "checkout commit_id" 的语义。
  // 没选具体节点(只切了 save 没选 commit)→ fallback 到 saves.activate。
  const confirm = async () => {
    const targetSaveId = pickedSave?.id;
    if (!targetSaveId) {
      // 完全没存档信息,不要带着旧 runtime 进 Game Console
      window.__apiToast?.("没选目标存档", { kind: "danger", duration: 2400 });
      return;
    }
    try {
      if (pickedNode != null && pickedNode !== "") {
        // 用户选了具体 commit:走 commit 级 activate,把 runtime 切到该节点 state
        const r = await window.api.branches.activate({
          node_id: pickedNode,
          commit_id: pickedNode,
        });
        if (r && r.ok === false) {
          throw new Error(r.error || r.detail || "commit 级激活失败");
        }
      } else {
        // 只选了 save 没选节点:fallback save 级 activate (切到该 save 的当前 active commit)
        await window.api.saves.activate(targetSaveId);
      }
    } catch (e) {
      window.__apiToast?.("切换分支失败", { kind: "danger", detail: e?.message, duration: 3000 });
      return;  // 不要带着旧 runtime 进去
    }
    location.href = "Game Console.html";
  };

  // STEP 1: Save selection
  if (step === "save") {
    return (
      <div className="pl-modal-backdrop" onClick={onClose}>
        <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(620px, 100%)"}}>
          <header className="pl-modal-head">
            <div>
              <div className="pl-modal-eyebrow">继续游戏 · 第 1 / 2 步</div>
              <h2 className="pl-modal-title">选择一个存档</h2>
            </div>
            <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
          </header>
          <div className="pl-save-picker">
            {savesLoading && (
              <div className="muted-2" style={{padding: "20px 12px", textAlign: "center", fontSize: 13}}>
                正在加载真实存档列表…
              </div>
            )}
            {!savesLoading && allSaves.length === 0 && (
              <div className="muted-2" style={{padding: "20px 12px", textAlign: "center", fontSize: 13, lineHeight: 1.7}}>
                你还没有任何真实存档。<br />
                点击下面『开始新游戏』基于导入剧本（或默认剧本）创建。
              </div>
            )}
            {allSaves.map(s => (
              <button key={s.id}
                className={`pl-save-pick-row ${pickedSave?.id === s.id ? "active" : ""}`}
                onClick={() => setPickedSave(s)}
                onDoubleClick={() => { setPickedSave(s); setStep("branch"); }}>
                <div className={`pl-radio ${pickedSave?.id === s.id ? "on" : ""}`} />
                <div className="pl-save-pick-body">
                  <div className="pl-save-pick-title">
                    {s.title}
                    {s.current && <span className="pill accent" style={{marginLeft: 8, fontSize: 10.5}}><span className="dot accent pulse" /> 在玩</span>}
                  </div>
                  <div className="pl-save-pick-meta muted-2 mono">
                    {s.branch_count} 节点 · {s.updated_at}
                  </div>
                </div>
              </button>
            ))}
            <button className="pl-save-pick-row pl-save-pick-new"
              onClick={() => setNewOpen(true)}>
              <div className="pl-save-pick-mark"><Icon name="plus" size={14} /></div>
              <div className="pl-save-pick-body">
                <div className="pl-save-pick-title">开始新游戏</div>
                <div className="pl-save-pick-meta muted-2">基于剧本创建一个新存档，从开场开始</div>
              </div>
              <Icon name="chevron_right" size={14} style={{color: "var(--muted-2)"}} />
            </button>
          </div>
          <footer className="pl-modal-foot">
            <span className="muted-2" style={{fontSize: 11.5}}>
              <Icon name="info" size={11} /> 双击存档直接进入分支选择
            </span>
            <div style={{display: "flex", gap: 8}}>
              <button className="btn ghost" onClick={onClose}>取消</button>
              <button className="btn primary" onClick={() => setStep("branch")} disabled={!pickedSave}>
                选择分支 <Icon name="arrow_right" size={12} />
              </button>
            </div>
          </footer>
          <NewGameModal
            open={newOpen}
            onClose={() => setNewOpen(false)}
            // Codex P0-1 修复:之前 onConfirm 把 payload 丢了 → 用户填的剧本 / 角色卡
            // 信息没生效,关闭 modal 后直接 confirm() 激活旧 save,看着像"开始新游戏"
            // 实际是继续当前存档。现在走统一原子流:saves.create → activate → 进游戏。
            onConfirm={async (payload) => {
              await window.__createAndEnterSave(payload);
              // 成功会跳页 (location.href),不会执行到下面
            }}
          />
        </div>
      </div>
    );
  }

  // STEP 2: Branch / node selection
  return (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={(e) => e.stopPropagation()} style={{width: "min(640px, 100%)"}}>
        <header className="pl-modal-head">
          <div>
            <div className="pl-modal-eyebrow">
              <button className="pl-back-btn" onClick={() => setStep("save")} data-tip="返回存档选择">
                <Icon name="chevron_left" size={11} /> 第 2 / 2 步
              </button>
            </div>
            <h2 className="pl-modal-title">{pickedSave?.title || "选择继续节点"}</h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>

        {/* task 45：真分支树。loading 时显示加载提示；空时显示空态（新账号还没存档的常见情况） */}
        {branchLoading && (
          <div className="muted-2" style={{padding: "20px 24px", textAlign: "center", fontSize: 13}}>
            正在加载存档分支树...
          </div>
        )}
        {!branchLoading && nodes.length === 0 && (
          <div className="muted-2" style={{padding: "32px 24px", textAlign: "center", fontSize: 13, lineHeight: 1.7}}>
            该存档还没有任何分支节点。<br />
            <span className="muted">点击下方『继续游戏』直接进入 root 开局，先玩起来就会自动生成节点。</span>
          </div>
        )}
        {!branchLoading && lastExit && (
          <button className={`pl-modal-hero ${pickedNode === lastExit.id ? "active" : ""}`}
                  onClick={() => setPickedNode(lastExit.id)} style={{textAlign: "left"}}>
            <div className="pl-modal-hero-mark">
              <span className="dot accent pulse" />
              <span className="mono">上次退出</span>
            </div>
            <div className="pl-modal-hero-body">
              <div className="pl-modal-hero-title">分支 {lastExit.branch} · {BRANCH_LABELS[lastExit.branch]?.name || "默认线"}</div>
              <div className="pl-modal-hero-summary serif">#{String(lastExit.id).padStart(2,"0")} · {lastExit.summary}</div>
              <div className="pl-modal-hero-meta muted-2 mono">turn {lastExit.turn_index ?? "?"} · {lastExit.kind || "round"}</div>
            </div>
            <div className="pl-modal-hero-radio">
              <div className={`pl-radio ${pickedNode === lastExit.id ? "on" : ""}`} />
            </div>
          </button>
        )}

        {!branchLoading && nodes.length > 1 && (
          <div className="pl-modal-section-label">或从其它节点开始 <span className="muted-2" style={{marginLeft: 6, fontSize: 11, textTransform: "none", letterSpacing: 0}}>从中段节点继续将自动新建分叉</span></div>
        )}

        <div className="pl-modal-branches">
          {nodes.filter(n => n.id !== lastExit?.id && !n.deleted).map(n => {
            const hasChildren = childCount(n.id) > 0;
            return (
              <button key={n.id}
                className={`pl-modal-branch ${pickedNode === n.id ? "active" : ""}`}
                onClick={() => setPickedNode(n.id)}>
                <div className={`pl-radio ${pickedNode === n.id ? "on" : ""}`} />
                <div className="pl-modal-branch-body">
                  <div className="pl-modal-branch-title">
                    #{String(n.id).padStart(2, "0")} · {n.summary}
                    {hasChildren && (
                      <span className="pill" data-tip="此节点已有后续，从这里继续会创建新分叉" style={{marginLeft: 8, fontSize: 10.5, color: "var(--warn)", borderColor: "rgba(212, 179, 102, 0.32)", background: "var(--warn-soft)"}}>
                        <Icon name="fork" size={9} /> 中段 · 将分叉
                      </span>
                    )}
                  </div>
                  <div className="pl-modal-branch-desc">
                    {n.short_refs && n.short_refs.length > 0 ? (
                      <>
                        {n.short_refs.map((rn, i) => (
                          <span key={i} className="pill" style={{
                            marginRight: 6, fontSize: 10.5,
                            color: rn === "main" || rn === "master" ? "var(--accent)" : "var(--info)",
                            borderColor: "var(--line)",
                          }} title={n.ref_names?.[i] || rn}>
                            {n.current ? "HEAD → " : ""}{rn}
                          </span>
                        ))}
                        {n.turn_index != null && (
                          <span className="muted-2 mono" style={{fontSize: 10.5}}>turn {n.turn_index}</span>
                        )}
                      </>
                    ) : (
                      <span className="muted-2 mono" style={{fontSize: 10.5}}>
                        {n.kind === "root" ? "存档起点" : `turn ${n.turn_index}`}
                      </span>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        <footer className="pl-modal-foot">
          <span className="muted-2" style={{fontSize: 11.5}}>
            <Icon name="info" size={11} />{" "}
            {isFork
              ? <>选中 <strong>#{String(picked.id).padStart(2,"0")}</strong> 不是当前分支末端，进入后会<strong style={{color: "var(--warn)"}}>新建分叉</strong>，原分支保留</>
              : <>选中 <strong>#{String(picked?.id || 0).padStart(2,"0")}</strong> 是当前分支末端，将<strong>继续</strong>同一分支</>}
          </span>
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost" onClick={() => setStep("save")}>上一步</button>
            <button className="btn primary" onClick={confirm} disabled={pickedNode == null}>
              <Icon name="play" size={12} /> {isFork ? "新建分叉并进入" : "继续游戏"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

/* =====================================================================
   NEW GAME WIZARD  (4-step)
   Step 1: 存档名称 + 剧本
   Step 2: 角色卡
   Step 3: 出生点 (按 phase 分组)
   Step 4: 初始身份 (LLM 推荐 + 自定义)
   ===================================================================== */

/* --- mock birthpoints (backend not yet available) --- */
const MOCK_BIRTHPOINTS_PHASES = [
  {
    phase_label: "初期穿越与火星线",
    chapter_min: 1, chapter_max: 299, chapter_count: 255,
    summary: "主角穿越初期，身份混乱，火星阴谋渐浮水面。",
    anchors: [
      { anchor_id: 1001, story_time_label: "初次睁眼", chapter_min: 1, chapter_max: 1, chapter_count: 1, sample_summary: "穿越者第一次在异世界睁开眼睛，一切尚未展开。" },
      { anchor_id: 1002, story_time_label: "宫廷初入", chapter_min: 8, chapter_max: 12, chapter_count: 5, sample_summary: "初次踏入皇宫，身份尚未明确，诸方势力窥探。" },
      { anchor_id: 1003, story_time_label: "火星密谋曝光", chapter_min: 40, chapter_max: 55, chapter_count: 16, sample_summary: "第一条涉及火星的线索浮现，主角卷入阴谋漩涡。" },
      { anchor_id: 1004, story_time_label: "第一次逃亡", chapter_min: 88, chapter_max: 92, chapter_count: 5, sample_summary: "形势急转直下，主角不得不出逃皇都。" },
      { anchor_id: 1005, story_time_label: "结盟关键人物", chapter_min: 150, chapter_max: 160, chapter_count: 11, sample_summary: "主角与关键盟友达成协议，局势暂时稳定。" },
    ],
  },
  {
    phase_label: "权力博弈中期",
    chapter_min: 300, chapter_max: 699, chapter_count: 400,
    summary: "各方势力明争暗斗，主角逐渐掌握更多筹码。",
    anchors: [
      { anchor_id: 2001, story_time_label: "摄政风波", chapter_min: 302, chapter_max: 310, chapter_count: 9, sample_summary: "摄政王势力与皇族正面交锋，朝堂动荡。" },
      { anchor_id: 2002, story_time_label: "秘密组织现身", chapter_min: 380, chapter_max: 395, chapter_count: 16, sample_summary: "隐藏在幕后的秘密组织第一次正式出手。" },
      { anchor_id: 2003, story_time_label: "关键背叛", chapter_min: 450, chapter_max: 455, chapter_count: 6, sample_summary: "信任之人倒戈，主角陷入孤立无援的困境。" },
      { anchor_id: 2004, story_time_label: "反击开始", chapter_min: 510, chapter_max: 530, chapter_count: 21, sample_summary: "主角积蓄力量完毕，全面反击开始。" },
      { anchor_id: 2005, story_time_label: "中期决战", chapter_min: 650, chapter_max: 660, chapter_count: 11, sample_summary: "双方兵力正面碰撞，局势出现根本性转变。" },
    ],
  },
  {
    phase_label: "星际危机爆发",
    chapter_min: 700, chapter_max: 1199, chapter_count: 500,
    summary: "星际殖民地局势失控，地球与火星矛盾激化。",
    anchors: [
      { anchor_id: 3001, story_time_label: "殖民地叛乱", chapter_min: 705, chapter_max: 715, chapter_count: 11, sample_summary: "火星第三殖民地宣告独立，引发连锁反应。" },
      { anchor_id: 3002, story_time_label: "舰队集结", chapter_min: 800, chapter_max: 820, chapter_count: 21, sample_summary: "地球联合政府派遣大规模舰队前往镇压。" },
      { anchor_id: 3003, story_time_label: "太空会战", chapter_min: 950, chapter_max: 975, chapter_count: 26, sample_summary: "双方舰队在火星轨道外展开史诗级对决。" },
      { anchor_id: 3004, story_time_label: "生化武器事件", chapter_min: 1050, chapter_max: 1060, chapter_count: 11, sample_summary: "神秘生化武器被引爆，局势急剧恶化。" },
      { anchor_id: 3005, story_time_label: "停火谈判", chapter_min: 1150, chapter_max: 1165, chapter_count: 16, sample_summary: "各方被迫坐上谈判桌，利益重新分配。" },
    ],
  },
  {
    phase_label: "终局与清算",
    chapter_min: 1200, chapter_max: 1599, chapter_count: 400,
    summary: "所有伏线汇聚，主角做出最终抉择，历史走向改变。",
    anchors: [
      { anchor_id: 4001, story_time_label: "真相揭露", chapter_min: 1205, chapter_max: 1215, chapter_count: 11, sample_summary: "穿越背后的真实原因终于浮出水面。" },
      { anchor_id: 4002, story_time_label: "大清算前夜", chapter_min: 1320, chapter_max: 1325, chapter_count: 6, sample_summary: "各方势力在最终对决前夕静待时机。" },
      { anchor_id: 4003, story_time_label: "最终决战", chapter_min: 1450, chapter_max: 1480, chapter_count: 31, sample_summary: "决定世界命运的终极战役全面爆发。" },
      { anchor_id: 4004, story_time_label: "新秩序建立", chapter_min: 1550, chapter_max: 1570, chapter_count: 21, sample_summary: "旧世界崩塌，新的权力格局逐渐成形。" },
      { anchor_id: 4005, story_time_label: "尾声时间线", chapter_min: 1595, chapter_max: 1599, chapter_count: 5, sample_summary: "时间线最末端，所有人物迎来各自结局。" },
    ],
  },
  {
    phase_label: "番外与支线",
    chapter_min: 1600, chapter_max: 1699, chapter_count: 100,
    summary: "脱离主线的独立故事，探索配角与平行世界。",
    anchors: [
      { anchor_id: 5001, story_time_label: "配角外传·序", chapter_min: 1601, chapter_max: 1605, chapter_count: 5, sample_summary: "从主要配角视角重述关键事件。" },
      { anchor_id: 5002, story_time_label: "平行宇宙节点", chapter_min: 1630, chapter_max: 1640, chapter_count: 11, sample_summary: "如果关键选择不同，历史将走向何方？" },
      { anchor_id: 5003, story_time_label: "后日谈·五年后", chapter_min: 1680, chapter_max: 1690, chapter_count: 11, sample_summary: "五年后的世界，人们如何与历史和解。" },
    ],
  },
];

/* --- Wizard step progress bar --- */
function WizardProgress({ step, total }) {
  return (
    <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
      {Array.from({ length: total }, (_, i) => (
        <div
          key={i}
          style={{
            height: 3,
            flex: 1,
            borderRadius: 99,
            background: i < step ? "var(--accent)" : i === step ? "var(--accent-edge)" : "var(--line)",
            transition: "background 0.2s",
          }}
        />
      ))}
      <span className="muted-2" style={{ fontSize: 11, whiteSpace: "nowrap", marginLeft: 4 }}>
        {step + 1} / {total}
      </span>
    </div>
  );
}

/* --- Inline error bar --- */
function InlineErr({ msg }) {
  if (!msg) return null;
  return (
    <div role="alert" style={{
      color: "var(--danger)", padding: "8px 10px",
      border: "1px solid var(--danger-soft)", borderRadius: 6,
      fontSize: 12.5, background: "var(--danger-soft)",
    }}>
      {msg}
    </div>
  );
}

/* ============================================================
   Step 3: 出生点选择
   ============================================================ */
function BirthpointStep({ scriptId, birthpoint, setBirthpoint }) {
  const [phases, setPhases] = React.useState([]);
  const [loadingBP, setLoadingBP] = React.useState(true);
  const [bpErr, setBpErr] = React.useState("");
  const [openPhase, setOpenPhase] = React.useState(null); // accordion state

  React.useEffect(() => {
    if (!scriptId) return;
    setLoadingBP(true); setBpErr("");
    (async () => {
      try {
        const r = await fetch(
          `${window.__API_BASE || ""}/api/scripts/${scriptId}/birthpoints`,
          { credentials: "include", headers: { Accept: "application/json" } }
        );
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        if (data && Array.isArray(data.phases) && data.phases.length > 0) {
          setPhases(data.phases);
          // auto-open first phase
          setOpenPhase(data.phases[0].phase_label);
        } else {
          // backend not ready yet — use mock
          setPhases(MOCK_BIRTHPOINTS_PHASES);
          setOpenPhase(MOCK_BIRTHPOINTS_PHASES[0].phase_label);
        }
      } catch (_) {
        // backend not ready — use mock, no error shown (silent fallback)
        setPhases(MOCK_BIRTHPOINTS_PHASES);
        setOpenPhase(MOCK_BIRTHPOINTS_PHASES[0].phase_label);
      } finally {
        setLoadingBP(false);
      }
    })();
  }, [scriptId]);

  if (loadingBP) {
    return (
      <div className="muted" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, padding: "16px 0" }}>
        <Icon name="spinner" size={13} className="spin" /> 正在加载出生点…
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 6 }}>
      <InlineErr msg={bpErr} />
      {phases.map(phase => {
        const isOpen = openPhase === phase.phase_label;
        return (
          <div key={phase.phase_label} style={{
            border: "1px solid var(--line-soft)",
            borderRadius: "var(--r-3, 8px)",
            overflow: "hidden",
          }}>
            {/* accordion header */}
            <button
              onClick={() => setOpenPhase(isOpen ? null : phase.phase_label)}
              style={{
                width: "100%", textAlign: "left",
                display: "flex", alignItems: "center", justifyContent: "space-between",
                gap: 10, padding: "9px 14px",
                background: isOpen ? "var(--panel-2)" : "transparent",
                border: "none", cursor: "pointer",
                borderBottom: isOpen ? "1px solid var(--line-soft)" : "none",
                transition: "background 0.15s",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
                <Icon
                  name={isOpen ? "chevron_down" : "chevron_right"}
                  size={11}
                  style={{ flexShrink: 0, color: "var(--muted)" }}
                />
                <span style={{ fontFamily: "var(--font-serif)", fontSize: 13.5, letterSpacing: "0.02em" }}>
                  {phase.phase_label}
                </span>
              </div>
              <span className="muted-2" style={{ fontSize: 11, whiteSpace: "nowrap", flexShrink: 0 }}>
                第 {phase.chapter_min}–{phase.chapter_max} 章 · {phase.chapter_count} 章
              </span>
            </button>

            {/* accordion body */}
            {isOpen && (
              <div style={{ display: "grid", gap: 4, padding: "8px 10px" }}>
                {phase.anchors.map(anchor => {
                  const isSelected = birthpoint && birthpoint.anchor_id === anchor.anchor_id;
                  return (
                    <label
                      key={anchor.anchor_id}
                      className={`pl-newgame-card${isSelected ? " active" : ""}`}
                      style={{ gridTemplateColumns: "14px 1fr auto", gap: 10, cursor: "pointer" }}
                    >
                      <input
                        type="radio"
                        checked={!!isSelected}
                        onChange={() => setBirthpoint({
                          phase_label: phase.phase_label,
                          anchor_id: anchor.anchor_id,
                          chapter_min: anchor.chapter_min,
                          chapter_max: anchor.chapter_max,
                          story_time_label: anchor.story_time_label,
                        })}
                      />
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontFamily: "var(--font-serif)", fontSize: 13, letterSpacing: "0.02em" }}>
                          {anchor.story_time_label}
                        </div>
                        {anchor.sample_summary && (
                          <div className="muted-2" style={{ fontSize: 11.5, marginTop: 2, lineHeight: 1.5 }}>
                            {anchor.sample_summary}
                          </div>
                        )}
                      </div>
                      <span className="muted-2" style={{ fontSize: 10.5, whiteSpace: "nowrap", alignSelf: "center" }}>
                        第 {anchor.chapter_min}{anchor.chapter_max !== anchor.chapter_min ? `–${anchor.chapter_max}` : ""} 章
                      </span>
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ============================================================
   Step 4: 初始身份
   ============================================================ */
function IdentityStep({ scriptId, birthpoint, pickedCard, allRoleOptions, identity, setIdentity }) {
  const [recs, setRecs] = React.useState([]);
  const [recsLoading, setRecsLoading] = React.useState(false);
  const [recsErr, setRecsErr] = React.useState("");
  const [customOpen, setCustomOpen] = React.useState(false);
  const [customName, setCustomName] = React.useState("");
  const [customRole, setCustomRole] = React.useState("");
  const [customBg, setCustomBg] = React.useState("");

  // fetch recommendations when step mounts (scriptId + birthpoint + character)
  React.useEffect(() => {
    setRecsLoading(true); setRecsErr("");
    const picked = allRoleOptions ? allRoleOptions.find(o => o.key === pickedCard) : null;
    const args = {
      script_id: scriptId ? parseInt(scriptId, 10) : undefined,
      birthpoint_phase: birthpoint ? birthpoint.phase_label : undefined,
      birthpoint_label: birthpoint ? birthpoint.story_time_label : undefined,
      character_card_id: picked ? (picked.id || null) : null,
      character_card_kind: picked ? picked.kind : null,
      n: 4,
    };
    (async () => {
      try {
        // task 123: 改调专用 endpoint (替代之前的 /api/console_assistant/tool 404)
        const r = await fetch(
          `${window.__API_BASE || ""}/api/scripts/${args.script_id}/recommend-identity`,
          {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({
              birthpoint_phase: args.birthpoint_phase || "",
              birthpoint_label: args.birthpoint_label || "",
              character_card_id: args.character_card_id,
              character_card_kind: args.character_card_kind,
              n: args.n || 4,
            }),
          }
        );
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        if (data && Array.isArray(data.recommendations) && data.recommendations.length > 0) {
          setRecs(data.recommendations);
        } else {
          setRecsErr((data && data.error) || "后端未返回推荐,请使用自定义身份。");
          setRecs([]);
        }
      } catch (e) {
        setRecsErr("推荐加载失败：" + (e.message || "网络错误") + "。请使用下方自定义身份。");
        setRecs([]);
      } finally {
        setRecsLoading(false);
      }
    })();
  }, []); // only on mount of this step

  const applyCustom = () => {
    if (!customName.trim()) return;
    setIdentity({ name: customName.trim(), role: customRole.trim(), background: customBg.trim(), _custom: true });
  };

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {/* recommendation area */}
      {recsLoading && (
        <div className="muted" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, padding: "12px 0" }}>
          <Icon name="spinner" size={13} className="spin" /> 正在生成身份推荐…
        </div>
      )}
      <InlineErr msg={recsErr} />
      {!recsLoading && recs.length > 0 && (
        <div style={{ display: "grid", gap: 6 }}>
          <div className="pl-modal-section-label" style={{ paddingTop: 0 }}>AI 推荐身份</div>
          {recs.map((rec, i) => {
            const isSelected = identity && identity.name === rec.name && identity.role === rec.role;
            return (
              <button
                key={i}
                onClick={() => setIdentity({ name: rec.name, role: rec.role, background: rec.background || "" })}
                style={{
                  textAlign: "left",
                  padding: "10px 14px",
                  border: isSelected ? "1px solid var(--accent-edge)" : "1px solid var(--line-soft)",
                  borderRadius: "var(--r-3, 8px)",
                  background: isSelected ? "var(--accent-soft)" : "transparent",
                  cursor: "pointer",
                  display: "grid", gap: 3,
                  transition: "border-color 0.12s, background 0.12s",
                }}
                onMouseEnter={e => { if (!isSelected) e.currentTarget.style.borderColor = "var(--line)"; e.currentTarget.style.background = isSelected ? "var(--accent-soft)" : "var(--panel-2)"; }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = isSelected ? "var(--accent-edge)" : "var(--line-soft)"; e.currentTarget.style.background = isSelected ? "var(--accent-soft)" : "transparent"; }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <strong style={{ fontFamily: "var(--font-serif)", fontSize: 13.5, letterSpacing: "0.02em" }}>
                    {rec.name}
                  </strong>
                  {rec.role && (
                    <span className="pill" style={{ fontSize: 10.5 }}>{rec.role}</span>
                  )}
                  {isSelected && <span className="pill accent" style={{ fontSize: 10.5, marginLeft: "auto" }}>已选</span>}
                </div>
                {rec.background && (
                  <span className="muted-2" style={{ fontSize: 11.5, lineHeight: 1.5 }}>{rec.background}</span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* custom identity accordion */}
      <div style={{
        border: "1px solid var(--line-soft)",
        borderRadius: "var(--r-3, 8px)",
        overflow: "hidden",
      }}>
        <button
          onClick={() => setCustomOpen(v => !v)}
          style={{
            width: "100%", textAlign: "left",
            display: "flex", alignItems: "center", justifyContent: "space-between",
            gap: 10, padding: "9px 14px",
            background: customOpen ? "var(--panel-2)" : "transparent",
            border: "none", cursor: "pointer",
            borderBottom: customOpen ? "1px solid var(--line-soft)" : "none",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Icon name={customOpen ? "chevron_down" : "chevron_right"} size={11} style={{ color: "var(--muted)" }} />
            <span style={{ fontSize: 13 }}>自定义身份</span>
          </div>
          {identity && identity._custom && (
            <span className="pill accent" style={{ fontSize: 10.5 }}>已填写</span>
          )}
        </button>
        {customOpen && (
          <div style={{ padding: "10px 14px", display: "grid", gap: 10 }}>
            <div className="pl-import-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <div className="pl-field">
                <label>姓名 <span className="pl-field-req">*</span></label>
                <input
                  value={customName}
                  onChange={e => setCustomName(e.target.value)}
                  placeholder="角色名"
                />
              </div>
              <div className="pl-field">
                <label>身份 / 角色</label>
                <input
                  value={customRole}
                  onChange={e => setCustomRole(e.target.value)}
                  placeholder="例：穿越者公主"
                />
              </div>
              <div className="pl-field" style={{ gridColumn: "1 / -1" }}>
                <label>背景设定</label>
                <textarea
                  rows={2}
                  value={customBg}
                  onChange={e => setCustomBg(e.target.value)}
                  placeholder="简述角色背景（可留空）"
                />
              </div>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button
                className="btn"
                onClick={applyCustom}
                disabled={!customName.trim()}
              >
                <Icon name="check" size={12} /> 使用此身份
              </button>
            </div>
            {identity && identity._custom && (
              <div style={{
                padding: "8px 10px",
                background: "var(--accent-soft)", border: "1px solid var(--accent-edge)",
                borderRadius: 6, fontSize: 12.5,
              }}>
                已选：<strong>{identity.name}</strong>
                {identity.role ? ` · ${identity.role}` : ""}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ============================================================
   MAIN WIZARD COMPONENT
   ============================================================ */
function NewGameModal({ open, onClose, onConfirm, defaultScriptId = null }) {
  const TOTAL_STEPS = 5;

  // ── shared data ──────────────────────────────────────────────
  const [scripts, setScripts] = useStatePL([]);
  const [personas, setPersonas] = useStatePL([]);
  const [userCards, setUserCards] = useStatePL([]);
  const [loading, setLoading] = useStatePL(true);

  // ── wizard navigation ────────────────────────────────────────
  const [step, setStep] = useStatePL(0); // 0-indexed (0..3)

  // ── Step 1 state ─────────────────────────────────────────────
  const [title, setTitle] = useStatePL("");
  const [scriptId, setScriptId] = useStatePL("");

  // ── Step 2 state ─────────────────────────────────────────────
  const [roleMode, setRoleMode] = useStatePL("existing");
  const [pickedCard, setPickedCard] = useStatePL("");
  const [newCardName, setNewCardName] = useStatePL("");
  const [newCardRole, setNewCardRole] = useStatePL("");
  const [newCardBg, setNewCardBg] = useStatePL("");

  // ── Step 3 state ─────────────────────────────────────────────
  const [birthpoint, setBirthpoint] = useStatePL(null);

  // ── Step 4 state ─────────────────────────────────────────────
  const [identity, setIdentity] = useStatePL(null);

  // ── Step 5 state ─────────────────────────────────────────────
  const [storyIntent, setStoryIntent] = useStatePL("");

  // ── submit ───────────────────────────────────────────────────
  const [submitErr, setSubmitErr] = useStatePL("");
  const [submitting, setSubmitting] = useStatePL(false);

  // ── load data when opened ────────────────────────────────────
  React.useEffect(() => {
    if (!open) return;
    // reset to step 0 and clear transient state
    setStep(0);
    setTitle(""); setSubmitErr(""); setSubmitting(false); setLoading(true);
    setNewCardName(""); setNewCardRole(""); setNewCardBg("");
    setBirthpoint(null); setIdentity(null); setStoryIntent("");
    (async () => {
      let scList = [];
      try {
        const r = await window.api.scripts.list();
        scList = Array.isArray(r) ? r : (r?.items || r?.scripts || []);
      } catch (_) {}
      let psList = [];
      try {
        const p = await window.api.account.personas.list();
        psList = (p && (p.items || p.personas)) || [];
      } catch (_) {}
      let ucList = [];
      try {
        const c = await window.api.cards.myList();
        ucList = (c && (c.items || c.cards)) || [];
      } catch (_) {}
      setScripts(scList);
      setPersonas(psList);
      setUserCards(ucList);
      // task 108: script priority: 1) caller defaultScriptId 2) localStorage 3) first
      let pickId = "";
      if (defaultScriptId && scList.some(x => String(x.id) === String(defaultScriptId))) {
        pickId = String(defaultScriptId);
      } else {
        let remembered = "";
        try { remembered = localStorage.getItem("newgame.lastScriptId") || ""; } catch (_) {}
        if (remembered && scList.some(x => String(x.id) === remembered)) {
          pickId = remembered;
        } else {
          pickId = scList.length ? String(scList[0].id) : "";
        }
      }
      setScriptId(pickId);
      // default character
      if (psList.length) { setRoleMode("existing"); setPickedCard(`persona:${psList[0].id || psList[0].slug}`); }
      else if (ucList.length) { setRoleMode("existing"); setPickedCard(`user:${ucList[0].id || ucList[0].slug}`); }
      else { setRoleMode("new"); setPickedCard(""); }
      // task 127: 默认存档名只用剧本名 — 角色还没选,不要预设角色名
      // (之前用 psList[0].name 但用户还没"选",误导)
      try {
        const sc = scList.find(x => String(x.id) === pickId);
        const scTitle = (sc && (sc.title || "").replace(/^《|》$/g, "")) || "";
        if (scTitle) setTitle(`${scTitle} · 新档`);
        else setTitle("新游戏");
      } catch (_) { setTitle("新游戏"); }
      setLoading(false);
    })();
  }, [open]);

  if (!open) return null;

  const allRoleOptions = [
    ...personas.map(p => ({
      key: `persona:${p.id || p.slug}`, kind: "persona", id: p.id || null, slug: p.slug || "",
      name: p.name || "未命名", subtitle: p.role || "玩家身份", pinned: !!p.is_default,
    })),
    ...userCards.map(c => ({
      key: `user:${c.id || c.slug}`, kind: "user_card", id: c.id || null, slug: c.slug || "",
      name: c.name || "未命名", subtitle: c.identity || "用户角色卡", pinned: false,
    })),
  ];

  // per-step validity
  const step1Valid = title.trim() && scriptId;
  const step2Valid = (roleMode === "existing" && pickedCard) || (roleMode === "new" && newCardName.trim());
  const step3Valid = !!birthpoint;
  const step4Valid = !!identity;
  const step5Valid = true; // optional step, always valid

  const stepValid = [step1Valid, step2Valid, step3Valid, step4Valid, step5Valid];
  const canNext = !loading && stepValid[step];
  const canSubmit = !submitting && stepValid[0] && stepValid[1] && stepValid[2] && stepValid[3];

  const goNext = () => { if (canNext && step < TOTAL_STEPS - 1) setStep(s => s + 1); };
  const goPrev = () => { if (step > 0) setStep(s => s - 1); };

  const handleSubmit = async () => {
    setSubmitErr(""); setSubmitting(true);
    try {
      const picked = allRoleOptions.find(o => o.key === pickedCard);
      const payload = {
        title: title.trim(),
        script_id: parseInt(scriptId, 10),
        character_id: roleMode === "existing" && picked ? (picked.id || picked.slug || null) : null,
        character_kind: roleMode === "existing" && picked ? picked.kind : null,
        new_card: roleMode === "new" ? {
          name: newCardName.trim(),
          role: newCardRole.trim(),
          background: newCardBg.trim(),
        } : null,
        role_mode: roleMode,
        birthpoint: birthpoint || null,
        identity: identity ? { name: identity.name, role: identity.role, background: identity.background } : null,
        story_intent: storyIntent.trim() || null,
      };
      const res = onConfirm?.(payload);
      if (res && typeof res.then === "function") await res;
    } catch (e) {
      setSubmitErr((e && (e.message || (e.payload && (e.payload.error || e.payload.detail)))) || "创建失败");
    } finally {
      setSubmitting(false);
    }
  };

  /* ── step labels ── */
  const stepLabels = ["剧本", "角色", "出生点", "初始身份", "剧情期望"];

  const node = (
    <div className="pl-modal-backdrop" onClick={onClose}>
      <div className="pl-modal" onClick={e => e.stopPropagation()} style={{ width: "min(660px, 100%)" }}>
        {/* header */}
        <header className="pl-modal-head">
          <div style={{ minWidth: 0 }}>
            <div className="pl-modal-eyebrow">新游戏 · {stepLabels[step]}</div>
            <h2 className="pl-modal-title" style={{ fontSize: 18 }}>
              {step === 0 && "选择剧本与存档名称"}
              {step === 1 && "选择扮演角色"}
              {step === 2 && "选择出生点"}
              {step === 3 && "设定初始身份"}
              {step === 4 && "剧情走向期望（可跳过）"}
            </h2>
          </div>
          <button className="iconbtn" onClick={onClose} data-tip="关闭"><Icon name="close" size={14} /></button>
        </header>

        {/* progress bar */}
        <WizardProgress step={step} total={TOTAL_STEPS} />

        {/* step content — scrollable */}
        <div className="pl-modal-form">
          {/* ══ Step 0: Title + Script ══ */}
          {step === 0 && (
            <>
              {loading && (
                <div className="muted" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                  <Icon name="spinner" size={13} className="spin" /> 正在加载剧本 / 角色…
                </div>
              )}
              {!loading && scripts.length === 0 && (
                <div style={{ padding: "10px 12px", border: "1px solid var(--danger-soft)", borderRadius: 6, background: "var(--danger-soft)", color: "var(--danger)", fontSize: 13 }}>
                  你还没有任何剧本。先去 <a href="#scripts-import" onClick={onClose}>剧本 / 导入</a> 上传一部，然后再回来新建存档。
                </div>
              )}
              <div className="pl-import-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <div className="pl-field">
                  <label>存档名称 <span className="pl-field-req">*</span></label>
                  <input
                    value={title}
                    onChange={e => setTitle(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter" && canNext) goNext(); }}
                    autoFocus
                  />
                </div>
                <div className="pl-field">
                  <label>剧本 <span className="pl-field-req">*</span></label>
                  <select
                    value={scriptId}
                    onChange={e => {
                      const v = e.target.value;
                      setScriptId(v);
                      setBirthpoint(null); // reset birthpoint when script changes
                      try { if (v) localStorage.setItem("newgame.lastScriptId", v); } catch (_) {}
                    }}
                    disabled={!scripts.length}
                  >
                    {scripts.length === 0
                      ? <option value="">（先导入一部剧本）</option>
                      : scripts.map(sc => <option key={sc.id} value={String(sc.id)}>{sc.title}</option>)}
                  </select>
                </div>
              </div>
            </>
          )}

          {/* ══ Step 1: Character ══ */}
          {step === 1 && (
            <>
              <div className="pl-field">
                <label>扮演角色</label>
                <div className="seg" style={{ display: "flex" }}>
                  <button
                    className={roleMode === "existing" ? "active" : ""}
                    onClick={() => setRoleMode("existing")}
                    disabled={allRoleOptions.length === 0}
                  >
                    <Icon name="cards" size={12} /> 使用现有
                  </button>
                  <button
                    className={roleMode === "new" ? "active" : ""}
                    onClick={() => setRoleMode("new")}
                  >
                    <Icon name="plus" size={12} /> 新建角色卡
                  </button>
                </div>
                {allRoleOptions.length === 0 && (
                  <span className="pl-hint">你还没有玩家身份 / 用户角色卡，自动切到「新建角色卡」。</span>
                )}
              </div>
              {roleMode === "existing" && allRoleOptions.length > 0 && (
                <div className="pl-newgame-cards">
                  {allRoleOptions.map(c => (
                    <label key={c.key} className={`pl-newgame-card ${pickedCard === c.key ? "active" : ""}`}>
                      <input type="radio" checked={pickedCard === c.key} onChange={() => setPickedCard(c.key)} />
                      <div className="pl-newgame-card-avatar serif">{c.name.slice(0, 1)}</div>
                      <div className="pl-newgame-card-body">
                        <strong>{c.name}</strong>
                        <span className="muted-2" style={{ fontSize: 11.5 }}>
                          {c.subtitle} · {c.kind === "persona" ? "玩家身份" : "角色卡"}
                        </span>
                      </div>
                      {c.pinned && <span className="pill accent" style={{ fontSize: 10.5 }}><Icon name="pin" size={9} /> 默认</span>}
                    </label>
                  ))}
                  <a className="pl-newgame-card pl-newgame-card-link" href="#cards" onClick={onClose}>
                    <Icon name="folder" size={14} />
                    <span>前往角色卡库管理 →</span>
                  </a>
                </div>
              )}
              {roleMode === "new" && (
                <div className="pl-import-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
                  <div className="pl-field">
                    <label>姓名 <span className="pl-field-req">*</span></label>
                    <input value={newCardName} onChange={e => setNewCardName(e.target.value)} />
                  </div>
                  <div className="pl-field">
                    <label>身份 / 角色</label>
                    <input value={newCardRole} onChange={e => setNewCardRole(e.target.value)} />
                  </div>
                  <div className="pl-field" style={{ gridColumn: "1 / -1" }}>
                    <label>
                      设定
                      <span className="muted-2" style={{ textTransform: "none", letterSpacing: 0, marginLeft: 6, fontSize: 11 }}>
                        创建后会自动加入角色卡库
                      </span>
                    </label>
                    <textarea rows={2} value={newCardBg} onChange={e => setNewCardBg(e.target.value)} />
                  </div>
                </div>
              )}
            </>
          )}

          {/* ══ Step 2: Birthpoint ══ */}
          {step === 2 && (
            <BirthpointStep
              scriptId={scriptId}
              birthpoint={birthpoint}
              setBirthpoint={setBirthpoint}
            />
          )}

          {/* ══ Step 3: Identity ══ */}
          {step === 3 && (
            <IdentityStep
              scriptId={scriptId}
              birthpoint={birthpoint}
              pickedCard={pickedCard}
              allRoleOptions={allRoleOptions}
              identity={identity}
              setIdentity={(id) => setIdentity(id)}
            />
          )}

          {/* ══ Step 4: Story Intent ══ */}
          {step === 4 && (
            <div>
              <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
                告诉 GM 你希望的剧情走向。哪些设定是 NPC 知道的？哪些是你的秘密？哪些是你希望 GM 优先发展的方向？
                <br /><span className="muted-2" style={{ fontSize: 11 }}>此项可选，留空跳过。填写后存入存档，GM 每轮都能参考。</span>
              </p>
              <div className="pl-field">
                <label>剧情期望 / 秘密分配</label>
                <textarea
                  rows={6}
                  style={{ resize: "vertical" }}
                  placeholder={"例：\n- 林晓芸知道原著剧情，但绝口不提，NPC 不知道她是穿越者\n- 希望 GM 优先推进与林有德的相遇\n- 不希望出现过于血腥的场面"}
                  value={storyIntent}
                  onChange={e => setStoryIntent(e.target.value)}
                />
              </div>
            </div>
          )}

          {/* custom identity "use this" sets _custom flag */}
          {/* (IdentityStep handles it internally via applyCustom) */}

          {/* inline submission error */}
          {submitErr && <InlineErr msg={"创建失败：" + submitErr} />}
        </div>

        {/* footer */}
        <footer className="pl-modal-foot">
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {step === 0 && (
              <span className="muted-2" style={{ fontSize: 11 }}>
                <Icon name="info" size={10} /> 共 {TOTAL_STEPS} 步
              </span>
            )}
            {step === 2 && !birthpoint && (
              <span className="muted-2" style={{ fontSize: 11 }}>
                <Icon name="info" size={10} /> 请选择一个出生点
              </span>
            )}
            {step === 3 && !identity && (
              <span className="muted-2" style={{ fontSize: 11 }}>
                <Icon name="info" size={10} /> 请选择或填写初始身份
              </span>
            )}
            {step === 4 && (
              <span className="muted-2" style={{ fontSize: 11 }}>
                <Icon name="info" size={10} /> 可跳过，留空即可
              </span>
            )}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn ghost" onClick={step === 0 ? onClose : goPrev}>
              {step === 0 ? "取消" : <><Icon name="chevron_left" size={11} /> 上一步</>}
            </button>
            {step < TOTAL_STEPS - 1 ? (
              <button className="btn primary" onClick={goNext} disabled={!canNext}>
                下一步 <Icon name="chevron_right" size={11} />
              </button>
            ) : (
              <button className="btn primary" onClick={handleSubmit} disabled={!canSubmit}>
                <Icon name="check" size={12} /> {submitting ? "正在创建…" : "创建并进入"}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
  return ReactDOM.createPortal(node, document.body);
}

Object.assign(window, {
  SavesPage, SavesListView, BranchesPage, ContinuePicker, NewGameModal,
});
