// MdEditorPage.jsx — 三栏 IDE 页面壳 + 状态编排(从 pages/md-editor.jsx 机械搬出,逐字节不变)。
// 历史病灶(agent 写库 vs 未保存改动互覆盖 / autosave 竞态)相关 handler 原样保留:
//   openConflictMerge / saveTab / refreshTab / proposeChapterDiff / autosave effect。
import React from 'react';
import { useTranslation } from 'react-i18next';
import { lsGet, lsSet, lsGetJSON } from '../../lib/storage.js';
import { undo, redo, selectAll } from '@codemirror/commands';
import { openSearchPanel } from '@codemirror/search';
import { toMd } from '../../lib/md-serialize.js';
import { runContinue } from '../../lib/md-continue.js';
import { showChapterDiff, hasChapterDiff } from '../../lib/md-diff.js';
import MdEditorAgent from '../MdEditorAgent.jsx';
import EditorKbPanel, { useKbHealthBadge } from '../EditorKbPanel.jsx';
import { usePlaytest } from '../EditorPlaytest.jsx';
import { IS_MAC, api, toast, nodeKey } from './helpers.js';
import { ContextMenu } from './ContextMenu.jsx';
import { TbIcon } from './TbIcon.jsx';
import { FileTree } from './FileTree.jsx';
import { EditorPane } from './EditorPane.jsx';
import { QuickOpen } from './QuickOpen.jsx';
import { GlobalSearch } from './GlobalSearch.jsx';
import { ChapterHistory } from './ChapterHistory.jsx';
import { WritingRules } from './WritingRules.jsx';
import { ProblemsPanel } from './ProblemsPanel.jsx';
import { loadNodeContentMeta, saveNodeContent } from './node-io.js';
const { useState, useEffect, useCallback, useRef } = React;

// ── 主页面 ───────────────────────────────────────────────────────────
export default function MdEditorPage() {
  const { t } = useTranslation();
  const [scripts, setScripts] = useState(null);
  // lsGet 返回裸字符串;剧本 id 是整数 → 必须 Number 化,否则 `s.id === scriptId`(数===串)恒不等 → 刷新后工作区显示「未选择」。
  const [scriptId, setScriptId] = useState(() => { const v = lsGet('mde.scriptId', null); return (v == null || v === '') ? null : (Number(v) || v); });
  const [tabs, setTabs] = useState([]);          // [{key, kind, id, label, content, original, loading, error, dirty}]
  const [activeKey, setActiveKey] = useState(null);
  const [treeReloadKey, setTreeReloadKey] = useState(0);   // agent 写库后 bump,触发文件树重载
  const activeViewRef = useRef(null);                      // 当前 CodeMirror 视图(供侧栏「续写到正文」)
  const agentRef = useRef(null);                           // MdEditorAgent 命令句柄(续写后同步桥接)
  const activeRef = useRef(null);                          // 当前标签(在接受续写的回调里读最新 label)
  const [syncNudge, setSyncNudge] = useState(null);        // 接受续写后提示同步:{text, label, rewrite} | null
  const [selLen, setSelLen] = useState(0);                 // 正文当前选中字数(右栏「选区改写」+ 选区上下文芯片)
  const [cursor, setCursor] = useState({ line: 1, col: 1, total: 0 });   // 底部状态栏:光标行:列 + 总字数
  const [leftHidden, setLeftHidden] = useState(false);     // 左侧文件树折叠(Mod+B,VSCode 风)
  const [quickOpen, setQuickOpen] = useState(false);       // Mod+P 快速打开
  const [searchOpen, setSearchOpen] = useState(false);     // Mod+Shift+F 全书检索
  const [historyFor, setHistoryFor] = useState(null);      // 章节版本历史(chapter_index | null)
  const [rulesOpen, setRulesOpen] = useState(false);       // 写作规范(.cursorrules)编辑弹窗
  const [kbOpen, setKbOpen] = useState(false);             // 知识库中心抽屉(P1:治理能力接进 IDE)
  const kbBadge = useKbHealthBadge(scriptId);              // 顶栏徽标:{stale_count, ready} | null
  const { playtest, busy: playtestBusy } = usePlaytest(scriptId);   // P1:写完即试玩
  const [issuesOpen, setIssuesOpen] = useState(false);     // 审稿问题面板(VSCode Problems 风)
  const [issueCount, setIssueCount] = useState(0);         // 顶栏「问题」徽标计数
  const [issuesReloadKey, setIssuesReloadKey] = useState(0); // 编辑器 agent 汇报问题后 bump → 重载
  const [autoComplete, setAutoComplete] = useState(() => lsGet('mde.autocomplete', '0') === '1');  // 内联续写(Copilot ghost)开关,默认关(BYO 计费)
  // 选区/光标上报(对象):右栏要 selLen(数字),状态栏要 line/col/total。
  const onSel = useCallback((info) => {
    if (info && typeof info === 'object') {
      setSelLen(info.len || 0);
      setCursor({ line: info.line || 1, col: info.col || 1, total: info.total || 0 });
    } else { setSelLen(info || 0); }
  }, []);
  // 读当前编辑器选区 + 上下文(供右栏 agent 把选中正文作为上下文)。在发送时实时读,保证拿到最新选区。
  const getSelectionContext = useCallback(() => {
    const v = activeViewRef.current;
    if (!v) return null;
    const sel = v.state.selection.main;
    if (sel.empty) return null;
    const doc = v.state.doc;
    return {
      selection: doc.sliceString(sel.from, sel.to),
      before: doc.sliceString(Math.max(0, sel.from - 1200), sel.from),
      after: doc.sliceString(sel.to, Math.min(doc.length, sel.to + 600)),
    };
  }, []);

  // 拉剧本列表(仅自己拥有的可编辑)。
  useEffect(() => {
    (async () => {
      try {
        const r = await api().scripts.list();
        const arr = r?.items || r?.scripts || (Array.isArray(r) ? r : []);
        const owned = arr.filter((s) => s.is_owner !== false && s.role !== 'subscriber');
        setScripts(owned);
        if (!scriptId && owned[0]) { setScriptId(owned[0].id); lsSet('mde.scriptId', owned[0].id); }
      } catch (e) { setScripts([]); toast(t('md_editor.toast.scripts_load_failed'), { kind: 'danger', detail: e?.message }); }
    })();
    // eslint-disable-next-line
  }, []);

  const pickScript = (id) => { setScriptId(id); lsSet('mde.scriptId', id); setTabs([]); setActiveKey(null); setMenu(null); };

  // ── 顶栏菜单 + 可拖拽分栏 ──────────────────────────────────────────────
  const [menu, setMenu] = useState(null);   // 'ws' | 'file' | 'edit' | null
  const [tabCtx, setTabCtx] = useState(null);     // 标签页右键菜单 {x,y,key}
  const [editorCtx, setEditorCtx] = useState(null); // 编辑器正文右键菜单 {x,y}
  const panesRef = useRef(null);
  const [leftW, setLeftW] = useState(() => { const n = Number(lsGet('mde.leftW', 240)); return n >= 150 && n <= 480 ? n : 240; });
  const [rightW, setRightW] = useState(() => { const n = Number(lsGet('mde.rightW', 320)); return n >= 220 && n <= 560 ? n : 320; });
  // 右栏(AI 助手)开合:有持久化用之;否则宽屏默认开、窄屏默认关(避免小屏一进来就被浮层盖住,且提供入口)。
  const [rightOpen, setRightOpen] = useState(() => {
    const v = lsGet('mde.rightOpen', null);
    if (v === '1' || v === true || v === 1) return true;
    if (v === '0' || v === false || v === 0) return false;
    return typeof window !== 'undefined' ? window.innerWidth > 1100 : true;
  });
  const toggleRight = useCallback(() => setRightOpen((v) => { const n = !v; lsSet('mde.rightOpen', n ? '1' : '0'); return n; }), []);
  const dragRef = useRef(null);
  const onSplitDown = (side) => (e) => {
    e.preventDefault();
    const startX = e.clientX, startLeft = leftW, startRight = rightW;
    const move = (ev) => {
      const dx = ev.clientX - startX;
      const w = side === 'left'
        ? Math.max(150, Math.min(480, startLeft + dx))
        : Math.max(220, Math.min(560, startRight - dx));
      panesRef.current?.style.setProperty(side === 'left' ? '--mde-left-w' : '--mde-right-w', w + 'px');
      dragRef.current = { side, w };
    };
    const up = () => {
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); window.removeEventListener('pointercancel', up);
      document.body.style.cursor = ''; document.body.style.userSelect = '';
      const d = dragRef.current; dragRef.current = null;
      if (d) { if (d.side === 'left') { setLeftW(d.w); lsSet('mde.leftW', d.w); } else { setRightW(d.w); lsSet('mde.rightW', d.w); } }
    };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up); window.addEventListener('pointercancel', up);
    document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none';
  };

  // 顶栏「编辑」操作:全部作用于当前 CodeMirror 视图(activeViewRef)。
  const withView = useCallback((fn) => { const v = activeViewRef.current; if (!v) { toast(t('md_editor.toast.open_file_first'), { kind: 'warn', duration: 1400 }); return; } v.focus(); fn(v); }, [t]);
  const doUndo = useCallback(() => withView((v) => undo(v)), [withView]);
  const doRedo = useCallback(() => withView((v) => redo(v)), [withView]);
  const doSelectAll = useCallback(() => withView((v) => selectAll(v)), [withView]);
  const doFind = useCallback(() => withView((v) => openSearchPanel(v)), [withView]);
  const doCopy = useCallback(() => withView(async (v) => { const s = v.state.sliceDoc(v.state.selection.main.from, v.state.selection.main.to); if (!s) return; try { await navigator.clipboard.writeText(s); } catch (_) { toast(t('md_editor.toast.copy_failed_kbd', { kbd: '⌘C' }), { kind: 'warn' }); } }), [withView, t]);
  const doCut = useCallback(() => withView(async (v) => { const sel = v.state.selection.main; const s = v.state.sliceDoc(sel.from, sel.to); if (!s) return; try { await navigator.clipboard.writeText(s); v.dispatch({ changes: { from: sel.from, to: sel.to } }); } catch (_) { toast(t('md_editor.toast.cut_failed_kbd', { kbd: '⌘X' }), { kind: 'warn' }); } }), [withView, t]);
  const doPaste = useCallback(() => withView(async (v) => { try { const txt = await navigator.clipboard.readText(); if (!txt) return; const sel = v.state.selection.main; v.dispatch({ changes: { from: sel.from, to: sel.to, insert: txt }, selection: { anchor: sel.from + txt.length } }); } catch (_) { toast(t('md_editor.toast.paste_failed_kbd', { kbd: '⌘V' }), { kind: 'warn' }); } }), [withView, t]);
  const doGotoLine = useCallback(() => withView((v) => { const raw = window.prompt(t('md_editor.prompt.goto_line')); const n = Number(raw); if (!n || n < 1) return; const line = v.state.doc.line(Math.min(Math.floor(n), v.state.doc.lines)); v.dispatch({ selection: { anchor: line.from }, scrollIntoView: true }); }), [withView, t]);

  // 文件菜单:重命名 / 删除当前剧本(严格 owner,后端 403 兜底)。
  const renameScript = useCallback(async () => {
    setMenu(null);
    if (!scriptId) return;
    const cur = (scripts || []).find((s) => s.id === scriptId);
    const name = window.prompt(t('md_editor.prompt.rename_script'), cur?.title || '');
    if (name == null) return;
    const nm = name.trim(); if (!nm) return;
    try { await api().scripts.rename(scriptId, nm); setScripts((prev) => (prev || []).map((s) => s.id === scriptId ? { ...s, title: nm } : s)); toast(t('md_editor.toast.renamed'), { kind: 'ok', duration: 1200 }); }
    catch (e) { toast(t('md_editor.toast.rename_failed'), { kind: 'danger', detail: e?.message }); }
  }, [scriptId, scripts]);
  const deleteScript = useCallback(async () => {
    setMenu(null);
    if (!scriptId) return;
    const cur = (scripts || []).find((s) => s.id === scriptId);
    const ok = await (window.__confirm
      ? window.__confirm({ title: t('md_editor.confirm.delete_script'), message: t('md_editor.confirm.delete_script_msg', { title: cur?.title || scriptId }), danger: true, confirmText: t('common.delete') })
      : Promise.resolve(window.confirm(t('md_editor.confirm.delete_script_plain'))));
    if (!ok) return;
    try {
      await api().scripts.delete(scriptId, { force: true });
      const rest = (scripts || []).filter((s) => s.id !== scriptId);
      setScripts(rest);
      if (rest[0]) pickScript(rest[0].id);
      else { setScriptId(null); lsSet('mde.scriptId', null); setTabs([]); setActiveKey(null); }
      toast(t('md_editor.toast.script_deleted'), { kind: 'ok', duration: 1400 });
    } catch (e) { toast(t('md_editor.toast.delete_failed'), { kind: 'danger', detail: e?.message }); }
  }, [scriptId, scripts]);

  // 顶栏菜单:点击外部关闭。
  useEffect(() => {
    if (!menu) return;
    const onDown = (e) => { if (!e.target.closest?.('.mde-menuwrap')) setMenu(null); };
    window.addEventListener('mousedown', onDown);
    return () => window.removeEventListener('mousedown', onDown);
  }, [menu]);

  // 作者优先:从零新建空白剧本 → 切到它(自动带第1章)。
  const createBlankScript = async () => {
    try {
      const r = await api().scripts.createBlank(t('md_editor.node_defaults.script'));
      if (!r?.script_id) throw new Error(r?.error || t('md_editor.errors.create_failed'));
      setScripts((prev) => [{ id: r.script_id, title: r.title }, ...(prev || [])]);
      pickScript(r.script_id);
      toast(t('md_editor.toast.blank_script_created'), { kind: 'ok', duration: 1400 });
    } catch (e) { toast(t('md_editor.toast.script_create_failed'), { kind: 'danger', detail: e?.message }); }
  };
  // 给当前剧本追加一章并打开。
  const addChapter = async () => {
    if (!scriptId) return;
    try {
      const r = await api().scripts.addChapter(scriptId, '');
      if (!r?.chapter_index) throw new Error(r?.error || t('md_editor.errors.create_failed'));
      setTreeReloadKey((x) => x + 1);
      openNode({ kind: 'chapter', id: r.chapter_index, label: `${t('md_editor.chapter_prefix', { index: r.chapter_index })} ${r.title || ''}`.trim() });
      toast(t('md_editor.toast.chapter_created'), { kind: 'ok', duration: 1200 });
    } catch (e) { toast(t('md_editor.toast.chapter_create_failed'), { kind: 'danger', detail: e?.message }); }
  };

  // 打开节点 → 新标签(或激活已开)。
  const openNode = useCallback(async (node) => {
    const key = nodeKey(node.kind, node.id);
    setActiveKey(key);
    setTabs((cur) => {
      if (cur.some((t) => t.key === key)) return cur;
      return [...cur, { key, kind: node.kind, id: node.id, label: node.label, content: '', original: '', loading: true, error: null, dirty: false }];
    });
    try {
      const meta = await loadNodeContentMeta(node.kind, scriptId, node.id);
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, content: meta.content, original: meta.content, baseUpdatedAt: meta.updatedAt, loading: false } : t));
    } catch (e) {
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, loading: false, error: e?.message || String(e) } : t));
    }
  }, [scriptId]);

  const onEdit = useCallback((key, val) => {
    setTabs((cur) => cur.map((t) => t.key === key ? { ...t, content: val, dirty: val !== t.original } : t));
  }, []);

  // 顶栏「问题」徽标:载入剧本/agent 汇报后拉一次计数(面板未开也显示数量)。
  useEffect(() => {
    if (!scriptId) { setIssueCount(0); return; }
    let cancelled = false;
    (async () => {
      try { const r = await api().scripts.issues(scriptId); if (!cancelled) setIssueCount(r && r.ok ? (r.issues || []).length : 0); }
      catch (_) {}
    })();
    return () => { cancelled = true; };
  }, [scriptId, issuesReloadKey]);

  // 刷新 / 切换工作区:恢复该剧本上次打开的标签页 + 激活标签(用户缓存,刷新不丢上下文)。
  useEffect(() => {
    if (!scriptId) return;
    const saved = lsGetJSON('mde.tabs.' + scriptId, null);
    const savedActive = lsGet('mde.activeKey.' + scriptId, null);
    if (!Array.isArray(saved) || !saved.length) return;
    let cancelled = false;
    (async () => {
      for (const t of saved) { if (cancelled) return; await openNode({ kind: t.kind, id: t.id, label: t.label }); }
      if (!cancelled && savedActive) setActiveKey(savedActive);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scriptId]);

  // 持久化已打开标签:只在非空时写,避免切换工作区瞬间的空态把缓存清掉。
  useEffect(() => {
    if (!scriptId || !tabs.length) return;
    try {
      lsSet('mde.tabs.' + scriptId, JSON.stringify(tabs.map((t) => ({ kind: t.kind, id: t.id, label: t.label }))));
      lsSet('mde.activeKey.' + scriptId, activeKey || '');
    } catch (_) {}
  }, [tabs, activeKey, scriptId]);

  const closeTab = useCallback(async (key) => {
    // 修复:局部 `const t` 曾遮蔽 useTranslation 的 t() → t('…') 把 tab 对象当函数调 = TypeError;
    // 且 message: tab.label 引用了未定义的 `tab`(实际变量被命名为遮蔽的 t)→ ReferenceError。
    // 关闭「有未保存改动」的标签必崩。改名局部为 tab,恢复 t() 为翻译函数。
    const tab = tabs.find((x) => x.key === key);
    if (tab?.dirty) {
      const ok = await (window.__confirm ? window.__confirm({ title: t('md_editor.confirm.discard_changes'), message: tab.label, danger: true, confirmText: t('md_editor.confirm.discard') }) : Promise.resolve(confirm(t('md_editor.confirm.discard_changes'))));
      if (!ok) return;
    }
    setTabs((cur) => {
      const idx = cur.findIndex((x) => x.key === key);
      const next = cur.filter((x) => x.key !== key);
      if (activeKey === key) setActiveKey(next[Math.max(0, idx - 1)]?.key || null);
      return next;
    });
  }, [tabs, activeKey]);

  // 批量关闭标签(关闭其他/右侧/已保存/全部):若集合里有未保存的,先确认一次。
  const closeTabs = useCallback(async (keys) => {
    const set = keys instanceof Set ? keys : new Set(keys);
    const targets = tabs.filter((t) => set.has(t.key));
    if (!targets.length) return;
    const dirtyCnt = targets.filter((t) => t.dirty).length;
    if (dirtyCnt) {
      const ok = await (window.__confirm
        ? window.__confirm({ title: t('md_editor.confirm.discard_tabs', { count: dirtyCnt }), message: t('md_editor.confirm.discard_tabs_msg'), danger: true, confirmText: t('md_editor.confirm.discard_and_close') })
        : Promise.resolve(confirm(t('md_editor.confirm.discard_tabs_plain', { count: dirtyCnt }))));
      if (!ok) return;
    }
    setTabs((cur) => {
      const next = cur.filter((t) => !set.has(t.key));
      if (set.has(activeKey)) setActiveKey(next[next.length - 1]?.key || null);
      return next;
    });
  }, [tabs, activeKey]);

  // 冲突三方合并(P0):把「服务端新版 vs 本地未保存」丢进现成逐块 diff 审阅。
  // 全部批准=采用对方;全部拒绝=保留自己(以服务端版本为基线强制落库);逐块=合并保存。
  // 仅当该章节是激活标签(有 EditorView)时可用,返回 false 让调用方走降级提示。
  const openConflictMerge = useCallback((key, localContent, serverMd, serverTs) => {
    const view = activeViewRef.current;
    const a = activeRef.current;
    if (!view || !a || a.key !== key || a.kind !== 'chapter') return false;
    const applyTab = (content, ts) => setTabs((cur) => cur.map((x) => x.key === key
      ? { ...x, content, original: content, dirty: false, saving: false, conflict: false, baseUpdatedAt: ts } : x));
    const forceSave = async (finalText, okMsg) => {
      try {
        const ts = await saveNodeContent('chapter', scriptId, a.id, finalText, serverMd, serverTs);
        applyTab(finalText, ts || serverTs);
        toast(okMsg, { kind: 'ok', duration: 1600 });
      } catch (e2) {
        toast(t('md_editor.toast.save_failed'), { kind: 'danger', detail: e2?.message });
      }
    };
    const ok = showChapterDiff(view, localContent, serverMd, {
      onAccept: () => { applyTab(serverMd, serverTs); toast(t('md_editor.conflict.took_theirs'), { kind: 'ok', duration: 1600 }); },
      onReject: () => { forceSave(localContent, t('md_editor.conflict.kept_yours')); },
      onMixed: (finalText) => { forceSave(finalText, t('md_editor.conflict.mixed_saved')); },
    });
    if (ok) toast(t('md_editor.conflict.merge_prompt'), { kind: 'warn', duration: 3200 });
    return ok;
  }, [scriptId, t]);

  const saveTab = useCallback(async (key, opts) => {
    const tab = tabs.find((x) => x.key === key);
    if (!tab || !tab.dirty) return;
    setTabs((cur) => cur.map((x) => x.key === key ? { ...x, saving: true } : x));
    try {
      const newTs = await saveNodeContent(tab.kind, scriptId, tab.id, tab.content, tab.original, tab.baseUpdatedAt);
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, original: tab.content, dirty: false, saving: false, conflict: false, baseUpdatedAt: newTs || x.baseUpdatedAt } : x));
      toast(t('md_editor.toast.saved'), { kind: 'ok', duration: 1200 });
    } catch (e) {
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, saving: false } : x));
      if (e && e.status === 409 && e.payload && e.payload.conflict) {
        const server = e.payload.server_chapter || {};
        const serverMd = toMd('chapter', server);
        const serverTs = server.updated_at || null;
        if (opts && opts.auto) {
          setTabs((cur) => cur.map((x) => x.key === key ? { ...x, conflict: true } : x));
          toast(t('md_editor.conflict.auto_deferred'), { kind: 'warn', duration: 3600 });
          return;
        }
        if (openConflictMerge(key, tab.content, serverMd, serverTs)) return;
        setTabs((cur) => cur.map((x) => x.key === key ? { ...x, conflict: true } : x));
        toast(t('md_editor.conflict.other_tab'), { kind: 'warn', duration: 3600 });
        return;
      }
      toast(t('md_editor.toast.save_failed'), { kind: 'danger', detail: e?.message });
    }
  }, [tabs, scriptId, t, openConflictMerge]);

  // agent 写库后:重载受影响的标签(若打开且无未保存改动)+ 刷新文件树。
  const refreshTab = useCallback(async (kind, id) => {
    setTreeReloadKey((x) => x + 1);
    const key = nodeKey(kind, id);
    const tab = tabs.find((x) => x.key === key);
    if (!tab) return;
    try {
      const meta = await loadNodeContentMeta(kind, scriptId, id);
      if (!tab.dirty) {
        setTabs((cur) => cur.map((x) => x.key === key ? { ...x, content: meta.content, original: meta.content, baseUpdatedAt: meta.updatedAt, dirty: false, conflict: false } : x));
        toast(t('md_editor.toast.ai_refreshed'), { kind: 'ok', duration: 1400 });
        return;
      }
      // P0:有未保存改动时不再只 toast(旧行为=用户随后 ⌘S 用旧底稿覆盖 AI 改动,静默丢数据)。
      // 激活章节→直接进三方合并;其他情况标记冲突,乐观锁在保存时兜底。
      if (kind === 'chapter' && openConflictMerge(key, tab.content, meta.content, meta.updatedAt)) return;
      setTabs((cur) => cur.map((x) => x.key === key ? { ...x, conflict: true } : x));
      toast(t('md_editor.toast.ai_edited_has_unsaved'), { kind: 'warn', duration: 3200 });
    } catch (_) { /* 静默 */ }
  }, [tabs, scriptId, t, openConflictMerge]);

  // 资源管理器增删改后:同步已打开的标签(删→关、改名→更新标题),并触发树重载。
  const onTreeMutate = useCallback((action, kind, id, label) => {
    const key = nodeKey(kind, id);
    if (action === 'delete') {
      setTabs((cur) => {
        const idx = cur.findIndex((t) => t.key === key);
        const next = cur.filter((t) => t.key !== key);
        if (activeKey === key) setActiveKey(next[Math.max(0, idx - 1)]?.key || null);
        return next;
      });
    } else if (action === 'rename' && label) {
      setTabs((cur) => cur.map((t) => t.key === key ? { ...t, label } : t));
    }
  }, [activeKey]);

  // 接受一段续写/改写后的桥接:够长就提示「要不要让助手把新设定同步进知识库」。
  // (续写引擎只产纯文本不落库,知识同步只能由右栏 agent 触发 —— 这条桥接把两路打通。)
  const onProseAccepted = useCallback((text, info) => {
    const tx = (text || '').trim();
    if (tx.length < 12) return;   // 太短(单词级)不打扰
    setSyncNudge({ text: tx, label: activeRef.current?.label || t('md_editor.sync.prose_label'), rewrite: !!(info && info.rewrite) });
  }, [t]);

  // 侧栏「续写到正文」:对当前打开的章节正文,在光标处(或选中段)用 AI 续写/改写。
  const onContinue = useCallback((instruction) => {
    const view = activeViewRef.current;
    if (!view) { toast(t('md_editor.toast.open_file_first_continue'), { kind: 'warn' }); return; }
    const _a = activeRef.current;
    const _ci = (_a && _a.kind === 'chapter') ? _a.id : null;   // 章号→后端装配相关设定+防剧透
    runContinue(view, { scriptId, instruction, onAccept: onProseAccepted, chapterIndex: _ci });
  }, [scriptId, onProseAccepted]);

  // agent 提议改某章 → 在中间编辑器内联 diff(绿增/红删)+ 顶栏「全部批准/拒绝」。
  // 仅当该章正是当前激活的 tab 时拦截到编辑器;否则返回 false → 侧栏走原确认。cbs.onAccept/onReject
  // 在用户点批准/拒绝时回调(触发对 agent 的 /confirm approve|reject)。任何异常都回 false 退回侧栏。
  const proposeChapterDiff = useCallback((chapterIndex, newText, cbs) => {
    try {
      const view = activeViewRef.current;
      const a = activeRef.current;
      if (!view || !a || a.kind !== 'chapter' || String(a.id) !== String(chapterIndex)) return false;
      if (newText == null) return false;
      const oldText = view.state.doc.toString();
      // 逐块取舍(混合):后端 approve 会写【整段 newText】→ 不能用 approve。改为 reject 掉 agent 提议,
      // 再把编辑器里玩家逐块选定的实际文本经 owner 校验端点直接落库(云端隔离)。
      const onMixed = async (finalText) => {
        try { cbs.onReject?.(); } catch (_) {}
        try {
          await saveNodeContent('chapter', scriptId, chapterIndex, finalText, oldText);
          const key = nodeKey('chapter', chapterIndex);
          setTabs((cur) => cur.map((x) => x.key === key ? { ...x, content: finalText, original: finalText, dirty: false } : x));
          setTreeReloadKey((x) => x + 1);
          toast(t('md_editor.diff.mixed_saved', { defaultValue: '已按你的逐段取舍保存' }), { kind: 'ok', duration: 1600 });
        } catch (e) {
          toast(t('md_editor.toast.save_failed'), { kind: 'danger', detail: e?.message });
        }
      };
      return showChapterDiff(view, oldText, newText, { ...cbs, onMixed });
    } catch (_) { return false; }
  }, [scriptId, t]);

  // 内联续写(Copilot ghost):据光标前文向后端要一句短续写。owner-scoped + 用户自有模型/key(后端隔离)。
  const ghostFetch = useCallback(async (before) => {
    try {
      const _a = activeRef.current;
      const ci = (_a && _a.kind === 'chapter') ? _a.id : null;
      const r = await api().scripts.autocomplete(scriptId, { before, chapter_index: ci });
      return (r && r.ok && r.text) ? String(r.text) : '';
    } catch (_) { return ''; }
  }, [scriptId]);
  const toggleAutoComplete = useCallback(() => {
    setAutoComplete((on) => { const next = !on; lsSet('mde.autocomplete', next ? '1' : '0'); return next; });
  }, []);
  // AI 复审本章(对标 Copilot /review):让右栏 agent 通读本章 + 汇总问题到「问题」面板(沿用 Batch 6 管线)。
  const reviewActiveChapter = useCallback(() => {
    const a = activeRef.current;
    if (!a || a.kind !== 'chapter') return;
    setRightOpen(true); lsSet('mde.rightOpen', '1');
    const ok = agentRef.current?.reviewChapter?.(a.id, a.label);
    if (ok === false) { toast(t('md_editor.review.busy', { defaultValue: 'AI 正忙,请稍候再试' }), { kind: 'warning' }); return; }
    toast(t('md_editor.review.started', { defaultValue: '正在复审本章,问题会汇总到「问题」面板' }), { kind: 'ok', duration: 2400 });
  }, [t]);

  // 「同步设定」:把刚接受的正文丢给右栏 agent,按 rule 4 读现状 + 同步知识资产。
  const doSync = useCallback(() => {
    const n = syncNudge;
    if (!n) return;
    setSyncNudge(null);
    try { agentRef.current?.syncFromProse(n.text, n.label, n.rewrite); } catch (_) { /* 静默 */ }
  }, [syncNudge]);

  // 全局快捷键(OS 自适应:Mac=⌘ / 其它=Ctrl):保存 / 折叠左树 / 快速打开 / 替换。
  useEffect(() => {
    const onKey = (e) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      const k = (e.key || '').toLowerCase();
      if (k === 's') { e.preventDefault(); if (activeKey) saveTab(activeKey); }
      else if (k === 'b' && !e.shiftKey && !e.altKey) { e.preventDefault(); setLeftHidden((v) => !v); }
      else if (k === 'p' && !e.shiftKey && !e.altKey) { e.preventDefault(); setQuickOpen(true); }
      else if (k === 'f' && e.shiftKey && !e.altKey) { e.preventDefault(); setSearchOpen(true); }
      // 替换:Mac=⌘⌥F(系统占用 ⌘H);Win/Linux=Ctrl+H。两者都打开 CM 搜索面板(含替换)。
      else if ((IS_MAC && e.altKey && k === 'f') || (!IS_MAC && k === 'h')) {
        e.preventDefault();
        const v = activeViewRef.current; if (v) { v.focus(); try { openSearchPanel(v); } catch (_) {} }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [activeKey, saveTab]);


  const active = tabs.find((t) => t.key === activeKey) || null;
  activeRef.current = active;

  // 自动保存:正文 dirty 后 2.5s 空闲存盘;内联 diff 审阅中【绝不】自动保存(否则把未批准的 diff
  // 文本落库)。云端多用户安全:saveTab 走 owner 校验的 updateChapter 端点。
  useEffect(() => {
    if (!active || !active.dirty) return undefined;
    if (active.conflict) return undefined;   // 冲突已标记:等用户 ⌘S 进合并,不再重试(否则每 2.5s 409+toast 轰炸)
    const key = active.key;
    const id = setTimeout(() => {
      const v = activeViewRef.current;
      if (v && hasChapterDiff(v)) return;   // diff 审阅中,跳过
      saveTab(key, { auto: true });
    }, 2500);
    return () => clearTimeout(id);
  }, [active && active.key, active && active.content, active && active.dirty, active && active.conflict, saveTab]);

  return (
    <div className="mde-root">
      {/* 顶栏:工作区 + 编辑图标 + 文件/编辑菜单 */}
      <div className="mde-topbar">
        {/* 工作区切换(剧本) */}
        <div className="mde-menuwrap">
          <button className="mde-ws" onClick={() => setMenu(menu === 'ws' ? null : 'ws')} title={t('md_editor.ws.switch_title')}>
            <span className="mde-ws-kicker">{t('md_editor.ws.label')}</span>
            <span className="mde-ws-name">{(scripts || []).find((s) => s.id === scriptId)?.title || (scripts === null ? t('common.loading') : t('md_editor.ws.none_selected'))}</span>
            <span className="mde-ws-caret">▾</span>
          </button>
          {menu === 'ws' && (
            <div className="mde-menu mde-ws-menu">
              {scripts === null && <div className="mde-menu-hint">{t('md_editor.ws.loading')}</div>}
              {scripts && scripts.length === 0 && <div className="mde-menu-hint">{t('md_editor.ws.no_scripts')}</div>}
              {(scripts || []).map((s) => (
                <button key={s.id} className={'mde-menu-item' + (s.id === scriptId ? ' on' : '')} onClick={() => pickScript(s.id)}>{s.title || t('md_editor.ws.script_fallback', { id: s.id })}</button>
              ))}
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" onClick={() => { setMenu(null); createBlankScript(); }}>＋ {t('md_editor.menu.new_blank_script')}</button>
            </div>
          )}
        </div>

        {/* 编辑操作图标组 */}
        <div className="mde-tb-icons">
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.undo')} title={t('md_editor.toolbar.undo')} onClick={doUndo}><TbIcon name="undo" /></button>
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.redo')} title={t('md_editor.toolbar.redo')} onClick={doRedo}><TbIcon name="redo" /></button>
          <span className="mde-tb-divider" />
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.copy')} title={t('md_editor.toolbar.copy')} onClick={doCopy}><TbIcon name="copy" /></button>
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.cut')} title={t('md_editor.toolbar.cut')} onClick={doCut}><TbIcon name="cut" /></button>
          <button className="mde-tb-ic" data-tip={t('md_editor.toolbar.paste')} title={t('md_editor.toolbar.paste')} onClick={doPaste}><TbIcon name="paste" /></button>
        </div>

        {/* 文件菜单 */}
        <div className="mde-menuwrap">
          <button className={'mde-menubtn' + (menu === 'file' ? ' on' : '')} onClick={() => setMenu(menu === 'file' ? null : 'file')}>{t('md_editor.menu.file')}</button>
          {menu === 'file' && (
            <div className="mde-menu">
              <button className="mde-menu-item" disabled={!scriptId} onClick={() => { setMenu(null); addChapter(); }}>{t('md_editor.menu.new_chapter')}</button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); createBlankScript(); }}>{t('md_editor.menu.new_blank_script')}</button>
              <button className="mde-menu-item" disabled={!scriptId} onClick={renameScript}>{t('md_editor.menu.rename_script')}</button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item danger" disabled={!scriptId} onClick={deleteScript}>{t('md_editor.menu.delete_script')}</button>
            </div>
          )}
        </div>

        {/* 编辑菜单 */}
        <div className="mde-menuwrap">
          <button className={'mde-menubtn' + (menu === 'edit' ? ' on' : '')} onClick={() => setMenu(menu === 'edit' ? null : 'edit')}>{t('md_editor.menu.edit')}</button>
          {menu === 'edit' && (
            <div className="mde-menu">
              <button className="mde-menu-item" onClick={() => { setMenu(null); doUndo(); }}>{t('md_editor.menu.undo')}<span className="mde-menu-kbd">⌘Z</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doRedo(); }}>{t('md_editor.menu.redo')}<span className="mde-menu-kbd">⌘⇧Z</span></button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" onClick={() => { setMenu(null); doCopy(); }}>{t('md_editor.menu.copy')}<span className="mde-menu-kbd">⌘C</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doCut(); }}>{t('md_editor.menu.cut')}<span className="mde-menu-kbd">⌘X</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doPaste(); }}>{t('md_editor.menu.paste')}<span className="mde-menu-kbd">⌘V</span></button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" onClick={() => { setMenu(null); doFind(); }}>{t('md_editor.menu.find')}<span className="mde-menu-kbd">⌘F</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doSelectAll(); }}>{t('md_editor.menu.select_all')}<span className="mde-menu-kbd">⌘A</span></button>
              <button className="mde-menu-item" onClick={() => { setMenu(null); doGotoLine(); }}>{t('md_editor.menu.goto_line')}</button>
              <div className="mde-menu-sep" />
              <button className="mde-menu-item" disabled={!active || !active.dirty} onClick={() => { setMenu(null); if (active) saveTab(active.key); }}>{t('common.save')}<span className="mde-menu-kbd">⌘S</span></button>
            </div>
          )}
        </div>

        <div className="mde-tb-spacer" />
        {scriptId && active && active.kind === 'chapter' && <button className={'mde-tb-txt' + (autoComplete ? ' on' : '')} data-tip={t('md_editor.ghost.tip', { defaultValue: '内联续写:打字停顿后给灰色续写建议,Tab 采纳(用你自己的模型)' })} title={t('md_editor.ghost.btn', { defaultValue: 'AI 续写' })} onClick={toggleAutoComplete}>{t('md_editor.ghost.btn', { defaultValue: 'AI 续写' })}{autoComplete ? ' ·开' : ''}</button>}
        {scriptId && <button className={'mde-tb-txt' + (issueCount > 0 ? ' has-badge' : '')} data-tip={t('md_editor.problems.tip', { defaultValue: 'AI 审稿发现的问题(可跳转章节)' })} title={t('md_editor.problems.btn', { defaultValue: '问题' })} onClick={() => setIssuesOpen(true)}>{t('md_editor.problems.btn', { defaultValue: '问题' })}{issueCount > 0 ? <span className="mde-tb-badge">{issueCount}</span> : null}</button>}
        {scriptId && <button className="mde-tb-txt" data-tip={t('md_editor.rules.tip', { defaultValue: '写作规范:每次生成都会遵守' })} title={t('md_editor.rules.btn', { defaultValue: '写作规范' })} onClick={() => setRulesOpen(true)}>{t('md_editor.rules.btn', { defaultValue: '写作规范' })}</button>}
        {scriptId && <button className={'mde-tb-txt' + (kbBadge && !kbBadge.ready ? ' has-badge' : '')} data-tip={t('md_editor.kb.tip', { defaultValue: '本剧本知识库健康度与重建(摘要精炼/世界书充实/复核卡/嵌入)' })} title={t('md_editor.kb.title', { defaultValue: '知识库中心' })} onClick={() => setKbOpen(true)}>{t('md_editor.kb.title', { defaultValue: '知识库中心' })}{kbBadge && !kbBadge.ready ? <span className="mde-tb-badge">{kbBadge.stale_count}</span> : null}</button>}
        {active && active.dirty && <button className="mde-save" onClick={() => saveTab(active.key)} disabled={active.saving}>{active.saving ? t('md_editor.save_btn.saving') : t('md_editor.save_btn.save')}</button>}
        <button className={'mde-tb-ic' + (rightOpen ? ' on' : '')} data-tip={rightOpen ? t('md_editor.panel.hide_ai') : t('md_editor.panel.show_ai')} title={rightOpen ? t('md_editor.panel.hide_ai') : t('md_editor.panel.show_ai')} onClick={toggleRight}><TbIcon name="panelRight" /></button>
      </div>

      {/* 窄屏右栏浮层的背景遮罩:点击/触摸关闭(CSS 只在 <1100px 显示;宽屏右栏是网格列不显示)。 */}
      {rightOpen && <div className="mde-scrim" onClick={toggleRight} aria-hidden="true" />}
      <div className={'mde-panes' + (rightOpen ? '' : ' right-collapsed') + (leftHidden ? ' left-collapsed' : '')} ref={panesRef} style={{ '--mde-left-w': leftW + 'px', '--mde-right-w': rightW + 'px' }}>
        {/* 左:文件树 */}
        <aside className="mde-left">
          {scriptId ? <FileTree scriptId={scriptId} openNode={openNode} activeKey={activeKey} reloadKey={treeReloadKey} onMutate={onTreeMutate} /> : <div className="mde-tree-hint">{t('md_editor.ws.select_first')}</div>}
        </aside>

        <div className="mde-splitter mde-splitter-left" onPointerDown={onSplitDown('left')} title={t('md_editor.splitter.left')} />

        {/* 中:标签 + 编辑器 */}
        <main className="mde-center">
          <div className="mde-tabs">
            {tabs.map((tb) => (
              <div key={tb.key} className={'mde-tab' + (tb.key === activeKey ? ' active' : '')} onClick={() => setActiveKey(tb.key)}
                title={tb.label}
                onContextMenu={(e) => { e.preventDefault(); setTabCtx({ x: e.clientX, y: e.clientY, key: tb.key }); }}
                onMouseDown={(e) => { if (e.button === 1) { e.preventDefault(); closeTab(tb.key); } /* 中键关闭(VSCode) */ }}>
                <span className={'mde-tab-label' + (tb.conflict ? ' mde-tab-conflict' : '')} title={tb.conflict ? t('md_editor.conflict.tab_tip') : undefined}>{tb.conflict ? '! ' : (tb.dirty ? '● ' : '')}{tb.label}</span>
                <span className="mde-tab-close" title={t('common.close')} onClick={(e) => { e.stopPropagation(); closeTab(tb.key); }}>×</span>
              </div>
            ))}
          </div>
          <div className="mde-editorwrap" onContextMenu={(e) => { if (!active) return; e.preventDefault(); setEditorCtx({ x: e.clientX, y: e.clientY }); }}>
            <EditorPane tab={active} onChange={onEdit} scriptId={scriptId} onViewReady={(v) => { activeViewRef.current = v; }} onContinueAccept={onProseAccepted} chapterIndex={active && active.kind === 'chapter' ? active.id : null} onSelectionChange={onSel} ghostEnabled={autoComplete} ghostFetch={ghostFetch} />
          </div>
          {/* 底部状态栏(VSCode 风):字数 + 光标行:列 + 选中 + 保存态 */}
          <div className="mde-statusbar">
            {active ? (
              <>
                <span className="mde-sb-item">{t('md_editor.statusbar.words', { n: (cursor.total || (active.content || '').length).toLocaleString(), defaultValue: '{{n}} 字' })}</span>
                <span className="mde-sb-item">{t('md_editor.statusbar.lncol', { line: cursor.line, col: cursor.col, defaultValue: '行 {{line}}, 列 {{col}}' })}</span>
                {selLen > 0 && <span className="mde-sb-item">{t('md_editor.statusbar.selected', { n: selLen, defaultValue: '选中 {{n}}' })}</span>}
                <span className="mde-sb-spacer" />
                {active.kind === 'chapter' && (
                  <button type="button" className="mde-sb-btn" onClick={reviewActiveChapter}>{t('md_editor.review.btn', { defaultValue: 'AI 复审本章' })}</button>
                )}
                {active.kind === 'chapter' && (
                  <button type="button" className="mde-sb-btn" onClick={() => setHistoryFor(active.id)}>{t('md_editor.history.btn', { defaultValue: '版本历史' })}</button>
                )}
                {active.kind === 'chapter' && (
                  <button type="button" className="mde-sb-btn" disabled={playtestBusy} onClick={() => playtest(active.id, active.label)}>{playtestBusy ? t('md_editor.playtest.busy', { defaultValue: '试玩中…' }) : t('md_editor.playtest.btn', { defaultValue: '从本章试玩' })}</button>
                )}
                <span className={'mde-sb-item' + (active.dirty ? ' dirty' : '')}>{active.dirty ? t('md_editor.statusbar.unsaved', { defaultValue: '未保存' }) : t('md_editor.statusbar.saved', { defaultValue: '已保存' })}</span>
              </>
            ) : <span className="mde-sb-item muted">{t('md_editor.statusbar.no_file', { defaultValue: '未打开文件' })}</span>}
          </div>
          {tabCtx && (() => {
            const idx = tabs.findIndex((t) => t.key === tabCtx.key);
            const others = tabs.filter((t) => t.key !== tabCtx.key).map((t) => t.key);
            const toRight = tabs.slice(idx + 1).map((t) => t.key);
            const saved = tabs.filter((t) => !t.dirty).map((t) => t.key);
            const items = [
              { label: t('common.close'), kbd: '⌘W', onClick: () => closeTab(tabCtx.key) },
              { label: t('md_editor.tab_ctx.close_others'), disabled: others.length === 0, onClick: () => { setActiveKey(tabCtx.key); closeTabs(others); } },
              { label: t('md_editor.tab_ctx.close_to_right'), disabled: toRight.length === 0, onClick: () => closeTabs(toRight) },
              { sep: true },
              { label: t('md_editor.tab_ctx.close_saved'), disabled: saved.length === 0, onClick: () => closeTabs(saved) },
              { label: t('md_editor.tab_ctx.close_all'), disabled: tabs.length === 0, onClick: () => closeTabs(tabs.map((tb) => tb.key)) },
            ];
            return <ContextMenu x={tabCtx.x} y={tabCtx.y} items={items} onClose={() => setTabCtx(null)} />;
          })()}
          {editorCtx && (
            <ContextMenu x={editorCtx.x} y={editorCtx.y} onClose={() => setEditorCtx(null)} items={[
              { label: t('md_editor.menu.cut'), kbd: '⌘X', onClick: doCut },
              { label: t('md_editor.menu.copy'), kbd: '⌘C', onClick: doCopy },
              { label: t('md_editor.menu.paste'), kbd: '⌘V', onClick: doPaste },
              { sep: true },
              { label: t('md_editor.menu.select_all'), kbd: '⌘A', onClick: doSelectAll },
              { sep: true },
              { label: t('md_editor.menu.undo'), kbd: '⌘Z', onClick: doUndo },
              { label: t('md_editor.menu.redo'), kbd: '⌘⇧Z', onClick: doRedo },
            ]} />
          )}
          {syncNudge && (
            <div className="mde-syncbar">
              <span className="mde-syncbar-text">
                {syncNudge.rewrite ? t('md_editor.sync.nudge_rewrite') : t('md_editor.sync.nudge_continue')}
              </span>
              <button className="mde-syncbar-go" onClick={doSync}>{t('md_editor.sync.sync_btn')}</button>
              <button className="mde-syncbar-no" onClick={() => setSyncNudge(null)}>{t('md_editor.sync.ignore_btn')}</button>
            </div>
          )}
        </main>

        <div className="mde-splitter mde-splitter-right" onPointerDown={onSplitDown('right')} title={t('md_editor.splitter.right')} />

        {/* 右:agent 直写面板(console_assistant SSE)+ 续写到正文 */}
        <aside className="mde-right">
          {scriptId
            ? <MdEditorAgent ref={agentRef} scriptId={scriptId} activeTab={active} onWriteComplete={refreshTab} onContinue={onContinue} onProposeChapterEdit={proposeChapterDiff} selLen={selLen} getSelectionContext={getSelectionContext} onIssuesReported={() => setIssuesReloadKey((x) => x + 1)} />
            : <div className="mde-tree-hint">{t('md_editor.ws.select_first')}</div>}
        </aside>
      </div>
      {quickOpen && scriptId && <QuickOpen scriptId={scriptId} openNode={openNode} onClose={() => setQuickOpen(false)} />}
      {searchOpen && scriptId && <GlobalSearch scriptId={scriptId} openNode={openNode} onClose={() => setSearchOpen(false)} />}
      {historyFor != null && scriptId && <ChapterHistory scriptId={scriptId} chapterIndex={historyFor} onClose={() => setHistoryFor(null)} onRestored={() => { try { refreshTab('chapter', historyFor); } catch (_) {} setHistoryFor(null); }} />}
      {rulesOpen && scriptId && <WritingRules scriptId={scriptId} onClose={() => setRulesOpen(false)} />}
      {kbOpen && scriptId && <EditorKbPanel scriptId={scriptId} open={kbOpen} onClose={() => setKbOpen(false)} />}
      {issuesOpen && scriptId && <ProblemsPanel scriptId={scriptId} reloadKey={issuesReloadKey} onCountChange={setIssueCount}
        onJump={(ch) => { if (ch != null) openNode({ kind: 'chapter', id: ch, label: t('md_editor.chapter_prefix', { index: ch }) }); setIssuesOpen(false); }}
        onClose={() => setIssuesOpen(false)} />}
    </div>
  );
}
