// MdEditorAgent.jsx — MD 编辑器右栏 AI 助手。复用后端 console_assistant(SSE + 工具循环 + 二次确认)。
// 把当前剧本 + 打开文件作为 page_context 传入,LLM 可用 script 级直写工具改库;destructive 工具走二次确认;
// 写成功后回调 onWriteComplete 让编辑器刷新对应标签。设计 docs/design/N_md_editor.md §5。
import React from 'react';

const { useState, useRef, useCallback, useEffect, forwardRef, useImperativeHandle } = React;

// 写工具名 → (kind, id-arg-key):写成功后据此刷新编辑器标签。
const WRITE_TOOL_MAP = {
  update_script_chapter: { kind: 'chapter', idArg: 'chapter_index' },
  upsert_worldbook_entry: { kind: 'worldbook', idArg: 'entry_id' },
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

const MdEditorAgent = forwardRef(function MdEditorAgent({ scriptId, activeTab, onWriteComplete, onContinue }, ref) {
  const [messages, setMessages] = useState([]);   // [{role, text, tools:[{call_id,tool,args,status,result}]}]
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const convIdRef = useRef(null);
  const scrollRef = useRef(null);
  const abortRef = useRef(null);
  // 三级权限(Q3):AI 改库的写入权限 read_only / review(默认) / full_access。持久化 editor.write_mode。
  const [writeMode, setWriteMode] = useState('review');

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
    setWriteMode(m);
    try { await window.api?.me?.preferences?.({ 'editor.write_mode': m }); } catch (_) {}
  }, []);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  // 新剧本 → 重置会话。
  useEffect(() => { convIdRef.current = null; setMessages([]); }, [scriptId]);

  const pageContext = useCallback(() => {
    const open_file = activeTab
      ? `【${labelKind(activeTab.kind)}】「${activeTab.label}」(${activeTab.kind} id=${activeTab.id})`
      : '(未打开具体文件)';
    const note = activeTab
      ? `用户正在 MD 编辑器编辑剧本 #${scriptId} 的 ${open_file}。可用 update_*/upsert_* 工具直接改并落库;改前先说清要改什么。`
      : `用户在 MD 编辑器,当前剧本 #${scriptId},未打开具体文件。`;
    // tab:'md-editor' 是后端 build_system_prompt 注入编辑器上下文块的触发标记(光有 script_id 不够)。
    return { script_id: scriptId, tab: 'md-editor', open_file, note };
  }, [scriptId, activeTab]);

  // 统一 SSE 事件处理(chat 与 confirm 共用)。assistantIdx = 当前 assistant 消息下标。
  const makeHandler = useCallback((assistantIdx) => (event, data) => {
    if (event === 'meta') { if (data.conversation_id) convIdRef.current = data.conversation_id; return; }
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
      setMessages((m) => m.map((msg, i) => i === assistantIdx
        ? { ...msg, tools: (msg.tools || []).map((tc) => tc.tool === data.tool || tc.call_id === data.call_id
            ? { ...tc, status: data.ok === false ? 'error' : 'done', result: data.result, error: data.error } : tc) }
        : msg));
      // 写工具成功 → 刷新编辑器对应标签。
      if (data.ok !== false) tryRefresh(data);
      return;
    }
    if (event === 'confirmation_required') {
      setMessages((m) => m.map((msg, i) => i === assistantIdx
        ? { ...msg, pendingConfirm: { call_id: data.call_id, tool: data.tool, args: data.args, description: data.description } }
        : msg));
      return;
    }
    if (event === 'error') {
      setMessages((m) => m.map((msg, i) => i === assistantIdx ? { ...msg, error: data.message || '出错了' } : msg));
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
            const id = tc.args?.[map.idArg];
            if (id != null) { try { onWriteComplete?.(map.kind, id); } catch (_) {} }
          }
        }
      }
      return m;
    });
  }, [onWriteComplete]);

  const send = useCallback(async (text) => {
    const msg = (text ?? input).trim();
    if (!msg || busy) return;
    setInput('');
    setBusy(true);
    let assistantIdx = -1;
    setMessages((m) => {
      const next = [...m, { role: 'user', text: msg }, { role: 'assistant', text: '', tools: [] }];
      assistantIdx = next.length - 1;
      return next;
    });
    try {
      abortRef.current = new AbortController();
      const res = await fetch('/api/console_assistant/chat', {
        method: 'POST', credentials: 'include', signal: abortRef.current.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, conversation_id: convIdRef.current || undefined, page_context: pageContext() }),
      });
      await consumeSSE(res, makeHandler(assistantIdx));
    } catch (e) {
      setMessages((m) => m.map((msg2, i) => i === assistantIdx ? { ...msg2, error: e?.message || String(e) } : msg2));
    } finally { setBusy(false); abortRef.current = null; }
  }, [input, busy, pageContext, makeHandler]);

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
    <div className="mde-agent">
      <div className="mde-agent-head">
        <span className="mde-agent-head-title">AI 助手{activeTab ? ` · ${activeTab.label}` : ''}</span>
        <select
          className="mde-agent-wmode"
          value={writeMode}
          title="AI 改库的写入权限:只读=只给建议不写;审查=写前要你确认;直接写=AI 直接落库"
          onChange={(e) => changeWriteMode(e.target.value)}
        >
          <option value="read_only">只读建议</option>
          <option value="review">审查后写</option>
          <option value="full_access">直接写</option>
        </select>
      </div>
      <div className="mde-agent-msgs" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="mde-agent-hint">
            让 AI 帮你改这个剧本 —— 例如「把这个角色的性格改得更阴郁」「给这章正文润色」「新建一条世界书:XX」。
            AI 会直接改库(危险操作会先让你确认)。
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={'mde-agent-msg ' + m.role}>
            {m.text && <div className="mde-agent-text">{m.text}</div>}
            {(m.tools || []).map((tc, j) => (
              <div key={j} className={'mde-agent-tool ' + tc.status}>
                <span className="mde-agent-tool-name">{tc.tool}</span>
                <span className="mde-agent-tool-status">{tc.status === 'running' ? '执行中…' : tc.status === 'error' ? '失败' : '完成'}</span>
                {tc.error && <div className="mde-agent-tool-err">{tc.error}</div>}
              </div>
            ))}
            {m.pendingConfirm && (
              <div className="mde-agent-confirm">
                <div className="mde-agent-confirm-q">
                  AI 想执行 <b>{m.pendingConfirm.tool}</b>(覆盖写入){m.pendingConfirm.description ? `:${m.pendingConfirm.description}` : ''}。确认?
                </div>
                <div className="mde-agent-confirm-btns">
                  <button className="ok" disabled={busy} onClick={() => resolveConfirm(i, 'approve')}>确认执行</button>
                  <button className="no" disabled={busy} onClick={() => resolveConfirm(i, 'reject')}>取消</button>
                </div>
              </div>
            )}
            {m.error && <div className="mde-agent-tool-err">{m.error}</div>}
          </div>
        ))}
      </div>
      {onContinue && activeTab && (
        <div className="mde-agent-toolbar">
          <button
            className="mde-agent-continue"
            title="把输入框作为指令(可空),让 AI 在当前正文光标处续写 / 选中段改写。也可在编辑器里按 ⌘K。"
            onClick={() => { onContinue(input.trim()); setInput(''); }}
          >续写到正文(光标处)</button>
          <span className="mde-agent-toolbar-hint">或在正文按 ⌘K</span>
        </div>
      )}
      <div className="mde-agent-input">
        <textarea
          value={input}
          placeholder={scriptId ? '让 AI 改这个剧本… / 或写续写指令配合上方按钮' : '先选剧本'}
          disabled={!scriptId || busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); } }}
        />
        <button disabled={!scriptId || busy || !input.trim()} onClick={() => send()}>{busy ? '…' : '发送'}</button>
      </div>
    </div>
  );
});

export default MdEditorAgent;

function labelKind(kind) {
  return ({ chapter: '章节正文', card: '角色卡', worldbook: '世界书', anchor: '时间线锚点', canon: 'Canon 实体' })[kind] || kind;
}
