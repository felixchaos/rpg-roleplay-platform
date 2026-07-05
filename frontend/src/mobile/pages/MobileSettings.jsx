/* MobileSettings.jsx — 移动端设置页(单文件,内部 section 状态切换)
   覆盖路由: settings / settings-models / settings-modelparams / settings-modules
            / settings-memory / settings-permissions / settings-account / settings-danger
   铁律:零 Cloudscape / 零电脑端 UI 复用;数据层全接 window.api.* 真实接口。
   ──────────────────────────────────────────────────────────────────────── */
import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Icon } from '../icons.jsx';
import { usePlatformData, useReactiveUser } from '../../platform-app.jsx';
// 模型选择器全站唯一规范组件(用户强制指令:不再自造 <select>)。Cloudscape 已在 platform 入口打包,
// 移动端复用同一实现,保证「已配 key 过滤 / capability 过滤 / 自定义手填 / dict 落库 / 跟随主 GM」完全一致。
import AgentModelPicker from '../../components/AgentModelPicker.jsx';
// Provider 别名表 + 归一化/方向转换上提到 components/catalog-helpers.js(语义统一 #16),
// 本文件保留短名薄别名,调用点零变化:
//   normId = normalizeProviderId(全别名表) · credId = catalogToCredentialId · catId = credentialToCatalogId
import { normalizeProviderId, credentialToCatalogId, catalogToCredentialId } from '../../components/catalog-helpers.js';
// 模块结构数组单一来源(语义统一 #19);移动端 label/tip 文案精简,保留本地按 id 取。
import { MODULES as AGENT_MODULES, MODULE_GROUPS, FEATURES as AGENT_FEATURES } from '../../agent-modules.js';
import { readScopedPref, readNumberPref } from '../../lib/prefs.js';
import { lsSetJSON } from '../../lib/storage.js';
// 竖排字段统一到 mobile/Field.jsx(语义统一 #36);本地原 MField 与之 DOM/CSS 逐字节一致,
// 故 import 为同名 MField,调用点 <MField label desc>{control}</MField> 零变化。
import { Field as MField } from '../Field.jsx';

/* ── 工具函数 ────────────────────────────────────────────────────── */
const normId = normalizeProviderId;
const credId = catalogToCredentialId;
const catId = credentialToCatalogId;

// K/M 缩写统一到 window.__fmt.compact(data-loader.js;语义统一 #30),保留本地别名免改调用点。
function fmtCtx(n) {
  if (window.__fmt && window.__fmt.compact) return window.__fmt.compact(n);
  if (!n) return '—';
  if (n>=1_000_000) return `${(n/1_000_000).toFixed(0)}M`;
  if (n>=1_000) return `${(n/1_000).toFixed(0)}K`;
  return String(n);
}

/* ── 可复用小件 ─────────────────────────────────────────────────── */
function Toggle({ on, onChange }) {
  return (
    <button
      className={'pl-toggle' + (on ? ' on' : '')}
      onClick={() => onChange(!on)}
      role="switch"
      aria-checked={on}
    />
  );
}

function SetGroup({ title, children, action }) {
  return (
    <div className="pl-sec">
      <div className="pl-sec-head">
        <h2>{title}</h2>
        {action}
      </div>
      <div className="pl-group">{children}</div>
    </div>
  );
}

function MSlider({ label, desc, value, min, max, step, onChange }) {
  const decimals = step < 1 ? (String(step).split('.')[1]||'').length : 0;
  return (
    <div className="pl-field">
      <div className="pl-slider-head">
        <span className="lab">{label}</span>
        <span className="val">{Number(value).toFixed(decimals)}</span>
      </div>
      {desc && <span className="desc" style={{ fontSize: 11, color: 'var(--muted-2)', marginBottom: 6, display: 'block' }}>{desc}</span>}
      <input
        className="pl-slider"
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

function Seg({ options, value, onChange }) {
  return (
    <div className="pl-seg2">
      {options.map(([id, label]) => (
        <button
          key={id}
          className={value === id ? 'active accent' : ''}
          onClick={() => onChange(id)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/* 存入 user_preferences 的自动保存 helper — 直接 POST 用户偏好 */
function usePrefSave(namespace) {
  const timerRef = useRef(null);
  const pendRef = useRef({});
  const save = useCallback((key, value) => {
    const fullKey = namespace ? `${namespace}.${key}` : key;
    pendRef.current[fullKey] = value;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(async () => {
      const batch = pendRef.current;
      pendRef.current = {};
      try {
        await window.api.account.preferences(batch);
      } catch (_) {}
    }, 400);
  }, [namespace]);
  return save;
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 偏好 (preferences)                                        */
/* ────────────────────────────────────────────────────────────────── */
function PrefSection({ nav }) {
  const { t } = useTranslation();
  const save = usePrefSave('pref');
  const [lang, setLang] = useState('zh-CN');
  const [serif, setSerif] = useState(true);
  const [auto, setAuto] = useState(true);
  const [blackSwan, setBlackSwan] = useState(false);
  const [threshold, setThreshold] = useState(0.5);
  const saveCurator = usePrefSave('curator');
  const saveBS = usePrefSave('black_swan');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        if (p['pref.ui_language']) setLang(p['pref.ui_language']);
        if (typeof p['pref.serif'] === 'boolean') setSerif(p['pref.serif']);
        if (typeof p['pref.autosave'] === 'boolean') setAuto(p['pref.autosave']);
        if (typeof p['black_swan.enabled'] === 'boolean') setBlackSwan(p['black_swan.enabled']);
        const raw = p['curator.confidence_threshold'];
        if (raw !== undefined && raw !== null) {
          const v = Number(raw);
          if (Number.isFinite(v)) setThreshold(Math.max(0, Math.min(1, v)));
        }
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const commitThreshold = (v) => {
    const n = Math.max(0, Math.min(1, Math.round(Number(v) * 20) / 20));
    setThreshold(n);
    saveCurator('confidence_threshold', n);
  };

  return (
    <>
      {/* 界面偏好 */}
      <SetGroup title={t('mobile.settings.pref.ui_prefs')}>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.pref.ui_language')}</strong>
            <span>{t('mobile.settings.pref.ui_language_desc')}</span>
          </div>
          <div className="pl-seg2" style={{ marginLeft: 'auto', flexShrink: 0, width: 170 }}>
            {[['zh-CN','简体'],['zh-TW','繁體'],['en','EN']].map(([id, l]) => (
              <button key={id} className={lang===id?'active accent':''} onClick={() => {
                setLang(id); save('ui_language', id);
                import('../../i18n/index.js').then(m => m.changeLanguage(id)).catch(() => {});
              }}>{l}</button>
            ))}
          </div>
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.pref.serif_font')}</strong>
            <span>{t('mobile.settings.pref.serif_font_desc')}</span>
          </div>
          <Toggle on={serif} onChange={(v) => { setSerif(v); save('serif', v); }} />
        </div>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.pref.autosave')}</strong>
            <span>{t('mobile.settings.pref.autosave_desc')}</span>
          </div>
          <Toggle on={auto} onChange={(v) => { setAuto(v); save('autosave', v); }} />
        </div>
      </SetGroup>

      {/* GM 叙事风格 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head"><h2>{t('mobile.settings.pref.gm_style')}</h2></div>
        <button className="pl-row" onClick={() => nav.toast(t('mobile.settings.pref.gm_style_desktop_only'), 'warn', 'sparkle')}>
          <span className="pl-row-ic accent"><Icon name="sparkle" size={18} /></span>
          <span className="pl-row-tx"><strong>{t('mobile.settings.pref.gm_style_custom')}</strong><span>{t('mobile.settings.pref.gm_style_custom_desc')}</span></span>
          <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
        </button>
      </div>

      {/* 黑天鹅事件 */}
      <SetGroup title={t('mobile.settings.pref.black_swan_agent')}>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.pref.black_swan_enable')}</strong>
            <span>{t('mobile.settings.pref.black_swan_enable_desc')}</span>
          </div>
          <Toggle on={blackSwan} onChange={(v) => { setBlackSwan(v); saveBS('enabled', v); }} />
        </div>
      </SetGroup>

      {/* 叙事提取器 */}
      <SetGroup title={t('mobile.settings.pref.extractor')}>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.pref.extractor_model')}</strong>
            <span>{t('mobile.settings.pref.extractor_model_desc')}</span>
          </div>
          <button
            style={{ fontSize: 11.5, color: 'var(--accent)', background: 'none', border: 'none' }}
            onClick={() => nav.go('settings-modules')}
          >
            {t('mobile.settings.pref.extractor_configure')} <Icon name="chevron_right" size={13} />
          </button>
        </div>
      </SetGroup>

      {/* Curator 反问阈值 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head"><h2>{t('mobile.settings.pref.curator_threshold')}</h2></div>
        <div className="pl-card" style={{ border: '1px solid var(--line-soft)', borderRadius: 14, background: 'var(--panel)', padding: 14 }}>
          <MSlider
            label={t('mobile.settings.pref.confidence_threshold')}
            desc={t('mobile.settings.pref.confidence_threshold_desc')}
            value={threshold}
            min={0} max={1} step={0.05}
            onChange={(v) => setThreshold(v)}
          />
          <div style={{ textAlign: 'right', marginTop: 4 }}>
            <button
              className="pl-btn-ghost"
              style={{ height: 36, fontSize: 13, width: 'auto', paddingInline: 16 }}
              onTouchEnd={() => commitThreshold(threshold)}
              onClick={() => commitThreshold(threshold)}
            >
              <Icon name="save" size={14} /> {t('common.save')}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

/* 供应商详情子视图 */
function ProviderDetail({ api, onBack, onSync, onToggleModel, onDeleteKey, nav }) {
  const { t } = useTranslation();
  const [showModels, setShowModels] = useState(true);
  const conn = api.connectivity || {};
  const enabledCount = api.models.filter(m => m.enabled).length;

  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title">
          <strong>{api.name}</strong>
          <span className="sub">{t('mobile.settings.models.provider_byok')}</span>
        </div>
        <div className="pl-head-actions">
          <button className="pl-headbtn" onClick={onSync} title={t('mobile.settings.models.sync_models')}><Icon name="refresh" size={17} /></button>
          <button className="pl-headbtn" onClick={onDeleteKey} title={t('mobile.settings.models.delete_key')} style={{ color: 'var(--danger)' }}><Icon name="trash" size={17} /></button>
        </div>
      </div>
      <div className="pl-body">
        <div className="pl-pad">
          {/* 供应商信息 */}
          <div className="pl-card" style={{ marginBottom: 16 }}>
            <div style={{ display: 'grid', gap: 8, fontSize: 13 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Provider ID</span>
                <span className="mono" style={{ color: 'var(--text-quiet)' }}>{api.id}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>Base URL</span>
                <span className="mono" style={{ color: 'var(--text-quiet)', fontSize: 11, maxWidth: '60%', textAlign: 'right', wordBreak: 'break-all' }}>{api.base_url || '—'}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>API Key</span>
                <span className="mono" style={{ color: 'var(--text-quiet)' }}>•••• {api.key_hint}</span>
              </div>
              {api.enabled===false && (
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--muted)' }}>{t('mobile.settings.models.status')}</span>
                  <span style={{ color: 'var(--muted-2)', fontSize: 12, fontWeight: 600 }}>{t('common.disabled')}</span>
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>{t('mobile.settings.models.connectivity')}</span>
                <span style={{
                  color: conn.status==='ok' ? 'var(--ok)' : conn.status==='err' ? 'var(--danger)' : 'var(--muted)',
                  fontSize: 12
                }}>
                  {conn.status==='ok' ? `✓ ${t('mobile.settings.models.conn_ok')}${conn.latency_ms ? ` · ${conn.latency_ms}ms` : ''}` :
                   conn.status==='err' ? `✗ ${t('common.error')}` :
                   conn.status==='checking' ? t('mobile.settings.models.conn_syncing') : t('mobile.settings.models.conn_untested')}
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--muted)' }}>{t('mobile.settings.models.model_count')}</span>
                <span style={{ color: 'var(--text-quiet)' }}>{t('mobile.settings.models.model_count_value', { enabled: enabledCount, total: api.models.length })}</span>
              </div>
            </div>
          </div>

          {/* 模型列表 */}
          <div className="pl-sec">
            <div className="pl-sec-head">
              <h2>{t('mobile.settings.models.models_heading', { count: api.models.length })}</h2>
              <button className="act" onClick={() => setShowModels(v => !v)}>
                {showModels ? t('mobile.settings.common.collapse') : t('mobile.settings.common.expand')} <Icon name={showModels ? 'chevron_up' : 'chevron_down'} size={13} />
              </button>
            </div>
            {showModels && (
              <div className="pl-group">
                {api.models.length === 0 && (
                  <div style={{ padding: '18px 14px', color: 'var(--muted)', fontSize: 13, textAlign: 'center' }}>
                    {t('mobile.settings.models.no_models_hint')}
                  </div>
                )}
                {api.models.map(m => (
                  <div key={m.id} className="pl-setrow">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 'none' }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                        background: m.health==='ok' ? 'var(--ok)' : m.health==='err' ? 'var(--danger)' : 'var(--muted-3)'
                      }} />
                    </div>
                    <div className="pl-setrow-tx">
                      <strong style={{ fontSize: 13 }}>{m.display}</strong>
                      <span className="mono">{m.real_name}</span>
                    </div>
                    <Toggle on={m.enabled} onChange={() => onToggleModel(m.id)} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 模型参数 (modelparams)                                     */
/* ────────────────────────────────────────────────────────────────── */
const MP_DEFAULTS = {
  temperature: 0.78, top_p: 0.92, top_k: 40,
  repetition_penalty: 1.15, frequency_penalty: 0.20, presence_penalty: 0.10,
  max_tokens: 4096, context_size: 16384, seed: -1,
  mirostat_mode: 'off', mirostat_tau: 5.0, mirostat_eta: 0.10, stop: '',
};
const MP_PRESETS = {
  conservative: { temperature:0.4, top_p:0.85, repetition_penalty:1.05, frequency_penalty:0.1, presence_penalty:0.0 },
  balanced:     { temperature:0.78, top_p:0.92, repetition_penalty:1.15, frequency_penalty:0.2, presence_penalty:0.1 },
  creative:     { temperature:1.0, top_p:0.98, repetition_penalty:1.2, frequency_penalty:0.3, presence_penalty:0.2 },
  deterministic:{ temperature:0.1, top_p:0.5, repetition_penalty:1.0, frequency_penalty:0.0, presence_penalty:0.0 },
};

// readPref / readNumPref 复用 lib/prefs.js 规范实现(语义统一 #24);保留短名薄别名,调用点零变化。
const readPref = readScopedPref;
const readNumPref = readNumberPref;

function ModelParamsSection() {
  const { t } = useTranslation();
  const save = usePrefSave('settings');
  const [preset, setPreset] = useState('balanced');
  const [params, setParams] = useState(MP_DEFAULTS);
  const [nsfw, setNsfw] = useState({ mode:'soft', intensity:0.5, extra_prompt:'' });
  const [effort, setEffort] = useState('medium');
  const [advanced, setAdvanced] = useState(false);
  const [showJson, setShowJson] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const prefs = (r && r.preferences) || {};
        const next = { ...MP_DEFAULTS };
        for (const key of Object.keys(MP_DEFAULTS)) {
          if (typeof MP_DEFAULTS[key] === 'number') next[key] = readNumPref(prefs, key, MP_DEFAULTS[key]);
          else next[key] = String(readPref(prefs, key, MP_DEFAULTS[key]) ?? '');
        }
        const p = String(readPref(prefs, 'preset', 'balanced') || 'balanced');
        if (['balanced','conservative','creative','deterministic','custom'].includes(p)) setPreset(p);
        setParams(next);
        setAdvanced(next.mirostat_mode !== 'off');
        const nsfwMode = String(readPref(prefs,'nsfw_mode', readPref(prefs,'nsfw',{}).mode||'soft') || 'soft');
        const nsfwIntensity = Number(readPref(prefs,'nsfw_intensity', readPref(prefs,'nsfw',{}).intensity ?? 0.5));
        setNsfw({
          mode: ['block','soft','open','explicit'].includes(nsfwMode) ? nsfwMode : 'soft',
          intensity: Number.isFinite(nsfwIntensity) ? nsfwIntensity : 0.5,
          extra_prompt: String(readPref(prefs,'nsfw_extra_prompt','') || ''),
        });
        const eff = String(readPref(prefs,'reasoning_effort','medium') || 'medium');
        if (['low','medium','high'].includes(eff)) setEffort(eff);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const u = (k, v) => { setParams(p => ({ ...p, [k]: v })); save(k, v); };
  const applyPreset = (name) => {
    setPreset(name); save('preset', name);
    const vals = MP_PRESETS[name];
    if (vals) { setParams(p => ({ ...p, ...vals })); Object.entries(vals).forEach(([k,v]) => save(k,v)); }
  };
  const updateNsfw = (patch) => {
    setNsfw(n => ({ ...n, ...patch }));
    if ('mode' in patch) save('nsfw_mode', patch.mode);
    if ('intensity' in patch) save('nsfw_intensity', patch.intensity);
    if ('extra_prompt' in patch) save('nsfw_extra_prompt', patch.extra_prompt);
  };

  return (
    <>
      {/* 预设 */}
      <MField label={t('mobile.settings.modelparams.preset')} desc={t('mobile.settings.modelparams.preset_desc')}>
        <Seg
          options={[['balanced',t('mobile.settings.modelparams.preset_balanced')],['conservative',t('mobile.settings.modelparams.preset_conservative')],['creative',t('mobile.settings.modelparams.preset_creative')],['deterministic',t('mobile.settings.modelparams.preset_deterministic')],['custom',t('mobile.settings.modelparams.preset_custom')]]}
          value={preset}
          onChange={applyPreset}
        />
      </MField>

      <MSlider label="Temperature" desc={t('mobile.settings.modelparams.temperature_desc')}
        value={params.temperature} min={0} max={2} step={0.05}
        onChange={(v) => { setPreset('custom'); u('temperature', v); }} />

      {/* 推理强度 */}
      <MField label={t('mobile.settings.modelparams.reasoning_effort')} desc={t('mobile.settings.modelparams.reasoning_effort_desc')}>
        <Seg
          options={[['low',t('mobile.settings.modelparams.effort_low')],['medium',t('mobile.settings.modelparams.effort_medium')],['high',t('mobile.settings.modelparams.effort_high')]]}
          value={effort}
          onChange={(v) => { setEffort(v); save('reasoning_effort', v); }}
        />
      </MField>

      <MSlider label="Top-p" desc={t('mobile.settings.modelparams.top_p_desc')}
        value={params.top_p} min={0} max={1} step={0.01}
        onChange={(v) => { setPreset('custom'); u('top_p', v); }} />

      <MSlider label="Top-k" desc={t('mobile.settings.modelparams.top_k_desc')}
        value={params.top_k} min={0} max={200} step={1}
        onChange={(v) => { setPreset('custom'); u('top_k', v); }} />

      <MSlider label={t('mobile.settings.modelparams.rep_penalty')} desc={t('mobile.settings.modelparams.rep_penalty_desc')}
        value={params.repetition_penalty} min={1} max={2} step={0.01}
        onChange={(v) => { setPreset('custom'); u('repetition_penalty', v); }} />

      <MSlider label="Frequency Penalty" desc={t('mobile.settings.modelparams.freq_penalty_desc')}
        value={params.frequency_penalty} min={-2} max={2} step={0.05}
        onChange={(v) => { setPreset('custom'); u('frequency_penalty', v); }} />

      <MSlider label="Presence Penalty" desc={t('mobile.settings.modelparams.presence_penalty_desc')}
        value={params.presence_penalty} min={-2} max={2} step={0.05}
        onChange={(v) => { setPreset('custom'); u('presence_penalty', v); }} />

      {/* 数值输入 */}
      <MField label={t('mobile.settings.modelparams.max_tokens')} desc={t('mobile.settings.modelparams.max_tokens_desc')}>
        <input className="pl-input" type="number" value={params.max_tokens}
          onChange={(e) => { setPreset('custom'); u('max_tokens', Number(e.target.value)); }} />
      </MField>

      <MField label={t('mobile.settings.modelparams.context_size')} desc={t('mobile.settings.modelparams.context_size_desc')}>
        <select className="pl-input" value={String(params.context_size)}
          onChange={(e) => u('context_size', Number(e.target.value))}>
          {[['4096','4K'],['8192','8K'],['16384','16K'],['32768','32K'],['65536','64K'],['131072','128K'],['1048576','1M']].map(([v,l]) => (
            <option key={v} value={v}>{l}</option>
          ))}
        </select>
      </MField>

      <MField label={t('mobile.settings.modelparams.seed')} desc={t('mobile.settings.modelparams.seed_desc')}>
        <input className="pl-input" type="number" value={params.seed}
          onChange={(e) => u('seed', Number(e.target.value))} placeholder="-1" />
      </MField>

      <MField label={t('mobile.settings.modelparams.stop')} desc={t('mobile.settings.modelparams.stop_desc')}>
        <input className="pl-input" value={params.stop}
          onChange={(e) => u('stop', e.target.value)} placeholder="player:|system:" />
      </MField>

      {/* NSFW */}
      <MField label={t('mobile.settings.modelparams.content_filter')}>
        <Seg
          options={[['block',t('mobile.settings.modelparams.nsfw_block')],['soft',t('mobile.settings.modelparams.nsfw_soft')],['open',t('mobile.settings.modelparams.nsfw_open')],['explicit',t('mobile.settings.modelparams.nsfw_explicit')]]}
          value={nsfw.mode}
          onChange={(v) => updateNsfw({ mode: v })}
        />
      </MField>

      {nsfw.mode !== 'block' && (
        <MSlider label={t('mobile.settings.modelparams.nsfw_intensity')} desc={t('mobile.settings.modelparams.nsfw_intensity_desc')}
          value={nsfw.intensity} min={0} max={1} step={0.05}
          onChange={(v) => updateNsfw({ intensity: v })} />
      )}

      <MField label={t('mobile.settings.modelparams.nsfw_extra_prompt')} desc={t('mobile.settings.modelparams.nsfw_extra_prompt_desc')}>
        <input className="pl-input" value={nsfw.extra_prompt}
          onChange={(e) => updateNsfw({ extra_prompt: e.target.value })}
          placeholder="All characters must be 18+" />
      </MField>

      {/* Mirostat */}
      <div className="pl-setrow">
        <div className="pl-setrow-tx"><strong>{t('mobile.settings.modelparams.mirostat')}</strong><span>{t('mobile.settings.modelparams.mirostat_desc')}</span></div>
        <Toggle on={advanced} onChange={setAdvanced} />
      </div>
      {advanced && (
        <>
          <MField label={t('mobile.settings.modelparams.mirostat_mode')}>
            <Seg options={[['off',t('mobile.settings.modelparams.mirostat_off')],['v1','v1'],['v2','v2']]} value={params.mirostat_mode}
              onChange={(v) => u('mirostat_mode', v)} />
          </MField>
          <MSlider label="Mirostat τ (tau)" desc={t('mobile.settings.modelparams.mirostat_tau_desc')}
            value={params.mirostat_tau} min={0} max={10} step={0.1}
            onChange={(v) => u('mirostat_tau', v)} />
          <MSlider label="Mirostat η (eta)" desc={t('mobile.settings.modelparams.mirostat_eta_desc')}
            value={params.mirostat_eta} min={0} max={1} step={0.01}
            onChange={(v) => u('mirostat_eta', v)} />
        </>
      )}

      {/* JSON 预览 */}
      <div style={{ marginTop: 8 }}>
        <button className="pl-btn-ghost" style={{ height: 38, fontSize: 13 }} onClick={() => setShowJson(v => !v)}>
          <Icon name={showJson ? 'chevron_up' : 'chevron_down'} size={14} /> {showJson ? t('mobile.settings.common.collapse') : t('mobile.settings.modelparams.view_json')}
        </button>
        {showJson && (
          <pre className="quote mono" style={{ fontSize: 11, marginTop: 8, overflowX: 'auto' }}>
            {JSON.stringify({
              temperature: params.temperature, top_p: params.top_p, top_k: params.top_k,
              repetition_penalty: params.repetition_penalty, frequency_penalty: params.frequency_penalty,
              presence_penalty: params.presence_penalty, max_tokens: params.max_tokens,
              context_size: params.context_size, seed: params.seed,
              stop: params.stop.split('|').filter(Boolean),
              nsfw: nsfw.mode==='block' ? null : { mode:nsfw.mode, intensity:nsfw.intensity, extra:nsfw.extra_prompt },
              ...(advanced ? { mirostat_mode:params.mirostat_mode, mirostat_tau:params.mirostat_tau, mirostat_eta:params.mirostat_eta } : {}),
            }, null, 2)}
          </pre>
        )}
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 模块分配 (modules)                                         */
/* ────────────────────────────────────────────────────────────────── */
// 模块清单与桌面端同构。每行用统一规范组件 AgentModelPicker(不再自造 <select>)。
//   flat 模块走 prefPrefix(<prefPrefix>.api_id / .model_real_name);
//   dict 模块(sub_agent / console)走 persistShape="dict" + dictKey={api_id, model};
//   embedder / image_gen allowInherit=false(必须自己选);其它可「跟随主 GM」。
// 结构字段走单一来源 AGENT_MODULES;移动端 label/tip 文案精简(与桌面端不同),保留本地按 id 取。
const MODULES = AGENT_MODULES.map((m) => ({ ...m }));

/* FeatureToggleM — 移动端引擎特性开关(每用户、默认开)。写 user_preferences["<key>.enabled"],
   与桌面端、后端 core.feature_flags 同键。initial 由父组件一次性 profile 下发。 */
const _FEAT_LABEL_DEF = {
  ctx_tiered: '分层上下文缓存', recorder_unified: '史官三合一', narrator_slim: '文宗精简(去工具循环)',
  rag_gate: 'RAG 检索闸', anchor_pace: '世界线锚点节奏', kb_state: '存档知识库 DB 化',
  consequence_ledger: '后果账本', world_heartbeat: '世界心跳', channel_fallback: '跨渠道自动备援',
  npc_agenda: 'NPC 议程',
};
const _FEAT_DESC_DEF = {
  ctx_tiered: '分层稳定前缀,命中前缀缓存,显著省 token。',
  recorder_unified: '状态提取 + 锚点判定合并为一次 LLM 调用。',
  narrator_slim: '主叙事单次成文、不带工具循环,状态交史官。',
  rag_gate: '司命判定本回合是否需检索,不需则跳过省 token。',
  anchor_pace: '按对话节奏推进锚点、逐个标记、死亡失效 —— 治跳章。',
  kb_state: '存档状态以数据库行存储(单一来源),便于检索维护。',
  consequence_ledger: '承诺/欠债到期或抵达指定地点时,提醒 GM 主动兑现。',
  world_heartbeat: '每隔几回合生成不在场处的世界小事,之后以传闻方式浮现。',
  channel_fallback: '主渠道持续故障时自动切换你配置的其他渠道(明确提示)。',
  npc_agenda: 'NPC 带持续演化的议程(想要什么/对玩家什么态度),跨回合连续。',
};
function FeatureToggleM({ featureKey, i18nKey, initial }) {
  const { t } = useTranslation();
  const [on, setOn] = useState(initial !== false);
  const save = usePrefSave(featureKey);
  useEffect(() => { setOn(initial !== false); }, [initial]);
  return (
    <div className="pl-card" style={{ marginBottom: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
      <div style={{ minWidth: 0 }}>
        <strong style={{ fontSize: 14 }}>{t(`settings.features.${i18nKey}.label`, { defaultValue: _FEAT_LABEL_DEF[featureKey] || featureKey })}</strong>
        <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2, lineHeight: 1.5 }}>{t(`settings.features.${i18nKey}.desc`, { defaultValue: _FEAT_DESC_DEF[featureKey] || '' })}</div>
      </div>
      <Toggle on={on} onChange={(v) => { setOn(v); save('enabled', v); }} />
    </div>
  );
}

function ModuleModelsSection({ nav }) {
  const { t } = useTranslation();
  // embedder 平台兜底状态:仅 admin/vip 显示平台 vertex embedding(后端已 _is_admin gate)。
  const [embedStatus, setEmbedStatus] = useState(null);
  useEffect(() => {
    fetch('/api/me/embedder/status', { credentials:'include' })
      .then(r => r.json()).then(es => setEmbedStatus(es?.ok ? es : null)).catch(() => {});
  }, []);
  const platformVertexAllowed = !!(embedStatus && embedStatus.platform_fallback_available);

  // 一次性读取特性偏好(各开关初值)。
  const [featPrefs, setFeatPrefs] = useState({});
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try { const r = await window.api.account.profile(); if (!cancelled && r && r.preferences) setFeatPrefs(r.preferences); } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <>
      <div className="pl-sec-note" style={{ marginBottom: 14 }}>
        {t('mobile.settings.modules.intro')}
      </div>
      {MODULE_GROUPS.map(grp => {
        const items = MODULES.filter(m => (m.group || 'misc') === grp.id);
        const feats = (AGENT_FEATURES || []).filter(f => f.group === grp.id);
        if (!items.length && !feats.length) return null;
        const groupLabel = t(`mobile.settings.modules.group.${grp.id}`, {
          defaultValue: { core: '对话核心（三贤者）', script: '剧本与角色卡', world: '世界模拟', gen: '检索与生成', misc: '通用兜底' }[grp.id] || grp.id,
        });
        return (
        <React.Fragment key={grp.id}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent, #0972d3)', margin: '14px 0 6px' }}>{groupLabel}</div>
          {feats.map(f => (
            <FeatureToggleM key={f.key} featureKey={f.key} i18nKey={f.i18nKey} initial={featPrefs[`${f.key}.enabled`]} />
          ))}
          {items.map(mod => (
        <div key={mod.id} className="pl-card" style={{ marginBottom: 10 }}>
          <div style={{ marginBottom: 10 }}>
            <strong style={{ fontSize: 14 }}>{t(`mobile.settings.modules.label.${mod.id}`)}</strong>
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{t(`mobile.settings.modules.tip.${mod.id}`)}</div>
          </div>
          <AgentModelPicker
            prefPrefix={mod.prefPrefix}
            persistShape={mod.persistShape || 'flat'}
            dictKey={mod.dictKey || null}
            capabilityFilter={mod.capabilityFilter || null}
            allowInherit={!!mod.inherit}
            defaultModel={mod.defaultModel || null}
            preferProvider={mod.preferProvider || null}
            fallbackPrefix={mod.fallbackPrefix || null}
            platformVertexAllowed={mod.id === 'embedder' ? platformVertexAllowed : false}
            variant="bare"
            configHash="apis"
          />
          {mod.id==='embedder' && embedStatus && !embedStatus.user_configured && !platformVertexAllowed && (
            <div style={{ fontSize: 11, color: 'var(--warn)', marginTop: 8, lineHeight: 1.5 }}>
              {t('mobile.settings.modules.embedder_no_key')}
            </div>
          )}
        </div>
          ))}
        </React.Fragment>
        );
      })}
      <div style={{ fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.6, marginTop: 8 }}>
        {t('mobile.settings.modules.embedder_switch_note')}
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 记忆 (memory)                                              */
/* ────────────────────────────────────────────────────────────────── */
function MemorySection() {
  const { t } = useTranslation();
  const save = usePrefSave('memory');
  const [recallDepth, setRecallDepth] = useState(6);
  const [summaryWindow, setSummaryWindow] = useState(8);
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

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 权限 (permissions)                                         */
/* ────────────────────────────────────────────────────────────────── */
const HIGH_RISK_ALL = ['timeline.pending_jump','player.background','world.constraints','relationships.*.tone'];
const CUSTOM_WL_RE = /^[a-zA-Z_][a-zA-Z0-9_.*]*$/;

function PermissionsSection({ nav }) {
  const { t } = useTranslation();
  const save = usePrefSave('perm');
  const [mode, setMode] = useState('review');
  const [whitelist, setWhitelist] = useState(['timeline.pending_jump','player.background','world.constraints']);
  const [custom, setCustom] = useState([]);
  const [customInput, setCustomInput] = useState('');
  const [customErr, setCustomErr] = useState('');
  // 审计日志
  const [auditEntries, setAuditEntries] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditErr, setAuditErr] = useState('');
  const [auditFilter, setAuditFilter] = useState('all');
  const [showAudit, setShowAudit] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await window.api.account.profile();
        if (cancelled) return;
        const p = (r && r.preferences) || {};
        const v = p['perm.default_mode'] || p.default_perm_mode;
        if (v) setMode(v);
        const wl = p['perm.high_risk_whitelist'];
        if (Array.isArray(wl)) setWhitelist(wl);
        const cwl = p['permissions.custom_whitelist'];
        if (Array.isArray(cwl)) setCustom(cwl);
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  const toggleWhitelist = (field) => {
    const next = whitelist.includes(field) ? whitelist.filter(f => f!==field) : [...whitelist, field];
    setWhitelist(next); save('high_risk_whitelist', next);
  };

  const saveCustom = async (next) => {
    setCustom(next);
    try { await window.api.account.preferences({ 'permissions.custom_whitelist': next }); } catch (_) {}
    lsSetJSON('perm.custom_whitelist', next);
  };

  const addCustom = () => {
    const val = customInput.trim();
    if (!val) { setCustomErr(t('mobile.settings.perm.custom_err_empty')); return; }
    if (val.length > 80) { setCustomErr(t('mobile.settings.perm.custom_err_too_long')); return; }
    if (!CUSTOM_WL_RE.test(val)) { setCustomErr(t('mobile.settings.perm.custom_err_format')); return; }
    if (HIGH_RISK_ALL.includes(val)) { setCustomErr(t('mobile.settings.perm.custom_err_builtin')); return; }
    if (custom.includes(val)) { setCustomErr(t('mobile.settings.perm.custom_err_exists')); return; }
    saveCustom([...custom, val]);
    setCustomInput(''); setCustomErr('');
  };

  const loadAudit = useCallback(async () => {
    setAuditLoading(true); setAuditErr('');
    try {
      const s = await window.api.game.state();
      const perms = (s && (s.permissions || s.state?.permissions)) || {};
      const log = Array.isArray(perms.audit_log) ? perms.audit_log : [];
      setAuditEntries(log.slice().reverse());
    } catch (e) { setAuditErr(e?.message || t('mobile.settings.perm.load_failed')); }
    finally { setAuditLoading(false); }
  }, []);

  const KIND_META = {
    write:            { label: t('mobile.settings.perm.kind_write'), color:'var(--ok)' },
    parse_error:      { label: t('mobile.settings.perm.kind_parse_error'), color:'var(--warn)' },
    rejected:         { label: t('mobile.settings.perm.kind_rejected'), color:'var(--danger)' },
    hard_forbidden:   { label: t('mobile.settings.perm.kind_hard_forbidden'), color:'var(--danger)' },
    extractor_error:  { label: t('mobile.settings.perm.kind_extractor_error'), color:'var(--warn)' },
    set_parser_error: { label: t('mobile.settings.perm.kind_set_parser_error'), color:'var(--warn)' },
    clarify_yield:    { label: t('mobile.settings.perm.kind_clarify_yield'), color:'var(--ok)' },
    acceptance_unmet: { label: t('mobile.settings.perm.kind_acceptance_unmet'), color:'var(--warn)' },
    question_skip:    { label: t('mobile.settings.perm.kind_question_skip'), color:'var(--muted)' },
  };
  const filteredAudit = auditFilter==='all' ? auditEntries : auditEntries.filter(e => e.kind===auditFilter);

  return (
    <>
      {/* 默认权限模式 */}
      <SetGroup title={t('mobile.settings.perm.gm_write_perm')}>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.perm.default_mode')}</strong>
            <span>{t('mobile.settings.perm.default_mode_desc')}</span>
          </div>
        </div>
        <div style={{ padding: '8px 13px 13px' }}>
          <div className="pl-seg2">
            {[['default',t('mobile.settings.perm.mode_default')],['review',t('mobile.settings.perm.mode_review')],['full_access',t('mobile.settings.perm.mode_full_access')]].map(([id, l]) => (
              <button key={id} className={mode===id?'active accent':''} onClick={() => { setMode(id); save('default_mode',id); }}>{l}</button>
            ))}
          </div>
          <div style={{ fontSize: 11, color: 'var(--muted-2)', marginTop: 8, lineHeight: 1.5 }}>
            {mode==='review' ? t('mobile.settings.perm.mode_review_hint') : mode==='full_access' ? t('mobile.settings.perm.mode_full_access_hint') : t('mobile.settings.perm.mode_default_hint')}
          </div>
        </div>

        {/* 高风险字段白名单 */}
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 10 }}>
          <div>
            <strong>{t('mobile.settings.perm.high_risk_whitelist')}</strong>
            <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 2 }}>{t('mobile.settings.perm.high_risk_whitelist_desc')}</div>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 7 }}>
            {HIGH_RISK_ALL.map(field => (
              <button
                key={field}
                className={whitelist.includes(field) ? 'pill accent' : 'pill'}
                onClick={() => toggleWhitelist(field)}
                style={{ cursor: 'pointer', fontSize: 11, height: 28, transition: 'all .15s' }}
              >
                {field}
              </button>
            ))}
          </div>
        </div>

        {/* 自定义白名单 */}
        <div className="pl-setrow" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 10 }}>
          <div>
            <strong>{t('mobile.settings.perm.custom_whitelist')}</strong>
            <div style={{ fontSize: 11.5, color: 'var(--muted-2)', marginTop: 2 }}>
              {t('mobile.settings.perm.custom_whitelist_format_prefix')} <span className="mono">player.hp</span> {t('mobile.settings.perm.custom_whitelist_format_or')} <span className="mono">world.*</span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, width: '100%' }}>
            <input
              className="pl-input"
              style={{ flex: 1, height: 40, fontSize: 13 }}
              value={customInput}
              placeholder="player.custom_field"
              onChange={(e) => { setCustomInput(e.target.value); if (customErr) setCustomErr(''); }}
              onKeyDown={(e) => { if (e.key==='Enter') { e.preventDefault(); addCustom(); } }}
            />
            <button className="pl-btn-primary" style={{ height: 40, width: 64, fontSize: 13, flexShrink: 0 }} onClick={addCustom}>
              <Icon name="plus" size={14} />
            </button>
          </div>
          {customErr && <div style={{ fontSize: 12, color: 'var(--danger)', marginTop: -4 }}>{customErr}</div>}
          {custom.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {custom.map(entry => (
                <div key={entry} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '4px 10px', borderRadius: 6, border: '1px solid var(--line-soft)',
                  background: 'var(--panel-2)', fontSize: 12.5, fontFamily: 'var(--font-mono)',
                }}>
                  {entry}
                  <button
                    onClick={() => saveCustom(custom.filter(e => e!==entry))}
                    style={{ color: 'var(--danger)', fontSize: 14, lineHeight: 1, padding: 0 }}
                  >×</button>
                </div>
              ))}
            </div>
          )}
          {custom.length===0 && <span style={{ fontSize: 12, color: 'var(--muted)' }}>{t('mobile.settings.perm.no_custom_entries')}</span>}
        </div>
      </SetGroup>

      {/* 审计日志 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head">
          <h2>{t('mobile.settings.perm.audit_log')}</h2>
          <button className="act" onClick={() => { if (!showAudit) loadAudit(); setShowAudit(v => !v); }}>
            {showAudit ? t('mobile.settings.common.collapse') : t('mobile.settings.common.expand')} <Icon name={showAudit ? 'chevron_up' : 'chevron_down'} size={13} />
          </button>
        </div>
        {showAudit && (
          <div style={{ fontSize: 11, color: 'var(--muted-2)', padding: '0 0 8px', lineHeight: 1.5 }}>
            {t('mobile.settings.perm.audit_scope_note', '仅显示最近活动会话的操作记录，无活动游戏时可能为空。')}
          </div>
        )}
        {showAudit && (
          <div className="pl-card" style={{ padding: 12 }}>
            <div style={{ display: 'flex', gap: 8, marginBottom: 10, alignItems: 'center' }}>
              <button className="pl-btn-ghost" style={{ height: 36, fontSize: 12, flex: 1 }}
                disabled={auditLoading} onClick={loadAudit}>
                <Icon name="refresh" size={13} /> {auditLoading ? t('common.loading') : t('mobile.settings.perm.refresh_log')}
              </button>
            </div>
            {auditErr && <div style={{ fontSize: 12, color: 'var(--danger)', marginBottom: 8 }}>{auditErr}</div>}

            {/* 类型筛选 */}
            {auditEntries.length > 0 && (
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 10 }}>
                {['all', ...Object.keys(KIND_META)].map(k => {
                  const count = k==='all' ? auditEntries.length : auditEntries.filter(e => e.kind===k).length;
                  if (k!=='all' && count===0) return null;
                  return (
                    <button key={k} onClick={() => setAuditFilter(k)}
                      className={auditFilter===k ? 'pill accent' : 'pill'}
                      style={{ cursor: 'pointer', fontSize: 10.5, height: 26, transition: 'all .15s' }}>
                      {k==='all' ? t('common.all') : (KIND_META[k]?.label || k)} · {count}
                    </button>
                  );
                })}
              </div>
            )}

            {auditEntries.length===0 && !auditLoading && (
              <div style={{ fontSize: 12.5, color: 'var(--muted)', textAlign: 'center', padding: '16px 0' }}>
                {t('mobile.settings.perm.no_audit_log')}
              </div>
            )}

            {filteredAudit.slice(0, 30).map((e, i) => {
              const meta = KIND_META[e.kind] || { label: e.kind, color: 'var(--muted)' };
              const detail = e.path
                ? `${e.path} = ${typeof e.value==='string' ? e.value : JSON.stringify(e.value)}`
                : (e.raw_spec || e.hint || '—');
              return (
                <div key={i} style={{
                  padding: '8px 0', borderBottom: '1px solid var(--line-soft)',
                  fontSize: 11.5, display: 'grid', gap: 3,
                }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', padding: '2px 7px',
                      borderRadius: 5, border: `1px solid ${meta.color}`, color: meta.color,
                      fontSize: 10.5, flexShrink: 0,
                    }}>{meta.label}</span>
                    <span className="mono" style={{ color: 'var(--muted-2)', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {(e.ts||'').replace('T',' ').slice(0, 16)}
                    </span>
                    {e.source && <span style={{ color: 'var(--muted-2)', fontSize: 10 }}>{e.source}</span>}
                  </div>
                  <div style={{ color: 'var(--text-quiet)', lineHeight: 1.4, wordBreak: 'break-word' }}>{detail}</div>
                  {e.hint && e.path && <div style={{ color: 'var(--muted-2)', fontSize: 10.5 }}>· {e.hint}</div>}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 账户 (account)                                             */
/* ────────────────────────────────────────────────────────────────── */
function AccountSection({ nav }) {
  const { t } = useTranslation();
  const user = useReactiveUser();
  const isCoBuilder = user?.is_co_builder === true;
  const [cbChecked, setCbChecked] = useState(() => !user?.co_builder_opt_out);
  const [cbSaving, setCbSaving] = useState(false);

  useEffect(() => { setCbChecked(!user?.co_builder_opt_out); }, [user?.co_builder_opt_out]);

  const handleCoBuilder = async (v) => {
    setCbChecked(v); setCbSaving(true);
    try {
      await fetch('/api/me/profile', {
        method: 'PATCH', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ co_builder_opt_out: !v }),
      });
      nav.toast(t('mobile.settings.account.co_builder_saved'), 'ok', 'check');
    } catch (e) {
      nav.toast(t('mobile.settings.account.save_failed'), 'danger', 'warn');
      setCbChecked(!v);
    } finally { setCbSaving(false); }
  };

  // API 用量(30天)
  const [usage, setUsage] = useState(null);
  useEffect(() => {
    window.api?.account?.usage?.(30).then(setUsage).catch(() => {});
  }, []);

  // 数据迁移
  const [est, setEst] = useState(null);
  const [includeChunks, setIncludeChunks] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [importFile, setImportFile] = useState(null);
  const [importing, setImporting] = useState(false);
  const [importJob, setImportJob] = useState(null);
  const [importResult, setImportResult] = useState(null);
  const esRef = useRef(null);
  const pollRef = useRef(null);
  const fileRef = useRef(null);

  useEffect(() => {
    window.api?.account?.migrateEstimate?.().then(setEst).catch(() => {});
  }, []);

  const doExport = () => {
    setExporting(true);
    try {
      const url = window.api.account.migrateExportUrl(includeChunks);
      const a = document.createElement('a'); a.href=url; a.rel='noopener';
      document.body.appendChild(a); a.click(); a.remove();
      nav.toast(t('mobile.settings.account.export_started'), 'ok', 'download');
    } catch (e) { nav.toast(t('mobile.settings.account.export_failed', { msg: e?.message||'' }), 'danger', 'warn'); }
    finally { setTimeout(() => setExporting(false), 800); }
  };

  const finishJob = async (jobId) => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current=null; }
    if (esRef.current) { try { esRef.current.close?.(); } catch {} esRef.current=null; }
    try {
      const s = await window.api.scripts.jobStatus(jobId);
      const job = s?.job || {};
      const summary = (job.usage_actual && job.usage_actual.summary) || {};
      setImportResult({ scripts: summary.scripts??0, saves: summary.saves??0, cards: summary.cards??0, warnings: job.warnings||[] });
      nav.toast(t('mobile.settings.account.import_done_toast', { scripts: summary.scripts??0, saves: summary.saves??0, cards: summary.cards??0 }), 'ok', 'check');
    } catch (e) { nav.toast(t('mobile.settings.account.import_failed', { msg: e?.message||'' }), 'danger', 'warn'); }
    finally { setImportJob(null); setImporting(false); }
  };

  const doImport = async () => {
    if (!importFile) return;
    setImporting(true); setImportResult(null); setImportJob({ stage:'scripts', stage_progress:0, stage_total:0 });
    let jobId=null;
    try {
      const r = await window.api.account.migrateImport(importFile);
      jobId = r?.job_id;
      if (!jobId) throw new Error(r?.error || t('mobile.settings.account.import_no_job_id'));
    } catch (e) {
      setImporting(false); setImportJob(null);
      nav.toast(t('mobile.settings.account.import_failed', { msg: e?.payload?.error || e?.message || '' }), 'danger', 'warn');
      return;
    }
    const isTerminal = (st) => ['done','done_with_errors','failed','cancelled'].includes(st);
    esRef.current = window.api.scripts.streamImport(jobId, {
      on_update: (jb) => { setImportJob(jb); if (isTerminal(jb.status)) finishJob(jobId); },
      on_done: () => finishJob(jobId),
      on_error: () => {
        if (pollRef.current) return;
        pollRef.current = setInterval(async () => {
          try {
            const s = await window.api.scripts.jobStatus(jobId);
            const j = s?.job; if (!j) return;
            setImportJob(j);
            if (isTerminal(j.status)) finishJob(jobId);
          } catch {}
        }, 2000);
      },
    });
  };

  useEffect(() => () => {
    if (esRef.current) { try { esRef.current.close?.(); } catch {} }
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  // 在线剧本库联邦
  const [conn, setConn] = useState(null);
  const DEFAULT_ONLINE_BASE = 'https://rpg-roleplay.stellatrix.icu';
  const reloadConn = useCallback(async () => {
    try { setConn(await window.api.federation.connectorGet()); }
    catch { setConn({ connected: false, base_url: DEFAULT_ONLINE_BASE }); }
  }, []);
  useEffect(() => { reloadConn(); }, [reloadConn]);

  const [connBase, setConnBase] = useState(DEFAULT_ONLINE_BASE);
  const [connToken, setConnToken] = useState('');
  const [connBusy, setConnBusy] = useState(false);

  useEffect(() => { if (conn?.base_url) setConnBase(conn.base_url); }, [conn?.base_url]);

  const savePat = async () => {
    if (!connToken.trim()) { nav.toast(t('mobile.settings.account.pat_empty'), 'warn', 'key'); return; }
    setConnBusy(true);
    try {
      await window.api.federation.connectorSet(connBase.trim(), connToken.trim());
      nav.toast(t('mobile.settings.account.federation_connected'), 'ok', 'check');
      setConnToken(''); reloadConn();
    } catch (e) { nav.toast(t('mobile.settings.account.federation_connect_failed', { msg: e?.payload?.error||e?.message||'' }), 'danger', 'warn'); }
    finally { setConnBusy(false); }
  };

  const disconnect = async () => {
    setConnBusy(true);
    try { await window.api.federation.connectorSet(connBase.trim(), ''); nav.toast(t('mobile.settings.account.federation_disconnected'), 'ok', 'unlock'); reloadConn(); }
    catch (e) { nav.toast(t('mobile.settings.account.operation_failed'), 'danger', 'warn'); }
    finally { setConnBusy(false); }
  };

  const initial = (user?.display_name || '?').slice(0, 1);

  return (
    <>
      {/* 用户信息卡 */}
      <div style={{ display:'flex', gap:14, alignItems:'center', marginBottom:18 }}>
        <div style={{ width:60, height:60, borderRadius:18, background:'var(--accent)', color:'#fff8f3',
          display:'grid', placeItems:'center', font:'600 24px var(--font-serif)', flexShrink:0 }}>
          {initial}
        </div>
        <div>
          <div style={{ fontSize:17, fontFamily:'var(--font-serif)', color:'var(--text)' }}>
            {user?.display_name || '—'}
          </div>
          <div style={{ fontSize:12, color:'var(--muted)' }}>
            @{user?.username || '—'} · {user?.role || 'user'}
          </div>
        </div>
      </div>

      {/* 30 天用量 */}
      {usage && (
        <div className="pl-sec" style={{ marginBottom: 18 }}>
          <div className="pl-sec-head"><h2>{t('mobile.settings.account.api_usage')}</h2></div>
          <div className="pl-stats">
            <div className="pl-stat">
              <span className="n accent">{(usage.total_tokens||0).toLocaleString()}</span>
              <div className="l">{t('mobile.settings.account.total_tokens')}</div>
            </div>
            <div className="pl-stat">
              <span className="n">{(usage.total_calls||0).toLocaleString()}</span>
              <div className="l">{t('mobile.settings.account.total_calls')}</div>
            </div>
            <div className="pl-stat">
              <span className="n">{(usage.cache_hit_rate != null ? `${Math.round(usage.cache_hit_rate*100)}%` : '—')}</span>
              <div className="l">{t('mobile.settings.account.cache_hit')}</div>
            </div>
          </div>
        </div>
      )}

      {/* Co-Builder 计划 */}
      <SetGroup title={t('mobile.settings.account.account_settings')}>
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.account.co_builder_title')}</strong>
            <span>{isCoBuilder ? t('mobile.settings.account.co_builder_active') : t('mobile.settings.account.co_builder_inactive')}</span>
          </div>
          {isCoBuilder ? (
            <Toggle on={cbChecked} onChange={handleCoBuilder} />
          ) : (
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>{t('mobile.settings.account.co_builder_unavailable')}</span>
          )}
        </div>
      </SetGroup>

      {/* 数据迁移 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head"><h2>{t('mobile.settings.account.data_migration')}</h2></div>
        <div className="pl-card" style={{ display: 'grid', gap: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.6 }}>
            {t('mobile.settings.account.data_migration_desc')}
            {est && (
              <div style={{ marginTop: 6, color: 'var(--text-quiet)' }}>
                {t('mobile.settings.account.data_migration_est', { scripts: est.scripts??0, saves: est.saves??0, cards: est.cards??0, model_entries: est.model_entries??0 })}
              </div>
            )}
          </div>

          {/* 包含切片 */}
          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            <Toggle on={includeChunks} onChange={setIncludeChunks} />
            <span style={{ fontSize: 12.5, color: 'var(--text-quiet)' }}>{t('mobile.settings.account.include_chunks')}</span>
          </div>

          <button className="pl-btn-primary" disabled={exporting} onClick={doExport}>
            <Icon name="download" size={16} /> {exporting ? t('mobile.settings.account.export_preparing') : t('mobile.settings.account.export_btn')}
          </button>

          {/* 导入 */}
          <div>
            <label style={{ fontSize: 12.5, color: 'var(--text-quiet)', display: 'block', marginBottom: 8 }}>
              {t('mobile.settings.account.import_label')}
            </label>
            <input
              ref={fileRef} type="file" accept=".zip,application/zip"
              onChange={(e) => { setImportFile(e.target.files?.[0]||null); setImportResult(null); }}
              style={{ fontSize: 13, color: 'var(--text-quiet)', marginBottom: 8 }}
            />
            <button className="pl-btn-ghost" disabled={!importFile||importing} onClick={doImport}>
              <Icon name="upload" size={14} /> {importing ? t('mobile.settings.account.importing') : t('mobile.settings.account.import_btn')}
            </button>
          </div>

          {/* 导入进度 */}
          {importJob && (
            <div>
              <div style={{ fontSize: 12.5, color: 'var(--text-quiet)', marginBottom: 6 }}>
                {{ scripts: t('mobile.settings.account.stage_scripts'), saves: t('mobile.settings.account.stage_saves'), cards: t('mobile.settings.account.stage_cards'), done: t('mobile.settings.account.stage_done') }[importJob.stage] || importJob.stage || t('mobile.settings.account.stage_processing')}
                {importJob.stage_total ? ` ${importJob.stage_progress||0}/${importJob.stage_total}` : '...'}
              </div>
              <div style={{ height:5, background:'var(--panel-3)', borderRadius:3, overflow:'hidden' }}>
                <div style={{ height:'100%', background:'var(--accent)', borderRadius:3,
                  width: `${importJob.stage_total ? Math.round(100*(importJob.stage_progress||0)/importJob.stage_total) : 30}%`,
                  transition:'width .3s' }} />
              </div>
            </div>
          )}

          {/* 导入结果 */}
          {importResult && (
            <div style={{ padding:12, borderRadius:10, border:'1px solid var(--line)',
              background: importResult.warnings?.length ? 'var(--warn-soft)' : 'var(--ok-soft)',
              fontSize: 12.5, color: 'var(--text-quiet)' }}>
              <div style={{ fontWeight:600, marginBottom:4 }}>{t('mobile.settings.account.import_done')}</div>
              <div>{t('mobile.settings.account.import_result', { scripts: importResult.scripts, saves: importResult.saves, cards: importResult.cards })}</div>
              {importResult.warnings?.length > 0 && (
                <ul style={{ margin:'6px 0 0', paddingLeft:16, fontSize:11.5 }}>
                  {importResult.warnings.slice(0,10).map((w,i) => <li key={i}>{w}</li>)}
                  {importResult.warnings.length>10 && <li>{t('mobile.settings.account.import_more_warnings', { n: importResult.warnings.length-10 })}</li>}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>

      {/* 在线剧本库联邦 */}
      <div className="pl-sec" style={{ marginTop: 18 }}>
        <div className="pl-sec-head"><h2>{t('mobile.settings.account.online_library')}</h2></div>
        <div className="pl-card" style={{ display: 'grid', gap: 14 }}>
          {conn?.connected ? (
            <>
              <div style={{ fontSize: 13, color: 'var(--ok)' }}>
                ✓ {t('mobile.settings.account.connected_to', { url: conn.base_url })}
              </div>
              <button className="pl-btn-ghost" disabled={connBusy} onClick={disconnect}>
                <Icon name="unlock" size={14} /> {t('mobile.settings.account.disconnect')}
              </button>
            </>
          ) : (
            <>
              <div style={{ fontSize: 12, color: 'var(--muted)', lineHeight: 1.5 }}>
                {t('mobile.settings.account.online_library_desc')}
              </div>
              <MField label={t('mobile.settings.account.service_url')}>
                <input className="pl-input" value={connBase}
                  onChange={(e) => setConnBase(e.target.value)}
                  placeholder={DEFAULT_ONLINE_BASE} />
              </MField>
              <MField label={t('mobile.settings.account.pat_label')} desc={t('mobile.settings.account.pat_desc')}>
                <input className="pl-input" type="password" value={connToken}
                  onChange={(e) => setConnToken(e.target.value)}
                  placeholder="rpgpat_…" />
              </MField>
              <button className="pl-btn-primary" disabled={connBusy} onClick={savePat}>
                <Icon name="link" size={15} /> {connBusy ? t('mobile.settings.account.connecting') : t('mobile.settings.account.save_and_connect')}
              </button>
            </>
          )}
        </div>
      </div>
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* SECTION: 危险区 (danger)                                            */
/* ────────────────────────────────────────────────────────────────── */
function DangerSection({ nav }) {
  const { t } = useTranslation();
  const { saves = [] } = usePlatformData();
  const gameSaves = saves.filter(s => s.save_kind !== 'tavern');
  const nSaves = gameSaves.length;
  const [showClearSheet, setShowClearSheet] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [clearProgress, setClearProgress] = useState(null);

  const openClear = () => { setConfirmText(''); setShowClearSheet(true); };
  const closeClear = () => { setShowClearSheet(false); setConfirmText(''); };

  const doDelete = async () => {
    if (nSaves === 0) { nav.toast(t('mobile.settings.danger.no_saves'), 'ok', 'info'); closeClear(); return; }
    setClearProgress({ done:0, total:nSaves });
    let done=0, fail=0;
    for (const s of gameSaves) {
      try { await window.api.saves.remove(s.id); } catch (_) { fail++; }
      done++;
      setClearProgress({ done, total:nSaves });
    }
    setClearProgress(null);
    closeClear();
    nav.toast(fail ? t('mobile.settings.danger.clear_partial', { done: done-fail, fail }) : t('mobile.settings.danger.clear_done', { done }), fail ? 'warn' : 'ok', 'trash');
    try { window.dispatchEvent(new CustomEvent('rpg-saves-updated')); } catch (_) {}
  };

  return (
    <>
      <div style={{ padding:'11px 13px', borderRadius:10, background:'var(--danger-soft)', border:'1px solid rgba(200,103,93,0.3)', fontSize:12.5, color:'var(--danger)', lineHeight:1.6, marginBottom:16 }}>
        {t('mobile.settings.danger.irreversible_warning')}
      </div>

      <SetGroup title={t('mobile.settings.danger.dangerous_ops')}>
        {/* 清空存档 */}
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.danger.clear_saves')}</strong>
            <span>{t('mobile.settings.danger.clear_saves_desc', { n: nSaves })}</span>
          </div>
          <button
            style={{ fontSize:13, color:'var(--danger)', background:'var(--danger-soft)', border:'1px solid rgba(200,103,93,0.3)', borderRadius:8, padding:'7px 14px' }}
            onClick={openClear}
          >
            {t('mobile.settings.danger.clear_btn')}
          </button>
        </div>

        {/* 重置平台 */}
        <div className="pl-setrow">
          <div className="pl-setrow-tx">
            <strong>{t('mobile.settings.danger.reset_platform')}</strong>
            <span>{t('mobile.settings.danger.reset_platform_desc')}</span>
          </div>
          <span style={{ fontSize: 11, color: 'var(--muted-2)', fontFamily: 'var(--font-mono)' }}>CLI</span>
        </div>
        <div style={{ padding:'6px 13px 12px', fontSize:11.5, color:'var(--muted)', lineHeight:1.5 }}>
          {t('mobile.settings.danger.reset_cmd_label')}
          <code className="mono" style={{ display:'block', marginTop:6, padding:'7px 10px', borderRadius:7, background:'var(--bg-deep)', border:'1px solid var(--line-soft)', fontSize:11, userSelect:'all', wordBreak:'break-all' }}>
            python -m rpg.platform_app.migrate reset --confirm
          </code>
        </div>
      </SetGroup>

      {/* 清空存档底部 Sheet */}
      {showClearSheet && (
        <div className="sheet-wrap show">
          <div className="sheet-scrim" onClick={clearProgress ? undefined : closeClear} />
          <div className="sheet" style={{ maxHeight: '75%' }}>
            <div className="sheet-grip" />
            <div className="sheet-title" style={{ color: 'var(--danger)' }}>{t('mobile.settings.danger.clear_saves')}</div>
            <div className="sheet-sub">
              {t('mobile.settings.danger.confirm_desc_prefix')} <strong style={{ color: 'var(--text)' }}>{nSaves}</strong> {t('mobile.settings.danger.confirm_desc_suffix')}
            </div>
            <div className="confirm-preview">
              {t('mobile.settings.danger.confirm_preview')}
            </div>
            <div style={{ marginBottom: 14 }}>
              <label style={{ fontSize: 12.5, color: 'var(--muted)', display:'block', marginBottom:8 }}>
                {t('mobile.settings.danger.confirm_input_prefix')} <strong style={{ color:'var(--danger)' }}>{t('mobile.settings.danger.confirm_keyword')}</strong> {t('mobile.settings.danger.confirm_input_suffix')}
              </label>
              <input
                className="pl-input"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder={t('mobile.settings.danger.confirm_keyword')}
                autoFocus
              />
            </div>
            {clearProgress && (
              <div style={{ marginBottom: 12, fontSize: 12.5, color: 'var(--text-quiet)' }}>
                {t('mobile.settings.danger.deleting_progress', { done: clearProgress.done, total: clearProgress.total })}
                <div style={{ height:4, background:'var(--panel-3)', borderRadius:2, marginTop:6, overflow:'hidden' }}>
                  <div style={{ height:'100%', background:'var(--danger)', borderRadius:2, width:`${Math.round(clearProgress.done/clearProgress.total*100)}%`, transition:'width .2s' }} />
                </div>
              </div>
            )}
            <div className="sheet-actions">
              <button className="sheet-btn" onClick={closeClear} disabled={!!clearProgress}>{t('common.cancel')}</button>
              <button className="sheet-btn danger"
                disabled={confirmText !== t('mobile.settings.danger.confirm_keyword') || !!clearProgress}
                onClick={doDelete}>
                <Icon name="trash" size={14} /> {t('mobile.settings.danger.clear_saves_confirm_btn')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* 主组件                                                               */
/* ────────────────────────────────────────────────────────────────── */
const SECTIONS = [
  { id:'preferences',   icon:'settings',  tone:'' },
  { id:'models',        icon:'cpu',       tone:'accent' },
  { id:'modelparams',   icon:'gauge',     tone:'' },
  { id:'modules',       icon:'layers',    tone:'info' },
  { id:'memory',        icon:'memory',    tone:'' },
  { id:'permissions',   icon:'shield',    tone:'ok' },
  { id:'account',       icon:'user',      tone:'' },
  { id:'danger',        icon:'warn',      tone:'warn' },
];

// 把路由 id 映射到 section id
const ROUTE_MAP = {
  'settings':               null,   // hub
  'settings-models':        'models',
  'settings-modelparams':   'modelparams',
  'settings-modules':       'modules',
  'settings-memory':        'memory',
  'settings-permissions':   'permissions',
  'settings-account':       'account',
  'settings-danger':        'danger',
};

export function MobileSettings({ nav }) {
  const { t } = useTranslation();
  // 外部路由可以通过 nav.params.section 指定起始分节
  const [section, setSection] = useState(() => {
    // 支持初始路由直达
    if (nav && nav.params && nav.params.section) return nav.params.section;
    // 支持由 nav.go('settings-xxx') 跳转时传的 routeId
    if (nav && nav.currentRouteId && ROUTE_MAP[nav.currentRouteId]) return ROUTE_MAP[nav.currentRouteId];
    return null; // null = hub 列表
  });

  // 监听 cap-navigate-subsection 事件(电脑端同款)
  useEffect(() => {
    const handler = (ev) => {
      const target = ev?.detail?.target;
      if (!target || typeof target !== 'string') return;
      const parts = target.split('.');
      if (parts[0] !== 'settings' || parts.length < 2) return;
      const ALIASES = { api:'models' };
      const sub = ALIASES[parts[1]] || parts[1];
      if (SECTIONS.some(s => s.id===sub)) setSection(sub);
    };
    window.addEventListener('cap-navigate-subsection', handler);
    return () => window.removeEventListener('cap-navigate-subsection', handler);
  }, []);

  const meta = SECTIONS.find(s => s.id===section) || null;

  /* ── Hub: 分节列表 ── */
  if (!section) {
    return (
      <>
        <div className="pl-head">
          <div className="pl-head-title center">
            <strong>{t('mobile.settings.title')}</strong>
          </div>
        </div>
        <div className="pl-body tabbed">
          <div className="pl-pad" style={{ display:'grid', gap:7 }}>
            {SECTIONS.map(s => (
              <button key={s.id} className="pl-row" onClick={() => setSection(s.id)}>
                <span className={`pl-row-ic ${s.tone||''}`}><Icon name={s.icon} size={18} /></span>
                <span className="pl-row-tx"><strong>{t(`mobile.settings.section.${s.id}.label`)}</strong><span>{t(`mobile.settings.section.${s.id}.sub`)}</span></span>
                <span className="pl-row-chev"><Icon name="chevron_right" size={17} /></span>
              </button>
            ))}
          </div>
        </div>
      </>
    );
  }

  /* ── 分节视图 ── */
  // ProviderDetail 自带 pl-head，需要特殊处理
  // 其他分节统一用下面的 shell
  return (
    <>
      {/* 如果是 models section 并且 ProviderDetail 正在展示，
          ProviderDetail 内部会 render 自己的 pl-head，所以让 ModelsSection 控制全屏 */}
      {section === 'models' ? (
        <ModelsSection nav={nav} onBack={() => setSection(null)} />
      ) : (
        <>
          <div className="pl-head">
            <button className="pl-back" onClick={() => setSection(null)}>
              <Icon name="chevron_left" size={20} />
            </button>
            <div className="pl-head-title">
              <strong>{meta ? t(`mobile.settings.section.${meta.id}.label`) : t('mobile.settings.title')}</strong>
              <span className="sub">{meta ? t(`mobile.settings.section.${meta.id}.sub`) : ''}</span>
            </div>
          </div>
          <div className="pl-body tabbed">
            <div className="pl-pad">
              {section === 'preferences'  && <PrefSection nav={nav} />}
              {section === 'modelparams'  && <ModelParamsSection />}
              {section === 'modules'      && <ModuleModelsSection nav={nav} />}
              {section === 'memory'       && <MemorySection />}
              {section === 'permissions'  && <PermissionsSection nav={nav} />}
              {section === 'account'      && <AccountSection nav={nav} />}
              {section === 'danger'       && <DangerSection nav={nav} />}
            </div>
          </div>
        </>
      )}
    </>
  );
}

/* models section 需要直接渲染(含内部子视图切换),包装一层让 onBack 工作 */
function ModelsSection({ nav, onBack }) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState(null);
  const [apis, setApis] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState('');
  const autoSynced = useRef(new Set());

  const mapModel = useCallback((m) => ({
    id: m.real_name || m.id,
    display: m.display_name || m.real_name || m.id,
    real_name: m.real_name || m.id,
    enabled: m.enabled !== false,
    visible: m.hidden !== true,
    capabilities: m.capabilities || {},
    health: m.health || 'untested',
    health_latency_ms: m.health_latency_ms,
  }), []);

  const load = useCallback(async () => {
    const [data, creds] = await Promise.all([
      window.api.models.list(),
      window.api.credentials.list().catch(() => ({ items:[] })),
    ]);
    const credMap = {};
    for (const c of (creds?.items || creds?.credentials || [])) {
      const cid = normId(c.api_id || c.id);
      credMap[cid] = { has_key: !!(c.has_credential||c.has_key||c.key_hint), key_hint: c.key_hint||'', enabled: c.enabled!==false, base_url_override: c.base_url_override||'', proxy_url: c.proxy_url || '' };
    }
    const list = data?.models?.apis || data?.apis || [];
    const rows = Array.isArray(list) ? list.map(api => {
      const cataId = catId(api.api_id || api.id);
      const criId = credId(cataId);
      const cred = credMap[criId] || credMap[normId(cataId)] || {};
      return {
        id: cataId, credential_id: criId,
        name: api.display_name || api.name || cataId,
        // 用户自己的 base_url_override(中转站)优先;非 admin 的 api.base_url 已被后端 redact 成空。
        base_url: cred.base_url_override || api.base_url || '',
        key_set: !!cred.has_key, key_hint: cred.key_hint || '—',
        connectivity: { status:'untested' },
        // proxy 从凭据读(api.proxy 对非 admin 恒 undefined);后端 list_credentials 返回 proxy_url。
        enabled: cred.enabled !== false, proxy: cred.proxy_url ? 'http_proxy' : 'direct',
        models: (api.models || api.entries || []).map(mapModel),
      };
    }).filter(a => a.key_set) : [];
    // 中转站 / 自定义供应商:把不在全局 catalog 里、但带 base_url_override 的用户凭据合成为
    // provider 行,否则保存后在列表里看不到、无法选模型;models=[] 由用户点同步从中转站拉取。
    const catalogIds = new Set((Array.isArray(list) ? list : []).map(a => normId(catId(a.api_id || a.id))));
    const customRows = Object.entries(credMap)
      .filter(([cid, c]) => c.has_key && c.base_url_override && !catalogIds.has(normId(cid)))
      .map(([cid, c]) => ({
        id: cid, credential_id: cid, name: cid,
        base_url: c.base_url_override, key_set: true, key_hint: c.key_hint || '—',
        connectivity: { status:'untested' },
        enabled: c.enabled !== false, proxy: c.proxy_url ? 'http_proxy' : 'direct',
        models: [], _custom: true,
      }));
    const allRows = [...rows, ...customRows];
    setApis(allRows);
    return allRows;
  }, [mapModel]);

  const syncRemote = useCallback(async (api, silent=false) => {
    if (!api) return;
    const aId = catId(api.id);
    setApis(arr => arr.map(a => a.id===aId ? { ...a, connectivity: { ...a.connectivity, status:'checking' } } : a));
    try {
      const r = await window.api.models.syncRemote({ api_id: aId, base_url: api.base_url||'' });
      if (!r?.ok) throw new Error(r?.error||'sync failed');
      const models = (r.models||[]).map(mapModel);
      setApis(arr => arr.map(a => a.id===aId ? {
        ...a, models,
        connectivity: { status:'ok', latency_ms:r.latency_ms, remote_total:r.remote_total??models.length },
      } : a));
      if (!silent) nav.toast(t('mobile.settings.models.sync_done', { count: models.length }), 'ok', 'refresh');
    } catch (e) {
      setApis(arr => arr.map(a => a.id===aId ? { ...a, connectivity: { status:'err', error:e?.message||'' } } : a));
      if (!silent) nav.toast(t('mobile.settings.models.sync_failed', { msg: e?.message||'' }), 'danger', 'warn');
    }
  }, [mapModel, nav]);

  const reload = useCallback(async () => {
    try { setLoadErr(''); await load(); }
    catch (e) { setLoadErr(e?.message || t('mobile.settings.models.load_failed')); }
    finally { setLoading(false); }
  }, [load, t]);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    if (loading) return;
    apis.forEach(api => {
      if (autoSynced.current.has(api.id)) return;
      autoSynced.current.add(api.id);
      syncRemote(api, true);
    });
  }, [loading, apis, syncRemote]);

  const selectedApi = apis.find(a => a.id===selected) || null;

  /* 供应商详情 */
  if (selectedApi) {
    return (
      <ProviderDetail
        api={selectedApi}
        onBack={() => setSelected(null)}
        onSync={() => syncRemote(selectedApi)}
        nav={nav}
        onToggleModel={async (mId) => {
          const m = selectedApi.models.find(x => x.id===mId);
          const prev = !!m?.enabled;
          setApis(arr => arr.map(a => a.id===selectedApi.id ? { ...a, models: a.models.map(x => x.id===mId ? { ...x, enabled:!x.enabled } : x) } : a));
          try {
            await window.api.models.upsertModel({ api_id: selectedApi.id, real_name: mId, enabled: !prev });
          } catch (e) {
            // POST /api/models/model 是 admin-only,非 admin 部署模式 403 → 回滚乐观翻转并提示。
            setApis(arr => arr.map(a => a.id===selectedApi.id ? { ...a, models: a.models.map(x => x.id===mId ? { ...x, enabled: prev } : x) } : a));
            nav.toast(e?.status===403 ? t('mobile.settings.models.admin_only') : t('mobile.settings.models.save_failed', { msg: e?.message||'' }), 'danger', 'warn');
          }
        }}
        onDeleteKey={async () => {
          if (!await window.__confirm({ message: t('mobile.settings.models.delete_key_confirm', { name: selectedApi.name }), danger: true })) return;
          try {
            await window.api.credentials.remove({ api_id: credId(selectedApi.id) });
            setSelected(null);
            setApis(arr => arr.filter(a => a.id!==selectedApi.id));
            nav.toast(t('mobile.settings.models.key_deleted'), 'ok', 'trash');
          } catch (e) { nav.toast(t('mobile.settings.models.delete_failed', { msg: e?.message||'' }), 'danger', 'warn'); }
        }}
      />
    );
  }

  /* 供应商列表 */
  return (
    <>
      <div className="pl-head">
        <button className="pl-back" onClick={onBack}><Icon name="chevron_left" size={20} /></button>
        <div className="pl-head-title">
          <strong>{t('mobile.settings.section.models.label')}</strong>
          <span className="sub">{t('mobile.settings.models.provider_count', { count: apis.length })}</span>
        </div>
      </div>
      <div className="pl-body tabbed">
        <div className="pl-pad">
          {loading && (
            <div className="pl-empty">
              <div className="ic"><Icon name="cpu" size={22} /></div>
              <p>{t('common.loading')}</p>
            </div>
          )}
          {!loading && loadErr && (
            <div className="pl-empty">
              <div className="ic"><Icon name="warn" size={22} /></div>
              <h3>{t('mobile.settings.models.load_failed')}</h3>
              <p>{loadErr}</p>
              <button className="pl-btn-ghost" style={{ marginTop:12, height:38, fontSize:13, width:'auto', padding:'0 18px' }}
                onClick={() => { setLoading(true); setLoadErr(''); reload(); }}>{t('mobile.settings.models.retry')}</button>
            </div>
          )}
          {!loading && !loadErr && apis.length===0 && (
            <div className="pl-empty">
              <div className="ic"><Icon name="key" size={22} /></div>
              <h3>{t('mobile.settings.models.no_providers')}</h3>
              <p>{t('mobile.settings.models.no_providers_hint')}</p>
            </div>
          )}
          {!loading && !loadErr && apis.map(pv => {
            const conn = pv.connectivity || {};
            const enabledCnt = pv.models.filter(m => m.enabled).length;
            const statusOk = conn.status==='ok';
            const statusErr = conn.status==='err';
            const statusBusy = conn.status==='checking';
            return (
              <button key={pv.id} className="pl-prov" style={{ width:'100%', textAlign:'left', marginBottom:10 }}
                onClick={() => setSelected(pv.id)}>
                <div className="pl-prov-head">
                  <span className="pl-prov-logo">{pv.name.slice(0,1)}</span>
                  <span className="pl-prov-id">
                    <strong>
                      {pv.name}
                      {pv.enabled===false && <span style={{ marginLeft:6, fontSize:11, fontWeight:600, color:'var(--muted-2)', border:'1px solid var(--line-soft)', borderRadius:4, padding:'1px 6px' }}>{t('common.disabled')}</span>}
                    </strong>
                    <span className="key mono">•••• {pv.key_hint}</span>
                  </span>
                  <span className={`pl-status ${statusOk?'online':''}`}>
                    <span className="d" style={statusErr ? { background:'var(--danger)' } : statusBusy ? { background:'var(--warn)' } : {}} />
                    {statusOk ? `✓ ${conn.latency_ms||''}${conn.latency_ms?'ms':t('mobile.settings.models.status_connected')}` :
                     statusErr ? `✗ ${t('common.error')}` : statusBusy ? t('mobile.settings.models.status_syncing') : t('mobile.settings.models.conn_untested')}
                  </span>
                  <Icon name="chevron_right" size={16} style={{ color:'var(--muted-3)', marginLeft:4 }} />
                </div>
                {pv.models.slice(0,2).map(m => (
                  <div key={m.id} className="pl-model-row">
                    <span className="mname" style={{ color: m.enabled ? 'var(--text)' : 'var(--muted-2)' }}>{m.display}</span>
                    <span className="mmeta" style={{ color: m.health==='ok'?'var(--ok)':m.health==='err'?'var(--danger)':'var(--muted-3)' }}>
                      {m.health==='ok' ? '✓' : m.health==='err' ? '✗' : '?'}
                    </span>
                  </div>
                ))}
                {pv.models.length > 0 && (
                  <div className="pl-model-row" style={{ justifyContent:'flex-end', color:'var(--muted-2)', fontSize:11 }}>
                    {t('mobile.settings.models.enabled_count', { enabled: enabledCnt, total: pv.models.length })}
                    {pv.models.length > 2 && ` · +${pv.models.length-2}`}
                  </div>
                )}
              </button>
            );
          })}
          <div style={{ padding:'8px 0', fontSize:12, color:'var(--muted)', textAlign:'center', lineHeight:1.6 }}>
            {t('mobile.settings.models.add_provider_hint')}
          </div>
        </div>
      </div>
    </>
  );
}

export default MobileSettings;
