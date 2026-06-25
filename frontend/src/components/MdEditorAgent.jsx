// MdEditorAgent.jsx — MD 编辑器右栏 AI 助手。复用后端 console_assistant(SSE + 工具循环 + 二次确认)。
// 把当前剧本 + 打开文件作为 page_context 传入,LLM 可用 script 级直写工具改库;destructive 工具走二次确认;
// 写成功后回调 onWriteComplete 让编辑器刷新对应标签。设计 docs/design/N_md_editor.md §5。
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Composer } from '../game-composer.jsx';
import { RpgMarkdown } from '../markdown-render.jsx';
import AgentModelPicker from './AgentModelPicker.jsx';

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

function parseSSEChunk(raw) {
  let event = 'message';
  let data = '';
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).replace(/^ /, '');
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; } catch (_) { return { event, data: {} }; }
}

async function consumeSSE(res, onEvent) {
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const ev = parseSSEChunk(buf.slice(0, i));
      buf = buf.slice(i + 2);
      if (ev) onEvent(ev.event, ev.data);
    }
  }
}

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

const MdEditorAgent = forwardRef(function MdEditorAgent({ scriptId, activeTab, onWriteComplete, onContinue, selLen = 0, getSelectionContext }, ref) {
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
    try { cid = localStorage.getItem(`mde.conv.${scriptId}`); } catch (_) {}
    if (!cid) return undefined;
    convIdRef.current = cid;
    (async () => {
      try {
        const res = await fetch(`/api/console_assistant/conversations/${encodeURIComponent(cid)}/messages`, { credentials: 'include' });
        if (cancelled || !res.ok) return;
        const j = await res.json();
        if (cancelled || !j?.ok || !Array.isArray(j.messages) || !j.messages.length) return;
        setMessages(j.messages.map((m) => ({ role: m.role, text: m.text, tools: [] })));
      } catch (_) { /* 还原失败静默,照常新会话 */ }
    })();
    return () => { cancelled = true; };
  }, [scriptId]);

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
        try { if (scriptIdRef.current) localStorage.setItem(`mde.conv.${scriptIdRef.current}`, data.conversation_id); } catch (_) {}
      }
      return;
    }
    if (event === 'token') {
      setMessages((m) => m.map((msg, i) => i === assistantIdx ? { ...msg, text: (msg.text || '') + (data.text || '') } : msg));
      return;
    }
    if (event === 'tool_call') {
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
      return;
    }
    if (event === 'confirmation_required') {
      setMessages((m) => m.map((msg, i) => i === assistantIdx
        ? { ...msg, pendingConfirm: { call_id: data.call_id, tool: data.tool, args: data.args, description: data.description, preview: data.preview } }
        : msg));
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
  const undoChapterEdit = useCallback(async (msgIdx, toolIdx, ci) => {
    if (!scriptId || ci == null) return;
    try {
      const r = await window.api?.scripts?.undoChapter?.(scriptId, ci);
      if (r && r.ok) {
        window.__apiToast?.(t('components.md_editor_agent.undo_ok', { defaultValue: '已撤销,正文已恢复改前内容' }), { kind: 'ok' });
        setMessages((m) => m.map((msg, i) => i === msgIdx
          ? { ...msg, tools: (msg.tools || []).map((tc, j) => j === toolIdx ? { ...tc, undone: true } : tc) }
          : msg));
        try { onWriteComplete?.('chapter', ci); } catch (_) {}
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
  }, [input, busy, pageContext, makeHandler, selLen, selCtxOff, getSelectionContext, attached]);

  const resolveConfirm = useCallback(async (msgIdx, decision) => {
    const pc = messages[msgIdx]?.pendingConfirm;
    if (!pc || busy) return;
    setBusy(true);
    setMessages((m) => m.map((msg, i) => i === msgIdx ? { ...msg, pendingConfirm: null } : msg));
    let assistantIdx = -1;
    setMessages((m) => { const next = [...m, { role: 'assistant', text: '', tools: [] }]; assistantIdx = next.length - 1; return next; });
    try {
      const res = await fetch('/api/console_assistant/confirm', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ conversation_id: convIdRef.current, call_id: pc.call_id, decision, page_context: pageContext() }),
      });
      await consumeSSE(res, makeHandler(assistantIdx));
    } catch (e) {
      setMessages((m) => m.map((msg2, i) => i === assistantIdx ? { ...msg2, error: e?.message || String(e) } : msg2));
    } finally { setBusy(false); }
  }, [messages, busy, pageContext, makeHandler]);

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
        <span className="mde-agent-head-icon">AI</span>
        <span className="mde-agent-head-title">{t('components.md_editor_agent.title')}{activeTab ? ` · ${activeTab.label}` : ''}</span>
      </div>
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
          <div className="mde-agent-hint">
            {t('components.md_editor_agent.hint')}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={'mde-agent-msg ' + m.role}>
            <div className="mde-agent-avatar" aria-hidden="true">{m.role === 'user' ? '你' : 'AI'}</div>
            <div className="mde-agent-body">
            {/* 工具调用在前(agent 先读后写,这些是「思考/取数」过程),最终答复在后,符合流的时序 */}
            {(m.tools || []).map((tc, j) => (
              (tc.tool === 'set_writing_plan' || tc.tool === 'report_writing_issues') ? (
                <MdeAgentPanel key={j} tc={tc} t={t} />
              ) : (
              <div key={j} className={'mde-agent-tool ' + tc.status}>
                <span className="mde-agent-tool-dot" aria-hidden="true" />
                <span className="mde-agent-tool-name">{toolLabel(tc.tool)}</span>
                <span className="mde-agent-tool-status">{tc.status === 'running' ? t('components.md_editor_agent.tool_status.running') : tc.status === 'error' ? t('components.md_editor_agent.tool_status.error') : t('components.md_editor_agent.tool_status.done')}</span>
                {tc.tool === 'update_script_chapter' && tc.status === 'done' && tc.args?.chapter_index != null && (
                  tc.undone
                    ? <span className="mde-agent-undone">{t('components.md_editor_agent.undone', { defaultValue: '已撤销' })}</span>
                    : <button type="button" className="mde-agent-undo" disabled={busy}
                        onClick={() => undoChapterEdit(i, j, tc.args.chapter_index)}>
                        {t('components.md_editor_agent.undo_btn', { defaultValue: '撤销此次改动' })}
                      </button>
                )}
                {tc.error && <div className="mde-agent-tool-err">{tc.error}</div>}
              </div>
              )
            ))}
            {m.text && (m.role === 'assistant'
              ? <div className="mde-agent-text"><RpgMarkdown.Block text={m.text} streaming={busy && i === messages.length - 1} /></div>
              : <div className="mde-agent-text">{m.text}</div>)}
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
            {m.error && <div className="mde-agent-tool-err">{m.error}</div>}
            </div>
          </div>
        ))}
      </div>
      {scriptId && activeTab && activeTab.kind === 'chapter' && (
        <div className="mde-agent-skills" role="group" aria-label={t('components.md_editor_agent.skills.group', { defaultValue: '写作技能' })}>
          <span className="mde-agent-skills-label">{t('components.md_editor_agent.skills.group', { defaultValue: '写作技能' })}</span>
          {WRITING_SKILLS.map((s) => (
            <button
              key={s.key}
              type="button"
              className="mde-agent-skill"
              disabled={busy}
              onClick={() => send(s.prompt)}
            >{t('components.md_editor_agent.skills.' + s.key, { defaultValue: s.label })}</button>
          ))}
        </div>
      )}
      {onContinue && activeTab && (
        <div className="mde-agent-toolbar">
          <button
            className="mde-agent-continue"
            title={t('components.md_editor_agent.continue_btn_title')}
            onClick={() => { onContinue(input.trim()); setInput(''); }}
          >{t('components.md_editor_agent.continue_btn')}</button>
          <span className="mde-agent-toolbar-hint">{t('components.md_editor_agent.continue_hint')}</span>
        </div>
      )}
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
      {scriptId && (
        <div className="mde-agent-modelbar">
          <span className="mde-agent-modelbar-label">{t('components.md_editor_agent.model_label', { defaultValue: '模型' })}</span>
          <AgentModelPicker
            variant="popover"
            persistShape="dict"
            dictKey="console_assistant_model_override"
            allowInherit
            inheritLabel={t('components.md_editor_agent.model_inherit', { defaultValue: '跟随默认模型' })}
            configHash="settings-models"
          />
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
          hideSlash
          hideAttach
          hideContinue
          hideImageGen
          hideModel
          hideContextUsage
          placeholder={scriptId ? t('components.md_editor_agent.placeholder_with_script') : t('components.md_editor_agent.placeholder_no_script')}
        />
      </div>
    </div>
  );
});

export default MdEditorAgent;

function labelKind(kind) {
  return ({ chapter: '章节正文', card: '角色卡', worldbook: '世界书', anchor: '时间线锚点', canon: 'Canon 实体' })[kind] || kind;
}
