// md-editor.jsx — VSCode 风 Markdown 编辑器(剧本知识资产内联编辑 + agent 直写)。
// 设计:docs/design/N_md_editor.md。三栏:左文件树 / 中多标签 CodeMirror / 右 agent。
// 本文件是页面壳 + 状态编排;CodeMirror 包在 components/CodeMirrorEditor.jsx(P3),
// 序列化在 lib/md-serialize.js(P2),agent 面板在 components/MdEditorAgent.jsx(P5)。
import React from 'react';
import './md-editor.css';
import { lsGet, lsSet } from '../lib/storage.js';
import CodeMirrorEditor from '../components/CodeMirrorEditor.jsx';
import MdEditorAgent from '../components/MdEditorAgent.jsx';
import { toMd, fromMd } from '../lib/md-serialize.js';

const { useState, useEffect, useCallback, useRef } = React;

// 文件树节点类型 → 中文标签 + 排序。
const NODE_GROUPS = [
  { kind: 'chapter',   label: '章节正文', icon: '§' },
  { kind: 'card',      label: '角色卡',   icon: '@' },
  { kind: 'worldbook', label: '世界书',   icon: '#' },
  { kind: 'anchor',    label: '时间线',   icon: '~' },
  { kind: 'canon',     label: 'Canon 实体', icon: '*' },
];

const api = () => (typeof window !== 'undefined' ? window.api : null);
const toast = (msg, opts) => { try { window.__apiToast?.(msg, opts); } catch (_) {} };

// ── 文件树:按组懒加载列表 ───────────────────────────────────────────────
function FileTree({ scriptId, openNode, activeKey, reloadKey }) {
  const [expanded, setExpanded] = useState(() => lsGet('mde.tree.expanded', 'chapter') || 'chapter');
  const [lists, setLists] = useState({});   // kind → {loading, error, items}
  const [filter, setFilter] = useState('');

  const loadGroup = useCallback(async (kind) => {
    if (!scriptId) return;
    setLists((s) => ({ ...s, [kind]: { ...(s[kind] || {}), loading: true } }));
    try {
      const items = await fetchGroupList(kind, scriptId);
      setLists((s) => ({ ...s, [kind]: { loading: false, items } }));
    } catch (e) {
      setLists((s) => ({ ...s, [kind]: { loading: false, error: e?.message || String(e), items: [] } }));
    }
  }, [scriptId]);

  // 切剧本 → 清缓存,重载当前展开组。
  useEffect(() => { setLists({}); if (scriptId && expanded) loadGroup(expanded); /* eslint-disable-next-line */ }, [scriptId]);
  // agent 写库后(reloadKey 变)→ 重载当前展开组(名称/数量可能变)。
  useEffect(() => { if (reloadKey && scriptId && expanded) loadGroup(expanded); /* eslint-disable-next-line */ }, [reloadKey]);

  const toggle = (kind) => {
    const next = expanded === kind ? '' : kind;
    setExpanded(next);
    lsSet('mde.tree.expanded', next);
    if (next && !lists[next]) loadGroup(next);
  };

  return (
    <div className="mde-tree">
      <div className="mde-tree-search">
        <input value={filter} placeholder="过滤…" onChange={(e) => setFilter(e.target.value)} />
      </div>
      <div className="mde-tree-body">
        {NODE_GROUPS.map((g) => {
          const st = lists[g.kind] || {};
          const isOpen = expanded === g.kind;
          const q = filter.trim().toLowerCase();
          const items = (st.items || []).filter((it) => !q || (it.label || '').toLowerCase().includes(q));
          return (
            <div key={g.kind} className="mde-tree-group">
              <button className={'mde-tree-grouphead' + (isOpen ? ' open' : '')} onClick={() => toggle(g.kind)}>
                <span className="mde-tree-caret">{isOpen ? '▾' : '▸'}</span>
                <span className="mde-tree-gicon">{g.icon}</span>
                <span className="mde-tree-glabel">{g.label}</span>
                {st.items && <span className="mde-tree-count">{st.items.length}</span>}
              </button>
              {isOpen && (
                <div className="mde-tree-children">
                  {st.loading && <div className="mde-tree-hint">加载中…</div>}
                  {st.error && <div className="mde-tree-hint err">加载失败:{st.error}</div>}
                  {!st.loading && !st.error && items.length === 0 && <div className="mde-tree-hint">（空）</div>}
                  {items.map((it) => {
                    const k = nodeKey(g.kind, it.id);
                    return (
                      <button
                        key={k}
                        className={'mde-tree-item' + (activeKey === k ? ' active' : '')}
                        title={it.label}
                        onClick={() => openNode({ kind: g.kind, id: it.id, label: it.label, meta: it })}
                      >
                        {it.label || `(${g.kind} ${it.id})`}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export const nodeKey = (kind, id) => `${kind}:${id}`;

// 每组的列表拉取 —— 复用 window.api.scripts.* / api.cards.*。
async function fetchGroupList(kind, sid) {
  const A = api();
  if (kind === 'chapter') {
    const r = await A.scripts.chapters(sid, { limit: 5000 });
    const arr = r?.chapters || r?.items || [];
    return arr.map((c) => ({ id: c.chapter_index, label: `第${c.chapter_index}章 ${c.title || ''}`.trim(), word_count: c.word_count }));
  }
  if (kind === 'card') {
    const r = await A.cards.scriptList(sid);
    const arr = Array.isArray(r) ? r : (r?.items || []);
    return arr.map((c) => ({ id: c.id, label: c.name + (c.full_name && c.full_name !== c.name ? ` (${c.full_name})` : '') }));
  }
  if (kind === 'worldbook') {
    const r = await A.scripts.worldbook(sid);
    const arr = r?.entries || r?.items || (Array.isArray(r) ? r : []);
    return arr.map((w) => ({ id: w.id, label: w.title || `(条目 ${w.id})` }));
  }
  if (kind === 'anchor') {
    const r = await A.scripts.timeline(sid);
    const phases = r?.phases || [];
    const out = [];
    for (const ph of phases) for (const a of (ph.anchors || [])) {
      out.push({ id: a.anchor_id || a.id, label: `${a.story_time_label || ph.phase_label || ''}（${a.chapter_min}-${a.chapter_max}）` });
    }
    return out;
  }
  if (kind === 'canon') {
    // canon-entities 列表端点在 P1 新增;暂经 graph 端点兜底。
    if (A.scripts.canonList) {
      const r = await A.scripts.canonList(sid);
      const arr = r?.entities || r?.items || [];
      return arr.map((e) => ({ id: e.logical_key, label: `${e.name}（${e.type}）` }));
    }
    try {
      const r = await A.scripts.graph(sid);
      const arr = r?.entities || [];
      return arr.map((e) => ({ id: e.logical_key, label: `${e.name}（${e.type}）` }));
    } catch (_) { return []; }
  }
  return [];
}

// ── 标签编辑器(P0:textarea;P3 替换为 CodeMirror)──────────────────────
function EditorPane({ tab, onChange }) {
  if (!tab) {
    return <div className="mde-empty">从左侧选择一个文件开始编辑<br /><span className="muted">章节正文 / 角色卡 / 世界书 / 时间线 / Canon</span></div>;
  }
  if (tab.loading) return <div className="mde-empty">加载中…</div>;
  if (tab.error) return <div className="mde-empty err">加载失败:{tab.error}</div>;
  return (
    <CodeMirrorEditor
      value={tab.content}
      docKey={tab.key}
      onChange={(v) => onChange(tab.key, v)}
    />
  );
}

// ── 主页面 ───────────────────────────────────────────────────────────
export default function MdEditorPage() {
  const [scripts, setScripts] = useState(null);
  const [scriptId, setScriptId] = useState(() => lsGet('mde.scriptId', null));
  const [tabs, setTabs] = useState([]);          // [{key, kind, id, label, content, original, loading, error, dirty}]
  const [activeKey, setActiveKey] = useState(null);
  const [treeReloadKey, setTreeReloadKey] = useState(0);   // agent 写库后 bump,触发文件树重载

  // 拉剧本列表(仅自己拥有的可编辑)。
  useEffect(() => {
    (async () => {
      try {
        const r = await api().scripts.list();
        const arr = r?.items || r?.scripts || (Array.isArray(r) ? r : []);
        const owned = arr.filter((s) => s.is_owner !== false && s.role !== 'subscriber');
        setScripts(owned);
        if (!scriptId && owned[0]) { setScriptId(owned[0].id); lsSet('mde.scriptId', owned[0].id); }
      } catch (e) { setScripts([]); toast('剧本列表加载失败', { kind: 'danger', detail: e?.message }); }
    })();
    // eslint-disable-next-line
  }, []);

  const pickScript = (id) => { setScriptId(id); lsSet('mde.scriptId', id); setTabs([]); setActiveKey(null); };

  // 打开节点 → 新标签(或激活已开)。
  const openNode = useCallback(async (node) => {
    const key = nodeKey(node.kind, node.id);
    setActiveKey(key);
    setTabs((cur) => {
      if (cur.some((t) => t.key === key)) return cur;
      return [...cur, { key, kind: node.kind, id: node.id, label: node.label, content: '', original: '', loading: true, error: null, dirty: false }];
    });
    try {
      const content = await loadNodeContent(node.kind, scriptId, node.id);
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, content, original: content, loading: false } : t));
    } catch (e) {
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, loading: false, error: e?.message || String(e) } : t));
    }
  }, [scriptId]);

  const onEdit = useCallback((key, val) => {
    setTabs((cur) => cur.map((t) => t.key === key ? { ...t, content: val, dirty: val !== t.original } : t));
  }, []);

  const closeTab = useCallback(async (key) => {
    const t = tabs.find((x) => x.key === key);
    if (t?.dirty) {
      const ok = await (window.__confirm ? window.__confirm({ title: '放弃未保存的修改?', message: t.label, danger: true, confirmText: '放弃' }) : Promise.resolve(confirm('放弃未保存的修改?')));
      if (!ok) return;
    }
    setTabs((cur) => {
      const idx = cur.findIndex((x) => x.key === key);
      const next = cur.filter((x) => x.key !== key);
      if (activeKey === key) setActiveKey(next[Math.max(0, idx - 1)]?.key || null);
      return next;
    });
  }, [tabs, activeKey]);

  const saveTab = useCallback(async (key) => {
    const t = tabs.find((x) => x.key === key);
    if (!t || !t.dirty) return;
    setTabs((cur) => cur.map((x) => x.key === key ? { ...x, saving: true } : x));
    try {
      await saveNodeContent(t.kind, scriptId, t.id, t.content, t.original);
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, original: t.content, dirty: false, saving: false } : x));
      toast('已保存', { kind: 'ok', duration: 1200 });
    } catch (e) {
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, saving: false } : x));
      toast('保存失败', { kind: 'danger', detail: e?.message });
    }
  }, [tabs, scriptId]);

  // agent 写库后:重载受影响的标签(若打开且无未保存改动)+ 刷新文件树。
  const refreshTab = useCallback(async (kind, id) => {
    setTreeReloadKey((x) => x + 1);
    const key = nodeKey(kind, id);
    const t = tabs.find((x) => x.key === key);
    if (!t) return;
    if (t.dirty) { toast('AI 改了这条,但你有未保存修改,未自动刷新', { kind: 'warn', duration: 2600 }); return; }
    try {
      const content = await loadNodeContent(kind, scriptId, id);
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, content, original: content, dirty: false } : x));
      toast('AI 已修改,已刷新', { kind: 'ok', duration: 1400 });
    } catch (_) { /* 静默 */ }
  }, [tabs, scriptId]);

  // Cmd/Ctrl+S 保存当前标签。
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')) {
        e.preventDefault();
        if (activeKey) saveTab(activeKey);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeKey, saveTab]);

  const active = tabs.find((t) => t.key === activeKey) || null;

  return (
    <div className="mde-root">
      {/* 顶栏:剧本选择 */}
      <div className="mde-topbar">
        <span className="mde-brand">MD 编辑器</span>
        <select className="mde-script-select" value={scriptId || ''} onChange={(e) => pickScript(Number(e.target.value) || e.target.value)}>
          {scripts === null && <option>加载剧本…</option>}
          {scripts && scripts.length === 0 && <option value="">（无可编辑剧本）</option>}
          {(scripts || []).map((s) => <option key={s.id} value={s.id}>{s.title || `剧本 ${s.id}`}</option>)}
        </select>
        {active && active.dirty && <button className="mde-save" onClick={() => saveTab(active.key)} disabled={active.saving}>{active.saving ? '保存中…' : '保存 (⌘S)'}</button>}
      </div>

      <div className="mde-panes">
        {/* 左:文件树 */}
        <aside className="mde-left">
          {scriptId ? <FileTree scriptId={scriptId} openNode={openNode} activeKey={activeKey} reloadKey={treeReloadKey} /> : <div className="mde-tree-hint">先选剧本</div>}
        </aside>

        {/* 中:标签 + 编辑器 */}
        <main className="mde-center">
          <div className="mde-tabs">
            {tabs.map((t) => (
              <div key={t.key} className={'mde-tab' + (t.key === activeKey ? ' active' : '')} onClick={() => setActiveKey(t.key)}>
                <span className="mde-tab-label">{t.dirty ? '● ' : ''}{t.label}</span>
                <span className="mde-tab-close" onClick={(e) => { e.stopPropagation(); closeTab(t.key); }}>×</span>
              </div>
            ))}
          </div>
          <EditorPane tab={active} onChange={onEdit} />
        </main>

        {/* 右:agent 直写面板(console_assistant SSE)*/}
        <aside className="mde-right">
          {scriptId
            ? <MdEditorAgent scriptId={scriptId} activeTab={active} onWriteComplete={refreshTab} />
            : <div className="mde-tree-hint">先选剧本</div>}
        </aside>
      </div>
    </div>
  );
}

// ── 节点内容 加载:GET 行 → md-serialize.toMd ─────────────────────────────
async function loadNodeContent(kind, sid, id) {
  const row = await loadRow(kind, sid, id);
  return toMd(kind, row);
}

async function loadRow(kind, sid, id) {
  const A = api();
  if (kind === 'chapter') {
    const r = await A.scripts.chapterDetail(sid, id);
    return r?.chapter ?? r ?? {};
  }
  if (kind === 'card') {
    const r = await A.cards.scriptGet(sid, id);
    return r?.card ?? r ?? {};
  }
  if (kind === 'worldbook') {
    const r = await A.scripts.worldbook(sid);
    const arr = r?.entries || r?.items || (Array.isArray(r) ? r : []);
    return arr.find((x) => String(x.id) === String(id)) || {};
  }
  if (kind === 'anchor') {
    // timeline 端点按 phase 聚合,锚点字段是子集(无 keywords/sample_title);
    // diff-based 保存只发改动字段,故未加载字段不会被覆盖(见 saveNodeContent)。
    const r = await A.scripts.timeline(sid);
    for (const ph of (r?.phases || [])) for (const a of (ph.anchors || [])) {
      if (String(a.anchor_id || a.id) === String(id)) {
        return { ...a, id: a.anchor_id || a.id, story_phase: a.story_phase || ph.phase_label || '' };
      }
    }
    return { id };
  }
  if (kind === 'canon') {
    if (A.scripts.canonGet) { const r = await A.scripts.canonGet(sid, id); return r?.entity ?? r ?? {}; }
    // 兜底:列表里找
    if (A.scripts.canonList) {
      const r = await A.scripts.canonList(sid);
      const arr = r?.entities || r?.items || [];
      return arr.find((e) => String(e.logical_key) === String(id)) || { logical_key: id };
    }
    return { logical_key: id };
  }
  return {};
}

// ── 节点内容 保存:fromMd(当前) vs fromMd(原始) 求 diff,只发改动字段 ──────────
async function saveNodeContent(kind, sid, id, content, original) {
  const A = api();
  const cur = fromMd(kind, content);
  const orig = original != null ? fromMd(kind, original) : {};
  const diff = diffPatch(orig, cur);
  if (Object.keys(diff).length === 0) return;   // 无实际改动

  if (kind === 'chapter') {
    await A.scripts.updateChapter(sid, id, diff);   // 收 {title?, content?, volume_title?}
    return;
  }
  if (kind === 'card') {
    await A.cards.scriptUpsert(sid, { id, ...diff });
    return;
  }
  if (kind === 'worldbook') {
    await A.scripts.worldbookUpdate(sid, id, diff);
    return;
  }
  if (kind === 'anchor') {
    if (!A.scripts.anchorUpdate) throw new Error('时间线写端点未就绪(需后端 P1)');
    await A.scripts.anchorUpdate(sid, id, diff);
    return;
  }
  if (kind === 'canon') {
    if (!A.scripts.canonUpsert) throw new Error('canon 写端点未就绪(需后端 P1)');
    await A.scripts.canonUpsert(sid, { logical_key: id, ...diff });
    return;
  }
  throw new Error(`未知实体类型:${kind}`);
}

// 浅 diff:返回 cur 中与 orig 不同(深比较值)的键。
function diffPatch(orig, cur) {
  const out = {};
  for (const k of Object.keys(cur)) {
    if (!deepEq(orig[k], cur[k])) out[k] = cur[k];
  }
  return out;
}
function deepEq(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a && b && typeof a === 'object') {
    const ka = Object.keys(a), kb = Object.keys(b);
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    if (ka.length !== kb.length) return false;
    return ka.every((k) => deepEq(a[k], b[k]));
  }
  return false;
}
