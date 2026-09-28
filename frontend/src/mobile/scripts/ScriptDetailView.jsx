/* 剧本详情子视图 —— 从 pages/MobileScripts.jsx 拆出,逐字节不变。 */

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { isCredentialsError } from '../../lib/creds.js';
import { fmtN, isPlayBlocked } from './helpers.js';
import { ChaptersView } from './ChaptersView.jsx';
import { WorldbookView } from './WorldbookView.jsx';
import { NpcView } from './NpcView.jsx';
import { TimelineView } from './TimelineView.jsx';
import { VersionsView } from './VersionsView.jsx';
import { OverridesView } from './OverridesView.jsx';
import { ShareView } from './ShareView.jsx';

/* ─── 剧本详情子视图 ───────────────────────────── */
function ScriptDetailView({ script, saves, embedStatus, currentUserId, onBack, onRefresh, nav }) {
  const { t } = useTranslation();
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

  // 开局闸只拦开新档:已有存档时「继续游戏」不受它影响(导入/重建中也能回到旧存档)。
  const playDisabled = !!playBlock && savesCount === 0;
  const onPlay = async () => {
    const sv = scriptSaves[0];
    if (sv) { nav.openGame?.(sv); return; }
    if (playBlock) { nav.toast(playBlock, 'accent', 'warn'); return; }
    nav.push?.('new-game', { scriptId: script.id });   // 无存档 → 进新游戏向导(锁定本剧本)
  };

  const onNewGame = async () => {
    if (playBlock) { nav.toast(playBlock, 'accent', 'warn'); return; }
    nav.push?.('new-game', { scriptId: script.id });   // 新建存档 → 新游戏向导
  };

  const onEmbed = async () => {
    if (embedRunning) return;
    try {
      const r = await fetch(`${window.__API_BASE || ''}/api/scripts/${script.id}/embed`, { method: 'POST', credentials: 'include' });
      const j = await r.json();
      if (j.ok === false) {
        if (isCredentialsError(j)) {
          nav.toast(t('mobile.scripts.detail.embed_no_creds'), 'accent', 'warn');
        } else {
          nav.toast(j.error || t('mobile.scripts.detail.embed_start_error'), 'danger', 'warn');
        }
        return;
      }
      nav.toast(t('mobile.scripts.detail.embed_started'), 'ok', 'check');
    } catch (e) { nav.toast(String(e), 'danger', 'warn'); }
  };

  const onDelete = async () => {
    if (!await window.__confirm({ message: t('mobile.scripts.detail.confirm_delete', { title: script.title }), danger: true })) return;
    try {
      const r = await window.api.scripts.delete(script.id, { force: true });
      if (!r || r.ok !== true) throw new Error(r?.error || t('mobile.scripts.detail.delete_error'));
      nav.toast(t('mobile.scripts.detail.deleted'), 'ok', 'check');
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      onBack();
    } catch (e) { nav.toast(e?.message || t('mobile.scripts.detail.delete_error'), 'danger', 'warn'); }
  };

  const onUnsubscribe = async () => {
    if (!await window.__confirm({ message: t('mobile.scripts.detail.confirm_unsubscribe', { title: script.title }), danger: true })) return;
    try {
      const r = await window.api.scripts.unsubscribe(script.id);
      if (!r || r.ok !== true) throw new Error(r?.error || t('mobile.scripts.detail.unsubscribe_error'));
      nav.toast(t('mobile.scripts.detail.unsubscribed'), 'ok', 'check');
      try { window.dispatchEvent(new CustomEvent('rpg-scripts-updated')); } catch (_) {}
      onBack();
    } catch (e) { nav.toast(e?.message || t('mobile.scripts.detail.unsubscribe_error'), 'danger', 'warn'); }
  };

  if (subView === 'chapters') return <ChaptersView script={script} onBack={() => setSubView(null)} nav={nav} />;
  if (subView === 'worldbook') return <WorldbookView script={script} onBack={() => setSubView(null)} />;
  if (subView === 'npc') return <NpcView script={script} onBack={() => setSubView(null)} />;
  if (subView === 'timeline') return <TimelineView script={script} onBack={() => setSubView(null)} />;
  if (subView === 'versions') return <VersionsView script={script} currentUserId={currentUserId} onBack={() => setSubView(null)} nav={nav} />;
  if (subView === 'overrides') return <OverridesView script={script} onBack={() => setSubView(null)} nav={nav} />;
  if (subView === 'share') return <ShareView script={script} currentUserId={currentUserId} onBack={() => setSubView(null)} onRefresh={onRefresh} nav={nav} />;

  const isInternal = typeof script.title === 'string' && script.title.startsWith('[内部]');

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack} aria-label={t('common.back')}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title">
          <strong style={{ fontSize: 14.5 }}>{script.title}</strong>
          <span className="sub mono">{script.uid}</span>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={() => setSubView('share')} title={t('mobile.scripts.detail.share_title')}>
            <Icon name={script.is_public ? 'globe' : 'upload'} size={17} />
          </button>
          {script.is_subscribed ? (
            <button className="pl-headbtn" onClick={onUnsubscribe} title={t('mobile.scripts.detail.unsubscribe_title')} style={{ color: 'var(--danger)' }}>
              <Icon name="trash" size={17} />
            </button>
          ) : (
            <button className="pl-headbtn" onClick={onDelete} title={t('common.delete')} style={{ color: 'var(--danger)' }}>
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
              <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, fontFamily: 'var(--font-serif)' }}>{t('mobile.scripts.detail.coming_soon')}</div>
              <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.65 }}>
                {t('mobile.scripts.detail.coming_soon_desc')}
              </div>
            </div>
          ) : (
            <>
              {/* 统计 */}
              <div className="pl-stats" style={{ marginBottom: 16 }}>
                <div className="pl-stat"><span className="n accent">{fmtN(script.chapter_count || 0)}</span><div className="l">{t('mobile.scripts.detail.stat_chapters')}</div></div>
                <div className="pl-stat"><span className="n">{((Number(script.word_count) || 0) / 10000).toFixed(1)}<span style={{ fontSize: 11 }}>{t('mobile.scripts.unit.wan')}</span></span><div className="l">{t('mobile.scripts.detail.stat_words')}</div></div>
                <div className="pl-stat"><span className="n">{savesCount}</span><div className="l">{t('mobile.scripts.detail.stat_saves')}</div></div>
                <div className="pl-stat">
                  <span className="n" style={{ fontSize: 14, color: embedDone ? 'var(--ok)' : embedRunning ? 'var(--warn)' : 'var(--muted)' }}>
                    {embedRunning ? `${embedPct}%` : embedDone ? '✓' : '—'}
                  </span>
                  <div className="l">{t('mobile.scripts.detail.stat_index')}</div>
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
                  <div className="pl-sec-head"><h2>{t('mobile.scripts.detail.import_report')}</h2></div>
                  <div className="pl-card">
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, fontSize: 12.5 }}>
                      <span style={{ color: 'var(--muted)' }}>{t('mobile.scripts.detail.split_mode')}</span>
                      <span>{script.import_report.mode_label || '—'}</span>
                    </div>
                    {script.import_report.confidence != null && (
                      <div style={{ marginBottom: 8 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, fontSize: 12 }}>
                          <span className="muted-2">{t('mobile.scripts.detail.confidence')}</span>
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
                  <div className="pl-sec-head"><h2>{t('mobile.scripts.detail.readiness')}</h2></div>
                  {(script.readiness.items || []).filter(it => !it.ok).map((it, i) => (
                    <button key={i} className="pl-row" onClick={() => {
                      const tabMap = { chunks: 'chapters', embeddings: 'chapters', canon: null, worldbook: 'worldbook', anchors: 'timeline' };
                      const sv = tabMap[it.key];
                      if (sv) setSubView(sv);
                    }}>
                      <span className="pl-row-ic warn"><Icon name="warn" size={17} /></span>
                      <span className="pl-row-tx">
                        <strong>{it.key}</strong>
                        <span>{it.total > 0 ? `${it.count}/${it.total}` : t('mobile.scripts.detail.not_built')}</span>
                      </span>
                      <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                    </button>
                  ))}
                </div>
              )}

              {/* 模块导航 */}
              <div className="pl-sec">
                <div className="pl-sec-head"><h2>{t('mobile.scripts.detail.modules_section')}</h2></div>
                <button className="pl-row" onClick={() => setSubView('chapters')}>
                  <span className="pl-row-ic accent"><Icon name="book_open" size={17} /></span>
                  <span className="pl-row-tx"><strong>{t('mobile.scripts.chapters.title')}</strong><span>{t('mobile.scripts.detail.chapters_desc')}</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('worldbook')}>
                  <span className="pl-row-ic ok"><Icon name="world" size={17} /></span>
                  <span className="pl-row-tx"><strong>{t('mobile.scripts.worldbook.title')}</strong><span>{t('mobile.scripts.detail.worldbook_desc')}</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('npc')}>
                  <span className="pl-row-ic info"><Icon name="cards" size={17} /></span>
                  <span className="pl-row-tx"><strong>{t('mobile.scripts.npc.title')}</strong><span>{t('mobile.scripts.detail.npc_desc')}</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('timeline')}>
                  <span className="pl-row-ic warn"><Icon name="timeline" size={17} /></span>
                  <span className="pl-row-tx"><strong>{t('mobile.scripts.timeline.title')}</strong><span>{t('mobile.scripts.detail.timeline_desc')}</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
              </div>

              {/* 进阶 */}
              <div className="pl-sec">
                <div className="pl-sec-head"><h2>{t('mobile.scripts.detail.advanced_section')}</h2></div>
                <button className="pl-row" onClick={() => setSubView('overrides')}>
                  <span className="pl-row-ic"><Icon name="settings" size={17} /></span>
                  <span className="pl-row-tx"><strong>{t('mobile.scripts.overrides.title')}</strong><span>script_overrides JSONB</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('versions')}>
                  <span className="pl-row-ic"><Icon name="history" size={17} /></span>
                  <span className="pl-row-tx"><strong>{t('mobile.scripts.versions.title')}</strong><span>{t('mobile.scripts.detail.versions_desc')}</span></span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={() => setSubView('share')}>
                  <span className="pl-row-ic ok"><Icon name={script.is_public ? 'globe' : 'lock'} size={17} /></span>
                  <span className="pl-row-tx">
                    <strong>{t('mobile.scripts.share.title')}</strong>
                    <span>{script.is_public ? t('mobile.scripts.detail.share_published') : t('mobile.scripts.share.publish_off_desc')} · {t('mobile.scripts.share.export_btn')}</span>
                  </span>
                  <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
                </button>
                <button className="pl-row" onClick={onEmbed} disabled={embedRunning}>
                  <span className={'pl-row-ic ' + (embedDone ? 'ok' : '')}><Icon name="sparkle" size={17} /></span>
                  <span className="pl-row-tx">
                    <strong>{t('mobile.scripts.detail.stat_index')}</strong>
                    <span>
                      {embedRunning ? t('mobile.scripts.detail.embed_running', { pct: embedPct }) : embedDone ? t('mobile.scripts.detail.embed_done', { count: totalAll }) : t('mobile.scripts.detail.embed_none')}
                    </span>
                  </span>
                  <span className="pl-row-chev"><Icon name={embedRunning ? 'refresh' : 'chevron_right'} size={17} /></span>
                </button>
              </div>

              {/* 不是自己的剧本 → 可 fork */}
              {!isOwner && script.owner_id && (
                <div style={{ marginTop: 16, padding: '12px 14px', borderRadius: 12, background: 'var(--info-soft)', border: '1px solid rgba(122,166,194,0.3)', fontSize: 13, color: 'var(--text-quiet)', lineHeight: 1.6 }}>
                  {t('mobile.scripts.detail.not_owner_short')}
                </div>
              )}

              {/* 主操作区 */}
              <div style={{ display: 'grid', gap: 9, marginTop: 22 }}>
                <button className="pl-btn-primary" onClick={onPlay} disabled={playDisabled}>
                  <Icon name="play" size={18} />
                  {scriptSaves.length > 0 ? t('mobile.scripts.detail.continue_game', { count: scriptSaves.length }) : playBlock ? t('mobile.scripts.detail.play_blocked') : t('mobile.scripts.detail.start_game')}
                </button>
                {scriptSaves.length > 0 && (
                  <button className="pl-btn-ghost" onClick={onNewGame}>
                    <Icon name="plus" size={16} /> {t('mobile.scripts.detail.new_save')}
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

export { ScriptDetailView };
