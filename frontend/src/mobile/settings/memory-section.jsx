import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { SetGroup, MSlider, Toggle, usePrefSave } from './shared.jsx';

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 记忆 (memory)                                              */
/* ────────────────────────────────────────────────────────────────── */
function MemorySection() {
  const { t } = useTranslation();
  const save = usePrefSave('memory');
  // 初值 = 后端 MemorySettings 的默认值(守卫 test_memory_settings_display_defaults.py)
  const [recallDepth, setRecallDepth] = useState(5);
  const [summaryWindow, setSummaryWindow] = useState(10);
  const [tokenBudget, setTokenBudget] = useState(800);
  const [autoArchive, setAutoArchive] = useState(50);
  const [pinnedMax, setPinnedMax] = useState(20);
  const [bucketPinned, setBucketPinned] = useState(true);
  const [bucketWorld, setBucketWorld] = useState(true);
  const [bucketChar, setBucketChar] = useState(true);

  const loadOr = (p, nk, ok) => {
    if (p[nk]!==undefined && p[nk]!==null) return p[nk];
    if (ok && p[ok]!==undefined && p[ok]!==null) return p[ok];
    return undefined;
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        const rd = loadOr(p, 'memory.recall_depth', 'settings.召回深度');
        if (rd !== undefined) setRecallDepth(Number(rd));
        const sw = loadOr(p, 'memory.summary_window', 'settings.摘要窗口');
        if (sw !== undefined) setSummaryWindow(Number(sw));
        const pm = loadOr(p, 'memory.pinned_max', 'settings.固定记忆上限');
        if (pm !== undefined) setPinnedMax(Number(pm));
        if (p['memory.token_budget'] !== undefined) setTokenBudget(Number(p['memory.token_budget']));
        if (p['memory.auto_archive_after_turns'] !== undefined) setAutoArchive(Number(p['memory.auto_archive_after_turns']));
        if (typeof p['memory.bucket_pinned_enabled'] === 'boolean') setBucketPinned(p['memory.bucket_pinned_enabled']);
        if (typeof p['memory.bucket_world_enabled'] === 'boolean') setBucketWorld(p['memory.bucket_world_enabled']);
        if (typeof p['memory.bucket_character_enabled'] === 'boolean') setBucketChar(p['memory.bucket_character_enabled']);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <>
      <SetGroup title={t('mobile.settings.memory.recall_behavior')}>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={t('mobile.settings.memory.recall_depth_label', { n: recallDepth })} desc={t('mobile.settings.memory.recall_depth_desc')}
            value={recallDepth} min={2} max={20} step={1}
            onChange={(v) => setRecallDepth(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => { const n=Math.max(2,Math.min(20,recallDepth)); save('recall_depth',n); }}>
            <Icon name="save" size={14} /> {t('common.save')}
          </button>
        </div>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={t('mobile.settings.memory.summary_window_label', { n: summaryWindow })} desc={t('mobile.settings.memory.summary_window_desc')}
            value={summaryWindow} min={3} max={20} step={1}
            onChange={(v) => setSummaryWindow(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => { const n=Math.max(3,Math.min(20,summaryWindow)); save('summary_window',n); }}>
            <Icon name="save" size={14} /> {t('common.save')}
          </button>
        </div>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={t('mobile.settings.memory.token_budget_label', { n: tokenBudget })} desc={t('mobile.settings.memory.token_budget_desc')}
            value={tokenBudget} min={200} max={2000} step={50}
            onChange={(v) => setTokenBudget(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => { const n=Math.max(200,Math.min(2000,tokenBudget)); save('token_budget',n); }}>
            <Icon name="save" size={14} /> {t('common.save')}
          </button>
        </div>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={t('mobile.settings.memory.auto_archive_label', { n: autoArchive })} desc={t('mobile.settings.memory.auto_archive_desc')}
            value={autoArchive} min={10} max={200} step={5}
            onChange={(v) => setAutoArchive(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => { const n=Math.max(10,Math.min(200,autoArchive)); save('auto_archive_after_turns',n); }}>
            <Icon name="save" size={14} /> {t('common.save')}
          </button>
        </div>
      </SetGroup>

      <SetGroup title={t('mobile.settings.memory.buckets')}>
        <div className="pl-setrow">
          <div className="pl-setrow-tx"><strong>{t('mobile.settings.memory.pinned_max')}</strong><span>{t('mobile.settings.memory.pinned_max_desc')}</span></div>
          <input
            type="number" min={5} max={100} value={pinnedMax}
            onChange={(e) => setPinnedMax(Number(e.target.value))}
            onBlur={(e) => { const n=Math.max(5,Math.min(100,Number(e.target.value))); setPinnedMax(n); save('pinned_max',n); }}
            style={{ width:72, fontSize:15, textAlign:'center', padding:'6px', border:'1px solid var(--line)', borderRadius:8, background:'var(--bg-deep)', color:'var(--text)' }}
          />
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx"><strong>{t('mobile.settings.memory.bucket_pinned')}</strong><span>{t('mobile.settings.memory.bucket_pinned_desc')}</span></div>
          <Toggle on={bucketPinned} onChange={(v) => { setBucketPinned(v); save('bucket_pinned_enabled',v); }} />
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx"><strong>{t('mobile.settings.memory.bucket_world')}</strong><span>{t('mobile.settings.memory.bucket_world_desc')}</span></div>
          <Toggle on={bucketWorld} onChange={(v) => { setBucketWorld(v); save('bucket_world_enabled',v); }} />
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx"><strong>{t('mobile.settings.memory.bucket_char')}</strong><span>{t('mobile.settings.memory.bucket_char_desc')}</span></div>
          <Toggle on={bucketChar} onChange={(v) => { setBucketChar(v); save('bucket_character_enabled',v); }} />
        </div>
      </SetGroup>
    </>
  );
}

export { MemorySection };
