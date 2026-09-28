// 记忆设置区(MemorySection)。纯机械搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { useAutoSave } from '../../platform-app.jsx';
import { SetGroup, SetRow } from './shared.jsx';
import CSSpaceBetween from '@cloudscape-design/components/space-between';
import CSInput from '@cloudscape-design/components/input';
import CSToggle from '@cloudscape-design/components/toggle';

function MemorySection() {
  const { t } = useTranslation();
  // A6.2: useAutoSave namespace 改为 "memory" 让 save(k, v) 写 memory.k
  const save = useAutoSave(t('settings.nav.memory'), "memory");

  // ── 召回行为字段 ──
  // 初值 = 后端 MemorySettings 的默认值(没设过的项 GM 实际用的就是它),不能另写一套;
  // 守卫 rpg/tests/unit/test_memory_settings_display_defaults.py。
  const [recallDepth, setRecallDepth] = useStatePL(5);
  const [summaryWindow, setSummaryWindow] = useStatePL(10);
  const [tokenBudget, setTokenBudget] = useStatePL(800);
  const [autoArchiveAfter, setAutoArchiveAfter] = useStatePL(50);

  // ── 记忆桶配置字段 ──
  const [pinnedMax, setPinnedMax] = useStatePL(20);
  const [bucketPinnedEnabled, setBucketPinnedEnabled] = useStatePL(true);
  const [bucketWorldEnabled, setBucketWorldEnabled] = useStatePL(true);
  const [bucketCharacterEnabled, setBucketCharacterEnabled] = useStatePL(true);

  // A6.2: loadOrFallback — 读新 key 优先,不存在再读旧 key
  const loadOrFallback = (p, newKey, oldKey) => {
    if (p[newKey] !== undefined && p[newKey] !== null) return p[newKey];
    if (oldKey && p[oldKey] !== undefined && p[oldKey] !== null) return p[oldKey];
    return undefined;
  };

  useEffectPL(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        // A6.2: 读新 key，兼容旧中文 key
        const rd = loadOrFallback(p, "memory.recall_depth", "settings.召回深度");
        if (rd !== undefined) setRecallDepth(Number(rd));
        const sw = loadOrFallback(p, "memory.summary_window", "settings.摘要窗口");
        if (sw !== undefined) setSummaryWindow(Number(sw));
        // pinned_max 同时对应旧 "settings.固定记忆上限"
        const pm = loadOrFallback(p, "memory.pinned_max", "settings.固定记忆上限");
        if (pm !== undefined) setPinnedMax(Number(pm));
        // 新字段 — 无旧 key
        if (p["memory.token_budget"] !== undefined) setTokenBudget(Number(p["memory.token_budget"]));
        if (p["memory.auto_archive_after_turns"] !== undefined) setAutoArchiveAfter(Number(p["memory.auto_archive_after_turns"]));
        if (typeof p["memory.bucket_pinned_enabled"] === "boolean") setBucketPinnedEnabled(p["memory.bucket_pinned_enabled"]);
        if (typeof p["memory.bucket_world_enabled"] === "boolean") setBucketWorldEnabled(p["memory.bucket_world_enabled"]);
        if (typeof p["memory.bucket_character_enabled"] === "boolean") setBucketCharacterEnabled(p["memory.bucket_character_enabled"]);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <CSSpaceBetween size="l">
      {/* A6.3 — 组 1: 召回行为 */}
      <SetGroup title={t('settings.memory.title_recall')}>
        <SetRow label={t('settings.memory.recall_depth')} description={t('settings.memory.recall_depth_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={2} max={20} step={1} value={recallDepth}
              onChange={(e) => setRecallDepth(Number(e.target.value))}
              onMouseUp={(e) => { const n = Number(e.target.value); if (n >= 2 && n <= 20) save("recall_depth", n); }}
              onTouchEnd={(e) => { const n = Number(e.target.value); if (n >= 2 && n <= 20) save("recall_depth", n); }}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={2} max={20} step={1} value={recallDepth}
              onChange={(e) => setRecallDepth(Number(e.target.value))}
              onBlur={(e) => { const n = Number(e.target.value); if (n >= 2 && n <= 20) save("recall_depth", n); }}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
        <SetRow label={t('settings.memory.summary_window')} description={t('settings.memory.summary_window_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={3} max={20} step={1} value={summaryWindow}
              onChange={(e) => setSummaryWindow(Number(e.target.value))}
              onMouseUp={(e) => { const n = Number(e.target.value); if (n >= 3 && n <= 20) save("summary_window", n); }}
              onTouchEnd={(e) => { const n = Number(e.target.value); if (n >= 3 && n <= 20) save("summary_window", n); }}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={3} max={20} step={1} value={summaryWindow}
              onChange={(e) => setSummaryWindow(Number(e.target.value))}
              onBlur={(e) => { const n = Number(e.target.value); if (n >= 3 && n <= 20) save("summary_window", n); }}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
        <SetRow label={t('settings.memory.token_budget')} description={t('settings.memory.token_budget_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={200} max={2000} step={50} value={tokenBudget}
              onChange={(e) => setTokenBudget(Number(e.target.value))}
              onMouseUp={(e) => { const n = Number(e.target.value); if (n >= 200 && n <= 2000) save("token_budget", n); }}
              onTouchEnd={(e) => { const n = Number(e.target.value); if (n >= 200 && n <= 2000) save("token_budget", n); }}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={200} max={2000} step={50} value={tokenBudget}
              onChange={(e) => setTokenBudget(Number(e.target.value))}
              onBlur={(e) => { const n = Number(e.target.value); if (n >= 200 && n <= 2000) save("token_budget", n); }}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
        <SetRow label={t('settings.memory.auto_archive')} description={t('settings.memory.auto_archive_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={10} max={200} step={5} value={autoArchiveAfter}
              onChange={(e) => setAutoArchiveAfter(Number(e.target.value))}
              onMouseUp={(e) => { const n = Number(e.target.value); if (n >= 10 && n <= 200) save("auto_archive_after_turns", n); }}
              onTouchEnd={(e) => { const n = Number(e.target.value); if (n >= 10 && n <= 200) save("auto_archive_after_turns", n); }}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={10} max={200} step={5} value={autoArchiveAfter}
              onChange={(e) => setAutoArchiveAfter(Number(e.target.value))}
              onBlur={(e) => { const n = Number(e.target.value); if (n >= 10 && n <= 200) save("auto_archive_after_turns", n); }}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
      </SetGroup>

      {/* A6.3 — 组 2: 记忆桶配置 */}
      <SetGroup title={t('settings.memory.title_buckets')}>
        <SetRow label={t('settings.memory.pinned_max')} description={t('settings.memory.pinned_max_desc')}>
          <CSInput type="number" value={String(pinnedMax)}
            onChange={({ detail }) => {
              setPinnedMax(detail.value);
              const n = Number(detail.value);
              if (detail.value !== '' && n >= 5 && n <= 100) save("pinned_max", n);
            }} />
        </SetRow>
        <SetRow label={t('settings.memory.bucket_pinned')} description={t('settings.memory.bucket_pinned_desc')}>
          <CSToggle checked={bucketPinnedEnabled}
            onChange={({ detail }) => { setBucketPinnedEnabled(detail.checked); save("bucket_pinned_enabled", detail.checked); }}>
            {bucketPinnedEnabled ? t('common.enabled') : t('common.disabled')}
          </CSToggle>
        </SetRow>
        <SetRow label={t('settings.memory.bucket_world')} description={t('settings.memory.bucket_world_desc')}>
          <CSToggle checked={bucketWorldEnabled}
            onChange={({ detail }) => { setBucketWorldEnabled(detail.checked); save("bucket_world_enabled", detail.checked); }}>
            {bucketWorldEnabled ? t('common.enabled') : t('common.disabled')}
          </CSToggle>
        </SetRow>
        <SetRow label={t('settings.memory.bucket_character')} description={t('settings.memory.bucket_character_desc')}>
          <CSToggle checked={bucketCharacterEnabled}
            onChange={({ detail }) => { setBucketCharacterEnabled(detail.checked); save("bucket_character_enabled", detail.checked); }}>
            {bucketCharacterEnabled ? t('common.enabled') : t('common.disabled')}
          </CSToggle>
        </SetRow>
      </SetGroup>
    </CSSpaceBetween>
  );
}

export {
  MemorySection,
};
