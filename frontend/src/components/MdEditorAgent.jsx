// MdEditorAgent.jsx — MD 编辑器右栏 AI 助手。复用后端 console_assistant(SSE + 工具循环 + 二次确认)。
// 把当前剧本 + 打开文件作为 page_context 传入,LLM 可用 script 级直写工具改库;destructive 工具走二次确认;
// 写成功后回调 onWriteComplete 让编辑器刷新对应标签。设计 docs/design/N_md_editor.md §5。
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Composer } from '../game-composer.jsx';
import { RpgMarkdown } from '../markdown-render.jsx';
import { consumeSSE } from '../lib/sse-consume.js';
import { lsGet, lsSet, lsRemove } from '../lib/storage.js';

// 编辑器 agent 模型选择落库目标:复用游戏/酒馆同一个 Composer 内置模型选择器,但写到 console_assistant
// 专属偏好(不污染游戏 GM 模型)。后端 console_assistant 解析优先读此键(app.py)。
const EDITOR_MODEL_PERSIST = {
  persistShape: 'dict', dictKey: 'console_assistant_model_override', allowInherit: true,
};

const { useState, useRef, useCallback, useEffect, forwardRef, useImperativeHandle } = React;

// 工具原始 API 名 → 人类可读标签(消息流里不再直接显示 update_script_chapter 这种裸标识符)。
const TOOL_LABELS = {
  get_chapter_text: '读章节正文', get_script_chapters: '列出章节', search_manuscript: '全书检索',
  list_worldbook_entries: '列出世界书', list_canon_entities: '列出设定实体', list_anchors: '列出时间线',
  list_script_npcs: '列出角色卡', get_script_character_card: '读角色卡', get_chapter_context: '读本章上下文',
  update_script_chapter: '改写章节', create_script_chapter: '新建章节',
  upsert_worldbook_entry: '写入世界书', upsert_worldbook_entries: '批量写世界书', delete_worldbook_entry: '删除世界书条目',
  update_npc_card: '编辑角色卡', create_npc_card: '新建角色卡',
  update_anchor: '编辑时间线', create_anchor: '新建时间线节点', delete_anchor: '删除时间线节点',
  upsert_canon_entity: '写入设定实体', extract_from_selection: '抽取选区设定',
  read_uploaded_document: '读取拖入文档', preview_document_split: '预览拆章', import_document_as_chapters: '导入为章节',
  delegate_writing_task: '委派子模型写作',
};
function toolLabel(name) { return TOOL_LABELS[name] || name; }

// 某工具调用是否可撤销(done 且未撤过 + 是「编辑已有」类写入):章节 / 世界书条目 / NPC 卡。
function _undoInfo(tc) {
  if (!tc || tc.status !== 'done' || tc.undone) return null;
  const a = tc.args || {};
  if (tc.tool === 'update_script_chapter' && a.chapter_index != null) return { kind: 'chapter', ci: a.chapter_index };
  if (tc.tool === 'upsert_worldbook_entry' && a.entry_id != null) return { kind: 'edit', table: 'worldbook_entries', id: a.entry_id };
  if (tc.tool === 'update_npc_card' && a.card_id != null) return { kind: 'edit', table: 'character_cards', id: a.card_id };
  return null;
}

// 用户铁律:前端禁止出现 emoji。SSE 流 / 历史里模型吐的 emoji 一律【确定性】剥除,不靠模型自觉。
// 剥除常见 emoji / 符号区(图形符号、Dingbats ✓✗⚠★、杂项符号、变体选择子、ZWJ);保留箭头 → 等文本标点。
const _EMOJI_RE = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2300}-\u{23FF}\u{FE00}-\u{FE0F}\u{200D}\u{20E3}]/gu;
function stripEmoji(s) { return typeof s === 'string' ? s.replace(_EMOJI_RE, '') : s; }

// 编辑器写权限只支持 3 档(后端 console_assistant 认 read_only/review/full_access,无 'default')。
// 限制 PermissionPopover 只显示这三档,避免用户点「默认权限」却被静默映射成「审查」造成的高亮跳变。
const EDITOR_PERMS = ['read_only', 'review', 'full_access'];

// 写工具名 → (kind, id-arg-key):写成功后据此刷新编辑器标签。
const WRITE_TOOL_MAP = {
  update_script_chapter: { kind: 'chapter', idArg: 'chapter_index' },
  upsert_worldbook_entry: { kind: 'worldbook', idArg: 'entry_id' },
  upsert_worldbook_entries: { kind: 'worldbook', batch: true },  // 批量:无单一 id,只刷新世界书树组
  update_npc_card: { kind: 'card', idArg: 'card_id' },
  update_anchor: { kind: 'anchor', idArg: 'anchor_id' },
  upsert_canon_entity: { kind: 'canon', idArg: 'logical_key' },
};

// 落库前「改动预览」:章节正文给当前→改为对照(供作者落库前看清改了什么),结构化写给「将写入」。
// 后端 console_assistant.write_preview 算好 before/after 经 confirmation_required.preview 下发。
function MdeWritePreview({ pv, t }) {
  const [open, setOpen] = useState(true);
  if (!pv) return null;
  const isChapter = pv.before !== undefined;
  const head = (pv.is_new
    ? t('components.md_editor_agent.preview.new', { defaultValue: '新建' })
    : t('components.md_editor_agent.preview.change', { defaultValue: '改动预览' }))
    + (pv.label ? ` · ${pv.label}` : '');
  return (
    <div className="mde-agent-preview">
      <button type="button" className="mde-agent-preview-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span className="mde-agent-preview-title">{head}</span>
        <span className="mde-agent-preview-toggle">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="mde-agent-preview-body">
          {isChapter && !pv.is_new && (
            <div className="mde-agent-preview-col">
              <div className="mde-agent-preview-col-label">
                {t('components.md_editor_agent.preview.before', { defaultValue: '当前' })}
                {typeof pv.before_chars === 'number' ? ` · ${pv.before_chars} 字` : ''}
              </div>
              <pre className="mde-agent-preview-text before">{pv.before}</pre>
            </div>
          )}
          <div className="mde-agent-preview-col">
            <div className="mde-agent-preview-col-label">
              {isChapter
                ? t('components.md_editor_agent.preview.after', { defaultValue: '改为' })
                : t('components.md_editor_agent.preview.will_write', { defaultValue: '将写入' })}
              {typeof pv.after_chars === 'number' ? ` · ${pv.after_chars} 字` : ''}
            </div>
            <pre className="mde-agent-preview-text after">{pv.after}</pre>
          </div>
          {pv.truncated && (
            <div className="mde-agent-preview-trunc">
              {t('components.md_editor_agent.preview.truncated', { defaultValue: '(预览已截断,落库为完整内容)' })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// cowork:agent 调 set_writing_plan / report_writing_issues 时,数据在 tool_call.args 里,
// 这里把它渲染成右栏面板(计划清单 / 审稿问题清单)而不是普通工具行。
function MdeAgentPanel({ tc, t }) {
  const a = tc.args || {};
  if (tc.tool === 'set_writing_plan') {
    const steps = Array.isArray(a.steps) ? a.steps.filter((s) => String(s).trim()) : [];
    if (!steps.length) return null;
    return (
      <div className="mde-agent-panel plan">
        <div className="mde-agent-panel-head">{a.title || t('components.md_editor_agent.panel.plan', { defaultValue: '写作计划' })}</div>
        <ol className="mde-agent-plan-list">
          {steps.map((s, k) => <li key={k}>{String(s)}</li>)}
        </ol>
      </div>
    );
  }
  const issues = Array.isArray(a.issues) ? a.issues : [];
  if (!issues.length) return null;
  return (
    <div className="mde-agent-panel issues">
      <div className="mde-agent-panel-head">
        {t('components.md_editor_agent.panel.issues', { defaultValue: '审稿问题' })} · {issues.length}
        {a.summary ? <span className="mde-agent-panel-sum"> — {a.summary}</span> : null}
      </div>
      <ul className="mde-agent-issue-list">
        {issues.map((it, k) => (
          <li key={k} className="mde-agent-issue">
            <div className="mde-agent-issue-meta">
              {it && it.chapter != null && <span className="mde-agent-issue-ch">{t('components.md_editor_agent.panel.chapter', { defaultValue: '第{{n}}章', n: it.chapter })}</span>}
              {it && it.severity && <span className="mde-agent-issue-sev">{String(it.severity)}</span>}
              {it && it.type && <span className="mde-agent-issue-type">{String(it.type)}</span>}
            </div>
            <div className="mde-agent-issue-detail">{it ? String(it.detail || '') : ''}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// 工具调用参数的紧凑展示(隐去内部/冗长键)。
function prettyArgs(args) {
  if (!args || typeof args !== 'object') return '';
  const skip = new Set(['script_id', 'save_id']);
  const lines = [];
  for (const [k, v] of Object.entries(args)) {
    if (skip.has(k)) continue;
    let s = typeof v === 'string' ? v : JSON.stringify(v);
    if (s == null) s = '';
    if (s.length > 600) s = s.slice(0, 600) + '…';
    lines.push(`${k}: ${s}`);
  }
  return lines.join('\n');
}

// Claude-Code 风工具块:一行 header(状态指示 + 标签 + 状态文字 + 展开),点开看参数 / 结果。
// delegate_writing_task = 子代理块(独立样式 + 运行动画 + 展开看子模型产出)。
function MdeToolBlock({ tc, t, busy, onUndo, canUndo }) {
  const [open, setOpen] = useState(false);
  const isSub = tc.tool === 'delegate_writing_task';
  const status = tc.status || 'done';
  const statusText = status === 'running'
    ? t('components.md_editor_agent.tool_status.running')
    : status === 'error' ? t('components.md_editor_agent.tool_status.error')
      : t('components.md_editor_agent.tool_status.done');
  const argStr = prettyArgs(tc.args);
  const resStr = tc.result != null ? stripEmoji(String(tc.result)) : '';
  const hasDetail = !!(argStr || resStr || tc.error);
  const subModel = isSub ? (tc.args?.model || tc.args?.api_id || '') : '';
  return (
    <div className={'mde-tool ' + status + (isSub ? ' subagent' : '')}>
      <button type="button" className="mde-tool-head" disabled={!hasDetail}
        onClick={() => hasDetail && setOpen((o) => !o)} aria-expanded={open}>
        <span className={'mde-tool-ind ' + status} aria-hidden="true" />
        <span className="mde-tool-title">
          {isSub ? t('components.md_editor_agent.subagent.title', { defaultValue: '委派写作' }) : toolLabel(tc.tool)}
          {subModel ? <span className="mde-tool-model">{subModel}</span> : null}
        </span>
        <span className="mde-tool-state">{statusText}</span>
        {hasDetail ? <span className={'mde-tool-chev' + (open ? ' open' : '')} aria-hidden="true" /> : null}
      </button>
      {open && hasDetail && (
        <div className="mde-tool-body">
          {argStr ? (
            <div className="mde-tool-sec">
              <div className="mde-tool-seclabel">{t('components.md_editor_agent.tool_args', { defaultValue: '参数' })}</div>
              <pre className="mde-tool-pre">{argStr}</pre>
            </div>
          ) : null}
          {resStr ? (
            <div className="mde-tool-sec">
              <div className="mde-tool-seclabel">{isSub
                ? t('components.md_editor_agent.subagent.output', { defaultValue: '委派稿' })
                : t('components.md_editor_agent.tool_result', { defaultValue: '结果' })}</div>
              <pre className="mde-tool-pre">{resStr}</pre>
            </div>
          ) : null}
          {tc.error ? <div className="mde-tool-errline">{stripEmoji(String(tc.error))}</div> : null}
        </div>
      )}
      {canUndo && (
        tc.undone
          ? <span className="mde-agent-undone">{t('components.md_editor_agent.undone', { defaultValue: '已撤销' })}</span>
          : <button type="button" className="mde-agent-undo" disabled={busy} onClick={onUndo}>
              {t('components.md_editor_agent.undo_btn', { defaultValue: '撤销此次改动' })}
            </button>
      )}
    </div>
  );
}

// 选区改写预设(写作引擎 rewrite 模式;onContinue 会对当前选区跑 runContinue)。
const REWRITE_PRESETS = [
  { key: 'tighten', labelKey: 'components.md_editor_agent.rewrite.tighten', instruction: '把选中这段改写得更紧凑有力:删去冗余与重复,保留全部信息、人称、时态与语气,不要扩写。' },
  { key: 'expand', labelKey: 'components.md_editor_agent.rewrite.expand', instruction: '把选中这段适度扩写:补充与上下文一致的感官细节、动作或内心,保持原有节奏、视角与语气,不引入新设定。' },
  { key: 'polish', labelKey: 'components.md_editor_agent.rewrite.polish', instruction: '润色选中这段:优化措辞与句子节奏、修正生硬或重复的表达,严格保持原意、人称、视角、语气与内容尺度不变。' },
  { key: 'vivid', labelKey: 'components.md_editor_agent.rewrite.vivid', instruction: '把选中这段改写得更具画面感:多用具体可感的描写、少用空泛的概括与解释,show-don\'t-tell,保持原意与语气。' },
];

// 写作技能(发给写作 agent 的精心 prompt;agent 会用读取工具读正文/设定再诊断,只给清单不擅自改库)。
// 章节场景下最有用 —— 通读 + 对照设定 + 给可执行建议。
const WRITING_SKILLS = [
  { key: 'review', label: '审稿', tipKey: 'review',
    prompt: '请审阅【当前打开的章节】。先用 get_chapter_text 读这章正文,再用 list_worldbook_entries / list_canon_entities / list_script_npcs / list_anchors 对照既有设定与时间线。找出:① 与世界书/正史/时间线/角色卡的设定矛盾;② 前后文连贯性断裂、人物行为不符设定;③ 明显重复的用词或句式;④ 视角(POV)或时态跳脱;⑤ 节奏拖沓或过快的段落。逐条列清单【位置(引一句原文)→ 问题 → 修改建议】。只诊断,先不要改库,等我决定。' },
  { key: 'outline', label: '梳理大纲',
    prompt: '请用 get_chapter_text 读【当前章节】,梳理它的分场/节拍大纲——每个节拍写清:发生了什么 + 推进了什么(信息/关系/张力),并指出结构上可加强或冗余的地方。只输出大纲与建议,不改库。' },
  { key: 'voice', label: '角色嗓音',
    prompt: '请用 get_chapter_text 读【当前章节】,挑出有台词的角色,逐个用 get_script_character_card 取其设定,评估台词是否贴合该角色的说话风格/性格/身份,指出跳脱之处并给修改方向。只诊断,不改库。' },
  { key: 'foreshadow', label: '伏笔线索',
    prompt: '请用 get_chapter_text 读【当前章节】(必要时 get_script_chapters 跨章回看),梳理本章埋下或呼应的伏笔/线索:哪些已回收、哪些悬而未决、哪些可能与既有设定冲突。只输出清单与建议,不改库。' },
];

const MdEditorAgent = forwardRef(function MdEditorAgent({ scriptId, activeTab, onWriteComplete, onContinue, onProposeChapterEdit, selLen = 0, getSelectionContext, onIssuesReported }, ref) {
  const { t } = useTranslation();
  const [messages, setMessages] = useState([]);   // [{role, text, tools:[{call_id,tool,args,status,result}]}]
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [selCtxOff, setSelCtxOff] = useState(false);   // 用户手动关掉了「把选中正文作为聊天上下文」
  // 选区从无到有时,默认重新开启「含选中正文」(每次新选区都给一次默认包含)。
  const prevSelRef = useRef(0);
  useEffect(() => { if (selLen > 0 && prevSelRef.current === 0) setSelCtxOff(false); prevSelRef.current = selLen; }, [selLen]);
  const convIdRef = useRef(null);
  const scrollRef = useRef(null);
  const abortRef = useRef(null);
  // 三级权限(Q3):AI 改库的写入权限 read_only / review(默认) / full_access。持久化 editor.write_mode。
  const [writeMode, setWriteMode] = useState('review');
  // 复用游戏聊天框 Composer 所需的浮层开关(只用权限/写模式浮层;模型/上下文环/斜杠/附件/生图在右栏全隐藏)。
  const [showPerm, setShowPerm] = useState(false);
  const togglePerm = useCallback(() => setShowPerm((v) => !v), []);
  // 内联切模型:复用 Composer 内置模型选择器(与游戏/酒馆同一个),model 仅作底部标签(真正落库由
  // Composer 内的 AgentModelPicker 写 console_assistant_model_override)。
  const [model, setModel] = useState('');
  const [showModel, setShowModel] = useState(false);
  const toggleModel = useCallback(() => setShowModel((v) => !v), []);
  // @引用(Cursor @file 风):把章节/角色卡/世界书 pin 进上下文 → 发送时指示 agent 用对应工具优先读取。
  const [pinnedRefs, setPinnedRefs] = useState([]);   // [{kind, id, label}]
  const [refPicker, setRefPicker] = useState(null);   // null | {items|null, q}
  const openRefPicker = useCallback(async () => {
    if (!scriptId) return;
    setRefPicker({ items: null, q: '' });
    try {
      const A = window.api;
      const [chs, cards, wb] = await Promise.all([
        A?.scripts?.chapters?.(scriptId, { limit: 5000 }).catch(() => null),
        A?.cards?.scriptList?.(scriptId).catch(() => null),
        A?.scripts?.worldbook?.(scriptId).catch(() => null),
      ]);
      const items = [];
      for (const c of ((chs?.chapters || chs?.items) || [])) items.push({ kind: 'chapter', id: c.chapter_index, label: `第${c.chapter_index}章 ${(c.title || '').replace(/^\s*第\s*[0-9一二三四五六七八九十百千]+\s*章\s*/, '')}`.trim() });
      for (const c of (Array.isArray(cards) ? cards : (cards?.items || []))) items.push({ kind: 'card', id: c.id, label: c.name || `卡#${c.id}` });
      for (const w of ((wb?.entries || wb?.items) || (Array.isArray(wb) ? wb : []))) items.push({ kind: 'worldbook', id: w.id, label: w.title || `世界书#${w.id}` });
      setRefPicker((p) => p ? { ...p, items } : p);
    } catch (_) { setRefPicker((p) => p ? { ...p, items: [] } : p); }
  }, [scriptId]);
  const addRef = useCallback((r) => {
    setPinnedRefs((arr) => (arr.some((x) => x.kind === r.kind && x.id === r.id) ? arr : [...arr, r]).slice(0, 12));
    setRefPicker(null);
  }, []);
  const removeRef = useCallback((r) => setPinnedRefs((arr) => arr.filter((x) => !(x.kind === r.kind && x.id === r.id))), []);

  // 拖入文档(txt/md):上传到 /agent-doc 暂存 → 拿 doc_id,下条消息带上让 agent 用确定性拆章工具处理。
  const [attached, setAttached] = useState(null);   // {doc_id, filename, chars}
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);

  const uploadDoc = useCallback(async (file) => {
    if (!scriptId || !file) return;
    const name = file.name || 'doc.txt';
    if (!/\.(txt|md)$/i.test(name)) {
      window.__apiToast?.(t('components.md_editor_agent.doc.only_txt_md', { defaultValue: '只支持 .txt / .md 文档' }), { kind: 'warn' });
      return;
    }
    setUploading(true);
    try {
      const buf = await file.arrayBuffer();
      const bytes = new Uint8Array(buf);
      let bin = '';
      for (let i = 0; i < bytes.length; i += 1) bin += String.fromCharCode(bytes[i]);
      const res = await fetch(`/api/scripts/${scriptId}/agent-doc`, {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: name, content_b64: btoa(bin) }),
      });
      const j = await res.json();
      if (j && j.ok) {
        setAttached({ doc_id: j.doc_id, filename: j.filename || name, chars: j.chars || 0 });
        window.__apiToast?.(t('components.md_editor_agent.doc.attached', { name: j.filename || name, defaultValue: `已附加「${j.filename || name}」,在下方说出要求(如「拆成章节」)` }), { kind: 'ok' });
      } else {
        window.__apiToast?.((j && j.error) || t('components.md_editor_agent.doc.fail', { defaultValue: '文档上传失败' }), { kind: 'danger' });
      }
    } catch (e) {
      window.__apiToast?.(t('components.md_editor_agent.doc.fail', { defaultValue: '文档上传失败' }), { kind: 'danger', detail: e?.message });
    } finally { setUploading(false); }
  }, [scriptId, t]);

  const onDocDragOver = useCallback((e) => {
    if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) {
      e.preventDefault(); setDragOver(true);
    }
  }, []);
  const onDocDragLeave = useCallback((e) => {
    if (e.currentTarget === e.target) setDragOver(false);
  }, []);
  const onDocDrop = useCallback((e) => {
    if (!(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length)) return;
    e.preventDefault(); setDragOver(false);
    uploadDoc(e.dataTransfer.files[0]);
  }, [uploadDoc]);

  useEffect(() => {
    (async () => {
      try {
        const p = await window.api?.me?.profile?.();
        const prefs = p?.preferences || p?.profile?.preferences || {};
        const m = prefs['editor.write_mode'];
        if (m) setWriteMode(m);
        const ov = prefs['console_assistant_model_override'];
        if (ov && ov.model) setModel(ov.model);   // 底部标签显示当前编辑器 agent 模型
      } catch (_) { /* 默认 review */ }
    })();
  }, []);

  const changeWriteMode = useCallback(async (m) => {
    // PermissionPopover 有 4 档(read_only/default/review/full_access),后端 console_assistant 写权限只认
    // 3 档 → 把游戏里的 'default'(普通)归一到 'review'(审查后写),语义上最接近、且安全。
    const mm = m === 'default' ? 'review' : m;
    setWriteMode(mm);
    try { await window.api?.me?.preferences?.({ 'editor.write_mode': mm }); } catch (_) {}
  }, []);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  // 切剧本 / 刷新 → 先清空,再尝试从 localStorage 取回本剧本上次的对话 id 并拉回历史(刷新不丢)。
  const scriptIdRef = useRef(scriptId);
  scriptIdRef.current = scriptId;
  useEffect(() => {
    let cancelled = false;
    convIdRef.current = null;
    setMessages([]);
    if (!scriptId) return undefined;
    let cid = null;
    cid = lsGet(`mde.conv.${scriptId}`);
    if (!cid) return undefined;
    convIdRef.current = cid;
    (async () => {
      try {
        const res = await fetch(`/api/console_assistant/conversations/${encodeURIComponent(cid)}/messages`, { credentials: 'include' });
        if (cancelled || !res.ok) return;
        const j = await res.json();
        if (cancelled || !j?.ok || !Array.isArray(j.messages) || !j.messages.length) return;
        // 恢复工具调用历史(后端 ui_turns 现带 tools:名/参数/状态/结果)→ 折叠块可还原。
        setMessages(j.messages.map((m) => ({ role: m.role, text: m.text, tools: Array.isArray(m.tools) ? m.tools : [] })));
      } catch (_) { /* 还原失败静默,照常新会话 */ }
    })();
    return () => { cancelled = true; };
  }, [scriptId]);

  // ── 对话管理:新对话 / 历史列表 / 切换 / 删除(后端 console_assistant 早有 4 端点,编辑器此前没接) ──
  const [convList, setConvList] = useState(null);
  const [showConvList, setShowConvList] = useState(false);

  const loadConversation = useCallback(async (cid) => {
    convIdRef.current = cid || null;
    setMessages([]);
    if (scriptIdRef.current && cid) lsSet(`mde.conv.${scriptIdRef.current}`, cid);
    if (!cid) return;
    try {
      const j = await window.api?.consoleAssistant?.getMessages?.(cid);
      if (!j?.ok || !Array.isArray(j.messages)) return;
      setMessages(j.messages.map((m) => ({ role: m.role, text: m.text, tools: Array.isArray(m.tools) ? m.tools : [] })));
    } catch (_) { /* 静默 */ }
  }, []);

  const newConversation = useCallback(async () => {
    let nid = null;
    try { nid = (await window.api?.consoleAssistant?.newConversation?.())?.conversation_id || null; } catch (_) {}
    convIdRef.current = nid;
    if (scriptIdRef.current) {
      if (nid) lsSet(`mde.conv.${scriptIdRef.current}`, nid);
      else lsRemove(`mde.conv.${scriptIdRef.current}`);
    }
    setMessages([]);
    setShowConvList(false);
  }, []);

  const toggleConvList = useCallback(async () => {
    const next = !showConvList;
    setShowConvList(next);
    if (next) {
      try {
        const r = await window.api?.consoleAssistant?.listConversations?.();
        setConvList(Array.isArray(r?.items) ? r.items : []);
      } catch (_) { setConvList([]); }
    }
  }, [showConvList]);

  const deleteConv = useCallback(async (cid, e) => {
    try { e?.stopPropagation?.(); } catch (_) {}
    try { await window.api?.consoleAssistant?.deleteConversation?.(cid); } catch (_) {}
    setConvList((list) => (Array.isArray(list) ? list.filter((c) => c.id !== cid) : list));
    if (convIdRef.current === cid) {
      convIdRef.current = null;
      setMessages([]);
      if (scriptIdRef.current) lsRemove(`mde.conv.${scriptIdRef.current}`);
    }
  }, []);

  const pageContext = useCallback(() => {
    // 章节 tab 的 id 即 chapter_index(见 md-editor.jsx tree/create)。带上它,后端在弱模型漏填
    // chapter_index 时可确定性补默认(否则 update_script_chapter 必失败,新剧本写作流完全失效)。
    const openChapterIndex = (activeTab && activeTab.kind === 'chapter') ? activeTab.id : undefined;
    const open_file = activeTab
      ? `【${labelKind(activeTab.kind)}】「${activeTab.label}」(${activeTab.kind === 'chapter' ? `chapter_index=${activeTab.id}` : `${activeTab.kind} id=${activeTab.id}`})`
      : '(未打开具体文件)';
    const note = activeTab
      ? `用户正在剧本编辑器编辑剧本 #${scriptId} 的 ${open_file}。可用 update_*/upsert_* 工具直接改并落库`
        + (openChapterIndex != null ? `(改本章正文用 update_script_chapter(chapter_index=${openChapterIndex}, content=...))` : '')
        + `;改前先说清要改什么。`
      : `用户在剧本编辑器,当前剧本 #${scriptId},未打开具体文件。`;
    // tab:'md-editor' 是后端 build_system_prompt 注入编辑器上下文块的触发标记(光有 script_id 不够)。
    return { script_id: scriptId, tab: 'md-editor', open_file, note, open_chapter_index: openChapterIndex };
  }, [scriptId, activeTab]);

  // 统一 SSE 事件处理(chat 与 confirm 共用)。assistantIdx = 当前 assistant 消息下标。
  const makeHandler = useCallback((assistantIdx) => (event, data) => {
    if (event === 'meta') {
      if (data.conversation_id) {
        convIdRef.current = data.conversation_id;
        // 记住本剧本的对话 id,刷新后据此拉回历史。
        if (scriptIdRef.current) lsSet(`mde.conv.${scriptIdRef.current}`, data.conversation_id);
      }
      return;
    }
    if (event === 'token') {
      setMessages((m) => m.map((msg, i) => i === assistantIdx ? { ...msg, text: (msg.text || '') + (data.text || '') } : msg));
      return;
    }
    if (event === 'tool_call') {
      if (data.tool === 'report_writing_issues') issuesReportedRef.current = true;  // done 时通知父级重载问题面板
      setMessages((m) => m.map((msg, i) => i === assistantIdx
        ? { ...msg, tools: [...(msg.tools || []), { call_id: data.call_id, tool: data.tool, args: data.args, status: 'running' }] }
        : msg));
      return;
    }
    if (event === 'tool_result') {
      // 关键修复:后端 tool_call 事件不带 call_id、tool_result 不带 tool 名 → 原「按 tool/call_id 配对」
      // 永不命中 → 工具永远卡在「执行中」。工具按调用顺序串行执行,故把【最早一个仍 running 的】工具
      // 标完成(FIFO),既治卡死、又能正确对应顺序(重名工具如多次 get_script_character_card 不再错配)。
      setMessages((m) => m.map((msg, i) => {
        if (i !== assistantIdx) return msg;
        let patched = false;
        const tools = (msg.tools || []).map((tc) => {
          if (!patched && tc.status === 'running') {
            patched = true;
            return { ...tc, status: data.ok === false ? 'error' : 'done', result: data.result, error: data.error };
          }
          return tc;
        });
        return { ...msg, tools };
      }));
      // 写工具成功 → 刷新编辑器对应标签。用 ref 取最新 tryRefresh(makeHandler deps=[],否则捕获过期闭包→读到旧 tabs)。
      if (data.ok !== false) tryRefreshRef.current?.(data);
      return;
    }
    if (event === 'done') {
      // 兜底:流结束时把残留 running 的工具全标完成(防个别 result 丢失/不配对导致永久转圈)。
      setMessages((m) => m.map((msg, i) => i === assistantIdx
        ? { ...msg, tools: (msg.tools || []).map((tc) => tc.status === 'running' ? { ...tc, status: 'done' } : tc) }
        : msg));
      // 本次 run 有审稿问题落库 → 通知父级重载「问题」面板 + 顶栏徽标(此刻 executor 已 commit)。
      if (issuesReportedRef.current) { issuesReportedRef.current = false; try { onIssuesReportedRef.current?.(); } catch (_) {} }
      return;
    }
    if (event === 'confirmation_required') {
      const pc = { call_id: data.call_id, tool: data.tool, args: data.args, description: data.description, preview: data.preview };
      // 章节改写 → 优先在【中间编辑器】里内联 diff 审阅(绿增/红删 + 顶栏全部批准/拒绝)。
      // 仅当该章正是当前激活 tab 时拦截;否则(或异常)退回侧栏确认块。
      if (data.tool === 'update_script_chapter' && data.preview && data.preview.after != null
          && typeof onProposeChapterEditRef.current === 'function') {
        let ok = false;
        try {
          ok = onProposeChapterEditRef.current(data.args?.chapter_index, data.preview.after, {
            onAccept: () => confirmCallRef.current?.(pc.call_id, 'approve'),
            onReject: () => confirmCallRef.current?.(pc.call_id, 'reject'),
          });
        } catch (_) { ok = false; }
        if (ok) {
          // 编辑器接管审阅 → 聊天里只留一行轻提示,不挂 sidebar 确认块。
          setMessages((m) => m.map((msg, i) => i === assistantIdx
            ? { ...msg, pendingConfirm: null, editorDiff: { tool: pc.tool } }
            : msg));
          return;
        }
      }
      setMessages((m) => m.map((msg, i) => i === assistantIdx ? { ...msg, pendingConfirm: pc } : msg));
      return;
    }
    if (event === 'error') {
      setMessages((m) => m.map((msg, i) => i === assistantIdx ? { ...msg, error: data.message || t('components.md_editor_agent.error_generic') } : msg));
      return;
    }
    // done / navigation_required / context_usage 等:忽略或可扩展。
  }, []);

  const tryRefresh = useCallback((data) => {
    // data.result 里可能带回写入的实体信息;但最稳是从 tool_call 的 args 拿 id。
    // 这里通过遍历最近一条 assistant 的 tools 找到对应写工具的 args。
    setMessages((m) => {
      for (let i = m.length - 1; i >= 0; i--) {
        for (const tc of (m[i].tools || [])) {
          const map = WRITE_TOOL_MAP[tc.tool];
          if (map && (tc.call_id === data.call_id || tc.tool === data.tool)) {
            if (map.batch) { try { onWriteComplete?.(map.kind, null); } catch (_) {} }  // 批量:只刷新该组树
            else { const id = tc.args?.[map.idArg]; if (id != null) { try { onWriteComplete?.(map.kind, id); } catch (_) {} } }
          }
        }
      }
      return m;
    });
  }, [onWriteComplete]);
  const tryRefreshRef = useRef(null);
  tryRefreshRef.current = tryRefresh;

  // 一键撤销 agent 对某章的改动:调后端恢复改前全文 → 刷新编辑器标签 → 标记该工具已撤销。
  // 通用撤销:章节正文 / 世界书条目 / NPC 角色卡。info 由 _undoInfo(tc) 给出。
  const doUndo = useCallback(async (msgIdx, toolIdx, info) => {
    if (!scriptId || !info) return;
    try {
      const r = info.kind === 'chapter'
        ? await window.api?.scripts?.undoChapter?.(scriptId, info.ci)
        : await window.api?.scripts?.undoEdit?.(scriptId, info.table, info.id);
      if (r && r.ok) {
        window.__apiToast?.(t('components.md_editor_agent.undo_ok', { defaultValue: '已撤销,恢复为改前内容' }), { kind: 'ok' });
        setMessages((m) => m.map((msg, i) => i === msgIdx
          ? { ...msg, tools: (msg.tools || []).map((tc, j) => j === toolIdx ? { ...tc, undone: true } : tc) }
          : msg));
        try {
          if (info.kind === 'chapter') onWriteComplete?.('chapter', info.ci);
          else onWriteComplete?.(info.table === 'character_cards' ? 'card' : 'worldbook', info.id);
        } catch (_) {}
      } else {
        window.__apiToast?.((r && r.error) || t('components.md_editor_agent.undo_fail', { defaultValue: '撤销失败' }), { kind: 'error' });
      }
    } catch (_) {
      window.__apiToast?.(t('components.md_editor_agent.undo_fail', { defaultValue: '撤销失败' }), { kind: 'error' });
    }
  }, [scriptId, onWriteComplete, t]);

  const send = useCallback(async (text) => {
    const raw = (text ?? input).trim();
    if (!raw || busy) return;
    // 选区上下文:有选中且未关闭 → 把选中正文前置进【发送内容】(librarian 即知「这段/选中」指什么);
    // 界面气泡仍只显示用户原话,不让长正文淹没对话。
    let sentMsg = raw;
    if (selLen > 0 && !selCtxOff) {
      const sc = getSelectionContext?.();
      if (sc && sc.selection && sc.selection.trim()) {
        sentMsg = `[我在正文里选中的片段 —— 下文若说「这段 / 选中 / 这一段」即指它]\n"""\n${sc.selection.slice(0, 4000)}\n"""\n\n${raw}`;
      }
    }
    // @引用:把 pin 的实体前置成指示,让 agent 用对应工具优先读取(无需客户端搬运正文)。
    if (pinnedRefs.length) {
      const lines = pinnedRefs.map((r) => {
        if (r.kind === 'chapter') return `- ${r.label}(用 get_chapter_text(chapter_index=${r.id}) 读取)`;
        if (r.kind === 'card') return `- 角色卡「${r.label}」(用 get_script_character_card(card_id=${r.id}) 读取)`;
        return `- 世界书「${r.label}」(entry_id=${r.id};用 list_worldbook_entries 核对)`;
      });
      sentMsg = `[用户特别指定要参考以下内容,请【先用对应工具读取】再回答,优先级高于自行检索]\n${lines.join('\n')}\n\n${sentMsg}`;
      setPinnedRefs([]);
    }
    // 拖入文档:把 doc_id 前置到消息里,agent 用 preview_document_split/import_document_as_chapters/
    // read_uploaded_document 处理(原文不进上下文,只传引用)。发出后清除附件。
    if (attached && attached.doc_id) {
      sentMsg = `[用户拖入了文档「${attached.filename}」(doc_id=${attached.doc_id},约${attached.chars}字)。`
        + `如需拆章先 preview_document_split(doc_id) 预览再 import_document_as_chapters;其它指令可用 `
        + `read_uploaded_document(doc_id) 读取内容]\n\n${sentMsg}`;
      setAttached(null);
    }
    setInput('');
    setBusy(true);
    let assistantIdx = -1;
    setMessages((m) => {
      const next = [...m, { role: 'user', text: raw }, { role: 'assistant', text: '', tools: [] }];
      assistantIdx = next.length - 1;
      return next;
    });
    try {
      abortRef.current = new AbortController();
      const res = await fetch('/api/console_assistant/chat', {
        method: 'POST', credentials: 'include', signal: abortRef.current.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: sentMsg, conversation_id: convIdRef.current || undefined, page_context: pageContext() }),
      });
      await consumeSSE(res, makeHandler(assistantIdx));
    } catch (e) {
      setMessages((m) => m.map((msg2, i) => i === assistantIdx ? { ...msg2, error: e?.message || String(e) } : msg2));
    } finally { setBusy(false); abortRef.current = null; }
  }, [input, busy, pageContext, makeHandler, selLen, selCtxOff, getSelectionContext, attached, pinnedRefs]);

  // 按 call_id 直接发 /confirm(approve|reject)——既给 sidebar 确认块用,也给编辑器内联 diff 的
  // 「全部批准/拒绝」回调用(见 makeHandler 的 confirmation_required 拦截)。
  const confirmCall = useCallback(async (callId, decision) => {
    if (!callId || busy) return;
    setBusy(true);
    let assistantIdx = -1;
    setMessages((m) => { const next = [...m, { role: 'assistant', text: '', tools: [] }]; assistantIdx = next.length - 1; return next; });
    try {
      const res = await fetch('/api/console_assistant/confirm', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: convIdRef.current, call_id: callId, decision, page_context: pageContext() }),
      });
      await consumeSSE(res, makeHandler(assistantIdx));
    } catch (e) {
      setMessages((m) => m.map((msg2, i) => i === assistantIdx ? { ...msg2, error: e?.message || String(e) } : msg2));
    } finally { setBusy(false); }
  }, [busy, pageContext, makeHandler]);

  const resolveConfirm = useCallback(async (msgIdx, decision) => {
    const pc = messages[msgIdx]?.pendingConfirm;
    if (!pc || busy) return;
    setMessages((m) => m.map((msg, i) => i === msgIdx ? { ...msg, pendingConfirm: null } : msg));
    confirmCall(pc.call_id, decision);
  }, [messages, busy, confirmCall]);

  // makeHandler deps=[] → 用 ref 取最新 onProposeChapterEdit / confirmCall(避免过期闭包)。
  const onProposeChapterEditRef = useRef(null);
  onProposeChapterEditRef.current = onProposeChapterEdit;
  const onIssuesReportedRef = useRef(null);
  onIssuesReportedRef.current = onIssuesReported;
  const issuesReportedRef = useRef(false);  // 本次 run 内见到 report_writing_issues → done 时通知父级重载
  const confirmCallRef = useRef(null);
  confirmCallRef.current = confirmCall;

  // 「续写后同步到知识库」桥接:编辑器接受一段续写/改写后,可一键把这段正文丢给本 agent,
  // 让它按【写作准则·知识同步】(rule 4)读现状 + 同步角色卡/世界书/时间线/canon。
  useImperativeHandle(ref, () => ({
    syncFromProse(text, label, rewrite) {
      const t = (text || '').trim();
      if (!t || busy) return;
      const msg =
        `我刚在「${label || '正文'}」${rewrite ? '改写' : '续写'}了下面这段正文。` +
        '如果其中**真实地**引入或改变了某个角色的设定/状态/关系、某项世界设定、' +
        '或修正了某个既有时间线节点,请按【写作准则·知识同步】用对应工具同步到知识资产' +
        '(角色卡/世界书/时间线/canon);如果没有需要同步的,直接回「无需同步」即可。' +
        '不要编造正文里没有的内容。\n\n' +
        `${rewrite ? '改写后的正文' : '续写的正文'}:\n"""\n${t}\n"""`;
      send(msg);
    },
    // 复审本章(对标 Copilot /review):让 agent 读该章 + 对照设定逐项审,最后调 report_writing_issues
    // 把问题写进右栏「问题」面板(持久化、可跳转;沿用 Batch 6 整条管线,owner 路径已校验)。
    reviewChapter(chapterIndex, label) {
      if (busy || chapterIndex == null) return false;
      const n = chapterIndex;
      const msg =
        `请对「${label || `第${n}章`}」做一次责任编辑式通读审稿。步骤:` +
        `① 先用工具读取第 ${n} 章正文与相关设定(世界书/角色卡/canon/时间线/前情);` +
        '② 对照逐项检查:与设定或前文的事实矛盾、人物声音/性格漂移、埋下却未回收的伏笔、' +
        '节奏问题、POV/人称越界、重复啰嗦;' +
        `③ 【务必调用 report_writing_issues】把发现的问题结构化汇总,每条带 chapter=${n}、severity(高/中/低)、` +
        'type(类别)、detail(具体描述+修改建议)。' +
        '若通读后确无明显问题,也调用一次 report_writing_issues,传一条 severity=低、type=通读、' +
        'detail 说明「本章未发现明显问题」。只审不改,不要动正文。';
      send(msg);
      return true;
    },
  }), [send, busy]);

  return (
    <div className={'mde-agent' + (dragOver ? ' dragover' : '')}
         onDragOver={onDocDragOver} onDragLeave={onDocDragLeave} onDrop={onDocDrop}>
      {dragOver && (
        <div className="mde-agent-dropzone" aria-hidden="true">
          <div className="mde-agent-dropzone-inner">
            <div className="mde-agent-dropzone-icon">⤓</div>
            {t('components.md_editor_agent.doc.drop_here', { defaultValue: '松手上传 · txt / md 文档' })}
          </div>
        </div>
      )}
      <div className="mde-agent-head">
        <span className="mde-agent-head-icon">✎</span>
        <span className="mde-agent-head-title">{t('components.md_editor_agent.title')}{activeTab ? ` · ${activeTab.label}` : ''}</span>
        <button type="button" className={'mde-agent-headbtn' + (showConvList ? ' active' : '')}
          onClick={toggleConvList} title={t('components.md_editor_agent.conv.history', { defaultValue: '历史对话' })}>
          {t('components.md_editor_agent.conv.history', { defaultValue: '历史' })}
        </button>
        <button type="button" className="mde-agent-headbtn"
          onClick={newConversation} title={t('components.md_editor_agent.conv.new', { defaultValue: '新对话' })}>
          {t('components.md_editor_agent.conv.new', { defaultValue: '新对话' })}
        </button>
      </div>
      {showConvList && (
        <div className="mde-agent-convlist">
          {(convList || []).length === 0 ? (
            <div className="mde-agent-conv-empty">{t('components.md_editor_agent.conv.empty', { defaultValue: '暂无历史对话' })}</div>
          ) : (convList || []).map((c) => (
            <div key={c.id} role="button" tabIndex={0}
              className={'mde-agent-conv' + (c.id === convIdRef.current ? ' active' : '')}
              onClick={() => { setShowConvList(false); loadConversation(c.id); }}>
              <span className="mde-agent-conv-text">{stripEmoji(c.last_user_message || t('components.md_editor_agent.conv.untitled', { defaultValue: '(空对话)' }))}</span>
              <span className="mde-agent-conv-n">{c.message_count || 0}</span>
              <button type="button" className="mde-agent-conv-del"
                title={t('common.remove', { defaultValue: '删除' })}
                onClick={(e) => deleteConv(c.id, e)}>×</button>
            </div>
          ))}
        </div>
      )}
      {selLen > 0 && (
        <div className="mde-agent-selcard">
          <div className="mde-agent-selcard-head">
            <span className="mde-agent-selcard-title">{t('components.md_editor_agent.rewrite.selected', { n: selLen, defaultValue: '已选中 {{n}} 字' })}</span>
            <button
              type="button"
              className={'mde-agent-selcard-ctx' + (selCtxOff ? '' : ' on')}
              title={t('components.md_editor_agent.rewrite.ctx_tip', { defaultValue: '把选中正文作为下方聊天的上下文(助手即知「这段」指什么)' })}
              onClick={() => setSelCtxOff((v) => !v)}
            >
              {selCtxOff
                ? t('components.md_editor_agent.rewrite.ctx_off', { defaultValue: '○ 不带选中正文' })
                : t('components.md_editor_agent.rewrite.ctx_on', { defaultValue: '● 已带选中正文' })}
            </button>
          </div>
          {onContinue && (
            <div className="mde-agent-selcard-actions">
              {REWRITE_PRESETS.map((p) => (
                <button key={p.key} type="button" disabled={busy} onClick={() => onContinue(p.instruction)}>
                  {t(p.labelKey, { defaultValue: ({ tighten: '精简', expand: '扩写', polish: '润色', vivid: '画面感' })[p.key] })}
                </button>
              ))}
              <button
                type="button"
                className="mde-agent-selcard-custom"
                disabled={busy || !input.trim()}
                title={t('components.md_editor_agent.rewrite.by_instruction_tip', { defaultValue: '用下方输入框里的要求改写选中正文' })}
                onClick={() => { onContinue(input.trim()); setInput(''); }}
              >{t('components.md_editor_agent.rewrite.by_instruction', { defaultValue: '按指令改写 →' })}</button>
            </div>
          )}
        </div>
      )}
      <div className="mde-agent-msgs" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="mde-agent-empty">
            <span className="mde-agent-empty-glyph" aria-hidden="true">✎</span>
            <div className="mde-agent-empty-title">{t('components.md_editor_agent.empty_title', { defaultValue: '改这个剧本,直接动库' })}</div>
            <div className="mde-agent-empty-hint">{t('components.md_editor_agent.hint')}</div>
          </div>
        )}
        {messages.map((m, i) => (
          m.role === 'user' ? (
            <div key={i} className="mde-msg user">
              <div className="mde-msg-user">{stripEmoji(m.text)}</div>
            </div>
          ) : (
            <div key={i} className="mde-msg asst">
              {/* 工具调用在前(读/取/写过程),最终答复在后,与流的时序一致 */}
              {(m.tools || []).map((tc, j) => (
                (tc.tool === 'set_writing_plan' || tc.tool === 'report_writing_issues')
                  ? <MdeAgentPanel key={j} tc={tc} t={t} />
                  : <MdeToolBlock
                      key={j} tc={tc} t={t} busy={busy}
                      canUndo={!!_undoInfo(tc)}
                      onUndo={() => doUndo(i, j, _undoInfo(tc))}
                    />
              ))}
              {m.text ? (
                <div className="mde-msg-prose">
                  <RpgMarkdown.Block text={stripEmoji(m.text)} streaming={busy && i === messages.length - 1} />
                </div>
              ) : null}
              {m.pendingConfirm && (
                <div className="mde-agent-confirm">
                  <div className="mde-agent-confirm-q">
                    {t('components.md_editor_agent.confirm_prompt', { tool: toolLabel(m.pendingConfirm.tool) })}{m.pendingConfirm.description ? `:${m.pendingConfirm.description}` : ''}
                  </div>
                  {m.pendingConfirm.preview && <MdeWritePreview pv={m.pendingConfirm.preview} t={t} />}
                  <div className="mde-agent-confirm-btns">
                    <button className="ok" disabled={busy} onClick={() => resolveConfirm(i, 'approve')}>{t('components.md_editor_agent.confirm_approve')}</button>
                    <button className="no" disabled={busy} onClick={() => resolveConfirm(i, 'reject')}>{t('common.cancel')}</button>
                  </div>
                </div>
              )}
              {m.editorDiff && (
                <div className="mde-agent-editordiff">
                  {t('components.md_editor_agent.editor_diff_hint', { defaultValue: '改动已在编辑器中以绿(增)/红(删)展示 —— 在编辑器顶栏点「全部批准 / 拒绝」。' })}
                </div>
              )}
              {m.error && <div className="mde-tool-errline">{stripEmoji(m.error)}</div>}
            </div>
          )
        ))}
      </div>
      {refPicker && (
        <div className="mde-qopen-scrim" onMouseDown={() => setRefPicker(null)}>
          <div className="mde-refpick" onMouseDown={(e) => e.stopPropagation()}>
            <input className="mde-qopen-input" autoFocus value={refPicker.q}
              placeholder={t('components.md_editor_agent.ref.placeholder', { defaultValue: '引用:章节 / 角色卡 / 世界书…' })}
              onChange={(e) => setRefPicker((p) => p ? { ...p, q: e.target.value } : p)}
              onKeyDown={(e) => { if (e.key === 'Escape') setRefPicker(null); }} />
            <div className="mde-qopen-list">
              {refPicker.items === null ? <div className="mde-qopen-empty">{t('common.loading')}</div>
                : (() => {
                  const f = (refPicker.items || []).filter((it) => !refPicker.q || (it.label || '').toLowerCase().includes(refPicker.q.toLowerCase())).slice(0, 60);
                  return f.length === 0 ? <div className="mde-qopen-empty">{t('md_editor.quickopen.none', { defaultValue: '无匹配' })}</div>
                    : f.map((it) => (
                      <div key={it.kind + ':' + it.id} className="mde-qopen-item" onMouseDown={() => addRef(it)}>
                        <span className="mde-qopen-icon">{({ chapter: '§', card: '@', worldbook: '#' })[it.kind] || '·'}</span>
                        <span className="mde-qopen-label">{it.label}</span>
                      </div>
                    ));
                })()}
            </div>
          </div>
        </div>
      )}
      {/* 统一输入坞:工具条(续写 / 引用 / 写作技能)→ 引用chips → 附件 → 输入,一个容器一条边线 */}
      <div className="mde-agent-dock">
        <div className="mde-agent-tools" role="group" aria-label={t('components.md_editor_agent.skills.group', { defaultValue: '写作技能' })}>
          {onContinue && activeTab && (
            <button type="button" className="mde-agent-tool primary"
              title={t('components.md_editor_agent.continue_btn_title')}
              onClick={() => { onContinue(input.trim()); setInput(''); }}
            >{t('components.md_editor_agent.continue_btn')}<kbd className="mde-agent-kbd">⌘K</kbd></button>
          )}
          <button type="button" className="mde-agent-tool" onClick={openRefPicker} disabled={!scriptId}
            title={t('components.md_editor_agent.ref.add_tip', { defaultValue: '引用章节/角色卡/世界书作为上下文' })}
          >{t('components.md_editor_agent.ref.add', { defaultValue: '@ 引用' })}</button>
          {scriptId && activeTab && activeTab.kind === 'chapter' && (
            <>
              <span className="mde-agent-tools-sep" aria-hidden="true" />
              {WRITING_SKILLS.map((s) => (
                <button key={s.key} type="button" className="mde-agent-tool skill" disabled={busy}
                  onClick={() => send(s.prompt)}
                >{t('components.md_editor_agent.skills.' + s.key, { defaultValue: s.label })}</button>
              ))}
            </>
          )}
        </div>
        {(attached || uploading) && (
          <div className="mde-agent-attachbar">
            {uploading && <span className="mde-agent-attach-up">{t('components.md_editor_agent.doc.uploading', { defaultValue: '上传中…' })}</span>}
            {attached && (
              <span className="mde-agent-attach">
                <span className="mde-agent-attach-icon" aria-hidden="true">⎘</span>
                <span className="mde-agent-attach-name" title={attached.filename}>{attached.filename}</span>
                <span className="mde-agent-attach-chars">{attached.chars}{t('components.md_editor_agent.doc.chars_suffix', { defaultValue: '字' })}</span>
                <button type="button" className="mde-agent-attach-x" onClick={() => setAttached(null)}
                  title={t('common.remove', { defaultValue: '移除' })} aria-label={t('common.remove', { defaultValue: '移除' })}>×</button>
              </span>
            )}
          </div>
        )}
        {pinnedRefs.length > 0 && (
          <div className="mde-agent-refbar">
            {pinnedRefs.map((r) => (
              <span key={r.kind + ':' + r.id} className="mde-agent-refchip">
                <span className="mde-agent-refchip-k">{({ chapter: '章', card: '卡', worldbook: '书' })[r.kind] || ''}</span>
                <span className="mde-agent-refchip-l" title={r.label}>{r.label}</span>
                <button type="button" className="mde-agent-refchip-x" onClick={() => removeRef(r)} aria-label={t('common.remove', { defaultValue: '移除' })}>×</button>
              </span>
            ))}
          </div>
        )}
        <div className="mde-agent-composer">
        <Composer
          text={input}
          setText={setInput}
          onSend={() => send()}
          onStop={() => { try { abortRef.current?.abort(); } catch (_) {} }}
          running={busy}
          permission={writeMode}
          setPermission={changeWriteMode}
          showPerm={showPerm}
          togglePerm={togglePerm}
          permissionOptions={EDITOR_PERMS}
          enterToSendKey="rpg.editor.enterToSend"
          gameState={null}
          model={model}
          setModel={setModel}
          showModel={showModel}
          toggleModel={toggleModel}
          modelPersist={EDITOR_MODEL_PERSIST}
          hideSlash
          hideAttach
          hideContinue
          hideImageGen
          hideContextUsage
          placeholder={scriptId ? t('components.md_editor_agent.placeholder_with_script') : t('components.md_editor_agent.placeholder_no_script')}
        />
        </div>
      </div>
    </div>
  );
});

export default MdEditorAgent;

function labelKind(kind) {
  return ({ chapter: '章节正文', card: '角色卡', worldbook: '世界书', anchor: '时间线锚点', canon: 'Canon 实体' })[kind] || kind;
}
