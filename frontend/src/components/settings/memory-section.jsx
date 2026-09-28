// 记忆设置区(MemorySection)。纯机械搬出,零行为变化。
import React from 'react';
import { useState as useStatePL, useEffect as useEffectPL } from 'react';
import { useTranslation } from 'react-i18next';
import { useAutoSave } from '../../platform-app.jsx';
import { SetGroup, SetRow } from './shared.jsx';
import { toIntSetting, storedIntSetting } from '../../lib/int-setting.js';
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

  // 数值项保存前一律取整并夹到界面区间,输入框回显取整后的值(后端 MemorySettings 全是 int,
  // 非整数整项丢弃、回落默认值:以前照存 7.5,页面显示 7.5、GM 用的却是默认值)。
  // committed = 最近一次生效的值:清空 / 填了非数字后失焦,回显它,不保存。
  const committed = React.useRef({ recall_depth: 5, summary_window: 10, token_budget: 800, auto_archive_after_turns: 50, pinned_max: 20 });
  const commitInt = (key, raw, min, max, setter) => {
    const n = toIntSetting(raw, min, max);
    if (n === null) { setter(committed.current[key]); return; }
    setter(n);
    if (n !== committed.current[key]) { committed.current[key] = n; save(key, n); }
  };
  // 读到的值同样按后端口径:不是整数(老数据里的 7.5)后端不认、用的是默认值,这里也显示默认值。
  const loadInt = (key, raw, setter) => {
    const n = storedIntSetting(raw);
    if (n === null) return;
    committed.current[key] = n;
    setter(n);
  };

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
        loadInt("recall_depth", loadOrFallback(p, "memory.recall_depth", "settings.召回深度"), setRecallDepth);
        loadInt("summary_window", loadOrFallback(p, "memory.summary_window", "settings.摘要窗口"), setSummaryWindow);
        // pinned_max 同时对应旧 "settings.固定记忆上限"
        loadInt("pinned_max", loadOrFallback(p, "memory.pinned_max", "settings.固定记忆上限"), setPinnedMax);
        // 新字段 — 无旧 key
        loadInt("token_budget", p["memory.token_budget"], setTokenBudget);
        loadInt("auto_archive_after_turns", p["memory.auto_archive_after_turns"], setAutoArchiveAfter);
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
              onMouseUp={(e) => commitInt("recall_depth", e.target.value, 2, 20, setRecallDepth)}
              onTouchEnd={(e) => commitInt("recall_depth", e.target.value, 2, 20, setRecallDepth)}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={2} max={20} step={1} value={recallDepth}
              onChange={(e) => setRecallDepth(e.target.value)}
              onBlur={(e) => commitInt("recall_depth", e.target.value, 2, 20, setRecallDepth)}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
        <SetRow label={t('settings.memory.summary_window')} description={t('settings.memory.summary_window_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={3} max={20} step={1} value={summaryWindow}
              onChange={(e) => setSummaryWindow(Number(e.target.value))}
              onMouseUp={(e) => commitInt("summary_window", e.target.value, 3, 20, setSummaryWindow)}
              onTouchEnd={(e) => commitInt("summary_window", e.target.value, 3, 20, setSummaryWindow)}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={3} max={20} step={1} value={summaryWindow}
              onChange={(e) => setSummaryWindow(e.target.value)}
              onBlur={(e) => commitInt("summary_window", e.target.value, 3, 20, setSummaryWindow)}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
        <SetRow label={t('settings.memory.token_budget')} description={t('settings.memory.token_budget_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={200} max={2000} step={50} value={tokenBudget}
              onChange={(e) => setTokenBudget(Number(e.target.value))}
              onMouseUp={(e) => commitInt("token_budget", e.target.value, 200, 2000, setTokenBudget)}
              onTouchEnd={(e) => commitInt("token_budget", e.target.value, 200, 2000, setTokenBudget)}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={200} max={2000} step={50} value={tokenBudget}
              onChange={(e) => setTokenBudget(e.target.value)}
              onBlur={(e) => commitInt("token_budget", e.target.value, 200, 2000, setTokenBudget)}
              className="mono" style={{width: 70, textAlign: "right"}} />
          </div>
        </SetRow>
        <SetRow label={t('settings.memory.auto_archive')} description={t('settings.memory.auto_archive_desc')}>
          <div style={{display: "flex", alignItems: "center", gap: 8}}>
            <input type="range" min={10} max={200} step={5} value={autoArchiveAfter}
              onChange={(e) => setAutoArchiveAfter(Number(e.target.value))}
              onMouseUp={(e) => commitInt("auto_archive_after_turns", e.target.value, 10, 200, setAutoArchiveAfter)}
              onTouchEnd={(e) => commitInt("auto_archive_after_turns", e.target.value, 10, 200, setAutoArchiveAfter)}
              style={{flex: 1, minWidth: 120}} />
            <input type="number" min={10} max={200} step={5} value={autoArchiveAfter}
              onChange={(e) => setAutoArchiveAfter(e.target.value)}
              onBlur={(e) => commitInt("auto_archive_after_turns", e.target.value, 10, 200, setAutoArchiveAfter)}
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
              // 边输边存(老行为),存的是取整后的值;区间外的先不存,失焦时再夹到区间里并回显。
              const r = Math.round(Number(detail.value));
              if (detail.value !== '' && r >= 5 && r <= 100 && r !== committed.current.pinned_max) {
                committed.current.pinned_max = r;
                save("pinned_max", r);
              }
            }}
            onBlur={() => commitInt("pinned_max", pinnedMax, 5, 100, setPinnedMax)} />
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
