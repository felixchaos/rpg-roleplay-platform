import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { SetGroup, MSlider, Toggle, usePrefSave } from './shared.jsx';
import { toIntSetting, storedIntSetting } from '../../lib/int-setting.js';

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
  // 固定记忆上限是自由输入框:清空 / 非数字失焦时回显最近一次的有效值
  const lastPinned = useRef(20);
  const setPinnedValid = (n) => { lastPinned.current = n; setPinnedMax(n); };
  const [bucketPinned, setBucketPinned] = useState(true);
  const [bucketWorld, setBucketWorld] = useState(true);
  const [bucketChar, setBucketChar] = useState(true);

  // 数值项按后端口径(MemorySettings 全是 int):保存前取整并夹到区间,回显取整后的值;
  // 读回来的非整数老数据后端不认、实际用默认值,这里也保持默认值显示(与桌面同一个 lib/int-setting.js)。
  const loadInt = (raw, setter) => { const n = storedIntSetting(raw); if (n !== null) setter(n); };
  const commitInt = (key, raw, min, max, setter, fallback) => {
    const n = toIntSetting(raw, min, max);
    if (n === null) { setter(fallback); return; }
    setter(n);
    save(key, n);
  };

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
        loadInt(loadOr(p, 'memory.recall_depth', 'settings.召回深度'), setRecallDepth);
        loadInt(loadOr(p, 'memory.summary_window', 'settings.摘要窗口'), setSummaryWindow);
        loadInt(loadOr(p, 'memory.pinned_max', 'settings.固定记忆上限'), setPinnedValid);
        loadInt(p['memory.token_budget'], setTokenBudget);
        loadInt(p['memory.auto_archive_after_turns'], setAutoArchive);
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
            onClick={() => commitInt('recall_depth', recallDepth, 2, 20, setRecallDepth, recallDepth)}>
            <Icon name="save" size={14} /> {t('common.save')}
          </button>
        </div>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={t('mobile.settings.memory.summary_window_label', { n: summaryWindow })} desc={t('mobile.settings.memory.summary_window_desc')}
            value={summaryWindow} min={3} max={20} step={1}
            onChange={(v) => setSummaryWindow(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => commitInt('summary_window', summaryWindow, 3, 20, setSummaryWindow, summaryWindow)}>
            <Icon name="save" size={14} /> {t('common.save')}
          </button>
        </div>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={t('mobile.settings.memory.token_budget_label', { n: tokenBudget })} desc={t('mobile.settings.memory.token_budget_desc')}
            value={tokenBudget} min={200} max={2000} step={50}
            onChange={(v) => setTokenBudget(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => commitInt('token_budget', tokenBudget, 200, 2000, setTokenBudget, tokenBudget)}>
            <Icon name="save" size={14} /> {t('common.save')}
          </button>
        </div>
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 10 }}>
          <MSlider label={t('mobile.settings.memory.auto_archive_label', { n: autoArchive })} desc={t('mobile.settings.memory.auto_archive_desc')}
            value={autoArchive} min={10} max={200} step={5}
            onChange={(v) => setAutoArchive(v)} />
          <button className="pl-btn-ghost" style={{ height:36, fontSize:13 }}
            onClick={() => commitInt('auto_archive_after_turns', autoArchive, 10, 200, setAutoArchive, autoArchive)}>
            <Icon name="save" size={14} /> {t('common.save')}
          </button>
        </div>
      </SetGroup>

      <SetGroup title={t('mobile.settings.memory.buckets')}>
        <div className="pl-setrow">
          <div className="pl-setrow-tx"><strong>{t('mobile.settings.memory.pinned_max')}</strong><span>{t('mobile.settings.memory.pinned_max_desc')}</span></div>
          <input
            type="number" min={5} max={100} value={pinnedMax}
            onChange={(e) => setPinnedMax(e.target.value)}
            onBlur={(e) => commitInt('pinned_max', e.target.value, 5, 100, setPinnedValid, lastPinned.current)}
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
