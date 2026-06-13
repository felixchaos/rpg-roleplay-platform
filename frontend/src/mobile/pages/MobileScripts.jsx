/* MobileScripts — 移动端剧本区(scripts / scripts-import / scripts-library)
   对应电脑端 src/pages/scripts.jsx 的 ScriptsListView / ScriptsImportView / ScriptsLibraryView。
   铁律:
   - 无任何 CS* / Cloudscape / game-app / game-panels / pages/*.jsx UI 组件导入
   - 数据层复用 window.api.* + usePlatformData
   - 单文件,三视图 (list / import / library) + 子视图 (detail / chapters / worldbook / npc / timeline)
   - 样式只用 mobile.css 已有 class + inline style + 返回 neededCss 中的新 class */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Icon } from '../icons.jsx';
import { usePlatformData } from '../../platform-app.jsx';

/* ─── 小工具 ─────────────────────────────────────── */
const fmtWan = (w) => {
  const n = Number(w) || 0;
  return n >= 10000
    ? (n / 10000).toFixed(n >= 100000 ? 0 : 1).replace(/\.0$/, '') + ' 万字'
    : n > 0 ? n + ' 字' : '—';
};
const fmtN = (n) => (n == null ? '—' : Number(n).toLocaleString());

const ACTIVE_STATUSES = new Set(['queued', 'pending', 'running', 'processing', 'importing', 'started']);
const TERMINAL_STATUSES = new Set(['done', 'done_with_errors', 'partial', 'failed', 'cancelled']);
const SPLIT_RULES = [
  { id: 'auto',       label: '自动识别(推荐)' },
  { id: 'corpus',     label: '语料库模式' },
  { id: 'chapter_cn', label: '中文章节标题' },
  { id: 'chapter_en', label: '英文章节标题' },
  { id: 'number_dot', label: '数字点号规则' },
  { id: 'paren_num',  label: '括号数字规则' },
  { id: 'custom',     label: '自定义正则' },
];

function isPlayBlocked(s) {
  if (!s) return '';
  const status = String(
    s.import_status || s.job_status || s.active_job?.status || s.readiness?.active_job?.status || ''
  ).toLowerCase();
  if (status && ACTIVE_STATUSES.has(status) && !TERMINAL_STATUSES.has(status)) return '正在导入中，请稍候';
  const missing = Array.isArray(s.readiness?.missing) ? s.readiness.missing : [];
  const BLOCKING = new Set(['chunks', 'anchors']);
  const blocked = missing.filter(k => BLOCKING.has(k));
  if (blocked.length) return `缺少必要数据：${blocked.join('、')}`;
  if (Number(s.chapter_count || 0) <= 0) return '章节数据缺失，请先完成导入';
  return '';
}

/* ─── 通用空态 ─────────────────────────────────── */
function EmptyState({ icon = 'book_open', title, desc, action }) {
  return (
    <div className="pl-empty">
      <div className="ic"><Icon name={icon} size={24} /></div>
      <h3>{title}</h3>
      {desc && <p>{desc}</p>}
      {action}
    </div>
  );
}

/* ─── 章节列表子视图 ──────────────────────────── */
function ChaptersView({ script, onBack }) {
  const [chapters, setChapters] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeIdx, setActiveIdx] = useState(0);
  const [activeContent, setActiveContent] = useState('');
  const [activeLoading, setActiveLoading] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!script) return;
    setLoading(true); setErr('');
    (async () => {
      try {
        const r = await window.api.scripts.chapters(script.id, { limit: 5000 });
        setChapters((r && (r.chapters || r.items)) || []);
      } catch (e) {
        setErr(e?.message || '加载失败');
      } finally { setLoading(false); }
    })();
  }, [script?.id, reloadTick]);

  useEffect(() => {
    if (!script || chapters.length === 0) { setActiveContent(''); return; }
    const cur = chapters[activeIdx];
    if (!cur) { setActiveContent(''); return; }
    const chIdx = cur.chapter_index ?? cur.index ?? activeIdx;
    let cancelled = false;
    setActiveLoading(true);
    (async () => {
      try {
        const r = await window.api.scripts.chapterDetail(script.id, chIdx);
        if (!cancelled) setActiveContent((r?.chapter?.content) || '');
      } catch (_) {
        if (!cancelled) setActiveContent(cur.content_preview || '');
      } finally { if (!cancelled) setActiveLoading(false); }
    })();
    return () => { cancelled = true; };
  }, [script?.id, activeIdx, chapters]);

  const cur = chapters[activeIdx];
  const curIdx = cur ? (cur.chapter_index ?? cur.index ?? activeIdx) : activeIdx;

  const onRename = async () => {
    if (!cur) return;
    const newTitle = window.prompt('新标题', cur.title || '');
    if (!newTitle || newTitle === cur.title) return;
    try {
      await window.api.scripts.updateChapter(script.id, curIdx, { title: newTitle });
      window.__apiToast?.('已重命名', { kind: 'ok' });
      setReloadTick(x => x + 1);
    } catch (e) { window.__apiToast?.(e?.message || '操作失败', { kind: 'danger' }); }
  };

  const onMergeNext = async () => {
    if (!cur || activeIdx >= chapters.length - 1) return;
    if (!window.confirm(`确认合并第 ${activeIdx + 1} 章与第 ${activeIdx + 2} 章？`)) return;
    try {
      const nextCh = chapters[activeIdx + 1];
      const nextIdx = nextCh ? (nextCh.chapter_index ?? nextCh.index ?? (activeIdx + 1)) : (activeIdx + 1);
      await window.api.scripts.mergeChapter(script.id, { first_index: curIdx, second_index: nextIdx });
      window.__apiToast?.('已合并', { kind: 'ok' });
      setReloadTick(x => x + 1);
    } catch (e) { window.__apiToast?.(e?.message || '操作失败', { kind: 'danger' }); }
  };
  // 合并上一章:把前面那章折进当前章,保留当前章标题(序章/前言折进第一章)。
  const onMergePrev = async () => {
    if (!cur || activeIdx <= 0) return;
    if (!window.confirm(`把第 ${activeIdx} 章合并进当前第 ${activeIdx + 1} 章(保留当前标题)？`)) return;
    try {
      const prevCh = chapters[activeIdx - 1];
      const prevIdx = prevCh ? (prevCh.chapter_index ?? prevCh.index ?? (activeIdx - 1)) : (activeIdx - 1);
      await window.api.scripts.mergeChapter(script.id, { first_index: prevIdx, second_index: curIdx, keep_title_index: curIdx });
      window.__apiToast?.('已合并', { kind: 'ok' });
      setReloadTick(x => x + 1);
    } catch (e) { window.__apiToast?.(e?.message || '操作失败', { kind: 'danger' }); }
  };

  const onResplit = async () => {
    const rule = window.prompt('重切分规则 (auto/chapter_cn/chapter_en/number_dot)', 'auto');
    if (!rule) return;
    try {
      await window.api.scripts.resplit(script.id, { split_rule: rule });
      window.__apiToast?.('已重新切分', { kind: 'ok' });
      setReloadTick(x => x + 1);
    } catch (e) { window.__apiToast?.(e?.message || '操作失败', { kind: 'danger' }); }
  };

  if (loading) {
    return (
      <>
        <div className="pl-head">
          <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
          <div className="pl-head-title"><strong>章节列表</strong></div>
        </div>
        <div className="pl-body"><div className="pl-pad"><div className="muted" style={{ fontSize: 13 }}>加载中…</div></div></div>
      </>
    );
  }

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title">
          <strong>章节列表</strong>
          <span className="sub">{chapters.length} 章 · 第 {activeIdx + 1} 章</span>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={onResplit} title="重新切分"><Icon name="refresh" size={18} /></button>
        </div>
      </div>
      <div className="pl-body tabbed">
        {err && <div style={{ padding: '12px 16px', color: 'var(--danger)', fontSize: 13 }}>{err}</div>}
        {chapters.length === 0 ? (
          <div className="pl-pad"><EmptyState icon="file" title="暂无章节" desc="导入剧本后将在此显示章节列表" /></div>
        ) : (
          <div style={{ display: 'grid', gridTemplateRows: '1fr', height: '100%' }}>
            {/* 章节选择器 */}
            <div style={{ overflowX: 'auto', display: 'flex', gap: 6, padding: '10px 16px 0', borderBottom: '1px solid var(--line-soft)' }} className="scroll">
              {chapters.map((c, i) => (
                <button
                  key={c.chapter_index ?? c.index ?? i}
                  onClick={() => setActiveIdx(i)}
                  style={{
                    flex: 'none', height: 32, padding: '0 12px', borderRadius: 999,
                    fontSize: 12, whiteSpace: 'nowrap',
                    background: i === activeIdx ? 'var(--accent-soft)' : 'var(--panel)',
                    color: i === activeIdx ? 'var(--accent)' : 'var(--muted)',
                    border: `1px solid ${i === activeIdx ? 'var(--accent-edge)' : 'var(--line-soft)'}`,
                  }}
                >
                  <span className="mono" style={{ fontSize: 11 }}>#{String(i + 1).padStart(3, '0')}</span>
                  {' '}
                  <span style={{ maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis', display: 'inline-block', verticalAlign: 'middle' }}>
                    {c.title || '无标题'}
                  </span>
                </button>
              ))}
            </div>
            {/* 章节正文 */}
            {cur && (
              <div className="pl-pad" style={{ overflow: 'auto' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
                  <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 15 }}>{cur.title || '无标题'}</strong>
                  <span className="mono muted-2" style={{ fontSize: 11 }}>{fmtN(cur.word_count || 0)} 字</span>
                  <div style={{ marginLeft: 'auto', display: 'flex', gap: 7 }}>
                    <button className="pl-pill" style={{ height: 28 }} onClick={onRename}><Icon name="edit" size={13} /> 改名</button>
                    {activeIdx > 0 && (
                      <button className="pl-pill" style={{ height: 28 }} onClick={onMergePrev}><Icon name="link" size={13} /> 合并上章</button>
                    )}
                    {activeIdx < chapters.length - 1 && (
                      <button className="pl-pill" style={{ height: 28 }} onClick={onMergeNext}><Icon name="link" size={13} /> 合并下章</button>
                    )}
                  </div>
                </div>
                <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'var(--font-serif)', fontSize: 13.5, lineHeight: 1.75, margin: 0, color: 'var(--text-quiet)' }}>
                  {activeLoading
                    ? (cur.content_preview || '') + '\n\n加载全文中…'
                    : ((activeContent || cur.content_preview || '').slice(0, 8000) + ((activeContent?.length > 8000) ? '\n\n[正文过长，已截断前 8000 字]' : ''))}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}

/* ─── 世界书子视图 ─────────────────────────────── */
function WorldbookView({ script, onBack }) {
  const [entries, setEntries] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!script) return;
    (async () => {
      try {
        const r = await window.api.scripts.worldbook(script.id);
        setEntries(Array.isArray(r) ? r : (r?.items || r?.entries || []));
      } catch (_) { setEntries([]); }
      finally { setLoading(false); }
    })();
  }, [script?.id]);

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title center">
          <strong>世界书</strong>
          <span className="sub">{script?.title}</span>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading && <div className="muted" style={{ fontSize: 13 }}>加载中…</div>}
          {!loading && (!entries || entries.length === 0) && (
            <EmptyState icon="world" title="暂无世界书条目" desc="导入并抽取后将自动生成" />
          )}
          {!loading && entries && entries.map((e, i) => (
            <div key={e.id || i} className="pl-card" style={{ marginBottom: 9 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 14.5, color: 'var(--text)' }}>
                  {e.key || e.keys || e.title || e.keyword || `条目 #${i + 1}`}
                </strong>
                <span className={'pill ' + (e.enabled !== false ? 'ok' : '')} style={{ height: 20, fontSize: 10 }}>
                  <span className={'dot ' + (e.enabled !== false ? 'ok' : '')} />
                  {e.enabled !== false ? '激活' : '休眠'}
                </span>
              </div>
              <p style={{ margin: 0, fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.6 }}>
                {e.content || e.value || e.text || '—'}
              </p>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/* ─── NPC 子视图 ──────────────────────────────── */
function NpcView({ script, onBack }) {
  const [npcs, setNpcs] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!script) return;
    (async () => {
      try {
        const r = await window.api.cards.scriptList(script.id);
        setNpcs(Array.isArray(r) ? r : (r?.items || r?.cards || []));
      } catch (_) { setNpcs([]); }
      finally { setLoading(false); }
    })();
  }, [script?.id]);

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title center">
          <strong>NPC 角色卡</strong>
          <span className="sub">{script?.title}</span>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading && <div className="muted" style={{ fontSize: 13 }}>加载中…</div>}
          {!loading && (!npcs || npcs.length === 0) && (
            <EmptyState icon="cards" title="暂无 NPC 角色卡" desc="导入抽取后自动生成" />
          )}
          {!loading && npcs && npcs.map((c, i) => (
            <div key={c.id || i} className="pl-card" style={{ marginBottom: 9 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 6 }}>
                <div style={{ width: 36, height: 36, borderRadius: 11, display: 'grid', placeItems: 'center', background: 'var(--panel-3)', border: '1px solid var(--line)', fontFamily: 'var(--font-serif)', fontSize: 16, color: 'var(--accent)' }}>
                  {(c.name || '?').slice(0, 1)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontFamily: 'var(--font-serif)', fontSize: 14.5, color: 'var(--text)' }}>
                    {c.name || '未命名'}
                    {c.metadata?.is_protagonist && (
                      <span className="pill accent" style={{ marginLeft: 7, height: 18, fontSize: 9.5 }}>主角</span>
                    )}
                    {c.enabled === false && (
                      <span className="pill" style={{ marginLeft: 5, height: 18, fontSize: 9.5 }}>已停用</span>
                    )}
                  </div>
                  <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 2 }}>
                    {c.identity || c.role || 'NPC'}
                    {c.first_revealed_chapter > 1 && <span className="mono" style={{ marginLeft: 6 }}>第 {c.first_revealed_chapter} 章出场</span>}
                  </div>
                </div>
              </div>
              {(c.content || c.description || c.bio) && (
                <p style={{ margin: 0, fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.6, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  {c.content || c.description || c.bio}
                </p>
              )}
              {Array.isArray(c.aliases) && c.aliases.length > 0 && (
                <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 7 }}>
                  {c.aliases.slice(0, 4).map((a, j) => <span key={j} className="pl-tag sm">{a}</span>)}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/* ─── 时间线子视图 ────────────────────────────── */
function TimelineView({ script, onBack }) {
  const [phases, setPhases] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!script) return;
    (async () => {
      try {
        const r = await window.api.scripts.timeline(script.id);
        setPhases(r?.phases || []);
      } catch (_) { setPhases([]); }
      finally { setLoading(false); }
    })();
  }, [script?.id]);

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title center">
          <strong>时间线锚点</strong>
          <span className="sub">{script?.title}</span>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading && <div className="muted" style={{ fontSize: 13 }}>加载中…</div>}
          {!loading && (!phases || phases.length === 0) && (
            <EmptyState icon="timeline" title="暂无时间线" desc="完成 anchors 模块构建后显示" />
          )}
          {!loading && phases && phases.map((p, i) => (
            <div key={i} style={{ marginBottom: 20 }}>
              <div style={{ marginBottom: 9 }}>
                <strong style={{ fontFamily: 'var(--font-serif)', fontSize: 14 }}>{p.phase_label}</strong>
                <span className="mono muted-2" style={{ fontSize: 11, marginLeft: 8 }}>
                  第 {p.chapter_min}–{p.chapter_max} 章
                </span>
              </div>
              {p.summary && <p style={{ margin: '0 0 10px', fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.6 }}>{p.summary}</p>}
              <div className="branch-tree">
                {(p.anchors || []).map((a) => (
                  <div key={a.anchor_id} className="branch-row">
                    <div className="branch-rail">
                      <span className="branch-node accent" />
                      <span className="branch-line" />
                    </div>
                    <div className="branch-card" style={{ width: '100%' }}>
                      <div className="branch-top">
                        <span className="branch-label serif">{a.story_time_label || `第 ${a.chapter_min}–${a.chapter_max} 章`}</span>
                      </div>
                      {a.sample_summary && (
                        <div className="branch-msg" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
                          {String(a.sample_summary).replace(/\s+/g, ' ').trim().slice(0, 200)}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/* ─── 版本历史子视图 ───────────────────────────── */
function VersionsView({ script, currentUserId, onBack }) {
  const [commits, setCommits] = useState([]);
  const [loading, setLoading] = useState(true);
  const [cursor, setCursor] = useState(null);
  const [hasMore, setHasMore] = useState(false);
  const [rollingBack, setRollingBack] = useState(null);

  const loadCommits = useCallback(async (c = null) => {
    if (!script) return;
    setLoading(true);
    try {
      const params = { limit: 30 };
      if (c) params.cursor = c;
      const r = await window.api.scripts.commits(script.id, params);
      const list = Array.isArray(r) ? r : (r?.items || r?.commits || []);
      if (c) setCommits(prev => [...prev, ...list]);
      else setCommits(list);
      const nextCursor = r?.next_cursor || null;
      setCursor(nextCursor);
      setHasMore(!!nextCursor);
    } catch (_) { window.__apiToast?.('加载版本历史失败', { kind: 'danger' }); }
    finally { setLoading(false); }
  }, [script?.id]);

  useEffect(() => { loadCommits(null); }, [loadCommits]);

  const isOwner = script && currentUserId && script.owner_id === currentUserId;

  const onRollback = async (commit) => {
    if (!window.confirm(`确认回退到版本 ${(commit.id || '').slice(0, 8)}？此操作不可撤销。`)) return;
    setRollingBack(commit.id);
    try {
      await window.api.scripts.checkout(script.id, commit.id);
      window.__apiToast?.('已回退到该版本', { kind: 'ok' });
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      onBack();
    } catch (e) {
      window.__apiToast?.(e?.message || '回退失败', { kind: 'danger' });
    } finally { setRollingBack(null); }
  };

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title center">
          <strong>版本历史</strong>
          <span className="sub">{script?.title}</span>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading && commits.length === 0 && <div className="muted" style={{ fontSize: 13 }}>加载中…</div>}
          {!loading && commits.length === 0 && (
            <EmptyState icon="history" title="暂无版本记录" />
          )}
          <div className="branch-tree">
            {commits.map((c, i) => (
              <div key={c.id || i} className="branch-row">
                <div className="branch-rail">
                  <span className={'branch-node ' + (c.id === script?.head_commit_id ? 'accent' : '')} />
                  <span className="branch-line" />
                </div>
                <div className="branch-card" style={{ width: '100%' }}>
                  <div className="branch-top">
                    <span className="branch-label serif">{c.message || c.kind || '—'}</span>
                    {c.id === script?.head_commit_id && (
                      <span className="branch-ref">当前</span>
                    )}
                  </div>
                  <div className="branch-msg mono">{(c.id || '').slice(0, 8)} · {c.kind || ''}</div>
                  <div className="branch-at">{c.created_at ? new Date(c.created_at).toLocaleString() : ''}</div>
                  {isOwner && c.id !== script?.head_commit_id && (
                    <button
                      onClick={() => onRollback(c)}
                      disabled={rollingBack === c.id}
                      style={{
                        marginTop: 8, height: 28, padding: '0 12px', borderRadius: 8,
                        fontSize: 12, color: 'var(--accent)', border: '1px solid var(--accent-edge)',
                        background: 'var(--accent-soft)',
                      }}
                    >
                      {rollingBack === c.id ? '回退中…' : '回退到此版本'}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
          {hasMore && (
            <button className="pl-btn-ghost" style={{ marginTop: 14 }} onClick={() => loadCommits(cursor)}>
              {loading ? '加载中…' : '加载更多'}
            </button>
          )}
        </div>
      </div>
    </>
  );
}

/* ─── 参数(overrides)子视图 ───────────────────── */
function OverridesView({ script, onBack }) {
  const [raw, setRaw] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [jsonValid, setJsonValid] = useState(true);

  useEffect(() => {
    if (!script) return;
    setLoading(true);
    (async () => {
      try {
        const r = await window.api.scripts.getOverrides(script.id);
        setRaw(JSON.stringify(r?.data ?? r ?? {}, null, 2));
      } catch (_) { setRaw('{}'); }
      finally { setLoading(false); }
    })();
  }, [script?.id]);

  const onChange = (v) => {
    setRaw(v);
    setDirty(true);
    try { JSON.parse(v); setJsonValid(true); } catch (_) { setJsonValid(false); }
  };

  const onSave = async () => {
    let parsed;
    try { parsed = JSON.parse(raw); } catch (e) {
      window.__apiToast?.('JSON 格式错误：' + e.message, { kind: 'danger' }); return;
    }
    setSaving(true);
    try {
      await window.api.scripts.saveOverrides(script.id, parsed);
      window.__apiToast?.('已保存', { kind: 'ok' });
      setDirty(false);
    } catch (e) { window.__apiToast?.(e?.message || '保存失败', { kind: 'danger' }); }
    finally { setSaving(false); }
  };

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title center">
          <strong>剧本参数覆盖</strong>
          <span className="sub">{script?.title}</span>
        </div>
        <div className="pl-head-actions">
          <button
            className="pl-headbtn"
            onClick={onSave}
            disabled={saving || !dirty || !jsonValid}
            style={{ color: dirty && jsonValid ? 'var(--accent)' : undefined }}
          >
            <Icon name="save" size={18} />
          </button>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          <p style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.65, marginBottom: 12 }}>
            script_overrides JSONB — 覆盖此剧本的全局参数，用于精细调整 GM 行为。
            {!jsonValid && <span style={{ color: 'var(--danger)', marginLeft: 6 }}>JSON 格式有误</span>}
          </p>
          {loading ? (
            <div className="muted" style={{ fontSize: 13 }}>加载中…</div>
          ) : (
            <textarea
              value={raw}
              onChange={e => onChange(e.target.value)}
              spellCheck={false}
              style={{
                width: '100%', minHeight: 320, fontFamily: 'var(--font-mono)', fontSize: 12.5,
                lineHeight: 1.55, background: 'var(--bg-deep)', color: 'var(--text)',
                border: `1px solid ${jsonValid ? 'var(--line-soft)' : 'var(--danger)'}`,
                borderRadius: 12, padding: '12px 14px', outline: 'none', resize: 'vertical',
              }}
            />
          )}
          <button
            className="pl-btn-primary"
            style={{ marginTop: 14 }}
            onClick={onSave}
            disabled={saving || !dirty || !jsonValid}
          >
            <Icon name="save" size={18} />
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </>
  );
}

/* ─── 发布/分享子视图 ─────────────────────────── */
function ShareView({ script, currentUserId, onBack, onRefresh }) {
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const isOwner = script && currentUserId && script.owner_id === currentUserId;
  const isPublic = !!script?.is_public;

  const onToggleVisibility = async () => {
    if (!isOwner) return;
    const next = !isPublic;
    if (next && (script.review_status || 'unreviewed') !== 'reviewed') {
      window.__apiToast?.('分享前需先完成剧本设定核对', { kind: 'warn', duration: 5000 });
      return;
    }
    if (next && !window.confirm(`确认将《${script.title}》发布到公开库？`)) return;
    setSaving(true);
    try {
      const r = await window.api.scripts.setVisibility(script.id, next);
      if (r?.ok === false) throw new Error(r.message || r.error || '操作失败');
      window.__apiToast?.(next ? '已发布到公开库' : '已取消发布', { kind: 'ok' });
      onRefresh?.();
    } catch (e) { window.__apiToast?.(e?.message || '操作失败', { kind: 'danger' }); }
    finally { setSaving(false); }
  };

  const onExport = async () => {
    setExporting(true);
    try {
      const filename = (script.title || 'script').replace(/[\\/:*?"<>|]/g, '_') + '_pack.zip';
      await window.api.scripts.exportPack(script.id, filename);
      window.__apiToast?.('导出成功：' + filename, { kind: 'ok' });
    } catch (e) { window.__apiToast?.(e?.message || '导出失败', { kind: 'danger' }); }
    finally { setExporting(false); }
  };

  const onFork = async () => {
    if (!window.confirm(`将《${script.title}》另存为可编辑副本？`)) return;
    try {
      const r = await window.api.scripts.fork(script.id, { title: `${script.title} (副本)` });
      if (!r || r.ok === false) throw new Error(r?.error || '操作失败');
      window.__apiToast?.('已另存为副本', { kind: 'ok' });
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      onBack();
    } catch (e) { window.__apiToast?.(e?.message || '操作失败', { kind: 'danger' }); }
  };

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title center">
          <strong>发布 / 导出</strong>
          <span className="sub">{script?.title}</span>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {isOwner && (
            <div className="pl-group" style={{ marginBottom: 16 }}>
              <div className="pl-setrow">
                <span className={'pl-row-ic ' + (isPublic ? 'ok' : '')} style={{ width: 38, height: 38 }}>
                  <Icon name="globe" size={16} />
                </span>
                <div className="pl-setrow-tx">
                  <strong>发布到在线库</strong>
                  <span>{isPublic ? '其他人可浏览并订阅此剧本' : '仅自己可见'}</span>
                </div>
                <button
                  className={'pl-toggle' + (isPublic ? ' on' : '')}
                  onClick={onToggleVisibility}
                  disabled={saving}
                />
              </div>
            </div>
          )}
          {!isOwner && script?.owner_id && (
            <div style={{
              padding: '12px 14px', borderRadius: 12, marginBottom: 16,
              background: 'var(--info-soft)', border: '1px solid rgba(122,166,194,0.3)',
              fontSize: 13, color: 'var(--text-quiet)', lineHeight: 1.6,
            }}>
              这是他人分享的剧本。你可以另存为副本后自由编辑。
              <button
                className="pl-btn-primary"
                style={{ marginTop: 10 }}
                onClick={onFork}
              >
                <Icon name="copy" size={17} /> 另存为可编辑副本
              </button>
            </div>
          )}

          <div className="pl-sec">
            <div className="pl-sec-head"><h2>导入 / 导出</h2></div>
            <button className="pl-btn-ghost" style={{ marginBottom: 9 }} onClick={onExport} disabled={exporting}>
              <Icon name="download" size={16} />{exporting ? '导出中…' : '导出剧本包 (.zip)'}
            </button>
          </div>

          {script?.sharing_mode && script.sharing_mode !== 'private' && (
            <div className="pl-sec">
              <div className="pl-sec-head"><h2>共享模式</h2></div>
              <div className="pl-card">
                <div style={{ fontSize: 13, color: 'var(--text-quiet)' }}>
                  当前模式：
                  <span style={{ color: 'var(--accent)', fontWeight: 500 }}>
                    {{
                      'public': '公开',
                      'pinned-snapshot': '固定快照',
                      'floating-latest': '跟随最新',
                    }[script.sharing_mode] || script.sharing_mode}
                  </span>
                  {script.sharing_mode === 'pinned-snapshot' && script.current_pin_commit_id && (
                    <span className="mono" style={{ marginLeft: 8, fontSize: 11, color: 'var(--muted-2)' }}>
                      {script.current_pin_commit_id.slice(0, 8)}
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/* ─── 剧本详情子视图 ───────────────────────────── */
function ScriptDetailView({ script, saves, embedStatus, currentUserId, onBack, onRefresh, nav }) {
  const [subView, setSubView] = useState(null);

  const es = embedStatus[script?.id];
  const embedDone = es && !es.running && (es.chunks?.done || 0) >= (es.chunks?.total || 1) && (es.chunks?.total || 0) > 0;
  const embedRunning = es?.running;
  const totalDone = es ? ((es.chunks?.done || 0) + (es.cards?.done || 0) + (es.worldbook?.done || 0)) : 0;
  const totalAll = es ? ((es.chunks?.total || 0) + (es.cards?.total || 0) + (es.worldbook?.total || 0)) : 0;
  const embedPct = totalAll > 0 ? Math.round(totalDone / totalAll * 100) : 0;

  const playBlock = isPlayBlocked(script);
  const scriptSaves = saves.filter(sv => sv.script_id === script?.id);
  const savesCount = scriptSaves.length;
  const isOwner = currentUserId && script?.owner_id === currentUserId;

  const onPlay = async () => {
    if (playBlock) { window.__apiToast?.(playBlock, { kind: 'warn', duration: 5000 }); return; }
    const sv = scriptSaves[0];
    if (sv) { nav.openGame?.(sv); return; }
    nav.push?.('new-game', { scriptId: script.id });   // 无存档 → 进新游戏向导(锁定本剧本)
  };

  const onNewGame = async () => {
    if (playBlock) { window.__apiToast?.(playBlock, { kind: 'warn', duration: 5000 }); return; }
    nav.push?.('new-game', { scriptId: script.id });   // 新建存档 → 新游戏向导
  };

  const onEmbed = async () => {
    if (embedRunning) return;
    try {
      const r = await fetch(`${window.__API_BASE || ''}/api/scripts/${script.id}/embed`, { method: 'POST', credentials: 'include' });
      const j = await r.json();
      if (j.ok === false) {
        if (j.code === 'credentials_required' || j.error_key === 'credentials_required') {
          window.__apiToast?.('未配置向量嵌入模型，请先在设置中配置 RAG / Embedding 模型', { kind: 'warn', duration: 7000 });
        } else {
          window.__apiToast?.(j.error || '向量化启动失败', { kind: 'danger' });
        }
        return;
      }
      window.__apiToast?.('向量化任务已启动', { kind: 'ok', duration: 3000 });
    } catch (e) { window.__apiToast?.(String(e), { kind: 'danger' }); }
  };

  const onDelete = async () => {
    if (!window.confirm(`确认删除《${script.title}》？此操作不可撤销，相关存档也将无法使用。`)) return;
    try {
      const r = await window.api.scripts.delete(script.id, { force: true });
      if (!r || r.ok !== true) throw new Error(r?.error || '删除失败');
      window.__apiToast?.('已删除', { kind: 'ok' });
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      onBack();
    } catch (e) { window.__apiToast?.(e?.message || '删除失败', { kind: 'danger' }); }
  };

  const onUnsubscribe = async () => {
    if (!window.confirm(`将「${script.title}」从你的剧本列表移除？原剧本不受影响，之后可在公开库重新导入。`)) return;
    try {
      const r = await window.api.scripts.unsubscribe(script.id);
      if (!r || r.ok !== true) throw new Error(r?.error || '移出失败');
      window.__apiToast?.('已移出我的列表', { kind: 'ok' });
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      onBack();
    } catch (e) { window.__apiToast?.(e?.message || '移出失败', { kind: 'danger' }); }
  };

  if (subView === 'chapters') return <ChaptersView script={script} onBack={() => setSubView(null)} />;
  if (subView === 'worldbook') return <WorldbookView script={script} onBack={() => setSubView(null)} />;
  if (subView === 'npc') return <NpcView script={script} onBack={() => setSubView(null)} />;
  if (subView === 'timeline') return <TimelineView script={script} onBack={() => setSubView(null)} />;
  if (subView === 'versions') return <VersionsView script={script} currentUserId={currentUserId} onBack={() => setSubView(null)} />;
  if (subView === 'overrides') return <OverridesView script={script} onBack={() => setSubView(null)} />;
  if (subView === 'share') return <ShareView script={script} currentUserId={currentUserId} onBack={() => setSubView(null)} onRefresh={onRefresh} />;

  const isInternal = typeof script.title === 'string' && script.title.startsWith('[内部]');

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title">
          <strong style={{ fontSize: 14.5 }}>{script.title}</strong>
          <span className="sub mono">{script.uid}</span>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={() => setSubView('share')} title="发布/导出">
            <Icon name={script.is_public ? 'globe' : 'upload'} size={17} />
          </button>
          {script.is_subscribed ? (
            <button className="pl-headbtn" onClick={onUnsubscribe} title="移出我的列表" style={{ color: 'var(--danger)' }}>
              <Icon name="trash" size={17} />
            </button>
          ) : (
            <button className="pl-headbtn" onClick={onDelete} title="删除" style={{ color: 'var(--danger)' }}>
              <Icon name="trash" size={17} />
            </button>
          )}
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {isInternal ? (
            <div style={{ textAlign: 'center', padding: '40px 20px' }}>
              <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.6 }}>🚧</div>
              <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, fontFamily: 'var(--font-serif)' }}>敬请期待</div>
              <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.65 }}>
                此功能模块正在开发中，公测后开放。
              </div>
            </div>
          ) : (
            <>
              {/* 统计 */}
              <div className="pl-stats" style={{ marginBottom: 16 }}>
                <div className="pl-stat"><span className="n accent">{fmtN(script.chapter_count || 0)}</span><div className="l">章节</div></div>
                <div className="pl-stat"><span className="n">{((Number(script.word_count) || 0) / 10000).toFixed(1)}<span style={{ fontSize: 11 }}>万</span></span><div className="l">字数</div></div>
                <div className="pl-stat"><span className="n">{savesCount}</span><div className="l">存档</div></div>
                <div className="pl-stat">
                  <span className="n" style={{ fontSize: 14, color: embedDone ? 'var(--ok)' : embedRunning ? 'var(--warn)' : 'var(--muted)' }}>
                    {embedRunning ? `${embedPct}%` : embedDone ? '✓' : '—'}
                  </span>
                  <div className="l">向量索引</div>
                </div>
              </div>

              {/* 就绪状态提示 */}
              {playBlock && (
                <div style={{ padding: '10px 13px', borderRadius: 12, marginBottom: 14, background: 'var(--warn-soft)', border: '1px solid rgba(212,179,102,0.3)', fontSize: 12.5, color: 'var(--warn)', lineHeight: 1.6 }}>
                  <Icon name="warn" size={13} style={{ marginRight: 6 }} />
                  {playBlock}
                </div>
              )}

              {/* 导入报告 */}
              {script.import_report && (
                <div className="pl-sec">
                  <div className="pl-sec-head"><h2>导入报告</h2></div>
                  <div className="pl-card">
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, fontSize: 12.5 }}>
                      <span style={{ color: 'var(--muted)' }}>切分模式</span>
                      <span>{script.import_report.mode_label || '—'}</span>
                    </div>
                    {script.import_report.confidence != null && (
                      <div style={{ marginBottom: 8 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, fontSize: 12 }}>
                          <span className="muted-2">置信度</span>
                          <span className="mono" style={{ fontSize: 11 }}>{Math.round(script.import_report.confidence * 100)}%</span>
                        </div>
                        <div className="pl-progress">
                          <i style={{ width: `${Math.round(script.import_report.confidence * 100)}%`, background: script.import_report.confidence >= 0.85 ? 'var(--ok)' : 'var(--warn)' }} />
                        </div>
                      </div>
                    )}
                    {script.import_report.problem_label && (
                      <div style={{ fontSize: 12, color: script.import_report.problem_kind === 'ok' ? 'var(--ok)' : 'var(--warn)' }}>
                        {script.import_report.problem_label}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* readiness 状态 */}
              {script.readiness && !script.readiness.ok && (
                <div className="pl-sec">
                  <div className="pl-sec-head"><h2>就绪状态</h2></div>
                  {(script.readiness.items || []).filter(it => !it.ok).map((it, i) => (
                    <button key={i} className="pl-row" onClick={() => {
                      const tabMap = { chunks: 'chapters', embeddings: 'chapters', canon: null, worldbook: 'worldbook', anchors: 'timeline' };
                      const t = tabMap[it.key];
                      if (t) setSubView(t);
                    }}>
                      <span className="pl-row-ic warn"><Icon name="warn" size={17} /></span>
                      <span className="pl-row-tx">
                        <strong>{it.key}</strong>
                        <span>{it.total > 0 ? `${it.count}/${it.total}` : '未构建'}</span>
                      </span>
                      <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                    </button>
                  ))}
                </div>
              )}

              {/* 模块导航 */}
              <div className="pl-sec">
                <div className="pl-sec-head"><h2>剧本模块</h2></div>
                <button className="pl-row" onClick={() => setSubView('chapters')}>
                  <span className="pl-row-ic accent"><Icon name="book_open" size={17} /></span>
                  <span className="pl-row-tx"><strong>章节列表</strong><span>浏览、重命名、合并、拆分</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('worldbook')}>
                  <span className="pl-row-ic ok"><Icon name="world" size={17} /></span>
                  <span className="pl-row-tx"><strong>世界书</strong><span>知识条目 · 语义激活</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('npc')}>
                  <span className="pl-row-ic info"><Icon name="cards" size={17} /></span>
                  <span className="pl-row-tx"><strong>NPC 角色卡</strong><span>抽取的可玩角色卡片</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('timeline')}>
                  <span className="pl-row-ic warn"><Icon name="timeline" size={17} /></span>
                  <span className="pl-row-tx"><strong>时间线锚点</strong><span>故事时间轴 · 章节分期</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
              </div>

              {/* 进阶 */}
              <div className="pl-sec">
                <div className="pl-sec-head"><h2>进阶</h2></div>
                <button className="pl-row" onClick={() => setSubView('overrides')}>
                  <span className="pl-row-ic"><Icon name="settings" size={17} /></span>
                  <span className="pl-row-tx"><strong>剧本参数覆盖</strong><span>script_overrides JSONB</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('versions')}>
                  <span className="pl-row-ic"><Icon name="history" size={17} /></span>
                  <span className="pl-row-tx"><strong>版本历史</strong><span>提交记录 · 回退 · 固定</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('share')}>
                  <span className="pl-row-ic ok"><Icon name={script.is_public ? 'globe' : 'lock'} size={17} /></span>
                  <span className="pl-row-tx">
                    <strong>发布 / 导出</strong>
                    <span>{script.is_public ? '已发布到在线库' : '仅自己可见'} · 导出剧本包</span>
                  </span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={onEmbed} disabled={embedRunning}>
                  <span className={'pl-row-ic ' + (embedDone ? 'ok' : '')}><Icon name="sparkle" size={17} /></span>
                  <span className="pl-row-tx">
                    <strong>向量索引</strong>
                    <span>
                      {embedRunning ? `向量化中 ${embedPct}%` : embedDone ? `已建索引 ${totalAll} 条` : '未建向量索引，点击开始'}
                    </span>
                  </span>
                  <span className="pl-row-chev"><Icon name={embedRunning ? 'refresh' : 'chevron_right'} size={17} /></span>
                </button>
              </div>

              {/* 不是自己的剧本 → 可 fork */}
              {!isOwner && script.owner_id && (
                <div style={{ marginTop: 16, padding: '12px 14px', borderRadius: 12, background: 'var(--info-soft)', border: '1px solid rgba(122,166,194,0.3)', fontSize: 13, color: 'var(--text-quiet)', lineHeight: 1.6 }}>
                  这是他人分享的剧本，你可将其另存为可编辑副本。
                </div>
              )}

              {/* 主操作区 */}
              <div style={{ display: 'grid', gap: 9, marginTop: 22 }}>
                <button className="pl-btn-primary" onClick={onPlay} disabled={!!playBlock}>
                  <Icon name="play" size={18} />
                  {playBlock ? '暂时无法进入游戏' : scriptSaves.length > 0 ? `继续游戏（${scriptSaves.length} 个存档）` : '开始新游戏'}
                </button>
                {scriptSaves.length > 0 && (
                  <button className="pl-btn-ghost" onClick={onNewGame}>
                    <Icon name="plus" size={16} /> 新建存档
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ─── 导入向导视图 ─────────────────────────────── */
function ImportView({ onBack }) {
  const [step, setStep] = useState(0); // 0=上传 1=配置 2=预览 3=进行中/结果
  const [selectedFile, setSelectedFile] = useState(null);
  const [title, setTitle] = useState('');
  const [rule, setRule] = useState('auto');
  const [customPattern, setCustomPattern] = useState('');
  const [enableCards, setEnableCards] = useState(true);
  const [enableWorldbook, setEnableWorldbook] = useState(true);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [estimate, setEstimate] = useState(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importProgress, setImportProgress] = useState('');
  const [importPercent, setImportPercent] = useState(0);
  const [job, setJob] = useState(null);
  const fileRef = useRef(null);

  const CHUNK_SIZE = 512 * 1024;

  const onPickFile = (file) => {
    if (!file) return;
    const name = (file.name || '').toLowerCase();
    if (!/\.(txt|md)$/.test(name)) {
      window.__apiToast?.('仅支持 .txt / .md 文件', { kind: 'danger' }); return;
    }
    if (file.size > 50 * 1024 * 1024) {
      window.__apiToast?.('文件过大（上限 50 MB）', { kind: 'danger' }); return;
    }
    setSelectedFile(file);
    setEstimate(null);
    if (!title) setTitle(file.name.replace(/\.(txt|md)$/i, ''));
    setStep(1);
  };

  const uploadChunks = async (file, onProgress) => {
    const totalBytes = file.size;
    const totalChunks = Math.max(1, Math.ceil(totalBytes / CHUNK_SIZE));
    onProgress?.({ stage: 'init', percent: 0 });
    const init = await window.api.uploads.init({ filename: file.name, total_bytes: totalBytes, total_chunks: totalChunks });
    const uploadId = init.upload_id || init.id;
    if (!uploadId) throw new Error('未获取到 upload_id');
    for (let i = 0; i < totalChunks; i++) {
      const blob = file.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
      await window.api.uploads.chunk(uploadId, blob, i);
      onProgress?.({ stage: 'chunk', done: i + 1, total: totalChunks, percent: Math.round(((i + 1) / totalChunks) * 100) });
    }
    await window.api.uploads.finish(uploadId, {});
    onProgress?.({ stage: 'finish', percent: 100 });
    return uploadId;
  };

  const startPreview = async () => {
    if (!selectedFile) { window.__apiToast?.('请先选择文件', { kind: 'warn' }); return; }
    setPreviewBusy(true);
    setEstimate(null);
    try {
      const uploadId = await uploadChunks(selectedFile, ({ stage, done, total, percent }) => {
        if (stage === 'init') setImportProgress('上传初始化…');
        else if (stage === 'chunk') setImportProgress(`上传中 ${done}/${total}`);
        else if (stage === 'finish') setImportProgress('上传完成，分析中…');
      });
      const result = await window.api.scripts.preview({
        upload_id: uploadId,
        split_rule: rule || 'auto',
        custom_pattern: customPattern || '',
        sample_limit: 20,
      });
      const chapters = Number(result.total_chapters) || (Array.isArray(result.preview) ? result.preview.length : 0);
      const words = Number(result.total_words) || 0;
      setEstimate({ chapters, words, upload_id: uploadId, preview: result.preview, report: result.report });
      setStep(2);
    } catch (e) {
      window.__apiToast?.(e?.message || '预览失败', { kind: 'danger', duration: 6000 });
    } finally {
      setPreviewBusy(false);
      setImportProgress('');
    }
  };

  const startImport = async () => {
    if (!selectedFile || !estimate) return;
    setImportBusy(true);
    setImportPercent(5);
    setImportProgress('上传文件…');
    setStep(3);
    try {
      let uploadId = estimate.upload_id;
      if (!uploadId) {
        uploadId = await uploadChunks(selectedFile, ({ stage, done, total, percent }) => {
          setImportPercent(Math.min(30, Math.round((percent || 0) * 0.30)));
          setImportProgress(`上传中 ${done || 0}/${total || 1}`);
        });
      }
      setImportPercent(30); setImportProgress('创建剧本…');
      const importResp = await window.api.scripts.importScript({
        upload_id: uploadId,
        title: title || selectedFile.name.replace(/\.(txt|md)$/i, ''),
        split_rule: rule || 'auto',
        custom_pattern: customPattern || '',
        require_llm_credentials: true,
      });
      if (!importResp || importResp.ok === false) throw new Error(importResp?.error || '创建失败');
      const sc = importResp.script || {};
      setImportPercent(40); setImportProgress('启动抽取流水线…');
      const pipelineResp = await window.api.scripts.importPipeline(sc.id, {
        enable_cards: enableCards,
        enable_worldbook: enableWorldbook,
        budget: estimate,
      });
      if (!pipelineResp || pipelineResp.ok === false || !pipelineResp.job_id) {
        if (pipelineResp?.code === 'credentials_required' || pipelineResp?.needs_credentials) {
          window.__apiToast?.('未配置 LLM API Key，请先在设置中配置，剧本已导入（章节已创建）', { kind: 'warn', duration: 7000 });
          setJob({ status: 'paused_credentials', title: sc.title || title, script_id: sc.id });
          try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
          return;
        }
        throw new Error(pipelineResp?.error || '流水线启动失败');
      }
      setJob({ status: 'running', id: pipelineResp.job_id, title: sc.title || title, script_id: sc.id });
      window.__apiToast?.('导入任务已派发后台', { kind: 'ok', duration: 3000 });
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
    } catch (e) {
      const isCredsErr = e?.code === 'credentials_required' || e?.payload?.needs_credentials || /credentials_required/.test(String(e));
      if (isCredsErr) {
        window.__apiToast?.('未配置 LLM API Key，请先在设置中配置', { kind: 'warn', duration: 7000 });
        setJob({ status: 'paused_credentials' });
      } else {
        window.__apiToast?.(e?.message || '导入失败', { kind: 'danger', duration: 6000 });
        setJob(null);
        setStep(2);
      }
    } finally {
      setImportBusy(false);
      setImportProgress('');
    }
  };

  const ruleLabel = SPLIT_RULES.find(r2 => r2.id === rule)?.label || rule;

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={step === 0 ? onBack : () => { if (step < 3) setStep(s => s - 1); else onBack(); }}>
          <Icon name="chevron_left" size={20} />
        </button>
        <div className="pl-head-title center">
          <strong>导入剧本</strong>
          <span className="sub">第 {step + 1} / 3 步</span>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {/* 步骤 0: 选择文件 */}
          {step === 0 && (
            <>
              <input ref={fileRef} type="file" accept=".txt,.md" style={{ display: 'none' }}
                onChange={e => onPickFile(e.target.files?.[0])} />
              <div
                onClick={() => fileRef.current?.click()}
                style={{
                  border: '2px dashed var(--line-strong)', borderRadius: 16, padding: '48px 20px',
                  textAlign: 'center', display: 'grid', gap: 12, placeItems: 'center',
                  cursor: 'pointer', transition: 'border-color .2s',
                }}
              >
                <span className="pl-row-ic accent" style={{ width: 56, height: 56 }}>
                  <Icon name="upload" size={26} />
                </span>
                <div>
                  <strong style={{ fontSize: 15, color: 'var(--text)' }}>点击选择文件</strong>
                  <div style={{ fontSize: 12, color: 'var(--muted-2)', marginTop: 5 }}>支持 .txt / .md · 最大 50 MB</div>
                </div>
              </div>
              <div className="pl-note" style={{ marginTop: 16 }}>
                导入后自动抽取 <strong>角色卡、世界书、时间线</strong>，并建立
                <strong>向量索引（RAG）</strong>。整套脚手架就位后即可开局。
              </div>
            </>
          )}

          {/* 步骤 1: 配置参数 */}
          {step === 1 && selectedFile && (
            <>
              <div className="pl-card" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 11 }}>
                <span className="pl-row-ic ok"><Icon name="file" size={17} /></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, color: 'var(--text)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {selectedFile.name}
                  </div>
                  <div className="mono muted-2" style={{ fontSize: 11 }}>
                    {(selectedFile.size / 1024).toFixed(0)} KB
                  </div>
                </div>
                <button onClick={() => { setSelectedFile(null); setTitle(''); setStep(0); }} style={{ color: 'var(--muted)', padding: 4 }}>
                  <Icon name="close" size={16} />
                </button>
              </div>

              <div className="pl-field">
                <label>剧本标题</label>
                <input className="pl-input" value={title} onChange={e => setTitle(e.target.value)} placeholder="从文件名自动填入" />
              </div>

              <div className="pl-field">
                <label>章节切分模式</label>
                <select
                  className="pl-input"
                  value={rule}
                  onChange={e => setRule(e.target.value)}
                  style={{ fontSize: 16 }}
                >
                  {SPLIT_RULES.map(r2 => <option key={r2.id} value={r2.id}>{r2.label}</option>)}
                </select>
              </div>

              {rule === 'custom' && (
                <div className="pl-field">
                  <label>自定义正则</label>
                  <input className="pl-input" value={customPattern} onChange={e => setCustomPattern(e.target.value)} placeholder="例: ^第.{1,10}章" />
                </div>
              )}

              <div className="pl-sec">
                <div className="pl-sec-head"><h2>流水线选项</h2></div>
                <div className="pl-group">
                  <div className="pl-setrow">
                    <div className="pl-setrow-tx">
                      <strong>提取 NPC 角色卡</strong>
                      <span>LLM 自动识别主要角色</span>
                    </div>
                    <button className={'pl-toggle' + (enableCards ? ' on' : '')} onClick={() => setEnableCards(!enableCards)} />
                  </div>
                  <div className="pl-setrow">
                    <div className="pl-setrow-tx">
                      <strong>生成世界书</strong>
                      <span>自动抽取人物/地点/道具条目</span>
                    </div>
                    <button className={'pl-toggle' + (enableWorldbook ? ' on' : '')} onClick={() => setEnableWorldbook(!enableWorldbook)} />
                  </div>
                </div>
                {(!enableCards || !enableWorldbook) && (
                  <div style={{ fontSize: 12, color: 'var(--warn)', marginTop: 8 }}>
                    关闭部分选项会影响游戏中的 RAG 检索精度
                  </div>
                )}
              </div>

              <button
                className="pl-btn-primary"
                style={{ marginTop: 20 }}
                disabled={previewBusy || !selectedFile}
                onClick={startPreview}
              >
                {previewBusy ? <><Icon name="refresh" size={17} /> {importProgress || '上传中…'}</> : <><Icon name="sparkle" size={17} />预览分章</>}
              </button>
              {previewBusy && (
                <div style={{ marginTop: 10 }}>
                  <div className="pl-progress"><i style={{ width: '60%' }} /></div>
                  <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 4 }}>{importProgress}</div>
                </div>
              )}
            </>
          )}

          {/* 步骤 2: 预览确认 */}
          {step === 2 && estimate && (
            <>
              <div className="pl-card" style={{ textAlign: 'center', padding: '22px 16px', marginBottom: 16 }}>
                <span className="pl-row-ic ok" style={{ width: 50, height: 50, margin: '0 auto 10px' }}>
                  <Icon name="check" size={24} />
                </span>
                <strong style={{ fontSize: 17, fontFamily: 'var(--font-serif)', color: 'var(--text)' }}>分析完成</strong>
                <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 6 }}>
                  识别 {estimate.chapters} 章
                  {estimate.report?.confidence != null && ` · 置信 ${Math.round(estimate.report.confidence * 100)}%`}
                </div>
              </div>

              <div className="pl-kvgrid" style={{ marginBottom: 16 }}>
                <div className="pl-kv"><div className="k">章节</div><div className="v">{fmtN(estimate.chapters)}</div></div>
                <div className="pl-kv"><div className="k">字数</div><div className="v">{fmtWan(estimate.words)}</div></div>
                <div className="pl-kv"><div className="k">切分模式</div><div className="v" style={{ fontSize: 12 }}>{ruleLabel}</div></div>
                <div className="pl-kv">
                  <div className="k">提取角色卡</div>
                  <div className="v" style={{ fontSize: 13 }}>{enableCards ? '是' : '否'}</div>
                </div>
              </div>

              {/* 章节预览列表 */}
              {Array.isArray(estimate.preview) && estimate.preview.length > 0 && (
                <div className="pl-sec">
                  <div className="pl-sec-head"><h2>章节预览（前 {estimate.preview.length} 章）</h2></div>
                  {estimate.preview.slice(0, 10).map((p, i) => (
                    <div key={i} className="pl-row" style={{ cursor: 'default' }}>
                      <span className="mono muted-2" style={{ fontSize: 11, width: 32, flex: 'none' }}>#{String(p.idx || i + 1).padStart(3, '0')}</span>
                      <span className="pl-row-tx">
                        <strong style={{ fontFamily: 'var(--font-serif)' }}>{p.title || '无标题'}</strong>
                        <span className="mono">{fmtN(p.words || 0)} 字</span>
                      </span>
                      {!p.ok && <span className="pill warn" style={{ height: 19, fontSize: 10 }}>有问题</span>}
                    </div>
                  ))}
                  {estimate.preview.length > 10 && (
                    <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--muted-2)', padding: '8px 0' }}>
                      还有 {estimate.preview.length - 10} 章…
                    </div>
                  )}
                </div>
              )}

              <div className="pl-note" style={{ marginTop: 14 }}>
                确认后将在后台建立向量索引及 LLM 抽取，约需数分钟。完成前可先浏览列表，索引就绪后检索更准。
              </div>
              <div style={{ display: 'grid', gap: 9, marginTop: 20 }}>
                <button className="pl-btn-primary" onClick={startImport} disabled={importBusy}>
                  <Icon name="check" size={17} /> 确认导入（后台运行）
                </button>
                <button className="pl-btn-ghost" onClick={() => setStep(1)}>
                  <Icon name="chevron_left" size={16} /> 返回修改
                </button>
              </div>
            </>
          )}

          {/* 步骤 3: 进行中/结果 */}
          {step === 3 && (
            <>
              {importBusy && (
                <div style={{ textAlign: 'center', padding: '40px 20px' }}>
                  <div style={{ width: 56, height: 56, borderRadius: 17, display: 'grid', placeItems: 'center', background: 'var(--accent-soft)', border: '1px solid var(--accent-edge)', margin: '0 auto 16px', color: 'var(--accent)' }}>
                    <Icon name="refresh" size={26} />
                  </div>
                  <div style={{ fontFamily: 'var(--font-serif)', fontSize: 17, color: 'var(--text)', marginBottom: 8 }}>上传中…</div>
                  <div style={{ fontSize: 12.5, color: 'var(--muted)' }}>{importProgress}</div>
                  <div className="pl-progress" style={{ marginTop: 16 }}>
                    <i style={{ width: `${importPercent}%`, transition: 'width .3s' }} />
                  </div>
                </div>
              )}
              {!importBusy && job && (
                <>
                  {(job.status === 'running' || job.status === 'paused_credentials') && (
                    <div className="pl-card" style={{ textAlign: 'center', padding: '30px 16px', marginBottom: 16 }}>
                      <span className={'pl-row-ic ' + (job.status === 'running' ? 'accent' : 'warn')} style={{ width: 50, height: 50, margin: '0 auto 12px' }}>
                        <Icon name={job.status === 'running' ? 'sparkle' : 'warn'} size={24} />
                      </span>
                      <div style={{ fontFamily: 'var(--font-serif)', fontSize: 16, color: 'var(--text)', marginBottom: 8 }}>
                        {job.status === 'running' ? '后台处理中' : '需要配置 API Key'}
                      </div>
                      <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.65 }}>
                        {job.status === 'running'
                          ? `《${job.title || '—'}》正在后台建立索引，可前往剧本列表查看进度。`
                          : '剧本已创建，但 LLM 抽取需要配置 API Key。请前往设置完成配置。'}
                      </div>
                    </div>
                  )}
                  <div style={{ display: 'grid', gap: 9 }}>
                    <button className="pl-btn-primary" onClick={onBack}>
                      <Icon name="book_open" size={17} /> 前往剧本列表
                    </button>
                    <button className="pl-btn-ghost" onClick={() => { setJob(null); setEstimate(null); setSelectedFile(null); setTitle(''); setStep(0); }}>
                      <Icon name="plus" size={16} /> 再导入一部
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ─── 在线剧本库视图 ─────────────────────────── */
function LibraryView({ onBack, nav }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [cloningId, setCloningId] = useState(null);
  const [importedIds, setImportedIds] = useState({});
  const [selectedItem, setSelectedItem] = useState(null);

  const reload = useCallback(async (query) => {
    setLoading(true);
    try {
      const r = await window.api.scripts.publicList(query ? { q: query } : undefined);
      setItems(Array.isArray(r?.items) ? r.items : []);
    } catch (e) {
      window.__apiToast?.(e?.message || '加载失败', { kind: 'danger' });
      setItems([]);
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { reload(''); }, [reload]);

  const onClone = async (s) => {
    setCloningId(s.id);
    try {
      const r = await window.api.scripts.cloneFromPublic(s.id);
      if (r?.ok === false) throw new Error(r.error || '导入失败');
      window.__apiToast?.(`已导入《${s.title}》到你的剧本库`, { kind: 'ok', duration: 3000 });
      setImportedIds(m => ({ ...m, [s.id]: true }));
      setItems(arr => arr.map(x => x.id === s.id ? { ...x, clone_count: (x.clone_count || 0) + 1 } : x));
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
    } catch (e) {
      window.__apiToast?.(e?.message || '导入失败', { kind: 'danger' });
    } finally { setCloningId(null); }
  };

  if (selectedItem) {
    const s = selectedItem;
    const alreadyImported = s.mine || importedIds[s.id];
    return (
      <>
        <div className="pl-head">
          <button className="pl-back" onClick={() => setSelectedItem(null)}><Icon name="chevron_left" size={20} /></button>
          <div className="pl-head-title">
            <strong style={{ fontSize: 14.5 }}>{s.title}</strong>
            <span className="sub">在线库 · {s.author || s.author_username || '未知作者'}</span>
          </div>
        </div>
        <div className="pl-body tabbed">
          <div className="pl-cover" style={{ height: 120, borderRadius: 0 }}>
            <span className="pl-cover-spine" />
            <div style={{ position: 'relative' }}>
              <h3 style={{ fontSize: 22 }}>{s.title}</h3>
              <div style={{ fontSize: 12, color: 'var(--text-quiet)', marginTop: 5 }}>{s.author || s.author_username || '—'}</div>
            </div>
            {alreadyImported && (
              <span className="pill ok" style={{ position: 'absolute', top: 10, right: 10, height: 20, fontSize: 10 }}>
                <span className="dot ok" />{s.mine ? '我的剧本' : '已导入'}
              </span>
            )}
          </div>
          <div className="pl-pad">
            {s.description && <p style={{ margin: '0 0 14px', fontSize: 13.5, color: 'var(--text-quiet)', lineHeight: 1.7 }}>{s.description}</p>}
            <div className="pl-kvgrid" style={{ marginBottom: 14 }}>
              <div className="pl-kv"><div className="k">章节</div><div className="v">{fmtN(s.chapter_count || 0)}</div></div>
              <div className="pl-kv"><div className="k">字数</div><div className="v">{fmtWan(s.word_count || 0)}</div></div>
              <div className="pl-kv"><div className="k">克隆数</div><div className="v">{fmtN(s.clone_count || 0)}</div></div>
              <div className="pl-kv"><div className="k">ID</div><div className="v mono" style={{ fontSize: 11 }}>{s.uid || String(s.id).slice(0, 8)}</div></div>
            </div>
            <div style={{ display: 'grid', gap: 9 }}>
              {alreadyImported ? (
                <button className="pl-btn-ghost" disabled style={{ color: 'var(--ok)', borderColor: 'rgba(126,184,142,0.3)' }}>
                  <Icon name="check" size={17} /> {s.mine ? '已在我的剧本库' : '已导入'}
                </button>
              ) : (
                <button className="pl-btn-primary" onClick={() => onClone(s)} disabled={cloningId === s.id}>
                  <Icon name="download" size={17} />{cloningId === s.id ? '导入中…' : '导入到我的剧本库'}
                </button>
              )}
              <button className="pl-btn-ghost" onClick={() => setSelectedItem(null)}>
                <Icon name="chevron_left" size={16} /> 返回
              </button>
            </div>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title center">
          <strong>在线剧本库</strong>
          <span className="sub">社区公开 · 导入即玩</span>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={() => reload(q)} title="刷新">
            <Icon name="refresh" size={18} />
          </button>
        </div>
      </div>
      <div className="pl-toolbar">
        <div className="pl-search">
          <Icon name="search" size={16} />
          <input
            placeholder="搜索公开剧本…"
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && reload(q)}
          />
          {q && <button onClick={() => { setQ(''); reload(''); }}><Icon name="close" size={15} /></button>}
        </div>
        {q && <button className="pl-pill" onClick={() => reload(q)}>搜索</button>}
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading && <div className="muted" style={{ fontSize: 13, padding: '20px 0' }}>加载中…</div>}
          {!loading && items.length === 0 && (
            <EmptyState icon="globe" title={q ? '未找到匹配剧本' : '暂无公开剧本'} desc={q ? '换个关键词试试' : '还没有人分享剧本'} />
          )}
          {items.map(s => (
            <button
              key={s.id}
              className="pl-cover-card"
              style={{ marginBottom: 13 }}
              onClick={() => setSelectedItem(s)}
            >
              <div className="pl-cover">
                <span className="pl-cover-spine" />
                <h3>{s.title}</h3>
                {(s.mine || importedIds[s.id]) && (
                  <span className="pill ok" style={{ position: 'absolute', top: 8, right: 10, height: 18, fontSize: 9.5 }}>
                    <span className="dot ok" />{s.mine ? '已在库' : '已导入'}
                  </span>
                )}
              </div>
              <div className="pl-cover-body">
                {s.description && <div className="pl-cover-desc">{s.description.slice(0, 80)}{s.description.length > 80 ? '…' : ''}</div>}
                <div className="pl-cover-meta">
                  <Icon name="user" size={11} />
                  {s.author || s.author_username || '—'}
                  <span className="sep">·</span>
                  <Icon name="book_open" size={11} />
                  {fmtN(s.chapter_count || 0)} 章
                  <span className="sep">·</span>
                  <Icon name="download" size={11} />
                  {fmtN(s.clone_count || 0)}
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

/* ─── 主组件 ────────────────────────────────── */
export function MobileScripts({ nav }) {
  const platform = usePlatformData();
  const { saves: platSaves = [] } = platform;

  const [scripts, setScripts] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all'); // all / ready / importing / public
  const [embedStatus, setEmbedStatus] = useState({});
  const [selectedScript, setSelectedScript] = useState(null);
  const [view, setView] = useState('list'); // list / import / library

  const currentUserId = window.RPG_AUTH?.user_id ?? null;

  const reload = useCallback(async () => {
    try {
      const r = await window.api.scripts.list();
      const list = Array.isArray(r) ? r : (r?.items || r?.scripts || []);
      const normed = list.map(window.__normalizeScript || (x => x));
      setScripts(normed);
      // 拉 embed 状态（非阻塞）
      Promise.all(normed.map(async s => {
        try {
          const sr = await fetch(`${window.__API_BASE || ''}/api/scripts/${s.id}/embed/status`, { credentials: 'include' });
          const sj = await sr.json();
          if (sj.ok && sj.status) setEmbedStatus(es => ({ ...es, [s.id]: sj.status }));
        } catch (_) {}
      })).catch(() => {});
    } catch (_) { setScripts([]); }
    finally { setLoaded(true); }
  }, []);

  useEffect(() => {
    reload();
    const refresh = () => reload();
    window.addEventListener('rpg:scripts:changed', refresh);
    window.addEventListener('rpg-scripts-updated', refresh);
    return () => {
      window.removeEventListener('rpg:scripts:changed', refresh);
      window.removeEventListener('rpg-scripts-updated', refresh);
    };
  }, [reload]);

  // 处理路由区分
  useEffect(() => {
    if (nav?.pageId === 'scripts-import') setView('import');
    else if (nav?.pageId === 'scripts-library') setView('library');
    else setView('list');
  }, [nav?.pageId]);

  const FILTERS = [
    { id: 'all', label: '全部' },
    { id: 'ready', label: '已就绪' },
    { id: 'importing', label: '导入中' },
    { id: 'public', label: '已公开' },
  ];

  const visibleScripts = scripts.filter(s => {
    const matchQ = !query.trim() || (`${s.title} ${s.uid}`).toLowerCase().includes(query.toLowerCase());
    let matchF = true;
    if (filter === 'ready') matchF = !isPlayBlocked(s);
    else if (filter === 'importing') matchF = !!s.import_status && ACTIVE_STATUSES.has(String(s.import_status).toLowerCase());
    else if (filter === 'public') matchF = !!s.is_public;
    return matchQ && matchF;
  });

  if (view === 'import') {
    return <ImportView onBack={() => { setView('list'); reload(); }} />;
  }

  if (view === 'library') {
    return <LibraryView onBack={() => setView('list')} nav={nav} />;
  }

  if (selectedScript) {
    return (
      <ScriptDetailView
        script={selectedScript}
        saves={platSaves}
        embedStatus={embedStatus}
        currentUserId={currentUserId}
        onBack={() => setSelectedScript(null)}
        onRefresh={() => { reload(); }}
        nav={nav}
      />
    );
  }

  // 列表视图
  return (
    <>
      <div className="pl-head">
        <div className="pl-head-title">
          <strong style={{ fontSize: 19, fontFamily: 'var(--font-serif)' }}>剧本</strong>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" title="在线库" onClick={() => setView('library')}>
            <Icon name="globe" size={18} />
          </button>
          <button className="pl-headbtn" style={{ color: 'var(--accent)', border: '1px solid var(--accent-edge)', background: 'var(--accent-soft)' }} onClick={() => setView('import')}>
            <Icon name="plus" size={20} />
          </button>
        </div>
      </div>

      <div className="pl-toolbar">
        <div className="pl-search">
          <Icon name="search" size={16} />
          <input
            placeholder="搜索剧本…"
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          {query && <button onClick={() => setQuery('')}><Icon name="close" size={15} /></button>}
        </div>
      </div>

      <div className="pl-seg-scroll">
        {FILTERS.map(f => (
          <button
            key={f.id}
            className={'pl-pill' + (filter === f.id ? ' active' : '')}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="pl-body tabbed">
        <div className="pl-pad" style={{ paddingTop: 4 }}>
          {!loaded && (
            <div className="muted" style={{ fontSize: 13, padding: '20px 0' }}>加载中…</div>
          )}
          {loaded && visibleScripts.length === 0 && (
            <EmptyState
              icon="book_open"
              title={query ? '未找到匹配剧本' : '暂无剧本'}
              desc={query ? '换个关键词试试' : '导入一部小说开始你的 RPG 之旅'}
              action={!query && (
                <button className="pl-btn-primary" style={{ marginTop: 16 }} onClick={() => setView('import')}>
                  <Icon name="upload" size={18} /> 导入剧本
                </button>
              )}
            />
          )}

          {visibleScripts.map(s => {
            const es = embedStatus[s.id];
            const embedDone = es && !es.running && (es.chunks?.done || 0) >= (es.chunks?.total || 1) && (es.chunks?.total || 0) > 0;
            const embedRunning = es?.running;
            const block = isPlayBlocked(s);
            const isInternal = typeof s.title === 'string' && s.title.startsWith('[内部]');
            const savesCount = platSaves.filter(sv => sv.script_id === s.id).length;

            return (
              <button
                key={s.id}
                className="pl-cover-card"
                style={{ marginBottom: 13 }}
                onClick={() => setSelectedScript(s)}
              >
                <div className="pl-cover">
                  <span className="pl-cover-spine" />
                  <h3>{s.title}</h3>
                  {isInternal && (
                    <span className="pill" style={{ position: 'absolute', top: 8, right: 10, height: 18, fontSize: 9.5 }}>敬请期待</span>
                  )}
                  {s.is_public && !isInternal && (
                    <span className="pill ok" style={{ position: 'absolute', top: 8, right: 10, height: 18, fontSize: 9.5 }}>
                      <span className="dot ok" />已公开
                    </span>
                  )}
                  {s.forked_from_script_id && (
                    <span className="pill info" style={{ position: 'absolute', top: isInternal || s.is_public ? 32 : 8, right: 10, height: 18, fontSize: 9.5 }}>fork</span>
                  )}
                </div>
                <div className="pl-cover-body">
                  {/* UID + 更新时间 */}
                  <div className="mono muted-2" style={{ fontSize: 10.5 }}>
                    {s.uid}
                    {s.updated_at && <span> · {s.updated_at}</span>}
                  </div>
                  <div className="pl-cover-meta">
                    <Icon name="book_open" size={11} />
                    {fmtN(s.chapter_count || 0)} 章
                    <span className="sep">·</span>
                    {fmtWan(s.word_count)}
                    {savesCount > 0 && (
                      <><span className="sep">·</span><Icon name="save" size={11} />{savesCount} 存档</>
                    )}
                    <span style={{ flex: 1 }} />
                    {embedRunning
                      ? <span className="pill warn" style={{ height: 18, fontSize: 9.5 }}><span className="dot warn" />索引中</span>
                      : embedDone
                        ? <span className="pill ok" style={{ height: 18, fontSize: 9.5 }}><span className="dot ok" />就绪</span>
                        : block
                          ? <span className="pill" style={{ height: 18, fontSize: 9.5 }}>未就绪</span>
                          : null}
                  </div>
                  {s.import_report?.mode_label && (
                    <div style={{ fontSize: 10.5, color: 'var(--muted-2)' }}>
                      {s.import_report.mode_label}
                      {s.import_report.confidence != null && (
                        <span className="mono" style={{ marginLeft: 5 }}>
                          {Math.round(s.import_report.confidence * 100)}%
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </button>
            );
          })}

          {/* 统计条 */}
          {loaded && scripts.length > 0 && (
            <div style={{ textAlign: 'center', padding: '14px 0 4px', fontSize: 11.5, color: 'var(--muted-2)' }}>
              共 {scripts.length} 部剧本
              {visibleScripts.length !== scripts.length && ` · 显示 ${visibleScripts.length} 部`}
            </div>
          )}
        </div>
      </div>

      {/* FAB 快捷导入 */}
      <button className="pl-fab" onClick={() => setView('import')} title="导入新剧本">
        <Icon name="upload" size={22} />
      </button>
    </>
  );
}

export default MobileScripts;
